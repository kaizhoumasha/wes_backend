"""共享 Plugin conformance 平台自身的失败合同。"""

from __future__ import annotations

from dataclasses import fields, replace
from types import SimpleNamespace

import pytest

from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    _system_capability_intents,
)
from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, PluginAttemptContext
from src.app.runtime.workline_plugins.contracts import PluginDecision
from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION
from src.app.runtime.workline_plugins.rough_sorter.state import RoughSorterState
from tests.workline_plugins.conformance import PluginConformanceFixture, assert_system_capability_effect_contract


def test_conformance_fixture_cannot_replace_production_effect_adapter() -> None:
    fixture_fields = {field.name for field in fields(PluginConformanceFixture)}

    assert "effect_converter" not in fixture_fields
    assert "effect_context" in fixture_fields


def _context() -> PluginAttemptContext:
    return PluginAttemptContext(
        attempt_id="conformance-contract",
        inbox_id=1,
        session_id=1,
        workline_id=1,
        event_type="SCAN_COMPLETED",
        payload={},
        plugin_state={},
        snapshot=AttemptSnapshot(
            processor_token="conformance-lease",
            session_version=1,
            plugin_state_version=0,
            binding_id=1,
            binding_version=1,
        ),
        runtime=SimpleNamespace(),
    )


def _typed_intent(capability_key: str, contract_version: str) -> RuntimeIntent:
    return RuntimeIntent.system_capability(
        capability_key=capability_key,
        contract_version=contract_version,
        operation_key="conformance:platform:1",
        payload={"fixture": True},
        precondition={"expected": "fixture-v1"},
        fact_version="fixture-v1",
        timeout_seconds=30,
        creator_authority="WORKLINE_PLUGIN",
        authorization_policy="PLUGIN_DECLARED_CAPABILITY",
        binding_snapshot={"binding_id": 1, "binding_version": 1},
        provider_snapshot={"provider_code": "RUNTIME", "profile": "runtime"},
    )


def _convert(intent: RuntimeIntent) -> tuple[RuntimeIntent, ...]:
    decision = PluginDecision[RoughSorterState](
        intents=(intent,),
        next_state=RoughSorterState(),
        outcome_code="CONFORMANCE",
    )
    return _system_capability_intents(_context(), tuple(decision.intents))


def test_conformance_rejects_undeclared_converter_output() -> None:
    with pytest.raises(AssertionError, match="未在插件 Definition 声明"):
        assert_system_capability_effect_contract(
            definition=DEFINITION,
            intents=_convert(_typed_intent("runtime.not_declared", "v1")),
        )


def test_conformance_rejects_unknown_generated_converter_output() -> None:
    identity = ("runtime.unknown_generated", "v1")
    definition = replace(DEFINITION, allowed_capabilities=(*DEFINITION.allowed_capabilities, identity))

    with pytest.raises(AssertionError, match="不存在于 generated index"):
        assert_system_capability_effect_contract(
            definition=definition,
            intents=_convert(_typed_intent(*identity)),
        )


def test_conformance_rejects_query_capability_converter_output_as_effect() -> None:
    identity = ("wms.rough_sorter_inventory_admission", "v1")

    with pytest.raises(AssertionError, match="必须绑定 EFFECT"):
        assert_system_capability_effect_contract(
            definition=DEFINITION,
            intents=_convert(_typed_intent(*identity)),
        )


def test_conformance_uses_converter_identity_instead_of_source_system() -> None:
    legacy_command = RuntimeIntent(
        kind=RuntimeIntentKind.COMMAND,
        device_role="input_arm",
        action="PICK_AND_PUT",
        source_system="runtime.not-declared@v999",
        payload_json={"pkg_code": "PKG-1"},
        timeout_seconds=30,
    )

    converted = _convert(legacy_command)

    assert converted[0].source_system is None
    assert_system_capability_effect_contract(definition=DEFINITION, intents=converted)
