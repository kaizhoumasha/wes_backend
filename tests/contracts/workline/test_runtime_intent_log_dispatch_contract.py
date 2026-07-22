"""BC-XX 幂等 claim/replay 行为契约。

验收: request_hash 不一致时拒绝 dispatch (主计划 §5.4 H5)。
mock 仅允许 `src/app/runtime/orchestration/` 内的 skeleton 实体。
"""

from __future__ import annotations

import pytest

from src.app.runtime.orchestration.idempotency_key import IdempotencyKey
from src.app.runtime.orchestration.services.idempotency_guard import (
    ClaimResult,
    IdempotencyConflict,
    IdempotencyGuard,
    is_wes_internal_key,
    make_wes_internal_key,
)


def _guard_with_repo(repo):
    return IdempotencyGuard(repository=repo)


class _FakeRepo:
    """最小 IdempotencyKeyRepository 替身 — 仅实现 claim_if_absent + get_by_identity。"""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], IdempotencyKey] = {}

    async def claim_if_absent(
        self,
        db,
        *,
        provider_code,
        operation_kind,
        idempotency_key,
        request_hash,
        execution_correlation_id,
        now_ms,
        business_owner_key=None,
    ):
        key = (provider_code, operation_kind, idempotency_key)
        if key in self._rows:
            return False
        self._rows[key] = IdempotencyKey(
            provider_code=provider_code,
            operation_kind=operation_kind,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            execution_correlation_id=execution_correlation_id,
            created_at=now_ms,
            business_owner_key=business_owner_key,
        )
        return True

    async def get_by_identity(self, db, *, provider_code, operation_kind, idempotency_key):
        return self._rows.get((provider_code, operation_kind, idempotency_key))


class _NoAuto:
    """最小 no_autoflush context manager 替身。"""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    """仅实现 no_autoflush 属性 — 返回可复用 context manager。"""

    def __init__(self) -> None:
        self._no_autoflush = _NoAuto()

    @property
    def no_autoflush(self):
        return self._no_autoflush


@pytest.mark.asyncio
async def test_first_claim_returns_new_and_persists():
    """happy path: 首次 claim 返回 NEW, IdempotencyKey 写入。"""
    repo = _FakeRepo()
    guard = _guard_with_repo(repo)
    db = _FakeSession()

    result = await guard.claim_or_match(
        db,
        provider_code="WMS",
        operation_kind="DISPATCH_COMMAND",
        idempotency_key="WES-DISPATCH_COMMAND-abc123",
        request_hash="hash-001",
        execution_correlation_id="corr-001",
        now_ms=1000,
    )

    assert result == ClaimResult.NEW
    assert len(repo._rows) == 1


@pytest.mark.asyncio
async def test_same_key_same_hash_returns_match():
    """replay path: 同 key 同 hash 安全跳过, 返回 MATCH。"""
    repo = _FakeRepo()
    guard = _guard_with_repo(repo)
    db = _FakeSession()

    first = await guard.claim_or_match(
        db,
        provider_code="WMS",
        operation_kind="DISPATCH_COMMAND",
        idempotency_key="WES-DISPATCH_COMMAND-abc123",
        request_hash="hash-001",
        execution_correlation_id="corr-001",
        now_ms=1000,
    )
    second = await guard.claim_or_match(
        db,
        provider_code="WMS",
        operation_kind="DISPATCH_COMMAND",
        idempotency_key="WES-DISPATCH_COMMAND-abc123",
        request_hash="hash-001",
        execution_correlation_id="corr-001",
        now_ms=2000,
    )

    assert first == ClaimResult.NEW
    assert second == ClaimResult.MATCH


@pytest.mark.asyncio
async def test_same_key_different_hash_raises_idempotency_conflict():
    """error path: 同 key 不同 hash 抛 IdempotencyConflict, 调用方必须中止 dispatch。"""
    repo = _FakeRepo()
    guard = _guard_with_repo(repo)
    db = _FakeSession()

    await guard.claim_or_match(
        db,
        provider_code="WMS",
        operation_kind="DISPATCH_COMMAND",
        idempotency_key="WES-DISPATCH_COMMAND-abc123",
        request_hash="hash-001",
        execution_correlation_id="corr-001",
        now_ms=1000,
    )

    with pytest.raises(IdempotencyConflict) as exc_info:
        await guard.claim_or_match(
            db,
            provider_code="WMS",
            operation_kind="DISPATCH_COMMAND",
            idempotency_key="WES-DISPATCH_COMMAND-abc123",
            request_hash="hash-002",
            execution_correlation_id="corr-001",
            now_ms=2000,
        )

    assert exc_info.value.idempotency_key == "WES-DISPATCH_COMMAND-abc123"
    assert exc_info.value.provider_code == "WMS"


def test_make_wes_internal_key_follows_naming_convention():
    """happy path: WES 内部 key 命名符合 `WES-{OPERATION_KIND}-{HASH}` 约定。"""
    key = make_wes_internal_key("DISPATCH_COMMAND", "abc123")
    assert key == "WES-DISPATCH_COMMAND-abc123"
    assert is_wes_internal_key(key)


def test_make_wes_internal_key_normalizes_operation_kind():
    """边界: 小写 + 中划线自动归一为大写下划线。"""
    key = make_wes_internal_key("dispatch-command", "abc123")
    assert key == "WES-DISPATCH_COMMAND-abc123"
    assert is_wes_internal_key(key)


def test_make_wes_internal_key_rejects_invalid_operation_kind():
    """error path: 空 / 非法字符的 operation_kind 拒绝构造。"""
    with pytest.raises(ValueError):
        make_wes_internal_key("", "abc123")

    with pytest.raises(ValueError):
        make_wes_internal_key("dispatch command", "abc123")


def test_make_wes_internal_key_rejects_empty_hash():
    """error path: 空 payload_hash 拒绝构造。"""
    with pytest.raises(ValueError):
        make_wes_internal_key("DISPATCH_COMMAND", "")


def test_is_wes_internal_key_rejects_external_provider_key():
    """error path: 外部 provider 提供的 key (e.g. WMS 回调 source_event_id) 不强制 WES 前缀。"""
    assert not is_wes_internal_key("evt-001-from-wms")
    assert not is_wes_internal_key("WES--empty-op")
    assert not is_wes_internal_key("not-wes-format")
