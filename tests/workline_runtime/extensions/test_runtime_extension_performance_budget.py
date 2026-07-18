"""平台扩展最小切片的稳定性能预算。"""

from __future__ import annotations

import asyncio
import statistics
import subprocess
import sys
import time
from types import SimpleNamespace

from src.app.runtime.system_capabilities.gateway import GatewayQueryResult
from src.app.runtime.system_capabilities.outcomes import Success
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.contracts import (
    PROFILE_IDENTITY,
    RoughSorterBindingSnapshot,
    RoughSorterInventoryAdmissionOutput,
)
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.runtime.workline_plugins.rough_sorter.config import RoughSorterConfig
from src.app.runtime.workline_plugins.rough_sorter.handlers import RoughSorterFacts, decide
from src.app.runtime.workline_plugins.rough_sorter.inputs import (
    parse_pick_and_put_result,
    parse_replay_request,
    parse_scan_completed,
)
from src.app.runtime.workline_plugins.rough_sorter.state import RoughSorterState
from tests.support.runtime_inbox_processing_postgresql import RecordingTaskQueueGateway

COLD_IMPORT_MEDIAN_MS = 1_500.0
NO_QUERY_DECISION_MEDIAN_MS = 2.0
ONE_QUERY_DECISION_MEDIAN_MS = 3.0
OUTBOX_ENQUEUE_MEDIAN_MS = 0.2
RECORDED_REPLAY_MEDIAN_MS = 1.0


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
            plugin_config_hash="a" * 64,
            generated_index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        ),
    )


class _QueryGateway:
    async def execute(self, _capability_key: str, _contract_version: str, _input_data: object) -> GatewayQueryResult:
        return GatewayQueryResult(
            outcome=Success(
                payload=RoughSorterInventoryAdmissionOutput(
                    accepted=True,
                    material_code="MAT-PERF",
                    batch_no="LOT-PERF",
                    warehouse_code="WH",
                    matched_item_count=1,
                    available_quantity=1,
                    source_version="perf-v1",
                )
            ),
            evidence=SimpleNamespace(reference="perf:evidence"),
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

    async def decisions() -> tuple[list[float], list[float], list[float]]:
        no_query: list[float] = []
        one_query: list[float] = []
        replay: list[float] = []
        gateway = _QueryGateway()
        for _ in range(21):
            started = time.perf_counter()
            await decide(
                parse_scan_completed({"PkgID": "PKG-PERF"}),
                state=RoughSorterState(),
                config=_config(),
                facts=_facts(),
                gateway=gateway,
            )
            no_query.append(time.perf_counter() - started)

            started = time.perf_counter()
            await decide(
                parse_pick_and_put_result(
                    {
                        "command_code": "CMD-PERF",
                        "command_type": "PICK_AND_PUT",
                        "result": "SUCCESS",
                        "data": {"reel_diameter": 100, "reel_thickness": 10},
                    }
                ),
                state=RoughSorterState(phase="PICK_TO_PIPELINE", current_correlation="CMD-PERF"),
                config=_config(),
                facts=_facts(),
                gateway=gateway,
            )
            one_query.append(time.perf_counter() - started)

            started = time.perf_counter()
            await decide(
                parse_replay_request({"idempotency_key": "perf-replay", "payload_digest": "a" * 64}),
                state=RoughSorterState(),
                config=_config(),
                facts=_facts(),
                gateway=gateway,
                replay=True,
            )
            replay.append(time.perf_counter() - started)
        return no_query[1:], one_query[1:], replay[1:]

    no_query_samples, query_samples, replay_samples = asyncio.run(decisions())
    queue = RecordingTaskQueueGateway()
    outbox_samples: list[float] = []
    for index in range(21):
        started = time.perf_counter()
        queue.enqueue_outbox(index, limit=50)
        outbox_samples.append(time.perf_counter() - started)

    measurements = {
        "cold generated index import": _assert_budget(
            "cold generated index import", cold_samples, COLD_IMPORT_MEDIAN_MS
        ),
        "single Inbox no-QUERY decision": _assert_budget(
            "single Inbox no-QUERY decision", no_query_samples, NO_QUERY_DECISION_MEDIAN_MS
        ),
        "single WMS QUERY decision": _assert_budget(
            "single WMS QUERY decision", query_samples, ONE_QUERY_DECISION_MEDIAN_MS
        ),
        "Outbox enqueue": _assert_budget("Outbox enqueue", outbox_samples[1:], OUTBOX_ENQUEUE_MEDIAN_MS),
        "recorded replay": _assert_budget("recorded replay", replay_samples, RECORDED_REPLAY_MEDIAN_MS),
    }
    print("performance medians(ms):", {key: round(value, 3) for key, value in measurements.items()})
