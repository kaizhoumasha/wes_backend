---
title: Workline Plugin 与 System Capability 平台设计
status: Implementation In Progress
created_at: 2026-07-15
reviewed_at: 2026-07-15
updated_at: 2026-07-16
scope: 平台架构与首个真实垂直切片，不包含完整 Workline 业务流程
replaces:
  - docs/plugin_development_guide.md 的旧插件开发口径
  - docs/business/workline_plugin_architecture_design.md 中已失效的旧插件运行框架口径
related:
  - docs/superpowers/plans/2026-07-15-workline-active-inventory-foundation.md
  - docs/architecture/workline-and-plugin-restructuring.md
  - docs/architecture/workline-restructuring-architecture.md
  - docs/architecture/workline-restructuring-module.md
  - docs/business/workline_business_data_event_flow_spec.md
---

# Workline Plugin 与 System Capability 平台设计

## 1. 文档定位

本文定义目标态 Workline 扩展平台及其可执行落地顺序。平台建立在现有 RuntimeInbox、Session、
RuntimeIntent、Effect/Outbox、Hold、重试、重放、Ports 和 material-flow capability 基础上，不建立平行
Runtime。

这是未发布系统的破坏性重构：不兼容旧 `WorklinePlugin`、旧装饰器、旧 registry、旧 template、adapter、
fallback 或 import shim。最终代码只保留目标合同，严格遵循 DRY、KISS、SOLID、YAGNI。

平台合同不得脱离真实业务先行定型。首个 thin vertical slice 必须使用 rough sorter 的一条真实、窄业务闭环；
中性 conformance plugin 只作为共享合同测试 fixture，不作为独立产品里程碑。

## 2. 问题与目标

上一轮重构完成了可靠 Runtime 底座，但删除旧插件后，新增 Workline 仍会把设备事件、命令结果和业务判断写入
核心 Orchestrator；现有系统能力也缺少统一、类型安全的 Plugin 调用面。

目标：

1. 新增 Workline 只新增自包含 Plugin、typed binding 和测试；不得增加 Orchestrator/EffectApplier 业务分支。
2. Plugin 解释 canonical input、读取受限事实、作出业务决策，并输出 typed `PluginDecision`。
3. System Capability 提供可复用、类型化、原子的 QUERY 或 EFFECT；不得编排完整 Workline。
4. Runtime 继续唯一拥有持久化、事务协调、锁、幂等、重试、Outbox、Hold、审计和 replay。
5. 构建期生成确定性静态索引；运行时不扫描目录、不字符串动态 import、不修改 registry。
6. 每项约束都能由合同测试、架构门禁、诊断或 cutover preflight 自动验证。

## 3. 范围挑战后的实施边界

### 3.1 第一实施切片

第一切片只交付证明平台合同所需的最小闭环：

- 先生成跨环境 active Workline inventory，并批准 migration matrix。
- 为 rough sorter 选择一条真实事件到真实结果的窄闭环，先形成独立业务规格。
- 在现有 Runtime capability 底座上演进最终 Definition、Gateway 和 typed outcome。
- 完成一个真实 Plugin、所需的最少 QUERY/EFFECT Capability、结果回流和 replay evidence。
- 用该闭环固化 conformance suite、生成索引和核心架构门禁。

第一切片不交付通用脚手架、完整诊断产品、全部真实 Workline 迁移或最终 cutover；这些是后续依赖子计划，
不得反向扩张第一切片抽象。

### 3.2 完整目标

完整目标包含：开发脚手架、无副作用诊断、所有 active Workline 的独立规格与实现、原子切换、旧入口清零和
开发文档更新。各子计划必须遵守本文合同，不得引入过渡 adapter 或双轨 dispatcher。

### 3.3 实施进度（截至 2026-07-16）

总体状态为 **Implementation In Progress**。`v0.17.0.0` / PR #86 已交付 T1 的单环境 active inventory
foundation；rough sorter 窄闭环业务合同已批准，但最小 Runtime contract、真实 vertical slice 与 cutover 尚未实施。

