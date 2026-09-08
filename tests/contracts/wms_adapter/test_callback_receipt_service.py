"""请求级收据保存原始字节，独立于协议身份和业务 ACK。"""

import base64
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.app.callback.models import CallbackLog
from src.app.wms_adapter.callback_receipt_service import CALLBACK_RECEIPT_BODY_LIMIT, WmsCallbackReceiptService
from src.core.uuid7 import new_uuid7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [b"\xff\x00", b'{"x":1,"x":2}', b"x" * (CALLBACK_RECEIPT_BODY_LIMIT + 1)],
    ids=["invalid-bytes", "duplicate-keys", "bounded-body"],
)
async def test_receipts_preserve_each_failed_attempt_without_overwriting(db_engine, raw: bytes) -> None:
    sessions = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    service = WmsCallbackReceiptService(sessions)
    request_id = new_uuid7()
    for status in (409, 202):
        await service.record(
            request_id=request_id,
            raw_body=raw,
            observed_body_bytes=len(raw),
            response_status=status,
            response_body=json.dumps({"status": status}).encode(),
            response_time_ms=7,
        )
    async with sessions() as db:
        rows = list(
            await db.scalars(select(CallbackLog).where(CallbackLog.request_id == request_id).order_by(CallbackLog.id))
        )
    assert [row.response_status for row in rows] == [409, 202]
    assert rows[0].error_message is not None
    for row in rows:
        receipt = row.request_body
        assert base64.b64decode(receipt["raw_body_base64"]) == raw[:CALLBACK_RECEIPT_BODY_LIMIT]
        assert receipt["body_truncated"] == (len(raw) > CALLBACK_RECEIPT_BODY_LIMIT)
        assert receipt["observed_body_bytes"] == len(raw)
        assert receipt["body_sha256"] is not None
        assert json.loads(receipt["response_body"]) == {"status": row.response_status}
