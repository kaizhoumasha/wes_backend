"""插件 PostgreSQL/worker 场景的小型装配；所有业务 owner 使用生产实现。"""

import json
import time
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler

import pytest_asyncio
from manual_picking.application.bin_line.return_model import BinLineReturn
from manual_picking.definition import DEFINITION
from sqlalchemy import select

from deployment.plugin_composition import build_deployment_runtime
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.runtime.orchestration.models.workline_position import WorkLinePosition
from src.app.transport.composition import build_transport_runtime
from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskStatus, PickingTaskType
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.integration.wms_adapter.outbound_picking.confirmation_support import confirmation_database as _database
from tests.support.transport_broker import MockWmsHttpServer


async def seed_task(db, line, *, queued=False):
    identity = new_uuid7()
    now = timezone.now_for_db()
    evidence = InboundEvidence(
        kind=InboundEvidenceKind.WMS_EVENT,
        source_identity=f"outbound.picking_task.issued@v1:{identity}",
        operation="outbound.picking_task.issued@v1",
        operation_id=identity,
        payload_digest="a" * 64,
        normalized_payload={},
        received_at=now,
        processed_at=now,
        published_at=now,
        decision_digest="c" * 64,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    db.add(evidence)
    await db.flush()
    task = PickingTask(
        task_id=f"TASK-{identity}",
        task_type=PickingTaskType.MANUAL,
        status=PickingTaskStatus.QUEUED if queued else PickingTaskStatus.EXECUTION_COMPLETED,
        queue_revision=1,
        dispatch_sequence=int(identity[-8:], 16),
        issued_at_ms=1,
        issued_evidence_id=evidence.id,
        workline_id=line.id,
    )
    db.add(task)
    await db.flush()
    return task


async def seed_line(db, *, bins=1, capacity=1):
    suffix = new_uuid7()[-12:]
    line = WorkLine(
        line_code=f"CYCLE-{suffix}",
        line_name="Rack cycle integration",
        line_type=LineType.MANUAL,
        run_mode=WorkLineRunMode.AUTO,
        is_active=True,
        plugin_key=DEFINITION.plugin_key,
        plugin_version=DEFINITION.plugin_version,
        config={
            "position_bindings": {slot.slot_key: slot.slot_key for slot in DEFINITION.position_slots},
            "device_bindings": {role.role_key: f"{role.role_key}-{suffix}" for role in DEFINITION.device_roles},
        },
        position_bindings={
            slot.slot_key: {"location_id": f"{slot.slot_key}-{suffix}", "location_type": slot.location_type}
            for slot in DEFINITION.position_slots
        },
    )
    db.add(line)
    await db.flush()
    for slot in DEFINITION.position_slots:
        db.add(
            WorkLinePosition(
                workline_id=line.id,
                workline_code=line.line_code,
                position_code=slot.slot_key,
                position_name=slot.slot_key,
                position_type=slot.position_type,
                position_role="SMT_CLASSIFIER_SINGLE_RACK_WORK" if slot.allowed_rack_kind else None,
                allowed_rack_kind=slot.allowed_rack_kind,
                capacity=capacity if slot.slot_key == "FIVE_RACK" else 1,
                logic_location_code=line.position_bindings[slot.slot_key]["location_id"],
            )
        )
    task = await seed_task(db, line)
    now = timezone.now_for_db()
    for index in range(bins):
        identity = new_uuid7()
        evidence = InboundEvidence(
            kind=InboundEvidenceKind.DEVICE_EVENT,
            source_identity=f"scan4:{identity}",
            device_code=f"SCAN4-{suffix}",
            contract_key="test.scan",
            contract_version="1.0",
            payload_digest="b" * 64,
            normalized_payload={},
            received_at=now,
            processed_at=now,
            published_at=now,
            decision_digest="c" * 64,
            apply_status=InboundEvidenceApplyStatus.APPLIED,
            workline_id=line.id,
        )
        db.add(evidence)
        await db.flush()
        scan3 = InboundEvidence(
            kind=InboundEvidenceKind.DEVICE_EVENT,
            source_identity=f"scan3:{identity}",
            device_code=f"SCAN3-{suffix}",
            contract_key="test.scan",
            contract_version="1.0",
            payload_digest="b" * 64,
            normalized_payload={},
            received_at=now,
            processed_at=now,
            published_at=now,
            decision_digest="c" * 64,
            apply_status=InboundEvidenceApplyStatus.APPLIED,
            workline_id=line.id,
        )
        db.add(scan3)
        await db.flush()
        db.add(
            BinLineReturn(
                workline_id=line.id,
                bin_code=f"BIN-{suffix}-{index}",
                scan3_evidence_id=scan3.id,
                scan4_evidence_id=evidence.id,
                scan4_event_time=int(timezone.now_utc().timestamp() * 1000) + index,
                return_state="READY",
            )
        )
    await db.flush()
    return line, task


@asynccontextmanager
async def runtime_for(sessions, url):
    transport = await build_transport_runtime(
        wms_base_url=url,
        transport_submit_path="/api/v1/wes/transport-requests",
        session_factory=sessions,
    )
    try:
        runtime = build_deployment_runtime(
            session_factory=sessions,
            transport_runtime=transport,
            device_command_service=DeviceCommandService(session_factory=sessions),
            enabled_plugin_keys=("manual-picking",),
        )
        # 本进程只显式驱动数据库服务；真实 broker/自动 wake 验证由独占 worker 进程负责。
        transport.service._task_queue = None
        runtime.execution.wms_confirmation_service._task_queue = None
        runtime.execution.fact_processor._task_queue = None
        yield runtime, transport
    finally:
        await transport.aclose()


async def activate(runtime, line_id):
    service = runtime.picking_task_plan_activation_service
    identity = (DEFINITION.plugin_key, DEFINITION.plugin_version)
    return await service._activate_workline(line_id, handler=service._handlers[identity], plugin_identity=identity)


async def locked_line(db, line_id):
    return await db.scalar(select(WorkLine).where(WorkLine.id == line_id).with_for_update())


class _BusinessHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        envelope = json.loads(self.rfile.read(int(self.headers["content-length"])))
        operation, data = envelope["operation"], envelope["data"]
        code, status = "DECIDED", 200
        if self.path.endswith("transport-requests"):
            code = self.server.transport_code
            status = {"RECEIVED": 202, "DUPLICATE": 200, "CONFLICT": 409}.get(code, 200)
            result = {"transport_task_id": data["transport_task_id"]}
        elif operation == "workline.return_buffer.drain_rack_decide@v1":
            result = self.server.drain_result
        elif operation == "outbound.bin.inbound_batch@v1":
            result = {"result": "RACK_FACE_DONE"}
        elif operation == "outbound.bin.return_batch@v1":
            result = {
                "result": "READY",
                "moves": [
                    {
                        "sequence_no": candidate["sequence_no"],
                        "bin_code": candidate["bin_code"],
                        "target": {
                            "type": "RACK_BIN_SLOT",
                            "rack_id": data["rack_id"],
                            "rack_face": data["rack_face"],
                            "slot_id": str(candidate["sequence_no"]),
                        },
                    }
                    for candidate in data["return_candidates"]
                ],
            }
        elif operation == "outbound.rack.departure_decide@v1":
            result = {
                "result": "READY",
                "rack_destination": {"type": "ZONE", "location_code": "WH01"},
            }
        elif operation == "outbound.picking_task.prepare@v1":
            status, code, result = 202, "PREPARE_ACCEPTED", {}
        else:
            raise AssertionError(f"unexpected operation {operation}")
        with self.server.condition:
            self.server.requests.append({"path": self.path, "envelope": envelope})
            self.server.condition.notify_all()
        body = json.dumps(
            {
                "operation_id": envelope["operation_id"],
                "code": code,
                "timestamp": int(time.time() * 1000),
                "data": result,
            }
        ).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        pass


class BusinessServer(MockWmsHttpServer):
    def __init__(self, *, rack_id=None, transport_code="RECEIVED", wait=False):
        super().__init__()
        self.RequestHandlerClass = _BusinessHandler
        self.transport_code = transport_code
        self.drain_rack_id = rack_id or f"RACK-{new_uuid7()[-12:]}"
        self.drain_result = (
            {"result": "WAIT", "reason_code": "NO_DRAIN_RACK_AVAILABLE", "retry_after_ms": 1}
            if wait
            else {"result": "READY", "racks": [{"rack_id": self.drain_rack_id, "rack_face": ["90"]}]}
        )


# 复用核心独占 migrated_database fixture，每例独立逻辑库以避免全局 worker 扫描互相污染。
rack_database = pytest_asyncio.fixture(loop_scope="module")(_database.__wrapped__)
