from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

JsonDict = dict[str, Any]
DeviceStatusLiteral = Literal["IDLE", "RUNNING", "ERROR", "OFFLINE"]

# 默认使用 localhost:8001（本地开发）
# Docker 环境通过环境变量覆盖为 host.docker.internal:xxxx
API_APP_ID = os.getenv("API_APP_ID", "app_Gqnvr3dpjGwlrjtO")
API_APP_SECRET = os.getenv("API_APP_SECRET", "sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao")
WES_EVENT_CALLBACK_URL = os.getenv("WES_EVENT_CALLBACK_URL", "http://localhost:8001/api/v1/callback/event")
WES_RESULT_CALLBACK_URL = os.getenv("WES_RESULT_CALLBACK_URL", "http://localhost:8001/api/v1/callback/result")
WES_EXTERNAL_CALLBACK_URL = os.getenv("WES_EXTERNAL_CALLBACK_URL", "http://localhost:8001/api/v1/callback/external")


def current_millis() -> int:
    return int(time.time() * 1000)


def calculate_signature(app_secret: str, app_id: str, timestamp: str, method: str, path: str) -> str:
    sign_string = f"{app_id}{timestamp}{method}{path}"
    return hmac.new(app_secret.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha256).hexdigest()


def build_api_auth_headers(method: str, path: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = calculate_signature(API_APP_SECRET, API_APP_ID, timestamp, method.upper(), path)
    return {
        "X-App-ID": API_APP_ID,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    }


async def post_signed_json(url: str, payload: JsonDict, timeout_seconds: float = 10.0) -> JsonDict:
    parsed_url = urlparse(url)
    headers = build_api_auth_headers("POST", parsed_url.path)
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {"raw": data}


class DeviceCommandAck(BaseModel):
    code: int
    message: str
    trace_id: str | None = None


class DeviceCommandPayload(BaseModel):
    command_code: str
    task_type: str
    priority: int = Field(default=1, ge=1, le=10)
    timeout: int = Field(default=30000, ge=1)
    params: dict[str, Any] = Field(default_factory=dict)
    timestamp: int


class CancelRequest(BaseModel):
    command_code: str


class DeviceStatusResponse(BaseModel):
    device_code: str
    status: DeviceStatusLiteral
    current_command_code: str | None = None
    error_code: str = "NONE"
    timestamp: int


class DeviceLocation(BaseModel):
    location_id: str
    location_type: str
    rack_id: str | None = None
    bin_id: str | None = None
    bin_type: str | None = None
    bin_cell_location: str | None = None
    reel_layer: str | None = None
    reel_thickness: str | None = None
    reel_diameter: str | None = None
    reel_totalthickness: str | None = None

    def to_payload(self) -> JsonDict:
        payload = self.model_dump()
        return {key: value for key, value in payload.items() if value is not None}
