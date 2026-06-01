"""Callback ingress response models."""

from typing import Literal

from pydantic import BaseModel, Field

from src.core.response import ResponseSchemaModel


class CallbackRejectedResponse(BaseModel):
    """Callback 入口拒收响应数据。"""

    ack: Literal[False] = Field(default=False, description="入口是否接收")
    reason_code: str | None = Field(default=None, description="拒收原因代码")


class CallbackResultAcceptedResponse(BaseModel):
    """设备结果回调接收响应数据。"""

    ack: Literal[True] = Field(default=True, description="入口是否接收")
    request_id: str | None = Field(default=None, description="入口请求 ID")
    trace_id: str | None = Field(default=None, description="Trace ID")
    event_id: str | None = Field(default=None, description="供应商事件 ID")
    causation_id: str | None = Field(default=None, description="因果事件 ID")


class CallbackEventAcceptedResponse(BaseModel):
    """设备事件回调接收响应数据。"""

    status: Literal["submitted", "duplicate"] = Field(description="入口处理状态")
    device_code: str = Field(description="设备编码")
    request_id: str | None = Field(default=None, description="入口请求 ID")
    trace_id: str | None = Field(default=None, description="Trace ID")
    event_id: str | None = Field(default=None, description="供应商事件 ID")
    causation_id: str | None = Field(default=None, description="因果事件 ID")


class CallbackExternalAcceptedResponse(BaseModel):
    """外部系统回调接收响应数据。"""

    status: Literal["submitted", "duplicate"] = Field(description="入口处理状态")
    callback_type: str = Field(description="外部回调类型")
    request_id: str | None = Field(default=None, description="入口请求 ID")
    trace_id: str | None = Field(default=None, description="Trace ID")
    event_id: str | None = Field(default=None, description="供应商事件 ID")
    causation_id: str | None = Field(default=None, description="因果事件 ID")


type CallbackRejectedIngressResponse = ResponseSchemaModel[CallbackRejectedResponse]
type CallbackResultIngressResponse = ResponseSchemaModel[CallbackResultAcceptedResponse | CallbackRejectedResponse]
type CallbackEventIngressResponse = ResponseSchemaModel[CallbackEventAcceptedResponse | CallbackRejectedResponse]
type CallbackExternalIngressResponse = ResponseSchemaModel[CallbackExternalAcceptedResponse | CallbackRejectedResponse]


def build_callback_rejected_response(reason_code: str | None = None) -> CallbackRejectedResponse:
    return CallbackRejectedResponse(reason_code=reason_code)


def build_callback_result_accepted_response(
    *,
    request_id: str | None,
    trace_id: str | None,
    event_id: str | None,
    causation_id: str | None,
) -> CallbackResultAcceptedResponse:
    return CallbackResultAcceptedResponse(
        request_id=request_id,
        trace_id=trace_id,
        event_id=event_id,
        causation_id=causation_id,
    )


def build_callback_event_accepted_response(
    *,
    status: Literal["submitted", "duplicate"],
    device_code: str,
    request_id: str | None,
    trace_id: str | None,
    event_id: str | None,
    causation_id: str | None,
) -> CallbackEventAcceptedResponse:
    return CallbackEventAcceptedResponse(
        status=status,
        device_code=device_code,
        request_id=request_id,
        trace_id=trace_id,
        event_id=event_id,
        causation_id=causation_id,
    )


def build_callback_external_accepted_response(
    *,
    status: Literal["submitted", "duplicate"],
    callback_type: str,
    request_id: str | None,
    trace_id: str | None,
    event_id: str | None,
    causation_id: str | None,
) -> CallbackExternalAcceptedResponse:
    return CallbackExternalAcceptedResponse(
        status=status,
        callback_type=callback_type,
        request_id=request_id,
        trace_id=trace_id,
        event_id=event_id,
        causation_id=causation_id,
    )


__all__ = [
    "CallbackEventAcceptedResponse",
    "CallbackEventIngressResponse",
    "CallbackExternalAcceptedResponse",
    "CallbackExternalIngressResponse",
    "CallbackRejectedIngressResponse",
    "CallbackRejectedResponse",
    "CallbackResultAcceptedResponse",
    "CallbackResultIngressResponse",
    "build_callback_event_accepted_response",
    "build_callback_external_accepted_response",
    "build_callback_rejected_response",
    "build_callback_result_accepted_response",
]
