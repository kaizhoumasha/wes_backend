"""Workline Runtime 异常定义。"""


class WorklineRuntimeError(Exception):
    """Workline Runtime 基础异常。"""


class PluginNotFoundError(WorklineRuntimeError):
    """插件未找到异常。

    当配置的插件未注册且未显式允许 NullPlugin 时抛出。
    """


class LockAcquireError(WorklineRuntimeError):
    """锁获取失败异常。"""


class StateMachineError(WorklineRuntimeError):
    """状态机相关异常。"""
