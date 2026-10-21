"""离线端口验收：SQLite 真实事务；测试表、设备参数及取出事实均非生产合同。"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from types import SimpleNamespace as N

import pytest
import pytest_asyncio
from sqlalchemy import JSON, Boolean, Column, Integer, MetaData, String, Table, UniqueConstraint, delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from wes_plugin_sdk import (
    PickingCellSource,
    PickingMaterialAccept,
    PickingMaterialWait,
    PickingTargetRackSlot,
    PickingTargetReplace,
    PickingTargetRotate,
)

from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE, DeviceCommandHandle, DeviceCommandRequest
from src.app.device.models.command import CommandStatus
from src.core.uuid7 import new_uuid7

NOW = datetime(2026, 10, 17)
SOURCE = PickingCellSource("CELL-7", 7, 2, 1, 20.0, 15.0)
OTHER = PickingCellSource("CELL-2", 2, 3, 1, 18.0, 12.0)
TARGET = PickingTargetRackSlot("TARGET", "A", "S1", 1, 1)
metadata = MetaData()
work_rows = Table(
    "fixture_work",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("workline_id", Integer),
    Column("authorization_evidence_id", Integer),
    Column("source", JSON),
    Column("consumed_pickup_id", String),
    Column("is_initial", Boolean),
    Column("top_removed", Boolean),
    Column("work_open", Boolean),
    Column("requirement_active", Boolean),
    Column("source_ready", Boolean),
)
command_rows = Table(
    "fixture_command",
    metadata,
    Column("command_code", String, primary_key=True),
    Column("execution_ref_id", String, unique=True),
    Column("request", JSON),
)
pickup_rows = Table(
    "fixture_pickup",
    metadata,
    Column("id", String, primary_key=True),
    Column("work_id", Integer),
    Column("authorization_evidence_id", Integer),
    Column("source", JSON),
    Column("command_code", String, unique=True),
    Column("material_execution_id", Integer),
    Column("accept_evidence_id", Integer),
    Column("target", JSON),
    Column("target_progress", String),
    UniqueConstraint("work_id", "authorization_evidence_id"),
)


async def test_only_request(db, *, work, pickup_id):
    return DeviceCommandRequest(
        device_code="TEST_ONLY_SOURCE_ARM",
        workline_id=work.workline_id,
        execution_ref_type=WORKLINE_BUSINESS_REF_TYPE,
        execution_ref_id=pickup_id,
        material_execution_id=None,
        contract_key="TEST_ONLY_OFFLINE",
        contract_version="1",
        task_type="TEST_ONLY_PICK",
        params={"fixture_only": True, "cell_index": work.source.cell_index},
        deadline_at=NOW + timedelta(seconds=1),
    )


test_only_request.__test__ = False


class OfflinePorts:
    """只为验收实现事务端口；未注册进插件，不代替 PostgreSQL Repository 或设备实现。"""

    def __init__(self, sessions):
        self.sessions, self.fail_at = sessions, None

    def crash(self, stage):
        if self.fail_at == stage:
            raise RuntimeError("crash:" + stage)

    @asynccontextmanager
    async def transaction(self):
        async with self.sessions() as db:
            # SQLite 没有行锁；仅测试 harness 用写事务串行化，不是工作线/平台业务锁。
            await db.execute(text("BEGIN IMMEDIATE"))
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def source_for_update(self, db, work_id):
        from automatic_picking.handlers.source_authorization import WorkSourceFact

        row = (await db.execute(select(work_rows).where(work_rows.c.id == work_id))).mappings().one()
        values = dict(row)
        values["work_id"] = values.pop("id")
        values["source"] = PickingCellSource(**values["source"]) if values["source"] is not None else None
        return WorkSourceFact(**values)

    async def create_command_in_session(self, db, request):
        command_code = new_uuid7()
        payload = asdict(request)
        payload["deadline_at"] = request.deadline_at.isoformat()
        await db.execute(
            command_rows.insert().values(
                command_code=command_code,
                execution_ref_id=request.execution_ref_id,
                request=payload,
            )
        )
        self.crash("command")
        return DeviceCommandHandle(command_code, CommandStatus.PENDING)

    async def add_pickup(self, db, *, pickup_id, work_id, authorization_evidence_id, source, command_code):
        await db.execute(
            pickup_rows.insert().values(
                id=pickup_id,
                work_id=work_id,
                authorization_evidence_id=authorization_evidence_id,
                source=asdict(source),
                command_code=command_code,
            )
        )
        self.crash("pickup")

    async def consume_authorization(self, db, *, work_id, authorization_evidence_id, pickup_id):
        changed = await db.execute(
            work_rows.update()
            .where(
                work_rows.c.id == work_id,
                work_rows.c.authorization_evidence_id == authorization_evidence_id,
                work_rows.c.consumed_pickup_id.is_(None),
            )
            .values(consumed_pickup_id=pickup_id, top_removed=None)
        )
        assert changed.rowcount == 1
        self.crash("consume")

    async def pickup_for_update(self, db, pickup_id):
        row = (await db.execute(select(pickup_rows).where(pickup_rows.c.id == pickup_id))).mappings().one()
        return N(**row)

    async def record_accept(self, db, *, pickup_id, evidence_id, outcome):
        await db.execute(
            pickup_rows.update()
            .where(pickup_rows.c.id == pickup_id)
            .values(
                accept_evidence_id=evidence_id,
                target=asdict(outcome.target_locator),
            )
        )
        self.crash("accept")

    async def handoff_authorization(self, db, *, work_id, evidence_id, source):
        await db.execute(
            work_rows.update()
            .where(work_rows.c.id == work_id)
            .values(
                authorization_evidence_id=evidence_id,
                source=asdict(source) if source is not None else None,
                consumed_pickup_id=None,
                is_initial=False,
            )
        )
        self.crash("handoff")

    async def rows(self, table):
        async with self.sessions() as db:
            return list((await db.execute(select(table))).mappings())


@pytest_asyncio.fixture
async def ports(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "offline.sqlite"))
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    ports = OfflinePorts(async_sessionmaker(engine, expire_on_commit=False))
    async with ports.transaction() as db:
        await db.execute(
            work_rows.insert().values(
                id=1,
                workline_id=7,
                authorization_evidence_id=31,
                source=asdict(SOURCE),
                consumed_pickup_id=None,
                is_initial=True,
                top_removed=None,
                work_open=True,
                requirement_active=True,
                source_ready=True,
            )
        )
    try:
        yield ports
    finally:
        await engine.dispose()


def flow(ports, mapper=test_only_request):
    from automatic_picking.application.source_flow import AutomaticPickingSourceFlow

    return AutomaticPickingSourceFlow(ports, ports, mapper)


async def consume(ports, evidence_id=31, work_id=1):
    async with ports.transaction() as db:
        return await flow(ports).consume_in_session(db, work_id=work_id, authorization_evidence_id=evidence_id)


async def accept(ports, pickup_id, source=SOURCE, evidence_id=41, target_preparation=None, material_id=101):
    async with ports.transaction() as db:
        return await flow(ports).handoff_in_session(
            db,
            work_id=1,
            pickup_id=pickup_id,
            material_execution_id=material_id,
            evidence_id=evidence_id,
            outcome=PickingMaterialAccept(TARGET, source, target_preparation),
        )


async def removed_and_associated(ports, pickup_id, material_id=101):
    # 明确注入的可靠取出及关联事实，不映射任何设备回调或猜测扫码关联。
    async with ports.transaction() as db:
        await db.execute(work_rows.update().where(work_rows.c.id == 1).values(top_removed=True))
        await db.execute(
            pickup_rows.update().where(pickup_rows.c.id == pickup_id).values(material_execution_id=material_id)
        )


@pytest.mark.asyncio
async def test_concurrent_consumers_and_restart_reuse_one_pickup_and_original_command(ports):
    first, second = await asyncio.gather(consume(ports), consume(ports))
    assert first == second == await consume(ports)
    pickups, commands, works = (
        await ports.rows(pickup_rows),
        await ports.rows(command_rows),
        await ports.rows(work_rows),
    )
    assert len(pickups) == len(commands) == 1
    assert pickups[0]["command_code"] == commands[0]["command_code"]
    assert commands[0]["execution_ref_id"] == works[0]["consumed_pickup_id"] == first
    assert commands[0]["request"]["material_execution_id"] is None
    assert pickups[0]["authorization_evidence_id"] == works[0]["authorization_evidence_id"] == 31


@pytest.mark.parametrize("stage", ["command", "pickup", "consume"])
@pytest.mark.asyncio
async def test_authorization_pickup_and_command_rollback_together(ports, stage):
    ports.fail_at = stage
    with pytest.raises(RuntimeError, match="crash:" + stage):
        await consume(ports)
    assert await ports.rows(pickup_rows) == await ports.rows(command_rows) == []
    work = (await ports.rows(work_rows))[0]
    assert work["authorization_evidence_id"] == 31 and work["consumed_pickup_id"] is None
    ports.fail_at = None
    assert await consume(ports) == await consume(ports)
    assert len(await ports.rows(pickup_rows)) == len(await ports.rows(command_rows)) == 1


@pytest.mark.parametrize("stage", ["accept", "handoff"])
@pytest.mark.asyncio
async def test_target_and_next_work_authorization_rollback_together(ports, stage):
    first = await consume(ports)
    await removed_and_associated(ports, first)
    ports.fail_at = stage
    with pytest.raises(RuntimeError, match="crash:" + stage):
        await accept(ports, first)
    work, pickup = (await ports.rows(work_rows))[0], (await ports.rows(pickup_rows))[0]
    assert (work["authorization_evidence_id"], work["consumed_pickup_id"]) == (31, first)
    assert pickup["accept_evidence_id"] is None and pickup["target"] is None
    ports.fail_at = None
    assert await accept(ports, first)
    assert not await accept(ports, first)
    assert (await ports.rows(work_rows))[0]["authorization_evidence_id"] == 41
    assert (await ports.rows(pickup_rows))[0]["target"] == asdict(TARGET)
    request = (await ports.rows(command_rows))[0]["request"]
    assert request["execution_ref_id"] == first and request["material_execution_id"] is None


@pytest.mark.asyncio
async def test_same_cell_lifo_waits_for_each_top_and_never_uses_work_cell_as_unique_pickup(ports):
    first = await consume(ports)
    async with ports.transaction() as db:
        await db.execute(pickup_rows.update().values(material_execution_id=101))
    assert await accept(ports, first)
    assert await consume(ports, 41) is None
    await removed_and_associated(ports, first)
    second = await consume(ports, 41)
    await removed_and_associated(ports, second, material_id=102)
    with pytest.raises(ValueError, match="Pickup"):
        await accept(ports, second, evidence_id=51, material_id=101)
    assert await accept(ports, second, evidence_id=51, material_id=102)
    third = await consume(ports, 51)
    assert len({first, second, third}) == 3
    assert [p["source"]["cell_id"] for p in await ports.rows(pickup_rows)] == ["CELL-7"] * 3
    assert [p["authorization_evidence_id"] for p in await ports.rows(pickup_rows)] == [31, 41, 51]
    assert [p["material_execution_id"] for p in await ports.rows(pickup_rows)] == [101, 102, None]


@pytest.mark.parametrize(
    "target_progress,preparation",
    [("target_unknown", PickingTargetReplace()), ("put_inflight", PickingTargetRotate()), ("report_unconfirmed", None)],
)
@pytest.mark.asyncio
async def test_next_source_does_not_wait_for_other_arm_or_report(ports, target_progress, preparation):
    first = await consume(ports)
    await removed_and_associated(ports, first)
    assert await accept(ports, first, OTHER, target_preparation=preparation)
    async with ports.transaction() as db:
        await db.execute(pickup_rows.update().values(target_progress=target_progress))
    second = await consume(ports, 41)
    assert second is not None and second != first
    assert [p["source"]["cell_id"] for p in await ports.rows(pickup_rows)] == ["CELL-7", "CELL-2"]
    assert (await ports.rows(pickup_rows))[0]["target_progress"] == target_progress


@pytest.mark.parametrize("top_removed", [None, False])
@pytest.mark.asyncio
async def test_ack_or_unknown_top_blocks_next_cell_even_when_target_report_is_done(ports, top_removed):
    first = await consume(ports)
    await removed_and_associated(ports, first)
    assert await accept(ports, first, OTHER)
    async with ports.transaction() as db:
        await db.execute(work_rows.update().values(top_removed=top_removed))
        await db.execute(pickup_rows.update().values(target_progress="report_confirmed"))
    assert await consume(ports, 41) is None
    assert len(await ports.rows(pickup_rows)) == len(await ports.rows(command_rows)) == 1


@pytest.mark.asyncio
async def test_current_work_recovers_pending_authorization_after_m1_history_is_removed(ports):
    first = await consume(ports)
    await removed_and_associated(ports, first)
    assert await accept(ports, first, OTHER)
    async with ports.transaction() as db:
        # 注入 M1 已闭合的前提，不在本片模拟 PUT/report 设备链。
        await db.execute(pickup_rows.update().values(target_progress="closed"))
        await db.execute(delete(pickup_rows).where(pickup_rows.c.id == first))
        await db.execute(delete(command_rows))
    assert await consume(ports) is None  # 旧 READY 通知不能消费新的 ACCEPT 授权。
    second = await consume(ports, 41)
    assert second is not None and second != first
    assert not await accept(ports, first, OTHER)
    assert (await ports.rows(work_rows))[0]["consumed_pickup_id"] == second
    assert len(await ports.rows(pickup_rows)) == len(await ports.rows(command_rows)) == 1


@pytest.mark.asyncio
async def test_omitted_next_source_is_an_explicit_work_decision_and_old_ready_cannot_restore_it(ports):
    first = await consume(ports)
    await removed_and_associated(ports, first)
    assert await accept(ports, first, None)
    work = (await ports.rows(work_rows))[0]
    assert work["authorization_evidence_id"] == 41 and work["source"] is None
    assert work["consumed_pickup_id"] is None
    assert await consume(ports, 41) is None and await consume(ports, 31) is None
    assert not await accept(ports, first, SOURCE)
    assert len(await ports.rows(pickup_rows)) == 1


@pytest.mark.asyncio
async def test_direct_requirement_cancellation_stops_new_pickup_but_keeps_existing_command(ports):
    first = await consume(ports)
    await removed_and_associated(ports, first)
    async with ports.transaction() as db:
        await db.execute(work_rows.update().values(requirement_active=False))
    assert await accept(ports, first)
    assert await consume(ports, 41) is None
    assert len(await ports.rows(command_rows)) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("workline_id", 99),
        ("execution_ref_id", "other"),
        ("material_execution_id", 101),
        ("execution_ref_type", "MATERIAL_EXECUTION"),
    ],
)
@pytest.mark.asyncio
async def test_command_mapping_must_keep_original_workline_and_prescan_pickup_identity(ports, field, value):
    async def wrong_request(db, **values):
        return replace(await test_only_request(db, **values), **{field: value})

    with pytest.raises(ValueError, match="Command"):
        async with ports.transaction() as db:
            await flow(ports, wrong_request).consume_in_session(db, work_id=1, authorization_evidence_id=31)
    assert await ports.rows(pickup_rows) == await ports.rows(command_rows) == []


@pytest.mark.parametrize("material_id,evidence_id", [(999, 41), (101, 31)])
@pytest.mark.asyncio
async def test_accept_cannot_change_pickup_material_or_reuse_its_source_evidence(ports, material_id, evidence_id):
    first = await consume(ports)
    await removed_and_associated(ports, first)
    with pytest.raises(ValueError, match="Pickup"):
        async with ports.transaction() as db:
            await flow(ports).handoff_in_session(
                db,
                work_id=1,
                pickup_id=first,
                material_execution_id=material_id,
                evidence_id=evidence_id,
                outcome=PickingMaterialAccept(TARGET, OTHER),
            )
    assert (await ports.rows(work_rows))[0]["authorization_evidence_id"] == 31
    assert (await ports.rows(pickup_rows))[0]["target"] is None


@pytest.mark.asyncio
async def test_wait_is_not_final_accept_and_cannot_replace_work_authorization(ports):
    first = await consume(ports)
    await removed_and_associated(ports, first)
    with pytest.raises(TypeError, match="ACCEPT"):
        async with ports.transaction() as db:
            await flow(ports).handoff_in_session(
                db,
                work_id=1,
                pickup_id=first,
                material_execution_id=101,
                evidence_id=41,
                outcome=PickingMaterialWait(1000),
            )
    work = (await ports.rows(work_rows))[0]
    assert (work["authorization_evidence_id"], work["consumed_pickup_id"]) == (31, first)
    assert (await ports.rows(pickup_rows))[0]["target"] is None


@pytest.mark.asyncio
async def test_authorization_stays_with_its_work_and_another_work_cannot_handoff_it(ports):
    first = await consume(ports)
    await removed_and_associated(ports, first)
    async with ports.transaction() as db:
        original = dict((await db.execute(select(work_rows))).mappings().one())
        await db.execute(
            work_rows.insert().values(
                **(
                    original
                    | {
                        "id": 2,
                        "authorization_evidence_id": 61,
                        "source": asdict(OTHER),
                        "consumed_pickup_id": None,
                        "is_initial": True,
                        "top_removed": None,
                    }
                )
            )
        )
        assert not await flow(ports).handoff_in_session(
            db,
            work_id=2,
            pickup_id=first,
            material_execution_id=101,
            evidence_id=41,
            outcome=PickingMaterialAccept(TARGET, OTHER),
        )
    assert await consume(ports, 31, work_id=2) is None
    second = await consume(ports, 61, work_id=2)
    assert second is not None and first != second
    assert [(p["work_id"], p["authorization_evidence_id"]) for p in await ports.rows(pickup_rows)] == [(1, 31), (2, 61)]


@pytest.mark.asyncio
async def test_new_work_round_does_not_reuse_closed_work_consumption(ports):
    first = await consume(ports)
    await removed_and_associated(ports, first)
    assert await accept(ports, first, None)
    async with ports.transaction() as db:
        await db.execute(pickup_rows.update().values(target_progress="closed"))
        await db.execute(work_rows.update().values(work_open=False))
        await db.execute(delete(pickup_rows))
        await db.execute(delete(command_rows))
        await db.execute(delete(work_rows))
        await db.execute(
            work_rows.insert().values(
                id=2,
                workline_id=7,
                authorization_evidence_id=61,
                source=asdict(SOURCE),
                consumed_pickup_id=None,
                is_initial=True,
                top_removed=None,
                work_open=True,
                requirement_active=True,
                source_ready=True,
            )
        )
    assert await consume(ports, 31, work_id=2) is None
    second = await consume(ports, 61, work_id=2)
    assert second is not None and second != first
    assert (await ports.rows(pickup_rows))[0]["work_id"] == 2
