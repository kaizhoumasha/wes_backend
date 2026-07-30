"""Runtime domain service 创建 SYSTEM_CAPABILITY EFFECT 的 authority 合同。"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
_CORRELATION_ID = "smt-inbound-handoff:17"
_WORKLINE_ID = 13


@dataclass(frozen=True, slots=True)
class _ResolvedDomainAuthority:
    producer: str = _PRODUCER
    correlation_id: str = _CORRELATION_ID
    business_owner_key: str = _BUSINESS_OWNER_KEY
    workline_id: int = _WORKLINE_ID

    @property
    def binding_snapshot(self) -> dict[str, object]:
        return {
            "producer": self.producer,
            "business_owner_key": self.business_owner_key,
            "workline_id": self.workline_id,
            "correlation_id": self.correlation_id,
        }


class _DomainAuthorityResolver:
    def __init__(
        self,
        authority: _ResolvedDomainAuthority | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.authority = authority or _ResolvedDomainAuthority()
        self.error = error
        self.calls: list[tuple[object, str]] = []

    async def resolve(self, db: object, *, correlation_id: str) -> _ResolvedDomainAuthority:
        self.calls.append((db, correlation_id))
        if self.error is not None:
            raise self.error
        return self.authority


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
    return {"db": object(), "correlation_id": _CORRELATION_ID}


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
        binding_snapshot=(
            binding_snapshot
            if binding_snapshot is not None
            else {
                "producer": producer,
                "business_owner_key": _BUSINESS_OWNER_KEY,
                "workline_id": _WORKLINE_ID,
                "correlation_id": _CORRELATION_ID,
            }
        ),
        provider_snapshot=(
            provider_snapshot if provider_snapshot is not None else {"provider_code": "RUNTIME", "profile": profile}
        ),
    )


def _service(
    repository: _RecordingEffectRepository,
    *,
    include_other_capability: bool = False,
    resolver: _DomainAuthorityResolver | None = None,
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
        domain_authority_resolver=resolver or _DomainAuthorityResolver(),
    )


@pytest.mark.asyncio
async def test_smt_handoff_domain_authority_claims_e11_with_frozen_domain_identity() -> None:
    repository = _RecordingEffectRepository()
    resolver = _DomainAuthorityResolver()
    ctx = _ctx()

    prepared = await _service(repository, resolver=resolver).prepare_and_claim(ctx, _intent())

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
    assert claim["correlation_id"] == _CORRELATION_ID
    assert claim["operation_identity"] == _OPERATION_KEY
    assert claim["binding_snapshot_json"] == resolver.authority.binding_snapshot
    assert "producer" not in claim
    assert "business_owner_key" not in claim
    assert "workline_id" not in claim
    assert resolver.calls == [(ctx["db"], _CORRELATION_ID)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "intent_kwargs", "include_other_capability"),
    [
        ("other producer", {"producer": "OTHER_PRODUCER"}, False),
        ("empty producer", {"producer": ""}, False),
        (
            "other capability",
            {"capability_key": "wms.fulfillment.other"},
            True,
        ),
        (
            "self asserted allowlist",
            {"producer": "OTHER_PRODUCER"},
            False,
        ),
        (
            "wrong business owner",
            {"binding_snapshot": {**_ResolvedDomainAuthority().binding_snapshot, "business_owner_key": "forged"}},
            False,
        ),
        (
            "wrong workline",
            {"binding_snapshot": {**_ResolvedDomainAuthority().binding_snapshot, "workline_id": 999}},
            False,
        ),
        (
            "wrong correlation anchor",
            {"binding_snapshot": {**_ResolvedDomainAuthority().binding_snapshot, "correlation_id": "forged"}},
            False,
        ),
        (
            "mixed creator authority",
            {"creator_authority": "WORKLINE_PLUGIN"},
            False,
        ),
        (
            "mixed authorization policy",
            {"authorization_policy": "PLUGIN_DECLARED_CAPABILITY"},
            False,
        ),
        (
            "wrong binding snapshot",
            {"binding_snapshot": {"producer": _PRODUCER, "allowed": True}},
            False,
        ),
        (
            "wrong provider snapshot",
            {"provider_snapshot": {"provider_code": "RUNTIME", "profile": "caller-owned"}},
            False,
        ),
    ],
)
async def test_domain_authority_rejects_invalid_identity_without_claim_side_effect(
    case: str,
    intent_kwargs: dict[str, object],
    include_other_capability: bool,
) -> None:
    repository = _RecordingEffectRepository()
    ctx = _ctx()
    if case == "self asserted allowlist":
        ctx["allowed_capabilities"] = {"OTHER_PRODUCER": (("wms.fulfillment.full_box_exchange", "v1"),)}

    with pytest.raises((PermissionError, TypeError, ValueError), match="runtime domain"):
        await _service(repository, include_other_capability=include_other_capability).prepare_and_claim(
            ctx,
            _intent(**intent_kwargs),
        )

    assert repository.calls == [], case


@pytest.mark.asyncio
async def test_domain_authority_rejects_missing_or_unpersisted_correlation_without_claim() -> None:
    for ctx, resolver in (
        ({"db": object()}, _DomainAuthorityResolver()),
        (
            {
                **_ctx(),
                "execution_correlation": SimpleNamespace(id=71),
                "workline": SimpleNamespace(id=_WORKLINE_ID),
            },
            _DomainAuthorityResolver(error=PermissionError("runtime domain authority is not persisted")),
        ),
    ):
        repository = _RecordingEffectRepository()
        with pytest.raises((PermissionError, ValueError), match="runtime domain"):
            await _service(repository, resolver=resolver).prepare_and_claim(ctx, _intent())
        assert repository.calls == []


def test_domain_idempotency_key_is_bounded_without_session_placeholders() -> None:
    key = SystemCapabilityIntentService._final_idempotency_key(
        _ctx(),
        _intent(operation_key="x" * 160),
        domain_producer=_PRODUCER,
    )

    assert len(key) == 160
    assert key.startswith("system-capability:wms.fulfillment.full_box_exchange@v1:domain:SMT_INBOUND_HANDOFF:")
    assert "None" not in key


def test_runtime_intent_log_execution_session_fk_is_nullable() -> None:
    column = RuntimeIntentLog.__table__.c.execution_session_id

    assert column.nullable is True
    assert {foreign_key.target_fullname for foreign_key in column.foreign_keys} == {"wes_runtime.execution_sessions.id"}
