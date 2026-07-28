"""WMS 全量工厂切换的零兼容发布护栏。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.app.callback.services.callback_orchestration_service import _WMS_EXTERNAL_CALLBACK_TYPES
from src.app.runtime.capabilities.material_flow.sorter_inbound_runtime_service import (
    sorter_inbound_runtime_service,
)
from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_IDENTITIES
from src.app.wms_integration.services.callback_normalizer import WMS_ALLOWED_CALLBACK_TYPES

REPO_ROOT = Path(__file__).parents[3]
REMOVED_TRANSPORT_IDENTITIES = {
    "wms.legacy-transport.production",
    "wms.transport.rack@v1",
    "wms.transport.handling@v1",
}
REMOVED_TERMINAL_CALLBACKS = {
    "RCS_GRN_RECEIVED",
    "RCS_PALLET_ARRIVED",
    "RCS_INVENTORY_UPDATED",
    "RCS_PDA_OPERATION_RECORDED",
    "WMS_EXCHANGE_COMPLETED",
    "RCS_EXCHANGE_COMPLETED",
    "WMS_TASK_CHANGE",
    "RCS_TASK_CHANGE",
    "WMS_REJECTED",
    "RCS_REJECTED",
    "WMS_FAILED",
    "RCS_FAILED",
    "WMS_RACK_TASK_RESULT",
    "RCS_RACK_TASK_RESULT",
    "WMS_RACK_TASK_PROGRESS",
    "RCS_RACK_TASK_PROGRESS",
    "WMS_RACK_ARRIVED",
    "RCS_RACK_ARRIVED",
    "WMS_RACK_EXCHANGE_PROGRESS",
    "RCS_RACK_EXCHANGE_PROGRESS",
    "WMS_RACK_EXCHANGE_FAILED",
    "RCS_RACK_EXCHANGE_FAILED",
    "WMS_RACK_OPERATION_FAILED",
    "RCS_RACK_OPERATION_FAILED",
    "WMS_BIN_MOVE_PROGRESS",
    "RCS_BIN_MOVE_PROGRESS",
    "WMS_BIN_MOVE_COMPLETED",
    "RCS_BIN_MOVE_COMPLETED",
    "WMS_BIN_MOVE_FAILED",
    "RCS_BIN_MOVE_FAILED",
    "WMS_TRANSPORT_COMPLETED",
    "RCS_TRANSPORT_COMPLETED",
    "WMS_FULL_BOX_EXCHANGE_RESULT",
    "RCS_FULL_BOX_EXCHANGE_RESULT",
    "WMS_EMPTY_BOX_TRANSFER_RESULT",
    "RCS_EMPTY_BOX_TRANSFER_RESULT",
    "WMS_FULL_BOX_TRANSFER_RESULT",
    "RCS_FULL_BOX_TRANSFER_RESULT",
    "WMS_HANDLING_TASK_RESULT",
    "RCS_HANDLING_TASK_RESULT",
    "WMS_ROUGH_SORTER_INBOUND",
}
# 这四个短名字也用于领域 reason/status，源码文本扫描会误报；它们仍由下方
# normalizer 拒绝合同和 API 参数化合同覆盖。
REMOVED_UNAMBIGUOUS_TERMINAL_CALLBACKS = REMOVED_TERMINAL_CALLBACKS - {
    "WMS_REJECTED",
    "RCS_REJECTED",
    "WMS_FAILED",
    "RCS_FAILED",
}
REMOVED_TRANSPORT_SYMBOLS = {
    "WmsTransportContractService",
    "WmsTransportMigrationRequiredError",
    "WmsRackTaskRequest",
    "WmsRcsRackGateway",
    "WmsRcsHandlingGateway",
    "freeze_legacy_transport_binding",
    "freeze_rack_task_binding",
}
REMOVED_TRANSPORT_FILES = (
    "src/app/wms_integration/services/transport_contract.py",
    "src/app/rack/services/gateway.py",
    "src/app/handling/services/gateway.py",
)


def test_removed_transport_and_terminal_callbacks_exist_only_in_migration_manifest() -> None:
    offenders: dict[str, set[str]] = {}
    for path in (REPO_ROOT / "src").rglob("*.py"):
        source = path.read_text()
        forbidden = REMOVED_UNAMBIGUOUS_TERMINAL_CALLBACKS | REMOVED_TRANSPORT_SYMBOLS
        if path.name != "provider_manifest.py":
            forbidden |= REMOVED_TRANSPORT_IDENTITIES
        found = {removed for removed in forbidden if removed in source}
        if found:
            offenders[str(path.relative_to(REPO_ROOT))] = found

    assert offenders == {}


def test_removed_transport_facade_ports_and_handlers_are_absent() -> None:
    assert all(not (REPO_ROOT / relative_path).exists() for relative_path in REMOVED_TRANSPORT_FILES)


def test_callback_orchestration_and_normalizer_share_the_same_wms_allow_set() -> None:
    assert _WMS_EXTERNAL_CALLBACK_TYPES == WMS_ALLOWED_CALLBACK_TYPES


def test_active_wms_docs_do_not_publish_legacy_callback_or_transport_paths() -> None:
    for relative_path in (
        "docs/business/wms_rcs_interface_requirements.md",
        "docs/business/rough_sorter_runtime_flow.md",
    ):
        source = (REPO_ROOT / relative_path).read_text()
        assert all(callback_type not in source for callback_type in REMOVED_TERMINAL_CALLBACKS)
        assert "/api/v1/callback/result" not in source
        assert "/api/wms/" not in source


def test_legacy_wms_operation_contracts_and_handlers_are_removed() -> None:
    contracts_source = (REPO_ROOT / "src/app/runtime/system_capabilities/wms/contracts.py").read_text()
    assert "WmsOperationContract" not in contracts_source
    for removed_port in (
        "confirm_inbound_operation.py",
        "notify_pkg_binding_operation.py",
        "full_box_exchange_operation.py",
    ):
        assert not (REPO_ROOT / "src/app/wms_integration/ports" / removed_port).exists()

    assert set(SYSTEM_CAPABILITY_IDENTITIES).isdisjoint(
        {
            ("wms.inventory.confirm_inbound", "v1"),
            ("wms.fulfillment.notify_pkg_binding", "v1"),
            ("wms.fulfillment.full_box_exchange", "v1"),
        }
    )


@pytest.mark.parametrize(
    "builder",
    (
        sorter_inbound_runtime_service.build_rough_sorter_inbound_plan,
        sorter_inbound_runtime_service.build_full_box_exchange_plan,
    ),
)
def test_unmigrated_sync_wms_runtime_entrypoints_fail_closed(builder) -> None:
    with pytest.raises(RuntimeError, match="T5 synchronous WMS runtime is not implemented"):
        builder({})


def test_external_contract_profile_documents_only_registry_and_status_hint() -> None:
    source = (REPO_ROOT / "docs/contracts/external-contract-profile.md").read_text()

    assert "operation_blueprint_count: 35" in source
    assert "WMS_EFFECT_STATUS_HINT" in source
    for event_type in (
        "WMS_GRN_RECEIVED",
        "WMS_PALLET_ARRIVED",
        "WMS_INVENTORY_UPDATED",
        "WMS_PDA_OPERATION_RECORDED",
    ):
        assert event_type in source
    assert "WmsFulfillmentPort.request_transport" not in source
    assert "WMS_TRANSPORT_COMPLETED" not in source
