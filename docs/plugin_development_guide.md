# WORKLINE 业务插件二次开发指南

> 本指南只回答一件事：如何根据仓库工作线物理情况、硬件厂家设备指令清单和 WMS 业务合同，
> 开发一套可部署、可测试的工作线业务插件。
>
> 架构原理以[WES 最小执行架构收敛设计][architecture-spec]为准，不在本文重复展开。

一次客户交付可以同时包含厂商 Adapter 和 WorkLine 业务插件，但二者是并列的独立包：Adapter 拥有厂商 wire 合同和
标准化映射，插件只消费角色化输入并拥有业务 Decision。任一包都不得以自己的测试替代另一包或核心基础能力验收。

## 1. 开发前必须准备

开始编码前取得以下资料：

| 输入 | 必须明确的内容 |
| --- | --- |
| 工作线物理资料 | 设备、位置、缓存、队列、容量、物理连接、对象流向、故障隔离范围 |
| 厂家接口资料 | 每台设备的 Event、Command、请求和响应、ACK、最终 CALLBACK、错误码、幂等规则 |
| WMS 业务合同 | 业务查询、来源和目标授权、人工任务、搬运目标、完成确认、业务拒绝 |
| 验收场景 | 正常流、等待、业务 NG、设备失败、WMS 不可用、重启清线、并发对象 |

资料必须能回答：

1. 哪个物理信号触发 WES。
2. 当前对象和目标位置在哪里。
3. 判定前需要读取哪些投影或 WMS 事实。
4. 判定后请求哪个逻辑设备动作，以及需要哪些业务参数。
5. 哪个 CALLBACK 表示物理动作最终完成。
6. 成功、NG、设备故障和依赖不可用分别如何继续。

厂家未提供最终 CALLBACK、命令关联、幂等行为或错误语义时，先补齐合同，不得在插件中猜测。

## 2. 按顺序开发

### 2.1 建立 WorkLine 物理模型

按现场实际划分 WorkLine，并整理：

- 入口、出口、工作位、缓存、队列、NG 去向和容量。
- 扫码器、输送设备、机械臂、检测设备等实际设备。
- 每台设备承担的稳定业务角色。
- 上下游连接、对象流向和 AGV/CTU 交接点。
- 单设备、当前对象或整线级故障隔离范围。

建议先形成以下清单：

| 清单 | 字段 |
| --- | --- |
| 位置 | 编码、角色、所属 WorkLine、容量、允许对象、上游、下游 |
| 设备 | 厂家编码、设备类型、角色、所属 WorkLine、Endpoint、状态来源 |
| 连接 | 上游、下游、物理方向、触发事件、执行设备 |
| 故障 | 厂家错误码、物理含义、影响对象、隔离范围、人工处理 |

设备内部步骤、坐标、机械互锁和安全逻辑属于 ECS/PLC。插件只选择逻辑设备动作，不拆分设备内部动作；
对应厂家 Adapter 负责把逻辑动作和业务参数映射为厂家长命令及其 Payload。

物理模型完成时，每个场景都必须能从触发输入追踪到当前对象、目标设备和位置、逻辑设备动作、
最终 CALLBACK、新位置及下一步。

### 2.2 整理独立厂家 Adapter 合同

为每台设备建立指令矩阵：

| 项目 | 记录内容 |
| --- | --- |
| Event | 事件名、触发条件、Payload、事件幂等键、WES ACK |
| Command | 命令名、请求 Payload、同步 ACK、设备忙或拒绝响应 |
| CALLBACK | 命令关联字段、成功和失败 Payload、最终物理后置条件 |
| Status | 空闲、运行、故障、离线的字段和取值 |
| Error | 错误码、物理含义、隔离范围、是否需要人工清线 |
| Idempotency | 重复命令和重复 CALLBACK 的厂家行为 |
| Timing | HTTP timeout、预计完成时间、CALLBACK 最长期限 |

必须区分：

```text
HTTP 请求成功
≠ ECS 已接纳 ACK
≠ 设备物理动作最终成功 CALLBACK
```

只有最终 CALLBACK 可以推进对象和位置。WES 不发明通用厂家命令，也不要求厂家改变其真实
Event、Command 或 Payload。

### 2.3 交付独立厂家 Adapter

每个厂家合同由三类代码隔离：

