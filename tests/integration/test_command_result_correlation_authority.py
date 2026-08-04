"""设备命令结果 ExecutionCorrelation 权威性的数据库集成测试。"""

import pytest

from src.app.device.models.command import DeviceCommand
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from tests.support.runtime_binding import binding_pin_fields


async def _seed_correlation(
    db_session,
    *,
    correlation_id: str,
    trace_id: str,
) -> ExecutionCorrelation:
    session = ExecutionSession(
        workline_id=1,
        plugin_key="test-plugin",
        manifest_version="v1",
        **binding_pin_fields(),
        state="RUNNING",
    )
    db_session.add(session)
    await db_session.flush()
    correlation = ExecutionCorrelation(
        correlation_id=correlation_id,
        execution_session_id=session.id,
        trace_id=trace_id,
    )
    db_session.add(correlation)
    await db_session.flush()
    return correlation


@pytest.mark.asyncio
@pytest.mark.parametrize("trace_mode", ["missing", "new", "ambiguous"])
async def test_command_result_uses_pinned_command_correlation_when_trace_is_not_authoritative(
    db_session,
    trace_mode: str,
) -> None:
    authority = await _seed_correlation(
        db_session,
        correlation_id=f"corr-command-owner-{trace_mode}",
        trace_id=f"trace-command-owner-{trace_mode}",
    )
    command = DeviceCommand(
        command_code=f"CMD-CORRELATION-{trace_mode.upper()}",
        device_id=71,
        task_type="VENDOR_COMMAND",
        correlation_id=authority.correlation_id,
    )
    db_session.add(command)
    await db_session.flush()

    callback_trace_id: str | None = None
    if trace_mode == "new":
        callback_trace_id = "trace-new-callback-request"
        await _seed_correlation(
            db_session,
            correlation_id="corr-new-callback-request",
            trace_id=callback_trace_id,
        )
    elif trace_mode == "ambiguous":
        callback_trace_id = "trace-shared-callback-request"
        await _seed_correlation(
            db_session,
            correlation_id="corr-shared-callback-a",
            trace_id=callback_trace_id,
        )
        await _seed_correlation(
            db_session,
            correlation_id="corr-shared-callback-b",
            trace_id=callback_trace_id,
        )

    accepted = await RuntimeInboxService().accept_command_result(
        db_session,
        command_code=command.command_code,
        command_id=command.id,
        source_event_id=f"evt-command-correlation-{trace_mode}",
        device_code="ARM-71",
        correlation_id=command.correlation_id,
        trace_id=callback_trace_id,
        payload_json={"command_code": command.command_code, "result": "SUCCESS"},
    )

    assert accepted.record.correlation_id == authority.correlation_id
    assert accepted.record.execution_session_id == authority.execution_session_id
