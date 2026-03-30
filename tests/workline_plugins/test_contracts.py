"""Plugin contract adapter tests."""

from src.workline_plugins.contracts import (
    STEP_CODE_FIELD,
    normalize_callback_event_payload,
    normalize_callback_result_payload,
    resolve_contract_version,
)


class TestWorklinePluginContracts:
    def test_normalize_callback_event_payload_uses_registered_plugin_contract(self) -> None:
        payload = {
            "device_id": "ARM_01",
            "event_type": "SCAN_COMPLETED",
            "timestamp": 1702627300000,
            "data": {
                "location": "STATION_INPUT1",
                "barcode1": "PKG1_12345678",
            },
        }

        normalized_payload, contract_version, inferred_step_code = normalize_callback_event_payload(
            plugin_key="smt_classifier",
            payload=payload,
        )

        assert normalized_payload["device_code"] == "ARM_01"
        assert normalized_payload["data"]["location_id"] == "STATION_INPUT1"
        assert normalized_payload["data"]["barcode"] == "PKG1_12345678"
        assert contract_version == "1.0"
        assert inferred_step_code == "WAITING_SCAN_EVENT"

    def test_normalize_callback_result_payload_uses_registered_plugin_contract(self) -> None:
        payload = {
            "command_id": "CMD-20250317-001",
            "device_id": "ARM_01",
            "result": "SUCCESS",
            "finish_time": 1702627250000,
            "data": {"task_type": "PICK_AND_PUT"},
        }

        normalized_payload, contract_version = normalize_callback_result_payload(
            plugin_key="smt_classifier",
            payload=payload,
        )

        assert normalized_payload["command_code"] == "CMD-20250317-001"
        assert normalized_payload["device_code"] == "ARM_01"
        assert normalized_payload["data"]["command_type"] == "PICK_AND_PUT"
        assert contract_version == "1.0"

    def test_normalize_callback_event_payload_falls_back_to_default_contract(self) -> None:
        payload = {
            "device_code": "PLC_01",
            "event_type": "DEVICE_ONLINE",
            "timestamp": 1702627300000,
            "data": {"status": "READY"},
        }

        normalized_payload, contract_version, inferred_step_code = normalize_callback_event_payload(
            plugin_key="unknown_plugin",
            payload=payload,
        )

        assert normalized_payload == payload
        assert contract_version is None
        assert inferred_step_code is None

    def test_resolve_contract_version_uses_registered_contract_module(self) -> None:
        assert resolve_contract_version("smt_classifier") == "1.0"
        assert resolve_contract_version("unknown_plugin") is None
        assert STEP_CODE_FIELD == "step_code"
