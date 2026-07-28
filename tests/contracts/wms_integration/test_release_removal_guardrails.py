"""WMS 全量工厂切换的零兼容发布护栏。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.app.runtime.capabilities.material_flow.sorter_inbound_runtime_service import (
    sorter_inbound_runtime_service,
)
from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_IDENTITIES

REPO_ROOT = Path(__file__).parents[3]
REMOVED_TRANSPORT_IDENTITIES = {
    "wms.transport.rack@v1",
    "wms.transport.handling@v1",
}
REMOVED_TERMINAL_CALLBACKS = {
    "WMS_TRANSPORT_COMPLETED",
    "WMS_FULL_BOX_EXCHANGE_RESULT",
}


def test_removed_transport_and_terminal_callbacks_exist_only_in_migration_manifest() -> None:
    offenders: dict[str, set[str]] = {}
    for path in (REPO_ROOT / "src").rglob("*.py"):
        if path.name == "provider_manifest.py":
            continue
        source = path.read_text()
        found = {removed for removed in REMOVED_TRANSPORT_IDENTITIES | REMOVED_TERMINAL_CALLBACKS if removed in source}
        if found:
            offenders[str(path.relative_to(REPO_ROOT))] = found

    assert offenders == {}


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
    assert "WmsFulfillmentPort.request_transport" not in source
    assert "WMS_TRANSPORT_COMPLETED" not in source
