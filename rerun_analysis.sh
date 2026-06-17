#!/bin/bash
# EDGE — Relance complète de l'analyse (tout en français)
# Régénère toutes les analyses avec les nouveaux prompts FR

set -euo pipefail

PROJECT_DIR="/root/domoria/projets/edge"
LOG_FILE="${PROJECT_DIR}/data/analysis_rerun.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

cd "$PROJECT_DIR"

echo "[$TIMESTAMP] === EDGE Analysis Re-run (FR) ===" >> "$LOG_FILE"

# Export OpenRouter credentials
export OPENROUTER_API_KEY="sk-or-...76d6"
export OPENROUTER_MODEL="openrouter/owl-alpha"

# Lancer uniquement l'analyzer (pas le scrape)
python3 -c "
from analyzer import run_analyzer
print('🚀 Lancement de l\'analyse complète...')
stats = run_analyzer('config/sources.yaml', min_score=5.0)
print(f'✅ Analyse terminée:')
print(f'   Articles analysés: {stats.scored}')
print(f'   Summarisés: {stats.summarized}')
print(f'   Stockés: {stats.stored}')
print(f'   Filtrés: {stats.filtered_out}')
print(f'   Erreurs: {stats.errors}')
print(f'   Tokens: {stats.total_tokens}')
print(f'   Durée: {stats.elapsed_seconds:.0f}s')
" 2>&1 | tee -a "$LOG_FILE"

ANALYSIS_EXIT=${PIPESTATUS[0]}
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$TIMESTAMP] === Analyse terminée (exit=$ANALYSIS_EXIT) ===" >> "$LOG_FILE"

exit $ANALYSIS_EXIT
