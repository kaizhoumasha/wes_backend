"""清理 WES 运行时/调试数据,保留主数据。

用途:开发/联调环境把历次跑流程残留的运行时数据(session/inbox/outbox/command/
timeline/diagnostic/resource 运行时投影等)清空,回到一个干净的"只有主数据"状态,
方便重新触发 START → SCAN_COMPLETED 观察落库。

设计约定:
- 固定运行时表清单(RUNTIME_TABLES),保留主数据表(MASTER_DATA_TABLES 白名单)。
- 默认 ``--dry-run``:只打印将清空的表 + 当前行数,不写库。
- 必须显式 ``--yes`` 才真正 TRUNCATE。
- 清空后重置设备投影字段(``current_command_id``、``device_status=IDLE``)与
  ``work_lines.runtime_status=STOPPED``,以便干净地重跑 START。
- 仅在 ``APP_DEBUG=True`` 时允许执行,生产环境直接拒绝(可用 ``--force`` 覆盖,
  仅限确有需要的人工运维场景)。

不在本脚本范围内:wes_sys.audit_logs(审计域,默认保留)、alembic_version、
用户/角色/权限主数据。如需一并清审计日志,传 ``--include-audit-logs``。

用法::

    uv run python scripts/data/reset_runtime_data.py            # dry-run 预览
    uv run python scripts/data/reset_runtime_data.py --yes      # 真正清空
    bash scripts/data/reset_runtime_data.sh --yes               # wrapper 等价
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import text

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.core.conf import settings
from src.database.db import close_db, get_db_context, init_db

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 运行时/调试数据表(wes_biz schema)。这些表记录的是"跑流程产生的事实",
# 清空后只要主数据在,就能重新跑。按字母序排列,便于核对。
RUNTIME_TABLES: tuple[str, ...] = (
    "callback_logs",
    "device_commands",
    "handling_operation_moves",
    "handling_operation_steps",
    "handling_operations",
    "ng_return_items",
    "rack_operations",
    "rack_tasks",
    "resource_bin_cell_occupancies",
    "resource_bin_content_snapshot_items",
    "resource_bin_content_snapshots",
    "resource_bin_material_mounts",
    "resource_bin_placements",
    "resource_rack_bin_mounts",
    "resource_rack_placements",
    "resource_state_events",
    "runtime_holds",
    "smt_inbound_handoff_demands",
    "smt_inbound_handoff_source_items",
    "system_outbox",
    "wms_call_evidence",
    "wms_circuit_breaker_state",
    "workline_bin_cell_reservations",
    "workline_diagnostics",
    "workline_dispatch_attempts",
    "workline_inbox",
    "workline_safety_incidents",
    "workline_sessions",
    "workline_timelines",
)

# 主数据/字典表白名单:绝对不能清。列出来既是文档,也用于自检——
# 如果 RUNTIME_TABLES 里误加进这些表,启动校验会直接报错退出。
MASTER_DATA_TABLES: frozenset[str] = frozenset(
    {
        "work_lines",
        "devices",
        "workline_rack_positions",
        "resource_racks",
        "resource_rack_types",
        "resource_rack_slot_templates",
        "resource_bins",
        "resource_bin_types",
        "resource_bin_slot_templates",
    }
)

# 审计域表,默认保留,需要 --include-audit-logs 才清。
AUDIT_LOG_TABLE = "wes_sys.audit_logs"


@dataclass
class ResetSummary:
    """清库结果摘要。"""

    mode: str = "dry-run"
    truncated: list[dict[str, Any]] = field(default_factory=list)
    reset_devices: int = 0
    reset_worklines: int = 0
    included_audit_logs: bool = False
    mock_wms_reset: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _qualified(name: str) -> str:
    """运行时表都在 wes_biz schema 下。"""
    return f"wes_biz.{name}"


def _mock_wms_reset_url() -> str:
    """推导 Mock WMS 的 /debug/reset 地址。

    优先级:``WES_MOCK_WMS_URL`` 环境变量 > 从 ``WMS_SYNC_BASE_URL`` 取 host
    (剥掉 /api/wms 前缀)> 默认 localhost:8011。
    """
    explicit = os.environ.get("WES_MOCK_WMS_URL")
    if explicit:
        return explicit.rstrip("/").removesuffix("/debug/reset") + "/debug/reset"
    base = os.environ.get("WMS_SYNC_BASE_URL", "http://localhost:8011/api/wms")
    parts = urlsplit(base)
    # 丢掉 api path,只保留 scheme://host:port
    return urlunsplit((parts.scheme, parts.netloc, "/debug/reset", "", ""))


async def reset_mock_wms() -> dict[str, Any]:
    """调 Mock WMS 的 /debug/reset 恢复初始状态。

    Mock WMS 是有状态的(记录货架占用工位/料箱挂载),只清 WES DB 不重置它,
    连续重跑会撞 ``TARGET_POSITION_OCCUPIED``。Mock ECS 无独立状态(命令由
    WES 重发),不需要重置。
    """
    url = _mock_wms_reset_url()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url)
        resp.raise_for_status()
        return {"url": url, "ok": True, "body": resp.json()}


def _validate_table_sets() -> None:
    """启动自检:运行时清单与主数据白名单不能有交集。"""
    overlap = set(RUNTIME_TABLES) & MASTER_DATA_TABLES
    if overlap:
        raise RuntimeError(
            f"运行时清单与主数据白名单存在交集,拒绝执行: {sorted(overlap)}",
        )


async def _row_count(db: AsyncSession, qualified_name: str) -> int:
    result = await db.execute(text(f"SELECT count(*) FROM {qualified_name}"))  # noqa: S608
    return int(result.scalar_one())


async def reset_runtime_data(
    db: AsyncSession,
    *,
    apply: bool,
    include_audit_logs: bool,
    reset_mocks: bool,
) -> ResetSummary:
    """清空运行时数据并重置投影字段。``apply=False`` 时只统计不写。"""
    summary = ResetSummary(mode="apply" if apply else "dry-run")

    # Mock WMS 必须先于 WES DB 重置:它记录的"货架已到工位"事实是 WES 出料分配
    # 的外部输入。先清 WES 会留下不一致窗口;且只有 apply 时才需要真正重置。
    if apply and reset_mocks:
        try:
            summary.mock_wms_reset = await reset_mock_wms()
        except Exception as exc:
            summary.mock_wms_reset = {"ok": False, "error": str(exc)}

    targets: list[str] = [_qualified(t) for t in RUNTIME_TABLES]
    if include_audit_logs:
        targets.append(AUDIT_LOG_TABLE)
        summary.included_audit_logs = True

    # 先统计每张表当前行数(dry-run 和 apply 都打印,作为前后对照)
    counts_before: dict[str, int] = {}
    for qualified_name in targets:
        counts_before[qualified_name] = await _row_count(db, qualified_name)

    if apply:
        # RESTART IDENTITY 重置序列;CASCADE 处理 FK 依赖(运行时表互相引用)。
        # TRUNCATE 是 DDL,Postgres 隐式提交,所以这里不依赖事务回滚保护。
        joined = ", ".join(targets)
        await db.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))

        # 清空命令后,devices.current_command_id 指向的命令已不存在,重置回空闲。
        dev_result = await db.execute(
            text(
                "UPDATE wes_biz.devices "
                "   SET current_command_id = NULL, "
                "       device_status = 'IDLE', "
                "       error_code = NULL, "
                "       last_heartbeat_at = NULL "
                " WHERE current_command_id IS NOT NULL "
                "    OR device_status <> 'IDLE' "
                "    OR error_code IS NOT NULL",
            ),
        )
        summary.reset_devices = int(dev_result.rowcount or 0)

        # WorkLine 回到 STOPPED,重新走 START 准入。
        wl_result = await db.execute(
            text(
                "UPDATE wes_biz.work_lines    SET runtime_status = 'STOPPED',        start_admission_status = NULL",
            ),
        )
        summary.reset_worklines = int(wl_result.rowcount or 0)
        await db.commit()

    for qualified_name in targets:
        summary.truncated.append(
            {"table": qualified_name, "rows_before": counts_before[qualified_name]},
        )

    return summary


def _format_summary(summary: ResetSummary) -> str:
    """人类可读的摘要,带颜色提示危险操作。"""
    lines: list[str] = []
    verb = "已清空" if summary.mode == "apply" else "将清空(dry-run)"
    lines.append(f"\n=== 运行时数据 {verb} ===")
    total = sum(t["rows_before"] for t in summary.truncated)
    lines.extend(f"  {t['table']:<48} {t['rows_before']:>8}" for t in summary.truncated)
    lines.append(f"  {'合计':<48} {total:>8}")
    if summary.mode == "apply":
        lines.append(
            f"\n设备投影重置: {summary.reset_devices} 行 | 工作线回 STOPPED: {summary.reset_worklines} 行",
        )
        if summary.included_audit_logs:
            lines.append("已一并清理 wes_sys.audit_logs")
        if summary.mock_wms_reset is not None:
            if summary.mock_wms_reset.get("ok"):
                lines.append(
                    f"Mock WMS 已重置: {summary.mock_wms_reset.get('url')}",
                )
            else:
                lines.append(
                    f"Mock WMS 重置失败: {summary.mock_wms_reset.get('error')}",
                )
    return "\n".join(lines)


async def _amain() -> int:
    _validate_table_sets()

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="确认执行清空(不加则只 dry-run 预览)",
    )
    parser.add_argument(
        "--include-audit-logs",
        action="store_true",
        help="一并清空 wes_sys.audit_logs(默认保留审计日志)",
    )
    parser.add_argument(
        "--reset-mocks",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="同时重置 Mock WMS 到初始状态(默认开,--no-reset-mocks 关闭)。"
        "Mock WMS 有状态,不重置则连续重跑会撞 TARGET_POSITION_OCCUPIED。",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="在非 APP_DEBUG 环境强制执行(慎用)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 摘要而非人类可读文本",
    )
    args = parser.parse_args()

    if not settings.APP_DEBUG and not args.force:
        print(
            "拒绝执行:当前环境 APP_DEBUG=False。本脚本仅用于开发/联调,如确需在生产环境清理请加 --force。",
            file=sys.stderr,
        )
        return 2

    if not args.yes:
        print(
            "Dry-run 模式:仅预览将清空的表与行数,不写库。加 --yes 真正执行。\n",
            file=sys.stderr,
        )

    await init_db()
    try:
        async with get_db_context() as db:
            summary = await reset_runtime_data(
                db,
                apply=args.yes,
                include_audit_logs=args.include_audit_logs,
                reset_mocks=args.reset_mocks,
            )
            if not args.yes:
                await db.rollback()
    finally:
        await close_db()

    if args.json:
        print(json.dumps(summary.to_dict(), ensure_ascii=False, sort_keys=True))
    else:
        print(_format_summary(summary))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
