# 北向能力简化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 v0.19.0.0 的北向能力收敛到“一厂一 Provider、若干 typed operation”的当前交付模型，以“幂等提交 + 按幂等键查询状态”作为 EFFECT 的可靠性闭环，删除尚未上线阶段不产生收益的动态 Provider、生产 shadow/readiness 和签名验收基础设施。

**Architecture:** WES 只维护与 WMS 的交互边界，不建模 WMS 内部工作流。QUERY 保持 typed Port、资源预算、证据和纯策略；EFFECT 通过现有 Intent/Outbox 幂等提交，提交结果不明确或未终结时统一查询 WMS 状态，callback 仅用于提前触发查询。Provider、endpoint、contract、auth 在部署时确定，运行时不存在多 Provider 切换。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、PostgreSQL/TimescaleDB、Celery、Pydantic、pytest、Alembic、GitNexus。

## 全局约束

- 当前系统和 WMS 均未上线，无监管留存要求；允许清理开发/联调数据后整体切换，不保留旧 schema、旧调用方式、双写或兼容 dispatcher。
- 完整 QUERY/EFFECT smoke 在联调环境完成并终结后再清理测试数据。正式切换中第一笔真实 EFFECT 是不可逆点：此前确认双方无 EFFECT 记录时可回退部署/migration；此后禁止 schema downgrade 或回退到不支持新状态账本的版本，只能停止新 EFFECT、保留确认 Worker 并 forward-fix/对账。
- 保持 `API → Service → Repository → Database`，callback API 只能调用 Service，状态轮询的数据访问只能落在 Repository。
- 保留 typed operation、Outbox、dispatch key、幂等键、canonical payload、冻结 binding、错误分类、观测和人工对账。这些是可靠性核心，不属于待删除的过度设计。
- 每个工厂部署始终只对接一个 WMS Provider，不支持多个不同 Provider 的 catalog、路由、切换或故障转移；环境差异由部署配置解决。
- active profile 只用于创建新 Intent。存量 Intent 继续使用创建时冻结的同一 WMS Provider endpoint/auth revision；保留历史 revision 是完成在途请求的协议要求，不代表运行时存在多个 Provider。
- WMS 状态 endpoint、跨系统 SLA 承诺和 WES 轮询预算统一由现有 Pydantic Settings 加载，并通过 `.env` profile 与 Docker Compose 同时注入 API/Celery；adapter、task 和 operation contract 禁止持有工厂相关默认值。
- Tasks 2–9 的当前开发进入条件是 Task 1 的仓内 WMS Mock 可行性门禁为 `GO`：实际 Docker Compose
  `mock_wms` 必须通过真实 TCP 黑盒关键语义探针。外部 WMS 的书面确认与目标环境联调仍是生产切换门禁，
  不阻塞当前 Mock 能力开发；任一适用门禁为 `NO-GO` 时先修订合同/ADR。
- callback 是可选加速信号，不携带终态权威。所有 `COMPLETED`、`REJECTED` 终态必须来自 WMS 状态查询响应。
- `COMPLETED` 状态必须携带 operation-specific `result_payload`，并按冻结 operation contract 的既有 `result_model` 严格校验后才能进入 reducer；`REJECTED` 只携带稳定 `reason_code`。原始或校验失败的 payload 不得作为业务结果持久化。
- 状态查询只通过 reducer 推进 RuntimeIntentLog，并按需打开或更新 ReconciliationCase。SystemOutbox 与 DispatchAttempt 只记录 transport 事实，既有 `SENT`、`UNKNOWN`、`FAILED` 终态不得被业务查询结果改写。
- 同一 `operation_identity + idempotency_key` 的 WMS `source_version` 必须是单调递增整数，并作为状态快照排序依据；`updated_at` 只用于展示和审计，不参与跨系统先后判断。
- WMS 幂等记录与状态结果的保留期必须不短于 WES 最大确认窗口加安全余量；提交受理后的状态可见性 SLA 不得超过 WES 的 `NOT_FOUND` 宽限期。双方在部署配置和联调验收报告中冻结具体值。
- 仅当状态查询从未观察到可见状态、持续 `NOT_FOUND` 超过已验收的可见性宽限期时，允许一次受控同键重提：复用原 operation identity、idempotency key、canonical payload 和冻结 binding，仅刷新 nonce、时间戳与签名。再次 `NOT_FOUND`、合同冲突或预算耗尽必须进入人工对账，禁止生成新幂等键。
- 修改任何函数、类或方法前，对具体符号运行 GitNexus upstream impact。`WmsProviderProfile`、`AttemptWriteSet` 为 HIGH，`SystemOutbox` 为 CRITICAL；若实施需要修改这些符号，先向用户报告影响范围并获得确认。
- `SystemOutbox` 只允许新增不可变的 `idempotency_key` 请求元数据，用于保证重试和进程重启后仍发送同一幂等键；不得承载 WMS 业务状态或查询结果。transport retry 与 EFFECT 语义确认继续分账。RuntimeIntentLog 显式持久化状态查询首次时间、下次时间、次数和独立 claim lease，使退避、宽限期、耗尽预算与 Worker 崩溃恢复均有持久化依据。
- WMS EFFECT 提交必须通过受控 header 携带 `operation_identity` 与 `idempotency_key`，两者都进入 HMAC canonical signing input；业务 JSON body 继续只承载 typed operation payload。提交与后续状态查询必须使用同一对持久化关联键，禁止从 dispatch key 或 payload 临时重算。
- 创建 WMS EFFECT Intent 时必须同时冻结 typed、带 hash 的状态查询 binding snapshot，包含单一 Provider identity/profile hash、binding revision、状态 target、auth scheme 和版本化 credential reference。状态查询不得重新解析当前 active profile。
- 状态查询 claim 必须先独立提交，再在数据库事务外调用 WMS；查询结果只允许由持有当前 lease token 的 Worker 写回，禁止数据库事务或行锁跨越网络请求。
- 状态查询先采用可配置小批量、批内顺序执行、指数退避加 jitter，并尊重 WMS `Retry-After` 与现有 circuit breaker；上线前不新增 Provider 级分布式限流器，后续只由真实 backlog/QPS/429 指标驱动升级。
- 每次提交前运行 GitNexus detect changes，只暂存本任务文件；不得包含当前工作区已有的 `AGENTS.md`、`CLAUDE.md` 修改。
- 每个任务提交必须保持质量门禁和已纳入范围的测试为绿色；禁止提交 intentional failing test，禁止用长期 `xfail` 代替尚未实现的 architecture guardrail。新断言与使其成立的实现放在同一任务提交。
- 新 migration 必须由 `uv run alembic revision -m "<task-specific message>"` 生成随机 revision ID，禁止手写 revision ID；每个任务使用准确、独立的 message，不把无关 schema 变化合并到同一 revision。
- 新增、移动或删除测试时遵守 `tests/README.md`；默认快速测试与 integration、resilience、mock 重测试保持隔离。
- 保留有价值注释；行为变化时同步修改注释中的设计理由。

## 根因与目标边界

### 已验证事实

- v0.19.0.0 一次性引入约 451 个文件变更，包含生产 shadow/readiness、动态环境 profile、签名 conformance attestation 和 callback 权威链路。
- `BoundedQueryShadowEvaluator` 没有生产构造点，shadow comparison 没有生产 enqueue 调用；但数据库表、Repository、Celery consumer、分区维护任务和 readiness API 已完整建设。
- `WmsProviderProfile.validate_composition()` 强制每个 EFFECT 恰好一个 callback，而代码中不存在统一的 EFFECT 状态查询 Port。
- `provider_catalog.py` 同时构建 sandbox、staging、production 三套 profile，但当前发布模型是每个工厂部署一个 Provider。
- 三个 EFFECT operation 的 callback adapter、effect adapter 和 preparation service 大量重复，差异主要是 operation identity 与类型名。
- 现有相关基线测试通过：`32 passed`。因此问题是需求与架构边界不匹配，不是当前代码无法自洽。

### 保留与删除

| 保留 | 删除或收敛 |
| --- | --- |
| 每个 operation 的 typed request/result、definition、handler、gateway | 运行时 sandbox/staging/production profile map |
| QUERY 预算、分页、evidence、纯策略 | 生产 shadow evidence、readiness、分区维护 |
| Intent、Outbox、dispatch key、canonical payload | Ed25519 staging attestation、生产 trust root |
| 幂等提交、transport retry、状态 reducer、人工对账 | 每个 EFFECT 必须配置 callback 的约束 |
| 单个部署生效的 Provider 配置 | 三套重复 callback adapter |
| 模拟 Provider 的测试夹具 | 已复制但没有领域差异的 effect preparation 基础设施 |

### EFFECT 目标状态流

```text
WES Intent/Outbox
  └─ 幂等提交到 WMS
       ├─ 明确未发送：按现有 transport policy 有界重试
       ├─ 已受理或结果不明确：按 operation_identity + idempotency_key 查询状态
       │    ├─ ACCEPTED / PROCESSING：保持非终态，稍后再次查询
       │    ├─ COMPLETED / REJECTED：由 reducer 写入 RuntimeIntentLog 语义终态
       │    ├─ NOT_FOUND 且仍在宽限期：只重查
       │    ├─ 持续 NOT_FOUND 超过宽限期：同键、同 payload 受控重提一次
       │    └─ 查询耗尽 / 合同冲突：打开 ReconciliationCase
       └─ 可选 callback：校验认证与关联键后，仅触发一次即时状态查询
```

## 文件结构规划

### 新增

- `docs/contracts/wms-northbound-interaction-contract.md`：交付给 WMS 团队的强制能力、字段、错误语义、验收用例。
- `docs/architecture/adr/2026-07-24-northbound-interaction-simplification.md`：记录从 shadow/callback 权威模型收敛到联调验收/状态查询模型的决策。
- `docs/operations/wms-northbound-feasibility-report.md`：记录 WMS 团队确认、stub 构建版本、关键语义探针证据和 Task 1 `GO/NO-GO` 结论。
- `scripts/verify_wms_northbound_feasibility.py`：只面向 WMS 最小联调 stub 的黑盒合同探针，不进入生产运行时。
- `src/app/wms_integration/ports/effect_status.py`：统一状态查询请求、快照和 Port。
- `src/app/wms_integration/adapters/effect_status_query_adapter.py`：通过现有 HTTP transport 调用部署中的 WMS 状态查询端点。
- `src/app/runtime/orchestration/services/wms_effect_status_service.py`：协调状态查询、reducer 和重查/对账决策。
- `src/app/runtime/orchestration/repositories/wms_effect_status_repository.py`：只负责查找待确认 Intent/Outbox 及持久化轮询结果。
- `src/app/runtime/orchestration/services/wms_effect_preparation_service.py`：承载三个 operation 完全一致的 EFFECT preparation/Outbox 组装流程。
- `tests/contracts/wms_integration/test_effect_status_contract.py`：WMS 状态合同单元测试。
- `tests/workline_runtime/system_capabilities/test_wms_effect_status_service.py`：状态流和失败路径测试。
- `tests/integration/workline_capabilities/test_wms_effect_status_postgresql.py`：真实事务下的重复查询、终态和对账测试。
- 由 Alembic 生成的 migration：为 RuntimeIntentLog 增加状态查询调度字段和到期扫描组合索引。
- 由 Alembic 生成的 migration：为 SystemOutbox 增加不可变的幂等请求键。
- 由 Alembic 生成的 migration：删除 shadow/readiness 表、分区函数及相关索引。

### 修改

