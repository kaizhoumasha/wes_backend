"""CTU 下一批只作业务选择，不推算背篓或滚筒容量。"""

from importlib import import_module

import wes_plugin_sdk as sdk


def test_inbound_precedes_return_fifo() -> None:
    policy = import_module("manual_picking.application.batch_policy")
    intent = policy.choose_next_batch(
        operation_id="019f0000-0000-7000-8000-000000000001",
        workline_code="LINE-1",
        task_id="PICK-1",
        rack_id="R1",
        rack_face="90",
        return_bins=("A000000001", "A000000002", "A000000003", "A000000004", "A000000005"),
        return_location="CNV0302",
        return_retry_due=True,
        allow_inbound=True,
    )

    assert type(intent) is sdk.BinInboundBatchIntent

    intent = policy.choose_next_batch(
        operation_id="019f0000-0000-7000-8000-000000000001",
        workline_code="LINE-1",
        task_id="PICK-1",
        rack_id="R1",
        rack_face="90",
        return_bins=("A000000001", "A000000002", "A000000003", "A000000004", "A000000005"),
        return_location="CNV0302",
        return_retry_due=True,
        allow_inbound=False,
    )
    assert type(intent) is sdk.BinReturnBatchIntent
    assert len(intent.return_candidates) == 4


def test_inbound_requests_face_only_when_return_cannot_run() -> None:
    policy = import_module("manual_picking.application.batch_policy")
    fields = {
        "operation_id": "019f0000-0000-7000-8000-000000000002",
        "workline_code": "LINE-1",
        "task_id": "PICK-1",
        "rack_id": "R1",
        "rack_face": "90",
        "return_bins": ("A000000001",),
        "return_location": "CNV0302",
        "allow_inbound": True,
    }

    intent = policy.choose_next_batch(**fields, return_retry_due=False)

    assert type(intent) is sdk.BinInboundBatchIntent
    assert (intent.task_id, intent.rack_id, intent.rack_face) == ("PICK-1", "R1", "90")
    assert policy.choose_next_batch(**(fields | {"allow_inbound": False}), return_retry_due=False) is None
