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

# 扫描目标
SCAN_DIRS = {
    "src/app/workline": "workline",
    "src/workline_runtime": "workline_runtime",
    "src/workline_plugins": "workline_plugins",
    "tests/workline_runtime": "workline_runtime",
    "tests/workline_plugins": "workline_plugins",
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
    "src/app/workline/services/inbox_batch_processor.py": (
        "src/app/runtime/orchestration/services/inbox/inbox_batch_processor.py"
    ),
    "src/app/workline/services/inbox_service.py": "src/app/runtime/orchestration/services/inbox/inbox_service.py",
    "src/app/workline/services/ng_return_item_service.py": (
        "src/app/runtime/capabilities/material_flow/ng_return_item_service.py"
    ),
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
    "src/app/workline/services/single_layer_rack_orchestration_service.py": (
        "src/app/runtime/capabilities/material_flow/single_layer_rack_orchestration_service.py"
    ),
    "src/app/workline/services/smt_inbound_handoff_service.py": (
        "src/app/runtime/orchestration/services/intent/smt_inbound_handoff_service.py"
    ),
    "src/app/workline/services/start_admission_service.py": (
        "src/app/runtime/capabilities/material_flow/start_admission_service.py"
    ),
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

# Business legacy cleanup 会把旧 WorkLine domain 业务合同迁入
# runtime/capabilities/material_flow/contracts。matrix 必须继续按 legacy entry_id 记账,
# 否则文件删除后 audit trace 会误以为业务承载项已经消失。
MIGRATED_DOMAIN_IMPLS = {
    "src/app/workline/domain/contexts/rough_sorter.py": (
        "src/app/runtime/capabilities/material_flow/contracts/rough_sorter_context.py"
    ),
    "src/app/workline/domain/contexts/smt_sorting_inbound.py": (
        "src/app/runtime/capabilities/material_flow/contracts/sorting_inbound_context.py"
    ),
    "src/app/workline/domain/contracts/rough_sorter.py": "src/app/runtime/capabilities/material_flow/contracts/rough_sorter.py",
    "src/app/workline/domain/contracts/six_in_one.py": "src/app/runtime/capabilities/material_flow/contracts/six_in_one.py",
    "src/app/workline/domain/material_identity.py": "src/app/runtime/capabilities/material_flow/contracts/material_identity.py",
    "src/app/workline/domain/ng_reason.py": "src/app/runtime/capabilities/material_flow/contracts/ng_reason.py",
    "src/app/workline/domain/services/smt_inbound_handoff_reason.py": (
        "src/app/runtime/capabilities/material_flow/contracts/smt_inbound_handoff_reason.py"
    ),
    "src/app/workline/domain/services/smt_inbound_handoff_route_service.py": (
        "src/app/runtime/capabilities/material_flow/smt_inbound_handoff_route_service.py"
    ),
    "src/app/workline/domain/services/smt_usage_policy.py": (
        "src/app/runtime/capabilities/material_flow/contracts/smt_usage_policy.py"
    ),
}

MIGRATED_TEST_IMPLS = {
    "tests/workline_plugins/test_barcode_decision_service.py": (
        "tests/contracts/workline/test_barcode_decision_contract.py"
    ),
    "tests/workline_plugins/test_rough_sorter_contract.py": (
        "tests/contracts/workline/test_rough_sorter_inbound_contract.py"
    ),
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
    "src/app/workline/repositories/smt_inbound_handoff_repository.py": (
        "src/app/runtime/orchestration/repositories/smt_inbound_handoff_repository.py"
    ),
}
MIGRATED_REPOSITORIES_TO_LEGACY = {impl: legacy for legacy, impl in MIGRATED_REPOSITORIES.items()}

SHIM_INTERNAL_SYMBOLS = {
    ("src/app/workline/services/__init__.py", "__getattr__"),
    ("src/app/workline/services/inbox_batch_processor.py", "_load_target_module"),
}

GUARDRAIL_SEED_SYMBOLS = {
    "src/workline_runtime/services.py": "build_workline_runtime_services",
}

# 业务语义关键词 → business_semantics + phase4 标记
# 顺序敏感：先匹配更具体的类别，再匹配通用类别
BUSINESS_SEMANTICS_RULES = [
    # 旧 plugin 框架（优先于 runtime，避免路径含 runtime 误判）
    (
        r"plugin_base|plugin_context|plugin_manifest|plugin_sdk|plugin_next|null_plugin|"
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
    """运行 git grep，返回匹配行列表。"""
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

    try:
        result = subprocess.run(  # noqa: S603
            ["git", "grep", "-n", "-E", pattern, "--", *paths],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return [ln for ln in result.stdout.splitlines() if ln.strip()]
    except Exception:
        pass
    return python_grep()


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
        (("start_admission", "admission"), "StartAdmissionCapability.evaluate"),
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
        return "src/app/runtime/orchestration/ports/wms_fulfillment.py", "WmsFulfillmentPort.request_transport"
    if "import device" in text or "device." in text or "device_" in text or "device command" in text:
        return "src/app/runtime/orchestration/ports/device_command.py", "DeviceCommandPort.dispatch"
    if "执行状态" in business_semantics:
        return _target_runtime_capability(entry_type, path, text)
    if "[phase4]" in business_semantics:
        return _target_phase4_capability(text)
    return _target_runtime_path(entry_type, path), "RuntimeOrchestrationService.execute"


def resolve_blocking_tests(business_semantics: str, entry_type: str, path: str, strategy: str) -> str:
    """返回删除/迁移前必须通过的测试清单，CSV 用分号分隔。"""
    text = f"{business_semantics} {path}".lower()
    if entry_type == "test":
        return path
    rules = [
        (
            ("rough_sorter", "粗分机"),
            "tests/characterization/workline_legacy/test_business_semantics_characterization.py;"
            "tests/contracts/workline/test_rough_sorter_inbound_contract.py",
        ),
        (
            ("full_box", "满箱"),
            "tests/characterization/workline_legacy/test_business_semantics_characterization.py;"
            "tests/contracts/workline/test_full_box_exchange_contract.py",
        ),
        (
            ("sorter_inbound", "smt_sorting", "分拣机"),
            "tests/characterization/workline_legacy/test_business_semantics_characterization.py",
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
            "tests/contracts/workline/test_start_admission_contract.py;"
            "tests/workline_runtime/test_plugin_manifest_and_topology.py",
        ),
        (("技术残留",), "tests/characterization/workline_legacy/test_business_semantics_characterization.py"),
    ]
    default = (
        "tests/characterization/workline_legacy/test_business_semantics_characterization.py"
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


def _defined_test_symbols_from_python(path: Path) -> list[str]:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []

    return [
        node.name
        for node in module.body
        if (
            (isinstance(node, ast.ClassDef) and node.name.startswith("Test"))
            or (isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_"))
        )
    ]


def _add_migrated_service_entries(add: Callable[[str, str, str, str], None]) -> None:
    for legacy_path, impl_path in MIGRATED_SERVICE_IMPLS.items():
        for symbol in _defined_symbols_from_python(REPO_ROOT / impl_path):
            add(legacy_path, symbol, "service", "workline")


def _add_migrated_domain_entries(add: Callable[[str, str, str, str], None]) -> None:
    for legacy_path, impl_path in MIGRATED_DOMAIN_IMPLS.items():
        for symbol in _defined_symbols_from_python(REPO_ROOT / impl_path):
            add(legacy_path, symbol, "domain_object", "workline")


def _add_migrated_test_entries(add: Callable[[str, str, str, str], None]) -> None:
    for legacy_path, impl_path in MIGRATED_TEST_IMPLS.items():
        for symbol in _defined_test_symbols_from_python(REPO_ROOT / impl_path):
            add(legacy_path, symbol, "test", "workline_plugins")


def _add_guardrail_seed_entries(entries: list[Entry], seen: set[str], seed_paths: list[SeedPath]) -> None:
    for path, owner, etype, bs, phase, risk in seed_paths:
        sym = GUARDRAIL_SEED_SYMBOLS.get(path)
        if sym is None:
            sym = (
                "<file>#CAPABILITY_IMPLEMENTATION_IMPORT" if "CAPABILITY_IMPLEMENTATION_IMPORT seed" in bs else "<file>"
            )
        eid = f"legacy:{path}:{sym}"
        if eid in seen:
            continue
        seen.add(eid)
        target_path, target_capability = resolve_migration_target(bs, etype, path, sym, "rebuild")
        blocking_tests = resolve_blocking_tests(bs, etype, path, "rebuild")
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
                strategy="rebuild",
                target_path=target_path,
                target_capability=target_capability,
                blocking_tests=blocking_tests,
                drop_phase=phase,
                risk=risk,
                notes="guardrail_seed_scope",
            )
        )


def parse_entries() -> list[Entry]:
    entries: list[Entry] = []
    seen: set[str] = set()

    def add(path: str, symbol: str, entry_type: str, owner: str):
        rel = str(Path(path).relative_to(REPO_ROOT)) if Path(path).is_absolute() else path
        sym = symbol or "<file>"
        eid = f"legacy:{rel}:{sym}"
        if eid in seen or (rel, sym) in SHIM_INTERNAL_SYMBOLS:
            return
        seen.add(eid)
        bs, p4 = classify_business_semantics(sym, rel)
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

    # 7. tests
    for line in git_grep(
        r"^class Test|^def test_|^async def test_", ["tests/workline_runtime", "tests/workline_plugins"]
    ):
        m = re.match(r"([^:]+):(\d+):(?:class |def |async def )([A-Za-z_][A-Za-z0-9_]*)", line)
        if m:
            path = m.group(1)
            owner = "workline_plugins" if "workline_plugins" in path else "workline_runtime"
            add(path, m.group(3), "test", owner)

    # 7b. 旧 plugin contract tests 迁到 target contracts 后仍按 legacy test path
    # 记账,避免移动测试导致 cleanup matrix 误删审计行。
    _add_migrated_test_entries(add)

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
            "src/app/rack/services/gateway.py",
            "rack",
            "service",
            "跨域 WMS import (WMS_INTEGRATION_BOUNDARY seed)",
            "phase2",
            "MEDIUM",
        ),
        (
            "src/app/handling/services/gateway.py",
            "handling",
            "service",
            "跨域 WMS import (WMS_INTEGRATION_BOUNDARY seed)",
            "phase2",
            "MEDIUM",
        ),
        (
            "src/app/workline/services/single_layer_rack_orchestration_service.py",
            "workline",
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
            "src/app/handling/models/operation.py",
            "handling",
            "model",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase1",
            "MEDIUM",
        ),
        (
            "src/app/handling/services/lifecycle_service.py",
            "handling",
            "service",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase1",
            "MEDIUM",
        ),
        (
            "src/app/handling/services/operation_service.py",
            "handling",
            "service",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase1",
            "MEDIUM",
        ),
        (
            "src/app/rack/models/operation.py",
            "rack",
            "model",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase2",
            "HIGH",
        ),
        (
            "src/app/rack/repositories/operation_repository.py",
            "rack",
            "repository",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase2",
            "MEDIUM",
        ),
        (
            "src/app/rack/services/operation_service.py",
            "rack",
            "service",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase2",
            "MEDIUM",
        ),
        (
            "src/app/rack/services/task_lifecycle_service.py",
            "rack",
            "service",
            "跨域 session FK (EXECUTION_CORRELATION_BOUNDARY seed)",
            "phase2",
            "MEDIUM",
        ),
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
