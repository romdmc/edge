#!/usr/bin/env python3
"""Build FTS5 index for EDGE."""
import sqlite3
import sys

db_path = sys.argv[1] if len(sys.argv) > 1 else "data/edge.db"

conn = sqlite3.connect(db_path)
conn.execute(
    "CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5("
    "title, content, summary, tags, content=articles, content_rowid=id)"
)
conn.execute("DELETE FROM articles_fts")

rows = conn.execute(
    "INSERT INTO articles_fts(rowid, title, content, summary, tags) "
    "SELECT a.id, a.title, "
    "COALESCE(a.content,''), COALESCE(an.summary,''), COALESCE(an.topics,'') "
    "FROM articles a "
    "JOIN analyses an ON an.article_id = a.id "
    "WHERE an.overall_score >= 5.0"
).rowcount

conn.commit()
print(f"FTS5 index built: {rows} rows")
conn.close()
