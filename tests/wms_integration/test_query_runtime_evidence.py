"""WMS data lane 长期 client、frozen auth、evidence 与版本门禁。"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from src.app.runtime.system_capabilities.wms.provider_catalog import (
    build_wms_provider_catalog,
    freeze_wms_query_binding,
)
from src.app.wms_integration.operation_registry import QUERY_OPERATIONS
from src.app.wms_integration.ports.query_outcome import QueryContractFailure, QuerySuccess
from src.app.wms_integration.query_evidence import classify_source_version
from src.app.wms_integration.query_runtime import WmsDataLaneQueryRuntime
from tests.contracts.wms_integration.provider_profile_support import (
    build_compiled_provider_profile,
    build_hmac_provider_profile_payload,
)
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES, RESULT_FIXTURES


class TrackingTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []
        self.close_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await self.handler(request)

    async def aclose(self) -> None:
        self.close_count += 1


class EvidenceWriter:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.source_versions: dict[tuple[str, str], tuple[str, str]] = {}

    async def before_call(self, **_kwargs):
        module = importlib.import_module("src.app.wms_integration.query_evidence")
        return module.WmsQueryCallPermit(allowed=True)

    async def record(self, **kwargs):
        from src.app.wms_integration.query_evidence import WmsQueryEvidenceRecord

        self.records.append(kwargs)
        outcome = kwargs["outcome"]
        source_version = getattr(getattr(outcome, "value", None), "source_version", None)
        response_hash = kwargs["response_hash"]
        if source_version is not None and response_hash is not None:
            key = (kwargs["operation_identity"], kwargs["request_canonical_hash"])
            previous = self.source_versions.get(key)
            reason_code = None
            if previous is not None:
                previous_version, previous_hash = previous
                reason_code = classify_source_version(
                    previous_version=previous_version,
                    previous_response_hash=previous_hash,
                    source_version=str(source_version),
                    response_hash=response_hash,
                )
            if reason_code is None:
                self.source_versions[key] = (str(source_version), response_hash)
            else:
                outcome = QueryContractFailure(
                    reason_code=reason_code,
                    message="source version conflict",
                )
        return WmsQueryEvidenceRecord(
            evidence_key=f"evidence:{len(self.records)}",
            outcome=outcome,
        )


class StaticCredentialProvider:
    def __init__(self, secret: bytes) -> None:
        self.secret = secret
        self.references: list[str] = []

    def resolve(self, credential_reference: str) -> bytes:
        self.references.append(credential_reference)
        return self.secret


async def no_sleep(_seconds: float) -> None:
    return None


def _executor(
    operation,
    *,
    compiled_profile,
    client,
    evidence_writer,
    credential_provider,
):
    module = importlib.import_module("src.app.wms_integration.query_executor")
    catalog = build_wms_provider_catalog(compiled_profile)
    return module.WmsRegistryQueryExecutor(
        operation=operation,
        endpoint=compiled_profile.operations[operation.identity],
        frozen_binding=freeze_wms_query_binding(catalog=catalog, operation_identity=operation.identity),
        client=client,
        credential_provider=credential_provider,
        evidence_writer=evidence_writer,
        sleep=no_sleep,
        now=lambda: datetime(2026, 7, 29, tzinfo=UTC),
        nonce_factory=lambda: "nonce-1",
    )


@pytest.mark.asyncio
async def test_attempts_and_pages_borrow_one_client_until_lane_shutdown() -> None:
    operation = QUERY_OPERATIONS[13]
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        if request.url.params.get("cursor") is None:
            return httpx.Response(200, json={"items": [], "next_cursor": "next", "source_version": "1"})
        return httpx.Response(200, json={"items": [], "source_version": "1"})

    transport = TrackingTransport(handler)
    client = httpx.AsyncClient(transport=transport, trust_env=False)
    executor = _executor(
        operation,
        compiled_profile=build_compiled_provider_profile(),
        client=client,
        evidence_writer=EvidenceWriter(),
        credential_provider=StaticCredentialProvider(b"unused"),
    )
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])

    outcome = await executor.execute(request)

    assert isinstance(outcome, QuerySuccess)
    assert calls == 3
    assert transport.close_count == 0
    await client.aclose()
    assert transport.close_count == 1


@pytest.mark.asyncio
async def test_none_and_hmac_use_shared_frozen_header_contract() -> None:
    operation = QUERY_OPERATIONS[0]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=RESULT_FIXTURES[operation.identity])

    none_transport = TrackingTransport(handler)
    none_client = httpx.AsyncClient(transport=none_transport, trust_env=False)
    none_credentials = StaticCredentialProvider(b"unused")
    none_executor = _executor(
        operation,
        compiled_profile=build_compiled_provider_profile(),
        client=none_client,
        evidence_writer=EvidenceWriter(),
        credential_provider=none_credentials,
    )
    hmac_profile = build_compiled_provider_profile(build_hmac_provider_profile_payload())
    hmac_transport = TrackingTransport(handler)
    hmac_client = httpx.AsyncClient(transport=hmac_transport, trust_env=False)
    secret = b"query-hmac-secret"
    hmac_credentials = StaticCredentialProvider(secret)
    hmac_executor = _executor(
        operation,
        compiled_profile=hmac_profile,
        client=hmac_client,
        evidence_writer=EvidenceWriter(),
        credential_provider=hmac_credentials,
    )
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    try:
        await none_executor.execute(request)
        await hmac_executor.execute(request)
    finally:
        await none_client.aclose()
        await hmac_client.aclose()

    none_headers = none_transport.requests[0].headers
    assert none_headers["X-WES-Content-SHA256"]
    assert all(not key.lower().startswith("x-wes-signature") for key in none_headers)
    assert none_credentials.references == []

    hmac_request = hmac_transport.requests[0]
    hmac_headers = hmac_request.headers
    credential_reference = hmac_profile.profile.outbound_auth.credential_reference
    assert credential_reference is not None
    assert hmac_credentials.references == [credential_reference]
    canonical = "\n".join(
        (
            hmac_request.method,
            hmac_request.url.path,
            hmac_headers["X-WES-Timestamp"],
            "nonce-1",
            hmac_headers["X-WES-Content-SHA256"],
        )
    )
    assert (
        hmac_headers["X-WES-Signature"]
        == hmac.new(
            secret,
            canonical.encode(),
            hashlib.sha256,
        ).hexdigest()
    )


@pytest.mark.asyncio
async def test_evidence_contains_binding_attempt_and_response_hash_without_q19_secrets() -> None:
    operation = QUERY_OPERATIONS[-1]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=RESULT_FIXTURES[operation.identity])

    writer = EvidenceWriter()
    transport = TrackingTransport(handler)
    client = httpx.AsyncClient(transport=transport, trust_env=False)
    profile = build_compiled_provider_profile()
    executor = _executor(
        operation,
        compiled_profile=profile,
        client=client,
        evidence_writer=writer,
        credential_provider=StaticCredentialProvider(b"unused"),
    )
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    try:
        outcome = await executor.execute(request)
    finally:
        await client.aclose()

    assert isinstance(outcome, QuerySuccess)
    record = writer.records[0]
    assert record["profile_digest"]
    assert record["endpoint_digest"] == profile.operations[operation.identity].endpoint_digest
    assert record["attempt_count"] == 1
    assert len(record["response_hash"]) == 64
    serialized = json.dumps(record["request_snapshot"], ensure_ascii=False, sort_keys=True)
    for secret in ("RAW-SIX-IN-ONE", "HHPN-001", "MFR-001", "PKG-001"):
        assert secret not in serialized


@pytest.mark.asyncio
async def test_stateful_source_version_rejects_regression_and_same_version_payload_conflict() -> None:
    operation = QUERY_OPERATIONS[14]
    responses = [
        {**RESULT_FIXTURES[operation.identity], "source_version": "2"},
        {**RESULT_FIXTURES[operation.identity], "source_version": "1"},
        {**RESULT_FIXTURES[operation.identity], "source_version": "2", "status": "RELEASED"},
    ]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    writer = EvidenceWriter()
    client = httpx.AsyncClient(transport=TrackingTransport(handler), trust_env=False)
    executor = _executor(
        operation,
        compiled_profile=build_compiled_provider_profile(),
        client=client,
        evidence_writer=writer,
        credential_provider=StaticCredentialProvider(b"unused"),
    )
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    try:
        first = await executor.execute(request)
        regression = await executor.execute(request)
        conflict = await executor.execute(request)
    finally:
        await client.aclose()

    assert isinstance(first, QuerySuccess)
    assert isinstance(regression, QueryContractFailure)
    assert regression.reason_code == "WMS_SOURCE_VERSION_REGRESSION"
    assert isinstance(conflict, QueryContractFailure)
    assert conflict.reason_code == "WMS_SOURCE_VERSION_PAYLOAD_CONFLICT"


@pytest.mark.asyncio
async def test_executor_uses_atomic_evidence_record_outcome_without_prevalidation_transaction() -> None:
    operation = QUERY_OPERATIONS[14]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=RESULT_FIXTURES[operation.identity])

    class AtomicConflictWriter(EvidenceWriter):
        async def record(self, **kwargs):  # type: ignore[no-untyped-def]
            from src.app.wms_integration.query_evidence import WmsQueryEvidenceRecord

            self.records.append(kwargs)
            return WmsQueryEvidenceRecord(
                evidence_key="evidence:atomic-conflict",
                outcome=QueryContractFailure(
                    reason_code="WMS_SOURCE_VERSION_PAYLOAD_CONFLICT",
                    message="concurrent source version conflict",
                ),
            )

    writer = AtomicConflictWriter()
    client = httpx.AsyncClient(transport=TrackingTransport(handler), trust_env=False)
    executor = _executor(
        operation,
        compiled_profile=build_compiled_provider_profile(),
        client=client,
        evidence_writer=writer,
        credential_provider=StaticCredentialProvider(b"unused"),
    )
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    try:
        outcome = await executor.execute(request)
    finally:
        await client.aclose()

    assert isinstance(outcome, QueryContractFailure)
    assert outcome.reason_code == "WMS_SOURCE_VERSION_PAYLOAD_CONFLICT"
    assert outcome.evidence_key == "evidence:atomic-conflict"
    assert len(writer.records) == 1


@pytest.mark.asyncio
async def test_data_lane_runtime_owns_one_client_for_all_19_query_operations() -> None:
    compiled_profile = build_compiled_provider_profile()
    catalog = build_wms_provider_catalog(compiled_profile)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(500)))
    runtime = WmsDataLaneQueryRuntime(
        compiled_profile=compiled_profile,
        catalog=catalog,
        client=client,
        credential_provider=StaticCredentialProvider(b"unused"),
        evidence_writer=EvidenceWriter(),
    )

    assert runtime.operation_identities == tuple(operation.identity for operation in QUERY_OPERATIONS)
    assert len({id(runtime.executor(operation.identity)._client) for operation in QUERY_OPERATIONS}) == 1

    await runtime.aclose()

    assert client.is_closed


@pytest.mark.parametrize(
    ("previous_version", "previous_hash", "version", "response_hash", "expected"),
    [
        ("2", "same", "1", "new", "WMS_SOURCE_VERSION_REGRESSION"),
        ("2", "old", "2", "new", "WMS_SOURCE_VERSION_PAYLOAD_CONFLICT"),
        ("opaque-a", "old", "opaque-b", "new", "WMS_SOURCE_VERSION_NOT_COMPARABLE"),
        ("2", "old", "3", "new", None),
    ],
)
def test_source_version_history_classification(
    previous_version: str,
    previous_hash: str,
    version: str,
    response_hash: str,
    expected: str | None,
) -> None:
    assert (
        classify_source_version(
            previous_version=previous_version,
            previous_response_hash=previous_hash,
            source_version=version,
            response_hash=response_hash,
        )
        == expected
    )


class _UnknownQueryRequest(BaseModel):
    value: str = "unknown"


@pytest.mark.asyncio
async def test_data_lane_runtime_rejects_mismatched_snapshot_and_unknown_lookups(monkeypatch) -> None:
    module = importlib.import_module("src.app.wms_integration.query_runtime")
    compiled_profile = build_compiled_provider_profile()
    other_profile = build_compiled_provider_profile()
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(500)))
    try:
        with pytest.raises(ValueError, match="compiled profile snapshot"):
            module.WmsDataLaneQueryRuntime(
                compiled_profile=compiled_profile,
                catalog=build_wms_provider_catalog(other_profile),
                client=client,
                credential_provider=StaticCredentialProvider(b"unused"),
                evidence_writer=EvidenceWriter(),
            )

        duplicate = (QUERY_OPERATIONS[0],) * 19
        monkeypatch.setattr(module, "QUERY_OPERATIONS", duplicate)
        with pytest.raises(RuntimeError, match="19 unique"):
            module.WmsDataLaneQueryRuntime(
                compiled_profile=compiled_profile,
                catalog=build_wms_provider_catalog(compiled_profile),
                client=client,
                credential_provider=StaticCredentialProvider(b"unused"),
                evidence_writer=EvidenceWriter(),
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_data_lane_runtime_execute_project_and_unknown_request_guards() -> None:
    compiled_profile = build_compiled_provider_profile()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=RESULT_FIXTURES[QUERY_OPERATIONS[0].identity])
        )
    )
    runtime = WmsDataLaneQueryRuntime(
        compiled_profile=compiled_profile,
        catalog=build_wms_provider_catalog(compiled_profile),
        client=client,
        credential_provider=StaticCredentialProvider(b"unused"),
        evidence_writer=EvidenceWriter(),
    )
    request = QUERY_OPERATIONS[0].request_model.model_validate(REQUEST_FIXTURES[QUERY_OPERATIONS[0].identity])
    try:
        assert isinstance(await runtime.execute(request), QuerySuccess)
        assert runtime.project(request).operation_identity == QUERY_OPERATIONS[0].identity
        with pytest.raises(LookupError, match="unknown"):
            runtime.executor("unknown")
        with pytest.raises(TypeError, match="unregistered"):
            await runtime.execute(_UnknownQueryRequest())
        with pytest.raises(TypeError, match="unregistered"):
            runtime.project(_UnknownQueryRequest())
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_data_lane_runtime_owner_binding_guards_and_close(monkeypatch) -> None:
    module = importlib.import_module("src.app.wms_integration.query_runtime")
    compiled_profile = build_compiled_provider_profile()

    def build_runtime() -> WmsDataLaneQueryRuntime:
        return WmsDataLaneQueryRuntime(
            compiled_profile=compiled_profile,
            catalog=build_wms_provider_catalog(compiled_profile),
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
            credential_provider=StaticCredentialProvider(b"unused"),
            evidence_writer=EvidenceWriter(),
        )

    first = build_runtime()
    second = build_runtime()
    module._active_runtime = None
    module._active_loop = None
    try:
        assert module.get_wms_data_lane_query_runtime() is None
        module.bind_wms_data_lane_query_runtime(first)
        module.bind_wms_data_lane_query_runtime(first)
        assert module.get_wms_data_lane_query_runtime() is first
        with pytest.raises(RuntimeError, match="already bound"):
            module.bind_wms_data_lane_query_runtime(second)
        with pytest.raises(RuntimeError, match="different"):
            module.unbind_wms_data_lane_query_runtime(second)

        owner_loop = module._active_loop
        monkeypatch.setattr(module.asyncio, "get_running_loop", lambda: object())
        with pytest.raises(RuntimeError, match="event loop mismatch"):
            module.get_wms_data_lane_query_runtime()
        monkeypatch.setattr(module.asyncio, "get_running_loop", lambda: owner_loop)

        await module.close_bound_wms_data_lane_query_runtime()
        assert first._client.is_closed
        await module.close_bound_wms_data_lane_query_runtime()
    finally:
        module._active_runtime = None
        module._active_loop = None
        if not first._client.is_closed:
            await first.aclose()
        await second.aclose()


@pytest.mark.asyncio
async def test_build_data_lane_runtime_assembles_production_dependencies(monkeypatch) -> None:
    runtime_module = importlib.import_module("src.app.wms_integration.query_runtime")
    credentials_module = importlib.import_module("src.app.sys.external_http_credentials")
    evidence_module = importlib.import_module("src.app.wms_integration.query_evidence")
    startup_module = importlib.import_module("src.app.wms_integration.provider_startup")
    compiled_profile = build_compiled_provider_profile()
    credential_provider = StaticCredentialProvider(b"unused")
    captured: dict[str, object] = {}

    class FakeEvidenceWriter:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        credentials_module,
        "build_environment_external_http_credential_provider",
        lambda *, settings_source: credential_provider,
    )
    monkeypatch.setattr(evidence_module, "WmsRegistryCallEvidenceWriter", FakeEvidenceWriter)
    startup = startup_module.WmsProviderStartupConfiguration(
        compiled_profile=compiled_profile,
        catalog=build_wms_provider_catalog(compiled_profile),
        wes_readiness=object(),
        fulfillment_readiness=object(),
    )

    runtime = runtime_module.build_wms_data_lane_query_runtime(startup, settings_source=object())
    try:
        assert len(runtime.operation_identities) == 19
        assert set(captured) == {"session_factory", "evidence_service", "breaker_service"}
    finally:
        await runtime.aclose()
