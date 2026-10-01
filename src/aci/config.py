"""Runtime config. Env-loaded; no secrets in repo."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from aci.domain.capability.models import DEFAULT_MAX_CONTEXT_TOKENS


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACI_")

    database_url: str = "postgresql+psycopg://aci:aci@localhost:5432/aci"
    object_store_root: str = "data/objects"
    log_level: str = "INFO"
    #: Which embedder backs retrieval: "hashing" (offline deterministic
    #: baseline, default — keeps tests/CI network-free) or "fastembed"
    #: (local ONNX semantic model, V2 §55; needs the `semantic` extra and
    #: a one-time model download).
    embedder: str = "hashing"
    #: fastembed model name (only used when embedder="fastembed"). Must
    #: emit EMBEDDING_DIMS (384) — bge-small-en-v1.5 does.
    embedder_model: str = "BAAI/bge-small-en-v1.5"
    #: Base URL this service is reachable at (A2A Agent Card interface URL,
    #: V3 §56; deployment overrides via ACI_SERVICE_URL).
    service_url: str = "http://localhost:8000"
    #: OpenAI-compatible endpoint backing the delegated-task executor (V3
    #: §56.1). Empty = no executor configured (UnconfiguredExecutor keeps the
    #: A2A surface honest); set to plug a model client into the runtime.
    agent_model_base_url: str = ""
    #: API key for the executor endpoint ("" = no Authorization header).
    agent_model_api_key: str = ""
    #: Model id the HarnessKernel model gateway requests (ADR-014).
    agent_model_id: str = ""
    #: Per-request timeout for the HarnessKernel model gateway (seconds).
    agent_model_timeout_seconds: float = 120.0
    #: JSON file holding AgentProfile records (deployment DATA, §56.1) — a
    #: bare list or {"profiles": [...]}. Empty = no profiles configured.
    agent_profiles_path: str = ""
    #: Directory whose immediate subdirectories are the workspace SOURCES a
    #: client may name in POST /v1/agent-runs (harness.md §16). Empty = no
    #: workspace tools: runs get no filesystem or process authority.
    agent_workspace_root: str = ""
    #: Per-run working copies: a run works in <agent_runs_root>/<run_id>, the
    #: source directory is never touched.
    agent_runs_root: str = "data/agent-runs"
    #: Server CEILING (§13.4, INV-02) of command prefixes the model's
    #: run_command tool and the client's verification_command may execute,
    #: e.g. ["python -m pytest"]; a request may only narrow it. Empty
    #: (default) = no run_command tool and no verification command.
    #: A non-empty ceiling runs workspace code — including files the model
    #: just wrote — inside the `agent_sandbox` (bwrap by default; fail closed
    #: when unusable). With agent_sandbox="none" it runs as the bare server
    #: user — only on a host you would let the model own. Env value is
    #: JSON: ACI_AGENT_PROCESS_PREFIXES='["python -m pytest"]'.
    agent_process_prefixes: list[str] = Field(default_factory=list)
    #: Timeout for one model-issued run_command (seconds).
    agent_command_timeout_seconds: float = 120.0
    #: Timeout for the client's verification_command (seconds).
    agent_verification_timeout_seconds: float = 300.0
    #: Bearer token guarding every /v1/agent-runs route (exposure gate:
    #: anyone who can reach the port can otherwise start a run that
    #: executes under the server user). Empty (default) = UNAUTHENTICATED
    #: mode — the deployment must keep the port on localhost; a startup
    #: warning says so. Set to expose the surface beyond localhost.
    agent_runs_token: str = ""
    #: Bearer token guarding the /a2a JSON-RPC endpoint (SendMessage runs a
    #: delegated agent task). Empty (default) = UNAUTHENTICATED mode — the
    #: deployment must keep the port on localhost. The public Agent Card
    #: (/.well-known/agent-card.json) stays open for discovery.
    a2a_token: str = ""
    #: Named A2A callers: principal → bearer token (JSON env, e.g.
    #: ACI_A2A_PRINCIPALS='{"orchestrator-a": "tok1"}'). A task is owned by the
    #: principal that created it; GetTask/CancelTask on another principal's
    #: task is TASK_NOT_FOUND. `a2a_token` (if set) is the principal "default".
    a2a_principals: dict[str, str] = Field(default_factory=dict)
    #: Bearer token guarding the REST read/write surfaces (/v1/routes,
    #: /v1/bundles, /v1/outcomes, /v1/evaluations, /v1/capabilities,
    #: /opencode/skills, /ui). Empty (default) = UNAUTHENTICATED mode —
    #: localhost-only by deployment assumption. /health and /ready stay open.
    api_token: str = ""
    #: OS sandbox for every agent-run process — the model's run_command AND
    #: the client's verification_command (harness.md §16.5, user decision
    #: 2026-10-01). "bwrap" (default): bubblewrap — read-only system +
    #: interpreter, the run workspace read-write at /workspace, no network,
    #: no $HOME/repo/data, cleared env, rlimits below. FAIL CLOSED: if bwrap
    #: is unusable on this host every command is REFUSED (PERMISSION_DENIED,
    #: startup warning) — never a silent unsandboxed run. "none": explicit
    #: opt-out, runs as the bare server user (loud startup warning).
    agent_sandbox: Literal["bwrap", "none"] = "bwrap"
    #: Per-process rlimits inside the bwrap sandbox.
    agent_sandbox_cpu_seconds: int = Field(default=600, gt=0)
    agent_sandbox_memory_mb: int = Field(default=4096, gt=0)
    agent_sandbox_file_size_mb: int = Field(default=1024, gt=0)
    agent_sandbox_max_processes: int = Field(default=256, gt=0)
    agent_sandbox_open_files: int = Field(default=1024, gt=0)
    #: Extra host directories bound READ-ONLY into the sandbox (e.g. a
    #: toolchain outside /usr). $HOME, its ancestors, the server cwd and `/`
    #: are refused. JSON env: ACI_AGENT_SANDBOX_RO_BINDS='["/opt/node"]'.
    agent_sandbox_ro_binds: list[str] = Field(default_factory=list)
    #: Server approval FLOOR (harness.md §13.6): tool ids whose every call
    #: pauses the agent run (INTERRUPTED_APPROVAL) until the client approves
    #: or denies it via POST /v1/agent-runs/{id}/resume. A request may ADD
    #: tools (`approval_required_tools`), never remove these. Approval is
    #: one-shot per call and never widens grants (INV-02). Empty (default) =
    #: no approval pauses. JSON env:
    #: ACI_AGENT_APPROVAL_REQUIRED_TOOLS='["run_command", "write_file"]'.
    agent_approval_required_tools: list[str] = Field(default_factory=list)
    #: Kernel capability selection policy (harness.md §11; kernel path ONLY —
    #: POST /v1/routes is unaffected and the route run still records the full
    #: routed bundle). Every activated skill is resent to the model on every
    #: turn, so one capability request activates only a narrowed slice of the
    #: routed bundle. Defaults from the 2026-10-01 DEV_CASES replay (n=31,
    #: aci_bench, fastembed, read-only): margin 0.15 capped at 3 is the only
    #: rule tried that matches top-5's hit rate (31/31 before any token cap;
    #: 30/31 with it — claude-api, ~21.5k tokens, can never be activated)
    #: while loading 1.61 skills instead of 5 (~3.5k vs ~11.8k skill tokens).
    #: PROVISIONAL: 0.15 just covers the largest observed gap (0.142, one
    #: debugging case); 0.10 keeps 30/31 and is not separable at n=31 —
    #: re-check when DEV_CASES grows.
    #: Top-k skills activated per capability request (rank order).
    agent_capability_max_items: int = Field(default=3, ge=1)
    #: Relative rerank-score margin: keep items scoring >= top score - margin.
    agent_capability_score_margin: float = Field(default=0.15, ge=0.0)
    #: Cap on the summed real SKILL.md sizes (estimated tokens) activated per
    #: request; the top item is kept even alone if it fits the per-skill cap.
    #: Defaults to the routing bundle budget (DEFAULT_MAX_CONTEXT_TOKENS, 8000 —
    #: composer v2 charges real sizes, so the router already stops there; one
    #: knob, not two competing caps). Above the DEV p90 of kept skills (~6.3k).
    agent_capability_max_total_tokens: int = Field(default=DEFAULT_MAX_CONTEXT_TOKENS, ge=1)


settings = Settings()
