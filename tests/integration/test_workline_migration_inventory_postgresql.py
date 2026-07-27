"""工作线迁移清单的 PostgreSQL 只读快照合同。"""

from __future__ import annotations

import asyncio
import os
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from scripts import workline_migration_inventory as inventory_cli
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.device import Device, DeviceStatus
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHold, RuntimeHoldStatus, RuntimeHoldType
from src.app.runtime.orchestration.models.session import SessionStatus, WorklineSession
from src.app.runtime.orchestration.repository_wiring import workline_repository
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.runtime.workline_plugins.registry import list_workline_capability_definitions
from src.app.sys.models.outbox import (
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxStatus,
    SystemOutboxTargetType,
)
from src.app.workline.models import (
    WorkLine,
    WorklineMigrationInventoryIssueCode,
    WorklineMigrationInventorySeverity,
    WorklinePluginBinding,
    WorklineRuntimeReferenceType,
)
from src.app.workline.models.workline import LineType
from src.app.workline.repositories import workline_plugin_binding_repository
from src.app.workline.services import WorklineMigrationInventoryService
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence


SESSION_ACTIVE = tuple(
    status
    for status in SessionStatus
    if status
    not in {
        SessionStatus.COMPLETED,
        SessionStatus.FAILED,
        SessionStatus.CANCELLED,
    }
)
SESSION_TERMINAL = (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
COMMAND_ACTIVE = (CommandStatus.PENDING, CommandStatus.SENT, CommandStatus.ACK_RECEIVED)
COMMAND_TERMINAL = (CommandStatus.COMPLETED, CommandStatus.FAILED, CommandStatus.TIMEOUT, CommandStatus.CANCELLED)
OUTBOX_ACTIVE = (
    SystemOutboxStatus.NEW,
    SystemOutboxStatus.DISPATCHING,
    SystemOutboxStatus.RETRY_WAIT,
)
OUTBOX_TERMINAL = (
    SystemOutboxStatus.SENT,
    SystemOutboxStatus.FAILED,
    SystemOutboxStatus.UNKNOWN,
    SystemOutboxStatus.CANCELLED,
)
INBOX_ACTIVE = ("RECEIVED", "PROCESSING", "FAILED")
INBOX_TERMINAL = ("PROCESSED", "DEAD_LETTER")
HOLD_ACTIVE = (RuntimeHoldStatus.OPEN, RuntimeHoldStatus.IN_PROGRESS, RuntimeHoldStatus.REOPENED)
HOLD_TERMINAL = (RuntimeHoldStatus.RESOLVED, RuntimeHoldStatus.VOIDED)


@dataclass(frozen=True, slots=True)
class SeededInventory:
    foundation_workline_id: int
    linked_workline_id: int
    device_id: int
    plugin_key: str
    contract_version: str
    plugin_binding_id: int
    plugin_binding_version: int
    plugin_config_hash: str
    plugin_index_digest: str


def _integration_environment() -> dict[str, str]:
    database_url = os.getenv("INTEGRATION_DATABASE_URL", "").strip()
    if not database_url:
        raise AssertionError(
            "必须设置 INTEGRATION_DATABASE_URL 指向本地 PostgreSQL test 数据库；本测试会基于它创建并清理随机隔离数据库"
        )
    return {**os.environ, "INTEGRATION_DATABASE_URL": database_url}


async def _with_database(scenario: Callable[[str, async_sessionmaker[AsyncSession]], Awaitable[None]]) -> None:
    async with temporary_database(environ=_integration_environment()) as (_database, database_url):
        run_alembic("upgrade", "head", database_url=database_url)
        engine = create_async_engine(database_url, pool_pre_ping=True, pool_size=3, max_overflow=0)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            await scenario(database_url, session_factory)
        finally:
            await engine.dispose()


@pytest.mark.integration
def test_plugin_binding_revision_roundtrip_and_database_constraints() -> None:
    """真实 PostgreSQL 锁定 upgrade/downgrade/upgrade 与关键数据库合同。"""

    async def scenario() -> None:
        async with temporary_database(environ=_integration_environment()) as (_database, database_url):
            run_alembic("upgrade", "e0d58415afc9", database_url=database_url)
            run_alembic("upgrade", "fa15ba0aef65", database_url=database_url)
            engine = create_async_engine(database_url)
            try:
                async with engine.connect() as connection:
                    columns = set(
                        (
                            await connection.execute(
                                text(
                                    "SELECT column_name FROM information_schema.columns "
                                    "WHERE table_schema = 'wes_biz' AND table_name = 'workline_plugin_bindings'"
                                )
                            )
                        ).scalars()
                    )
                    assert {
                        "typed_config_json",
                        "generated_index_digest",
                        "is_enabled",
                        "is_revoked",
                        "revoked_reason",
                    } <= columns
                    assert (
                        await connection.scalar(
                            text(
                                "SELECT COUNT(*) FROM pg_constraint "
                                "WHERE conname IN ('uq_workline_plugin_binding_identity', "
                                "'fk_execution_sessions_plugin_binding', 'fk_execution_work_items_plugin_binding')"
                            )
                        )
                        == 3
                    )
                    assert (
                        await connection.scalar(
                            text(
                                "SELECT COUNT(*) FROM pg_constraint "
                                "WHERE conname LIKE 'ck_%_plugin_binding_version_positive' "
                                "OR conname LIKE 'ck_%_plugin_state_version_non_negative'"
                            )
                        )
                        == 6
                    )
                    assert (
                        await connection.scalar(
                            text(
                                "SELECT column_default FROM information_schema.columns "
                                "WHERE table_schema = 'wes_biz' AND table_name = 'workline_plugin_bindings' "
                                "AND column_name = 'is_revoked'"
                            )
                        )
                        == "false"
                    )
            finally:
                await engine.dispose()

            run_alembic("downgrade", "e0d58415afc9", database_url=database_url)
            engine = create_async_engine(database_url)
            try:
                async with engine.connect() as connection:
                    assert (
                        await connection.scalar(text("SELECT to_regclass('wes_biz.workline_plugin_bindings')")) is None
                    )
            finally:
                await engine.dispose()
            run_alembic("upgrade", "fa15ba0aef65", database_url=database_url)

    asyncio.run(scenario())


@pytest.mark.integration
def test_runtime_binding_revision_enforces_database_invariant() -> None:
    """真实 PostgreSQL 逐表证明 mandatory binding pin、FK 与完整写入合同。"""

    async def scenario(_database_url: str, session_factory: async_sessionmaker[AsyncSession]) -> None:
        pin_fields_by_table = {
            ("wes_biz", "workline_sessions"): {
                "plugin_key",
                "contract_version",
                "plugin_binding_id",
                "plugin_binding_version",
                "plugin_config_hash",
                "plugin_index_digest",
            },
            ("wes_runtime", "execution_sessions"): {
                "plugin_key",
                "manifest_version",
                "plugin_binding_id",
                "plugin_binding_version",
                "plugin_config_hash",
                "plugin_index_digest",
            },
            ("wes_runtime", "execution_work_items"): {
                "plugin_key",
                "manifest_version",
                "plugin_binding_id",
                "plugin_binding_version",
                "plugin_config_hash",
                "plugin_index_digest",
            },
        }
        async with session_factory() as db:
            nullable_rows = (
                await db.execute(
                    text(
                        "SELECT table_schema, table_name, column_name, is_nullable "
                        "FROM information_schema.columns "
                        "WHERE (table_schema, table_name) IN "
                        "(('wes_biz', 'workline_sessions'), "
                        "('wes_runtime', 'execution_sessions'), "
                        "('wes_runtime', 'execution_work_items'))"
                    )
                )
            ).all()
        nullability = {(schema, table, column): nullable for schema, table, column, nullable in nullable_rows}
        for (schema, table), pin_fields in pin_fields_by_table.items():
            assert {
                column for (row_schema, row_table, column) in nullability if (row_schema, row_table) == (schema, table)
            } >= pin_fields
            assert all(nullability[(schema, table, field)] == "NO" for field in pin_fields)

        definition = list_workline_capability_definitions()[0]
        async with session_factory() as db:
            workline = WorkLine(
                line_code="IT-RUNTIME-BINDING-INVARIANT",
                line_name="Runtime Binding Invariant",
                line_type=LineType.AUTO,
                plugin_key=definition.plugin_key,
                contract_version=definition.contract_version,
                is_active=True,
            )
            db.add(workline)
            await db.flush()
            binding = WorklinePluginBinding(
                workline_id=workline.id,
                plugin_key=definition.plugin_key,
                contract_version=definition.contract_version,
                binding_version=1,
                typed_config_json={},
                typed_config_hash="d" * 64,
                provider_profile_snapshot_json=[],
                device_snapshot_json=[],
                generated_index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
                environment="test",
                activated_at=datetime(2026, 7, 27, 12),
                activated_by="integration-test",
                activated_reason="mandatory-binding-invariant",
            )
            db.add(binding)
            await db.flush()
            workline.active_plugin_binding_id = binding.id
            workline.active_plugin_binding_version = binding.binding_version
            workline.active_plugin_config_hash = binding.typed_config_hash
            workline.active_plugin_index_digest = binding.generated_index_digest
            workline.active_plugin_provider_requirements_json = []
            valid_session = WorklineSession(
                session_code="IT-RUNTIME-BINDING-VALID",
                workline_id=workline.id,
                plugin_key=binding.plugin_key,
                contract_version=binding.contract_version,
                plugin_binding_id=binding.id,
                plugin_binding_version=binding.binding_version,
                plugin_config_hash=binding.typed_config_hash,
                plugin_index_digest=binding.generated_index_digest,
            )
            valid_execution = ExecutionSession(
                workline_id=workline.id,
                plugin_key=binding.plugin_key,
                manifest_version=binding.contract_version,
                plugin_binding_id=binding.id,
                plugin_binding_version=binding.binding_version,
                plugin_config_hash=binding.typed_config_hash,
                plugin_index_digest=binding.generated_index_digest,
            )
            db.add_all([valid_session, valid_execution])
            await db.flush()
            valid_correlation = ExecutionCorrelation(
                correlation_id="IT-RUNTIME-BINDING-VALID",
                execution_session_id=valid_execution.id,
                trace_id="IT-RUNTIME-BINDING-VALID",
            )
            work_item_correlation_ids = [
                *(f"IT-RUNTIME-BINDING-WORK-ITEM-MISSING-{index}" for index in range(1, 7)),
                "IT-RUNTIME-BINDING-WORK-ITEM-BAD-FK",
                "IT-RUNTIME-BINDING-WORK-ITEM-VALID-COPY",
            ]
            db.add_all(
                [
                    valid_correlation,
                    *(
                        ExecutionCorrelation(
                            correlation_id=correlation_id,
                            execution_session_id=valid_execution.id,
                            trace_id=correlation_id,
                        )
                        for correlation_id in work_item_correlation_ids
                    ),
                ]
            )
            await db.flush()
            valid_work_item = ExecutionWorkItem(
                execution_session_id=valid_execution.id,
                correlation_id=valid_correlation.correlation_id,
                plugin_key=binding.plugin_key,
                manifest_version=binding.contract_version,
                plugin_binding_id=binding.id,
                plugin_binding_version=binding.binding_version,
                plugin_config_hash=binding.typed_config_hash,
                plugin_index_digest=binding.generated_index_digest,
                object_type="material",
                object_key="IT-RUNTIME-BINDING-VALID",
                current_step="INGRESS",
            )
            db.add(valid_work_item)
            await db.commit()
            workline_id = workline.id
            binding_id = binding.id
            execution_session_id = valid_execution.id
            workline_session_id = valid_session.id
            execution_work_item_id = valid_work_item.id

        assert (
            workline_id is not None
            and binding_id is not None
            and execution_session_id is not None
            and workline_session_id is not None
            and execution_work_item_id is not None
        )

        async def clone_valid_row(
            schema: str,
            table: str,
            source_id: int,
            *,
            omitted_column: str | None = None,
            replacements: dict[str, Any] | None = None,
        ) -> None:
            replacements = replacements or {}
            async with session_factory.begin() as db:
                columns = list(
                    (
                        await db.execute(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema = :schema AND table_name = :table "
                                "AND column_name <> 'id' AND is_generated = 'NEVER' "
                                "ORDER BY ordinal_position"
                            ),
                            {"schema": schema, "table": table},
                        )
                    ).scalars()
                )
                columns = [column for column in columns if column != omitted_column]
                quoted_columns = [f'"{column.replace(chr(34), chr(34) * 2)}"' for column in columns]
                parameters: dict[str, Any] = {"source_id": source_id}
                select_expressions: list[str] = []
                for index, (column, quoted_column) in enumerate(zip(columns, quoted_columns, strict=True)):
                    if column not in replacements:
                        select_expressions.append(quoted_column)
                        continue
                    parameter_name = f"replacement_{index}"
                    parameters[parameter_name] = replacements[column]
                    select_expressions.append(f":{parameter_name}")
                await db.execute(
                    text(
                        f'INSERT INTO "{schema}"."{table}" ({", ".join(quoted_columns)}) '
                        f'SELECT {", ".join(select_expressions)} FROM "{schema}"."{table}" '
                        "WHERE id = :source_id"
                    ),
                    parameters,
                )

        for index, field in enumerate(
            sorted(pin_fields_by_table[("wes_biz", "workline_sessions")]),
            start=1,
        ):
            with pytest.raises(IntegrityError):
                await clone_valid_row(
                    "wes_biz",
                    "workline_sessions",
                    workline_session_id,
                    omitted_column=field,
                    replacements={"session_code": f"IT-RUNTIME-BINDING-SESSION-MISSING-{index}"},
                )

        for field in sorted(pin_fields_by_table[("wes_runtime", "execution_sessions")]):
            with pytest.raises(IntegrityError):
                await clone_valid_row(
                    "wes_runtime",
                    "execution_sessions",
                    execution_session_id,
                    omitted_column=field,
                )

        for index, field in enumerate(
            sorted(pin_fields_by_table[("wes_runtime", "execution_work_items")]),
            start=1,
        ):
            with pytest.raises(IntegrityError):
                await clone_valid_row(
                    "wes_runtime",
                    "execution_work_items",
                    execution_work_item_id,
                    omitted_column=field,
                    replacements={"correlation_id": f"IT-RUNTIME-BINDING-WORK-ITEM-MISSING-{index}"},
                )

        invalid_binding_id = binding_id + 1_000_000
        with pytest.raises(IntegrityError):
            await clone_valid_row(
                "wes_biz",
                "workline_sessions",
                workline_session_id,
                replacements={
                    "session_code": "IT-RUNTIME-BINDING-SESSION-BAD-FK",
                    "plugin_binding_id": invalid_binding_id,
                },
            )
        with pytest.raises(IntegrityError):
            await clone_valid_row(
                "wes_runtime",
                "execution_sessions",
                execution_session_id,
                replacements={"plugin_binding_id": invalid_binding_id},
            )
        with pytest.raises(IntegrityError):
            await clone_valid_row(
                "wes_runtime",
                "execution_work_items",
                execution_work_item_id,
                replacements={
                    "correlation_id": "IT-RUNTIME-BINDING-WORK-ITEM-BAD-FK",
                    "plugin_binding_id": invalid_binding_id,
                },
            )

        await clone_valid_row(
            "wes_biz",
            "workline_sessions",
            workline_session_id,
            replacements={"session_code": "IT-RUNTIME-BINDING-SESSION-VALID-COPY"},
        )
        await clone_valid_row("wes_runtime", "execution_sessions", execution_session_id)
        await clone_valid_row(
            "wes_runtime",
            "execution_work_items",
            execution_work_item_id,
            replacements={"correlation_id": "IT-RUNTIME-BINDING-WORK-ITEM-VALID-COPY"},
        )

        async with session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(WorklineSession)) == 2
            assert await db.scalar(select(func.count()).select_from(ExecutionSession)) == 2
            assert await db.scalar(select(func.count()).select_from(ExecutionWorkItem)) == 2

    asyncio.run(_with_database(scenario))


