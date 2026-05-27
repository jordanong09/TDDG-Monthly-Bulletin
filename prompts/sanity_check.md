# Sanity-check prompt

> Used by `scripts/analyse.py` / `scripts/render.py` (Stage 5+) as a final
> pass over the draft bulletin JSON produced by `monthly_synthesis.md`.
> The goal is to catch the failure modes a hurried writer commits and
> flag them before the bulletin reaches a human reviewer. Pair with
> `system.md`.

## Template variables

| Variable                  | Source                                          |
|---------------------------|-------------------------------------------------|
| `{{ bulletin_json }}`     | the draft bulletin JSON from the synthesis step |
| `{{ analysed_articles_json }}` | the same per-article analysis corpus that fed synthesis, used to verify claims trace back to articles |

## Prompt body

You are reviewing a draft TDDG bulletin before it goes to a human
reviewer. You are looking for the failure modes below. You are not
rewriting the bulletin; you are listing what is wrong with it so the
writer can fix it.

---

**Draft bulletin**

```json
{{ bulletin_json }}
```

---

**Underlying analysed corpus** (so you can verify that claims in the
bulletin trace back to actual articles):

```json
{{ analysed_articles_json }}
```

---

## What to look for

Check the draft against every category below. Cite the exact section and,
where useful, the offending text.

**hype** — banned phrasings from the system prompt are present:
`revolutionary`, `game-changing`, `transformative` (as self-evident
claim), `cutting-edge`, `state-of-the-art`, `next-generation`,
`best-in-class`, `synergy`/`synergies`, `leverage` as a verb,
`unlocks`/`unleashes`/`supercharges`, `paradigm shift`, `ecosystem`
outside biology, `robust` as a generic positive, `delve`/`dive deep`,
`in today's world`. Flag each.

**marketing** — tone that reads like a vendor brochure or trade-show
press release: superlatives without evidence, lists of capabilities with
no critical framing, vendor names presented as endorsement, undated
"will" claims.

**unsupported** — a claim in the bulletin that does not appear in any
corpus article, or that overstates what the article actually said. For
each, name the bulletin claim and either name a supporting article or
state that none exists.

**misassignment** — an article assigned to a branch in
`branch_sections` that does not match the article's branch_relevance
scores in the corpus.

**filler** — items, themes, or watch areas that do not answer the Key
Design Rule. An entry is filler when removing it would not weaken the
bulletin's signal to a branch chief. Be honest about this. Filler is the
most common failure mode and the hardest to admit to.

**stylistic** — em-dash overuse (more than one per paragraph used as
stylistic flourish), rule-of-three list patterns repeated within a
section, negative parallelism ("not X, but Y") used as default sentence
shape, vague attribution ("experts say", "studies show") without a
source.

**structural** — sections that violate their rules: Cross-Cutting Themes
with fewer than 3 supporting articles, Watch Areas that recap rather
than look forward, BLUF that buries the lede past sentence one, branch
sections present for branches with no qualifying articles, source
references missing for cited article IDs.

## Calling `ready_for_review`

- `ready_for_review: true` means a human reviewer can read this and
  decide whether to publish. It does not mean the bulletin is perfect.
  Set true when issues are minor and confined to wording.
- `ready_for_review: false` means the bulletin has a defect serious
  enough that the writer must rework it before review. Set false if
  there is unsupported content, misassignments, structural violations,
  or pervasive hype/marketing tone.

## Required output (JSON only)

Emit exactly this object. No preamble. No markdown fence. No trailing
commentary.

```json
{
  "issues": [
    {
      "severity": "low",
      "category": "hype",
      "section": "bluf.summary",
      "where": "exact offending phrase or 1-line excerpt",
      "explanation": "why this is a problem",
      "suggested_fix": "concrete suggestion, not 'consider revising'"
    }
  ],
  "summary": {
    "issue_count_by_severity": {"low": 0, "medium": 0, "high": 0},
    "issue_count_by_category": {"hype": 0, "marketing": 0, "unsupported": 0, "misassignment": 0, "filler": 0, "stylistic": 0, "structural": 0}
  },
  "ready_for_review": true
}
```

## Severity scale

- `low`: wording problem; bulletin remains usable.
- `medium`: section-level problem; one section needs rework.
- `high`: bulletin-level problem (unsupported claims, repeated
  misassignments, pervasive marketing tone). Triggers
  `ready_for_review: false`.

## What you are NOT doing

- Not rewriting the bulletin. Suggest fixes; do not perform them.
- Not re-evaluating the articles' relevance scores. That was done
  upstream.
- Not summarising the bulletin. The reviewer can read it themselves.
- Not adding new content. If a section is empty for a defensible
  reason (genuinely quiet month, no cross-cutting themes), that is
  not an issue to flag.
