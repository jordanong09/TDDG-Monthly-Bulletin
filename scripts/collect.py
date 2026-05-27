"""
TDDG Monthly Bulletin - Collection script.

Reads config/sources.yaml + config/settings.yaml, pulls entries from every
`enabled: true` source (RSS via feedparser, HTML via requests+BeautifulSoup
with per-source selectors from config/html_selectors.yaml), date-filters,
dedupes, and writes the result to data/raw/{YYYY-MM-DD}/articles.json.

No filtering by relevance, no LLM. Just retrieval + metadata capture.

Run from project root:
    python scripts/collect.py

Idempotent: re-running on the same day overwrites that day's articles.json.
Polite: honours robots.txt, delays between requests per-host, sets a
truthful User-Agent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data" / "raw"
LOGS_DIR = PROJECT_ROOT / "logs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def hash_url(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def parse_date_safe(value) -> str | None:
    """Parse a date string and return a UTC ISO 8601 string, or None on failure."""
    if not value:
        return None
    try:
        dt = date_parser.parse(value)
    except (ValueError, TypeError, date_parser.ParserError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def is_recent(iso_dt: str | None, lookback_days: int) -> bool:
    """Keep the item if we have no date (let the analyst sort it out) or it's within lookback."""
    if not iso_dt:
        return True
    try:
        dt = date_parser.isoparse(iso_dt)
    except Exception:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    return dt >= cutoff


def clean_html_to_text(raw: str, max_chars: int = 800) -> str:
    if not raw:
        return ""
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    return text[:max_chars]


