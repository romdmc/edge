#!/usr/bin/env python3
"""
EDGE REST API — stdlib-only HTTP server.

Endpoints:
  GET  /api/health
  GET  /api/articles          (?page=1&per_page=20)
  GET  /api/articles/{id}
  GET  /api/tags
  GET  /api/stats
  POST /api/articles/{id}/vote   body: {"direction": "up"|"down"}
  POST /api/newsletter/subscribe  body: {"email": "user@example.com"}
  GET  /api/newsletter/unsubscribe?token=xxx
  GET  /api/newsletter/subscribers

Port: 8081
DB:   /root/domoria/projets/edge/data/edge.db
"""

import json
import logging
import os
import re
import sqlite3
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from auth import (  # noqa: E402
    init_auth_db,
    create_user,
    authenticate,
    create_session,
    validate_session,
    delete_session,
)

# Newsletter
from newsletter import (
    subscribe_email,
    unsubscribe_token as do_unsubscribe_token,
    get_active_subscriber_count,
    NEWSLETTER_DB_PATH,
)

# Comments
from comments import (
    init_comments_db,
    add_comment,
    get_comments,
    delete_comment,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PORT = 8081
DB_PATH = Path(__file__).resolve().parent / "data" / "edge.db"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("edge-api")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    """Return a new SQLite connection per request (thread-safe for simple use)."""
    if not DB_PATH.exists():
        log.error("Database not found at %s", DB_PATH)
        raise FileNotFoundError(f"Database not found: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_votes_table(conn)
    return conn


def _ensure_votes_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('up', 'down')),
            ip TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (article_id) REFERENCES articles(id)
        )
        """
    )
    conn.commit()


def json_response(handler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def error_response(handler, status: int, message: str) -> None:
    json_response(handler, status, {"error": message})


def read_body(handler) -> dict:
    content_length = int(handler.headers.get("Content-Length", 0))
    if content_length == 0:
        return {}
    raw = handler.rfile.read(content_length)
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def handle_health(handler) -> None:
    json_response(handler, 200, {"status": "ok"})


def handle_articles_list(handler, params: dict) -> None:
    page = max(1, int(params.get("page", [1])[0]))
    per_page = min(100, max(1, int(params.get("per_page", [20])[0])))
    offset = (page - 1) * per_page

    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        rows = conn.execute(
            """
            SELECT
                a.id,
                a.title,
                a.url,
                s.name AS source_name,
                a.author,
                a.published_at,
                COALESCE(an.overall_score, 0) AS overall_score,
                an.topics,
                an.summary
            FROM articles a
            LEFT JOIN analyses an ON an.article_id = a.id
            LEFT JOIN sources s ON s.id = a.source_id
            ORDER BY a.published_at DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        ).fetchall()

        articles = []
        for r in rows:
            articles.append({
                "id": r["id"],
                "title": r["title"],
                "url": r["url"],
                "source_name": r["source_name"],
                "author": r["author"],
                "published_at": r["published_at"],
                "overall_score": r["overall_score"],
                "topics": json.loads(r["topics"]) if r["topics"] else [],
                "summary": r["summary"],
            })

        json_response(handler, 200, {
            "data": articles,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": (total + per_page - 1) // per_page,
            },
        })
    finally:
        conn.close()


