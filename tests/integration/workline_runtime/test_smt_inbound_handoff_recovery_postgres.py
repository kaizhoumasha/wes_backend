"""PostgreSQL gated tests for SMT inbound handoff recovery access paths."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.dialects import postgresql

from src.app.workline.models.inbox import InboxKind, InboxStatus, SourceSystem, WorklineInbox
from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItem,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.workline.repositories.smt_inbound_handoff_repository import SmtInboundHandoffRepository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

RECOVERY_INDEX = "ix_smt_inbound_handoff_source_items_post_claim_recovery"


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