- `src/app/runtime/system_capabilities/wms/contracts.py`：单 Provider 组合规则；EFFECT 必须有状态查询，callback 改为可选。
- `src/app/runtime/system_capabilities/wms/provider_catalog.py`：只构建当前部署 profile，不再生成三环境运行时目录。
- `src/app/runtime/system_capabilities/wms/effect_binding.py`：保持冻结 binding 语义，移除对动态 profile map 的依赖。
- `src/app/wms_integration/runtime_factory.py`：装配单个部署 Provider 和状态查询 adapter。
- `src/app/wms_integration/ports/__init__.py`、`src/app/wms_integration/adapters/__init__.py`：导出新 Port/adapter。
- `src/app/sys/models/outbox.py`、`src/app/sys/canonical_dispatch.py`、`src/app/sys/services/outbox_engine.py`：持久化并封装 EFFECT 幂等请求元数据，将受控关联 header 纳入 canonical 签名。
- `src/core/conf.py`、`.env.dev`、`.env.test`、`.env.prod`、`docker-compose.yml` 及部署 compose：定义并透传 WMS 状态 endpoint、SLA 承诺和 WES 状态确认预算，保证 API/Celery 配置一致。
- `src/app/runtime/orchestration/effect_state_contract.py`、`src/app/runtime/orchestration/effect_bridges.py`：加入状态查询事件，移除 callback 直接终结。
- `src/app/runtime/orchestration/services/inbox/wms_typed_effect_callback_router.py`：callback 只做认证、关联和即时查询调度。
- `src/app/runtime/orchestration/runtime_intent.py`、`src/app/runtime/orchestration/runtime_intent_effects.py`：用查询快照推进 EFFECT 状态。
- `src/app/runtime/orchestration/runtime_intent_log.py`：持久化状态查询首次时间、下次时间、次数、最后应用的 WMS source version、claim lease，以及同一 WMS Provider 的 immutable status binding snapshot/hash；该快照只表达历史 revision，不承担 Provider 选择。
- `src/celery_app/tasks/workline.py`、`src/celery_app/config.py`：增加状态查询即时任务与兜底扫描，删除 shadow 任务。
- `src/app/runtime/workline_plugins/attempt_coordinator.py`：移除从未被生产赋值的 shadow write set。
- `src/app/runtime/system_capabilities/evidence.py`：移除始终为 `None` 的 `shadow_expected`。
- `src/app/runtime/orchestration/repositories/northbound_operations_repository.py`、`src/app/runtime/orchestration/services/query/northbound_operations_query_service.py`：移除 readiness 投影。
- 三个 operation 的 `effect_adapter.py`、`intent_adapter.py` 及 `__init__.py`：接入共享 preparation；保留 operation 特有 payload 映射。
- `docs/superpowers/specs/2026-07-21-northbound-capability-extraction-design.md`：标记被本 ADR 部分取代。
- `docs/architecture/target-state-contract.md`、`docs/contracts/external-contract-profile.md`、`docs/contracts/observability-contract.md`、`docs/operations/northbound-operation-slo-catalog.md`、`docs/runbooks/northbound-operation-observability.md`：同步目标合同、指标和排障方式。

### 删除

- `src/app/runtime/system_capabilities/shadow_models.py`
- `src/app/runtime/system_capabilities/shadow_partitioning.py`
- `src/app/runtime/system_capabilities/shadow_readiness.py`
- `src/app/runtime/system_capabilities/shadow_repository.py`
- `src/app/runtime/system_capabilities/shadow_service.py`
- `src/app/runtime/system_capabilities/wms/conformance_trust_root.py`
- 三个 operation 的 `callback_adapter.py`
- 只验证 shadow/readiness、签名 attestation、动态多 profile 的测试和 fixture；有价值的 typed contract/conformance case 改写后保留。

---

### Task 1：冻结最小北向合同与架构决策

**验收状态：已完成。** 2026-07-25 已显式构建 Docker image
WMS `sha256:b3fc373dc9531e39a6731851d6bb5b208c5f29199c7446c1945693d9208a45c8` 与 ECS
`sha256:3c2ef80df6325ef8b83a6f4ec850edddad4629f0e28ba33c488f1b21d65b8a61`，共享镜像双入口 smoke
`2 passed`；实际 Compose `mock_wms` 通过三个 typed EFFECT 的真实 TCP 黑盒探针：heavy/live pytest
`3 passed`，CLI 46 case 全部 `passed=true`。
未来外部 WMS 联调模板保留，但不再构成当前 P0 进入门禁。

**Files:**

- Create: `docs/contracts/wms-northbound-interaction-contract.md`
- Create: `docs/architecture/adr/2026-07-24-northbound-interaction-simplification.md`
- Modify: `docs/superpowers/specs/2026-07-21-northbound-capability-extraction-design.md`
- Test: `tests/contracts/wms_integration/test_wms_northbound_feasibility_probe.py`

**Step 1: 锁定绿色基线与后续 guardrail 归属**

先运行现有相关 architecture/contract tests 并记录绿色基线。本任务不提交预期失败的 architecture test；以下断言由使其成立的实现任务负责：

- Task 2：每个 EFFECT 声明状态查询能力。
- Task 4：callback 可以不存在，且 callback router 不得直接写 `COMPLETED`/`REJECTED`。
- Task 5：一个部署只有一个 active Provider profile，不存在多 Provider catalog 或 runtime selector。
- Task 6：生产源码不再引用 shadow readiness；Task 5 同时覆盖 conformance trust root/profile map 的归零。

运行：

```bash
uv run pytest tests/architecture/test_northbound_wms_typed_operation_boundaries.py -q
```

预期：现有基线通过；本任务不修改该文件。

**Step 2: 编写 WMS 团队交付合同**

文档必须明确：

- 提交请求必须接受 `operation_identity` 和 `idempotency_key`。
- 同一幂等键与同一 canonical payload 已完成时返回原业务结果；仍在处理中时返回 HTTP 409 和稳定错误码 `IDEMPOTENCY_REQUEST_IN_PROGRESS`。
- 同一幂等键配不同 canonical payload fingerprint 时返回 HTTP 422 和稳定错误码 `IDEMPOTENCY_CONFLICT`，WES 立即进入人工对账，不将其作为暂时并发重试。
- 状态查询请求键为 `operation_identity + idempotency_key`。
- 状态枚举固定为 `ACCEPTED | PROCESSING | COMPLETED | REJECTED | NOT_FOUND`。
- 响应包含 `provider_reference`、`reason_code`、`updated_at`、`source_version`，可空性和格式写清楚；可见状态的 `source_version` 是同一查询键下从零或正整数开始、新状态严格递增的权威序号，幂等重放保留原版本；`NOT_FOUND` 的 `source_version` 必须为空，`updated_at` 不承担排序语义。
- `COMPLETED` 必须包含 `result_payload`，其 schema 由 `operation_identity` 对应的 WES result model 冻结；结果中的 dispatch/correlation 字段必须与原请求一致，`accepted` 必须为 `true`，内外层 source version 如同时存在必须规范化后一致。`REJECTED` 必须包含稳定 `reason_code` 且不得伪造成功结果；其他非终态不得携带可被误认作最终结果的 payload。
- WMS 对幂等记录和状态结果的最小保留期必须满足 `WMS retention >= WES max confirmation age + safety margin`；保留期内不得让同一幂等键重新生效。
- WMS 从提交受理到状态可查询的最大可见性延迟必须满足 `WMS visibility SLA <= WES NOT_FOUND grace period`。
- 在保留期内，WMS 必须把同一 operation identity、幂等键和 payload 的再次提交处理为原请求的幂等重放；该约束既适用于普通 transport retry，也适用于 WES 在持续 `NOT_FOUND` 超过宽限期后执行的唯一一次受控恢复重提。
- WES 最大确认窗口、`NOT_FOUND` 宽限期、安全余量和 WMS 承诺值均作为部署参数进入联调验收，不在通用合同中写死工厂无关的天数。
- callback 可选；若提供，只发送关联键并作为提示，不要求 WMS 在 callback 中复刻终态 payload。
- 给出认证、超时、重试、错误码、最大响应体、日志脱敏和时钟格式要求。
- WMS 429 必须返回合法 `Retry-After`；WES 将其作为下一次状态查询时间的下限，不在限流窗口内忙重试。
- 给出联调验收矩阵：已完成请求的重复提交、处理中请求的重复提交、同 key 不同 fingerprint 冲突、保留期内幂等键不得重新生效、提交超时、可见性 SLA 边界、处理中、完成、拒绝、暂时未找到、WMS 5xx、状态查询超时。
- 联调必须额外验证“首次提交实际未到达时，同键同 payload 重提可创建请求”和“首次提交已受理但状态暂不可见时，同键重提不产生第二份业务效果”。

**Step 3: 执行 WMS 可行性 GO/NO-GO 门禁**

当前开发阶段先由仓内实际 Docker Mock 冻结交互合同；生产切换前仍须由外部 WMS 团队书面确认至少以下内容：

- 单一 WMS Provider 的 submit/status endpoint、认证方式和 operation 列表。
- 幂等键作用域、同 key 同/不同 payload 行为、错误状态码和稳定错误码。
- 状态枚举、单调 `source_version`、operation-specific typed result 和拒绝原因。
- 幂等/结果保留期、状态可见性 SLA、最大响应体、限流与 `Retry-After`。

开发门禁显式构建 Compose `mock_wms` 后，运行不依赖 WES 生产 adapter 的真实 TCP 黑盒探针，覆盖：

- 首次提交、处理中同键重放、已完成同键重放、同键不同 fingerprint 冲突。
- `ACCEPTED → PROCESSING → COMPLETED` 的版本单调性与 typed result。
- `REJECTED` 的稳定 reason code，`NOT_FOUND` 的空 version。
- 首次请求未到达时同键重提可创建；已受理但暂不可见时同键重提不产生第二份业务效果。
- 配置的可见性和保留期边界，以及 429/5xx/超时的协议形状。

探针不得输出 secret 或任意未脱敏响应体。可行性报告记录 owner、确认时间、image digest、各 case 结果、
承诺参数和 `GO/NO-GO`。当前开发 Mock 的全部强制 case 通过即可关闭 P0 开发门禁；外部 WMS 仍须在生产切换前
由双方确认独立证据，不能沿用 Mock 的 `GO`。

**Step 4: 编写 ADR 并标记旧设计的取代关系**

ADR 记录：

- Context：未上线、一厂一 Provider、可联调清数据整体切换。
- Decision：单部署 Provider；幂等提交 + 状态查询；callback 可选提示。
- Removed：生产 shadow/readiness、动态 profile、签名 staging attestation。
- Retained：typed operation、Outbox、canonical payload、冻结 binding、人工对账。
- Consequences：不支持运行时热切换；新增 Provider 必须先通过相同合同验收并随部署发布。

旧设计文档开头增加“部分取代”提示和 ADR 链接，不重写历史内容。

**Step 5: 验证文档、探针与绿色基线**

```bash
uv run pytest tests/contracts/wms_integration/test_wms_northbound_feasibility_probe.py -q
uv run pytest tests/architecture/test_northbound_wms_typed_operation_boundaries.py -q
uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
```

全部通过；不得通过 `xfail`、跳过或临时放宽现有断言制造绿色结果。

**Step 6: 检查并提交**

- 运行 GitNexus detect changes，确认只涉及文档、非生产黑盒探针及其 contract test。
- 确认 feasibility report 结论为 `GO`；`NO-GO` 不得创建 Task 2 提交。
- Commit：`docs(wms): 明确单 Provider 北向交互合同`

### Task 2：打通 EFFECT 幂等提交元数据并增加状态查询 Port

**Entry gate:** `docs/operations/wms-northbound-feasibility-report.md` 的实际 Compose Mock 结论为 `GO`。
外部 WMS 双方确认仍是生产切换门禁，不阻塞当前开发任务。

