"""Durable low-cardinality projection recovery metrics with Redis degradation."""

from __future__ import annotations

from threading import Lock
from typing import Any

from src.database.redis_client import get_redis

_REDIS_KEY = "wes:metrics:transport_projection_recovery"
_LOCK = Lock()
_DEFAULTS: dict[str, int | float] = {
    "candidate_count": 0,
    "replayed_count": 0,
    "superseded_total": 0,
    "retryable_total": 0,
    "orphan_sample_count": 0,
    "oldest_candidate_age": 0.0,
}
_METRICS = dict(_DEFAULTS)


async def _redis_call(method: str, *args: object, **kwargs: object) -> Any | None:
    client = get_redis()
    if client is None:
        return None
    try:
        return await getattr(client, method)(*args, **kwargs)
    except Exception:
        return None


async def record_batch(*, candidate_count: int, replayed_count: int, oldest_candidate_age: float) -> None:
    values: dict[str, int | float] = {
        "candidate_count": candidate_count,
        "replayed_count": replayed_count,
        "oldest_candidate_age": max(oldest_candidate_age, 0.0),
    }
    with _LOCK:
        _METRICS.update(values)
    await _redis_call("hset", _REDIS_KEY, mapping=values)


async def _increment(name: str) -> None:
    with _LOCK:
        _METRICS[name] += 1
    await _redis_call("hincrby", _REDIS_KEY, name, 1)


async def record_retryable() -> None:
    await _increment("retryable_total")


async def record_orphan_sample(count: int) -> None:
    with _LOCK:
        _METRICS["orphan_sample_count"] = count
    await _redis_call("hset", _REDIS_KEY, mapping={"orphan_sample_count": count})


async def record_superseded() -> None:
    await _increment("superseded_total")


async def snapshot() -> dict[str, int | float]:
    values = await _redis_call("hgetall", _REDIS_KEY)
    if isinstance(values, dict) and values:
        parsed = dict(_DEFAULTS)
        for name in parsed:
            value = values.get(name) or values.get(name.encode())
            if value is None:
                continue
            parsed[name] = float(value) if name == "oldest_candidate_age" else int(value)
        return parsed
    with _LOCK:
        return dict(_METRICS)


__all__ = [
    "record_batch",
    "record_orphan_sample",
    "record_retryable",
    "record_superseded",
    "snapshot",
]
