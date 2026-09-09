from wes_plugin_sdk.prepare_policy import PrepareContext, PrepareTaskType

from src.app.workline_integration_debug.composition import ManualIntegrationPreparePolicy


def test_manual_integration_prepare_is_fixed_to_manual_without_plugin_activation() -> None:
    context = PrepareContext(
        is_active=False,
        line_type="AUTO",
        run_mode="AUTO",
        plugin_key=None,
        flow_mode=None,
    )

    assert ManualIntegrationPreparePolicy().select_task_type(context) is PrepareTaskType.MANUAL
