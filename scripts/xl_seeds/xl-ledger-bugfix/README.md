# ledger

Small-business bookkeeping: import bank exports, record manual entries,
and report balances and daily summaries in your business timezone.

```
ledger/            the package
  money.py         Money (integer cents, strict parsing, no float math)
  model.py         Transaction / Posting / Account + validation
  csvimport.py     the hand-rolled bank-export reader
  periods.py       business days in a configurable timezone
  store.py         JSON store (transactions.json, accounts.json)
  cache.py         in-memory report cache (LRU)
  reports.py       running balance, overdraft, daily summary
  service.py       LedgerService: imports, manual entries, reports
  cli.py           the `ledger` command line
```

## The import format

A bank export is a header row followed by one row per transaction:

```
timestamp,description,account,amount,currency
2026-01-05T14:30:00+00:00,Card payment,groceries,-54.20,USD
2026-01-05T15:00:00+00:00,"Payroll, net",checking,-3,120.00,USD
```

Rules (all of them are guarantees the rest of the tool builds on):

* **Every data row is imported.** The last row of a file is a real
  transaction like any other — with or without a trailing newline.
* Fields may be quoted; a quoted field may contain commas, and a literal
  quote inside a quoted field is doubled (`""`).
* Blank lines are skipped; `\n` and `\r\n` line endings both work.
* The header must be exactly `timestamp,description,account,amount,currency`.
* Timestamps are ISO-8601 **with an offset** (`Z` accepted). A naive
  timestamp is a validation error — the ledger never guesses a zone.
* Amounts are strict: optional `-`, optional thousands separators
  (correct groups of three), and a fractional part exactly as wide as
  the currency's minor units (`12.5` and `12.500` are errors for USD;
  JPY takes no fractional part at all).
* An import is atomic: every row is parsed and validated before anything
  is added, so a bad row adds nothing.

## Business days

Timestamps are stored in UTC. **A transaction's business day is the
calendar date of its timestamp expressed in the report timezone** —
`day_of(ts, tz)`. A Tokyo transaction recorded at 22:30 UTC belongs to
the *next* day in `Asia/Tokyo`; a New York transaction recorded at
02:30 UTC belongs to the *previous* day in `America/New_York`. DST is
handled by the zone database (`day_bounds` returns the true UTC instants
that bracket a business day, 23 or 25 hours long when a zone springs
forward or falls back).

## Manual entries and the report cache

`LedgerService` caches computed reports in a `ReportCache`. **Any
mutation that changes what a report would say must invalidate the
cache** — both imports and manual entries. A stale cache is a wrong
dashboard, which is worse than no cache.

## The account statement

`running_balance` orders a account's transactions **chronologically —
by timestamp, tie-broken by id** — so a backfilled entry lands where it
*happened*, not where it was typed. The running balance after each row
follows that order, and `first_overdraft` points at the first row that
goes below zero.

## Ids and the sync hook

Transaction ids are assigned by the store, starting at 1, and are
**unique**. `Store.allocate_hook` (a one-argument callable) is the sync
replication extension point: it runs after an id has been allocated. The
store guarantees that a hook that adds its own transaction can never
collide with the allocation in flight — ids are never reused and never
duplicated, even when the hook records concurrently with an import.

## CLI

```
python -m ledger.cli --store DIR import --file EXPORT.csv
python -m ledger.cli --store DIR record --description "Payroll" \
    --timestamp 2026-01-05T09:00:00+00:00 --currency USD \
    --posting checking:-3,120.00 --posting income:3,120.00
python -m ledger.cli --store DIR summary --day 2026-01-05
python -m ledger.cli --store DIR balance --account checking
python -m ledger.cli --store DIR list --account checking --limit 10
```

Exit code 0 on success, 1 on any ledger error (`ParseError`,
`ValidationError`, `DuplicateTransactionError`, `UnknownAccountError`).

## Reconciliation, export, statements

* `ledger.reconcile` matches the store against a bank statement export
  (same CSV shape): a match is the same amount + currency + normalized
  description, greedy in ledger order, each statement row used at most
  once. `reconcile(...).balanced` is True only when both sides are empty.
* `ledger.exporter` writes the store back out as bank CSV with the same
  quoting rules — an import -> export -> import round trip preserves
  every row.
* `ledger.statements` renders a one-business-day statement (opening,
  rows, closing) using `day_bounds`, so the period brackets midnight in
  the report timezone.
* `ledger.config` is the deployment file (`store_dir`, `timezone`,
  `base_currency`, `default_account_kind`, `report_cache_entries`):
  defaults when missing, `ValidationError` when bad.
* `ledger.audit` is an append-only JSONL audit log (`append`, `load`,
  `tail`); a missing file is an empty log, a corrupt line is an error.

## Data model

* `Money` — integer minor units + currency; strict parse/render; no
  float anywhere; mixed-currency arithmetic is an error, never a
  conversion. `allocate(weights)` splits an amount without losing or
  creating a single cent (largest remainder first).
* `Posting` — one account + one signed `Money` (positive = debit,
  negative = credit; `is_debit` / `is_credit`).
* `Transaction` — aware UTC timestamp (naive is rejected), non-empty
  description, at least one posting, one currency per transaction,
  `source` in {manual, import}; `amount_for(account)` sums its postings
  on one account, `total()` sums all of them.
* `Account` — name, kind in {asset, liability, income, expense,
  equity}, currency.
* `Store` — insertion order is the store's order; `all(account)`
  filters by posting presence; `balance(account)` sums every posting.

* `ledger.validate` — the month-end hygiene pass: `validate_store`
  reports duplicate ids, naive timestamps, unknown currencies/accounts,
  bad sources and empty descriptions as `Finding`s (`report.ok` when
  clean); it never mutates anything.

## Extending

The seams to build on: `Store.allocate_hook` (sync replication),
`ReportCache` (any new report kind must be invalidated by every mutation
that changes it), `ledger.csvimport.parse_csv` (new dialects), and the
`reports`/`statements` pure functions (they take iterables, so a new
source of transactions needs no store change).

## On-disk format

`transactions.json` is `{"transactions": [transaction dicts]}` in
insertion order; `accounts.json` is `{"accounts": {name: account}}`.
Money serializes as `{"cents": n, "currency": "USD"}` and timestamps as
ISO-8601 strings with an offset. The format is stable — treat it as an
interface.
