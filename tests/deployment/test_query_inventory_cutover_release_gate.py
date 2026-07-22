"""CI release 在新容器启动前必须执行 inventory QUERY cutover gate。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from src.app.runtime.system_capabilities.shadow_readiness import ReadinessGateError

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_release_gate_cli_fails_closed_on_readiness_rejection(monkeypatch, capsys) -> None:
    from scripts import check_query_inventory_cutover_readiness as gate_script

    run_gate = AsyncMock(side_effect=ReadinessGateError("missing READY+GO"))
    monkeypatch.setattr(gate_script, "run_gate", run_gate)

    assert gate_script.main() == 1
    assert "blocked" in capsys.readouterr().err
    run_gate.assert_awaited_once()


def test_jenkins_release_runs_gate_after_migration_and_before_application_start() -> None:
    source = (REPO_ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    migration = source.index("api alembic upgrade head")
    gate = source.index("api python scripts/check_query_inventory_cutover_readiness.py")
    start = source.index("$COMPOSE_CMD up -d --no-build --no-deps ${DEPLOY_SERVICES}")

    assert migration < gate < start
