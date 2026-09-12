"""人工计划纠正 HTTP 合同已退役。"""

from pathlib import Path

from fastapi import FastAPI

from src.register import register_routers


def test_apply_correction_route_and_openapi_contract_are_retired() -> None:
    app = FastAPI()
    register_routers(app)

    retired_path = "/api/v1/outbound-picking/tasks/{task_id}/plan-blockers/{blocking_evidence_id}/apply-correction"
    assert retired_path not in {getattr(route, "path", None) for route in app.routes}
    assert retired_path not in app.openapi()["paths"]
    assert not (Path(__file__).parents[2] / "src/app/wms_integration/outbound_picking/v1").exists()
