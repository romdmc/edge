# EDGE — Plan d'Amélioration

> Document vivant. Mis à jour au fil des itérations.

---

## Phase 1 — Fondations (Semaines 1-4)

### Fiabilisation du pipeline
| Tâche | Priorité | Détail |
|---|---|---|
| Gestion d'erreurs robuste | P0 | Retry avec backoff, circuit breaker sur appels LLM |
| Monitoring | P0 | Alertes Telegram si pipeline échoue, logs structurés |
| Tests automatisés | P1 | Tests unitaires scraper/analyzer, tests d'intégration pipeline |
| Health check | P1 | Endpoint `/health` pour monitoring uptime |

### Compléter les sources
| Tâche | Priorité | Détail |
|---|---|---|
| YouTube Data API v3 | P0 | Clé API gratuite (10k req/jour), transcripts auto |
| Twitter/X API | P1 | Comptes influents edge/cloud/value |
| Newsletters | P1 | Import via email (Mailgun forward → webhook) |
| Hacker News API | P1 | API officielle pour commentaires et score |
| Reddit OAuth | P1 | Compte dédié pour contourner le 403 |

### Améliorer le scoring LLM
| Tâche | Priorité | Détail |
|---|---|---|
| Prompts optimisés | P0 | Few-shot examples, meilleur contexte edge/value/coût |
| Seuils adaptatifs | P1 | Auto-ajustement pour viser ~15-20 articles/jour |
| Détection de doublons sémantiques | P1 | Pas juste hash, mais similarité de contenu |
| Multi-modèle | P2 | Cross-validation avec 2 modèles différents |

---

## Phase 2 — Enrichissement (Semaines 5-8)

### Contenu
| Tâche | Priorité | Détail |
|---|---|---|
| Sources spécialisées edge | P0 | Blogs IoT, MEC, fog computing, CDN |
|| Catégorisation fine | P1 | Sous-topiques : IoT, 5G, serverless, FinOps, green IT |
|| Digest hebdomadaire | ✅ FAIT | Résumé "best of the week" chaque lundi — `/digest.html` |
|| Articles liés | ✅ FAIT | Cross-liens entre articles sur même sujet — section "Autres articles de cette source" |
|| Pages sources | ✅ FAIT | `/sources.html` + pages détaillées par source |
|| Séries thématiques | ✅ FAIT | `/series.html` + pages détaillées par topic |
|| Newsletter HTML | ✅ FAIT | Template email responsive — `/newsletter.html` |
|| Pagination | ✅ FAIT | `/all.html` paginée (20 articles/page) |
|| Recherche | ✅ FAIT | Barre de recherche + pages de résultats pré-générées |

### Newsletter
| Tâche | Priorité | Détail |
|---|---|---|
| Template email texte brut | P0 | Simple, lisible, sans HTML complexe |
| Inscription/désinscription | P1 | Liste SQLite + token unique par utilisateur |
| Envoi automatique | P1 | Via cron + SMTP (Mailgun free tier ou SMTP dédié) |
| Personnalisation | P2 | Préférences de topics par utilisateur |

---

## Phase 3 — Intelligence (Semaines 9-12)

### Feedback utilisateur
| Tâche | Priorité | Détail |
|---|---|---|
| Upvote/downvote | P0 | Boutons sur chaque article, stocké en DB |
| Commentaires | P1 | Système léger (pas de compte, juste nom + texte) |
| Signaux implicites | P1 | Temps de lecture, clic sur source, partage |

### Détection de tendances
| Tâche | Priorité | Détail |
|---|---|---|
| Sujets émergents | P0 | Détection de pics d'actualité sur un topic |
| Évolution temporelle | P1 | Graphique de popularité des topics dans le temps |
| Alertes personnalisées | P2 | Notification quand un sujet dépasse un seuil |

### API publique
| Tâche | Priorité | Détail |
|---|---|---|
| REST API read-only | P0 | `/api/articles`, `/api/tags`, `/api/stats` |
| Rate limiting | P1 | 100 req/min par IP |
| Documentation | P1 | OpenAPI/Swagger auto-généré |
| Webhook | P2 | Notification quand nouveau digest publié |

---

## Phase 4 — Scale (Mois 4-6)

### Multi-langue contenu
| Tâche | Priorité | Détail |
|---|---|---|
| Traduction automatique | P1 | Articles traduits EN→FR via LLM |
| Sources non-anglophones | P2 | Blogs tech FR, DE, ES, JP |
| UI multi-langue | ✅ FAIT | FR/EN avec sélecteur |

### Multi-utilisateur
| Tâche | Priorité | Détail |
|---|---|---|
| Comptes utilisateurs | P1 | Auth simple (email + mot de passe) |
| Préférences | P1 | Topics favoris, fréquence de notification |
| Alertes personnalisées | P2 | "Alerte-moi sur edge computing" |
| Dashboard personnel | P2 | Historique de lecture, articles sauvés |

### Monétisation
| Tâche | Priorité | Détail |
|---|---|---|
| Newsletter premium | P2 | Contenu exclusif, early access |
| API payante | P2 | Au-delà de 1000 req/jour |
| Sponsoring ciblé | P2 | Articles sponsorisés clairement identifiés |
| Affiliation | P2 | Liens d'affiliation sur outils mentionnés |

### Application mobile
| Tâche | Priorité | Détail |
|---|---|---|
| PWA | P1 | Progressive Web App, installable, offline |
| Notifications push | P2 | Web Push API pour nouveaux digests |
| App native | P3 | React Native ou Flutter (plus tard) |

---

## Considérations transversales

### Sécurité
- Rate limiting sur API et formulaires
- Validation/sanitization des inputs
- Pas de données personnelles stockées inutilement
- HTTPS obligatoire (Traefik + Let's Encrypt)

### Performance
- Cache CDN (Cloudflare free tier) devant nginx
- Génération incrémentale (ne régénérer que le nouveau)
- Compression gzip/brotli nginx
- Images lazy loading

### Coûts estimés (mensuel)
| Poste | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| VPS | 0€ (existant) | 0€ | 0€ | 5-10€ |
| OpenRouter LLM | ~5€ | ~10€ | ~15€ | ~20€ |
| YouTube API | 0€ (gratuit) | 0€ | 0€ | 0€ |
| SMTP/Newsletter | 0€ | 0-5€ | 5-10€ | 10-20€ |
| **Total** | **~5€** | **~15€** | **~30€** | **~50€** |

### KPIs de succès
| Métrique | Objectif Phase 1 | Objectif Phase 4 |
|---|---|---|
| Articles/jour | 10-15 | 20-30 |
| Sources actives | 15 | 50+ |
| Uptime pipeline | 95% | 99% |
| Utilisateurs newsletter | 0 | 500+ |
| API calls/jour | 0 | 1000+ |
| Langues | 2 | 4+ |
