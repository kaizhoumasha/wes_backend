"""WorkLine 设备全集替换与并发争用的 PostgreSQL 证据。"""

from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from wes_plugin_sdk import PluginDefinition

from src.app.device.models.device import Device
from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.models.position_projection import PositionProjection
from src.app.execution.plugin_binding import PluginRuntimeBinding
from src.app.execution.repositories.position_projection_repository import PositionProjectionRepository
from src.app.resource.models.resource import RackPlacement
from src.app.runtime.orchestration.models.workline_position import WorkLinePosition
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus, PickingTaskType
from src.app.workline.installed_plugin import InstalledWorkLinePlugin
from src.app.workline.models.workline import LineType, WorkLine, WorkLinePositionInput, WorkLineRunMode
from src.app.workline.repositories.workline_repository import WorkLineRepository
from src.app.workline.services.workline_configuration_service import WorkLineConfigurationService
from src.core.exceptions import BusinessException
from tests.support.postgresql_heavy import run_alembic, temporary_database

pytestmark = pytest.mark.integration


def _plugin() -> InstalledWorkLinePlugin:
    return InstalledWorkLinePlugin(
        definition=PluginDefinition(
            plugin_key="postgresql_test",
            plugin_version="1.0",
            display_name="PostgreSQL test",
            supported_line_types=(LineType.AUTO,),
        ),
        runtime_binding=PluginRuntimeBinding(
            plugin_key="postgresql_test",
            plugin_version="1.0",
            handlers=(),
            fact_factory=object(),  # type: ignore[arg-type]
        ),
        start_plan_builder=object(),
    )


