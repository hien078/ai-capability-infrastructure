# AI Capability Infrastructure — Architecture & Implementation Plan V2

> **Status:** Proposed architecture — supersedes the original `plan.md` design.
>
> **Design date:** 2026-09-27
>
> **Primary goal:** Build a reusable, client-independent AI Capability Infrastructure that can route, package, expose, govern, evaluate, and evolve reusable AI capabilities without coupling the core platform to OpenCode, MCP, A2A, a specific model provider, or a specific agent runtime.

---

# 0. Executive Summary

The system is built around one conceptual abstraction:

> **Capability = a versioned, governed unit of reusable AI functionality or guidance that a client may discover, resolve, invoke, read, or delegate to.**

However, **Capability is not a single universal runtime object**. Different capability kinds have different semantics:

- a **skill** is resolved/loaded and applied by a host,
- a **tool** is invoked,
- a **resource** is read,
- a **workflow** is instantiated/executed,
- a **service** is called,
- an **agent** is delegated a task.

Therefore the platform uses a shared `CapabilityDescriptor` plus kind-specific specifications rather than one giant flat schema.

The core architecture is divided into three planes:

1. **Data Plane** — serves live client requests: search, route, resolve, invoke, delegate.
2. **Control Plane** — manages registry state, releases, promotion, provenance, policy, security review, and benchmarks.
3. **Observability Plane** — receives traces, outcomes, costs, verification evidence, and feedback.

The canonical data model separates:

```text
Capability identity
    ↓
Immutable CapabilityVersion
    ↓
CapabilityRelease / channel state
    ↓
CapabilityBinding / client-protocol exposure
    ↓
Derived CapabilityMetrics
```

This prevents mutable telemetry, release state, protocol exposure, and immutable content from being mixed into one record.

For OpenCode V2, the preferred integration is:

```text
OpenCode
  ↓ prompt hook
Thin OpenCode Adapter / Plugin
  ↓ REST routing request
Capability Infrastructure
  ↓ selected version-pinned skill IDs
OpenCode native skill resolution
  ↓
HTTP Skill Catalog served by this platform
  ↓
OpenCode executes locally using its own tools/permissions
```

MCP remains a first-class generic adapter. Procedural skills should be exposed using the MCP Skills extension where supported, while routing/outcome operations may be exposed as a small number of MCP tools.

A2A is reserved primarily for autonomous agent delegation across an agent boundary. It is not required in V1 and should not be used merely because two internal modules are called "agents".

V1 remains a modular monolith.

---

# 1. What This Architecture Is — and Is Not

## 1.1 This system is

A reusable capability infrastructure that can:

- ingest reusable AI assets,
- normalize and version them,
- preserve provenance and licensing metadata,
- govern which versions are safe for which clients,
- retrieve relevant capabilities for a task,
- rerank candidates,
- compose a minimal execution bundle,
- expose capabilities through multiple adapters,
- record real execution evidence,
- benchmark changes before promotion,
- evolve without coupling the domain core to a single client or protocol.

## 1.2 This system is not

It is not:

- an OpenCode fork,
- an MCP implementation disguised as a product,
- a giant prompt library,
- a microservice mesh from day one,
- an autonomous self-modifying agent,
- a replacement for client-local filesystem/shell/git permissions,
- a universal agent runtime in V1,
- a graph database project,
- a vector database product,
- a model gateway in V1.

---

# 2. Core Architectural Principles

## 2.1 Client independence

OpenCode, Claude Code, Codex, custom coding agents, research agents, and internal applications are consumers.

No core domain rule may depend on OpenCode-specific concepts.

Client-specific behavior belongs in adapters.

## 2.2 Protocol independence

MCP, A2A, and REST are protocol/transport boundaries.

SDKs are client libraries built on top of those interfaces, not protocols of equal architectural level.

Correct mental model:

```text
Application / Agent / Client
        │
        ├── direct REST
        ├── MCP
        ├── A2A
        └── SDK ──► REST/MCP/A2A
```

## 2.3 Capability is conceptual; semantics are kind-specific

Never assume all capability kinds can be `execute()`d.

Use explicit verbs:

```text
skill       → resolve / load / apply
resource    → read
service     → call
workflow    → instantiate / execute
tool        → invoke
agent       → delegate
```

## 2.4 Immutable content, mutable release state

A published `CapabilityVersion` is immutable.

Promotion does not modify a version. It changes a release/channel pointer.

Telemetry does not modify a version. It produces derived metrics.

## 2.5 One authoritative registry

There is exactly one authoritative Capability Registry.

There must not be a separate authoritative Skill Registry and Agent Registry containing conflicting lifecycle state.

Skill-specific and agent-specific services may exist, but they reference the same canonical registry.

## 2.6 Progressive disclosure

The runtime should not expose thousands of full capability bodies to the model.

Typical skill path:

```text
3000 production capabilities
        ↓ eligibility / policy
900 eligible
        ↓ metadata + semantic retrieval
20 candidates
        ↓ reranker
5 candidates
        ↓ dependency resolution + composer
0–5 selected skills
        ↓ lazy load
only required skill content enters context
```

## 2.7 Zero selected skills is valid

The router must be allowed to return no recommendation.

Forcing a capability when confidence is poor is worse than returning an empty bundle.

## 2.8 Local authority stays local

For coding clients, the remote platform may recommend procedures, but the client remains the authority for:

- filesystem access,
- shell execution,
- Git operations,
- local secrets,
- process execution,
- local permission prompts,
- local session state,
- local model/tool loop.

A skill must never implicitly grant tool permissions.

## 2.9 Modular monolith first

Logical boundaries first, physical distribution later.

Split a module into an independent service only after there is evidence for:

- independent scaling,
- different security boundary,
- reliability isolation,
- different ownership,
- heavy background workload,
- incompatible latency profile,
- independent deployment cadence.

---

# 3. Corrected North-Star Architecture

```text
                              CLIENT WORLD

        ┌──────────────┬──────────────┬──────────────┬───────────────┐
        │              │              │              │               │
     OpenCode      MCP Client      REST App      SDK Consumer    A2A Peer
        │              │              │              │               │
        ▼              ▼              ▼              ▼               ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                         EDGE / ADAPTER LAYER                            │
 │                                                                         │
 │ OpenCode Adapter | MCP Adapter | REST API | SDKs | A2A Gateway        │
 │                                                                         │
 │ AuthN | Rate Limit | Quota | Request Context | Protocol Translation   │
 └───────────────────────────────┬─────────────────────────────────────────┘
                                 │
                                 ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                        APPLICATION SERVICES                             │
 │                                                                         │
 │ SearchCapabilities                                                     │
 │ RouteCapabilities                                                      │
 │ ResolveCapability                                                      │
 │ ReadCapabilityArtifact                                                 │
 │ ReportOutcome                                                          │
 │ InvokeService            [later]                                       │
 │ DelegateAgentTask        [later]                                       │
 └───────────────────────────────┬─────────────────────────────────────────┘
                                 │
                                 ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                            DOMAIN CORE                                  │
 │                                                                         │
 │ Capability Registry                                                    │
 │ Capability Versions                                                    │
 │ Release Policy                                                         │
 │ Eligibility Policy                                                     │
 │ Taxonomy / Facets                                                      │
 │ Retrieval                                                              │
 │ Reranking                                                              │
 │ Dependency Resolution                                                  │
 │ Bundle Composition                                                     │
 └───────────────────────────────┬─────────────────────────────────────────┘
                                 │
                ┌────────────────┼─────────────────┐
                │                │                 │
                ▼                ▼                 ▼
        ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
        │ Skill        │  │ Service      │  │ Agent        │
        │ Provider     │  │ Provider     │  │ Provider     │
        │              │  │   [later]    │  │   [later]    │
        └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
               │                 │                 │
               └─────────────────┼─────────────────┘
                                 ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                              DATA LAYER                                 │
 │                                                                         │
 │ PostgreSQL | pgvector | Object Storage | Cache/Queue when justified   │
 └─────────────────────────────────────────────────────────────────────────┘


                         CONTROL PLANE

 Source Registry → Ingestion → Quarantine → Normalize → Security/License
      → Benchmark → Review → Release/Promote → Production


                      OBSERVABILITY PLANE

 Runtime Events → Traces → Outcome Evidence → Verification → Metrics
      → Benchmark datasets / routing analysis / promotion feedback
```

---

# 4. Plane Separation

## 4.1 Data Plane

The data plane serves runtime requests.

Primary properties:

- low latency,
- read-mostly registry access,
- deterministic policy enforcement,
- version-pinned output,
- bounded context size,
- no unsafe content mutation,
- traceable decisions.

Typical V1 request:

```text
route task
   ↓
eligibility
   ↓
retrieval
   ↓
rerank
   ↓
compose
   ↓
return immutable references to selected skill versions
```

## 4.2 Control Plane

The control plane changes what may exist in production.

Responsibilities:

- source registry,
- ingestion,
- provenance,
- canonicalization,
- license policy,
- security review,
- version creation,
- benchmark execution,
- promotion,
- rollback,
- deprecation,
- tenant/client policy,
- upstream change monitoring.

The control plane must not be a synchronous hop in every route request.

## 4.3 Observability Plane

Responsibilities:

