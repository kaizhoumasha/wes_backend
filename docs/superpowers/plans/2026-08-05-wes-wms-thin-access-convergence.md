# WES Phase 3 WMS Adapter 新能力重建 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Status:** `ENTRY_BLOCKED_BY_WMS_CONTRACT`。Decision A 已确定初稿覆盖 16 项的 wire 基线，并批准 E02 使用 `POST`；
> 出库顶层设计已删除旧 Q10–Q13，并新增 E17/E18；Task 2–12 在 33 项完整字段矩阵、其余 17 项裁决，以及
> E08–E14 status method/path、状态闭集、关联键和幂等承诺全部批准前
> 不得启动。

**Goal:** 消费 Phase 2 `OutboundHttpTransport`，在独立应用包 `src/app/wms_adapter/` 中暗构建 33 项 WMS 类型化能力及其测试；不接入当前生产路径，
不迁移、不改写、不删除旧 WMS 能力。

**Architecture:** 新能力位于 `src/app/wms_adapter/`，按 `master_data`、`document`、`inventory`、
`reconciliation`、`fulfillment` 五个业务域组织，源码文件只使用语义名称。`HttpWmsGateway` 只依赖 Phase 2
`OutboundHttpTransport` 和新建的 `WmsCallControl`；后者以短事务实现发送前 evidence fail-closed、breaker permit 与发送后
outcome/evidence/breaker 更新。三条公开 Protocol 不暴露 HTTP、数据库、Gateway 或通用 `call`。Phase 3 不修改旧
`src/app/wms_integration/`、现有 Composition Root、Settings、部署配置和旧测试；新旧源码虽然共存，但生产流量仍只有旧路径。

**Tech Stack:** Python 3.13、Pydantic 2、Phase 2 `src/core/outbound_http/`、Pytest 9、Ruff、BasedPyright、
Import Linter、GitNexus。

## Global Constraints

- Phase 3 必须消费 Phase 2 `OutboundHttpTransport`/builder，不得创建或注入裸 `httpx` Client。
- 当前 WMS outbound 认证合同只有 `NONE`；不实现 HMAC，不提供 auth enum、credential、认证 Header 配置/动态注入或未来
  认证 seam。JSON 等 wire 必需的固定协议 Header 由对应 operation 的 `WmsCallSpec` 持有，不进入配置。
- Phase 3 只暗构建新能力和新能力测试；旧能力迁移、生产接线、旧代码/配置/测试/数据删除统一留给 Phase 5。
- 架构基础能力与 WMS 业务能力分别验收：Phase 2 测试不证明 WMS 合同，Phase 3 测试不复制 Phase 2 内部行为。
- 未发布系统不保留兼容路径、不迁移旧数据；实现遵循 DRY、KISS、SOLID、YAGNI，以当前真实合同为上限。
- 纯文档任务只做文档校验；实施阶段的代码行为使用 TDD，并遵守仓库测试所有权和 HEAVY 边界。
- 每个新增或修改 HEAVY 候选生产路径的 Task 都必须在同一 Task 更新精确 selector mapping；持久化影响路径
  映射到权威 HEAVY，经评审只有 FAST/QUALITY 影响的路径显式标记 NONE，不得用整包宽泛 mapping 代替逐责任边界。
- 33 项 identity/completion owner 暂按当前 WMS 北向合同保留；初稿覆盖的 16 项采用其 path/业务字段和当前批准 method，
  其中 E02 为 `POST`；其余 17 项仍待 WMS/业务方补齐。初稿中的 `page/page_size` 只是一份响应样例，不引入分页合同。
- Phase 2 `base_url` 只保存 HTTP origin，operation 模块持有完整 `/api/wms` 或 `/api/MCS` path；不配置公共 prefix。

---

## 1. 阶段裁决

### 1.1 Phase 3 只重建新能力

- 新建最终命名的 `src/app/wms_adapter/`，不在旧 `src/app/wms_integration/` 内增量改造。
- 新建 33 个语义化垂直模块；Q/E 编号只保留在合同追踪表和测试 case id，不进入文件名、类名或公开方法名。
- 新建三条类型化窄端口：`WmsCapabilities`、`WmsConfirmationSender`、`WmsForwardedTransportClient`。
- 新建行为无状态的 `HttpWmsGateway` 和 `build_wms_adapter(...)`；factory 必须调用 Phase 2
  `build_outbound_http_transport(system_id="wms", ...)`。
- 新建 WMS outbound call evidence 与 DB-backed circuit breaker；所有正常远端 outcome 携带真实 `evidence_key`，网络调用
  期间不持有数据库事务或行锁。
- 每个公开方法固定只调用一次 Phase 2 `send`。列表结果可以包含有界 `items`，但请求和结果不定义 `cursor`、
  `page_size`、`next_cursor`、跨页一致性或累计分页预算。
- 当前 outbound 认证合同仅为 `NONE`。新包不提供 auth 字段、认证枚举、credential、认证 Header 配置/动态注入、HMAC
  实现或未来 seam；固定协议 Header 只允许来自已批准的 operation wire 合同。
- 新 Adapter 不注册到 `src/register.py`、Celery、FastAPI、WorkLine runtime 或任何当前生产 Composition Root。
- 本阶段只修改新能力、本计划明确列出的当前合同文档和新能力测试资产。

### 1.2 “源码共存”不是“运行时双轨”

Phase 3 退出时旧实现仍是唯一活动生产路径，新 Adapter 只被自身测试导入。暗构建门禁必须同时证明：

1. 除新包自身外，当前全部生产 `src/` 代码没有以绝对或相对形式 import `src.app.wms_adapter`；
   `migrations/env.py` 只允许为 Alembic metadata discovery 导入新 model，不构成生产装配。
2. 新包没有 import `src.app.wms_integration` 或任何旧 Provider/Profile/Registry。
3. 不存在环境变量、配置键、runtime switch 或路由逻辑可以在新旧实现之间选择。
4. 不存在 shadow request、shadow write 或新旧结果比较任务。

全仓旧 Provider、旧认证、旧 Transport 和旧测试的删除门禁属于 Phase 5，不属于 Phase 3。

## 2. What already exists

- Phase 2 已提供 framework-neutral 的 `OutboundHttpRequest`、`OutboundHttpResult`、`OutboundHttpTransport`、
  `OutboundHttpResponseLimits` 与 `build_outbound_http_transport(...)`；Phase 3 直接复用这些公开合同。
- Phase 2 已拥有长期 Client、单次发送、总超时、响应解码/资源上限和传输失败事实；Phase 3 不复制这些机制。
- 旧 `src/app/wms_integration/` 已存在 Provider/Profile、分页执行器、evidence 和 breaker。它们只用于识别 Phase 5 删除闭包，
  不作为新实现 import、继承、复制或测试 oracle。
- 当前生产 Composition Root 仍只装配旧路径；新 `src/app/wms_adapter/` 尚不存在，也没有实际生产消费者。
- `OutboundHttpResult` 不提供 wire byte 计数。由于 Phase 3 不实现多页聚合，既不需要也不得为此扩展 Phase 2 合同。
- `docs/hardware/wms_rcs_interface_requirements.md` 是 2026-03 的 WMS 交互约定初稿，可作为业务输入，但不是当前架构真源。
  它概念性覆盖 Q01–Q10 和 E01/E02/E05/E07/E08/E09，仍缺其余 17 项 operation 的完整 wire 定义。
- Decision A 已裁决：初稿覆盖 16 项的 path/业务字段与当前批准 method 进入 wire 合同，E02 method 固定为 `POST`；旧
  `MCS`/`WorklineInbox` 架构、Bearer Header、自动重试、缓存和 `page/page_size` 分页语义被剔除。
