"""Phase 3 plane snapshot lightweight benchmark."""

from __future__ import annotations

from time import perf_counter_ns

from src.app.workline.models.plane import PlaneSnapshot


def _p95_ms(samples_ns: list[int]) -> float:
    ordered = sorted(samples_ns)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
    return ordered[index] / 1_000_000


def _measure(operation, *, iterations: int) -> float:
    samples: list[int] = []
    for _ in range(iterations):
        started_at = perf_counter_ns()
        operation()
        samples.append(perf_counter_ns() - started_at)
    return _p95_ms(samples)


def _snapshot_payload(*, object_count: int) -> dict[str, object]:
    return {
        "schema_version": "plane.snapshot.v1",
        "workline_code": "WL-BENCH",
        "scene_schema_version": "plane.scene.v1",
        "objects": [
            {
                "object_code": f"OBJ-{index}",
                "object_label": f"Object {index}",
                "state": "IN_FLIGHT" if index % 2 else "IDLE",
            }
            for index in range(object_count)
        ],
        "extremes": [
            {
                "code": "RECONCILING",
                "label": "Reconciling",
                "severity": "warning",
            }
        ],
    }


def test_plane_snapshot_benchmark() -> None:
    payload = _snapshot_payload(object_count=100)
    payload_10x = _snapshot_payload(object_count=1000)

    def build_snapshot() -> None:
        snapshot = PlaneSnapshot.model_validate(payload)
        assert len(snapshot.objects) == 100

    def build_snapshot_10x() -> None:
        snapshot = PlaneSnapshot.model_validate(payload_10x)
        assert len(snapshot.objects) == 1000

    snapshot_p95_ms = _measure(build_snapshot, iterations=120)
    snapshot_10x_p95_ms = _measure(build_snapshot_10x, iterations=40)

    assert snapshot_p95_ms < 20.0
    assert snapshot_10x_p95_ms < 100.0
