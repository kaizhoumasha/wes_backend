"""已关闭的旧 WMS/RCS transport 边界。

35 项 WMS operation registry 已成为唯一北向合同。T5 dispatcher 尚未实现，
因此所有仍调用旧 transport builder 的入口都必须明确失败，不能生成 outbox。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NoReturn

DEFAULT_RACK_OPERATION_ENDPOINT = "WMS_RCS_RACK_OPERATION"
BIN_OPERATION_ENDPOINT = "WMS_RCS_BIN_OPERATION"
FULL_BOX_EXCHANGE_ENDPOINT = "WMS_RCS_FULL_BOX_EXCHANGE"
SINGLE_LAYER_RACK_OPERATION_AUTHORITY_SYSTEM = "WMS"
SINGLE_LAYER_RACK_KIND = "SINGLE_LAYER"


class WmsTransportMigrationRequiredError(RuntimeError):
    """旧 transport 已移除且 T5 dispatcher 尚未提供时的 fail-closed 错误。"""


def _raise_transport_removed() -> NoReturn:
    raise WmsTransportMigrationRequiredError("legacy WMS transport is removed; T5 dispatcher is not implemented")


@dataclass(frozen=True, slots=True)
class WmsRackTaskRequest:
    """仅保留类型边界；旧 builder 不再产生实例。"""

    dispatch_key: str
    target_code: str
    payload_json: dict[str, Any]
    canonical_payload_bytes: bytes
    payload_hash: str


def freeze_legacy_transport_binding(**_: Any) -> NoReturn:
    """旧 transport 不再冻结 provider binding。"""

    _raise_transport_removed()


class WmsTransportContractService:
    """所有旧 transport producer 均已关闭。"""

    def __init__(self, **_: Any) -> None:
        pass

    def build_single_layer_rack_operation_request(self, **_: Any) -> NoReturn:
        _raise_transport_removed()

    def build_rack_task_request(self, **_: Any) -> NoReturn:
        _raise_transport_removed()

    def build_rack_task_envelope(self, **_: Any) -> NoReturn:
        _raise_transport_removed()

    def build_handling_ctu_move_envelope(self, **_: Any) -> NoReturn:
        _raise_transport_removed()


wms_transport_contract_service = WmsTransportContractService()

__all__ = [
    "BIN_OPERATION_ENDPOINT",
    "DEFAULT_RACK_OPERATION_ENDPOINT",
    "FULL_BOX_EXCHANGE_ENDPOINT",
    "SINGLE_LAYER_RACK_KIND",
    "SINGLE_LAYER_RACK_OPERATION_AUTHORITY_SYSTEM",
    "WmsRackTaskRequest",
    "WmsTransportContractService",
    "WmsTransportMigrationRequiredError",
    "freeze_legacy_transport_binding",
    "wms_transport_contract_service",
]