**Files:**

- Create: `src/app/wms_integration/ports/effect_status.py`
- Create: `src/app/wms_integration/adapters/effect_status_query_adapter.py`
- Modify: `src/app/sys/models/outbox.py`
- Modify: `src/app/sys/canonical_dispatch.py`
- Modify: `src/app/sys/services/outbox_engine.py`
- Modify: `src/app/wms_integration/ports/__init__.py`
- Modify: `src/app/wms_integration/adapters/__init__.py`
- Modify: `src/app/wms_integration/runtime_factory.py`
- Modify: `src/core/conf.py`
- Modify: `.env.dev`
- Modify: `.env.test`
- Modify: `.env.prod`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.deploy.yml`
- Modify: `docker-compose.test-deploy.yml`
- Modify: 三个 WMS EFFECT operation 的 `gateway.py`、`effect_adapter.py`、preparation service
- Create: Alembic 生成的 SystemOutbox 幂等请求键 migration
- Test: `tests/contracts/system_capabilities/test_canonical_external_http_dispatch.py`
- Test: `tests/sys/test_system_outbox_repository.py`
- Test: `tests/integration/test_system_outbox_canonical_payload_postgresql.py`
- Test: `tests/contracts/wms_integration/test_effect_status_contract.py`
- Test: `tests/contracts/wms_integration/test_wms_transport_runtime_configuration.py`
- Test: `tests/deployment/test_docker_compose_mock_urls.py`
- Test: `tests/contracts/wms_integration/test_provider_conformance_suite.py`
- Test: `tests/architecture/test_northbound_wms_typed_operation_boundaries.py`

**Step 1: 影响分析**

对 `SystemOutbox`、`DispatchEnvelope`、`ExternalHttpDispatchRequest`、canonical signing 函数、`Settings`、`WmsIntegrationRuntimeFactory` 及三个 operation 的 adapter/preparation 符号执行 upstream impact。`SystemOutbox` 已知为 CRITICAL，实施本任务前必须报告直接调用者、execution flows 和迁移影响并再次等待确认。

**Step 2: 写失败的 Port 合同测试**

测试精确覆盖：

- preparation 从已持久化的 RuntimeIntentLog 原样复制幂等键到 SystemOutbox；Outbox retry、Celery 重投和进程重启后仍使用完全相同的键。
- WMS EFFECT 提交只允许受控 header 集，并明确包含 `Idempotency-Key` 与 `X-WES-Operation-Identity`；两者均进入 HMAC canonical signing input，任一值被篡改都会导致签名验证失败。
- generic SystemOutbox 的新字段允许非 WMS 历史/其他 operation 为空，但 WMS EFFECT preparation、dispatch envelope 和 gateway 必须拒绝空值。
- typed business JSON body 与 canonical payload 不因加入请求元数据而改变，且不得混入 operation identity、幂等键或任意基础设施 header。
- 请求只接受规范 operation identity 和非空幂等键。
- 状态查询使用与首次提交完全相同的 `operation_identity + idempotency_key`，不得从 dispatch key 或业务 payload 重算。
- typed status binding builder 能从唯一 active profile 生成 canonical snapshot/hash，并可严格 round-trip 校验；Task 2 只冻结该合同类型，不写 RuntimeIntentLog，持久化接线由 Task 3 与状态字段 migration 同步完成。
- 快照只接受五个约定状态。
- `COMPLETED` 强制携带有大小上限的 `result_payload`，并根据 operation registry 选择 `ConfirmInboundOperationResult`、`FullBoxExchangeOperationResult` 或 `NotifyPackageBindingOperationResult` 严格校验；未知 operation、额外字段、关联字段不一致、`accepted != true` 或内外版本冲突均按合同冲突处理。
- `REJECTED` 强制携带稳定 `reason_code` 且不接受成功 `result_payload`；`ACCEPTED | PROCESSING | NOT_FOUND` 不得携带最终业务结果。
- adapter 不保存任意原始 payload；只有通过 operation-specific result model 校验的 canonical typed result 才能交给 reducer 和 outcome history。
- `ACCEPTED | PROCESSING | COMPLETED | REJECTED` 快照要求非负整数 `source_version`；同一查询键下版本不得回退，相同版本只能返回 canonical 内容相同的快照。`NOT_FOUND` 必须没有 `source_version`，且不得清空或回退 WES 已保存的最后版本。
- `COMPLETED`、`REJECTED` 可以带业务原因；`NOT_FOUND` 不等同失败终态。
- 状态查询 adapter 将 401/403、429、5xx、超时和响应 schema 错误映射到现有命名错误分类；提交接口的 409/422 不在该 adapter 解释。
- 429 解析合法 `Retry-After` 并覆盖更早的本地退避时间；缺失或非法值退回有上限的指数退避加 jitter。
- 相同查询可安全重复，adapter 不产生本地副作用。
- conformance 配置拒绝 `WMS retention < WES max confirmation age + safety margin` 或 `WMS visibility SLA > WES NOT_FOUND grace period` 的组合。
- architecture guardrail 在状态 Port 实现的同一提交中断言每个 EFFECT 都声明状态查询能力。
- Settings 明确提供状态 endpoint、WMS 幂等保留期、WMS 可见性 SLA、WES 最大确认期、`NOT_FOUND` 宽限期、安全余量、扫描批大小、lease、最大查询次数和退避上下限；API 与 Celery 从相同 env/compose 值构建配置。
- 缺少 endpoint/secret、数值非正、退避上下限颠倒、lease 不足以覆盖单次 transport timeout，或跨系统保留期/可见性不变量不成立时，应用启动必须 fail fast。

运行：

```bash
uv run pytest tests/contracts/wms_integration/test_effect_status_contract.py -q
```

预期：导入失败。

**Step 3: 持久化并签名幂等提交元数据**

- SystemOutbox 增加创建后不可变的 `idempotency_key` 请求元数据；通用模型为兼容非 WMS operation 可空，但 WMS EFFECT 路径强制非空。
- preparation 只能从对应 RuntimeIntentLog 复制已持久化的幂等键，不允许 adapter、gateway 或重试路径重新生成。
- DispatchEnvelope 与受控 external HTTP request 暴露该字段；canonical dispatch 将 `Idempotency-Key` 与 `X-WES-Operation-Identity` 纳入闭集 header 和签名输入。
- 三个 WMS EFFECT gateway 发送上述 header，typed body 保持现有业务 schema。不得开放任意 header map。
- 定义 immutable typed status binding snapshot/hash builder，覆盖当前唯一 active Provider 的状态 target、profile/binding revision、auth scheme 和版本化 credential reference；快照不得包含 secret material。Task 2 的 adapter 显式接收已验证 snapshot，不在本任务提前写入尚未迁移的 RuntimeIntentLog 字段。

使用以下命令创建本任务独立 migration，并覆盖 upgrade/downgrade、既有非 WMS Outbox 记录和 WMS EFFECT 非空约束测试：

```bash
uv run alembic revision -m "add system outbox idempotency key"
```

**Step 4: 扩展部署配置并执行启动校验**

在现有 `Settings` 中增加以下职责明确的配置，不在 adapter 内提供隐式工厂默认值：

- WMS 承诺：状态查询 URL、幂等记录保留秒数、提交后状态可见性 SLA 秒数。
- WES 预算：最大确认 age、`NOT_FOUND` 宽限期、安全余量、扫描批大小、claim lease、最大查询次数、退避上下限。

同步 dev/test/prod env profile 和 API/Celery 使用的 Compose 变量；secret 继续只通过现有 secret 配置注入，不写入文档、日志或 fixture。启动校验必须验证 URL/HTTPS、正数/上下限、transport timeout 与 lease 的关系，以及两条 retention/visibility 跨系统不变量。配置测试必须证明 API 与 Celery 的解析结果一致。

**Step 5: 实现最小 Port 和 adapter**

只引入以下接口形状，不加入 WMS 内部阶段：

```python
class WmsEffectStatusQueryPort(Protocol):
    async def query_status(self, request: WmsEffectStatusRequest) -> WmsEffectStatusSnapshot: ...
