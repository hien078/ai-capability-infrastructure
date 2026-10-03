"""Ledger configuration: a small JSON file per deployment.

The config pins the store directory, the business timezone (the one
the daily summary groups by), the base currency for new accounts and
the default account kind. Everything has a default; a missing file is
not an error, a bad file is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ledger.errors import ValidationError
from ledger.model import ACCOUNT_KINDS
from ledger.money import DECIMALS
from ledger.periods import load_tz


@dataclass
class LedgerConfig:
    """Deployment settings."""

    store_dir: str = "ledger-data"
    timezone: str = "UTC"
    base_currency: str = "USD"
    default_account_kind: str = "asset"
    report_cache_entries: int = 128
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Raise :class:`ValidationError` unless every setting is sound."""
        if not self.store_dir or not self.store_dir.strip():
            raise ValidationError("store_dir must not be empty")
        load_tz(self.timezone)  # raises on unknown zones
        if self.base_currency not in DECIMALS:
            raise ValidationError(f"unknown currency {self.base_currency!r}")
        if self.default_account_kind not in ACCOUNT_KINDS:
            raise ValidationError(f"unknown account kind {self.default_account_kind!r}")
        if self.report_cache_entries < 1:
            raise ValidationError("report_cache_entries must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "store_dir": self.store_dir,
            "timezone": self.timezone,
            "base_currency": self.base_currency,
            "default_account_kind": self.default_account_kind,
            "report_cache_entries": self.report_cache_entries,
            "extra": self.extra,
        }


def load_config(path: Path) -> LedgerConfig:
    """Load a config file; a missing file yields the defaults."""
    path = Path(path)
    if not path.exists():
        return LedgerConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"bad config file: {exc}") from None
    if not isinstance(data, dict):
        raise ValidationError("config file must be a JSON object")
    known = {f for f in LedgerConfig().__dict__ if not f.startswith("_")}
    extra = {k: v for k, v in data.items() if k not in known}
    config = LedgerConfig(
        store_dir=data.get("store_dir", "ledger-data"),
        timezone=data.get("timezone", "UTC"),
        base_currency=data.get("base_currency", "USD"),
        default_account_kind=data.get("default_account_kind", "asset"),
        report_cache_entries=data.get("report_cache_entries", 128),
        extra=extra,
    )
    config.validate()
    return config


def save_config(config: LedgerConfig, path: Path) -> None:
    """Write a config file (validated first)."""
    config.validate()
    Path(path).write_text(json.dumps(config.to_dict(), indent=2) + "\n", encoding="utf-8")
