# WES Phase 7 DeviceCommand/ECS 通用能力生产收敛计划

> **状态：ReviewRequired**
> 本文冻结 Phase 7 的职责、目标闭环、删除边界和验收门禁。开始代码实施前，仍须在当时最新分支上冻结引用图、逐文件
> successor/`NONE` 矩阵、最终模型、事务、worker、准确删除清单、精确验证命令和提交边界。

**Goal:** 在零业务插件核心上，独立交付与具体工作线、供应商和业务流程无关的 `DeviceCommand` 可靠生命周期、设备统一接口、
状态/事件/结果证据、`LineRunEpoch` fencing 和唯一生产装配，并删除旧 RuntimeIntentLog/SystemOutbox/device gateway 执行闭包。

**Design baseline:**
`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`

**Contract baselines:**

- `docs/architecture/device-command-contract.md`
- `docs/integration/third_party_integration_whitepaper.md`

**Master plan:**
`docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`

---

## 1. 阶段定位

`TransportTask` 与 `DeviceCommand` 是两个平行可靠对象：

| 对象 | 外部责任方 | 可靠性事实 | 业务消费者 |
| --- | --- | --- | --- |
| `TransportTask` | 当前由 WMS 转发 RCS/AGV/CTU | submit ACK、成员位置、异步 TransportResult、对账 | Phase 8/9 真实插件 |
| `DeviceCommand` | 供应商 ECS/网关 | 状态准入、同步 ACK、异步 CALLBACK、事件/结果证据 | Phase 8/9 真实插件 |

两者可以复用 Phase 2 Outbound HTTP 的单次发送和 Client 生命周期，但不得共享状态机、identity、重试策略、证据表、
Repository 或业务测试。Phase 7 技术上不消费 Phase 6 Transport 的运行结果，但实施调度仍须等待 Phase 6 退出门禁；其功能
输入只有 Phase 5 形成的零插件核心和 Phase 2 稳定传输。

Phase 7 结束时允许没有业务插件，也允许没有真实设备绑定。零绑定状态下只装配统一 Adapter 和固定 route；未绑定设备返回
`DEVICE_NOT_FOUND`，不得发送 outbound 请求或接纳设备事件。Phase 8 安装获批设备附录和 endpoint/device 绑定后，统一入口
才可接收该设备没有活动 Epoch 的诊断事件；这类事件不得创建业务执行对象或调用插件。

## 2. 权威与边界

| 事实或行为 | 唯一 owner | Phase 7 约束 |
| --- | --- | --- |
| 命令 identity、不可变 payload、deadline 和生命周期 | WES `DeviceCommand` | 不经 Intent/Effect/SystemOutbox 间接拥有 |
| Client、timeout、连接池、有界响应和单次发送 | Phase 2 Outbound HTTP | 不自动重试、不解释设备业务 |
| 固定路径、公共包络、ACK/CALLBACK 和错误语义 | 统一设备接口 | 不按供应商配置路径或字段别名 |
| 设备内部步骤、PLC、安全互锁和物理动作 | ECS/PLC | WES 不拆解长命令、不实现机械安全 |
| 具体 `task_type`/`event_type`/Payload 闭集 | 获批设备合同附录 | Phase 7 不创建空附录或全局枚举 |
| 业务对象、发送时机和 CALLBACK 后续 Decision | Phase 8/9 插件 | Phase 7 不拥有粗分、SMT、满箱或出库流程 |
| 业务来源、目标、优先级、路线、取消和恢复 | WMS | Phase 7 不本地改判 |

## 3. 固定统一接口

Phase 7 只实现白皮书已批准的四个固定入口：

- `POST /api/v1/device/command`；
- `GET /api/v1/device/status?device_code={device_code}`；
- `POST /api/v1/callback/result`；
- `POST /api/v1/callback/event`。

固定路径、公共包络、`device_code`、`command_code`、`source_event_id`、`contract_key`、`contract_version` 和错误语义不可按
供应商覆盖。当前纯局域网合同不要求应用层 Token、HMAC、Nonce、Clock 或 credential seam；未来真实合同若变化，只允许在
最窄 owner 上另行评审，不预留三选一认证框架。

