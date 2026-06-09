"""跨计划 WorkLine 沙箱 stitching smoke。

覆盖 STOPPED guard、START 准入、SMT Sorting P0 插件 intent、RuntimeIntent effect、
命令网关 realtime status 以及本地 NG 收敛的跨计划合同。
"""

from __future__ import annotations

import importlib
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import Request, Response
from sqlalchemy import select

from src.app.device.models import CommandStatus, Device, DeviceProtocol
from src.app.device.models.command import DeviceCommand
from src.app.device.models.device import DeviceStatus
from src.app.resource.models import (
    BinCellOccupancy,
    BinCellOccupancyStatus,
    BinMaterialMount,
    BinMaterialMountStatus,
    ResourceSourceSystem,
    ResourceStateEvent,
    ResourceStateEventType,
)
from src.app.sys.models import SystemOutbox, SystemOutboxDispatchType
from src.app.workline.inbox_claim_bucket import build_claim_bucket_key
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.app.workline.models.inbox import WorklineInbox
from src.app.workline.models.runtime_hold import NgReasonSource, NgReturnItem, NgReturnItemStatus, RuntimeHold
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.models.session import SessionStatus, WorklineSession
from src.app.workline.services import write_back_service as workline_effects
from src.app.workline.services.device_command_gateway import DeviceCommandGateway
from src.app.workline.services.start_admission_service import (
    StartAdmissionStatusFetchResult,
    StartAdmissionStatusTarget,
    WorkLineStartAdmissionService,
)
from src.core.response.response_code import ResourceErrorCode
from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_NG_PLACE,
    COMMAND_SOURCE_PICK,
    COMMAND_TARGET_PLACE,
    EVENT_SESSION_COMPLETE_REQUESTED,
    EVENT_WORKING_BIN_SCAN,
    NG_REASON_LOCAL_SORTING_NG,
    PHASE_WAITING_SCAN,
    ROLE_SORTING_NG_STATION,
    ROLE_SORTING_SCAN_PLATFORM,
    ROLE_SORTING_SOURCE_ARM,
    ROLE_SORTING_TARGET_ARM,
    ROLE_SORTING_WORKSTATION,
    SMT_SORTING_INBOUND_CONTRACT_VERSION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)
from src.workline_plugins.smt_sorting_inbound.plugin import SmtSortingInboundPlugin
from src.workline_runtime.orchestrator import OrchestratorResult
from src.workline_runtime.runtime_intent import RuntimeIntentKind
from src.workline_runtime.runtime_intent_effects import RuntimeIntentEffectApplier
from src.workline_runtime.trace_context import TraceContext

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.workline_runtime.plugin_context import PluginContext

command_repository_module = importlib.import_module("src.app.device.repositories.command_repository")
gateway_module = importlib.import_module("src.app.workline.services.device_command_gateway")


JsonDict = dict[str, object]


def _inbox_with_claim_bucket(**kwargs: Any) -> WorklineInbox:
    payload_json = dict(kwargs.get("payload_json") or {})
    kwargs["payload_json"] = payload_json
    kwargs["claim_bucket_key"] = build_claim_bucket_key(
        session_id=kwargs.get("session_id"),
        device_id=kwargs.get("device_id"),
        workline_id=kwargs.get("workline_id"),
        payload_json=payload_json,
    )
    return WorklineInbox(**kwargs)


class RecordingStatusFetcher:
    """记录 START 准入批量状态探测调用。"""

    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.calls: list[tuple[StartAdmissionStatusTarget, float]] = []

    async def __call__(
        self,
        target: StartAdmissionStatusTarget,
        timeout_seconds: float,
    ) -> StartAdmissionStatusFetchResult:
        self.calls.append((target, timeout_seconds))
        return StartAdmissionStatusFetchResult(status_code=self.status_code, payload=self.payload)


