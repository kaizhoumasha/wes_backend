"""人工 Bin 处理插件的显式、无扫描 handler 组合。

Task 0-1 阶段只搭建插件骨架, `build_handlers()` 返回空 tuple。
Task 2/3 冻结领域模型和插件 Decision 后, 在此显式登记固定 handler 实例,
不引入动态扫描或运行时发现机制。
"""

from __future__ import annotations

PLUGIN_KEY = "manual_bin_processing"
PLUGIN_VERSION = "0.1.0"

type ManualBinProcessingHandler = object


def build_handlers() -> tuple[ManualBinProcessingHandler, ...]:
    """只在部署组合时构造固定 handler. 模块导入不实例化依赖."""

    return ()


__all__ = ["PLUGIN_KEY", "PLUGIN_VERSION", "ManualBinProcessingHandler", "build_handlers"]
