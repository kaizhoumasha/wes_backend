"""平台扩展最小切片的 production-chain PostgreSQL heavy 性能预算。"""

from __future__ import annotations

import asyncio
import json
import statistics
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import Request
from sqlalchemy import func
from sqlmodel import select

from src.app.callback.services.callback_ingress_service import CallbackIngressService
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.device import Device
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_service import RuntimeInboxService
from src.app.runtime.system_capabilities.device.device_command_write.contracts import DeviceCommandWriteInput
from src.app.runtime.system_capabilities.replay import TimelineRecordedReplayService
from src.app.sys.models import SystemOutbox
from src.app.wms_integration.services import (
    WmsCircuitBreakerService,
    WmsEndpointConfig,
    WmsHttpClient,
    WmsHttpTimeoutConfig,
    WmsTypedPortService,
)
from src.app.workline.models.workline import WorkLine
from tests.support.runtime_inbox_processing_postgresql import (
    claim,
    processor,
    seed_scan_flow,
    with_temporary_runtime_database,
)

if TYPE_CHECKING:
    import pytest

COLD_IMPORT_MEDIAN_MS = 1_500.0
NO_QUERY_INBOX_MEDIAN_MS = 500.0
WMS_QUERY_INBOX_MEDIAN_MS = 800.0
OUTBOX_ENQUEUE_MEDIAN_MS = 50.0
RECORDED_REPLAY_MEDIAN_MS = 20.0
MEASURED_SAMPLE_COUNT = 5


def _callback_request(payload: dict[str, object]) -> Request:
    body = json.dumps(payload).encode("utf-8")
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/callback/result",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 12345),
        },
        receive=receive,
    )


async def _process_one(db: Any, service: RuntimeInboxService, *, token: str) -> dict[str, int]:
    claimed = await claim(db, service, token=token)
    return await processor(service).process_claimed(db, claim=claimed)


def _wms_service(session_factory: Any, http_calls: list[httpx.Request]) -> WmsTypedPortService:
    async def handler(request: httpx.Request) -> httpx.Response:
        http_calls.append(request)
        return httpx.Response(
            200,
            json={
                "request_id": request.url.params.get("request_id", "perf-wms"),
                "items": [
                    {
                        "sku": "MAT-IT-001",
                        "warehouse_code": "WH-IT",
                        "lot_no": "LOT-IT-001",
                        "available_qty": "10",
                        "total_qty": "10",
                        "reserved_qty": "0",
                    }
                ],
            },
        )

    return WmsTypedPortService(
        session_factory=session_factory,
        endpoint_config=WmsEndpointConfig(
            base_url="http://wms-performance.test/api",
            timeout=WmsHttpTimeoutConfig(connect=1, read=1, write=1, pool=1),
        ),
        http_client=WmsHttpClient(transport=httpx.MockTransport(handler)),
        breaker_service=WmsCircuitBreakerService(failure_threshold=2, retry_after_seconds=60),
        cache=None,
    )


