"""7B：成功 PUT 和权威 PkgID 仅由离线输入给定，无设备/WMS 派发。"""

import asyncio
from dataclasses import asdict
from datetime import timedelta

import pytest
import pytest_asyncio
from automatic_picking.application.plugin import build_plugin
from sqlalchemy import func, select
from test_material_flow_postgresql import FixtureScanReader
from test_pickup_identity_postgresql import TARGET, components
from test_rotated_target_put_postgresql import combined, published_through_combined, rotating, target
from test_rotated_target_put_postgresql import removed as rotated_source_removed
from test_target_put_postgresql import accepted, advance, current_pickup, database, prepared
from wes_plugin_sdk import DeviceResultReadyFact, PickingTargetRackSlot

from src.app.device.models.command import DeviceCommand
from src.app.execution.models import InboundEvidence, MaterialExecution, WmsConfirmation
from src.app.execution.plugin_binding import StaticPluginBinding
from src.app.execution.services import DecisionApplier, FactProcessor, MaterialExecutionService, WmsConfirmationService
from src.app.wms_integration.outbound_picking.services import MaterialDecideResultReader, MaterialDecideScheduler
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
PKG = "权威-PKG-7B"


class FixturePutReader:
    """只解释 fixture；生产未提供该读口，不定义设备报文或 PkgID 来源。"""

    def __init__(self, p):
        self.p = p

    async def read_in_session(self, db, *, evidence_id):
        from automatic_picking.application.placement_flow import MatchedPutResult

        if not self.p.identity_available:
            return None
        evidence = await db.get(InboundEvidence, evidence_id)
        value = evidence.normalized_payload["fixture"]
        if value["pkg_id"] is None:
            return None
        return MatchedPutResult(**{**value, "target": PickingTargetRackSlot(**value["target"])})


def application(p, *, repository=None):
    from automatic_picking.application.material_flow import AutomaticPickingMaterialFlow
    from automatic_picking.application.placement_flow import AutomaticPickingPlacementFlow

    repo, _, source = components(p)
    repo = repository or repo
    return AutomaticPickingMaterialFlow(
        repo,
        source,
        FixtureScanReader(),
        MaterialExecutionService(),
        scheduler=MaterialDecideScheduler(),
        reader=MaterialDecideResultReader(),
        put_flow=AutomaticPickingPlacementFlow(repo, MaterialDecideResultReader(), FixturePutReader(p)),
    )


def processor(p, *, app=None, confirmations=None):
    app = app or application(p)
    binding = build_plugin(scan_flow=None, batch_driver=None, works=None, material_flow=app).runtime_binding
    return FactProcessor(
        session_factory=p.sessions,
        plugin_binding=StaticPluginBinding((binding,)),
        decision_applier=DecisionApplier(
            device_command_service=components(p)[1],
            wms_confirmation_service=confirmations or WmsConfirmationService(),
            transport_service=object(),
            material_execution_service=MaterialExecutionService(),
            clock=lambda: p.now,
        ),
        clock=lambda: p.now,
    )


@pytest_asyncio.fixture
async def placed(accepted):
    p = accepted
    assert await advance(p) == 1
    return await successful_put(p)