- request traces,
- routing traces,
- candidate sets,
- selected bundle contents,
- client execution events,
- test outcomes,
- evaluator verdicts,
- human feedback,
- cost,
- tokens,
- latency,
- failures,
- data quality signals.

Observability generates evidence. It does not directly promote new versions.

---

# 5. Bounded Contexts

Use these logical bounded contexts even inside one repository/process.

```text
Identity & Access
Capability Catalog
Capability Lifecycle
Policy & Eligibility
Routing
Bundle Composition
Skill Content
Protocol Adapters
Outcome & Evidence
Benchmarking
Provenance & Licensing
Observability
```

Avoid module names that duplicate meaning, such as simultaneously having:

```text
capability/graph
skills/graph
skills/evaluator
evaluation/
capability/registry
agents/registry
```

Each concept should have one canonical home.

---

# 6. Capability Domain Model

## 6.1 Core identity

```text
Capability
```

Represents stable logical identity across versions.

Example:

```yaml
id: systematic-debugging
kind: skill
created_at: ...
owner_scope: global
```

The logical capability itself does not contain mutable benchmark results or protocol bindings.

## 6.2 CapabilityVersion

Immutable after publication.

```yaml
capability_id: systematic-debugging
version: 2.4.1
kind: skill
schema_version: 1
content_digest: sha256:...
created_at: ...

metadata:
  display_name: Systematic Debugging
  description: Evidence-driven debugging workflow.

facets:
  domains:
    - software-engineering
  task_types:
    - debugging
  concerns:
    - root-cause-analysis

spec:
  ... kind-specific specification ...
```

## 6.3 CapabilityRelease

Represents lifecycle/channel state.

```yaml
capability_id: systematic-debugging
version: 2.4.1
channel: production
status: active
promoted_at: ...
approved_by: ...
policy_snapshot_id: ...
```

Possible channels (delivery only — trust states live in `IngestionStatus`
on the source record, §21):

```text
staging
production
```

Possible release states:

```text
active
disabled
deprecated
revoked
```

Do not encode release state by physically moving source directories as the authoritative mechanism.

## 6.4 CapabilityBinding

Describes how a specific version/release is exposed.

Examples:

```text
OpenCode HTTP Skill Catalog
MCP Skills extension
REST endpoint
A2A Agent Card
internal application binding
```

Example:

```yaml
binding_id: bind_123
capability_id: systematic-debugging
version: 2.4.1
binding_type: opencode-http-skill
visibility_scope: public-production
config:
  skill_id: systematic-debugging
  autoinvoke: false
```

Protocol exposure is not intrinsic capability content.

## 6.5 CapabilityMetrics

Derived from observations.

Examples:

```text
usage_count
verified_success_rate
human_override_rate
avg_context_tokens
avg_total_tokens
avg_latency_ms
error_rate
regression_rate
```

Metrics must not mutate historical `CapabilityVersion` records.

---

# 7. Capability Kinds

Initial top-level kinds:

```text
skill
resource
tool
workflow
service
agent
```

Avoid using these as top-level kinds:

```text
evaluator
memory
integration
model
```

Those are generally better represented as roles, providers, or implementation categories.

Examples:

```yaml
kind: service
roles:
  - evaluator
```

```yaml
kind: service
roles:
  - memory
```

```yaml
kind: tool
roles:
  - browser
```

---

# 8. Kind-Specific Specifications

Use a discriminated union.

Pseudo-Pydantic:

```python
CapabilitySpec = Annotated[
    SkillSpec
    | ToolSpec
    | ResourceSpec
    | WorkflowSpec
    | ServiceSpec
    | AgentSpec,
    Field(discriminator="kind"),
]
```

Do not create one object with dozens of nullable fields.

## 8.1 SkillSpec

```yaml
kind: skill
entrypoint: SKILL.md
artifacts:
  - SKILL.md
  - references/checklist.md

provides:
  - root-cause-analysis
  - debugging-procedure

requirements:
  context:
    - task-description
  optional_context:
    - logs
    - repository-summary

permissions:
  requested_tools: []

side_effects: none

routing_hints:
  task_types:
    - debugging
```

The skill may contain instructions that suggest tools, but `requested_tools` never grants permission.

## 8.2 ToolSpec

```yaml
kind: tool
input_schema: ...
output_schema: ...
side_effects: read-only
idempotency: safe
required_permissions:
  - repository.read
```

## 8.3 ResourceSpec

```yaml
kind: resource
media_type: text/markdown
mutability: immutable
access_mode: read
```

## 8.4 WorkflowSpec

```yaml
kind: workflow
steps:
  - ...
execution_mode: client-orchestrated
resume_supported: false
```

## 8.5 ServiceSpec

```yaml
kind: service
operation: technical-research
request_schema: ...
response_schema: ...
execution_mode: remote
side_effects: read-only
```

## 8.6 AgentSpec

```yaml
kind: agent
provides:
  - code-review
supported_input_modes:
  - text
supported_output_modes:
  - text
  - artifact
execution_semantics: delegated-autonomy
```

Agent runtime configuration belongs to an agent profile/runtime subsystem, not necessarily inside the immutable public descriptor.

---

# 9. Capability Roles and Facets

Do not force the catalog into one rigid tree.

Use faceted classification.

Example:

```yaml
domain:
  - software-engineering

task_type:
  - debugging

lifecycle_phase:
  - implementation
  - verification

technology:
  - typescript

concern:
  - authentication
  - concurrency

artifact_type:
  - backend-service

risk_class:
  - medium
```

Hierarchical taxonomy may still be used inside individual facets.

This avoids ambiguity such as whether an authentication debugging skill belongs under:

```text
software-engineering/debugging
```

or:

```text
vibe-coding/auth
```

It can belong to both relevant facets without duplicate capability identity.

---

# 10. One Registry, Multiple Providers

The authoritative source of truth is:

```text
Capability Registry
```

Do not create independent lifecycle registries for:

```text
skills
agents
services
```

Instead:

```text
Capability Registry
   ├── Capability(kind=skill)
   ├── Capability(kind=service)
   └── Capability(kind=agent)
```

Providers implement kind-specific behavior:

```text
SkillProvider
ServiceProvider
AgentProvider
```

But they reference the same `capability_id + version` identities.

---

# 11. Request Model: Envelope + Typed Operations

Do not normalize every incoming request into one universal `CapabilityRequest`.

Use a common request envelope plus typed commands/queries.

## 11.1 RequestContext

```yaml
request_id: uuid
trace_id: uuid
principal_id: ...
organization_id: ... optional
workspace_id: ... optional
client:
  type: opencode
  version: ...
protocol:
  type: rest
  version: ...
deadline: ...
locale: ...
```

## 11.2 SearchCapabilitiesQuery

```yaml
query: "authentication debugging"
filters:
  kinds:
    - skill
  domains:
    - software-engineering
limit: 20
```

## 11.3 RouteCapabilitiesCommand

```yaml
task:
  text: "Fix an intermittent authentication race condition"

context:
  language: typescript
  frameworks:
    - nextjs
  phase: debugging
  repository_summary: optional-minimal-summary

constraints:
  max_items: 5
  max_context_tokens: 6000
  allowed_kinds:
    - skill
```

## 11.4 ResolveCapabilityQuery

```yaml
capability_id: systematic-debugging
version: 2.4.1
artifact: SKILL.md
```

## 11.5 ReportOutcomeCommand

Carries evidence and observations, not merely `success: true`.

## 11.6 DelegateAgentTaskCommand

Future operation. Maps naturally to A2A but remains separate from skill routing semantics.

---

# 12. Application Services

V1 application services:

```text
SearchCapabilities
RouteCapabilities
ResolveCapability
ReadCapabilityArtifact
ReportOutcome
```

Future:

```text
InvokeService
StartWorkflow
DelegateAgentTask
GetDelegatedTask
CancelDelegatedTask
```

The domain core should not know whether these were called via REST, MCP, OpenCode plugin, or SDK.

---

# 13. Capability Gateway Reframed

The old concept of one "Capability Gateway" containing normalization, auth, routing, and protocol logic can become a god object.

Instead use:

```text
Edge Layer
    ↓
Application Service Layer
    ↓
Domain Core
```

The term `gateway` may remain as deployment terminology, but internally it must stay thin.

Edge responsibilities:

- authenticate caller,
- establish `RequestContext`,
- validate transport-level input,
- apply coarse rate limits,
- translate protocol representation,
- call application service,
- translate result back.

It must not contain:

- retrieval logic,
- reranker logic,
- benchmark policy,
- promotion logic,
- skill canonicalization,
- client-specific business rules in generic handlers.

---

# 14. Routing Pipeline

V1 route flow:

```text
Route request
    ↓
Task normalization
    ↓
Eligibility / Policy filter
    ↓
Metadata + facet filtering
    ↓
Semantic retrieval
    ↓
Candidate set
    ↓
Reranker
    ↓
Dependency resolution
    ↓
Bundle composer
    ↓
Bundle validation
    ↓
Version pinning
    ↓
Return CapabilityBundle
```

Each stage emits trace data.

---

# 15. Eligibility / Policy Layer

This layer runs before expensive semantic retrieval where practical.

A capability may be excluded for:

- wrong lifecycle state,
- not in production channel,
- revoked version,
- client incompatibility,
- protocol incompatibility,
- tenant restriction,
- workspace restriction,
- license restriction,
- trust tier,
- missing required client feature,
- prohibited permission requirement,
- unsupported language/framework,
- policy conflict,
- explicit deny rule.

