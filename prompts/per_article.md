# Per-article analysis prompt

> Used by `scripts/analyse.py` (Stage 5+) to run one Claude call per
> filtered article. Jinja-rendered before sending. Pair with `system.md`.

## Template variables

The script substitutes these before sending the prompt:

| Variable             | Source                                     |
|----------------------|--------------------------------------------|
| `{{ article_title }}`   | article record `title` field             |
| `{{ article_source }}`  | article record `source_name`             |
| `{{ article_url }}`     | article record `url`                     |
| `{{ article_date }}`    | article record `published_date`          |
| `{{ article_summary }}` | article record `summary` (≤800 chars)    |
| `{{ branches_yaml }}`   | full text of `config/branches.yaml` so the analyst sees branch names, focus areas, core questions, and the keyword vocabulary |

## Prompt body

You are analysing one article for the TDDG monthly bulletin. Apply the
Key Design Rule. Read the article metadata, then judge.

---

**Article**

- Title: {{ article_title }}
- Source: {{ article_source }}
- URL: {{ article_url }}
- Published: {{ article_date }}

**Summary as published by the source** (this is what the feed exposed; it
may be partial or just the lede):

{{ article_summary }}

---

**The six TDDG branches** (each branch has focus areas, a core question,
and a keyword vocabulary):

```yaml
{{ branches_yaml }}
```

---

## Your task

1. Decide whether this article plausibly answers the Key Design Rule:
   *How does this development affect the way the Army should train, learn,
   lead, simulate, analyse, or develop doctrine?* If not, set
   `relevant: false` and leave the editorial fields empty.

2. If relevant: produce the analysis fields below. Be specific about what
   the Army should *do, study, decide, or watch* as a result. Avoid the
   banned phrasings listed in the system prompt.

3. Score `relevance_score` on the inclusive range 0.0 to 1.0. Anchor
   roughly:
   - 0.9-1.0: directly actionable for one or more branches now
   - 0.7-0.9: warrants formal analysis or a study
   - 0.5-0.7: worth tracking; a branch chief should be aware
   - 0.3-0.5: edge of relevance; cite only if it strengthens a theme
   - below 0.3: should probably have been filtered earlier; set
     `relevant: false`

4. `branch_relevance` lists only the branches where score ≥ 0.3. A single
   development can be relevant to multiple branches; if so, score each.

5. `watch_signals` is a short list (1-5) of compact tags that summarise
   what to monitor as a follow-on. Lowercase, hyphenated, e.g.
   "xapi-adoption", "ste-cost-curve", "nco-pme-reform".

6. `confidence` reflects how confident you are in your judgement given
   the source quality and the amount of detail in the summary. Vendor
   announcements with no independent corroboration → "low". Peer-reviewed
   or primary government source with detail → "high".

## Required output (JSON only)

Emit exactly this shape. No preamble. No markdown fence. No trailing
commentary.

```json
{
  "relevant": true,
  "relevance_score": 0.0,
  "summary": "2-3 sentences. Factual. What the source actually says.",
  "why_it_matters": "1-2 sentences linking this to Army training transformation.",
  "training_implication": "What this means for how the Army trains, learns, leads, simulates, analyses, or develops doctrine. Specific.",
  "branch_relevance": [
    {"branch_id": 1, "score": 0.0, "rationale": "one sentence"}
  ],
  "possible_implementation": "A concrete adoption or experimentation idea. No vapourware.",
  "follow_up_study": "A short suggestion for a study, pilot, wargame, or RFI.",
  "watch_signals": ["short", "tags"],
  "confidence": "low"
}
```

When `relevant` is `false`, use this shape instead and stop:

```json
{
  "relevant": false,
  "relevance_score": 0.0,
  "summary": "One sentence on what the article is about.",
  "why_it_matters": "",
  "training_implication": "",
  "branch_relevance": [],
  "possible_implementation": "",
  "follow_up_study": "",
  "watch_signals": [],
  "confidence": "low"
}
```