async def _seed_worklines(db: AsyncSession) -> SeededInventory:
    definition = list_workline_capability_definitions()[0]
    foundation = WorkLine(
        line_code="IT-INVENTORY-FOUNDATION",
        line_name="Inventory Foundation",
        line_type=LineType.AUTO,
        plugin_key=definition.plugin_key,
        contract_version=definition.contract_version,
        is_active=True,
    )
    linked = WorkLine(
        line_code="IT-INVENTORY-LINKED",
        line_name="Inventory Linked",
        line_type=LineType.AUTO,
        plugin_key=definition.plugin_key,
        contract_version=definition.contract_version,
        is_active=True,
    )
    db.add_all([foundation, linked])
    await db.flush()
    foundation_binding = WorklinePluginBinding(
        workline_id=foundation.id,
        plugin_key=definition.plugin_key,
        contract_version=definition.contract_version,
        binding_version=1,
        typed_config_json={},
        typed_config_hash="a" * 64,
        provider_profile_snapshot_json=[],
        device_snapshot_json=[],
        generated_index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        environment="production",
        activated_at=datetime(2026, 7, 17, 8),
        activated_by="integration-test",
        activated_reason="inventory-contract",
    )
    linked_binding = WorklinePluginBinding(
        workline_id=linked.id,
        plugin_key=definition.plugin_key,
        contract_version=definition.contract_version,
        binding_version=1,
        typed_config_json={},
        typed_config_hash="b" * 64,
        provider_profile_snapshot_json=[],
        device_snapshot_json=[],
        generated_index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        environment="production",
        activated_at=datetime(2026, 7, 17, 8),
        activated_by="integration-test",
        activated_reason="inventory-contract",
    )
    db.add_all([foundation_binding, linked_binding])
    await db.flush()
    foundation.active_plugin_binding_id = foundation_binding.id
    foundation.active_plugin_binding_version = foundation_binding.binding_version
    foundation.active_plugin_config_hash = foundation_binding.typed_config_hash
    foundation.active_plugin_index_digest = foundation_binding.generated_index_digest
    foundation.active_plugin_provider_requirements_json = ["WMS@v1"]
    linked.active_plugin_binding_id = linked_binding.id
    linked.active_plugin_binding_version = linked_binding.binding_version
    linked.active_plugin_config_hash = linked_binding.typed_config_hash
    linked.active_plugin_index_digest = linked_binding.generated_index_digest
    linked.active_plugin_provider_requirements_json = []
    execution_session = ExecutionSession(
        workline_id=foundation.id,
        plugin_key=foundation_binding.plugin_key,
        manifest_version=foundation_binding.contract_version,
        plugin_binding_id=foundation_binding.id,
        plugin_binding_version=foundation_binding.binding_version,
        plugin_config_hash=foundation_binding.typed_config_hash,
        plugin_index_digest=foundation_binding.generated_index_digest,
    )
    db.add(execution_session)
    await db.flush()
    correlation = ExecutionCorrelation(
        correlation_id="IT-INVENTORY-FOUNDATION-CORRELATION",
        execution_session_id=execution_session.id,
        trace_id="IT-INVENTORY-FOUNDATION-TRACE",
    )
    db.add(correlation)
    await db.flush()
    db.add_all(
        [
            ExecutionWorkItem(
                execution_session_id=execution_session.id,
                correlation_id=correlation.correlation_id,
                plugin_key=foundation_binding.plugin_key,
                manifest_version=foundation_binding.contract_version,
                plugin_binding_id=foundation_binding.id,
                plugin_binding_version=foundation_binding.binding_version,
                plugin_config_hash=foundation_binding.typed_config_hash,
                plugin_index_digest=foundation_binding.generated_index_digest,
                object_type="material",
                object_key="IT-MATERIAL-1",
                current_step="INGRESS",
            ),
            RuntimeIntentLog(
                execution_session_id=execution_session.id,
                correlation_id=correlation.correlation_id,
                provider_code="WMS",
                target_domain="wms_integration",
                target_action="query",
                idempotency_key="IT-INVENTORY-INTENT-1",
                request_hash="c" * 64,
                dispatch_key="IT-INVENTORY-INTENT-1",
            ),
        ]
    )
    device = Device(
        device_code="IT-INVENTORY-DEVICE",
        device_name="Inventory Device",
        work_line_id=linked.id,
        device_role="INVENTORY_TEST",
        device_status=DeviceStatus.IDLE,
    )
    db.add(device)
    await db.flush()
    assert (
        foundation.id is not None and linked.id is not None and device.id is not None and linked_binding.id is not None
    )
    return SeededInventory(
        foundation_workline_id=foundation.id,
        linked_workline_id=linked.id,
        device_id=device.id,
        plugin_key=linked_binding.plugin_key,
        contract_version=linked_binding.contract_version,
        plugin_binding_id=linked_binding.id,
        plugin_binding_version=linked_binding.binding_version,
        plugin_config_hash=linked_binding.typed_config_hash,
        plugin_index_digest=linked_binding.generated_index_digest,
    )