```

复用现有 HTTP transport、endpoint 配置、认证、预算、错误分类和 redaction；禁止再建一套 HTTP client。
wire snapshot 的 `result_payload` 仅是有大小上限的解析边界；adapter 必须通过冻结的 operation registry 选择对应 `result_model`，产出校验后的 typed result。不得把开放式 JSON 当作领域结果向下游传播。
adapter 的 endpoint、timeout 和 credential reference 只来自 Intent 的 frozen status binding；active Settings 只参与新 Intent preparation。credential resolver 可在最大确认窗口内解析同一 Provider 的旧 credential revision，但不得据此构建多 Provider catalog。

**Step 6: 接入 Provider conformance case**

将状态查询加入未签名的确定性 conformance case。测试只验证交互合同和 replay fixture，不验证 staging 签名。

**Step 7: 验证**

```bash
uv run pytest tests/contracts/wms_integration/test_effect_status_contract.py tests/contracts/wms_integration/test_provider_conformance_suite.py -q
uv run pytest tests/contracts/wms_integration/test_wms_transport_runtime_configuration.py tests/deployment/test_docker_compose_mock_urls.py -q
uv run pytest tests/contracts/system_capabilities/test_canonical_external_http_dispatch.py tests/sys/test_system_outbox_repository.py -q
uv run pytest tests/integration/test_system_outbox_canonical_payload_postgresql.py -q
uv run ruff check src/app/wms_integration tests/contracts/wms_integration
```

预期：全部通过。

**Step 8: 检查并提交**

- 运行 GitNexus detect changes。
- Commit：`feat(wms): 打通幂等提交与状态查询合同`

### Task 3：用状态查询建立 EFFECT 终态闭环

**Files:**

- Create: `src/app/runtime/orchestration/repositories/wms_effect_status_repository.py`
- Create: `src/app/runtime/orchestration/services/wms_effect_status_service.py`
- Modify: `src/app/runtime/orchestration/repositories/__init__.py`
- Modify: `src/app/runtime/orchestration/services/__init__.py`
- Modify: `src/app/runtime/orchestration/effect_state_contract.py`
- Modify: `src/app/runtime/orchestration/effect_bridges.py`
- Modify: `src/app/sys/external_http_transport.py`
- Modify: `src/app/sys/services/outbox_engine.py`
- Modify: `src/app/runtime/orchestration/services/inbox/dispatch_attempt_service.py`
- Modify: `src/app/runtime/orchestration/runtime_intent.py`
- Modify: `src/app/runtime/orchestration/runtime_intent_effects.py`
- Modify: `src/app/runtime/orchestration/runtime_intent_log.py`
- Modify: 三个现有 WMS EFFECT preparation service/effect adapter
- Modify: `src/celery_app/tasks/workline.py`
- Modify: `src/celery_app/config.py`
- Create: Alembic 生成的 WMS EFFECT 状态查询调度 migration
- Test: `tests/workline_runtime/test_effect_state_contract.py`
- Test: `tests/workline_runtime/system_capabilities/test_wms_effect_status_service.py`
- Test: `tests/workline_runtime/test_external_http_transport_attempt.py`
- Test: `tests/workline_runtime/test_external_http_workline_dispatcher.py`
- Test: 三个 operation 的 typed EFFECT contract tests
- Test: `tests/integration/workline_capabilities/test_wms_effect_status_postgresql.py`

**Step 1: 影响分析**

至少分析 `EffectReducer`、`ExternalHttpTransportResult`、`_send_external_http`、RuntimeIntent 的状态应用方法、三个 operation 的 preparation/effect adapter、Celery workline task 注册函数。当前已知 `EffectReducer` 为 MEDIUM；如新分析出现 HIGH/CRITICAL，先汇报并确认。

**Step 2: 写 reducer 和 service 的失败测试**

覆盖：

- `ACCEPTED`、`PROCESSING` 保持非终态且安排下一次查询。
- `COMPLETED`、`REJECTED` 分别产生唯一终态；重复快照不重复写 outcome。
- 创建 WMS EFFECT Intent 时，preparation 从唯一 active profile 生成并持久化 status binding snapshot/hash；事务失败不得只留下 Intent、Outbox 或 snapshot 的部分写入。
- active profile 的 endpoint/credential revision 后续变化时，存量 Intent 仍 round-trip 读取原 snapshot；snapshot/hash 不匹配、字段缺失或 secret material 混入时拒绝写入或进入人工对账。
- `COMPLETED` 只接受已按 operation-specific result model 校验、且 correlation 字段与原 Intent 一致的 typed result；结果缺失、schema 错误、额外字段、operation/result model 不匹配或内外版本冲突均不推进终态，而是保留受控证据并打开人工对账。
- `REJECTED` 只接受稳定 `reason_code`，不得用空结果或伪造的 success payload 代替拒绝原因。
- 低于最后应用 `source_version` 的快照只记录 stale evidence，不推进状态或覆盖当前 outcome。
- 相同 `source_version`、相同 canonical 快照视为幂等重放；相同版本但内容不同立即打开人工对账。
- `COMPLETED` 与 `REJECTED` 的矛盾终态无论版本高低都保留双方证据并进入人工对账，不采用 last-write-wins。
- 无论查询结果为何，SystemOutbox 与 DispatchAttempt 的 `SENT`、`UNKNOWN`、`FAILED` transport 终态均保持不变。
- `NOT_FOUND` 在宽限期内只重查，不重新提交 EFFECT。
- 状态查询从未观察到可见状态、持续 `NOT_FOUND` 超过宽限期时，只允许一次同 operation、同幂等键、同 canonical payload、同冻结 binding 的受控重提；新请求刷新 nonce、时间戳与签名，但不得生成新业务键。
- 受控重提必须持久化计数并通过现有 outbound transport 追加 DispatchAttempt；不得重置 SystemOutbox transport 终态、`attempt_count` 或普通 retry schedule。进程崩溃、重复 Celery delivery 和租约重领后仍不能执行第二次。
- 曾观察到 `ACCEPTED | PROCESSING | COMPLETED | REJECTED` 后再出现 `NOT_FOUND` 属于合同回退，直接人工对账，不允许重提。
- 同键重提再次返回不可判定结果、状态查询仍为 `NOT_FOUND`、返回幂等冲突或恢复预算耗尽时打开人工对账；任何路径都禁止换新幂等键。
- 查询超时/5xx 有界重试，耗尽后打开现有 `ReconciliationCase`。
- EFFECT 提交返回 409 + `IDEMPOTENCY_REQUEST_IN_PROGRESS` 时保持 transport `SENT` 事实并安排状态查询。
- EFFECT 提交返回 422 + `IDEMPOTENCY_CONFLICT` 时保持 transport `SENT` 事实，立即进入人工对账并保留 canonical hash。
- 409/422 缺少稳定错误码、状态码与错误码组合不一致或错误码格式非法时，按合同冲突进入人工对账，不猜测恢复动作。
- 协议错误码提取参数化覆盖：合法顶层字符串、空 body、非 JSON、超出约定大小、非字符串、超长、嵌套对象、未知错误码以及同时携带敏感字段的响应。
- 上述异常响应均不得把原始 body、认证信息或额外字段写入 transport evidence；只保留状态码、受控通用错误和通过校验的顶层 `protocol_error_code`。
- 非 WMS EFFECT 的 EXTERNAL_HTTP 409/422 保持现有通用 transport 行为，不获得 WMS 幂等语义。
- 晚到 callback 与轮询并发时，只有状态查询快照能推进终态。
- 已终态 Intent 不再查询。
- claim 后 Worker 崩溃时，租约到期后可被其他 Worker 重领。
- 旧 Worker 在租约过期并被重领后返回迟到响应时，因 lease token 不匹配而不能写回。
- 大量 Intent 同时到期时，每个扫描任务只领取配置的小批量并批内顺序查询；后续退避时间带 jitter，不产生同步重试尖峰。
- WMS 429 的合法 `Retry-After` 成为 `status_check_after` 下限；非法值使用本地有界退避，circuit breaker 打开时不继续调用 WMS。

运行：

```bash
uv run pytest tests/workline_runtime/test_effect_state_contract.py tests/workline_runtime/system_capabilities/test_wms_effect_status_service.py -q
uv run pytest tests/workline_runtime/test_external_http_transport_attempt.py tests/workline_runtime/test_external_http_workline_dispatcher.py -q
```

预期：新事件和 service 尚不存在而失败。

**Step 3: 实现 Repository**

Repository 仅提供：

- 按 dispatch key 读取对应 RuntimeIntentLog 与 Outbox 关联信息。
- 通过 `(effect_status, status_check_after)` 组合索引，使用有界批次和 `FOR UPDATE SKIP LOCKED` claim 到期或 lease 已过期的非终态 WMS EFFECT。
- claim 事务写入唯一 lease token 与 lease 到期时间后立即提交；不得持有数据库事务或行锁调用 WMS。
- 查询结果在新事务中按当前 lease token fencing：匹配时才应用 RuntimeIntentLog 查询快照、记录查询时间并按需打开或更新对账 case；不匹配的迟到结果直接丢弃。

RuntimeIntentLog 明确增加：

- `status_check_started_at`：第一次进入状态确认的数据库时间，作为 `NOT_FOUND` 宽限期起点。
- `status_check_after`：下一次允许查询的数据库时间；终态或进入人工对账后清空。
- `status_check_count`：已执行的状态查询次数，用于退避和耗尽预算。
- `status_resubmit_count`：状态确认阶段已执行的受控同键重提次数；当前合同最大为一，必须在发起网络调用前与 claim lease 一起持久化。
- `status_source_version`：最后成功应用的 WMS 单调版本；尚无可见快照时为空。
- `status_check_lease_token`：当前查询 Worker 的唯一 fencing token；写回必须匹配。
- `status_check_lease_until`：当前 claim 的数据库时间租约；Worker 崩溃后允许到期重领。
- `status_binding_snapshot_json`：创建 Intent 时冻结的 typed 非秘密状态查询 binding，包含唯一 WMS Provider identity/profile hash、binding revision、target、auth scheme 和版本化 credential reference。
- `status_binding_snapshot_hash`：上述 canonical snapshot 的 SHA-256；读取时必须先重算并验证。

Task 3 在创建 RuntimeIntentLog/Outbox 的既有短事务中调用 Task 2 的 typed builder 并写入 status binding snapshot/hash；不得增加临时 JSON history、第二次补写事务或 fallback 到当前 active profile。

完成、失败或放弃当前查询时释放 lease；只有进程异常退出时依赖租约到期恢复。最后一次查询结果与错误仍写入现有 outcome history，不增加重复的 last-error 字段。状态查询不得改写 `SystemOutbox.next_retry_at` 或 `attempt_count`。
SystemOutbox 与 DispatchAttempt 只作为关联读取和 transport evidence 使用；Repository 不提供用查询结果改写其状态的方法。

**Step 4: 生成并验证调度 migration**

```bash
uv run alembic revision -m "add wms effect status polling state"
uv run pytest tests/migrations -q
```

在生成的 migration 中一次性增加七个轮询/租约字段、两个 status binding snapshot/hash 字段和组合索引。upgrade/downgrade 只处理新字段与索引，不修改既有 Intent/Outbox 业务数据。

**Step 5: 实现 Service 与 Celery 调度**

- 通用 HTTP transport 继续只判断请求是否离开本地边界；仅当错误响应满足响应体大小、JSON object、顶层字符串和错误码长度限制时提取 `protocol_error_code`。不在 transport 层解释 WMS 领域含义，也不保存任意响应体、嵌套内容或其他字段。
- `EffectTransportBridge` 仅对声明为 WMS EFFECT 的提交结果解释 `(http_status_code, protocol_error_code)`：409/in-progress 转状态查询，422/conflict 转人工对账；非法组合 fail closed 到合同冲突对账。
- DispatchAttempt 的既有 `response_json.transport` 保存该低敏协议错误码，无需新增 SystemOutbox 或 DispatchAttempt 字段。
- 即时任务按 dispatch key 查询一次。
- 兜底批任务只扫描到期的非终态 WMS EFFECT，使用部署可配置的小批量并在单个任务内顺序查询。
- 即时任务与兜底任务都必须先提交 claim lease，再在事务外调用 WMS，并以 lease token fencing 写回。
- 状态查询和受控同键重提必须校验 frozen status binding hash，并只使用该 snapshot 的同一 Provider endpoint/auth revision；snapshot 无法解析、旧 credential revision 不可用或被紧急吊销时 fail closed 到人工对账，禁止 fallback 到当前 active profile。
- 非终态重查采用有上限的指数退避加 jitter；429 取 `max(本地退避, Retry-After)`，并复用现有 Provider circuit breaker。不得在本任务中新增 Redis token bucket、分布式 semaphore 或专用限流服务。
- transport 明确未发送仍走现有 Outbox retry。
- 已受理或发送结果不明确后先进入状态查询；只有从未观察到可见状态且持续 `NOT_FOUND` 超过宽限期，才能由状态确认 Service 执行一次受控同键重提。
- 受控重提在独立短事务中先以 lease fencing 将 `status_resubmit_count` 从零推进为一并提交，再于事务外复用原 Outbox envelope 调用现有 transport。它追加 DispatchAttempt transport 事实，但不改写 SystemOutbox 终态或普通 retry 字段。
- 重复 Celery delivery 先由 claim lease 串行化；极端重复响应再由 lease fencing、幂等查询和 reducer 去重。

**Step 6: PostgreSQL 集成测试**

使用显式 integration 目录验证短事务 claim、事务外 HTTP、重复任务、租约到期重领、迟到 Worker fencing、source version 乱序/重放/冲突、终态竞争和对账 case 唯一性。

```bash
uv run pytest tests/integration/workline_capabilities/test_wms_effect_status_postgresql.py -q
```

**Step 7: 回归**

```bash
uv run pytest tests/workline_runtime/test_effect_state_contract.py tests/workline_runtime/system_capabilities/test_wms_effect_status_service.py -q
uv run pytest tests/resilience/test_external_http_effect_crash_matrix_postgresql.py -q
uv run pytest tests/contracts/wms_integration/test_confirm_inbound_typed_effect.py tests/contracts/wms_integration/test_full_box_exchange_typed_effect.py tests/contracts/wms_integration/test_notify_pkg_binding_typed_effect.py -q
```

预期：全部通过。

**Step 8: 检查并提交**

- 运行 GitNexus detect changes，重点确认没有新增 API→Repository 或 Service→Database 调用。
- Commit：`feat(runtime): 以状态查询确认 WMS 效果终态`

### Task 4：将 callback 降级为可选提示

**Files:**

- Modify: `src/app/runtime/orchestration/services/inbox/wms_typed_effect_callback_router.py`
- Modify: `src/app/runtime/orchestration/services/wms_effect_status_service.py`
- Modify: `src/app/runtime/orchestration/repositories/wms_effect_status_repository.py`
- Modify: `src/app/callback/services/callback_orchestration_service.py`
- Modify: `src/app/callback/services/callback_ingress_service.py`
- Modify: `src/app/callback/v1/callback.py`
- Modify: `src/app/wms_integration/services/callback_normalizer.py`
- Modify: `src/app/callback/contracts/registry.py`
- Modify: `src/app/runtime/system_capabilities/wms/contracts.py`
- Delete: `src/app/runtime/system_capabilities/wms/inventory/confirm_inbound/callback_adapter.py`
- Delete: `src/app/runtime/system_capabilities/wms/fulfillment/full_box_exchange/callback_adapter.py`
- Delete: `src/app/runtime/system_capabilities/wms/fulfillment/notify_pkg_binding/callback_adapter.py`
- Modify: 三个 operation 的 `__init__.py`
- Test: `tests/contracts/wms_integration/test_typed_effect_callback_routing.py`
- Test: `tests/workline_runtime/system_capabilities/test_wms_effect_status_service.py`
- Test: `tests/architecture/test_northbound_wms_typed_operation_boundaries.py`

**Step 1: 影响分析与确认**

对 `WmsProviderProfile`、`WmsTypedEffectCallbackRouter` 及 callback route/service 符号运行 upstream impact。`WmsProviderProfile` 已知为 HIGH，必须报告 blast radius 并获得用户确认后再改。

**Step 2: 改写失败测试**

新断言：

- 没有 callback contract 的 EFFECT profile 合法。
- callback 只校验认证、operation identity、idempotency key 和 dispatch correlation。
- 合法 callback 先把对应非终态 Intent 的 `status_check_after` 持久化提前到当前数据库时间并提交，再 best-effort enqueue 即时状态查询；不直接调用 reducer 终结。
- callback 调度事务提交后、enqueue 前进程崩溃，或 Celery broker 拒绝/超时时，callback 仍按成功提示响应；周期扫描必须能领取已持久化为到期的 Intent。
- enqueue 失败只记录命名指标和脱敏日志，不修改 RuntimeIntentLog 语义状态，也不修改 SystemOutbox/DispatchAttempt transport 状态。
- 重复、迟到 callback 可安全忽略或合并。
- callback 不再调用 `finish_sent_external_by_dispatch_key()`；接收前后的 SystemOutbox/DispatchAttempt transport 状态完全一致。
- 非法认证、未知 operation、关联键不匹配仍命名失败并记录安全事件。

**Step 3: 修改组合规则和 router**

删除“每个 EFFECT 恰好一个 callback”的不变量。若 Provider 提供 callback，只注册一个通用 hint contract；不再按 operation 注册三个终态 adapter。

router 通过状态查询 Service 请求“提前到期”，由 Repository 在短事务内锁定并更新非终态 Intent；事务提交后才触发 Celery。已终态 Intent、未知关联键和当前已有更早调度时间分别按合同忽略或命名失败，不创建第二套 callback 队列。

**Step 4: 删除 per-operation callback adapter**

同时清理导出、registry 和只服务旧 callback 权威链路的测试数据。保留 callback 认证、限流、重放防护和审计。

**Step 5: 验证**

```bash
uv run pytest tests/contracts/wms_integration/test_typed_effect_callback_routing.py tests/workline_runtime/system_capabilities/test_wms_effect_status_service.py tests/architecture/test_northbound_wms_typed_operation_boundaries.py -q
uv run ruff check src/app/runtime/orchestration src/app/runtime/system_capabilities/wms
```

预期：全部通过。

**Step 6: 检查并提交**

- 运行 GitNexus detect changes。
- Commit：`refactor(wms): 将效果回调收敛为状态查询提示`

### Task 5：收敛为单部署 Provider 并简化 conformance

**Files:**

- Modify: `src/app/runtime/system_capabilities/wms/provider_catalog.py`
- Modify: `src/app/runtime/system_capabilities/wms/contracts.py`
- Modify: `src/app/runtime/system_capabilities/wms/effect_binding.py`
- Modify: `src/app/wms_integration/runtime_factory.py`
- Delete: `src/app/runtime/system_capabilities/wms/conformance_trust_root.py`
- Modify: `src/app/runtime/system_capabilities/wms/provider_conformance.py`
- Modify: `src/app/runtime/system_capabilities/wms/conformance_manifest.py`
- Modify: `tests/support/wms_provider_conformance.py`
- Delete: `tests/contracts/wms_integration/test_provider_conformance_trust_root.py`
- Modify: `tests/contracts/wms_integration/test_provider_conformance_suite.py`
- Modify: `tests/contracts/wms_integration/test_provider_conformance_report.py`
- Modify: `tests/mock/test_wms_provider_conformance_simulator.py`
- Modify: `tests/architecture/test_northbound_wms_typed_operation_boundaries.py`

**Step 1: 影响分析与确认**

对 `WmsProviderProfile`、`freeze_wms_effect_binding`、runtime factory 构造函数执行 upstream impact。前者已知 HIGH；报告受影响调用链后取得确认。

**Step 2: 写失败测试**

验证：

- 进程内只存在一个由当前部署配置构建的 active profile。
- 系统只允许一个稳定的 WMS Provider identity；不得因 endpoint/credential revision 轮换恢复 Provider map、路由器或多 Provider fallback。
- profile 包含多个 typed operation，不包含运行时 environment selector。
- 冻结 binding 仍记录单一 Provider 的 profile identity/hash、binding revision、submit/status endpoint identity 和版本化 auth reference；新 active revision 不改写存量 Intent snapshot。
- 同一 Provider 的旧 credential revision 至少保留到对应 Intent 最大确认窗口结束；紧急吊销时存量 Intent 转人工对账，不切换到另一个 Provider 或当前 credential。
- 未配置 endpoint/secret 时启动失败。
- active profile、状态 adapter、API 与 Celery 必须使用同一组 Settings；启动时校验 WMS retention/visibility 承诺与 WES confirmation/grace 预算，不允许 adapter fallback。
- 模拟 Provider 只能在测试/开发显式装配，生产配置拒绝。
- conformance 报告可确定性重放，但不依赖签名 trust root。
- architecture guardrail 在单 Provider 装配实现的同一提交中断言无 Provider map、runtime selector 和 conformance trust root。

**Step 3: 简化 Provider 装配**

删除 `WMS_PROVIDER_PROFILES` 一类三环境 map 和运行时 profile selector；保留一个 `build_active_wms_provider_profile(settings)` 装配入口。环境仍可拥有不同配置文件，但不在业务进程中同时建模。该入口只为新 Intent 生成同一 WMS Provider 的当前 revision；存量 Intent 直接读取已冻结的 status binding snapshot，不回查 catalog。

**Step 4: 简化 conformance**

- 删除 Ed25519 trust root、签名和 staging attestation。
- 将通用 case evaluator 缩减为合同测试支撑；生产启动只校验 active profile 的结构与配置。
- 保留 replay asset、结果 schema 和模拟 Provider，用于 WMS 联调前的本地验收。

**Step 5: 验证**

```bash
uv run pytest tests/contracts/wms_integration/test_provider_conformance_suite.py tests/contracts/wms_integration/test_provider_conformance_report.py -q
uv run pytest tests/mock/test_wms_provider_conformance_simulator.py -q
uv run pytest tests/architecture/test_wms_provider_conformance_boundaries.py -q
```

预期：全部通过。

**Step 6: 检查并提交**

- 运行 GitNexus detect changes，确认 frozen binding 和 Outbox 可靠性链未被删除。
- Commit：`refactor(wms): 收敛单部署 Provider 配置`

### Task 6：删除未接入生产路径的 shadow/readiness

**Files:**

- Delete: `src/app/runtime/system_capabilities/shadow_models.py`
- Delete: `src/app/runtime/system_capabilities/shadow_partitioning.py`
- Delete: `src/app/runtime/system_capabilities/shadow_readiness.py`
- Delete: `src/app/runtime/system_capabilities/shadow_repository.py`
- Delete: `src/app/runtime/system_capabilities/shadow_service.py`
- Modify: `src/app/runtime/system_capabilities/__init__.py`
- Modify: `src/app/runtime/orchestration/operational_models.py`
- Modify: `src/app/runtime/orchestration/models/runtime.py`
- Modify: `src/app/runtime/orchestration/repositories/northbound_operations_repository.py`
- Modify: `src/app/runtime/orchestration/services/query/northbound_operations_query_service.py`
- Modify: `src/app/workline/v1/operation.py`
- Modify: `src/celery_app/tasks/workline.py`
- Modify: `src/celery_app/config.py`
- Create: Alembic 生成的 shadow schema 删除 migration
- Delete: `tests/database/test_query_shadow_partition_contract.py`
- Delete: `tests/architecture/test_query_shadow_readiness_boundaries.py`
- Delete: `tests/workline_runtime/system_capabilities/test_query_shadow_recorded_replay.py`
- Delete: `tests/workline_runtime/system_capabilities/test_query_shadow_async_path.py`
- Delete: `tests/workline_runtime/system_capabilities/test_query_shadow_readiness.py`
- Delete: `tests/integration/workline_capabilities/test_query_shadow_readiness_postgresql.py`
- Modify: `tests/runtime/orchestration/test_northbound_operations_repository.py`
- Modify: `tests/runtime/orchestration/test_northbound_operations_query_service.py`
- Modify: `tests/api/test_northbound_operations_route.py`
- Modify: `tests/architecture/test_northbound_wms_typed_operation_boundaries.py`

**Step 1: 影响分析**

分别分析 `QueryShadowReadinessService`、shadow task、northbound readiness projection 和 `QueryEvidence`。当前 readiness service 为 LOW；若删除链条触及 HIGH/CRITICAL，先汇报。

**Step 2: 先删除外部可见的 readiness 合同**

改写 repository/service/API 测试，使北向 operation snapshot 只报告 operation identity、mode、健康度和基础观测，不再包含 shadow readiness、差异率或切换批准。

运行：

```bash
uv run pytest tests/runtime/orchestration/test_northbound_operations_repository.py tests/runtime/orchestration/test_northbound_operations_query_service.py tests/api/test_northbound_operations_route.py -q
```

预期：因生产投影仍包含 readiness 而失败。

**Step 3: 删除 Celery 与 service/repository 路径**

删除 shadow comparison consumer、readiness recompute、partition maintenance task 和 Beat 配置；清理 dependency injection 与模块导出。

**Step 4: 生成并编辑 migration**

```bash
uv run alembic revision -m "remove query shadow readiness"
```

在生成文件中只删除 v0.19 新增的 shadow/readiness 表、索引和分区函数。因为系统未上线，downgrade 可以重建空结构，不迁移或恢复测试数据。

**Step 5: 删除 shadow 专属测试**

删除只验证已移除能力的测试；不要删除 QUERY typed contract、预算、evidence 或纯策略测试。

**Step 6: 验证**

```bash
uv run pytest tests/runtime/orchestration/test_northbound_operations_repository.py tests/runtime/orchestration/test_northbound_operations_query_service.py tests/api/test_northbound_operations_route.py -q
uv run pytest tests/migrations -q
uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
uv run pytest --collect-only -q -o addopts='' | tail -5
```

预期：全部通过，默认收集数按删除测试相应下降。

**Step 7: 静态确认生产路径归零**

```bash
rg -n "shadow_(expected|comparison|readiness)|QueryShadow|maintain_query_shadow" src migrations
```

预期：除历史 migration 和明确的删除 migration 外无匹配。历史 migration 不修改。
同时在本任务提交中加入/收紧 architecture guardrail，锁定生产源码不再出现 shadow readiness；测试必须与删除实现一起变绿。

**Step 8: 检查并提交**

- 运行 GitNexus detect changes。
- Commit：`refactor(runtime): 删除闲置查询影子平台`

### Task 7：从 Attempt 与 evidence 合同移除 shadow 残留

**Files:**

- Modify: `src/app/runtime/workline_plugins/attempt_coordinator.py`
- Modify: `src/app/runtime/system_capabilities/evidence.py`
- Modify: `src/app/runtime/system_capabilities/gateway.py`
- Modify: `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_orchestrator_bridge.py`
- Modify: `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_writeback_service.py`
- Modify: `tests/workline_runtime/extensions/test_plugin_attempt_coordinator.py`
- Modify: `tests/contracts/system_capabilities/test_query_evidence_contract.py`

**Step 1: HIGH 风险影响分析与确认**

对 `AttemptWriteSet`、`_shadow_write_set_matches`、`QueryEvidence` 执行 upstream impact。`AttemptWriteSet` 已知 HIGH，先向用户列出直接调用者和受影响 execution flows，获得确认后再改。

**Step 2: 写失败测试**

- `AttemptWriteSet` 只包含主业务写集，不再接受 shadow comparison。
- replay/lease fencing/idempotency 不因删除 shadow 字段而改变。
- `QueryEvidence` 保留实际结果的 provenance、budget 和 canonical hash，不再暴露始终为 `None` 的 expected side。

**Step 3: 最小删除**

删除字段、匹配 helper、构造参数和空分支；不顺带重构 attempt coordinator 的 lease、fencing 或 commit 协议。

**Step 4: 验证**

```bash
uv run pytest tests/workline_runtime/extensions/test_plugin_attempt_coordinator.py tests/contracts/system_capabilities/test_query_evidence_contract.py -q
uv run pytest tests/integration/test_runtime_plugin_attempt_postgresql.py -q
```

预期：全部通过。

**Step 5: 检查并提交**

- 运行 GitNexus detect changes，确认变更没有越过 shadow 字段删除边界。
- Commit：`refactor(runtime): 清理尝试写集中的影子残留`

### Task 8：合并三个 EFFECT operation 的重复基础设施

**Files:**

- Create: `src/app/runtime/orchestration/services/wms_effect_preparation_service.py`
- Modify: `src/app/runtime/orchestration/services/__init__.py`
- Modify: `src/app/runtime/orchestration/services/confirm_inbound_effect_preparation_service.py`
- Modify: `src/app/runtime/orchestration/services/full_box_exchange_effect_preparation_service.py`
- Modify: `src/app/runtime/orchestration/services/notify_package_binding_effect_preparation_service.py`
- Modify: `src/app/runtime/system_capabilities/wms/inventory/confirm_inbound/effect_adapter.py`
- Modify: `src/app/runtime/system_capabilities/wms/inventory/confirm_inbound/intent_adapter.py`
- Modify: `src/app/runtime/system_capabilities/wms/inventory/confirm_inbound/__init__.py`
- Modify: `src/app/runtime/system_capabilities/wms/fulfillment/full_box_exchange/effect_adapter.py`
- Modify: `src/app/runtime/system_capabilities/wms/fulfillment/full_box_exchange/intent_adapter.py`
- Modify: `src/app/runtime/system_capabilities/wms/fulfillment/full_box_exchange/__init__.py`
- Modify: `src/app/runtime/system_capabilities/wms/fulfillment/notify_pkg_binding/effect_adapter.py`
- Modify: `src/app/runtime/system_capabilities/wms/fulfillment/notify_pkg_binding/intent_adapter.py`
- Modify: `src/app/runtime/system_capabilities/wms/fulfillment/notify_pkg_binding/__init__.py`
- Test: `tests/contracts/wms_integration/test_confirm_inbound_typed_effect.py`
- Test: `tests/contracts/wms_integration/test_full_box_exchange_typed_effect.py`
- Test: `tests/contracts/wms_integration/test_notify_pkg_binding_typed_effect.py`

**Step 1: 逐符号影响分析**

对三个 preparation service、effect adapter 和 intent adapter 的公开符号分别运行 upstream impact。此任务只合并确认相同的基础设施流程，不抽象 operation 特有 payload 或业务字段。

**Step 2: 写参数化特征测试**

以三个 operation 为参数，锁定共同不变量：

- canonical payload 与 payload hash 生成一致。
- dispatch key、idempotency key、frozen binding 和 Outbox metadata 完整。
- 业务 request/result model 仍由各 operation 自己拥有。
- 错误码和 retry classification 不变化。

**Step 3: 提取共享 preparation service**

共享 service 接受 operation definition、typed request 和现有 binding，返回现有 Intent/Outbox 产物。各 operation 文件只负责类型化入口与领域 payload 映射。

若某段代码涉及 operation 特有字段、领域校验或结果解释，留在原文件，不为追求文件数强行合并。

**Step 4: 删除无意义薄包装**

当三个旧 preparation service 已无独立行为时删除它们；若为依赖注入提供清晰命名，可保留不含逻辑的类型别名，但最终不得形成兼容双轨。

**Step 5: 验证**

```bash
uv run pytest tests/workline_runtime/system_capabilities -q
uv run pytest tests/contracts/wms_integration -q
uv run ruff check src/app/runtime/orchestration/services src/app/runtime/system_capabilities/wms
```

预期：全部通过。

**Step 6: 检查并提交**

- 运行 GitNexus detect changes。
- Commit：`refactor(wms): 合并效果操作准备流程`

### Task 9：联调验收、数据清理与整体切换

**Files:**

- Modify: `docs/contracts/observability-contract.md`
- Modify: `docs/operations/northbound-operation-slo-catalog.md`
- Modify: `docs/runbooks/northbound-operation-observability.md`
- Modify: `docs/architecture/target-state-contract.md`
- Modify: `docs/contracts/external-contract-profile.md`
- Modify: `docs/architecture/northbound-legacy-removal-report.json`
- Modify: `tests/fixtures/wms_provider_conformance/query_inventory_replay.v1.json`
- Add/Modify: WMS EFFECT 状态查询 replay fixture

**Step 1: 更新观测与 runbook**

用以下指标替代 shadow/readiness：

- submit accepted/ambiguous/not-sent 数量与延迟。
- status query 各状态数量、延迟、重试次数和 age。
- status query backlog 数量/最大 age、单批领取量/耗时、429、`Retry-After`、circuit-open 和实际退避时长。
- `NOT_FOUND` 超过宽限期、查询耗尽、幂等冲突和 open reconciliation 数量。
- callback hint 接收、拒绝、重复、触发查询和 enqueue 降级数量。

runbook 只描述 WES 可观察的交互事实，不推断 WMS 内部处理步骤。

**Step 2: 在联调环境执行 WMS 合同验收**

按 `docs/contracts/wms-northbound-interaction-contract.md` 的矩阵验证全部 QUERY 和 EFFECT operation。验收输出包含：

- Provider identity、contract version、构建版本。
- WES 最大确认窗口、`NOT_FOUND` 宽限期、安全余量，以及 WMS 承诺的幂等/状态保留期和可见性 SLA。
- 每个 case 的 request canonical hash、状态码、规范化结果和耗时。
- 敏感字段脱敏后的失败证据。
- WES/WMS 双方确认人和验收时间。

验收报告是发布证据，不进入运行时签名或 readiness 状态机。
该步骤是对真实联调环境、全部 operation 和发布构建的最终验收，不替代 Task 1 基于 WMS stub 的早期可行性门禁。

**Step 3: 清理测试数据并整体切换**

- 确认 Step 2 的全部 EFFECT 已终结或对账关闭，停止联调任务和 Celery worker。
- 备份必要的诊断日志；清空双方约定的联调业务数据、Intent、Outbox、inbox、reconciliation 和 shadow 遗留表。
- 应用 migration，启动单 active Provider 配置；保持真实 EFFECT admission 关闭。
- 执行健康检查、配置校验、状态 backlog/worker 检查和一个 QUERY smoke case。此阶段若失败，先证明 WES/WMS 均无新 EFFECT receipt，再允许回退部署和 migration；不启用运行时双 Provider。
- preflight 全部通过后记录切换 GO 并开放真实 EFFECT admission。第一笔真实 EFFECT 一旦离开本地边界或结果不明确，即越过不可逆点。
- 不可逆点后若发生故障，立即关闭新的 EFFECT admission，但保持状态查询、callback hint、租约恢复和 reconciliation Worker 运行；保留当前 schema/账本，通过同键查询、人工对账和 forward-fix 恢复。禁止 downgrade migration、清空在途数据或回退到 callback 权威/无状态查询的旧版本。

具体清理 SQL 必须在执行阶段根据环境和表依赖单独审查，本计划不内嵌破坏性脚本。

**Step 4: 全量验证**

```bash
uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
uv run pytest tests/contracts/wms_integration tests/workline_runtime/system_capabilities tests/runtime/orchestration tests/api/test_northbound_operations_route.py -q
uv run pytest tests/integration/workline_capabilities/test_wms_effect_status_postgresql.py tests/integration/test_northbound_operations_postgresql.py -q
uv run pytest tests/resilience/test_external_http_effect_crash_matrix_postgresql.py -q
uv run pytest --collect-only -q -o addopts='' | tail -5
./scripts/git-quality-gate.sh --profile quality
```

预期：全部通过；quality gate 无新增架构、格式、类型或安全错误。

**Step 5: 最终静态验收**

```bash
rg -n "QueryShadow|shadow_readiness|conformance_trust_root|WMS_PROVIDER_PROFILES" src tests docs
rg -n "callback.*(COMPLETED|REJECTED)|callback_adapter" src/app/runtime
```

预期：

- 第一条只允许旧设计文档中的历史说明和 migration 历史。
- 第二条无生产代码匹配。

**Step 6: GitNexus 与文档一致性检查**

- 运行 GitNexus detect changes，确认受影响流程仅为 WMS profile 装配、EFFECT 终态确认、callback hint、shadow 删除和重复基础设施合并。
- 更新 legacy removal report，列出保留与删除符号的可验证证据。
- Commit：`docs(wms): 完成北向能力简化与联调切换说明`

## 完成定义

- 每个部署只实例化一个 WMS Provider profile，并能承载多个 typed operation。
- 新 Intent 使用唯一 active profile；存量 Intent 使用同一 WMS Provider 的 frozen submit/status binding revision。系统不存在多 Provider catalog、路由或 fallback。
- 所有 EFFECT 都通过幂等提交和状态查询闭环；callback 缺失不影响正确性。
- 持续 `NOT_FOUND` 只在从未观察到可见状态、超过已验收宽限期时受控同键重提一次；该动作跨重启仍有严格一次预算，且不会重置 Outbox transport 状态。
- WMS EFFECT 首次提交、Outbox 重试、进程重启和状态查询始终使用同一对已持久化的 `operation_identity + idempotency_key`；提交 header 纳入 canonical 签名，typed business body 不混入基础设施字段。
- WMS EFFECT 提交的 409/in-progress 与 422/conflict 在提交结果 bridge 解释；状态查询 adapter 和非 WMS 通用 HTTP transport 不承担该领域语义。
- 协议错误码提取对空、非 JSON、超限、类型错误、嵌套、未知码和敏感字段响应均 fail closed，且 DispatchAttempt evidence 不含任意远端 body。
- callback 不能直接推进 RuntimeIntentLog 语义状态；只有状态查询快照可以产生 `COMPLETED`、`REJECTED`。
- `COMPLETED` 的业务结果已通过对应 operation 的既有 typed result model 严格校验；`REJECTED` 具有稳定 reason code，未校验的开放式 JSON 不进入 RuntimeIntentLog outcome。
- callback 即时任务 enqueue 失败不影响合同成功响应或双账本状态；持久化的到期调度可被周期扫描接管，并产生可观测的降级指标。
- SystemOutbox 与 DispatchAttempt 在状态查询和 callback 前后保持原 transport 终态，不承载 WMS 业务完成状态。
- 状态查询不持有数据库事务调用 WMS；Worker 崩溃后可以租约重领，迟到响应不能越过 lease token fencing 写回。
- 状态查询以小批量、批内顺序、jittered backoff、`Retry-After` 和现有 circuit breaker 形成背压；没有新增分布式限流组件。
- 状态快照按持久化的 WMS `source_version` 单调应用；旧版本不回退状态，同版本异内容和矛盾终态进入人工对账。
- 生产源码不存在 shadow comparison/readiness、签名 conformance trust root 或运行时多 profile 选择器。
- QUERY 的预算、分页、evidence 和纯策略行为没有回归。
- Outbox、dispatch key、canonical payload、frozen binding、错误分类和人工对账保持有效。
- WMS 团队可以仅凭合同文档实现接口，并通过联调验收矩阵。
- Task 1 在任何 WES 生产代码改造前取得 WMS 团队书面确认和 stub 黑盒探针 `GO`；Task 9 另行完成真实联调环境的全量发布验收。
- 部署配置满足 WMS 保留期与状态可见性不变量，联调报告包含具体参数及边界用例证据。
- API 与 Celery 从同一 Settings/env/Compose 配置链读取状态 endpoint、SLA 和轮询预算；缺失、非法或互相矛盾的配置在启动阶段失败。
- migration 可在空的开发/联调环境完成 upgrade/downgrade；清理测试数据后可整体切换。
- 正式切换的 rollback boundary 有证据：首个真实 EFFECT 前可在确认双方零 receipt 后回退；首个真实 EFFECT 后只允许停止 admission、保留状态确认账本并向前修复。
- 默认快速回归、受影响 integration/resilience 测试和质量门禁全部通过。
- 每个任务检查点均为绿色提交；不存在为了跨任务保留红测而加入的 intentional failure 或长期 `xfail`。

## 风险控制

- **HIGH/CRITICAL blast radius：** `WmsProviderProfile`、`AttemptWriteSet`、`SystemOutbox` 的变更必须拆开处理；SystemOutbox 只新增不可变的幂等请求元数据，实施前仍须重新执行影响分析并获得确认。
- **幂等键漂移风险：** 幂等键只从 RuntimeIntentLog 复制并持久化到 SystemOutbox，重试、重启和状态查询均复用原值；禁止在 gateway 或 dispatch retry 中重算。
- **签名覆盖缺口风险：** operation identity 和幂等键使用闭集 header，并同时进入 canonical signing input；业务 payload schema 不承担传输元数据。
- **重复提交风险：** 明确“未发送”仍走普通 transport retry；受理或结果不明确后先查状态。只有从未观察到可见状态且持续 `NOT_FOUND` 超过宽限期，才允许同键、同 payload、同冻结 binding 受控重提一次；禁止新键重提。
- **幂等冲突误分类风险：** 同 key/同 fingerprint 处理中使用 409 并转状态查询；同 key/不同 fingerprint 使用 422 并立即对账；禁止依赖错误文本决定恢复动作。
- **端点错误语义污染风险：** 409/422 只由 WMS EFFECT 提交结果 bridge 按稳定协议错误码解释；通用 transport 只保留有界低敏证据，状态查询 adapter 与其他 EXTERNAL_HTTP operation 不继承该领域规则。
- **错误响应输入风险：** `protocol_error_code` 只从满足大小、类型和长度上限的顶层字段提取；异常或未知内容不落原始 body、不猜测语义，WMS 409/422 无法验证时进入人工对账。
- **迟到可见与记录过期风险：** `NOT_FOUND` 有宽限期，不能立即判拒绝或重新提交；WMS 保留期必须覆盖 WES 最大确认窗口和安全余量，状态可见性 SLA 必须落在宽限期内，避免把已受理请求误判为不存在或让幂等键提前重新生效。
- **恢复重提竞态风险：** `status_resubmit_count` 在网络调用前持久化并受 lease token fencing；受控重提只追加 DispatchAttempt，不重置 Outbox 终态或 retry 计数。Worker 崩溃最多导致偏保守地转人工，不允许第二次自动重提。
- **轮询恢复风险：** 退避、宽限期、耗尽预算和 claim lease 以 RuntimeIntentLog 的独立字段为准；claim 独立提交、HTTP 事务外执行、写回按 token fencing，禁止从 JSON history 推导或复用 Outbox transport retry 字段。
- **重启惊群风险：** 小批量、批内顺序、指数退避 jitter、`Retry-After` 和 circuit breaker 共同限速；观测 backlog age/429 后再决定是否需要 Provider 级全局限流，禁止无数据预建分布式限流平台。
- **快照乱序风险：** 只使用 WMS 单调 `source_version` 排序，`updated_at` 不参与跨系统比较；旧版本仅留证，同版本异内容和矛盾终态必须对账。
- **终态缺少业务结果风险：** `COMPLETED` 必须携带并通过 operation-specific result model 校验的结果；correlation、accepted 或版本不一致均 fail closed 到人工对账，禁止仅凭状态枚举丢失 typed result。
- **callback 竞态与 broker 故障：** callback 先提交 RuntimeIntentLog 的到期调度，再 best-effort enqueue，且不参与 reducer 终态决策；进程或 broker 故障由周期扫描接管并记录降级指标。
- **双账本污染风险：** 状态查询只写 RuntimeIntentLog/ReconciliationCase；禁止用业务查询结果改写 SystemOutbox 或 DispatchAttempt 的 transport 终态。
- **误删有价值能力：** 以“是否服务单 Provider 的 typed QUERY/EFFECT 可靠性”为判断标准，保留预算、证据、Outbox、冻结 binding 和人工对账。
- **迁移风险：** 不改历史 migration；生成新的 destructive cutover migration，并只在已确认可清数据的开发/联调环境执行。
- **migration 归属风险：** Task 2、3、6 分别生成 Outbox 幂等键、Intent 状态确认字段、shadow/readiness 删除 revision；禁止复用错误 message 或合并跨任务 schema 变化。
- **跨系统回滚风险：** EFFECT 无法与数据库原子撤销。完整 EFFECT smoke 只在联调环境执行；正式切换首个真实 EFFECT 是不可逆点，之后禁止 schema downgrade/不兼容旧版本回滚，故障时关闭新 admission 但保持查询与对账 Worker。
- **配置漂移风险：** 状态 endpoint、SLA 和轮询预算只由现有 Settings 加载，env profile 与 API/Celery Compose 变量必须成对测试；禁止 adapter 局部默认值掩盖缺失配置。
- **外部可行性后置风险：** Task 1 必须以 WMS 书面确认和最小 stub 黑盒探针形成 `GO/NO-GO`；未通过时暂停 Tasks 2–9，禁止用 WES mock 自证 WMS 能力。
- **冻结 binding 漂移风险：** active profile 只服务新 Intent；存量 Intent 的状态查询和受控重提必须验证并使用 frozen status binding snapshot。旧 credential revision 在确认窗口内保留，吊销或缺失时转人工对账，禁止 fallback 到当前 revision 或其他 Provider。
- **范围漂移：** 每个任务独立提交，提交前用 GitNexus detect changes 和 `git diff --cached --name-only` 核对范围。

## What already exists

| 已有能力 | 现状 | 本计划处理 |
| --- | --- | --- |
| 三个 EFFECT typed request/result Port | `confirm_inbound`、`full_box_exchange`、`notify_pkg_binding` 已有冻结 Pydantic contract | 复用并将状态查询 `COMPLETED` 结果严格映射回既有 result model |
| `SystemOutbox`、`DispatchAttempt` 与 canonical payload | 已记录可靠 transport 事实、payload hash 和尝试证据 | 保留双账本，只补不可变幂等请求元数据和受控签名 header |
| `FrozenExternalHttpBinding` | 已冻结 submit target、profile/binding hash 和版本化 credential reference | 沿用同一模式定义 status binding snapshot；不创建 Provider router |
| HMAC canonical dispatch 与 credential resolver | 已支持闭集 header、nonce、时间戳和版本化 secret reference | 将 operation identity/idempotency key 纳入签名，并保留同一 Provider 的历史 credential revision |
| Runtime Intent/EFFECT reducer | 已有语义状态所有权和 outcome history | 扩展状态查询事件、source version 单调规则、typed result 和对账分支 |
| `ReconciliationCase` | 已承接 UNKNOWN、合同冲突和人工恢复 | 复用，不新增第二套异常工单 |
| callback ingress/auth/replay 防护 | 已有认证、关联和审计基础设施 | 保留 ingress，只把 callback 从终态权威降级为查询提示 |
| Celery workline task/Beat | 已有即时任务与周期任务装配点 | 增加状态查询即时/扫描任务，删除未使用 shadow task |
| QUERY budget/evidence/policy | 已覆盖分页、预算、provenance 和纯策略 | 原样保留，不把 EFFECT 简化扩散到 QUERY |
| Provider conformance fixture/replay | 已有确定性回放与 mock Provider | 去掉生产签名 attestation，复用为 Task 1/9 合同验收证据 |

## NOT in scope（明确不在范围内）

- **多个不同 WMS Provider：** 每个工厂部署只对接一个 WMS；不建设 catalog、路由、热切换或 fallback。
- **WMS 内部工作流建模：** WES 只定义 submit/status/callback 交互合同，不复制 WMS 阶段、库存状态机或内部补偿。
- **运行时 shadow/readiness 平台：** 系统未上线且可联调验收，删除无生产调用的 shadow、readiness 和分区维护。
- **生产 conformance 签名与 trust root：** 保留可重复合同测试和验收报告，不把发布证明变成运行时信任系统。
- **Provider 级分布式限流器：** 先使用小批量、顺序查询、jitter、`Retry-After` 和现有 breaker；只有真实指标证明不足时重新立项。
- **跨系统 EFFECT 自动撤销：** WMS 效果无法与 WES 数据库原子回滚；不可逆点后只做状态确认、对账和 forward-fix。
- **监管归档与长期合规留存：** 当前没有监管要求；只保留可靠性窗口所需的幂等、结果和诊断证据。
- **统一运营看板：** `TODOS.md` 已有“统一运营看板、告警与 Runbook”，本计划只交付本领域指标与 runbook 输入，不重复建跨域运营面。

## 测试覆盖图

```text
CODE PATHS                                                OPERATOR / INTEGRATION FLOWS
[Task 1] WMS feasibility gate                             [+] WMS 团队交付
  ├─ [★★★ PLAN] written contract/SLA complete               ├─ [★★★ PLAN] stub GO → Tasks 2-9
  ├─ [★★★ PLAN] duplicate/conflict/version/result cases      └─ [★★★ PLAN] stub NO-GO → revise contract, stop
  └─ [★★★ PLAN] secret/body redaction

