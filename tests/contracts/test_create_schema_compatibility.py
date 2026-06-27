from src.app.device.models.device import DeviceCreate
from src.app.workline.models.workline import LineType, WorkLineCreate


def test_device_create_keeps_default_factory_fields_optional() -> None:
    payload = {
        "device_code": "DEV-CREATE-001",
        "device_name": "Create Device",
        "device_role": "INPUT_ARM",
    }

    device = DeviceCreate.model_validate(payload)

    assert not DeviceCreate.model_fields["capabilities_json"].is_required()
    assert not DeviceCreate.model_fields["diagnostic_profile"].is_required()
    assert device.capabilities_json == {}
    assert device.diagnostic_profile == {}


def test_workline_create_keeps_default_factory_fields_optional() -> None:
    payload = {
        "line_code": "WL-CREATE-001",
        "line_name": "Create WorkLine",
        "line_type": LineType.AUTO,
    }

    workline = WorkLineCreate.model_validate(payload)

    assert not WorkLineCreate.model_fields["config"].is_required()
    assert not WorkLineCreate.model_fields["runtime_config_json"].is_required()
    assert not WorkLineCreate.model_fields["diagnostic_profile"].is_required()
    assert workline.config == {}
    assert workline.runtime_config_json == {}
    assert workline.diagnostic_profile == {}