def _session(seed: SeededInventory, status: SessionStatus, index: int) -> WorklineSession:
    return WorklineSession(
        session_code=f"IT-INV-SESSION-{status.value}-{index}",
        workline_id=seed.linked_workline_id,
        plugin_key=seed.plugin_key,
        contract_version=seed.contract_version,
        plugin_binding_id=seed.plugin_binding_id,
        plugin_binding_version=seed.plugin_binding_version,
        plugin_config_hash=seed.plugin_config_hash,
        plugin_index_digest=seed.plugin_index_digest,
        status=status,
    )


def _command(seed: SeededInventory, status: CommandStatus, index: int) -> DeviceCommand:
    return DeviceCommand(
        command_code=f"IT-INV-COMMAND-{status.value}-{index}",
        device_id=seed.device_id,
        workline_id=seed.linked_workline_id,
        task_type="INVENTORY_TEST",
        status=status,
    )


def _outbox(seed: SeededInventory, status: SystemOutboxStatus, index: int) -> SystemOutbox:
    dispatching = status is SystemOutboxStatus.DISPATCHING
    return SystemOutbox(
        workline_id=seed.linked_workline_id,
        dispatch_type=SystemOutboxDispatchType.INTERNAL_SIGNAL,
        dispatch_key=f"it-inv-outbox-{status.value}-{index}",
        target_type=SystemOutboxTargetType.INTERNAL_SERVICE,
        target_code="inventory-test",
        provider_profile_identity="test.inventory.v1",
        operation_identity="test.inventory.internal-signal",
        status=status,
        lease_owner_token=f"inventory-test-owner:{index}" if dispatching else None,
        lease_expires_at=datetime(2099, 1, 1) if dispatching else None,
    )