- Phase 2 builder 已要求 `base_url` 只含 origin，因此每个 operation path 必须持有完整 API prefix。
- 当前合同批准 E02 使用 `POST`，Phase 2 既有 `GET`/`POST` method 合同已满足 Phase 3，不新增 `DELETE` 或其他 method。
- 当前 WMS 北向合同仍没有逐项完整 request/result 字段矩阵；初稿也未覆盖其余 17 项，E08–E14 status、状态闭集、
  `request_id`/`task_id` 关联和幂等承诺同样未冻结。Task 1 必须补齐并取得业务/WMS 批准，不得从旧实现猜测。

## 3. NOT in scope

- 不修改、移动、重导出或删除 `src/app/wms_integration/` 的任何文件。
- 不修改旧 Provider/Profile/Registry、QUERY runtime、Effect/status/Outbox、callback ingress、旧 evidence/breaker 或旧配置。
- 不修改现有 WMS 测试，不以旧测试或旧实现输出作为新能力正确性的 oracle。
- 不修改 `.env*`、Settings、Compose、Jenkins、Celery beat/route、Runbook 或当前部署启动逻辑。
- 不新建 `TransportTask`、`WmsConfirmation`、可靠对象表、scanner、retry、dispatch/claim fencing、terminal/recovery 或
  业务插件 Decision；breaker 自身防止过期 HALF_OPEN 结果污染的 `probe_generation` 不属于可靠对象生命周期。
- 不迁移旧代码或旧数据，不建立 shim、alias、fallback、shadow write、双写、双读或选择新旧路径的 feature flag。
- 不实现动态 registry、生产 conformance runtime、codegen 或生产 fake；测试态能力覆盖检查属于新 Adapter 测试。
- 不实现 cursor/page-number 分页、自动续页、跨页 `source_version` 校验、累计页数/行数/bytes 预算或分页扩展 seam。
- 不轮询 E08–E14 status，不自动 retry，不吞掉 `CancelledError`，不为未来厂商合同预留可配置策略。

## 4. 目标文件结构

WMS 北向 Adapter 是产品内唯一业务系统 ACL，不是 `device_adapters/<adapter_key>/` 下的设备厂商二次开发包。
因此其跨系统 FAST 合同由 `tests/contracts/wms_adapter/` 拥有，真实持久化与事务场景由
`tests/integration/wms_adapter/` 拥有；这些测试只证明 WMS Adapter，不得反向替代 Phase 2 基础传输或 Phase 4 核心能力测试。

```text
src/app/wms_adapter/
├── __init__.py                  # 只导出三条 Protocol、配置、outcome、factory
├── config.py                    # WmsAdapterConfig：base_url + timeout_seconds
├── outcomes.py                 # 封闭 WMS 调用结果，不含可靠生命周期
├── ports.py                     # 三条公开 Protocol
├── gateway.py                   # HttpWmsGateway；唯一私有发送/解析路径
├── call_control.py              # Gateway 使用的内部 permit/evidence/breaker Protocol
├── factory.py                   # 消费 Phase 2 builder + session factory；固定 system_id=wms
├── _shared.py                   # 严格 DTO 基类、WmsCallSpec、编码/解析小工具
├── models/
│   ├── __init__.py
│   ├── call_evidence.py         # 新表 wms_outbound_call_evidence
│   └── circuit_breaker.py       # 新表 wms_outbound_circuit_breaker
├── repositories/
│   ├── __init__.py
│   ├── call_evidence_repository.py
│   └── circuit_breaker_repository.py
├── services/
│   ├── __init__.py
│   └── call_control_service.py  # 两段短事务协调；网络调用不在事务内
├── master_data/
│   ├── __init__.py
│   ├── get_material.py
│   ├── get_materials.py
│   ├── list_zones.py
│   ├── list_locations.py
│   ├── get_rack.py
│   ├── list_racks.py
│   └── get_bin.py
├── document/
│   ├── __init__.py
│   ├── get_grn.py
│   ├── list_grn_packages.py
│   └── validate_rough_sorter_admission.py
├── inventory/
│   ├── __init__.py
│   ├── query_inventory.py
│   ├── get_reservation.py
│   ├── reserve_inventory.py
│   ├── release_reservation.py
│   ├── confirm_inbound.py
│   ├── confirm_outbound.py
│   ├── transfer_inventory.py
│   └── confirm_return_putaway.py
├── reconciliation/
│   ├── __init__.py
│   ├── check_bin_drift.py
│   ├── check_rack_drift.py
│   └── check_full_drift.py
└── fulfillment/
    ├── __init__.py
    ├── notify_pkg_binding.py
    ├── request_rack_supply.py
    ├── request_rack_transport.py
    ├── change_rack_face.py
    ├── full_box_exchange.py
    ├── move_bins_to_conveyor_entry.py
    ├── move_bins_from_conveyor_exit.py
    ├── request_load_unit_transport.py
    ├── publish_manual_task.py
    ├── cancel_request.py
    ├── report_picking_source_ng.py
    └── confirm_picking_completed.py

tests/contracts/wms_adapter/
├── __init__.py
├── support.py                   # 仅测试使用的 LocalOutboundHttpTransport
├── test_config_and_outcomes.py
├── test_ports.py
├── test_master_data.py
├── test_document.py
├── test_inventory.py
├── test_reconciliation.py
├── test_fulfillment.py
├── test_gateway_outcomes.py
├── test_call_control_contract.py
└── test_factory.py

tests/architecture/
└── test_wms_adapter_dark_build_guardrail.py

tests/integration/wms_adapter/
├── __init__.py
└── test_call_control_persistence.py
```

Alembic 生成两个最终语义新表；表名不复用旧表，避免暗构建期间 SQLModel metadata 冲突。Phase 5 删除旧表，Phase 10
生成干净 baseline。不创建 `mock_adapter/`、`xxx_adapter/`、编号文件、生产 `fakes.py` 或公开 operation registry。外部
Mock WMS 仍属于现有 HEAVY/E2E 资产，Phase 3 不修改。

## 5. 33 项目标映射与合同成熟度

| 追踪 | Domain | 语义文件 | 公开方法 |
| --- | --- | --- | --- |
| Q01 | master_data | `get_material.py` | `get_material` |
| Q02 | master_data | `get_materials.py` | `get_materials` |
| Q03 | master_data | `list_zones.py` | `list_zones` |
| Q04 | master_data | `list_locations.py` | `list_locations` |
| Q05 | master_data | `get_rack.py` | `get_rack` |
| Q06 | master_data | `list_racks.py` | `list_racks` |
| Q07 | master_data | `get_bin.py` | `get_bin` |
| Q08 | document | `get_grn.py` | `get_grn` |
| Q09 | document | `list_grn_packages.py` | `list_grn_packages` |
| Q10 | inventory | `query_inventory.py` | `query_inventory` |
| Q11 | inventory | `get_reservation.py` | `get_reservation` |
| Q12 | reconciliation | `check_bin_drift.py` | `check_bin_drift` |
| Q13 | reconciliation | `check_rack_drift.py` | `check_rack_drift` |
| Q14 | reconciliation | `check_full_drift.py` | `check_full_drift` |
| Q15 | document | `validate_rough_sorter_admission.py` | `validate_rough_sorter_admission` |
| E01 | inventory | `reserve_inventory.py` | `reserve_inventory` |
| E02 | inventory | `release_reservation.py` | `release_reservation` |
| E03 | inventory | `confirm_inbound.py` | `confirm_inbound` |
| E04 | inventory | `confirm_outbound.py` | `confirm_outbound` |
| E05 | inventory | `transfer_inventory.py` | `transfer_inventory` |
| E06 | inventory | `confirm_return_putaway.py` | `confirm_return_putaway` |
| E07 | fulfillment | `notify_pkg_binding.py` | `notify_pkg_binding` |
| E08 | fulfillment | `request_rack_supply.py` | `request_rack_supply` + `get_rack_supply_status` |
| E09 | fulfillment | `request_rack_transport.py` | `request_rack_transport` + `get_rack_transport_status` |
| E10 | fulfillment | `change_rack_face.py` | `change_rack_face` + `get_rack_face_change_status` |
| E11 | fulfillment | `full_box_exchange.py` | `full_box_exchange` + `get_full_box_exchange_status` |
| E12 | fulfillment | `move_bins_to_conveyor_entry.py` | `move_bins_to_conveyor_entry` + `get_conveyor_entry_batch_status` |
| E13 | fulfillment | `move_bins_from_conveyor_exit.py` | `move_bins_from_conveyor_exit` + `get_conveyor_exit_batch_status` |
| E14 | fulfillment | `request_load_unit_transport.py` | `request_load_unit_transport` + `get_load_unit_transport_status` |
| E15 | fulfillment | `publish_manual_task.py` | `publish_manual_task` |
| E16 | fulfillment | `cancel_request.py` | `cancel_request` |
| E17 | fulfillment | `report_picking_source_ng.py` | `report_picking_source_ng` |
| E18 | fulfillment | `confirm_picking_completed.py` | `confirm_picking_completed` |