[Task 2] prepare → Outbox → signed submit                 [+] 首次提交与重放
  ├─ [★★★ PLAN] persisted idempotency key/header/signature  ├─ [★★★ PLAN] same key/same payload
  ├─ [★★★ PLAN] missing key/binding/config fail closed       ├─ [★★★ PLAN] same key/different payload
  ├─ [★★★ PLAN] not-sent bounded transport retry             └─ [★★★ PLAN] restart preserves key/binding
  └─ [★★★ PLAN] 409 in-progress / 422 conflict classification

[Task 3] claim → commit → HTTP → fenced writeback         [+] 状态确认
  ├─ [★★★ PLAN] ACCEPTED/PROCESSING → reschedule             ├─ [★★★ PLAN] callback absent
  ├─ [★★★ PLAN] COMPLETED → typed result                     ├─ [★★★ PLAN] callback late/duplicate
  ├─ [★★★ PLAN] REJECTED → stable reason                     └─ [★★★ PLAN] worker crash/restart
  ├─ [★★★ PLAN] stale/equal/conflicting source_version
  ├─ [★★★ PLAN] NOT_FOUND grace → one same-key resubmit
  ├─ [★★★ PLAN] timeout/5xx/429/circuit-open → backoff
  └─ [★★★ PLAN] budget/conflict → reconciliation

