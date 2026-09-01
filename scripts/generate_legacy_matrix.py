#!/usr/bin/env python3
"""扫描 legacy 代码入口，生成 legacy-cleanup-matrix.csv。

按 P0-002 规范对每个入口赋 entry_type / current_owner / business_semantics /
strategy / drop_phase / risk。发现命令对齐 SPEC §Proposed Change 的入口粒度。

用法: uv run python scripts/generate_legacy_matrix.py
产出: docs/architecture/legacy-cleanup-matrix.csv + 汇总统计到 stdout
"""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
# 生成器以脚本路径直接执行时，Python 默认只把 scripts/ 放入 import path。
sys.path.insert(0, str(REPO_ROOT))

# 扫描目标
SCAN_DIRS = {
    "src/app/workline": "workline",
    "src/workline_runtime": "workline_runtime",
    "src/workline_plugins": "workline_plugins",
    "tests/workline_runtime": "workline_runtime",
    "docs/templates/workline_plugin": "workline_runtime",
}

# runtime migration 会把 workline/services 的实现物理迁入 runtime/orchestration
# 或 runtime/capabilities，但 legacy cleanup matrix 仍需按旧入口追踪清理策略。
MIGRATED_SERVICE_IMPLS = {
    "src/app/workline/services/dispatch_attempt_service.py": (
        "src/app/runtime/orchestration/services/inbox/dispatch_attempt_service.py"
    ),
    "src/app/workline/services/device_command_gateway.py": (
        "src/app/runtime/orchestration/services/device_command_gateway.py"
    ),
    "src/app/workline/services/inbox_service.py": "src/app/runtime/orchestration/services/inbox/inbox_service.py",
    "src/app/workline/services/object_transition_event_service.py": (
        "src/app/runtime/orchestration/services/inbox/object_transition_event_service.py"
    ),
    "src/app/workline/services/operation_service.py": "src/app/runtime/orchestration/services/intent/operation_service.py",
    "src/app/workline/services/outbox_dispatch_service.py": (
        "src/app/runtime/orchestration/services/inbox/outbox_dispatch_service.py"
    ),
    "src/app/workline/services/runtime_hold_creation_service.py": (
        "src/app/runtime/orchestration/services/hold/runtime_hold_creation_service.py"
    ),
    "src/app/workline/services/runtime_hold_query_service.py": (
        "src/app/runtime/orchestration/services/hold/runtime_hold_query_service.py"
    ),
    "src/app/workline/services/runtime_hold_release_service.py": (
        "src/app/runtime/orchestration/services/hold/runtime_hold_release_service.py"
    ),
    "src/app/workline/services/runtime_query_service.py": (
        "src/app/runtime/orchestration/services/query/runtime_query_service.py"
    ),
    "src/app/workline/services/runtime_reconciliation_service.py": (
        "src/app/runtime/orchestration/services/reconciliation/runtime_reconciliation_service_impl.py"
    ),
    "src/app/workline/services/start_admission_service.py": ("src/app/workline/services/workline_start_service.py"),
    "src/app/workline/services/timeline_sequence_service.py": (
        "src/app/runtime/orchestration/services/trace/timeline_sequence_service.py"
    ),
    "src/app/workline/services/trace_query_service.py": (
        "src/app/runtime/orchestration/services/trace/trace_query_service.py"
    ),
    "src/app/workline/services/trace_resource_view_builder.py": (
        "src/app/runtime/orchestration/services/trace/trace_resource_view_builder.py"
    ),
    "src/app/workline/services/trace_response_builder.py": (
        "src/app/runtime/orchestration/services/trace/trace_response_builder.py"
    ),
}
MIGRATED_IMPL_TO_LEGACY = {impl: legacy for legacy, impl in MIGRATED_SERVICE_IMPLS.items()}
# 旧服务删除前的历史顶层符号是 legacy provenance 的正向真源。
# 目标 runtime 后续新增的 helper/result 不得反向膨胀历史清理范围。
MIGRATED_SERVICE_SYMBOL_PROVENANCE: dict[str, tuple[str, ...]] = {
    "src/app/workline/services/device_command_gateway.py": (
        "DeviceCommandGateway",
        "_DeviceCommandGovernanceError",
        "_build_device_command_log_envelope",
        "_build_device_status_precheck_detail",
        "_build_device_status_url",
        "_enforce_device_command_governance",
        "_ensure_realtime_device_status_ready",
        "_extract_device_status_state",
        "_get_device_for_command_dispatch",
        "_is_same_session_current_command",
        "_mark_device_command_failed_if_dispatch_exhausted",
        "_mark_outbox_blocked_by_workline_state",
        "_raise_device_command_governance_error",
        "_raise_device_status_precheck_wait",
        "_redact_device_command_payload",
        "_release_device_runtime_if_failed_command_was_current",
        "_resolve_command_type_for_governance",
        "_resolve_device_command_path",
        "_resolve_device_protocol_scheme",
        "_resolve_device_status_path",
        "_resolve_device_status_timeout_seconds",
    ),
    "src/app/workline/services/dispatch_attempt_service.py": (
        "WorklineDispatchAttemptService",
        "_enum_value",
        "_finalize_attempt",
        "_flush_if_supported",
        "_next_attempt_no",
    ),
    "src/app/workline/services/inbox_service.py": (
        "DuplicateInboxError",
        "WorklineInboxService",
        "_SupportsIsoformat",
        "_format_deadline",
        "_validate_internal_handoff_correlation",
    ),
    "src/app/workline/services/object_transition_event_service.py": (
        "ObjectTransitionEventService",
        "_domain_value",
        "_escape_key_part",
        "_required",
    ),
    "src/app/workline/services/operation_service.py": (
        "WorklineOperationService",
        "_enum_value",
    ),
    "src/app/workline/services/outbox_dispatch_service.py": (
        "DispatchResult",
        "OutboxDispatchService",
        "_block_outbox_for_device_busy",
        "_block_outbox_for_device_resource_wait",
        "_block_outbox_for_workline_safety",
        "_count_workline_safety_block_result",
        "_dispatch_failure_diagnostic_code",
        "_escalate_status_precheck_wait_if_needed",
        "_is_device_busy_attempt",
        "_is_device_idle_for_requeue",
        "_is_dispatched_command",
        "_is_status_precheck_wait_over_ttl",
        "_latest_dispatch_attempt",
        "_remember_blocked_head_device_scope",
        "_repair_orphaned_device_busy_dispatches",
        "_repair_self_blocked_device_busy_dispatches",
        "_resolve_device_id_for_target_code",
        "_resource_wait_detail",
        "_resource_wait_diagnostic_key",
        "_resource_wait_elapsed_seconds",
    ),
    "src/app/workline/services/runtime_hold_creation_service.py": (
        "RuntimeHoldCreationService",
        "_dt_key",
        "_enum_value",
        "_int_attr",
        "_json_attr",
        "_required_int_attr",
        "_str_attr",
    ),
    "src/app/workline/services/runtime_hold_query_service.py": (
        "RuntimeHoldQueryService",
        "_as_dict",
        "_enum_value",
    ),
    "src/app/workline/services/runtime_hold_release_service.py": (
        "ReturnToNgReleaseContext",
        "RuntimeHoldReleaseError",
        "RuntimeHoldReleaseService",
        "_as_dict",
        "_enum_value",
        "_is_valid_runtime_measurement_payload",
        "_latest_matching_late_callback_data",
        "_requires_runtime_measurement_continue_payload",
        "_runtime_continue_result_payload",
        "_valid_measurement_reel_payload",
        "_validate_runtime_measurement_continue_payload",
    ),
    "src/app/workline/services/runtime_query_service.py": (
        "RuntimeQueryService",
        "_BlockedOutboxProjection",
        "_DeviceIdentity",
        "_RuntimeEvidenceSession",
        "_RuntimeResourceEvidenceProjection",
        "_activity_dt",
        "_api_utc_datetime",
        "_blocked_outbox_is_earlier",
        "_build_device_identity_maps",
        "_command_duration_ms",
        "_datetime_or_none",
        "_dedupe_runtime_resource_evidence_items",
        "_device_identity_from_source",
        "_device_session_clause",
        "_enum_str",
        "_event_type_from_payload",
        "_first_payload_str",
        "_first_text",
        "_first_value",
        "_highest_priority_state",
        "_int_or_none",
        "_is_maintenance_device",
        "_is_timed_out",
        "_latest_activity_at",
        "_latest_rows_subquery",
        "_make_runtime_resource_evidence_item",
        "_non_empty_text",
        "_parse_session_id",
        "_payload_dict",
        "_recent_failed_clause",
        "_recent_failure_or_timeout_clause",
        "_recent_failure_since",
        "_require_int_id",
        "_resolve_trace_device",
        "_runtime_active_snapshot_cell_payloads",
        "_runtime_active_snapshot_flat_cell_payloads",
        "_runtime_active_snapshot_nested_bin_cell_payloads",
        "_runtime_active_snapshot_parent_metadata",
        "_runtime_cell_code",
        "_runtime_metadata_default_group_has_value",
        "_runtime_normalized_station_position_defaults",
        "_runtime_payload_with_metadata_defaults",
        "_runtime_resource_evidence_item_key",
        "_runtime_resource_evidence_item_sort_key",
        "_runtime_resource_evidence_items_from_active_snapshot",
        "_runtime_resource_evidence_items_from_payload",
        "_runtime_resource_evidence_kind_from_payload",
        "_runtime_resource_evidence_metadata",
        "_runtime_resource_evidence_payloads",
        "_runtime_resource_evidence_rack_kind",
        "_runtime_resource_evidence_reel_payloads",
        "_runtime_resource_kind",
        "_runtime_set_metadata_default_value",
        "_session_initial_payload_display_identity",
        "_status_str",
        "_trace_action_source",
        "_trace_current_action",
        "_trace_path_has_facts",
        "_waiting_not_timed_out_clause",
        "_waiting_timeout_clause",
    ),
    "src/app/workline/services/runtime_reconciliation_service.py": (
        "WorklineRuntimeReconciliationService",
        "_as_dict",
        "_canonical_json_hash",
        "_dt_key",
        "_enum_value",
        "_late_callback_evidence_key",
        "_payload_int",
        "_payload_str",
        "_resolve_id",
    ),
    "src/app/workline/services/start_admission_service.py": (
        "StartAdmissionResult",
        "StartAdmissionStatusFetchResult",
        "StartAdmissionStatusTarget",
        "WorkLineStartAdmissionService",
    ),
    "src/app/workline/services/timeline_sequence_service.py": (
        "_dialect_name",
        "add_timeline_with_sequence",
        "allocate_timeline_seq_no",
    ),
    "src/app/workline/services/trace_query_service.py": (
        "TraceQueryResult",
        "TraceQueryService",
        "_callback_diagnostic_extra",
        "_enum_str",
        "_merge_unique_by_id",
        "_payload_dict",
        "_safe_str",
        "_timeline_trace",
    ),
    "src/app/workline/services/trace_resource_view_builder.py": (
        "_bin_field",
        "_fill_bin_key_from_existing",
        "_first_non_empty",
        "_iter_active_bin_rack_payloads",
        "_iter_bin_cells",
        "_iter_flat_cells",
        "_iter_nested_bins",
        "_merge_active_bin_rack",
        "_merge_bin_bucket",
        "_merge_cell_payload",
        "_merge_non_empty",
        "_parse_iso_timestamp",
        "_payload_sort_key",
        "_payload_timestamp",
        "_resolve_bin_key",
        "build_trace_resource_view",
    ),
    "src/app/workline/services/trace_response_builder.py": (
        "_blocked_wait_seconds",
        "_build_callback_log_item",
        "_build_command_item",
        "_build_diagnostic_item",
        "_build_dispatch_attempt_item",
        "_build_inbox_item",
        "_build_outbox_item",
        "_build_resource_evidence",
        "_build_session_item",
        "_build_timeline_item",
        "_build_trace_summary",
        "_enum_str",
        "_resource_evidence_dict",
        "_resource_wait_detail_summary",
        "_status_str",
        "build_failed_command_evidence",
        "build_trace_response",
        "build_trace_session_item",
        "build_trace_timeline_item",
    ),
}

# Business legacy cleanup 会把旧 WorkLine domain 业务合同迁入
# runtime/capabilities/material_flow/contracts。matrix 必须继续按 legacy entry_id 记账,
# 否则文件删除后 audit trace 会误以为业务承载项已经消失。
MIGRATED_DOMAIN_IMPLS = {
    "src/app/workline/domain/contracts/six_in_one.py": "src/app/runtime/capabilities/material_flow/contracts/six_in_one.py",
    "src/app/workline/domain/material_identity.py": "src/app/runtime/capabilities/material_flow/contracts/material_identity.py",
    "src/app/workline/domain/ng_reason.py": "src/app/runtime/capabilities/material_flow/contracts/ng_reason.py",
}

# runtime migration F-1/F-2:workline/repositories 运行态 repository 物理迁入
# runtime/orchestration/repositories。CAPABILITY_IMPLEMENTATION_IMPORT seed
# 仍按旧入口追踪,映射回 legacy 路径。
MIGRATED_REPOSITORIES = {
    "src/app/workline/repositories/bin_cell_reservation_repository.py": (
        "src/app/runtime/orchestration/repositories/bin_cell_reservation_repository.py"
    ),
    "src/app/workline/repositories/diagnostic_repository.py": (
        "src/app/runtime/orchestration/repositories/diagnostic_repository.py"
    ),
    "src/app/workline/repositories/dispatch_attempt_repository.py": (
        "src/app/runtime/orchestration/repositories/dispatch_attempt_repository.py"
    ),
    "src/app/workline/repositories/inbox_repository.py": (
        "src/app/runtime/orchestration/repositories/inbox_repository.py"
    ),
    "src/app/workline/repositories/material_unit_repository.py": (
        "src/app/runtime/orchestration/repositories/material_unit_repository.py"
    ),
    "src/app/workline/repositories/object_transition_event_repository.py": (
        "src/app/runtime/orchestration/repositories/object_transition_event_repository.py"
    ),
    "src/app/workline/repositories/rack_position_repository.py": (
        "src/app/runtime/orchestration/repositories/rack_position_repository.py"
    ),
    "src/app/workline/repositories/runtime_hold_repository.py": (
        "src/app/runtime/orchestration/repositories/runtime_hold_repository.py"
    ),
    "src/app/workline/repositories/session_repository.py": (
        "src/app/runtime/orchestration/repositories/session_repository.py"
    ),
}
MIGRATED_REPOSITORIES_TO_LEGACY = {impl: legacy for legacy, impl in MIGRATED_REPOSITORIES.items()}

