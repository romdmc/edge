#!/usr/bin/env python3
"""
EDGE — Static Site Generator
==============================

Generates a minimalist, elegant static HTML site from the EDGE SQLite database.

Pages produced:
  output/index.html              — today's digest, sorted by score
  output/YYYY-MM-DD.html         — per-date digest pages
  output/articles/{id}.html      — individual article page
  output/tags/{tag}.html         — per-tag listing page
  output/archives.html           — chronological archive

Features:
  - Jinja2 templates (``templates/``)
  - Dark-mode-first responsive CSS (embedded in base template)
  - Incremental generation (only new/updated pages are written)
  - Tag cloud on archives page
  - Score badges (high/mid/low)
  - Visual score bars on article pages

Usage:
    from generator import Generator, generate_site, run_generator
    stats = generate_site("data/edge.db", "output")

    # Or from CLI:
    python generator.py config/sources.yaml --min-score 5

Dependencies:
    jinja2, pyyaml
"""

from __future__ import annotations

import hashlib
import html as html_module
import json
import logging
import re
import shutil
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Jinja2 is required. Install it with: pip install jinja2"
    ) from exc

from i18n import t as translate

from seo import generate_sitemap, generate_robots, generate_feed

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("edge.generator")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "edge.db"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "output"
DEFAULT_TEMPLATES_DIR = Path(__file__).parent / "templates"
HASH_DIR = DEFAULT_OUTPUT_DIR / ".gen_hashes"
GENERATED_AT = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ArticleView:
    """Flat representation of an article + its analysis, ready for templates."""

    id: int
    title: str
    url: str
    source_name: str
    author: str
    published_at: str  # ISO date string
    edge_score: float
    value_score: float
    cost_score: float
    overall_score: float
    topics: list[str]
    summary: str
    key_quotes: list[str]
    raw_content: str
    topic_slugs: list[str] = field(default_factory=list)
    analyzed_at: str = ""
    title_fr: str = ""


@dataclass
class GeneratorStats:
    """Aggregated statistics for a generator run."""

    pages_generated: int = 0
    pages_skipped: int = 0
    articles_indexed: int = 0
    tags_indexed: int = 0
    dates_indexed: int = 0
    errors: int = 0
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def elapsed_seconds(self) -> float:
        end = self.end_time or time.time()
        return end - self.start_time

    def as_dict(self) -> dict[str, Any]:
        return {
            "pages_generated": self.pages_generated,
            "pages_skipped": self.pages_skipped,
            "articles_indexed": self.articles_indexed,
            "tags_indexed": self.tags_indexed,
            "dates_indexed": self.dates_indexed,
            "errors": self.errors,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }


# ---------------------------------------------------------------------------
# Incremental generation helpers
# ---------------------------------------------------------------------------


def _content_hash(content: str) -> str:
    """Return a short MD5 hash of *content* for change detection."""
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _hash_path(output_path: Path, output_dir: Path = DEFAULT_OUTPUT_DIR) -> Path:
    """Return the path where the hash for *output_path* is stored."""
    try:
        relative = output_path.relative_to(output_dir)
    except ValueError:
        relative = output_path.resolve().relative_to(output_dir.resolve())
    return HASH_DIR / f"{relative}.md5"


def _needs_regen(output_path: Path, new_content: str, output_dir: Path = DEFAULT_OUTPUT_DIR) -> bool:
    """
    Return True if *output_path* does not exist or its content has changed.
    Uses sidecar ``.md5`` files to avoid re-writing identical HTML.
    """
    if not output_path.exists():
        return True
    hash_file = _hash_path(output_path, output_dir)
    if not hash_file.exists():
        return True
    old_hash = hash_file.read_text(encoding="utf-8").strip()
    return old_hash != _content_hash(new_content)


def _write_with_hash(output_path: Path, content: str, output_dir: Path = DEFAULT_OUTPUT_DIR) -> None:
    """Write *content* to *output_path* and update the sidecar hash file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    hash_file = _hash_path(output_path, output_dir)
    hash_file.parent.mkdir(parents=True, exist_ok=True)
    hash_file.write_text(_content_hash(content), encoding="utf-8")


def _safe_tag_slug(tag: str) -> str:
    """Convert a tag string to a safe filename slug."""
    slug = tag.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "untagged"


def _normalize_content(content: str) -> str:
    """Strip HTML tags and normalize whitespace for display."""
    if not content:
        return ""
    text = re.sub(r"<[^>]+>", " ", content)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Database queries
# ---------------------------------------------------------------------------


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def fetch_published_articles(
    db_path: Path,
    min_score: float = 5.0,
) -> list[ArticleView]:
    """
    Return all articles that have an analysis row with overall_score >= min_score.
    Results are ordered by published_at DESC, then overall_score DESC.
    """
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                a.id, a.title, a.url,
                COALESCE(s.name, '')      AS source_name,
                COALESCE(a.author, '')    AS author,
                COALESCE(a.published_at, '') AS published_at,
                an.edge_score, an.value_score, an.cost_score,
                an.overall_score,
                COALESCE(an.topics, '[]') AS topics_json,
                COALESCE(an.summary, '')  AS summary,
                COALESCE(an.key_quotes, '[]') AS key_quotes_json,
                COALESCE(a.raw_content, '') AS raw_content,
                COALESCE(an.analyzed_at, '') AS analyzed_at
            FROM articles a
            JOIN analyses an ON an.article_id = a.id
            LEFT JOIN sources s  ON s.id = a.source_id
            WHERE an.overall_score >= ?
            ORDER BY a.published_at DESC, an.overall_score DESC
            """,
            (min_score,),
        ).fetchall()

        articles: list[ArticleView] = []
        for r in rows:
            try:
                topics = json.loads(r["topics_json"])
                # Handle double-escaped JSON (string containing JSON array)
                if isinstance(topics, str):
                    topics = json.loads(topics)
                if not isinstance(topics, list):
                    topics = []
            except (json.JSONDecodeError, TypeError, ValueError):
                topics = []
            topic_slugs = [_safe_tag_slug(t) for t in topics]
            try:
                key_quotes = json.loads(r["key_quotes_json"])
            except (json.JSONDecodeError, TypeError):
                key_quotes = []

            articles.append(
                ArticleView(
                    id=r["id"],
                    title=r["title"],
                    url=r["url"],
                    source_name=r["source_name"],
                    author=r["author"],
                    published_at=r["published_at"],
                    edge_score=r["edge_score"] or 0,
                    value_score=r["value_score"] or 0,
                    cost_score=r["cost_score"] or 0,
                    overall_score=r["overall_score"] or 0,
                    topics=topics,
                    summary=r["summary"],
                    topic_slugs=topic_slugs,
                    key_quotes=key_quotes,
                    raw_content=r["raw_content"],
                    analyzed_at=r["analyzed_at"],
                )
            )
        return articles
    finally:
        conn.close()


