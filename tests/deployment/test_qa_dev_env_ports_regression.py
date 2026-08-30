# Regression: ISSUE-002 — worktree checks must probe the configured host ports.
# Found by /qa on 2026-08-30
# Report: .gstack/qa-reports/qa-report-127-0-0-1-15173-2026-08-30.md

from pathlib import Path

import pytest

from tests.deployment.test_local_development_environment import _run_development_check


def test_development_check_uses_configured_host_ports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    configured_ports = {
        "APP_HOST_PORT": "18001",
        "FRONTEND_PORT": "15173",
        "NGINX_HTTP_PORT": "18080",
        "MOCK_ECS_PORT": "18010",
        "MOCK_WMS_PORT": "18011",
    }
    for name, value in configured_ports.items():
        monkeypatch.setenv(name, value)

    completed, trace = _run_development_check(
        tmp_path,
        expected_root=frontend,
        mounted_root=str(frontend),
    )

    assert completed.returncode == 0, completed.stderr
    curl_commands = "\n".join(command for command in trace if command.startswith("curl "))
    for port in configured_ports.values():
        assert f"127.0.0.1:{port}" in curl_commands
