"""平台扩展最小切片的 production-path 性能预算。"""

from __future__ import annotations

import asyncio
import statistics
import subprocess
import sys
import time
from decimal import Decimal
from types import SimpleNamespace

from sqlmodel import select

from src.app.device.models.device import Device
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    GeneratedPluginAttemptRunner,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_service import RuntimeInboxService
from src.app.runtime.system_capabilities.device.device_command_write.contracts import DeviceCommandWriteInput
from src.app.runtime.system_capabilities.evidence import QueryEvidence
from src.app.runtime.system_capabilities.gateway import GatewayQueryResult
from src.app.runtime.system_capabilities.outcomes import Success
from src.app.runtime.system_capabilities.replay import TimelineRecordedReplayService
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.contracts import (
    PROFILE_IDENTITY,
    RoughSorterBindingSnapshot,
    RoughSorterInventoryAdmissionOutput,
)
from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, PluginAttemptContext
from src.app.runtime.workline_plugins.dispatcher import PinnedPluginSnapshot, PluginDispatchRequest
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.runtime.workline_plugins.rough_sorter.config import RoughSorterConfig
from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION
from src.app.runtime.workline_plugins.rough_sorter.handlers import RoughSorterFacts
from src.app.workline.models.workline import WorkLine
from tests.support.runtime_inbox_processing_postgresql import (
    claim,
    processor,
    seed_scan_flow,
    with_temporary_runtime_database,
)

COLD_IMPORT_MEDIAN_MS = 1_500.0
NO_QUERY_DECISION_MEDIAN_MS = 8.0
ONE_QUERY_DECISION_MEDIAN_MS = 10.0
OUTBOX_ENQUEUE_MEDIAN_MS = 40.0
RECORDED_REPLAY_MEDIAN_MS = 15.0


def _config() -> RoughSorterConfig:
    return RoughSorterConfig.model_validate(
        {
            "device_roles": {"input_arm": "INPUT_ARM", "conveyor": "CONVEYOR", "output_arm": "OUTPUT_ARM"},
            "pipeline_input_location": "PIPELINE-IN",
            "pipeline_output_location": "PIPELINE-OUT",
            "ng_location": "NG",
            "warehouse_code": "WH",
            "owner_code": "OWNER",
            "provider_profile": PROFILE_IDENTITY,
        }
    )


def _facts() -> RoughSorterFacts:
    return RoughSorterFacts(
        business_key="PKG-PERF",
        hhpn="MAT-PERF",
        lot_code="LOT-PERF",
        binding_snapshot=RoughSorterBindingSnapshot(
            binding_id=1,
            binding_version=1,
            profile_identity=PROFILE_IDENTITY,
            plugin_config_hash=sha256_digest(_config().model_dump(mode="json")),
            generated_index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        ),
    )


class _QueryGateway:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, _capability_key: str, _contract_version: str, _input_data: object) -> GatewayQueryResult:
        self.calls += 1
        serialized_input = _input_data.model_dump(mode="json") if hasattr(_input_data, "model_dump") else _input_data
        output = RoughSorterInventoryAdmissionOutput(
            accepted=True,
            material_code="MAT-PERF",
            batch_no="LOT-PERF",
            warehouse_code="WH",
            matched_item_count=1,
            available_quantity=Decimal("1"),
            source_version="perf-v1",
        )
        evidence = QueryEvidence(
            capability_key="wms.rough_sorter_inventory_admission",
            contract_version="v1",
            input_hash=sha256_digest(serialized_input),
            output_hash=sha256_digest(output.model_dump(mode="json")),
            authority="WMS",
            source="performance-production-gateway",
            evidence_at="2026-07-18T00:00:00Z",
            source_version="perf-v1",
            admission_snapshot={"profile_identity": PROFILE_IDENTITY},
            summary={"outcome": {"type": "Success"}},
        )
        return GatewayQueryResult(outcome=Success(payload=output), evidence=evidence)


def _dispatch_request(*, query: bool) -> PluginDispatchRequest:
    config = _config().model_dump(mode="json")
    state = {"phase": "PICK_TO_PIPELINE", "current_correlation": "CMD-PERF"} if query else {"phase": "READY"}
    raw_input = (
        {
            "command_code": "CMD-PERF",
            "command_type": "PICK_AND_PUT",
            "result": "SUCCESS",
            "data": {"reel_diameter": 100, "reel_thickness": 10},
        }
        if query
        else {"PkgID": "PKG-PERF"}
    )
    return PluginDispatchRequest(
        plugin_key=DEFINITION.plugin_key,
        contract_version=DEFINITION.contract_version,
        logical_route="PICK_AND_PUT_RESULT" if query else "SCAN_COMPLETED",
        raw_config=config,
        raw_state=state,
        context_state=state,
        raw_input=raw_input,
        raw_facts=_facts().model_dump(mode="json"),
        snapshot=PinnedPluginSnapshot(
            plugin_key=DEFINITION.plugin_key,
            contract_version=DEFINITION.contract_version,
            binding_identity="binding:1:1",
            binding_id=1,
            binding_version=1,
            config_hash=sha256_digest(config),
            index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
            profile_identity=PROFILE_IDENTITY,
        ),
    )


