"""Transport 联调任务清理：预检、审计与原子删除。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.sys.services.audit_service import audit_log_service
from src.app.transport.contracts import TRANSPORT_DEBUG_CALLER_WORKLINE_ID, TransportContractError, TransportTaskKind
from src.app.transport_debug.debug_reset import (
    TransportDebugResetPreview,
    TransportDebugResetResult,
    TransportDebugStep,
    TransportDebugStepConfirmation,
    normalize_transport_task_id,
)
from src.core.exceptions import NotFoundException

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.transport.models import TransportTask
    from src.app.transport.repository import TransportRepository
    from src.app.transport_debug.repository import TransportDebugRunRepository


def _validated_transport_task_id(value: str) -> str:
    try:
        return normalize_transport_task_id(value)
    except ValueError as exc:
        raise TransportContractError("transport_task_id must contain 1..80 non-NUL characters") from exc


class TransportDebugResetService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        transport_repository: TransportRepository,
        debug_run_repository: TransportDebugRunRepository,
    ) -> None:
        self._sessions = session_factory
        self._repository = transport_repository
        self._debug_run_repository = debug_run_repository

    async def preview_debug_task_reset(self, transport_task_id: str) -> TransportDebugResetPreview:
        """预览指定 TransportTask 的本地 Transport 链路。"""

        task_id = _validated_transport_task_id(transport_task_id)
        async with self._sessions() as db:
            return await self._build_debug_reset_preview(db, task_id, for_update=False)

    async def reset_debug_task(
        self,
        transport_task_id: str,
        confirmation: TransportDebugStepConfirmation | None = None,
    ) -> TransportDebugResetResult:
        """锁定并原子删除指定 TransportTask 的完整本地 Transport 链路。"""

        task_id = _validated_transport_task_id(transport_task_id)
        async with self._sessions.begin() as db:
            _ = await self._build_debug_reset_preview(db, task_id, for_update=True)
            if await self._debug_run_repository.is_task_linked_to_active_run(db, task_id):
                raise TransportContractError("active transport debug run task cannot be reset")
            if confirmation is not None:
                task = await self._repository.get_task(db, task_id, for_update=True)
                if task is None:
                    raise NotFoundException(resource_type="TransportTask", resource_id=task_id)
                await self._audit_debug_step_confirmation(db, task, confirmation)
            (
                callback_receipt_count,
                evidence_count,
                position_projection_count,
                member_count,
                task_count,
            ) = await self._repository.delete_debug_task_aggregate(db, task_id)
            if task_count != 1:
                raise RuntimeError(f"TransportTask delete count is invalid: {task_count}")
        return TransportDebugResetResult(
            transport_task_id=task_id,
            deleted_callback_receipt_count=callback_receipt_count,
            deleted_evidence_count=evidence_count,
            deleted_position_projection_count=position_projection_count,
            deleted_member_count=member_count,
        )

    async def _audit_debug_step_confirmation(
        self,
        db: AsyncSession,
        task: TransportTask,
        confirmation: TransportDebugStepConfirmation,
    ) -> None:
        if task.caller_json.get("workline_id") != TRANSPORT_DEBUG_CALLER_WORKLINE_ID:
            raise TransportContractError("operator confirmation requires a TRANSPORT_DEBUG task")
        expected_kind = {
            TransportDebugStep.RACK_TO_STATION: TransportTaskKind.RACK_MOVE.value,
            TransportDebugStep.BINS_TO_INFEED: TransportTaskKind.BIN_MOVE.value,
            TransportDebugStep.BINS_TO_RACK: TransportTaskKind.BIN_MOVE.value,
            TransportDebugStep.RACK_TO_STORAGE: TransportTaskKind.RACK_MOVE.value,
        }[confirmation.step]
        if task.kind != expected_kind:
            raise TransportContractError("operator confirmation step does not match Transport task kind")
        if not _debug_step_matches_frozen_request(task, confirmation.step):
            raise TransportContractError("operator confirmation step does not match frozen Transport request")
        _ = await audit_log_service.create_audit_log(
            db,
            method="POST",
            title="确认 Transport 联调物理步骤",
            path=f"/api/v1/transport/debug-tasks/{task.transport_task_id}/reset",
            args={
                "model": "TransportTask",
                "operation": "debug_step_confirm",
                "record_id": task.transport_task_id,
                "changes": {
                    "source": "OPERATOR_DEBUG",
                    "business_authoritative": False,
                    "step": confirmation.step.value,
                    "assertion": confirmation.assertion,
                    "client_request_id": task.client_request_id,
                    "kind": task.kind,
                    "frozen_targets": _debug_frozen_targets(task),
                },
            },
        )

    async def _build_debug_reset_preview(
        self,
        db: AsyncSession,
        transport_task_id: str,
        *,
        for_update: bool,
    ) -> TransportDebugResetPreview:
        task = await self._repository.get_task(db, transport_task_id, for_update=for_update)
        if task is None:
            raise NotFoundException(resource_type="TransportTask", resource_id=transport_task_id)
        (
            callback_receipt_count,
            evidence_count,
            position_projection_count,
            member_count,
        ) = await self._repository.get_debug_reset_counts(db, transport_task_id)
        return TransportDebugResetPreview(
            transport_task_id=task.transport_task_id,
            status=task.status,
            outcome_version=task.outcome_version,
            evidence_count=evidence_count,
            callback_receipt_count=callback_receipt_count,
            position_projection_count=position_projection_count,
            member_count=member_count,
        )


def _debug_frozen_targets(task: TransportTask) -> list[dict[str, Any]]:
    request = task.request_json
    if task.kind == TransportTaskKind.RACK_MOVE.value:
        return [
            {
                "object_id": request["rack_id"],
                "target": request["target"],
                "arrival_face": request["target_face"],
                "rcs_template_id": request["rcs_template_id"],
            }
        ]
    if task.kind == TransportTaskKind.BIN_MOVE.value:
        return [
            {
                "object_id": move["bin_code"],
                "target": move["target"],
                "arrival_face": move["target"].get("rack_face"),
            }
            for move in request["moves"]
        ]
    raise TransportContractError("operator confirmation supports RACK_MOVE or BIN_MOVE only")


def _debug_step_matches_frozen_request(task: TransportTask, step: TransportDebugStep) -> bool:
    request = task.request_json
    if task.kind == TransportTaskKind.RACK_MOVE.value:
        expected_request = {
            TransportDebugStep.RACK_TO_STATION: {
                "rack_id": "510056",
                "source": {"kind": "ZONE", "location_code": "WH05"},
                "target": {"kind": "RACK_POSITION", "location_code": "KT16"},
                "target_face": "90",
                "rcs_template_id": "CTU01",
            },
            TransportDebugStep.RACK_TO_STORAGE: {
                "rack_id": "510056",
                "source": {"kind": "RACK_POSITION", "location_code": "KT16"},
                "target": {"kind": "ZONE", "location_code": "WH05"},
                "target_face": "90",
                "rcs_template_id": "CTU03",
            },
        }.get(step)
        return expected_request is not None and all(
            request.get(key) == value for key, value in expected_request.items()
        )
    if task.kind == TransportTaskKind.BIN_MOVE.value:
        rack_slots = (
            ("A000001922", "510056A3F2C101"),
            ("A000002653", "510056A2F2C101"),
        )
        expected_moves = {
            TransportDebugStep.BINS_TO_INFEED: [
                {
                    "bin_code": bin_code,
                    "source": {
                        "kind": "RACK_BIN_SLOT",
                        "rack_id": "510056",
                        "rack_face": "90",
                        "slot_id": slot_id,
                    },
                    "target": {"kind": "HANDOFF_POSITION", "location_code": "CNV0301"},
                }
                for bin_code, slot_id in rack_slots
            ],
            TransportDebugStep.BINS_TO_RACK: [
                {
                    "bin_code": bin_code,
                    "source": {"kind": "HANDOFF_POSITION", "location_code": "CNV0302"},
                    "target": {
                        "kind": "RACK_BIN_SLOT",
                        "rack_id": "510056",
                        "rack_face": "90",
                        "slot_id": slot_id,
                    },
                }
                for bin_code, slot_id in rack_slots
            ],
        }.get(step)
        return expected_moves is not None and request.get("moves") == expected_moves
    return False
