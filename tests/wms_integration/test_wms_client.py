import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.wms_integration.models import (
    ConfirmInboundRequest,
    ConfirmInboundResponse,
    ConfirmOutboundRequest,
    ConfirmOutboundResponse,
    ReleaseReservationRequest,
    ReleaseReservationResponse,
    ReserveInventoryRequest,
    ReserveInventoryResponse,
    WmsCallEvidence,
    WmsCircuitBreakerState,
    WmsCircuitBreakerStatus,
    WmsOperationName,
)
from src.app.wms_integration.services import (
    WmsBusinessRejectedError,
    WmsCircuitBreakerService,
    WmsCircuitOpenError,
    WmsEndpointConfig,
    WmsEvidencePersistenceError,
    WmsHttpClient,
    WmsHttpTimeoutConfig,
    WmsTimeoutError,
    WmsTypedPortService,
    WmsUnavailableError,
)
from src.utils.timezone import timezone


def _session_factory(db_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


def _endpoint_config() -> WmsEndpointConfig:
    return WmsEndpointConfig(
        base_url="http://wms.test/api",
        timeout=WmsHttpTimeoutConfig(connect=1.1, read=2.2, write=3.3, pool=4.4),
    )


def _evidence_key(operation_name: WmsOperationName, request_id: str) -> str:
    return f"ev:{operation_name}:{request_id}"


def _service(
    db_engine,
    transport: httpx.AsyncBaseTransport,
    *,
    breaker_service: WmsCircuitBreakerService | None = None,
    evidence_service: Any | None = None,
    observability_emit: Any | None = None,
    use_default_evidence_key: bool = False,
) -> WmsTypedPortService:
    kwargs: dict[str, Any] = {
        "session_factory": _session_factory(db_engine),
        "endpoint_config": _endpoint_config(),
        "http_client": WmsHttpClient(transport=transport),
        "breaker_service": breaker_service or WmsCircuitBreakerService(failure_threshold=2, retry_after_seconds=60),
    }
    if evidence_service is not None:
        kwargs["evidence_service"] = evidence_service
    if observability_emit is not None:
        kwargs["observability_emit"] = observability_emit
    if not use_default_evidence_key:
        kwargs["evidence_key_factory"] = _evidence_key
    return WmsTypedPortService(**kwargs)


async def _get_evidence(db_engine, evidence_key: str) -> WmsCallEvidence | None:
    async with _session_factory(db_engine)() as db:
        result = await db.execute(select(WmsCallEvidence).where(WmsCallEvidence.evidence_key == evidence_key))
        return result.scalar_one_or_none()


async def _get_breaker_state(
    db_engine,
    *,
    target_code: str,
    operation_name: str,
) -> WmsCircuitBreakerState | None:
    async with _session_factory(db_engine)() as db:
        result = await db.execute(
            select(WmsCircuitBreakerState).where(
                WmsCircuitBreakerState.target_code == target_code,
                WmsCircuitBreakerState.operation_name == operation_name,
            )
        )
        return result.scalar_one_or_none()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "method_name",
        "port_request",
        "response_payload",
        "response_type",
        "expected_path",
    ),
    [
        (
            "reserve_inventory",
            ReserveInventoryRequest(request_id="REQ-RESERVE", reservation_key="RSV-1", sku="SKU-1", qty="1"),
            {"request_id": "REQ-RESERVE", "reservation_key": "RSV-1", "accepted": True},
            ReserveInventoryResponse,
            "/api/inventory/reserve",
        ),
        (
            "release_reservation",
            ReleaseReservationRequest(request_id="REQ-RELEASE", reservation_key="RSV-1"),
            {"request_id": "REQ-RELEASE", "reservation_key": "RSV-1", "released": True},
            ReleaseReservationResponse,
            "/api/inventory/reserve/RSV-1",
        ),
        (
            "confirm_inbound",
            ConfirmInboundRequest(request_id="REQ-IN", inbound_key="IN-1", sku="SKU-1", qty="1"),
            {"request_id": "REQ-IN", "inbound_key": "IN-1", "confirmed": True},
            ConfirmInboundResponse,
            "/api/inbound/confirm",
        ),
        (
            "confirm_outbound",
            ConfirmOutboundRequest(request_id="REQ-OUT", outbound_key="OUT-1", sku="SKU-1", qty="1"),
            {"request_id": "REQ-OUT", "outbound_key": "OUT-1", "confirmed": True},
            ConfirmOutboundResponse,
            "/api/outbound/confirm",
        ),
    ],
)
async def test_wms_typed_ports_cover_all_effect_operations(
    db_engine,
    method_name: str,
    port_request,
    response_payload: dict[str, Any],
    response_type: type,
    expected_path: str,
) -> None:
    captured_requests: list[dict[str, Any]] = []

    async def handler(http_request: httpx.Request) -> httpx.Response:
        captured_requests.append(
            {
                "method": http_request.method,
                "path": http_request.url.path,
                "content": http_request.content,
                "query": dict(http_request.url.params),
            }
        )
        return httpx.Response(200, json={"data": response_payload})

    service = _service(db_engine, httpx.MockTransport(handler))
    response = await getattr(service, method_name)(port_request)

    evidence = await _get_evidence(db_engine, f"ev:{method_name}:{port_request.request_id}")
    expected_method = {"release_reservation": "DELETE"}.get(method_name, "POST")
    assert isinstance(response, response_type)
    assert captured_requests[0]["method"] == expected_method
    assert captured_requests[0]["path"] == expected_path
    assert captured_requests[0]["query"] == {}
    if expected_method == "DELETE":
        assert captured_requests[0]["content"] == b""
    else:
        assert json.loads(captured_requests[0]["content"].decode())["request_id"] == port_request.request_id
    assert evidence is not None
    assert evidence.status == "SUCCEEDED"
    assert evidence.operation_name == method_name


