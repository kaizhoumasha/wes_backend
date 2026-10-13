from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.app.wms_adapter.return_rack_task_completion.wire import ReturnRackTaskCompletedEvent
from src.app.wms_integration.return_rack_task_completion.reader import ReturnRackTaskCompletedReader

PAYLOAD = {
    "operation": "outbound.return_rack.task_completed@v1",
    "operation_id": "019f3404-a100-7b01-8b01-000000000099",
    "timestamp": 1788390200000,
    "data": {"task_id": "PICK-1", "rack_id": "R1", "through_plan_revision": 2, "completed_at": 1788390199000},
}


def test_strict_return_rack_task_completion_contract():
    assert ReturnRackTaskCompletedEvent.model_validate(PAYLOAD).data.through_plan_revision == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("through_plan_revision", True),
        ("through_plan_revision", 0),
        ("completed_at", 0),
        ("completed_at", 1788390200001),
        ("rack_id", ""),
        ("task_id", "bad id"),
        ("extra", 1),
    ],
)
def test_invalid_data_is_rejected(field, value):
    payload = deepcopy(PAYLOAD)
    payload["data"][field] = value
    with pytest.raises(ValueError):
        ReturnRackTaskCompletedEvent.model_validate(payload)


def test_extra_envelope_is_rejected():
    with pytest.raises(ValueError):
        ReturnRackTaskCompletedEvent.model_validate({**PAYLOAD, "extra": 1})


@pytest.mark.parametrize("operation_id,task_id", [("other", 31), (PAYLOAD["operation_id"], None)])
def test_typed_reader_rejects_unbound_or_drifted_identity(operation_id, task_id):
    evidence = SimpleNamespace(
        operation=PAYLOAD["operation"],
        operation_id=operation_id,
        picking_task_id=task_id,
        normalized_payload=PAYLOAD,
    )
    with pytest.raises(ValueError, match="Evidence 身份未绑定"):
        ReturnRackTaskCompletedReader().read(evidence)


def test_typed_reader_ignores_other_operation():
    assert ReturnRackTaskCompletedReader().read(SimpleNamespace(operation="other")) is None
