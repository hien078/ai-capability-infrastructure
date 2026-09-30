"""Cooperative cancellation (harness.md §6.3: LoopPolicy.cancellation="cooperative").

Runs are cancelled by flag, not by thread murder: components call
``raise_if_cancelled()`` at safe points, which raises ``RunCancelled`` and
lets the kernel record a CANCELLED stop instead of a crash.
"""

import threading


class RunCancelled(Exception):
    """Raised inside a run whose CancelToken was cancelled."""

    def __init__(self, run_id: str, message: str = "") -> None:
        super().__init__(message or f"run {run_id} cancelled")
        self.run_id = run_id


class CancelToken:
    """Thread-safe cooperative cancellation flag for one run."""

    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self._event = threading.Event()

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise RunCancelled(self._run_id)

    def __get_pydantic_core_schema__(self, handler: object) -> object:
        """Allow CancelToken as a pydantic field type (is-instance schema)."""
        from pydantic_core import core_schema

        return core_schema.is_instance_schema(cls=CancelToken)
