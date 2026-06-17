#!/usr/bin/env python3
"""
EDGE — LLM Article Analyzer
=============================

Production-ready analyzer that scores and summarizes tech articles stored in
the EDGE SQLite database using an OpenRouter-backed LLM (via the OpenAI-compatible
client).

Two-pass analysis per batch of articles:
  1. **Scoring**  — relevance on 3 axes (edge / value / cost) + topic tags
  2. **Summary**  — 3-5 sentence journalistic summary in French + key quotes

Only articles whose ``overall_score >= min_score`` (default 5) are persisted to
the ``analyses`` table.

Usage:
    from analyzer import run_analyzer
    stats = run_analyzer("config/sources.yaml", min_score=5)

    # Or from the CLI:
    python analyzer.py config/sources.yaml --min-score 5

Dependencies:
    openai (v2+), pyyaml
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

try:
    from openai import OpenAI, APIStatusError, RateLimitError, APIError
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "The 'openai' package is required. Install it with: pip install 'openai>=2.0'"
    ) from exc

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("edge.analyzer")

# ---------------------------------------------------------------------------
# Project .env loader
# ---------------------------------------------------------------------------

def _load_project_env() -> None:
    """Load .env from the project root into os.environ (non-destructive)."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass

_load_project_env()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "edge.db"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openrouter/owl-alpha"
BATCH_SIZE = 5
MAX_RETRIES = 5
RETRY_BASE_DELAY = 2.0  # seconds — exponential backoff base
REQUEST_TIMEOUT = 120  # seconds
MAX_CONTENT_CHARS = 6000  # truncate article content sent to the LLM
MAX_SUMMARY_CHARS = 3000  # truncate content for the summary pass

# System prompts
_SCORING_SYSTEM_PROMPT = """Tu es un expert en edge computing, systèmes distribués et économie technologique.
Tu dois évaluer la pertinence d'articles de presse tech selon 3 axes.

Pour chaque article, attribue une note de 0 à 10 sur chaque axe :
- edge_score : pertinence edge computing / systèmes distribués / traitement au bord / IoT / fog computing / CDN / infrastructure décentralisée
- value_score : pertinence création de valeur / modèles économiques / ROI / monétisation / avantage compétitif
- cost_score : pertinence économie de coûts / optimisation / frugalité / réduction des dépenses / efficience

Identifie aussi 2-5 topics/thèmes principaux (en français, format court, 1-3 mots max par topic).

Réponds UNIQUEMENT en JSON valide, sans markdown, sans texte avant ou après.
Format de sortie :
{
  "articles": [
    {
      "index": <numéro de l'article dans la liste, 0-based>,
      "edge_score": <0-10>,
      "value_score": <0-10>,
      "cost_score": <0-10>,
      "topics": ["topic1", "topic2"],
      "title_fr": "<titre traduit en français, accrocheur, journalistique>"
    }
  ]
}"""

_SUMMARY_SYSTEM_PROMPT = """Tu es un journaliste tech français spécialisé en edge computing et systèmes distribués.
Tu dois résumer des articles en français, dans un style journalistique clair et concis.

Pour chaque article :
- Résume en 3-5 phrases maximum, en français
- Extrait 1-3 citations clés ou phrases marquantes (traduis-les en français si elles sont dans une autre langue)

Réponds UNIQUEMENT en JSON valide, sans markdown, sans texte avant ou après.
Format de sortie :
{
  "articles": [
    {
      "index": <numéro de l'article dans la liste, 0-based>,
      "summary": "<résumé en français, 3-5 phrases>",
      "key_quotes": ["citation 1", "citation 2"]
    }
  ]
}"""

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Article:
    """Lightweight representation of an article row from the database."""

    id: int
    title: str
    content: str
    url: str
    author: str = ""
    published_at: str = ""


@dataclass
class AnalysisResult:
    """Result of the two-pass LLM analysis for a single article."""

    article_id: int
    edge_score: float = 0.0
    value_score: float = 0.0
    cost_score: float = 0.0
    overall_score: float = 0.0
    topics: list[str] = field(default_factory=list)
    title_fr: str = ""
    summary: str = ""
    key_quotes: list[str] = field(default_factory=list)
    llm_model: str = ""
    tokens_used: int = 0


