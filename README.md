# EDGE — Veille tech auto-améliorant

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**EDGE** est un site de veille tech bilingue (FR/EN) qui scrape, analyse et publie automatiquement les meilleures actualités tech, filtrées par intelligence artificielle.

## 🎯 Fonctionnalités

- **Scraping multi-source** : RSS, Reddit, YouTube (34 sources)
- **Analyse LLM** : Chaque article est évalué sur 3 axes (Edge/Value/Cost)
- **Site statique bilingue** : FR + EN généré via Jinja2
- **Recherche full-text** : FTS5 SQLite
- **API REST** : 20 endpoints (articles, votes, auth, newsletter, commentaires)
- **Système de vote** : Upvote/downvote sur chaque article
- **Commentaires** : CRUD avec modération
- **Newsletter** : Inscription/désinscription
- **Authentification** : Register/login avec sessions
- **SEO** : sitemap.xml, robots.txt, RSS feed
- **PWA** : manifest.json + service worker
- **Détection de tendances** : Topics émergents/déclinants
- **Pipeline automatisé** : Cron quotidien (scrape → analyze → generate → deploy)

## 🏗️ Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Scraper   │────▶│  Analyzer   │────▶│  Generator  │
│  (RSS/YT)   │     │   (LLM)     │     │  (Jinja2)   │
└─────────────┘     └─────────────┘     └─────────────┘
       │                   │                    │
       ▼                   ▼                    ▼
   ┌───────────────────────────────────────────────┐
   │              SQLite (edge.db)                 │
   └───────────────────────────────────────────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │   nginx (Docker)      │
              │   + API REST (8081)   │
              └───────────────────────┘
```

## 🚀 Installation

```bash
# Cloner
git clone https://github.com/DOMORIA-SAS/edge.git
cd edge

# Dépendances
pip install jinja2 pyyaml

# Configurer les sources
cp config/sources.yaml.example config/sources.yaml
# Éditer config/sources.yaml avec vos sources

# Lancer le pipeline
bash run.sh

# Ou étape par étape :
python3 pipeline.py config/sources.yaml --min-score 5.0 --model openrouter/owl-alpha --max-articles 30
python3 -c "from generator import generate_site; generate_site('data/edge.db', 'output')"
```

## 📁 Structure

```
edge/
├── run.sh              # Pipeline complet
├── pipeline.py         # Orchestrateur
├── scraper.py          # Scraper RSS/Reddit/YouTube
├── analyzer.py         # Analyseur LLM
├── generator.py        # Générateur site statique
├── api.py              # Serveur API REST
├── auth.py             # Authentification
├── newsletter.py       # Newsletter
├── comments.py         # Commentaires
├── seo.py              # SEO (sitemap, robots, RSS)
├── feedback.py         # Feedback & logging
├── trends.py           # Détection de tendances
├── i18n.py             # Internationalisation
├── config/
│   └── sources.yaml    # Configuration des sources
├── templates/          # Templates Jinja2
├── locales/            # FR/EN translations
└── static/             # Assets PWA
```

## 🔧 Configuration

Créez un fichier `.env` à la racine :

```env
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=openrouter/owl-alpha
```

## 📊 API REST

| Endpoint | Méthode | Description |
|----------|---------|-------------|
| `/api/articles` | GET | Liste paginée |
| `/api/articles/{id}` | GET | Détail article |
| `/api/articles/{id}/vote` | POST | Upvote/downvote |
| `/api/articles/{id}/comments` | GET/POST | Commentaires |
| `/api/tags` | GET | Tags + compteur |
| `/api/stats` | GET | Stats globales |
| `/api/auth/register` | POST | Inscription |
| `/api/auth/login` | POST | Connexion |
| `/api/newsletter/subscribe` | POST | Newsletter |

## 📜 Licence

MIT — Voir [LICENSE](LICENSE)

---

Incubé par **DOMORIA SAS** — SIREN 104 147 467
