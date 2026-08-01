# WES 核心测试语义与重量收敛实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 WES 核心测试收敛到 SPEC 定义的最小执行内核、通用 WorkLine 能力、外部合同和可靠性不变量；把所有具体工作线、具体厂商和业务插件测试从核心 `tests/` 移出，随独立二次开发插件包重新交付。

**Architecture:** 核心仓库与二次开发插件包拥有不同测试边界。核心 `tests/` 不保存具体工作线流程、具体插件 Handler、具体厂商设备映射或插件 fixture；插件采用 `workline_plugins/<plugin_key>/` 独立包结构，自带 `pyproject.toml`、`src/`、`tests/` 和 `fixtures/`，并由自己的测试入口和 CI 验收。核心测试继续按 `FAST`、`QUALITY`、`HEAVY` 分层。

**Tech Stack:** Python 3.13、Pytest 9、pytest-asyncio、JUnit XML、Ruff、Bandit、GitNexus、Jenkins。

---

## 1. 唯一设计基线

- `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`
- `TODOS.md`

SPEC 负责目标架构和所有权边界；本计划只负责测试资产的归属、删除、改写、执行分层和门禁，不实现具体工作线插件或生产执行内核。

已完成的治理基础：

- [x] 默认测试拓扑已拆分为 `FAST`、`QUALITY`、`HEAVY`。
- [x] topology guardrail、FAST JUnit 预算脚本和 HEAVY selector 已建立。
- [x] SPEC 已明确核心测试与二次开发插件测试的所有权边界。
- [x] 插件包物理结构确定为 `workline_plugins/<plugin_key>/{pyproject.toml,src,tests,fixtures}`。

## 2. 锁定决策

1. WES 核心 `tests/` 只测试：
   - 最小执行对象与执行内核；
   - 通用 WorkLine 身份、拓扑、`LineRunEpoch`；
   - 设备和位置投影；
   - ECS/WMS/RCS 共享合同；
   - 入站幂等、ACK/CALLBACK 分离、可靠投递、迟到证据、人工清线等通用可靠性；
   - API、Repository、数据库、部署和架构边界。
2. 下列测试不属于核心 `tests/`：
   - 粗分机、自动分拣、人工分拣、满箱交换、复杂出库等具体流程；
   - 具体插件的 config、state、handler、conformance、fixture 和场景组合；
   - 绑定具体厂商设备、命令、事件、Payload 或映射的合同；
   - 具体工作线闭环的 E2E、韧性、并发和负载测试。
3. 当前仓库中的具体插件测试直接从核心 `tests/` 删除，不把旧 Runtime/Manifest 测试原样搬到新目录，也不创建只有测试没有插件代码的空包。
4. 具体插件二次开发时，插件代码、测试和 fixture 必须在同一个工作包加入；插件未通过自身测试，不得进入部署包。
5. 测试处置只使用三种语义：
   - `CORE_REWRITE`：证明通用 WES 不变量，改写到最终核心对象后保留；
   - `PLUGIN_OWNED`：证明具体工作线、插件或厂商行为，从核心删除，随插件包重建；
   - `LEGACY_DELETE`：只证明旧 Runtime、Manifest、Capability、Intent/Effect、Hold、Recovery、Reservation、旧迁移或兼容入口，直接删除。
6. 不建立 CSV 迁移矩阵。删除提交或 PR 描述记录分类与所有权；不得按 `legacy`、`replay`、`reconciliation` 等关键词批量删除。
7. `tests/workline_plugins/` 必须不存在；核心测试不得 import 根目录二次开发插件包或核心源码中的具体插件实现。
8. 核心默认 pytest、核心覆盖率和核心 HEAVY selector 不发现、不映射、不运行 `workline_plugins/*/tests/`。
9. 通用插件 SPI/SDK 可以用最小 fake 在核心测试中验证，但 fake 不得承载任何真实工作线、客户或厂商规则。
10. `tests/workline_runtime/` 不是长期所有权名称。保留价值必须按最终核心对象改写到稳定领域目录；仅证明旧平台装配的测试删除。
11. 系统未发布，不保留旧测试路径 re-export、fixture alias、兼容 marker、双路径门禁或旧迁移验收。
12. 每个 commit 在精确 staging 后运行 GitNexus `detect_changes --scope all`；修改已有函数、类或方法前按仓库规则运行 impact analysis。

## 3. 最终所有权

### 3.1 核心仓库

