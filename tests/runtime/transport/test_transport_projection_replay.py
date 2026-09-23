from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.execution.services import PositionProjectionRetryableError
from src.app.transport.repository import TransportRepository

CANDIDATE_TIME = datetime(2026, 9, 22)


class _Rows:
    def all(self):
        return []


class _Db:
    def __init__(self):
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _Rows()


class _Tx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class _Sessions:
    def __init__(self, db):
        self.db = db

    def begin(self):
        return _Tx(self.db)


class _ReplayRepository:
    async def list_projection_recovery_orphans(self, _db, *, limit):
        assert limit == 100
        return []

    async def list_ack_invalidation_projection_candidates(self, _db, *, limit):
        assert limit == 100
        return [("task-1", "BIN", "bin-1", "submit-op-1", CANDIDATE_TIME)]

    async def get_task(self, _db, task_id, *, for_update=False):
        assert task_id == "task-1"
        return SimpleNamespace(
            transport_task_id="task-1",
            client_request_id="request-1",
            authority_workline_id=7,
            submit_operation_id="submit-op-1",
            status="ACCEPTED",
            updated_at=SimpleNamespace(),
        )

    async def list_members(self, _db, _task_id):
        return [SimpleNamespace(object_type="BIN", object_id="bin-1", status="SUCCEEDED", last_operation_id="op-1")]

    async def lock_position_result(self, _db, _object_type, _object_id):
        return None


class _DrainReplayRepository(_ReplayRepository):
    async def list_final_result_projection_candidates(self, _db, *, limit):
        assert limit == 100
        return [("task-1", "RACK", "rack-1", "result-op-1", CANDIDATE_TIME)]

    async def get_task(self, _db, task_id, *, for_update=False):
        task = await super().get_task(_db, task_id, for_update=for_update)
        task.status = "SUCCEEDED"
        task.caller_json = {"workline_id": "7"}
        return task

    async def list_members(self, _db, _task_id):
        return [
            SimpleNamespace(
                object_type="RACK",
                object_id="rack-1",
                status="SUCCEEDED",
                last_operation_id="result-op-1",
                final_position_json={"kind": "RACK_POSITION", "location_code": "FIVE_RACK"},
                position_unknown=False,
                arrival_face="A",
                updated_at=SimpleNamespace(),
            )
        ]


class _DrainAckReplayRepository(_DrainReplayRepository):
    async def list_ack_invalidation_projection_candidates(self, _db, *, limit):
        assert limit == 100
        return [("task-1", "RACK", "rack-1", "submit-op-1", CANDIDATE_TIME)]

    async def get_task(self, _db, task_id, *, for_update=False):
        return await _ReplayRepository.get_task(self, _db, task_id, for_update=for_update)


class _BatchReplayRepository(_ReplayRepository):
    async def list_ack_invalidation_projection_candidates(self, _db, *, limit):
        assert limit == 100
        return [
            ("task-retry", "BIN", "bin-retry", "submit-retry", CANDIDATE_TIME),
            ("task-ok", "BIN", "bin-ok", "submit-ok", CANDIDATE_TIME),
        ]

    async def get_task(self, _db, task_id, *, for_update=False):
        suffix = task_id.removeprefix("task-")
        return SimpleNamespace(
            transport_task_id=task_id,
            client_request_id=f"request-{suffix}",
            authority_workline_id=7,
            submit_operation_id=f"submit-{suffix}",
            status="ACCEPTED",
            updated_at=SimpleNamespace(),
        )

    async def list_members(self, _db, task_id):
        suffix = task_id.removeprefix("task-")
        return [SimpleNamespace(object_type="BIN", object_id=f"bin-{suffix}")]


