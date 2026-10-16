"""6C：精确关联由离线 fixture 给定，真实事务与 FactProcessor 接线。"""

import asyncio
from dataclasses import asdict, replace
from datetime import timedelta

import pytest
from automatic_picking.application.material_flow import AutomaticPickingMaterialFlow, MatchedMaterialScan
from automatic_picking.application.pickup_model import AutomaticPickingPickup
from automatic_picking.application.work_model import AutomaticPickingWork
from automatic_picking.definition import DEFINITION
from automatic_picking.handlers.material_flow import material_decided, material_scanned
from sqlalchemy import func, select
from test_pickup_identity_postgresql import (
    OTHER,
    TARGET,
    components,
    consume,
    database,
    prepared,
)

from src.app.device.models.command import DeviceCommand
from src.app.execution.models import InboundEvidence, MaterialExecution, WmsConfirmation
from src.app.execution.plugin_binding import PluginRuntimeBinding, StaticPluginBinding
from src.app.execution.services import DecisionApplier, FactProcessor, MaterialExecutionService, WmsConfirmationService
from src.app.wms_adapter.client import WmsAccessResult
from src.app.wms_adapter.dispatch import WmsDispatchCode
from src.app.wms_adapter.outbound_picking.material_decide_adapter import PickingMaterialDecideAdapter
from src.core.outbound_http import OutboundHttpDeliveryState
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
BARCODE = " 原样条码\n—not-PkgID "


class FixtureScanReader:
    """测试专用字段，不定义生产扫码报文、设备映射或关联算法。"""

    async def read_in_session(self, db, *, evidence_id):
        evidence = await db.get(InboundEvidence, evidence_id)
        return MatchedMaterialScan(evidence_id=evidence.id, **evidence.normalized_payload["fixture"])


def flow(p, *, repository=None):
    repo, _, source = components(p)
    return AutomaticPickingMaterialFlow(repository or repo, source, FixtureScanReader(), MaterialExecutionService())


def processor(p, *, application=None):
    application = application or flow(p)
    _, commands, _ = components(p)
    return FactProcessor(
        session_factory=p.sessions,
        plugin_binding=StaticPluginBinding(
            (
                PluginRuntimeBinding(
                    plugin_key=DEFINITION.plugin_key,
                    plugin_version=DEFINITION.plugin_version,
                    handlers=(material_scanned, material_decided),
                    fact_factory=application,
                    decision_application=application,
                ),
            )
        ),
        decision_applier=DecisionApplier(
            device_command_service=commands,
            wms_confirmation_service=WmsConfirmationService(),
            transport_service=object(),
            material_execution_service=MaterialExecutionService(),
            clock=lambda: p.now,
        ),
        clock=lambda: p.now,
    )


async def scan(p, source_pickup_id, **changes):
    async with p.sessions.begin() as db:
        pickup = await db.scalar(
            select(AutomaticPickingPickup).where(AutomaticPickingPickup.pickup_id == source_pickup_id)
        )
        value = {
            "pickup_id": source_pickup_id,
            "command_code": pickup.command_code,
            "workline_id": p.line.id,
            "barcode": BARCODE,
            "scanned_at": 456,
        }
        value.update(changes)
        payload = {"fixture": value}
        evidence = InboundEvidence(
            kind="DEVICE_EVENT",
            source_identity="fixture-scan6c:" + new_uuid7(),
            device_code="OFFLINE-SCANNER",
            command_code=pickup.command_code,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            received_at=p.now,
            processed_at=p.now,
            apply_status="APPLIED",
            contract_version="1.0",
            workline_id=p.line.id,
        )
        db.add(evidence)
        await db.flush()
        return evidence.id


async def bind(p, evidence_id):
    async with p.sessions.begin() as db:
        return await flow(p).bind_scan_in_session(db, evidence_id=evidence_id)


async def initial(p):
    pickup_id = await consume(p)
    evidence_id = await scan(p, pickup_id)
    material_id = await bind(p, evidence_id)
    assert await processor(p).process_batch() == 1
    async with p.sessions() as db:
        confirmation = await db.scalar(
            select(WmsConfirmation).where(WmsConfirmation.material_execution_id == material_id)
        )
        return pickup_id, evidence_id, material_id, confirmation


async def result(p, confirmation, *, data=None, operation_id=None):
    data = data or {
        "result": "ACCEPT",
        "target_locator": {"type": "RACK_SLOT", **asdict(TARGET)},
        "next_source_action": asdict(OTHER),
    }
    payload = {
        "operation_id": operation_id or confirmation.operation_id,
        "timestamp": 1,
        "code": "DECIDED",
        "data": data,
    }
    async with p.sessions.begin() as db:
        row = InboundEvidence(
            kind="WMS_RESULT",
            source_identity="fixture-result6c:" + confirmation.operation_id,
            operation="outbound.material.decide@v1",
            operation_id=payload["operation_id"],
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            workline_id=p.line.id,
            material_execution_id=confirmation.material_execution_id,
            received_at=p.now,
            processed_at=p.now,
            apply_status="APPLIED",
            contract_version="1.0",
        )
        db.add(row)
        await db.flush()
        live = await db.get(WmsConfirmation, confirmation.id)
        live.status, live.response_evidence_id, live.response_result = "COMPLETED", row.id, data["result"]
        return row.id


