"""REPLACE 仅承接已发布 READY 的旧架 F01；不创建新架进场或 PUT。"""

from dataclasses import dataclass

from .target_replacement_repository import ROUND_PREFIX, TARGET_DEPARTURE_STEP, TargetReplacementRepository


@dataclass(frozen=True, slots=True)
class MatchedTargetDeparture:
    workline_id: int
    material_execution_id: int
    accept_evidence_id: int
    old_rack_id: str
    inbound_client_request_id: str
    arrival_evidence_id: int
    departure_operation_id: str

    def __post_init__(self):
        if any(
            type(v) is not int or v <= 0
            for v in (self.workline_id, self.material_execution_id, self.accept_evidence_id, self.arrival_evidence_id)
        ) or any(
            type(v) is not str or not v.strip() or len(v) > limit
            for v, limit in (
                (self.old_rack_id, 100),
                (self.inbound_client_request_id, 120),
                (self.departure_operation_id, 120),
            )
        ):
            raise ValueError("REPLACE departure requires exact current identities")


class AutomaticPickingTargetReplacementFlow:
    def __init__(self, creator, reader, *, repository=None):
        self.creator, self.reader = creator, reader
        self.repository = repository or TargetReplacementRepository()

    async def recover_in_session(self, db, *, line_id):
        # 已创建义务不再依赖 Pickup、旧进场历史或当前映射。
        for transport_id in await self.repository.pending(db, line_id):
            await self.repository.transport(db, line_id, transport_id)

    async def prepare_in_session(self, db, *, work, pickup, execution, accepted):
        if pickup.target_departure_transport_task_id is not None:
            await self.repository.transport(db, work.workline_id, pickup.target_departure_transport_task_id)
            return 0
        observed = await self.reader.read_in_session(
            db, workline_id=work.workline_id, material_execution_id=execution.id, target=accepted.target_locator
        )
        if observed is None:
            return 0
        if (
            type(observed) is not MatchedTargetDeparture
            or observed.workline_id != work.workline_id
            or observed.material_execution_id != execution.id
            or observed.accept_evidence_id != pickup.accept_evidence_id
            or observed.old_rack_id == accepted.target_locator.rack_id
        ):
            raise ValueError("REPLACE departure input differs from this ACCEPT")
        original = await self.repository.for_round(db, work.workline_id, observed.inbound_client_request_id)
        if original is not None:
            from sqlalchemy import select

            from src.app.transport.models import TransportTask

            transport_id = await db.scalar(
                select(TransportTask.transport_task_id).where(
                    TransportTask.client_request_id == original.client_request_id
                )
            )
            _, binding = await self.repository.transport(db, work.workline_id, transport_id)
            fact = await self.repository.decision(
                db, work.workline_id, observed.departure_operation_id, observed.old_rack_id
            )
            if fact is None or fact.evidence_id != binding.source_evidence_id:
                raise ValueError("REPLACE round already owns another frozen departure")
            if not await self.repository.current_arrival(db, work.workline_id, observed, fact):
                return 0
        else:
            snapshot = await self.repository.decision(
                db, work.workline_id, observed.departure_operation_id, observed.old_rack_id
            )
            if snapshot is None or not await self.repository.current_arrival(db, work.workline_id, observed, snapshot):
                return 0
            handle = await self.creator.create_transfer_departure(
                db,
                workline_id=work.workline_id,
                source_evidence_id=snapshot.evidence_id,
                operation_id=ROUND_PREFIX + observed.inbound_client_request_id,
                step=TARGET_DEPARTURE_STEP,
                rack_id=observed.old_rack_id,
                destination=snapshot.outcome.result.rack_destination,
            )
            transport_id = handle.transport_task_id
        pickup.target_departure_transport_task_id = transport_id
        await db.flush()
        return 1

    async def handed_off_in_session(self, db, *, line_id, transport_id):
        task, _ = await self.repository.transport(db, line_id, transport_id)
        return (
            task.status == "ACCEPTED"
            or task.result_deadline_at is not None
            or await self.repository.finished(db, line_id, transport_id)
        )

    async def apply_transport_result_in_session(self, db, *, line, evidence):
        task, binding = await self.repository.transport(db, line.id, evidence.transport_task_id)
        return (
            "TARGET_REPLACEMENT_DEPARTURE_OBSERVED"
            if self.repository.matches(evidence, task, binding)
            else "RECONCILING"
        )
