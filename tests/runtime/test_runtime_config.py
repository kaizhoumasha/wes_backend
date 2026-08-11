"""通用运行时配置解析合同。"""

from types import SimpleNamespace

from src.app.runtime.normalization.contracts.runtime_config import resolve_execution_context


def test_runtime_config_does_not_project_retired_workline_plugin_identity() -> None:
    workline = SimpleNamespace(
        id=7,
        line_code="LINE-07",
        plugin_key="retired-plugin",
        contract_version="v1",
    )
    device = SimpleNamespace(id=11, device_code="DEVICE-11", work_line_id=7)

    context = resolve_execution_context(workline, {"SCANNER": [device]})

    assert context.workline is not None
    assert {"plugin_key", "contract_version"}.isdisjoint(type(context.workline).model_fields)
    resolved_device = context.devices_by_role["SCANNER"][0]
    assert {"plugin_key", "contract_version"}.isdisjoint(type(resolved_device).model_fields)
