"""§80 horizon task set — long-horizon / unfamiliar-codebase fixtures.

The lever the 5 measured §80 rounds left untested (AGENTS.md, lever (a)):
every prior round sat under the same ceiling because the model solved each
fixture naked. Horizon fixtures raise the *exploration* cost, not the
cleverness cost —

- a realistic ~8-10 file package (the agent has never seen it; the prompt
  gives a SYMPTOM, never a file name);
- the bug lives 1-2 layers away from where the symptom shows up;
- two fixtures carry TWO independent bugs (fixing the visible one still
  leaves the suite red — the agent must keep going);
- the tempting fix at the symptom's location is pinned out by a second
  assertion, exactly like the multi set.

§34 discipline: every fixture is pre-verified FAILING-as-shipped AND
PASSING-after-root-fix by scripts/verify_horizon_fixtures.py before any
agent run. The root fixes below in FIXTURE_FIXES are the minimal
root-cause edits (never test patches).
"""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _t(name: str, files: dict[str, str], prompt: str) -> dict[str, Any]:
    """Same task shape as proof_loop._task (kept local to avoid a circular import)."""
    return {"name": name, "files": files, "prompt": prompt}


HORIZON_PROMPT = (
    "Fresh checkout of an unfamiliar project. Something is wrong with it: {symptom} "
    "The test suite has one or more failures. Explore the codebase, find the ROOT "
    "cause — the symptom's location is not necessarily where the cause lives, and "
    "there may be more than one thing broken — fix it, and make the whole suite "
    "green. Show the verification output."
)