def _inbox(seed: SeededInventory, status: str, index: int) -> RuntimeInbox:
    return RuntimeInbox(
        workline_id=seed.linked_workline_id,
        provider_code="INVENTORY_TEST",
        event_type="INVENTORY_TEST",
        source_event_id=f"it-inv-inbox-{status}-{index}",
        payload_hash=f"hash-{status}-{index}",
        payload_json={},
        payload_schema_version=1,
        claim_bucket_key=f"inventory-{index}",
        kind="INTERNAL_EVENT",
        status=status,
        received_at=index + 1,
    )


def _hold(seed: SeededInventory, status: RuntimeHoldStatus, index: int, *, blocking: bool = True) -> RuntimeHold:
    return RuntimeHold(
        hold_type=RuntimeHoldType.MANUAL_HOLD,
        status=status,
        blocking=blocking,
        workline_id=seed.linked_workline_id,
        source_kind="INVENTORY_TEST",
        source_reason="INVENTORY_TEST",
        source_idempotency_key=f"it-inv-hold-{status.value}-{index}-{blocking}",
    )


async def _seed_status_matrix(db: AsyncSession, seed: SeededInventory) -> None:
    records: list[Any] = []
    records.extend(_session(seed, status, index) for index, status in enumerate((*SESSION_ACTIVE, *SESSION_TERMINAL)))
    records.extend(_command(seed, status, index) for index, status in enumerate((*COMMAND_ACTIVE, *COMMAND_TERMINAL)))
    records.extend(_outbox(seed, status, index) for index, status in enumerate((*OUTBOX_ACTIVE, *OUTBOX_TERMINAL)))
    records.extend(_inbox(seed, status, index) for index, status in enumerate((*INBOX_ACTIVE, *INBOX_TERMINAL)))
    records.extend(_hold(seed, status, index) for index, status in enumerate((*HOLD_ACTIVE, *HOLD_TERMINAL)))
    # active status 但 blocking=false 也必须排除，锁定 RuntimeHold 的复合 active 合同。
    records.append(_hold(seed, RuntimeHoldStatus.OPEN, 99, blocking=False))
    db.add_all(records)
    await db.commit()


