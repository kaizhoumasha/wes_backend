"""联调台选择值到现有 Transport 调试合同的固定映射。"""

from __future__ import annotations

from src.app.transport.contracts import (
    TRANSPORT_DEBUG_CALLER_WORKLINE_ID,
    BinMove,
    HandoffPosition,
    MoveBinsRequest,
    MoveRackRequest,
    RackBinSlot,
    RackPosition,
    RackReference,
    RcsTemplateId,
    RotateRackRequest,
    TransportCaller,
    TransportContractError,
    ZonePosition,
)
from src.app.workline_integration_debug.contracts import IntegrationTransportAction, IntegrationTransportActionKind


def _rack_position(value: dict[str, str]):  # type: ignore[no-untyped-def]
    kind = value.get("kind")
    code = value.get("location_code", "")
    if kind == "RACK":
        return RackReference(code)
    if kind == "ZONE":
        return ZonePosition(code)
    if kind == "RACK_POSITION":
        return RackPosition(code)
    raise TransportContractError("rack position kind must be RACK, ZONE, or RACK_POSITION")


def _bin_position(value: dict[str, str]):  # type: ignore[no-untyped-def]
    kind = value.get("kind")
    if kind == "HANDOFF_POSITION":
        return HandoffPosition(value.get("location_code", ""))
    if kind == "RACK_BIN_SLOT":
        return RackBinSlot(
            rack_id=value.get("rack_id", ""),
            rack_face=value.get("rack_face", ""),
            slot_id=value.get("slot_id", ""),
        )
    raise TransportContractError("bin position kind must be HANDOFF_POSITION or RACK_BIN_SLOT")


def build_transport_request(action: IntegrationTransportAction, *, bin_moves: tuple[BinMove, ...] | None = None):
    caller = TransportCaller(TRANSPORT_DEBUG_CALLER_WORKLINE_ID)
    if action.kind is IntegrationTransportActionKind.MOVE_RACK:
        return MoveRackRequest(
            action.client_request_id,
            caller,
            action.rack_id,
            _rack_position(action.source),
            _rack_position(action.target),
            action.target_face,
            RcsTemplateId(action.rcs_template_id),
        )
    if action.kind is IntegrationTransportActionKind.ROTATE_RACK:
        position = _rack_position(action.source)
        if type(position) is not RackPosition:
            raise TransportContractError("rack rotation requires an explicit rack position")
        return RotateRackRequest(
            action.client_request_id,
            caller,
            action.rack_id,
            position,
            action.target_face or "",
            RcsTemplateId(action.rcs_template_id),
        )
    if action.kind is IntegrationTransportActionKind.MOVE_BINS:
        if bin_moves is not None:
            return MoveBinsRequest(action.client_request_id, caller, bin_moves)
        if action.bin_code is None:
            raise TransportContractError("MOVE_BINS requires bin_code")
        return MoveBinsRequest(
            action.client_request_id,
            caller,
            (BinMove(action.bin_code, _bin_position(action.source), _bin_position(action.target)),),
        )
    raise TransportContractError(f"unsupported integration transport action: {action.kind}")


__all__ = ["build_transport_request"]