HORIZON_TASKS: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# 1. ledger: reversal sign destroyed in posting, symptom shows in the report
# ---------------------------------------------------------------------------
HORIZON_TASKS.append(
    _t(
        "horizon-ledger-reversal",
        {
            "ledger/__init__.py": "",
            "ledger/entries.py": (
                "from dataclasses import dataclass\n"
                "\n"
                "\n"
                "@dataclass(frozen=True)\n"
                "class Entry:\n"
                "    account: str\n"
                "    amount_cents: int\n"
                '    kind: str  # "debit" | "credit"\n'
                "\n"
                "    def signed_cents(self) -> int:\n"
                '        """Balance convention: debits are positive, credits negative.\n'
                "\n"
                "        The sign of ``amount_cents`` itself is meaningful — reversal\n"
                "        and correction entries carry a NEGATIVE amount with their\n"
                "        kind intact (a negative credit contributes positively).\n"
                '        """\n'
                '        return self.amount_cents if self.kind == "debit" else -self.amount_cents\n'
            ),
            "ledger/journal.py": (
                "from ledger.entries import Entry\n"
                "\n"
                "\n"
                "class Journal:\n"
                "    def __init__(self) -> None:\n"
                "        self._entries: list[Entry] = []\n"
                "\n"
                "    def post(self, entry: Entry) -> None:\n"
                "        self._entries.append(entry)\n"
                "\n"
                "    def entries_for(self, account: str) -> list[Entry]:\n"
                "        return [e for e in self._entries if e.account == account]\n"
                "\n"
                "    def all(self) -> list[Entry]:\n"
                "        return list(self._entries)\n"
            ),
            "ledger/posting.py": (
                "from ledger.entries import Entry\n"
                "from ledger.journal import Journal\n"
                "\n"
                "\n"
                "def post_to(journal: Journal, account: str, amount_cents: int, "
                "kind: str) -> Entry:\n"
                '    """Post an entry to ``account``.\n'
                "\n"
                "    Reversals and corrections arrive as negative amounts with\n"
                "    their original kind — the sign travels with the entry into\n"
                "    the journal, which is the source of truth for reports.\n"
                '    """\n'
                '    if kind not in ("debit", "credit"):\n'
                '        raise ValueError(f"unknown kind: {kind}")\n'
                "    entry = Entry(account, abs(amount_cents), kind)\n"
                "    journal.post(entry)\n"
                "    return entry\n"
            ),
            "ledger/report.py": (
                "from ledger.journal import Journal\n"
                "\n"
                "\n"
                "def balance(journal: Journal, account: str) -> int:\n"
                '    """Signed balance in cents: debits positive, credits negative."""\n'
                "    return sum(e.signed_cents() for e in journal.entries_for(account))\n"
            ),
            "ledger/cli.py": (
                "from ledger.journal import Journal\n"
                "from ledger.report import balance\n"
                "\n"
                "\n"
                "def print_report(journal: Journal, accounts: list[str]) -> None:\n"
                "    for account in accounts:\n"
                '        print(f"{account}: {balance(journal, account) / 100:.2f}")\n'
            ),
            "test_entries.py": (
                "from ledger.entries import Entry\n"
                "\n"
                "\n"
                "def test_signed_cents_debit_positive_credit_negative() -> None:\n"
                '    assert Entry("cash", 500, "debit").signed_cents() == 500\n'
                '    assert Entry("cash", 500, "credit").signed_cents() == -500\n'
                "\n"
                "\n"
                "def test_negative_amount_keeps_its_kind() -> None:\n"
                '    e = Entry("cash", -500, "credit")\n'
                '    assert e.kind == "credit"\n'
                "    assert e.signed_cents() == 500\n"
            ),
            "test_journal.py": (
                "from ledger.entries import Entry\n"
                "from ledger.journal import Journal\n"
                "\n"
                "\n"
                "def test_entries_for_filters_by_account() -> None:\n"
                "    j = Journal()\n"
                '    j.post(Entry("cash", 100, "debit"))\n'
                '    j.post(Entry("revenue", 200, "credit"))\n'
                '    j.post(Entry("cash", 50, "credit"))\n'
                '    assert [e.amount_cents for e in j.entries_for("cash")] == [100, 50]\n'
                "\n"
                "\n"
                "def test_all_returns_a_copy() -> None:\n"
                "    j = Journal()\n"
                '    j.post(Entry("cash", 100, "debit"))\n'
                "    j.all().clear()\n"
                "    assert len(j.all()) == 1\n"
            ),
            "test_posting.py": (
                "from ledger.journal import Journal\n"
                "from ledger.posting import post_to\n"
                "\n"
                "\n"
                "def test_posting_preserves_reversal_sign() -> None:\n"
                '    """Reversal entries must reach the journal with their negative sign.\n'
                "\n"
                "    The journal is the source of truth — a report can never\n"
                "    reconstruct a sign that posting destroyed.\n"
                '    """\n'
                "    j = Journal()\n"
                '    entry = post_to(j, "revenue", -250, "credit")\n'
                "    assert entry.amount_cents == -250\n"
                '    assert j.entries_for("revenue")[0].amount_cents == -250\n'
                "\n"
                "\n"
                "def test_posting_rejects_unknown_kind() -> None:\n"
                "    j = Journal()\n"
                "    try:\n"
                '        post_to(j, "cash", 100, "debit?")\n'
                '        raise AssertionError("expected ValueError")\n'
                "    except ValueError:\n"
                "        pass\n"
            ),
            "test_report.py": (
                "from ledger.journal import Journal\n"
                "from ledger.posting import post_to\n"
                "from ledger.report import balance\n"
                "\n"
                "\n"
                "def test_balance_counts_reversal_with_its_sign() -> None:\n"
                "    j = Journal()\n"
                '    post_to(j, "revenue", 400, "credit")\n'
                '    post_to(j, "revenue", -150, "credit")  # partial reversal\n'
                '    assert balance(j, "revenue") == -250\n'
                "\n"
                "\n"
                "def test_balance_on_a_simple_journal() -> None:\n"
                "    j = Journal()\n"
                '    post_to(j, "cash", 100, "debit")\n'
                '    post_to(j, "cash", 30, "credit")\n'
                '    assert balance(j, "cash") == 70\n'
            ),
        },
        HORIZON_PROMPT.format(
            symptom="the monthly balance printed for one account is wrong "
            "(a partial reversal seems to count twice)."
        ),
    )
)