[Task 4-8] simplify without semantic drift                [+] 运维与切换
  ├─ [★★★ PLAN] callback durable schedule + enqueue fallback ├─ [★★★ PLAN] backlog/429 observable
  ├─ [★★★ PLAN] one active Provider, frozen old revision      ├─ [★★★ PLAN] pre-EFFECT rollback allowed
  ├─ [★★★ PLAN] shadow/readiness absence guardrails           └─ [★★★ PLAN] post-EFFECT forward-only recovery
  └─ [★★★ PLAN] shared preparation characterization

Legend: ★★★ = behavior + edge cases + named error paths
Coverage target: every enumerated branch has a planned unit, contract, integration,
resilience, architecture, or black-box acceptance test; implementation completion
must replace PLAN with passing evidence. No LLM eval or browser E2E is applicable.
```

需要在实现代码中维护的 ASCII 注释：

- `runtime_intent_log.py`：状态确认字段、lease fencing、单次受控重提和终态关系图。
- `wms_effect_status_service.py`：短事务 claim → 事务外 HTTP → token fenced writeback 管线。
- `effect_bridges.py`：submit transport 结果到“查询/对账/普通 retry”的分类树。
- `wms_typed_effect_callback_router.py`：持久化提前到期 → commit → best-effort enqueue → scanner fallback。
- `outbox.py`：transport ledger 与 RuntimeIntent semantic ledger 的所有权边界；修改附近现有注释时同步更新。

## 生产失败模式审计

| 新路径 | 真实失败方式 | 测试 | 处理 | 运维可见性 |
| --- | --- | --- | --- | --- |
| WMS 可行性门禁 | WMS stub 不支持幂等冲突或单调版本 | Task 1 black-box contract test | `NO-GO`，暂停后续任务 | feasibility report 明确失败 case |
| 配置启动 | API/Celery endpoint、SLA 或预算不一致 | Settings/Compose contract test | 启动 fail fast | 命名配置错误，不输出 secret |
| EFFECT preparation | Intent 已建但 Outbox/key/binding 部分写入 | typed EFFECT + PostgreSQL transaction test | 同一短事务回滚 | 创建失败指标与脱敏日志 |
| signed submit | header 被篡改或 idempotency key 漂移 | canonical dispatch contract test | 签名失败/本地拒绝 | DispatchAttempt 受控证据 |
| submit response | 非法 409/422 body 诱导错误恢复 | 参数化 transport/bridge tests | fail closed 到对账 | protocol error metric，无原 body |
| status claim | Worker claim 后崩溃 | PostgreSQL lease recovery test | lease 到期重领 | lease age/backlog 指标 |
| fenced writeback | 旧 Worker 迟到覆盖新结果 | token mismatch race test | 丢弃迟到写回 | stale worker 计数 |
| status snapshot | source version 回退或同版本异内容 | reducer unit/integration tests | 留证并对账，不覆盖终态 | contract conflict/reconciliation |
| typed result | `COMPLETED` payload 缺字段或 correlation 不一致 | 三个 operation contract tests | 不终结，进入对账 | 命名 schema/correlation failure |
| `NOT_FOUND` | 首次提交确实未到达 | grace/resubmit crash matrix | 同键同 payload 最多重提一次 | resubmit count/age 指标 |
| resubmit fencing | 计数提交后进程在 HTTP 前崩溃 | crash-window integration test | 偏保守转人工，不二次自动重提 | open reconciliation |
| 429/积压 | WMS 长时间限流导致任务惊群 | backoff/jitter/Retry-After tests | 小批量、顺序、breaker | backlog max age、429、实际退避 |
| frozen credential | 存量 Intent 的旧 credential 被吊销 | binding/credential failure test | 禁止 active fallback，转对账 | credential resolution reason code |
| callback hint | DB commit 后 broker enqueue 失败 | callback degradation test | 周期 scanner 接管 | enqueue degraded metric |
| 正式切换 | 首个真实 EFFECT 后应用故障 | cutover runbook rehearsal | 关 admission，保留 Worker，forward-fix | GO/不可逆点/恢复证据 |

审计结论：所有列出的失败模式均有计划测试、命名处理和可观察信号；无“无测试 + 无处理 + 静默失败”的 critical gap。

## 实施顺序与 Worktree 策略

本计划以顺序实施为主，不建议为 Tasks 2–8 默认创建并行 worktree：这些任务反复修改
`runtime/orchestration`、`runtime/system_capabilities/wms`、`wms_integration`、`sys`
和 Celery 装配，平行开发会产生高冲突并使 migration/contract 基线漂移。

```text
Task 1 GO/NO-GO
  → Task 2 Port/config/submit foundation
    → Task 3 status persistence + reducer + workers
      → Task 4 callback hint
        → Task 5 single Provider assembly
          → Task 6 shadow/readiness removal
            → Task 7 evidence residue cleanup
              → Task 8 preparation DRY consolidation
                → Task 9 acceptance + cutover
