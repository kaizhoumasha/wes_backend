"""NG material item persistence service."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy.exc import IntegrityError

from src.app.workline.models.runtime_hold import (
    MaterialDisposition,
    NgReturnItem,
    NgReturnItemStatus,
)
from src.app.workline.repositories.runtime_hold_repository import runtime_hold_repository
from src.utils.value_normalization import as_dict
from src.workline_plugin_registry import get_workline_plugin_definition
from src.workline_runtime.material_identity import MaterialIdentityInput, MaterialIdentityResolutionStatus
from src.workline_runtime.ng_reason import NgReasonDefinition, build_ng_reason_catalog

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.app.workline.repositories.runtime_hold_repository import RuntimeHoldRepository


def _as_non_empty_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


class NgMaterialConflictError(ValueError):
    """同一物料已有不同来源 active NG 回流项。"""

    reason_code = "NG_MATERIAL_CONFLICT"

    def __init__(
        self,
        *,
        material_identity_key: str,
        existing_item: NgReturnItem,
        evidence: dict[str, Any],
    ) -> None:
        super().__init__(f"material already has active NG return item: {material_identity_key}")
        self.material_identity_key = material_identity_key
        self.existing_item_id = existing_item.id
        self.existing_runtime_hold_id = existing_item.created_from_runtime_hold_id
        self.evidence = evidence


class NgReturnItemService:
    """Creates NG material queue items from workflow-owned NG outcomes."""

    def __init__(self, *, runtime_hold_repo: RuntimeHoldRepository | None = None) -> None:
        self.runtime_hold_repo = runtime_hold_repo or runtime_hold_repository

    async def record_completed_ng_flow(
        self,
        db: AsyncSession,
        *,
        session: Any,
        workline: Any,
        inbox: Any,
        transition: str | None,
        occurred_at: Any,
    ) -> NgReturnItem | None:
        """Record a normal workflow NG material after physical NG diversion completed."""

        session_context = as_dict(getattr(session, "context_json", None))
        if not self._is_completed_ng_flow(session_context=session_context, transition=transition):
            return None

        plugin_key = (
            _as_non_empty_str(getattr(session, "plugin_key", None))
            or _as_non_empty_str(getattr(workline, "plugin_key", None))
            or ""
        )
        definition = get_workline_plugin_definition(plugin_key)
        if definition is None:
            raise ValueError(f"不支持的工作线插件: {plugin_key}")

        material_identity = definition.manifest.resolve_material_identity(
            MaterialIdentityInput(
                session_context=session_context,
                source_payload=self._source_payload(session_context),
                command_payload=as_dict(getattr(inbox, "payload_json", None)),
                plugin_context={
                    "plugin_key": plugin_key,
                    "contract_version": _as_non_empty_str(getattr(session, "contract_version", None))
                    or _as_non_empty_str(getattr(workline, "contract_version", None)),
                    "session_code": getattr(session, "session_code", None),
                },
            )
        )
        material_identity_key = self._material_identity_key(
            material_identity=material_identity,
            plugin_key=plugin_key,
            session=session,
        )
        material_identity_json = self._material_identity_json(
            material_identity=material_identity,
            material_identity_key=material_identity_key,
        )
        existing_item = await self.runtime_hold_repo.get_active_ng_return_item_by_material_identity(
            db,
            material_identity_key,
        )
        source_command_id = self._source_command_id(session=session, inbox=inbox)
        if existing_item is not None:
            if self._is_same_source(existing_item, session=session, source_command_id=source_command_id):
                return existing_item
            raise self._material_conflict_error(
                existing_item=existing_item,
                material_identity_key=material_identity_key,
                session=session,
                inbox=inbox,
                source_command_id=source_command_id,
                material_identity_json=material_identity_json,
            )

        reason = self._resolve_ng_reason(definition.manifest.list_ng_reasons(), session_context)
        evidence = self._build_evidence(
            session=session,
            inbox=inbox,
            material_identity_key=material_identity_key,
            material_identity_hash=material_identity.raw_evidence_hash,
            occurred_at=occurred_at,
        )
        try:
            async with db.begin_nested():
                item = NgReturnItem(
                    source_workline_id=cast("int", session.workline_id),
                    source_session_id=cast("int", session.id),
                    source_command_id=source_command_id,
                    source_event_id=_as_non_empty_str(getattr(inbox, "event_id", None)),
                    material_identity_key=material_identity_key,
                    material_identity_json=material_identity_json,
                    physical_handoff_evidence_json=evidence,
                    disposition=MaterialDisposition.RETURN_TO_NG,
                    ng_reason_source=reason.source,
                    ng_reason_code=reason.canonical_code,
                    ng_reason_label=reason.label,
                    created_from_runtime_hold_id=None,
                    status=NgReturnItemStatus.WAITING_REWORK,
                    confirmed_at=occurred_at,
                )
                db.add(item)
                await db.flush()
                return item
        except IntegrityError:
            existing_item = await self.runtime_hold_repo.get_active_ng_return_item_by_material_identity(
                db,
                material_identity_key,
            )
            if existing_item is not None and self._is_same_source(
                existing_item,
                session=session,
                source_command_id=source_command_id,
            ):
                return existing_item
            if existing_item is not None:
                raise self._material_conflict_error(
                    existing_item=existing_item,
                    material_identity_key=material_identity_key,
                    session=session,
                    inbox=inbox,
                    source_command_id=source_command_id,
                    material_identity_json=material_identity_json,
                ) from None
            raise

    def _is_completed_ng_flow(self, *, session_context: dict[str, Any], transition: str | None) -> bool:
        if transition != "pick_ng":
            return False
        return bool(
            _as_non_empty_str(session_context.get("ng_reason"))
            or _as_non_empty_str(session_context.get("pick_place_reason"))
            or _as_non_empty_str(session_context.get("scan_ng_reason_code"))
        )

    def _source_payload(self, session_context: dict[str, Any]) -> dict[str, Any]:
        return as_dict(session_context.get("initial_payload") or session_context.get("source_payload"))

    def _material_identity_key(self, *, material_identity: Any, plugin_key: str, session: Any) -> str:
        if material_identity.resolution_status == MaterialIdentityResolutionStatus.RESOLVED:
            return cast("str", material_identity.idempotency_key)
        return f"workflow-ng:{plugin_key}:session:{session.id}"

    def _material_identity_json(self, *, material_identity: Any, material_identity_key: str) -> dict[str, Any]:
        payload = asdict(material_identity)
        payload["resolution_status"] = material_identity.resolution_status.value
        if material_identity.resolution_status != MaterialIdentityResolutionStatus.RESOLVED:
            payload["idempotency_key"] = material_identity_key
            payload["fallback_identity"] = True
            payload["fallback_source"] = "SESSION"
        return payload

    def _resolve_ng_reason(
        self,
        plugin_reasons: Any,
        session_context: dict[str, Any],
    ) -> NgReasonDefinition:
        raw_code = (
            _as_non_empty_str(session_context.get("scan_ng_reason_code"))
            or _as_non_empty_str(session_context.get("ng_reason"))
            or _as_non_empty_str(session_context.get("pick_place_reason"))
        )
        if raw_code is None:
            raise ValueError("NG reason is missing")
        catalog = build_ng_reason_catalog(plugin_reasons)
        reason = catalog.by_code.get(raw_code)
        if reason is None:
            raise ValueError(f"NG reason is not mapped: {raw_code}")
        return reason

    def _source_command_id(self, *, session: Any, inbox: Any) -> int | None:
        command_id = getattr(inbox, "command_id", None)
        if isinstance(command_id, int):
            return command_id
        awaiting_command_id = getattr(session, "awaiting_command_id", None)
        return awaiting_command_id if isinstance(awaiting_command_id, int) else None

    def _is_same_source(self, item: NgReturnItem, *, session: Any, source_command_id: int | None) -> bool:
        return item.source_session_id == getattr(session, "id", None) and item.source_command_id == source_command_id

    def _material_conflict_error(
        self,
        *,
        existing_item: NgReturnItem,
        material_identity_key: str,
        session: Any,
        inbox: Any,
        source_command_id: int | None,
        material_identity_json: dict[str, Any],
    ) -> NgMaterialConflictError:
        session_context = as_dict(getattr(session, "context_json", None))
        scan_event_payload = self._source_payload(session_context)
        command_result_payload = as_dict(getattr(inbox, "payload_json", None))
        evidence = {
            "reason_code": NgMaterialConflictError.reason_code,
            "material_identity_key": material_identity_key,
            "existing_ng_return_item_id": existing_item.id,
            "existing_runtime_hold_id": existing_item.created_from_runtime_hold_id,
            "existing_source_session_id": existing_item.source_session_id,
            "existing_source_command_id": existing_item.source_command_id,
            "existing_source_event_id": existing_item.source_event_id,
            "new_source_session_id": getattr(session, "id", None),
            "new_source_command_id": source_command_id,
            "new_source_event_id": _as_non_empty_str(getattr(inbox, "event_id", None)),
            "new_trace_id": _as_non_empty_str(getattr(inbox, "trace_id", None))
            or _as_non_empty_str(getattr(session, "trace_id", None)),
            "expected_material_identity_key": existing_item.material_identity_key,
            "actual_material_identity_key": material_identity_json.get("idempotency_key") or material_identity_key,
            "scan_event_type": scan_event_payload.get("event_type"),
            "scan_event_payload": scan_event_payload,
            "command_result_payload": command_result_payload,
            "new_material_identity_json": material_identity_json,
        }
        return NgMaterialConflictError(
            material_identity_key=material_identity_key,
            existing_item=existing_item,
            evidence=evidence,
        )

    def _build_evidence(
        self,
        *,
        session: Any,
        inbox: Any,
        material_identity_key: str,
        material_identity_hash: str | None,
        occurred_at: Any,
    ) -> dict[str, Any]:
        payload = as_dict(getattr(inbox, "payload_json", None))
        return {
            "source": "WORKFLOW_SCAN_NG",
            "source_inbox_id": getattr(inbox, "id", None),
            "source_event_id": _as_non_empty_str(getattr(inbox, "event_id", None)),
            "trace_id": _as_non_empty_str(getattr(inbox, "trace_id", None))
            or _as_non_empty_str(getattr(session, "trace_id", None)),
            "session_code": getattr(session, "session_code", None),
            "command_code": payload.get("command_code"),
            "confirmed_at": occurred_at.isoformat(),
            "material_identity_key": material_identity_key,
            "material_identity_evidence_hash": material_identity_hash,
            "command_result_payload": payload,
        }


ng_return_item_service = NgReturnItemService()


__all__ = ["NgMaterialConflictError", "NgReturnItemService", "ng_return_item_service"]
