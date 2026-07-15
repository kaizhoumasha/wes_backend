"""作业线迁移清单分类与确定性摘要服务。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import ValidationError

from src.app.runtime.capability_catalog import list_workline_capability_definitions
from src.app.runtime.orchestration.repository_wiring import workline_repository
from src.app.runtime.runtime_capability_catalog import RUNTIME_CAPABILITY_PROVIDER_PROFILES
from src.app.workline.models import (
    WorkLine,
    WorklineMigrationInventoryIssue,
    WorklineMigrationInventoryIssueCode,
    WorklineMigrationInventoryItem,
    WorklineMigrationInventoryReport,
    WorklineMigrationInventorySeverity,
    WorklineProviderProfileInventoryItem,
    WorklineRuntimeReferenceSample,
    WorklineRuntimeReferenceSummary,
    WorklineRuntimeReferenceType,
)
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_SUMMARY_KEYS = frozenset({"count", "sample", "by_type"})
_BY_TYPE_KEYS = frozenset({"sessions", "commands", "outboxes", "inboxes", "runtime_holds"})
_MISSING = object()


class WorklineMigrationInventoryLimitExceeded(RuntimeError):
    """清单规模超出逐条摘要查询的安全上限。"""


class WorklineMigrationInventoryInvariantError(RuntimeError):
    """生产源数据无法形成严格迁移清单合同时抛出。"""


class _InventoryRepository(Protocol):
    async def get_list(self, db: AsyncSession, **kwargs: Any) -> tuple[int, list[Any]]: ...

    async def get_unfinished_workload_summary(self, db: AsyncSession, workline_id: int) -> dict[str, Any]: ...


def _load_provider_profiles() -> Iterable[Any]:
    return RUNTIME_CAPABILITY_PROVIDER_PROFILES.values()


class WorklineMigrationInventoryService:
    """在调用方持有的只读快照中生成迁移清单。

    caller-owned REPEATABLE READ + READ ONLY snapshot
        -> build_report -> repository/catalog -> canonical digest

    Service 不 begin / commit / rollback；事务边界始终由调用方管理。
    """

    def __init__(
        self,
        *,
        repository: _InventoryRepository = workline_repository,
        capability_definitions_loader: Callable[[], Iterable[Any]] = list_workline_capability_definitions,
        provider_profile_loader: Callable[[], Iterable[Any]] = _load_provider_profiles,
        clock: Callable[[], datetime] = timezone.now_utc,
        max_worklines: int = 100,
    ) -> None:
        if type(max_worklines) is not int or max_worklines < 1 or max_worklines > 100:
            raise ValueError("max_worklines 必须为 1..100，禁止临时放宽迁移清单安全上限")
        self.repository = repository
        self._capability_definitions_loader = capability_definitions_loader
        self._provider_profile_loader = provider_profile_loader
        self._clock = clock
        self._max_worklines = max_worklines

    async def build_report(self, db: AsyncSession, *, environment: str) -> WorklineMigrationInventoryReport:
        total, source_worklines = await self.repository.get_list(
            db,
            limit=self._max_worklines + 1,
            offset=0,
            order_by_raw=[WorkLine.line_code, WorkLine.id],
        )
        if total > self._max_worklines or len(source_worklines) > self._max_worklines:
            raise WorklineMigrationInventoryLimitExceeded(
                "作业线迁移清单超过 100 条；必须先实现 bulk summary port，禁止截断或临时放宽上限"
            )

        capability_catalog = self._build_capability_catalog(self._capability_definitions_loader())
        provider_catalog = self._build_provider_catalog(self._provider_profile_loader())
        items: list[WorklineMigrationInventoryItem] = []
        for source in source_worklines:
            workline_id = self._strict_source_int(source, "id")
            summary = await self.repository.get_unfinished_workload_summary(db, workline_id)
            items.append(self._build_item(source, summary, capability_catalog))

        ordered_items = tuple(sorted(items, key=lambda item: (item.line_code, item.workline_id)))
        ordered_issues = tuple(
            sorted(
                (issue for item in ordered_items for issue in item.issues),
                key=self._issue_sort_key,
            )
        )
        foundation_ready = not any(
            issue.severity is WorklineMigrationInventorySeverity.BLOCKER for issue in ordered_issues
        )
        generated_at = self._utc_now()
        try:
            normalized_report = WorklineMigrationInventoryReport(
                environment=environment,
                generated_at=generated_at,
                inventory_digest="0" * 64,
                foundation_ready=foundation_ready,
                worklines=ordered_items,
                provider_profile_catalog=provider_catalog,
                issues=ordered_issues,
            )
        except ValidationError as exc:
            raise WorklineMigrationInventoryInvariantError(f"报告字段不满足迁移清单合同: {exc}") from exc
        digest_payload = normalized_report.model_dump(
            mode="json",
            exclude={"generated_at", "inventory_digest"},
        )
        return normalized_report.model_copy(update={"inventory_digest": self._digest(digest_payload)})

    @staticmethod
    def _build_capability_catalog(definitions: Iterable[Any]) -> dict[str, Any]:
        catalog: dict[str, Any] = {}
        for definition in definitions:
            capability_key = getattr(definition, "capability_key", _MISSING)
            contract_version = getattr(definition, "contract_version", _MISSING)
            if not WorklineMigrationInventoryService._non_blank_string(capability_key):
                raise WorklineMigrationInventoryInvariantError("capability definition 缺少非空 capability_key")
            if not WorklineMigrationInventoryService._non_blank_string(contract_version):
                raise WorklineMigrationInventoryInvariantError(
                    f"capability definition {capability_key!r} 缺少非空 contract_version"
                )
            if capability_key in catalog:
                raise WorklineMigrationInventoryInvariantError(f"capability catalog 重复 key: {capability_key}")
            catalog[capability_key] = definition
        return catalog

    @staticmethod
    def _build_provider_catalog(profiles: Iterable[Any]) -> tuple[WorklineProviderProfileInventoryItem, ...]:
        items: list[WorklineProviderProfileInventoryItem] = []
        provider_codes: set[str] = set()
        for profile in profiles:
            source = {
                "provider_code": getattr(profile, "provider_code", _MISSING),
                "contract_version": getattr(profile, "contract_version", _MISSING),
                "environment": getattr(profile, "environment", _MISSING),
                "runtime_capabilities_query": WorklineMigrationInventoryService._normalize_provider_capabilities(
                    profile, "runtime_capabilities_query"
                ),
                "runtime_capabilities_effect": WorklineMigrationInventoryService._normalize_provider_capabilities(
                    profile, "runtime_capabilities_effect"
                ),
            }
            try:
                item = WorklineProviderProfileInventoryItem(**source)
            except ValidationError as exc:
                raise WorklineMigrationInventoryInvariantError(f"provider profile 不满足迁移清单合同: {exc}") from exc
            if item.provider_code in provider_codes:
                raise WorklineMigrationInventoryInvariantError(
                    f"provider profile 重复 provider_code: {item.provider_code}"
                )
            provider_codes.add(item.provider_code)
            items.append(item)
        return tuple(sorted(items, key=lambda item: item.provider_code))

    @staticmethod
    def _normalize_provider_capabilities(profile: Any, field: str) -> tuple[str, ...]:
        capabilities = getattr(profile, field, _MISSING)
        if not isinstance(capabilities, (list, tuple)):
            raise WorklineMigrationInventoryInvariantError(f"provider profile {field} 必须为 list/tuple")
        if any(type(capability) is not str or not capability.strip() for capability in capabilities):
            raise WorklineMigrationInventoryInvariantError(f"provider profile {field} 元素必须为非空字符串")
        return tuple(sorted(capability.strip() for capability in capabilities))

    @staticmethod
    def _build_item(
        source: Any,
        raw_summary: Any,
        capability_catalog: Mapping[str, Any],
    ) -> WorklineMigrationInventoryItem:
        workline_id = WorklineMigrationInventoryService._strict_source_int(source, "id")
        line_code = getattr(source, "line_code", _MISSING)
        is_active = getattr(source, "is_active", _MISSING)
        plugin_key = getattr(source, "plugin_key", _MISSING)
        configured_version = getattr(source, "contract_version", _MISSING)
        run_mode_source = getattr(source, "run_mode", _MISSING)
        run_mode = getattr(run_mode_source, "value", run_mode_source)
        if plugin_key is _MISSING or configured_version is _MISSING:
            raise WorklineMigrationInventoryInvariantError(f"WorkLine {workline_id} 缺少插件配置字段")

        runtime_references = WorklineMigrationInventoryService._normalize_summary(raw_summary, workline_id)
        catalog_definition = capability_catalog.get(plugin_key) if isinstance(plugin_key, str) else None
        catalog_version = None if catalog_definition is None else catalog_definition.contract_version
        issues = WorklineMigrationInventoryService._classify_issues(
            workline_id=workline_id,
            line_code=line_code,
            is_active=is_active,
            plugin_key=plugin_key,
            configured_version=configured_version,
            catalog_version=catalog_version,
            reference_total=runtime_references.total,
        )
        try:
            return WorklineMigrationInventoryItem(
                workline_id=workline_id,
                line_code=line_code,
                is_active=is_active,
                plugin_key=plugin_key,
                configured_contract_version=configured_version,
                catalog_contract_version=catalog_version,
                run_mode=run_mode,
                runtime_references=runtime_references,
                foundation_ready=not any(
                    issue.severity is WorklineMigrationInventorySeverity.BLOCKER for issue in issues
                ),
                issues=issues,
            )
        except ValidationError as exc:
            raise WorklineMigrationInventoryInvariantError(
                f"WorkLine {workline_id} 源字段不满足迁移清单合同: {exc}"
            ) from exc

    @staticmethod
    def _normalize_summary(raw_summary: Any, workline_id: int) -> WorklineRuntimeReferenceSummary:
        if not isinstance(raw_summary, Mapping) or set(raw_summary) != _SUMMARY_KEYS:
            raise WorklineMigrationInventoryInvariantError(f"WorkLine {workline_id} summary 顶层键不精确")
        raw_by_type = raw_summary["by_type"]
        if not isinstance(raw_by_type, Mapping) or set(raw_by_type) != _BY_TYPE_KEYS:
            raise WorklineMigrationInventoryInvariantError(f"WorkLine {workline_id} summary.by_type 键不精确")
        by_type: dict[str, int] = {}
        for key in _BY_TYPE_KEYS:
            value = raw_by_type[key]
            if type(value) is not int or value < 0:
                raise WorklineMigrationInventoryInvariantError(
                    f"WorkLine {workline_id} summary.by_type.{key} 必须为非负严格整数"
                )
            by_type[key] = value
        total = raw_summary["count"]
        if type(total) is not int or total < 0:
            raise WorklineMigrationInventoryInvariantError(f"WorkLine {workline_id} summary.count 必须为非负严格整数")
        if total != sum(by_type.values()):
            raise WorklineMigrationInventoryInvariantError(f"WorkLine {workline_id} summary.count 与分类合计不一致")
        raw_sample = raw_summary["sample"]
        if total == 0 and raw_sample is not None:
            raise WorklineMigrationInventoryInvariantError(f"WorkLine {workline_id} 零引用 summary 不得包含 sample")
        sample = (
            None if raw_sample is None else WorklineMigrationInventoryService._normalize_sample(raw_sample, workline_id)
        )
        try:
            return WorklineRuntimeReferenceSummary(
                **by_type,
                total=total,
                sample=sample,
            )
        except ValidationError as exc:
            raise WorklineMigrationInventoryInvariantError(
                f"WorkLine {workline_id} summary 不满足迁移清单合同: {exc}"
            ) from exc

    @staticmethod
    def _normalize_sample(raw_sample: Any, workline_id: int) -> WorklineRuntimeReferenceSample:
        if not isinstance(raw_sample, Mapping):
            raise WorklineMigrationInventoryInvariantError(f"WorkLine {workline_id} summary.sample 必须为 mapping")
        sample_type = raw_sample.get("type")
        definitions = {
            "session": (WorklineRuntimeReferenceType.SESSION, "session_code"),
            "command": (WorklineRuntimeReferenceType.COMMAND, "command_code"),
            "outbox": (WorklineRuntimeReferenceType.OUTBOX, "dispatch_key"),
            "inbox": (WorklineRuntimeReferenceType.INBOX, "inbox_id"),
            "runtime_hold": (WorklineRuntimeReferenceType.RUNTIME_HOLD, "count"),
        }
        definition = definitions.get(sample_type)
        if definition is None:
            raise WorklineMigrationInventoryInvariantError(f"WorkLine {workline_id} summary.sample.type 未知")
        reference_type, reference_field = definition
        raw_status = raw_sample.get("status", _MISSING)
        status = raw_status.value if isinstance(raw_status, Enum) else raw_status
        reference_source = raw_sample.get(reference_field, _MISSING)
        if type(status) is not str or not status.strip():
            raise WorklineMigrationInventoryInvariantError(f"WorkLine {workline_id} summary.sample.status 必须非空")
        status = status.strip()
        if sample_type == "runtime_hold":
            if type(reference_source) is not int or reference_source <= 0:
                raise WorklineMigrationInventoryInvariantError(
                    f"WorkLine {workline_id} runtime_hold sample.count 必须为正严格整数"
                )
            reference = f"count:{reference_source}"
        elif sample_type == "inbox":
            if type(reference_source) is not int or reference_source <= 0:
                raise WorklineMigrationInventoryInvariantError(
                    f"WorkLine {workline_id} inbox sample.inbox_id 必须为正严格整数"
                )
            reference = str(reference_source)
        else:
            if type(reference_source) is not str or not reference_source.strip():
                raise WorklineMigrationInventoryInvariantError(
                    f"WorkLine {workline_id} summary.sample.{reference_field} 必须为非空字符串"
                )
            reference = reference_source.strip()
        try:
            return WorklineRuntimeReferenceSample(type=reference_type, reference=reference, status=status)
        except ValidationError as exc:
            raise WorklineMigrationInventoryInvariantError(
                f"WorkLine {workline_id} sample 不满足迁移清单合同: {exc}"
            ) from exc

    @staticmethod
    def _classify_issues(
        *,
        workline_id: int,
        line_code: Any,
        is_active: Any,
        plugin_key: Any,
        configured_version: Any,
        catalog_version: str | None,
        reference_total: int,
    ) -> tuple[WorklineMigrationInventoryIssue, ...]:
        issue_specs: list[tuple[WorklineMigrationInventoryIssueCode, str]] = []
        if is_active is True and plugin_key is None:
            issue_specs.append((WorklineMigrationInventoryIssueCode.ACTIVE_WITHOUT_PLUGIN, "启用作业线未配置插件"))
        if is_active is True and configured_version is None:
            issue_specs.append(
                (WorklineMigrationInventoryIssueCode.ACTIVE_WITHOUT_CONTRACT_VERSION, "启用作业线未配置合同版本")
            )
        if plugin_key is not None and catalog_version is None:
            issue_specs.append((WorklineMigrationInventoryIssueCode.UNKNOWN_PLUGIN, "配置插件不在 capability catalog"))
        if plugin_key is not None and catalog_version is not None and configured_version != catalog_version:
            issue_specs.append(
                (WorklineMigrationInventoryIssueCode.CONTRACT_VERSION_MISMATCH, "配置合同版本与 catalog 不一致")
            )
        if reference_total > 0:
            issue_specs.append(
                (WorklineMigrationInventoryIssueCode.RUNTIME_REFERENCES_PRESENT, "仍存在未完成运行态引用")
            )
        try:
            issues = tuple(
                WorklineMigrationInventoryIssue(
                    code=code,
                    severity=WorklineMigrationInventorySeverity.BLOCKER,
                    message=message,
                    workline_id=workline_id,
                    line_code=line_code,
                )
                for code, message in issue_specs
            )
        except ValidationError as exc:
            raise WorklineMigrationInventoryInvariantError(
                f"WorkLine {workline_id} issue 源字段不满足迁移清单合同: {exc}"
            ) from exc
        return tuple(sorted(issues, key=WorklineMigrationInventoryService._issue_sort_key))

    @staticmethod
    def _issue_sort_key(issue: WorklineMigrationInventoryIssue) -> tuple[str, str, int, str]:
        return (
            issue.severity.value,
            issue.code.value,
            issue.workline_id if issue.workline_id is not None else -1,
            issue.line_code or "",
        )

    @staticmethod
    def _digest(payload: Mapping[str, Any]) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _utc_now(self) -> datetime:
        generated_at = self._clock()
        if (
            not isinstance(generated_at, datetime)
            or generated_at.tzinfo is None
            or generated_at.utcoffset() != timedelta(0)
        ):
            raise WorklineMigrationInventoryInvariantError("clock 必须返回 aware UTC datetime")
        return generated_at

    @staticmethod
    def _strict_source_int(source: Any, field: str) -> int:
        value = getattr(source, field, _MISSING)
        if type(value) is not int:
            raise WorklineMigrationInventoryInvariantError(f"WorkLine.{field} 必须为严格整数")
        return value

    @staticmethod
    def _non_blank_string(value: Any) -> bool:
        return isinstance(value, str) and bool(value.strip())


workline_migration_inventory_service = WorklineMigrationInventoryService()


__all__ = [
    "WorklineMigrationInventoryInvariantError",
    "WorklineMigrationInventoryLimitExceeded",
    "WorklineMigrationInventoryService",
    "workline_migration_inventory_service",
]