| 工作项 | 状态 | 已完成证据 / 剩余边界 |
| --- | --- | --- |
| Active inventory foundation | 已完成 | 严格冻结报告模型、权威 repository 装配、确定性 digest、100 条安全上限、只读 CLI 与稳定退出码已合并到 `develop` |
| PostgreSQL inventory contract | 已完成 | 五类运行引用状态矩阵、`REPEATABLE READ + READ ONLY`、MVCC 快照与数据库拒写测试通过 |
| 治理与发布门禁 | 已完成 | 默认回归 `2666 passed, 5 skipped`，PostgreSQL integration `11 passed`，Ruff、Bandit、测试拓扑与架构门禁通过 |
| 跨环境 migration matrix 与批准 | 未完成 | 当前命令每次只生成一个环境的 foundation report；仍需聚合、签名与批准证据 |
| WorkItem/Intent version pin、binding requirement | 未完成 | 待 Runtime contract 与 binding 模型落地后纳入同一 inventory/preflight |
| Rough sorter 窄闭环业务合同（T2） | 已完成 | [业务合同](../../business/rough_sorter_scan_decision_contract.md)与 [13-case fixture](../../../tests/fixtures/workline_contract/rough_sorter/scan_decision_cases.json) 已由 kaizhou 于 `2026-07-16T19:39:04+08:00` 批准；这是业务合同批准，不是生产 Runtime 交付 |
| 最小 Runtime contract 与 T3-T9 | 未开始 | 当前生产 Runtime 仍存在 fixture 中所有标记为 `partial` / `gap` 的实现缺口；必须按依赖顺序实施和验证，不得把 T2 批准解释为 vertical slice 已交付 |

T2 业务合同门禁已通过，当前完成度允许进入最小 Runtime contract 的后续实施，但 T3-T9 均未开始，且**不代表**
完整 T1、Integration Gate 或 cutover readiness。inventory CLI 的 `foundation_ready=true` 只证明当前阶段事实合同通过，
不能替代跨环境批准、版本引用清零和最终切换门禁。

## 4. What already exists

| 现有能力 | 处理方式 |
| --- | --- |
| `RuntimeInbox`、Normalizer、Session resolver、claim/retry/Hold | 直接复用；只移除 Workline-specific 决策 |
| `RuntimeCapabilityDefinition/Catalog/Dispatcher` | 原地演进为 `SystemCapabilityDefinition` 及其静态索引 |
| `CapabilityPortRegistry`、`RuntimeCapabilityContext` | 保留 Port 限制思想；修正为 attempt-scoped 实例，不缓存 Session-bound service |
| `RuntimeIntentEffectApplier`、Outbox、结果 Inbox | 复用状态 owner；只增加一个通用 `SYSTEM_CAPABILITY` 分支 |
| material-flow capability 与领域 Service/Port | 作为真实 System Capability 的适配基础，不重写领域底座 |
| timeline、intent evidence、replay/diagnostic 基础 | 优先扩展 typed decision evidence，不默认新增 evidence 表 |
| Runtime capability、IntentApplier、RuntimeInbox 合同测试 | 作为回归基线；新增平台合同通过参数化 conformance suite 补齐 |

现有 `WorklineCapabilityDefinition` 影响面高，迁移必须拆为“引入最终类型 → 迁移调用方 → 删除旧 catalog”三个
结构提交；最终态不得留下 runtime adapter、alias 或 fallback。

## 5. 目标架构

```text
Ingress / Celery / Timer
          │
          ▼
   RuntimeInbox (raw kind)
          │
          ▼
   Canonical Normalizer ───── Runtime lifecycle route
          │                    (claim/retry/dead-letter/technical timeout)
          ▼
   logical typed input
          │
          ▼
 Workline Plugin Dispatcher ── generated Workline Plugin Index
          │
          ├── QUERY ── SystemCapabilityGateway ── Domain Service / read Port
          │                   │
          │                   └── typed decision evidence
          ▼
 PluginDecision(intents, next_state)
          │
          ▼
 Runtime atomic persistence
          │
          ▼
 Generic EffectApplier ─────── generated System Capability Index
          │
          ├── LOCAL_TRANSACTIONAL ── Domain Service
          └── OUTBOX_ASYNC ───────── Outbox ── provider/device
                                              │
                                              ▼
                                    raw result RuntimeInbox
                                              │
                                              └── logical typed result → Plugin
```

依赖方向保持：API → Service → Repository → Database。Plugin 不形成新的数据访问层，Capability 也不能绕过所属
领域 Service。

## 6. 唯一领域概念

平台最终只保留两个扩展定义：

- `WorklinePluginDefinition`
- `SystemCapabilityDefinition`

只共享最小 identity/validation/generator utility，不创建通用 `ExtensionDefinition`、扩展 DSL 或插件式 generator
framework。

### 6.1 WorklinePluginDefinition

Definition 是插件身份唯一作者态真源，至少表达：

