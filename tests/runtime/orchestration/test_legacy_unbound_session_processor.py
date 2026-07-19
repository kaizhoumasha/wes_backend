"""未绑定迁移 Session 的隔离兼容 processor 回归测试。"""

from types import SimpleNamespace

import pytest

from src.app.runtime.capabilities.material_flow.contracts.rough_sorter import (
    ROUGH_SORTER_CONTRACT_VERSION,
    ROUGH_SORTER_PLUGIN_KEY,
)
from src.app.runtime.capabilities.material_flow.contracts.smt_sorting_inbound import (
    SMT_SORTING_INBOUND_CONTRACT_VERSION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.runtime.orchestration.runtime_intent_effects import _resolve_target_device
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_processor_service import (
    LegacyUnboundSessionProcessor,
)
from src.app.runtime.workline_plugins.legacy_compatibility import is_supported_legacy_unbound_session


@pytest.mark.parametrize(
    ("plugin_key", "contract_version"),
    [
        (ROUGH_SORTER_PLUGIN_KEY, ROUGH_SORTER_CONTRACT_VERSION),
        (SMT_SORTING_INBOUND_PLUGIN_KEY, SMT_SORTING_INBOUND_CONTRACT_VERSION),
    ],
)
def test_only_audited_legacy_identities_are_admitted(plugin_key: str, contract_version: str) -> None:
    session = SimpleNamespace(plugin_key=plugin_key, contract_version=contract_version)
    workline = SimpleNamespace(plugin_key=plugin_key, contract_version=contract_version)

    assert is_supported_legacy_unbound_session(session, workline) is True


@pytest.mark.asyncio
async def test_default_legacy_processor_converts_valid_smt_source_pick_to_command() -> None:
    class Db:
        async def execute(self, *_args: object, **_kwargs: object) -> None:
            return None

    event_payload = {
        "message_type": "INTERNAL_EVENT",
        "event_type": "SORTING_SOURCE_PICK_REQUESTED",
        "data": {
            "handoff_demand_id": 11,
            "handoff_source_item_id": 12,
            "claim_attempt_no": 1,
            "bin_code": "BIN-01",
            "bin_cell_index": 2,
            "material_identity_key": "MAT-01",
            "pkg_code": "PKG-01",
            "reel_thickness": "1.2",
        },
    }
    result = await LegacyUnboundSessionProcessor().process(
        Db(),
        session=SimpleNamespace(
            id=10,
            plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
            contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
        ),
        workline=SimpleNamespace(
            id=20,
            plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
            contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
        ),
        inbox=SimpleNamespace(
            id=92,
            kind="INTERNAL_EVENT",
            event_type="SORTING_SOURCE_PICK_REQUESTED",
            event_id="evt-source-pick-1",
            causation_id="cause-1",
            trace_id="trace-legacy",
            payload_json=event_payload,
        ),
        devices_by_role={},
        services=object(),
        trace_id="trace-legacy",
    )

    assert result.success is True
    assert result.intents is not None and len(result.intents) == 1
    assert result.intents[0].kind.value == "COMMAND"
    assert result.intents[0].device_role == "SORTING_SOURCE_ARM"
    assert result.intents[0].action == "SORTING_SOURCE_PICK"


def test_explicit_role_command_resolves_without_source_device() -> None:
    target = SimpleNamespace(
        id=101,
        device_code="SMT-SOURCE-ARM-01",
        device_role="SORTING_SOURCE_ARM",
        role_index=1,
        sort_order=1,
        upstream_device_id=None,
    )
    intent = RuntimeIntent.command(
        device_role="SORTING_SOURCE_ARM",
        action="SORTING_SOURCE_PICK",
        payload={},
        result_policy="COMMAND_RESULT",
    )

    resolved = _resolve_target_device(
        {
            "source_device": None,
            "devices_by_role": {"SORTING_SOURCE_ARM": [target]},
            "workline": SimpleNamespace(runtime_config_json=None, config=None),
            "plugin_binding": None,
        },
        intent,
    )

    assert resolved is target


def test_explicit_device_command_resolves_without_source_device() -> None:
    target = SimpleNamespace(
        id=101,
        device_code="SMT-SOURCE-ARM-01",
        device_role="SORTING_SOURCE_ARM",
        role_index=1,
        sort_order=1,
        upstream_device_id=None,
    )
    intent = RuntimeIntent.command(
        target_device_id=101,
        action="SORTING_SOURCE_PICK",
        payload={},
        result_policy="COMMAND_RESULT",
    )

    resolved = _resolve_target_device(
        {
            "source_device": None,
            "devices_by_role": {"SORTING_SOURCE_ARM": [target]},
            "workline": SimpleNamespace(runtime_config_json=None, config=None),
            "plugin_binding": None,
        },
        intent,
    )

    assert resolved is target


def test_implicit_current_command_still_requires_source_device() -> None:
    intent = RuntimeIntent.command(
        action="SORTING_SOURCE_PICK",
        payload={},
        result_policy="COMMAND_RESULT",
    )

    with pytest.raises(ValueError, match="without source device"):
        _resolve_target_device(
            {
                "source_device": None,
                "devices_by_role": {},
                "workline": SimpleNamespace(runtime_config_json=None, config=None),
                "plugin_binding": None,
            },
            intent,
        )
