---
title: 北向公共能力双层提炼设计
status: CEO + Engineering 评审完成，可进入实施
created_at: 2026-07-21
reviewed_at: 2026-07-21
parent: docs/architecture/target-state-contract.md
---

# 北向公共能力双层提炼设计

## 1. 结论

本设计不只拆分粗分机专用 WMS handler，而是建立可验证、可替换、可解释的北向能力边界：

1. 每个真实 WMS operation 使用独立、强类型的 `wms.*` capability；不得使用 family 级万能 handler、自由字符串 dispatcher 或 `dict[str, Any]` 请求。
2. `material_flow.*` 只执行无 I/O 的纯业务 policy 或 typed request builder；技术失败不得伪装成业务拒绝。
3. WorkLine Plugin 只保留本线配置、状态机、路由和动作选择，不依赖 WMS client、Provider DTO 或协议错误。
4. QUERY 由 Runtime 同步执行；EFFECT 使用事务内 durable outbox 与事务后 attempt-scoped Port dispatch 两段式执行。
5. Provider 必须通过同一套不可覆写的 conformance suite；迁移必须有 shadow comparison、不可变 readiness report、回放语料、SLO、告警和 Runbook。
6. 系统尚未发布。实现不得保留旧版本、旧 schema、旧数据或旧调用方式的兼容分支、alias、双写或迁移脚本；开发/测试数据允许清理重建。
7. 完整目标不缩水，但不得新建与现有 RuntimeIntent、SystemOutbox、WMS typed service/evidence 平行的执行面；只允许在既有主链上补齐 typed operation 与缺失语义。

完整交付边界包括所有现存字符串式 `port_method` / `effect_contracts` 消费者归零及通用旧路径删除。首批三个 operation 是实施切片，不是允许长期双机制的范围豁免。

实施在隔离 feature branch/worktree 中按门禁式逻辑提交推进；只有全部 legacy 调用归零、兼容路径删除且完整门禁通过后，才整体合入 `develop`，避免主开发分支长期保留双机制。

## 2. 设计原则与硬约束

- **DRY**：QUERY/EFFECT 技术语义、evidence、错误映射、幂等、callback 归并和观测只在 System Capability Runtime 实现一次。
- **KISS**：每个真实 operation 一个明确 capability identity；不用 union god handler、动态 recipe、DSL 或插件内反射调用。
- **SOLID**：Plugin 负责本线动作，material-flow 负责纯业务规则，Runtime 负责执行语义，Port/Provider 负责外部协议翻译。
- **YAGNI**：没有真实消费者就不创建 operation；模拟 Provider 先做最小 adapter/HTTP stub，不预建独立服务。
- **零兼容**：当前系统未发布，只保留单一目标合同；破坏性变化直接更新合同并清理开发/测试数据，不建设多版本 dispatcher。
- **零静默失败**：每种失败必须有命名、typed outcome、evidence、指标和测试；禁止 `except Exception` 后统一重试或吞掉错误。

## 3. What already exists

| Existing asset | Current value | Decision |
| --- | --- | --- |
| `src/app/runtime/system_capabilities/gateway.py` | 已有 QUERY gateway 与标准执行入口 | 复用，不新建平行 gateway |
| `src/app/runtime/system_capabilities/definition.py` | 已有 `SystemCapabilityDefinition` | 保持纯能力合同且不增加 transport/dispatch 字段；WMS operation 使用现有字段声明，该符号影响分析为 HIGH，因此避免无必要修改 |
| `src/app/runtime/system_capabilities/outcomes.py` | 已有标准 outcome | 复用并补齐命名错误，不让 Provider 异常穿透到 Plugin |
| `src/app/runtime/system_capabilities/index_builder.py` 与 `generated_index.py` | 已有生成索引 | 复用现有生成体系，扩展 capability catalog 与兼容报告 |
| `src/app/runtime/orchestration/services/intent/system_capability_effect_service.py` | 已有 transaction-side claim/outcome 语义 | 保持事务内无外部 I/O；增加事务后 Port dispatch 路径 |
| `src/app/runtime/orchestration/runtime_intent_log.py` | 已有 intent/effect ledger，但与 SystemOutbox 重复记录 transport 状态 | 收敛为 capability 语义账本；删除重复 transport attempt/retry/error 字段 |
| `src/app/wms_integration/ports/` | 已有 7 个领域 Port | 复用稳定 Port；真实 adapter 必须通过统一 conformance suite |
| `src/app/runtime/system_capabilities/wms/rough_sorter_inventory_admission/` | 当前混合 WMS 调用、错误映射和业务准入 | 拆为通用 QUERY + 纯 policy，验证后删除整个专用 capability |
| `src/app/runtime/workline_plugins/rough_sorter/handlers.py` | 当前构造 WMS DTO 并解释 outcome | 改为 typed capability request + 本线动作选择 |
| `src/app/runtime/capabilities/material_flow/sorter_inbound_runtime_service.py` | 当前存在字符串 `port_method` / `effect_contracts` | 全量盘点并逐 operation 替换，最终删除字符串式通用路径 |

实施前已完成的 GitNexus 影响分析：`SystemCapabilityDefinition` 为 HIGH（23 个直接、97 个总影响符号）；`SystemCapabilityEffectService` 为 LOW（3 个直接、14 个总影响符号）；`SystemCapabilityIndexBuilder` 为 LOW（2 个直接影响符号）。本设计只改文档，编码前仍须按当时索引重新运行影响分析。

## 4. 目标架构

```text
┌─────────────────────────────────────────────────────────────────────┐
│ WorkLine Plugin / Runtime Orchestrator                              │
│ typed config · state machine · declared capability · action choice │
└───────────────┬───────────────────────────────────────┬─────────────┘
                │ QUERY                                 │ EFFECT proposal
                ▼                                       ▼
┌──────────────────────────────┐       ┌──────────────────────────────┐
│ System Capability Gateway    │       │ RuntimeIntentLog / Outbox    │
│ validate · resolve · timeout │       │ claim · frozen spec · lease  │
│ outcome · evidence · trace   │       │ idempotency · state machine  │
└───────────────┬──────────────┘       └───────────────┬──────────────┘
                │ attempt-scoped Port                  │ after commit
                │                                      ▼
                │                      ┌──────────────────────────────┐
                │                      │ Existing Outbox Dispatcher   │
                │                      │ bulkhead · rate · retry      │
                │                      │ callback · reconciliation    │
                │                      └───────────────┬──────────────┘
                ▼                                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│ WMS Ports / Provider Adapters                                      │
│ protocol translation · provider limits · normalized authority data │
└───────────────┬─────────────────────────────────────────────────────┘
                │ authority snapshot / receipt / callback
                ▼
┌──────────────────────────────┐       ┌──────────────────────────────┐
│ Pure material-flow policy    │       │ Evidence / Observability     │
│ typed input · decision proof │       │ compare · replay · readiness │
│ no I/O · no retry · no state │       │ SLO · dashboard · runbook    │
└───────────────┬──────────────┘       └──────────────────────────────┘
                ▼
        Plugin chooses local action
```

真实编排顺序是 Runtime/Plugin 先取得 WMS 权威结果，再把规范化 snapshot 交给纯 policy；`material_flow` 不调用 WMS，不能画成它依赖 WMS 的 I/O 链。

### 4.1 目录职责

| Boundary | Owns | Must not own |
| --- | --- | --- |
| `runtime/workline_plugins/<line>` | 本线 typed config、状态机、路由、动作选择 | WMS DTO/client、Provider 错误、通用库存规则 |
| `runtime/capabilities/material_flow` | 纯 policy、typed request builder、policy version、decision evidence | Port 调用、外部重试、RuntimeIntentLog 写入 |
| `runtime/system_capabilities/wms` | Definition/handler、技术 outcome、evidence、replay、dispatch spec；直接引用 operation 领域合同 | 重复定义 Port request/snapshot、工作线字段、设备角色、本线动作、业务拒绝规则 |
| `wms_integration/ports` | 每个 operation 唯一的严格、冻结领域 request/snapshot 与稳定 Port | Provider transport DTO、WorkLine 状态、浮点数量 |
| Provider adapter/models | 每个 operation 的外部协议 DTO、唯一 ACL 映射、分页与错误翻译 | material-flow policy、Plugin 决策、共享 transport 重实现 |
| WMS shared transport executor | 无 operation 分支的 HTTP、预算、breaker、evidence 与共享 outcome | 领域 DTO 映射、operation switch、公开 family-level Port 方法 |