def handle_article_detail(handler, article_id: int) -> None:
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT
                a.id, a.title, a.url, a.content, a.raw_content,
                a.author, a.published_at, a.hash, a.fetched_at,
                s.name AS source_name,
                an.id AS analysis_id,
                an.edge_score, an.value_score, an.cost_score,
                an.overall_score, an.topics, an.summary,
                an.key_quotes, an.llm_model, an.tokens_used,
                an.analyzed_at, an.title_fr
            FROM articles a
            LEFT JOIN analyses an ON an.article_id = a.id
            LEFT JOIN sources s ON s.id = a.source_id
            WHERE a.id = ?
            """,
            (article_id,),
        ).fetchone()

        if row is None:
            error_response(handler, 404, f"Article {article_id} not found")
            return

        # Net vote score
        vote_row = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN direction='up' THEN 1 ELSE 0 END), 0) AS upvotes,
                COALESCE(SUM(CASE WHEN direction='down' THEN 1 ELSE 0 END), 0) AS downvotes
            FROM votes WHERE article_id = ?
            """,
            (article_id,),
        ).fetchone()
        net_score = vote_row["upvotes"] - vote_row["downvotes"]

        article = {
            "id": row["id"],
            "title": row["title"],
            "url": row["url"],
            "content": row["content"],
            "raw_content": row["raw_content"],
            "author": row["author"],
            "published_at": row["published_at"],
            "hash": row["hash"],
            "fetched_at": row["fetched_at"],
            "source_name": row["source_name"],
            "analysis": None if row["analysis_id"] is None else {
                "edge_score": row["edge_score"],
                "value_score": row["value_score"],
                "cost_score": row["cost_score"],
                "overall_score": row["overall_score"],
                "topics": json.loads(row["topics"]) if row["topics"] else [],
                "summary": row["summary"],
                "key_quotes": json.loads(row["key_quotes"]) if row["key_quotes"] else [],
                "llm_model": row["llm_model"],
                "tokens_used": row["tokens_used"],
                "analyzed_at": row["analyzed_at"],
                "title_fr": row["title_fr"],
            },
            "votes": {
                "up": vote_row["upvotes"],
                "down": vote_row["downvotes"],
                "net": net_score,
            },
        }
        json_response(handler, 200, article)
    finally:
        conn.close()


def handle_tags(handler) -> None:
    """Extract tags from the JSON `topics` column in analyses."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT topics FROM analyses WHERE topics IS NOT NULL AND topics != '[]'"
        ).fetchall()

        tag_counts: dict[str, int] = {}
        for r in rows:
            try:
                topics = json.loads(r["topics"]) if r["topics"] else []
            except (json.JSONDecodeError, TypeError):
                continue
            for t in topics:
                tag_counts[t] = tag_counts.get(t, 0) + 1

        tags = [{"name": k, "article_count": v} for k, v in
                sorted(tag_counts.items(), key=lambda x: -x[1])]
        json_response(handler, 200, {"data": tags, "total": len(tags)})
    finally:
        conn.close()


def handle_stats(handler) -> None:
    conn = get_db()
    try:
        total_articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        total_analyses = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
        avg_score = conn.execute(
            "SELECT AVG(overall_score) FROM analyses WHERE overall_score IS NOT NULL"
        ).fetchone()[0]

        top_sources = conn.execute(
            """
            SELECT s.name, COUNT(a.id) AS article_count
            FROM sources s
            JOIN articles a ON a.source_id = s.id
            GROUP BY s.id
            ORDER BY article_count DESC
            LIMIT 10
            """
        ).fetchall()

        total_votes = conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0]

        json_response(handler, 200, {
            "total_articles": total_articles,
            "total_analyses": total_analyses,
            "average_score": round(avg_score, 2) if avg_score is not None else None,
            "total_votes": total_votes,
            "top_sources": [
                {"name": r["name"], "article_count": r["article_count"]}
                for r in top_sources
            ],
        })
    finally:
        conn.close()


def handle_vote(handler, article_id: int) -> None:
    try:
        body = read_body(handler)
    except (json.JSONDecodeError, UnicodeDecodeError):
        error_response(handler, 400, "Invalid JSON body")
        return

    direction = body.get("direction")
    if direction not in ("up", "down"):
        error_response(handler, 400, "Field 'direction' must be 'up' or 'down'")
        return

    # Client IP
    ip = handler.headers.get("X-Forwarded-For", handler.client_address[0])

    conn = get_db()
    try:
        # Verify article exists
        row = conn.execute("SELECT id FROM articles WHERE id = ?", (article_id,)).fetchone()
        if row is None:
            error_response(handler, 404, f"Article {article_id} not found")
            return

        conn.execute(
            "INSERT INTO votes (article_id, direction, ip) VALUES (?, ?, ?)",
            (article_id, direction, ip),
        )
        conn.commit()

        vote_row = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN direction='up' THEN 1 ELSE 0 END), 0) AS upvotes,
                COALESCE(SUM(CASE WHEN direction='down' THEN 1 ELSE 0 END), 0) AS downvotes
            FROM votes WHERE article_id = ?
            """,
            (article_id,),
        ).fetchone()
        net = vote_row["upvotes"] - vote_row["downvotes"]

        json_response(handler, 200, {
            "article_id": article_id,
            "direction": direction,
            "up": vote_row["upvotes"],
            "down": vote_row["downvotes"],
            "net_score": net,
        })
    finally:
        conn.close()


