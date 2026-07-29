"""WMS inventory QUERY transport 与领域 outcome 合同。"""

from __future__ import annotations

import asyncio
import gzip
import time
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.runtime.system_capabilities.wms.inventory.query_inventory.contract import CONTRACT
from src.app.runtime.system_capabilities.wms.provider_catalog import resolve_wms_operation_binding
from src.app.wms_integration.adapters.query_inventory_operation_adapter import InventoryQueryOperationAdapter
from src.app.wms_integration.models import WmsCallEvidence
from src.app.wms_integration.operation_contract import WmsPaginationConstraint
from src.app.wms_integration.ports.query_inventory_operation import InventoryQueryOperationRequest
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
)
from src.app.wms_integration.services import WmsCircuitBreakerService, wms_call_evidence_service
from src.app.wms_integration.services.query_transport import (
    WmsBoundQueryEndpoint,
    WmsCallEvidenceQueryWriter,
    WmsQueryCallPermit,
    WmsQueryTransportExecutor,
)
from tests.contracts.wms_integration.provider_profile_support import (
    build_hmac_provider_profile_payload,
    build_provider_catalog,
)

PROVIDER_CATALOG = build_provider_catalog(build_hmac_provider_profile_payload())

if TYPE_CHECKING:
    from collections.abc import Mapping


class RecordingEvidenceWriter:
    def __init__(self, *, fail: bool = False) -> None:
        self.outcomes: list[object] = []
        self.fail = fail

    async def before_call(self, *, operation_identity: str, target_code: str) -> WmsQueryCallPermit:
        return WmsQueryCallPermit(allowed=True)

    async def record(
        self,
        *,
        operation_identity: str,
        target_code: str,
        request_snapshot: Mapping[str, object],
        outcome: object,
        permit: WmsQueryCallPermit,
    ) -> str:
        if self.fail:
            raise RuntimeError("evidence storage unavailable")
        self.outcomes.append(outcome)
        return f"evidence:{operation_identity}:1"


class CircuitOpenEvidenceWriter(RecordingEvidenceWriter):
    async def before_call(self, *, operation_identity: str, target_code: str) -> WmsQueryCallPermit:
        return WmsQueryCallPermit(
            allowed=False,
            reason="OPEN",
            retry_after_seconds=12,
        )


class StaticCredentialProvider:
    def resolve(self, _credential_reference: str) -> bytes:
        return b"query-transport-test-secret"


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


def _adapter(
    handler,
    *,
    evidence_writer: RecordingEvidenceWriter | None = None,
    budget: dict[str, object] | None = None,
    pagination: WmsPaginationConstraint | None = None,
) -> InventoryQueryOperationAdapter:
    binding = resolve_wms_operation_binding(
        catalog=PROVIDER_CATALOG,
        profile_identity=PROVIDER_CATALOG.profile_identity,
        operation_identity=CONTRACT.identity,
    )
    current_budget = binding.operation.budget
    effective_budget = SimpleNamespace(
        **{
            "deadline_seconds": current_budget.deadline_seconds,
            "max_attempts": current_budget.max_attempts,
            "backoff_seconds": current_budget.backoff_seconds,
            "max_wire_bytes": current_budget.max_wire_bytes,
            "max_decoded_bytes": current_budget.max_decoded_bytes,
            "max_rows": current_budget.max_rows,
            "max_chunk_bytes": current_budget.max_chunk_bytes,
            "max_compression_ratio": current_budget.max_compression_ratio,
            "allowed_content_encodings": current_budget.allowed_content_encodings,
            "max_json_depth": current_budget.max_json_depth,
            "max_field_length": current_budget.max_field_length,
            **(budget or {}),
        }
    )
    operation = binding.operation.model_copy(
        update={
            "budget": effective_budget,
            "pagination": pagination or binding.operation.pagination,
        }
    )
    binding = binding.model_copy(update={"operation": operation})
    executor = WmsQueryTransportExecutor(
        endpoint=WmsBoundQueryEndpoint(binding=binding, base_url="https://wms.test"),
        transport=httpx.MockTransport(handler),
        evidence_writer=evidence_writer or RecordingEvidenceWriter(),
        credential_provider=StaticCredentialProvider(),
    )
    return InventoryQueryOperationAdapter(executor=executor)