Provider transport inventory DTO 与 Port/domain inventory snapshot 必须使用不同名称，保持 ACL 分层且消除旧 provider/domain inventory item 的同名歧义。数量从 Provider DTO 映射到领域 snapshot 后全程使用 `Decimal`，禁止 `Decimal → float → Decimal` 往返；Provider 缺失字段保持 `None` 或返回命名合同错误，禁止用空字符串、`UNKNOWN` 等值伪造事实。

删除当前 `WmsTypedPortService` 的 family-level 公开 operation 方法与 `operation_name` 特殊分支。共享 transport executor 只执行 `WmsOperationContract` 已确定的 HTTP、预算、breaker、evidence 和 transport outcome；每个 operation adapter 单独拥有领域合同 ↔ Provider DTO 映射。不得复制 per-operation HTTP/evidence/breaker service。

Provider catalog 采用组合模型，不把 `ExternalContractProfile` 扩张为 God Object：`ExternalContractProfile` 只负责 provider/version/environment identity；每个 operation 使用独立 `WmsOperationContract` 承载 endpoint、HTTP method、timeout、预算、retry policy 与所需 outbound auth scheme，不包含 credential identity；binding/profile 的 `OutboundAuthProfile` 选择不可变、版本化 credential reference；入站 callback 使用独立 `InboundCallbackContract`。conformance fixture 路径与 required cases 属于测试/构建期 manifest，不进入生产运行时模型。删除当前字符串式 `runtime_capabilities_query/effect`、`cache_ttl_seconds`、WMS 专用 `WmsEndpointConfig` 以及 `EndpointRegistry` 中重复的 WMS endpoint 条目，不建设双目录同步或兼容 adapter。

Author-time 单一真源是代码中的 typed operation contract、System Capability Definition 与 Provider profile 声明。确定性生成器只读取这些声明，派生 capability catalog、Provider compatibility report、digest 与删除门禁输入；生成物禁止手改，CI 必须重新生成并验证零 diff。不得新增 YAML/JSON 运行时 DSL 或在多处手填同一 operation identity/version。

入站 callback security 与出站 `OutboundAuthProfile` 必须分离。出站 profile 使用封闭 auth scheme，Phase 0 只实现真实 WMS 当前合同需要的 scheme，不预建未使用机制；production 禁止 `NONE`。发送边界依据冻结 target snapshot 与版本化 credential reference 构造认证信息，日志/evidence 禁止保存密钥材料或完整认证 header。密钥轮换发布新 credential version 且只供新 intent 使用；旧版本必须保留到在途 outbox 终态。紧急撤销使尚未发送的旧 intent 停止派发，不得自动替换为新 key，必须进入人工取消/重建或 reconciliation。callback 的签名、nonce 和 replay window 仍由独立入站 security profile 负责。

Intent/outbox 创建时从该 catalog 冻结 profile identity、profile hash、完整非秘密 target snapshot 与版本化 credential reference；后续派发与重试只读取冻结快照，不重新解析当前环境变量、最新 profile 或 credential alias。数据库只保存版本化 reference，实际密钥材料由 secret provider 在发送时解析，禁止进入 snapshot、evidence 或日志。

## 5. Capability 与合同

### 5.1 Operation 粒度

| First consumer | Mode | Port operation | Capability contract |
| --- | --- | --- | --- |
| 粗分机库存准入 | QUERY | `WmsInventoryQueryPort.query_inventory` | 独立 typed query request/result；空库存是 `Success(empty snapshot)` |
| 粗分机入库确认 | EFFECT | `WmsInventoryTransactionPort.confirm_inbound` | 独立 typed request/result；定义幂等 owner、接受边界、UNKNOWN 与 reconciliation |
| 粗分机料盘绑定 | EFFECT | `WmsFulfillmentPort.notify_pkg_binding` | 独立 typed request/result；定义 callback correlation、完成边界和乱序归并 |

其余真实 operation 由 Phase 0 legacy inventory 列出，逐项建立独立 identity。不得把 query/transaction/fulfillment family 暴露成一个带 discriminator 的万能 capability。

### 5.2 QUERY 合同

- 请求使用强类型过滤条件；必须声明 Provider 分页语义、行数预算、字节预算和 deadline。
- 字节预算必须在 JSON 解析前生效：可信 `Content-Length` 已超限时立即拒绝；否则流式累计 wire bytes，超限立即终止。只允许 profile 明确声明的 content encoding，同时限制 decoded bytes 与压缩比；decoded body 合格后才解析 JSON，并限制嵌套深度与单字段长度。分页逐页累计 wire/decoded 总字节、总行数和 deadline。
- 超过任一预算返回命名技术错误，不允许静默截断或先无界聚合后再检查。
- 空结果是成功的空 authority snapshot；`material_flow` 再决定 `REJECT` 或 `HOLD`。
- Phase 1 全局移除现有 `query_inventory` 跨请求缓存：删除 `WmsQueryCacheService`、对应 TTL 配置、缓存测试与文档，不保留按调用方绕过或兼容分支。单次 execution 只查询一次，同一 snapshot 同时供生产 policy、shadow evaluator 和 evidence 使用。未来只有在真实 QPS、Provider latency 与限流数据证明必要时，才通过独立 ADR 重新引入缓存。
- QUERY evidence 写入失败时 fail closed；没有 evidence 不返回可消费成功结果。
- 所有 WMS QUERY Port 共用一个封闭、泛型的领域 outcome union：`Success[T] / BusinessReject / TechnicalFailure / ContractFailure`。Provider adapter 只做一次 transport → domain outcome 分类，Capability 以穷尽匹配转换为 Runtime outcome；删除逐 operation 的 `Unavailable/Rejected/ContractError` 异常类和重复 catch 链。未识别分类直接成为 `ContractFailure`，不得落入默认 retryable。
- 只有显式 `TechnicalFailure(retryable=true)` 才能消耗 retry budget。未预期异常统一 fail closed 为内部合同故障，写 critical diagnostic 且不自动重试；`DBAPIError` 等事务失效异常继续向外传播，由事务边界统一 rollback。删除核心 QUERY/EFFECT 路径中 `except Exception → RetryableFailure("UNKNOWN")` 的默认映射。

```text
INPUT
  ├─ nil/missing ─────────▶ WmsCapabilityInputError ─▶ HOLD + diagnostic
  ├─ valid but empty filter▶ WmsCapabilityInputError ─▶ HOLD + diagnostic
  ├─ valid request
  │     ├─ Provider success, rows > 0 ─▶ Success(snapshot) ─▶ pure policy
  │     ├─ Provider success, rows = 0 ─▶ Success(empty) ────▶ pure policy
  │     ├─ timeout/unavailable/429 ────▶ RetryableFailure ─▶ HOLD/retry budget
  │     ├─ malformed/over-budget ─────▶ ContractViolation ─▶ HOLD + alert
  │     └─ evidence write failure ────▶ EvidenceWriteFailure ─▶ fail closed
  └─ wrong type/version ───▶ WmsContractViolation ─▶ reject activation/execution
```

### 5.3 纯 material-flow policy

输入只包含领域请求、规范化 authority snapshot 和明确的 policy version。输出包含：

- `ADMIT` / `REJECT` / `HOLD`；
- 结构化 reason code；
- 使用过的 rule/policy version；
- 参与判定的输入字段摘要与 decision provenance。

policy 不访问数据库、Port、RuntimeIntentLog、环境变量或系统时间。需要时间语义时由调用方注入已冻结的 aware UTC 值。

### 5.4 EFFECT 两段式合同

EFFECT 必须复用现有 SystemOutbox、DispatchAttempt 和 Outbox dispatcher。`SystemCapabilityDefinition` 保持 transport-agnostic，不新增 endpoint、payload builder、dispatch factory 或其他派发字段。每个 WMS EFFECT 使用独立 typed handler，经对应领域 gateway 把 typed request 映射为现有 `DispatchEnvelope`；不得创建 WMS 专用平行 dispatcher、ledger 或重试引擎。事务内 handler 只验证并持久化 claim/outbox；提交后现有 dispatcher 才执行外部 I/O。operation 的 Port/Provider 语义必须在 gateway/adapter 边界保持可验证，不允许 Runtime 退化为自由 URL/headers/`Mapping` 字典。

