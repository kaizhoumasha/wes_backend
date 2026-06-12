"""WorkLine Service 层"""

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models import parse_device_capabilities
from src.app.device.repositories import device_repository
from src.app.workline.models import (
    WorkLine,
    WorkLineConfigurationCheck,
    WorkLineConfigurationStatus,
    WorkLinePluginManifestSummary,
    WorkLinePluginOption,
    WorkLineRunMode,
)
from src.app.workline.models.rack_position import WorklineRackPosition
from src.app.workline.models.workline import (
    CommandBinding,
    CommandResultBinding,
    DeviceRequirement,
    EventBinding,
    FlowEdge,
    NodeRef,
    Position,
    PositionArg,
    PositionArgSource,
    PositionCarrierCapability,
    ResourceBoundary,
    TopologySpec,
)
from src.app.workline.repositories import WorkLineRepository, workline_repository
from src.app.workline.repositories.rack_position_repository import workline_rack_position_repository
from src.common.cache_config import cache_settings
from src.core.base_service import BaseService
from src.core.conf import settings
from src.core.exceptions import BusinessException, OptimisticLockException
from src.utils.device_cache import workline_device_cache
from src.workline_plugin_registry import (
    get_plugin_contract_version,
    get_workline_plugin_definition,
    list_workline_plugin_definitions,
    validate_workline_plugin_assignment,
)
from src.workline_runtime.run_mode import (
    is_sandbox_allowed_environment,
    is_simulation_run_mode,
    normalize_run_mode,
)
from src.workline_runtime.runtime_events import assert_not_reserved_runtime_event
from src.workline_runtime.topology import WorklineTopologyView

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


