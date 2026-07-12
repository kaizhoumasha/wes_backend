# 作业线业务/数据/事件流规范草案

> **版本**: 0.1
> **日期**: 2026-03-25
> **状态**: Draft / SSOT
> **适用范围**: 作业线运行时编排、设备事件接入、指令结果回流、插件决策、拓扑推导

---

## 1. 文档定位

本文档是当前仓库内关于作业线运行时语义的唯一权威合同，统一约束以下问题：

1. 业务流如何启动、推进、终止
2. 数据对象各自承担什么职责
3. `callback/event` 与 `callback/result` 如何进入编排主线
4. `correlation_id`、`command_code`、`session_id` 的边界
5. 设备拓扑如何推导
6. 插件、编排器、派发器各自能做什么，不能做什么

以下文档如与本文不一致，以本文为准：

- `workline_plugin_architecture_design.md`
- `wms_rcs_interface_requirements.md`
- `../archive/legacy-smt-classifier/workline_topology_overview.md`（历史样例，仅用于语义对照）

---

## 2. 核心结论

### 2.1 工作线边界

`LEFT/RIGHT` 不是插件内部的业务分支，而是两条独立的 `WorkLine` 实例。

插件只处理“当前工作线内部”的设备集合、状态和拓扑，不负责跨工作线判断，也不依赖“左侧/右侧”命名做业务分支。

### 2.2 拓扑来源

设备拓扑只从基础数据推导：

- `Device.work_line_id`: 设备归属哪条 `WorkLine`
- `Device.upstream_device_id`: 设备在当前工作线中的上游关系

运行时不得依赖 `workline.config`、位置前缀、设备编码前缀、文档示意表等非权威来源推导拓扑。

### 2.3 统一编排主线

所有运行时输入必须回到同一条编排主线：

`Ingress -> RuntimeInbox -> Orchestrator -> Session/Timeline/Decision/Outbox -> OutboxDispatchService -> callback 回流`

禁止存在“部分输入走编排器，另一部分输入直接改业务状态”的双轨语义。

---

## 3. 术语与对象边界

### 3.1 业务对象

| 对象 | 职责 | 说明 |
|------|------|------|
| `WorkLine` | 编排边界 | 一条独立运行的作业线实例 |
| `Device` | 设备静态主数据 | 描述设备归属、角色、能力、拓扑关系 |
| `WorklineSession` | 业务运行实例 | 一次完整业务处理过程的内部状态容器 |
| `WorklineTimeline` | 业务时间线 | 记录编排推进、决策、等待、终态 |
| `DeviceCommand` | 单条设备命令记录 | 记录控制流中的一条设备动作 |
| `RuntimeInbox` | 统一编排入口 | 接住所有待编排输入 |
| `SystemOutbox` | 统一副作用出口 | 接住所有待派发动作 |

### 3.2 三类 ID 语义

| 字段 | 类型 | 职责 |
|------|------|------|
| `correlation_id` | 追溯流 | 串联整条业务链路，用于跨事件、跨命令、跨回调追溯 |
| `command_code` | 控制流 | 命中某一条设备命令生命周期，用于结果回传归属 |
| `session_id` | 内部运行态 | 命中当前 `WorklineSession`，用于恢复/续跑会话 |

硬约束：

1. `correlation_id` 不是 HTTP 请求 ID
2. `callback/result` 的第一归属键是 `command_code`
3. `session_id` 不承担对外控制语义，只承担内部恢复语义

### 3.3 术语对照表

| 术语 | 所属层次 | 用途 | 备注 |
|------|----------|------|------|
| `request_id` | 来源系统 | 来源消息唯一标识 | 进入 WES 后映射为 `source_message_id` |
| `source_message_id` | 接入层 | 来源侧幂等、来源侧追踪 | 不等于 `correlation_id` |
| `correlation_id` | 业务语义层 | 整条业务链追溯 | 追溯流主键 |
| `command_code` | 业务语义层 | 单条设备命令控制流归属 | 控制流主键 |
| `command_id` | 实现层 | 内部数据库外键 | 仅在持久化和关联查询中使用 |
| `session_id` | 运行时实现层 | 内部会话恢复 | 不对外承担控制语义 |

规则：

1. 对外接口、跨系统交互、业务时序说明，优先使用 `request_id`、`source_message_id`、`correlation_id`、`command_code`
2. 数据库表关联、内部 ORM 外键、实现细节说明，可以使用 `command_id`
3. 任何文档不得把 `request_id` 写成 `correlation_id`
4. 任何业务时序图不得把 `command_id` 当作控制流主键

---

## 4. 入口语义

### 4.1 `callback/event`

`callback/event` 表示某个事件事实进入 WES，通常是某一系列设备动作的起点或中途触发点。

接入层职责只包括：

1. 请求校验
2. 原始日志落库
3. ACK
4. 写 `RuntimeInbox`

规则：

1. 若该事件开启一条新的业务链，则生成新的 `correlation_id`
2. 若该事件明确属于现有业务链，则延续既有 `correlation_id`
3. 接入层不做插件业务决策
4. 接入层不直接推进 `Session`
5. 接入层不直接下发后续设备命令

### 4.2 `callback/result`

`callback/result` 表示某条设备命令的执行结果回流 WES。

规则：

