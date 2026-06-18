#!/usr/bin/env python3
"""
Fix all Jinja2 templates to use {{ site_url }} prefix for internal links.
This makes the site work both on VPS (site_url="") and GitHub Pages (site_url="https://romdmc.github.io/edge").
"""
import os, re

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'templates')
VPS_API_BASE = 'http://72.60.187.136:8081'

# Patterns to fix: href="/path" -> href="{{ site_url }}/path"
# But NOT href="http://..." or href="https://..."
LINK_PATTERN = re.compile(r'href="/(?!http|https|#|mailto|tel)([^"]*)"')
FETCH_PATTERN = re.compile(r"fetch\('/api/")
FETCH_PATTERN2 = re.compile(r'fetch\("/api/')

def fix_template(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original = content
    
    # Fix href="/path" -> href="{{ site_url }}/path"
    content = LINK_PATTERN.sub(r'href="{{ site_url }}/\1"', content)
    
    # Fix fetch('/api/...) -> fetch('{{ api_base }}/api/...)
    content = FETCH_PATTERN.sub("fetch('{{ api_base }}/api/", content)
    content = FETCH_PATTERN2.sub('fetch("{{ api_base }}/api/', content)
    
    if content != original:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print('Fixed: %s' % os.path.basename(filepath))
        return True
    return False

fixed = 0
for fname in os.listdir(TEMPLATES_DIR):
    if fname.endswith('.html'):
        fpath = os.path.join(TEMPLATES_DIR, fname)
        if fix_template(fpath):
            fixed += 1

print('\nTotal templates fixed: %d' % fixed)
