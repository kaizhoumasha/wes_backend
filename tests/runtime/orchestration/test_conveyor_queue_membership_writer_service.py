"""ConveyorQueueMembership DB-backed writer service tests."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from src.app.runtime.orchestration.conveyor_queue_membership import ConveyorQueueMembership
from src.app.runtime.orchestration.models.object_transition_event import ObjectTransitionEvent
from src.app.runtime.orchestration.repositories.conveyor_queue_membership_repository import (
    ConveyorQueueMembershipRepository,
)
from src.app.runtime.orchestration.services.conveyor_queue_writer import ConveyorQueueWriteDecisionKind


class _ConveyorQueueUniqueRaceRepository(ConveyorQueueMembershipRepository):
    """模拟查询后并发插入导致唯一索引冲突的 repository。"""

    def __init__(self) -> None:
        super().__init__()
        self.list_count = 0

    async def list_active_by_identity(self, db, *, workline_id, bin_code=None, placeholder_key=None, for_update=False):
        self.list_count += 1
        if self.list_count == 1:
            return []
        return await super().list_active_by_identity(
            db,
            workline_id=workline_id,
            bin_code=bin_code,
            placeholder_key=placeholder_key,
            for_update=for_update,
        )

    async def create_without_session_rollback(self, *_args, **_kwargs) -> ConveyorQueueMembership:
        raise IntegrityError("INSERT INTO conveyor_queue_memberships", {}, Exception("unique active bin"))


class _ConveyorQueueResolveUniqueRaceRepository(ConveyorQueueMembershipRepository):
    """模拟 placeholder resolve 前读不到并发写入的 ACTIVE bin。"""

    def __init__(self) -> None:
        super().__init__()
        self.bin_lookup_count = 0

    async def list_active_by_identity(self, db, *, workline_id, bin_code=None, placeholder_key=None, for_update=False):
        active = await self.get_active_by_placeholder_key(
            db,
            workline_id=workline_id,
            placeholder_key=placeholder_key,
        )
        return [active] if active is not None else []

    async def get_active_by_bin_code(self, db, *, workline_id: int, bin_code: str, for_update: bool = False):
        self.bin_lookup_count += 1
        if self.bin_lookup_count == 1:
            return None
        return await super().get_active_by_bin_code(
            db,
            workline_id=workline_id,
            bin_code=bin_code,
        )


class _ConveyorQueueCreateResolveUniqueRaceRepository(ConveyorQueueMembershipRepository):
    """模拟 create 前读不到 bin/placeholder，插入冲突后才能读到两种 ACTIVE 身份。"""

    def __init__(self) -> None:
        super().__init__()
        self.list_count = 0

    async def list_active_by_identity(self, db, *, workline_id, bin_code=None, placeholder_key=None, for_update=False):
        self.list_count += 1
        if self.list_count == 1:
            return []
        return await super().list_active_by_identity(
            db,
            workline_id=workline_id,
            bin_code=bin_code,
            placeholder_key=placeholder_key,
            for_update=for_update,
        )

    async def create_without_session_rollback(self, *_args, **_kwargs) -> ConveyorQueueMembership:
        raise IntegrityError("INSERT INTO conveyor_queue_memberships", {}, Exception("unique active bin"))


async def _membership_count(db_session) -> int:
    result = await db_session.execute(select(func.count()).select_from(ConveyorQueueMembership))
    return int(result.scalar_one())


async def _transition_events(db_session) -> list[ObjectTransitionEvent]:
    result = await db_session.execute(select(ObjectTransitionEvent).order_by(ObjectTransitionEvent.id.asc()))
    return list(result.scalars().all())


def test_conveyor_queue_membership_repository_builds_postgres_for_update_statement() -> None:
    """Writer 写入前读取 ACTIVE 候选时必须具备 PostgreSQL 行级锁语义。"""

    repository = ConveyorQueueMembershipRepository()

    statement = repository.build_active_identity_select(
        workline_id=1,
        bin_code="BIN-001",
        placeholder_key="scan:001",
        for_update=True,
    )
    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE" in compiled
    assert "conveyor_queue_memberships" in compiled
    assert "membership_status" in compiled


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_creates_active_membership(db_session) -> None:
    """Writer service 必须真实写入 runtime ConveyorQueueMembership active 投影。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    service = ConveyorQueueMembershipWriterService()

    result = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        declared_queue_codes={"Q-IN"},
        correlation_id="corr-queue-001",
        evidence_json={"source_event_id": "evt-create"},
        auto_commit=False,
    )

    assert result.created is True
    assert result.decision.kind == ConveyorQueueWriteDecisionKind.CREATE_ACTIVE
    assert result.membership.id is not None
    assert result.membership.membership_status == "ACTIVE"
    assert result.membership.bin_code == "BIN-001"
    assert result.membership.queue_code == "Q-IN"
    assert result.membership.correlation_id == "corr-queue-001"
    assert result.membership.evidence_json["source_event_id"] == "evt-create"
    assert await _membership_count(db_session) == 1


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_reuses_same_active_membership(db_session) -> None:
    """同 workline、同 bin、同 queue 重放必须复用 active membership。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    service = ConveyorQueueMembershipWriterService()
    first = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        declared_queue_codes={"Q-IN"},
        evidence_json={"source_event_id": "evt-first"},
        auto_commit=False,
    )

    replay = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        declared_queue_codes={"Q-IN"},
        evidence_json={"source_event_id": "evt-replay"},
        auto_commit=False,
    )

    assert replay.created is False
    assert replay.decision.kind == ConveyorQueueWriteDecisionKind.IDEMPOTENT_REPLAY
    assert replay.membership.id == first.membership.id
    assert await _membership_count(db_session) == 1


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_rereads_existing_after_unique_conflict(db_session) -> None:
    """并发插入撞 ACTIVE 唯一约束时，必须重读 existing 并返回非 created。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    existing = ConveyorQueueMembership(
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        membership_status="ACTIVE",
        entered_at=1700000000000,
    )
    db_session.add(existing)
    await db_session.flush()
    assert existing.id is not None

    service = ConveyorQueueMembershipWriterService(repository=_ConveyorQueueUniqueRaceRepository())

    result = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        declared_queue_codes={"Q-IN"},
        evidence_json={"source_event_id": "evt-race"},
        auto_commit=False,
    )

    assert result.created is False
    assert result.membership.id == existing.id
    assert await _membership_count(db_session) == 1


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_reports_integrity_conflict_diagnostics(db_session) -> None:
    """唯一约束冲突重读必须在结果中暴露可观测诊断。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    existing = ConveyorQueueMembership(
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        membership_status="ACTIVE",
        entered_at=1700000000000,
    )
    db_session.add(existing)
    await db_session.flush()

    service = ConveyorQueueMembershipWriterService(repository=_ConveyorQueueUniqueRaceRepository())

    result = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        declared_queue_codes={"Q-IN"},
        evidence_json={"source_event_id": "evt-race-diagnostics"},
        auto_commit=False,
    )

    assert result.decision.kind == ConveyorQueueWriteDecisionKind.IDEMPOTENT_REPLAY
    assert result.diagnostics.decision_kind == "IDEMPOTENT_REPLAY"
    assert result.diagnostics.decision_reason == "ACTIVE_BIN_ALREADY_IN_QUEUE"
    assert result.diagnostics.created is False
    assert result.diagnostics.reused_existing_after_integrity_conflict is True
    assert result.diagnostics.runtime_hold_required is False
    assert result.diagnostics.reconciliation_required is False
    assert result.diagnostics.membership_status == "ACTIVE"
    assert result.membership.id == existing.id
    assert await _membership_count(db_session) == 1


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_rechecks_queue_after_unique_conflict(db_session) -> None:
    """唯一冲突重读到跨 queue ACTIVE bin 时，必须重新判定并转 RECONCILING。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    existing = ConveyorQueueMembership(
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        membership_status="ACTIVE",
        entered_at=1700000000000,
    )
    db_session.add(existing)
    await db_session.flush()

    service = ConveyorQueueMembershipWriterService(repository=_ConveyorQueueUniqueRaceRepository())

    result = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-OUT",
        queue_role="EXIT_SCAN",
        bin_code="BIN-001",
        declared_queue_codes={"Q-IN", "Q-OUT"},
        evidence_json={"source_event_id": "evt-cross-queue-race"},
        auto_commit=False,
    )

    assert result.created is False
    assert result.decision.kind == ConveyorQueueWriteDecisionKind.RECONCILING
    assert result.diagnostics.reused_existing_after_integrity_conflict is True
    assert result.diagnostics.reconciliation_required is True
    assert result.membership.id == existing.id
    assert result.membership.membership_status == "RECONCILING"
    assert result.membership.evidence_json["existing_queue_code"] == "Q-IN"
    assert result.membership.evidence_json["conflicting_queue_code"] == "Q-OUT"
    assert await _membership_count(db_session) == 1


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_resolves_placeholder_in_place(db_session) -> None:
    """placeholder resolve 必须原地绑定真实 bin，不额外创建第二条 active。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    service = ConveyorQueueMembershipWriterService()
    placeholder = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        placeholder_key="scan:001",
        declared_queue_codes={"Q-IN"},
        evidence_json={"source_event_id": "evt-placeholder"},
        auto_commit=False,
    )

    resolved = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        placeholder_key="scan:001",
        declared_queue_codes={"Q-IN"},
        evidence_json={"source_event_id": "evt-resolve"},
        auto_commit=False,
    )

    assert resolved.created is False
    assert resolved.decision.kind == ConveyorQueueWriteDecisionKind.RESOLVE_PLACEHOLDER
    assert resolved.membership.id == placeholder.membership.id
    assert resolved.membership.bin_code == "BIN-001"
    assert resolved.membership.placeholder_key is None
    assert resolved.membership.evidence_json["resolved_from_placeholder_key"] == "scan:001"
    assert resolved.membership.evidence_json["source_event_id"] == "evt-resolve"
    assert await _membership_count(db_session) == 1


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_marks_placeholder_reconciling_after_resolve_unique_conflict(
    db_session,
) -> None:
    """placeholder resolve 撞 ACTIVE bin 唯一约束时必须隔离冲突并转 RECONCILING。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    existing_bin = ConveyorQueueMembership(
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        membership_status="ACTIVE",
        entered_at=1700000000000,
        evidence_json={"resolved_from_placeholder_key": "scan:other"},
    )
    placeholder = ConveyorQueueMembership(
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        placeholder_key="scan:001",
        membership_status="ACTIVE",
        entered_at=1700000000001,
    )
    db_session.add_all([existing_bin, placeholder])
    await db_session.flush()

    service = ConveyorQueueMembershipWriterService(repository=_ConveyorQueueResolveUniqueRaceRepository())
    result = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        placeholder_key="scan:001",
        declared_queue_codes={"Q-IN"},
        evidence_json={"source_event_id": "evt-resolve-race"},
        auto_commit=False,
    )

    assert result.created is False
    assert result.decision.kind == ConveyorQueueWriteDecisionKind.RECONCILING
    assert result.diagnostics.reconciliation_required is True
    assert result.diagnostics.runtime_hold_required is True
    assert result.membership.id == placeholder.id
    assert result.membership.membership_status == "RECONCILING"
    assert result.membership.bin_code is None
    assert result.membership.placeholder_key == "scan:001"
    assert result.membership.evidence_json["conflicting_bin_code"] == "BIN-001"
    assert result.membership.evidence_json["conflicting_membership_id"] == existing_bin.id
    assert await _membership_count(db_session) == 2


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_prioritizes_placeholder_resolve_when_bin_already_active(
    db_session,
) -> None:
    """bin 与 placeholder 同时 ACTIVE 时，resolve 重放不能静默跳过 placeholder。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    existing_bin = ConveyorQueueMembership(
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        membership_status="ACTIVE",
        entered_at=1700000000000,
    )
    placeholder = ConveyorQueueMembership(
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        placeholder_key="scan:001",
        membership_status="ACTIVE",
        entered_at=1700000000001,
    )
    db_session.add_all([existing_bin, placeholder])
    await db_session.flush()

    service = ConveyorQueueMembershipWriterService()
    result = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        placeholder_key="scan:001",
        declared_queue_codes={"Q-IN"},
        evidence_json={"source_event_id": "evt-resolve-replay"},
        auto_commit=False,
    )

    assert result.created is False
    assert result.decision.kind == ConveyorQueueWriteDecisionKind.RECONCILING
    assert result.diagnostics.reconciliation_required is True
    assert result.diagnostics.runtime_hold_required is True
    assert result.membership.id == placeholder.id
    assert result.membership.membership_status == "RECONCILING"
    assert result.membership.placeholder_key == "scan:001"
    assert result.membership.evidence_json["conflicting_bin_code"] == "BIN-001"
    assert result.membership.evidence_json["conflicting_membership_id"] == existing_bin.id
    assert await _membership_count(db_session) == 2


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_rechecks_all_identities_after_resolve_create_conflict(
    db_session,
) -> None:
    """resolve 请求 create 撞唯一约束后必须重读 bin + placeholder 两种 ACTIVE 身份。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    existing_bin = ConveyorQueueMembership(
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        membership_status="ACTIVE",
        entered_at=1700000000000,
    )
    placeholder = ConveyorQueueMembership(
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        placeholder_key="scan:001",
        membership_status="ACTIVE",
        entered_at=1700000000001,
    )
    db_session.add_all([existing_bin, placeholder])
    await db_session.flush()

    service = ConveyorQueueMembershipWriterService(repository=_ConveyorQueueCreateResolveUniqueRaceRepository())
    result = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        placeholder_key="scan:001",
        declared_queue_codes={"Q-IN"},
        evidence_json={"source_event_id": "evt-create-resolve-race"},
        auto_commit=False,
    )

    assert result.created is False
    assert result.decision.kind == ConveyorQueueWriteDecisionKind.RECONCILING
    assert result.diagnostics.reused_existing_after_integrity_conflict is True
    assert result.diagnostics.reconciliation_required is True
    assert result.diagnostics.runtime_hold_required is True
    assert result.membership.id == placeholder.id
    assert result.membership.membership_status == "RECONCILING"
    assert result.membership.placeholder_key == "scan:001"
    assert result.membership.evidence_json["conflicting_bin_code"] == "BIN-001"
    assert result.membership.evidence_json["conflicting_membership_id"] == existing_bin.id
    assert await _membership_count(db_session) == 2


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_marks_placeholder_not_bin_on_placeholder_queue_conflict(
    db_session,
) -> None:
    """bin 已在请求队列但 placeholder 在其它队列时，必须标记 placeholder 冲突。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    existing_bin = ConveyorQueueMembership(
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        membership_status="ACTIVE",
        entered_at=1700000000000,
    )
    placeholder = ConveyorQueueMembership(
        workline_id=1,
        conveyor_code="CV-02",
        queue_code="Q-OUT",
        queue_role="EXIT_SCAN",
        placeholder_key="scan:001",
        membership_status="ACTIVE",
        entered_at=1700000000001,
    )
    db_session.add_all([existing_bin, placeholder])
    await db_session.flush()

    service = ConveyorQueueMembershipWriterService()
    result = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-001",
        placeholder_key="scan:001",
        declared_queue_codes={"Q-IN", "Q-OUT"},
        evidence_json={"source_event_id": "evt-placeholder-queue-conflict"},
        auto_commit=False,
    )

    await db_session.refresh(existing_bin)
    await db_session.refresh(placeholder)

    assert result.created is False
    assert result.decision.kind == ConveyorQueueWriteDecisionKind.RECONCILING
    assert result.decision.reason == "ACTIVE_PLACEHOLDER_QUEUE_CONFLICT"
    assert result.membership.id == placeholder.id
    assert existing_bin.membership_status == "ACTIVE"
    assert placeholder.membership_status == "RECONCILING"
    assert placeholder.evidence_json["existing_queue_code"] == "Q-OUT"
    assert placeholder.evidence_json["conflicting_queue_code"] == "Q-IN"
    assert await _membership_count(db_session) == 2


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_replays_placeholder_without_bin_idempotently(db_session) -> None:
    """真实 bin 到来前的 placeholder 重放必须复用 active 身份。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    service = ConveyorQueueMembershipWriterService()
    first = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        placeholder_key="scan:001",
        declared_queue_codes={"Q-IN"},
        evidence_json={"source_event_id": "evt-placeholder"},
        auto_commit=False,
    )

    replay = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        placeholder_key="scan:001",
        declared_queue_codes={"Q-IN"},
        evidence_json={"source_event_id": "evt-placeholder-replay"},
        auto_commit=False,
    )

    assert replay.created is False
    assert replay.decision.kind == ConveyorQueueWriteDecisionKind.IDEMPOTENT_REPLAY
    assert replay.membership.id == first.membership.id
    assert replay.membership.bin_code is None
    assert replay.membership.placeholder_key == "scan:001"
    assert replay.membership.queue_code == "Q-IN"
    assert await _membership_count(db_session) == 1


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_marks_conflict_reconciling(db_session) -> None:
    """同 bin 跨 queue 冲突必须标记 RECONCILING，不能静默切换 active 队列。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    service = ConveyorQueueMembershipWriterService()
    active = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-002",
        declared_queue_codes={"Q-IN", "Q-OUT"},
        evidence_json={"source_event_id": "evt-active"},
        auto_commit=False,
    )

    conflict = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-OUT",
        queue_role="EXIT_SCAN",
        bin_code="BIN-002",
        declared_queue_codes={"Q-IN", "Q-OUT"},
        evidence_json={"source_event_id": "evt-conflict"},
        auto_commit=False,
    )

    assert conflict.created is False
    assert conflict.decision.kind == ConveyorQueueWriteDecisionKind.RECONCILING
    assert conflict.membership.id == active.membership.id
    assert conflict.membership.membership_status == "RECONCILING"
    assert conflict.membership.queue_code == "Q-IN"
    assert conflict.membership.evidence_json["existing_queue_code"] == "Q-IN"
    assert conflict.membership.evidence_json["conflicting_queue_code"] == "Q-OUT"
    assert await _membership_count(db_session) == 1


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_blocks_unknown_queue_without_write(db_session) -> None:
    """strict mode 下未知 manifest queue 必须阻断写入并暴露 policy decision。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
        ConveyorQueueWriteBlocked,
    )

    service = ConveyorQueueMembershipWriterService()

    with pytest.raises(ConveyorQueueWriteBlocked) as exc_info:
        await service.write_active(
            db_session,
            workline_id=1,
            conveyor_code="CV-01",
            queue_code="Q-UNKNOWN",
            queue_role="ENTRY_SCAN",
            bin_code="BIN-003",
            declared_queue_codes={"Q-IN"},
            evidence_json={"source_event_id": "evt-blocked"},
            auto_commit=False,
        )

    assert exc_info.value.decision.kind == ConveyorQueueWriteDecisionKind.BLOCKED
    assert exc_info.value.decision.reason == "UNKNOWN_QUEUE_CODE"
    assert await _membership_count(db_session) == 0


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_requires_source_event_id(db_session) -> None:
    """新 writer 写路径必须带可追溯 source_event_id。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    service = ConveyorQueueMembershipWriterService()

    with pytest.raises(ValueError, match="source_event_id"):
        await service.write_active(
            db_session,
            workline_id=1,
            conveyor_code="CV-01",
            queue_code="Q-IN",
            queue_role="ENTRY_SCAN",
            bin_code="BIN-004",
            declared_queue_codes={"Q-IN"},
            evidence_json={},
            auto_commit=False,
        )

    with pytest.raises(ValueError, match="source_event_id"):
        await service.write_active(
            db_session,
            workline_id=1,
            conveyor_code="CV-01",
            queue_code="Q-IN",
            queue_role="ENTRY_SCAN",
            bin_code="BIN-004",
            declared_queue_codes={"Q-IN"},
            source_event_id=" ",
            evidence_json={"source_event_id": "evt-should-not-hide-blank"},
            auto_commit=False,
        )


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_records_transition_for_non_idempotent_write(db_session) -> None:
    """非幂等 queue 写入必须记录统一 ObjectTransitionEvent。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    service = ConveyorQueueMembershipWriterService()

    first = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-005",
        declared_queue_codes={"Q-IN"},
        source_event_id="evt-create-transition",
        evidence_json={"operator": "runtime-writer"},
        auto_commit=False,
    )
    replay = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-005",
        declared_queue_codes={"Q-IN"},
        source_event_id="evt-create-transition-replay",
        evidence_json={"operator": "runtime-writer"},
        auto_commit=False,
    )

    events = await _transition_events(db_session)
    assert replay.created is False
    assert len(events) == 1
    assert events[0].domain == "HANDLING"
    assert events[0].object_type == "CONVEYOR_QUEUE_MEMBERSHIP"
    assert events[0].object_key == "BIN-005"
    assert events[0].projection_type == "QUEUE_MEMBERSHIP"
    assert events[0].from_state is None
    assert events[0].to_state == "Q-IN"
    assert events[0].reason_code == "CREATE_NEW_ACTIVE_MEMBERSHIP"
    assert events[0].source_event_id == "evt-create-transition"
    assert events[0].source_ref_json["workline_id"] == 1
    assert events[0].source_ref_json["membership_id"] == first.membership.id
    assert events[0].source_ref_json["queue_code"] == "Q-IN"
    assert events[0].evidence_json["source_event_id"] == "evt-create-transition"


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_closes_active_membership_and_records_transition(db_session) -> None:
    """terminal adapter 必须把 ACTIVE 标记为 LEFT 并记录迁移事件。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    service = ConveyorQueueMembershipWriterService()
    active = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-006",
        declared_queue_codes={"Q-IN"},
        source_event_id="evt-close-active",
        evidence_json={},
        auto_commit=False,
    )

    closed = await service.close_active(
        db_session,
        workline_id=1,
        bin_code="BIN-006",
        reason_code="QUEUE_LEFT",
        source_event_id="evt-close-left",
        evidence_json={"step": "terminal"},
        auto_commit=False,
    )
    replay = await service.close_active(
        db_session,
        workline_id=1,
        bin_code="BIN-006",
        reason_code="QUEUE_LEFT",
        source_event_id="evt-close-left",
        auto_commit=False,
        ignore_missing=True,
    )

    events = await _transition_events(db_session)
    assert closed is not None
    assert replay is None
    assert closed.id == active.membership.id
    assert closed.membership_status == "LEFT"
    assert closed.left_at is not None
    assert closed.evidence_json["source_event_id"] == "evt-close-left"
    assert len(events) == 2
    assert events[-1].object_key == "BIN-006"
    assert events[-1].from_state == "Q-IN"
    assert events[-1].to_state == "LEFT"
    assert events[-1].reason_code == "QUEUE_LEFT"
    assert events[-1].source_event_id == "evt-close-left"
    assert events[-1].source_ref_json["membership_id"] == active.membership.id


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_marks_identity_reconciling_and_records_transition(db_session) -> None:
    """reconciling adapter 必须更新 ACTIVE membership 并保留统一迁移事件。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    service = ConveyorQueueMembershipWriterService()
    active = await service.write_active(
        db_session,
        workline_id=1,
        conveyor_code="CV-01",
        queue_code="Q-IN",
        queue_role="ENTRY_SCAN",
        bin_code="BIN-007",
        declared_queue_codes={"Q-IN"},
        source_event_id="evt-recon-active",
        evidence_json={},
        auto_commit=False,
    )

    reconciling = await service.mark_reconciling_for_identity(
        db_session,
        workline_id=1,
        bin_code="BIN-007",
        reason_code="QUEUE_MEMBERSHIP_RECONCILING",
        source_event_id="evt-reconciling",
        evidence_json={"issue": "manual-check"},
        auto_commit=False,
    )

    events = await _transition_events(db_session)
    assert reconciling is not None
    assert reconciling.id == active.membership.id
    assert reconciling.membership_status == "RECONCILING"
    assert reconciling.evidence_json["issue"] == "manual-check"
    assert len(events) == 2
    assert events[-1].object_key == "BIN-007"
    assert events[-1].from_state == "Q-IN"
    assert events[-1].to_state == "RECONCILING"
    assert events[-1].reason_code == "QUEUE_MEMBERSHIP_RECONCILING"
    assert events[-1].source_event_id == "evt-reconciling"


@pytest.mark.asyncio
async def test_conveyor_queue_membership_writer_terminal_adapters_validate_source_event_id(db_session) -> None:
    """terminal/reconciling 公共入口不能接受缺失或空白 source_event_id。"""

    from src.app.runtime.orchestration.services.conveyor_queue_membership_writer_service import (
        ConveyorQueueMembershipWriterService,
    )

    service = ConveyorQueueMembershipWriterService()

    with pytest.raises(ValueError, match="source_event_id"):
        await service.close_active(
            db_session,
            workline_id=1,
            bin_code="BIN-008",
            reason_code="QUEUE_LEFT",
            evidence_json={},
            auto_commit=False,
            ignore_missing=True,
        )

    with pytest.raises(ValueError, match="source_event_id"):
        await service.mark_reconciling_for_identity(
            db_session,
            workline_id=1,
            bin_code="BIN-008",
            reason_code="QUEUE_MEMBERSHIP_RECONCILING",
            source_event_id=" ",
            evidence_json={"source_event_id": "evt-should-not-hide-blank"},
            auto_commit=False,
            ignore_missing=True,
        )