class FakeStatusResponse:
    status_code = 200
    text = ""

    def json(self) -> dict[str, Any]:
        return {"state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None}}


class CapturingAsyncClient:
    requests: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> CapturingAsyncClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> FakeStatusResponse:
        self.requests.append({"method": "GET", "url": url, "timeout": kwargs.get("timeout")})
        return FakeStatusResponse()

    async def post(self, url: str, *, json: dict[str, Any], **kwargs: Any) -> FakeStatusResponse:
        self.requests.append({"method": "POST", "url": url, "json": json, "timeout": kwargs.get("timeout")})
        return FakeStatusResponse()


class FakeStationLeaseStatusProvider:
    available = True
    reason_code = None
    active_rack_code = None
    active_session_id = None
    active_dispatch_key = None

    async def station_lease_status(
        self,
        _position_code: str,
        *,
        allow_active_rack_bound: bool = False,
    ) -> FakeStationLeaseStatusProvider:
        return self


class EmbeddedTargetSnapshotProvider:
    def __init__(self, session_context: dict[str, Any]) -> None:
        self.session_context = session_context

    async def active_bin_rack(self, *, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        sorting = self.session_context.get("sorting")
        if not isinstance(sorting, dict):
            return None
        snapshot = sorting.get("active_target_bin")
        return dict(snapshot) if isinstance(snapshot, dict) else None


class NullCommandRepository:
    async def get_by_command_code(self, _db: object, _command_code: str) -> None:
        return None


def _build_request(*, body: JsonDict, path: str) -> Request:
    request = MagicMock()
    request.client = MagicMock()
    request.client.host = "192.168.1.100"
    request.url = MagicMock()
    request.url.path = path
    request.headers = {"User-Agent": "CrossPlanSmoke"}
    request.method = "POST"
    request.json = AsyncMock(return_value=body)
    return cast("Request", request)


def _event_payload(**overrides: object) -> JsonDict:
    payload: JsonDict = {
        "device_code": "SORT-SCAN-PLATFORM",
        "event_type": EVENT_WORKING_BIN_SCAN,
        "timestamp": 1702627300000,
        "data": {"material_identity_key": "mid:pkg-001", "pkg_code": "PKG-001", "reel_thickness": "7.125"},
    }
    payload.update(overrides)
    return payload


def _ctx(session_context: dict[str, Any], *, services: SimpleNamespace | None = None) -> PluginContext:
    runtime_services = services or SimpleNamespace(
        active_rack_snapshot_provider=EmbeddedTargetSnapshotProvider(session_context),
        station_lease_status_provider=FakeStationLeaseStatusProvider(),
    )
    return cast(
        "PluginContext",
        SimpleNamespace(
            trace_id="trace-cross-plan-smoke",
            config={},
            logger=SimpleNamespace(info=lambda *_args: None, warning=lambda *_args: None),
            normalized_input=None,
            session=SimpleNamespace(id=3001, context_json=session_context),
            services=runtime_services,
        ),
    )


def _merge_plugin_context_patch_for_smoke(session_context: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """仅用于本 smoke 串联插件返回；不模拟 runtime effect applier 的完整 patch 语义。"""

    session_context.update(patch)
    return session_context


def _workline(**overrides: Any) -> WorkLine:
    data: dict[str, Any] = {
        "line_code": "WL-CROSS-PLAN-SMOKE",
        "line_name": "跨计划沙箱 smoke 线",
        "line_type": LineType.AUTO,
        "plugin_key": SMT_SORTING_INBOUND_PLUGIN_KEY,
        "contract_version": SMT_SORTING_INBOUND_CONTRACT_VERSION,
        "run_mode": WorkLineRunMode.AUTO,
        "runtime_status": WorkLineRuntimeStatus.STOPPED,
        "is_active": True,
    }
    data.update(overrides)
    return WorkLine(**data)


def _device(
    *,
    device_code: str,
    role: str,
    supports_command_types: list[str] | None = None,
    supports_event_types: list[str] | None = None,
) -> Device:
    return Device(
        device_code=device_code,
        device_name=device_code,
        device_role=role,
        role_index=1,
        host="mock-ecs",
        port=8010,
        protocol=DeviceProtocol.HTTP,
        callback_path="/api/v1/device/command",
        capabilities_json={
            "status_path": "/api/v1/device/status",
            "supports_event_types": supports_event_types or [],
            "supports_command_types": supports_command_types or [],
        },
        device_status=DeviceStatus.IDLE,
    )


async def _persist_workline_with_devices(db_session: AsyncSession, workline: WorkLine) -> list[Device]:
    db_session.add(workline)
    await db_session.commit()
    await db_session.refresh(workline)
    devices = [
        _device(
            device_code="SORT-SOURCE-ARM",
            role=ROLE_SORTING_SOURCE_ARM,
            supports_command_types=[COMMAND_SOURCE_PICK],
        ),
        _device(
            device_code="SORT-TARGET-ARM",
            role=ROLE_SORTING_TARGET_ARM,
            supports_command_types=[COMMAND_TARGET_PLACE, COMMAND_NG_PLACE],
        ),
        _device(
            device_code="SORT-SCAN-PLATFORM",
            role=ROLE_SORTING_SCAN_PLATFORM,
            supports_event_types=[EVENT_WORKING_BIN_SCAN],
        ),
        _device(device_code="SORT-NG-STATION", role=ROLE_SORTING_NG_STATION),
        _device(
            device_code="SORT-WORKSTATION",
            role=ROLE_SORTING_WORKSTATION,
            supports_event_types=[EVENT_SESSION_COMPLETE_REQUESTED],
        ),
    ]
    for device in devices:
        device.work_line_id = workline.id
        db_session.add(device)
    await db_session.commit()
    return devices


def _idle_payload(devices: list[Device]) -> dict[str, Any]:
    return {
        "devices": [
            {
                "device_code": device.device_code,
                "state": {"mode": "AUTO", "status": "IDLE", "current_command_id": None},
            }
            for device in devices
        ]
    }


def _source_pick_result(data: dict[str, Any]) -> WorklineInbox:
    return _inbox_with_claim_bucket(
        kind="COMMAND_RESULT",
        source_system="DEVICE",
        payload_json={
            "command_code": "CMD-SOURCE-PICK-SMOKE",
            "device_code": "SORT-SOURCE-ARM",
            "task_type": COMMAND_SOURCE_PICK,
            "result": "SUCCESS",
            "data": data,
        },
    )


def _scan_event(data: dict[str, Any]) -> WorklineInbox:
    return _inbox_with_claim_bucket(
        kind="DEVICE_EVENT",
        source_system="DEVICE",
        payload_json={
            "event_id": "SCAN-EVENT-SMOKE",
            "device_code": "SORT-SCAN-PLATFORM",
            "event_type": EVENT_WORKING_BIN_SCAN,
            "data": data,
        },
    )


def _target_place_result() -> WorklineInbox:
    return _inbox_with_claim_bucket(
        kind="COMMAND_RESULT",
        source_system="DEVICE",
        payload_json={
            "command_code": "CMD-TARGET-PLACE-SMOKE",
            "device_code": "SORT-TARGET-ARM",
            "task_type": COMMAND_TARGET_PLACE,
            "result": "SUCCESS",
            "data": {},
        },
    )


def _ng_place_result() -> WorklineInbox:
    return _inbox_with_claim_bucket(
        kind="COMMAND_RESULT",
        source_system="DEVICE",
        payload_json={
            "command_code": "CMD-NG-PLACE-SMOKE",
            "device_code": "SORT-TARGET-ARM",
            "task_type": COMMAND_NG_PLACE,
            "result": "SUCCESS",
            "data": {},
        },
    )


def _dispatchable_device() -> object:
    return SimpleNamespace(
        id=100,
        device_code="SORT-TARGET-ARM",
        host="mock-ecs",
        port=8010,
        protocol="HTTP",
        callback_path="/api/v1/device/command",
        device_status=DeviceStatus.IDLE,
        current_command_id=None,
        maintenance_mode=False,
        capabilities_json={"supports_command_types": [COMMAND_TARGET_PLACE]},
    )


def _response_data(response: JsonDict) -> JsonDict:
    data = response["data"]
    if hasattr(data, "model_dump"):
        return cast("JsonDict", data.model_dump())
    return cast("JsonDict", data)


def _effect_ctx(
    *,
    db_session: AsyncSession,
    session: WorklineSession,
    workline: WorkLine,
    inbox: WorklineInbox,
    devices: list[Device],
) -> dict[str, Any]:
    devices_by_role: dict[str, list[Device]] = {}
    inbox_device_code = (inbox.payload_json or {}).get("device_code")
    source_device = next(
        (device for device in devices if device.device_code == inbox_device_code),
        devices[0] if devices else None,
    )
    for device in devices:
        devices_by_role.setdefault(str(device.device_role), []).append(device)
    return {
        "db": db_session,
        "session": session,
        "workline": workline,
        "inbox": inbox,
        "devices_by_role": devices_by_role,
        "source_device": source_device,
        "orch_result": OrchestratorResult(success=True, intents=[]),
        "current_status": session.status.value if hasattr(session.status, "value") else str(session.status),
        "trace_id": session.trace_id,
        "trace": TraceContext.from_runtime(session=session, workline=workline, inbox=inbox, trace_id=session.trace_id),
        "session_ctx": dict(session.context_json or {}),
        "now": datetime(2026, 1, 1, 0, 2, 0),
        "awaiting_command_id": None,
        "awaiting_command_code": None,
        "next_timeline_seq_no": None,
    }


@pytest.mark.asyncio
async def test_cross_plan_sandbox_smoke(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """串联 STOPPED guard、START、SMT Sorting P0、命令派发和 local NG 收敛。"""

    from src.app.callback.v1.callback import callback_event

    workline = _workline(stopped_reason="MANUAL_STOP")
    devices = await _persist_workline_with_devices(db_session, workline)

    # 1. STOPPED 工作线拒收 SMT Sorting 生产事件，且不创建 inbox。
    http_response = Response()
    with (
        patch(
            "src.app.callback.services.callback_ingress_service.inbox_service.create_device_event_inbox",
            new=AsyncMock(),
        ) as mock_create_inbox,
        patch(
            "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
            new=AsyncMock(),
        ),
        patch(
            "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
            new=AsyncMock(),
        ),
        patch("src.app.callback.v1.callback._enqueue_workline_processing") as mock_enqueue,
        patch("src.app.callback.v1.callback.get_request_id", return_value="req-cross-plan-stopped"),
    ):
        response = await callback_event(
            request=_build_request(body=_event_payload(), path="/api/v1/callback/event"),
            db=db_session,
            response=http_response,
        )

    assert http_response.status_code == 409
    assert response["code"] == ResourceErrorCode.CONFLICT.code
    assert _response_data(cast("JsonDict", response))["reason_code"] == "WORKLINE_NOT_ACCEPTING_WORK"
    mock_create_inbox.assert_not_awaited()
    mock_enqueue.assert_not_called()
    await db_session.refresh(workline)
    assert workline.runtime_status == WorkLineRuntimeStatus.STOPPED
    inboxes = (await db_session.execute(select(WorklineInbox))).scalars().all()
    assert inboxes == []

    # 2. ECS/mock START 通过 START admission 后，工作线从 STOPPED 进入 READY。
    fetcher = RecordingStatusFetcher(_idle_payload(devices))
    admission = await WorkLineStartAdmissionService(status_fetcher=fetcher).admit_start_for_device(
        db_session,
        "SORT-SCAN-PLATFORM",
        request_id="req-cross-plan-start",
        trace_id="trace-cross-plan-start",
    )

    await db_session.refresh(workline)
    assert admission.accepted is True
    assert admission.http_status == 200
    assert workline.runtime_status == WorkLineRuntimeStatus.READY
    assert len(fetcher.calls) == 1
    rack_dispatches = (
        (
            await db_session.execute(
                select(SystemOutbox).where(
                    SystemOutbox.workline_id == workline.id,
                    SystemOutbox.operation_domain == "RACK",
                    SystemOutbox.dispatch_type == SystemOutboxDispatchType.EXTERNAL_HTTP,
                )
            )
        )
        .scalars()
        .all()
    )
    sorting_commands = (await db_session.execute(select(DeviceCommand))).scalars().all()
    assert rack_dispatches == []
    assert sorting_commands == []

    plugin = SmtSortingInboundPlugin()
    session_context: dict[str, Any] = {
        "sorting": {
            "context_schema_version": 1,
            "stations": {"scan_platform": "EMPTY"},
        }
    }

    # 3. READY 后源端取盘产出 MATERIAL_UNMOUNTED 并打开 current_material；重复取盘不重复出账。
    source_data = {
        "bin_code": "SRC-BIN-01",
        "bin_cell_index": "A01",
        "bin_cell_code": "A01",
        "material_identity_key": "mid:pkg-001",
        "pkg_code": "PKG-001",
        "wms_inventory_id": "WMS-001",
        "reel_thickness": "7.125",
        "source_version": "12",
    }
    source_intents = await plugin.on_command_result(_ctx(session_context), _source_pick_result(source_data))

    assert [intent.kind for intent in source_intents] == [
        RuntimeIntentKind.RESOURCE_FACT,
        RuntimeIntentKind.UPDATE_CONTEXT,
    ]
    assert source_intents[0].action == "MATERIAL_UNMOUNTED"
    session_context = _merge_plugin_context_patch_for_smoke(session_context, source_intents[1].context_patch)
    assert session_context["sorting"]["current_material"]["material_identity_key"] == "mid:pkg-001"

    replay_intents = await plugin.on_command_result(_ctx(session_context), _source_pick_result(source_data))

    assert [intent.kind for intent in replay_intents] == [RuntimeIntentKind.BLOCK]
    assert replay_intents[0].reason_code == "SORTING_CURRENT_MATERIAL_OPEN"

    # 4. 扫码成功写入 pending_target_placement，并产生命令 intent。
    session_context["sorting"]["active_target_bin"] = {
        "snapshot_version": "snap-target-001",
        "cells": [
            {
                "bin_code": "TGT-BIN-01",
                "bin_cell_index": "B02",
                "status": "EMPTY",
                "capacity_depth_mm": "30.500",
                "used_depth_mm": "0",
            }
        ],
    }
    scan_intents = await plugin.on_device_event(
        _ctx(session_context),
        _scan_event(
            {
                "material_identity_key": "mid:pkg-001",
                "pkg_code": "PKG-001",
                "reel_thickness": "7.125",
            }
        ),
    )

    assert [intent.kind for intent in scan_intents] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
    session_context = _merge_plugin_context_patch_for_smoke(session_context, scan_intents[0].context_patch)
    pending_target = session_context["sorting"]["pending_target_placement"]
    assert pending_target["target_bin_code"] == "TGT-BIN-01"
    assert pending_target["target_cell_code"] == "B02"
    command_intent = scan_intents[1]
    assert command_intent.action == COMMAND_TARGET_PLACE
    assert command_intent.device_role == ROLE_SORTING_TARGET_ARM

    # 5. command dispatch 先 GET realtime status，确认 IDLE 后才 POST command。
    CapturingAsyncClient.requests.clear()
    monkeypatch.setattr(httpx, "AsyncClient", CapturingAsyncClient)
    monkeypatch.setattr(
        gateway_module, "_get_device_for_command_dispatch", AsyncMock(return_value=_dispatchable_device())
    )
    monkeypatch.setattr(command_repository_module, "DeviceCommandRepository", NullCommandRepository)
    dispatch_success = await DeviceCommandGateway().dispatch(
        AsyncMock(),
        SimpleNamespace(
            id=1,
            target_code="SORT-TARGET-ARM",
            target_type="DEVICE",
            dispatch_key="device-command:CMD-TARGET-PLACE-SMOKE",
            payload_json={
                "command_code": "CMD-TARGET-PLACE-SMOKE",
                "task_type": COMMAND_TARGET_PLACE,
                **command_intent.payload_json,
            },
            session_id=3001,
        ),
    )

    assert dispatch_success is True
    assert [request["method"] for request in CapturingAsyncClient.requests] == ["GET", "POST"]
    assert CapturingAsyncClient.requests[0]["url"] == (
        "http://mock-ecs:8010/api/v1/device/status?device_code=SORT-TARGET-ARM"
    )
    assert CapturingAsyncClient.requests[1]["url"] == "http://mock-ecs:8010/api/v1/device/command"
    post_payload = CapturingAsyncClient.requests[1]["json"]
    assert post_payload["command_code"] == "CMD-TARGET-PLACE-SMOKE"
    assert post_payload["task_type"] == COMMAND_TARGET_PLACE
    assert post_payload["target_bin_code"] == "TGT-BIN-01"
    assert post_payload["target_cell_code"] == "B02"

    # 6. 目标端成功产出 MATERIAL_MOUNTED，并关闭 current_material/pending_target_placement。
    target_intents = await plugin.on_command_result(_ctx(session_context), _target_place_result())

    assert [intent.kind for intent in target_intents] == [
        RuntimeIntentKind.RESOURCE_FACT,
        RuntimeIntentKind.UPDATE_CONTEXT,
    ]
    assert target_intents[0].action == "MATERIAL_MOUNTED"
    session_context = _merge_plugin_context_patch_for_smoke(session_context, target_intents[1].context_patch)
    assert "current_material" not in session_context["sorting"]
    assert "pending_target_placement" not in session_context["sorting"]

    # 7. 本地 NG 记录 LOCAL_SORTING_NG 语义，并允许 session completion。
    ng_context: dict[str, Any] = {
        "sorting": {
            "context_schema_version": 1,
            "stations": {"scan_platform": "EMPTY"},
            "business_phase": PHASE_WAITING_SCAN,
            "current_material": {
                "source_bin_code": "SRC-BIN-01",
                "source_cell_code": "A01",
                "material_identity_key": "mid:pkg-001",
                "pkg_code": "PKG-001",
                "reel_thickness_mm": "7.125",
            },
        }
    }
    ng_scan_intents = await plugin.on_device_event(
        _ctx(ng_context),
        _scan_event({"material_identity_key": "mid:actual-other", "pkg_code": "PKG-001", "reel_thickness": "7.125"}),
    )

    assert [intent.kind for intent in ng_scan_intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.MARK_NG,
        RuntimeIntentKind.COMMAND,
    ]
    ng_context = _merge_plugin_context_patch_for_smoke(ng_context, ng_scan_intents[0].context_patch)
    assert ng_scan_intents[0].context_patch["scan_ng_reason_code"] == "LOCAL_SORTING_NG"
    assert ng_scan_intents[1].reason_code == "LOCAL_SORTING_NG"
    assert ng_scan_intents[2].action == COMMAND_NG_PLACE
    assert ng_scan_intents[2].device_role == ROLE_SORTING_TARGET_ARM

    ng_place_intents = await plugin.on_command_result(_ctx(ng_context), _ng_place_result())

    assert [intent.kind for intent in ng_place_intents] == [RuntimeIntentKind.UPDATE_CONTEXT]
    ng_context = _merge_plugin_context_patch_for_smoke(ng_context, ng_place_intents[0].context_patch)
    assert ng_place_intents[0].context_patch["ng_reason"] == "LOCAL_SORTING_NG"
    assert "current_material" not in ng_context["sorting"]
    assert ng_context["sorting"]["stations"]["scan_platform"] == "EMPTY"

    complete_intents = await plugin.on_device_event(
        _ctx(ng_context),
        _inbox_with_claim_bucket(
            kind="DEVICE_EVENT",
            source_system="DEVICE",
            payload_json={
                "event_id": "COMPLETE-EVENT-SMOKE",
                "device_code": "SORT-WORKSTATION",
                "event_type": EVENT_SESSION_COMPLETE_REQUESTED,
                "data": {},
            },
        ),
    )

    assert [intent.kind for intent in complete_intents] == [RuntimeIntentKind.COMPLETE]
    assert complete_intents[0].context_patch["sorting"]["business_phase"] == "COMPLETED"

    # 8. WORKLINE_START_REQUESTED 不是 SMT 插件普通业务事件。
    manifest = SmtSortingInboundPlugin.manifest
    manifest_roles = {requirement.role for requirement in manifest.required_device_roles}
    command_target_roles = {role for roles in manifest.command_target_roles.values() for role in roles}
    assert "NG_ARM" not in manifest_roles
    assert "NG_ARM" not in command_target_roles
    assert manifest.command_target_roles[COMMAND_NG_PLACE] == (ROLE_SORTING_TARGET_ARM,)
    assert "WORKLINE_START_REQUESTED" not in manifest.supported_events
    assert "WORKLINE_START_REQUESTED" not in manifest.event_source_roles


@pytest.mark.asyncio
async def test_cross_plan_runtime_effect_stitching_persists_context_resource_fact_and_outbox(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """插件 intent 必须通过 effect 层真实衔接到 context、resource fact、command/outbox。"""

    workline = _workline(line_code="WL-CROSS-PLAN-EFFECT", runtime_status=WorkLineRuntimeStatus.READY)
    devices = await _persist_workline_with_devices(db_session, workline)
    source_occupancy = BinCellOccupancy(
        bin_code="SRC-BIN-01",
        bin_cell_code="A01",
        bin_cell_index="A01",
        material_identity_key="mid:pkg-001",
        reel_count=1,
        used_depth_mm=Decimal("7.125"),
        capacity_depth_mm=Decimal("30.500"),
        remaining_depth_mm=Decimal("23.375"),
        occupancy_status=BinCellOccupancyStatus.OCCUPIED,
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="SOURCE-SNAPSHOT-SMOKE",
        source_version="12",
        trace_id="trace-cross-plan-effect",
        started_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(source_occupancy)
    await db_session.flush()
    source_mount = BinMaterialMount(
        bin_cell_occupancy_id=source_occupancy.id,
        cell_stack_position=1,
        bin_code="SRC-BIN-01",
        bin_cell_code="A01",
        bin_cell_index="A01",
        material_identity_key="mid:pkg-001",
        pkg_code="PKG-001",
        wms_inventory_id="WMS-001",
        reel_thickness="7.125",
        mount_status=BinMaterialMountStatus.OCCUPIED,
        source_system=ResourceSourceSystem.WES_RUNTIME,
        source_event_id="SOURCE-SNAPSHOT-SMOKE",
        source_version="12",
        trace_id="trace-cross-plan-effect",
        started_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(source_mount)
    session = WorklineSession(
        session_code="SESSION-CROSS-PLAN-EFFECT",
        workline_id=workline.id,
        plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
        contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
        status=SessionStatus.RUNNING,
        trace_id="trace-cross-plan-effect",
        context_json={
            "sorting": {
                "context_schema_version": 1,
                "stations": {"scan_platform": "EMPTY"},
                "business_phase": PHASE_WAITING_SCAN,
                "active_target_bin": {
                    "snapshot_version": "snap-target-effect",
                    "cells": [
                        {
                            "bin_code": "TGT-BIN-01",
                            "bin_cell_index": "B02",
                            "status": "EMPTY",
                            "capacity_depth_mm": "30.500",
                            "used_depth_mm": "0",
                        }
                    ],
                },
            }
        },
    )
    db_session.add(session)
    await db_session.flush()

    source_inbox = _source_pick_result(
        {
            "bin_code": "SRC-BIN-01",
            "bin_cell_index": "A01",
            "bin_cell_code": "A01",
            "material_identity_key": "mid:pkg-001",
            "pkg_code": "PKG-001",
            "wms_inventory_id": "WMS-001",
            "reel_thickness": "7.125",
            "source_version": "12",
        }
    )
    source_inbox.workline_id = workline.id
    source_inbox.session_id = session.id
    source_inbox.trace_id = session.trace_id
    source_inbox.claim_bucket_key = build_claim_bucket_key(
        session_id=session.id,
        workline_id=workline.id,
        payload_json=source_inbox.payload_json,
    )
    db_session.add(source_inbox)
    await db_session.flush()
    await db_session.refresh(session)
    await db_session.refresh(source_inbox)

    plugin = SmtSortingInboundPlugin()
    source_plugin_ctx = _ctx(dict(session.context_json or {}))
    source_plugin_ctx.session = session
    source_intents = await plugin.on_command_result(source_plugin_ctx, source_inbox)

    assert [intent.kind for intent in source_intents] == [
        RuntimeIntentKind.RESOURCE_FACT,
        RuntimeIntentKind.UPDATE_CONTEXT,
    ]
    assert source_intents[0].action == ResourceStateEventType.MATERIAL_UNMOUNTED.value
    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier().apply(
        _effect_ctx(db_session=db_session, session=session, workline=workline, inbox=source_inbox, devices=devices),
        source_intents,
    )
    await db_session.flush()
    await db_session.refresh(session)
    await db_session.refresh(source_occupancy)
    await db_session.refresh(source_mount)

    assert session.context_json["sorting"]["current_material"]["material_identity_key"] == "mid:pkg-001"
    assert source_mount.mount_status == BinMaterialMountStatus.REMOVED
    assert source_mount.ended_at is not None
    assert source_occupancy.reel_count == 0
    assert source_occupancy.used_depth_mm == Decimal("0.000")
    assert source_occupancy.remaining_depth_mm == Decimal("30.500")
    assert source_occupancy.occupancy_status == BinCellOccupancyStatus.REMOVED
    assert source_occupancy.ended_at is not None

    expected_idempotency_key = source_intents[0].idempotency_key
    resource_event = (
        await db_session.execute(
            select(ResourceStateEvent).where(ResourceStateEvent.idempotency_key == expected_idempotency_key)
        )
    ).scalar_one()
    assert resource_event.event_type == ResourceStateEventType.MATERIAL_UNMOUNTED
    assert resource_event.idempotency_key == expected_idempotency_key
    assert resource_event.trace_id == "trace-cross-plan-effect"
    assert resource_event.session_id == str(session.id)
    assert resource_event.workline_id == workline.id

    scan_inbox = _scan_event(
        {
            "material_identity_key": "mid:pkg-001",
            "pkg_code": "PKG-001",
            "reel_thickness": "7.125",
        }
    )
    scan_inbox.workline_id = workline.id
    scan_inbox.session_id = session.id
    scan_inbox.trace_id = session.trace_id
    scan_inbox.claim_bucket_key = build_claim_bucket_key(
        session_id=session.id,
        workline_id=workline.id,
        payload_json=scan_inbox.payload_json,
    )
    db_session.add(scan_inbox)
    await db_session.commit()
    await db_session.refresh(session)
    await db_session.refresh(scan_inbox)

    plugin_ctx = _ctx(dict(session.context_json or {}))
    plugin_ctx.session = session
    scan_intents = await plugin.on_device_event(plugin_ctx, scan_inbox)

    assert [intent.kind for intent in scan_intents] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]

    await RuntimeIntentEffectApplier().apply(
        _effect_ctx(db_session=db_session, session=session, workline=workline, inbox=scan_inbox, devices=devices),
        scan_intents,
    )
    await db_session.commit()
    await db_session.refresh(session)

    assert session.status == SessionStatus.WAITING_DEVICE_RESULT
    assert session.context_json["sorting"]["current_material"]["material_identity_key"] == "mid:pkg-001"
    assert session.context_json["sorting"]["pending_target_placement"]["target_cell_code"] == "B02"
    assert session.awaiting_command_id is not None

    command = await db_session.get(DeviceCommand, session.awaiting_command_id)
    assert command is not None
    assert command.task_type == COMMAND_TARGET_PLACE
    assert command.trace_id == "trace-cross-plan-effect"
    assert command.session_id_int == session.id

    outboxes = (
        (
            await db_session.execute(
                select(SystemOutbox).where(
                    SystemOutbox.dispatch_key == f"device-command:{command.command_code}",
                    SystemOutbox.session_id == session.id,
                    SystemOutbox.workline_id == workline.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(outboxes) == 1
    assert outboxes[0].dispatch_key == f"device-command:{command.command_code}"
    assert outboxes[0].target_code == "SORT-TARGET-ARM"
    assert outboxes[0].payload_json["task_type"] == COMMAND_TARGET_PLACE


@pytest.mark.asyncio
async def test_cross_plan_ng_material_conflict_blocks_session_completion(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨计划 smoke 覆盖 NG_MATERIAL_CONFLICT 阻止 Session 完成。"""

    workline = _workline(line_code="WL-CROSS-PLAN-NG-CONFLICT", runtime_status=WorkLineRuntimeStatus.READY)
    devices = await _persist_workline_with_devices(db_session, workline)
    session = WorklineSession(
        session_code="SESSION-CROSS-PLAN-NG-CONFLICT",
        workline_id=workline.id,
        plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
        contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
        status=SessionStatus.RUNNING,
        trace_id="trace-cross-plan-ng-conflict",
        context_json={
            "sorting": {
                "context_schema_version": 1,
                "stations": {"scan_platform": "EMPTY"},
            }
        },
    )
    inbox = _inbox_with_claim_bucket(
        kind="DEVICE_EVENT",
        source_system="DEVICE",
        workline_id=workline.id,
        trace_id="trace-cross-plan-ng-conflict",
        event_id="COMPLETE-EVENT-NG-CONFLICT",
        payload_json={
            "event_id": "COMPLETE-EVENT-NG-CONFLICT",
            "device_code": "SORT-WORKSTATION",
            "event_type": EVENT_SESSION_COMPLETE_REQUESTED,
            "data": {},
        },
    )
    db_session.add(session)
    await db_session.flush()
    inbox.session_id = session.id
    inbox.claim_bucket_key = build_claim_bucket_key(
        session_id=session.id,
        workline_id=workline.id,
        payload_json=inbox.payload_json,
    )
    ng_device = next(device for device in devices if device.device_code == "SORT-TARGET-ARM")
    existing_command = DeviceCommand(
        command_code="CMD-SMT-NG-CONFLICT-EXISTING",
        device_id=ng_device.id,
        workline_id=workline.id,
        session_id=session.session_code,
        session_id_int=session.id,
        plugin_key=workline.plugin_key,
        contract_version=workline.contract_version,
        task_type=COMMAND_NG_PLACE,
        params={"material_identity_key": "mid:pkg-001", "ng_location": "NG-01"},
        status=CommandStatus.COMPLETED,
        trace_id=session.trace_id,
    )
    db_session.add(existing_command)
    await db_session.flush()
    session.context_json = {
        "sorting": {
            "context_schema_version": 1,
            "stations": {"scan_platform": "EMPTY"},
        },
        "ng_reason": NG_REASON_LOCAL_SORTING_NG,
        "pick_place_reason": NG_REASON_LOCAL_SORTING_NG,
        "scan_ng_reason_code": NG_REASON_LOCAL_SORTING_NG,
        "scan_ng_reason_message": "本地 NG 放置成功",
        "source_payload": {
            "current_material": {
                "source_bin_code": "SRC-BIN-01",
                "source_cell_code": "A01",
                "material_identity_key": "mid:pkg-001",
                "actual_material_identity_key": "mid:actual-other",
                "pkg_code": "PKG-001",
                "reel_thickness_mm": "7.125",
                "ng_status": "MOVING_TO_NG",
            },
            "ng_command_payload": {
                "command_code": "CMD-SMT-NG-CONFLICT",
                "device_code": "SORT-TARGET-ARM",
                "command_type": COMMAND_NG_PLACE,
                "result": "SUCCESS",
                "data": {"ng_location": "NG-01"},
            },
        },
    }
    material_identity_key = f"workflow-ng:{SMT_SORTING_INBOUND_PLUGIN_KEY}:session:{session.id}"
    existing_ng_item = NgReturnItem(
        source_workline_id=workline.id,
        source_session_id=session.id,
        source_command_id=existing_command.id,
        source_event_id="EXISTING-NG-ITEM",
        material_identity_key=material_identity_key,
        material_identity_json={
            "resolution_status": "MISSING",
            "idempotency_key": material_identity_key,
            "fallback_identity": True,
            "fallback_source": "SESSION",
        },
        physical_handoff_evidence_json={"source": "WORKFLOW_SCAN_NG"},
        ng_reason_source=NgReasonSource.PLUGIN,
        ng_reason_code=NG_REASON_LOCAL_SORTING_NG,
        ng_reason_label="本地分拣 NG",
        created_from_runtime_hold_id=None,
        status=NgReturnItemStatus.WAITING_REWORK,
    )
    db_session.add(existing_ng_item)
    db_session.add(inbox)
    await db_session.commit()
    await db_session.refresh(session)
    await db_session.refresh(inbox)
    await db_session.refresh(existing_ng_item)

    complete_intents = await SmtSortingInboundPlugin().on_device_event(_ctx(dict(session.context_json or {})), inbox)

    assert [intent.kind for intent in complete_intents] == [RuntimeIntentKind.COMPLETE]

    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier().apply(
        _effect_ctx(db_session=db_session, session=session, workline=workline, inbox=inbox, devices=[]),
        complete_intents,
    )
    await db_session.flush()
    await db_session.refresh(session)
    await db_session.refresh(workline)

    runtime_hold = (
        await db_session.execute(select(RuntimeHold).where(RuntimeHold.source_reason == "NG_MATERIAL_CONFLICT"))
    ).scalar_one()

    assert session.status == SessionStatus.MANUAL_HOLD
    assert session.context_json["ng_material_conflict"]["reason_code"] == "NG_MATERIAL_CONFLICT"
    assert session.context_json["ng_material_conflict"]["material_identity_key"] == material_identity_key
    assert session.context_json["ng_material_conflict"]["runtime_hold_id"] == runtime_hold.id
    assert workline.runtime_status == WorkLineRuntimeStatus.RECONCILING
    assert workline.stopped_reason == "NG_MATERIAL_CONFLICT"
    assert runtime_hold.source_kind == "RESOURCE_RECONCILIATION"
    assert runtime_hold.source_reason == "NG_MATERIAL_CONFLICT"
    assert (
        runtime_hold.source_idempotency_key == "resource-reconciliation:NG_MATERIAL_CONFLICT:COMPLETE-EVENT-NG-CONFLICT"
    )
    assert runtime_hold.session_id == session.id
    assert runtime_hold.workline_id == workline.id
    evidence = runtime_hold.evidence_snapshot_json
    assert evidence["reason_code"] == "NG_MATERIAL_CONFLICT"
    assert evidence["material_identity_key"] == material_identity_key
    assert evidence["existing_ng_return_item_id"] == existing_ng_item.id
    assert evidence["existing_source_session_id"] == session.id
    assert evidence["existing_source_command_id"] == existing_command.id
    assert evidence["new_source_session_id"] == session.id
    assert evidence["new_source_command_id"] is None
    assert evidence["new_source_event_id"] == "COMPLETE-EVENT-NG-CONFLICT"
    assert evidence["source_event_id"] == "COMPLETE-EVENT-NG-CONFLICT"
