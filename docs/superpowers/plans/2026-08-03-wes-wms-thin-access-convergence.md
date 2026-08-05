# WES Phase 3 WMS 薄接入边界收敛实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前 54 个文件、约 8,671 行并混合 Provider、Catalog、Runtime、Effect、status lifecycle
与部署鉴证的 `wms_integration`，收敛成类型化 WMS 查询、业务确认发送和无状态 WMS 转发搬运 Client；
同时保住 Phase 4 接管前唯一活动可靠性链，不产生兼容层、双活或事实丢失窗口。

**Architecture:** 35 项已冻结 wire contract 继续存在，每项以一个垂直 capability 模块内聚 DTO、固定
method/path、拒绝码和 `WmsCallSpec`，并按目标所有权暴露三条显式窄边界：19 项只读查询由
`WmsCapabilities` 暴露，E01–E07/E15 由 `WmsConfirmationSender` 暴露，E08–E14/E16 由
`WmsForwardedTransportClient` 暴露。生产运行时不提供 capability registry、generic `call`、动态发现或
codegen。Composition Root 先把 WMS Adapter 包提供的无 Secret 认证合同交给 Phase 2
`OutboundHttpTransportFactory`，为 WMS 装配一个进程内明确生命周期、已完成认证装配的
`OutboundHttpTransport`，再注入 WMS 业务 Gateway；WMS 业务层不接收裸 `httpx.AsyncClient`，不管理连接池、
凭据解析、Secret、签名过程或通用传输异常。Phase 3 切换 QUERY、交付无状态 sender/client，并把旧可靠链的
HTTP/配置依赖原地改接上述唯一 Transport 与类型化端口；旧链只继续拥有持久化、claim、重试、fencing 和终态等
可靠生命周期。Phase 4 建立 `WmsConfirmation` 与 `TransportTask` 后原子替换并删除该旧生命周期闭包。

**Tech Stack:** Python 3.13、Pydantic 2、HTTPX、FastAPI、SQLModel/SQLAlchemy、PostgreSQL、Celery、
Pytest 9、Ruff、Bandit、Import Linter、Bash architecture guardrails。

**Status:** Reviewed — 已顺延为 Phase 3 并应用 Phase 2 Transport/认证边界；Phase 2 子计划尚未获批，且本计划
Task 1 的 SPEC §14.2–14.3 同步仍未完成。两项门禁全部通过并重新批准本计划前，当前不得启动实施。

**Authority:**

- `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`
- `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- `docs/superpowers/plans/2026-08-04-wes-outbound-http-transport-convergence.md`（Phase 2 启动前另行编写并批准）
- `docs/contracts/wms-northbound-interaction-contract.md`
- `docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md`

**Implementation baseline:** `develop@28eb99d9`

---

## 1. 复审裁决

### 1.1 采用方案

采用“目标薄边界先落地，可靠所有者在 Phase 4 原子交接”的方案。

```text
Composition Root
  └─> OutboundHttpTransportFactory
       └─> WMS 专属、已装配认证的 OutboundHttpTransport
            └─> WMS Adapter / HttpWmsGateway
                 ├─> WmsCapabilities
                 ├─> WmsConfirmationSender
                 └─> WmsForwardedTransportClient

业务插件 ───────────────> WmsCapabilities ───────────────┐
                                                            │
Phase 4 WmsConfirmation ─> WmsConfirmationSender ───────────┤
                                                            ├─> WMS Adapter
Phase 4 TransportTask ───> WmsForwardedTransportClient ─────┘

Phase 3 切换：QUERY 切换到 HttpWmsGateway；旧 Runtime/Effect/status 的 HTTP/认证/配置依赖
             原地改接无状态 sender/client，删除 Provider Profile 和旧传输路径；旧链仍是确认与搬运
             唯一活动可靠性所有者，不增加第二张义务表、第二个 scanner 或第二条重试链。
Phase 4 切换：最终对象及权威测试通过 → 一次切换 → 删除旧所有者。
```

不采用以下两种方案：

1. **Phase 3 立即删除旧 Effect/status。** `WmsConfirmation` 与 `TransportTask` 尚不存在，会违反
   “最终可靠对象先建立、旧所有者后删除”的 SPEC 门禁并丢失可靠确认语义。
2. **新旧可靠生命周期并行运行。** 会形成双写、双领、双重试和终态竞争，违反单一运行路径约束。

### 1.2 Phase 2 输入与 WMS 认证边界

Phase 3 必须消费 Phase 2 已验收的 `OutboundHttpTransport`，不得重新创建或注入裸 `httpx.AsyncClient`。
Phase 2 的生产交付固定在 `src/core/outbound_http/`，且在该阶段不修改 WMS 旧模块、不激活 WMS Composition Root、
不切换或删除 WMS 调用路径。Phase 3 是 WMS 的首个接入阶段：由本计划负责装配 WMS 专属 Transport、切换调用者并
删除 WMS 重复传输/认证 owner；Phase 4 只消费本阶段交付的类型化端口，不直接 import 或装配 Phase 2。

| 所有者 | 拥有 | 不拥有 |
| --- | --- | --- |
| Phase 2 | 在 `src/core/outbound_http/` 全新增量交付 Client 生命周期、连接池、base URL、Timeout、单次发送、受限响应、通用传输异常、安全日志、认证策略接口、凭据解析、HMAC 基础计算、Clock、Nonce | WMS method/path/DTO、业务拒绝、canonical/Header、重试和生命周期；WMS 旧代码修改与生产装配 |
| WMS Adapter 包 | 当前 WMS 合同允许的 auth 闭集、无 Secret 的 canonical/Header/签名版本纯合同、method/path、wire DTO、业务结果解释、一次公开调用的 breaker permit/状态更新 | 凭据解析、HMAC 计算、Secret、裸 Client、连接池、通用 transport 异常 |
| Phase 4 可靠对象 | 根据类型化结果管理持久化、领取、轮询、重试、依赖暂停、终态和恢复 | HTTP、认证、签名协议、breaker permit/状态更新 |

当前公共认证闭集只允许 `NONE` 与 `HMAC_SHA256`：

- `NONE` 只允许合同明确无认证的可信隔离内网，且不得携带 `credential_reference`。
- `HMAC_SHA256` 只接受版本化 `credential_reference`；Secret 不进入 WMS 配置、数据库、日志或异常。
- `BASIC` 不实现、不进入公共配置枚举，也不显示为 WMS 通用选项；只有真实 WMS 合同要求时才能先修订计划。
- WMS canonical/Header 不得下沉为可配置模板、Header Mapping 或表达式 DSL。
- WMS Adapter 包只提供纯 canonical/Header/版本合同，不解析 `credential_reference`、不接收 Secret、
  不计算 HMAC；Composition Root 将该合同和类型化配置交给 Phase 2 Factory 完成认证装配。
- `HttpWmsGateway`、`WmsConfirmationSender` 和 `WmsForwardedTransportClient` 只接收已认证 Transport，
  不可见认证策略实例和签名过程。
- Phase 3 删除旧 Provider Profile、认证 fallback 和双轨传输路径，不把旧配置解释为长期兼容合同。

以上认证边界只适用于 **WES 调用 WMS 的 outbound HTTP**。`WMS → WES` callback 的 ingress 认证仍由
callback API 边界拥有，既不经过 Phase 2 Factory，也不复用 outbound `credential_reference`。Phase 3 必须在
删除 Provider Profile 的同一次原子切换中，将 `WmsInboundAuthPolicy` 改为消费 WMS 类型化配置中的独立
inbound callback policy：只有厂商合同明确允许的可信隔离内网可以无签名放行 WMS 业务事件/status hint；其余
请求继续由既有 API Application/HMAC 认证 fail closed。该策略不得读取 Secret、解析凭据、计算 HMAC，亦不得
把 inbound 认证扩张成第三套公共认证框架。

### 1.3 35 项合同的目标所有权

| 边界 | Operation | Phase 3 职责 | 明确不拥有 |
| --- | --- | --- | --- |
| `WmsCapabilities` | Q01–Q19 | 同步类型化查询、分页、错误映射、证据 | 插件 Decision、依赖暂停状态 |
| `WmsConfirmationSender` | E01–E07、E15 | 同步提交并返回终态业务结果 | 确认义务、领取、重试、恢复 |
| `WmsForwardedTransportClient` | E08–E14、E16 | submit/status/cancel 的类型化 HTTP Client | TransportTask、批次状态、轮询调度、重试、终态成员最终事实 |

E16 虽返回同步 HTTP 结果，但它取消的是已有搬运请求，因此归 Transport Port，不归 WMS 业务确认。

### 1.4 WMS outcome 与 evidence 失败语义

所有预期远端结果使用同一封闭 union，不用异常表达正常业务分支：

| 分支 | 必填字段 | 重试语义 |
| --- | --- | --- |
| `WmsCallSuccess[T]` | `value`、真实非空 `evidence_key` | 已成功，不重试 |
| `WmsBusinessReject` | `reason_code`、`message`、真实非空 `evidence_key` | 正常业务拒绝，不重试 |
| `WmsDependencyFailure` | `reason_code`、`message`、`retryable`、`retry_after_seconds`、真实非空 `evidence_key` | 只按显式 `retryable` 判定 |
| `WmsContractFailure` | `reason_code`、`message`、真实非空 `evidence_key` | 合同错误，不重试 |

Phase 2 只返回单次传输事实；Phase 3 WMS Adapter 将超时/5xx 等传输事实、断路器结果、业务拒绝和远端
Payload 不合约分别解释为依赖失败、业务拒绝和合同失败。公共 Transport 不得执行这些 WMS 业务映射。
无效本地配置、缺失依赖注入、程序错误和 evidence 基础设施失败不伪装成正常远端 outcome：

| evidence 故障点 | 必须行为 |
| --- | --- |
| 发送前无法创建 STARTED evidence | fail closed，不获取网络结果、不发送 HTTP，抛出明确的本地基础设施错误 |
| HTTP 已发送但 final evidence 无法持久化 | 不返回普通 outcome，标记“远端结果未知”；只携带真实已创建的 evidence key，不得伪造 |
| 写操作远端结果未知 | Phase 4 可靠所有者保留原 `dispatch_key` 恢复；禁止生成新键或按普通依赖失败自动重试 |

因此，所有正常 outcome 的 `evidence_key` 都非空且可查询；evidence 未成功持久化时不存在“看似正常但使用空键”
的第五分支。

### 1.5 配置裁决

用一个 `WMS_CONFIG_FILE` 取代 Provider Profile，并在 Composition Root 将公共 transport 配置与 WMS Adapter
配置分别交给各自类型化 loader。配置只包含：

- Phase 2 transport 所需的单一 `base_url`、连接池与 Timeout/响应预算；
- WMS 查询、确认和状态查询的分页、行数及业务 deadline 上限；
- WMS Adapter 允许闭集内的认证选择与版本化凭据引用；当前合同明确无认证时仅允许 `NONE`。该认证 binding
  只由 Composition Root 和 Phase 2 Factory 消费，不传入 `HttpWmsGateway` 或三条业务端口；
- 独立的 inbound callback policy，只表达“合同确认的可信隔离内网允许无签名”或“沿用既有 API
  Application/HMAC 认证”两种行为，不携带 outbound credential reference、Secret 或签名模板。

业务 Gateway 只接收 call settings 与已认证 Transport；callback ingress 只接收 inbound callback policy。两类
配置不可互相 fallback，也不得因 outbound 选择 `NONE` 就自动放宽 inbound callback。

删除 provider identity、contract digest、readiness、deployment attestation、process role、execution lane、
capability manifest、simulation registry 和运行时 conformance。method/path 不进入部署配置，由各垂直 capability
模块的 `WmsCallSpec` 固定；配置不得添加、删除或覆盖能力。

### 1.6 Phase 3 不可删除的资产

下列五个混合测试受测试收敛计划 Task 4/5 保护，只允许因 import 路径变化做机械更新，不得在 Phase 3
删除或削弱断言：

- `tests/mock/wms_operation_fixtures.py`
- `tests/contracts/wms_integration/test_wms_operation_catalog.py`
- `tests/contracts/wms_integration/test_effect_status_contract.py`
- `tests/support/runtime_inbox_processing_postgresql.py`
- `tests/integration/test_runtime_inbox_processing_postgresql.py`

旧 Effect/status/Outbox 可靠生命周期不得在 Phase 3 删除，但必须在 Phase 3 原地改接类型化 sender/client，
并删除其旧 Provider Profile、凭据解析、裸 Client 和通用 HTTP 路径；其 Phase 4 精确删除清单见 Task 10。

### 1.7 Phase 3 → Phase 4：WMS 转发 AGV/CTU 交接包

该交接包单独验收，但不形成 Phase 3.5、新运行阶段或第四条可靠性链。

| Operation | 业务搬运目标 | Phase 3 Client | Phase 4 `TransportTask` 承接 |
| --- | --- | --- | --- |
| E08 `request_rack_supply` | 为工作位补充指定类型货架 | submit + rack-supply status DTO | demand identity、领取、进度、最终到位事实 |
| E09 `request_rack_transport` | 搬运指定货架到目标工作位 | submit + rack-transport status DTO | 任务身份、重试、最终位置 |
| E10 `change_rack_face` | 在工作位切换货架面 | submit + face-change status DTO | 任务推进、最终货架面 |
| E11 `full_box_exchange` | 满箱换空箱并返回最终储位关系 | submit + exchange status DTO | 冻结成员、交换进度、最终关系 |
| E12 `move_bins_to_conveyor_entry` | CTU 批量投箱到输送线入口 | submit + entry-batch status DTO | 冻结批次成员、批次状态、终态成员最终事实、未知结果处理 |
| E13 `move_bins_from_conveyor_exit` | CTU 批量接回输送线出口料箱 | submit + exit-batch status DTO | 候选前缀、批次状态、终态成员最终货架/储位事实 |
| E14 `request_load_unit_transport` | 搬运托盘、料架或其他载具 | submit + load-unit status DTO | 任务身份、进度、最终位置 |
| E16 `cancel_request` | 取消上述 WMS 转发搬运请求 | 单次 cancel + typed disposition | 何时允许取消、取消后的任务状态 |

**Phase 3 必须交付：**

- 八类 request/ACK/pending/terminal/cancel wire DTO 和稳定拒绝码；
- 七个显式 submit、七个显式 status、一个显式 cancel 方法；
- `operation identity + dispatch_key` 幂等头、Provider reference 和 source version 的透传校验；
  `dispatch_key` 是 submit、ACK、status、terminal、cancel、hint 的唯一 wire 幂等键，不定义
  `idempotency_key` 别名或双键映射；
- method/path、WMS 业务预算、WMS 结果解释、脱敏同步调用证据，以及无 Secret 的 WMS-specific
  canonical/Header/签名版本纯合同；
- 由 Composition Root 将纯合同交给 Phase 2 Factory，复用其 Timeout、受限响应、通用异常分类、凭据解析、
  HMAC/Clock/Nonce 和认证装配；WMS Adapter 不调用 credential resolver 或 HMAC 原语；
- 无状态 fake，供 Phase 4 在不启动真实 WMS、Celery 或旧 Runtime 的情况下测试 Transport Port。
- sender/client 在 Phase 3 原子切换中成为旧 Effect/status 生命周期的唯一 HTTP 出口；该装配不新增持久化、
  claim、轮询、重试或终态 owner，并在 Phase 4 与旧生命周期一并替换。

**Phase 4 才能拥有：**

- `TransportTask`、批次成员和状态持久化；
- due claim、轮询间隔、重试预算、依赖暂停和恢复；
- callback/status hint 唤醒、source-version fencing、迟到结果和未知物理结果处理；
- 任务终态驱动的对象/位置投影以及插件下一步 Decision；
- 无状态 Client 到 `WmsConfirmation`/`TransportTask` 的生产装配和原子切换。

**交接不变量：** Phase 3 Client 的每次调用只完成一次 HTTP 交互；不得在 Client 内循环到终态、写
`TransportTask`、启动定时任务或修改对象/位置投影。旧可靠 owner 可以在 Phase 3 原地改接该 Client，但只能保留
既有持久化、claim、重试、fencing 和终态职责，不得保留旧 HTTP/认证/配置路径或新增第二套状态。Phase 4 不得重新实现
path、DTO、WMS 结果映射、WMS-specific 认证合同、breaker permit 或 WMS evidence，否则视为跨层复制。

---

## 2. 最终文件布局

Phase 3 完成后，目标公共边界为：

```text
src/app/wms_integration/
├── capabilities/
│   ├── _shared.py
│   └── 35 个合同模块               # 精确文件名见北向合同的 Capability module 列
├── ports/
│   ├── capabilities.py
│   ├── confirmation.py
│   ├── forwarded_transport.py
│   └── outcomes.py
├── adapters/
│   ├── auth.py                       # 无 Secret 的 WMS canonical/Header/版本纯合同
│   └── http_gateway.py
├── configuration.py
├── factory.py
├── inbound/
│   ├── contracts.py
│   └── normalizer.py
├── models/
│   ├── circuit_breaker.py
│   └── evidence.py
├── repositories/
│   ├── circuit_breaker_repository.py
│   └── evidence_repository.py
└── services/
    ├── circuit_breaker_service.py
    ├── evidence_service.py
    ├── redaction.py
    └── response_mapping.py
