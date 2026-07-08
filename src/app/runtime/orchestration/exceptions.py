# runtime migration C5b 桥接:src.workline_runtime.exceptions 的门面副本
# wlr 目录在阶段 3 整体删除时,本桥接与 wlr 副本合并 / 删除。
# C5a 未单独镜像 exceptions.py,本桥接为 C5b orchestrator_bridge 的
# PluginNotFoundError 引用提供 mirror 来源(C5a brief 漏报)。

"""Workline Runtime 异常定义。"""


class WorklineRuntimeError(Exception):
    """Workline Runtime 基础异常。"""


class PluginNotFoundError(WorklineRuntimeError):
    """插件未找到异常。

    当配置的插件未注册且未显式允许 NullPlugin 时抛出。
    """


class LockAcquireError(WorklineRuntimeError):
    """锁获取失败异常。"""
