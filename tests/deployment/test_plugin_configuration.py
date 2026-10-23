"""部署期插件输入的解析与优先级；不安装插件或创建运行时。"""

import pytest
from pydantic import ValidationError


def test_first_plan_timeout_has_no_business_default(monkeypatch):
    from deployment.plugin_configuration import DeploymentPluginSettings

    monkeypatch.delenv("AUTOMATIC_PICKING_FIRST_PLAN_TIMEOUT_MS", raising=False)
    assert DeploymentPluginSettings(_env_file=None).AUTOMATIC_PICKING_FIRST_PLAN_TIMEOUT_MS is None


def test_process_value_overrides_dotenv_without_changing_either(monkeypatch, tmp_path):
    from deployment.plugin_configuration import DeploymentPluginSettings

    config = tmp_path / ".env"
    config.write_text("AUTOMATIC_PICKING_FIRST_PLAN_TIMEOUT_MS=1200\nUNRELATED_SETTING=keep\n")
    monkeypatch.delenv("AUTOMATIC_PICKING_FIRST_PLAN_TIMEOUT_MS", raising=False)
    assert DeploymentPluginSettings(_env_file=config).AUTOMATIC_PICKING_FIRST_PLAN_TIMEOUT_MS == 1200
    monkeypatch.setenv("AUTOMATIC_PICKING_FIRST_PLAN_TIMEOUT_MS", "2300")
    assert DeploymentPluginSettings(_env_file=config).AUTOMATIC_PICKING_FIRST_PLAN_TIMEOUT_MS == 2300


@pytest.mark.parametrize("value", ["", "0", "-1", "1.0", "1e3", "true", " 1000 ", "１０００"])
def test_first_plan_timeout_rejects_invalid_process_values(monkeypatch, value):
    from deployment.plugin_configuration import DeploymentPluginSettings

    monkeypatch.setenv("AUTOMATIC_PICKING_FIRST_PLAN_TIMEOUT_MS", value)
    with pytest.raises(ValidationError, match="AUTOMATIC_PICKING_FIRST_PLAN_TIMEOUT_MS"):
        DeploymentPluginSettings(_env_file=None)


@pytest.mark.parametrize("value", [True, 1.0])
def test_programmatic_boolean_or_float_is_not_integer_milliseconds(value):
    from deployment.plugin_configuration import DeploymentPluginSettings

    with pytest.raises(ValidationError, match="AUTOMATIC_PICKING_FIRST_PLAN_TIMEOUT_MS"):
        DeploymentPluginSettings(_env_file=None, AUTOMATIC_PICKING_FIRST_PLAN_TIMEOUT_MS=value)
