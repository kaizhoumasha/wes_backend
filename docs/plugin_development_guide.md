# 工作线（WorkLine）执行插件二次开发指南

> 本指南只回答一件事：如何根据工作线物理情况、获批设备合同附录和 WMS 业务合同，开发一套可部署、可测试的
> WorkLine 执行插件（WorkLine plugin）。
>
> 架构原理以[WES 最小执行架构收敛设计][architecture-spec]为准；设备通信以
> [WES 第三方设备统一接口白皮书][device-wire]为准。
>
> 本指南版本：2.1；引用的设备统一接口（wire）始终以白皮书当前批准版本为准。本文是目标态指导蓝本
> （normative target blueprint），不以当前代码是否已经提供全部 SDK 或目录为生效条件。实现与本文不一致时，应由后续实施
> 向本文对齐，不得反向保留旧接口、兼容层或双路径。

WorkLine 插件拥有业务结果到执行决定（Decision）的映射。供应商或其设备控制系统（ECS）直接实现 WES 统一接口
（wire）；插件不得读取供应商私有 JSON、实现 HTTP 通信或承担设备物理控制。

实现差异必须形成可追踪 TODO：已经纳入架构收敛总控计划的差异，直接补入对应 Phase 的交付项和验收标准；已有真实触发条件
但尚未排期的独立工作才写入项目根目录 `TODOS.md`。TODO 至少记录目标条款、当前差异、对齐阶段或依赖和验收标准，不在本文
复制当前旧实现，也不建立第二套目标真源。

## 1. 开发前必须准备

| 输入 | 必须明确的内容 |
| --- | --- |
| 工作线物理资料 | 设备、位置、缓存、队列、容量、物理连接、对象流向、故障隔离范围 |
| 设备合同附录 | `device_code`、`task_type`、`event_type`、`params`、`data`、结果、错误和时限 |
| WMS 业务合同 | 业务查询、来源和目标授权、人工任务、搬运目标、完成确认、业务拒绝 |
| 验收场景 | 正常流、等待、业务 NG、设备失败、WMS 不可用、重启清线、并发对象 |

资料必须能回答：

1. 哪个设备事件触发 WES。
2. 当前对象和目标位置在哪里。
3. 判定前需要读取哪些投影或 WMS 事实。
4. 判定后请求哪个已批准 `task_type`，以及需要哪些逻辑业务参数。
5. 哪个结果回调（result callback）表示物理动作最终完成。
6. 成功、NG、设备故障和依赖不可用分别如何继续。

设备合同附录未明确最终回调、命令关联、幂等行为或错误语义时，先补齐合同，不得在插件中猜测。

## 2. 按顺序开发

### 2.1 建立 WorkLine 物理模型

按现场实际划分 WorkLine，并整理：

- 入口、出口、工作位、缓存、队列、NG 去向和容量；
- 扫码器、输送设备、机械臂、检测设备等实际设备；
- 每台设备承担的稳定业务角色；
- 上下游连接、对象流向和 AGV/CTU 交接点；
- 单设备、当前对象或整线级故障隔离范围。

建议先形成以下清单：

| 清单 | 字段 |
| --- | --- |
| 位置 | 编码、角色、所属 WorkLine、容量、允许对象、上游、下游 |
| 设备 | 独立命令资源编码 `device_code`、设备类型、角色、所属 WorkLine、服务端点（Endpoint）、状态来源 |
| 连接 | 上游、下游、物理方向、触发事件、执行设备 |
| 故障 | 类型化错误、物理含义、影响对象、隔离范围、人工处理 |

设备内部步骤、坐标、机械互锁和安全逻辑属于 ECS/PLC。插件只把 WMS 已给出的业务结果映射为统一接口中的逻辑设备动作，
不选择业务来源、目标、路线或处置，也不拆分设备内部动作。

`device_code` 按可独立下发命令和判断忙闲的资源划分，不按 PLC 或 Endpoint 数量划分；同一 Endpoint 可以服务多个可并行的
`device_code`。

### 2.2 确认并绑定获批设备合同附录

设备合同附录由供应商、业务负责人、WES 架构负责人和项目交付负责人按白皮书共同批准。插件开发者不负责单方面“冻结”合同，
只消费已获批的附录，并确认以下最小合同矩阵已经闭合：