async def successful_put(p):
    """沿原 PUT 注入明确成功/PkgID；只构造既有 7B 离线输入。"""
    p.identity_available = True
    async with p.sessions.begin() as db:
        removed = await db.scalar(
            select(InboundEvidence).where(InboundEvidence.source_identity == "fixture-removed:" + p.pickup_id)
        )
        # 来源取出已由离线输入给定并消费；不把它当作本片待处理 PUT。
        removed.published_at, removed.decision_digest = p.now, canonical_json_digest({"fixture": "source-removal"})
        pickup = await current_pickup(p, db)
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == pickup.put_command_code))
        p.put_code = command.command_code
        payload = {
            "fixture": {
                "evidence_id": 0,
                "workline_id": p.line.id,
                "pickup_id": p.pickup_id,
                "material_execution_id": p.material_id,
                "command_code": command.command_code,
                "device_code": command.device_code,
                "target": asdict(TARGET),
                "pkg_id": PKG,
                "occurred_at": 987,
            }
        }
        evidence = InboundEvidence(
            kind="DEVICE_RESULT",
            source_identity="fixture-put7b:" + new_uuid7(),
            device_code=command.device_code,
            command_code=command.command_code,
            normalized_payload=payload,
            payload_digest=canonical_json_digest(payload),
            workline_id=p.line.id,
            material_execution_id=p.material_id,
            received_at=p.now,
            processed_at=p.now,
            apply_status="APPLIED",
            contract_version="1.0",
        )
        db.add(evidence)
        await db.flush()
        payload = {"fixture": {**payload["fixture"], "evidence_id": evidence.id}}
        evidence.normalized_payload, evidence.payload_digest = payload, canonical_json_digest(payload)
        command.status, command.completed_at, command.result_evidence_id = "SUCCEEDED", p.now, evidence.id
        p.put_evidence_id = evidence.id
    return p


@pytest_asyncio.fixture
async def rotated_placed(target):
    p = target
    app = combined(p)
    assert await advance(p, app) == 1
    await published_through_combined(p, app)
    await rotated_source_removed(p)
    assert await advance(p, app) == 1
    return await successful_put(p)


async def test_success_before_ack_atomically_records_position_and_report_without_closing(placed):
    p = placed
    assert await processor(p).process_batch() == 1
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        assert pickup.put_result_evidence_id == p.put_evidence_id
        report = await db.get(WmsConfirmation, pickup.movement_confirmation_id)
        assert report.status == "PENDING" and report.material_execution_id == p.material_id
        assert report.request_payload["operation"] == "outbound.material.movement_report@v1"
        assert report.request_payload["data"] == {
            "task_id": p.task.task_id,
            "source_locator": {
                "type": "BIN_CELL",
                "rack_id": "RACK-1",
                "rack_face": "A",
                "bin_code": "A123456789",
                "cell_id": "CELL-7",
            },
            "PkgID": PKG,
            "to_locator": {"type": "RACK_SLOT", "rack_id": "TARGET", "rack_face": "A", "slot_id": "S1"},
            "occurred_at": 987,
        }
        assert (await db.get(InboundEvidence, p.put_evidence_id)).published_at is not None
        assert (await db.get(MaterialExecution, p.material_id)).status == "RUNNING"
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == p.put_code))
        assert command.ack_received_at is None
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 2
    assert await processor(p).process_batch() == 0


async def counts(p):
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        evidence = await db.get(InboundEvidence, p.put_evidence_id)
        return (
            pickup.put_result_evidence_id,
            pickup.movement_confirmation_id,
            await db.scalar(select(func.count()).select_from(WmsConfirmation)),
            evidence.published_at,
        )


async def change_fixture(p, **changes):
    async with p.sessions.begin() as db:
        evidence = await db.get(InboundEvidence, p.put_evidence_id)
        payload = {"fixture": {**evidence.normalized_payload["fixture"], **changes}}
        evidence.normalized_payload, evidence.payload_digest = payload, canonical_json_digest(payload)


async def assert_rotated_report(p):
    async with p.sessions() as db:
        pickup = await current_pickup(p, db)
        report = await db.get(WmsConfirmation, pickup.movement_confirmation_id)
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == p.put_code))
        assert pickup.target_rotation_transport_task_id is not None
        assert pickup.put_result_evidence_id == p.put_evidence_id
        assert report.status == "PENDING" and report.operation_id == p.pickup_id
        assert report.material_execution_id == p.material_id
        assert report.request_payload["data"] == {
            "task_id": p.task.task_id,
            "source_locator": {
                "type": "BIN_CELL",
                "rack_id": "RACK-1",
                "rack_face": "A",
                "bin_code": "A123456789",
                "cell_id": "CELL-7",
            },
            "PkgID": PKG,
            "to_locator": {"type": "RACK_SLOT", "rack_id": "TARGET", "rack_face": "A", "slot_id": "S1"},
            "occurred_at": 987,
        }
        assert command.ack_received_at is None and command.result_evidence_id == p.put_evidence_id
        assert (await db.get(MaterialExecution, p.material_id)).status == "RUNNING"
        fact = await db.get(InboundEvidence, p.put_evidence_id)
        assert fact.published_at is not None and fact.decision_digest is not None
        assert await db.scalar(select(func.count()).select_from(WmsConfirmation)) == 2
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 2


