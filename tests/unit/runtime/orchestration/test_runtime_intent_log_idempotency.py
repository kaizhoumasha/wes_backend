"""H5 IdempotencyGuard 行为测试 (主计划 §5.4)。

SPEC §569 文件清单点名: `tests/unit/runtime/orchestration/test_runtime_intent_log_idempotency.py`。

测试 IdempotencyGuard 最小语义:
- NEW: 首次 claim, 写入 IdempotencyKey 行, 调用方继续 dispatch
- MATCH: 同 (provider, op_kind, key) + 同 request_hash 已存在, 调用方安全跳过 (崩溃重放)
- 同 key 不同 hash → IdempotencyConflict, 调用方中止 dispatch (防双发)

完整 409 + 安全审计留安全审计扩展 (SPEC §586 out-of-scope)。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.idempotency_key import IdempotencyKey
from src.app.runtime.orchestration.repositories.idempotency_key_repository import IdempotencyKeyRepository
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.services.idempotency_guard import (
    ClaimResult,
    IdempotencyConflict,
    IdempotencyGuard,
    is_wes_internal_key,
    make_wes_internal_key,
)
from tests.support.runtime_binding import binding_pin_fields

NOW_MS = 1_700_000_000_000


async def _seed_correlation(db_session, *, correlation_id: str = "corr-h5-001") -> ExecutionCorrelation:
    """建立 ExecutionSession + ExecutionCorrelation, 满足 IdempotencyKey FK 前置。"""
    session = ExecutionSession(
        workline_id=1,
        plugin_key="test-plugin",
        manifest_version="v1",
        **binding_pin_fields(),
        state="RUNNING",
    )
    db_session.add(session)
    await db_session.flush()
    correlation = ExecutionCorrelation(
        correlation_id=correlation_id,
        execution_session_id=session.id,
        trace_id="trace-h5",
    )
    db_session.add(correlation)
    await db_session.flush()
    return correlation


# ---- WES 内部 key 命名约束 ----


def test_wes_internal_key_format_accepts_valid():
    """`WES-{OPERATION_KIND}-{HASH}` 格式 (主计划 §5.4)。"""
    assert is_wes_internal_key("WES-DEVICE_DISPATCH-sha256abc")
    assert is_wes_internal_key("WES-FULFILLMENT-abc123.def")


def test_wes_internal_key_format_rejects_invalid():
    """外部 provider 提供的 key 不命中 WES 内部前缀。"""
    assert not is_wes_internal_key("WMS-EVENT-001")
    assert not is_wes_internal_key("plain-key")
    assert not is_wes_internal_key("wes-device-abc")  # 大小写敏感


def test_make_wes_internal_key_normalizes_operation_kind():
    """make_wes_internal_key 把 op_kind 标准化为大写 + 下划线。"""
    assert make_wes_internal_key("device_dispatch", "sha256abc") == "WES-DEVICE_DISPATCH-sha256abc"
    assert make_wes_internal_key("device-dispatch", "sha256abc") == "WES-DEVICE_DISPATCH-sha256abc"


def test_make_wes_internal_key_rejects_empty_or_invalid():
    """payload_hash 不可空; operation_kind 仅允许字母/数字/下划线。"""
    with pytest.raises(ValueError):
        make_wes_internal_key("device_dispatch", "")
    with pytest.raises(ValueError):
        make_wes_internal_key("device dispatch!", "hash")


# ---- IdempotencyGuard 行为 ----


@pytest.mark.asyncio
async def test_idempotency_guard_first_claim_returns_new_and_persists(db_session):
    """首次 claim 返回 NEW 并落库 IdempotencyKey 行。"""
    correlation = await _seed_correlation(db_session)
    guard = IdempotencyGuard()

    result = await guard.claim_or_match(
        db_session,
        provider_code="WES",
        operation_kind="DEVICE_DISPATCH",
        idempotency_key="WES-DEVICE_DISPATCH-hash001",
        request_hash="sha256-001",
        execution_correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
    )

    assert result is ClaimResult.NEW
    stored = (
        await db_session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.idempotency_key == "WES-DEVICE_DISPATCH-hash001",
            )
        )
    ).scalar_one()
    assert stored.request_hash == "sha256-001"
    assert stored.execution_correlation_id == correlation.correlation_id
    assert stored.created_at == NOW_MS


@pytest.mark.asyncio
async def test_idempotency_guard_same_key_same_hash_returns_match(db_session):
    """同 key 同 hash 返回 MATCH, 不重复插入 (崩溃重放安全)。"""
    correlation = await _seed_correlation(db_session)
    guard = IdempotencyGuard()
    kwargs = {
        "provider_code": "WES",
        "operation_kind": "FULFILLMENT",
        "idempotency_key": "WES-FULFILLMENT-hash002",
        "request_hash": "sha256-002",
        "execution_correlation_id": correlation.correlation_id,
        "now_ms": NOW_MS,
    }

    first = await guard.claim_or_match(db_session, **kwargs)
    second = await guard.claim_or_match(db_session, **kwargs)

    assert first is ClaimResult.NEW
    assert second is ClaimResult.MATCH

    rows = (
        (
            await db_session.execute(
                select(IdempotencyKey).where(
                    IdempotencyKey.idempotency_key == "WES-FULFILLMENT-hash002",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1, "MATCH 路径必须复用既有行, 不能重复插入"


@pytest.mark.asyncio
async def test_idempotency_guard_atomic_insert_conflict_same_hash_returns_match(db_session):
    """DB 唯一键冲突由 atomic insert 处理, 同 hash 必须按 MATCH 返回。"""
    correlation = await _seed_correlation(db_session)
    guard = IdempotencyGuard()
    await db_session.execute(
        IdempotencyKey.__table__.insert().values(
            provider_code="WES",
            operation_kind="FULFILLMENT",
            idempotency_key="WES-FULFILLMENT-race",
            execution_correlation_id=correlation.correlation_id,
            request_hash="sha256-race",
            created_at=NOW_MS,
        )
    )

    result = await guard.claim_or_match(
        db_session,
        provider_code="WES",
        operation_kind="FULFILLMENT",
        idempotency_key="WES-FULFILLMENT-race",
        request_hash="sha256-race",
        execution_correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
    )

    assert result is ClaimResult.MATCH
    rows = (
        (
            await db_session.execute(
                select(IdempotencyKey).where(
                    IdempotencyKey.idempotency_key == "WES-FULFILLMENT-race",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].request_hash == "sha256-race"


@pytest.mark.asyncio
async def test_idempotency_guard_atomic_insert_conflict_different_hash_rejects(db_session):
    """DB 唯一键冲突后若既有行 hash 不同, 仍必须中止 dispatch。"""
    correlation = await _seed_correlation(db_session)
    guard = IdempotencyGuard()
    await db_session.execute(
        IdempotencyKey.__table__.insert().values(
            provider_code="WES",
            operation_kind="FULFILLMENT",
            idempotency_key="WES-FULFILLMENT-race-conflict",
            execution_correlation_id=correlation.correlation_id,
            request_hash="sha256-other-worker",
            created_at=NOW_MS,
        )
    )

    with pytest.raises(IdempotencyConflict) as exc_info:
        await guard.claim_or_match(
            db_session,
            provider_code="WES",
            operation_kind="FULFILLMENT",
            idempotency_key="WES-FULFILLMENT-race-conflict",
            request_hash="sha256-local",
            execution_correlation_id=correlation.correlation_id,
            now_ms=NOW_MS,
        )

    assert exc_info.value.provider_code == "WES"
    assert exc_info.value.operation_kind == "FULFILLMENT"
    assert exc_info.value.idempotency_key == "WES-FULFILLMENT-race-conflict"


@pytest.mark.asyncio
async def test_idempotency_guard_does_not_autoflush_unrelated_pending_rows(db_session):
    """claim 自身不能提前 flush 调用方事务里的无关 pending ORM 对象。"""
    correlation = await _seed_correlation(db_session)
    pending_intent = RuntimeIntentLog(
        execution_session_id=correlation.execution_session_id,
        correlation_id=correlation.correlation_id,
        provider_code="WES",
        target_domain="device",
        target_action="dispatch",
        idempotency_key="WES-DEVICE_DISPATCH-pending",
        request_hash="sha256-pending",
        dispatch_key="WES-DEVICE_DISPATCH-pending",
    )
    db_session.add(pending_intent)

    result = await IdempotencyGuard().claim_or_match(
        db_session,
        provider_code="WES",
        operation_kind="DEVICE_DISPATCH",
        idempotency_key="WES-DEVICE_DISPATCH-no-autoflush",
        request_hash="sha256-no-autoflush",
        execution_correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
    )

    assert result is ClaimResult.NEW
    assert pending_intent.id is None


@pytest.mark.asyncio
async def test_idempotency_key_repository_rejects_unsupported_dialect(db_session, monkeypatch):
    """atomic claim 只支持 PostgreSQL/SQLite, 其它 dialect 必须显式失败。"""
    repository = IdempotencyKeyRepository()
    monkeypatch.setattr(db_session, "get_bind", lambda: SimpleNamespace(dialect=SimpleNamespace(name="mysql")))

    with pytest.raises(NotImplementedError, match="mysql"):
        await repository.claim_if_absent(
            db_session,
            provider_code="WES",
            operation_kind="DEVICE_DISPATCH",
            idempotency_key="WES-DEVICE_DISPATCH-unsupported-dialect",
            request_hash="sha256-dialect",
            execution_correlation_id="corr-dialect",
            now_ms=NOW_MS,
            business_owner_key=None,
        )


@pytest.mark.asyncio
async def test_idempotency_guard_same_key_different_hash_rejects(db_session):
    """同 key 不同 hash 抛 IdempotencyConflict (防 outbound replay 双发)。"""
    correlation = await _seed_correlation(db_session)
    guard = IdempotencyGuard()
    base = {
        "provider_code": "WES",
        "operation_kind": "DEVICE_DISPATCH",
        "idempotency_key": "WES-DEVICE_DISPATCH-hash003",
        "execution_correlation_id": correlation.correlation_id,
        "now_ms": NOW_MS,
    }

    await guard.claim_or_match(db_session, request_hash="sha256-original", **base)

    with pytest.raises(IdempotencyConflict) as exc_info:
        await guard.claim_or_match(db_session, request_hash="sha256-tampered", **base)

    assert exc_info.value.provider_code == "WES"
    assert exc_info.value.operation_kind == "DEVICE_DISPATCH"
    assert exc_info.value.idempotency_key == "WES-DEVICE_DISPATCH-hash003"

    # 行不应被覆盖, 仍是 original hash
    stored = (
        await db_session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.idempotency_key == "WES-DEVICE_DISPATCH-hash003",
            )
        )
    ).scalar_one()
    assert stored.request_hash == "sha256-original"


@pytest.mark.asyncio
async def test_idempotency_guard_distinguishes_provider_and_operation_kind(db_session):
    """不同 (provider, op_kind, key) 组合是独立幂等空间。"""
    correlation = await _seed_correlation(db_session)
    guard = IdempotencyGuard()

    result_wes = await guard.claim_or_match(
        db_session,
        provider_code="WES",
        operation_kind="DEVICE_DISPATCH",
        idempotency_key="key-shared",
        request_hash="hash-a",
        execution_correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
    )
    result_wms = await guard.claim_or_match(
        db_session,
        provider_code="WMS",
        operation_kind="DEVICE_DISPATCH",
        idempotency_key="key-shared",
        request_hash="hash-b",
        execution_correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
    )
    result_other_op = await guard.claim_or_match(
        db_session,
        provider_code="WES",
        operation_kind="FULFILLMENT",
        idempotency_key="key-shared",
        request_hash="hash-c",
        execution_correlation_id=correlation.correlation_id,
        now_ms=NOW_MS,
    )

    assert result_wes is ClaimResult.NEW
    assert result_wms is ClaimResult.NEW
    assert result_other_op is ClaimResult.NEW
