"""精确扫码关联后的单盘绑定、Fact 增强与 ACCEPT 事务应用；不安装生产扫码入口。"""

from dataclasses import dataclass, replace

from wes_plugin_sdk import (
    EvidenceReadyFact,
    PickingMaterialAccept,
    PickingMaterialOutcome,
    PickingMaterialWait,
    WmsResultReadyFact,
)

from automatic_picking.handlers.material_flow import MaterialDecidedFact, MaterialScannedFact, material_scanned
from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE
from src.utils.canonical_json import canonical_json_digest


@dataclass(frozen=True, slots=True)
class MatchedMaterialScan:
    """注入 reader 已完成设备合同的精确关联；本类型不定义或猜测供应商报文。"""

    evidence_id: int
    workline_id: int
    pickup_id: str
    command_code: str
    barcode: str
    scanned_at: int

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in (self.evidence_id, self.workline_id)):
            raise ValueError("scan Evidence/WorkLine identity must be positive integers")
        if any(type(value) is not str or not value.strip() for value in (self.pickup_id, self.command_code)):
            raise ValueError("scan Pickup/Command identity is required")
        if type(self.barcode) is not str or not 1 <= len(self.barcode) <= 1000:
            raise ValueError("barcode must be original text of 1..1000 characters")
        if type(self.scanned_at) is not int or not 0 <= self.scanned_at <= 2**63 - 1:
            raise ValueError("scanned_at must be nonnegative int64 milliseconds")


class AutomaticPickingMaterialFlow:
    def __init__(self, repository, source_flow, scan_reader, executions):
        self.repository, self.source_flow, self.scan_reader, self.executions = (
            repository,
            source_flow,
            scan_reader,
            executions,
        )

    async def _scan(self, db, evidence_id):
        scan = await self.scan_reader.read_in_session(db, evidence_id=evidence_id)
        if type(scan) is not MatchedMaterialScan or scan.evidence_id != evidence_id:
            raise ValueError("matched scan identity differs from requested Evidence")
        evidence = await self.repository.material_evidence(db, evidence_id)
        if (
            evidence is None
            or evidence.kind != "DEVICE_EVENT"
            or evidence.apply_status != "APPLIED"
            or evidence.workline_id != scan.workline_id
            or evidence.command_code != scan.command_code
            or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
        ):
            raise ValueError("matched scan identity differs from persisted Evidence")
        return scan

    async def bind_scan_in_session(self, db, *, evidence_id):
        # 与 FactProcessor 同序：先锁 Evidence，再锁 WorkLine/Work/Pickup。
        evidence = await self.repository.scan_evidence_for_update(db, evidence_id)
        scan = await self._scan(db, evidence_id)
        work, pickup, command = await self.repository.material_context_for_update(db, scan.pickup_id)
        if (
            evidence is None
            or evidence.kind != "DEVICE_EVENT"
            or evidence.apply_status != "APPLIED"
            or evidence.workline_id != work.workline_id
            or scan.workline_id != work.workline_id
            or scan.command_code != pickup.command_code
            or evidence.command_code != pickup.command_code
            or command.execution_ref_type != WORKLINE_BUSINESS_REF_TYPE
            or command.material_execution_id is not None
            or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
        ):
            raise ValueError("matched scan/Work/Pickup/Command identity differs")
        execution = await self.repository.material_execution(db, pickup.material_execution_id)
        if execution is not None:
            if (
                execution.admission_evidence_id != evidence_id
                or evidence.material_execution_id != execution.id
                or execution.execution_code != pickup.pickup_id
                or execution.material_trace_id != pickup.pickup_id
                or execution.workline_id != work.workline_id
            ):
                raise ValueError("scan admission identity differs from bound material")
            return execution.id
        if evidence.material_execution_id is not None or evidence.published_at is not None:
            raise ValueError("scan identity was already consumed")
        # Pickup UUID 是 WES 本地 trace/执行身份，不能充当 WMS 的 PkgID。
        execution = await self.executions.create_or_get_for_initial_evidence(
            db,
            execution_code=pickup.pickup_id,
            material_trace_id=pickup.pickup_id,
            workline_id=work.workline_id,
            changed_at=evidence.received_at,
            evidence_id=evidence_id,
        )
        pickup.material_execution_id = evidence.material_execution_id = execution.id
        await db.flush()
        return execution.id

    async def build(self, db, fact):
        work, pickup, _ = await self.repository.material_context_for_update(db, fact.material_execution_id)
        execution = await self.repository.material_execution(db, pickup.material_execution_id)
        if (
            execution is None
            or execution.execution_code != fact.material_execution_id
            or execution.workline_id != work.workline_id
        ):
            raise ValueError("Fact material identity differs from bound Pickup")
        scan = await self._scan(db, execution.admission_evidence_id)
        if (
            scan.pickup_id != pickup.pickup_id
            or scan.command_code != pickup.command_code
            or scan.workline_id != work.workline_id
        ):
            raise ValueError("Fact scan identity differs from bound Pickup")
        task_id, source = await self.repository.material_source(db, work, pickup)
        initial = MaterialScannedFact(
            fact_id=fact.fact_id,
            evidence_id=fact.evidence_id,
            fact_version=fact.fact_version,
            material_execution_id=fact.material_execution_id,
            task_id=task_id,
            source_locator=source,
            barcode=scan.barcode,
            scanned_at=scan.scanned_at,
        )
        if type(fact) is EvidenceReadyFact:
            if fact.evidence_id != str(execution.admission_evidence_id):
                raise ValueError("Fact admission identity differs")
            return initial
        if type(fact) is not WmsResultReadyFact or type(fact.outcome) is not PickingMaterialOutcome:
            raise TypeError("6C requires scan or typed picking material result")
        evidence = await self.repository.material_evidence(db, int(fact.evidence_id))
        if evidence is None or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest:
            raise ValueError("material result Evidence digest differs")
        confirmation, request = await self.repository.material_confirmation(db, operation_id=fact.operation_id)
        (expected,) = material_scanned(initial)
        if (
            confirmation is None
            or confirmation.material_execution_id != execution.id
            or confirmation.response_evidence_id != int(fact.evidence_id)
            or confirmation.status != "COMPLETED"
            or fact.operation_id != execution.execution_code
            or request != replace(expected, material_execution_id=None, fact_id=None)
        ):
            raise ValueError("material result/request identity differs from bound Pickup")
        if type(fact.outcome.result) not in (PickingMaterialAccept, PickingMaterialWait):
            raise TypeError("6C only applies confirmed ACCEPT or WAIT")
        return MaterialDecidedFact(
            fact_id=fact.fact_id,
            evidence_id=fact.evidence_id,
            fact_version=fact.fact_version,
            material_execution_id=fact.material_execution_id,
            pickup_id=pickup.pickup_id,
            work_id=work.id,
            result=fact.outcome.result,
        )

    async def apply_in_session(self, db, *, fact):
        if type(fact) is MaterialDecidedFact and type(fact.result) is PickingMaterialAccept:
            _, pickup, _ = await self.repository.material_context_for_update(db, fact.pickup_id)
            await self.source_flow.handoff_in_session(
                db,
                work_id=fact.work_id,
                pickup_id=fact.pickup_id,
                material_execution_id=pickup.material_execution_id,
                evidence_id=int(fact.evidence_id),
                outcome=fact.result,
            )
