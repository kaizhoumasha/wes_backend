from src.app.wms_integration.services.service_locator import wms_typed_port_service


def test_wms_typed_port_singleton_uses_async_context_manager_session_factory() -> None:
    session_context = wms_typed_port_service.session_factory()

    assert hasattr(session_context, "__aenter__")
    assert hasattr(session_context, "__aexit__")
