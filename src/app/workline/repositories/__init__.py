"""WorkLine Repository 导出。

WorkLine 运行态迁出后收缩为纯配置域 repository 聚合；运行态 repository 已物理迁入
runtime/orchestration/repositories/。
"""

from .workline_repository import WorkLineRepository, workline_repository

__all__ = [
    "WorkLineRepository",
    "workline_repository",
]