| 项目 | 记录内容 |
| --- | --- |
| Event | `contract_key`/`contract_version`、`event_type`、触发条件、`data`、部署级唯一 `source_event_id`、WES ACK |
| Command | `contract_key`/`contract_version`、`task_type`、`params`、同步 ACK、`AUTO + IDLE` 单设备单活动命令和原子拒绝 |
| Callback | `command_code`、原命令 `contract_key`/`contract_version`、部署级唯一 `source_event_id`、唯一终态、成功和失败 `data`、最终物理后置条件 |
| Status | 必填 `device_code` 查询参数、共享 `mode`/`status`、实际 `contract_key`/`contract_version`、状态最大观察年龄、禁止缓存、当前命令和错误详情 |
| Error | 标准语义、供应商原始证据、隔离范围、人工处理 |
| Idempotency | 稳定身份与规范化载荷不可变绑定、重复命令、重复回调、命令终态冲突和安全修正行为 |
| Timing | ACK 超时、预计完成时间、回调时间来源、时钟同步与允许偏差、人工对账窗口 |
| 版本绑定（version binding） | `contract_key`、附录版本、设备实例、ECS/网关或固件版本、配置版本和 `LineRunEpoch` |

必须区分：

```text
HTTP 请求送达
≠ ECS 已接纳 ACK
≠ 设备物理动作最终成功 CALLBACK
```

只有最终 CALLBACK 可以推进对象和位置。设备合同附录只能收窄白皮书中的可扩展字段，不能修改固定路径、公共包络
（envelope）、身份或 ACK/CALLBACK 语义。

附录中任何会改变 wire 行为的字段、结果、错误、时限、ECS/网关或固件变化都必须先批准新版本并重新执行一致性验收。
活动 `LineRunEpoch` 不得切换附录版本；必须停止新接纳，闭合或人工清理活动对象，再绑定新附录、配置和插件版本创建新 Epoch。

### 2.3 建立业务结果到执行动作映射表

每个真实业务场景先写决策表，再写处理器（Handler）：

| 字段 | 内容 |
| --- | --- |
| 触发 | 类型化设备事件、最终 CALLBACK 或 WMS 业务输入 |
| 当前事实 | 对象、位置、队列、目标设备状态 |
| WMS 业务结果 | 当前步骤的授权、来源、目标、优先级、路线或处置；必须是封闭结果 |
| 正常执行 Decision | 按 WMS 结果等待、创建设备命令、创建搬运、创建 WMS 确认或完成 |
| 成功后置条件 | CALLBACK 后更新的对象、位置、队列和设备投影 |
| 业务 NG | WMS 给出的 NG 原因和业务去向；插件不得本地改判 |
| 执行异常 | 设备错误、隔离范围、人工处理 |
| 依赖异常 | WMS 不可用时的暂停和待确认事实 |

### 2.4 实现 Handler

Handler 只接收：

- 已按统一接口和设备合同附录校验的类型化输入；
- 当前 `LineRunEpoch` 和对象执行的只读事实；
- `ProjectionReader` 返回的位置、队列和设备投影；
- 对应业务模块返回的同步、封闭 WMS 业务结果；
- 决定工厂（Decision Factory）。

Handler 只返回以下封闭 Decision 类别；具体 SDK 使用可判别类型表达，不允许插件返回任意字典或自定义副作用：

- 等待设备、位置或新的业务输入；
- 请求一个逻辑设备动作并创建 `DeviceCommand`；
- 使用 WMS 已批准的对象、来源、目标和约束请求一个供应商无关的 `TransportTask`；
- 创建一次 WMS 确认义务；
- 按 WMS 业务结果执行指定下一步或业务 NG；
- 暂停当前对象或停止新的依赖型准入，并保留全部可靠对象身份；
- 按类型化设备故障隔离指定对象、设备或配置范围，不扩大为默认整线故障；
- 请求进入人工对账或人工清线，冻结不确定的对象和位置；
- 完成本次对象执行。

`TransportTask` 只表达已批准的搬运事实，不选择 AGV/CTU、车辆、供应商、路线或调度策略。人工对账或清线 Decision 只建立
待处理事实和冻结范围；插件不得自行宣告现场已清理、伪造终态或自动恢复。

后续步骤只由与当前可靠对象匹配的输入触发：

- `DeviceCommand` 最终 CALLBACK；
- Transport evidence 应用端口接受的标准化成员位置事实或类型化异步终态结果；
- `WmsConfirmation` 同步结果（outcome）；
- 新的业务输入。

普通 WMS 业务事件不能终结 `TransportTask`；成员位置事实只更新位置投影，只有通过 Transport evidence 应用端口校验并
持久化的异步终态才能终结任务。

