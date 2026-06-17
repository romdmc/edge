#!/usr/bin/env python3
"""
EDGE — SEO Module
==================

Generates SEO artefacts for the static site:
  - sitemap.xml   (all article, tag, date, and static pages)
  - robots.txt    (allow-all with sitemap pointer)
  - feed.xml      (RSS 2.0 feed of the last 20 articles)

Usage:
    from seo import generate_sitemap, generate_robots, generate_feed
    generate_sitemap("data/edge.db", "output", "https://edge.domoria.com")
    generate_robots("output", "https://edge.domoria.com")
    generate_feed("data/edge.db", "output", "fr")

Note: Imports from ``generator`` are done *inside* functions to avoid a
circular-dependency with ``generator`` → ``seo`` → ``generator``.
"""

from __future__ import annotations

import html as html_module
import logging
import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

logger = logging.getLogger("edge.seo")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SITEMAP_XML_HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n'

_STATIC_PAGES = [
    ("/archives.html", "weekly", "0.6"),
    ("/sources.html", "weekly", "0.5"),
    ("/series.html", "weekly", "0.5"),
    ("/trends.html", "weekly", "0.5"),
    ("/manifeste.html", "monthly", "0.3"),
    ("/manifesto.html", "monthly", "0.3"),
    ("/digest.html", "weekly", "0.6"),
    ("/newsletter.html", "weekly", "0.4"),
]


# ---------------------------------------------------------------------------
# Sitemap
# ---------------------------------------------------------------------------


