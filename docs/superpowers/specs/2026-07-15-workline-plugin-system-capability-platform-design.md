---
title: Workline Plugin 与 System Capability 平台设计
status: Draft for written review
created_at: 2026-07-15
scope: 平台架构，不包含具体 Workline 业务流程
replaces:
  - docs/plugin_development_guide.md 的旧插件开发口径
  - docs/business/workline_plugin_architecture_design.md 中已失效的旧插件运行框架口径
related:
  - docs/architecture/workline-and-plugin-restructuring.md
  - docs/architecture/workline-restructuring-architecture.md
  - docs/architecture/workline-restructuring-module.md
  - docs/business/workline_business_data_event_flow_spec.md
---

# Workline Plugin 与 System Capability 平台设计

## 1. 文档定位

本文定义面向后续新 Workline 的统一扩展平台。目标不是恢复已经删除的旧 `WorklinePlugin` 代码，而是在上一轮目标态 Runtime 架构之上，重新建立低复杂度、低核心侵入、可审计的插件式开发体验。

本设计已经确认以下前提：

- 这是破坏性重构，不兼容旧 `WorklinePlugin`、旧装饰器、旧 registry、旧 template、adapter 或 fallback。
- 保留上一轮重构已经完成并通过合同验证的目标态 Runtime、Ports、事实模型和系统能力实现。
- 旧 Workline 业务代码和旧测试不作为新业务行为的默认验收基线。每条 Workline 必须重新形成独立业务规格。
- 平台规格与具体 Workline 业务规格分开。本文不定义 rough sorter、SMT 或其他具体流程步骤。
- 所有当前必需 Workline 完成新规格和新实现后，再进行一次原子切换；`develop` 不长期承载新旧双轨。

## 2. 问题陈述

上一轮重构完成了 RuntimeInbox、RuntimeIntent、Effect/Outbox、Session、Hold、幂等、重试、重放和目标态 material-flow capability 等运行底座，但同时删除了旧插件的自包含目录、handler 路由、模板、诊断和开发入口。

当前新增 Workline 存在以下问题：

- 设备事件和命令结果业务分支进入核心 Orchestrator。
- Workline manifest、业务合同、handler、provider profile 和 capability catalog 分散维护。
- 新增流程可能需要修改多个中央文件，容易引入跨 Workline 回归。
- 系统已有能力缺少面向插件的统一、类型安全调用入口。
- 系统没有的能力缺少可重复的快速定义方式。
- 现有插件开发指南仍引用已经删除的路径，无法执行。

根因不是 Runtime 主链路失效，而是目标态架构缺少等价的 authoring surface，即面向开发者的扩展面。

## 3. 目标与非目标

### 3.1 目标

1. 新增 Workline 时，人工只新增自包含插件目录和对应测试目录，不修改 Orchestrator、EffectApplier 或中央手写 catalog。
2. 插件根据设备事件、命令结果和业务要求进行流程编排。
3. 插件通过类型安全 Gateway 使用系统能力，不直接接触数据库、Repository、HTTP Client、Celery 或外部 SDK。
4. 系统能力可以作为自包含模块快速新增，并由构建期生成静态索引。
5. QUERY 与 EFFECT 语义严格分离；所有副作用继续受 Runtime 的事务、幂等、Outbox、重试和审计治理。
6. 保留上一轮目标态 Runtime 和系统能力实现，避免重写已经验证的底座。
7. 通过脚手架、fixtures、诊断工具和统一合同测试降低后续开发复杂度。

### 3.2 非目标

- 不支持运行时安装、卸载、热更新或第三方 Python 包插件。
- 不建设插件市场、跨进程 capability RPC 或远程执行平台。
- 不建设 YAML 流程 DSL、低代码编排器或可视化流程编辑器。
- 不允许 manifest 保存条件表达式、脚本、状态机实现或供应商字段映射。
- 不恢复旧 Plugin API，也不提供兼容适配期。
- 不在本规格中定义任何具体 Workline 的业务流程。
- 不因为平台重构而重写现有 RuntimeInbox、Session、Intent、Effect、Outbox、Hold 或 WMS Port 底座。

## 4. 设计原则

### 4.1 DRY

