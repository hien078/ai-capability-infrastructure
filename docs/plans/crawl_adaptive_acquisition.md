# crawl.md — Adaptive Acquisition & Excellence Intelligence Plan

**Version:** V1.0  
**Date:** 2026-09-29  
**Target:** Capability Platform V2, designed to evolve cleanly into V3

## 0. Executive Summary

The subsystem should not be a simple GitHub crawler. Its job is to continuously decide **what is worth searching for**, discover candidates, understand their architecture, extract reusable capabilities, determine what is actually “excellent” in context, refine useful mechanisms into platform-native artifacts, validate them, promote them safely, and learn from production usage.

The core loop is:

```text
CLIENT DEMAND + WORLD SIGNALS + STRATEGIC SOURCES + CORPUS GAPS
                              │
                              ▼
                    Demand Intelligence
                              │
                              ▼
                   Acquisition Intelligence
                              │
                              ▼
                     Search / Discovery
                              │
                              ▼
                 Deterministic Hard Gates
                              │
                              ▼
                  Repository Understanding
                              │
                              ▼
                     Capability Mining
                              │
                              ▼
                    Excellence Engine
             ┌────────────────┼────────────────┐
             ▼                ▼                ▼
          LLM Judges      Static Evidence   Benchmarks
             └────────────────┼────────────────┘
                              ▼
                         Refinement
                              │
                              ▼
                         Verification
                              │
                              ▼
                   Promotion / Canary
                              │
                              ▼
                     Canonical Corpus
                              │
                              ▼
                    Production Routing
                              │
                              ▼
                         Telemetry
                              │
                              └────────────► next acquisition cycle
```

Three rules govern the whole design:

> **Acquisition is demand-aware but not demand-controlled.**

> **Code measures what is measurable. LLMs understand what is meaningful. Benchmarks verify what actually works.**

> **Repositories are sources. Capabilities, mechanisms, workflows, patterns, tools, skills, and agent behaviors are the actual assets.**

---

## 1. Problems this system must solve

The system must simultaneously answer:

1. What should we search for now?
2. Which external sources deserve continuous monitoring?
3. Which new/trending artifacts are worth deeper investigation?
4. What does a repository actually do?
5. Which parts of it are genuinely reusable or novel?
6. What does “elite” mean for this specific type of capability and task?
7. Is the candidate better than what the corpus already has?
8. Can its claimed value be verified empirically?
9. Can it be reused legally and safely?
10. How should it be refined into a canonical platform capability?
11. How should new capabilities enter production safely?
12. When should an old capability be demoted or replaced?

The system should optimize for **production capability improvement**, not for crawl volume.

---

## 2. Architectural separation: Demand vs Excellence

The most important architectural boundary is to separate two questions.

### 2.1 Demand Intelligence

Answers:

> **What should we search for?**

Signals include:

- immediate client request;
- recent client interests;
- persistent recurring demand;
- aggregate platform demand;
- strategic vendor/repository changes;
- trends;
- research activity;
- gaps or failures in the current corpus;
- deliberate exploration.

Output:

```text
AcquisitionOpportunity
```

Example:

```yaml
topic: browser-agent-verification
reason:
  - recent_client_demand_spike
  - corpus_gap
  - external_activity_growth
priority: high
horizon: 30d
```

### 2.2 Excellence Intelligence

Answers:

> **What should we keep, refine, promote, replace, or reject?**

Inputs:

- candidate capability;
- evidence;
- current corpus;
- target task/domain;
- model/runtime environment;
- benchmark results;
- security/license constraints.

Possible decisions:

```text
PROMOTE
PROMOTE_EXPERIMENTAL
REVIEW
HOLD
RESEARCH_ONLY
REJECT_DUPLICATE
REJECT_LOW_VALUE
REJECT_SECURITY
REJECT_LICENSE
REPLACE_INCUMBENT
```

Demand may decide what to investigate. It must never directly imply quality.

---

## 3. Signal Plane

The Acquisition Intelligence layer should consume several independent signal families.

### 3.1 Immediate client intent

For each request, extract structured demand:

```yaml
domain:
subdomain:
intent:
required_capabilities:
technologies:
constraints:
target_runtime:
quality_requirements:
```

Example requests such as:

```text
“fix responsive layout”
“implement this Figma page”
“verify the browser output visually”
```

should be semantically mapped to latent needs such as:

```text
frontend-engineering
responsive-layout
design-to-code
browser-verification
visual-regression
```

This is an LLM task, not just keyword counting.

### 3.2 Recent demand

Maintain configurable windows:

```text
24h
7d
30d
90d
```

Track semantic topic movement and demand acceleration, not just absolute counts.

### 3.3 Persistent demand

Separate stable recurring needs from short-lived spikes. A one-week trend should not overwrite long-term strategic demand.

### 3.4 Aggregate platform demand

If product policy allows aggregation, only use privacy-preserving normalized demand signals.

Recommended rules:

- tenant isolation;
- no cross-tenant raw conversation access for acquisition analytics;
- aggregate capability categories where possible instead of storing raw text;
- minimum cohort thresholds;
- retention limits;
- opt-out where appropriate;
- never infer or store sensitive personal attributes as market signals.

The goal is capability-demand estimation, not behavioral surveillance.

### 3.5 Strategic watchlists

Maintain source tiers.

```text
Tier S — first-party frontier vendors/labs
Tier A — major agent and coding-agent ecosystems
Tier B — high-value specialist ecosystems
Tier C — broader community discovery
```

Examples of categories to watch:

```text
coding agents
agent harnesses
browser/computer-use agents
research agents
memory
retrieval
verification
tool routing
orchestration
observability
security
benchmarks
```

The watchlist itself must be versioned and reviewed periodically.

### 3.6 Trend signals

Use several horizons rather than “trending in one month” alone:

```text
24h   → sudden emergence
7d    → short trend
30d   → meaningful momentum
90d   → ecosystem shift
```

Signals may include:

- star velocity;
- fork velocity;
- contributor growth;
- releases;
- issue/PR activity;
- package adoption;
- references by credible projects;
- paper/code attention;
- benchmark participation.

Important:

```text
TREND != QUALITY
TREND = REASON TO INVESTIGATE
```

### 3.7 Research signals

Monitor relevant:

- papers;
- conference proceedings;
- benchmark releases;
- paper-linked implementations;
- technical reports;
- credible engineering blogs.

The system should be able to discover a mechanism from a paper before it becomes a popular repository.

### 3.8 Corpus-gap signals

The platform itself should generate acquisition opportunities.

Examples:

```text
browser tasks have high failure rate
current verifier uses too many tokens
no strong Swift capability exists
three duplicate memory skills perform poorly
current routing strategy regressed after model upgrade
```

Flow:

```text
Production / Benchmark weakness
            ↓
       CapabilityGap
            ↓
  AcquisitionOpportunity
```

---