```

每个 capability 模块同时定义 request/result DTO、固定 method/path、稳定拒绝码和一个不可变
`WmsCallSpec`；`_shared.py` 只提供 `StrictWmsModel`、分页/value type 和 `WmsCallSpec` 数据结构，不保存能力列表。
`ports`、`configuration.py`、`factory.py` 和 `inbound` 是允许外部依赖的公共边界；`factory.py` 只接收已经由
Phase 2 Factory 装配完成的 Transport，不创建 Client。Gateway 的私有 `_call`
只接收调用方显式传入的 spec，不按字符串查 registry。旧可靠性文件在 Phase 3 期间仍存在，但不得被目标公共
模块 import，且必须由 Task 10 的 Phase 4 删除门禁锁定。

新增、优化或删除 WMS 能力的固定动作只有：修改一个 capability 模块、对应窄 Protocol 方法、Gateway 显式
方法和同名测试。测试态 harness 会扫描 capability 文件并检查四者闭包；生产包不存在同类扫描器或目录发现。

| 变更 | 开发动作 | 完成门禁 |
| --- | --- | --- |
| 新增能力 | 新增一个垂直模块、一个显式端口/Gateway 方法和同名测试 | harness 发现且四者闭合；北向合同同步增加一行 |
| 优化能力 | 只改该模块与同名测试；共享逻辑仅在三处重复且语义稳定后下沉 `_shared.py` | 其他既有能力合同无差异 |
| 删除能力 | 先删除调用者，再删除端口/Gateway 方法、模块和同名测试 | harness、import closure、文件集与北向合同同时归零 |

### 2.1 当前 54 个生产文件处置矩阵

`MOVE` 表示先把仍需语义迁入上述目标文件并更新全部 import，再删除源文件；`PHASE4_HANDOFF` 表示 Phase 3
只保留该文件的可靠生命周期职责，允许且必须机械移除 Provider Profile、凭据解析、裸 Client 和旧传输依赖，
并在 Phase 4 与最终可靠所有者原子替换后删除。`PHASE4_HANDOFF` 不是旧 HTTP/认证路径的保留许可。

| 当前文件 | 处置 | 最终所有者或删除条件 |
| --- | --- | --- |
| `__init__.py` | KEEP | 保持空领域入口，不 re-export 旧类型 |
| `adapters/__init__.py` | KEEP | 只导出目标 Gateway 和 Phase 4 handoff adapter；Phase 4 删除 handoff 导出 |
| `adapters/effect_status_query_adapter.py` | PHASE4_HANDOFF | Phase 3 改为只调用 `WmsForwardedTransportClient`，Phase 4 与旧 status owner 删除 |
| `deployment_attestation.py` | DELETE | Provider/Profile 部署鉴证不进入目标配置或 Phase 4 生命周期 |
| `effect_lane_runtime.py` | PHASE4_HANDOFF | Phase 3 改为持有类型化 sender/client 而非 HTTP Client/readiness，Phase 4 删除 |
| `effect_preparation_runtime.py` | PHASE4_HANDOFF | 与旧 preparation owner 原子删除 |
| `effect_runtime.py` | PHASE4_HANDOFF | 与 `WmsConfirmation`/`TransportTask` 切换时删除 |
| `endpoint_compiler.py` | DELETE | Phase 3 status/Effect 改接类型化端口后删除，不保留 Provider endpoint 编译 |
| `evidence/__init__.py` | DELETE | `models/evidence.py` 是唯一 WMS 调用证据模型所有者 |
| `evidence/catalog.py` | DELETE | 删除 Provider reference catalog/drift 平台语义 |
| `evidence/envelope.py` | DELETE | 最终外部证据包络由 `InboundEvidence`/共享 callback 合同拥有 |
| `models/__init__.py` | KEEP | 只导出 breaker/evidence 目标模型 |
| `models/circuit_breaker.py` | KEEP | 共享 PostgreSQL breaker 状态 |
| `models/evidence.py` | KEEP | 唯一 WMS 调用 evidence 模型；旧链字段冻结到 Phase 4，最终模型删除 provider identity/digest |
| `models/ports.py` | DELETE | 三项旧同步 DTO 被垂直 capability 模块替代 |
| `operation_contract.py` | PHASE4_HANDOFF | 旧链静态依赖；目标模块不得 import，Phase 4 删除 |
| `operation_registry.py` | PHASE4_HANDOFF | 旧链静态依赖；不扩展，Phase 4 删除 |
| `ports/__init__.py` | KEEP | 只公开目标窄端口；旧延迟导出在 Phase 4 删除 |
| `ports/document_operations.py` | MOVE | DTO 分拆到对应 Q08–Q13/Q19 capability 模块后删除 |
| `ports/effect_preparation.py` | PHASE4_HANDOFF | 与旧 Effect preparation owner 删除 |
| `ports/effect_status.py` | PHASE4_HANDOFF | 只保留 claim/fencing/terminal DTO；Phase 3 删除 provider/auth/binding 字段，Phase 4 删除 |
| `ports/event.py` | MOVE | 迁入 `inbound/contracts.py` |
| `ports/fulfillment_operations.py` | MOVE | DTO 分拆到对应 E07–E16 capability 模块；旧链 import 机械改向后删除 |
| `ports/inventory_operations.py` | MOVE | DTO 分拆到对应 Q14/Q15/E01–E06 capability 模块后删除 |
| `ports/master_data_operations.py` | MOVE | DTO 分拆到对应 Q01–Q07 capability 模块后删除 |
| `ports/operation_common.py` | MOVE | 公共 value type 迁入 `capabilities/_shared.py` 后删除 |
| `ports/query_execution.py` | DELETE | QUERY 切换后由显式 `WmsCapabilities` 取代 |
| `ports/query_outcome.py` | PHASE4_HANDOFF | 旧 status adapter 静态依赖；Phase 4 删除 |
| `ports/reconciliation_operations.py` | MOVE | DTO 分拆到对应 Q16–Q18 capability 模块后删除 |
| `provider_manifest.py` | DELETE | Phase 3 删除旧 Provider 能力/鉴证平台 |
| `provider_profile.py` | DELETE | `WMS_CONFIG_FILE` 和 Adapter 认证闭集激活时原子删除 |
| `provider_readiness.py` | DELETE | Composition Root/类型化端口装配取代 process role/readiness |
| `provider_simulator_registry.py` | DELETE | 目标无 Provider simulator registry |
| `provider_startup.py` | DELETE | Phase 2 Factory + WMS factory 取代 Provider startup |
| `query_evidence.py` | PHASE4_HANDOFF | 旧 runtime/status adapter 静态依赖；Phase 4 删除 |
| `query_executor.py` | DELETE | 19 项 QUERY 原子切换到 Gateway 后删除 |
| `query_projection.py` | DELETE | QUERY 返回 typed result，不保留投影 facade |
| `query_response.py` | DELETE | 共享 response mapping 接管后删除 |
| `query_runtime.py` | DELETE | API/Celery Gateway 装配接管后删除 |
| `repositories/__init__.py` | KEEP | 只导出 breaker/evidence repository |
| `repositories/circuit_breaker_repository.py` | KEEP | 共享 breaker persistence |
| `repositories/evidence_repository.py` | KEEP | 唯一 evidence persistence |
| `runtime_factory.py` | PHASE4_HANDOFF | Phase 3 只注入类型化 client/evidence/breaker，不解析凭据或借用 Client；Phase 4 删除 |
| `services/__init__.py` | KEEP | 收缩为目标 transport/evidence/breaker/redaction 导出 |
| `services/callback_normalizer.py` | PHASE4_HANDOFF | 旧 status hint 类型静态依赖；Phase 4 由最终 inbound owner 接管 |
| `services/circuit_breaker_service.py` | KEEP | 共享 breaker service |
| `services/evidence_service.py` | KEEP | 同步 evidence 目标 owner；旧 async summary 在 Phase 4 删除 |
| `services/exceptions.py` | MOVE | 正常远端分支迁入 outcome；仅保留明确的本地基础设施错误 |
| `services/fulfillment_lifecycle.py` | PHASE4_HANDOFF | 与旧 fulfillment lifecycle 原子删除 |
| `services/http_transport.py` | DELETE | Phase 2 不移动或修改该旧 helper；Phase 3 将无 Secret 的 WMS canonical/Header 纯合同迁入 `adapters/auth.py`，目标 Gateway 改接 `src/core/outbound_http/` 后由 Task 8 删除整个旧 helper |
| `services/redaction.py` | KEEP | 共享脱敏/hash |
| `services/wms_event_normalizer.py` | MOVE | 迁入 `inbound/normalizer.py` |
| `state_machine.py` | PHASE4_HANDOFF | 与旧 Effect/status state machine 删除 |
| `transport_url.py` | MOVE | URL 校验迁入 `configuration.py` 后删除 |

矩阵验收必须证明恰好覆盖上述 54 个当前文件；若实施基线文件集变化，先更新矩阵再编码。Phase 3 完成时，
`models/evidence.py` 是唯一 WMS 调用 evidence 模型，`evidence/` 目录不存在；所有 `provider_*`、
`endpoint_compiler.py`、`deployment_attestation.py`、WMS 裸 Client 和旧认证路径均已删除；`PHASE4_HANDOFF`
文件只包含可靠生命周期职责。最终目标文件集与本节布局一致，所有 `DELETE` 源文件和旧抽象 public export 零命中。

---

## 3. 类型化端口冻结

### 3.1 `WmsCapabilities`

按接口隔离原则分成四个只读子 Protocol，组合 Protocol 只用于插件装配：

| 子 Protocol | 方法与类型 |
| --- | --- |
| `WmsMasterDataCapabilities` | `get_material(GetMaterialRequest) -> WmsCallOutcome[GetMaterialResult]` |
|  | `list_materials(ListMaterialsRequest) -> WmsCallOutcome[ListMaterialsResult]` |
|  | `list_zones(ListZonesRequest) -> WmsCallOutcome[ListZonesResult]` |
|  | `list_locations(ListLocationsRequest) -> WmsCallOutcome[ListLocationsResult]` |
|  | `get_rack(GetRackRequest) -> WmsCallOutcome[GetRackResult]` |
|  | `list_racks(ListRacksRequest) -> WmsCallOutcome[ListRacksResult]` |
|  | `get_bin(GetBinRequest) -> WmsCallOutcome[GetBinResult]` |
| `WmsDocumentCapabilities` | `get_grn(GetGrnRequest) -> WmsCallOutcome[GetGrnResult]` |
|  | `list_grn_packages(ListGrnPackagesRequest) -> WmsCallOutcome[ListGrnPackagesResult]` |
|  | `get_pick_order(GetPickOrderRequest) -> WmsCallOutcome[GetPickOrderResult]` |
|  | `get_outbound_order(GetOutboundOrderRequest) -> WmsCallOutcome[GetOutboundOrderResult]` |
|  | `get_wave(GetWaveRequest) -> WmsCallOutcome[GetWaveResult]` |
|  | `get_task_snapshot(GetTaskSnapshotRequest) -> WmsCallOutcome[GetTaskSnapshotResult]` |
|  | `validate_rough_sorter_admission(ValidateRoughSorterAdmissionRequest) -> WmsCallOutcome[ValidateRoughSorterAdmissionResult]` |
| `WmsInventoryCapabilities` | `query_inventory(InventorySnapshotQueryRequest) -> WmsCallOutcome[InventorySnapshotQueryResult]` |
|  | `get_reservation(GetReservationRequest) -> WmsCallOutcome[GetReservationResult]` |
| `WmsReconciliationCapabilities` | `check_bin_drift(CheckBinDriftRequest) -> WmsCallOutcome[CheckBinDriftResult]` |
|  | `check_rack_drift(CheckRackDriftRequest) -> WmsCallOutcome[CheckRackDriftResult]` |
|  | `check_full_drift(CheckFullDriftRequest) -> WmsCallOutcome[CheckFullDriftResult]` |

所有方法均为 `async` 且只接收一个严格、不可变 request DTO。禁止公共 `execute(operation_name, payload)`。

### 3.2 `WmsConfirmationSender`

| 方法 | Request | Result |
| --- | --- | --- |
| `reserve_inventory` | `ReserveInventoryRequest` | `ReserveInventoryResult` |
| `release_reservation` | `ReleaseReservationRequest` | `ReleaseReservationResult` |
| `confirm_inbound` | `ConfirmInboundRequest` | `ConfirmInboundResult` |
| `confirm_outbound` | `ConfirmOutboundRequest` | `ConfirmOutboundResult` |
| `transfer_inventory` | `TransferInventoryRequest` | `TransferInventoryResult` |
| `confirm_return_putaway` | `ConfirmReturnPutawayRequest` | `ConfirmReturnPutawayResult` |
| `notify_pkg_binding` | `NotifyPkgBindingRequest` | `NotifyPkgBindingResult` |
| `publish_manual_task` | `PublishManualTaskRequest` | `PublishManualTaskResult` |

每个方法返回对应的 `WmsCallOutcome[Result]`。`dispatch_key` 是 WMS 原子幂等合同的一部分，不创建
Outbox 或重试；Phase 4 的 `WmsConfirmation` 负责稳定生成并保存它。

### 3.3 `WmsForwardedTransportClient`

提供七个显式 `submit_*`、七个显式 `get_*_status` 和一个 `cancel_request` 方法。submit 成功返回
`WmsTransportAccepted`；status 成功返回对应的 `WmsTransportPending | OperationResult`；cancel 返回
`CancelRequestResult`。公共端口不提供 scanner、poll-until-terminal、callback handler 或后台任务。

---

## 4. 实施任务

**Phase 3 entry gate:** Phase 2 Outbound HTTP 子计划必须已经编写、评审、批准并完成退出门禁；
`src/core/outbound_http/` 中的 `OutboundHttpTransportFactory`、Transport、认证策略接口、凭据解析、HMAC 原语和
生命周期合同均可直接消费，测试树已提供 MockTransport/Fake 证明且生产包不导出测试替身。Phase 2 交付差异不得
包含 WMS 旧生产文件或 WMS Composition Root 修改；该条件未满足时，本节所有生产代码任务均不得启动。

执行分五条 lane：A 负责权威文档和两类处置矩阵；B 负责垂直 capability/ports 与测试态 conformance；C 负责
配置、HTTP/evidence/breaker 和部署生命周期；D 在 B+C 通过后执行 Gateway、QUERY 切换、索引收缩与旧测试
处置；E 在 D 后冻结 Phase 4 handoff 并完成验收。B/C 可以并行，但必须预先按文件分配所有权；D/E 顺序执行，
Task 8/9 保持同一原子工作树。

### Task 1：冻结权威文档和原子交接边界

**Status:** 未完成，且是实施阻断项。本轮只在规划层裁决目标顺序；顶层 SPEC §14.2–14.3 仍保留旧九阶段编号，
并要求 Phase 3 不改接旧 Effect/status、把 Provider/Catalog 整体冻结到下一阶段。必须先在单独文档评审批次中同步并
批准该 SPEC，再允许从 Task 2 开始；不得仅凭总控计划覆盖仍冲突的权威输入。

**Files:**

- Modify: `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`
- Modify: `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- Reference: `docs/superpowers/README.md`（Task 1 外部归档索引）
- Modify: `docs/business/wms_rcs_interface_requirements.md`
- Modify: `docs/business/wms_full_factory_operation_blueprint.md`
- Modify: `docs/contracts/wms-northbound-interaction-contract.md`
- Modify: `docs/architecture/authority-matrix.md`

