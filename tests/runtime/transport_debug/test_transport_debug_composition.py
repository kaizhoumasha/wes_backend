from types import SimpleNamespace
from unittest.mock import MagicMock

from src.app.transport_debug.composition import build_transport_debug_run_service


def test_debug_run_composes_over_independent_transport_runtime() -> None:
    transport_service = object()
    sessions = MagicMock()

    service = build_transport_debug_run_service(
        session_factory=sessions,
        transport_runtime=SimpleNamespace(service=transport_service),
    )

    assert service._transport is transport_service
    assert service._sessions is sessions