def test_two_worklines_cannot_claim_the_same_unbound_device() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            try:
                async with sessions.begin() as db:
                    left = WorkLine(
                        id=347454468883008,
                        line_code="CONFIG-PG-LEFT",
                        line_name="Configuration left",
                        line_type=LineType.AUTO,
                    )
                    right = WorkLine(
                        id=347454468883009,
                        line_code="CONFIG-PG-RIGHT",
                        line_name="Configuration right",
                        line_type=LineType.AUTO,
                    )
                    device = Device(
                        device_code="CONFIG-PG-DEVICE",
                        device_name="Configuration device",
                    )
                    db.add_all([left, right, device])
                    await db.flush()
                    assert left.id is not None and right.id is not None
                    left_id, right_id = left.id, right.id
                    left_version, right_version = left.version, right.version
                    device_id = device.id
                    # 大 ID 在尚未绑定设备时也必须能查询配置状态。
                    status = await WorkLineConfigurationService(definitions=()).configuration_status(
                        db, workline_id=left_id
                    )
                    assert status.workline_id == left_id
                    assert not status.can_activate

                ready = asyncio.Barrier(2)

                async def claim(workline_id: int, version: int) -> str:
                    async with sessions() as db:
                        await ready.wait()
                        service = WorkLineConfigurationService(definitions=((_plugin()).definition,))
                        try:
                            await service.save_base(
                                db,
                                positions=tuple(
                                    WorkLinePositionInput(
                                        device_id=device_id,
                                        position_code=code,
                                        position_name=code,
                                        position_role=role,
                                        allowed_rack_kind=kind,
                                    )
                                    for code, role, kind in (
                                        ("WORK-1", "SMT_SORTER_STATION", "FIVE_LAYER"),
                                        ("WORK-2", "SMT_SORTER_STATION", "FIVE_LAYER"),
                                        ("RETURN", "SMT_RETURN_RACK_POSITION", "RETURN"),
                                        ("TRANSFER", "SMT_TRANSFER_RACK_POSITION", "TRANSFER"),
                                    )
                                ),
                                workline_id=workline_id,
                                version=version,
                                device_codes=("CONFIG-PG-DEVICE",),
                            )
                        except BusinessException:
                            await db.rollback()
                            return "CONFLICT"
                        return "SAVED"

                outcomes = await asyncio.gather(
                    claim(left_id, left_version),
                    claim(right_id, right_version),
                )
                assert sorted(outcomes) == ["CONFLICT", "SAVED"]

                async with sessions() as db:
                    persisted = await db.scalar(select(Device).where(Device.device_code == "CONFIG-PG-DEVICE"))
                    assert persisted is not None
                    assert persisted.work_line_id in {left_id, right_id}
                    status = await WorkLineConfigurationService(
                        definitions=((_plugin()).definition,)
                    ).base_configuration(db, workline_id=persisted.work_line_id)
                    assert status.workline_id == persisted.work_line_id
                    assert len(status.positions) == 4
                    rows = list((await db.scalars(select(WorkLinePosition))).all())
                    assert len(rows) == 4
                    assert {row.workline_id for row in rows} == {persisted.work_line_id}
                    winner_id = persisted.work_line_id
                    workline = await db.get(WorkLine, winner_id)
                    assert workline is not None
                    saved_version = workline.version
                    saved_ids = {row.position_code: row.id for row in rows}
                    rows[0].metadata_json = {"site_note": "preserve"}
                    preserved_code = rows[0].position_code
                    await db.commit()

                # 提交故障必须同时回滚工作位删除、设备解绑和配置版本。
                async with sessions() as db:
                    with patch.object(db, "commit", AsyncMock(side_effect=RuntimeError("commit failed"))):
                        with pytest.raises(RuntimeError, match="commit failed"):
                            await WorkLineConfigurationService(definitions=((_plugin()).definition,)).save_base(
                                db,
                                workline_id=winner_id,
                                version=saved_version,
                                device_codes=(),
                                positions=(),
                            )
                async with sessions() as db:
                    workline = await db.get(WorkLine, winner_id)
                    assert workline is not None and workline.version == saved_version
                    device = await db.scalar(select(Device).where(Device.device_code == "CONFIG-PG-DEVICE"))
                    assert device is not None and device.work_line_id == winner_id
                    status = await WorkLineConfigurationService(
                        definitions=((_plugin()).definition,)
                    ).base_configuration(db, workline_id=winner_id)
                    assert len(status.positions) == 4
                    edited = tuple(
                        position.model_copy(update={"position_name": position.position_name + " updated"})
                        for position in status.positions
                    )
                    await WorkLineConfigurationService(definitions=((_plugin()).definition,)).save_base(
                        db,
                        workline_id=winner_id,
                        version=saved_version,
                        device_codes=("CONFIG-PG-DEVICE",),
                        positions=edited,
                    )
                async with sessions() as db:
                    rows = list((await db.scalars(select(WorkLinePosition))).all())
                    assert {row.position_code: row.id for row in rows} == saved_ids
                    assert next(row for row in rows if row.position_code == preserved_code).metadata_json == {
                        "site_note": "preserve"
                    }
                    assert all(row.position_name.endswith(" updated") for row in rows)
                    assert {row.device_id for row in rows} == {device_id}
                    line = await db.get(WorkLine, winner_id)
                    assert line is not None
                    version = line.version
                async with sessions() as db:
                    line = await WorkLineConfigurationService(definitions=((_plugin()).definition,)).save(
                        db,
                        workline_id=winner_id,
                        version=version,
                        plugin_key="postgresql_test",
                        config={},
                    )
                    await WorkLineConfigurationService(definitions=((_plugin()).definition,)).save(
                        db,
                        workline_id=winner_id,
                        version=line.version,
                        plugin_key=None,
                        config={},
                    )
                async with sessions() as db:
                    rows = list((await db.scalars(select(WorkLinePosition))).all())
                    assert {row.position_code: row.id for row in rows} == saved_ids
                    assert {row.device_id for row in rows} == {device_id}
                    device = await db.get(Device, device_id)
                    assert device is not None and device.work_line_id == winner_id

                # 没有通用位置投影时，货架到位记录仍禁止删除或改写基础工作位。
                async with sessions.begin() as db:
                    db.add(
                        RackPlacement(
                            rack_code="CONFIG-OCCUPIED",
                            workline_id=winner_id,
                            position_code="WORK-1",
                            placement_status="ARRIVED",
                            source_system="ECS",
                            source_event_id="config-arrived",
                            started_at=datetime(2026, 9, 8),
                        )
                    )
                async with sessions() as db:
                    line = await db.get(WorkLine, winner_id)
                    assert line is not None
                    with pytest.raises(BusinessException, match="货架或料箱占位"):
                        await WorkLineConfigurationService(definitions=((_plugin()).definition,)).save_base(
                            db,
                            workline_id=winner_id,
                            version=line.version,
                            device_codes=(),
                            positions=(),
                        )
                    await db.rollback()
                    assert len(list((await db.scalars(select(WorkLinePosition))).all())) == 4

            finally:
                await engine.dispose()

    asyncio.run(scenario())


