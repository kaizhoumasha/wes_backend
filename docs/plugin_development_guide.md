# WORKLINE 插件开发指南

本文面向后续 WORKLINE 插件开发者。插件只负责一种 WORKLINE 业务模板：把硬件设备角色、业务状态、业务判断和命令意图组织起来。插件架构负责提供运行态能力，减少每扩展一条业务线时的心智负担。

当前参考材料：

- `docs/templates/workline_plugin/`：沉淀通用插件结构的模板和 fixtures。

## 核心边界

### 插件负责什么

- 定义本业务线的 `plugin_key`、`contract_version` 和 manifest。
- 用 pure-data manifest 声明设备、位置、拓扑、事件、命令和资源边界。
- 在插件类成员上定义业务键解析、结果分类、context model、物料身份解析和 NG 原因目录。
- 定义业务 payload 模型。
- 定义类型化 context。
- 通过 handler 把事件和命令结果转成 `RuntimeIntent` 或 `list[RuntimeIntent]`：更新上下文、下发命令、业务 NG、阻断或完成。

### 插件不负责什么

- 不直接写 Inbox / Outbox / DeviceCommand / WorklineSession。
- 不查询数据库拼设备拓扑，启用前配置校验由平台根据 manifest 的角色、数量和能力要求完成；`Device.upstream_device_id` 只作为物理路径辅助信息。
- 不把 sandbox 标志写入消息 payload。
- 不生成、传入或覆盖 `command_code`；设备命令编码由 WES Runtime 统一生成，并作为设备幂等键下发。
- 不在 runtime、callback、dispatcher 中为某个业务插件开私有分支。
- 不维护插件私有状态机，不写 Session 状态，不维护物料当前位置。
- 不处理命令幂等、ACK、Result、Timeout、Retry；这些属于 Runtime。

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
  plugin.py
tests/workline_plugins/test_<plugin_key>_plugin.py
```

模板见 `docs/templates/workline_plugin/`。模板抽象通用插件结构，不复制 SMT 的私有复杂度。

## Manifest 与 runtime helper

manifest 是 pure data，只保留八类可序列化静态事实：

```text
plugin_key, contract_version, devices, positions, topology, commands, events, resource_boundaries
```

- `devices` 使用 `DeviceRequirement`，声明设备角色、数量和硬件能力。
- positions 只声明 WES-managed rack docking positions / inventory-fact anchors，不枚举所有物理点位。
- `topology` 中 MATERIAL_FLOW 只表达 rack position 到 rack position；设备动作边使用 `FlowEdgeType.OPERATION`。
- `events` 使用 `EventBinding`，声明事件名、来源设备角色、事件分类和 payload schema 引用。
- `commands` 使用 `CommandBinding`，声明命令名、目标设备角色、位置参数和结果事件绑定。
- `resource_boundaries` 使用 `ResourceBoundary`，声明 rack/WMS/snapshot/lease 等资源编排边界。
- PositionArg 静态位置使用 `position_ref`，`position_ref` 与 `source` 互斥；`PositionArgSource` 不支持 `STATIC`。

运行时行为不进入 manifest。registry helper 通过插件实例读取以下成员：

- `resolve_business_key(payload_json)`
- `classify_result(payload_json)`
- 必须实现 `get_context_model()`
- `resolve_material_identity(input_value)`
- `list_ng_reasons()`

## contract.py

`contract.py` 放协议边界和业务分类逻辑。

- 使用 Pydantic 定义 `event.data`、`result.data`、`command.params` 模型。
- `resolve_business_key` 只从业务层读取稳定业务键。
- `classify_result` 区分设备执行失败、数据非法和业务结果。
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

## plugin.py

插件入口必须把 pure-data manifest、runtime helper 和 handler 写清楚。示例只展示合同形状，完整业务判断放在插件自己的 handler 或 service 中。

```python
from typing import Any

from src.workline_runtime.material_identity import MaterialIdentity, MaterialIdentityInput
from src.workline_runtime.ng_reason import NgReasonDefinition
from src.workline_runtime.plugin_base import WorklinePlugin, on_event
from src.workline_runtime.plugin_manifest import (
    CommandBinding,
    DeviceRequirement,
    EventBinding,
    EventCategory,
    FlowEdge,
    FlowEdgeType,
    NodeRef,
    NodeRefKind,
    Position,
    PositionArg,
    PositionArgRole,
    PositionCarrierCapability,
    ResourceBoundary,
    TopologySpec,
    WorklinePluginManifest,
)

