"""The workspace change read model (the shared platform-surface contract,
2026-10-03; xl-harness FINDINGS 1-2): manifest persistence + the diff.

Deterministic — tmp trees only, no HTTP, no DB, no model, no network.
"""

import hashlib
import json
from pathlib import Path

import pytest

from aci.application.workspace_changes import (
    KERNEL_INFRA_FILES,
    MANIFEST_DIRNAME,
    MAX_DIFF_BYTES,
    compute_changes,
    manifest_path,
    read_start_manifest,
    redact,
    snapshot_tree,
    write_start_manifest,
)

SECRET = "sk-abcdefghijklmnopqrst"
PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----\n"


def _tree(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TestManifest:
    def test_round_trip_persists_files_and_source(self, tmp_path: Path) -> None:
        source = _tree(tmp_path / "src", {"a.txt": "a\n", "sub/b.py": "b = 1\n"})
        run_dir = _tree(tmp_path / "runs/run_1", {"a.txt": "a\n", "sub/b.py": "b = 1\n"})
        write_start_manifest(tmp_path / "runs", "run_1", source=source, run_dir=run_dir)
        manifest = read_start_manifest(tmp_path / "runs", "run_1")
        assert manifest is not None
        assert manifest.source == str(source.resolve())
        assert manifest.files == snapshot_tree(run_dir)

    def test_the_manifest_lives_outside_the_working_copy(self, tmp_path: Path) -> None:
        """The model holds write grants over the whole workspace: a manifest
        inside the run dir could be rewritten to hide the run's own changes.
        It must be a sibling, under the runs root, never inside run_dir."""
        source = _tree(tmp_path / "src", {"a.txt": "a\n"})
        run_dir = _tree(tmp_path / "runs/run_1", {"a.txt": "a\n"})
        write_start_manifest(tmp_path / "runs", "run_1", source=source, run_dir=run_dir)
        target = manifest_path(tmp_path / "runs", "run_1")
        assert target.is_file()
        assert target.parent.name == MANIFEST_DIRNAME
        assert not target.is_relative_to(run_dir)
        assert target.parent.is_relative_to(tmp_path / "runs")
        # And nothing manifest-shaped leaked into the working copy.
        assert list(run_dir.rglob("*.json")) == []

    def test_missing_and_corrupt_manifests_read_as_none(self, tmp_path: Path) -> None:
        assert read_start_manifest(tmp_path / "runs", "run_ghost") is None
        target = manifest_path(tmp_path / "runs", "run_bad")
        target.parent.mkdir(parents=True)
        target.write_text("{not json", encoding="utf-8")
        assert read_start_manifest(tmp_path / "runs", "run_bad") is None
        other = manifest_path(tmp_path / "runs", "run_ver")
        other.write_text(json.dumps({"version": 99, "files": {}}), encoding="utf-8")
        assert read_start_manifest(tmp_path / "runs", "run_ver") is None

    def test_an_empty_workspace_is_a_valid_manifest(self, tmp_path: Path) -> None:
        source = _tree(tmp_path / "src", {})
        run_dir = _tree(tmp_path / "runs/run_1", {})
        write_start_manifest(tmp_path / "runs", "run_1", source=source, run_dir=run_dir)
        manifest = read_start_manifest(tmp_path / "runs", "run_1")
        assert manifest is not None
        assert manifest.files == {}  # empty, not None: an empty start state


class TestSnapshotTree:
    def test_noise_and_kernel_infrastructure_are_excluded(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path / "w",
            {
                "app.py": "x = 1\n",
                "src/mod.py": "y = 2\n",
                "__pycache__/mod.cpython-313.pyc": "junk",
                ".pytest_cache/v/cache/lastfailed": "[]",
                ".venv/bin/python": "binary-ish",
                "node_modules/pkg/index.js": "z = 3\n",
                ".git/config": "[core]",
                ".aci-sandbox-profile.sb": "(version 1)",
                "nested/__pycache__/deep.pyc": "junk",
                "nested/.aci-sandbox-profile.sb": "(version 1)",
            },
        )
        assert snapshot_tree(root) == {
            "app.py": _sha("x = 1\n"),
            "src/mod.py": _sha("y = 2\n"),
        }

    def test_symlinks_are_not_content(self, tmp_path: Path) -> None:
        root = _tree(tmp_path / "w", {"a.txt": "a\n"})
        (root / "link.txt").symlink_to(root / "a.txt")
        assert snapshot_tree(root) == {"a.txt": _sha("a\n")}


class TestComputeChanges:
    @staticmethod
    def _started(tmp_path: Path, files: dict[str, str]) -> tuple[Path, Path, dict[str, str]]:
        """A source + a provisioned run dir + the persisted manifest."""
        source = _tree(tmp_path / "src", files)
        run_dir = _tree(tmp_path / "runs/run_1", files)
        write_start_manifest(tmp_path / "runs", "run_1", source=source, run_dir=run_dir)
        manifest = read_start_manifest(tmp_path / "runs", "run_1")
        assert manifest is not None
        return source, run_dir, manifest.files

    def test_added_modified_deleted_with_hashes_and_sizes(self, tmp_path: Path) -> None:
        source, run_dir, manifest = self._started(
            tmp_path, {"keep.txt": "keep\n", "edit.txt": "old\n", "gone.txt": "gone\n"}
        )
        (run_dir / "edit.txt").write_text("new content\n", encoding="utf-8")
        (run_dir / "gone.txt").unlink()
        (run_dir / "new.txt").write_text("added\n", encoding="utf-8")

        changes = compute_changes(manifest, run_dir, source=source)
        by_path = {f.path: f for f in changes.files}
        assert set(by_path) == {"edit.txt", "gone.txt", "new.txt"}  # keep.txt unchanged
        assert by_path["new.txt"].status == "added"
        assert by_path["new.txt"].sha256_before is None
        assert by_path["new.txt"].sha256_after == _sha("added\n")
        assert by_path["new.txt"].size_after == len("added\n")
        assert by_path["edit.txt"].status == "modified"
        assert by_path["edit.txt"].sha256_before == _sha("old\n")
        assert by_path["edit.txt"].sha256_after == _sha("new content\n")
        assert by_path["gone.txt"].status == "deleted"
        assert by_path["gone.txt"].sha256_before == _sha("gone\n")
        assert by_path["gone.txt"].sha256_after is None
        assert by_path["gone.txt"].size_after == 0
        assert not changes.truncated

    def test_diff_is_unified_and_workspace_relative(self, tmp_path: Path) -> None:
        source, run_dir, manifest = self._started(tmp_path, {"sub/edit.py": "old = 1\n"})
        (run_dir / "sub" / "edit.py").write_text("new = 2\n", encoding="utf-8")
        changes = compute_changes(manifest, run_dir, source=source)
        assert "--- a/sub/edit.py" in changes.diff
        assert "+++ b/sub/edit.py" in changes.diff
        assert "-old = 1" in changes.diff
        assert "+new = 2" in changes.diff
        assert str(run_dir) not in changes.diff  # never the server path

    def test_deleted_file_diffs_from_the_unchanged_source(self, tmp_path: Path) -> None:
        source, run_dir, manifest = self._started(tmp_path, {"gone.txt": "gone line\n"})
        (run_dir / "gone.txt").unlink()
        changes = compute_changes(manifest, run_dir, source=source)
        assert "--- a/gone.txt" in changes.diff
        assert "-gone line" in changes.diff

    def test_source_changed_since_start_degrades_the_hunk_not_the_list(
        self, tmp_path: Path
    ) -> None:
        """The whole point of the manifest: the client may change the source
        workspace after delegation — the FILE LIST must stay stable (it never
        reads the source), only the hunk degrades honestly."""
        source, run_dir, manifest = self._started(tmp_path, {"edit.txt": "old\n"})
        (run_dir / "edit.txt").write_text("run wrote this\n", encoding="utf-8")
        (source / "edit.txt").write_text("the client rewrote the source\n", encoding="utf-8")
        changes = compute_changes(manifest, run_dir, source=source)
        by_path = {f.path: f for f in changes.files}
        assert by_path["edit.txt"].status == "modified"
        # The list is manifest-stable: the START hash, not the new source's.
        assert by_path["edit.txt"].sha256_before == _sha("old\n")
        assert by_path["edit.txt"].sha256_after == _sha("run wrote this\n")
        # The hunk is withheld — the new source content never leaks in.
        assert "[diff unavailable: the source workspace changed since run start]" in (changes.diff)
        assert "the client rewrote the source" not in changes.diff

    def test_no_source_at_all_still_lists_every_file(self, tmp_path: Path) -> None:
        source, run_dir, manifest = self._started(tmp_path, {"edit.txt": "old\n"})
        (run_dir / "edit.txt").write_text("new\n", encoding="utf-8")
        (run_dir / "added.txt").write_text("added\n", encoding="utf-8")
        changes = compute_changes(manifest, run_dir, source=None)
        by_path = {f.path: f for f in changes.files}
        assert by_path["edit.txt"].status == "modified"
        assert by_path["added.txt"].status == "added"
        assert "+added" in changes.diff  # added files always diff fully
        assert "[diff unavailable" in changes.diff  # modified: no start bytes

    def test_binary_files_are_listed_without_a_diff(self, tmp_path: Path) -> None:
        source = _tree(tmp_path / "src", {"img.bin": "old-bytes"})
        run_dir = _tree(tmp_path / "runs/run_1", {"img.bin": "old-bytes"})
        write_start_manifest(tmp_path / "runs", "run_1", source=source, run_dir=run_dir)
        manifest = read_start_manifest(tmp_path / "runs", "run_1")
        assert manifest is not None
        (run_dir / "img.bin").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
        changes = compute_changes(manifest.files, run_dir, source=source)
        by_path = {f.path: f for f in changes.files}
        assert by_path["img.bin"].status == "modified"
        assert by_path["img.bin"].sha256_after is not None
        assert "[binary file changed]" in changes.diff

    def test_kernel_infrastructure_never_appears_as_a_change(self, tmp_path: Path) -> None:
        """The kernel writes its Seatbelt profile into the run workspace —
        infrastructure, never task output (xl-harness FINDING 5)."""
        source, run_dir, manifest = self._started(tmp_path, {"app.py": "x = 1\n"})
        (run_dir / "app.py").write_text("x = 2\n", encoding="utf-8")
        for name in KERNEL_INFRA_FILES:
            (run_dir / name).write_text("(version 1)", encoding="utf-8")
        changes = compute_changes(manifest, run_dir, source=source)
        assert [f.path for f in changes.files] == ["app.py"]

    def test_diff_cap_truncates(self, tmp_path: Path) -> None:
        source, run_dir, manifest = self._started(tmp_path, {})
        for i in range(20):
            (run_dir / f"f{i:02}.txt").write_text("x" * 30_000 + "\n", encoding="utf-8")
        changes = compute_changes(manifest, run_dir, source=source, max_diff_bytes=50_000)
        assert changes.truncated is True
        assert len(changes.diff.encode("utf-8")) <= 50_000 + 200
        assert "[diff truncated at 50000 bytes]" in changes.diff
        # The FILE LIST is complete regardless of the diff cap.
        assert len(changes.files) == 20

    def test_unsafe_manifest_keys_never_read_outside(self, tmp_path: Path) -> None:
        """A manifest is server-written, but a key is never trusted to name a
        path: `..` / absolute entries degrade to no-hunk, never a read."""
        run_dir = _tree(tmp_path / "runs/run_1", {"app.py": "x = 1\n"})
        outside = _tree(tmp_path / "elsewhere", {"secret.txt": "top secret\n"})
        evil = {
            "../../elsewhere/secret.txt": _sha("top secret\n"),
            "/etc/passwd": _sha("whatever"),
            "app.py": _sha("old\n"),
        }
        changes = compute_changes(evil, run_dir, source=outside)
        assert "top secret" not in changes.diff
        by_path = {f.path: f for f in changes.files}
        assert by_path["app.py"].status == "modified"
        assert "[diff unavailable" in changes.diff


class TestRedaction:
    def test_redactable_patterns_get_the_guards_replacement(self) -> None:
        assert redact(f"token = {SECRET}\n") == "token = [REDACTED:api token (sk-)]\n"

    def test_a_private_key_block_is_never_redactable(self) -> None:
        assert redact(PRIVATE_KEY) == ""

    def test_clean_text_passes_through(self) -> None:
        assert redact("just code\n") == "just code\n"

    def test_a_changed_file_with_a_secret_is_redacted_in_the_diff(self, tmp_path: Path) -> None:
        source = _tree(tmp_path / "src", {"cfg.txt": "plain\n"})
        run_dir = _tree(tmp_path / "runs/run_1", {"cfg.txt": "plain\n"})
        write_start_manifest(tmp_path / "runs", "run_1", source=source, run_dir=run_dir)
        manifest = read_start_manifest(tmp_path / "runs", "run_1")
        assert manifest is not None
        (run_dir / "cfg.txt").write_text(f"api_key = {SECRET}\n", encoding="utf-8")
        changes = compute_changes(manifest.files, run_dir, source=source)
        assert SECRET not in changes.diff
        assert "[REDACTED:api token (sk-)]" in changes.diff
        # The file itself is still listed — only the text is scrubbed.
        assert [f.path for f in changes.files] == ["cfg.txt"]

    def test_a_private_key_file_is_withheld_entirely(self, tmp_path: Path) -> None:
        source = _tree(tmp_path / "src", {"key.pem": "header\n"})
        run_dir = _tree(tmp_path / "runs/run_1", {"key.pem": "header\n"})
        write_start_manifest(tmp_path / "runs", "run_1", source=source, run_dir=run_dir)
        manifest = read_start_manifest(tmp_path / "runs", "run_1")
        assert manifest is not None
        (run_dir / "key.pem").write_text(PRIVATE_KEY, encoding="utf-8")
        changes = compute_changes(manifest.files, run_dir, source=source)
        assert PRIVATE_KEY not in changes.diff
        assert "MIIB" not in changes.diff
        assert "[diff withheld: secret pattern (private key block)]" in changes.diff
        assert [f.path for f in changes.files] == ["key.pem"]


@pytest.mark.parametrize("name", sorted(KERNEL_INFRA_FILES))
def test_kernel_infra_names_are_the_sandbox_profile(name: str) -> None:
    """Pinned to the Seatbelt profile name (src/aci/runtime/sandbox.py):
    if the kernel ever writes another infra file, extend the exclusion."""
    assert name == ".aci-sandbox-profile.sb"


def test_the_diff_cap_is_the_shared_contracts_200kb() -> None:
    assert MAX_DIFF_BYTES == 200 * 1024