| 测试层 | 唯一职责 | 不得承担 |
| --- | --- | --- |
| 核心领域单元测试 | 执行对象、WorkLine/Epoch、投影和可靠性状态转移 | 具体插件业务决策 |
| 共享合同测试 | ECS/WMS/RCS 共享 DTO、认证、幂等、ACK/CALLBACK、错误映射 | 具体厂商 Payload 和工作线流程 |
| API 测试 | route、权限、请求响应、Service facade | Repository、插件决策和完整编排 |
| Repository/Adapter 集成测试 | 数据库约束、事务和共享 Adapter 边界 | 插件场景排列 |
| 架构测试 | 分层、import、旧架构缺席、核心/插件所有权和测试拓扑 | 业务流程 |
| 核心 E2E/韧性/负载 | 只验证跨插件通用的 WES 机制 | 具体工作线闭环 |

### 3.2 二次开发插件包

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
- 具体厂商 DTO、命令、事件和映射；
- 插件级集成、E2E、韧性、并发和负载场景；
- 部署前插件验收结果。

插件包通过声明 WES SDK 依赖复用核心能力，不通过复制核心测试或将源码写回核心 `src/` 集成。

## 4. 核心执行重量

### 4.1 FAST

默认 `uv run pytest` 只收集核心 `tests/` 中的轻量测试。禁止真实 PostgreSQL、Redis、HTTP、Celery、Docker、多进程、主动等待和容量采样。

最终预算：

- CI 参考环境固定 2 vCPU / 4 GB；
- 核心 FAST 总时长不超过 60 秒；
- 单例不超过 1 秒；
- `tests/unit/` 在 N≥30 时 p95 不超过 100 毫秒。

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

### Task 1：同步所有权文档与长期规则

**Files:**

- Modify: `TODOS.md`
- Modify: `docs/plugin_development_guide.md`
- Modify: `AGENTS.md`
- Modify: `tests/README.md`
- Modify: `docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md`

- [ ] 把插件目标路径统一为仓库根目录 `workline_plugins/<plugin_key>/` 独立包。
- [ ] 删除把 `tests/workline_plugins/`、具体插件和旧 orchestrator/platform 作为核心长期测试目录的规则。
- [ ] 明确插件测试不进入核心 pytest、覆盖率、质量门禁或 HEAVY selector。
- [ ] 将 TODO 中通用幂等与 CALLBACK fencing 标记为核心可靠性，不绑定具体插件测试路径。
- [ ] 验证当前态文档无相互冲突的目标路径和测试所有权。

**Verification:**

```bash
uv run pytest tests/contracts/wms_integration/test_release_removal_guardrails.py -q
git diff --check
```

### Task 2：建立核心/插件所有权门禁

**Files:**

- Modify: `tests/support/test_suite_topology.py`
- Modify: `tests/architecture/test_suite_topology_guardrail.py`
- Create: `tests/architecture/test_core_plugin_test_ownership_guardrail.py`
- Modify: `scripts/check_fast_test_budget.py`
- Modify: `tests/scripts/test_check_fast_test_budget.py`

- [ ] topology allowlist 删除 `tests/workline_plugins`。
- [ ] 门禁要求核心 `tests/workline_plugins/` 不存在。
- [ ] 第一阶段门禁扫描核心测试的 import，禁止导入根目录 `workline_plugins` 包；允许只含通用 SPI/SDK 的最小 fake。
- [ ] FAST 预算删除 `tests/workline_plugins/` p95 分组，只保留核心单元测试预算。
- [ ] selector 合同确认插件包路径不属于核心候选或 mapping。

**Verification:**

```bash
uv run pytest tests/architecture/test_suite_topology_guardrail.py \
  tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
uv run pytest tests/scripts/test_check_fast_test_budget.py tests/scripts/test_select_heavy_tests.py -q
```

### Task 3：优先移出显式插件测试目录

**Scope:**

- Delete: `tests/workline_plugins/`
- Delete: `tests/characterization/workline_legacy/`

- [ ] 将目录内测试逐文件确认标记为 `PLUGIN_OWNED` 或 `LEGACY_DELETE`。
- [ ] 删除目录；不复制到核心其他目录，不在没有插件代码时创建独立插件包。
- [ ] 删除所有对上述测试路径的文档、脚本、fixture、收集和预算引用。
- [ ] 删除提交说明记录：具体插件行为由未来对应插件包重建，核心承接为 `NONE`；通用不变量另走 `CORE_REWRITE`。

**Verification:**

```bash
test ! -d tests/workline_plugins
test ! -d tests/characterization/workline_legacy
uv run pytest tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
uv run pytest --collect-only -q -o addopts=''
```

### Task 4：清除散落在核心目录的具体插件测试

**Scope:**

- 直接 import 粗分机、自动分拣等具体插件实现的测试；
- 以具体工作线流程、现场拓扑或插件 fixture 为唯一验收对象的合同、脚本、集成、E2E、mock 和韧性测试；
- 只证明旧插件平台、generated index、binding、manifest 或 runtime dispatch 的测试。