async def test_rotated_success_before_ack_creates_report_without_rechecking_rotation(rotated_placed):
    from src.app.execution.models import PositionProjection

    p = rotated_placed
    # PUT 已完成；架面后来移动不抹掉原 Command 的确定成功事实。
    async with p.sessions.begin() as db:
        position = await db.scalar(select(PositionProjection).where(PositionProjection.object_id == TARGET.rack_id))
        position.arrival_face = "B"
    assert await processor(p).process_batch() == 1
    await assert_rotated_report(p)


async def test_rotated_report_concurrent_recovery_and_replay_keep_original_identity(rotated_placed):
    p = rotated_placed
    async with asyncio.timeout(15):
        assert sorted(
            await asyncio.gather(processor(p).process_batch(), processor(p).process_batch(), advance(p, combined(p)))
        ) == [0, 0, 1]
    first = await counts(p)
    async with p.sessions.begin() as db:
        (await db.get(InboundEvidence, p.put_evidence_id)).published_at = None
    assert await processor(p).process_batch() == 1
    assert await counts(p) == first
    assert await advance(p, combined(p)) == 0 and await processor(p).process_batch() == 0
    await assert_rotated_report(p)


@pytest.mark.parametrize("point", ["after_report", "after_link"])
async def test_rotated_report_crash_rolls_back_then_rebuilds_from_original_evidence(rotated_placed, point):
    p = rotated_placed
    app = application(p)

    class CrashConfirmation(WmsConfirmationService):
        async def create_or_get(self, *args, **kwargs):
            value = await super().create_or_get(*args, **kwargs)
            if point == "after_report":
                raise RuntimeError("offline rotated report crash")
            return value

    if point == "after_link":
        original = app.put_flow.apply_in_session

        async def crash(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("offline rotated link crash")

        app.put_flow.apply_in_session = crash
    assert await processor(p, app=app, confirmations=CrashConfirmation()).process_batch() == 0
    assert await counts(p) == (None, None, 1, None)
    p.now += timedelta(seconds=2)
    assert await processor(p).process_batch() == 1
    await assert_rotated_report(p)


@pytest.mark.parametrize(
    "changes",
    [
        {"command_code": "FOREIGN-PUT"},
        {"pickup_id": "FOREIGN-PICKUP"},
        {"evidence_id": 999999},
        {"target": {"rack_id": "OTHER", "rack_face": "A", "slot_id": "S1", "rack_layer": 1, "rack_column": 1}},
    ],
)
async def test_rotated_wrong_success_identity_never_reports(rotated_placed, changes):
    p = rotated_placed
    await change_fixture(p, **changes)
    assert await processor(p).process_batch() == 0
    assert await counts(p) == (None, None, 1, None)


async def test_rotated_missing_pkg_defers_original_evidence_without_report(rotated_placed):
    p = rotated_placed
    await change_fixture(p, pkg_id=None)
    assert await processor(p).process_batch() == 1
    assert await counts(p) == (None, None, 1, None)
    async with p.sessions() as db:
        fact = await db.get(InboundEvidence, p.put_evidence_id)
        assert fact.decision_digest is None and fact.decision_next_attempt_at is not None
    await change_fixture(p, pkg_id=PKG)
    assert await processor(p).process_batch() == 1
    await assert_rotated_report(p)


async def test_concurrent_processors_and_batch_recovery_keep_one_position_and_report(placed):
    p = placed
    async with asyncio.timeout(15):
        assert sorted(await asyncio.gather(processor(p).process_batch(), processor(p).process_batch(), advance(p))) == [
            0,
            0,
            1,
        ]
    first = await counts(p)
    assert first[0] == p.put_evidence_id and first[1] is not None and first[2] == 2
    p.now += timedelta(days=1)
    assert await advance(p) == 0 and await processor(p).process_batch() == 0
    assert await counts(p) == first


@pytest.mark.parametrize("point", ["after_report", "after_link"])
async def test_report_position_and_publication_rollback_together_then_retry(placed, point):
    p = placed
    app = application(p)

    class CrashConfirmation(WmsConfirmationService):
        async def create_or_get(self, *args, **kwargs):
            value = await super().create_or_get(*args, **kwargs)
            if point == "after_report":
                raise RuntimeError("fixture crash after report")
            return value

    if point == "after_link":
        original = app.put_flow.apply_in_session

        async def crash(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("fixture crash after link")

        app.put_flow.apply_in_session = crash
    assert await processor(p, app=app, confirmations=CrashConfirmation()).process_batch() == 0
    assert await counts(p) == (None, None, 1, None)
    p.now += timedelta(seconds=2)
    assert await processor(p).process_batch() == 1
    assert (await counts(p))[2] == 2


async def test_worker_dies_after_claim_lease_recovers_without_notification(placed):
    from src.app.execution.repositories import inbound_evidence_repository

    p = placed
    async with p.sessions.begin() as db:
        rows = await inbound_evidence_repository.claim_decision_batch(
            db,
            now=p.now,
            claim_token="dead-worker",
            claim_expires_at=p.now + timedelta(seconds=30),
            limit=100,
        )
        assert [row.id for row in rows] == [p.put_evidence_id]
    assert await processor(p).process_batch() == 0
    p.now += timedelta(seconds=31)
    assert await processor(p).process_batch() == 1
    assert (await counts(p))[0] == p.put_evidence_id


@pytest.mark.parametrize("state", ["PENDING", "ACKNOWLEDGED", "RECONCILING", "FAILED", "TIMED_OUT"])
async def test_non_success_command_never_reports_a_position(placed, state):
    p = placed
    async with p.sessions.begin() as db:
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == p.put_code))
        command.status = state
    assert await processor(p).process_batch() == 1
    assert await counts(p) == (None, None, 1, None)
    async with p.sessions() as db:
        evidence = await db.get(InboundEvidence, p.put_evidence_id)
        assert evidence.decision_digest is None and evidence.decision_next_attempt_at is not None


