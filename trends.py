#!/usr/bin/env python3
"""
EDGE — Trend Detection Module
================================

Analyses articles from the last 7 days to detect emerging topics,
compute trend scores, and generate a trends JSON file + HTML page.

Trend score formula:
    trend_score = (recent_count / max(prev_count, 1)) * avg_score * log(recent_count + 1)

The score combines:
    - Growth ratio:  recent 7-day count / previous 7-day count
    - Quality:       average overall_score of recent articles on the topic
    - Volume:        logarithmic bonus for higher article counts

Usage:
    python3 trends.py
    python3 trends.py --db data/edge.db --output output/trends.json

Output:
    output/trends.json   — structured trend data
    output/trends.html   — rendered trends page (via generator integration)
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("edge.trends")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "edge.db"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"
DEFAULT_TEMPLATES_DIR = PROJECT_ROOT / "templates"
TRENDS_JSON_PATH = DEFAULT_OUTPUT_DIR / "trends.json"
RECENT_DAYS = 7
PREVIOUS_DAYS = 7
TOP_TRENDS_COUNT = 10
MIN_SCORE = 5.0

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TopicStats:
    """Statistics for a single topic across two time periods."""
    topic: str
    slug: str
    recent_count: int = 0
    previous_count: int = 0
    avg_score: float = 0.0
    recent_avg_score: float = 0.0
    growth_ratio: float = 0.0
    trend_score: float = 0.0
    article_ids: list[int] = field(default_factory=list)
    is_emerging: bool = False
    is_declining: bool = False
    direction: str = "→"  # ↑ ↓ →


@dataclass
class TrendReport:
    """Full trend report."""
    generated_at: str = ""
    recent_start: str = ""
    recent_end: str = ""
    previous_start: str = ""
    previous_end: str = ""
    top_trends: list[dict] = field(default_factory=list)
    emerging: list[dict] = field(default_factory=list)
    declining: list[dict] = field(default_factory=list)
    all_topics: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_tag_slug(tag: str) -> str:
    """Convert a tag string to a safe filename slug."""
    import re
    slug = tag.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "untagged"


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _parse_topics(topics_json: str) -> list[str]:
    """Parse topics from a JSON string, handling double-encoding."""
    try:
        topics = json.loads(topics_json)
        if isinstance(topics, str):
            topics = json.loads(topics)
        if not isinstance(topics, list):
            return []
        return [str(t) for t in topics]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------


def fetch_articles_for_period(
    db_path: Path,
    start_date: str,
    end_date: str,
    min_score: float = MIN_SCORE,
) -> list[dict]:
    """Return articles published within [start_date, end_date] inclusive."""
    conn = _open_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                a.id, a.title, a.url,
                COALESCE(s.name, '')       AS source_name,
                COALESCE(a.author, '')     AS author,
                COALESCE(a.published_at, '') AS published_at,
                an.overall_score,
                COALESCE(an.topics, '[]')  AS topics_json,
                COALESCE(an.summary, '')   AS summary
            FROM articles a
            JOIN analyses an ON an.article_id = a.id
            LEFT JOIN sources s ON s.id = a.source_id
            WHERE an.overall_score >= ?
              AND a.published_at >= ?
              AND a.published_at <= ?
            ORDER BY an.overall_score DESC
            """,
            (min_score, start_date, end_date),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def compute_topic_stats(
    recent_articles: list[dict],
    previous_articles: list[dict],
) -> dict[str, TopicStats]:
    """
    Compute per-topic statistics comparing the recent 7-day window
    with the previous 7-day window.
    """
    # --- recent period ---
    recent_topic_articles: dict[str, list[dict]] = defaultdict(list)
    for art in recent_articles:
        for topic in _parse_topics(art.get("topics_json", "[]")):
            recent_topic_articles[topic].append(art)

    # --- previous period ---
    previous_topic_articles: dict[str, list[dict]] = defaultdict(list)
    for art in previous_articles:
        for topic in _parse_topics(art.get("topics_json", "[]")):
            previous_topic_articles[topic].append(art)

    # --- all unique topics ---
    all_topics = set(recent_topic_articles.keys()) | set(previous_topic_articles.keys())

    stats: dict[str, TopicStats] = {}
    for topic in all_topics:
        recent = recent_topic_articles.get(topic, [])
        previous = previous_topic_articles.get(topic, [])

        recent_count = len(recent)
        previous_count = len(previous)

        # Average scores
        recent_avg = (
            sum(a["overall_score"] for a in recent) / recent_count
            if recent_count > 0
            else 0.0
        )
        all_articles = recent + previous
        avg_score = (
            sum(a["overall_score"] for a in all_articles) / len(all_articles)
            if all_articles
            else 0.0
        )

        # Growth ratio (avoid division by zero)
        if previous_count == 0 and recent_count > 0:
            growth_ratio = float(recent_count)  # infinite growth → use count as proxy
        elif previous_count > 0:
            growth_ratio = recent_count / previous_count
        else:
            growth_ratio = 0.0

        # Trend score: growth * avg_score * log(volume + 1)
        volume_bonus = math.log(recent_count + 1) if recent_count > 0 else 0
        trend_score = growth_ratio * avg_score * volume_bonus

        # Direction
        if growth_ratio > 1.2:
            direction = "↑"
        elif growth_ratio < 0.8:
            direction = "↓"
        else:
            direction = "→"

        # Emerging: appeared in recent but not in previous
        is_emerging = previous_count == 0 and recent_count > 0

        # Declining: was in previous but much less in recent
        is_declining = (
            previous_count > 0
            and recent_count > 0
            and growth_ratio < 0.5
        ) or (previous_count > 0 and recent_count == 0)

        stats[topic] = TopicStats(
            topic=topic,
            slug=_safe_tag_slug(topic),
            recent_count=recent_count,
            previous_count=previous_count,
            avg_score=round(avg_score, 2),
            recent_avg_score=round(recent_avg, 2),
            growth_ratio=round(growth_ratio, 3),
            trend_score=round(trend_score, 3),
            article_ids=[a["id"] for a in recent],
            is_emerging=is_emerging,
            is_declining=is_declining,
            direction=direction,
        )

    return stats


