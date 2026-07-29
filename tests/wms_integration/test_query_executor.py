"""WMS 19 项 QUERY 的统一 executor、outcome 与预算矩阵。"""

from __future__ import annotations

import gzip
import importlib
import json
from copy import deepcopy
from dataclasses import replace
from typing import Any

import httpx
import pytest
from sqlalchemy.exc import DBAPIError

from src.app.runtime.system_capabilities.wms.provider_catalog import (
    build_wms_provider_catalog,
    freeze_wms_query_binding,
)
from src.app.sys.external_http_binding import ExternalHttpTargetSnapshot
from src.app.wms_integration.operation_registry import QUERY_OPERATIONS
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
)
from tests.contracts.wms_integration.provider_profile_support import (
    build_compiled_provider_profile,
    build_hmac_provider_profile_payload,
)
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES, RESULT_FIXTURES

COMPILED_PROFILE = build_compiled_provider_profile()
CATALOG = build_wms_provider_catalog(COMPILED_PROFILE)


class EvidenceWriter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.records: list[dict[str, Any]] = []

    async def before_call(self, **_kwargs):
        module = importlib.import_module("src.app.wms_integration.query_evidence")
        return module.WmsQueryCallPermit(allowed=True)

    async def record(self, **kwargs):
        from src.app.wms_integration.query_evidence import WmsQueryEvidenceRecord

        if self.fail:
            raise RuntimeError("evidence unavailable")
        self.records.append(kwargs)
        return WmsQueryEvidenceRecord(
            evidence_key=f"evidence:{kwargs['operation_identity']}:1",
            outcome=kwargs["outcome"],
        )


class NoCredentials:
    def resolve(self, _credential_reference: str) -> bytes:
        raise AssertionError("NONE binding must not resolve credentials")


class RawAsyncStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes | tuple[bytes, ...]) -> None:
        self._chunks = (content,) if isinstance(content, bytes) else content

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


async def no_sleep(_seconds: float) -> None:
    return None


