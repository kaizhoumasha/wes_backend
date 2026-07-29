"""WMS Provider profile 的 Settings 入口与启动装配门禁。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.core.conf import Settings
from tests.contracts.wms_integration.provider_profile_support import write_provider_profile

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_settings_accepts_only_absolute_provider_profile_file(tmp_path) -> None:
    absolute = tmp_path / "provider.yaml"
    configured = Settings(WMS_PROVIDER_PROFILE_FILE=absolute)  # pyright: ignore[reportCallIssue]
    assert absolute == configured.WMS_PROVIDER_PROFILE_FILE

    with pytest.raises(ValidationError, match="absolute path"):
        Settings(WMS_PROVIDER_PROFILE_FILE="config/provider.yaml")  # pyright: ignore[reportCallIssue]


def test_startup_assembly_rejects_missing_profile_path() -> None:
    from src.app.wms_integration.provider_startup import assemble_wms_provider_startup

    with pytest.raises(ValueError, match="WMS_PROVIDER_PROFILE_FILE"):
        assemble_wms_provider_startup(SimpleNamespace(WMS_PROVIDER_PROFILE_FILE=None))
    with pytest.raises(ValueError, match="absolute path"):
        assemble_wms_provider_startup(SimpleNamespace(WMS_PROVIDER_PROFILE_FILE="provider.yaml"))


def test_startup_assembly_loads_one_profile_for_both_lane_readiness(tmp_path) -> None:
    from src.app.wms_integration.provider_readiness import WmsProviderProcessRole
    from src.app.wms_integration.provider_startup import assemble_wms_provider_startup

    path = write_provider_profile(tmp_path / "provider.yaml")
    startup = assemble_wms_provider_startup(SimpleNamespace(WMS_PROVIDER_PROFILE_FILE=path))

    assert startup.compiled_profile.profile_digest == startup.wes_readiness.profile_digest
    assert startup.compiled_profile.profile_digest == startup.fulfillment_readiness.profile_digest
    assert startup.wes_readiness.process_role is WmsProviderProcessRole.WES
    assert startup.fulfillment_readiness.process_role is WmsProviderProcessRole.FULFILLMENT


def test_transport_startup_gate_uses_compiled_profile_instead_of_legacy_endpoint_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from src.app.runtime.system_capabilities.wms import provider_catalog

    profile_path = write_provider_profile(tmp_path / "provider.yaml")
    settings_source = SimpleNamespace(
        APP_ENV="prod",
        WMS_PROVIDER_PROFILE_FILE=profile_path,
        WMS_QUERY_IN_PROCESS_SIMULATION_ENABLED=False,
        WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS=100,
        WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS=70,
        WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS=20,
        WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS=2,
        WES_EFFECT_NOT_FOUND_GRACE_SECONDS=3,
    )

    startup = provider_catalog.validate_wms_transport_configuration(settings_source=settings_source)

    assert startup.compiled_profile.profile.server_url == "http://factory-wms.example:8080"
    assert not hasattr(settings_source, "WMS_SYNC_BASE_URL")
    assert not hasattr(settings_source, "WMS_EFFECT_STATUS_URL")


def test_active_configuration_contains_no_legacy_wms_endpoint_settings() -> None:
    assert "WMS_SYNC_BASE_URL" not in Settings.model_fields
    assert "WMS_EFFECT_STATUS_URL" not in Settings.model_fields

    active_paths = (
        REPO_ROOT / "src/core/conf.py",
        REPO_ROOT / "src/app/runtime/system_capabilities/wms/provider_catalog.py",
        REPO_ROOT / "src/app/wms_integration/runtime_factory.py",
        REPO_ROOT / "src/app/wms_integration/ports/effect_status.py",
        REPO_ROOT / ".env.dev",
        REPO_ROOT / ".env.test",
        REPO_ROOT / ".env.prod",
        REPO_ROOT / "docker-compose.yml",
        REPO_ROOT / "docker-compose.deploy.yml",
        REPO_ROOT / "docker-compose.test-deploy.yml",
    )
    legacy_names = ("WMS_SYNC_BASE_URL", "WMS_EFFECT_STATUS_URL")
    for path in active_paths:
        source = path.read_text(encoding="utf-8")
        assert all(name not in source for name in legacy_names), path.relative_to(REPO_ROOT)