Example:

```python
eligible = policy_engine.filter(
    principal=context.principal,
    client=context.client,
    task=task,
    capabilities=production_capabilities,
)
```

The reranker must never be expected to fix security or authorization mistakes made earlier.

---

# 16. Retrieval Layer

Purpose:

> Maximize recall while keeping the candidate set small enough for stronger ranking.

V1 retrieval:

```text
Facet filter
    +
Metadata keyword/BM25-like matching where useful
    +
pgvector semantic similarity
    +
optional historical priors
```

Output target:

```text
10–30 candidates
```

Do not use a specialized vector database in V1 unless measurements show PostgreSQL + pgvector is inadequate.

## 16.1 Embedding policy

Embed trusted normalized summaries, not arbitrary raw third-party prompt text by default.

Suggested embedding document:

```text
name
description
provides
task types
domain facets
technology facets
trusted derived summary
```

This reduces prompt-injection exposure in later LLM-based routing.

---

# 17. Reranker Layer

Stable interface:

```python
class CapabilityReranker(Protocol):
    def rerank(
        self,
        task: TaskDescriptor,
        candidates: list[EligibleCandidate],
        context: RoutingContext,
    ) -> list[RankedCandidate]:
        ...
```

Implementations may include:

```text
HeuristicReranker
EmbeddingReranker
DirectLLMReranker
CrossEncoderReranker
HybridReranker
JevReranker       [experimental]
```

JEV is not architecture-critical.

## 17.1 Reranker security rule

Do not inject full untrusted raw skill bodies into an LLM reranker.

The reranker should consume trusted/sanitized routing metadata.

Full skill content is loaded only after eligibility and selection, and only from a promoted/pinned artifact.

---

# 18. Dependency Resolution

After reranking, resolve relationships such as:

```text
REQUIRES
SUPPORTS
CHECKS
CONFLICTS_WITH
AVOIDS
SPECIALIZES
GENERALIZES
REPLACES
DEPRECATED_BY
```

V1 may implement only:

```text
REQUIRES
CONFLICTS_WITH
CHECKS
```

Do not introduce a graph database in V1.

PostgreSQL table:

```text
capability_relations(
    source_capability_id,
    source_version_constraint,
    target_capability_id,
    target_version_constraint,
    relation,
    metadata
)
```

---

# 19. Bundle Composer

The composer optimizes for **minimal sufficient capability context**, not maximum capability count.

Objective conceptually:

```text
maximize expected task utility
-
context cost
-
conflict risk
-
latency cost
```

A bundle with one strong skill may be better than five moderately relevant skills.

## 19.1 CapabilityBundle

```yaml
bundle_id: bun_...
route_run_id: route_...
created_at: ...

items:
  - capability_id: systematic-debugging
    version: 2.4.1
    digest: sha256:...
    kind: skill
    role: primary
    load_mode: lazy
    reason_code: root-cause-debugging-match

  - capability_id: regression-verification
    version: 1.3.0
    digest: sha256:...
    kind: skill
    role: check
    load_mode: lazy

execution_order:
  - systematic-debugging
  - regression-verification

guardrails:
  - do-not-patch-before-reproduction

budget:
  max_items: 5
  max_context_tokens: 6000

policy_snapshot_id: pol_...
expires_at: ...
```

Valid bundle size:

```text
0..N, normally N <= 5
```

A zero-item bundle is a normal successful routing result.

---

# 20. Skill Content Model

A canonical skill is stored as an immutable content package.

Recommended structure:

```text
skill-package/
├── SKILL.md
├── references/
├── examples/
├── scripts/
└── assets/
```

The content package has:

```text
manifest
file list
per-file hash
package digest
source provenance
license metadata
security scan status
```

The package digest is used for integrity and release pinning.

---

# 21. Skill Lifecycle

The lifecycle is TWO SEPARATE state machines, never one:

```text
INGESTION STATE MACHINE (trust)          RELEASE STATE MACHINE (delivery)
                                         
ACQUISITION (manual, §22)               (no release pointer exists)
  ↓                                        
SOURCE SNAPSHOT                          STAGING ← explicit promotion
  ↓                                        ↓
QUARANTINE (ingestion_status)           PRODUCTION ← explicit promotion
  ↓                                        ↓
INGESTION GATES G1–G4                   deprecated / revoked (status flips)
  ↓
CANONICALIZATION (normalizer_version)
  ↓
IMMUTABLE CAPABILITY VERSION
  ↓
accepted → eligible for a STAGING release
```

`IngestionStatus` (quarantined / rejected / normalized / accepted) lives on
the provenance source record. `ReleaseChannel` (staging / production) is a
delivery pointer. **"Raw" is an ingestion state, not a release channel** —
a quarantined skill has NO release pointer at all and is therefore invisible
to every client surface (catalog, routing, MCP) by construction, not by
filtering.

Full pipeline:

```text
CAPABILITY GAP
  ↓
ACQUISITION (§22, manual in V1)
  ↓
SOURCE SNAPSHOT (pinned revision)
  ↓
QUARANTINE (untrusted: stored, hashed, inspectable — never advertised)
  ↓
INGESTION GATES: G1 structure · G2 provenance · G3 license policy · G4 security policy
  ↓
CANONICALIZATION (deterministic, auditable, normalizer_version recorded)
  ↓
IMMUTABLE CAPABILITY VERSION
  ↓
STAGING (explicit pointer move)
  ↓
PROMOTION GATES: G5 compatibility · G6 benchmark · G7 regression · G8 policy
  ↓
PRODUCTION (explicit pointer move)
  ↓
DELIVERY (OpenCode catalog / MCP / REST — projections of production only)
  ↓
OUTCOME / TELEMETRY
```

Do not make folder location the authoritative lifecycle state.

Directories such as:

```text
corpus/raw
corpus/candidate
```

may exist for human convenience, but database records remain authoritative:
`source_records.ingestion_status` for trust, `capability_releases` for
delivery.

---

# 22. Raw Skill Acquisition

Use capability-gap-driven acquisition.

```text
Observed capability gap
        ↓
Define target behavior
        ↓
Search high-quality sources
        ↓
Collect 2–5 candidates
        ↓
License + provenance review
        ↓
Security quarantine
        ↓
Normalize / compare
        ↓
Canonical version
```

Avoid collecting thousands of repositories without a capability hypothesis.

## 22.1 V1 acquisition is intentionally manual — and declarative

No crawler, no automatic Internet-wide discovery, no scheduled upstream
monitoring (§38 watcher is manual-run). WHAT to ingest lives in a
declarative source registry (`config/sources.yaml`): repo, pinned commit,
picked skill paths, optional license override. The ingestion engine
(`scripts/ingest_real_skills.py`) only consumes that file — adding a source
or a skill never requires editing engine logic. Human review of content
happens BEFORE ingestion (reading the candidates); the snapshot/quarantine
step (§21) is unconditional and precedes any trust decision, so "ingested"
never means "trusted".

---

# 23. Provenance Model

Every source ingestion should retain an event trail.

Minimum fields:

```text
source_type
source_repository
source_path
source_url_reference
commit_sha / source revision
source_version
content_hash
license_identifier
license_text_reference
author / organization if available
ingested_at
ingestion_tool_version
local_transformations
derived_from
review_events
security_scan_events
benchmark_events
promotion_events
```

Canonicalization must not erase lineage.

---

# 24. Licensing Policy

A simple `license: MIT` field is not enough if capabilities may later be distributed commercially.

Normalize legal-operational permissions into machine-readable policy fields where possible:

```text
can_ingest
can_modify
can_store
can_redistribute
commercial_use_allowed
attribution_required
share_alike_required
internal_only
unknown_requires_review
```

Unknown or ambiguous license state should block production redistribution by default.

License policy belongs in control-plane eligibility, not in LLM judgment.

---

# 25. Security Architecture

Security is an execution model, not a checklist.

## 25.1 Trust boundaries

```text
External Source
    │  untrusted
    ▼
Ingestion Quarantine
    │
    ▼
Normalizer / Scanner
    │
    ▼
Reviewed Canonical Artifact
    │
    ▼
Production Release
    │
    ▼
Client Adapter
    │
    ▼
Client Permission Boundary
    │
    ▼
Local Tools / Remote Services
```

## 25.2 Threat matrix

| Boundary | Threat | Required control |
|---|---|---|
| external source → ingestion | malicious instructions | quarantine, provenance, static scan, review |
| ingestion → canonical | hidden privilege escalation | transformation diff, normalization policy |
| raw content → LLM reranker | prompt injection | trusted routing summaries only |
| platform → client | content substitution | version pin + digest |
| client → API | spoofed identity | authentication |
| caller → capability | unauthorized use | authorization + scope policy |
| skill → local tools | permission escalation | client remains final permission authority |
| scripts → runtime | arbitrary code | explicit executable classification + sandbox |
| remote fetch → network | SSRF/exfiltration | egress policy |
| telemetry → storage | secret/code leakage | redaction + minimization + retention policy |
| tenant A → tenant B | cross-tenant leakage | organization/workspace-aware authorization |
| cache → client | stale/revoked skill | release/version validation + TTL/revocation handling |

