# Disambiguation Rules for Overlapping Skills

When several installed skills match the same sub-task, choose one winner rather than loading
all of them. Duplicate or near-duplicate skills waste context and can give contradictory
instructions. Apply these rules in order.

## Decision Order

### 1. Explicit use-case or trigger tag wins

If a skill's tags, triggers, or description explicitly name the user's task, it beats a general
skill that merely mentions related words.

Example:

- Task: "write a Playwright browser test"
- `playwright-expert` has an explicit Playwright/E2E trigger.
- `test-master` is broader QA/test strategy.
- Winner: `playwright-expert`. `test-master` stays Reference unless the task also needs a test
  plan or coverage audit.

### 2. Framework or stack specificity wins

A technology-specific skill beats a language-general or domain-general skill when the stack is
known.

Examples:

| Task | Winner | Loser | Reason |
|---|---|---|---|
| Vue 3 composition API screen | `vue-expert` | `javascript-pro` | Framework-specific |
| FastAPI + async SQLAlchemy API | `fastapi-expert` | `python-pro` | Framework-specific |
| PostgreSQL index design | `postgres-pro` | `database-optimizer` | Database-specific |
| Next.js App Router page | `nextjs-developer` | `react-expert` | Framework-specific |

Use the general skill when no stack is specified, or as Support when it covers a distinct
cross-cutting concern (typing, security, tests).

### 3. Narrower description scope wins

Compare how broad each description is. A narrow specialist beats a broad generalist for the
narrow task.

Example:

- Task: "tune a slow PostgreSQL query"
- `postgres-pro` is narrower than `sql-pro` or `database-optimizer`.
- Winner: `postgres-pro`.

Do not use "narrower" as a proxy for "longer description". Read the actual scope.

### 4. Newer version wins

If two skills have the same scope and specificity, compare `version` from inventory metadata.
Prefer the newer version. If versions are absent or equal, use the source precedence reported
by the scanner (later OpenCode source wins).

Never choose a newer but unrelated skill over an older but exact match.

### 5. Complementary skills may coexist

Different concerns can justify multiple skills for one sub-task. Coexistence is valid only when
the overlap is small and the roles are distinct.

Example:

- `python-pro` owns typed implementation.
- `test-driven-development` owns the red/green/refactor process.
- `secure-code-guardian` owns input validation and injection prevention.
- These are complementary: Core + Core/Support can coexist.

Counter-example:

- `designing-frontend-interfaces`, `impeccable`, and `hallmark` all claim landing-page design.
- Loading all three for the same build is overlap, not complement.
- Pick one implementation winner; keep the others as Reference or use one later for a separate
  review sub-task.

### 6. Ask the user only when the choice is irreversible

Ask only when the alternatives are mutually exclusive and costly to reverse, for example:

- Django vs FastAPI when the user has not chosen a backend and the data model depends on it.
- A native mobile stack vs a cross-platform stack.
- Replacing an existing framework or database rather than adding a feature.

Do not ask about reversible choices. For a landing page, choosing one of three design skills is
reversible; pick the best fit, note the alternative, and proceed.

## Worked Examples

### Three landing-page skills

Inventory candidates:

- `designing-frontend-interfaces` — broad web/UI design, explicitly mentions landing pages
- `impeccable` — design polish, audit, UX, landing pages
- `hallmark` — design skill for new websites/pages

Task: "build a landing page for a SaaS product".

Decision:

1. All three overlap; do not load all three for implementation.
2. `designing-frontend-interfaces` is the narrowest match for building page structure and visual
   direction.
3. `impeccable` is a better fit if the task is critique/polish of an existing page.
4. `hallmark` is a better fit when the user explicitly names Hallmark or asks for a new design
   from scratch with its specific style.
5. Winner for the build: `designing-frontend-interfaces`. Keep one audit skill as Reference,
   not loaded unless a later review sub-task appears.

### Two UI kits under one frontend framework

Task: "build a React dashboard with shadcn/ui and charts".

- `react-expert` covers implementation.
- A UI-kit skill covers component conventions.
- A chart library skill covers visualization.
- These are complementary if each has a distinct scope; load all three only if all three scopes
  are actually needed. If two skills both describe "React component libraries", pick the one
  whose tags name the requested kit.

### Process vs implementation

Task: "fix a failing test in a Python service".

- `systematic-debugging` owns root-cause investigation.
- `python-pro` owns the Python fix.
- `test-driven-development` owns the regression test.
- Load process first, then implementation; they do not compete.

### Same skill family, different variants

Task: "build a Vue 3 app with plain JavaScript, no TypeScript".

- `vue-expert` and `vue-expert-js` overlap.
- The "JavaScript only, no TypeScript" constraint is explicit.
- Winner: `vue-expert-js`. The general `vue-expert` stays Reference.

## Cluster Resolution Checklist

- [ ] Group candidates whose description Jaccard similarity is ≥ 0.35.
- [ ] Map each cluster to the sub-task it is meant to solve.
- [ ] Apply rules 1–4 to choose one winner.
- [ ] Allow rule 5 only when roles are genuinely different.
- [ ] Apply rule 6 only for irreversible, mutually exclusive choices.
- [ ] Put winners in Core/Support; put losers in Reference and do not load them.
- [ ] Record the decision and reason in the skill plan table.