from .context import ExampleContext
from .contract import (
    ItemArrivedPayload,
    build_measure_params,
    classify_result,
    resolve_business_key,
    resolve_material_identity,
)


class ExamplePlugin(WorklinePlugin):
    plugin_key = "example_plugin"
    contract_version = "2026.04"

    manifest = WorklinePluginManifest(
        plugin_key=plugin_key,
        contract_version=contract_version,
        devices=(
            DeviceRequirement(role="ENTRY_SENSOR", min_count=1, max_count=1, hardware_capabilities=("scan_item",)),
            DeviceRequirement(role="MEASURE_DEVICE", min_count=1, max_count=1),
        ),
        positions=(
            Position(
                code="ENTRY_POSITION",
                role="ENTRY",
                station_code="ENTRY_STATION",
                carrier_capability=PositionCarrierCapability(allowed_rack_kinds=("SINGLE_LAYER",)),
            ),
            Position(
                code="MEASURE_POSITION",
                role="WORK",
                station_code="MEASURE_STATION",
                carrier_capability=PositionCarrierCapability(allowed_rack_kinds=("SINGLE_LAYER",)),
            ),
        ),
        topology=TopologySpec(
            flow_edges=(
                FlowEdge(
                    from_node=NodeRef(NodeRefKind.DEVICE_ROLE, "ENTRY_SENSOR"),
                    to_node=NodeRef(NodeRefKind.POSITION, "ENTRY_POSITION"),
                    type=FlowEdgeType.OPERATION,
                ),
                FlowEdge(
                    from_node=NodeRef(NodeRefKind.POSITION, "ENTRY_POSITION"),
                    to_node=NodeRef(NodeRefKind.POSITION, "MEASURE_POSITION"),
                    type=FlowEdgeType.MATERIAL_FLOW,
                ),
            )
        ),
        events=(
            EventBinding(
                event="ITEM_ARRIVED",
                source_device_roles=("ENTRY_SENSOR",),
                category=EventCategory.ENTRY_DEVICE,
                payload_schema_ref="ItemArrivedPayload",
            ),
        ),
        commands=(
            CommandBinding(
                command="MEASURE_ITEM",
                target_device_role="MEASURE_DEVICE",
                position_args=(
                    PositionArg(name="work_position", role=PositionArgRole.TARGET, position_ref="MEASURE_POSITION"),
                ),
                payload_schema_ref="MeasureParams",
            ),
        ),
        resource_boundaries=(
            ResourceBoundary(
                position_code="MEASURE_POSITION",
                rack_kind="SINGLE_LAYER",
                business_demand_type="MEASURE_WORK_RACK",
                wms_operation_type="SUPPLY_MEASURE_RACK",
                snapshot_kind="ACTIVE_MEASURE_RACK",
                lease_scope="STATION",
            ),
        ),
    )

    def resolve_business_key(self, payload_json: dict[str, Any]) -> str | None:
        return resolve_business_key(payload_json)

    def classify_result(self, payload_json: dict[str, Any]) -> str | None:
        return classify_result(payload_json)

    def get_context_model(self) -> type[ExampleContext]:
        return ExampleContext

    def resolve_material_identity(self, input_value: MaterialIdentityInput) -> MaterialIdentity:
        return resolve_material_identity(input_value)

    def list_ng_reasons(self) -> tuple[NgReasonDefinition, ...]:
        return ()

    @on_event("ITEM_ARRIVED")
    async def handle_item_arrived(self, ctx, event: ItemArrivedPayload):
        return ctx.next.command(
            action="MEASURE_ITEM",
            device_role="MEASURE_DEVICE",
            destination_role="MEASURE_DEVICE",
            payload=build_measure_params(
                business_key=event.data.business_key,
                station_code=event.data.station_code,
            ),
            timeout_seconds=120,
        )
