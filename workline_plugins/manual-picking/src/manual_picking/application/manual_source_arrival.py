"""受控人工到位只恢复当前来源架业务，不闭合原 Transport。"""

import hashlib
import json

from src.app.sys.services.audit_service import audit_log_service
from src.utils.timezone import timezone

from .manual_source_arrival_model import ManualPickingSourceArrival
from .manual_source_arrival_repository import ManualSourceArrivalRepository


class ManualSourceArrivalService:
    def __init__(self, *, sessions, repository=None, audit=None):
        self._sessions = sessions
        self._repository = repository or ManualSourceArrivalRepository()
        self._audit = audit or audit_log_service

    async def confirm(
        self,
        *,
        workline_id,
        picking_task_id,
        plan_revision,
        rack_id,
        rack_face,
        location_code,
        transport_task_id,
        expected_request_body_digest,
        confirmed_by,
        confirmation_reference,
    ):
        if (
            not isinstance(confirmed_by, str)
            or not confirmed_by.strip()
            or len(confirmed_by) > 32
            or not isinstance(confirmation_reference, str)
            or not confirmation_reference.strip()
            or len(confirmation_reference) > 512
        ):
            raise ValueError("manual arrival requires named confirmer and evidence reference")
        confirmation = {
            "workline_id": workline_id,
            "picking_task_id": picking_task_id,
            "plan_revision": plan_revision,
            "rack_id": rack_id,
            "rack_face": rack_face,
            "location_code": location_code,
            "transport_task_id": transport_task_id,
            "request_body_digest": expected_request_body_digest,
            "confirmed_by": confirmed_by,
            "confirmation_reference": confirmation_reference,
        }
        digest = hashlib.sha256(
            json.dumps(confirmation, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        async with self._sessions.begin() as db:
            line = await self._repository.line_for_update(db, workline_id)
            existing = await self._repository.by_transport(db, workline_id, transport_task_id)
            if existing is not None and existing.source_transport_task_id == transport_task_id:
                if existing.confirmation_digest != digest:
                    raise ValueError("manual arrival confirmation changed")
                return existing.audit_id
            if (
                line is None
                or not line.is_active
                or line.plugin_key != "manual-picking"
                or line.position_bindings.get("FIVE_RACK", {}).get("location_id") != location_code
            ):
                raise ValueError("manual arrival differs from current source workpoint")
            row = await self._repository.context(
                db,
                workline_id=workline_id,
                picking_task_id=picking_task_id,
                plan_revision=plan_revision,
                rack_id=rack_id,
                rack_face=rack_face,
                transport_task_id=transport_task_id,
            )
            if row is None:
                raise ValueError("manual arrival has no unique original source identity")
            task, source, binding, transport, member = row
            if (
                task.task_type != "MANUAL"
                or task.status != "EXECUTING"
                or source.cancelled_evidence_id is not None
                or binding.causal_token <= 0
                or binding.window_released_at is not None
                or transport.kind != "RACK_MOVE"
                or transport.authority_workline_id != workline_id
                or transport.submit_request_body_digest != expected_request_body_digest
                or hashlib.sha256(transport.submit_request_body.encode()).hexdigest() != expected_request_body_digest
                or transport.request_json.get("target_face") != rack_face
                or member.object_type != "RACK"
                or member.object_id != rack_id
                or member.target_json != {"kind": "RACK_POSITION", "location_code": location_code}
            ):
                raise ValueError("manual arrival differs from active requirement or frozen transport")
            if transport.status != "ACCEPTED":
                raise ValueError("manual arrival requires the original ACCEPTED transport")
            now = timezone.now_for_db()
            fact = ManualPickingSourceArrival(
                workline_id=workline_id,
                picking_task_id=picking_task_id,
                source_evidence_id=source.source_evidence_id,
                source_causal_token=binding.causal_token,
                source_transport_task_id=transport_task_id,
                plan_revision=plan_revision,
                rack_id=rack_id,
                rack_face=rack_face,
                location_code=location_code,
                observed_at=now,
                updated_at=now,
                audit_id=0,
                confirmation_digest=digest,
            )
            if await self._repository.superseded(db, fact):
                raise ValueError("manual arrival cannot replace a newer rack action")
            audit = await self._audit.create_audit_log(
                db,
                method="MANUAL",
                title="人工确认来源架当前作业面到位",
                path="/maintenance/manual-picking/source-arrival",
                args={
                    "operation": "MANUAL_SOURCE_RACK_ARRIVED",
                    "model": "ManualPickingSourceArrival",
                    "record_id": str(workline_id),
                    "confirmation": confirmation,
                    "original_status": transport.status,
                },
                msg="独立到位事实恢复批次；原搬运、冻结请求和回执不变",
            )
            fact.audit_id = audit.id
            await self._repository.retire_current(db, workline_id)
            await self._repository.save(db, fact)
            return audit.id