- [ ] 同步并批准当前 SPEC §14.2–14.3：改为十阶段编号，明确 Phase 3 只机械改接旧可靠链的 Transport/配置/认证依赖并
  删除 Provider/Catalog，Phase 4 在最终可靠对象通过后原子删除旧 Effect/status/SystemOutbox 生命周期；保留 35 项
  wire contract，不改变业务需求。
- [x] 在 §5.3 区分同步查询/确认与 WMS 转发异步搬运，声明生命周期归 `TransportTask`。
- [x] 在总控 Phase 3/4 写入“旧可靠所有者保留到最终对象后原子删除”的单向交接规则。
- [x] 把已标记为 `Superseded` 的旧 WMS SPEC 移出项目，仅在外部归档目录保留完整历史内容；项目内不得保留
  副本、占位文件、软链接或转发文档，也不得再把它或旧 registry 作为当前架构真源。
- [x] 更新北向合同的所有权说明：Q01–Q19、E01–E07/E15、E08–E14/E16 分属三条端口。
- [x] 更新 35 项业务蓝图，删除 Provider/Catalog、RuntimeIntent/Effect、lane 和 Manifest 当前目标描述。
- [x] 在北向合同补齐 35 项固定 method/path；目标 WMS 尚未确认的 path 必须先完成合同裁决，不得退回部署配置、
  运行时 registry 或在实现阶段自行发明。
- [x] 同步 `authority-matrix.md`，只保留 `WmsCapabilities`、`WmsConfirmation`、`TransportTask` 与
  `InboundEvidence` 的最终所有权名称。

**Verification:**

Task 1 只做文档裁决与外部归档，不新增 pytest；仅执行 Markdown、引用、路径缺席和 whitespace 检查。
上述 SPEC 同步获批前验证不得标绿，Task 2 及其后的生产代码任务不得开始。实现行为门禁从 Task 2 开始，并随对应
生产代码变更交付。

```bash
rtk ./scripts/markdownlint.sh \
  docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md \
  docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md \
  docs/business/wms_rcs_interface_requirements.md \
  docs/business/wms_full_factory_operation_blueprint.md \
  docs/contracts/wms-northbound-interaction-contract.md \
  docs/architecture/authority-matrix.md
rtk proxy test ! -e docs/superpowers/specs/2026-07-28-wms-full-factory-integration-design.md
rtk proxy test -f ../archive_docs/wes_backend/2026-07-28-wms-full-factory-integration-design.md
rtk git diff --check
```

Expected: markdown lint、归档位置检查和 whitespace check 均退出 0；旧 WMS SPEC 已移出项目，只在外部归档中
保留完整历史内容。

**Commit boundary:** 只暂存本任务 `Files` 的精确路径，禁止目录级暂存和 `git add -A`。提交说明：
`docs(wms): 冻结 WMS 薄边界与原子交接`。

### Task 2：先建立公共边界和测试重量门禁

**Files:**

- Create: `tests/architecture/test_wms_thin_public_boundary.py`
- Modify: `scripts/architecture-guardrails.sh`
- Modify: `tests/architecture/test_wms_integration_boundary_guardrail.py`

- [ ] 写失败测试，扫描 `capabilities/`、四个目标 `ports/*.py`、`adapters/auth.py`、
  `adapters/http_gateway.py`、`configuration.py`、`factory.py` 和 `inbound/`，禁止 import：
  `src.app.runtime`、`src.app.sys`、`operation_contract`、`operation_registry`、`provider_*`、
  `effect_runtime`、`effect_status`、`RuntimeIntent`、`Effect`、`Outbox`、`SystemCapability`、`httpx`；
  WMS Adapter 只能 import Phase 2 Transport/认证合同，不得接收 `httpx.AsyncClient`、credential resolver、
  Secret 或 HMAC 实现。
- [ ] 用仓库内临时违规 fixture 分别验证公共端口和 Adapter 规则能捕获 `httpx` import、Client 构造、凭据解析
  及 HMAC 实现；目标文件尚不存在时只验证规则实现，
  不做“空目录即通过”的伪门禁。
- [ ] 断言生产代码不存在 capability 文件扫描、目录发现、中心 registry、generic public `call` 或 WMS codegen。

**Run RED:**

```bash
rtk uv run pytest \
  tests/architecture/test_wms_thin_public_boundary.py \
  tests/architecture/test_wms_integration_boundary_guardrail.py -q
```

Expected: 新规则实现前因无法识别违规 fixture 失败；规则实现后通过，不使用 `skip` 或 `xfail` 暂存。

- [ ] 在 `architecture-guardrails.sh` 增加 `WMS_THIN_PUBLIC_BOUNDARY` 规则；规则只扫描目标公共文件，
  不为旧可靠文件新增 allowlist。
- [ ] 保留现有 `WMS_INTEGRATION_BOUNDARY` 对内部领域直接 import WMS transport/service 的禁止规则。

**Commit boundary:** 只暂存本任务的三个精确路径。提交说明：
`test(wms): 冻结 WMS 薄公共边界`。

### Task 3：建立 35 个垂直 capability 模块

**Files:**

- Create: `src/app/wms_integration/capabilities/__init__.py`
- Create: `src/app/wms_integration/capabilities/_shared.py`
- Create: 北向合同 `Capability module` 列精确列出的 35 个 `src/app/wms_integration/capabilities/*.py`
- Create: `tests/contracts/wms_integration/test_wms_wire_models.py`
- Create: `tests/contracts/wms_integration/test_wms_capability_conformance.py`
- Modify: 下方冻结清单中的 65 个 production/test importer；实施前若同一查询结果发生变化，必须先更新并重新批准
  本计划，不得让已批准任务范围随工作区动态扩张。

<details>
<summary>Task 3 冻结 importer 清单（65 个）</summary>

```text
src/app/runtime/capabilities/material_flow/rough_sorter_q19_admission_service.py
src/app/runtime/capabilities/material_flow/sorter_inbound_preview_service.py
src/app/runtime/orchestration/services/effect_reducer_service.py
src/app/runtime/orchestration/services/full_box_exchange_service.py
src/app/runtime/orchestration/services/rack_demand_service.py
src/app/runtime/orchestration/services/wms_conveyor_batch_service.py
src/app/runtime/orchestration/services/wms_conveyor_return_batch_service.py
src/app/runtime/orchestration/services/wms_effect_status_service.py
src/app/runtime/orchestration/services/wms_fulfillment_domain_projector.py
src/app/runtime/system_capabilities/wms/document/get_grn/definition.py
src/app/runtime/system_capabilities/wms/document/get_outbound_order/definition.py
src/app/runtime/system_capabilities/wms/document/get_pick_order/definition.py
src/app/runtime/system_capabilities/wms/document/get_task_snapshot/definition.py
src/app/runtime/system_capabilities/wms/document/get_wave/definition.py
src/app/runtime/system_capabilities/wms/document/list_grn_packages/definition.py
src/app/runtime/system_capabilities/wms/document/validate_rough_sorter_admission/definition.py
src/app/runtime/system_capabilities/wms/fulfillment/cancel_request/definition.py
src/app/runtime/system_capabilities/wms/fulfillment/change_rack_face/definition.py
src/app/runtime/system_capabilities/wms/fulfillment/full_box_exchange/definition.py
src/app/runtime/system_capabilities/wms/fulfillment/move_bins_from_conveyor_exit/definition.py
src/app/runtime/system_capabilities/wms/fulfillment/move_bins_to_conveyor_entry/definition.py
src/app/runtime/system_capabilities/wms/fulfillment/notify_pkg_binding/definition.py
src/app/runtime/system_capabilities/wms/fulfillment/publish_manual_task/definition.py
src/app/runtime/system_capabilities/wms/fulfillment/request_load_unit_transport/definition.py
src/app/runtime/system_capabilities/wms/fulfillment/request_rack_supply/definition.py
src/app/runtime/system_capabilities/wms/fulfillment/request_rack_transport/definition.py
src/app/runtime/system_capabilities/wms/inventory/confirm_inbound/definition.py
src/app/runtime/system_capabilities/wms/inventory/confirm_outbound/definition.py
src/app/runtime/system_capabilities/wms/inventory/confirm_return_putaway/definition.py
src/app/runtime/system_capabilities/wms/inventory/get_reservation/definition.py
src/app/runtime/system_capabilities/wms/inventory/query_inventory/definition.py
src/app/runtime/system_capabilities/wms/inventory/release_reservation/definition.py
src/app/runtime/system_capabilities/wms/inventory/reserve_inventory/definition.py
src/app/runtime/system_capabilities/wms/inventory/transfer_inventory/definition.py
src/app/runtime/system_capabilities/wms/master_data/get_bin/definition.py
src/app/runtime/system_capabilities/wms/master_data/get_material/definition.py
src/app/runtime/system_capabilities/wms/master_data/get_rack/definition.py
src/app/runtime/system_capabilities/wms/master_data/list_locations/definition.py
src/app/runtime/system_capabilities/wms/master_data/list_materials/definition.py
src/app/runtime/system_capabilities/wms/master_data/list_racks/definition.py
src/app/runtime/system_capabilities/wms/master_data/list_zones/definition.py
src/app/runtime/system_capabilities/wms/reconciliation/check_bin_drift/definition.py
src/app/runtime/system_capabilities/wms/reconciliation/check_full_drift/definition.py
src/app/runtime/system_capabilities/wms/reconciliation/check_rack_drift/definition.py
src/app/runtime/workline_plugins/rough_sorter/pre_attempt.py
src/app/runtime/workline_plugins/smt_sorting_inbound/contracts.py
src/app/wms_integration/effect_runtime.py
src/app/wms_integration/operation_registry.py
src/app/wms_integration/ports/effect_status.py
tests/contracts/wms_integration/test_effect_status_contract.py
tests/contracts/wms_integration/test_provider_conformance_suite.py
tests/contracts/wms_integration/test_wms_batch_ack_contract.py
tests/contracts/wms_integration/test_wms_operation_catalog.py
tests/contracts/wms_integration/test_wms_provider_endpoint_compiler.py
tests/contracts/wms_integration/test_wms_query_projection.py
tests/contracts/workline/test_external_contract_profile_fixtures.py
tests/mock/test_wms_mock_server.py
tests/mock/wms_northbound_contract.py
tests/support/runtime_inbox_processing_postgresql.py
tests/support/wms_provider_conformance.py
tests/sys/test_wms_async_effect_dispatch.py
tests/workline_runtime/system_capabilities/test_wms_effect_status_reliability.py
tests/workline_runtime/system_capabilities/test_wms_effect_status_service.py
tests/workline_runtime/system_capabilities/wms/test_query_inventory_capability.py
tests/workline_runtime/test_effect_reducer.py
```

</details>

冻结清单的漂移检查命令：

```bash
rtk rg -l 'ports\.(master_data|document|inventory|reconciliation|fulfillment)_operations' src tests | sort
```

- [ ] 将 `StrictWmsModel`、cursor、Decimal、RFC3339、`dispatch_key` value type 和 `WmsCallSpec` 结构移到
  `_shared.py`；该文件不得持有能力集合或 import 具体能力。
- [ ] 将五个现有 `*_operations.py` 中的 DTO、validator 和 value type 按 operation 分拆到 35 个模块；每个模块
  同时固定 identity、method/path、分页语义、允许拒绝码和 request/result type，不复制字段、不保留 re-export。
- [ ] 保持 Pydantic `extra="forbid"`、`frozen=True`、`strict=True`，并保留 Decimal string、UTC 时间、
  tuple 和 operation-specific 终态身份校验。
- [ ] 参数化测试全部 wire model：未知字段拒绝、scalar 不隐式转换、model 不可变、request/result
  round-trip 稳定。
- [ ] conformance harness 只存在于 `tests/`：扫描 35 个 capability 文件，检查模块导出、北向合同 identity、
  Protocol/Gateway 显式方法和同名测试。禁止生成生产 registry 或把测试 harness 放入 `src/`。
- [ ] 机械更新受保护混合测试的 import，不改变它们的业务与可靠性断言。

**Run RED/GREEN:**

```bash
rtk uv run pytest \
  tests/contracts/wms_integration/test_wms_wire_models.py \
  tests/contracts/wms_integration/test_wms_capability_conformance.py -q
rtk uv run pytest \
  tests/contracts/wms_integration/test_wms_operation_catalog.py \
  tests/contracts/wms_integration/test_effect_status_contract.py -q
```

Expected: 新 wire tests 通过；两个受保护混合测试仍保持原断言并通过。

**Commit boundary:** 暂存本任务逐项列出的 capability、测试和 importer 精确路径。提交说明：
`refactor(wms): 将 wire 合同拆分为垂直能力模块`。

### Task 4：实现封闭 outcome 和三条类型化端口

**Files:**

- Create: `src/app/wms_integration/ports/outcomes.py`
- Create: `src/app/wms_integration/ports/capabilities.py`
- Create: `src/app/wms_integration/ports/confirmation.py`
- Create: `src/app/wms_integration/ports/forwarded_transport.py`
- Modify: `src/app/wms_integration/ports/__init__.py`
- Create: `tests/contracts/wms_integration/test_wms_thin_port_shapes.py`
- Create: `tests/contracts/wms_integration/test_wms_outcomes.py`

- [ ] 按 §1.4 契约表写四分支 union 测试；所有正常分支必须带真实非空 `evidence_key`，依赖失败必须显式
  给出 `retryable`，业务拒绝和合同失败默认不可重试。
