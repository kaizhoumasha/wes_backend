"""显式连接 Docker Compose mock_wms 的北向 live 验收。

此目录不进入默认快速回归；运行者必须显式提供真实 base URL，测试不会用 skip 冒充 PASS。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest

from scripts.verify_wms_northbound_feasibility import _status_headers, _submit_headers, run_probe
from src.app.runtime.capabilities.material_flow.contracts.rough_sorter_inventory_admission import (
    RoughSorterBindingSnapshot,
    RoughSorterInventoryAdmissionPolicyInput,
    RoughSorterInventoryQueryOutcomeKind,
    RoughSorterInventoryQuerySnapshot,
)
from src.app.runtime.capabilities.material_flow.rough_sorter_inventory_admission_policy import (
    decide_rough_sorter_inventory_admission,
)
from src.app.runtime.system_capabilities.wms.inventory.query_inventory.contract import CONTRACT
from src.app.runtime.system_capabilities.wms.provider_catalog import (
    build_wms_provider_catalog,
    resolve_wms_operation_binding,
)
from src.app.sys.canonical_dispatch import canonical_json_bytes
from src.app.sys.external_http_credentials import EXTERNAL_HTTP_CREDENTIAL_ENV_BY_REFERENCE
from src.app.wms_integration.adapters import InventoryQueryOperationAdapter
from src.app.wms_integration.ports.query_inventory_operation import (
    OPERATION_IDENTITY,
    InventoryQueryOperationRequest,
)
from src.app.wms_integration.ports.query_outcome import QuerySuccess, QueryTechnicalFailure
from src.app.wms_integration.services.query_transport import (
    WmsBoundQueryEndpoint,
    WmsQueryCallPermit,
    WmsQueryTransportExecutor,
)
from src.core.conf import settings
from tests.contracts.wms_integration.provider_profile_support import (
    build_compiled_provider_profile,
    build_hmac_provider_profile_payload,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_COMPOSE_FILE = BACKEND_ROOT / "docker-compose.wms-acceptance.yml"
ACCEPTANCE_CONTRACT_ENV = {
    "WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS": "9",
    "WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS": "2",
    "WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES": "4096",
    "WMS_EFFECT_SUBMIT_TIMEOUT_SECONDS": "30",
    "WMS_EFFECT_STATUS_TIMEOUT_SECONDS": "2",
}


def _acceptance_wms_image() -> str:
    return os.getenv("MOCK_WMS_ACCEPTANCE_IMAGE", "wes-mock:wms").strip() or "wes-mock:wms"


def _live_connection() -> tuple[str, float]:
    base_url = os.getenv("WMS_NORTHBOUND_LIVE_BASE_URL", "").strip()
    if not base_url:
        pytest.fail("WMS_NORTHBOUND_LIVE_BASE_URL is required for explicit live acceptance")
    return base_url, float(os.getenv("WMS_NORTHBOUND_LIVE_TIMEOUT_SECONDS", "0.25"))


def _compose_command(project_name: str, healthcheck_timing_override: Path, *args: str) -> list[str]:
    docker = shutil.which("docker")
    assert docker is not None
    return [
        docker,
        "compose",
        "--project-name",
        project_name,
        "-f",
        str(ACCEPTANCE_COMPOSE_FILE),
        "-f",
        str(healthcheck_timing_override),
        *args,
    ]


def _wait_for_acceptance_health(
    project_name: str, healthcheck_timing_override: Path, expected_status: str, timeout_seconds: float = 10
) -> str:
    deadline = time.monotonic() + timeout_seconds
    observed_status = "container-not-created"
    while time.monotonic() < deadline:
        container = subprocess.run(
            _compose_command(project_name, healthcheck_timing_override, "ps", "-q", "mock_wms_acceptance"),
            cwd=BACKEND_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        container_id = container.stdout.strip()
        if container.returncode == 0 and container_id:
            health = subprocess.run(
                [
                    shutil.which("docker") or "docker",
                    "inspect",
                    container_id,
                    "--format",
                    "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if health.returncode == 0:
                observed_status = health.stdout.strip()
                if observed_status == expected_status:
                    return observed_status
        time.sleep(0.5)
    return observed_status


def _recreate_acceptance_mock(
    project_name: str, healthcheck_timing_override: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _compose_command(
            project_name, healthcheck_timing_override, "up", "-d", "--force-recreate", "mock_wms_acceptance"
        ),
        cwd=BACKEND_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
    )


def test_acceptance_healthcheck_fails_fast_for_invalid_contract_configuration(tmp_path: Path) -> None:
    """独立验收项目必须以合同端点暴露空合同参数，而不是仅报告进程存活。"""
    docker = shutil.which("docker")
    assert docker is not None
    acceptance_image = _acceptance_wms_image()
    image = subprocess.run(
        [docker, "image", "inspect", acceptance_image],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert image.returncode == 0, image.stderr

    project_name = f"wes_acceptance_health_{uuid.uuid4().hex}"
    healthcheck_timing_override = tmp_path / "acceptance-healthcheck-timing.yml"
    healthcheck_timing_override.write_text(
        """services:
  mock_wms_acceptance:
    healthcheck:
      interval: 200ms
      timeout: 100ms
      retries: 3
      start_period: 100ms