def _item(report: Any, workline_id: int) -> Any:
    return next(item for item in report.worklines if item.workline_id == workline_id)


async def _database_snapshot(db: AsyncSession, seed: SeededInventory) -> dict[str, Any]:
    models = (WorkLine, WorklineSession, DeviceCommand, SystemOutbox, RuntimeInbox, RuntimeHold)
    counts = {model.__name__: int(await db.scalar(select(func.count()).select_from(model)) or 0) for model in models}
    return {
        "counts": counts,
        "worklines": tuple(
            (
                row.id,
                row.line_code,
                row.line_name,
                row.plugin_key,
                row.contract_version,
                row.run_mode,
                row.is_active,
                row.version,
            )
            for row in (await db.execute(select(WorkLine).order_by(WorkLine.id))).scalars()
        ),
        "sessions": tuple(
            (row.session_code, row.status, row.workline_id)
            for row in (await db.execute(select(WorklineSession).order_by(WorklineSession.id))).scalars()
        ),
        "commands": tuple(
            (row.command_code, row.status, row.workline_id)
            for row in (await db.execute(select(DeviceCommand).order_by(DeviceCommand.id))).scalars()
        ),
        "outboxes": tuple(
            (row.dispatch_key, row.status, row.workline_id)
            for row in (await db.execute(select(SystemOutbox).order_by(SystemOutbox.id))).scalars()
        ),
        "inboxes": tuple(
            (row.id, row.status, row.workline_id)
            for row in (await db.execute(select(RuntimeInbox).order_by(RuntimeInbox.id))).scalars()
        ),
        "holds": tuple(
            (row.source_idempotency_key, row.status, row.blocking, row.workline_id)
            for row in (await db.execute(select(RuntimeHold).order_by(RuntimeHold.id))).scalars()
        ),
    }