Q02 固定为按 `ids` 批量获取物料，不是无条件通用 list。E08/E09 submit wire 分别采用初稿业务字段，唯一关联字段名为
`request_id`；WES 内部 `dispatch_key` 可映射成该值，但不得同时发送多套别名。初稿只返回 `task_id`，没有定义 E08–E14
status endpoint、状态闭集或关联规则；`GET /api/wms/operations/status` 仍是待批准提案。Q15 不包含内部 Session id
或执行对象数据库主键。

## 6. 公共合同

### 6.1 配置

`WmsAdapterConfig` 只允许：

```text
base_url: str
timeout_seconds: float
```

配置对象不读取全局 Settings，不接受 Provider profile、auth scheme、credential reference、secret、认证 Header、动态
Header、role、lane、simulation 或 retry 参数。合法性由 Phase 2 builder 继续验证；WMS 层只保证字段类型和不可变性。

### 6.2 三条 Protocol

- `WmsCapabilities`：Q01–Q15 的 15 个显式 async 方法；列表方法只返回单次响应中的有界 `items`。
- `WmsConfirmationSender`：E01–E07/E15/E17–E18 的 10 个显式 async 方法；每次调用只发送一次。
- `WmsForwardedTransportClient`：E08–E14 的 submit/status 与 E16 cancel 共 15 个显式 async 方法；不轮询。

三个 Protocol 不继承彼此，不暴露 `HttpWmsGateway`、`OutboundHttpRequest`、HTTP status、字符串 operation selector、
registry 或 generic `call`。`HttpWmsGateway` 可以同时实现三个 Protocol，消费者只注入所需窄接口。

### 6.3 `WmsCallSpec`

不可变 `WmsCallSpec` 只包含：

```text
identity
method
path_template
request_location = PATH | QUERY | JSON
request_model
result_model
business_reject_codes
fixed_headers
```

`fixed_headers` 只保存该 operation 已批准且不可变的非认证 wire Header；无固定 Header 时为空。不得包含 Provider、lane、
completion owner、retry、timeout、auth、credential、callback、动态 Header/handler 或 lifecycle 状态。

### 6.4 封闭 outcome

`WmsCallOutcome[T]` 只表达一次 WMS 调用的稳定事实：成功结果、业务拒绝、breaker 拒绝、请求未发送、交付状态未知、
响应无效或 evidence 最终写入失败。它不得决定 retry、terminal、dependency pause 或对象状态迁移。Phase 2 的
failure/delivery fact 必须被完整保留到对应 WMS outcome，不得用一个宽泛异常吞掉 `NOT_SENT` 与
`DELIVERY_UNKNOWN` 的差异。所有已发送或收到远端响应的 outcome 都携带发送前已创建的真实 `evidence_key`。

### 6.5 生命周期

`build_wms_adapter(config, session_factory)` 固定调用 Phase 2 builder，构造 `WmsCallControl` 并返回具体
`HttpWmsGateway`。Gateway 持有唯一 Transport，提供 `aclose()` 并只委托一次；三条业务 Protocol 不暴露生命周期方法。
Phase 5 Composition Root 负责传入进程级 session factory、保留具体 Gateway 并关闭，Phase 3 只验证该合同，不接入生产
启动/关闭流程。

### 6.6 Evidence 与 breaker 事务边界

一次公开调用固定为：

```text
短事务 A：申请 breaker permit
    ├─ BLOCKED：写入 BLOCKED evidence → commit → 不发送
    └─ ALLOWED：写入 STARTED evidence + probe_generation → commit
          ↓
Phase 2 Transport：恰好一次 send；网络期间无数据库事务/锁
          ↓
短事务 B：完成 evidence + 按同一 generation 更新 breaker → commit
```

- 短事务 A 失败：fail closed，不发送 HTTP。
- breaker 拒绝：短事务 A 直接创建终态 `BLOCKED` evidence，不创建 `STARTED`，不进入短事务 B，并返回 retry-after fact。
- HTTP 完成后短事务 B 失败：返回 `REMOTE_RESULT_UNKNOWN`，保留真实 `evidence_key` 和 STARTED 记录，禁止伪造成功。
- `CancelledError` 原样传播；已提交的 STARTED evidence 保持不变，明确表示结果未知。本阶段不建 scanner/recovery。
- HALF_OPEN 结果必须携带 `probe_generation`，过期或旧代次结果不得污染当前 probe。
- 网络调用期间不得持有数据库事务、row lock 或 advisory lock。
- Evidence 只保存 operation identity、关联键、脱敏有界快照/hash、HTTP/传输事实和 outcome 摘要；不得含 Provider identity、
  profile digest、auth/header/credential 或异步 Outbox/Callback payload 副本。
- Evidence 状态闭集为 `STARTED | SUCCEEDED | BUSINESS_REJECTED | FAILED | BLOCKED`；不承载 retryability 或可靠对象生命周期。
- Breaker key 固定为 `operation_identity`，参数固定为连续失败 3 次、OPEN 60 秒、HALF_OPEN 单 probe 成功后关闭；
  probe lease 固定 60 秒，超时后由下一次 begin 递增 generation 重新领取，避免取消或进程退出永久卡死；这些参数不进入配置。

### 6.7 Outcome 与 breaker 映射

| 输入事实 | WMS outcome | Evidence 终态 | Breaker | 关键约束 |
| --- | --- | --- | --- | --- |
| 2xx + 有效 DTO | success | `SUCCEEDED` | success | 返回 typed result 与真实 `evidence_key` |
| 合同闭集业务拒绝 | business reject | `BUSINESS_REJECTED` | success | 证明依赖可达，不触发熔断 |
| 除合同闭集业务拒绝外的全部非 2xx（含 3xx、意外 4xx、429、5xx） | dependency/remote failure | `FAILED` | failure | 3xx 不跟随；429 保留 `Retry-After` 事实，不自动 retry |
| JSON/DTO 无效、响应超限 | contract/response failure | `FAILED` | failure | 资源上限由 Phase 2 单响应合同拥有 |
| Phase 2 `NOT_SENT` | request not sent | `FAILED` | failure | 不把未发送伪装成未知交付 |
| Phase 2 `DELIVERY_UNKNOWN` | delivery unknown | `FAILED` | failure | 完整保留 Phase 2 事实 |
| breaker 拒绝 | blocked | `BLOCKED` | 不变 | 零 HTTP send |
| begin 持久化失败 | evidence unavailable | 无记录或事务回滚 | 不变 | fail closed，零 HTTP send，不伪造 key |
| finish 持久化失败 | `REMOTE_RESULT_UNKNOWN` | 保持 `STARTED` | 不确认更新 | 保留真实 key，不返回普通成功 |
| `CancelledError` | 原样传播 | 保持 `STARTED` | 不确认更新 | 不转为普通失败；Phase 3 不恢复 |

非预期编程错误原样传播，不压缩进业务 outcome。每个公开方法只执行一次 Phase 2 `send`，不重试、不自动续页、不轮询。

## 7. 测试、失败与性能设计

### 7.1 测试所有权

