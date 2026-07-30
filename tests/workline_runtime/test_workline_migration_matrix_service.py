"""跨环境 WorkLine 迁移矩阵、批准证据与 preflight 输入合同。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.app.contracts.external_contract_profile_catalog import list_external_contract_profiles
from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_INDEX_DIGEST
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.workline.models import (
    WorklineInventoryApprovalEvidence,
    WorklineMigrationInventoryIssue,
    WorklineMigrationInventoryIssueCode,
    WorklineMigrationInventoryItem,
    WorklineMigrationInventoryReport,
    WorklineMigrationInventorySeverity,
    WorklineMigrationMatrixIssueCode,
    WorklineProviderProfileInventoryItem,
    WorklineRuntimeReferenceSummary,
)
from src.app.workline.services import (
    WorklineMigrationMatrixInvariantError,
    WorklineMigrationMatrixPreflightError,
    WorklineMigrationMatrixService,
)

NOW = datetime(2026, 7, 26, 10, tzinfo=UTC)
PLUGIN_DIGEST = WORKLINE_PLUGIN_INDEX_DIGEST
CAPABILITY_DIGEST = SYSTEM_CAPABILITY_INDEX_DIGEST
PROVIDER_PROFILE_CATALOG = tuple(
    sorted(
        (
            WorklineProviderProfileInventoryItem(
                provider_code=profile.provider_code,
                contract_version=profile.contract_version,
            )
            for profile in list_external_contract_profiles()
        ),
        key=lambda item: (item.provider_code, item.contract_version),
    )
)


def _inventory(
    environment: str,
    *,
    ready: bool = True,
    capability_digest: str = CAPABILITY_DIGEST,
    generated_at: datetime = NOW,
    issues: tuple[WorklineMigrationInventoryIssue, ...] = (),
    worklines: tuple[WorklineMigrationInventoryItem, ...] = (),
    provider_profile_catalog: tuple[WorklineProviderProfileInventoryItem, ...] = PROVIDER_PROFILE_CATALOG,
):
    report = WorklineMigrationInventoryReport(
        environment=environment,
        generated_at=generated_at,
        inventory_digest="0" * 64,
        plugin_index_digest=PLUGIN_DIGEST,
        system_capability_index_digest=capability_digest,
        foundation_ready=ready,
        worklines=worklines,
        provider_profile_catalog=provider_profile_catalog,
        issues=issues,
    )
    payload = report.model_dump(mode="json", exclude={"generated_at", "inventory_digest"})
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return WorklineMigrationInventoryReport.model_validate(
        {**report.model_dump(mode="python"), "inventory_digest": digest}
    )


def _approval(report: WorklineMigrationInventoryReport) -> WorklineInventoryApprovalEvidence:
    return WorklineInventoryApprovalEvidence(
        environment=report.environment,
        inventory_digest=report.inventory_digest,
        inventory_generated_at=report.generated_at,
        approved_by=f"owner-{report.environment}",
        approved_at=NOW,
        reason="批准当前环境迁移清单",
    )


def test_approval_evidence_binds_the_exact_inventory_report_instance() -> None:
    production = _inventory("production")

    try:
        approval = WorklineInventoryApprovalEvidence(
            environment=production.environment,
            inventory_digest=production.inventory_digest,
            inventory_generated_at=production.generated_at,
            approved_by="owner-production",
            approved_at=NOW,
            reason="批准当前环境迁移清单",
        )
    except ValidationError as exc:
        pytest.fail(f"批准证据尚未绑定 inventory_generated_at: {exc}")

    assert approval.inventory_generated_at == production.generated_at


def test_matrix_aggregates_required_environments_and_digest_bound_approvals_deterministically() -> None:
    production = _inventory("production")
    staging = _inventory("staging")
    service = WorklineMigrationMatrixService(clock=lambda: NOW)

    first = service.build_matrix(
        inventories=(production, staging),
        approvals=(_approval(production), _approval(staging)),
        required_environments=("staging", "production"),
    )
    second = WorklineMigrationMatrixService(clock=lambda: NOW + timedelta(hours=1)).build_matrix(
        inventories=(staging, production),
        approvals=(_approval(staging), _approval(production)),
        required_environments=("production", "staging"),
    )

    assert first.inventory_gate_ready is True
    assert first.required_environments == ("production", "staging")
    assert tuple(report.environment for report in first.inventories) == ("production", "staging")
    assert tuple(approval.environment for approval in first.approvals) == ("production", "staging")
    assert first.issues == ()
    assert first.matrix_digest == second.matrix_digest


@pytest.mark.parametrize(
    ("approvals", "expected_code"),
    [
        ((), WorklineMigrationMatrixIssueCode.APPROVAL_MISSING),
        ("wrong-digest", WorklineMigrationMatrixIssueCode.APPROVAL_DIGEST_MISMATCH),
    ],
)
def test_matrix_blocks_missing_or_digest_mismatched_approval(
    approvals: tuple[()] | str,
    expected_code: WorklineMigrationMatrixIssueCode,
) -> None:
    production = _inventory("production")
    evidence = (
        ()
        if approvals == ()
        else (
            WorklineInventoryApprovalEvidence(
                environment="production",
                inventory_digest="f" * 64,
                inventory_generated_at=production.generated_at,
                approved_by="owner",
                approved_at=NOW,
                reason="批准错误摘要",
            ),
        )
    )

    matrix = WorklineMigrationMatrixService(clock=lambda: NOW).build_matrix(
        inventories=(production,),
        approvals=evidence,
        required_environments=("production",),
    )

    assert matrix.inventory_gate_ready is False
    assert tuple(issue.code for issue in matrix.issues) == (expected_code,)


def test_matrix_blocks_approval_bound_to_a_previous_report_instance() -> None:
    original = _inventory("production")
    approval = _approval(original)
    regenerated = original.model_copy(update={"generated_at": NOW + timedelta(hours=1)})

    matrix = WorklineMigrationMatrixService(clock=lambda: NOW + timedelta(hours=1)).build_matrix(
        inventories=(regenerated,),
        approvals=(approval,),
        required_environments=("production",),
    )

    assert matrix.inventory_gate_ready is False
    assert {issue.code.value for issue in matrix.issues} == {"APPROVAL_REPORT_MISMATCH"}


def test_matrix_blocks_missing_environment_not_ready_inventory_and_index_drift() -> None:
    blocker = WorklineMigrationInventoryIssue(
        code=WorklineMigrationInventoryIssueCode.RUNTIME_REFERENCES_PRESENT,
        severity=WorklineMigrationInventorySeverity.BLOCKER,
        message="仍存在未完成运行态引用",
        workline_id=1,
        line_code="LINE-01",
    )
    blocked_workline = WorklineMigrationInventoryItem(
        workline_id=1,
        line_code="LINE-01",
        is_active=False,
        plugin_key=None,
        configured_contract_version=None,
        catalog_contract_version=None,
        run_mode="AUTO",
        runtime_references=WorklineRuntimeReferenceSummary(
            sessions=1,
            commands=0,
            outboxes=0,
            inboxes=0,
            runtime_holds=0,
            total=1,
        ),
        foundation_ready=False,
        issues=(blocker,),
    )
    production = _inventory(
        "production",
        ready=False,
        issues=(blocker,),
        worklines=(blocked_workline,),
    )
    staging = _inventory("staging", capability_digest="c" * 64)

    matrix = WorklineMigrationMatrixService(clock=lambda: NOW).build_matrix(
        inventories=(production, staging),
        approvals=(_approval(production), _approval(staging)),
        required_environments=("production", "staging", "sandbox"),
    )

    assert matrix.inventory_gate_ready is False
    assert {issue.code for issue in matrix.issues} == {
        WorklineMigrationMatrixIssueCode.INDEX_DIGEST_MISMATCH,
        WorklineMigrationMatrixIssueCode.INVENTORY_NOT_READY,
        WorklineMigrationMatrixIssueCode.MISSING_REQUIRED_ENVIRONMENT,
    }


def test_matrix_blocks_indexes_that_are_consistent_but_do_not_match_the_deployment() -> None:
    production = _inventory("production", capability_digest="c" * 64)

    matrix = WorklineMigrationMatrixService(clock=lambda: NOW).build_matrix(
        inventories=(production,),
        approvals=(_approval(production),),
        required_environments=("production",),
    )

    assert matrix.inventory_gate_ready is False
    assert {issue.code for issue in matrix.issues} == {
        WorklineMigrationMatrixIssueCode.INDEX_DIGEST_MISMATCH,
    }


def test_matrix_reports_index_drift_before_revalidating_old_capability_projection() -> None:
    old_projection = WorklineMigrationInventoryItem(
        workline_id=1,
        line_code="LINE-01",
        is_active=False,
        plugin_key="rough_sorter",
        configured_contract_version="rough_sorter.v2",
        catalog_contract_version="rough_sorter.v2",
        run_mode="AUTO",
        capability_requirements=(),
        runtime_references=WorklineRuntimeReferenceSummary(
            sessions=0,
            commands=0,
            outboxes=0,
            inboxes=0,
            runtime_holds=0,
            total=0,
        ),
        foundation_ready=True,
        issues=(),
    )
    production = _inventory(
        "production",
        capability_digest="c" * 64,
        worklines=(old_projection,),
    )

    matrix = WorklineMigrationMatrixService(clock=lambda: NOW).build_matrix(
        inventories=(production,),
        approvals=(_approval(production),),
        required_environments=("production",),
    )

    assert matrix.inventory_gate_ready is False
    assert tuple(issue.code for issue in matrix.issues) == (WorklineMigrationMatrixIssueCode.INDEX_DIGEST_MISMATCH,)


def test_matrix_rejects_inventory_whose_payload_no_longer_matches_its_digest() -> None:
    production = _inventory("production")
    tampered = production.model_copy(update={"foundation_ready": False})

    with pytest.raises(WorklineMigrationMatrixInvariantError, match="inventory_digest"):
        WorklineMigrationMatrixService(clock=lambda: NOW).build_matrix(
            inventories=(tampered,),
            approvals=(_approval(production),),
            required_environments=("production",),
        )


def test_matrix_rejects_inventory_whose_ready_flag_conflicts_with_blocker_issues() -> None:
    blocker = WorklineMigrationInventoryIssue(
        code=WorklineMigrationInventoryIssueCode.RUNTIME_REFERENCES_PRESENT,
        severity=WorklineMigrationInventorySeverity.BLOCKER,
        message="仍存在未完成运行态引用",
    )
    contradictory = _inventory("production", ready=True, issues=(blocker,))

    with pytest.raises(WorklineMigrationMatrixInvariantError, match="派生状态"):
        WorklineMigrationMatrixService(clock=lambda: NOW).build_matrix(
            inventories=(contradictory,),
            approvals=(_approval(contradictory),),
            required_environments=("production",),
        )


def test_matrix_rejects_inventory_that_omits_issue_required_by_facts() -> None:
    workline = WorklineMigrationInventoryItem(
        workline_id=1,
        line_code="LINE-01",
        is_active=False,
        plugin_key=None,
        configured_contract_version=None,
        catalog_contract_version=None,
        run_mode="AUTO",
        runtime_references=WorklineRuntimeReferenceSummary(
            sessions=1,
            commands=0,
            outboxes=0,
            inboxes=0,
            runtime_holds=0,
            total=1,
        ),
        foundation_ready=True,
        issues=(),
    )
    contradictory = _inventory("production", ready=True, worklines=(workline,))

    with pytest.raises(WorklineMigrationMatrixInvariantError, match="派生状态"):
        WorklineMigrationMatrixService(clock=lambda: NOW).build_matrix(
            inventories=(contradictory,),
            approvals=(_approval(contradictory),),
            required_environments=("production",),
        )


def test_matrix_rejects_inventory_with_inconsistent_runtime_reference_totals() -> None:
    workline = WorklineMigrationInventoryItem(
        workline_id=1,
        line_code="LINE-01",
        is_active=False,
        plugin_key=None,
        configured_contract_version=None,
        catalog_contract_version=None,
        run_mode="AUTO",
        runtime_references=WorklineRuntimeReferenceSummary(
            sessions=1,
            commands=0,
            outboxes=0,
            inboxes=0,
            runtime_holds=0,
            total=0,
        ),
        foundation_ready=True,
        issues=(),
    )
    contradictory = _inventory("production", ready=True, worklines=(workline,))

    with pytest.raises(WorklineMigrationMatrixInvariantError, match="引用摘要"):
        WorklineMigrationMatrixService(clock=lambda: NOW).build_matrix(
            inventories=(contradictory,),
            approvals=(_approval(contradictory),),
            required_environments=("production",),
        )


def test_matrix_rejects_inventory_that_omits_generated_capability_requirements() -> None:
    workline = WorklineMigrationInventoryItem(
        workline_id=1,
        line_code="LINE-01",
        is_active=False,
        plugin_key="rough_sorter",
        configured_contract_version="rough_sorter.v2",
        catalog_contract_version="rough_sorter.v2",
        run_mode="AUTO",
        capability_requirements=(),
        runtime_references=WorklineRuntimeReferenceSummary(
            sessions=0,
            commands=0,
            outboxes=0,
            inboxes=0,
            runtime_holds=0,
            total=0,
        ),
        foundation_ready=True,
        issues=(),
    )
    contradictory = _inventory("production", ready=True, worklines=(workline,))

    with pytest.raises(WorklineMigrationMatrixInvariantError, match="能力派生"):
        WorklineMigrationMatrixService(clock=lambda: NOW).build_matrix(
            inventories=(contradictory,),
            approvals=(_approval(contradictory),),
            required_environments=("production",),
        )


def test_matrix_rejects_inventory_with_forged_catalog_contract_version() -> None:
    workline = WorklineMigrationInventoryItem(
        workline_id=1,
        line_code="LINE-01",
        is_active=False,
        plugin_key="rough_sorter",
        configured_contract_version="rough_sorter.v2",
        catalog_contract_version="forged.v1",
        run_mode="AUTO",
        runtime_references=WorklineRuntimeReferenceSummary(
            sessions=0,
            commands=0,
            outboxes=0,
            inboxes=0,
            runtime_holds=0,
            total=0,
        ),
        foundation_ready=True,
        issues=(),
    )
    contradictory = _inventory("production", ready=True, worklines=(workline,))

    with pytest.raises(WorklineMigrationMatrixInvariantError, match="能力派生"):
        WorklineMigrationMatrixService(clock=lambda: NOW).build_matrix(
            inventories=(contradictory,),
            approvals=(_approval(contradictory),),
            required_environments=("production",),
        )


def test_matrix_blocks_provider_profile_catalog_drift_with_machine_readable_issue() -> None:
    drifted = _inventory("production", provider_profile_catalog=())

    matrix = WorklineMigrationMatrixService(clock=lambda: NOW).build_matrix(
        inventories=(drifted,),
        approvals=(_approval(drifted),),
        required_environments=("production",),
    )

    assert matrix.inventory_gate_ready is False
    assert tuple(issue.code.value for issue in matrix.issues) == ("PROVIDER_PROFILE_CATALOG_MISMATCH",)


def test_matrix_rejects_future_dated_inventory_and_approval() -> None:
    future = _inventory("production", generated_at=NOW + timedelta(hours=1))
    future_approval = WorklineInventoryApprovalEvidence(
        environment=future.environment,
        inventory_digest=future.inventory_digest,
        inventory_generated_at=future.generated_at,
        approved_by="owner-production",
        approved_at=future.generated_at,
        reason="未来时间戳不得通过",
    )

    with pytest.raises(WorklineMigrationMatrixInvariantError, match="未来"):
        WorklineMigrationMatrixService(clock=lambda: NOW).build_matrix(
            inventories=(future,),
            approvals=(future_approval,),
            required_environments=("production",),
        )


def test_approved_matrix_is_reusable_as_fail_closed_preflight_input() -> None:
    production = _inventory("production")
    service = WorklineMigrationMatrixService(clock=lambda: NOW)
    matrix = service.build_matrix(
        inventories=(production,),
        approvals=(_approval(production),),
        required_environments=("production",),
    )

    service.assert_approved_matrix(
        matrix,
        expected_matrix_digest=matrix.matrix_digest,
        max_inventory_age=timedelta(hours=1),
    )

    with pytest.raises(WorklineMigrationMatrixPreflightError, match="摘要"):
        service.assert_approved_matrix(
            matrix,
            expected_matrix_digest="f" * 64,
            max_inventory_age=timedelta(hours=1),
        )
    blocked = service.build_matrix(
        inventories=(production,),
        approvals=(),
        required_environments=("production",),
    )
    with pytest.raises(WorklineMigrationMatrixPreflightError, match="未通过"):
        service.assert_approved_matrix(
            blocked,
            expected_matrix_digest=blocked.matrix_digest,
            max_inventory_age=timedelta(hours=1),
        )


def test_preflight_requires_an_external_matrix_digest_anchor() -> None:
    production = _inventory("production")
    service = WorklineMigrationMatrixService(clock=lambda: NOW)
    matrix = service.build_matrix(
        inventories=(production,),
        approvals=(_approval(production),),
        required_environments=("production",),
    )

    with pytest.raises(WorklineMigrationMatrixPreflightError, match="部署固定值"):
        service.assert_approved_matrix(
            matrix,
            expected_matrix_digest=None,
            max_inventory_age=timedelta(hours=1),
        )


def test_preflight_rejects_inventory_older_than_the_required_max_age() -> None:
    old_inventory = _inventory("production", generated_at=NOW - timedelta(hours=2))
    approval = WorklineInventoryApprovalEvidence(
        environment=old_inventory.environment,
        inventory_digest=old_inventory.inventory_digest,
        inventory_generated_at=old_inventory.generated_at,
        approved_by="owner-production",
        approved_at=NOW,
        reason="批准旧报告",
    )
    service = WorklineMigrationMatrixService(clock=lambda: NOW)
    matrix = service.build_matrix(
        inventories=(old_inventory,),
        approvals=(approval,),
        required_environments=("production",),
    )

    with pytest.raises(WorklineMigrationMatrixPreflightError, match="过期"):
        service.assert_approved_matrix(
            matrix,
            expected_matrix_digest=matrix.matrix_digest,
            max_inventory_age=timedelta(hours=1),
        )


def test_preflight_requires_a_positive_inventory_max_age() -> None:
    production = _inventory("production")
    service = WorklineMigrationMatrixService(clock=lambda: NOW)
    matrix = service.build_matrix(
        inventories=(production,),
        approvals=(_approval(production),),
        required_environments=("production",),
    )

    with pytest.raises(WorklineMigrationMatrixPreflightError, match="最大有效期"):
        service.assert_approved_matrix(
            matrix,
            expected_matrix_digest=matrix.matrix_digest,
            max_inventory_age=timedelta(0),
        )


def test_preflight_revalidates_each_embedded_inventory_payload() -> None:
    production = _inventory("production")
    service = WorklineMigrationMatrixService(clock=lambda: NOW)
    matrix = service.build_matrix(
        inventories=(production,),
        approvals=(_approval(production),),
        required_environments=("production",),
    )
    tampered_inventory = production.model_copy(update={"foundation_ready": False})
    tampered_matrix = matrix.model_copy(update={"inventories": (tampered_inventory,)})

    with pytest.raises(WorklineMigrationMatrixPreflightError, match="inventory_digest"):
        service.assert_approved_matrix(
            tampered_matrix,
            expected_matrix_digest=matrix.matrix_digest,
            max_inventory_age=timedelta(hours=1),
        )


def test_preflight_recomputes_derived_gate_state_instead_of_trusting_artifact_fields() -> None:
    production = _inventory("production")
    service = WorklineMigrationMatrixService(clock=lambda: NOW)
    blocked = service.build_matrix(
        inventories=(production,),
        approvals=(),
        required_environments=("production",),
    )
    forged = blocked.model_copy(update={"inventory_gate_ready": True, "issues": ()})
    forged = forged.model_copy(update={"matrix_digest": service._matrix_digest(forged)})

    with pytest.raises(WorklineMigrationMatrixPreflightError, match="派生状态"):
        service.assert_approved_matrix(
            forged,
            expected_matrix_digest=forged.matrix_digest,
            max_inventory_age=timedelta(hours=1),
        )


def test_matrix_rejects_approval_without_matching_inventory() -> None:
    production = _inventory("production")
    orphan_approval = WorklineInventoryApprovalEvidence(
        environment="staging",
        inventory_digest="f" * 64,
        inventory_generated_at=NOW,
        approved_by="staging-owner",
        approved_at=NOW,
        reason="没有对应 inventory 的批准",
    )

    with pytest.raises(WorklineMigrationMatrixInvariantError, match="没有对应 inventory"):
        WorklineMigrationMatrixService(clock=lambda: NOW).build_matrix(
            inventories=(production,),
            approvals=(_approval(production), orphan_approval),
            required_environments=("production",),
        )