- [ ] 单独测试发送前 evidence 失败与发送后 evidence finalization 失败；二者均不得构造普通 outcome 或伪造
  `evidence_key`，写操作的未知结果必须保留原 `dispatch_key`。
- [ ] 按 §3 的完整方法表实现 Protocol；所有返回类型为具体 `WmsCallOutcome[T]`。
- [ ] `WmsForwardedTransportClient` 的 status 方法使用明确的 pending/terminal 类型，不返回 `dict`、
  `Any` 或旧 `WmsEffectStatusSnapshot`。
- [ ] `ports/__init__.py` 为目标端口增加显式导出，不新增旧 query/effect/status re-export；旧链当前依赖的延迟
  export 保持原样并登记到 Task 10，Phase 4 原子删除。

**Run:**

```bash
rtk uv run pytest \
  tests/contracts/wms_integration/test_wms_thin_port_shapes.py \
  tests/contracts/wms_integration/test_wms_outcomes.py -q
rtk uv run pyright src/app/wms_integration/capabilities src/app/wms_integration/ports
```

Expected: 端口形状、closed union 和类型检查全部通过。

**Commit boundary:** 只暂存本任务 `Files` 的精确路径。提交说明：
`feat(wms): 定义类型化 WMS 薄端口`。

### Task 5：建立目标 Composition Root/WMS 类型化配置

**Files:**

- Create: `src/app/wms_integration/configuration.py`
- Create: `tests/contracts/wms_integration/test_wms_connection_settings.py`

- [ ] 写失败测试：配置文件必须绝对、可读、YAML 键唯一；`WmsConnectionSettings` 是 Composition Root
  读取的封闭复合配置，分为 Phase 2 transport、outbound 认证 binding、WMS call settings 和独立 inbound
  callback policy。transport 负责 `base_url`、连接池、Timeout 和响应预算；outbound 认证 binding 只包含允许
  闭集内的方案和版本化 `credential_reference`；WMS call settings 只包含分页、行数和业务 deadline；inbound
  callback policy 只允许“合同确认的可信隔离内网无签名”或“沿用既有 API Application/HMAC”两种行为。
  业务 Gateway 只接收 call settings 与已认证 Transport，不接收认证 binding；callback ingress 只接收 inbound
  policy。配置不接收 operation path、能力开关、原始 Secret、Header Mapping 或签名表达式。
- [ ] 验证 `NONE` 仅用于合同明确无认证的可信隔离内网且 credential reference 为空；`HMAC_SHA256` 必须使用
  版本化 reference；`BASIC`、未知方案和 Adapter 未允许的方案全部 fail fast。
- [ ] 验证 outbound auth 与 inbound callback policy 不联动：outbound `NONE` 不自动允许 unsigned callback；
  inbound 隔离内网策略不解析 credential，也不绕过非 WMS callback 的既有 API 认证。
- [ ] 测试缺失、不可读、格式错误和无效配置均 fail fast；不实现内网地址信任矩阵、SSRF 防护层或全局 import-time singleton。
- [ ] 本任务只完成目标配置模型和 loader 合同，不创建 factory，不接入 `src/core/conf.py`、API、Celery 或旧
  Runtime；旧 Provider 仍是唯一活动配置来源，因此不存在两个活动配置。
- [ ] 不让新 loader 读取旧 Provider YAML，不增加旧变量 alias、fallback 或双文件读取。

**Run:**

```bash
rtk uv run pytest tests/contracts/wms_integration/test_wms_connection_settings.py -q
```

Expected: 目标配置解析、验证和 fail-fast 测试通过；生产装配仍只使用原有 Provider 配置，新目标配置尚未激活。

**Commit boundary:** 只暂存本任务 `Files` 的精确路径。提交说明：
`feat(wms): 定义 WMS 连接配置`。

### Task 6：消费 Phase 2 Transport，收敛 WMS 结果、同步证据和 breaker

**Files:**

- Create: `src/app/wms_integration/services/response_mapping.py`
- Create: `src/app/wms_integration/adapters/auth.py`
- Modify: `src/app/wms_integration/services/redaction.py`
- Modify: `src/app/wms_integration/services/evidence_service.py`
- Modify: `src/app/wms_integration/models/evidence.py`
- Modify: `src/app/wms_integration/repositories/evidence_repository.py`
- Modify: `src/app/wms_integration/services/circuit_breaker_service.py`
- Modify: `src/app/wms_integration/query_executor.py`
- Create: `tests/contracts/wms_integration/test_wms_response_mapping.py`
- Create: `tests/contracts/wms_integration/test_wms_sync_evidence.py`
- Modify: `tests/wms_integration/test_evidence.py`
- Modify: `tests/wms_integration/test_query_evidence_branches.py`
- Modify: `tests/resilience/test_wms_circuit_breaker.py`

- [ ] Phase 2 Transport 已完整覆盖 Client 生命周期、单次发送、受限 wire/decoded response、压缩预算和通用
  传输异常；本任务不得复制这些测试或实现，只用其 typed transport fact 验证 WMS 408/429/4xx/5xx、
  `Retry-After`、业务拒绝和不合约 Payload 的 WMS-specific 映射。
- [ ] 将现有 WMS canonical string、签名字段顺序、Header 名和签名版本迁入 `adapters/auth.py`，形成不含
  credential resolver、Secret、HMAC 计算、Clock/Nonce 实例的纯认证合同。Composition Root 把该合同与
  WMS 类型化配置交给 Phase 2 Factory，由 Factory 解析凭据、调用 HMAC/Clock/Nonce 并返回已认证 Transport；
  不得创建任意签名模板、直接读取 Secret 或把认证策略实例注入业务 Gateway。
- [ ] 为目标 Gateway 建立 `WmsCallOutcome` 映射，不得 `except Exception` 后一律可重试；
  旧 `query_response.py` 在生产原子切换前保持唯一活动实现。
- [ ] 目标同步 evidence 只写 operation、request/trace、脱敏快照、hash、HTTP status、reason、retryable
  和时间，不读取 provider identity/digest。Phase 3 不删除旧链仍使用的字段；Task 10 把字段与旧 async writer
  登记为 Phase 4 应用模型删除项，Phase 9 在干净基线中移除数据库列。
- [ ] 每个公开调用先建立一条 STARTED evidence，再执行 HTTP 并原位完成；发送前写入失败则不发送，发送后
  finalization 失败则报告远端结果未知。禁止空/伪造 `evidence_key`。
- [ ] Phase 3 期间保留旧 async evidence 写入函数供唯一活动可靠链使用；Task 10 将它登记为 Phase 4 删除项。
- [ ] WMS Adapter 继续拥有一次公开调用的 breaker permit 和 DB 共享状态更新，保留
  OPEN/HALF_OPEN/CLOSED 与 probe fencing；移除对 Runtime observability 类型的直接依赖，仅接收注入的
  callable。Phase 4 可靠对象只消费 `WmsDependencyFailure` 并管理重试、依赖暂停和恢复，不重复申请 permit。
- [ ] 一个公开分页调用只申请一次 breaker permit；所有页面共享累计 absolute deadline、
  wire bytes、decoded bytes、页数和行数预算，只完成一条 evidence，并最终更新一次 breaker。
- [ ] `services/http_transport.py` 在本任务中保持旧 QUERY/status 路径的唯一活动 helper，不删除、不扩展；目标
  Gateway/response mapper 只消费 Phase 2 Transport，WMS 无 Secret 认证合同迁入 Adapter。Task 8 在全部调用者
  原子切换并通过 import closure 后删除该 helper；任何目标 WMS 生产文件不得直接 import `httpx`。

**Run:**

```bash
rtk uv run pytest \
  tests/contracts/wms_integration/test_wms_response_mapping.py \
  tests/contracts/wms_integration/test_wms_sync_evidence.py -q
rtk uv run pytest tests/resilience/test_wms_circuit_breaker.py -q
```

Expected: 纯映射/证据合同测试属于 FAST；旧生产路径未切换，breaker 的 PostgreSQL 状态共享测试继续由
HEAVY selector 承接，不得复制到 FAST。

**Commit boundary:** 暂存本任务 `Files` 与对应 HEAVY mapping 的精确路径。提交说明：
`refactor(wms): 统一调用结果、证据与断路器预算`。

### Task 7：实现未激活的 `HttpWmsGateway` 19 项查询

**Files:**

- Create: `src/app/wms_integration/adapters/http_gateway.py`
- Create: `src/app/wms_integration/factory.py`
- Create: `tests/contracts/wms_integration/test_http_wms_capabilities.py`
- Modify: `tests/architecture/test_wms_thin_public_boundary.py`

- [ ] 在 WMS 测试树定义实现公开 `OutboundHttpTransport` 合同的最小 local fake，或经公开 Factory seam 注入
  `httpx.MockTransport`，逐方法验证 19 项 method/path/query/body、分页、DTO 和四分支 outcome；不得 import
  `tests/core/outbound_http/` 的 Phase 2 测试内部资产，也不向 WMS Adapter 暴露裸 Client。
- [ ] `HttpWmsGateway` 每个公共方法直接 import 自身 capability 模块的 `WmsCallSpec` 并调用私有 `_call`；
  禁止字符串 operation 参数、中心映射和公共 spec 查询。
- [ ] 列表查询内部消费 cursor，但必须满足 Task 6 的一次 permit、累计预算、一条 evidence 和一次最终 breaker
  更新；空结果是成功。
- [ ] 在通用架构门禁中证明 Q19 caller 只依赖 `WmsDocumentCapabilities` 类型化端口。核心 WMS 合同测试拥有
  Q19 method/path、严格 request/result DTO、序列化和共享 outcome 映射；粗分机 admission 分支、Decision、对象推进、
  业务场景 fixture 和期望结果只由对应插件包测试拥有，不进入核心测试。本任务不修改生产 Q19 caller、
  API/Celery 装配或旧 QUERY 平台。
- [ ] Gateway factory 只接受从 `WmsConnectionSettings` 提取且不含认证字段的 `WmsCallSettings`、已完成认证装配的 Phase 2
  `OutboundHttpTransport`、evidence recorder 和 breaker；
  不读取全局 settings，也不在 import 时绑定单例。

**Run:**

```bash
rtk uv run pytest tests/contracts/wms_integration/test_http_wms_capabilities.py -q
rtk uv run pytest \
  tests/architecture/test_wms_query_transport_boundaries.py \
  tests/architecture/test_wms_thin_public_boundary.py -q
rtk uv run pytest \
  tests/contracts/wms_integration/test_wms_capability_conformance.py \
  tests/architecture/test_wms_thin_public_boundary.py -q
```

Expected: 19 项目标查询合同通过；旧 QUERY 平台仍是唯一活动实现，新 Gateway 尚未进入生产装配。

**Commit boundary:** 只暂存本任务 `Files` 和明确列出的测试路径。提交说明：
`feat(wms): 实现类型化 WMS 查询 Gateway`。

### Task 8：实现无状态 Client 并原子切换 QUERY/部署装配

**Files — create/modify:**

- Modify: `src/app/wms_integration/adapters/http_gateway.py`
- Modify: `src/app/wms_integration/adapters/effect_status_query_adapter.py`
- Modify: `src/app/wms_integration/factory.py`
- Modify: `src/app/wms_integration/effect_lane_runtime.py`
- Modify: `src/app/wms_integration/effect_preparation_runtime.py`
- Modify: `src/app/wms_integration/runtime_factory.py`
- Modify: `src/app/wms_integration/ports/effect_status.py`
- Modify: `src/app/wms_integration/query_evidence.py`
- Modify: `src/app/contracts/external_contract_profile.py`
- Modify: `src/app/contracts/external_contract_profile_catalog.py`
- Modify: `src/app/wms_integration/services/callback_normalizer.py`
- Modify: `src/app/callback/contracts/external_callbacks.py`
- Modify: `src/app/callback/services/callback_ingress_service.py`
- Modify: `src/app/callback/services/wms_inbound_auth.py`
- Modify: `src/app/runtime/orchestration/repositories/northbound_operations_repository.py`
- Modify: `src/app/runtime/orchestration/consumers/callback_runtime_inbox_writer.py`
- Modify: `src/app/runtime/orchestration/services/inbox/__init__.py`
- Modify: `src/app/runtime/orchestration/services/inbox/wms_runtime_inbox_handler.py`
- Modify: `src/app/runtime/orchestration/services/inbox/wms_typed_effect_callback_router.py`
- Modify: `src/app/runtime/orchestration/services/wms_effect_status_service.py`
- Create: `tests/contracts/wms_integration/test_http_wms_confirmation_sender.py`
- Create: `tests/contracts/wms_integration/test_http_wms_forwarded_transport.py`
- Modify: `tests/api/test_callback_wms_inbound_auth.py`
- Modify: `tests/architecture/test_confirm_inbound_legacy_cutover.py`
- Modify: `tests/architecture/test_inbound_normalizer_ownership_guardrail.py`
- Modify: `tests/architecture/test_inbound_normalizer_profile_validation.py`
- Modify: `tests/architecture/test_notify_pkg_binding_legacy_cutover.py`
- Modify: `tests/architecture/test_wms_query_transport_boundaries.py`
- Modify: `tests/contracts/wms_integration/test_typed_effect_callback_routing.py`
- Modify: `tests/contracts/wms_integration/test_wms_batch_ack_contract.py`
- Modify: `tests/contracts/workline/test_external_contract_profile_fixtures.py`
- Modify: `tests/deployment/test_docker_compose_mock_urls.py`
- Modify: `tests/deployment/test_jenkins_heavy_required.py`
- Modify: `tests/integration/test_celery_async_runtime.py`
- Modify: `tests/resilience/test_runtime_integration_lab.py`
- Modify: `tests/resilience/fixtures/runtime_integration_lab_fixture.json`
- Delete: `tests/fixtures/external_contracts/wms/default/duplicate.json`
- Delete: `tests/fixtures/external_contracts/wms/default/missing_event_id.json`
- Delete: `tests/fixtures/external_contracts/wms/default/reject.json`
- Delete: `tests/fixtures/external_contracts/wms/default/success.json`
- Delete: `tests/fixtures/external_contracts/wms/default/timeout.json`
- Delete: `tests/mock/wms_scripted_provider.py`
- Modify: `tests/runtime/orchestration/test_northbound_operation_observability.py`
- Modify: `tests/runtime/orchestration/test_northbound_operations_repository.py`
- Modify: `tests/unit/runtime/test_capability_port_registry.py`
- Modify: `tests/wms_integration/test_wms_effect_runtime.py`
- Modify: `tests/workline_runtime/test_wms_runtime_inbox_inbound.py`
- Modify: `tests/workline_runtime/test_station_external_http_frozen_binding.py`
- Modify: `tests/workline_runtime/system_capabilities/test_wms_effect_status_service.py`
- Modify: `tests/workline_runtime/system_capabilities/test_wms_effect_status_reliability.py`
- Modify: `tests/workline_runtime/system_capabilities/test_wms_fulfillment_domain_projection_hooks.py`
- Modify: `tests/sys/test_wms_async_effect_dispatch.py`
- Modify: `tests/integration/workline_capabilities/test_wms_effect_status_postgresql.py`
- Create: `tests/deployment/test_wms_config_compose_mount.py`
- Create: `tests/deployment/test_wms_process_composition.py`
- Create: `tests/deployment/test_wms_startup_lifecycle.py`
- Create: `tests/architecture/test_wms_phase3_import_closure.py`
- Create: `tests/architecture/test_wms_phase3_file_set.py`
- Create: `tests/integration/test_wms_process_composition_postgresql.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Modify: `tests/scripts/test_select_heavy_tests.py`
- Modify: `src/core/conf.py`
- Modify: `.env.dev`
- Modify: `.env.prod`
- Modify: `.env.test`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.deploy.yml`
- Modify: `docker-compose.test-deploy.yml`
- Modify: `Jenkinsfile.backend-ci`
- Modify: `Jenkinsfile.test-deploy`
- Modify: `src/register.py`
- Modify: `src/celery_app/app.py`
- Modify: `src/celery_app/async_runtime.py`
- Modify: `src/celery_app/outbox_dispatch_composition.py`
- Modify: `src/app/workline/runtime_services.py`
- Modify: `src/app/runtime/capabilities/material_flow/rough_sorter_q19_admission_service.py`
- Modify: `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_orchestrator_bridge.py`
- Modify: `src/app/runtime/orchestration/integration_lab.py`
- Modify: `src/app/workline/services/plugin_binding_service.py`
- Modify: `src/app/workline/services/migration_inventory_service.py`
- Modify: `src/app/workline/services/migration_matrix_service.py`
- Modify: `src/app/workline/services/workline_service.py`
- Modify: `src/app/runtime/workline_plugins/dispatcher.py`
- Modify: `src/app/runtime/workline_plugins/rough_sorter/config.py`
- Modify: `src/app/runtime/workline_plugins/rough_sorter/handlers.py`
- Modify: `src/app/runtime/workline_plugins/rough_sorter/pre_attempt.py`
- Modify: `src/app/runtime/workline_plugins/smt_sorting_inbound/contracts.py`
- Modify: `src/app/runtime/system_capabilities/wms/contracts.py`
- Modify: `src/app/runtime/system_capabilities/wms/effect_runtime.py`
- Modify: `src/app/runtime/system_capabilities/wms/scheduling_identity.py`
- Modify: `src/app/runtime/system_capabilities/generated_index.py`
- Modify: `scripts/generate_runtime_extensions.py`
- Modify: `scripts/architecture-guardrails.sh`
- Modify: `scripts/docker-deploy-simple.sh`
- Modify: `.dockerignore`