```text
Phase 2 tests
  └─ Client lifecycle / pool / timeout / bounded response / transport failure

Phase 3 new WMS Adapter tests
  └─ DTO / method / path / result mapping / Phase 2 fact translation / evidence / breaker

Phase 4 core tests
  └─ persistence / claim / retry / fencing / terminal / recovery / projection

Phase 5 cutover tests
  └─ production composition / old owner absence / successor-NONE / runtime smoke
```

Phase 3 测试不得 import `tests/core/outbound_http/` 的测试资产，也不得导入旧 `tests/contracts/wms_integration/` support。
`tests/contracts/wms_adapter/support.py` 只实现 Phase 2 公开 Protocol，记录请求并返回预置传输事实；它不复制连接池、超时、
解压或资源预算逻辑。

Phase 3 只向 Phase 4 交付生产类型化 Protocol 和 outcome，不交付测试 fake。Phase 4 核心测试必须在自身测试树定义最小
typed-port fake，不得导入 `tests/contracts/wms_adapter/support.py`，也不得越过 Phase 3 类型化端口直接消费 Phase 2 Transport。

FAST 使用 `WmsCallControl` local fake 验证 Gateway 编排和 outcome；真实 evidence/breaker repository、事务提交/回滚、并发
HALF_OPEN probe 与发送后 evidence 写失败进入 `tests/integration/wms_adapter/`，不混入默认快速回归。

### 7.2 调用与测试数据流

```text
typed request
    ↓ strict DTO / fixed WmsCallSpec
WmsCallControl.begin(operation_identity, correlation facts)
    ├─ begin rollback ───────────────→ fail closed / 0 send
    ├─ breaker BLOCKED ──────────────→ BLOCKED evidence / 0 send
    └─ STARTED evidence committed
              ↓
      OutboundHttpTransport.send × 1
              ↓
      Phase 2 facts + bounded body
              ↓
      decode / typed mapping / business reject mapping
              ↓
WmsCallControl.finish(evidence_key, generation, outcome)
    ├─ commit ──────────────────────→ typed WMS outcome
    └─ rollback ────────────────────→ REMOTE_RESULT_UNKNOWN

FAST：DTO/spec/request mapping → local Transport + local WmsCallControl → outcome/evidence assertions
HEAVY：真实 repositories + PostgreSQL transaction/concurrency → call-control state assertions
```

### 7.3 失败模式与测试 owner

| 失败模式 | 可观察结果 | 主要测试 owner | 重量 |
| --- | --- | --- | --- |
| 请求 DTO 或未知字段非法 | 发送前校验失败，零 HTTP | domain contract test | FAST |
| begin 事务失败 | fail closed，无伪造 evidence key，零 HTTP | call-control persistence | HEAVY |
| breaker OPEN/HALF_OPEN 竞争或 probe owner 消失 | 仅允许合法单 probe；lease 到期后新 generation 可重领 | call-control persistence | HEAVY |
| Phase 2 `NOT_SENT`/`DELIVERY_UNKNOWN` | 映射为不同封闭 outcome | gateway outcome test | FAST |
| 2xx 无效 JSON/DTO 或响应超限 | `FAILED` evidence + breaker failure | gateway outcome test | FAST |
| 业务拒绝 | `BUSINESS_REJECTED` + breaker success | domain/gateway test | FAST |
| 3xx/429/5xx/意外 4xx 及其他非 2xx | 保留远端事实，`FAILED` + breaker failure | gateway outcome test | FAST |
| finish 事务失败 | `REMOTE_RESULT_UNKNOWN`，记录保持 `STARTED` | call-control persistence | HEAVY |
| 取消发生在 send/finish 前 | `CancelledError` 传播，STARTED 不被伪终结 | gateway + persistence | FAST + HEAVY |
| Gateway/Transport 重复关闭 | Transport 只关闭一次 | factory test | FAST |
| 生产路径误导入新包 | 暗构建架构门禁失败 | architecture guardrail | FAST |

### 7.4 性能与资源边界

- 每个公开调用：一次 Phase 2 `send`、最多两个短数据库事务；BLOCKED/begin 失败为零 send。
- 每个进程/目标 WMS：一个 Phase 2 长期 Transport/Client；Gateway 不按请求创建连接池。
- 网络等待期间零数据库事务、零 row/advisory lock；breaker 的 permit 与 finish 都以最小锁范围完成。
- 每个响应继续受 Phase 2 wire/decoded/header/cleanup 上限约束；Phase 3 不缓存、不聚合多页、不增加 bytes 计数合同。
- 两张表只建立唯一键、状态/时间与 breaker 领取所需索引；不为未出现的报表、扫描器或历史查询预建索引。
- Phase 3 暗构建期间不实现 evidence retention job。Phase 5 在首次生产接线前必须按实际调用量冻结保留边界、清理 owner
  和运维验证；不得让每次调用追加的 evidence 在生产启用后成为无人负责的无界数据。

### 7.5 并行化裁决

Phase 3 Task 1 是纯文档合同补全，可以继续；Task 2–12 必须等待完整字段、其余 17 项与 E08–E14 异步关联三组合同门禁
全部关闭。Phase 3 的 Task 1–12 在一个 PR 中
以单泳道顺序交付。Task 4–8 理论上可在 Task 1–3 完成后并行，但它们共享端口导出、Gateway 绑定、合同测试和 conformance
触点；拆 worktree 的合并成本高于收益。实施时保持 12 个顺序任务，并在每个 Task 内完成 red-green-refactor 与局部验收。

## 8. Implementation Tasks

当前只剩三组 WMS 合同入口门禁：完整 33 项字段矩阵、其余 17 项 wire 裁决、E08–E14 status/状态闭集/关联键/幂等承诺。
Task 1 可继续补全文档合同；三组门禁全部关闭后，Phase 3 才能启动 Task 2–12 代码实施。

- [ ] **T1 (P1)** — WMS contract — 补齐 33 项字段矩阵、其余 17 项 wire 裁决和 E08–E14 异步关联合同
  - Surfaced by: Architecture Review — 初稿只覆盖 16 项，没有给出完整 33 项可实施字段矩阵，也未冻结 E08–E14 status、
    状态闭集、关联键和幂等承诺。
  - Files: `docs/hardware/wms_rcs_interface_requirements.md`（只读）、`docs/hardware/README.md`、
    `docs/contracts/wms-northbound-interaction-contract.md`
  - Verify: 人工业务/WMS 合同评审；可编辑文档运行
    `scripts/markdownlint.sh docs/hardware/README.md docs/contracts/wms-northbound-interaction-contract.md docs/superpowers/plans/2026-08-05-wes-wms-thin-access-convergence.md` 和
    `git diff HEAD --check -- . ':(exclude)docs/hardware/wms_rcs_interface_requirements.md'`；只读初稿的
    `shasum -a 256 docs/hardware/wms_rcs_interface_requirements.md` 必须匹配 `docs/hardware/README.md` 登记的摘要

### Task 1：同步 WMS wire 主真源的语义命名与 NONE 边界

**Files:**

- Modify: `docs/contracts/wms-northbound-interaction-contract.md`
- Modify: `docs/hardware/README.md`

- [x] 将 33 项 Capability module 从 `q01_...`/`e01_...` 改为本计划 §5 的语义文件名。
- [x] 把 conformance 明确为只存在于新 Adapter 测试的静态覆盖检查；保留 evidence fail-closed、breaker 和 Phase 2
  单响应有界资源合同。
- [x] 删除未经真实厂商合同证明的 cursor/page-size/next-cursor/自动续页语义；列表 QUERY 固定一次请求返回有界 `items`。
- [x] 明确 outbound auth 为 `NONE`，且合同没有 auth scheme、credential、HMAC、认证 Header 配置/动态注入或扩展点；固定
  协议 Header 由 operation wire 合同拥有。
- [x] 固定 Q15 无内部 Session id；明确 WES 内部 `dispatch_key` 不自动成为 wire 字段，E08/E09 按初稿只发送
  `request_id`，不发送双键或别名。