def _executor(operation, handler, *, evidence_writer=None):
    module = importlib.import_module("src.app.wms_integration.query_executor")
    assert hasattr(module, "WmsRegistryQueryExecutor"), "缺少 registry 驱动 QUERY executor"
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    executor = module.WmsRegistryQueryExecutor(
        operation=operation,
        endpoint=COMPILED_PROFILE.operations[operation.identity],
        frozen_binding=freeze_wms_query_binding(
            catalog=CATALOG,
            operation_identity=operation.identity,
        ),
        client=client,
        credential_provider=NoCredentials(),
        evidence_writer=evidence_writer or EvidenceWriter(),
        sleep=no_sleep,
    )
    return executor, client


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", QUERY_OPERATIONS, ids=lambda operation: operation.identity)
async def test_all_19_queries_parse_strict_typed_success(operation) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=RESULT_FIXTURES[operation.identity])

    executor, client = _executor(operation, handler)
    request = operation.request_model.model_validate_json(json.dumps(REQUEST_FIXTURES[operation.identity]))
    try:
        outcome = await executor.execute(request)
    finally:
        await client.aclose()

    assert isinstance(outcome, QuerySuccess)
    assert outcome.value == operation.result_model.model_validate_json(json.dumps(RESULT_FIXTURES[operation.identity]))
    assert outcome.evidence_key == f"evidence:{operation.identity}:1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_type", "reason_code"),
    [
        ("business_reject", QueryBusinessReject, None),
        ("timeout", QueryTechnicalFailure, "WMS_PROVIDER_TIMEOUT"),
        ("connect", QueryTechnicalFailure, "WMS_PROVIDER_UNAVAILABLE"),
        ("rate_limit", QueryTechnicalFailure, "WMS_RATE_LIMITED"),
        ("unavailable", QueryTechnicalFailure, "WMS_UNAVAILABLE"),
        ("malformed", QueryContractFailure, "WMS_MALFORMED_RESPONSE"),
        ("unknown_field", QueryContractFailure, "WMS_MALFORMED_RESPONSE"),
    ],
)
@pytest.mark.parametrize("operation", QUERY_OPERATIONS, ids=lambda operation: operation.identity)
async def test_shared_failure_matrix_for_all_19_queries(
    operation,
    scenario: str,
    expected_type: type,
    reason_code: str | None,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if scenario == "business_reject":
            return httpx.Response(
                404,
                json={
                    "classification": "BUSINESS_REJECT",
                    "reason_code": operation.reject_codes[0],
                    "message": "rejected",
                },
            )
        if scenario == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if scenario == "connect":
            raise httpx.ConnectError("connect failed", request=request)
        if scenario == "rate_limit":
            return httpx.Response(429, headers={"retry-after": "3"})
        if scenario == "unavailable":
            return httpx.Response(503)
        if scenario == "malformed":
            return httpx.Response(200, content=b"not-json")
        payload = deepcopy(RESULT_FIXTURES[operation.identity])
        payload["unknown"] = "forbidden"
        return httpx.Response(200, json=payload)

    executor, client = _executor(operation, handler)
    request = operation.request_model.model_validate_json(json.dumps(REQUEST_FIXTURES[operation.identity]))
    try:
        outcome = await executor.execute(request)
    finally:
        await client.aclose()

    assert isinstance(outcome, expected_type)
    assert outcome.reason_code == (operation.reject_codes[0] if reason_code is None else reason_code)
    if isinstance(outcome, QueryTechnicalFailure):
        assert outcome.retryable is True
    if scenario == "rate_limit":
        assert outcome.retry_after_seconds == 3


@pytest.mark.parametrize(
    ("status_code", "expected_retryable"),
    [
        (500, True),
        (502, True),
        (503, True),
        (504, True),
        (501, False),
        (505, False),
        (600, False),
    ],
)
def test_http_retry_allowlist_excludes_unimplemented_version_and_non_http_status(
    status_code: int,
    expected_retryable: bool,
) -> None:
    from src.app.wms_integration.query_response import classify_http_failure

    outcome = classify_http_failure(status_code, None, {})

    assert isinstance(outcome, QueryTechnicalFailure)
    assert outcome.retryable is expected_retryable
    assert outcome.reason_code == ("WMS_UNAVAILABLE" if expected_retryable else "WMS_UNEXPECTED_HTTP_STATUS")


@pytest.mark.asyncio
async def test_pagination_rejects_cursor_cycle_page_row_and_source_snapshot_drift() -> None:
    operation = QUERY_OPERATIONS[13]
    request = operation.request_model.model_validate_json(json.dumps(REQUEST_FIXTURES[operation.identity]))

    async def cursor_cycle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [], "next_cursor": "same", "source_version": "2"},
        )

    async def snapshot_drift(request: httpx.Request) -> httpx.Response:
        source_version = "2" if request.url.params.get("cursor") is None else "3"
        return httpx.Response(
            200,
            json={
                "items": [],
                "next_cursor": "next" if source_version == "2" else None,
                "source_version": source_version,
            },
        )

    cycle_executor, cycle_client = _executor(operation, cursor_cycle)
    drift_executor, drift_client = _executor(operation, snapshot_drift)
    try:
        cycle = await cycle_executor.execute(request)
        drift = await drift_executor.execute(request)
    finally:
        await cycle_client.aclose()
        await drift_client.aclose()

    assert isinstance(cycle, QueryContractFailure)
    assert cycle.reason_code == "WMS_PAGINATION_CURSOR_REUSED"
    assert isinstance(drift, QueryContractFailure)
    assert drift.reason_code == "WMS_SOURCE_VERSION_CHANGED_DURING_PAGINATION"


@pytest.mark.asyncio
async def test_page_and_row_budgets_fail_closed() -> None:
    operation = QUERY_OPERATIONS[13]
    request = operation.request_model.model_validate_json(json.dumps(REQUEST_FIXTURES[operation.identity]))
    bounded = operation.model_copy(
        update={
            "budget": operation.budget.model_copy(update={"max_rows": 1}),
            "pagination": operation.pagination.model_copy(update={"max_pages": 1, "max_rows": 1}),
        }
    )

    async def too_many_rows(_request: httpx.Request) -> httpx.Response:
        item = {
            "material_code": "MAT-1",
            "available_quantity": "1",
            "total_quantity": "1",
            "reserved_quantity": "0",
        }
        return httpx.Response(200, json={"items": [item, item], "source_version": "1"})

    async def too_many_pages(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [], "next_cursor": "next", "source_version": "1"})

    row_executor, row_client = _executor(bounded, too_many_rows)
    page_executor, page_client = _executor(bounded, too_many_pages)
    try:
        rows = await row_executor.execute(request)
        pages = await page_executor.execute(request)
    finally:
        await row_client.aclose()
        await page_client.aclose()

    assert isinstance(rows, QueryContractFailure)
    assert rows.reason_code == "WMS_ROW_BUDGET_EXCEEDED"
    assert isinstance(pages, QueryContractFailure)
    assert pages.reason_code == "WMS_PAGE_BUDGET_EXCEEDED"