## 25.3 Instruction does not equal authority

Core invariant:

> A capability may request or recommend an operation, but only the execution host can grant permission to perform it.

A `SKILL.md` containing shell commands must not bypass OpenCode's permission system.

## 25.4 Secret handling

The central routing platform should not require raw repository secrets.

Never send:

- `.env` contents,
- tokens,
- SSH keys,
- cloud credentials,
- package registry secrets,
- unrelated source files,

for capability routing.

---

# 26. Privacy and Minimal Context

Replace generic `repo_context` with a typed `TaskContext`.

Example:

```yaml
language: typescript
frameworks:
  - nextjs
phase: debugging
error_class: race-condition
file_hints:
  - src/auth/session.ts
repository_summary: "Session refresh path uses shared mutable cache"
```

Default rule:

> Send the minimum information required to route capabilities.

Raw source code should remain client-local unless an explicitly chosen remote service requires it and policy permits it.

---

# 27. Multi-Tenancy Foundations

Even if V1 is single-user, reserve the identity model for future tenancy.

Core concepts:

```text
Principal
Organization
Workspace
PolicyScope
```

Do not blindly add `tenant_id` to every table before needed, but ensure access-control APIs accept scope context from the start.

Capability visibility may be:

```text
global
organization
workspace
private
```

---

# 28. OpenCode V2 Integration — Preferred Architecture

OpenCode is a special client adapter, not the platform core.

Preferred flow:

```text
User prompt
    ↓
OpenCode prompt admission
    ↓
Thin OpenCode plugin hook
    ↓
extract minimal routing context
    ↓
POST /v1/routes
    ↓
Capability Infrastructure
    ↓
CapabilityBundle with 0–5 version-pinned skill IDs
    ↓
plugin adds selected skill IDs to the prompt
    ↓
OpenCode native skill resolution
    ↓
HTTP Skill Catalog served by platform
    ↓
OpenCode lazily loads selected skill content
    ↓
model/tool loop
    ↓
OpenCode local permissions + filesystem + shell + git
    ↓
plugin/client instrumentation
    ↓
POST /v1/outcomes
```

## 28.1 Why this is preferred

It uses OpenCode's native skill lifecycle instead of sending large skill bodies inside a custom MCP tool response.

The platform remains responsible for intelligence:

```text
eligibility
retrieval
reranking
composition
version selection
```

OpenCode remains responsible for host behavior:

```text
skill resolution
local context
local tools
permissions
execution loop
```

## 28.2 HTTP Skill Catalog

Expose production skill releases in an OpenCode-compatible catalog.

The catalog is an adapter over canonical capability artifacts.

It is not a second source of truth.

Conceptual mapping:

```text
CapabilityVersion + CapabilityRelease + CapabilityBinding
        ↓
OpenCode catalog index entry
        ↓
versioned skill files
```

## 28.3 Autoinvoke policy

For centrally routed skills, prefer disabling automatic global model exposure where practical.

The router selects skill IDs, then the OpenCode adapter injects only selected skills.

This preserves progressive disclosure and avoids advertising the entire catalog to the model.

## 28.4 Adapter failure behavior

Routing outage policy must be explicit.

Possible modes:

```text
fail-open:
    continue prompt without platform-selected skills

fail-closed:
    reject/hold prompt because a mandatory policy capability could not be resolved
```

Default developer workflow:

```text
fail-open
```

Compliance/security-controlled workflows may choose fail-closed.

## 28.5 OpenCode-specific code location

All OpenCode-specific logic belongs under:

```text
adapters/inbound/opencode/
```

No domain module may import an OpenCode-specific type.

---

# 29. MCP Integration — Updated Design

MCP is a generic client protocol adapter.

For modern MCP implementations, support the current stateless request model and explicit protocol negotiation rather than relying on server-side session state.

## 29.1 Procedural skills

Where supported, expose canonical skills using the MCP Skills extension rather than inventing hundreds of individual skill tools.

Conceptual mapping:

```text
Canonical Skill Package
      ↓
MCP Skills binding
      ↓
skills/list
skills/get
resources/read
```

Skill files should remain digest-verifiable and version-bound by the platform.

## 29.2 Routing tools

Keep custom MCP tools small and stable.

Recommended V1 custom tools:

```text
route_capabilities(...)
report_outcome(...)
```

Optional:

```text
search_capabilities(...)
```

Do not create one MCP tool per skill.

## 29.3 Capability content

Use the protocol's resource/skill mechanisms for content where possible.

Do not overload tool results with enormous instruction bodies if the client can lazily fetch content.

## 29.4 MCP request state

Application state must not depend on a long-lived MCP session existing.

Persist any durable platform state explicitly using identifiers such as:

```text
request_id
route_run_id
bundle_id
principal_id
workspace_id
```

## 29.5 Long-running work

If future services need deferred MCP execution, the MCP Tasks extension may be used where client/server support is available.

This does not replace the A2A domain model for autonomous cross-agent delegation.

---

# 30. A2A Integration — Future Boundary

Mental model retained:

```text
MCP = use/read/invoke a capability
A2A = delegate responsibility to an autonomous agent
```

This is an architectural convention, not a claim that protocols are mutually exclusive.

A2A should be introduced when:

- an autonomous agent has an independently addressable identity,
- it accepts delegated tasks,
- work may be long-running,
- task state matters,
- messages/status/artifacts must cross a system boundary,
- interoperability with external agent systems is valuable.

Do not use A2A merely for method calls between Planner and Coder objects inside one process.

## 30.1 Future A2A mapping

```text
Capability(kind=agent)
       ↓
CapabilityBinding(type=a2a)
       ↓
Agent Card exposure
       ↓
A2A Task / Message / Artifact lifecycle
```

## 30.2 Naming collision

Distinguish:

```text
ProceduralSkill
```

from an A2A agent's declared skill/capability description.

Do not use ambiguous internal field names such as `skill.skills.skills`.

---

# 31. SDK Architecture

SDKs are convenience clients.

```text
Python SDK
TypeScript SDK
```

V1 SDKs should primarily wrap REST.

Responsibilities:

- authentication headers,
- serialization,
- typed models,
- pagination,
- deadlines,
- safe retries,
- idempotency keys where applicable,
- streaming abstraction later,
- consistent error types.

## 31.1 Retry policy

Do not auto-retry all operations.

Safe examples:

```text
GET/resolve
search
route if explicitly designed idempotent
```

Potentially unsafe:

```text
invoke side-effecting service
delegate agent task
promote release
create artifact
```

Those require idempotency semantics.

---

# 32. Execution Semantics

V1 skill platform does not remotely execute coding work.

Flow:

```text
platform selects guidance
client loads guidance
client executes locally
client reports evidence
```

Future remote capability semantics must be explicit.

Use fields such as:

```text
execution_mode:
    client_applied
    remote_call
    delegated_task
    read_only

side_effect_class:
    none
    read_only
    local_write
    remote_write
    external_effect
```

Avoid a universal `execute_capability()` API.

---

# 33. Outcome and Evidence Model

Never reduce outcome telemetry to:

```json
{"success": true}
```

Use evidence provenance.

Example:

```yaml
outcome_id: out_...
route_run_id: route_...
bundle_id: bun_...

client_report:
  status: completed

observations:
  tests_before:
    passed: 31
    failed: 4
  tests_after:
    passed: 35
    failed: 0
  lint_passed: true
  build_passed: true
  changed_files: 3
  tool_calls: 17

verdicts:
  - source: test_harness
    status: success
    confidence: high

  - source: agent_self_report
    status: success
    confidence: low

human_feedback:
  corrected: false

cost:
  input_tokens: ...
  output_tokens: ...
  estimated_usd: ...

latency_ms: ...
```

## 33.1 Verdict sources

Track separately:

```text
agent_self_report
client_report
test_harness
static_analysis
external_evaluator
human_review
production_signal
```

Do not merge them into one boolean before storage.

---

# 34. Causal Evaluation Principle

A task succeeding with Skill A selected does not prove Skill A caused success.

Benchmarks need baselines/control variants.

Recommended experiment matrix:

```text
A — client alone
B — client + native manually curated skills
C — client + platform retrieval only
D — client + retrieval + reranker
E — client + retrieval + reranker + composer
```

Compare:

```text
task success
test success
regressions
human corrections
input tokens
output tokens
latency
cost
tool calls
retry count
```

The primary thesis to validate is:

> Does the infrastructure improve end-to-end task outcomes enough to justify its complexity and cost?

---

# 35. Benchmark Framework

Each benchmark case:

```yaml
task_id: auth-race-001
category: debugging
fixture: repo/auth-race-v1

task:
  text: "Fix intermittent authentication failures under concurrent refreshes."

relevant_capabilities:
  strong:
    - systematic-debugging
    - concurrency-analysis
  acceptable:
    - auth-debugging
  irrelevant:
    - css-layout

acceptance_tests:
  - test_refresh_race
  - test_no_duplicate_session

forbidden_actions:
  - disable_authentication
  - delete_tests

budget:
  max_cost_usd: ...
  max_latency_ms: ...
```

`relevant_capabilities` helps evaluate routing but must not define the only acceptable solution path.

Primary score comes from task acceptance criteria.

## 35.1 Benchmark scale

Suggested progression:

