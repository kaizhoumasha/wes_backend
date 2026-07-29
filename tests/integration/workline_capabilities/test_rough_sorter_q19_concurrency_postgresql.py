"""粗分机 Q19 首次决策的 PostgreSQL 行锁与零副作用证据。"""

from __future__ import annotations

import asyncio

from sqlalchemy import func
from sqlmodel import select

from src.app.runtime.capabilities.material_flow.rough_sorter_q19_admission_service import (
    RoughSorterQ19AdmissionService,
)
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog
from src.app.sys.models import SystemOutbox
from src.app.wms_integration.operation_registry import QUERY_OPERATIONS
from src.app.wms_integration.ports.query_outcome import QueryContractFailure, QuerySuccess
from src.app.wms_integration.query_projection import project_wms_query_request
from tests.contracts.wms_integration.provider_profile_support import build_compiled_provider_profile
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES, RESULT_FIXTURES
from tests.support.runtime_inbox_processing_postgresql import (
    seed_scan_flow,
    with_temporary_runtime_database,
)


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
