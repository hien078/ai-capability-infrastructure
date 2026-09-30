"""Runtime config. Env-loaded; no secrets in repo."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    #: WARNING: a non-empty ceiling runs workspace code — including files the
    #: model just wrote — as the server user WITHOUT a sandbox (LocalWorkspace,
    #: §16.4). Enable only on a host you would let the model own. Env value is
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


settings = Settings()