""",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "DOCKER_HOST_BIND_IP": "127.0.0.1",
        "MOCK_WMS_ACCEPTANCE_PORT": "0",
        "MOCK_WMS_ACCEPTANCE_IMAGE": acceptance_image,
        **ACCEPTANCE_CONTRACT_ENV,
    }
    try:
        invalid_environment = {**environment, "WMS_EFFECT_SUBMIT_TIMEOUT_SECONDS": ""}
        invalid = _recreate_acceptance_mock(project_name, healthcheck_timing_override, invalid_environment)
        assert invalid.returncode == 0, invalid.stdout + invalid.stderr
        assert _wait_for_acceptance_health(project_name, healthcheck_timing_override, "unhealthy") == "unhealthy"

        cleaned = subprocess.run(
            _compose_command(project_name, healthcheck_timing_override, "down", "--remove-orphans"),
            cwd=BACKEND_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=environment,
        )
        assert cleaned.returncode == 0, cleaned.stdout + cleaned.stderr

        valid = _recreate_acceptance_mock(project_name, healthcheck_timing_override, environment)
        assert valid.returncode == 0, valid.stdout + valid.stderr
        assert _wait_for_acceptance_health(project_name, healthcheck_timing_override, "healthy") == "healthy"
    finally:
        subprocess.run(
            _compose_command(project_name, healthcheck_timing_override, "down", "--remove-orphans"),
            cwd=BACKEND_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=environment,
        )


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


def _live_compiled_profile(*, base_url: str, credential_reference: str):
    payload = build_hmac_provider_profile_payload()
    payload["server_url"] = base_url
    payload["effect_status_path"] = "/northbound/operations/status"
    payload["outbound_auth"]["credential_reference"] = credential_reference
    return build_compiled_provider_profile(payload)


class _LiveQueryEvidenceWriter:
    async def before_call(self, *, operation_identity: str, target_code: str) -> WmsQueryCallPermit:
        return WmsQueryCallPermit(allowed=True)

    async def record(self, **_kwargs) -> str:
        return "evidence:docker-wms:rough-sorter-001"


class _LiveQueryCredentialProvider:
    def __init__(self, *, credential_reference: str, secret: bytes) -> None:
        self._credential_reference = credential_reference
        self._secret = secret

    def resolve(self, credential_reference: str) -> bytes:
        if credential_reference != self._credential_reference:
            raise ValueError("unexpected live WMS credential reference")
        return self._secret


def _live_inventory_adapter(*, base_url: str, binding, credential_reference: str, secret: bytes):
    return InventoryQueryOperationAdapter(
        executor=WmsQueryTransportExecutor(
            endpoint=WmsBoundQueryEndpoint(binding=binding, base_url=f"{base_url}/api/wms"),
            transport=None,
            evidence_writer=_LiveQueryEvidenceWriter(),
            credential_provider=_LiveQueryCredentialProvider(
                credential_reference=credential_reference,
                secret=secret,
            ),
        )
    )


@pytest.mark.asyncio
async def test_compose_mock_wms_northbound_live_contract() -> None:
    base_url, timeout_seconds = _live_connection()

    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_seconds),
        trust_env=False,
    ) as client:
        credential_reference, _secret = await _reset_and_active_credential(client)
        compiled_profile = _live_compiled_profile(
            base_url=base_url,
            credential_reference=credential_reference,
        )
        report = await run_probe(
            client,
            compiled_profile=compiled_profile,
            request_timeout_seconds=timeout_seconds,
        )

    assert report.passed is True, report.cases


@pytest.mark.asyncio
async def test_compose_mock_wms_inventory_query_matches_production_adapter_over_tcp() -> None:
    base_url, _timeout_seconds = _live_connection()
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(_timeout_seconds),
        trust_env=False,
    ) as client:
        credential_reference, secret = await _reset_and_active_credential(client)
    compiled_profile = _live_compiled_profile(base_url=base_url, credential_reference=credential_reference)
    catalog = build_wms_provider_catalog(compiled_profile)
    binding = resolve_wms_operation_binding(
        catalog=catalog,
        profile_identity=catalog.profile_identity,
        operation_identity=CONTRACT.identity,
    )
    assert binding.outbound_auth.credential_reference == credential_reference
    adapter = _live_inventory_adapter(
        base_url=base_url,
        binding=binding,
        credential_reference=credential_reference,
        secret=secret,
    )

    outcome = await adapter.execute(
        InventoryQueryOperationRequest(
            material_code="CAP001",
            lot_no="LOT-A",
            warehouse_code="WH-IT",
            owner_code="OWNER-IT",
        )
    )

    assert isinstance(outcome, QuerySuccess), outcome
    assert outcome.evidence_key == "evidence:docker-wms:rough-sorter-001"
    assert outcome.value.source_version == "mock-inventory-v1"
    assert len(outcome.value.items) == 1
    item = outcome.value.items[0]
    assert item.material_code == "CAP001"
    assert item.lot_no == "LOT-A"
    assert item.warehouse_code == "WH-IT"
    assert item.owner_code == "OWNER-IT"
    assert item.available_quantity > 0

    wrong_dimensions = await adapter.execute(
        InventoryQueryOperationRequest(
            material_code="CAP001",
            lot_no="LOT-A",
            warehouse_code="WH-IT",
            owner_code="OWNER-WRONG",
        )
    )
    assert isinstance(wrong_dimensions, QuerySuccess), wrong_dimensions
    assert wrong_dimensions.value.items == ()
    assert wrong_dimensions.value.source_version == "mock-inventory-v1"

    decision = decide_rough_sorter_inventory_admission(
        RoughSorterInventoryAdmissionPolicyInput(
            material_code="CAP001",
            lot_no="LOT-A",
            warehouse_code="WH-IT",
            owner_code="OWNER-IT",
            binding_snapshot=RoughSorterBindingSnapshot(
                binding_id=1,
                binding_version=1,
                profile_identity=binding.profile.identity,
                plugin_config_hash="a" * 64,
                generated_index_digest="b" * 64,
            ),
            supported_profile_identities=(binding.profile.identity,),
            source_operation=OPERATION_IDENTITY,
            query_snapshot=RoughSorterInventoryQuerySnapshot(
                outcome_kind=RoughSorterInventoryQueryOutcomeKind.SUCCESS,
                result=outcome.value,
                evidence_key=outcome.evidence_key,
            ),
        )
    )
    assert decision.decision == "ADMIT"
    assert decision.reason_code == "WMS_ADMITTED"
    assert decision.provenance.source.evidence_key == outcome.evidence_key
    assert decision.provenance.source.source_version == "mock-inventory-v1"


@pytest.mark.asyncio
async def test_compose_mock_wms_inventory_query_hmac_fails_closed_over_tcp() -> None:
    base_url, timeout_seconds = _live_connection()
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_seconds),
        trust_env=False,
    ) as client:
        credential_reference, secret = await _reset_and_active_credential(client)
        unsigned = await client.get(
            "/api/wms/inventory/query",
            params={
                "material_id": "CAP001",
                "lot_no": "LOT-A",
                "warehouse_code": "WH-IT",
                "owner_code": "OWNER-IT",
            },
        )
    compiled_profile = _live_compiled_profile(base_url=base_url, credential_reference=credential_reference)
    catalog = build_wms_provider_catalog(compiled_profile)
    binding = resolve_wms_operation_binding(
        catalog=catalog,
        profile_identity=catalog.profile_identity,
        operation_identity=CONTRACT.identity,
    )
    invalid_auth = await _live_inventory_adapter(
        base_url=base_url,
        binding=binding,
        credential_reference=credential_reference,
        secret=secret + b"-wrong",
    ).execute(
        InventoryQueryOperationRequest(
            material_code="CAP001",
            lot_no="LOT-A",
            warehouse_code="WH-IT",
            owner_code="OWNER-IT",
        )
    )

    assert unsigned.status_code == 401
    assert unsigned.json() == {"code": "MISSING_OR_INVALID_AUTH_HEADER"}
    assert secret.decode("utf-8") not in unsigned.text
    assert isinstance(invalid_auth, QueryTechnicalFailure), invalid_auth
    assert invalid_auth.reason_code == "WMS_AUTHENTICATION_FAILED"
    assert invalid_auth.retryable is False


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
        [docker, "compose", "-f", str(ACCEPTANCE_COMPOSE_FILE), "logs", "--no-color", "mock_wms_acceptance"],
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
        [docker, "compose", "-f", str(ACCEPTANCE_COMPOSE_FILE), "ps", "-q", "mock_wms_acceptance"],
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
