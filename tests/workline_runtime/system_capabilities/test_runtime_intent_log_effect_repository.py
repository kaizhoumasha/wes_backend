"""RuntimeIntentLog 单一 effect ledger 的生产 Repository 状态合同。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from src.app.runtime.orchestration.repositories.runtime_intent_log_repository import RuntimeIntentLogRepository
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.runtime.orchestration.system_capability_effect_claim import SystemCapabilityClaimResult


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
        "plugin_key": "rough_sorter",
        "plugin_contract_version": "v1",
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


@pytest.mark.asyncio
async def test_production_repository_preserves_rejected_terminal_on_same_claim(db_session) -> None:
    repository = RuntimeIntentLogRepository()
    claim = _claim()

    assert await repository.claim_or_match(db_session, **claim) is SystemCapabilityClaimResult.NEW
    await repository.record_outcome(db_session, claim=claim, evidence=_evidence("business_reject", occurred_at_ms=1100))
    assert await repository.claim_or_match(db_session, **claim) is SystemCapabilityClaimResult.MATCH

    persisted = await repository.get_success_evidence(db_session, claim=claim)
    assert persisted is None
    row = (
        await db_session.execute(select(RuntimeIntentLog).where(RuntimeIntentLog.idempotency_key == "effect-key-1"))
    ).scalar_one()
    assert row.effect_status is RuntimeIntentStatus.REJECTED
    assert [item["outcome_kind"] for item in row.outcome_history_json] == ["business_reject"]


@pytest.mark.asyncio
async def test_production_repository_claim_is_rolled_back_with_outer_transaction(db_session) -> None:
    repository = RuntimeIntentLogRepository()
    claim = _claim()

    assert await repository.claim_or_match(db_session, **claim) is SystemCapabilityClaimResult.NEW
    row = await repository.get_claimed_intent(db_session, claim=claim)
    assert row is not None
    assert row.plugin_key == "rough_sorter"
    assert row.plugin_contract_version == "v1"
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