```text
10 tasks      → smoke benchmark
30–50 tasks   → development benchmark
100+ tasks    → stronger per-category signal
```

Do not make architecture-wide conclusions from ten tasks.

---

# 36. Telemetry Model

Every route run records:

```text
route_run_id
request_id
trace_id
principal/workspace scope
client type/version
task category
normalized routing features
policy snapshot
eligible count
retrieval configuration
retrieved candidate IDs + versions + scores
reranker implementation/version
reranked candidates + scores
composer implementation/version
selected bundle
routing latency
routing token usage
errors
created_at
```

Every outcome event records its source and evidence.

Avoid storing raw secrets or unnecessary code in telemetry.

---

# 37. Promotion Pipeline

Two gate sets, never collapsed (§21 state machines):

```text
Upstream change / new source
        ↓
INGESTION GATES (untrusted → accepted)
  G1 structure/schema validity   (enforced before persistence)
  G2 provenance integrity         (source record exists)
  G3 license policy               (redistributable, §24)
  G4 security policy             (scan passed, §25)
        ↓  pass → STAGING (explicit pointer move)
        ↓  fail → stays QUARANTINED (never any release pointer)

PROMOTION GATES (staging → production)
  G5 client/runtime compatibility
  G6 benchmark suite
  G7 regression comparison
  G8 review / promotion policy
        ↓  pass → PRODUCTION (explicit pointer move)
```

A security pass is NOT production readiness; a license pass is NOT
production readiness; schema validity is NOT production readiness. Each
gate answers its own question only.

Where the benchmark infrastructure is not yet mature enough to gate G6/G7
automatically, promotion is an explicit MANUAL/DEVELOPMENT decision
(`approved_by` recorded on the release) — never a faked automated gate.

Never:

```text
upstream update → automatic production replacement
```

## 37.1 Rollback

Production promotion must be reversible by changing a release pointer/state.

Do not require rebuilding capability content to roll back.

---

# 38. Upstream Synchronization

An upstream watcher may later detect:

```text
new commit
new tag
changed file
license change
repository archived
security advisory
```

Detected changes create new ingestion candidates.

They do not mutate an existing production version.

---

# 39. Content Integrity

Every immutable capability artifact should have:

```text
per-file SHA-256
package digest
manifest digest
```

Bindings should expose enough information for clients/adapters to detect stale or substituted content.

The selected bundle should include pinned version + digest references.

---

# 40. Storage Architecture

## 40.1 PostgreSQL

Authoritative structured state:

- capability identities,
- capability versions,
- releases,
- bindings,
- facets/taxonomy,
- relationships,
- provenance events,
- policy records,
- routing traces,
- bundle metadata,
- outcome metadata,
- benchmark definitions/results.

## 40.2 pgvector

Embeddings for:

- trusted capability routing documents,
- task examples,
- optional historical routing traces.

## 40.3 Object storage / filesystem

Immutable blobs:

- raw imported source snapshots,
- canonical skill packages,
- scripts,
- references,
- benchmark fixtures,
- benchmark artifacts,
- reports.

Prefer content-addressed storage where practical.

## 40.4 Redis / queue

Do not add by default.

Add when justified by:

- background ingestion jobs,
- benchmark workers,
- distributed execution,
- high-value caching,
- task queues,
- rate limiting at scale.

---

# 41. V1 Database Schema

Recommended minimum tables:

```text
capabilities
capability_versions
capability_releases
capability_bindings

capability_facets
facet_nodes
capability_relations

capability_artifacts
artifact_files

provenance_events
source_records            -- carries ingestion_status (quarantined/rejected/normalized/accepted, §21)
license_assessments
security_assessments

embedding_documents
embedding_vectors

route_runs
route_candidates
bundles
bundle_items

outcome_events
outcome_verdicts

benchmark_cases
benchmark_runs
benchmark_results

policy_snapshots
```

## 41.1 Important constraints

Examples:

```text
UNIQUE(capability_id, version)
UNIQUE(capability_id, channel) for one active channel pointer if modeled that way
immutable published capability_version rows
foreign-key bundle items to exact versions
```

Use migrations from day one.

---

# 42. Repository Structure — Revised

Use valid Python package names and clearer boundaries.

```text
ai_capability_infrastructure/
│
├── pyproject.toml
├── README.md
├── docs/
│   ├── architecture.md
│   ├── threat_model.md
│   ├── protocol_bindings.md
│   └── adr/
│
├── src/
│   └── aci/
│       │
│       ├── domain/
│       │   ├── capability/
│       │   │   ├── models.py
│       │   │   ├── versions.py
│       │   │   ├── releases.py
│       │   │   ├── bindings.py
│       │   │   └── relations.py
│       │   │
│       │   ├── taxonomy/
│       │   ├── policy/
│       │   ├── routing/
│       │   ├── bundles/
│       │   └── outcomes/
│       │
│       ├── application/
│       │   ├── search_capabilities.py
│       │   ├── route_capabilities.py
│       │   ├── resolve_capability.py
│       │   ├── read_artifact.py
│       │   └── report_outcome.py
│       │
│       ├── providers/
│       │   ├── skills/
│       │   │   ├── package.py
│       │   │   ├── ingestion.py
│       │   │   ├── canonicalization.py
│       │   │   └── catalog.py
│       │   ├── services/
│       │   └── agents/
│       │
│       ├── routing/
│       │   ├── eligibility.py
│       │   ├── retrieval.py
│       │   ├── rerankers/
│       │   ├── dependencies.py
│       │   └── composer.py
│       │
│       ├── adapters/
│       │   ├── inbound/
│       │   │   ├── rest/
│       │   │   ├── mcp/
│       │   │   ├── opencode/
│       │   │   └── a2a/          # future
│       │   │
│       │   └── outbound/
│       │       ├── postgres/
│       │       ├── pgvector/
│       │       ├── object_store/
│       │       ├── model_provider/
│       │       └── telemetry/
│       │
│       ├── control_plane/
│       │   ├── sources/
│       │   ├── provenance/
│       │   ├── licensing/
│       │   ├── security/
│       │   ├── promotion/
│       │   ├── versioning/
│       │   └── upstream_watch/
│       │
│       ├── evaluation/
│       │   ├── benchmarks/
│       │   ├── harness/
│       │   ├── evaluators/
│       │   └── reports/
│       │
│       ├── observability/
│       │   ├── tracing.py
│       │   ├── metrics.py
│       │   ├── events.py
│       │   └── redaction.py
│       │
│       └── config/
│
├── sdk/
│   ├── python/
│   └── typescript/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── protocol/
│   ├── security/
│   ├── benchmark/
│   └── end_to_end/
│
└── migrations/
```

Do not create all future folders immediately. This is the target logical layout.

---

# 43. REST API — V1

Recommended external API:

```text
GET  /health
GET  /ready

POST /v1/capabilities/search
GET  /v1/capabilities/{capability_id}
GET  /v1/capabilities/{capability_id}/versions/{version}

POST /v1/routes
GET  /v1/routes/{route_run_id}

GET  /v1/bundles/{bundle_id}

POST /v1/outcomes
```

OpenCode catalog adapter:

```text
GET /opencode/skills/index.json
GET /opencode/skills/{skill_id}/{file_path}
```

Administrative/control-plane APIs should be separated from public runtime APIs.

Example:

```text
/admin/v1/...
```

and protected by stronger authorization.

---

# 44. Internal Application Interfaces

Suggested interfaces:

```python
class CapabilityRepository(Protocol): ...
class ReleaseRepository(Protocol): ...
class ArtifactStore(Protocol): ...
class EligibilityPolicy(Protocol): ...
class CandidateRetriever(Protocol): ...
class CapabilityReranker(Protocol): ...
class DependencyResolver(Protocol): ...
class BundleComposer(Protocol): ...
class OutcomeRecorder(Protocol): ...
```

Application services depend on protocols/interfaces, not directly on FastAPI, SQLAlchemy, MCP, or OpenCode types.

---

# 45. Error Model

Use stable machine-readable errors.

Examples:

```text
CAPABILITY_NOT_FOUND
CAPABILITY_VERSION_NOT_FOUND
CAPABILITY_NOT_ELIGIBLE
CAPABILITY_REVOKED
BUNDLE_VALIDATION_FAILED
ROUTING_TIMEOUT
POLICY_DENIED
ARTIFACT_INTEGRITY_ERROR
CLIENT_INCOMPATIBLE
RATE_LIMITED
AUTHENTICATION_REQUIRED
PERMISSION_DENIED
```

Transport adapters translate these into protocol-specific errors.

---

# 46. Caching

Safe cache targets:

- capability descriptors,
- production release maps,
- trusted routing embeddings,
- OpenCode catalog index,
- immutable skill artifacts.

Cache keys must include version/digest where content correctness matters.

Revocation must bypass or invalidate stale cache entries.

Do not cache authorization decisions indefinitely.

---

# 47. Versioning Strategy

Distinguish:

```text
schema version
capability semantic version
artifact digest
binding version
API version
router implementation version
policy snapshot version
```

Do not overload one `version` field to mean all of these.

Example route trace:

```yaml
router:
  implementation: hybrid-reranker
  version: 0.3.2

policy_snapshot: pol_2026_09_27_01

selected:
  capability_id: systematic-debugging
  capability_version: 2.4.1
  artifact_digest: sha256:...
```