def handle_newsletter_subscribe(handler) -> None:
    try:
        body = read_body(handler)
    except (json.JSONDecodeError, UnicodeDecodeError):
        error_response(handler, 400, "Invalid JSON body")
        return

    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        error_response(handler, 400, "Field 'email' is required")
        return

    token = subscribe_email(NEWSLETTER_DB_PATH, email)
    json_response(handler, 200, {"status": "ok", "message": "Subscribed", "unsubscribe_token": token})


def handle_newsletter_unsubscribe(handler, params: dict) -> None:
    token = (params.get("token", [""])[0] or "").strip()
    if not token:
        error_response(handler, 400, "Missing token")
        return

    do_unsubscribe_token(NEWSLETTER_DB_PATH, token)

    # Return HTML success page
    html = (
        "<!DOCTYPE html>"
        "<html lang='fr' data-theme='dark'>"
        "<head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1.0'>"
        "<title>Désabonné — Newsletter EDGE</title>"
        "<style>"
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
        "background:#0d0d0d;color:#e0e0e0;display:flex;align-items:center;justify-content:center;"
        "min-height:100vh;margin:0;padding:1.5rem;text-align:center;}"
        ".card{background:#161616;border:1px solid #2a2a2a;border-radius:6px;padding:2rem;max-width:440px;}"
        "h1{font-size:1.2rem;margin-bottom:0.8rem;}"
        "p{color:#888;font-size:0.95rem;margin-bottom:1.5rem;}"
        "a{color:#ff6600;text-decoration:none;font-weight:600;}"
        "a:hover{text-decoration:underline;}"
        "</style></head><body>"
        "<div class='card'>"
        "<h1>Vous avez été désabonné de la newsletter EDGE.</h1>"
        "<p>Vous ne recevrez plus notre digest hebdomadaire par email.</p>"
        "<a href='/'>Retour à l'accueil</a>"
        "</div></body></html>"
    )
    body = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_newsletter_subscribers(handler) -> None:
    count = get_active_subscriber_count(NEWSLETTER_DB_PATH)
    json_response(handler, 200, {"active_subscribers": count})


# ---------------------------------------------------------------------------
# Comment handlers
# ---------------------------------------------------------------------------

def _get_auth_user(handler) -> dict | None:
    """Extract and validate the Bearer token from the Authorization header."""
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        return validate_session(str(DB_PATH), token)
    return None


def handle_comment_create(handler, article_id: int) -> None:
    try:
        body = read_body(handler)
    except (json.JSONDecodeError, UnicodeDecodeError):
        error_response(handler, 400, "Invalid JSON body")
        return

    content = (body.get("content") or "").strip()
    author_name = (body.get("author_name") or "").strip()

    if not content:
        error_response(handler, 400, "Field 'content' is required")
        return
    if not author_name:
        error_response(handler, 400, "Field 'author_name' is required")
        return

    # Optional auth — if a valid token is provided, use that user
    user = _get_auth_user(handler)
    user_id = user["id"] if user else None
    if user:
        author_name = user.get("display_name") or author_name

    # Verify article exists
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM articles WHERE id = ?", (article_id,)).fetchone()
        if row is None:
            error_response(handler, 404, f"Article {article_id} not found")
            return
    finally:
        conn.close()

    cid = add_comment(str(DB_PATH), article_id, user_id, author_name, content)
    json_response(handler, 201, {
        "id": cid,
        "article_id": article_id,
        "author_name": author_name,
        "content": content,
        "status": "approved",
    })