async def _measure_inbox_operation(monkeypatch: pytest.MonkeyPatch, *, with_wms_query: bool, sample: int) -> float:
    measured: float | None = None

    async def scenario(session_factory: Any, _queue_gateway: Any) -> None:
        nonlocal measured
        http_calls: list[httpx.Request] = []
        monkeypatch.setattr(
            "src.app.wms_integration.services.wms_typed_port_service",
            _wms_service(session_factory, http_calls),
        )
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            service = RuntimeInboxService()
            if not with_wms_query:
                started = time.perf_counter()
                result = await _process_one(db, service, token=f"perf-no-query-{sample}")
                measured = time.perf_counter() - started
                assert result["success"] == 1
                assert http_calls == []
                evidence_count = await db.scalar(
                    select(func.count())
                    .select_from(WorklineTimeline)
                    .where(
                        WorklineTimeline.related_inbox_id == seeded.inbox_id,
                        WorklineTimeline.payload_json["record_type"].as_string() == "SYSTEM_CAPABILITY_EVIDENCE",
                    )
                )
                assert evidence_count == 0
                return

            assert (await _process_one(db, service, token=f"perf-query-seed-{sample}"))["success"] == 1
            command = await db.scalar(
                select(DeviceCommand).where(
                    DeviceCommand.workline_id == seeded.workline_id,
                    DeviceCommand.task_type == "PICK_AND_PUT",
                )
            )
            assert command is not None
            source_event_id = f"perf-wms-result-{sample}"
            response = await CallbackIngressService().handle_result(
                _callback_request(
                    {
                        "command_code": command.command_code,
                        "device_code": "IT-ARM-01",
                        "result": "SUCCESS",
                        "finish_time": int(time.time() * 1000),
                        "source_event_id": source_event_id,
                        "trace_id": seeded.trace_id,
                        "data": {"reel_diameter": 100, "reel_thickness": 10},
                    }
                ),
                db,
                request_id=f"perf-wms-request-{sample}",
                start_time=time.time(),
                enqueue_processing=lambda: None,
            )
            assert response["code"] == "1000"
            callback = await db.scalar(select(RuntimeInbox).where(RuntimeInbox.source_event_id == source_event_id))
            assert callback is not None

            started = time.perf_counter()
            result = await _process_one(db, service, token=f"perf-query-{sample}")
            measured = time.perf_counter() - started
            assert result["success"] == 1
            assert len(http_calls) == 1
            evidence = await db.scalar(
                select(WorklineTimeline).where(
                    WorklineTimeline.related_inbox_id == callback.id,
                    WorklineTimeline.payload_json["record_type"].as_string() == "SYSTEM_CAPABILITY_EVIDENCE",
                )
            )
            assert evidence is not None
            assert evidence.payload_json["evidence"]["capability_key"] == "wms.rough_sorter_inventory_admission"
            assert evidence.payload_json["evidence"]["contract_version"] == "v1"

    await with_temporary_runtime_database(scenario)
    assert measured is not None
    return measured


async def _measure_outbox_and_replay() -> tuple[list[float], list[float]]:
    outbox_samples: list[float] = []
    replay_samples: list[float] = []

    async def scenario(session_factory: Any, _queue_gateway: Any) -> None:
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            service = RuntimeInboxService()
            assert (await _process_one(db, service, token="perf-replay-source"))["success"] == 1
            decision = await db.scalar(
                select(WorklineTimeline)
                .where(
                    WorklineTimeline.related_inbox_id == seeded.inbox_id,
                    WorklineTimeline.payload_json["record_type"].as_string() == "PLUGIN_DECISION",
                )
                .order_by(WorklineTimeline.id.desc())
            )
            assert decision is not None
            replay_service = TimelineRecordedReplayService()
            for _ in range(MEASURED_SAMPLE_COUNT + 1):
                started = time.perf_counter()
                replay = await replay_service.load(
                    db,
                    source_inbox_id=seeded.inbox_id,
                    expected_definition_identity=decision.payload_json["definition_identity"],
                    expected_binding_identity=decision.payload_json["binding_identity"],
                    expected_index_digest=decision.payload_json["index_digest"],
                )
                replay_samples.append(time.perf_counter() - started)
                assert replay.hold_reason is None and replay.decision is not None

            session = await db.get(WorklineSession, seeded.session_id)
            workline = await db.get(WorkLine, seeded.workline_id)
            device = await db.get(Device, seeded.arm_id)
            assert session is not None and workline is not None and device is not None
            source_command = await db.scalar(
                select(DeviceCommand).where(
                    DeviceCommand.workline_id == seeded.workline_id,
                    DeviceCommand.device_id == seeded.arm_id,
                )
            )
            assert source_command is not None
            source_command.status = CommandStatus.COMPLETED
            await db.commit()
            device_service = DeviceCommandService()
            for index in range(MEASURED_SAMPLE_COUNT + 1):
                started = time.perf_counter()
                prepared_command, _outbox = await device_service.prepare_runtime_effect(
                    db,
                    session=session,
                    workline=workline,
                    request=DeviceCommandWriteInput(
                        target_device_id=seeded.arm_id,
                        action="PICK_AND_PUT",
                        payload={"sample": index},
                        result_policy="COMMAND_RESULT",
                    ),
                    target_device_id=seeded.arm_id,
                    target_device_code=None,
                    expected_workline_id=seeded.workline_id,
                    expected_fact_version=f"device:v{device.version}",
                    expected_available=True,
                    idempotency_key=f"perf-outbox-{index}",
                    execution_correlation_id="workline-session:IT-RUNTIME-INBOX-SESSION",
                    trace_id=seeded.trace_id,
                )
                await db.commit()
                outbox_samples.append(time.perf_counter() - started)
                # 清理动作位于计时窗口外，下一样本仍遵守设备唯一未完成命令门禁。
                prepared_command.status = CommandStatus.COMPLETED
                await db.commit()
            assert await db.scalar(select(func.count()).select_from(SystemOutbox)) == MEASURED_SAMPLE_COUNT + 2

    await with_temporary_runtime_database(scenario)
    return outbox_samples[1:], replay_samples[1:]


