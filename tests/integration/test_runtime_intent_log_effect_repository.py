"""RuntimeIntentLog 单一 effect ledger 的生产 Repository 状态合同。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from src.app.runtime.orchestration.effect_bridges import (
    EffectCallbackBridge,
    EffectCallbackOutcome,
    EffectTransportBridge,
)
from src.app.runtime.orchestration.repositories.runtime_intent_log_repository import RuntimeIntentLogRepository
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.services.effect_reducer_service import EffectReducer
from src.app.runtime.orchestration.services.intent.system_capability_effect_service import (
    SystemCapabilityEffectService,
)
from src.app.runtime.orchestration.system_capability_effect_claim import (
    SystemCapabilityAdmissionClosed,
    SystemCapabilityClaimResult,
    SystemCapabilityIdempotencyConflict,
)
from src.app.runtime.system_capabilities.outcomes import BusinessReject, Success
from src.app.runtime.system_capabilities.wms.effect_runtime import build_wms_effect_capability_definition
from src.app.sys.external_http_transport import ExternalHttpProtocolResult, ExternalHttpTransportResult
from src.app.wms_integration.operation_contract import WmsCompletionMode
from src.app.wms_integration.operation_registry import EFFECT_OPERATIONS
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES, RESULT_FIXTURES


def _claim() -> dict[str, object]:
    return {
        "provider_code": "RUNTIME",
        "operation_kind": "system_capability_effect",
        "idempotency_key": "effect-key-1",
        "request_hash": "a" * 64,
        "dispatch_key": "effect-dispatch-1",
        "execution_session_id": 21,
        "execution_work_item_id": 41,
        "correlation_id": "corr-1",
        "capability_key": "runtime.session_hold",
        "capability_contract_version": "v1",
        "operation_identity": "hold-1",
        "creator_authority": "WORKLINE_PLUGIN",
        "authorization_policy": "PLUGIN_DECLARED_CAPABILITY",
        "binding_snapshot_json": {"binding_id": 9, "binding_version": 1},
        "provider_snapshot_json": {"provider_code": "RUNTIME", "profile": "runtime"},
        "precondition_json": {"expected": 1},
        "fact_version": "fact:1",
        "payload_hash": "a" * 64,
        "completion_mode": "LOCAL_TRANSACTIONAL",
        "updated_at_ms": 1000,
    }


def _domain_claim() -> dict[str, object]:
    return {
        **_claim(),
        "execution_session_id": None,
        "execution_work_item_id": None,
        "correlation_id": "corr-domain-1",
        "capability_key": "external.transport_confirmation",
        "operation_identity": "transport-confirmation:task-17",
        "creator_authority": "RUNTIME_DOMAIN_SERVICE",
        "authorization_policy": "DOMAIN_CAPABILITY_ALLOWLIST",
        "binding_snapshot_json": {
            "producer": "CORE_TRANSPORT",
            "business_owner_key": "transport-task:17",
            "workline_id": 13,
            "correlation_id": "corr-domain-1",
        },
    }


def _evidence(kind: str, *, occurred_at_ms: int) -> SimpleNamespace:
    code = "SUCCESS" if kind == "success" else "STALE_PRECONDITION"
    payload = {"kind": "success", "payload": {"held": True, "reason_code": "REVIEW"}}
    if kind == "business_reject":
        payload = {
            "kind": "business_reject",
            "reason_code": code,
            "message": "facts changed",
            "retryable": False,
            "details": {},
        }
    data = {
        "capability_key": "runtime.session_hold",
        "contract_version": "v1",
        "operation_key": "hold-1",
        "idempotency_key": "effect-key-1",
        "payload_hash": "a" * 64,
        "outcome_kind": kind,
        "outcome_code": code,
        "outcome": payload,
        "occurred_at_ms": occurred_at_ms,
    }
    return SimpleNamespace(**data, model_dump=lambda **_kwargs: dict(data))


class _PersistedWmsReplayIntentService:
    """第二次 apply 只重新读取生产 repository，不缓存首次 reducer 结果。"""

    def __init__(self, *, repository, claim, operation) -> None:
        self.repository = repository
        self.claim = claim
        self.operation = operation

    async def prepare_and_claim(self, ctx, _intent):
        claim_result = await self.repository.claim_or_match(ctx["db"], **self.claim)
        intent_log = await self.repository.get_claimed_intent(ctx["db"], claim=self.claim)
        return SimpleNamespace(
            definition=build_wms_effect_capability_definition(self.operation),
            request=self.operation.request_model.model_validate(REQUEST_FIXTURES[self.operation.identity]),
            idempotency_key=self.claim["idempotency_key"],
            payload_hash=self.claim["payload_hash"],
            claim_result=claim_result,
            intent_log=intent_log,
            has_durable_outbox=True,
        )


@pytest.mark.asyncio
async def test_production_repository_preserves_rejected_terminal_on_same_claim(db_session) -> None:
    repository = RuntimeIntentLogRepository()
    claim = _claim()

    assert await repository.claim_or_match(db_session, **claim) is SystemCapabilityClaimResult.NEW
    await EffectCallbackBridge().record(
        db_session,
        dispatch_key=str(claim["dispatch_key"]),
        outcome=EffectCallbackOutcome.REJECTED,
        occurred_at_ms=1100,
        source_event_id="local-effect:business-reject",
        reason_code="STALE_PRECONDITION",
        evidence_json=_evidence("business_reject", occurred_at_ms=1100).model_dump(),
    )
    assert await repository.claim_or_match(db_session, **claim) is SystemCapabilityClaimResult.MATCH

    persisted = await repository.get_success_evidence(db_session, claim=claim)
    assert persisted is None
    row = (
        await db_session.execute(select(RuntimeIntentLog).where(RuntimeIntentLog.idempotency_key == "effect-key-1"))
    ).scalar_one()
    assert row.effect_status is RuntimeIntentStatus.REJECTED
    assert [item["outcome_kind"] for item in row.outcome_history_json] == ["business_reject"]


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_kind", ("success", "business_reject"))
async def test_sync_dispatch_reducer_terminal_replays_from_persisted_envelope_on_second_apply(
    db_session,
    terminal_kind,
) -> None:
    operation = next(
        operation for operation in EFFECT_OPERATIONS if operation.completion_mode is WmsCompletionMode.SYNC_RESULT
    )
    request_payload = REQUEST_FIXTURES[operation.identity]
    claim = {
        **_claim(),
        "capability_key": operation.identity.rsplit("@", maxsplit=1)[0],
        "capability_contract_version": "v1",
        "operation_identity": "operation-1",
        "completion_mode": "OUTBOX_ASYNC",
        "dispatch_key": request_payload["dispatch_key"],
    }
    repository = RuntimeIntentLogRepository()
    assert await repository.claim_or_match(db_session, **claim) is SystemCapabilityClaimResult.NEW
    response_payload = (
        RESULT_FIXTURES[operation.identity]
        if terminal_kind == "success"
        else {
            "reason_code": operation.reject_codes[0],
            "message": "provider rejected the immutable request",
        }
    )
    transport_result = ExternalHttpTransportResult.accepted(
        http_status_code=200 if terminal_kind == "success" else 409,
        protocol_result=(
            ExternalHttpProtocolResult.ACCEPTED if terminal_kind == "success" else ExternalHttpProtocolResult.REJECTED
        ),
        response_body=json.dumps(response_payload, ensure_ascii=False, separators=(",", ":")).encode(),
    )
    bridge = EffectTransportBridge(reducer=EffectReducer())
    resolution = bridge.resolve_result(
        operation_identity=operation.identity,
        payload_json=request_payload,
        result=transport_result,
        dispatch_key=request_payload["dispatch_key"],
        attempt_no=1,
        retry_exhausted=False,
        occurred_at_ms=1100,
    )
    await bridge.record_result(
        db_session,
        dispatch_key=request_payload["dispatch_key"],
        attempt_no=1,
        result=transport_result,
        retry_exhausted=False,
        occurred_at_ms=1100,
        operation_identity=operation.identity,
        payload_json=request_payload,
        resolution=resolution,
    )
    await db_session.commit()
    db_session.expire_all()

    service = SystemCapabilityEffectService(
        intent_service=_PersistedWmsReplayIntentService(
            repository=repository,
            claim=claim,
            operation=operation,
        )
    )
    replay = await service.apply(
        {"db": db_session},
        SimpleNamespace(
            capability_key=claim["capability_key"],
            contract_version="v1",
            operation_key=claim["operation_identity"],
        ),
    )

    assert replay.idempotent_replay is True
    if terminal_kind == "success":
        assert replay.outcome == Success(
            payload=operation.result_model.model_validate(RESULT_FIXTURES[operation.identity])
        )
        assert replay.remote_completed is True
    else:
        assert isinstance(replay.outcome, BusinessReject)
        assert replay.outcome.reason_code == operation.reject_codes[0]
        assert replay.remote_completed is False
    persisted = (
        await db_session.execute(
            select(RuntimeIntentLog).where(RuntimeIntentLog.idempotency_key == claim["idempotency_key"])
        )
    ).scalar_one()
    assert persisted.outcome_json["outcome"] == replay.outcome.model_dump(mode="json")
    assert all("typed_outcome" not in evidence for evidence in persisted.outcome_history_json)


@pytest.mark.asyncio
async def test_production_repository_matches_existing_proposed_claim_and_locks_it(db_session) -> None:
    repository = RuntimeIntentLogRepository()
    claim = _claim()

    assert await repository.claim_or_match(db_session, **claim) is SystemCapabilityClaimResult.NEW
    proposed = await repository.get_claimed_intent(db_session, claim=claim)
    assert proposed is not None
    assert proposed.effect_status is RuntimeIntentStatus.PROPOSED

    assert await repository.claim_or_match(db_session, **claim) is SystemCapabilityClaimResult.MATCH
    replay = await repository.get_claimed_intent(db_session, claim=claim)
    assert replay is not None
    assert replay.id == proposed.id
    assert replay.dispatch_key == claim["dispatch_key"]
    assert replay.effect_status is RuntimeIntentStatus.PROPOSED


@pytest.mark.asyncio
async def test_existing_only_claim_rejects_absent_row_without_inserting(db_session) -> None:
    repository = RuntimeIntentLogRepository()
    claim = _claim()

    with pytest.raises(SystemCapabilityAdmissionClosed):
        await repository.claim_or_match(db_session, allow_insert=False, **claim)

    assert await repository.get_claimed_intent(db_session, claim=claim) is None


@pytest.mark.asyncio
async def test_existing_only_claim_matches_exact_existing_row(db_session) -> None:
    repository = RuntimeIntentLogRepository()
    claim = _claim()
    assert await repository.claim_or_match(db_session, **claim) is SystemCapabilityClaimResult.NEW

    assert await repository.claim_or_match(db_session, allow_insert=False, **claim) is SystemCapabilityClaimResult.MATCH


@pytest.mark.asyncio
async def test_existing_only_claim_preserves_idempotency_conflict(db_session) -> None:
    repository = RuntimeIntentLogRepository()
    claim = _claim()
    assert await repository.claim_or_match(db_session, **claim) is SystemCapabilityClaimResult.NEW

    with pytest.raises(SystemCapabilityIdempotencyConflict):
        await repository.claim_or_match(
            db_session,
            allow_insert=False,
            **{**claim, "request_hash": "b" * 64},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        {"correlation_id": "corr-domain-2"},
        {
            "binding_snapshot_json": {
                "producer": "CORE_TRANSPORT",
                "business_owner_key": "transport-task:other",
                "workline_id": 13,
                "correlation_id": "corr-domain-1",
            }
        },
        {
            "binding_snapshot_json": {
                "producer": "CORE_TRANSPORT",
                "business_owner_key": "transport-task:17",
                "workline_id": 99,
                "correlation_id": "corr-domain-1",
            }
        },
    ],
)
async def test_domain_match_rejects_correlation_owner_or_workline_drift(db_session, changed) -> None:
    repository = RuntimeIntentLogRepository()
    claim = _domain_claim()
    assert await repository.claim_or_match(db_session, **claim) is SystemCapabilityClaimResult.NEW

    with pytest.raises(SystemCapabilityIdempotencyConflict):
        await repository.claim_or_match(db_session, **{**claim, **changed})


@pytest.mark.asyncio
async def test_production_repository_claim_is_rolled_back_with_outer_transaction(db_session) -> None:
    repository = RuntimeIntentLogRepository()
    claim = _claim()

    assert await repository.claim_or_match(db_session, **claim) is SystemCapabilityClaimResult.NEW
    row = await repository.get_claimed_intent(db_session, claim=claim)
    assert row is not None
    assert row.capability_key == "runtime.session_hold"
    assert row.capability_contract_version == "v1"
    assert row.operation_identity == "hold-1"
    assert row.target_domain == "runtime"
    assert row.payload_hash == "a" * 64
    assert row.completion_mode == "LOCAL_TRANSACTIONAL"
    assert row.creator_authority == "WORKLINE_PLUGIN"
    assert row.authorization_policy == "PLUGIN_DECLARED_CAPABILITY"
    assert row.binding_snapshot_json == {"binding_id": 9, "binding_version": 1}
    assert row.provider_snapshot_json == {"provider_code": "RUNTIME", "profile": "runtime"}
    await db_session.rollback()

    result = await db_session.execute(
        select(RuntimeIntentLog).where(RuntimeIntentLog.idempotency_key == "effect-key-1")
    )
    assert result.scalar_one_or_none() is None
    assert await repository.claim_or_match(db_session, **claim) is SystemCapabilityClaimResult.NEW


@pytest.mark.asyncio
async def test_production_repository_requires_explicit_dispatch_key(db_session) -> None:
    claim = _claim()
    claim.pop("dispatch_key")

    with pytest.raises(ValueError, match="dispatch_key"):
        await RuntimeIntentLogRepository().claim_or_match(db_session, **claim)


@pytest.mark.asyncio
async def test_production_repository_rejects_dispatch_key_change_on_matching_identity(db_session) -> None:
    repository = RuntimeIntentLogRepository()
    claim = _claim()
    assert await repository.claim_or_match(db_session, **claim) is SystemCapabilityClaimResult.NEW

    changed = {**claim, "dispatch_key": "effect-dispatch-replacement"}
    with pytest.raises(ValueError, match=r"dispatch_key.*不可变"):
        await repository.claim_or_match(db_session, **changed)
    await db_session.rollback()


@pytest.mark.asyncio
async def test_production_repository_locks_conflicted_intent_by_stable_identity(db_session) -> None:
    repository = RuntimeIntentLogRepository()
    claim = _claim()
    assert await repository.claim_or_match(db_session, **claim) is SystemCapabilityClaimResult.NEW

    with pytest.raises(SystemCapabilityIdempotencyConflict) as exc_info:
        await repository.claim_or_match(db_session, **{**claim, "request_hash": "b" * 64})

    conflict = exc_info.value
    authoritative = await repository.get_conflicted_intent_for_update(
        db_session,
        provider_code=conflict.provider_code,
        operation_kind=conflict.operation_kind,
        idempotency_key=conflict.idempotency_key,
    )
    assert authoritative is not None
    assert authoritative.dispatch_key == claim["dispatch_key"]
    assert authoritative.request_hash == "a" * 64