# ---------------------------------------------------------------------------
# 2. scheduler: Queue.add inserts at the FRONT (LIFO) — jobs complete
#    newest-first and retried jobs preempt older ones; the audit trail is
#    ordered wrong. Root fix is in queue.add; the tempting fix (pop from the
#    other end) is pinned out by the pending-order contract test.
# ---------------------------------------------------------------------------
HORIZON_TASKS.append(
    _t(
        "horizon-scheduler-order",
        {
            "scheduler/__init__.py": "",
            "scheduler/jobs.py": (
                "from dataclasses import dataclass, field\n"
                "\n"
                "\n"
                "@dataclass\n"
                "class Job:\n"
                "    job_id: str\n"
                "    name: str\n"
                "    attempts: int = 0\n"
                "    log: list[str] = field(default_factory=list)\n"
                "\n"
                "    def record(self, message: str) -> None:\n"
                '        self.log.append(f"attempt {self.attempts}: {message}")\n'
            ),
            "scheduler/queue.py": (
                "from scheduler.jobs import Job\n"
                "\n"
                "\n"
                "class Queue:\n"
                '    """A FIFO job queue: oldest job first, exactly as submitted."""\n'
                "\n"
                "    def __init__(self) -> None:\n"
                "        self._jobs: list[Job] = []\n"
                "\n"
                "    def add(self, job: Job) -> None:\n"
                "        self._jobs.insert(0, job)\n"
                "\n"
                "    def next(self) -> Job | None:\n"
                "        if not self._jobs:\n"
                "            return None\n"
                "        return self._jobs.pop(0)\n"
                "\n"
                "    def pending(self) -> list[Job]:\n"
                '        """Jobs waiting, OLDEST first (submission order)."""\n'
                "        return list(self._jobs)\n"
            ),
            "scheduler/retry.py": (
                "from scheduler.jobs import Job\n"
                "\n"
                "\n"
                "MAX_ATTEMPTS = 3\n"
                "\n"
                "\n"
                "def should_retry(job: Job, outcome: str) -> bool:\n"
                '    return outcome == "failed" and job.attempts < MAX_ATTEMPTS\n'
            ),
            "scheduler/runner.py": (
                "from scheduler.queue import Queue\n"
                "from scheduler.retry import should_retry\n"
                "\n"
                "\n"
                "def run_all(queue: Queue, run_job) -> list[str]:\n"
                '    """Run every queued job to completion, OLDEST first (with retries).\n'
                "\n"
                '    ``run_job`` maps a Job to "ok" | "failed"; a failed job goes\n'
                "    back on the queue while the retry policy allows it — behind\n"
                "    the jobs that were already waiting.\n"
                '    """\n'
                "    done: list[str] = []\n"
                "    while (job := queue.next()) is not None:\n"
                "        job.attempts += 1\n"
                "        outcome = run_job(job)\n"
                "        job.record(outcome)\n"
                '        if outcome == "ok":\n'
                "            done.append(job.job_id)\n"
                "        elif should_retry(job, outcome):\n"
                "            queue.add(job)\n"
                "    return done\n"
            ),
            "scheduler/audit.py": (
                "from scheduler.jobs import Job\n"
                "\n"
                "\n"
                "def render_audit(jobs: list[Job]) -> str:\n"
                '    """One line per completed job, in COMPLETION order."""\n'
                '    return "\\n".join(f"{j.job_id}: {j.name}" for j in jobs)\n'
            ),
            "test_jobs.py": (
                "from scheduler.jobs import Job\n"
                "\n"
                "\n"
                "def test_record_stamps_the_attempt() -> None:\n"
                '    j = Job("j1", "cleanup")\n'
                "    j.attempts = 2\n"
                '    j.record("ok")\n'
                '    assert j.log == ["attempt 2: ok"]\n'
            ),
            "test_retry.py": (
                "from scheduler.jobs import Job\n"
                "from scheduler.retry import should_retry\n"
                "\n"
                "\n"
                "def test_failed_jobs_retry_until_the_cap() -> None:\n"
                '    j = Job("j1", "cleanup")\n'
                '    assert should_retry(j, "failed")\n'
                "    j.attempts = 3\n"
                '    assert not should_retry(j, "failed")\n'
                "\n"
                "\n"
                "def test_ok_jobs_never_retry() -> None:\n"
                '    j = Job("j1", "cleanup")\n'
                '    assert not should_retry(j, "ok")\n'
            ),
            "test_queue.py": (
                "from scheduler.jobs import Job\n"
                "from scheduler.queue import Queue\n"
                "\n"
                "\n"
                "def test_add_appends_in_submission_order() -> None:\n"
                '    """The queue contract: add() enqueues at the BACK."""\n'
                "    q = Queue()\n"
                '    q.add(Job("j1", "cleanup"))\n'
                '    q.add(Job("j2", "backup"))\n'
                '    assert [j.job_id for j in q.pending()] == ["j1", "j2"]\n'
                "\n"
                "\n"
                "def test_next_returns_the_oldest_job() -> None:\n"
                "    q = Queue()\n"
                '    q.add(Job("j1", "cleanup"))\n'
                '    q.add(Job("j2", "backup"))\n'
                '    assert q.next().job_id == "j1"\n'
                '    assert q.next().job_id == "j2"\n'
                "    assert q.next() is None\n"
            ),
            "test_runner.py": (
                "from scheduler.jobs import Job\n"
                "from scheduler.queue import Queue\n"
                "from scheduler.runner import run_all\n"
                "\n"
                "\n"
                "def _queue(*jobs: Job) -> Queue:\n"
                "    q = Queue()\n"
                "    for j in jobs:\n"
                "        q.add(j)\n"
                "    return q\n"
                "\n"
                "\n"
                "def test_jobs_complete_in_submission_order() -> None:\n"
                '    q = _queue(Job("j1", "cleanup"), Job("j2", "backup"), Job("j3", "report"))\n'
                '    assert run_all(q, lambda job: "ok") == ["j1", "j2", "j3"]\n'
                "\n"
                "\n"
                "def test_a_retried_job_does_not_preempt_older_ones() -> None:\n"
                '    q = _queue(Job("j1", "cleanup"), Job("j2", "backup"), Job("j3", "report"))\n'
                "    calls: list[str] = []\n"
                "\n"
                "    def flaky_first(job: Job) -> str:\n"
                "        calls.append(job.job_id)\n"
                '        return "failed" if len(calls) == 1 else "ok"\n'
                "\n"
                "    # j1 fails once and goes back BEHIND j2/j3 — it must not preempt them\n"
                '    assert run_all(q, flaky_first) == ["j2", "j3", "j1"]\n'
            ),
        },
        HORIZON_PROMPT.format(
            symptom="the audit trail shows jobs completing NEWEST-first, and a "
            "job that failed once seems to preempt jobs that were waiting "
            "before it."
        ),
    )
)

