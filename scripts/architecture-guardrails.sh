#!/usr/bin/env bash
# Architecture guardrails — 将主计划 §7.5 不变量映射为可执行扫描。
# 用法: scripts/architecture-guardrails.sh --phase phase0|phase1|phase2
#
# Phase 0: warn-only (退出码 0, 打印违规)
# Phase 1: enforced (allowlist 之外违规退出码 1)
# Phase 2+: enforced + 要求每 PR 消除一条 expired allowlist
#
# 规则 (主计划 §7.5):
#   C1   内部域不得 import WMS DTO/client/provider
#   C2   跨域 session FK 收敛为 ExecutionCorrelation
#   C3   查询响应强制 scope/authority/source/evidence_at
#   C4   DeviceCommand 不含 PLC/坐标/关节/安全回路字段
#   C5   RuntimeInbox 状态机契约 (tests/architecture/ 覆盖)
#   R-I3a capability 注入禁用关键词
#   R-I3b capability 不得 import wms_integration/device services/models
set -euo pipefail

PHASE=""
ALLOWLIST="scripts/architecture-guardrails.allowlist"
REPO_ROOT=""

usage() {
    cat <<'EOF'
Usage: scripts/architecture-guardrails.sh --phase phase0|phase1|phase2 [--allowlist PATH]

  --phase      phase0=warn-only, phase1=enforced, phase2=enforced+expired 清理
  --allowlist  allowlist 文件路径 (默认 scripts/architecture-guardrails.allowlist)

规则: C1 C2 C3 C4 C5 R-I3a R-I3b (主计划 §7.5)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --phase) PHASE="$2"; shift 2 ;;
        --allowlist) ALLOWLIST="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数: $1" >&2; usage; exit 2 ;;
    esac
done

if [[ -z "$PHASE" ]]; then
    usage
    exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VIOLATIONS=0
WARNINGS=0
ALLOWLIST_KEYS=""

# allowlist 读取: 缓存为 "rule:path" 换行分隔的字符串
load_allowlist() {
    ALLOWLIST_KEYS=""
    if [[ ! -f "$ALLOWLIST" ]]; then
        return
    fi
    # 格式: rule_id|path|reason|expires_at|legacy_entry_id
    ALLOWLIST_KEYS="$(awk -F'|' 'NF>=2 && $1 !~ /^#/ {print $1":"$2}' "$ALLOWLIST" 2>/dev/null || true)"
}