async def test_exact_scan_binding_is_idempotent_concurrent_and_does_not_rewrite_source_command(prepared):
    p = prepared
    pickup_id = await consume(p)
    event = await scan(p, pickup_id)
    ids = await asyncio.gather(bind(p, event), bind(p, event))
    assert ids[0] == ids[1]
    async with p.sessions() as db:
        pickup = await db.scalar(select(AutomaticPickingPickup).where(AutomaticPickingPickup.pickup_id == pickup_id))
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == pickup.command_code))
        execution = await db.get(MaterialExecution, ids[0])
        assert execution.execution_code == execution.material_trace_id == pickup_id
        assert execution.admission_evidence_id == event and pickup.material_execution_id == execution.id
        assert command.material_execution_id is None and command.execution_ref_id == pickup_id
        assert await db.scalar(select(func.count()).select_from(MaterialExecution)) == 1
    duplicate = await scan(p, pickup_id, scanned_at=789)
    with pytest.raises(ValueError, match="identity"):
        await bind(p, duplicate)


@pytest.mark.parametrize(
    "changes",
    [
        {"workline_id": 999999},
        {"command_code": "WRONG"},
        {"pickup_id": "missing"},
        {"barcode": ""},
        {"barcode": "x" * 1001},
        {"scanned_at": True},
    ],
)
async def test_wrong_scan_identity_rolls_back_without_material(prepared, changes):
    p = prepared
    pickup_id = await consume(p)
    event = await scan(p, pickup_id, **changes)
    with pytest.raises((ValueError, TypeError)):
        await bind(p, event)
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(MaterialExecution)) == 0
        assert (await db.get(InboundEvidence, event)).material_execution_id is None


async def test_initial_decide_freezes_original_payload_and_recovery_has_one_confirmation(prepared):
    p = prepared
    pickup, scan_id, material_id, confirmation = await initial(p)
    assert confirmation.operation_id == pickup
    assert confirmation.request_payload["data"] == {
        "task_id": p.task.task_id,
        "source_locator": {
            "type": "BIN_CELL",
            "rack_id": "RACK-1",
            "rack_face": "A",
            "bin_code": p.work.bin_code,
            "cell_id": "CELL-7",
        },
        "barcode": BARCODE,
        "scanned_at": 456,
    }
    assert await processor(p).process_batch() == 0
    assert await bind(p, scan_id) == material_id
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1


@pytest.mark.parametrize("next_source", [True, False])
async def test_accept_application_publication_and_work_handoff_are_atomic(prepared, next_source):
    p = prepared
    pickup_id, _, _, confirmation = await initial(p)
    data = {"result": "ACCEPT", "target_locator": {"type": "RACK_SLOT", **asdict(TARGET)}}
    if next_source:
        data["next_source_action"] = asdict(OTHER)
    event = await result(p, confirmation, data=data)
    application = flow(p)
    original = application.apply_in_session

    async def crash(db, *, fact):
        await original(db, fact=fact)
        raise RuntimeError("crash after handoff before publication")

    application.apply_in_session = crash
    assert await processor(p, application=application).process_batch() == 0
    async with p.sessions.begin() as db:
        pickup = await db.scalar(select(AutomaticPickingPickup).where(AutomaticPickingPickup.pickup_id == pickup_id))
        work = await db.get(AutomaticPickingWork, p.work.id)
        evidence = await db.get(InboundEvidence, event)
        assert pickup.accept_evidence_id is None and evidence.published_at is None
        assert work.authorization_evidence_id == p.ready.id and work.consumed_pickup_id == pickup_id
        evidence.decision_next_attempt_at = None
    assert await processor(p).process_batch() == 1
    async with p.sessions() as db:
        pickup = await db.scalar(select(AutomaticPickingPickup).where(AutomaticPickingPickup.pickup_id == pickup_id))
        work = await db.get(AutomaticPickingWork, p.work.id)
        assert pickup.accept_evidence_id == work.authorization_evidence_id == event
        assert work.consumed_pickup_id is None
        assert (await db.get(InboundEvidence, event)).published_at == p.now
    assert await processor(p).process_batch() == 0
    # ACCEPT 不是可靠取出；还不能消耗后继授权。
    async with p.sessions.begin() as db:
        assert await components(p)[2].advance_in_session(db, work_id=p.work.id) == 0


