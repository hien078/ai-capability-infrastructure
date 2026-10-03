"""Hidden acceptance: the store — ids, persistence, the sync hook.

Ids are assigned by the store, start at 1 and are unique. The
``allocate_hook`` extension point runs after allocation; a hook that
adds its own transaction must never collide with the allocation in
flight.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ledger.errors import UnknownAccountError, ValidationError
from ledger.model import Posting, Transaction
from ledger.money import Money
from ledger.store import Store

UTC = UTC


def make_txn(desc="t", ts=None, cents=100, account="checking"):
    return Transaction(
        timestamp=ts or datetime(2026, 1, 1, tzinfo=UTC),
        description=desc,
        postings=[Posting(account, Money(cents))],
    )


def test_new_store_is_empty(tmp_path):
    assert Store(tmp_path).all() == []


def test_add_assigns_sequential_ids(tmp_path):
    store = Store(tmp_path)
    assert [store.add_transaction(make_txn()).id for _ in range(3)] == [1, 2, 3]


def test_add_rejects_an_explicit_id(tmp_path):
    store = Store(tmp_path)
    txn = make_txn()
    txn.id = 5
    with pytest.raises(ValidationError):
        store.add_transaction(txn)


def test_get_missing_returns_none(tmp_path):
    assert Store(tmp_path).get(99) is None


def test_all_is_insertion_order(tmp_path):
    store = Store(tmp_path)
    for name in ("c", "a", "b"):
        store.add_transaction(make_txn(desc=name))
    assert [t.description for t in store.all()] == ["c", "a", "b"]


def test_all_filters_by_account(tmp_path):
    store = Store(tmp_path)
    store.add_transaction(make_txn(desc="one", account="checking"))
    store.add_transaction(make_txn(desc="two", account="groceries"))
    assert [t.description for t in store.all(account="groceries")] == ["two"]


def test_save_load_roundtrip(tmp_path):
    store = Store(tmp_path)
    store.add_transaction(make_txn(desc="one", cents=100))
    store.add_transaction(
        make_txn(desc="two", ts=datetime(2026, 1, 2, 3, 4, tzinfo=UTC), cents=-250)
    )
    reloaded = Store(tmp_path)
    assert [t.id for t in reloaded.all()] == [1, 2]
    assert [t.description for t in reloaded.all()] == ["one", "two"]
    assert reloaded.all()[1].postings[0].amount.cents == -250
    assert reloaded.all()[1].timestamp == datetime(2026, 1, 2, 3, 4, tzinfo=UTC)


def test_accounts_crud(tmp_path):
    store = Store(tmp_path)
    store.ensure_account("checking", kind="asset")
    store.ensure_account("checking", kind="liability")  # idempotent
    assert store.account("checking").kind == "asset"
    with pytest.raises(UnknownAccountError):
        store.account("nope")
    assert [a.name for a in store.accounts()] == ["checking"]


def test_allocate_hook_sees_the_allocated_id(tmp_path):
    store = Store(tmp_path)
    seen: list[int] = []
    store.allocate_hook = seen.append
    txn = store.add_transaction(make_txn())
    assert seen == [txn.id]


def test_allocate_hook_concurrent_insert_never_collides(tmp_path):
    """The sync hook adds a transaction while an allocation is in flight.

    The store must come out with three rows and three distinct ids —
    never a duplicate-id crash.
    """
    store = Store(tmp_path)
    store.add_transaction(make_txn(desc="one"))

    def hook(allocated):
        store.allocate_hook = None  # mirror exactly one allocation
        store.add_transaction(make_txn(desc="hook"))

    store.allocate_hook = hook
    txn = store.add_transaction(make_txn(desc="outer"))
    rows = store.all()
    ids = [r.id for r in rows]
    assert len(rows) == 3
    assert len(set(ids)) == 3
    assert txn.id in ids


def test_hook_rows_survive_a_reload(tmp_path):
    store = Store(tmp_path)
    store.add_transaction(make_txn(desc="one"))

    def hook(allocated):
        store.allocate_hook = None
        store.add_transaction(make_txn(desc="hook"))

    store.allocate_hook = hook
    store.add_transaction(make_txn(desc="outer"))
    reloaded = Store(tmp_path)
    assert len(reloaded.all()) == 3
    assert len({t.id for t in reloaded.all()}) == 3


def test_balance_sums_every_posting(tmp_path):
    store = Store(tmp_path)
    store.add_transaction(make_txn(cents=100, account="checking"))
    store.add_transaction(make_txn(cents=-25, account="checking"))
    assert store.balance("checking").cents == 75