@pytest.mark.asyncio
async def test_evidence_failure_replaces_success_with_contract_failure() -> None:
    operation = QUERY_OPERATIONS[0]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=RESULT_FIXTURES[operation.identity])

    executor, client = _executor(operation, handler, evidence_writer=EvidenceWriter(fail=True))
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    try:
        outcome = await executor.execute(request)
    finally:
        await client.aclose()

    assert isinstance(outcome, QueryContractFailure)
    assert outcome.reason_code == "WMS_EVIDENCE_WRITE_FAILED"


def test_executor_private_projection_and_reject_guards() -> None:
    module = importlib.import_module("src.app.wms_integration.query_executor")
    projection = module.WmsQueryRequestProjection(
        operation_identity="query",
        method="GET",
        url="https://wms.example/query",
        path_field_names=(),
        query_params=(("code", "A"), ("code", "B"), ("code", "C")),
        json_body=None,
        request_canonical_hash="hash",
        evidence_snapshot={},
    )

    assert module._query_payload(projection) == {"code": ["A", "B", "C"]}
    undeclared = module._validate_business_reject(
        QUERY_OPERATIONS[0],
        QueryBusinessReject("UNDECLARED", "rejected"),
    )
    assert isinstance(undeclared, QueryContractFailure)
    assert module._validate_business_reject(QUERY_OPERATIONS[0], None) is None


