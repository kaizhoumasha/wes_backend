"""WMS QUERY 共用的泛型执行 Port。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pydantic import BaseModel

    from src.app.wms_integration.ports.query_outcome import WmsQueryOutcome


class WmsQueryExecutionPort(Protocol):
    """按 request model 从静态 registry 唯一解析 operation 并执行。"""

    async def execute(self, request: BaseModel) -> WmsQueryOutcome[Any]: ...


__all__ = ["WmsQueryExecutionPort"]