- [ ] 生成精确候选清单并逐文件阅读，不按名称批量删除。
- [ ] `PLUGIN_OWNED`：从核心删除，未来随对应插件包按最终代码重建。
- [ ] `LEGACY_DELETE`：直接删除，并同步清理生产平台删除计划中的引用。
- [ ] 混合文件中的通用不变量标记为 `CORE_REWRITE`，先在最终核心对象上建立权威测试，再删除混合文件。
- [ ] 第二阶段扩展所有权门禁，禁止核心测试导入核心源码中的任何具体插件实现。
- [ ] 核心测试树对具体插件 import、fixture 和场景名称零命中。

**Verification:**

```bash
uv run pytest tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
uv run pytest tests/architecture -q
```

### Task 5：把通用 WorkLine 与可靠性语义改写到最终核心对象

**Entry condition:** 最终 `InboundEvidence`、`DeviceCommand`、`TransportTask`、`WmsConfirmation`、`LineRunEpoch`、设备/位置投影及其生产路径已经交付。

- [ ] 保留 WorkLine 静态身份、物理拓扑和配置校验。
- [ ] 保留 Epoch 版本冻结、人工清线和新 Epoch 恢复。
- [ ] 保留入站持久化后 ACK、同键同 Payload 幂等、冲突证据。
- [ ] 保留命令持久化、ACK/CALLBACK 分离、未知物理结果不自动重放。
- [ ] 保留设备/位置投影、单设备忙闲和位置容量。
- [ ] 保留处理幂等与 CALLBACK fencing；这些是核心可靠性，不归具体插件。
- [ ] 删除 RuntimeInbox、Intent/Effect、Capability、Manifest、Hold、Recovery、Reservation 等旧所有者测试。

### Task 6：收敛核心 FAST、QUALITY 和 HEAVY

- [ ] 同一核心行为只保留最低稳定层的完整断言；高层只验证新增边界。
- [ ] 将真实数据库、HTTP、Celery、进程、故障和容量测试移到核心 HEAVY。
- [ ] 删除核心 HEAVY selector 中任何具体插件测试映射。
- [ ] FAST 在固定 2 vCPU / 4 GB 环境达到 60 秒总预算和单例预算。
- [ ] 质量门禁由 `--report-only` 切到强制预算模式。

### Task 7：最终缺席与交付验收

- [ ] 核心 `tests/workline_plugins/` 不存在。
- [ ] 核心测试不导入任何具体工作线插件或根目录二次开发插件包。
- [ ] 核心测试中不存在粗分机、自动分拣、人工分拣、满箱交换或复杂出库的业务规则断言。
- [ ] 只验证旧平台、旧兼容、旧迁移和旧 revision chain 的测试归零。
- [ ] 当前已交付插件包的代码、测试和 fixture 同包存在，并分别独立通过测试。
- [ ] 核心默认快速回归、QUALITY、受影响核心 HEAVY 和完整质量门禁全部通过。

**Core verification:**

```bash
uv run pytest tests/architecture/test_suite_topology_guardrail.py \
  tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
uv run pytest tests/scripts -q
uv run pytest --collect-only -q -o addopts=''
uv run pytest -q --junitxml=reports/fast-tests.xml
uv run python scripts/check_fast_test_budget.py reports/fast-tests.xml
./scripts/git-quality-gate.sh --profile quality
```

每个已交付插件包另在其目录运行自己的测试入口；核心验收不代替插件验收。

## 6. 删除与提交规则

每个删除提交或 PR 描述必须包含：

- 分类：`PLUGIN_OWNED`、`LEGACY_DELETE` 或已完成承接的 `CORE_REWRITE`；
- 删除范围；
- 核心承接测试路径，或明确 `NONE`；
- 未来插件所有者，例如 `workline_plugins/rough_sorter/tests/`；
- 实际验证命令和结果。

禁止：

- 为保持绿灯而删除尚未承接的核心可靠性断言；
- 把插件测试改名后继续放在 `tests/contracts/`、`tests/workline_runtime/` 或 HEAVY 目录；
- 把旧测试原样复制到插件包；
- 仅创建插件测试目录而不同时交付插件代码和 fixture；
- 让核心 CI 递归发现或运行插件包测试。

## 7. 完成定义

只有同时满足以下条件，本计划才完成：

1. 核心 `tests/` 只证明 SPEC 定义的 WES 基础能力和通用可靠性。
2. 具体工作线、具体插件和具体厂商测试全部由独立二次开发包拥有。
3. `CORE_REWRITE`、`PLUGIN_OWNED`、`LEGACY_DELETE` 三类处置均有可审计提交记录。
4. 核心和每个已交付插件包分别拥有独立、明确、可重复的测试入口。
5. 核心测试预算、架构门禁和受影响 HEAVY 验收全部通过。