@pytest.mark.asyncio
async def test_wms_error_4xx_raises_business_rejected_without_breaker_failure(
    db_engine,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"reason_code": "INSUFFICIENT_STOCK", "message": "库存不足"})

    service = _service(
        db_engine,
        httpx.MockTransport(handler),
        breaker_service=WmsCircuitBreakerService(),
    )

    with pytest.raises(WmsBusinessRejectedError) as exc_info:
        await service.reserve_inventory(
            ReserveInventoryRequest(request_id="REQ-409", reservation_key="RSV-409", sku="SKU-1", qty="10")
        )

    error = exc_info.value
    evidence = await _get_evidence(db_engine, "ev:reserve_inventory:REQ-409")
    breaker_state = await _get_breaker_state(
        db_engine,
        target_code="WMS_INVENTORY",
        operation_name="reserve_inventory",
    )

    assert error.operation_name == "reserve_inventory"
    assert error.evidence_key == "ev:reserve_inventory:REQ-409"
    assert error.http_status == 409
    assert error.reason_code == "INSUFFICIENT_STOCK"
    assert error.retryable is False
    assert evidence is not None
    assert evidence.status == "FAILED"
    assert evidence.retryable is False
    assert breaker_state is not None
    assert breaker_state.state == WmsCircuitBreakerStatus.CLOSED
    assert breaker_state.failure_count == 0


@pytest.mark.asyncio
async def test_wms_error_4xx_counts_as_breaker_success_in_half_open(db_engine) -> None:
    breaker_service = WmsCircuitBreakerService(failure_threshold=1, retry_after_seconds=60)
    session_factory = _session_factory(db_engine)
    async with session_factory() as db:
        opened = await breaker_service.record_failure(
            db,
            target_code="WMS_INVENTORY",
            operation_name="reserve_inventory",
            evidence_key="ev:seed-half-open",
        )
        opened.opened_until = timezone.now_for_db() - timedelta(seconds=1)
        await db.commit()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"reason_code": "SKU_LOCKED", "message": "物料锁定"})

    service = _service(db_engine, httpx.MockTransport(handler), breaker_service=breaker_service)

    with pytest.raises(WmsBusinessRejectedError):
        await service.reserve_inventory(
            ReserveInventoryRequest(
                request_id="REQ-HALF-409",
                reservation_key="RSV-HALF",
                sku="SKU-1",
                qty="1",
            )
        )

    breaker_state = await _get_breaker_state(
        db_engine,
        target_code="WMS_INVENTORY",
        operation_name="reserve_inventory",
    )
    evidence = await _get_evidence(db_engine, "ev:reserve_inventory:REQ-HALF-409")

    assert breaker_state is not None
    assert breaker_state.state == WmsCircuitBreakerStatus.CLOSED
    assert breaker_state.failure_count == 0
    assert breaker_state.last_evidence_key == "ev:reserve_inventory:REQ-HALF-409"
    assert evidence is not None
    assert evidence.status == "FAILED"
    assert evidence.retryable is False


