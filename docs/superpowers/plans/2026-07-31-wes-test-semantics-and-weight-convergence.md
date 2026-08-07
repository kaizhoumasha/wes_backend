# WES 核心测试语义与重量收敛实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 WES 核心测试收敛到 SPEC 定义的最小执行内核、通用 WorkLine 能力、外部合同和可靠性不变量；
把具体厂商合同和具体执行插件测试从核心 `tests/` 移出，分别随独立 Adapter 包和插件包重新交付。

**Architecture:** 核心仓库、厂商 Adapter 包和执行插件包拥有三组独立测试边界。核心 `tests/` 不保存具体工作线流程、具体插件 Handler 或具体厂商映射；
Adapter 使用 `device_adapters/<adapter_key>/`，插件使用 `workline_plugins/<plugin_key>/`，两者均自带 `pyproject.toml`、`src/`、`tests/` 和 `fixtures/`，
并由各自的测试入口和 CI 验收。核心测试继续按 `FAST`、`QUALITY`、`HEAVY` 分层。

测试所有权是三向隔离：核心测试不得借用具体业务或厂商场景证明基础能力；其中 Phase 2
`tests/core/outbound_http/` 只证明公共传输基础层。Adapter 测试只证明厂商合同与标准化映射；Phase 3 WMS Client 测试只证明
HTTP/JSON 访问合同，具体 WMS 业务 API 测试由对应业务模块拥有；插件测试只证明业务结果到执行 Decision 的映射和对象推进。这些测试都不得替代核心持久化、幂等、
传输、并发和恢复不变量测试。

**Tech Stack:** Python 3.13、Pytest 9、pytest-asyncio、JUnit XML、Ruff、Bandit、GitNexus、Jenkins。