def handle_comment_list(handler, article_id: int) -> None:
    comments = get_comments(str(DB_PATH), article_id)
    json_response(handler, 200, {"data": comments, "total": len(comments)})


def handle_comment_delete(handler, article_id: int, comment_id: int) -> None:
    user = _get_auth_user(handler)
    if user is None:
        error_response(handler, 401, "Authentication required")
        return

    deleted = delete_comment(str(DB_PATH), comment_id)
    if not deleted:
        error_response(handler, 404, f"Comment {comment_id} not found")
        return

    json_response(handler, 200, {"message": "Comment deleted", "id": comment_id})


# ---------------------------------------------------------------------------
# Auth handlers
# ---------------------------------------------------------------------------

def handle_auth_register(handler) -> None:
    try:
        body = read_body(handler)
    except (json.JSONDecodeError, UnicodeDecodeError):
        error_response(handler, 400, "Invalid JSON body")
        return

    email = (body.get("email") or "").strip().lower()
    password = body.get("password", "")
    display_name = (body.get("display_name") or "").strip()

    if not email or not password or not display_name:
        error_response(handler, 400, "Fields 'email', 'password', and 'display_name' are required")
        return

    if len(password) < 6:
        error_response(handler, 400, "Password must be at least 6 characters")
        return

    try:
        uid = create_user(str(DB_PATH), email, password, display_name)
    except sqlite3.IntegrityError:
        json_response(handler, 409, {"error": "Email already registered"})
        return

    json_response(handler, 201, {"user_id": uid, "email": email, "display_name": display_name})


def handle_auth_login(handler) -> None:
    try:
        body = read_body(handler)
    except (json.JSONDecodeError, UnicodeDecodeError):
        error_response(handler, 400, "Invalid JSON body")
        return

    email = (body.get("email") or "").strip().lower()
    password = body.get("password", "")

    if not email or not password:
        error_response(handler, 400, "Fields 'email' and 'password' are required")
        return

    user = authenticate(str(DB_PATH), email, password)
    if user is None:
        error_response(handler, 401, "Wrong email or password")
        return

    token = create_session(str(DB_PATH), user["id"])
    json_response(handler, 200, {
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "role": user["role"],
        },
    })


def handle_auth_logout(handler) -> None:
    try:
        body = read_body(handler)
    except (json.JSONDecodeError, UnicodeDecodeError):
        error_response(handler, 400, "Invalid JSON body")
        return

    token = body.get("token", "")
    if not token:
        error_response(handler, 400, "Field 'token' is required")
        return

    delete_session(str(DB_PATH), token)
    json_response(handler, 200, {"message": "Logged out"})


