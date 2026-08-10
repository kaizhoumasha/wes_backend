"""WorkLine Repository 导出。

WorkLine 运行态迁出后收缩为纯配置域 repository 聚合:
workline_repository + safety_incident_repository。运行态 repository 已物理迁入
runtime/orchestration/repositories/。
"""

from .safety_incident_repository import WorklineSafetyIncidentRepository, workline_safety_incident_repository
from .workline_repository import WorkLineRepository

__all__ = [
    "WorkLineRepository",
    "WorklineSafetyIncidentRepository",
    "workline_safety_incident_repository",
]
