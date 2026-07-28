"""外部 HTTP 派发的 canonical payload 值对象。

领域 gateway 只在创建派发包络时序列化一次；后续持久化、签名、发送和重试
都复用这里冻结的原始 bytes，禁止从查询投影重新构造请求体。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from src.app.wms_integration.operation_registry import EFFECT_OPERATION_IDENTITIES
from src.utils.value_normalization import require_string

if TYPE_CHECKING:
    from src.app.sys.external_http_binding import FrozenExternalHttpBinding

_CANONICAL_WMS_OPERATION_IDENTITY_RE = re.compile(r"^wms\.[a-z0-9_]+\.[a-z0-9_]+@v[1-9][0-9]*$")
_WMS_EFFECT_OPERATION_IDENTITIES = EFFECT_OPERATION_IDENTITIES


def _persisted_bytes(value: object, field_name: str) -> bytes:
    """在持久化恢复边界把未验证值收窄为 bytes。"""

    if not isinstance(value, bytes):
        raise TypeError(f"{field_name} must be bytes")
    return value


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
    def from_persisted(cls, *, canonical_payload_bytes: object, payload_hash: object) -> CanonicalPayload:
        """从数据库恢复原 bytes，只校验完整性，不重新序列化查询投影。"""

        return cls(
            body=_persisted_bytes(canonical_payload_bytes, "canonical_payload_bytes"),
            sha256=require_string(payload_hash, "payload_hash"),
        )

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
    method: Literal["POST"]
    timeout_seconds: float
    credential_reference: str
    auth_scheme: Literal["HMAC_SHA256"]
    timestamp: str
    nonce: str
    operation_identity: str
    _signature: str = field(repr=False)
    idempotency_key: str | None = None

    @classmethod
    def from_persisted(
        cls,
        *,
        binding: FrozenExternalHttpBinding,
        canonical_payload_bytes: object,
        payload_hash: object,
        secret: bytes,
        timestamp: str,
        nonce: str,
        idempotency_key: str | None = None,
    ) -> ExternalHttpDispatchRequest:
        payload = CanonicalPayload.from_persisted(
            canonical_payload_bytes=canonical_payload_bytes,
            payload_hash=payload_hash,
        )
        if not isinstance(secret, bytes) or not secret:
            raise ValueError("resolved credential material must be non-empty bytes")
        timestamp = str(timestamp or "").strip()
        nonce = str(nonce or "").strip()
        if not timestamp or "\n" in timestamp or not nonce or "\n" in nonce:
            raise ValueError("request authentication timestamp and nonce must be non-empty single-line values")
        if binding.operation_identity in _WMS_EFFECT_OPERATION_IDENTITIES and idempotency_key is None:
            raise ValueError("WMS EFFECT request requires a non-empty idempotency_key")
        if idempotency_key is not None:
            if (
                not isinstance(idempotency_key, str)
                or not idempotency_key.strip()
                or "\n" in idempotency_key
                or "\r" in idempotency_key
            ):
                raise ValueError("idempotency_key must be a non-empty single-line value")
            if not _CANONICAL_WMS_OPERATION_IDENTITY_RE.fullmatch(binding.operation_identity):
                raise ValueError("WMS EFFECT request requires a canonical operation identity")
        target = binding.target_snapshot
        path = urlparse(target.url).path or "/"
        signature_fields = [target.http_method, path, timestamp, nonce, payload.sha256]
        if idempotency_key is not None:
            signature_fields.extend((binding.operation_identity, idempotency_key))
        signature_payload = "\n".join(signature_fields).encode()
        signature = hmac.new(secret, signature_payload, hashlib.sha256).hexdigest()
        return cls(
            endpoint=EndpointDefinition(code=target.code, url=target.url),
            payload=payload,
            method=target.http_method,
            timeout_seconds=target.timeout_seconds,
            credential_reference=binding.credential_reference,
            auth_scheme=binding.auth_scheme,
            timestamp=timestamp,
            nonce=nonce,
            operation_identity=binding.operation_identity,
            _signature=signature,
            idempotency_key=idempotency_key,
        )

    @property
    def body(self) -> bytes:
        return self.payload.body

    @property
    def payload_hash(self) -> str:
        return self.payload.sha256

    @property
    def headers(self) -> dict[str, str]:
        """返回封闭的认证 header 集；调用方不能注入自由 header mapping。"""

        headers = {
            "Content-Type": "application/json",
            "X-WES-Content-SHA256": self.payload_hash,
            "X-WES-Credential-Reference": self.credential_reference,
            "X-WES-Nonce": self.nonce,
            "X-WES-Signature": self._signature,
            "X-WES-Signature-Algorithm": self.auth_scheme,
            "X-WES-Timestamp": self.timestamp,
        }
        if self.idempotency_key is not None:
            headers["Idempotency-Key"] = self.idempotency_key
            headers["X-WES-Operation-Identity"] = self.operation_identity
        return headers

    def sign_hmac_sha256(self, secret: bytes) -> str:
        return self.payload.sign_hmac_sha256(secret)


__all__ = [
    "CanonicalPayload",
    "EndpointDefinition",
    "ExternalHttpDispatchRequest",
    "canonical_json_bytes",
    "payload_sha256",
]