@dataclass
class AnalyzerStats:
    """Aggregated statistics for an analyzer run."""

    total_articles: int = 0
    batches_processed: int = 0
    scored: int = 0
    summarized: int = 0
    stored: int = 0
    filtered_out: int = 0
    errors: int = 0
    total_tokens: int = 0
    total_retries: int = 0
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def elapsed_seconds(self) -> float:
        end = self.end_time or time.time()
        return end - self.start_time

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_articles": self.total_articles,
            "batches_processed": self.batches_processed,
            "scored": self.scored,
            "summarized": self.summarized,
            "stored": self.stored,
            "filtered_out": self.filtered_out,
            "errors": self.errors,
            "total_tokens": self.total_tokens,
            "total_retries": self.total_retries,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

# Schema for the analyses table — kept alongside the module that uses it
ANALYSES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY,
    article_id INTEGER UNIQUE NOT NULL,
    edge_score REAL DEFAULT 0,
    value_score REAL DEFAULT 0,
    cost_score REAL DEFAULT 0,
    overall_score REAL DEFAULT 0,
    topics TEXT DEFAULT '[]',
    summary TEXT,
    key_quotes TEXT DEFAULT '[]',
    llm_model TEXT,
    tokens_used INTEGER DEFAULT 0,
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (article_id) REFERENCES articles(id)
);

CREATE INDEX IF NOT EXISTS idx_analyses_score ON analyses(overall_score);
CREATE INDEX IF NOT EXISTS idx_analyses_article ON analyses(article_id);
"""


def _get_db(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection and ensure the schema exists."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(ANALYSES_SCHEMA_SQL)
    conn.commit()
    return conn


def fetch_unanalyzed_articles(db_path: str | Path, limit: int | None = None) -> list[Article]:
    """
    Return articles that do **not** yet have a row in ``analyses``.

    Articles are ordered by ``id`` DESC (newest first) so the freshest content
    gets analysed when the run is interrupted.

    Parameters
    ----------
    limit : int, optional
        Maximum number of articles to return. ``None`` means no limit (default).

    Returns
    -------
    list[Article]
    """
    conn = _get_db(db_path)
    try:
        query = """
            SELECT a.id, a.title, a.content, a.url, a.author, a.published_at
            FROM articles a
            LEFT JOIN analyses an ON an.article_id = a.id
            WHERE an.id IS NULL
            ORDER BY a.id DESC
        """
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        rows = conn.execute(query).fetchall()
        return [
            Article(
                id=r[0],
                title=r[1],
                content=r[2] or "",
                url=r[3],
                author=r[4] or "",
                published_at=r[5] or "",
            )
            for r in rows
        ]
    finally:
        conn.close()


def store_analysis(db_path: str | Path, result: AnalysisResult) -> None:
    """Insert or replace an analysis row."""
    conn = _get_db(db_path)
    try:
        # Ensure title_fr column exists
        try:
            conn.execute("ALTER TABLE analyses ADD COLUMN title_fr TEXT DEFAULT ''")
        except Exception:
            pass
        conn.execute(
            """
            INSERT INTO analyses
                (article_id, edge_score, value_score, cost_score,
                 overall_score, topics, title_fr, summary, key_quotes,
                 llm_model, tokens_used, analyzed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET
                edge_score = excluded.edge_score,
                value_score = excluded.value_score,
                cost_score = excluded.cost_score,
                overall_score = excluded.overall_score,
                topics = excluded.topics,
                title_fr = excluded.title_fr,
                summary = excluded.summary,
                key_quotes = excluded.key_quotes,
                llm_model = excluded.llm_model,
                tokens_used = excluded.tokens_used,
                analyzed_at = excluded.analyzed_at
            """,
            (
                result.article_id,
                result.edge_score,
                result.value_score,
                result.cost_score,
                result.overall_score,
                json.dumps(result.topics, ensure_ascii=False),
                result.title_fr,
                result.summary,
                json.dumps(result.key_quotes, ensure_ascii=False),
                result.llm_model,
                result.tokens_used,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Hermes credential helper
# ---------------------------------------------------------------------------

def _load_openrouter_key_from_hermes() -> str:
    """
    Try to load the OpenRouter API key from Hermes auth.json credential pool.
    Falls back to empty string if not found.
    """
    import json as _json
    auth_path = Path.home() / ".hermes" / "auth.json"
    if not auth_path.exists():
        return ""
    try:
        with open(auth_path) as f:
            data = _json.load(f)
        pool = data.get("credential_pool", {})
        creds = pool.get("openrouter", [])
        for cred in creds:
            # Prefer non-exhausted credentials
            if cred.get("last_status") != "exhausted":
                src = cred.get("source", "")
                if src.startswith("env:"):
                    env_var = src[4:]
                    val = os.environ.get(env_var, "")
                    if val:
                        return val
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# LLM Client wrapper
# ---------------------------------------------------------------------------


class LLMClient:
    """
    Thin wrapper around the OpenAI client pointed at OpenRouter.

    Handles:
    - API key from environment (``OPENROUTER_API_KEY``)
    - Model selection (from env ``OPENROUTER_MODEL`` or constructor arg)
    - Exponential-backoff retry on rate-limit / transient errors
    - Token accounting
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str = OPENROUTER_BASE_URL,
        timeout: float = REQUEST_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            self.api_key = _load_openrouter_key_from_hermes()
        if not self.api_key:
            raise EnvironmentError(
                "OPENROUTER_API_KEY is not set. "
                "Export it in the environment or pass api_key= to LLMClient."
            )
        self.model = model or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
        self.max_retries = max_retries
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=base_url,
            timeout=timeout,
        )
        self.total_tokens: int = 0
        self.total_retries: int = 0
        logger.info("LLMClient initialised — model=%s", self.model)

    # -- low-level call with retry -------------------------------------------

    def _chat_complete(
        self,
        system_prompt: str,
        user_content: str,
    ) -> tuple[str, int]:
        """
        Send a chat-completion request with exponential-backoff retry.

        Returns ``(response_text, tokens_used)``.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=0.1,
                    max_tokens=2048,
                )
                if not resp.choices or resp.choices[0].message is None:
                    raise APIError("LLM returned empty choices")
                text = resp.choices[0].message.content or ""
                tokens = resp.usage.total_tokens if resp.usage else 0
                self.total_tokens += tokens
                if attempt > 1:
                    logger.debug("LLM call succeeded on attempt %d", attempt)
                return text, tokens

            except RateLimitError as exc:
                self.total_retries += 1
                delay = RETRY_BASE_DELAY ** attempt
                logger.warning(
                    "Rate limited (attempt %d/%d) — retrying in %.1fs",
                    attempt,
                    self.max_retries,
                    delay,
                )
                last_exc = exc
                time.sleep(delay)

            except APIStatusError as exc:
                self.total_retries += 1
                # Retry on 5xx server errors, not on 4xx client errors
                if exc.status_code and 500 <= exc.status_code < 600:
                    delay = RETRY_BASE_DELAY ** attempt
                    logger.warning(
                        "Server error %s (attempt %d/%d) — retrying in %.1fs",
                        exc.status_code,
                        attempt,
                        self.max_retries,
                        delay,
                    )
                    last_exc = exc
                    time.sleep(delay)
                else:
                    raise

            except APIError as exc:
                self.total_retries += 1
                delay = RETRY_BASE_DELAY ** attempt
                logger.warning(
                    "API error (attempt %d/%d): %s — retrying in %.1fs",
                    attempt,
                    self.max_retries,
                    exc,
                    delay,
                )
                last_exc = exc
                time.sleep(delay)

        raise RuntimeError(
            f"LLM call failed after {self.max_retries} attempts"
        ) from last_exc

    # -- JSON extraction helper ---------------------------------------------

    @staticmethod
    def _parse_json_response(raw: str) -> dict[str, Any]:
        """
        Parse the LLM response as JSON, stripping common markdown fences.
        """
        text = raw.strip()
        # Strip ```json ... ``` fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            # Remove first line (```json or ```)
            lines = lines[1:]
            # Remove last line if it's ```
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse LLM JSON response: %s\nRaw: %s", exc, raw[:500])
            raise


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_TRUNCATION_NOTE = " [tronqué]"


def _truncate(text: str, max_len: int) -> str:
    """Truncate *text* at *max_len* chars, appending a note if cut."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + _TRUNCATION_NOTE


