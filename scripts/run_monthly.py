"""
TDDG Monthly Bulletin - end-to-end pipeline orchestrator.

Runs collect -> filter -> analyse -> synthesise -> render in order.
Each stage is a subprocess that streams its own logs to this console.
On non-zero exit the orchestrator stops and surfaces the offending log path.

Run from project root (or via run_monthly.bat on Windows):
    python scripts/run_monthly.py                  # full pipeline, current month
    python scripts/run_monthly.py --skip-collect   # re-analyse without re-fetching
    python scripts/run_monthly.py --month 2026-04  # explicit target month for the bulletin

The orchestrator stops BEFORE promotion. Promotion is a separate, manual
step that requires APPROVED.md to exist - see MONTHLY_CHECKLIST.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_FILTERED = PROJECT_ROOT / "data" / "filtered"
DATA_ANALYSED = PROJECT_ROOT / "data" / "analysed"
DRAFTS_DIR = PROJECT_ROOT / "bulletins" / "drafts"
LOGS_DIR = PROJECT_ROOT / "logs"


def current_month_label() -> str:
    return datetime.now().strftime("%Y-%m")


def today_label() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def count_articles(path: Path) -> int | None:
    """Return article count from a JSON list file, or None if it doesn't exist."""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return len(data)
        return None
    except Exception:
        return None


def latest_log(prefix: str) -> Path | None:
    """Find the most recent log file matching {prefix}_*.log in logs/."""
    if not LOGS_DIR.exists():
        return None
    matches = sorted(LOGS_DIR.glob(f"{prefix}_*.log"))
    return matches[-1] if matches else None


def run_stage(label: str, cmd: list[str], log_prefix: str) -> tuple[int, float, Path | None]:
    """
    Run a stage as a subprocess. Stream its output live to this console.
    Return (returncode, elapsed_seconds, log_path).
    """
    print()
    print("=" * 70)
    print(f">>> {label}")
    print(f"    cmd: {' '.join(cmd)}")
    print("=" * 70, flush=True)
    start = time.monotonic()
    proc = subprocess.run(cmd, cwd=PROJECT_ROOT)
    elapsed = time.monotonic() - start
    log_path = latest_log(log_prefix)
    return proc.returncode, elapsed, log_path


