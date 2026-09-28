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


settings = Settings()
