#!/bin/bash
# EDGE — Deploy to GitHub Pages
# Build le site statique depuis la DB locale et pousse sur la branche gh-pages
# À exécuter sur le VPS (après le pipeline principal)

set -euo pipefail

PROJECT_DIR="/root/domoria/projets/edge"
GH_PAGES_DIR="/tmp/edge-ghpages-deploy"
GITHUB_TOKEN=$(cat "$PROJECT_DIR/.github_token" 2>/dev/null || echo "")
GITHUB_USER="romdmc"
REPO="edge"

if [ -z "$GITHUB_TOKEN" ]; then
    echo "ERROR: .github_token not found"
    exit 1
fi

cd "$PROJECT_DIR"

echo "=== EDGE → GitHub Pages Deploy ==="

# 1. S'assurer que la DB existe
if [ ! -f "data/edge.db" ]; then
    echo "ERROR: data/edge.db not found. Run the pipeline first."
    exit 1
fi

ARTICLES=$(python3 -c "
import sqlite3
conn = sqlite3.connect('data/edge.db')
c = conn.cursor()
c.execute('SELECT COUNT(*) FROM articles')
print(c.fetchone()[0])
conn.close()
")
echo "✓ DB: $ARTICLES articles"

# 2. Installer les deps si besoin
pip install jinja2 pyyaml 2>/dev/null

# 3. Générer le site statique FR dans _site/
echo "=== Building FR site ==="
rm -rf _site
mkdir -p _site

export EDGE_API_BASE="http://72.60.187.136:8081"

python3 -c "
from generator import generate_site
from pathlib import Path
stats = generate_site(Path('data/edge.db'), Path('_site'), lang='fr')
print(f'FR: {stats.pages_generated} pages, {stats.articles_indexed} articles, {stats.errors} errors')
"

# 4. Générer le site statique EN dans _site/en/
echo "=== Building EN site ==="
python3 -c "
from generator import generate_site
from pathlib import Path
stats = generate_site(Path('data/edge.db'), Path('_site/en'), lang='en')
print(f'EN: {stats.pages_generated} pages, {stats.articles_indexed} articles, {stats.errors} errors')
"

# 5. Copier les assets statics
echo "=== Copying static assets ==="
cp -r static/* _site/static/ 2>/dev/null || true
mkdir -p _site/en/static
cp -r static/* _site/en/static/ 2>/dev/null || true

# 6. .nojekyll pour GitHub Pages
touch _site/.nojekyll

# 7. Fix permissions
find _site -type f -exec chmod 644 {} \;
find _site -type d -exec chmod 755 {} \;

echo "=== Build complete ==="
echo "Files: $(find _site -type f | wc -l)"
echo "Size: $(du -sh _site/ | cut -f1)"

# 8. Cloner le repo gh-pages dans un dossier temporaire
echo "=== Pushing to gh-pages branch ==="
rm -rf "$GH_PAGES_DIR"

GIT_URL="https://${GITHUB_USER}:${GITHUB_TOKEN}@github.com/${GITHUB_USER}/${REPO}.git"

git clone --single-branch --branch gh-pages "$GIT_URL" "$GH_PAGES_DIR" 2>/dev/null || {
    echo "  gh-pages branch doesn't exist yet — creating it..."
    # Créer la branche gh-pages depuis main
    git checkout -b gh-pages 2>/dev/null || git checkout gh-pages 2>/dev/null || {
        # Créer une branche orpheline
        git checkout --orphan gh-pages
        git rm -rf . 2>/dev/null || true
        git commit --allow-empty -m "Initialize gh-pages branch"
        git push "$GIT_URL" gh-pages
    }
    git checkout main 2>/dev/null || true
    git clone --single-branch --branch gh-pages "$GIT_URL" "$GH_PAGES_DIR"
}

# 9. Supprimer les anciens fichiers (sauf .git)
cd "$GH_PAGES_DIR"
find . -not -path './.git/*' -not -name '.git' -not -name '.' -delete 2>/dev/null || true

# 10. Copier les fichiers générés (y compris dotfiles comme .nojekyll)
cp -r "$PROJECT_DIR"/_site/. .

# 11. Commit et push
git config user.email "edge@domoria.fr"
git config user.name "EDGE Bot"

git add -A
if git diff --cached --quiet; then
    echo "✓ No changes — gh-pages is up to date"
else
    git commit -m "Deploy EDGE to GitHub Pages — $(date '+%Y-%m-%d %H:%M UTC')"
    git push "$GIT_URL" gh-pages
    echo "✓ Deployed to GitHub Pages!"
fi

# Cleanup
rm -rf "$GH_PAGES_DIR"

echo ""
echo "=== Done ==="
echo "Site: https://romdmc.github.io/edge/"