- 一个系统动作只能有一个 System Capability owner。
- WMS 调用、设备命令、物料/容器事实、中间态记录、资源预约等能力不得复制到多个插件。
- Workline Plugin 与 System Capability 共用同一套 descriptor 校验、构建期索引生成和诊断基础设施。
- 通用 payload normalize、路由、幂等、错误映射和审计由平台集中提供。

### 4.2 KISS

- System Capability 只有 `QUERY` 和 `EFFECT` 两种模式。
- Workline 流程使用普通 Python typed handler 表达，不引入新 DSL。
- 运行时只加载构建期生成的静态索引，不扫描目录，不执行字符串动态 import。
- Plugin 只做流程决策，System Capability 只做原子能力，Runtime 只做执行治理。

### 4.3 SOLID

- 单一职责：Plugin、Gateway、System Capability、Runtime 各自拥有明确边界。
- 开闭原则：新增 Workline 或 System Capability 通过新增模块完成，不修改通用分发器。
- 里氏替换：所有 Plugin 与 Capability 必须通过统一 conformance suite。
- 接口隔离：Plugin 只看到自己声明的 typed capabilities，不获得全局 service container。
- 依赖倒置：Plugin 和 Capability 依赖 typed protocol/port，不依赖具体 HTTP、数据库或供应商实现。

### 4.4 YAGNI

- 只为已经出现的业务需求新增 System Capability。
- 不预建通用依赖图、补偿语言、脚本沙箱或多版本并行加载器。
- 不把所有领域动作抽象成万能模型；新能力优先复用现有 RuntimeIntent primitive、Service 和 Port。
- 只有现有 Runtime primitive 无法表达真实需求时，才扩展 Runtime 底座，并作为独立平台变更评审。

## 5. 保留与替换边界

### 5.1 必须保留

- `RuntimeInbox` 的统一入口、claim、失败、dead-letter 和 replay 语义。
- Session、ExecutionSession、ExecutionWorkItem、correlation 和 timeline 事实。
- `RuntimeIntent`、EffectApplier、SystemOutbox 和派发闭环。
- RuntimeHold、幂等、锁、重试、超时、人工控制和诊断能力。
- WMS query/effect Ports、ExternalContractProfile 和 provider admission。
- material、container、resource、location、reservation 等目标态事实和已有 Service。
- 上一轮重构已经完成的 material-flow capability 和相关合同测试。

### 5.2 必须替换或删除

- Orchestrator 中按 Workline key、事件名、命令名编写的业务 `if/else`。
- EffectApplier 中为单个 Workline 或单个业务能力新增的分支。
- 手工维护的碎片化 Plugin/Capability catalog 暴露方式。
- 旧 Plugin API、旧 registry、旧动态加载、adapter 和 fallback。
- 已失效的插件开发指南和仍宣称旧运行框架可用的文档口径。

## 6. 总体架构

```text
Ingress
  -> RuntimeInbox
  -> Normalizer
  -> Generic Workline Plugin Dispatcher
  -> Workline Plugin Handler
       -> SystemCapabilityGateway.query(...)
       -> SystemCapabilityGateway.effect(...) -> RuntimeIntent
  -> Runtime Intent Validation
  -> Runtime Effect Pipeline
  -> Generated System Capability Index
  -> System Capability Handler
  -> Domain Service / Port / Outbox
  -> Session / Timeline / Hold / Resource Facts / External Callback Loop
```

架构分为三层：

| 层次 | 责任 | 禁止事项 |
| --- | --- | --- |
| Workline Plugin | 解释设备与业务输入、读取业务上下文、调用只读能力、决定下一步、生成 effect intent | 不写 Repository，不持有 DB，不调 HTTP，不直接 dispatch，不拥有 Runtime lifecycle |
| System Capability | 暴露可复用、类型化、原子的查询或动作能力 | 不编排完整 Workline，不越过所属领域 Service，不直接依赖供应商实现 |
| Runtime Foundation | 持久化、事务、幂等、锁、重试、Outbox、追踪、Hold、人工控制、重放和 effect 治理 | 不包含具体 Workline 业务判断 |

## 7. 模块组织

### 7.1 Workline Plugin

目标目录：

