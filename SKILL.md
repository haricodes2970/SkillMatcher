---
name: skill-matcher
description: "Orchestrates multiple installed skills for one request. Use when a prompt spans several domains or when several overlapping skills exist (e.g. multiple landing-page or UI skills) and the right combination must be selected and activated automatically. Decomposes the request into sub-tasks, scores every installed skill against each sub-task, resolves overlaps by use case, and activates the essential set."
---

# Skill Matcher

## Direct Tag Contract

When the user writes `@skill-matcher`, invokes this skill by ID, or asks for multi-skill
orchestration:

1. **This is the only tag required.** Never ask the user to tag individual skills.
2. **Run this protocol immediately.** Do not wait for confirmation and do not only describe
   what you would do.
3. **Scan the live inventory on every invocation.** Never reuse a previous list, a previous
   plan, or a hard-coded set of skill names. The install may contain 50 skills today and 150
   tomorrow.
4. **Follow the 6 steps in order:** Inventory → Decompose → Score → Resolve overlaps →
   Tier and activate → Report.
5. **Emit the skill plan before doing the actual task.** The table is part of the response,
   not an optional explanation after the work.

The tag selects the orchestrator. The orchestrator selects the working skill set. A small
prompt may need only one skill; a cross-domain prompt may need many. The user never has to
know which ones.

## Overview

This skill turns one user tag into a dynamic multi-skill plan:

- discovers every skill OpenCode can load, including skills installed after this file was written
- breaks the request into atomic sub-tasks
- scores the full inventory against each sub-task
- detects overlapping skills and chooses one winner per sub-task
- loads a useful, bounded set instead of guessing or loading everything
- reports the plan before implementation

A skill cannot call another skill by itself. The agent must call the native `skill` tool once
for each selected Core or Support skill. This file is therefore a protocol the agent executes,
not an autonomous router.

## Protocol

### 1. Inventory every installed skill

Resolve paths relative to this SKILL.md and run:

```bash
python3 scripts/scan_skills.py --stats > /tmp/skill-matcher-inventory.json
```

`--stats` prints source and skill counts to stderr; stdout remains valid JSON. The scanner
mirrors OpenCode V2 discovery:

| Scope | Sources |
|---|---|
| Global OpenCode | `~/.config/opencode/skills` |
| Global compatibility | `~/.claude/skills`, `~/.agents/skills` |
| Project | `.opencode/skills`, `.claude/skills`, `.agents/skills` in every ancestor |
| Legacy fallback | `~/.opencode/skills`, `./skills` |
| Explicit config | local paths in the `skills` array of any `opencode.json(c)` |
| Custom | `--extra-path PATH` or `SKILL_MATCHER_EXTRA_PATHS` |

It supports flat skills (`review.md`), directory skills (`git-release/SKILL.md`), and grouped
skills (`teams/release/SKILL.md`). Hidden directories, `node_modules`, and skills deeper than
six levels are skipped, matching OpenCode's own rules. The count is never capped: 60 skills,
200 skills, or 5 skills all work without editing this file.

Read the emitted records. Important fields are `id`, `name`, `description`, `tags`,
`use_case`, `domain`, `version`, `source`, and `advertised`. Use `id` for activation; OpenCode
selects skills by the path-derived ID, not by the frontmatter display name. Skills marked
`advertised: false` must not be auto-selected.

If the scanner returns zero skills, report that; do not invent names. If it fails, run
`python3 scripts/scan_skills.py` once without `--stats` and show the error.

### 2. Decompose the request into atomic sub-tasks

Split by deliverable, technology boundary, and cross-cutting concern. Example:

> "Build a landing page with Stripe checkout, analytics, and tests."

| # | Atomic sub-task | Domain |
|---|---|---|
| 1 | Design and build the landing page | frontend / design |
| 2 | Integrate Stripe checkout | payments / backend |
| 3 | Add analytics events | frontend / data |
| 4 | Add end-to-end tests | testing |

Do not create fake sub-tasks just to load more skills. A single-domain request stays one
sub-task. A multi-domain request naturally produces several.

### 3. Score the full inventory against every sub-task

For each sub-task:

```bash
python3 scripts/match_skills.py /tmp/skill-matcher-inventory.json "<sub-task>"
```

The matcher returns:

- `ranked_skills` — absolute TF-IDF cosine scores, strongest first
- `tiers.core` — strongest candidates (max 5)
- `tiers.support` — cross-cutting candidates (max 10)
- `tiers.reference` — long tail, available for lookup but not loaded
- `clusters` — groups whose descriptions are Jaccard-similar at ≥ 0.35
- `confident`, `top_score`, `note` — honesty about weak matches
- `total_skills` — the live inventory size (self excluded)

