"""Workline 领域层导出。

按领域职责对外暴露统一入口：

- `models`: 领域模型，承载领域概念与结构化结果
- `services`: 领域服务，承载可复用的业务规则与判定逻辑
"""

# pyright: reportUnsupportedDunderAll=false

# models: 领域模型
from .models import BarcodeDecision, BarcodeDecisionType

__all__ = [
    "BarcodeDecision",
    "BarcodeDecisionService",
    "BarcodeDecisionType",
    "SmtRackBinSchedulingDecision",
    "SmtRackBinSchedulingDecisionKind",
    "SmtRackBinSchedulingService",
    "SmtRackOperationRequest",
    "barcode_decision_service",
]


def __getattr__(name: str) -> object:
    """懒加载领域服务导出，避免 contract/catalog import 时拉起 runtime model。"""

    if name in {
        "BarcodeDecisionService",
        "SmtRackBinSchedulingDecision",
        "SmtRackBinSchedulingDecisionKind",
        "SmtRackBinSchedulingService",
        "SmtRackOperationRequest",
        "barcode_decision_service",
    }:
        from . import services

        return getattr(services, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
