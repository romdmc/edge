#!/usr/bin/env python3
"""
Fix all generated HTML files: replace absolute paths with relative ones.
For GitHub Pages served from /edge/, absolute paths like /articles/xxx.html
don't work. We make them relative: articles/xxx.html
Also fix API calls to point to VPS when on GitHub Pages.
"""
import os, re, sys

output_dir = sys.argv[1] if len(sys.argv) else 'output'
vps_api_base = sys.argv[2] if len(sys.argv) > 1 else ''  # e.g. 'http://72.60.187.136:8081'

fixed_files = 0
total_replacements = 0

for root, dirs, files in os.walk(output_dir):
    for fname in files:
        if not fname.endswith('.html'):
            continue
        fpath = os.path.join(root, fname)
        
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        original = content
        
        # Fix absolute internal links: href="/articles/..." -> href="articles/..."
        # But NOT href="http://..." or href="https://..."
        # Pattern: href="/(something)" where something is not http
        content = re.sub(
            r'href="/(articles/|tags/|series/|sources/|digest\.html|search\.html|all\.html|archives\.html|manifeste\.html|login\.html|register\.html|newsletter\.html|newsletter_subscribe\.html|newsletter_unsubscribe\.html|index\.html)"',
            r'href="\1"',
            content
        )
        
        # Fix absolute paths in navigation links (href="/" is OK for home, but should be relative too)
        # href="/" -> href="./" for pages in subdirectories, or href="index.html" for root
        rel_path = os.path.relpath(output_dir, root)
        if rel_path == '.':
            # We're in root
            content = re.sub(r'href="/(?![/\w])"', 'href="index.html"', content)
            content = re.sub(r'href="/series\.html"', 'href="series.html"', content)
        else:
            # We're in a subdirectory (e.g. articles/, tags/)
            prefix = rel_path + '/'
            content = re.sub(r'href="/"', 'href="' + prefix + 'index.html"', content)
        
        # Fix API calls to point to VPS
        if vps_api_base:
            content = content.replace("fetch('/api/", "fetch('" + vps_api_base + "/api/")
            content = content.replace("fetch(\"'/api/", "fetch('" + vps_api_base + "/api/")
        
        # Fix form actions
        content = re.sub(r'action="/(api/[^"]*)"', r'action="\1"', content)
        
        if content != original:
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(content)
            fixed_files += 1
            
            # Count replacements
            changes = sum(1 for a, b in zip(original.split('\n'), content.split('\n')) if a != b)
            total_replacements += changes
            print('Fixed: %s (%d changes)' % (os.path.relpath(fpath, output_dir), changes))

print('\n=== Done ===')
print('Files fixed: %d' % fixed_files)
print('Total line changes: %d' % total_replacements)