class _FinalBatchReplayRepository(_ReplayRepository):
    async def list_final_result_projection_candidates(self, _db, *, limit):
        assert limit == 100
        return [
            ("task-retry", "BIN", "bin-retry", "op-retry", CANDIDATE_TIME),
            ("task-ok", "BIN", "bin-ok", "op-ok", CANDIDATE_TIME),
        ]

    async def get_task(self, _db, task_id, *, for_update=False):
        suffix = task_id.removeprefix("task-")
        return SimpleNamespace(
            transport_task_id=task_id,
            client_request_id=f"request-{suffix}",
            authority_workline_id=7,
            submit_operation_id=f"submit-{suffix}",
            status="SUCCEEDED",
            updated_at=SimpleNamespace(),
        )

    async def list_members(self, _db, task_id):
        suffix = task_id.removeprefix("task-")
        return [
            SimpleNamespace(
                object_type="BIN",
                object_id=f"bin-{suffix}",
                status="SUCCEEDED",
                last_operation_id=f"op-{suffix}",
                final_position_json={"slot": suffix},
                position_unknown=False,
                arrival_face=None,
                updated_at=SimpleNamespace(),
            )
        ]


class _DbForReplay:
    def __init__(self, picking_status="EXECUTING"):
        self.picking_status = picking_status

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(id=7))

    async def scalar(self, _statement):
        return SimpleNamespace(workline_id=7, picking_task_id=11)

    async def get(self, _model, _id):
        return SimpleNamespace(status=self.picking_status)


class _DbForDrainReplay(_DbForReplay):
    async def scalar(self, _statement):
        return SimpleNamespace(id=17, workline_id=7, picking_task_id=None)


