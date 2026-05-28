"""
TDDG Monthly Bulletin - Per-article LLM analysis.

Reads the most recent data/filtered/{YYYY-MM-DD}/articles.json, runs each
article through prompts/per_article.md via the Claude API, parses the
JSON response into an `analysis` key on the article, and writes survivors
(relevant + score >= relevance_threshold) to data/analysed/{date}/articles.json.

Run from project root:
    python scripts/analyse.py

Requires ANTHROPIC_API_KEY in .env (copy .env.example and fill in the key).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import datetime
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
FILTERED_DIR = PROJECT_ROOT / "data" / "filtered"
ANALYSED_DIR = PROJECT_ROOT / "data" / "analysed"
LOGS_DIR = PROJECT_ROOT / "logs"


# ---------------------------------------------------------------------------
# Tunables (with sensible fallbacks if settings.yaml is missing the key)
# ---------------------------------------------------------------------------
DEFAULT_MAX_OUTPUT_TOKENS = 1500
DEFAULT_RELEVANCE_THRESHOLD = 0.55
DEFAULT_MAX_ARTICLES_PER_RUN = 80
DEFAULT_MODEL = "claude-opus-4-6"

# Article summaries are truncated to this many chars before being inserted
# into the prompt. RSS summaries are already capped at 800 in collect.py;
# this catches the rare longer-than-expected case from HTML scrapes.
MAX_SUMMARY_CHARS = 2000

# Anthropic SDK exceptions that are worth retrying. Auth/quota/bad-request
# errors are NOT in this set - they would just fail the same way on retry.
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


def latest_filtered_dir() -> Path | None:
    if not FILTERED_DIR.exists():
        return None
    candidates = sorted(
        [p for p in FILTERED_DIR.iterdir()
         if p.is_dir() and (p / "articles.json").exists()]
    )
    return candidates[-1] if candidates else None


def setup_logging() -> tuple[logging.Logger, Path]:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"analyse_{ts}.log"

    logger = logging.getLogger("analyse")
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


def parse_analysis_json(text: str) -> dict:
    """
    Parse Claude's response into a dict.

    The prompt explicitly forbids markdown fences and preamble, but be
    defensive: strip a leading ```json fence if Claude added one anyway,
    and trim whitespace.
    """
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return json.loads(s)


def call_claude_with_retry(
    client: anthropic.Anthropic,
    model: str,
    system: str,
    user_prompt: str,
    max_tokens: int,
    logger: logging.Logger,
    max_retries: int = 3,
) -> str:
    """
    Call the Anthropic Messages API and return the concatenated text content.

    Retries up to `max_retries` times on transient errors with exponential
    backoff (1s, 2s, 4s). Raises on the final failure or on any
    non-transient error (auth, malformed request).
    """
    delay = 1.0
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
            )
            # Response content is a list of content blocks; concatenate text.
            text_parts = []
            for block in resp.content:
                t = getattr(block, "text", None)
                if t:
                    text_parts.append(t)
            return "".join(text_parts)
        except RETRY_ERRORS as exc:
            last_exc = exc
            if attempt == max_retries:
                logger.error(
                    "API failed after %d attempts: %s", max_retries, exc,
                )
                raise
            logger.warning(
                "API error attempt %d/%d (%s); retrying in %.1fs",
                attempt, max_retries, type(exc).__name__, delay,
            )
            time.sleep(delay)
            delay *= 2
    # Should never reach here.
    raise RuntimeError(f"unreachable; last error: {last_exc}")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main() -> int:
    logger, log_path = setup_logging()
    logger.info("=" * 70)
    logger.info("TDDG Bulletin - analyse run starting")
    logger.info("=" * 70)

    # --- load env + key -----------------------------------------------------
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error(
            "ANTHROPIC_API_KEY missing. Copy .env.example to .env and add "
            "your key (https://console.anthropic.com/settings/keys)."
        )
        return 2

    # --- locate input -------------------------------------------------------
    filtered_dir = latest_filtered_dir()
    if filtered_dir is None:
        logger.error(
            "No data/filtered/{date}/articles.json found. "
            "Run scripts/filter.py first."
        )
        return 2

    in_path = filtered_dir / "articles.json"
    with open(in_path, "r", encoding="utf-8") as f:
        articles = json.load(f)
    logger.info("Input: %s  (%d articles)", in_path, len(articles))

    if not articles:
        logger.warning("Input has zero articles; nothing to analyse.")
        date_label = filtered_dir.name
        out_dir = ANALYSED_DIR / date_label
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "articles.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump([], f)
        logger.info("Wrote empty %s", out_path)
        return 0

    # --- load config + prompts ----------------------------------------------
    settings = load_yaml(CONFIG_DIR / "settings.yaml")
    branches_yaml_text = load_text(CONFIG_DIR / "branches.yaml")
    system_prompt = load_text(PROMPTS_DIR / "system.md")
    per_article_tpl = Template(load_text(PROMPTS_DIR / "per_article.md"))

    model = (settings.get("claude_model_analyse")
             or settings.get("claude_model")
             or DEFAULT_MODEL)
    max_tokens = settings.get(
        "max_output_tokens_per_article", DEFAULT_MAX_OUTPUT_TOKENS,
    )
    threshold = settings.get(
        "relevance_threshold", DEFAULT_RELEVANCE_THRESHOLD,
    )
    max_per_run = settings.get(
        "max_articles_per_run", DEFAULT_MAX_ARTICLES_PER_RUN,
    )

    # --- cost guard ---------------------------------------------------------
    if len(articles) > max_per_run:
        logger.error(
            "ABORT: %d articles exceeds max_articles_per_run=%d.\n"
            "  Tighten the filter before re-running this stage:\n"
            "    - raise PASS_THRESHOLD in scripts/filter.py (currently 0.20)\n"
            "    - add more phrases to config/exclusions.yaml\n"
            "    - disable noisy sources (set enabled: false in config/sources.yaml)\n"
            "  Then re-run scripts/filter.py and scripts/analyse.py.",
            len(articles), max_per_run,
        )
        return 3

    logger.info(
        "Model: %s   max_tokens/article: %d   relevance_threshold: %.2f",
        model, max_tokens, threshold,
    )

    # --- the loop -----------------------------------------------------------
    try:
        client = anthropic.Anthropic(api_key=api_key)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Failed to initialise Anthropic client: %s: %s",
            type(exc).__name__, exc,
        )
        return 4

    analysed: list[dict] = []
    api_errors = 0
    parse_errors = 0
    drop_irrelevant = 0
    drop_low_score = 0

    for i, art in enumerate(articles, 1):
        # Outer try/except captures ANY error per article (including TypeError,
        # ValueError, Jinja errors) and logs the full traceback to the file
        # so failures don't vanish into stdout-only tracebacks.
        try:
            if not isinstance(art, dict):
                logger.error(
                    "[%d/%d] skipping non-dict article record: type=%s value=%r",
                    i, len(articles), type(art).__name__, str(art)[:120],
                )
                continue

            article_id = art.get("id", "?")
            title = art.get("title", "") or ""
            summary = (art.get("summary") or "")[:MAX_SUMMARY_CHARS]

            prompt = per_article_tpl.render(
                article_title=title,
                article_source=art.get("source_name", "") or "",
                article_url=art.get("url", "") or "",
                article_date=art.get("published_date", "") or "",
                article_summary=summary,
                branches_yaml=branches_yaml_text,
            )
        except Exception as exc:  # noqa: BLE001
            api_errors += 1
            logger.exception(
                "[%d/%d] %s: pre-API error (%s): %s",
                i, len(articles), art.get("id", "?") if isinstance(art, dict) else "?",
                type(exc).__name__, exc,
            )
            continue

        # --- call API ------------------------------------------------------
        try:
            text = call_claude_with_retry(
                client, model, system_prompt, prompt, max_tokens, logger,
            )
        except Exception as exc:  # noqa: BLE001
            api_errors += 1
            logger.exception(
                "[%d/%d] %s: API failed (%s): %s",
                i, len(articles), article_id, type(exc).__name__, exc,
            )
            art["analysis"] = {"parse_error": True, "error": f"api: {exc}"}
            analysed.append(art)
            continue

        # --- parse JSON ----------------------------------------------------
        try:
            analysis = parse_analysis_json(text)
        except (json.JSONDecodeError, TypeError) as exc:
            parse_errors += 1
            logger.error(
                "[%d/%d] %s: JSON parse failed (%s): %s",
                i, len(articles), article_id, type(exc).__name__, exc,
            )
            logger.error("  raw response (first 500 chars): %s",
                         (text or "")[:500])
            art["analysis"] = {
                "parse_error": True,
                "error": f"parse: {exc}",
                "raw_excerpt": (text or "")[:1000],
            }
            analysed.append(art)
            continue

        art["analysis"] = analysis

        # --- log per-article decision -------------------------------------
        try:
            if not isinstance(analysis, dict):
                raise TypeError(
                    f"expected analysis JSON object, got {type(analysis).__name__}"
                )
            relevant = bool(analysis.get("relevant", False))
            try:
                score = float(analysis.get("relevance_score", 0) or 0)
            except (TypeError, ValueError):
                score = 0.0
            conf = analysis.get("confidence", "low")
            top_branch_id = None
            if analysis.get("branch_relevance"):
                try:
                    top = max(
                        analysis["branch_relevance"],
                        key=lambda b: float(b.get("score", 0) or 0),
                    )
                    top_branch_id = top.get("branch_id")
                except (ValueError, TypeError, AttributeError):
                    top_branch_id = None
        except Exception as exc:  # noqa: BLE001
            parse_errors += 1
            logger.exception(
                "[%d/%d] %s: post-parse error (%s): %s",
                i, len(articles), article_id, type(exc).__name__, exc,
            )
            art["analysis"] = {
                "parse_error": True,
                "error": f"post-parse: {exc}",
                "raw_excerpt": (text or "")[:500],
            }
            analysed.append(art)
            continue

        logger.info(
            "[%d/%d] %s  rel=%s score=%.2f branch=%s conf=%s  %s",
            i, len(articles), article_id, relevant, score,
            top_branch_id, conf, title[:80],
        )

        if not relevant:
            drop_irrelevant += 1
            continue
        if score < threshold:
            drop_low_score += 1
            continue

        analysed.append(art)

    # --- write survivors ----------------------------------------------------
    survivors = [
        a for a in analysed
        if not a.get("analysis", {}).get("parse_error")
        and a.get("analysis", {}).get("relevant")
        and float(a.get("analysis", {}).get("relevance_score", 0) or 0) >= threshold
    ]

    date_label = filtered_dir.name
    out_dir = ANALYSED_DIR / date_label
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "articles.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(survivors, f, indent=2, ensure_ascii=False)

    # --- summary -----------------------------------------------------------
    logger.info("=" * 70)
    logger.info("Summary")
    logger.info("-" * 70)
    logger.info("Input articles:           %d", len(articles))
    logger.info("API errors (skipped):     %d", api_errors)
    logger.info("Parse errors (skipped):   %d", parse_errors)
    logger.info("Dropped (irrelevant):     %d", drop_irrelevant)
    logger.info("Dropped (score < %.2f):    %d", threshold, drop_low_score)
    logger.info("Kept (survivors):         %d", len(survivors))
    logger.info("-" * 70)
    logger.info("Output: %s", out_path)
    logger.info("Log:    %s", log_path)

    # Branch distribution of survivors
    if survivors:
        by_branch: dict[int, int] = {}
        for a in survivors:
            br = a.get("analysis", {}).get("branch_relevance") or []
            if br:
                try:
                    top = max(br, key=lambda b: float(b.get("score", 0) or 0))
                    bid = top.get("branch_id")
                    if bid is not None:
                        by_branch[bid] = by_branch.get(bid, 0) + 1
                except (ValueError, TypeError):
                    pass
        logger.info("-" * 70)
        logger.info("Survivors by top branch:")
        for bid in sorted(by_branch):
            logger.info("  branch %d  %d", bid, by_branch[bid])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
