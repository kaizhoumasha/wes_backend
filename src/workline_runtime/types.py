"""
插件运行时类型定义

定义插件返回结果的领域意图类型：
- WaitIntent: 等待意图
- CommandIntent: 设备命令意图
- FailureIntent: 失败归因意图
- PluginResult: 插件返回结果

设计参考: 设计文档 phase2-orchestrator
"""

from typing import Any

from pydantic import BaseModel, Field


class WaitIntent(BaseModel):
    """等待意图

    插件返回此意图时，编排器将 Session 置于等待状态，
    等待外部回调（设备结果、人工响应等）。

    Attributes:
        wait_type: 等待类型 (COMMAND_RESULT, EXTERNAL_RESPONSE, etc.)
        wait_token: 用于回调匹配的唯一标识
        deadline_seconds: 超时秒数
    """

    wait_type: str
    wait_token: str
    deadline_seconds: int


class CommandIntent(BaseModel):
    """设备命令意图

    插件返回此意图时，编排器将创建设备命令并写入 Outbox。

    Attributes:
        target_device_id: 目标设备 ID
        action: 动作类型 (PICK_AND_PUT, MOVE_FORWARD, etc.)
        parameters: 命令参数
    """

    target_device_id: int
    action: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class FailureIntent(BaseModel):
    """失败归因意图

    插件返回此意图时，编排器将 Session 标记为失败，
    并记录失败归因用于排障。

    Attributes:
        domain: 失败域 (HARDWARE, SOFTWARE, UPSTREAM, etc.)
        code: 具体错误码
        message: 人类可读的错误消息
    """

    domain: str
    code: str
    message: str


class PluginResult(BaseModel):
    """插件返回结果 - 领域意图集合

    插件处理完成后返回此结果，编排器根据其中的意图执行相应操作。

    Attributes:
        transition: 状态机触发器（由插件状态机定义）
        context_patch: Session 上下文更新
        decisions: 编排决策列表
        commands: 设备命令列表
        wait: 等待条件
        failure: 失败归因
        complete: 完成标记（Session 将被标记为 COMPLETED）
    """

    # 状态迁移
    transition: str | None = None

    # 上下文更新
    context_patch: dict[str, Any] = Field(default_factory=dict)

    # 领域决策
    decisions: list[dict[str, Any]] = Field(default_factory=list)

    # 设备命令
    commands: list[CommandIntent] = Field(default_factory=list)

    # 等待条件
    wait: WaitIntent | None = None

    # 失败归因
    failure: FailureIntent | None = None

    # 完成标记
    complete: bool = False


__all__ = [
    "CommandIntent",
    "FailureIntent",
    "PluginResult",
    "WaitIntent",
]