class _ObservingRepository:
    def __init__(self, *, listed: asyncio.Event | None = None, resume: asyncio.Event | None = None) -> None:
        self._listed = listed
        self._resume = resume
        self.transaction_settings: dict[str, str] = {}

    async def get_list(self, db: AsyncSession, **kwargs: Any) -> tuple[int, list[Any]]:
        rows = await db.execute(
            text(
                "SELECT current_setting('transaction_isolation'), "
                "current_setting('transaction_read_only'), "
                "current_setting('statement_timeout'), "
                "current_setting('idle_in_transaction_session_timeout')"
            )
        )
        isolation, read_only, statement_timeout, idle_timeout = rows.one()
        self.transaction_settings = {
            "isolation": isolation,
            "read_only": read_only,
            "statement_timeout": statement_timeout,
            "idle_timeout": idle_timeout,
        }
        result = await workline_repository.get_list(db, **kwargs)
        if self._listed is not None and self._resume is not None:
            self._listed.set()
            await self._resume.wait()
        return result

    async def get_unfinished_workload_summary(self, db: AsyncSession, workline_id: int) -> dict[str, Any]:
        return await workline_repository.get_unfinished_workload_summary(db, workline_id)


async def _build_via_cli(database_url: str, repository: _ObservingRepository | None = None) -> Any:
    service = WorklineMigrationInventoryService(
        repository=repository or _ObservingRepository(),
        extension_reference_repository=workline_plugin_binding_repository,
    )
    with (
        patch.object(inventory_cli, "settings", SimpleNamespace(DATABASE_URL=database_url, APP_ENV="test")),
        patch.object(inventory_cli, "workline_migration_inventory_service", service),
    ):
        return await inventory_cli.build_report()


async def _complete_concurrent_inventory(
    *,
    inventory_task: asyncio.Task[Any],
    listed: asyncio.Event,
    resume: asyncio.Event,
    writer: Callable[[], Awaitable[None]],
    timeout: float,
) -> Any:
    """完成并发写入并保证 inventory task 在任何退出路径都被回收。"""

    try:
        await asyncio.wait_for(listed.wait(), timeout=timeout)
        await writer()
        resume.set()
        return await asyncio.wait_for(inventory_task, timeout=timeout)
    finally:
        resume.set()
        if not inventory_task.done():
            inventory_task.cancel()
            with suppress(asyncio.CancelledError):
                await inventory_task
        elif not inventory_task.cancelled():
            inventory_task.exception()


def test_postgresql_status_matrix_samples_transaction_and_no_write_contract() -> None:
    async def scenario(database_url: str, session_factory: async_sessionmaker[AsyncSession]) -> None:
        async with session_factory() as db:
            seed = await _seed_worklines(db)
            await _seed_status_matrix(db, seed)
            before = await _database_snapshot(db, seed)

        observer = _ObservingRepository()
        report = await asyncio.wait_for(_build_via_cli(database_url, observer), timeout=20)
        foundation = _item(report, seed.foundation_workline_id)
        linked = _item(report, seed.linked_workline_id)

        assert foundation.foundation_ready is True
        assert foundation.runtime_references.total == 0
        assert foundation.active_plugin_binding_version == 1
        assert foundation.active_plugin_config_hash == "a" * 64
        assert foundation.active_plugin_index_digest == WORKLINE_PLUGIN_INDEX_DIGEST
        assert foundation.provider_requirements == ("WMS@v1",)
        assert tuple(reference.type.value for reference in foundation.runtime_extension_references) == (
            "INTENT",
            "WORK_ITEM",
        )
        assert {reference.plugin_key for reference in foundation.runtime_extension_references} == {
            foundation.plugin_key
        }
        assert linked.runtime_references.model_dump(exclude={"sample"}) == {
            "sessions": len(SESSION_ACTIVE),
            "commands": len(COMMAND_ACTIVE),
            "outboxes": len(OUTBOX_ACTIVE),
            "inboxes": len(INBOX_ACTIVE),
            "runtime_holds": len(HOLD_ACTIVE),
            "total": sum(map(len, (SESSION_ACTIVE, COMMAND_ACTIVE, OUTBOX_ACTIVE, INBOX_ACTIVE, HOLD_ACTIVE))),
        }
        assert linked.runtime_references.sample is not None
        assert linked.runtime_references.sample.type is WorklineRuntimeReferenceType.SESSION
        assert linked.runtime_references.sample.reference == f"IT-INV-SESSION-{SESSION_ACTIVE[0].value}-0"
        assert linked.runtime_references.sample.status == SESSION_ACTIVE[0].value
        reference_issues = [
            issue
            for issue in linked.issues
            if issue.code is WorklineMigrationInventoryIssueCode.RUNTIME_REFERENCES_PRESENT
        ]
        assert len(reference_issues) == 1
        assert reference_issues[0].severity is WorklineMigrationInventorySeverity.BLOCKER
        assert reference_issues[0].workline_id == seed.linked_workline_id
        assert linked.foundation_ready is False
        assert report.foundation_ready is False
        assert observer.transaction_settings == {
            "isolation": "repeatable read",
            "read_only": "on",
            "statement_timeout": "5s",
            "idle_timeout": "15s",
        }

        async with session_factory() as db:
            after = await _database_snapshot(db, seed)
        assert len(before["worklines"]) == 2
        assert after == before

    asyncio.run(_with_database(scenario))