```text
src/app/runtime/workline_plugins/<plugin_key>/
```

单个插件按职责拆分为：

- descriptor：插件标识、合同版本、manifest、context model、handler modules、所需 System Capability。
- manifest：设备角色、拓扑、资源边界和静态配置。
- contracts：设备事件、命令结果和业务输入的 typed model。
- context：插件拥有的类型化业务上下文。
- handlers：事件、结果、内部事件和外部业务输入的流程决策。
- decision services：可选的纯业务计算；不得访问 DB 或外部系统。

对应测试放在：

```text
tests/workline_plugins/<plugin_key>/
```

普通新增 Workline 允许产生机器生成索引 diff，但开发者不得手改核心 Runtime 文件。

### 7.2 System Capability

目标目录：

```text
src/app/runtime/system_capabilities/<domain>/<capability_key>/
```

单个能力声明：

- capability key 和版本。
- `QUERY` 或 `EFFECT` 模式。
- typed input/output。
- required Port、权限和 provider profile 要求。
- timeout、idempotency 和审计策略。
- query handler 或 effect handler。
- 所属领域 Service/Port adapter。

System Capability 目录是平台适配入口，不是新的数据访问层。若能力需要新增数据库事实，仍必须在所属领域按 Model → Repository → Service 分层实现，再由 capability handler 调用 Service。禁止在 capability handler 中直接使用 Repository 或 SQLAlchemy。

对应测试放在：

```text
tests/runtime/system_capabilities/<domain>/<capability_key>/
```

## 8. 核心合同

### 8.1 WorklinePluginDefinition

插件定义至少表达：

- `plugin_key` 与 `contract_version`。
- manifest 路径和业务 context model。
- handler 路由集合。
- 允许使用的 System Capability key 集合。
- business key、material identity、result classification、NG reason 等可选解析器。

定义是不可变静态数据，不保存插件运行实例、数据库对象或 service locator。

### 8.2 Plugin Handler

第一版支持以下业务输入：

- `DEVICE_EVENT`：按 canonical event type 路由。
- `COMMAND_RESULT`：按 command type 与 normalized outcome/classification 路由。
- 业务 `INTERNAL_EVENT`：按 canonical event type 路由。
- 声明了 runtime capability 的 `EXTERNAL_HTTP`：按 runtime capability 与 callback type 路由。

以下输入继续由 Runtime 统一拥有：

- `TIMER_TIMEOUT`。
- 人工 hold、resume、cancel。
- replay 和 dead-letter 管理。
- lock、claim、retry 和 lifecycle 事件。

Handler 输入固定为受限 `PluginContext` 和 typed payload，输出固定为 `list[RuntimeIntent]`。Handler 可以异步调用只读 QUERY capability，但不能执行 EFFECT。

### 8.3 PluginContext

PluginContext 只暴露：

- Session/WorkItem 的只读快照。
- Workline 配置和 typed business context。
- 设备拓扑只读视图。
- clock、trace、correlation 和当前 inbox evidence。
- 仅包含插件声明能力的 `SystemCapabilityGateway`。
- RuntimeIntent builder。

PluginContext 不暴露：

- AsyncSession、Repository、UnitOfWork 实现。
- HTTP client、供应商 DTO、异常或 SDK。
- Celery、Outbox writer、Effect dispatcher。
- 全局 Service container 或按字符串获取任意 Service 的能力。

### 8.4 SystemCapabilityDefinition

系统能力定义至少表达：

- key、version、mode。
- typed input/output model。
- handler protocol。
- required Ports 与 permission/admission 条件。
- timeout、idempotency 和审计策略。

系统只允许两种 mode：

| Mode | 调用时机 | 返回 | 副作用 |
| --- | --- | --- | --- |
| QUERY | Plugin handler 决策阶段 | typed output | 严禁 |
| EFFECT | Runtime effect 阶段 | typed execution result/evidence | 允许，但必须受 Runtime 治理 |

### 8.5 SystemCapabilityGateway

Gateway 提供两种语义：

- `query`：验证插件声明、typed input、权限、Port/profile 和 timeout 后，执行只读 handler 并返回 typed output。
- `effect`：验证插件声明和 typed input 后，只创建通用 `SYSTEM_CAPABILITY` RuntimeIntent，不在 Plugin 调用栈执行副作用。

