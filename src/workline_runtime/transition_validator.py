"""
TransitionValidator - 状态迁移校验器

Phase 2 默认行为：
- 无状态机时允许所有迁移（向后兼容）
- 只记录日志，不阻止业务流程

Phase 3 状态机校验：
- 使用 transitions 库的状态机校验
- 拦截无效迁移，返回具体错误信息

设计参考: 设计文档 phase2-orchestrator
"""

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransitionDecision:
    """一次插件 transition 的校验和目标状态解析结果。"""

    valid: bool
    transition: str | None
    from_plugin_state: str | None
    to_plugin_state: str | None = None
    error: str | None = None
    applied: bool = False


class TransitionValidator:
    """状态迁移校验器

    Phase 2 行为：
    - 无状态机时允许所有迁移
    - 返回 (True, None) 表示允许

    Phase 3 行为：
    - 有状态机时校验迁移有效性
    - 返回 (False, error_message) 表示拒绝

    Attributes:
        None - 无状态，纯校验逻辑
    """

    def resolve(
        self,
        current_state: str | None,
        transition: str | None,
        state_machine_class: type[Any] | None,
    ) -> TransitionDecision:
        """校验状态迁移并解析目标插件状态。"""

        if not transition:
            return TransitionDecision(
                valid=True,
                transition=None,
                from_plugin_state=current_state,
                to_plugin_state=None,
                applied=False,
            )

        if state_machine_class is None:
            logger.debug("No state machine configured, allowing transition: %s", transition)
            return TransitionDecision(
                valid=True,
                transition=transition,
                from_plugin_state=current_state,
                to_plugin_state=current_state,
                applied=False,
            )

        try:
            model = _MockModel(state=current_state)
            state_machine = state_machine_class(model)

            if not state_machine.may_trigger(transition):
                error = (
                    f"Invalid transition '{transition}' from state '{current_state}'. "
                    f"Check state machine definition for allowed transitions."
                )
                logger.warning(error)
                return TransitionDecision(
                    valid=False,
                    transition=transition,
                    from_plugin_state=current_state,
                    error=error,
                )

            trigger = getattr(model, transition, None)
            if not callable(trigger):
                return TransitionDecision(
                    valid=True,
                    transition=transition,
                    from_plugin_state=current_state,
                    to_plugin_state=current_state,
                    applied=False,
                )

            trigger()
            to_state = getattr(model, "state", current_state)
            return TransitionDecision(
                valid=True,
                transition=transition,
                from_plugin_state=current_state,
                to_plugin_state=to_state if isinstance(to_state, str) and to_state else current_state,
                applied=True,
            )

        except Exception as e:
            error = f"Transition validation error: {e}"
            logger.exception(error)
            return TransitionDecision(
                valid=False,
                transition=transition,
                from_plugin_state=current_state,
                error=error,
            )

    def validate(
        self,
        current_status: str,
        transition: str | None,
        state_machine_class: type[Any] | None,
    ) -> tuple[bool, str | None]:
        """兼容旧测试/调用点的校验接口；新代码应使用 `resolve`。"""

        decision = self.resolve(current_status, transition, state_machine_class)
        return decision.valid, decision.error


class _MockModel:
    """临时模型对象，用于状态机校验

    状态机需要一个带有 state 属性的模型对象。
    这个内部类提供最小化的模型实现。
    """

    def __init__(self, state: str | None):
        self.state = state


__all__ = ["TransitionDecision", "TransitionValidator"]
