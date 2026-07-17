"""SYSTEM_CAPABILITY EFFECT intent 与三个最小 capability 的静态合同。"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.repositories.runtime_intent_log_repository import RuntimeIntentLogRepository
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind
from src.app.runtime.system_capabilities.definition import EffectCompletionMode, SystemCapabilityMode
from src.app.runtime.system_capabilities.device.device_command_write.definition import DEFINITION as DEVICE_DEFINITION
from src.app.runtime.system_capabilities.device.device_command_write.handler import DeviceCommandWriteHandler
from src.app.runtime.system_capabilities.material_flow.material_unit_write.definition import (
    DEFINITION as MATERIAL_DEFINITION,
)
from src.app.runtime.system_capabilities.material_flow.material_unit_write.handler import MaterialUnitWriteHandler
from src.app.runtime.system_capabilities.runtime.session_hold.definition import DEFINITION as HOLD_DEFINITION
from src.app.runtime.system_capabilities.runtime.session_hold.handler import SessionHoldHandler


def _intent(**overrides: object) -> RuntimeIntent:
    values: dict[str, object] = {
        "capability_key": "material_flow.material_unit_write",
        "contract_version": "v1",
        "operation_key": "scan:PKG-001:create",
        "payload": {
            "operation": "CREATE",
            "pkg_code": "PKG-001",
            "material_identity_key": "MAT-001",
            "six_in_one": {"PkgID": "PKG-001"},
            "status": "IN_TRANSIT",
        },
        "precondition": {"expected_absent": True},
        "fact_version": "material-unit:v0",
        "timeout_seconds": 5,
        "creator_authority": "WORKLINE_PLUGIN",
        "authorization_policy": "rough-sorter-effect-v1",
        "binding_snapshot": {"binding_id": 7, "binding_version": 2},
        "provider_snapshot": {"provider_code": "RUNTIME", "profile": "runtime"},
    }
    values.update(overrides)
    return RuntimeIntent.system_capability(**values)  # type: ignore[arg-type]


def test_system_capability_factory_pins_typed_payload_hash_and_authority_snapshots() -> None:
    intent = _intent()

    assert intent.kind is RuntimeIntentKind.SYSTEM_CAPABILITY
    assert intent.capability_key == "material_flow.material_unit_write"
    assert intent.contract_version == "v1"
    assert intent.operation_key == "scan:PKG-001:create"
    assert intent.payload_hash == sha256_digest(intent.payload_json)
    assert intent.precondition_json == {"expected_absent": True}
    assert intent.fact_version == "material-unit:v0"
    assert intent.creator_authority == "WORKLINE_PLUGIN"
    assert intent.authorization_policy == "rough-sorter-effect-v1"
    assert intent.binding_snapshot == {"binding_id": 7, "binding_version": 2}
    assert intent.provider_snapshot == {"provider_code": "RUNTIME", "profile": "runtime"}
    assert intent.timeout_seconds == 5


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capability_key", ""),
        ("contract_version", ""),
        ("operation_key", ""),
        ("payload", {}),
        ("fact_version", ""),
        ("timeout_seconds", 0),
        ("creator_authority", ""),
        ("authorization_policy", ""),
        ("binding_snapshot", {}),
        ("provider_snapshot", {}),
    ],
)
def test_system_capability_factory_rejects_incomplete_contract(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError, ValidationError)):
        _intent(**{field: value})


def test_system_capability_rejects_legacy_transport_or_mismatched_payload_hash() -> None:
    base = _intent().model_dump(mode="python")

    with pytest.raises(ValidationError, match="must not use legacy intent fields"):
        RuntimeIntent.model_validate({**base, "action": "rough-sorter-special-case"})
    with pytest.raises(ValidationError, match="payload_hash"):
        RuntimeIntent.model_validate({**base, "payload_hash": "0" * 64})


def test_system_capability_intent_log_pins_plugin_capability_and_execution_snapshots() -> None:
    from types import SimpleNamespace

    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot

    prepared = RuntimeIntentLogRepository().prepare_attempt_intents(
        locked=SimpleNamespace(
            inbox=SimpleNamespace(
                id=51,
                execution_session_id=61,
                correlation_id="corr-effect-ledger",
            )
        ),
        snapshot=AttemptSnapshot(
            processor_token="lease-1",
            session_version=3,
            plugin_state_version=2,
            definition_identity="rough_sorter@rough_sorter.v2",
            binding_id=7,
            binding_version=2,
            index_digest="a" * 64,
        ),
        intents=(_intent(),),
    )[0]

    log = prepared.model
    assert log.plugin_key == "rough_sorter"
    assert log.plugin_contract_version == "rough_sorter.v2"
    assert log.capability_key == "material_flow.material_unit_write"
    assert log.capability_contract_version == "v1"
    assert log.operation_identity == "scan:PKG-001:create"
    assert log.target_domain == "material_flow"
    assert log.payload_hash == _intent().payload_hash
    assert log.completion_mode == "LOCAL_TRANSACTIONAL"
    assert log.creator_authority == "WORKLINE_PLUGIN"
    assert log.authorization_policy == "rough-sorter-effect-v1"
    assert log.binding_snapshot_json == {"binding_id": 7, "binding_version": 2}
    assert log.provider_snapshot_json == {"provider_code": "RUNTIME", "profile": "runtime"}


def test_three_effect_definitions_have_closed_completion_modes() -> None:
    assert MATERIAL_DEFINITION.mode is SystemCapabilityMode.EFFECT
    assert MATERIAL_DEFINITION.completion_mode is EffectCompletionMode.LOCAL_TRANSACTIONAL
    assert DEVICE_DEFINITION.mode is SystemCapabilityMode.EFFECT
    assert DEVICE_DEFINITION.completion_mode is EffectCompletionMode.OUTBOX_ASYNC
    assert HOLD_DEFINITION.mode is SystemCapabilityMode.EFFECT
    assert HOLD_DEFINITION.completion_mode is EffectCompletionMode.LOCAL_TRANSACTIONAL


@pytest.mark.parametrize("handler", [MaterialUnitWriteHandler, DeviceCommandWriteHandler, SessionHoldHandler])
def test_effect_handlers_have_no_repository_transaction_or_external_io_escape(handler: type[object]) -> None:
    source = inspect.getsource(handler)
    forbidden = ("Repository", ".commit(", ".rollback(", "httpx", "requests.", "send_task", "delay(")
    assert all(token not in source for token in forbidden)
