"""人工 prepare 准入属于插件。只消费 SDK 不可变事实。"""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from wes_plugin_sdk.prepare_policy import (
    PrepareContext,
    PrepareDeviceFact,
    PrepareRuntimeFacts,
    PrepareTaskType,
)

from manual_bin_processing.application.prepare_policy import ManualPickingPreparePolicy

NOW = datetime(2026, 9, 4)
CONTEXT = PrepareContext(True, "MANUAL", "AUTO", "manual_bin_processing", "MANUAL_BIN_PROCESSING")
DEVICE = PrepareDeviceFact("manual.conveyor", "1.0", 5000, "manual.conveyor", "1.0", NOW, "AUTO", "IDLE", None)
FACTS = PrepareRuntimeFacts(True, (DEVICE,), False)


def test_policy_selects_manual_queue_and_accepts_fresh_idle_clear_context():
    policy = ManualPickingPreparePolicy()
    assert policy.select_task_type(CONTEXT) is PrepareTaskType.MANUAL
    assert policy.is_ready(FACTS, now=NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"is_active": False},
        {"line_type": "AUTO"},
        {"run_mode": "MANUAL"},
        {"plugin_key": "other"},
        {"flow_mode": "other"},
    ],
)
def test_policy_rejects_other_context(changes):
    assert ManualPickingPreparePolicy().select_task_type(replace(CONTEXT, **changes)) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"observed_contract_key": None},
        {"observed_contract_key": "other"},
        {"observed_contract_version": "2.0"},
        {"received_at": NOW - timedelta(seconds=6)},
        {"mode": "MANUAL"},
        {"status": "BUSY"},
        {"current_command_code": "CMD-1"},
    ],
)
def test_policy_rejects_missing_or_invalid_device_fact(changes):
    facts = replace(FACTS, devices=(replace(DEVICE, **changes),))
    assert not ManualPickingPreparePolicy().is_ready(facts, now=NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"has_position_bindings": False},
        {"devices": ()},
        {"has_positioned_object": True},
    ],
)
def test_policy_rejects_unsafe_missing_topology_or_positioned_object(changes):
    assert not ManualPickingPreparePolicy().is_ready(replace(FACTS, **changes), now=NOW)