def fail(label: str, rc: int, log_path: Path | None, elapsed: float) -> int:
    print()
    print("!" * 70)
    print(f"PIPELINE STOPPED: {label} exited with code {rc} after {elapsed:.1f}s")
    if log_path is not None:
        print(f"Log: {log_path}")
    print("!" * 70)
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--month",
        default=current_month_label(),
        help="Target month for synthesis + render, in YYYY-MM format "
             "(default: current month).",
    )
    parser.add_argument(
        "--skip-collect",
        action="store_true",
        help="Skip the collect step. Useful when re-analysing the same "
             "fetched data after a config or prompt change.",
    )
    args = parser.parse_args()

    month_label = args.month
    pipeline_start = time.monotonic()
    py = sys.executable

    print("=" * 70)
    print("TDDG Monthly Bulletin - pipeline orchestrator")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Target month: {month_label}")
    print(f"Skip collect: {args.skip_collect}")
    print("=" * 70)

    durations: list[tuple[str, float]] = []
    counts: dict[str, int | None] = {}

    # --- 1. collect -------------------------------------------------------
    if not args.skip_collect:
        rc, dt, log = run_stage(
            "Stage 1/5  collect",
            [py, str(SCRIPTS_DIR / "collect.py")],
            log_prefix="collect",
        )
        durations.append(("collect", dt))
        if rc != 0:
            return fail("collect", rc, log, dt)
    else:
        print("\n>>> Stage 1/5  collect  SKIPPED (--skip-collect)")
        durations.append(("collect", 0.0))

    counts["raw"] = count_articles(DATA_RAW / today_label() / "articles.json")

    # --- 2. filter --------------------------------------------------------
    rc, dt, log = run_stage(
        "Stage 2/5  filter",
        [py, str(SCRIPTS_DIR / "filter.py")],
        log_prefix="filter",
    )
    durations.append(("filter", dt))
    if rc != 0:
        return fail("filter", rc, log, dt)
    counts["filtered"] = count_articles(DATA_FILTERED / today_label() / "articles.json")

    # --- 3. analyse -------------------------------------------------------
    rc, dt, log = run_stage(
        "Stage 3/5  analyse",
        [py, str(SCRIPTS_DIR / "analyse.py")],
        log_prefix="analyse",
    )
    durations.append(("analyse", dt))
    if rc != 0:
        return fail("analyse", rc, log, dt)
    counts["analysed"] = count_articles(DATA_ANALYSED / today_label() / "articles.json")

    # --- 4. synthesise ----------------------------------------------------
    rc, dt, log = run_stage(
        "Stage 4/5  synthesise",
        [py, str(SCRIPTS_DIR / "synthesise.py"), "--month", month_label],
        log_prefix=f"synthesise_{month_label}",
    )
    durations.append(("synthesise", dt))
    if rc != 0:
        return fail("synthesise", rc, log, dt)

    bulletin_path = DRAFTS_DIR / month_label / "bulletin.json"
    issues_path = DRAFTS_DIR / month_label / "issues.json"
    counts["bulletin_sections"] = None
    if bulletin_path.exists():
        try:
            with open(bulletin_path, "r", encoding="utf-8") as f:
                b = json.load(f)
            counts["bulletin_sections"] = sum(
                1 for k in (
                    "bluf", "branch_relevance_dashboard", "key_developments",
                    "branch_sections", "cross_cutting_themes", "watch_areas",
                    "source_references"
                ) if b.get(k)
            )
        except Exception:
            pass

    # --- 5. render --------------------------------------------------------
    rc, dt, log = run_stage(
        "Stage 5/5  render",
        [py, str(SCRIPTS_DIR / "render.py"), "--month", month_label],
        log_prefix="render",  # render.py does not write its own log file
    )
    durations.append(("render", dt))
    if rc != 0:
        return fail("render", rc, log, dt)

    draft_html = DRAFTS_DIR / month_label / "index.html"
    total = time.monotonic() - pipeline_start

    # --- Summary ----------------------------------------------------------
    print()
    print("=" * 70)
    print("PIPELINE COMPLETE")
    print("=" * 70)
    print(f"Target month: {month_label}")
    print(f"Total time:   {total:.1f}s ({total/60:.1f} min)")
    print()
    print("Per-stage duration:")
    longest_label = max(len(s) for s, _ in durations)
    for s, dt in durations:
        bar = "#" * int(min(40, dt / max(total, 0.001) * 40))
        print(f"  {s.ljust(longest_label)}  {dt:6.1f}s  {bar}")
    print()
    print("Article counts:")
    print(f"  raw collected      : {counts.get('raw')}")
    print(f"  passed pre-filter  : {counts.get('filtered')}")
    print(f"  passed LLM analyse : {counts.get('analysed')}")
    print(f"  bulletin sections  : {counts.get('bulletin_sections')}")
    print()
    if issues_path.exists():
        try:
            with open(issues_path, "r", encoding="utf-8") as f:
                iss = json.load(f)
            n_iss = len(iss.get("issues") or [])
            ready = iss.get("ready_for_review")
            print(f"Sanity-check: {n_iss} issue(s); ready_for_review = {ready}")
        except Exception:
            pass

    print()
    if draft_html.exists():
        print(f"DRAFT READY for human review:")
        print(f"  {draft_html}")
        print()
        print("Next step: open it in a browser. If acceptable, follow")
        print("MONTHLY_CHECKLIST.md to write APPROVED.md and run promote.py.")
    else:
        print("WARNING: expected draft HTML at:")
        print(f"  {draft_html}")
        print("but the file does not exist. Check the render stage log.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
