#!/usr/bin/env bash
# Architecture guardrails — 将当前架构不变量映射为可执行扫描。
# 用法: scripts/architecture-guardrails.sh --mode warn|enforced|expiry-check
#
# warn: warn-only (退出码 0, 打印违规)
# enforced: allowlist 之外违规退出码 1
# expiry-check: enforced + 过期 allowlist 退出码 1
#
# 规则:
#   WMS_INTEGRATION_BOUNDARY             内部域不得 import WMS DTO/client/provider
#   EXECUTION_CORRELATION_BOUNDARY       跨域 session FK 收敛为 ExecutionCorrelation
#   AUTHORITY_METADATA_BOUNDARY          查询响应强制 scope/authority/source/evidence_at
#   DEVICE_COMMAND_BOUNDARY              DeviceCommand 不含 PLC/坐标/关节/安全回路字段
#   RUNTIME_INBOX_STATE_MACHINE          RuntimeInbox 状态机契约 (tests/architecture/ 覆盖)
#   CAPABILITY_FORBIDDEN_DEPENDENCY      capability 注入禁用关键词
#   CAPABILITY_IMPLEMENTATION_IMPORT     capability 不得 import wms_integration/device services/models
#   INBOUND_NORMALIZER_OWNERSHIP         capability 不得持有 inbound normalizer (WmsEventPort 等)
set -euo pipefail

GUARDRAIL_MODE=""
ALLOWLIST="scripts/architecture-guardrails.allowlist"
REPO_ROOT=""
RULE_WMS_INTEGRATION_BOUNDARY="WMS_INTEGRATION_BOUNDARY"
RULE_EXECUTION_CORRELATION_BOUNDARY="EXECUTION_CORRELATION_BOUNDARY"
RULE_AUTHORITY_METADATA_BOUNDARY="AUTHORITY_METADATA_BOUNDARY"
RULE_DEVICE_COMMAND_BOUNDARY="DEVICE_COMMAND_BOUNDARY"
RULE_RUNTIME_INBOX_STATE_MACHINE="RUNTIME_INBOX_STATE_MACHINE"
RULE_CAPABILITY_FORBIDDEN_DEPENDENCY="CAPABILITY_FORBIDDEN_DEPENDENCY"
RULE_CAPABILITY_IMPLEMENTATION_IMPORT="CAPABILITY_IMPLEMENTATION_IMPORT"
RULE_INBOUND_NORMALIZER_OWNERSHIP="INBOUND_NORMALIZER_OWNERSHIP"
RULE_LEGACY_RUNTIME_IMPORT="LEGACY_RUNTIME_IMPORT"

usage() {
    cat <<'EOF'
Usage: scripts/architecture-guardrails.sh --mode warn|enforced|expiry-check [--allowlist PATH]

  --mode       warn=warn-only, enforced=allowlist enforced, expiry-check=expired allowlist fails
  --allowlist  allowlist 文件路径 (默认 scripts/architecture-guardrails.allowlist)

规则: WMS_INTEGRATION_BOUNDARY EXECUTION_CORRELATION_BOUNDARY AUTHORITY_METADATA_BOUNDARY DEVICE_COMMAND_BOUNDARY RUNTIME_INBOX_STATE_MACHINE CAPABILITY_FORBIDDEN_DEPENDENCY CAPABILITY_IMPLEMENTATION_IMPORT INBOUND_NORMALIZER_OWNERSHIP LEGACY_RUNTIME_IMPORT
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode) GUARDRAIL_MODE="$2"; shift 2 ;;
        --allowlist) ALLOWLIST="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数: $1" >&2; usage; exit 2 ;;
    esac
done

if [[ -z "$GUARDRAIL_MODE" ]]; then
    usage
    exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VIOLATIONS=0
WARNINGS=0
ALLOWLIST_KEYS=""
TODAY="${ARCHITECTURE_GUARDRAILS_TODAY:-$(date +%F)}"

# allowlist 读取: 缓存为 "rule:path" 换行分隔的字符串
load_allowlist() {
    ALLOWLIST_KEYS=""
    if [[ ! -f "$ALLOWLIST" ]]; then
        return
    fi
    # 格式: rule_id|path|reason|expires_at|legacy_entry_id|drop_phase
    ALLOWLIST_KEYS="$(awk -F'|' 'NF>=2 && $1 !~ /^#/ {print $1":"$2}' "$ALLOWLIST" 2>/dev/null || true)"
}

