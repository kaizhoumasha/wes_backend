"""共享 WMS 入口逐请求留痕，复用统一日志，不依赖报文身份是否合法。"""

from __future__ import annotations

import base64
import hashlib

from src.app.sys.models.api_access_log import APIAccessLogCreate
from src.app.sys.services.api_access_log_service import api_access_log_service

CALLBACK_RECEIPT_BODY_LIMIT = 64 * 1024


class WmsCallbackReceiptService:
    def __init__(self, *, log_service=api_access_log_service) -> None:
        self._logs = log_service

    def record(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        peer_address: str | None,
        raw_body: bytes,
        observed_body_bytes: int,
        response_status: int,
        response_body: bytes,
        response_time_ms: int,
    ) -> None:
        """冻结逐请求有界快照，响应发送后由宿主尽力写入。"""

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
        self._logs.defer_record(
            APIAccessLogCreate(
                system_id="wms",
                direction="INBOUND",
                method=method,
                path=path,
                peer_address=peer_address,
                request_id=request_id,
                details=receipt,
                status_code=response_status,
                response_time_ms=response_time_ms,
                error_code=f"HTTP_{response_status}" if response_status >= 400 else None,
            )
        )


wms_callback_receipt_service = WmsCallbackReceiptService()

__all__ = ["WmsCallbackReceiptService", "wms_callback_receipt_service"]