## 4. Demand Intelligence Engine

Suggested module:

```text
core/acquisition/demand/
├── intent_extractor/
├── recent_demand/
├── persistent_demand/
├── aggregate_demand/
├── gap_detector/
├── trend_fusion/
├── opportunity_ranker/
└── privacy_guard/
```

### 4.1 Canonical demand representation

Do not store only keywords.

```yaml
domain: software-engineering
subdomain: frontend
capability:
  - design-to-code
  - visual-verification
  - responsive-layout
technology:
  - react
  - nextjs
intent:
  - implementation
  - debugging
```

### 4.2 Demand priority model

A useful initial heuristic is:

\[
D(o)=\alpha I+\beta R+\gamma P+\delta G+\epsilon C+\zeta T+\eta X
\]

where:

- \(I\): immediate intent;
- \(R\): recent demand;
- \(P\): persistent demand;
- \(G\): aggregate platform demand;
- \(C\): corpus gap severity;
- \(T\): external trend signal;
- \(X\): exploration value.

Weights are configurable policy, not permanent truths.

### 4.3 Exploration budget

Reserve explicit crawl budget for things clients are not already asking for.

Reasonable V2 starting policy:

```text
60–75% demand-driven
15–25% strategic/trending
10–20% exploration
```

The precise numbers should later be tuned from telemetry.

Without exploration, the platform can create a filter bubble:

```text
client asks A
→ system finds more A
→ corpus becomes A-heavy
→ router surfaces more A
→ client keeps using A
→ acquisition searches for even more A
```

---

## 5. Acquisition Planner

The planner transforms signals into executable discovery jobs.

Input:

```text
AcquisitionOpportunity
```

Output:

```yaml
job_id:
objective:
search_topics:
source_classes:
source_priority:
time_window:
freshness_requirement:
crawl_depth:
budget:
expected_capability_types:
evaluation_profile:
```

Example:

```yaml
objective: improve browser verification for coding agents
search_topics:
  - browser verification
  - visual regression agent
  - DOM grounding
  - browser-use verification
sources:
  - strategic_watchlist
  - github
  - papers
window: 30d
budget:
  repo_candidates: 300
  deep_analysis: 30
  benchmark: 8
```

The planner should use an LLM to expand concepts and generate diverse search formulations rather than relying on static queries alone.

---

## 6. Query Generation

From a need such as:

```text
“stronger browser-agent verification”
```

generate search families such as:

```text
browser agent verifier
visual grounding agent
DOM action verification
computer-use evaluation
web agent benchmark
UI agent self-correction
browser trajectory verification
```

Also generate:

- synonyms;
- underlying mechanisms;
- competing terminology;
- likely packages;
- paper vocabulary;
- known alternative architectural patterns.

Search for mechanisms, not just product names.

---

## 7. Discovery Adapters

Use adapters that emit a common schema.

```text
adapters/acquisition/
├── github/
├── gitlab/
├── arxiv/
├── semantic_scholar/
├── openalex/
├── huggingface/
├── pypi/
├── npm/
├── official_feeds/
├── blogs/
└── generic_web/
```

Canonical output:

```yaml
DiscoveryCandidate:
  source:
  source_type:
  canonical_url:
  owner:
  artifact_type:
  discovered_at:
  published_at:
  updated_at:
  metadata:
  provenance:
```

The adapter layer prevents the rest of the pipeline from depending on source-specific schemas.

---

## 8. Crawl Modes and Scheduling

The crawler should support multiple modes simultaneously.

### 8.1 On-demand acquisition

Triggered when a client needs a capability the corpus cannot satisfy well.

```text
Client request
    ↓
Capability Registry
    ↓
Good enough capability exists?
   ├─ yes → route
   └─ no  → acquisition opportunity
```

### 8.2 Strategic continuous watch

Track releases, commits, new repos, new skills, architectural changes and security updates from selected sources.

Prefer incremental diff-based analysis over full re-crawling.

### 8.3 Trending scan

Recommended logical windows:

```text
24h lightweight
7d moderate
30d full trend analysis
90d ecosystem-shift analysis
```

### 8.4 Gap-driven acquisition

Production regression or benchmark weakness directly triggers targeted search.

### 8.5 Exploration crawl

Allocate a fixed budget to unfamiliar but plausible capability areas.

### 8.6 Revisit / refresh

Re-evaluate previously rejected or held candidates when:

- major releases occur;
- dependencies improve;
- demand changes;
- benchmarks change;
- model/runtime changes materially.

---

## 9. Raw Snapshot and Provenance

Never evaluate a mutable live repository without first anchoring a version.

```text
corpus/raw/
└── source/
    └── artifact/
        └── version/
            ├── metadata.json
            ├── provenance.json
            ├── license.txt
            ├── source_snapshot/
            ├── hashes.json
            └── fetch_log.json
```

Required identifiers:

```text
source
repository/artifact ID
commit SHA or version
retrieval timestamp
license state
content hash
```

Every future evaluation must be reproducible from the same snapshot.

---

## 10. Deterministic Hard Gates

Deterministic code should own factual gates, not semantic excellence.

### 10.1 License gate

Classify:

```text
allowed
allowed_with_conditions
concept_only
review_required
blocked
unknown
```

An LLM may explain a license but must not be the final source of legal compatibility truth.

### 10.2 Integrity

Record:

- commit hash;
- source hash;
- fork/upstream relation;
- signed release information when available;
- generated/vendored file markers.

### 10.3 Security pre-gate

Before executing external code:

- dependency scan;
- secret scan;
- suspicious install scripts;
- binary detection;
- risky shell commands;
- network/file-system behavior indicators.

External code must execute only in an isolated sandbox.

### 10.4 Maintenance evidence

Record, but do not overinterpret:

- last commit;
- release history;
- contributors;
- CI presence;
- tests;
- documentation;
- dependency freshness;
- archived state;
- issue/PR activity.

### 10.5 Duplicate fingerprints

Generate:

- exact content hash;
- fork ancestry;
- file hashes;
- AST/code fingerprints;
- dependency signatures;
- semantic embeddings;
- later: mechanism signatures.

---

## 11. Repository Understanding

This is the first major LLM stage.

```text
core/acquisition/understanding/
├── repo_mapper/
├── context_selector/
├── architecture_analyst/
├── execution_flow/
├── capability_miner/
└── mechanism_signature/
```

### 11.1 Repo Mapper

Construct:

```text
Repository
├── architecture
├── modules
├── entrypoints
├── execution flow
├── tools
├── prompts
├── agents
├── skills
├── workflows
├── tests
├── benchmarks
├── configuration
└── dependencies
```

Use parsers for structure and LLMs for meaning.

### 11.2 Context selection

Do not pass the entire repository blindly to a powerful model.

```text
repo tree
  ↓
static relevance extraction
  ↓
cheap semantic triage
  ↓
important files
  ↓
strong-model deep analysis
```

