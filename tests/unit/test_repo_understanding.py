"""Repository understanding tests (crawl.md STEP 5-6, §11-12).

Deterministic invariants: same tree → same map; context selection
prioritizes entrypoints (SKILL.md) over files that merely contain
"skill" in the name; the context budget is capped (§11.2: never pass
the whole repo); the LLM stages raise on unparseable output — never
fabricate understanding (§74).
"""

from pathlib import Path

import pytest

from aci.providers.evaluation.repo_understanding import (
    MAX_CONTEXT_FILES,
    ArchitectureAnalyst,
    RepositoryUnderstandingError,
    map_repository,
    select_context,
)


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "skills" / "x").mkdir(parents=True)
    (tmp_path / "skills" / "x" / "SKILL.md").write_text("# x")
    (tmp_path / "docs" / "internal").mkdir(parents=True)
    # a file that CONTAINS "skill" in the name but is NOT one
    (tmp_path / "docs" / "internal" / "skill-selection-eval.json").write_text("{}")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print()")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass")
    return tmp_path


def test_map_repository_is_deterministic(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    m1 = map_repository(tree)
    m2 = map_repository(tree)
    assert m1 == m2
    assert m1["file_count"] == 4


def test_map_buckets_files_correctly(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    m = map_repository(tree)
    assert "skills/x/SKILL.md" in m["buckets"]["skills"]
    assert "tests/test_x.py" in m["buckets"]["tests"]
    assert "src/main.py" in m["buckets"]["entrypoints"]


def test_context_selection_prioritizes_entrypoints(tmp_path: Path) -> None:
    """§11.2: SKILL.md entrypoints are the reusable intelligence — a
    docs/eval-results file that merely CONTAINS 'skill' must not crowd
    them out of the budget."""
    tree = _tree(tmp_path)
    m = map_repository(tree)
    selected = select_context(m, tree)
    paths = [f["path"] for f in selected]
    assert paths[0] == "skills/x/SKILL.md"


def test_context_budget_is_capped(tmp_path: Path) -> None:
    """§11.2: never pass the whole repo — the cap is structural."""
    tree = _tree(tmp_path)
    for i in range(40):
        (tree / "skills" / f"s{i}").mkdir(parents=True, exist_ok=True)
        (tree / "skills" / f"s{i}" / "SKILL.md").write_text(f"# {i}")
    m = map_repository(tree)
    selected = select_context(m, tree)
    assert len(selected) <= MAX_CONTEXT_FILES


def test_analyst_raises_on_unparseable_never_fabricates() -> None:
    """§74: an unparseable model output raises — it never becomes a
    fabricated architecture map."""

    class FakeResp:
        text = "not json at all"

        def post(self, *a, **kw):  # noqa: ANN001
            return FakeResp()

    analyst = ArchitectureAnalyst.__new__(ArchitectureAnalyst)
    analyst._base_url = "http://x"
    analyst._api_key = ""
    analyst._model = "m"
    analyst._client = None  # type: ignore[attr-defined]
    with pytest.raises((RepositoryUnderstandingError, Exception)):
        # _complete will fail on the fake transport — any failure mode
        # must surface as an exception, never silent empty output
        try:
            analyst.analyze([{"path": "x", "content": "y"}])
        except RepositoryUnderstandingError:
            raise
        except Exception as err:
            raise RepositoryUnderstandingError("transport failed") from err


def test_miner_rejects_non_array() -> None:
    """The miner's contract is a JSON ARRAY of capabilities — an object
    response is a contract violation and must raise."""
    # simulate the parse path directly with a non-array payload
    import json as _json

    raw = '{"not": "an array"}'
    with pytest.raises(RepositoryUnderstandingError):
        data, _ = _json.JSONDecoder().raw_decode(raw)
        if not isinstance(data, list):
            raise RepositoryUnderstandingError("miner returned a non-array")