def build_trend_report(
    db_path: Path,
    min_score: float = MIN_SCORE,
) -> TrendReport:
    """
    Build the full trend report by comparing the last 7 days
    with the 7 days before that.
    """
    today = date.today()
    recent_end = today.isoformat()
    recent_start = (today - timedelta(days=RECENT_DAYS - 1)).isoformat()
    previous_end = (today - timedelta(days=RECENT_DAYS)).isoformat()
    previous_start = (today - timedelta(days=RECENT_DAYS + PREVIOUS_DAYS - 1)).isoformat()

    logger.info("Analysing trends…")
    logger.info("  Recent : %s → %s", recent_start, recent_end)
    logger.info("  Previous: %s → %s", previous_start, previous_end)

    # Fetch articles for both periods
    # Extend the date range to cover the full day (published_at is ISO datetime)
    recent_articles = fetch_articles_for_period(
        db_path,
        recent_start + "T00:00:00",
        recent_end + "T23:59:59",
        min_score,
    )
    previous_articles = fetch_articles_for_period(
        db_path,
        previous_start + "T00:00:00",
        previous_end + "T23:59:59",
        min_score,
    )

    logger.info("  Recent articles : %d", len(recent_articles))
    logger.info("  Previous articles: %d", len(previous_articles))

    # Compute per-topic stats
    topic_stats = compute_topic_stats(recent_articles, previous_articles)
    logger.info("  Unique topics: %d", len(topic_stats))

    # --- Build report sections ---

    # Top trends: highest trend_score among topics with recent_count > 0
    active_topics = [ts for ts in topic_stats.values() if ts.recent_count > 0]
    active_topics.sort(key=lambda ts: ts.trend_score, reverse=True)
    top_trends = active_topics[:TOP_TRENDS_COUNT]

    # Emerging: topics that appear for the first time
    emerging = [ts for ts in topic_stats.values() if ts.is_emerging]
    emerging.sort(key=lambda ts: ts.trend_score, reverse=True)

    # Declining: topics in decline
    declining = [ts for ts in topic_stats.values() if ts.is_declining]
    declining.sort(key=lambda ts: ts.growth_ratio)

    def _to_dict(ts: TopicStats) -> dict:
        return {
            "topic": ts.topic,
            "slug": ts.slug,
            "recent_count": ts.recent_count,
            "previous_count": ts.previous_count,
            "avg_score": ts.avg_score,
            "recent_avg_score": ts.recent_avg_score,
            "growth_ratio": ts.growth_ratio,
            "trend_score": ts.trend_score,
            "direction": ts.direction,
            "article_ids": ts.article_ids,
        }

    report = TrendReport(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        recent_start=recent_start,
        recent_end=recent_end,
        previous_start=previous_start,
        previous_end=previous_end,
        top_trends=[_to_dict(ts) for ts in top_trends],
        emerging=[_to_dict(ts) for ts in emerging],
        declining=[_to_dict(ts) for ts in declining],
        all_topics=[_to_dict(ts) for ts in active_topics],
    )

    return report


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def write_trends_json(report: TrendReport, output_path: Path = TRENDS_JSON_PATH) -> None:
    """Write the trend report to a JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": report.generated_at,
        "period": {
            "recent": {"start": report.recent_start, "end": report.recent_end},
            "previous": {"start": report.previous_start, "end": report.previous_end},
        },
        "top_trends": report.top_trends,
        "emerging": report.emerging,
        "declining": report.declining,
        "all_topics": report.all_topics,
    }
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("✓ Trends JSON written to %s", output_path)


# ---------------------------------------------------------------------------
# Public API (for generator integration)
# ---------------------------------------------------------------------------


def run_trends(
    db_path: Path = DEFAULT_DB_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    min_score: float = MIN_SCORE,
) -> TrendReport:
    """
    Run the full trend detection pipeline.

    Parameters
    ----------
    db_path : Path
        Path to the SQLite database.
    output_dir : Path
        Directory for the output JSON file.
    min_score : float
        Minimum overall_score for article inclusion.

    Returns
    -------
    TrendReport
    """
    report = build_trend_report(db_path, min_score)
    json_path = output_dir / "trends.json"
    write_trends_json(report, json_path)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="EDGE Trend Detection Module",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to the SQLite database",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory for trends.json",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=MIN_SCORE,
        help="Minimum overall score for article inclusion",
    )
    args = parser.parse_args()

    report = run_trends(
        db_path=args.db,
        output_dir=args.output,
        min_score=args.min_score,
    )

    # Print summary
    print()
    print("=" * 60)
    print("  EDGE — Trend Detection Report")
    print("=" * 60)
    print(f"  Period (recent) : {report.recent_start} → {report.recent_end}")
    print(f"  Period (previous): {report.previous_start} → {report.previous_end}")
    print(f"  Top trends      : {len(report.top_trends)}")
    print(f"  Emerging topics : {len(report.emerging)}")
    print(f"  Declining topics: {len(report.declining)}")
    print()

    if report.top_trends:
        print("  Top 10 Trends:")
        for i, t in enumerate(report.top_trends, 1):
            print(f"    {i:2d}. {t['direction']} {t['topic']:<30s} "
                  f"score={t['trend_score']:.2f}  "
                  f"({t['recent_count']} articles, "
                  f"growth={t['growth_ratio']:.2f}x)")

    if report.emerging:
        print(f"\n  Emerging topics ({len(report.emerging)}):")
        for t in report.emerging[:10]:
            print(f"    ✦ {t['topic']:<30s} ({t['recent_count']} articles)")

    if report.declining:
        print(f"\n  Declining topics ({len(report.declining)}):")
        for t in report.declining[:10]:
            print(f"    ↓ {t['topic']:<30s} (was {t['previous_count']}, now {t['recent_count']})")

    print("=" * 60)