Typed request 与 dispatch spec 在持久化前始终保持冻结模型。领域 gateway 是唯一 canonical serialization 边界：对 `EXTERNAL_HTTP` 持久化不可变 `canonical_payload_bytes`，payload hash、出站签名和 HTTP body 全部直接基于同一 bytes；`payload_json` 只作为可查询投影，禁止用于发送或重试。Dispatcher 与重试必须原样发送已冻结 bytes，不得经 JSON/JSONB 读取后重新序列化、补字段或重新选择默认值；replay 只按冻结 operation contract 解析验证。通用 `DispatchEnvelope` 不改造成影响所有 outbox 的泛型模型，但其 EXTERNAL_HTTP 分支必须携带 canonical bytes。

现有 `EXTERNAL_HTTP` 的 `bool` sender 合同必须替换为统一的 typed transport result，至少区分 `NOT_SENT`、`ACCEPTED`、`AMBIGUOUS`，并记录 transport phase、HTTP/协议结果和安全重试结论。`NOT_SENT` 才可进入有界自动重试，`ACCEPTED` 进入 `SENT`，`AMBIGUOUS` 必须停止自动派发并在 transport ledger 留下不可重试的未知结果，同时驱动 capability 语义进入 `UNKNOWN → RECONCILING`。该规则适用于全部 `EXTERNAL_HTTP`，不得为 WMS 建立特例。

账本权威必须单一：`RuntimeIntentLog` 只保存 capability immutable request、幂等身份与 `PROPOSED/ACCEPTED/COMPLETED/REJECTED/TECHNICAL_FAILED/UNKNOWN/RECONCILING` 语义状态；`REJECTED` 仅表示明确业务拒绝，`TECHNICAL_FAILED` 仅表示已确认未发送/未接受且 retry budget 耗尽。删除其中重复的 `dispatch_status`、transport attempt/retry 和 last-error 状态。`SystemOutbox` 唯一保存 `NEW/DISPATCHING/RETRY_WAIT/SENT/FAILED/UNKNOWN/CANCELLED` transport 状态，`WorklineDispatchAttempt` 保存每次 `DISPATCHING/SENT/FAILED/UNKNOWN/CANCELLED` attempt、typed transport outcome、lease 和 evidence。transport `UNKNOWN` 仅表示送达结果不确定，并使该 outbox 不再具备自动派发资格。`RuntimeIntentLog` 与 `SystemOutbox` 在同一事务写入相同的不可变 `dispatch_key`，两侧各自施加唯一约束，以合同测试保证 1:1；不建立 runtime schema 到 biz schema 的跨域外键，也不允许从 payload/correlation 猜测关联。只有 reducer 可以根据 transport/callback evidence 推进 capability 语义，禁止把 `SENT` 直接等同于业务成功。

Intent 创建时冻结 Provider profile identity/hash、非秘密 target snapshot、binding revision、capability/operation identity、contract version、request hash 与幂等语义。重试不得读取最新配置或环境变量改变执行目标；紧急暂停只停止派发，不能改写冻结快照。

Reducer 接受的封闭事件集合为：`INTENT_PROPOSED`、`ATTEMPT_STARTED`、`TRANSPORT_NOT_SENT`、`TRANSPORT_ACCEPTED`、`TRANSPORT_AMBIGUOUS`、`CALLBACK_ACCEPTED`、`CALLBACK_COMPLETED`、`CALLBACK_REJECTED`、`RECONCILIATION_OPENED`、`RECONCILIATION_RESOLVED`、`IDEMPOTENCY_CONFLICT`。其中 `CLAIMED/DISPATCHED` 只对应 attempt/outbox 事件或状态，不是 capability 语义状态；`IDEMPOTENCY_CONFLICT` 是合同 outcome + reconciliation event，不是持久化生命周期状态。独立 `ReconciliationCase` 只持久化 `OPEN/RESOLVED` 与 evidence/decision；它不覆盖或回退 intent 的不可变业务终态。

| Event | SystemOutbox / Attempt transition | RuntimeIntentLog transition |
| --- | --- | --- |
| `INTENT_PROPOSED` | `— → NEW` | `— → PROPOSED` |
| `ATTEMPT_STARTED` | `NEW/RETRY_WAIT → DISPATCHING` | 保持 `PROPOSED` |
| `TRANSPORT_NOT_SENT` | `DISPATCHING → RETRY_WAIT`；预算耗尽后 `FAILED` | 保持 `PROPOSED`；预算耗尽后 `TECHNICAL_FAILED` |
| `TRANSPORT_ACCEPTED` | `DISPATCHING → SENT` | `PROPOSED → ACCEPTED` |
| `TRANSPORT_AMBIGUOUS` | `DISPATCHING → UNKNOWN`，禁止再领取 | `PROPOSED/ACCEPTED → UNKNOWN` |
| `CALLBACK_ACCEPTED` | 保持 transport terminal | `PROPOSED/UNKNOWN → ACCEPTED` |
| `CALLBACK_COMPLETED` | 保持 transport terminal | 无 OPEN case 且 intent 未终态时 `PROPOSED/ACCEPTED/UNKNOWN → COMPLETED`；否则只追加 evidence |
| `CALLBACK_REJECTED` | 保持 transport terminal | 无 OPEN case 且 intent 未终态、事实不矛盾时 `PROPOSED/ACCEPTED/UNKNOWN → REJECTED`；否则只追加 evidence |
| `RECONCILIATION_OPENED` | 保持 transport terminal | 创建/更新 `ReconciliationCase(OPEN)`；非终态 `UNKNOWN/ACCEPTED → RECONCILING`，已有 terminal 保持不变 |
| `RECONCILIATION_RESOLVED` | 保持 transport terminal | case `OPEN → RESOLVED`；仅未终态 `RECONCILING → COMPLETED/REJECTED`，已有 terminal 保持不变 |
| `IDEMPOTENCY_CONFLICT` | 不创建新 attempt | 保留原状态，创建/更新 `ReconciliationCase(OPEN)` 并记录合同 outcome |

```text
RuntimeIntentLog: PROPOSED ─▶ ACCEPTED ─▶ COMPLETED
                         ├──▶ REJECTED             (business terminal)
                         ├──▶ TECHNICAL_FAILED     (clearly unsent terminal)
                         └──▶ UNKNOWN ─▶ RECONCILING ─▶ COMPLETED / REJECTED

SystemOutbox: NEW ─▶ DISPATCHING ─┬─▶ SENT
                                  ├─▶ RETRY_WAIT ─▶ DISPATCHING
                                  ├─▶ FAILED
                                  ├─▶ UNKNOWN  (no auto retry)
                                  └─▶ CANCELLED

ReconciliationCase: — ─▶ OPEN ──RECONCILIATION_RESOLVED──▶ RESOLVED

terminal + same duplicate callback  ─▶ keep terminal + append evidence
terminal + contradictory callback   ─▶ keep terminal + append evidence + OPEN case
OPEN case + ordinary callback       ─▶ append evidence only; policy emits RESOLVED event
```

只有明确未发送或未被远端接受的 attempt 才能自动重试。发送后 timeout、连接 reset 或语义不明的 5xx 进入 `UNKNOWN`，禁止盲目重发。若外部可能已接受但 effect evidence 写入失败，同样进入 `UNKNOWN`。

### 5.5 并发、租约与背压

- Outbox worker 使用 `SKIP LOCKED`、owner token 和有限 lease；lease 丢失后旧 worker 不得提交状态。
- SystemOutbox 持久化不可变、低基数并建立组合索引的 `provider_profile_identity` 与 `operation_identity` 调度列；禁止 worker 从 JSON payload/snapshot 提取调度身份。
- 按 Provider profile + operation 设置独立并发、速率、批量大小和 retry budget；worker 按活跃桶轮转，并在每桶额度内使用 `SKIP LOCKED` 领取。
- 达到限制时保留 durable backlog，不无限预取；允许暂停单个 binding/profile。
- 监控 queue age、backlog、rate limit、lease steal/loss 和 `UNKNOWN` 比例。

## 6. Shadow、Replay 与 Provider Conformance

### 6.1 Shadow comparison

Shadow 复用同一规范化输入和同一 WMS authority snapshot；禁止重复 WMS 调用、业务写入、RuntimeIntent 创建或设备动作。允许写独立 append-only comparison evidence。