- DTO：精确校验厂家实际 Payload。
- HTTP Adapter：消费已装配、框架无关的 `OutboundHttpTransport`，拥有厂家 method/path/wire DTO、真实合同要求的认证和
  ACK/业务响应解释；不得创建裸 HTTP Client、管理连接池或复制通用传输错误分类。
- Mapper：把厂家设备、Event 和 CALLBACK 映射为工作线角色化输入，并把插件请求的逻辑设备动作映射为厂家命令。

Adapter 必须在 `device_adapters/<adapter_key>/` 独立交付代码、测试和 fixture，不嵌入任何插件包。Adapter 不包含工作线业务规则，
插件不读取厂家原始 JSON，也不声明厂商协议依赖。新增厂家时，不应修改最小执行内核或业务插件规则。

### 2.4 建立业务场景决策表

每个真实业务场景先写决策表，再写 Handler：

| 字段 | 内容 |
| --- | --- |
| 触发 | 角色化 Event、最终 CALLBACK 或 WMS 业务输入 |
| 当前事实 | 对象、位置、队列、目标设备状态 |
| WMS 查询 | 当前步骤需要的授权、来源、目标或业务判定 |
| 正常 Decision | 等待、设备命令、搬运、WMS 确认、正常路由或完成 |
| 成功后置条件 | CALLBACK 后更新的对象、位置、队列和设备投影 |
| 业务 NG | NG 原因、来源证据、物理去向 |
| 执行异常 | 硬件错误、隔离范围、人工处理 |
| 依赖异常 | WMS 不可用时的暂停和待确认事实 |

### 2.5 实现 Handler

Handler 只接收：

- 已完成身份校验和角色映射的类型化输入。
- 当前 `LineRunEpoch` 和对象执行的只读事实。
- `ProjectionReader` 返回的位置、队列和设备投影。
- `WmsCapabilities` 返回的同步业务结果。
- Decision Factory。

Handler 只返回以下封闭 Decision：

- 等待设备或位置。
- 请求一个逻辑设备动作。
- 请求一次 AGV/CTU 搬运。
- 创建一次 WMS 确认义务。
- 路由到正常下一步或业务 NG。
- 完成本次对象执行。

后续步骤只由与当前可靠对象匹配的以下输入触发：

- `DeviceCommand` 最终 CALLBACK。
- `TransportTask` status query 返回的 typed terminal result。
- `WmsConfirmation` 同步 outcome。
- 新的业务输入。

WMS callback/hint 只唤醒匹配的 `TransportTask` 查询，不携带或决定终态。一个 Handler 不等待整条工作线执行
完成。

### 2.6 注册和配置

插件使用稳定 Python 引用显式注册。注册内容只包括：

- 插件身份和版本。
- 支持的工作线流程模式。
- Handler 的设备角色、输入类型和适用流程。

每个活动 `LineRunEpoch` 固定插件版本、配置版本和流程模式。切换插件、模式、角色绑定或物理拓扑前，
必须清线并创建新 Epoch。

配置只保存现场事实：

- 设备实例与标准角色绑定；Endpoint/Timeout 由 Composition Root 用于装配对应 Transport。当前已确认 outbound 合同无认证，
  不提供凭据或认证配置；真实合同出现后必须先修订架构与 Adapter 计划。
- 设备角色、位置角色和实际物理拓扑。
- 位置、队列容量和故障隔离范围。
- 无法由约定推导的少量业务参数。

相同类型工作线只创建不同配置实例，不复制插件代码。

## 3. 目标文件结构

```text
device_adapters/<adapter_key>/
├── pyproject.toml
├── src/
│   └── <adapter_package>/
│       ├── __init__.py
│       ├── contracts.py
│       ├── http_adapter.py
│       └── mapper.py
├── tests/
│   ├── contracts/
│   ├── integration/
│   ├── e2e/
│   └── resilience/
└── fixtures/

workline_plugins/<plugin_key>/
├── pyproject.toml
├── src/
│   └── <plugin_package>/
│       ├── __init__.py
│       ├── contracts.py
│       ├── handlers.py
│       └── registration.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   └── resilience/
└── fixtures/
```

Adapter 包和插件包是两个独立二次开发交付单元。Adapter 声明厂商协议依赖；插件只声明 WES SDK 和业务侧依赖。
两类包分别维护测试配置和构建入口，代码、测试和 fixture 必须与各自所有者同包加入；不得在核心 `tests/` 或另一类包中
寄存测试，也不得创建只有测试没有对应实现的空包。