def fetch_available_dates(db_path: Path, min_score: float = 5.0) -> list[str]:
    """Return a list of distinct ISO date strings (YYYY-MM-DD) that have published articles."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT DATE(a.published_at) AS d
            FROM articles a
            JOIN analyses an ON an.article_id = a.id
            WHERE an.overall_score >= ?
              AND a.published_at IS NOT NULL
            ORDER BY d DESC
            """,
            (min_score,),
        ).fetchall()
        return [r["d"] for r in rows if r["d"]]
    finally:
        conn.close()


def fetch_all_articles_paged(
    db_path: Path,
    min_score: float = 5.0,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[ArticleView], int]:
    """
    Return (articles, total_count) for the given page, ordered by overall_score DESC.
    """
    conn = _open_db(db_path)
    try:
        # Get total count
        count_row = conn.execute(
            "SELECT COUNT(*) FROM articles a JOIN analyses an ON an.article_id = a.id WHERE an.overall_score >= ?",
            (min_score,),
        ).fetchone()
        total = count_row[0] if count_row else 0

        # Get page of articles
        offset = (page - 1) * per_page
        rows = conn.execute(
            """
            SELECT
                a.id, a.title, a.url,
                COALESCE(s.name, '')      AS source_name,
                COALESCE(a.author, '')    AS author,
                COALESCE(a.published_at, '') AS published_at,
                an.edge_score, an.value_score, an.cost_score,
                an.overall_score,
                COALESCE(an.topics, '[]') AS topics_json,
                COALESCE(an.summary, '')  AS summary,
                COALESCE(an.key_quotes, '[]') AS key_quotes_json,
                COALESCE(a.raw_content, '') AS raw_content,
                COALESCE(an.analyzed_at, '') AS analyzed_at
            FROM articles a
            JOIN analyses an ON an.article_id = a.id
            LEFT JOIN sources s  ON s.id = a.source_id
            WHERE an.overall_score >= ?
            ORDER BY an.overall_score DESC, a.published_at DESC
            LIMIT ? OFFSET ?
            """,
            (min_score, per_page, offset),
        ).fetchall()

        articles: list[ArticleView] = []
        for r in rows:
            try:
                topics = json.loads(r["topics_json"])
                if isinstance(topics, str):
                    topics = json.loads(topics)
                if not isinstance(topics, list):
                    topics = []
            except (json.JSONDecodeError, TypeError, ValueError):
                topics = []
            topic_slugs = [_safe_tag_slug(t) for t in topics]
            try:
                key_quotes = json.loads(r["key_quotes_json"])
                if isinstance(key_quotes, str):
                    key_quotes = json.loads(key_quotes)
                if not isinstance(key_quotes, list):
                    key_quotes = []
            except (json.JSONDecodeError, TypeError, ValueError):
                key_quotes = []

            articles.append(
                ArticleView(
                    id=r["id"],
                    title=r["title"],
                    url=r["url"],
                    source_name=r["source_name"],
                    author=r["author"],
                    published_at=r["published_at"],
                    edge_score=r["edge_score"] or 0,
                    value_score=r["value_score"] or 0,
                    cost_score=r["cost_score"] or 0,
                    overall_score=r["overall_score"] or 0,
                    topics=topics,
                    summary=r["summary"],
                    topic_slugs=topic_slugs,
                    key_quotes=key_quotes,
                    raw_content=r["raw_content"],
                    analyzed_at=r["analyzed_at"],
                )
            )
        return articles, total
    finally:
        conn.close()


