"""Handling operation 服务。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from src.app.handling.models import (
    HandlingMoveStatus,
    HandlingObjectType,
    HandlingOperationStatus,
    HandlingStepKind,
    HandlingStepStatus,
)
from src.app.handling.repositories import (
    HandlingMoveRepository,
    HandlingOperationRepository,
    HandlingStepRepository,
    handling_move_repository,
    handling_operation_repository,
    handling_step_repository,
)
from src.app.handling.services.completion_policy import resolve_request_completion_policy
from src.app.handling.services.gateway import WmsRcsHandlingGateway, wms_rcs_handling_gateway
from src.app.sys.models import (
    SystemOutboxDispatchType,
    SystemOutboxStatus,
    SystemOutboxTargetType,
)
from src.app.sys.repositories import SystemOutboxRepository, system_outbox_repository
from src.utils.timezone import timezone
from src.utils.value_normalization import optional_int

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_FORBIDDEN_CALLER_MOVE_FIELDS = {
    "dispatch_key",
    "external_target_code",
    "outbox_id",
    "payload_json",
    "http_headers",
    "url",
    "auth",
    "retry",
}


class HandlingOperationService:
    """系统级 Handling operation 服务。"""

    def __init__(
        self,
        *,
        operation_repository: HandlingOperationRepository = handling_operation_repository,
        move_repository: HandlingMoveRepository = handling_move_repository,
        step_repository: HandlingStepRepository = handling_step_repository,
        outbox_repository: SystemOutboxRepository = system_outbox_repository,
        gateway: WmsRcsHandlingGateway = wms_rcs_handling_gateway,
    ) -> None:
        self.operation_repository = operation_repository
        self.move_repository = move_repository
        self.step_repository = step_repository
        self.outbox_repository = outbox_repository
        self.gateway = gateway

    async def request_bin_operation(
        self,
        db: AsyncSession,
        *,
        operation_type: str,
        operation_key: str,
        moves: Sequence[Mapping[str, Any]],
        trace_id: str,
        workline_id: int | None = None,
        workline_code: str | None = None,
        material_session_id: int | None = None,
        carrier_type: str = "CTU",
        carrier_code: str | None = None,
        timeout_seconds: int | None = None,
    ) -> Any:
        """创建系统级 bin handling operation。

        调用方只传内部 move 语义；WMS/RCS 目标、dispatch_key 和 outbox 包络由本服务生成。
        """

        operation_key = _required_text(operation_key, "operation_key")
        operation_type = _required_text(operation_type, "operation_type")
        trace_id = _required_text(trace_id, "trace_id")
        if not moves:
            raise ValueError("moves 不能为空")
        _reject_external_move_fields(moves)

        existing = await self.operation_repository.get_by_operation_key(db, operation_key)
        if existing is not None:
            _validate_existing_operation_matches_request(
                existing,
                operation_type=operation_type,
                workline_id=workline_id,
                workline_code=workline_code,
                material_session_id=material_session_id,
                trace_id=trace_id,
                carrier_type=carrier_type,
                carrier_code=carrier_code,
                moves=moves,
                timeout_seconds=timeout_seconds,
            )
            return existing

        now = timezone.now_for_db()
        operation = await self.operation_repository.create(
            db,
            {
                "operation_key": operation_key,
                "operation_type": operation_type,
                "object_type": HandlingObjectType.BIN.value,
                "operation_status": HandlingOperationStatus.REQUESTED.value,
                "completion_policy": resolve_request_completion_policy(operation_type),
                "workline_id": workline_id,
                "workline_code": _optional_text(workline_code),
                "material_session_id": material_session_id,
                "trace_id": trace_id,
                "carrier_type": carrier_type,
                "carrier_code": _optional_text(carrier_code),
                "request_json": {
                    "moves": [dict(move) for move in moves],
                    "timeout_seconds": timeout_seconds,
                },
                "requested_at": now,
                "started_at": now,
            },
        )

        for sequence_no, raw_move in enumerate(moves, start=1):
            move = await self.move_repository.create(
                db,
                {
                    "operation_id": operation.id,
                    "operation_key": operation_key,
                    "sequence_no": sequence_no,
                    "object_type": HandlingObjectType.BIN.value,
                    "move_status": HandlingMoveStatus.REQUESTED.value,
                    "rack_code": _optional_text(raw_move.get("rack_code")),
                    "rack_slot_code": _optional_text(raw_move.get("rack_slot_code")),
                    "bin_code": _optional_text(raw_move.get("bin_code")),
                    "placeholder_key": _optional_text(raw_move.get("placeholder_key")),
                    "candidate_authorized_bin_ids": _candidate_ids(raw_move.get("candidate_authorized_bin_ids")),
                    "source_type": _required_text(raw_move.get("source_type"), "move.source_type"),
                    "source_code": _required_text(raw_move.get("source_code"), "move.source_code"),
                    "target_type": _required_text(raw_move.get("target_type"), "move.target_type"),
                    "target_code": _required_text(raw_move.get("target_code"), "move.target_code"),
                    "carrier_type": carrier_type,
                    "carrier_code": _optional_text(carrier_code),
                    "required": bool(raw_move.get("required", True)),
                    "metadata_json": dict(raw_move),
                },
            )
            envelope = self.gateway.build_ctu_move_envelope(operation=operation, move=move, sequence_no=sequence_no)
            outbox = await self.outbox_repository.create(
                db,
                {
                    "session_id": material_session_id,
                    "workline_id": workline_id,
                    "operation_domain": "HANDLING",
                    "operation_key": operation_key,
                    "dispatch_type": SystemOutboxDispatchType.EXTERNAL_HTTP.value,
                    "dispatch_key": _required_text(envelope.get("dispatch_key"), "dispatch_key"),
                    "target_type": SystemOutboxTargetType.HTTP_ENDPOINT.value,
                    "target_code": _required_text(envelope.get("target_code"), "target_code"),
                    "payload_json": _mapping(envelope.get("payload_json"), "payload_json"),
                    "status": SystemOutboxStatus.NEW.value,
                    "trace_id": trace_id,
                },
            )
            await self.step_repository.create(
                db,
                {
                    "operation_id": operation.id,
                    "operation_key": operation_key,
                    "move_id": move.id,
                    "sequence_no": sequence_no,
                    "step_key": f"{operation_key}:external:{sequence_no}",
                    "step_kind": HandlingStepKind.EXTERNAL_REQUEST.value,
                    "step_status": HandlingStepStatus.REQUESTED.value,
                    "dispatch_key": envelope["dispatch_key"],
                    "outbox_id": outbox.id,
                    "target_code": envelope["target_code"],
                    "request_json": envelope["payload_json"],
                    "started_at": now,
                },
            )

        return operation


def _required_text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} 不能为空")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _reject_external_move_fields(moves: Sequence[Mapping[str, Any]]) -> None:
    for move in moves:
        leaked = _FORBIDDEN_CALLER_MOVE_FIELDS.intersection(move)
        if leaked:
            raise ValueError(f"插件不得传入外部派发字段: {', '.join(sorted(leaked))}")


def _validate_existing_operation_matches_request(
    existing: Any,
    *,
    operation_type: str,
    workline_id: int | None,
    workline_code: str | None,
    material_session_id: int | None,
    trace_id: str,
    carrier_type: str,
    carrier_code: str | None,
    moves: Sequence[Mapping[str, Any]],
    timeout_seconds: int | None,
) -> None:
    mismatches: list[str] = []
    if _optional_text(getattr(existing, "operation_type", None)) != operation_type:
        mismatches.append("operation_type")
    if optional_int(getattr(existing, "workline_id", None)) != workline_id:
        mismatches.append("workline_id")
    if _optional_text(getattr(existing, "workline_code", None)) != _optional_text(workline_code):
        mismatches.append("workline_code")
    if optional_int(getattr(existing, "material_session_id", None)) != material_session_id:
        mismatches.append("material_session_id")
    if _optional_text(getattr(existing, "trace_id", None)) != trace_id:
        mismatches.append("trace_id")
    if _optional_text(getattr(existing, "carrier_type", None)) != _optional_text(carrier_type):
        mismatches.append("carrier_type")
    if _optional_text(getattr(existing, "carrier_code", None)) != _optional_text(carrier_code):
        mismatches.append("carrier_code")

    request_json = getattr(existing, "request_json", None)
    if not isinstance(request_json, Mapping):
        mismatches.append("request_json")
    else:
        if _move_specs_for_compare(request_json.get("moves")) != _move_specs_for_compare(moves):
            mismatches.append("moves")
        if request_json.get("timeout_seconds") != timeout_seconds:
            mismatches.append("timeout_seconds")

    if mismatches:
        raise ValueError(f"operation_key 已存在但请求上下文不一致: {', '.join(mismatches)}")


def _move_specs_for_compare(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        return None
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            return None
        result.append(dict(item))
    return result


def _candidate_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("candidate_authorized_bin_ids 必须是字符串列表")
    return [_required_text(item, "candidate_authorized_bin_ids[]") for item in value]


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} 必须是对象")
    return dict(value)


handling_operation_service = HandlingOperationService()


__all__ = ["HandlingOperationService", "handling_operation_service"]
