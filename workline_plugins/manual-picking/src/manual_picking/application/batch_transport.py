"""WMS 批次 READY 到宿主 Transport BIN_MOVE 的精确转换。"""

from wes_plugin_sdk import BinInboundBatchIntent, BinInboundBatchReady, BinReturnBatchIntent, BinReturnBatchReady

from src.app.transport.contracts import BinMove, HandoffPosition, RackBinSlot


def inbound_moves(
    intent: BinInboundBatchIntent, ready: BinInboundBatchReady, *, inlet_location: str
) -> tuple[BinMove, ...]:
    moves: list[BinMove] = []
    for member in ready.bins:
        source = member.source_locator
        if (source.rack_id, source.rack_face) != (intent.rack_id, intent.rack_face):
            raise ValueError("inbound source rack face differs from frozen WMS request")
        moves.append(
            BinMove(
                member.bin_code,
                RackBinSlot(source.rack_id, source.rack_face, source.slot_id),
                HandoffPosition(inlet_location),
            )
        )
    return tuple(moves)


def return_moves(intent: BinReturnBatchIntent, ready: BinReturnBatchReady) -> tuple[BinMove, ...]:
    if len(ready.moves) > len(intent.return_candidates):
        raise ValueError("return moves must match the frozen FIFO prefix")
    moves: list[BinMove] = []
    for candidate, member in zip(intent.return_candidates, ready.moves, strict=False):
        target = member.target
        if (member.sequence_no, member.bin_code) != (candidate.sequence_no, candidate.bin_code):
            raise ValueError("return moves must match the frozen FIFO prefix")
        if (target.rack_id, target.rack_face) != (intent.rack_id, intent.rack_face):
            raise ValueError("return target rack face differs from frozen WMS request")
        moves.append(
            BinMove(
                member.bin_code,
                HandoffPosition(candidate.source_location_code),
                RackBinSlot(target.rack_id, target.rack_face, target.slot_id),
            )
        )
    return tuple(moves)


__all__ = ["inbound_moves", "return_moves"]
