"""Runtime Hold release service."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy.exc import IntegrityError

from src.app.device.models.command import CommandResult, CommandStatus, DeviceCommand
from src.app.device.models.device import Device
from src.app.device.repositories import device_command_repository
from src.app.device.services.device_service import DeviceService
from src.app.runtime.orchestration.models.runtime_hold import (
    MaterialDisposition,
    NgReasonSource,
    NgReturnItem,
    NgReturnItemStatus,
    RuntimeHold,
    RuntimeHoldStatus,
    RuntimeHoldType,
)
from src.app.runtime.orchestration.models.session import (
    RuntimeReconciliationResolution,
    RuntimeReconciliationState,
    SessionStatus,
)
from src.app.runtime.orchestration.repositories import workline_session_repository
from src.app.runtime.orchestration.repositories.runtime_hold_repository import runtime_hold_repository
from src.app.runtime.orchestration.repository_wiring import workline_repository
from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    workline_runtime_status_projection_service,
)
from src.app.sys.repositories import system_outbox_repository
from src.utils.timezone import timezone
from src.utils.value_normalization import as_dict, enum_str, optional_int

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.device.repositories import DeviceCommandRepository
    from src.app.runtime.capabilities.material_flow.contracts.ng_reason import NgReasonDefinition
    from src.app.runtime.orchestration.models.runtime_hold_api import ResolveRuntimeHoldRequest
    from src.app.runtime.orchestration.repositories.runtime_hold_repository import RuntimeHoldRepository
    from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
    from src.app.sys.repositories import SystemOutboxRepository
    from src.app.workline.repositories.workline_repository import WorkLineRepository


def _latest_matching_late_callback_data(session: Any | None, command: DeviceCommand) -> dict[str, Any]:
    context = as_dict(getattr(session, "context_json", None))
    evidence = context.get("runtime_reconciliation_late_callback_evidence")
    if not isinstance(evidence, list):
        return {}

    for item in reversed([raw_item for raw_item in evidence if isinstance(raw_item, dict)]):
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        evidence_command_code = item.get("command_code") or payload.get("command_code")
        if evidence_command_code != command.command_code:
            continue
        if enum_str(payload.get("result")) != CommandResult.SUCCESS.value:
            continue
        data = payload.get("data")
        if isinstance(data, dict) and data:
            return dict(data)
    return {}


def _runtime_continue_result_payload(
    request: ResolveRuntimeHoldRequest,
    command: DeviceCommand,
    *,
    session: Any | None = None,
) -> dict[str, Any]:
    payload = as_dict(request.result_payload)
    if payload:
        return payload
    payload = _latest_matching_late_callback_data(session, command)
    if payload:
        return payload
    return as_dict(command.params)


class RuntimeHoldReleaseError(ValueError):
    """Runtime Hold release domain error."""

    def __init__(self, error_code: str, message: str, *, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.data = data


@dataclass(frozen=True)
class ReturnToNgReleaseContext:
    """Server-normalized RETURN_TO_NG facts."""

    material_identity: Any
    ng_reason: NgReasonDefinition
    physical_handoff_evidence: dict[str, Any]


class RuntimeHoldReleaseService:
    """Runtime Hold 解除服务，是 WorkLine 回到 STOPPED 等待 START 的唯一业务入口。"""

    def __init__(
        self,
        *,
        runtime_hold_repo: RuntimeHoldRepository | None = None,
        workline_repo: WorkLineRepository | None = None,
        session_repo: WorklineSessionRepository | None = None,
        outbox_repo: SystemOutboxRepository | None = None,
        command_repo: DeviceCommandRepository | None = None,
        device_service: DeviceService | None = None,
    ) -> None:
        self.runtime_hold_repo = runtime_hold_repo or runtime_hold_repository
        self.workline_repo = workline_repo or workline_repository
        self.session_repo = session_repo or workline_session_repository
        self.outbox_repo = outbox_repo or system_outbox_repository
        self.command_repo = command_repo or device_command_repository
        self.device_service = device_service or DeviceService()

    def build_latest_evidence_hash(self, hold: RuntimeHold, *, session: Any | None = None) -> str:
        """构建可被 GET/API 复用的 deterministic evidence hash。"""

        payload = {
            "evidence_snapshot_json": hold.evidence_snapshot_json or {},
            "late_callback_evidence": self._late_callback_evidence(session),
            "source_refs": {
                "source_kind": hold.source_kind,
                "source_reason": hold.source_reason,
                "source_inbox_id": hold.source_inbox_id,
                "source_outbox_id": hold.source_outbox_id,
                "source_command_id": hold.source_command_id,
                "source_device_id": hold.source_device_id,
                "session_id": hold.session_id,
                "workline_id": hold.workline_id,
            },
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)
        return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"

    async def resolve_hold(
        self,
        db: AsyncSession,
        hold_id: int,
        request: ResolveRuntimeHoldRequest,
        operator_id: int,
        *,
        allow_safety_estop: bool = False,
    ) -> dict[str, Any]:
        """解除 RuntimeHold，并在最后一个 active blocking hold 解除后恢复 WorkLine。"""

        hold = await self.runtime_hold_repo.get_for_update(db, hold_id)
        if hold is None:
            raise ValueError(f"RuntimeHold 不存在: {hold_id}")
        if hold.status == RuntimeHoldStatus.RESOLVED:
            raise RuntimeHoldReleaseError(
                "RUNTIME_HOLD_ALREADY_RESOLVED",
                f"RuntimeHold 已解除: {hold_id}",
            )
        if not hold.is_active_blocking:
            raise ValueError(f"RuntimeHold 当前状态不允许解除: {hold_id}, status={enum_str(hold.status)}")
        if hold.hold_type == RuntimeHoldType.SAFETY_ESTOP and not allow_safety_estop:
            raise RuntimeHoldReleaseError(
                "RUNTIME_HOLD_SAFETY_ESTOP_REQUIRES_CLEAR_ESTOP",
                "SAFETY_ESTOP RuntimeHold must be resolved via clear-estop API",
            )

        workline = await self.workline_repo.get_for_update(db, hold.workline_id)
        if workline is None:
            raise ValueError(f"工作线不存在: {hold.workline_id}")

        session = None
        if hold.session_id is not None:
            session = await self.session_repo.get_for_update(db, hold.session_id)
            if session is None:
                raise ValueError(f"会话不存在: {hold.session_id}")

        if hold.version != request.hold_version:
            raise RuntimeHoldReleaseError(
                "RUNTIME_HOLD_VERSION_CONFLICT",
                f"version conflict: hold_id={hold_id}, current={hold.version}, provided={request.hold_version}",
            )
        latest_evidence_hash = self.build_latest_evidence_hash(hold, session=session)
        if latest_evidence_hash != request.latest_evidence_hash:
            raise RuntimeHoldReleaseError("RUNTIME_HOLD_EVIDENCE_CHANGED", "evidence changed")

        now = timezone.now_for_db()
        self._validate_release_request(request)

        ng_item = None
        return_to_ng_context = None
        if request.material_disposition == MaterialDisposition.RETURN_TO_NG.value:
            return_to_ng_context = await self._prepare_return_to_ng_release(
                db,
                hold=hold,
                request=request,
                operator_id=operator_id,
                confirmed_at=now,
                session=session,
                workline=workline,
            )
            ng_item = await self._create_ng_return_item(
                db,
                hold=hold,
                request=request,
                operator_id=operator_id,
                confirmed_at=now,
                release_context=return_to_ng_context,
            )

        source_command = await self._resolve_source_command(
            db,
            hold=hold,
            request=request,
            resolved_at=now,
            session=session,
        )
        self._write_release_facts(
            hold,
            request=request,
            operator_id=operator_id,
            resolved_at=now,
            ng_item=ng_item,
            return_to_ng_context=return_to_ng_context,
        )
        hold.status = RuntimeHoldStatus.RESOLVED
        hold.resolved_by = operator_id
        hold.resolved_at = now
        created_inbox_id: int | None = None
        if session is not None:
            if source_command is not None and self._should_replay_command_result(
                hold=hold,
                request=request,
            ):
                self._resolve_session_for_command_result_replay(
                    session,
                    request=request,
                    operator_id=operator_id,
                    resolved_at=now,
                    command=source_command,
                )
                created_inbox_id = await self._create_continue_command_result_inbox(
                    db,
                    hold=hold,
                    request=request,
                    command=source_command,
                    session=session,
                )
            else:
                self._resolve_session(session, request=request, operator_id=operator_id, resolved_at=now)

        await self._clear_runtime_device_error(db, hold=hold)

        hold.increment_version()
        await db.flush()
        remaining_holds = await self.runtime_hold_repo.get_active_blocking_by_workline(db, hold.workline_id)
        remaining_active_blocking_holds = len(remaining_holds)
        release_workline_scope = remaining_active_blocking_holds == 0
        if release_workline_scope:
            _ = await self.outbox_repo.park_blocked_by_runtime_hold_until_start(
                db,
                runtime_hold_id=cast("int", hold.id),
                workline_id=hold.workline_id,
            )
            released_outbox_count = 0
            _ = await workline_runtime_status_projection_service.project_stopped_waiting_start(
                db,
                workline_id=hold.workline_id,
            )
        else:
            released_outbox_count = await self.outbox_repo.release_blocked_by_runtime_hold_or_workline(
                db,
                runtime_hold_id=cast("int", hold.id),
                workline_id=hold.workline_id,
                release_workline_scope=False,
            )
            await self._project_remaining_hold_status(db, workline_id=hold.workline_id, remaining_holds=remaining_holds)

        await db.flush()
        runtime_snapshot = await workline_runtime_status_projection_service.runtime_status_snapshot(
            db,
            workline_id=hold.workline_id,
        )
        return {
            "hold_id": hold.id,
            "status": enum_str(hold.status),
            "workline_id": hold.workline_id,
            "workline_runtime_status": runtime_snapshot.runtime_status,
            "remaining_active_blocking_holds": remaining_active_blocking_holds,
            "released_outbox_count": released_outbox_count,
            "ng_return_item_id": getattr(ng_item, "id", None),
            "created_inbox_id": created_inbox_id,
        }

    def _validate_release_request(self, request: ResolveRuntimeHoldRequest) -> None:
        if not request.checks or not all(request.checks.values()):
            raise ValueError("release checklist must all be true")
        if not request.operator_note.strip():
            raise ValueError("operator_note is required")

        if request.material_disposition == MaterialDisposition.CONTINUE.value:
            return

        if request.resolution != SessionStatus.FAILED.value:
            raise ValueError("RETURN_TO_NG resolution must be FAILED")
        if request.ng_reason is None:
            raise RuntimeHoldReleaseError(
                "RUNTIME_HOLD_MISSING_RELEASE_EVIDENCE",
                "RETURN_TO_NG requires ng_reason",
            )
        evidence = request.physical_handoff_evidence
        if evidence is None:
            raise RuntimeHoldReleaseError(
                "RUNTIME_HOLD_MISSING_RELEASE_EVIDENCE",
                "RETURN_TO_NG requires physical_handoff_evidence",
            )
        if not evidence.line_clear_checked or not evidence.late_callback_reviewed:
            raise RuntimeHoldReleaseError(
                "RUNTIME_HOLD_MISSING_RELEASE_EVIDENCE",
                "RETURN_TO_NG requires line_clear_checked and late_callback_reviewed",
            )

    async def _create_ng_return_item(
        self,
        db: AsyncSession,
        *,
        hold: RuntimeHold,
        request: ResolveRuntimeHoldRequest,
        operator_id: int,
        confirmed_at: Any,
        release_context: ReturnToNgReleaseContext,
    ) -> NgReturnItem:
        if hold.session_id is None:
            raise ValueError("RETURN_TO_NG requires hold.session_id")
        # ng_reason and physical_handoff_evidence already validated by _validate_release_request

        material_identity = release_context.material_identity
        ng_reason = release_context.ng_reason
        material_identity_key = cast("str", material_identity.idempotency_key)
        item: NgReturnItem | None = None
        try:
            async with db.begin_nested():
                item = NgReturnItem(
                    source_workline_id=hold.workline_id,
                    source_session_id=hold.session_id,
                    source_command_id=hold.source_command_id,
                    source_event_id=hold.trace_id,
                    material_identity_key=material_identity_key,
                    material_identity_json=asdict(material_identity),
                    physical_handoff_evidence_json=release_context.physical_handoff_evidence,
                    disposition=MaterialDisposition.RETURN_TO_NG,
                    ng_reason_source=NgReasonSource(ng_reason.source.value),
                    ng_reason_code=ng_reason.canonical_code,
                    ng_reason_label=ng_reason.label,
                    operator_note=request.operator_note,
                    created_from_runtime_hold_id=cast("int", hold.id),
                    status=NgReturnItemStatus.WAITING_REWORK,
                    confirmed_by=operator_id,
                    confirmed_at=confirmed_at,
                )
                db.add(item)
                await db.flush()
        except IntegrityError:
            await self._raise_material_conflict(db, material_identity_key)
        if item is None:
            raise RuntimeError("创建 NG Return Item 后无法读取")
        return item

    async def _prepare_return_to_ng_release(
        self,
        db: AsyncSession,
        *,
        hold: RuntimeHold,
        request: ResolveRuntimeHoldRequest,
        operator_id: int,
        confirmed_at: Any,
        session: Any | None,
        workline: Any,
    ) -> ReturnToNgReleaseContext:
        from src.app.runtime.capabilities.material_flow.contracts.material_identity import (
            MaterialIdentityResolutionStatus,
        )

        # ng_reason and physical_handoff_evidence already validated by _validate_release_request

        material_identity = self._resolve_material_identity(hold, request=request, session=session)
        if material_identity.resolution_status != MaterialIdentityResolutionStatus.RESOLVED:
            raise RuntimeHoldReleaseError(
                "RUNTIME_HOLD_MISSING_RELEASE_EVIDENCE",
                f"material identity unresolved: {material_identity.resolution_status.value}",
            )
        material_identity_key = cast("str", material_identity.idempotency_key)
        existing_item = await self.runtime_hold_repo.get_active_ng_return_item_by_material_identity(
            db,
            material_identity_key,
            exclude_runtime_hold_id=cast("int", hold.id),
        )
        if existing_item is not None:
            await self._raise_material_conflict(db, material_identity_key, existing_item=existing_item)

        ng_reason = self._resolve_ng_reason(hold, request.ng_reason)
        physical_handoff_evidence = self._build_physical_handoff_evidence(
            workline=workline,
            request=request,
            material_identity=material_identity,
            operator_id=operator_id,
            confirmed_at=confirmed_at,
        )
        return ReturnToNgReleaseContext(
            material_identity=material_identity,
            ng_reason=ng_reason,
            physical_handoff_evidence=physical_handoff_evidence,
        )

    async def _raise_material_conflict(
        self,
        db: AsyncSession,
        material_identity_key: str,
        *,
        existing_item: NgReturnItem | None = None,
    ) -> None:
        if existing_item is None:
            existing_item = await self.runtime_hold_repo.get_active_ng_return_item_by_material_identity(
                db,
                material_identity_key,
            )
        data: dict[str, Any] = {"material_identity_key": material_identity_key}
        if existing_item is not None:
            data.update(
                {
                    "existing_ng_return_item_id": existing_item.id,
                    "existing_runtime_hold_id": existing_item.created_from_runtime_hold_id,
                    "existing_status": enum_str(existing_item.status),
                }
            )
        raise RuntimeHoldReleaseError(
            "RUNTIME_HOLD_MATERIAL_CONFLICT",
            f"material already has active NG return item: {material_identity_key}",
            data=data,
        )

    def _resolve_ng_reason(self, hold: RuntimeHold, ng_reason: Any) -> NgReasonDefinition:
        from src.app.runtime.capabilities.material_flow.contracts.ng_reason import build_ng_reason_catalog

        del hold
        catalog = build_ng_reason_catalog()
        reason = catalog.by_code.get(ng_reason.code)
        if reason is None or reason.source.value != ng_reason.source:
            raise RuntimeHoldReleaseError(
                "RUNTIME_HOLD_REASON_UNMAPPED",
                f"NG reason is not mapped: {ng_reason.source}:{ng_reason.code}",
            )
        return reason

    def _build_physical_handoff_evidence(
        self,
        *,
        workline: Any,
        request: ResolveRuntimeHoldRequest,
        material_identity: Any,
        operator_id: int,
        confirmed_at: Any,
    ) -> dict[str, Any]:
        evidence = request.physical_handoff_evidence
        if evidence is None:
            raise ValueError("RETURN_TO_NG requires physical_handoff_evidence")
        location = self._resolve_ng_location(workline, evidence)
        payload = evidence.model_dump(mode="json")
        payload["ng_location"] = location
        payload["handoff_confirmed_by"] = operator_id
        payload["handoff_confirmed_at"] = confirmed_at.isoformat()
        payload["material_identity_key"] = material_identity.idempotency_key
        payload["material_identity_evidence_hash"] = material_identity.raw_evidence_hash
        payload["server_evidence_hash"] = self._evidence_hash(payload)
        return payload

    def _resolve_ng_location(self, workline: Any, evidence: Any) -> dict[str, Any]:
        locations = self._configured_ng_locations(workline)
        if not locations:
            raise RuntimeHoldReleaseError(
                "RUNTIME_HOLD_HANDOFF_LOCATION_UNMAPPED",
                "NG location whitelist is not configured",
            )

        code = evidence.ng_location_code.strip()
        scan = evidence.ng_location_scan.strip()
        location = locations.get(code)
        if location is None:
            raise RuntimeHoldReleaseError(
                "RUNTIME_HOLD_HANDOFF_LOCATION_UNMAPPED",
                f"NG location is not mapped: {code}",
            )
        accepted_scans = {code, *location["aliases"]}
        if scan not in accepted_scans:
            raise RuntimeHoldReleaseError(
                "RUNTIME_HOLD_HANDOFF_LOCATION_UNMAPPED",
                f"NG location scan does not match whitelist: {scan}",
            )
        return {
            "code": code,
            "label": location["label"],
            "scan": scan,
            "source": "workline.runtime_config_json.runtime_hold.ng_locations",
        }

    def _configured_ng_locations(self, workline: Any) -> dict[str, dict[str, Any]]:
        config = as_dict(getattr(workline, "runtime_config_json", None))
        runtime_hold_config = as_dict(config.get("runtime_hold"))
        raw_locations = runtime_hold_config.get("ng_locations")
        if raw_locations is None:
            raw_locations = config.get("ng_locations")
        if not isinstance(raw_locations, list):
            return {}

        locations: dict[str, dict[str, Any]] = {}
        for raw_location in raw_locations:
            if isinstance(raw_location, str):
                code = raw_location.strip()
                if code:
                    locations[code] = {"label": code, "aliases": set()}
                continue
            if not isinstance(raw_location, dict):
                continue
            if raw_location.get("enabled") is False:
                continue
            raw_code = raw_location.get("code")
            code = raw_code.strip() if isinstance(raw_code, str) else ""
            if not code:
                continue
            raw_aliases = raw_location.get("aliases", [])
            if not isinstance(raw_aliases, (list, tuple, set)):
                raw_aliases = []
            aliases = {alias.strip() for alias in raw_aliases if isinstance(alias, str) and alias.strip()}
            raw_label = raw_location.get("label")
            locations[code] = {
                "label": raw_label.strip() if isinstance(raw_label, str) and raw_label.strip() else code,
                "aliases": aliases,
            }
        return locations

    def _evidence_hash(self, payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)
        return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"

    def _resolve_material_identity(
        self, hold: RuntimeHold, *, request: ResolveRuntimeHoldRequest, session: Any | None
    ) -> Any:
        from src.app.runtime.capabilities.material_flow.contracts.material_identity import (
            MaterialIdentity,
            MaterialIdentityResolutionStatus,
            hash_material_evidence,
        )

        evidence = cast("Any", request.physical_handoff_evidence)
        material_scan_payload = evidence.material_scan_payload
        if not isinstance(material_scan_payload, dict):
            material_scan_payload = {"scan": material_scan_payload}

        evidence = {
            "session_context": as_dict(getattr(session, "context_json", None)),
            "source_payload": self._material_source_payload(hold),
            "material_scan_payload": material_scan_payload,
            "hold_id": hold.id,
        }
        return MaterialIdentity(
            resolution_status=MaterialIdentityResolutionStatus.RESOLVED,
            idempotency_key=hash_material_evidence(evidence),
            display=material_scan_payload,
            raw_evidence_hash=hash_material_evidence(evidence),
        )

    def _write_release_facts(
        self,
        hold: RuntimeHold,
        *,
        request: ResolveRuntimeHoldRequest,
        operator_id: int,
        resolved_at: Any,
        ng_item: NgReturnItem | None,
        return_to_ng_context: ReturnToNgReleaseContext | None,
    ) -> None:
        hold.material_disposition = MaterialDisposition(request.material_disposition)
        if return_to_ng_context is not None:
            hold.ng_reason_source = return_to_ng_context.ng_reason.source
            hold.ng_reason_code = return_to_ng_context.ng_reason.canonical_code
            hold.ng_reason_label = return_to_ng_context.ng_reason.label
        elif request.ng_reason is not None:
            hold.ng_reason_source = NgReasonSource(request.ng_reason.source)
            hold.ng_reason_code = request.ng_reason.code
            hold.ng_reason_label = request.ng_reason.label
        hold.release_evidence_json = {
            "resolution": request.resolution,
            "checks": dict(request.checks),
            "operator_note": request.operator_note,
            "operator_id": operator_id,
            "resolved_at": resolved_at.isoformat(),
            "material_disposition": request.material_disposition,
            "latest_evidence_hash": request.latest_evidence_hash,
            "result_payload": request.result_payload or {},
            "physical_handoff_evidence": (
                return_to_ng_context.physical_handoff_evidence if return_to_ng_context is not None else None
            ),
            "ng_return_item_id": getattr(ng_item, "id", None),
        }

    def _resolve_session(
        self,
        session: Any,
        *,
        request: ResolveRuntimeHoldRequest,
        operator_id: int,
        resolved_at: Any,
    ) -> None:
        from src.app.workline.domain.services.session_lifecycle_service import (
            workline_session_lifecycle_service,
        )

        workline_session_lifecycle_service.resolve(
            session,
            resolution=SessionStatus(request.resolution),
            occurred_at=resolved_at,
        )
        self._mark_reconciliation_resolved(session, request=request, resolved_at=resolved_at)
        self._write_session_release_context(
            session,
            request=request,
            operator_id=operator_id,
            resolved_at=resolved_at,
        )

    def _resolve_session_for_command_result_replay(
        self,
        session: Any,
        *,
        request: ResolveRuntimeHoldRequest,
        operator_id: int,
        resolved_at: Any,
        command: DeviceCommand,
    ) -> None:
        if command.id is None:
            raise ValueError(f"DeviceCommand 缺少主键: {command.command_code}")
        from src.app.workline.domain.services.session_lifecycle_service import (
            workline_session_lifecycle_service,
        )

        workline_session_lifecycle_service.replay_command_result_wait(
            session,
            command_code=command.command_code,
            occurred_at=resolved_at,
        )
        self._mark_reconciliation_resolved(session, request=request, resolved_at=resolved_at)
        self._write_session_release_context(
            session,
            request=request,
            operator_id=operator_id,
            resolved_at=resolved_at,
        )

    def _mark_reconciliation_resolved(
        self,
        session: Any,
        *,
        request: ResolveRuntimeHoldRequest,
        resolved_at: Any,
    ) -> None:
        if session.reconciliation_state == RuntimeReconciliationState.PENDING:
            session.reconciliation_state = RuntimeReconciliationState.RESOLVED
            session.reconciliation_resolution = RuntimeReconciliationResolution(request.resolution)
            session.reconciliation_resolved_at = resolved_at

    def _write_session_release_context(
        self,
        session: Any,
        *,
        request: ResolveRuntimeHoldRequest,
        operator_id: int,
        resolved_at: Any,
    ) -> None:
        context = as_dict(session.context_json)
        context["runtime_hold_release"] = {
            "resolution": request.resolution,
            "checks": dict(request.checks),
            "operator_note": request.operator_note,
            "operator_id": operator_id,
            "resolved_at": resolved_at.isoformat(),
            "material_disposition": request.material_disposition,
            "result_payload": request.result_payload or {},
        }
        session.context_json = context

    def _should_replay_command_result(
        self,
        *,
        hold: RuntimeHold,
        request: ResolveRuntimeHoldRequest,
    ) -> bool:
        return (
            hold.source_command_id is not None
            and request.material_disposition == MaterialDisposition.CONTINUE.value
            and request.resolution == SessionStatus.COMPLETED.value
        )

    async def _resolve_source_command(
        self,
        db: AsyncSession,
        *,
        hold: RuntimeHold,
        request: ResolveRuntimeHoldRequest,
        resolved_at: Any,
        session: Any | None,
    ) -> DeviceCommand | None:
        if hold.source_command_id is None:
            return None
        command = await self.command_repo.get_by_id(db, hold.source_command_id)
        if command is None:
            return None
        if request.resolution == SessionStatus.COMPLETED.value:
            result_payload = _runtime_continue_result_payload(request, command, session=session)
            command.status = CommandStatus.COMPLETED
            command.result = CommandResult.SUCCESS
            command.result_data = result_payload
            command.error_detail = None
        elif request.resolution == SessionStatus.FAILED.value:
            command.status = CommandStatus.FAILED
            command.result = CommandResult.FAILED
            command.error_detail = {
                **as_dict(command.error_detail),
                "error_code": "RUNTIME_HOLD_FAILED",
                "operator_resolution": request.resolution,
            }
        else:
            command.status = CommandStatus.CANCELLED
            command.result = None
            command.error_detail = {
                **as_dict(command.error_detail),
                "error_code": "RUNTIME_HOLD_CANCELLED",
                "operator_resolution": request.resolution,
            }
        command.completed_at = resolved_at
        return command

    async def _create_continue_command_result_inbox(
        self,
        db: AsyncSession,
        *,
        hold: RuntimeHold,
        request: ResolveRuntimeHoldRequest,
        command: DeviceCommand,
        session: Any | None,
    ) -> int:
        if command.id is None:
            raise ValueError(f"DeviceCommand 缺少主键: {command.command_code}")
        if command.workline_id is None:
            command.workline_id = hold.workline_id
        device = await db.get(Device, command.device_id)
        if device is None:
            raise ValueError(f"设备不存在: {command.device_id}")

        command_type = enum_str(command.task_type)
        result_payload = _runtime_continue_result_payload(request, command, session=session)
        payload = {
            "command_code": command.command_code,
            "device_code": device.device_code,
            "task_type": command_type,
            "result": CommandResult.SUCCESS.value,
            "runtime_hold_release": True,
            "data": result_payload,
        }
        # RuntimeInbox 是 command result 唯一事实源，后续 processor 经统一路径消费。
        from src.app.runtime.orchestration.services.runtime_inbox import (
            runtime_inbox_service,
        )

        source_event_id = f"runtime-hold:result:{hold.id}:{command.command_code}"
        runtime_inbox_result = await runtime_inbox_service.accept_command_result(
            db,
            command_code=command.command_code,
            source_event_id=source_event_id,
            device_code=device.device_code,
            workline_id=command.workline_id,
            device_id=command.device_id,
            command_id=command.id,
            trace_id=command.trace_id or hold.trace_id,
            event_id=source_event_id,
            causation_id=hold.trace_id,
            payload_json=payload,
            auto_commit=False,
        )
        return cast("int", runtime_inbox_result.record.id)

    async def _clear_runtime_device_error(self, db: AsyncSession, *, hold: RuntimeHold) -> None:
        device_error = self._device_error_for_hold(hold)
        if hold.source_device_id is None or device_error is None:
            return
        _ = await self.device_service.clear_reconciliation_error(
            db,
            device_id=hold.source_device_id,
            expected_error_code=device_error,
            auto_commit=False,
        )

    def _device_error_for_hold(self, hold: RuntimeHold) -> str | None:
        if hold.hold_type != RuntimeHoldType.RUNTIME_RECONCILIATION:
            return None
        if hold.source_reason == "CALLBACK_DEADLINE_EXPIRED":
            return "CALLBACK_DEADLINE_EXPIRED"
        if hold.source_reason in {"COMMAND_ACK_EXHAUSTED", "OUTBOX_DISPATCH_FAILED"}:
            return "OUTBOX_DISPATCH_FAILED"
        return None

    async def _project_remaining_hold_status(
        self,
        db: Any,
        *,
        workline_id: int,
        remaining_holds: list[RuntimeHold],
    ) -> None:
        first_hold = remaining_holds[0]
        safety_hold = next((item for item in remaining_holds if item.hold_type == RuntimeHoldType.SAFETY_ESTOP), None)
        if safety_hold is not None:
            safety_evidence = as_dict(getattr(safety_hold, "evidence_snapshot_json", None))
            active_safety_incident_id = optional_int(safety_evidence.get("incident_id")) or optional_int(
                safety_evidence.get("safety_incident_id")
            )
            if active_safety_incident_id is None:
                snapshot = await workline_runtime_status_projection_service.runtime_status_snapshot(
                    db,
                    workline_id=workline_id,
                )
                active_safety_incident_id = optional_int(getattr(snapshot, "active_safety_incident_id", None))
            _ = await workline_runtime_status_projection_service.project_estopped_active_hold(
                db,
                workline_id=workline_id,
                reason=safety_hold.source_reason,
                active_safety_incident_id=active_safety_incident_id,
            )
        else:
            _ = await workline_runtime_status_projection_service.project_reconciling(
                db,
                workline_id=workline_id,
                reason=first_hold.source_reason,
            )

    def _material_source_payload(self, hold: RuntimeHold) -> dict[str, Any]:
        evidence = as_dict(hold.evidence_snapshot_json)
        for key in ("inbox_payload", "outbox_payload", "source_payload"):
            nested = evidence.get(key)
            if isinstance(nested, dict):
                return dict(nested)
        return evidence

    def _late_callback_evidence(self, session: Any | None) -> list[dict[str, Any]]:
        context = as_dict(getattr(session, "context_json", None))
        evidence = context.get("runtime_reconciliation_late_callback_evidence")
        if not isinstance(evidence, list):
            return []
        return [dict(item) for item in evidence if isinstance(item, dict)]


runtime_hold_release_service = RuntimeHoldReleaseService()


__all__ = [
    "RuntimeHoldReleaseError",
    "RuntimeHoldReleaseService",
    "runtime_hold_release_service",
]
