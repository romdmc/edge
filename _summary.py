import sqlite3, json

conn = sqlite3.connect('/root/domoria/projets/edge/data/edge.db')
c = conn.cursor()

# Articles
c.execute('SELECT COUNT(*) FROM articles')
total_articles = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM articles WHERE date(fetched_at) = '2026-06-17'")
today_articles = c.fetchone()[0]

print("=== Articles ===")
print("  Total in DB: %d" % total_articles)
print("  Fetched today: %d" % today_articles)

# Analyses
c.execute('SELECT COUNT(*) FROM analyses')
total_analyses = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM analyses WHERE date(analyzed_at) = '2026-06-17'")
today_analyses = c.fetchone()[0]

# Score distribution
c.execute('SELECT COUNT(*) FROM analyses WHERE overall_score >= 5.0')
above_threshold = c.fetchone()[0]
c.execute('SELECT COUNT(*) FROM analyses WHERE overall_score < 5.0')
below_threshold = c.fetchone()[0]

print()
print("=== Analyses ===")
print("  Total in DB: %d" % total_analyses)
print("  Analyzed today: %d" % today_analyses)
print("  Above threshold (>=5.0): %d" % above_threshold)
print("  Below threshold (<5.0): %d" % below_threshold)

# Pipeline runs - get column names first
c.execute('PRAGMA table_info(pipeline_runs)')
cols = c.fetchall()
col_names = [r[1] for r in cols]
print()
print("=== Pipeline Runs columns: %s ===" % col_names)

# Find the timestamp column
ts_col = None
for name in col_names:
    if 'start' in name.lower() or 'time' in name.lower() or 'date' in name.lower():
        ts_col = name
        break
if not ts_col and col_names:
    ts_col = col_names[0]

if ts_col:
    c.execute('SELECT * FROM pipeline_runs ORDER BY "%s" DESC LIMIT 3' % ts_col)
    runs = c.fetchall()
    print("=== Pipeline Runs (latest 3) ===")
    for r in runs:
        d = dict(zip(col_names, r))
        for k, v in d.items():
            print("  %s: %s" % (k, v))
        print()

# Latest analyses with scores
print("=== Latest 15 Analyses ===")
c.execute('SELECT a.title, an.overall_score, an.edge_score, an.value_score, an.cost_score, an.analyzed_at, an.title_fr FROM analyses an JOIN articles a ON an.article_id = a.id ORDER BY an.analyzed_at DESC LIMIT 15')
for (title, overall, es, vs, cs, analyzed, title_fr) in c.fetchall():
    t = (title_fr or title or 'N/A')[:85]
    print("  [%.1f] e=%.1f v=%.1f c=%.1f | %s | %s" % (overall or 0, es or 0, vs or 0, cs or 0, analyzed, t))

# Source stats
print()
print("=== Source Stats (latest 10) ===")
c.execute('PRAGMA table_info(source_stats)')
cols = c.fetchall()
col_names = [r[1] for r in cols]
c.execute('SELECT * FROM source_stats ORDER BY fetched_at DESC LIMIT 10')
for r in c.fetchall():
    d = dict(zip(col_names, r))
    print("  %s" % d)

conn.close()
