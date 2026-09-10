"""独占 PostgreSQL 证明身份隔离、过滤和纯读取。"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from src.app.execution.models.inbound_evidence import InboundEvidence, InboundEvidenceKind
from src.app.execution.models.wms_confirmation import WmsConfirmation
from src.app.execution.repositories.execution_observation_repository import ExecutionObservationRepository
from src.app.execution.services.execution_observation_service import ExecutionObservationService
from src.app.wms_diagnostics.v1.execution import get_observation_service, router
from src.app.workline.models.workline import LineType, WorkLine
from src.core.uuid7 import new_uuid7
from src.register import create_app


@pytest.mark.asyncio
async def test_persisted_queries_use_operation_identity_and_wms_kind(integration_session_factory):
    repository = ExecutionObservationRepository()
    identity = new_uuid7()
    now = datetime(2026, 9, 9)
    async with integration_session_factory() as db:
        try:
            line = WorkLine(line_code=f"observation-{identity[:18]}", line_name="基础查询", line_type=LineType.AUTO)
            db.add(line)
            await db.flush()
            confirmation = WmsConfirmation(
                operation="sample.first@v1",
                operation_id=identity,
                workline_id=line.id,
                request_digest="a" * 64,
                request_payload={},
                deadline_at=now + timedelta(minutes=5),
            )
            db.add(confirmation)
            for operation, kind in [
                ("sample.first@v1", InboundEvidenceKind.WMS_EVENT),
                ("sample.second@v1", InboundEvidenceKind.WMS_RESULT),
                ("sample.device@v1", InboundEvidenceKind.DEVICE_EVENT),
            ]:
                db.add(
                    InboundEvidence(
                        kind=kind,
                        source_identity=f"{operation}:{identity}",
                        payload_digest="b" * 64,
                        normalized_payload={},
                        received_at=now,
                        operation=operation,
                        operation_id=identity,
                        device_code="observation-device" if kind == InboundEvidenceKind.DEVICE_EVENT else None,
                    )
                )
            await db.flush()
            assert await repository.get_confirmation(db, "sample.first@v1", identity) is confirmation
            assert await repository.get_confirmation(db, "sample.second@v1", identity) is None
            assert await repository.get_confirmation(db, "sample.first@v1", new_uuid7()) is None
            for operation in ("sample.first@v1", "sample.second@v1"):
                evidence = await repository.get_evidence(db, operation, identity)
                assert evidence is not None and evidence.operation == operation
                assert evidence.processed_at is None and evidence.published_at is None
            assert await repository.get_evidence(db, "sample.device@v1", identity) is None
            assert await repository.get_evidence(db, "sample.first@v1", new_uuid7()) is None

            @asynccontextmanager
            async def query_session():
                yield db

            # 使用真实生产路由装配、Service 和 PostgreSQL；仅替换授权及独占会话入口。
            app = create_app()
            app.dependency_overrides[get_observation_service] = lambda: ExecutionObservationService(
                query_session, repository
            )
            for route in router.routes:
                for dependency in route.dependencies:
                    app.dependency_overrides[dependency.dependency] = lambda: None
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                query = {"operation": "sample.first@v1", "operation_id": identity}
                response = await client.get("/api/v1/wms-diagnostics/confirmations", params=query)
                assert response.status_code == 200
                assert response.json()["data"]["operation_id"] == identity
                assert response.json()["data"]["updated_at"] is None
                response = await client.get("/api/v1/wms-diagnostics/evidences", params=query)
                assert response.status_code == 200
                assert response.json()["data"]["apply_status"] == "PENDING"
                assert response.json()["data"]["processed_at"] is None
                response = await client.get(
                    "/api/v1/wms-diagnostics/evidences", params=query | {"operation": "sample.device@v1"}
                )
                assert response.status_code == 404
            assert not db.dirty and not db.deleted
        finally:
            await db.rollback()