**Files — delete after import closure passes:**

- `src/app/wms_integration/query_executor.py`
- `src/app/wms_integration/query_runtime.py`
- `src/app/wms_integration/query_projection.py`
- `src/app/wms_integration/query_response.py`
- `src/app/wms_integration/services/http_transport.py`
- `src/app/wms_integration/deployment_attestation.py`
- `src/app/wms_integration/endpoint_compiler.py`
- `src/app/wms_integration/provider_manifest.py`
- `src/app/wms_integration/provider_profile.py`
- `src/app/wms_integration/provider_readiness.py`
- `src/app/wms_integration/provider_simulator_registry.py`
- `src/app/wms_integration/provider_startup.py`
- `src/app/runtime/system_capabilities/wms/provider_catalog.py`
- `src/app/runtime/system_capabilities/wms/provider_conformance.py`
- `src/app/runtime/system_capabilities/wms/conformance_manifest.py`
- `src/app/runtime/system_capabilities/wms/conformance_matrix.py`
- `src/app/runtime/system_capabilities/wms/generated_operation_index.py`
- `src/app/wms_integration/ports/query_execution.py`
- `src/app/contracts/wms_inbound.py`（`WMS_BUSINESS_EVENT_TYPES` 迁入 `inbound/contracts.py` 后删除）
- Task 3 已完成 MOVE 的五个 `*_operations.py` 与 `operation_common.py`
- §2.1 标记为 Phase 3 `DELETE` 的 evidence/models/inbound 旧源文件
- `src/app/runtime/system_capabilities/wms/` 下 19 个 QUERY definition 目录、`query_definition.py` 和
  `query_handler.py`

**共享 External Contract Profile 的 WMS 切片处置：**

| 当前生产文件 | Phase 3 处置 | 保留边界或删除条件 |
| --- | --- | --- |
| `src/app/contracts/external_contract_profile.py` | SPLIT | 删除 `WmsExternalContractProfile`、WMS closed-union 特判及 WMS timeout/security/fixture 配置；仅保留非 WMS 的 ECS/Device/AGV 入站合同临时所有权 |
| `src/app/contracts/external_contract_profile_catalog.py` | SPLIT | 删除 `WMS_MATERIAL_FLOW_PROFILE`、WMS 全局目录项及 WMS identity 解析；非 WMS 目录不得参与 WMS Transport、认证、能力准入或 callback |
| `callback_ingress_service.py`、`integration_lab.py`、`runtime_inbox_orchestrator_bridge.py` | REWRITE | WMS 分支改接 `inbound/contracts.py`、独立 inbound policy 和类型化 WMS 能力；非 WMS 分支可暂用 generic profile |
| `plugin_binding_service.py`、`workline_service.py` | REWRITE | WMS 插件绑定不再要求、解析、快照或回显 `provider_profile`；准入只依赖冻结插件身份与显式类型化能力，generic profile 仅服务非 WMS 入站合同 |
| `migration_inventory_service.py`、`migration_matrix_service.py` | REWRITE | 从旧迁移盘点/矩阵删除 WMS Profile Catalog 项和一致性判定；保留的 generic provider 项是 Phase 6/7/8 的临时旧所有者，不得被 WMS 路径消费 |
| `runtime/workline_plugins/dispatcher.py`、`rough_sorter/config.py`、`rough_sorter/handlers.py`、`smt_sorting_inbound/contracts.py` | REWRITE | 机械删除 WMS `provider_profile` 配置、profile snapshot/identity 比对与 facts 传播；不优化业务 Decision，Phase 6/7 仍负责插件最终迁出 |

该 SPLIT 必须在同一原子切换中完成。Phase 3 结束时，`WmsExternalContractProfile`、
`WMS_MATERIAL_FLOW_PROFILE` 及其全部生产调用者、测试引用均为零；不得把它们改名为 contract profile、保留空的
WMS catalog 项或用字符串 identity 继续传递认证/Transport 配置。generic `ExternalContractProfile` 只是一项明确的
非 WMS 临时旧所有者，Phase 6/7 随真实 ECS/RCS Adapter 交付缩减，Phase 8 最终闭环，不得反向进入 WMS Adapter。

- [ ] 参数化验证 E01–E07/E15 的 request、`dispatch_key` 幂等头、同步 terminal DTO 和四分支 outcome。
- [ ] ACK、status、terminal、cancel 和 hint 必须回显同一 `dispatch_key`；静态合同与实现均不得出现独立
  `idempotency_key` 字段、alias 或双键转换。
- [ ] 参数化验证 E08–E14 submit ACK、七类 status pending/terminal DTO 以及 E16 cancel；Client 不轮询、
  不 sleep、不 schedule、不写任务状态。旧 Effect/status 生命周期在本任务原地改接该 Client，但仍由原 owner
  管理持久化、claim、重试、fencing 和终态，不创建新可靠状态。
- [ ] 在同一工作树变更中激活目标 Gateway 唯一读取的 `WMS_CONFIG_FILE`，只修改三个 tracked env profile，再运行
  `./scripts/init-env.sh` 刷新 worktree-local `.env`；不得把 `.env` 加入暂存。
- [ ] Compose/Jenkins 的目标 Gateway 装配只传递新变量；Composition Root 在 API/Celery 各通过 Phase 2 Factory
  装配一个 WMS 专属 Transport，再构造一个 Gateway；配置缺失或错误 fail fast，shutdown 与启动失败均关闭
  Transport/其所拥有 Client。WMS Adapter 不接收裸 Client。删除旧 QUERY 配置读取，不为目标 loader 保留
  alias/fallback；旧 Effect/status composition 同时改为注入同一个 WMS 专属 Transport 和类型化 sender/client，
  删除 Provider Profile、Provider readiness/startup、endpoint compiler、凭据解析和旧 Client owner。
- [ ] 同一原子切换将 `WmsInboundAuthPolicy` 从 compiled Provider Profile 改接独立 inbound callback policy；
  只保留“合同确认的可信隔离内网允许 unsigned WMS 业务事件/status hint，否则沿用既有 API
  Application/HMAC fail closed”的当前行为。该 ingress policy 不调用 Phase 2 Factory、不读取 outbound
  credential reference，也不因 outbound `NONE` 自动放宽 callback。
- [ ] 将 callback type/normalizer/router 对 Provider Catalog 的静态依赖改接 `inbound/contracts.py` 的封闭
  callback DTO；`callback_ingress_service.py` 的函数内惰性 import 同步改接 `inbound/normalizer.py`，不得在普通 import
  扫描之外遗留对 `services/wms_event_normalizer.py` 的运行时依赖；不得以新的 callback registry、Provider 常量别名或
  re-export 保留旧 Catalog 身份。
- [ ] 将 `src/app/contracts/wms_inbound.py` 的普通业务事件闭集迁入同一个 `inbound/contracts.py` 后删除旧文件；
  `wms_inbound_auth.py`、`external_callbacks.py`、`callback_runtime_inbox_writer.py`、`ports/event.py` 和
  `wms_runtime_inbox_handler.py` 全部改向新 owner。`WMS_BUSINESS_EVENT_TYPES` 只能有一处定义，禁止 re-export、复制
  常量集合或保留第二份 callback/RuntimeInbox 合同真源。
- [ ] `effect_lane_runtime.py`、`runtime_factory.py`、`effect_status_query_adapter.py` 和 `ports/effect_status.py`
  只做依赖替换：移除 process-role/readiness/profile/binding auth、credential provider、裸 Client 和通用 HTTP；
  保留原持久化身份、claim、backoff、fencing、迟到结果和终态语义。旧 SystemOutbox WMS dispatch 只能调用
  显式 sender/client 方法，不得继续进入 `src.app.sys` 的通用 EXTERNAL_HTTP 发送路径；非 WMS Outbox 消费者不在本阶段改写。
- [ ] `ports/effect_status.py`、`runtime_factory.py` 与 `query_evidence.py` 在 Phase 3 handoff 中同步删除
  `provider_profile_identity`/`provider_profile_hash`；冻结字段只允许存在于 `models/evidence.py`、
  `repositories/evidence_repository.py`、`services/evidence_service.py` 三个旧证据 owner，Task 10 与旧 async evidence
  应用层一起删除。不得把字段豁免扩散到端口、runtime 或 DTO。
- [ ] `wms_effect_status_service.py` 及其 FAST/HEAVY fixtures 必须移除 compiled profile、provider binding 和
  endpoint compiler，改为消费类型化 sender/client 与冻结 operation identity；只替换配置、认证和 HTTP 出口，
  不改写 status persistence、claim、backoff、fencing、迟到结果、终态和恢复语义。
- [ ] 将 Q19 caller 机械改为注入 `WmsDocumentCapabilities`，并从外部 request DTO 删除内部 `session_id`；当前
  Session admission fact 查找继续通过 `resolve(..., session_id=...)` 显式内部参数完成，不建立 alias、fallback 或
  双字段映射。除此之外只调整依赖和调用，不优化粗分机业务结构，Phase 6 才直接替换内部 Session 所有权。
- [ ] `NorthboundOperationsRepository` 删除 Provider Catalog 注入和“空账本时按 Catalog 合成 QUERY 行”的
  旧平台行为，只保留实际 evidence/旧 Outbox 的观测事实；对应回归测试必须改写或删除，不能用冻结 Catalog
  常量继续制造不存在的运行时能力。
- [ ] 切换全部 19 项 QUERY caller，包括
  `runtime_inbox_orchestrator_bridge.py` 的静态 `ports.query_execution` importer；删除 query runtime bind/close、
  Runtime Service Locator 和 QUERY System Capability definitions；重新生成/更新全局 index，使其只保留旧
  fulfillment definitions。
- [ ] `scripts/generate_runtime_extensions.py` 只做旧生成器的阶段性收缩，不新增 WMS codegen；Phase 4 删除剩余
  fulfillment definitions 后一并删除 WMS 生成入口。
- [ ] fulfillment definitions 只保留 Phase 4 接管所需的冻结 operation identity/DTO；删除 Provider Catalog、
  `provider_conformance.py`/conformance matrix、readiness、startup、deployment attestation 和旧认证/传输依赖。旧 Effect/status/Outbox 的可靠
  状态机和调度语义不得改写；不创建 `WmsConfirmation`、`TransportTask`、第二张可靠表或第二个 Celery scanner。
- [ ] `system_capabilities/wms/contracts.py`、`effect_runtime.py`、`scheduling_identity.py` 和
  `runtime/orchestration/integration_lab.py` 机械删除 provider identity/version/simulator 依赖；保留的旧 fulfillment
  调用只使用冻结 operation identity、DTO 和 `dispatch_key`。删除本任务明确列出的 Provider Catalog/conformance/
  generated operation index，禁止用新常量别名或 re-export 伪装旧 Provider 身份。
- [ ] 运行静态 import-closure 门禁：Phase 3 删除集不得被任何保留文件 import，generated index 不得 import
  已删除 QUERY definition；扫描范围必须覆盖全部保留生产代码与测试，而不只覆盖 `src/app/wms_integration/`。
- [ ] `scripts/architecture-guardrails.sh` 的 inbound normalizer 合法 holder 与违规 fixture 同步改向目标
  `inbound/contracts.py`/`inbound/normalizer.py`；不得让架构门禁本身成为已删除 `ports/event.py` 的路径真源。
- [ ] 文件集门禁读取 §2.1 的冻结清单：Phase 3 DELETE 源文件必须缺席、PHASE4_HANDOFF 必须仍在且零
  Provider/Profile/credential/httpx 依赖、目标垂直模块必须精确 35 项，`evidence/` 目录和旧 public re-export
  必须缺席。
- [ ] 更新 HEAVY selector 真源和合同测试：`.env.*`、Compose/Jenkins、`src/core/conf.py`、`src/register.py`、
  Phase 2 transport factory、`configuration.py`、`factory.py` 与 WMS 进程装配精确映射到
  `test_wms_process_composition_postgresql.py`；保留 `src/celery_app/async_runtime.py` 对三项既有 Celery
  runtime HEAVY 的映射，不得用空 `heavy_tests` 或宽泛目录匹配掩盖影响。
- [ ] 删除部署鉴证脚本时同步从 `scripts/docker-deploy-simple.sh` 移除鉴证函数与生产启动调用；生产启动只保留目标
  WMS 配置、进程装配和 startup lifecycle 门禁，不以空函数、条件跳过或旧脚本路径保留兼容入口。
- [ ] Task 8 与 Task 9 是一次原子切换：新测试和部署门禁先通过，旧测试后处置，中间态不得提交。

**Run:**