---

# 48. Compatibility Model

A capability may declare compatibility metadata such as:

```text
supported_clients
minimum_client_features
supported_languages
supported_frameworks
required_protocol_extensions
required_artifact_features
```

Compatibility is a filter, not a reranker preference.

---

# 49. V1 Scope — Revised

V1 includes:

```text
Capability Registry
CapabilityVersion
CapabilityRelease
CapabilityBinding
Skill Content Provider
Provenance-aware local ingestion
Canonical skill format
Faceted taxonomy
Eligibility policy
PostgreSQL + pgvector retrieval
Baseline reranker interface
Bundle composer
REST runtime API
OpenCode HTTP Skill Catalog
Thin OpenCode integration/plugin
MCP adapter
MCP Skills exposure where supported
Structured outcome ingestion
Routing telemetry
Benchmark harness
```

V1 does not include:

```text
A2A runtime
Agent Platform
remote autonomous execution
memory platform
model gateway
JEV dependency
graph database
large-scale crawler
automatic production promotion
self-modifying agents
microservice decomposition
complex admin frontend
```

---

# 50. V1 Technology Stack

Recommended:

```text
Python
FastAPI
Pydantic
PostgreSQL
pgvector
SQLAlchemy
Alembic
pytest
official/current MCP Python SDK compatible with target MCP revision
```

Recommended development support:

```text
Docker Compose
Ruff
mypy or pyright
pre-commit
structured logging
OpenTelemetry-compatible tracing abstraction
```

Do not add Kafka, Kubernetes, Neo4j, Redis, or Celery until a measured requirement exists.

---

# 51. V1 Corpus Target

Start smaller than the original plan implied.

```text
30–50 raw candidates
15–25 canonical skills
10–20 production skills
```

Choose production skills to cover distinct high-value coding task categories rather than many near-duplicates.

Suggested first capabilities:

```text
systematic-debugging
codebase-understanding
implementation-planning
regression-verification
code-review
safe-refactoring
test-design
api-integration
schema-migration-review
authentication-debugging
concurrency-debugging
dependency-upgrade-review
```

Exact names are examples, not fixed architecture.

---

# 52. V1 Implementation Sequence — Revised

## Phase 0 — Architecture contracts

Create first:

```text
ADR set
CapabilityDescriptor schema
CapabilityVersion schema
CapabilityRelease schema
CapabilityBinding schema
CapabilityBundle schema
TaskContext schema
OutcomeEvidence schema
error model
```

Acceptance:

- schemas validate,
- mutable/immutable boundaries are explicit,
- no protocol-specific types leak into domain models.

## Phase 1 — Foundation

Build:

```text
repository
configuration
logging
tracing abstraction
PostgreSQL
Alembic
health/readiness
CI
unit test harness
```

Acceptance:

- service boots,
- migrations run from empty DB,
- readiness checks DB,
- tests run in CI.

## Phase 2 — Canonical Registry

Implement:

```text
Capability
CapabilityVersion
CapabilityRelease
CapabilityBinding
Artifact metadata
Facet metadata
```

Acceptance:

- one authoritative registry,
- immutable published versions,
- production release can be changed without changing content,
- rollback works by release pointer/state.

## Phase 3 — Skill Package Provider

Implement:

```text
SKILL.md parser
package manifest
file hashing
content digest
artifact store
local source ingestion
quarantine state
```

Acceptance:

- ingest sample skills,
- preserve exact source snapshot,
- calculate hashes,
- reject invalid package structure,
- distinguish raw source from canonical artifact.

## Phase 4 — Provenance + License + Security Gates

Implement minimum:

```text
source metadata
license assessment
security assessment
transformation log
promotion prerequisites
```

Acceptance:

- unknown license can block distributable production channel,
- unreviewed raw skill cannot be production,
- every production version has provenance chain.

## Phase 5 — Facets + Eligibility

Implement:

```text
facet schema
production filter
client compatibility filter
trust filter
scope authorization
policy snapshot
```

Acceptance:

- ineligible capability never reaches reranker,
- revoked release disappears immediately from route results.

## Phase 6 — Retrieval Baseline

Implement:

```text
trusted routing document
embeddings
pgvector search
metadata/facet filtering
candidate trace
```

Acceptance:

- benchmark tasks retrieve relevant capabilities in top-K,
- raw malicious text is not blindly inserted into LLM reranking prompts.

## Phase 7 — Reranker

Start with:

```text
heuristic baseline
optional direct-LLM reranker
```

Acceptance:

- interface is swappable,
- reranker version recorded in trace,
- comparison against retrieval-only baseline exists.

## Phase 8 — Dependency Resolver + Composer

Implement:

```text
requires
conflicts
checks
0–5 item bundles
context budget
version pinning
digest pinning
```

Acceptance:

- empty bundle valid,
- no conflicting items,
- dependencies valid,
- deterministic schema validation,
- context budget enforced.

## Phase 9 — REST Runtime API

Expose:

```text
search
resolve
route
bundle lookup
outcome ingestion
```

Acceptance:

- API independent of OpenCode,
- second test client can use API without adapter changes to core.

## Phase 10 — OpenCode Catalog Adapter

Implement:

```text
HTTP skill catalog projection
version mapping
immutable skill file serving
```

Acceptance:

- OpenCode can discover/load platform-hosted production skills,
- catalog content derives from registry/release state,
- no duplicated lifecycle state.

## Phase 11 — OpenCode Routing Adapter

Implement thin plugin/hook:

```text
prompt → minimal TaskContext
POST /v1/routes
selected skill IDs → OpenCode prompt skill selection
outcome instrumentation
```

Acceptance:

- OpenCode has no manually maintained local copy of centrally managed skills,
- only selected skills are loaded,
- routing failure behavior is explicit,
- local permissions remain authoritative.

## Phase 12 — MCP Adapter

Expose:

```text
MCP Skills binding
route_capabilities tool
report_outcome tool
```

Acceptance:

- generic MCP test client can route and load a production skill,
- core domain code contains no MCP-specific types,
- modern stateless behavior does not rely on in-memory session state.

## Phase 13 — Outcome Evidence

Implement:

```text
outcome event ingestion
verdict source types
build/test/lint evidence
human correction flag
cost/latency fields
```

Acceptance:

- agent self-report and test-harness verdict are stored separately,
- route run can be joined to exact bundle/version/digest.

## Phase 14 — Benchmark Harness

Implement experiment variants:

```text
client alone
client + native manual skill baseline
retrieval only
retrieval + rerank
retrieval + rerank + composer
```

Acceptance:

- same fixture can be replayed,
- per-variant metrics exported,
- benchmark result references exact router and capability versions.

---

# 53. Suggested Sprint Plan

## Sprint 1 — Domain contracts + foundation

Deliver:

- ADRs,
- schemas,
- DB,
- migrations,
- health/readiness,
- test infrastructure.

## Sprint 2 — Registry + immutable versions

Deliver:

- capability identities,
- versions,
- releases,
- bindings,
- artifact metadata.

## Sprint 3 — Skill ingestion + provenance

Deliver:

- parser,
- hashing,
- quarantine,
- provenance,
- license/security gate baseline.

## Sprint 4 — Eligibility + retrieval

Deliver:

- facets,
- policy filters,
- pgvector,
- candidate traces.

## Sprint 5 — Rerank + composer

Deliver:

- baseline reranker,
- dependency rules,
- bundle composer,
- 0–5 capability behavior.

## Sprint 6 — REST + OpenCode catalog

Deliver:

- runtime API,
- OpenCode HTTP catalog,
- lazy skill retrieval.

## Sprint 7 — OpenCode routing plugin

Deliver:

- prompt hook,
- route API integration,
- selected skill injection,
- failure policy,
- instrumentation.

## Sprint 8 — MCP adapter

Deliver:

- MCP skills exposure,
- routing tool,
- outcome tool,
- protocol tests.

## Sprint 9 — Outcome evidence + observability

Deliver:

- route traces,
- execution evidence,
- cost/latency metrics,
- redaction.

## Sprint 10 — Benchmark experiment

Deliver:

- smoke benchmark,
- A/B/C/D/E variants,
- first architecture-value report.

---

# 54. V1 Exit Criteria — Revised

V1 is complete only if all are true:

1. OpenCode does not require manually copied local versions of centrally managed production skills.
2. OpenCode can load skills from the platform's skill catalog.
3. A real coding prompt can trigger platform routing before model execution.
4. Routing may legitimately return zero skills.
5. Selected skills are exact version-pinned immutable artifacts.
6. Platform policy removes revoked/ineligible skills before reranking.
7. Only selected skill content is lazily loaded by the client.
8. OpenCode still owns filesystem/shell/git/permission decisions.
9. A route trace records eligible candidates, retrieved candidates, ranked candidates, bundle, versions, and router versions.
10. Outcome reporting distinguishes self-report from verified evidence.
11. At least one second client can use the same domain capability infrastructure through REST or MCP.
12. Core domain/application modules have no dependency on OpenCode SDK/types.
13. Core domain/application modules have no dependency on MCP SDK/types.
14. A production capability can be rolled back without changing its immutable artifact.
15. A revoked capability cannot be selected after revocation takes effect.
16. Smoke benchmarks compare the platform against at least one no-platform baseline.
17. Security tests verify that skill instructions cannot grant client tool permissions.
18. Provenance and license status exist for every production skill.