def test_postgresql_sample_priority_falls_through_all_reference_types() -> None:
    async def scenario(database_url: str, session_factory: async_sessionmaker[AsyncSession]) -> None:
        async with session_factory() as db:
            seed = await _seed_worklines(db)
            session = _session(seed, SessionStatus.RUNNING, 0)
            command = _command(seed, CommandStatus.PENDING, 0)
            outbox = _outbox(seed, SystemOutboxStatus.NEW, 0)
            inbox = _inbox(seed, "RECEIVED", 0)
            hold = _hold(seed, RuntimeHoldStatus.OPEN, 0)
            db.add_all([session, command, outbox, inbox, hold])
            await db.commit()

            transitions = (
                (WorklineRuntimeReferenceType.SESSION, session, SessionStatus.COMPLETED),
                (WorklineRuntimeReferenceType.COMMAND, command, CommandStatus.COMPLETED),
                (WorklineRuntimeReferenceType.OUTBOX, outbox, SystemOutboxStatus.SENT),
                (WorklineRuntimeReferenceType.INBOX, inbox, "PROCESSED"),
                (WorklineRuntimeReferenceType.RUNTIME_HOLD, hold, RuntimeHoldStatus.RESOLVED),
            )
            observed: list[WorklineRuntimeReferenceType] = []
            for expected_type, record, terminal_status in transitions:
                report = await asyncio.wait_for(_build_via_cli(database_url), timeout=20)
                sample = _item(report, seed.linked_workline_id).runtime_references.sample
                assert sample is not None
                observed.append(sample.type)
                assert sample.type is expected_type
                record.status = terminal_status
                await db.commit()

            assert tuple(observed) == (
                WorklineRuntimeReferenceType.SESSION,
                WorklineRuntimeReferenceType.COMMAND,
                WorklineRuntimeReferenceType.OUTBOX,
                WorklineRuntimeReferenceType.INBOX,
                WorklineRuntimeReferenceType.RUNTIME_HOLD,
            )

    asyncio.run(_with_database(scenario))


def test_postgresql_each_repository_status_is_counted_only_when_active() -> None:
    async def scenario(_database_url: str, session_factory: async_sessionmaker[AsyncSession]) -> None:
        async with session_factory() as db:
            seed = await _seed_worklines(db)
            await db.commit()

            cases: Sequence[tuple[str, Any, bool]] = (
                *(
                    ("sessions", _session(seed, status, index), status in SESSION_ACTIVE)
                    for index, status in enumerate(SessionStatus)
                ),
                *(
                    ("commands", _command(seed, status, index), status in COMMAND_ACTIVE)
                    for index, status in enumerate(CommandStatus)
                ),
                *(
                    ("outboxes", _outbox(seed, status, index), status in OUTBOX_ACTIVE)
                    for index, status in enumerate(SystemOutboxStatus)
                ),
                *(
                    ("inboxes", _inbox(seed, status, index), status in INBOX_ACTIVE)
                    for index, status in enumerate((*INBOX_ACTIVE, *INBOX_TERMINAL))
                ),
                *(
                    ("runtime_holds", _hold(seed, status, index), status in HOLD_ACTIVE)
                    for index, status in enumerate(RuntimeHoldStatus)
                ),
            )
            for key, record, is_active in cases:
                db.add(record)
                await db.flush()
                summary = await workline_repository.get_unfinished_workload_summary(db, seed.linked_workline_id)
                assert summary["by_type"][key] == int(is_active), (key, record.status)
                assert summary["count"] == int(is_active), (key, record.status)
                await db.delete(record)
                await db.flush()

    asyncio.run(_with_database(scenario))


