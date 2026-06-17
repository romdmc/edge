#!/usr/bin/env python3
"""
EDGE — Authentication module
==============================

SQLite-backed user authentication with session management.

Tables:
  users    — id, email, password_hash, display_name, created_at, last_login, role
  sessions — id, user_id, token, created_at, expires_at

Usage:
    from auth import init_auth_db, create_user, authenticate, create_session
    init_auth_db("data/edge.db")
    uid = create_user("data/edge.db", "user@example.com", "pass", "User")
    user = authenticate("data/edge.db", "user@example.com", "pass")
    token = create_session("data/edge.db", user["id"])
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("edge.auth")

SESSION_DAYS = 7
TOKEN_BYTES = 24  # 48 hex chars

# Try to import bcrypt; fall back to salted SHA-256
try:
    import bcrypt  # type: ignore[import-untyped]

    def _hash_password(password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def _verify_password(password: str, password_hash: str) -> bool:
        try:
            return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        except Exception:
            return False

    _BCRYPT_AVAILABLE = True

except ImportError:
    _BCRYPT_AVAILABLE = False

    def _hash_password(password: str) -> str:
        salt = secrets.token_hex(16)
        digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
        return f"sha256${salt}${digest}"

    def _verify_password(password: str, password_hash: str) -> bool:
        if not password_hash.startswith("sha256$"):
            return False
        try:
            _, salt, stored = password_hash.split("$", 2)
            digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
            return digest == stored
        except (ValueError, AttributeError):
            return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Hash a password.  Uses bcrypt if available, otherwise salted SHA-256."""
    return _hash_password(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a *password* against a stored *password_hash*."""
    return _verify_password(password, password_hash)


def init_auth_db(db_path: str | Path) -> None:
    """Create the ``users`` and ``sessions`` tables if they don't exist."""
    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT    NOT NULL UNIQUE,
                password_hash TEXT    NOT NULL,
                display_name  TEXT    NOT NULL,
                created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                last_login    TEXT,
                role          TEXT    NOT NULL DEFAULT 'user' CHECK(role IN ('user','admin'))
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id),
                token      TEXT    NOT NULL UNIQUE,
                created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                expires_at TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_token  ON sessions(token);
            CREATE INDEX IF NOT EXISTS idx_sessions_user   ON sessions(user_id);
            """
        )
        conn.commit()
        logger.info("Auth tables initialised in %s", db_path)
    finally:
        conn.close()


def create_user(
    db_path: str | Path,
    email: str,
    password: str,
    display_name: str,
    role: str = "user",
) -> int:
    """Insert a new user and return the ``user_id``.

    Raises
    ------
    sqlite3.IntegrityError
        If the email is already registered.
    """
    db_path = Path(db_path)
    init_auth_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, display_name, role) VALUES (?, ?, ?, ?)",
            (email, hash_password(password), display_name, role),
        )
        conn.commit()
        uid = cur.lastrowid
        logger.info("Created user %d (%s)", uid, email)
        return uid
    finally:
        conn.close()


def authenticate(db_path: str | Path, email: str, password: str) -> dict[str, Any] | None:
    """Return the user row as a dict if credentials are valid, else ``None``."""
    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        # Update last_login
        conn.execute(
            "UPDATE users SET last_login = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
            (row["id"],),
        )
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def create_session(db_path: str | Path, user_id: int) -> str:
    """Create a session for *user_id* and return the token."""
    db_path = Path(db_path)
    token = secrets.token_hex(TOKEN_BYTES)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO sessions (user_id, token, expires_at) VALUES (?, ?, ?)",
            (user_id, token, expires_at.isoformat()),
        )
        conn.commit()
        logger.info("Session created for user %d", user_id)
        return token
    finally:
        conn.close()


def validate_session(db_path: str | Path, token: str) -> dict[str, Any] | None:
    """Return the user row for a valid, non-expired session, else ``None``."""
    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT u.*
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
              AND s.expires_at > strftime('%Y-%m-%dT%H:%M:%SZ','now')
            """,
            (token,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        conn.close()


def delete_session(db_path: str | Path, token: str) -> bool:
    """Delete a session (logout).  Returns ``True`` if a row was removed."""
    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        deleted = cur.rowcount > 0
        if deleted:
            logger.info("Session deleted")
        return deleted
    finally:
        conn.close()