# ---------------------------------------------------------------------------
# Politeness
# ---------------------------------------------------------------------------
class PolitenessGate:
    """Per-host delay enforcement + robots.txt checks."""

    def __init__(self, delay_seconds: float, user_agent: str):
        self.delay = delay_seconds
        self.user_agent = user_agent
        self._last_request_at: dict[str, float] = {}
        self._robots_cache: dict[str, robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.netloc
        if host not in self._robots_cache:
            rp = robotparser.RobotFileParser()
            rp.set_url(f"{parsed.scheme}://{host}/robots.txt")
            try:
                rp.read()
                self._robots_cache[host] = rp
            except Exception:
                # If robots.txt can't be fetched, default to allowing.
                # This matches what most polite crawlers do.
                self._robots_cache[host] = None
        rp = self._robots_cache[host]
        if rp is None:
            return True
        try:
            return rp.can_fetch(self.user_agent, url)
        except Exception:
            return True

    def wait(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last_request_at.get(host, 0.0)
        elapsed = time.time() - last
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request_at[host] = time.time()


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------
def collect_rss(source: dict, gate: PolitenessGate, settings: dict, logger: logging.Logger) -> list[dict]:
    url = source["url"]
    if not gate.allowed(url):
        logger.warning("[%s] robots.txt disallows fetch - skipping", source["id"])
        return []

    gate.wait(url)
    headers = {"User-Agent": gate.user_agent}
    timeout = settings.get("http_timeout_seconds", 30)

    # feedparser uses urllib internally; we fetch with requests so we can
    # apply timeout + custom UA reliably, then parse the bytes.
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)

    if feed.bozo and not feed.entries:
        raise RuntimeError(f"feed parse failed: {feed.bozo_exception}")

    lookback = settings.get("lookback_days", 35)
    cap = settings.get("max_articles_per_source", 25)

    items: list[dict] = []
    for entry in feed.entries:
        link = (entry.get("link") or "").strip()
        if not link:
            continue
        title = (entry.get("title") or "").strip()
        published = entry.get("published") or entry.get("updated") or entry.get("created")
        published_iso = parse_date_safe(published)
        if not is_recent(published_iso, lookback):
            continue
        summary_raw = entry.get("summary") or entry.get("description") or ""
        # Some feeds use a content list.
        if not summary_raw and entry.get("content"):
            try:
                summary_raw = entry["content"][0].get("value", "")
            except (KeyError, IndexError, AttributeError):
                summary_raw = ""

        items.append(
            {
                "id": hash_url(link),
                "title": title,
                "url": link,
                "source_id": source["id"],
                "source_name": source["name"],
                "published_date": published_iso,
                "summary": clean_html_to_text(summary_raw),
                "categories": source.get("category", []),
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if len(items) >= cap:
            break

    return items


def collect_html(
    source: dict,
    selector: dict,
    gate: PolitenessGate,
    settings: dict,
    logger: logging.Logger,
) -> list[dict]:
    url = source["url"]
    if not gate.allowed(url):
        logger.warning("[%s] robots.txt disallows fetch - skipping", source["id"])
        return []

    link_sel = selector.get("article_link")
    title_sel = selector.get("title")
    date_sel = selector.get("published_date")

    if not link_sel:
        raise RuntimeError("selector missing 'article_link'")

    gate.wait(url)
    headers = {"User-Agent": gate.user_agent}
    timeout = settings.get("http_timeout_seconds", 30)
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    lookback = settings.get("lookback_days", 35)
    cap = settings.get("max_articles_per_source", 25)

    items: list[dict] = []
    seen_urls: set[str] = set()

    for el in soup.select(link_sel):
        href = (el.get("href") or "").strip()
        if not href:
            continue
        full_url = urljoin(url, href)
        if not full_url.startswith(("http://", "https://")):
            continue
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        # Title: prefer a relative title selector inside the link's parent,
        # then the link's own text content.
        title = ""
        if title_sel:
            container = el.find_parent() or soup
            t = container.select_one(title_sel)
            if t:
                title = t.get_text(strip=True)
        if not title:
            title = el.get_text(strip=True)
        if not title:
            # No title we can parse - skip the row, don't fabricate.
            continue

        published_iso = None
        if date_sel:
            container = el.find_parent() or soup
            d = container.select_one(date_sel)
            if d:
                # Prefer machine-readable datetime attribute if present.
                dt_attr = d.get("datetime") or d.get_text(strip=True)
                published_iso = parse_date_safe(dt_attr)

        if not is_recent(published_iso, lookback):
            continue

        items.append(
            {
                "id": hash_url(full_url),
                "title": title,
                "url": full_url,
                "source_id": source["id"],
                "source_name": source["name"],
                "published_date": published_iso,
                "summary": "",
                "categories": source.get("category", []),
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if len(items) >= cap:
            break

    return items


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------
def dedupe(articles: list[dict], threshold: float, logger: logging.Logger) -> list[dict]:
    """Drop articles with duplicate URL hash, then drop near-duplicate titles."""
    by_id: dict[str, dict] = {}
    for a in articles:
        if a["id"] not in by_id:
            by_id[a["id"]] = a
        else:
            logger.debug("dropping URL-dup id=%s url=%s", a["id"], a["url"])

    out: list[dict] = []
    for a in by_id.values():
        is_dup = False
        for existing in out:
            ratio = SequenceMatcher(None, a["title"].lower(), existing["title"].lower()).ratio()
            if ratio >= threshold:
                logger.debug(
                    "dropping title-dup '%s' ~ '%s' (ratio=%.2f) from %s",
                    a["title"], existing["title"], ratio, a["source_id"],
                )
                is_dup = True
                break
        if not is_dup:
            out.append(a)
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def setup_logging() -> tuple[logging.Logger, Path]:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"collect_{timestamp}.log"

    logger = logging.getLogger("collect")
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


def main() -> int:
    logger, log_path = setup_logging()
    logger.info("=" * 70)
    logger.info("TDDG Bulletin - collect run starting")
    logger.info("=" * 70)

    load_dotenv(PROJECT_ROOT / ".env")
    user_agent = os.getenv("USER_AGENT", "TDDG-Bulletin-Agent/0.1 (research)")

    sources_cfg = load_yaml(CONFIG_DIR / "sources.yaml")
    settings = load_yaml(CONFIG_DIR / "settings.yaml")
    selectors_path = CONFIG_DIR / "html_selectors.yaml"
    selectors_cfg = load_yaml(selectors_path) if selectors_path.exists() else {}
    selectors = (selectors_cfg or {}).get("selectors", {}) or {}

    sources = sources_cfg.get("sources", [])
    enabled = [s for s in sources if s.get("enabled")]
    logger.info("Loaded %d sources, %d enabled", len(sources), len(enabled))
    logger.info("User-Agent: %s", user_agent)

    gate = PolitenessGate(
        delay_seconds=settings.get("fetch_delay_seconds", 1.5),
        user_agent=user_agent,
    )

    all_articles: list[dict] = []
    per_source_counts: dict[str, int] = {}
    errors: list[tuple[str, str]] = []

    for src in enabled:
        sid = src["id"]
        try:
            if src["type"] == "rss":
                items = collect_rss(src, gate, settings, logger)
            elif src["type"] == "html":
                sel = selectors.get(sid)
                if not sel or not any(sel.values()):
                    logger.warning("[%s] HTML source has no selector configured - skipping", sid)
                    per_source_counts[sid] = 0
                    continue
                items = collect_html(src, sel, gate, settings, logger)
            else:
                logger.warning("[%s] unknown type '%s' - skipping", sid, src.get("type"))
                per_source_counts[sid] = 0
                continue

            per_source_counts[sid] = len(items)
            all_articles.extend(items)
            logger.info("[%s] kept %d items", sid, len(items))
        except Exception as exc:  # noqa: BLE001 - we want to keep the run alive
            errors.append((sid, str(exc)))
            per_source_counts[sid] = 0
            logger.error("[%s] failed: %s", sid, exc)

    threshold = settings.get("dedupe_similarity_threshold", 0.85)
    pre_count = len(all_articles)
    deduped = dedupe(all_articles, threshold, logger)
    logger.info("Dedupe: %d -> %d", pre_count, len(deduped))

    today = datetime.now().strftime("%Y-%m-%d")
    out_dir = DATA_DIR / today
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "articles.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(deduped, f, indent=2, ensure_ascii=False)
    out_size = out_path.stat().st_size

    logger.info("=" * 70)
    logger.info("Per-source counts:")
    for sid in sorted(per_source_counts):
        logger.info("  %-32s %d", sid, per_source_counts[sid])
    logger.info("-" * 70)
    logger.info("Sources with errors: %d", len(errors))
    for sid, err in errors:
        logger.info("  %-32s %s", sid, err)
    logger.info("-" * 70)
    logger.info("Total articles after dedupe: %d", len(deduped))
    logger.info("Output: %s  (%d bytes)", out_path, out_size)
    logger.info("Log:    %s", log_path)
    logger.info("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
