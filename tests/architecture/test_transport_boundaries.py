from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import get_type_hints

from src.app import transport
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


def test_shared_strict_json_decoder_does_not_import_transport_domain() -> None:
    tree = ast.parse(
        (ROOT / "src/app/wms_adapter/strict_json.py").read_text(encoding="utf-8"),
        filename="src/app/wms_adapter/strict_json.py",
    )

    assert not any(
        (isinstance(node, ast.ImportFrom) and node.module is not None and node.module.startswith("src.app.transport"))
        or (isinstance(node, ast.Import) and any(alias.name.startswith("src.app.transport") for alias in node.names))
        for node in ast.walk(tree)
    )


def _transport_imports_and_constructor_calls(path: Path) -> tuple[set[tuple[str, str]], list[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = {
        (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None and node.module.startswith("src.app.transport")
        for alias in node.names
    }
    calls = [node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    return imports, calls


def test_transport_public_api_exposes_only_stable_port_dtos_and_runtime() -> None:
    assert transport.__all__ == [
        "BinExchangePair",
        "BinMove",
        "ExchangeBinsRequest",
        "HandoffPosition",
        "MoveBinsRequest",
        "MoveRackRequest",
        "RackBinSlot",
        "RackFace",
        "RackPosition",
        "RotateRackRequest",
        "TransportCaller",
        "TransportHandle",
        "TransportOutcome",
        "TransportPort",
        "TransportRuntime",
        "build_transport_runtime",
    ]
    assert not hasattr(transport, "TransportService")

    port = transport.TransportPort
    expected_signatures = {
        "move_rack": {
            "client_request_id": str,
            "caller": transport.TransportCaller,
            "rack_id": str,
            "source": transport.RackPosition,
            "target": transport.RackPosition,
            "target_face": transport.RackFace,
            "return": transport.TransportHandle,
        },
        "rotate_rack": {
            "client_request_id": str,
            "caller": transport.TransportCaller,
            "rack_id": str,
            "position": transport.RackPosition,
            "target_face": transport.RackFace,
            "return": transport.TransportHandle,
        },
        "move_bins": {
            "client_request_id": str,
            "caller": transport.TransportCaller,
            "moves": tuple[transport.BinMove, ...],
            "return": transport.TransportHandle,
        },
        "exchange_bins": {
            "client_request_id": str,
            "caller": transport.TransportCaller,
            "exchange_pairs": tuple[transport.BinExchangePair, ...],
            "return": transport.TransportHandle,
        },
    }
    assert {name for name, value in port.__dict__.items() if inspect.iscoroutinefunction(value)} == set(
        expected_signatures
    )
    for method_name, expected_hints in expected_signatures.items():
        method = getattr(port, method_name)
        assert tuple(inspect.signature(method).parameters) == ("self", *tuple(expected_hints)[:-1])
        assert get_type_hints(method) == expected_hints


def test_transport_api_and_celery_use_only_production_composition_root() -> None:
    expected_imports = {
        ("src.app.transport.composition", "build_transport_runtime"),
        ("src.app.transport.composition", "validate_transport_runtime_profile"),
    }
    for relative_path in ("src/register.py", "src/celery_app/async_runtime.py"):
        imports, calls = _transport_imports_and_constructor_calls(ROOT / relative_path)
        assert imports == expected_imports, relative_path
        assert calls.count("build_transport_runtime") == 1, relative_path
        assert "TransportService" not in calls, relative_path


def test_transport_internal_batches_stay_on_internal_service() -> None:
    batch_methods = (
        "submit_pending_tasks",
        "process_pending_evidence",
        "reconcile_overdue_tasks",
        "publish_pending_outcomes",
    )
    implementations: dict[str, list[str]] = {name: [] for name in batch_methods}
    for path in sorted((ROOT / "src/app/transport").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for class_node in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            for method_node in class_node.body:
                if (
                    isinstance(method_node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and method_node.name in batch_methods
                ):
                    implementations[method_node.name].append(f"{path.name}:{class_node.name}")

    assert implementations == {name: ["service.py:TransportService"] for name in batch_methods}
    for method_name in batch_methods:
        assert inspect.iscoroutinefunction(getattr(TransportService, method_name))
