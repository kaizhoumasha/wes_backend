"""WMS 全量工厂切换的零兼容发布护栏。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.app.callback.contracts.external_callbacks import (
    WMS_ALLOWED_CALLBACK_TYPES,
    validate_external_callback_type,
)
from src.app.runtime.capabilities.material_flow.sorter_inbound_runtime_service import (
    sorter_inbound_runtime_service,
)
from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_IDENTITIES
from src.app.wms_integration.services.callback_normalizer import (
    WMS_ALLOWED_CALLBACK_TYPES as NORMALIZER_WMS_ALLOWED_CALLBACK_TYPES,
)

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
    # 仍作为既有 rack 生命周期失败 reason code 使用，不再作为 callback_type。
    "RCS_RACK_OPERATION_FAILED",
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
    "src/app/workline/external_http_profile.py",
)
REMOVED_EXTERNAL_FACADE_LITERALS = {
    "WMS_RCS_RACK_OPERATION",
    "WMS_RCS_BIN_OPERATION",
    "WMS_RCS_FULL_BOX_EXCHANGE",
    "WMS_RCS_RACK_OPERATION_URL",
    "WMS_RCS_BIN_OPERATION_URL",
    "WMS_RCS_FULL_BOX_EXCHANGE_URL",
    "WMS_LEGACY_TRANSPORT",
    "WORKLINE_PLUGIN_RUNTIME",
    "workline.plugin-runtime",
    "workline.external-http",
    "freeze_plugin_external_http_binding",
    "_build_external_http_outbox_model",
    "WmsEffectPreparationService",
    "wms_effect_preparation_service",
}
ACTIVE_SCAN_ROOTS = (
    "src",
    "scripts",
    "tests/fixtures",
    "tests/resilience/fixtures",
    "docs/business",
    "docs/integration",
    "docs/architecture",
)
ACTIVE_SCAN_FILES = (
    ".env.dev",
    ".env.test",
    ".env.prod",
    "docker-compose.yml",
    "docker-compose.deploy.yml",
)
_FULL_CALLBACK_NEGATIVE_EVIDENCE = frozenset(
    {
        "RCS_BIN_MOVE_COMPLETED",
        "RCS_BIN_MOVE_FAILED",
        "RCS_BIN_MOVE_PROGRESS",
        "RCS_EMPTY_BOX_TRANSFER_RESULT",
        "RCS_EXCHANGE_COMPLETED",
        "RCS_FULL_BOX_EXCHANGE_RESULT",
        "RCS_FULL_BOX_TRANSFER_RESULT",
        "RCS_GRN_RECEIVED",
        "RCS_HANDLING_TASK_RESULT",
        "RCS_INVENTORY_UPDATED",
        "RCS_PALLET_ARRIVED",
        "RCS_PDA_OPERATION_RECORDED",
        "RCS_RACK_ARRIVED",
        "RCS_RACK_EXCHANGE_FAILED",
        "RCS_RACK_EXCHANGE_PROGRESS",
        "RCS_RACK_TASK_PROGRESS",
        "RCS_RACK_TASK_RESULT",
        "RCS_TASK_CHANGE",
        "RCS_TRANSPORT_COMPLETED",
        "WMS_BIN_MOVE_COMPLETED",
        "WMS_BIN_MOVE_FAILED",
        "WMS_BIN_MOVE_PROGRESS",
        "WMS_EMPTY_BOX_TRANSFER_RESULT",
        "WMS_EXCHANGE_COMPLETED",
        "WMS_FULL_BOX_EXCHANGE_RESULT",
        "WMS_FULL_BOX_TRANSFER_RESULT",
        "WMS_HANDLING_TASK_RESULT",
        "WMS_RACK_ARRIVED",
        "WMS_RACK_EXCHANGE_FAILED",
        "WMS_RACK_EXCHANGE_PROGRESS",
        "WMS_RACK_OPERATION_FAILED",
        "WMS_RACK_TASK_PROGRESS",
        "WMS_RACK_TASK_RESULT",
        "WMS_ROUGH_SORTER_INBOUND",
        "WMS_TASK_CHANGE",
        "WMS_TRANSPORT_COMPLETED",
    }
)
NEGATIVE_TEST_EVIDENCE_FILES = {
    "tests/api/test_callback_external_api.py": _FULL_CALLBACK_NEGATIVE_EVIDENCE,
    "tests/api/test_callback_route_contracts.py": frozenset({"WMS_FULL_BOX_EXCHANGE_RESULT", "WMS_RACK_TASK_RESULT"}),
    "tests/architecture/test_inbound_normalizer_profile_validation.py": frozenset(
        {"WMS_FULL_BOX_EXCHANGE_RESULT", "WMS_RACK_ARRIVED"}
    ),
    "tests/architecture/test_northbound_wms_typed_operation_boundaries.py": frozenset(
        {"wms_effect_preparation_service"}
    ),
    "tests/callback/test_callback_runtime_inbox_authority.py": frozenset({"WMS_RACK_TASK_RESULT"}),
    "tests/contracts/wms_integration/test_wms_operation_catalog.py": frozenset(
        {
            "WMS_FULL_BOX_EXCHANGE_RESULT",
            "WMS_TRANSPORT_COMPLETED",
            "wms.transport.handling@v1",
            "wms.transport.rack@v1",
        }
    ),
    "tests/wms_integration/test_callback_normalizer.py": _FULL_CALLBACK_NEGATIVE_EVIDENCE,
    "tests/workline_runtime/test_operation_sandbox_external_idempotency.py": frozenset({"WMS_RACK_TASK_RESULT"}),
}
SCOPED_ACTIVE_FORBIDDEN_LITERALS = {
    "docs/architecture/device-command-contract.md": frozenset(
        {"source_arm_prefetch_capacity", "扫码平台状态为 `FREE`"}
    ),
    "docs/architecture/workline-restructuring-module.md": frozenset(
        {"source_arm_prefetch_capacity", "扫码平台状态为 `FREE`"}
    ),
    "docs/architecture/workline-restructuring-implementation.md": frozenset(
        {
            "source_arm_prefetch_capacity",
            "扫码平台 FREE",
            "WMS/CTU 批量投箱入线与逐箱 callback",
            "扫码平台预取互锁及 manifest validator",
            "预取",
            "南向投放成功的 typed `COMMAND_RESULT`",
        }
    ),
    "docs/architecture/sorter-inbound-capability-spec.md": frozenset(
        {
            "source_arm_prefetch_capacity",
            "source arm prefetch",
            "WMS/CTU 批量投箱入线与逐箱 callback",
            "typed `COMMAND_RESULT` 解锁",
        }
    ),
    "docs/business/smt_sorter_inbound_workflow_guide.md": frozenset(
        {
            "扫码平台互锁状态机",
            "扫码平台业务状态机",
            "WMS/RCS 回调 | WMS/RCS | `EXTERNAL_HTTP`",
            "扫码平台业务投影：是否空闲",
            "硬件互锁：扫码平台空",
            "typed `COMMAND_RESULT` 解锁",
        }
    ),
    "docs/business/inbound_acceptance_steps.md": frozenset(
        {
            "RuntimeInbox` | WMS/ECS/CTU/AGV callback",
            "CTU 逐箱取出满箱 callback",
            "CTU 逐箱放入五层货架 callback",
            "CTU 将空箱补回单层货架 callback",
            "WMS/CTU 从五层货架逐箱取出",
            "CTU 从退料线逐箱取出并放回五层货架",
            "WES 更新料箱在途位置",
        }
    ),
    **{
        f"tests/fixtures/external_contracts/wms/default/{fixture_name}.json": frozenset(
            {"transport_request_id", "source_location", "target_location"}
        )
        for fixture_name in ("success", "reject", "timeout")
    },
}


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


def test_active_artifacts_have_no_removed_callback_or_external_facade_literal() -> None:
    """生产、部署、fixture、脚本和活跃文档必须全部零旧入口。"""

    paths = [REPO_ROOT / relative_path for relative_path in ACTIVE_SCAN_FILES]
    for relative_root in ACTIVE_SCAN_ROOTS:
        paths.extend(path for path in (REPO_ROOT / relative_root).rglob("*") if path.is_file())

    offenders: dict[str, set[str]] = {}
    forbidden = REMOVED_UNAMBIGUOUS_TERMINAL_CALLBACKS | REMOVED_EXTERNAL_FACADE_LITERALS | REMOVED_TRANSPORT_IDENTITIES
    for path in paths:
        if path.suffix not in {"", ".dev", ".json", ".md", ".prod", ".py", ".sh", ".test", ".yml"}:
            continue
        source = path.read_text(encoding="utf-8")
        relative_path = str(path.relative_to(REPO_ROOT))
        scoped_forbidden = SCOPED_ACTIVE_FORBIDDEN_LITERALS.get(relative_path, frozenset())
        found = {literal for literal in forbidden | scoped_forbidden if literal in source}
        if relative_path == "src/app/wms_integration/provider_manifest.py":
            found -= REMOVED_TRANSPORT_IDENTITIES
        if found:
            offenders[relative_path] = found

    assert offenders == {}


def test_removed_literals_in_tests_are_explicit_negative_evidence_only() -> None:
    offenders: dict[str, set[str]] = {}
    forbidden = REMOVED_UNAMBIGUOUS_TERMINAL_CALLBACKS | REMOVED_EXTERNAL_FACADE_LITERALS | REMOVED_TRANSPORT_IDENTITIES
    for path in (REPO_ROOT / "tests").rglob("*"):
        if not path.is_file() or path.suffix not in {".json", ".py", ".yml"}:
            continue
        relative_path = str(path.relative_to(REPO_ROOT))
        if relative_path == "tests/contracts/wms_integration/test_release_removal_guardrails.py":
            continue
        found = frozenset(literal for literal in forbidden if literal in path.read_text(encoding="utf-8"))
        allowed = NEGATIVE_TEST_EVIDENCE_FILES.get(relative_path, frozenset())
        if found != allowed:
            offenders[relative_path] = set(found ^ allowed)

    assert offenders == {}


def test_removed_transport_facade_ports_and_handlers_are_absent() -> None:
    assert all(not (REPO_ROOT / relative_path).exists() for relative_path in REMOVED_TRANSPORT_FILES)
    write_back_source = (REPO_ROOT / "src/app/workline/services/write_back_service.py").read_text()
    intent_effect_source = (REPO_ROOT / "src/app/runtime/orchestration/runtime_intent_effects.py").read_text()
    assert "_build_external_http_outbox_model" not in write_back_source
    assert "WMS external HTTP facade is removed" in intent_effect_source


def test_all_runtime_inbox_external_writers_use_callback_domain_allow_set() -> None:
    assert NORMALIZER_WMS_ALLOWED_CALLBACK_TYPES is WMS_ALLOWED_CALLBACK_TYPES
    writer_source = (REPO_ROOT / "src/app/runtime/orchestration/consumers/callback_runtime_inbox_writer.py").read_text()
    orchestration_source = (REPO_ROOT / "src/app/callback/services/callback_orchestration_service.py").read_text()
    sandbox_source = (REPO_ROOT / "src/app/runtime/orchestration/services/intent/operation_service.py").read_text()
    for source in (writer_source, orchestration_source, sandbox_source):
        assert "validate_external_callback_type" in source

    for callback_type in ("WMS_RACK_TASK_RESULT", "RACK_OPERATION"):
        with pytest.raises(ValueError, match="callback_type is not allowed"):
            validate_external_callback_type(
                {"callback_type": callback_type, "source_system": "WMS"},
            )


def test_active_wms_docs_do_not_publish_legacy_callback_or_transport_paths() -> None:
    for relative_path in (
        "docs/business/wms_rcs_interface_requirements.md",
        "docs/business/rough_sorter_runtime_flow.md",
        "docs/integration/wms_rcs_interface_requirements.md",
        "docs/architecture/SRS.md",
        "docs/architecture/sorter-inbound-capability-spec.md",
    ):
        source = (REPO_ROOT / relative_path).read_text()
        assert all(callback_type not in source for callback_type in REMOVED_TERMINAL_CALLBACKS)
        assert "/api/v1/callback/result" not in source
        assert "/api/wes/transport-request" not in source


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


def test_shared_wms_effect_system_outbox_producer_is_removed_until_t5() -> None:
    services_root = REPO_ROOT / "src/app/runtime/orchestration/services"
    assert not (services_root / "wms_effect_preparation_service.py").exists()
    services_exports = (services_root / "__init__.py").read_text()
    assert "WmsEffectPreparationService" not in services_exports
    assert "wms_effect_preparation_service" not in services_exports


def test_generic_system_outbox_tests_do_not_publish_wms_positive_wiring() -> None:
    forbidden = {
        "wms.fulfillment.request_rack_supply@v1",
        "wms.fulfillment.notify_pkg_binding@v1",
        "WMS_FULFILLMENT_REQUEST_RACK_SUPPLY",
        "WMS_FULFILLMENT_REQUEST_RACK_TRANSPORT",
        "WMS_INVENTORY_TRANSFER",
        "http://wms-rcs/api/wes/transport-request",
        "http://wms/api/fulfillment/rack-supply",
    }
    for relative_path in (
        "tests/sys/test_system_outbox_engine.py",
        "tests/sys/test_external_http_transport_mapping.py",
    ):
        source = (REPO_ROOT / relative_path).read_text()
        assert {literal for literal in forbidden if literal in source} == set()


def test_active_docs_and_wms_fixtures_do_not_publish_removed_prefetch_or_transport_contract() -> None:
    for relative_path in (
        "docs/architecture/device-command-contract.md",
        "docs/architecture/workline-restructuring-module.md",
        "docs/architecture/workline-restructuring-implementation.md",
    ):
        source = (REPO_ROOT / relative_path).read_text()
        assert "source_arm_prefetch_capacity" not in source
        assert "扫码平台状态为 `FREE`" not in source

    for fixture_name in ("success.json", "reject.json", "timeout.json"):
        source = (REPO_ROOT / "tests/fixtures/external_contracts/wms/default" / fixture_name).read_text()
        assert "transport_request_id" not in source
        assert "source_location" not in source
        assert "target_location" not in source


def test_callback_ingress_reuses_callback_domain_wms_allow_set() -> None:
    from importlib import import_module

    callback_ingress_module = import_module("src.app.callback.services.callback_ingress_service")

    assert callback_ingress_module._EXTERNAL_CALLBACK_WMS_ALLOWED_TYPES is WMS_ALLOWED_CALLBACK_TYPES
    source = (REPO_ROOT / "src/app/callback/services/callback_ingress_service.py").read_text()
    assert "from src.app.callback.contracts.external_callbacks import WMS_ALLOWED_CALLBACK_TYPES" in source
    assert "WMS_TYPED_EFFECT_CALLBACK_TYPES" not in source


def test_rack_operation_read_side_regression_coverage_is_preserved() -> None:
    source = (REPO_ROOT / "tests/rack/test_rack_operation_service.py").read_text()
    required_tests = {
        "test_derive_operation_status_requires_all_required_tasks_succeeded",
        "test_derive_operation_status_requires_resource_projection_confirmation",
        "test_derive_operation_status_callback_trusted_skips_resource_projection_confirmation",
        "test_sync_operation_status_marks_reconciliation_expected",
        "test_derive_operation_status_consumes_projection_per_inbound_task",
        "test_derive_operation_status_reconciles_when_move_out_rack_still_at_source_position",
    }
    assert {test_name for test_name in required_tests if f"def {test_name}" not in source} == set()


def test_final_acceptance_sorter_docs_freeze_pick_ack_and_batch_only_ctu_boundary() -> None:
    required_pick_ack_docs = (
        "docs/business/smt_sorter_inbound_workflow_guide.md",
        "docs/architecture/sorter-inbound-capability-spec.md",
        "docs/architecture/workline-restructuring-implementation.md",
    )
    for relative_path in required_pick_ack_docs:
        source = (REPO_ROOT / relative_path).read_text()
        assert "southbound_pick_acknowledged" in source
        assert "扫码平台业务投影：是否空闲" not in source
        assert "typed `COMMAND_RESULT` 解锁" not in source
        assert "南向投放成功的 typed `COMMAND_RESULT`" not in source

    acceptance_source = (REPO_ROOT / "docs/business/inbound_acceptance_steps.md").read_text()
    for forbidden in (
        "RuntimeInbox` | WMS/ECS/CTU/AGV callback",
        "CTU 逐箱取出满箱 callback",
        "CTU 逐箱放入五层货架 callback",
        "CTU 将空箱补回单层货架 callback",
        "WMS/CTU 从五层货架逐箱取出",
        "CTU 从退料线逐箱取出并放回五层货架",
        "WES 更新料箱在途位置",
    ):
        assert forbidden not in acceptance_source
    assert "WES 只消费 WMS 批次终态" in acceptance_source


def test_coarse_fulfillment_port_and_positive_contract_tests_are_physically_removed() -> None:
    import importlib.util

    assert importlib.util.find_spec("src.app.wms_integration.ports.fulfillment") is None
    assert not (REPO_ROOT / "src/app/wms_integration/ports/fulfillment.py").exists()
    assert not (REPO_ROOT / "tests/contracts/workline/test_wms_fulfillment_request_contract.py").exists()


def test_rack_operation_direct_kind_and_move_rack_projection_regressions_are_preserved() -> None:
    source = (REPO_ROOT / "tests/rack/test_rack_operation_service.py").read_text()
    required_tests = {
        "test_derive_operation_status_requires_matching_rack_kind_projection",
        "test_derive_operation_status_requires_move_rack_target_projection",
    }
    assert {test_name for test_name in required_tests if f"def {test_name}" not in source} == set()


def test_negative_evidence_allowlist_is_literal_scoped() -> None:
    assert isinstance(NEGATIVE_TEST_EVIDENCE_FILES, dict)
    assert all(
        isinstance(relative_path, str) and isinstance(allowed_literals, frozenset)
        for relative_path, allowed_literals in NEGATIVE_TEST_EVIDENCE_FILES.items()
    )