async def test_wait_is_determinate_and_retains_consumed_authorization_without_followup(prepared):
    p = prepared
    pickup_id, _, material_id, confirmation = await initial(p)
    event = await result(p, confirmation, data={"result": "WAIT", "retry_after_ms": 1234})
    assert await processor(p).process_batch() == 1
    async with p.sessions() as db:
        work = await db.get(AutomaticPickingWork, p.work.id)
        execution = await db.get(MaterialExecution, material_id)
        assert work.authorization_evidence_id == p.ready.id and work.consumed_pickup_id == pickup_id
        assert execution.status == "HOLD" and execution.last_transition_reason == "MATERIAL_DECIDE_WAIT"
        assert (await db.get(InboundEvidence, event)).published_at == p.now
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1


async def test_result_with_unmatched_operation_identity_cannot_handoff(prepared):
    p = prepared
    pickup_id, _, _, confirmation = await initial(p)
    event = await result(p, confirmation, operation_id="wrong-identity")
    assert await processor(p).process_batch() == 0
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingWork, p.work.id)).consumed_pickup_id == pickup_id
        assert (await db.get(InboundEvidence, event)).published_at is None


@pytest.mark.parametrize("final", ["WAIT", "ACCEPT"])
async def test_unknown_delivery_retries_original_identity_then_applies_determinate_result(prepared, final):
    p = prepared
    pickup_id, _, material_id, confirmation = await initial(p)

    class Client:
        def __init__(self):
            self.calls = []

        async def post(self, path, *, json, **kwargs):
            self.calls.append((path, json))
            unknown = len(self.calls) == 1
            data = (
                {"result": "WAIT", "retry_after_ms": 1234}
                if final == "WAIT"
                else {"result": "ACCEPT", "target_locator": {"type": "RACK_SLOT", **asdict(TARGET)}}
            )
            return WmsAccessResult(
                delivery_state=(
                    OutboundHttpDeliveryState.DELIVERY_UNKNOWN
                    if unknown
                    else OutboundHttpDeliveryState.RESPONSE_RECEIVED
                ),
                failure_kind=None,
                status_code=None if unknown else 200,
                response_headers=(("content-type", "application/json"),),
                body_present=not unknown,
                json_body=None
                if unknown
                else {"operation_id": json["operation_id"], "timestamp": 1, "code": "DECIDED", "data": data},
                json_failure=None,
            )

    client = Client()
    dispatcher = WmsConfirmationService(session_factory=p.sessions, adapter=PickingMaterialDecideAdapter(client))
    assert await dispatcher.dispatch_batch(now=p.now) == 1
    async with p.sessions() as db:
        live = await db.get(WmsConfirmation, confirmation.id)
        assert live.status == "PENDING" and live.retry_eligible and live.response_evidence_id is None
        assert live.next_attempt_at == p.now + timedelta(seconds=1) and live.attempt_count == 1
        assert live.operation_id == pickup_id and live.request_payload == confirmation.request_payload
        assert (await db.get(AutomaticPickingWork, p.work.id)).consumed_pickup_id == pickup_id
    assert await processor(p).process_batch() == 0
    assert await dispatcher.dispatch_batch(now=p.now + timedelta(seconds=2)) == 1
    assert client.calls == [("/api/v1/wes/decisions", confirmation.request_payload)] * 2
    assert await processor(p).process_batch() == 1
    async with p.sessions() as db:
        live = await db.get(WmsConfirmation, confirmation.id)
        work = await db.get(AutomaticPickingWork, p.work.id)
        assert live.status == "COMPLETED" and live.response_result == final
        assert (await db.get(MaterialExecution, material_id)).status == "HOLD"
        assert work.consumed_pickup_id == (pickup_id if final == "WAIT" else None)
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 1


async def test_next_material_decide_does_not_wait_for_previous_material_close(prepared):
    p = prepared
    pickup_id, _, material_id, confirmation = await initial(p)
    await result(p, confirmation)
    assert await processor(p).process_batch() == 1
    async with p.sessions.begin() as db:
        pickup = await db.scalar(select(AutomaticPickingPickup).where(AutomaticPickingPickup.pickup_id == pickup_id))
        removed = InboundEvidence(
            kind="DEVICE_RESULT",
            source_identity="fixture-removed:" + pickup_id,
            command_code=pickup.command_code,
            device_code="OFFLINE-SOURCE",
            normalized_payload={},
            payload_digest=canonical_json_digest({}),
            workline_id=p.line.id,
            received_at=p.now,
            processed_at=p.now,
            published_at=p.now,
            apply_status="APPLIED",
            decision_digest="e" * 64,
        )
        db.add(removed)
    async with p.sessions.begin() as db:
        assert await components(p)[2].advance_in_session(db, work_id=p.work.id) == 1
        work = await db.get(AutomaticPickingWork, p.work.id)
        next_pickup = work.consumed_pickup_id
        assert (await db.get(MaterialExecution, material_id)).status == "HOLD"
    event = await scan(p, next_pickup)
    next_material = await bind(p, event)
    assert next_material != material_id
    assert await processor(p).process_batch() == 1
    async with p.sessions() as db:
        request = await db.scalar(select(WmsConfirmation).where(WmsConfirmation.material_execution_id == next_material))
        assert request.request_payload["data"]["source_locator"]["cell_id"] == OTHER.cell_id
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 2


