"""WMS Provider profile 的 Settings 入口与启动装配门禁。"""

from __future__ import annotations

import shutil
import subprocess
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

    assert startup.catalog.compiled_profile is startup.compiled_profile
    assert startup.catalog.profile_digest == startup.compiled_profile.profile_digest
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
    assert not hasattr(settings_source, "WMS_SYNC_" + "BASE_URL")
    assert not hasattr(settings_source, "WMS_EFFECT_" + "STATUS_URL")


def test_active_configuration_contains_no_legacy_wms_endpoint_settings() -> None:
    legacy_names = (
        "WMS_SYNC_" + "BASE_URL",
        "WMS_EFFECT_" + "STATUS_URL",
        "WMS_MATERIAL_FLOW_" + "ACTIVE_HMAC_VERSION",
    )
    assert all(name not in Settings.model_fields for name in legacy_names)

    git_executable = shutil.which("git")
    assert git_executable is not None
    tracked_files = (
        subprocess.run(
            [git_executable, "ls-files", "-z"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .split("\0")
    )
    for relative_path in tracked_files:
        if not relative_path:
            continue
        path = REPO_ROOT / relative_path
        source = path.read_text(encoding="utf-8", errors="ignore")
        assert all(name not in source for name in legacy_names), relative_path
