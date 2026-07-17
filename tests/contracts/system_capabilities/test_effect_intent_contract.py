"""SYSTEM_CAPABILITY EFFECT intent 与三个最小 capability 的静态合同。"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.repositories.runtime_intent_log_repository import RuntimeIntentLogRepository
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.system_capabilities.definition import EffectCompletionMode, SystemCapabilityMode
from src.app.runtime.system_capabilities.device.device_command_write.definition import DEFINITION as DEVICE_DEFINITION
from src.app.runtime.system_capabilities.device.device_command_write.handler import DeviceCommandWriteHandler
from src.app.runtime.system_capabilities.material_flow.material_unit_write.definition import (
    DEFINITION as MATERIAL_DEFINITION,
)
from src.app.runtime.system_capabilities.material_flow.material_unit_write.handler import MaterialUnitWriteHandler
from src.app.runtime.system_capabilities.runtime.session_hold.definition import DEFINITION as HOLD_DEFINITION
from src.app.runtime.system_capabilities.runtime.session_hold.handler import SessionHoldHandler
from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION as ROUGH_SORTER_PLUGIN_DEFINITION


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
        "authorization_policy": "PLUGIN_DECLARED_CAPABILITY",
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
    assert intent.authorization_policy == "PLUGIN_DECLARED_CAPABILITY"
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


def test_system_capability_operation_key_accepts_auditable_160_character_boundary() -> None:
    intent = _intent(operation_key="a" * 160)
    assert intent.operation_key == "a" * 160


@pytest.mark.parametrize("operation_key", ["a" * 161, "contains space", "bad$key", ":starts-with-colon"])
def test_system_capability_operation_key_rejects_overflow_and_illegal_characters(operation_key: str) -> None:
    with pytest.raises(ValidationError):
        _intent(operation_key=operation_key)


def test_system_capability_rejects_legacy_transport_or_mismatched_payload_hash() -> None:
    base = _intent().model_dump(mode="python")

    with pytest.raises(ValidationError, match="must not use legacy intent fields"):
        RuntimeIntent.model_validate({**base, "action": "rough-sorter-special-case"})
    with pytest.raises(ValidationError, match="payload_hash"):
        RuntimeIntent.model_validate({**base, "payload_hash": "0" * 64})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capability_key", "device.device_command_write"),
        ("contract_version", "v1"),
        ("operation_key", "forged-operation"),
        ("payload_hash", "a" * 64),
        ("precondition_json", {"expected": 1}),
        ("fact_version", "fact:1"),
        ("creator_authority", "WORKLINE_PLUGIN"),
        ("authorization_policy", "PLUGIN_DECLARED_CAPABILITY"),
        ("binding_snapshot", {"binding_id": 7, "binding_version": 2}),
        ("provider_snapshot", {"provider_code": "RUNTIME", "profile": "runtime"}),
    ],
)
def test_non_system_capability_intents_reject_every_capability_only_field(field: str, value: object) -> None:
    with pytest.raises(ValidationError, match="SYSTEM_CAPABILITY-only"):
        RuntimeIntent.model_validate(
            {
                "kind": RuntimeIntentKind.UPDATE_CONTEXT,
                "context_patch": {"source": "legacy"},
                field: value,
            }
        )


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
    assert log.authorization_policy == "PLUGIN_DECLARED_CAPABILITY"
    assert log.binding_snapshot_json == {"binding_id": 7, "binding_version": 2}
    assert log.provider_snapshot_json == {"provider_code": "RUNTIME", "profile": "runtime"}


def test_three_effect_definitions_have_closed_completion_modes() -> None:
    assert MATERIAL_DEFINITION.mode is SystemCapabilityMode.EFFECT
    assert MATERIAL_DEFINITION.completion_mode is EffectCompletionMode.LOCAL_TRANSACTIONAL
    assert DEVICE_DEFINITION.mode is SystemCapabilityMode.EFFECT
    assert DEVICE_DEFINITION.completion_mode is EffectCompletionMode.OUTBOX_ASYNC
    assert HOLD_DEFINITION.mode is SystemCapabilityMode.EFFECT
    assert HOLD_DEFINITION.completion_mode is EffectCompletionMode.LOCAL_TRANSACTIONAL


def test_rough_sorter_declares_all_effect_capabilities_in_generated_author_allowlist() -> None:
    declared = set(ROUGH_SORTER_PLUGIN_DEFINITION.allowed_capabilities)
    assert {
        (MATERIAL_DEFINITION.capability_key, MATERIAL_DEFINITION.contract_version),
        (DEVICE_DEFINITION.capability_key, DEVICE_DEFINITION.contract_version),
        (HOLD_DEFINITION.capability_key, HOLD_DEFINITION.contract_version),
    } <= declared


def test_runtime_intent_log_is_the_only_effect_ledger() -> None:
    columns = RuntimeIntentLog.__table__.c
    assert columns.provider_code.nullable is False
    assert columns.request_hash.nullable is False
    assert columns.effect_status.nullable is True
    assert columns.outcome_json.nullable is False
    assert columns.outcome_history_json.nullable is False
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in RuntimeIntentLog.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("provider_code", "operation_kind", "idempotency_key") in unique_columns


def test_plugin_binding_migration_extends_runtime_intent_log_without_second_effect_ledger() -> None:
    migration = Path(
        "migrations/versions/20260717_0739_fa15ba0aef65_add_workline_plugin_runtime_binding.py"
    ).read_text()
    assert "system_capability_effect_records" not in migration
    for column in ("effect_status", "outcome_json", "outcome_history_json", "execution_work_item_id"):
        assert f'"{column}"' in migration


@pytest.mark.parametrize("handler", [MaterialUnitWriteHandler, DeviceCommandWriteHandler, SessionHoldHandler])
def test_effect_handlers_have_no_repository_transaction_or_external_io_escape(handler: type[object]) -> None:
    source = inspect.getsource(handler)
    forbidden = ("Repository", ".commit(", ".rollback(", "httpx", "requests.", "send_task", "delay(")
    assert all(token not in source for token in forbidden)


def test_effect_services_delegate_all_database_mutations_to_repositories() -> None:
    from src.app.device.services.device_command_service import DeviceCommandService
    from src.app.runtime.orchestration.services.material_unit_mutation_service import MaterialUnitMutationService
    from src.app.runtime.orchestration.services.session_hold_mutation_service import SessionHoldMutationService

    methods = (
        MaterialUnitMutationService.create,
        MaterialUnitMutationService.update_status,
        DeviceCommandService.prepare_runtime_effect,
        SessionHoldMutationService.hold,
    )
    forbidden = ("db.add(", 'ctx["db"].get(', "db.get(")
    for method in methods:
        source = inspect.getsource(method)
        assert all(token not in source for token in forbidden), f"{method.__qualname__} bypasses Repository"
