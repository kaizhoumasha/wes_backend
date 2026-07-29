"""平台扩展最小切片的 production-chain PostgreSQL heavy 性能预算。"""

from __future__ import annotations

import asyncio
import statistics
import subprocess
import sys
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import func, update
from sqlmodel import select

from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.device import Device
from src.app.device.repositories.command_repository import device_command_repository
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.smt_inbound_handoff import SmtInboundHandoffSourceItem
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import SmtInboundHandoffService
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    RuntimeInboxProcessorBridge,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_service import RuntimeInboxService
from src.app.runtime.system_capabilities.device.device_command_write.contracts import DeviceCommandWriteInput
from src.app.runtime.system_capabilities.replay import TimelineRecordedReplayService
from src.app.sys.models import SystemOutbox
from src.app.wms_integration.ports.document_operations import (
    ValidateRoughSorterAdmissionRequest,
    ValidateRoughSorterAdmissionResult,
)
from src.app.wms_integration.ports.query_outcome import QuerySuccess
from src.app.workline.models.workline import WorkLine
from src.core.conf import settings
from src.utils.timezone import timezone
from tests.support.runtime_inbox_processing_postgresql import (
    claim,
    seed_scan_flow,
    with_temporary_runtime_database,
)
from tests.support.smt_sorting_inbound_postgresql import (
    NoopQueueGateway,
    seed_smt_source_pick_claim,
)
from tests.support.wms_query_runtime import bind_stub_wms_query_runtime

if TYPE_CHECKING:
    import pytest

COLD_IMPORT_MEDIAN_MS = 1_500.0
NO_QUERY_INBOX_MEDIAN_MS = 500.0
WMS_QUERY_INBOX_MEDIAN_MS = 800.0
OUTBOX_ENQUEUE_MEDIAN_MS = 50.0
RECORDED_REPLAY_MEDIAN_MS = 20.0
SMT_SOURCE_PICK_ATTEMPT_MEDIAN_MS = 500.0
SMT_RECOVERY_BATCH_SIZE = 100
SMT_RECOVERY_COMMAND_QUERIES_PER_ITEM = 1
SMT_RECOVERY_CANDIDATE_LIMIT = 2
MEASURED_SAMPLE_COUNT = 5


async def _process_one(db: Any, service: RuntimeInboxService, queue_gateway: Any, *, token: str) -> dict[str, int]:
    claimed = await claim(db, service, token=token)
    return await RuntimeInboxProcessorBridge(inbox_service=service, queue_gateway=queue_gateway).process_claimed(
        db, claim=claimed
    )


def _q19_query_handler(http_calls: list[httpx.Request]):
    async def handler(
        request: ValidateRoughSorterAdmissionRequest,
    ) -> QuerySuccess[ValidateRoughSorterAdmissionResult]:
        assert isinstance(request, ValidateRoughSorterAdmissionRequest)
        http_calls.append(httpx.Request("POST", "http://wms-performance.invalid/documents/rough-sorter/admission"))
        return QuerySuccess(
            ValidateRoughSorterAdmissionResult(
                decision="ADMIT",
                grn_id="GRN-PERF-001",
                po_number="PO-PERF-001",
                po_item="10",
                material_code=request.six_in_one.HHPN,
                pkg_id=request.six_in_one.PkgID,
                measurement_decision="PASS",
                standard_reel_diameter_mm=request.reel_diameter_mm,
                reel_diameter_tolerance_mm="1",
                standard_reel_thickness_mm=request.reel_thickness_mm,
                reel_thickness_tolerance_mm="0.5",
                rule_version="performance-fixture-rule",
                source_version="performance-fixture-v1",
            ),
            evidence_key="evidence:performance-fixture",
        )

    return handler


async def _measure_inbox_operation(monkeypatch: pytest.MonkeyPatch, *, with_wms_query: bool, sample: int) -> float:
    measured: float | None = None

    async def scenario(session_factory: Any, queue_gateway: Any) -> None:
        nonlocal measured
        http_calls: list[httpx.Request] = []
        bind_stub_wms_query_runtime(monkeypatch, _q19_query_handler(http_calls))
        monkeypatch.setattr(settings, "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V2", "performance-fixture-secret")
        async with session_factory() as db:
            seeded = await seed_scan_flow(db, persist_q19_admit=not with_wms_query)
            service = RuntimeInboxService()
            if not with_wms_query:
                started = time.perf_counter()
                result = await _process_one(db, service, queue_gateway, token=f"perf-no-query-{sample}")
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

            started = time.perf_counter()
            result = await _process_one(db, service, queue_gateway, token=f"perf-q19-{sample}")
            measured = time.perf_counter() - started
            assert result["success"] == 1, result
            assert len(http_calls) == 1
            session = await db.get(WorklineSession, seeded.session_id)
            assert session is not None
            persisted_q19 = session.context_json["wms_admission_decision"]
            assert isinstance(persisted_q19, dict)
            assert persisted_q19["evidence_reference"] == "evidence:performance-fixture"

    await with_temporary_runtime_database(scenario)
    assert measured is not None
    return measured


