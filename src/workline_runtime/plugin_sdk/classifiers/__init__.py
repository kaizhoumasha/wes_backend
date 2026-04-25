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
