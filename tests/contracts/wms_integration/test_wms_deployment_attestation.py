"""WMS 四角色 deployment attestation 的离线确定性合同。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from tests.contracts.wms_integration.provider_profile_support import (
    build_hmac_provider_profile_payload,
    write_provider_profile,
)


def _attestation_module():
    try:
        from src.app.wms_integration import deployment_attestation
    except ImportError:
        pytest.fail("WMS deployment attestation 领域模块尚未实现")
    return deployment_attestation


def _settings(profile_path) -> SimpleNamespace:
    return SimpleNamespace(
        APP_ENV="prod",
        WMS_PROVIDER_PROFILE_FILE=profile_path,
        WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED=False,
        WMS_EFFECT_ADMISSION_ENABLED=True,
        WMS_EFFECT_STATUS_TIMEOUT_SECONDS=5.0,
        WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES=65536,
        WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS=3600,
        WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS=20.0,
        WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS=1800,
        WES_EFFECT_NOT_FOUND_GRACE_SECONDS=30.0,
        WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS=300,
        WES_EFFECT_STATUS_SCAN_BATCH_SIZE=20,
        WES_EFFECT_STATUS_MAX_IN_FLIGHT=5,
        WES_EFFECT_STATUS_CLAIM_LEASE_SECONDS=120.0,
        WES_EFFECT_STATUS_MAX_QUERY_ATTEMPTS=8,
        WES_EFFECT_STATUS_INITIAL_BACKOFF_SECONDS=1.0,
        WES_EFFECT_STATUS_MAX_BACKOFF_SECONDS=30.0,
        WES_EFFECT_STATUS_SCAN_PERIOD_SECONDS=10.0,
    )


def _image_identity(marker: str = "a") -> str:
    return f"sha256:{marker * 64}"


def _build_artifact(*, role: str, settings_source: object, image_marker: str = "a"):
    module = _attestation_module()
    worker_queues = None
    worker_concurrency = None
    if role == "wes-worker":
        worker_queues = "default,celery,device"
    elif role == "fulfillment-worker":
        worker_queues = "wms-fulfillment"
        worker_concurrency = "1"
    return module.build_wms_deployment_attestation(
        role=role,
        image_identity=_image_identity(image_marker),
        settings_source=settings_source,
        worker_queues=worker_queues,
        worker_concurrency=worker_concurrency,
    )


def _build_four_artifacts(settings_source: object) -> tuple[Any, ...]:
    return tuple(
        _build_artifact(role=role, settings_source=settings_source)
        for role in ("api", "wes-worker", "fulfillment-worker", "beat")
    )


def _verify(module: Any, artifacts: object, settings_source: object):
    return module.verify_wms_deployment_attestations(
        artifacts,
        settings_source=settings_source,
        expected_image_identity=_image_identity(),
    )


def test_four_role_attestations_from_one_configuration_verify(tmp_path) -> None:
    module = _attestation_module()
    settings_source = _settings(write_provider_profile(tmp_path / "provider.yaml"))

    artifacts = _build_four_artifacts(settings_source)

    verified = _verify(module, artifacts, settings_source)
    assert tuple(artifact.role for artifact in verified) == (
        "api",
        "wes-worker",
        "fulfillment-worker",
        "beat",
    )
    assert {artifact.common.operation_count for artifact in verified} == {35}


def test_builder_uses_exactly_one_transport_compilation(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _attestation_module()
    compiled_profile = SimpleNamespace(
        profile=SimpleNamespace(
            profile=SimpleNamespace(identity="wms.contract", contract_version="contract"),
        ),
        profile_digest="1" * 64,
        operations={},
    )
    readiness = SimpleNamespace(
        process_role=SimpleNamespace(value="wes"),
        execution_lane=SimpleNamespace(value="wms-data"),
        operation_identities=(),
        endpoint_keys=(),
    )
    startup = SimpleNamespace(
        compiled_profile=compiled_profile,
        wes_readiness=readiness,
        fulfillment_readiness=readiness,
    )
    calls: list[object] = []

    def fake_validate(*, settings_source: object) -> object:
        calls.append(settings_source)
        return startup

    monkeypatch.setattr(module, "validate_wms_transport_configuration", fake_validate)
    monkeypatch.setattr(module, "WMS_OPERATION_IDENTITIES", ())
    monkeypatch.setattr(module, "WMS_OPERATION_INDEX_DIGEST", "2" * 64)
    monkeypatch.setattr(module, "conformance_endpoint_digest", lambda _profile: "3" * 64)

    module.build_wms_deployment_attestation(
        role="api",
        image_identity=_image_identity(),
        settings_source=SimpleNamespace(
            WMS_EFFECT_ADMISSION_ENABLED=False,
            WMS_EFFECT_STATUS_TIMEOUT_SECONDS=1,
            WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES=1,
            WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS=1,
            WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS=1,
            WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS=1,
            WES_EFFECT_NOT_FOUND_GRACE_SECONDS=1,
            WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS=1,
            WES_EFFECT_STATUS_SCAN_BATCH_SIZE=1,
            WES_EFFECT_STATUS_MAX_IN_FLIGHT=1,
            WES_EFFECT_STATUS_CLAIM_LEASE_SECONDS=1,
            WES_EFFECT_STATUS_MAX_QUERY_ATTEMPTS=1,
            WES_EFFECT_STATUS_INITIAL_BACKOFF_SECONDS=1,
            WES_EFFECT_STATUS_MAX_BACKOFF_SECONDS=1,
            WES_EFFECT_STATUS_SCAN_PERIOD_SECONDS=1,
        ),
    )

    assert len(calls) == 1


def test_artifact_is_strict_frozen_and_redacted(tmp_path) -> None:
    module = _attestation_module()
    profile_path = write_provider_profile(
        tmp_path / "provider.yaml",
        build_hmac_provider_profile_payload(),
    )
    artifact = _build_artifact(role="api", settings_source=_settings(profile_path))
    serialized = artifact.model_dump_json()

    assert "https://factory-wms.example" not in serialized
    assert "/api/wms" not in serialized
    assert "secret://wms/factory@v1" not in serialized
    assert "credential" not in serialized.lower()
    assert "server_url" not in serialized

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        module.WmsDeploymentAttestation.model_validate({**artifact.model_dump(mode="json"), "unexpected": True})
    with pytest.raises(ValidationError, match="frozen"):
        artifact.role = "beat"


@pytest.mark.parametrize(
    ("field_name", "drifted_value"),
    [
        ("image_identity", _image_identity("b")),
        ("provider_identity", "wms.drifted"),
        ("contract_version", "drifted"),
        ("profile_digest", "4" * 64),
        ("operation_index_digest", "5" * 64),
        ("endpoint_digest", "6" * 64),
        ("effect_admission_enabled", False),
        ("runtime_configuration_digest", "7" * 64),
    ],
)
def test_verify_rejects_any_common_fact_drift(tmp_path, field_name: str, drifted_value: object) -> None:
    module = _attestation_module()
    settings_source = _settings(write_provider_profile(tmp_path / "provider.yaml"))
    artifacts = list(_build_four_artifacts(settings_source))
    common = artifacts[-1].common.model_copy(update={field_name: drifted_value})
    artifacts[-1] = artifacts[-1].model_copy(update={"common": common})

    with pytest.raises(ValueError, match="trusted common baseline"):
        _verify(module, artifacts, settings_source)


def test_verify_rejects_four_consistently_forged_common_facts(tmp_path) -> None:
    module = _attestation_module()
    settings_source = _settings(write_provider_profile(tmp_path / "provider.yaml"))
    artifacts = list(_build_four_artifacts(settings_source))
    forged_common = artifacts[0].common.model_copy(
        update={
            "image_identity": _image_identity("f"),
            "provider_identity": "wms.forged",
            "contract_version": "forged",
            "profile_digest": "1" * 64,
            "operation_index_digest": "2" * 64,
            "endpoint_digest": "3" * 64,
            "effect_admission_enabled": False,
            "runtime_configuration_digest": "4" * 64,
        }
    )
    forged = tuple(artifact.model_copy(update={"common": forged_common}) for artifact in artifacts)

    with pytest.raises(ValueError, match="trusted common baseline"):
        module.verify_wms_deployment_attestations(
            forged,
            settings_source=settings_source,
            expected_image_identity=_image_identity(),
        )


@pytest.mark.parametrize("mutation", ("missing", "duplicate"))
def test_verify_rejects_missing_or_duplicate_role(tmp_path, mutation: str) -> None:
    module = _attestation_module()
    settings_source = _settings(write_provider_profile(tmp_path / "provider.yaml"))
    artifacts = list(_build_four_artifacts(settings_source))
    if mutation == "missing":
        artifacts.pop()
    else:
        artifacts[-1] = artifacts[0]

    with pytest.raises(ValueError, match="exactly once"):
        _verify(module, artifacts, settings_source)


def test_schema_rejects_unknown_role(tmp_path) -> None:
    module = _attestation_module()
    artifact = _build_artifact(
        role="api",
        settings_source=_settings(write_provider_profile(tmp_path / "provider.yaml")),
    )
    payload = artifact.model_dump(mode="json")
    payload["role"] = "unknown"

    with pytest.raises(ValidationError):
        module.WmsDeploymentAttestation.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "drifted_value"),
    [
        ("operation_count", 34),
        ("operation_order_digest", "8" * 64),
    ],
)
def test_verify_rejects_operation_count_or_order_drift(
    tmp_path,
    field_name: str,
    drifted_value: object,
) -> None:
    module = _attestation_module()
    settings_source = _settings(write_provider_profile(tmp_path / "provider.yaml"))
    artifacts = list(_build_four_artifacts(settings_source))
    common = artifacts[0].common.model_copy(update={field_name: drifted_value})
    artifacts[0] = artifacts[0].model_copy(update={"common": common})

    with pytest.raises(ValueError, match="trusted common baseline"):
        _verify(module, artifacts, settings_source)


def test_verify_rejects_worker_queue_or_concurrency_drift(tmp_path) -> None:
    module = _attestation_module()
    settings_source = _settings(write_provider_profile(tmp_path / "provider.yaml"))

    with pytest.raises(ValueError, match="default,celery,device"):
        module.build_wms_deployment_attestation(
            role="wes-worker",
            image_identity=_image_identity(),
            settings_source=settings_source,
            worker_queues="celery",
        )
    with pytest.raises(ValueError, match="concurrency=1"):
        module.build_wms_deployment_attestation(
            role="fulfillment-worker",
            image_identity=_image_identity(),
            settings_source=settings_source,
            worker_queues="wms-fulfillment",
            worker_concurrency="2",
        )


def test_beat_rejects_missing_required_schedule_or_route(tmp_path) -> None:
    module = _attestation_module()
    from src.celery_app import config

    settings_source = _settings(write_provider_profile(tmp_path / "provider.yaml"))
    missing_schedule = dict(config.beat_schedule)
    missing_schedule.pop("dispatch-wms-data-outbox-batch")
    with pytest.raises(ValueError, match="Beat required schedule"):
        module.build_wms_deployment_attestation(
            role="beat",
            image_identity=_image_identity(),
            settings_source=settings_source,
            beat_schedule_source=missing_schedule,
            task_routes_source=config.task_routes,
        )

    missing_route = dict(config.task_routes)
    missing_route.pop("src.celery_app.tasks.workline.scan_wms_effect_status_batch")
    with pytest.raises(ValueError, match="wms-fulfillment"):
        module.build_wms_deployment_attestation(
            role="beat",
            image_identity=_image_identity(),
            settings_source=settings_source,
            beat_schedule_source=config.beat_schedule,
            task_routes_source=missing_route,
        )


def test_cli_verify_rejects_more_than_four_artifact_lines(tmp_path) -> None:
    module = _attestation_module()
    artifacts = _build_four_artifacts(_settings(write_provider_profile(tmp_path / "provider.yaml")))
    artifact_lines = [artifact.model_dump_json() for artifact in (*artifacts, artifacts[0])]

    with pytest.raises(ValueError, match="exactly four"):
        module.parse_wms_deployment_attestation_lines(artifact_lines)


def test_cli_emits_and_verifies_four_offline_redacted_artifacts(tmp_path) -> None:
    profile_path = write_provider_profile(tmp_path / "provider.yaml")
    # tmp_path 不在仓库内，CLI 路径以当前测试文件反推。
    script_path = Path(__file__).resolve().parents[3] / "scripts/check_wms_deployment_attestation.py"
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "prod",
            "POSTGRES_HOST": "attestation-must-not-connect.invalid",
            "REDIS_HOST": "attestation-must-not-connect.invalid",
            "WMS_PROVIDER_PROFILE_FILE": str(profile_path),
            "WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED": "false",
            "WMS_EFFECT_ADMISSION_ENABLED": "true",
            "WMS_EFFECT_STATUS_TIMEOUT_SECONDS": "5",
            "WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES": "65536",
            "WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS": "3600",
            "WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS": "20",
            "WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS": "1800",
            "WES_EFFECT_NOT_FOUND_GRACE_SECONDS": "30",
            "WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS": "300",
            "WES_EFFECT_STATUS_SCAN_BATCH_SIZE": "1",
            "WES_EFFECT_STATUS_MAX_IN_FLIGHT": "1",
            "WES_EFFECT_STATUS_CLAIM_LEASE_SECONDS": "120",
            "WES_EFFECT_STATUS_MAX_QUERY_ATTEMPTS": "8",
            "WES_EFFECT_STATUS_INITIAL_BACKOFF_SECONDS": "1",
            "WES_EFFECT_STATUS_MAX_BACKOFF_SECONDS": "30",
            "WMS_DEPLOYMENT_IMAGE_ID": _image_identity(),
        }
    )
    artifact_paths = []
    for role in ("api", "wes-worker", "fulfillment-worker", "beat"):
        role_environment = dict(environment)
        role_environment["WMS_DEPLOYMENT_ROLE"] = role
        role_environment["WMS_DEPLOYMENT_IMAGE_ID"] = _image_identity()
        if role == "wes-worker":
            role_environment["CELERY_WORKER_QUEUES"] = "default,celery,device"
        elif role == "fulfillment-worker":
            role_environment["CELERY_WORKER_QUEUES"] = "wms-fulfillment"
            role_environment["CELERY_WORKER_CONCURRENCY"] = "1"
        completed = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "emit",
            ],
            cwd=script_path.parents[1],
            env=role_environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert completed.returncode == 0, completed.stderr
        assert "factory-wms.example" not in completed.stdout
        artifact_path = tmp_path / f"{role}.json"
        artifact_path.write_text(completed.stdout, encoding="utf-8")
        artifact_paths.append(artifact_path)

    completed = subprocess.run(
        [sys.executable, str(script_path), "verify-stdin"],
        cwd=script_path.parents[1],
        env=environment,
        check=False,
        capture_output=True,
        input="\n".join(path.read_text(encoding="utf-8").strip() for path in artifact_paths) + "\n",
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["schema_version"] == "wms-deployment-attestation-summary.v1"
    assert summary["roles"] == ["api", "wes-worker", "fulfillment-worker", "beat"]
    assert summary["common"]["operation_count"] == 35
    assert set(summary["role_fact_digests"]) == {
        "api",
        "wes-worker",
        "fulfillment-worker",
        "beat",
    }
    assert "factory-wms.example" not in completed.stdout
    assert "credential" not in completed.stdout.lower()


def test_cli_does_not_expose_public_role_or_image_arguments() -> None:
    script_path = Path(__file__).resolve().parents[3] / "scripts/check_wms_deployment_attestation.py"
    completed = subprocess.run(
        [sys.executable, str(script_path), "emit", "--help"],
        cwd=script_path.parents[1],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--role" not in completed.stdout
    assert "--image-identity" not in completed.stdout
