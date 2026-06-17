#!/usr/bin/env python3
"""
EDGE — Feedback & Auto-Improvement Module
===========================================

Provides self-monitoring and auto-tuning capabilities for the EDGE pipeline.

SQL schema additions:
  - ``pipeline_runs`` — records every pipeline execution with timing, counts,
    token usage, and cost estimates.
  - ``source_stats`` — tracks per-source quality metrics (average score,
    total articles, last successful fetch).

Functions:
  - ``ensure_feedback_schema``     — create the feedback tables if absent.
  - ``log_run(db_path, stats)``   — persist a pipeline run record.
  - ``get_daily_stats(db_path, days=7)`` — recent daily roll-up.
  - ``get_source_recommendations(db_path)`` — per-source keep/review/remove.
  - ``tune_thresholds(db_path, target_articles=15)`` — suggest min_score tweaks.

Usage:
    from feedback import log_run, get_daily_stats, tune_thresholds

    # After a pipeline run:
    log_run("data/edge.db", {"duration_seconds": 42.3, ...})

    # Inspect recent performance:
    stats = get_daily_stats("data/edge.db", days=7)

    # Get source quality recommendations:
    recs = get_source_recommendations("data/edge.db")

    # Auto-tune the score threshold:
    suggestion = tune_thresholds("data/edge.db", target_articles=15)

Dependencies:
    stdlib only (sqlite3, json, logging, pathlib)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("edge.feedback")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

FEEDBACK_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY,
    run_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    duration_seconds REAL,
    articles_scraped INTEGER,
    articles_analyzed INTEGER,
    articles_published INTEGER,
    tokens_used INTEGER,
    cost_estimate REAL,
    status TEXT DEFAULT 'success'
);

CREATE TABLE IF NOT EXISTS source_stats (
    source_id INTEGER PRIMARY KEY,
    total_articles INTEGER DEFAULT 0,
    avg_score REAL DEFAULT 0,
    last_success TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_date ON pipeline_runs(run_date);
CREATE INDEX IF NOT EXISTS idx_source_stats_score ON source_stats(avg_score);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_db(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection and ensure the feedback schema exists."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(FEEDBACK_SCHEMA_SQL)
    conn.commit()
    return conn


def ensure_feedback_schema(db_path: str | Path) -> None:
    """
    Create the feedback tables if they do not already exist.

    Safe to call at module import time or at the start of every pipeline run.

    Parameters
    ----------
    db_path : path-like
        Path to the SQLite database.
    """
    conn = _open_db(db_path)
    conn.close()
    logger.debug("Feedback schema ensured at %s", db_path)


# ---------------------------------------------------------------------------
# log_run
# ---------------------------------------------------------------------------


def log_run(
    db_path: str | Path,
    stats: dict[str, Any],
) -> int:
    """
    Record a pipeline run in the ``pipeline_runs`` table.

    Parameters
    ----------
    db_path : path-like
        Path to the SQLite database.
    stats : dict
        Run statistics. Recognised keys:

        - ``duration_seconds`` (float) — wall-clock duration.
        - ``articles_scraped`` (int) — new articles stored by the scraper.
        - ``articles_analyzed`` (int) — articles that passed the score threshold.
        - ``articles_published`` (int) — articles included in the generated site.
        - ``tokens_used`` (int) — total LLM tokens consumed.
        - ``cost_estimate`` (float) — estimated cost in USD.
        - ``status`` (str) — ``'success'``, ``'partial'``, or ``'failed'``.

    Returns
    -------
    int
        The row id of the inserted record.
    """
    conn = _open_db(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO pipeline_runs
                (run_date, duration_seconds, articles_scraped,
                 articles_analyzed, articles_published,
                 tokens_used, cost_estimate, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                stats.get("duration_seconds", 0.0),
                stats.get("articles_scraped", 0),
                stats.get("articles_analyzed", 0),
                stats.get("articles_published", 0),
                stats.get("tokens_used", 0),
                stats.get("cost_estimate", 0.0),
                stats.get("status", "success"),
            ),
        )
        conn.commit()
        row_id = cur.lastrowid
        logger.info("Pipeline run logged (id=%d) — status=%s", row_id, stats.get("status", "success"))
        return row_id
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# get_daily_stats
# ---------------------------------------------------------------------------


def get_daily_stats(
    db_path: str | Path,
    days: int = 7,
) -> dict[str, Any]:
    """
    Return aggregated daily statistics for the last *days* days.

    Parameters
    ----------
    db_path : path-like
        Path to the SQLite database.
    days : int, optional
        Number of days to look back (default 7).

    Returns
    -------
    dict
        Dictionary with keys:

        - ``period_days`` (int) — the requested window.
        - ``total_runs`` (int) — number of pipeline runs in the window.
        - ``avg_duration_seconds`` (float) — mean run duration.
        - ``total_articles_scraped`` (int) — sum of scraped articles.
        - ``total_articles_analyzed`` (int) — sum of analyzed articles.
        - ``total_articles_published`` (int) — sum of published articles.
        - ``total_tokens`` (int) — sum of LLM tokens used.
        - ``total_cost_usd`` (float) — sum of estimated costs.
        - ``success_rate`` (float) — fraction of runs with status ``'success'``.
        - ``daily_breakdown`` (list[dict]) — per-day rows with the same metrics.
    """
    conn = _open_db(db_path)
    try:
        # Aggregate totals
        row = conn.execute(
            """
            SELECT
                COUNT(*)                                          AS total_runs,
                COALESCE(AVG(duration_seconds), 0)               AS avg_duration,
                COALESCE(SUM(articles_scraped), 0)                AS total_scraped,
                COALESCE(SUM(articles_analyzed), 0)               AS total_analyzed,
                COALESCE(SUM(articles_published), 0)              AS total_published,
                COALESCE(SUM(tokens_used), 0)                     AS total_tokens,
                COALESCE(SUM(cost_estimate), 0)                   AS total_cost,
                COALESCE(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END), 0) AS successes
            FROM pipeline_runs
            WHERE run_date >= datetime('now', ?)
            """,
            (f"-{days} days",),
        ).fetchone()

        total_runs = row[0] if row else 0
        successes = row[7] if row else 0
        success_rate = (successes / total_runs) if total_runs > 0 else 0.0

        # Daily breakdown
        daily_rows = conn.execute(
            """
            SELECT
                DATE(run_date)                                    AS day,
                COUNT(*)                                          AS runs,
                COALESCE(SUM(articles_scraped), 0)                AS scraped,
                COALESCE(SUM(articles_analyzed), 0)               AS analyzed,
                COALESCE(SUM(articles_published), 0)              AS published,
                COALESCE(SUM(tokens_used), 0)                     AS tokens,
                COALESCE(SUM(cost_estimate), 0)                   AS cost,
                COALESCE(AVG(duration_seconds), 0)                AS avg_duration
            FROM pipeline_runs
            WHERE run_date >= datetime('now', ?)
            GROUP BY DATE(run_date)
            ORDER BY day DESC
            """,
            (f"-{days} days",),
        ).fetchall()

        daily_breakdown: list[dict[str, Any]] = [
            {
                "day": r[0],
                "runs": r[1],
                "articles_scraped": r[2],
                "articles_analyzed": r[3],
                "articles_published": r[4],
                "tokens_used": r[5],
                "cost_usd": round(r[6], 6),
                "avg_duration_seconds": round(r[7], 2),
            }
            for r in daily_rows
        ]

        return {
            "period_days": days,
            "total_runs": total_runs,
            "avg_duration_seconds": round(row[1] or 0, 2),
            "total_articles_scraped": row[2] or 0,
            "total_articles_analyzed": row[3] or 0,
            "total_articles_published": row[4] or 0,
            "total_tokens": row[5] or 0,
            "total_cost_usd": round(row[6] or 0, 6),
            "success_rate": round(success_rate, 2),
            "daily_breakdown": daily_breakdown,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# get_source_recommendations
# ---------------------------------------------------------------------------


def get_source_recommendations(
    db_path: str | Path,
    *,
    keep_threshold: float = 5.0,
    review_threshold: float = 3.0,
) -> list[dict[str, Any]]:
    """
    Analyse per-source quality and return keep / review / remove recommendations.

    The recommendation logic:

    - **keep**  — ``avg_score >= keep_threshold`` (default 5.0).
    - **review** — ``review_threshold <= avg_score < keep_threshold``.
    - **remove** — ``avg_score < review_threshold`` (default 3.0).

    Parameters
    ----------
    db_path : path-like
        Path to the SQLite database.
    keep_threshold : float, optional
        Minimum average score for a ``'keep'`` recommendation (default 5.0).
    review_threshold : float, optional
        Minimum average score for a ``'review'`` recommendation (default 3.0).
        Below this, the recommendation is ``'remove'``.

    Returns
    -------
    list[dict]
        One dict per source with keys:

        - ``source_id`` (int)
        - ``source_name`` (str)
        - ``source_type`` (str)
        - ``total_articles`` (int)
        - ``avg_score`` (float)
        - ``recommendation`` (str) — ``'keep'``, ``'review'``, or ``'remove'``
        - ``reason`` (str) — human-readable explanation.
    """
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                s.id,
                s.name,
                s.type,
                COUNT(a.id)                              AS total_articles,
                COALESCE(AVG(an.overall_score), 0)       AS avg_score
            FROM sources s
            LEFT JOIN articles a  ON a.source_id = s.id
            LEFT JOIN analyses an ON an.article_id = a.id
            WHERE s.active = 1
            GROUP BY s.id
            ORDER BY avg_score DESC
            """,
        ).fetchall()

        recommendations: list[dict[str, Any]] = []
        for r in rows:
            source_id, name, source_type, total, avg = r
            avg = round(avg or 0, 2)

            if avg >= keep_threshold:
                rec = "keep"
                reason = f"Bonne qualité moyenne ({avg}/10) — à conserver."
            elif avg >= review_threshold:
                rec = "review"
                reason = f"Qualité moyenne faible ({avg}/10) — à revoir."
            else:
                rec = "remove"
                reason = f"Qualité moyenne très faible ({avg}/10) — envisager de supprimer."

            recommendations.append({
                "source_id": source_id,
                "source_name": name,
                "source_type": source_type,
                "total_articles": total,
                "avg_score": avg,
                "recommendation": rec,
                "reason": reason,
            })

        logger.info(
            "Source recommendations: %d keep, %d review, %d remove",
            sum(1 for r in recommendations if r["recommendation"] == "keep"),
            sum(1 for r in recommendations if r["recommendation"] == "review"),
            sum(1 for r in recommendations if r["recommendation"] == "remove"),
        )

        return recommendations
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# tune_thresholds
# ---------------------------------------------------------------------------


