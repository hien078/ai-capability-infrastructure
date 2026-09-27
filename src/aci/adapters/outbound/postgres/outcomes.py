"""Outcome evidence persistence (plan §§33, 41; ADR-010).

Verdicts stay multi-source with confidence; `unknown` stays `unknown` —
nothing here ever collapses evidence into a single success flag.
"""

from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.orm import OutcomeEventRow, OutcomeVerdictRow
from aci.domain.capability.models import OutcomeEvidence, OutcomeVerdict


class SqlAlchemyOutcomeRecorder:
    """Implements the OutcomeRecorder protocol."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def record(self, evidence: OutcomeEvidence) -> OutcomeEvidence:
        with self._sessions() as session, session.begin():
            session.add(
                OutcomeEventRow(
                    outcome_id=evidence.outcome_id,
                    route_run_id=evidence.route_run_id,
                    bundle_id=evidence.bundle_id,
                    received_at=evidence.received_at,
                    latency_ms=evidence.latency_ms,
                    tests_before=dict(evidence.tests_before),
                    tests_after=dict(evidence.tests_after),
                    client_status=evidence.client_status,
                    lint_passed=evidence.lint_passed,
                    build_passed=evidence.build_passed,
                    changed_files=evidence.changed_files,
                    tool_calls=evidence.tool_calls,
                    human_corrected=evidence.human_corrected,
                    input_tokens=evidence.input_tokens,
                    output_tokens=evidence.output_tokens,
                    estimated_usd=evidence.estimated_usd,
                )
            )
            # Explicit parent flush: never rely on UOW ordering for raw-FK children.
            session.flush()
            for position, verdict in enumerate(evidence.verdicts):
                session.add(
                    OutcomeVerdictRow(
                        outcome_id=evidence.outcome_id,
                        position=position,
                        source=verdict.source,
                        status=verdict.status,
                        confidence=verdict.confidence,
                    )
                )
        return evidence

    def get_outcome(self, outcome_id: str) -> OutcomeEvidence | None:
        with self._sessions() as session:
            event = session.get(OutcomeEventRow, outcome_id)
            if event is None:
                return None
            verdict_rows: list[OutcomeVerdictRow] = (
                session.query(OutcomeVerdictRow)
                .filter_by(outcome_id=outcome_id)
                .order_by(OutcomeVerdictRow.position)
                .all()
            )
            verdicts = [
                OutcomeVerdict(
                    source=row.source,  # type: ignore[arg-type]
                    status=row.status,  # type: ignore[arg-type]
                    confidence=row.confidence,  # type: ignore[arg-type]
                )
                for row in verdict_rows
            ]
            return OutcomeEvidence(
                outcome_id=event.outcome_id,
                route_run_id=event.route_run_id,
                bundle_id=event.bundle_id,
                received_at=event.received_at,
                verdicts=verdicts,
                tests_before=dict(event.tests_before),
                tests_after=dict(event.tests_after),
                latency_ms=event.latency_ms,
                client_status=event.client_status,
                lint_passed=event.lint_passed,
                build_passed=event.build_passed,
                changed_files=event.changed_files,
                tool_calls=event.tool_calls,
                human_corrected=event.human_corrected,
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
                estimated_usd=event.estimated_usd,
            )
