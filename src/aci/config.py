"""Runtime config. Env-loaded; no secrets in repo."""

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


settings = Settings()