核心仓库不保存具体插件源码。只有出现真实独立职责时才增加文件，不得预建客户尚未需要的扩展点或业务模板。

## 4. 开发硬规则

- 插件不得访问数据库 Session、ORM、Repository、SQL、HTTP Client 或 Celery。
- 所有依赖必须显式注入；禁止 Service Locator、全局容器查找和动态 import。
- 厂家差异只能进入 Adapter、DTO、Mapper 和合同测试。
- 插件只读取本地投影，通过 `WmsCapabilities` 访问同步 WMS 业务事实。
- 改变 WMS 状态的结果通过 `WmsConfirmation` 可靠提交，不在插件中直接发送。
- 设备命令必须先持久化为 `DeviceCommand`；ACK 不得当作最终完成。
- 设备间机械互锁由 ECS/PLC 负责，插件不得建立工作线级全局锁。
- 业务 NG 是正常分支，不得伪装为硬件故障。
- WMS 不可用属于依赖暂停，不得标记为业务 NG。
- 物理状态不确定时必须人工清线，不得自动重放命令或猜测现场状态。
- 不使用 Manifest、动态 Catalog、生成索引或通用 Intent/Effect/Capability 平台。
- 不保留旧字段 alias、兼容 shim、双写、双读或旧路径 fallback。
- 相似逻辑满足 Rule of Three 后才抽取小型技术库，不建设通用工作流框架。

## 5. 测试与验证

| 测试 | 至少覆盖 |
| --- | --- |
| Adapter 厂家合同 | 真实 Event、Command、ACK、CALLBACK、标准化映射、非法 Payload 和关联冲突 |
| 插件逻辑 | 标准化输入下的正常 Decision、设备忙、位置满、模式不匹配、业务 NG、硬件故障、依赖暂停 |
| 插件执行闭环 | 角色化最终结果、投影更新、多对象并发和人工清线 |
| 架构边界 | Handler 不依赖数据库、Repository、HTTP、Celery、Service Locator 或全局容器 |

入站持久化与幂等、通用命令证据、`LineRunEpoch` fencing 等 WES 基础能力由核心测试证明；Adapter 测试只覆盖厂商合同与
标准化映射；插件测试只覆盖业务 Decision 和对象推进。三类测试不得互相替代或复制。

每个 Adapter/插件包使用自己的 Pytest 配置、覆盖率和 CI。进入对应包目录后至少运行：

```bash
uv sync --dev
uv run pytest tests -q
uv run ruff format --check .
uv run ruff check .
```

Adapter/插件包需要真实 PostgreSQL、HTTP、CALLBACK、故障或并发环境时，在自身 CI 显式准备并运行受影响场景。
WES 核心默认 pytest、核心覆盖率、核心质量门禁和核心 HEAVY selector 都不得发现或运行这些二次开发包测试。

## 6. 交付检查

- [ ] 所有现场设备和位置都有明确角色与配置。
- [ ] 所有厂家 Event、Command、ACK、CALLBACK 和错误码都在对应 Adapter 包中拥有 DTO 与合同测试。
- [ ] 每个业务场景都有决策表、Handler 和成功/失败测试。
- [ ] Adapter 保证每个设备命令来自厂家清单，每个 CALLBACK 都能唯一关联命令；插件不读取这些 wire 事实。
- [ ] 插件只处理角色化输入，只返回封闭 Decision。
- [ ] 插件没有数据库、Repository、HTTP、Celery 或动态依赖查找。
- [ ] 业务 NG、硬件故障、依赖暂停和人工清线语义明确分离。
- [ ] 插件版本、配置版本和流程模式固定在 `LineRunEpoch`。
- [ ] 插件代码、测试和 fixture 位于同一个 `workline_plugins/<plugin_key>/` 独立包。
- [ ] Adapter 代码、测试和 fixture 位于同一个 `device_adapters/<adapter_key>/` 独立包。
- [ ] Adapter 和插件各自的合同/单元测试、受影响重测试和质量门禁分别全部通过。
- [ ] 核心 `tests/` 未新增任何具体工作线、插件或厂商行为测试，插件包也未寄存厂商 Adapter 测试。

## 参考

- [WES 最小执行架构收敛设计][architecture-spec]
- [测试指南][test-guide]

[architecture-spec]: superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
[test-guide]: ../tests/README.md