- `plugin_key`、`contract_version`。
- typed config model、typed PluginState model（可选）。
- logical input handler routes。
- 允许调用的 System Capability key/version 范围。
- business key、material identity、result classification、NG reason 等可选纯解析器。

目录名只是约定并由生成器对 Definition 校验，不参与运行时身份解析。Definition 不保存插件实例、数据库对象、
现场设备值、service locator 或 provider implementation。

### 6.2 配置模型与运行时绑定

不保留作者态 Manifest 作为第三个真源：

- `PluginConfigModel` 定义设备角色、拓扑结构、资源边界字段和校验规则，不包含现场实例值。
- `WorklinePluginBinding` 保存具体 Workline/租户/站点的设备、资源和 provider profile 值。
- 配置激活时进行 typed validation；Session 固定记录 binding version/hash。
- API 所需 manifest view 由 Definition、config schema 和 binding 组合生成。
- YAML 只可作为 fixture/example，不是运行时权威源。

### 6.3 PluginContext、PluginState 与 PluginDecision

`PluginContext[TState]` 是每次 attempt 临时装配的只读对象，包含 Session/WorkItem、权威领域事实、只读拓扑、
trace、当前 Inbox evidence、声明过的 Gateway 和当前 typed state；不得整体持久化。

领域事实继续由所属 Model/Service 唯一拥有。只有无法合理从权威事实推导的插件局部编排状态，才定义最小
`PluginState`。它是 Session 上的一级 typed/versioned snapshot，不写入自由格式 metadata。

Handler 返回封闭的 `PluginDecision(intents, next_state)`。Runtime 使用 optimistic version，在同一决策事务中
原子写入 next state、QUERY evidence 与 intents。旧状态未排空时禁止删除其实现版本，不建设多版本状态迁移框架。

### 6.4 Handler 输入所有权

Plugin 处理：

- canonical `DEVICE_EVENT`、`COMMAND_RESULT`、业务 `INTERNAL_EVENT`。
- `EXTERNAL_HTTP`/`INTERNAL_EVENT` 归一化后的 logical capability result。
- Runtime timer evidence 归一化后的 typed business timeout。

Runtime 继续处理 claim、lock、retry、dead-letter、replay 管理、Inbox worker timeout 和其他纯生命周期事件。
Runtime 拥有 timer 调度，Plugin 只拥有 timeout 后的业务选择。

## 7. System Capability 合同

### 7.1 Definition 与模式

`SystemCapabilityDefinition` 至少表达 key/version、`QUERY` 或 `EFFECT`、typed input/output、handler factory、
required Ports、admission 条件、timeout、effect completion mode 和审计策略。

静态 Definition/factory 可进程级共享；Gateway、Service、Port instance 和 QUERY evidence cache 必须按单次
Inbox attempt 创建。任何绑定 AsyncSession 的对象不得进入全局 registry cache。

### 7.2 封闭 outcome

Capability 使用同一组封闭结果：

- `Success[T]`
- `BusinessReject`
- `RetryableFailure`
- `ContractViolation`

预期结果使用返回值，不使用异常控制流。未知异常由 Runtime 统一映射为 retryable `UNKNOWN`；重试耗尽后进入
dead-letter/Hold。Capability 不得自定义自由格式 retry/evidence 协议。

### 7.3 QUERY

QUERY 只读且可在 Plugin 决策阶段调用：

- Plugin 必须预先声明能力；Gateway 校验 typed input、Port/profile、权限与 deadline。
- 本地 QUERY 通过只读 Service/Port 和短生命周期只读事务执行。
- 外部只读 QUERY 可以同步执行，但不得占用 Inbox 写事务、数据库连接或行锁。
- 相同 capability key/version、canonical input 和 admission snapshot 只在本 attempt 内合并执行/in-flight task。
- 不建立跨 Inbox、Session 或 Worker 的业务结果缓存。
- 每个 attempt 限制唯一 QUERY 数和 evidence 总字节数；超限 fail closed。

每个成功或预期失败结果都记录 typed decision evidence：能力 key/version、输入/输出 hash、authority/source、
`evidence_at`、source version、admission snapshot 和脱敏结果。优先写入现有 timeline/intent evidence，不默认新增表。

### 7.4 QUERY 与事务阶段

```text
短事务：claim Inbox + 读取决策快照 + attempt token
                    │ commit/release
                    ▼
无写事务：bounded QUERY + typed evidence
                    │
                    ▼
短事务：校验 lease/version/token
        ├── changed → 丢弃结果并安全重试
        └── valid   → 原子写 evidence + PluginState + intents
```