@pytest.mark.asyncio
async def test_final_projection_candidate_scan_uses_causal_identity_without_business_status():
    db = _Db()
    assert await TransportRepository().list_final_result_projection_candidates(db, limit=100) == []
    sql = str(db.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "picking_tasks.status" not in sql
    assert "IS DISTINCT FROM" in sql
    assert "transport_decision_bindings" in sql
    assert "picking_tasks" not in sql
    assert "source_causal_token" in sql
    assert "ORDER BY wes_runtime.transport_members.updated_at ASC" in sql


@pytest.mark.asyncio
async def test_ack_invalidation_candidate_scan_is_separate_and_ack_fenced():
    db = _Db()
    assert await TransportRepository().list_ack_invalidation_projection_candidates(db, limit=100) == []
    sql = str(db.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "status = 'ACCEPTED'" in sql
    assert "ACK_INVALIDATION" in sql
    assert "transport_decision_bindings" in sql
    assert "picking_tasks.status" not in sql
    assert "picking_tasks" not in sql
    assert "source_causal_token" in sql


def test_replay_task_has_fixed_batch_contract():
    from src.celery_app.tasks.transport import replay_transport_projections_batch

    assert replay_transport_projections_batch.name.endswith("replay_transport_projections_batch")


@pytest.mark.asyncio
@pytest.mark.parametrize("picking_status", ["EXECUTION_COMPLETED", "CANCELLED"])
async def test_picking_parent_status_does_not_discard_authoritative_final_position(picking_status):
    from src.app.transport.service import TransportService

    position_port = SimpleNamespace(apply_transport_result=AsyncMock(return_value=None))
    service = TransportService(
        _Sessions(_DbForReplay(picking_status)),
        _FinalBatchReplayRepository(),
        object(),
        result_timeout=timedelta(seconds=1),
        position_projections=position_port,
    )

    assert await service.replay_final_result_projections(100) == 2
    assert position_port.apply_transport_result.await_count == 2


@pytest.mark.asyncio
async def test_ack_replay_delegates_to_invalidation_owner_without_provider_submit():
    from src.app.transport.service import TransportService

    position_port = SimpleNamespace(invalidate_transport_member=AsyncMock(return_value=None))
    service = TransportService(
        _Sessions(_DbForReplay()),
        _ReplayRepository(),
        object(),
        result_timeout=timedelta(seconds=1),
        position_projections=position_port,
    )

    assert await service.replay_ack_invalidations(100) == 1
    position_port.invalidate_transport_member.assert_awaited_once()


@pytest.mark.asyncio
async def test_ack_replay_rolls_back_retryable_candidate_and_continues_batch(caplog):
    from src.app.transport.service import TransportService

    position_port = SimpleNamespace(
        invalidate_transport_member=AsyncMock(
            side_effect=(PositionProjectionRetryableError("serialization retry"), None)
        )
    )
    service = TransportService(
        _Sessions(_DbForReplay()),
        _BatchReplayRepository(),
        object(),
        result_timeout=timedelta(seconds=1),
        position_projections=position_port,
    )

    caplog.set_level("INFO", logger="src.app.transport.service")
    assert await service.replay_ack_invalidations(100) == 1
    assert position_port.invalidate_transport_member.await_count == 2
    retry_logs = [record for record in caplog.records if "transport.projection_replay.retryable" in record.message]
    assert len(retry_logs) == 1
    assert "branch=ack_invalidation" in retry_logs[0].message
    batch_logs = [record for record in caplog.records if "transport.projection_replay.batch" in record.message]
    assert len(batch_logs) == 1
    assert "candidates=2" in batch_logs[0].message
    assert "replayed=1" in batch_logs[0].message


@pytest.mark.asyncio
async def test_final_replay_continues_after_retryable_candidate(caplog):
    from src.app.transport.service import TransportService

    position_port = SimpleNamespace(
        apply_transport_result=AsyncMock(side_effect=(PositionProjectionRetryableError("serialization retry"), None))
    )
    service = TransportService(
        _Sessions(_DbForReplay()),
        _FinalBatchReplayRepository(),
        object(),
        result_timeout=timedelta(seconds=1),
        position_projections=position_port,
    )

    caplog.set_level("INFO", logger="src.app.transport.service")
    assert await service.replay_final_result_projections(100) == 1
    assert position_port.apply_transport_result.await_count == 2
    batch_logs = [record for record in caplog.records if record.message.startswith("transport.projection_replay.batch")]
    assert len(batch_logs) == 1
    assert "branch=final_result" in batch_logs[0].message
    assert "candidates=2" in batch_logs[0].message
    assert "replayed=1" in batch_logs[0].message


@pytest.mark.asyncio
async def test_final_replay_passes_taskless_drain_fact_to_causal_projection():
    from src.app.transport.service import TransportService

    position_port = SimpleNamespace(apply_transport_result=AsyncMock(return_value=None))
    service = TransportService(
        _Sessions(_DbForDrainReplay()),
        _DrainReplayRepository(),
        object(),
        result_timeout=timedelta(seconds=1),
        position_projections=position_port,
    )

    assert await service.replay_final_result_projections(100) == 1
    position_port.apply_transport_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_result_passes_historical_taskless_drain_fact_to_causal_projection():
    from src.app.transport.service import TransportService

    position_port = SimpleNamespace(apply_transport_result=AsyncMock(return_value=None))
    repository = _DrainReplayRepository()
    service = TransportService(
        _Sessions(_DbForDrainReplay()),
        repository,
        object(),
        result_timeout=timedelta(seconds=1),
        position_projections=position_port,
    )
    task = await repository.get_task(_DbForDrainReplay(), "task-1", for_update=True)
    member = (await repository.list_members(_DbForDrainReplay(), "task-1"))[0]
    evidence = SimpleNamespace(operation_id="result-op-1")

    await service._apply_member_position_projection(
        _DbForDrainReplay(),
        task,
        member,
        evidence,
        position_json=member.final_position_json,
        position_unknown=False,
        arrival_face="A",
        updated_at=member.updated_at,
    )

    position_port.apply_transport_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_result_records_superseded_projection(monkeypatch):
    import src.app.transport.service as service_module
    from src.app.transport.service import TransportService

    superseded = AsyncMock()
    monkeypatch.setattr(service_module, "record_superseded", superseded)
    position_port = SimpleNamespace(
        apply_transport_result=AsyncMock(return_value=SimpleNamespace(source_transport_task_id="task-newer"))
    )
    repository = _DrainReplayRepository()
    service = TransportService(
        _Sessions(_DbForDrainReplay()),
        repository,
        object(),
        result_timeout=timedelta(seconds=1),
        position_projections=position_port,
    )
    task = await repository.get_task(_DbForDrainReplay(), "task-1", for_update=True)
    member = (await repository.list_members(_DbForDrainReplay(), "task-1"))[0]

    await service._apply_member_position_projection(
        _DbForDrainReplay(),
        task,
        member,
        SimpleNamespace(operation_id="result-op-1"),
        position_json=member.final_position_json,
        position_unknown=False,
        arrival_face="A",
        updated_at=member.updated_at,
    )

    superseded.assert_awaited_once()


@pytest.mark.asyncio
async def test_projection_recovery_metrics_snapshot_has_stable_low_cardinality_shape():
    from src.app.transport.projection_metrics import snapshot

    assert set(await snapshot()) == {
        "candidate_count",
        "replayed_count",
        "superseded_total",
        "retryable_total",
        "orphan_sample_count",
        "oldest_candidate_age",
    }


@pytest.mark.asyncio
async def test_projection_recovery_metrics_round_trip_through_redis(monkeypatch):
    from src.app.transport import projection_metrics

    class FakeRedis:
        def __init__(self):
            self.values = {}

        async def hset(self, _key, *, mapping):
            self.values.update({name: str(value) for name, value in mapping.items()})

        async def hincrby(self, _key, name, amount):
            self.values[name] = str(int(self.values.get(name, "0")) + amount)

        async def hgetall(self, _key):
            return dict(self.values)

    redis = FakeRedis()
    monkeypatch.setattr(projection_metrics, "get_redis", lambda: redis)

    await projection_metrics.record_batch(candidate_count=4, replayed_count=2, oldest_candidate_age=12.5)
    await projection_metrics.record_retryable()
    await projection_metrics.record_superseded()
    await projection_metrics.record_orphan_sample(3)

    assert await projection_metrics.snapshot() == {
        "candidate_count": 4,
        "replayed_count": 2,
        "superseded_total": 1,
        "retryable_total": 1,
        "orphan_sample_count": 3,
        "oldest_candidate_age": 12.5,
    }


@pytest.mark.asyncio
async def test_ack_replay_passes_taskless_drain_uncertainty_to_causal_projection():
    from src.app.transport.service import TransportService

    position_port = SimpleNamespace(invalidate_transport_member=AsyncMock(return_value=None))
    service = TransportService(
        _Sessions(_DbForDrainReplay()),
        _DrainAckReplayRepository(),
        object(),
        result_timeout=timedelta(seconds=1),
        position_projections=position_port,
    )

    assert await service.replay_ack_invalidations(100) == 1
    position_port.invalidate_transport_member.assert_awaited_once()


@pytest.mark.asyncio
async def test_ack_replay_does_not_swallow_unknown_candidate_failure():
    from src.app.transport.service import TransportService

    position_port = SimpleNamespace(invalidate_transport_member=AsyncMock(side_effect=RuntimeError("bug")))
    service = TransportService(
        _Sessions(_DbForReplay()),
        _ReplayRepository(),
        object(),
        result_timeout=timedelta(seconds=1),
        position_projections=position_port,
    )

    with pytest.raises(RuntimeError, match="bug"):
        await service.replay_ack_invalidations(100)


def test_task_diagnostics_contract_is_structured_and_low_cardinality(monkeypatch, caplog):
    from src.celery_app import task_diagnostics

    request = SimpleNamespace(
        delivery_info={"routing_key": "wms-fulfillment"},
        hostname="worker-1",
    )
    monkeypatch.setattr(task_diagnostics, "current_task", SimpleNamespace(request=request))
    monkeypatch.setenv("TRANSPORT_BROKER_KEY_PREFIX", "it:transport:run-42:")
    caplog.set_level("INFO", logger="src.celery_app.task_diagnostics")

    context = task_diagnostics.task_started("transport.replay", limit=10)
    task_diagnostics.task_finished(context, processed=2)

    events = {record.event: record for record in caplog.records}
    assert {"task.body.start", "task.body.done"} <= events.keys()
    assert context == {
        "task_name": "transport.replay",
        "queue": "wms-fulfillment",
        "worker_hostname": "worker-1",
        "run_id": "run-42",
        "key_prefix": "it:transport:run-42:",
    }
    assert events["task.body.start"].limit == 10
    assert events["task.body.done"].processed == 2
