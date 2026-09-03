"""Transport 调试 API 的 Swagger 运行时失败合同回归。"""


def test_transport_debug_openapi_declares_runtime_failure_statuses() -> None:
    # Regression: ISSUE-001 — Swagger 未声明 Transport 调试接口的运行时失败状态
    # Found by /qa on 2026-08-21
    # Report: .gstack/qa-reports/qa-report-localhost-8001-2026-08-21.md
    from main import app

    paths = app.openapi()["paths"]
    create_responses = paths["/api/v1/transport/debug-tasks"]["post"]["responses"]
    read_responses = paths["/api/v1/transport/tasks/{transport_task_id}"]["get"]["responses"]

    assert {"202", "400", "409", "422", "503"} <= create_responses.keys()
    assert {"200", "404", "422", "503"} <= read_responses.keys()


def test_transport_debug_run_openapi_declares_full_diagnostics_contract() -> None:
    from main import app

    paths = app.openapi()["paths"]

    assert {"202", "400", "409", "422", "503"} <= paths["/api/v1/transport/debug-runs"]["post"]["responses"].keys()
    assert {"200", "400", "422", "503"} <= paths["/api/v1/transport/debug-runs"]["get"]["responses"].keys()
    assert {"200", "404", "422", "503"} <= paths["/api/v1/transport/debug-runs/{run_id}"]["get"]["responses"].keys()
    assert {"200", "400", "404", "409", "422", "503"} <= paths["/api/v1/transport/debug-runs/{run_id}/abort"]["post"][
        "responses"
    ].keys()
    assert paths["/api/v1/transport/debug-runs/stream"]["get"]["responses"]["200"]["content"] == {
        "text/event-stream": {"schema": {"type": "string"}}
    }
