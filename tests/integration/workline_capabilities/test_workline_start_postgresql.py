"""WorkLine START 的 PostgreSQL 事务、锁与直接替换证据。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from wes_plugin_sdk import PluginDefinition, WorkLineDeviceRole, WorkLinePositionSlot

from src.app.device.models.device import Device
from src.app.execution.plugin_binding import PluginRuntimeBinding
from src.app.workline.activation import (
    WorkLineActivationPlan,
    WorkLineDeviceBinding,
    WorkLinePositionBinding,
)
from src.app.workline.installed_plugin import InstalledWorkLinePlugin
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.services.workline_start_service import (
    WorkLineStartService,
    WorkLineStartVersionConflictError,
)
from tests.support.postgresql_heavy import run_alembic, temporary_database

pytestmark = pytest.mark.integration

RETIRED_COLUMNS = {
    "start_admission_status",
    "start_admission_message",
    "start_admission_failed_device_code",
    "start_admission_checked_at",
    "last_start_request_id",
    "last_start_trace_id",
}


@dataclass
class Builder:
    device_by_workline: dict[int, int]
    calls: list[int] = field(default_factory=list)

    async def build(
        self, _db: object, workline: WorkLine, *, position_bindings: tuple[WorkLinePositionBinding, ...]
    ) -> WorkLineActivationPlan:
        assert workline.id is not None
        self.calls.append(workline.id)
        device_id = self.device_by_workline[workline.id]
        return WorkLineActivationPlan(
            plugin_key="postgresql_test",
            plugin_version="1.0",
            flow_mode="GENERIC_FLOW",
            device_bindings=(
                WorkLineDeviceBinding(
                    workline_id=workline.id,
                    device_id=device_id,
                    device_code=f"START-PG-DEVICE-{workline.id}",
                    device_role="DEVICE_ROLE",
                    endpoint_base_url="http://ecs-start-pg:8080",
                    contract_key="generic.contract",
                    contract_version="1.0",
                    status_max_age_ms=1_000,
                    command_timeout_ms=5_000,
                ),
            ),
            position_bindings=position_bindings,
        )


def _plugin(builder: Builder) -> InstalledWorkLinePlugin:
    return InstalledWorkLinePlugin(
        definition=PluginDefinition(
            plugin_key="postgresql_test",
            plugin_version="1.0",
            display_name="PostgreSQL test",
            supported_line_types=(LineType.AUTO,),
            device_roles=(WorkLineDeviceRole(role_key="DEVICE_ROLE", display_name="设备"),),
            position_slots=(
                WorkLinePositionSlot(
                    slot_key="INPUT_POSITION", display_name="入口", position_type="STATION", location_type="RACK_CELL"
                ),
            ),
        ),
        runtime_binding=PluginRuntimeBinding(
            plugin_key="postgresql_test",
            plugin_version="1.0",
            handlers=(),
            fact_factory=object(),  # type: ignore[arg-type]
        ),
        start_plan_builder=builder,
    )


async def _seed_workline(session_factory: async_sessionmaker[AsyncSession], suffix: str) -> tuple[int, int]:
    async with session_factory.begin() as db:
        workline = WorkLine(
            line_code=f"START-PG-{suffix}",
            line_name=f"START PostgreSQL {suffix}",
            line_type=LineType.AUTO,
            plugin_key="postgresql_test",
        )
        db.add(workline)
        await db.flush()
        assert workline.id is not None
        device = Device(
            device_code=f"START-PG-DEVICE-{workline.id}",
            device_name=f"START PostgreSQL Device {suffix}",
            work_line_id=workline.id,
        )
        db.add(device)
        await db.flush()
        assert device.id is not None
        from src.app.runtime.orchestration.models.workline_position import WorkLinePosition

        db.add(
            WorkLinePosition(
                workline_id=workline.id,
                workline_code=workline.line_code,
                position_code="LOCAL",
                position_name="入口",
                position_type="STATION",
                logic_location_code=f"LOCATION-{workline.id}",
            )
        )
        workline.config = {
            "device_bindings": {"DEVICE_ROLE": device.device_code},
            "position_bindings": {"INPUT_POSITION": "LOCAL"},
        }
        await db.flush()
        return workline.id, device.id


def test_workline_start_publishes_current_contract_and_serializes_version() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            try:
                line_id, device_id = await _seed_workline(sessions, "CONCURRENT")
                builder = Builder({line_id: device_id})
                service = WorkLineStartService(plugins=(_plugin(builder),))

                async def start():
                    async with sessions.begin() as db:
                        return await service.start(db, workline_id=line_id, version=0)

                results = await asyncio.gather(start(), start(), return_exceptions=True)
                assert sum(isinstance(result, WorkLine) for result in results) == 1
                assert sum(isinstance(result, WorkLineStartVersionConflictError) for result in results) == 1
                assert builder.calls == [line_id]
                async with sessions() as db:
                    persisted = await db.get(WorkLine, line_id)
                    assert persisted.is_active and persisted.version == 1
                    assert persisted.plugin_version == "1.0"
                    assert persisted.flow_mode == "GENERIC_FLOW"
                    assert persisted.config == {
                        "device_bindings": {"DEVICE_ROLE": f"START-PG-DEVICE-{line_id}"},
                        "position_bindings": {"INPUT_POSITION": "LOCAL"},
                    }
                    assert persisted.device_contracts[f"START-PG-DEVICE-{line_id}"]["device_id"] == device_id
                    assert persisted.position_bindings == {
                        "INPUT_POSITION": {"location_id": f"LOCATION-{line_id}", "location_type": "RACK_CELL"}
                    }
                    columns = set(
                        await db.scalars(
                            text(
                                "SELECT column_name FROM information_schema.columns WHERE table_schema='wes_biz' AND table_name='work_lines'"
                            )
                        )
                    )
                    assert RETIRED_COLUMNS.isdisjoint(columns)
                rollback_id, rollback_device = await _seed_workline(sessions, "ROLLBACK")
                rollback_builder = Builder({rollback_id: rollback_device})
                rollback_service = WorkLineStartService(plugins=(_plugin(rollback_builder),))
                with pytest.raises(RuntimeError, match="abort transaction"):
                    async with sessions.begin() as db:
                        await rollback_service.start(db, workline_id=rollback_id, version=0)
                        raise RuntimeError("abort transaction")
                async with sessions() as db:
                    untouched = await db.get(WorkLine, rollback_id)
                    assert not untouched.is_active and untouched.version == 0
                    assert untouched.plugin_version is None
                    assert untouched.device_contracts == {} and untouched.position_bindings == {}
            finally:
                await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.asyncio
async def test_declaration_only_start_persists_basic_contracts(integration_session_factory):
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from sqlalchemy import delete

    from src.app.device.contracts import EcsDeviceStatus
    from src.app.device.services import device_command_admission
    from src.app.runtime.orchestration.models.workline_position import WorkLinePosition
    from src.utils.timezone import timezone

    sessions = integration_session_factory
    line_id, device_id = await _seed_workline(sessions, uuid4().hex[:10])
    try:
        async with sessions.begin() as db:
            device = await db.get(Device, device_id)
            device.endpoint_base_url = "http://ecs-start-pg:8080"
            device.is_active = True
        provider = AsyncMock()
        provider.get_adapter.return_value.fetch_statuses.return_value = (
            EcsDeviceStatus.model_validate(
                {
                    "device": {
                        "device_code": f"START-PG-DEVICE-{line_id}",
                        "device_name": None,
                        "device_type": None,
                        "role": None,
                        "supported_commands": None,
                        "supported_events": None,
                    },
                    "state": {
                        "device_code": f"START-PG-DEVICE-{line_id}",
                        "is_online": True,
                        "mode": "AUTO",
                        "status": "IDLE",
                        "current_command_code": None,
                        "scenario": None,
                        "updated_at": int(timezone.now_utc().timestamp() * 1000),
                    },
                }
            ),
        )
        definition = _plugin(Builder({})).definition
        service = WorkLineStartService(
            plugins=(InstalledWorkLinePlugin(definition=definition),),
            device_adapter_provider=provider,
            device_admission=device_command_admission,
        )
        async with sessions.begin() as db:
            await service.start(db, workline_id=line_id, version=0)
        async with sessions() as db:
            persisted = await db.get(WorkLine, line_id)
            assert persisted.is_active and persisted.version == 1 and persisted.flow_mode is None
            assert persisted.plugin_version == definition.plugin_version
            assert persisted.device_contracts[f"START-PG-DEVICE-{line_id}"]["device_id"] == device_id
            assert persisted.position_bindings["INPUT_POSITION"]["location_id"] == f"LOCATION-{line_id}"
    finally:
        async with sessions.begin() as db:
            await db.execute(delete(WorkLinePosition).where(WorkLinePosition.workline_id == line_id))
            await db.execute(delete(Device).where(Device.id == device_id))
            await db.execute(delete(WorkLine).where(WorkLine.id == line_id))
