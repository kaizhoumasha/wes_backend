"""成功 PUT 沿本盘原始扫码形成报告，复用 FactProcessor 的位置/报告原子事务。"""

from dataclasses import dataclass

from wes_plugin_sdk import (
    DeviceResultReadyFact,
    FactRecorded,
    PickingBinCell,
    PickingMaterialAccept,
    PickingRackSlot,
    PickingTargetRackSlot,
    PickingTargetReplace,
    PickingTargetRotate,
    WmsResultReadyFact,
)

from automatic_picking.application.material_flow import MatchedMaterialScan
from automatic_picking.handlers.placement import MaterialPlacementPendingFact, MaterialPutFact, MaterialReportedFact
from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE
from src.app.execution.services.inbound_evidence_service import InboundEvidenceDigestPolicy, normalize_payload
from src.app.wms_integration.outbound_picking.services import MaterialMovementReportResultReader


@dataclass(frozen=True, slots=True)
class MatchedPutResult:
    """读口只提供匹配原 Command 的确定成功、精确目标和实际完成时间。"""

    evidence_id: int
    workline_id: int
    pickup_id: str
    material_execution_id: int
    command_code: str
    device_code: str
    target: PickingTargetRackSlot
    occurred_at: int

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in (self.evidence_id, self.workline_id, self.material_execution_id)):
            raise ValueError("PUT result requires positive Evidence/WorkLine/material identity")
        if any(type(v) is not str or not v.strip() for v in (self.pickup_id, self.command_code, self.device_code)):
            raise ValueError("PUT result requires Pickup/Command/device identity")
        if type(self.target) is not PickingTargetRackSlot:
            raise TypeError("PUT result requires exact RACK_SLOT")