SHIM_INTERNAL_SYMBOLS = {
    ("src/app/workline/services/__init__.py", "__getattr__"),
    ("src/app/workline/services/inbox_batch_processor.py", "_load_target_module"),
}

# Legacy audit 只追踪待迁移或待删除入口。当前仍在运行的迁移清单生产实现
# 暂不进入 cleanup ledger；测试已按新 SPEC 作为旧迁移验收直接退役。
ACTIVE_FOUNDATION_PATHS = frozenset(
    {
        "src/app/workline/models/migration_inventory.py",
        "src/app/workline/models/migration_matrix.py",
        "src/app/workline/services/migration_inventory_service.py",
        "src/app/workline/services/migration_matrix_service.py",
    }
)

# 仍待 CORE_REWRITE 的通用可靠性测试暂由旧 Capability 目录承载；
# 已退役的 extensions 目录不再享有豁免。
ACTIVE_PLATFORM_PREFIXES = ("tests/workline_runtime/system_capabilities/",)
ACTIVE_PLATFORM_PATHS = frozenset(
    {
        "tests/workline_runtime/test_workline_session_repository_versioning.py",
    }
)
ACTIVE_PLATFORM_SYMBOLS = frozenset(
    {
        ("src/app/workline/services/device_command_gateway.py", "StaleRuntimeDeviceCommandAdmission"),
        ("src/app/workline/services/device_command_gateway.py", "prepare_runtime_device_command_effect"),
        (
            "tests/workline_runtime/test_runtime_intent_effect_applier.py",
            "test_system_capability_intent_uses_one_generic_effect_service_branch",
        ),
        (
            "tests/workline_runtime/test_runtime_intent_effect_applier.py",
            "test_stale_material_effect_short_circuits_following_device_effects",
        ),
        (
            "tests/workline_runtime/test_runtime_intent_effect_applier.py",
            "test_resource_reservation_uses_runtime_material_flow_default_singleton",
        ),
    }
)

# 当前 WorkLine START 事务边界所需的最小 Port，不属于待迁移的旧 runtime/orchestration 载体。
CURRENT_CONTRACT_SYMBOLS = frozenset(
    {
        ("src/app/workline/services/workline_start_service.py", "OutboxRepositoryPort"),
    }
)
ACTIVE_PLATFORM_FORBIDDEN_IMPORTS = frozenset()


