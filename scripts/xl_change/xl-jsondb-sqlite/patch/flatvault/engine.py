"""The sqlite3 engine behind the flatvault facade.

One sqlite database per data directory (``<root>/flatvault.db``); one
sql table per flatvault table (``t_<name>``, columns ``id`` + ``doc`` —
the doc is the same JSON the old engine wrote, datetime markers and
all). Ids come from a per-table counter in ``vault_meta`` and are
never reused. Every write is one sqlite transaction — crash-safe by
construction. Legacy ``<name>.json`` files are imported once, on first
open, and then left in place untouched as the backup.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from flatvault import serialize
from flatvault.errors import TableError
from flatvault.queries import Query, match

META_TABLE = "vault_meta"


def _ident(name: str) -> str:
    """A quoted sqlite identifier for one flatvault table."""
    if not name or any(ch in name for ch in ("/", "\\", "\x00")):
        raise TableError(f"bad table name {name!r}")
    return '"' + ("t_" + name).replace('"', '""') + '"'


class Engine:
    """All sql, no policy — the facades keep the public API.

    The connection is created with ``check_same_thread=False`` and every
    operation holds one reentrant lock: the app layer's stdlib HTTP
    server answers each request on its own thread, and the engine must
    survive that (the JSON engine always did).
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "flatvault.db"
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        with self._lock:
            self.conn.execute(
                f"CREATE TABLE IF NOT EXISTS {META_TABLE} (key TEXT PRIMARY KEY, value TEXT)"
            )
            self._migrate_legacy_files()

    # ---------------------------------------------------------------- schema

    def _ensure_table(self, name: str) -> None:
        self.conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_ident(name)} (id INTEGER PRIMARY KEY, doc TEXT NOT NULL)"
        )

    def _table_exists(self, name: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (f"t_{name}",),
        ).fetchone()
        return row is not None

    def table_names(self) -> list[str]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 't_%'"
            ).fetchall()
        names = {row[0][2:] for row in rows}  # strip the t_ prefix
        return sorted(names)

    # ------------------------------------------------------------- migration

    def _migrate_legacy_files(self) -> None:
        """Import every legacy ``<name>.json`` once; keep the files."""
        for json_path in sorted(self.root.glob("*.json")):
            name = json_path.stem
            if self._table_exists(name):
                continue  # already migrated — the sqlite file wins
            try:
                doc = serialize.read_document(json_path)
            except Exception:
                continue  # not a table file — leave it alone
            if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
                continue
            rows = [row for row in doc["rows"] if isinstance(row, dict)]
            self._ensure_table(name)
            with self._atomic():
                for row in rows:
                    if isinstance(row.get("id"), int):
                        self.conn.execute(
                            f"INSERT OR REPLACE INTO {_ident(name)} (id, doc) VALUES (?, ?)",
                            (row["id"], serialize.dump(row)),
                        )
                ids = [row["id"] for row in rows if isinstance(row.get("id"), int)]
                next_id = doc.get("next_id")
                if not isinstance(next_id, int):
                    next_id = max(ids, default=0) + 1
                self._set_meta(f"next_id:{name}", str(next_id))

    # ------------------------------------------------------------------ meta

    def _get_meta(self, key: str) -> str | None:
        row = self.conn.execute(f"SELECT value FROM {META_TABLE} WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            f"INSERT OR REPLACE INTO {META_TABLE} (key, value) VALUES (?, ?)",
            (key, value),
        )

    # ------------------------------------------------------------ atomic ops

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        """One locked sqlite transaction unless an outer one is open."""
        with self._lock:
            if self.conn.in_transaction:
                yield
                return
            self.conn.execute("BEGIN")
            try:
                yield
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    def _allocate_id(self, name: str) -> int:
        """The next id: the counter, or above the current maximum."""
        raw = self._get_meta(f"next_id:{name}")
        counter = int(raw) if raw is not None else 1
        row = self.conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {_ident(name)}").fetchone()
        candidate = max(counter, (row[0] if row else 0) + 1)
        self._set_meta(f"next_id:{name}", str(candidate + 1))
        return candidate

    # ------------------------------------------------------------------ CRUD

    def insert(self, name: str, doc: dict[str, Any]) -> int:
        with self._atomic():
            self._ensure_table(name)
            row_id = self._allocate_id(name)
            row = dict(doc)
            row["id"] = row_id
            self.conn.execute(
                f"INSERT INTO {_ident(name)} (id, doc) VALUES (?, ?)",
                (row_id, serialize.dump(row)),
            )
        return row_id

    def get(self, name: str, row_id: int) -> dict[str, Any] | None:
        with self._lock:
            self._ensure_table(name)
            row = self.conn.execute(
                f"SELECT doc FROM {_ident(name)} WHERE id = ?", (row_id,)
            ).fetchone()
        return serialize.load(row[0]) if row else None

    def iter_rows(self, name: str) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_table(name)
            rows = self.conn.execute(f"SELECT doc FROM {_ident(name)} ORDER BY id").fetchall()
        return [serialize.load(row[0]) for row in rows]

    def find(self, name: str, query: Query | None = None) -> list[dict[str, Any]]:
        rows = self.iter_rows(name)
        if query is None:
            return rows
        return [row for row in rows if match(query, row)]

    def update(self, name: str, row_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
        if "id" in fields:
            raise TableError("id cannot be updated")
        with self._atomic():
            current = self.get(name, row_id)
            if current is None:
                return None
            current.update(fields)
            self.conn.execute(
                f"UPDATE {_ident(name)} SET doc = ? WHERE id = ?",
                (serialize.dump(current), row_id),
            )
        return current

    def delete(self, name: str, row_id: int) -> bool:
        with self._atomic():
            self._ensure_table(name)
            cursor = self.conn.execute(f"DELETE FROM {_ident(name)} WHERE id = ?", (row_id,))
        return cursor.rowcount > 0

    def count(self, name: str, query: Query | None = None) -> int:
        if query is None:
            with self._lock:
                self._ensure_table(name)
                row = self.conn.execute(f"SELECT COUNT(*) FROM {_ident(name)}").fetchone()
            return int(row[0])
        return len(self.find(name, query))

    def delete_where(self, name: str, query: Query) -> int:
        with self._atomic():
            self._ensure_table(name)
            victims = [row["id"] for row in self.find(name, query)]
            for victim in victims:
                self.conn.execute(f"DELETE FROM {_ident(name)} WHERE id = ?", (victim,))
        return len(victims)

    def drop(self, name: str) -> None:
        with self._atomic():
            self.conn.execute(f"DROP TABLE IF EXISTS {_ident(name)}")
            self.conn.execute(f"DELETE FROM {META_TABLE} WHERE key = ?", (f"next_id:{name}",))

    # ----------------------------------------------------------- transactions

    def begin(self) -> None:
        with self._lock:
            self.conn.execute("BEGIN")

    def commit(self) -> None:
        with self._lock:
            self.conn.execute("COMMIT")

    def rollback(self) -> None:
        with self._lock:
            self.conn.execute("ROLLBACK")

    def savepoint(self, name: str) -> None:
        """Open a savepoint inside the current transaction."""
        with self._lock:
            self.conn.execute(f"SAVEPOINT {name}")

    def rollback_to(self, name: str) -> None:
        """Undo everything back to (and keep) a savepoint."""
        with self._lock:
            self.conn.execute(f"ROLLBACK TO {name}")

    def release(self, name: str) -> None:
        """Close a savepoint (its work joins the outer transaction)."""
        with self._lock:
            self.conn.execute(f"RELEASE {name}")

    def batch_insert(self, name: str, docs: list[dict[str, Any]]) -> list[int]:
        """Insert many rows in one transaction; returns their ids in order."""
        ids: list[int] = []
        with self._atomic():
            for doc in docs:
                ids.append(self.insert(name, doc))
        return ids

    def close(self) -> None:
        with self._lock:
            self.conn.close()