- [x] 确认 `docs/hardware/wms_rcs_interface_requirements.md` 为 WMS 交互约定初稿和 Task 1 业务输入；保持原文不修改。
- [x] Decision A：初稿覆盖 16 项采用其 path/业务字段与当前批准 method，E02 固定为 `POST`；当前架构同时清除认证、
  重试、缓存、分页和旧生命周期语义。
- [x] 建立初稿覆盖矩阵，明确 16 项已采用的 path、当前 method 及尚缺字段，并登记其余 17 项缺失；旧 ports 只可用于发现
  遗漏，不得覆盖初稿或当前批准合同。
- [x] 冻结 prefix 所有权：Phase 2 `base_url` 只含 origin，operation path 持有完整 `/api/wms` 或 `/api/MCS` path；
  不做运行时双重兼容拼接。
- [ ] 在北向合同中补齐 33 项 request/result 的字段、必填性、类型、枚举/精度/时间格式、固定协议 Header 和业务拒绝码闭集。
- [ ] 由 WMS/业务方补齐并批准其余 17 项 method/path/DTO/拒绝码；proposed path 不能直接作为实现依据。
- [ ] 按出库顶层设计冻结 WMS → WES `PickingTask` inbound 合同，覆盖创建、排队优先级更新、替代来源追加、指定恢复和取消；
  不得恢复旧 Q10–Q13 拉取式单据/任务查询。
- [ ] 由 WMS 批准 E08–E14 status method/path、状态闭集、`request_id`/`task_id` 关联和幂等承诺；未批准前不得实现
  proposed status endpoint，也不得回退到 callback 直接决定终态。
- [ ] 完整字段矩阵、其余 17 项 wire 裁决和 E08–E14 异步关联合同任一未获批时停止，不启动 Task 2；禁止根据方法名、
  旧测试或实现输出猜测 DTO、status endpoint、状态或关联语义。
- [x] 对可编辑 Markdown 运行格式检查及
  `git diff HEAD --check -- . ':(exclude)docs/hardware/wms_rcs_interface_requirements.md'`；只读初稿不做空白格式化，
  `shasum -a 256 docs/hardware/wms_rcs_interface_requirements.md` 已与 `docs/hardware/README.md` 登记摘要匹配。
  本 Task 是纯文档变更，不新增或修改测试。

**Commit:** `docs(wms-adapter): 冻结 Phase3 新能力合同`

### Task 2：建立共享 DTO 基类、outcome、配置与 call-control 合同

**Files:**

- Create: `src/app/wms_adapter/__init__.py`
- Create: `src/app/wms_adapter/config.py`
- Create: `src/app/wms_adapter/outcomes.py`
- Create: `src/app/wms_adapter/call_control.py`
- Create: `src/app/wms_adapter/_shared.py`
- Create: `tests/contracts/wms_adapter/__init__.py`
- Create: `tests/contracts/wms_adapter/support.py`
- Create: `tests/contracts/wms_adapter/test_config_and_outcomes.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Test: `tests/scripts/test_select_heavy_tests.py`

- [ ] 写失败测试：配置只公开 `base_url`/`timeout_seconds`；public exports 无 auth/provider/registry/generic call；
  `WmsCallSpec.fixed_headers` 只接受 operation 固定非认证 Header；内部 `WmsCallControl` 只提供 begin/finish，不泄露
  Session/Repository。本 Task 不创建业务 Protocol 或占位方法签名。
- [ ] 运行目标测试，确认因新包不存在而失败。
- [ ] 实现不可变配置、严格 DTO 基类、最小 `WmsCallSpec`、封闭 outcome 和 call-control Protocol。
- [ ] 为本 Task 新增的公共合同/编码路径添加精确 HEAVY selector NONE；这些路径只由 FAST 合同测试承接，
  不将尚未存在的持久化测试伪造为 owner。先补 selector 合同用例，再用 `--scope staged` 证明这些路径精确选择 NONE。
- [ ] 运行目标测试，确认通过；再运行 Ruff 与 BasedPyright 针对新包。

**Commit:** `feat(wms-adapter): 建立共享公共合同`

### Task 3：建立 fail-closed evidence 与 DB-backed breaker

**Files:**

- Create: `src/app/wms_adapter/models/__init__.py`
- Create: `src/app/wms_adapter/models/call_evidence.py`
- Create: `src/app/wms_adapter/models/circuit_breaker.py`
- Create: `src/app/wms_adapter/repositories/__init__.py`
- Create: `src/app/wms_adapter/repositories/call_evidence_repository.py`
- Create: `src/app/wms_adapter/repositories/circuit_breaker_repository.py`
- Create: `src/app/wms_adapter/services/__init__.py`
- Create: `src/app/wms_adapter/services/call_control_service.py`
- Modify: `migrations/env.py`
- Create: `migrations/versions/<alembic-generated-revision>_add_wms_outbound_call_control.py`
- Create: `tests/contracts/wms_adapter/test_call_control_contract.py`
- Create: `tests/integration/wms_adapter/__init__.py`
- Create: `tests/integration/wms_adapter/test_call_control_persistence.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Test: `tests/scripts/test_select_heavy_tests.py`

- [ ] 先写 FAST 失败测试，冻结 permit、evidence state、breaker decision 和 `WmsCallControl` begin/finish 合同；不访问真实数据库。
- [ ] 使用 Alembic generator 创建 revision，再编辑为 `wes_biz.wms_outbound_call_evidence` 与
  `wes_biz.wms_outbound_circuit_breaker` 两张最终语义新表；不得复用旧表名或修改历史 revision。
- [ ] 在 `migrations/env.py` 显式导入新 `wms_adapter.models`；在隔离进程中只经 Alembic env 加载
  `target_metadata`，验证两张新表已注册，再在已升级的空测试库运行 `uv run alembic check`。不得依赖测试模块主动导入
  model 证明注册成功；该导入只服务 Alembic 模型发现，不得接入生产 Composition Root。
- [ ] 在同一 Task/Commit 仅为本 Task 新增的 `models/**`、`repositories/**`、`services/**`、
  `migrations/env.py` 和实际生成的 revision 添加精确 HEAVY mapping，指向
  `tests/integration/wms_adapter/test_call_control_persistence.py`；不得把纯 DTO/spec/Gateway 路径并入该映射，也不得使用宽泛
  `src/app/wms_adapter/**`、`migrations/versions/**`、显式 NONE 或旧 WMS 测试掩盖持久化影响。
- [ ] 先补 selector 合同失败用例，再实现 mapping；精确暂存本 Task 文件后运行
  `uv run scripts/select_heavy_tests.py --scope staged`，证明生产模块、Alembic 注册和 revision 都选中新 HEAVY 测试。
- [ ] 写 HEAVY 失败测试，覆盖 STARTED evidence 先提交、begin 失败并回滚、BLOCKED、finish 成功、finish 失败保持 STARTED、
  HALF_OPEN generation/fencing/probe lease 回收、固定 3/60/1 参数、并发首次创建、取消传播和事务回滚；本 Task 不断言
  尚未存在的 Gateway send。
- [ ] 实现两个最小 model/repository 与一个 `WmsCallControlService`；新模型不包含旧 Provider identity、profile digest、
  drift scan、async summary 和 retryable 决策字段，不 import 或修改旧 WMS package。
- [ ] Breaker 只按 `operation_identity` 建键，参数固定为连续失败 3 次、OPEN 60 秒、HALF_OPEN 单 probe 成功关闭；
  probe lease 固定 60 秒并以 generation fencing 回收；不增加配置项或独立 evidence/breaker service 层。
- [ ] 证明 begin/finish 都是短事务，返回时不逸出活动 Session、row lock 或 advisory lock；Gateway 的调用顺序由 Task 9 验证。
- [ ] 运行 FAST call-control 测试、显式 integration 测试、隔离 metadata discovery、迁移 upgrade/render/check、selector
  合同、Ruff 与 BasedPyright。

**Commit:** `feat(wms-adapter): 建立调用证据与熔断能力`

