"""共享 WMS 入口逐请求留痕，复用 CallbackLog，不依赖报文身份是否合法。"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import TYPE_CHECKING

from src.app.callback.services import callback_log_service
from src.database import db as database

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

CALLBACK_RECEIPT_BODY_LIMIT = 64 * 1024


class WmsCallbackReceiptService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._sessions = sessions

    async def record(
        self,
        *,
        request_id: str,
        raw_body: bytes,
        observed_body_bytes: int,
        response_status: int,
        response_body: bytes,
        response_time_ms: int,
    ) -> None:
        """在返回 ACK 前提交独立收据；不把请求重试覆盖成首条收据。"""

        sessions = self._sessions or database.AsyncSessionLocal
        if sessions is None:
            raise RuntimeError("callback receipt database is unavailable")
        captured = raw_body[:CALLBACK_RECEIPT_BODY_LIMIT]
        truncated = observed_body_bytes > len(captured)
        # 原始 bytes 可包含重复键、非法 UTF-8 或 NUL，不能先 JSON 解析再作为原文保存。
        receipt = {
            "raw_body_base64": base64.b64encode(captured).decode("ascii"),
            "body_truncated": truncated,
            "observed_body_bytes": observed_body_bytes,
            "captured_body_bytes": len(captured),
            "body_sha256": hashlib.sha256(raw_body).hexdigest() if len(raw_body) == observed_body_bytes else None,
            "response_body": response_body.decode("utf-8", errors="replace"),
        }
        async with sessions() as db:
            _ = await callback_log_service.log_callback(
                db,
                callback_type="wms_event",
                subject_code="WMS",
                request_body=receipt,
                request_id=request_id,
                response_status=response_status,
                response_time_ms=response_time_ms,
                ingress_outcome="ACCEPTED"
                if response_status < 400
                else "FAILED"
                if response_status >= 500
                else "REJECTED",
                failure_stage="WMS_EVENT_INGRESS" if response_status >= 400 else None,
                error_message=json.dumps({"http_status": response_status, "response": receipt["response_body"]})
                if response_status >= 400
                else None,
            )


wms_callback_receipt_service = WmsCallbackReceiptService()

__all__ = ["WmsCallbackReceiptService", "wms_callback_receipt_service"]
