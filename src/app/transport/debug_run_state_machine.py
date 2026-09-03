"""Transport 自动联调轮次的纯状态机与请求构造。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal

from src.app.transport.contracts import (
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
    TransportRequest,
    TransportTaskStatus,
    ZonePosition,
)
from src.app.transport.debug_run_contracts import TransportDebugRunPhase

if TYPE_CHECKING:
    from src.app.transport.models import TransportDebugRun, TransportDebugRunStep, TransportMember, TransportTask

_CALLER = TransportCaller("TRANSPORT_DEBUG", "TRANSPORT_DEBUG_AUTO")
type _DebugPosition = RackBinSlot | HandoffPosition | RackReference | RackPosition | ZonePosition


@dataclass(frozen=True, slots=True)
class DebugTaskEvaluation:
    disposition: Literal["WAIT", "SUCCEEDED", "FAILED", "ATTENTION"]
    reason_code: str | None = None


def build_debug_transport_request(
    run: TransportDebugRun,
    step: TransportDebugRunStep,
) -> TransportRequest | None:
    phase = _phase(step.phase)
    if phase is TransportDebugRunPhase.WAIT_SCAN12:
        return None
    if not step.client_request_id:
        raise TransportContractError("external debug step requires client_request_id")

    configuration = run.configuration_json
    rack_id = _text(configuration, "rack_id")
    group = _group(configuration, step.group_index)
    face = _text(group, "face")

    if phase is TransportDebugRunPhase.RACK_TO_STATION:
        return MoveRackRequest(
            step.client_request_id,
            _CALLER,
            rack_id,
            RackReference(rack_id),
            RackPosition(_text(configuration, "workstation")),
            face,
            RcsTemplateId(_text(configuration, "rack_out_template")),
        )
    if phase in {TransportDebugRunPhase.BINS_TO_INFEED, TransportDebugRunPhase.BINS_TO_RACK}:
        infeed = HandoffPosition(_text(configuration, "infeed_position"))
        outfeed = HandoffPosition(_text(configuration, "outfeed_position"))
        moves: list[BinMove] = []
        for selection in _bins(group):
            rack_slot = RackBinSlot(rack_id, face, _text(selection, "slot_id"))
            if phase is TransportDebugRunPhase.BINS_TO_INFEED:
                moves.append(BinMove(_text(selection, "bin_id"), rack_slot, infeed))
            else:
                moves.append(BinMove(_text(selection, "bin_id"), outfeed, rack_slot))
        return MoveBinsRequest(step.client_request_id, _CALLER, tuple(moves))
    if phase is TransportDebugRunPhase.ROTATE_TO_NEXT_FACE:
        return RotateRackRequest(
            step.client_request_id,
            _CALLER,
            rack_id,
            RackReference(rack_id),
            face,
            RcsTemplateId(_text(configuration, "rack_rotate_template")),
        )
    if phase is TransportDebugRunPhase.RACK_TO_STORAGE:
        return MoveRackRequest(
            step.client_request_id,
            _CALLER,
            rack_id,
            RackReference(rack_id),
            ZonePosition(_text(configuration, "storage_zone")),
            _text(configuration, "rack_return_face"),
            RcsTemplateId(_text(configuration, "rack_return_template")),
        )
    raise AssertionError("unreachable")


def next_debug_step(
    run: TransportDebugRun,
    completed_step: TransportDebugRunStep,
) -> tuple[str, int] | None:
    phase = _phase(completed_step.phase)
    group_index = _group_index(completed_step.group_index)
    if phase is TransportDebugRunPhase.RACK_TO_STATION:
        return TransportDebugRunPhase.BINS_TO_INFEED.value, group_index
    if phase is TransportDebugRunPhase.BINS_TO_INFEED:
        return TransportDebugRunPhase.WAIT_SCAN12.value, group_index
    if phase is TransportDebugRunPhase.WAIT_SCAN12:
        return TransportDebugRunPhase.BINS_TO_RACK.value, group_index
    if phase is TransportDebugRunPhase.BINS_TO_RACK:
        next_group = group_index + 1
        if next_group < len(_face_groups(run.configuration_json)):
            return TransportDebugRunPhase.ROTATE_TO_NEXT_FACE.value, next_group
        return TransportDebugRunPhase.RACK_TO_STORAGE.value, group_index
    if phase is TransportDebugRunPhase.ROTATE_TO_NEXT_FACE:
        return TransportDebugRunPhase.BINS_TO_INFEED.value, group_index
    if phase is TransportDebugRunPhase.RACK_TO_STORAGE:
        return None
    raise AssertionError("unreachable")


def evaluate_debug_transport_task(
    step: TransportDebugRunStep,
    task: TransportTask,
    members: tuple[TransportMember, ...] | list[TransportMember],
    run: TransportDebugRun,
) -> DebugTaskEvaluation:
    try:
        status = TransportTaskStatus(task.status)
    except ValueError:
        return DebugTaskEvaluation("ATTENTION", "TRANSPORT_STATUS_UNKNOWN")
    if status in {TransportTaskStatus.PENDING, TransportTaskStatus.ACCEPTED}:
        return DebugTaskEvaluation("WAIT")
    if status in {TransportTaskStatus.REJECTED, TransportTaskStatus.FAILED}:
        return DebugTaskEvaluation("FAILED", task.reason_code)
    if status is TransportTaskStatus.RECONCILING:
        return DebugTaskEvaluation("ATTENTION", task.reason_code or "TRANSPORT_RECONCILING")

    try:
        request = build_debug_transport_request(run, step)
    except (TransportContractError, ValueError):
        return DebugTaskEvaluation("ATTENTION", "DEBUG_STEP_CONFIGURATION_INVALID")
    if request is None or task.client_request_id != step.client_request_id or task.kind != request.kind.value:
        return DebugTaskEvaluation("ATTENTION", "TRANSPORT_RESULT_MISMATCH")
    if not _members_match(request, members, run.configuration_json):
        return DebugTaskEvaluation("ATTENTION", "TRANSPORT_RESULT_MISMATCH")
    return DebugTaskEvaluation("SUCCEEDED")


def _members_match(  # noqa: PLR0911 - closed request kinds use separate exact-result exits
    request: TransportRequest,
    members: tuple[TransportMember, ...] | list[TransportMember],
    configuration: dict[str, object],
) -> bool:
    if isinstance(request, MoveBinsRequest):
        expected = {move.bin_id: move for move in request.moves}
        if len(members) != len(expected) or len({member.object_id for member in members}) != len(members):
            return False
        for member in members:
            move = expected.get(member.object_id)
            if move is None or member.object_type != "BIN" or member.status != "SUCCEEDED":
                return False
            if not _member_matches(member, move.source, move.target, None):
                return False
        return True

    if len(members) != 1:
        return False
    member = members[0]
    if member.object_type != "RACK" or member.object_id != request.rack_id or member.status != "SUCCEEDED":
        return False
    if isinstance(request, RotateRackRequest):
        final_position = asdict(RackPosition(_text(configuration, "workstation")))
        frozen_position = final_position if isinstance(request.position, RackReference) else asdict(request.position)
        return (
            not member.position_unknown
            and member.source_json == frozen_position
            and member.target_json == frozen_position
            and member.final_position_json == final_position
            and member.arrival_face == request.target_face
        )
    if isinstance(request, MoveRackRequest) and request.rcs_template_id is RcsTemplateId.CTU03:
        final_position = member.final_position_json
        return (
            not member.position_unknown
            and member.source_json == asdict(request.source)
            and member.target_json == asdict(request.target)
            and isinstance(final_position, dict)
            and final_position.get("kind") == "RACK_POSITION"
            and isinstance(final_position.get("location_code"), str)
            and bool(final_position["location_code"])
            and member.arrival_face == request.target_face
        )
    if not isinstance(request, MoveRackRequest):
        return False
    return _member_matches(member, request.source, request.target, request.target_face)


def _member_matches(
    member: TransportMember,
    source: _DebugPosition,
    target: _DebugPosition,
    arrival_face: str | None,
) -> bool:
    return (
        not member.position_unknown
        and member.source_json == asdict(source)
        and member.target_json == asdict(target)
        and member.final_position_json == asdict(target)
        and member.arrival_face == arrival_face
    )


def _phase(value: str) -> TransportDebugRunPhase:
    try:
        return TransportDebugRunPhase(value)
    except ValueError as error:
        raise TransportContractError(f"unsupported debug run phase: {value}") from error


def _group_index(value: int | None) -> int:
    if type(value) is not int or value < 0:
        raise TransportContractError("debug step group_index is invalid")
    return value


def _face_groups(configuration: dict[str, object]) -> list[dict[str, object]]:
    groups = configuration.get("face_groups")
    if not isinstance(groups, list) or not groups or not all(isinstance(group, dict) for group in groups):
        raise TransportContractError("debug run face_groups are invalid")
    return groups


def _group(configuration: dict[str, object], group_index: int | None) -> dict[str, object]:
    index = _group_index(group_index)
    groups = _face_groups(configuration)
    if index >= len(groups):
        raise TransportContractError("debug step group_index is out of range")
    return groups[index]


def _bins(group: dict[str, object]) -> list[dict[str, object]]:
    bins = group.get("bins")
    if not isinstance(bins, list) or not bins or not all(isinstance(selection, dict) for selection in bins):
        raise TransportContractError("debug run bins are invalid")
    return bins


def _text(values: dict[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise TransportContractError(f"debug run {key} is invalid")
    return value


__all__ = [
    "DebugTaskEvaluation",
    "build_debug_transport_request",
    "evaluate_debug_transport_task",
    "next_debug_step",
]
