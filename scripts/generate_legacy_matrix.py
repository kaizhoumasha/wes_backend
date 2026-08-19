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
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
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
    "src/app/workline/services/bin_cell_reservation_service.py": (
        "src/app/runtime/capabilities/material_flow/bin_cell_reservation_service.py"
    ),
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
    "src/app/workline/services/station_lease_service.py": (
        "src/app/runtime/capabilities/material_flow/station_lease_service.py"
    ),
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
    "src/app/workline/services/bin_cell_reservation_service.py": (
        "BinCellReservationResult",
        "BinCellReservationStatusCode",
        "WorklineBinCellReservationService",
    ),
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
    "src/app/workline/services/station_lease_service.py": (
        "StationLeaseReasonCode",
        "StationLeaseResult",
        "WorklineStationLeaseService",
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
        ("src/app/workline/services/workline_start_service.py", "RuntimeHoldRepositoryPort"),
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
