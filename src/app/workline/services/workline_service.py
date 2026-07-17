"""WorkLine Service 层"""

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models import parse_device_capabilities
from src.app.device.repositories import device_repository
from src.app.runtime.capability_catalog import (
    get_workline_capability_definition,
    get_workline_contract_version,
    list_workline_capability_definitions,
    validate_workline_capability_assignment,
)
from src.app.runtime.orchestration.events_bridge import assert_not_reserved_runtime_event
from src.app.runtime.orchestration.models.rack_position import WorklineRackPosition
from src.app.runtime.orchestration.repositories.rack_position_repository import workline_rack_position_repository
from src.app.runtime.orchestration.repository_wiring import workline_repository
from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    WorkLineRuntimeStatusProjectionService,
    workline_runtime_status_projection_service,
)
from src.app.runtime.orchestration.topology_bridge import WorklineTopologyView
from src.app.workline.domain.run_mode import (
    is_sandbox_allowed_environment,
    is_simulation_run_mode,
    normalize_run_mode,
)
from src.app.workline.models import (
    WorkLine,
    WorkLineConfigurationCheck,
    WorkLineConfigurationStatus,
    WorkLinePluginManifestSummary,
    WorkLinePluginOption,
    WorkLineRunMode,
)
from src.app.workline.models.workline import (
    CommandBinding,
    DeviceRequirement,
    EventBinding,
    FlowEdge,
    NodeRef,
    PipelineQueue,
    RackPosition,
    RackPositionCarrierCapability,
    ResourceBoundary,
    SessionSubject,
    StateMachine,
    StateMachineOwner,
    StateMachineSubject,
    StateMachineTransition,
    TopologySpec,
)
from src.app.workline.repositories import WorkLineRepository
from src.app.workline.services.plugin_binding_service import (
    PluginBindingAdmissionError,
    WorklinePluginBindingService,
    workline_plugin_binding_service,
)
from src.common.cache_config import cache_settings
from src.core.base_service import BaseService
from src.core.conf import settings
from src.core.exceptions import BusinessException, OptimisticLockException
from src.utils.device_cache import workline_device_cache

_BLOCKER = "BLOCKER"
_FAIL = "FAIL"
_OK = "PASS"
_WARN = "WARN"
_DEFAULT_DEVICE_STATUS_PATH = "/api/v1/device/status"
_ACTIVE_CONFIGURATION_FIELDS = frozenset(
    {
        "line_code",
        "plugin_key",
        "contract_version",
        "config",
        "runtime_config_json",
        "run_mode",
        "line_type",
    }
)
_ACTIVE_IDENTITY_FIELDS = frozenset({"line_code", "line_type"})