> **总控阶段归属（2026-08-03）：** 本计划是
> `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
> 的 Phase 1 权威计划。Task 1、2、3、6 及 Task 4/7 的可独立治理部分已在
> `develop@28eb99d9` 合入；Task 5 和混合资产承接归 Phase 6，插件同包验收归
> Phase 7/8，旧 revision 与最终质量验收归 Phase 10/11。本计划在这些延后义务完成前
> 仍保持未完成状态。

---

## 1. 设计与调度基线

- `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`
- `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`

SPEC 负责目标架构和所有权边界；Master Plan 负责阶段调度和退出门禁；本计划只负责测试资产的归属、删除、
改写、执行分层和门禁，不实现具体工作线插件或生产执行内核。

已完成的治理基础：

- [x] 默认测试拓扑已拆分为 `FAST`、`QUALITY`、`HEAVY`。
- [x] topology guardrail、FAST JUnit 预算脚本和 HEAVY selector 已建立。
- [x] SPEC 已明确核心、WMS/厂商 Adapter 与执行插件测试的独立所有权边界。
- [x] 插件包物理结构确定为 `workline_plugins/<plugin_key>/{pyproject.toml,src,tests,fixtures}`。
- [x] Adapter 包物理结构确定为 `device_adapters/<adapter_key>/{pyproject.toml,src,tests,fixtures}`。

## 2. 锁定决策

1. WES 核心 `tests/` 只测试：
   - 最小执行对象与执行内核；
   - 通用 WorkLine 身份、拓扑、`LineRunEpoch`；
   - 设备和位置投影；
   - ECS/WMS/RCS 共享合同；
   - Phase 2 Outbound HTTP Transport 生命周期、受限响应、通用异常分类、无认证边界和脱敏日志；
   - 入站幂等、ACK/CALLBACK 分离、可靠投递、迟到证据、人工清线等通用可靠性；
   - API、Repository、数据库、部署和架构边界。
2. 下列测试不属于核心 `tests/`：
   - 粗分机、自动分拣、人工分拣、满箱交换、复杂出库等具体流程；
   - 具体插件的 config、state、handler、conformance、fixture 和场景组合；
   - 绑定具体厂商设备、命令、事件、Payload 或映射的合同；
   - 具体工作线闭环的 E2E、韧性、并发和负载测试。
3. 当前仓库中的具体插件测试直接从核心 `tests/` 删除，不把旧 Runtime/Manifest 测试原样搬到新目录，也不创建只有测试没有插件代码的空包。
4. 具体 Adapter/插件二次开发时，各自的代码、测试和 fixture 必须在同一工作包加入；任一包未通过自身测试，不得进入部署包。
5. 测试处置只使用四种语义：
   - `CORE_REWRITE`：证明通用 WES 不变量，改写到最终核心对象后保留；
   - `ADAPTER_OWNED`：证明具体厂商 DTO、协议、Payload、原始码或映射，从核心删除，随 Adapter 包重建；
   - `PLUGIN_OWNED`：证明具体工作线执行映射或执行插件行为，从核心删除，随插件包重建；
   - `LEGACY_DELETE`：只证明旧 Runtime、Manifest、Capability、Intent/Effect、Hold、Recovery、Reservation、旧迁移或兼容入口，直接删除。
6. 不建立 CSV 迁移矩阵。删除提交或 PR 描述记录分类与所有权；不得按 `legacy`、`replay`、`reconciliation` 等关键词批量删除。
7. `tests/workline_plugins/` 必须不存在；核心测试不得 import 根目录二次开发插件包或核心源码中的具体插件实现。
8. 核心默认 pytest、核心覆盖率和核心 HEAVY selector 不发现、不映射、不运行
   `workline_plugins/*/tests/` 或 `device_adapters/*/tests/`。
9. 通用插件 SPI/SDK 可以用最小 fake 在核心测试中验证，但 fake 不得承载任何真实工作线、客户或厂商规则。
10. `tests/workline_runtime/` 不是长期所有权名称。保留价值必须按最终核心对象改写到稳定领域目录；仅证明旧平台装配的测试删除。
11. 系统未发布，不保留旧测试路径 re-export、fixture alias、兼容 marker、双路径门禁或旧迁移验收。
12. 每个 commit 在精确 staging 后运行 GitNexus `detect_changes --scope all`；修改已有函数、类或方法前按仓库规则运行 impact analysis。

## 3. 最终所有权

### 3.1 核心仓库

| 测试层 | 唯一职责 | 不得承担 |
| --- | --- | --- |
| 核心领域单元测试 | 执行对象、WorkLine/Epoch、投影和可靠性状态转移 | WMS 业务决策或具体插件执行映射 |
| Phase 2 Outbound HTTP 单元测试 | `tests/core/outbound_http/` 以 `httpx.MockTransport` 和测试内 local fake 验证生命周期、请求装配、受限响应、传输事实分类和脱敏日志 | 真实外部系统、厂商 DTO/canonical/Header/认证、业务拒绝、重试/终态/恢复和大规模 E2E |
| 共享合同测试 | ECS/WMS/RCS 共享 DTO、认证、幂等、ACK/CALLBACK、错误映射 | 具体厂商 Payload 和工作线流程 |
| API 测试 | route、权限、请求响应、Service facade | Repository、插件决策和完整编排 |
| Repository/共享 Adapter 集成测试 | 数据库约束、事务和共享技术 Adapter 边界 | 厂商 wire 合同和插件场景排列 |
| 架构测试 | 分层、import、旧架构缺席、核心/插件所有权和测试拓扑 | 业务流程 |
| 核心 E2E/韧性/负载 | 只验证跨插件通用的 WES 机制 | 具体工作线闭环 |

### 3.2 厂商 Adapter 包

```text
device_adapters/<adapter_key>/
├── pyproject.toml
├── src/
├── tests/
└── fixtures/
```

Adapter 包唯一拥有：

- 具体厂商 DTO、认证差异和 wire Payload；
- 具体厂商命令、事件、ACK/CALLBACK 和原始码映射；
- 厂商合同 fixture、Mock、集成、E2E 和韧性场景；
- 标准化角色事件与逻辑动作的映射验收。

厂商 Adapter 不得拥有 WMS 业务 Decision、工作线执行映射、对象推进或具体业务流程。

### 3.3 二次开发插件包

```text
workline_plugins/<plugin_key>/
├── pyproject.toml
├── src/
├── tests/
└── fixtures/
```

插件包唯一拥有：

- 具体工作线决策表与 Handler 行为；
- 具体设备角色和现场拓扑组合；
- 插件级集成、E2E、韧性、并发和负载场景；
- 部署前插件验收结果。

插件包通过声明 WES SDK 依赖复用核心能力，只消费 Adapter 输出的标准化角色事件和逻辑动作；不声明厂商协议依赖，
不复制核心测试，也不将源码写回核心 `src/` 集成。

## 4. 核心执行重量

### 4.1 FAST

默认 `uv run pytest` 只收集核心 `tests/` 中的轻量测试。禁止真实 PostgreSQL、Redis、HTTP、Celery、Docker、多进程、主动等待和容量采样。

Phase 2 测试固定在 `tests/core/outbound_http/`，只使用 `httpx.MockTransport`、测试内 local fake 和纯单元测试；测试替身
不从 `src/core/outbound_http/` 生产包导出。WMS Client 访问测试由 Phase 3 拥有；具体 WMS/RCS/ECS 业务或 Adapter 测试由
Phase 7/8 按真实交付包分别拥有。Phase 4 核心可靠对象测试只替换类型化端口，不直接构造 Phase 2 Transport。

最终预算：

- CI 参考环境固定 2 vCPU / 4 GB；
- 核心 FAST 总时长不超过 60 秒；
- 单例不超过 3 秒；
- `tests/unit/` 在 N≥30 时 p95 不超过 100 毫秒。

以上三项预算均由质量门禁强制执行，任一超限即阻断。

插件包测试不计入核心 FAST 预算。

### 4.2 QUALITY

- `tests/architecture/`：架构、缺席、所有权和拓扑门禁；
- `tests/scripts/`：治理脚本合同；
- Ruff、Bandit、Import Linter 和静态扫描。

### 4.3 HEAVY

核心 HEAVY 只验证核心机制需要的数据库、网络、进程、故障和容量风险，目录为：

- `tests/integration/`
- `tests/e2e/`
- `tests/resilience/`
- `tests/load/`
- `tests/mock/`

具体工作线或插件 HEAVY 测试属于插件包，不得加入核心 HEAVY selector 映射。

## 5. 实施任务

### Task 1（已完成）：同步所有权文档与长期规则

**Files:**

- Modify: `TODOS.md`
- Modify: `docs/plugin_development_guide.md`
- Modify: `AGENTS.md`
- Modify: `tests/README.md`
- Modify: `docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md`

- [x] 把插件目标路径统一为仓库根目录 `workline_plugins/<plugin_key>/` 独立包。
- [x] 删除把 `tests/workline_plugins/`、具体插件和旧 orchestrator/platform 作为核心长期测试目录的规则。
- [x] 明确插件测试不进入核心 pytest、覆盖率、质量门禁或 HEAVY selector。
- [x] 将 TODO 中通用幂等与 CALLBACK fencing 标记为核心可靠性，不绑定具体插件测试路径。
- [x] 验证当前态文档无相互冲突的目标路径和测试所有权。

**Verification:**

```bash
rtk uv run pytest tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
rtk git diff --check
```

### Task 2（已完成）：建立核心/插件所有权门禁

**Files:**

- Modify: `tests/support/test_suite_topology.py`
- Modify: `tests/architecture/test_suite_topology_guardrail.py`
- Create: `tests/architecture/test_core_plugin_test_ownership_guardrail.py`
- Modify: `scripts/check_fast_test_budget.py`
- Modify: `tests/scripts/test_check_fast_test_budget.py`

- [x] topology allowlist 删除 `tests/workline_plugins`。
- [x] 门禁要求核心 `tests/workline_plugins/` 不存在。
- [x] 第一阶段门禁扫描核心测试的 import，禁止导入根目录 `workline_plugins` 包；允许只含通用 SPI/SDK 的最小 fake。
- [x] FAST 预算删除 `tests/workline_plugins/` p95 分组，只保留核心单元测试预算。
- [x] selector 合同确认插件包路径不属于核心候选或 mapping。

**Verification:**

```bash
rtk uv run pytest tests/architecture/test_suite_topology_guardrail.py \
  tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
