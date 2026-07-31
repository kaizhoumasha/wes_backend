"""T8 新增模块的精确 branch coverage target manifest。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

T8_COVERAGE_TARGETS = (
    "src.app.contracts.external_contract_profile",
    "src.app.contracts.external_contract_profile_catalog",
    "src.app.wms_integration.provider_profile",
    "src.app.wms_integration.provider_manifest",
    "src.app.runtime.system_capabilities.wms.conformance_manifest",
    "src.app.runtime.system_capabilities.wms.provider_conformance",
    "src.app.runtime.system_capabilities.wms.conformance_matrix",
    "src.app.workline.models.migration_inventory",
    "tests.support.wms_conformance_runner",
    "scripts.run_wms_conformance",
)


def validate_t8_coverage_targets() -> None:
    """目标必须可解析，且不得用 omit/pragma 绕开分支覆盖。"""

    for module_name in T8_COVERAGE_TARGETS:
        spec = importlib.util.find_spec(module_name)
        if spec is None or spec.origin is None:
            raise ValueError(f"T8 coverage target cannot be resolved: {module_name}")
        source = Path(spec.origin).read_text(encoding="utf-8").lower()
        if "pragma: no cover" in source or "coverage: ignore" in source or "omit =" in source:
            raise ValueError(f"T8 coverage target contains a coverage escape hatch: {module_name}")


__all__ = ["T8_COVERAGE_TARGETS", "validate_t8_coverage_targets"]
