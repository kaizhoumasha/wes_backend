"""WMS QUERY source_version compare-and-record 的 PostgreSQL 原子性证据。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import select

from src.app.wms_integration.models import WmsCallEvidence, WmsEvidenceStatus
from src.app.wms_integration.ports.query_outcome import QueryContractFailure, QuerySuccess
from src.app.wms_integration.query_evidence import (
    WmsQueryCallPermit,
    WmsRegistryCallEvidenceWriter,
)
from src.app.wms_integration.services.evidence_service import WmsCallEvidenceService
from tests.support.runtime_inbox_postgresql import run_alembic, temporary_database


class _NoopBreaker:
    async def record_success(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("permit.allowed=False 时不得写 breaker")

    async def record_failure(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("permit.allowed=False 时不得写 breaker")


def test_concurrent_same_source_version_with_different_payload_has_exactly_one_success() -> None:
    async def scenario() -> None:
        async with temporary_database() as (_database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            writer = WmsRegistryCallEvidenceWriter(
                session_factory=session_factory,
                evidence_service=WmsCallEvidenceService(),
                breaker_service=_NoopBreaker(),
            )
            ready = asyncio.Barrier(2)

            async def record(response_hash: str):  # type: ignore[no-untyped-def]
                await ready.wait()
                return await writer.record(
                    operation_identity="wms.inventory.query_inventory@v1",
                    target_code="WMS_INVENTORY_QUERY_INVENTORY",
                    profile_identity="wms.2026-07-28.full-factory.sandbox",
                    profile_digest="a" * 64,
                    endpoint_digest="b" * 64,
                    request_snapshot={"material_hash": "c" * 64},
                    request_canonical_hash="d" * 64,
                    response_hash=response_hash,
                    attempt_count=1,
                    http_status=200,
                    outcome=QuerySuccess(SimpleNamespace(source_version="2")),
                    permit=WmsQueryCallPermit(allowed=False),
                )

            first, second = await asyncio.gather(record("1" * 64), record("2" * 64))
            outcomes = (first.outcome, second.outcome)
            assert sum(isinstance(outcome, QuerySuccess) for outcome in outcomes) == 1
            conflicts = [outcome for outcome in outcomes if isinstance(outcome, QueryContractFailure)]
            assert len(conflicts) == 1
            assert conflicts[0].reason_code == "WMS_SOURCE_VERSION_PAYLOAD_CONFLICT"

            async with session_factory() as db:
                rows = list((await db.execute(select(WmsCallEvidence))).scalars())
                assert len(rows) == 2
                assert sum(row.status == WmsEvidenceStatus.SUCCEEDED for row in rows) == 1
                assert sum(row.status == WmsEvidenceStatus.FAILED for row in rows) == 1
            await engine.dispose()

    asyncio.run(scenario())
