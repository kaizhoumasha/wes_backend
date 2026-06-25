#!/usr/bin/env python3
"""扫描 legacy 代码入口，生成 legacy-cleanup-matrix.csv。

按 P0-002 规范对每个入口赋 entry_type / current_owner / business_semantics /
strategy / drop_phase / risk。发现命令对齐 SPEC §Proposed Change 的入口粒度。

用法: uv run python scripts/generate_legacy_matrix.py
产出: docs/architecture/legacy-cleanup-matrix.csv + 汇总统计到 stdout
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

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
    target_capability: str = ""
    notes: str = field(default="", repr=False)


def git_grep(pattern: str, paths: list[str]) -> list[str]:
    """运行 git grep，返回匹配行列表。"""
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "grep", "-n", "-E", pattern, "--", *paths],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return [ln for ln in result.stdout.splitlines() if ln.strip()]
    except Exception:
        return []


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


def parse_entries() -> list[Entry]:
    entries: list[Entry] = []
    seen: set[str] = set()

    def add(path: str, symbol: str, entry_type: str, owner: str):
        rel = str(Path(path).relative_to(REPO_ROOT)) if Path(path).is_absolute() else path
        sym = symbol or "<file>"
        eid = f"legacy:{rel}:{sym}"
        if eid in seen:
            return
        seen.add(eid)
        bs, p4 = classify_business_semantics(sym, rel)
        strat, phase, risk = assign_strategy(bs, entry_type)
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

    # 3. Services (class)
    for line in git_grep(r"^class [A-Za-z_]", ["src/app/workline/services"]):
        m = re.match(r"([^:]+):(\d+):class ([A-Za-z_][A-Za-z0-9_]*)", line)
        if m and "Service" in m.group(3):
            add(m.group(1), m.group(3), "service", "workline")

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

    # 7. tests
    for line in git_grep(
        r"^class Test|^def test_|^async def test_", ["tests/workline_runtime", "tests/workline_plugins"]
    ):
        m = re.match(r"([^:]+):(\d+):(?:class |def |async def )([A-Za-z_][A-Za-z0-9_]*)", line)
        if m:
            path = m.group(1)
            owner = "workline_plugins" if "workline_plugins" in path else "workline_runtime"
            add(path, m.group(3), "test", owner)

    # 8. doc_templates (文件级)
    for tmpl in (REPO_ROOT / "docs/templates/workline_plugin").glob("*"):
        if tmpl.is_file():
            add(str(tmpl), "<file>", "doc_template", "workline_runtime")

    # 9. guardrail_seed_scope: P0-007 seed allowlist 命中的跨域路径
    # (callback/rack/handling/resource/wms_integration, 非完整清理, 仅供 allowlist 追踪)
    seed_paths = [
        (
            "src/app/callback/services/callback_ingress_service.py",
            "callback",
            "service",
            "跨域 WMS import (C1 seed)",
            "phase2",
            "MEDIUM",
        ),
        ("src/app/rack/services/gateway.py", "rack", "service", "跨域 WMS import (C1 seed)", "phase2", "MEDIUM"),
        (
            "src/app/handling/services/gateway.py",
            "handling",
            "service",
            "跨域 WMS import (C1 seed)",
            "phase2",
            "MEDIUM",
        ),
        (
            "src/app/workline/services/single_layer_rack_orchestration_service.py",
            "workline",
            "service",
            "跨域 WMS import (C1 seed)",
            "phase2",
            "MEDIUM",
        ),
        (
            "src/app/workline/repositories/debug_data_cleanup_repository.py",
            "workline",
            "repository",
            "跨域 WMS import (C1 seed)",
            "phase5-tech",
            "LOW",
        ),
        (
            "src/app/resource/services/projection_service.py",
            "resource",
            "service",
            "跨域 session FK (C2 seed)",
            "phase1",
            "MEDIUM",
        ),
        (
            "src/app/resource/services/projection_integrity_service.py",
            "resource",
            "service",
            "跨域 session FK (C2 seed)",
            "phase1",
            "MEDIUM",
        ),
        ("src/app/resource/models/resource.py", "resource", "model", "跨域 session FK (C2 seed)", "phase1", "MEDIUM"),
        (
            "src/app/wms_integration/services/transport_contract.py",
            "wms_integration",
            "service",
            "跨域 session FK (C2 seed)",
            "phase1",
            "MEDIUM",
        ),
    ]
    for path, owner, etype, bs, phase, risk in seed_paths:
        full = REPO_ROOT / path
        if full.exists():
            sym = "<file>"
            eid = f"legacy:{path}:{sym}"
            if eid not in seen:
                seen.add(eid)
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
                        target_capability="",
                        drop_phase=phase,
                        risk=risk,
                        notes="guardrail_seed_scope",
                    )
                )

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
        "target_capability",
        "drop_phase",
        "risk",
        "notes",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
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
                    "target_capability": e.target_capability,
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
