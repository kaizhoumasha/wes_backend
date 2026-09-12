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

1. 每个 `DeviceCommand` 都是独立可靠义务；WES 在创建时冻结目标设备、执行身份、合同和不可变载荷，派发只依据这些冻结事实
   及该命令自身的可派发状态。ECS Status 和 WES preflight 只提供现场诊断快照，不作为 Command create/dispatch admission；
   同设备实时互斥、运行态与设备安全许可由 ECS 在接纳 Command 时原子裁决。
2. 在任何外部调用前持久化 `DeviceCommand` 及其幂等、关联和截止时间事实。
3. 同步 ACK 只表示设备接纳，不表示物理动作完成。
4. 只有匹配原业务命令、WorkLine 及具体执行关联 的最终 CALLBACK 才能推进物理位置和具体执行对象；`MANUAL_DEBUG` 和
   `EVENT_DEBUG` CALLBACK 只闭合命令与 evidence，不进入业务 Decision。
5. 每条合同有效 RESULT 使用 `RESULT:<完整规范化报文摘要>` 作为消息身份，与 `command_code` 关联身份分离。精确重放保持幂等，
   内容不同的消息使用不同身份独立可靠留存；只有首个合同有效的关联终态结果闭合命令，后续结果不得覆盖既有终态或重复推进。
6. 合同有效但未知或尚未关联的 RESULT 可靠持久化且不推进业务对象；`PENDING` 命令先见匹配 RESULT 时，只将该命令转为
   `RECONCILING` 并记录 `RESULT_BEFORE_DISPATCH`，禁止该命令自身派发，不阻塞同设备的其它独立命令。
7. 稳定身份始终绑定同一规范化语义载荷，包括明确拒绝的尝试。只有请求可证明未离开 WES 或设备明确返回“未接纳”时才能
   安全重提：载荷不变沿用原身份，合同修正改变载荷摘要时使用新身份。结果可能已送达、已接纳、幂等冲突或 ACK 未知时禁止
   换身份或自动重放；等待匹配回调，状态查询只补充活动证据，仍无法闭合时进入人工对账。

生产装配在命令创建或合法设备 Evidence 接纳的事务中登记对应扫描唤醒，只在外层事务提交后发布；事务回滚不发布，
savepoint 回滚只丢弃该范围的唤醒。同一事务内相同扫描唤醒合并，消息不携带业务快照，worker 始终重读持久化事实。
命令派发和 Evidence 处理满批后继续唤醒扫描；唤醒发布失败只记录日志，由既有 Beat 扫描兜底，不改变已提交事实或接纳语义。

WES 不拆解供应商长命令，不解释 ECS 内部步骤，也不实现设备间安全互锁。

## 3. 内部模型边界

最终 `DeviceCommand` 只保存执行可靠性所需的内部事实：

- 稳定命令身份、目标设备，以及业务命令所属 WorkLine 和具体执行对象关联；
- 已按统一接口和设备合同附录验证的命令载荷（payload）不可变快照，包含 `contract_key` 和 `contract_version`；
- 载荷摘要（payload digest）、截止时间、下发尝试和最终结果证据；
- `PENDING / DISPATCHING / ACKNOWLEDGED / RECONCILING / SUCCEEDED / FAILED / TIMED_OUT` 通用生命周期；
- 关联（correlation）、追踪（trace）和诊断信息。

具体字段名以最终模型为准；不得为当前旧模型保留别名、转换层或兼容字段。

### 3.1 无业务联调例外

现场供应商联调只能由超级用户通过诊断 API 创建 `execution_ref_type="MANUAL_DEBUG"` 命令。该命令：

- 必须以 `client_request_id` 作为幂等身份，并直接指定 `device_code`；
- 不关联业务 WorkLine 绑定或 `MaterialExecution`；
- `POST /api/v1/device/commands/debug/preflight` 复用统一 ECS Adapter 枚举全部状态；创建接口接收
  `client_request_id`、`endpoint_base_url`、`device_code`、`timeout`、`task_type`、`params` 和审计 `reason`；
  WES 在命令记录中冻结规范化后的局域网 Endpoint、固定内部合同元数据、超时、`reason` 和 `created_by`；
- 仅将 `device_code`、`command_code`、`task_type`、固定 `priority=1`、`timeout`、Unix 毫秒 `timestamp`
  和 `params` 发送给 ECS；WES 合同元数据和 trace 不进入 ECS 包络；
