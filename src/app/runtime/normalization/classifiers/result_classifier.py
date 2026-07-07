# 阶段 2 burn-down C5b 镜像:src.workline_runtime.plugin_sdk.classifiers.result_classifier 的平级副本
# wlr 目录在阶段 3 整体删除时,本镜像与 wlr 副本合并 / 删除。

"""命令结果分类器。"""

from typing import Any, Literal, cast

_SUCCESS = {"SUCCESS", "OK", "DONE", "COMPLETED", "PASS"}
_RETRYABLE = {"TIMEOUT", "RETRY", "TEMP_FAILURE"}
_TERMINAL = {"FAILED", "ERROR", "NG", "REJECTED"}
_BUSINESS_CLASSIFICATIONS = {
    "business_decision",
    "hardware_failure",
    "data_invalid",
    "system_failure",
}

ResultClassification = Literal[
    "business_decision",
    "hardware_failure",
    "data_invalid",
    "system_failure",
]


def classify_result(value: str | None) -> str:
    """将供应商 result 归一化为统一语义。"""

    normalized = (value or "UNKNOWN").strip().upper()
    if normalized in _SUCCESS:
        return "SUCCESS"
    if normalized in _RETRYABLE:
        return "RETRYABLE_FAILURE"
    if normalized in _TERMINAL:
        return "TERMINAL_FAILURE"
    return normalized or "UNKNOWN"


def normalize_result_classification(value: str | None) -> ResultClassification | None:
    """规范化业务/异常分类。"""

    normalized = (value or "").strip().lower()
    if not normalized:
        return None
    if normalized not in _BUSINESS_CLASSIFICATIONS:
        raise ValueError(f"Unsupported result classification: {value}")
    return cast("ResultClassification", normalized)


def classify_result_category(
    value: str | None,
    *,
    error_detail: dict[str, Any] | None = None,
) -> ResultClassification | None:
    """按通用规则给命令结果增加失败分类。

    业务 NG 不在通用层硬编码，必须由插件运行时 result classifier 显式覆盖。
    """

    normalized_result = classify_result(value)
    if normalized_result == "SUCCESS":
        return None
    if normalized_result == "TERMINAL_FAILURE":
        return "hardware_failure" if error_detail else "system_failure"
    if normalized_result == "RETRYABLE_FAILURE":
        return "system_failure"
    return "system_failure"


__all__ = [
    "ResultClassification",
    "classify_result",
    "classify_result_category",
    "normalize_result_classification",
]
