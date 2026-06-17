#!/bin/bash
# EDGE — Fresh Run complet (scrape + analyze + generate FR + EN)
# Avec les nouveaux prompts français

set -euo pipefail

PROJECT_DIR="/root/domoria/projets/edge"
LOG_FILE="${PROJECT_DIR}/data/fresh_run.log"

cd "$PROJECT_DIR"

echo "=== EDGE Fresh Run: $(date) ===" >> "$LOG_FILE"

# Charger .env
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

export OPENROUTER_MODEL="openrouter/owl-alpha"

# Créer le feedback schema si absent (ne pas reset la DB !)
python3 -c "
from feedback import ensure_feedback_schema
ensure_feedback_schema('data/edge.db')
print('✓ Feedback schema OK')
"

echo "🚀 Étape 1/4: Scrape + Analyze..." | tee -a "$LOG_FILE"
python3 pipeline.py \
    config/sources.yaml \
    --min-score 5.0 \
    --model openrouter/owl-alpha \
    --batch-size 5 \
    2>&1 | tee -a "$LOG_FILE"

PIPELINE_EXIT=${PIPELINE_EXIT:-${PIPESTATUS[0]}}
if [ "$PIPELINE_EXIT" -ne 0 ]; then
    echo "❌ Pipeline échoué (exit=$PIPELINE_EXIT)" | tee -a "$LOG_FILE"
    exit 1
fi

echo "🚀 Étape 2/4: Génération FR..." | tee -a "$LOG_FILE"
python3 -c "
from generator import generate_site
stats = generate_site('data/edge.db', 'output', min_score=5.0, lang='fr')
print(f'   FR: {stats.pages_generated} pages, {stats.articles_indexed} articles, {stats.errors} erreurs')
" 2>&1 | tee -a "$LOG_FILE"

echo "🚀 Étape 3/4: Génération EN..." | tee -a "$LOG_FILE"
python3 -c "
from generator import generate_site
stats = generate_site('data/edge.db', 'output_en', min_score=5.0, lang='en', site_url='http://72.60.187.136:8080/en')
print(f'   EN: {stats.pages_generated} pages, {stats.articles_indexed} articles, {stats.errors} erreurs')
" 2>&1 | tee -a "$LOG_FILE"

echo "🚀 Étape 4/4: Synchronisation nginx..." | tee -a "$LOG_FILE"
cp -r output/* /usr/share/nginx/html/ 2>/dev/null || true
cp -r output_en/en/* /usr/share/nginx/html/en/ 2>/dev/null || true

echo "✅ Fresh Run terminé: $(date)" | tee -a "$LOG_FILE"
echo "📊 Vérification:"
curl -s -o /dev/null -w "   digest: %{http_code}\n" http://localhost:8080/digest.html
curl -s -o /dev/null -w "   sources: %{http_code}\n" http://localhost:8080/sources.html
curl -s -o /dev/null -w "   series: %{http_code}\n" http://localhost:8080/series.html