def test_configuration_can_claim_the_active_replacement_for_a_deleted_device_code() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            try:
                async with sessions.begin() as db:
                    workline = WorkLine(
                        line_code="CONFIG-PG-REUSED-CODE",
                        line_name="Configuration reused device code",
                        line_type=LineType.AUTO,
                    )
                    db.add(workline)
                    await db.flush()
                    deleted = Device(
                        device_code="CONFIG-PG-REUSED-DEVICE",
                        device_name="Deleted device",
                        work_line_id=workline.id,
                        is_deleted=True,
                    )
                    replacement = Device(
                        device_code="CONFIG-PG-REUSED-DEVICE",
                        device_name="Replacement device",
                    )
                    db.add_all([deleted, replacement])
                    await db.flush()
                    assert workline.id is not None
                    workline_id = workline.id
                    version = workline.version
                    replacement_id = replacement.id

                async with sessions() as db:
                    await WorkLineConfigurationService(definitions=((_plugin()).definition,)).save_base(
                        db,
                        positions=(),
                        workline_id=workline_id,
                        version=version,
                        device_codes=("CONFIG-PG-REUSED-DEVICE",),
                    )

                async with sessions() as db:
                    devices = list(
                        (
                            await db.scalars(
                                select(Device)
                                .where(Device.device_code == "CONFIG-PG-REUSED-DEVICE")
                                .order_by(Device.id)
                            )
                        ).all()
                    )
                    assert len(devices) == 2
                    assert devices[0].is_deleted is True
                    assert devices[1].id == replacement_id
                    assert devices[1].work_line_id == workline_id
            finally:
                await engine.dispose()

    asyncio.run(scenario())


def test_position_projection_blocker_reports_workline_positions_and_unknown_only() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            try:
                async with sessions.begin() as db:
                    workline = WorkLine(
                        line_code="CONFIG-PG-PROJECTION-BLOCKER",
                        line_name="Configuration projection blocker",
                        line_type=LineType.AUTO,
                        is_active=True,
                    )
                    db.add(workline)
                    await db.flush()
                    assert workline.id is not None
                    workline.position_bindings = {
                        "PIPELINE_OUTLET": {"location_id": "OUTLET-1", "location_type": "PIPELINE_OUTLET"}
                    }
                    db.add_all(
                        [
                            PositionProjection(
                                object_type="RACK",
                                object_id="RACK-ON-LINE",
                                workline_id=workline.id,
                                position_json={"kind": "RACK_POSITION", "location_code": "OUTLET-1"},
                                position_unknown=False,
                                source_operation_id="019d0000-0000-7000-8000-000000000001",
                                source_transport_task_id="PROJECTION-ON-LINE",
                            ),
                            PositionProjection(
                                object_type="RACK",
                                object_id="RACK-OUTSIDE",
                                workline_id=workline.id,
                                position_json={"kind": "RACK_POSITION", "location_code": "STORAGE-1"},
                                position_unknown=False,
                                source_operation_id="019d0000-0000-7000-8000-000000000002",
                                source_transport_task_id="PROJECTION-OUTSIDE",
                            ),
                            PositionProjection(
                                object_type="RACK",
                                object_id="RACK-UNKNOWN",
                                workline_id=workline.id,
                                position_json=None,
                                position_unknown=True,
                                source_operation_id="019d0000-0000-7000-8000-000000000003",
                                source_transport_task_id="PROJECTION-UNKNOWN",
                            ),
                        ]
                    )
                    await db.flush()

                    summary = await PositionProjectionRepository().get_active_workline_summary(db, workline.id)

                    assert summary == {
                        "count": 2,
                        "sample": {
                            "type": "position_projection",
                            "id": str(
                                await db.scalar(
                                    select(PositionProjection.id).where(PositionProjection.object_id == "RACK-ON-LINE")
                                )
                            ),
                            "status": "OUTLET-1",
                            "identity": "RACK:RACK-ON-LINE",
                        },
                    }
            finally:
                await engine.dispose()

    asyncio.run(scenario())