首次决策失败且尚未提交时，重试可读取新事实；一旦决策提交，replay 只注入 recorded result，不再次访问 provider。

### 7.5 EFFECT 与幂等

Plugin 调用 `effect` 只创建通用 `SYSTEM_CAPABILITY` RuntimeIntent，不在 handler 调用栈执行副作用。

Plugin 提供业务语义 `operation_key`。Runtime 使用 capability key/version、session/workitem、operation identity
派生最终幂等 key，并记录 canonical payload hash：同 key 同 payload 幂等成功，同 key 不同 payload 必须拒绝，
不得覆盖。

依赖可变事实的 EFFECT 携带 typed precondition/fact version；所属领域 Service 在同一事务中执行条件更新。
precondition 失效返回 `BusinessReject(STALE_PRECONDITION)`，结果回流 Plugin 重新决策，不盲目重试，也不建设
通用条件表达式 DSL。

### 7.6 事务唯一所有者

Runtime application service 是本地 EFFECT 事务唯一所有者：

- Capability handler 不创建、commit 或 rollback 事务。
- 领域 Service 在该路径参与外层事务，只 flush，不内部 commit。
- 领域事实、Runtime evidence/effect 状态与必要 Outbox 在同一数据库事务提交。
- 外部 I/O 永不进入该事务；事务内只写 Outbox。
- 不为此建设全仓通用 UnitOfWork 框架。

### 7.7 完成语义

- `LOCAL_TRANSACTIONAL`：事务 commit 后完成。
- `OUTBOX_ASYNC`：Outbox 入队后只表示 durably accepted；queued/dispatched/retry/exhausted 继续由现有 Outbox
  状态拥有，远端 ack/callback 通过 RuntimeInbox 回流，Plugin 决定业务完成。

Capability 平台不新增与 RuntimeIntent/Outbox 重复的状态表。

## 8. 授权、Admission 与可信边界

授权分为：

1. 构建期 Plugin capability allowlist。
2. 配置激活/运行时 Workline binding、provider、Port 和环境 admission。
3. 人工操作的 operator RBAC。

自动设备/Celery 事件使用系统/Workline authority，不伪造管理员。Intent 保存创建时主体、policy/version、binding/
provider snapshot、授权原因、有效期与 trace；执行时仍检查撤权、停用、有效期、环境 admission 和 kill switch。
失败则 fail closed/Hold，不 fallback 或改用其他 provider。manual override 必须记录真实 operator、原因、范围和有效期。

Plugin/System Capability 是仓库内可信代码。禁止 import、AST/Ruff 和架构测试是 CI 架构门禁，不是 Python 安全
沙箱。真正约束来自最小 Context/Gateway、只读事务、领域分层和 EFFECT 隔离。第三方不可信插件必须进程隔离，
不在当前范围内。

## 9. 构建期索引与 Admission 分工

使用一个 `generate_runtime_extensions` 命令协调两个强类型 Builder，分别输出独立 Workline Plugin Index 与
System Capability Index。只共享稳定排序、key/version 校验、原子写入和 `--check`。

构建期检查：身份、类型、handler 签名、route 唯一性、声明引用、QUERY/EFFECT 使用方式、已知 Port/profile 名称、
禁止依赖、稳定输出和 cold-start import。构建期不判断具体现场是否具备 provider instance。

配置激活期解析当前环境的 binding、provider profile 和 Port implementation；缺失或版本不匹配则拒绝激活。
每次执行再按固定 snapshot 验证当前可用性与撤权状态。

运行时只 import 生成文件，不扫描文件系统、不字符串动态 import、不允许启动后变更索引。

## 10. 结果回流、版本绑定与 Replay

不新增原始 `SYSTEM_CAPABILITY_RESULT` Inbox kind。外部原始结果继续保存为 `EXTERNAL_HTTP` 或 `INTERNAL_EVENT`；
Normalizer 依据 correlation、capability key/version 和 callback type 生成唯一 logical typed result route。

Session/WorkItem 固定 plugin key/version、binding hash 和生成索引摘要；Intent/evidence 固定实际使用的 Plugin 与
Capability key/version。普通 retry 使用原绑定版本，缺失则 fail closed/Hold，绝不静默升级。

两种操作必须明确区分：

- 确定性 replay：重放记录输入、QUERY evidence 和已绑定实现，不访问 provider。
- 新代码重新决策：显式创建新的 replay attempt/Session lineage，保留对原记录的 causation，不冒充原 replay。

