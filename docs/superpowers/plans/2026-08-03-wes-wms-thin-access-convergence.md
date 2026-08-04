# WES Phase 2 WMS 薄接入边界收敛实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前 54 个文件、约 8,671 行并混合 Provider、Catalog、Runtime、Effect、status lifecycle
与部署鉴证的 `wms_integration`，收敛成类型化 WMS 查询、业务确认发送和无状态 WMS 转发搬运 Client；
同时保住 Phase 3 接管前唯一活动可靠性链，不产生兼容层、双活或事实丢失窗口。

**Architecture:** 35 项已冻结 wire contract 继续存在，每项以一个垂直 capability 模块内聚 DTO、固定
method/path、拒绝码和 `WmsCallSpec`，并按目标所有权暴露三条显式窄边界：19 项只读查询由
`WmsCapabilities` 暴露，E01–E07/E15 由 `WmsConfirmationSender` 暴露，E08–E14/E16 由
`WmsForwardedTransportClient` 暴露。生产运行时不提供 capability registry、generic `call`、动态发现或
codegen。Phase 2 只切换 QUERY 并交付无状态 sender/client，不改写旧可靠链；Phase 3 建立
`WmsConfirmation` 与 `TransportTask` 后原子切换并删除旧生命周期及其静态依赖闭包。

**Tech Stack:** Python 3.13、Pydantic 2、HTTPX、FastAPI、SQLModel/SQLAlchemy、PostgreSQL、Celery、
Pytest 9、Ruff、Bandit、Import Linter、Bash architecture guardrails。

**Status:** Approved — Eng Review decisions applied

**Authority:**

- `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`
- `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- `docs/contracts/wms-northbound-interaction-contract.md`
- `docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md`

**Implementation baseline:** `origin/develop@cf2f1f91`

---

## 1. 复审裁决

### 1.1 采用方案

采用“目标薄边界先落地，可靠所有者在 Phase 3 原子交接”的方案。

```text
业务插件 ───────────────> WmsCapabilities ───────────────┐
                                                            │
Phase 3 WmsConfirmation ─> WmsConfirmationSender ───────────┤
                                                            ├─> HttpWmsGateway
Phase 3 TransportTask ───> WmsForwardedTransportClient ─────┘

Phase 2 期间：QUERY 切换到 HttpWmsGateway；旧 Runtime/Effect/status 保持原样，
             仍是确认与搬运唯一活动可靠性所有者，不临时改接新 Client。