def _string_list_from_iterable(value: object) -> list[str]:
    """将 manifest raw iterable 字段转换为字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, bytes):
        return [value.decode()]
    if isinstance(value, Iterable):
        return [str(item) for item in value if str(item)]
    return []


class WorkLineService(BaseService[WorkLine, WorkLineRepository]):
    """作业线业务逻辑层"""

    def __init__(
        self,
        repository: WorkLineRepository = workline_repository,
        *,
        runtime_status_projection_service: WorkLineRuntimeStatusProjectionService = (
            workline_runtime_status_projection_service
        ),
        plugin_binding_service: WorklinePluginBindingService = workline_plugin_binding_service,
    ) -> None:
        super().__init__(
            repository,
            enable_cache=True,
            cache_prefix=cache_settings.WORKLINE.prefix,
            cache_expire=cache_settings.WORKLINE.expire,
            list_cache_prefix=cache_settings.WORKLINE_LIST.prefix,
            list_cache_expire=cache_settings.WORKLINE_LIST.expire,
        )
        self.runtime_status_projection_service = runtime_status_projection_service
        self.plugin_binding_service = plugin_binding_service

    @staticmethod
    def _resolve_plugin_key(data: dict[str, Any], current: WorkLine | None = None) -> str | None:
        plugin_key = data.get("plugin_key", getattr(current, "plugin_key", None))
        return plugin_key if isinstance(plugin_key, str) and plugin_key else None

    @staticmethod
    def _manifest_attr(manifest: object, plugin_key: str, field_name: str) -> Any:
        if not hasattr(manifest, field_name):
            raise ValueError(f"工作线插件 {plugin_key} manifest.{field_name} 缺失")
        return getattr(manifest, field_name)

    @classmethod
    def _manifest_sequence(cls, manifest: object, plugin_key: str, field_name: str) -> list[Any]:
        value = cls._manifest_attr(manifest, plugin_key, field_name)
        if isinstance(value, str | bytes) or not isinstance(value, Iterable):
            raise ValueError(f"工作线插件 {plugin_key} manifest.{field_name} 必须是结构化集合")  # noqa: TRY004
        return list(value)

    @staticmethod
    def _manifest_value(value: object) -> object:
        return getattr(value, "value", value)

    @classmethod
    def _build_device_requirement_summary(cls, requirement: object) -> DeviceRequirement:
        return DeviceRequirement(
            role=requirement.role,
            min_count=requirement.min_count,
            max_count=getattr(requirement, "max_count", None),
            hardware_capabilities=_string_list_from_iterable(getattr(requirement, "hardware_capabilities", ())),
        )

    @classmethod
    def _build_rack_position_carrier_capability_summary(cls, capability: object) -> RackPositionCarrierCapability:
        return RackPositionCarrierCapability(
            allowed_rack_kinds=_string_list_from_iterable(getattr(capability, "allowed_rack_kinds", ())),
            min_capacity=capability.min_capacity,
            max_capacity=capability.max_capacity,
            allowed_slot_kinds=_string_list_from_iterable(getattr(capability, "allowed_slot_kinds", ())),
        )

    @classmethod
    def _build_rack_position_summary(cls, rack_position: object) -> RackPosition:
        return RackPosition(
            code=rack_position.code,
            role=rack_position.role,
            station_code=rack_position.station_code,
            carrier_capability=cls._build_rack_position_carrier_capability_summary(rack_position.carrier_capability),
        )

    @classmethod
    def _build_node_ref_summary(cls, node_ref: object) -> NodeRef:
        return NodeRef(
            kind=str(cls._manifest_value(node_ref.kind)),
            ref=node_ref.ref,
        )

    @classmethod
    def _build_flow_edge_summary(cls, edge: object) -> FlowEdge:
        return FlowEdge(
            from_node=cls._build_node_ref_summary(edge.from_node),
            to_node=cls._build_node_ref_summary(edge.to_node),
            type=str(cls._manifest_value(edge.type)),
        )

    @classmethod
    def _build_topology_summary(cls, topology: object) -> TopologySpec:
        return TopologySpec(
            flow_edges=[cls._build_flow_edge_summary(edge) for edge in getattr(topology, "flow_edges", ())]
        )

    @classmethod
    def _build_event_binding_summary(cls, event: object) -> EventBinding:
        return EventBinding(
            event=event.event,
            source_device_roles=_string_list_from_iterable(getattr(event, "source_device_roles", ())),
            category=str(cls._manifest_value(event.category)),
        )

    @classmethod
    def _build_command_binding_summary(cls, command: object) -> CommandBinding:
        return CommandBinding.model_validate(
            {
                "command": command.command,
                "target_device_role": command.target_device_role,
            }
        )

    @staticmethod
    def _build_resource_boundary_summary(boundary: object) -> ResourceBoundary:
        return ResourceBoundary(
            rack_position_code=boundary.rack_position_code,
            rack_kind=boundary.rack_kind,
            business_demand_type=boundary.business_demand_type,
            wms_operation_type=boundary.wms_operation_type,
            snapshot_kind=boundary.snapshot_kind,
            lease_scope=boundary.lease_scope,
        )

    @classmethod
    def _build_session_subject_summary(cls, subject: object | None) -> SessionSubject | None:
        if subject is None:
            return None
        return SessionSubject(
            type=subject.type,
            physical_form=subject.physical_form,
            identity_sources=_string_list_from_iterable(getattr(subject, "identity_sources", ())),
        )

    @staticmethod
    def _build_state_machine_subject_summary(subject: object) -> StateMachineSubject:
        return StateMachineSubject(
            category=subject.category,
            type=subject.type,
            physical_form=subject.physical_form,
        )

    @staticmethod
    def _build_state_machine_owner_summary(owner: object) -> StateMachineOwner:
        return StateMachineOwner(
            model=owner.model,
            field=owner.field,
        )

    @classmethod
    def _build_state_machine_transition_summary(cls, transition: object) -> StateMachineTransition:
        return StateMachineTransition(
            from_state=transition.from_state,
            to_states=_string_list_from_iterable(getattr(transition, "to_states", ())),
        )

    @classmethod
    def _build_state_machine_summary(cls, state_machine: object) -> StateMachine:
        return StateMachine(
            id=state_machine.id,
            subject=cls._build_state_machine_subject_summary(state_machine.subject),
            state_owner=cls._build_state_machine_owner_summary(state_machine.state_owner),
            granularity=state_machine.granularity,
            transitions=[
                cls._build_state_machine_transition_summary(transition)
                for transition in getattr(state_machine, "transitions", ())
            ],
        )

    @staticmethod
    def _build_pipeline_queue_summary(queue: object) -> PipelineQueue:
        return PipelineQueue(
            code=queue.code,
            role=queue.role,
            capacity=queue.capacity,
            order_policy=queue.order_policy,
        )

    def list_plugin_options(self) -> list[WorkLinePluginOption]:
        """从运行能力目录导出作业线能力/契约版本选项。"""

        options: list[WorkLinePluginOption] = []
        for definition in list_workline_capability_definitions():
            manifest = definition.manifest
            plugin_key = definition.capability_key
            options.append(
                WorkLinePluginOption(
                    plugin_key=plugin_key,
                    label=plugin_key,
                    contract_versions=[manifest.contract_version],
                    default_contract_version=manifest.contract_version,
                )
            )
        return options

    def get_plugin_manifest_summary(
        self,
        plugin_key: str,
        contract_version: str | None = None,
    ) -> WorkLinePluginManifestSummary | None:
        """返回单个工作线能力 manifest 摘要。"""

        definition = get_workline_capability_definition(plugin_key)
        if definition is None:
            return None

        manifest = definition.manifest
        if contract_version and manifest.contract_version != contract_version:
            return None

        plugin_key = definition.capability_key
        return WorkLinePluginManifestSummary(
            plugin_key=plugin_key,
            contract_version=manifest.contract_version,
            devices=[
                self._build_device_requirement_summary(device)
                for device in self._manifest_sequence(manifest, plugin_key, "devices")
            ],
            rack_positions=[
                self._build_rack_position_summary(rack_position)
                for rack_position in self._manifest_sequence(manifest, plugin_key, "rack_positions")
            ],
            topology=self._build_topology_summary(self._manifest_attr(manifest, plugin_key, "topology")),
            events=[
                self._build_event_binding_summary(event)
                for event in self._manifest_sequence(manifest, plugin_key, "events")
            ],
            commands=[
                self._build_command_binding_summary(command)
                for command in self._manifest_sequence(manifest, plugin_key, "commands")
            ],
            resource_boundaries=[
                self._build_resource_boundary_summary(boundary)
                for boundary in self._manifest_sequence(manifest, plugin_key, "resource_boundaries")
            ],
            session_subject=self._build_session_subject_summary(
                getattr(manifest, "session_subject", None),
            ),
            state_machines=[
                self._build_state_machine_summary(state_machine)
                for state_machine in getattr(manifest, "state_machines", ())
            ],
            pipeline_queues=[
                self._build_pipeline_queue_summary(queue) for queue in getattr(manifest, "pipeline_queues", ())
            ],
        )

    async def create(self, db: AsyncSession, data: dict[str, Any], cache: object | None = None) -> WorkLine | None:
        """创建工作线时仅校验插件标识，拓扑校验留到设备已关联后。"""
        self._reject_active_state_write(data)
        self._validate_plugin_key(data.get("plugin_key"))
        self._validate_plugin_contract_version(data)
        self._validate_run_mode(data)
        self._validate_runtime_config(data)
        self._apply_runtime_defaults(data)
        result = await self.repo.create(db, data)
        _ = await self._ensure_default_runtime_status_projection(db, result)
        await self._commit_mutation(db)
        if cache:
            await self.invalidate_cache(cache, invalidate_list=True)
        return result

    async def update(
        self,
        db: AsyncSession,
        id: int,
        data: dict[str, Any],
        cache: object | None = None,
    ) -> WorkLine | None:
        """更新工作线基础配置；拓扑完整性由启用预检负责。"""
        self._reject_active_state_write(data)
        if _ACTIVE_CONFIGURATION_FIELDS.intersection(data):
            current = await self.repo.get_for_update(db, id)
        else:
            current = await self.repo.get_by_id(db, id)
        if current is None:
            raise ValueError(f"WorkLine 不存在: {id}")

        self._reject_active_configuration_update(current, data)
        self._validate_plugin_key(data.get("plugin_key"))
        self._validate_plugin_contract_version(data, current=current)
        self._validate_run_mode(data, current=current)
        self._validate_runtime_config(data, current=current)
        self._apply_runtime_defaults(data, current=current)
        return await super().update(db, id, data, cache)

    async def delete(self, db: AsyncSession, id: int, cache: object | None = None) -> bool | None:
        """删除工作线后失效设备缓存"""
        current = await self.repo.get_for_update(db, id)
        if current is None:
            return None
        if current.is_active:
            raise BusinessException("作业线已启用，请先停用后再删除")

        workload = await self.repo.get_unfinished_workload_summary(db, id)
        if workload["count"] > 0:
            raise BusinessException(
                message="存在未完成运行负载，不能删除作业线",
                detail={"workload": workload},
            )

        result = await super().delete(db, id, cache)
        if result:
            # 失效该工作线的设备缓存
            workline_device_cache.invalidate(id)
        return result

    async def restore(self, db: AsyncSession, id: int, cache: object | None = None) -> WorkLine | None:
        """恢复工作线时补齐 runtime 状态默认投影。"""

        result = await self.repo.restore(db, id)
        _ = await self._ensure_default_runtime_status_projection(db, result)
        await self._commit_mutation(db)
        if cache:
            await self.invalidate_cache(cache, id, invalidate_list=True)
        return result

    async def _validate_plugin_assignment(
        self,
        db: AsyncSession,
        current: WorkLine | None,
        data: dict[str, Any],
    ) -> None:
        plugin_key = self._resolve_plugin_key(data, current)
        if plugin_key is None:
            return

        workline_id = getattr(current, "id", None)
        devices = await device_repository.get_by_work_line_id(db, workline_id) if isinstance(workline_id, int) else []
        workline_like = SimpleNamespace(
            id=workline_id,
            line_code=data.get("line_code", getattr(current, "line_code", None)),
            line_name=data.get("line_name", getattr(current, "line_name", None)),
            plugin_key=plugin_key,
        )
        validate_workline_capability_assignment(plugin_key, workline_like, devices)

    async def configuration_status(self, db: AsyncSession, workline_id: int) -> WorkLineConfigurationStatus:
        """返回 WorkLine 启用前结构化配置状态。"""

        workline = await self.repo.get_by_id(db, workline_id)
        if workline is None:
            raise ValueError(f"WorkLine 不存在: {workline_id}")

        devices = await device_repository.get_by_work_line_id(db, workline_id)
        rack_positions = await self._list_rack_positions(db, workline)
        checks = self._build_configuration_checks(workline, devices, rack_positions)
        can_activate = self._can_activate(checks)
        return WorkLineConfigurationStatus(
            workline_id=workline_id,
            is_active=bool(workline.is_active),
            can_activate=can_activate,
            checks=checks,
        )

    async def activate(
        self,
        db: AsyncSession,
        workline_id: int,
        *,
        version: int,
        cache: object | None = None,
        actor: str = "system",
        reason: str = "workline-activation",
        environment: str | None = None,
    ) -> WorkLine | None:
        """通过配置预检后启用 WorkLine。"""

        await self.repo.acquire_plugin_pin_exclusive(db, workline_id)
        current = await self.repo.get_for_update(db, workline_id, populate_existing=True)
        if current is None:
            raise ValueError(f"WorkLine 不存在: {workline_id}")
        self._assert_version(current, workline_id, version)

        devices = await device_repository.get_by_work_line_id(db, workline_id)
        rack_positions = await self._list_rack_positions(db, current)
        checks = self._build_configuration_checks(current, devices, rack_positions)
        can_activate = self._can_activate(checks)
        if not can_activate:
            raise BusinessException(
                message="配置预检未通过，不能启用作业线",
                detail={"checks": [check.model_dump() for check in checks]},
            )
        projection_created = await self._ensure_default_runtime_status_projection(db, current)
        binding_managed = self.plugin_binding_service.manages(current)
        if current.is_active and not binding_managed:
            if projection_created:
                await self._commit_mutation(db)
            return current
        if binding_managed:
            if getattr(current, "active_plugin_binding_id", None) is None:
                workload = await self.repo.get_unfinished_workload_summary(db, workline_id)
                if workload["count"] > 0:
                    raise BusinessException(
                        message="存在未完成 legacy 运行负载，不能首次启用平台插件绑定",
                        code="4001",
                        detail={"reason_code": "LEGACY_RUNTIME_WORKLOAD_PRESENT"},
                    )
            binding_environment = environment or WorklinePluginBindingService.resolve_runtime_environment(
                settings.APP_ENV
            )
            try:
                binding = await self.plugin_binding_service.activate(
                    db,
                    workline=current,
                    expected_workline_version=version,
                    actor=actor,
                    reason=reason,
                    environment=binding_environment,
                    devices=devices,
                )
            except PluginBindingAdmissionError as exc:
                raise BusinessException(
                    message="插件绑定准入失败，请检查配置、设备和外部合同",
                    code="4001",
                    detail={"reason_code": "PLUGIN_BINDING_ADMISSION_FAILED"},
                ) from exc
            update_data = self._binding_pin_update(binding, version=version)
            if not current.is_active:
                update_data["is_active"] = True
            updated = await self.repo.update(db, workline_id, update_data)
            await self._commit_mutation(db)
            if cache:
                await self.invalidate_cache(cache, workline_id, invalidate_list=True)
            return updated
        return await self._set_active_state(db, workline_id, is_active=True, version=version, cache=cache)

    @staticmethod
    def _binding_pin_update(binding: Any, *, version: int) -> dict[str, Any]:
        provider_requirements = [
            f"{profile['provider_code']}@{profile['contract_version']}#{profile['environment']}"
            for profile in getattr(binding, "provider_profile_snapshot_json", ())
        ]
        return {
            "active_plugin_binding_id": binding.id,
            "active_plugin_binding_version": binding.binding_version,
            "active_plugin_config_hash": binding.typed_config_hash,
            "active_plugin_index_digest": binding.generated_index_digest,
            "active_plugin_provider_requirements_json": provider_requirements,
            "active_plugin_port_requirements_json": list(binding.port_requirements_json),
            "version": version,
        }

    async def deactivate(
        self,
        db: AsyncSession,
        workline_id: int,
        *,
        version: int,
        cache: object | None = None,
    ) -> WorkLine | None:
        """停用 WorkLine；存在未完成运行负载时拒绝。"""

        current = await self.repo.get_for_update(db, workline_id)
        if current is None:
            raise ValueError(f"WorkLine 不存在: {workline_id}")
        self._assert_version(current, workline_id, version)

        workload = await self.repo.get_unfinished_workload_summary(db, workline_id)
        if workload["count"] > 0:
            raise BusinessException(
                message="存在未完成运行负载，不能停用作业线",
                detail={"workload": workload},
            )
        if not current.is_active:
            return current
        return await self._set_active_state(db, workline_id, is_active=False, version=version, cache=cache)

    async def _set_active_state(
        self,
        db: AsyncSession,
        workline_id: int,
        *,
        is_active: bool,
        version: int,
        cache: object | None,
    ) -> WorkLine | None:
        updated = await self.repo.update(db, workline_id, {"is_active": is_active, "version": version})
        await self._commit_mutation(db)
        if cache:
            await self.invalidate_cache(cache, workline_id, invalidate_list=True)
        return updated

    async def _ensure_default_runtime_status_projection(self, db: AsyncSession, workline: WorkLine | None) -> bool:
        if workline is None:
            return False
        workline_id = getattr(workline, "id", None)
        if not isinstance(workline_id, int):
            raise TypeError("WorkLine 缺少主键，无法创建 runtime 状态投影")
        snapshot = await self.runtime_status_projection_service.runtime_status_snapshot(db, workline_id=workline_id)
        if snapshot.runtime_status is not None:
            return False
        result = await self.runtime_status_projection_service.ensure_default_result(db, workline_id=workline_id)
        return bool(getattr(result, "created", False))

    @staticmethod
    def _assert_version(workline: WorkLine, workline_id: int, version: int) -> None:
        if getattr(workline, "version", None) != version:
            raise OptimisticLockException(
                resource_type="WorkLine",
                resource_id=workline_id,
                current_version=getattr(workline, "version", None),
                provided_version=version,
            )

    @staticmethod
    def _reject_active_state_write(data: dict[str, Any]) -> None:
        if "is_active" in data:
            raise BusinessException(
                message="作业线启用状态只能通过专用操作修改",
                detail={"fields": ["is_active"]},
            )

    @staticmethod
    def _reject_active_configuration_update(workline: WorkLine, data: dict[str, Any]) -> None:
        if not bool(getattr(workline, "is_active", False)):
            return
        submitted_fields = sorted(_ACTIVE_IDENTITY_FIELDS.intersection(data))
        if submitted_fields:
            raise BusinessException(
                message="已启用作业线下不能修改作业线身份字段，请先停用作业线",
                detail={
                    "workline_id": getattr(workline, "id", None),
                    "fields": submitted_fields,
                },
            )

    @staticmethod
    def _can_activate(checks: list[WorkLineConfigurationCheck]) -> bool:
        return not any(check.status == _FAIL and check.severity == _BLOCKER for check in checks)

    @staticmethod
    async def _list_rack_positions(db: AsyncSession, workline: WorkLine) -> list[WorklineRackPosition]:
        workline_id = getattr(workline, "id", None)
        if not isinstance(workline_id, int):
            return []

        columns = cast("Any", WorklineRackPosition).__table__.c
        _, rack_position_rows = await workline_rack_position_repository.get_list(
            db,
            limit=1000,
            where_clauses_raw=[columns.workline_id == workline_id],
        )
        return rack_position_rows

    def _build_configuration_checks(
        self,
        workline: WorkLine,
        devices: list[Any],
        rack_positions: list[Any] | None = None,
    ) -> list[WorkLineConfigurationCheck]:
        checks: list[WorkLineConfigurationCheck] = []
        plugin_key = self._resolve_plugin_key({}, workline)
        definition = get_workline_capability_definition(plugin_key)
        if plugin_key is None:
            return [
                self._check(
                    "PLUGIN_CONFIGURED",
                    _FAIL,
                    _BLOCKER,
                    {"message": "未选择工作线插件"},
                )
            ]
        if definition is None:
            return [
                self._check(
                    "PLUGIN_CONFIGURED",
                    _FAIL,
                    _BLOCKER,
                    {"plugin_key": plugin_key, "message": "不支持的工作线插件"},
                )
            ]

        manifest = definition.manifest
        checks.append(self._check("PLUGIN_CONFIGURED", _OK, "INFO", {"plugin_key": plugin_key}))
        expected_contract_version = manifest.contract_version
        checks.append(
            self._check(
                "CONTRACT_VERSION_CURRENT",
                _OK if workline.contract_version == expected_contract_version else _FAIL,
                "INFO" if workline.contract_version == expected_contract_version else _BLOCKER,
                {
                    "actual": workline.contract_version,
                    "expected": expected_contract_version,
                },
            )
        )
        checks.append(self._run_mode_check(workline))

        topology = WorklineTopologyView.from_devices(devices)
        checks.extend(self._role_requirement_checks(manifest, topology))
        checks.extend(self._event_source_checks(manifest, topology))
        checks.extend(self._command_target_checks(manifest, topology))
        checks.extend(self._command_target_capability_config_checks(manifest, devices))
        checks.extend(self._command_target_communication_checks(workline, manifest, devices))
        checks.extend(self._rack_position_carrier_capability_checks(manifest, rack_positions))
        return checks

    @staticmethod
    def _check(
        code: str,
        status: str,
        severity: str,
        context: dict[str, Any],
    ) -> WorkLineConfigurationCheck:
        return WorkLineConfigurationCheck(
            code=code,
            status=status,  # type: ignore[arg-type]
            severity=severity,  # type: ignore[arg-type]
            context=context,
        )

    @staticmethod
    def _run_mode_check(workline: WorkLine) -> WorkLineConfigurationCheck:
        run_mode = normalize_run_mode(getattr(workline, "run_mode", WorkLineRunMode.AUTO))
        if is_simulation_run_mode(run_mode) and not is_sandbox_allowed_environment(settings.APP_ENV):
            return WorkLineService._check(
                "RUN_MODE_ALLOWED",
                _FAIL,
                _BLOCKER,
                {"run_mode": run_mode, "app_env": settings.APP_ENV},
            )
        return WorkLineService._check("RUN_MODE_ALLOWED", _OK, "INFO", {"run_mode": run_mode})

    @staticmethod
    def _role_requirement_checks(manifest: Any, topology: WorklineTopologyView) -> list[WorkLineConfigurationCheck]:
        checks: list[WorkLineConfigurationCheck] = []
        for requirement in manifest.devices:
            devices = topology.devices_for_role(requirement.role)
            count = len(devices)
            role_passes = count >= requirement.min_count and (
                requirement.max_count is None or count <= requirement.max_count
            )
            checks.append(
                WorkLineService._check(
                    "ROLE_REQUIREMENT",
                    _OK if role_passes else _FAIL,
                    "INFO" if role_passes else _BLOCKER,
                    {
                        "role": requirement.role,
                        "min_count": requirement.min_count,
                        "max_count": requirement.max_count,
                        "count": count,
                    },
                )
            )

            required_capabilities = frozenset(getattr(requirement, "hardware_capabilities", ()))
            if required_capabilities:
                for device in devices:
                    missing_capabilities = required_capabilities.difference(device.capabilities)
                    checks.append(
                        WorkLineService._check(
                            "DEVICE_CAPABILITY",
                            _OK if not missing_capabilities else _FAIL,
                            "INFO" if not missing_capabilities else _BLOCKER,
                            {
                                "role": requirement.role,
                                "device_id": device.device_id,
                                "device_code": device.device_code,
                                "missing_capabilities": sorted(missing_capabilities),
                            },
                        )
                    )
        return checks

    @staticmethod
    def _event_source_checks(manifest: Any, topology: WorklineTopologyView) -> list[WorkLineConfigurationCheck]:
        checks: list[WorkLineConfigurationCheck] = []
        for event in manifest.events:
            if WorkLineService._manifest_value(getattr(event, "category", None)) != "ENTRY_DEVICE":
                continue
            event_type = event.event
            roles = tuple(getattr(event, "source_device_roles", ()))
            has_source = any(
                device.supports_event(event_type) for role in roles for device in topology.devices_for_role(role)
            )
            checks.append(
                WorkLineService._check(
                    "EVENT_SOURCE_CAPABILITY",
                    _OK if has_source else _FAIL,
                    "INFO" if has_source else _BLOCKER,
                    {"event_type": event_type, "roles": list(roles)},
                )
            )
        return checks

    @staticmethod
    def _command_target_checks(manifest: Any, topology: WorklineTopologyView) -> list[WorkLineConfigurationCheck]:
        checks: list[WorkLineConfigurationCheck] = []
        for command in manifest.commands:
            command_type = command.command
            target_role = command.target_device_role
            has_target = any(device.supports_command(command_type) for device in topology.devices_for_role(target_role))
            checks.append(
                WorkLineService._check(
                    "COMMAND_TARGET_CAPABILITY",
                    _OK if has_target else _FAIL,
                    "INFO" if has_target else _BLOCKER,
                    {"command_type": command_type, "roles": [target_role]},
                )
            )
        return checks

    @staticmethod
    def _command_target_communication_checks(
        workline: WorkLine,
        manifest: Any,
        devices: list[Any],
    ) -> list[WorkLineConfigurationCheck]:
        checks: list[WorkLineConfigurationCheck] = []
        run_mode = normalize_run_mode(getattr(workline, "run_mode", WorkLineRunMode.AUTO))
        missing_config_status = _WARN if is_simulation_run_mode(run_mode) else _FAIL
        missing_config_severity = "WARNING" if is_simulation_run_mode(run_mode) else _BLOCKER
        target_map = WorkLineService._command_target_device_map(manifest, devices)
        for device_id, (device, command_types) in sorted(
            target_map.items(), key=lambda item: str(getattr(item[1][0], "device_code", ""))
        ):
            status_path = WorkLineService._resolve_device_status_path(device)
            missing_fields = []
            if not getattr(device, "host", None):
                missing_fields.append("host")
            if not getattr(device, "port", None):
                missing_fields.append("port")
            if not status_path:
                missing_fields.append("status_path")
            checks.append(
                WorkLineService._check(
                    "COMMAND_TARGET_COMMUNICATION",
                    _OK if not missing_fields else missing_config_status,
                    "INFO" if not missing_fields else missing_config_severity,
                    {
                        "device_id": device_id,
                        "device_code": getattr(device, "device_code", None),
                        "command_types": sorted(command_types),
                        "run_mode": run_mode,
                        "scheme": WorkLineService._resolve_device_scheme(device),
                        "host": getattr(device, "host", None),
                        "port": getattr(device, "port", None),
                        "status_path": status_path,
                        "missing_fields": missing_fields,
                    },
                )
            )
        return checks

    @staticmethod
    def _command_target_capability_config_checks(manifest: Any, devices: list[Any]) -> list[WorkLineConfigurationCheck]:
        checks: list[WorkLineConfigurationCheck] = []
        for command in manifest.commands:
            command_type = command.command
            target_role = command.target_device_role
            for device in devices:
                device_id = getattr(device, "id", None)
                if not isinstance(device_id, int):
                    continue
                if getattr(device, "device_role", None) != target_role:
                    continue
                try:
                    _ = parse_device_capabilities(getattr(device, "capabilities_json", None))
                except (TypeError, ValidationError, ValueError) as exc:
                    checks.append(
                        WorkLineService._check(
                            "COMMAND_TARGET_CAPABILITY_CONFIG",
                            _FAIL,
                            _BLOCKER,
                            {
                                "device_id": device_id,
                                "device_code": getattr(device, "device_code", None),
                                "device_role": getattr(device, "device_role", None),
                                "command_type": command_type,
                                "capabilities_error": str(exc),
                            },
                        )
                    )
        return checks

    @staticmethod
    def _command_target_device_map(manifest: Any, devices: list[Any]) -> dict[int, tuple[Any, set[str]]]:
        target_map: dict[int, tuple[Any, set[str]]] = {}
        for command in manifest.commands:
            command_type = command.command
            target_role = command.target_device_role
            for device in devices:
                device_id = getattr(device, "id", None)
                if not isinstance(device_id, int):
                    continue
                if getattr(device, "device_role", None) != target_role:
                    continue
                if not WorkLineService._device_supports_command(device, command_type):
                    continue
                _, command_types = target_map.setdefault(device_id, (device, set()))
                command_types.add(command_type)
        return target_map

    @staticmethod
    def _rack_position_carrier_capability_checks(
        manifest: Any,
        rack_positions: list[Any] | None,
    ) -> list[WorkLineConfigurationCheck]:
        configured_rack_position_by_code = {
            getattr(configured_position, "position_code", None): configured_position
            for configured_position in rack_positions or []
            if isinstance(getattr(configured_position, "position_code", None), str)
        }
        checks: list[WorkLineConfigurationCheck] = []
        for rack_position in getattr(manifest, "rack_positions", ()):
            rack_position_code = getattr(rack_position, "code", None)
            configured_rack_position = configured_rack_position_by_code.get(rack_position_code)

            capability = rack_position.carrier_capability
            allowed_rack_kinds = [
                str(WorkLineService._manifest_value(rack_kind))
                for rack_kind in getattr(capability, "allowed_rack_kinds", ())
            ]
            min_capacity = capability.min_capacity
            max_capacity = capability.max_capacity
            if configured_rack_position is None:
                checks.append(
                    WorkLineService._check(
                        "RACK_POSITION_CARRIER_CAPABILITY",
                        _FAIL,
                        _BLOCKER,
                        {
                            "rack_position_code": rack_position_code,
                            "rack_position_role": getattr(rack_position, "role", None),
                            "station_code": getattr(rack_position, "station_code", None),
                            "missing_rack_position_config": True,
                            "enabled": False,
                            "allowed_rack_kind": None,
                            "allowed_rack_kinds": allowed_rack_kinds,
                            "capacity": None,
                            "min_capacity": min_capacity,
                            "max_capacity": max_capacity,
                        },
                    )
                )
                continue

            allowed_rack_kind = WorkLineService._rack_kind_value(
                getattr(configured_rack_position, "allowed_rack_kind", None)
            )
            capacity = getattr(configured_rack_position, "capacity", None)
            enabled = bool(getattr(configured_rack_position, "enabled", True))
            rack_kind_passes = allowed_rack_kind in allowed_rack_kinds
            capacity_passes = isinstance(capacity, int) and min_capacity <= capacity <= max_capacity
            rack_position_passes = enabled and rack_kind_passes and capacity_passes
            checks.append(
                WorkLineService._check(
                    "RACK_POSITION_CARRIER_CAPABILITY",
                    _OK if rack_position_passes else _FAIL,
                    "INFO" if rack_position_passes else _BLOCKER,
                    {
                        "rack_position_code": rack_position_code,
                        "rack_position_role": getattr(rack_position, "role", None),
                        "station_code": getattr(rack_position, "station_code", None),
                        "enabled": enabled,
                        "allowed_rack_kind": allowed_rack_kind,
                        "allowed_rack_kinds": allowed_rack_kinds,
                        "capacity": capacity,
                        "min_capacity": min_capacity,
                        "max_capacity": max_capacity,
                    },
                )
            )
        return checks

    @staticmethod
    def _rack_kind_value(value: Any) -> str | None:
        if value is None:
            return None
        return str(WorkLineService._manifest_value(value))

    @staticmethod
    def _device_supports_command(device: Any, command_type: str) -> bool:
        try:
            capabilities = parse_device_capabilities(getattr(device, "capabilities_json", None))
        except (TypeError, ValidationError, ValueError):
            return False
        return capabilities.supports_command(command_type)

    @staticmethod
    def _resolve_device_scheme(device: Any) -> str:
        protocol = getattr(device, "protocol", "HTTP")
        protocol_value = getattr(protocol, "value", protocol)
        scheme = str(protocol_value or "HTTP").lower()
        return scheme if scheme in {"http", "https"} else "http"

    @staticmethod
    def _resolve_device_status_path(device: Any) -> str | None:
        capabilities = getattr(device, "capabilities_json", None)
        if isinstance(capabilities, dict):
            for key in ("status_path", "device_status_path"):
                value = capabilities.get(key)
                if isinstance(value, str) and value.strip():
                    return WorkLineService._normalize_status_path(value)
        return _DEFAULT_DEVICE_STATUS_PATH

    @staticmethod
    def _normalize_status_path(value: str) -> str:
        path = value.strip()
        return path if path.startswith("/") else f"/{path}"

    @staticmethod
    def _validate_plugin_key(plugin_key: object) -> None:
        if not isinstance(plugin_key, str) or not plugin_key:
            return
        definition = get_workline_capability_definition(plugin_key)
        if definition is None:
            from src.core.exceptions import BadRequestException

            raise BadRequestException(message=f"不支持的工作线插件: {plugin_key}")
        try:
            _ = definition.manifest
        except (TypeError, ValueError) as exc:
            from src.core.exceptions import BadRequestException

            raise BadRequestException(message=str(exc)) from exc

    @staticmethod
    def _validate_plugin_contract_version(data: dict[str, Any], current: WorkLine | None = None) -> None:
        """校验契约版本快照必须来自插件 manifest。"""

        contract_version = data.get("contract_version")
        if not isinstance(contract_version, str) or not contract_version:
            return

        plugin_key = WorkLineService._resolve_plugin_key(data, current)
        resolved = get_workline_contract_version(plugin_key)
        if isinstance(resolved, str) and resolved and contract_version != resolved:
            from src.core.exceptions import BadRequestException

            raise BadRequestException(message=f"插件 {plugin_key} 的契约版本必须为 {resolved}")

    @staticmethod
    def _validate_run_mode(data: dict[str, Any], current: WorkLine | None = None) -> None:
        """校验 WORKLINE 级运行模式。

        SIMULATION 是真实派发到沙箱通道的调试模式，只允许在开发/测试环境启用。
        """

        raw_run_mode = data.get("run_mode", getattr(current, "run_mode", WorkLineRunMode.AUTO))
        run_mode = normalize_run_mode(raw_run_mode)
        if run_mode not in {item.value for item in WorkLineRunMode}:
            return

        if is_simulation_run_mode(run_mode) and not is_sandbox_allowed_environment(settings.APP_ENV):
            from src.core.exceptions import BadRequestException

            raise BadRequestException(message="SIMULATION 运行模式只能在 dev/test 环境启用")

    @staticmethod
    def _apply_runtime_defaults(data: dict[str, Any], current: WorkLine | None = None) -> None:
        """为工作线写入运行时治理默认值。"""
        plugin_key_explicit = "plugin_key" in data
        contract_version_explicit = "contract_version" in data
        plugin_key = WorkLineService._resolve_plugin_key(data, current)
        if plugin_key is None:
            if plugin_key_explicit and not contract_version_explicit:
                data["contract_version"] = None
            return
        if current is not None and not plugin_key_explicit and not contract_version_explicit:
            return

        resolved = get_workline_contract_version(plugin_key)
        if isinstance(resolved, str) and resolved:
            data["contract_version"] = resolved

    @staticmethod
    def _validate_runtime_config(data: dict[str, Any], current: WorkLine | None = None) -> None:
        """校验工作线运行时配置，仅约束当前已消费的关键字段。"""

        if "runtime_config_json" not in data and current is None:
            return

        runtime_config = data.get("runtime_config_json", getattr(current, "runtime_config_json", None))
        if runtime_config is None:
            return
        if not isinstance(runtime_config, dict):
            raise TypeError("runtime_config_json 必须为对象")

        event_mapping = runtime_config.get("event_type_mapping")
        if event_mapping is not None and not isinstance(event_mapping, dict):
            raise TypeError("runtime_config_json.event_type_mapping 必须为对象")
        if isinstance(event_mapping, dict):
            for source_event_type, mapped_event_type in event_mapping.items():
                if isinstance(mapped_event_type, str) and mapped_event_type:
                    assert_not_reserved_runtime_event(
                        mapped_event_type,
                        owner="runtime_config_json.event_type_mapping",
                        declaration_surface=f"{source_event_type} 的映射目标",
                    )


# 创建单例
workline_service = WorkLineService()