切换前必须确保所有依赖将被删除版本的活动记录为零，因此最终仍不保留 N/N-1 loader。

## 11. Active inventory、迁移与原子切换

### 11.1 权威清单

首个真实 slice 前运行只读 inventory，汇总所有环境 Workline 配置、现场/租户 binding、非终态 Session/WorkItem、
未完成 Inbox/Intent/Outbox 和 provider/Port 要求，产出 migration matrix。Integration Gate 前冻结配置；任何变更
都必须重新生成并批准清单。cutover preflight 复用同一统计逻辑。

### 11.2 实施顺序

1. inventory 与 rough sorter 窄闭环业务规格。
2. 最小平台合同与真实 vertical slice。
3. 从真实 slice 固化 conformance suite、生成器和诊断骨架。
4. 完整 rough sorter 规格与实现。
5. SMT 及其他 migration matrix 中的必需 Workline 各自独立规格与实现。
6. Integration Gate、配置冻结、历史 trace 离线 replay。
7. 有界排空、原子切换、旧入口清零。
8. 脚手架、完整诊断与开发文档作为依赖子计划交付，不阻塞第一切片。

### 11.3 历史 trace

历史实现和旧测试不是新行为验收基线，但匿名化现场/联调事件、命令结果、固件差异、乱序、重复和 timeout 样本
是规格发现材料。新 Normalizer/诊断器对 corpus 离线 replay，输出“已覆盖/明确改变/无法解释”；每个差异由新
Workline 规格显式批准，不要求与旧代码输出一致，也不保留旧 shim。

### 11.4 有界排空

冻结入口后按 Session、Inbox、Intent、Outbox 输出旧版本引用清单。在业务批准的最大等待时间内继续处理可完成项；
异常项只能通过显式人工恢复、业务取消或领域补偿关闭并留下审计证据。preflight 仅在所有旧版本可执行引用为零、
所有 active Definition 存在、API/Worker/Beat 版本与索引摘要一致、旧入口清零时通过。

截止时间仍非零则恢复旧版本入口并中止发布；不得强制切换、自动迁移旧 Session 或运行旧 sidecar。

### 11.5 Cutover point 与回滚

```text
冻结入口 → 排空 → 部署/迁移/startup smoke
                     │
                     ├── 恢复入口前失败：整体回滚旧版本
                     │
                     └── 恢复入口并产生新写入：roll-forward only
                                              └── 停止入口 / Hold / 修复发布
```

恢复入口是不可逆 cutover point。数据库 downgrade 只验证迁移结构，不代表生产数据可在 cutover 后安全回滚；
灾难恢复属于整站备份恢复，不等同应用版本 rollback。

## 12. 模块与测试归属

目标生产目录：

```text
src/app/runtime/workline_plugins/<plugin_key>/
src/app/runtime/system_capabilities/<domain>/<capability_key>/
```

测试严格进入现有治理目录：

```text
tests/workline_plugins/<plugin_key>/              # Plugin unit、binding、handler
tests/workline_runtime/system_capabilities/...    # Capability/Gateway/evidence 纯逻辑
tests/contracts/system_capabilities/...           # 跨领域公开合同
tests/integration/workline_capabilities/...       # PostgreSQL、Celery、provider 联调
tests/e2e/workline_capabilities/...               # 完整业务闭环与原子切换
tests/architecture/...                            # import、分层、旧入口清零
```

平台只维护一套参数化 conformance suite。每个 Plugin 通过 fixture 证明自己满足公共合同，只补自身状态机、业务 NG、
乱序、timeout 和人工处理行为；Integration/E2E 不重复单元级排列组合。

## 13. 测试覆盖图与失败模式

```text
CODE PATHS                                            OPERATIONS / USER FLOWS
[+] Generator                                         [+] Add Plugin/Capability
  ├── duplicate/ambiguous/illegal → fail closed         ├── scaffold → implement → generate
  └── drift/import smoke → CI failure                    └── --check + cold-start smoke

[+] Inbox → Plugin                                    [+] Business loop [→E2E]
  ├── route/admission/typed input                        ├── event → QUERY → Intent → Effect
  ├── timeout logical route                              ├── result re-entry → next decision
  └── invalid decision → ContractViolation               └── duplicate/out-of-order/Hold

[+] QUERY                                             [+] Replay [→E2E]
  ├── four typed outcomes + UNKNOWN                      ├── recorded evidence, no provider call
  ├── coalescing/limit/deadline                           └── explicit new-code re-decision lineage
  └── stale attempt token → discard/retry

[+] EFFECT                                            [+] Cutover [→E2E]
  ├── idempotent same / payload conflict                 ├── inventory/freeze/drain/preflight
  ├── precondition pass / stale rejection                ├── rollback before cutover point
  └── local commit / outbox accepted / result             └── roll-forward after new writes
```

