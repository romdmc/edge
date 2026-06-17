#!/usr/bin/env python3
"""
EDGE — Internationalisation (i18n) helper module
===================================================

Provides translation loading and key-based lookup with optional
Python ``str.format`` interpolation.

Usage::

    from i18n import t, load_translations, set_default_lang

    # Simple lookup
    label = t("nav_home")             # → "Accueil" (default lang = fr)
    label = t("nav_home", lang="en")  # → "Home"

    # With interpolation
    msg = t("tag_count", tag="AI", count=5, plural="s")

Defaults
--------
* Default language: ``fr``
* Supported languages: ``fr``, ``en``
* Locale files: ``locales/fr.json``, ``locales/en.json``

If a key is missing from the locale dict, the key itself is returned
as a fallback (Jinja2 templates will still display *something*).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("edge.i18n")

DEFAULT_LANG: str = "fr"
SUPPORTED_LANGS: list[str] = ["fr", "en"]
LOCALES_DIR: Path = Path(__file__).parent / "locales"

_cache: dict[str, dict[str, str]] = {}
_default_lang: str = DEFAULT_LANG


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def set_default_lang(lang: str) -> None:
    """Override the global default language.

    Parameters
    ----------
    lang : str
        An ISO 639-1 language code.  Must be in ``SUPPORTED_LANGS``.
    """
    global _default_lang
    if lang not in SUPPORTED_LANGS:
        logger.warning(
            "Language '%s' not in supported list %s — falling back to '%s'.",
            lang,
            SUPPORTED_LANGS,
            DEFAULT_LANG,
        )
        lang = DEFAULT_LANG
    _default_lang = lang


def get_default_lang() -> str:
    """Return the current default language code."""
    return _default_lang


def load_translations(lang: str) -> dict[str, str]:
    """Load and cache the translation dictionary for *lang*.

    Parameters
    ----------
    lang : str
        ISO 639-1 language code (e.g. ``"fr"``, ``"en"``).

    Returns
    -------
    dict[str, str]
        Mapping of translation keys to translated strings.
        Returns an empty dict if the locale file is missing or invalid.
    """
    if lang not in _cache:
        path = LOCALES_DIR / f"{lang}.json"
        if path.exists():
            try:
                with open(path, encoding="utf-8") as fh:
                    _cache[lang] = json.load(fh)
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Failed to load locale '%s': %s", lang, exc)
                _cache[lang] = {}
        else:
            logger.warning("Locale file not found: %s", path)
            _cache[lang] = {}
    return _cache[lang]


def t(key: str, lang: str | None = None, **kwargs: object) -> str:
    """Translate *key* into the given language.

    Falls back to the key itself when the translation is missing, so
    templates always render *something*.

    Parameters
    ----------
    key : str
        Translation key (e.g. ``"nav_home"``).
    lang : str, optional
        Target language.  Defaults to the module-level default (``fr``).
    **kwargs
        Optional interpolation variables passed to ``str.format``.

    Returns
    -------
    str
        The translated (and optionally interpolated) string.

    Examples
    --------
    >>> t("nav_home")
    'Accueil'
    >>> t("nav_home", lang="en")
    'Home'
    >>> t("tag_count", tag="AI", count=3, plural="s")
    "3 articles étiquetés « AI »."
    """
    effective_lang = lang or _default_lang
    trans = load_translations(effective_lang)
    text = trans.get(key, key)

    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass  # Return unformatted text on missing keys

    return text


def clear_cache() -> None:
    """Clear the translation cache (useful for tests / hot-reload)."""
    _cache.clear()
