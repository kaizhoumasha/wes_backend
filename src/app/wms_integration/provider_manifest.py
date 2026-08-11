"""由静态 registry 派生的 Provider、conformance 与业务覆盖 manifest。"""

from __future__ import annotations

from dataclasses import dataclass

from src.app.wms_integration.operation_contract import WmsCompletionMode, WmsOperationDefinition
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY, WMS_OPERATIONS

WMS_PROVIDER_OPERATION_MANIFEST = WMS_OPERATIONS


@dataclass(frozen=True, slots=True)
class WmsConformanceRequirement:
    """每项 operation 必须具备 typed fixture 和统一合同题。"""

    operation: WmsOperationDefinition
    required_cases: tuple[str, ...]


_QUERY_CASES = ("success", "business_reject", "timeout", "rate_limit", "malformed", "budget")
_INVENTORY_QUERY_CASES = (
    "success",
    "empty",
    "missing_field",
    "invalid_decimal",
    "reject",
    "timeout",
    "rate_limit",
    "unavailable",
    "malformed",
    "pagination",
    "precision",
    "budget",
    "evidence_failure",
)
_SYNC_EFFECT_CASES = (
    "success",
    "business_reject",
    "idempotent_replay",
    "idempotency_conflict",
    "in_progress",
    "timeout",
)
_ASYNC_EFFECT_CASES = (
    "accepted",
    "business_reject",
    "idempotent_replay",
    "idempotency_conflict",
    "status_query",
    "partial_failure",
)


def conformance_cases_for_operation(operation: WmsOperationDefinition) -> tuple[str, ...]:
    """按 operation mode family 返回已评审题库，不允许 Provider 自定义弱化。"""

    if operation.identity == "wms.inventory.query_inventory@v1":
        return _INVENTORY_QUERY_CASES
    if operation.completion_mode is None:
        return _QUERY_CASES
    if operation.completion_mode is WmsCompletionMode.ASYNC_TASK:
        return _ASYNC_EFFECT_CASES
    return _SYNC_EFFECT_CASES


WMS_CONFORMANCE_REQUIREMENTS = tuple(
    WmsConformanceRequirement(
        operation=operation,
        required_cases=conformance_cases_for_operation(operation),
    )
    for operation in WMS_OPERATIONS
)


@dataclass(frozen=True, slots=True)
class WmsBusinessScenario:
    """业务场景到 operation identity 的机器可校验归属。"""

    scenario_code: str
    operation_identities: frozenset[str]


WMS_BUSINESS_SCENARIO_MANIFEST = (
    WmsBusinessScenario(
        "MASTER_DATA_AND_ROUTING",
        frozenset(
            {
                "wms.master_data.get_material@v1",
                "wms.master_data.list_materials@v1",
                "wms.master_data.list_zones@v1",
                "wms.master_data.list_locations@v1",
                "wms.master_data.get_rack@v1",
                "wms.master_data.list_racks@v1",
                "wms.master_data.get_bin@v1",
            }
        ),
    ),
    WmsBusinessScenario(
        "RECEIVING",
        frozenset(
            {
                "wms.document.get_grn@v1",
                "wms.document.list_grn_packages@v1",
                "wms.inventory.confirm_inbound@v1",
                "wms.fulfillment.notify_pkg_binding@v1",
            }
        ),
    ),
    WmsBusinessScenario(
        "OUTBOUND_AND_RESERVATION",
        frozenset(
            {
                "wms.document.get_pick_order@v1",
                "wms.document.get_outbound_order@v1",
                "wms.document.get_wave@v1",
                "wms.inventory.query_inventory@v1",
                "wms.inventory.get_reservation@v1",
                "wms.inventory.reserve_inventory@v1",
                "wms.inventory.release_reservation@v1",
                "wms.inventory.confirm_outbound@v1",
            }
        ),
    ),
    WmsBusinessScenario(
        "INVENTORY_ACCOUNTING",
        frozenset(
            {
                "wms.inventory.transfer_inventory@v1",
                "wms.inventory.confirm_return_putaway@v1",
            }
        ),
    ),
    WmsBusinessScenario(
        "RACK_AND_FULL_BOX_FULFILLMENT",
        frozenset(
            {
                "wms.fulfillment.request_rack_supply@v1",
                "wms.fulfillment.request_rack_transport@v1",
                "wms.fulfillment.change_rack_face@v1",
                "wms.fulfillment.request_load_unit_transport@v1",
                "wms.fulfillment.cancel_request@v1",
            }
        ),
    ),
    WmsBusinessScenario(
        "RECOVERY_AND_RECONCILIATION",
        frozenset(
            {
                "wms.document.get_task_snapshot@v1",
                "wms.reconciliation.check_bin_drift@v1",
                "wms.reconciliation.check_rack_drift@v1",
                "wms.reconciliation.check_full_drift@v1",
                "wms.fulfillment.publish_manual_task@v1",
            }
        ),
    ),
)

_scenario_coverage = set().union(*(scenario.operation_identities for scenario in WMS_BUSINESS_SCENARIO_MANIFEST))
if _scenario_coverage != set(WMS_OPERATION_BY_IDENTITY):
    raise RuntimeError("WMS business scenario manifest must cover every registered operation")


def require_full_factory_registry(operation_identities: tuple[str, ...]) -> None:
    """在 T2/T5 接线前 fail closed，禁止旧四项 profile 冒充全工厂合同。"""

    expected = tuple(operation.identity for operation in WMS_OPERATIONS)
    if operation_identities != expected:
        raise ValueError("active WMS profile does not match the frozen 31-operation registry")


__all__ = [
    "WMS_BUSINESS_SCENARIO_MANIFEST",
    "WMS_CONFORMANCE_REQUIREMENTS",
    "WMS_PROVIDER_OPERATION_MANIFEST",
    "WmsBusinessScenario",
    "WmsConformanceRequirement",
    "conformance_cases_for_operation",
    "require_full_factory_registry",
]
