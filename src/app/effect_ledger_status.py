"""EFFECT transport 账本状态合同。

本模块不依赖领域包初始化，供状态机与持久化模型共同使用。
"""

from enum import Enum


class DispatchAttemptStatus(str, Enum):
    """单次 transport attempt 状态。

    DISPATCHING -> SENT / FAILED / UNKNOWN / CANCELLED
    UNKNOWN 终止本次 attempt；后续 reconciliation 不覆盖该 evidence。
    """

    DISPATCHING = "DISPATCHING"
    SENT = "SENT"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"


class SystemOutboxStatus(str, Enum):
    """唯一 transport 状态。

    NEW -> DISPATCHING -+-> SENT
                        +-> RETRY_WAIT -> DISPATCHING
                        +-> FAILED / UNKNOWN / CANCELLED
    UNKNOWN 是不可自动重试的送达歧义，不代表业务成功或失败。
    """

    NEW = "NEW"
    DISPATCHING = "DISPATCHING"
    RETRY_WAIT = "RETRY_WAIT"
    SENT = "SENT"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"


__all__ = ["DispatchAttemptStatus", "SystemOutboxStatus"]
