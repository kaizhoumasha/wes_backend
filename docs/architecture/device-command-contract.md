---
status: Approved
created_at: 2026-06-25
updated_at: 2026-08-25
spec: docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
wire_authority: docs/integration/third_party_integration_whitepaper.md
scope: WES 核心设备命令基础能力边界
related:
  - docs/hardware/SMT流水线接口调用说明书20260320-v1.pdf
  - docs/contracts/wms-rough-sorter-inbound-integration-requirements.md
  - docs/contracts/wms-inbound-putaway-integration-requirements.md
---

# DeviceCommand 核心边界合同

## 1. 文档定位

本文只定义 WES 核心共享的设备命令可靠性边界。本轮 SMT 联调冻结 Command、Status、Result、Event 四个 wire；
`SMT流水线接口调用说明书20260320-v1.pdf` 只提供设备、动作和状态枚举输入，不改变已冻结 wire 字段。

能力所有权严格分为四层：

| 层级 | 所有内容 | 不得包含 |
| --- | --- | --- |
| WES 核心 | 命令持久化、幂等身份、目标设备、截止时间（deadline）、ACK/CALLBACK 证据、通用状态与诊断 | 供应商私有路径、字段别名、具体工作线规则 |
| 出站 HTTP 基础层（Outbound HTTP） | 客户端（Client）生命周期、超时（timeout）、单次发送、有界响应和传输事实分类 | 设备业务参数、自动重试、命令生命周期和工作线判定 |
| 设备统一接口层（uniform device wire） | 白皮书规定的固定路径、公共数据传输对象（DTO）、身份、ACK/CALLBACK 和错误语义 | 工作线业务、PLC 控制、供应商私有兼容分支 |
| WorkLine 插件（WorkLine plugin） | 将 WMS 封闭业务结果映射为命令创建时机、具体执行对象、已授权逻辑目标和下一步执行决定（Decision） | HTTP、Repository、重试、设备安全互锁、业务来源/目标/路线裁决 |

所有供应商必须适配 WES 的统一接口（wire）。具体设备差异只能写入获批设备合同附录中的 `task_type`、`event_type`、
`params`、`data` 和错误详情，不能通过 WES 内的供应商私有适配器（Adapter）、兼容字段或动态分派实现。

## 2. 核心命令闭环

核心只保证以下不变量：

1. 每个独立命令资源 `device_code` 最多存在一个已接纳且未终态的命令；业务命令下发前确认目标状态条目身份一致、
   `is_online=true`、`mode=AUTO`、`status=IDLE`、无活动命令，且 `updated_at` 未超过冻结的允许年龄。合同身份只由 WES
   命令与活动 `LineRunEpoch` binding 冻结，不要求 Status 返回。诊断命令在实际发送前查询目标 ECS Status，并额外要求
   `task_type` 位于非空 `supported_commands`；`MANUAL_DEBUG` 创建前还执行同样的预检，任一准入失败都不得发送 Command。
2. 在任何外部调用前持久化 `DeviceCommand` 及其幂等、关联和截止时间事实。
3. 同步 ACK 只表示设备接纳，不表示物理动作完成。
4. 只有匹配当前业务命令及其冻结 `LineRunEpoch` 的最终 CALLBACK 才能推进物理位置和具体执行对象；`MANUAL_DEBUG` 和
   `EVENT_DEBUG` CALLBACK 只闭合命令与 evidence，不进入业务 Decision。
5. `command_code` 最多绑定一个已接纳终态结果；WES 内部使用 `RESULT:{command_code}` 作为结果身份。重复 CALLBACK 不重复推进，
   同一身份对应不同载荷时拒绝并保留冲突证据。
6. 未知、乱序或无法关联的结果拒绝接纳且不推进当前对象；`PENDING` 命令收到 RESULT 时必须先进入
   `RECONCILING` 并占住设备槽，确保 worker 不会继续下发物理动作。
7. 稳定身份始终绑定同一规范化语义载荷，包括明确拒绝的尝试。只有请求可证明未离开 WES 或设备明确返回“未接纳”时才能
   安全重提：载荷不变沿用原身份，合同修正改变载荷摘要时使用新身份。结果可能已送达、已接纳、幂等冲突或 ACK 未知时禁止
   换身份或自动重放；等待匹配回调，状态查询只补充活动证据，仍无法闭合时进入人工对账。

WES 不拆解供应商长命令，不解释 ECS 内部步骤，也不实现设备间安全互锁。

## 3. 内部模型边界

最终 `DeviceCommand` 只保存执行可靠性所需的内部事实：

- 稳定命令身份、目标设备，以及业务命令冻结的 `LineRunEpoch` 和当前具体执行对象关联；
- 已按统一接口和设备合同附录验证的命令载荷（payload）不可变快照，包含 `contract_key` 和 `contract_version`；
- 载荷摘要（payload digest）、截止时间、下发尝试和最终结果证据；
- `PENDING / DISPATCHING / ACKNOWLEDGED / RECONCILING / SUCCEEDED / FAILED / TIMED_OUT` 通用生命周期；
- 关联（correlation）、追踪（trace）和诊断信息。

