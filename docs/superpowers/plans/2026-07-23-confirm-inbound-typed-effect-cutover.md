# `confirm_inbound` typed EFFECT 迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 将 `confirm_inbound` 硬切换为唯一 typed WMS EFFECT，并删除该 operation 的全部旧 Port、字符串路由、配置、消费者和测试引用。

**架构：** material-flow 只创建冻结的 `RuntimeIntent.system_capability`；operation-owned handler 经既有 `ConfirmInboundDispatchGateway` 生成 `DispatchEnvelope`，再在调用方事务内复用 `RuntimeIntentLogRepository.add_proposed_pair` 写入 T8 双账本。提交后的网络 I/O、typed transport、reducer、lease/fencing、冻结 binding 和 crash recovery 全部继续由 T8a-g 既有路径负责。

**技术栈：** Python 3.13、Pydantic、SQLModel/SQLAlchemy async、PostgreSQL、Pytest、Alembic、GitNexus。

## 全局约束

- API → Service → Repository → Database；不跨层直接访问。
- 所有项目命令使用 `uv run ...`，Shell 命令使用 RTK。
- 每个既有函数、类或方法修改前运行 GitNexus upstream impact。
- 严格 RED → GREEN；没有失败测试不得修改生产代码。
- 不保留 alias、delegate、fallback、旧数据迁移或双运行。
- 不迁移其它 WMS operation，不实施 T10、T11、Jenkins 或 GitLab。
- 不提交用户已有的 `AGENTS.md`、`CLAUDE.md` 修改。

---

### Task 1：冻结 operation-owned EFFECT 合同

**文件：**

- 新增：`src/app/runtime/system_capabilities/wms/inventory/confirm_inbound/definition.py`
- 新增：`src/app/runtime/system_capabilities/wms/inventory/confirm_inbound/handler.py`
- 新增：`src/app/runtime/system_capabilities/wms/inventory/confirm_inbound/effect_adapter.py`
- 修改：`src/app/runtime/system_capabilities/wms/inventory/confirm_inbound/__init__.py`
- 生成：`src/app/runtime/system_capabilities/generated_index.py`
- 测试：`tests/contracts/wms_integration/test_confirm_inbound_typed_effect.py`

**接口：**

- 消费 `ConfirmInboundOperationRequest`、`ConfirmInboundDispatchGateway.build_envelope` 和已 claim 的 `RuntimeIntentLog`。
- 输出 `OUTBOX_ASYNC` System Capability definition，以及同事务 `RuntimeIntentLog(PROPOSED) + SystemOutbox(NEW)`。

- [x] 先写 capability/handler/adapter 合同测试，覆盖 definition、typed request、冻结 envelope、双账本和重复 claim。
- [x] 运行聚焦测试，确认因 definition/handler/adapter 尚不存在而失败。
- [x] 实现最小 operation-owned handler 与 adapter，不执行 I/O、不 commit、不新增 dispatcher/ledger/retry。
- [x] 生成 System Capability index，并运行聚焦测试至通过。

### Task 2：硬切换 material-flow 消费者

**文件：**

- 修改：`src/app/runtime/capabilities/material_flow/sorter_inbound_runtime_service.py`
- 修改：`src/app/runtime/capabilities/material_flow/sorter_inbound_preview_service.py`
- 测试：`tests/workline_runtime/test_sorter_inbound_runtime_service.py`
- 测试：`tests/workline_runtime/test_sorter_inbound_preview_service.py`
- 测试：`tests/workline_runtime/test_runtime_intent_effect_applier.py`

**接口：**

- material-flow 产生 `wms.inventory.confirm_inbound@v1` 的 `RuntimeIntent.system_capability`。
- preview 只暴露稳定 operation identity，不再暴露旧 `Port.method`。

- [x] 先改测试断言 typed SYSTEM_CAPABILITY、稳定业务 identity、冻结快照和重复执行不新增 outbox。
- [x] 运行聚焦测试，确认旧 EXTERNAL_REQUEST/字符串合同导致失败。
- [x] 最小修改消费者；仅迁移 `confirm_inbound`，保留其它 operation 当前行为。
- [x] 运行聚焦 runtime 测试至通过。

### Task 3：删除 `confirm_inbound` 遗留链

**文件：**

- 修改：`src/app/wms_integration/ports/inventory_transaction.py`
- 修改：`src/app/wms_integration/models/ports.py`
- 修改：`src/app/wms_integration/models/__init__.py`
- 修改：`src/app/wms_integration/services/typed_ports.py`
- 修改：`src/app/wms_integration/services/endpoint_config.py`
- 修改：`src/app/contracts/external_contract_profile_catalog.py`
- 修改或删除：该 operation 对应 legacy tests、mock consumer 和配置断言。

**接口：**

- `confirm_inbound` 只剩 T2 typed operation contract 与 T9 capability/gateway/adapter。
- 其它库存 operation 的 legacy 接口保持不变。

- [x] 先新增全仓旧路径归零门禁，并让其报告当前 Port/model/service/config/catalog/test 引用。
- [x] 删除旧 method、DTO、endpoint config、profile entry、consumer 与不再成立的 legacy tests，不加兼容委托。
- [x] 运行 WMS contracts、client、breaker、mock 与架构门禁至通过。

### Task 4：真实 PostgreSQL EFFECT 生命周期

**文件：**

- 新增：`tests/integration/test_confirm_inbound_typed_effect_postgresql.py`
- 新增或扩展：`tests/resilience/test_external_http_effect_crash_matrix_postgresql.py`

**接口：**

- 同一业务 identity 重复执行只保留一条 intent/outbox，rotation 只影响新 intent。
- transport reject、clearly-not-sent、ambiguous、callback 与 reconciliation 都只通过 T8 reducer 推进。

- [x] 先写 Docker PostgreSQL integration/resilience 测试并确认缺少 T9 接线时失败。
- [x] 仅修正 T9 接线缺口；不得修改 T8 状态语义。
- [x] 运行真实 PostgreSQL integration/resilience 集合至通过。

### Task 5：inventory 归零、验证与提交

**文件：**

- 修改：`docs/architecture/northbound-wms-operation-inventory.csv`
- 修改：T1 inventory 中归属 T9 的活动文档。
- 修改：`.superpowers/sdd/task-T9-report.md`

- [x] 删除 inventory 中 14 条已消除的 `confirm_inbound` 遗留项，并更新活动文档为 stable operation identity。
- [x] 运行 inventory 双向门禁和全仓 `rg` 归零审计。
- [x] 运行相关快速回归、Docker PostgreSQL、测试拓扑、显式收集和完整 quality profile。
- [x] stage 后运行 GitNexus detect changes，复核只影响预期 EFFECT/consumer 流。
- [x] 使用中文 Conventional Commit 提交，明确排除 `AGENTS.md`、`CLAUDE.md`。
