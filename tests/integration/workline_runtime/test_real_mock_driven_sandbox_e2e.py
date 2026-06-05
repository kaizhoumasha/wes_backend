"""真实 mock 驱动的 WorkLine 进程内 E2E。

这些用例不启动 Docker、Celery worker 或端口服务；WMS mock 通过
FastAPI ASGITransport 接入，WES 回调走 callback_external，WorkLine
Inbox 使用进程内 batch processor 消费。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.device.models import Device, DeviceProtocol
from src.app.device.models.command import DeviceCommand
from src.app.device.models.device import DeviceStatus
from src.app.rack.models.operation import RackOperation, RackTask, RackTaskStatus
from src.app.resource.models import (
    BinCellOccupancy,
    BinCellOccupancyStatus,
    BinMaterialMount,
    BinMaterialMountStatus,
    RackKind,
    ResourceSourceSystem,
    ResourceStateEvent,
)
from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType, SystemOutboxStatus
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.app.workline.models.inbox import InboxKind, InboxStatus, WorklineInbox
from src.app.workline.models.rack_position import WorklineRackPosition, WorklineRackPositionRole
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.models.session import RunMode, SessionStatus, WorklineSession
from src.app.workline.services.inbox_batch_processor import InboxBatchProcessor
from src.app.workline.services.inbox_service import inbox_service
from src.app.workline.services.outbox_dispatch_service import outbox_dispatch_service
from src.core.response.response_code import ClientErrorCode
from src.utils.timezone import timezone
from src.workline_plugins.rough_sorter.contract import (
    ACTION_MOVE_FORWARD,
    ACTION_PICK_AND_PUT,
    ACTION_PUT_TO_BIN,
    EVENT_SCAN_COMPLETED,
    PHASE_COMPLETED,
    PHASE_MOVING_FORWARD,
    PHASE_PICK_TO_PIPELINE,
    PHASE_PUTTING_TO_BIN,
    PHASE_WAITING_RACK,
    ROLE_CONVEYOR,
    ROLE_INPUT_ARM,
    ROLE_OUTPUT_ARM,
)
from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_SOURCE_PICK,
    COMMAND_TARGET_PLACE,
    EVENT_WORKING_BIN_SCAN,
    ROLE_SORTING_SCAN_PLATFORM,
    ROLE_SORTING_SOURCE_ARM,
    ROLE_SORTING_TARGET_ARM,
    SMT_SORTING_INBOUND_CONTRACT_VERSION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)
from src.workline_runtime.sandbox_catalog import rough_sorter_scan_completed_payload
from tests.mock import wms_mock_server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from fastapi import Request


class _NoopBucketLockProvider:
    @asynccontextmanager
    async def __call__(self, _bucket_key: str) -> AsyncIterator[None]:
        yield


class _FakeExternalCallbackRequest:
    def __init__(self, body: dict[str, Any]) -> None:
        self.client = MagicMock()
        self.client.host = "127.0.0.1"
        self.url = MagicMock()
        self.url.path = "/api/v1/callback/external"
        self.headers = {"User-Agent": "WMS-Mock-ASGI"}
        self.method = "POST"
        self._body = body

    async def json(self) -> dict[str, Any]:
        return self._body


class _CallbackActiveRackSnapshotProvider:
    async def active_bin_rack(self, *, context: Any | None = None) -> dict[str, Any] | None:
        if isinstance(context, dict) and isinstance(context.get("active_bin_rack"), dict):
            return dict(cast("dict[str, Any]", context["active_bin_rack"]))
        return None


@pytest.fixture(autouse=True)
def _reset_mock_wms_state() -> Iterator[None]:
    wms_mock_server.reset_mock_wms_state()
    yield
    wms_mock_server.reset_mock_wms_state()


@pytest.fixture(autouse=True)
def _disable_runtime_enqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    inbox_batch_processor_module = import_module("src.app.workline.services.inbox_batch_processor")

    monkeypatch.setattr(
        "src.core.task_queue_gateway.task_queue_gateway.enqueue_workline_inbox",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.core.task_queue_gateway.task_queue_gateway.enqueue_outbox",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        inbox_batch_processor_module,
        "_build_orchestrator_lock_provider",
        lambda _db: lambda lock_key: _NoopBucketLockProvider()(lock_key),
    )


@pytest.fixture
def _disable_sse_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    event_stream_module = import_module("src.app.sys.services.event_stream_service")
    monkeypatch.setattr(event_stream_module, "publish_deferred_sse_events", AsyncMock(return_value=None))


@pytest.fixture
def _use_callback_active_rack_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_services_module = import_module("src.workline_runtime.services")
    inbox_batch_processor_module = import_module("src.app.workline.services.inbox_batch_processor")
    real_builder = runtime_services_module.build_workline_runtime_services

    def _build_services(*args: Any, **kwargs: Any) -> Any:
        services = real_builder(*args, **kwargs)
        return replace(services, active_rack_snapshot_provider=_CallbackActiveRackSnapshotProvider())

    monkeypatch.setattr(inbox_batch_processor_module, "build_workline_runtime_services", _build_services)


def _session_factory_context(session_factory: async_sessionmaker[AsyncSession]) -> Any:
    @asynccontextmanager
    async def _factory() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    return _factory


def _inbox_processor(session_factory: async_sessionmaker[AsyncSession]) -> InboxBatchProcessor:
    return InboxBatchProcessor(
        session_factory=_session_factory_context(session_factory),
        bucket_lock_provider=lambda _db, _bucket_key: _NoopBucketLockProvider()(_bucket_key),
    )


def _rough_scan_payload(*, pkg_id: str, sku: str = "CAP001", lot_no: str = "LOT-A") -> dict[str, Any]:
    payload = rough_sorter_scan_completed_payload()
    payload["data"] = dict(payload["data"])
    payload["data"].update({"PkgID": pkg_id, "HHPN": sku, "LotCode": lot_no})
    return payload


def _rough_result_inbox(
    command: DeviceCommand,
    *,
    task_type: str,
    device_code: str,
    result: str = "SUCCESS",
    data: dict[str, Any] | None = None,
) -> WorklineInbox:
    return WorklineInbox(
        kind=InboxKind.COMMAND_RESULT,
        source_system="DEVICE",
        source_message_id=f"result:{command.command_code}",
        idempotency_key=f"result:{command.command_code}",
        command_id=command.id,
        device_id=command.device_id,
        workline_id=command.workline_id,
        session_id=command.session_id,
        trace_id=command.trace_id,
        payload_json={
            "command_code": command.command_code,
            "device_code": device_code,
            "task_type": task_type,
            "result": result,
            "data": data or {},
        },
    )


async def _persist_rough_sorter_fixture(
    db: AsyncSession,
    *,
    prefix: str,
) -> tuple[WorkLine, Device]:
    workline = WorkLine(
        line_code=f"{prefix}_rough_line",
        line_name=f"{prefix} 粗分机",
        line_type=LineType.AUTO,
        plugin_key="rough_sorter",
        run_mode=WorkLineRunMode.SIMULATION,
        runtime_status=WorkLineRuntimeStatus.READY,
        is_active=True,
        runtime_config_json={},
    )
    db.add(workline)
    await db.flush()
    db.add(
        WorklineRackPosition(
            workline_id=cast("int", workline.id),
            workline_code=workline.line_code,
            position_code="SINGLE_LAYER_A",
            position_name=f"{prefix} 单层货架 A",
            position_role=WorklineRackPositionRole.SMT_CLASSIFIER_SINGLE_RACK_WORK,
            allowed_rack_kind=RackKind.SINGLE_LAYER,
            capacity=1,
            logic_location_code=f"{workline.line_code}:SINGLE_LAYER_A",
            external_location_code="SINGLE_LAYER_A",
            device_role=ROLE_OUTPUT_ARM,
            priority=10,
            enabled=True,
        )
    )
    scan_device = Device(
        device_code=f"{prefix}_RS_SCAN",
        device_name=f"{prefix} 粗分扫码",
        work_line_id=workline.id,
        device_role="ROUGH_SCAN_PLATFORM",
        role_index=1,
        host="mock-ecs",
        port=8010,
        protocol=DeviceProtocol.HTTP,
        device_status=DeviceStatus.IDLE,
        capabilities_json={"supports_event_types": [EVENT_SCAN_COMPLETED]},
    )
    devices = [
        scan_device,
        Device(
            device_code=f"{prefix}_RS_PICK",
            device_name=f"{prefix} 粗分取料",
            work_line_id=workline.id,
            device_role=ROLE_INPUT_ARM,
            role_index=1,
            host="mock-ecs",
            port=8010,
            protocol=DeviceProtocol.HTTP,
            device_status=DeviceStatus.IDLE,
            capabilities_json={"supports_command_types": [ACTION_PICK_AND_PUT]},
        ),
        Device(
            device_code=f"{prefix}_RS_CONVEYOR",
            device_name=f"{prefix} 粗分流水线",
            work_line_id=workline.id,
            device_role=ROLE_CONVEYOR,
            role_index=1,
            host="mock-ecs",
            port=8010,
            protocol=DeviceProtocol.HTTP,
            device_status=DeviceStatus.IDLE,
            capabilities_json={"supports_command_types": [ACTION_MOVE_FORWARD]},
        ),
        Device(
            device_code=f"{prefix}_RS_PUT",
            device_name=f"{prefix} 粗分入格",
            work_line_id=workline.id,
            device_role=ROLE_OUTPUT_ARM,
            role_index=1,
            host="mock-ecs",
            port=8010,
            protocol=DeviceProtocol.HTTP,
            device_status=DeviceStatus.IDLE,
            capabilities_json={"supports_command_types": [ACTION_PUT_TO_BIN]},
        ),
    ]
    db.add_all(devices)
    await db.commit()
    await db.refresh(workline)
    await db.refresh(scan_device)
    return workline, scan_device


async def _create_rough_scan_inbox(
    db: AsyncSession,
    *,
    trace_id: str,
    scan_device: Device,
    pkg_id: str,
    sku: str = "CAP001",
    lot_no: str = "LOT-A",
) -> WorklineInbox:
    payload = _rough_scan_payload(pkg_id=pkg_id, sku=sku, lot_no=lot_no)
    created = await inbox_service.create_device_event_inbox(
        db=db,
        device_code=scan_device.device_code,
        event_type=EVENT_SCAN_COMPLETED,
        timestamp=1710000000000,
        data=payload["data"],
        trace_id=trace_id,
        event_id=f"{trace_id}:scan",
        source_message_id=f"{trace_id}:scan",
        auto_commit=False,
    )
    await db.commit()
    return created


async def _latest_command(db: AsyncSession, *, session_id: int, task_type: str) -> DeviceCommand:
    statement = (
        select(DeviceCommand)
        .where(
            DeviceCommand.session_id_int == session_id,  # type: ignore[arg-type]
            DeviceCommand.task_type == task_type,  # type: ignore[arg-type]
        )
        .order_by(DeviceCommand.id.desc())
        .limit(1)
    )
    return (await db.execute(statement)).scalar_one()


async def _process_inboxes(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    limit: int = 10,
) -> dict[str, int]:
    async with session_factory() as db:
        return await _inbox_processor(session_factory).process_batch(db, limit=limit, parallelism=1)


async def _append_and_process_command_result(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    command: DeviceCommand,
    task_type: str,
    data: dict[str, Any] | None = None,
) -> None:
    async with session_factory() as db:
        device = await db.get(Device, command.device_id)
        assert device is not None
        db.add(_rough_result_inbox(command, task_type=task_type, device_code=device.device_code, data=data))
        await db.commit()
    result = await _process_inboxes(session_factory, limit=10)
    assert result["success"] == 1


async def _mark_device_command_outbox_sent(
    db: AsyncSession,
    *,
    session_id: int,
    task_type: str,
) -> None:
    outboxes = (
        (
            await db.execute(
                select(SystemOutbox).where(
                    SystemOutbox.session_id == session_id,  # type: ignore[arg-type]
                    SystemOutbox.dispatch_type == SystemOutboxDispatchType.DEVICE_COMMAND,  # type: ignore[arg-type]
                    SystemOutbox.payload_json["task_type"].as_string() == task_type,
                    SystemOutbox.status == SystemOutboxStatus.NEW,  # type: ignore[arg-type]
                )
            )
        )
        .scalars()
        .all()
    )
    assert outboxes
    now = timezone.now_for_db()
    for outbox in outboxes:
        outbox.status = SystemOutboxStatus.SENT
        outbox.sent_at = now
        outbox.next_retry_at = None
    await db.commit()


async def _post_callback_external(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    from src.app.callback.v1.callback import callback_external

    with patch("src.app.callback.v1.callback.get_request_id", return_value=str(payload.get("source_event_id"))):
        response = await callback_external(
            request=cast("Request", _FakeExternalCallbackRequest(payload)),
            db=db,
        )
    return cast("dict[str, Any]", response)


async def _wms_asgi_sender_factory(session_factory: async_sessionmaker[AsyncSession]) -> Any:
    async def _sender(_url: str, payload_json: dict[str, Any]) -> bool:
        async def _callback(_url: str, callback_payload: dict[str, Any]) -> dict[str, Any]:
            async with session_factory() as callback_db:
                return await _post_callback_external(callback_db, callback_payload)

        with patch.object(wms_mock_server, "_post_callback", _callback):
            transport = httpx.ASGITransport(app=wms_mock_server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://wms-mock") as client:
                response = await client.post("/api/wms/rack-operation", json=payload_json)
                return 200 <= response.status_code < 300

    return _sender


async def _dispatch_rack_outbox(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    sender = await _wms_asgi_sender_factory(session_factory)

    async def _should_dispatch_to_sandbox(_service: Any, _db: Any, outbox: SystemOutbox) -> bool:
        return outbox.target_code != "WMS_RCS_RACK_OPERATION"

    async with session_factory() as db:
        with (
            patch("src.app.sys.services.outbox_engine._send_external_http", new=sender),
            patch.object(
                type(outbox_dispatch_service),
                "_should_dispatch_to_sandbox",
                new=_should_dispatch_to_sandbox,
            ),
        ):
            return await outbox_dispatch_service.dispatch(db, limit=20)


async def _rough_sorter_reaches_rack_wait(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    prefix: str,
    pkg_id: str,
    trace_id: str,
    reel_diameter: str,
    reel_thickness: str,
) -> tuple[int, str]:
    async with session_factory() as db:
        _workline, scan_device = await _persist_rough_sorter_fixture(db, prefix=prefix)
        scan_inbox = await _create_rough_scan_inbox(db, trace_id=trace_id, scan_device=scan_device, pkg_id=pkg_id)
        scan_inbox_id = cast("int", scan_inbox.id)

    result = await _process_inboxes(session_factory, limit=10)
    assert result["success"] == 1

    async with session_factory() as db:
        scan_inbox = await db.get(WorklineInbox, scan_inbox_id)
        assert scan_inbox is not None and scan_inbox.session_id is not None
        session_id = scan_inbox.session_id
        session = await db.get(WorklineSession, session_id)
        assert session is not None
        assert session.context_json["phase"] == PHASE_PICK_TO_PIPELINE
        pick_command = await _latest_command(db, session_id=session_id, task_type=ACTION_PICK_AND_PUT)

    await _append_and_process_command_result(
        session_factory,
        command=pick_command,
        task_type=ACTION_PICK_AND_PUT,
        data={"reel_diameter": reel_diameter, "reel_thickness": reel_thickness},
    )

    async with session_factory() as db:
        move_command = await _latest_command(db, session_id=session_id, task_type=ACTION_MOVE_FORWARD)
        session = await db.get(WorklineSession, session_id)
        assert session is not None
        assert session.context_json["phase"] == PHASE_MOVING_FORWARD

    await _append_and_process_command_result(session_factory, command=move_command, task_type=ACTION_MOVE_FORWARD)

    async with session_factory() as db:
        session = await db.get(WorklineSession, session_id)
        assert session is not None
        context = cast("dict[str, Any]", session.context_json)
        assert context["phase"] == PHASE_WAITING_RACK
        assert session.status == SessionStatus.WAITING_EXTERNAL
        assert session.current_wait_type == "RACK_OPERATION"
        operation_key = cast("str", context["rack_operation"]["operation_key"])
        return session_id, operation_key


@pytest.mark.asyncio
async def test_rough_sorter_real_wms_mock_rack_callback_resumes_storage_flow(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    _disable_sse_publish: None,
    _use_callback_active_rack_provider: None,
) -> None:
    session_factory = async_sessionmaker(db_session.bind, class_=type(db_session), expire_on_commit=False)
    prefix = "real_mock_rough_success"
    trace_id = f"{prefix}:trace"
    session_id, operation_key = await _rough_sorter_reaches_rack_wait(
        session_factory,
        prefix=prefix,
        pkg_id=f"{prefix}_PKG",
        trace_id=trace_id,
        reel_diameter="330.0",
        reel_thickness="20.0",
    )
    async with session_factory() as db:
        await _mark_device_command_outbox_sent(db, session_id=session_id, task_type=ACTION_PICK_AND_PUT)
        await _mark_device_command_outbox_sent(db, session_id=session_id, task_type=ACTION_MOVE_FORWARD)

    dispatch_result = await _dispatch_rack_outbox(session_factory)
    assert dispatch_result["success"] == 1

    async with session_factory() as db:
        outbox = (
            (
                await db.execute(
                    select(SystemOutbox).where(SystemOutbox.operation_key == operation_key)  # type: ignore[arg-type]
                )
            )
            .scalars()
            .one()
        )
        assert outbox.dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP
        assert outbox.status == SystemOutboxStatus.SENT
        task = (
            (
                await db.execute(select(RackTask).where(RackTask.operation_key == operation_key))  # type: ignore[arg-type]
            )
            .scalars()
            .one()
        )
        assert task.task_status == RackTaskStatus.SUCCEEDED
        assert task.callback_json["callback_type"] == "WMS_RACK_ARRIVED"
        assert task.callback_json["active_bin_rack"]["rack_code"].startswith("RACK-3CELL-")
        external_inbox = (
            (
                await db.execute(
                    select(WorklineInbox).where(WorklineInbox.kind == InboxKind.EXTERNAL_HTTP)  # type: ignore[arg-type]
                )
            )
            .scalars()
            .one()
        )
        assert external_inbox.status == InboxStatus.NEW
        assert external_inbox.payload_json["callback_type"] == "WMS_RACK_ARRIVED"

    external_result = await _process_inboxes(session_factory, limit=10)
    assert external_result["success"] == 2

    async with session_factory() as db:
        session = await db.get(WorklineSession, session_id)
        assert session is not None
        context = cast("dict[str, Any]", session.context_json)
        assert context["phase"] == PHASE_PUTTING_TO_BIN
        target_bin_code = context["target_bin_location"].get("bin_code") or context["target_bin_location"]["bin_id"]
        assert target_bin_code.startswith("BIN")
        assert context["target_bin_location"]["bin_cell_index"] in {"1", "2", "7"}
        assert context.get("rack_operation", {}).get("operation_key") in {None, operation_key}
        assert session.status == SessionStatus.WAITING_DEVICE_RESULT
        assert session.current_wait_type == "COMMAND_RESULT"
        assert await _latest_command(db, session_id=session_id, task_type=ACTION_PUT_TO_BIN)

    async with session_factory() as db:
        put_command = await _latest_command(db, session_id=session_id, task_type=ACTION_PUT_TO_BIN)

    await _append_and_process_command_result(session_factory, command=put_command, task_type=ACTION_PUT_TO_BIN)

    async with session_factory() as db:
        session = await db.get(WorklineSession, session_id)
        assert session is not None
        assert session.status == SessionStatus.COMPLETED
        assert session.context_json["phase"] == PHASE_COMPLETED
        mounted = (
            (
                await db.execute(
                    select(BinMaterialMount).where(BinMaterialMount.pkg_code == f"{prefix}_PKG")  # type: ignore[arg-type]
                )
            )
            .scalars()
            .one()
        )
        assert mounted.mount_status == BinMaterialMountStatus.OCCUPIED


@pytest.mark.asyncio
async def test_rough_sorter_real_wms_mock_rejects_exhausted_rack_pool(
    db_session: AsyncSession,
    _disable_sse_publish: None,
) -> None:
    for rack in wms_mock_server.mock_wms_state.rack_pool.values():
        if rack["layout_code"] == "THREE_CELL":
            rack["status"] = "ALLOCATED"

    session_factory = async_sessionmaker(db_session.bind, class_=type(db_session), expire_on_commit=False)
    prefix = "real_mock_rough_exhausted"
    session_id, operation_key = await _rough_sorter_reaches_rack_wait(
        session_factory,
        prefix=prefix,
        pkg_id=f"{prefix}_PKG",
        trace_id=f"{prefix}:trace",
        reel_diameter="330.0",
        reel_thickness="20.0",
    )
    async with session_factory() as db:
        await _mark_device_command_outbox_sent(db, session_id=session_id, task_type=ACTION_PICK_AND_PUT)
        await _mark_device_command_outbox_sent(db, session_id=session_id, task_type=ACTION_MOVE_FORWARD)

    dispatch_result = await _dispatch_rack_outbox(session_factory)
    assert dispatch_result["success"] == 1

    async with session_factory() as db:
        task = (
            (
                await db.execute(select(RackTask).where(RackTask.operation_key == operation_key))  # type: ignore[arg-type]
            )
            .scalars()
            .one()
        )
        assert task.task_status == RackTaskStatus.FAILED
        assert task.callback_json["callback_type"] == "WMS_RACK_EXCHANGE_FAILED"
        assert task.error_code == "NO_AVAILABLE_RACK"

    lifecycle_result = await _process_inboxes(session_factory, limit=10)
    assert lifecycle_result["success"] == 1

    async with session_factory() as db:
        session = await db.get(WorklineSession, session_id)
        assert session is not None
        assert session.status == SessionStatus.MANUAL_HOLD
        assert session.failure_code == "NO_AVAILABLE_RACK"
        mounts = (
            (
                await db.execute(
                    select(BinMaterialMount).where(BinMaterialMount.pkg_code == f"{prefix}_PKG")  # type: ignore[arg-type]
                )
            )
            .scalars()
            .all()
        )
        assert mounts == []


async def _persist_smt_sorting_fixture(
    db: AsyncSession,
    *,
    prefix: str,
) -> tuple[WorkLine, Device]:
    workline = WorkLine(
        line_code=f"{prefix}_smt_line",
        line_name=f"{prefix} 分拣机",
        line_type=LineType.AUTO,
        plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
        contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
        run_mode=WorkLineRunMode.SIMULATION,
        runtime_status=WorkLineRuntimeStatus.READY,
        is_active=True,
        runtime_config_json={
            "route_roles": {
                COMMAND_SOURCE_PICK: ROLE_SORTING_SOURCE_ARM,
                COMMAND_TARGET_PLACE: ROLE_SORTING_TARGET_ARM,
            }
        },
    )
    db.add(workline)
    await db.flush()
    source = Device(
        device_code=f"{prefix}_SORT_SOURCE",
        device_name=f"{prefix} 源端机械臂",
        work_line_id=workline.id,
        device_role=ROLE_SORTING_SOURCE_ARM,
        role_index=1,
        host="mock-ecs",
        port=8010,
        protocol=DeviceProtocol.HTTP,
        capabilities_json={"supports_command_types": [COMMAND_SOURCE_PICK]},
    )
    scan = Device(
        device_code=f"{prefix}_SORT_SCAN",
        device_name=f"{prefix} 扫码台",
        work_line_id=workline.id,
        device_role=ROLE_SORTING_SCAN_PLATFORM,
        role_index=1,
        host="mock-ecs",
        port=8010,
        protocol=DeviceProtocol.HTTP,
        capabilities_json={"supports_event_types": [EVENT_WORKING_BIN_SCAN]},
    )
    target = Device(
        device_code=f"{prefix}_SORT_TARGET",
        device_name=f"{prefix} 目标机械臂",
        work_line_id=workline.id,
        device_role=ROLE_SORTING_TARGET_ARM,
        role_index=1,
        host="mock-ecs",
        port=8010,
        protocol=DeviceProtocol.HTTP,
        capabilities_json={"supports_command_types": [COMMAND_TARGET_PLACE]},
    )
    db.add_all([source, scan, target])
    await db.commit()
    await db.refresh(workline)
    await db.refresh(source)
    return workline, source


def _source_pick_inbox(
    *,
    session: WorklineSession,
    source_device: Device,
    data: dict[str, Any],
) -> WorklineInbox:
    return WorklineInbox(
        kind=InboxKind.COMMAND_RESULT,
        source_system="DEVICE",
        source_message_id=f"{session.trace_id}:source-pick",
        idempotency_key=f"{session.trace_id}:source-pick",
        device_id=source_device.id,
        workline_id=session.workline_id,
        session_id=session.id,
        trace_id=session.trace_id,
        payload_json={
            "command_code": f"{session.trace_id}:CMD-SOURCE-PICK",
            "device_code": source_device.device_code,
            "task_type": COMMAND_SOURCE_PICK,
            "result": "SUCCESS",
            "data": data,
        },
    )


def _working_bin_scan_inbox(
    *,
    session: WorklineSession,
    scan_device: Device,
    data: dict[str, Any],
) -> WorklineInbox:
    return WorklineInbox(
        kind=InboxKind.DEVICE_EVENT,
        source_system="DEVICE",
        source_message_id=f"{session.trace_id}:working-bin-scan",
        idempotency_key=f"{session.trace_id}:working-bin-scan",
        device_id=scan_device.id,
        workline_id=session.workline_id,
        session_id=session.id,
        trace_id=session.trace_id,
        payload_json={
            "event_id": f"{session.trace_id}:working-bin-scan",
            "device_code": scan_device.device_code,
            "event_type": EVENT_WORKING_BIN_SCAN,
            "data": data,
        },
    )


def _target_place_inbox(
    *,
    session: WorklineSession,
    command: DeviceCommand,
    device_code: str,
) -> WorklineInbox:
    return WorklineInbox(
        kind=InboxKind.COMMAND_RESULT,
        source_system="DEVICE",
        source_message_id=f"{session.trace_id}:target-place",
        idempotency_key=f"{session.trace_id}:target-place",
        command_id=command.id,
        device_id=command.device_id,
        workline_id=session.workline_id,
        session_id=session.id,
        trace_id=session.trace_id,
        payload_json={
            "command_code": command.command_code,
            "device_code": device_code,
            "task_type": COMMAND_TARGET_PLACE,
            "result": "SUCCESS",
            "data": {},
        },
    )


@pytest.mark.asyncio
async def test_smt_sorting_inbound_real_resource_projection_flow(
    db_session: AsyncSession,
    _disable_sse_publish: None,
) -> None:
    session_factory = async_sessionmaker(db_session.bind, class_=type(db_session), expire_on_commit=False)
    prefix = "real_mock_smt_sorting"
    trace_id = f"{prefix}:trace"
    material_key = f"mid:{prefix}:pkg-001"
    pkg_code = f"{prefix}_PKG"

    async with session_factory() as db:
        workline, source_device = await _persist_smt_sorting_fixture(db, prefix=prefix)
        scan_device = (
            (
                await db.execute(
                    select(Device).where(Device.device_code == f"{prefix}_SORT_SCAN")  # type: ignore[arg-type]
                )
            )
            .scalars()
            .one()
        )
        session = WorklineSession(
            session_code=f"{prefix}_session",
            workline_id=cast("int", workline.id),
            plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
            contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
            run_mode=RunMode.SIMULATION,
            business_key=material_key,
            barcode=pkg_code,
            status=SessionStatus.RUNNING,
            trace_id=trace_id,
            context_json={
                "sorting": {
                    "context_schema_version": 1,
                    "stations": {"scan_platform": "EMPTY"},
                    "active_target_bin": {
                        "snapshot_version": "snap-real-resource",
                        "cells": [
                            {
                                "bin_code": f"{prefix}_TGT_BIN",
                                "bin_cell_index": "B02",
                                "bin_cell_code": "B02",
                                "status": "EMPTY",
                                "capacity_depth_mm": "30.500",
                                "used_depth_mm": "0",
                            }
                        ],
                    },
                }
            },
            started_at=timezone.now_for_db(),
        )
        db.add(session)
        source_occupancy = BinCellOccupancy(
            bin_code=f"{prefix}_SRC_BIN",
            bin_cell_index="A01",
            bin_cell_code="A01",
            material_identity_key=material_key,
            material_code="CAP001",
            lot_code="LOT-A",
            reel_count=1,
            used_depth_mm="7.125",
            capacity_depth_mm="10",
            remaining_depth_mm="2.875",
            occupancy_status=BinCellOccupancyStatus.OCCUPIED,
            source_system="WES_RUNTIME",
            source_event_id=f"{trace_id}:seed-source-occupancy",
            trace_id=trace_id,
            session_id="seed",
            started_at=timezone.now_for_db(),
        )
        db.add(source_occupancy)
        await db.flush()
        db.add(
            BinMaterialMount(
                bin_cell_occupancy_id=source_occupancy.id,
                cell_stack_position=1,
                bin_code=f"{prefix}_SRC_BIN",
                bin_cell_index="A01",
                bin_cell_code="A01",
                material_identity_key=material_key,
                pkg_code=pkg_code,
                material_code="CAP001",
                lot_code="LOT-A",
                reel_thickness="7.125",
                wms_inventory_id=f"{prefix}_WMS_INV",
                mount_status=BinMaterialMountStatus.OCCUPIED,
                source_system=ResourceSourceSystem.WES_RUNTIME,
                source_event_id=f"{trace_id}:seed-source-mount",
                source_version="12",
                trace_id=trace_id,
                session_id="seed",
                started_at=timezone.now_for_db(),
            )
        )
        await db.commit()
        await db.refresh(session)
        session_id = cast("int", session.id)

        db.add(
            _source_pick_inbox(
                session=session,
                source_device=source_device,
                data={
                    "bin_code": f"{prefix}_SRC_BIN",
                    "bin_cell_index": "A01",
                    "bin_cell_code": "A01",
                    "material_identity_key": material_key,
                    "pkg_code": pkg_code,
                    "wms_inventory_id": f"{prefix}_WMS_INV",
                    "reel_thickness": "7.125",
                    "source_version": "12",
                },
            )
        )
        await db.commit()

    source_result = await _process_inboxes(session_factory, limit=10)
    assert source_result["success"] == 1

    async with session_factory() as db:
        unmounted_event = (
            (
                await db.execute(
                    select(ResourceStateEvent).where(
                        ResourceStateEvent.event_type == "MATERIAL_UNMOUNTED",  # type: ignore[arg-type]
                        ResourceStateEvent.resource_code == pkg_code,  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .one()
        )
        assert unmounted_event.payload_json["bin_code"] == f"{prefix}_SRC_BIN"
        source_mount = (
            (
                await db.execute(
                    select(BinMaterialMount).where(
                        BinMaterialMount.pkg_code == pkg_code,  # type: ignore[arg-type]
                        BinMaterialMount.bin_code == f"{prefix}_SRC_BIN",  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .one()
        )
        assert source_mount.mount_status == BinMaterialMountStatus.REMOVED
        assert source_mount.ended_at is not None
        session = await db.get(WorklineSession, session_id)
        assert session is not None
        assert session.context_json["sorting"]["current_material"]["material_identity_key"] == material_key
        scan_device = (
            (
                await db.execute(
                    select(Device).where(Device.device_code == f"{prefix}_SORT_SCAN")  # type: ignore[arg-type]
                )
            )
            .scalars()
            .one()
        )
        db.add(
            _working_bin_scan_inbox(
                session=session,
                scan_device=scan_device,
                data={"material_identity_key": material_key, "pkg_code": pkg_code, "reel_thickness": "7.125"},
            )
        )
        await db.commit()

    scan_result = await _process_inboxes(session_factory, limit=10)
    assert scan_result["success"] == 1

    async with session_factory() as db:
        session = await db.get(WorklineSession, session_id)
        assert session is not None
        sorting_context = session.context_json["sorting"]
        assert sorting_context["pending_target_placement"]["target_bin_code"] == f"{prefix}_TGT_BIN"
        assert sorting_context["pending_target_placement"]["target_cell_code"] == "B02"
        command = await _latest_command(db, session_id=session_id, task_type=COMMAND_TARGET_PLACE)
        target_device = await db.get(Device, command.device_id)
        assert target_device is not None
        db.add(_target_place_inbox(session=session, command=command, device_code=target_device.device_code))
        await db.commit()

    target_result = await _process_inboxes(session_factory, limit=10)
    assert target_result["success"] == 1

    async with session_factory() as db:
        target_mount = (
            (
                await db.execute(
                    select(BinMaterialMount).where(
                        BinMaterialMount.pkg_code == pkg_code,  # type: ignore[arg-type]
                        BinMaterialMount.bin_code == f"{prefix}_TGT_BIN",  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .one()
        )
        assert target_mount.mount_status == BinMaterialMountStatus.OCCUPIED
        historical_mounts = (
            (
                await db.execute(
                    select(BinMaterialMount).where(
                        BinMaterialMount.pkg_code == pkg_code,  # type: ignore[arg-type]
                        BinMaterialMount.mount_status == BinMaterialMountStatus.REMOVED,  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(historical_mounts) == 1
        assert historical_mounts[0].bin_code == f"{prefix}_SRC_BIN"
        target_occupancy = (
            (
                await db.execute(
                    select(BinCellOccupancy).where(
                        BinCellOccupancy.bin_code == f"{prefix}_TGT_BIN",  # type: ignore[arg-type]
                        BinCellOccupancy.bin_cell_index == "B02",  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .one()
        )
        assert target_occupancy.occupancy_status == BinCellOccupancyStatus.OCCUPIED
        session = await db.get(WorklineSession, session_id)
        assert session is not None
        sorting_context = session.context_json["sorting"]
        assert "current_material" not in sorting_context
        assert "pending_target_placement" not in sorting_context


@pytest.mark.asyncio
async def test_callback_external_rejects_missing_dispatch_key(
    db_session: AsyncSession,
) -> None:
    response = await _post_callback_external(
        db_session,
        {
            "callback_type": "WMS_RACK_ARRIVED",
            "source_system": "WMS",
            "operation_key": "missing-dispatch-key",
            "trace_id": "missing-dispatch-key-trace",
        },
    )

    assert response["code"] == ClientErrorCode.VALIDATION_ERROR.code
