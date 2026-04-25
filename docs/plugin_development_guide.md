# WORKLINE 插件开发指南

本文面向后续 WORKLINE 插件开发者。插件只负责一种 WORKLINE 业务模板：把硬件设备角色、业务状态、业务判断和命令意图组织起来。插件架构负责提供运行态能力，减少每扩展一条业务线时的心智负担。

当前参考插件：

- `src/workline_plugins/smt_classifier/`：复杂 SMT 粗分业务样板。
- `src/workline_plugins/inbound_tote_qc/`：第二个最小插件 spike，用于验证平台抽象不依赖 SMT 特例。
- `docs/templates/workline_plugin/`：从两个插件交集沉淀出来的模板和 fixtures。

## 核心边界

### 插件负责什么

- 定义本业务线的 `plugin_key`、`contract_version` 和 manifest。
- 声明设备角色、事件来源、命令目标和设备能力要求。
- 定义业务 payload 模型、业务键解析器和结果分类器。
- 定义类型化 context 和状态机。
- 通过 handler 把事件和命令结果转成 `PluginResult`：状态迁移、业务决策、命令、等待、完成或 failure。

### 插件不负责什么

- 不直接写 Inbox / Outbox / DeviceCommand / WorklineSession。
- 不查询数据库拼设备拓扑，拓扑由平台根据 `Device.upstream_device_id` 和 manifest 校验。
- 不把 sandbox 标志写入消息 payload。
- 不在 runtime、callback、dispatcher 中为某个业务插件开私有分支。
- 不使用 legacy `step_code` 作为业务状态字段；统一使用 `plugin_state`。

## 白皮书包络

`docs/integration/third_party_integration_whitepaper.md` 规定了事件、结果和命令的两层结构。业务字段只能放在业务层。

| 流向 | 顶层字段 | 业务字段位置 |
| --- | --- | --- |
| 设备事件 event | `device_code`, `event_type`, `timestamp`, `data` | `data` |
| 命令结果 result | `command_code`, `device_code`, `result`, `finish_time`, `data`, `error_detail` | `data` |
| WES 下发命令 | `device_code`, `command_code`, `task_type`, `priority`, `timeout`, `timestamp`, `params` | `params` |

示例：

```json
{
  "device_code": "SCAN01",
  "event_type": "TOTE_ARRIVED",
  "timestamp": 1777046400000,
  "data": {
    "tote_id": "TOTE-20260425-001",
    "station_code": "INBOUND_QC_01"
  }
}
```

不要这样写：

```json
{
  "device_code": "SCAN01",
  "event_type": "TOTE_ARRIVED",
  "timestamp": 1777046400000,
  "tote_id": "TOTE-20260425-001"
}
```

## 推荐目录

新插件目录应从以下结构开始：

```text
src/workline_plugins/<plugin_key>/
  __init__.py
  contract.py
  context.py
  state_machine.py
  plugin.py
tests/workline_plugins/test_<plugin_key>_plugin.py
```

模板见 `docs/templates/workline_plugin/`。模板不是 SMT 的缩小复制，而是 SMT 和 `inbound_tote_qc` 的共同结构。

## contract.py

`contract.py` 放协议边界和业务分类逻辑。

- 使用 Pydantic 定义 `event.data`、`result.data`、`command.params` 模型。
- `business_key_resolver` 只从业务层读取稳定业务键。
- `result_classifier` 区分设备执行失败、数据非法和业务结果。
- 命令参数通过 helper 构建，确保业务字段只进入 `params`。

最小示例：

