"""不依赖业务插件的 DeviceCommand/ECS 生产接线 E2E。"""

from __future__ import annotations

import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from src.app.device.contracts import DeviceCommandRequest
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.device import Device
from src.app.device.models.evidence import DeviceStatusObservation
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.execution.models.inbound_evidence import InboundEvidence, InboundEvidenceConflict
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochDeviceBinding
from src.app.workline.models.workline import LineType, WorkLine
from src.utils.timezone import timezone
from tests.contracts.wms_integration.provider_profile_support import (
    build_provider_profile_payload,
    write_provider_profile,
)
from tests.integration.conftest import (
    integration_engine,
    integration_guard,
    integration_session_factory,
    patch_global_session_factory,
)
from tests.support.ecs_uniform_wire import (
    DeviceCommandBrokerWorker,
    UniformEcsServer,
    WesCallbackServer,
)

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

DISPATCH_TASK = "src.celery_app.tasks.device_command.dispatch_device_commands_batch"
EVIDENCE_TASK = "src.celery_app.tasks.device_command.process_device_evidence_batch"


async def test_real_broker_ecs_callback_worker_and_postgresql_close_command(
    tmp_path,
    integration_session_factory,
) -> None:
    database_url = os.environ["INTEGRATION_DATABASE_URL"]
    redis_url = os.environ["INTEGRATION_REDIS_URL"]
    suffix = uuid4().hex[:12]
    line_id: int | None = None
    device_id: int | None = None
    epoch_id: int | None = None
    binding_id: int | None = None
    command_code: str | None = None
    callback_server: WesCallbackServer | None = None
    ecs_server: UniformEcsServer | None = None
    worker: DeviceCommandBrokerWorker | None = None
    success = False

    async def _cleanup_database() -> None:
        if command_code is None:
            return
        async with integration_session_factory.begin() as db:
            evidence_ids = select(InboundEvidence.id).where(InboundEvidence.command_code == command_code)
            await db.execute(
                delete(InboundEvidenceConflict).where(InboundEvidenceConflict.first_evidence_id.in_(evidence_ids))
            )
            await db.execute(
                delete(DeviceStatusObservation).where(DeviceStatusObservation.command_code == command_code)
            )
            await db.execute(delete(DeviceCommand).where(DeviceCommand.command_code == command_code))
            await db.execute(delete(InboundEvidence).where(InboundEvidence.command_code == command_code))
            if binding_id is not None:
                await db.execute(delete(LineRunEpochDeviceBinding).where(LineRunEpochDeviceBinding.id == binding_id))
            if epoch_id is not None:
                await db.execute(delete(LineRunEpoch).where(LineRunEpoch.id == epoch_id))
            if device_id is not None:
                await db.execute(delete(Device).where(Device.id == device_id))
            if line_id is not None:
                await db.execute(delete(WorkLine).where(WorkLine.id == line_id))

    try:
        async with integration_session_factory.begin() as db:
            line = WorkLine(
                line_code=f"LINE-E2E-{suffix}",
                line_name="DeviceCommand E2E",
                line_type=LineType.AUTO,
            )
            db.add(line)
            await db.flush()
            line_id = line.id
            device = Device(
                device_code=f"ARM-E2E-{suffix}",
                device_name="DeviceCommand E2E Arm",
                work_line_id=line.id,
                device_role="ROBOT_ARM",
            )
            db.add(device)
            await db.flush()
            device_id = device.id
            epoch = LineRunEpoch(
                epoch_code=f"EPOCH-E2E-{suffix}",
                workline_id=line.id,
                plugin_key="device_command_test",
                plugin_version="1.0.0",
                flow_mode="TEST",
                topology_digest="a" * 64,
                configuration_digest="b" * 64,
                started_at=timezone.now_for_db(),
            )
            db.add(epoch)
            await db.flush()
            epoch_id = epoch.id
            binding = LineRunEpochDeviceBinding(
                line_run_epoch_id=epoch.id,
                device_id=device.id,
                device_code=device.device_code,
                contract_key="arm.pick",
                contract_version="2.0",
                status_max_age_ms=30_000,
                command_timeout_ms=30_000,
            )
            db.add(binding)
            await db.flush()
            binding_id = binding.id

        handle = await DeviceCommandService(session_factory=integration_session_factory).create_command(
            DeviceCommandRequest(
                device_code=f"ARM-E2E-{suffix}",
                line_run_epoch_id=epoch_id,
                execution_ref_type="E2E_EXECUTION",
                execution_ref_id=f"EXEC-{suffix}",
                contract_key="arm.pick",
                contract_version="2.0",
                task_type="PICK",
                params={"source_location": "STATION-A", "target_location": "STATION-B"},
                deadline_at=timezone.now_for_db() + timedelta(seconds=25),
                trace_id=f"TRACE-{suffix}",
            )
        )
        command_code = handle.command_code

        callback_server = WesCallbackServer(session_factory=integration_session_factory).start()
        ecs_server = UniformEcsServer(callback_url=callback_server.result_url).start()
        provider_payload = build_provider_profile_payload()
        provider_payload["server_url"] = ecs_server.url
        provider_file = write_provider_profile(tmp_path / "provider.yaml", provider_payload)
        worker = DeviceCommandBrokerWorker(
            database_url=database_url,
            redis_url=redis_url,
            provider_file=provider_file,
            ecs_base_url=ecs_server.url,
        )
        worker.start()

        assert worker.run_task(DISPATCH_TASK) == 1
        async with integration_session_factory() as db:
            dispatched = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == command_code))
        assert dispatched is not None
        assert dispatched.status in {
            CommandStatus.ACKNOWLEDGED,
            CommandStatus.SUCCEEDED,
        }, (dispatched.status, dispatched.failure_code, dispatched.reconciliation_reason)
        assert ecs_server.status_requests == [f"ARM-E2E-{suffix}"]
        assert len(ecs_server.command_requests) == 1
        assert ecs_server.callback_errors == [], ecs_server.callback_errors
        assert len(ecs_server.callback_responses) == 1, ecs_server.callback_responses
        assert worker.run_task(EVIDENCE_TASK) == 1

        async with integration_session_factory() as db:
            command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == command_code))
            evidence = await db.scalar(select(InboundEvidence).where(InboundEvidence.command_code == command_code))
            observations = list(
                (
                    await db.execute(
                        select(DeviceStatusObservation).where(DeviceStatusObservation.command_code == command_code)
                    )
                )
                .scalars()
                .all()
            )

        assert command is not None and command.status == CommandStatus.SUCCEEDED
        assert evidence is not None and evidence.apply_status == "APPLIED"
        assert command.result_evidence_id == evidence.id
        assert len(observations) == 1
        assert ecs_server.status_requests == [f"ARM-E2E-{suffix}"]
        assert len(ecs_server.command_requests) == 1
        assert ecs_server.callback_errors == []
        assert ecs_server.callback_responses == [
            {"status": 200, "body": {"code": 200, "message": "ACK", "trace_id": f"TRACE-{suffix}"}}
        ]
        success = True
    finally:
        cleanup_errors: list[BaseException] = []
        for cleanup in (
            (lambda: worker.close(success=success)) if worker is not None else None,
            ecs_server.close if ecs_server is not None else None,
            callback_server.close if callback_server is not None else None,
        ):
            if cleanup is None:
                continue
            try:
                cleanup()
            except BaseException as error:
                cleanup_errors.append(error)
        try:
            await _cleanup_database()
        except BaseException as error:
            cleanup_errors.append(error)
        if cleanup_errors:
            raise BaseExceptionGroup("DeviceCommand E2E cleanup failed", cleanup_errors)