@pytest.mark.asyncio
async def test_explicit_empty_inventory_is_success_with_evidence() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    evidence_writer = RecordingEvidenceWriter()
    adapter = _adapter(handler, evidence_writer=evidence_writer)

    outcome = await adapter.execute(InventoryQueryOperationRequest(material_code="MAT-001"))

    assert isinstance(outcome, QuerySuccess)
    assert outcome.value.items == ()
    assert outcome.evidence_key == "evidence:wms.inventory.query_inventory@v1:1"
    assert len(evidence_writer.outcomes) == 1


@pytest.mark.asyncio
async def test_success_preserves_decimal_precision_and_missing_provider_facts() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"sku": "MAT-001", "available_qty": "9007199254740993.125"}]},
        )

    outcome = await _adapter(handler).execute(InventoryQueryOperationRequest(material_code="MAT-001"))

    assert isinstance(outcome, QuerySuccess)
    assert outcome.value.items[0].available_quantity == Decimal("9007199254740993.125")
    assert outcome.value.items[0].warehouse_code is None
    assert outcome.value.items[0].storage_location_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_type", "reason_code", "retryable"),
    [
        (409, QueryBusinessReject, "INSUFFICIENT_STOCK", None),
        (429, QueryTechnicalFailure, "WMS_RATE_LIMITED", True),
        (503, QueryTechnicalFailure, "WMS_UNAVAILABLE", True),
    ],
)
async def test_http_failures_use_closed_named_outcomes(
    status_code: int,
    expected_type: type,
    reason_code: str,
    retryable: bool | None,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={"retry-after": "3"},
            json={
                "classification": "BUSINESS_REJECT",
                "reason_code": "INSUFFICIENT_STOCK",
                "message": "rejected",
            },
        )

    outcome = await _adapter(handler).execute(InventoryQueryOperationRequest(material_code="MAT-001"))

    assert isinstance(outcome, expected_type)
    assert outcome.reason_code == reason_code
    if retryable is not None:
        assert outcome.retryable is retryable
    if status_code == 429:
        assert outcome.retry_after_seconds == 3


@pytest.mark.asyncio
async def test_timeout_is_explicit_retryable_technical_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    outcome = await _adapter(handler).execute(InventoryQueryOperationRequest(material_code="MAT-001"))

    assert isinstance(outcome, QueryTechnicalFailure)
    assert outcome.reason_code == "WMS_PROVIDER_TIMEOUT"
    assert outcome.retryable is True


@pytest.mark.asyncio
async def test_retryable_rate_limit_retries_contract_backoff_until_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    backoffs: list[float] = []

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(429, headers={"retry-after": "1"})
        return httpx.Response(200, json={"items": []})

    async def record_sleep(seconds: float) -> None:
        backoffs.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    outcome = await _adapter(handler).execute(InventoryQueryOperationRequest(material_code="MAT-001"))

    assert isinstance(outcome, QuerySuccess)
    assert attempts == 3
    assert backoffs == [1, 2]


@pytest.mark.asyncio
async def test_open_circuit_records_failure_evidence_without_sending_http() -> None:
    http_called = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal http_called
        http_called = True
        return httpx.Response(200, json={"items": []})

    evidence_writer = CircuitOpenEvidenceWriter()
    outcome = await _adapter(handler, evidence_writer=evidence_writer).execute(
        InventoryQueryOperationRequest(material_code="MAT-001")
    )

    assert isinstance(outcome, QueryTechnicalFailure)
    assert outcome.reason_code == "WMS_CIRCUIT_OPEN"
    assert outcome.retry_after_seconds == 12
    assert outcome.evidence_key is not None
    assert http_called is False


