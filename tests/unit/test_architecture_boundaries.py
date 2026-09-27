"""Khóa biên kiến trúc ADR-001..004: abstraction, registry, planes, adapter boundary."""

import pathlib
from typing import get_args

from pydantic import BaseModel

from aci.domain.capability import models

SRC = pathlib.Path(__file__).parents[2] / "src"
DOMAIN = SRC / "aci" / "domain"


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def test_capability_spec_is_discriminated_union_of_six_kinds() -> None:
    kinds: set[str] = set()
    for arg in get_args(models.CapabilitySpec):
        for member in get_args(arg):
            if isinstance(member, type) and issubclass(member, BaseModel):
                kind_field = member.model_fields.get("kind")
                if kind_field is not None and kind_field.annotation is not None:
                    kinds.update(get_args(kind_field.annotation))
    assert kinds == {"skill", "resource", "tool", "workflow", "service", "agent"}
    disc = models.CapabilityVersion.model_fields["spec"].discriminator
    assert disc == "kind"


def test_no_generic_execute_verb_in_domain() -> None:
    for f in DOMAIN.rglob("*.py"):
        assert "execute_capability" not in _read(f).lower(), f


def test_registry_separation_version_release_binding() -> None:
    assert models.CapabilityVersion.model_config.get("frozen") is True
    assert models.CapabilityBundle.model_config.get("frozen") is True
    release_fields = set(models.CapabilityRelease.model_fields)
    version_fields = set(models.CapabilityVersion.model_fields)
    assert {"channel", "status"} <= release_fields
    assert "channel" not in version_fields and "status" not in version_fields
    assert "binding_type" in set(models.CapabilityBinding.model_fields)
    assert "content_digest" not in release_fields


def test_plane_separation_no_control_import_in_route_contracts() -> None:
    text = _read(models.__file__ and DOMAIN / "capability" / "models.py")
    for banned in ("promotion", "benchmark", "quarantine"):
        assert banned not in text.lower()


def _py_files(root: pathlib.Path) -> list[pathlib.Path]:
    return [f for f in root.rglob("*.py") if "__pycache__" not in f.parts]


def test_adapter_boundary_domain_never_imports_adapters() -> None:
    for f in _py_files(DOMAIN):
        for line in _read(f).splitlines():
            code = line.split("#", 1)[0].strip()
            if code.startswith(("import ", "from ")):
                assert "adapters" not in code, f
