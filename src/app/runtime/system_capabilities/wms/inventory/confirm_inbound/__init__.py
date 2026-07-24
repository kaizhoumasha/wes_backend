"""入库确认 operation 合同与 gateway。"""

from .contract import CONTRACT
from .gateway import ConfirmInboundDispatchGateway

__all__ = ["CONTRACT", "ConfirmInboundDispatchGateway"]
