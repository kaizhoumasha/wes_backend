"""Runtime domain service 创建 SYSTEM_CAPABILITY EFFECT 的 authority 合同。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.services.intent.system_capability_intent_service import (
    SystemCapabilityIntentService,
)
from src.app.runtime.orchestration.system_capability_effect_claim import SystemCapabilityClaimResult
from src.app.runtime.system_capabilities.wms.fulfillment.full_box_exchange.definition import (
    DEFINITION as FULL_BOX_EXCHANGE_DEFINITION,
)

_PRODUCER = "SMT_INBOUND_HANDOFF"
_CAPABILITY = "wms.fulfillment.full_box_exchange"
_OPERATION_KEY = "wms-e11:handoff-17:box-23"
_BUSINESS_OWNER_KEY = "smt-inbound-handoff-demand:17"


class _RecordingEffectRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.intent_log = SimpleNamespace(
            id=91,
            effect_status=RuntimeIntentStatus.PROPOSED,
            dispatch_key=_OPERATION_KEY,
        )

    async def claim_or_match(self, _db: object, **claim: Any) -> SystemCapabilityClaimResult:
        self.calls.append(claim)
        return SystemCapabilityClaimResult.NEW

    async def get_claimed_intent(self, _db: object, *, claim: dict[str, Any]) -> object:
        assert claim == self.calls[-1]
        return self.intent_log


def _ctx() -> dict[str, object]:
    return {
        "db": object(),
        "execution_correlation": SimpleNamespace(
            id=71,
            correlation_id="smt-inbound-handoff:17",
            execution_session_id=None,
            trace_id="trace-release-11",
            source_event_id="rack-release-11",
            business_owner_key=_BUSINESS_OWNER_KEY,
        ),
        "workline": SimpleNamespace(id=13),
    }


def _intent(
    *,
    capability_key: str = _CAPABILITY,
    producer: str = _PRODUCER,
    creator_authority: str = "RUNTIME_DOMAIN_SERVICE",
    authorization_policy: str = "DOMAIN_CAPABILITY_ALLOWLIST",
    binding_snapshot: dict[str, object] | None = None,
    provider_snapshot: dict[str, object] | None = None,
    payload_marker: str = "A",
    operation_key: str = _OPERATION_KEY,
) -> RuntimeIntent:
    profile = FULL_BOX_EXCHANGE_DEFINITION.admission
    return RuntimeIntent.system_capability(
        capability_key=capability_key,
        contract_version="v1",
        operation_key=operation_key,
        dispatch_key=operation_key,
        payload={
            "dispatch_key": operation_key,
            "exchange_request_key": operation_key,
            "station_code": "SMT-EXCHANGE",
            "rack_id": "RACK-1",
            "rack_face": "A",
            "full_box_id": "BOX-23",
            "source_slot_id": "SLOT-1",
            "occupancies": [
                {
                    "occupancy_id": f"OCC-{payload_marker}",
                    "pkg_id": "PKG-1",
                    "material_code": "MAT-1",
                    "quantity": "1",
                }
            ],
        },
        precondition={"handoff_demand_id": 17},
        fact_version="handoff-demand:17:v1",
        timeout_seconds=FULL_BOX_EXCHANGE_DEFINITION.timeout_seconds,
        creator_authority=creator_authority,
        authorization_policy=authorization_policy,
        binding_snapshot=binding_snapshot if binding_snapshot is not None else {"producer": producer},
        provider_snapshot=(
            provider_snapshot if provider_snapshot is not None else {"provider_code": "RUNTIME", "profile": profile}
        ),
    )


def _service(
    repository: _RecordingEffectRepository,
    *,
    include_other_capability: bool = False,
) -> SystemCapabilityIntentService:
    definitions = {(_CAPABILITY, "v1"): FULL_BOX_EXCHANGE_DEFINITION}
    if include_other_capability:
        definitions[("wms.fulfillment.other", "v1")] = replace(
            FULL_BOX_EXCHANGE_DEFINITION,
            capability_key="wms.fulfillment.other",
        )
    return SystemCapabilityIntentService(
        definitions=definitions,
        plugin_definitions={},
        plugin_index_digest="d" * 64,
        effect_repository=repository,
        effect_reducer=object(),
        effect_reconciliation_bridge=object(),
    )


@pytest.mark.asyncio
async def test_smt_handoff_domain_authority_claims_e11_with_frozen_domain_identity() -> None:
    repository = _RecordingEffectRepository()

    prepared = await _service(repository).prepare_and_claim(_ctx(), _intent())

    assert prepared.idempotency_key == (
        "system-capability:wms.fulfillment.full_box_exchange@v1:domain:SMT_INBOUND_HANDOFF:wms-e11:handoff-17:box-23"
    )
    assert "None" not in prepared.idempotency_key
    [claim] = repository.calls
    assert claim["execution_session_id"] is None
    assert claim["execution_work_item_id"] is None
    assert claim["plugin_key"] is None
    assert claim["plugin_contract_version"] is None
    assert claim["binding_id"] is None
    assert claim["binding_version"] is None
    assert claim["correlation_id"] == "smt-inbound-handoff:17"
    assert claim["business_owner_key"] == _BUSINESS_OWNER_KEY
    assert claim["workline_id"] == 13
    assert claim["producer"] == _PRODUCER
    assert claim["operation_identity"] == _OPERATION_KEY
    assert claim["binding_snapshot_json"] == {"producer": _PRODUCER}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "ctx_mutation", "intent_kwargs", "include_other_capability"),
    [
        ("other producer", None, {"producer": "OTHER_PRODUCER"}, False),
        ("empty producer", None, {"producer": ""}, False),
        (
            "other capability",
            None,
            {"capability_key": "wms.fulfillment.other"},
            True,
        ),
        (
            "self asserted allowlist",
            lambda ctx: ctx.update(
                {"allowed_capabilities": {"OTHER_PRODUCER": (("wms.fulfillment.full_box_exchange", "v1"),)}}
            ),
            {"producer": "OTHER_PRODUCER"},
            False,
        ),
        (
            "missing correlation",
            lambda ctx: ctx.pop("execution_correlation"),
            {},
            False,
        ),
        (
            "unpersisted correlation",
            lambda ctx: setattr(ctx["execution_correlation"], "id", None),
            {},
            False,
        ),
        (
            "missing business owner",
            lambda ctx: setattr(ctx["execution_correlation"], "business_owner_key", None),
            {},
            False,
        ),
        (
            "correlation claims plugin session",
            lambda ctx: setattr(ctx["execution_correlation"], "execution_session_id", 31),
            {},
            False,
        ),
        (
            "missing workline",
            lambda ctx: ctx.pop("workline"),
            {},
            False,
        ),
        (
            "fake plugin pin",
            lambda ctx: ctx.update(
                {
                    "session": SimpleNamespace(id=31),
                    "work_item": SimpleNamespace(id=41),
                    "inbox": SimpleNamespace(id=51),
                    "plugin_binding": SimpleNamespace(id=61),
                }
            ),
            {},
            False,
        ),
        (
            "mixed creator authority",
            None,
            {"creator_authority": "WORKLINE_PLUGIN"},
            False,
        ),
        (
            "mixed authorization policy",
            None,
            {"authorization_policy": "PLUGIN_DECLARED_CAPABILITY"},
            False,
        ),
        (
            "wrong binding snapshot",
            None,
            {"binding_snapshot": {"producer": _PRODUCER, "allowed": True}},
            False,
        ),
        (
            "wrong provider snapshot",
            None,
            {"provider_snapshot": {"provider_code": "RUNTIME", "profile": "caller-owned"}},
            False,
        ),
    ],
)
async def test_domain_authority_rejects_invalid_identity_without_claim_side_effect(
    case: str,
    ctx_mutation: Any,
    intent_kwargs: dict[str, object],
    include_other_capability: bool,
) -> None:
    repository = _RecordingEffectRepository()
    ctx = _ctx()
    if ctx_mutation is not None:
        ctx_mutation(ctx)

    with pytest.raises((PermissionError, TypeError, ValueError), match="runtime domain"):
        await _service(repository, include_other_capability=include_other_capability).prepare_and_claim(
            ctx,
            _intent(**intent_kwargs),
        )

    assert repository.calls == [], case


def test_domain_idempotency_key_is_bounded_without_session_placeholders() -> None:
    key = SystemCapabilityIntentService._final_idempotency_key(
        _ctx(),
        _intent(operation_key="x" * 160),
    )

    assert len(key) == 160
    assert key.startswith("system-capability:wms.fulfillment.full_box_exchange@v1:domain:SMT_INBOUND_HANDOFF:")
    assert "None" not in key


def test_runtime_intent_log_execution_session_fk_is_nullable() -> None:
    column = RuntimeIntentLog.__table__.c.execution_session_id

    assert column.nullable is True
    assert {foreign_key.target_fullname for foreign_key in column.foreign_keys} == {"wes_runtime.execution_sessions.id"}
