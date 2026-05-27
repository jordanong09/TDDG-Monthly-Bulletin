# Monthly Bulletin Checklist

The canonical end-to-end procedure for producing one month's bulletin.
This document is the single source of truth for the monthly workflow.

Estimated time: 30-60 minutes total, of which ~10 minutes is the automated
pipeline and the rest is reading and reviewing the draft.

---

## Before you start

- You have the `.venv` set up (run `setup.ps1` once if not)
- `.env` exists with a valid `ANTHROPIC_API_KEY`
- You have a working internet connection (the collect stage fetches feeds)
- Today is end-of-month or early next month (the natural cadence)

---

## Step 1 — run the pipeline (~5-15 min, depends on article count)

Double-click `run_monthly.bat` in the project root.

A console window opens and you see five stages run in order:

1. `collect` — fetches new items from every enabled source in `config/sources.yaml`
2. `filter` — keyword + tripwire + exclusion pre-filter
3. `analyse` — per-article Claude analysis (the slow stage, ~5-10s per article)
4. `synthesise` — monthly synthesis + sanity-check pass
5. `render` — produces the draft HTML

The window stays open at the end. The last lines show:

- Per-stage durations
- Article counts at each stage
- Path to the draft HTML (something like
  `bulletins\drafts\2026-05\index.html`)

If any stage fails, the orchestrator stops and prints the log path. Read
the log, fix the issue (often a dead RSS feed or a stale CSS selector),
and rerun. Use `--skip-collect` to avoid refetching:

```
run_monthly.bat --skip-collect
```

To target a specific month (default is the current calendar month):

```
run_monthly.bat --month 2026-04
```

---

## Step 2 — review the draft (~15-30 min)

Open the draft HTML in a browser. It carries a "DRAFT — NOT FOR
DISTRIBUTION" watermark and a sanity-check panel at the bottom listing
any issues the LLM flagged in its own draft.

Read it as the recipient. The Key Design Rule is your scoring criterion:

> Every output must answer: *How does this development affect the way
> the Army should train, learn, lead, simulate, analyse, or develop
> doctrine?*

Specific things to check:

- **BLUF** — does the headline tell a branch chief something they would
  act on in the next 30 days? If it reads like a "this month in defence"
  recap, the prompt or the article set is wrong.
- **Key Developments** — does each entry have a concrete `so what`? An
  entry that ends in "should consider" or "may want to examine" is
  filler; flag it.
- **Branch sections** — are the implications specific to that branch, or
  generic? A branch section that names a specific document, programme, or
  decision is useful. One that paraphrases the article isn't.
- **Cross-Cutting Themes** — do the supporting article IDs really span
  the theme, or are 2 of 3 about the same thing? The prompt requires ≥3
  articles per theme; check it.
- **Watch Areas** — forward-looking with named indicators, or recap?
  Recap doesn't belong here.
- **Sanity-check panel** — read every issue. The LLM flags the obvious
  problems but it doesn't catch them all. Use them as a starting point.

If the draft is poor enough that wording fixes won't help, the root
cause is usually one of:

1. **Filter too permissive** — junk reached the analyse stage. Tighten
   `config/exclusions.yaml` or raise `PASS_THRESHOLD` in `filter.py`,
   then `run_monthly.bat --skip-collect`.
2. **Source list wrong** — too many marketing feeds, not enough primary
   sources. Edit `config/sources.yaml` and rerun.
3. **Prompts drifting** — the analyses sound like generic defence
   commentary, not training-futures analysis. Sharpen
   `prompts/system.md` (the Key Design Rule section) or
   `prompts/per_article.md`, then rerun `--skip-collect`.

It is acceptable to iterate two or three times before approving. Cost
on a 30-article corpus is roughly USD $1-3 per re-run on Opus.

---

## Step 3 — approve

Once the draft is good enough to publish, create the approval file:

```
notepad bulletins\drafts\<YYYY-MM>\APPROVED.md
```

Use this template (paste and fill in):

```markdown
# Approved

Reviewer: <your name>
Date: YYYY-MM-DD
Notes: Reviewed BLUF, branch sections, cross-cutting themes, watch
       areas, and sanity-check findings. <Add any caveats, e.g.
       "Dropped a vendor-press-release item from branch 2 section
       in manual edit before approval.">
```

Save and close.

Without `APPROVED.md`, `promote.py` refuses to run. There is no way to
auto-publish.

---

## Step 4 — promote

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\promote.py --month YYYY-MM
```

This:

- Copies the draft into `bulletins\published\YYYY-MM\`
- Re-renders the bulletin HTML without the DRAFT watermark
- Mirrors it to `website\archive\YYYY-MM\index.html`
- Regenerates `website\index.html` with the new bulletin as current
- Regenerates `website\archive\index.html` with the updated history

Open `website\index.html` and confirm the new bulletin is at the top.

---

## Step 5 — push to GitHub (~1 min)

```powershell
git add bulletins/published website/index.html website/archive
git commit -m "publish: YYYY-MM bulletin"
git push
```

GitHub Pages picks up the change within a minute or two and serves the
site at your configured domain. Confirm by opening the live URL in a
private/incognito browser.

---

## Troubleshooting

| Symptom                                          | Likely cause                                       | Fix                                                           |
|--------------------------------------------------|----------------------------------------------------|---------------------------------------------------------------|
| `collect` reports many 4xx errors                | RSS URL changed since `sources.yaml` was written   | Update the URL in `config/sources.yaml`; or set `enabled: false` |
| `collect` reports many "no selector" warnings    | HTML source needs a selector                       | Inspect the page DOM, fill in `config/html_selectors.yaml`    |
| `analyse` aborts with `ABORT: N exceeds max_articles_per_run` | Pre-filter is too loose this month       | Raise `PASS_THRESHOLD` in `filter.py` or tighten `exclusions.yaml`; rerun `--skip-collect` |
| `analyse` reports many parse errors              | Prompt or model issue; Claude returned non-JSON    | Check `prompts/per_article.md` for syntax that confuses the model; check `logs/analyse_*.log` for the raw response |
| `synthesise` fails with `ANTHROPIC_API_KEY missing` | `.env` not configured                          | Copy `.env.example` to `.env`, add your key                   |
| Sanity-check reports `ready_for_review: false`   | LLM flagged structural or hype problems            | Read the issues, edit `prompts/monthly_synthesis.md` if recurring, rerun synthesis (use `--month` to target) |
| `promote.py` refuses to run                      | `APPROVED.md` is missing                           | Create it (see Step 3)                                        |
| Landing page doesn't update after promotion      | Pushed wrong files                                 | Verify `website/index.html` was committed and pushed          |

---

## Quick reference

```
.\run_monthly.bat                       # full pipeline, current month
.\run_monthly.bat --skip-collect        # re-analyse without re-fetching
.\run_monthly.bat --month 2026-04       # target a specific month

python scripts\promote.py --month 2026-04
```