@pytest.mark.asyncio
async def test_malformed_response_and_unexpected_exception_fail_closed() -> None:
    async def malformed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    async def unexpected(_request: httpx.Request) -> httpx.Response:
        raise RuntimeError("provider SDK defect")

    malformed_outcome = await _adapter(malformed).execute(InventoryQueryOperationRequest(material_code="MAT-001"))
    unexpected_outcome = await _adapter(unexpected).execute(InventoryQueryOperationRequest(material_code="MAT-001"))

    assert isinstance(malformed_outcome, QueryContractFailure)
    assert malformed_outcome.reason_code == "WMS_MALFORMED_RESPONSE"
    assert isinstance(unexpected_outcome, QueryContractFailure)
    assert unexpected_outcome.reason_code == "WMS_UNEXPECTED_TRANSPORT_FAILURE"


@pytest.mark.asyncio
async def test_evidence_failure_replaces_success_with_fail_closed_contract_outcome() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    outcome = await _adapter(handler, evidence_writer=RecordingEvidenceWriter(fail=True)).execute(
        InventoryQueryOperationRequest(material_code="MAT-001")
    )

    assert isinstance(outcome, QueryContractFailure)
    assert outcome.reason_code == "WMS_EVIDENCE_WRITE_FAILED"
    assert outcome.evidence_key is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_factory", "budget", "reason_code"),
    [
        (
            lambda: httpx.Response(200, headers={"content-length": "10000"}, content=b"{}"),
            {"max_wire_bytes": 64, "max_decoded_bytes": 128},
            "WMS_WIRE_BUDGET_EXCEEDED",
        ),
        (
            lambda: httpx.Response(200, stream=ChunkStream([b"{" + b" " * 40, b" " * 40 + b"}"])),
            {"max_wire_bytes": 64, "max_decoded_bytes": 128},
            "WMS_WIRE_BUDGET_EXCEEDED",
        ),
        (
            lambda: httpx.Response(
                200,
                headers={"content-length": "2"},
                stream=ChunkStream([b"{" + b" " * 80 + b"}"]),
            ),
            {"max_wire_bytes": 64, "max_decoded_bytes": 128},
            "WMS_WIRE_BUDGET_EXCEEDED",
        ),
        (
            lambda: httpx.Response(200, stream=ChunkStream([b"{" + b" " * 40 + b"}"])),
            {"max_wire_bytes": 128, "max_decoded_bytes": 128, "max_chunk_bytes": 32},
            "WMS_CHUNK_BUDGET_EXCEEDED",
        ),
        (
            lambda: httpx.Response(
                200,
                headers={"content-encoding": "br"},
                stream=ChunkStream([b"not-supported"]),
            ),
            {},
            "WMS_UNSUPPORTED_CONTENT_ENCODING",
        ),
    ],
)
async def test_wire_content_length_chunk_and_encoding_budgets_fail_closed(
    response_factory,
    budget: dict[str, object],
    reason_code: str,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return response_factory()

    outcome = await _adapter(handler, budget=budget).execute(InventoryQueryOperationRequest(material_code="MAT-001"))

    assert isinstance(outcome, QueryContractFailure)
    assert outcome.reason_code == reason_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decoded_body", "budget", "reason_code"),
    [
        (
            b'{"items":[],"padding":"' + b"a" * 256 + b'"}',
            {"max_decoded_bytes": 128, "max_compression_ratio": 100.0},
            "WMS_DECODED_BUDGET_EXCEEDED",
        ),
        (
            b'{"items":[],"padding":"' + b"a" * 2048 + b'"}',
            {"max_decoded_bytes": 4096, "max_compression_ratio": 2.0},
            "WMS_COMPRESSION_RATIO_EXCEEDED",
        ),
    ],
)
async def test_decoded_and_compression_ratio_budgets_fail_closed(
    decoded_body: bytes,
    budget: dict[str, object],
    reason_code: str,
) -> None:
    compressed = gzip.compress(decoded_body)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=ChunkStream([compressed]))

    outcome = await _adapter(handler, budget=budget).execute(InventoryQueryOperationRequest(material_code="MAT-001"))

    assert isinstance(outcome, QueryContractFailure)
    assert outcome.reason_code == reason_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "budget", "reason_code"),
    [
        ({"items": [], "nested": {"a": {"b": {"c": 1}}}}, {"max_json_depth": 3}, "WMS_JSON_DEPTH_EXCEEDED"),
        ({"items": [], "field": "x" * 65}, {"max_field_length": 64}, "WMS_JSON_FIELD_LENGTH_EXCEEDED"),
        (
            {"items": [{"sku": "MAT-1", "available_qty": "1"}, {"sku": "MAT-2", "available_qty": "2"}]},
            {"max_rows": 1},
            "WMS_ROW_BUDGET_EXCEEDED",
        ),
    ],
)
async def test_json_structure_field_and_row_budgets_fail_closed(
    payload: dict[str, object],
    budget: dict[str, object],
    reason_code: str,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    outcome = await _adapter(handler, budget=budget).execute(InventoryQueryOperationRequest(material_code="MAT-001"))

    assert isinstance(outcome, QueryContractFailure)
    assert outcome.reason_code == reason_code


@pytest.mark.asyncio
async def test_pagination_aggregates_once_and_enforces_cumulative_row_budget() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        if cursor is None:
            return httpx.Response(
                200,
                json={"items": [{"sku": "MAT-1", "available_qty": "1"}], "next_cursor": "page-2"},
            )
        return httpx.Response(200, json={"items": [{"sku": "MAT-2", "available_qty": "2"}]})

    pagination = WmsPaginationConstraint(
        request_cursor_field="cursor",
        response_cursor_field="next_cursor",
        response_items_field="items",
        max_pages=3,
        max_rows=10_000,
        max_page_size=500,
    )
    success = await _adapter(handler, pagination=pagination).execute(
        InventoryQueryOperationRequest(material_code="MAT-001")
    )
    over_budget = await _adapter(handler, pagination=pagination, budget={"max_rows": 1}).execute(
        InventoryQueryOperationRequest(material_code="MAT-001")
    )

    assert isinstance(success, QuerySuccess)
    assert tuple(item.material_code for item in success.value.items) == ("MAT-1", "MAT-2")
    assert isinstance(over_budget, QueryContractFailure)
    assert over_budget.reason_code == "WMS_ROW_BUDGET_EXCEEDED"


@pytest.mark.asyncio
async def test_total_deadline_covers_all_pages() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.03)
        return httpx.Response(200, json={"items": []})

    started_at = time.perf_counter()
    outcome = await _adapter(handler, budget={"deadline_seconds": 0.01}).execute(
        InventoryQueryOperationRequest(material_code="MAT-001")
    )
    elapsed = time.perf_counter() - started_at

    assert isinstance(outcome, QueryTechnicalFailure)
    assert outcome.reason_code == "WMS_PROVIDER_TIMEOUT"
    assert elapsed < 0.1


