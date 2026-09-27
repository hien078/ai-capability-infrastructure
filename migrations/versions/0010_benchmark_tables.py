"""Benchmark tables (Phase 14, plan §§41, 70).

``benchmark_cases`` (§35 fixtures, registered idempotently), ``benchmark_runs``
(one execution of a case set), ``benchmark_results`` (one row per case ×
variant, pinning the exact router implementations and the exact capability
id/version/digest selected — §52 acceptance).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "benchmark_cases",
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("fixture", sa.Text(), nullable=False),
        sa.Column("task_text", sa.Text(), nullable=False),
        sa.Column("annotations", postgresql.JSONB(), nullable=False),
        sa.Column("budget", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("case_id"),
    )
    op.create_table(
        "benchmark_runs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_table(
        "benchmark_results",
        sa.Column("result_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("variant", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("route_run_id", sa.Text(), nullable=True),
        sa.Column("bundle_id", sa.Text(), nullable=True),
        sa.Column("selected", postgresql.JSONB(), nullable=False),
        sa.Column("router", postgresql.JSONB(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["benchmark_runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["case_id"], ["benchmark_cases.case_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["route_run_id"], ["route_runs.route_run_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("result_id"),
    )
    op.create_index("ix_benchmark_results_run", "benchmark_results", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_benchmark_results_run", table_name="benchmark_results")
    op.drop_table("benchmark_results")
    op.drop_table("benchmark_runs")
    op.drop_table("benchmark_cases")