class WorkLineService(BaseService[WorkLine, WorkLineRepository]):
    """作业线业务逻辑层"""

    def __init__(self) -> None:
        super().__init__(
            workline_repository,
            enable_cache=True,
            cache_prefix=cache_settings.WORKLINE.prefix,
            cache_expire=cache_settings.WORKLINE.expire,
            list_cache_prefix=cache_settings.WORKLINE_LIST.prefix,
            list_cache_expire=cache_settings.WORKLINE_LIST.expire,
        )

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

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, Iterable):
            return [str(item) for item in value]
        return []

    @classmethod
    def _build_device_requirement_summary(cls, requirement: object) -> DeviceRequirement:
        return DeviceRequirement(
            role=requirement.role,
            min_count=requirement.min_count,
            max_count=getattr(requirement, "max_count", None),
            hardware_capabilities=cls._string_list(getattr(requirement, "hardware_capabilities", ())),
        )

    @classmethod
    def _build_position_carrier_capability_summary(cls, capability: object) -> PositionCarrierCapability:
        return PositionCarrierCapability(
            allowed_rack_kinds=cls._string_list(getattr(capability, "allowed_rack_kinds", ())),
            min_capacity=capability.min_capacity,
            max_capacity=capability.max_capacity,
            allowed_slot_kinds=cls._string_list(getattr(capability, "allowed_slot_kinds", ())),
        )

    @classmethod
    def _build_position_summary(cls, position: object) -> Position:
        return Position(
            code=position.code,
            role=position.role,
            station_code=position.station_code,
            carrier_capability=cls._build_position_carrier_capability_summary(position.carrier_capability),
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
            source_device_roles=cls._string_list(getattr(event, "source_device_roles", ())),
            category=str(cls._manifest_value(event.category)),
            payload_schema_ref=getattr(event, "payload_schema_ref", None),
        )

    @classmethod
    def _build_position_arg_source_summary(cls, source: object | None) -> PositionArgSource | None:
        if source is None:
            return None
        return PositionArgSource(
            kind=str(cls._manifest_value(source.kind)),
            path=source.path,
            fallback_position_ref=getattr(source, "fallback_position_ref", None),
        )

    @classmethod
    def _build_position_arg_summary(cls, arg: object) -> PositionArg:
        return PositionArg(
            name=arg.name,
            role=str(cls._manifest_value(arg.role)),
            required=getattr(arg, "required", True),
            position_ref=getattr(arg, "position_ref", None),
            source=cls._build_position_arg_source_summary(getattr(arg, "source", None)),
        )

    @classmethod
    def _build_command_result_binding_summary(cls, binding: object) -> CommandResultBinding:
        return CommandResultBinding(
            result=binding.result,
            event=binding.event,
            category=str(cls._manifest_value(binding.category)),
            classification=getattr(binding, "classification", None),
            terminal=getattr(binding, "terminal", False),
            next_event=getattr(binding, "next_event", None),
        )

    @classmethod
    def _build_command_binding_summary(cls, command: object) -> CommandBinding:
        return CommandBinding(
            command=command.command,
            target_device_role=command.target_device_role,
            position_args=[cls._build_position_arg_summary(arg) for arg in getattr(command, "position_args", ())],
            payload_schema_ref=getattr(command, "payload_schema_ref", None),
            result_bindings=[
                cls._build_command_result_binding_summary(binding)
                for binding in getattr(command, "result_bindings", ())
            ],
        )

    @staticmethod
    def _build_resource_boundary_summary(boundary: object) -> ResourceBoundary:
        return ResourceBoundary(
            position_code=boundary.position_code,
            rack_kind=boundary.rack_kind,
            business_demand_type=boundary.business_demand_type,
            wms_operation_type=boundary.wms_operation_type,
            snapshot_kind=boundary.snapshot_kind,
            lease_scope=boundary.lease_scope,
        )

    def list_plugin_options(self) -> list[WorkLinePluginOption]:
        """从插件注册表导出作业线插件/契约版本选项。"""

        options: list[WorkLinePluginOption] = []
        for definition in list_workline_plugin_definitions():
            manifest = definition.manifest
            plugin_key = definition.plugin_key
            options.append(
                WorkLinePluginOption(
                    plugin_key=plugin_key,
                    label=plugin_key,
                    contract_versions=[manifest.contract_version],
                    default_contract_version=manifest.contract_version,
                )
            )
        return options

    def get_plugin_manifest_summary(self, plugin_key: str) -> WorkLinePluginManifestSummary | None:
        """返回单个工作线插件 manifest 摘要。"""

        definition = get_workline_plugin_definition(plugin_key)
        if definition is None:
            return None

        manifest = definition.manifest
        plugin_key = definition.plugin_key
        return WorkLinePluginManifestSummary(
            plugin_key=plugin_key,
            contract_version=manifest.contract_version,
            devices=[
                self._build_device_requirement_summary(device)
                for device in self._manifest_sequence(manifest, plugin_key, "devices")
            ],
            positions=[
                self._build_position_summary(position)
                for position in self._manifest_sequence(manifest, plugin_key, "positions")
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
        )

    async def create(self, db: AsyncSession, data: dict[str, Any], cache: object | None = None) -> WorkLine | None:
        """创建工作线时仅校验插件标识，拓扑校验留到设备已关联后。"""
        self._reject_active_state_write(data)
        self._validate_plugin_key(data.get("plugin_key"))
        self._validate_plugin_contract_version(data)
        self._validate_run_mode(data)
        self._validate_runtime_config(data)
        self._apply_runtime_defaults(data)
        return await super().create(db, data, cache)

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
        validate_workline_plugin_assignment(plugin_key, workline_like, devices)

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
    ) -> WorkLine | None:
        """通过配置预检后启用 WorkLine。"""

        current = await self.repo.get_for_update(db, workline_id)
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
        if current.is_active:
            return current
        return await self._set_active_state(db, workline_id, is_active=True, version=version, cache=cache)

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
        submitted_fields = sorted(_ACTIVE_CONFIGURATION_FIELDS.intersection(data))
        if submitted_fields:
            raise BusinessException(
                message="已启用作业线下不能修改插件、合同或运行配置，请先停用作业线",
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
        _, positions = await workline_rack_position_repository.get_list(
            db,
            limit=1000,
            where_clauses_raw=[columns.workline_id == workline_id],
        )
        return positions

    def _build_configuration_checks(
        self,
        workline: WorkLine,
        devices: list[Any],
        rack_positions: list[Any] | None = None,
    ) -> list[WorkLineConfigurationCheck]:
        checks: list[WorkLineConfigurationCheck] = []
        plugin_key = self._resolve_plugin_key({}, workline)
        definition = get_workline_plugin_definition(plugin_key)
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
        checks.extend(self._position_carrier_capability_checks(manifest, rack_positions))
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
                    missing_capabilities = required_capabilities - device.capabilities
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
    def _position_carrier_capability_checks(
        manifest: Any,
        rack_positions: list[Any] | None,
    ) -> list[WorkLineConfigurationCheck]:
        if not rack_positions:
            return []

        rack_position_by_code = {
            getattr(position, "position_code", None): position
            for position in rack_positions
            if isinstance(getattr(position, "position_code", None), str)
        }
        checks: list[WorkLineConfigurationCheck] = []
        for position in getattr(manifest, "positions", ()):
            position_code = getattr(position, "code", None)
            rack_position = rack_position_by_code.get(position_code)
            if rack_position is None:
                continue

            capability = position.carrier_capability
            allowed_rack_kinds = [
                str(WorkLineService._manifest_value(rack_kind))
                for rack_kind in getattr(capability, "allowed_rack_kinds", ())
            ]
            allowed_rack_kind = WorkLineService._rack_kind_value(getattr(rack_position, "allowed_rack_kind", None))
            capacity = getattr(rack_position, "capacity", None)
            min_capacity = capability.min_capacity
            max_capacity = capability.max_capacity
            enabled = bool(getattr(rack_position, "enabled", True))
            rack_kind_passes = allowed_rack_kind in allowed_rack_kinds
            capacity_passes = isinstance(capacity, int) and min_capacity <= capacity <= max_capacity
            position_passes = enabled and rack_kind_passes and capacity_passes
            checks.append(
                WorkLineService._check(
                    "POSITION_CARRIER_CAPABILITY",
                    _OK if position_passes else _FAIL,
                    "INFO" if position_passes else _BLOCKER,
                    {
                        "position_code": position_code,
                        "position_role": getattr(position, "role", None),
                        "station_code": getattr(position, "station_code", None),
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
        definition = get_workline_plugin_definition(plugin_key)
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
        resolved = get_plugin_contract_version(plugin_key)
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

        resolved = get_plugin_contract_version(plugin_key)
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
