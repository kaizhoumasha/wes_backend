from __future__ import annotations

import inspect
from pathlib import Path

from src.app.transport.service import TransportService

ROOT = Path(__file__).resolve().parents[2]


def test_transport_core_has_no_business_device_or_http_client_dependency() -> None:
    forbidden = (
        "httpx",
        "src.core.outbound_http",
        "DeviceCommand",
        "PickingTask",
        "workline_plugins",
        "src.app.device",
    )
    for path in (ROOT / "src/app/transport").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not any(value in source for value in forbidden), path


def test_wms_transport_adapter_only_uses_wms_client_boundary() -> None:
    for name in ("transport_adapter.py", "transport_event_handler.py", "transport_wire.py"):
        source = (ROOT / "src/app/wms_adapter" / name).read_text(encoding="utf-8")
        assert "src.core.outbound_http" not in source


def test_transport_remains_dark_and_exposes_four_internal_batch_entries() -> None:
    production_paths = [ROOT / "main.py", ROOT / "src/register.py", *(ROOT / "src/celery_app").rglob("*.py")]
    production_sources = "\n".join(path.read_text(encoding="utf-8") for path in production_paths)
    assert "build_transport_service" not in production_sources
    assert "TransportEventHandler" not in production_sources
    for method_name in (
        "submit_pending_tasks",
        "process_pending_evidence",
        "reconcile_overdue_tasks",
        "publish_pending_outcomes",
    ):
        assert inspect.iscoroutinefunction(getattr(TransportService, method_name))
