from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.support.sqlmodel_metadata import register_required_sqlmodel_metadata

register_required_sqlmodel_metadata()


class _CountingClient:
    def __init__(self) -> None:
        self.close_count = 0

    async def aclose(self) -> None:
        self.close_count += 1


class _CleanupError(RuntimeError):
    pass


class _ConstructionError(RuntimeError):
    pass


@pytest.mark.asyncio
async def test_explicit_endpoint_builds_one_closed_transport_runtime_without_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.transport import composition
    from src.app.wms_adapter import factory

    client = _CountingClient()
    client_factory = MagicMock(return_value=client)
    monkeypatch.setattr(factory, "build_wms_client", client_factory)

    runtime = await composition.build_transport_runtime(
        wms_base_url="http://factory-wms.example:8080",
        transport_submit_path="/api/WES/TransportRequests",
        session_factory=MagicMock(),
    )

    client_factory.assert_called_once_with(base_url="http://factory-wms.example:8080", timeout_seconds=10.0)
    assert runtime.client is client
    assert runtime.adapter._client is client
    assert runtime.adapter._submit_path == "/api/WES/TransportRequests"
    assert runtime.port is runtime.service
    assert runtime.service._position_projections is runtime.position_projection_service
    assert runtime.service.provider is runtime.adapter
    assert runtime.handler._recorder is runtime.service
    assert not hasattr(runtime.service, "_outcome_publisher")

    await runtime.aclose()
    await runtime.aclose()
    assert client.close_count == 1


def test_runtime_builder_has_no_preconstructed_client_injection_seam() -> None:
    from src.app.transport.composition import build_transport_runtime

    assert tuple(inspect.signature(build_transport_runtime).parameters) == (
        "wms_base_url",
        "transport_submit_path",
        "session_factory",
    )


@pytest.mark.asyncio
async def test_partial_runtime_construction_closes_the_created_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.transport import composition
    from src.app.wms_adapter import factory

    client = _CountingClient()
    monkeypatch.setattr(factory, "build_wms_client", MagicMock(return_value=client))
    monkeypatch.setattr(
        composition,
        "TransportService",
        MagicMock(side_effect=RuntimeError("service construction failed")),
    )

    with pytest.raises(RuntimeError, match="service construction failed"):
        await composition.build_transport_runtime(
            wms_base_url="http://factory-wms.example:8080",
            transport_submit_path="/api/WES/TransportRequests",
            session_factory=MagicMock(),
        )

    assert client.close_count == 1


@pytest.mark.asyncio
async def test_partial_runtime_construction_preserves_primary_error_when_client_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.transport import composition
    from src.app.wms_adapter import factory

    client = _CountingClient()

    async def failing_close() -> None:
        client.close_count += 1
        raise _CleanupError("client cleanup failed")

    client.aclose = failing_close
    monkeypatch.setattr(factory, "build_wms_client", MagicMock(return_value=client))
    monkeypatch.setattr(
        composition,
        "TransportService",
        MagicMock(side_effect=_ConstructionError("service construction failed")),
    )
    cleanup_warning = MagicMock()
    monkeypatch.setattr(composition.logger, "warning", cleanup_warning)

    with pytest.raises(_ConstructionError, match="service construction failed"):
        await composition.build_transport_runtime(
            wms_base_url="http://factory-wms.example:8080",
            transport_submit_path="/api/WES/TransportRequests",
            session_factory=MagicMock(),
        )

    assert client.close_count == 1
    cleanup_warning.assert_called_once()
    assert "client 清理未完成" in cleanup_warning.call_args.args[0]


@pytest.mark.asyncio
async def test_outcome_publisher_is_supplied_only_at_the_publish_call() -> None:
    from src.app.transport.service import TransportService

    class _Repository:
        async def claim_pending_outcomes(self, _db: object, **_kwargs: object) -> list[object]:
            return []

    @asynccontextmanager
    async def begin():
        yield object()

    sessions = SimpleNamespace(begin=begin)
    publisher = SimpleNamespace(publish=AsyncMock())
    service = TransportService(sessions, _Repository(), SimpleNamespace())  # type: ignore[arg-type]

    assert await service.publish_pending_outcomes(1, publisher) == 0
    publisher.publish.assert_not_awaited()
