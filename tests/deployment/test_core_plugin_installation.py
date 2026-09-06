"""空装配与显式插件选择的基础合同，不导入具体业务插件。"""

from types import SimpleNamespace

import pytest

from deployment.plugin_composition import build_deployment_runtime


def test_empty_composition_builds_core_without_a_business_handler() -> None:
    runtime = build_deployment_runtime(
        enabled_plugin_keys=(),
        session_factory=object(),
        transport_runtime=SimpleNamespace(service=object(), position_projection_service=object(), client=object()),
        device_command_service=object(),
    )
    assert runtime.plugins == ()
    assert runtime.wms_recovery_event_handler is None
    assert runtime.execution.fact_processor is not None


@pytest.mark.parametrize("keys", [("unknown-plugin",), ("rough_sorter", "rough_sorter")])
def test_invalid_plugin_selection_fails_before_constructing_resources(keys: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        build_deployment_runtime(
            enabled_plugin_keys=keys,
            session_factory=object(),
            transport_runtime=object(),
            device_command_service=object(),
        )
