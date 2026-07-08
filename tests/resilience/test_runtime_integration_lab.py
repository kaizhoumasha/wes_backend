"""IntegrationLab fixture runner contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.app.runtime.orchestration.integration_lab import IntegrationLabScenarioRunner

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = Path(__file__).with_name("fixtures") / "runtime_integration_lab_fixture.json"
REQUIRED_CASES = ("duplicate", "happy_path", "network_partition", "out_of_order", "reject", "timeout")


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_runtime_integration_lab_fixture_runs_full_wms_ecs_chain() -> None:
    runner = IntegrationLabScenarioRunner(repo_root=REPO_ROOT)

    result = runner.run(_load_fixture())

    assert result.provider_codes == ("ECS", "WMS")
    assert result.covered_cases == REQUIRED_CASES
    assert result.required_event_kinds_present is True
    assert result.replay_result.outbox_effect_keys == ("device-command:CMD-IL-001", "wms-fulfillment:FUL-IL-001")
    assert result.replay_result.reconciliation_reasons == (
        "late_device_result",
        "duplicate_callback",
        "ecs_timeout",
        "wms_business_reject",
        "network_partition",
    )
    assert tuple(
        (diff.object_key, diff.from_state, diff.to_state) for diff in result.replay_result.projection_diff
    ) == (
        ("workline:WL-IL", None, "ACTIVE"),
        ("session:S-IL", None, "RUNNING"),
        ("pkg:PKG-IL-0001", None, "RECEIVED"),
        ("pkg:PKG-IL-0001", "RECEIVED", "DISPATCHING"),
        ("pkg:PKG-IL-0001", "DISPATCHING", "ACKED"),
        ("fulfillment:FUL-IL-001", None, "SENT"),
        ("pkg:PKG-IL-0001", "ACKED", "VISIBLE"),
        ("pkg:PKG-IL-0001", "VISIBLE", "RECONCILING"),
        ("fulfillment:FUL-IL-001", "SENT", "RECONCILING"),
        ("link:WMS-ECS", None, "RECONCILING"),
    )


def test_runtime_integration_lab_rejects_non_sandbox_provider_profile() -> None:
    fixture = _load_fixture()
    fixture["provider_profiles"][0]["environment"] = "production"

    runner = IntegrationLabScenarioRunner(repo_root=REPO_ROOT)

    with pytest.raises(ValueError, match="sandbox"):
        runner.run(fixture)
