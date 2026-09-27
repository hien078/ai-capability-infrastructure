"""Benchmark persistence (plan §§41, 70; Phase 14).

Implements the evaluation layer's ``BenchmarkStore`` protocol: cases are
upserted idempotently (a fixture re-run never duplicates), runs/results are
append-only, and results read back with their exact version references.
"""

from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.orm import (
    BenchmarkCaseRow,
    BenchmarkResultRow,
    BenchmarkRunRow,
)
from aci.evaluation.models import (
    BenchmarkCase,
    BenchmarkResult,
    BenchmarkRun,
    RouterVersions,
    SelectedCapability,
)


def _case_row(case: BenchmarkCase) -> BenchmarkCaseRow:
    return BenchmarkCaseRow(
        case_id=case.case_id,
        category=case.category,
        fixture=case.fixture,
        task_text=case.task_text,
        annotations={
            "relevant_strong": list(case.relevant_strong),
            "relevant_acceptable": list(case.relevant_acceptable),
            "relevant_irrelevant": list(case.relevant_irrelevant),
            "acceptance_tests": list(case.acceptance_tests),
            "forbidden_actions": list(case.forbidden_actions),
            "baseline_capability_ids": list(case.baseline_capability_ids),
        },
        budget={
            "max_cost_usd": case.max_cost_usd,
            "max_latency_ms": case.max_latency_ms,
        },
    )


def _case_model(row: BenchmarkCaseRow) -> BenchmarkCase:
    annotations: dict[str, Any] = dict(row.annotations)
    budget: dict[str, Any] = dict(row.budget)
    return BenchmarkCase(
        case_id=row.case_id,
        category=row.category,
        fixture=row.fixture,
        task_text=row.task_text,
        relevant_strong=list(annotations.get("relevant_strong", [])),
        relevant_acceptable=list(annotations.get("relevant_acceptable", [])),
        relevant_irrelevant=list(annotations.get("relevant_irrelevant", [])),
        acceptance_tests=list(annotations.get("acceptance_tests", [])),
        forbidden_actions=list(annotations.get("forbidden_actions", [])),
        baseline_capability_ids=list(annotations.get("baseline_capability_ids", [])),
        max_cost_usd=budget.get("max_cost_usd"),
        max_latency_ms=budget.get("max_latency_ms"),
    )


class SqlAlchemyBenchmarkStore:
    """Implements the ``BenchmarkStore`` protocol (evaluation/harness.py)."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def put_case(self, case: BenchmarkCase) -> None:
        with self._sessions() as session, session.begin():
            session.merge(_case_row(case))

    def put_run(self, run: BenchmarkRun) -> None:
        with self._sessions() as session, session.begin():
            session.add(
                BenchmarkRunRow(
                    run_id=run.run_id,
                    label=run.label,
                    created_at=run.created_at,
                    case_count=run.case_count,
                )
            )

    def put_result(self, result: BenchmarkResult) -> None:
        with self._sessions() as session, session.begin():
            session.add(
                BenchmarkResultRow(
                    result_id=result.result_id,
                    run_id=result.run_id,
                    case_id=result.case_id,
                    variant=result.variant,
                    created_at=result.created_at,
                    route_run_id=result.route_run_id,
                    bundle_id=result.bundle_id,
                    selected=[s.model_dump(mode="json") for s in result.selected],
                    router=result.router.model_dump(mode="json"),
                    metrics=dict(result.metrics),
                )
            )

    def list_results(self, run_id: str) -> list[BenchmarkResult]:
        with self._sessions() as session:
            rows: list[BenchmarkResultRow] = (
                session.query(BenchmarkResultRow)
                .filter_by(run_id=run_id)
                .order_by(BenchmarkResultRow.result_id)
                .all()
            )
            return [self._result_model(row) for row in rows]

    def get_case(self, case_id: str) -> BenchmarkCase | None:
        with self._sessions() as session:
            row = session.get(BenchmarkCaseRow, case_id)
            return None if row is None else _case_model(row)

    @staticmethod
    def _result_model(row: BenchmarkResultRow) -> BenchmarkResult:
        router: dict[str, Any] = dict(row.router)
        return BenchmarkResult(
            result_id=row.result_id,
            run_id=row.run_id,
            case_id=row.case_id,
            variant=row.variant,  # type: ignore[arg-type]
            created_at=row.created_at,
            route_run_id=row.route_run_id,
            bundle_id=row.bundle_id,
            selected=[SelectedCapability.model_validate(s) for s in row.selected],
            router=RouterVersions(
                reranker_implementation=router.get("reranker_implementation"),
                reranker_version=router.get("reranker_version"),
                composer_implementation=router.get("composer_implementation"),
                composer_version=router.get("composer_version"),
            ),
            metrics=dict(row.metrics),
        )