必须覆盖的现实失败及可见结果：

| 路径 | 生产失败 | 测试 | 处理与可见性 |
| --- | --- | --- | --- |
| 索引生成 | 重复 route 或产物漂移 | unit + architecture | CI 明确失败，不静默 |
| 配置激活 | binding/provider 缺失 | contract | 拒绝激活并输出缺失项 |
| Plugin route | 未知或歧义输入 | conformance | `ContractViolation` + diagnostic |
| QUERY | timeout/未知异常 | unit + integration | typed retry；耗尽后 Hold |
| QUERY | attempt 期间 lease/version 改变 | concurrency | 丢弃结果并安全重试 |
| Evidence | 过大或脱敏失败 | unit | fail closed，不持久化敏感原文 |
| EFFECT | 相同 key 不同 payload | contract | 冲突拒绝并审计 |
| EFFECT | 事实版本已变化 | integration | stale precondition 回流 Plugin |
| Outbox | 派发成功但 callback 丢失 | integration | timeout/retry/Hold，不误报完成 |
| Result route | 重复、乱序或 correlation 缺失 | regression | 幂等/拒绝并给出诊断 |
| Replay | 绑定实现不存在 | regression | Hold，禁止静默升级 |
| Cutover | 旧引用非零或进程摘要不一致 | PostgreSQL/Celery integration | preflight 阻断发布 |

不存在“无测试、无错误处理且静默”的已知路径；实现新增分支时必须同步扩展此矩阵。

## 14. 性能与容量约束

- 静态索引启动时一次加载；当前不引入 lazy import。
- QUERY 只做 attempt-scoped coalescing，不建设跨请求 TTL/LRU 或分布式缓存。
- 外部 I/O 不占写事务、连接或行锁；单 QUERY 与 decision phase 都有 deadline。
- attempt 设置最大唯一 QUERY 数、单条/总 evidence 字节上限和 Intent 数上限。
- 真实 slice 建立基准：cold-start import、单 Inbox 决策延迟、数据库连接占用、Outbox enqueue 和 replay 延迟。
- 超过预算时先优化实际热点，不预建缓存框架或分布式调度器。

## 15. 架构与质量门禁

- Orchestrator/EffectApplier 不得包含 Workline key、事件名、命令名或业务 timeout 分支。
- Plugin 不得 import Repository、SQLAlchemy、HTTP client、Celery、provider DTO 或 service locator。
- System Capability 不得直接访问 Repository，只调用所属领域 Service/Port。
- EFFECT 不得在 Plugin handler 调用栈执行。
- 生成索引必须确定、不可漂移；未注册、未声明或环境不满足必须 fail closed。
- 旧 Plugin API、registry、adapter、fallback、import 和双轨 dispatcher 必须为零。
- 默认快速回归、collect-only topology guardrail、Ruff、Bandit、质量门禁、FastAPI/Celery startup smoke 必须通过。
- 涉及数据库时运行 Alembic upgrade/downgrade 与 PostgreSQL integration；downgrade 不代表 cutover 后业务回滚。

建议在分阶段事务、Effect 完成语义和 Session 状态模型实现文件中保留短 ASCII 注释，说明 owner 与状态转换；
普通 Definition、纯 validator 和简单 Builder 不添加装饰性图示。

## 16. 实施依赖与并行边界

| 工作流 | 模块 | 依赖 |
| --- | --- | --- |
| A. inventory 与 rough sorter 窄规格 | 配置、Runtime 查询、业务文档 | 无 |
| B. Definition/Gateway/outcome/index | Runtime capability、contracts | A 的真实需求边界 |
| C. Plugin binding/state/dispatcher | Workline Plugin、Session、Normalizer | A、B |
| D. EFFECT/事务/Outbox/result | Runtime Intent、领域 Service、Outbox | B |
| E. conformance/guardrail/test topology | tests/workline_*、contracts、architecture | B 的合同 |
| F. cutover preflight 与部署验证 | deployment、integration、migration | C、D、E、全部 Workline |
| G. 脚手架、完整诊断、文档 | scripts、diagnostics、docs | B、C、D 稳定 |

