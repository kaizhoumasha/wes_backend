"""Active callback route contract tests."""


def test_active_callback_contract_excludes_retired_external_ingress() -> None:
    from main import app

    paths = app.openapi()["paths"]

    assert "/api/v1/callback/external" not in paths
    assert "/api/v1/callback/result" in paths
    assert "/api/v1/callback/event" in paths
    assert "/api/v1/wms/events" in paths
