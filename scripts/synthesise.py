"""
TDDG Monthly Bulletin - Monthly synthesis.

Merges all analysed articles for a target month and runs them through
prompts/monthly_synthesis.md to produce a structured bulletin JSON,
then runs prompts/sanity_check.md over the draft to flag issues.

Outputs:
    bulletins/drafts/{YYYY-MM}/bulletin.json   - the synthesised bulletin
    bulletins/drafts/{YYYY-MM}/issues.json     - sanity-check findings

Run from project root:
    python scripts/synthesise.py                  # previous month
    python scripts/synthesise.py --month 2026-05  # explicit month

Requires ANTHROPIC_API_KEY in .env.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
import yaml
from dotenv import load_dotenv
from jinja2 import Template


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
ANALYSED_DIR = PROJECT_ROOT / "data" / "analysed"
DRAFTS_DIR = PROJECT_ROOT / "bulletins" / "drafts"
LOGS_DIR = PROJECT_ROOT / "logs"


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
# Above this article count we switch from a single-call synthesis to a
# two-pass chunked-by-branch approach.
#
# Reasoning:
# - Opus 4-6 has a 200k-token context window, so 35 articles (~2-3kb of JSON
#   each plus the branches.yaml and the prompt) fits comfortably below 60k
#   input tokens with plenty of room for the structured output.
# - Single-call synthesis is preferred when feasible: the model sees every
#   article at once, which produces better cross-cutting themes and a more
#   coherent BLUF.
# - Above ~35 articles, single-call quality degrades empirically (the model
#   shifts from analysing to summarising), and chunking by branch gives more
#   focused branch sections at the cost of weaker cross-branch synthesis.
# - The assembly call after per-branch synthesis re-introduces cross-branch
#   thinking using compact per-branch summaries instead of raw articles.
SINGLE_CALL_THRESHOLD = 35

DEFAULT_MODEL = "claude-opus-4-6"
DEFAULT_MAX_OUTPUT_TOKENS = 8000

RETRY_ERRORS: tuple = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def previous_month_label() -> str:
    today = datetime.now()
    first_of_this_month = today.replace(day=1)
    last_of_prev = first_of_this_month - timedelta(days=1)
    return last_of_prev.strftime("%Y-%m")


def setup_logging(month_label: str) -> tuple[logging.Logger, Path]:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"synthesise_{month_label}_{ts}.log"

    logger = logging.getLogger("synthesise")
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


def merge_month_articles(month_label: str, logger: logging.Logger) -> list[dict]:
    """Walk data/analysed/*/articles.json; collect unique items in this month."""
    seen: dict[str, dict] = {}
    found_dirs = 0
    if not ANALYSED_DIR.exists():
        return []
    for date_dir in sorted(ANALYSED_DIR.iterdir()):
        if not date_dir.is_dir():
            continue
        # Folder names are YYYY-MM-DD; keep only those in the target month.
        if not date_dir.name.startswith(month_label):
            continue
        path = date_dir / "articles.json"
        if not path.exists():
            continue
        found_dirs += 1
        with open(path, "r", encoding="utf-8") as f:
            articles = json.load(f)
        for a in articles:
            if a.get("id") and a["id"] not in seen:
                seen[a["id"]] = a
    logger.info(
        "Found %d analysed folders for %s; %d unique articles after dedupe",
        found_dirs, month_label, len(seen),
    )
    return list(seen.values())


def parse_json_response(text: str) -> dict:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return json.loads(s)


def call_with_retry(
    client: anthropic.Anthropic,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    logger: logging.Logger,
    max_retries: int = 3,
) -> str:
    delay = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return "".join(getattr(b, "text", "") for b in resp.content)
        except RETRY_ERRORS as exc:
            if attempt == max_retries:
                raise
            logger.warning(
                "API attempt %d/%d failed (%s); retrying in %.1fs",
                attempt, max_retries, type(exc).__name__, delay,
            )
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def render_synthesis_prompt(
    template_text: str,
    month_label: str,
    branches_yaml_text: str,
    articles: list[dict],
) -> str:
    tpl = Template(template_text)
    return tpl.render(
        month_label=month_label,
        branches_yaml=branches_yaml_text,
        analysed_articles_json=json.dumps(articles, indent=2, ensure_ascii=False),
    )


# ---------------------------------------------------------------------------
# Synthesis strategies
# ---------------------------------------------------------------------------
def synthesise_single_call(
    client, model, system, synthesis_tpl_text,
    month_label, branches_yaml_text, articles, max_tokens, logger,
) -> dict:
    prompt = render_synthesis_prompt(
        synthesis_tpl_text, month_label, branches_yaml_text, articles,
    )
    logger.info(
        "Single-call synthesis: %d articles, prompt ~%d chars",
        len(articles), len(prompt),
    )
    text = call_with_retry(client, model, system, prompt, max_tokens, logger)
    return parse_json_response(text)


def group_by_top_branch(articles: list[dict]) -> dict:
    """Bucket articles by the top-scoring branch in their analysis.branch_relevance."""
    by_branch: dict = defaultdict(list)
    for a in articles:
        br = (a.get("analysis") or {}).get("branch_relevance") or []
        if not br:
            by_branch["unassigned"].append(a)
            continue
        try:
            top = max(br, key=lambda x: float(x.get("score", 0) or 0))
            bid = top.get("branch_id", "unassigned")
        except (ValueError, TypeError):
            bid = "unassigned"
        by_branch[bid].append(a)
    return dict(by_branch)


def synthesise_chunked(
    client, model, system, synthesis_tpl_text,
    month_label, branches_yaml_text, articles, max_tokens, logger,
) -> dict:
    """Two-pass synthesis when the corpus is too large for one call."""
    groups = group_by_top_branch(articles)
    logger.info(
        "Chunked synthesis: %d branch buckets (incl. %d unassigned articles)",
        len(groups), len(groups.get("unassigned", [])),
    )

    # Pass 1: per-branch synthesis using the same prompt with a smaller corpus.
    per_branch: dict = {}
    for branch_id, branch_articles in groups.items():
        logger.info(
            "[chunk pass-1] branch=%s articles=%d",
            branch_id, len(branch_articles),
        )
        prompt = render_synthesis_prompt(
            synthesis_tpl_text, month_label, branches_yaml_text, branch_articles,
        )
        text = call_with_retry(client, model, system, prompt, max_tokens, logger)
        per_branch[branch_id] = parse_json_response(text)

    # Pass 2: assembly. Compose a compact input from the per-branch outputs.
    per_branch_summaries = []
    for branch_id, b_out in per_branch.items():
        for sec in (b_out.get("branch_sections") or []):
            per_branch_summaries.append({
                "branch_id": sec.get("branch_id", branch_id),
                "branch_name": sec.get("branch_name", ""),
                "summary": sec.get("summary", ""),
                "items": sec.get("items", []),
            })

    article_refs = [
        {
            "id": a["id"],
            "title": a.get("title", ""),
            "url": a.get("url", ""),
            "source": a.get("source_name", ""),
            "date": a.get("published_date", ""),
            "top_score": float(
                ((a.get("analysis") or {}).get("relevance_score") or 0)
            ),
        }
        for a in articles
    ]

    assembly_prompt = (
        f"You have produced per-branch synthesis outputs for the {month_label} "
        f"TDDG bulletin. Now assemble the FINAL monthly bulletin in the same "
        f"JSON schema you used per-branch.\n\n"
        f"Re-use the per-branch sections verbatim as `branch_sections`. Your "
        f"job is to write the global pieces:\n"
        f"  - bluf (headline + 3-5 sentence summary)\n"
        f"  - branch_relevance_dashboard (all 6 branches, even quiet ones)\n"
        f"  - key_developments (3-7 across all branches)\n"
        f"  - cross_cutting_themes (ONLY if >=3 articles share the thread)\n"
        f"  - watch_areas (forward-looking)\n"
        f"  - source_references (every article cited)\n\n"
        f"Per-branch synthesis outputs:\n"
        f"```json\n{json.dumps(per_branch_summaries, indent=2)}\n```\n\n"
        f"Article reference table (for source_references):\n"
        f"```json\n{json.dumps(article_refs, indent=2)}\n```\n\n"
        f"Emit only the JSON object. No preamble. No markdown fence. Use the "
        f"exact schema from monthly_synthesis.md."
    )
    logger.info("[chunk pass-2] assembly call: %d branch summaries, %d articles",
                len(per_branch_summaries), len(article_refs))
    text = call_with_retry(client, model, system, assembly_prompt, max_tokens, logger)
    return parse_json_response(text)


# ---------------------------------------------------------------------------
# Sanity check
# ---------------------------------------------------------------------------
def run_sanity_check(
    client, model, system, sanity_tpl_text,
    bulletin: dict, articles: list[dict], max_tokens, logger,
) -> dict:
    tpl = Template(sanity_tpl_text)
    prompt = tpl.render(
        bulletin_json=json.dumps(bulletin, indent=2, ensure_ascii=False),
        analysed_articles_json=json.dumps(articles, indent=2, ensure_ascii=False),
    )
    logger.info("Sanity-check call: prompt ~%d chars", len(prompt))
    text = call_with_retry(client, model, system, prompt, max_tokens, logger)
    return parse_json_response(text)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--month",
        default=previous_month_label(),
        help="Target month in YYYY-MM format (default: previous month).",
    )
    args = parser.parse_args()
    month_label = args.month

    if not re.fullmatch(r"\d{4}-\d{2}", month_label):
        print(f"--month must be YYYY-MM, got {month_label!r}", file=sys.stderr)
        return 2

    logger, log_path = setup_logging(month_label)
    logger.info("=" * 70)
    logger.info("TDDG Bulletin - synthesise run for %s", month_label)
    logger.info("=" * 70)

    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY missing from .env")
        return 2

    articles = merge_month_articles(month_label, logger)
    if not articles:
        logger.error(
            "No analysed articles found for %s. Make sure data/analysed/%s-*/"
            " exists and contains articles.json.", month_label, month_label,
        )
        return 2

    settings = load_yaml(CONFIG_DIR / "settings.yaml")
    branches_yaml_text = load_text(CONFIG_DIR / "branches.yaml")
    system_prompt = load_text(PROMPTS_DIR / "system.md")
    synthesis_tpl_text = load_text(PROMPTS_DIR / "monthly_synthesis.md")
    sanity_tpl_text = load_text(PROMPTS_DIR / "sanity_check.md")

    model = (settings.get("claude_model_render")
             or settings.get("claude_model")
             or DEFAULT_MODEL)
    max_tokens = settings.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)

    logger.info("Model: %s  max_tokens: %d", model, max_tokens)

    client = anthropic.Anthropic(api_key=api_key)

    # --- synthesis ---------------------------------------------------------
    try:
        if len(articles) <= SINGLE_CALL_THRESHOLD:
            bulletin = synthesise_single_call(
                client, model, system_prompt, synthesis_tpl_text,
                month_label, branches_yaml_text, articles, max_tokens, logger,
            )
        else:
            bulletin = synthesise_chunked(
                client, model, system_prompt, synthesis_tpl_text,
                month_label, branches_yaml_text, articles, max_tokens, logger,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Synthesis failed: %s", exc)
        return 1

    # --- sanity check ------------------------------------------------------
    issues_payload: dict
    try:
        issues_payload = run_sanity_check(
            client, model, system_prompt, sanity_tpl_text,
            bulletin, articles, max_tokens, logger,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Sanity-check failed: %s", exc)
        issues_payload = {
            "issues": [],
            "summary": {"error": f"sanity-check failed: {exc}"},
            "ready_for_review": False,
        }

    # --- write outputs -----------------------------------------------------
    out_dir = DRAFTS_DIR / month_label
    out_dir.mkdir(parents=True, exist_ok=True)
    bulletin_path = out_dir / "bulletin.json"
    issues_path = out_dir / "issues.json"
    with open(bulletin_path, "w", encoding="utf-8") as f:
        json.dump(bulletin, f, indent=2, ensure_ascii=False)
    with open(issues_path, "w", encoding="utf-8") as f:
        json.dump(issues_payload, f, indent=2, ensure_ascii=False)

    # --- summary -----------------------------------------------------------
    n_issues = len(issues_payload.get("issues") or [])
    ready = issues_payload.get("ready_for_review", False)
    logger.info("=" * 70)
    logger.info("Synthesis complete for %s", month_label)
    logger.info("  Articles synthesised:   %d", len(articles))
    logger.info("  Sections produced:      %d", sum(
        1 for k in ("bluf","branch_relevance_dashboard","key_developments",
                    "branch_sections","cross_cutting_themes","watch_areas",
                    "source_references")
        if bulletin.get(k)
    ))
    logger.info("  Sanity-check issues:    %d", n_issues)
    logger.info("  Ready for review:       %s", ready)
    logger.info("  Bulletin: %s", bulletin_path)
    logger.info("  Issues:   %s", issues_path)
    logger.info("  Log:      %s", log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