并行策略：A 先行；B 完成最小合同后，C、D、E 可在独立 worktree 并行，但 C/D 都触及 Runtime orchestration，
合并前必须顺序集成并解决所有权冲突；F 等待全部业务迁移；G 可在真实 slice 稳定后与后续 Workline 并行。
不要为第一切片创建过多 worktree；共享 Session/RuntimeIntent 模型的变更必须串行。

## 17. NOT in scope

- 旧 Plugin API 或行为兼容：系统未发布，保留兼容层只会增加双重真源。
- 运行时安装、卸载、热更新和第三方插件市场：当前扩展均为仓库内可信代码。
- 不可信 Python 插件沙箱：需要进程/RPC 隔离，当前没有业务需求。
- YAML 流程 DSL、低代码编排或可视化设计器：真实 Workline 尚不足以证明需要。
- 通用 Extension Definition/Generator framework：只有两种明确扩展类型。
- N/N-1 loader、旧 Runtime sidecar、状态自动迁移：采用有界排空和原子切换。
- 跨 Inbox 业务结果缓存、分布式 QUERY cache：会引入陈旧和隔离问题。
- 通用 precondition DSL、全仓 UnitOfWork 重构：领域 Service 原子方法和局部 transaction coordinator 已足够。
- 第一切片内完成全部脚手架、完整诊断和全部 Workline：分别进入依赖子计划，避免平台先于真实需求膨胀。

## 18. Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above.

- [ ] **T1 (P1, human: ~1d / CC: ~2h)** — Inventory — 建立 active Workline 权威清单与 migration matrix
  - Surfaced by: Scope/Outside voice — 切换集合必须在平台实现前可计算。
  - Files: Runtime query/service、deployment preflight、业务规格目录。
  - Verify: fixture 环境覆盖 DB/config/non-terminal runtime refs，输出稳定清单。
  - [x] Foundation（`v0.17.0.0` / PR #86）— 单环境只读报告、五类运行引用、provider profile catalog、确定性 digest、CLI、PostgreSQL 合同和治理门禁。
  - [ ] Remaining — WorkItem/Intent 版本引用、逐 Workline binding/provider/Port requirement、跨环境聚合、批准证据与 cutover preflight 复用。
- [x] **T2 (P1, human: ~2d / CC: ~4h)** — Rough sorter — 批准首个真实窄闭环业务规格
  - Surfaced by: Scope challenge — 平台合同不得由中性示例猜测。
  - Evidence: [业务合同](../../business/rough_sorter_scan_decision_contract.md)、[13-case fixture](../../../tests/fixtures/workline_contract/rough_sorter/scan_decision_cases.json) 与 [合同测试](../../../tests/contracts/workline/test_rough_sorter_scan_decision_spec.py)。
  - Approval: kaizhou 于 `2026-07-16T19:39:04+08:00` 批准；此项只完成业务合同批准，不表示生产 Runtime 或 vertical slice 已交付。
  - Verify: 输入、状态、能力、成功/NG/timeout/replay 验收完整。
- [ ] **T3 (P1, human: ~4d / CC: ~1d)** — Runtime contracts — 收敛两类 Definition、typed outcome 与静态索引
  - Surfaced by: Architecture/Code quality — 删除重复 catalog、身份与 generator 抽象。
  - Input: 首先只支持本切片真实需要的 typed outcome、QUERY evidence、Intent identity 与 recorded replay；不得加入第二条 WorkLine、通用 DSL、Plugin marketplace 或其他预建抽象。本项只定义需求边界，不表示 T3 已开始。
  - Files: runtime capability/contracts/index generator、architecture tests。
  - Verify: 生成确定、`--check`、cold-start、旧 catalog 清零。
- [ ] **T4 (P1, human: ~5d / CC: ~1d)** — Plugin runtime — 实现 binding、Context、PluginState、PluginDecision 与业务 timeout route
  - Surfaced by: Outside voice — 插件状态和 timer 业务所有权必须闭环。
  - Files: workline_plugins、Session、Normalizer/dispatcher。
  - Verify: optimistic conflict、typed state、timeout route 和无核心业务分支测试。
- [ ] **T5 (P1, human: ~5d / CC: ~1d)** — Capability Gateway — 实现 attempt-scoped QUERY、evidence 与 replay
  - Surfaced by: Architecture/Performance — QUERY 需确定证据、短事务与有界合并。
  - Files: capability gateway、runtime attempt coordinator、timeline/intent evidence。
  - Verify: outcome、deadline、limit、stale token、recorded replay 全覆盖。