```python
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.workline_runtime.plugin_base import EventPayload

class ItemArrivedData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_key: str
    station_code: str
    expected_value: float = Field(gt=0)
    tolerance: float = Field(gt=0)


class ItemArrivedPayload(EventPayload):
    data: ItemArrivedData


class MeasureCompletedData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_key: str
    actual_value: float = Field(gt=0)


def resolve_business_key(payload: dict[str, Any]) -> str:
    data = payload.get("data")
    if not isinstance(data, dict) or not data.get("business_key"):
        raise ValueError("ITEM_ARRIVED.data.business_key is required")
    return str(data["business_key"])


def classify_result(payload: dict[str, Any]) -> str | None:
    if payload.get("result") == "FAILED":
        return "hardware_failure"
    return None


def build_measure_params(*, business_key: str, station_code: str) -> dict[str, str]:
    return {"business_key": business_key, "station_code": station_code}
```

## context.py

`context.py` 定义插件自己的业务上下文。handler 不应散落读取 `ctx.session.context_json.get(...)`，应先解析成类型化对象。

```python
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.workline_runtime.plugin_context import PluginContext


class ExampleContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plugin_state: str = "IDLE"
    business_key: str | None = None
    expected_value: float | None = None
    tolerance: float | None = None
    reason_code: str | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "ExampleContext":
        return cls.model_validate(value or {})

    @classmethod
    def from_session(cls, ctx: PluginContext) -> "ExampleContext":
        return cls.from_mapping(ctx.session.context_json)

    def to_patch(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)
```

## state_machine.py

状态机应显式声明状态和 trigger。插件 handler 通过 `.transition("<trigger>")` 声明业务意图，状态合法性由平台和状态机兜底校验。

```python
from enum import StrEnum

from transitions import Machine


class ExampleState(StrEnum):
    IDLE = "IDLE"
    WAITING_MEASURE = "WAITING_MEASURE"
    WAITING_DIVERT = "WAITING_DIVERT"
    MANUAL_HOLD = "MANUAL_HOLD"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class ExampleStateMachine:
    states = [state.value for state in ExampleState]
    transitions = [
        {"trigger": "item_arrived", "source": ExampleState.IDLE.value, "dest": ExampleState.WAITING_MEASURE.value},
        {
            "trigger": "measure_ok",
            "source": ExampleState.WAITING_MEASURE.value,
            "dest": ExampleState.WAITING_DIVERT.value,
        },
        {
            "trigger": "measure_ng",
            "source": ExampleState.WAITING_MEASURE.value,
            "dest": ExampleState.WAITING_DIVERT.value,
        },
        {"trigger": "divert_ok", "source": ExampleState.WAITING_DIVERT.value, "dest": ExampleState.COMPLETED.value},
        {"trigger": "manual_hold", "source": ExampleState.WAITING_DIVERT.value, "dest": ExampleState.MANUAL_HOLD.value},
        {
            "trigger": "fail",
            "source": [
                ExampleState.IDLE.value,
                ExampleState.WAITING_MEASURE.value,
                ExampleState.WAITING_DIVERT.value,
                ExampleState.MANUAL_HOLD.value,
            ],
            "dest": ExampleState.ERROR.value,
        },
    ]

    def __init__(self, initial: str = ExampleState.IDLE.value):
        self.state = initial
        self.machine = Machine(
            model=self,
            states=self.states,
            transitions=self.transitions,
            initial=initial,
            auto_transitions=False,
            ignore_invalid_triggers=False,
        )

    def may_trigger(self, trigger: str) -> bool:
        return bool(getattr(self, f"may_{trigger}", lambda: False)())
```

## plugin.py

插件入口必须把 manifest、handler 和业务结果写清楚。