EFFECT intent 至少固定记录：

- capability key 与 version。
- typed payload 与 payload hash。
- idempotency key。
- correlation/causation/trace。
- timeout、permission 和 provider admission snapshot。

### 8.6 通用 System Capability Effect

EffectApplier 只增加一个通用 `SYSTEM_CAPABILITY` 分支。该分支通过生成索引找到 typed effect handler，并在 Runtime 控制的 effect phase 执行。

具体 System Capability 不再要求修改 EffectApplier。Handler 必须遵守：

- 本地数据写入调用所属领域 Service，并处于 Runtime 明确的事务边界。
- 外部系统动作通过已有 Effect Port/SystemOutbox 派发，不直接调用 HTTP。
- 执行结果返回稳定 evidence、retryability 和 failure classification。
- 幂等冲突不得通过静默覆盖解决。

## 9. 构建期索引

平台生成两个静态索引：

- Workline Plugin Index。
- System Capability Index。

两个生成器共用同一 generator core，并执行以下检查：

1. key/version 唯一且命名合法。
2. descriptor、manifest 和目录标识一致。
3. handler 签名和 typed input/output 合法。
4. route 不重复、不歧义。
5. Plugin 声明的 System Capability 全部存在。
6. QUERY/EFFECT 使用方式正确。
7. required Port/profile 可被环境满足。
8. capability implementation 不包含禁止 import。
9. 输出按稳定 key 排序，同一输入生成完全一致内容。

运行时只 import 生成文件，不扫描文件系统，不执行字符串动态 import，不允许注册表在启动后变更。

CI 和本地质量门禁必须提供 `--check` 模式，发现生成索引漂移时直接失败。

## 10. 数据流

### 10.1 设备事件与命令结果

1. Ingress 校验并写 RuntimeInbox。
2. Normalizer 生成 typed canonical input。
3. Runtime 根据 Workline 配置解析 plugin key。
4. Generic Dispatcher 从生成索引获取 PluginDefinition。
5. Plugin handler 根据 typed context 和输入做流程决策。
6. QUERY capability 返回只读事实。
7. Plugin 返回普通 RuntimeIntent 或 System Capability EFFECT intent。
8. Runtime 校验并持久化 intent。
9. Effect pipeline 执行本地 Service 或写 Outbox。
10. 结果通过 callback/RuntimeInbox 回流，继续下一步流程。

### 10.2 外部系统能力

外部 EFFECT 必须满足：

- Provider profile 已声明所需 Port。
- 请求先形成持久化 intent/evidence。
- 派发走 SystemOutbox 或现有 effect ledger。
- callback/result 回流 RuntimeInbox。
- Plugin 不持有供应商 client，也不判断供应商私有错误结构。

### 10.3 物料、容器与资源中间态

中间态由所属领域事实模型拥有。Plugin 只表达“需要记录或推进什么”，System Capability 调用对应领域 Service 写入事实，并由 Runtime 记录 causation、timeline 和 idempotency evidence。

禁止把物料、容器或资源状态仅存入 Plugin 私有 context 作为权威事实。

## 11. 错误处理

| 场景 | 处理 |
| --- | --- |
| descriptor、route、manifest 或索引错误 | 构建期/启动期 fail-fast，不进入运行 |
| payload 无法通过 typed validation | 写稳定 `CAPABILITY_PAYLOAD_INVALID` diagnostic，不调用业务 handler |
| configured plugin 或 capability 不存在 | fail closed，不 fallback |
| handler 缺失 | 建立 scope 明确的 Block/Hold，避免无限重试 |
| QUERY Port 暂时失败 | RuntimeInbox FAILED，按 Runtime retry policy 重试，耗尽后 dead-letter/Hold |
| Plugin 返回非法 Intent 或写保留字段 | 拒绝并记录 capability contract violation |
| EFFECT 暂时失败 | 按 capability retryability 与 Runtime policy 重试 |
| EFFECT 确定性业务拒绝 | 形成 typed failure evidence，由 Plugin 在回流输入中决定后续流程 |
| 幂等 key 同 key 不同 payload | 冲突并进入审计/对账，不覆盖旧事实 |

