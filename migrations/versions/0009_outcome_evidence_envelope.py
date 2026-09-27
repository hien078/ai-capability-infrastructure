"""Outcome evidence envelope (Phase 13, plan §33).

Adds the §33 fields to ``outcome_events``: client completion status,
build/test/lint observations, human correction flag, and cost — all nullable so
existing rows and partial evidence stay valid.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (column, type) — every field optional, never merged into one flag (§33).
_COLUMNS: list[tuple[str, Any]] = [
    ("client_status", sa.Text()),
    ("lint_passed", sa.Boolean()),
    ("build_passed", sa.Boolean()),
    ("changed_files", sa.Integer()),
    ("tool_calls", sa.Integer()),
    ("human_corrected", sa.Boolean()),
    ("input_tokens", sa.BigInteger()),
    ("output_tokens", sa.BigInteger()),
    ("estimated_usd", sa.Float()),
]


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("outcome_events", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("outcome_events", name)
