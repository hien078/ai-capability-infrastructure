"""Store-wide validation: check every invariant, report every break.

Month-end hygiene: run over a store and get a report of everything that
should never be true — duplicate ids, naive timestamps, unknown
currencies, postings on accounts that do not exist, sources outside the
allowed set. The validator never mutates anything; it only reports.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from ledger.model import SOURCES, Transaction
from ledger.money import DECIMALS
from ledger.periods import load_tz
from ledger.store import Store


@dataclass(frozen=True)
class Finding:
    """One broken invariant."""

    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@dataclass
class ValidationReport:
    """Everything a validation run found."""

    findings: list[Finding] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.findings

    def __iter__(self) -> Iterator[Finding]:
        return iter(self.findings)

    def __str__(self) -> str:
        head = f"checked {self.checked} transactions"
        if self.ok:
            return head + ", all invariants hold"
        return head + "\n" + "\n".join(f"  {f}" for f in self.findings)


def validate_transaction(txn: Transaction) -> list[Finding]:
    """The per-transaction invariants (never raises)."""
    findings: list[Finding] = []
    if txn.id is None:
        findings.append(Finding("ID_UNASSIGNED", f"{txn.description!r} has no id"))
    if txn.timestamp.tzinfo is None or txn.timestamp.utcoffset() is None:
        findings.append(Finding("NAIVE_TIMESTAMP", f"id {txn.id} timestamp has no offset"))
    if txn.source not in SOURCES:
        findings.append(Finding("BAD_SOURCE", f"id {txn.id} source {txn.source!r}"))
    if not txn.description or not txn.description.strip():
        findings.append(Finding("EMPTY_DESCRIPTION", f"id {txn.id}"))
    if not txn.postings:
        findings.append(Finding("NO_POSTINGS", f"id {txn.id}"))
    for posting in txn.postings:
        if posting.amount.currency not in DECIMALS:
            findings.append(
                Finding(
                    "UNKNOWN_CURRENCY",
                    f"id {txn.id} account {posting.account!r} currency {posting.amount.currency!r}",
                )
            )
        if not posting.account or not posting.account.strip():
            findings.append(Finding("EMPTY_ACCOUNT", f"id {txn.id}"))
    return findings


def validate_store(store: Store, check_accounts: bool = True) -> ValidationReport:
    """Run every invariant over a store."""
    report = ValidationReport()
    seen_ids: dict[int, int] = {}
    for txn in store.all():
        report.checked += 1
        report.findings.extend(validate_transaction(txn))
        if txn.id is not None:
            if txn.id in seen_ids:
                report.findings.append(
                    Finding(
                        "DUPLICATE_ID",
                        f"id {txn.id} also on transaction #{seen_ids[txn.id]}",
                    )
                )
            seen_ids[txn.id] = len(seen_ids) + 1
    if check_accounts:
        known = {a.name for a in store.accounts()}
        for txn in store.all():
            for posting in txn.postings:
                if posting.account not in known:
                    report.findings.append(
                        Finding(
                            "UNKNOWN_ACCOUNT",
                            f"id {txn.id} posts to {posting.account!r}",
                        )
                    )
    return report


def validate_timezone(name: str) -> Finding | None:
    """The configured timezone must exist (returns a finding or None)."""
    try:
        load_tz(name)
    except Exception as exc:  # ValidationError from load_tz
        return Finding("BAD_TIMEZONE", f"{name!r}: {exc}")
    return None
