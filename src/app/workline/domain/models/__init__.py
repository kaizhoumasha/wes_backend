"""Workline 领域模型导出。

当前领域模型：

- `BarcodeDecision`: 条码业务判定结果
- `BarcodeDecisionType`: 条码判定枚举
"""

from src.app.runtime.capabilities.phase4.contracts.six_in_one import SixInOne

from .barcode_decision import BarcodeDecision, BarcodeDecisionType

_ = BarcodeDecision.model_rebuild(_types_namespace={"SixInOne": SixInOne})

__all__ = ["BarcodeDecision", "BarcodeDecisionType"]
