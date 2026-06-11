"""SMT usage policy 共享口径测试。"""

from __future__ import annotations

import importlib
from typing import Any

import pytest


def _usage_module() -> Any:
    try:
        return importlib.import_module("src.app.workline.domain.services.smt_usage_policy")
    except ModuleNotFoundError as exc:
        pytest.fail(f"缺少 SMT usage policy 模块: {exc}")


@pytest.mark.parametrize(
    ("raw_usage", "expected_usage", "expected_band"),
    [
        (0, 0.0, "DIRECT_SORTING"),
        (0.5, 0.5, "PREFERRED_FULL_BOX_EXCHANGE"),
        ("0.8", 0.8, "REQUIRE_FULL_BOX_EXCHANGE"),
        (1, 1.0, "REQUIRE_FULL_BOX_EXCHANGE"),
    ],
)
def test_smt_usage_policy_normalizes_threshold_values(
    raw_usage: Any, expected_usage: float, expected_band: str
) -> None:
    module = _usage_module()
    policy = module.SmtUsagePolicy()

    result = policy.resolve_release_bin_usage({"usage": raw_usage})

    assert result.valid is True
    assert result.usage == expected_usage
    assert result.failure_code is None
    assert policy.usage_band(result.usage) == expected_band


@pytest.mark.parametrize(
    "snapshot",
    [
        {"usage_snapshot": "0.75"},
        {"bin_usage": "0.75"},
    ],
)
def test_smt_usage_policy_accepts_legacy_release_usage_fields(snapshot: dict[str, Any]) -> None:
    module = _usage_module()

    result = module.SmtUsagePolicy().resolve_release_bin_usage(snapshot)

    assert result.valid is True
    assert result.usage == 0.75


@pytest.mark.parametrize(
    "snapshot",
    [
        {},
        {"usage": None},
        {"usage": ""},
        {"usage": "80"},
        {"usage": -0.01},
        {"usage": True},
        {"usage": "not-a-number"},
    ],
)
def test_smt_usage_policy_reports_invalid_usage_without_silent_zero(snapshot: dict[str, Any]) -> None:
    module = _usage_module()

    result = module.SmtUsagePolicy().resolve_release_bin_usage(snapshot)

    assert result.valid is False
    assert result.usage is None
    assert result.failure_code == "USAGE_INVALID"
    assert result.message


def test_handoff_and_rack_bin_scheduling_share_release_usage_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _usage_module()
    resource_module = importlib.import_module("src.app.resource.services.smt_rack_bin_scheduling_service")

    class RecordingUsagePolicy(module.SmtUsagePolicy):
        def __init__(self) -> None:
            super().__init__()
            self.snapshots: list[dict[str, Any]] = []

        def resolve_release_bin_usage(self, snapshot: Any) -> Any:
            self.snapshots.append(dict(snapshot))
            return super().resolve_release_bin_usage(snapshot)

    policy = RecordingUsagePolicy()
    monkeypatch.setattr(resource_module, "SMT_USAGE_POLICY", policy)

    usage = resource_module.SmtRackBinSchedulingService()._release_bin_usage({"usage_snapshot": "0.25"})

    assert usage == 0.25
    assert policy.snapshots == [{"usage_snapshot": "0.25"}]


def test_usage_policy_is_exported_from_domain_services_package() -> None:
    module = _usage_module()
    package = importlib.import_module("src.app.workline.domain.services")

    assert package.SmtUsagePolicy is module.SmtUsagePolicy
    assert package.SmtUsageResult is module.SmtUsageResult
    assert package.SMT_USAGE_POLICY is module.SMT_USAGE_POLICY