def test_task_admission_and_deactivate_share_workline_lock() -> None:
    class _BusinessBlocker:
        def __init__(self) -> None:
            self.active = False

        async def get_unfinished_workload_summary(self, _db: object, _workline_id: int) -> dict[str, object]:
            return {
                "count": int(self.active),
                "sample": {"identity": "ADMISSION-1"} if self.active else None,
            }

    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            blocker = _BusinessBlocker()
            admitted_with_locks = asyncio.Event()
            release_admission = asyncio.Event()
            try:
                async with sessions.begin() as db:
                    workline = WorkLine(
                        line_code="CONFIG-PG-LOCK-ORDER",
                        line_name="Configuration lock order",
                        line_type=LineType.MANUAL,
                        run_mode=WorkLineRunMode.AUTO,
                        plugin_key="postgresql_test",
                        is_active=True,
                    )
                    db.add(workline)
                    await db.flush()
                    assert workline.id is not None
                    workline_id = workline.id
                    workline_version = workline.version

                async def admit_task() -> None:
                    worklines = WorkLineRepository()
                    async with sessions.begin() as db:
                        locked_workline = await worklines.get_for_update(db, workline_id)
                        assert locked_workline is not None and locked_workline.is_active
                        admitted_with_locks.set()
                        await release_admission.wait()
                        blocker.active = True

                async def deactivate() -> str:
                    service = WorkLineConfigurationService(
                        definitions=(
                            (
                                InstalledWorkLinePlugin(
                                    definition=PluginDefinition(
                                        plugin_key="postgresql_test",
                                        plugin_version="1.0",
                                        display_name="PostgreSQL test",
                                        supported_line_types=(LineType.MANUAL,),
                                    ),
                                    runtime_binding=PluginRuntimeBinding(
                                        plugin_key="postgresql_test",
                                        plugin_version="1.0",
                                        handlers=(),
                                        fact_factory=object(),  # type: ignore[arg-type]
                                    ),
                                    start_plan_builder=object(),
                                    business_blocker=blocker,
                                )
                            ).definition,
                        ),
                        business_blockers={"postgresql_test": blocker},
                    )
                    async with sessions() as db:
                        try:
                            await service.deactivate(db, workline_id=workline_id, version=workline_version)
                        except BusinessException as exc:
                            await db.rollback()
                            return str(exc)
                    return "DEACTIVATED"

                admission_task = asyncio.create_task(admit_task())
                await admitted_with_locks.wait()
                deactivation_task = asyncio.create_task(deactivate())
                await asyncio.sleep(0.05)
                release_admission.set()
                _, deactivation_result = await asyncio.wait_for(
                    asyncio.gather(admission_task, deactivation_task),
                    timeout=5,
                )

                assert "ADMISSION-1" in deactivation_result
                async with sessions() as db:
                    persisted_workline = await db.get(WorkLine, workline_id)
                    assert persisted_workline is not None and persisted_workline.is_active
            finally:
                await engine.dispose()

    asyncio.run(scenario())


