"""ObjectTransitionEvent 统一 transition 事件合同测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select

from src.app.workline.models import (
    ObjectTransitionDomain,
    ObjectTransitionEvent,
)
from src.app.workline.repositories import ObjectTransitionEventRepository
from src.app.workline.services import ObjectTransitionEventService
from src.database.sqlite_schema import configure_sqlite_schemas


@pytest_asyncio.fixture(scope="function")
async def transition_session():
    """独立内存 DB，只建 object_transition_events 表。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
    )
    configure_sqlite_schemas(engine.sync_engine)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=[ObjectTransitionEvent.__table__])
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all, tables=[ObjectTransitionEvent.__table__])
    await engine.dispose()


def test_object_transition_event_declares_expected_indexes() -> None:
    """模型层声明 T3 约定的幂等与查询索引。"""
    actual_indexes = {
        (index.name, tuple(column.name for column in index.columns), bool(index.unique))
        for index in ObjectTransitionEvent.__table__.indexes
    }

    assert (
        "uq_object_transition_events_idempotency_key_not_null",
        ("idempotency_key",),
        True,
    ) in actual_indexes
    assert (
        "ix_object_transition_events_trace_occurred",
        ("trace_id", "occurred_at"),
        False,
    ) in actual_indexes
    assert (
        "ix_object_transition_events_session_occurred",
        ("workline_session_id", "occurred_at"),
        False,
    ) in actual_indexes
    assert (
        "ix_object_transition_events_object_occurred",
        ("domain", "object_type", "object_key", "occurred_at"),
        False,
    ) in actual_indexes
    assert (
        "ix_object_transition_events_domain_source",
        ("domain", "source_event_id"),
        False,
    ) in actual_indexes

    partial_unique = next(
        index
        for index in ObjectTransitionEvent.__table__.indexes
        if index.name == "uq_object_transition_events_idempotency_key_not_null"
    )
    assert str(partial_unique.dialect_options["postgresql"]["where"]) == "idempotency_key IS NOT NULL"
    assert str(partial_unique.dialect_options["sqlite"]["where"]) == "idempotency_key IS NOT NULL"


def test_object_transition_event_json_columns_match_migration_contract() -> None:
    """模型 metadata 与迁移保持一致，避免后续 autogenerate 漂移。"""
    columns = ObjectTransitionEvent.__table__.c

    assert columns.source_ref_json.nullable is False
    assert columns.evidence_json.nullable is False
    assert columns.source_ref_json.default is not None
    assert columns.evidence_json.default is not None


def test_build_idempotency_key_uses_derived_transition_grain() -> None:
    """同一 source_event_id 派生不同 to_state/reason_code 时 key 必须不同。"""
    service = ObjectTransitionEventService(repository=ObjectTransitionEventRepository())

    stored_key = service.build_idempotency_key(
        source_event_id="fact-001",
        domain=ObjectTransitionDomain.RESOURCE,
        object_type="MATERIAL_UNIT",
        object_key="PKG-001",
        projection_type="STATUS",
        to_state="STORED",
        reason_code="ARRIVED",
    )
    queue_key = service.build_idempotency_key(
        source_event_id="fact-001",
        domain=ObjectTransitionDomain.RESOURCE,
        object_type="MATERIAL_UNIT",
        object_key="PKG-001",
        projection_type="QUEUE",
        to_state="WAITING",
        reason_code="RESOURCE_WAIT",
    )

    assert stored_key != queue_key
    assert "fact-001" in stored_key
    assert "RESOURCE" in stored_key
    assert "MATERIAL_UNIT" in stored_key
    assert "PKG-001" in stored_key
    assert "STATUS" in stored_key
    assert "STORED" in stored_key
    assert "ARRIVED" in stored_key


@pytest.mark.asyncio
async def test_record_transition_replay_returns_existing_event(transition_session: AsyncSession) -> None:
    """同一派生幂等键重放不得重复插入 transition。"""
    service = ObjectTransitionEventService(repository=ObjectTransitionEventRepository())

    first = await service.record_transition(
        transition_session,
        domain=ObjectTransitionDomain.RESOURCE,
        object_type="MATERIAL_UNIT",
        object_key="PKG-001",
        projection_type="STATUS",
        from_state="IN_TRANSIT",
        to_state="STORED",
        reason_code="ARRIVED",
        source_event_id="fact-001",
        source_ref_json={"inbox_id": 11},
        evidence_json={"cell": "A01"},
        workline_session_id=1001,
        trace_id="trace-001",
        auto_commit=True,
    )
    replay = await service.record_transition(
        transition_session,
        domain=ObjectTransitionDomain.RESOURCE,
        object_type="MATERIAL_UNIT",
        object_key="PKG-001",
        projection_type="STATUS",
        from_state="IN_TRANSIT",
        to_state="STORED",
        reason_code="ARRIVED",
        source_event_id="fact-001",
        source_ref_json={"inbox_id": 11},
        evidence_json={"cell": "A01"},
        workline_session_id=1001,
        trace_id="trace-001",
        auto_commit=True,
    )

    result = await transition_session.execute(select(ObjectTransitionEvent))
    events = result.scalars().all()
    assert len(events) == 1
    assert first.id == replay.id
    assert replay.idempotency_key == first.idempotency_key


