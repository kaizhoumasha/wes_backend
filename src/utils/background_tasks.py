"""
BackgroundTasks 上下文管理模块

提供 FastAPI BackgroundTasks 的全局上下文管理，
使得 Repository 层可以透明地使用后台任务功能。

设计理念：
- 使用 contextvars 管理请求级别的 BackgroundTasks
- 通过全局依赖注入自动设置上下文
- Repository 层无需修改路由代码即可使用
- 支持降级：如果没有 BackgroundTasks，可以同步执行

使用示例：
    # 1. 在应用级别配置全局依赖（一次配置）
    app = FastAPI(dependencies=[Depends(inject_background_tasks)])

    # 2. 在 Repository 或 Service 中使用
    background_tasks = get_background_tasks()
    if background_tasks:
        background_tasks.add_task(some_function, arg1, arg2)
"""

from contextvars import ContextVar

from fastapi import BackgroundTasks

# 请求级别的 BackgroundTasks 上下文变量
_background_tasks: ContextVar["BackgroundTasks | None"] = ContextVar("_background_tasks", default=None)


def set_background_tasks(tasks: BackgroundTasks) -> None:
    """
    设置当前请求的 BackgroundTasks

    Args:
        tasks: FastAPI BackgroundTasks 实例

    Note:
        通常由全局依赖注入自动调用，无需手动调用
    """
    _ = _background_tasks.set(tasks)


def get_background_tasks() -> BackgroundTasks | None:
    """
    获取当前请求的 BackgroundTasks

    Returns:
        BackgroundTasks 实例，如果不存在则返回 None

    Example:
        background_tasks = get_background_tasks()
        if background_tasks:
            background_tasks.add_task(send_email, email="user@example.com")
        else:
            # 降级为同步执行
            await send_email(email="user@example.com")
    """
    return _background_tasks.get()


async def inject_background_tasks(background_tasks: BackgroundTasks) -> None:
    """
    全局依赖函数：自动注入 BackgroundTasks 到上下文

    Args:
        background_tasks: FastAPI 自动注入的 BackgroundTasks

    Usage:
        # 在应用级别配置（推荐）
        app = FastAPI(dependencies=[Depends(inject_background_tasks)])

        # 或在路由器级别配置
        router = APIRouter(dependencies=[Depends(inject_background_tasks)])

    Note:
        配置后，所有路由都会自动执行此依赖，将 BackgroundTasks 注入到上下文中。
        Repository 层可以通过 get_background_tasks() 获取并使用。
    """
    set_background_tasks(background_tasks)


__all__ = [
    "get_background_tasks",
    "inject_background_tasks",
    "set_background_tasks",
]
