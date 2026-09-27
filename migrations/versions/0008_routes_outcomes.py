"""Routing telemetry + bundles + outcomes (Phase 9, plan §§36, 41)."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "route_runs",
        sa.Column("route_run_id", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("principal_id", sa.Text(), nullable=False),
        sa.Column("organization_id", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=True),
        sa.Column("client_type", sa.Text(), nullable=False),
        sa.Column("client_version", sa.Text(), nullable=True),
        sa.Column("protocol_type", sa.Text(), nullable=False),
        sa.Column("task_text", sa.Text(), nullable=False),
        sa.Column("policy_snapshot_id", sa.Text(), nullable=True),
        sa.Column("eligible_count", sa.Integer(), nullable=False),
        sa.Column("stages", postgresql.JSONB(), nullable=False),
        sa.Column("reranker_implementation", sa.Text(), nullable=False),
        sa.Column("reranker_version", sa.Text(), nullable=False),
        sa.Column("composer_implementation", sa.Text(), nullable=False),
        sa.Column("composer_version", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("bundle_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("route_run_id"),
    )
    op.create_table(
        "bundles",
        sa.Column("bundle_id", sa.Text(), nullable=False),
        sa.Column("route_run_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execution_order", postgresql.JSONB(), nullable=False),
        sa.Column("budget", postgresql.JSONB(), nullable=True),
        sa.Column("policy_snapshot_id", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["route_run_id"], ["route_runs.route_run_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("bundle_id"),
    )
    op.create_table(
        "bundle_items",
        sa.Column("bundle_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("capability_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("load_mode", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["bundle_id"], ["bundles.bundle_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["capability_id", "version"],
            ["capability_versions.capability_id", "capability_versions.version"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("bundle_id", "position"),
    )
    op.create_table(
        "outcome_events",
        sa.Column("outcome_id", sa.Text(), nullable=False),
        sa.Column("route_run_id", sa.Text(), nullable=False),
        sa.Column("bundle_id", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("tests_before", postgresql.JSONB(), nullable=False),
        sa.Column("tests_after", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["route_run_id"], ["route_runs.route_run_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("outcome_id"),
    )
    op.create_table(
        "outcome_verdicts",
        sa.Column("outcome_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["outcome_id"], ["outcome_events.outcome_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("outcome_id", "position"),
    )


def downgrade() -> None:
    op.drop_table("outcome_verdicts")
    op.drop_table("outcome_events")
    op.drop_table("bundle_items")
    op.drop_table("bundles")
    op.drop_table("route_runs")
