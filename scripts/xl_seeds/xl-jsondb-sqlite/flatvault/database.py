"""A database: a directory of tables.

``db.table(name)`` creates lazily; ``drop_table``/``drop_all`` remove;
``save`` flushes every registered table; ``close`` saves and forgets.
"""

from __future__ import annotations

from pathlib import Path

from flatvault.errors import TableError
from flatvault.tables import Table


class Database:
    """The table registry over one directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._tables: dict[str, Table] = {}

    def table(self, name: str) -> Table:
        """Get or create one table (file ``<root>/<name>.json``)."""
        if name not in self._tables:
            self._tables[name] = Table(name, self.root / f"{name}.json")
        return self._tables[name]

    def table_names(self) -> list[str]:
        """Registered table names, sorted."""
        return sorted(self._tables)

    def drop_table(self, name: str) -> None:
        """Remove one table and its file."""
        if name not in self._tables:
            raise TableError(f"unknown table {name!r}")
        table = self._tables.pop(name)
        table.path.unlink(missing_ok=True)

    def drop_all(self) -> None:
        """Remove every registered table."""
        for name in list(self._tables):
            self.drop_table(name)

    def save(self) -> None:
        """Flush every registered table."""
        for table in self._tables.values():
            table.save()

    def close(self) -> None:
        """Save and forget every table."""
        self.save()
        self._tables.clear()
