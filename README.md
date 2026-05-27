# TDDG Monthly Bulletin Agent

A monthly bulletin agent for HQ TRADOC TDDG (Training Doctrine Development Group). The agent ingests open-source signals on training, learning, and human-performance futures from a configured list of feeds and pages, filters them for relevance, uses Claude to analyse and synthesise the material, and produces a monthly bulletin published as a static website via GitHub Pages.

> **Note:** This description is a placeholder. Replace this paragraph with the consolidated text from sections 1, 4, 5, 14, and 15 of the requirements document.

## Folder map

```
tddg-bulletin-agent/
├── config/              # YAML/JSON settings (run cadence, keywords, model params)
├── sources/             # Source registry: feeds, sites, RSS, watchlists
├── data/
│   ├── raw/             # Untouched fetched items (gitignored)
│   ├── filtered/        # Items passing the relevance filter (gitignored)
│   └── analysed/        # Claude-tagged + summarised items
├── bulletins/
│   ├── drafts/          # Working drafts of each month's bulletin
│   └── published/       # Final committed monthly bulletins
├── scripts/             # Python pipeline (fetch, filter, analyse, render)
├── prompts/             # Prompt templates passed to the Claude API
├── website/
│   ├── archive/         # Past bulletins as HTML
│   └── assets/          # CSS, images, shared static files
├── logs/                # Run logs (gitignored)
├── .gitignore
├── README.md
└── requirements.txt
```

## How to run monthly

Activate the virtual environment first:

```powershell
cd "C:\Users\jorda\OneDrive\Documents\TDDG Monthly Bulletin"
.\.venv\Scripts\Activate.ps1
```

Then run the pipeline (only Stage 2 is implemented so far — collection):

```powershell
# Stage 2: collect raw articles into data/raw/{YYYY-MM-DD}/articles.json
python scripts/collect.py
```

Future stages (not yet built):

```powershell
# Stage 3: filter for relevance using a cheap Claude model
python scripts/filter.py

# Stage 4: analyse + synthesise with Claude
python scripts/analyse.py

# Stage 5: render bulletin draft into bulletins/drafts/
python scripts/render.py

# Stage 6: publish to bulletins/published/ + website/archive/, push to GitHub
python scripts/publish.py
```

After publishing, `git push` updates the GitHub Pages site.

## Environment

- Python 3.11+
- Windows (PowerShell)
- Virtual environment: `.venv/` (created at bootstrap)
- Dependencies: see `requirements.txt`

## Local setup (Windows)

```powershell
cd "C:\Users\jorda\OneDrive\Documents\TDDG Monthly Bulletin"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Environment variables

Secrets and per-machine settings live in a local `.env` file at the project
root. `.env` is gitignored; a committed `.env.example` shows what variables
are required.

To set up:

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill in `ANTHROPIC_API_KEY` (get one at
[console.anthropic.com](https://console.anthropic.com/settings/keys)) and
adjust the `USER_AGENT` contact email so source-site operators can reach
you if there is a problem.

## Configuration

All run-time settings live in `config/`:

| File              | Purpose                                                |
|-------------------|--------------------------------------------------------|
| `sources.yaml`    | The open-source feeds and sites monitored each month   |
| `branches.yaml`   | The six TDDG branches: focus areas, questions, keywords |
| `settings.yaml`   | Tunables: lookback, thresholds, Claude model selection |

Edit these files to add/remove sources, retune the relevance filter, or
switch models. Scripts re-read them at the start of every run.
