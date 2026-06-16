"""PostgreSQL gated tests for SMT inbound handoff recovery access paths."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects import postgresql

from src.app.workline.models import LineType, WorkLine, WorkLineRunMode, WorklineSession
from src.app.workline.models.inbox import InboxKind, InboxStatus, SourceSystem, WorklineInbox
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.workline.repositories.smt_inbound_handoff_repository import SmtInboundHandoffRepository
from src.app.workline.services.smt_inbound_handoff_service import SmtInboundHandoffService
from src.utils.timezone import timezone
from src.workline_plugins.smt_sorting_inbound.constants import (
    SMT_SORTING_INBOUND_CONTRACT_VERSION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

RECOVERY_INDEX = "ix_smt_inbound_handoff_source_items_post_claim_recovery"


class _RouteService:
    def __init__(self, workline: WorkLine) -> None:
        self.workline = workline

    async def resolve_route(self, _db: object, **_kwargs: Any) -> object:
        return SimpleNamespace(
            kind="SELECTED",
            manual_hold=False,
            retryable=False,
            selected_workline=self.workline,
            selected_workline_id=self.workline.id,
            selected_workline_code=self.workline.line_code,
            source_station_code="SOURCE_STATION_A",
            source_position_code="SOURCE_STATION_A",
            route_evidence={
                "manifest_contract_version": SMT_SORTING_INBOUND_CONTRACT_VERSION,
                "source_rack_position_code": "SOURCE_STATION_A",
                "source_station_code": "SOURCE_STATION_A",
                "source_position_code": "SOURCE_STATION_A",
                "target_rack_position_code": "TARGET_STATION",
            },
            failure_code=None,
            failure_message=None,
            next_attempt_at=None,
        )


def _iter_plan_nodes(plan_node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield plan_node
    for child in plan_node.get("Plans", []):
        if isinstance(child, dict):
            yield from _iter_plan_nodes(child)


async def _explain_plan_nodes(db: AsyncSession, statement: Any) -> list[dict[str, Any]]:
    compiled = statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    result = await db.execute(text(f"EXPLAIN (ANALYZE, FORMAT JSON) {compiled}"))
    raw_plan = result.scalar_one()
    plan_doc = json.loads(raw_plan) if isinstance(raw_plan, str) else raw_plan
    return list(_iter_plan_nodes(plan_doc[0]["Plan"]))


def _uses_index(plan_nodes: list[dict[str, Any]], *, index_name: str) -> bool:
    for node in plan_nodes:
        if "Index" in str(node.get("Node Type", "")) and node.get("Index Name") == index_name:
            return True
    return False


async def _cleanup_handoff_rows(db: AsyncSession, *, test_prefix: str) -> None:
    demand_ids = select(SmtInboundHandoffDemand.id).where(
        SmtInboundHandoffDemand.rack_release_id.like(f"{test_prefix}%")
    )
    await db.execute(
        delete(SmtInboundHandoffSourceItem).where(SmtInboundHandoffSourceItem.handoff_demand_id.in_(demand_ids))
    )
    await db.execute(
        delete(SmtInboundHandoffDemand).where(SmtInboundHandoffDemand.rack_release_id.like(f"{test_prefix}%"))
    )
    await db.execute(delete(WorklineInbox).where(WorklineInbox.trace_id.like(f"{test_prefix}%")))
    await db.commit()


def _target_workline(line_code: str) -> WorkLine:
    return WorkLine(
        line_code=line_code,
        line_name=f"{line_code} 分拣线",
        line_type=LineType.AUTO,
        plugin_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
        contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
        run_mode=WorkLineRunMode.AUTO,
        runtime_status=WorkLineRuntimeStatus.READY,
        is_active=True,
        config={
            "smt_inbound_handoff_route": {
                "enabled": True,
                "priority": 1,
                "source_station_code": "SOURCE_STATION_A",
            }
        },
    )


async def _seed_ready_item(
    db: AsyncSession,
    *,
    test_prefix: str,
    suffix: str,
) -> SmtInboundHandoffSourceItem:
    demand = SmtInboundHandoffDemand(
        demand_key=f"{test_prefix}:smt-inbound-handoff:{suffix}",
        rack_release_id=f"{test_prefix}:release:{suffix}",
        single_layer_rack_code=f"{test_prefix}:rack:{suffix}",
        status=SmtInboundHandoffDemandStatus.READY_FOR_SORTING,
        trace_id=f"{test_prefix}:claim:{suffix}",
        bin_snapshots_json={"bins": []},
    )
    db.add(demand)
    await db.flush()
    item = SmtInboundHandoffSourceItem(
        handoff_demand_id=demand.id,
        item_key=f"{test_prefix}:item:{suffix}",
        bin_code=f"{test_prefix}:BIN-{suffix}",
        bin_cell_code=f"A0{suffix}",
        material_identity_key=f"{test_prefix}:MAT-{suffix}",
        pkg_code=f"{test_prefix}:PKG-{suffix}",
        status=SmtInboundHandoffSourceItemStatus.READY,
    )
    db.add(item)
    await db.flush()
    return item


async def _claim_source_item(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    workline: WorkLine,
    trace_id: str,
) -> str:
    service = SmtInboundHandoffService(route_service=_RouteService(workline))
    async with session_factory() as db:
        async with db.begin():
            result = await service.claim_next_source_item(db, trace_id=trace_id)
            return str(result.kind)


@pytest.mark.asyncio
async def test_stuck_source_item_recovery_explain_uses_post_claim_partial_index(
    integration_session_factory: async_sessionmaker[AsyncSession],
    test_prefix: str,
) -> None:
    repository = SmtInboundHandoffRepository()
    base_time = timezone.now_for_db()

    async with integration_session_factory() as db:
        demand = SmtInboundHandoffDemand(
            demand_key=f"{test_prefix}:smt-inbound-handoff",
            rack_release_id=f"{test_prefix}:release",
            single_layer_rack_code=f"{test_prefix}:rack",
            status=SmtInboundHandoffDemandStatus.CLAIMED_BY_SORTING,
            trace_id=f"{test_prefix}:recovery",
            bin_snapshots_json={"bins": []},
        )
        db.add(demand)
        await db.flush()
        inbox = WorklineInbox(
            kind=InboxKind.INTERNAL_EVENT,
            source_system=SourceSystem.SYSTEM,
            idempotency_key=f"{test_prefix}:source-pick-request",
            trace_id=f"{test_prefix}:recovery",
            claim_bucket_key=f"session:{test_prefix}",
            payload_json={"message_type": "INTERNAL_EVENT", "event_type": "SORTING_SOURCE_PICK_REQUESTED"},
            status=InboxStatus.PROCESSED,
            received_at=base_time - timedelta(minutes=10),
        )
        db.add(inbox)
        await db.flush()
        db.add_all(
            [
                SmtInboundHandoffSourceItem(
                    handoff_demand_id=demand.id,
                    item_key=f"{test_prefix}:item:history:{index}",
                    status=SmtInboundHandoffSourceItemStatus.PICKED,
                    updated_at=base_time - timedelta(days=1, seconds=index),
                )
                for index in range(2000)
            ]
        )
        db.add(
            SmtInboundHandoffSourceItem(
                handoff_demand_id=demand.id,
                item_key=f"{test_prefix}:item:stuck",
                status=SmtInboundHandoffSourceItemStatus.PICK_REQUESTED,
                source_pick_inbox_id=inbox.id,
                updated_at=base_time - timedelta(minutes=20),
            )
        )
        await db.commit()
        await db.execute(text("ANALYZE wes_biz.smt_inbound_handoff_source_items"))
        await db.commit()

    statement = repository.build_stuck_source_item_recovery_statement(
        now=base_time,
        stale_after_seconds=300,
        limit=10,
    )

    async with integration_session_factory() as db:
        transaction = await db.begin()
        try:
            plan_nodes = await _explain_plan_nodes(db, statement)
        finally:
            await transaction.rollback()
        await _cleanup_handoff_rows(db, test_prefix=test_prefix)

    assert _uses_index(plan_nodes, index_name=RECOVERY_INDEX)


@pytest.mark.asyncio
async def test_concurrent_handoff_claims_same_target_workline_create_one_session_and_inbox(
    integration_session_factory: async_sessionmaker[AsyncSession],
    test_prefix: str,
) -> None:
    async with integration_session_factory() as db:
        workline = _target_workline(f"{test_prefix}:WL-SMT-SORT-CLAIM")
        db.add(workline)
        await db.flush()
        await _seed_ready_item(db, test_prefix=test_prefix, suffix="1")
        await _seed_ready_item(db, test_prefix=test_prefix, suffix="2")
        await db.commit()

    results = await asyncio.gather(
        _claim_source_item(integration_session_factory, workline=workline, trace_id=f"{test_prefix}:claim:1"),
        _claim_source_item(integration_session_factory, workline=workline, trace_id=f"{test_prefix}:claim:2"),
    )

    async with integration_session_factory() as db:
        session_count = int(
            (
                await db.execute(
                    select(func.count()).select_from(WorklineSession).where(WorklineSession.workline_id == workline.id)
                )
            ).scalar_one()
            or 0
        )
        inbox_count = int(
            (
                await db.execute(
                    select(func.count()).select_from(WorklineInbox).where(WorklineInbox.workline_id == workline.id)
                )
            ).scalar_one()
            or 0
        )
        claimed_items = list(
            (
                await db.execute(
                    select(SmtInboundHandoffSourceItem).where(
                        SmtInboundHandoffSourceItem.target_workline_id == workline.id,
                        SmtInboundHandoffSourceItem.status == SmtInboundHandoffSourceItemStatus.PICK_REQUESTED,
                    )
                )
            )
            .scalars()
            .all()
        )

    assert sorted(results) == ["CLAIMED", "RETRY"]
    assert session_count == 1
    assert inbox_count == 1
    assert len(claimed_items) == 1