## 4. 最小可靠闭环

```text
插件或显式调用方请求创建命令
        ↓
冻结 device_code / command_code / Epoch / contract / payload digest / deadline
        ↓
事务内持久化 DeviceCommand
        ↓
查询该 device_code 的实时状态
        ↓
仅 AUTO + IDLE + 无活动命令 + 合同/Epoch 匹配时发送
        ↓
同步 ACK：只记录是否接纳
        ↓
异步 CALLBACK：先保存证据并 ACK
        ↓
后台锁定命令，校验 identity / digest / Epoch / 唯一终态
        ↓
形成 SUCCEEDED / FAILED / TIMED_OUT / 人工对账
        ↓
有真实插件时发布类型化结果；零插件时不存在业务命令生产者
```

Phase 7 只实现一个可靠命令聚合，不建立通用 Outbox、Effect engine、workflow engine 或任意 dispatch registry。

## 5. 状态、identity 与重提

### 5.1 单设备活动命令

- `device_code` 是可独立下发命令和独立判断忙闲的资源 identity；同一 Endpoint 可服务多个 `device_code`。
- 每个 `device_code` 最多一个已接纳且未终态命令；数据库约束和事务准入共同保证，不能只靠进程锁。
- 新命令发送前必须得到未过期的实时状态，并精确满足 `mode=AUTO`、`status=IDLE`、
  `current_command_code=null`、合同身份与活动 Epoch 一致。
- `current_command_code` 只用于诊断和对账，不能证明旧命令未执行或已经成功。

### 5.2 稳定 identity

- `command_code` 一经创建永久绑定同一规范化 payload digest，包括明确拒绝的尝试。
- 相同 identity、相同 payload 的重复接纳返回首次结果，不重复触发物理动作。
- 相同 identity、不同 payload 是冲突，保存证据并失败关闭。
- 结果和事件使用部署级唯一、永久不复用的 `source_event_id`；同一 identity 的相同 payload 重传返回首次 ACK，冲突不得推进。

### 5.3 安全重提

只有请求可证明未离开 WES，或 ECS 明确返回白皮书定义的“未接纳”响应时，才允许使用相同 `command_code` 和相同 payload
有界重提。delivery unknown、已 ACK、identity 冲突或结果可能已送达时禁止换 identity 自动重放；必须等待匹配 CALLBACK、
收集状态证据并在窗口结束后进入人工对账。

本阶段不提供通用优先级队列。业务优先级由 WMS 结果给出；插件只把该结果映射为命令就绪或等待，Phase 7 不解释、改写或
重排业务优先级，只按明确、稳定的领取顺序处理已持久化命令。

## 6. CALLBACK 与 Epoch fencing

### 6.1 结果回调

1. 在解码和 DTO 校验前执行原始 body 上限检查；
2. 按 `command_code` 找到原命令并核验 `device_code`、`contract_key` 和 `contract_version`；
3. 按获批附录校验 callback DTO，并对 callback 语义载荷单独计算 evidence digest；不得比较命令 `params` 与结果 `data`；
4. 原子保存 `source_event_id`、evidence digest、原始证据和命令/Epoch 关联；
5. 持久化成功后同步返回 ACK；
6. 后台锁命令并应用唯一终态；重复不二次推进，冲突只保存诊断证据。

未知命令不建立已接纳结果证据；不同 `source_event_id`、不同摘要或矛盾终态不得覆盖首次已接纳终态。

### 6.2 设备事件

- 首次观察事件时原子冻结活动 `line_run_epoch_id`；没有活动 Epoch 时保存 `null`。
- `line_run_epoch_id=null` 的事件是诊断证据，持久化后 ACK，但不调用插件。
- 旧 Epoch 的迟到或重传事件不得绑定当前 Epoch；活动 Epoch 内合同、设备绑定、配置或拓扑发生变化必须创建新 Epoch。
- 只有仍绑定活动 Epoch、合同一致且具有真实插件 consumer 的事件才能异步交给插件。

## 7. 目标内部职责

Phase 7 允许的生产职责严格限定为：