`CreateWmsConfirmation` 的 `request_data` 由插件按 operation 的获批严格 DTO 一次性构造，并在 Decision 创建时递归冻结；
核心只校验、持久化和可靠派发，不回查业务表补全请求。`CreateTransportTask` 使用 `correlation_id + step` 冻结插件决定身份，
并以 `resource_fence_id` 标识资源围栏；核心不再暴露具体工作线的 `TransportLeg` 或其它业务命名。

一个 Handler 不等待整条工作线执行完成。

### 2.5 静态元数据、显式组合和配置

每个 Handler 类使用 SDK `@handler(...)` 声明不可变静态元数据：类型化 Fact、稳定名称和支持版本。装饰器只把
`HandlerMetadata` 附着到类，不扫描包、不实例化 Handler，也不写入全局注册表。插件入口通过稳定 Python 引用返回固定
Handler tuple；部署 Composition Root 显式导入该入口并注入核心侧 factory、resolver 和可靠对象应用端口。

静态元数据和组合内容只包括：

- 插件身份和版本；
- 支持的工作线流程模式；
- Handler 的设备角色、输入类型和适用流程。

每个活动 `LineRunEpoch` 固定插件版本、配置版本和流程模式。切换插件、模式、角色绑定或物理拓扑前，必须清线并创建新
Epoch。

配置只保存现场事实：

- 设备实例、统一 Endpoint、超时和标准角色绑定；
- 设备角色、位置角色和实际物理拓扑；
- 位置、队列容量和故障隔离范围；
- 无法由约定推导的少量业务参数。

配置不保存供应商协议选择、私有路径、字段别名或应用层认证分支。相同类型工作线只创建不同配置实例，不复制插件代码。

## 3. 目标文件结构

```text
workline_plugins/<plugin_key>/
├── pyproject.toml
├── uv.lock
├── src/
│   └── <plugin_package>/
│       ├── __init__.py
│       ├── facts.py
│       ├── plugin.py
│       └── handlers/
│           ├── __init__.py
│           ├── _guards.py
│           └── <business_trigger>.py
├── tests/
│   ├── <handler_or_contract_test>.py
│   └── e2e/
└── fixtures/
```

插件包只声明 WES SDK 和业务侧依赖，独立维护测试配置和构建入口。核心 `src/` 和核心 `tests/` 不保存具体插件源码或测试；
已交付插件只放在仓库根目录的独立插件包中。只有出现真实独立职责时才增加文件，不预建设备 Adapter 包、客户尚未需要的
扩展点或业务模板。

Handler 按稳定业务触发拆分，而不是按物理 `EVENT`、`COMMAND`、`ACK`、`CALLBACK` 各建一套文件。`plugin.py` 只显式构造
固定 tuple，不扫描 `handlers/`、不使用动态 import，也不提供可变 registry。插件所需执行、Epoch 和位置快照由核心
Composition Root 在同一事务构造为类型化 Fact；Handler 只消费该不可变快照。

供应商一致性验收是对外部 ECS/网关实现的验收，不与 WorkLine 插件同包，也不进入核心业务测试。

## 4. 开发硬规则

- 插件不得访问数据库 Session、ORM、Repository、SQL、HTTP Client 或 Celery。
- 所有依赖必须显式注入；禁止 Service Locator、全局容器查找和动态 import。
- 供应商差异必须由供应商 ECS/网关收敛为白皮书统一接口，不得进入插件或核心分支。
- 插件只读取本地执行投影，通过对应业务模块取得同步 WMS 业务结果；不得直接使用 `WmsClient`，也不得组合事实重算
  来源、目标、优先级、业务路线、业务异常分类、替代来源、取消、恢复或业务终态。
- 改变 WMS 状态的结果通过 `WmsConfirmation` 可靠提交，不在插件中直接发送。
- 设备命令必须先持久化为 `DeviceCommand`；ACK 不得当作最终完成。
- 设备间机械互锁由 ECS/PLC 负责，插件不得建立工作线级全局锁。
- WMS 业务拒绝是正常分支；插件结合稳定业务异常分类和执行证据决定物理 NG 路由，不得伪装为硬件故障。
- WMS 业务结果缺失、过期、矛盾或物理不可执行时失败关闭；插件不得选择替代业务方案。
- WMS 不可用只暂停新的 WMS 依赖准入和尚未取得业务结果的 Decision，不得标记为业务 NG。已经 ACK 的 `DeviceCommand`
  继续等待并消费最终 CALLBACK；设备事件和 CALLBACK 仍须可靠接收、持久化和关联；已完成物理事实形成或保留原
  `WmsConfirmation`，未决 `TransportTask` 保留原身份等待可靠异步终态；提交结果未知或回调超期时进入人工对账。恢复后只有对应业务操作（operation）合同
  明确允许且可靠对象标记为可重试时才可使用原身份安全重提。
