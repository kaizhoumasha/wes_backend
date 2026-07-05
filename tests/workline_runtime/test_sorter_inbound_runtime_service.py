"""Phase4 sorter inbound runtime capability 合同。"""

from __future__ import annotations

from pathlib import Path

from src.app.runtime.orchestration.runtime_intent import RuntimeIntentKind

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_rough_sorter_runtime_builds_effect_intents_without_environment_branching() -> None:
    """粗分机入库 runtime path 只面向 provider contract，不判断外部是真设备还是模拟器。"""

    from src.app.runtime.capabilities.phase4.sorter_inbound_runtime_service import (
        Phase4SorterInboundRuntimeService,
    )

    service = Phase4SorterInboundRuntimeService()

    plan = service.build_rough_sorter_inbound_plan(
        {
            "request_id": "rough-runtime-001",
            "correlation_id": "corr-rough-001",
            "provider_code": "WMS-A",
            "object_key": "PKG-ROUGH-001",
            "target_cell_code": "CELL-A-01",
            "pkg_code": "PKG-ROUGH-001",
            "pallet_id": "PALLET-A-01",
            "station_code": "ROUGH-OUT-01",
            "material_code": "MAT-A",
            "quantity": 1,
            "warehouse_code": "WH-A",
            "source_event_id": "ecs-scan-001",
            "source_version": "ecs.v1",
        }
    )

    assert plan.legacy_plugin_entry_used is False
    assert plan.provider_code == "WMS-A"
    assert plan.contract_profile == "provider-contract"
    assert "mock" not in plan.model_dump_json().lower()
    assert "production" not in plan.model_dump_json().lower()

    intents_by_action = {intent.action or intent.target_code: intent for intent in plan.intents}
    assert [intent.kind for intent in plan.intents] == [
        RuntimeIntentKind.RESOURCE_RESERVATION,
        RuntimeIntentKind.RESOURCE_FACT,
        RuntimeIntentKind.EXTERNAL_REQUEST,
        RuntimeIntentKind.EXTERNAL_REQUEST,
    ]
    assert intents_by_action["CELL_RESERVATION_RESERVE"].payload_json["target_cell_code"] == "CELL-A-01"
    assert intents_by_action["RUNTIME_LOCATION_EVENT"].payload_json["business_step"] == "LOCAL_PHYSICAL_FACT"

    pkg_binding = plan.effect_contracts["WmsFulfillmentPort.notify_pkg_binding"]
    inventory = plan.effect_contracts["WmsInventoryTransactionPort.confirm_inbound"]
    assert pkg_binding["dispatch_key"] == "phase4:rough-runtime-001:pkg-binding"
    assert inventory["dispatch_key"] == "phase4:rough-runtime-001:inventory-confirm"
    assert pkg_binding["payload"]["package_id"] == "PKG-ROUGH-001"
    assert inventory["payload"]["warehouse_code"] == "WH-A"


def test_sorter_runtime_blocks_join_gate_failure_as_object_scope_reconciliation() -> None:
    """分拣机 join gate 未满足时不得静默选边，必须生成 object scope hold intent。"""

    from src.app.runtime.capabilities.phase4.sorter_inbound_runtime_service import (
        Phase4SorterInboundRuntimeService,
    )

    service = Phase4SorterInboundRuntimeService()

    plan = service.build_sorter_inbound_plan(
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
    assert plan.effect_contracts == {}
    assert len(plan.intents) == 1
    assert plan.intents[0].kind == RuntimeIntentKind.BLOCK
    assert plan.intents[0].reason_code == "SORTER_JOIN_GATE_NOT_SATISFIED"
    assert plan.intents[0].payload_json["scope_type"] == "OBJECT"
    assert set(plan.intents[0].payload_json["missing_conditions"]) == {
        "AUTHORIZED_BIN_RESOLVED",
        "TARGET_BIN_AT_WORK_POSITION",
        "TARGET_CELL_RESERVABLE",
        "CELL_RESERVATION_RESERVED",
        "WAITING_DEADLINE_DECLARED",
    }


def test_full_box_exchange_runtime_uses_fulfillment_intent_and_filters_sorting_candidates() -> None:
    """满箱交换必须通过 fulfillment contract 发起，不把已满箱对象送入逐件分拣候选。"""

    from src.app.runtime.capabilities.phase4.sorter_inbound_runtime_service import (
        Phase4SorterInboundRuntimeService,
    )

    service = Phase4SorterInboundRuntimeService()

    plan = service.build_full_box_exchange_plan(
        {
            "request_id": "full-box-runtime-001",
            "correlation_id": "corr-full-box-001",
            "provider_code": "WMS-A",
            "rack_code": "RACK-001",
            "rack_side": "A",
            "empty_box_id": "EMPTY-001",
            "full_box_id": "FULL-001",
            "full_box_object_keys": ["PKG-FULL-001"],
            "remaining_object_keys": ["PKG-FULL-001", "PKG-PIECE-001"],
        }
    )

    assert plan.reconciliation_required is False
    assert plan.evidence["sorting_candidate_object_keys"] == ["PKG-PIECE-001"]
    assert len(plan.intents) == 1
    assert plan.intents[0].kind == RuntimeIntentKind.RACK_BIN_EXCHANGE_REQUEST
    assert plan.intents[0].action == "FULL_BOX_EXCHANGE"
    assert plan.effect_contracts["WmsFulfillmentPort.full_box_exchange"]["payload"] == {
        "rack_id": "RACK-001",
        "empty_box_id": "EMPTY-001",
        "full_box_id": "FULL-001",
    }


def test_runtime_capability_service_does_not_branch_on_external_environment() -> None:
    """runtime capability 不能根据外部 provider 是否模拟来选择业务路径。"""

    source = (
        REPO_ROOT / "src" / "app" / "runtime" / "capabilities" / "phase4" / "sorter_inbound_runtime_service.py"
    ).read_text(encoding="utf-8")

    for forbidden in ("LOCAL_MOCK_ONLY", "production_write_path", "APP_ENV", "readiness_profile"):
        assert forbidden not in source