def _build_scoring_prompt(articles: list[Article]) -> str:
    """Build the user message for the scoring pass."""
    parts = ["Évalue les articles suivants et réponds en JSON.\n"]
    for i, art in enumerate(articles):
        content = _truncate(art.content, MAX_CONTENT_CHARS)
        parts.append(
            f"--- Article {i} ---\n"
            f"Titre : {art.title}\n"
            f"URL : {art.url}\n"
            f"Contenu :\n{content}\n"
        )
    return "\n".join(parts)


def _build_summary_prompt(articles: list[Article]) -> str:
    """Build the user message for the summary pass."""
    parts = ["Résume les articles suivants et réponds en JSON.\n"]
    for i, art in enumerate(articles):
        content = _truncate(art.content, MAX_SUMMARY_CHARS)
        parts.append(
            f"--- Article {i} ---\n"
            f"Titre : {art.title}\n"
            f"Contenu :\n{content}\n"
        )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Core analysis functions
# ---------------------------------------------------------------------------


def score_articles(
    client: LLMClient,
    articles: list[Article],
) -> dict[int, dict[str, Any]]:
    """
    Score a batch of articles on the 3 relevance axes.

    Returns a dict mapping ``article.id`` → scoring dict with keys
    ``edge_score``, ``value_score``, ``cost_score``, ``topics``.
    """
    if not articles:
        return {}

    prompt = _build_scoring_prompt(articles)
    raw, tokens = client._chat_complete(_SCORING_SYSTEM_PROMPT, prompt)
    parsed = LLMClient._parse_json_response(raw)

    results: dict[int, dict[str, Any]] = {}
    for item in parsed.get("articles", []):
        idx = item.get("index", -1)
        if 0 <= idx < len(articles):
            art = articles[idx]
            results[art.id] = {
                "edge_score": float(item.get("edge_score", 0)),
                "value_score": float(item.get("value_score", 0)),
                "cost_score": float(item.get("cost_score", 0)),
                "topics": item.get("topics", []),
                "title_fr": item.get("title_fr", ""),
                "tokens": tokens // len(articles),  # rough per-article split
            }
    return results


