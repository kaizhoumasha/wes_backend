"""AGV/CTU 通用搬运能力。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.app.transport.composition import TransportRuntime, build_transport_runtime
    from src.app.transport.contracts import (
        BinExchangePair,
        BinMove,
        ExchangeBinsRequest,
        HandoffPosition,
        MoveBinsRequest,
        MoveRackRequest,
        RackBinSlot,
        RackMovePosition,
        RackPosition,
        RackReference,
        RcsTemplateId,
        RotateRackRequest,
        TransportCaller,
        TransportHandle,
        TransportOutcome,
        TransportPort,
        ZonePosition,
    )

__all__ = [
    "BinExchangePair",
    "BinMove",
    "ExchangeBinsRequest",
    "HandoffPosition",
    "MoveBinsRequest",
    "MoveRackRequest",
    "RackBinSlot",
    "RackMovePosition",
    "RackPosition",
    "RackReference",
    "RcsTemplateId",
    "RotateRackRequest",
    "TransportCaller",
    "TransportHandle",
    "TransportOutcome",
    "TransportPort",
    "TransportRuntime",
    "ZonePosition",
    "build_transport_runtime",
]

_CONTRACT_EXPORTS = frozenset(__all__) - {"TransportRuntime", "build_transport_runtime"}


def __getattr__(name: str) -> Any:
    """按需装载合同或运行时，避免纯 wire import 提前连接数据库配置。"""

    if name in _CONTRACT_EXPORTS:
        module_name = "src.app.transport.contracts"
    elif name in {"TransportRuntime", "build_transport_runtime"}:
        module_name = "src.app.transport.composition"
    else:
        raise AttributeError(name)

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
