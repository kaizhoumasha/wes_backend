from dataclasses import replace
from datetime import UTC, datetime

import pytest
from manual_picking.plugin import build_prepare_policy
from wes_plugin_sdk.prepare_policy import (
    PrepareContext,
    PrepareRuntimeFacts,
    PrepareTaskType,
)

NOW = datetime(2026, 9, 12, tzinfo=UTC)
CONTEXT = PrepareContext(True, "MANUAL", "AUTO", "manual-picking", None)
READY_FACTS = PrepareRuntimeFacts(
    device_roles=("SCAN1", "SCAN2", "SCAN3", "SCAN4"),
    position_roles=("FIVE_RACK", "RETURN_RACK", "TRANSFER_RACK", "INLET", "OUTLET"),
)


def test_plugin_prepare_policy_selects_manual_task_for_ready_workline() -> None:
    policy = build_prepare_policy()

    assert policy.select_task_type(CONTEXT) is PrepareTaskType.MANUAL
    assert policy.is_ready(READY_FACTS, now=NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"is_active": False},
        {"line_type": "AUTO"},
        {"run_mode": "MANUAL"},
        {"plugin_key": "other"},
        {"flow_mode": "LEGACY_FLOW"},
    ],
)
def test_plugin_prepare_policy_rejects_other_workline_context(changes: dict[str, object]) -> None:
    policy = build_prepare_policy()

    assert policy.select_task_type(replace(CONTEXT, **changes)) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"device_roles": ("SCAN1", "SCAN2", "SCAN3")},
        {"device_roles": ("SCAN1", "SCAN2", "SCAN3", "SCAN3")},
        {"position_roles": ("FIVE_RACK", "RETURN_RACK", "TRANSFER_RACK", "INLET")},
        {"position_roles": ("FIVE_RACK", "RETURN_RACK", "TRANSFER_RACK", "INLET", "INLET")},
    ],
)
def test_plugin_prepare_policy_rejects_incomplete_static_bindings(changes: dict[str, object]) -> None:
    policy = build_prepare_policy()

    assert not policy.is_ready(replace(READY_FACTS, **changes), now=NOW)
