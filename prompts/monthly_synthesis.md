# Monthly synthesis prompt

> Used by `scripts/analyse.py` / `scripts/render.py` (Stage 5+) once per
> month after every article has been analysed. Produces the structured
> bulletin object the website template renders. Pair with `system.md`.

## Template variables

| Variable                | Source                                                |
|-------------------------|-------------------------------------------------------|
| `{{ month_label }}`     | `YYYY-MM` (e.g. `2026-05`)                            |
| `{{ branches_yaml }}`   | full text of `config/branches.yaml`                   |
| `{{ analysed_articles_json }}` | the array of per-article analysis results from `data/analysed/{date}/articles.json`, including the article metadata fields each record was built from |

## Prompt body

You are producing the TDDG Training Futures Bulletin for **{{ month_label }}**.

The corpus below is the full set of articles that passed the relevance
filter and the per-article analysis for this month. Each entry contains
both the original article metadata and the analyst's per-article output.

Apply the Key Design Rule to the bulletin as a whole. The bulletin
exists to tell six branch chiefs what changed this month that they
should care about, and what — if anything — they should do about it.

---

**Branch definitions**

```yaml
{{ branches_yaml }}
```

---

**Analysed corpus** (JSON array of per-article analyses with their source
metadata):

```json
{{ analysed_articles_json }}
```

---

## Your task

Produce a single JSON object with the seven bulletin sections. The
website template renders the JSON; you are not writing prose for direct
reader display. Be specific. Cite article IDs when you make a claim that
came from the corpus.

### Rules per section

**1. BLUF** — the bottom line for a branch chief who has 90 seconds.
A `headline` line (≤120 chars) and a `summary` of 3-5 sentences. State
the month's single most important development first. If the month had no
significant developments, say so directly. Do not pad.

**2. Branch Relevance Dashboard** — one entry per branch, all six included
even when quiet. `article_count` is the number of corpus articles where
this branch's score ≥ 0.3. `intensity` is `"low"` (0-2 articles),
`"medium"` (3-6), or `"high"` (7+). `one_line` is a single declarative
sentence stating the month's headline for that branch, or "Quiet month"
if no items scored above 0.3.

**3. Key Developments** — 3 to 7 entries. These are the developments a
branch chief would want to know about first. Each entry has `what`
(2-3 sentences, factual), `so_what` (1-2 sentences on Army implication),
and links back to its supporting article IDs.

**4. Branch-Specific Sections** — one section per branch *that has at
least one article scoring ≥ 0.3 for it*. Branches with no qualifying
articles are omitted from this section (but still appear in the dashboard
with `intensity: "low"`). Each branch section has a 1-paragraph `summary`
and an `items` list of the 2-5 most important articles for that branch.

**5. Cross-Cutting Themes** — surface themes that span multiple branches.
**Only include a theme if at least 3 articles in the corpus share it.**
If fewer than 3, omit the theme entirely; do not stretch. Each theme has
a short name, a 2-3 sentence description, and the article IDs that
support it.

**6. Watch Areas** — forward-looking signals to monitor in the next 1-3
months. Not recap. Not summary. These are things that, if they develop
further, would change a branch's posture. Each entry has the signal,
why it matters, and 1-3 specific indicators that would tell us the
signal is real.

**7. Source References** — flat list of every article cited anywhere
above, in the order first cited, with `article_id`, `title`, `source`,
`url`, `date`.

## Required output (JSON only)

Emit exactly this object. No preamble. No markdown fence. No trailing
commentary.

```json
{
  "month": "{{ month_label }}",
  "bluf": {
    "headline": "",
    "summary": ""
  },
  "branch_relevance_dashboard": [
    {
      "branch_id": 1,
      "branch_name": "",
      "article_count": 0,
      "intensity": "low",
      "one_line": ""
    }
  ],
  "key_developments": [
    {
      "title": "",
      "what": "",
      "so_what": "",
      "branches": [1],
      "supporting_article_ids": [""]
    }
  ],
  "branch_sections": [
    {
      "branch_id": 1,
      "branch_name": "",
      "summary": "",
      "items": [
        {
          "title": "",
          "implication": "",
          "supporting_article_ids": [""]
        }
      ]
    }
  ],
  "cross_cutting_themes": [
    {
      "theme": "",
      "description": "",
      "supporting_article_ids": ["", "", ""]
    }
  ],
  "watch_areas": [
    {
      "signal": "",
      "why": "",
      "indicators": [""]
    }
  ],
  "source_references": [
    {
      "article_id": "",
      "title": "",
      "source": "",
      "url": "",
      "date": ""
    }
  ]
}
```

## Reminders

- Honour the anti-pattern list from the system prompt. A bulletin written
  in marketing voice fails its purpose.
- If a section is genuinely empty for this month (no themes meeting the
  ≥3-article threshold, no Key Developments worth naming), emit an empty
  array rather than inventing content.
- Every claim in the bulletin must trace back to one or more article IDs
  in the corpus. If you cannot cite, do not claim.
