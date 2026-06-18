# EDGE — Kanban

## ✅ Phase 1 — Fondation (TERMINÉE)
- [x] Stack Python + SQLite + Jinja2 + nginx Docker
- [x] Scraper RSS/Reddit/YouTube
- [x] Analyseur LLM (3 axes: edge/value/cost)
- [x] Générateur site statique FR/EN
- [x] Recherche full-text (FTS5)
- [x] Pagination + tags + archives
- [x] Sources + séries + manifeste

## ✅ Phase 2 — Enrichissement (TERMINÉE)
- [x] Digest hebdomadaire
- [x] Newsletter HTML
- [x] Sources détaillées
- [x] Séries thématiques
- [x] Manifeste EDGE

## ✅ Phase 3 — Interactivité (TERMINÉE)
- [x] Upvote/downvote sur articles
- [x] API REST (articles, tags, stats, vote)
- [x] Détection de tendances (topics émergents/déclinants)
- [x] Page tendances avec barres de progression

## ✅ Phase 4 — Engagement (TERMINÉE)
- [x] PWA (manifest.json + service worker)
- [x] Newsletter (inscription/désinscription)
- [x] Authentification (register/login/logout/sessions)
- [x] Pages login/register

## ✅ Phase 5 — Commentaires + SEO (TERMINÉE)
- [x] Système de commentaires (CRUD + API)
- [x] SEO: sitemap.xml (110 URLs)
- [x] SEO: robots.txt
- [x] SEO: RSS feed.xml (20 articles)
- [x] Meta descriptions + Open Graph
- [x] Fix nginx 403 (permissions 0600 → 0644)

## ✅ Phase 6 — GitHub Pages Deploy (TERMINÉE)
- [x] Script `build_ghpages.sh` (build statique pur, sans deps VPS)
- [x] Script `deploy_ghpages.sh` (push branche gh-pages)
- [x] Configuration GitHub Pages source → branche `gh-pages`
- [x] `.nojekyll` pour désactiver Jekyll
- [x] Intégration dans `run.sh` (auto-deploy après pipeline)
- [x] Site en ligne : https://romdmc.github.io/edge/
- [x] FR (racine) + EN (/en/) bilingue
- [x] 281 fichiers déployés, 27 articles, 72 tags

## 🔜 Phase 7 — À venir
- [ ] Envoi email SMTP (newsletter réelle)
- [ ] Admin dashboard (gestion utilisateurs/commentaires)
- [ ] Fix PWA (manifest.json/sw.js en 403 — Docker overlay)
- [ ] Tests end-to-end
- [ ] Optimisation performance (cache API, compression gzip)
- [ ] OAuth Reddit pour les sources bloquées