必写、fail-closed 的 QUERY evidence 是 shadow expected 样本的唯一 durable 权威：在同一 evidence 事务中持久化 `shadow_eligible`、old/new policy 与 contract versions、确定性 `comparison_key`。生产请求内只执行有明确 CPU/deadline 预算的纯 shadow evaluation；comparison append 通过现有任务队列异步写入，不新建 shadow outbox/dispatcher，也不使用无监管 `asyncio.create_task`。异步队列不承担 expected 计数权威；Readiness 从 durable QUERY evidence 查询 expected 集合并与 comparison rows 对账。enqueue/consumer/store 失败或 expected/stored 缺口必须立即使 readiness window 失效并告警，生产响应不等待 comparison store。

Comparison row 不复制完整请求或 WMS authority snapshot，只保存 input/output hash、WMS evidence ID/reference、old/new policy version、outcome/reason/difference classification 与受控脱敏 divergence diff。详细调查通过 evidence 引用重建；被引用 WMS evidence 的保留期不得短于 comparison。禁止为查询方便复制完整 payload 或建立 payload 通用索引。

```text
normalized input + one authority snapshot
                  │
          ┌───────┴────────┐
          ▼                ▼
 temporary old evaluator  new versioned policy
          │                │
          └───────┬────────┘
                  ▼
 typed comparison: action · reason · error class · evidence
                  ▼
 immutable ReadinessReport ─▶ explicit go/no-go approval
```

Shadow evaluator 失败不得改变生产/主路径动作，但必须计入 readiness failure、重置对应观察窗口并告警。Comparison store 不可用时主路径继续；中断区间无资格计入 readiness，恢复后重新开始完整观察窗口。

Comparison evidence 在 PostgreSQL 中按 `observed_at` 分区，默认月分区；Phase 0 用峰值写入量、单行大小与 90 天总量决定是否改日分区。只建立 Provider/profile + 时间、readiness window + difference class、trace/evidence 定位索引。90 天到期删除旧分区，不逐行大批删除。

分区生命周期复用现有定时任务基础设施：始终预创建当前月及未来 3 个月分区，并对预创建失败提前告警；不设置 default partition。缺失目标分区时 comparison consumer 明确失败并使 readiness window 失效，禁止静默落入兜底表。清理先 drop 已到期 comparison partition，被引用 WMS evidence 的保留期必须更长；在线 drop 必须受 lock timeout/运行窗口约束并有失败重试与 Runbook。

迁移验证完成后保留最小通用 shadow runner、comparison schema 和 readiness generator；删除迁移专用旧 capability、旧 evaluator 与临时 routing，只长期保留不可还原聚合及完全合成/脱敏 fixture。

### 6.2 Readiness 门禁

每个 Provider/profile 必须连续观察至少 7 天且累计不少于 1,000 个 eligible samples，并满足：

- 未解释的关键 reason/action 差异为 0；
- 技术失败误映射为业务拒绝为 0；
- timeout/contract violation 不劣于旧 evaluator；
- 纯 shadow 只评估 `policy evaluation p99`，相对旧 evaluator 增幅不超过 10%；
- 独立的 `production QUERY end-to-end p99` 门禁覆盖 gateway、adapter、Provider、分页与 evidence，并满足 operation/profile SLO；不得从纯 shadow latency 推导端到端结论；
- evidence 完整率及平台 SLO 底线通过。

低流量环境连续 30 天仍不足 1,000 样本时，只允许以“全部样本零未解释差异 + 完整 replay/conformance + owner 书面 waiver”替代样本门槛。contract、policy、normalization、evaluator 版本变化以及 comparison store 中断都会重置观察窗口。

唯一审批依据是不可变 `ReadinessReport`：冻结 report ID、生成器及合同版本、观察窗、eligible/排除样本数与原因、差异分类、policy p99、QUERY end-to-end p99、SLO 结论和 evidence 引用。两项 latency 门禁任一失败都不得批准；Dashboard 不能单独批准切换。

### 6.3 Replay 与统一试卷

Replay runner 是纯执行器，不可访问生产 adapter、credential、outbox 或真实 callback endpoint。Replay envelope 至少包含规范化输入、input hash、contract/policy/provider profile 版本、authority snapshot/evidence 引用、时间语义与 redaction version。

所有 Provider 使用同一套不可覆写、不可 skip/xfail 的参数化核心 conformance suite。真实 adapter、确定性模拟 Provider 与 canonical replay factory 都覆盖 success、empty、reject、timeout、unavailable、malformed、pagination、precision、rate limit、idempotency 和 callback timing。Provider 只能声明有限可选 feature；强制语义不满足时 binding 激活 fail closed。

Conformance 使用两级门禁：CI 中真实 adapter 对 canonical scripted HTTP provider 执行全部不可跳过核心用例；binding 激活前在 staging 对真实 Provider endpoint 执行受控 live conformance，生成不可变认证报告并纳入 ReadinessReport。CI、replay、simulator 和本地测试不得访问生产 endpoint 或凭据；staging 报告必须记录 profile identity/hash、endpoint revision、试卷版本、样本与失败证据。

模拟 Provider 以最小 adapter/HTTP stub 起步，提供命名故障点与确定性 callback 调度。生产构建产物不包含模拟器；生产启动发现模拟器注册立即失败；binding admission 也拒绝 simulator profile。

#### Conformance attestation 信任边界

Conformance attestation 的可信性以同进程代码与部署环境均为 trusted 为前提。部署进程在模块导入时一次读取并冻结 Ed25519 trust root；composition、live runner 与持久化报告验证器在该信任边界内完成部署签发和验证，运行期间的模块属性重绑定不能更换已冻结的 root。

Python 同一进程内的任意恶意代码可以通过 reflection、`object.__new__` 或读取环境与内存绕过语言级 private/closure 约束，因此任意同进程代码执行视为进程完全失陷，不在 conformance attestation 威胁模型内。private、closure、sealed executor 和弱引用 registry 只用于收紧公开 API 与误用面，不作为抵抗已取得同进程执行能力攻击者的安全隔离，也不作“不可伪造”保证。

## 7. Error & Rescue Registry

| Method/codepath | Named error/outcome | Rescue action | Consumer/operator sees | Test |
| --- | --- | --- | --- | --- |
| QUERY request validation | `WmsCapabilityInputError` | 不调用 Provider，写诊断 evidence | `HOLD`，明确输入错误 | unit/contract |
| QUERY Port resolution | `WmsPortUnavailable` | fail closed，按预算重试 | `HOLD`，Provider unavailable | contract/integration |
| QUERY transport timeout | `WmsProviderTimeout` | 有界退避重试 | `HOLD`，暂时不可用 | conformance |
| QUERY rate limit | `WmsProviderRateLimited` | 尊重 retry-after 与 profile budget | `HOLD`，限流 | conformance/load |
| QUERY malformed response | `WmsProviderMalformedResponse` | 不进入 policy，告警 | `HOLD`，合同异常 | conformance |
| QUERY row/byte over limit | `WmsQueryBudgetExceeded` | 不截断，fail closed | `HOLD`，查询超限 | boundary/load |
| QUERY compressed/deep payload | `WmsResponseStructureBudgetExceeded` | 终止解码/解析，fail closed | `HOLD`，Provider 响应预算异常 | contract/load/security |
| QUERY evidence persist | `CapabilityEvidenceWriteError` | fail closed，不返回成功 | `HOLD`，证据不可用 | PostgreSQL integration |
| pure policy input | `MaterialFlowPolicyInputError` | 返回 typed HOLD reason | `HOLD`，策略输入无效 | table-driven unit |
| Outbox claim | `OutboxClaimConflict` | 其他 worker 已取得则跳过 | 无重复发送 | PostgreSQL concurrency |
| Dispatch lease | `OutboxLeaseLost` | 当前 worker 停止提交结果 | backlog/lease alert | resilience |
| Outbound authentication | `WmsOutboundAuthenticationError` | 不发送或停止重试，阻断 binding/profile；撤销 key 不自动替换 | 配置/密钥错误，运维取消/重建或 reconciliation | contract/security |
| EFFECT explicit reject | `WmsBusinessRejected` | 写 `REJECTED` terminal evidence | 明确业务拒绝 | conformance |
| EFFECT clearly unsent | `WmsDispatchNotAccepted` | 进入有限 `RETRY_WAIT` | 暂时处理中 | resilience |
| EFFECT retry exhausted while clearly unsent | `WmsDeliveryTechnicalFailure` | 写 `TECHNICAL_FAILED`，不 reconciliation | 明确技术投递失败 | integration/resilience |
| EFFECT ambiguous transport | `WmsDispatchUnknown` | 进入 `UNKNOWN`，查询/人工 reconciliation | 状态未知，禁止重发 | resilience |
| Duplicate different payload | `WmsIdempotencyConflict` | 进入 reconciliation | 幂等冲突 | integration |
| Callback unknown correlation | `CallbackCorrelationError` | 隔离 callback，告警，不改业务状态 | 待人工核对 | contract/security |
| Callback body/structure over limit | `CallbackPayloadBudgetExceeded` | ingress 拒绝且审计，不进入 reducer | 明确请求过大/结构异常 | contract/load/security |
| Callback contradiction | `ContradictoryAuthorityEvidence` | 保留双方事实，创建 reconciliation case | 冲突待处理 | integration |
| Shadow evaluator | `ShadowEvaluatorError` | 主路径继续，readiness 失败并重置 | 不影响动作，运维告警 | unit/integration |
| Shadow enqueue/consumer gap | `ShadowComparisonGap` | 主路径继续，expected/stored 不一致使窗口失效 | 不影响动作，运维告警 | integration/resilience |
| Comparison store | `ComparisonEvidenceUnavailable` | 主路径继续，中断窗口失效 | 运维告警，不能批准切换 | integration |
| Readiness generation | `ReadinessReportGenerationError` | 不生成/不批准报告 | go/no-go 被阻断 | unit/integration |

