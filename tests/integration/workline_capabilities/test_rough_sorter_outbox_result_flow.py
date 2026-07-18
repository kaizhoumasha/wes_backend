"""粗分机 DeviceCommand/Outbox 与 logical result 回流证据。"""

from __future__ import annotations

import asyncio

from sqlalchemy import func
from sqlmodel import select

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.device import Device
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.runtime.orchestration.services.intent.system_capability_effect_service import SystemCapabilityEffectService
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.runtime.system_capabilities.outcomes import Success
from src.app.sys.models import SystemOutbox
from tests.integration.workline_capabilities.test_system_capability_effect_postgresql import _effect_context
from tests.support.runtime_inbox_processing_postgresql import seed_scan_flow, with_temporary_runtime_database


def _device_intent(ctx: dict[str, object], target_device_id: int, *, device_version: int) -> RuntimeIntent:
    session = ctx["session"]
    return RuntimeIntent.system_capability(
        capability_key="device.device_command_write",
        contract_version="v1",
        operation_key="pick-to-pipeline-1",
        payload={
            "target_device_id": target_device_id,
            "action": "PICK_AND_PUT",
            "payload": {"target": "PIPELINE-IN-IT"},
            "timeout_ms": 30_000,
        },
        precondition={"expected_available": True},
        fact_version=f"device:v{device_version}",
        timeout_seconds=5,
        creator_authority="WORKLINE_PLUGIN",
        authorization_policy="PLUGIN_DECLARED_CAPABILITY",
        binding_snapshot={
            "binding_id": session.plugin_binding_id,  # type: ignore[attr-defined]
            "binding_version": session.plugin_binding_version,  # type: ignore[attr-defined]
        },
        provider_snapshot={"provider_code": "RUNTIME", "profile": "runtime"},
    )


def test_outbox_acceptance_is_not_remote_completion_and_callback_is_runtime_inbox() -> None:
    """queued/accepted 只证明 durable acceptance；业务结果必须由 callback Inbox 表达。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            ctx = await _effect_context(db)
            arm = await db.get(Device, seeded.arm_id)
            assert arm is not None
            ctx["devices_by_role"] = {"ROUGH_SORTER_INPUT_ARM": [arm]}
            result = await SystemCapabilityEffectService().apply(
                ctx,
                _device_intent(ctx, seeded.arm_id, device_version=arm.version),
            )
            assert isinstance(result.outcome, Success)
            assert result.durably_accepted is True
            assert result.remote_completed is False
            command_code = result.outcome.payload.command_code
            await db.commit()

        async with session_factory() as db:
            command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == command_code))
            assert command is not None and command.status == CommandStatus.PENDING
            assert await db.scalar(select(func.count()).select_from(SystemOutbox)) == 1
            accepted = await RuntimeInboxService().accept_command_result(
                db,
                command_code=command_code,
                source_event_id="it-device-result-1",
                device_code="IT-ARM-01",
                workline_id=seeded.workline_id,
                device_id=seeded.arm_id,
                command_id=command.id,
                trace_id=seeded.trace_id,
                payload_json={
                    "logical_route": "PICK_AND_PUT_RESULT",
                    "command_code": command_code,
                    "command_type": "PICK_AND_PUT",
                    "result": "SUCCESS",
                    "data": {"diameter_mm": "100", "thickness_mm": "10"},
                },
            )
            await db.commit()
            callback = await db.get(RuntimeInbox, accepted.record.id)
            assert callback is not None and callback.kind == "COMMAND_RESULT"
            assert callback.status == "RECEIVED"
            assert command.status == CommandStatus.PENDING

    asyncio.run(with_temporary_runtime_database(scenario))


def test_missing_callback_becomes_visible_timeout_without_fake_success() -> None:
    """callback 丢失时由 TIMER_TIMEOUT/重试链路可见，不能把 Outbox 行当完成证据。"""

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            ctx = await _effect_context(db)
            arm = await db.get(Device, seeded.arm_id)
            assert arm is not None
            result = await SystemCapabilityEffectService().apply(
                ctx,
                _device_intent(ctx, seeded.arm_id, device_version=arm.version),
            )
            assert isinstance(result.outcome, Success)
            command_code = result.outcome.payload.command_code
            command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == command_code))
            session = await db.get(WorklineSession, seeded.session_id)
            assert command is not None and session is not None
            timeout = await RuntimeInboxService().accept_timer_timeout(
                db,
                session_id=seeded.session_id,
                execution_session_id=ctx["inbox"].execution_session_id,  # type: ignore[attr-defined]
                workline_id=seeded.workline_id,
                wait_token="it-wait-token",
                wait_type="COMMAND_RESULT",
                awaiting_device_command_code=command_code,
                command_code=command_code,
                device_id=seeded.arm_id,
                command_id=command.id,
            )
            await db.commit()

        async with session_factory() as verify_db:
            timer = await verify_db.get(RuntimeInbox, timeout.record.id)
            command = await verify_db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == command_code))
            assert timer is not None and timer.kind == "TIMER_TIMEOUT" and timer.status == "RECEIVED"
            assert command is not None and command.status != CommandStatus.COMPLETED

    asyncio.run(with_temporary_runtime_database(scenario))
