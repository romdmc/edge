#!/bin/bash
# EDGE — Pipeline complet (VPS)
# Lance le pipeline complet: scrape → analyze → generate (FR + EN) → deploy

set -euo pipefail

PROJECT_DIR="/root/domoria/projets/edge"
LOG_FILE="${PROJECT_DIR}/data/pipeline.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

cd "$PROJECT_DIR"

echo "[$TIMESTAMP] === EDGE Pipeline Start ===" >> "$LOG_FILE"

# Export OpenRouter credentials
export OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-$(grep -oP 'OPENROUTER_API_KEY=\K.*' .env 2>/dev/null || echo '')}"
export OPENROUTER_MODEL="${OPENROUTER_MODEL:-openrouter/owl-alpha}"

# Installer les deps si besoin
pip install jinja2 pyyaml requests 2>/dev/null

# Créer les schemas si absents
python3 -c "
from feedback import ensure_feedback_schema
from auth import init_auth_db
ensure_feedback_schema('data/edge.db')
init_auth_db('data/edge.db')
print('✓ Feedback + Auth schemas OK')
"

# Lancer le pipeline Python (scrape → analyze → trends → generate FR)
echo "[$TIMESTAMP] === Running Pipeline ===" >> "$LOG_FILE"
python3 pipeline.py \
    config/sources.yaml \
    --min-score 5.0 \
    --model openrouter/owl-alpha \
    --max-articles 30 \
    --site-url "https://romdmc.github.io/edge" \
    --verbose \
    2>&1 | tee -a "$LOG_FILE"

PIPELINE_EXIT=${PIPESTATUS[0]}

# Générer le site EN
echo "[$TIMESTAMP] === Generation EN ===" >> "$LOG_FILE"
python3 -c "
from generator import generate_site
from pathlib import Path
stats = generate_site(Path('data/edge.db'), Path('output/en'), lang='en')
print(f'EN: {stats.pages_generated} pages, {stats.articles_indexed} articles, {stats.errors} errors')
" 2>&1 | tee -a "$LOG_FILE"

TIMESTAMP_END=$(date '+%Y-%m-%d %H:%M:%S')

# Fixer les permissions pour nginx
find output output_en -type f -exec chmod 644 {} \; 2>/dev/null || true
chmod 755 output/static output/static/icons output_en/static output_en/static/icons 2>/dev/null || true

# Synchroniser le site nginx local
cp -r output/* /usr/share/nginx/html/ 2>/dev/null || true
cp templates/login.html /usr/share/nginx/html/ 2>/dev/null || true
cp templates/register.html /usr/share/nginx/html/ 2>/dev/null || true
cp templates/newsletter_subscribe.html /usr/share/nginx/html/ 2>/dev/null || true
cp templates/newsletter_unsubscribe.html /usr/share/nginx/html/ 2>/dev/null || true
find /usr/share/nginx/html -type f -exec chmod 644 {} \; 2>/dev/null || true
find /usr/share/nginx/html -type d -exec chmod 755 {} \; 2>/dev/null || true
echo "✓ Site nginx synchronisé" | tee -a "$LOG_FILE"

# Redémarrer l'API server
bash api_server.sh restart 2>&1 | tee -a "$LOG_FILE"

# Log le run
python3 -c "
from feedback import log_run
log_run('data/edge.db', {
    'duration_seconds': 0,
    'articles_scraped': 0,
    'articles_analyzed': 0,
    'articles_published': 0,
    'tokens_used': 0,
    'cost_estimate': 0,
    'status': 'success' if $PIPELINE_EXIT == 0 else 'failed'
})
print('✓ Run logged')
" 2>&1 | tee -a "$LOG_FILE"

# Déployer sur GitHub Pages
echo "[$TIMESTAMP_END] === Deploy GitHub Pages ===" >> "$LOG_FILE"
bash deploy_ghpages.sh 2>&1 | tee -a "$LOG_FILE"

echo "[$TIMESTAMP_END] === EDGE Pipeline Done ===" >> "$LOG_FILE"

exit $PIPELINE_EXIT
