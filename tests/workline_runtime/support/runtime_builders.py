from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


def make_mock_db(
    *,
    include_execute_result: bool = True,
    include_timeout_command: bool = True,
    spec: type[object] | None = None,
) -> AsyncMock:
    """创建 WorkLine runtime 测试常用的异步 DB session mock。"""
    db = AsyncMock(spec=spec)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    if include_execute_result:
        execute_result = MagicMock()
        execute_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=execute_result)
    else:
        db.execute = AsyncMock()

    db.get = AsyncMock(return_value=make_mock_command_record() if include_timeout_command else None)
    return db


def make_mock_command_record(**overrides: object) -> SimpleNamespace:
    data = {
        "id": 9,
        "command_code": "CMD-001",
        "device_id": 7,
        "status": "ACK_RECEIVED",
        "ack_received_at": datetime.now(UTC) - timedelta(minutes=4),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_mock_workline(**overrides: object) -> MagicMock:
    workline = MagicMock()
    workline.id = 1
    workline.line_code = "SMT-001"
    workline.plugin_key = "test_plugin"
    workline.run_mode = "SIMULATION"
    workline.config = {"scan_timeout": 30, "retry_count": 3}
    for key, value in overrides.items():
        setattr(workline, key, value)
    return workline


def make_workline(
    workline_id: int = 1,
    plugin_key: str | None = "test_plugin",
) -> MagicMock:
    return make_mock_workline(id=workline_id, plugin_key=plugin_key, run_mode="AUTO")


def make_mock_session(**overrides: object) -> MagicMock:
    session = MagicMock()
    session.id = 123
    session.workline_id = 2001
    session.trace_id = None
    session.status = "RUNNING"
    session.run_mode = "SIMULATION"
    session.context_json = {"barcode": "ABC123"}
    for key, value in overrides.items():
        setattr(session, key, value)
    return session


def make_mock_device(**overrides: object) -> MagicMock:
    device = MagicMock()
    device.id = 1
    device.device_code = "SCAN-001"
    device.device_role = "SCANNER"
    for key, value in overrides.items():
        setattr(device, key, value)
    return device


def make_mock_devices_by_role() -> dict[str, list[MagicMock]]:
    return {
        "SCANNER": [
            make_mock_device(id=1, device_code="SCAN-001", device_role="SCANNER"),
            make_mock_device(id=2, device_code="SCAN-002", device_role="SCANNER"),
        ],
        "CONVEYOR": [make_mock_device(id=3, device_code="CONV-001", device_role="CONVEYOR")],
    }


def make_devices_by_role() -> dict[str, list[MagicMock]]:
    return {
        "SCANNER": [make_mock_device(id=1, device_code="SCANNER_01", device_role="SCANNER")],
        "CONVEYOR": [make_mock_device(id=2, device_code="CONVEYOR_01", device_role="CONVEYOR")],
    }


def make_mock_outbox(**overrides: object) -> SimpleNamespace:
    data = {
        "id": 1,
        "dispatch_type": "DEVICE_COMMAND",
        "target_type": "DEVICE",
        "target_code": "DEVICE_001",
        "status": "NEW",
        "payload_json": {},
        "attempt_count": 0,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_mock_inbox(**overrides: object) -> MagicMock:
    inbox = MagicMock()
    inbox.kind = overrides.pop("kind", None)
    inbox.device_id = overrides.pop("device_id", None)
    inbox.command_id = overrides.pop("command_id", None)
    inbox.session_id = overrides.pop("session_id", None)
    inbox.workline_id = overrides.pop("workline_id", None)
    inbox.trace_id = overrides.pop("trace_id", None)
    inbox.source_message_id = overrides.pop("source_message_id", None)
    inbox.payload_json = overrides.pop("payload_json", {})
    for key, value in overrides.items():
        setattr(inbox, key, value)
    return inbox


def make_inbox(
    *,
    kind: object,
    device_id: int | None = None,
    command_id: int | None = None,
    session_id: int | None = None,
    trace_id: str | None = None,
    source_message_id: str | None = None,
    payload_json: dict | None = None,
) -> MagicMock:
    return make_mock_inbox(
        kind=kind,
        device_id=device_id,
        command_id=command_id,
        session_id=session_id,
        trace_id=trace_id,
        source_message_id=source_message_id,
        payload_json=payload_json or {},
    )
