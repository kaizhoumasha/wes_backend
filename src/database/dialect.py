"""数据库方言解析工具。"""

from __future__ import annotations

from inspect import isawaitable
from typing import Any


def dialect_name(db: Any) -> str | None:
    """解析数据库方言；异步 mock bind 明确视为未知方言。"""

    get_bind = getattr(db, "get_bind", None)
    bind = get_bind() if callable(get_bind) else getattr(db, "bind", None)
    if isawaitable(bind):
        close = getattr(bind, "close", None)
        if callable(close):
            _ = close()
        return None
    dialect = getattr(bind, "dialect", None)
    name = getattr(dialect, "name", None)
    return name if isinstance(name, str) else None