@pytest.mark.asyncio
async def test_executor_constructor_and_request_type_guards() -> None:
    module = importlib.import_module("src.app.wms_integration.query_executor")
    operation = QUERY_OPERATIONS[0]
    endpoint = COMPILED_PROFILE.operations[operation.identity]
    binding = freeze_wms_query_binding(catalog=CATALOG, operation_identity=operation.identity)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(500)))

    def construct(*, operation_value=operation, endpoint_value=endpoint, binding_value=binding):
        return module.WmsRegistryQueryExecutor(
            operation=operation_value,
            endpoint=endpoint_value,
            frozen_binding=binding_value,
            client=client,
            credential_provider=NoCredentials(),
            evidence_writer=EvidenceWriter(),
        )

    try:
        with pytest.raises(ValueError, match="QUERY semantics"):
            construct(operation_value=operation.model_copy(update={"mode": "EFFECT"}))
        with pytest.raises(ValueError, match="identity mismatch"):
            construct(endpoint_value=COMPILED_PROFILE.operations[QUERY_OPERATIONS[1].identity])

        method_binding = deepcopy(binding)
        object.__setattr__(
            method_binding,
            "target_snapshot",
            ExternalHttpTargetSnapshot(
                code=binding.target_snapshot.code,
                url=binding.target_snapshot.url,
                http_method="POST",
                timeout_seconds=binding.target_snapshot.timeout_seconds,
            ),
        )
        with pytest.raises(ValueError, match="method mismatch"):
            construct(binding_value=method_binding)

        endpoint_binding = deepcopy(binding)
        object.__setattr__(
            endpoint_binding,
            "target_snapshot",
            ExternalHttpTargetSnapshot(
                code=binding.target_snapshot.code,
                url="https://different.example/query",
                http_method="GET",
                timeout_seconds=binding.target_snapshot.timeout_seconds,
            ),
        )
        with pytest.raises(ValueError, match="frozen endpoint"):
            construct(binding_value=endpoint_binding)

        executor = construct()
        with pytest.raises(TypeError, match="typed request"):
            await executor.execute(QUERY_OPERATIONS[1].request_model())
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_executor_circuit_open_and_empty_evidence_key_fail_closed() -> None:
    operation = QUERY_OPERATIONS[0]

    class CircuitOpenWriter(EvidenceWriter):
        async def before_call(self, **_kwargs):
            from src.app.wms_integration.query_evidence import WmsQueryCallPermit

            return WmsQueryCallPermit(allowed=False, retry_after_seconds=4)

    class EmptyKeyWriter(EvidenceWriter):
        async def record(self, **kwargs):
            from src.app.wms_integration.query_evidence import WmsQueryEvidenceRecord

            return WmsQueryEvidenceRecord(evidence_key=" ", outcome=kwargs["outcome"])

    async def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("circuit-open 不得调用 provider")

    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    open_executor, open_client = _executor(operation, handler, evidence_writer=CircuitOpenWriter())
    empty_executor, empty_client = _executor(
        operation,
        lambda _request: httpx.Response(200, json=RESULT_FIXTURES[operation.identity]),
        evidence_writer=EmptyKeyWriter(),
    )
    try:
        opened = await open_executor.execute(request)
        empty = await empty_executor.execute(request)
    finally:
        await open_client.aclose()
        await empty_client.aclose()

    assert isinstance(opened, QueryTechnicalFailure)
    assert opened.reason_code == "WMS_CIRCUIT_OPEN"
    assert opened.retry_after_seconds == 4
    assert isinstance(empty, QueryContractFailure)
    assert empty.reason_code == "WMS_EVIDENCE_WRITE_FAILED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "reason_code"),
    [
        (
            lambda request: (_ for _ in ()).throw(httpx.RequestError("request failed", request=request)),
            "WMS_PROVIDER_UNAVAILABLE",
        ),
        (
            lambda _request: httpx.Response(
                200,
                headers={"content-encoding": "br"},
                stream=RawAsyncStream(b"{}"),
            ),
            "WMS_UNSUPPORTED_CONTENT_ENCODING",
        ),
        (
            lambda _request: httpx.Response(
                200,
                headers={"content-encoding": "gzip"},
                stream=RawAsyncStream(b"broken"),
            ),
            "WMS_CONTENT_ENCODING_INVALID",
        ),
        (
            lambda _request: httpx.Response(200, content=b"{}", headers={"content-length": "invalid"}),
            "WMS_MALFORMED_RESPONSE",
        ),
        (
            lambda _request: httpx.Response(200, json=[]),
            "WMS_MALFORMED_RESPONSE",
        ),
    ],
)
async def test_executor_transport_and_decode_exception_matrix(handler, reason_code: str) -> None:
    operation = QUERY_OPERATIONS[0]
    executor, client = _executor(operation, handler)
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    try:
        outcome = await executor.execute(request)
    finally:
        await client.aclose()

    assert isinstance(outcome, QueryContractFailure | QueryTechnicalFailure)
    assert outcome.reason_code == reason_code


@pytest.mark.asyncio
async def test_executor_total_deadline_fails_as_retryable_timeout() -> None:
    import asyncio

    operation = QUERY_OPERATIONS[0].model_copy(
        update={"budget": QUERY_OPERATIONS[0].budget.model_copy(update={"deadline_seconds": 0.001, "max_attempts": 1})}
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=RESULT_FIXTURES[operation.identity])

    executor, client = _executor(operation, handler)
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    try:
        outcome = await executor.execute(request)
    finally:
        await client.aclose()

    assert isinstance(outcome, QueryTechnicalFailure)
    assert outcome.reason_code == "WMS_PROVIDER_TIMEOUT"
    assert outcome.retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("budget_update", "response_factory", "reason_code"),
    [
        (
            {"max_wire_bytes": 1},
            lambda _operation: httpx.Response(200, stream=RawAsyncStream((b"{", b"}"))),
            "WMS_WIRE_BUDGET_EXCEEDED",
        ),
        (
            {},
            lambda _operation: httpx.Response(
                200,
                json={"nested": {"x": {"x": {"x": {"x": {"x": {"x": {"x": {"x": {"x": {"x": {"x": {"x": 1}}}}}}}}}}}}},
            ),
            "WMS_JSON_DEPTH_EXCEEDED",
        ),
        (
            {},
            lambda _operation: httpx.Response(200, json={"x" * 16_385: 1}),
            "WMS_JSON_FIELD_LENGTH_EXCEEDED",
        ),
    ],
)
async def test_executor_public_execute_enforces_wire_and_json_budgets(
    budget_update: dict[str, int],
    response_factory,
    reason_code: str,
) -> None:
    base = QUERY_OPERATIONS[0]
    operation = base.model_copy(update={"budget": base.budget.model_copy(update=budget_update)})
    executor, client = _executor(operation, lambda _request: response_factory(operation))
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    try:
        outcome = await executor.execute(request)
    finally:
        await client.aclose()

    assert isinstance(outcome, QueryContractFailure)
    assert outcome.reason_code == reason_code