# ---------------------------------------------------------------------------
# 3. catalog: sort_items mutates the caller's list AND search narrows the
#    catalog in place — two bugs, both far from the rendered symptom.
# ---------------------------------------------------------------------------
HORIZON_TASKS.append(
    _t(
        "horizon-catalog-order",
        {
            "catalog/__init__.py": "",
            "catalog/items.py": (
                "from dataclasses import dataclass\n"
                "\n"
                "\n"
                "@dataclass(frozen=True)\n"
                "class Item:\n"
                "    id: str\n"
                "    name: str\n"
                "    price_cents: int\n"
            ),
            "catalog/sort.py": (
                "from catalog.items import Item\n"
                "\n"
                "\n"
                'def sort_items(items: list[Item], by: str = "name") -> list[Item]:\n'
                '    """Return ``items`` ordered by ``by`` ("name" or "price"), id as tiebreak.\n'
                "\n"
                "    The caller's list is left untouched — ordering is a view,\n"
                "    never a side effect.\n"
                '    """\n'
                '    if by == "name":\n'
                "        items.sort(key=lambda i: (i.name, i.id))\n"
                '    elif by == "price":\n'
                "        items.sort(key=lambda i: (i.price_cents, i.id))\n"
                "    else:\n"
                '        raise ValueError(f"unknown sort key: {by}")\n'
                "    return items\n"
            ),
            "catalog/query.py": (
                "from catalog.items import Item\n"
                "from catalog.sort import sort_items\n"
                "\n"
                "\n"
                "def search(catalog: list[Item], needle: str) -> list[Item]:\n"
                '    """Return the matching items in name order.\n'
                "\n"
                "    Works directly on the catalog for large-catalog efficiency:\n"
                "    matching narrows ``catalog`` in place, then orders it.\n"
                '    """\n'
                "    catalog[:] = [i for i in catalog if needle.lower() in i.name.lower()]\n"
                '    return sort_items(catalog, by="name")\n'
                "\n"
                "\n"
                "def page(items: list[Item], offset: int, limit: int) -> list[Item]:\n"
                "    return items[offset : offset + limit]\n"
            ),
            "catalog/report.py": (
                "from catalog.items import Item\n"
                "\n"
                "\n"
                "def render(catalog: list[Item]) -> str:\n"
                '    """Human listing, in CATALOG (insertion) order."""\n'
                '    return "\\n".join(f"{i.id}: {i.name} ({i.price_cents / 100:.2f})" '
                "for i in catalog)\n"
            ),
            "catalog/app.py": (
                "from catalog.items import Item\n"
                "from catalog.query import page, search\n"
                "from catalog.report import render\n"
                "\n"
                "\n"
                "def search_page(catalog: list[Item], needle: str, offset: int, "
                "limit: int) -> str:\n"
                "    return render(page(search(catalog, needle), offset, limit))\n"
            ),
            "test_sort.py": (
                "from catalog.items import Item\n"
                "from catalog.sort import sort_items\n"
                "\n"
                "\n"
                "def _items() -> list[Item]:\n"
                '    return [Item("c", "carrot", 300), Item("a", "apple", 100), '
                'Item("b", "banana", 200)]\n'
                "\n"
                "\n"
                "def test_sort_does_not_mutate_the_caller_list() -> None:\n"
                '    """Ordering is a view: the caller keeps insertion order."""\n'
                "    items = _items()\n"
                '    sort_items(items, by="name")\n'
                '    assert [i.id for i in items] == ["c", "a", "b"]\n'
                "\n"
                "\n"
                "def test_sort_orders_by_name_with_id_tiebreak() -> None:\n"
                '    assert [i.id for i in sort_items(_items(), by="name")] '
                '== ["a", "b", "c"]\n'
                "\n"
                "\n"
                "def test_sort_orders_by_price() -> None:\n"
                '    assert [i.id for i in sort_items(_items(), by="price")] '
                '== ["a", "b", "c"]\n'
                "\n"
                "\n"
                "def test_sort_rejects_unknown_key() -> None:\n"
                "    try:\n"
                '        sort_items(_items(), by="weight")\n'
                '        raise AssertionError("expected ValueError")\n'
                "    except ValueError:\n"
                "        pass\n"
            ),
            "test_query.py": (
                "from catalog.items import Item\n"
                "from catalog.query import page, search\n"
                "\n"
                "\n"
                "def _catalog() -> list[Item]:\n"
                '    return [Item("c", "carrot", 300), Item("a", "apple", 100), '
                'Item("b", "banana", 200)]\n'
                "\n"
                "\n"
                "def test_search_matches_substring_case_insensitive() -> None:\n"
                '    assert [i.id for i in search(_catalog(), "APP")] == ["a"]\n'
                "\n"
                "\n"
                "def test_page_slices() -> None:\n"
                "    items = _catalog()\n"
                '    assert [i.id for i in page(items, 1, 2)] == ["a", "b"]\n'
            ),
            "test_report.py": (
                "from catalog.items import Item\n"
                "from catalog.query import search\n"
                "from catalog.report import render\n"
                "\n"
                "\n"
                "def test_render_keeps_insertion_order_after_a_search_ran() -> None:\n"
                '    """A search must never reorder or shrink the caller\'s catalog."""\n'
                '    catalog = [Item("c", "carrot", 300), Item("a", "apple", 100), '
                'Item("b", "banana", 200)]\n'
                '    search(catalog, "an")\n'
                '    assert "c: carrot" in render(catalog)\n'
                '    assert render(catalog).splitlines()[0].startswith("c:")\n'
            ),
        },
        HORIZON_PROMPT.format(
            symptom="after a search runs, the catalog listing renders in a "
            "different order — and shrunken: items that were there before "
            "the search are gone."
        ),
    )
)

