"""SMT handoff 生产 route identity/boundary PostgreSQL 验收。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.contracts.external_contract_profile_catalog import WMS_MATERIAL_FLOW_PROFILE
from src.app.device.models.device import Device, DeviceStatus
from src.app.resource.models import RackKind
from src.app.runtime.orchestration.models.rack_position import WorklineRackPosition, WorklineRackPositionRole
from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import SmtInboundHandoffService
from src.app.runtime.workline_plugins.smt_sorting_inbound.contracts import SmtSortingInboundConfig
from src.app.runtime.workline_plugins.smt_sorting_inbound.definition import DEFINITION
from src.app.workline.models import LineType, WorkLine
from src.app.workline.services.plugin_binding_service import WorklinePluginBindingService
from src.app.workline.services.workline_service import workline_service
from src.core.conf import settings
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database


async def _seed_generated_candidate(db: AsyncSession) -> WorkLine:
    config = SmtSortingInboundConfig(
        provider_profile=WMS_MATERIAL_FLOW_PROFILE.identity,
        ctu_basket_capacity=6,
        conveyor_entry_queue={
            "code": "SMT-CONVEYOR-ENTRY",
            "role": "ENTRY",
            "capacity": 8,
            "order_policy": "FIFO",
        },
        return_queue={
            "code": "SMT-RETURN",
            "role": "RETURN_QUEUE",
            "order_policy": "FIFO",
        },
    )
    workline = WorkLine(
        line_code="IT-SMT-PRODUCTION-ROUTE",
        line_name="SMT production route",
        line_type=LineType.AUTO,
        plugin_key=DEFINITION.plugin_key,
        contract_version=DEFINITION.contract_version,
        config=config.model_dump(mode="json"),
        runtime_config_json={
            "smt_inbound_handoff_route": {
                "enabled": True,
                "priority": 10,
                "source_rack_position_code": "UNKNOWN_SOURCE",
            }
        },
        is_active=False,
    )
    db.add(workline)
    await db.flush()
    device = Device(
        device_code="IT-SMT-PRODUCTION-ROUTE-ARM",
        device_name="SMT production route arm",
        work_line_id=workline.id,
        device_role="SORTING_SOURCE_ARM",
        vendor_type="ECS",
        device_status=DeviceStatus.IDLE,
        capabilities_json={"supports_command_types": ["SORTING_SOURCE_PICK"]},
        host="127.0.0.1",
        port=1,
    )
    positions = [
        WorklineRackPosition(
            workline_id=workline.id,
            workline_code=workline.line_code,
            position_code=position_code,
            position_name=f"SMT {position_code}",
            position_role=WorklineRackPositionRole.SMT_SORTER_STATION,
            allowed_rack_kind=rack_kind,
            capacity=1,
            logic_location_code=f"{workline.line_code}:{position_code}",
            external_location_code=position_code,
            device_role=device_role,
            enabled=True,
        )
        for position_code, rack_kind, device_role in (
            ("SOURCE_STATION_A", RackKind.SINGLE_LAYER, "SORTING_SOURCE_ARM"),
            ("SOURCE_STATION_B", RackKind.SINGLE_LAYER, "SORTING_SOURCE_ARM"),
            ("TARGET_STATION", RackKind.FIVE_LAYER, None),
        )
    ]
    db.add_all([device, *positions])
    await db.flush()
    activated = await workline_service.activate(
        db,
        int(workline.id),
        version=workline.version,
        actor="integration-test",
        reason="production-route-boundary",
        environment=WorklinePluginBindingService.resolve_runtime_environment(settings.APP_ENV),
    )
    assert activated is not None
    return activated


def test_production_route_rejects_retired_identity_and_unknown_source_boundary() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            try:
                async with session_factory() as db:
                    retired = WorkLine(
                        line_code="IT-SMT-RETIRED-IDENTITY",
                        line_name="SMT retired identity",
                        line_type=LineType.AUTO,
                        plugin_key="SMT_SORTING_" + "INBOUND",
                        contract_version="2026-06-21" + ".p1",
                        runtime_config_json={"smt_inbound_handoff_route": {"enabled": True}},
                        is_active=True,
                    )
                    db.add(retired)
                    await db.commit()

                    service = SmtInboundHandoffService()
                    assert await service.repository.list_sorting_candidate_worklines(db) == []

                    generated = await _seed_generated_candidate(db)
                    await db.commit()
                    candidates = await service.repository.list_sorting_candidate_worklines(db)
                    assert [candidate.id for candidate in candidates] == [generated.id]

                    result = await service.route_service.resolve_route(
                        db,
                        demand=SimpleNamespace(id=1),
                        source_item=SimpleNamespace(id=2),
                        candidate_worklines=candidates,
                    )
                    assert result.kind == "MANUAL_HOLD"
                    assert result.failure_code == "SOURCE_BOUNDARY_INVALID"
                    assert result.route_evidence["configured_source_rack_position_code"] == "UNKNOWN_SOURCE"
                    assert result.route_evidence["manifest_contract_version"] == DEFINITION.contract_version
            finally:
                await engine.dispose()

    asyncio.run(scenario())