@pytest.mark.asyncio
async def test_wms_error_5xx_records_unavailable_and_breaker_failure(db_engine) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"reason_code": "WMS_DOWN", "message": "WMS unavailable"})

    service = _service(
        db_engine,
        httpx.MockTransport(handler),
        breaker_service=WmsCircuitBreakerService(failure_threshold=1, retry_after_seconds=60),
    )

    with pytest.raises(WmsUnavailableError) as exc_info:
        await service.confirm_outbound(
            ConfirmOutboundRequest(request_id="REQ-503", outbound_key="OUT-503", sku="SKU-1", qty="1")
        )

    error = exc_info.value
    evidence = await _get_evidence(db_engine, "ev:confirm_outbound:REQ-503")
    breaker_state = await _get_breaker_state(
        db_engine,
        target_code="WMS_OUTBOUND",
        operation_name="confirm_outbound",
    )

    assert error.evidence_key == "ev:confirm_outbound:REQ-503"
    assert error.http_status == 503
    assert error.reason_code == "WMS_DOWN"
    assert error.retryable is True
    assert evidence is not None
    assert evidence.status == "FAILED"
    assert breaker_state is not None
    assert breaker_state.state == WmsCircuitBreakerStatus.OPEN


@pytest.mark.asyncio
async def test_wms_5xx_breaker_transition_observability_uses_request_trace(db_engine) -> None:
    from src.app.runtime.orchestration.observability import RuntimeObservabilityRegistry

    emitted = []
    registry = RuntimeObservabilityRegistry(observers=(emitted.append,))

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"reason_code": "WMS_DOWN", "message": "WMS unavailable"})

    service = _service(
        db_engine,
        httpx.MockTransport(handler),
        breaker_service=WmsCircuitBreakerService(
            failure_threshold=1,
            retry_after_seconds=60,
            observability_emit=registry.emit,
        ),
    )

    with pytest.raises(WmsUnavailableError):
        await service.reserve_inventory(
            ReserveInventoryRequest(
                request_id="REQ-OBS",
                trace_id="trace-wms-obs",
                reservation_key="RSV-OBS",
                sku="SKU-1",
                qty="1",
            )
        )

    assert len(emitted) == 1
    assert emitted[0].attributes["trace_id"] == "trace-wms-obs"
    assert emitted[0].attributes["provider_code"] == "WMS_INVENTORY"
    assert emitted[0].attributes["operation_kind"] == "reserve_inventory"
    assert emitted[0].attributes["breaker_state"] == "OPEN"


@pytest.mark.asyncio
async def test_wms_error_circuit_open_fast_fails_without_http_call(db_engine) -> None:
    breaker_service = WmsCircuitBreakerService(failure_threshold=1, retry_after_seconds=60)
    session_factory = _session_factory(db_engine)
    async with session_factory() as db:
        await breaker_service.record_failure(
            db,
            target_code="WMS_INVENTORY",
            operation_name="release_reservation",
            evidence_key="ev:seed-open",
        )
        await db.commit()

    http_called = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal http_called
        http_called = True
        return httpx.Response(200, json={"reservation_key": "RSV-OPEN", "released": True})

    service = _service(db_engine, httpx.MockTransport(handler), breaker_service=breaker_service)

    with pytest.raises(WmsCircuitOpenError) as exc_info:
        await service.release_reservation(ReleaseReservationRequest(request_id="REQ-OPEN", reservation_key="RSV-OPEN"))

    error = exc_info.value
    evidence = await _get_evidence(db_engine, "ev:release_reservation:REQ-OPEN")

    assert http_called is False
    assert error.evidence_key == "ev:release_reservation:REQ-OPEN"
    assert error.reason_code == "WMS_CIRCUIT_OPEN"
    assert error.retryable is True
    assert evidence is not None
    assert evidence.status == "FAILED"
    assert evidence.reason_code == "WMS_CIRCUIT_OPEN"


