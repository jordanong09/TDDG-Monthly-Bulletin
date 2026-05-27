"""
TDDG Monthly Bulletin - Pre-LLM relevance filter.

Reads the most recent data/raw/{YYYY-MM-DD}/articles.json, scores every
article against each branch's keyword vocabulary, applies hard exclusions
and always-include tripwires, and writes the survivors to
data/filtered/{YYYY-MM-DD}/articles.json.

This is a cheap coarse pass. Its job is to keep the obviously-irrelevant
out of the LLM stages, not to make final relevance calls. A relatively
permissive default (max_branch_score >= 0.20) is intentional - the LLM
filter that comes later does the precise judging.

Run from project root:
    python scripts/filter.py
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
RAW_DIR = PROJECT_ROOT / "data" / "raw"
FILTERED_DIR = PROJECT_ROOT / "data" / "filtered"
LOGS_DIR = PROJECT_ROOT / "logs"


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
# Cap raw keyword hits at this number before normalising to 0-1.
# Five distinct branch keywords in the title+summary is "this is squarely
# in scope"; more than that doesn't make it more relevant for our purposes.
KEYWORD_HIT_CAP = 5

# Boost added to a branch's score when one of the article's source-categories
# matches a branch's `primary_categories`. Keeps scoring honest when the
# source itself is clearly aligned (e.g. an MS&T item should naturally lean
# toward Simulation even if its title uses unusual wording).
CATEGORY_BOOST = 0.15

# Score floor for "pass via keyword". Tunable; users should expect to raise
# this after the first two real runs to keep noise down.
PASS_THRESHOLD = 0.20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def latest_raw_dir() -> Path | None:
    """Pick the most recent data/raw/YYYY-MM-DD/ that contains articles.json."""
    if not RAW_DIR.exists():
        return None
    candidates = sorted(
        [p for p in RAW_DIR.iterdir() if p.is_dir() and (p / "articles.json").exists()]
    )
    return candidates[-1] if candidates else None


def compile_keyword(keyword: str) -> tuple[str, re.Pattern]:
    """
    Compile a phrase to a regex with word-boundary anchors at the edges.

    Word boundaries prevent false positives like:
      - "AAR" matching inside "guARd"
      - "PME" matching inside "develoPMEnt"
      - "STE" matching inside "STEm"
      - "dies at" matching inside "stuDIES AT the academy"

    Internal characters (spaces, slashes, hyphens) are passed through via
    re.escape, so multi-word phrases and tokens like "I/ITSEC" or
    "human-machine teaming" match as written.

    Used for branch keywords, tripwires, and exclusions alike.
    """
    k = keyword.lower().strip()
    pattern = re.compile(rf"\b{re.escape(k)}\b", re.IGNORECASE)
    return k, pattern


def score_against_branch(text: str, compiled_keywords: list[tuple[str, re.Pattern]]) -> tuple[float, list[str]]:
    """Return (normalised_score, sorted_list_of_hit_keywords)."""
    hits = set()
    for label, pattern in compiled_keywords:
        if pattern.search(text):
            hits.add(label)
    raw = len(hits)
    score = min(raw, KEYWORD_HIT_CAP) / KEYWORD_HIT_CAP
    return score, sorted(hits)


def first_phrase_match(text: str, compiled_phrases: list[tuple[str, re.Pattern]]) -> str | None:
    """
    Return the label of the first compiled phrase whose pattern matches `text`.
    Phrases are compiled via compile_keyword so matching uses word boundaries -
    consistent with branch keyword scoring.
    """
    for label, pattern in compiled_phrases:
        if pattern.search(text):
            return label
    return None


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging() -> tuple[logging.Logger, Path]:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"filter_{ts}.log"

    logger = logging.getLogger("filter")
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


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main() -> int:
    logger, log_path = setup_logging()
    logger.info("=" * 70)
    logger.info("TDDG Bulletin - filter run starting")
    logger.info("=" * 70)

    raw_dir = latest_raw_dir()
    if raw_dir is None:
        logger.error("No data/raw/{date}/articles.json found. Run collect.py first.")
        return 2

    raw_path = raw_dir / "articles.json"
    with open(raw_path, "r", encoding="utf-8") as f:
        articles = json.load(f)
    logger.info("Input: %s  (%d articles)", raw_path, len(articles))

    branches_cfg = load_yaml(CONFIG_DIR / "branches.yaml")
    tripwires_cfg = load_yaml(CONFIG_DIR / "tripwires.yaml")
    exclusions_cfg = load_yaml(CONFIG_DIR / "exclusions.yaml")

    branches = branches_cfg.get("branches", [])
    tripwires = tripwires_cfg.get("tripwires", []) or []
    exclusions = exclusions_cfg.get("exclusions", []) or []

    logger.info(
        "Loaded %d branches, %d tripwires, %d exclusions",
        len(branches), len(tripwires), len(exclusions),
    )

    # Pre-compile keyword patterns per branch.
    compiled: dict[int, list[tuple[str, re.Pattern]]] = {}
    primary_cats: dict[int, set[str]] = {}
    branch_names: dict[int, str] = {}
    for b in branches:
        bid = b["id"]
        branch_names[bid] = b["name"]
        compiled[bid] = [compile_keyword(k) for k in b.get("keywords", [])]
        primary_cats[bid] = set(b.get("primary_categories", []) or [])

    # Pre-compile tripwires and exclusions with the same word-boundary logic
    # used for branch keywords. This is what stops "PME" from matching inside
    # "development" and "dies at" from matching inside "studies at".
    compiled_tripwires = [compile_keyword(t) for t in tripwires if t]
    compiled_exclusions = [compile_keyword(e) for e in exclusions if e]

    # Decision counters
    kept: list[dict] = []
    drop_excl = 0
    drop_score = 0
    pass_score = 0
    pass_tripwire = 0

    # For the post-run report we keep small samples of each outcome.
    sample_pass_score: list[dict] = []
    sample_pass_tripwire: list[dict] = []
    sample_drop_score: list[dict] = []
    sample_drop_excl: list[dict] = []
    SAMPLE_CAP = 5

    for art in articles:
        title = art.get("title", "") or ""
        summary = art.get("summary", "") or ""
        categories = set(art.get("categories", []) or [])
        text_for_scoring = f"{title}\n{summary}".lower()
        title_lower = title.lower()

        # 1. Hard exclusions on title (highest priority - can't be rescued).
        excl_hit = first_phrase_match(title, compiled_exclusions)
        if excl_hit:
            drop_excl += 1
            reason = f"excluded by '{excl_hit}'"
            logger.info("DROP  [excl] %-40s %s", excl_hit[:40], title[:100])
            if len(sample_drop_excl) < SAMPLE_CAP:
                sample_drop_excl.append({**art, "filter_reason": reason})
            continue

        # 2. Score against every branch.
        branch_scores: dict[str, float] = {}
        branch_hits: dict[str, list[str]] = {}
        max_score = 0.0
        max_bid: int | None = None
        for bid, patterns in compiled.items():
            score, hits = score_against_branch(text_for_scoring, patterns)
            # Category boost: only if this branch has a primary_categories list
            # and at least one of those categories is on the article.
            if primary_cats[bid] and primary_cats[bid] & categories:
                score = min(1.0, score + CATEGORY_BOOST)
            branch_scores[str(bid)] = round(score, 3)
            branch_hits[str(bid)] = hits
            if score > max_score:
                max_score = score
                max_bid = bid

        # 3. Always-include tripwires (checked AFTER scoring so we record scores).
        tripwire_hit = first_phrase_match(title, compiled_tripwires)

        decision_record = {
            **art,
            "branch_scores": branch_scores,
            "branch_hits": branch_hits,
            "max_branch_score": round(max_score, 3),
            "max_branch_id": max_bid,
            "max_branch_name": branch_names.get(max_bid) if max_bid else None,
        }

        if tripwire_hit and max_score < PASS_THRESHOLD:
            # Rescued from the drop pile by a tripwire.
            decision_record["filter_decision"] = "pass_tripwire"
            decision_record["filter_reason"] = f"tripwire '{tripwire_hit}'"
            kept.append(decision_record)
            pass_tripwire += 1
            logger.info("PASS  [trip] score=%.2f  %s", max_score, title[:100])
            if len(sample_pass_tripwire) < SAMPLE_CAP:
                sample_pass_tripwire.append(decision_record)
            continue

        if max_score >= PASS_THRESHOLD:
            decision_record["filter_decision"] = "pass_score"
            decision_record["filter_reason"] = (
                f"branch {max_bid} ({branch_names.get(max_bid)}) score {max_score:.2f}"
            )
            kept.append(decision_record)
            pass_score += 1
            logger.info(
                "PASS  [scor] score=%.2f branch=%s  %s",
                max_score, max_bid, title[:100],
            )
            if len(sample_pass_score) < SAMPLE_CAP:
                sample_pass_score.append(decision_record)
            continue

        # 4. Drop on low score.
        drop_score += 1
        reason = f"max score {max_score:.2f} < {PASS_THRESHOLD}"
        decision_record["filter_decision"] = "drop_score"
        decision_record["filter_reason"] = reason
        logger.info("DROP  [scor] score=%.2f  %s", max_score, title[:100])
        if len(sample_drop_score) < SAMPLE_CAP:
            sample_drop_score.append(decision_record)

    # ----------------------------------------------------------------------
    # Write survivors
    # ----------------------------------------------------------------------
    date_label = raw_dir.name  # YYYY-MM-DD
    out_dir = FILTERED_DIR / date_label
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "articles.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(kept, f, indent=2, ensure_ascii=False)

    # ----------------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------------
    logger.info("=" * 70)
    logger.info("Summary")
    logger.info("-" * 70)
    logger.info("Input articles:           %d", len(articles))
    logger.info("Passed (score):           %d", pass_score)
    logger.info("Passed (tripwire rescue): %d", pass_tripwire)
    logger.info("Dropped (low score):      %d", drop_score)
    logger.info("Dropped (hard exclusion): %d", drop_excl)
    logger.info("Kept total:               %d", len(kept))
    logger.info("-" * 70)
    logger.info("Output: %s", out_path)
    logger.info("Log:    %s", log_path)

    # Per-branch distribution among kept articles
    if kept:
        by_branch: dict[int, int] = {}
        for art in kept:
            bid = art.get("max_branch_id")
            if bid is not None:
                by_branch[bid] = by_branch.get(bid, 0) + 1
        logger.info("-" * 70)
        logger.info("Kept articles by top-scoring branch:")
        for bid in sorted(by_branch):
            logger.info(
                "  branch %d %-40s %d",
                bid, branch_names.get(bid, ""), by_branch[bid],
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
