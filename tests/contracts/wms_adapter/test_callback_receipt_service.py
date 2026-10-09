"""请求级快照保存原始字节；同步登记不访问诊断数据库。"""

import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.app.wms_adapter.callback_receipt_service import CALLBACK_RECEIPT_BODY_LIMIT, WmsCallbackReceiptService


@pytest.mark.parametrize(
    "raw",
    [b"\xff\x00", b'{"x":1,"x":2}', b"x" * (CALLBACK_RECEIPT_BODY_LIMIT + 1)],
    ids=["invalid-bytes", "duplicate-keys", "bounded-body"],
)
def test_receipts_preserve_each_failed_attempt_without_overwriting(raw):
    logs = SimpleNamespace(defer_record=Mock())
    service = WmsCallbackReceiptService(log_service=logs)
    for status in (409, 202):
        service.record(
            request_id="request-1",
            method="POST",
            path="/custom/wms/events",
            peer_address=None,
            raw_body=raw,
            observed_body_bytes=len(raw),
            response_status=status,
            response_body=json.dumps({"status": status}).encode(),
            response_time_ms=7,
        )
    entries = [call.args[0] for call in logs.defer_record.call_args_list]
    assert [entry.status_code for entry in entries] == [409, 202]
    assert entries[0].error_code == "HTTP_409"
    assert entries[1].error_code is None
    for entry in entries:
        assert (entry.system_id, entry.direction, entry.method, entry.path) == (
            "wms",
            "INBOUND",
            "POST",
            "/custom/wms/events",
        )
        receipt = entry.details
        assert base64.b64decode(receipt["raw_body_base64"]) == raw[:CALLBACK_RECEIPT_BODY_LIMIT]
        assert receipt["body_truncated"] == (len(raw) > CALLBACK_RECEIPT_BODY_LIMIT)
        assert receipt["observed_body_bytes"] == len(raw)
        assert receipt["body_sha256"] is not None
        assert json.loads(receipt["response_body"]) == {"status": entry.status_code}
