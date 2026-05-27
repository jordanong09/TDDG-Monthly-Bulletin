"""
TDDG Monthly Bulletin - HTML renderer.

Reads bulletins/drafts/{YYYY-MM}/bulletin.json (and optional issues.json
from the sanity-check stage), then renders an HTML draft via the Jinja
template at website/templates/bulletin.html and writes:

    bulletins/drafts/{YYYY-MM}/index.html

Run from project root:
    python scripts/render.py                  # render the most recent draft
    python scripts/render.py --month 2026-05  # explicit month

This step uses no Claude API and no network.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DRAFTS_DIR = PROJECT_ROOT / "bulletins" / "drafts"
PUBLISHED_DIR = PROJECT_ROOT / "bulletins" / "published"
TEMPLATE_DIR = PROJECT_ROOT / "website" / "templates"


def latest_draft_month():
    if not DRAFTS_DIR.exists():
        return None
    candidates = sorted(
        p.name for p in DRAFTS_DIR.iterdir()
        if p.is_dir() and (p / "bulletin.json").exists()
    )
    return candidates[-1] if candidates else None


def render_bulletin(bulletin_path, output_path, is_draft=True,
                    issues_path=None, generated_at=None):
    with open(bulletin_path, "r", encoding="utf-8") as f:
        bulletin = json.load(f)
    issues = None
    if is_draft and issues_path is not None and issues_path.exists():
        with open(issues_path, "r", encoding="utf-8") as f:
            issues = json.load(f)
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("bulletin.html")
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = template.render(
        bulletin=bulletin, issues=issues,
        is_draft=is_draft, generated_at=generated_at,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path.stat().st_size


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--month",
        help="Month to render in YYYY-MM (default: latest draft).")
    parser.add_argument("--published", action="store_true",
        help="Render from bulletins/published/{month}/ without the DRAFT watermark.")
    args = parser.parse_args()

    base_dir = PUBLISHED_DIR if args.published else DRAFTS_DIR
    is_draft = not args.published

    month_label = args.month or latest_draft_month()
    if month_label is None:
        print("No drafts found. Run scripts/synthesise.py first.", file=sys.stderr)
        return 2
    if not re.fullmatch(r"\d{4}-\d{2}", month_label):
        print(f"--month must be YYYY-MM, got {month_label!r}", file=sys.stderr)
        return 2

    src_dir = base_dir / month_label
    bulletin_path = src_dir / "bulletin.json"
    issues_path = src_dir / "issues.json"
    if not bulletin_path.exists():
        print(f"Missing {bulletin_path}", file=sys.stderr)
        return 2

    out_path = src_dir / "index.html"
    size_bytes = render_bulletin(
        bulletin_path=bulletin_path,
        output_path=out_path,
        is_draft=is_draft,
        issues_path=issues_path,
    )
    size_kb = size_bytes / 1024
    label = "DRAFT" if is_draft else "PUBLISHED"
    print(f"[{label}] {month_label}  ->  {out_path}  ({size_kb:.1f} KB)")
    if is_draft and issues_path.exists():
        with open(issues_path, "r", encoding="utf-8") as f:
            issues = json.load(f)
        n = len(issues.get("issues") or [])
        ready = issues.get("ready_for_review")
        print(f"  sanity-check: {n} issue(s); ready_for_review = {ready}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
