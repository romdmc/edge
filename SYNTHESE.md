# EDGE — Synthèse du projet

> **Statut** : ✅ Terminé (Phases 1-6) — En production
> **URL** : http://72.60.187.136:8080
> **GitHub Pages** : https://romdmc.github.io/edge/ ✅ LIVE
> **Repo GitHub** : https://github.com/romdmc/edge

---

## 📋 Fiche projet

| Champ | Valeur |
|---|---|
| **Nom** | EDGE (Enhanced Daily GEnerator) |
| **Type** | Blog/actu tech auto-améliorant |
| **Stack** | Python + SQLite + Jinja2 + nginx Docker + OpenRouter LLM |
| **Langues** | FR/EN (bilingue) |
| **SIREN** | 104 147 467 (DOMORIA SAS) |
| **Incubateur** | DOMORIA SAS |
| **Phase actuelle** | 5/5 terminée |

---

## 🎯 Concept

EDGE est un site de veille tech qui **scrape**, **analyse** et **publie** automatiquement les meilleures actualités tech, filtrées par intelligence artificielle. Chaque article est évalué sur 3 axes par un LLM :

- **📡 Edge** — Pertinence edge computing / infrastructure
- **💎 Value** — Création de valeur économique
- **🔧 Cost** — Économie de coûts / frugalité

---

## 🏗️ Architecture

```
Sources (RSS/Reddit/YouTube)
        │
        ▼
   ┌──────────┐
   │ Scraper  │ ← 34 sources actives
   └────┬─────┘
        │
        ▼
   ┌──────────┐
   │ Analyzer │ ← LLM OpenRouter (3 axes)
   └────┬─────┘
        │
        ▼
   ┌──────────┐     ┌──────────┐
   │Generator │────▶│  nginx   │ (port 8080)
   │(Jinja2)  │     │ (Docker) │
   └────┬─────┘     └──────────┘
        │
        ▼
   ┌──────────┐
   │   API    │ (port 8081)
   │  REST    │
   └──────────┘
```

---

## ✅ Phases complétées

### Phase 1 — Fondation
- [x] Scraper RSS/Reddit/YouTube (34 sources)
- [x] Analyseur LLM (3 axes: edge/value/cost)
- [x] Générateur site statique FR/EN
- [x] Recherche full-text (FTS5 SQLite)
- [x] Pagination, tags, archives, sources, séries

### Phase 2 — Enrichissement
- [x] Digest hebdomadaire
- [x] Newsletter HTML
- [x] Pages sources détaillées
- [x] Séries thématiques
- [x] Manifeste EDGE

### Phase 3 — Interactivité
- [x] Upvote/downvote sur articles
- [x] API REST (20 endpoints)
- [x] Détection de tendances
- [x] Page tendances

### Phase 4 — Engagement
- [x] PWA (manifest.json + service worker)
- [x] Newsletter (inscription/désinscription)
- [x] Authentification (register/login/sessions)
- [x] Pages login/register

### Phase 5 — Commentaires + SEO
- [x] Système de commentaires (CRUD + API)
- [x] SEO: sitemap.xml (86 URLs)
- [x] SEO: robots.txt
- [x] SEO: RSS feed.xml (20 articles)
- [x] Meta descriptions + Open Graph
- [x] Fix nginx 403 (permissions)

---

## 📊 Métriques de production

| Métrique | Valeur |
|---|---|
| Articles en base | 653 |
| Analyses LLM | 20 |
| Pages générées | 101 |
| Sources actives | 34 |
| Endpoints API | 20 |
| Uptime | ✅ 200 OK |

---

## 🔧 Fichiers clés

| Fichier | Description |
|---|---|
| `run.sh` | Pipeline complet (scrape→analyze→generate→deploy) |
| `pipeline.py` | Orchestrateur Python |
| `scraper.py` | Scraper RSS/Reddit/YouTube |
| `analyzer.py` | Analyseur LLM |
| `generator.py` | Générateur site statique |
| `api.py` | Serveur API REST (port 8081) |
| `auth.py` | Authentification |
| `newsletter.py` | Newsletter |
| `comments.py` | Commentaires |
| `seo.py` | SEO (sitemap, robots, RSS) |
| `trends.py` | Détection de tendances |
| `i18n.py` | Internationalisation |
| `config/sources.yaml` | Configuration des sources |
| `templates/` | 18 templates Jinja2 |
| `locales/` | FR/EN traductions |
| `static/` | Assets PWA |

---

## 🔑 API REST (port 8081)

| Endpoint | Méthode | Description |
|---|---|---|
| `/api/health` | GET | Status |
| `/api/articles` | GET | Liste paginée |
| `/api/articles/{id}` | GET | Détail article |
| `/api/articles/{id}/vote` | POST | Upvote/downvote |
| `/api/articles/{id}/comments` | GET/POST | Commentaires |
| `/api/tags` | GET | Tags + compteur |
| `/api/stats` | GET | Stats globales |
| `/api/auth/register` | POST | Inscription |
| `/api/auth/login` | POST | Connexion |
| `/api/auth/logout` | POST | Déconnexion |
| `/api/auth/me` | GET | Utilisateur courant |
| `/api/newsletter/subscribe` | POST | Newsletter |
| `/api/newsletter/unsubscribe` | GET | Désabonnement |

---

## ⚠️ Problèmes connus

| Problème | Impact | Contournement |
|---|---|---|
| PWA (manifest.json, sw.js) en 403 | PWA non fonctionnel sur mobile | `docker restart edge-edge-1` |
| Reddit scraper bloqué (403) | 8 sources Reddit inactives | OAuth Reddit à implémenter |
| FTS5 colonne `summary` manquante | Recherche full-text partielle | Migration DB à faire |

---

## 🔜 Prochaines étapes (Phase 6)

- [ ] Envoi email SMTP (newsletter réelle)
- [ ] Admin dashboard (gestion utilisateurs/commentaires)
- [ ] Fix PWA (Docker overlay)
- [ ] Tests end-to-end
- [ ] Performance (cache API, gzip, HTTP/2)
- [ ] OAuth Reddit pour les sources bloquées

---

## 📜 Licence

MIT — Incubé par DOMORIA SAS — SIREN 104 147 467
31 Voie Romaine, 31410 Saint-Hilaire, Occitanie, France
