"""Phase5 business cleanup: WorkLine domain mirrors are no longer runtime business contracts."""

from __future__ import annotations

import importlib

import pytest

TARGET_PHASE4_CONTRACT_MODULES = (
    "src.app.runtime.capabilities.phase4.contracts.ng_reason",
    "src.app.runtime.capabilities.phase4.contracts.material_identity",
    "src.app.runtime.capabilities.phase4.contracts.six_in_one",
    "src.app.runtime.capabilities.phase4.contracts.rough_sorter",
    "src.app.runtime.capabilities.phase4.contracts.sorting_inbound_context",
)
LEGACY_BUSINESS_CONTRACT_MODULES = (
    "src.app.workline.domain.ng_reason",
    "src.app.workline.domain.material_identity",
    "src.app.workline.domain.contexts.rough_sorter",
    "src.app.workline.domain.contexts.smt_sorting_inbound",
    "src.app.workline.domain.contracts.rough_sorter",
    "src.app.workline.domain.contracts.six_in_one",
    "src.app.workline.domain.contracts.smt_sorting_inbound",
    "src.app.workline.domain.services.smt_inbound_handoff_reason",
    "src.app.workline.domain.services.smt_inbound_handoff_route_service",
    "src.app.workline.domain.services.smt_usage_policy",
)


@pytest.mark.parametrize("module_name", TARGET_PHASE4_CONTRACT_MODULES)
def test_phase4_business_contract_modules_are_importable(module_name: str) -> None:
    assert importlib.import_module(module_name).__name__ == module_name


@pytest.mark.parametrize("module_name", LEGACY_BUSINESS_CONTRACT_MODULES)
def test_legacy_workline_business_contract_modules_are_not_importable(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_workline_config_contract_package_keeps_device_error_code_only() -> None:
    from src.app.workline.domain import contracts

    assert hasattr(contracts, "DeviceErrorCode")
    assert not hasattr(contracts, "SixInOne")
    assert getattr(contracts, "__all__", []) == ["DeviceErrorCode"]
