"""
Hook 系统基础设施

提供通用的 Hook 机制，支持在操作前后执行自定义逻辑。

设计理念：
- 优先级控制：支持 Hook 执行顺序
- 条件执行：支持根据上下文条件决定是否执行
- 错误处理：支持自定义错误处理逻辑
- 同步/异步：自动识别并执行同步或异步 Hook
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from inspect import iscoroutinefunction
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


class HookType(str, Enum):
    """Hook 类型枚举"""

    BEFORE_CREATE = "before_create"
    AFTER_CREATE = "after_create"
    BEFORE_UPDATE = "before_update"
    AFTER_UPDATE = "after_update"
    BEFORE_DELETE = "before_delete"
    AFTER_DELETE = "after_delete"


@dataclass
class HookContext:
    """Hook 执行上下文"""

    session: AsyncSession
    params: dict[str, Any]
    results: dict[str, Any]


HookFunc = Callable[[HookContext], Any]


@dataclass
class Hook:
    """Hook 配置"""

    func: HookFunc
    priority: int = 0
    condition: Callable[[HookContext], bool] | None = None
    error_handler: Callable[[Exception, HookContext], Any] | None = None


class HookManager:
    """Hook 管理器"""

    def __init__(self):
        self.hooks: dict[HookType, list[Hook]] = defaultdict(list)

    def add_hook(
        self,
        hook_type: HookType,
        func: HookFunc,
        priority: int = 0,
        condition: Callable[[HookContext], bool] | None = None,
        error_handler: Callable[[Exception, HookContext], Any] | None = None,
    ) -> None:
        """添加 hook"""
        hook = Hook(
            func=func,
            priority=priority,
            condition=condition,
            error_handler=error_handler,
        )
        self.hooks[hook_type].append(hook)
        self.hooks[hook_type].sort(key=lambda x: x.priority)

    async def execute_hooks(self, hook_type: HookType, context: HookContext) -> None:
        """执行指定类型的 hooks"""
        for hook in self.hooks[hook_type]:
            if hook.condition and not hook.condition(context):
                continue

            try:
                if iscoroutinefunction(hook.func):
                    await hook.func(context)
                else:
                    hook.func(context)
            except Exception as e:
                if hook.error_handler:
                    hook.error_handler(e, context)
                else:
                    raise


__all__ = ["Hook", "HookContext", "HookFunc", "HookManager", "HookType"]
