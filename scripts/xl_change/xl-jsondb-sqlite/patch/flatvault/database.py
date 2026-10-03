"""A database: a directory of tables over one sqlite file.

Same public API as the JSON engine. ``transaction()`` is new: a context
manager, atomic across tables, rollback on any exception. Legacy
``<name>.json`` files in the directory are migrated on first open and
left in place as the backup.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from flatvault.engine import Engine
from flatvault.errors import TableError
from flatvault.tables import Table


class Database:
    """The table registry over one sqlite database."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._engine = Engine(self.root)
        self._txn_depth = 0

    def table(self, name: str) -> Table:
        """Get or create one table."""
        self._engine._ensure_table(name)  # noqa: SLF001 - facade owns the engine
        return Table(name, self.root / f"{name}.json", self._engine)

    def table_names(self) -> list[str]:
        """Table names, sorted."""
        return self._engine.table_names()

    def drop_table(self, name: str) -> None:
        """Remove one table."""
        if not self._engine._table_exists(name):  # noqa: SLF001
            raise TableError(f"unknown table {name!r}")
        self._engine.drop(name)

    def drop_all(self) -> None:
        """Remove every table."""
        for name in self.table_names():
            self.drop_table(name)

    def save(self) -> None:
        """Kept for API compat — every write commits immediately."""

    def close(self) -> None:
        """Close the engine."""
        self._engine.close()

    @contextmanager
    def transaction(self) -> Iterator[Database]:
        """Atomic across tables: commit on success, rollback on any exception.

        Nested transactions are savepoints: an inner rollback undoes
        only the inner scope, an inner commit is still undone by an outer
        rollback.
        """
        self._txn_depth += 1
        if self._txn_depth == 1:
            self._engine.begin()
            savepoint = None
        else:
            savepoint = f"sp_{self._txn_depth}"
            self._engine.savepoint(savepoint)
        try:
            yield self
        except BaseException:
            if savepoint is None:
                self._engine.rollback()
            else:
                self._engine.rollback_to(savepoint)
                self._engine.release(savepoint)
            raise
        else:
            if savepoint is None:
                self._engine.commit()
            else:
                self._engine.release(savepoint)
        finally:
            self._txn_depth -= 1
