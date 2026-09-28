"""Skill package licensing provider (V2 governance, plan §55)."""

from aci.providers.licensing.detector import (
    LicenseDetection,
    detect_license,
    spdx_permissions,
)

__all__ = ["LicenseDetection", "detect_license", "spdx_permissions"]