### 11.3 Architecture analysis output

```yaml
problem_solved:
core_architecture:
execution_loop:
key_mechanisms:
state_model:
tool_model:
memory_model:
error_recovery:
verification:
novel_elements:
limitations:
transferable_components:
```

Every claim should be grounded to repository evidence internally.

---

## 12. Capability Mining

Do not ask:

> “Is this repo elite?”

Ask:

> “Which mechanisms inside this repo deserve independent evaluation?”

Example:

```text
Repository X
├── planner           average
├── memory            ordinary
├── retry engine      excellent
├── verifier          excellent
└── UI                irrelevant
```

Output:

```text
Candidate A = retry engine
Candidate B = verifier
```

### 12.1 Supported capability types

At minimum:

```text
skill
tool
workflow
agent
agent-pattern
harness
evaluator
router
planner
memory-strategy
retrieval-strategy
verifier
guardrail
benchmark
resource
service
protocol-adapter
```

### 12.2 Candidate schema

```yaml
candidate_id:
source_refs:
type:
name:
problem:
mechanism:
inputs:
outputs:
preconditions:
dependencies:
execution_pattern:
claimed_benefits:
observed_evidence:
known_limitations:
extractability:
license_constraints:
```

---

## 13. Mechanism-Level Deduplication

Text similarity is insufficient.

For example:

```text
“self-healing coding loop”
“iterative autonomous repair”
```

may both mean:

```text
observe → diagnose → patch → test → retry
```

Conversely, two things called “agent memory” may use entirely different mechanisms.

Create a `MechanismSignature`:

```yaml
state_transition_pattern:
tools_required:
feedback_source:
control_loop:
memory_behavior:
termination_condition:
verification_behavior:
```

Dedup layers:

```text
1. exact/file duplicate
2. fork duplicate
3. implementation similarity
4. semantic similarity
5. mechanism equivalence
```

---

## 14. Defining Excellence

There is no context-free universal elite capability.

Represent excellence as:

\[
E(c\mid t,u,e,\tau)
\]

where:

- \(c\) = capability;
- \(t\) = task/domain;
- \(u\) = target user/client class;
- \(e\) = model/runtime/environment;
- \(\tau\) = time/frontier state.

### 14.1 Intrinsic quality

Properties mainly belonging to the capability:

```text
correctness
reliability
robustness
security
maintainability
reproducibility
clarity
testability
observability
documentation
```

### 14.2 Contextual quality

Depends on where it is used:

```text
task relevance
model compatibility
runtime compatibility
cost suitability
latency suitability
domain suitability
client constraints
```

### 14.3 Frontier quality

Relative to existing alternatives:

```text
novelty
capability gain
replacement value
efficiency improvement
performance improvement
simplicity improvement
```

---

## 15. Excellence Vector

Do not permanently compress quality into one scalar.

Recommended representation:

```yaml
excellence:
  correctness:
  empirical_performance:
  reliability:
  robustness:
  security:
  novelty:
  generalizability:
  composability:
  maintainability:
  observability:
  reproducibility:
  efficiency:
  token_efficiency:
  latency:
  documentation:
  maturity:
  provenance_confidence:
  freshness:
  task_relevance:
  capability_gain:
  redundancy:
```

A temporary scalar may be derived for queue ordering, but the underlying vector must remain available.

---

## 16. Type-Specific Excellence Profiles

A universal rubric is incorrect.

### Skill

Prioritize:

```text
instruction quality
clarity
task improvement
token efficiency
transferability
model robustness
failure guidance
composition quality
```

### Tool

Prioritize:

```text
correctness
API stability
reliability
latency
security
error handling
observability
dependency quality
```

### Agent

Prioritize:

```text
task success
planning
tool use
state management
recovery
termination
cost
latency
robustness
```

### Harness

Prioritize:

```text
orchestration
sandboxing
context management
fault isolation
hooks
observability
extensibility
state durability
security
```

### Workflow

Prioritize:

```text
reproducibility
clarity
composability
failure handling
cost
human-intervention requirement
```

### Evaluator

Prioritize:

```text
correlation with real task success
false positives
false negatives
robustness against gaming
consistency
cost
```

---

## 17. Excellence Constitution

Create a versioned policy layer:

```text
core/evaluator/excellence/
├── constitution.yaml
├── profiles/
│   ├── skill.yaml
│   ├── tool.yaml
│   ├── agent.yaml
│   ├── harness.yaml
│   ├── workflow.yaml
│   └── evaluator.yaml
└── domains/
    ├── coding.yaml
    ├── research.yaml
    ├── browser.yaml
    ├── data.yaml
    └── ...
```

Example:

```yaml
type: agent
hard_gates:
  malicious_behavior: fail
  critical_security_issue: fail
  license_blocker: fail

dimensions:
  task_success:
    importance: critical
  reliability:
    importance: critical
  recovery:
    importance: high
  architecture:
    importance: high
  novelty:
    importance: medium
  popularity:
    importance: very_low
```

The constitution must be versioned because the meaning of “excellent” changes as models, context windows, cost, runtimes, and agent architectures evolve.

---

## 18. LLM Judge Ensemble

Never let one LLM produce an irreversible “elite/not elite” decision.

Use specialized roles:

```text
Candidate
   ├── Architecture Judge
   ├── Engineering Judge
   ├── Domain Judge
   ├── Novelty Judge
   ├── Capability-Gain Judge
   ├── Security Critic
   └── Skeptical Critic
             │
             ▼
         Evidence Merge
             │
             ▼
           Verifier
```

### Architecture Judge

Questions:

- What is genuinely architectural?
- What is incidental implementation detail?
- Is complexity justified?
- Is the abstraction reusable?
- Which mechanism is transferable?

### Engineering Judge

Questions:

- Is implementation robust?
- Are edge cases handled?
- Are tests meaningful?
- Is failure recovery real?
- Are dependencies reasonable?

### Domain Judge

Uses domain-specific expectations for coding, research, browser, data, etc.

### Novelty Judge

Compares against the corpus:

- materially different or renamed duplicate?
- new mechanism or minor variation?
- known mechanism with meaningful improvement?

### Capability-Gain Judge

Estimate marginal value:

\[
Gain(c)=Value(Corpus+c)-Value(Corpus)
\]

A very good capability may still have low marginal value if the corpus already contains an equal or better equivalent.

### Skeptical Critic

Explicitly search for reasons **not** to promote the candidate.

This counters:

- README marketing;
- prestige bias;
- star bias;
- novelty hype;
- elegant-but-useless architecture.

---

## 19. Evidence Rules for LLM Evaluation

Every important judge conclusion should use structured evidence:

```yaml
claim:
evidence:
source_location:
confidence:
counter_evidence:
uncertainty:
```

Example:

```yaml
claim: robust tool-failure recovery
evidence:
  - retry state transition exists in implementation
  - tests cover failed tool response
counter_evidence:
  - timeout recovery is not tested
confidence: medium_high
```

