#!/usr/bin/env python3
"""
EDGE Newsletter Manager — SQLite-backed subscription management.
"""

import os
import secrets
import sqlite3
from pathlib import Path

NEWSLETTER_DB_PATH = Path(__file__).resolve().parent / "data" / "newsletter.db"


def generate_unsubscribe_token() -> str:
    """Generate a random 32-character hex token."""
    return secrets.token_hex(16)


def init_newsletter_db(db_path) -> None:
    """
    Create the subscribers table if it doesn't exist.

    Columns:
        id               — INTEGER PRIMARY KEY AUTOINCREMENT
        email            — TEXT NOT NULL UNIQUE
        subscribed_at    — TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        status           — TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'unsubscribed'))
        unsubscribe_token — TEXT UNIQUE
    """
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active', 'unsubscribed')),
                unsubscribe_token TEXT UNIQUE
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def subscribe_email(db_path, email: str) -> str:
    """
    Add a new subscriber or reactivate an existing one.

    Returns the unsubscribe_token.
    If the email is already active, returns the existing token.
    If previously unsubscribed, reactivates and returns the existing token.
    """
    init_newsletter_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, email, status, unsubscribe_token FROM subscribers WHERE email = ?",
            (email,),
        ).fetchone()

        if row is not None:
            # Already subscribed — return existing token
            if row["status"] == "active":
                return row["unsubscribe_token"]
            # Unsubscribed — reactivate
            conn.execute(
                "UPDATE subscribers SET status = 'active', subscribed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row["id"],),
            )
            conn.commit()
            return row["unsubscribe_token"]

        # New subscriber
        token = generate_unsubscribe_token()
        conn.execute(
            "INSERT INTO subscribers (email, unsubscribe_token) VALUES (?, ?)",
            (email, token),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def unsubscribe_token(db_path, token: str) -> bool:
    """
    Set the subscriber with the given token to 'unsubscribed'.
    True if a row was updated, False if token not found.
    """
    init_newsletter_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "UPDATE subscribers SET status = 'unsubscribed' WHERE unsubscribe_token = ? AND status = 'active'",
            (token,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_active_subscribers(db_path) -> list[str]:
    """Return a list of active subscriber emails."""
    init_newsletter_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT email FROM subscribers WHERE status = 'active' ORDER BY subscribed_at DESC"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def get_active_subscriber_count(db_path) -> int:
    """Return the count of active subscribers."""
    init_newsletter_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM subscribers WHERE status = 'active'"
        ).fetchone()
        return row[0]
    finally:
        conn.close()