def _active_import_base(relative_path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    module_parts = list(relative_path.with_suffix("").parts)
    if module_parts:
        module_parts.pop()
    ascend = node.level - 1
    prefix = module_parts[: len(module_parts) - ascend] if ascend <= len(module_parts) else []
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


def find_active_platform_legacy_imports(
    *,
    repo_root: Path = REPO_ROOT,
    prefixes: tuple[str, ...] = ACTIVE_PLATFORM_PREFIXES,
) -> list[str]:
    """ACTIVE_PLATFORM_PREFIXES 只排除目标态入口，不得隐藏旧路由 import。"""

    violations: list[str] = []
    for prefix in prefixes:
        root = repo_root / prefix
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
            except SyntaxError:  # noqa: S112 - 语法错误由 architecture scanner 按所属 rule 报告
                continue
            relative = path.relative_to(repo_root)
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    base = _active_import_base(relative, node)
                    modules.append(base)
                    modules.extend(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
                if any(
                    module == forbidden or module.startswith(f"{forbidden}.")
                    for module in modules
                    for forbidden in ACTIVE_PLATFORM_FORBIDDEN_IMPORTS
                ):
                    violations.append(f"{relative.as_posix()}:{node.lineno}")
    return violations


GUARDRAIL_SEED_SYMBOLS = {
    "src/workline_runtime/services.py": "build_workline_runtime_services",
}

# 业务语义关键词 → business_semantics + phase4 标记
# 顺序敏感：先匹配更具体的类别，再匹配通用类别
BUSINESS_SEMANTICS_RULES = [
    # 旧 plugin 框架（优先于 runtime，避免路径含 runtime 误判）
    (
        r"plugin_base|plugin_context|plugin_sdk|plugin_next|null_plugin|"
        r"plugincontext|worklineplugin|pluginnotfound",
        "旧 plugin 框架，目标删除",
    ),
    # phase4 业务流程（粗分机/满箱交换/分拣机/SMT/NG）
    (r"rough_sorter|粗分机", "[phase4] 粗分机业务流程"),
    (r"full_box|满箱交换|full_box_exchange", "[phase4] 满箱交换业务流程"),
    (r"sorter_inbound|分拣机入库|smt_sorting", "[phase4] 分拣机入库业务流程"),
    (r"smt_inbound|smt_inbound_handoff", "[phase4] SMT 入库业务流程"),
    (r"ng_return|ng_reason|ngreturnitem", "[phase4] NG 退货/处理业务流程"),
    (r"single_layer_rack|单层机架", "[phase4] 单层机架编排业务流程"),
    (r"station_lease|stationlease", "[phase4] Station Lease 业务流程"),
    (r"bin_cell_reservation|bincellreservation", "[phase4] Bin Cell 预约业务流程"),
    (r"material_identity|materialidentity", "[phase4] Material Identity 业务流程"),
    (r"six_in_one|sixinone|六合一", "[phase4] SixInOne 合同"),
    (r"start_admission|admission", "[phase4] Start Admission 业务流程"),
    (r"smt_usage|smtusage", "[phase4] SMT 使用策略"),
    # WorkLine 配置域（主配置类 + manifest/topology/safety/plane/rack_position）
    (
        r"^worklinebase$|^workline$|^worklinecreate$|^worklineupdate$|^worklineresponse$|"
        r"manifest|topology|safety_zone|safety_incident|resource_boundary|"
        r"plane_scene|plane_snapshot|rack_position|rackposition|pipelinerqueue|"
        r"event_binding|command_binding|state_machine|device_requirement",
        "WorkLine 配置域能力",
    ),
    # 执行状态（目标迁 runtime 域）
    (
        r"session|inbox|timeline|runtimehold|intent|effect|outbox|orchestrat|dispatch|"
        r"runtime\.py|runtime_api|trace_",
        "执行状态，目标迁 runtime/orchestration",
    ),
    # 跨域 session（字段级，P0-004 矩阵覆盖）
    (r"workline_session_id|material_session_id|current_session_id|sorting_session_id", "跨域 session 引用"),
    # 技术残留
    (r"debug|sandbox|fake|mock|cleanup|diagnostic|integration_debug", "技术残留/调试"),
]

# 策略判定规则（按 business_semantics 类别）
STRATEGY_RULES = [
    # 旧 plugin 框架 → delete
    (r"旧 plugin 框架", "delete", "phase5-tech", "MEDIUM"),
    # 技术残留 → delete
    (r"技术残留|调试", "delete", "phase5-tech", "LOW"),
    # 执行状态 → rebuild (迁 runtime)
    (r"执行状态", "rebuild", "phase2", "HIGH"),
    # 跨域 session → rebuild (P0-004 矩阵)
    (r"跨域 session", "rebuild", "phase1", "MEDIUM"),
    # phase4 业务流程 → rebuild (Phase 4 重建)
    (r"\[phase4\]", "rebuild", "phase4", "HIGH"),
    # WorkLine 配置域 → move (保留配置能力)
    (r"WorkLine 配置", "move", "phase2", "MEDIUM"),
    # 测试 → keep-contract (characterization 候选)
    (r"test", "keep-contract", "phase5-tech", "LOW"),
    # doc_template → delete
    (r"doc_template|模板", "delete", "phase5-tech", "LOW"),
]


@dataclass
class Entry:
    entry_id: str
    entry_type: str
    relative_path: str
    symbol_or_route: str
    current_owner: str
    business_semantics: str
    phase4_carrier: bool = False
    classification_status: str = "final"
    strategy: str = ""
    drop_phase: str = ""
    risk: str = "LOW"
    target_path: str = ""
    target_capability: str = ""
    blocking_tests: str = ""
    notes: str = field(default="", repr=False)


Phase10PrelockSpec = tuple[str, str, str, str, str, str, str, str, str, str]
Phase10ImportSpec = tuple[str, str, tuple[tuple[str, str], ...]]


@dataclass(frozen=True)
class Phase10SchemaSnapshot:
    """Fresh interpreter 中采集的 migration model provenance。"""

    tables: frozenset[str]
    foreign_key_targets: tuple[tuple[str, tuple[str, ...]], ...]
    model_tables: tuple[tuple[str, str], ...]
    fingerprint: str


# Phase 10 Execution Lock 前的唯一 disposition manifest。这里只登记冻结边界上的
# legacy/retain/schema identity；不按 package 全量枚举目标态符号。
PHASE10_PRELOCK_SPECS: tuple[Phase10PrelockSpec, ...] = (
    # category, path, symbol, entry_type, owner, disposition, target_path,
    # target_capability, blocking_tests, risk
    (
        "runtime",
        "src/app/runtime/orchestration/consumers/callback_runtime_inbox_writer.py",
        "CallbackRuntimeInboxWriter",
        "service",
        "runtime",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/repositories/bin_cell_reservation_repository.py",
        "WorklineBinCellReservationRepository",
        "repository",
        "runtime",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/repositories/runtime_inbox_repository.py",
        "RuntimeInboxRepository",
        "repository",
        "runtime",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/repositories/runtime_intent_log_repository.py",
        "RuntimeIntentLogRepository",
        "repository",
        "runtime",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/runtime_intent.py",
        "<file>",
        "runtime_module",
        "runtime",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/runtime_intent_effects.py",
        "<file>",
        "runtime_module",
        "runtime",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/services/hold/runtime_hold_creation_service.py",
        "RuntimeHoldCreationService",
        "service",
        "runtime",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/services/hold/runtime_hold_query_service.py",
        "RuntimeHoldQueryService",
        "service",
        "runtime",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/services/hold/runtime_hold_release_service.py",
        "RuntimeHoldReleaseService",
        "service",
        "runtime",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/services/inbox/object_transition_event_service.py",
        "ObjectTransitionEventService",
        "service",
        "runtime",
        "retain",
        "src/app/runtime/orchestration/services/inbox/object_transition_event_service.py",
        "ObjectTransitionEventService",
        "tests/architecture/test_execution_correlation_boundary_guardrail.py",
        "MEDIUM",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/services/intent/system_capability_effect_service.py",
        "SystemCapabilityEffectService",
        "service",
        "runtime",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/services/intent/system_capability_intent_service.py",
        "SystemCapabilityIntentService",
        "service",
        "runtime",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/services/query/runtime_query_service.py",
        "RuntimeQueryService",
        "service",
        "runtime",
        "switch",
        "src/app/runtime/orchestration/services/query/runtime_query_service.py",
        "RuntimeQueryService",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/services/reconciliation/runtime_reconciliation_service_impl.py",
        "WorklineRuntimeReconciliationService",
        "service",
        "runtime",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_service.py",
        "RuntimeInboxService",
        "service",
        "runtime",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/services/trace/trace_query_service.py",
        "TraceQueryService",
        "service",
        "runtime",
        "switch",
        "src/app/runtime/orchestration/services/trace/trace_query_service.py",
        "TraceQueryService",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/repositories/release_operational_readiness_repository.py",
        "ReleaseOperationalReadinessRepository",
        "repository",
        "runtime",
        "retain",
        "src/app/runtime/orchestration/repositories/release_operational_readiness_repository.py",
        "ReleaseOperationalReadinessRepository",
        "tests/architecture/test_release_operational_readiness_repository_boundary.py;"
        "tests/integration/test_release_operational_readiness_postgresql.py",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/models/runtime_location_event.py",
        "RuntimeLocationEvent",
        "model",
        "runtime",
        "retain",
        "src/app/runtime/orchestration/models/runtime_location_event.py",
        "RuntimeLocationEvent",
        "tests/runtime/orchestration/",
        "MEDIUM",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/models/session.py",
        "WorklineSession",
        "model",
        "runtime",
        "retain",
        "src/app/runtime/orchestration/models/session.py",
        "WorklineSession",
        "tests/workline_runtime/",
        "HIGH",
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/models/timeline.py",
        "WorklineTimeline",
        "model",
        "runtime",
        "retain",
        "src/app/runtime/orchestration/models/timeline.py",
        "WorklineTimeline",
        "tests/workline_runtime/",
        "HIGH",
    ),
    *(
        (
            "runtime",
            path,
            symbol,
            entry_type,
            "runtime",
            "delete",
            "",
            "NONE",
            "tests/architecture/test_legacy_absence_guardrail.py",
            "HIGH",
        )
        for path, symbol, entry_type in (
            ("src/app/runtime/orchestration/effect_bridges.py", "<file>", "runtime_module"),
            ("src/app/runtime/orchestration/effect_result.py", "<file>", "runtime_module"),
            ("src/app/runtime/orchestration/effect_state_contract.py", "<file>", "runtime_module"),
            ("src/app/runtime/orchestration/integration_lab.py", "<file>", "runtime_module"),
            ("src/app/runtime/orchestration/repository_wiring.py", "<file>", "runtime_module"),
            ("src/app/runtime/orchestration/scenario_replay.py", "<file>", "runtime_module"),
            (
                "src/app/runtime/orchestration/repositories/runtime_hold_repository.py",
                "RuntimeHoldRepository",
                "repository",
            ),
            (
                "src/app/runtime/orchestration/services/effect_reconciliation_resolution_service.py",
                "EffectReconciliationResolutionService",
                "service",
            ),
            ("src/app/runtime/orchestration/services/effect_reducer_service.py", "EffectReducer", "service"),
            (
                "src/app/runtime/orchestration/services/inbox/wms_runtime_inbox_handler.py",
                "WmsRuntimeInboxHandler",
                "service",
            ),
            (
                "src/app/runtime/orchestration/services/inbox/wms_typed_effect_callback_router.py",
                "WmsTypedEffectCallbackRouter",
                "service",
            ),
            (
                "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_context_loader.py",
                "<file>",
                "service",
            ),
            (
                "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_orchestrator_bridge.py",
                "RuntimeInboxProcessorBridge",
                "service",
            ),
            (
                "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_validation_service.py",
                "RuntimeInboxValidationService",
                "service",
            ),
            (
                "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_writeback_service.py",
                "RuntimeInboxWriteBackService",
                "service",
            ),
            (
                "src/app/runtime/orchestration/services/runtime_snapshot_assembler.py",
                "RuntimeSnapshotAssembler",
                "service",
            ),
            ("src/app/runtime/orchestration/system_capability_effect_claim.py", "<file>", "runtime_module"),
        )
    ),
    *(
        (
            category,
            path,
            symbol,
            (
                "repository"
                if "/repositories/" in path
                else "service"
                if "/services/" in path or path.endswith("/unit_of_work.py")
                else "contract"
                if "/contracts/" in path
                else "runtime_module"
            ),
            path.split("/")[2] if path.startswith("src/app/") else "core",
            disposition,
            path if disposition == "switch" else "",
            target_capability if disposition == "switch" else "NONE",
            "tests/architecture/test_cleanup_matrix_guardrail.py",
            "HIGH",
        )
        for path, symbol, category, disposition, target_capability in (
            ("src/app/callback/contracts/external_callbacks.py", "<file>", "wms", "delete", ""),
            (
                "src/app/callback/services/callback_ingress_service.py",
                "CallbackProviderProfileAdmissionService",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/callback/services/callback_orchestration_service.py",
                "CallbackOrchestrationService",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/device/services/device_context_service.py",
                "<file>",
                "runtime",
                "switch",
                "DeviceContextService",
            ),
            (
                "src/app/resource/services/projection_service.py",
                "ResourceProjectionService",
                "runtime",
                "switch",
                "ResourceProjectionService",
            ),
            (
                "src/app/resource/services/relation_service.py",
                "ResourceRelationService",
                "runtime",
                "switch",
                "ResourceRelationService",
            ),
            (
                "src/app/runtime/orchestration/bin_route_instance.py",
                "<file>",
                "runtime",
                "switch",
                "BinRouteInstance",
            ),
            (
                "src/app/runtime/orchestration/conveyor_queue_membership.py",
                "<file>",
                "runtime",
                "switch",
                "ConveyorQueueMembership",
            ),
            (
                "src/app/runtime/orchestration/execution_correlation.py",
                "<file>",
                "runtime",
                "switch",
                "ExecutionCorrelation",
            ),
            (
                "src/app/runtime/orchestration/execution_session.py",
                "<file>",
                "runtime",
                "switch",
                "ExecutionSession",
            ),
            (
                "src/app/runtime/orchestration/execution_work_item.py",
                "<file>",
                "runtime",
                "switch",
                "ExecutionWorkItem",
            ),
            (
                "src/app/runtime/orchestration/idempotency_key.py",
                "<file>",
                "runtime",
                "switch",
                "IdempotencyKey",
            ),
            (
                "src/app/runtime/orchestration/material_flow_owner.py",
                "<file>",
                "runtime",
                "switch",
                "MaterialFlowOwner",
            ),
            (
                "src/app/runtime/orchestration/models/bin_cell_reservation.py",
                "<file>",
                "runtime",
                "switch",
                "WorklineBinCellReservation",
            ),
            (
                "src/app/runtime/orchestration/models/diagnostic.py",
                "<file>",
                "runtime",
                "switch",
                "WorklineDiagnostic",
            ),
            (
                "src/app/runtime/orchestration/models/dispatch_attempt.py",
                "<file>",
                "runtime",
                "switch",
                "WorklineDispatchAttempt",
            ),
            (
                "src/app/runtime/orchestration/models/runtime_hold.py",
                "<file>",
                "runtime",
                "switch",
                "RuntimeHold",
            ),
            (
                "src/app/runtime/orchestration/reconciliation_case.py",
                "<file>",
                "runtime",
                "switch",
                "ReconciliationCase",
            ),
            (
                "src/app/runtime/orchestration/runtime_hold.py",
                "<file>",
                "runtime",
                "switch",
                "RuntimeHold",
            ),
            (
                "src/app/runtime/orchestration/runtime_inbox.py",
                "<file>",
                "runtime",
                "switch",
                "RuntimeInbox",
            ),
            (
                "src/app/runtime/orchestration/runtime_intent_log.py",
                "<file>",
                "runtime",
                "switch",
                "RuntimeIntentLog",
            ),
            (
                "src/app/runtime/orchestration/runtime_timeline.py",
                "<file>",
                "runtime",
                "switch",
                "RuntimeTimeline",
            ),
            (
                "src/app/runtime/orchestration/wms_rack_demand.py",
                "<file>",
                "runtime",
                "switch",
                "WmsRackDemand",
            ),
            (
                "src/app/runtime/orchestration/workline_runtime_status_projection.py",
                "<file>",
                "runtime",
                "switch",
                "WorklineRuntimeStatusProjection",
            ),
            (
                "src/app/runtime/capabilities/material_flow/bin_cell_reservation_service.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/capabilities/material_flow/station_lease_service.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            ("src/app/runtime/orchestration/material_target_resolver.py", "<file>", "runtime", "delete", ""),
            ("src/app/runtime/orchestration/observability.py", "<file>", "wms", "delete", ""),
            ("src/app/runtime/orchestration/operation_observability.py", "<file>", "wms", "delete", ""),
            (
                "src/app/runtime/orchestration/repositories/conveyor_queue_membership_repository.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/repositories/diagnostic_repository.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/repositories/dispatch_attempt_repository.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/repositories/effect_reducer_repository.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/repositories/idempotency_key_repository.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/repositories/northbound_operations_repository.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/repositories/session_execution_anchor_repository.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/repositories/wms_fulfillment_domain_repository.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/repositories/wms_putaway_sync_barrier_repository.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/repositories/workline_runtime_status_projection_repository.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/repositories/wms_effect_status_repository.py",
                "WmsEffectStatusRepository",
                "wms",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/services/hold/wms_putaway_sync_barrier_service.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/services/inbox/__init__.py",
                "<file>",
                "runtime",
                "switch",
                "ObjectTransitionEventService",
            ),
            (
                "src/app/runtime/orchestration/services/inbox/dispatch_attempt_service.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/services/inbox/external_http_lease_loss_service.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/services/inbox/non_http_lease_exhaustion_service.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/services/inbox/outbox_dispatch_service.py",
                "OutboxDispatchService",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/services/intent/operation_service.py",
                "WorklineOperationService",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/services/conveyor_queue_membership_writer_service.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/services/idempotency_guard.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/services/rack_demand_service.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/services/workline_runtime_status_projection_service.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/services/material_unit_mutation_service.py",
                "<file>",
                "runtime",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/services/query/material_location_query_service.py",
                "<file>",
                "runtime",
                "switch",
                "MaterialLocationQueryService",
            ),
            (
                "src/app/runtime/orchestration/services/session/session_resolver.py",
                "<file>",
                "runtime",
                "switch",
                "SessionResolver",
            ),
            (
                "src/app/runtime/orchestration/services/wms_effect_status_service.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            (
                "src/app/runtime/orchestration/services/wms_fulfillment_domain_projector.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            ("src/app/runtime/orchestration/wms_effect_observability.py", "<file>", "wms", "delete", ""),
            ("src/app/runtime/system_capabilities/wms/conformance_manifest.py", "<file>", "wms", "delete", ""),
            ("src/app/runtime/system_capabilities/wms/conformance_matrix.py", "<file>", "wms", "delete", ""),
            ("src/app/runtime/system_capabilities/wms/contracts.py", "<file>", "wms", "delete", ""),
            (
                "src/app/runtime/system_capabilities/wms/document/get_grn/definition.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            (
                "src/app/runtime/system_capabilities/wms/document/get_outbound_order/definition.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            (
                "src/app/runtime/system_capabilities/wms/document/get_pick_order/definition.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            (
                "src/app/runtime/system_capabilities/wms/document/get_task_snapshot/definition.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            (
                "src/app/runtime/system_capabilities/wms/document/get_wave/definition.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            (
                "src/app/runtime/system_capabilities/wms/document/list_grn_packages/definition.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            ("src/app/runtime/system_capabilities/wms/effect_runtime.py", "<file>", "wms", "delete", ""),
            (
                "src/app/runtime/system_capabilities/wms/generated_operation_index.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            (
                "src/app/runtime/system_capabilities/wms/master_data/get_bin/definition.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            (
                "src/app/runtime/system_capabilities/wms/master_data/get_material/definition.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            (
                "src/app/runtime/system_capabilities/wms/master_data/get_rack/definition.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            (
                "src/app/runtime/system_capabilities/wms/master_data/list_locations/definition.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            (
                "src/app/runtime/system_capabilities/wms/master_data/list_materials/definition.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            (
                "src/app/runtime/system_capabilities/wms/master_data/list_racks/definition.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            (
                "src/app/runtime/system_capabilities/wms/master_data/list_zones/definition.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            ("src/app/runtime/system_capabilities/wms/provider_catalog.py", "<file>", "wms", "delete", ""),
            ("src/app/runtime/system_capabilities/wms/query_definition.py", "<file>", "wms", "delete", ""),
            ("src/app/runtime/system_capabilities/wms/query_handler.py", "<file>", "wms", "delete", ""),
            (
                "src/app/runtime/system_capabilities/wms/reconciliation/check_bin_drift/definition.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            (
                "src/app/runtime/system_capabilities/wms/reconciliation/check_full_drift/definition.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            (
                "src/app/runtime/system_capabilities/wms/reconciliation/check_rack_drift/definition.py",
                "<file>",
                "wms",
                "delete",
                "",
            ),
            ("src/app/runtime/system_capabilities/wms/scheduling_identity.py", "<file>", "wms", "delete", ""),
            ("src/app/transport/composition.py", "<file>", "wms", "switch", "build_transport_runtime"),
            ("src/app/sys/models/operation_completion.py", "<file>", "sys", "delete", ""),
            ("src/app/sys/models/outbox.py", "<file>", "sys", "switch", "SystemOutbox"),
            (
                "src/app/wms_integration/models/circuit_breaker.py",
                "<file>",
                "wms",
                "switch",
                "WmsCircuitBreakerState",
            ),
            (
                "src/app/wms_integration/models/evidence.py",
                "<file>",
                "wms",
                "switch",
                "WmsCallEvidence",
            ),
            ("src/app/workline/services/diagnostic_service.py", "<file>", "runtime", "delete", ""),
            ("src/app/workline/runtime_services.py", "<file>", "wms", "delete", ""),
            (
                "src/app/workline/services/safety_service.py",
                "<file>",
                "runtime",
                "switch",
                "WorkLineSafetyService",
            ),
            (
                "src/app/workline/services/workline_service.py",
                "<file>",
                "runtime",
                "switch",
                "WorkLineService",
            ),
            (
                "src/app/workline/services/workline_start_service.py",
                "<file>",
                "runtime",
                "switch",
                "WorkLineStartService",
            ),
            ("src/app/workline/unit_of_work.py", "<file>", "runtime", "switch", "WorklineUnitOfWork"),
        )
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/services/query/workline_active_objects_service.py",
        "WorklineActiveObjectsService",
        "service",
        "runtime",
        "switch",
        "src/app/runtime/orchestration/services/query/workline_active_objects_service.py",
        "WorklineActiveObjectsService",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    *(
        (
            "runtime",
            path,
            symbol,
            entry_type,
            "runtime",
            "retain",
            path,
            symbol,
            blocking_tests,
            "HIGH",
        )
        for path, symbol, entry_type, blocking_tests in (
            (
                "src/app/runtime/orchestration/models/object_transition_event.py",
                "ObjectTransitionEvent",
                "model",
                "tests/architecture/test_execution_correlation_boundary_guardrail.py",
            ),
            (
                "src/app/runtime/orchestration/repositories/runtime_location_event_repository.py",
                "RuntimeLocationEventRepository",
                "repository",
                "tests/runtime/orchestration/",
            ),
            (
                "src/app/runtime/orchestration/repositories/session_repository.py",
                "WorklineSessionRepository",
                "repository",
                "tests/workline_runtime/",
            ),
            (
                "src/app/runtime/orchestration/repositories/timeline_sequence_repository.py",
                "TimelineSequenceRepository",
                "repository",
                "tests/contracts/workline/test_runtime_timeline_query_contract.py",
            ),
            (
                "src/app/runtime/orchestration/services/runtime_location_event_service.py",
                "RuntimeLocationEventService",
                "service",
                "tests/runtime/orchestration/",
            ),
        )
    ),
    (
        "runtime",
        "src/app/runtime/orchestration/services/trace/timeline_sequence_service.py",
        "<file>",
        "service",
        "runtime",
        "retain",
        "src/app/runtime/orchestration/services/trace/timeline_sequence_service.py",
        "allocate_timeline_seq_no",
        "tests/contracts/workline/test_runtime_timeline_query_contract.py",
        "HIGH",
    ),
    *(
        (
            "runtime",
            path,
            "<file>",
            "package_export",
            owner,
            disposition,
            path if disposition == "switch" else "",
            target_capability if disposition == "switch" else "NONE",
            "tests/architecture/test_legacy_absence_guardrail.py",
            "HIGH",
        )
        for path, owner, disposition, target_capability in (
            (
                "src/app/runtime/orchestration/__init__.py",
                "runtime",
                "switch",
                "__all__",
            ),
            ("src/app/runtime/orchestration/consumers/__init__.py", "runtime", "delete", ""),
            (
                "src/app/runtime/orchestration/models/__init__.py",
                "runtime",
                "switch",
                "WorklineSession",
            ),
            ("src/app/runtime/orchestration/models/operation.py", "runtime", "delete", ""),
            (
                "src/app/runtime/orchestration/repositories/__init__.py",
                "runtime",
                "switch",
                "RuntimeLocationEventRepository",
            ),
            (
                "src/app/runtime/orchestration/services/__init__.py",
                "runtime",
                "switch",
                "__all__",
            ),
            ("src/app/runtime/orchestration/services/hold/__init__.py", "runtime", "delete", ""),
            ("src/app/runtime/orchestration/services/intent/__init__.py", "runtime", "delete", ""),
            (
                "src/app/runtime/orchestration/services/query/__init__.py",
                "runtime",
                "switch",
                "__all__",
            ),
            ("src/app/runtime/orchestration/services/runtime_inbox/__init__.py", "runtime", "delete", ""),
            (
                "src/app/runtime/orchestration/services/trace/__init__.py",
                "runtime",
                "switch",
                "__all__",
            ),
            (
                "src/app/workline/v1/active_objects.py",
                "workline",
                "switch",
                "get_workline_active_objects",
            ),
            ("src/app/workline/v1/operation.py", "workline", "switch", "start_workline"),
            ("src/app/workline/v1/runtime_operations.py", "workline", "delete", ""),
        )
    ),
    (
        "sys",
        "src/app/runtime/orchestration/services/system_outbox_cancellation_service.py",
        "SystemOutboxCancellationService",
        "service",
        "runtime",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "sys",
        "src/app/sys/canonical_dispatch.py",
        "<file>",
        "runtime_module",
        "sys",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_outbound_http_boundary_guardrail.py",
        "HIGH",
    ),
    (
        "sys",
        "src/app/sys/external_http_transport.py",
        "<file>",
        "runtime_module",
        "sys",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_outbound_http_boundary_guardrail.py",
        "HIGH",
    ),
    *(
        (
            "sys",
            "src/app/sys/canonical_dispatch.py",
            symbol,
            "contract_symbol",
            "sys",
            "delete",
            "",
            "NONE",
            "tests/architecture/test_outbound_http_boundary_guardrail.py",
            "HIGH",
        )
        for symbol in (
            "CanonicalPayload",
            "EndpointDefinition",
            "ExternalHttpDispatchRequest",
            "CanonicalPayload.sign_hmac_sha256",
            "_persisted_bytes",
            "canonical_json_bytes",
            "payload_sha256",
        )
    ),
    *(
        (
            "sys",
            "src/app/sys/external_http_transport.py",
            symbol,
            "contract_symbol",
            "sys",
            "delete",
            "",
            "NONE",
            "tests/architecture/test_outbound_http_boundary_guardrail.py",
            "HIGH",
        )
        for symbol in (
            "ExternalHttpProtocolResult",
            "ExternalHttpSender",
            "ExternalHttpTransportOutcome",
            "ExternalHttpTransportPhase",
            "ExternalHttpTransportResult",
        )
    ),
    *(
        (
            "sys",
            "src/core/outbound_http/contracts.py",
            symbol,
            "contract_symbol",
            "core",
            "retain",
            "src/core/outbound_http/contracts.py",
            symbol,
            "tests/core/outbound_http/test_contracts.py",
            "HIGH",
        )
        for symbol in (
            "OutboundHttpDeliveryState",
            "OutboundHttpFailureKind",
            "OutboundHttpMethod",
            "OutboundHttpRequest",
            "OutboundHttpResponseLimits",
            "OutboundHttpResult",
            "OutboundHttpTransport",
        )
    ),
    (
        "sys",
        "src/app/sys/repositories/outbox_repository.py",
        "SystemOutboxRepository",
        "repository",
        "sys",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "sys",
        "src/app/sys/services/outbox_delivery.py",
        "<file>",
        "service",
        "sys",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    (
        "sys",
        "src/app/sys/services/outbox_engine.py",
        "SystemOutboxEngine",
        "service",
        "sys",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    *(
        (
            "sys",
            path,
            "<file>",
            "runtime_module" if "/services/" not in path else "service",
            "sys",
            "delete",
            "",
            "NONE",
            "tests/architecture/test_legacy_absence_guardrail.py",
            "HIGH",
        )
        for path in (
            "src/app/sys/dispatch_concurrency.py",
            "src/app/sys/external_http_binding.py",
            "src/app/sys/external_http_credentials.py",
            "src/app/sys/external_http_dispatch_faults.py",
            "src/app/sys/external_http_evidence.py",
            "src/app/sys/services/endpoint_registry.py",
        )
    ),
    *(
        (
            "sys",
            path,
            "<file>",
            "package_export",
            "sys",
            "switch",
            path,
            target_capability,
            "tests/architecture/test_legacy_absence_guardrail.py",
            "HIGH",
        )
        for path, target_capability in (
            ("src/app/sys/models/__init__.py", "AuditLog"),
            ("src/app/sys/repositories/__init__.py", "audit_log_repository"),
            ("src/app/sys/services/__init__.py", "AuditLogService"),
        )
    ),
    (
        "wms",
        "src/app/wms_adapter/client.py",
        "WmsClient",
        "service",
        "wms_adapter",
        "retain",
        "src/app/wms_adapter/client.py",
        "WmsClient",
        "tests/contracts/wms_adapter/test_client.py",
        "HIGH",
    ),
    (
        "wms",
        "src/app/wms_adapter/execution_confirmation_adapter.py",
        "<file>",
        "service",
        "wms_adapter",
        "retain",
        "src/app/wms_adapter/execution_confirmation_adapter.py",
        "WmsExecutionConfirmationAdapter",
        "tests/runtime/execution/test_wms_confirmation_service.py",
        "HIGH",
    ),
    (
        "wms",
        "src/app/wms_adapter/inbound_adapter.py",
        "WmsInboundAdapter",
        "service",
        "wms_adapter",
        "retain",
        "src/app/wms_adapter/inbound_adapter.py",
        "WmsInboundAdapter",
        "tests/contracts/wms_adapter/",
        "HIGH",
    ),
    (
        "wms",
        "src/app/wms_adapter/inbound_auth.py",
        "WmsInboundAuthPolicy",
        "service",
        "wms_adapter",
        "switch",
        "src/app/wms_adapter/inbound_auth.py",
        "WmsInboundAuthPolicy",
        "tests/contracts/wms_adapter/",
        "HIGH",
    ),
    (
        "wms",
        "src/app/wms_adapter/transport_adapter.py",
        "<file>",
        "service",
        "wms_adapter",
        "retain",
        "src/app/wms_adapter/transport_adapter.py",
        "WmsTransportAdapter",
        "tests/contracts/wms_adapter/",
        "HIGH",
    ),
    *(
        (
            "wms",
            path,
            "<file>",
            "runtime_module",
            "wms_integration",
            "delete",
            "",
            "NONE",
            "tests/architecture/test_legacy_absence_guardrail.py",
            "HIGH",
        )
        for path in (
            "src/app/wms_integration/adapters/effect_status_query_adapter.py",
            "src/app/wms_integration/deployment_attestation.py",
            "src/app/wms_integration/effect_lane_runtime.py",
            "src/app/wms_integration/effect_preparation_runtime.py",
            "src/app/wms_integration/effect_runtime.py",
            "src/app/wms_integration/endpoint_compiler.py",
            "src/app/wms_integration/evidence/catalog.py",
            "src/app/wms_integration/evidence/envelope.py",
            "src/app/wms_integration/models/ports.py",
            "src/app/wms_integration/operation_registry.py",
            "src/app/wms_integration/ports/document_operations.py",
            "src/app/wms_integration/ports/effect_preparation.py",
            "src/app/wms_integration/ports/effect_status.py",
            "src/app/wms_integration/ports/event.py",
            "src/app/wms_integration/ports/master_data_operations.py",
            "src/app/wms_integration/ports/query_execution.py",
            "src/app/wms_integration/ports/query_outcome.py",
            "src/app/wms_integration/ports/reconciliation_operations.py",
            "src/app/wms_integration/provider_manifest.py",
            "src/app/wms_integration/provider_profile.py",
            "src/app/wms_integration/provider_readiness.py",
            "src/app/wms_integration/provider_simulator_registry.py",
            "src/app/wms_integration/provider_startup.py",
            "src/app/wms_integration/query_evidence.py",
            "src/app/wms_integration/query_executor.py",
            "src/app/wms_integration/query_projection.py",
            "src/app/wms_integration/query_response.py",
            "src/app/wms_integration/query_runtime.py",
            "src/app/wms_integration/repositories/circuit_breaker_repository.py",
            "src/app/wms_integration/repositories/evidence_repository.py",
            "src/app/wms_integration/runtime_factory.py",
            "src/app/wms_integration/services/callback_normalizer.py",
            "src/app/wms_integration/services/circuit_breaker_service.py",
            "src/app/wms_integration/services/evidence_service.py",
            "src/app/wms_integration/services/exceptions.py",
            "src/app/wms_integration/services/fulfillment_lifecycle.py",
            "src/app/wms_integration/services/http_transport.py",
            "src/app/wms_integration/services/redaction.py",
            "src/app/wms_integration/services/wms_event_normalizer.py",
            "src/app/wms_integration/state_machine.py",
            "src/app/wms_integration/transport_url.py",
        )
    ),
    (
        "wms",
        "src/app/wms_integration/__init__.py",
        "<file>",
        "package_export",
        "wms_integration",
        "retain",
        "src/app/wms_integration/__init__.py",
        "__all__",
        "tests/runtime/execution/test_wms_confirmation_service.py",
        "HIGH",
    ),
    *(
        (
            "wms",
            path,
            "<file>",
            "package_export",
            "wms_integration",
            disposition,
            path if disposition == "switch" else "",
            target_capability if disposition == "switch" else "NONE",
            (
                "tests/runtime/execution/test_wms_confirmation_service.py"
                if disposition == "switch"
                else "tests/architecture/test_legacy_absence_guardrail.py"
            ),
            "HIGH",
        )
        for path, disposition, target_capability in (
            ("src/app/wms_integration/adapters/__init__.py", "delete", ""),
            ("src/app/wms_integration/evidence/__init__.py", "delete", ""),
            (
                "src/app/wms_integration/models/__init__.py",
                "switch",
                "__all__",
            ),
            (
                "src/app/wms_integration/ports/__init__.py",
                "switch",
                "__all__",
            ),
            ("src/app/wms_integration/repositories/__init__.py", "delete", ""),
            ("src/app/wms_integration/services/__init__.py", "delete", ""),
        )
    ),
    *(
        (
            "wms",
            path,
            "<file>",
            "contract",
            "wms_integration",
            "switch",
            path,
            target_capability,
            "tests/runtime/execution/test_wms_confirmation_service.py",
            "HIGH",
        )
        for path, target_capability in (
            (
                "src/app/wms_integration/ports/fulfillment_operations.py",
                "NotifyPkgBindingRequest",
            ),
            (
                "src/app/wms_integration/ports/inventory_operations.py",
                "ConfirmInboundRequest",
            ),
            (
                "src/app/wms_integration/ports/operation_common.py",
                "validate_json_payload",
            ),
        )
    ),
    (
        "wms",
        "src/app/wms_integration/operation_contract.py",
        "<file>",
        "contract",
        "wms_integration",
        "delete",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
        "HIGH",
    ),
    *(
        (
            "wms",
            "src/app/wms_integration/operation_contract.py",
            symbol,
            "contract_symbol",
            "wms_integration",
            "delete",
            "",
            "NONE",
            "tests/architecture/test_legacy_absence_guardrail.py",
            "HIGH",
        )
        for symbol in (
            "WmsCompletionMode",
            "WmsDomainProjectionKind",
            "WmsExecutionLane",
            "WmsHttpMethod",
            "WmsOperationBudget",
            "WmsOperationDefinition",
            "WmsOperationMode",
            "WmsPaginationConstraint",
            "effect_operation",
            "query_operation",
        )
    ),
    *(
        (
            "wms",
            path,
            symbol,
            "contract_symbol",
            "wms_integration",
            "retain",
            path,
            symbol,
            "tests/runtime/execution/test_wms_confirmation_service.py",
            "HIGH",
        )
        for path, symbol in (
            ("src/app/wms_integration/ports/fulfillment_operations.py", "NotifyPkgBindingRequest"),
            ("src/app/wms_integration/ports/fulfillment_operations.py", "NotifyPkgBindingResult"),
            (
                "src/app/wms_integration/ports/fulfillment_operations.py",
                "validate_notify_pkg_binding_terminal_identity",
            ),
            ("src/app/wms_integration/ports/inventory_operations.py", "ConfirmInboundRequest"),
            ("src/app/wms_integration/ports/inventory_operations.py", "ConfirmInboundResult"),
            (
                "src/app/wms_integration/ports/inventory_operations.py",
                "validate_confirm_inbound_terminal_identity",
            ),
            ("src/app/wms_integration/ports/operation_common.py", "validate_json_payload"),
        )
    ),
    *(
        (
            "celery-boot",
            path,
            symbol,
            "celery_task",
            "celery_app",
            "delete",
            "",
            "NONE",
            "tests/deployment/test_execution_worker_startup.py;tests/deployment/test_wms_confirmation_dispatcher.py",
            "HIGH",
        )
        for path, symbol in (
            (
                "src/celery_app/tasks/runtime_inbox.py",
                "src.celery_app.tasks.runtime_inbox.process_runtime_inbox_batch",
            ),
            (
                "src/celery_app/tasks/runtime_inbox.py",
                "src.celery_app.tasks.runtime_inbox.process_signal",
            ),
            (
                "src/celery_app/tasks/sys.py",
                "src.celery_app.tasks.sys.dispatch_system_outbox_batch",
            ),
            (
                "src/celery_app/tasks/sys.py",
                "src.celery_app.tasks.sys.dispatch_wms_data_outbox_batch",
            ),
            (
                "src/celery_app/tasks/sys.py",
                "src.celery_app.tasks.sys.dispatch_wms_fulfillment_outbox_batch",
            ),
            ("src/celery_app/tasks/sys.py", "src.celery_app.tasks.sys.process_signal"),
            (
                "src/celery_app/tasks/workline.py",
                "src.celery_app.tasks.workline.check_wms_effect_status",
            ),
            (
                "src/celery_app/tasks/workline.py",
                "src.celery_app.tasks.workline.scan_wms_effect_status_batch",
            ),
            (
                "src/celery_app/tasks/workline.py",
                "src.celery_app.tasks.workline.process_signal",
            ),
        )
    ),
    *(
        (
            "celery-boot",
            path,
            "<file>",
            "runtime_module",
            "celery_app",
            disposition,
            path if disposition == "switch" else "",
            target_capability if disposition == "switch" else "NONE",
            "tests/deployment/test_execution_worker_startup.py;tests/deployment/test_wms_confirmation_dispatcher.py",
            "HIGH",
        )
        for path, disposition, target_capability in (
            ("src/celery_app/outbox_dispatch_composition.py", "delete", ""),
            ("src/celery_app/tasks/runtime_inbox.py", "delete", ""),
            ("src/celery_app/tasks/sys.py", "delete", ""),
            ("src/celery_app/tasks/workline.py", "switch", "drain_safety_incidents_batch"),
        )
    ),
    (
        "celery-boot",
        "src/celery_app/tasks/__init__.py",
        "<file>",
        "package_export",
        "celery_app",
        "switch",
        "src/celery_app/tasks/__init__.py",
        "__all__",
        "tests/deployment/test_execution_worker_startup.py;tests/deployment/test_wms_confirmation_dispatcher.py",
        "HIGH",
    ),
    *(
        (
            "celery-boot",
            path,
            symbol,
            "boot_wiring",
            "celery_app" if path.startswith("src/celery_app/") else "api",
            "switch",
            path,
            target_capability,
            "tests/deployment/test_execution_worker_startup.py;tests/deployment/test_wms_confirmation_dispatcher.py",
            "HIGH",
        )
        for path, symbol, target_capability in (
            ("src/celery_app/app.py", "<file>", "celery_app"),
            ("src/celery_app/async_runtime.py", "<file>", "celery_async_runtime"),
            ("src/celery_app/config.py", "<file>", "beat_schedule"),
            ("src/register.py", "register_init", "register_init"),
        )
    ),
    (
        "celery-boot",
        "src/celery_app/config.py",
        "queue:wms-fulfillment",
        "queue",
        "celery_app",
        "retain",
        "src/celery_app/config.py",
        "queue:wms-fulfillment",
        "tests/deployment/test_wms_confirmation_dispatcher.py",
        "HIGH",
    ),
    *(
        (
            category,
            path,
            "<file>",
            "current_wiring",
            "core" if path.startswith("src/core/") else "deployment" if category == "deployment" else category,
            disposition,
            path if disposition == "switch" else "",
            target_capability if disposition == "switch" else "NONE",
            "tests/architecture/test_cleanup_matrix_guardrail.py",
            "HIGH",
        )
        for path, category, disposition, target_capability in (
            ("src/app/callback/models/external.py", "wms", "delete", ""),
            ("src/app/contracts/external_contract_profile.py", "wms", "delete", ""),
            ("src/app/contracts/external_contract_profile_catalog.py", "wms", "delete", ""),
            ("src/app/contracts/runtime_inbox_query.py", "runtime", "delete", ""),
            ("src/app/contracts/wms_inbound.py", "wms", "delete", ""),
            (
                "docs/architecture/heavy-test-impact.toml",
                "deployment",
                "switch",
                "file:docs/architecture/heavy-test-impact.toml",
            ),
            (
                "scripts/architecture-guardrails.sh",
                "deployment",
                "switch",
                "file:scripts/architecture-guardrails.sh",
            ),
            ("scripts/classify_runtime_inbox_acceptance.py", "runtime", "delete", ""),
            ("scripts/data/reset_runtime_data.py", "runtime", "switch", "reset_runtime_data"),
            ("scripts/dev-env.sh", "deployment", "switch", "file:scripts/dev-env.sh"),
            ("scripts/git-quality-gate.sh", "deployment", "switch", "file:scripts/git-quality-gate.sh"),
            ("scripts/run_runtime_inbox_postgresql_acceptance.py", "runtime", "delete", ""),
            ("scripts/run_runtime_inbox_postgresql_acceptance_ci.sh", "runtime", "delete", ""),
            (
                "scripts/run_selected_heavy_local.sh",
                "deployment",
                "switch",
                "file:scripts/run_selected_heavy_local.sh",
            ),
            ("scripts/run_wms_conformance.py", "wms", "delete", ""),
            ("src/core/conf.py", "deployment", "switch", "Settings"),
            ("src/core/task_queue_gateway.py", "celery-boot", "switch", "CeleryTaskQueueGateway"),
        )
    ),
    *(
        (
            "deployment",
            path,
            "<file>",
            "deployment",
            "deployment",
            "switch",
            path,
            f"file:{path}",
            "tests/deployment/test_execution_worker_startup.py;tests/deployment/test_wms_confirmation_dispatcher.py",
            "HIGH",
        )
        for path in (
            ".env.dev",
            ".env.prod",
            ".env.test",
            "Jenkinsfile.backend-ci",
            "Jenkinsfile.test-deploy",
            "docker-compose.deploy.yml",
            "docker-compose.frontend.yml",
            "docker-compose.test-deploy.yml",
            "docker-compose.yml",
        )
    ),
    (
        "deployment",
        "Jenkinsfile.release-checker-ci",
        "<file>",
        "deployment",
        "deployment",
        "retain",
        "Jenkinsfile.release-checker-ci",
        "file:Jenkinsfile.release-checker-ci",
        "tests/deployment/",
        "MEDIUM",
    ),
    (
        "deployment",
        "docker-compose.wms-acceptance.yml",
        "<file>",
        "deployment",
        "deployment",
        "retain",
        "docker-compose.wms-acceptance.yml",
        "file:docker-compose.wms-acceptance.yml",
        "tests/deployment/test_docker_compose_mock_urls.py",
        "MEDIUM",
    ),
    *(
        (
            "schema-deferred",
            path,
            symbol,
            "model",
            owner,
            "schema-deferred" if path.endswith("workline_runtime_status_projection.py") else "delete",
            "migrations/env.py" if path.endswith("workline_runtime_status_projection.py") else "",
            table_identity if path.endswith("workline_runtime_status_projection.py") else "NONE",
            (
                "tests/architecture/test_runtime_status_owner_guardrail.py"
                if path.endswith("workline_runtime_status_projection.py")
                else "tests/architecture/test_legacy_absence_guardrail.py"
            ),
            "HIGH",
        )
        for path, symbol, owner, table_identity, _blocking_tests in (
            (
                "src/app/runtime/orchestration/models/diagnostic.py",
                "WorklineDiagnostic",
                "runtime",
                "wes_biz.workline_diagnostics",
                "",
            ),
            (
                "src/app/runtime/orchestration/bin_route_instance.py",
                "BinRouteInstance",
                "runtime",
                "wes_runtime.bin_route_instances",
                "",
            ),
            (
                "src/app/runtime/orchestration/conveyor_queue_membership.py",
                "ConveyorQueueMembership",
                "runtime",
                "wes_runtime.conveyor_queue_memberships",
                "",
            ),
            (
                "src/app/runtime/orchestration/idempotency_key.py",
                "IdempotencyKey",
                "runtime",
                "wes_runtime.idempotency_keys",
                "",
            ),
            (
                "src/app/runtime/orchestration/material_flow_owner.py",
                "MaterialFlowOwner",
                "runtime",
                "wes_runtime.material_flow_owners",
                "",
            ),
            (
                "src/app/runtime/orchestration/execution_correlation.py",
                "ExecutionCorrelation",
                "runtime",
                "wes_runtime.execution_correlations",
                "tests/contracts/workline/test_runtime_session_advance_contract.py",
            ),
            (
                "src/app/runtime/orchestration/execution_session.py",
                "ExecutionSession",
                "runtime",
                "wes_runtime.execution_sessions",
                "tests/contracts/workline/test_runtime_session_advance_contract.py",
            ),
            (
                "src/app/runtime/orchestration/execution_work_item.py",
                "ExecutionWorkItem",
                "runtime",
                "wes_runtime.execution_work_items",
                "tests/contracts/workline/test_runtime_session_advance_contract.py",
            ),
            (
                "src/app/runtime/orchestration/models/bin_cell_reservation.py",
                "WorklineBinCellReservation",
                "runtime",
                "wes_biz.workline_bin_cell_reservations",
                "tests/workline_runtime/test_bin_cell_reservation_target_lifecycle.py",
            ),
            (
                "src/app/runtime/orchestration/models/dispatch_attempt.py",
                "WorklineDispatchAttempt",
                "runtime",
                "wes_biz.workline_dispatch_attempts",
                "tests/workline_runtime/test_dispatch_attempt_lease_fencing.py",
            ),
            (
                "src/app/runtime/orchestration/models/runtime_hold.py",
                "NgReturnItem",
                "runtime",
                "wes_biz.ng_return_items",
                "tests/runtime/orchestration/test_runtime_hold_plugin_identity_absence.py",
            ),
            (
                "src/app/runtime/orchestration/models/runtime_hold.py",
                "RuntimeHold",
                "runtime",
                "wes_biz.runtime_holds",
                "tests/runtime/orchestration/test_runtime_hold_plugin_identity_absence.py",
            ),
            (
                "src/app/runtime/orchestration/reconciliation_case.py",
                "ReconciliationCase",
                "runtime",
                "wes_runtime.reconciliation_cases",
                "tests/contracts/system_capabilities/test_effect_reducer_schema_contract.py",
            ),
            (
                "src/app/runtime/orchestration/runtime_hold.py",
                "RuntimeHold",
                "runtime",
                "wes_runtime.runtime_holds",
                "tests/contracts/workline/test_runtime_snapshot_contract.py",
            ),
            (
                "src/app/runtime/orchestration/runtime_inbox.py",
                "RuntimeInbox",
                "runtime",
                "wes_runtime.runtime_inbox",
                "tests/runtime/orchestration/test_runtime_inbox_schema_contract.py",
            ),
            (
                "src/app/runtime/orchestration/runtime_intent_log.py",
                "RuntimeIntentLog",
                "runtime",
                "wes_runtime.runtime_intent_logs",
                "tests/contracts/workline/test_runtime_snapshot_contract.py",
            ),
            (
                "src/app/runtime/orchestration/runtime_timeline.py",
                "RuntimeTimeline",
                "runtime",
                "wes_runtime.runtime_timelines",
                "tests/contracts/workline/test_runtime_timeline_query_contract.py",
            ),
            (
                "src/app/runtime/orchestration/wms_rack_demand.py",
                "WmsRackDemand",
                "runtime",
                "wes_runtime.wms_rack_demands",
                "tests/workline_runtime/system_capabilities/test_wms_rack_supply_demand.py",
            ),
            (
                "src/app/runtime/orchestration/workline_runtime_status_projection.py",
                "WorklineRuntimeStatusProjection",
                "runtime",
                "wes_runtime.workline_runtime_status_projections",
                "tests/workline_runtime/test_workline_runtime_status_projection_service.py",
            ),
            (
                "src/app/sys/models/outbox.py",
                "SystemOutbox",
                "sys",
                "wes_biz.system_outbox",
                "tests/runtime/orchestration/test_runtime_inbox_schema_contract.py",
            ),
            (
                "src/app/wms_integration/models/circuit_breaker.py",
                "WmsCircuitBreakerState",
                "wms_integration",
                "wes_biz.wms_circuit_breaker_state",
                "tests/resilience/test_wms_circuit_breaker.py",
            ),
            (
                "src/app/wms_integration/models/evidence.py",
                "WmsCallEvidence",
                "wms_integration",
                "wes_biz.wms_call_evidence",
                "tests/wms_integration/test_evidence.py",
            ),
        )
    ),
)


def git_grep(pattern: str, paths: list[str]) -> list[str]:
    """合并 tracked 与 filesystem 匹配，覆盖当前尚未纳入 index 的新文件。"""
    compiled = re.compile(pattern)

    def python_grep() -> list[str]:
        matches: list[str] = []
        for raw_path in paths:
            root = REPO_ROOT / raw_path
            candidates = [root] if root.is_file() else root.rglob("*")
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                try:
                    lines = candidate.read_text(encoding="utf-8").splitlines()
                except UnicodeDecodeError:  # noqa: S112
                    # git grep 默认跳过二进制内容；Python fallback 保持同样语义。
                    continue
                rel = candidate.relative_to(REPO_ROOT).as_posix()
                for lineno, line in enumerate(lines, start=1):
                    if compiled.search(line):
                        matches.append(f"{rel}:{lineno}:{line}")
        return matches

    git_matches: list[str] = []
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "grep", "-n", "-E", pattern, "--", *paths],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            git_matches = [ln for ln in result.stdout.splitlines() if ln.strip()]
    except Exception:
        pass
    return list(dict.fromkeys([*git_matches, *python_grep()]))


def classify_business_semantics(symbol: str, path: str) -> tuple[str, bool]:
    """返回 (business_semantics, phase4_carrier)。"""
    text = f"{symbol} {path}".lower()
    for pattern, semantics in BUSINESS_SEMANTICS_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return semantics, semantics.startswith("[phase4]")
    # 默认按 owner 推断
    if "test" in path:
        return "测试", False
    if "plugin" in path:
        return "旧 plugin 框架，目标删除", False
    return "none", False


def assign_strategy(business_semantics: str, entry_type: str) -> tuple[str, str, str]:
    """返回 (strategy, drop_phase, risk)。"""
    text = f"{business_semantics} {entry_type}"
    for pattern, strategy, drop_phase, risk in STRATEGY_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return strategy, drop_phase, risk
    # 默认
    return "keep-contract", "phase5-tech", "LOW"


def _target_runtime_path(entry_type: str, path: str) -> str:
    """按入口类型给 runtime/orchestration 迁移目标目录。"""
    type_targets = {
        "model": "src/app/runtime/orchestration/models/",
        "repository": "src/app/runtime/orchestration/repositories/",
        "api_route": "src/app/runtime/orchestration/v1/",
        "test": "tests/contracts/workline/",
    }
    if entry_type in type_targets:
        return type_targets[entry_type]
    return "src/app/runtime/orchestration/" if "workline_runtime" in path else "src/app/runtime/orchestration/services/"


def _first_matching_value(text: str, rules: list[tuple[tuple[str, ...], str]], default: str) -> str:
    """按 marker 顺序返回第一个匹配值。"""
    return next((value for markers, value in rules if any(marker in text for marker in markers)), default)


def _target_move_path(entry_type: str, path: str) -> str:
    move_targets = [
        (("model",), "src/app/workline/models/"),
        (("repositories",), "src/app/workline/repositories/"),
        (("services",), "src/app/workline/services/"),
    ]
    if entry_type == "api_route":
        return "src/app/workline/v1/"
    if entry_type == "test":
        return "tests/contracts/workline/"
    return _first_matching_value(path, move_targets, "src/app/workline/")


def _target_phase4_capability(text: str) -> tuple[str, str]:
    phase4_targets = [
        (("rough_sorter", "粗分机"), "RoughSorterInboundCapability.run"),
        (("full_box", "满箱"), "FullBoxExchangeCapability.execute"),
        (("sorter_inbound", "smt_sorting", "分拣机"), "SorterInboundCapability.run"),
        (("smt_inbound",), "SmtInboundHandoffCapability.execute"),
        (("ng_return", "ng 退货"), "NgReturnCapability.process"),
        (("single_layer_rack", "单层机架"), "SingleLayerRackCapability.orchestrate"),
        (("station_lease",), "StationLeaseCapability.reserve"),
        (("bin_cell_reservation",), "BinCellReservationCapability.reserve"),
        (("material_identity",), "MaterialIdentityCapability.resolve"),
        (("six_in_one",), "SixInOneContractCapability.validate"),
        (("start_admission", "admission"), "WorkLineStartService.start"),
        (("smt_usage",), "SmtUsagePolicyCapability.evaluate"),
    ]
    capability = _first_matching_value(text, phase4_targets, "MaterialFlowBusinessCapability.execute")
    return "src/app/runtime/capabilities/material_flow/", capability


def _target_runtime_capability(entry_type: str, path: str, text: str) -> tuple[str, str]:
    runtime_targets = [
        (("inbox",), "RuntimeInboxService.process_one"),
        (("outbox", "dispatch", "effect"), "RuntimeIntentLog.dispatch_effect"),
        (("timeline", "trace"), "RuntimeTimelineQueryService.query"),
        (("hold",), "RuntimeHoldService.evaluate"),
        (("session", "orchestrat"), "RuntimeSessionService.advance"),
    ]
    capability = _first_matching_value(text, runtime_targets, "RuntimeOrchestrationService.execute")
    return _target_runtime_path(entry_type, path), capability


def _target_wms_integration_boundary(path: str) -> tuple[str, str]:
    if path == "src/workline_runtime/services.py":
        return "src/app/wms_integration/ports/inventory_operations.py", "wms.inventory.query_inventory@v1"
    return "", ""


def resolve_migration_target(
    business_semantics: str,
    entry_type: str,
    path: str,
    symbol: str,
    strategy: str,
) -> tuple[str, str]:
    """返回 (target_path, target_capability)。

    Phase 0 不生成新 runtime 代码，但矩阵必须给后续 destructive cleanup 一个明确承载者。
    """
    text = f"{business_semantics} {path} {symbol}".lower()
    if strategy == "move":
        return _target_move_path(entry_type, path), ""

    if strategy != "rebuild":
        return "", ""

    if "跨域 session" in business_semantics:
        return "src/app/runtime/orchestration/models/execution_correlation.py", "ExecutionCorrelation.correlation_id"
    if "跨域 WMS import" in business_semantics or "wms_integration" in text:
        return _target_wms_integration_boundary(path)
    if "import device" in text or "device." in text or "device_" in text or "device command" in text:
        return "src/app/runtime/orchestration/ports/device_command.py", "DeviceCommandPort.dispatch"
    if "执行状态" in business_semantics:
        return _target_runtime_capability(entry_type, path, text)
    if "start_admission" in text or "start admission" in text:
        return "src/app/workline/services/workline_start_service.py", "WorkLineStartService.start"
    if path == "src/app/workline/domain/ng_reason.py":
        target = (MIGRATED_DOMAIN_IMPLS[path], symbol)
    elif "[phase4]" in business_semantics:
        target = _target_phase4_capability(text)
    else:
        target = (_target_runtime_path(entry_type, path), "RuntimeOrchestrationService.execute")
    return target


def resolve_blocking_tests(business_semantics: str, entry_type: str, path: str, strategy: str) -> str:
    """返回删除/迁移前必须通过的测试清单，CSV 用分号分隔。"""
    text = f"{business_semantics} {path}".lower()
    if entry_type == "test":
        return path
    rules = [
        (
            ("rough_sorter", "粗分机"),
            "tests/architecture/test_core_plugin_test_ownership_guardrail.py",
        ),
        (
            ("full_box", "满箱"),
            "tests/architecture/test_core_plugin_test_ownership_guardrail.py",
        ),
        (
            ("sorter_inbound", "smt_sorting", "分拣机"),
            "tests/architecture/test_core_plugin_test_ownership_guardrail.py",
        ),
        (
            ("执行状态",),
            "tests/architecture/test_runtime_inbox_state_machine_guardrail.py;"
            "tests/contracts/workline/test_runtime_snapshot_contract.py",
        ),
        (("跨域 session",), "tests/architecture/test_execution_correlation_boundary_guardrail.py"),
        (
            ("跨域 WMS import",),
            "tests/architecture/test_wms_integration_boundary_guardrail.py;"
            "tests/contracts/workline/test_external_contract_profile_fixtures.py",
        ),
        (
            ("WorkLine 配置",),
            "tests/workline/test_workline_start_service.py;tests/api/test_workline_start_api.py",
        ),
        (
            ("start admission", "start_admission"),
            "tests/workline/test_workline_start_service.py;tests/api/test_workline_start_api.py",
        ),
        (("技术残留",), "tests/architecture/test_core_plugin_test_ownership_guardrail.py"),
    ]
    default = (
        "tests/architecture/test_core_plugin_test_ownership_guardrail.py"
        if strategy == "delete"
        else "tests/contracts/workline/;tests/architecture/"
    )
    return _first_matching_value(f"{business_semantics} {text}", rules, default)


SeedPath = tuple[str, str, str, str, str, str]


def _capability_implementation_import_business_semantics(line: str) -> str:
    if "src.app.wms_integration." in line:
        return "capability import wms_integration 实现 (CAPABILITY_IMPLEMENTATION_IMPORT seed)"
    return "capability import device 实现 (CAPABILITY_IMPLEMENTATION_IMPORT seed)"


def _has_exact_capability_implementation_retain_relation(path: str) -> bool:
    """仅 exact allowlist→prelock retain relation 可阻止 legacy seed 生成。"""

    matching_rows: list[tuple[str, str, str, str, str, str]] = []
    allowlist = REPO_ROOT / "scripts" / "architecture-guardrails.allowlist"
    for row in allowlist.read_text(encoding="utf-8").splitlines():
        if not row.startswith("CAPABILITY_IMPLEMENTATION_IMPORT|"):
            continue
        fields = tuple(row.split("|"))
        if len(fields) != 6:
            raise RuntimeError("CAPABILITY_IMPLEMENTATION_IMPORT allowlist row must have exactly 6 fields")
        if fields[1] == path:
            matching_rows.append(fields)

    target_rows = [row for row in matching_rows if not row[4].endswith("#CAPABILITY_IMPLEMENTATION_IMPORT")]
    if not target_rows:
        return False
    if len(matching_rows) != 1 or len(target_rows) != 1:
        raise RuntimeError(f"CAPABILITY_IMPLEMENTATION_IMPORT retain relation must be unique: {path}")

    rule, allowlist_path, reason, expires_at, entry_id, drop_phase = target_rows[0]
    retain_specs: dict[str, Phase10PrelockSpec] = {}
    for spec in PHASE10_PRELOCK_SPECS:
        category, spec_path, symbol, _entry_type, owner, disposition, target_path, target_capability, *_rest = spec
        if (
            category == "runtime"
            and owner == "runtime"
            and disposition == "retain"
            and target_path == spec_path
            and target_capability == symbol
        ):
            retain_specs[f"legacy:{spec_path}:{symbol}"] = spec
    spec = retain_specs.get(entry_id)
    if (
        rule != "CAPABILITY_IMPLEMENTATION_IMPORT"
        or allowlist_path != path
        or not reason.strip()
        or not expires_at.strip()
        or spec is None
        or spec[1] != path
        or drop_phase != "phase10"
    ):
        raise RuntimeError(f"CAPABILITY_IMPLEMENTATION_IMPORT retain relation is invalid: {path}")
    return True


def _append_capability_implementation_import_seed_paths(seed_paths: list[SeedPath]) -> None:
    capability_implementation_import_pattern = r"from src\.app\.(wms_integration|device)\.(services|models)\..* import"
    scan_paths = [
        "src/app/workline/services",
        "src/app/workline/repositories",
        "src/app/runtime/orchestration/repositories",
        *MIGRATED_SERVICE_IMPLS.values(),
    ]
    for line in git_grep(capability_implementation_import_pattern, scan_paths):
        m = re.match(r"([^:]+):(\d+):", line)
        if not m:
            continue
        if _has_exact_capability_implementation_retain_relation(m.group(1)):
            continue
        path = MIGRATED_IMPL_TO_LEGACY.get(m.group(1), MIGRATED_REPOSITORIES_TO_LEGACY.get(m.group(1), m.group(1)))
        etype = "repository" if "/repositories/" in path else "service"
        seed_paths.append(
            (
                path,
                "workline",
                etype,
                _capability_implementation_import_business_semantics(line),
                "phase2",
                "MEDIUM",
            )
        )


def _append_runtime_extension_guardrail_seed_paths(seed_paths: list[SeedPath]) -> None:
    """逐文件登记 Task 10 前仍需清理的旧路由与编排分支。"""

    allowlist = REPO_ROOT / "scripts" / "architecture-guardrails.allowlist"
    for row in allowlist.read_text(encoding="utf-8").splitlines():
        if not row.startswith(("LEGACY_CAPABILITY_ROUTING_IMPORT|", "RUNTIME_EXTENSION_GENERIC_ORCHESTRATION|")):
            continue
        rule, path, _reason, _expires_at, _entry_id, phase = row.split("|")
        owner = path.split("/")[2] if path.startswith("src/app/") else "runtime"
        etype = "service" if "/services/" in path else "runtime_helper"
        seed_paths.append(
            (
                path,
                owner,
                etype,
                f"Task 10 待清理扩展平台残留 ({rule} seed)",
                phase,
                "HIGH" if rule == "RUNTIME_EXTENSION_GENERIC_ORCHESTRATION" else "MEDIUM",
            )
        )


def _exported_symbols_from_all(path: Path) -> list[str]:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []

    symbols: list[str] = []
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            continue
        if not isinstance(node.value, ast.List | ast.Tuple):
            continue
        symbols.extend(
            item.value for item in node.value.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)
        )
    return symbols


def _defined_symbols_from_python(path: Path) -> list[str]:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []

    return [
        node.name for node in module.body if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def _phase10_python_symbols(path: Path) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    symbols: set[str] = set()
    for node in module.body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            symbols.add(node.name)
            if isinstance(node, ast.ClassDef):
                symbols.update(
                    f"{node.name}.{child.name}"
                    for child in node.body
                    if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                )
        elif isinstance(node, ast.Assign):
            symbols.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            symbols.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            symbols.update(alias.asname or alias.name for alias in node.names if alias.name != "*")
        elif isinstance(node, ast.Import):
            symbols.update(alias.asname or alias.name.split(".", maxsplit=1)[0] for alias in node.names)
    return symbols


def _phase10_registered_celery_task_names(path: Path) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    names: set[str] = set()
    for node in module.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            for keyword in decorator.keywords:
                if (
                    keyword.arg == "name"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    names.add(keyword.value.value)
    return names


def _validate_phase10_queue_identity(path: Path, identity: str) -> None:
    if not identity.startswith("queue:"):
        raise RuntimeError(f"phase10 queue identity must use queue:<name>: {identity}")
    queue_name = identity.removeprefix("queue:")
    module = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    configured_queue_names = {
        value.value
        for node in ast.walk(module)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant)
        and key.value == "queue"
        and isinstance(value, ast.Constant)
        and isinstance(value.value, str)
    }
    if queue_name not in configured_queue_names:
        raise RuntimeError(f"phase10 queue identity does not exist in {path}: {identity}")


_PHASE10_SCHEMA_COLLECTOR_SOURCE = r"""
import hashlib
import importlib
import json
import sys

from sqlmodel import SQLModel

request = json.loads(sys.stdin.read())
import_specs = request["import_specs"]
import_spec_digest = request["import_spec_digest"]
source_models = request["source_models"]
canonical_import_specs = json.dumps(import_specs, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
if hashlib.sha256(canonical_import_specs.encode("utf-8")).hexdigest() != import_spec_digest:
    raise RuntimeError("import spec digest mismatch")
for kind, module_name, names in import_specs:
    if kind == "from":
        imported_module = __import__(module_name, fromlist=[name for name, _asname in names])
        for name, asname in names:
            if name == "*":
                raise RuntimeError("wildcard model import is not supported")
            if asname and not asname.isidentifier():
                raise RuntimeError("invalid from-import alias")
            getattr(imported_module, name)
    elif kind == "import":
        if len(names) != 1 or names[0][0] != module_name:
            raise RuntimeError("invalid exact module import spec")
        if names[0][1] and not names[0][1].isidentifier():
            raise RuntimeError("invalid module import alias")
        imported_module = importlib.import_module(module_name)
        if sys.modules.get(module_name) is not imported_module:
            raise RuntimeError("exact module import did not resolve")
    else:
        raise RuntimeError("unsupported model import kind")

tables = {}
for table in sorted(SQLModel.metadata.sorted_tables, key=lambda item: item.fullname):
    tables[table.fullname] = sorted(
        {foreign_key.target_fullname.rsplit(".", maxsplit=1)[0] for foreign_key in table.foreign_keys}
    )

model_tables = {}
for module_name, symbol in source_models:
    model = getattr(importlib.import_module(module_name), symbol)
    table = getattr(model, "__table__", None)
    model_tables[f"{module_name}:{symbol}"] = getattr(table, "fullname", "NONE")

snapshot = {
    "protocol": 1,
    "import_specs": import_specs,
    "import_spec_digest": import_spec_digest,
    "source_models": source_models,
    "tables": tables,
    "model_tables": model_tables,
}
canonical = json.dumps(snapshot, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
result = {"fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(), "snapshot": snapshot}
print(json.dumps(result, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
"""


def _phase10_migration_import_specs() -> tuple[Phase10ImportSpec, ...]:
    migration_env = REPO_ROOT / "migrations" / "env.py"
    module = ast.parse(migration_env.read_text(encoding="utf-8"), filename=migration_env.as_posix())
    specs: list[Phase10ImportSpec] = []
    import_nodes = sorted(
        (node for node in ast.walk(module) if isinstance(node, ast.Import | ast.ImportFrom)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    for node in import_nodes:
        if isinstance(node, ast.ImportFrom):
            if not node.module or not (node.module == "src.app" or node.module.startswith("src.app.")):
                continue
            if node.level != 0 or any(alias.name == "*" for alias in node.names):
                raise RuntimeError("phase10 migration model import must be an absolute named import")
            specs.append(
                (
                    "from",
                    node.module,
                    tuple((alias.name, alias.asname or "") for alias in node.names),
                )
            )
            continue
        specs.extend(
            ("import", alias.name, ((alias.name, alias.asname or ""),))
            for alias in node.names
            if alias.name == "src.app" or alias.name.startswith("src.app.")
        )
    if not specs:
        raise RuntimeError("phase10 migration model import graph is empty")
    return tuple(specs)


def _phase10_schema_source_models() -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            {
                (path.removesuffix(".py").replace("/", "."), symbol)
                for _category, path, symbol, _entry_type, _owner, disposition, *_rest in PHASE10_PRELOCK_SPECS
                if disposition == "schema-deferred"
            }
        )
    )


def collect_isolated_phase10_schema_snapshot(
    import_specs: tuple[Phase10ImportSpec, ...], source_models: tuple[tuple[str, str], ...]
) -> Phase10SchemaSnapshot:
    """在无 inherited env 的 fresh interpreter 中采集 migration metadata/FK provenance。"""

    import_specs_payload = [
        [kind, module_name, [list(name) for name in names]] for kind, module_name, names in import_specs
    ]
    canonical_import_specs = json.dumps(import_specs_payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    import_spec_digest = hashlib.sha256(canonical_import_specs.encode("utf-8")).hexdigest()
    return _collect_isolated_phase10_schema_snapshot(import_specs, import_spec_digest, source_models)


@cache
def _collect_isolated_phase10_schema_snapshot(
    import_specs: tuple[Phase10ImportSpec, ...],
    import_spec_digest: str,
    source_models: tuple[tuple[str, str], ...],
) -> Phase10SchemaSnapshot:
    """以 canonical specs/digest/source models 为完整 cache key 执行 isolated collector。"""

    import_specs_payload = [
        [kind, module_name, [list(name) for name in names]] for kind, module_name, names in import_specs
    ]
    request = {
        "import_specs": import_specs_payload,
        "import_spec_digest": import_spec_digest,
        "source_models": [list(source_model) for source_model in source_models],
    }
    try:
        completed = subprocess.run(  # noqa: S603 -- fixed current interpreter runs a constant collector program
            [sys.executable, "-c", _PHASE10_SCHEMA_COLLECTOR_SOURCE],
            cwd=REPO_ROOT,
            env={
                "API_SECRET_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
                "DATABASE_POOL_SIZE": "1",
                "DATABASE_RUNTIME_ROLE": "cli",
                "JWT_SECRET_KEY": "phase10-schema-collector-only-key-000000000000000000",
                "PYTHONHASHSEED": "0",
                "PYTHONPATH": str(REPO_ROOT),
            },
            input=json.dumps(request, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("phase10 isolated schema metadata collector timed out") from exc
    if completed.returncode != 0:
        raise RuntimeError(f"phase10 isolated schema metadata collector failed with exit {completed.returncode}")
    try:
        result = json.loads(completed.stdout)
        if not isinstance(result, dict):
            raise TypeError
        snapshot_payload = result["snapshot"]
        fingerprint = result["fingerprint"]
        if not isinstance(snapshot_payload, dict):
            raise TypeError
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError("phase10 isolated schema metadata collector returned invalid JSON") from exc
    canonical = json.dumps(snapshot_payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    actual_fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if not isinstance(fingerprint, str) or fingerprint != actual_fingerprint:
        raise RuntimeError("phase10 isolated schema metadata fingerprint mismatch")
    if snapshot_payload.get("protocol") != 1:
        raise RuntimeError("phase10 isolated schema metadata protocol mismatch")
    if snapshot_payload.get("import_specs") != request["import_specs"]:
        raise RuntimeError("phase10 isolated schema metadata import provenance mismatch")
    if snapshot_payload.get("import_spec_digest") != request["import_spec_digest"]:
        raise RuntimeError("phase10 isolated schema metadata import digest mismatch")
    if snapshot_payload.get("source_models") != request["source_models"]:
        raise RuntimeError("phase10 isolated schema metadata source provenance mismatch")
    tables_payload = snapshot_payload.get("tables")
    model_tables_payload = snapshot_payload.get("model_tables")
    if not isinstance(tables_payload, dict) or not isinstance(model_tables_payload, dict):
        raise TypeError("phase10 isolated schema metadata payload is invalid")
    if any(
        not isinstance(table, str)
        or not isinstance(targets, list)
        or any(not isinstance(target, str) for target in targets)
        for table, targets in tables_payload.items()
    ) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in model_tables_payload.items()):
        raise RuntimeError("phase10 isolated schema metadata table payload is invalid")
    return Phase10SchemaSnapshot(
        tables=frozenset(tables_payload),
        foreign_key_targets=tuple((table, tuple(targets)) for table, targets in sorted(tables_payload.items())),
        model_tables=tuple(sorted(model_tables_payload.items())),
        fingerprint=fingerprint,
    )


@cache
def load_phase10_schema_snapshot() -> Phase10SchemaSnapshot:
    return collect_isolated_phase10_schema_snapshot(
        _phase10_migration_import_specs(),
        _phase10_schema_source_models(),
    )


def validate_phase10_schema_identity(
    path: str,
    symbol: str,
    target_capability: str,
    *,
    schema_snapshot: Phase10SchemaSnapshot | None = None,
) -> None:
    """验证 source model 与 isolated migration metadata 的 schema.table identity 一致。"""

    if not re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*", target_capability):
        raise RuntimeError(f"phase10 schema identity is invalid: {path}:{target_capability}")
    snapshot = schema_snapshot or load_phase10_schema_snapshot()
    if target_capability not in snapshot.tables:
        raise RuntimeError(
            f"phase10 schema identity is absent from isolated migration metadata: {path}:{target_capability}"
        )
    model_key = f"{path.removesuffix('.py').replace('/', '.')}:{symbol}"
    actual_table = dict(snapshot.model_tables).get(model_key)
    if actual_table != target_capability:
        raise RuntimeError(
            f"phase10 schema identity does not match isolated source model: "
            f"{path}:{symbol}:{actual_table or 'NONE'}!={target_capability}"
        )


def _validate_phase10_prelock_spec(
    spec: Phase10PrelockSpec, *, schema_snapshot: Phase10SchemaSnapshot | None = None
) -> None:
    (
        _category,
        path,
        symbol,
        entry_type,
        _owner,
        disposition,
        target_path,
        target_capability,
        blocking_tests,
        _risk,
    ) = spec
    source_path = REPO_ROOT / path
    if not source_path.exists():
        raise RuntimeError(f"phase10 pre-lock identity path does not exist: {path}")
    if entry_type == "celery_task":
        if symbol not in _phase10_registered_celery_task_names(source_path):
            raise RuntimeError(f"phase10 Celery registered task identity does not exist: {path}:{symbol}")
    elif entry_type == "queue":
        _validate_phase10_queue_identity(source_path, symbol)
    elif symbol != "<file>" and (source_path.suffix != ".py" or symbol not in _phase10_python_symbols(source_path)):
        raise RuntimeError(f"phase10 Python symbol identity does not exist: {path}:{symbol}")

    for test_path in blocking_tests.split(";"):
        if not test_path or not (REPO_ROOT / test_path).exists():
            raise RuntimeError(f"phase10 blocking test path does not exist: {path}:{test_path}")

    if disposition == "delete":
        if target_path or target_capability != "NONE":
            raise RuntimeError(f"phase10 delete disposition must target NONE: {path}:{symbol}")
        return
    if disposition not in {"switch", "retain", "schema-deferred"}:
        raise RuntimeError(f"phase10 disposition is not final: {path}:{symbol}:{disposition}")

    resolved_target_path = REPO_ROOT / target_path
    if not resolved_target_path.exists():
        raise RuntimeError(f"phase10 target path does not exist: {path}:{target_path}")
    if disposition == "schema-deferred":
        validate_phase10_schema_identity(path, symbol, target_capability, schema_snapshot=schema_snapshot)
    elif entry_type == "queue":
        if target_capability != symbol:
            raise RuntimeError(f"phase10 queue target must preserve its machine identity: {path}:{symbol}")
        _validate_phase10_queue_identity(resolved_target_path, target_capability)
    elif resolved_target_path.suffix == ".py":
        if target_capability not in _phase10_python_symbols(resolved_target_path):
            raise RuntimeError(f"phase10 target Python symbol does not exist: {target_path}:{target_capability}")
    elif target_capability != f"file:{target_path}":
        raise RuntimeError(f"phase10 non-Python target must use file:<path> identity: {path}:{target_capability}")


def _phase10_source_identity_exists(spec: Phase10PrelockSpec) -> bool:
    """判断冻结 identity 是否仍存在；Task 5 只退休已物理移除的 delete/switch identity。"""

    _category, path, symbol, entry_type, _owner, _disposition, *_rest = spec
    source_path = REPO_ROOT / path
    if not source_path.exists():
        return False
    if symbol == "<file>":
        return True
    if entry_type == "celery_task":
        return symbol in _phase10_registered_celery_task_names(source_path)
    if entry_type == "queue":
        try:
            _validate_phase10_queue_identity(source_path, symbol)
        except RuntimeError:
            return False
        return True
    return source_path.suffix == ".py" and symbol in _phase10_python_symbols(source_path)


def _phase10_prelock_spec_is_retired(spec: Phase10PrelockSpec) -> bool:
    """Execution Lock 后仅 delete/switch identity 可因 Task 5 实际移除而退出当前矩阵。"""

    disposition = spec[5]
    return disposition in {"delete", "switch"} and not _phase10_source_identity_exists(spec)


def _add_phase10_prelock_entries(entries: list[Entry], seen: set[str]) -> None:
    """登记 Execution Lock 前冻结的 Phase 10 disposition，不允许静默覆盖旧条目。"""

    for spec in PHASE10_PRELOCK_SPECS:
        if _phase10_prelock_spec_is_retired(spec):
            continue
        _validate_phase10_prelock_spec(spec)
        (
            category,
            path,
            symbol,
            entry_type,
            owner,
            disposition,
            target_path,
            target_capability,
            blocking_tests,
            risk,
        ) = spec
        entry_id = f"legacy:{path}:{symbol}"
        if entry_id in seen:
            raise RuntimeError(f"phase10 pre-lock identity is not unique: {entry_id}")
        seen.add(entry_id)
        entries.append(
            Entry(
                entry_id=entry_id,
                entry_type=entry_type,
                relative_path=path,
                symbol_or_route=symbol,
                current_owner=owner,
                business_semantics=f"Phase 10 {category} {disposition} disposition",
                phase4_carrier=False,
                classification_status="final",
                strategy=disposition,
                target_path=target_path,
                target_capability=target_capability,
                blocking_tests=blocking_tests,
                drop_phase="phase11-schema" if disposition == "schema-deferred" else "phase10",
                risk=risk,
                notes=f"phase10-prelock:{category}:{disposition}",
            )
        )


def _add_migrated_service_entries(add: Callable[[str, str, str, str], None]) -> None:
    if set(MIGRATED_SERVICE_SYMBOL_PROVENANCE) != set(MIGRATED_SERVICE_IMPLS):
        raise RuntimeError("migrated service symbol provenance must cover every legacy service path")
    for legacy_path, symbols in MIGRATED_SERVICE_SYMBOL_PROVENANCE.items():
        for symbol in symbols:
            add(legacy_path, symbol, "service", "workline")


def _add_migrated_domain_entries(add: Callable[[str, str, str, str], None]) -> None:
    for legacy_path, impl_path in MIGRATED_DOMAIN_IMPLS.items():
        for symbol in _defined_symbols_from_python(REPO_ROOT / impl_path):
            add(legacy_path, symbol, "domain_object", "workline")


def _add_guardrail_seed_entries(entries: list[Entry], seen: set[str], seed_paths: list[SeedPath]) -> None:
    for path, owner, etype, bs, phase, risk in seed_paths:
        sym = GUARDRAIL_SEED_SYMBOLS.get(path)
        if sym is None:
            guardrail_rule = next(
                (
                    rule
                    for rule in (
                        "CAPABILITY_IMPLEMENTATION_IMPORT",
                        "LEGACY_CAPABILITY_ROUTING_IMPORT",
                        "RUNTIME_EXTENSION_GENERIC_ORCHESTRATION",
                    )
                    if f"{rule} seed" in bs
                ),
                None,
            )
            sym = f"<file>#{guardrail_rule}" if guardrail_rule else "<file>"
        eid = f"legacy:{path}:{sym}"
        if eid in seen:
            continue
        seen.add(eid)
        strategy = "delete" if path == "src/app/callback/services/callback_ingress_service.py" else "rebuild"
        target_path, target_capability = resolve_migration_target(bs, etype, path, sym, strategy)
        blocking_tests = resolve_blocking_tests(bs, etype, path, strategy)
        entries.append(
            Entry(
                entry_id=eid,
                entry_type=etype,
                relative_path=path,
                symbol_or_route=sym,
                current_owner=owner,
                business_semantics=bs,
                phase4_carrier=False,
                classification_status="final",
                strategy=strategy,
                target_path=target_path,
                target_capability=target_capability,
                blocking_tests=blocking_tests,
                drop_phase=phase,
                risk=risk,
                notes="guardrail_seed_scope",
            )
        )


def parse_entries() -> list[Entry]:
    active_legacy_imports = find_active_platform_legacy_imports()
    if active_legacy_imports:
        joined = ", ".join(active_legacy_imports)
        raise RuntimeError(f"active extension platform imports legacy capability routing: {joined}")

    entries: list[Entry] = []
    seen: set[str] = set()

    def add(path: str, symbol: str, entry_type: str, owner: str):
        rel = str(Path(path).relative_to(REPO_ROOT)) if Path(path).is_absolute() else path
        sym = symbol or "<file>"
        eid = f"legacy:{rel}:{sym}"
        if (
            rel in ACTIVE_FOUNDATION_PATHS
            or rel in ACTIVE_PLATFORM_PATHS
            or rel.startswith(ACTIVE_PLATFORM_PREFIXES)
            or (rel, sym) in ACTIVE_PLATFORM_SYMBOLS
            or eid in seen
            or (rel, sym) in SHIM_INTERNAL_SYMBOLS
        ):
            return
        seen.add(eid)
        bs, p4 = ("none", False) if (rel, sym) in CURRENT_CONTRACT_SYMBOLS else classify_business_semantics(sym, rel)
        strat, phase, risk = assign_strategy(bs, entry_type)
        target_path, target_capability = resolve_migration_target(bs, entry_type, rel, sym, strat)
        blocking_tests = resolve_blocking_tests(bs, entry_type, rel, strat)
        entries.append(
            Entry(
                entry_id=eid,
                entry_type=entry_type,
                relative_path=rel,
                symbol_or_route=sym,
                current_owner=owner,
                business_semantics=bs,
                phase4_carrier=p4,
                strategy=strat,
                target_path=target_path,
                target_capability=target_capability,
                blocking_tests=blocking_tests,
                drop_phase=phase,
                risk=risk,
            )
        )

    # 1. API routes (FastAPI 装饰器可能跨行, 不解析 path, 用 method+line 作 symbol)
    for line in git_grep(r"@router\.(get|post|put|delete|patch)", ["src/app/workline/v1"]):
        m = re.match(r"([^:]+):(\d+):@router\.(\w+)\(", line)
        if m:
            add(m.group(1), f"route_{m.group(3)}_L{m.group(2)}", "api_route", "workline")

    # 2. Models (class)  — POSIX ERE 不支持 \w，用 [A-Za-z_]
    for line in git_grep(r"^class [A-Za-z_]", ["src/app/workline/models"]):
        m = re.match(r"([^:]+):(\d+):class ([A-Za-z_][A-Za-z0-9_]*)", line)
        if m:
            add(m.group(1), m.group(3), "model", "workline")

    # 3. Services (class + def + async def)
    for line in git_grep(
        r"^class [A-Za-z_]|^def [a-z_]|^async def [a-z_]",
        ["src/app/workline/services"],
    ):
        m = re.match(r"([^:]+):(\d+):(?:class |def |async def )([A-Za-z_][A-Za-z0-9_]*)", line)
        if m:
            add(m.group(1), m.group(3), "service", "workline")

    # 3b. 已迁入 runtime/capabilities 的 service 仍按旧 workline/services 入口记账。
    _add_migrated_service_entries(add)

    # 4. Repositories
    for line in git_grep(r"^class [A-Za-z_]", ["src/app/workline/repositories"]):
        m = re.match(r"([^:]+):(\d+):class ([A-Za-z_][A-Za-z0-9_]*)", line)
        if m and "Repository" in m.group(3):
            add(m.group(1), m.group(3), "repository", "workline")

    # 5. Domain (class + def)
    for line in git_grep(r"^class [A-Za-z_]|^def [a-z_]|^async def [a-z_]", ["src/app/workline/domain"]):
        m = re.match(r"([^:]+):(\d+):(?:class |def |async def )([A-Za-z_][A-Za-z0-9_]*)", line)
        if m:
            add(m.group(1), m.group(3), "domain_object", "workline")

    # 5b. Business legacy cleanup 后,已迁入 material-flow contracts/services 的 domain
    # 符号仍按 legacy path 进入 matrix,保证 audit trace 与 ledger 稳定。
    _add_migrated_domain_entries(add)

    # 6. workline_runtime + workline_plugins (class + def)
    for line in git_grep(
        r"^class [A-Za-z_]|^def [a-z_]|^async def [a-z_]", ["src/workline_runtime", "src/workline_plugins"]
    ):
        m = re.match(r"([^:]+):(\d+):(?:class |def |async def )([A-Za-z_][A-Za-z0-9_]*)", line)
        if m:
            path = m.group(1)
            owner = "workline_plugins" if "workline_plugins" in path else "workline_runtime"
            etype = "plugin" if "plugin" in path.lower() else "runtime_helper"
            add(path, m.group(3), etype, owner)

    # 6b. plugin/runtime artifact public exports (__all__)
    for export_root in ("src/workline_runtime", "src/workline_plugins"):
        for path in sorted((REPO_ROOT / export_root).rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            owner = "workline_plugins" if "workline_plugins" in rel else "workline_runtime"
            etype = "plugin" if "workline_plugins" in rel else "runtime_helper"
            for symbol in _exported_symbols_from_all(path):
                add(rel, symbol, etype, owner)

    # 7. 核心仅扫描通用 runtime 测试；具体插件测试由仓库根目录独立包自行治理。
    for line in git_grep(r"^class Test|^def test_|^async def test_", ["tests/workline_runtime"]):
        m = re.match(r"([^:]+):(\d+):(?:class |def |async def )([A-Za-z_][A-Za-z0-9_]*)", line)
        if m:
            path = m.group(1)
            add(path, m.group(3), "test", "workline_runtime")

    # 8. doc_templates (文件级)
    for tmpl in (REPO_ROOT / "docs/templates/workline_plugin").glob("*"):
        if tmpl.is_file():
            add(str(tmpl), "<file>", "doc_template", "workline_runtime")

    # 9. guardrail_seed_scope: P0-007 seed allowlist 命中的跨域路径
    # (callback/rack/handling/resource/wms_integration, 非完整清理, 仅供 allowlist 追踪)
    seed_paths = [
        # WMS_INTEGRATION_BOUNDARY: 跨域 WMS import
        (
            "src/app/callback/services/callback_ingress_service.py",
            "callback",
            "service",
            "跨域 WMS import (WMS_INTEGRATION_BOUNDARY seed)",
            "phase2",
            "MEDIUM",
        ),
        (
            "src/workline_runtime/services.py",
            "workline_runtime",
            "runtime_helper",
            "跨域 WMS import (WMS_INTEGRATION_BOUNDARY seed)",
            "phase5-tech",
            "LOW",
        ),
        # EXECUTION_CORRELATION_BOUNDARY: 跨域 session FK
        (
            "src/app/resource/services/projection_service.py",
            "resource",
            "service",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase1",
            "MEDIUM",
        ),
        (
            "src/app/resource/services/projection_integrity_service.py",
            "resource",
            "service",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase1",
            "MEDIUM",
        ),
        (
            "src/app/resource/services/relation_service.py",
            "resource",
            "service",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase1",
            "MEDIUM",
        ),
        (
            "src/app/resource/services/smt_rack_bin_scheduling_service.py",
            "resource",
            "service",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase1",
            "MEDIUM",
        ),
        (
            "src/app/resource/models/resource.py",
            "resource",
            "model",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase1",
            "MEDIUM",
        ),
        (
            "src/app/wms_integration/services/transport_contract.py",
            "wms_integration",
            "service",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase1",
            "MEDIUM",
        ),
        (
            "src/app/workline/models/object_transition_event.py",
            "workline",
            "model",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase2",
            "MEDIUM",
        ),
        (
            "src/app/workline/repositories/object_transition_event_repository.py",
            "workline",
            "repository",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase2",
            "LOW",
        ),
        (
            "src/app/workline/services/object_transition_event_service.py",
            "workline",
            "service",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase2",
            "MEDIUM",
        ),
        (
            "src/app/runtime/orchestration/services/inbox/object_transition_event_service.py",
            "runtime",
            "service",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed — impl 物理迁入 inbox/ 后 path 跟踪)",
            "phase2",
            "MEDIUM",
        ),
        (
            "src/app/workline/services/runtime_reconciliation_service.py",
            "workline",
            "service",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase2",
            "MEDIUM",
        ),
        (
            "src/app/runtime/orchestration/services/reconciliation/runtime_reconciliation_service_impl.py",
            "runtime",
            "service",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed — impl 物理迁入 reconciliation/ 后 path 跟踪)",
            "phase2",
            "MEDIUM",
        ),
    ]

    _append_capability_implementation_import_seed_paths(seed_paths)
    _append_runtime_extension_guardrail_seed_paths(seed_paths)
    seed_paths.extend(
        [
            (
                "src/app/runtime/orchestration/services/intent/operation_service.py",
                "runtime",
                "service",
                "capability import device 实现 (CAPABILITY_IMPLEMENTATION_IMPORT seed — impl 物理迁入 intent/ 后 path 跟踪)",
                "phase2",
                "MEDIUM",
            ),
            (
                "src/app/runtime/orchestration/services/hold/runtime_hold_query_service.py",
                "runtime",
                "service",
                "capability import device 实现 (CAPABILITY_IMPLEMENTATION_IMPORT seed — impl 物理迁入 hold/ 后 path 跟踪)",
                "phase2",
                "MEDIUM",
            ),
            (
                "src/app/runtime/orchestration/services/hold/runtime_hold_release_service.py",
                "runtime",
                "service",
                "capability import device 实现 (CAPABILITY_IMPLEMENTATION_IMPORT seed — impl 物理迁入 hold/ 后 path 跟踪)",
                "phase2",
                "MEDIUM",
            ),
            (
                "src/app/runtime/orchestration/services/reconciliation/runtime_reconciliation_service_impl.py",
                "runtime",
                "service",
                "capability import device 实现 (CAPABILITY_IMPLEMENTATION_IMPORT seed — impl 物理迁入 reconciliation/ 后 path 跟踪)",
                "phase2",
                "MEDIUM",
            ),
            (
                "src/app/runtime/orchestration/services/trace/trace_query_service.py",
                "runtime",
                "service",
                "capability import device 实现 (CAPABILITY_IMPLEMENTATION_IMPORT seed — impl 物理迁入 trace/ 后 path 跟踪)",
                "phase2",
                "MEDIUM",
            ),
            (
                "src/app/runtime/orchestration/services/device_command_gateway.py",
                "runtime",
                "service",
                "capability import device 实现 (CAPABILITY_IMPLEMENTATION_IMPORT seed — impl 物理迁入 orchestration/ 后 path 跟踪)",
                "phase2",
                "MEDIUM",
            ),
        ]
    )
    _add_guardrail_seed_entries(entries, seen, seed_paths)

    # 10. Phase 10 pre-lock disposition：在既有 generator/CSV 中形成唯一机器真源。
    _add_phase10_prelock_entries(entries, seen)

    return entries


def main() -> int:
    entries = parse_entries()
    if not entries:
        print("ERROR: 未扫描到任何入口", file=sys.stderr)
        return 1

    out_path = REPO_ROOT / "docs/architecture/legacy-cleanup-matrix.csv"
    fields = [
        "entry_id",
        "entry_type",
        "relative_path",
        "symbol_or_route",
        "current_owner",
        "business_semantics",
        "phase4_carrier",
        "classification_status",
        "strategy",
        "target_path",
        "target_capability",
        "blocking_tests",
        "drop_phase",
        "risk",
        "notes",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for e in entries:
            writer.writerow(
                {
                    "entry_id": e.entry_id,
                    "entry_type": e.entry_type,
                    "relative_path": e.relative_path,
                    "symbol_or_route": e.symbol_or_route,
                    "current_owner": e.current_owner,
                    "business_semantics": e.business_semantics,
                    "phase4_carrier": e.phase4_carrier,
                    "classification_status": e.classification_status,
                    "strategy": e.strategy,
                    "target_path": e.target_path,
                    "target_capability": e.target_capability,
                    "blocking_tests": e.blocking_tests,
                    "drop_phase": e.drop_phase,
                    "risk": e.risk,
                    "notes": e.notes,
                }
            )

    # 汇总统计
    print(f"已生成 {out_path.relative_to(REPO_ROOT)}")
    print(f"\n=== total_entries: {len(entries)} ===")
    print("\n=== total_entries_by_type ===")
    for t, c in Counter(e.entry_type for e in entries).most_common():
        print(f"  {t}: {c}")
    print("\n=== total_entries_by_strategy ===")
    for s, c in Counter(e.strategy for e in entries).most_common():
        print(f"  {s}: {c}")
    print("\n=== total_entries_by_drop_phase ===")
    for p, c in Counter(e.drop_phase for e in entries).most_common():
        print(f"  {p}: {c}")
    print("\n=== total_entries_by_owner ===")
    for o, c in Counter(e.current_owner for e in entries).most_common():
        print(f"  {o}: {c}")
    print(f"\n=== phase4_carrier: {sum(1 for e in entries if e.phase4_carrier)} ===")
    print(f"=== pending-review: {sum(1 for e in entries if e.classification_status == 'pending-review')} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