禁止 catch-all 后统一映射为 retryable。每个 adapter 必须把 transport phase、HTTP/协议码与 Provider evidence 映射到以上封闭分类；未识别异常转为 `WmsContractViolation` 并 fail closed。

## 8. Security & Data Protection

| Threat | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| 跨 WorkLine/租户读取 comparison/replay | Medium | High | 专用 RBAC、WorkLine/tenant 行级作用域、审计日志 |
| Replay 意外调用真实 WMS | Low | High | 纯 runner，无生产 adapter/credential/outbox/callback endpoint |
| 模拟 Provider 进入生产 | Low | High | 构建排除、启动 fail-fast、binding admission 三层阻断 |
| 高基数/敏感业务值进入指标 | Medium | Medium | 封闭低基数 label；禁止物料号、binding ID、业务 key |
| callback correlation 猜测或越权 | Medium | High | typed correlation、binding scope、签名/认证复用 Provider 合同、审计 |
| query 过滤导致无限分页/OOM | Medium | High | typed filters、分页合同、行/字节预算、deadline |
| evidence hash 被字典反推 | Medium | Medium | 用户明确选择普通 SHA-256；只能称假名化/弱遮蔽，不得声称匿名化 |

Comparison/replay/readiness 需要独立权限动作与审计：查看明细、运行 replay、生成报告、批准 go/no-go、暂停 binding/profile。详细 comparison evidence 保留 90 天；长期只保留不可还原聚合与完全合成/脱敏 fixture。

残余风险：普通 SHA-256 对低熵物料码、批次号等可被字典反推。不得把 hash 当匿名化，也不得将可枚举业务值公开共享；若未来跨信任边界导出，必须重新评审并优先改为带密钥摘要或不可逆聚合。

## 9. Failure Modes Registry

| Codepath | Failure mode | Rescued? | Test? | Consumer sees | Logged/evidence? |
| --- | --- | --- | --- | --- | --- |
| QUERY | nil/empty/wrong type | Yes | Yes | HOLD + named reason | Yes |
| QUERY | Provider empty inventory | Yes | Yes | policy decides REJECT/HOLD | Yes |
| QUERY | timeout/unavailable/429 | Yes | Yes | HOLD/retry | Yes |
| QUERY | malformed/over-budget | Yes | Yes | HOLD/contract failure | Yes |
| QUERY | evidence unavailable | Yes, fail closed | Yes | HOLD | Yes |
| POLICY | input/version mismatch | Yes | Yes | HOLD | Yes |
| SHADOW | evaluator error | Yes, production continues | Yes | no action change | Yes + readiness reset |
| SHADOW | comparison store outage | Yes, production continues | Yes | approval blocked | Yes |
| OUTBOX | duplicate workers | Yes | Yes | one external attempt | Yes |
| OUTBOX | worker crash before/after send | Yes | Yes | retry or UNKNOWN by phase | Yes |
| EFFECT | timeout/reset/ambiguous 5xx | Yes | Yes | UNKNOWN | Yes |
| EFFECT | evidence failure after possible acceptance | Yes | Yes | UNKNOWN | Yes |
| CALLBACK | duplicate/late/out-of-order | Yes | Yes | stable terminal or reconciling | Yes |
| CALLBACK | contradictory authority evidence | Yes | Yes | RECONCILING | Yes |
| OPERATIONS | queue saturation/provider slowdown | Yes | Yes | isolated backlog | Yes + alert |
| SECURITY | unauthorized comparison/replay | Yes | Yes | forbidden | Audit |
| PRODUCTION | simulator registration | Yes, startup blocked | Yes | service refuses start | Critical log |

本设计没有 `RESCUED=N + TEST=N + Silent` 行，**CRITICAL GAP：0**。

## 10. Test Review

独立测试计划产物：`/Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/aaronzhou79-develop-eng-review-test-plan-20260721-180146.md`。

```text
NEW DATA FLOWS
  QUERY request ─▶ Provider snapshot ─▶ pure policy ─▶ Plugin action
  shared snapshot ─▶ old evaluator + new policy ─▶ comparison ─▶ readiness
  EFFECT proposal ─▶ outbox ─▶ dispatch ─▶ receipt/callback ─▶ terminal/reconcile
  replay envelope ─▶ pure runner ─▶ deterministic result

NEW ASYNC WORK
  outbox batch claim · lease renewal/loss · fair dispatch · retry · callback merge

NEW EXTERNAL CALLS
  query_inventory · confirm_inbound · notify_pkg_binding · reconciliation query

NEW ERROR PATHS
  named input/transport/contract/evidence/lease/idempotency/callback/readiness errors
```

Reducer 与异步边界采用三层验证：纯 reducer 运行表驱动事件排列和 property invariant；真实 PostgreSQL integration 验证事务、唯一约束、`SKIP LOCKED` 与 fencing；resilience 测试在命名故障点注入 crash/reset/timeout。必须始终成立：相同 evidence 序列结果确定、terminal 不倒退、矛盾只进入 reconciliation、`AMBIGUOUS` 永不自动重发。

### 10.1 测试层级与目录

Phase 0 必须先生成现有测试 inventory，并逐项标记 `KEEP / REWRITE / DELETE`。`KEEP` 仅限仍成立的业务不变量；缓存、`cache_ttl_seconds`、`WmsEndpointConfig`、family-level `WmsTypedPortService`、RuntimeIntentLog 旧 dispatch 五态、`EXTERNAL_HTTP` bool sender、重复库存模型与 float 数量相关测试必须删除并由目标合同测试替换，不得通过兼容实现让旧测试继续通过。Completion gate 同时要求生产代码和测试代码中的旧符号引用归零。

| Coverage | Test type and placement |
| --- | --- |
| pure policy、request builder、state reducer、readiness calculator | unit：`tests/workline_runtime/` |
| capability input/output、evidence、Provider core exam | contract：`tests/contracts/` |
| Plugin allowlist/typed request/no WMS import | `tests/workline_plugins/` + architecture guardrail |
| PostgreSQL outbox claim、`SKIP LOCKED`、lease、partition、evidence transaction | explicit heavy：`tests/integration/` |
| worker crash before claim/after claim/before send/after send/before evidence | explicit heavy：`tests/resilience/` |
| full rough-sorter/query/effect/callback behavior | few E2E：`tests/e2e/` |
| row/byte budget、bulkhead、queue age、p99 | `tests/load/` |
| deterministic simulator/fault fixtures | `tests/mock/`，不得进入默认快速集 |

所有 Provider 运行同一不可覆写的参数化核心试卷，核心 case 不得 skip/xfail。真实 PostgreSQL integration 与 crash matrix 是出货门禁，不能只用 SQLite 或 mock 证明锁、事务与 lease。

### 10.2 确定性

观察窗口、lease、退避、TTL 与 callback 时序统一注入 Clock；测试用 fake clock 主动推进。Outbox worker、模拟 Provider、callback runner 提供确定性调度步骤和命名故障点。禁止真实 `sleep`、无种子随机和无界轮询；跨进程等待使用可观测状态与有限 deadline。

