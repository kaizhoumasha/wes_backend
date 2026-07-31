"""Material-flow sorter runtime 的 T5 边界与本地 join-gate 合同。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.app.runtime.capabilities.material_flow.sorter_inbound_runtime_service import (
    SorterInboundRuntimeService,
)
from src.app.runtime.orchestration.runtime_intent import RuntimeIntentKind

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "builder_name",
    ("build_rough_sorter_inbound_plan", "build_full_box_exchange_plan"),
)
def test_unmigrated_wms_runtime_builders_fail_closed(builder_name: str) -> None:
    builder = getattr(SorterInboundRuntimeService(), builder_name)

    with pytest.raises(RuntimeError, match="T5 synchronous WMS runtime is not implemented"):
        builder({})


def test_sorter_runtime_blocks_join_gate_failure_as_object_scope_reconciliation() -> None:
    plan = SorterInboundRuntimeService().build_sorter_inbound_plan(
        {
            "request_id": "sorter-runtime-join-001",
            "correlation_id": "corr-sorter-001",
            "provider_code": "WMS-A",
            "expected_authorized_bin_ids": ["BIN-A-001"],
            "actual_scanned_bin_id": "BIN-X-999",
            "target_bin_position_state": "IN_TRANSFER",
            "target_cell_reservable": False,
            "cell_reservation_state": "NONE",
            "waiting_deadline_declared": False,
            "object_key": "PKG-SORTER-001",
        }
    )

    assert plan.reconciliation_required is True
    assert plan.allowed_next_effect_scope == "OBJECT_ONLY"
    assert len(plan.intents) == 1
    assert plan.intents[0].kind == RuntimeIntentKind.BLOCK
    assert plan.intents[0].reason_code == "SORTER_JOIN_GATE_NOT_SATISFIED"
    assert set(plan.intents[0].payload_json["missing_conditions"]) == {
        "AUTHORIZED_BIN_RESOLVED",
        "TARGET_BIN_AT_WORK_POSITION",
        "TARGET_CELL_RESERVABLE",
        "CELL_RESERVATION_RESERVED",
        "WAITING_DEADLINE_DECLARED",
    }


def test_sorter_runtime_success_records_ready_to_drop_location_fact() -> None:
    plan = SorterInboundRuntimeService().build_sorter_inbound_plan(
        {
            "request_id": "sorter-runtime-ready-001",
            "correlation_id": "corr-sorter-ready-001",
            "provider_code": "WMS-A",
            "expected_authorized_bin_ids": ["BIN-A-001"],
            "actual_scanned_bin_id": "BIN-A-001",
            "target_bin_position_state": "AT_WORK_POSITION",
            "target_cell_reservable": True,
            "cell_reservation_state": "RESERVED",
            "waiting_deadline_declared": True,
            "target_work_position_code": "SORTER-WP-01",
            "object_key": "PKG-SORTER-READY-001",
        }
    )

    assert plan.reconciliation_required is False
    assert len(plan.intents) == 1
    assert plan.intents[0].kind == RuntimeIntentKind.RESOURCE_FACT
    assert plan.intents[0].payload_json["business_step"] == "SORTER_READY_TO_DROP"
    assert plan.intents[0].payload_json["location_code"] == "SORTER-WP-01"


def test_runtime_capability_service_does_not_branch_on_external_environment() -> None:
    source = (
        REPO_ROOT / "src" / "app" / "runtime" / "capabilities" / "material_flow" / "sorter_inbound_runtime_service.py"
    ).read_text()

    for forbidden in ("LOCAL_MOCK_ONLY", "production_write_path", "APP_ENV", "readiness_profile"):
        assert forbidden not in source
