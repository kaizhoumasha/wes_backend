from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.core.query_models import FilterGroup, SortField


@runtime_checkable
class CrudServiceProtocol(Protocol):
    response_schema: type | None

    async def get_by_id(
        self,
        db: AsyncSession,
        cache: object,
        id: int,
        max_depth: int = 2,
        include_deleted: bool = False,
    ) -> Any | None: ...

    async def get_list(
        self,
        db: AsyncSession,
        cache: object,
        limit: int = 10,
        offset: int = 0,
        filters: FilterGroup | None = None,
        sort: list[SortField] | None = None,
        max_depth: int = 1,
        include_deleted: bool = False,
    ) -> tuple[int, list[Any]]: ...

    async def create(
        self,
        db: AsyncSession,
        data: dict[str, Any],
        cache: object | None = None,
    ) -> Any | None: ...

    async def update(
        self,
        db: AsyncSession,
        id: int,
        data: dict[str, Any],
        cache: object | None = None,
    ) -> Any | None: ...

    async def delete(self, db: AsyncSession, id: int, cache: object | None = None) -> bool | None: ...

    async def restore(self, db: AsyncSession, id: int, cache: object | None = None) -> Any | None: ...

    async def get_deleted(self, db: AsyncSession, limit: int = 10, offset: int = 0) -> tuple[int, list[Any]]: ...

    async def permanent_delete(self, db: AsyncSession, id: int, cache: object | None = None) -> bool: ...

    def to_response(self, model: Any, response_schema: type) -> Any: ...

    def to_list_response(self, models: list[Any], response_schema: type) -> list[Any]: ...


@runtime_checkable
class TreeServiceProtocol(CrudServiceProtocol, Protocol):
    async def get_tree(
        self,
        db: AsyncSession,
        root_id: int | None = None,
        max_depth: int = -1,
    ) -> list[Any]: ...

    async def get_siblings(
        self,
        db: AsyncSession,
        node_id: int,
        include_self: bool = False,
    ) -> list[Any]: ...

    async def get_ancestors(
        self,
        db: AsyncSession,
        node_id: int,
        include_self: bool = False,
    ) -> list[Any]: ...

    async def move_node(
        self,
        db: AsyncSession,
        node_id: int,
        new_parent_id: int | None,
    ) -> Any: ...


__all__ = ["CrudServiceProtocol", "TreeServiceProtocol"]
