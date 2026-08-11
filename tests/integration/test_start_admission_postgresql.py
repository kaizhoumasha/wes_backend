"""WorkLine START 准入的真实 PostgreSQL/HTTP 闭环。"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import pytest

from src.app.device.models.device import Device, DeviceProtocol, DeviceStatus
from src.app.runtime.capabilities.material_flow.start_admission_service import WorkLineStartAdmissionService
from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    workline_runtime_status_projection_service,
)
from src.app.workline.models.workline import LineType, WorkLine
from tests.support.runtime_inbox_processing_postgresql import (
    RecordingTaskQueueGateway,
    with_temporary_runtime_database,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration


@contextmanager
def _device_status_server(device_code: str) -> Iterator[tuple[int, list[str]]]:
    requested_paths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requested_paths.append(self.path)
            parsed = urlsplit(self.path)
            if parsed.path != "/api/v1/device/status" or parse_qs(parsed.query) != {"device_code": [device_code]}:
                self.send_response(422)
                self.end_headers()
                return
            body = json.dumps(
                {
                    "device_code": device_code,
                    "contract_key": "scanner.read",
                    "contract_version": "1.0",
                    "mode": "AUTO",
                    "status": "IDLE",
                    "current_command_code": None,
                    "error_detail": None,
                    "timestamp": 1_786_377_600_000,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_port), requested_paths
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_start_admission_uses_uniform_status_wire_and_persists_ready() -> None:
    device_code = "IT-START-SCANNER-01"
    with _device_status_server(device_code) as (port, requested_paths):

        async def scenario(
            session_factory: async_sessionmaker[AsyncSession],
            _queue_gateway: RecordingTaskQueueGateway,
        ) -> None:
            async with session_factory() as db:
                workline = WorkLine(
                    line_code="IT-START-ADMISSION",
                    line_name="START Admission Integration",
                    line_type=LineType.AUTO,
                    is_active=True,
                )
                db.add(workline)
                await db.flush()
                assert workline.id is not None
                workline_id = workline.id
                await workline_runtime_status_projection_service.ensure_default(db, workline_id=workline_id)
                db.add(
                    Device(
                        device_code=device_code,
                        device_name="START Admission Scanner",
                        work_line_id=workline_id,
                        device_role="SCANNER",
                        host="127.0.0.1",
                        port=port,
                        protocol=DeviceProtocol.HTTP,
                        device_status=DeviceStatus.IDLE,
                        capabilities_json={},
                    )
                )
                await db.commit()

                result = await WorkLineStartAdmissionService().admit_start(
                    db,
                    workline_id,
                    source_device_code=device_code,
                    request_id="it-start-request",
                    trace_id="it-start-trace",
                )

                db.expire_all()
                persisted = await db.get(WorkLine, workline_id)
                snapshot = await workline_runtime_status_projection_service.runtime_status_snapshot(
                    db,
                    workline_id=workline_id,
                    populate_existing=True,
                )
                assert result.accepted is True
                assert persisted is not None and persisted.start_admission_status == "SUCCESS"
                assert snapshot.runtime_status == "READY"

        asyncio.run(with_temporary_runtime_database(scenario))

    assert requested_paths == [f"/api/v1/device/status?device_code={device_code}"]
