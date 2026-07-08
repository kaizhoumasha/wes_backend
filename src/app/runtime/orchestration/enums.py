# runtime migration C5a 镜像:src.workline_runtime.enums 的平级副本
# wlr 目录在阶段 3 整体删除时,本镜像改名为正式模块。

"""
作业线运行时契约层枚举定义

本模块定义作业线插件化编排系统的运行时契约枚举类型，确保：
- 枚举值的唯一性和一致性
- 业务语义的清晰表达
- 与架构设计文档的严格对应

注意：SessionStatus / InboxStatus 是实体层枚举，定义在：
- src/app/workline/models/session.py（SessionStatus）
- src/app/workline/models/inbox.py（InboxStatus）

本模块只保留运行时契约层枚举（插件面向的编排契约）：
FailureDomain, TimelineStage, OutboxStatus, OutboxDispatchType,
ManualOperationType, DecisionType

参考文档：
- docs/workline_plugin_architecture_design.md 第 6 章（运行时契约）
- docs/workline_plugin_architecture_design.md 第 8 章（领域模型）
- docs/workline_plugin_architecture_design.md 第 12 章（故障归因）

设计原则：
- DRY: 枚举值统一管理，避免在多处定义
- KISS: 使用简单枚举，不引入复杂的继承层次
- SOLID: 单一职责，每个枚举类只负责一个业务域
"""

from enum import Enum


class FailureDomain(str, Enum):
    """
    故障域枚举

    定义故障的根本原因分类（架构 12.1 节）。

    故障归因优先落在最贴近根因的边界，用于快速区分问题来源。

    属性:
        HARDWARE: 硬件故障（设备损坏、传感器失效）
        NETWORK: 网络故障（连接超时、丢包）
        SOFTWARE: 软件错误（代码异常、状态机违规）
        ORCHESTRATION: 编排错误（Session 不存在、并发冲突）
        ALGORITHM: 算法问题（无可用库位、路径规划失败）
        UPSTREAM: 上游系统问题（WMS 超时、上游数据异常）
        DOWNSTREAM: 下游系统问题（AGV 拒绝、RCS 失败）
        CONFIG: 配置错误（设备参数错误、拓扑配置问题）
        DATA: 数据问题（条码格式错误、状态不一致）
        TIMEOUT: 超时（设备响应超时、外部系统超时）
        MANUAL_INTERVENTION: 人工介入（人工取消、人工确认失败）
    """

    HARDWARE = "HARDWARE"
    NETWORK = "NETWORK"
    SOFTWARE = "SOFTWARE"
    ORCHESTRATION = "ORCHESTRATION"
    ALGORITHM = "ALGORITHM"
    UPSTREAM = "UPSTREAM"
    DOWNSTREAM = "DOWNSTREAM"
    CONFIG = "CONFIG"
    DATA = "DATA"
    TIMEOUT = "TIMEOUT"
    MANUAL_INTERVENTION = "MANUAL_INTERVENTION"


class TimelineStage(str, Enum):
    """
    时间线阶段枚举

    定义 WorklineTimeline 的所有阶段（架构 8.4 节）。

    Timeline 是排障主视图，记录完整因果链。

    属性:
        INGEST: 接入阶段（接收原始请求、写 Inbox）
        ROUTE: 路由阶段（解析设备/作业线/插件归属）
        DECISION: 决策阶段（插件业务判断）
        DISPATCH_PREPARE: 派发准备（生成 Outbox 记录）
        WAITING: 等待阶段（等待设备或外部系统响应）
        CALLBACK: 回调阶段（接收结果回调）
        MANUAL: 人工操作阶段（处理人工介入）
        TIMEOUT: 超时阶段（处理超时事件）
        COMPENSATION: 补偿阶段（回滚或补偿操作）
        COMPLETE: 完成阶段（业务成功结束）
        FAIL: 失败阶段（业务失败结束）
    """

    INGEST = "INGEST"
    ROUTE = "ROUTE"
    DECISION = "DECISION"
    DISPATCH_PREPARE = "DISPATCH_PREPARE"
    WAITING = "WAITING"
    CALLBACK = "CALLBACK"
    MANUAL = "MANUAL"
    TIMEOUT = "TIMEOUT"
    COMPENSATION = "COMPENSATION"
    COMPLETE = "COMPLETE"
    FAIL = "FAIL"