@pytest.mark.asyncio
async def test_same_source_event_can_keep_sibling_transitions(transition_session: AsyncSession) -> None:
    """同一原始 fact 派生出的兄弟 transition 必须能同时保留。"""
    service = ObjectTransitionEventService(repository=ObjectTransitionEventRepository())

    status_event = await service.record_transition(
        transition_session,
        domain=ObjectTransitionDomain.RESOURCE,
        object_type="MATERIAL_UNIT",
        object_key="PKG-001",
        projection_type="STATUS",
        from_state="IN_TRANSIT",
        to_state="STORED",
        reason_code="ARRIVED",
        source_event_id="fact-001",
        auto_commit=True,
    )
    queue_event = await service.record_transition(
        transition_session,
        domain=ObjectTransitionDomain.RESOURCE,
        object_type="MATERIAL_UNIT",
        object_key="PKG-001",
        projection_type="QUEUE",
        from_state=None,
        to_state="WAITING",
        reason_code="RESOURCE_WAIT",
        source_event_id="fact-001",
        auto_commit=True,
    )

    siblings = await service.get_by_source_event(
        transition_session,
        domain=ObjectTransitionDomain.RESOURCE,
        source_event_id="fact-001",
    )

    assert status_event.id != queue_event.id
    assert {event.projection_type for event in siblings} == {"STATUS", "QUEUE"}
    assert {event.source_event_id for event in siblings} == {"fact-001"}


def test_object_transition_exports_are_declared() -> None:
    """workline model/repository/service 必须从对应 __init__ 导出。"""
    from src.app.workline import models, repositories, services

    for name in (
        "ObjectTransitionDomain",
        "ObjectTransitionEvent",
        "ObjectTransitionEventBase",
        "ObjectTransitionEventCreate",
        "ObjectTransitionEventResponse",
    ):
        assert hasattr(models, name)
        assert name in models.__all__

    for name in ("ObjectTransitionEventRepository", "object_transition_event_repository"):
        assert hasattr(repositories, name)
        assert name in repositories.__all__

    for name in ("ObjectTransitionEventService", "object_transition_event_service"):
        assert hasattr(services, name)
        assert name in services.__all__


def test_object_transition_migration_declares_index_contract() -> None:
    """迁移源码必须声明 T3 约定的 partial unique 与组合索引。"""
    migration_files = sorted(Path("migrations/versions").glob("*object_transition_events*.py"))
    assert migration_files, "缺少 object_transition_events migration"

    source = migration_files[-1].read_text(encoding="utf-8")
    normalized = " ".join(source.split())

    assert "CREATE TABLE IF NOT EXISTS wes_biz.object_transition_events" in source
    assert "uq_object_transition_events_idempotency_key_not_null" in source
    assert "WHERE idempotency_key IS NOT NULL" in source
    assert "trace_id, occurred_at" in normalized
    assert "workline_session_id, occurred_at" in normalized
    assert "domain, object_type, object_key, occurred_at" in normalized
    assert "domain, source_event_id" in normalized


@pytest.mark.asyncio
async def test_repository_query_methods_follow_index_contract(transition_session: AsyncSession) -> None:
    """Repository 提供按 trace/session/object/source 的查询能力。"""
    service = ObjectTransitionEventService(repository=ObjectTransitionEventRepository())
    event = await service.record_transition(
        transition_session,
        domain=ObjectTransitionDomain.HANDLING,
        object_type="QUEUE_MEMBERSHIP",
        object_key="Q-001",
        projection_type="MEMBERSHIP",
        from_state="PENDING",
        to_state="ACTIVE",
        reason_code="CLAIMED",
        source_event_id="fact-queue-001",
        workline_session_id=2001,
        trace_id="trace-queue-001",
        auto_commit=True,
    )

    repository = ObjectTransitionEventRepository()

    by_trace = await repository.list_by_trace_id(transition_session, "trace-queue-001")
    by_session = await repository.list_by_workline_session_id(transition_session, 2001)
    by_object = await repository.list_by_object(
        transition_session,
        domain=ObjectTransitionDomain.HANDLING,
        object_type="QUEUE_MEMBERSHIP",
        object_key="Q-001",
    )
    by_source = await repository.list_by_source_event(
        transition_session,
        domain=ObjectTransitionDomain.HANDLING,
        source_event_id="fact-queue-001",
    )
    scalar = await transition_session.execute(text("SELECT COUNT(*) FROM wes_biz.object_transition_events"))

    assert scalar.scalar_one() == 1
    assert [item.id for item in by_trace] == [event.id]
    assert [item.id for item in by_session] == [event.id]
    assert [item.id for item in by_object] == [event.id]
    assert [item.id for item in by_source] == [event.id]