- 幂等重放先查询既有 `client_request_id`，相同载荷、`reason` 和 `created_by` 直接返回且不访问 ECS；
- 复用相同的 Celery 扫描派发、统一 ECS wire、CALLBACK ingress、evidence 和 PostgreSQL 生命周期；preflight 只返回诊断快照，
  不作为 create/dispatch admission。派发只依据冻结身份、合同、载荷与命令自身状态，实时互斥和安全许可由 ECS 原子接纳裁决；
- 只能通过查询接口观察命令与规范化 CALLBACK，不触发 WorkLine、插件或业务对象推进。

这是一条受限的联调创建入口，不是供应商私有协议适配层。WES 仍只发送白皮书统一命令包络，供应商 ECS/网关负责内部协议转换。

ECS 还可以在 EVENT 顶层显式传入 `is_debug=true`，触发 `execution_ref_type="EVENT_DEBUG"` 的一次性联调命令。该路径：

- 先按普通 EVENT 持久化并独立返回 ACK，再由 evidence worker 异步创建命令；创建成功后 evidence 以 `IGNORED` 明确表示不进入
  WorkLine/业务 Decision，不表示联调命令失败；
- 使用 EVENT 内部稳定身份作为命令幂等身份，重复 EVENT 最多创建一条命令，正文漂移保持冲突；
- 新 EVENT 使用自己的 identity 创建独立命令，不等待同设备旧 `DeviceCommand` 终态；旧命令的 identity、payload、状态、对账原因、Evidence 和资源围栏保持不变；
- 联调目标由 `Settings.DEVICE_EVENT_DEBUG_ENDPOINT_BASE_URL` 指定，新建命令时校验并冻结；Docker 本机开发编排明确指向 ECS Mock，配置变化不改写旧命令。固定超时 `30000ms`，固定任务类型 `MOVE_FORWARD`，并将 EVENT `data`
  原样作为 `params`；
- 复用既有 DeviceCommand、统一 ECS Adapter、worker、CALLBACK 和 evidence；WES 不以本地 Status 或旧命令快照决定新独立命令能否执行，由 ECS 在接纳时裁决；
- 本次新建 `PENDING` 命令在事务提交后唤醒既有 DeviceCommand 派发扫描；唤醒失败不改写命令或 evidence，Beat 仍负责补偿扫描；
- 以 `ECS_EVENT_DEBUG:<event-identity>` 记录系统触发原因，`created_by=null`，不伪装为人工联调。

当 EVENT 关联的 WorkLine 存在未关闭的手工出库联调 Run 时，WES 仍持久接收 `is_debug=true` EVENT，但直接将 Evidence
标记为 `IGNORED`，不创建或唤醒 `EVENT_DEBUG` 指令。该手工编排隔离优先于显式 `is_debug=true` 和自动运输联调提升，避免
供应商直发 `MOVE_FORWARD` 与联调台按节点创建的 `MANUAL_DEBUG` 指令并行作用于同一工作线。

自动运输联调还可在启动时开启 `test_mode`，复用上述 EVENT_DEBUG 能力。开关默认关闭，启动时冻结到轮次配置，
连续多轮沿用启动配置；活动轮次结束后不再提升新事件。在活动 `test_mode` 轮次内，所有设备编码精确匹配
`STATION_SCAN` 加数字的 `SCAN_COMPLETED` 事件，即使 ECS 省略 `is_debug` 或传入 `false`，也按 debug 事件处理，
向该扫码点下发 `MOVE_FORWARD`。该范围包含已绑定正式工作线的扫码点，不限于本轮 `scan_device_codes`；被提升的事件
不进入 WorkLine/业务 Decision。其它事件保持原处理方式，ECS 显式 `is_debug=true` 的能力不受此开关影响。

首次接收时冻结有效 debug 标志，重复接收沿用原 Evidence 的有效标志，不因轮次结束或新轮次配置变化重新判定。
外部事件身份仍按原始规范化 EVENT 计算；开关不改变既有事件身份，不重放历史事件。自动提升与显式 debug 共用上述
创建锁、命令幂等、结果回调及单命令对账约束，不改写其它命令事实。

WES 不保存或查询 EVENT command blocker，不提供人工 reprocess、`reconcile-device-idle`、ECS 空闲探测或人工失败码。旧命令只能由匹配原 `command_code` 的权威 Result Callback 或既有对账事实闭合；新 EVENT 或新命令成功不会自动重放、失败化、释放或覆盖旧命令。

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
- Status 顶层只含 `devices` 数组，每项严格包含 `device` 元数据和 `state`；其中 `device`、`state` 与 `scenario` 全部只提供
  诊断快照。WES dispatch 不查询或依赖 Status，实时互斥、运行态与设备安全许可由 ECS 在原子接纳 Command 时裁决；