Phase 3 切换：最终对象及权威测试通过 → 一次切换 → 删除旧所有者。
```

不采用以下两种方案：

1. **Phase 2 立即删除旧 Effect/status。** `WmsConfirmation` 与 `TransportTask` 尚不存在，会违反
   “最终可靠对象先建立、旧所有者后删除”的 SPEC 门禁并丢失可靠确认语义。
2. **新旧可靠生命周期并行运行。** 会形成双写、双领、双重试和终态竞争，违反单一运行路径约束。

### 1.2 35 项合同的目标所有权

| 边界 | Operation | Phase 2 职责 | 明确不拥有 |
| --- | --- | --- | --- |
| `WmsCapabilities` | Q01–Q19 | 同步类型化查询、分页、错误映射、证据 | 插件 Decision、依赖暂停状态 |
| `WmsConfirmationSender` | E01–E07、E15 | 同步提交并返回终态业务结果 | 确认义务、领取、重试、恢复 |
| `WmsForwardedTransportClient` | E08–E14、E16 | submit/status/cancel 的类型化 HTTP Client | TransportTask、批次状态、轮询调度、重试、终态成员最终事实 |

E16 虽返回同步 HTTP 结果，但它取消的是已有搬运请求，因此归 Transport Port，不归 WMS 业务确认。

### 1.3 公共 outcome 与 evidence 失败语义

所有预期远端结果使用同一封闭 union，不用异常表达正常业务分支：

| 分支 | 必填字段 | 重试语义 |
| --- | --- | --- |
| `WmsCallSuccess[T]` | `value`、真实非空 `evidence_key` | 已成功，不重试 |
| `WmsBusinessReject` | `reason_code`、`message`、真实非空 `evidence_key` | 正常业务拒绝，不重试 |
| `WmsDependencyFailure` | `reason_code`、`message`、`retryable`、`retry_after_seconds`、真实非空 `evidence_key` | 只按显式 `retryable` 判定 |
| `WmsContractFailure` | `reason_code`、`message`、真实非空 `evidence_key` | 合同错误，不重试 |

HTTP 超时、断路器打开、5xx、业务拒绝和远端 Payload 不合约分别映射到依赖失败、业务拒绝和合同失败。
无效本地配置、缺失依赖注入、程序错误和 evidence 基础设施失败不伪装成正常远端 outcome：

| evidence 故障点 | 必须行为 |
| --- | --- |
| 发送前无法创建 STARTED evidence | fail closed，不获取网络结果、不发送 HTTP，抛出明确的本地基础设施错误 |
| HTTP 已发送但 final evidence 无法持久化 | 不返回普通 outcome，标记“远端结果未知”；只携带真实已创建的 evidence key，不得伪造 |
| 写操作远端结果未知 | Phase 3 可靠所有者保留原 `dispatch_key` 恢复；禁止生成新键或按普通依赖失败自动重试 |

因此，所有正常 outcome 的 `evidence_key` 都非空且可查询；evidence 未成功持久化时不存在“看似正常但使用空键”
的第五分支。

### 1.4 配置裁决

用一个 `WMS_CONFIG_FILE` 取代 Provider Profile。配置只包含：

- 单一 `base_url`；
- 查询、确认和状态查询的有界 HTTP 预算及分页上限；
- 仅在已确认 WMS 合同明确要求时提供的可选认证与凭据引用；当前局域网默认不配置认证。

删除 provider identity、contract digest、readiness、deployment attestation、process role、execution lane、
capability manifest、simulation registry 和运行时 conformance。method/path 不进入部署配置，由各垂直 capability
模块的 `WmsCallSpec` 固定；配置不得添加、删除或覆盖能力。

### 1.5 Phase 2 不可删除的资产

下列五个混合测试受测试收敛计划 Task 4/5 保护，只允许因 import 路径变化做机械更新，不得在 Phase 2
删除或削弱断言：

- `tests/mock/wms_operation_fixtures.py`
- `tests/contracts/wms_integration/test_wms_operation_catalog.py`
- `tests/contracts/wms_integration/test_effect_status_contract.py`
- `tests/support/runtime_inbox_processing_postgresql.py`
- `tests/integration/test_runtime_inbox_processing_postgresql.py`

旧 Effect/status/Outbox 可靠链也不得在 Phase 2 删除或临时改接新 Client；其精确交接清单和静态依赖闭包见
Task 10。

### 1.6 Phase 2 → Phase 3：WMS 转发 AGV/CTU 交接包

该交接包单独验收，但不形成 Phase 2.5、新运行阶段或第四条可靠性链。

| Operation | 业务搬运目标 | Phase 2 Client | Phase 3 `TransportTask` 承接 |
| --- | --- | --- | --- |
| E08 `request_rack_supply` | 为工作位补充指定类型货架 | submit + rack-supply status DTO | demand identity、领取、进度、最终到位事实 |
| E09 `request_rack_transport` | 搬运指定货架到目标工作位 | submit + rack-transport status DTO | 任务身份、重试、最终位置 |
| E10 `change_rack_face` | 在工作位切换货架面 | submit + face-change status DTO | 任务推进、最终货架面 |
| E11 `full_box_exchange` | 满箱换空箱并返回最终储位关系 | submit + exchange status DTO | 冻结成员、交换进度、最终关系 |
| E12 `move_bins_to_conveyor_entry` | CTU 批量投箱到输送线入口 | submit + entry-batch status DTO | 冻结批次成员、批次状态、终态成员最终事实、未知结果处理 |
| E13 `move_bins_from_conveyor_exit` | CTU 批量接回输送线出口料箱 | submit + exit-batch status DTO | 候选前缀、批次状态、终态成员最终货架/储位事实 |
| E14 `request_load_unit_transport` | 搬运托盘、料架或其他载具 | submit + load-unit status DTO | 任务身份、进度、最终位置 |
| E16 `cancel_request` | 取消上述 WMS 转发搬运请求 | 单次 cancel + typed disposition | 何时允许取消、取消后的任务状态 |

**Phase 2 必须交付：**

- 八类 request/ACK/pending/terminal/cancel wire DTO 和稳定拒绝码；
- 七个显式 submit、七个显式 status、一个显式 cancel 方法；
- `operation identity + dispatch_key` 幂等头、Provider reference 和 source version 的透传校验；
  `dispatch_key` 是 submit、ACK、status、terminal、cancel、hint 的唯一 wire 幂等键，不定义
  `idempotency_key` 别名或双键映射；
- method/path、超时、响应预算、错误分类、脱敏同步调用证据，以及冻结合同真实要求的可选认证；
- 无状态 fake，供 Phase 3 在不启动真实 WMS、Celery 或旧 Runtime 的情况下测试 Transport Port。
- sender/client 的合同与 fake，不进入旧 Effect/status 生产装配。

**Phase 3 才能拥有：**

- `TransportTask`、批次成员和状态持久化；
- due claim、轮询间隔、重试预算、依赖暂停和恢复；
- callback/status hint 唤醒、source-version fencing、迟到结果和未知物理结果处理；
- 任务终态驱动的对象/位置投影以及插件下一步 Decision；
- 无状态 Client 到 `WmsConfirmation`/`TransportTask` 的生产装配和原子切换。

**交接不变量：** Phase 2 Client 的每次调用只完成一次 HTTP 交互；不得在 Client 内循环到终态、写
`TransportTask`、启动定时任务或修改对象/位置投影。Phase 3 不得重新实现 path、DTO、HTTP 错误映射或合同明确要求的可选认证
或 WMS evidence，否则视为跨层复制。

---

## 2. 最终文件布局

Phase 2 完成后，目标公共边界为：

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
    ├── http_transport.py
    ├── redaction.py
    └── response_mapping.py
```

每个 capability 模块同时定义 request/result DTO、固定 method/path、稳定拒绝码和一个不可变
`WmsCallSpec`；`_shared.py` 只提供 `StrictWmsModel`、分页/value type 和 `WmsCallSpec` 数据结构，不保存能力列表。
`ports`、`configuration.py`、`factory.py` 和 `inbound` 是允许外部依赖的公共边界；Gateway 的私有 `_call`
只接收调用方显式传入的 spec，不按字符串查 registry。旧可靠性文件在 Phase 2 期间仍存在，但不得被目标公共
模块 import，且必须由 Task 10 的 Phase 3 删除门禁锁定。

新增、优化或删除 WMS 能力的固定动作只有：修改一个 capability 模块、对应窄 Protocol 方法、Gateway 显式
方法和同名测试。测试态 harness 会扫描 capability 文件并检查四者闭包；生产包不存在同类扫描器或目录发现。

| 变更 | 开发动作 | 完成门禁 |
| --- | --- | --- |
| 新增能力 | 新增一个垂直模块、一个显式端口/Gateway 方法和同名测试 | harness 发现且四者闭合；北向合同同步增加一行 |
| 优化能力 | 只改该模块与同名测试；共享逻辑仅在三处重复且语义稳定后下沉 `_shared.py` | 其他既有能力合同无差异 |
| 删除能力 | 先删除调用者，再删除端口/Gateway 方法、模块和同名测试 | harness、import closure、文件集与北向合同同时归零 |

### 2.1 当前 54 个生产文件处置矩阵

`MOVE` 表示先把仍需语义迁入上述目标文件并更新全部 import，再删除源文件；`PHASE3_HANDOFF` 表示 Phase 2
保持原样，只允许 DTO import 的机械调整，并在 Phase 3 与旧可靠所有者原子删除。

