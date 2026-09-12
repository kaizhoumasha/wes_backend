from src.app.workline.v1 import operation as operation_api


def test_sandbox_process_route_is_removed() -> None:
    route_paths = {getattr(route, "path", None) for route in operation_api.router.routes}

    assert "/sandbox/process" not in route_paths


def test_target_safety_api_excludes_generic_runtime_control_routes() -> None:
    """WorkLine operation 不暴露已退役的恢复或 generic reconciliation 路由。"""

    route_paths = {getattr(route, "path", None) for route in operation_api.router.routes}

    assert route_paths.isdisjoint(
        {
            "/reconciliations/effects/{dispatch_key}/resolve",
            "/reconciliations/sessions/{session_id}/resolve",
            "/replay/inboxes/{inbox_id}",
            "/sandbox/ack",
            "/sandbox/completed",
            "/sandbox/external-callbacks",
            "/sandbox/pending",
            "/sandbox/worklines/{workline_id}/simulate-estop",
            "/safety/worklines/{workline_id}/clear-estop",
        }
    )


def test_clear_estop_contract_is_fully_retired() -> None:
    route_paths = {getattr(route, "path", None) for route in operation_api.router.routes}

    assert "/safety/worklines/{workline_id}/clear-estop" not in route_paths
    assert not hasattr(operation_api, "ClearWorkLineEstopRequest")
    assert not hasattr(operation_api, "clear_workline_estop")
    assert not hasattr(operation_api, "workline_safety_service")