No unsupported “8.9/10” verdict should be accepted as sufficient evidence.

---

## 20. Pairwise Frontier Comparison

Prefer comparison against current alternatives rather than isolated rating.

```text
Candidate X
   vs
Current best A
   vs
Current best B
   vs
Baseline
```

Ask:

```text
Where is X better?
Where is X worse?
Under what conditions?
What evidence supports each difference?
What tradeoff is introduced?
```

This makes “elite” relative to the platform’s current frontier.

---

## 21. Benchmark Layer

Whenever a claim can be executed, test it.

Benchmark dimensions may include:

```text
task success
correctness
failure recovery
tool-call efficiency
token usage
latency
memory usage
security behavior
regression rate
human intervention
```

### Coding examples

```text
bug repair
feature implementation
test generation
repo navigation
refactoring
dependency repair
```

### Browser examples

```text
navigation success
DOM grounding
visual verification
form completion
recovery after page change
```

### Research examples

```text
source coverage
citation correctness
claim grounding
contradiction detection
```

Store reproducibility data:

```text
benchmark version
model version
runtime version
environment image
tool versions
prompt version
result artifacts
```

---

## 22. Model and Runtime Dependence

A capability can perform well on one model and poorly on another.

Store:

```yaml
evaluated_on:
  model:
  version:
  runtime:
  context_window:
  tool_environment:
```

Re-evaluate high-value capabilities after material model or runtime upgrades.

---

## 23. Refinement Engine

External discoveries should rarely be copied unchanged.

Refinement pipeline:

```text
extract
→ normalize
→ simplify
→ isolate
→ remove unnecessary dependencies
→ rewrite instructions/interfaces
→ add guardrails
→ add tests
→ add evals
→ optimize context
→ document provenance
```

### Skill refinement

Target package:

```text
skill/
├── SKILL.md
├── references/
├── scripts/
├── tests/
├── evals/
└── metadata.yaml
```

### Code capability refinement

Where licensing permits:

- isolate minimal reusable mechanism;
- clean interface;
- dependency pinning;
- timeout handling;
- logging;
- sandbox compatibility;
- deterministic wrappers;
- tests.

### Concept refinement

Sometimes the idea is excellent while the original code is not reusable. In that case:

```text
source architecture idea
       ↓
canonical internal specification
       ↓
new clean implementation
```

Preserve provenance and license boundaries.

---

## 24. Provenance and Licensing

Every canonical capability should record:

```yaml
provenance:
  sources:
    - repo:
      commit:
      files:
      license:
  extraction_method:
  transformed_by:
  transformation_version:
  benchmark_refs:
```

Separate reuse rights for:

```text
idea
instruction text
source code
assets
data
model weights
```

Potential status:

```text
safe_to_reuse
safe_to_adapt_with_notice
concept_only
internal_research_only
manual_legal_review
blocked
```

---

## 25. Security Pipeline

Treat all external code as untrusted.

```text
Static scan
   ↓
Dependency scan
   ↓
Secret scan
   ↓
Install-script inspection
   ↓
Sandbox execution
   ↓
Behavior monitoring
   ↓
Network/filesystem policy
   ↓
Promotion
```

Default sandbox policy:

- no production secrets;
- restricted outbound network;
- limited filesystem access;
- CPU/memory/time limits;
- disposable environment;
- explicit permission for external side effects.

---

## 26. Promotion Lifecycle

Recommended state machine:

```text
DISCOVERED
   ↓
RAW
   ↓
PARSED
   ↓
CANDIDATE
   ↓
REVIEWED
   ↓
VERIFIED
   ↓
CANARY
   ↓
PRODUCTION
   ↓
DEPRECATED
   ↓
RETIRED
```

Side states:

```text
HOLD
REJECTED
QUARANTINED
LEGAL_REVIEW
SECURITY_REVIEW
```

### Candidate → Reviewed

Requires:

- semantic understanding;
- provenance;
- duplicate analysis;
- initial license status;
- first judge pass.

### Reviewed → Verified

Requires:

- evidence verification;
- benchmark where applicable;
- security checks;
- comparison to incumbent.

### Verified → Canary

Requires:

- expected positive capability gain;
- no critical blocker;
- monitoring plan;
- rollback path.

### Canary → Production

Requires:

- positive real usage evidence;
- acceptable failure rate;
- acceptable cost/latency;
- no material regression.

---

## 27. Canary and Rollback

Do not route all traffic to a newly promoted capability immediately.

Use:

```text
shadow evaluation
→ limited canary
→ constrained task classes
→ progressive rollout
→ production
```

Every production capability should have:

```text
previous stable version
rollback trigger
rollback action
telemetry comparison
state compatibility notes
```

---

## 28. Production Feedback Loop

Collect capability-level operational evidence:

```text
capability selected
task class
model/runtime
success/failure
fallback
latency
tokens
tool calls
error category
verification result
user correction/override where appropriate
```

Apply privacy rules before analytics.

Production telemetry feeds:

```text
Demand Intelligence
Corpus Gap Detector
Replacement Search
Demotion
Benchmark refresh
```

---

## 29. Demotion, Replacement, Retirement

Possible triggers:

- new candidate dominates incumbent;
- dependency abandoned;
- security vulnerability;
- increasing failure rate;
- excessive cost;
- model upgrade makes capability obsolete;
- duplication/merger;
- license change;
- long-term lack of usefulness.

Use:

```text
production
→ deprecated
→ compatibility-only
→ retired
```

Preserve historical provenance and evaluations.

---

## 30. Anti-Hype and Anti-Bias Controls

### Popularity is a weak prior

Stars, forks and downloads should mostly affect:

```text
discovery priority
```

not:

```text
final excellence
```

### Prestige-blind judge pass

Where practical, hide the vendor/repository name from at least one judge and evaluate the mechanism first.

### Separate claim types

Store separately:

```text
claimed_by_author
observed_in_code
verified_by_benchmark
observed_in_production
```

Never collapse these into one field.

### Novelty != usefulness

Track separately:

```text
novelty
practical_value
capability_gain
```

A novel idea with weak practical performance may go to research storage rather than production.

---

## 31. Exploration vs Exploitation

The acquisition portfolio should contain both:

```text
Exploitation
→ better versions of known needed capabilities

Exploration
→ unknown/emerging mechanisms outside current demand
```

V2 should use simple configurable budgets. Bandits/Bayesian allocation can be considered in V3 after enough telemetry exists.

Do not prematurely add RL just to make the system “smart”.

---

## 32. Cost-Aware LLM Cascade

Do not use the strongest model on every discovery.

Example funnel:

```text
100,000 raw discoveries
        ↓
deterministic filters
        ↓
20,000
        ↓
embeddings / cheap semantic triage
        ↓
3,000
        ↓
cheap LLM analysis
        ↓
500
        ↓
strong LLM architecture analysis
        ↓
100
        ↓
multi-judge
        ↓
20
        ↓
benchmark
        ↓
5–10 promoted capabilities
```