is_allowlisted() {
    local rule="$1" path="$2"
    local key="$rule:$path"
    [[ -z "$ALLOWLIST_KEYS" ]] && return 1
    # 精确匹配
    if printf '%s\n' "$ALLOWLIST_KEYS" | grep -qxF "$key"; then
        return 0
    fi
    # capability implementation import / inbound normalizer ownership 只能逐文件枚举，不能靠目录前缀覆盖未来违规。
    if [[ "$rule" == "$RULE_CAPABILITY_IMPLEMENTATION_IMPORT" || "$rule" == "$RULE_INBOUND_NORMALIZER_OWNERSHIP" ]]; then
        return 1
    fi
    # 前缀匹配 (allowlist path 可为目录前缀)
    local k
    for k in $ALLOWLIST_KEYS; do
        if [[ "$k" == "$rule:"* && "$key" == "$k"* ]]; then
            return 0
        fi
    done
    return 1
}

emit_violation() {
    local rule="$1" file="$2" line="$3" reason="$4" fix="$5"
    if is_allowlisted "$rule" "$file"; then
        echo "[ALLOWED] $rule $file:$line — $reason (allowlist 覆盖)" >&2
        return 0
    fi
    echo "[$rule] $rule violation" >&2
    echo "  file: $file:$line" >&2
    echo "  reason: $reason" >&2
    echo "  fix: $fix" >&2
    echo "" >&2
    VIOLATIONS=$((VIOLATIONS + 1))
}

run_python() {
    if command -v uv >/dev/null 2>&1; then
        uv run python "$@"
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 "$@"
        return
    fi
    if command -v python >/dev/null 2>&1 && python -c 'import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)' >/dev/null 2>&1; then
        python "$@"
        return
    fi
    echo "[ALLOWLIST] 未找到 Python 3 解释器，无法解析 legacy-cleanup-matrix.csv" >&2
    return 127
}