def search_articles(
    db_path: Path,
    query: str,
    min_score: float = 5.0,
    limit: int = 50,
) -> list[ArticleView]:
    """
    Full-text search across articles using FTS5.
    Falls back to LIKE search if FTS5 table is not populated.
    """
    conn = _open_db(db_path)
    try:
        # Try FTS5 first
        try:
            rows = conn.execute(
                """
                SELECT a.id, a.title, a.url,
                       COALESCE(s.name, '') AS source_name,
                       COALESCE(a.author, '') AS author,
                       COALESCE(a.published_at, '') AS published_at,
                       an.edge_score, an.value_score, an.cost_score,
                       an.overall_score,
                       COALESCE(an.topics, '[]') AS topics_json,
                       COALESCE(an.summary, '') AS summary,
                       COALESCE(an.key_quotes, '[]') AS key_quotes_json,
                       COALESCE(a.raw_content, '') AS raw_content,
                       COALESCE(an.analyzed_at, '') AS analyzed_at
                FROM articles_fts fts
                JOIN articles a ON a.id = fts.rowid
                JOIN analyses an ON an.article_id = a.id
                LEFT JOIN sources s ON s.id = a.source_id
                WHERE articles_fts MATCH ?
                  AND an.overall_score >= ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, min_score, limit),
            ).fetchall()
        except Exception:
            # Fallback: LIKE search
            like_q = f"%{query}%"
            rows = conn.execute(
                """
                SELECT a.id, a.title, a.url,
                       COALESCE(s.name, '') AS source_name,
                       COALESCE(a.author, '') AS author,
                       COALESCE(a.published_at, '') AS published_at,
                       an.edge_score, an.value_score, an.cost_score,
                       an.overall_score,
                       COALESCE(an.topics, '[]') AS topics_json,
                       COALESCE(an.summary, '') AS summary,
                       COALESCE(an.key_quotes, '[]') AS key_quotes_json,
                       COALESCE(a.raw_content, '') AS raw_content,
                       COALESCE(an.analyzed_at, '') AS analyzed_at
                FROM articles a
                JOIN analyses an ON an.article_id = a.id
                LEFT JOIN sources s ON s.id = a.source_id
                WHERE an.overall_score >= ?
                  AND (a.title LIKE ? OR a.content LIKE ? OR an.summary LIKE ?)
                ORDER BY an.overall_score DESC
                LIMIT ?
                """,
                (min_score, like_q, like_q, like_q, limit),
            ).fetchall()

        articles: list[ArticleView] = []
        for r in rows:
            try:
                topics = json.loads(r["topics_json"])
                if isinstance(topics, str):
                    topics = json.loads(topics)
                if not isinstance(topics, list):
                    topics = []
            except (json.JSONDecodeError, TypeError, ValueError):
                topics = []
            topic_slugs = [_safe_tag_slug(t) for t in topics]
            try:
                key_quotes = json.loads(r["key_quotes_json"])
                if isinstance(key_quotes, str):
                    key_quotes = json.loads(key_quotes)
                if not isinstance(key_quotes, list):
                    key_quotes = []
            except (json.JSONDecodeError, TypeError, ValueError):
                key_quotes = []
            articles.append(
                ArticleView(
                    id=r["id"],
                    title=r["title"],
                    url=r["url"],
                    source_name=r["source_name"],
                    author=r["author"],
                    published_at=r["published_at"],
                    edge_score=r["edge_score"] or 0,
                    value_score=r["value_score"] or 0,
                    cost_score=r["cost_score"] or 0,
                    overall_score=r["overall_score"] or 0,
                    topics=topics,
                    summary=r["summary"],
                    topic_slugs=topic_slugs,
                    key_quotes=key_quotes,
                    raw_content=r["raw_content"],
                    analyzed_at=r["analyzed_at"],
                )
            )
        return articles
    finally:
        conn.close()


def fetch_related_articles(
    db_path: Path, article_id: int, source_name: str, limit: int = 5,
) -> list[ArticleView]:
    """Return articles from the same source (excluding the current one)."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT a.id, a.title, a.url,
                   COALESCE(s.name, '') AS source_name,
                   COALESCE(a.author, '') AS author,
                   COALESCE(a.published_at, '') AS published_at,
                   an.edge_score, an.value_score, an.cost_score,
                   an.overall_score,
                   COALESCE(an.topics, '[]') AS topics_json,
                   COALESCE(an.summary, '') AS summary,
                   COALESCE(an.key_quotes, '[]') AS key_quotes_json,
                   COALESCE(a.raw_content, '') AS raw_content,
                   COALESCE(an.analyzed_at, '') AS analyzed_at
            FROM articles a
            JOIN analyses an ON an.article_id = a.id
            LEFT JOIN sources s ON s.id = a.source_id
            WHERE a.id != ? AND s.name = ? AND an.overall_score >= 5.0
            ORDER BY an.overall_score DESC
            LIMIT ?
            """,
            (article_id, source_name, limit),
        ).fetchall()
        articles: list[ArticleView] = []
        for r in rows:
            try:
                topics = json.loads(r["topics_json"])
                if isinstance(topics, str):
                    topics = json.loads(topics)
                if not isinstance(topics, list):
                    topics = []
            except (json.JSONDecodeError, TypeError, ValueError):
                topics = []
            articles.append(
                ArticleView(
                    id=r["id"],
                    title=r["title"],
                    url=r["url"],
                    source_name=r["source_name"],
                    author=r["author"],
                    published_at=r["published_at"],
                    edge_score=r["edge_score"] or 0,
                    value_score=r["value_score"] or 0,
                    cost_score=r["cost_score"] or 0,
                    overall_score=r["overall_score"] or 0,
                    topics=topics,
                    summary=r["summary"],
                    topic_slugs=[_safe_tag_slug(t) for t in topics],
                    key_quotes=[],
                    raw_content=r["raw_content"],
                    analyzed_at=r["analyzed_at"],
                )
            )
        return articles
    finally:
        conn.close()


def fetch_articles_by_week(
    db_path: Path,
    week_start: str,  # YYYY-MM-DD
    week_end: str,    # YYYY-MM-DD
    min_score: float = 5.0,
) -> list[ArticleView]:
    """Return articles published within [week_start, week_end], ordered by score DESC."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                a.id, a.title, a.url,
                COALESCE(s.name, '')      AS source_name,
                COALESCE(a.author, '')    AS author,
                COALESCE(a.published_at, '') AS published_at,
                an.edge_score, an.value_score, an.cost_score,
                an.overall_score,
                COALESCE(an.topics, '[]') AS topics_json,
                COALESCE(an.summary, '')  AS summary,
                COALESCE(an.key_quotes, '[]') AS key_quotes_json,
                COALESCE(a.raw_content, '') AS raw_content,
                COALESCE(an.analyzed_at, '') AS analyzed_at
            FROM articles a
            JOIN analyses an ON an.article_id = a.id
            LEFT JOIN sources s  ON s.id = a.source_id
            WHERE an.overall_score >= ?
              AND a.published_at >= ? AND a.published_at <= ?
            ORDER BY an.overall_score DESC, a.published_at DESC
            """,
            (min_score, week_start, week_end),
        ).fetchall()
        return [_row_to_article(r) for r in rows]
    finally:
        conn.close()


def fetch_sources_summary(
    db_path: Path,
    min_score: float = 5.0,
) -> list[dict]:
    """Return per-source summary: name, slug, article_count, avg_score, latest_article, top_topics."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                s.name AS source_name,
                COUNT(a.id) AS article_count,
                AVG(an.overall_score) AS avg_score,
                MAX(a.published_at) AS latest_date
            FROM articles a
            JOIN analyses an ON an.article_id = a.id
            JOIN sources s ON s.id = a.source_id
            WHERE an.overall_score >= ?
            GROUP BY s.name
            ORDER BY article_count DESC
            """,
            (min_score,),
        ).fetchall()
        results = []
        for r in rows:
            # Fetch latest article for this source
            latest_rows = conn.execute(
                """
                SELECT a.id, a.title, a.published_at
                FROM articles a
                JOIN analyses an ON an.article_id = a.id
                JOIN sources s ON s.id = a.source_id
                WHERE s.name = ? AND an.overall_score >= ?
                ORDER BY an.overall_score DESC
                LIMIT 1
                """,
                (r["source_name"], min_score),
            ).fetchall()
            latest = latest_rows[0] if latest_rows else None

            # Fetch top topics for this source
            topic_rows = conn.execute(
                """
                SELECT an.topics
                FROM articles a
                JOIN analyses an ON an.article_id = a.id
                JOIN sources s ON s.id = a.source_id
                WHERE s.name = ? AND an.overall_score >= ?
                """,
                (r["source_name"], min_score),
            ).fetchall()
            topic_counts: dict[str, int] = defaultdict(int)
            for tr in topic_rows:
                try:
                    tops = json.loads(tr["topics"])
                    if isinstance(tops, str):
                        tops = json.loads(tops)
                    for t in tops:
                        topic_counts[t] += 1
                except Exception:
                    pass
            top_topics = sorted(topic_counts, key=topic_counts.get, reverse=True)[:5]

            results.append({
                "name": r["source_name"],
                "slug": _safe_tag_slug(r["source_name"]),
                "article_count": r["article_count"],
                "avg_score": r["avg_score"] or 0.0,
                "latest_article": dict(latest) if latest else None,
                "top_topics": top_topics,
            })
        return results
    finally:
        conn.close()


def fetch_series_summary(
    db_path: Path,
    min_score: float = 5.0,
    min_articles: int = 2,
) -> list[dict]:
    """Return article series grouped by topic (topics with >= min_articles articles)."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT a.id, a.title, a.url,
                   COALESCE(s.name, '') AS source_name,
                   COALESCE(a.author, '') AS author,
                   COALESCE(a.published_at, '') AS published_at,
                   an.edge_score, an.value_score, an.cost_score,
                   an.overall_score,
                   COALESCE(an.topics, '[]') AS topics_json,
                   COALESCE(an.summary, '') AS summary,
                   COALESCE(an.key_quotes, '[]') AS key_quotes_json,
                   COALESCE(a.raw_content, '') AS raw_content,
                   COALESCE(an.analyzed_at, '') AS analyzed_at
            FROM articles a
            JOIN analyses an ON an.article_id = a.id
            LEFT JOIN sources s ON s.id = a.source_id
            WHERE an.overall_score >= ?
            ORDER BY a.published_at DESC
            """,
            (min_score,),
        ).fetchall()
        articles = [_row_to_article(r) for r in rows]
    finally:
        conn.close()

    # Group by topic
    topic_articles: dict[str, list[ArticleView]] = defaultdict(list)
    for art in articles:
        for topic in art.topics:
            topic_articles[topic].append(art)

    series = []
    for topic, arts in topic_articles.items():
        if len(arts) < min_articles:
            continue
        arts.sort(key=lambda a: (a.published_at or "", a.overall_score), reverse=True)
        avg = sum(a.overall_score for a in arts) / len(arts)
        series.append({
            "topic": topic,
            "slug": _safe_tag_slug(topic),
            "article_count": len(arts),
            "avg_score": avg,
            "first_date": arts[-1].published_at or "",
            "last_date": arts[0].published_at or "",
            "latest_article": arts[0],
            "articles": arts,
        })
    series.sort(key=lambda s: s["article_count"], reverse=True)
    return series


def _row_to_article(r) -> ArticleView:
    """Convert a DB row to an ArticleView (factored out for reuse)."""
    try:
        topics = json.loads(r["topics_json"])
        if isinstance(topics, str):
            topics = json.loads(topics)
        if not isinstance(topics, list):
            topics = []
    except (json.JSONDecodeError, TypeError, ValueError):
        topics = []
    try:
        key_quotes = json.loads(r["key_quotes_json"])
        if isinstance(key_quotes, str):
            key_quotes = json.loads(key_quotes)
        if not isinstance(key_quotes, list):
            key_quotes = []
    except (json.JSONDecodeError, TypeError, ValueError):
        key_quotes = []
    return ArticleView(
        id=r["id"],
        title=r["title"],
        url=r["url"],
        source_name=r["source_name"],
        author=r["author"],
        published_at=r["published_at"],
        edge_score=r["edge_score"] or 0,
        value_score=r["value_score"] or 0,
        cost_score=r["cost_score"] or 0,
        overall_score=r["overall_score"] or 0,
        topics=topics,
        summary=r["summary"],
        topic_slugs=[_safe_tag_slug(t) for t in topics],
        key_quotes=key_quotes,
        raw_content=r["raw_content"],
        analyzed_at=r["analyzed_at"],
        title_fr=r["title_fr"] if "title_fr" in r.keys() else "",
    )


def fetch_tag_map(
    db_path: Path, min_score: float = 5.0
) -> dict[str, list[ArticleView]]:
    """Return a mapping of tag_slug -> list[ArticleView] for all tags."""
    articles = fetch_published_articles(db_path, min_score)
    tag_map: dict[str, list[ArticleView]] = defaultdict(list)
    for art in articles:
        for tag in art.topics:
            slug = _safe_tag_slug(tag)
            tag_map[slug].append(art)
    # Sort each tag's articles by score
    for slug in tag_map:
        tag_map[slug].sort(key=lambda a: a.overall_score, reverse=True)
    return dict(tag_map)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class Generator:
    """
    Generates the static site.

    Parameters
    ----------
    db_path : Path
        Path to the SQLite database.
    output_dir : Path
        Directory where HTML files will be written.
    templates_dir : Path
        Directory containing Jinja2 templates.
    min_score : float
        Minimum overall_score for an article to be included.
    site_url : str
        Absolute URL of the site (used for canonical links).
    lang : str
        Language code for i18n (default ``"fr"``).
    """

    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        output_dir: Path = DEFAULT_OUTPUT_DIR,
        templates_dir: Path = DEFAULT_TEMPLATES_DIR,
        min_score: float = 5.0,
        site_url: str = "",
        lang: str = "fr",
    ) -> None:
        self.db_path = Path(db_path)
        self.output_dir = Path(output_dir)
        self.templates_dir = Path(templates_dir)
        self.min_score = min_score
        self.site_url = site_url
        self.lang = lang
        self.stats = GeneratorStats()

        # Hash tracking for incremental generation
        self._old_hashes: dict[str, str] = {}
        self._load_old_hashes()

        # Jinja2 env
        self._env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=True,
            keep_trailing_newline=True,
        )
        # Make available to all templates
        self._env.globals["generated_at"] = GENERATED_AT
        self._env.globals["t"] = lambda key, **kw: translate(key, lang=self.lang, **kw)
        self._env.globals["lang"] = self.lang
        self._env.globals["zip"] = zip
        self._env.globals["site_url"] = self.site_url
        self._env.globals["api_base"] = os.environ.get("EDGE_API_BASE", "")

    # -- hash management ----------------------------------------------------

    def _load_old_hashes(self) -> None:
        """Load previously stored hashes from the hash directory."""
        if not HASH_DIR.exists():
            return
        for hash_file in HASH_DIR.rglob("*.md5"):
            try:
                self._old_hashes[str(hash_file)] = hash_file.read_text(
                    encoding="utf-8"
                ).strip()
            except OSError:
                pass

    def _page_changed(self, output_path: Path, content: str) -> bool:
        """Check if a page needs regeneration."""
        if not output_path.exists():
            return True
        new_hash = _content_hash(content)
        hash_file = _hash_path(output_path, self.output_dir)
        old = self._old_hashes.get(str(hash_file), "")
        if hash_file.exists() and old:
            return old != new_hash
        if hash_file.exists():
            old = hash_file.read_text(encoding="utf-8").strip()
            return old != new_hash
        return True

    def _write_page(self, output_path: Path, content: str) -> bool:
        """
        Write a page to disk if it has changed. Returns True if written.
        """
        if not self._page_changed(output_path, content):
            self.stats.pages_skipped += 1
            return False
        _write_with_hash(output_path, content, self.output_dir)
        self.stats.pages_generated += 1
        logger.debug("  → %s", output_path.relative_to(self.output_dir))
        return True

    # -- data helpers -------------------------------------------------------

    def _get_articles(self) -> list[ArticleView]:
        return fetch_published_articles(self.db_path, self.min_score)

    def _group_by_date(
        self, articles: list[ArticleView]
    ) -> dict[str, list[ArticleView]]:
        groups: dict[str, list[ArticleView]] = defaultdict(list)
        for art in articles:
            day = (art.published_at or "")[:10]  # YYYY-MM-DD
            if not day:
                day = "unknown"
            groups[day].append(art)
        # Sort each date by score desc
        for d in groups:
            groups[d].sort(key=lambda a: a.overall_score, reverse=True)
        return dict(groups)

    def _group_by_month(
        self, articles: list[ArticleView]
    ) -> list[tuple[str, list[ArticleView]]]:
        """Return list of (month_label, articles) sorted newest first."""
        groups: dict[str, list[ArticleView]] = defaultdict(list)
        for art in articles:
            key = (art.published_at or "")[:7]  # YYYY-MM
            if not key:
                key = "unknown"
            groups[key].append(art)
        for k in groups:
            groups[k].sort(key=lambda a: (a.published_at or "", a.overall_score), reverse=True)
        return sorted(groups.items(), key=lambda x: x[0], reverse=True)

    def _build_tag_cloud(
        self, articles: list[ArticleView]
    ) -> list[tuple[str, int, str]]:
        """Return (tag_name, count, slug) sorted by count desc."""
        counts: dict[str, int] = defaultdict(int)
        for art in articles:
            for tag in art.topics:
                counts[tag] += 1
        return sorted(
            [(tag, count, _safe_tag_slug(tag)) for tag, count in counts.items()],
            key=lambda x: x[1],
            reverse=True,
        )

    # -- page renderers -----------------------------------------------------

    def _render_home(
        self,
        date_articles: list[ArticleView],
        available_dates: list[str],
        date_today: str,
    ) -> str:
        """Render the home (index) page."""
        avg = (
            sum(a.overall_score for a in date_articles) / len(date_articles)
            if date_articles
            else 0.0
        )
        tmpl = self._env.get_template("index.html")
        return tmpl.render(
            articles=date_articles,
            available_dates=available_dates,
            date_today=date_today,
            avg_score=f"{avg:.1f}",
            active_page="home",
        )

    def _render_all_pages(
        self,
        db_path: Path,
        min_score: float,
        per_page: int = 20,
    ) -> int:
        """Generate paginated 'all articles' pages. Returns number of pages."""
        total = fetch_all_articles_paged(db_path, min_score, page=1, per_page=per_page)[1]
        total_pages = max(1, (total + per_page - 1) // per_page)
        tag_cloud = self._build_tag_cloud(
            fetch_all_articles_paged(db_path, min_score, page=1, per_page=1000)[0]
        )

        for page_num in range(1, total_pages + 1):
            page_articles, _ = fetch_all_articles_paged(db_path, min_score, page=page_num, per_page=per_page)
            avg = (
                sum(a.overall_score for a in page_articles) / len(page_articles)
                if page_articles
                else 0.0
            )
            tmpl = self._env.get_template("all.html")
            content = tmpl.render(
                articles=page_articles,
                total_articles=total,
                page_num=page_num,
                total_pages=total_pages,
                per_page=per_page,
                tag_cloud=tag_cloud,
                active_page="all",
            )
            if page_num == 1:
                self._write_page(self.output_dir / "all.html", content)
            else:
                self._write_page(self.output_dir / f"all-{page_num}.html", content)
        return total_pages

    def _render_search_page(
        self,
        db_path: Path,
        query: str,
        min_score: float,
    ) -> None:
        """Generate a search results page for the given query."""
        results = search_articles(db_path, query, min_score)
        tmpl = self._env.get_template("search.html")
        content = tmpl.render(
            articles=results,
            query=query,
            active_page="search",
        )
        # Write to a sanitized filename
        safe_name = _safe_tag_slug(query)[:50]
        self._write_page(self.output_dir / f"search-{safe_name}.html", content)

    def _render_date_page(
        self,
        date_str: str,
        date_articles: list[ArticleView],
        available_dates: list[str],
    ) -> str:
        """Render a per-date digest page."""
        avg = (
            sum(a.overall_score for a in date_articles) / len(date_articles)
            if date_articles
            else 0.0
        )
        tmpl = self._env.get_template("index.html")
        return tmpl.render(
            articles=date_articles,
            available_dates=available_dates,
            date_today=date_str,
            avg_score=f"{avg:.1f}",
            active_page="home",
        )

    def _render_article_page(self, article: ArticleView, related: list[ArticleView] | None = None) -> str:
        """Render a single article page."""
        tmpl = self._env.get_template("article.html")
        return tmpl.render(article=article, related_articles=related or [], active_page="article")

    def _render_tag_page(self, tag_slug: str, articles: list[ArticleView]) -> str:
        """Render a tag listing page (display-name aware)."""
        display_name = tag_slug
        for art in articles:
            for t in art.topics:
                if _safe_tag_slug(t) == tag_slug:
                    display_name = t
                    break
            if display_name != tag_slug:
                break

        tmpl = self._env.get_template("tag.html")
        return tmpl.render(tag=display_name, articles=articles, active_page="tags")

    def _render_archives_page(
        self,
        months: list[tuple[str, list[ArticleView]]],
        tag_cloud: list[tuple[str, int, str]],
    ) -> str:
        """Render the chronological archives page."""
        tmpl = self._env.get_template("archives.html")
        return tmpl.render(
            months=months,
            tag_cloud=tag_cloud,
            active_page="archives",
        )

    # -- digest page ---------------------------------------------------------

    def _render_digest_page(
        self,
        week_start: str,
        week_end: str,
        articles: list[ArticleView],
        prev_week_url: str | None = None,
        next_week_url: str | None = None,
    ) -> str:
        """Render a weekly digest page."""
        top_articles = [a for a in articles if a.overall_score >= 7.0]
        avg = sum(a.overall_score for a in articles) / len(articles) if articles else 0.0

        # Group by day
        days: dict[str, list[ArticleView]] = defaultdict(list)
        for art in articles:
            day = (art.published_at or "")[:10]
            if day:
                days[day].append(art)
        for d in days:
            days[d].sort(key=lambda a: a.overall_score, reverse=True)

        # Topic cloud
        topic_counts: dict[str, int] = defaultdict(int)
        sources_set: set[str] = set()
        for art in articles:
            sources_set.add(art.source_name)
            for t in art.topics:
                topic_counts[t] += 1
        topic_cloud = sorted(
            [{"topic": t, "count": c, "slug": _safe_tag_slug(t)} for t, c in topic_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:20]
        top_topic = topic_cloud[0]["topic"] if topic_cloud else "—"

        tmpl = self._env.get_template("digest.html")
        return tmpl.render(
            articles=articles,
            top_articles=top_articles,
            days=dict(days),
            week_start=week_start,
            week_end=week_end,
            avg_score=f"{avg:.1f}",
            top_topic=top_topic,
            sources_count=len(sources_set),
            topic_cloud=topic_cloud,
            prev_week_url=prev_week_url,
            next_week_url=next_week_url,
            active_page="digest",
        )

    # -- newsletter page -----------------------------------------------------

    def _render_newsletter(
        self,
        week_start: str,
        week_end: str,
        articles: list[ArticleView],
    ) -> str:
        """Render the HTML newsletter."""
        top_articles = [a for a in articles if a.overall_score >= 7.0][:5]
        rest = [a for a in articles if a.overall_score < 7.0][:10]
        avg = sum(a.overall_score for a in articles) / len(articles) if articles else 0.0
        sources_set = set(a.source_name for a in articles)

        tmpl = self._env.get_template("newsletter.html")
        return tmpl.render(
            articles=articles,
            top_articles=top_articles,
            rest_articles=rest,
            week_start=week_start,
            week_end=week_end,
            avg_score=f"{avg:.1f}",
            sources_count=len(sources_set),
            site_url=self.site_url,
            active_page="newsletter",
        )

    # -- sources pages -------------------------------------------------------

    def _render_sources_page(self, sources: list[dict]) -> str:
        """Render the sources listing page."""
        total_articles = sum(s["article_count"] for s in sources)
        avg = (
            sum(s["avg_score"] * s["article_count"] for s in sources) / total_articles
            if total_articles
            else 0.0
        )
        tmpl = self._env.get_template("sources.html")
        return tmpl.render(
            sources=sources,
            total_articles=total_articles,
            avg_score=f"{avg:.1f}",
            active_page="sources",
        )

    def _render_source_detail_page(
        self, source_name: str, articles: list[ArticleView]
    ) -> str:
        """Render a single source detail page."""
        avg = sum(a.overall_score for a in articles) / len(articles) if articles else 0.0
        tmpl = self._env.get_template("source-detail.html")
        return tmpl.render(
            source_name=source_name,
            articles=articles,
            avg_score=f"{avg:.1f}",
            first_date=articles[-1].published_at if articles else "",
            last_date=articles[0].published_at if articles else "",
            active_page="sources",
        )

    # -- series pages --------------------------------------------------------

    def _render_series_page(self, series: list[dict]) -> str:
        """Render the series listing page."""
        tmpl = self._env.get_template("series.html")
        return tmpl.render(series=series, active_page="series")

    def _render_series_detail_page(
        self, topic: str, articles: list[ArticleView]
    ) -> str:
        """Render a single series detail page."""
        avg = sum(a.overall_score for a in articles) / len(articles) if articles else 0.0
        sources_set = set(a.source_name for a in articles)
        tmpl = self._env.get_template("series-detail.html")
        return tmpl.render(
            topic=topic,
            articles=articles,
            avg_score=f"{avg:.1f}",
            first_date=articles[-1].published_at if articles else "",
            last_date=articles[0].published_at if articles else "",
            sources_count=len(sources_set),
            active_page="series",
        )

    # -- manifeste page -------------------------------------------------------

    def _render_manifeste(self) -> str:
        """Render the manifesto / about page."""
        tmpl = self._env.get_template("manifeste.html")
        return tmpl.render(active_page="manifeste")

    # -- trends page ---------------------------------------------------------

    def _render_trends_page(self, trends_data: dict) -> str:
        """Render the trends page from pre-computed trends JSON data."""
        top_trends = trends_data.get("top_trends", [])
        emerging = trends_data.get("emerging", [])
        declining = trends_data.get("declining", [])
        period = trends_data.get("period", {})
        recent = period.get("recent", {})
        previous = period.get("previous", {})

        # Compute max values for bar scaling
        max_trend_score = max(
            (t.get("trend_score", 0) for t in top_trends), default=1.0
        )
        max_emerging_count = max(
            (t.get("recent_count", 0) for t in emerging), default=1
        )

        tmpl = self._env.get_template("trends.html")
        return tmpl.render(
            top_trends=top_trends,
            emerging=emerging,
            declining=declining,
            recent_start=recent.get("start", ""),
            recent_end=recent.get("end", ""),
            previous_start=previous.get("start", ""),
            previous_end=previous.get("end", ""),
            max_trend_score=max_trend_score,
            max_emerging_count=max_emerging_count,
            active_page="trends",
        )

    # -- main entry point ---------------------------------------------------

    def generate(self) -> GeneratorStats:
        """
        Generate all pages of the static site.

        Returns
        -------
        GeneratorStats
        """
        self.stats = GeneratorStats(start_time=time.time())
        self.output_dir.mkdir(parents=True, exist_ok=True)
        HASH_DIR.mkdir(parents=True, exist_ok=True)

        # Copy PWA assets to root of output (not static/ subdir — Docker overlay issue)
        static_dir = Path(__file__).parent / 'static'
        if static_dir.exists():
            for item in static_dir.iterdir():
                dest = self.output_dir / item.name
                if item.is_dir():
                    shutil.copytree(str(item), dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(str(item), dest)
            logger.info("Copied PWA assets → %s", self.output_dir)

        logger.info("=" * 60)
        logger.info("EDGE Generator — starting run")
        logger.info("  DB      : %s", self.db_path)
        logger.info("  Output  : %s", self.output_dir)
        logger.info("  Min score: %.1f", self.min_score)
        logger.info("=" * 60)

        # Fetch data
        articles = self._get_articles()
        self.stats.articles_indexed = len(articles)
        logger.info("Fetched %d articles (score ≥ %.1f)", len(articles), self.min_score)

        if not articles:
            logger.warning("No articles to publish — generating empty index only.")
            tmpl = self._env.get_template("index.html")
            content = tmpl.render(
                articles=[],
                available_dates=[],
                date_today=date.today().isoformat(),
                avg_score="0.0",
                active_page="home",
            )
            self._write_page(self.output_dir / "index.html", content)
            self.stats.end_time = time.time()
            return self.stats

        available_dates = fetch_available_dates(self.db_path, self.min_score)
        self.stats.dates_indexed = len(available_dates)
        date_groups = self._group_by_date(articles)
        tag_map = fetch_tag_map(self.db_path, self.min_score)
        tag_cloud = self._build_tag_cloud(articles)
        months = self._group_by_month(articles)
        today_str = date.today().isoformat()

        # ---- Home page (today) ----
        logger.info("Generating home page…")
        today_articles = date_groups.get(today_str, [])
        # If today has fewer than 10 articles, pad with top recent articles
        # from other dates so the front page is never empty
        if len(today_articles) < 10:
            seen_ids = {a.id for a in today_articles}
            fallback = sorted(
                [a for a in articles if a.id not in seen_ids],
                key=lambda a: a.overall_score,
                reverse=True,
            )[:20]
            home_display = today_articles + fallback
        else:
            home_display = today_articles[:30]
        home_html = self._render_home(home_display, available_dates, today_str)
        self._write_page(self.output_dir / "index.html", home_html)

        # ---- Date pages ----
        for date_str, arts in date_groups.items():
            if date_str == today_str:
                continue  # index.html already covers today
            date_html = self._render_date_page(date_str, arts, available_dates)
            self._write_page(self.output_dir / f"{date_str}.html", date_html)

        # ---- Article pages ----
        logger.info("Generating article pages (%d)…", len(articles))
        for art in articles:
            try:
                related = fetch_related_articles(
                    self.db_path, art.id, art.source_name, limit=5
                )
                art_html = self._render_article_page(art, related)
                self._write_page(self.output_dir / "articles" / f"{art.id}.html", art_html)
            except Exception as exc:
                self.stats.errors += 1
                logger.error("Failed to render article %d: %s", art.id, exc)

        # ---- All articles pages (paginated) ----
        logger.info("Generating all-articles pages…")
        num_all_pages = self._render_all_pages(self.db_path, self.min_score, per_page=20)
        self.stats.pages_generated += num_all_pages

        # ---- Search pages (pre-generate for common queries) ----
        logger.info("Generating search pages…")
        self._render_search_page(self.db_path, "AI", self.min_score)
        self._render_search_page(self.db_path, "edge computing", self.min_score)
        self._render_search_page(self.db_path, "startup", self.min_score)

        # ---- Tag pages ----
        logger.info("Generating tag pages (%d)…", len(tag_map))
        self.stats.tags_indexed = len(tag_map)
        for slug, tagged_articles in tag_map.items():
            try:
                tag_html = self._render_tag_page(slug, tagged_articles)
                self._write_page(self.output_dir / "tags" / f"{slug}.html", tag_html)
            except Exception as exc:
                self.stats.errors += 1
                logger.error("Failed to render tag %s: %s", slug, exc)

        # ---- Archives page ----
        logger.info("Generating archives page…")
        archives_html = self._render_archives_page(months, tag_cloud)
        self._write_page(self.output_dir / "archives.html", archives_html)

        # ---- Weekly Digest page ----
        logger.info("Generating weekly digest…")
        from datetime import timedelta
        today = date.today()
        # Find Monday of current week
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        week_start = monday.isoformat()
        week_end = sunday.isoformat()
        # Previous week
        prev_monday = monday - timedelta(days=7)
        prev_sunday = prev_monday + timedelta(days=6)
        # Next week (only if not in future)
        next_monday = monday + timedelta(days=7)
        next_sunday = next_monday + timedelta(days=6)
        prev_week_url = f"/digest-{prev_monday.isoformat()}.html" if prev_monday >= date(2025, 1, 1) else None
        next_week_url = f"/digest-{next_monday.isoformat()}.html" if next_monday <= today else None

        digest_articles = fetch_articles_by_week(self.db_path, week_start, week_end, self.min_score)
        if digest_articles:
            digest_html = self._render_digest_page(week_start, week_end, digest_articles, prev_week_url, next_week_url)
            self._write_page(self.output_dir / f"digest-{week_start}.html", digest_html)
            # Also write as index-digest.html for easy access
            self._write_page(self.output_dir / "digest.html", digest_html)
            logger.info("  Digest: %d articles for week %s → %s", len(digest_articles), week_start, week_end)
        else:
            logger.info("  No articles for current week, skipping digest")

        # ---- Newsletter HTML ----
        logger.info("Generating newsletter HTML…")
        if digest_articles:
            newsletter_html = self._render_newsletter(week_start, week_end, digest_articles)
            self._write_page(self.output_dir / "newsletter.html", newsletter_html)

        # ---- Sources pages ----
        logger.info("Generating sources pages…")
        sources = fetch_sources_summary(self.db_path, self.min_score)
        if sources:
            sources_html = self._render_sources_page(sources)
            self._write_page(self.output_dir / "sources.html", sources_html)
            # Per-source detail pages
            for src in sources:
                src_articles = fetch_published_articles(self.db_path, self.min_score)
                src_articles = [a for a in src_articles if a.source_name == src["name"]]
                if src_articles:
                    src_html = self._render_source_detail_page(src["name"], src_articles)
                    self._write_page(self.output_dir / "sources" / f"{src['slug']}.html", src_html)
            logger.info("  Sources: %d sources, %d with detail pages", len(sources), len([s for s in sources if s["article_count"] > 0]))

        # ---- Series pages ----
        logger.info("Generating series pages…")
        series = fetch_series_summary(self.db_path, self.min_score, min_articles=2)
        if series:
            series_html = self._render_series_page(series)
            self._write_page(self.output_dir / "series.html", series_html)
            # Per-series detail pages (top 20)
            for s in series[:20]:
                s_html = self._render_series_detail_page(s["topic"], s["articles"])
                self._write_page(self.output_dir / "series" / f"{s['slug']}.html", s_html)
            logger.info("  Series: %d topics with ≥2 articles", len(series))

        # ---- Trends page ----
        logger.info("Generating trends page…")
        trends_json_path = self.output_dir / "trends.json"
        if trends_json_path.exists():
            try:
                trends_data = json.loads(trends_json_path.read_text(encoding="utf-8"))
                trends_html = self._render_trends_page(trends_data)
                self._write_page(self.output_dir / "trends.html", trends_html)
                logger.info("  Trends page generated from %s", trends_json_path)
            except Exception as exc:
                self.stats.errors += 1
                logger.error("Failed to render trends page: %s", exc)
        else:
            logger.info("  No trends.json found — run trends.py first to generate it")

        # ---- Manifeste page ----
        logger.info("Generating manifeste page…")
        manifeste_html = self._render_manifeste()
        filename = "manifesto.html" if self.lang == "en" else "manifeste.html"
        self._write_page(self.output_dir / filename, manifeste_html)

        # ---- SEO artefacts ----
        logger.info("Generating SEO artefacts (sitemap, robots, feed)…")
        try:
            generate_sitemap(self.db_path, self.output_dir, self.site_url)
            generate_robots(self.output_dir, self.site_url)
            generate_feed(self.db_path, self.output_dir, self.lang)
        except Exception as exc:
            self.stats.errors += 1
            logger.error("SEO artefact generation failed: %s", exc)

        self.stats.end_time = time.time()

        logger.info("=" * 60)
        logger.info("EDGE Generator — run complete")
        logger.info("  Articles indexed : %d", self.stats.articles_indexed)
        logger.info("  Tags indexed     : %d", self.stats.tags_indexed)
        logger.info("  Dates indexed    : %d", self.stats.dates_indexed)
        logger.info("  Pages generated  : %d", self.stats.pages_generated)
        logger.info("  Pages skipped    : %d", self.stats.pages_skipped)
        logger.info("  Errors           : %d", self.stats.errors)
        logger.info("  Elapsed          : %.1fs", self.stats.elapsed_seconds)
        logger.info("=" * 60)

        return self.stats


# ---------------------------------------------------------------------------
# Convenience entry points
# ---------------------------------------------------------------------------


def generate_site(
    db_path: str | Path,
    output_dir: str | Path,
    *,
    templates_dir: str | Path = DEFAULT_TEMPLATES_DIR,
    min_score: float = 5.0,
    site_url: str = "",
    lang: str = "fr",
) -> GeneratorStats:
    """
    Generate the full static site from a database path and output directory.
    Rebuilds FTS5 index after generation.
    """
    generator = Generator(
        db_path=Path(db_path),
        output_dir=Path(output_dir),
        templates_dir=Path(templates_dir),
        min_score=min_score,
        site_url=site_url,
        lang=lang,
    )
    stats = generator.generate()
    # Rebuild FTS5 index
    try:
        fts_count = rebuild_fts_index(db_path)
        logger.info("FTS5 index rebuilt: %d rows", fts_count)
    except Exception as exc:
        logger.warning("FTS5 index rebuild failed: %s", exc)
    return stats


def rebuild_fts_index(db_path: str | Path) -> int:
    """Rebuild the FTS5 index from articles + analyses. Returns count of indexed rows."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("DELETE FROM articles_fts")
        rows = conn.execute(
            """
            INSERT INTO articles_fts(rowid, title, content, summary, tags)
            SELECT
                a.id,
                a.title,
                COALESCE(a.content, ''),
                COALESCE(an.summary, ''),
                COALESCE(an.topics, '')
            FROM articles a
            JOIN analyses an ON an.article_id = a.id
            WHERE an.overall_score >= 5.0
            """
        ).rowcount
        conn.commit()
        return rows
    finally:
        conn.close()


def run_generator(
    config_path: str | Path = "config/sources.yaml",
    db_path: str | Path = DEFAULT_DB_PATH,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    templates_dir: str | Path = DEFAULT_TEMPLATES_DIR,
    min_score: float = 5.0,
    site_url: str = "",
    lang: str = "fr",
) -> GeneratorStats:
    """Convenience wrapper: resolve paths relative to config, then generate."""
    config_path = Path(config_path)
    if str(db_path) == str(DEFAULT_DB_PATH):
        db_path = config_path.parent / "data" / "edge.db"
    return generate_site(
        db_path=db_path,
        output_dir=output_dir,
        templates_dir=templates_dir,
        min_score=min_score,
        site_url=site_url,
        lang=lang,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="EDGE Static Site Generator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("config", help="Path to sources YAML config")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path")
    parser.add_argument(
        "--output", default=str(DEFAULT_OUTPUT_DIR), help="Output directory"
    )
    parser.add_argument(
        "--templates", default=str(DEFAULT_TEMPLATES_DIR), help="Templates directory"
    )
    parser.add_argument(
        "--min-score", type=float, default=5.0, help="Minimum score for inclusion"
    )
    parser.add_argument("--site-url", default="", help="Site base URL")
    parser.add_argument(
        "--lang", default="fr", choices=["fr", "en"], help="UI language"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="DEBUG logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    stats = run_generator(
        config_path=args.config,
        db_path=args.db,
        output_dir=args.output,
        templates_dir=args.templates,
        min_score=args.min_score,
        site_url=args.site_url,
        lang=args.lang,
    )

    print(json.dumps(stats.as_dict(), indent=2, ensure_ascii=False))

    if stats.errors > 0:
        sys.exit(1)