@pytest.mark.asyncio
async def test_wms_half_open_trial_in_progress_blocks_second_outbound_effect_without_http(db_engine) -> None:
    breaker_service = WmsCircuitBreakerService(failure_threshold=1, retry_after_seconds=60)
    session_factory = _session_factory(db_engine)
    async with session_factory() as db:
        opened = await breaker_service.record_failure(
            db,
            target_code="WMS_INVENTORY",
            operation_name="release_reservation",
            evidence_key="ev:seed-half-open-busy",
        )
        opened.opened_until = timezone.now_for_db() - timedelta(seconds=1)
        await db.commit()

    async with session_factory() as db:
        first_trial = await breaker_service.before_call(
            db,
            target_code="WMS_INVENTORY",
            operation_name="release_reservation",
        )
        await db.commit()

    assert first_trial.allowed is True
    assert first_trial.reason == "OPEN_RETRY_AFTER_ELAPSED"

    http_called = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal http_called
        http_called = True
        return httpx.Response(200, json={"reservation_key": "RSV-HALF-OPEN-BUSY", "released": True})

    service = _service(db_engine, httpx.MockTransport(handler), breaker_service=breaker_service)

    with pytest.raises(WmsCircuitOpenError) as exc_info:
        await service.release_reservation(
            ReleaseReservationRequest(request_id="REQ-HALF-OPEN-BUSY", reservation_key="RSV-HALF-OPEN-BUSY")
        )

    error = exc_info.value
    evidence = await _get_evidence(db_engine, "ev:release_reservation:REQ-HALF-OPEN-BUSY")
    breaker_state = await _get_breaker_state(
        db_engine,
        target_code="WMS_INVENTORY",
        operation_name="release_reservation",
    )

    assert http_called is False
    assert error.evidence_key == "ev:release_reservation:REQ-HALF-OPEN-BUSY"
    assert error.reason_code == "WMS_CIRCUIT_OPEN"
    assert evidence is not None
    assert evidence.status == "FAILED"
    assert evidence.reason_code == "WMS_CIRCUIT_OPEN"
    assert evidence.response_snapshot["reason"] == "HALF_OPEN_TRIAL_IN_PROGRESS"
    assert breaker_state is not None
    assert breaker_state.state == WmsCircuitBreakerStatus.HALF_OPEN


@pytest.mark.asyncio
async def test_wms_success_persistence_failure_raises_non_retryable_typed_error(
    db_engine,
) -> None:
    from src.app.runtime.orchestration.observability import RuntimeObservabilityRegistry

    class FailingSuccessEvidenceService:
        async def record_sync_call(self, _db: AsyncSession, **kwargs):
            if kwargs["status"] == "SUCCEEDED":
                raise RuntimeError("database unavailable")
            raise AssertionError("本测试不应记录失败 evidence")

    emitted = []
    registry = RuntimeObservabilityRegistry(observers=(emitted.append,))

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "reservation_key": "RSV-PERSIST",
                "accepted": True,
                "request_id": "REQ-PERSIST",
            },
        )

    service = _service(
        db_engine,
        httpx.MockTransport(handler),
        evidence_service=FailingSuccessEvidenceService(),
        observability_emit=registry.emit,
    )

    with pytest.raises(WmsEvidencePersistenceError) as exc_info:
        await service.reserve_inventory(
            ReserveInventoryRequest(
                request_id="REQ-PERSIST",
                trace_id="trace-persist-failure",
                reservation_key="RSV-PERSIST",
                sku="SKU-1",
                qty="1",
            )
        )

    error = exc_info.value
    assert error.operation_name == "reserve_inventory"
    assert error.evidence_key == "ev:reserve_inventory:REQ-PERSIST"
    assert error.http_status == 200
    assert error.reason_code == "WMS_EVIDENCE_PERSISTENCE_FAILED"
    assert error.target_code == "WMS_INVENTORY"
    assert error.retryable is False
    assert len(emitted) == 1
    assert emitted[0].name == "wms_evidence.persistence_failure"
    assert emitted[0].attributes["trace_id"] == "trace-persist-failure"
    assert emitted[0].attributes["provider_code"] == "WMS_INVENTORY"
    assert emitted[0].attributes["operation_kind"] == "reserve_inventory"
    assert emitted[0].attributes["evidence_key"] == "ev:reserve_inventory:REQ-PERSIST"