---

# 55. V2 Scope — Catalog Governance and Quality

After V1 proves measurable value:

Add:

```text
100–300 raw candidates
50+ canonical capabilities
30–50 production capabilities
richer capability relations
better security scanners
license automation
upstream watcher
benchmark arena
promotion dashboard
telemetry dashboards
human review workflow
more sophisticated rerankers
```

Do not increase corpus size just to increase count.

---

# 56. V3 Scope — Agent Platform

Only after skill infrastructure has stable value.

Add:

```text
AgentRuntime
AgentProfile
Agent capability bindings
Task persistence
Planner profile
Coder profile
Reviewer profile
Verifier profile
optional Researcher profile
A2A gateway for independent agent boundaries
```

## 56.1 Agent architecture

Prefer:

```text
AgentRuntime
   +
AgentProfile(data/config)
```

instead of one code subsystem per role.

Example profile:

```yaml
id: reviewer
version: 1.0.0
model_profile: reasoning-medium
allowed_tools:
  - repository.read
  - tests.read
skill_policy:
  required:
    - code-review
budget:
  max_tokens: ...
execution_policy:
  can_write_repository: false
```

Planner/Coder/Reviewer/Verifier are profiles unless their runtime semantics genuinely differ.

---

# 57. V4 Scope — Reusable Service Platforms

Possible additions:

```text
Research Service
Evaluation Service
Memory/Knowledge Service
Browser Service
Artifact Service
Integration Service
```

Each registers capabilities through the same Capability Registry.

Do not create independent incompatible registries.

---

# 58. V5 Scope — Adaptive Intelligence

Only after enough real telemetry exists:

```text
cost-aware routing
adaptive reranking
JEV experiments
routing bandits / learned priors
skill graph learning
automatic composition suggestions
model routing
automatic candidate-generation suggestions
```

Production promotion remains governed.

Safe loop:

```text
telemetry
   ↓
proposal
   ↓
benchmark
   ↓
security/policy review
   ↓
promotion
```

Never:

```text
telemetry → self-modify production automatically
```

---

# 59. JEV Policy

JEV remains replaceable experimentation behind `CapabilityReranker`.

Promote only with evidence that it improves the end-to-end system.

Evaluate:

```text
routing recall
routing precision
task success
false-fix rate
total task tokens
total task cost
latency
human correction rate
```

A better routing score that makes total task success worse is not an improvement.

---

# 60. Failure Modes to Design For

## 60.1 Router outage

Mitigation:

- explicit fail-open/fail-closed policy,
- short deadline,
- no hanging prompt admission,
- trace outage separately.

## 60.2 Vector index unavailable

Fallback:

- metadata/facet retrieval,
- deterministic baseline routing where possible.

## 60.3 LLM reranker unavailable

Fallback:

- embedding/heuristic order,
- record degraded mode.

## 60.4 Skill revoked after being cached

Mitigation:

- release validation,
- cache TTL,
- digest pinning,
- adapter revocation policy.

## 60.5 Upstream repository compromised

Mitigation:

- immutable production versions,
- no automatic upstream replacement,
- quarantine new candidate,
- provenance alert.

## 60.6 Skill prompt injection

Mitigation:

- raw content quarantine,
- trusted routing summaries,
- security review,
- host permission boundary.

## 60.7 Conflicting selected skills

Mitigation:

- explicit conflicts relation,
- composer validation,
- minimal-bundle objective.

## 60.8 No useful skill exists

Correct behavior:

```text
return empty bundle
```

not a forced low-quality recommendation.

## 60.9 Outcome cannot be verified

Store:

```text
verdict = unknown
```

Do not convert absence of evidence into success.

## 60.10 Duplicate capability names

Identity must use stable IDs + origin/version semantics, never display name alone.

---

# 61. Security Test Cases

Minimum automated cases:

```text
malicious SKILL.md asks for secret exfiltration
skill attempts to override system instructions
skill requests denied shell action
skill package hash mismatch
revoked skill remains in client cache
cross-workspace capability access
unknown license attempts promotion
unsafe script bundled as supporting file
reranker sees adversarial candidate metadata
telemetry payload contains secret-like token
```

Expected behavior must be codified in tests.

---

# 62. Protocol Contract Tests

## REST

Test:

- validation,
- auth,
- stable errors,
- idempotency where documented,
- version pinning.

## OpenCode

Test:

- catalog format,
- skill ID mapping,
- lazy loading,
- plugin injection,
- no duplicate/shadowed skill IDs,
- fail-open/fail-closed behavior.

## MCP

Test:

- protocol negotiation,
- stateless request handling,
- skills listing/loading,
- resource integrity,
- custom routing tool,
- outcome tool,
- legacy compatibility only if intentionally supported.

## A2A later

Test:

- Agent Card mapping,
- task lifecycle,
- artifact handling,
- cancellation,
- authorization.

---

# 63. Observability Requirements

Use structured events.

Example event types:

```text
route.started
route.eligibility_completed
route.retrieval_completed
route.rerank_completed
route.bundle_created
capability.resolved
capability.load_failed
outcome.received
outcome.verified
release.promoted
release.revoked
benchmark.completed
security.violation
```

All events should carry:

```text
trace_id
request_id where applicable
timestamp
principal scope where allowed
component
schema_version
```

Never rely on ad-hoc log strings as the only source of analytics.

---

# 64. Cost Model

Track separately:

```text
routing embedding cost
reranker model cost
skill context token cost
client task model cost
remote tool/service cost
benchmark cost
```

The platform should optimize **total task cost**, not merely routing cost.

A more expensive router may be worthwhile if it substantially reduces failed coding attempts.

---

# 65. Latency Budget

Establish a routing latency budget because routing sits before execution.

Example conceptual budget:

```text
policy/filter       small
retrieval           small
rerank              bounded
compose             small
network overhead    bounded
```

Do not set hard numbers before measurement, but record stage-level latency from V1.

Use client deadline propagation.

---

# 66. Data Retention

Define retention separately for:

```text
routing metadata
raw task text
repository summaries
outcome events
benchmark artifacts
security audit events
raw source snapshots
```

Default to minimizing potentially sensitive user/project content.

Do not retain full prompts/code indefinitely merely because telemetry exists.

---

# 67. Administrative Operations

Control-plane operations:

```text
register source
create candidate
create canonical version
run assessment
stage release
promote release
rollback release
revoke release
deprecate capability
rebuild embeddings
run benchmark suite
compare benchmark runs
```

These operations require stronger authorization than runtime search/route APIs.

---

# 68. Suggested ADRs

Create Architecture Decision Records before implementation.

## ADR-001 — One canonical Capability Registry

Decision:

> Skills, services, workflows, resources, tools, and agents share one identity/version/release registry.

## ADR-002 — Capability as discriminated union

Decision:

> Shared descriptor + kind-specific spec, not a giant universal object.

## ADR-003 — Immutable versions

Decision:

> Published versions are immutable; promotion changes release state.

## ADR-004 — Protocol adapters outside domain core

Decision:

> MCP/OpenCode/REST/A2A types do not enter domain code.

## ADR-005 — OpenCode native skill mechanism

Decision:

> Use OpenCode native remote catalog/loading where practical; use platform routing to select IDs.

## ADR-006 — MCP Skills for procedural skill transport

Decision:

> Use standard MCP skill/resource mechanisms where supported rather than one custom tool per skill.

## ADR-007 — No A2A in V1

Decision:

> A2A only when autonomous agent boundaries exist.

## ADR-008 — Zero-item bundle is valid

Decision:

> Router may abstain.

## ADR-009 — Security policy precedes semantic ranking

Decision:

> Ineligible content never reaches normal candidate ranking.

## ADR-010 — Outcome evidence is multi-source

Decision:

> Do not use a single self-reported success boolean.

## ADR-011 — Modular monolith first

Decision:

> Logical boundaries, one deployment initially.

## ADR-012 — No automatic production evolution

Decision:

> Telemetry may generate proposals, never silently mutate production.

---

# 69. First End-to-End Reference Flow

User:

```text
"Fix an intermittent authentication race condition."
```

OpenCode prompt hook creates:

```yaml
task:
  text: Fix an intermittent authentication race condition.
context:
  language: typescript
  phase: debugging
  error_class: concurrency
constraints:
  max_items: 5
  max_context_tokens: 6000
```

Platform:

```text
1. identify caller/workspace/client
2. read active production releases
3. eligibility removes incompatible/revoked capabilities
4. facet filter: debugging + auth + concurrency
5. semantic retrieval returns 18 candidates
6. reranker produces ranked list
7. dependency resolver checks conflicts/requires/checks
8. composer chooses minimal useful bundle
```

Possible output:

```yaml
items:
  - systematic-debugging@2.4.1
  - concurrency-analysis@1.2.0
  - regression-verification@1.3.0
```

OpenCode:

```text
loads exact selected skills from HTTP catalog
executes repository work locally
runs tests locally
keeps local tool permissions authoritative
```

Outcome:

```text
plugin/client reports route/bundle IDs
build result
before/after tests
tool-call count
latency/token data where available
```

Platform stores evidence but does not assume selection caused success.

---

# 70. First Benchmark Experiment

Use 10 smoke tasks initially.

