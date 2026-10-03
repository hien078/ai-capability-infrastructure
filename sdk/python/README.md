# aci-client

Thin typed Python SDK for the ACI REST API (plan §31, ADR-007 — SDKs wrap
REST, they never sit beside the protocols as peers). No `aci` imports: the
package works against a remote server.

```bash
pip install .          # from this directory
```

Runtime dependencies: `httpx` (HTTP) + `pydantic` (typed wire models) — both
already pinned in the repo's `requirements-lock.txt`.

## Use

```python
from aci_client import ACIClient, OutcomeVerdict, TaskContext

client = ACIClient("http://localhost:8000", token="...")  # token optional

# Route a task through the server's §14 pipeline -> pinned bundle.
routed = client.route(
    "fix the flaky login test",
    context=TaskContext(language="python", phase="debugging"),
    max_items=3,
)

# Fetch each selected skill: SKILL.md text + files, sha256-verified against
# the immutable artifact manifest before anything is returned.
for item in routed.bundle.items:
    skill = client.get_skill(item.capability_id, version=item.version)
    print(skill.skill_md)

# Discovery-only search (production-active metadata).
hits = client.search("debugging", limit=10)

# Close the §33 feedback loop with multi-source evidence.
client.report_outcome(
    routed.route_run_id,
    routed.bundle.bundle_id,
    [OutcomeVerdict(source="test_harness", status="success", confidence="high")],
    tests_after={"passed": 12, "failed": 0},
)

# The HarnessKernel surface (ADR-014): synchronous run + read model.
run = client.run_agent("add a retry to the uploader", workspace="demo")
run = client.get_run(run.run_id)  # .usage / .changes may be None on older servers
client.cancel_run(run.run_id)
```

`AsyncACIClient` exposes the same surface (`async def` + `await`, `aclose()`).

## Errors

Every server error maps to `ACIError` carrying the stable §45 code
(`err.code`, `err.status_code`); request-validation failures are
`ACIValidationError` (422), transport failures are `ACIConnectionError`,
and a skill blob that fails its sha256 check raises `SkillIntegrityError`
before any content is returned (§39 supply chain).

Retry policy (§31.1): connection failures on idempotent GETs are retried
(`3` attempts); POSTs are **never** auto-retried — `route` persists
telemetry, outcomes append, agent runs execute real work.

## Auth

One bearer token for `/v1/*` + the catalog; `agent_runs_token=` (defaults
to `token`) for `/v1/agent-runs` — a deployment may gate the two surfaces
with different tokens (`ACI_API_TOKEN` vs `ACI_AGENT_RUNS_TOKEN`).

## Example

`examples/preload_agent.py` — the "router for weak clients" pattern: route
the task, PRELOAD the selected skills into the system prompt, call any
OpenAI-compatible endpoint. `--dry-run` prints the assembled prompt
without a model call.

## Tests

From the repo root the SDK tests are wired into the repo's pytest
(`testpaths` includes `sdk/python/tests`); standalone: `cd sdk/python &&
pytest`. Unit tests use `httpx.MockTransport` (no network, no DB); the
contract test (`test_contract_integration.py`) needs the `aci` package +
a local Postgres and is marked `integration` (skips otherwise).