def generate_sitemap(
    db_path: str | Path,
    output_dir: str | Path,
    site_url: str,
) -> Path:
    """
    Generate ``sitemap.xml`` in *output_dir*.

    Includes:
      - Home page (``/``)
      - All article pages (``/articles/{id}.html``)
      - All tag pages (``/tags/{slug}.html``)
      - All date pages (``/YYYY-MM-DD.html``)
      - Static pages (archives, sources, series, trends, manifesto, digest)

    Parameters
    ----------
    db_path : str | Path
        Path to the SQLite database.
    output_dir : str | Path
        Directory where ``sitemap.xml`` will be written.
    site_url : str
        Absolute base URL of the site (e.g. ``"https://edge.domoria.com"``).

    Returns
    -------
    Path
        Path to the generated ``sitemap.xml``.
    """
    # Lazy import to break circular dependency: generator → seo → generator
    from generator import (
        fetch_available_dates,
        fetch_published_articles,
        fetch_tag_map,
    )

    db_path = Path(db_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    base = site_url.rstrip("/")
    root = Element("urlset")
    root.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")

    def _add_url(
        loc: str,
        lastmod: str | None = None,
        changefreq: str = "daily",
        priority: str = "0.5",
    ) -> None:
        url_el = SubElement(root, "url")
        SubElement(url_el, "loc").text = f"{base}{loc}"
        if lastmod:
            SubElement(url_el, "lastmod").text = lastmod
        SubElement(url_el, "changefreq").text = changefreq
        SubElement(url_el, "priority").text = priority

    # Home page
    _add_url("/", changefreq="daily", priority="1.0")

    # Static pages
    for page, freq, prio in _STATIC_PAGES:
        _add_url(page, changefreq=freq, priority=prio)

    # Article pages
    articles = fetch_published_articles(db_path)
    for art in articles:
        lastmod = None
        if art.published_at:
            try:
                dt = datetime.fromisoformat(art.published_at.replace("Z", "+00:00"))
                lastmod = dt.strftime("%Y-%m-%d")
            except (ValueError, AttributeError):
                lastmod = art.published_at[:10] if len(art.published_at) >= 10 else None
        _add_url(
            f"/articles/{art.id}.html",
            lastmod=lastmod,
            changefreq="monthly",
            priority="0.8",
        )

    # Tag pages
    tag_map = fetch_tag_map(db_path)
    for slug in tag_map:
        _add_url(f"/tags/{slug}.html", changefreq="daily", priority="0.6")

    # Date pages
    available_dates = fetch_available_dates(db_path)
    for date_str in available_dates:
        _add_url(f"/{date_str}.html", lastmod=date_str, changefreq="daily", priority="0.7")

    # Build pretty-printed XML
    xml_bytes = tostring(root, encoding="unicode")
    import xml.dom.minidom

    dom = xml.dom.minidom.parseString(xml_bytes)
    pretty = dom.toprettyxml(indent="  ", encoding=None)
    lines = pretty.splitlines()
    if lines and lines[0].startswith("<?xml"):
        lines = lines[1:]
    content = SITEMAP_XML_HEADER + "\n".join(lines) + "\n"

    sitemap_path = output_dir / "sitemap.xml"
    sitemap_path.write_text(content, encoding="utf-8")
    url_count = len(articles) + len(tag_map) + len(available_dates) + len(_STATIC_PAGES) + 1
    logger.info("sitemap.xml generated → %s (%d URLs)", sitemap_path, url_count)
    return sitemap_path


# ---------------------------------------------------------------------------
# Robots.txt
# ---------------------------------------------------------------------------


def generate_robots(
    output_dir: str | Path,
    site_url: str,
) -> Path:
    """
    Generate ``robots.txt`` in *output_dir*.

    Allows all crawlers and points to ``sitemap.xml``.

    Parameters
    ----------
    output_dir : str | Path
        Directory where ``robots.txt`` will be written.
    site_url : str
        Absolute base URL of the site.

    Returns
    -------
    Path
        Path to the generated ``robots.txt``.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    base = site_url.rstrip("/")
    content = textwrap.dedent(f"""\
        # EDGE robots.txt
        # https://www.robotstxt.org/robotstxt.html

        User-agent: *
        Allow: /

        Sitemap: {base}/sitemap.xml
    """)

    robots_path = output_dir / "robots.txt"
    robots_path.write_text(content, encoding="utf-8")
    logger.info("robots.txt generated → %s", robots_path)
    return robots_path


# ---------------------------------------------------------------------------
# RSS Feed
# ---------------------------------------------------------------------------


def generate_feed(
    db_path: str | Path,
    output_dir: str | Path,
    lang: str = "fr",
) -> Path:
    """
    Generate ``feed.xml`` (RSS 2.0) in *output_dir*.

    Contains the last 20 published articles, ordered by published_at DESC.

    Parameters
    ----------
    db_path : str | Path
        Path to the SQLite database.
    output_dir : str | Path
        Directory where ``feed.xml`` will be written.
    lang : str
        Language code for the feed (``"fr"`` or ``"en"``).

    Returns
    -------
    Path
        Path to the generated ``feed.xml``.
    """
    # Lazy import to break circular dependency
    from generator import fetch_published_articles

    db_path = Path(db_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    articles = fetch_published_articles(db_path)[:20]

    rss = Element("rss")
    rss.set("version", "2.0")
    rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")
    rss.set("xmlns:content", "http://purl.org/rss/1.0/modules/content/")

    channel = SubElement(rss, "channel")

    description = (
        "Veille tech auto-améliorant. Digest quotidien des meilleures actualités tech, filtré par IA."
        if lang == "fr"
        else "Self-improving tech news. Daily digest of the best tech news, filtered by AI."
    )

    SubElement(channel, "title").text = "EDGE"
    SubElement(channel, "description").text = description
    SubElement(channel, "link").text = "/"
    SubElement(channel, "language").text = lang
    SubElement(channel, "lastBuildDate").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    SubElement(channel, "generator").text = "EDGE Static Site Generator"

    # atom:link self-reference
    atom_link = SubElement(channel, "{http://www.w3.org/2005/Atom}link")
    atom_link.set("href", "/feed.xml")
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    for art in articles:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = art.title
        SubElement(item, "link").text = f"/articles/{art.id}.html"
        guid = SubElement(item, "guid")
        guid.text = f"/articles/{art.id}.html"
        guid.set("isPermaLink", "true")

        # Description: summary or truncated raw content
        desc = ""
        if art.summary:
            desc = art.summary
        elif art.raw_content:
            text = re.sub(r"<[^>]+>", " ", art.raw_content)
            text = html_module.unescape(text)
            text = re.sub(r"\s+", " ", text).strip()
            desc = text[:300]
        SubElement(item, "description").text = desc

        # Publication date
        if art.published_at:
            try:
                dt = datetime.fromisoformat(art.published_at.replace("Z", "+00:00"))
                pub_date = dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
            except (ValueError, AttributeError):
                pub_date = art.published_at
        else:
            pub_date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        SubElement(item, "pubDate").text = pub_date

        # Source
        if art.source_name:
            SubElement(item, "source").text = art.source_name

    xml_bytes = tostring(rss, encoding="unicode")
    import xml.dom.minidom

    dom = xml.dom.minidom.parseString(xml_bytes)
    pretty = dom.toprettyxml(indent="  ", encoding="UTF-8")
    if isinstance(pretty, bytes):
        content = pretty.decode("utf-8")
    else:
        content = pretty

    feed_path = output_dir / "feed.xml"
    feed_path.write_text(content, encoding="utf-8")
    logger.info("feed.xml generated → %s (%d articles)", feed_path, len(articles))
    return feed_path