rtk uv run pytest tests/scripts/test_check_fast_test_budget.py tests/scripts/test_select_heavy_tests.py -q
```

### Task 3（已完成）：优先移出显式插件测试目录

**Scope:**

- Delete: `tests/workline_plugins/`
- Delete: `tests/characterization/workline_legacy/`

- [x] 将目录内测试逐文件确认标记为 `PLUGIN_OWNED` 或 `LEGACY_DELETE`。
- [x] 删除目录；不复制到核心其他目录，不在没有插件代码时创建独立插件包。
- [x] 删除所有对上述测试路径的文档、脚本、fixture、收集和预算引用。
- [x] 删除提交说明记录：具体插件行为由未来对应插件包重建，核心承接为 `NONE`；通用不变量另走 `CORE_REWRITE`。

**Verification:**

```bash
! rtk git ls-files -- \
  'tests/workline_plugins/**' \
  'tests/characterization/workline_legacy/**' | rtk rg .
rtk uv run pytest tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
rtk uv run pytest --collect-only -q -o addopts=''
```

### Task 4（部分完成，等待 Task 5）：清除散落在核心目录的具体插件测试

**Scope:**

- 直接 import 粗分机、自动分拣等具体插件实现的测试；
- 以具体工作线流程、现场拓扑或插件 fixture 为唯一验收对象的合同、脚本、集成、E2E、mock 和韧性测试；
- 只证明旧插件平台、generated index、binding、manifest 或 runtime dispatch 的测试。

**Current status:** 纯 `PLUGIN_OWNED` 与可独立判断的 `LEGACY_DELETE` 测试已清理。以下混合资产仍同时承载具体插件标识和通用 WMS/入站可靠性语义，必须等 Task 5 建立最终核心对象权威测试后再拆分或删除：

- `tests/mock/wms_operation_fixtures.py`
- `tests/contracts/wms_integration/test_wms_operation_catalog.py`
- `tests/contracts/wms_integration/test_effect_status_contract.py`
- `tests/support/runtime_inbox_processing_postgresql.py`
- `tests/integration/test_runtime_inbox_processing_postgresql.py`

- [x] 生成精确候选清单并逐文件阅读，不按名称批量删除。
- [ ] `PLUGIN_OWNED`：从核心删除，未来随对应插件包按最终代码重建。
- [ ] `LEGACY_DELETE`：直接删除，并同步清理生产平台删除计划中的引用。
- [ ] 混合文件中的通用不变量标记为 `CORE_REWRITE`，先在最终核心对象上建立权威测试，再删除混合文件。
- [ ] 第二阶段扩展所有权门禁，禁止核心测试导入核心源码中的任何具体插件实现。
- [ ] 核心测试树对具体插件 import、fixture 和场景名称零命中。

**Verification:**

```bash
rtk uv run pytest tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
rtk uv run pytest tests/architecture -q
```

### Task 5（延后，随执行架构重构启动）：把通用 WorkLine 与可靠性语义改写到最终核心对象

**Entry condition:** 最终 `InboundEvidence`、`DeviceCommand`、`TransportTask`、`WmsConfirmation`、`LineRunEpoch`、设备/位置投影及其生产路径已经交付。

**Current status:** 延后执行，不属于当前测试收敛批次。截至 2026-08-03，入口条件中的最终对象和生产路径
尚未完整交付；本计划不得为完成测试迁移而越权实现生产执行内核。总控 Phase 4 负责交付最小执行对象和通用 WorkLine
新能力；Phase 5 负责生产切换和直接旧 owner 测试处置；其退出门禁通过后，Phase 6 必须执行本 Task 的剩余混合资产承接，
并由 Master Plan 的 Phase 6 任务及退出门禁直接跟踪，不在 `TODOS.md` 维护重复调度项。

**Phase 5 原子切换的同步测试义务（不属于 Task 5 交付）:**

- [ ] 先在最终 `DeviceCommand` 测试树承接持久化、claim、单设备互斥、resource wait、重试、fencing、
  ACK/CALLBACK、终态和恢复，再删除旧 `SystemOutboxDispatchType.DEVICE_COMMAND` 测试 owner；不得把旧 SystemOutbox
  包在 typed Device port 外继续测试。
- [ ] `tests/sys/test_system_outbox_engine.py`、`tests/sys/test_system_outbox_dispatch_concurrency_contract.py`、
  `tests/workline_runtime/test_system_outbox_resource_wait_contract.py`、
  `tests/workline_runtime/test_dispatch_attempt_lease_fencing.py`、
  `tests/contracts/system_capabilities/test_canonical_external_http_dispatch.py`、`tests/api/test_qa_regression_002.py`、
  `tests/api/test_workline_runtime_sse.py`、`tests/integration/test_system_outbox_repository.py`、
  `tests/integration/test_system_outbox_dispatch_concurrency.py` 和 `tests/resilience/test_runtime_scenario_replay.py` 中直接验证
  DeviceCommand SystemOutbox 的用例，全部在 Phase 5 按 `CORE_REWRITE` 映射到最终 `DeviceCommand` 权威测试后删除；仅验证
  旧 DispatchEnvelope/schema 且无最终语义的用例标记 `NONE`，其他 dispatch type 的有效断言不得误删。
- [ ] 同一 Phase 5 矩阵还必须纳入 `tests/sys/test_system_outbox_engine_boundaries.py` 对
  `_dispatch_device_command` 的直接 import，`tests/workline_runtime/test_runtime_reconciliation_idempotency.py` 和
  `tests/workline_runtime/test_workline_runtime_status_projection_service.py` 的 ACK-exhausted
  Reconciliation/SystemOutbox 路径，`tests/workline_runtime/system_capabilities/test_system_capability_effect_service.py` 的
  `DeviceCommand + SystemOutbox` 双创建断言，以及 `tests/workline_runtime/test_runtime_intent_effect_applier.py` 的
  `device-command:` Intent/Effect/Outbox 路径；缺席门禁必须按被删除符号、import 和语义扫描，不得只依赖
  `DEVICE_COMMAND` 字面量。
- [ ] `tests/workline_runtime/system_capabilities/test_device_command_authoritative_precondition.py` 的
  `prepare_runtime_effect()` 三对象写入合同、`tests/runtime/orchestration/test_command_result_correlation_authority.py` 对同一
  旧入口的 stub，以及 `tests/workline_runtime/test_external_http_workline_dispatcher.py` 对
  `_mark_device_command_failed_if_dispatch_exhausted`、`_dispatch_blocked_resource_heads`、
  `_repair_orphaned_device_busy_dispatches` 和 `_repair_self_blocked_device_busy_dispatches` 四个旧 DeviceCommand
  SystemOutbox helper 的直接 patch，也必须在 Phase 5 改接最终 `DeviceCommand` successor 或按 `NONE` 删除旧耦合；
  Phase 5 缺席门禁同步扫描这些被删除入口。
- [ ] integration/resilience 处置同步精确更新 `docs/architecture/heavy-test-impact.toml`，并显式运行受影响 HEAVY。

Task 5 入口除最终生产对象外，还要求上述 Phase 5 直接测试 owner 已全部处置且 FAST/受影响 HEAVY 可收集；Task 5 只接收
不直接引用已删除符号、但仍混合旧架构语义的剩余测试资产。

Task 5 完成前：

- Task 4 中同时包含插件行为和通用可靠性不变量的混合测试不得直接删除；必须等最终核心对象上的权威测试建立后再处置。
- Task 4 的“具体插件 import、fixture 和场景名称零命中”以及 Task 7 的最终缺席验收继续保持待办。
- 当前批次只能声明测试所有权、重量和门禁的阶段性收敛，不能声明本计划整体完成。

- [ ] 保留 WorkLine 静态身份、物理拓扑和配置校验。
- [ ] 保留 Epoch 版本冻结、人工清线和新 Epoch 恢复。
- [ ] 保留入站持久化后 ACK、同键同 Payload 幂等、冲突证据。
- [ ] 保留命令持久化、ACK/CALLBACK 分离、未知物理结果不自动重放。
- [ ] 保留设备/位置投影、单设备忙闲和位置容量。
- [ ] 保留处理幂等与 CALLBACK fencing；这些是核心可靠性，不归具体插件。
- [ ] 删除 RuntimeInbox、Intent/Effect、Capability、Manifest、Hold、Recovery、Reservation
  等旧所有者测试。

### Task 6（已完成）：收敛核心 FAST、QUALITY 和 HEAVY

- [x] 同一核心行为只保留最低稳定层的完整断言；高层只验证新增边界。
- [x] 将真实数据库、HTTP、Celery、进程、故障和容量测试移到核心 HEAVY。
- [x] 删除核心 HEAVY selector 中任何具体插件测试映射。
- [x] FAST 在固定 2 vCPU / 4 GB 环境达到 60 秒总预算和单例预算。
- [x] 质量门禁强制执行 FAST 总时长、单例和 `tests/unit/` p95 预算。

### Task 7（部分完成，等待 Task 5）：最终缺席与交付验收

- [x] 核心 `tests/workline_plugins/` 不存在。
- [ ] 核心测试不导入任何具体工作线插件或根目录二次开发插件包。
- [ ] 核心测试不导入任何具体厂商 Adapter 或根目录 `device_adapters` 二次开发包。
- [ ] 核心测试中不存在粗分机、自动分拣、人工分拣、满箱交换或复杂出库的 WMS 业务规则或插件执行映射断言。
- [ ] 只验证旧平台、旧兼容、旧迁移和旧 revision chain 的测试归零。
- [ ] 当前已交付 Adapter/插件包的代码、测试和 fixture 同包存在，并分别独立通过测试。
- [ ] 核心默认快速回归、QUALITY、受影响核心 HEAVY 和完整质量门禁全部通过。

**Core verification:**

```bash
rtk uv run pytest tests/architecture/test_suite_topology_guardrail.py \
  tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
