"""作业线迁移清单分类与确定性摘要服务。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import ValidationError

from src.app.contracts.external_contract_profile import ExternalContractProfile, WmsExternalContractProfile
from src.app.contracts.external_contract_profile_catalog import list_external_contract_profiles
from src.app.runtime.orchestration.repository_wiring import workline_repository
from src.app.runtime.system_capabilities.generated_index import (
    SYSTEM_CAPABILITY_INDEX,
    SYSTEM_CAPABILITY_INDEX_DIGEST,
)
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.runtime.workline_plugins.registry import list_workline_capability_definitions
from src.app.workline.models import (
    WorkLine,
    WorklineCapabilityRequirementInventoryItem,
    WorklineMigrationInventoryIssue,
    WorklineMigrationInventoryIssueCode,
    WorklineMigrationInventoryItem,
    WorklineMigrationInventoryReport,
    WorklineMigrationInventorySeverity,
    WorklineProviderProfileInventoryItem,
    WorklineRuntimeExtensionReference,
    WorklineRuntimeReferenceSample,
    WorklineRuntimeReferenceSummary,
    WorklineRuntimeReferenceType,
)
from src.app.workline.repositories.plugin_binding_repository import workline_plugin_binding_repository
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
    async def get_list(
        self,
        db: AsyncSession,
        *,
        limit: int,
        offset: int,
        order_by_raw: list[Any],
    ) -> tuple[int, Sequence[Any]]: ...

    async def get_unfinished_workload_summary(self, db: AsyncSession, workline_id: int) -> dict[str, Any]: ...


class _ExtensionReferenceRepository(Protocol):
    async def list_runtime_extension_references_by_workline_ids(
        self,
        db: AsyncSession,
        workline_ids: tuple[int, ...],
    ) -> dict[int, list[dict[str, Any]]]: ...


@dataclass(frozen=True, slots=True)
class _NormalizedCapabilityDefinition:
    capability_key: str
    contract_version: str
    allowed_capabilities: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _NormalizedSystemCapabilityDefinition:
    capability_key: str
    contract_version: str
    mode: str
    admission: str
    required_ports: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _CapabilityProjection:
    plugin_known: bool
    catalog_identity_matched: bool
    catalog_contract_version: str | None
    capability_requirements: tuple[WorklineCapabilityRequirementInventoryItem, ...]


def _load_provider_profiles() -> Iterable[Any]:
    return list_external_contract_profiles()


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
        system_capability_definitions_loader: Callable[[], Iterable[Any]] = lambda: SYSTEM_CAPABILITY_INDEX.values(),
        provider_profile_loader: Callable[[], Iterable[Any]] = _load_provider_profiles,
        extension_reference_repository: _ExtensionReferenceRepository | None = None,
        clock: Callable[[], datetime] = timezone.now_utc,
        max_worklines: int = 100,
    ) -> None:
        if type(max_worklines) is not int or max_worklines < 1 or max_worklines > 100:
            raise ValueError("max_worklines 必须为 1..100，禁止临时放宽迁移清单安全上限")
        self.repository = repository
        self._capability_definitions_loader = capability_definitions_loader
        self._system_capability_definitions_loader = system_capability_definitions_loader
        self._provider_profile_loader = provider_profile_loader
        self._extension_reference_repository = extension_reference_repository
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
                f"作业线迁移清单超过 {self._max_worklines} 条；必须先实现 bulk summary port，禁止截断或临时放宽上限"
            )

        capability_catalog = self._build_capability_catalog(self._capability_definitions_loader())
        system_capability_catalog = self._build_system_capability_catalog(self._system_capability_definitions_loader())
        provider_catalog = self._build_provider_catalog(self._provider_profile_loader())
        workline_ids = tuple(sorted(self._strict_source_int(source, "id") for source in source_worklines))
        extension_references_by_workline = (
            {}
            if self._extension_reference_repository is None
            else await self._extension_reference_repository.list_runtime_extension_references_by_workline_ids(
                db, workline_ids
            )
        )
        items: list[WorklineMigrationInventoryItem] = []
        for source in source_worklines:
            workline_id = self._strict_source_int(source, "id")
            summary = await self.repository.get_unfinished_workload_summary(db, workline_id)
            extension_references = extension_references_by_workline.get(workline_id, [])
            items.append(
                self._build_item(
                    source,
                    summary,
                    capability_catalog,
                    system_capability_catalog,
                    extension_references,
                )
            )

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
                plugin_index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
                system_capability_index_digest=SYSTEM_CAPABILITY_INDEX_DIGEST,
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
        final_payload = normalized_report.model_dump(mode="python")
        final_payload["inventory_digest"] = self._digest(digest_payload)
        try:
            return WorklineMigrationInventoryReport.model_validate(final_payload)
        except ValidationError as exc:
            raise WorklineMigrationInventoryInvariantError(f"最终报告不满足迁移清单合同: {exc}") from exc

    @staticmethod
    def _build_capability_catalog(
        definitions: Iterable[Any],
    ) -> dict[tuple[str, str], _NormalizedCapabilityDefinition]:
        catalog: dict[tuple[str, str], _NormalizedCapabilityDefinition] = {}
        for definition in definitions:
            source_key = getattr(definition, "plugin_key", _MISSING)
            if source_key is _MISSING:
                # 测试注入只验证 inventory 的通用边界；生产 Definition 固定使用 plugin_key。
                source_key = getattr(definition, "capability_key", _MISSING)
            capability_key = WorklineMigrationInventoryService._normalize_catalog_string(source_key, "plugin_key")
            contract_version = WorklineMigrationInventoryService._normalize_catalog_string(
                getattr(definition, "contract_version", _MISSING), "contract_version"
            )
            identity = (capability_key, contract_version)
            if identity in catalog:
                raise WorklineMigrationInventoryInvariantError(
                    f"capability catalog 重复 identity: {capability_key}@{contract_version}"
                )
            catalog[identity] = _NormalizedCapabilityDefinition(
                capability_key=capability_key,
                contract_version=contract_version,
                allowed_capabilities=tuple(
                    sorted(
                        (
                            WorklineMigrationInventoryService._normalize_catalog_string(
                                capability_identity[0], "allowed capability key"
                            ),
                            WorklineMigrationInventoryService._normalize_catalog_string(
                                capability_identity[1], "allowed capability version"
                            ),
                        )
                        for capability_identity in getattr(definition, "allowed_capabilities", ())
                    )
                ),
            )
        return catalog

    @staticmethod
    def _build_system_capability_catalog(
        definitions: Iterable[Any],
    ) -> dict[tuple[str, str], _NormalizedSystemCapabilityDefinition]:
        catalog: dict[tuple[str, str], _NormalizedSystemCapabilityDefinition] = {}
        for definition in definitions:
            capability_key = WorklineMigrationInventoryService._normalize_catalog_string(
                getattr(definition, "capability_key", _MISSING), "system capability key"
            )
            contract_version = WorklineMigrationInventoryService._normalize_catalog_string(
                getattr(definition, "contract_version", _MISSING), "system capability version"
            )
            mode_source = getattr(definition, "mode", _MISSING)
            mode = WorklineMigrationInventoryService._normalize_catalog_string(
                mode_source.value if isinstance(mode_source, Enum) else mode_source,
                "system capability mode",
            )
            admission = WorklineMigrationInventoryService._normalize_catalog_string(
                getattr(definition, "admission", _MISSING), "system capability admission"
            )
            required_ports = tuple(
                sorted(
                    f"{port.__module__}.{port.__qualname__}"
                    for port in getattr(definition, "required_ports", ())
                    if isinstance(port, type)
                )
            )
            if len(required_ports) != len(getattr(definition, "required_ports", ())):
                raise WorklineMigrationInventoryInvariantError(
                    f"system capability {capability_key}@{contract_version} required_ports 必须为类型"
                )
            identity = (capability_key, contract_version)
            if identity in catalog:
                raise WorklineMigrationInventoryInvariantError(
                    f"system capability catalog 重复 identity: {capability_key}@{contract_version}"
                )
            catalog[identity] = _NormalizedSystemCapabilityDefinition(
                capability_key=capability_key,
                contract_version=contract_version,
                mode=mode,
                admission=admission,
                required_ports=required_ports,
            )
        return catalog

    @staticmethod
    def _build_provider_catalog(profiles: Iterable[Any]) -> tuple[WorklineProviderProfileInventoryItem, ...]:
        items: list[WorklineProviderProfileInventoryItem] = []
        provider_identities: set[tuple[str, str, str | None]] = set()
        for profile in profiles:
            if isinstance(profile, WmsExternalContractProfile):
                environment = None
            elif isinstance(profile, ExternalContractProfile):
                environment = profile.environment
            else:
                raise WorklineMigrationInventoryInvariantError(
                    f"provider profile 类型不在 closed union: {type(profile).__name__}"
                )
            source = {
                "provider_code": profile.provider_code,
                "contract_version": profile.contract_version,
                "environment": environment,
            }
            try:
                item = WorklineProviderProfileInventoryItem.model_validate(source)
            except ValidationError as exc:
                raise WorklineMigrationInventoryInvariantError(f"provider profile 不满足迁移清单合同: {exc}") from exc
            identity = (item.provider_code, item.contract_version, item.environment)
            if identity in provider_identities:
                raise WorklineMigrationInventoryInvariantError(f"provider profile 重复 identity: {identity}")
            provider_identities.add(identity)
            items.append(item)
        return tuple(
            sorted(
                items,
                key=lambda item: (item.provider_code, item.contract_version, item.environment or ""),
            )
        )

    @classmethod
    def derive_provider_profile_catalog(
        cls,
        profiles: Iterable[Any],
    ) -> tuple[WorklineProviderProfileInventoryItem, ...]:
        """从 Provider Profile definitions 生成唯一 inventory 投影。"""

        return cls._build_provider_catalog(profiles)

    @classmethod
    def derive_capability_projection(
        cls,
        *,
        workline_id: int,
        plugin_key: str | None,
        configured_version: str | None,
        capability_definitions: Iterable[Any],
        system_capability_definitions: Iterable[Any],
    ) -> _CapabilityProjection:
        """从 Plugin/System Capability definitions 生成唯一 inventory 投影。"""

        return cls._derive_capability_projection(
            workline_id=workline_id,
            plugin_key=plugin_key,
            configured_version=configured_version,
            capability_catalog=cls._build_capability_catalog(capability_definitions),
            system_capability_catalog=cls._build_system_capability_catalog(system_capability_definitions),
        )

    @staticmethod
    def _derive_capability_projection(
        *,
        workline_id: int,
        plugin_key: str | None,
        configured_version: str | None,
        capability_catalog: Mapping[tuple[str, str], _NormalizedCapabilityDefinition],
        system_capability_catalog: Mapping[tuple[str, str], _NormalizedSystemCapabilityDefinition],
    ) -> _CapabilityProjection:
        definitions_for_plugin = (
            tuple(
                definition
                for (catalog_plugin_key, _catalog_version), definition in capability_catalog.items()
                if catalog_plugin_key == plugin_key
            )
            if isinstance(plugin_key, str)
            else ()
        )
        catalog_definition = (
            capability_catalog.get((plugin_key, configured_version))
            if isinstance(plugin_key, str) and isinstance(configured_version, str)
            else definitions_for_plugin[0]
            if configured_version is None and len(definitions_for_plugin) == 1
            else None
        )
        capability_requirements: tuple[WorklineCapabilityRequirementInventoryItem, ...] = ()
        if catalog_definition is not None:
            missing_capabilities = tuple(
                identity
                for identity in catalog_definition.allowed_capabilities
                if identity not in system_capability_catalog
            )
            if missing_capabilities:
                raise WorklineMigrationInventoryInvariantError(
                    f"WorkLine {workline_id} plugin 引用未生成的 system capability: {missing_capabilities}"
                )
            capability_requirements = tuple(
                WorklineCapabilityRequirementInventoryItem(
                    capability_key=system_capability_catalog[identity].capability_key,
                    contract_version=system_capability_catalog[identity].contract_version,
                    mode=system_capability_catalog[identity].mode,
                    admission=system_capability_catalog[identity].admission,
                    required_ports=system_capability_catalog[identity].required_ports,
                )
                for identity in catalog_definition.allowed_capabilities
            )
        return _CapabilityProjection(
            plugin_known=bool(definitions_for_plugin),
            catalog_identity_matched=(
                isinstance(plugin_key, str)
                and isinstance(configured_version, str)
                and (plugin_key, configured_version) in capability_catalog
            ),
            catalog_contract_version=None if catalog_definition is None else catalog_definition.contract_version,
            capability_requirements=capability_requirements,
        )

    @staticmethod
    def _build_item(
        source: Any,
        raw_summary: Any,
        capability_catalog: Mapping[tuple[str, str], _NormalizedCapabilityDefinition],
        system_capability_catalog: Mapping[tuple[str, str], _NormalizedSystemCapabilityDefinition],
        raw_extension_references: Iterable[Mapping[str, Any]] = (),
    ) -> WorklineMigrationInventoryItem:
        workline_id = WorklineMigrationInventoryService._strict_source_int(source, "id")
        line_code = WorklineMigrationInventoryService._required_source_string(source, "line_code", workline_id)
        is_active = getattr(source, "is_active", _MISSING)
        if not isinstance(is_active, bool):
            raise WorklineMigrationInventoryInvariantError(f"WorkLine {workline_id} is_active 必须为 bool")
        plugin_key = WorklineMigrationInventoryService._optional_source_string(source, "plugin_key", workline_id)
        configured_version = WorklineMigrationInventoryService._optional_source_string(
            source, "contract_version", workline_id
        )
        run_mode = WorklineMigrationInventoryService._required_source_string(source, "run_mode", workline_id)
        active_binding_id = getattr(source, "active_plugin_binding_id", None)
        active_binding_version = getattr(source, "active_plugin_binding_version", None)
        active_config_hash = getattr(source, "active_plugin_config_hash", None)
        active_index_digest = getattr(source, "active_plugin_index_digest", None)
        provider_requirements_source = getattr(source, "active_plugin_provider_requirements_json", ())
        if active_binding_id is not None and (type(active_binding_id) is not int or active_binding_id <= 0):
            raise WorklineMigrationInventoryInvariantError(
                f"WorkLine {workline_id} active_plugin_binding_id 必须为正严格整数或 None"
            )
        if active_binding_version is not None and (
            type(active_binding_version) is not int or active_binding_version <= 0
        ):
            raise WorklineMigrationInventoryInvariantError(
                f"WorkLine {workline_id} active_plugin_binding_version 必须为正严格整数或 None"
            )
        for field_name, digest in (
            ("active_plugin_config_hash", active_config_hash),
            ("active_plugin_index_digest", active_index_digest),
        ):
            if digest is not None and (
                type(digest) is not str
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise WorklineMigrationInventoryInvariantError(
                    f"WorkLine {workline_id} {field_name} 必须为 lowercase SHA-256 或 None"
                )
        if not isinstance(provider_requirements_source, (list, tuple)) or any(
            type(value) is not str or not value.strip() for value in provider_requirements_source
        ):
            raise WorklineMigrationInventoryInvariantError(
                f"WorkLine {workline_id} provider_requirements 必须为非空字符串集合"
            )
        provider_requirements = tuple(sorted({value.strip() for value in provider_requirements_source}))
        try:
            extension_references = tuple(
                sorted(
                    (
                        WorklineRuntimeExtensionReference.model_validate(reference)
                        for reference in raw_extension_references
                    ),
                    key=lambda reference: (reference.type.value, reference.reference),
                )
            )
        except ValidationError as exc:
            raise WorklineMigrationInventoryInvariantError(
                f"WorkLine {workline_id} WorkItem/Intent extension reference 不满足合同: {exc}"
            ) from exc

        runtime_references = WorklineMigrationInventoryService._normalize_summary(raw_summary, workline_id)
        capability_projection = WorklineMigrationInventoryService._derive_capability_projection(
            workline_id=workline_id,
            plugin_key=plugin_key,
            configured_version=configured_version,
            capability_catalog=capability_catalog,
            system_capability_catalog=system_capability_catalog,
        )
        issues = WorklineMigrationInventoryService.classify_item_issues(
            workline_id=workline_id,
            line_code=line_code,
            is_active=is_active,
            plugin_key=plugin_key,
            configured_version=configured_version,
            plugin_known=capability_projection.plugin_known,
            catalog_identity_matched=capability_projection.catalog_identity_matched,
            active_binding_id=active_binding_id,
            active_binding_version=active_binding_version,
            active_config_hash=active_config_hash,
            active_index_digest=active_index_digest,
            reference_total=runtime_references.total,
        )
        try:
            return WorklineMigrationInventoryItem(
                workline_id=workline_id,
                line_code=line_code,
                is_active=is_active,
                plugin_key=plugin_key,
                configured_contract_version=configured_version,
                catalog_contract_version=capability_projection.catalog_contract_version,
                run_mode=run_mode,
                active_plugin_binding_id=active_binding_id,
                active_plugin_binding_version=active_binding_version,
                active_plugin_config_hash=active_config_hash,
                active_plugin_index_digest=active_index_digest,
                provider_requirements=provider_requirements,
                capability_requirements=capability_projection.capability_requirements,
                runtime_extension_references=extension_references,
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
        if not isinstance(raw_summary, Mapping) or frozenset(raw_summary) != _SUMMARY_KEYS:
            raise WorklineMigrationInventoryInvariantError(f"WorkLine {workline_id} summary 顶层键不精确")
        raw_by_type = raw_summary["by_type"]
        if not isinstance(raw_by_type, Mapping) or frozenset(raw_by_type) != _BY_TYPE_KEYS:
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
            None
            if raw_sample is None
            else WorklineMigrationInventoryService._normalize_sample(raw_sample, workline_id, by_type)
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
    def _normalize_sample(
        raw_sample: Any,
        workline_id: int,
        by_type: Mapping[str, int],
    ) -> WorklineRuntimeReferenceSample:
        if not isinstance(raw_sample, Mapping):
            raise WorklineMigrationInventoryInvariantError(f"WorkLine {workline_id} summary.sample 必须为 mapping")
        sample_type = raw_sample.get("type")
        definitions = {
            "session": (WorklineRuntimeReferenceType.SESSION, "session_code", "sessions"),
            "command": (WorklineRuntimeReferenceType.COMMAND, "command_code", "commands"),
            "outbox": (WorklineRuntimeReferenceType.OUTBOX, "dispatch_key", "outboxes"),
            "inbox": (WorklineRuntimeReferenceType.INBOX, "inbox_id", "inboxes"),
            "runtime_hold": (WorklineRuntimeReferenceType.RUNTIME_HOLD, "count", "runtime_holds"),
        }
        definition = definitions.get(sample_type) if isinstance(sample_type, str) else None
        if definition is None:
            raise WorklineMigrationInventoryInvariantError(f"WorkLine {workline_id} summary.sample.type 未知")
        reference_type, reference_field, count_field = definition
        if set(raw_sample) != {"type", "status", reference_field}:
            raise WorklineMigrationInventoryInvariantError(
                f"WorkLine {workline_id} summary.sample 键必须精确匹配 {sample_type} 合同"
            )
        if by_type[count_field] <= 0:
            raise WorklineMigrationInventoryInvariantError(
                f"WorkLine {workline_id} summary.sample.type 与 summary.by_type 不一致"
            )
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
            if reference_source != by_type[count_field]:
                raise WorklineMigrationInventoryInvariantError(
                    f"WorkLine {workline_id} runtime_hold sample.count 与 summary.by_type.runtime_holds 不一致"
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
    def classify_item_issues(
        *,
        workline_id: int,
        line_code: Any,
        is_active: Any,
        plugin_key: Any,
        configured_version: Any,
        plugin_known: bool,
        catalog_identity_matched: bool,
        active_binding_id: int | None,
        active_binding_version: int | None,
        active_config_hash: str | None,
        active_index_digest: str | None,
        reference_total: int,
    ) -> tuple[WorklineMigrationInventoryIssue, ...]:
        issue_specs: list[tuple[WorklineMigrationInventoryIssueCode, WorklineMigrationInventorySeverity, str]] = []
        if is_active is True and plugin_key is None:
            issue_specs.append(
                (
                    WorklineMigrationInventoryIssueCode.ACTIVE_WITHOUT_PLUGIN,
                    WorklineMigrationInventorySeverity.BLOCKER,
                    "启用作业线未配置插件",
                )
            )
        if is_active is True and configured_version is None:
            issue_specs.append(
                (
                    WorklineMigrationInventoryIssueCode.ACTIVE_WITHOUT_CONTRACT_VERSION,
                    WorklineMigrationInventorySeverity.BLOCKER,
                    "启用作业线未配置合同版本",
                )
            )
        if plugin_key is not None and not plugin_known:
            issue_specs.append(
                (
                    WorklineMigrationInventoryIssueCode.UNKNOWN_PLUGIN,
                    WorklineMigrationInventorySeverity.BLOCKER
                    if is_active is True
                    else WorklineMigrationInventorySeverity.WARNING,
                    "配置插件未进入 generated Plugin index，禁止激活",
                )
            )
        if plugin_key is not None and plugin_known and not catalog_identity_matched:
            issue_specs.append(
                (
                    WorklineMigrationInventoryIssueCode.CONTRACT_VERSION_MISMATCH,
                    WorklineMigrationInventorySeverity.BLOCKER,
                    "配置合同版本与 catalog 不一致",
                )
            )
        if is_active is True and catalog_identity_matched:
            active_pin = (active_binding_id, active_binding_version, active_config_hash, active_index_digest)
            if any(value is None for value in active_pin):
                issue_specs.append(
                    (
                        WorklineMigrationInventoryIssueCode.ACTIVE_PLUGIN_BINDING_INCOMPLETE,
                        WorklineMigrationInventorySeverity.BLOCKER,
                        "启用的平台插件作业线缺少完整 active binding pin",
                    )
                )
            elif active_index_digest != WORKLINE_PLUGIN_INDEX_DIGEST:
                issue_specs.append(
                    (
                        WorklineMigrationInventoryIssueCode.ACTIVE_PLUGIN_INDEX_DIGEST_MISMATCH,
                        WorklineMigrationInventorySeverity.BLOCKER,
                        "启用的平台插件作业线 binding 索引摘要已过期",
                    )
                )
        if reference_total > 0:
            issue_specs.append(
                (
                    WorklineMigrationInventoryIssueCode.RUNTIME_REFERENCES_PRESENT,
                    WorklineMigrationInventorySeverity.BLOCKER,
                    "仍存在未完成运行态引用",
                )
            )
        try:
            issues = tuple(
                WorklineMigrationInventoryIssue(
                    code=code,
                    severity=severity,
                    message=message,
                    workline_id=workline_id,
                    line_code=line_code,
                )
                for code, severity, message in issue_specs
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
    def _required_source_string(source: Any, field: str, workline_id: int) -> str:
        value = getattr(source, field, _MISSING)
        value = value.value if isinstance(value, Enum) else value
        if type(value) is not str or not value.strip():
            raise WorklineMigrationInventoryInvariantError(f"WorkLine {workline_id} 源字段 {field} 必须为非空字符串")
        return value.strip()

    @staticmethod
    def _optional_source_string(source: Any, field: str, workline_id: int) -> str | None:
        value = getattr(source, field, _MISSING)
        if value is None:
            return None
        if type(value) is not str or not value.strip():
            raise WorklineMigrationInventoryInvariantError(
                f"WorkLine {workline_id} 源字段 {field} 必须为 None 或非空字符串"
            )
        return value.strip()

    @staticmethod
    def _normalize_catalog_string(value: Any, field: str) -> str:
        if type(value) is not str or not value.strip():
            raise WorklineMigrationInventoryInvariantError(f"capability definition {field} 必须为非空字符串")
        return value.strip()


workline_migration_inventory_service = WorklineMigrationInventoryService(
    extension_reference_repository=workline_plugin_binding_repository
)


__all__ = [
    "WorklineMigrationInventoryInvariantError",
    "WorklineMigrationInventoryLimitExceeded",
    "WorklineMigrationInventoryService",
    "workline_migration_inventory_service",
]