# ---------------------------------------------------------------------------
# 4. settings: migration drops the labels mapping AND the env overlay
#    crashes on set-but-empty variables — two bugs, two modules.
# ---------------------------------------------------------------------------
HORIZON_TASKS.append(
    _t(
        "horizon-settings-migration",
        {
            "settings/__init__.py": "",
            "settings/schema.py": (
                "CURRENT_VERSION = 2\n"
                "\n"
                'DEFAULTS = {"retries": 3, "timeout": 30}\n'
                "\n"
                "\n"
                "def validate(doc: dict) -> None:\n"
                '    """A v2 document carries version, retries, timeout and a labels mapping."""\n'
                '    if doc.get("version") != CURRENT_VERSION:\n'
                '        raise ValueError(f"expected version {CURRENT_VERSION}, '
                "got {doc.get('version')!r}\")\n"
                '    if not isinstance(doc.get("retries"), int):\n'
                '        raise ValueError("retries must be an int")\n'
                '    if not isinstance(doc.get("timeout"), int):\n'
                '        raise ValueError("timeout must be an int")\n'
                '    if not isinstance(doc.get("labels", {}), dict):\n'
                '        raise ValueError("labels must be a mapping")\n'
            ),
            "settings/migrate.py": (
                "from settings.schema import CURRENT_VERSION\n"
                "\n"
                "\n"
                "def migrate(doc: dict) -> dict:\n"
                '    """Bring a v1 document up to the current schema.\n'
                "\n"
                "    v1 carried {retries, timeout, labels}; v2 adds an explicit\n"
                "    version field. EVERY v1 field must survive the migration\n"
                "    unchanged — a migration that silently drops user data is\n"
                "    a data-loss incident, not a convenience.\n"
                '    """\n'
                '    if doc.get("version") == CURRENT_VERSION:\n'
                "        return dict(doc)\n"
                '    if doc.get("version") != 1:\n'
                '        raise ValueError(f"cannot migrate from version '
                "{doc.get('version')!r}\")\n"
                "    migrated = {\n"
                '        "version": CURRENT_VERSION,\n'
                '        "retries": doc.get("retries", 3),\n'
                '        "timeout": doc.get("timeout", 30),\n'
                "    }\n"
                "    return migrated\n"
            ),
            "settings/loader.py": (
                "from settings.migrate import migrate\n"
                "from settings.schema import DEFAULTS, validate\n"
                "\n"
                "\n"
                "def load(doc: dict) -> dict:\n"
                '    """Load a config document (any version) into validated current form."""\n'
                "    migrated = migrate(doc)\n"
                "    validate(migrated)\n"
                "    return {**DEFAULTS, **migrated}\n"
            ),
            "settings/env.py": (
                "import os\n"
                "\n"
                "\n"
                "def apply_env(doc: dict) -> dict:\n"
                '    """Overlay ACI_ env vars on a loaded config.\n'
                "\n"
                "    An EMPTY variable is not a value — it must be ignored,\n"
                "    leaving the document's own setting in place.\n"
                '    """\n'
                "    out = dict(doc)\n"
                '    for key in ("retries", "timeout"):\n'
                '        raw = os.environ.get(f"ACI_{key.upper()}")\n'
                "        if raw is not None:\n"
                "            out[key] = int(raw)\n"
                "    return out\n"
            ),
            "test_schema.py": (
                "from settings.schema import validate\n"
                "\n"
                "\n"
                "def test_valid_v2_document_passes() -> None:\n"
                '    validate({"version": 2, "retries": 3, "timeout": 30, "labels": {}})\n'
                "\n"
                "\n"
                "def test_wrong_version_is_rejected() -> None:\n"
                "    try:\n"
                '        validate({"version": 1, "retries": 3, "timeout": 30})\n'
                '        raise AssertionError("expected ValueError")\n'
                "    except ValueError:\n"
                "        pass\n"
            ),
            "test_migrate.py": (
                "from settings.migrate import migrate\n"
                "\n"
                "\n"
                "def test_v1_labels_survive_the_migration() -> None:\n"
                '    """Migration must carry user data across unchanged."""\n'
                '    v1 = {"version": 1, "retries": 5, "timeout": 60, '
                '"labels": {"team": "core"}}\n'
                "    migrated = migrate(v1)\n"
                '    assert migrated["labels"] == {"team": "core"}\n'
                "\n"
                "\n"
                "def test_v1_scalars_survive_the_migration() -> None:\n"
                '    migrated = migrate({"version": 1, "retries": 5, "timeout": 60})\n'
                '    assert migrated["retries"] == 5\n'
                '    assert migrated["timeout"] == 60\n'
                '    assert migrated["version"] == 2\n'
                "\n"
                "\n"
                "def test_unknown_version_is_rejected() -> None:\n"
                "    try:\n"
                '        migrate({"version": 7})\n'
                '        raise AssertionError("expected ValueError")\n'
                "    except ValueError:\n"
                "        pass\n"
            ),
            "test_loader.py": (
                "from settings.loader import load\n"
                "\n"
                "\n"
                "def test_load_migrates_and_fills_defaults() -> None:\n"
                '    loaded = load({"version": 1, "retries": 5, '
                '"labels": {"team": "core"}})\n'
                '    assert loaded["retries"] == 5\n'
                '    assert loaded["timeout"] == 30\n'
                '    assert loaded["labels"] == {"team": "core"}\n'
            ),
            "test_env.py": (
                "import os\n"
                "\n"
                "from settings.env import apply_env\n"
                "\n"
                "\n"
                "def test_set_var_overrides() -> None:\n"
                '    os.environ["ACI_RETRIES"] = "9"\n'
                "    try:\n"
                '        out = apply_env({"retries": 3, "timeout": 30})\n'
                '        assert out["retries"] == 9\n'
                "    finally:\n"
                '        del os.environ["ACI_RETRIES"]\n'
                "\n"
                "\n"
                "def test_empty_var_is_ignored_not_a_crash() -> None:\n"
                '    """An empty variable leaves the document\'s own value in place."""\n'
                '    os.environ["ACI_RETRIES"] = ""\n'
                "    try:\n"
                '        out = apply_env({"retries": 3, "timeout": 30})\n'
                '        assert out["retries"] == 3\n'
                "    finally:\n"
                '        del os.environ["ACI_RETRIES"]\n'
            ),
        },
        HORIZON_PROMPT.format(
            symptom="loading an old (v1) config file loses the labels section, "
            "and the env overlay crashes when a variable is set but empty."
        ),
    )
)

