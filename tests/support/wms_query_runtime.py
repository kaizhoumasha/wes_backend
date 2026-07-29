"""WMS QUERY data lane runtime 的 heavy-test 注入辅助。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import pytest
    from pydantic import BaseModel

    from src.app.wms_integration.ports.query_outcome import WmsQueryOutcome


class StubWmsQueryExecutionPort:
    """以 request handler 模拟唯一的泛型 QUERY Port。"""

    def __init__(self, handler: Callable[[BaseModel], Awaitable[WmsQueryOutcome[Any]]]) -> None:
        self._handler = handler

    async def execute(self, request: BaseModel) -> WmsQueryOutcome[Any]:
        return await self._handler(request)


def bind_stub_wms_query_runtime(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[BaseModel], Awaitable[WmsQueryOutcome[Any]]],
) -> StubWmsQueryExecutionPort:
    """让 heavy-test 的 runtime service 解析到指定泛型 QUERY Port。"""

    port = StubWmsQueryExecutionPort(handler)
    monkeypatch.setattr(
        "src.app.wms_integration.query_runtime.get_wms_data_lane_query_runtime",
        lambda: port,
    )
    return port


__all__ = ["StubWmsQueryExecutionPort", "bind_stub_wms_query_runtime"]
