# 阶段 2 burn-down C5b 镜像:src.workline_runtime.plugin_sdk.normalizers 的平级副本
# wlr 目录在阶段 3 整体删除时,本包与 wlr 包合并 / 删除。

"""插件 SDK 标准化工具。"""

from .event_mapper import canonicalize_event_type
from .input_normalizer import normalize_inbox_input

__all__ = ["canonicalize_event_type", "normalize_inbox_input"]
