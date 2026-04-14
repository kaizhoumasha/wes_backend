"""Workline 领域服务导出。

当前领域服务：

- `BarcodeDecisionService`: 条码提取、合法性校验、业务 NG 判定
- `barcode_decision_service`: 条码判定服务单例
"""

from .barcode_decision_service import BarcodeDecisionService, barcode_decision_service

__all__ = ["BarcodeDecisionService", "barcode_decision_service"]
