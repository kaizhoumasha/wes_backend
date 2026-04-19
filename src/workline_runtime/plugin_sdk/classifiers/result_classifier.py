"""命令结果分类器。"""

_SUCCESS = {"SUCCESS", "OK", "DONE", "COMPLETED", "PASS"}
_RETRYABLE = {"TIMEOUT", "RETRY", "TEMP_FAILURE"}
_TERMINAL = {"FAILED", "ERROR", "NG", "REJECTED"}


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


__all__ = ["classify_result"]