@pytest.mark.asyncio
async def test_wms_4xx_invalid_json_uses_default_business_reason_without_breaker_failure(
    db_engine,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, content=b"not-json")

    service = _service(
        db_engine,
        httpx.MockTransport(handler),
        breaker_service=WmsCircuitBreakerService(),
    )

    with pytest.raises(WmsBusinessRejectedError) as exc_info:
        await service.reserve_inventory(
            ReserveInventoryRequest(
                request_id="REQ-4XX-NONJSON",
                reservation_key="RSV-4XX",
                sku="SKU-1",
                qty="1",
            )
        )

    error = exc_info.value
    evidence = await _get_evidence(db_engine, "ev:reserve_inventory:REQ-4XX-NONJSON")
    breaker_state = await _get_breaker_state(
        db_engine,
        target_code="WMS_INVENTORY",
        operation_name="reserve_inventory",
    )

    assert error.reason_code == "WMS_BUSINESS_REJECTED"
    assert error.retryable is False
    assert evidence is not None
    assert evidence.reason_code == "WMS_BUSINESS_REJECTED"
    assert evidence.retryable is False
    assert breaker_state is not None
    assert breaker_state.state == WmsCircuitBreakerStatus.CLOSED
    assert breaker_state.failure_count == 0


@pytest.mark.asyncio
async def test_wms_5xx_invalid_json_uses_default_unavailable_reason_and_breaker_failure(
    db_engine,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"not-json")

    service = _service(
        db_engine,
        httpx.MockTransport(handler),
        breaker_service=WmsCircuitBreakerService(failure_threshold=1, retry_after_seconds=60),
    )

    with pytest.raises(WmsUnavailableError) as exc_info:
        await service.confirm_inbound(
            ConfirmInboundRequest(request_id="REQ-5XX-NONJSON", inbound_key="IN-5XX", sku="SKU-1", qty="1")
        )

    error = exc_info.value
    evidence = await _get_evidence(db_engine, "ev:confirm_inbound:REQ-5XX-NONJSON")
    breaker_state = await _get_breaker_state(
        db_engine,
        target_code="WMS_INBOUND",
        operation_name="confirm_inbound",
    )

    assert error.reason_code == "WMS_UNAVAILABLE"
    assert error.retryable is True
    assert evidence is not None
    assert evidence.reason_code == "WMS_UNAVAILABLE"
    assert breaker_state is not None
    assert breaker_state.state == WmsCircuitBreakerStatus.OPEN


@pytest.mark.asyncio
async def test_wms_4xx_json_list_uses_default_business_reason(db_engine) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json=["SKU_LOCKED"])

    service = _service(
        db_engine,
        httpx.MockTransport(handler),
        breaker_service=WmsCircuitBreakerService(),
    )

    with pytest.raises(WmsBusinessRejectedError) as exc_info:
        await service.reserve_inventory(
            ReserveInventoryRequest(
                request_id="REQ-4XX-LIST",
                reservation_key="RSV-LIST",
                sku="SKU-1",
                qty="1",
            )
        )

    evidence = await _get_evidence(db_engine, "ev:reserve_inventory:REQ-4XX-LIST")
    breaker_state = await _get_breaker_state(
        db_engine,
        target_code="WMS_INVENTORY",
        operation_name="reserve_inventory",
    )

    assert exc_info.value.reason_code == "WMS_BUSINESS_REJECTED"
    assert evidence is not None
    assert evidence.reason_code == "WMS_BUSINESS_REJECTED"
    assert breaker_state is not None
    assert breaker_state.failure_count == 0


@pytest.mark.asyncio
async def test_wms_5xx_json_list_uses_default_unavailable_reason(db_engine) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json=["WMS_DOWN"])

    service = _service(
        db_engine,
        httpx.MockTransport(handler),
        breaker_service=WmsCircuitBreakerService(failure_threshold=1, retry_after_seconds=60),
    )

    with pytest.raises(WmsUnavailableError) as exc_info:
        await service.confirm_outbound(
            ConfirmOutboundRequest(request_id="REQ-5XX-LIST", outbound_key="OUT-LIST", sku="SKU-1", qty="1")
        )

    evidence = await _get_evidence(db_engine, "ev:confirm_outbound:REQ-5XX-LIST")
    breaker_state = await _get_breaker_state(
        db_engine,
        target_code="WMS_OUTBOUND",
        operation_name="confirm_outbound",
    )

    assert exc_info.value.reason_code == "WMS_UNAVAILABLE"
    assert evidence is not None
    assert evidence.reason_code == "WMS_UNAVAILABLE"
    assert breaker_state is not None
    assert breaker_state.state == WmsCircuitBreakerStatus.OPEN