```

- 默认直接在从 `develop` 创建的单一 feature branch 顺序执行，每个任务保持绿色并独立提交。
- WMS 团队实现 stub/真实接口可与 WES 仓库工作并行，但它是外部协作，不应在本仓库创建第二条实现分支。
- 若必须并行，仅允许把 Task 1 文档/外部探针或 Task 9 非代码验收材料放入独立 worktree；合并前仍以主实现分支的合同和 migration head 为准。
- 任何 worktree 都按仓库规则独立运行 `./scripts/init-env.sh dev`、`uv sync --dev` 和质量门禁，不共享 `.env`、`.venv` 或 pytest cache。

## TODOS.md 评审结论

没有新增 TODO 候选：

- 统一运营看板已由现有 TODO 覆盖，本计划只更新其 WMS 指标输入。
- 多 Provider、WMS 内部逻辑和生产 shadow/readiness 是明确拒绝的范围，不应写成延期事项。
- Provider 级分布式限流只有真实 backlog/QPS/429 数据证明简单背压不足时才形成新问题；当前没有可执行触发证据，不创建占位 TODO。

## Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above.
Run with Claude Code or Codex; checkbox as you ship.

- [x] **T1 (P1, human: ~1–2d + WMS coordination / CC: ~30min)** — WMS contract — 实际 Mock 黑盒探针已取得 feasibility `GO`
  - Surfaced by: Architecture review — WMS 外部可行性原被后置到 Task 9。
  - Files: `docs/contracts/wms-northbound-interaction-contract.md`, `docs/operations/wms-northbound-feasibility-report.md`, `scripts/verify_wms_northbound_feasibility.py`
  - Verify: `uv run pytest tests/contracts/wms_integration/test_wms_northbound_feasibility_probe.py -q`
- [ ] **T2 (P1, human: ~2–3d / CC: ~3–5h)** — WMS transport contract — 打通持久化幂等键、受控签名 header、Settings 和 status Port
  - Surfaced by: Outside voice + architecture review — submit 未携带关联键，配置文件和状态合同缺失。
  - Files: `src/app/sys/`, `src/app/wms_integration/`, `src/core/conf.py`, env/Compose profiles, generated Outbox migration
  - Verify: Task 2 contract、deployment、repository、integration commands 全部通过。
- [ ] **T3 (P1, human: ~4–6d / CC: ~6–10h)** — Runtime reliability — 建立持久化状态确认、typed result、租约围栏和同键单次重提
  - Surfaced by: Architecture/test/performance review — 轮询状态、业务结果、崩溃恢复、乱序和 `NOT_FOUND` 恢复必须形成闭环。
  - Files: `src/app/runtime/orchestration/`, `src/celery_app/`, generated RuntimeIntent migration
  - Verify: reducer/service unit tests、PostgreSQL integration、resilience crash matrix 和三类 typed EFFECT tests 通过。
- [ ] **T4 (P1, human: ~1–2d / CC: ~2–3h)** — Callback boundary — 将 callback 收敛为可恢复的查询提示
  - Surfaced by: Architecture review — callback 不能作为终态权威，broker 失败不能丢失确认。
  - Files: `src/app/callback/`, callback router/normalizer/registry, WMS contracts
  - Verify: callback routing、scanner fallback、architecture guardrail tests 通过。
- [ ] **T5 (P1, human: ~1–2d / CC: ~2–3h)** — Provider assembly — 只装配一个 WMS Provider 并保留同 Provider frozen revision
  - Surfaced by: Scope/code-quality review — 三环境 profile map 与一厂一 Provider 不匹配，active revision 不能覆盖存量 Intent。
  - Files: WMS provider catalog/contracts/effect binding/runtime factory/conformance modules
  - Verify: provider conformance、mock、configuration 和 architecture tests 通过。
- [ ] **T6 (P2, human: ~2–3d / CC: ~4–6h)** — Runtime cleanup — 删除无生产调用的 shadow/readiness 平台
  - Surfaced by: Scope review — 完整 shadow 平台没有生产构造/enqueue 路径。
  - Files: runtime system capabilities、northbound projection/API、Celery、generated destructive migration
  - Verify: projection/API/migration/topology tests 与生产引用归零检查通过。
- [ ] **T7 (P2, human: ~1d / CC: ~1–2h)** — Evidence cleanup — 删除 Attempt/QueryEvidence 的 shadow 残留
  - Surfaced by: Code-quality review — 空 shadow write set 和 `shadow_expected=None` 是无收益合同噪声。
  - Files: attempt coordinator、query evidence/gateway、runtime inbox writeback
  - Verify: attempt/evidence contract tests 与 PostgreSQL integration test 通过。
- [ ] **T8 (P2, human: ~1–2d / CC: ~2–3h)** — WMS DRY — 在特征测试保护下合并三个 preparation 流程
  - Surfaced by: Code-quality review — 三个 EFFECT preparation 大量重复但业务 payload 必须继续分离。
  - Files: runtime orchestration preparation services、三个 WMS operation adapter package
  - Verify: system capability、三个 typed EFFECT contract 和 Ruff 检查通过。
- [ ] **T9 (P1, human: ~2–3d + WMS coordination / CC: ~1–2h)** — Release — 完成真实联调、清数据和 forward-only 整体切换
  - Surfaced by: Architecture review — EFFECT 后回滚不安全，发布必须有明确不可逆点。
  - Files: observability/SLO/runbook/target-state docs、conformance fixtures、legacy removal report
  - Verify: Task 9 全量测试、quality gate、静态归零和双方验收报告通过。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR | 6 proposals, 6 accepted, 0 deferred |
| Codex Review | `/codex review` | Independent 2nd opinion | 3 | CLEAR | 9 findings；8 个可执行缺口已折叠，1 个缩减范围建议因既定 9-task 选择不再采纳 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 2 | CLEAR | 本轮 20 issues, 0 critical gaps, 0 unresolved |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 后端集成与清理计划，不涉及 UI |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 未单独运行；实施命令、绿色提交和 worktree 边界已在本计划明确 |

**CODEX:** 补出了 submit 关联键、typed terminal result、受控同键重提、配置落点、冻结 revision、早期 WMS 门禁和 forward-only cutover 等缺口。

**CROSS-MODEL:** 两侧一致认为应保留 typed operation、Outbox、冻结 binding 和人工对账，同时删除当前阶段无收益的多 profile、shadow/readiness 与生产 attestation；分歧仅在是否进一步缩小已确认的 9-task scope。

**VERDICT:** CEO + CODEX + ENG CLEARED — ready to implement；Task 1 的 WMS feasibility `GO` 仍是进入生产代码改造的外部执行门禁。

NO UNRESOLVED DECISIONS