```bash
rtk uv run pytest \
  tests/contracts/wms_integration/test_http_wms_capabilities.py \
  tests/contracts/wms_integration/test_http_wms_confirmation_sender.py \
  tests/contracts/wms_integration/test_http_wms_forwarded_transport.py -q
rtk uv run pytest \
  tests/contracts/wms_integration/test_wms_connection_settings.py \
  tests/contracts/wms_integration/test_wms_capability_conformance.py \
  tests/architecture/test_wms_thin_public_boundary.py -q
rtk uv run pytest \
  tests/deployment/test_wms_config_compose_mount.py \
  tests/deployment/test_docker_compose_mock_urls.py \
  tests/deployment/test_jenkins_heavy_required.py \
  tests/deployment/test_wms_process_composition.py \
  tests/deployment/test_wms_startup_lifecycle.py -q
rtk uv run pytest \
  tests/architecture/test_wms_phase3_import_closure.py \
  tests/architecture/test_wms_phase3_file_set.py -q
rtk uv run pytest \
  tests/api/test_callback_wms_inbound_auth.py \
  tests/workline_runtime/system_capabilities/test_wms_effect_status_service.py \
  tests/workline_runtime/system_capabilities/test_wms_effect_status_reliability.py \
  tests/workline_runtime/system_capabilities/test_wms_fulfillment_domain_projection_hooks.py \
  tests/sys/test_wms_async_effect_dispatch.py -q
rtk uv run pytest tests/scripts/test_select_heavy_tests.py -q
rtk uv run scripts/select_heavy_tests.py --scope unstaged
```

Expected: 19 项 QUERY 和唯一配置装配已切换；sender/client 已实现并成为旧可靠链唯一 HTTP 出口；旧链的
可靠生命周期语义未改写，Provider/Profile/旧认证/旧 Transport 已删除，generated index 只剩 Phase 4 handoff
所需 fulfillment definitions，inbound callback 保持独立且 fail closed，Phase 3 删除集无 dangling import。

**Do not commit yet:** 继续 Task 9。禁止形成生产旧模块已删除、旧测试仍断裂的中间提交。

### Task 9：按逐文件矩阵处置旧测试并收敛重量

新 owner 测试必须先通过，再执行下表的 REWRITE/DELETE；不得按 `provider`、`replay`、`conformance` 等关键词
批量删除。

| 当前测试/测试资产 | 处置 | successor 或 NONE 理由 |
| --- | --- | --- |
| `provider_profile_support.py` | DELETE | → `test_wms_connection_settings.py`；目标配置 fixture 不复用 Provider helper |
| `test_callback_wms_inbound_auth.py` | REWRITE | → 独立 inbound callback policy + 既有 API Application/HMAC ingress；不得复用 outbound auth 或 Provider helper |
| `test_typed_effect_callback_routing.py` | REWRITE | PHASE4_HANDOFF；callback DTO 改接 `inbound/contracts.py`，status hint 仍只触发旧唯一 status owner |
| `test_wms_batch_ack_contract.py` | REWRITE | → E12/E13 垂直 DTO/spec + typed forwarded transport tests；删除 Provider binding/旧 registry 断言 |
| `test_northbound_operations_repository.py` | REWRITE | 只验证实际 evidence/旧 Outbox 观测事实；删除 Provider Catalog 注入和合成 QUERY 行 |
| `test_qa_northbound_catalog_regression.py` | DELETE | → NONE；目标明确删除空账本按 Catalog 合成 QUERY 行的旧平台行为 |
| `test_northbound_operation_observability.py` | REWRITE | 由测试态 capability harness 提供 35 项预期身份；不得 import 已删除的 generated operation index |
| `test_capability_port_registry.py` | REWRITE | PHASE4_HANDOFF；以本地 async Protocol 保留 registry 通用断言，不再 import 已删除的 WMS generic QUERY port |
| `test_station_external_http_frozen_binding.py` | REWRITE | PHASE4_HANDOFF；改用 typed sender/client fixture 验证原 dispatch key 的重启恢复，不再 monkeypatch Provider Catalog |
| `test_wms_effect_status_service.py` | REWRITE | PHASE4_HANDOFF；typed client/provider-free fixture，完整保留 persistence/claim/backoff/fencing/terminal 断言 |
| `test_wms_effect_status_reliability.py` | REWRITE | PHASE4_HANDOFF；改接 provider-free status fixture，保留 crash/retry/late-result 断言 |
| `test_wms_fulfillment_domain_projection_hooks.py` | REWRITE | PHASE4_HANDOFF；垂直 DTO/typed client fixture，不再 import Provider helper |
| `test_wms_async_effect_dispatch.py` | REWRITE | PHASE4_HANDOFF；typed sender/client fixture，不再间接依赖 compiled profile |
| `test_wms_effect_status_postgresql.py` | REWRITE | PHASE4_HANDOFF HEAVY；provider-free binding 下继续验证 claim/fencing/terminal |
| `test_provider_conformance_matrix.py` | DELETE | → `test_wms_capability_conformance.py` |
| `test_provider_conformance_replay_asset.py` | DELETE | → NONE；目标无 replay/conformance 平台 |
| `test_provider_conformance_report.py` | DELETE | → NONE；目标无 conformance report 产物 |
| `test_provider_conformance_runner_cli.py` | DELETE | → NONE；对应 CLI 删除 |
| `test_provider_conformance_suite.py` | DELETE | → `test_wms_capability_conformance.py` + Gateway MockTransport tests |
| `test_wms_effect_capability_index.py` | REWRITE | → `test_wms_phase4_handoff_guardrail.py`；Phase 3 只验证 index 保留 fulfillment 条目 |
| `test_wms_frozen_http_binding_projection.py` | DELETE | → capability module spec + Gateway binding tests |
| `test_wms_compiled_profile_active_truth.py` | DELETE | → connection settings + process composition tests |
| `test_wms_provider_digest_readiness.py` | DELETE | → startup lifecycle tests；目标无 provider digest |
| `test_wms_provider_endpoint_compiler.py` | DELETE | → Gateway/status typed client composition；Phase 3 不保留 endpoint compiler |
| `test_wms_provider_profile.py` | DELETE | → `test_wms_connection_settings.py` + process composition；Phase 3 不保留 Provider Profile |
| `test_wms_query_projection.py` | DELETE | → `test_http_wms_capabilities.py` |
| `test_query_executor.py` | DELETE | → `test_http_wms_capabilities.py` + `test_wms_response_mapping.py`；目标无统一 registry executor |
| `test_query_response_branches.py` | DELETE | → Phase 2 bounded-response tests + `test_wms_response_mapping.py`；目标无旧 QUERY response helper |
| `test_query_runtime_evidence.py` | DELETE | → `test_wms_sync_evidence.py` + process composition tests；目标无 QUERY runtime singleton |
| `test_wms_query_transport_boundaries.py` | REWRITE | → Phase 2 Transport + `HttpWmsGateway` 边界；删除 `query_executor.py` import 和旧 executor 内部结构断言 |
| `wms_query_runtime.py` | DELETE | → Gateway Fake/MockTransport fixtures；目标测试不再绑定泛型 QUERY runtime |
| `test_wms_transport_runtime_configuration.py` | REWRITE | PHASE4_HANDOFF；验证旧生命周期只注入 Phase 2 Transport/typed client，Phase 4 由 TransportTask 测试承接 |
| `test_wms_provider_conformance_boundaries.py` | DELETE | → `test_wms_thin_public_boundary.py` |
| `test_wms_provider_replay_boundaries.py` | DELETE | → NONE；目标无 replay 平台 |
| `test_wms_shared_effect_pipeline_guardrail.py` | REWRITE | → `test_wms_phase4_handoff_guardrail.py`；保护旧可靠链至原子交接 |
| `test_wms_deployment_attestation_gate.py` | DELETE | → NONE；目标无部署鉴证 gate |
| `test_wms_deployment_attestation_runner.py` | DELETE | → NONE；目标无部署鉴证 runner |
| `test_wms_effect_lane_dispatch.py` | REWRITE | PHASE4_HANDOFF；验证旧 lane 只调用显式 sender/client 且无裸 HTTP，Phase 4 由最终可靠对象测试承接 |
| `test_wms_effect_runtime.py` | REWRITE | PHASE4_HANDOFF；垂直 DTO + typed sender/client，删除 Provider Catalog/通用 EXTERNAL_HTTP 断言 |
| `test_wms_provider_profile_compose_mount.py` | DELETE | → `test_wms_config_compose_mount.py`；Phase 3 删除旧 Provider mount |
| `test_wms_provider_profile_startup.py` | DELETE | → `test_wms_startup_lifecycle.py`；Phase 3 删除旧 Provider startup |
| `test_wms_transport_startup.py` | REWRITE | PHASE4_HANDOFF；验证唯一 Phase 2 Transport 注入，Phase 4 由 TransportTask composition tests 承接 |
| `test_wms_deployment_attestation.py` | DELETE | → NONE；旧部署鉴证平台删除 |
| `test_wms_northbound_feasibility_probe.py` | DELETE | → NONE；不以真实 HTTP probe 代替目标合同测试 |
| `test_wms_provider_conformance_collection.py` | DELETE | → NONE；目标无运行时 conformance collection |
| `test_wms_provider_conformance_simulator.py` | DELETE | → `test_wms_capability_conformance.py` + MockTransport tests |
| `wms_scripted_provider.py` | DELETE | → `test_wms_capability_conformance.py` + Gateway MockTransport fixtures；其 importer 与 `wms_provider_conformance.py` 同步删除 |
| `test_typed_evidence_envelope.py` | DELETE | → NONE；目标删除旧 ExternalReference catalog/drift evidence 平台，WMS 同步 evidence 由 `test_wms_sync_evidence.py` 验证 |
| `test_wms_event_normalizer_registry.py` | REWRITE | → `inbound/contracts.py` + `inbound/normalizer.py`；保留封闭 callback DTO/normalization 断言，删除旧 `ports/event.py` 与 registry owner |
| `test_wms_runtime_inbox_inbound.py` | REWRITE | PHASE4_HANDOFF；普通 WMS event 与 status hint 都改用 `inbound/contracts.py` 唯一闭集，删除 `src.app.contracts.wms_inbound` import；Phase 4 由 `InboundEvidence`/`TransportTask` successor 承接后删除旧 RuntimeInbox handler 测试 |
| `test_inbound_normalizer_ownership_guardrail.py` | REWRITE | 将合法 holder、扫描夹具和禁止 import 改向 `inbound/contracts.py`/`inbound/normalizer.py`，不得引用已删除的 `ports/event.py` |
| `test_external_contract_profile_fixtures.py` | REWRITE | 删除 `WmsExternalContractProfile`、`WMS_MATERIAL_FLOW_PROFILE`、WMS catalog/simulator/fixture 断言；只保留仍由 ECS/Device/AGV generic 入站合同拥有的测试，不把 WMS Profile 改名保留 |
| `test_runtime_integration_lab.py` | REWRITE | HEAVY；WMS 场景只消费 Phase 3 typed WMS fake/inbound contract，不再断言或构造 WMS Provider Profile；ECS generic profile 场景可保留到对应 Adapter 阶段 |
| `runtime_integration_lab_fixture.json` | REWRITE | 删除 `provider_profiles` 中完整 WMS profile 与旧 fixture-set 引用；WMS 场景改用冻结 operation/typed outcome，禁止引入替代 Profile 字段 |
| `tests/fixtures/external_contracts/wms/default/{duplicate,missing_event_id,reject,success,timeout}.json` | DELETE | success/reject/timeout → 垂直 capability + Fake/MockTransport；duplicate/missing_event_id → target inbound normalizer/auth tests；旧 `FixtureCase`、`idempotency_key` 和 profile-set 资产无运行时 successor |
| `test_inbound_normalizer_profile_validation.py` | REWRITE | WMS 用例改向 `inbound/contracts.py` 与独立 callback policy；generic ECS/Device profile 校验留在共享合同测试，零 `WmsExternalContractProfile` |
| `test_confirm_inbound_legacy_cutover.py` | REWRITE | WMS confirm 边界改验 E01 垂直 capability/typed sender，删除对 `external_contract_profile_catalog.py` 的源码字符串断言 |
| `test_notify_pkg_binding_legacy_cutover.py` | REWRITE | WMS package binding 边界改验 E07 垂直 capability/typed sender，删除对 `external_contract_profile_catalog.py` 的源码字符串断言 |
| `test_celery_async_runtime.py` | REWRITE | HEAVY；改接目标 WMS Gateway/Phase 2 Transport 进程生命周期，删除 readiness、query runtime 与 Provider Catalog import；继续由 Task 11 显式运行 |
| `test_jenkins_heavy_required.py` | REWRITE | 从 CI 必需资产和 `.dockerignore` 合同移除已删除的 `docker-compose.wms-acceptance.yml`，保留其余 HEAVY/Compose 门禁 |
| `test_docker_compose_mock_urls.py` | REWRITE | 删除 acceptance compose、Provider Profile 和旧 credential 断言，改验 `WMS_CONFIG_FILE` 与目标 API/Celery WMS Transport 装配 |
| `test_wms_operation_catalog.py` | REWRITE | wire 断言迁入 capability harness；旧 fulfillment 断言留给 Phase 4 handoff |
| `test_effect_status_contract.py` | REWRITE | PHASE4_HANDOFF；最终由 TransportTask/WmsConfirmation tests 承接后删除 |
| `wms_operation_fixtures.py` | REWRITE | 只机械改为垂直 DTO import；Phase 5 决定 CORE_REWRITE/PLUGIN_OWNED |
| `runtime_inbox_processing_postgresql.py` | REWRITE | PHASE4_HANDOFF；Phase 3 先删除 Provider Profile 常量依赖并改用冻结的垂直 operation identity，Phase 5 在 `InboundEvidence` 上建立 successor 后删除 |
| `test_runtime_inbox_processing_postgresql.py` | REWRITE | Phase 5 在 `InboundEvidence` HEAVY test 上建立 successor 后删除 |

**Delete support assets after all referencing tests are handled:**

- `tests/fixtures/wms_provider_conformance/query_inventory_replay.v1.json`
- `tests/support/wms_conformance_coverage.py`
- `tests/support/wms_conformance_runner.py`
- `tests/support/wms_provider_conformance.py`
- `tests/support/wms_provider_replay.py`
- `tests/support/wms_query_runtime.py`
- `scripts/check_wms_deployment_attestation.py`
- `scripts/run_wms_conformance.py`
- `scripts/run_wms_deployment_attestation.sh`
- `scripts/verify_wms_northbound_feasibility.py`
- `docker-compose.wms-acceptance.yml`

- [ ] FAST 只保留垂直 DTO/spec、显式端口、共享 mapper、MockTransport、装配和架构边界；不运行 Docker、
  子进程、真实 HTTP、真实 WMS 或全量 conformance matrix。
- [ ] HEAVY 只保留 PostgreSQL breaker 竞争、旧 status claim/fencing 和必要故障注入；每个移动/删除路径同步
  更新 `docs/architecture/heavy-test-impact.toml` 与 selector contract，不能留下失效 mapping。
- [ ] `integration_lab.py`、`test_runtime_integration_lab.py` 及其 JSON fixture 必须形成精确 HEAVY mapping；Phase 3
  实际运行该 HEAVY 测试，证明 WMS Profile 输入已删除且 typed WMS fake 仍可驱动既有故障场景。五个旧 WMS
  external-contract fixture 删除后，selector、测试和运行时引用全部归零。
- [ ] 对所有生产 DELETE 模块及测试/support DELETE 资产执行反向引用扫描；删除 `provider_profile_support.py` 前，全部直接 importer 和通过
  `test_wms_effect_status_service.py` fixture 间接依赖的测试必须逐项落入上表并完成 REWRITE/DELETE，保留测试对
  Provider helper/compiled profile 的 import 必须为零；其余 Phase 3 删除模块的测试 importer 同样必须在上表
  逐项处置并归零。若实施基线出现表外 importer，必须先修订本矩阵，禁止临场批量删除或保留兼容 helper。
  successor 路径必须先通过，NONE 理由必须进入提交说明或 PR 描述。