Numbers are illustrative; budgets should be measured and tuned.

---

## 33. Confidence-Aware Escalation

Escalate to stronger models or human review when:

```text
judges disagree
strategic impact is high
expected capability gain is high
security concern exists
license is ambiguous
evidence is weak
candidate appears frontier-level
```

Routine candidates should use cheaper paths.

---

## 34. Human-in-the-Loop

Human review should be an escalation path, not the default bottleneck.

Recommended human checkpoints:

- legal ambiguity;
- security-critical promotion;
- major platform architecture replacement;
- evaluator constitution changes;
- uncertain high-impact candidates.

---

## 35. Core Data Model

Required entities:

```text
Source
Artifact
ArtifactVersion
DiscoverySignal
DemandSignal
AcquisitionOpportunity
AcquisitionJob
RepositoryMap
Mechanism
CandidateCapability
Evidence
Evaluation
BenchmarkRun
RefinementRun
Capability
CapabilityVersion
CapabilityRelease
ProductionObservation
```

Example candidate:

```yaml
candidate_id: capcand_...
identity:
  provisional_name: iterative-tool-failure-recovery
  type: agent-pattern
source:
  repo: ...
  commit: ...
problem:
  description: recover from failed tool executions
mechanism:
  state_machine:
    - observe_failure
    - classify
    - generate_repair
    - retry
    - verify
  termination:
    max_attempts: 3
evidence:
  implementation: true
  tests: partial
  benchmark: pending
evaluation:
  architecture: high
  novelty: medium
  generalizability: high
  capability_gain: high
status: candidate
```

---

## 36. Canonical Capability IR

The normalized representation should be independent of its original source.

```yaml
capability:
  id:
  name:
  kind:
  version:
intent:
  solves:
  use_when:
  avoid_when:
interface:
  inputs:
  outputs:
mechanism:
  description:
  state_machine:
  dependencies:
requirements:
  tools:
  runtime:
  model_characteristics:
quality:
  excellence_vector:
  evaluated_contexts:
evidence:
  benchmarks:
  judge_reports:
  production_observations:
provenance:
  sources:
  licenses:
lifecycle:
  status:
  promoted_at:
  deprecated_at:
```

---

## 37. Corpus Structure

```text
corpus/
├── raw/
├── parsed/
├── candidates/
├── canonical/
├── verified/
├── production/
├── deprecated/
└── quarantine/
```

---

## 38. Suggested Service Layout

```text
core/
├── acquisition/
│   ├── demand/
│   ├── signals/
│   ├── planner/
│   ├── query_generator/
│   ├── scheduler/
│   └── budget/
├── discovery/
│   ├── adapters/
│   ├── ranking/
│   └── dedup/
├── ingestion/
│   ├── snapshot/
│   ├── provenance/
│   ├── licensing/
│   └── security/
├── understanding/
│   ├── repo_mapper/
│   ├── architecture_analyst/
│   ├── capability_miner/
│   └── mechanism_signature/
├── evaluator/
│   ├── excellence/
│   ├── judges/
│   ├── critic/
│   ├── comparator/
│   ├── verifier/
│   └── benchmark/
├── refinement/
│   ├── normalizer/
│   ├── skill_refiner/
│   ├── code_refiner/
│   ├── workflow_refiner/
│   └── test_generator/
├── promotion/
│   ├── state_machine/
│   ├── canary/
│   └── rollback/
└── feedback/
    ├── telemetry/
    ├── drift/
    ├── replacement/
    └── retirement/
```

---

## 39. Event-Driven Backbone

Useful events:

```text
ClientDemandObserved
DemandSpikeDetected
CorpusGapDetected
StrategicSourceChanged
TrendDetected
CandidateDiscovered
ArtifactFetched
LicenseClassified
SecurityRiskFound
RepositoryMapped
CapabilityMined
CandidateEvaluated
BenchmarkCompleted
CapabilityVerified
CapabilityPromoted
CapabilityDemoted
ProductionRegressionDetected
```

This keeps components independently replaceable.

---

## 40. Queue Priorities

Suggested policy:

```text
P0 production regression / security
P1 explicit client need with missing capability
P2 high-demand corpus gap
P3 major strategic-source update
P4 strong trend candidate
P5 exploration
```

---

## 41. Failure Handling

Every stage should be idempotent and persist:

```text
job state
attempt count
last error
retry policy
dead-letter reason
```

Failed acquisitions must not disappear silently.

---

## 42. Observability

### Discovery dashboard

```text
candidates/day
source distribution
duplicate rate
crawl failures
trend sources
```

### Understanding dashboard

```text
repos mapped
LLM cost/repo
capabilities/repo
analysis confidence
```

### Evaluation dashboard

```text
judge agreement
critic rejection rate
benchmark pass rate
promotion rate
```

### Production dashboard

```text
task success
capability usage
fallback rate
cost
latency
regressions
```

Most important acquisition metric:

```text
percentage of promoted discoveries that create measurable production improvement
```

not:

```text
number of repos crawled
```

---

## 43. Key Metrics

### Acquisition precision

\[
Precision=\frac{UsefulPromotedCapabilities}{DeeplyEvaluatedCandidates}
\]

### Capability gain

\[
Gain(c)=Perf(C+c)-Perf(C)
\]

### Replacement gain

\[
ReplacementGain(c,n)=Perf(C-c+n)-Perf(C)
\]

### Cost per useful capability

\[
CPU=\frac{Crawl+LLM+BenchmarkCost}{PromotedCapabilitiesWithPositiveEvidence}
\]

True global recall cannot be known in an open world, so use benchmark reference sets and periodic audits as a recall proxy.

---

## 44. Client Preference and Privacy

Use three levels.

### Level 1 — Session intent

Safe default:

```text
current task
current domain
current constraints
```

### Level 2 — Tenant recent demand

Where permitted, store normalized demand rather than raw conversations when raw text is unnecessary.

Example:

```text
browser-verification +3
react-frontend +7
deployment +2
```

### Level 3 — Global aggregate demand

Only aggregated and privacy-protected.

Do not use acquisition telemetry to build sensitive personal profiles.

---

## 45. Temporal Intelligence and Confidence Decay

Capabilities evolve through:

```text
frontier → standard → legacy → obsolete
```

Store:

```text
first_seen
last_evaluated
last_benchmarked
frontier_status
replacement_candidates
```

Evaluation confidence should decay when:

- source changes;
- dependency versions change;
- model/runtime changes;
- benchmarks age;
- ecosystem assumptions shift.

Confidence decay can automatically trigger refresh jobs.

---

## 46. Incremental Change Detection

For known repositories:

