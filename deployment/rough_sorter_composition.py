"""粗分机插件唯一静态部署组合根。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

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
from rough_sorter.plugin import PLUGIN_KEY, PLUGIN_VERSION, build_handlers

from deployment._rough_sorter_factory import (
    RoughSorterPluginFactFactory as _RoughSorterPluginFactFactory,
)
from deployment._rough_sorter_persistence import RoughSorterInitialExecutionCorrelator
from deployment._rough_sorter_transport import RoughSorterTransportOutcomePublisher
from deployment._rough_sorter_types import RoughSorterTypes
from deployment._rough_sorter_wms_resolver import RoughSorterWmsConfirmationRequestResolver
from src.app.execution.composition import ExecutionRuntime, build_execution_runtime
from src.app.execution.plugin_binding import PluginRuntimeBinding, StaticPluginBinding
from src.app.wms_adapter.inbound_adapter import WmsInboundAdapter, WmsInboundBusinessWaitPlanner
from src.core.task_queue_gateway import task_queue_gateway

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.device.services import DeviceCommandService
    from src.app.execution.services.wms_confirmation_service import WmsConfirmationAdapterPort
    from src.app.transport.composition import TransportRuntime

_ROUGH_SORTER_TYPES = RoughSorterTypes(
    plugin_key=PLUGIN_KEY,
    plugin_version=PLUGIN_VERSION,
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


def build_rough_sorter_runtime(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    transport_runtime: TransportRuntime,
    device_command_service: DeviceCommandService,
) -> RoughSorterDeploymentRuntime:
    """Web/Celery 共用的唯一静态 rough sorter 运行时装配。"""

    factory = RoughSorterPluginFactFactory()
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
    "RoughSorterWmsConfirmationRequestResolver",
    "build_rough_sorter_runtime",
]