```

## 业务 NG、异常流与错误

必须区分业务结果和系统错误，否则 trace、timeline、运营查询会混淆。第一性原则：

> 只要系统知道下一步去哪，并且能自动推进，就不是错误。

因此，错误只保留给“流程无法继续推进，需要人工、维修、对账或外部介入”的情况。NG 是物料的业务结果，不是系统失败；已建模异常流是非主路径，不是失败。

| 类型 | 含义 | 是否算错误 | 插件表达 |
| --- | --- | --- | --- |
| OK | 业务判断通过，可走正常下游 | 否 | `RuntimeIntent.command(...)` 或 `RuntimeIntent.complete(...)` |
| 业务 NG | 业务规则判断出的预期结果，例如重量超差、质检不合格、扫码判定 NG | 否 | `RuntimeIntent.mark_ng(...)`，再 `command(...)` 到 NG 设备/缓存位，或 `complete(...)` |
| 已建模异常流 | 非主路径，但插件和 Runtime 已知道如何处理，例如返工、转 NG、人工复核前置动作 | 否 | `mark_ng(...)`、`command(...)`、`continue_next(...)`、`complete(...)` |
| 阻断 | 当前自动流程不能继续，但可以由人工处理、恢复、重试或对账 | 是 | `RuntimeIntent.block(...)`，说明 `reason_code`、责任域和 evidence |
| 硬件/通信故障 | 设备离线、动作失败、ACK/Result 超时、物料位置不确定 | 是 | 插件不要伪装成业务 NG；Runtime 进入阻断/对账路径 |
| 数据非法 | payload 缺字段、业务数据类型错误、包络不符合协议 | 视边界而定 | 未入站由 callback/inbox 拒绝；已入 Session 但无法解释时 `block(...)` |
| 系统异常 | 插件 bug、配置缺失、拓扑不可达、Runtime 校验失败 | 是 | 插件避免吞掉；Runtime 记录诊断并阻断 |

插件开发硬规则：

- 不把业务 NG 写入 `failure_code`、`error_code`、`FAILED` 或设备故障态。
- 不用 `CommandResult.FAILED` 表达“检测结果 NG”。设备动作成功但业务结果 NG 时，设备应回 `result=SUCCESS`，并在 `data` 中携带 `inspection_result=NG`、`ng_reason` 等业务字段。
- 只有设备动作未完成、位置不确定、通信失败、超时、拓扑不可达、插件意图非法等“无法自动推进”的情况，才进入错误/阻断。
- 业务 NG 必须携带 `reason_code`、`message`、`business_key` 或可解析物料身份，以及 evidence，便于 trace/timeline 查询和统计。
- 插件只做业务决策；Session 状态、设备状态、命令幂等、超时、重试、对账和阻断恢复由 Runtime 负责。

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
7. 重复处理后续沙箱指令，直到 Session `COMPLETED`、业务 NG 分流完成或进入阻断/错误。

### 插件级诊断与 Sandbox 的区别

插件级诊断工具只解释单条 payload 会命中哪个 handler、解析出什么 context、返回什么 `RuntimeIntent`。它不创建真实 Session，不写 Outbox，也不验证派发和手工 callback 闭环。

WORKLINE 级调试必须用 sandbox，因为只有 sandbox 覆盖事件输入、命令派发、手工 callback 和 Session 推进。

## 测试 Checklist

每个新插件至少覆盖：

- manifest：八个顶层静态字段、`DeviceRequirement`、`EventBinding`、`CommandBinding`、`ResourceBoundary` 和位置参数引用完整性。
- 包络：event/result 业务字段只在 `data`，下发命令业务字段只在 `params`。
- 业务键：插件实例 `resolve_business_key` 不依赖 runtime 私有逻辑。
- 当前事实：handler 只根据 Session context、设备事件和命令结果生成下一步 `RuntimeIntent`。
- happy path：一个事件产生一个命令，等待一个回调，Session 能继续推进。
- 业务 NG：表达为 `RuntimeIntent.mark_ng(...)`，不写成系统 failure，并验证后续 NG 分流或完成。
- 已建模异常流：例如返工、转 NG、人工复核前置动作，必须验证不会污染 `failure_code` / 设备 `error_code`。
- 系统错误：设备动作失败、payload 已入 Session 但无法解释、timeout 至少覆盖一个，必须进入 `block(...)` 或 Runtime 对账路径。
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
3. 写 `context.py`，明确插件需要读取和更新的业务事实。
4. 写 manifest，声明设备、位置、拓扑、结构化事件、结构化命令和资源边界。
5. 写第一个事件 handler，让它产生一个命令和 wait。
6. 写第一个命令 result handler，让它推进状态或完成。
7. 补业务 NG、已建模异常流和真正系统错误。
8. 用插件级诊断检查 handler，再用 sandbox 跑完整 WORKLINE happy path。

## 参考

- 白皮书：`docs/integration/third_party_integration_whitepaper.md`
- 重构计划：`docs/business/workline_plugin_refactor_next_phase_plan.md`
- Runtime 基类：`src/workline_runtime/plugin_base.py`
- Manifest：`src/workline_runtime/plugin_manifest.py`
- 插件模板：`docs/templates/workline_plugin/`