rtk uv run pytest tests/scripts -q
rtk uv run pytest --collect-only -q -o addopts=''
rtk uv run pytest -q --junitxml=reports/fast-tests.xml
rtk uv run python scripts/check_fast_test_budget.py reports/fast-tests.xml
rtk ./scripts/git-quality-gate.sh --profile quality
```

每个已交付 Adapter/插件包另在其目录运行自己的测试入口；核心验收不代替二次开发包验收。

## 6. 删除与提交规则

每个删除提交或 PR 描述必须包含：

- 逐文件处置：`REWRITE`、`DELETE → <successor test>` 或 `DELETE → NONE + 理由`；厂商合同移交标注
  `ADAPTER_OWNED`，执行插件移交标注 `PLUGIN_OWNED`，旧实现删除标注 `LEGACY_DELETE`；
- 删除范围；
- successor 必须先在最终对象上通过，才能删除旧测试；`NONE` 必须说明该测试只验证何种已删除实现；
- 未来唯一所有者，例如 `device_adapters/<adapter_key>/tests/` 或
  `workline_plugins/rough_sorter/tests/`；
- 实际验证命令和结果。

WMS Phase 3 只新增 `wms_adapter` Client 访问测试，不处置旧 WMS 测试；旧 WMS 测试逐文件 successor/NONE 由十一阶段总控 Phase 5
和该阶段切换计划固定。HEAVY 测试移动或删除时，
同一变更必须更新 `docs/architecture/heavy-test-impact.toml`，不得留下失效路径或用臆造 NONE 掩盖风险。

禁止：

- 为保持绿灯而删除尚未承接的核心可靠性断言；
- 把 Adapter/插件测试改名后继续放在 `tests/contracts/`、`tests/workline_runtime/` 或 HEAVY 目录；
- 把旧测试原样复制到 Adapter 包或插件包；
- 仅创建测试目录而不同时交付对应 Adapter/插件代码和 fixture；
- 让核心 CI 递归发现或运行 Adapter/插件包测试。

## 7. 完成定义

只有同时满足以下条件，本计划才完成：

1. 核心 `tests/` 只证明 SPEC 定义的 WES 基础能力和通用可靠性。
2. 具体厂商合同测试全部由独立 Adapter 包拥有；具体工作线和业务测试全部由独立插件包拥有。
3. `CORE_REWRITE`、`ADAPTER_OWNED`、`PLUGIN_OWNED`、`LEGACY_DELETE` 四类处置均有可审计提交记录。
4. 核心和每个已交付 Adapter/插件包分别拥有独立、明确、可重复的测试入口。
5. 核心测试预算、架构门禁和受影响 HEAVY 验收全部通过。
