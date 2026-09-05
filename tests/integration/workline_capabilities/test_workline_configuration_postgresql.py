"""WorkLine 设备全集替换与并发争用的 PostgreSQL 证据。"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.device.models.device import Device
from src.app.execution.models.position_projection import PositionProjection
from src.app.execution.plugin_binding import PluginRuntimeBinding
from src.app.execution.repositories.position_projection_repository import PositionProjectionRepository
from src.app.workline.installed_plugin import InstalledWorkLinePlugin
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochPositionBinding, LineRunEpochStatus
from src.app.workline.models.workline import LineType, WorkLine, WorkLineRunMode
from src.app.workline.repositories.line_run_epoch_repository import LineRunEpochRepository
from src.app.workline.repositories.workline_repository import WorkLineRepository
from src.app.workline.services.workline_configuration_service import WorkLineConfigurationService
from src.core.exceptions import BusinessException
from tests.support.postgresql_heavy import run_alembic, temporary_database

pytestmark = pytest.mark.integration


def _plugin() -> InstalledWorkLinePlugin:
    return InstalledWorkLinePlugin(
        display_name="PostgreSQL test",
        runtime_binding=PluginRuntimeBinding(
            plugin_key="postgresql_test",
            plugin_version="1.0",
            handlers=(),
            fact_factory=object(),  # type: ignore[arg-type]
        ),
        start_plan_builder=object(),
        supported_line_types=(LineType.AUTO,),
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
                        line_code="CONFIG-PG-LEFT",
                        line_name="Configuration left",
                        line_type=LineType.AUTO,
                    )
                    right = WorkLine(
                        line_code="CONFIG-PG-RIGHT",
                        line_name="Configuration right",
                        line_type=LineType.AUTO,
                    )
                    device = Device(
                        device_code="CONFIG-PG-DEVICE",
                        device_name="Configuration device",
                        device_role="TRANSFER_DEVICE",
                    )
                    db.add_all([left, right, device])
                    await db.flush()
                    assert left.id is not None and right.id is not None
                    left_id, right_id = left.id, right.id
                    left_version, right_version = left.version, right.version

                ready = asyncio.Barrier(2)

                async def claim(workline_id: int, version: int) -> str:
                    async with sessions() as db:
                        await ready.wait()
                        service = WorkLineConfigurationService(plugins=(_plugin(),))
                        try:
                            await service.save(
                                db,
                                workline_id=workline_id,
                                version=version,
                                plugin_key="postgresql_test",
                                config={},
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
                        device_role="TRANSFER_DEVICE",
                        work_line_id=workline.id,
                        is_deleted=True,
                    )
                    replacement = Device(
                        device_code="CONFIG-PG-REUSED-DEVICE",
                        device_name="Replacement device",
                        device_role="TRANSFER_DEVICE",
                    )
                    db.add_all([deleted, replacement])
                    await db.flush()
                    assert workline.id is not None
                    workline_id = workline.id
                    version = workline.version
                    replacement_id = replacement.id

                async with sessions() as db:
                    await WorkLineConfigurationService(plugins=(_plugin(),)).save(
                        db,
                        workline_id=workline_id,
                        version=version,
                        plugin_key="postgresql_test",
                        config={},
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
                    epoch = LineRunEpoch(
                        epoch_code="CONFIG-PG-PROJECTION-BLOCKER-EPOCH",
                        workline_id=workline.id,
                        plugin_key="postgresql_test",
                        plugin_version="1.0",
                        flow_mode="ROUGH_SORT_INBOUND",
                        topology_digest="a" * 64,
                        configuration_digest="b" * 64,
                        configuration_snapshot_json={},
                        started_at=datetime(2026, 9, 5),
                    )
                    db.add(epoch)
                    await db.flush()
                    assert epoch.id is not None
                    db.add(
                        LineRunEpochPositionBinding(
                            line_run_epoch_id=epoch.id,
                            position_role="PIPELINE_OUTLET",
                            location_id="OUTLET-1",
                            location_type="PIPELINE_OUTLET",
                        )
                    )
                    db.add_all(
                        [
                            PositionProjection(
                                object_type="RACK",
                                object_id="RACK-ON-LINE",
                                workline_id=workline.id,
                                line_run_epoch_id=epoch.id,
                                position_json={"kind": "RACK_POSITION", "location_code": "OUTLET-1"},
                                position_unknown=False,
                                source_operation_id="019d0000-0000-7000-8000-000000000001",
                                source_transport_task_id="PROJECTION-ON-LINE",
                            ),
                            PositionProjection(
                                object_type="RACK",
                                object_id="RACK-OUTSIDE",
                                workline_id=workline.id,
                                line_run_epoch_id=epoch.id,
                                position_json={"kind": "RACK_POSITION", "location_code": "STORAGE-1"},
                                position_unknown=False,
                                source_operation_id="019d0000-0000-7000-8000-000000000002",
                                source_transport_task_id="PROJECTION-OUTSIDE",
                            ),
                            PositionProjection(
                                object_type="RACK",
                                object_id="RACK-UNKNOWN",
                                workline_id=workline.id,
                                line_run_epoch_id=epoch.id,
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


def test_task_admission_and_deactivate_share_workline_then_epoch_lock_order() -> None:
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
                    epoch = LineRunEpoch(
                        epoch_code="CONFIG-PG-LOCK-ORDER-EPOCH",
                        workline_id=workline.id,
                        plugin_key="postgresql_test",
                        plugin_version="1.0",
                        flow_mode="MANUAL_PICKING",
                        topology_digest="a" * 64,
                        configuration_digest="b" * 64,
                        configuration_snapshot_json={},
                        started_at=datetime(2026, 9, 5),
                    )
                    db.add(epoch)
                    await db.flush()
                    workline_id = workline.id
                    workline_version = workline.version

                async def admit_task() -> None:
                    worklines = WorkLineRepository()
                    epochs = LineRunEpochRepository()
                    async with sessions.begin() as db:
                        locked_workline = await worklines.get_for_update(db, workline_id)
                        assert locked_workline is not None and locked_workline.is_active
                        active = await epochs.get_active_for_workline(db, workline_id)
                        assert active is not None and active.id is not None
                        await epochs.lock_epoch_lifecycle(db, active.id)
                        locked_epoch = await epochs.get_active_for_workline_for_update(db, workline_id)
                        assert locked_epoch is not None and locked_epoch.id == active.id
                        admitted_with_locks.set()
                        await release_admission.wait()
                        blocker.active = True

                async def deactivate() -> str:
                    service = WorkLineConfigurationService(
                        plugins=(
                            InstalledWorkLinePlugin(
                                display_name="PostgreSQL test",
                                runtime_binding=PluginRuntimeBinding(
                                    plugin_key="postgresql_test",
                                    plugin_version="1.0",
                                    handlers=(),
                                    fact_factory=object(),  # type: ignore[arg-type]
                                ),
                                start_plan_builder=object(),
                                supported_line_types=(LineType.MANUAL,),
                                business_blocker=blocker,
                            ),
                        )
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
                    persisted_epoch = await db.scalar(
                        select(LineRunEpoch).where(LineRunEpoch.workline_id == workline_id)
                    )
                    assert persisted_workline is not None and persisted_workline.is_active
                    assert persisted_epoch is not None and persisted_epoch.status == LineRunEpochStatus.ACTIVE
            finally:
                await engine.dispose()

    asyncio.run(scenario())
