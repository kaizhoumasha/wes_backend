"""WMS inventory QUERY 在 runtime composition root 使用的 Port factory。"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx

from src.app.runtime.system_capabilities.wms.inventory.query_inventory.contract import CONTRACT
from src.app.runtime.system_capabilities.wms.provider_catalog import resolve_wms_operation_binding, wms_sync_base_url
from src.app.sys.external_http_credentials import AuditedVersionedCredentialProvider
from src.app.sys.external_http_credentials import (
    build_environment_external_http_credential_provider as build_environment_credential_provider,
)
from src.app.wms_integration.adapters import InventoryQueryOperationAdapter
from src.app.wms_integration.services.circuit_breaker_service import wms_circuit_breaker_service
from src.app.wms_integration.services.evidence_service import wms_call_evidence_service
from src.app.wms_integration.services.query_transport import (
    WmsBoundQueryEndpoint,
    WmsCallEvidenceQueryWriter,
    WmsCredentialProvider,
    WmsQueryEvidenceWriter,
    WmsQueryTransportExecutor,
)
from src.database.db import get_db_context

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.app.wms_integration.ports.query_inventory_operation import InventoryQueryOperationPort

    SandboxInventoryRowsProvider = Callable[..., list[dict[str, Any]]]


class _EphemeralCredentialProvider:
    def __init__(self, *, credential_reference: str, secret: bytes) -> None:
        self._credential_reference = credential_reference
        self._secret = secret

    def resolve(self, credential_reference: str) -> bytes:
        if credential_reference != self._credential_reference:
            raise LookupError("sandbox credential reference mismatch")
        return self._secret


def _sandbox_transport(
    *,
    rows_provider: SandboxInventoryRowsProvider,
    credential_reference: str,
    secret: bytes,
) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        if not _sandbox_signature_is_valid(
            request,
            credential_reference=credential_reference,
            secret=secret,
        ):
            return httpx.Response(401)
        rows = rows_provider(
            sku=request.url.params.get("material_id", ""),
            lot_no=request.url.params.get("lot_no"),
            warehouse_code=request.url.params.get("warehouse_code"),
            owner_code=request.url.params.get("owner_code"),
        )
        payload = {
            "items": [_json_ready_row(row) for row in rows],
            "source_version": "SANDBOX_WMS_INVENTORY_V1",
        }
        return httpx.Response(
            200,
            content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"content-type": "application/json"},
        )

    return httpx.MockTransport(handler)


def _sandbox_signature_is_valid(
    request: httpx.Request,
    *,
    credential_reference: str,
    secret: bytes,
) -> bool:
    if request.headers.get("X-WMS-Credential-Reference") != credential_reference:
        return False
    if request.headers.get("X-WMS-Signature-Algorithm") != "HMAC_SHA256":
        return False
    timestamp = request.headers.get("X-WMS-Timestamp", "")
    nonce = request.headers.get("X-WMS-Nonce", "")
    body_hash = hashlib.sha256(request.content).hexdigest()
    if request.headers.get("X-WMS-Content-SHA256") != body_hash:
        return False
    canonical = "\n".join((request.method, request.url.raw_path.decode("ascii"), timestamp, nonce, body_hash))
    expected = hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, request.headers.get("X-WMS-Signature", ""))


def _json_ready_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: str(value) if isinstance(value, Decimal) else value for key, value in row.items()}


def build_inventory_query_port_factory(
    *,
    provider_profile: Any,
    simulation: bool,
    sandbox_rows_provider: SandboxInventoryRowsProvider,
    transport: httpx.AsyncBaseTransport | None = None,
    credential_provider: WmsCredentialProvider | None = None,
    evidence_writer: WmsQueryEvidenceWriter | None = None,
    base_url: str | None = None,
) -> Callable[[], InventoryQueryOperationPort]:
    """以 attempt pin 的完整 profile 构建唯一 QUERY executor factory。"""

    profile_identity = getattr(provider_profile, "identity", None)
    if not isinstance(profile_identity, str) or not profile_identity:
        raise ValueError("pinned provider profile identity is required")
    binding = resolve_wms_operation_binding(
        profile_identity=profile_identity,
        operation_identity=CONTRACT.identity,
    )
    writer = evidence_writer or WmsCallEvidenceQueryWriter(
        session_factory=get_db_context,
        provider_profile_identity=profile_identity,
        evidence_service=wms_call_evidence_service,
        breaker_service=wms_circuit_breaker_service,
    )
    resolved_base_url = base_url
    resolved_transport = transport
    resolved_credential_provider = credential_provider
    if simulation:
        credential_reference = binding.outbound_auth.credential_reference
        if credential_reference is None:
            raise ValueError("sandbox QUERY binding requires credential reference")
        secret = secrets.token_bytes(32)
        resolved_base_url = resolved_base_url or "https://wms-sandbox.invalid"
        resolved_transport = resolved_transport or _sandbox_transport(
            rows_provider=sandbox_rows_provider,
            credential_reference=credential_reference,
            secret=secret,
        )
        resolved_credential_provider = resolved_credential_provider or _EphemeralCredentialProvider(
            credential_reference=credential_reference,
            secret=secret,
        )
    else:
        resolved_base_url = resolved_base_url or wms_sync_base_url()
        resolved_credential_provider = resolved_credential_provider or build_environment_credential_provider()
    if resolved_credential_provider is None:
        raise ValueError("WMS QUERY credential provider is required")
    if not isinstance(resolved_credential_provider, AuditedVersionedCredentialProvider):
        resolved_credential_provider = AuditedVersionedCredentialProvider(
            resolved_credential_provider,
            provider_kind="custom",
        )
    endpoint = WmsBoundQueryEndpoint(binding=binding, base_url=resolved_base_url)

    def factory() -> InventoryQueryOperationPort:
        return InventoryQueryOperationAdapter(
            executor=WmsQueryTransportExecutor(
                endpoint=endpoint,
                transport=resolved_transport,
                evidence_writer=writer,
                credential_provider=resolved_credential_provider,
            )
        )

    return factory


__all__ = ["build_inventory_query_port_factory"]