@pytest.mark.asyncio
async def test_real_evidence_writer_persists_query_outcome_before_return(db_engine) -> None:
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [], "source_version": "WMS-42"})

    executor = WmsQueryTransportExecutor(
        endpoint=WmsBoundQueryEndpoint(
            binding=resolve_wms_operation_binding(
                catalog=PROVIDER_CATALOG,
                profile_identity=PROVIDER_CATALOG.profile_identity,
                operation_identity=CONTRACT.identity,
            ),
            base_url="https://wms.test",
        ),
        transport=httpx.MockTransport(handler),
        evidence_writer=WmsCallEvidenceQueryWriter(
            session_factory=session_factory,
            provider_profile_identity=PROVIDER_CATALOG.profile_identity,
            evidence_service=wms_call_evidence_service,
            breaker_service=WmsCircuitBreakerService(),
        ),
        credential_provider=StaticCredentialProvider(),
    )
    outcome = await InventoryQueryOperationAdapter(executor=executor).execute(
        InventoryQueryOperationRequest(material_code="MAT-001")
    )

    assert isinstance(outcome, QuerySuccess)
    assert outcome.evidence_key is not None
    async with session_factory() as db:
        result = await db.execute(select(WmsCallEvidence).where(WmsCallEvidence.evidence_key == outcome.evidence_key))
        evidence = result.scalar_one()
    assert evidence.operation_name == CONTRACT.identity
    assert evidence.provider_profile_identity == PROVIDER_CATALOG.profile_identity
    assert evidence.status == "SUCCEEDED"
    assert evidence.response_snapshot["source_version"] == "WMS-42"