- [ ] 反向扫描不得只匹配文件顶层静态 import：必须同时覆盖函数内惰性 import、`TYPE_CHECKING` import、Shell/Python
  脚本调用、Compose/Jenkins/`.dockerignore` 路径，以及测试通过 `Path.read_text()`/`is_file()` 读取删除资产的情况。
  `run_wms_deployment_attestation.sh`、`tests.support.wms_provider_conformance` 和
  `docker-compose.wms-acceptance.yml` 在全部保留生产、测试、脚本和部署文件中的引用必须为零。
- [ ] 对共享 Profile SPLIT 另做符号级闭包：全仓保留文件中
  `WmsExternalContractProfile`/`WMS_MATERIAL_FLOW_PROFILE` 为零；`external_contract_profile_catalog.py` 不含 WMS
  实例或 WMS identity 分支；WMS callback、插件绑定、迁移盘点、IntegrationLab 和 Runtime bridge 均不得解析或传播
  generic profile。generic ECS/Device/AGV Profile 的保留不得被当成 WMS 删除门禁豁免。

**Run:**

```bash
rtk uv run pytest tests/contracts/wms_integration tests/wms_integration tests/architecture tests/deployment -q
rtk uv run pytest tests/scripts -q
rtk uv run pytest --collect-only -q -o addopts=''
rtk ./scripts/git-quality-gate.sh --profile quality
rtk uv run scripts/select_heavy_tests.py --scope unstaged
```

Expected: 新 WMS FAST 子集只验证目标合同；全仓收集无 dangling import；FAST 仍低于 60 秒总预算；
PHASE4_HANDOFF 测试证明旧链只发生传输/配置依赖替换，可靠生命周期语义未被改写。

**Commit boundary:** Task 8/9 同一提交，只暂存两项任务 `Files` 和矩阵明确列出的精确路径，禁止
`git add -A` 或目录级暂存。精确暂存后必须运行 `rtk uv run scripts/select_heavy_tests.py --scope staged`，并实际
执行 selector 输出的全部 HEAVY 测试；两者通过后才能提交。提交说明：
`refactor(wms): 原子切换 WMS 查询薄边界`。

### Task 10：冻结 Phase 4 原子删除清单

**Files:**

- Create: `tests/architecture/test_wms_phase4_handoff_guardrail.py`
- Modify: `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- Modify: `docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md`

Phase 4 必须在最终对象权威测试通过后删除以下旧可靠性所有者：

- `src/app/wms_integration/effect_runtime.py`
- `src/app/wms_integration/effect_lane_runtime.py`
- `src/app/wms_integration/effect_preparation_runtime.py`
- `src/app/wms_integration/runtime_factory.py`
- `src/app/wms_integration/adapters/effect_status_query_adapter.py`
- `src/app/wms_integration/ports/effect_preparation.py`
- `src/app/wms_integration/ports/effect_status.py`
- `src/app/wms_integration/state_machine.py`
- `src/app/wms_integration/services/fulfillment_lifecycle.py`
- `src/app/runtime/orchestration/repositories/wms_effect_status_repository.py`
- `src/app/runtime/orchestration/services/wms_effect_status_service.py`
- `src/app/runtime/orchestration/services/inbox/wms_typed_effect_callback_router.py` 的旧 status-service resolver/调用；
  Phase 3 `inbound/contracts.py` 产出的 typed status hint 在 Phase 4 改接 `TransportTask` 唯一的类型化
  status-hint application port，随后删除旧 router owner。hint 只缩短下一次领取时间，
  不直接写终态、不创建第二个 scanner，也不把 WMS DTO 带入核心对象
- `src/app/runtime/orchestration/services/inbox/wms_runtime_inbox_handler.py` 整个旧 WMS RuntimeInbox handler；
  `runtime_inbox_orchestrator_bridge.py` 的 `_resolve_wms_inbound_handler`/`_process_wms_inbound` 调用链改接最终
  `InboundEvidence` 消费入口，status hint 再进入上述 `TransportTask` port，普通 WMS event 进入对应本地投影处理
- `src/app/runtime/orchestration/services/inbox/__init__.py` 中两个 WMS handler/router re-export，以及
  `callback_runtime_inbox_writer.py` 的 `write_wms_event_callback`/WMS event 闭集分支；非 WMS RuntimeInbox writer
  暂留给其对应阶段，不能 import WMS inbound 合同
- `src/app/callback/services/callback_orchestration_service.py` 的 `process_wms_event` 以及 `process_external` 的 WMS
  status-hint 分支，与 `callback_runtime_inbox_writer.py` 的 `_resolve_external_source_event_id` WMS hint 特判、
  `write_external_callback` WMS 分支和 `write_wms_event_callback`；两类 WMS ingress 在同一事务改写为先持久化
  `InboundEvidence`，普通事件唤醒最终 evidence consumer，status hint 调用 `TransportTask` typed hint port。非 WMS
  `process_external`/RuntimeInbox 行为保持原 owner，禁止把 WMS payload 同时写入两种 inbox
- `src/app/callback/services/callback_ingress_service.py` 的 WMS ordinary-event 与 WMS status-hint 异常映射分支；
  原子切换后只消费最终 application port 的封闭 typed outcome：`DUPLICATE` 仍按成功 ACK，`CONFLICT` 映射 409，
  `PAYLOAD_TOO_LARGE` 映射 413，`CORRELATION_UNAVAILABLE` 映射 503，输入或 hint 被拒绝映射 400。WMS 分支不得再
  捕获 `RuntimeInboxConflict`、`RuntimeInboxPayloadTooLarge`、`RuntimeInboxCorrelationUnavailable`，不得用异常 alias、
  包装转换或宽泛 `Exception` 保持旧合同；非 WMS callback 的 RuntimeInbox 异常映射留给其对应阶段
- `src/app/runtime/orchestration/services/intent/operation_service.py` 的
  `submit_sandbox_external_callback` WMS 分支及其 `_accept_runtime_message` 调用，与
  `src/app/workline/v1/operation.py` 对该 WMS 分支的无条件 RuntimeInbox enqueue。Phase 4 的 WMS 测试只使用 Phase 3
  typed fake + 最终 `InboundEvidence`/`TransportTask` fixture；删除公共 sandbox route 的 WMS source/callback 支持，
  不为已删除的 WMS SystemOutbox 建兼容查找或第二条模拟入口。非 WMS sandbox 行为由对应阶段单独处置
- `src/app/runtime/orchestration/models/operation.py` 的 `SandboxExternalCallbackRequest` 同步删除
  `source_system="WMS"` 默认值与 WMS 允许分支；请求模型/OpenAPI 在进入 service 前即拒绝 WMS，不能继续宣称支持
  已删除路径，也不能把校验推迟到运行时 service fallback
- `src/celery_app/tasks/workline.py` 中 `check_wms_effect_status` 与 `scan_wms_effect_status_batch`
- `src/celery_app/tasks/sys.py` 中 `dispatch_wms_data_outbox_batch` 与
  `dispatch_wms_fulfillment_outbox_batch`
- `src/celery_app/config.py` 中两项 WMS Outbox beat/route 与两项旧 status task route/beat 配置
- `src/celery_app/outbox_dispatch_composition.py` 中 `WMS_DATA`/`WMS_DISPATCH` claim scope、WMS engine
  cache/build 分支和旧 lane runtime 注入；`SYSTEM` scope 只为非 WMS SystemOutbox 保留
- `src/core/task_queue_gateway.py` 中两个 WMS Outbox task 常量、两个 `OutboxDispatchTarget` WMS 枚举值、映射项及
  `enqueue_wms_effect_status`；通用 runtime inbox/internal signal/非 WMS outbox 排队能力不在本清单删除
- `src/app/sys/services/outbox_engine.py` 中 WMS submit observation、status enqueue 及
  `WMS_ASYNC_EFFECT_OPERATION_IDENTITIES` 分支；通用非 WMS SystemOutbox engine 留给 Phase 6/7/8 的明确所有者
- `src/app/sys/models/outbox.py` 中 WMS operation identity import/常量、WMS idempotency validator 与调用点；通用
  SystemOutbox 表和非 WMS 约束不由本清单提前删除
- `src/app/sys/canonical_dispatch.py` 中 WMS identity regex/集合及 WMS 幂等签名特判；任何 WMS canonical/Header
  只能由 Phase 3 Adapter 合同 + Phase 2 已装配 Transport 拥有
- `src/app/runtime/orchestration/services/effect_reducer_service.py` 中 WMS async identity、status-authoritative 和
  recovered typed ACK 特判；最终可靠对象的 reducer/投影测试通过后随旧 Intent/Effect owner 原子删除
- `src/app/runtime/system_capabilities/wms/effect_runtime.py` 的 WMS SystemOutbox producer，以及
  `src/celery_app/tasks/workline.py` 中 E11 对 `OutboxDispatchTarget.WMS_FULFILLMENT` 的唤醒；改由唯一
  `TransportTask` 持久化/领取入口返回其自己的唤醒事实，不桥接旧 WMS Outbox target
- `src/app/runtime/orchestration/integration_lab.py` 的旧 Runtime/Profile 场景 owner；Phase 4 最小 fake 的端到端
  successor 通过后删除，`test_runtime_integration_lab.py` 与 JSON fixture 同步改向最终对象或删除，不得继续 import
  已删除的 Runtime owner
- `src/app/wms_integration/services/evidence_service.py` 中 `record_async_summary`，以及 model/repository/service
  中只服务旧链的 provider identity/digest 字段
- §2.1 全部 `PHASE4_HANDOFF` 文件，包括 `operation_contract.py`、`operation_registry.py`、
  `query_evidence.py` 和 `ports/query_outcome.py`；`endpoint_compiler.py` 与全部 `provider_*` 已在 Phase 3 删除，
  不得再次列为 Phase 4 handoff
- `src/app/runtime/system_capabilities/wms/` 中 Phase 3 仅为旧生命周期保留的 fulfillment definitions、
  effect runtime、contracts 和生成资产；provider catalog/conformance/readiness/startup 已在 Phase 3 删除
- `src/app/runtime/system_capabilities/generated_index.py` 中剩余 WMS 条目，以及只为旧 WMS capability 服务的
  `scripts/generate_runtime_extensions.py` import/生成分支

- [ ] guardrail 断言上述生命周期资产在 Phase 3 存在且目标公共模块不 import 它们；同时证明这些资产及其
  production callers 零 `httpx`、零 credential resolver、零 Provider Profile/startup/readiness/endpoint compiler
  import，只能依赖 Phase 2 Transport 与类型化 WMS 端口。任一保留文件 import Phase 4 删除集即失败；
  这不是永久 allowlist。
- [ ] 在 guardrail 顶部写明删除条件：`WmsConfirmation` 与 `TransportTask` 生产路径、权威测试、
  crash/retry/fencing 测试全部通过。
- [ ] Phase 4 删除这些资产时必须同时删除 guardrail 本身，不把“待删测试”留到 Phase 8。
- [ ] 删除 fulfillment definitions 前先切换全部调用者；重新生成/改写全局 index 后，所有保留消费者均不得
  观察到 WMS capability 条目，最终删除整个 `system_capabilities/wms/` 目录。
- [ ] 将下列测试的 WMS 切片原子处置：`test_wms_effect_lane_dispatch.py`、`test_wms_effect_runtime.py` 删除并由
  `TransportTask`/`WmsConfirmation` 组合与可靠性测试承接；`test_outbox_dispatch_target_gateway.py`、
  `test_celery_task_runtime_contract.py`、`test_system_outbox_claim_scope_contract.py`、
  `test_system_outbox_dispatch_concurrency_postgresql.py`、`test_system_outbox_engine_boundaries.py`、
  `test_external_http_transport_mapping.py` 和 `test_runtime_inbox_celery_cutover.py` 只保留非 WMS SystemOutbox/队列
  断言，全部 WMS scope/task/status-enqueue 断言删除。successor 必须先通过，不能用旧任务名 alias 保持测试。
- [ ] `test_typed_effect_callback_routing.py` 拆分所有权：Phase 3 Adapter 合同测试继续验证 WMS hint DTO、operation/
  dispatch correlation 与调用 typed hint port；Phase 4 核心测试验证 `TransportTask` hint 去重、立即可领取、crash
  恢复且不直接终态。旧 router 测试与生产 owner 在 successor 通过后删除。
- [ ] `test_wms_runtime_inbox_inbound.py` 的普通事件持久化/幂等断言由 `InboundEvidence` 测试承接，status-hint
  断言由 `TransportTask` hint 测试承接；两类 successor 通过后删除旧 handler 测试，并扫描
  `runtime_inbox_orchestrator_bridge.py`、`services/inbox/__init__.py` 和 callback writer，确保没有旧 handler/router
  import、re-export 或 WMS RuntimeInbox 写入分支。
- [ ] `test_callback_orchestration_no_dual_write.py`、`test_callback_runtime_inbox_authority.py` 和
  `test_external_runtime_inbox_persistence_flow.py` 的 WMS 分支改验“只写一条 `InboundEvidence`，不写 RuntimeInbox”；
  `test_wms_event_runtime_inbox_idempotency.py` 的 source-event 幂等、冲突、correlation 和 broker-failure 断言由最终
  `InboundEvidence` FAST/HEAVY successor 逐项承接后删除旧测试。AGV/ECS/Device 的 RuntimeInbox 断言保留给对应
  阶段，不得为迁就混合文件而保留 WMS 分支。
- [ ] `tests/api/test_callback_wms_event_api.py` 将直接构造旧 RuntimeInbox correlation 异常的用例改为最终
  `InboundEvidence` typed outcome，并逐项验证 duplicate/409/413/503 与受限错误证据；
  `tests/api/test_callback_external_api.py` 独立覆盖 WMS status-hint 对最终 `TransportTask` typed outcome 的 duplicate 成功
  ACK 与 400/409/413/503 映射，并对各分支断言只持久化一条 `InboundEvidence`、零 RuntimeInbox 写入；不得借
  ordinary-event API 用例代替 external-hint ingress 的 response/error mapping。两个 API FAST 文件均不得 import、
  patch 或断言旧 RuntimeInbox 异常；
  `test_callback_event_api.py`、`test_callback_result_api.py` 中非 WMS Device/ECS 路径的旧异常合同不由 Phase 4 越权修改。
- [ ] Phase 4 删除旧 WMS handler/router 前执行 API→orchestration→writer 反向调用扫描，证明
  `process_wms_event`、`process_external` 的 WMS hint、`write_external_callback`/`write_wms_event_callback` 均已切换；
  `callback_ingress_service.py` 的 WMS exception branch 只接收最终 typed outcome，不允许 API 继续 ACK 并写入无人消费的
  WMS RuntimeInbox，也不允许 callback 直接推进 TransportTask 终态。
- [ ] 反向扫描同时覆盖绕过 callback writer 的 WorkLine sandbox 路径；
  `test_operation_sandbox_external_idempotency.py` 删除 WMS RuntimeInbox/SystemOutbox 幂等、lease 和时间戳断言，由
  Phase 3 typed fake 与 Phase 4 `InboundEvidence`/`TransportTask` tests 承接；
  `test_runtime_inbox_enqueue_contract.py` 改为证明 WMS sandbox callback 不再属于 RuntimeInbox enqueue caller，且
  WMS 输入在写库/ACK/enqueue 前被拒绝；同一测试或对应 API contract test 必须断言
  `SandboxExternalCallbackRequest`/OpenAPI 的允许集合不含 WMS、缺省请求不再解析为 WMS。不得保留默认
  `source_system="WMS"`、`WMS|RCS` schema pattern 或运行时 fallback。
- [ ] Phase 4 缺席门禁同时扫描 request model、route 和 service：`SandboxExternalCallbackRequest` 不含 WMS
  default/enum/pattern，sandbox route 的 OpenAPI 不暴露 WMS，service 无 WMS 分支，API validation 测试证明请求在任何
  persistence/ACK/enqueue 之前失败。
- [ ] `test_wms_effect_status_service.py`、`test_wms_effect_status_reliability.py` 和
  `test_wms_effect_status_postgresql.py` 的 claim/backoff/fencing/late-result/terminal 断言由 `TransportTask` 对应
  FAST/HEAVY 测试逐项承接后删除；不得因 Phase 3 已做 provider-free rewrite 而把它们留到 Phase 5 或 Phase 8。
- [ ] guardrail 对上述文件执行符号级缺席检查：`dispatch_wms_*_outbox_batch`、`WMS_DATA`/`WMS_DISPATCH` scope、
  WMS `OutboxDispatchTarget`、`enqueue_wms_effect_status`、SystemOutbox 的 WMS identity/idempotency/status-enqueue
  分支全部为零；`WmsConfirmation`/`TransportTask` 是唯一 WMS 可靠记录与 scanner/claim owner。
- [ ] 将五个受保护混合测试继续指向 Phase 5 的核心可靠性承接；Phase 4 删除旧生产 owner 时只允许将其通用
  断言机械改向最终对象，不提前宣称测试计划完成。
- [ ] 逐项核对 §1.7 的八类 AGV/CTU 交接资产，确保 Phase 3 fake 和 typed client 可由 Phase 4
  `TransportTask` 测试直接消费，不依赖旧 Runtime 装配。

**Run:**

```bash
rtk uv run pytest tests/architecture/test_wms_phase4_handoff_guardrail.py -q
rtk ./scripts/markdownlint.sh \
  docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md \
  docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md
