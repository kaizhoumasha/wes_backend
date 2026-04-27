"""
回调接收日志模型 (Callback Log)

用于记录设备回调的详细元数据，支持问题排查和链路追踪。
"""

from datetime import datetime
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy import JSON, Column, Text
from sqlmodel import Field as SQLField

from src.core.mixins import DataTableMixin
from src.database.schema_conf import SchemaType


class CallbackLog(DataTableMixin, table=True):
    """
    回调接收日志数据库表模型

    记录每次设备回调的详细元数据，用于：
    - 问题排查：查看原始请求内容
    - 性能监控：分析响应时间
    - 链路追踪：通过 request_id/trace_id 串联调用链
    - 安全审计：记录调用来源（IP、User-Agent）

    字段说明:
    - callback_type: 回调类型 (event/result)
    - device_id: 设备 ID
    - request_body: 原始请求体（JSON）
    - client_ip: 客户端 IP
    - user_agent: 客户端 User-Agent
    - request_id: 请求 ID（链路追踪）
    - response_status: HTTP 响应状态码
    - response_time_ms: 响应时间（毫秒）
    - error_message: 错误消息（如果处理失败）
    - trace_id: Trace ID（串联整个流程）
    """

    __tablename__: ClassVar[str] = "callback_logs"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value  # 业务数据表

    # 回调信息
    callback_type: str = SQLField(
        index=True,
        description="回调类型: event/result",
    )
    device_id: str = SQLField(
        max_length=50,
        index=True,
        description="设备 ID",
    )

    # 请求信息
    request_body: dict[str, Any] = SQLField(
        sa_column=Column(JSON),
        description="原始请求体（JSON 格式）",
    )

    # 网络信息
    client_ip: str | None = SQLField(
        default=None,
        max_length=50,
        description="客户端 IP 地址",
    )
    user_agent: str | None = SQLField(
        default=None,
        sa_column=Column(Text),
        description="客户端 User-Agent",
    )

    # 链路追踪
    request_id: str | None = SQLField(
        default=None,
        max_length=100,
        index=True,
        description="请求 ID（用于链路追踪）",
    )
    trace_id: str | None = SQLField(
        default=None,
        max_length=100,
        index=True,
        description="统一 Trace ID",
    )
    event_id: str | None = SQLField(
        default=None,
        max_length=200,
        index=True,
        description="供应商事件 ID",
    )
    causation_id: str | None = SQLField(
        default=None,
        max_length=200,
        description="因果事件 ID",
    )

    # 响应信息
    response_status: int = SQLField(
        description="HTTP 响应状态码",
    )
    response_time_ms: int = SQLField(
        description="响应时间（毫秒）",
    )

    # 错误信息
    error_message: str | None = SQLField(
        default=None,
        sa_column=Column(Text),
        description="错误消息（如果处理失败）",
    )
    ingress_outcome: str | None = SQLField(
        default=None,
        max_length=50,
        description="入口结果：ACCEPTED/REJECTED/FAILED/DUPLICATE",
    )
    failure_stage: str | None = SQLField(
        default=None,
        max_length=100,
        description="入口失败阶段：REQUEST_PARSE/ENVELOPE_VALIDATE/DEVICE_CONTEXT_RESOLVE/...",
    )


# ==================== Schema ====================


class CallbackLogCreate(BaseModel):
    """创建回调日志 Schema"""

    callback_type: str
    device_id: str
    request_body: dict[str, Any]
    client_ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    event_id: str | None = None
    causation_id: str | None = None
    response_status: int
    response_time_ms: int
    error_message: str | None = None
    ingress_outcome: str | None = None
    failure_stage: str | None = None


class CallbackLogResponse(BaseModel):
    """回调日志响应 Schema"""

    id: int
    callback_type: str
    device_id: str
    request_body: dict[str, Any]
    client_ip: str | None
    user_agent: str | None
    request_id: str | None
    trace_id: str | None
    event_id: str | None
    causation_id: str | None
    response_status: int
    response_time_ms: int
    error_message: str | None
    ingress_outcome: str | None
    failure_stage: str | None
    created_at: datetime
    updated_at: datetime


# ==================== 导出 ====================


__all__ = [
    "CallbackLog",
    "CallbackLogCreate",
    "CallbackLogResponse",
]