class OutboxStatus(str, Enum):
    """
    发件箱状态枚举

    定义 SystemOutbox 的派发状态（架构 8.8 节）。

    状态迁移保证副作用的可靠派发。

    属性:
        NEW: 新创建的 Outbox 记录，等待派发
        DISPATCHING: 正在派发（已锁定，防止重复派发）
        SENT: 已发送
        BLOCKED_RESOURCE: 因运行时资源隔离暂缓派发
        FAILED: 派发失败（可重试）
        CANCELLED: 已取消（不再派发）
    """

    NEW = "NEW"
    DISPATCHING = "DISPATCHING"
    SENT = "SENT"
    BLOCKED_RESOURCE = "BLOCKED_RESOURCE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class OutboxDispatchType(str, Enum):
    """
    发件箱派发类型枚举

    定义 Outbox 的派发目标类型（架构 8.8 节）。

    属性:
        DEVICE_COMMAND: 设备指令（调用设备标准接口）
        EXTERNAL_HTTP: 外部 HTTP 请求（调用 WMS/RCS/AGV 等系统）
        INTERNAL_SIGNAL: 内部信号（跨 Session 或跨服务通信）
    """

    DEVICE_COMMAND = "DEVICE_COMMAND"
    EXTERNAL_HTTP = "EXTERNAL_HTTP"
    INTERNAL_SIGNAL = "INTERNAL_SIGNAL"


class ManualOperationType(str, Enum):
    """
    人工操作类型枚举

    定义人工可以向系统提交的操作类型（架构 6.7 节）。

    人工操作必须被视为结构化输入，而不是"人工改库"。

    属性:
        RETRY_LAST_COMMAND: 重试上一步指令
        MARK_SUCCESS_AND_CONTINUE: 标记为成功并继续
        MARK_NG_AND_CLOSE: 标记为 NG 并关闭会话
        CANCEL_SESSION: 取消当前会话
        CUSTOM_ACTION: 自定义操作（插件自定义处理逻辑）
    """

    RETRY_LAST_COMMAND = "RETRY_LAST_COMMAND"
    MARK_SUCCESS_AND_CONTINUE = "MARK_SUCCESS_AND_CONTINUE"
    MARK_NG_AND_CLOSE = "MARK_NG_AND_CLOSE"
    CANCEL_SESSION = "CANCEL_SESSION"
    CUSTOM_ACTION = "CUSTOM_ACTION"


class DecisionType(str, Enum):
    """
    决策类型枚举

    定义关键业务决策的类型（架构 8.5 节）。

    DecisionLog 记录关键业务判断过程，用于排障和审计。

    属性:
        BARCODE_VALIDATION: 条码校验（格式、合法性、是否存在）
        BIN_ALLOCATION: 库位分配（分箱算法、可用库位查找）
        ROUTE_SELECTION: 路由选择（确定下一步流程）
        RESOURCE_REQUEST: 资源请求（AGV/CTU/RCS 调度）
        FAILURE_CLASSIFICATION: 失败分类（归因分析）
    """

    BARCODE_VALIDATION = "BARCODE_VALIDATION"
    BIN_ALLOCATION = "BIN_ALLOCATION"
    ROUTE_SELECTION = "ROUTE_SELECTION"
    RESOURCE_REQUEST = "RESOURCE_REQUEST"
    FAILURE_CLASSIFICATION = "FAILURE_CLASSIFICATION"


class FailureCode:
    """
    失败码常量

    定义标准化的失败码，用于运行时失败归因字段。

    属性:
        CONTRACT_MISMATCH: 契约版本不匹配
        DEVICE_TIMEOUT: 设备响应超时
        DEVICE_NOT_FOUND: 设备不存在
        BARCODE_NG: 条码校验失败
    """

    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    DEVICE_TIMEOUT = "DEVICE_TIMEOUT"
    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
    BARCODE_NG = "BARCODE_NG"


# 导出所有枚举类，便于通过 orchestration 域 import *
__all__ = [
    "DecisionType",
    "FailureCode",
    "FailureDomain",
    "ManualOperationType",
    "OutboxDispatchType",
    "OutboxStatus",
    "TimelineStage",
]
