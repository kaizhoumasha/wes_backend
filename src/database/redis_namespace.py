"""Canonical Redis namespace derived from the active PostgreSQL database."""

from __future__ import annotations

import hashlib
import re

_POSTGRES_DATABASE_IDENTITY_PATTERN = re.compile(r"[a-z0-9_]{1,63}\Z")


def database_redis_cache_prefix(database_identity: str) -> str:
    """Return the opaque Redis root namespace for one PostgreSQL database."""
    if not isinstance(database_identity, str) or not _POSTGRES_DATABASE_IDENTITY_PATTERN.fullmatch(database_identity):
        raise ValueError("POSTGRES_DB 必须由 1-63 个小写字母、数字或下划线组成")
    digest = hashlib.sha256(database_identity.encode("utf-8")).hexdigest()
    return f"app:{digest}"


__all__ = ["database_redis_cache_prefix"]
