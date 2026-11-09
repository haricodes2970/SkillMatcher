# SkillMatcher

One tag. The right skills. Every time.

SkillMatcher is an OpenCode skill that turns a single `@skill-matcher` mention into a dynamic
multi-skill plan. It scans every skill you have installed, breaks the request into sub-tasks,
scores the whole inventory, resolves overlapping skills, and activates the useful set.

It is built for the case where you have dozens of skills and do not want to remember which ones
apply. The inventory is discovered live, so installing more skills tomorrow requires no changes.

```text
@skill-matcher build a landing page with Stripe checkout and tests
```

The matcher reports a plan, then loads the skills it selected:

| Sub-Task | Selected Skill(s) | Tier | Why |
|---|---|---|---|
| Landing page design | `designing-frontend-interfaces` | Core | Exact landing-page scope |
| Stripe checkout | payment/backend skill | Core | Payment-specific |
| Tests | `test-driven-development` | Core | Process skill, loaded first |
| Accessibility check | `building-accessible-interfaces` | Support | Cross-cutting verification |

## Why one tag

Tagging several skills manually is easy to get wrong: you forget one, pick two that conflict, or
load a generalist when a specialist exists. SkillMatcher makes the orchestrator the only thing
you tag. It decides which skills the task actually needs, including skills installed after the
protocol was written.

A small prompt can still use one skill. A broad prompt can use many. The tag count stays at one.

## Install

Clone the repository and link it into an OpenCode skill source:

```bash
git clone https://github.com/haricodes2970/SkillMatcher.git
ln -s "$PWD/SkillMatcher" ~/.config/opencode/skills/skill-matcher
```

OpenCode discovers skills in `~/.config/opencode/skills`, `~/.claude/skills`,
`~/.agents/skills`, and project-level `.opencode/skills`, `.claude/skills`, and `.agents/skills`.
The link allows OpenCode to activate the skill by ID `skill-matcher`.

Alternatively, copy the directory into any skill source root. The folder name and the path-derived
ID should stay `skill-matcher`.

## Use

In an OpenCode prompt:

```text
@skill-matcher build a Python CLI calculator and test it
```

The agent follows `SKILL.md`:

1. scans the live inventory
2. decomposes the request
3. scores every skill against every sub-task
4. resolves overlaps using `references/disambiguation.md`
5. loads Core and Support skills through OpenCode's native `skill` tool
6. prints the plan before doing the work

## Tools

### Inventory

```bash
python3 scripts/scan_skills.py --stats > /tmp/skills.json
```

The scanner mirrors OpenCode V2 discovery:

| Scope | Sources |
|---|---|
| Global OpenCode | `~/.config/opencode/skills` |
| Global compatibility | `~/.claude/skills`, `~/.agents/skills` |
| Project | `.opencode/skills`, `.claude/skills`, `.agents/skills` in ancestors |
| Legacy | `~/.opencode/skills`, `./skills` |
| Config | local entries in `opencode.json(c)` `skills` arrays |
| Custom | `--extra-path PATH`, `SKILL_MATCHER_EXTRA_PATHS` |

It reads flat skills (`review.md`), directory skills (`git-release/SKILL.md`), and grouped skills
(`teams/release/SKILL.md`). It skips hidden directories, `node_modules`, and skills deeper than six
levels, matching OpenCode's own search limit. There is no fixed skill count.

The output is a JSON array. Each record contains:

```json
{
  "id": "python-pro",
  "name": "python-pro",
  "description": "Use when building Python applications...",
  "path": "/home/user/.config/opencode/skills/python-pro/SKILL.md",
  "source": "global:opencode",
  "advertised": true,
  "tags": "Python development, type hints, pytest",
  "domain": "language",
  "version": "1.0.0"
}
```

`id` is the value to pass to OpenCode's `skill` tool. `advertised: false` means the skill's author
hid it from OpenCode's automatic list; SkillMatcher will not auto-select it.