- Command/Result/Event 外部时间统一使用 Unix 毫秒；事件内部身份为
  `EVENT:{sha256(device_code + event_type + timestamp + is_debug + canonical data)}`，省略 `is_debug` 等同于 `false`；
- ECS 同步接纳应答与 WES CALLBACK 应答统一为整数 `code=200`、`message="ACK"`。

WES 提供超级用户内部诊断接口 `GET /api/v1/device/evidences/history` 与 `GET /api/v1/device/evidences/stream`。
历史接口支持 `device_code`、`kind`、`command_code`、当前 `apply_status` 过滤及 `limit`（默认 20、范围 1–100）、不透明 `cursor`。
响应 `data.items` 每行包含 `row_key`、`recorded_at`、`attempt`、`latest_update`，并通过 `data.next_cursor` 翻页。
`next_cursor=null` 表示没有下一页；格式非法的游标返回 HTTP `400`，超过 1024 字符的游标及非法查询参数由 API 校验拒绝。
每次到达 callback route 的尝试复用 `CallbackLog`，固定类型 `device_ingress_attempt`、主体 `DEVICE_INGRESS`；
`request_body` 保存已脱敏的 `DeviceIngressAttempt` 诊断文档，其中 `raw_payload` 才是安全的原请求投影。重复、拒绝、冲突和失败同样记录，
日志使用独立短会话，提交后再发布实时事件。诊断存储失败会记录异常并继续发布 SSE，不改变既有 callback ACK 或已落盘 Evidence 语义。
数据库不可用期间的尝试不能宣称已可靠留存；非法 JSON、超限 body 不保存原始字节。设备入口之前被代理或中间件拒绝的流量不属于该 route 的日志。

历史回读按记录时间倒序，再按来源类型和稳定主键分页；`attempt` 保留当时接收状态，`latest_update` 关联当前 Evidence 状态。
无关联 Evidence 时 `latest_update=null`，`apply_status` 过滤使用 attempt 当时状态。没有对应 attempt 的既有
DEVICE_EVENT/DEVICE_RESULT Evidence 以真实 `received_at` 展示，`attempt=null`，不伪造历史 HTTP 请求或 ACK；尚未处理时
`latest_update.processed_at=null`。行身份为 `attempt:{request_id}` 或 `evidence:{evidence_id}`，用于页面历史与实时去重。
SSE 仍使用专用 Redis Pub/Sub 频道、live-only、无 replay，展示 callback 尝试及 Evidence 更新；前端接入时应在首次进入和重连后回读历史补齐，
Redis 缓慢或不可用不得改变 callback ACK 或业务推进语义。

`contract_key`、`contract_version` 和 `source_event_id` 是 WES 内部治理与幂等字段，不要求 ECS 传输。顶层协议不提供 Cancel，
白皮书旧版自动重试策略不恢复。

WES 接收 ECS 状态、命令响应及回调时，忽略协议顶层和已定义嵌套对象的冗余字段；已定义字段的必填、类型、枚举、关联及终态校验保持有效。冗余字段不进入规范化业务模型或幂等摘要，诊断仍保留脱敏原文。设备事件/结果的 `data` 是合同声明的业务载荷，仍完整交给所属设备能力解释，不把其中尚未消费的业务事实当作包络冗余字段删除。

每个实际设备的获批合同附录只定义：

- 该设备支持的 `task_type`、`event_type`；
- 对应 `params`、`data` 和 `error_detail` 的已定义字段与必要校验；
- 设备能力、完成时限、状态最大观察年龄、时间来源与允许偏差和人工对账窗口；
- 正常、失败、重复、冲突、乱序和恢复验收场景。

`docs/hardware/` 原样保留供应商提供的协议与联调资料。供应商或其网关负责把内部协议收敛为 WES 统一接口；WES 不
反向改写供应商原文，也不把厂商资料提升为核心架构真源。

生产组合根（Composition Root）只装配统一设备服务端点（Endpoint）、超时和共享传输端口（Transport），不按供应商选择协议实现。当前目标协议不要求
应用层 Token、签名、Nonce 或 HMAC；纯局域网隔离和访问控制由部署边界负责。

## 5. WorkLine 执行边界

具体工作线插件只决定：

- 当前有效 WMS 业务结果和执行证据是否允许创建命令；
- 命令关联哪个 `MaterialExecution`、任务或插件当前工位动作；
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
