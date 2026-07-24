"""Workline 插件不可变 binding 的激活与运行准入。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ValidationError

from src.app.contracts.external_contract_profile_catalog import (
    ExternalContractProfileCatalog,
    external_contract_profile_catalog,
)
from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem
from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_INDEX
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX, WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.workline.repositories.plugin_binding_repository import workline_plugin_binding_repository
from src.core.conf import settings
from src.core.exceptions import OptimisticLockException
from src.utils.timezone import timezone
from src.utils.value_normalization import runtime_profile_environment

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from src.app.runtime.system_capabilities.definition import SystemCapabilityDefinition
    from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition


class PluginBindingAdmissionError(RuntimeError):
    """binding 激活或执行准入失败；调用方必须 fail closed。"""


@dataclass(frozen=True, slots=True)
class _PluginBindingActivationPlan:
    definition: Any
    typed_config: dict[str, Any]
    profiles: tuple[Any, ...]
    device_snapshot: tuple[dict[str, Any], ...]


class _BindingRepository(Protocol):
    async def next_binding_version(self, db: Any, workline_id: int, plugin_key: str, contract_version: str) -> int: ...

    async def create_immutable(self, db: Any, data: dict[str, Any]) -> Any: ...

    async def get_pinned(self, db: Any, binding_id: int) -> Any | None: ...

    async def save_pinned_runtime_aggregate(
        self,
        db: Any,
        *,
        execution_session: ExecutionSession,
        correlation: ExecutionCorrelation,
        work_item: ExecutionWorkItem,
    ) -> tuple[ExecutionSession, ExecutionWorkItem]: ...


class WorklinePluginBindingService:
    """组合 Definition、设备事实和外部合同目录，生成不可变运行 binding。"""

    def __init__(
        self,
        *,
        repository: _BindingRepository = workline_plugin_binding_repository,
        runtime_repository: Any | None = None,
        plugin_index: Mapping[tuple[str, str], WorklinePluginDefinition] = WORKLINE_PLUGIN_INDEX,
        capability_index: Mapping[tuple[str, str], SystemCapabilityDefinition] = SYSTEM_CAPABILITY_INDEX,
        plugin_index_digest: str = WORKLINE_PLUGIN_INDEX_DIGEST,
        profile_catalog: ExternalContractProfileCatalog | None = None,
        clock: Any = timezone.now_for_db,
    ) -> None:
        self.repository = repository
        self.runtime_repository = runtime_repository or repository
        self.plugin_index = plugin_index
        self.capability_index = capability_index
        self.plugin_index_digest = plugin_index_digest
        self.profile_catalog = profile_catalog or external_contract_profile_catalog
        self.clock = clock

    def manages(self, workline: Any) -> bool:
        """仅接管已进入生成索引的平台插件，兼容迁移期 legacy WorkLine。"""

        if isinstance(getattr(workline, "active_plugin_binding_id", None), int):
            return True
        identity = (getattr(workline, "plugin_key", None), getattr(workline, "contract_version", None))
        return identity in self.plugin_index

    @staticmethod
    def resolve_runtime_environment(app_env: object) -> str:
        return runtime_profile_environment(app_env)

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
        plan = self.validate_activation_configuration(
            workline=workline,
            environment=environment,
            devices=devices,
        )

        binding_version = await self.repository.next_binding_version(
            db, workline_id, plan.definition.plugin_key, plan.definition.contract_version
        )
        activated_at = self.clock()
        # WorkLine active pin 由 WorkLineService 通过乐观更新原子切换；此处只追加 immutable row。
        return await self.repository.create_immutable(
            db,
            {
                "workline_id": workline_id,
                "plugin_key": plan.definition.plugin_key,
                "contract_version": plan.definition.contract_version,
                "binding_version": binding_version,
                "typed_config_json": plan.typed_config,
                "typed_config_hash": sha256_digest(plan.typed_config),
                "provider_profile_snapshot_json": [profile.model_dump(mode="json") for profile in plan.profiles],
                "device_snapshot_json": list(plan.device_snapshot),
                "generated_index_digest": self.plugin_index_digest,
                "environment": environment,
                "activated_at": activated_at,
                "activated_by": actor,
                "activated_reason": reason,
                "valid_from": activated_at,
                "is_enabled": True,
            },
        )

    def validate_activation_configuration(
        self,
        *,
        workline: Any,
        environment: str,
        devices: Sequence[Any],
    ) -> _PluginBindingActivationPlan:
        """无副作用地校验并规范化 typed config、Provider 与设备角色。"""

        workline_id = getattr(workline, "id", None)
        if not isinstance(workline_id, int):
            raise PluginBindingAdmissionError("workline id 缺失")
        plugin_key = getattr(workline, "plugin_key", None)
        contract_version = getattr(workline, "contract_version", None)
        if not isinstance(plugin_key, str) or not isinstance(contract_version, str):
            raise PluginBindingAdmissionError("plugin identity 缺失")
        identity = (plugin_key, contract_version)
        definition = self.plugin_index.get(identity)
        if definition is None:
            raise PluginBindingAdmissionError(f"plugin definition 未生成或版本不匹配: {identity}")
        try:
            typed_config = definition.config_model.model_validate(getattr(workline, "config", {})).model_dump(
                mode="json"
            )
        except ValidationError as exc:
            raise PluginBindingAdmissionError(f"config validation failed: {exc}") from exc

        capability_definitions: list[SystemCapabilityDefinition] = []
        for capability_identity in definition.allowed_capabilities:
            capability = self.capability_index.get(capability_identity)
            if capability is None:
                raise PluginBindingAdmissionError(f"capability definition 缺失: {capability_identity}")
            capability_definitions.append(capability)
        provider_contract_definitions = tuple(
            capability for capability in capability_definitions if capability.admission != "runtime"
        )
        profiles: list[Any] = []
        if provider_contract_definitions:
            configured_profile = typed_config.get("provider_profile")
            if not isinstance(configured_profile, str) or not configured_profile:
                raise PluginBindingAdmissionError("provider_profile 缺失")
            try:
                profile = self.profile_catalog.resolve_identity(configured_profile)
                if profile.environment != environment:
                    raise LookupError("provider profile environment 与 binding environment 不一致")
                admission_family = profile.identity.rpartition(".")[0]
                unsupported_admissions = sorted(
                    {
                        capability.admission
                        for capability in provider_contract_definitions
                        if capability.admission not in {profile.identity, admission_family}
                    }
                )
                if unsupported_admissions:
                    raise LookupError("provider profile 与 capability admission 不一致")
                profiles.append(profile)
            except LookupError as exc:
                raise PluginBindingAdmissionError(f"provider admission failed: {exc}") from exc

        device_snapshot = self._build_device_snapshot(
            typed_config=typed_config,
            workline_id=workline_id,
            devices=devices,
        )

        return _PluginBindingActivationPlan(
            definition=definition,
            typed_config=typed_config,
            profiles=tuple(profiles),
            device_snapshot=tuple(device_snapshot),
        )

    @staticmethod
    def _build_device_snapshot(
        *,
        typed_config: Mapping[str, Any],
        workline_id: int,
        devices: Sequence[Any],
    ) -> list[dict[str, Any]]:
        """从 typed config 的角色引用固定激活时设备身份；兼容旧通用 code 配置。"""

        configured_roles = typed_config.get("device_roles")
        if isinstance(configured_roles, dict):
            required_roles = {role for role in configured_roles.values() if isinstance(role, str) and role.strip()}
            devices_by_role = {
                role: [device for device in devices if getattr(device, "device_role", None) == role]
                for role in required_roles
            }
            missing_roles = sorted(role for role, matched in devices_by_role.items() if not matched)
            if missing_roles:
                raise PluginBindingAdmissionError(f"device role requirement 缺失: {missing_roles}")
            snapshot: list[dict[str, Any]] = []
            for role in sorted(required_roles):
                for device in sorted(devices_by_role[role], key=lambda item: str(getattr(item, "device_code", ""))):
                    device_id = getattr(device, "id", None)
                    device_code = getattr(device, "device_code", None)
                    assigned_workline_id = getattr(device, "work_line_id", None)
                    if (
                        not isinstance(device_id, int)
                        or isinstance(device_id, bool)
                        or not isinstance(device_code, str)
                        or not device_code
                        or assigned_workline_id != workline_id
                    ):
                        raise PluginBindingAdmissionError(f"device role identity 非法: {role}")
                    snapshot.append(
                        {
                            "device_id": device_id,
                            "device_code": device_code,
                            "device_role": role,
                            "workline_id": assigned_workline_id,
                            "provider_code": getattr(device, "provider_code", None)
                            or getattr(device, "vendor_type", None),
                        }
                    )
            return snapshot

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
        return [
            {
                "device_code": code,
                "provider_code": getattr(device_by_code[code], "provider_code", None),
            }
            for code in sorted(set(required_device_codes))
        ]

    @staticmethod
    def assert_device_snapshot(binding: Any, *, devices_by_role: Mapping[str, Sequence[Any]]) -> None:
        """运行时只允许 binding 激活时固定的角色设备集合。"""

        typed_config = getattr(binding, "typed_config_json", {}) or {}
        configured_roles = typed_config.get("device_roles") if isinstance(typed_config, dict) else None
        if not isinstance(configured_roles, dict):
            return
        required_roles = {role for role in configured_roles.values() if isinstance(role, str) and role.strip()}
        expected = list(getattr(binding, "device_snapshot_json", ()) or ())
        if not expected:
            raise PluginBindingAdmissionError("binding device snapshot 缺失")
        actual = WorklinePluginBindingService._build_device_snapshot(
            typed_config=typed_config,
            workline_id=int(getattr(binding, "workline_id", 0)),
            devices=[device for role in required_roles for device in devices_by_role.get(role, ())],
        )
        if expected != actual:
            raise PluginBindingAdmissionError("binding device snapshot 与当前设备拓扑不一致")

    async def get_pinned(self, db: Any, *, binding_id: int) -> Any:
        """读取历史 pin 不过滤 is_enabled，保证 retry 能解析原始版本。"""

        binding = await self.repository.get_pinned(db, binding_id)
        if binding is None:
            raise PluginBindingAdmissionError(f"pinned binding 不存在: {binding_id}")
        return binding

    async def resolve_new_session_binding(self, db: Any, *, workline: Any) -> Any | None:
        """新 Session 先解析 active pin；从未绑定的 legacy WorkLine 返回 None。"""

        binding_id = getattr(workline, "active_plugin_binding_id", None)
        if not isinstance(binding_id, int):
            return None
        return await self.get_pinned(db, binding_id=binding_id)

    def assert_pinned_identity(self, *, binding: Any, workline: Any, session: Any) -> None:
        """历史 retry 只校验 session pin 与 binding 本身，不追随 WorkLine 当前 active pin。"""

        expected = {
            "plugin_key": getattr(binding, "plugin_key", None),
            "plugin_binding_id": getattr(binding, "id", None),
            "plugin_binding_version": getattr(binding, "binding_version", None),
            "plugin_config_hash": getattr(binding, "typed_config_hash", None),
            "plugin_index_digest": getattr(binding, "generated_index_digest", None),
        }
        actual = {field: getattr(session, field, None) for field in expected}
        if (
            getattr(binding, "workline_id", None) != getattr(workline, "id", None)
            or getattr(binding, "contract_version", None) != getattr(session, "contract_version", None)
            or actual != expected
        ):
            raise PluginBindingAdmissionError("pinned binding identity 不匹配")

    async def pin_new_runtime_session(
        self,
        db: Any,
        *,
        workline: Any,
        session: Any,
        binding: Any | None = None,
    ) -> tuple[ExecutionSession, ExecutionWorkItem] | None:
        """新平台 Session 在 caller 事务内固定 binding，并创建同 pin 的 Execution 聚合。"""

        if not self.manages(workline):
            return None
        binding_id = getattr(workline, "active_plugin_binding_id", None)
        if not isinstance(binding_id, int):
            raise PluginBindingAdmissionError("平台插件尚未激活 immutable binding")
        if binding is None:
            binding = await self.get_pinned(db, binding_id=binding_id)
        if binding is None:
            raise PluginBindingAdmissionError("pinned binding 不存在")
        if getattr(binding, "id", None) != binding_id:
            raise PluginBindingAdmissionError("预解析 binding 与 WorkLine active pin 不一致")
        active_identity = (
            getattr(workline, "active_plugin_binding_version", None),
            getattr(workline, "active_plugin_config_hash", None),
            getattr(workline, "active_plugin_index_digest", None),
        )
        binding_identity = (
            getattr(binding, "binding_version", None),
            getattr(binding, "typed_config_hash", None),
            getattr(binding, "generated_index_digest", None),
        )
        if active_identity != binding_identity:
            raise PluginBindingAdmissionError("WorkLine active binding pin 不一致")
        self.assert_execution_admitted(
            binding,
            environment=self.resolve_runtime_environment(settings.APP_ENV),
            now=self.clock(),
        )
        binding_identity = (binding.plugin_key, binding.contract_version)
        definition = self.plugin_index.get(binding_identity)
        if definition is None:
            raise PluginBindingAdmissionError(f"active binding definition 未生成或版本不匹配: {binding_identity}")
        plugin_state = definition.state_model.model_validate({})
        self.pin_runtime_records(binding=binding, records=(session,), plugin_state=plugin_state)
        session.contract_version = binding.contract_version
        correlation_id = f"workline-session:{session.session_code}"
        now = self.clock()
        execution_session = ExecutionSession(
            workline_id=session.workline_id,
            plugin_key=binding.plugin_key,
            manifest_version=binding.contract_version,
            created_at=now,
            updated_at=now,
        )
        correlation = ExecutionCorrelation(
            correlation_id=correlation_id,
            trace_id=session.trace_id or correlation_id,
            source_event_id=session.last_request_id,
            business_owner_key=session.business_key,
            created_at=now,
            updated_at=now,
        )
        work_item = ExecutionWorkItem(
            execution_session_id=0,
            correlation_id=correlation_id,
            object_type="session",
            object_key=session.business_key or session.session_code,
            current_step="INGRESS",
        )
        self.pin_runtime_records(
            binding=binding,
            records=(execution_session, work_item),
            plugin_state=plugin_state,
        )
        return await self.runtime_repository.save_pinned_runtime_aggregate(
            db,
            execution_session=execution_session,
            correlation=correlation,
            work_item=work_item,
        )

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
        if bool(getattr(binding, "is_revoked", False)):
            raise PluginBindingAdmissionError("binding 已撤权")
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


workline_plugin_binding_service = WorklinePluginBindingService(profile_catalog=external_contract_profile_catalog)

__all__ = [
    "PluginBindingAdmissionError",
    "WorklinePluginBindingService",
    "workline_plugin_binding_service",
]
