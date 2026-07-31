"""粗分机 Q19 首次决策的 PostgreSQL 行锁与零副作用证据。"""

from __future__ import annotations

import asyncio
from copy import deepcopy

from sqlalchemy import func
from sqlmodel import select

from src.app.device.models.command import DeviceCommand
from src.app.runtime.capabilities.material_flow.rough_sorter_q19_admission_service import (
    RoughSorterQ19AdmissionService,
)
from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.models.timeline import WorklineTimeline
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxService
from src.app.sys.models import SystemOutbox
from src.app.wms_integration.operation_registry import QUERY_OPERATIONS
from src.app.wms_integration.ports.query_outcome import QueryContractFailure, QuerySuccess
from src.app.wms_integration.query_projection import project_wms_query_request
from tests.contracts.wms_integration.provider_profile_support import build_compiled_provider_profile
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES, RESULT_FIXTURES
from tests.support.runtime_inbox_processing_postgresql import (
    claim,
    processor,
    seed_scan_flow,
    with_temporary_runtime_database,
)
from tests.support.wms_query_runtime import bind_stub_wms_query_runtime


class _BlockingQ19Runtime:
    def __init__(self) -> None:
        self.operation = QUERY_OPERATIONS[-1]
        self.endpoint = build_compiled_provider_profile().operations[self.operation.identity]
        self.execute_count = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def project(self, request):  # type: ignore[no-untyped-def]
        return project_wms_query_request(operation=self.operation, endpoint=self.endpoint, request=request)

    async def execute(self, _request):  # type: ignore[no-untyped-def]
        self.execute_count += 1
        self.entered.set()
        await self.release.wait()
        result = self.operation.result_model.model_validate(RESULT_FIXTURES[self.operation.identity])
        return QuerySuccess(result, evidence_key="query:q19:pg-first")


def _request(session_id: int):
    operation = QUERY_OPERATIONS[-1]
    return operation.request_model.model_validate(
        {
            **REQUEST_FIXTURES[operation.identity],
            "session_id": session_id,
        }
    )


def test_same_session_concurrent_q19_resolve_queries_once_and_replays_or_rejects_hash_drift() -> None:
    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as seed_db:
            seeded = await seed_scan_flow(seed_db, persist_q19_admit=False)

        runtime = _BlockingQ19Runtime()
        service = RoughSorterQ19AdmissionService(runtime)
        canonical_request = _request(seeded.session_id)
        conflicting_request = canonical_request.model_copy(update={"station_code": "ROUGH-SORTER-CONFLICT"})

        async def resolve(request):  # type: ignore[no-untyped-def]
            async with session_factory() as db:
                outcome = await service.resolve(db, session_id=seeded.session_id, request=request)
                await db.commit()
                return outcome

        first_task = asyncio.create_task(resolve(canonical_request))
        await asyncio.wait_for(runtime.entered.wait(), timeout=5)
        replay_task = asyncio.create_task(resolve(canonical_request))
        conflict_task = asyncio.create_task(resolve(conflicting_request))
        await asyncio.sleep(0.1)
        assert runtime.execute_count == 1
        runtime.release.set()
        first, replay, conflict = await asyncio.wait_for(
            asyncio.gather(first_task, replay_task, conflict_task),
            timeout=10,
        )

        assert isinstance(first, QuerySuccess)
        assert isinstance(replay, QuerySuccess)
        assert replay.value == first.value
        assert isinstance(conflict, QueryContractFailure)
        assert conflict.reason_code == "WMS_Q19_REPLAY_REQUEST_MISMATCH"
        assert runtime.execute_count == 1

        async with session_factory() as verify_db:
            assert await verify_db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 0
            assert await verify_db.scalar(select(func.count()).select_from(SystemOutbox)) == 0

    asyncio.run(with_temporary_runtime_database(scenario))


def test_crash_after_first_q19_then_payload_hash_drift_holds_without_new_effect(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        provider_calls = 0

        async def forbidden_query(*_args):  # type: ignore[no-untyped-def]
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("persisted Q19 hash mismatch must not call provider")

        bind_stub_wms_query_runtime(monkeypatch, forbidden_query)
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            session = await db.get(WorklineSession, seeded.session_id)
            inbox = await db.get(RuntimeInbox, seeded.inbox_id)
            assert session is not None and inbox is not None
            first_decision = deepcopy(session.context_json["wms_admission_decision"])
            drifted_payload = deepcopy(inbox.payload_json)
            drifted_payload["data"]["Qty"] = "11"
            inbox.payload_json = drifted_payload
            await db.commit()

            claimed = await claim(db, service, token="q19-hash-drift")
            result = await processor(service).process_claimed(db, claim=claimed)

            assert result == {"processed": 1, "success": 0, "failed": 1, "skipped": 0, "resource_wait": 0}
            assert provider_calls == 0
            assert await db.scalar(select(func.count()).select_from(DeviceCommand)) == 0
            assert await db.scalar(select(func.count()).select_from(RuntimeIntentLog)) == 0
            assert await db.scalar(select(func.count()).select_from(SystemOutbox)) == 0
            db.expire_all()
            session = await db.get(WorklineSession, seeded.session_id)
            assert session is not None
            assert session.context_json["wms_admission_decision"] == first_decision
            assert session.plugin_state_version == 0
            timeline = await db.scalar(
                select(WorklineTimeline).where(
                    WorklineTimeline.related_inbox_id == seeded.inbox_id,
                    WorklineTimeline.payload_json["record_type"].as_string() == "PLUGIN_DECISION",
                )
            )
            assert timeline is not None
            assert timeline.payload_json["decision"]["outcome_code"] == "HOLD"
            assert timeline.payload_json["decision"]["hold_reason"] == "WMS_Q19_REPLAY_REQUEST_MISMATCH"

    asyncio.run(with_temporary_runtime_database(scenario))


def test_crash_after_first_q19_then_same_payload_replays_decision_and_same_pick_without_http(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        provider_calls = 0

        async def forbidden_query(*_args):  # type: ignore[no-untyped-def]
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("matching persisted Q19 must replay without provider")

        bind_stub_wms_query_runtime(monkeypatch, forbidden_query)
        service = RuntimeInboxService()
        async with session_factory() as db:
            await seed_scan_flow(db)
            claimed = await claim(db, service, token="q19-same-payload-replay")
            result = await processor(service).process_claimed(db, claim=claimed)

            assert result["success"] == 1
            assert provider_calls == 0
            commands = list((await db.execute(select(DeviceCommand).order_by(DeviceCommand.id))).scalars())
            assert len(commands) == 1
            assert commands[0].task_type == "PICK_AND_PUT"
            assert await db.scalar(select(func.count()).select_from(SystemOutbox)) == 1

    asyncio.run(with_temporary_runtime_database(scenario))
