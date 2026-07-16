"""Workline 插件不可变 binding 的激活与运行准入。"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ValidationError

from src.app.contracts.external_contract_profile_catalog import ExternalContractProfileCatalog
from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_INDEX
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX, WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.workline.repositories.plugin_binding_repository import workline_plugin_binding_repository
from src.core.exceptions import OptimisticLockException
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from src.app.runtime.system_capabilities.definition import SystemCapabilityDefinition
    from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition


class PluginBindingAdmissionError(RuntimeError):
    """binding 激活或执行准入失败；调用方必须 fail closed。"""


class _BindingRepository(Protocol):
    async def next_binding_version(self, db: Any, workline_id: int, plugin_key: str, contract_version: str) -> int: ...

    async def create_immutable(self, db: Any, data: dict[str, Any]) -> Any: ...

    async def get_pinned(self, db: Any, binding_id: int) -> Any | None: ...


class WorklinePluginBindingService:
    """组合 Definition、设备事实和外部合同目录，生成不可变运行 binding。"""

    def __init__(
        self,
        *,
        repository: _BindingRepository = workline_plugin_binding_repository,
        plugin_index: Mapping[tuple[str, str], WorklinePluginDefinition] = WORKLINE_PLUGIN_INDEX,
        capability_index: Mapping[tuple[str, str], SystemCapabilityDefinition] = SYSTEM_CAPABILITY_INDEX,
        plugin_index_digest: str = WORKLINE_PLUGIN_INDEX_DIGEST,
        profile_catalog: ExternalContractProfileCatalog | None = None,
        clock: Any = timezone.now_for_db,
    ) -> None:
        self.repository = repository
        self.plugin_index = plugin_index
        self.capability_index = capability_index
        self.plugin_index_digest = plugin_index_digest
        self.profile_catalog = profile_catalog or ExternalContractProfileCatalog(())
        self.clock = clock

    async def activate(
        self,
        db: Any,
        *,
        workline: Any,
        expected_workline_version: int,
        actor: str,
        reason: str,
        environment: str,
        devices: Sequence[Any],
    ) -> Any:
        if not actor.strip() or not reason.strip() or not environment.strip():
            raise PluginBindingAdmissionError("actor/reason/environment 必须为非空字符串")
        if len(self.plugin_index_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.plugin_index_digest
        ):
            raise PluginBindingAdmissionError("generated plugin index digest 非法")
        workline_id = getattr(workline, "id", None)
        if not isinstance(workline_id, int):
            raise PluginBindingAdmissionError("workline id 缺失")
        current_version = getattr(workline, "version", None)
        if current_version != expected_workline_version:
            raise OptimisticLockException(
                resource_type="WorkLine",
                resource_id=workline_id,
                current_version=current_version,
                provided_version=expected_workline_version,
            )
        identity = (getattr(workline, "plugin_key", None), getattr(workline, "contract_version", None))
        definition = self.plugin_index.get(identity)
        if definition is None:
            raise PluginBindingAdmissionError(f"plugin definition 未生成或版本不匹配: {identity}")
        try:
            typed_config = definition.config_model.model_validate(getattr(workline, "config", {})).model_dump(
                mode="json"
            )
        except ValidationError as exc:
            raise PluginBindingAdmissionError(f"config validation failed: {exc}") from exc

        required_device_codes = typed_config.get("required_device_codes", ())
        if not isinstance(required_device_codes, (list, tuple)):
            raise PluginBindingAdmissionError("config required_device_codes 必须为集合")
        device_by_code = {
            str(device.device_code): device
            for device in devices
            if isinstance(getattr(device, "device_code", None), str)
        }
        missing_devices = sorted(set(required_device_codes).difference(device_by_code))
        if missing_devices:
            raise PluginBindingAdmissionError(f"device requirement 缺失: {missing_devices}")
        device_snapshot = [
            {
                "device_code": code,
                "provider_code": getattr(device_by_code[code], "provider_code", None),
            }
            for code in sorted(set(required_device_codes))
        ]

        capability_definitions: list[SystemCapabilityDefinition] = []
        for capability_identity in definition.allowed_capabilities:
            capability = self.capability_index.get(capability_identity)
            if capability is None:
                raise PluginBindingAdmissionError(f"capability definition 缺失: {capability_identity}")
            capability_definitions.append(capability)
        provider_contract_definitions = tuple(
            capability for capability in capability_definitions if capability.admission == "provider-contract"
        )
        required_port_types = tuple(
            sorted(
                {port for capability in provider_contract_definitions for port in capability.required_ports},
                key=lambda port: (port.__module__, port.__qualname__),
            )
        )
        profile = None
        port_requirements: list[str] = []
        if provider_contract_definitions:
            provider_code = typed_config.get("provider_code")
            provider_contract_version = typed_config.get("provider_contract_version")
            if not isinstance(provider_code, str) or not provider_code:
                raise PluginBindingAdmissionError("provider_code 缺失")
            if provider_contract_version is not None and not isinstance(provider_contract_version, str):
                raise PluginBindingAdmissionError("provider_contract_version 必须为字符串")
            try:
                profile = self.profile_catalog.resolve(
                    provider_code=provider_code,
                    environment=environment,
                    contract_version=provider_contract_version,
                )
                port_requirements = self.profile_catalog.assert_ports_declared(profile, required_port_types)
            except LookupError as exc:
                raise PluginBindingAdmissionError(f"provider/Port admission failed: {exc}") from exc

        binding_version = await self.repository.next_binding_version(
            db, workline_id, definition.plugin_key, definition.contract_version
        )
        activated_at = self.clock()
        row = await self.repository.create_immutable(
            db,
            {
                "workline_id": workline_id,
                "plugin_key": definition.plugin_key,
                "contract_version": definition.contract_version,
                "binding_version": binding_version,
                "typed_config_json": typed_config,
                "typed_config_hash": sha256_digest(typed_config),
                "provider_profile_snapshot_json": [] if profile is None else [profile.model_dump(mode="json")],
                "port_requirements_json": port_requirements,
                "device_snapshot_json": device_snapshot,
                "generated_index_digest": self.plugin_index_digest,
                "environment": environment,
                "activated_at": activated_at,
                "activated_by": actor,
                "activated_reason": reason,
                "valid_from": activated_at,
                "is_enabled": True,
            },
        )
        # WorkLine.config 仍是草稿；运行入口只跟随这些 immutable binding pin。
        workline.active_plugin_binding_id = row.id
        workline.active_plugin_binding_version = row.binding_version
        workline.active_plugin_config_hash = row.typed_config_hash
        workline.active_plugin_index_digest = row.generated_index_digest
        workline.active_plugin_provider_requirements_json = (
            [] if profile is None else [f"{profile.provider_code}@{profile.contract_version}"]
        )
        workline.active_plugin_port_requirements_json = list(port_requirements)
        return row

    async def get_pinned(self, db: Any, *, binding_id: int) -> Any:
        """读取历史 pin 不过滤 is_enabled，保证 retry 能解析原始版本。"""

        binding = await self.repository.get_pinned(db, binding_id)
        if binding is None:
            raise PluginBindingAdmissionError(f"pinned binding 不存在: {binding_id}")
        return binding

    @staticmethod
    def pin_runtime_records(*, binding: Any, records: Sequence[Any], plugin_state: BaseModel) -> None:
        """在同一事务内把 Session/ExecutionSession/WorkItem 固定到同一 binding。"""

        state_snapshot = plugin_state.model_dump(mode="json")
        for record in records:
            record.plugin_key = binding.plugin_key
            record.plugin_binding_id = binding.id
            record.plugin_binding_version = binding.binding_version
            record.plugin_config_hash = binding.typed_config_hash
            record.plugin_index_digest = binding.generated_index_digest
            record.plugin_state_json = dict(state_snapshot)
            record.plugin_state_version = 0

    @staticmethod
    def assert_execution_admitted(binding: Any, *, environment: str, now: datetime) -> None:
        """每次执行重查撤权、有效期、环境和 kill switch。"""

        if not bool(getattr(binding, "is_enabled", False)):
            raise PluginBindingAdmissionError("binding kill switch 已关闭")
        if getattr(binding, "environment", None) != environment:
            raise PluginBindingAdmissionError("binding environment 不匹配")
        valid_from = getattr(binding, "valid_from", None)
        valid_until = getattr(binding, "valid_until", None)
        aware_now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
        aware_valid_from = (
            None
            if valid_from is None
            else valid_from.replace(tzinfo=UTC)
            if valid_from.tzinfo is None
            else valid_from.astimezone(UTC)
        )
        aware_valid_until = (
            None
            if valid_until is None
            else valid_until.replace(tzinfo=UTC)
            if valid_until.tzinfo is None
            else valid_until.astimezone(UTC)
        )
        if aware_valid_from is not None and aware_now < aware_valid_from:
            raise PluginBindingAdmissionError("binding 尚未生效")
        if aware_valid_until is not None and aware_now >= aware_valid_until:
            raise PluginBindingAdmissionError("binding 已过期")


workline_plugin_binding_service = WorklinePluginBindingService()

__all__ = [
    "PluginBindingAdmissionError",
    "WorklinePluginBindingService",
    "workline_plugin_binding_service",
]
