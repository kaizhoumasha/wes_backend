"""粗分机插件唯一静态部署组合根。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from rough_sorter.activation import POSITION_ROLES, RoughSorterConfigurationError, parse_activation_configuration
from rough_sorter.facts import (
    AdmissionDecidedFact,
    AdmissionResult,
    CompletionKind,
    CompletionResult,
    DeviceOutcome,
    DevicePositionConfirmedFact,
    DeviceStep,
    MaterialEvidenceReadyFact,
    PlacementCommandStatus,
    PlacementCompletedFact,
    PlacementConfirmationStatus,
    PlacementReleaseEvidence,
    PlacementResponseResult,
    RackMoveLegPlan,
    RackReleaseSnapshot,
    RecoveryDecidedFact,
    RecoveryDeferContinuation,
    RecoveryDeviceContinuation,
    RecoveryWmsContinuation,
    ReplacementPlanDecidedFact,
    ReplacementResult,
    RoughSorterRuntimeSnapshot,
    ShapeResult,
    TargetDecidedFact,
    TargetResult,
    TransportOutcome,
    TransportOutcomePublishedFact,
    rack_release_snapshot_ref,
)
from rough_sorter.handlers._guards import ROLE_CONTRACTS
from rough_sorter.plugin import PLUGIN_KEY, PLUGIN_VERSION, build_handlers

from deployment._rough_sorter_factory import (
    RoughSorterPluginFactFactory as _RoughSorterPluginFactFactory,
)
from deployment._rough_sorter_persistence import RoughSorterInitialExecutionCorrelator
from deployment._rough_sorter_transport import RoughSorterTransportOutcomePublisher
from deployment._rough_sorter_types import RoughSorterTypes
from deployment._rough_sorter_wms_resolver import RoughSorterWmsConfirmationRequestResolver
from src.app.device.repositories.device_repository import device_repository
from src.app.execution.composition import ExecutionRuntime, build_execution_runtime
from src.app.execution.plugin_binding import PluginRuntimeBinding, StaticPluginBinding
from src.app.wms_adapter.inbound_adapter import WmsInboundAdapter, WmsInboundBusinessWaitPlanner
from src.app.workline.epoch_activation import (
    LineRunEpochDeviceBindingInput,
    LineRunEpochPositionBindingInput,
    WorkLineEpochActivationPlan,
)
from src.app.workline.services.workline_start_service import WorkLineStartConfigurationError, WorkLineStartService
from src.core.task_queue_gateway import task_queue_gateway

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.device.services import DeviceCommandService
    from src.app.execution.services.wms_confirmation_service import WmsConfirmationAdapterPort
    from src.app.transport.composition import TransportRuntime

_ROUGH_SORTER_TYPES = RoughSorterTypes(
    plugin_key=PLUGIN_KEY,
    plugin_version=PLUGIN_VERSION,
    role_contracts=ROLE_CONTRACTS,
    position_roles=POSITION_ROLES,
    build_handlers=build_handlers,
    AdmissionDecidedFact=AdmissionDecidedFact,
    AdmissionResult=AdmissionResult,
    CompletionKind=CompletionKind,
    CompletionResult=CompletionResult,
    DeviceOutcome=DeviceOutcome,
    DevicePositionConfirmedFact=DevicePositionConfirmedFact,
    DeviceStep=DeviceStep,
    MaterialEvidenceReadyFact=MaterialEvidenceReadyFact,
    PlacementCommandStatus=PlacementCommandStatus,
    PlacementCompletedFact=PlacementCompletedFact,
    PlacementConfirmationStatus=PlacementConfirmationStatus,
    PlacementReleaseEvidence=PlacementReleaseEvidence,
    PlacementResponseResult=PlacementResponseResult,
    RackMoveLegPlan=RackMoveLegPlan,
    RackReleaseSnapshot=RackReleaseSnapshot,
    RecoveryDecidedFact=RecoveryDecidedFact,
    RecoveryDeferContinuation=RecoveryDeferContinuation,
    RecoveryDeviceContinuation=RecoveryDeviceContinuation,
    RecoveryWmsContinuation=RecoveryWmsContinuation,
    ReplacementPlanDecidedFact=ReplacementPlanDecidedFact,
    ReplacementResult=ReplacementResult,
    RoughSorterRuntimeSnapshot=RoughSorterRuntimeSnapshot,
    ShapeResult=ShapeResult,
    TargetDecidedFact=TargetDecidedFact,
    TargetResult=TargetResult,
    TransportOutcome=TransportOutcome,
    TransportOutcomePublishedFact=TransportOutcomePublishedFact,
    rack_release_snapshot_ref=rack_release_snapshot_ref,
)


class RoughSorterPluginFactFactory(_RoughSorterPluginFactFactory):
    """显式注入唯一 rough sorter 类型集合的部署 factory。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(types=_ROUGH_SORTER_TYPES, **kwargs)


