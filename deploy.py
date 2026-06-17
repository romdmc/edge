#!/usr/bin/env python3
"""Create GitHub repo and push EDGE code."""
import urllib.request
import json
import subprocess
import os
import sys

TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github_token")

def get_token():
    # Try file first
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            token = f.read().strip()
            if token and not token.startswith("ghp_3L..."):
                return token
    # Try env
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    # Try stdin
    if not sys.stdin.isatty():
        token = sys.stdin.read().strip()
        if token:
            return token
    return ""

TOKEN = get_token()
if not TOKEN:
    print("❌ Token non trouvé. Créez .github_token ou exportez GITHUB_TOKEN")
    sys.exit(1)

def github_api(method, path, data=None):
    url = f"https://api.github.com{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"token {TOKEN}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"❌ {method} {path} → {e.code}: {err}")
        sys.exit(1)

# 1. Get authenticated user
user = github_api("GET", "/user")
username = user["login"]
print(f"✅ Authentifié comme: {username}")

# 2. Create repo
repo = github_api("POST", "/user/repos", {
    "name": "edge",
    "description": "EDGE — Veille tech auto-améliorant. Site bilingue FR/EN avec scraping, analyse LLM, API REST, PWA.",
    "private": False,
    "has_issues": True,
    "has_projects": True,
    "auto_init": False,
    "license": "mit"
})
repo_url = repo["html_url"]
print(f"✅ Repo créé: {repo_url}")

# 3. Configure git
subprocess.run(["git", "init"], check=True, cwd="/root/domoria/projets/edge")
subprocess.run(["git", "config", "user.name", "Romain"], check=True, cwd="/root/domoria/projets/edge")
subprocess.run(["git", "config", "user.email", "romain@domoria.fr"], check=True, cwd="/root/domoria/projets/edge")
subprocess.run(["git", "config", "credential.helper", "store"], check=True, cwd="/root/domoria/projets/edge")

# 4. Create .gitignore
gitignore = """# Data (too large for git)
data/
*.db
*.db-wal
*.db-shm

# Generated output
output/
output_en/

# Environment / secrets
.env
.env.*

# Python
__pycache__/
*.pyc
*.pyo
*.egg-info/
.eggs/
dist/
build/
*.egg

# Virtual environments
venv/
.venv/
env/

# IDE
.idea/
.vscode/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Logs
*.log
data/pipeline.log

# Temp
*.tmp
*.temp
tmp/
"""
with open("/root/domoria/projets/edge/.gitignore", "w") as f:
    f.write(gitignore)
print("✅ .gitignore créé")

# 5. Add all files
subprocess.run(["git", "add", "-A"], check=True, cwd="/root/domoria/projets/edge")

# 6. Commit
result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd="/root/domoria/projets/edge")
if result.stdout.strip():
    subprocess.run(["git", "commit", "-m", "Initial commit — EDGE veille tech auto-améliorant"], check=True, cwd="/root/domoria/projets/edge")
    print("✅ Commit créé")
else:
    print("⚠️  Nothing to commit")

# 7. Add remote and push
remote_url = f"https://{username}:{TOKEN}@github.com/{username}/edge.git"
subprocess.run(["git", "remote", "add", "origin", remote_url], check=True, cwd="/root/domoria/projets/edge")
subprocess.run(["git", "branch", "-M", "main"], check=True, cwd="/root/domoria/projets/edge")
subprocess.run(["git", "push", "-u", "origin", "main"], check=True, cwd="/root/domoria/projets/edge")
print(f"✅ Code poussé sur {repo_url}")

# 8. Enable GitHub Pages via API
try:
    github_api("POST", f"/repos/{username}/edge/pages", {
        "source": {
            "branch": "main",
            "path": "/"
        }
    })
    print(f"GitHub Pages activé: https://{username}.github.io/edge/")
except urllib.error.HTTPError as e:
    if e.code == 422:
        print("⚠️  GitHub Pages peut nécessiter une activation manuelle")
        print(f"   Va sur: {repo_url}/settings/pages")
        print(f"   Source: Branch 'main', folder '/'")
    else:
        raise

print(f"""
🎉 Déploiement terminé !

📦 Repo:     {repo_url}
🌐 Pages:    https://{username}.github.io/edge/

Prochaines étapes:
1. Va sur {repo_url}/settings/pages
2. Active GitHub Pages (Source: main, /)
3. Le site sera disponible sur https://{username}.github.io/edge/
""")
