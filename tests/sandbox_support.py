"""Test-side process-sandbox selection (§16.5).

Production fails CLOSED when bwrap is unusable. Tests whose subject is NOT the
sandbox (run wiring, verification plumbing, H-bench fairness) still have to
execute commands on hosts without usable bubblewrap, so they ask for
`available_sandbox()`: the real BwrapSandbox where it works (CI installs it),
the explicit NoSandbox opt-out elsewhere. Sandbox tests use
`bwrap_or_skip()` instead — they SKIP where bwrap is unusable.
"""

import pytest

from aci.runtime.sandbox import BwrapSandbox, NoSandbox, ProcessSandbox


def available_sandbox() -> ProcessSandbox:
    sandbox = BwrapSandbox()
    return sandbox if sandbox.unavailable_reason() is None else NoSandbox()


def bwrap_or_skip() -> BwrapSandbox:
    sandbox = BwrapSandbox()
    reason = sandbox.unavailable_reason()
    if reason is not None:
        pytest.skip(f"bubblewrap sandbox unusable on this host: {reason}")
    return sandbox