- `DeviceCommand` 聚合和最小状态枚举；
- 命令、状态观察、结果/事件 evidence 的 Repository；
- 创建、领取、发送、记录 ACK、应用 CALLBACK、超期和发布结果的窄 Service；
- 统一 ECS outbound Adapter 和两个 inbound Handler；
- 显式 Composition Root、固定 API route 和有界后台 worker；
- 只读诊断查询所需的最小投影。

命名和文件结构由详细实施设计冻结，但不得出现动态 Provider、Service Locator、插件扫描、供应商 Adapter registry、通用
Intent/Effect、通用 SystemOutbox 或第二套命令生命周期。

## 8. 旧 owner 处置

Phase 7 启动时重新扫描并覆盖：

- `src/app/device/` 下旧 `DeviceCommandService`、模型、Repository 和调用者；
- `RuntimeIntentLog`、`SystemOutbox`、`DEVICE_COMMAND` dispatch 和 repair helper；
- runtime device gateway、SystemCapability device command write 和旧 callback bridge；
- 可配置 command/status/callback path、旧 `event_id`、`priority`/`timeout` wire、`current_command_id` 别名；
- 裸 `httpx.AsyncClient`、私有认证、Provider Profile 和设备专属配置；
- API、Celery、beat、worker、部署、诊断和测试 owner。

允许的处置只有 `DELETE → final DeviceCommand successor`、`DELETE → NONE` 或 `RETAIN`。禁止搬迁旧源码、保留 re-export、
兼容 DTO、fallback、双写、双读或按设备切分新旧路径。

## 9. 工作包

### Task 1：冻结引用图、模型和事务不变量

- 生成逐文件 successor/`NONE` 矩阵，区分 DeviceCommand、设备状态、事件证据、结果证据、插件业务和供应商私有协议。
- 冻结唯一 identity、不可变字段、状态转换、数据库唯一约束、claim/lease、deadline 和人工对账原因闭集。
- 明确每个写事务的 owner，禁止 Service 跨层直接访问数据库或由 API 调 Repository。

### Task 2：按 TDD 交付核心命令可靠性

- 先建立命令创建、单设备互斥、稳定 payload、claim/lease、明确未接纳、delivery unknown、ACK、deadline 和唯一终态测试。
- 再实现最小模型、Repository 和 Service；不接 API、不接 Celery、不删除旧 owner。

### Task 3：按 TDD 交付统一 ECS Adapter 和固定 wire

- 先建立四个固定路径、公共包络、请求上限、状态准入、错误分类和 Client 生命周期合同测试。
- 再实现统一 Adapter/Handler；供应商私有 DTO 和真实设备行为不进入核心测试。

### Task 4：按 TDD 交付 evidence、Epoch fencing 和有界 worker

- 先建立 ACK-after-persist、部署级唯一 `source_event_id`、重复、冲突、未知、迟到、旧 Epoch 和零 Epoch 事件测试。
- 再实现证据 Repository、应用 Service、超期/发布 worker 和诊断投影。

### Task 5：原子生产收敛并删除旧 owner

- 先验证新的核心、wire 和 PostgreSQL owner；
- 再一次性切换 Composition Root、API、Celery 和部署配置；
- 随后删除旧 DeviceCommand/SystemOutbox/gateway/callback/config/schema/tests；
- 最后运行 FAST、QUALITY、精确 HEAVY、运行态 smoke 和旧 owner 缺席门禁。

## 10. 测试所有权

| 验收面 | 唯一 owner |
| --- | --- |
| `DeviceCommand` 状态机、身份、deadline、互斥、claim、ACK/CALLBACK 和 fencing | 核心 `tests/` |
| 固定路径、公共包络、DTO、状态准入、重复/冲突和错误语义 | 核心统一接口合同测试 |
| PostgreSQL 唯一约束、claim/lease、事务和并发 | 核心显式 integration |
| 真实 `task_type`/`event_type`、设备时限和 ECS 行为 | 供应商一致性验收 |
| 何时创建命令及结果后如何推进业务对象 | Phase 8/9 插件包 |
| TransportTask 和 WMS/RCS 搬运 | Phase 6 Transport 测试 |