```

Expected: Phase 3 无可靠性空窗，Phase 4 删除入口和受保护测试归属明确。

**Commit boundary:** 只暂存本任务 `Files` 的精确路径。提交说明：
`test(wms): 锁定 Phase 4 可靠性原子交接`。

### Task 11：Phase 3 全量验收

- [ ] 公共边界和配置零旧架构 import/名称。
- [ ] 19 查询、8 确认、7 submit/status 与 1 cancel 的类型化合同全部通过。
- [ ] 35 个垂直模块均通过测试态 conformance；生产运行时不存在中心 registry、动态发现或 WMS codegen。
- [ ] API/Celery 各一个进程级 Gateway/WMS 专属 Transport，分页复用 Phase 2 管理的连接池；启动失败和 shutdown
  都关闭 Transport 所拥有的 Client，WMS Adapter 零裸 Client。
- [ ] evidence 发送前/后失败、远端结果未知、原 `dispatch_key` 恢复和非空 `evidence_key` 语义全部通过。
- [ ] 旧可靠链只发生 Transport/配置/认证依赖替换，持久化、claim、重试、fencing 和终态语义未改写且仍是
  唯一活动所有者；没有第二张义务表、第二个 scanner、双写或 fallback。
- [ ] QUERY definitions 已从 generated index 移除，fulfillment definitions 明确留在 Phase 4 handoff；无悬空 import。
- [ ] Phase 3 文件集与 54 文件矩阵完全一致，`evidence/` 双 owner、旧 public export 和未登记生产文件均为零。
- [ ] 全部 WMS 生产模块零 `httpx` import、零凭据解析、零 HMAC 计算、零通用 transport 异常实现；
  WMS canonical/Header/版本纯合同只在 Adapter 包中存在，业务 Gateway 只接收已认证 Transport，Phase 2
  通用传输测试未被复制。
- [ ] `WmsInboundAuthPolicy` 只消费独立 inbound callback policy：可信隔离内网 unsigned 例外有明确合同，
  其他请求沿用既有 API Application/HMAC fail closed；零 Provider Profile、零 outbound credential/Factory 依赖。
- [ ] `src/app/contracts/wms_inbound.py` 缺席；普通业务事件闭集和 status-hint DTO 只由
  `wms_integration/inbound/contracts.py` 拥有，callback writer、auth、external callback contract、旧 RuntimeInbox
  handler 均已改向该单一真源，零旧 import/re-export。
- [ ] `WmsExternalContractProfile`、`WMS_MATERIAL_FLOW_PROFILE` 和 WMS global external contract catalog 零命中；
  callback、插件绑定、迁移盘点、IntegrationLab 与 Runtime bridge 的 WMS 分支只消费目标 inbound/typed port 合同，
  generic ECS/Device/AGV profile 未进入 WMS 路径。
- [ ] 受保护混合测试未删除，Phase 4/5 承接标记一致。
- [ ] `provider_profile_identity`/`provider_profile_hash` 在 WMS 包内只允许命中 evidence model/repository/service
  三个冻结 owner；ports、runtime、query evidence 和目标 Adapter 全部零命中，精确文件集检查通过。
- [ ] FAST、QUALITY 和受影响 HEAVY 通过；只因本阶段变更运行相关 HEAVY，不扩张默认套件。
- [ ] Task 8/9 提交前的 unstaged/staged selector 已命中并实际运行全部相关 HEAVY；本任务再使用 Phase 3
  implementation baseline 检查整个阶段差异，不能用当前空工作区代替阶段范围。

**Verification:**

```bash
rtk uv run pytest \
  tests/contracts/wms_integration \
  tests/wms_integration \
  tests/architecture -q
rtk uv run ruff format --check src tests
rtk uv run ruff check src tests
rtk uv run pyright src
rtk bash scripts/architecture-guardrails.sh --mode enforced
rtk ./scripts/git-quality-gate.sh --profile quality
rtk uv run scripts/select_heavy_tests.py --base 28eb99d9
rtk uv run pytest \
  tests/integration/test_celery_async_runtime.py \
  tests/integration/test_celery_async_runtime_postgresql.py \
  tests/integration/test_celery_prefork_harness_cleanup.py \
  tests/integration/test_wms_process_composition_postgresql.py \
  tests/resilience/test_runtime_integration_lab.py \
  tests/resilience/test_wms_circuit_breaker.py \
  tests/integration/workline_capabilities/test_wms_effect_status_postgresql.py -q
rtk git diff --check
```

Expected: 所有命令退出 0；FAST 预算不退化；PostgreSQL 可靠性测试证明 Phase 4 接管前没有状态语义缺口。

**Final absence checks:**

Phase 3 的“Provider 缺席”检查只禁止旧模块/import、配置符号、类型和运行时 owner；不得匹配
`models/evidence.py`、repository/service 中为唯一旧可靠链冻结到 Phase 4 的 `provider_profile_identity`/
`provider_profile_hash` 数据字段。Task 10 在最终对象承接后删除这些字段的应用层读写，Phase 9 再清理数据库列。

```bash
! rtk rg -n \
  'httpx|external_http_credentials|credential_provider|import hmac|from hmac|WMS_PROVIDER_PROFILE|WMS_PROVIDER_PROCESS_ROLE|src\.app\.(runtime|sys)|operation_registry|operation_contract|provider_(profile|manifest|readiness|simulator_registry|startup)|effect_runtime|effect_status' \
  src/app/wms_integration/capabilities \
  src/app/wms_integration/ports/capabilities.py \
  src/app/wms_integration/ports/confirmation.py \
  src/app/wms_integration/ports/forwarded_transport.py \
  src/app/wms_integration/adapters/auth.py \
  src/app/wms_integration/adapters/http_gateway.py \
  src/app/wms_integration/configuration.py \
  src/app/wms_integration/factory.py
! rtk rg -n \
  '(^|[^A-Za-z0-9_])httpx([^A-Za-z0-9_]|$)|external_http_credentials|credential_provider|import hmac|from hmac|WMS_PROVIDER_PROFILE|WMS_PROVIDER_PROCESS_ROLE|src\.app\.wms_integration\.provider_(profile|manifest|readiness|simulator_registry|startup)|WmsProvider(Profile|Readiness|ProcessRole|Startup)|endpoint_compiler|deployment_attestation' \
  src/app/wms_integration
provider_field_files="$(rtk rg -l 'provider_profile_(identity|hash)' src/app/wms_integration | sort)"
expected_provider_field_files=$'src/app/wms_integration/models/evidence.py\nsrc/app/wms_integration/repositories/evidence_repository.py\nsrc/app/wms_integration/services/evidence_service.py'
test "$provider_field_files" = "$expected_provider_field_files"
! rtk rg -n \
  'src\.app\.contracts\.wms_inbound|wms_integration\.(provider_profile|endpoint_compiler)|system_capabilities\.wms\.(provider_catalog|provider_conformance|conformance_manifest|conformance_matrix)' \
  src tests
! rtk rg -n \
  'provider_profile_support|build_compiled_provider_profile|build_provider_catalog' \
  tests
! rtk rg -n \
  'OutboundHttpTransportFactory|credential_reference|credential_provider|httpx' \
  src/app/callback/services/wms_inbound_auth.py
```

**Commit boundary:** 只暂存 Task 11 验收修正涉及的精确路径，禁止 `git add -A`。提交说明：
`refactor(wms): 完成 WMS 薄接入边界收敛`。

---

## 5. 测试语义与重量预算

| 层级 | 保留的完整断言 | 不进入该层 |
| --- | --- | --- |
| Contract FAST | 垂直 DTO/spec、测试态 conformance、合同明确要求的可选认证、四分支 outcome、端口形状 | 具体插件流程、真实 WMS |
| Adapter FAST | WMS method/path/DTO、分页、业务结果映射、WMS canonical/Header、脱敏证据 | Phase 2 Client 生命周期与通用异常矩阵、Docker、真实网络 |
| Deployment FAST | `WMS_CONFIG_FILE`、API/Celery 单 Gateway/Transport、fail-fast、资源关闭 | 裸 Client、真实 WMS、旧配置 fallback |
| Architecture FAST | import closure、公共 API、QUERY index 收缩、Phase 4 handoff | 业务状态机重复断言 |
| Persistence HEAVY | breaker 竞争、status claim/fencing、证据事务 | 35 项重复全链路矩阵 |
| Phase 4/5 | `TransportTask`、`WmsConfirmation` crash/retry/terminal | 旧 Runtime/Effect 所有者 |

FAST 中 35 项操作只用一个参数表覆盖 method/path/DTO，不为每项复制完整网络场景。错误分支只在共享
response mapper 完整覆盖；各端口方法只验证自己的新增绑定，严格遵守“一条行为最低稳定层完整断言”。

---

## 6. 自评审

### 6.1 SPEC 覆盖

| SPEC 要求 | 本计划承接 |
| --- | --- |
| WMS 是单据、库存、主数据和授权权威 | 19 项 query DTO/port 保留，WES 不复制权威状态 |
| 插件只依赖 `WmsCapabilities` | Task 4/7 的显式 Protocol、注入和 QUERY 平台删除 |
| 物理完成后形成可靠 WMS 确认 | Task 8 将旧唯一可靠 owner 原地改接无状态 sender；Phase 4 建立最终义务对象后原子替换旧 owner |
| WMS 转发 AGV/CTU 仍经 Transport Port | E08–E14/E16 独立 Client，生命周期明确交给 Phase 4 |
| WMS Adapter 消费公共 HTTP 基础层 | Task 6–8 注入 Phase 2 Transport，删除 WMS transport helper 和裸 Client |
| WMS callback ingress 认证独立于 outbound | Task 5/8 建立独立 inbound policy，保留可信隔离内网例外与既有 API auth fail-closed 边界 |
| 不保留 RuntimeIntent/Generic Effect/System Capability 热路径 | Phase 3 删除 QUERY definitions；旧 fulfillment 闭包由 Task 10 锁定到 Phase 4 删除 |
| 不兼容旧配置/旧数据 | 单一 `WMS_CONFIG_FILE`，无 alias/fallback；不写 migration 转换 |
| 测试按语义和重量收敛 | Task 9 和 §5，不用全量 conformance/Docker 证明静态合同 |

### 6.2 类型一致性

- Query、confirmation、transport 的正常远端分支使用相同 `WmsCallOutcome[T]`；本地配置/evidence 故障使用
  明确基础设施错误，不伪装为远端 outcome。
- `dispatch_key` 只表示 WMS 幂等身份；Phase 3 Client 不把它解释成生命周期状态。
- E08–E14 的 pending/terminal 类型只由 transport Client 返回；`WmsConfirmationSender` 不可见。
- E16 按领域归 Transport，不因同步 HTTP completion 被误放入 confirmation。
- 每个 capability 模块的 `WmsCallSpec` 是静态 wire 事实，不构成中心 Catalog 或运行时发现 API。
- WMS Adapter 包只拥有无 Secret 的 canonical/Header/签名版本纯合同；Composition Root 将其交给 Phase 2
  Factory，Secret 解析、HMAC/Clock/Nonce 和认证装配均由 Phase 2 完成，业务 Gateway 只接收已认证 Transport；
  `BASIC` 不进入配置闭集。
- inbound callback policy 与 outbound auth 是两个方向的不同合同；前者由 callback ingress 消费，不能读取
  Phase 2 credential，也不能由 outbound `NONE` 隐式推导。

### 6.3 DRY/KISS/SOLID/YAGNI

- 一个 strict wire base、一个 outcome union、一个 WMS 业务解释 pipeline，并复用 Phase 2 每进程一个 WMS
  Transport，避免 query/effect 两套实现及分页连接池抖动。
- 面向消费者拆分三个端口；插件看不到 confirmation/transport，可靠对象看不到 HTTP。
- 不创建 DSL、动态 registry、provider 插件、通用 workflow、第二套账本或未来协议扩展点。
- 35 项差异只保留在对应垂直 capability 模块，公共 API 不暴露元模型；测试态 harness 让能力增删保持低成本。

### 6.4 占位符审计

计划中不得存在未决占位标记、未定类名或未定文件路径。实施完成前运行：

```bash
! rtk rg -n 'T[B]D|TO[D]O|以后[补]|待确[认]|placeholde[r]' \
  docs/superpowers/plans/2026-08-03-wes-wms-thin-access-convergence.md
```

### 6.5 复审结论

结论为 **通过，17 项 Eng Review 决策、第二轮 9 项审计结论和本轮 4 项边界复审均已落实且无未决选择**。
关键门禁包括：Phase 3 只替换旧可靠链的 Transport/配置/认证依赖，不改写其可靠生命周期；能力垂直内聚且生产无
registry；54 文件与旧测试均有逐项处置；Provider Profile/旧认证/旧 Transport 在 Phase 3 归零；
QUERY/fulfillment System Capability 分阶段收缩；evidence 远端未知、单一 `dispatch_key`、WMS breaker、
分页累计预算、进程级 WMS Transport、部署生命周期、测试所有权与 HEAVY selector 闭环都有明确测试。

只有 Phase 2 退出门禁通过且本计划按实际 Phase 2 交付重新批准后，才能按 Task 1–11 顺序实施。Phase 3
完成态不是最终系统完成态，不得单独合并回 `develop`；必须继续同一架构收敛分支进入 Phase 4，完成可靠
所有者与静态依赖闭包的原子删除。