def test_picking_binding_commit_is_visible_to_waiting_workline_deactivate() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            binding_ready = asyncio.Event()
            release_binding = asyncio.Event()
            deactivate_connected = asyncio.Event()
            backend_pids: dict[str, int] = {}
            running: list[asyncio.Task] = []
            now = datetime(2026, 9, 6)
            try:
                async with sessions.begin() as db:
                    workline = WorkLine(
                        line_code="CONFIG-PG-PICKING-FENCE",
                        line_name="Picking lifecycle fence",
                        line_type=LineType.AUTO,
                        run_mode=WorkLineRunMode.AUTO,
                        plugin_key="postgresql_test",
                        is_active=True,
                    )
                    db.add(workline)
                    await db.flush()
                    evidence = InboundEvidence(
                        kind=InboundEvidenceKind.WMS_EVENT,
                        source_identity="CONFIG-PG-PICKING-FENCE-ISSUED",
                        operation="outbound.picking_task.issued@v1",
                        operation_id="019d0000-0000-7000-8000-000000000011",
                        payload_digest="c" * 64,
                        normalized_payload={"data": {}},
                        received_at=now,
                        apply_status=InboundEvidenceApplyStatus.APPLIED,
                        processed_at=now,
                    )
                    db.add(evidence)
                    await db.flush()
                    task = PickingTask(
                        task_id="CONFIG-PG-PICKING-FENCE-TASK",
                        task_type=PickingTaskType.AUTO,
                        queue_revision=1,
                        dispatch_sequence=1,
                        issued_at_ms=1,
                        issued_evidence_id=evidence.id,
                    )
                    db.add(task)
                    await db.flush()
                    workline_id, workline_version, task_id = (
                        workline.id,
                        workline.version,
                        task.id,
                    )

                async def bind_task() -> None:
                    async with sessions.begin() as db:
                        backend_pids["binding"] = await db.scalar(text("SELECT pg_backend_pid()"))
                        locked_line = await WorkLineRepository().get_for_update(db, workline_id)
                        assert locked_line is not None and locked_line.is_active
                        locked_task = await db.get(PickingTask, task_id, with_for_update=True)
                        assert locked_task is not None
                        locked_task.status = PickingTaskStatus.PREPARING
                        locked_task.workline_id = workline_id
                        await db.flush()
                        binding_ready.set()
                        await release_binding.wait()

                async def deactivate() -> BusinessException:
                    async with sessions() as db:
                        backend_pids["deactivate"] = await db.scalar(text("SELECT pg_backend_pid()"))
                        deactivate_connected.set()
                        try:
                            with pytest.raises(BusinessException) as rejected:
                                await WorkLineConfigurationService(definitions=((_plugin()).definition,)).deactivate(
                                    db, workline_id=workline_id, version=workline_version
                                )
                            return rejected.value
                        finally:
                            await db.rollback()

                running.append(asyncio.create_task(bind_task()))
                await asyncio.wait_for(binding_ready.wait(), timeout=5)
                running.append(asyncio.create_task(deactivate()))
                await asyncio.wait_for(deactivate_connected.wait(), timeout=5)
                async with asyncio.timeout(5), sessions() as observer:
                    while True:
                        blockers = await observer.scalar(
                            text("SELECT pg_blocking_pids(:pid)"), {"pid": backend_pids["deactivate"]}
                        )
                        if backend_pids["binding"] in blockers:
                            break
                        assert not running[1].done(), "deactivate 未等待 PickingTask 绑定事务的 WorkLine 锁"
                        await asyncio.sleep(0.01)
                release_binding.set()
                _, rejection = await asyncio.wait_for(asyncio.gather(*running), timeout=5)
                workload = rejection.detail["workload"]
                assert workload["by_type"]["picking_tasks"] == 1
                assert workload["samples"]["picking_tasks"]["identity"] == "CONFIG-PG-PICKING-FENCE-TASK"
                async with sessions() as db:
                    persisted_line = await db.get(WorkLine, workline_id)
                    persisted_task = await db.get(PickingTask, task_id)
                    assert persisted_line is not None and persisted_line.is_active
                    assert persisted_task is not None and persisted_task.status == PickingTaskStatus.PREPARING
                    assert persisted_task.workline_id == workline_id
            finally:
                release_binding.set()
                for pending in running:
                    if not pending.done():
                        pending.cancel()
                await asyncio.gather(*running, return_exceptions=True)
                await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("position_type", ["RACK_POSITION", "STATION"])