```text
old commit
    ↓
git diff
    ↓
change classifier
    ↓
architecture/capability affected?
   ├─ no → metadata refresh only
   └─ yes → targeted re-analysis
```

Classify changes as:

```text
docs-only
bugfix
dependency
new feature
architecture
security
major release
```

Avoid expensive full analysis when only minor files changed.

---

## 47. Paper-to-Capability Pipeline

```text
Paper
  ↓
claim extraction
  ↓
mechanism extraction
  ↓
code available?
 ├─ yes → implementation analysis
 └─ no  → concept candidate
  ↓
reproduction feasibility
  ↓
prototype if valuable
  ↓
benchmark
  ↓
candidate capability
```

Do not promote executable claims based only on paper claims when empirical validation is possible.

---

## 48. Framework/Product-to-Capability Pipeline

Large systems should be decomposed:

```text
Large Agent System
      ↓
planner
context management
tool routing
sandbox
memory
verification
recovery
observability
```

Evaluate each mechanism independently instead of ingesting a brand or repository as a monolith.

---

## 49. Capability Composition

Some strong systems emerge from combinations.

Example:

```text
A = strong planner
B = strong verifier
C = strong retry mechanism
```

The composer may propose:

```text
A + B + C
```

but the composition must be re-evaluated. Component quality does not guarantee composition quality.

---

## 50. Counterfactual Testing

For important candidates compare:

```text
baseline
vs
baseline + candidate
vs
baseline + incumbent
```

This directly estimates marginal value and protects against placebo improvements or benchmark noise.

---

## 51. Adversarial Evaluation

High-value agent/harness capabilities should be tested under:

- misleading documentation;
- broken dependencies;
- malformed tool output;
- timeouts;
- partial state;
- prompt injection;
- malicious repository content;
- unexpected file layouts;
- conflicting instructions.

---

## 52. Evaluator Drift and Calibration

Judges themselves change.

Version:

```text
judge model
judge prompt
rubric
constitution
benchmark
aggregation logic
```

Maintain a gold evaluation set containing:

```text
known excellent
known mediocre
known unsafe
known duplicate
known overhyped
known niche-but-useful
```

Track:

```text
judge ↔ benchmark correlation
judge ↔ human expert correlation
false promotion rate
false rejection rate
confidence calibration
```

When upgrading judges, replay the gold set before production rollout.

---

## 53. Ensemble Decision Logic

Do not average everything blindly.

Recommended sequence:

```text
Hard Gates
   ↓
Evidence Quality
   ↓
Context Relevance
   ↓
Intrinsic Quality
   ↓
Pairwise Frontier Comparison
   ↓
Benchmark
   ↓
Capability Gain
   ↓
Promotion Decision
```

Examples:

```text
security fail → reject regardless of novelty
benchmark strongly negative → no production promotion
high novelty + low practical gain → research-only
high gain + strong evidence → verified/canary
```

---

## 54. Evidence Package

Before final evaluation, assemble:

```text
EvidencePackage
├── source/provenance
├── repository map
├── extracted capability
├── source references
├── author claims
├── static metrics
├── dependency information
├── security findings
├── comparison candidates
├── benchmark results
└── judge reports
```

Judges consume this controlled package rather than arbitrary unbounded context.

---

## 55. Negative Knowledge

Store why candidates were rejected.

Example:

```yaml
candidate: X
rejected_because:
  - no real recovery tests
  - mechanism already exists
  - restrictive license
```

This prevents repeated crawl/evaluation waste and becomes valuable training/evaluation data later.

---

## 56. Frontier Registry

Maintain the best-known capability **by context**, not one global winner.

Example:

```yaml
browser-verification:
  low-cost: capability_A
  highest-success: capability_B
  local-only: capability_C
  low-latency: capability_D
```

This structure is much more useful to the later router.

---

## 57. Corpus Health Manager

Continuously inspect the internal corpus for:

- duplicates;
- obsolete capabilities;
- unused capabilities;
- conflicting capabilities;
- low-confidence capabilities;
- missing evaluations;
- capability categories with poor coverage.

Outputs feed both acquisition and retirement.

---

## 58. Replacement Search

When production performance falls:

```text
ProductionRegression
       ↓
ReplacementOpportunity
       ↓
Targeted Acquisition
       ↓
Pairwise Benchmark
       ↓
Canary Replacement
```

Replacement search should become one of the most valuable feedback loops in V3.

---

## 59. Capability Lineage Graph

Later, track relationships:

```text
derived_from
improves
replaces
combines
conflicts_with
equivalent_to
inspired_by
```

Do not introduce a graph database merely because a graph sounds sophisticated. Start with relational relations and move to a graph layer only when graph queries become operationally valuable.

---

## 60. Cost and Budget Management

Every acquisition job needs explicit limits:

```yaml
budget:
  fetch_count:
  repo_bytes:
  cheap_llm_tokens:
  strong_llm_tokens:
  benchmark_compute:
  max_wall_time:
```

Stop conditions include:

```text
enough strong candidates found
no new mechanism discovered
budget exhausted
candidate dominated by incumbent
license/security blocker
source quality too low
```

---

## 61. Acquisition Priority Heuristic

A useful investigation priority can be derived from:

\[
Priority = Demand \times Gap \times ExpectedGain \times SourceConfidence \times Freshness
\]

plus an explicit exploration allocation.

This score decides **what to investigate first**. It is not an excellence score.

---

## 62. Recommended V2 Scope

V2 should implement:

1. current/recent demand signals;
2. strategic watchlists;
3. GitHub and official-source discovery;
4. 7d/30d trend scans;
5. raw versioned snapshots;
6. provenance and license classification;
7. deterministic security gates;
8. repo mapping;
9. LLM architecture analysis;
10. capability mining;
11. mechanism-level dedup;
12. Excellence Vector;
13. specialized LLM judges + critic;
14. pairwise comparison against incumbent;
15. benchmark interface;
16. refinement/canonicalization;
17. lifecycle states;
18. canary/rollback;
19. production telemetry;
20. gap-driven acquisition loop.

V2 should deliberately avoid overbuilding:

- reinforcement learning;
- unrestricted self-modifying agents;
- autonomous legal decisions;
- giant knowledge graph before need is proven;
- complex swarm orchestration;
- arbitrary external code execution outside sandbox.

---

## 63. V3 Extensions

After V2 has real telemetry, consider:

```text
adaptive crawl-budget optimization
learned opportunity ranking
bandit-based exploration/exploitation
automatic benchmark generation
cross-model capability optimization
judge-selection optimization
capability composition search
causal attribution of production gains
market/ecosystem forecasting
lineage graph reasoning
```

---

## 64. Implementation Roadmap

### Phase 0 — Foundations

Build:

- canonical schemas;
- event model;
- provenance model;
- lifecycle state machine;
- Excellence Constitution v0.1;
- privacy policy;
- security execution policy.

Acceptance condition:

```text
a candidate can be traced and reproduced end-to-end
```

