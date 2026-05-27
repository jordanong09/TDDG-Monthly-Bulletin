"""
TDDG Monthly Bulletin - Human-review promotion.

Promotes an approved draft into the published archive and refreshes the
website's landing and archive pages.

Required workflow:
    1. Open bulletins/drafts/{YYYY-MM}/index.html and read it.
    2. If acceptable, create bulletins/drafts/{YYYY-MM}/APPROVED.md with
       your name, today's date, and any review notes.
    3. Run:  python scripts/promote.py --month YYYY-MM

Auto-promotion is intentionally impossible: without APPROVED.md, this
script refuses to run.

What gets written:
    bulletins/published/{YYYY-MM}/bulletin.json    (copy of draft)
    bulletins/published/{YYYY-MM}/APPROVED.md      (copy of approval)
    bulletins/published/{YYYY-MM}/index.html       (rendered, no DRAFT watermark)
    website/archive/{YYYY-MM}/index.html           (same HTML, served by GitHub Pages)
    website/index.html                             (regenerated landing)
    website/archive/index.html                     (regenerated archive list)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

# Reuse the bulletin renderer rather than duplicating the template loader.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from render import render_bulletin


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DRAFTS_DIR = PROJECT_ROOT / "bulletins" / "drafts"
PUBLISHED_DIR = PROJECT_ROOT / "bulletins" / "published"
WEBSITE_DIR = PROJECT_ROOT / "website"
WEBSITE_ARCHIVE_DIR = WEBSITE_DIR / "archive"
TEMPLATE_DIR = WEBSITE_DIR / "templates"
LOGS_DIR = PROJECT_ROOT / "logs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def setup_logging(month_label: str) -> tuple[logging.Logger, Path]:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"promote_{month_label}_{ts}.log"

    logger = logging.getLogger("promote")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger, log_path


def list_published_months() -> list[str]:
    """Return YYYY-MM folders in bulletins/published/ that have a bulletin.json,
    sorted descending (newest first)."""
    if not PUBLISHED_DIR.exists():
        return []
    months = [
        p.name for p in PUBLISHED_DIR.iterdir()
        if p.is_dir()
        and re.fullmatch(r"\d{4}-\d{2}", p.name)
        and (p / "bulletin.json").exists()
    ]
    months.sort(reverse=True)
    return months


def load_bulletin_meta(month: str) -> dict:
    """Read bulletins/published/{month}/bulletin.json and return a compact
    summary for landing/archive listings."""
    path = PUBLISHED_DIR / month / "bulletin.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    bluf = data.get("bluf") or {}
    return {
        "month": month,
        "headline": bluf.get("headline", "(no headline)"),
        "summary": bluf.get("summary", ""),
        "bluf": bluf,
        "branch_relevance_dashboard": data.get("branch_relevance_dashboard") or [],
    }


def render_landing(logger: logging.Logger) -> Path:
    months = list_published_months()
    current = None
    recent: list[dict] = []
    if months:
        current = load_bulletin_meta(months[0])
        recent = [
            {
                "month": m,
                "headline": load_bulletin_meta(m)["headline"],
            }
            for m in months[1:6]
        ]

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("index.html")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = template.render(
        current=current,
        recent=recent,
        generated_at=generated_at,
    )
    out = WEBSITE_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    logger.info("Wrote landing: %s (%d bytes)", out, out.stat().st_size)
    return out


def render_archive(logger: logging.Logger) -> Path:
    months = list_published_months()
    by_year: dict[str, list[dict]] = defaultdict(list)
    for m in months:
        year = m.split("-", 1)[0]
        meta = load_bulletin_meta(m)
        by_year[year].append({"month": m, "headline": meta["headline"]})

    years_desc = sorted(by_year.keys(), reverse=True)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("archive.html")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = template.render(
        by_year=dict(by_year),
        years_desc=years_desc,
        total_count=sum(len(v) for v in by_year.values()),
        generated_at=generated_at,
    )
    WEBSITE_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    out = WEBSITE_ARCHIVE_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    logger.info("Wrote archive: %s (%d bytes)", out, out.stat().st_size)
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--month", required=True,
        help="Month to promote, in YYYY-MM format.",
    )
    args = parser.parse_args()
    month_label = args.month

    if not re.fullmatch(r"\d{4}-\d{2}", month_label):
        print(f"--month must be YYYY-MM, got {month_label!r}", file=sys.stderr)
        return 2

    logger, log_path = setup_logging(month_label)
    logger.info("=" * 70)
    logger.info("TDDG Bulletin - promotion attempt for %s", month_label)
    logger.info("=" * 70)

    draft_dir = DRAFTS_DIR / month_label
    bulletin_path = draft_dir / "bulletin.json"
    approved_path = draft_dir / "APPROVED.md"
    issues_path = draft_dir / "issues.json"

    # --- Gate 1: draft exists ---------------------------------------------
    if not bulletin_path.exists():
        logger.error(
            "Draft not found: %s\n"
            "  Run scripts/synthesise.py --month %s first.",
            bulletin_path, month_label,
        )
        return 2

    # --- Gate 2: APPROVED.md exists (the human-review checkpoint) ---------
    if not approved_path.exists():
        msg = (
            "\n" + ("=" * 70) + "\n"
            "REFUSING TO PROMOTE: no APPROVED.md in the draft folder.\n"
            + ("=" * 70) + "\n\n"
            f"This is the human-review checkpoint. To approve {month_label}:\n\n"
            f"  1. Open the draft in a browser:\n"
            f"     {(draft_dir / 'index.html')}\n\n"
            f"  2. If the bulletin is acceptable, create the approval file:\n"
            f"     {approved_path}\n\n"
            f"     Include your name, today's date, and any review notes.\n"
            f"     Example contents:\n\n"
            f"       # Approved\n"
            f"       Reviewer: <your name>\n"
            f"       Date: YYYY-MM-DD\n"
            f"       Notes: Reviewed BLUF, branch sections, sanity-check\n"
            f"              findings. OK to publish.\n\n"
            f"  3. Re-run:\n"
            f"     python scripts/promote.py --month {month_label}\n"
        )
        logger.error(msg)
        return 1

    # --- Stage 1: copy draft -> published ---------------------------------
    published_month_dir = PUBLISHED_DIR / month_label
    published_month_dir.mkdir(parents=True, exist_ok=True)
    pub_bulletin = published_month_dir / "bulletin.json"
    pub_approved = published_month_dir / "APPROVED.md"
    shutil.copy2(bulletin_path, pub_bulletin)
    shutil.copy2(approved_path, pub_approved)
    if issues_path.exists():
        # Drafts include sanity-check issues; preserve them in the published
        # folder as an audit trail (they are NOT rendered in the public HTML).
        shutil.copy2(issues_path, published_month_dir / "issues.json")
    logger.info("Copied draft -> %s", published_month_dir)

    # --- Stage 2: render published bulletin (no DRAFT watermark) ---------
    pub_html = published_month_dir / "index.html"
    render_bulletin(
        bulletin_path=pub_bulletin,
        output_path=pub_html,
        is_draft=False,
    )
    logger.info("Rendered published bulletin: %s", pub_html)

    # --- Stage 3: also place the same HTML under website/archive/ --------
    web_month_dir = WEBSITE_ARCHIVE_DIR / month_label
    web_month_dir.mkdir(parents=True, exist_ok=True)
    web_html = web_month_dir / "index.html"
    shutil.copy2(pub_html, web_html)
    logger.info("Mirrored to web archive: %s", web_html)

    # --- Stage 4: regenerate landing + archive list -----------------------
    render_landing(logger)
    render_archive(logger)

    # --- Done -------------------------------------------------------------
    logger.info("=" * 70)
    logger.info("PROMOTED %s", month_label)
    logger.info("  Published JSON:    %s", pub_bulletin)
    logger.info("  Published HTML:    %s", pub_html)
    logger.info("  Web archive page:  %s", web_html)
    logger.info("  Landing:           %s", WEBSITE_DIR / "index.html")
    logger.info("  Archive index:     %s", WEBSITE_ARCHIVE_DIR / "index.html")
    logger.info("  Log:               %s", log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
