"""跨环境 WorkLine migration matrix CLI 合同。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

import scripts.workline_migration_inventory as inventory_cli
import scripts.workline_migration_matrix as cli
from src.app.contracts.external_contract_profile_catalog import list_external_contract_profiles
from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_INDEX_DIGEST
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.workline.models import WorklineInventoryApprovalEvidence, WorklineMigrationInventoryReport
from src.app.workline.services import WorklineMigrationInventoryService

NOW = datetime(2026, 7, 26, 10, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_inventory(path: Path, environment: str) -> WorklineMigrationInventoryReport:
    report = WorklineMigrationInventoryReport(
        environment=environment,
        generated_at=NOW,
        inventory_digest="0" * 64,
        plugin_index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
        system_capability_index_digest=SYSTEM_CAPABILITY_INDEX_DIGEST,
        foundation_ready=True,
        provider_profile_catalog=WorklineMigrationInventoryService.derive_provider_profile_catalog(
            list_external_contract_profiles()
        ),
    )
    payload = report.model_dump(mode="json", exclude={"generated_at", "inventory_digest"})
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    report = WorklineMigrationInventoryReport.model_validate(
        {**report.model_dump(mode="python"), "inventory_digest": digest}
    )
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def _write_approval(path: Path, report: WorklineMigrationInventoryReport) -> None:
    evidence = WorklineInventoryApprovalEvidence(
        environment=report.environment,
        inventory_digest=report.inventory_digest,
        inventory_generated_at=report.generated_at,
        approved_by="release-owner",
        approved_at=NOW,
        reason="批准当前环境 inventory",
    )
    path.write_text(evidence.model_dump_json(indent=2), encoding="utf-8")


def test_cli_aggregates_reports_and_approvals_into_machine_readable_matrix(
    tmp_path: Path,
    capsys,
) -> None:
    production_path = tmp_path / "production.json"
    staging_path = tmp_path / "staging.json"
    production = _write_inventory(production_path, "production")
    staging = _write_inventory(staging_path, "staging")
    production_approval = tmp_path / "production-approval.json"
    staging_approval = tmp_path / "staging-approval.json"
    _write_approval(production_approval, production)
    _write_approval(staging_approval, staging)

    status = cli.run(
        [
            "--inventory-report",
            str(staging_path),
            "--inventory-report",
            str(production_path),
            "--approval",
            str(staging_approval),
            "--approval",
            str(production_approval),
            "--required-environment",
            "production",
            "--required-environment",
            "staging",
            "--check",
        ],
        clock=lambda: NOW,
    )

    payload = json.loads(capsys.readouterr().out)
    assert status == cli.EXIT_OK
    assert payload["inventory_gate_ready"] is True
    assert [item["environment"] for item in payload["inventories"]] == ["production", "staging"]


def test_cli_check_returns_blocked_when_approval_is_missing(tmp_path: Path, capsys) -> None:
    production_path = tmp_path / "production.json"
    _write_inventory(production_path, "production")

    status = cli.run(
        [
            "--inventory-report",
            str(production_path),
            "--required-environment",
            "production",
            "--check",
        ],
        clock=lambda: NOW,
    )

    payload = json.loads(capsys.readouterr().out)
    assert status == cli.EXIT_INVENTORY_BLOCKED
    assert payload["issues"][0]["code"] == "APPROVAL_MISSING"


def test_cli_returns_stable_runtime_error_for_non_utf8_artifact(tmp_path: Path, capsys) -> None:
    invalid_report = tmp_path / "invalid.json"
    invalid_report.write_bytes(b"\xff")

    try:
        status = cli.run(
            [
                "--inventory-report",
                str(invalid_report),
                "--required-environment",
                "production",
            ]
        )
    except UnicodeDecodeError as exc:
        pytest.fail(f"CLI 泄漏 UnicodeDecodeError: {exc}")

    captured = capsys.readouterr()
    assert status == cli.EXIT_RUNTIME_ERROR
    assert "migration matrix 生成失败" in captured.err


def test_cli_runtime_error_does_not_echo_invalid_artifact_values(tmp_path: Path, capsys) -> None:
    inventory_path = tmp_path / "production.json"
    report = _write_inventory(inventory_path, "prod")
    approval_path = tmp_path / "production-approval.json"
    approval_path.write_text(
        json.dumps(
            {
                "environment": report.environment,
                "inventory_digest": report.inventory_digest,
                "inventory_generated_at": report.generated_at.isoformat(),
                "approved_by": "release-owner",
                "approved_at": NOW.isoformat(),
                "reason": "批准当前环境 inventory",
                "secret": "TOP-SECRET-TOKEN",
            }
        ),
        encoding="utf-8",
    )

    status = cli.run(
        [
            "--inventory-report",
            str(inventory_path),
            "--approval",
            str(approval_path),
            "--required-environment",
            "prod",
        ]
    )

    captured = capsys.readouterr()
    assert status == cli.EXIT_RUNTIME_ERROR
    assert "migration matrix 生成失败" in captured.err
    assert "TOP-SECRET-TOKEN" not in captured.err


def test_operations_runbook_uses_inventory_cli_environment_names() -> None:
    runbook = (REPO_ROOT / "docs/operations/workline-plugin-migration-inventory.md").read_text(encoding="utf-8")
    required_environments = set(re.findall(r"--required-environment\s+([a-z]+)", runbook))
    expected_environment_action = next(
        action for action in inventory_cli._build_parser()._actions if action.dest == "expected_environment"
    )

    assert required_environments
    assert required_environments <= set(expected_environment_action.choices)


def test_plugin_platform_spec_has_one_current_t1_status() -> None:
    spec = (
        REPO_ROOT / "docs/superpowers/specs/2026-07-15-workline-plugin-system-capability-platform-design.md"
    ).read_text(encoding="utf-8")

    assert "### 当前结论（截至 2026-07-26）" in spec
    assert "T1 Remaining 仍未完成" not in spec
    assert "T1 Remaining、其他 active Workline" not in spec
