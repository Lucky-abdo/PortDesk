#!/usr/bin/env python3
"""Extract <script> contents from CLIENT.html and run it through node --check
to verify there are no syntax errors introduced by the Phase 4/5/6 edits."""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
PROJECT = HERE.parent / 'portdesk'
html_path = PROJECT / 'CLIENT.html'

html = html_path.read_text(encoding='utf-8')

# Extract <script>...</script> blocks (no src= attribute)
scripts = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, flags=re.DOTALL)
print(f'Found {len(scripts)} inline <script> blocks')

# Write each one to a separate .js file and run node --check
any_failed = False
for i, src in enumerate(scripts):
    js_path = HERE / f'_client_script_{i}.js'
    js_path.write_text(src, encoding='utf-8')
    r = subprocess.run(['node', '--check', str(js_path)], capture_output=True, text=True)
    if r.returncode != 0:
        print(f'  Script {i}: FAIL ({len(src)} bytes)')
        print('  ' + r.stderr.replace('\n', '\n  '))
        any_failed = True
    else:
        print(f'  Script {i}: OK ({len(src)} bytes)')
    try:
        js_path.unlink()
    except Exception:
        pass

sys.exit(1 if any_failed else 0)