@pytest.mark.parametrize(
    ("reference_type", "factory", "expected_reference", "expected_status"),
    (
        (
            WorklineRuntimeReferenceType.SESSION,
            lambda seed: _session(seed, SessionStatus.RUNNING, 0),
            "IT-INV-SESSION-RUNNING-0",
            "RUNNING",
        ),
        (
            WorklineRuntimeReferenceType.COMMAND,
            lambda seed: _command(seed, CommandStatus.PENDING, 0),
            "IT-INV-COMMAND-PENDING-0",
            "PENDING",
        ),
        (
            WorklineRuntimeReferenceType.OUTBOX,
            lambda seed: _outbox(seed, SystemOutboxStatus.NEW, 0),
            "it-inv-outbox-NEW-0",
            "NEW",
        ),
        (WorklineRuntimeReferenceType.INBOX, lambda seed: _inbox(seed, "RECEIVED", 0), None, "RECEIVED"),
        (
            WorklineRuntimeReferenceType.RUNTIME_HOLD,
            lambda seed: _hold(seed, RuntimeHoldStatus.OPEN, 0),
            "count:1",
            "ACTIVE_BLOCKING",
        ),
    ),
)
def test_postgresql_normalizes_each_reference_sample(
    reference_type: WorklineRuntimeReferenceType,
    factory: Callable[[SeededInventory], Any],
    expected_reference: str | None,
    expected_status: str,
) -> None:
    async def scenario(database_url: str, session_factory: async_sessionmaker[AsyncSession]) -> None:
        async with session_factory() as db:
            seed = await _seed_worklines(db)
            record = factory(seed)
            db.add(record)
            await db.flush()
            normalized_reference = (
                str(record.id) if reference_type is WorklineRuntimeReferenceType.INBOX else expected_reference
            )
            assert normalized_reference is not None
            await db.commit()
        report = await asyncio.wait_for(_build_via_cli(database_url), timeout=20)
        sample = _item(report, seed.linked_workline_id).runtime_references.sample
        assert sample is not None
        assert (sample.type, sample.reference, sample.status) == (
            reference_type,
            normalized_reference,
            expected_status,
        )

    asyncio.run(_with_database(scenario))


def test_postgresql_repeatable_read_snapshot_excludes_concurrent_inbox_until_next_transaction() -> None:
    async def scenario(database_url: str, session_factory: async_sessionmaker[AsyncSession]) -> None:
        async with session_factory() as db:
            seed = await _seed_worklines(db)
            await db.commit()

        listed = asyncio.Event()
        resume = asyncio.Event()
        observer = _ObservingRepository(listed=listed, resume=resume)
        inventory_task = asyncio.create_task(_build_via_cli(database_url, observer))

        async def insert_inbox() -> None:
            async with session_factory() as writer:
                writer.add(_inbox(seed, "RECEIVED", 0))
                await writer.commit()

        current_report = await _complete_concurrent_inventory(
            inventory_task=inventory_task,
            listed=listed,
            resume=resume,
            writer=insert_inbox,
            timeout=10,
        )
        assert _item(current_report, seed.linked_workline_id).runtime_references.inboxes == 0
        next_report = await asyncio.wait_for(_build_via_cli(database_url), timeout=20)
        assert _item(next_report, seed.linked_workline_id).runtime_references.inboxes == 1

    asyncio.run(_with_database(scenario))


def test_concurrent_inventory_cleanup_cancels_task_when_writer_fails() -> None:
    async def scenario() -> None:
        listed = asyncio.Event()
        resume = asyncio.Event()

        async def inventory() -> None:
            listed.set()
            await resume.wait()
            await asyncio.Future()

        async def failing_writer() -> None:
            raise RuntimeError("受控 writer 故障")

        inventory_task = asyncio.create_task(inventory())
        with pytest.raises(RuntimeError, match="受控 writer 故障"):
            await _complete_concurrent_inventory(
                inventory_task=inventory_task,
                listed=listed,
                resume=resume,
                writer=failing_writer,
                timeout=1,
            )
        assert resume.is_set()
        assert inventory_task.done()
        assert inventory_task.cancelled()

    asyncio.run(scenario())


def test_postgresql_read_only_transaction_rejects_noop_update_by_sqlstate() -> None:
    async def scenario(database_url: str, session_factory: async_sessionmaker[AsyncSession]) -> None:
        async with session_factory() as db:
            seed = await _seed_worklines(db)
            await db.commit()

        engine = create_async_engine(database_url, isolation_level="REPEATABLE READ")
        read_only_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with read_only_factory() as db:
                with pytest.raises(DBAPIError) as exc_info:
                    async with db.begin():
                        await db.execute(text("SET TRANSACTION READ ONLY"))
                        await db.execute(
                            update(WorkLine)
                            .where(WorkLine.id == seed.linked_workline_id)
                            .values(line_name=WorkLine.line_name)
                        )
                driver_error = exc_info.value.orig
                sqlstate = getattr(driver_error, "sqlstate", None) or getattr(driver_error, "pgcode", None)
                assert sqlstate == "25006"
        finally:
            await engine.dispose()

        async with session_factory() as verifier:
            line = await verifier.get(WorkLine, seed.linked_workline_id)
            assert line is not None and line.line_name == "Inventory Linked"

    asyncio.run(_with_database(scenario))