def _assert_budget(name: str, samples: list[float], budget_ms: float) -> float:
    sample_ms = [item * 1_000 for item in samples]
    measured = statistics.median(sample_ms)
    assert measured <= budget_ms, f"{name} median {measured:.3f}ms exceeds {budget_ms:.3f}ms; samples={sample_ms}"
    return measured


def test_runtime_extension_minimum_slice_performance_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    """测量窗口只包围单次 production operation，不包含建库、migration 与 fixture setup。"""

    async def invalidate_cache(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(DeviceCommandService, "_invalidate_command_cache", invalidate_cache)
    cold_samples: list[float] = []
    command = [
        sys.executable,
        "-c",
        "import src.app.runtime.system_capabilities.generated_index; "
        "import src.app.runtime.workline_plugins.generated_index",
    ]
    for _ in range(3):
        started = time.perf_counter()
        subprocess.run(command, check=True, capture_output=True, text=True)
        cold_samples.append(time.perf_counter() - started)

    async def run() -> tuple[list[float], list[float], list[float], list[float]]:
        # 每条链路先运行一次完整 warmup，再记录后续独立临时库样本。
        await _measure_inbox_operation(monkeypatch, with_wms_query=False, sample=-1)
        no_query = [
            await _measure_inbox_operation(monkeypatch, with_wms_query=False, sample=index)
            for index in range(MEASURED_SAMPLE_COUNT)
        ]
        await _measure_inbox_operation(monkeypatch, with_wms_query=True, sample=-1)
        wms_query = [
            await _measure_inbox_operation(monkeypatch, with_wms_query=True, sample=index)
            for index in range(MEASURED_SAMPLE_COUNT)
        ]
        outbox, replay = await _measure_outbox_and_replay()
        return no_query, wms_query, outbox, replay

    no_query_samples, wms_query_samples, outbox_samples, replay_samples = asyncio.run(run())
    measurements = {
        "cold generated index import": _assert_budget(
            "cold generated index import", cold_samples, COLD_IMPORT_MEDIAN_MS
        ),
        "single RuntimeInbox no-QUERY": _assert_budget(
            "single RuntimeInbox no-QUERY", no_query_samples, NO_QUERY_INBOX_MEDIAN_MS
        ),
        "formal callback WMS QUERY": _assert_budget(
            "formal callback WMS QUERY", wms_query_samples, WMS_QUERY_INBOX_MEDIAN_MS
        ),
        "Outbox enqueue": _assert_budget("Outbox enqueue", outbox_samples, OUTBOX_ENQUEUE_MEDIAN_MS),
        "recorded replay": _assert_budget("recorded replay", replay_samples, RECORDED_REPLAY_MEDIAN_MS),
    }
    samples = {
        "cold generated index import": [round(item * 1_000, 3) for item in cold_samples],
        "single RuntimeInbox no-QUERY": [round(item * 1_000, 3) for item in no_query_samples],
        "formal callback WMS QUERY": [round(item * 1_000, 3) for item in wms_query_samples],
        "Outbox enqueue": [round(item * 1_000, 3) for item in outbox_samples],
        "recorded replay": [round(item * 1_000, 3) for item in replay_samples],
    }
    print("performance samples(ms):", samples)
    print("performance medians(ms):", {key: round(value, 3) for key, value in measurements.items()})