### Phase 1 — Discovery MVP

Build:

- GitHub adapter;
- official watchlist;
- scheduler;
- 7d/30d scans;
- raw store;
- metadata extraction;
- license detection;
- basic dedup.

Acceptance:

```text
system autonomously produces a clean candidate queue
```

### Phase 2 — Demand Intelligence

Build:

- current intent extraction;
- recent semantic demand;
- corpus-gap detector;
- acquisition opportunities;
- crawl-budget allocation;
- exploration quota.

Acceptance:

```text
crawl priorities change meaningfully with real demand and corpus weakness
```

### Phase 3 — Repository Understanding

Build:

- repo mapper;
- context selector;
- architecture analyst;
- execution-flow extractor.

Acceptance:

```text
system can explain a repository correctly without loading everything into one prompt
```

### Phase 4 — Capability Mining

Build:

- mechanism extractor;
- candidate generator;
- mechanism signatures;
- semantic + structural dedup.

Acceptance:

```text
one repository can yield zero, one, or many independent capabilities
```

### Phase 5 — Excellence Engine

Initial judges:

```text
Architecture
Engineering
Domain
Novelty
Capability Gain
Critic
```

Acceptance:

```text
each evaluation contains evidence, comparison, uncertainty and counter-evidence
```

### Phase 6 — Benchmark Framework

Create abstractions:

```text
BenchmarkSuite
BenchmarkCase
BenchmarkRun
BenchmarkResult
```

Start with coding because executable outcomes are easier to validate.

Acceptance:

```text
a new capability can be compared reproducibly against an incumbent
```

### Phase 7 — Refinement

Build:

- canonicalizer;
- skill refiner;
- code/workflow refiner;
- test/eval generation;
- provenance preservation.

Acceptance:

```text
external discovery becomes a platform-native candidate rather than a copied repository
```

### Phase 8 — Promotion / Canary

Build:

- lifecycle enforcement;
- canary routing;
- fallback;
- rollback;
- production observation.

Acceptance:

```text
no candidate can silently become production
```

### Phase 9 — Feedback Loop

Feed production success, failure, cost and demand back into:

```text
gap detection
replacement search
acquisition priority
re-evaluation
demotion
```

Acceptance:

```text
the system autonomously detects where the corpus needs improvement
```

---

## 65. Recommended Initial Domain

Start with:

```text
software-engineering / coding-agent capabilities
```

Reasons:

- large open-source supply;
- clear executable benchmarks;
- measurable task outcomes;
- direct relevance to the current Capability Platform;
- many skills, agents, harnesses and tools to study.

Do not start with every domain at once.

---

## 66. Suggested Initial Source Waves

### Wave 1 — highest-confidence sources

```text
official OpenAI sources
official Anthropic sources
major coding-agent repositories
major agent orchestration repositories
```

### Wave 2

```text
strong relevant GitHub projects
papers linked to code
package ecosystems
benchmark ecosystems
```

### Wave 3

```text
broader community
technical blogs
experimental repositories
```

This ordering reduces noise while the evaluator is still being calibrated.

---

## 67. Initial Excellence Priorities for Coding Capabilities

Suggested ordering:

```text
1. task success
2. correctness
3. reliability
4. recovery behavior
5. verification quality
6. security
7. capability gain
8. generalizability
9. composability
10. cost/token efficiency
11. maintainability
12. novelty
13. popularity
```

Popularity is deliberately near the bottom.

---

## 68. Mandatory Evaluation Questions

Every serious candidate should answer:

1. What problem does this capability solve?
2. Is that problem strategically or currently important?
3. What is the actual mechanism?
4. Where is the evidence that the mechanism exists?
5. Is it materially different from existing corpus capabilities?
6. Is it better in any meaningful context?
7. Can the claim be benchmarked?
8. What are the tradeoffs?
9. What dependencies are required?
10. Is it safe?
11. Is reuse legally acceptable?
12. Can it be isolated and normalized?
13. What is its cost?
14. Which models/runtimes support it well?
15. What evidence would cause us to replace or retire it later?

---

## 69. Example End-to-End Run

Suppose client activity shows increasing frontend/browser demand.

```text
Recent requests
      ↓
Demand Intelligence
      ↓
visual-browser-verification opportunity
      ↓
Acquisition Planner
      ↓
GitHub + watchlists + papers
      ↓
300 discoveries
      ↓
hard gates
      ↓
80 candidates
      ↓
cheap semantic triage
      ↓
20 repositories
      ↓
repo mapping
      ↓
LLM capability mining
      ↓
35 candidate mechanisms
      ↓
mechanism dedup
      ↓
12 unique candidates
      ↓
multi-judge
      ↓
5 strong candidates
      ↓
benchmark against current verifier
      ↓
2 positive candidates
      ↓
refinement
      ↓
verified
      ↓
canary
      ↓
1 promoted capability
```

The successful outcome is not:

```text
“We found the most popular repository.”
```

It is:

```text
“We found and verified a mechanism that improves the platform.”
```

---

## 70. Suggested Repository Layout

```text
capability-platform/
├── core/
│   ├── acquisition/
│   │   ├── demand/
│   │   ├── signals/
│   │   ├── planner/
│   │   ├── query/
│   │   ├── scheduler/
│   │   └── budget/
│   ├── discovery/
│   │   ├── github/
│   │   ├── papers/
│   │   ├── packages/
│   │   ├── official/
│   │   └── dedup/
│   ├── ingestion/
│   │   ├── snapshot/
│   │   ├── provenance/
│   │   ├── licensing/
│   │   └── security/
│   ├── understanding/
│   │   ├── mapper/
│   │   ├── architecture/
│   │   ├── miner/
│   │   └── mechanism/
│   ├── evaluator/
│   │   ├── excellence/
│   │   ├── judges/
│   │   ├── critic/
│   │   ├── comparator/
│   │   ├── verifier/
│   │   └── benchmark/
│   ├── refinement/
│   │   ├── canonicalizer/
│   │   ├── skill/
│   │   ├── code/
│   │   ├── workflow/
│   │   └── tests/
│   ├── promotion/
│   │   ├── lifecycle/
│   │   ├── canary/
│   │   └── rollback/
│   └── feedback/
│       ├── telemetry/
│       ├── drift/
│       ├── replacement/
│       └── retirement/
├── corpus/
│   ├── raw/
│   ├── parsed/
│   ├── candidates/
│   ├── canonical/
│   ├── verified/
│   ├── production/
│   ├── deprecated/
│   └── quarantine/
├── admin/
│   ├── benchmark/
│   ├── provenance/
│   ├── security/
│   ├── evaluator/
│   └── telemetry/
└── policies/
    ├── excellence/
    ├── privacy/
    ├── license/
    ├── security/
    └── promotion/
```

---

## 71. Storage Strategy

Use different stores for different problems.