@pytest.mark.parametrize("missing", ["reader_fact", "pkg_id"])
async def test_missing_authority_is_durably_deferred_then_same_evidence_recovers(placed, missing):
    p = placed
    if missing == "reader_fact":
        p.identity_available = False
    else:
        await change_fixture(p, pkg_id=None)
    assert await processor(p).process_batch() == 1
    assert await counts(p) == (None, None, 1, None)
    p.identity_available = True
    if missing == "pkg_id":
        await change_fixture(p, pkg_id=PKG)
    assert await processor(p).process_batch() == 1
    assert (await counts(p))[0] == p.put_evidence_id


@pytest.mark.parametrize(
    "changes",
    [
        {"evidence_id": 999999},
        {"workline_id": 999999},
        {"pickup_id": "FOREIGN-PICKUP"},
        {"material_execution_id": 999999},
        {"command_code": "FOREIGN-COMMAND"},
        {"device_code": "FOREIGN-DEVICE"},
        {"target": {"rack_id": "OTHER", "rack_face": "A", "slot_id": "S1", "rack_layer": 1, "rack_column": 1}},
        {"pkg_id": ""},
        {"pkg_id": "X" * 257},
        {"pkg_id": 123},
        {"occurred_at": -1},
        {"occurred_at": True},
    ],
)
async def test_unmatched_or_invalid_put_fact_cannot_commit_position_or_report(placed, changes):
    p = placed
    await change_fixture(p, **changes)
    assert await processor(p).process_batch() == 0
    assert await counts(p) == (None, None, 1, None)


