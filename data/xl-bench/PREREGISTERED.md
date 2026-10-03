# XL-orchestration bench — PRE-REGISTERED analysis plan (DRAFT)

Written BEFORE any measurement round runs (job xl-harness, 2026-10-03). The
harness is `scripts/xl_bench.py` (sidecar + runner + fixture validator); the
round is executed on the LINUX host only (`--round` refuses elsewhere — the
bwrap isolation contract does not exist on macOS). The Mac wiring smoke
(`--smoke`) is NOT a measurement and is not analyzed here.

## Prior evidence (stated honestly, per the standing rule)

Delegation/orchestration value has NEVER been shown at this model tier on
single-repo tasks: the delegation probe (n=5, rubric), §80 (7 rounds, n=33,
acceptance), and E1 (n=136/arm, powered) all found NO delta for
decomposition or for the kernel vs a naive loop (ADR-014 amendments 18/23).
E1b found no fixture pool with headroom at this tier. The XL bet is that the
value boundary lives at ORCHESTRATION SCALE (multi-hour, multi-leaf,
project-size tasks with re-planning), which none of those instruments
measured. This bench is the first test of THAT boundary — a null result here
extends the prior verdict to the orchestration regime.

## Question

Does OpenCode-as-orchestrator delegating code-writing leaves to the ACI
service (HarnessKernel executes each leaf; ADR-014 founding architecture)
beat OpenCode doing the whole XL task alone — on hidden-suite pass fraction
— and what does it cost when requirements change mid-run (re-planning waste)?

## Arms (same model OneNexus/glm-5.3, same task text, same wall budget)

- **A1 SOLO** — OpenCode alone (native loop, may use its own subagents). No
  playbook, no delegate tool, no ACI presence.
- **A2 DELEGATE** — OpenCode as ORCHESTRATOR with the `delegate_to_aci`
  OpenCode custom tool (bench-side, `scripts/xl_oc_template/`) + the A2
  playbook (eager decomposition; every leaf a contract + a mechanical check).
- **A3 DELEGATE+DISCIPLINE** — A2 + the A3 playbook: rolling-wave planning,
  uncertainty-first read-only PROBE leaves, no tiny/global-context leaves,
  plan versions on every leaf, cancel stale leaves on re-plan.

Perturbation: half of each arm's runs get the fixture's REQUIREMENT CHANGE
at 35% of the wall budget — the orchestrator process is stopped and the SAME
OpenCode session resumed with the change note as a new user message
(identical mechanism for every arm). Perturbed runs are graded on the
POST-CHANGE hidden suite (original tests the change invalidates are
retired); unperturbed runs on the original suite.

## Fixtures

Five XL fixtures, one per kind from the question — build-software,
fix-hard-bug, research, project-scale, UI — each: a seed workspace + a
written task + a HIDDEN acceptance suite kept outside the workspace (post-hoc
only) + a REQUIREMENT-CHANGE pack (change note + extra hidden tests +
invalidated-originals list) + a reference solution kept outside the workspace.
Every fixture must pass `scripts/xl_bench.py verify-fixture` BEFORE the
round: fail-as-shipped (seed fails the suite), pass-when-solved (reference
passes BOTH the original and the post-change suites). Hidden suites are
sized for partial credit: the primary outcome is pass FRACTION
(passed/(passed+failed+errors)), never only all-or-nothing. Grading is
objective only (pytest / Playwright / answer keys) — never a model judge.

## Pre-registered hypotheses

- **H1 (A2 vs A1):** delegation beats solo on hidden pass fraction.
- **H3 (A3 vs A1):** disciplined delegation beats solo on hidden pass fraction.
- **H2 (A3 vs A2, perturbed runs only):** the discipline reduces re-planning
  waste under the mid-run requirement change — waste = lines written then
  reverted (git shadow-tree proxy, pre-registered below) + leaves
  cancelled/discarded after the change lands.

## n per cell

n = 3 repeats per (fixture × arm × perturbation):
5 fixtures × 3 arms × 2 perturbations × 3 repeats = **90 runs** (30/arm).
Wall budget per run = the fixture kind's budget, **≤ 3 h** (the design cap);
the change lands at 35% of that budget. Cells run serially (the shared
gateway prefers parallel 1 — the E2D lesson); expected campaign length at
mean 1.5 h/run ≈ 6 days of host time, so the round may be split by fixture
kind without changing this plan (each split still runs every arm).

## Analysis (fixed before data)

- Primary outcome: hidden-suite pass fraction per run.
- H1/H3: paired by fixture — for each fixture, Wilcoxon signed-rank AND sign
  test over the paired (A2, A1) / (A3, A1) pass fractions across
  repeat × perturbation; plus a per-fixture table (mean pass fraction per
  arm, per perturbation). Two-sided, α = 0.05. n=30 pairs/arm is small —
  report exact p and the per-fixture table, and treat p ∈ [0.05, 0.2] as
  directional only (the §34 standing caveat).
- H2: paired by fixture over perturbed runs only — (A3, A2) differences in
  lines_reverted and in leaves_cancelled; Wilcoxon signed-rank + sign test,
  same caveats.
- Secondary (descriptive, no test): wall, OpenCode tokens (stream) + kernel
  tokens (agent_runs.usage), leaves per run, leaves cancelled, pass
  fraction split by perturbation.
- The reverted-lines metric is a PROXY: lines_written = lines added across
  consecutive 30 s git shadow-tree snapshots; lines_survived = added
  initial→final; lines_reverted = written − survived (floor 0). A rewritten
  line counts as written twice. Pre-registered as-is; no post-hoc swap.

## Invalid rows and top-ups (fixed before data)

A row is INVALID and excluded when any of:

1. **MODEL_FAILURE** — the run died on the provider before doing work
   (gateway 5xx / transport / auth error in the stream, under 60 s, no tool
   use). Same rule as rc_bench.
2. **CONTAMINATION** — the transcript reaches outside its own run: the ACI
   server port, the DB (5432/psql), docker, sibling run roots, the bench
   object store, `~/Data/Projects`, or any hidden-suite/reference path.
   (The sidecar port is legitimate — the delegate tool itself uses it.)
3. **HARNESS_CRASH** — the cell raised (recorded as a row with
   invalid_reason=HARNESS_CRASH:<type>).
4. **NO_TESTS_RAN** — the post-hoc grade collected zero tests (a fixture or
   grading bug, not a run result).

Top-ups: every invalid cell is re-run once (max 2 top-up attempts per cell);
a cell still invalid after that is reported as invalid with the reason and
the cell's n reduced — never silently dropped, never replaced by a
different repeat. The round JSON keeps every row (valid and invalid) with
its invalid_reason.

## What would count as evidence FOR the architecture

A significant (or consistently directional, per-fixture-positive) H1/H3 pass
fraction win at orchestration scale where every prior instrument found none
— with H2 showing the discipline keeps the change cost bounded. A null
result extends the amendments-18/23 verdict to the orchestration regime and
should close the question at this model tier. Any positive claim carries the
§34 caveats: one model family, author-built fixtures, n=3/cell.
