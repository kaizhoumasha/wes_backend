"""SMT generated Plugin mandatory binding 的 PostgreSQL 验收。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.contracts.external_contract_profile_catalog import WMS_MATERIAL_FLOW_PROFILE
from src.app.device.models.device import Device, DeviceStatus
from src.app.resource.models import RackKind
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.rack_position import WorklineRackPosition, WorklineRackPositionRole
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import SmtInboundHandoffService
from src.app.runtime.workline_plugins.dispatcher import (
    PinnedPluginSnapshot,
    PluginAttemptFactSource,
    PluginDispatchRequest,
    WorklinePluginDispatcher,
)
from src.app.runtime.workline_plugins.smt_sorting_inbound.contracts import SmtSortingInboundConfig
from src.app.runtime.workline_plugins.smt_sorting_inbound.definition import DEFINITION
from src.app.workline.models import LineType, WorkLine, WorklinePluginBinding
from src.app.workline.services.plugin_binding_service import WorklinePluginBindingService
from src.app.workline.services.workline_service import workline_service
from src.core.conf import settings
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database


async def _seed_binding(db: AsyncSession) -> tuple[WorkLine, WorklinePluginBinding, SmtSortingInboundConfig]:
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
        line_code="IT-SMT-GENERATED-BINDING",
        line_name="SMT Generated Binding",
        line_type=LineType.AUTO,
        plugin_key=DEFINITION.plugin_key,
        contract_version=DEFINITION.contract_version,
        config=config.model_dump(mode="json"),
        is_active=False,
    )
    db.add(workline)
    await db.flush()
    device = Device(
        device_code="IT-SMT-GENERATED-BINDING-ARM",
        device_name="SMT Generated Binding Arm",
        work_line_id=workline.id,
        device_role="SORTING_SOURCE_ARM",
        vendor_type="ECS",
        device_status=DeviceStatus.IDLE,
        capabilities_json={"supports_command_types": ["SORTING_SOURCE_PICK"]},
        host="127.0.0.1",
        port=1,
    )
    rack_positions = [
        WorklineRackPosition(
            workline_id=workline.id,
            workline_code=workline.line_code,
            position_code=position_code,
            position_name=f"SMT generated {position_code}",
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
    db.add_all([device, *rack_positions])
    await db.flush()
    activated = await workline_service.activate(
        db,
        int(workline.id),
        version=workline.version,
        actor="integration-test",
        reason="mandatory-binding",
        environment=WorklinePluginBindingService.resolve_runtime_environment(settings.APP_ENV),
    )
    assert activated is not None
    assert activated.active_plugin_binding_id is not None
    binding = await db.get(WorklinePluginBinding, activated.active_plugin_binding_id)
    assert binding is not None
    assert activated.active_plugin_binding_version == binding.binding_version
    assert activated.active_plugin_config_hash == binding.typed_config_hash
    assert activated.active_plugin_index_digest == binding.generated_index_digest
    return activated, binding, config


def test_smt_claim_binding_is_atomic_and_fresh_generated_dispatch_succeeds() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            try:
                async with session_factory() as db:
                    workline, binding, config = await _seed_binding(db)

                class FailingAfterAggregateService(SmtInboundHandoffService):
                    async def _link_claim_session_material_unit(
                        self,
                        _db: AsyncSession,
                        *,
                        session: WorklineSession,
                        item: object,
                    ) -> None:
                        _ = (session, item)
                        raise RuntimeError("forced-after-runtime-aggregate")

                demand = SimpleNamespace(id=11, demand_key="smt-demand-11", trace_id="trace-11")
                item = SimpleNamespace(id=12, claim_attempt_no=1, pkg_code=None)
                async with session_factory() as db:
                    persisted_workline = await db.get(WorkLine, workline.id)
                    persisted_binding = await db.get(WorklinePluginBinding, binding.id)
                    assert persisted_workline is not None and persisted_binding is not None
                    try:
                        await FailingAfterAggregateService()._create_sorting_claim_session(
                            db,
                            workline=persisted_workline,
                            binding=persisted_binding,
                            workline_code=persisted_workline.line_code,
                            demand=demand,
                            item=item,
                            trace_id="trace-rollback",
                            route_evidence={
                                "source_rack_position_code": "SOURCE_STATION_A",
                                "target_rack_position_code": "TARGET_STATION",
                            },
                        )
                    except RuntimeError as exc:
                        assert str(exc) == "forced-after-runtime-aggregate"
                        await db.rollback()
                    else:
                        raise AssertionError("创建中途失败必须传播并触发外层事务回滚")

                async with session_factory() as db:
                    for model in (WorklineSession, ExecutionSession, ExecutionWorkItem):
                        assert await db.scalar(select(func.count()).select_from(model)) == 0

                    persisted_workline = await db.get(WorkLine, workline.id)
                    persisted_binding = await db.get(WorklinePluginBinding, binding.id)
                    assert persisted_workline is not None and persisted_binding is not None
                    claim_runtime = await SmtInboundHandoffService()._create_sorting_claim_session(
                        db,
                        workline=persisted_workline,
                        binding=persisted_binding,
                        workline_code=persisted_workline.line_code,
                        demand=demand,
                        item=item,
                        trace_id="trace-success",
                        route_evidence={
                            "source_rack_position_code": "SOURCE_STATION_A",
                            "target_rack_position_code": "TARGET_STATION",
                        },
                    )
                    session = claim_runtime.session
                    await db.commit()
                    assert session.plugin_binding_id == persisted_binding.id
                    execution_session = await db.scalar(select(ExecutionSession))
                    work_item = await db.scalar(select(ExecutionWorkItem))
                    assert execution_session is not None and work_item is not None
                    assert claim_runtime.execution_session_id == execution_session.id
                    assert claim_runtime.correlation_id == work_item.correlation_id
                    assert execution_session.plugin_binding_id == session.plugin_binding_id
                    assert work_item.plugin_binding_id == session.plugin_binding_id
                    assert work_item.manifest_version == DEFINITION.contract_version

                snapshot = PinnedPluginSnapshot(
                    plugin_key=DEFINITION.plugin_key,
                    contract_version=DEFINITION.contract_version,
                    binding_identity=f"binding:{binding.id}:{binding.binding_version}",
                    binding_id=binding.id,
                    binding_version=binding.binding_version,
                    config_hash=binding.typed_config_hash,
                    index_digest=binding.generated_index_digest,
                    profile_identity=config.provider_profile,
                )
                decision = await WorklinePluginDispatcher().dispatch(
                    request=PluginDispatchRequest(
                        plugin_key=DEFINITION.plugin_key,
                        contract_version=DEFINITION.contract_version,
                        logical_route="SOURCE_PICK_REQUESTED",
                        raw_config=config.model_dump(mode="json"),
                        raw_state={},
                        context_state={},
                        raw_input={
                            "route": "SOURCE_PICK_REQUESTED",
                            "handoff_demand_id": demand.id,
                            "handoff_source_item_id": item.id,
                            "claim_attempt_no": item.claim_attempt_no,
                            "source_pick_request_event_id": "smt-source-pick-requested-13",
                        },
                        fact_source=PluginAttemptFactSource(
                            snapshot=snapshot,
                            device_fact_versions=(("SORTING_SOURCE_ARM", 31, 0),),
                        ),
                        snapshot=snapshot,
                    ),
                    gateway=object(),
                )
                assert getattr(decision, "kind", None) != "contract_violation", decision
                assert decision.outcome_code == "SOURCE_PICK_REQUESTED"
            finally:
                await engine.dispose()

    asyncio.run(scenario())
