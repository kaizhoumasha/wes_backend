"""Runtime Hold query service."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from src.app.device.models.command import DeviceCommand
from src.app.workline.models.runtime_hold import (
    MaterialDisposition,
    NgReturnItem,
    RuntimeHold,
    RuntimeHoldType,
)
from src.app.workline.models.runtime_hold_api import (
    NgReasonOption,
    NgReturnItemResponse,
    RuntimeHoldBlocker,
    RuntimeHoldDetailResponse,
    RuntimeHoldReleaseEligibility,
    RuntimeHoldSource,
    RuntimeHoldSummary,
)
from src.app.workline.models.session import WorklineSession
from src.app.workline.repositories.runtime_hold_repository import RuntimeHoldRepository, runtime_hold_repository
from src.app.workline.services.runtime_hold_release_service import (
    RuntimeHoldReleaseService,
    runtime_hold_release_service,
)
from src.app.workline.services.trace_response_builder import build_failed_command_evidence
from src.utils.value_normalization import as_dict, optional_enum_str
from src.workline_plugin_registry import get_workline_plugin_definition
from src.workline_runtime.ng_reason import BUILTIN_NG_REASONS, NgReasonDefinition

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_CALLBACK_TIMEOUT_CHECKS = [
    "device_inspected",
    "physical_state_confirmed",
    "inventory_or_position_reconciled",
    "late_callback_reviewed",
]
_DISPATCH_ACK_CHECKS = [
    "device_reachable_checked",
    "command_code_checked",
    "physical_state_confirmed",
    "safe_to_release_blocked_work",
]


class RuntimeHoldQueryService:
    """Read-only Runtime Hold projection builder."""

    def __init__(
        self,
        *,
        repository: RuntimeHoldRepository | None = None,
        release_service: RuntimeHoldReleaseService | None = None,
    ) -> None:
        self.repository = repository or runtime_hold_repository
        self.release_service = release_service or runtime_hold_release_service

    async def get_detail(self, db: AsyncSession, hold_id: int) -> RuntimeHoldDetailResponse | None:
        """读取 RuntimeHold detail；不产生任何写入。"""

        hold = await self.repository.get_by_id(db, hold_id)
        if hold is None:
            return None

        session = await db.get(WorklineSession, hold.session_id) if hold.session_id is not None else None
        command = await db.get(DeviceCommand, hold.source_command_id) if hold.source_command_id is not None else None
        blockers = [
            self._blocker(item)
            for item in await self.repository.get_active_blocking_by_workline(db, hold.workline_id)
            if item.id != hold.id
        ]
        return RuntimeHoldDetailResponse(
            summary=self._summary(hold),
            source=self._source(hold),
            evidence_snapshot_json=as_dict(hold.evidence_snapshot_json),
            release_evidence_json=as_dict(hold.release_evidence_json),
            failed_command_evidence=build_failed_command_evidence(command),
            release_eligibility=self._release_eligibility(hold, session=session),
            blockers=blockers,
        )

    async def list_holds(
        self,
        db: AsyncSession,
        *,
        workline_id: int | None = None,
        session_id: int | None = None,
        status: str | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[RuntimeHoldSummary]:
        """读取 RuntimeHold 列表；包含 session/workline 级 Hold。"""

        holds = await self.repository.list_holds(
            db,
            workline_id=workline_id,
            session_id=session_id,
            status=status,
            active_only=active_only,
            limit=limit,
        )
        return [self._summary(hold) for hold in holds]

    def list_ng_reasons(self, *, plugin_key: str | None = None) -> list[NgReasonOption]:
        """返回插件 NG reasons + 系统 fallback。"""

        reasons: list[NgReasonDefinition] = []
        definition = get_workline_plugin_definition(plugin_key)
        if definition is not None:
            reasons.extend(definition.manifest.list_ng_reasons())
        reasons.extend(BUILTIN_NG_REASONS)
        return [self._reason_option(item) for item in reasons]

    async def list_ng_return_items(
        self,
        db: AsyncSession,
        *,
        runtime_hold_id: int | None = None,
        status: str | None = None,
        material_identity_key: str | None = None,
        limit: int = 100,
    ) -> list[NgReturnItemResponse]:
        """查询 NG return item。"""

        items = await self.repository.list_ng_return_items(
            db,
            runtime_hold_id=runtime_hold_id,
            status=status,
            material_identity_key=material_identity_key,
            limit=limit,
        )
        return [self._ng_return_item(item) for item in items]

    def _release_eligibility(
        self,
        hold: RuntimeHold,
        *,
        session: WorklineSession | None,
    ) -> RuntimeHoldReleaseEligibility:
        can_resolve = hold.is_active_blocking
        return RuntimeHoldReleaseEligibility(
            can_resolve=can_resolve,
            required_checks=self._required_checks(hold),
            allowed_resolutions=["COMPLETED", "FAILED", "CANCELLED"] if can_resolve else [],
            allowed_material_dispositions=self._allowed_dispositions(hold) if can_resolve else [],
            latest_evidence_hash=self.release_service.build_latest_evidence_hash(hold, session=session),
            reason=None if can_resolve else f"RuntimeHold status is {optional_enum_str(hold.status)}",
        )

    def _required_checks(self, hold: RuntimeHold) -> list[str]:
        if hold.hold_type == RuntimeHoldType.SAFETY_ESTOP:
            return ["estop_button_reset", "area_safe"]
        if hold.source_reason in {"COMMAND_ACK_EXHAUSTED", "OUTBOX_DISPATCH_FAILED"}:
            return list(_DISPATCH_ACK_CHECKS)
        return list(_CALLBACK_TIMEOUT_CHECKS)

    def _allowed_dispositions(self, hold: RuntimeHold) -> list[str]:
        if hold.hold_type == RuntimeHoldType.SAFETY_ESTOP:
            return [MaterialDisposition.CONTINUE.value]
        return [MaterialDisposition.CONTINUE.value, MaterialDisposition.RETURN_TO_NG.value]

    def _summary(self, hold: RuntimeHold) -> RuntimeHoldSummary:
        return RuntimeHoldSummary(
            id=cast("int", hold.id),
            hold_type=optional_enum_str(hold.hold_type) or "",
            status=optional_enum_str(hold.status) or "",
            blocking=hold.blocking,
            workline_id=hold.workline_id,
            session_id=hold.session_id,
            trace_id=hold.trace_id,
            plugin_key=hold.plugin_key,
            contract_version=hold.contract_version,
            source_reason=hold.source_reason,
            material_disposition=optional_enum_str(hold.material_disposition),
            ng_reason_code=hold.ng_reason_code,
            ng_reason_label=hold.ng_reason_label,
            version=hold.version,
            created_at=getattr(hold, "created_at", None),
            resolved_at=hold.resolved_at,
            resolved_by=hold.resolved_by,
        )

    def _source(self, hold: RuntimeHold) -> RuntimeHoldSource:
        return RuntimeHoldSource(
            source_kind=hold.source_kind,
            source_reason=hold.source_reason,
            source_inbox_id=hold.source_inbox_id,
            source_outbox_id=hold.source_outbox_id,
            source_command_id=hold.source_command_id,
            source_device_id=hold.source_device_id,
            source_idempotency_key=hold.source_idempotency_key,
        )

    def _blocker(self, hold: RuntimeHold) -> RuntimeHoldBlocker:
        return RuntimeHoldBlocker(
            id=cast("int", hold.id),
            hold_type=optional_enum_str(hold.hold_type) or "",
            status=optional_enum_str(hold.status) or "",
            source_reason=hold.source_reason,
            session_id=hold.session_id,
            source_device_id=hold.source_device_id,
        )

    def _reason_option(self, reason: NgReasonDefinition) -> NgReasonOption:
        return NgReasonOption(
            source=reason.source.value,
            code=reason.canonical_code,
            label=reason.label,
            plugin_key=reason.plugin_key,
            contract_version=reason.contract_version,
            maps_from=list(reason.maps_from),
        )

    def _ng_return_item(self, item: NgReturnItem) -> NgReturnItemResponse:
        return NgReturnItemResponse(
            id=cast("int", item.id),
            source_workline_id=item.source_workline_id,
            source_session_id=item.source_session_id,
            source_command_id=item.source_command_id,
            source_event_id=item.source_event_id,
            material_identity_key=item.material_identity_key,
            material_identity_json=as_dict(item.material_identity_json),
            physical_handoff_evidence_json=as_dict(item.physical_handoff_evidence_json),
            disposition=optional_enum_str(item.disposition) or "",
            ng_reason_source=optional_enum_str(item.ng_reason_source) or "",
            ng_reason_code=item.ng_reason_code or "",
            ng_reason_label=item.ng_reason_label or "",
            operator_note=item.operator_note,
            created_from_runtime_hold_id=item.created_from_runtime_hold_id,
            status=optional_enum_str(item.status) or "",
            confirmed_by=item.confirmed_by,
            confirmed_at=item.confirmed_at,
            created_at=getattr(item, "created_at", None),
        )


runtime_hold_query_service = RuntimeHoldQueryService()


__all__ = [
    "RuntimeHoldQueryService",
    "runtime_hold_query_service",
]
