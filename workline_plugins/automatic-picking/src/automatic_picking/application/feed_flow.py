"""冻结清单的有序投料；仅当前分段的确定事实允许推进游标。"""

from src.app.transport.contracts import BinMove, HandoffPosition, RackBinSlot


class AutomaticPickingFeedFlow:
    def __init__(self, repository, creator):
        self.repository, self.creator = repository, creator

    async def advance_in_session(self, db, *, line, feed, ready):
        if feed.phase in {"FEED_COMPLETE", "CANCELLED", "RECONCILING"}:
            return 0
        if feed.transport_task_id is not None:
            state = await self.repository.chunk_state(db, feed)
            if state is None:
                raise ValueError("current feed lost its Transport")
            if state.status in {"FAILED", "REJECTED", "RECONCILING"}:
                feed.phase = "RECONCILING"
                return 1
            chunk = ready.bins[feed.offset : feed.offset + 4]
            if (
                state.status != "SUCCEEDED"
                or state.outcome_version != state.published_outcome_version
                or not await self.repository.chunk_delivered(db, feed, chunk)
            ):
                return 0
            end = feed.offset + len(chunk)
            if end == len(ready.bins) or await self.repository.source_cancelled(db, feed):
                feed.offset = end
                feed.phase = "FEED_COMPLETE" if end == len(ready.bins) else "CANCELLED"
                # 末段原身份仍供迟到扫码验证，不用历史位置或另一批次代替。
                return 1
            if not await self.repository.chunk_entered(db, feed, chunk):
                return 0
            feed.offset = end
            feed.transport_task_id = None
        if await self.repository.source_cancelled(db, feed):
            feed.phase = "CANCELLED"
            return 1
        if await self.repository.channel_busy(db, line.id):
            return 0
        chunk = ready.bins[feed.offset : feed.offset + 4]
        moves = tuple(
            BinMove(
                m.bin_code,
                RackBinSlot(m.source_locator.rack_id, m.source_locator.rack_face, m.source_locator.slot_id),
                HandoffPosition(line.position_bindings["INLET"]["location_id"]),
            )
            for m in chunk
        )
        result = await self.creator.create(
            db,
            workline_id=line.id,
            picking_task_id=feed.picking_task_id,
            source_evidence_id=feed.response_evidence_id,
            correlation_id=f"auto-feed:{feed.id}:{feed.offset}",
            step="AUTOMATIC_PICKING_FEED",
            resource_fence_id=feed.operation_id,
            moves=moves,
        )
        feed.transport_task_id, feed.phase = result.transport_task_id, "FEEDING"
        return 1