@pytest.mark.parametrize("drift", ["digest", "base_command", "base_device", "result_reference", "accept_reference"])
async def test_persisted_identity_drift_never_creates_report(placed, drift):
    p = placed
    async with p.sessions.begin() as db:
        evidence = await db.get(InboundEvidence, p.put_evidence_id)
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == p.put_code))
        if drift == "digest":
            evidence.payload_digest = "f" * 64
        elif drift == "base_command":
            evidence.command_code = "FOREIGN-COMMAND"
        elif drift == "base_device":
            evidence.device_code = "FOREIGN-DEVICE"
        elif drift == "result_reference":
            command.result_evidence_id = p.scan_id
        else:
            (await current_pickup(p, db)).accept_evidence_id = p.ready.id
    result = await processor(p).process_batch()
    assert result == (1 if drift == "result_reference" else 0)
    assert await counts(p) == (None, None, 1, None)


async def test_published_result_replay_and_recomputed_same_decision_reuse_report(placed):
    p = placed
    assert await processor(p).process_batch() == 1
    first = await counts(p)
    assert await processor(p).process_batch() == 0
    async with p.sessions.begin() as db:
        (await db.get(InboundEvidence, p.put_evidence_id)).published_at = None
    assert await processor(p).process_batch() == 1
    assert await counts(p) == first


async def test_old_result_identity_drift_cannot_overwrite_confirmed_position_or_frozen_report(placed):
    p = placed
    await processor(p).process_batch()
    first = await counts(p)
    async with p.sessions.begin() as db:
        (await db.get(InboundEvidence, p.put_evidence_id)).published_at = None
    await change_fixture(p, pkg_id="DIFFERENT-AUTHORITY")
    # 内核已处理该 Evidence 并识别 Decision 摘要冲突，计数不代表业务发布成功。
    assert await processor(p).process_batch() == 1
    current = await counts(p)
    assert current[:3] == first[:3] and current[3] is None
    async with p.sessions() as db:
        report = await db.get(WmsConfirmation, first[1])
        assert report.request_payload["data"]["PkgID"] == PKG
        assert (await db.get(InboundEvidence, p.put_evidence_id)).apply_status == "RECONCILING"
        assert (await db.get(MaterialExecution, p.material_id)).status == "RECONCILING"


async def test_late_physical_result_is_reported_after_member_cancel_and_parent_terminal(placed):
    from src.app.wms_integration.outbound_picking.models import PickingTask, PickingTaskBinSourceRack

    p = placed
    async with p.sessions.begin() as db:
        (await db.get(PickingTaskBinSourceRack, p.feed.source_member_id)).cancelled_evidence_id = p.ready.id
        (await db.get(PickingTask, p.task.id)).status = "EXECUTION_COMPLETED"
    assert await processor(p).process_batch() == 1
    assert (await counts(p))[0] == p.put_evidence_id


async def test_m2_authorization_progresses_independently_of_m1_report(placed):
    p = placed
    async with p.sessions.begin() as db:
        assert await components(p)[2].advance_in_session(db, work_id=p.work.id) == 1
    assert await processor(p).process_batch() == 1
    async with p.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(MaterialExecution)) == 1
        assert (await db.get(MaterialExecution, p.material_id)).status != "CLOSED"
        assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 3


async def test_existing_7a_put_survives_upgrade_and_populated_downgrade_is_refused(placed, database):
    import subprocess

    from tests.support.postgresql_heavy import run_alembic

    p = placed
    url, _ = database
    run_alembic("downgrade", "4e30101440d2", database_url=url)
    run_alembic("upgrade", "head", database_url=url)
    assert await processor(p).process_batch() == 1
    with pytest.raises(subprocess.CalledProcessError):
        run_alembic("downgrade", "4e30101440d2", database_url=url)
    assert (await counts(p))[0] == p.put_evidence_id