def tune_thresholds(
    db_path: str | Path,
    target_articles: int = 15,
    *,
    min_bound: float = 2.0,
    max_bound: float = 9.0,
    step: float = 0.5,
) -> dict[str, Any]:
    """
    Suggest a ``min_score`` threshold that would yield approximately
    *target_articles* articles per day.

    The function scans existing analysis scores and finds the threshold
    that gets closest to the target count.

    Parameters
    ----------
    db_path : path-like
        Path to the SQLite database.
    target_articles : int, optional
        Desired number of articles per day (default 15).
    min_bound : float, optional
        Lower bound for the search (default 2.0).
    max_bound : float, optional
        Upper bound for the search (default 9.0).
    step : float, optional
        Granularity of the search (default 0.5).

    Returns
    -------
    dict
        Dictionary with keys:

        - ``current_threshold`` (float) — the threshold currently in use
          (estimated from the median of recent pipeline_runs cost_estimate,
          or 5.0 as fallback).
        - ``suggested_threshold`` (float) — the recommended new threshold.
        - ``expected_articles`` (int) — estimated articles at the suggested threshold.
        - ``target_articles`` (int) — the requested target.
        - ``all_options`` (list[dict]) — every tried threshold with its count.
    """
    conn = _open_db(db_path)
    try:
        # Gather all analysis scores
        rows = conn.execute(
            "SELECT overall_score FROM analyses WHERE overall_score IS NOT NULL"
        ).fetchall()
        scores: list[float] = [r[0] for r in rows]

        if not scores:
            logger.warning("No analysis scores found — cannot tune thresholds.")
            return {
                "current_threshold": 5.0,
                "suggested_threshold": 5.0,
                "expected_articles": 0,
                "target_articles": target_articles,
                "all_options": [],
            }

        # Try every threshold and count how many articles would pass
        all_options: list[dict[str, Any]] = []
        best_threshold = min_bound
        best_diff = abs(len(scores) - target_articles)

        threshold = min_bound
        while threshold <= max_bound:
            count = sum(1 for s in scores if s >= threshold)
            diff = abs(count - target_articles)
            all_options.append({
                "threshold": round(threshold, 1),
                "article_count": count,
                "diff_from_target": diff,
            })
            if diff < best_diff:
                best_diff = diff
                best_threshold = threshold
            threshold = round(threshold + step, 2)

        # Estimate current threshold from recent runs (fallback 5.0)
        current_row = conn.execute(
            "SELECT cost_estimate FROM pipeline_runs ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        current_threshold = 5.0  # sensible default
        if current_row and current_row[0]:
            # Rough heuristic: higher cost ≈ lower threshold (more articles)
            # This is a placeholder; in practice the caller passes min_score explicitly.
            current_threshold = 5.0

        expected = sum(1 for s in scores if s >= best_threshold)

        logger.info(
            "Threshold tuning: current=%.1f → suggested=%.1f (≈%d articles, target=%d)",
            current_threshold,
            best_threshold,
            expected,
            target_articles,
        )

        return {
            "current_threshold": current_threshold,
            "suggested_threshold": round(best_threshold, 1),
            "expected_articles": expected,
            "target_articles": target_articles,
            "all_options": all_options,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# refresh_source_stats
# ---------------------------------------------------------------------------


def refresh_source_stats(db_path: str | Path) -> None:
    """
    Recompute and upsert per-source statistics into ``source_stats``.

    Call this periodically (e.g. after each pipeline run) to keep the
    source quality metrics up to date.

    Parameters
    ----------
    db_path : path-like
        Path to the SQLite database.
    """
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                s.id,
                COUNT(a.id)                        AS total_articles,
                COALESCE(AVG(an.overall_score), 0) AS avg_score,
                MAX(a.fetched_at)                  AS last_success
            FROM sources s
            LEFT JOIN articles a  ON a.source_id = s.id
            LEFT JOIN analyses an ON an.article_id = a.id
            GROUP BY s.id
            """,
        ).fetchall()

        for r in rows:
            conn.execute(
                """
                INSERT INTO source_stats (source_id, total_articles, avg_score, last_success)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    total_articles = excluded.total_articles,
                    avg_score     = excluded.avg_score,
                    last_success  = excluded.last_success
                """,
                (r[0], r[1], round(r[2] or 0, 2), r[3]),
            )
        conn.commit()
        logger.info("Refreshed stats for %d sources.", len(rows))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI (quick diagnostics)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="EDGE Feedback & Auto-Improvement",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--db", default=str(Path("data/edge.db")), help="SQLite DB path")
    sub = parser.add_subparsers(dest="command", help="Command")

    # stats
    p_stats = sub.add_parser("stats", help="Show daily stats")
    p_stats.add_argument("--days", type=int, default=7, help="Lookback window")

    # recommendations
    sub.add_parser("recommendations", help="Show source recommendations")

    # tune
    p_tune = sub.add_parser("tune", help="Tune score threshold")
    p_tune.add_argument("--target", type=int, default=15, help="Target articles/day")

    # refresh
    sub.add_parser("refresh", help="Refresh source_stats table")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    db = Path(args.db)
    if not db.exists():
        logger.error("Database not found: %s", db)
        sys.exit(1)

    ensure_feedback_schema(db)

    if args.command == "stats":
        result = get_daily_stats(db, days=args.days)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))

    elif args.command == "recommendations":
        recs = get_source_recommendations(db)
        for r in recs:
            icon = {"keep": "✅", "review": "⚠️", "remove": "❌"}[r["recommendation"]]
            print(f"  {icon} [{r['recommendation'].upper():6s}] {r['source_name']:30s} "
                  f"avg={r['avg_score']:5.2f}  articles={r['total_articles']:4d}  "
                  f"— {r['reason']}")

    elif args.command == "tune":
        result = tune_thresholds(db, target_articles=args.target)
        print(f"  Current threshold : {result['current_threshold']}")
        print(f"  Suggested threshold: {result['suggested_threshold']}")
        print(f"  Expected articles : {result['expected_articles']} (target={result['target_articles']})")
        print()
        print("  Threshold | Articles")
        print("  " + "-" * 24)
        for opt in result["all_options"]:
            marker = " ← best" if opt["threshold"] == result["suggested_threshold"] else ""
            print(f"  {opt['threshold']:9.1f} | {opt['article_count']:8d}{marker}")

    elif args.command == "refresh":
        refresh_source_stats(db)
        print("Source stats refreshed.")