Scoring uses skill ID/name (3x), tags/use-case/domain (2x), and description (1x), plus a small
name-match bonus and an adjacent-phrase bonus. Generic verbs such as "build" and "create" are
ignored so unrelated skills do not win on filler words.

**Tiers are candidate pools, not the final activation list.** Use them to avoid scanning the
whole JSON by hand, then apply step 4. If `confident` is false, say the match is weak and prefer
Reference skills or a clarification-free best effort rather than pretending the top hit is
certain.

### 4. Resolve overlaps

For every cluster and every sub-task with multiple plausible skills, apply
`references/disambiguation.md`:

1. Explicit use-case/trigger tag wins.
2. Framework or stack specificity wins.
3. Narrower description scope wins.
4. Newer version wins.
5. Complementary skills may coexist when they cover different concerns.
6. Ask the user only when the choice is irreversible or mutually exclusive.

Pick **one winner per sub-task** from each overlapping cluster. A loser may become a Reference
skill, but it is not loaded. Example: `designing-frontend-interfaces` and `impeccable` both cover
landing-page design; choose one for implementation and keep the other as Reference unless the
task explicitly needs both design and a separate quality audit.

### 5. Tier and activate the essential set

| Tier | Limit | Loaded? | Purpose |
|---|---:|---|---|
| Core | 5 | Yes | Direct owners of the sub-tasks |
| Support | 10 | Yes | Testing, security, accessibility, docs, review |
| Reference | Unlimited | No | Lookup only when the task needs it |

Hard rule: **Core + Support ≤ 15 loaded skills.**

Select every distinct skill that materially improves the result, not an arbitrary minimum.
For a one-domain prompt, one Core skill can be enough. For a broad prompt, fill Core and
Support up to the caps. Do not load two overlapping skills for the same sub-task.

Activation order:

1. Process skills first (planning, TDD, debugging, review).
2. Implementation skills second (language, framework, domain).
3. Verification skills last (tests, security, accessibility).

Call the native skill tool once per selected skill:

```text
skill(id="python-pro")
skill(id="test-driven-development")
```

If a selected ID fails to load, report the error, remove it from the plan, and continue with the
remaining skills. Do not silently substitute a different skill.

### 6. Report the plan, then execute

Emit this table **before** starting the actual task:

| Sub-Task | Selected Skill(s) | Tier | Why / overlap resolved |
|---|---|---|---|
| Landing page design | `designing-frontend-interfaces` | Core | Exact landing-page scope; `impeccable` kept as Reference |
| Stripe checkout | `stripe-expert` | Core | Payment-specific skill |
| Form validation | `secure-code-guardian` | Support | Input validation and injection prevention |
| Tests | `test-driven-development`, `playwright-expert` | Core + Support | Process + browser verification |

Also state:

- inventory count and scan source summary
- overlapping clusters and the winner chosen for each
- any weak/uncertain matches
- any skill that failed to activate
- total loaded skills (must be ≤ 15)

Then call the selected skills and do the task.

## Quick Example

User: `@skill-matcher build a Python CLI calculator and test it`

1. Scan: 88 skills found (number changes automatically).
2. Decompose: CLI implementation; tests.
3. Score: `python-pro` and `test-driven-development`/`test-master` rank highest.
4. Resolve: `python-pro` owns implementation; `test-driven-development` owns process. They are
   complementary, not overlapping.
5. Tier: both Core, total 2.
6. Report the table, load both skills, then implement under TDD.

## Constraints

- Never hard-code a skill inventory or a maximum number of installed skills.
- Scan fresh on every tagged invocation; the install base is expected to grow.
- Core ≤ 5, Support ≤ 10, total loaded ≤ 15.
- Reference is unlimited but must not be loaded.
- One winner per overlapping cluster per sub-task.
- `skill-matcher` must never select itself.
- Skills with `advertised: false` are not auto-selected.
- Use the native `skill` tool for activation; this skill cannot activate others by itself.
- The plan table comes before implementation, not after.

## Failure Handling

| Situation | Action |
|---|---|
| No skills found | Report zero; proceed with built-in capability only |
| Matcher returns no confident match | Say so; use Reference candidates or best effort |
| Cluster has no obvious winner | Apply rules in `references/disambiguation.md` |
| Choice is irreversible | Ask the user once, with the shortlist and trade-off |
| Skill load fails | Report it, drop it, continue |
| Inventory is huge | Use `--stats`, tiers, and Reference; never load everything |

## References

- `references/disambiguation.md` — overlap resolution rules and worked examples
- `scripts/scan_skills.py` — dynamic inventory across OpenCode sources
- `scripts/match_skills.py` — TF-IDF ranking, phrase matching, clusters, tiers
- `tests/test_scripts.py` — regression tests for discovery, scoring, and CLI behaviour