### Matching

```bash
python3 scripts/match_skills.py /tmp/skills.json "build a landing page with Stripe checkout"
```

Output includes:

- `ranked_skills` — absolute TF-IDF cosine scores, strongest first
- `tiers.core` — strongest candidates, max 5
- `tiers.support` — cross-cutting candidates, max 10
- `tiers.reference` — long tail, not loaded
- `clusters` — description Jaccard similarity ≥ 0.35
- `confident`, `top_score`, `note` — weak-match reporting
- `total_skills` — live inventory size, with the matcher itself excluded

Use `--include-hidden` only when you explicitly want skills marked `opencode/autoinvoke: false`.

## How scoring works

Each skill becomes a weighted bag of words:

| Field | Weight | Why |
|---|---:|---|
| skill ID + display name | 3x | Names are strong intent signals |
| tags + use-case + domain | 2x | Explicit author intent |
| description | 1x | Natural-language detail |

TF-IDF down-weights words present in nearly every skill. Cosine similarity keeps scores
comparable across queries, so a weak query stays visibly weak instead of being normalised until
the top result is always `1.0`.

Two small bonuses handle cases bag-of-words misses:

- **Name bonus** — a query mentioning `Python` prefers `python-pro` over a framework skill that
  merely discusses Python.
- **Phrase bonus** — `landing page` prefers web design over cloud `landing zone`; adjacent words
  carry intent.

Generic construction verbs (`build`, `create`, `make`, `implement`, `write`, `simple`) are
ignored so unrelated skills do not rank on filler.

## Overlap resolution

`references/disambiguation.md` defines the order:

1. explicit use-case/trigger tag
2. framework/stack specificity
3. narrower description scope
4. newer version
5. complementary skills may coexist
6. ask the user only for irreversible choices

Descriptions with Jaccard similarity ≥ 0.35 are clustered. The agent chooses one winner per
sub-task and puts the loser in Reference instead of loading both.

## Tier limits

| Tier | Limit | Loaded | Purpose |
|---|---:|---|---|
| Core | 5 | Yes | Direct owners of sub-tasks |
| Support | 10 | Yes | Testing, security, accessibility, review |
| Reference | unlimited | No | Lookup only |

Core + Support must never exceed 15. The limits are guardrails against context bloat, not a
target to fill.

## Tests

```bash
python3 -m pytest -q
```

The suite covers:

- flat and nested skill discovery
- hidden, `node_modules`, and depth-limit filtering
- YAML frontmatter and folded descriptions without PyYAML
- no fixed skill limit (60 synthetic skills)
- stack-specific ranking over generic mentions
- generic-verb filtering
- phrase matching (`landing page` vs `landing zone`)
- self-exclusion and description clustering
- hidden-skill filtering
- CLI inventory loading

All runtime code uses only the Python standard library. Tests require `pytest`.

## Files

```text
SKILL.md                       protocol the agent executes
references/disambiguation.md   overlap resolution rules and examples
scripts/scan_skills.py         dynamic OpenCode inventory
scripts/match_skills.py        TF-IDF ranking, clusters, tiers
tests/test_scripts.py          regression suite
```

## Design notes

- **Why a protocol instead of a script that activates skills?** OpenCode's `skill` tool is
  agent-mediated. A skill cannot call another skill. The protocol makes that boundary explicit.
- **Why scan every time?** A cached inventory goes stale the moment a skill is installed. Scanning
  88 local skills takes milliseconds; stale orchestration costs a whole task.
- **Why absolute scores?** Normalising the top result to `1.0` hides weak matches. Absolute cosine
  plus a confidence floor lets the agent say "I am not sure" instead of loading a random skill.
- **Why max 15?** Context is finite. More loaded skills is not more capability once they start
  repeating instructions or contradicting each other.
- **Why keep the loser in Reference?** It remains available for a later sub-task without paying
  the context cost now.
