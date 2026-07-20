"""粗分机 WMS 库存准入 capability。"""

from .contracts import (
    RoughSorterBindingSnapshot,
    RoughSorterInventoryAdmissionInput,
    RoughSorterInventoryAdmissionOutput,
)
from .definition import DEFINITION, PROFILE_IDENTITY
from .handler import RoughSorterInventoryAdmissionHandler

__all__ = [
    "DEFINITION",
    "PROFILE_IDENTITY",
    "RoughSorterBindingSnapshot",
    "RoughSorterInventoryAdmissionHandler",
    "RoughSorterInventoryAdmissionInput",
    "RoughSorterInventoryAdmissionOutput",
]
