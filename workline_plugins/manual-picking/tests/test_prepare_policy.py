from dataclasses import replace

import pytest
from manual_picking.plugin import build_prepare_policy
from wes_plugin_sdk.prepare_policy import (
    PrepareContext,
    PrepareTaskType,
)

CONTEXT = PrepareContext(True, "MANUAL", "AUTO", "manual-picking", None)


def test_plugin_prepare_policy_selects_manual_task_for_active_workline() -> None:
    policy = build_prepare_policy()

    assert policy.select_task_type(CONTEXT) is PrepareTaskType.MANUAL


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
