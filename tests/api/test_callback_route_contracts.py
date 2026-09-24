"""Active callback route contract tests."""


def test_active_callback_routes_are_published() -> None:
    from main import app

    paths = app.openapi()["paths"]

    assert "/api/v1/callback/result" in paths
    assert "/api/v1/callback/event" in paths
    assert "/api/v1/wms/events" in paths