错误信息必须使用稳定 code 和结构化 evidence，不把内部异常、SQL 或供应商私有响应直接暴露给 Plugin 或 API。

## 12. 快速定义新能力

新增 System Capability 的标准流程：

1. 选择 owner domain 和 capability key。
2. 判断是 QUERY 还是 EFFECT。
3. 定义 typed input/output 与权限、Port、timeout、idempotency 合同。
4. 优先适配现有领域 Service、Port 或 RuntimeIntent primitive。
5. 若缺少领域事实，先在 owner domain 完成 Model → Repository → Service，再接 capability adapter。
6. 添加 unit、contract 和 integration tests。
7. 运行生成器更新静态索引。
8. 运行无副作用诊断和质量门禁。

“零核心手改”指不修改 Orchestrator、EffectApplier 业务分支或中央手写 registry；不意味着可以绕过领域分层。新增真实数据模型、Port 或外部 provider 时，仍需在所属领域完成必要实现和迁移。

## 13. 开发工具

平台应提供：

- `create_workline_plugin`：生成插件目录、descriptor、manifest、typed handler、fixtures 和测试骨架。
- `create_system_capability`：生成 QUERY/EFFECT capability 目录、合同、handler 和测试骨架。
- Workline Plugin Index generator/checker。
- System Capability Index generator/checker。
- 无副作用诊断器：展示 normalize、plugin resolve、route、typed context、QUERY result 和生成的 RuntimeIntent；不创建 Session，不写 Outbox，不执行 EFFECT。
- 通用 conformance test helpers。

脚手架只减少机械劳动，不生成具体业务流程或推测业务状态机。

## 14. 破坏性重构与原子切换

### 14.1 总体策略

重构在独立长期分支/worktree 中完成，按以下子项目推进：

1. 平台规格与平台基础实现。
2. 使用中性 conformance plugin/capability 验证平台。
3. 为 rough sorter 编写全新业务规格并实现。
4. 为 SMT 和其他当前必需 Workline 分别编写全新业务规格并实现。
5. 执行 Integration Gate。
6. 一次删除旧业务分发和失效入口，切换到生成索引。

平台基础实现不得单独替换生产路由。各业务子项目可以在隔离重构线持续集成，但在所有 active plugin key 完成前不合入最终切换。

### 14.2 不允许的过渡方式

- 新 Plugin adapter 调用旧 Plugin 或旧业务 handler。
- 新 handler 缺失时 fallback 到旧路径。
- 同一 RuntimeInbox 同时执行新旧业务逻辑。
- 按配置长期保留两套 dispatcher。
- 为了测试通过保留旧动态 registry 或 import shim。

### 14.3 原子切换前置条件

- 所有当前配置中的 active plugin key 均有新 PluginDefinition。
- 每条 Workline 都有独立批准的业务规格。
- 每条 Workline 都有 unit、integration、regression 和端到端合同测试。
- 新平台覆盖所有需要的 System Capability。
- Orchestrator 和 EffectApplier 中不存在 Workline key、事件名、命令名或业务分支。
- 旧 Plugin API、adapter、fallback、旧 registry 和旧 import 全部清零。
- 现有目标态 Runtime 与系统能力合同测试保持通过。
- 数据迁移、回滚和部署顺序已验证。

## 15. 测试与质量门禁

### 15.1 平台测试

- Plugin SDK 与 System Capability SDK unit tests。
- handler route precedence、重复检测和 async 行为。
- QUERY/EFFECT 隔离、权限和 provider admission。
- 通用 `SYSTEM_CAPABILITY` intent 的幂等、重试、审计和失败分类。
- 生成索引确定性、漂移检测和 cold-start import smoke。
- typed payload/result validation。
- 禁止依赖和分层架构 guardrail。
- 中性 conformance plugin 从 RuntimeInbox 到 RuntimeIntent/Effect 的完整闭环。

### 15.2 每个 Workline 的必备测试

- handler unit tests。
- manifest、contract、context 和 capability declaration tests。
- RuntimeInbox → Plugin → System Capability → Intent/Effect integration tests。
- 成功、业务 NG、系统失败、重复、乱序、timeout、replay 和人工处理 regression tests。
- 外部 provider fixture/contract tests。

