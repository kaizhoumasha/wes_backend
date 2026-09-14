from types import SimpleNamespace

from src.app.runtime.normalization.normalizers.input_normalizer import normalize_inbox_input


def test_device_scan_bin_code_becomes_business_key() -> None:
    inbox = SimpleNamespace(
        kind="DEVICE_EVENT",
        event_type="SCAN_COMPLETED",
        payload_json={
            "device_code": "STATION_SCAN9",
            "event_type": "SCAN_COMPLETED",
            "data": {"bin_code": "A000000394-B"},
        },
    )

    normalized = normalize_inbox_input(inbox)

    assert normalized.business_key == "A000000394-B"
