#!/usr/bin/env python3
"""
EDGE — Pipeline Orchestrator
==============================

Runs the full EDGE pipeline sequentially:

    scrape → analyze → generate

Each step is timed, logged, and its success/failure is tracked.
If the scraper fails completely (zero articles stored), the analyzer
and generator are skipped. If the analyzer finds nothing to analyze,
the generator is still run (it may have data from a previous run).

Usage:
    from pipeline import run_pipeline
    summary = run_pipeline("config/sources.yaml")

    # Or from CLI:
    python pipeline.py config/sources.yaml --min-score 5

Dependencies:
    pyyaml
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("edge.pipeline")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "edge.db"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"
DEFAULT_TEMPLATES_DIR = PROJECT_ROOT / "templates"

# Estimated cost per 1K tokens (USD) — conservative default for Gemini 2.5 Flash
COST_PER_1K_TOKENS = 0.0003

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class StepResult:
    """Result of a single pipeline step."""

    name: str
    success: bool
    elapsed_seconds: float = 0.0
    stats: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class PipelineSummary:
    """Summary of the full pipeline run."""

    success: bool = False
    total_elapsed_seconds: float = 0.0
    steps: list[StepResult] = field(default_factory=list)
    articles_scraped: int = 0
    articles_analyzed: int = 0
    articles_published: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "total_elapsed_seconds": round(self.total_elapsed_seconds, 2),
            "articles_scraped": self.articles_scraped,
            "articles_analyzed": self.articles_analyzed,
            "articles_published": self.articles_published,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "errors": self.errors,
            "steps": [
                {
                    "name": s.name,
                    "success": s.success,
                    "elapsed_seconds": round(s.elapsed_seconds, 2),
                    "stats": s.stats,
                    "error": s.error,
                }
                for s in self.steps
            ],
        }


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _resolve(value: str | Path, base: Path) -> Path:
    """Resolve *value* as a path relative to *base* if not absolute."""
    p = Path(value)
    if p.is_absolute():
        return p
    return (base / p).resolve()


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------


def _run_scraper(
    sources_config: Path,
    db_path: Path,
    yt_api_key: str | None = None,
) -> StepResult:
    """Execute the scraper step."""
    step = StepResult(name="scraper", success=False)
    t0 = time.time()

    try:
        from scraper import run_scraper

        logger.info("▶ SCRAPER — fetching sources from %s", sources_config)
        stats: dict[str, Any] = run_scraper(
            config_path=str(sources_config),
            db_path=str(db_path),
            yt_api_key=yt_api_key,
        )

        step.success = stats.get("errors", 0) == 0
        step.stats = stats

        fetched = stats.get("total_fetched", 0)
        stored = stats.get("total_stored", 0)
        dupes = stats.get("total_dupes", 0)
        errors = stats.get("errors", 0)

        if stored == 0 and dupes == 0:
            logger.warning("Scraper stored 0 new articles (no new content or all errored)")

        logger.info(
            "◀ SCRAPER — %d fetched, %d stored, %d dupes, %d errors (%.1fs)",
            fetched,
            stored,
            dupes,
            errors,
            time.time() - t0,
        )

    except Exception as exc:
        step.error = str(exc)
        logger.error("✗ SCRAPER failed: %s", exc, exc_info=True)

    step.elapsed_seconds = time.time() - t0
    return step


def _run_analyzer(
    sources_config: Path,
    db_path: Path,
    min_score: float = 5.0,
    model: str | None = None,
    batch_size: int = 5,
    max_articles: int | None = None,
) -> StepResult:
    """Execute the analyzer step."""
    step = StepResult(name="analyzer", success=False)
    t0 = time.time()

    try:
        from analyzer import run_analyzer

        logger.info("▶ ANALYZER — scoring & summarizing (min_score=%.1f)", min_score)
        stats = run_analyzer(
            config_path=str(sources_config),
            min_score=min_score,
            db_path=str(db_path),
            model=model,
            batch_size=batch_size,
            max_articles=max_articles,
        )

        step.success = True  # analyzer may have 0 articles and still succeed
        step.stats = stats.as_dict()

        logger.info(
            "◀ ANALYZER — %d scored, %d stored, %d filtered, %d errors, %d tokens (%.1fs)",
            stats.scored,
            stats.stored,
            stats.filtered_out,
            stats.errors,
            stats.total_tokens,
            time.time() - t0,
        )

    except Exception as exc:
        step.error = str(exc)
        logger.error("✗ ANALYZER failed: %s", exc, exc_info=True)

    step.elapsed_seconds = time.time() - t0
    return step


def _run_trends(
    db_path: Path,
    output_dir: Path,
    min_score: float = 5.0,
) -> StepResult:
    """Execute the trends detection step."""
    step = StepResult(name="trends", success=False)
    t0 = time.time()

    try:
        from trends import run_trends

        logger.info("▶ TRENDS — detecting emerging topics")
        report = run_trends(
            db_path=db_path,
            output_dir=output_dir,
            min_score=min_score,
        )

        step.success = True
        step.stats = {
            "top_trends": len(report.top_trends),
            "emerging": len(report.emerging),
            "declining": len(report.declining),
        }

        logger.info(
            "◀ TRENDS — %d top, %d emerging, %d declining (%.1fs)",
            len(report.top_trends),
            len(report.emerging),
            len(report.declining),
            time.time() - t0,
        )

    except Exception as exc:
        step.error = str(exc)
        logger.error("✗ TRENDS failed: %s", exc, exc_info=True)

    step.elapsed_seconds = time.time() - t0
    return step


def _run_generator(
    db_path: Path,
    output_dir: Path,
    templates_dir: Path,
    min_score: float = 5.0,
    site_url: str = "",
) -> StepResult:
    """Execute the generator step."""
    step = StepResult(name="generator", success=False)
    t0 = time.time()

    try:
        from generator import generate_site

        logger.info("▶ GENERATOR — building static site → %s", output_dir)
        stats = generate_site(
            db_path=db_path,
            output_dir=output_dir,
            templates_dir=templates_dir,
            min_score=min_score,
            site_url=site_url,
        )

        step.success = stats.errors == 0
        step.stats = stats.as_dict()

        logger.info(
            "◀ GENERATOR — %d pages generated, %d skipped, %d errors (%.1fs)",
            stats.pages_generated,
            stats.pages_skipped,
            stats.errors,
            time.time() - t0,
        )

    except Exception as exc:
        step.error = str(exc)
        logger.error("✗ GENERATOR failed: %s", exc, exc_info=True)

    step.elapsed_seconds = time.time() - t0
    return step


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def run_pipeline(
    config_path: str = "config/sources.yaml",
    *,
    min_score: float = 5.0,
    yt_api_key: str | None = None,
    model: str | None = None,
    batch_size: int = 5,
    max_articles: int | None = None,
    site_url: str = "",
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    templates_dir: str | Path = DEFAULT_TEMPLATES_DIR,
) -> PipelineSummary:
    """
    Run the full EDGE pipeline: scrape → analyze → generate.

    This is the single entry point that orchestrates the entire
    EDGE content pipeline from source configuration to static
    HTML output.

    Parameters
    ----------
    config_path : str
        Path to the sources YAML configuration file (default: ``config/sources.yaml``).
    min_score : float, optional
        Minimum overall_score for article inclusion (default 5.0).
    yt_api_key : str, optional
        YouTube Data API v3 key. Falls back to ``YT_API_KEY`` env var.
    model : str, optional
        OpenRouter model string. Falls back to ``OPENROUTER_MODEL`` env var.
    batch_size : int, optional
        Articles per LLM batch (default 5).
    site_url : str, optional
        Absolute site URL for canonical links.
    output_dir : path-like, optional
        Output directory for generated HTML (default: ``output/``).
    templates_dir : path-like, optional
        Jinja2 templates directory (default: ``templates/``).

    Returns
    -------
    PipelineSummary
        Complete summary of the run including per-step timing,
        article counts, token usage, and estimated cost.

    Raises
    ------
    FileNotFoundError
        If the config file does not exist.
    """
    t_start = time.time()
    summary = PipelineSummary()

    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║           EDGE — Pipeline Orchestrator                  ║")
    logger.info("╚══════════════════════════════════════════════════════════╝")

    # Resolve all paths relative to the project root (parent of config dir)
    sources_config = Path(config_path)
    if not sources_config.exists():
        logger.error("Config file not found: %s", sources_config)
        summary.errors.append(f"Config file not found: {sources_config}")
        return summary

    project_root = sources_config.parent.parent
    db_path = _resolve("data/edge.db", project_root)
    output_dir_resolved = _resolve(output_dir, project_root)
    templates_dir_resolved = _resolve(templates_dir, project_root)

    logger.info("Config  : %s", sources_config)
    logger.info("DB      : %s", db_path)
    logger.info("Output  : %s", output_dir_resolved)
    logger.info("Min score: %.1f", min_score)

    # ── Step 1: Scraper ─────────────────────────────────────────────────
    scraper_result = _run_scraper(
        sources_config=sources_config,
        db_path=db_path,
        yt_api_key=yt_api_key,
    )
    summary.steps.append(scraper_result)

    if not scraper_result.success:
        summary.errors.append(
            f"Scraper failed: {scraper_result.error or 'partial failure'}"
        )

    scraper_stats = scraper_result.stats
    summary.articles_scraped = scraper_stats.get("total_stored", 0)

    # If scraper completely failed (0 fetched, 0 stored), skip analyzer
    total_fetched = scraper_stats.get("total_fetched", 0)
    total_stored = scraper_stats.get("total_stored", 0)
    skip_analyzer = total_fetched == 0 and total_stored == 0

    if skip_analyzer:
        logger.warning(
            "Scraper produced nothing — skipping analyzer. "
            "Generator will use existing data if available."
        )

    # ── Step 2: Analyzer ────────────────────────────────────────────────
    if not skip_analyzer:
        analyzer_result = _run_analyzer(
            sources_config=sources_config,
            db_path=db_path,
            min_score=min_score,
            model=model,
            batch_size=batch_size,
            max_articles=max_articles,
        )
        summary.steps.append(analyzer_result)

        if not analyzer_result.success:
            summary.errors.append(
                f"Analyzer failed: {analyzer_result.error or 'partial failure'}"
            )

        analyzer_stats = analyzer_result.stats
        summary.articles_analyzed = analyzer_stats.get("stored", 0)
        summary.total_tokens = analyzer_stats.get("total_tokens", 0)
    else:
        logger.info("Skipping analyzer (no new articles to analyze).")

    # ── Step 3: Trends ───────────────────────────────────────────────────
    trends_result = _run_trends(
        db_path=db_path,
        output_dir=output_dir_resolved,
        min_score=min_score,
    )
    summary.steps.append(trends_result)

    if not trends_result.success:
        summary.errors.append(
            f"Trends failed: {trends_result.error or 'partial failure'}"
        )

    # ── Step 4: Generator ───────────────────────────────────────────────
    generator_result = _run_generator(
        db_path=db_path,
        output_dir=output_dir_resolved,
        templates_dir=templates_dir_resolved,
        min_score=min_score,
        site_url=site_url,
    )
    summary.steps.append(generator_result)

    if not generator_result.success:
        summary.errors.append(
            f"Generator failed: {generator_result.error or 'partial failure'}"
        )

    gen_stats = generator_result.stats
    summary.articles_published = gen_stats.get("articles_indexed", 0)

    # ── Final summary ───────────────────────────────────────────────────
    summary.total_elapsed_seconds = time.time() - t_start
    summary.estimated_cost_usd = (summary.total_tokens / 1000) * COST_PER_1K_TOKENS
    summary.success = all(s.success for s in summary.steps) and not summary.errors

    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║                  Pipeline Summary                       ║")
    logger.info("╠══════════════════════════════════════════════════════════╣")
    logger.info("║  Articles scraped  : %-34d ║", summary.articles_scraped)
    logger.info("║  Articles analyzed : %-34d ║", summary.articles_analyzed)
    logger.info("║  Articles published: %-34d ║", summary.articles_published)
    logger.info("║  Total tokens      : %-34d ║", summary.total_tokens)
    logger.info("║  Est. cost         : $%-33s ║", f"{summary.estimated_cost_usd:.6f}")
    logger.info("║  Total time        : %-34s ║", f"{summary.total_elapsed_seconds:.1f}s")
    logger.info("║  Status            : %-34s ║",
                "✓ SUCCESS" if summary.success else "✗ FAILED")
    if summary.errors:
        logger.info("║  Errors            : %-34s ║", str(len(summary.errors)))
        for err in summary.errors:
            logger.info("║    • %-50s ║", err[:50])
    logger.info("╚══════════════════════════════════════════════════════════╝")

    # ── Feedback: ensure schema + log run ─────────────────────────────────
    try:
        from feedback import ensure_feedback_schema, log_run
        ensure_feedback_schema(db_path)
        log_run(
            db_path,
            {
                "duration_seconds": summary.total_elapsed_seconds,
                "articles_scraped": summary.articles_scraped,
                "articles_analyzed": summary.articles_analyzed,
                "articles_published": summary.articles_published,
                "tokens_used": summary.total_tokens,
                "cost_estimate": summary.estimated_cost_usd,
                "status": "success" if summary.success else "failed",
            },
        )
        logger.info("✓ Feedback: run logged")
    except Exception as exc:
        logger.warning("Feedback logging failed (non-fatal): %s", exc)

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="EDGE Pipeline Orchestrator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "config",
        nargs="?",
        default="config/sources.yaml",
        help="Path to sources YAML config",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=5.0,
        help="Minimum overall score for article inclusion",
    )
    parser.add_argument(
        "--yt-api-key",
        default=None,
        help="YouTube Data API v3 key (falls back to YT_API_KEY env var)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="OpenRouter model (falls back to OPENROUTER_MODEL env var)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Articles per LLM batch",
    )
    parser.add_argument(
        "--max-articles",
        type=int,
        default=None,
        help="Max unanalyzed articles to process (default: all)",
    )
    parser.add_argument(
        "--site-url",
        default="",
        help="Site base URL for canonical links",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Output directory for generated HTML",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="DEBUG logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    summary = run_pipeline(
        config_path=args.config,
        min_score=args.min_score,
        yt_api_key=args.yt_api_key,
        model=args.model,
        batch_size=args.batch_size,
        max_articles=args.max_articles,
        site_url=args.site_url,
        output_dir=args.output,
    )

    print(json.dumps(summary.as_dict(), indent=2, ensure_ascii=False))

    if not summary.success:
        sys.exit(1)
