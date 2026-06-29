# 阶段 2 burn-down C5b 镜像:src.workline_runtime.plugin_sdk.classifiers 的平级副本
# wlr 目录在阶段 3 整体删除时,本包与 wlr 包合并 / 删除。

"""插件 SDK 分类器。"""

from .result_classifier import (
    ResultClassification,
    classify_result,
    classify_result_category,
    normalize_result_classification,
)

__all__ = [
    "ResultClassification",
    "classify_result",
    "classify_result_category",
    "normalize_result_classification",
]