def handle_auth_me(handler) -> None:
    token = None
    # Try body first, then query param
    try:
        body = read_body(handler)
        token = body.get("token", "")
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    if not token:
        parsed = urlparse(handler.path)
        params = parse_qs(parsed.query)
        token = params.get("token", [None])[0]
    if not token:
        error_response(handler, 400, "Field 'token' is required")
        return

    user = validate_session(str(DB_PATH), token)
    if user is None:
        error_response(handler, 401, "Invalid or expired session")
        return

    json_response(handler, 200, {
        "id": user["id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "role": user["role"],
    })


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

# Pre-compiled patterns for performance
_RE_HEALTH = re.compile(r"^/api/health/?$")
_RE_ARTICLES = re.compile(r"^/api/articles/?$")
_RE_ARTICLE_DETAIL = re.compile(r"^/api/articles/(\d+)/?$")
_RE_ARTICLE_VOTE = re.compile(r"^/api/articles/(\d+)/vote/?$")
_RE_TAGS = re.compile(r"^/api/tags/?$")
_RE_STATS = re.compile(r"^/api/stats/?$")
_RE_NEWSLETTER_SUBSCRIBE = re.compile(r"^/api/newsletter/subscribe/?$")
_RE_NEWSLETTER_UNSUBSCRIBE = re.compile(r"^/api/newsletter/unsubscribe/?$")
_RE_NEWSLETTER_SUBSCRIBERS = re.compile(r"^/api/newsletter/subscribers/?$")
_RE_AUTH_REGISTER = re.compile(r"^/api/auth/register/?$")
_RE_AUTH_LOGIN = re.compile(r"^/api/auth/login/?$")
_RE_AUTH_LOGOUT = re.compile(r"^/api/auth/logout/?$")
_RE_AUTH_ME = re.compile(r"^/api/auth/me/?$")
_RE_COMMENT_LIST = re.compile(r"^/api/articles/(\d+)/comments/?$")
_RE_COMMENT_DELETE = re.compile(r"^/api/articles/(\d+)/comments/(\d+)/?$")


class EdgeAPIHandler(BaseHTTPRequestHandler):
    """Route requests to the correct handler."""

    def log_message(self, fmt, *args):
        """Override to use our logger instead of stderr."""
        log.info("%s %s", self.address_string(), fmt % args)

    # ---- CORS preflight ----
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    # ---- GET ----
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        try:
            if _RE_HEALTH.match(path):
                handle_health(self)
            elif _RE_ARTICLES.match(path):
                handle_articles_list(self, params)
            elif m := _RE_ARTICLE_DETAIL.match(path):
                handle_article_detail(self, int(m.group(1)))
            elif m := _RE_COMMENT_LIST.match(path):
                handle_comment_list(self, int(m.group(1)))
            elif _RE_TAGS.match(path):
                handle_tags(self)
            elif _RE_STATS.match(path):
                handle_stats(self)
            elif _RE_NEWSLETTER_UNSUBSCRIBE.match(path):
                handle_newsletter_unsubscribe(self, params)
            elif _RE_NEWSLETTER_SUBSCRIBERS.match(path):
                handle_newsletter_subscribers(self)
            elif _RE_AUTH_ME.match(path):
                handle_auth_me(self)
            else:
                error_response(self, 404, "Not found")
        except Exception:
            log.exception("Unhandled error on GET %s", path)
            error_response(self, 500, "Internal server error")

    # ---- POST ----
    def do_POST(self):
        path = (urlparse(self.path).path).rstrip("/") or "/"
        try:
            if m := _RE_ARTICLE_VOTE.match(path):
                handle_vote(self, int(m.group(1)))
            elif m := _RE_COMMENT_LIST.match(path):
                handle_comment_create(self, int(m.group(1)))
            elif _RE_NEWSLETTER_SUBSCRIBE.match(path):
                handle_newsletter_subscribe(self)
            elif _RE_AUTH_REGISTER.match(path):
                handle_auth_register(self)
            elif _RE_AUTH_LOGIN.match(path):
                handle_auth_login(self)
            elif _RE_AUTH_LOGOUT.match(path):
                handle_auth_logout(self)
            else:
                error_response(self, 404, "Not found")
        except Exception:
            log.exception("Unhandled error on POST %s", path)
            error_response(self, 500, "Internal server error")

    # ---- DELETE ----
    def do_DELETE(self):
        path = (urlparse(self.path).path).rstrip("/") or "/"
        try:
            if m := _RE_COMMENT_DELETE.match(path):
                handle_comment_delete(self, int(m.group(1)), int(m.group(2)))
            else:
                error_response(self, 404, "Not found")
        except Exception:
            log.exception("Unhandled error on DELETE %s", path)
            error_response(self, 500, "Internal server error")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("EDGE API starting on port %d", PORT)
    log.info("Database: %s", DB_PATH)

    # Quick DB check
    if not DB_PATH.exists():
        log.error("Database file does not exist: %s", DB_PATH)
        sys.exit(1)

    # Ensure auth tables exist
    init_auth_db(DB_PATH)

    # Ensure comments table exists
    init_comments_db(DB_PATH)

    server = HTTPServer(("0.0.0.0", PORT), EdgeAPIHandler)
    log.info("Listening on http://0.0.0.0:%d", PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down…")
        server.server_close()


if __name__ == "__main__":
    main()
