"""显式连接 Docker Compose mock_wms 的北向 live 验收。

此目录不进入默认快速回归；运行者必须显式提供真实 base URL，测试不会用 skip 冒充 PASS。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest

from scripts.verify_wms_northbound_feasibility import _status_headers, _submit_headers, run_probe
from src.app.sys.canonical_dispatch import canonical_json_bytes
from src.app.sys.external_http_credentials import EXTERNAL_HTTP_CREDENTIAL_ENV_BY_REFERENCE
from src.core.conf import settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_COMPOSE_FILE = BACKEND_ROOT / "docker-compose.wms-acceptance.yml"


def _acceptance_wms_image() -> str:
    return os.getenv("MOCK_WMS_ACCEPTANCE_IMAGE", "wes-mock:wms").strip() or "wes-mock:wms"


def _live_connection() -> tuple[str, float]:
    base_url = os.getenv("WMS_NORTHBOUND_LIVE_BASE_URL", "").strip()
    if not base_url:
        pytest.fail("WMS_NORTHBOUND_LIVE_BASE_URL is required for explicit live acceptance")
    return base_url, float(os.getenv("WMS_NORTHBOUND_LIVE_TIMEOUT_SECONDS", "0.25"))


def test_acceptance_image_override_selects_runtime_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    image = "registry.example/wes-mock:wms-review"
    monkeypatch.setenv("MOCK_WMS_ACCEPTANCE_IMAGE", image)

    assert _acceptance_wms_image() == image


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
        request_headers = [
            _submit_headers(
                secret=secret,
                credential_reference=credential_reference,
                path=path,
                body=body,
                operation_identity=operation_identity,
                key=idempotency_key,
            )
            for _ in range(8)
        ]
        responses = await asyncio.gather(
            *(client.post(path, content=body, headers=headers) for headers in request_headers)
        )
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


def test_compose_mock_wms_live_logs_redact_query_keys_and_expected_disconnects() -> None:
    docker = shutil.which("docker")
    assert docker is not None

    completed = subprocess.run(
        [docker, "compose", "-f", str(ACCEPTANCE_COMPOSE_FILE), "logs", "--no-color", "mock_wms"],
        cwd=BACKEND_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    logs = completed.stdout + completed.stderr

    assert completed.returncode == 0, logs
    assert "idempotency_key=" not in logs
    assert "ClientDisconnect" not in logs
    assert "Exception in ASGI application" not in logs


def test_live_acceptance_runs_the_selected_wms_image_without_source_mounts() -> None:
    docker = shutil.which("docker")
    assert docker is not None
    acceptance_image = _acceptance_wms_image()
    expected_image = subprocess.run(
        [docker, "image", "inspect", acceptance_image, "--format", "{{.Id}}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert expected_image.returncode == 0, expected_image.stderr

    container = subprocess.run(
        [docker, "compose", "-f", str(ACCEPTANCE_COMPOSE_FILE), "ps", "-q", "mock_wms"],
        cwd=BACKEND_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    container_id = container.stdout.strip()
    assert container.returncode == 0 and container_id, container.stderr

    inspected = subprocess.run(
        [docker, "inspect", container_id, "--format", "{{.Image}}|{{json .Mounts}}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    image_id, mounts = inspected.stdout.strip().split("|", maxsplit=1)

    assert inspected.returncode == 0, inspected.stderr
    assert image_id == expected_image.stdout.strip()
    assert '"/app/tests/mock"' not in mounts