| 当前文件 | 处置 | 最终所有者或删除条件 |
| --- | --- | --- |
| `__init__.py` | KEEP | 保持空领域入口，不 re-export 旧类型 |
| `adapters/__init__.py` | KEEP | 只导出目标 Gateway；Phase 3 前可保留旧 adapter 的延迟 import |
| `adapters/effect_status_query_adapter.py` | PHASE3_HANDOFF | 与旧 status owner 原子删除 |
| `deployment_attestation.py` | PHASE3_HANDOFF | 旧 Effect 部署闭包删除时一并删除，不迁入目标配置 |
| `effect_lane_runtime.py` | PHASE3_HANDOFF | 与旧 Effect lane 原子删除 |
| `effect_preparation_runtime.py` | PHASE3_HANDOFF | 与旧 preparation owner 原子删除 |
| `effect_runtime.py` | PHASE3_HANDOFF | 与 `WmsConfirmation`/`TransportTask` 切换时删除 |
| `endpoint_compiler.py` | PHASE3_HANDOFF | 仍被旧 status 链静态引用；Phase 3 删除 |
| `evidence/__init__.py` | DELETE | `models/evidence.py` 是唯一 WMS 调用证据模型所有者 |
| `evidence/catalog.py` | DELETE | 删除 Provider reference catalog/drift 平台语义 |
| `evidence/envelope.py` | DELETE | 最终外部证据包络由 `InboundEvidence`/共享 callback 合同拥有 |
| `models/__init__.py` | KEEP | 只导出 breaker/evidence 目标模型 |
| `models/circuit_breaker.py` | KEEP | 共享 PostgreSQL breaker 状态 |
| `models/evidence.py` | KEEP | 唯一 WMS 调用 evidence 模型；旧链字段冻结到 Phase 3，最终模型删除 provider identity/digest |
| `models/ports.py` | DELETE | 三项旧同步 DTO 被垂直 capability 模块替代 |
| `operation_contract.py` | PHASE3_HANDOFF | 旧链静态依赖；目标模块不得 import，Phase 3 删除 |
| `operation_registry.py` | PHASE3_HANDOFF | 旧链静态依赖；不扩展，Phase 3 删除 |
| `ports/__init__.py` | KEEP | 只公开目标窄端口；旧延迟导出在 Phase 3 删除 |
| `ports/document_operations.py` | MOVE | DTO 分拆到对应 Q08–Q13/Q19 capability 模块后删除 |
| `ports/effect_preparation.py` | PHASE3_HANDOFF | 与旧 Effect preparation owner 删除 |
| `ports/effect_status.py` | PHASE3_HANDOFF | 与旧 status owner 删除 |
| `ports/event.py` | MOVE | 迁入 `inbound/contracts.py` |
| `ports/fulfillment_operations.py` | MOVE | DTO 分拆到对应 E07–E16 capability 模块；旧链 import 机械改向后删除 |
| `ports/inventory_operations.py` | MOVE | DTO 分拆到对应 Q14/Q15/E01–E06 capability 模块后删除 |
| `ports/master_data_operations.py` | MOVE | DTO 分拆到对应 Q01–Q07 capability 模块后删除 |
| `ports/operation_common.py` | MOVE | 公共 value type 迁入 `capabilities/_shared.py` 后删除 |
| `ports/query_execution.py` | DELETE | QUERY 切换后由显式 `WmsCapabilities` 取代 |
| `ports/query_outcome.py` | PHASE3_HANDOFF | 旧 status adapter 静态依赖；Phase 3 删除 |
| `ports/reconciliation_operations.py` | MOVE | DTO 分拆到对应 Q16–Q18 capability 模块后删除 |
| `provider_manifest.py` | PHASE3_HANDOFF | 不扩展；旧 Effect 配置闭包随 Phase 3 删除 |
| `provider_profile.py` | PHASE3_HANDOFF | 不扩展；旧 Effect 配置闭包随 Phase 3 删除 |
| `provider_readiness.py` | PHASE3_HANDOFF | 旧 runtime/lane 静态依赖；Phase 3 删除 |
| `provider_simulator_registry.py` | PHASE3_HANDOFF | 不扩展；旧 Effect 验收闭包随 Phase 3 删除 |
| `provider_startup.py` | PHASE3_HANDOFF | 不扩展；旧 Effect 启动闭包随 Phase 3 删除 |
| `query_evidence.py` | PHASE3_HANDOFF | 旧 runtime/status adapter 静态依赖；Phase 3 删除 |
| `query_executor.py` | DELETE | 19 项 QUERY 原子切换到 Gateway 后删除 |
| `query_projection.py` | DELETE | QUERY 返回 typed result，不保留投影 facade |
| `query_response.py` | DELETE | 共享 response mapping 接管后删除 |
| `query_runtime.py` | DELETE | API/Celery Gateway 装配接管后删除 |
| `repositories/__init__.py` | KEEP | 只导出 breaker/evidence repository |
| `repositories/circuit_breaker_repository.py` | KEEP | 共享 breaker persistence |
| `repositories/evidence_repository.py` | KEEP | 唯一 evidence persistence |
| `runtime_factory.py` | PHASE3_HANDOFF | 旧 status 链装配 owner；Phase 3 删除 |
| `services/__init__.py` | KEEP | 收缩为目标 transport/evidence/breaker/redaction 导出 |
| `services/callback_normalizer.py` | PHASE3_HANDOFF | 旧 status hint 类型静态依赖；Phase 3 由最终 inbound owner 接管 |
| `services/circuit_breaker_service.py` | KEEP | 共享 breaker service |
| `services/evidence_service.py` | KEEP | 同步 evidence 目标 owner；旧 async summary 在 Phase 3 删除 |
| `services/exceptions.py` | MOVE | 正常远端分支迁入 outcome；仅保留明确的本地基础设施错误 |
| `services/fulfillment_lifecycle.py` | PHASE3_HANDOFF | 与旧 fulfillment lifecycle 原子删除 |
| `services/http_transport.py` | KEEP | 复用预算、有界响应和 borrowed client primitive；不预建通用签名框架 |
| `services/redaction.py` | KEEP | 共享脱敏/hash |
| `services/wms_event_normalizer.py` | MOVE | 迁入 `inbound/normalizer.py` |
| `state_machine.py` | PHASE3_HANDOFF | 与旧 Effect/status state machine 删除 |
| `transport_url.py` | MOVE | URL 校验迁入 `configuration.py` 后删除 |

