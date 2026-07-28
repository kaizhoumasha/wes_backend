"""Runtime mandatory plugin binding DDL PostgreSQL 合同。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.runtime.workline_plugins.registry import list_workline_capability_definitions
from src.app.workline.models import WorkLine, WorklinePluginBinding
from src.app.workline.models.workline import LineType
from tests.integration.test_workline_migration_inventory_postgresql import _with_database

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


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
