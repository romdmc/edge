#!/usr/bin/env python3
"""
EDGE — Multi-Source Scraper
============================

Production-ready scraper that ingests tech news from RSS feeds, Reddit,
and YouTube transcripts. Articles are deduplicated via SHA-256 content hashing
and stored in a local SQLite database.

Usage:
    from scraper import run_scraper
    stats = run_scraper("config/sources.yaml")

    # Or run directly:
    python scraper.py config/sources.yaml

Dependencies:
    feedparser, requests, youtube-transcript-api, pyyaml
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser
import requests
import yaml
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("edge.scraper")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "edge.db"
REQUEST_TIMEOUT = 30  # seconds
RETRY_COUNT = 3
RETRY_BACKOFF = 2.0  # exponential backoff multiplier
REDDIT_USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "EdgeBot/1.0 (by /u/edge-scraper)",
]
REDDIT_USER_AGENT = REDDIT_USER_AGENTS[0]

# SQL schema — kept in one place for clarity
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    url TEXT NOT NULL,
    config TEXT DEFAULT '{}',
    last_fetch TIMESTAMP,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    content TEXT,
    raw_content TEXT,
    author TEXT,
    published_at TIMESTAMP,
    hash TEXT UNIQUE NOT NULL,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_articles_hash ON articles(hash);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);

-- FTS5 full-text search virtual table
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title,
    content,
    summary,
    tags,
    content=articles,
    content_rowid=id
);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def normalize_text(text: str | None) -> str:
    """Strip HTML tags, entities, and collapse whitespace for hashing."""
    if not text:
        return ""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode HTML entities
    text = html.unescape(text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def compute_hash(text: str) -> str:
    """Return SHA-256 hex digest of *normalized* text."""
    normalized = normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def retry_request(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    **kwargs: Any,
) -> requests.Response:
    """
    HTTP request with exponential-backoff retry.

    Raises ``requests.RequestException`` after *RETRY_COUNT* failures.
    """
    last_exc: Exception | None = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            resp = requests.request(
                method,
                url,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                **kwargs,
            )
            if resp.status_code == 429:
                wait = RETRY_BACKOFF ** attempt
                logger.warning("Rate-limited on %s — waiting %.1fs", url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning(
                "Request failed (%d/%d) for %s: %s",
                attempt,
                RETRY_COUNT,
                url,
                exc,
            )
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_BACKOFF ** attempt)
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Database layer
# ---------------------------------------------------------------------------


class Database:
    """Thin wrapper around the SQLite connection."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    # -- sources -----------------------------------------------------------

    def upsert_source(
        self, name: str, type_: str, url: str, config: dict | None = None
    ) -> int:
        """Insert or update a source row; return its integer id."""
        cfg_json = json.dumps(config or {})
        cur = self.conn.execute(
            """
            INSERT INTO sources (name, type, url, config)
            VALUES (?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (name, type_, url, cfg_json),
        )
        if cur.rowcount == 0:
            self.conn.execute(
                "UPDATE sources SET url=?, config=?, active=1 WHERE name=? AND type=?",
                (url, cfg_json, name, type_),
            )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM sources WHERE name=? AND type=?", (name, type_)
        ).fetchone()
        return row[0]

    def update_last_fetch(self, source_id: int) -> None:
        self.conn.execute(
            "UPDATE sources SET last_fetch=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), source_id),
        )
        self.conn.commit()

    # -- articles ----------------------------------------------------------

    def insert_article(self, article: dict[str, Any]) -> bool:
        """
        Insert an article. Returns True if a new row was created,
        False if the URL or hash already existed (dedup).
        """
        try:
            self.conn.execute(
                """
                INSERT INTO articles
                    (source_id, url, title, content, raw_content,
                     author, published_at, hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article["source_id"],
                    article["url"],
                    article["title"],
                    article.get("content"),
                    article.get("raw_content"),
                    article.get("author"),
                    article.get("published_at"),
                    article["hash"],
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def close(self) -> None:
        self.conn.close()


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------


class RSSFetcher:
    """Fetch and parse RSS / Atom feeds."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def fetch_source(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        name: str = source["name"]
        url: str = source["url"]
        max_items: int = source.get("config", {}).get("max_items", 25)
        logger.info("RSS ▶ %s (%s)", name, url)

        try:
            feed = feedparser.parse(url)
        except Exception as exc:
            logger.error("Failed to parse RSS feed %s: %s", url, exc)
            return []

        if feed.bozo and not feed.entries:
            logger.warning("Feed %s returned no entries (bozo=%s)", url, feed.bozo_exception)

        source_id = self.db.upsert_source(name, "rss", url, source.get("config"))
        articles: list[dict[str, Any]] = []

        for entry in feed.entries[:max_items]:
            title = entry.get("title", "Untitled")
            link = entry.get("link", "")
            if not link:
                continue

            # Content: prefer content[0].value, fall back to summary
            raw = ""
            if entry.get("content"):
                raw = entry["content"][0].get("value", "")
            raw = raw or entry.get("summary", entry.get("description", ""))

            # Published date
            published = None
            if entry.get("published_parsed"):
                try:
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
                except (TypeError, ValueError):
                    pass

            author = entry.get("author", "")
            content_text = normalize_text(raw)
            h = compute_hash(title + " " + content_text)

            articles.append(
                {
                    "source_id": source_id,
                    "url": link,
                    "title": title,
                    "content": content_text,
                    "raw_content": raw,
                    "author": author,
                    "published_at": published,
                    "hash": h,
                }
            )

        self.db.update_last_fetch(source_id)
        logger.info("RSS ◀ %s — %d entries parsed", name, len(articles))
        return articles


class RedditFetcher:
    """Fetch top daily posts from Reddit via the public JSON API."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._ua_index = 0

    def _next_ua(self) -> str:
        ua = REDDIT_USER_AGENTS[self._ua_index % len(REDDIT_USER_AGENTS)]
        self._ua_index += 1
        return ua

    def fetch_source(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        name: str = source["name"]
        url: str = source["url"]
        limit: int = source.get("config", {}).get("limit", 25)
        logger.info("Reddit ▶ %s", name)

        data = None
        for attempt in range(RETRY_COUNT):
            try:
                resp = retry_request(
                    url,
                    headers={
                        "User-Agent": self._next_ua(),
                        "Accept": "application/json",
                    },
                )
                data = resp.json()
                if data.get("data", {}).get("children"):
                    break
                # Empty or rate-limited — try next UA
                logger.warning("Reddit %s: empty response (attempt %d)", name, attempt + 1)
                time.sleep(RETRY_BACKOFF ** attempt)
            except Exception as exc:
                logger.warning("Reddit %s attempt %d failed: %s", name, attempt + 1, exc)
                time.sleep(RETRY_BACKOFF ** attempt)

        if not data:
            logger.error("Reddit %s: all attempts failed", name)
            return []

        posts = data.get("data", {}).get("children", [])
        source_id = self.db.upsert_source(name, "reddit", url, source.get("config"))
        articles: list[dict[str, Any]] = []

        for post in posts[:limit]:
            p = post.get("data", {})
            title = p.get("title", "Untitled")
            permalink = p.get("permalink", "")
            url = f"https://www.reddit.com{permalink}" if permalink else ""
            if not url:
                continue

            # selftext for text posts, otherwise use title as content
            raw = p.get("selftext", "") or title
            author = p.get("author", "[deleted]")

            # created_utc → ISO
            published = None
            if p.get("created_utc"):
                try:
                    published = datetime.fromtimestamp(
                        p["created_utc"], tz=timezone.utc
                    ).isoformat()
                except (OSError, ValueError):
                    pass

            content_text = normalize_text(raw)
            h = compute_hash(title + " " + content_text)

            articles.append(
                {
                    "source_id": source_id,
                    "url": url,
                    "title": title,
                    "content": content_text,
                    "raw_content": raw,
                    "author": author,
                    "published_at": published,
                    "hash": h,
                }
            )

        self.db.update_last_fetch(source_id)
        logger.info("Reddit ◀ %s — %d posts parsed", name, len(articles))
        return articles


class YouTubeFetcher:
    """
    Fetch recent videos from a YouTube channel and extract transcripts.

    Uses the YouTube Data API v3 search endpoint to discover recent video IDs,
    then ``youtube-transcript-api`` to pull transcripts.

    .. note::
        Set the ``YT_API_KEY`` environment variable for full video discovery.
        Without it, the fetcher will attempt to use ``yt-dlp``-style fallback
        or skip video discovery gracefully.
    """

    def __init__(self, db: Database, api_key: str | None = None) -> None:
        self.db = db
        self.api_key = api_key

    # -- video discovery ---------------------------------------------------

    def _get_recent_video_ids(self, channel_id: str, max_videos: int = 5) -> list[str]:
        """Return up to *max_videos* recent video IDs for a channel."""
        import os

        key = self.api_key or os.environ.get("YT_API_KEY")
        if not key:
            logger.warning(
                "No YT_API_KEY set — cannot discover videos for channel %s. "
                "Set the YT_API_KEY env var or pass api_key= to YouTubeFetcher.",
                channel_id,
            )
            return []

        search_url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            "part": "id",
            "channelId": channel_id,
            "maxResults": max_videos,
            "order": "date",
            "type": "video",
            "key": key,
        }
        try:
            resp = retry_request(search_url, params=params)
            data = resp.json()
            return [item["id"]["videoId"] for item in data.get("items", [])]
        except Exception as exc:
            logger.error("YouTube search failed for %s: %s", channel_id, exc)
            return []

    # -- transcript extraction ---------------------------------------------

    def _get_transcript(self, video_id: str) -> str:
        """Return the full transcript text for a video, or '' on failure."""
        try:
            entries = YouTubeTranscriptApi.get_transcript(video_id)
            return " ".join(entry.get("text", "") for entry in entries)
        except (NoTranscriptFound, TranscriptsDisabled):
            logger.debug("No transcript for video %s", video_id)
            return ""
        except Exception as exc:
            logger.warning("Transcript error for %s: %s", video_id, exc)
            return ""

    # -- main entry point --------------------------------------------------

    def fetch_source(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        name: str = source["name"]
        channel_id: str = source.get("config", {}).get("channel_id", source["url"])
        max_videos: int = source.get("config", {}).get("max_videos", 5)
        logger.info("YouTube ▶ %s (channel %s)", name, channel_id)

        video_ids = self._get_recent_video_ids(channel_id, max_videos)
        if not video_ids:
            logger.warning("No videos found for %s — skipping", name)
            return []

        source_id = self.db.upsert_source(name, "youtube", channel_id, source.get("config"))
        articles: list[dict[str, Any]] = []

        for vid in video_ids:
            video_url = f"https://www.youtube.com/watch?v={vid}"
            transcript = self._get_transcript(vid)
            if not transcript:
                continue

            title = f"YouTube — {vid}"  # title enrichment would need another API call
            content_text = normalize_text(transcript)
            h = compute_hash(title + " " + content_text)

            articles.append(
                {
                    "source_id": source_id,
                    "url": video_url,
                    "title": title,
                    "content": content_text,
                    "raw_content": transcript,
                    "author": name,
                    "published_at": None,
                    "hash": h,
                }
            )

        self.db.update_last_fetch(source_id)
        logger.info("YouTube ◀ %s — %d transcripts fetched", name, len(articles))
        return articles


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def load_config(config_path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Load and return the YAML source configuration."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def run_scraper(
    config_path: str | Path,
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    yt_api_key: str | None = None,
) -> dict[str, int]:
    """
    Main entry point — read *config_path*, fetch all sources, store results.

    Parameters
    ----------
    config_path : path-like
        YAML file with source definitions.
    db_path : path-like, optional
        SQLite database path (default: ``<project>/data/edge.db``).
    yt_api_key : str, optional
        YouTube Data API v3 key. Falls back to ``YT_API_KEY`` env var.

    Returns
    -------
    dict
        Stats with keys ``total_fetched``, ``total_stored``, ``total_dupes``,
        ``errors``.
    """
    config = load_config(config_path)
    db = Database(db_path)

    rss_fetcher = RSSFetcher(db)
    reddit_fetcher = RedditFetcher(db)
    yt_fetcher = YouTubeFetcher(db, api_key=yt_api_key)

    total_fetched = 0
    total_stored = 0
    total_dupes = 0
    errors = 0

    # -- RSS ---------------------------------------------------------------
    for source in config.get("rss", []):
        try:
            articles = rss_fetcher.fetch_source(source)
            total_fetched += len(articles)
            for art in articles:
                if db.insert_article(art):
                    total_stored += 1
                else:
                    total_dupes += 1
        except Exception as exc:
            logger.error("RSS source %s failed: %s", source.get("name"), exc)
            errors += 1

    # -- Reddit ------------------------------------------------------------
    for source in config.get("reddit", []):
        try:
            articles = reddit_fetcher.fetch_source(source)
            total_fetched += len(articles)
            for art in articles:
                if db.insert_article(art):
                    total_stored += 1
                else:
                    total_dupes += 1
        except Exception as exc:
            logger.error("Reddit source %s failed: %s", source.get("name"), exc)
            errors += 1

    # -- YouTube -----------------------------------------------------------
    for source in config.get("youtube", []):
        try:
            articles = yt_fetcher.fetch_source(source)
            total_fetched += len(articles)
            for art in articles:
                if db.insert_article(art):
                    total_stored += 1
                else:
                    total_dupes += 1
        except Exception as exc:
            logger.error("YouTube source %s failed: %s", source.get("name"), exc)
            errors += 1

    db.close()

    stats = {
        "total_fetched": total_fetched,
        "total_stored": total_stored,
        "total_dupes": total_dupes,
        "errors": errors,
    }
    logger.info("Scraper finished — %s", stats)
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="EDGE multi-source scraper")
    parser.add_argument("config", help="Path to sources YAML config")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path")
    parser.add_argument("--yt-api-key", default=None, help="YouTube Data API v3 key")
    parser.add_argument("--verbose", "-v", action="store_true", help="DEBUG logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    stats = run_scraper(args.config, args.db, yt_api_key=args.yt_api_key)
    print(json.dumps(stats, indent=2))

    if stats["errors"] > 0:
        sys.exit(1)