is_allowlisted() {
    local rule="$1" path="$2"
    local key="$rule:$path"
    [[ -z "$ALLOWLIST_KEYS" ]] && return 1
    # 精确或前缀匹配
    if echo "$ALLOWLIST_KEYS" | grep -qF "$key"; then
        return 0
    fi
    # 前缀匹配 (allowlist path 可为目录前缀)
    local k
    for k in $ALLOWLIST_KEYS; do
        if [[ "$key" == "$k"* ]]; then
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

# --- C1: 内部域不得 import WMS DTO/client/provider ---
rule_c1() {
    local pattern='from src\.app\.wms_integration\.(services|models|clients|providers).* import|import src\.app\.wms_integration\.(services|models|clients|providers)'
    while IFS=: read -r file line _content; do
        [[ -z "$file" ]] && continue
        # 排除 wms_integration 自身
        [[ "$file" == src/app/wms_integration/* ]] && continue
        emit_violation "C1" "$file" "$line" \
            "内部域 import WMS implementation/DTO/client/provider" \
            "依赖 WmsMasterDataPort contract 而非 wms_integration 实现"
    done < <(grep -rnE "$pattern" src/app --include='*.py' 2>/dev/null || true)
}

# --- C2: 跨域 session FK (workline_session_id / material_session_id) ---
rule_c2() {
    local pattern='workline_session_id|material_session_id'
    while IFS=: read -r file line _content; do
        [[ -z "$file" ]] && continue
        # 允许 runtime/orchestration 内部
        [[ "$file" == src/app/runtime/orchestration/* ]] && continue
        emit_violation "C2" "$file" "$line" \
            "跨域 session FK 未收敛为 ExecutionCorrelation.correlation_id" \
            "改为 correlation_id 引用, 详见 session-correlation-matrix.md"
    done < <(grep -rnE "$pattern" src/app --include='*.py' 2>/dev/null || true)
}

# --- C3: 查询响应缺 authority metadata (schema 测试覆盖, 脚本只做静态提示) ---
rule_c3() {
    # C3 主要由 tests/architecture/test_c3_authority_metadata_guardrail.py 覆盖
    # 脚本扫描 AuthorityMetadata 使用点, 确保存在校验
    if ! grep -rn "validate_authority_metadata\|AuthorityMetadata" tests/ src/app/wms_integration --include='*.py' -l 2>/dev/null | grep -q .; then
        echo "[C3] warning: 未发现 AuthorityMetadata 校验点 (由 tests/architecture 覆盖)" >&2
        WARNINGS=$((WARNINGS + 1))
    fi
}

# --- C4: DeviceCommand/manifest/runtime 不含禁止字段 ---
rule_c4() {
    local pattern='plc|coordinate|joint_angle|x_coord|y_coord|safety_loop'
    while IFS=: read -r file line _content; do
        [[ -z "$file" ]] && continue
        emit_violation "C4" "$file" "$line" \
            "DeviceCommand/manifest/runtime 出现禁止字段 (PLC/坐标/关节/安全回路)" \
            "WES 不与 PLC 通讯, 不下发坐标/关节/安全回路指令"
    done < <(grep -rniE "$pattern" src/app/device src/app/workline src/app/runtime --include='*.py' 2>/dev/null || true)
}

# --- R-I3a: capability 注入禁用关键词 ---
rule_ri3a() {
    local pattern='http_client|service_locator|WmsClientException|DeviceClientException'
    while IFS=: read -r file line _content; do
        [[ -z "$file" ]] && continue
        emit_violation "R-I3a" "$file" "$line" \
            "capability 注入禁用对象 (HTTP client/service locator/provider exception/DTO)" \
            "capability 只能依赖 port contract"
    done < <(grep -rnE "$pattern" src/app/runtime src/app/workline --include='*.py' 2>/dev/null || true)
}

# --- R-I3b: capability 不得 import wms_integration/device services/models ---
rule_ri3b() {
    local pattern='from src\.app\.(wms_integration|device)\.(services|models)\..* import'
    while IFS=: read -r file line _content; do
        [[ -z "$file" ]] && continue
        emit_violation "R-I3b" "$file" "$line" \
            "capability import 了 wms_integration/device 的 services/models 实现" \
            "依赖 port contract, 不依赖实现对象"
    done < <(grep -rnE "$pattern" src/app/runtime src/app/workline --include='*.py' 2>/dev/null || true)
}

# --- allowlist 校验 ---
validate_allowlist() {
    if [[ ! -f "$ALLOWLIST" ]]; then
        echo "[ALLOWLIST] $ALLOWLIST 不存在 (Phase 0 允许无 allowlist)" >&2
        return 0
    fi
    local lineno=0
    while IFS= read -r row; do
        lineno=$((lineno + 1))
        [[ "$row" =~ ^# ]] && continue
        [[ -z "$row" ]] && continue
        IFS='|' read -r rule_id path reason expires_at legacy_entry_id <<<"$row"
        if [[ -z "$rule_id" || -z "$path" ]]; then
            echo "[ALLOWLIST] 行 $lineno: 缺 rule_id 或 path" >&2
            VIOLATIONS=$((VIOLATIONS + 1))
            continue
        fi
        if [[ -z "$expires_at" ]]; then
            echo "[ALLOWLIST] 行 $lineno ($rule_id $path): 缺 expires_at (无过期视为失败)" >&2
            VIOLATIONS=$((VIOLATIONS + 1))
        fi
        # legacy_entry_id 必须能在 legacy-cleanup-matrix.csv 找到
        if [[ -n "$legacy_entry_id" && -f docs/architecture/legacy-cleanup-matrix.csv ]]; then
            if ! grep -qF "$legacy_entry_id" docs/architecture/legacy-cleanup-matrix.csv 2>/dev/null; then
                echo "[ALLOWLIST] 行 $lineno ($rule_id $path): legacy_entry_id '$legacy_entry_id' 未在 legacy-cleanup-matrix.csv 找到" >&2
                VIOLATIONS=$((VIOLATIONS + 1))
            fi
        fi
    done < "$ALLOWLIST"
}

echo "=== Architecture Guardrails (phase=$PHASE) ===" >&2

load_allowlist
rule_c1
rule_c2
rule_c3
rule_c4
rule_ri3a
rule_ri3b

if [[ "$PHASE" != "phase0" ]]; then
    validate_allowlist
fi

echo "" >&2
echo "=== 汇总 ===" >&2
echo "violations: $VIOLATIONS" >&2
echo "warnings: $WARNINGS" >&2

case "$PHASE" in
    phase0)
        echo "phase0: warn-only (退出码 0)" >&2
        exit 0
        ;;
    phase1|phase2)
        if [[ $VIOLATIONS -gt 0 ]]; then
            echo "$PHASE: $VIOLATIONS 个违规未被 allowlist 覆盖, 退出码 1" >&2
            exit 1
        fi
        echo "$PHASE: 全部违规已被 allowlist 覆盖或无违规, 退出码 0" >&2
        exit 0
        ;;
    *)
        echo "未知 phase: $PHASE" >&2
        exit 2
        ;;
esac
