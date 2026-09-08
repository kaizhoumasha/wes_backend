"""有界诊断处理与只读查询；故障不得影响原业务调用。"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import aclosing
from typing import TYPE_CHECKING, Any, Literal

from pydantic import TypeAdapter, ValidationError

from src.app.wms_diagnostics.comparison import compare_fields
from src.app.wms_diagnostics.contracts import (
    ExchangeDetail,
    ExchangeFilters,
    ExchangeObservation,
    ExchangePage,
    ExchangeQuery,
    ExchangeSummary,
    WirePreview,
)
from src.app.wms_diagnostics.observation import WmsCallObservation
from src.app.wms_diagnostics.redaction import preview_json, protocol_headers, safe_value

if TYPE_CHECKING:
    from src.app.wms_diagnostics.config import DiagnosticsConfig
    from src.app.wms_diagnostics.repository import DiagnosticsRepository

WMS_DIAGNOSTICS_CHANNEL = "wms:diagnostics:stream"


def _summary(observation: WmsCallObservation) -> dict[str, Any]:
    result = {name: getattr(observation, name) for name in ExchangeSummary.model_fields if hasattr(observation, name)}
    # 外部身份只保留有界文本；不把任意对象或异常 repr 带入降级摘要。
    for name, value in result.items():
        if isinstance(value, str):
            limit = 256 if name == "path" else 128
            result[name] = value[:limit].encode("utf-8", errors="replace")[:limit].decode("utf-8", errors="ignore")
    return result


class WmsDiagnosticsService:
    def __init__(
        self,
        repository: DiagnosticsRepository,
        publisher: Any,
        config: DiagnosticsConfig,
        *,
        build_version: str = "unknown",
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._config = config
        self._build_version = build_version[:64]

    async def start(
        self,
        *,
        direction: Literal["WMS_TO_WES", "WES_TO_WMS"],
        operation: str | None = None,
        operation_id: str | None = None,
        payload: object = None,
        body: bytes | None = None,
    ) -> WmsCallObservation | None:
        observation = None
        try:
            async with asyncio.timeout(self._config.budget_ms * 0.1 / 1000):
                deadline = time.monotonic() + self._config.budget_ms * 0.1 / 1000
                observation = WmsCallObservation(direction=direction, operation=operation, operation_id=operation_id)
                if body is not None:
                    observation.request_body = body
                    observation.request_source = "WIRE" if direction == "WMS_TO_WES" else "FROZEN_PAYLOAD"
                elif payload is not None:
                    safe, observation.incomplete = safe_value(payload, deadline=deadline)
                    observation.request_body = json.dumps(safe, ensure_ascii=False).encode()
                    observation.request_source = "FROZEN_PAYLOAD"
                preview, state = (
                    preview_json(observation.request_body, deadline=deadline)
                    if observation.request_body is not None
                    else (None, "NOT_CAPTURED")
                )
                event = ExchangeObservation(
                    **_summary(observation),
                    request=WirePreview(body=preview, state=state, source=observation.request_source),
                )
                await self._publisher.publish_to(
                    WMS_DIAGNOSTICS_CHANNEL, "wms_exchange.started", event.model_dump(mode="json")
                )
        except Exception:
            if observation is not None:
                observation.incomplete = True
        return observation

    async def finish(self, observation: WmsCallObservation | None) -> bool:
        if observation is None or observation.finished:
            return False
        observation.finished = True
        saved_id = None
        event = None
        # 整理/保存最多 60%，任何失败都保留最后 30% 的完成发布机会。
        try:
            duration = self._config.budget_ms * 0.6 / 1000
            async with asyncio.timeout(duration):
                event = self._build(observation, deadline=time.monotonic() + duration)
                encoded = self._bounded_json(event, self._config.max_record_bytes)
                saved_id = await self._repository.append(encoded)
                event = ExchangeObservation.model_validate_json(encoded)
        except Exception:
            event = None
        try:
            async with asyncio.timeout(self._config.budget_ms * 0.3 / 1000):
                if event is None:
                    event = ExchangeObservation(**(_summary(observation) | {"incomplete": True}))
                event = event.model_copy(update={"exchange_id": saved_id})
                # 公共 SSE 外层 envelope 也计入 32 KiB，预留 1 KiB。
                encoded = self._bounded_json(event, 31 * 1024)
                await self._publisher.publish_to(WMS_DIAGNOSTICS_CHANNEL, "wms_exchange.completed", json.loads(encoded))
        except Exception:  # nosec B110
            pass  # 诊断失败不得影响原业务结果；取消异常不属于 Exception。
        return saved_id is not None

    def _build(self, observation: WmsCallObservation, *, deadline: float) -> ExchangeObservation:
        previews = {}
        comparisons = []
        incomplete = observation.incomplete
        for side in ("request", "response"):
            raw = getattr(observation, f"{side}_body")
            body, state = preview_json(raw, deadline=deadline) if raw is not None else (None, "NOT_CAPTURED")
            if side == "response" and observation.status_code is None and observation.elapsed_ms is not None:
                body, state = None, "NO_RESPONSE"
            source = observation.request_source if side == "request" else "WIRE" if raw is not None else "NOT_CAPTURED"
            headers, headers_incomplete = protocol_headers(getattr(observation, f"{side}_headers"))
            previews[side] = WirePreview(body=body, state=state, source=source, headers=headers)
            incomplete |= headers_incomplete
            incomplete |= state in {"TRUNCATED", "UNSAFE_JSON", "NOT_CAPTURED", "NO_RESPONSE"}
            schema = getattr(observation, f"{side}_schema")
            contract = getattr(observation, f"{side}_contract")
            if contract is not None and time.monotonic() < deadline:
                schema = contract.json_schema() if isinstance(contract, TypeAdapter) else contract.model_json_schema()
            if (
                (schema is not None or getattr(observation, f"{side}_errors"))
                and body is not None
                and state == "CAPTURED"
            ):
                fields = compare_fields(
                    schema or {},
                    json.loads(body),
                    side=side,
                    source=observation.contract_source
                    if contract is None
                    else f"{contract.__class__.__module__ if isinstance(contract, TypeAdapter) else contract.__module__}:{schema.get('title', 'response-union')}",
                    errors=getattr(observation, f"{side}_errors"),
                    validated=getattr(observation, f"{side}_validated"),
                    deadline=deadline,
                )
                comparisons.extend(fields)
                incomplete |= len(fields) >= 256
        errors = observation.request_errors or observation.response_errors
        status = (
            "ERROR"
            if errors
            else "PASS"
            if observation.request_validated and observation.response_validated and not incomplete
            else "NOT_VALIDATED"
        )
        return ExchangeObservation(
            **(
                _summary(observation)
                | previews
                | {
                    "comparisons": comparisons[:256],
                    "build_version": self._build_version,
                    "contract_status": status,
                    "incomplete": incomplete or len(comparisons) > 256,
                }
            )
        )

    @staticmethod
    def _bounded_json(event: ExchangeObservation, limit: int) -> str:
        encoded = event.model_dump_json()
        while len(encoded.encode("utf-8")) > limit:
            if event.comparisons:
                event = event.model_copy(
                    update={
                        "comparisons": event.comparisons[: len(event.comparisons) // 2],
                        "incomplete": True,
                        "contract_status": "ERROR" if event.contract_status == "ERROR" else "NOT_VALIDATED",
                    }
                )
            elif event.request.body or event.response.body or event.request.headers or event.response.headers:
                event = event.model_copy(
                    update={
                        side: getattr(event, side).model_copy(
                            update={"body": None, "headers": [], "state": "TRUNCATED"}
                        )
                        for side in ("request", "response")
                    }
                    | {
                        "incomplete": True,
                        "contract_status": "ERROR" if event.contract_status == "ERROR" else "NOT_VALIDATED",
                    }
                )
            else:
                raise ValueError("minimum diagnostic exceeds budget")
            encoded = event.model_dump_json()
        return encoded

    async def list_exchanges(self, query: ExchangeQuery) -> ExchangePage:
        async with asyncio.timeout(self._config.budget_ms / 1000):
            return await self._repository.list(query)

    async def stream_events(self, query: ExchangeFilters):
        from src.app.wms_diagnostics.repository import matches

        async with aclosing(self._publisher.subscribe(WMS_DIAGNOSTICS_CHANNEL, timeout_seconds=25.0)) as stream:
            async for envelope in stream:
                if envelope is None:
                    yield ": heartbeat\n\n"
                    continue
                event_type = envelope.get("type")
                if event_type not in {"wms_exchange.started", "wms_exchange.completed"}:
                    continue
                try:
                    event = ExchangeObservation.model_validate(envelope.get("payload"))
                    if not matches(event, query):
                        continue
                    encoded = self._bounded_json(event, 31 * 1024)
                except (ValidationError, ValueError, TypeError):
                    from src.core.logger import logger

                    logger.debug("WMS 诊断实时流忽略不符合展示合同的事件")
                    continue
                yield f"event: {event_type}\ndata: {encoded}\n\n"

    async def get_exchange(self, exchange_id: str) -> ExchangeDetail | None:
        async with asyncio.timeout(self._config.budget_ms / 1000):
            return await self._repository.get(exchange_id)


def build_diagnostics_service() -> WmsDiagnosticsService:
    """在宿主事件循环中取得已配置 Redis；不自行建立客户端或后台任务。"""
    from src.app.sys.services.event_stream_service import event_stream_service
    from src.app.wms_diagnostics.config import diagnostics_config
    from src.app.wms_diagnostics.repository import DiagnosticsRepository
    from src.core.conf import settings
    from src.database.redis_client import get_redis

    config = diagnostics_config()
    return WmsDiagnosticsService(
        DiagnosticsRepository(get_redis(), config), event_stream_service, config, build_version=settings.VERSION
    )