def _context(*, query: bool, gateway: _QueryGateway, attempt: int) -> PluginAttemptContext:
    request = _dispatch_request(query=query)
    return PluginAttemptContext(
        attempt_id=f"perf-{attempt}",
        inbox_id=attempt + 1,
        session_id=1,
        workline_id=1,
        event_type=request.logical_route,
        payload=request.raw_input,
        plugin_state=request.raw_state,
        snapshot=AttemptSnapshot(
            processor_token=f"perf-{attempt}",
            session_version=1,
            plugin_state_version=1,
            binding_id=1,
            binding_version=1,
            plugin_config_hash=request.snapshot.config_hash,
            index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        ),
        runtime=SimpleNamespace(gateway=gateway),
        dispatch_request=request,
    )


def _median_ms(samples: list[float]) -> float:
    return statistics.median(samples) * 1_000


def _assert_budget(name: str, samples: list[float], budget_ms: float) -> float:
    measured = _median_ms(samples)
    assert measured <= budget_ms, (
        f"{name} median {measured:.3f}ms exceeds {budget_ms:.3f}ms; "
        f"samples={[round(item * 1_000, 3) for item in samples]}"
    )
    return measured


def test_runtime_extension_minimum_slice_performance_budgets() -> None:
    """冷启动、Runner、DB Outbox 与 repository replay 均测 production owner。"""

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

    measurements: dict[str, float] = {}

    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        no_query: list[float] = []
        one_query: list[float] = []
        gateway = _QueryGateway()
        runner = GeneratedPluginAttemptRunner()
        for index in range(11):
            started = time.perf_counter()
            no_query_write_set = await runner.run(_context(query=False, gateway=gateway, attempt=index))
            no_query.append(time.perf_counter() - started)
            assert no_query_write_set.evidence == ()

            started = time.perf_counter()
            query_write_set = await runner.run(_context(query=True, gateway=gateway, attempt=index + 100))
            one_query.append(time.perf_counter() - started)
            assert len(query_write_set.evidence) == 1
            assert isinstance(query_write_set.evidence[0], QueryEvidence)

        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            inbox_service = RuntimeInboxService()
            claimed = await claim(db, inbox_service, token="perf-source")
            result = await processor(inbox_service).process_claimed(db, claim=claimed)
            assert result["processed"] == 1

            timeline = await db.scalar(
                select(WorklineTimeline)
                .where(
                    WorklineTimeline.related_inbox_id == seeded.inbox_id,
                    WorklineTimeline.payload_json["record_type"].as_string() == "PLUGIN_DECISION",
                )
                .order_by(WorklineTimeline.id.desc())
            )
            assert timeline is not None
            payload = timeline.payload_json
            replay_service = TimelineRecordedReplayService()
            replay_samples: list[float] = []
            for _ in range(11):
                started = time.perf_counter()
                replay = await replay_service.load(
                    db,
                    source_inbox_id=seeded.inbox_id,
                    expected_definition_identity=payload["definition_identity"],
                    expected_binding_identity=payload["binding_identity"],
                    expected_index_digest=payload["index_digest"],
                )
                replay_samples.append(time.perf_counter() - started)
                assert replay.hold_reason is None and replay.decision is not None

            session = await db.get(WorklineSession, seeded.session_id)
            workline = await db.get(WorkLine, seeded.workline_id)
            device = await db.get(Device, seeded.arm_id)
            assert session is not None and workline is not None and device is not None
            outbox_samples: list[float] = []
            device_service = DeviceCommandService()
            for index in range(11):
                started = time.perf_counter()
                await device_service.prepare_runtime_effect(
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
                    trace_id=seeded.trace_id,
                )
                await db.commit()
                outbox_samples.append(time.perf_counter() - started)

        measurements.update(
            {
                "single Inbox no-QUERY decision": _assert_budget(
                    "single Inbox no-QUERY decision", no_query[1:], NO_QUERY_DECISION_MEDIAN_MS
                ),
                "single WMS QUERY decision": _assert_budget(
                    "single WMS QUERY decision", one_query[1:], ONE_QUERY_DECISION_MEDIAN_MS
                ),
                "Outbox enqueue": _assert_budget("Outbox enqueue", outbox_samples[1:], OUTBOX_ENQUEUE_MEDIAN_MS),
                "recorded replay": _assert_budget("recorded replay", replay_samples[1:], RECORDED_REPLAY_MEDIAN_MS),
            }
        )

    asyncio.run(with_temporary_runtime_database(scenario))
    measurements["cold generated index import"] = _assert_budget(
        "cold generated index import", cold_samples, COLD_IMPORT_MEDIAN_MS
    )
    print("performance medians(ms):", {key: round(value, 3) for key, value in measurements.items()})
