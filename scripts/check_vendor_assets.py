"""Vendor assets gate: frontend must render without any public CDN.

Checks that the self-hosted vendor files exist, no template still references
a public CDN for bootstrap/marked, all five touched templates compile, and
the vendor files serve through the app with 200.
"""
from pathlib import Path

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import insights_web

TEMPLATES = ("base.html", "login.html", "signup.html", "article.html", "article_edit.html")
VENDOR = ("vendor/bootstrap.min.css", "vendor/bootstrap.bundle.min.js", "vendor/marked.min.js")


def main():
    root = Path(__file__).resolve().parent.parent

    for v in VENDOR:
        p = root / "static" / v
        assert p.is_file() and p.stat().st_size > 1000, f"missing vendor file: {v}"

    hits = [
        str(p)
        for p in (root / "templates").rglob("*.html")
        if "cdn.jsdelivr.net" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert not hits, f"jsdelivr references remain: {hits}"

    app = insights_web.app
    for t in TEMPLATES:
        app.jinja_env.get_template(t)

    client = app.test_client()
    for v in VENDOR:
        r = client.get(f"/static/{v}")
        assert r.status_code == 200, (v, r.status_code)

    r = client.get("/login")
    assert r.status_code == 200, r.status_code
    assert b"vendor/bootstrap.min.css" in r.data
    assert b"jsdelivr" not in r.data

    print("VENDOR_ASSETS_OK")


if __name__ == "__main__":
    main()