- 物理状态不确定时必须人工清线，不得自动重放命令或猜测现场状态。
- 不使用 Manifest、动态 Catalog、生成索引或通用 Intent/Effect/Capability 平台。
- 不保留旧字段 alias、兼容 shim、双写、双读或旧路径 fallback。
- 相似逻辑满足 Rule of Three 后才抽取小型技术库，不建设通用工作流框架。

## 5. 测试与验证

| 测试 | 至少覆盖 |
| --- | --- |
| 核心统一接口合同 | 固定路径、公共包络、身份、重复、冲突和 ACK/CALLBACK 边界 |
| 供应商一致性验收 | 真实设备实现符合白皮书和获批设备合同附录 |
| 插件纯逻辑测试 | 类型化输入与 WMS 结果下的 Decision、设备忙、物理冲突、模式不匹配、业务 NG、设备故障和依赖暂停 |
| SDK 测试夹具集成 | 类型化输入、依赖注入、封闭 Decision 及其到可靠对象的公共接线，不承载真实业务场景 |
| 部署级端到端验收 | 安装后的插件经 WES 公共入口、真实持久化、HTTP/CALLBACK、故障和多对象并发形成闭环 |
| 架构边界 | Handler 不依赖数据库、Repository、HTTP、Celery、Service Locator 或全局容器 |

入站持久化与幂等、通用命令证据、`LineRunEpoch` fencing 等 WES 基础能力由核心测试证明；SDK 测试夹具只证明公共 SPI/SDK
接线；供应商一致性验收只证明外部实现符合协议；插件纯逻辑测试只证明 WMS 结果到执行 Decision 的映射；部署级端到端验收
证明安装组合能够闭环。各层不得相互替代或复制。

插件包进入自身目录后至少运行：

```bash
uv sync --dev
uv run pytest tests -q
uv run ruff format --check .
uv run ruff check .
```

插件包内的 Handler 测试不得启动真实 PostgreSQL、HTTP、Celery 或供应商设备。需要真实 PostgreSQL、HTTP、CALLBACK、
故障或并发环境时，在部署级端到端验收流水线通过安装后的 WES 公共边界运行；这不会改变插件禁止访问数据库和网络的规则。
WES 核心默认 pytest、核心覆盖率、核心质量门禁和核心 HEAVY selector 不得发现或运行具体插件测试或部署验收。

## 6. 交付检查

- [ ] 所有现场设备和位置都有明确角色与配置。
- [ ] 所有设备 Event、Command、ACK、CALLBACK 和错误语义都在获批设备合同附录中闭合。
- [ ] 获批附录的合同版本、设备/ECS/固件、配置和 `LineRunEpoch` 绑定明确，行为变化不会在活动 Epoch 内静默切换。
- [ ] 供应商实现已通过统一接口一致性验收。
- [ ] 每个业务场景都有 WMS 封闭结果、执行映射表、Handler 和成功/失败测试。
- [ ] 插件只处理类型化输入，只返回封闭 Decision。
- [ ] 插件没有数据库、Repository、HTTP、Celery 或动态依赖查找。
- [ ] WMS 业务 NG、设备故障、依赖暂停和人工清线语义明确分离，插件不本地改判。
- [ ] WMS 不可用只阻止新的依赖型决定；既有命令、回调、确认义务和运输任务保留身份并按各自生命周期闭环。
- [ ] 插件版本、配置版本和流程模式固定在 `LineRunEpoch`。
- [ ] 插件代码、测试和 fixture 位于同一个 `workline_plugins/<plugin_key>/` 独立包。
- [ ] 核心、供应商一致性和插件验收分别通过，未互相替代。
- [ ] 插件纯逻辑、SDK 测试夹具和部署级端到端验收边界明确，Handler 未因集成测试取得数据库或网络依赖。
- [ ] 所有已发现实现差异都已挂入对应 Phase 或 `TODOS.md`，并具有明确验收标准。
- [ ] 核心 `tests/` 未新增任何具体工作线、插件或供应商内部行为测试。

## 参考

- [WES 最小执行架构收敛设计][architecture-spec]
- [WES 第三方设备统一接口白皮书][device-wire]
- [测试指南][test-guide]

[architecture-spec]: superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
[device-wire]: integration/third_party_integration_whitepaper.md
[test-guide]: ../tests/README.md
