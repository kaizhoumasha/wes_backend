"""显式连接 Docker Compose mock_wms 的北向 live 验收。

此目录不进入默认快速回归；运行者必须显式提供真实 base URL，测试不会用 skip 冒充 PASS。
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlencode

import httpx
import pytest

from scripts.verify_wms_northbound_feasibility import _status_headers, _submit_headers, run_probe
from src.app.sys.canonical_dispatch import canonical_json_bytes
from src.app.sys.external_http_credentials import EXTERNAL_HTTP_CREDENTIAL_ENV_BY_REFERENCE
from src.core.conf import settings


def _live_connection() -> tuple[str, float]:
    base_url = os.getenv("WMS_NORTHBOUND_LIVE_BASE_URL", "").strip()
    if not base_url:
        pytest.fail("WMS_NORTHBOUND_LIVE_BASE_URL is required for explicit live acceptance")
    return base_url, float(os.getenv("WMS_NORTHBOUND_LIVE_TIMEOUT_SECONDS", "0.25"))


async def _reset_and_active_credential(client: httpx.AsyncClient) -> tuple[str, bytes]:
    reset = await client.post("/debug/reset")
    assert reset.status_code == 200
    contract = await client.get("/northbound/contract")
    assert contract.status_code == 200
    credential_reference = str(contract.json()["credential_reference"])
    secret_env = EXTERNAL_HTTP_CREDENTIAL_ENV_BY_REFERENCE[credential_reference]
    configured_secret = os.getenv(secret_env) or getattr(settings, secret_env, "")
    assert configured_secret
    return credential_reference, configured_secret.encode("utf-8")


@pytest.mark.asyncio
async def test_compose_mock_wms_northbound_live_contract() -> None:
    base_url, timeout_seconds = _live_connection()

    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_seconds),
        trust_env=False,
    ) as client:
        await _reset_and_active_credential(client)
        report = await run_probe(client, request_timeout_seconds=timeout_seconds)

    assert report.passed is True, report.cases


@pytest.mark.asyncio
async def test_compose_mock_wms_concurrent_identical_replay_over_tcp() -> None:
    base_url, timeout_seconds = _live_connection()
    operation_identity = "wms.fulfillment.notify_pkg_binding@v1"
    idempotency_key = "live-concurrent-replay-001"
    path = "/api/wms/fulfillment/package-binding"
    body = canonical_json_bytes(
        {
            "dispatch_key": "live-dispatch-concurrent-replay-001",
            "package_id": "live-package-concurrent-replay-001",
            "pallet_id": "live-pallet-concurrent-replay-001",
            "station_code": "live-station-concurrent-replay-001",
        }
    )

    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_seconds),
        trust_env=False,
    ) as client:
        credential_reference, secret = await _reset_and_active_credential(client)
        headers = _submit_headers(
            secret=secret,
            credential_reference=credential_reference,
            path=path,
            body=body,
            operation_identity=operation_identity,
            key=idempotency_key,
        )
        responses = await asyncio.gather(*(client.post(path, content=body, headers=headers) for _ in range(8)))
        effects = await client.get(
            "/debug/northbound/effects",
            params={"operation_identity": operation_identity, "idempotency_key": idempotency_key},
        )

    assert [response.status_code for response in responses].count(202) == 1
    assert [response.status_code for response in responses].count(409) == 7
    assert effects.status_code == 200
    assert effects.json()["effect_count"] == 1


@pytest.mark.asyncio
async def test_compose_mock_wms_concurrent_fault_claim_over_tcp() -> None:
    base_url, timeout_seconds = _live_connection()
    operation_identity = "wms.inventory.confirm_inbound@v1"
    idempotency_key = "live-concurrent-fault-claim-001"
    status_path = "/northbound/operations/status?" + urlencode(
        (("operation_identity", operation_identity), ("idempotency_key", idempotency_key))
    )

    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_seconds),
        trust_env=False,
    ) as client:
        credential_reference, secret = await _reset_and_active_credential(client)
        configured = await client.post(
            "/debug/northbound/faults",
            json={
                "status": 503,
                "target_path": "/northbound/operations/status",
                "method": "GET",
                "operation_identity": operation_identity,
                "delay": 0.05,
            },
        )
        headers = _status_headers(
            secret=secret,
            credential_reference=credential_reference,
            raw_path=status_path,
        )
        responses = await asyncio.gather(
            client.get(status_path, headers=headers),
            client.get(status_path, headers=headers),
        )

    assert configured.status_code == 200
    assert [response.status_code for response in responses].count(503) == 1
    assert [response.status_code for response in responses].count(200) == 1
    assert next(response for response in responses if response.status_code == 503).json() == {
        "code": "TEMPORARILY_UNAVAILABLE"
    }