class AutomaticPickingPlacementFlow:
    def __init__(self, repository, material_reader, put_reader, *, scan_reader):
        self.repository, self.material_reader, self.put_reader = repository, material_reader, put_reader
        self.report_reader = MaterialMovementReportResultReader()
        self.scan_reader = scan_reader

    async def _context(self, db, execution_code):
        work, pickup, _ = await self.repository.material_context_for_update(db, execution_code)
        execution = await self.repository.material_execution(db, pickup.material_execution_id)
        command = await self.repository.put_command(db, pickup.put_command_code)
        if (
            execution is None
            or execution.execution_code != pickup.pickup_id
            or execution.material_trace_id != pickup.pickup_id
            or execution.workline_id != work.workline_id
            or command is None
            or command.command_code == pickup.command_code
            or command.workline_id != work.workline_id
            or command.material_execution_id != execution.id
            or command.execution_ref_type != WORKLINE_BUSINESS_REF_TYPE
            or command.execution_ref_id != "put:" + pickup.pickup_id
        ):
            raise ValueError("placement differs from frozen Pickup/PUT identity")
        return work, pickup, execution, command

    @staticmethod
    def _common(fact):
        return {
            "fact_id": fact.fact_id,
            "evidence_id": fact.evidence_id,
            "fact_version": fact.fact_version,
            "material_execution_id": fact.material_execution_id,
        }

    async def build(self, db, fact):
        work, pickup, execution, command = await self._context(db, fact.material_execution_id)
        common = self._common(fact)
        if type(fact) is WmsResultReadyFact:
            return await self._reported(db, fact, work, pickup, execution, command)
        evidence = await self.repository.material_evidence(db, int(fact.evidence_id))
        if (
            type(fact) is not DeviceResultReadyFact
            or evidence is None
            or evidence.kind != "DEVICE_RESULT"
            or evidence.apply_status != "APPLIED"
            or evidence.command_code != command.command_code
            or evidence.workline_id != work.workline_id
            or evidence.material_execution_id != execution.id
            or evidence.device_code != command.device_code
            or fact.command_code != command.command_code
            or fact.device_code != command.device_code
            or fact.material_trace_id != execution.material_trace_id
            or normalize_payload(evidence.normalized_payload, digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE)[1]
            != evidence.payload_digest
        ):
            raise ValueError("placement Evidence differs from bound PUT")
        if command.result_evidence_id != evidence.id or command.status != "SUCCEEDED":
            return MaterialPlacementPendingFact(**common, reason_code="PUT_RESULT_PENDING")
        observed = await self.put_reader.read_in_session(db, evidence_id=evidence.id)
        if observed is None:
            return MaterialPlacementPendingFact(**common, reason_code="PUT_FACT_PENDING")
        if (
            type(observed) is not MatchedPutResult
            or observed.evidence_id != evidence.id
            or observed.workline_id != work.workline_id
            or observed.pickup_id != pickup.pickup_id
            or observed.material_execution_id != execution.id
            or observed.command_code != command.command_code
            or observed.device_code != command.device_code
        ):
            raise ValueError("successful PUT fact differs from bound material identity")
        accepted, task_id, source = await self._accepted(db, work, pickup, execution)
        preparation = accepted.outcome.result.target_preparation
        if preparation is not None and type(preparation) not in (PickingTargetRotate, PickingTargetReplace):
            raise ValueError("successful PUT requires ordinary, ROTATE or REPLACE target preparation")
        if accepted.outcome.result.target_locator != observed.target:
            raise ValueError("successful PUT target differs from published ACCEPT")
        if pickup.put_result_evidence_id is not None and pickup.put_result_evidence_id != evidence.id:
            raise ValueError("placement already refers to another successful result")
        return MaterialPutFact(
            **common,
            task_id=task_id,
            source=source,
            target=observed.target,
            barcode=accepted.intent.barcode,
            occurred_at=observed.occurred_at,
            plan_revision=accepted.intent.plan_revision,
        )

    async def _accepted(self, db, work, pickup, execution):
        if pickup.material_confirmation_id is None:
            accepted = await self.material_reader.by_operation_id(
                db,
                material_execution_id=execution.id,
                operation_id=execution.execution_code,
            )
        else:
            accepted = await self.material_reader.by_id(
                db,
                material_execution_id=execution.id,
                confirmation_id=pickup.material_confirmation_id,
            )
        task_id, source = await self.repository.material_source(db, work, pickup)
        if (
            accepted is None
            or accepted.status != "COMPLETED"
            or accepted.published_at is None
            or accepted.evidence_id != pickup.accept_evidence_id
            or type(accepted.outcome.result) is not PickingMaterialAccept
            or accepted.intent.task_id != task_id
            or type(accepted.intent.source_locator) not in (PickingBinCell, PickingRackSlot)
            or accepted.intent.source_locator != source
            or accepted.intent.plan_revision != await self.repository.source_revision(db, work)
        ):
            raise ValueError("successful PUT target/source differs from current published ACCEPT")
        scan = await self.scan_reader.read_in_session(db, evidence_id=execution.admission_evidence_id)
        if (
            type(scan) is not MatchedMaterialScan
            or scan.evidence_id != execution.admission_evidence_id
            or scan.workline_id != work.workline_id
            or scan.pickup_id != pickup.pickup_id
            or scan.command_code != pickup.command_code
            or scan.barcode != accepted.intent.barcode
            or scan.scanned_at != accepted.intent.scanned_at
        ):
            raise ValueError("placement barcode differs from original scan/ACCEPT identity")
        return accepted, task_id, source

    async def _reported(self, db, fact, work, pickup, execution, command):
        common = self._common(fact)
        if pickup.movement_confirmation_id is None:
            raise ValueError("report requires frozen placement obligation")
        report = await self.report_reader.by_id(
            db, material_execution_id=execution.id, confirmation_id=pickup.movement_confirmation_id
        )
        if (
            report is None
            or report.intent.operation_id != execution.execution_code
            or fact.operation_id != report.intent.operation_id
            or report.evidence_id != int(fact.evidence_id)
        ):
            raise ValueError("report result differs from current placement obligation")
        if report.status != "COMPLETED":
            return MaterialPlacementPendingFact(**common, reason_code="MOVEMENT_RESULT_PENDING")
        if report.outcome != fact.outcome or type(report.outcome.result) is not FactRecorded:
            raise ValueError("closure requires exact typed RECORDED/DUPLICATE")
        accepted, task_id, source = await self._accepted(db, work, pickup, execution)
        preparation = accepted.outcome.result.target_preparation
        if preparation is not None and type(preparation) not in (PickingTargetRotate, PickingTargetReplace):
            raise ValueError("report closure requires ordinary, ROTATE or REPLACE target preparation")
        target = accepted.outcome.result.target_locator
        position = await self.repository.material_evidence(db, pickup.put_result_evidence_id)
        if (
            position is None
            or position.kind != "DEVICE_RESULT"
            or position.apply_status != "APPLIED"
            or position.workline_id != work.workline_id
            or position.material_execution_id != execution.id
            or position.command_code != command.command_code
            or position.device_code != command.device_code
            or normalize_payload(position.normalized_payload, digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE)[1]
            != position.payload_digest
            or position.published_at is None
            or position.decision_digest is None
            or command.status != "SUCCEEDED"
            or command.result_evidence_id != position.id
            or report.intent.barcode != accepted.intent.barcode
            or report.intent.task_id != task_id
            or report.intent.source_locator != source
            or report.intent.to_locator != PickingRackSlot(target.rack_id, target.rack_face, target.slot_id)
            or report.intent.plan_revision != accepted.intent.plan_revision
            or (pickup.work_id is not None and work.authorization_evidence_id == pickup.authorization_evidence_id)
        ):
            raise ValueError("closure lost confirmed placement or ACCEPT handoff")
        removed_id = None
        if pickup.direct_pick_id is not None:
            removed_id = await self.repository.removed_for_close(db, work=work, pickup=pickup, source=source)
            if removed_id is None:
                return MaterialPlacementPendingFact(**common, reason_code="SOURCE_REMOVAL_PENDING")
        elif work.authorization_evidence_id == pickup.accept_evidence_id and work.consumed_pickup_id is None:
            removed_id = await self.repository.removed_for_close(
                db, work=work, pickup=pickup, source=accepted.outcome.result.next_source_action
            )
            if removed_id is None:
                return MaterialPlacementPendingFact(**common, reason_code="SOURCE_REMOVAL_PENDING")
        return MaterialReportedFact(**common, removed_evidence_id=removed_id)

    async def apply_in_session(self, db, *, fact):
        work, pickup, execution, _ = await self._context(db, fact.material_execution_id)
        if type(fact) is MaterialReportedFact:
            if pickup.direct_pick_id is not None:
                if fact.removed_evidence_id is None or work.completed_evidence_id not in (None, int(fact.evidence_id)):
                    raise ValueError("DirectPick closure lost current removal or completion identity")
                work.completed_evidence_id = int(fact.evidence_id)
                await db.flush()
                return
            if work.authorization_evidence_id == pickup.accept_evidence_id and work.consumed_pickup_id is None:
                if fact.removed_evidence_id is None:
                    raise ValueError("closure requires current Work removal fact")
                # 与 CLOSED、报告 Evidence 发布同事务；已推进的来源游标不回写。
                work.top_removed_evidence_id = fact.removed_evidence_id
            await db.flush()
            return
        report = await self.repository.movement_confirmation(db, execution.id, execution.execution_code)
        if report is None:
            raise ValueError("placement requires the same-transaction report obligation")
        if pickup.put_result_evidence_id not in (
            None,
            int(fact.evidence_id),
        ) or pickup.movement_confirmation_id not in (None, report.id):
            raise ValueError("placement/report references must retain frozen identities")
        # 位置内容由精确成功 Evidence 保存，报告正文归 Confirmation；此处只保存当前关联。
        pickup.put_result_evidence_id, pickup.movement_confirmation_id = int(fact.evidence_id), report.id
        await db.flush()