具体字段名以最终模型为准；不得为当前旧模型保留别名、转换层或兼容字段。

### 3.1 无业务联调例外

现场供应商联调只能由超级用户通过诊断 API 创建 `execution_ref_type="MANUAL_DEBUG"` 命令。该命令：

- 必须以 `client_request_id` 作为幂等身份，并直接指定 `device_code`；
- 不关联 `LineRunEpoch`、设备 binding 或 `MaterialExecution`；
- `POST /api/v1/device/commands/debug/preflight` 复用统一 ECS Adapter 枚举全部状态；创建接口接收
  `client_request_id`、`endpoint_base_url`、`device_code`、`timeout`、`task_type`、`params` 和审计 `reason`；
  WES 在命令记录中冻结规范化后的局域网 Endpoint、固定内部合同元数据、超时、`reason` 和 `created_by`；
- 仅将 `device_code`、`command_code`、`task_type`、固定 `priority=1`、`timeout`、Unix 毫秒 `timestamp`
  和 `params` 发送给 ECS；WES 合同元数据和 trace 不进入 ECS 包络；
- 幂等重放先查询既有 `client_request_id`，相同载荷、`reason` 和 `created_by` 直接返回且不访问 ECS；
- 复用相同的 Celery 扫描派发、统一 ECS wire、CALLBACK ingress、evidence 和 PostgreSQL 生命周期，并在发送前再次执行运行态准入；
- 只能通过查询接口观察命令与规范化 CALLBACK，不触发 WorkLine、插件或业务对象推进。

这是一条受限的联调创建入口，不是供应商私有协议适配层。WES 仍只发送白皮书统一命令包络，供应商 ECS/网关负责内部协议转换。

ECS 还可以在 EVENT 顶层显式传入 `is_debug=true`，触发 `execution_ref_type="EVENT_DEBUG"` 的一次性联调命令。该路径：

- 先按普通 EVENT 持久化并独立返回 ACK，再由 evidence worker 异步创建命令；创建成功后 evidence 以 `IGNORED` 明确表示不进入
  WorkLine/业务 Decision，不表示联调命令失败；
- 使用 EVENT 内部稳定身份作为命令幂等身份，重复 EVENT 最多创建一条命令；
- 同设备已有未终态 `DeviceCommand` 时，不创建失败占位命令，不访问 ECS；evidence 进入 `RECONCILING`，并持久化指向旧命令的 blocker 因果事实；
- 联调期间目标固定为 `http://10.24.209.26:8080/`，固定超时 `30000ms`，固定任务类型 `MOVE_FORWARD`，并将 EVENT `data`
  原样作为 `params`；
- 复用既有 DeviceCommand、统一 ECS Adapter、worker、运行态准入、CALLBACK 和 evidence；Status 未声明支持
  `MOVE_FORWARD`、设备不在线、非 `AUTO / IDLE`、存在 `current_command_code` 或其它当前准入失败时，已创建的联调命令直接闭合为失败，不排队等待设备后续可用；
- 本次新建 `PENDING` 命令在事务提交后唤醒既有 DeviceCommand 派发扫描；唤醒失败不改写命令或 evidence，Beat 仍负责补偿扫描；
- 以 `ECS_EVENT_DEBUG:<event-identity>` 记录系统触发原因，`created_by=null`，不伪装为人工联调。

blocker 查询返回检测时的旧命令状态与对账原因、当前命令状态和不可变 `block_id`。匹配原 `command_code` 的 Result Callback 仍是闭合旧命令的首选路径。只有 blocker 指向、仍为 `RECONCILING / DELIVERY_UNKNOWN`、且冻结 binding 能提供状态新鲜度合同的业务命令，才允许超级用户在实时证明设备在线、`AUTO / IDLE`、无当前命令且状态未过期后，将旧命令闭合为 `FAILED / MANUAL_RECONCILIATION_DEVICE_IDLE`。该操作不伪造 Result 或成功终态；已接纳但尚未应用的 Result 优先，必须拒绝人工闭合。未冻结状态新鲜度合同的诊断命令只能由 Result Callback 闭合。

旧命令终态不会自动重放 EVENT。超级用户只能携带 GET blocker 返回的当前 `block_id` 显式重处理；锁内确认该 blocker 仍是 latest `BLOCKED`、旧命令已终态且设备没有其它未终态命令后，才可将原 evidence 重置为 `PENDING`。重处理不改写 EVENT 身份、载荷、摘要或 Epoch 绑定；旧 `block_id` 不得作用于后续新 blocker。人工闭合和重处理的状态变化与审计必须同事务成功或回滚。

ACK 与命令创建属于两个异步执行路径，WES 不承诺 ECS 在 worker 启动前已经读取到 ACK 字节。

## 4. 统一接口与设备附录边界

本轮冻结的顶层白皮书 wire 定义：