Friday-2am 测试：在真实 PostgreSQL 中让 worker 在“远端已接受、evidence 未提交”处崩溃，重启后 intent 必须进入 UNKNOWN/reconciliation，且远端只收到一次业务动作。

Hostile QA 测试：同一 correlation 先收到 success callback，再收到同步 reject 与不同 payload duplicate；系统必须保留所有 evidence、不得 last-write-wins、不得重复 effect。

Chaos 测试：单 Provider 持续 429/timeout、comparison store 中断、worker lease 被抢占；其他 Provider 继续派发，readiness 窗口失效且系统无静默丢单。

## 11. Performance、SLO 与运维

- Phase 1 QUERY 不跨请求缓存。先收集实际 QPS、Provider latency、限流与结果规模，再单独评审缓存；最终 policy 决策永不缓存。
- QUERY 必须在 body 解析前流式限制 wire/decoded bytes、允许的 encoding、压缩比、JSON 深度与单字段长度，并逐页累计总行数、总字节和 deadline；callback ingress 使用独立更小预算。超限命名失败，不截断、不无界聚合。
- Comparison 表默认月分区，90 天分区删除；只索引低基数分类、时间和 evidence reference，不复制 payload、不建 payload 通用索引；引用 evidence 的保留期至少覆盖 comparison 生命周期。
- Outbox 依赖 indexed scheduling columns 按 Provider profile + operation bulkhead，公平轮转并限制预取；单桶限流/积压不得占用其他桶领取额度。
- Metrics 标签仅包含封闭 capability/operation/provider profile/outcome/policy version；业务 key 只进入受控 evidence。
- Trace 从 Plugin execution 传播到 QUERY evidence、policy decision、RuntimeIntentLog、dispatch attempt、callback 与 reconciliation case。

SLO 采用平台底线 + operation/profile 预算。平台强制最低可用性、错误分类完整率、UNKNOWN 上限与 evidence 完整率；operation/profile 声明 latency、callback completion 与 Provider rate budget。Phase 0 必须将具体阈值、窗口、燃尽策略、dashboard panel、alert owner 和 Runbook 写入版本化 SLO catalog；缺失则 binding 不得激活。

Day-1 dashboard 至少包含：调用量/outcome、QUERY p50/p95/p99、evidence failure、shadow difference、readiness window、outbox queue age/backlog、rate limit、lease loss、UNKNOWN/reconciliation age、callback duplicate/contradiction。每个告警链接到对应 Runbook。

## 12. 未发布系统的实施、验证与删除顺序

```text
P0 contracts/ADR + legacy inventory
  │
  ▼
P1 generic QUERY + pure policy + conformance skeleton
  │
  ▼
P2 rough-sorter typed migration + decision evidence
  │
  ▼
P3 controlled shadow/readiness + simulator + SLO operations
  │  gate: 7d/1000 or low-volume waiver
  ▼
delete rough-sorter legacy capability/evaluator/routing
  │
  ▼
P4 confirm_inbound OUTBOX_ASYNC
  │
  ▼
P5 notify_pkg_binding callback/reconciliation
  │
  ▼
migrate every remaining legacy operation
  │  gate: inventory zero + generated report clean + tests green
  ▼
delete string port_method/effect_contracts path + clear dev/test data
```

不建设 N/N−1、旧 schema/状态读取、旧数据迁移、多版本 dispatcher 或兼容 alias。数据库 revision 仍使用 Alembic generator 创建，但只描述目标 schema；开发/测试环境可在明确范围内清理并重建数据。

部署/验证失败时：QUERY/policy 尚处临时验证阶段可切回验证 oracle；已经发送的 EFFECT 不允许用代码回退撤销或重发，必须根据冻结 evidence 进入 reconciliation。旧实现一旦通过删除门禁被删除，就不提供运行时回退，只修复目标实现。

### 12.1 Worktree parallelization strategy

先在主 feature worktree 完成并合并 T1/T2 合同基座；只有 catalog、operation identity 和 generated digest 稳定后才分叉后续 worktree，避免多个 lane 同时改写真源。

| Step/workstream | Modules touched | Depends on |
| --- | --- | --- |
| Foundation：T1 → T2 | `wms_integration/ports/`、`contracts/`、`runtime/system_capabilities/` | — |
| QUERY：T3 → T4 | `wms_integration/`、`runtime/capabilities/material_flow/` | Foundation |
| Provider quality：T5 | `tests/contracts/`、`tests/mock/`、replay/conformance | Foundation |
| Shadow/readiness：T6 | comparison/readiness、task consumer、`tests/integration/` | QUERY + Provider quality |
| EFFECT runtime：T8a → T8b → T8c → T8d → T8e → T8f → T8g → T9 → T10 | `runtime/orchestration/`、`sys/outbox`、WMS effect adapters | Foundation |
| First migration：T7 | rough-sorter Plugin/Capability/tests | QUERY + Provider quality + Shadow/readiness |
| Operations/removal：T11 → T12 | observability/security/docs + Phase 0 inventory modules | all prior lanes |

```text
Foundation（单 worktree）
       │
       ├─ Lane A: QUERY T3 → policy T4 ──────────────┐
       ├─ Lane B: Provider quality T5 ───────────────┼─▶ Shadow T6 ─▶ rough-sorter T7
       └─ Lane C: EFFECT T8a → … → T8g → T9 → T10 ──┘
                                                        │
                                                        ▼
                                              T11 operations → T12 removal
```

Lane A/B/C 可在 Foundation 合并后使用独立 worktree 并行；Lane A 与 B 都可能触碰 adapter/conformance fixture，必须由 Foundation 先锁定合同且避免同时修改同一文件。T6 等待 A+B，T7 等待 T3–T6，T12 严格最后执行。最终只在全部 lane 合并、legacy inventory 为零和完整门禁通过后合入 `develop`。

### 12.2 Implementation inline diagrams

以下实现文件应保留紧邻代码的短 ASCII 注释，解释权威和状态转移而非重复语法：

- `RuntimeIntentLog` model：语义账本状态与 `dispatch_key` 关联边界；
- `SystemOutbox` / `DispatchAttempt` model：transport status、lease/fencing 与 `AMBIGUOUS` 停发；
- Outbox dispatch service：bucket round-robin → `SKIP LOCKED` → typed transport result → reducer；
- comparison consumer/readiness service：expected/stored gap → window invalidation → immutable report；
- callback reducer：duplicate/late/contradictory evidence 的单调归并。

### 12.3 Retrospective learning

近期提交已多次处理 RuntimeInbox 单一事实源、过期测试清理和 WorkLine capability 平台收敛。本设计触及同一高风险区域，因此比普通新增功能更严格：禁止重新引入双账本 transport 状态，旧测试不能作为兼容理由，所有生成索引/配置/文档引用必须随 legacy path 同步归零。

## 13. 分期验收

### Phase 0：合同与全量盘点

- 完整列出 capability definition、generated index、Plugin allowlist、调用方、binding、测试、指标、文档与配置引用。
- 锁定 operation identity、typed contract、到现有 `DispatchEnvelope` 的最小 typed 映射、错误 taxonomy、Provider profile、replay envelope 和具体生成命令。
- 输出 legacy consumer inventory；每项有 owner、真实消费者、目标 operation 和删除门禁。
- Exit：不存在未决架构项；新合同无工作线字段或 Provider DTO。

### Phase 1：QUERY 与准入

- 通用 `query_inventory` QUERY、纯 admission policy、decision evidence。
- 真实 Provider、模拟 Provider、replay factory 通过同一核心试卷。
- 粗分机只构造 typed request 并选择本线动作。
- Exit：readiness 通过；旧粗分机 capability/evaluator/routing 删除；无兼容 alias。

### Phase 2：`confirm_inbound`

- typed EFFECT、冻结 dispatch spec、outbox lease、幂等、UNKNOWN、reconciliation。
- Exit：重复执行不会重复外部事务；所有终态可从 evidence 重建。

### Phase 3：`notify_pkg_binding`

- typed EFFECT、callback correlation、接受/完成边界、迟到/乱序归并。
- Exit：callback 先于响应、重复、矛盾、超时后成功均有唯一 reducer 结果与 Runbook。

### Completion：Legacy 归零