async def _measure_outbox_and_replay() -> tuple[list[float], list[float]]:
    outbox_samples: list[float] = []
    replay_samples: list[float] = []

    async def scenario(session_factory: Any, queue_gateway: Any) -> None:
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            service = RuntimeInboxService()
            assert (await _process_one(db, service, queue_gateway, token="perf-replay-source"))["success"] == 1
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
            source_inbox = await db.get(RuntimeInbox, seeded.inbox_id)
            assert session is not None and workline is not None and device is not None and source_inbox is not None
            assert source_inbox.execution_session_id is not None
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
                command_code = f"CMD-PERF-EFFECT-{index}"
                dispatch_key = f"device-command:{command_code}"
                intent_log = RuntimeIntentLog(
                    execution_session_id=source_inbox.execution_session_id,
                    correlation_id="workline-session:IT-RUNTIME-INBOX-SESSION",
                    provider_code="RUNTIME",
                    operation_kind="system_capability_effect",
                    target_domain="device",
                    target_action="performance_sample",
                    idempotency_key=f"perf-outbox-{index}",
                    request_hash=f"{index:064x}",
                    dispatch_key=dispatch_key,
                )
                started = time.perf_counter()
                prepared_command, _outbox = await device_service.prepare_runtime_effect(
                    db,
                    session=session,
                    workline=workline,
                    request=DeviceCommandWriteInput(
                        target_device_id=seeded.arm_id,
                        action="PICK_AND_PUT",
                        payload={"sample": index},
                        command_code=command_code,
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
                    intent_log=intent_log,
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


async def _measure_smt_source_pick_generated_attempt() -> list[float]:
    samples: list[float] = []

    async def scenario(session_factory: Any, _queue_gateway: Any) -> None:
        async with session_factory() as db:
            for index in range(MEASURED_SAMPLE_COUNT + 1):
                seeded = await seed_smt_source_pick_claim(
                    db,
                    suffix=f"attempt-perf-{index}",
                    route_priority=MEASURED_SAMPLE_COUNT - index,
                )
                started = time.perf_counter()
                result = await RuntimeInboxProcessorBridge(queue_gateway=NoopQueueGateway()).process_claimed(
                    db,
                    claim=seeded.claim,
                )
                samples.append(time.perf_counter() - started)
                assert result["success"] == 1, result

    await with_temporary_runtime_database(scenario)
    return samples[1:]


class _CountingCommandRecoveryRepository:
    def __init__(self) -> None:
        self.calls = 0
        self.max_candidates = 0

    async def list_by_runtime_correlation(self, db: Any, **kwargs: Any) -> list[DeviceCommand]:
        self.calls += 1
        candidates = await device_command_repository.list_by_runtime_correlation(db, **kwargs)
        self.max_candidates = max(self.max_candidates, len(candidates))
        return candidates


def test_smt_source_pick_recovery_command_query_budget_for_100_items() -> None:
    async def scenario(session_factory: Any, _queue_gateway: Any) -> None:
        source_item_ids: list[int] = []
        async with session_factory() as db:
            for index in range(SMT_RECOVERY_BATCH_SIZE):
                seeded = await seed_smt_source_pick_claim(
                    db,
                    suffix=f"recovery-perf-{index:03d}",
                    route_priority=SMT_RECOVERY_BATCH_SIZE - index,
                )
                result = await RuntimeInboxProcessorBridge(queue_gateway=NoopQueueGateway()).process_claimed(
                    db,
                    claim=seeded.claim,
                )
                assert result["success"] == 1, result
                source_item_ids.append(seeded.source_item_id)

            await db.execute(
                update(SmtInboundHandoffSourceItem)
                .where(SmtInboundHandoffSourceItem.id.in_(source_item_ids))
                .values(
                    source_pick_command_id=None,
                    source_pick_command_code=None,
                    source_pick_dispatch_key=None,
                    updated_at=timezone.now_for_db() - timedelta(minutes=10),
                )
            )
            await db.commit()
            db.expire_all()

            counting_repository = _CountingCommandRecoveryRepository()
            summary = await SmtInboundHandoffService(
                command_repository=counting_repository,  # type: ignore[arg-type]
            ).scan_smt_inbound_handoff_demands_batch(
                db,
                scan_limit=0,
                recovery_limit=SMT_RECOVERY_BATCH_SIZE,
                claim_limit=0,
                stale_after_seconds=1,
            )
            await db.commit()

            assert summary["scanned"] == SMT_RECOVERY_BATCH_SIZE, summary
            assert summary["advanced"] == SMT_RECOVERY_BATCH_SIZE, summary
            assert counting_repository.calls <= (SMT_RECOVERY_BATCH_SIZE * SMT_RECOVERY_COMMAND_QUERIES_PER_ITEM)
            assert counting_repository.max_candidates <= SMT_RECOVERY_CANDIDATE_LIMIT

    asyncio.run(with_temporary_runtime_database(scenario))


def test_smt_source_pick_generated_attempt_median_budget() -> None:
    samples = asyncio.run(_measure_smt_source_pick_generated_attempt())
    measured = _assert_budget(
        "SMT source-pick generated attempt",
        samples,
        SMT_SOURCE_PICK_ATTEMPT_MEDIAN_MS,
    )
    print(
        "SMT source-pick performance samples(ms):",
        [round(item * 1_000, 3) for item in samples],
        "median(ms):",
        round(measured, 3),
    )


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