- `POST /api/v1/device/command`；
- `GET /api/v1/device/status?device_code={device_code}`；不传 Query 时返回当前 ECS 的全部设备；
- `POST /api/v1/callback/result`；
- `POST /api/v1/callback/event`；
- Command 顶层只含 `device_code`、`command_code`、`task_type`、`priority`、`timeout`、`timestamp`、`params`；
- Result 顶层只含 `command_code`、`device_code`、`result`、`finish_time`、`data`、`error_detail`；
- Event 顶层公共字段为 `device_code`、`event_type`、`timestamp`、可选严格布尔值 `is_debug` 和 `data`，设备专属业务字段由合同
  附录约束；
- Status 顶层只含 `devices` 数组，每项严格包含 `device` 元数据和 `state`；正常派发只使用 `state` 的身份、在线、模式、
  状态、活动命令和更新时间字段，元数据与 `scenario` 仅作诊断；
- Command/Result/Event 外部时间统一使用 Unix 毫秒；事件内部身份为
  `EVENT:{sha256(device_code + event_type + timestamp + is_debug + canonical data)}`，省略 `is_debug` 等同于 `false`；
- ECS 同步接纳应答与 WES CALLBACK 应答统一为整数 `code=200`、`message="ACK"`。

WES 另提供超级用户内部诊断接口 `GET /api/v1/device/evidences/stream`。它通过专用 Redis Pub/Sub 频道实时展示活动期间的
Result/Event HTTP 尝试及 evidence `APPLIED/RECONCILING` 更新，不持久化、不重放；发布采用有界 best-effort，Redis 缓慢或
不可用均不得改变 callback ACK 或业务推进语义。

`contract_key`、`contract_version` 和 `source_event_id` 是 WES 内部治理与幂等字段，不要求 ECS 传输。顶层协议不提供 Cancel，
白皮书旧版自动重试策略不恢复。

每个实际设备的获批合同附录只定义：

- 该设备支持的 `task_type`、`event_type`；
- 对应 `params`、`data` 和 `error_detail` 的字段闭集；
- 设备能力、完成时限、状态最大观察年龄、时间来源与允许偏差和人工对账窗口；
- 正常、失败、重复、冲突、乱序和恢复验收场景。

`docs/hardware/` 原样保留供应商提供的协议与联调资料。供应商或其网关负责把内部协议收敛为 WES 统一接口；WES 不
反向改写供应商原文，也不把厂商资料提升为核心架构真源。

生产组合根（Composition Root）只装配统一设备服务端点（Endpoint）、超时和共享传输端口（Transport），不按供应商选择协议实现。当前目标协议不要求
应用层 Token、签名、Nonce 或 HMAC；纯局域网隔离和访问控制由部署边界负责。

## 5. WorkLine 执行边界

具体工作线插件只决定：

- 当前有效 WMS 业务结果和执行证据是否允许创建命令；
- 命令关联哪个 `MaterialExecution`、`BinExecution` 或其他具体对象；
- 已批准 `task_type` 需要哪些逻辑业务参数；
- CALLBACK 后如何按 WMS 结果返回下一条命令、结束、NG 执行或对象级暂停中的封闭执行决定。

来源、目标、优先级、业务路线、业务异常分类、替代来源、取消、恢复和业务终态由 WMS 给出；插件根据该结果和设备证据
决定等待、发送、暂停、物理 NG 隔离或对账。插件发现结果缺失、过期、矛盾或物理不可执行时必须失败关闭，不得选择另一
个业务方案。

粗分机、自动分拣、人工分拣、满箱交换等流程不得写入核心合同或核心测试。

## 6. 禁止能力

WES 核心、统一接口层和插件都不得建立以下软件控制字段或抽象：

- PLC 点位、物理坐标、关节角度、速度曲线；
- 安全回路、急停复位或运动控制；
- WES 核心全局 `task_type` 或 `event_type` 枚举；
- 运行时工作流 DSL、动态插件发现或通用命令解释器；
- 供应商私有路径、认证、DTO 分支或 Adapter 注册表；
- 为旧 `task_type`、旧载荷或旧回调字段保留的兼容入口。

这些物理控制和安全事实由 ECS/现场安全系统拥有，WES 只消费其状态、ACK、CALLBACK 和事件证据。

## 7. 测试所有权

| 测试范围 | 唯一所有者 |
| --- | --- |
| `DeviceCommand` 通用生命周期、幂等、关联、截止时间、证据和禁止硬件控制字段 | 核心 `tests/` |
| HTTP 单次发送、Client 生命周期、有界响应和基础传输错误分类 | 核心 `tests/` |
| 固定路径、公共包络、身份、重复和冲突语义 | 核心统一接口合同测试 |
| 供应商实现是否符合白皮书与设备合同附录 | 供应商一致性验收，不进入核心业务测试 |
| 具体工作线何时创建命令及 CALLBACK 后业务推进 | 对应 WorkLine 插件包 |

核心测试不得使用具体供应商或工作线场景证明基础能力；供应商一致性验收和插件测试也不得替代核心可靠性测试。
