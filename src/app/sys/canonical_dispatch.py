"""外部 HTTP 派发的 canonical payload 值对象。

领域 gateway 只在创建派发包络时序列化一次；后续持久化、签名、发送和重试
都复用这里冻结的原始 bytes，禁止从查询投影重新构造请求体。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EndpointDefinition:
    """已注册外部 HTTP endpoint 的 typed 快照。"""

    code: str
    url: str


def canonical_json_bytes(projection: Mapping[str, Any]) -> bytes:
    """把 JSON object 投影序列化为唯一、无空白的 UTF-8 bytes。"""

    if not isinstance(projection, Mapping):
        raise TypeError("canonical payload projection must be a JSON object")
    try:
        return json.dumps(
            dict(projection),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("canonical payload projection must contain only valid JSON values") from exc


def payload_sha256(canonical_payload_bytes: bytes) -> str:
    """直接对冻结请求体计算 SHA-256。"""

    return hashlib.sha256(canonical_payload_bytes).hexdigest()


@dataclass(frozen=True)
class CanonicalPayload:
    """已冻结的 canonical HTTP payload。"""

    body: bytes
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.body, bytes) or not self.body:
            raise ValueError("canonical_payload_bytes must be non-empty bytes")
        if payload_sha256(self.body) != self.sha256:
            raise ValueError("payload_hash does not match canonical_payload_bytes")

    @classmethod
    def from_projection(cls, projection: Mapping[str, Any]) -> CanonicalPayload:
        body = canonical_json_bytes(projection)
        return cls(body=body, sha256=payload_sha256(body))

    @classmethod
    def from_persisted(cls, *, canonical_payload_bytes: bytes, payload_hash: str) -> CanonicalPayload:
        """从数据库恢复原 bytes，只校验完整性，不重新序列化查询投影。"""

        return cls(body=canonical_payload_bytes, sha256=payload_hash)

    def validate_projection(self, projection: Mapping[str, Any]) -> None:
        """仅在写入边界验证查询投影与冻结 bytes 一致。"""

        if canonical_json_bytes(projection) != self.body:
            raise ValueError("canonical payload projection differs from canonical_payload_bytes")

    def parse_projection(self) -> dict[str, Any]:
        """Replay 按冻结 bytes 解析并验证 canonical JSON object。"""

        try:
            projection = json.loads(self.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("canonical_payload_bytes must contain valid UTF-8 JSON") from exc
        if not isinstance(projection, dict):
            raise TypeError("canonical_payload_bytes must contain a JSON object")
        if canonical_json_bytes(projection) != self.body:
            raise ValueError("canonical_payload_bytes are not in canonical JSON form")
        return projection

    def sign_hmac_sha256(self, secret: bytes) -> str:
        """直接使用同一冻结 bytes 生成 HMAC；不读取查询投影。"""

        if not isinstance(secret, bytes) or not secret:
            raise ValueError("HMAC secret must be non-empty bytes")
        return hmac.new(secret, self.body, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class ExternalHttpDispatchRequest:
    """Sender 唯一允许接收的 typed HTTP 派发请求。"""

    endpoint: EndpointDefinition
    payload: CanonicalPayload

    @classmethod
    def from_persisted(
        cls,
        *,
        endpoint: EndpointDefinition,
        canonical_payload_bytes: bytes,
        payload_hash: str,
    ) -> ExternalHttpDispatchRequest:
        return cls(
            endpoint=endpoint,
            payload=CanonicalPayload.from_persisted(
                canonical_payload_bytes=canonical_payload_bytes,
                payload_hash=payload_hash,
            ),
        )

    @property
    def body(self) -> bytes:
        return self.payload.body

    @property
    def payload_hash(self) -> str:
        return self.payload.sha256

    def sign_hmac_sha256(self, secret: bytes) -> str:
        return self.payload.sign_hmac_sha256(secret)


__all__ = [
    "CanonicalPayload",
    "EndpointDefinition",
    "ExternalHttpDispatchRequest",
    "canonical_json_bytes",
    "payload_sha256",
]