matrix_drop_marker_for_entry() {
    local legacy_entry_id="$1"
    [[ -z "$legacy_entry_id" || ! -f docs/architecture/legacy-cleanup-matrix.csv ]] && return 1
    run_python - "$legacy_entry_id" <<'PY'
import csv
import sys

legacy_entry_id = sys.argv[1]
with open("docs/architecture/legacy-cleanup-matrix.csv", newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        if row["entry_id"] == legacy_entry_id:
            print(row["drop_phase"])
            sys.exit(0)
sys.exit(1)
PY
}

is_valid_date() {
    local value="$1"
    local parsed=""
    [[ "$value" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || return 1
    if parsed="$(date -j -f "%Y-%m-%d" "$value" "+%F" 2>/dev/null)"; then
        [[ "$parsed" == "$value" ]]
        return
    fi
    parsed="$(date -d "$value" "+%F" 2>/dev/null)" || return 1
    [[ "$parsed" == "$value" ]]
}

# --- WMS_INTEGRATION_BOUNDARY: 内部域不得 import WMS DTO/client/provider ---
rule_wms_integration_boundary() {
    local pattern='from src\.app\.wms_integration\.(services|models|clients|providers).* import|import src\.app\.wms_integration\.(services|models|clients|providers)'
    while IFS=: read -r file line _content; do
        [[ -z "$file" ]] && continue
        # 排除 wms_integration 自身
        [[ "$file" == src/app/wms_integration/* ]] && continue
        emit_violation "$RULE_WMS_INTEGRATION_BOUNDARY" "$file" "$line" \
            "内部域 import WMS implementation/DTO/client/provider" \
            "依赖 WmsMasterDataPort contract 而非 wms_integration 实现"
    done < <(grep -rnE "$pattern" src/app --include='*.py' 2>/dev/null || true)
}

# --- EXECUTION_CORRELATION_BOUNDARY: 跨域 session FK (workline_session_id / material_session_id) ---
rule_execution_correlation_boundary() {
    local pattern='workline_session_id|material_session_id'
    while IFS=: read -r file line _content; do
        [[ -z "$file" ]] && continue
        # 允许 runtime/orchestration 内部
        [[ "$file" == src/app/runtime/orchestration/* ]] && continue
        emit_violation "$RULE_EXECUTION_CORRELATION_BOUNDARY" "$file" "$line" \
            "跨域 session FK 未收敛为 ExecutionCorrelation.correlation_id" \
            "改为 correlation_id 引用, 详见 session-correlation-matrix.md"
    done < <(grep -rnE "$pattern" src/app --include='*.py' 2>/dev/null || true)
}

# --- AUTHORITY_METADATA_BOUNDARY: 查询响应缺 authority metadata (schema 测试覆盖, 脚本只做静态提示) ---
rule_authority_metadata_boundary() {
    # authority metadata boundary 主要由 tests/architecture/test_authority_metadata_boundary_guardrail.py 覆盖
    # 脚本扫描 AuthorityMetadata 使用点, 确保存在校验
    if ! grep -rn "validate_authority_metadata\|AuthorityMetadata" tests/ src/app/wms_integration --include='*.py' -l 2>/dev/null | grep -q .; then
        echo "[$RULE_AUTHORITY_METADATA_BOUNDARY] warning: 未发现 AuthorityMetadata 校验点 (由 tests/architecture 覆盖)" >&2
        WARNINGS=$((WARNINGS + 1))
    fi
}

# --- DEVICE_COMMAND_BOUNDARY: DeviceCommand/manifest/runtime 不含禁止字段 ---
# 只匹配 "字段声明" 语义 (Pydantic 风格):
#   plc_address: str = Field(...)        <- catch
#   coordinate: float = ...              <- catch
# 不匹配:
#   "plc",                               <- ignore (黑名单字面量, device command 反注入实现)
#   plc_address / coordinate 等禁止字段   <- ignore (docstring/注释)
#   if key in _FORBIDDEN_PARAM_KEYS:     <- ignore (变量引用)
rule_device_command_boundary() {
    local pattern='^[[:space:]]+(plc|coordinate|joint_angle|x_coord|y_coord|safety_loop)[a-z_]*[[:space:]]*[:=]'
    while IFS=: read -r file line _content; do
        [[ -z "$file" ]] && continue
        emit_violation "$RULE_DEVICE_COMMAND_BOUNDARY" "$file" "$line" \
            "DeviceCommand/manifest/runtime 出现禁止字段 (PLC/坐标/关节/安全回路)" \
            "WES 不与 PLC 通讯, 不下发坐标/关节/安全回路指令"
    done < <(grep -rnE "$pattern" src/app/device src/app/workline src/app/runtime --include='*.py' 2>/dev/null || true)
}

# --- CAPABILITY_FORBIDDEN_DEPENDENCY: capability 注入禁用关键词 ---
rule_capability_forbidden_dependency() {
    local pattern='http_client|service_locator|WmsClientException|DeviceClientException'
    while IFS=: read -r file line _content; do
        [[ -z "$file" ]] && continue
        emit_violation "$RULE_CAPABILITY_FORBIDDEN_DEPENDENCY" "$file" "$line" \
            "capability 注入禁用对象 (HTTP client/service locator/provider exception/DTO)" \
            "capability 只能依赖 port contract"
    done < <(grep -rnE "$pattern" src/app/runtime src/app/workline --include='*.py' 2>/dev/null || true)
}

# --- LEGACY_RUNTIME_IMPORT: src.workline_runtime production import 严格型 ---
# legacy runtime 整目录已删; 保留 rule 作为永久安全网防止回归:
# 任何 src/ 下 production code import src.workline_runtime 视为违规 (legacy runtime 已不存在)。
# 仅以下前缀允许 (legacy runtime 内部 import 自身 / 测试 / Alembic 迁移):
#   1. tests/     (测试)
#   2. migrations/  (Alembic 数据迁移)
rule_legacy_runtime_import() {
    local pattern='from src\.workline_runtime|import src\.workline_runtime'
    while IFS=: read -r file line _content; do
        [[ -z "$file" ]] && continue
        # 排除 legacy runtime 自身内部 import (历史允许,旧 runtime 入口删除后目录已删,此分支防御性保留)
        [[ "$file" == src/workline_runtime/* ]] && continue
        # 排除测试 + 迁移 (allowlist 前缀覆盖)
        [[ "$file" == tests/* ]] && continue
        [[ "$file" == migrations/* ]] && continue
        emit_violation "$RULE_LEGACY_RUNTIME_IMPORT" "$file" "$line" \
            "production code import src.workline_runtime (legacy runtime import boundary 违规)" \
            "src/workline_runtime/ 整目录已删,不可直接 import; 改用 src.app.runtime.orchestration 或 src.app.workline 域内 mirror"
    done < <(grep -rnE "$pattern" src --include='*.py' 2>/dev/null || true)
}

# --- CAPABILITY_IMPLEMENTATION_IMPORT: capability 不得 import wms_integration/device services/models ---
rule_capability_implementation_import() {
    local pattern='from src\.app\.(wms_integration|device)\.(services|models)\..* import'
    while IFS=: read -r file line _content; do
        [[ -z "$file" ]] && continue
        emit_violation "$RULE_CAPABILITY_IMPLEMENTATION_IMPORT" "$file" "$line" \
            "capability import 了 wms_integration/device 的 services/models 实现" \
            "依赖 port contract, 不依赖实现对象"
    done < <(grep -rnE "$pattern" src/app/runtime src/app/workline --include='*.py' 2>/dev/null || true)
}

# --- INBOUND_NORMALIZER_OWNERSHIP: capability 不得持有 inbound normalizer 类型 ---
rule_inbound_normalizer_ownership() {
    while IFS=$'\t' read -r file line reason; do
        [[ -z "$file" ]] && continue
        emit_violation "$RULE_INBOUND_NORMALIZER_OWNERSHIP" "$file" "$line" \
            "$reason" \
            "inbound normalizer 仅允许专用 normalization wiring 持有; capability 走 query/effect port contract"
    done < <(run_python - <<'PY'
import ast
import re
from pathlib import Path

SCAN_ROOTS = (
    Path("src/app/runtime"),
    Path("src/app/workline"),
    Path("src/app/callback"),
    Path("src/app/wms_integration/services"),
    Path("src/app/device"),
)
FORBIDDEN_NAMES = frozenset(
    {
        "WmsEventPort",
        "DeviceEventPort",
        "InboundEventPort",
        "RuntimeInbox",
    }
)
FORBIDDEN_IMPORT_MODULES = frozenset(
    {
        "src.app.wms_integration.ports.event",
        "src.app.device.ports.event",
        "src.app.runtime.inbound_normalizer_registry",
        "src.app.runtime.orchestration.runtime_inbox",
    }
)
FORBIDDEN_IMPORT_MEMBERS = {
    "src.app.wms_integration.ports": frozenset(("event",)),
    "src.app.device.ports": frozenset(("event",)),
}
EXCLUDED_FILES = frozenset(
    {
        "src/app/wms_integration/ports/event.py",
        "src/app/wms_integration/ports/__init__.py",
        "src/app/wms_integration/services/wms_event_normalizer.py",
        "src/app/runtime/inbound_normalizer_registry.py",
        "src/app/runtime/orchestration/__init__.py",
        "src/app/runtime/orchestration/runtime_inbox.py",
        "src/app/contracts/external_contract_profile.py",
        # RuntimeInbox claim/write-back 是 runtime 域内表 repository,
        # 不是 inbound normalizer interface. Plan Task 3 主计划 §3 锁定.
        "src/app/runtime/orchestration/repositories/runtime_inbox_claim_repository.py",
        "src/app/runtime/orchestration/repositories/runtime_inbox_repository.py",
        "src/app/runtime/orchestration/consumers/runtime_inbox_repository.py",
        "src/app/runtime/orchestration/consumers/runtime_inbox_service.py",
        # Task 5 三阶段 Processor 拆分: validation / orchestrator-delegate /
        # writeback / composition 全部驻留在 runtime_inbox/ 目录, 是
        # RuntimeInbox 主链路收束的 processor 实现, 不是 inbound normalizer
        # capability. 锁定 plan Task 5.
        "src/app/runtime/orchestration/services/runtime_inbox/__init__.py",
        "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_validation_service.py",
        "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_processor_service.py",
        "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_writeback_service.py",
        "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_orchestrator_bridge.py",
        # OrchestratorService 是 RuntimeInbox 主链路收束的 orchestration 入口,
        # process_inbox 签名从 WorklineInbox | None 过渡为 WorklineInbox | RuntimeInbox | None,
        # 必须显式接收两者. 不是 inbound normalizer capability. 锁定 plan Task 5.
        "src/app/runtime/orchestration/orchestrator_bridge.py",
    }
)
EXCLUDED_PREFIXES = ()

NAME_PATTERN = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(sorted(FORBIDDEN_NAMES)) + r")(?![A-Za-z0-9_])")


def is_excluded(path: Path) -> bool:
    rel = path.as_posix()
    return rel in EXCLUDED_FILES or any(rel.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def module_is_forbidden(module: str) -> bool:
    return module in FORBIDDEN_IMPORT_MODULES or any(
        module.startswith(f"{forbidden}.") for forbidden in FORBIDDEN_IMPORT_MODULES
    )


def annotation_mentions_forbidden(annotation: ast.AST | None) -> bool:
    if annotation is None:
        return False
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and NAME_PATTERN.search(node.value):
            return True
    return False


def value_root_name(node: ast.AST) -> str | None:
    current = node
    while isinstance(current, ast.Attribute):
        current = current.value
    if isinstance(current, ast.Name):
        return current.id
    return None


def visit_file(path: Path) -> None:
    rel = path.as_posix()
    if is_excluded(path):
        return
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except SyntaxError as exc:
        line = exc.lineno or 1
        print(f"{rel}\t{line}\tinbound normalizer ownership boundary 无法解析 Python AST, 静态边界无法确认")
        return

    forbidden_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported_names = {alias.name for alias in node.names}
            forbidden_imported_names = imported_names.intersection(FORBIDDEN_NAMES)
            if module_is_forbidden(module):
                forbidden_aliases.update(alias.asname or alias.name for alias in node.names if alias.name not in {"*"})
                print(f"{rel}\t{node.lineno}\t业务 capability import inbound normalizer 模块 (主计划 §3.5.1 + H2 黑名单)")
            elif module in FORBIDDEN_IMPORT_MEMBERS and imported_names.intersection(FORBIDDEN_IMPORT_MEMBERS[module]):
                forbidden_aliases.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name in FORBIDDEN_IMPORT_MEMBERS[module]
                )
                print(f"{rel}\t{node.lineno}\t业务 capability import inbound normalizer 模块 (主计划 §3.5.1 + H2 黑名单)")
            elif forbidden_imported_names:
                print(f"{rel}\t{node.lineno}\t业务 capability import inbound normalizer 类型或模块 (主计划 §3.5.1 + H2 黑名单)")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if module_is_forbidden(alias.name):
                    forbidden_aliases.add(alias.asname or alias.name.split(".", maxsplit=1)[0])
                    print(f"{rel}\t{node.lineno}\t业务 capability import inbound normalizer 模块 (主计划 §3.5.1 + H2 黑名单)")
                    break
        elif isinstance(node, ast.AnnAssign):
            if annotation_mentions_forbidden(node.annotation):
                print(f"{rel}\t{node.lineno}\t业务 capability 持有 inbound normalizer type hint (主计划 §3.5.1 + H2 黑名单)")
        elif isinstance(node, ast.arg):
            if annotation_mentions_forbidden(node.annotation):
                print(f"{rel}\t{node.lineno}\t业务 capability 参数暴露 inbound normalizer type hint (主计划 §3.5.1 + H2 黑名单)")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if annotation_mentions_forbidden(node.returns):
                print(f"{rel}\t{node.lineno}\t业务 capability 返回 inbound normalizer type hint (主计划 §3.5.1 + H2 黑名单)")
        elif isinstance(node, ast.Attribute):
            if value_root_name(node) in forbidden_aliases and node.attr in FORBIDDEN_NAMES:
                print(f"{rel}\t{node.lineno}\t业务 capability 引用 inbound normalizer 类型 (主计划 §3.5.1 + H2 黑名单)")


for root in SCAN_ROOTS:
    if not root.exists():
        continue
    for path in root.rglob("*.py"):
        visit_file(path)
PY
    )
}

# --- allowlist 校验 ---
validate_allowlist() {
    if [[ ! -f "$ALLOWLIST" ]]; then
        echo "[ALLOWLIST] $ALLOWLIST 不存在 (warn mode 允许无 allowlist)" >&2
        return 0
    fi
    local lineno=0
    while IFS= read -r row; do
        lineno=$((lineno + 1))
        [[ "$row" =~ ^# ]] && continue
        [[ -z "$row" ]] && continue
        IFS='|' read -r rule_id path reason expires_at legacy_entry_id drop_phase <<<"$row"
        if [[ -z "$rule_id" || -z "$path" ]]; then
            echo "[ALLOWLIST] 行 $lineno: 缺 rule_id 或 path" >&2
            VIOLATIONS=$((VIOLATIONS + 1))
            continue
        fi
        if [[ -z "$legacy_entry_id" ]]; then
            echo "[ALLOWLIST] 行 $lineno ($rule_id $path): 缺 legacy_entry_id" >&2
            VIOLATIONS=$((VIOLATIONS + 1))
        fi
        if [[ -z "$drop_phase" ]]; then
            echo "[ALLOWLIST] 行 $lineno ($rule_id $path): 缺 drop_phase" >&2
            VIOLATIONS=$((VIOLATIONS + 1))
        fi
        if [[ ( "$rule_id" == "$RULE_CAPABILITY_IMPLEMENTATION_IMPORT" || "$rule_id" == "$RULE_INBOUND_NORMALIZER_OWNERSHIP" ) && ( "$path" == */ || -d "$path" ) ]]; then
            echo "[ALLOWLIST] 行 $lineno ($rule_id $path): $rule_id 必须逐文件枚举, 禁止目录前缀" >&2
            VIOLATIONS=$((VIOLATIONS + 1))
        fi
        if [[ -z "$expires_at" ]]; then
            echo "[ALLOWLIST] 行 $lineno ($rule_id $path): 缺 expires_at (无过期视为失败)" >&2
            VIOLATIONS=$((VIOLATIONS + 1))
        elif ! is_valid_date "$expires_at"; then
            echo "[ALLOWLIST] 行 $lineno ($rule_id $path): expires_at 日期无效 '$expires_at'" >&2
            VIOLATIONS=$((VIOLATIONS + 1))
        elif [[ "$expires_at" < "$TODAY" ]]; then
            if [[ "$GUARDRAIL_MODE" == "expiry-check" ]]; then
                echo "[ALLOWLIST] 行 $lineno ($rule_id $path): allowlist 已过期 $expires_at" >&2
                VIOLATIONS=$((VIOLATIONS + 1))
            else
                echo "[ALLOWLIST] 行 $lineno ($rule_id $path): allowlist 已过期 $expires_at (enforced mode warning)" >&2
                WARNINGS=$((WARNINGS + 1))
            fi
        fi
        # legacy_entry_id 必须能在 legacy-cleanup-matrix.csv 找到
        # 例外: legacy runtime import 的 legacy_entry_id 是导入点自描述,
        #       指向"反向 import src.workline_runtime 的文件"本身,不属于迁移对象矩阵。
        if [[ -n "$legacy_entry_id" && -f docs/architecture/legacy-cleanup-matrix.csv && "$rule_id" != "$RULE_LEGACY_RUNTIME_IMPORT" ]]; then
            matrix_drop_phase="$(matrix_drop_marker_for_entry "$legacy_entry_id" || true)"
            if [[ -z "$matrix_drop_phase" ]]; then
                echo "[ALLOWLIST] 行 $lineno ($rule_id $path): legacy_entry_id 精确匹配失败 '$legacy_entry_id'" >&2
                VIOLATIONS=$((VIOLATIONS + 1))
            elif [[ -n "$drop_phase" && "$drop_phase" != "$matrix_drop_phase" ]]; then
                echo "[ALLOWLIST] 行 $lineno ($rule_id $path): drop_phase 不一致 allowlist=$drop_phase matrix=$matrix_drop_phase" >&2
                VIOLATIONS=$((VIOLATIONS + 1))
            fi
        fi
    done < "$ALLOWLIST"
}

echo "=== Architecture Guardrails (mode=$GUARDRAIL_MODE) ===" >&2

load_allowlist
rule_wms_integration_boundary
rule_execution_correlation_boundary
rule_authority_metadata_boundary
rule_device_command_boundary
rule_capability_forbidden_dependency
rule_legacy_runtime_import
rule_capability_implementation_import
rule_inbound_normalizer_ownership

if [[ "$GUARDRAIL_MODE" != "warn" ]]; then
    validate_allowlist
fi

echo "" >&2
echo "=== 汇总 ===" >&2
echo "violations: $VIOLATIONS" >&2
echo "warnings: $WARNINGS" >&2

case "$GUARDRAIL_MODE" in
    warn)
        echo "warn: warn-only (退出码 0)" >&2
        exit 0
        ;;
    enforced|expiry-check)
        if [[ $VIOLATIONS -gt 0 ]]; then
            echo "$GUARDRAIL_MODE: $VIOLATIONS 个违规未被 allowlist 覆盖, 退出码 1" >&2
            exit 1
        fi
        echo "$GUARDRAIL_MODE: 全部违规已被 allowlist 覆盖或无违规, 退出码 0" >&2
        exit 0
        ;;
    *)
        echo "未知 mode: $GUARDRAIL_MODE" >&2
        exit 2
        ;;
esac