矩阵验收必须证明恰好覆盖上述 54 个当前文件；若实施基线文件集变化，先更新矩阵再编码。Phase 2 完成时，
`models/evidence.py` 是唯一 WMS 调用 evidence 模型，`evidence/` 目录不存在；最终目标文件集与本节布局一致，
所有 `DELETE` 源文件和旧抽象 public export 零命中。

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
Outbox 或重试；Phase 3 的 `WmsConfirmation` 负责稳定生成并保存它。

### 3.3 `WmsForwardedTransportClient`

提供七个显式 `submit_*`、七个显式 `get_*_status` 和一个 `cancel_request` 方法。submit 成功返回
`WmsTransportAccepted`；status 成功返回对应的 `WmsTransportPending | OperationResult`；cancel 返回
`CancelRequestResult`。公共端口不提供 scanner、poll-until-terminal、callback handler 或后台任务。

---

## 4. 实施任务

执行分五条 lane：A 负责权威文档和两类处置矩阵；B 负责垂直 capability/ports 与测试态 conformance；C 负责
配置、HTTP/evidence/breaker 和部署生命周期；D 在 B+C 通过后执行 Gateway、QUERY 切换、索引收缩与旧测试
处置；E 在 D 后冻结 Phase 3 handoff 并完成验收。B/C 可以并行，但必须预先按文件分配所有权；D/E 顺序执行，
Task 8/9 保持同一原子工作树。

### Task 1：冻结权威文档和原子交接边界

**Status:** 已由本计划评审批次完成；后续执行从 Task 2 开始。

**Files:**

