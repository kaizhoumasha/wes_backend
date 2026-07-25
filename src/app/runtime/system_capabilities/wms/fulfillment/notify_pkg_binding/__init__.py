"""料盘绑定通知 operation 合同、gateway 与 typed EFFECT。"""

from .contract import CONTRACT
from .effect_adapter import NotifyPackageBindingEffectAdapter, notify_package_binding_effect_adapter
from .effect_contract import (
    NotifyPackageBindingDispatchAccepted,
    NotifyPackageBindingEffectAdmission,
    NotifyPackageBindingEffectPrecondition,
)
from .gateway import NotifyPackageBindingDispatchGateway
from .intent_adapter import NotifyPackageBindingIntentAdapter, notify_package_binding_intent_adapter

__all__ = [
    "CONTRACT",
    "NotifyPackageBindingDispatchAccepted",
    "NotifyPackageBindingDispatchGateway",
    "NotifyPackageBindingEffectAdapter",
    "NotifyPackageBindingEffectAdmission",
    "NotifyPackageBindingEffectPrecondition",
    "NotifyPackageBindingIntentAdapter",
    "notify_package_binding_effect_adapter",
    "notify_package_binding_intent_adapter",
]