### Task 4：实现 master_data 七项能力

**Files:**

- Create: `src/app/wms_adapter/master_data/__init__.py`
- Create: `src/app/wms_adapter/master_data/get_material.py`
- Create: `src/app/wms_adapter/master_data/get_materials.py`
- Create: `src/app/wms_adapter/master_data/list_zones.py`
- Create: `src/app/wms_adapter/master_data/list_locations.py`
- Create: `src/app/wms_adapter/master_data/get_rack.py`
- Create: `src/app/wms_adapter/master_data/list_racks.py`
- Create: `src/app/wms_adapter/master_data/get_bin.py`
- Create: `tests/contracts/wms_adapter/test_master_data.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Test: `tests/scripts/test_select_heavy_tests.py`

- [ ] 对 Q01–Q07 逐项写失败测试，断言严格 request/result、固定完整 path、path/query 编码、业务拒绝码；Q02 只接受
  `ids` 批量查询，Q03/Q04/Q06 的集合结果只定义本次响应的有界 `items`，不接受 cursor/page-size。本 Task 只验证 DTO/spec，
  不断言 Gateway send；单次发送统一由 Task 9 验证。
- [ ] 运行新测试，确认因模块不存在而失败。
- [ ] 每个语义文件只实现本 operation 的 request/result/spec；至少三个 operation 真实共享的 value object 才进入 `_shared.py`。
- [ ] 为 `master_data/**` 添加精确 selector NONE 并补 selector 合同；该目录只持有 DTO/spec，不访问持久化边界。
- [ ] 运行该域测试与 Ruff，确认通过。

**Commit:** `feat(wms-adapter): 实现主数据能力`

### Task 5：实现 document 三项能力

**Files:**

- Create: `src/app/wms_adapter/document/__init__.py`
- Create: `src/app/wms_adapter/document/get_grn.py`
- Create: `src/app/wms_adapter/document/list_grn_packages.py`
- Create: `src/app/wms_adapter/document/validate_rough_sorter_admission.py`
- Create: `tests/contracts/wms_adapter/test_document.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Test: `tests/scripts/test_select_heavy_tests.py`

- [ ] 对 Q08–Q09/Q15 写失败测试，覆盖 Task 1 最终批准的 wire、单次列表响应、GRN/Package 关系和 Q15 封闭拒绝码。
- [ ] 单独断言 Q15 序列化结果不含 `session_id`、执行对象 id 或数据库主键。
- [ ] 为 `document/**` 添加精确 selector NONE 并补 selector 合同；该目录只持有 DTO/spec，不访问持久化边界。
- [ ] 运行新测试确认失败，实现最小 DTO/spec，再运行测试与 Ruff 确认通过。

**Commit:** `feat(wms-adapter): 实现单据能力`

### Task 6：实现 inventory 八项能力

**Files:**

- Create: `src/app/wms_adapter/inventory/__init__.py`
- Create: `src/app/wms_adapter/inventory/query_inventory.py`
- Create: `src/app/wms_adapter/inventory/get_reservation.py`
- Create: `src/app/wms_adapter/inventory/reserve_inventory.py`
- Create: `src/app/wms_adapter/inventory/release_reservation.py`
- Create: `src/app/wms_adapter/inventory/confirm_inbound.py`
- Create: `src/app/wms_adapter/inventory/confirm_outbound.py`
- Create: `src/app/wms_adapter/inventory/transfer_inventory.py`
- Create: `src/app/wms_adapter/inventory/confirm_return_putaway.py`
- Create: `tests/contracts/wms_adapter/test_inventory.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Test: `tests/scripts/test_select_heavy_tests.py`

- [ ] 对 Q10–Q11/E01–E06 写失败测试，覆盖 query/path/json 编码、E02 `POST`、同步 terminal result、业务拒绝和
  各 operation 最终批准的唯一 wire 关联字段。
- [ ] 断言全部写操作 DTO 不接受未批准的 `dispatch_key`/`idempotency_key` 别名、Provider identity 或 auth 字段。
- [ ] 为 `inventory/**` 添加精确 selector NONE 并补 selector 合同；该目录只持有 DTO/spec，不访问持久化边界。
- [ ] 运行新测试确认失败，实现最小 DTO/spec，再运行测试与 Ruff 确认通过。

**Commit:** `feat(wms-adapter): 实现库存能力`

### Task 7：实现 reconciliation 三项能力

**Files:**

- Create: `src/app/wms_adapter/reconciliation/__init__.py`
- Create: `src/app/wms_adapter/reconciliation/check_bin_drift.py`
- Create: `src/app/wms_adapter/reconciliation/check_rack_drift.py`
- Create: `src/app/wms_adapter/reconciliation/check_full_drift.py`
- Create: `tests/contracts/wms_adapter/test_reconciliation.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Test: `tests/scripts/test_select_heavy_tests.py`

- [ ] 对 Q12–Q14 写失败测试，覆盖固定 query、严格漂移结果、`source_version` 和未知字段拒绝。
- [ ] 为 `reconciliation/**` 添加精确 selector NONE 并补 selector 合同；该目录只持有 DTO/spec，不访问持久化边界。
- [ ] 运行新测试确认失败，实现最小 DTO/spec，再运行测试与 Ruff 确认通过。

**Commit:** `feat(wms-adapter): 实现对账能力`

### Task 8：实现 fulfillment 十二项能力及七项状态查询

**Files:**

- Create: `src/app/wms_adapter/fulfillment/__init__.py`
- Create: `src/app/wms_adapter/fulfillment/notify_pkg_binding.py`
- Create: `src/app/wms_adapter/fulfillment/request_rack_supply.py`
- Create: `src/app/wms_adapter/fulfillment/request_rack_transport.py`
- Create: `src/app/wms_adapter/fulfillment/change_rack_face.py`
- Create: `src/app/wms_adapter/fulfillment/full_box_exchange.py`
- Create: `src/app/wms_adapter/fulfillment/move_bins_to_conveyor_entry.py`
- Create: `src/app/wms_adapter/fulfillment/move_bins_from_conveyor_exit.py`
- Create: `src/app/wms_adapter/fulfillment/request_load_unit_transport.py`
- Create: `src/app/wms_adapter/fulfillment/publish_manual_task.py`
- Create: `src/app/wms_adapter/fulfillment/cancel_request.py`
- Create: `src/app/wms_adapter/fulfillment/report_picking_source_ng.py`
- Create: `src/app/wms_adapter/fulfillment/confirm_picking_completed.py`
- Create: `tests/contracts/wms_adapter/test_fulfillment.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Test: `tests/scripts/test_select_heavy_tests.py`

- [ ] 对 E07–E18 写失败测试，覆盖同步结果、七项 submit、批准后的 status endpoint 与 cancel；E08/E09 只序列化
  `request_id`，不泄露内部 `dispatch_key` 或发送双键。
- [ ] 覆盖 Task 1 最终批准的 E11/E12/E13 精确 wire 字段与封闭 ACK/result，不把当前业务目标提案冒充 WMS 合同，
  不测试 WorkLine PICK/SCAN/PUT 决策。
- [ ] 为 `fulfillment/**` 添加精确 selector NONE 并补 selector 合同；该目录只持有 DTO/spec，不访问持久化边界。
- [ ] 运行新测试确认失败，实现最小 DTO/spec，再运行测试与 Ruff 确认通过。

**Commit:** `feat(wms-adapter): 实现履约能力`

### Task 9：实现 Gateway 调用编排与结果翻译

**Files:**

- Create: `src/app/wms_adapter/ports.py`
- Create: `src/app/wms_adapter/gateway.py`
- Create: `tests/contracts/wms_adapter/test_ports.py`
- Create: `tests/contracts/wms_adapter/test_gateway_outcomes.py`
- Modify: `src/app/wms_adapter/__init__.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Test: `tests/scripts/test_select_heavy_tests.py`

- [ ] 在 33 项 DTO/spec 全部存在后写失败测试，冻结三个互不继承的业务 Protocol，方法集合精确匹配 §6.2，且每个方法
  直接使用对应 operation 的最终 request/result 类型，不允许占位类型、generic payload 或后续补签名。
- [ ] 写失败测试：每个 allowed 公开调用只申请一个 permit、创建一条 STARTED evidence，并且恰好形成一次 Phase 2 request；
  BLOCKED/begin 失败为零 request；GET/POST/path/query/固定协议 headers/body 精确，JSON body 不得缺失已批准的
  media type。
- [ ] 用记录顺序的 `WmsCallControl`/Transport local fake 断言 begin 已完成后才 send、send 完成后才 finish；结合 Task 3
  “begin/finish 返回时无活动 Session/锁”合同，证明网络阶段不持有数据库事务或锁。
- [ ] 覆盖 2xx 成功、业务拒绝、3xx、意外 4xx、429/5xx、其他非 2xx、无效 JSON/DTO、NOT_SENT、
  DELIVERY_UNKNOWN、breaker BLOCKED、
  begin/finish 失败的完整 outcome 映射；`CancelledError` 原样传播，不转换为普通 outcome。业务拒绝计为 breaker success，
  其余远端/合同失败按 §6.7 更新 breaker。
- [ ] 断言 Gateway 构造器只接受 `OutboundHttpTransport` 与 `WmsCallControl`，没有 `httpx.AsyncClient`、Session、Repository、
  auth/provider/profile 参数。
- [ ] 运行测试确认失败，实现三条最终 typed Protocol、唯一私有 `_send`/`_decode`/`_finish` 编排路径和显式公开方法绑定，
  不提供 registry 或 generic public call；网络阶段不打开数据库事务。
- [ ] 为 `gateway.py` 添加精确 selector NONE 并补 selector 合同；Gateway 只依赖 `WmsCallControl` 合同，持久化实现由 Task 3 HEAVY 独立承接。
- [ ] 运行五个域测试、Protocol/Gateway 测试、Ruff 和 BasedPyright，确认通过。

**Commit:** `feat(wms-adapter): 实现无状态 HTTP Gateway`

### Task 10：消费 Phase 2 builder 并冻结生命周期

**Files:**

- Create: `src/app/wms_adapter/factory.py`
- Create: `tests/contracts/wms_adapter/test_factory.py`
- Modify: `src/app/wms_adapter/gateway.py`
- Modify: `src/app/wms_adapter/__init__.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Test: `tests/scripts/test_select_heavy_tests.py`

- [ ] 写失败测试：factory 固定传递 `system_id="wms"`、base URL、timeout，并使用传入的 `session_factory` 构造持久化
  `WmsCallControl`；返回 Gateway；`aclose()` 恰好关闭一次 Transport；业务 Protocol 不含 `aclose`。
- [ ] 运行测试确认失败，实现最小 factory、call-control 装配与 Gateway 生命周期委托；不读取全局 Settings。
- [ ] 为 `factory.py` 添加精确 selector NONE 并补 selector 合同；factory 装配由 FAST 合同测试承接，真实 call-control 持久化继续由 Task 3 HEAVY 承接。
- [ ] 运行新测试和 `tests/core/outbound_http/`，前者证明 Phase 3 消费，后者只作为 Phase 2 回归，不互相替代。

**Commit:** `feat(wms-adapter): 接入 Phase2 Transport builder`

### Task 11：建立暗构建边界门禁

**Files:**

- Create: `tests/architecture/test_wms_adapter_dark_build_guardrail.py`

- [ ] 写失败门禁，扫描新包零 `httpx`、旧 WMS package、Provider/Profile/HMAC/credential、编号文件、generic public `call`。
- [ ] 写失败 AST 门禁，限定 WMS 生产代码只有 `src/app/wms_adapter/factory.py` 可以 import、引用或调用
  `build_outbound_http_transport`；Gateway 和其余 WMS 模块只接收 `OutboundHttpTransport` Protocol。
- [ ] 写失败门禁，对新包之外的全部生产 `src/` 做 AST/导入依赖扫描，同时覆盖绝对与相对 import；任何现有
  生产模块引用 `wms_adapter` 都必须失败，不得只检查 Composition Root、Celery、API 或 WorkLine 的直接 import。
- [ ] 运行 architecture guardrail 和新 Adapter 测试，确认通过。

**Commit:** `test(wms-adapter): 冻结暗构建边界`

### Task 12：Phase 3 退出验证

**Files:**

- Verify only; no planned production edits.

- [ ] 运行 GitNexus detect changes，确认变更只包含新 `wms_adapter`、Alembic 模型注册与新 revision、新测试、wire 合同和
  精确 HEAVY mapping。
- [ ] 运行 `uv run pytest tests/contracts/wms_adapter tests/architecture/test_wms_adapter_dark_build_guardrail.py -q`。
- [ ] 运行 `uv run pytest tests/integration/wms_adapter -q -o addopts=''`，显式验证新 evidence/breaker 的真实持久化与并发。
- [ ] 运行 `uv run pytest tests/core/outbound_http -q`，仅证明 Phase 2 公共合同未回归。
- [ ] 运行 `uv run pytest tests/scripts -q` 和测试拓扑 guardrail。
- [ ] Task 12 若产生未提交的验收修复，只对这些文件精确暂存并运行
  `uv run scripts/select_heavy_tests.py --scope staged`；无验收修复时不得用空 staged diff 冒充验证。完整 Phase 3 提交差异
  始终以 `uv run scripts/select_heavy_tests.py --base origin/${CI_TARGET_BRANCH}` 验证。
- [ ] 在升级后的空测试库运行隔离 metadata discovery、迁移 render 与 `uv run alembic check`，确认 Alembic env 能发现两张新表。
- [ ] 运行 `uv run ruff format --check src/app/wms_adapter tests/contracts/wms_adapter`、
  `uv run ruff check src/app/wms_adapter tests/contracts/wms_adapter tests/architecture/test_wms_adapter_dark_build_guardrail.py`。
- [ ] 运行 BasedPyright、Import Linter 和 `./scripts/git-quality-gate.sh --profile quality`。
- [ ] 运行精确扫描，确认新包零旧依赖、旧包零变更、新包之外的全部生产 `src/` 零新包 import。
- [ ] 运行 AST 扫描，确认 WMS 生产代码仅 `factory.py` 引用 Phase 2 builder，Gateway/其余模块零 builder import/call。
- [ ] 运行精确扫描，确认新合同和新包零 cursor/page-size/next-cursor/自动续页语义，且每个公开方法只绑定一次 send。
- [ ] 在 PR 中明确：Phase 3 只完成暗构建，不宣称 WMS 已切换；旧代码、旧配置、旧测试和旧数据处置均属于 Phase 5。

**Commit if verification fixes exist:** `fix(wms-adapter): 完成 Phase3 暗构建验收`

## 9. 验收命令

```bash
uv run pytest tests/contracts/wms_adapter tests/architecture/test_wms_adapter_dark_build_guardrail.py -q
uv run pytest tests/integration/wms_adapter -q -o addopts=''
uv run pytest tests/core/outbound_http -q
uv run pytest tests/scripts -q
uv run scripts/select_heavy_tests.py --base origin/${CI_TARGET_BRANCH}
uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
uv run pytest --collect-only -q -o addopts='' | tail -5
uv run ruff format --check src/app/wms_adapter tests/contracts/wms_adapter
uv run ruff check src/app/wms_adapter tests/contracts/wms_adapter tests/architecture/test_wms_adapter_dark_build_guardrail.py
uv run basedpyright src/app/wms_adapter
uv run lint-imports
./scripts/git-quality-gate.sh --profile quality
```

Phase 3 权威测试只有新 Adapter 测试与暗构建门禁。Phase 2 测试、selector 和 quality profile 是回归/仓库门禁，不能作为
33 项 WMS 业务能力的替代证明。本阶段不运行旧 WMS HEAVY/E2E 作为新能力验收，也不修改它们。

## 10. Phase 5 handoff 清单

Phase 3 只登记下列未来动作，不执行：

- 生产 Composition Root 构造 `build_wms_adapter(...)` 并管理 `aclose()`。
- Phase 5 架构门禁证明 WMS Composition Root 零 `src.core.outbound_http` import、零 Transport 构造或注入。
- WMS QUERY、确认、搬运/status/cancel 消费者切换到三条类型化端口。
- 普通 WMS event callback ingress 改接 Phase 4 `InboundEvidence` application port；可选 hint 只有 inbound 合同完整批准且
  Phase 4 successor 验收后才接入，否则按 `NONE` 只删除旧 hint 分支。
- 删除旧 Provider/Profile/Registry/QUERY/Effect/status/Outbox、裸 WMS HTTP/HMAC/credential/fallback。
- 删除旧 WMS call evidence/breaker 服务、模型、表和全部直接引用；新表不读取或迁移旧记录。
- 删除旧 Settings/Compose/Jenkins/Celery/Runbook 输入和旧测试 owner。
- 清理开发/测试旧表数据；不迁移旧数据、不兼容读取。
- 在首次生产流量进入新 Adapter 前冻结新 call evidence 的保留边界、清理 owner 和运维验证；Phase 3 不预建清理任务。

Phase 5 必须基于届时真实代码重新运行 Serena/GitNexus 引用分析，不能把本清单当作静态删除脚本。

## 11. 自审结果

| 检查项 | 结果 |
| --- | --- |
| Phase 3 是否只构建新能力 | 是；旧生产包、装配、配置和旧测试均不在 Files 清单 |
| 是否真实消费 Phase 2 | 是；Task 10 factory 固定调用已完成的 Phase 2 GET/POST builder |
| 是否存在裸 Client 或重复传输 | 否；Gateway 只接收 `OutboundHttpTransport` 与 `WmsCallControl` |
| 是否包含未经证明的分页 | 否；列表能力一次请求返回有界 `items`，无 cursor、自动续页或累计分页预算 |
| 当前认证边界 | 仅 `NONE`；无 auth/HMAC/credential 配置或动态 seam；固定非认证 wire Header 只归 operation spec |
| 文件是否带 Q/E 序号 | 否；编号只在本文和测试 case id |
| 是否存在运行时双轨 | 否；新包未接入任何生产 Composition Root |
| 测试是否只对新能力负责 | 是；不修改或复用旧 WMS 测试，回归门禁不冒充业务验收 |
| 是否迁移或删除旧能力 | 否；消费者切换和旧 owner 删除全部移至 Phase 5 |
| 是否过度设计 | 否；五个业务域、三条端口、一个 Gateway、一个 call-control service、一个 factory，无 registry/fake 平台/认证扩展 |
| Phase 4/5 交接是否明确 | 是；Phase 4 消费新端口暗构建可靠对象，Phase 5 执行唯一原子切换 |
| 16 项初稿 wire 是否已裁决 | 是；采用初稿 path/业务字段和当前 method；E02 为 `POST`；剔除旧架构机制 |
| 33 项 DTO 是否可直接实施 | 否；完整字段矩阵、其余 17 项及 E08–E14 异步关联合同未完成，Task 2–12 继续阻断 |

## 12. 工程复审摘要

- **架构边界：** Phase 3 只暗构建新 Adapter；Phase 4 只暗构建新平台；Phase 5 才原子迁移生产消费者并删除旧闭包。
- **真实复用：** Phase 3 只依赖 Phase 2 公开 Transport/builder/result/limits；旧 WMS 包只用于识别 Phase 5 删除范围。
- **合同收缩：** 初稿只在一份货架列表响应样例中出现 `page/page_size`，没有请求游标、续页规则或一致性语义；
  cursor/自动续页只存在旧通用实现，因此目标仍收缩为一次有界列表响应。
- **Decision A：** 初稿覆盖的 16 项采用其 path/业务字段和当前批准 method；E02 为 `POST`；认证、重试、缓存、分页和旧
  生命周期由当前架构清除。
- **语义修正：** Q02 改为 `get_materials(ids)`；Q08 采用 GRN header + `items[]`；E08/E09 wire 采用初稿
  `request_id` 和业务字段，不泄露内部 `dispatch_key`。
- **入口阻断：** 完整字段矩阵、其余 17 项及 E08–E14 status/状态闭集/关联键/幂等承诺仍缺；E02 已批准使用 `POST`，
  不再形成 Phase 2 前置阻断。
- **异步缺口：** 初稿 E08/E09 只给 submit 与 `task_id`，没有 status method/path、状态闭集、幂等和关联承诺；proposed
  status endpoint 不得提前实现。
- **KISS 修正：** evidence 与 breaker 保留独立模型/repository，但由单一 call-control service 协调，删除两层单用途 service 计划。
- **失败闭包：** begin、BLOCKED、Phase 2 交付事实、业务拒绝、无效响应和 finish 失败均有明确 outcome/evidence/breaker
  所有者；取消原样传播，已提交的 STARTED evidence 保持不变。
- **性能边界：** 每次调用一次 send、最多两个短事务、网络期间无事务/锁、每进程一个长期 Transport，无 retry/cache/分页聚合。
- **测试边界：** FAST 只验证新 WMS 合同与编排；HEAVY 只验证新 call-control 持久化/并发；Phase 2 和旧 WMS 测试不充当业务 oracle。
- **并行化：** 一个 PR、单泳道、12 个顺序 Task；共享合同和出口不拆 worktree。
- **外部复审：** 累计检出 42 项并已逐项核验修复。前 8 项收敛固定 wire Header 字段闭包、HEAVY mapping 同步时点、
  隔离 Alembic metadata discovery、阶段状态、全分支 selector、Task 门禁和取消传播；后续 32 项收敛 Task 计数、Phase 5 切换编号、
  入口状态表述、逐 Task HEAVY 所有权、全量生产 import 门禁、Master 同步、Phase 2 暗构建图、只读初稿完整性、统计噪声和
  验收命令目标、Phase 4 测试 fake 所有权、SPEC 同步和 Phase 5 唯一 factory 装配。E02 改为 `POST` 后，P0 相关门禁已由
  当前合同直接撤销，不保留兼容路径；WMS builder 调用所有权、AST 门禁、三组入口门禁的 fail-closed 表述和可编译的
  Task 顺序、SRS 内外幂等/breaker/重提所有权边界、资源 ADR 和可选 hint 的批准/`NONE` 接入语义也已闭合，但三组外部
  批准条件仍未满足。本轮新增 2 项收敛 Serena 状态矛盾与编码支持集合的单一真源；最终独立复审未检出新意见。

## GSTACK REVIEW REPORT

| Review | 本轮状态 | 发现 | 未解决 | 说明 |
| --- | --- | ---: | ---: | --- |
| ENG REVIEW | BLOCKED | 11 | 1 | 其余 17 项、完整字段矩阵与异步关联仍待批准；E02 已批准使用 `POST` |
| INDEPENDENT REVIEW | CLEAR | 42 | 0 | 累计 42 项均已核验修复；最终独立复审未检出新意见；外部合同阻断不计为文档缺陷 |
| SERENA REVIEW | CLEAR | 3 | 0 | 已激活 `wes_backend` 并完成符号/引用复核；3 个可复现类型合同问题已修复，仓库 `uv run basedpyright` 为零错误 |
| SEQUENTIAL REVIEW | CLEAR | 7 | 0 | Decision A、prefix 所有权、阶段边界、单次发送、事务与测试所有权已闭合 |
| DESIGN REVIEW | N/A | 0 | 0 | 无 UI/交互范围 |

**VERDICT：Phase 3 架构边界与 16 项 wire 基线已收敛；Task 2–12 仍不得实施。**

**UNRESOLVED DECISIONS:**

- WMS 合同：补齐 16 项完整字段矩阵，由 WMS/业务方补齐和批准其余 17 项 operation，并冻结 E08–E14 status、状态闭集、
  `request_id`/`task_id` 关联和幂等承诺。