def test_base_position_device_migration_refuses_lossy_downgrade(position_type: str) -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url)
            sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            try:
                async with sessions.begin() as db:
                    line = WorkLine(line_code="BASE-MIGRATION", line_name="Base migration", line_type=LineType.AUTO)
                    device = Device(device_code="BASE-MIGRATION-DEVICE", device_name="Base migration device")
                    db.add_all([line, device])
                    await db.flush()
                    position = WorkLinePosition(
                        workline_id=line.id,
                        workline_code=line.line_code,
                        position_code="RETURN",
                        position_name="Return",
                        position_type=position_type,
                        position_role="SMT_RETURN_RACK_POSITION" if position_type == "RACK_POSITION" else None,
                        allowed_rack_kind="RETURN" if position_type == "RACK_POSITION" else None,
                        device_id=device.id,
                    )
                    db.add(position)
                async with sessions() as db:
                    assert await db.scalar(text("SELECT to_regclass('wes_biz.workline_rack_positions')")) is None
                    assert await db.scalar(text("SELECT to_regclass('wes_biz.workline_positions')")) is not None
                    head_revision = await db.scalar(text("SELECT version_num FROM wes_sys.alembic_version"))
                if position_type == "RACK_POSITION":
                    await engine.dispose()
                    run_alembic("downgrade", "a7e8ad4339e5", database_url=database_url)
                    async with sessions() as db:
                        legacy = (
                            await db.execute(
                                text(
                                    "SELECT id, workline_id, device_id FROM wes_biz.workline_rack_positions WHERE id = :id"
                                ),
                                {"id": position.id},
                            )
                        ).one()
                        assert tuple(legacy) == (position.id, line.id, device.id)
                        assert await db.scalar(text("SELECT to_regclass('wes_biz.workline_positions')")) is None
                    await engine.dispose()
                    run_alembic("upgrade", "head", database_url=database_url)
                    async with sessions() as db:
                        restored = await db.get(WorkLinePosition, position.id)
                        assert restored is not None
                        assert (restored.workline_id, restored.device_id, restored.position_code) == (
                            line.id,
                            device.id,
                            "RETURN",
                        )
                with pytest.raises(subprocess.CalledProcessError) as failure:
                    run_alembic("downgrade", "bebf575cca2b", database_url=database_url)
                expected = (
                    "Cannot downgrade while rack positions reference physical devices"
                    if position_type == "RACK_POSITION"
                    else "普通工作位仍存在"
                )
                assert expected in failure.value.stderr
                async with sessions.begin() as db:
                    assert await db.scalar(text("SELECT version_num FROM wes_sys.alembic_version")) == head_revision
                    row = await db.get(WorkLinePosition, position.id)
                    assert row is not None and row.device_id == device.id
                    row.device_id = None
                # New position purposes also protect against a lossy downgrade.
                with pytest.raises(subprocess.CalledProcessError):
                    run_alembic("downgrade", "bebf575cca2b", database_url=database_url)
                async with sessions.begin() as db:
                    row = await db.get(WorkLinePosition, position.id)
                    assert row is not None
                    await db.delete(row)
                run_alembic("downgrade", "bebf575cca2b", database_url=database_url)
                run_alembic("upgrade", "head", database_url=database_url)
                async with sessions() as db:
                    assert (
                        await db.scalar(text("SELECT to_regclass('wes_biz.ix_wes_biz_workline_positions_device_id')"))
                        is not None
                    )
                    assert await db.get(Device, device.id) is not None
            finally:
                await engine.dispose()

    asyncio.run(scenario())