@pytest.mark.asyncio
async def test_executor_public_execute_decodes_gzip_and_rejects_undeclared_business_code() -> None:
    operation = QUERY_OPERATIONS[0]
    compressed = gzip.compress(json.dumps(RESULT_FIXTURES[operation.identity]).encode())
    gzip_executor, gzip_client = _executor(
        operation,
        lambda _request: httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=RawAsyncStream(compressed),
        ),
    )
    reject_executor, reject_client = _executor(
        operation,
        lambda _request: httpx.Response(
            422,
            json={
                "classification": "BUSINESS_REJECT",
                "reason_code": "UNDECLARED",
                "message": "rejected",
            },
        ),
    )
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    try:
        decoded = await gzip_executor.execute(request)
        rejected = await reject_executor.execute(request)
    finally:
        await gzip_client.aclose()
        await reject_client.aclose()

    assert isinstance(decoded, QuerySuccess)
    assert isinstance(rejected, QueryContractFailure)
    assert rejected.reason_code == "WMS_UNDECLARED_REJECT_CODE"


@pytest.mark.asyncio
async def test_executor_503_and_429_honor_backoff_and_retry_after() -> None:
    operation = QUERY_OPERATIONS[0]
    sleeps: list[float] = []
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        if calls == 2:
            return httpx.Response(429, headers={"retry-after": "3"})
        return httpx.Response(200, json=RESULT_FIXTURES[operation.identity])

    async def track_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    executor, client = _executor(operation, handler)
    executor._sleep = track_sleep
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    try:
        outcome = await executor.execute(request)
    finally:
        await client.aclose()

    assert isinstance(outcome, QuerySuccess)
    assert calls == 3
    assert sleeps == [operation.budget.backoff_seconds[0], 3]


@pytest.mark.asyncio
async def test_executor_invalid_frozen_credential_resolution_fails_preparation_closed() -> None:
    module = importlib.import_module("src.app.wms_integration.query_executor")
    operation = QUERY_OPERATIONS[0]
    compiled = build_compiled_provider_profile(build_hmac_provider_profile_payload())
    catalog = build_wms_provider_catalog(compiled)

    class InvalidCredentials:
        def resolve(self, _credential_reference: str) -> bytes:
            raise ValueError("credential is unavailable")

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: (_ for _ in ()).throw(AssertionError("不得发送无凭据请求"))),
        trust_env=False,
    )
    executor = module.WmsRegistryQueryExecutor(
        operation=operation,
        endpoint=compiled.operations[operation.identity],
        frozen_binding=freeze_wms_query_binding(catalog=catalog, operation_identity=operation.identity),
        client=client,
        credential_provider=InvalidCredentials(),
        evidence_writer=EvidenceWriter(),
        sleep=no_sleep,
    )
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    try:
        outcome = await executor.execute(request)
    finally:
        await client.aclose()

    assert isinstance(outcome, QueryContractFailure)
    assert outcome.reason_code == "WMS_QUERY_PREPARATION_FAILED"


@pytest.mark.asyncio
async def test_executor_dbapi_evidence_failure_is_not_downgraded() -> None:
    operation = QUERY_OPERATIONS[0]

    class DbFailureWriter(EvidenceWriter):
        async def record(self, **_kwargs):
            raise DBAPIError("statement", {}, RuntimeError("db failed"))

    executor, client = _executor(
        operation,
        lambda _request: httpx.Response(200, json=RESULT_FIXTURES[operation.identity]),
        evidence_writer=DbFailureWriter(),
    )
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    try:
        with pytest.raises(DBAPIError):
            await executor.execute(request)
    finally:
        await client.aclose()
