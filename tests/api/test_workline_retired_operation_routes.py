"""当前无业务 owner 的 WorkLine 操作路由缺席合同。"""


def test_openapi_excludes_operations_without_runtime_owner() -> None:
    """公开 API 不得接受随后必然进入 DEAD_LETTER 的操作。"""

    from main import app

    paths = app.openapi()["paths"]

    assert {
        "/api/v1/workline/operations/manual/sessions/{session_id}",
        "/api/v1/workline/operations/results",
        "/api/v1/workline/operations/sandbox/events",
        "/api/v1/workline/operations/sandbox/templates",
    }.isdisjoint(paths)
