from types import SimpleNamespace

from src.celery_app.tasks.workline import _build_device_command_log_envelope


def test_device_command_log_envelope_keeps_business_params_and_redacts_secrets() -> None:
    outbox = SimpleNamespace(
        id=501,
        session_id=93,
        dispatch_key="dispatch-measure-001",
        target_type="DEVICE",
        target_code="ARM03",
    )
    payload = {
        "command_code": "CMD_MEASUREMENT_REEL_001",
        "task_type": "MEASUREMENT_REEL",
        "params": {
            "PkgID": "SVYU00125TP4LCR02_2",
            "station": "ARM03",
            "api_token": "supplier-secret",
        },
    }

    envelope = _build_device_command_log_envelope(outbox, payload, endpoint="http://arm03/api/v1/device/command")

    assert envelope["outbox_id"] == 501
    assert envelope["dispatch_key"] == "dispatch-measure-001"
    assert envelope["target_code"] == "ARM03"
    assert envelope["endpoint"] == "http://arm03/api/v1/device/command"
    assert envelope["payload"]["command_code"] == "CMD_MEASUREMENT_REEL_001"
    assert envelope["payload"]["params"]["PkgID"] == "SVYU00125TP4LCR02_2"
    assert envelope["payload"]["params"]["station"] == "ARM03"
    assert envelope["payload"]["params"]["api_token"] == "***REDACTED***"
