from fastapi.routing import APIRoute

from src.register import app


def test_auth_my_route_registered() -> None:
    paths = {route.path for route in app.routes}
    assert "/api/v1/auth/my" in paths


def test_menu_my_route_registered() -> None:
    paths = {route.path for route in app.routes}
    assert "/api/v1/menus/my" in paths


def test_menu_my_route_response_wrapped() -> None:
    target = next(
        (
            route
            for route in app.routes
            if isinstance(route, APIRoute) and route.path == "/api/v1/menus/my" and "GET" in route.methods
        ),
        None,
    )
    assert target is not None
    assert target.response_model is not None
    assert "ResponseSchemaModel" in str(target.response_model)