- 逐 operation 迁移 Phase 0 inventory 其余消费者。
- 删除字符串式 `port_method` / `effect_contracts` 路径、无调用代码、无生成引用、无配置引用。
- 清理开发/测试旧数据；架构 guardrail 阻止重新引入自由字符串 WMS dispatch。

## 14. NOT in scope

- 不把 WMS 库存、单据或主数据复制为 WES 权威事实。
- 不新增通用规则引擎、动态编排 DSL 或运行时 capability recipe。
- 不直接接入或调度 RCS、AGV、CTU；WMS 保持北向履约入口。
- 不为无真实消费者的 operation 预建空壳。
- 不保留旧 capability alias、旧 schema/状态、历史数据迁移、多版本 dispatcher 或永久双运行。
- 不把模拟 Provider 建成独立产品；没有测试证明前不新增服务或容器。
- 本设计无 UI scope；Design & UX Review 评估完成并跳过。

## 15. Dream state delta

完成本设计后，新增 WorkLine 可从生成 catalog 选择稳定 operation，新增 Provider 可通过统一试卷获得可执行兼容结论，现场可用 decision evidence、trace 和 readiness report 解释每个 HOLD/REJECT/UNKNOWN。距离 12 个月理想状态仍有两项自然演进，但现在不预建：真实负载证明需要的 QUERY 缓存，以及真实跨进程联调证明需要的独立模拟服务。

可逆性评分：**4/5（删除旧路径前）**，因为 routing 和纯 policy 可快速替换；**3/5（删除后）**，因为不保留旧版本，但 typed contract、replay corpus、evidence 和确定性测试使修复仍可控。EFFECT 的远端副作用天然不可逆，必须依靠幂等与 reconciliation，而不是代码回滚。

## 16. Stale Diagram Audit

| Diagram | Location | Status |
| --- | --- | --- |
| 原双层目录边界图 | 本文旧版第 2 节 | 已替换；旧图错误暗示 `material_flow → WMS` I/O 依赖 |
| 目标架构图 | 本文第 4 节 | 已按 Runtime QUERY/EFFECT 编排更新 |
| QUERY 四路径图 | 本文第 5.2 节 | 新增，覆盖 nil/empty/success/error/evidence failure |
| EFFECT 状态机 | 本文第 5.4 节 | 新增，覆盖 retry/UNKNOWN/conflict/reconciliation |
| Shadow 数据流 | 本文第 6.1 节 | 新增，明确单次 WMS 查询与无业务副作用 |
| 实施与删除序列 | 本文第 12 节 | 新增，反映未发布系统零兼容策略 |

未修改其他架构文档中的图；工程实施如改变 `docs/architecture/target-state-contract.md` 所述边界，必须在同一变更中更新对应图。

## 17. Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above. Run with Claude Code or Codex; checkbox as you ship.

- [ ] **T1（P1，human: ~4h / CC: ~30min）** — contracts — 完成 legacy consumer inventory 与 operation ADR
  - Surfaced by: Code Quality / Test / Long-Term — 字符串式 WMS 调用与旧测试合同必须在本设计内归零。
  - Files: `src/app/runtime/capabilities/material_flow/`、`src/app/runtime/system_capabilities/`、Plugin definitions、现有测试 inventory、设计 ADR。
  - Verify: inventory 覆盖调用方、binding、生成索引、测试、指标与文档；每项有目标 identity，测试逐项标记 `KEEP/REWRITE/DELETE`。
- [ ] **T2（P1，human: ~1d / CC: ~2h）** — capability contracts — 建立独立 typed operation 合同，并由每个 EFFECT 的领域 gateway 映射到现有 `DispatchEnvelope`
  - Surfaced by: Architecture — 禁止 family god handler、自由字符串 dispatch，以及把 transport 职责塞进高影响 Definition。
  - Files: `src/app/wms_integration/ports/` 的唯一 operation 合同、`src/app/runtime/system_capabilities/wms/`、对应领域 gateway、`ExternalContractProfile` identity、`WmsOperationContract`、`InboundCallbackContract`、conformance manifest、generated index；`SystemCapabilityDefinition` 不新增 dispatch 字段。
  - Verify: contract/architecture tests 证明 Capability/Port 无重复 operation schema、数量全程 `Decimal`、Provider DTO 只映射一次且不伪造缺省值；每个 operation 独立、Definition identity 不包含 transport metadata、运行时 Profile 无 fixture/cache/字符串 Port.method 字段、production operation 不允许 outbound auth `NONE`。
- [ ] **T3（P1，human: ~1d / CC: ~2h）** — inventory QUERY — 实现通用查询、预算、typed snapshot 与 fail-closed evidence，并删除现有跨请求缓存路径
  - Surfaced by: Architecture / Code Quality / Error Map / Performance — empty success、预算超限不截断；统一封闭领域 outcome；全局删除 `WmsQueryCacheService`、TTL 配置、缓存测试与文档，不保留调用方特例或兼容分支。
  - Files: WMS shared QUERY outcome、无 operation 分支的 transport executor、inventory Port/adapter、QUERY capability、evidence；删除 family-level `WmsTypedPortService` 方法与特殊 payload 分支。
  - Verify: success/empty/business reject/timeout/429/malformed/pagination/precision/over-budget/evidence failure/unexpected exception contract tests；包括伪造/缺失 `Content-Length`、wire/decoded chunk 越界、异常压缩比、unsupported encoding、JSON 深度/字段长度、分页累计越界与 deadline；guardrail 阻止恢复 operation-specific 异常梯子、family operation switch、重复 transport service 或 catch-all retryable。
- [ ] **T4（P1，human: ~1d / CC: ~2h）** — material-flow — 提取纯 admission policy 与 decision provenance
  - Surfaced by: Architecture / Debuggability — 业务判定必须无 I/O、可解释、可重放。
  - Files: `src/app/runtime/capabilities/material_flow/`、rough-sorter typed contracts。
  - Verify: 表驱动测试覆盖 ADMIT/REJECT/HOLD、nil/empty/version mismatch。
- [ ] **T5（P1，human: ~2d / CC: ~4h）** — provider quality — 建立不可覆写 conformance suite、纯 replay runner 与最小模拟 Provider
  - Surfaced by: Test / Security — 所有 Provider 使用同一张试卷，模拟器三层禁止生产。
  - Files: `tests/contracts/`、`tests/mock/`、Provider profile/adapters、replay。
  - Verify: CI 中真实 adapter、simulator、replay factory 全部通过 scripted provider 核心参数集且无 skip/xfail；staging live conformance 生成可验证、不可变报告，所有非 staging runner 无生产 endpoint/credential 访问能力。
- [ ] **T6（P1，human: ~2d / CC: ~4h）** — shadow/readiness — 实现 comparison 分区存储、不可变报告与删除门禁
  - Surfaced by: Data Flow / Observability / Performance — shadow 失败不改动作但必须重置窗口。
  - Files: QUERY evidence shadow eligibility/version/comparison key、bounded pure evaluator、现有 task queue producer/consumer、comparison model/repository/service、readiness generator、Alembic revision、现有定时任务中的 partition maintainer。
  - Verify: expected 集合只能从 durable evidence 派生，生产响应不等待 store；在 evidence commit 前后、enqueue 前后注入 crash，任何缺口均可见；enqueue/consumer/store outage、expected/stored gap、evaluator error、version change 均重置窗口；预建当前+未来 3 个月、缺失分区 fail/readiness invalid、跨月并发写、90 天在线 drop 与 lock timeout；审批引用 report ID。
- [ ] **T7（P1，human: ~1d / CC: ~2h）** — rough sorter — 迁移首个 QUERY 切片并删除专用 capability
  - Surfaced by: Deployment / Zero Compatibility — 旧 evaluator 只能临时验证，不得进入可发布代码。
  - Files: rough-sorter Plugin、旧专用 capability、generated index、相关 tests。
  - Verify: readiness gate 通过；旧 identity、routing、binding、生成引用和 import 全部归零。
- [ ] **T8a（P1，human: ~1d / CC: ~2h）** — EFFECT state contract — 落地双账本最终枚举、reducer event 与 schema
  - Surfaced by: Architecture / Outside voice — 持久化状态、事件和 outcome 必须有唯一归属。
  - Files: RuntimeIntentLog、SystemOutbox、DispatchAttempt、repository、Alembic revision。
  - Verify: 转移矩阵 contract tests；双侧 `dispatch_key` 唯一且同事务 1:1；旧 transport 字段归零。
