"""清理 WES PostgreSQL 与 Redis 运行时/调试数据,保留主数据。

用途:开发/联调环境把历次跑流程残留的运行时数据(session/inbox/outbox/command/
timeline/diagnostic/resource 运行时投影等)清空,回到一个干净的"只有主数据"状态,
方便重新触发 START → SCAN_COMPLETED 观察落库。

设计约定:
- 固定 schema-qualified 过程表清单(RUNTIME_TABLES),保留主数据表(MASTER_DATA_TABLES 白名单)。
- 默认清理当前 PostgreSQL 数据库的 Redis 缓存命名空间，以及专用 Celery broker/result DB；
  ``auth:*`` 认证会话不在缓存命名空间内，默认保留。
- 默认 ``--dry-run``:只打印将清空的表 + 当前行数,不写库。
- 必须显式 ``--yes`` 才真正 TRUNCATE。
- ``--transport-task-id`` 按 ID 清理一个 TransportTask 的完整本地 Transport 链路，
  不重置其它运行数据或 Mock。
- 清空后将 ``wes_biz.workline_runtime_status_projections`` 重置为 ``STOPPED``，
  以便干净地重跑 START；Device 主数据不承载运行态，不做改写。
- 全量 reset 仅在 ``APP_DEBUG=True`` 时允许执行；生产型配置可用 ``--force``
  显式覆盖，供数据可丢弃的联调服务器人工运维。

不在本脚本范围内:wes_sys.audit_logs(审计域,默认保留)、alembic_version、
用户/角色/权限主数据。如需一并清审计日志,传 ``--include-audit-logs``。

用法::

    uv run python scripts/data/reset_runtime_data.py            # dry-run 预览
    uv run python scripts/data/reset_runtime_data.py --yes      # 真正清空
    uv run python scripts/data/reset_runtime_data.py --yes --no-reset-redis
    uv run python scripts/data/reset_runtime_data.py --transport-task-id transport-... --yes
    bash scripts/data/reset_runtime_data.sh --yes               # wrapper 等价
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from redis.asyncio import Redis
from sqlalchemy import text

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.app.transport.debug_reset import normalize_transport_task_id
from src.core.conf import settings
from src.core.outbound_http.contracts import (
    OutboundHttpDeliveryState,
    OutboundHttpMethod,
    OutboundHttpRequest,
    OutboundHttpResponseLimits,
)
from src.core.outbound_http.factory import build_outbound_http_transport
from src.database.db import close_db, get_db_context, init_db
from src.database.redis_namespace import database_redis_cache_prefix

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, order=True)
class TableTarget:
    """清理目标的显式 schema identity。"""

    schema: str
    table: str

    @property
    def identity(self) -> str:
        return f"{self.schema}.{self.table}"


def _biz(table: str) -> TableTarget:
    return TableTarget("wes_biz", table)


def _sys(table: str) -> TableTarget:
    return TableTarget("wes_sys", table)


# 运行时/调试数据表。这些表记录的是"跑流程产生的事实",清空后只要主数据在,
# 就能重新跑。退役表不再作为 reset target。
RUNTIME_TABLES: tuple[TableTarget, ...] = (
    *(
        _biz(table)
        for table in (
            "callback_logs",
            "device_commands",
            "direct_pick_executions",
            "inbound_evidence_conflicts",
            "inbound_evidences",
            "manual_picking_passages",
            "material_executions",
            "material_units",
            "object_transition_events",
            "picking_task_bin_source_racks",
            "picking_tasks",
            "position_projections",
            "resource_bin_cell_occupancies",
            "resource_bin_content_snapshot_items",
            "resource_bin_content_snapshots",
            "resource_bin_material_mounts",
            "resource_bin_placements",
            "resource_rack_bin_mounts",
            "resource_rack_placements",
            "resource_state_events",
            "transport_decision_bindings",
            "wms_confirmations",
            "workline_sessions",
            "workline_timelines",
        )
    ),
    *(
        _biz(table)
        for table in (
            "transport_callback_receipts",
            "transport_debug_position_projections",
            "transport_debug_run_steps",
            "transport_debug_runs",
            "transport_evidence",
            "transport_members",
            "transport_tasks",
            "workline_runtime_status_projections",
        )
    ),
    _sys("api_access_logs"),
)

# 主数据/字典表白名单:绝对不能清。列出来既是文档,也用于自检——
# 如果 RUNTIME_TABLES 里误加进这些表,启动校验会直接报错退出。
MASTER_DATA_TABLES: frozenset[TableTarget] = frozenset(
    {
        _biz("work_lines"),
        _biz("devices"),
        _biz("workline_positions"),
        _biz("resource_racks"),
        _biz("resource_rack_types"),
        _biz("resource_rack_slot_templates"),
        _biz("resource_bins"),
        _biz("resource_bin_types"),
        _biz("resource_bin_slot_templates"),
        _sys("alembic_version"),
        _sys("api_app_permissions"),
        _sys("api_applications"),
        _sys("permissions"),
        _sys("role_permissions"),
        _sys("roles"),
        _sys("user_roles"),
        _sys("users"),
    }
)

# 审计域表,默认保留,需要 --include-audit-logs 才清。
AUDIT_LOG_TABLE = TableTarget("wes_sys", "audit_logs")


@dataclass
class ResetSummary:
    """清库结果摘要。"""

    mode: str = "dry-run"
    truncated: list[dict[str, Any]] = field(default_factory=list)
    reset_worklines: int = 0
    included_audit_logs: bool = False
    mock_wms_reset: dict[str, Any] | None = None
    redis: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TransportTaskResetSummary:
    """单个 Transport 联调任务的定向清理摘要。"""

    mode: str
    transport_task_id: str
    status: str
    rows_before: dict[str, int]
    deleted: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RedisTarget:
    """一个不暴露凭据的 Redis 清理目标。"""

    roles: tuple[str, ...]
    url: str = field(repr=False, compare=False)
    database: int
    endpoint: str
    pattern: str


def _qualified(target: TableTarget) -> str:
    """返回只由代码内常量构造的 schema-qualified identity。"""
    return target.identity


def _mock_wms_reset_url() -> str:
    """推导 Mock WMS 的 /debug/reset 地址。

    优先级：``WES_MOCK_WMS_URL`` 显式覆盖，否则使用本地 Mock 默认地址。
    """
    explicit = os.environ.get("WES_MOCK_WMS_URL")
    if explicit:
        return explicit.rstrip("/").removesuffix("/debug/reset") + "/debug/reset"
    return "http://localhost:8011/debug/reset"


def _redis_database(url: str) -> int:
    parsed = urlsplit(url)
    if parsed.scheme not in {"redis", "rediss"}:
        raise RuntimeError(f"不支持的 Redis URL scheme: {parsed.scheme or '<empty>'}")
    path = parsed.path.removeprefix("/")
    if not path:
        return 0
    try:
        database = int(path)
    except ValueError as exc:
        raise RuntimeError("Redis URL database 必须是非负整数") from exc
    if database < 0:
        raise RuntimeError("Redis URL database 必须是非负整数")
    return database


def _redis_targets() -> tuple[RedisTarget, ...]:
    """冻结 Redis 过程数据范围；DB 0 只清当前 PostgreSQL 数据库命名空间。"""
    cache_pattern = f"{database_redis_cache_prefix(settings.POSTGRES_DB)}:*"
    configured = (
        ("application-cache", settings.REDIS_URL, cache_pattern),
        ("celery-broker", settings.CELERY_BROKER, "*"),
        ("celery-result-backend", settings.CELERY_BACKEND, "*"),
    )
    merged: dict[tuple[str, str | None, int | None, str | None, int], dict[str, Any]] = {}
    for role, url, pattern in configured:
        parsed = urlsplit(url)
        database = _redis_database(url)
        identity = (parsed.scheme, parsed.hostname, parsed.port, parsed.username, database)
        target = merged.setdefault(identity, {"roles": [], "url": url, "patterns": set()})
        target["roles"].append(role)
        target["patterns"].add(pattern)

    targets: list[RedisTarget] = []
    for identity, target in merged.items():
        scheme, host, port, _, database = identity
        patterns = target["patterns"]
        pattern = "*" if "*" in patterns else next(iter(patterns))
        targets.append(
            RedisTarget(
                roles=tuple(sorted(target["roles"])),
                url=target["url"],
                database=database,
                endpoint=f"{scheme}://{host or '<unknown>'}:{port or 6379}/{database}",
                pattern=pattern,
            )
        )
    return tuple(sorted(targets, key=lambda target: (target.endpoint, target.roles)))


async def reset_redis_process_data(*, apply: bool) -> list[dict[str, Any]]:
    """扫描并按冻结范围删除 Redis 过程键；认证会话不在 DB 0 命名空间内。"""
    summaries: list[dict[str, Any]] = []
    for target in _redis_targets():
        client = Redis.from_url(
            target.url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=5,
        )
        try:
            keys = [str(key) async for key in client.scan_iter(match=target.pattern, count=500)]
            deleted = 0
            if apply:
                for offset in range(0, len(keys), 500):
                    deleted += int(await client.delete(*keys[offset : offset + 500]))
            summaries.append(
                {
                    "roles": list(target.roles),
                    "endpoint": target.endpoint,
                    "pattern": target.pattern,
                    "keys_before": len(keys),
                    "deleted": deleted,
                }
            )
        finally:
            await client.aclose()
    return summaries


async def reset_mock_wms() -> dict[str, Any]:
    """调 Mock WMS 的 /debug/reset 恢复初始状态。

    Mock WMS 是有状态的(记录货架占用工位/料箱挂载),只清 WES DB 不重置它,
    连续重跑会撞 ``TARGET_POSITION_OCCUPIED``。Mock ECS 无独立状态(命令由
    WES 重发),不需要重置。
    """
    url = _mock_wms_reset_url()
    base_url, _, path = url.partition("/debug/reset")
    transport = build_outbound_http_transport(
        system_id="dev_mock_wms_reset",
        base_url=base_url,
        timeout_seconds=10.0,
    )
    try:
        result = await transport.send(
            OutboundHttpRequest(
                method=OutboundHttpMethod.POST,
                path=path or "/debug/reset",
                response_limits=OutboundHttpResponseLimits(max_wire_bytes=256 * 1024, max_decoded_bytes=256 * 1024),
            )
        )
    finally:
        await transport.aclose()
    if result.delivery_state != OutboundHttpDeliveryState.RESPONSE_RECEIVED or result.status_code is None:
        raise RuntimeError("Mock WMS reset request did not receive a response")
    if not 200 <= result.status_code < 300:
        raise RuntimeError(f"Mock WMS reset failed with status={result.status_code}")
    return {"url": url, "ok": True, "body": json.loads(result.decoded_body)}


def _validate_table_sets(targets: tuple[TableTarget, ...] = RUNTIME_TABLES) -> None:
    """启动自检:运行时清单与主数据白名单不能有交集。"""
    if len(targets) != len(set(targets)):
        raise RuntimeError("运行时清单包含重复目标,拒绝执行")
    overlap = set(targets) & MASTER_DATA_TABLES
    if overlap:
        raise RuntimeError(
            f"运行时清单包含主数据目标,拒绝执行: {[target.identity for target in sorted(overlap)]}",
        )


async def _validate_targets_exist(db: AsyncSession, targets: tuple[TableTarget, ...]) -> None:
    """在任何外部或数据库 mutation 前验证目标表及其 schema。"""
    result = await db.execute(
        text(
            "SELECT table_schema, table_name "
            "FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' "
            "  AND table_schema NOT IN ('pg_catalog', 'information_schema')"
        )
    )
    catalog = {(str(row[0]), str(row[1])) for row in result.all()}
    expected = {(target.schema, target.table) for target in targets}
    missing = sorted(expected - catalog)
    if not missing:
        return

    mismatches: list[str] = []
    absent: list[str] = []
    for schema, table in missing:
        alternate = sorted(f"{found_schema}.{table}" for found_schema, found_table in catalog if found_table == table)
        expected_identity = f"{schema}.{table}"
        if alternate:
            mismatches.append(f"{expected_identity} -> {', '.join(alternate)}")
        else:
            absent.append(expected_identity)
    if mismatches:
        raise RuntimeError(f"reset 目标 schema 不匹配,拒绝执行: {'; '.join(mismatches)}")
    raise RuntimeError(f"reset 目标表不存在,拒绝执行: {', '.join(absent)}")


async def _row_count(db: AsyncSession, qualified_name: str) -> int:
    result = await db.execute(text(f"SELECT count(*) FROM {qualified_name}"))  # noqa: S608
    return int(result.scalar_one())


def _transport_task_reset_allowed(app_env: str, *, force: bool) -> bool:
    """生产型配置默认拒绝，数据可丢弃的联调服务器需显式 force。"""
    return app_env != "prod" or force


def _parse_transport_task_id(value: str) -> str:
    try:
        return normalize_transport_task_id(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("transport_task_id 必须是 1..80 个非空且不含 NUL 的字符") from exc


async def _transport_task_row_count(
    db: AsyncSession,
    table: str,
    transport_task_id: str,
    *,
    id_column: str = "transport_task_id",
) -> int:
    result = await db.execute(
        text(f"SELECT count(*) FROM wes_biz.{table} WHERE {id_column} = :transport_task_id"),  # noqa: S608
        {"transport_task_id": transport_task_id},
    )
    return int(result.scalar_one())


async def reset_transport_task_data(
    db: AsyncSession,
    *,
    transport_task_id: str,
    apply: bool,
) -> TransportTaskResetSummary:
    """按 ID 定向清理一个 TransportTask 的完整本地 Transport 链路。"""
    task_id = normalize_transport_task_id(transport_task_id)

    task_query = (
        "SELECT transport_task_id, status "
        "FROM wes_biz.transport_tasks "
        "WHERE transport_task_id = :transport_task_id FOR UPDATE"
        if apply
        else "SELECT transport_task_id, status "
        "FROM wes_biz.transport_tasks "
        "WHERE transport_task_id = :transport_task_id"
    )
    result = await db.execute(
        text(task_query),
        {"transport_task_id": task_id},
    )
    row = result.one_or_none()
    if row is None:
        raise RuntimeError(f"TransportTask 不存在: {task_id}")

    _, status = row
    if apply:
        active_run = await db.scalar(
            text(
                "SELECT run.run_id FROM wes_biz.transport_debug_run_steps AS step "
                "JOIN wes_biz.transport_debug_runs AS run ON run.run_id = step.run_id "
                "WHERE step.transport_task_id = :transport_task_id "
                "AND run.status IN ('RUNNING', 'NEEDS_ATTENTION') "
                "LIMIT 1"
            ),
            {"transport_task_id": task_id},
        )
        if active_run is not None:
            raise RuntimeError("TransportTask 正被活动 Transport 自动联调轮次引用，拒绝清理")
    receipt_result = await db.execute(
        text(
            "SELECT count(*) FROM wes_biz.transport_callback_receipts "
            "WHERE response_data_json ->> 'transport_task_id' = :transport_task_id"
        ),
        {"transport_task_id": task_id},
    )
    rows_before = {
        "wes_biz.transport_callback_receipts": int(receipt_result.scalar_one()),
        "wes_biz.transport_evidence": await _transport_task_row_count(db, "transport_evidence", task_id),
        "wes_biz.transport_debug_position_projections": await _transport_task_row_count(
            db,
            "transport_debug_position_projections",
            task_id,
            id_column="source_transport_task_id",
        ),
        "wes_biz.transport_members": await _transport_task_row_count(db, "transport_members", task_id),
        "wes_biz.transport_tasks": 1,
    }
    summary = TransportTaskResetSummary(
        mode="apply" if apply else "dry-run",
        transport_task_id=task_id,
        status=str(status),
        rows_before=rows_before,
    )
    if not apply:
        return summary

    try:
        delete_statements = (
            (
                "transport_callback_receipts",
                "DELETE FROM wes_biz.transport_callback_receipts "
                "WHERE response_data_json ->> 'transport_task_id' = :transport_task_id",
            ),
            (
                "transport_evidence",
                "DELETE FROM wes_biz.transport_evidence WHERE transport_task_id = :transport_task_id",
            ),
            (
                "transport_debug_position_projections",
                "DELETE FROM wes_biz.transport_debug_position_projections "
                "WHERE source_transport_task_id = :transport_task_id",
            ),
            (
                "transport_members",
                "DELETE FROM wes_biz.transport_members WHERE transport_task_id = :transport_task_id",
            ),
            (
                "transport_tasks",
                "DELETE FROM wes_biz.transport_tasks WHERE transport_task_id = :transport_task_id",
            ),
        )
        for table, statement in delete_statements:
            delete_result = await db.execute(
                text(statement),
                {"transport_task_id": task_id},
            )
            summary.deleted[f"wes_biz.{table}"] = int(delete_result.rowcount or 0)
        if summary.deleted["wes_biz.transport_tasks"] != 1:
            raise RuntimeError(f"TransportTask 删除数量异常: {summary.deleted['wes_biz.transport_tasks']}")
        await db.commit()
    except Exception:
        with suppress(Exception):
            await db.rollback()
        raise
    return summary


async def reset_runtime_data(
    db: AsyncSession,
    *,
    apply: bool,
    include_audit_logs: bool,
    reset_mocks: bool,
    reset_redis: bool = False,
) -> ResetSummary:
    """清空运行时数据并重置投影字段。``apply=False`` 时只统计不写。"""
    _validate_table_sets()
    summary = ResetSummary(mode="apply" if apply else "dry-run")
    targets = list(RUNTIME_TABLES)
    if include_audit_logs:
        targets.append(AUDIT_LOG_TABLE)
        summary.included_audit_logs = True
    target_tuple = tuple(targets)
    await _validate_targets_exist(db, target_tuple)

    # 先统计每张表当前行数(dry-run 和 apply 都打印,作为前后对照)
    counts_before: dict[str, int] = {}
    for target in targets:
        qualified_name = _qualified(target)
        counts_before[qualified_name] = await _row_count(db, qualified_name)

    if not apply and reset_redis:
        summary.redis = await reset_redis_process_data(apply=False)

    if apply:
        # Mock WMS 必须先于任何 WES DB mutation:它记录的"货架已到工位"事实是
        # WES 出料分配的外部输入。失败时 fail closed；仅 --no-reset-mocks 可跳过。
        if reset_mocks:
            try:
                summary.mock_wms_reset = await reset_mock_wms()
            except Exception as exc:
                raise RuntimeError(f"Mock WMS 重置失败,数据库未清理: {exc}") from exc

        # Redis 先清理，避免旧 Celery 消息在数据库 reset 提交后重新投递过期流程。
        # Redis 仅承载可重建缓存、broker 和 result backend；失败时数据库尚未修改。
        if reset_redis:
            try:
                summary.redis = await reset_redis_process_data(apply=True)
            except Exception as exc:
                raise RuntimeError(f"Redis 过程数据清理失败,数据库未清理: {exc}") from exc

        try:
            # RESTART IDENTITY 重置序列;CASCADE 处理 FK 依赖(运行时表互相引用)。
            # PostgreSQL TRUNCATE 仍受当前事务保护,与后续投影 reset 一起提交。
            joined = ", ".join(_qualified(target) for target in targets)
            await db.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))

            # WorkLine runtime 投影回到 STOPPED，等待 START 校验并启用当前配置。
            wl_result = await db.execute(
                text(
                    "INSERT INTO wes_biz.workline_runtime_status_projections ("
                    "workline_id, runtime_status, source, stopped_at, stopped_reason, "
                    "resumed_at, evidence_json"
                    ") "
                    "SELECT id, 'STOPPED', 'scripts/data/reset_runtime_data', "
                    "       now() AT TIME ZONE 'UTC', 'RUNTIME_RESET', NULL, '{}'::json "
                    "  FROM wes_biz.work_lines "
                    " WHERE is_deleted = false "
                    "ON CONFLICT (workline_id) DO UPDATE SET "
                    "    runtime_status = EXCLUDED.runtime_status, "
                    "    source = EXCLUDED.source, "
                    "    stopped_at = EXCLUDED.stopped_at, "
                    "    stopped_reason = EXCLUDED.stopped_reason, "
                    "    resumed_at = NULL, "
                    "    evidence_json = EXCLUDED.evidence_json",
                ),
            )
            summary.reset_worklines = int(wl_result.rowcount or 0)
            await db.commit()
        except Exception:
            # 本函数拥有 apply 的 commit，也必须在所有 mutation/commit 失败路径释放事务。
            # rollback 自身故障不得覆盖最先发生的数据库错误。
            with suppress(Exception):
                await db.rollback()
            raise

    for target in targets:
        qualified_name = _qualified(target)
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
        lines.append(f"\n工作线回 STOPPED: {summary.reset_worklines} 行")
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
    if summary.redis:
        lines.append("\nRedis 过程数据:")
        for target in summary.redis:
            roles = ",".join(target["roles"])
            action = target["deleted"] if summary.mode == "apply" else target["keys_before"]
            lines.append(f"  {roles:<38} {target['endpoint']} pattern={target['pattern']} keys={action}")
    return "\n".join(lines)


def _format_transport_task_summary(summary: TransportTaskResetSummary) -> str:
    verb = "已清理" if summary.mode == "apply" else "将清理(dry-run)"
    lines = [
        f"\n=== Transport 联调任务 {verb} ===",
        f"  transport_task_id: {summary.transport_task_id}",
        f"  status: {summary.status}",
    ]
    lines.extend(f"  {table:<48} {count:>8}" for table, count in summary.rows_before.items())
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
        "--transport-task-id",
        type=_parse_transport_task_id,
        help="按 ID 清理指定 TransportTask 的完整本地 Transport 链路",
    )
    parser.add_argument(
        "--reset-mocks",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="全量模式同时重置 Mock WMS 到初始状态(默认开,--no-reset-mocks 关闭；定向模式忽略)。"
        "Mock WMS 有状态,不重置则连续重跑会撞 TARGET_POSITION_OCCUPIED。",
    )
    parser.add_argument(
        "--reset-redis",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="全量模式清理当前数据库缓存命名空间及专用 Celery broker/result Redis DB"
        "(默认开,--no-reset-redis 关闭；定向模式忽略)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制越过环境闸(全量:APP_DEBUG=False；定向:APP_ENV=prod，慎用)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 摘要而非人类可读文本",
    )
    args = parser.parse_args()

    transport_task_reset_requested = args.transport_task_id is not None
    if transport_task_reset_requested and args.include_audit_logs:
        parser.error("--transport-task-id 不能与 --include-audit-logs 同时使用")

    if transport_task_reset_requested and not _transport_task_reset_allowed(settings.APP_ENV, force=args.force):
        print("拒绝执行:生产型配置定向删除 TransportTask 必须显式加 --force。", file=sys.stderr)
        return 2
    if not transport_task_reset_requested and not settings.APP_DEBUG and not args.force:
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
            if transport_task_reset_requested:
                summary = await reset_transport_task_data(
                    db,
                    transport_task_id=args.transport_task_id,
                    apply=args.yes,
                )
            else:
                summary = await reset_runtime_data(
                    db,
                    apply=args.yes,
                    include_audit_logs=args.include_audit_logs,
                    reset_mocks=args.reset_mocks,
                    reset_redis=args.reset_redis,
                )
            if not args.yes:
                await db.rollback()
    finally:
        await close_db()

    if args.json:
        print(json.dumps(summary.to_dict(), ensure_ascii=False, sort_keys=True))
    elif isinstance(summary, TransportTaskResetSummary):
        print(_format_transport_task_summary(summary))
    else:
        print(_format_summary(summary))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
