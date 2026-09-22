"""Low-cardinality projection recovery counters exposed by admin metrics."""

from __future__ import annotations

from threading import Lock

_LOCK = Lock()
_METRICS: dict[str, int | float] = {
    "candidate_count": 0,
    "replayed_count": 0,
    "stale_suppressed_total": 0,
    "superseded_total": 0,
    "retryable_total": 0,
    "oldest_candidate_age": 0.0,
}


def record_batch(*, candidate_count: int, replayed_count: int) -> None:
    with _LOCK:
        _METRICS["candidate_count"] = candidate_count
        _METRICS["replayed_count"] = replayed_count


def record_retryable() -> None:
    with _LOCK:
        _METRICS["retryable_total"] += 1


def record_stale_suppressed() -> None:
    with _LOCK:
        _METRICS["stale_suppressed_total"] += 1


def snapshot() -> dict[str, int | float]:
    with _LOCK:
        return dict(_METRICS)


__all__ = ["record_batch", "record_retryable", "record_stale_suppressed", "snapshot"]
