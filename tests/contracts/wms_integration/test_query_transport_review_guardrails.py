"""T3 评审回归：pinned binding、认证、失败分类与统一 transport 生命周期。"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import importlib
import inspect
import re
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, get_args

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError

from src.app.contracts.external_contract_profile_catalog import WMS_MATERIAL_FLOW_SANDBOX_PROFILE
from src.app.runtime.capability_port_registry import CapabilityPortRegistry, RuntimeCapabilityContext
from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)
from src.app.runtime.system_capabilities.gateway import SystemCapabilityGateway
from src.app.runtime.system_capabilities.wms.inventory.query_inventory.contract import CONTRACT
from src.app.wms_integration.adapters.query_inventory_operation_adapter import InventoryQueryOperationAdapter
from src.app.wms_integration.models import WmsOperationName
from src.app.wms_integration.ports.query_inventory_operation import InventoryQueryOperationRequest
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QueryTechnicalFailure,
)
from src.app.wms_integration.runtime_factory import build_inventory_query_port_factory
from src.app.wms_integration.services.query_transport import WmsQueryCallPermit, WmsQueryTransportExecutor

if TYPE_CHECKING:
    from collections.abc import Mapping

REPO_ROOT = Path(__file__).resolve().parents[3]


def _query_transport_contracts():
    transport_module = importlib.import_module("src.app.wms_integration.services.query_transport")
    provider_catalog = importlib.import_module("src.app.runtime.system_capabilities.wms.provider_catalog")
    assert hasattr(transport_module, "WmsBoundQueryEndpoint")
    assert hasattr(provider_catalog, "resolve_wms_operation_binding")
    return transport_module.WmsBoundQueryEndpoint, provider_catalog.resolve_wms_operation_binding


class _EvidenceWriter:
    def __init__(self) -> None:
        self.snapshots: list[Mapping[str, object]] = []

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
        self.snapshots.append(request_snapshot)
        return "evidence-1"


class _CredentialProvider:
    def __init__(self, secret: bytes) -> None:
        self.secret = secret
        self.references: list[str] = []

    def resolve(self, credential_reference: str) -> bytes:
        self.references.append(credential_reference)
        return self.secret


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def __aiter__(self):
        yield self._content


def _adapter(
    handler,
    *,
    allowed_content_encodings: tuple[str, ...] = ("identity", "gzip", "deflate"),
) -> InventoryQueryOperationAdapter:
    WmsBoundQueryEndpoint, resolve_wms_operation_binding = _query_transport_contracts()
    binding = resolve_wms_operation_binding(
        profile_identity="wms.2026-07-06.material-flow.production",
        operation_identity=CONTRACT.identity,
    )
    binding = binding.model_copy(
        update={
            "operation": binding.operation.model_copy(
                update={
                    "budget": binding.operation.budget.model_copy(
                        update={"allowed_content_encodings": allowed_content_encodings}
                    )
                }
            )
        }
    )
    endpoint = WmsBoundQueryEndpoint(binding=binding, base_url="https://wms.test")
    executor = WmsQueryTransportExecutor(
        endpoint=endpoint,
        transport=httpx.MockTransport(handler),
        evidence_writer=_EvidenceWriter(),
        credential_provider=_CredentialProvider(b"test-only-hmac-secret"),
        now=lambda: datetime(2026, 7, 21, tzinfo=UTC),
        nonce_factory=lambda: "nonce-1",
    )
    return InventoryQueryOperationAdapter(executor=executor)


@pytest.mark.asyncio
async def test_production_query_consumes_pinned_binding_and_signs_exact_http_request() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"items": [], "source_version": "WMS-42"})

    WmsBoundQueryEndpoint, resolve_wms_operation_binding = _query_transport_contracts()
    secret = b"never-persist-this-secret"
    credential_provider = _CredentialProvider(secret)
    evidence_writer = _EvidenceWriter()
    binding = resolve_wms_operation_binding(
        profile_identity="wms.2026-07-06.material-flow.production",
        operation_identity=CONTRACT.identity,
    )
    executor = WmsQueryTransportExecutor(
        endpoint=WmsBoundQueryEndpoint(binding=binding, base_url="https://wms.test"),
        transport=httpx.MockTransport(handler),
        evidence_writer=evidence_writer,
        credential_provider=credential_provider,
        now=lambda: datetime(2026, 7, 21, tzinfo=UTC),
        nonce_factory=lambda: "nonce-1",
    )

    await InventoryQueryOperationAdapter(executor=executor).execute(
        InventoryQueryOperationRequest(material_code="MAT-001")
    )

    request = captured[0]
    credential_reference = binding.outbound_auth.credential_reference
    assert credential_reference is not None
    assert credential_provider.references == [credential_reference]
    assert request.headers["X-WMS-Credential-Reference"] == credential_reference
    body_hash = hashlib.sha256(request.content).hexdigest()
    timestamp = str(int(datetime(2026, 7, 21, tzinfo=UTC).timestamp()))
    assert request.headers["X-WMS-Timestamp"] == timestamp
    canonical = "\n".join(
        (
            request.method,
            request.url.raw_path.decode("ascii"),
            timestamp,
            "nonce-1",
            body_hash,
        )
    )
    expected = hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    assert request.headers["X-WMS-Signature"] == expected
    assert request.headers["X-WMS-Signature-Algorithm"] == "HMAC_SHA256"
    assert secret.decode() not in repr(request.headers)
    assert secret.decode() not in repr(evidence_writer.snapshots)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "content", "expected_code", "retryable"),
    [
        (429, b"", "WMS_RATE_LIMITED", True),
        (429, b"not-json", "WMS_RATE_LIMITED", True),
        (503, b"", "WMS_UNAVAILABLE", True),
        (503, b"not-json", "WMS_UNAVAILABLE", True),
        (408, b"", "WMS_PROVIDER_TIMEOUT", True),
        (401, b"not-json", "WMS_AUTHENTICATION_FAILED", False),
        (403, b"", "WMS_AUTHORIZATION_FAILED", False),
        (400, b"not-json", "WMS_PROVIDER_CLIENT_ERROR", False),
    ],
)
async def test_http_status_is_classified_before_optional_body_parsing(
    status: int,
    content: bytes,
    expected_code: str,
    retryable: bool,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=content)

    outcome = await _adapter(handler).execute(InventoryQueryOperationRequest(material_code="MAT-001"))

    assert isinstance(outcome, QueryTechnicalFailure)
    assert outcome.reason_code == expected_code
    assert outcome.retryable is retryable


@pytest.mark.asyncio
async def test_only_explicit_provider_business_reject_is_classified_as_business() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "classification": "BUSINESS_REJECT",
                "reason_code": "INSUFFICIENT_STOCK",
                "message": "stock is insufficient",
            },
        )

    outcome = await _adapter(handler).execute(InventoryQueryOperationRequest(material_code="MAT-001"))

    assert isinstance(outcome, QueryBusinessReject)
    assert outcome.reason_code == "INSUFFICIENT_STOCK"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("encoding", "encoded"),
    [
        ("gzip", gzip.compress(b'{"items":[]}')[:-4]),
        ("gzip", gzip.compress(b'{"items":[]}') + b"trailing"),
        ("deflate", zlib.compress(b'{"items":[]}')[:-2]),
        ("deflate", b"not-deflate"),
    ],
)
async def test_truncated_trailing_or_corrupt_content_encoding_never_succeeds(
    encoding: str,
    encoded: bytes,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-encoding": encoding},
            stream=_ChunkStream(encoded),
        )

    outcome = await _adapter(handler).execute(InventoryQueryOperationRequest(material_code="MAT-001"))

    assert isinstance(outcome, QueryContractFailure)
    assert outcome.reason_code == "WMS_CONTENT_ENCODING_INVALID"


def test_sandbox_factory_reuses_closed_transport_executor_and_pinned_profile() -> None:
    factory = build_inventory_query_port_factory(
        provider_profile=WMS_MATERIAL_FLOW_SANDBOX_PROFILE,
        simulation=True,
        sandbox_rows_provider=lambda **_kwargs: [],
    )

    port = factory()

    assert isinstance(port, InventoryQueryOperationAdapter)
    assert isinstance(port._executor, WmsQueryTransportExecutor)
    assert port._executor.binding.profile.identity == WMS_MATERIAL_FLOW_SANDBOX_PROFILE.identity
    runtime_factory_source = inspect.getsource(build_inventory_query_port_factory)
    assert "before_call(" not in runtime_factory_source
    assert "evidence_writer.record(" not in runtime_factory_source


def test_legacy_query_dto_endpoint_and_sandbox_lifecycle_are_absent_everywhere() -> None:
    forbidden = (
        "Query" + "InventoryRequest",
        "Query" + "InventoryResponse",
        "Wms" + "InventoryItem",
        "Sandbox" + "InventoryQueryOperationPort",
    )
    violations: list[str] = []
    for root_name in ("src", "tests", "docs"):
        for path in (REPO_ROOT / root_name).rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".md", ".csv"}:
                continue
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if re.search(rf"\b{re.escape(token)}\b", text):
                    violations.append(f"{path.relative_to(REPO_ROOT)}:{token}")
    assert violations == []

    endpoint_path = REPO_ROOT / "src/app/wms_integration/services/endpoint_config.py"
    assert not endpoint_path.exists()
    legacy_query_entry = "query" + "_inventory"
    assert legacy_query_entry not in get_args(WmsOperationName)


class _ExceptionInput(BaseModel):
    value: int


class _ExceptionOutput(BaseModel):
    value: int


class _ExceptionHandler:
    error: Exception

    async def __call__(self, _request: _ExceptionInput) -> object:
        raise self.error


def _exception_gateway(error: Exception) -> SystemCapabilityGateway:
    _ExceptionHandler.error = error
    definition = SystemCapabilityDefinition(
        capability_key="wms.review.query",
        contract_version="v1",
        mode=SystemCapabilityMode.QUERY,
        input_model=_ExceptionInput,
        output_model=_ExceptionOutput,
        handler_factory=_ExceptionHandler,
        required_ports=(),
        admission="provider-contract",
        timeout_seconds=1,
        completion_mode=EffectCompletionMode.LOCAL_TRANSACTIONAL,
        audit_policy="metadata",
    )
    return SystemCapabilityGateway(
        attempt_id="review-exception",
        definitions={(definition.capability_key, definition.contract_version): definition},
        allowed_capabilities=frozenset({(definition.capability_key, definition.contract_version)}),
        context=RuntimeCapabilityContext(CapabilityPortRegistry()),
        admission_profile="provider-contract",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        DBAPIError("SELECT 1", {}, RuntimeError("db unavailable")),
        RuntimeError("evidence invariant escaped closed executor"),
    ],
)
async def test_gateway_does_not_convert_escaped_query_failures_to_retryable_unknown(error: Exception) -> None:
    with pytest.raises(type(error)):
        await _exception_gateway(error).execute("wms.review.query", "v1", {"value": 1})
