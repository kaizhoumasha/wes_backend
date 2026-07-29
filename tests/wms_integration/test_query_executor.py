"""WMS 19 项 QUERY 的统一 executor、outcome 与预算矩阵。"""

from __future__ import annotations

import importlib
import json
from copy import deepcopy
from dataclasses import replace
from typing import Any

import httpx
import pytest

from src.app.runtime.system_capabilities.wms.provider_catalog import (
    build_wms_provider_catalog,
    freeze_wms_query_binding,
)
from src.app.wms_integration.operation_registry import QUERY_OPERATIONS
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
)
from tests.contracts.wms_integration.provider_profile_support import build_compiled_provider_profile
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

    async def validate_source_version(self, **_kwargs) -> str | None:
        return None

    async def record(self, **kwargs) -> str:
        if self.fail:
            raise RuntimeError("evidence unavailable")
        self.records.append(kwargs)
        return f"evidence:{kwargs['operation_identity']}:1"


class NoCredentials:
    def resolve(self, _credential_reference: str) -> bytes:
        raise AssertionError("NONE binding must not resolve credentials")


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
