#!/usr/bin/env python3
"""
EDGE — Comment manager module
==============================

SQLite-backed comment storage for EDGE articles.

Table:
  comments — id, article_id, user_id, author_name, content, created_at, status

Usage:
    from comments import init_comments_db, add_comment, get_comments, get_comment_count, delete_comment
    init_comments_db("data/edge.db")
    cid = add_comment("data/edge.db", 1, None, "Alice", "Great article!")
    comments = get_comments("data/edge.db", 1)
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger("edge.comments")


def init_comments_db(db_path: str | Path) -> None:
    """Create the ``comments`` table if it doesn't exist."""
    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS comments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id  INTEGER NOT NULL,
                user_id     INTEGER,
                author_name TEXT    NOT NULL,
                content     TEXT    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                status      TEXT    NOT NULL DEFAULT 'approved'
                            CHECK(status IN ('approved', 'pending', 'spam')),
                FOREIGN KEY (article_id) REFERENCES articles(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_comments_article ON comments(article_id, status)"
        )
        conn.commit()
        logger.info("Comments table initialised in %s", db_path)
    finally:
        conn.close()


def add_comment(
    db_path: str | Path,
    article_id: int,
    user_id: int | None,
    author_name: str,
    content: str,
) -> int:
    """Insert a new comment and return the comment id."""
    db_path = Path(db_path)
    init_comments_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            """
            INSERT INTO comments (article_id, user_id, author_name, content)
            VALUES (?, ?, ?, ?)
            """,
            (article_id, user_id, author_name, content),
        )
        conn.commit()
        cid = cur.lastrowid
        logger.info("Comment %d added on article %d by %s", cid, article_id, author_name)
        return cid
    finally:
        conn.close()


def get_comments(db_path: str | Path, article_id: int) -> list[dict[str, Any]]:
    """Return approved comments for an article, ordered by created_at ASC."""
    db_path = Path(db_path)
    init_comments_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, article_id, user_id, author_name, content, created_at, status
            FROM comments
            WHERE article_id = ? AND status = 'approved'
            ORDER BY created_at ASC
            """,
            (article_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_comment_count(db_path: str | Path, article_id: int) -> int:
    """Return the count of approved comments for an article."""
    db_path = Path(db_path)
    init_comments_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM comments WHERE article_id = ? AND status = 'approved'",
            (article_id,),
        ).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def delete_comment(db_path: str | Path, comment_id: int) -> bool:
    """Delete a comment by id. Returns True if a row was removed."""
    db_path = Path(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
        conn.commit()
        deleted = cur.rowcount > 0
        if deleted:
            logger.info("Comment %d deleted", comment_id)
        return deleted
    finally:
        conn.close()