- [ ] **T6 (P1, human: ~5d / CC: ~1d)** — Effect pipeline — 实现事务 owner、precondition、幂等和两种完成语义
  - Surfaced by: Architecture/Outside voice — 防止部分提交、TOCTOU 与过早完成。
  - Files: RuntimeIntent Effect、领域 Service、Outbox/result normalization。
  - Verify: PostgreSQL/Celery integration 覆盖冲突、rollback、callback 丢失和结果回流。
- [ ] **T7 (P1, human: ~3d / CC: ~6h)** — Test platform — 建立共享 conformance suite 与架构门禁
  - Surfaced by: Test review — 公共合同必须复用且符合测试拓扑。
  - Files: tests/workline_plugins、workline_runtime、contracts、architecture。
  - Verify: topology guardrail、受影响域、collect-only 和质量门禁。
- [ ] **T8 (P1, human: ~4d / CC: ~1d)** — Cutover — 实现 inventory-backed preflight、排空和 roll-forward 演练
  - Surfaced by: Test/Outside voice — 原子切换需要可终止、可自动判定。
  - Files: deployment、integration、migration、runbook。
  - Verify: 非零旧引用/摘要不一致阻断；cutover point 前 rollback、之后 roll-forward。
- [ ] **T9 (P2, human: ~4d / CC: ~1d)** — Developer experience — 交付脚手架、无副作用诊断和新开发指南
  - Surfaced by: Scope challenge — 工具应在真实 slice 固化合同后实现。
  - Files: scripts、runtime diagnostics、docs/plugin_development_guide.md。
  - Verify: 新 Plugin/Capability 示例从生成到诊断可执行且不写运行数据。

## 19. 验收标准

1. rough sorter 真实窄闭环证明 RuntimeInbox → Plugin → QUERY/evidence → Intent → EFFECT/result → Plugin。
2. Orchestrator、EffectApplier 和中央手写 catalog 不含任何 Workline-specific 分支。
3. 只有两个 Definition；无作者态 Manifest 身份/config 真源，无兼容 adapter/fallback。
4. retry/replay、binding、Plugin/Capability 版本和索引摘要均可审计且不静默升级。
5. QUERY 不占写事务，EFFECT 事务原子，stale precondition 可回流重新决策。
6. 所有 Plugin 通过共享 conformance suite，并拥有自身业务 regression/E2E。
7. migration matrix 中所有 active Workline 均有批准规格、实现和测试。
8. cutover preflight 自动证明旧引用为零、进程版本一致、旧入口清零。
9. cutover point 前可回滚；产生新写入后只允许 roll-forward。
10. 文档、索引、测试拓扑和质量门禁与最终代码一致。

## GSTACK REVIEW REPORT（2026-07-15 历史评审快照）

以下内容记录 2026-07-15 评审时点的判断与约束，不代表当前实施状态。

### Review outcome

- Step 0 Scope Challenge：保留完整目标，但首个实现收敛为 rough sorter 真实 thin vertical slice。
- Architecture Review：6 个问题，全部解决。
- Code Quality Review：4 个问题，全部解决。
- Test Review：已输出覆盖图，3 类结构性缺口已纳入计划。
- Performance Review：2 个问题，全部解决。
- Outside Voice：Codex 已运行，16 项发现均逐项裁决；15 项纳入或由既有决策强化，1 项保留原方案。
- Unresolved decisions：0。
- Critical silent failure gaps：0。
- TODOS.md updates：0；候选均为计划内任务或明确非目标。
- Parallelization：3 个中期并行工作流，Runtime 共享模型顺序集成，cutover 串行收口。
- Engineering principles：所有选择均以 DRY、KISS、SOLID、YAGNI 和无兼容目标为约束。

### Architecture verdict（历史）

方案可以进入分阶段实施，但必须先完成 active inventory 与 rough sorter 真实窄闭环规格。平台不得先于真实需求
形成通用框架；任何旧入口、双轨运行、隐式版本升级、跨层访问或自由格式状态都属于阻断项。

### 当前结论（截至 2026-07-16）

- T2 rough sorter 业务合同已由 kaizhou 批准，业务合同门禁已通过。
- T1 Remaining 仍未完成，不得把 inventory foundation 解释为完整 T1。
- T3 尚未开始；最小 Runtime contract 与生产 vertical slice 均未交付。

### Review artifacts

- QA test plan：`~/.gstack/projects/kaizhoumasha-wes_backend/kaizhou-develop-eng-review-test-plan-20260715-124414.md`
- Implementation task JSONL：由本次评审生成到 `~/.gstack/projects/kaizhoumasha-wes_backend/`。
