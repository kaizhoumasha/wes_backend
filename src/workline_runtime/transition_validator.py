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
from typing import Any

logger = logging.getLogger(__name__)


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

    def validate(
        self,
        current_status: str,
        transition: str | None,
        state_machine_class: type[Any] | None,
    ) -> tuple[bool, str | None]:
        """校验状态迁移是否有效

        Args:
            current_status: 当前状态
            transition: 待触发的迁移名称
            state_machine_class: 状态机类（Phase 3 提供）

        Returns:
            (is_valid, error_message):
                - (True, None): 迁移有效
                - (False, error_str): 迁移无效，包含错误原因

        Example:
            >>> validator = TransitionValidator()
            >>> validator.validate("RUNNING", "scan_ok", None)
            (True, None)
        """
        # Phase 2: 无状态机时允许所有迁移
        if state_machine_class is None:
            logger.debug(f"No state machine configured, allowing transition: {current_status} -> {transition}")
            return True, None

        # Phase 3: 使用状态机校验
        try:
            # 创建临时模型对象，用于状态机校验
            model = _MockModel(state=current_status)
            state_machine = state_machine_class(model)

            # 使用 may_trigger 检查迁移是否有效
            if state_machine.may_trigger(transition):
                return True, None

            # 迁移无效
            error = (
                f"Invalid transition '{transition}' from state '{current_status}'. "
                f"Check state machine definition for allowed transitions."
            )
            logger.warning(error)
            return False, error

        except Exception as e:
            # 状态机异常（初始化失败等）
            error = f"Transition validation error: {e}"
            logger.exception(error)
            return False, error


class _MockModel:
    """临时模型对象，用于状态机校验

    状态机需要一个带有 state 属性的模型对象。
    这个内部类提供最小化的模型实现。
    """

    def __init__(self, state: str):
        self.state = state


__all__ = ["TransitionValidator"]
