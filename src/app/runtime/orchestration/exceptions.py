# 旧 plugin runtime 桥接实现:src.workline_runtime.exceptions 的门面副本
# 旧 runtime 入口删除后,本桥接承载对应正式边界。
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