```text
Relational DB
→ identity, versions, lifecycle, provenance, evaluation metadata

Object Store
→ repository snapshots, benchmark artifacts, logs

Vector Index
→ semantic discovery and similarity

Search Index
→ full-text and code metadata

Graph Layer (later, only if justified)
→ lineage and mechanism relationships
```

---

## 72. Suggested APIs

```text
POST /acquisition/opportunities
POST /acquisition/jobs
GET  /candidates/{id}
POST /candidates/{id}/evaluate
POST /candidates/{id}/benchmark
POST /candidates/{id}/refine
POST /capabilities/{id}/promote
POST /capabilities/{id}/demote
GET  /frontier/{domain}
GET  /demand/signals
```

MCP/A2A/SDK adapters can expose the relevant subset later.

---

## 73. Where Agents Should and Should Not Be Used

Use agents for semantic autonomy:

```text
Acquisition Planner Agent
Repo Analyst Agent
Capability Miner Agent
Architecture Judge
Engineering Judge
Domain Judge
Novelty Judge
Critic Agent
Refinement Agent
Verification Agent
```

Do not use agents for deterministic infrastructure tasks:

```text
hashing
git diff
permission enforcement
database integrity
metrics
state transitions
dependency graph extraction
basic license metadata
```

Each agent must have:

```text
specific role
limited tools
structured output schema
token budget
timeout
evidence requirement
confidence field
escalation rule
```

Avoid one uncontrolled “super-agent”.

---

## 74. LLM Failure Controls

Protect against:

- hallucinated architecture;
- invented files;
- fake benchmark claims;
- prestige bias;
- inconsistent scoring;
- overconfident novelty claims.

Require:

```text
file-level evidence
deterministic file-existence checks
structured evidence references
judge disagreement detection
critic pass
benchmark validation when executable
```

---

## 75. Core Design Rules

1. **Crawler is an executor, not the brain.**
2. **Demand Intelligence decides what deserves search attention.**
3. **Excellence Intelligence decides what deserves retention and promotion.**
4. **Demand influences acquisition, not quality judgment.**
5. **Popularity is a discovery signal, not a quality verdict.**
6. **Repositories are sources; mechanisms/capabilities are assets.**
7. **Use LLMs for semantic understanding and architectural judgment.**
8. **Use deterministic code for facts, gates, reproducibility and security.**
9. **Use benchmarks for executable claims.**
10. **Evaluate against the current corpus, never only in isolation.**
11. **Measure marginal capability gain.**
12. **Use multiple judges plus a skeptical critic.**
13. **Store evidence, counter-evidence and uncertainty.**
14. **Preserve provenance and licensing across refinement.**
15. **Promote gradually through verification and canary stages.**
16. **Continuously demote, replace and retire obsolete capabilities.**
17. **Protect client privacy and resist demand feedback loops.**
18. **Version the definition of excellence itself.**
19. **Start narrow and calibrate before scaling.**
20. **Optimize for measurable production improvement, not crawl throughput.**

---

## 76. Immediate Build Order

```text
STEP 1
Freeze these schemas:
- DemandSignal
- AcquisitionOpportunity
- DiscoveryCandidate
- CandidateCapability
- EvidencePackage
- ExcellenceVector

STEP 2
Create Excellence Constitution v0.1.

STEP 3
Build GitHub + official-watchlist discovery MVP.

STEP 4
Implement versioned raw snapshots, provenance, license and deterministic gates.

STEP 5
Implement Repo Mapper + LLM Architecture Analyst.

STEP 6
Implement LLM Capability Miner.

STEP 7
Implement mechanism-level dedup.

STEP 8
Implement first judge set:
- Architecture
- Engineering
- Novelty
- Capability Gain
- Critic

STEP 9
Build one reproducible coding benchmark suite.

STEP 10
Implement canonical refinement output.

STEP 11
Implement lifecycle:
Candidate → Reviewed → Verified → Canary → Production.

STEP 12
Collect production telemetry and feed it back into demand/gap acquisition.

STEP 13
Only after this loop works, optimize budgets, exploration and judge intelligence.
```

---

## 77. V2 Cut Line

A strong V2 does **not** need to autonomously understand the entire open-source world.

A strong V2 needs to reliably execute this closed loop for one domain:

```text
real demand / strategic signal
        ↓
find promising candidates
        ↓
understand them correctly
        ↓
extract reusable mechanisms
        ↓
compare with current corpus
        ↓
verify with evidence and benchmarks
        ↓
refine into platform-native capability
        ↓
promote safely
        ↓
learn from production
```

If this loop works well for coding-agent capabilities, the architecture is strong enough to scale toward V3.

---

## 78. Final System Map

```text
                           CLIENTS
                              │
                              ▼
                     Demand Intelligence
                              │
            ┌─────────────────┼──────────────────┐
            ▼                 ▼                  ▼
      Current Intent     Recent Demand       Corpus Gaps
            │                 │                  │
            └─────────────────┼──────────────────┘
                              ▼
                    Acquisition Planner
                              ▲
                  ┌───────────┼───────────┐
                  ▼           ▼           ▼
               Trends     Watchlists   Exploration
                  └───────────┼───────────┘
                              ▼
                           Search
                              │
                              ▼
                           Crawler
                              │
                              ▼
                      Raw Snapshot Store
                              │
                              ▼
                 Deterministic Hard Gates
                              │
                              ▼
                         Repo Mapper
                              │
                              ▼
                     LLM Understanding
                              │
                              ▼
                     Capability Mining
                              │
                              ▼
                  Mechanism-Level Dedup
                              │
                              ▼
                       Candidate Pool
                              │
            ┌─────────────────┼──────────────────┐
            ▼                 ▼                  ▼
        LLM Judges       Static Evidence      Benchmarks
            │                 │                  │
            └─────────────────┼──────────────────┘
                              ▼
                       Elite Comparator
                              │
                              ▼
                          Refiner
                              │
                              ▼
                          Verifier
                              │
                              ▼
                    Promotion Pipeline
                              │
                              ▼
                     Canonical Corpus
                              │
                              ▼
                    Capability Router
                              │
                              ▼
                           Client
                              │
                              ▼
                       Usage Telemetry
                              │
               ┌──────────────┴──────────────┐
               ▼                             ▼
       Demand Intelligence             Corpus Health
               │                             │
               └──────────────┬──────────────┘
                              ▼
                    Next Acquisition Cycle
```

---

## 79. Final Definition of the Subsystem

This subsystem is not a crawler in the traditional sense.

It is an **Adaptive Acquisition & Excellence Intelligence System** whose job is:

> continuously understand what capabilities matter, search the external world for promising mechanisms, extract the valuable pieces, establish evidence of their quality relative to the current frontier, refine them into governed platform-native capabilities, and continuously replace them when better evidence appears.

That definition should remain stable even if individual crawlers, LLMs, benchmarks, or agent frameworks change.

---

**End of `crawl.md`**
