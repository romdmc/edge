#!/bin/bash
# EDGE — Build for GitHub Pages
# Génère un site 100% statique depuis la DB SQLite
# Conçu pour tourner sur GitHub Actions (pas de deps VPS)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== EDGE GitHub Pages Build ==="

# 1. Installer les deps
pip install jinja2 pyyaml 2>/dev/null

# 2. Vérifier la DB
if [ ! -f "data/edge.db" ]; then
    echo "ERROR: data/edge.db not found. Run the pipeline first."
    exit 1
fi

DB_SIZE=$(python3 -c "
import sqlite3
conn = sqlite3.connect('data/edge.db')
c = conn.cursor()
c.execute('SELECT COUNT(*) FROM articles')
print(c.fetchone()[0])
conn.close()
" 2>/dev/null || echo "0")
echo "✓ DB: $DB_SIZE articles"

# 3. Générer le site statique FR dans _site/
echo "=== Generating FR site ==="
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
echo "=== Generating EN site ==="
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

# 6. .nojekyll pour GitHub Pages (pas de traitement Jekyll)
touch _site/.nojekyll

# 7. Fix permissions
find _site -type f -exec chmod 644 {} \;
find _site -type d -exec chmod 755 {} \;

echo ""
echo "=== Build complete ==="
echo "Files: $(find _site -type f | wc -l)"
echo "Size: $(du -sh _site/ | cut -f1)"
