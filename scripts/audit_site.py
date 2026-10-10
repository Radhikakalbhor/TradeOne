import os
import sys
sys.path.insert(0, os.path.abspath("."))
import re
from app.main import app

routes = set()
for r in app.routes:
    if hasattr(r, 'path'):
        routes.add(r.path)

print(f"Total registered FastAPI routes: {len(routes)}")

templates_dir = "app/templates"
found_links = []
for root, dirs, files in os.walk(templates_dir):
    for f in files:
        if f.endswith(".html"):
            p = os.path.join(root, f)
            with open(p, "r", encoding="utf-8") as fh:
                content = fh.read()
                # Find href, src, action
                matches = re.findall(r'(?:href|src|action)=["\']([^"\'#?]+)(?:[?#][^"\']*)?["\']', content)
                for m in matches:
                    if not m.startswith("{{") and not m.startswith("http://") and not m.startswith("https://") and not m.startswith("data:") and not m.startswith("javascript:"):
                        found_links.append((p, m))

print(f"Total extracted links/assets from templates: {len(found_links)}")
print("--- AUDITING ASSETS AND ROUTES ---")

broken_assets = []
broken_routes = []

for tmpl, link in sorted(set(found_links)):
    # normalize relative path or static
    if link.startswith("/static/"):
        rel_path = os.path.join("app", link.lstrip("/").replace("/", os.sep))
        if not os.path.exists(rel_path):
            broken_assets.append((tmpl, link, rel_path))
    elif link.startswith("/"):
        matched = False
        for r in routes:
            pattern = "^" + re.sub(r"\{[^}]+\}", r"[^/]+", r) + "$"
            if re.match(pattern, link):
                matched = True
                break
        if not matched:
            broken_routes.append((tmpl, link))

print(f"\n[BROKEN ASSETS] Found {len(broken_assets)}:")
for tmpl, link, rel in broken_assets:
    print(f"  Asset: {link}")
    print(f"    In: {tmpl}")
    print(f"    Missing on disk: {rel}")

print(f"\n[POTENTIAL 404 ROUTES] Found {len(broken_routes)}:")
for tmpl, link in broken_routes:
    print(f"  Route: {link}")
    print(f"    In: {tmpl}")