@dataclass(frozen=True, slots=True)
class RoughSorterDeploymentRuntime:
    execution: ExecutionRuntime
    transport_outcome_publisher: RoughSorterTransportOutcomePublisher


class DeviceRepositoryPort(Protocol):
    async def get_by_work_line_id_for_update(self, db: Any, work_line_id: int) -> list[Any]: ...


class RoughSorterStartPlanBuilder:
    """锁定一次 Device 集合，在内存中形成完整基础激活计划。"""

    def __init__(
        self,
        *,
        device_repository: DeviceRepositoryPort = cast("DeviceRepositoryPort", device_repository),
    ) -> None:
        self._devices = device_repository

    async def build(self, db: Any, workline: Any) -> WorkLineEpochActivationPlan:
        config = getattr(workline, "config", None)
        try:
            if not isinstance(config, Mapping):
                raise RoughSorterConfigurationError("WorkLine.config 必须是对象")
            parsed = parse_activation_configuration(cast("Mapping[str, object]", config).get("rough_sorter"))
        except RoughSorterConfigurationError as exc:
            raise WorkLineStartConfigurationError(str(exc)) from exc

        devices = await self._devices.get_by_work_line_id_for_update(db, workline.id)
        device_bindings: list[LineRunEpochDeviceBindingInput] = []
        for role, contract_key in ROLE_CONTRACTS.items():
            matches = [device for device in devices if device.device_role == role]
            if len(matches) != 1:
                raise WorkLineStartConfigurationError(f"粗分机 WorkLine 必须且只能包含一个 {role}")
            device = matches[0]
            if not bool(device.is_active):
                raise WorkLineStartConfigurationError(f"{role} 未静态启用")
            if device.id is None or not device.endpoint_base_url:
                raise WorkLineStartConfigurationError(f"{role} 缺少可冻结的 Device Endpoint")
            contract = parsed.device_contracts[role]
            try:
                device_bindings.append(
                    LineRunEpochDeviceBindingInput(
                        device_id=device.id,
                        device_code=device.device_code,
                        device_role=role,
                        endpoint_base_url=device.endpoint_base_url,
                        contract_key=contract_key,
                        contract_version="1.0",
                        status_max_age_ms=contract.status_max_age_ms,
                        command_timeout_ms=contract.command_timeout_ms,
                    )
                )
            except ValueError as exc:
                raise WorkLineStartConfigurationError(f"{role} Device Endpoint 非法") from exc

        return WorkLineEpochActivationPlan(
            plugin_key=PLUGIN_KEY,
            plugin_version=PLUGIN_VERSION,
            flow_mode="ROUGH_SORT_INBOUND",
            configuration_snapshot=parsed.snapshot,
            device_bindings=tuple(device_bindings),
            position_bindings=tuple(
                LineRunEpochPositionBindingInput(
                    position_role=role,
                    location_id=parsed.position_bindings[role],
                    location_type=role,
                )
                for role in POSITION_ROLES
            ),
        )


def build_rough_sorter_start_service() -> WorkLineStartService:
    """API 进程唯一的粗分机 START 业务组合。"""

    return WorkLineStartService(plan_builder=RoughSorterStartPlanBuilder())


def build_rough_sorter_runtime(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    transport_runtime: TransportRuntime,
    device_command_service: DeviceCommandService,
) -> RoughSorterDeploymentRuntime:
    """Web/Celery 共用的唯一静态 rough sorter 运行时装配。"""

    factory = RoughSorterPluginFactFactory(transport_repository=transport_runtime.repository)
    plugin_binding = StaticPluginBinding(
        (
            PluginRuntimeBinding(
                plugin_key=PLUGIN_KEY,
                plugin_version=PLUGIN_VERSION,
                handlers=build_handlers(),
                fact_factory=factory,
                initial_execution_correlator=RoughSorterInitialExecutionCorrelator(),
            ),
        )
    )
    execution = build_execution_runtime(
        session_factory=session_factory,
        plugin_binding=plugin_binding,
        wms_request_resolver=RoughSorterWmsConfirmationRequestResolver(fact_factory=factory),
        device_command_service=device_command_service,
        transport_service=transport_runtime.service,
        wms_confirmation_adapter=cast("WmsConfirmationAdapterPort", WmsInboundAdapter(transport_runtime.client)),
        wms_business_wait_planner=WmsInboundBusinessWaitPlanner(),
        task_queue_gateway=task_queue_gateway,
    )
    return RoughSorterDeploymentRuntime(
        execution=execution,
        transport_outcome_publisher=RoughSorterTransportOutcomePublisher(
            session_factory=session_factory,
            evidence_service=execution.inbound_evidence_service,
        ),
    )


__all__ = [
    "RoughSorterDeploymentRuntime",
    "RoughSorterInitialExecutionCorrelator",
    "RoughSorterPluginFactFactory",
    "RoughSorterStartPlanBuilder",
    "RoughSorterWmsConfirmationRequestResolver",
    "build_rough_sorter_runtime",
    "build_rough_sorter_start_service",
]