@pytest.mark.parametrize(
    "corruption", ["request_barcode", "request_operation", "response_reference", "scan_digest", "result_digest"]
)
async def test_persisted_identity_drift_cannot_publish_or_release_authorization(prepared, corruption):
    p = prepared
    pickup_id, scan_id, _, confirmation = await initial(p)
    event = await result(p, confirmation, data={"result": "WAIT", "retry_after_ms": 1000})
    async with p.sessions.begin() as db:
        live = await db.get(WmsConfirmation, confirmation.id)
        if corruption.startswith("request_"):
            payload = {**live.request_payload, "data": {**live.request_payload["data"]}}
            if corruption == "request_barcode":
                payload["data"]["barcode"] = "wrong-barcode"
            else:
                payload["operation_id"] = new_uuid7()
            live.request_payload, live.request_digest = payload, canonical_json_digest(payload)
        elif corruption == "response_reference":
            live.response_evidence_id = p.ready.id
        else:
            evidence = await db.get(InboundEvidence, scan_id if corruption == "scan_digest" else event)
            evidence.payload_digest = "f" * 64
    assert await processor(p).process_batch() == 0
    async with p.sessions() as db:
        assert (await db.get(AutomaticPickingWork, p.work.id)).consumed_pickup_id == pickup_id
        assert (await db.get(InboundEvidence, event)).published_at is None


async def test_scan_creation_and_pickup_association_rollback_together(prepared):
    p = prepared
    pickup_id = await consume(p)
    event = await scan(p, pickup_id)

    class Crash(MaterialExecutionService):
        async def create_or_get_for_initial_evidence(self, *args, **kwargs):
            await super().create_or_get_for_initial_evidence(*args, **kwargs)
            raise RuntimeError("crash after material create")

    application = flow(p)
    application.executions = Crash()
    with pytest.raises(RuntimeError, match="crash"):
        async with p.sessions.begin() as db:
            await application.bind_scan_in_session(db, evidence_id=event)
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(MaterialExecution)) == 0
        pickup = await db.scalar(select(AutomaticPickingPickup).where(AutomaticPickingPickup.pickup_id == pickup_id))
        assert pickup.material_execution_id is None
        assert (await db.get(InboundEvidence, event)).material_execution_id is None
    assert await bind(p, event) is not None


async def test_scan_replay_waits_on_evidence_before_workline_while_fact_processor_prepares(prepared):
    from automatic_picking.application.pickup_repository import PickupRepository

    from src.app.workline.repositories.workline_repository import WorkLineRepository

    p = prepared
    pickup_id = await consume(p)
    event = await scan(p, pickup_id)
    material_id = await bind(p, event)
    processor_holds_evidence, release_processor = asyncio.Event(), asyncio.Event()
    bind_attempts_evidence, bind_holds_line = asyncio.Event(), asyncio.Event()

    class PausedWorklines(WorkLineRepository):
        async def get_for_update(self, db, workline_id):
            processor_holds_evidence.set()
            await release_processor.wait()
            return await super().get_for_update(db, workline_id)

    class ObservedPickupRepository(PickupRepository):
        async def material_context_for_update(self, db, pickup_id):
            value = await super().material_context_for_update(db, pickup_id)
            bind_holds_line.set()
            return value

        async def scan_evidence_for_update(self, db, evidence_id):
            bind_attempts_evidence.set()
            return await super().scan_evidence_for_update(db, evidence_id)

    running = processor(p)
    running._worklines = PausedWorklines()
    binding = flow(p, repository=ObservedPickupRepository(p.observations))

    async def replay():
        async with p.sessions.begin() as db:
            return await binding.bind_scan_in_session(db, evidence_id=event)

    async with asyncio.timeout(10):
        process = asyncio.create_task(running.process_batch())
        await processor_holds_evidence.wait()
        replay_task = asyncio.create_task(replay())
        await bind_attempts_evidence.wait()
        try:
            assert not bind_holds_line.is_set(), "binding must not hold WorkLine while waiting on processor Evidence"
        finally:
            release_processor.set()
            results = await asyncio.gather(process, replay_task, return_exceptions=True)
        assert results == [1, material_id]
