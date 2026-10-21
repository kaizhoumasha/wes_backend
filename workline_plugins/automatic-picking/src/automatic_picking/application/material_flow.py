"""精确扫码关联后的单盘绑定、Fact 增强与 ACCEPT 事务应用；不安装生产扫码入口。"""

from dataclasses import dataclass, replace
from datetime import timedelta

from wes_plugin_sdk import (
    DeviceResultReadyFact,
    EvidenceReadyFact,
    MaterialMovementReportOutcome,
    PickingMaterialAccept,
    PickingMaterialOutcome,
    PickingMaterialWait,
    WmsResultReadyFact,
    wms_operations,
)

from automatic_picking.handlers.material_flow import MaterialDecidedFact, MaterialScannedFact, material_scanned
from automatic_picking.handlers.placement import MaterialPutFact, MaterialReportedFact
from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE
from src.app.execution.services.inbound_evidence_service import InboundEvidenceDigestPolicy, normalize_payload
from src.core.uuid7 import new_uuid7


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
    def __init__(
        self,
        repository,
        source_flow,
        scan_reader,
        executions,
        *,
        scheduler,
        reader,
        put_flow=None,
        uuid_factory=new_uuid7,
    ):
        self.repository, self.source_flow, self.scan_reader, self.executions = (
            repository,
            source_flow,
            scan_reader,
            executions,
        )
        self.scheduler, self.reader, self.uuid_factory = scheduler, reader, uuid_factory
        self.put_flow = put_flow

    async def _intent(self, db, work, pickup, execution, operation_id):
        if (
            execution is None
            or execution.execution_code != pickup.pickup_id
            or execution.workline_id != work.workline_id
        ):
            raise ValueError("material identity differs from bound Pickup")
        scan = await self._scan(db, execution.admission_evidence_id)
        if (
            scan.pickup_id != pickup.pickup_id
            or scan.command_code != pickup.command_code
            or scan.workline_id != work.workline_id
        ):
            raise ValueError("scan identity differs from bound Pickup")
        task_id, source = await self.repository.material_source(db, work, pickup)
        return wms_operations.outbound_material_decide(
            operation_id=operation_id,
            task_id=task_id,
            source_locator=source,
            barcode=scan.barcode,
            scanned_at=scan.scanned_at,
        )

    async def _current(self, db, pickup, execution):
        if pickup.material_confirmation_id is None:
            return await self.reader.by_operation_id(
                db,
                material_execution_id=execution.id,
                operation_id=execution.execution_code,
            )
        snapshot = await self.reader.by_id(
            db,
            material_execution_id=execution.id,
            confirmation_id=pickup.material_confirmation_id,
        )
        if snapshot is None:
            raise ValueError("current material request is missing")
        return snapshot

    @staticmethod
    def _wait_due(pickup, snapshot):
        due = snapshot.completed_at + timedelta(milliseconds=snapshot.outcome.result.retry_after_ms)
        if pickup.material_retry_at is not None and pickup.material_retry_at != due:
            raise ValueError("WAIT due time differs from frozen result")
        changed = pickup.material_retry_at is None
        pickup.material_retry_at = due
        return changed

    async def advance_in_session(self, db, *, line_id, now):
        count = 0
        for pickup_id in await self.repository.material_pending(db, line_id):
            work, pickup, _ = await self.repository.material_context_for_update(db, pickup_id)
            execution = await self.repository.material_execution(db, pickup.material_execution_id)
            if execution.status == "CLOSED" or pickup.accept_evidence_id is not None:
                continue
            snapshot = await self._current(db, pickup, execution)
            if snapshot is None:
                initial = await self.repository.material_evidence(db, execution.admission_evidence_id)
                if initial is None or initial.published_at is not None:
                    raise ValueError("published scan lost its material request")
                continue
            expected = await self._intent(db, work, pickup, execution, snapshot.intent.operation_id)
            if snapshot.intent != expected:
                raise ValueError("current material request differs from frozen scan")
            changed = pickup.material_confirmation_id is None
            pickup.material_confirmation_id = snapshot.confirmation_id
            if (
                snapshot.published_at is not None
                and snapshot.outcome is not None
                and type(snapshot.outcome.result) is PickingMaterialWait
            ):
                changed = self._wait_due(pickup, snapshot) or changed
                if pickup.material_retry_at <= now and await self.repository.material_retry_allowed(db, work, pickup):
                    intent = wms_operations.outbound_material_decide(
                        operation_id=self.uuid_factory(),
                        task_id=expected.task_id,
                        source_locator=expected.source_locator,
                        barcode=expected.barcode,
                        scanned_at=expected.scanned_at,
                    )
                    pickup.material_confirmation_id = await self.scheduler.create_in_session(
                        db,
                        intent,
                        material_execution_id=execution.id,
                        created_at=now,
                    )
                    pickup.material_retry_at = None
                    changed = True
            count += int(changed)
        await db.flush()
        return count

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
            or (evidence.command_code is not None and evidence.command_code != scan.command_code)
            or normalize_payload(evidence.normalized_payload, digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE)[1]
            != evidence.payload_digest
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
            or (evidence.command_code is not None and evidence.command_code != pickup.command_code)
            or command.execution_ref_type != WORKLINE_BUSINESS_REF_TYPE
            or command.material_execution_id is not None
            or normalize_payload(evidence.normalized_payload, digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE)[1]
            != evidence.payload_digest
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
        evidence.command_code = command.command_code
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
        if self.put_flow is not None and (
            type(fact) is DeviceResultReadyFact
            or (type(fact) is WmsResultReadyFact and type(fact.outcome) is MaterialMovementReportOutcome)
        ):
            return await self.put_flow.build(db, fact)
        work, pickup, _ = await self.repository.material_context_for_update(db, fact.material_execution_id)
        execution = await self.repository.material_execution(db, pickup.material_execution_id)
        if (
            execution is None
            or execution.execution_code != fact.material_execution_id
            or execution.workline_id != work.workline_id
        ):
            raise ValueError("Fact material identity differs from bound Pickup")
        intent = await self._intent(db, work, pickup, execution, execution.execution_code)
        initial = MaterialScannedFact(
            fact_id=fact.fact_id,
            evidence_id=fact.evidence_id,
            fact_version=fact.fact_version,
            material_execution_id=fact.material_execution_id,
            task_id=intent.task_id,
            source_locator=intent.source_locator,
            barcode=intent.barcode,
            scanned_at=intent.scanned_at,
        )
        if type(fact) is EvidenceReadyFact:
            if fact.evidence_id != str(execution.admission_evidence_id):
                raise ValueError("Fact admission identity differs")
            return initial
        if type(fact) is not WmsResultReadyFact or type(fact.outcome) is not PickingMaterialOutcome:
            raise TypeError("6C requires scan or typed picking material result")
        confirmation = await self.reader.by_operation_id(
            db, material_execution_id=execution.id, operation_id=fact.operation_id
        )
        (expected,) = material_scanned(initial)
        if (
            confirmation is None
            or confirmation.evidence_id != int(fact.evidence_id)
            or confirmation.status != "COMPLETED"
            or (
                pickup.material_confirmation_id is not None
                and confirmation.confirmation_id != pickup.material_confirmation_id
            )
            or (pickup.material_confirmation_id is None and fact.operation_id != execution.execution_code)
            or confirmation.intent
            != replace(expected, operation_id=fact.operation_id, material_execution_id=None, fact_id=None)
            or confirmation.outcome != fact.outcome
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
        if type(fact) in (MaterialPutFact, MaterialReportedFact):
            await self.put_flow.apply_in_session(db, fact=fact)
            return
        _, pickup, _ = await self.repository.material_context_for_update(db, fact.material_execution_id)
        execution = await self.repository.material_execution(db, pickup.material_execution_id)
        snapshot = await self._current(db, pickup, execution)
        if snapshot is None:
            raise ValueError("material Decision has no reliable request")
        if type(fact) is MaterialScannedFact:
            if snapshot.intent.operation_id != execution.execution_code:
                return
            pickup.material_confirmation_id = snapshot.confirmation_id
        elif type(fact) is MaterialDecidedFact:
            if snapshot.evidence_id != int(fact.evidence_id):
                return
            pickup.material_confirmation_id = snapshot.confirmation_id
            if type(fact.result) is PickingMaterialWait:
                self._wait_due(pickup, snapshot)
            else:
                pickup.material_retry_at = None
        if type(fact) is MaterialDecidedFact and type(fact.result) is PickingMaterialAccept:
            await self.source_flow.handoff_in_session(
                db,
                work_id=fact.work_id,
                pickup_id=fact.pickup_id,
                material_execution_id=pickup.material_execution_id,
                evidence_id=int(fact.evidence_id),
                outcome=fact.result,
            )
        await db.flush()
