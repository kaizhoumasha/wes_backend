"""E03/E07 WMS target runtime 的静态装配合同。"""

from types import SimpleNamespace

from deployment import rough_sorter_composition
from src.app.wms_adapter.execution_confirmation_adapter import WmsConfirmationTypedRouter
from src.app.wms_adapter.execution_confirmation_resolver import WmsConfirmationRequestTypedRouter


def test_e03_e07_reuse_transport_runtime_client_and_typed_resolver(monkeypatch) -> None:
    captured: dict[str, object] = {}
    execution = SimpleNamespace(inbound_evidence_service=object())

    def build_execution_runtime(**kwargs: object) -> object:
        captured.update(kwargs)
        return execution

    monkeypatch.setattr(rough_sorter_composition, "build_execution_runtime", build_execution_runtime)
    shared_client = object()

    runtime = rough_sorter_composition.build_rough_sorter_runtime(
        session_factory=object(),  # type: ignore[arg-type]
        transport_runtime=SimpleNamespace(
            client=shared_client,
            repository=object(),
            service=object(),
            position_projection_service=object(),
        ),
        device_command_service=object(),  # type: ignore[arg-type]
    )

    adapter = captured["wms_confirmation_adapter"]
    resolver = captured["wms_request_resolver"]
    assert runtime.execution is execution
    assert isinstance(adapter, WmsConfirmationTypedRouter)
    assert adapter._execution_adapter._client is shared_client
    assert adapter._rough_sorter_adapter._client is shared_client
    assert isinstance(resolver, WmsConfirmationRequestTypedRouter)
