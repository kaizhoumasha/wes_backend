"""现有 WMS typed client 到异步库存查询 Port 的 attempt-scoped adapter。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from pydantic import ValidationError

from src.app.wms_integration.models import QueryInventoryRequest, QueryInventoryResponse
from src.app.wms_integration.ports.inventory_query import (
    WmsInventoryItem,
    WmsInventoryQueryContractError,
    WmsInventoryQueryRejected,
    WmsInventoryQueryUnavailable,
)
from src.app.wms_integration.services.exceptions import (
    WmsBusinessRejectedError,
    WmsIntegrationError,
    WmsUnavailableError,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class _TypedInventoryClient(Protocol):
    async def query_inventory(self, request: QueryInventoryRequest) -> Any: ...


_PROVIDER_CONTRACT_REASON_CODES = frozenset(
    {
        "WMS_RESPONSE_PARSE_ERROR",
        "WMS_UNSUPPORTED_HTTP_METHOD",
    }
)


class WmsInventoryQueryPortAdapter:
    """每 attempt 新建的 DTO/Port adapter。

    Decimal quantity 显式转为 Port float；转换结果确定，但不承诺 Decimal 精度。
    """

    def __init__(self, client: _TypedInventoryClient, *, request_id_factory: Callable[[], str]) -> None:
        self._client = client
        self._request_id_factory = request_id_factory

    async def query_inventory(
        self,
        material_code: str,
        *,
        warehouse_code: str | None = None,
    ) -> list[WmsInventoryItem]:
        request = QueryInventoryRequest(
            request_id=self._request_id_factory(),
            sku=material_code,
            warehouse_code=warehouse_code,
        )
        try:
            raw_response = await self._client.query_inventory(request)
        except WmsBusinessRejectedError as exc:
            raise WmsInventoryQueryRejected("WMS inventory query rejected") from exc
        except WmsUnavailableError as exc:
            if exc.reason_code in _PROVIDER_CONTRACT_REASON_CODES:
                raise WmsInventoryQueryContractError("invalid WMS inventory response") from exc
            raise WmsInventoryQueryUnavailable("WMS inventory query unavailable") from exc
        except (TimeoutError, WmsIntegrationError) as exc:
            raise WmsInventoryQueryUnavailable("WMS inventory query unavailable") from exc
        try:
            response = QueryInventoryResponse.model_validate(raw_response)
            return [
                WmsInventoryItem(
                    material_code=item.sku,
                    warehouse_code=item.warehouse_code or warehouse_code or "UNKNOWN",
                    # 现有 typed DTO 不公开库位；保留空值，不能在 adapter 猜测 provider 字段。
                    storage_location_code="",
                    quantity=float(item.available_qty),
                    batch_no=item.lot_no,
                )
                for item in response.items
            ]
        except (TypeError, ValueError, ValidationError) as exc:
            raise WmsInventoryQueryContractError("invalid WMS inventory response") from exc


def build_wms_inventory_query_port_factory(
    client: _TypedInventoryClient,
    *,
    request_id_factory: Callable[[], str],
) -> Callable[[], WmsInventoryQueryPortAdapter]:
    """返回零参 factory；CapabilityPortRegistry 默认每次构造新 adapter。"""

    def factory() -> WmsInventoryQueryPortAdapter:
        return WmsInventoryQueryPortAdapter(client, request_id_factory=request_id_factory)

    return factory


__all__ = ["WmsInventoryQueryPortAdapter", "build_wms_inventory_query_port_factory"]