```python
from src.workline_runtime.plugin_base import PluginResultBuilder, WorklinePlugin, on_command, on_event, step
from src.workline_runtime.plugin_manifest import DeviceRoleRequirement, WorklinePluginManifest
from src.workline_runtime.plugin_sdk.contracts import NormalizedCommandResult
from src.workline_runtime.types import CommandTargetScope

from .context import ExampleContext
from .contract import ItemArrivedPayload, MeasureCompletedData, build_measure_params, classify_result, resolve_business_key
from .state_machine import ExampleState, ExampleStateMachine


class ExamplePlugin(WorklinePlugin):
    plugin_key = "example_plugin"
    contract_version = "2026.04"
    manifest = WorklinePluginManifest(
        plugin_key=plugin_key,
        contract_version=contract_version,
        required_device_roles=(
            DeviceRoleRequirement("ENTRY_SENSOR", 1, 1, frozenset({"scan_item"})),
            DeviceRoleRequirement("MEASURE_DEVICE", 1, 1, frozenset({"measure_item"})),
        ),
        business_key_resolver=resolve_business_key,
        result_classifier=classify_result,
        state_machine_class=ExampleStateMachine,
        context_model=ExampleContext,
        event_source_roles={"ITEM_ARRIVED": "ENTRY_SENSOR"},
        command_target_roles={"MEASURE_ITEM": "MEASURE_DEVICE"},
    )

    @on_event("ITEM_ARRIVED")
    async def handle_item_arrived(self, ctx, event: ItemArrivedPayload):
        return (
            PluginResultBuilder(ctx)
            .transition("item_arrived")
            .command(
                command_type="MEASURE_ITEM",
                target_scope=CommandTargetScope.DOWNSTREAM,
                device_role="MEASURE_DEVICE",
                parameters=build_measure_params(
                    business_key=event.data.business_key,
                    station_code=event.data.station_code,
                ),
            )
            .wait(event_type="MEASURE_ITEM", timeout_seconds=120)
            .context(
                ExampleContext(
                    plugin_state=ExampleState.WAITING_MEASURE,
                    business_key=event.data.business_key,
                    expected_value=event.data.expected_value,
                    tolerance=event.data.tolerance,
                ).to_patch()
            )
            .build()
        )

    @on_command("MEASURE_ITEM", result="SUCCESS")
    @step(ExampleState.WAITING_MEASURE)
    async def handle_measure_success(self, ctx, result: NormalizedCommandResult):
        data = MeasureCompletedData.model_validate(result.data)
        business_ctx = ExampleContext.from_session(ctx)
        is_ng = abs(data.actual_value - business_ctx.expected_value) > business_ctx.tolerance
        builder = PluginResultBuilder(ctx).transition("measure_ng" if is_ng else "measure_ok")
        if is_ng:
            builder.business_decision(
                reason_code="VALUE_OUT_OF_TOLERANCE",
                message="业务检测超出允差",
                business_key=data.business_key,
                evidence={
                    "expected_value": business_ctx.expected_value,
                    "actual_value": data.actual_value,
                    "tolerance": business_ctx.tolerance,
                },
            )
        return builder.build()
```

## 业务 NG 与系统异常

必须区分业务结果和系统异常，否则 trace、timeline、运营查询会混淆。

| 类型 | 含义 | 插件表达 |
| --- | --- | --- |
| 业务 NG | 业务规则判断出的预期结果，例如重量超差、质检不合格 | `.business_decision(...)`，可继续派发分流命令 |
| 数据非法 | 回调 payload 缺字段、业务数据类型错误、包络不符合协议 | `build_payload_invalid_failure(...)` 或 `failure(domain="DATA", ...)` |
| 硬件异常 | 设备返回 `FAILED`、设备离线、传感器异常 | `failure(domain="HARDWARE", ...)` |
| 超时 | 等待设备或外部系统回调超时 | `failure(domain="TIMEOUT", ...)` |
| 系统异常 | 插件 bug、状态迁移非法、配置缺失、runtime 异常 | `failure(domain="SOFTWARE", ...)` 或由平台诊断 |

业务 NG 不是系统 failure。业务 NG 应携带 `reason_code`、`message`、`business_key` 和 evidence，便于 trace/timeline 查询。

## Sandbox 调试

Sandbox 是 WORKLINE 级调试能力，不是 dry-run，也不是插件级 replay。

### 行为