- Modify: `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`
- Modify: `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- Reference: `docs/superpowers/README.md`（Task 1 外部归档索引）
- Modify: `docs/business/wms_rcs_interface_requirements.md`
- Modify: `docs/business/wms_full_factory_operation_blueprint.md`
- Modify: `docs/contracts/wms-northbound-interaction-contract.md`
- Modify: `docs/architecture/authority-matrix.md`

- [x] 在当前 SPEC 中明确 supersede 旧 WMS Provider/Catalog/Runtime/Effect 架构，但保留 35 项 wire contract。
- [x] 在 §5.3 区分同步查询/确认与 WMS 转发异步搬运，声明生命周期归 `TransportTask`。
- [x] 在总控 Phase 2/3 写入“旧可靠所有者保留到最终对象后原子删除”的单向交接规则。
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
实现行为门禁从 Task 2 开始，并随对应生产代码变更交付。

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

- [ ] 写失败测试，扫描 `capabilities/`、四个目标 `ports/*.py`、`configuration.py`、`factory.py` 和
  `inbound/`，禁止 import：
  `src.app.runtime`、`src.app.sys`、`operation_contract`、`operation_registry`、`provider_*`、
  `effect_runtime`、`effect_status`、`RuntimeIntent`、`Effect`、`Outbox`、`SystemCapability`。
- [ ] 用仓库内临时违规 fixture 验证规则能捕获上述 import；目标文件尚不存在时只验证规则实现，
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

- [ ] 按 §1.3 契约表写四分支 union 测试；所有正常分支必须带真实非空 `evidence_key`，依赖失败必须显式
  给出 `retryable`，业务拒绝和合同失败默认不可重试。
- [ ] 单独测试发送前 evidence 失败与发送后 evidence finalization 失败；二者均不得构造普通 outcome 或伪造
  `evidence_key`，写操作的未知结果必须保留原 `dispatch_key`。
- [ ] 按 §3 的完整方法表实现 Protocol；所有返回类型为具体 `WmsCallOutcome[T]`。
- [ ] `WmsForwardedTransportClient` 的 status 方法使用明确的 pending/terminal 类型，不返回 `dict`、
  `Any` 或旧 `WmsEffectStatusSnapshot`。
- [ ] `ports/__init__.py` 为目标端口增加显式导出，不新增旧 query/effect/status re-export；旧链当前依赖的延迟
  export 保持原样并登记到 Task 10，Phase 3 原子删除。

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

### Task 5：建立目标连接配置

**Files:**

- Create: `src/app/wms_integration/configuration.py`
- Create: `tests/contracts/wms_integration/test_wms_connection_settings.py`

- [ ] 写失败测试：配置文件必须绝对、可读、YAML 键唯一，`base_url` 必须是合法 HTTP(S) URL，预算与分页上限必须为正；只有真实 WMS 合同明确要求时才验证对应可选认证字段。
  配置不接收 operation path 或能力开关。
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

### Task 6：收敛通用 HTTP、错误映射、同步证据和 breaker

**Files:**

- Create: `src/app/wms_integration/services/response_mapping.py`
- Modify: `src/app/wms_integration/services/http_transport.py`
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
- Modify: `tests/wms_integration/test_query_runtime_evidence.py`
- Modify: `tests/resilience/test_wms_circuit_breaker.py`

- [ ] 先移植最低层测试，覆盖 HTTP 408/429/4xx/5xx、业务拒绝、`Retry-After`、超时、压缩炸弹、
  JSON 深度/字段/总字节预算和不合约 Payload。
- [ ] 为目标 Gateway 建立共享 `WmsCallOutcome` 映射，不得 `except Exception` 后一律可重试；
  旧 `query_response.py` 在生产原子切换前保持唯一活动实现。
- [ ] 目标同步 evidence 只写 operation、request/trace、脱敏快照、hash、HTTP status、reason、retryable
  和时间，不读取 provider identity/digest。Phase 2 不删除旧链仍使用的字段；Task 10 把字段与旧 async writer
  登记为 Phase 3 应用模型删除项，Phase 8 在干净基线中移除数据库列。
- [ ] 每个公开调用先建立一条 STARTED evidence，再执行 HTTP 并原位完成；发送前写入失败则不发送，发送后
  finalization 失败则报告远端结果未知。禁止空/伪造 `evidence_key`。
- [ ] Phase 2 期间保留旧 async evidence 写入函数供唯一活动可靠链使用；Task 10 将它登记为 Phase 3 删除项。
- [ ] breaker 保留 DB 共享状态、OPEN/HALF_OPEN/CLOSED 和 probe fencing；移除对 Runtime observability
  类型的直接依赖，仅接收注入的 callable。
- [ ] 一个公开分页调用只申请一次 breaker permit；所有页面和 retry attempt 共享累计 absolute deadline、
  wire bytes、decoded bytes、页数和行数预算，只完成一条 evidence，并最终更新一次 breaker。

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

- [ ] 用 HTTPX `MockTransport` 逐方法验证 19 项 method/path/query/body、分页、DTO 和四分支 outcome；仅对已冻结合同中真实存在的认证差异增加参数化断言。
- [ ] `HttpWmsGateway` 每个公共方法直接 import 自身 capability 模块的 `WmsCallSpec` 并调用私有 `_call`；
  禁止字符串 operation 参数、中心映射和公共 spec 查询。
- [ ] 列表查询内部消费 cursor，但必须满足 Task 6 的一次 permit、累计预算、一条 evidence 和一次最终 breaker
  更新；空结果是成功。
- [ ] 在通用架构门禁中证明 Q19 caller 只依赖 `WmsDocumentCapabilities` 类型化端口；不把粗分机业务
  Payload、分支或断言写入核心测试。本任务不修改生产 Q19 caller、API/Celery 装配或旧 QUERY 平台。
- [ ] Gateway factory 只接受显式 `WmsConnectionSettings`、HTTP transport、evidence recorder 和 breaker；
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
- Modify: `src/app/wms_integration/factory.py`
- Create: `tests/contracts/wms_integration/test_http_wms_confirmation_sender.py`
- Create: `tests/contracts/wms_integration/test_http_wms_forwarded_transport.py`
- Create: `tests/deployment/test_wms_config_compose_mount.py`
- Create: `tests/deployment/test_wms_process_composition.py`
- Create: `tests/deployment/test_wms_startup_lifecycle.py`
- Create: `tests/architecture/test_wms_phase2_import_closure.py`
- Create: `tests/architecture/test_wms_phase2_file_set.py`
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
- Modify: `src/celery_app/async_runtime.py`
- Modify: `src/app/workline/runtime_services.py`
- Modify: `src/app/runtime/capabilities/material_flow/rough_sorter_q19_admission_service.py`
- Modify: `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_orchestrator_bridge.py`
- Modify: `src/app/runtime/workline_plugins/rough_sorter/pre_attempt.py`
- Modify: `src/app/runtime/system_capabilities/generated_index.py`
- Modify: `scripts/generate_runtime_extensions.py`

**Files — delete after import closure passes:**

- `src/app/wms_integration/query_executor.py`
- `src/app/wms_integration/query_runtime.py`
- `src/app/wms_integration/query_projection.py`
- `src/app/wms_integration/query_response.py`
- `src/app/wms_integration/ports/query_execution.py`
- Task 3 已完成 MOVE 的五个 `*_operations.py` 与 `operation_common.py`
- §2.1 标记为 Phase 2 `DELETE` 的 evidence/models/inbound 旧源文件
- `src/app/runtime/system_capabilities/wms/` 下 19 个 QUERY definition 目录、`query_definition.py` 和
  `query_handler.py`

- [ ] 参数化验证 E01–E07/E15 的 request、`dispatch_key` 幂等头、同步 terminal DTO 和四分支 outcome。
- [ ] ACK、status、terminal、cancel 和 hint 必须回显同一 `dispatch_key`；静态合同与实现均不得出现独立
  `idempotency_key` 字段、alias 或双键转换。
- [ ] 参数化验证 E08–E14 submit ACK、七类 status pending/terminal DTO 以及 E16 cancel；Client 不轮询、
  不 sleep、不 schedule、不写任务状态，且本阶段不接入旧 Effect/status 链。
- [ ] 在同一工作树变更中激活目标 Gateway 唯一读取的 `WMS_CONFIG_FILE`，只修改三个 tracked env profile，再运行
  `./scripts/init-env.sh` 刷新 worktree-local `.env`；不得把 `.env` 加入暂存。
- [ ] Compose/Jenkins 的目标 Gateway 装配只传递新变量；API/Celery 各装配一个 Gateway/AsyncClient，配置缺失
  或错误 fail fast，shutdown 与启动失败均关闭资源。删除旧 QUERY 配置读取，不为目标 loader 保留
  alias/fallback；旧 Effect 链所需冻结配置只在其旧 composition 中保留到 Phase 3，不得被新 Gateway 读取。
- [ ] 将 Q19 caller 机械改为注入 `WmsDocumentCapabilities`；只调整依赖和调用，不优化粗分机业务结构，
  Phase 5 才拥有业务优化。
- [ ] 切换全部 19 项 QUERY caller，包括
  `runtime_inbox_orchestrator_bridge.py` 的静态 `ports.query_execution` importer；删除 query runtime bind/close、
  Runtime Service Locator 和 QUERY System Capability definitions；重新生成/更新全局 index，使其只保留旧
  fulfillment definitions。
- [ ] `scripts/generate_runtime_extensions.py` 只做旧生成器的阶段性收缩，不新增 WMS codegen；Phase 3 删除剩余
  fulfillment definitions 后一并删除 WMS 生成入口。
- [ ] 不修改旧 Effect/status/Outbox 生产实现，不删除其 Provider/Catalog/registry/outcome/evidence 静态依赖，
  不创建 `WmsConfirmation`、`TransportTask`、第二张可靠表或第二个 Celery scanner。
- [ ] 运行静态 import-closure 门禁：Phase 2 删除集不得被任何保留文件 import，generated index 不得 import
  已删除 QUERY definition。
- [ ] 文件集门禁读取 §2.1 的冻结清单：Phase 2 DELETE 源文件必须缺席、PHASE3_HANDOFF 必须仍在、目标垂直
  模块必须精确 35 项，`evidence/` 目录和旧 public re-export 必须缺席。
- [ ] 更新 HEAVY selector 真源和合同测试：`.env.*`、Compose/Jenkins、`src/core/conf.py`、`src/register.py`、
  `configuration.py`、`factory.py` 与 WMS 进程装配精确映射到
  `test_wms_process_composition_postgresql.py`；保留 `src/celery_app/async_runtime.py` 对三项既有 Celery
  runtime HEAVY 的映射，不得用空 `heavy_tests` 或宽泛目录匹配掩盖影响。
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
  tests/deployment/test_wms_process_composition.py \
  tests/deployment/test_wms_startup_lifecycle.py -q
rtk uv run pytest \
  tests/architecture/test_wms_phase2_import_closure.py \
  tests/architecture/test_wms_phase2_file_set.py -q
rtk uv run pytest tests/scripts/test_select_heavy_tests.py -q
rtk uv run scripts/select_heavy_tests.py --scope unstaged
```

Expected: 19 项 QUERY 和唯一配置装配已切换；sender/client 已实现但未接入旧可靠链；旧链及其静态依赖完全
未改写，generated index 只剩 fulfillment definitions，Phase 2 删除集无 dangling import。

**Do not commit yet:** 继续 Task 9。禁止形成生产旧模块已删除、旧测试仍断裂的中间提交。

### Task 9：按逐文件矩阵处置旧测试并收敛重量

新 owner 测试必须先通过，再执行下表的 REWRITE/DELETE；不得按 `provider`、`replay`、`conformance` 等关键词
批量删除。

| 当前测试/测试资产 | 处置 | successor 或 NONE 理由 |
| --- | --- | --- |
| `provider_profile_support.py` | DELETE | → `test_wms_connection_settings.py`；目标配置 fixture 不复用 Provider helper |
| `test_provider_conformance_matrix.py` | DELETE | → `test_wms_capability_conformance.py` |
| `test_provider_conformance_replay_asset.py` | DELETE | → NONE；目标无 replay/conformance 平台 |
| `test_provider_conformance_report.py` | DELETE | → NONE；目标无 conformance report 产物 |
| `test_provider_conformance_runner_cli.py` | DELETE | → NONE；对应 CLI 删除 |
| `test_provider_conformance_suite.py` | DELETE | → `test_wms_capability_conformance.py` + Gateway MockTransport tests |
| `test_wms_effect_capability_index.py` | REWRITE | → `test_wms_phase3_handoff_guardrail.py`；Phase 2 只验证 index 保留 fulfillment 条目 |
| `test_wms_frozen_http_binding_projection.py` | DELETE | → capability module spec + Gateway binding tests |
| `test_wms_compiled_profile_active_truth.py` | DELETE | → connection settings + process composition tests |
| `test_wms_provider_digest_readiness.py` | DELETE | → startup lifecycle tests；目标无 provider digest |
| `test_wms_provider_endpoint_compiler.py` | REWRITE | PHASE3_HANDOFF；只保留旧 status 链所需回归，Phase 3 随旧链删除 |
| `test_wms_provider_profile.py` | REWRITE | PHASE3_HANDOFF；只保护冻结旧 Effect 配置，不增加能力 |
| `test_wms_query_projection.py` | DELETE | → `test_http_wms_capabilities.py` |
| `test_wms_transport_runtime_configuration.py` | REWRITE | PHASE3_HANDOFF；旧搬运链保持原行为，Phase 3 由 TransportTask 测试承接 |
| `test_wms_provider_conformance_boundaries.py` | DELETE | → `test_wms_thin_public_boundary.py` |
| `test_wms_provider_replay_boundaries.py` | DELETE | → NONE；目标无 replay 平台 |
| `test_wms_shared_effect_pipeline_guardrail.py` | REWRITE | → `test_wms_phase3_handoff_guardrail.py`；保护旧可靠链至原子交接 |
| `test_wms_deployment_attestation_gate.py` | DELETE | → NONE；目标无部署鉴证 gate |
| `test_wms_deployment_attestation_runner.py` | DELETE | → NONE；目标无部署鉴证 runner |
| `test_wms_effect_lane_dispatch.py` | REWRITE | PHASE3_HANDOFF；旧 lane 不扩展，Phase 3 由最终可靠对象测试承接 |
| `test_wms_provider_profile_compose_mount.py` | REWRITE | PHASE3_HANDOFF；新 QUERY 装配由 `test_wms_config_compose_mount.py` 承接 |
| `test_wms_provider_profile_startup.py` | REWRITE | PHASE3_HANDOFF；新 QUERY 启动由 `test_wms_startup_lifecycle.py` 承接 |
| `test_wms_transport_startup.py` | REWRITE | PHASE3_HANDOFF；Phase 3 由 TransportTask composition tests 承接 |
| `test_wms_deployment_attestation.py` | DELETE | → NONE；旧部署鉴证平台删除 |
| `test_wms_northbound_feasibility_probe.py` | DELETE | → NONE；不以真实 HTTP probe 代替目标合同测试 |
| `test_wms_provider_conformance_collection.py` | DELETE | → NONE；目标无运行时 conformance collection |
| `test_wms_provider_conformance_simulator.py` | DELETE | → `test_wms_capability_conformance.py` + MockTransport tests |
| `test_wms_operation_catalog.py` | REWRITE | wire 断言迁入 capability harness；旧 fulfillment 断言留给 Phase 3 handoff |
| `test_effect_status_contract.py` | REWRITE | PHASE3_HANDOFF；最终由 TransportTask/WmsConfirmation tests 承接后删除 |
| `wms_operation_fixtures.py` | REWRITE | 只机械改为垂直 DTO import；Phase 4 决定 CORE_REWRITE/PLUGIN_OWNED |
| `runtime_inbox_processing_postgresql.py` | REWRITE | Phase 4 在 `InboundEvidence` 上建立 successor 后删除 |
| `test_runtime_inbox_processing_postgresql.py` | REWRITE | Phase 4 在 `InboundEvidence` HEAVY test 上建立 successor 后删除 |

**Delete support assets after all referencing tests are handled:**

- `tests/fixtures/wms_provider_conformance/query_inventory_replay.v1.json`
- `tests/support/wms_conformance_coverage.py`
- `tests/support/wms_conformance_runner.py`
- `tests/support/wms_provider_conformance.py`
- `tests/support/wms_provider_replay.py`
- `scripts/check_wms_deployment_attestation.py`
- `scripts/run_wms_conformance.py`
- `scripts/run_wms_deployment_attestation.sh`
- `scripts/verify_wms_northbound_feasibility.py`
- `docker-compose.wms-acceptance.yml`

- [ ] FAST 只保留垂直 DTO/spec、显式端口、共享 mapper、MockTransport、装配和架构边界；不运行 Docker、
  子进程、真实 HTTP、真实 WMS 或全量 conformance matrix。
- [ ] HEAVY 只保留 PostgreSQL breaker 竞争、旧 status claim/fencing 和必要故障注入；每个移动/删除路径同步
  更新 `docs/architecture/heavy-test-impact.toml` 与 selector contract，不能留下失效 mapping。
- [ ] 对所有 DELETE 执行反向引用扫描；successor 路径必须先通过，NONE 理由必须进入提交说明或 PR 描述。

**Run:**

```bash
rtk uv run pytest tests/contracts/wms_integration tests/wms_integration tests/architecture tests/deployment -q
rtk uv run pytest tests/scripts -q
rtk uv run pytest --collect-only -q -o addopts=''
rtk ./scripts/git-quality-gate.sh --profile quality
rtk uv run scripts/select_heavy_tests.py --scope unstaged
```

Expected: 新 WMS FAST 子集只验证目标合同；全仓收集无 dangling import；FAST 仍低于 60 秒总预算；
PHASE3_HANDOFF 测试继续保护未改写的旧可靠链。

**Commit boundary:** Task 8/9 同一提交，只暂存两项任务 `Files` 和矩阵明确列出的精确路径，禁止
`git add -A` 或目录级暂存。精确暂存后必须运行 `rtk uv run scripts/select_heavy_tests.py --scope staged`，并实际
执行 selector 输出的全部 HEAVY 测试；两者通过后才能提交。提交说明：
`refactor(wms): 原子切换 WMS 查询薄边界`。

### Task 10：冻结 Phase 3 原子删除清单

**Files:**

- Create: `tests/architecture/test_wms_phase3_handoff_guardrail.py`
- Modify: `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- Modify: `docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md`

Phase 3 必须在最终对象权威测试通过后删除以下旧可靠性所有者：

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
- `src/celery_app/tasks/workline.py` 中 `check_wms_effect_status` 与 `scan_wms_effect_status_batch`
- `src/celery_app/config.py` 中两项旧 status task route/beat 配置
- `src/app/wms_integration/services/evidence_service.py` 中 `record_async_summary`，以及 model/repository/service
  中只服务旧链的 provider identity/digest 字段
- §2.1 全部 `PHASE3_HANDOFF` 文件，包括 `operation_contract.py`、`operation_registry.py`、
  `endpoint_compiler.py`、`provider_*`、`query_evidence.py` 和 `ports/query_outcome.py`
- `src/app/runtime/system_capabilities/wms/` 中 Phase 2 保留的 fulfillment definitions、effect runtime、
  provider catalog、contracts、conformance 和生成资产
- `src/app/runtime/system_capabilities/generated_index.py` 中剩余 WMS 条目，以及只为旧 WMS capability 服务的
  `scripts/generate_runtime_extensions.py` import/生成分支

- [ ] guardrail 断言上述资产在 Phase 2 存在且目标公共模块不 import 它们；同时静态分析所有保留生产文件，
  任一保留文件 import Phase 3 删除集即失败。这不是永久 allowlist。
- [ ] 在 guardrail 顶部写明删除条件：`WmsConfirmation` 与 `TransportTask` 生产路径、权威测试、
  crash/retry/fencing 测试全部通过。
- [ ] Phase 3 删除这些资产时必须同时删除 guardrail 本身，不把“待删测试”留到 Phase 7。
- [ ] 删除 fulfillment definitions 前先切换全部调用者；重新生成/改写全局 index 后，所有保留消费者均不得
  观察到 WMS capability 条目，最终删除整个 `system_capabilities/wms/` 目录。
- [ ] 将五个受保护混合测试继续指向 Phase 4 的核心可靠性承接，不提前宣称测试计划完成。
- [ ] 逐项核对 §1.6 的八类 AGV/CTU 交接资产，确保 Phase 2 fake 和 typed client 可由 Phase 3
  `TransportTask` 测试直接消费，不依赖旧 Runtime 装配。

**Run:**

```bash
rtk uv run pytest tests/architecture/test_wms_phase3_handoff_guardrail.py -q
rtk ./scripts/markdownlint.sh \
  docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md \
  docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md
```

Expected: Phase 2 无可靠性空窗，Phase 3 删除入口和受保护测试归属明确。

**Commit boundary:** 只暂存本任务 `Files` 的精确路径。提交说明：
`test(wms): 锁定 Phase 3 可靠性原子交接`。

### Task 11：Phase 2 全量验收

- [ ] 公共边界和配置零旧架构 import/名称。
- [ ] 19 查询、8 确认、7 submit/status 与 1 cancel 的类型化合同全部通过。
- [ ] 35 个垂直模块均通过测试态 conformance；生产运行时不存在中心 registry、动态发现或 WMS codegen。
- [ ] API/Celery 各一个进程级 Gateway/AsyncClient，分页复用连接池；启动失败和 shutdown 都关闭资源。
- [ ] evidence 发送前/后失败、远端结果未知、原 `dispatch_key` 恢复和非空 `evidence_key` 语义全部通过。
- [ ] 旧可靠链保持未改写且仍是唯一活动所有者；没有第二张义务表、第二个 scanner、双写或 fallback。
- [ ] QUERY definitions 已从 generated index 移除，fulfillment definitions 明确留在 Phase 3 handoff；无悬空 import。
- [ ] Phase 2 文件集与 54 文件矩阵完全一致，`evidence/` 双 owner、旧 public export 和未登记生产文件均为零。
- [ ] 受保护混合测试未删除，Phase 3/4 承接标记一致。
- [ ] FAST、QUALITY 和受影响 HEAVY 通过；只因本阶段变更运行相关 HEAVY，不扩张默认套件。
- [ ] Task 8/9 提交前的 unstaged/staged selector 已命中并实际运行全部相关 HEAVY；本任务再使用 Phase 2
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
rtk uv run scripts/select_heavy_tests.py --base cf2f1f91
rtk uv run pytest \
  tests/integration/test_celery_async_runtime.py \
  tests/integration/test_celery_async_runtime_postgresql.py \
  tests/integration/test_celery_prefork_harness_cleanup.py \
  tests/integration/test_wms_process_composition_postgresql.py \
  tests/resilience/test_wms_circuit_breaker.py \
  tests/integration/workline_capabilities/test_wms_effect_status_postgresql.py -q
rtk git diff --check
```

Expected: 所有命令退出 0；FAST 预算不退化；PostgreSQL 可靠性测试证明 Phase 3 接管前没有状态语义缺口。

**Final absence checks:**

```bash
! rtk rg -n \
  'WMS_PROVIDER_PROFILE|WMS_PROVIDER_PROCESS_ROLE|src\.app\.(runtime|sys)|operation_registry|operation_contract|provider_|effect_runtime|effect_status' \
  src/app/wms_integration/capabilities \
  src/app/wms_integration/ports/capabilities.py \
  src/app/wms_integration/ports/confirmation.py \
  src/app/wms_integration/ports/forwarded_transport.py \
  src/app/wms_integration/configuration.py \
  src/app/wms_integration/factory.py
```

**Commit boundary:** 只暂存 Task 11 验收修正涉及的精确路径，禁止 `git add -A`。提交说明：
`refactor(wms): 完成 WMS 薄接入边界收敛`。

---

## 5. 测试语义与重量预算

| 层级 | 保留的完整断言 | 不进入该层 |
| --- | --- | --- |
| Contract FAST | 垂直 DTO/spec、测试态 conformance、合同明确要求的可选认证、四分支 outcome、端口形状 | 具体插件流程、真实 WMS |
| Adapter FAST | 单次 HTTP、分页、预算、错误映射、脱敏证据 | Docker、Celery、真实网络 |
| Deployment FAST | `WMS_CONFIG_FILE`、API/Celery 单 Gateway/Client、fail-fast、资源关闭 | 真实 WMS、旧配置 fallback |
| Architecture FAST | import closure、公共 API、QUERY index 收缩、Phase 3 handoff | 业务状态机重复断言 |
| Persistence HEAVY | breaker 竞争、status claim/fencing、证据事务 | 35 项重复全链路矩阵 |
| Phase 3/4 | `TransportTask`、`WmsConfirmation` crash/retry/terminal | 旧 Runtime/Effect 所有者 |

FAST 中 35 项操作只用一个参数表覆盖 method/path/DTO，不为每项复制完整网络场景。错误分支只在共享
response mapper 完整覆盖；各端口方法只验证自己的新增绑定，严格遵守“一条行为最低稳定层完整断言”。

---

## 6. 自评审

### 6.1 SPEC 覆盖

| SPEC 要求 | 本计划承接 |
| --- | --- |
| WMS 是单据、库存、主数据和授权权威 | 19 项 query DTO/port 保留，WES 不复制权威状态 |
| 插件只依赖 `WmsCapabilities` | Task 4/7 的显式 Protocol、注入和 QUERY 平台删除 |
| 物理完成后形成可靠 WMS 确认 | Task 8 只提供未接入 sender；Phase 3 原子交接创建义务 |
| WMS 转发 AGV/CTU 仍经 Transport Port | E08–E14/E16 独立 Client，生命周期明确交给 Phase 3 |
| 不保留 RuntimeIntent/Generic Effect/System Capability 热路径 | Phase 2 删除 QUERY definitions；旧 fulfillment 闭包由 Task 10 锁定到 Phase 3 删除 |
| 不兼容旧配置/旧数据 | 单一 `WMS_CONFIG_FILE`，无 alias/fallback；不写 migration 转换 |
| 测试按语义和重量收敛 | Task 9 和 §5，不用全量 conformance/Docker 证明静态合同 |

### 6.2 类型一致性

- Query、confirmation、transport 的正常远端分支使用相同 `WmsCallOutcome[T]`；本地配置/evidence 故障使用
  明确基础设施错误，不伪装为远端 outcome。
- `dispatch_key` 只表示 WMS 幂等身份；Phase 2 Client 不把它解释成生命周期状态。
- E08–E14 的 pending/terminal 类型只由 transport Client 返回；`WmsConfirmationSender` 不可见。
- E16 按领域归 Transport，不因同步 HTTP completion 被误放入 confirmation。
- 每个 capability 模块的 `WmsCallSpec` 是静态 wire 事实，不构成中心 Catalog 或运行时发现 API。

### 6.3 DRY/KISS/SOLID/YAGNI

- 一个 strict wire base、一个 outcome union、一个私有 HTTP pipeline 和每进程一个 AsyncClient，避免
  query/effect 两套实现及分页连接池抖动。
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

结论为 **通过，17 项 Eng Review 决策及第二轮 9 项审计结论已落实且无未决选择**。关键门禁包括：Phase 2 不改写旧可靠链；能力
垂直内聚且生产无 registry；54 文件与旧测试均有逐项处置；QUERY/fulfillment System Capability 分阶段收缩；
evidence 远端未知、单一 `dispatch_key`、分页累计预算、进程级 AsyncClient、部署生命周期、测试所有权与
HEAVY selector 闭环都有明确测试。

可以按 Task 1–11 顺序实施。Phase 2 完成态不是最终系统完成态，不得单独合并回 `develop`；必须继续同一
架构收敛分支进入 Phase 3，完成可靠所有者与静态依赖闭包的原子删除。
