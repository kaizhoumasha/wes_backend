"""人工拣料插件的显式、无扫描业务组合。"""

from manual_picking.application.transport_outcome import ManualPickingTransportOutcomePublisher
from manual_picking.definition import DEFINITION
from manual_picking.handlers import PickingTaskPlanAppliedHandler
from manual_picking.prepare_policy import ManualPickingPreparePolicy

PLUGIN_KEY = DEFINITION.plugin_key
PLUGIN_VERSION = DEFINITION.plugin_version

type ManualPickingHandler = PickingTaskPlanAppliedHandler


def build_handlers() -> tuple[ManualPickingHandler, ...]:
    """构造当前已接入的纯业务 handler，不执行运行时注册。"""

    return (PickingTaskPlanAppliedHandler(),)


def build_prepare_policy() -> ManualPickingPreparePolicy:
    """构造 PickingTask prepare 业务策略。"""

    return ManualPickingPreparePolicy()


def build_transport_outcome_publisher() -> ManualPickingTransportOutcomePublisher:
    """构造原 Transport 结果的可靠接收适配。"""

    return ManualPickingTransportOutcomePublisher()


__all__ = [
    "PLUGIN_KEY",
    "PLUGIN_VERSION",
    "ManualPickingHandler",
    "build_handlers",
    "build_prepare_policy",
    "build_transport_outcome_publisher",
]