1. `result` 不新开业务流
2. `result` 必须首先按 `command_code` 命中原命令
3. `result` 必须延续该命令所属链路的 `correlation_id`
4. `result` 写入 `RuntimeInbox` 后，由编排器决定是否存在下一步动作
5. 某个 `result` 后可以没有下一步动作，此时应落明确终态

### 4.3 Inbox 类型

`RuntimeInbox` 必须把以下输入建模为一等入口类型：

- `DEVICE_EVENT`
- `COMMAND_RESULT`
- `EXTERNAL_CALLBACK`
- `TIMEOUT`
- `MANUAL_OPERATION`

禁止把 `COMMAND_RESULT` 伪装成 `DEVICE_EVENT` 再让编排器二次猜测。

---

## 5. 编排职责边界

### 5.1 接入层

接入层只负责：

1. 接收
2. 校验
3. 原始落库
4. ACK
5. 写 Inbox

接入层不负责：

1. 直接执行业务决策
2. 直接调用插件推进流程
3. 直接修改 `WorklineSession`
4. 直接发设备命令

### 5.2 编排器

编排器负责：

1. 消费 `RuntimeInbox`
2. 解析 `device -> workline -> plugin`
3. 创建或恢复 `WorklineSession`
4. 调用插件做业务决策
5. 原子写入 `Session / Timeline / Decision / Outbox`

### 5.3 插件

插件只输出领域意图：

1. 状态推进意图
2. 上下文变更
3. 待派发设备动作
4. 待调用外部动作
5. 等待条件
6. 失败结论

插件不直接：

1. 写 Repository
2. 调 HTTP Client
3. 发设备命令
4. 拼装审计落库细节

### 5.4 OutboxDispatchService / Outbox

所有副作用必须经 `SystemOutbox`：

1. 事务内写 `Outbox`
2. 事务外派发
3. 设备结果或外部回调重新回流到 `RuntimeInbox`

---

## 6. 拓扑与位置建模

### 6.1 工作线拓扑

工作线配置以插件 manifest 声明的设备角色、数量和能力为事实源；`Device.upstream_device_id` 只作为物理路径辅助信息。

插件只依赖：

- 当前 `WorkLine`
- 当前工作线下的设备集合
- 设备角色
- 设备能力
- 物理路径辅助信息

### 6.2 位置 ID

位置 ID 可以作为设备协议中的 `source` / `target` 参数使用，但它只是运行载荷的一部分，不是工作线归属和拓扑推导的来源。

例如：

- `STATION_INPUT1`
- `STATION_PIPELINE1_OUTPUT1`
- `STATION_NG_PLATFORM1`

这些命名可以保留为设备接口约定，但不能升级为“运行时按前缀分线”的系统语义。

---

## 7. 业务流、数据流、事件流

### 7.1 业务流

1. 某设备或外部系统发起 `event`
2. WES ACK 并写 `RuntimeInbox`
3. 编排器定位工作线与插件
4. 插件根据当前上下文与拓扑做决策
5. 编排器写 `Outbox`
6. OutboxDispatchService 派发设备命令
7. 设备通过 `callback/result` 回流
8. 编排器继续推进或落终态

### 7.2 数据流

1. 静态主数据先完成配置：`WorkLine / Device / plugin_key / upstream_device_id`
2. 运行时输入进入 `RuntimeInbox`
3. 编排输出沉淀到 `WorklineSession / WorklineTimeline / SystemOutbox`
4. 控制流证据沉淀到 `DeviceCommand`
5. 整条业务链通过 `correlation_id` 串联

### 7.3 事件流

1. `event` 是动作链起点或中途触发点
2. `result` 是某条控制动作的执行回点
3. `timeout` 是等待超时后的系统触发事件
4. `manual_operation` 是人工恢复、跳过、重试等显式操作
5. 所有事件都必须回流到 Inbox，再进入同一编排主线

---

## 8. 用左侧粗分线时序图做验证

以 `../archive/legacy-smt-classifier/workline_topology_overview.md` 中历史左侧粗分线时序为语义对照，结论如下：

1. `SCAN_COMPLETED` 属于 `callback/event`
2. WES 接住事件后，应创建或恢复 `WorklineSession`
3. WES 下发 `PICK_AND_PUT`、`MOVE_FORWARD`、`PICK_AND_PUT` 时，每条命令都生成各自 `command_code`
4. 机械臂和流水线返回的 `COMMAND_RESULT` 都属于 `callback/result`
5. 这些 `result` 延续同一 `correlation_id`
6. 最后一条 `result` 后，若无下一步动作，则由编排器将 `Session` 落为 `COMPLETED`

因此，这张图可作为业务正确性样例，但不能被解读为“左侧/右侧是插件内部分支逻辑”。

---

## 9. 当前偏差与修订原则

当前仓库存在以下偏差，后续实现与文档修订必须以本文为准：

1. 文档目标已是单轨编排，部分代码仍是双轨处理
2. `correlation_id` 在部分入口代码中被错误退化成 `request_id`
3. `COMMAND_RESULT` 在部分实现中仍不是一等 Inbox 类型
4. 部分旧文档和旧插件代码仍残留“左右硬编码”和“位置前缀分线”思路

后续修订原则：

1. 先统一规范
2. 再统一文档
3. 最后统一实现

---

## 10. 后续执行顺序

1. 以本文为基准修订现有文档
2. 基于本文做一次 `plan-eng-review`
3. 通过评审后再进入代码整改
4. 代码整改完成后回写最终版正式规范