核心不得用粗分、SMT、满箱、出库或供应商名称证明基础能力；供应商验收和插件测试也不得替代核心可靠性。

## 11. 退出门禁

1. 生产只存在一个 `DeviceCommand` 聚合、一个统一 ECS Adapter 和一组固定 callback Handler；
2. 每个 `device_code` 最多一个已接纳未终态命令，状态与 Epoch 不可信时失败关闭；
3. ACK 只记录接纳，只有匹配 CALLBACK 可以形成物理终态；
4. 相同 identity 幂等，冲突、未知、迟到和 delivery unknown 不自动重放或推进；
5. 零 Epoch 事件可诊断但不调用插件，旧 Epoch 证据不重绑；
6. 旧 RuntimeIntentLog/SystemOutbox/device gateway、可配置路径、旧 identity/compat 字段、私有认证和裸 Client 零生产引用；
7. 核心、供应商一致性和插件测试所有权不重叠；
8. FAST、QUALITY、精确 HEAVY、Ruff、类型、Import Linter、Bandit、运行态 smoke 和缺席门禁全部通过；
9. 系统可处于零业务插件、零设备绑定状态；未绑定设备返回 `DEVICE_NOT_FOUND`，不发送 outbound 请求或接纳设备事件；
10. Phase 8 只通过显式应用端口消费本阶段能力，并负责安装真实设备附录和 endpoint/device 绑定。

## 12. 明确不在范围内（NOT in scope）

- 粗分、SMT、自动/人工分拣、满箱交换、复杂出库或 PickingTask 业务；
- 供应商原始 DTO、私有路径、私有认证、协议转换和 PLC/安全控制；
- WES 全局 `task_type`/`event_type` 枚举、空设备附录或供应商模板；
- TransportTask、WMS/RCS 搬运、库存、主数据和 WMS 业务裁决；
- 动态 Provider、Service Locator、Manifest、插件 registry、通用 Intent/Effect/SystemOutbox 或工作流引擎；
- MQTT、OPC UA、WebSocket 等未批准协议；
- 旧数据迁移、兼容字段、桥接表、回填、downgrade 和双路径；
- 修改或清理 `docs/hardware/` 厂商原始资料。

## 13. 失败模式与控制

| 失败模式 | 结果 | 控制 |
| --- | --- | --- |
| 把旧 `DeviceCommandService` 当目标模板 | RuntimeIntentLog/SystemOutbox 耦合继续存在 | 从批准合同和最终不变量重建，旧代码只作删除清单证据 |
| ACK 被当作物理完成 | 对象提前推进，仓储事实错误 | 状态机和测试强制 ACK/CALLBACK 分离 |
| timeout 后换 identity 自动重发 | 同一设备重复执行物理动作 | delivery unknown 禁止重放，进入回调等待和人工对账 |
| 状态缓存或合同不匹配仍发送 | 命令发给错误配置/旧固件 | 每次准入读取新鲜状态并核验 Epoch 合同身份 |
| 事件重传绑定新 Epoch | 旧现场事实污染当前运行代际 | 首次观察时冻结可空 Epoch，后续不可变 |
| 供应商差异进入核心分支 | 新增第二套路径和不可维护兼容层 | 差异只进入设备附录或供应商 ECS/网关 |
| 用插件 E2E 代替核心可靠性 | 基础能力缺陷被业务 happy path 掩盖 | 分层测试 owner 和缺席门禁 |

## 14. 评审结论

Phase 7 是正式独立基础阶段，既不前置到 Phase 4/5，也不埋入粗分插件。它只交付稳定设备命令和统一 wire；供应商附录及
业务推进留给 Phase 8/9。该边界可以在零插件状态独立验收，也避免每个插件重复建设 HTTP、幂等、ACK/CALLBACK 和重试逻辑。

当前文档已冻结架构目标，但引用图、逐文件 successor/`NONE` 矩阵、最终模型字段、事务和 worker 清单、准确删除范围、
精确验证命令及提交边界仍须在实施分支上冻结，因此保持 `ReviewRequired`。