- [ ] **T8b（P1，human: ~1d / CC: ~2h）** — canonical dispatch — 持久化 canonical payload bytes 并统一 hash/签名/body
  - Surfaced by: Code Quality / Outside voice — JSONB 重新序列化不能保证原字节。
  - Files: 领域 gateway canonical serializer、DispatchEnvelope EXTERNAL_HTTP 分支、SystemOutbox、repository。
  - Verify: 数据库往返逐字节一致；retry 不读取 `payload_json`，同一 bytes 决定 hash/签名/HTTP body。
- [ ] **T8c（P1，human: ~1d / CC: ~2h）** — typed sender — 以 typed transport result 替换全部 `EXTERNAL_HTTP` 布尔合同
  - Surfaced by: Architecture / Error Map — 仅 `NOT_SENT` 可重试，`AMBIGUOUS` 必须停发。
  - Files: `outbox_delivery.py`、`outbox_engine.py`、Outbox dispatch service、DispatchAttempt evidence。
  - Verify: NOT_SENT/ACCEPTED/AMBIGUOUS/explicit reject mapping；timeout/reset/ambiguous 5xx 不重复发送。
- [ ] **T8d（P1，human: ~1d / CC: ~2h）** — effect reducer — 实现 transport/callback/reconciliation 的单调 reducer
  - Surfaced by: Architecture / Test — terminal 不倒退，矛盾 evidence 只进入 reconciliation。
  - Files: effect reducer、RuntimeIntentLog service、独立 ReconciliationCase、callback bridge/reconciliation policy。
  - Verify: 表驱动排列 + property invariants；terminal 永不改写，ordinary callback 不能关闭 OPEN case，只有 `RECONCILIATION_RESOLVED` 可关闭并推进未终态 intent；duplicate/late/contradictory matrix。
- [ ] **T8e（P1，human: ~1.5d / CC: ~3h）** — dispatch concurrency — 实现 lease/fencing 与 indexed fair-bucket 调度
  - Surfaced by: Architecture / Performance — Provider 桶隔离且 lease 丢失后旧 worker 不得提交。
  - Files: SystemOutbox scheduling columns、repository/dispatcher、DispatchAttempt。
  - Verify: 真实 PostgreSQL `SKIP LOCKED`/lease/fair-bucket tests；单桶限流不饿死其他桶。
- [ ] **T8f（P1，human: ~1d / CC: ~2h）** — frozen auth target — 冻结 target snapshot 与版本化 credential reference
  - Surfaced by: Security / Outside voice — 重试不得读取最新 endpoint 或自动替换轮换密钥。
  - Files: Provider/binding profile、OutboundAuthProfile、secret provider boundary、SystemOutbox snapshot。
  - Verify: rotation 只影响新 intent；旧 key 撤销停止发送；secret/header 不进入日志/evidence。
- [ ] **T8g（P1，human: ~1d / CC: ~2h）** — effect resilience — 完成发送与 evidence 边界 crash matrix
  - Surfaced by: Deployment / Test — 外部已接受但本地未知必须进入 UNKNOWN，禁止盲重发。
  - Files: `tests/integration/`、`tests/resilience/`、fault injection hooks。
  - Verify: before/after claim/send/evidence、lease loss、unexpected exception 全矩阵；每个前置提交保持构建和对应测试通过。
- [ ] **T9（P1，human: ~2d / CC: ~4h）** — inventory transaction — 迁移 `confirm_inbound`
  - Surfaced by: EFFECT lifecycle — 幂等、拒绝、UNKNOWN 与 reconciliation 必须可证明。
  - Files: inventory transaction capability/Port adapter、consumer、integration/resilience tests。
  - Verify: 重复执行不重复外部事务；所有 terminal/unknown evidence 可重建。
- [ ] **T10（P1，human: ~2d / CC: ~4h）** — fulfillment — 迁移 `notify_pkg_binding` callback reducer
  - Surfaced by: Data Flow / Error Map — callback 可重复、乱序、迟到且与同步结果矛盾。
  - Files: fulfillment capability/Port adapter、callback/reconciliation、tests。
  - Verify: callback-before-response、duplicate、contradiction、timeout-then-success 全矩阵。
- [ ] **T11（P1，human: ~2d / CC: ~4h）** — observability/security — 交付 SLO catalog、dashboard、alert、Runbook 与受限运维入口
  - Surfaced by: Security / Observability — readiness/replay 需要 RBAC、租户作用域和可复核证据。
  - Files: metrics/tracing、RBAC、audit、`OutboundAuthProfile`/secret provider 边界、operational docs。
  - Verify: 低基数 label guardrail、跨租户拒绝、trace 全链路、每个告警链接 Runbook，以及日志/evidence 不包含 secret 或完整认证 header。
- [ ] **T12（P1，估时由 T1 inventory 生成）** — legacy removal meta gate — 按真实 operation 展开迁移任务并完成全量归零
  - Surfaced by: User constraint / Outside voice / Long-Term — inventory 未知前不得伪造“全部剩余 operation”固定估时；未发布系统不得保留兼容机制或延期清理。
  - Files: T1 inventory 所列每个消费者、`sorter_inbound_runtime_service.py` 等旧 dispatcher、guardrails；每个真实 operation 生成独立任务、依赖、human/CC 估时与验证命令。
  - Verify: 所有展开任务完成；生产与测试代码中的 `port_method` / `effect_contracts` / cache/旧 endpoint/旧 dispatch 状态/boolean sender 引用为 0；删除 `WmsEndpointConfig`、WMS EndpointRegistry 重复条目与 Provider profile 字符串 Port.method 清单；生成报告干净，开发/测试旧数据清理，架构测试阻止回归。

## 18. Review Completion Summary

| Area | Result |
| --- | --- |
| Mode | FULL_REVIEW；完整目标保留，复用现有平台并以零兼容门禁整体收敛 |
| Step 0 | scope accepted as complete target；Foundation 后使用隔离 worktree/lane 实施 |
| Architecture | 8 个问题全部解决；operation typed capability、QUERY/EFFECT 分离、双账本权威与冻结执行边界明确 |
| Code quality | 7 个问题全部解决；DRY/KISS/SOLID/YAGNI、唯一模型/outcome/catalog/serialization 边界成为硬门禁 |
| Test review | 覆盖图与独立测试计划已生成；3 个 gap 全部解决；真实 PostgreSQL、resilience、双级 conformance |
| Performance | 4 个问题全部解决；流式双字节预算、公平桶、异步 comparison、引用式存储与分区生命周期 |
| Error & rescue | 23 个命名错误/路径；0 个 silent critical gap |
| Security | 7 类威胁；普通 SHA-256 保留 1 项已接受残余风险 |
| Data/interaction | QUERY 四路径、shadow、outbox/callback 竞态均已映射 |
| Observability | 不可变 readiness report 为权威；SLO/dashboard/alert/runbook 入范围 |
| Deployment | 未发布系统直接收敛目标 schema/contract；不做 N/N−1 或旧数据迁移 |
| Long-term | 删除前可逆性 4/5，删除后 3/5；legacy 清理不得延期 |
| Design & UX | SKIPPED：无 UI scope |
| What already exists / NOT in scope | 已写；复用 RuntimeIntent/SystemOutbox/WMS evidence，明确不建 DSL、平行 dispatcher 或兼容层 |
| TODO | 新增 0；更新既有统一运营 TODO 的边界，北向 capability 观测由本设计交付 |
| Outside voice | Claude subagent 两轮对抗复核；10 个发现经用户批准全部修复，最终 PASS |
| Parallelization | Foundation 后 3 个并行 lane；Shadow/首迁移/归零门禁按依赖顺序执行 |
| Implementation tasks | 18 条 JSONL 任务；T8 拆为 T8a–T8g，T12 由 T1 inventory 动态展开 |
| Lake Score | 22/22 个工程问题均选择完整方案 |
| Diagrams | 8：架构、QUERY、双账本/EFFECT、shadow、测试覆盖、实施删除、worktree lanes、状态归并 |
| Unresolved decisions | 0 |

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR | 6 proposals, 6 accepted, 0 deferred；0 critical gaps |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | 7 天内无独立 Codex diff review |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (PLAN) | 22 issues, 0 critical gaps；全部决策已写回 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | SKIPPED | 无 UI scope |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 7 天内无评审 |

**VERDICT:** CEO + ENG CLEARED — ready to implement。

NO UNRESOLVED DECISIONS
