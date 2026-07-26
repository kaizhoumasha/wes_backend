"""跨环境 WorkLine 迁移矩阵聚合与批准门禁。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from src.app.contracts.external_contract_profile_catalog import list_external_contract_profiles
from src.app.runtime.system_capabilities.generated_index import (
    SYSTEM_CAPABILITY_INDEX,
    SYSTEM_CAPABILITY_INDEX_DIGEST,
)
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX, WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.workline.models import (
    WorklineInventoryApprovalEvidence,
    WorklineMigrationInventoryIssue,
    WorklineMigrationInventoryReport,
    WorklineMigrationInventorySeverity,
    WorklineMigrationMatrixIssue,
    WorklineMigrationMatrixIssueCode,
    WorklineMigrationMatrixReport,
)
from src.utils.timezone import timezone

from .migration_inventory_service import WorklineMigrationInventoryService

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


class WorklineMigrationMatrixInvariantError(RuntimeError):
    """输入报告或批准证据无法形成唯一、可信矩阵。"""


class WorklineMigrationMatrixPreflightError(RuntimeError):
    """矩阵不能作为后续切换 preflight 输入。"""


class WorklineMigrationMatrixService:
    """聚合已生成的环境报告，不访问数据库、不执行 freeze/drain/cutover。"""

    def __init__(
        self,
        *,
        expected_plugin_index_digest: str = WORKLINE_PLUGIN_INDEX_DIGEST,
        expected_system_capability_index_digest: str = SYSTEM_CAPABILITY_INDEX_DIGEST,
        clock: Callable[[], datetime] = timezone.now_utc,
    ) -> None:
        self._expected_plugin_index_digest = expected_plugin_index_digest
        self._expected_system_capability_index_digest = expected_system_capability_index_digest
        self._clock = clock

    def build_matrix(
        self,
        *,
        inventories: Iterable[WorklineMigrationInventoryReport],
        approvals: Iterable[WorklineInventoryApprovalEvidence],
        required_environments: Iterable[str],
    ) -> WorklineMigrationMatrixReport:
        required = self._normalize_required_environments(required_environments)
        ordered_inventories = tuple(sorted(inventories, key=lambda report: report.environment))
        inventory_by_environment = cast(
            "dict[str, WorklineMigrationInventoryReport]",
            self._unique_by_environment(ordered_inventories, source="inventory"),
        )
        for report in ordered_inventories:
            self._verify_inventory_digest(report)
            self._verify_inventory_derived_state(
                report,
                verify_deployment_projection=(
                    report.plugin_index_digest == self._expected_plugin_index_digest
                    and report.system_capability_index_digest == self._expected_system_capability_index_digest
                ),
            )

        ordered_approvals = tuple(sorted(approvals, key=lambda evidence: evidence.environment))
        approval_by_environment = cast(
            "dict[str, WorklineInventoryApprovalEvidence]",
            self._unique_by_environment(ordered_approvals, source="approval"),
        )
        approvals_without_inventory = sorted(set(approval_by_environment) - set(inventory_by_environment))
        if approvals_without_inventory:
            raise WorklineMigrationMatrixInvariantError(
                f"approval environment 没有对应 inventory: {approvals_without_inventory}"
            )
        generated_at = self._utc_now()
        self._verify_artifact_times(
            inventories=ordered_inventories,
            approvals=ordered_approvals,
            evaluation_time=generated_at,
        )
        issues = self._build_issues(
            required=required,
            inventory_by_environment=inventory_by_environment,
            approval_by_environment=approval_by_environment,
            expected_plugin_index_digest=self._expected_plugin_index_digest,
            expected_system_capability_index_digest=self._expected_system_capability_index_digest,
        )
        inventory_gate_ready = not issues
        try:
            normalized = WorklineMigrationMatrixReport(
                generated_at=generated_at,
                matrix_digest="0" * 64,
                inventory_gate_ready=inventory_gate_ready,
                required_environments=required,
                inventories=ordered_inventories,
                approvals=ordered_approvals,
                issues=issues,
            )
        except ValidationError as exc:
            raise WorklineMigrationMatrixInvariantError(f"迁移矩阵字段不满足合同: {exc}") from exc
        payload = normalized.model_dump(mode="python")
        payload["matrix_digest"] = self._matrix_digest(normalized)
        try:
            return WorklineMigrationMatrixReport.model_validate(payload)
        except ValidationError as exc:
            raise WorklineMigrationMatrixInvariantError(f"最终迁移矩阵不满足合同: {exc}") from exc

    def assert_approved_matrix(
        self,
        matrix: WorklineMigrationMatrixReport,
        *,
        expected_matrix_digest: str,
        max_inventory_age: timedelta,
    ) -> None:
        if expected_matrix_digest is None:
            raise WorklineMigrationMatrixPreflightError("必须提供部署固定值作为迁移矩阵摘要")
        if not isinstance(max_inventory_age, timedelta) or max_inventory_age <= timedelta(0):
            raise WorklineMigrationMatrixPreflightError("inventory 最大有效期必须为正 timedelta")
        evaluation_time = self._utc_now()
        expired_environments = sorted(
            report.environment
            for report in matrix.inventories
            if evaluation_time - report.generated_at > max_inventory_age
        )
        if expired_environments:
            raise WorklineMigrationMatrixPreflightError(
                f"inventory 报告已过期，必须重新生成并批准: {expired_environments}"
            )
        for report in matrix.inventories:
            try:
                self._verify_inventory_digest(report)
            except WorklineMigrationMatrixInvariantError as exc:
                raise WorklineMigrationMatrixPreflightError(str(exc)) from exc
        actual_digest = self._matrix_digest(matrix)
        if matrix.matrix_digest != actual_digest:
            raise WorklineMigrationMatrixPreflightError("迁移矩阵内容与自身摘要不一致")
        if matrix.matrix_digest != expected_matrix_digest:
            raise WorklineMigrationMatrixPreflightError("迁移矩阵摘要与部署固定值不一致")
        try:
            canonical = self.build_matrix(
                inventories=matrix.inventories,
                approvals=matrix.approvals,
                required_environments=matrix.required_environments,
            )
        except WorklineMigrationMatrixInvariantError as exc:
            raise WorklineMigrationMatrixPreflightError(str(exc)) from exc
        if (
            matrix.inventory_gate_ready != canonical.inventory_gate_ready
            or matrix.issues != canonical.issues
            or matrix.required_environments != canonical.required_environments
            or matrix.inventories != canonical.inventories
            or matrix.approvals != canonical.approvals
        ):
            raise WorklineMigrationMatrixPreflightError("迁移矩阵派生状态与原始输入不一致")
        if not matrix.inventory_gate_ready:
            raise WorklineMigrationMatrixPreflightError("跨环境 inventory gate 未通过")

    @staticmethod
    def _normalize_required_environments(environments: Iterable[str]) -> tuple[str, ...]:
        normalized: list[str] = []
        for environment in environments:
            if type(environment) is not str or not environment.strip():
                raise WorklineMigrationMatrixInvariantError("required environment 必须为非空字符串")
            normalized.append(environment.strip())
        if not normalized:
            raise WorklineMigrationMatrixInvariantError("required environments 不得为空")
        if len(normalized) != len(set(normalized)):
            raise WorklineMigrationMatrixInvariantError("required environments 不得重复")
        return tuple(sorted(normalized))

    @staticmethod
    def _unique_by_environment(items: Iterable[object], *, source: str) -> dict[str, object]:
        by_environment: dict[str, object] = {}
        for item in items:
            environment = getattr(item, "environment", None)
            if environment in by_environment:
                raise WorklineMigrationMatrixInvariantError(f"{source} environment 重复: {environment}")
            by_environment[environment] = item
        return by_environment

    @classmethod
    def _verify_inventory_digest(cls, report: WorklineMigrationInventoryReport) -> None:
        if report.inventory_digest != cls._inventory_digest(report):
            raise WorklineMigrationMatrixInvariantError(f"环境 {report.environment} inventory_digest 与报告内容不一致")

    @classmethod
    def _verify_inventory_derived_state(
        cls,
        report: WorklineMigrationInventoryReport,
        *,
        verify_deployment_projection: bool,
    ) -> None:
        for item in report.worklines:
            runtime_references = item.runtime_references
            reference_total = sum(
                (
                    runtime_references.sessions,
                    runtime_references.commands,
                    runtime_references.outboxes,
                    runtime_references.inboxes,
                    runtime_references.runtime_holds,
                )
            )
            if runtime_references.total != reference_total:
                raise WorklineMigrationMatrixInvariantError(
                    f"环境 {report.environment} WorkLine {item.workline_id} 运行引用摘要不一致"
                )

            declared_ready = not any(
                issue.severity is WorklineMigrationInventorySeverity.BLOCKER for issue in item.issues
            )
            if item.foundation_ready is not declared_ready:
                raise WorklineMigrationMatrixInvariantError(
                    f"环境 {report.environment} WorkLine {item.workline_id} inventory 就绪状态与问题清单不一致"
                )
            if not verify_deployment_projection:
                continue

            plugin_key = item.plugin_key
            configured_version = item.configured_contract_version
            capability_projection = WorklineMigrationInventoryService.derive_capability_projection(
                workline_id=item.workline_id,
                plugin_key=plugin_key,
                configured_version=configured_version,
                capability_definitions=WORKLINE_PLUGIN_INDEX.values(),
                system_capability_definitions=SYSTEM_CAPABILITY_INDEX.values(),
            )
            if (
                item.catalog_contract_version != capability_projection.catalog_contract_version
                or item.capability_requirements != capability_projection.capability_requirements
            ):
                raise WorklineMigrationMatrixInvariantError(
                    f"环境 {report.environment} WorkLine {item.workline_id} inventory 能力派生状态不一致"
                )
            expected_issues = WorklineMigrationInventoryService.classify_item_issues(
                workline_id=item.workline_id,
                line_code=item.line_code,
                is_active=item.is_active,
                plugin_key=plugin_key,
                configured_version=configured_version,
                plugin_known=capability_projection.plugin_known,
                catalog_identity_matched=capability_projection.catalog_identity_matched,
                active_binding_id=item.active_plugin_binding_id,
                active_binding_version=item.active_plugin_binding_version,
                active_config_hash=item.active_plugin_config_hash,
                active_index_digest=item.active_plugin_index_digest,
                reference_total=reference_total,
            )
            expected_ready = not any(
                issue.severity is WorklineMigrationInventorySeverity.BLOCKER for issue in expected_issues
            )
            if item.issues != expected_issues or item.foundation_ready is not expected_ready:
                raise WorklineMigrationMatrixInvariantError(
                    f"环境 {report.environment} WorkLine {item.workline_id} inventory 派生状态不一致"
                )

        expected_issues = tuple(
            sorted(
                (issue for item in report.worklines for issue in item.issues),
                key=cls._inventory_issue_sort_key,
            )
        )
        expected_ready = not any(
            issue.severity is WorklineMigrationInventorySeverity.BLOCKER for issue in expected_issues
        )
        if report.issues != expected_issues or report.foundation_ready is not expected_ready:
            raise WorklineMigrationMatrixInvariantError(f"环境 {report.environment} inventory 报告派生状态不一致")

    @staticmethod
    def _inventory_issue_sort_key(issue: WorklineMigrationInventoryIssue) -> tuple[str, str, int, str]:
        return (
            issue.severity.value,
            issue.code.value,
            issue.workline_id if issue.workline_id is not None else -1,
            issue.line_code or "",
        )

    @staticmethod
    def _verify_artifact_times(
        *,
        inventories: tuple[WorklineMigrationInventoryReport, ...],
        approvals: tuple[WorklineInventoryApprovalEvidence, ...],
        evaluation_time: datetime,
    ) -> None:
        future_inventories = sorted(
            report.environment for report in inventories if report.generated_at > evaluation_time
        )
        future_approvals = sorted(
            approval.environment for approval in approvals if approval.approved_at > evaluation_time
        )
        if future_inventories or future_approvals:
            raise WorklineMigrationMatrixInvariantError(
                f"inventory/approval 时间戳位于未来: inventories={future_inventories}, approvals={future_approvals}"
            )

    def _utc_now(self) -> datetime:
        generated_at = self._clock()
        if (
            not isinstance(generated_at, datetime)
            or generated_at.tzinfo is None
            or generated_at.utcoffset() != timedelta(0)
        ):
            raise WorklineMigrationMatrixInvariantError("clock 必须返回 aware UTC datetime")
        return generated_at

    @classmethod
    def _build_issues(
        cls,
        *,
        required: tuple[str, ...],
        inventory_by_environment: dict[str, WorklineMigrationInventoryReport],
        approval_by_environment: dict[str, WorklineInventoryApprovalEvidence],
        expected_plugin_index_digest: str,
        expected_system_capability_index_digest: str,
    ) -> tuple[WorklineMigrationMatrixIssue, ...]:
        issues: list[WorklineMigrationMatrixIssue] = []
        required_set = set(required)
        issues.extend(
            [
                WorklineMigrationMatrixIssue(
                    code=WorklineMigrationMatrixIssueCode.MISSING_REQUIRED_ENVIRONMENT,
                    message="缺少必需环境 inventory",
                    environment=environment,
                )
                for environment in sorted(required_set - set(inventory_by_environment))
            ]
        )
        issues.extend(
            [
                WorklineMigrationMatrixIssue(
                    code=WorklineMigrationMatrixIssueCode.UNEXPECTED_ENVIRONMENT,
                    message="inventory 环境不在本次批准范围",
                    environment=environment,
                )
                for environment in sorted(set(inventory_by_environment) - required_set)
            ]
        )

        reports = tuple(inventory_by_environment.values())
        plugin_digests = {report.plugin_index_digest for report in reports}
        capability_digests = {report.system_capability_index_digest for report in reports}
        if reports and (
            plugin_digests != {expected_plugin_index_digest}
            or capability_digests != {expected_system_capability_index_digest}
        ):
            issues.append(
                WorklineMigrationMatrixIssue(
                    code=WorklineMigrationMatrixIssueCode.INDEX_DIGEST_MISMATCH,
                    message="各环境 Plugin/System Capability 生成索引摘要不一致、缺失或不匹配部署期望",
                )
            )

        expected_provider_catalog = WorklineMigrationInventoryService.derive_provider_profile_catalog(
            list_external_contract_profiles()
        )
        for environment, report in sorted(inventory_by_environment.items()):
            if report.provider_profile_catalog != expected_provider_catalog:
                issues.append(
                    WorklineMigrationMatrixIssue(
                        code=WorklineMigrationMatrixIssueCode.PROVIDER_PROFILE_CATALOG_MISMATCH,
                        message="Provider Profile 目录与当前部署不一致",
                        environment=environment,
                    )
                )
            if not report.foundation_ready:
                issues.append(
                    WorklineMigrationMatrixIssue(
                        code=WorklineMigrationMatrixIssueCode.INVENTORY_NOT_READY,
                        message="单环境 inventory foundation 未通过",
                        environment=environment,
                    )
                )
            approval = approval_by_environment.get(environment)
            if approval is None:
                issues.append(
                    WorklineMigrationMatrixIssue(
                        code=WorklineMigrationMatrixIssueCode.APPROVAL_MISSING,
                        message="缺少绑定当前 inventory digest 的批准证据",
                        environment=environment,
                    )
                )
            elif approval.inventory_digest != report.inventory_digest:
                issues.append(
                    WorklineMigrationMatrixIssue(
                        code=WorklineMigrationMatrixIssueCode.APPROVAL_DIGEST_MISMATCH,
                        message="批准证据绑定的 inventory digest 已过期",
                        environment=environment,
                    )
                )
            elif approval.inventory_generated_at != report.generated_at or approval.approved_at < report.generated_at:
                issues.append(
                    WorklineMigrationMatrixIssue(
                        code=WorklineMigrationMatrixIssueCode.APPROVAL_REPORT_MISMATCH,
                        message="批准证据未绑定当前 inventory 报告实例或批准时间早于报告生成时间",
                        environment=environment,
                    )
                )
        return tuple(sorted(issues, key=lambda issue: (issue.code.value, issue.environment or "")))

    @staticmethod
    def _inventory_digest(report: WorklineMigrationInventoryReport) -> str:
        payload = report.model_dump(mode="json", exclude={"generated_at", "inventory_digest"})
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _matrix_digest(matrix: WorklineMigrationMatrixReport) -> str:
        payload = {
            "schema_version": matrix.schema_version,
            "inventory_gate_ready": matrix.inventory_gate_ready,
            "required_environments": matrix.required_environments,
            "inventories": tuple(
                {
                    "environment": report.environment,
                    "inventory_digest": report.inventory_digest,
                    "plugin_index_digest": report.plugin_index_digest,
                    "system_capability_index_digest": report.system_capability_index_digest,
                }
                for report in matrix.inventories
            ),
            "approvals": tuple(approval.model_dump(mode="json") for approval in matrix.approvals),
            "issues": tuple(issue.model_dump(mode="json") for issue in matrix.issues),
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


workline_migration_matrix_service = WorklineMigrationMatrixService()


__all__ = [
    "WorklineMigrationMatrixInvariantError",
    "WorklineMigrationMatrixPreflightError",
    "WorklineMigrationMatrixService",
    "workline_migration_matrix_service",
]