# ---------------------------------------------------------------------------
# 5. cache: codec strips tzinfo on load — TTL arithmetic then crashes
#    against the aware clock. Root fix in codec; the tempting fix (strip tz
#    from the clock instead) is pinned out by the round-trip test.
# ---------------------------------------------------------------------------
HORIZON_TASKS.append(
    _t(
        "horizon-cache-tz",
        {
            "cache/__init__.py": "",
            "cache/clock.py": (
                "from datetime import UTC, datetime\n"
                "\n"
                "\n"
                "class Clock:\n"
                '    """Time source. ``now()`` is always timezone-AWARE (UTC)."""\n'
                "\n"
                "    def now(self) -> datetime:\n"
                "        return datetime.now(UTC)\n"
                "\n"
                "\n"
                "class FakeClock(Clock):\n"
                "    def __init__(self, start: datetime) -> None:\n"
                "        self._start = start\n"
                "\n"
                "    def now(self) -> datetime:\n"
                "        return self._start\n"
            ),
            "cache/codec.py": (
                "import json\n"
                "from datetime import UTC, datetime\n"
                "\n"
                "\n"
                "def dumps(value: dict) -> str:\n"
                '    """Serialize a cache entry. Datetimes round-trip as UTC."""\n'
                "\n"
                "    def enc(v):\n"
                "        if isinstance(v, datetime):\n"
                '            return {"__dt__": v.astimezone(UTC).isoformat()}\n'
                '        raise TypeError(f"not JSON-serializable: {type(v)!r}")\n'
                "\n"
                "    return json.dumps(value, default=enc)\n"
                "\n"
                "\n"
                "def loads(raw: str) -> dict:\n"
                "    def dec(v):\n"
                '        if "__dt__" in v:\n'
                "            # naive UTC internally — arithmetic is simpler without offsets\n"
                '            return datetime.fromisoformat(v["__dt__"]).replace(tzinfo=None)\n'
                "        return v\n"
                "\n"
                "    return json.loads(raw, object_hook=dec)\n"
            ),
            "cache/store.py": (
                "from cache.codec import dumps, loads\n"
                "\n"
                "\n"
                "class Store:\n"
                "    def __init__(self) -> None:\n"
                "        self._data: dict[str, str] = {}\n"
                "\n"
                "    def put(self, key: str, value: dict) -> None:\n"
                "        self._data[key] = dumps(value)\n"
                "\n"
                "    def get(self, key: str) -> dict | None:\n"
                "        raw = self._data.get(key)\n"
                "        return loads(raw) if raw is not None else None\n"
            ),
            "cache/ttl.py": (
                "from datetime import datetime\n"
                "\n"
                "\n"
                "def expired(entry: dict, now: datetime) -> bool:\n"
                '    """True when ``entry`` is older than its TTL at ``now``."""\n'
                '    stored_at: datetime = entry["stored_at"]\n'
                "    age = (now - stored_at).total_seconds()\n"
                '    return age > entry["ttl_seconds"]\n'
            ),
            "cache/service.py": (
                "from cache.clock import Clock\n"
                "from cache.store import Store\n"
                "from cache.ttl import expired\n"
                "\n"
                "\n"
                "class CacheService:\n"
                "    def __init__(self, store: Store, clock: Clock, "
                "ttl_seconds: int = 60) -> None:\n"
                "        self._store = store\n"
                "        self._clock = clock\n"
                "        self._ttl = ttl_seconds\n"
                "\n"
                "    def put(self, key: str, value: dict) -> None:\n"
                '        self._store.put(key, {"value": value, '
                '"stored_at": self._clock.now(), "ttl_seconds": self._ttl})\n'
                "\n"
                "    def get(self, key: str) -> dict | None:\n"
                "        entry = self._store.get(key)\n"
                "        if entry is None or expired(entry, self._clock.now()):\n"
                "            return None\n"
                '        return entry["value"]\n'
            ),
            "test_codec.py": (
                "from datetime import UTC, datetime\n"
                "\n"
                "from cache.codec import dumps, loads\n"
                "\n"
                "\n"
                "def test_datetime_round_trips_timezone_aware() -> None:\n"
                '    """A cached datetime comes back AWARE — the cache never flattens time."""\n'
                "    when = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)\n"
                '    out = loads(dumps({"at": when}))\n'
                '    assert out["at"] == when\n'
                '    assert out["at"].tzinfo is not None\n'
                "\n"
                "\n"
                "def test_plain_values_round_trip() -> None:\n"
                '    out = loads(dumps({"a": 1, "b": "x"}))\n'
                '    assert out == {"a": 1, "b": "x"}\n'
            ),
            "test_store.py": (
                "from cache.store import Store\n"
                "\n"
                "\n"
                "def test_put_then_get_round_trips() -> None:\n"
                "    s = Store()\n"
                '    s.put("k", {"a": 1})\n'
                '    assert s.get("k") == {"a": 1}\n'
                "\n"
                "\n"
                "def test_missing_key_is_none() -> None:\n"
                '    assert Store().get("nope") is None\n'
            ),
            "test_ttl.py": (
                "from datetime import UTC, datetime, timedelta\n"
                "\n"
                "from cache.ttl import expired\n"
                "\n"
                "\n"
                "def test_fresh_entry_is_not_expired() -> None:\n"
                "    now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)\n"
                '    entry = {"stored_at": now - timedelta(seconds=10), "ttl_seconds": 60}\n'
                "    assert not expired(entry, now)\n"
                "\n"
                "\n"
                "def test_stale_entry_is_expired() -> None:\n"
                "    now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)\n"
                '    entry = {"stored_at": now - timedelta(seconds=61), "ttl_seconds": 60}\n'
                "    assert expired(entry, now)\n"
            ),
            "test_service.py": (
                "from datetime import UTC, datetime, timedelta\n"
                "\n"
                "from cache.clock import FakeClock\n"
                "from cache.service import CacheService\n"
                "from cache.store import Store\n"
                "\n"
                "\n"
                "def test_fresh_value_is_readable() -> None:\n"
                "    clock = FakeClock(datetime(2026, 9, 28, 12, 0, tzinfo=UTC))\n"
                "    svc = CacheService(Store(), clock, ttl_seconds=60)\n"
                '    svc.put("k", {"a": 1})\n'
                '    assert svc.get("k") == {"a": 1}\n'
                "\n"
                "\n"
                "def test_stale_value_is_a_miss() -> None:\n"
                "    clock = FakeClock(datetime(2026, 9, 28, 12, 0, tzinfo=UTC))\n"
                "    svc = CacheService(Store(), clock, ttl_seconds=60)\n"
                '    svc.put("k", {"a": 1})\n'
                "    clock._start = clock._start + timedelta(seconds=61)\n"
                '    assert svc.get("k") is None\n'
            ),
        },
        HORIZON_PROMPT.format(
            symptom="reading back a value that was JUST cached raises a "
            "TypeError down in the TTL check."
        ),
    )
)
