"""RuntimeInbox PostgreSQL 正常处理与崩溃恢复共用的真实生产链路。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from unittest.mock import patch

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.device.models.command import DeviceCommand
from src.app.device.models.device import Device, DeviceStatus
from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.session import SessionStatus, WorklineSession
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    workline_runtime_status_projection_service,
)
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.sys.models import SystemOutbox
from src.app.workline.models.plugin_binding import WorklinePluginBinding
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.services.plugin_binding_service import WorklinePluginBindingService
from src.core.conf import settings
from src.utils.timezone import timezone
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass(frozen=True, slots=True)
class SeededScanFlow:
    inbox_id: int
    session_id: int
    workline_id: int
    arm_id: int
    trace_id: str


@dataclass(slots=True)
class RecordingTaskQueueGateway:
    """记录出队唤醒请求，确保 heavy test 不接触真实 Celery broker。"""

    outbox_enqueues: list[tuple[int | None, int]] = field(default_factory=list)

    def enqueue_outbox(self, outbox_id: int | None = None, *, limit: int = 50) -> None:
        self.outbox_enqueues.append((outbox_id, limit))


def processor(service: RuntimeInboxService) -> RuntimeInboxProcessorBridge:
    """构造 destructive switch 后的 generated-plugin 生产桥接。"""

    return RuntimeInboxProcessorBridge(inbox_service=service)


async def seed_scan_flow(db: AsyncSession) -> SeededScanFlow:
    trace_id = "it-runtime-inbox-trace"
    typed_config = {
        "device_roles": {
            "input_arm": "ROUGH_SORTER_INPUT_ARM",
            "conveyor": "ROUGH_SORTER_CONVEYOR",
            "output_arm": "ROUGH_SORTER_OUTPUT_ARM",
        },
        "pipeline_input_location": "PIPELINE-IN-IT",
        "pipeline_output_location": "PIPELINE-OUT-IT",
        "ng_location": "NG-IT",
        "warehouse_code": "WH-IT",
        "owner_code": "OWNER-IT",
        "provider_profile": "wms.v1.sandbox",
    }
    config_hash = sha256_digest(typed_config)
    workline = WorkLine(
        line_code="IT-RUNTIME-INBOX-SCAN",
        line_name="RuntimeInbox Production Flow",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        config=typed_config,
        is_active=True,
    )
    db.add(workline)
    await db.flush()
    binding = WorklinePluginBinding(
        workline_id=workline.id,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        binding_version=1,
        typed_config_json=typed_config,
        typed_config_hash=config_hash,
        provider_profile_snapshot_json=[{"profile_identity": "wms.v1.sandbox"}],
        port_requirements_json=["InventoryPort.query"],
        device_snapshot_json=[],
        generated_index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        environment=WorklinePluginBindingService.resolve_runtime_environment(settings.APP_ENV),
        activated_at=timezone.now_for_db(),
        activated_by="integration-test",
        activated_reason="PostgreSQL runtime processing evidence",
    )
    db.add(binding)
    await db.flush()
    workline.active_plugin_binding_id = binding.id
    workline.active_plugin_binding_version = binding.binding_version
    workline.active_plugin_config_hash = config_hash
    workline.active_plugin_index_digest = binding.generated_index_digest
    workline.active_plugin_provider_requirements_json = ["wms.v1.sandbox"]
    workline.active_plugin_port_requirements_json = list(binding.port_requirements_json)
    await workline_runtime_status_projection_service.project_ready_after_start(db, workline_id=workline.id)
    scanner = Device(
        device_code="IT-SCANNER-01",
        device_name="Integration Scanner",
        work_line_id=workline.id,
        device_role="ROUGH_SORTER_SCANNER",
        device_status=DeviceStatus.IDLE,
        version=1,
    )
    arm = Device(
        device_code="IT-ARM-01",
        device_name="Integration Input Arm",
        work_line_id=workline.id,
        device_role="ROUGH_SORTER_INPUT_ARM",
        device_status=DeviceStatus.IDLE,
        version=1,
    )
    db.add_all([scanner, arm])
    await db.flush()
    session = WorklineSession(
        session_code="IT-RUNTIME-INBOX-SESSION",
        workline_id=workline.id,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        plugin_binding_id=binding.id,
        plugin_binding_version=binding.binding_version,
        plugin_config_hash=config_hash,
        plugin_index_digest=binding.generated_index_digest,
        status=SessionStatus.RUNNING,
        trace_id=trace_id,
    )
    correlation = ExecutionCorrelation(
        correlation_id="it-runtime-inbox-correlation",
        trace_id=trace_id,
        source_event_id="it-runtime-inbox-event",
        business_owner_key="it-runtime-inbox-scan",
    )
    db.add_all([session, correlation])
    await db.flush()
    execution_session = ExecutionSession(
        workline_id=workline.id,
        plugin_key=session.plugin_key,
        manifest_version=session.contract_version,
        plugin_binding_id=binding.id,
        plugin_binding_version=binding.binding_version,
        plugin_config_hash=config_hash,
        plugin_index_digest=binding.generated_index_digest,
        state="RUNNING",
    )
    db.add(execution_session)
    await db.flush()
    correlation.execution_session_id = execution_session.id
    db.add(
        ExecutionWorkItem(
            execution_session_id=execution_session.id,
            correlation_id=correlation.correlation_id,
            plugin_key=session.plugin_key,
            plugin_binding_id=binding.id,
            plugin_binding_version=binding.binding_version,
            plugin_config_hash=config_hash,
            plugin_index_digest=binding.generated_index_digest,
            object_type="material",
            object_key="PKG-IT-001",
            current_step="SCAN",
        )
    )
    await db.flush()
    accepted = await RuntimeInboxService().accept_device_event(
        db,
        device_code=scanner.device_code,
        event_type="SCAN_COMPLETED",
        payload_json={
            "event_type": "SCAN_COMPLETED",
            "canonical_event_type": "SCAN_COMPLETED",
            "device_code": scanner.device_code,
            "data": {
                "session_id": session.id,
                "HHPN": "MAT-IT-001",
                "MfrPN": "VENDOR-IT-001",
                "Qty": "10",
                "DateCode": "20260711",
                "LotCode": "LOT-IT-001",
                "PkgID": "PKG-IT-001",
            },
        },
        trace_id=trace_id,
        event_id="it-runtime-inbox-event",
        workline_id=workline.id,
        device_id=scanner.id,
    )
    accepted.record.execution_session_id = execution_session.id
    accepted.record.correlation_id = correlation.correlation_id
    await db.commit()
    assert all(value is not None for value in (accepted.record.id, session.id, workline.id, arm.id))
    return SeededScanFlow(
        inbox_id=int(accepted.record.id),
        session_id=int(session.id),
        workline_id=int(workline.id),
        arm_id=int(arm.id),
        trace_id=trace_id,
    )


async def claim(db: AsyncSession, service: RuntimeInboxService, *, token: str) -> dict[str, object]:
    claims = await service.claim_for_processing(db, limit=1, processor_token=token, stale_after_seconds=60)
    assert len(claims) == 1
    await db.commit()
    return claims[0]


async def expire_and_recover(db: AsyncSession, service: RuntimeInboxService, *, inbox_id: int) -> None:
    # 用确定性 DB 时间推进替代 sleep，模拟 worker lease 已经过期。
    await db.execute(update(RuntimeInbox).where(RuntimeInbox.id == inbox_id).values(lease_until=0))
    await db.commit()
    assert await service.recover_stale_leases(db, stale_after_seconds=60, limit=1) == 1
    await db.commit()


async def assert_effects(db: AsyncSession, seeded: SeededScanFlow, *, expected_count: int) -> None:
    """按本场景稳定业务键精确核验 command/outbox/目标 timeline。"""

    session = await db.get(WorklineSession, seeded.session_id)
    assert session is not None
    if expected_count:
        command = await db.scalar(
            select(DeviceCommand).where(
                DeviceCommand.workline_id == seeded.workline_id,
                DeviceCommand.trace_id == seeded.trace_id,
            )
        )
        assert command is not None and command.device_id == seeded.arm_id
    else:
        assert session.status == SessionStatus.RUNNING
        assert session.awaiting_device_command_code is None

    command_count = await db.scalar(
        select(func.count())
        .select_from(DeviceCommand)
        .where(DeviceCommand.workline_id == seeded.workline_id, DeviceCommand.trace_id == seeded.trace_id)
    )
    outbox_count = await db.scalar(
        select(func.count()).select_from(SystemOutbox).where(SystemOutbox.session_id == seeded.session_id)
    )
    assert command_count == expected_count
    assert outbox_count == expected_count


async def assert_processed_terminal(db: AsyncSession, *, inbox_id: int) -> None:
    db.expire_all()
    inbox = await db.get(RuntimeInbox, inbox_id)
    assert inbox is not None
    assert inbox.status == "PROCESSED"
    assert inbox.processor_token is None
    assert inbox.lease_until is None


async def with_temporary_runtime_database(
    scenario: Callable[[async_sessionmaker[AsyncSession], RecordingTaskQueueGateway], Awaitable[None]],
) -> None:
    async with temporary_database() as (_database, database_url):
        run_alembic("upgrade", "head", database_url=database_url)
        engine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=0,
            pool_timeout=10,
        )
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        queue_gateway = RecordingTaskQueueGateway()
        try:
            with patch("src.core.task_queue_gateway.task_queue_gateway", queue_gateway):
                await scenario(session_factory, queue_gateway)
        finally:
            await engine.dispose()


__all__ = [
    "RecordingTaskQueueGateway",
    "SeededScanFlow",
    "assert_effects",
    "assert_processed_terminal",
    "claim",
    "expire_and_recover",
    "processor",
    "seed_scan_flow",
    "with_temporary_runtime_database",
]