- `WorkLine.run_mode=SIMULATION` 只能在 `APP_ENV=dev` 或 `APP_ENV=test` 使用。
- 创建 Session 时会快照 WorkLine 的 `run_mode`，运行中的 Session 不受后续 WorkLine 配置修改影响。
- `DEVICE_COMMAND` 和 `EXTERNAL_HTTP` 在 SIMULATION 下仍走真实编排链路，但派发出口切到 sandbox。
- 消息 payload 尽量与 live 一致，不增加 `sandbox` 标志字段。
- 调试人员从沙箱待处理列表处理指令，再按白皮书 result 包络手工回调，推进同一个 Session。

### Happy path

1. 在开发或测试环境创建 `run_mode=SIMULATION` 的 WorkLine。
2. 绑定满足 manifest 的设备拓扑和能力。
3. 发送事件回调，业务字段放在 `data`。
4. Runtime 创建或解析 Session，插件 handler 产生命令，Outbox 进入 sandbox 派发。
5. 调试人员读取沙箱待处理指令，按设备实际协议人工构造 result 回调。
6. 回调仍走 callback 入口，业务字段放在 `data`，Session 继续推进。
7. 重复处理后续沙箱指令，直到 Session `COMPLETED`、业务 NG 分流或 failure。

### 插件级诊断与 Sandbox 的区别

插件级诊断工具只解释单条 payload 会命中哪个 handler、解析出什么 context、返回什么 `PluginResult`。它不创建真实 Session，不写 Outbox，也不验证派发和手工 callback 闭环。

WORKLINE 级调试必须用 sandbox，因为只有 sandbox 覆盖事件输入、命令派发、手工 callback 和 Session 推进。

## 测试 Checklist

每个新插件至少覆盖：

- manifest：`plugin_key`、`contract_version`、设备角色、事件来源、命令目标、状态机和 context model。
- 包络：event/result 业务字段只在 `data`，下发命令业务字段只在 `params`。
- 业务键：`business_key_resolver` 不依赖 runtime 私有逻辑。
- 状态机：合法 trigger 可走通，非法 trigger 被拒绝。
- happy path：一个事件产生一个命令，等待一个回调，Session 能继续推进。
- 业务 NG：表达为 `business_decision`，不写成系统 failure。
- 系统异常：设备 `FAILED`、payload 非法、timeout 至少覆盖一个。
- sandbox：`SIMULATION` 不改变消息 payload，派发到 sandbox，由手工 callback 推进。

建议先运行：

```bash
uv run pytest -q tests/workline_plugins/test_<plugin_key>_plugin.py
uv run ruff format src/workline_plugins/<plugin_key> tests/workline_plugins/test_<plugin_key>_plugin.py
uv run ruff check src/workline_plugins/<plugin_key> tests/workline_plugins/test_<plugin_key>_plugin.py
```

涉及 callback、outbox、session resolver 或 sandbox 时，再补 runtime/API 相关测试。

## 开发顺序

1. 从 `docs/templates/workline_plugin/` 复制模板。
2. 先写 `contract.py`，锁定 `data` / `params` 和业务键。
3. 写 `context.py` 和 `state_machine.py`，明确业务状态。
4. 写 manifest，声明设备角色、能力和拓扑方向。
5. 写第一个事件 handler，让它产生一个命令和 wait。
6. 写第一个命令 result handler，让它推进状态或完成。
7. 补业务 NG 和系统异常。
8. 用插件级诊断检查 handler，再用 sandbox 跑完整 WORKLINE happy path。

## 参考

- 白皮书：`docs/integration/third_party_integration_whitepaper.md`
- 重构计划：`docs/business/workline_plugin_refactor_next_phase_plan.md`
- 第二插件 spike：`docs/business/workline_plugin_second_spike.md`
- Runtime 基类：`src/workline_runtime/plugin_base.py`
- Manifest：`src/workline_runtime/plugin_manifest.py`
- SMT 样板：`src/workline_plugins/smt_classifier/`
- 最小第二插件：`src/workline_plugins/inbound_tote_qc/`
