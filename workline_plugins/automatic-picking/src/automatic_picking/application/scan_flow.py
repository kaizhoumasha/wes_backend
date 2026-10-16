"""自动线本点扫码与 Work 结果的静态业务入口。"""

import re
from datetime import timedelta

from automatic_picking.definition import DEFINITION
from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE, DeviceCommandRequest, EcsDeviceEvent
from src.app.execution.models import InboundEvidence
from src.app.execution.plugin_binding import BusinessEvidenceApplication, BusinessEvidenceDisposition
from src.app.wms_integration.outbound_picking.repositories.picking_task_repository import PickingTaskRepository
from src.app.workline.installed_plugin import parse_device_bindings
from src.app.workline.repositories.workline_repository import workline_repository
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone


def normal_bin_code(raw, role):
    suffix = {"SCAN1": "-B", "SCAN2": "-C"}.get(role)
    if suffix is None or not isinstance(raw, str) or len(raw) != 12 or not raw.endswith(suffix):
        return None
    return raw[:-2] if re.fullmatch(r"A[0-9]{9}", raw[:-2]) else None


class AutomaticPickingScanFlow:
    def __init__(
        self, works, feeds, work_flow, commands, *, batch_driver=None, tasks=None, worklines=None, exit_flow=None
    ):
        self.works, self.feeds, self.work_flow, self.commands = works, feeds, work_flow, commands
        self.exit_flow = exit_flow
        self.batch_driver = batch_driver
        self.tasks = tasks or PickingTaskRepository()
        self.worklines = worklines or workline_repository

    async def apply_in_session(self, db, evidence_id, *, workline_id):
        line = await self.worklines.get_for_authority_update(db, workline_id)
        evidence = await db.get(InboundEvidence, evidence_id, with_for_update=True)
        if (
            line is None
            or evidence is None
            or evidence.workline_id != workline_id
            or evidence.apply_status != "APPLIED"
            or (line.plugin_key, line.plugin_version) != (DEFINITION.plugin_key, DEFINITION.plugin_version)
        ):
            return BusinessEvidenceApplication(BusinessEvidenceDisposition.RECONCILING)
        if evidence.kind == "WMS_RESULT":
            result = await self._result(db, line, evidence)
        elif evidence.kind == "DEVICE_RESULT" and self.exit_flow is not None:
            result = await self.exit_flow.direction_result_in_session(db, line=line, evidence=evidence)
        elif evidence.kind == "DEVICE_EVENT" and line.is_active:
            try:
                event = EcsDeviceEvent.model_validate(evidence.normalized_payload)
            except ValueError:
                return BusinessEvidenceApplication(BusinessEvidenceDisposition.RECONCILING)
            if event.event_type != "SCAN_COMPLETED" or event.is_debug or event.device_code != evidence.device_code:
                result = "IGNORED"
            else:
                bindings = parse_device_bindings(line.config, DEFINITION.device_roles)
                role = next((key for key, value in bindings.items() if value == event.device_code), None)
                result = await self.apply_scan_in_session(
                    db,
                    line=line,
                    evidence=evidence,
                    role=role,
                    raw=event.data.get("bin_code"),
                    timestamp=event.timestamp,
                    now=timezone.now_for_db(),
                )
        else:
            result = "IGNORED"
        if result in {"WAIT_FOR_FEED", "WAIT_FOR_EXIT"}:
            return BusinessEvidenceApplication(BusinessEvidenceDisposition.DEFERRED, retry_after_ms=1000)
        if result == "IGNORED":
            return BusinessEvidenceApplication(BusinessEvidenceDisposition.IGNORED)
        if result == "RECONCILING":
            return BusinessEvidenceApplication(BusinessEvidenceDisposition.RECONCILING)
        return BusinessEvidenceApplication(
            BusinessEvidenceDisposition.APPLIED, canonical_json_digest({"evidence_id": evidence_id, "result": result})
        )

    async def _result(self, db, line, evidence):
        if evidence.operation == "outbound.bin.inbound_batch@v1":
            feed = await self.feeds.by_operation(db, evidence.operation_id)
            if feed is None or feed.workline_id != line.id or feed.archived_at is not None:
                return "RECONCILING"
            snapshot = await self.feeds.inbound(db, feed)
            if snapshot is None or snapshot[2].id != evidence.id:
                return "RECONCILING"
            await self.batch_driver.advance_feed_in_session(db, line, feed, snapshot)
            return "INBOUND_RECEIVED"
        if evidence.operation != "outbound.bin.work_plan@v1":
            return "IGNORED"
        work = await self.works.by_operation(db, evidence.operation_id)
        if (
            work is None
            or work.workline_id != line.id
            or work.picking_task_id != evidence.picking_task_id
            or work.archived_at is not None
        ):
            return "RECONCILING"
        task = await self.tasks.get_by_id_for_update(db, work.picking_task_id)
        if task is None or task.workline_id != line.id:
            return "RECONCILING"
        snapshot = await self.work_flow.reader.by_operation_id(
            db, picking_task_id=task.id, operation_id=work.operation_id
        )
        if snapshot is None or snapshot.status != "COMPLETED" or snapshot.evidence_id != evidence.id:
            return "RECONCILING"
        await self.work_flow.advance_in_session(db, task=task, work=work, now=timezone.now_for_db())
        if self.exit_flow is not None:
            await self.exit_flow.advance_in_session(db, line=line, work=work, task=task)
        return work.phase

    async def apply_scan_in_session(self, db, *, line, evidence, role, raw, timestamp, now):
        if role in {"SCAN3", "SCAN4"}:
            apply = (
                (self.exit_flow.scan3_in_session if role == "SCAN3" else self.exit_flow.scan4_in_session)
                if self.exit_flow is not None
                else None
            )
            return await apply(db, line=line, evidence=evidence, timestamp=timestamp) if apply else "WAIT_FOR_EXIT"
        code = normal_bin_code(raw, role)
        if code is None:
            return "IGNORED"
        current = await self.works.current_work(db, line.id, code)
        if current is not None:
            return "WORK_RESCAN" if role == "SCAN2" else "ENTRY_ALREADY_AT_WORK"
        task = await self.tasks.get_executing_for_workline_for_update(db, line.id)
        if task is None:
            return "RECONCILING"
        candidates = await self.feeds.scan_candidates(db, line.id, task, code)
        if len(candidates) != 1:
            return "RECONCILING"
        feed = candidates[0]
        prior = await self.feeds.entry(db, feed.id, code)
        if not await self.feeds.admission_ready(db, feed, code) or (
            role == "SCAN1"
            and (prior is None or prior.scan1_command_code is None)
            and not await self.feeds.bin_delivered(db, feed, code)
        ):
            return "WAIT_FOR_FEED"
        entry = prior or await self.feeds.record_entry(db, feed, code, evidence.id)
        if role == "SCAN1":
            if entry.scan1_command_code is None:
                entry.scan1_command_code = await self._forward(db, line, evidence.id)
            return "ENTRY_RECORDED"
        await self.work_flow.admit_in_session(
            db,
            line_id=line.id,
            task=task,
            feed=feed,
            bin_code=code,
            evidence_id=evidence.id,
            scanned_at=timestamp,
            now=now,
        )
        entry.work_admitted = True
        return "WORK_ADMITTED"

    async def _forward(self, db, line, evidence_id):
        device_code = parse_device_bindings(line.config, DEFINITION.device_roles)["SCAN1"]
        binding = await self.worklines.get_binding_for_command_creation(
            db, workline_id=line.id, device_code=device_code
        )
        if binding is None:
            raise ValueError("SCAN1 has no frozen command contract")
        handle = await self.commands.create_command_in_session(
            db,
            DeviceCommandRequest(
                device_code=device_code,
                workline_id=line.id,
                execution_ref_type=WORKLINE_BUSINESS_REF_TYPE,
                execution_ref_id=f"automatic-picking:{evidence_id}:SCAN1",
                material_execution_id=None,
                contract_key=binding.contract_key,
                contract_version=binding.contract_version,
                task_type="MOVE_FORWARD",
                params={},
                deadline_at=timezone.now_for_db() + timedelta(milliseconds=binding.command_timeout_ms),
            ),
        )
        return handle.command_code
