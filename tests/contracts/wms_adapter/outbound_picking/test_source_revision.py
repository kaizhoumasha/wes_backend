"""三条来源请求必须保留 RACK_SLOT 成员版本，BIN_CELL 不携带版本。"""

from importlib import import_module

import pytest
import wes_plugin_sdk as sdk
from wes_plugin_sdk import wms_operations

from src.utils.canonical_json import canonical_json_digest

CASES = (
    ("material_decide", "parse_material_decide_request", "outbound_material_decide"),
    ("source_empty", "parse_source_empty_request", "outbound_source_empty_decide"),
    ("movement_report", "parse_material_movement_report_request", "outbound_material_movement_report"),
)
OPERATION_ID = "019f3405-2200-7b01-8b01-000000000001"


def request_data(domain, source="RACK_SLOT"):
    locator = {"type": source, "rack_id": "SOURCE-1", "rack_face": "A"}
    locator.update({"slot_id": "SLOT-1"} if source == "RACK_SLOT" else {"bin_code": "BIN-1", "cell_id": "CELL-1"})
    data = {"task_id": "TASK-1", "source_locator": locator}
    if domain == "material_decide":
        data.update(
            barcode="原始,条码",
            scanned_at=123,
        )
    elif domain == "source_empty":
        data["observed_at"] = 123
    else:
        data.update(
            PkgID="P",
            to_locator={"type": "RACK_SLOT", "rack_id": "TARGET-1", "rack_face": "B", "slot_id": "SLOT-2"},
            occurred_at=123,
        )
    return data


def envelope(domain, data):
    operation = {
        "material_decide": "outbound.material.decide@v1",
        "source_empty": "outbound.source.empty_decide@v1",
        "movement_report": "outbound.material.movement_report@v1",
    }[domain]
    return {"operation_id": OPERATION_ID, "operation": operation, "timestamp": 0, "data": data}


@pytest.mark.parametrize("domain,parser_name,_factory", CASES)
@pytest.mark.parametrize("revision", [None, True, 0, -1, 2**63, "2", 2.0])
def test_rack_slot_rejects_invalid_member_revision(domain, parser_name, _factory, revision):
    parser = getattr(import_module(f"src.app.wms_adapter.outbound_picking.{domain}_wire"), parser_name)
    with pytest.raises(ValueError):
        parser(envelope(domain, request_data(domain) | {"plan_revision": revision}))


@pytest.mark.parametrize("domain,parser_name,_factory", CASES)
def test_rack_slot_rejects_missing_member_revision(domain, parser_name, _factory):
    parser = getattr(import_module(f"src.app.wms_adapter.outbound_picking.{domain}_wire"), parser_name)
    with pytest.raises(ValueError):
        parser(envelope(domain, request_data(domain)))


@pytest.mark.parametrize("domain,parser_name,_factory", CASES)
@pytest.mark.parametrize("revision", [None, 2])
def test_bin_cell_rejects_revision_even_when_null(domain, parser_name, _factory, revision):
    parser = getattr(import_module(f"src.app.wms_adapter.outbound_picking.{domain}_wire"), parser_name)
    with pytest.raises(ValueError):
        parser(envelope(domain, request_data(domain, "BIN_CELL") | {"plan_revision": revision}))


@pytest.mark.parametrize("domain,_parser,factory_name", CASES)
@pytest.mark.parametrize("revision", [1, 2**63 - 1])
def test_typed_request_preserves_member_revision_in_frozen_payload(domain, _parser, factory_name, revision):
    data = request_data(domain) | {"plan_revision": revision}
    values = {
        "operation_id": OPERATION_ID,
        "task_id": "TASK-1",
        "plan_revision": revision,
        "source_locator": sdk.PickingRackSlot("SOURCE-1", "A", "SLOT-1"),
    }
    if domain == "material_decide":
        values.update(barcode="原始,条码", scanned_at=123)
    elif domain == "source_empty":
        values["observed_at"] = 123
    else:
        values.update(pkg_id="P", to_locator=sdk.PickingRackSlot("TARGET-1", "B", "SLOT-2"), occurred_at=123)
    encode = import_module(f"src.app.wms_adapter.outbound_picking.{domain}_typed").encode_request
    intent = getattr(wms_operations, factory_name)(**values)
    payload = encode(intent, timestamp=0)
    assert payload == envelope(domain, data)
    other = envelope(domain, data | {"plan_revision": 2 if revision == 1 else 1})
    assert canonical_json_digest(payload) != canonical_json_digest(other)