### 15.3 架构门禁

- Orchestrator 不得包含 Workline-specific key、事件名或命令名。
- Plugin 不得 import Repository、SQLAlchemy、HTTP client、Celery、provider DTO 或 service locator。
- System Capability 不得直接访问 Repository；只依赖所属领域 Service/Port。
- EFFECT 不得在 Plugin handler 调用栈执行。
- 生成索引不得漂移。
- 未注册和未声明能力必须 fail closed。
- 旧 Plugin/runtime import 和兼容入口必须为零。

### 15.4 项目级验证

实现阶段按仓库规则运行：

- 受影响测试域。
- 默认快速回归和 collect-only topology guardrail。
- Ruff format/check。
- Bandit。
- `./scripts/git-quality-gate.sh --profile quality`。
- FastAPI 和 Celery import/startup smoke。
- 涉及数据库时的 Alembic upgrade/downgrade 和 PostgreSQL integration tests。

## 16. 文档交付

原子切换前必须完成：

- 将 `docs/plugin_development_guide.md` 重写为新 Workline Plugin 开发指南。
- 新增 System Capability 开发指南。
- 为旧 `workline_plugin_architecture_design.md` 明确 Historical/非当前入口状态。
- 更新架构索引、测试目录说明和本地命令。
- 为脚手架、索引生成器和诊断器提供可执行示例。
- 删除或归档所有引用已删除旧路径的当前态说明。

## 17. 验收标准

### 17.1 平台验收

1. 中性测试 Plugin 只通过新增插件目录和测试目录接入。
2. 中性 System Capability 只通过新增 capability 目录和测试目录接入。
3. 除机器生成索引外，新增上述模块时 Runtime 核心文件无人工 diff。
4. QUERY 可返回 typed output，且无法产生副作用。
5. EFFECT 只生成持久化 intent，并由 Runtime effect phase 执行。
6. 重复 route、非法依赖、未声明 capability 和索引漂移均在门禁中失败。
7. 无副作用诊断器能完整解释 normalize、route、context 和 intent。

### 17.2 最终切换验收

1. 所有 active Workline 均通过新 Plugin 平台运行。
2. 所有业务步骤只调用已注册 System Capability 或通用 RuntimeIntent primitive。
3. Orchestrator、EffectApplier 和中央 Runtime 文件不含 Workline-specific 业务逻辑。
4. 旧 Plugin API、registry、adapter、fallback 和失效模板全部删除。
5. 上一轮目标态 Runtime 和系统能力合同测试无回归。
6. 完整 quality gate、数据库验证、Celery 验证和回滚演练通过。

## 18. 风险与控制

| 风险 | 控制 |
| --- | --- |
| System Capability 变成新的 God module | 强制 owner domain、Service 层依赖和禁止 Repository/import guardrail |
| Plugin 借 QUERY 偷做副作用 | query protocol、只读 Port、测试 double 和事务写检测 |
| 通用 `SYSTEM_CAPABILITY` intent 失去类型安全 | descriptor 固定 input/output model，持久化 capability version 与 payload hash |
| 自动生成掩盖注册问题 | 构建期 fail-fast、确定性输出、CI `--check`、生成文件可审计 |
| 原子切换范围过大 | 平台和业务分规格、隔离 worktree 持续集成、Integration Gate 后才切换 |
| 重写业务时遗漏现场语义 | 每条 Workline 单独业务规格、外部合同评审和端到端验收，不从旧代码静默继承 |
| 为追求零核心修改而绕过分层 | 明确零核心仅指通用分发核心；真实新模型/Port 必须在 owner domain 正常实现 |

## 19. 后续子项目

本文书面评审通过后，后续顺序为：

1. 编写平台实现计划。
2. 在隔离 worktree 实现并验证平台基础。
3. 单独 brainstorm 全新 rough sorter 业务规格。
4. 单独 brainstorm 全新 SMT 业务规格。
5. 枚举其他 active Workline 并逐一补齐规格。
6. 编写最终 Integration Gate 与 Atomic Cutover 计划。

任何具体 Workline 进入实现前，都必须先完成独立业务规格，不得直接从旧代码反推并复制流程。