def summarize_articles(
    client: LLMClient,
    articles: list[Article],
) -> dict[int, dict[str, Any]]:
    """
    Summarize a batch of articles in French.

    Returns a dict mapping ``article.id`` → summary dict with keys
    ``summary``, ``key_quotes``.
    """
    if not articles:
        return {}

    prompt = _build_summary_prompt(articles)
    raw, tokens = client._chat_complete(_SUMMARY_SYSTEM_PROMPT, prompt)
    parsed = LLMClient._parse_json_response(raw)

    results: dict[int, dict[str, Any]] = {}
    for item in parsed.get("articles", []):
        idx = item.get("index", -1)
        if 0 <= idx < len(articles):
            art = articles[idx]
            results[art.id] = {
                "summary": item.get("summary", ""),
                "key_quotes": item.get("key_quotes", []),
                "tokens": tokens // len(articles),
            }
    return results


# ---------------------------------------------------------------------------
# Batch processor
# ---------------------------------------------------------------------------


def _batches(items: list[Any], size: int) -> list[list[Any]]:
    """Yield successive batches of *size* from *items*."""
    return [items[i : i + size] for i in range(0, len(items), size)]


def process_batch(
    client: LLMClient,
    articles: list[Article],
) -> list[AnalysisResult]:
    """
    Run the two-pass analysis on a single batch of articles.

    Returns a list of ``AnalysisResult`` objects (one per article).
    """
    # Pass 1 — scoring
    logger.info("  ▶ Scoring batch of %d articles…", len(articles))
    scores = score_articles(client, articles)

    # Pass 2 — summarization (only for articles that got scored)
    logger.info("  ▶ Summarizing batch of %d articles…", len(articles))
    summaries = summarize_articles(client, articles)

    results: list[AnalysisResult] = []
    for art in articles:
        s = scores.get(art.id, {})
        summ = summaries.get(art.id, {})

        edge = s.get("edge_score", 0.0)
        value = s.get("value_score", 0.0)
        cost = s.get("cost_score", 0.0)
        overall = round((edge + value + cost) / 3, 2)

        tokens = s.get("tokens", 0) + summ.get("tokens", 0)

        results.append(
            AnalysisResult(
                article_id=art.id,
                edge_score=edge,
                value_score=value,
                cost_score=cost,
                overall_score=overall,
                topics=s.get("topics", []),
                title_fr=s.get("title_fr", ""),
                summary=summ.get("summary", ""),
                key_quotes=summ.get("key_quotes", []),
                llm_model=client.model,
                tokens_used=tokens,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_analyzer(
    config_path: str | Path,
    min_score: float = 5.0,
    db_path: str | Path = DEFAULT_DB_PATH,
    model: str | None = None,
    api_key: str | None = None,
    batch_size: int = BATCH_SIZE,
    max_articles: int | None = None,
) -> AnalyzerStats:
    """
    Full analysis pipeline.

    1. Fetch unanalyzed articles from the database.
    2. Process them in batches of *batch_size*.
    3. For each batch, run scoring + summarization via the LLM.
    4. Persist results with ``overall_score >= min_score`` to the ``analyses`` table.

    Parameters
    ----------
    config_path : path-like
        YAML config file (used for path resolution; the DB path is derived
        from the project structure unless *db_path* is overridden).
    min_score : float, optional
        Minimum ``overall_score`` threshold for persisting an analysis (default 5).
    db_path : path-like, optional
        Override the SQLite database path.
    model : str, optional
        OpenRouter model string. Falls back to ``OPENROUTER_MODEL`` env var,
        then ``google/gemini-2.5-flash``.
    api_key : str, optional
        OpenRouter API key. Falls back to ``OPENROUTER_API_KEY`` env var.
    batch_size : int, optional
        Number of articles per LLM call (default 5).
    max_articles : int, optional
        Maximum number of unanalyzed articles to process this run.
        ``None`` means no limit (process all). Use this to cap LLM cost
        when the backlog is large.

    Returns
    -------
    AnalyzerStats
        Aggregated statistics for the run.
    """
    stats = AnalyzerStats(start_time=time.time())

    # Resolve DB path relative to config if not explicitly given
    config_path = Path(config_path)
    if not db_path:
        db_path = config_path.parent / "data" / "edge.db"

    logger.info("=" * 60)
    logger.info("EDGE Analyzer — starting run")
    logger.info("  Config  : %s", config_path)
    logger.info("  DB      : %s", db_path)
    logger.info("  Min score: %.1f", min_score)
    logger.info("=" * 60)

    # Initialise LLM client
    client = LLMClient(model=model, api_key=api_key)

    # Fetch unanalyzed articles (optionally capped)
    articles = fetch_unanalyzed_articles(db_path, limit=max_articles)
    stats.total_articles = len(articles)
    logger.info("Found %d unanalyzed articles", len(articles))

    if not articles:
        logger.info("Nothing to analyse — exiting.")
        stats.end_time = time.time()
        return stats

    # Process in batches
    batch_list = _batches(articles, batch_size)
    logger.info(
        "Processing %d articles in %d batches of %d",
        len(articles),
        len(batch_list),
        batch_size,
    )

    for batch_num, batch in enumerate(batch_list, start=1):
        logger.info(
            "Batch %d/%d (%d articles)",
            batch_num,
            len(batch_list),
            len(batch),
        )
        try:
            results = process_batch(client, batch)
            stats.batches_processed += 1
            stats.scored += len(results)
            stats.summarized += len(results)

            for result in results:
                if result.overall_score >= min_score:
                    store_analysis(db_path, result)
                    stats.stored += 1
                    logger.info(
                        "  ✓ Article #%d — score=%.1f (edge=%.0f value=%.0f cost=%.0f) — %s",
                        result.article_id,
                        result.overall_score,
                        result.edge_score,
                        result.value_score,
                        result.cost_score,
                        articles[
                            next(
                                i
                                for i, a in enumerate(batch)
                                if a.id == result.article_id
                            )
                        ].title[:60],
                    )
                else:
                    stats.filtered_out += 1
                    logger.debug(
                        "  ✗ Article #%d — score=%.1f (below threshold)",
                        result.article_id,
                        result.overall_score,
                    )

        except Exception as exc:
            stats.errors += 1
            logger.error(
                "Batch %d failed: %s", batch_num, exc, exc_info=True
            )

        # Small delay between batches to be gentle on the API
        if batch_num < len(batch_list):
            time.sleep(0.5)

    # Final stats
    stats.total_tokens = client.total_tokens
    stats.total_retries = client.total_retries
    stats.end_time = time.time()

    logger.info("=" * 60)
    logger.info("EDGE Analyzer — run complete")
    logger.info("  Articles found   : %d", stats.total_articles)
    logger.info("  Batches processed: %d", stats.batches_processed)
    logger.info("  Scored           : %d", stats.scored)
    logger.info("  Stored (≥%.1f)    : %d", min_score, stats.stored)
    logger.info("  Filtered out     : %d", stats.filtered_out)
    logger.info("  Errors           : %d", stats.errors)
    logger.info("  Total tokens     : %d", stats.total_tokens)
    logger.info("  Total retries    : %d", stats.total_retries)
    logger.info("  Elapsed          : %.1fs", stats.elapsed_seconds)
    logger.info("=" * 60)

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="EDGE LLM Article Analyzer",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("config", help="Path to sources YAML config")
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help="SQLite database path",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=5.0,
        help="Minimum overall score to store an analysis",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="OpenRouter model (overrides OPENROUTER_MODEL env var)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="OpenRouter API key (overrides OPENROUTER_API_KEY env var)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help="Articles per LLM batch",
    )
    parser.add_argument(
        "--max-articles",
        type=int,
        default=None,
        help="Max unanalyzed articles to process (default: all)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    stats = run_analyzer(
        config_path=args.config,
        min_score=args.min_score,
        db_path=args.db,
        model=args.model,
        api_key=args.api_key,
        batch_size=args.batch_size,
        max_articles=args.max_articles,
    )

    print(json.dumps(stats.as_dict(), indent=2, ensure_ascii=False))

    if stats.errors > 0:
        sys.exit(1)