For each task run variants:

```text
A. OpenCode without platform skills
B. OpenCode with manually curated/native baseline skill
C. Platform retrieval only
D. Retrieval + reranker
E. Retrieval + reranker + composer
```

Questions:

1. Does E improve acceptance-test success over A/B?
2. Does reranking improve D over C?
3. Does composition improve E over D?
4. What is added latency?
5. What is added token/context cost?
6. Does the system introduce new regressions?
7. How often does the best action equal an empty bundle?

Do not expand to Agent Platform until these measurements demonstrate value.

---

# 71. Success Metrics

## 71.1 System metrics

```text
verified task success rate
acceptance test pass rate
regression rate
human correction rate
total task cost
total task latency
routing latency
context token overhead
routing abstention quality
```

## 71.2 Retrieval metrics

```text
top-k recall
candidate-set size
eligible-to-retrieved ratio
```

## 71.3 Reranker metrics

```text
top-1 relevance
top-k relevance
misroute rate
reranker latency
reranker token cost
```

## 71.4 Skill metrics

```text
usage count
verified success association
failure association
human correction association
average added context
compatibility failures
load failures
```

Do not label association metrics as causal improvement without controlled evidence.

---

# 72. What Not To Build Yet

Avoid:

```text
300 production skills immediately
hundreds of MCP tools
A2A for internal function calls
microservices per module
Neo4j in V1
Kafka in V1
Redis without measured need
Kubernetes for local MVP
mandatory JEV routing
self-modifying production skills
automatic upstream promotion
remote shell execution in V1
giant prompt context with every skill
one flat universal Capability object
one universal execute_capability verb
separate conflicting registries
```

---

# 73. What Must Be Clear Before Coding

Before implementation starts, answer these in ADR/config form rather than leaving them implicit:

```text
What is a capability ID?
What makes a version immutable?
What is the production channel?
What is a binding?
What is a release rollback?
When can routing return zero capabilities?
What client context is allowed to leave the machine?
What permissions are never grantable by a skill?
What evidence counts as verified success?
What license states block redistribution?
What happens when routing is unavailable?
How is a revoked cached skill handled?
Which exact MCP revisions/features are supported?
Which exact OpenCode V2 integration contract is supported?
```

---

# 74. Architecture Invariants

The architecture is healthy only while these remain true.

## Invariant 1

OpenCode can be replaced without rewriting the domain core.

## Invariant 2

MCP can be removed or supplemented without changing capability identity/version semantics.

## Invariant 3

A capability's immutable version does not change because metrics changed.

## Invariant 4

Promotion and rollback do not mutate immutable artifacts.

## Invariant 5

Skill instructions never grant host permissions.

## Invariant 6

Ineligible/revoked capabilities cannot be rescued by an LLM reranker.

## Invariant 7

A router may abstain.

## Invariant 8

A client can lazily resolve only the capabilities selected for a task.

## Invariant 9

Every selected production artifact is traceable to source provenance and digest.

## Invariant 10

Outcome evidence records who/what produced the verdict.

## Invariant 11

Benchmark results reference exact capability, router, policy, and fixture versions.

## Invariant 12

A new capability kind can be added without making all existing kinds implement irrelevant execution methods.

## Invariant 13

A new protocol adapter can be added without changing the registry schema fundamentally.

## Invariant 14

A new agent platform consumes the existing capability infrastructure instead of creating a parallel incompatible catalog.

## Invariant 15

The system's value is judged by end-to-end task outcomes, not catalog size or router sophistication alone.

---

# 75. Immediate Build Order

Do these next, in this order:

1. Freeze this architecture as `Architecture V2`.
2. Write ADR-001 through ADR-012.
3. Define Pydantic models for `Capability`, `CapabilityVersion`, `CapabilityRelease`, `CapabilityBinding`.
4. Define `TaskContext`, `RouteCapabilitiesCommand`, `CapabilityBundle`.
5. Define `OutcomeEvidence` and verdict-source enums.
6. Define database schema and migrations.
7. Implement immutable artifact hashing/storage.
8. Implement one authoritative registry.
9. Ingest 5 hand-picked skills manually to validate the model.
10. Implement production release selection.
11. Implement eligibility rules.
12. Build simple metadata retrieval before embeddings.
13. Add pgvector retrieval.
14. Build heuristic reranker.
15. Build minimal composer with zero-item support.
16. Expose REST `/v1/routes`.
17. Expose OpenCode HTTP skill catalog.
18. Build thin OpenCode routing plugin.
19. Verify local permission boundary.
20. Add MCP Skills adapter + routing tool.
21. Build structured outcome ingestion.
22. Run 10-task smoke benchmark with baseline variants.
23. Refine schemas from real traces.
24. Only then grow corpus to 30–50 raw candidates.
25. Only after V1 value is measured, begin V2 governance expansion.

---

# 76. Final Mental Model

Do not think:

```text
MCP server full of skills
```

Think:

```text
                         CAPABILITY SYSTEM

                    canonical identity/version
                             │
                    governance + policy
                             │
                    routing intelligence
                             │
                       minimal bundle
                             │
           ┌─────────────────┼──────────────────┐
           │                 │                  │
        OpenCode            MCP               REST
      native skills       skills/tools        apps/SDK
           │                 │                  │
           └─────────────────┼──────────────────┘
                             │
                    execution stays where
                    its authority belongs
```

And later:

```text
                             │
                             ▼
                      Agent Capability
                             │
                             ▼
                         A2A binding
                             │
                             ▼
                    autonomous delegation
```

The strategic objective is therefore:

> **Build a stable capability domain model and governance layer first; treat routing as replaceable intelligence; treat MCP, OpenCode, REST, SDKs, and A2A as adapters; and keep actual execution authority explicit and kind-specific.**

---

# 77. Standards/Integration Assumptions to Re-Verify During Implementation

This architecture intentionally follows current 2026-era behavior but does not hard-code protocol internals into the domain model.

Before implementing protocol adapters, verify the current official specifications for:

- OpenCode V2 remote skills/catalog behavior,
- OpenCode prompt/plugin hooks and skill selection semantics,
- MCP base protocol revision supported by the chosen SDK,
- MCP Skills extension support,
- MCP Tasks extension if deferred execution is introduced,
- A2A Agent Card / Task / Message / Artifact semantics before V3.

Protocol changes should require adapter updates, not domain-core redesign.

---

# 78. Superseded Decisions From Original Plan

The following original ideas are intentionally replaced.

| Original | V2 replacement |
|---|---|
| one broad flat Capability schema | CapabilityDescriptor + discriminated kind specs |
| metadata + metrics + status in one object | Version + Release + Binding + Metrics separation |
| Capability Registry then Skill Registry | one authoritative Capability Registry |
| universal CapabilityRequest | RequestContext + typed commands/queries |
| `execute_capability` as generic gateway verb | kind-specific resolve/read/invoke/delegate semantics |
| always return 1–5 skills | return 0–5 by default |
| SDK beside MCP/A2A/REST as protocol | SDK wraps network interfaces |
| control plane visually in request chain | control plane separate from data plane |
| security checklist | threat boundaries + enforcement points |
| self-reported success | multi-source outcome evidence |
| folder lifecycle as implied source of truth | DB release state + immutable artifacts |
| MCP custom skill tools as main skill transport | MCP Skills extension where supported |
| MCP mandatory for OpenCode integration | native OpenCode remote skill catalog + thin router plugin preferred |
| A2A between any internal agents | A2A for real autonomous system boundaries |
| tree-only taxonomy | faceted taxonomy with optional hierarchies |
| benchmark router accuracy first | end-to-end controlled task outcome first |

---

# 79. Definition of Architecture Correctness

The architecture is considered correct enough to scale when all of the following are demonstrably true:

- protocol upgrades do not require rewriting capability identity/version models,
- client replacement does not require rewriting routing logic,
- router replacement does not require registry migration,
- capability metrics can change without mutating historical versions,
- releases can be promoted/rolled back/revoked independently of stored artifacts,
- a malicious raw skill cannot enter production without crossing explicit gates,
- an imported instruction cannot bypass host permissions,
- a large catalog does not become a large model context,
- production routing can return no capability when appropriate,
- every recommendation can be traced to exact versions and policy state,
- benchmark comparisons can reproduce the exact routing stack used,
- adding agents later reuses the same capability system,
- A2A remains optional rather than infecting internal architecture,
- OpenCode remains an adapter rather than the center of the system,
- measured end-to-end results—not architectural fashion—decide which intelligence components survive.

---

# 80. Final Recommendation

Build V1 as a **Capability Registry + Skill Intelligence + Multi-Adapter Delivery system**, not as a generalized autonomous-agent platform.

The first product-quality proof should be:

```text
A real OpenCode task
        ↓
minimal routing context
        ↓
policy-safe candidate selection
        ↓
0–5 version-pinned skills
        ↓
OpenCode native lazy skill load
        ↓
local coding execution
        ↓
verified outcome evidence
        ↓
controlled benchmark comparison
```

If this loop measurably improves real tasks, then the foundation is strong enough to justify V2 catalog expansion and eventually V3 agent/A2A infrastructure.

If it does not, improve or simplify the routing/capability layer before adding more architecture.

That discipline is the main protection against turning the project into a large infrastructure system whose complexity exceeds the value it produces.
