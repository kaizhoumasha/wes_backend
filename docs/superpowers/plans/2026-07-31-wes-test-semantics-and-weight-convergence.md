# WES 测试语义与重量收敛实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前测试套件收敛为以 WES 最小执行架构 SPEC 为唯一语义基线、默认执行轻量且每项行为只有一个明确测试所有者的最终测试体系。

**Architecture:** 先用逐文件处置矩阵冻结现状和目标归属，再以目标垂直切片为单位先建立最终对象测试、后删除旧架构测试。测试按 `FAST`、`QUALITY`、`HEAVY` 三个执行层分离；语义处置与执行重量正交管理，避免用移动目录掩盖旧行为，也避免因删除旧实现而丢失可靠性不变量。

**Tech Stack:** Python 3.13、Pytest 9、pytest-asyncio、JUnit XML、Ruff、Bandit、GitNexus、Jenkins。

---

## 1. 实施基线

唯一设计基线：

- `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`

计划编写时的测试快照：

| 指标 | 当前值 |
| --- | ---: |
| `test_*.py` 文件 | 459 |
| 测试代码行 | 141,923 |
| 默认收集用例 | 5,390 |
| `tests/workline_runtime/` 文件 | 79 |
| `tests/contracts/` 文件 | 61 |
| `tests/integration/` 文件 | 44 |
| `tests/architecture/` 文件 | 41 |
| `tests/runtime/` 文件 | 35 |

以上数字只记录起点，不作为最终保留数量。最终判断标准是目标业务语义完整、重复所有权消失、旧架构测试归零和执行预算达标，不能为了追求数量减少而删除有价值的成功、失败或幂等断言。

## 2. 已锁定决策

1. 测试只为最终 SPEC 定义的业务合同、领域行为、可靠性不变量和架构边界负责，不为当前实现结构背书。
2. 不保留旧 Runtime、Manifest、System Capability、Intent/Effect、RuntimeHold、Reconciliation、CellReservation、旧迁移链或兼容入口的测试。
3. 删除测试前必须完成逐文件语义判断；不得按 `replay`、`legacy`、`reconciliation` 等关键词批量删除。
4. 目标对象尚未建立时，不提前批量删除仍承载可靠性不变量的旧测试；先建立目标测试并通过，再删除对应旧测试。
5. 语义处置与重量处置分开记录。把旧架构测试移动到重测试目录不等于完成语义收敛。
6. 同一行为只有一个主要测试所有者。其他层只验证本层边界，不重复领域决策的完整排列组合。
7. 默认 `pytest` 只运行 `FAST`；`QUALITY` 由质量门禁显式运行一次；`HEAVY` 按受影响目录和环境显式运行。
8. 未发布系统不保留旧测试命令、旧 gate profile、旧 fixture alias、旧测试路径 re-export 或迁移兼容。
9. 不以全仓覆盖率百分比决定测试去留。覆盖率用于发现盲区，不能替代业务语义所有权。
10. 收敛矩阵是实施期控制面，全部行关闭后删除；最终仓库只保留长期测试治理和永久缺席门禁。

## 3. 最终测试所有权

| 测试层 | 唯一职责 | 不得承担 |
| --- | --- | --- |
| 领域/插件单元测试 | 角色化输入到封闭 `Decision`、状态转移、成功和失败分支 | ORM、HTTP、Celery、真实数据库、跨模块装配 |
| 合同测试 | ECS/WMS/RCS DTO、幂等键、Payload Hash、ACK/CALLBACK 分离、错误映射 | 工作线完整流程、Repository 实现细节 |
| API 测试 | route、认证授权、请求响应模型、Service facade 调用 | Repository、投影、插件决策和编排内部状态 |
| Repository/Adapter 集成测试 | 数据库约束、事务、序列化、真实 Adapter 边界 | 重复领域规则的组合测试 |
| 架构测试 | import、依赖方向、旧架构缺席、测试拓扑和文档约束 | 业务流程和性能循环 |
| E2E 测试 | 少量代表性跨组件闭环 | 穷举领域分支 |
| 韧性测试 | 断连、超时、重启、迟到回调、人工恢复边界 | 常规成功路径的重复覆盖 |
| 负载测试 | 吞吐、并发和容量预算 | 功能正确性 |

当一个断言可在更低层稳定证明时，高层测试只保留该层新增的集成风险。业务 fixture 可以复用，但不得把同一行为在 API、Service、Repository、E2E 各复制一遍。

## 4. 执行重量分层

### 4.1 `FAST`：默认快速回归

允许：

- 纯函数、领域对象、插件 handler、类型化端口和轻量 fake。
- API facade、Schema、错误映射和不启动外部进程的 Service 单元测试。
- 小范围临时文件和确定性 fixture。

禁止：

- PostgreSQL、Redis、真实 HTTP、Celery Worker、Docker 或其他外部服务。
- `subprocess`、主动 `sleep`、轮询等待、全仓文件扫描。
- Alembic upgrade/downgrade、真实并发、性能采样和大规模参数排列。

最终预算：

- 参考 CI 容器中默认套件总耗时不超过 60 秒。
- 默认套件中单个用例不超过 1 秒。
- `tests/unit/` 与 `tests/workline_plugins/` 中的纯领域/插件单元测试 p95 不超过 100 毫秒。
- 运行结果可重复，不依赖测试执行顺序和本机遗留状态。

### 4.2 `QUALITY`：显式质量门禁

内容：

- `tests/architecture/` 下的 import、依赖、缺席、文档和测试拓扑检查。
- Ruff、Bandit、Import Linter 和稳定的静态扫描。

约束：

- 不进入默认 `pytest`。
- 质量门禁只以一次 `pytest tests/architecture -q` 执行全部架构测试。
- 同一规则只能有一个权威实现；Shell 扫描、Python guardrail 和 Pytest 断言不得重复维护同一名单。

### 4.3 `HEAVY`：显式重测试

目录：

- `tests/integration/`
- `tests/e2e/`
- `tests/resilience/`
- `tests/load/`
- `tests/mock/`

进入条件：

- 需要真实数据库、Redis、HTTP、Celery、Docker、多进程、并发、断连、时间等待或容量采样。
- 只有该层能证明的数据库、网络、进程或系统级风险。

重测试不设统一的本地全跑入口。每个实现工作包必须声明受影响目录、环境前置条件和实际运行命令。

## 5. 逐文件处置矩阵

实施期创建 `docs/architecture/wes-test-convergence-matrix.csv`，每个当前 `test_*.py` 文件恰好一行，字段固定为：

| 字段 | 约束 |
| --- | --- |
| `test_path` | 处置前测试文件路径，唯一；`OPEN` 时必须存在，关闭改写或删除后允许不存在 |
| `current_semantics` | 当前实际证明的行为，不写文件名复述 |
| `target_capability` | SPEC 中的目标合同、对象、业务流程或 `NONE` |
| `behavior_owner` | 最终唯一测试层和领域 |
| `semantic_action` | `KEEP`、`REWRITE`、`SPLIT`、`REPLACE_GATE`、`DELETE` |
| `target_path` | 保留或替换后的确切路径；`DELETE` 使用空值 |
| `execution_tier` | `FAST`、`QUALITY`、`HEAVY`；`DELETE` 使用空值 |
| `duplicate_of` | 被合并到的权威测试路径；不存在重复时为空 |
| `work_package` | `BASELINE`、`CORE_ROUGH_SORTER`、`AUTO_INBOUND`、`MANUAL_FOUR_LINE`、`OUTBOUND_EXCHANGE`、`FINAL_REMOVAL` |
| `deletion_gate` | 删除前必须已通过的目标测试或永久门禁路径 |
| `evidence_note` | 保留、改写或删除的具体理由 |
| `status` | `OPEN` 或 `CLOSED`；目标路径和删除门禁通过后才能关闭 |

语义动作定义：

- `KEEP`：已经直接使用目标对象并证明最终行为，仅允许去重和轻量化。
- `REWRITE`：业务语义保留，但测试必须从旧对象重写到最终对象。
- `SPLIT`：同一文件混合目标行为与旧实现断言；目标行为迁入权威路径，旧断言删除。
- `REPLACE_GATE`：旧架构存在性或旧平台形状测试，替换为最终架构缺席或依赖边界门禁。
- `DELETE`：只验证旧架构、兼容、迁移链、实现细节，或与权威测试完全重复。

矩阵不能使用模糊说明。每个 `REWRITE`、`SPLIT`、`REPLACE_GATE` 行必须给出确切 `target_path` 和 `deletion_gate`；每个 `DELETE` 行必须说明为什么不存在需要迁移的目标行为。实施中新增的测试文件也必须作为既有 `target_path` 落地，或新增独立矩阵行；矩阵自身的临时 guardrail 标为 `DELETE`，在 `FINAL_REMOVAL` 关闭。

## 6. 文件职责图

实施期治理文件：

| 文件 | 职责 |
| --- | --- |
| `docs/architecture/wes-test-convergence-matrix.csv` | 临时逐文件语义、所有权、重量和删除门禁 |
| `scripts/check_test_convergence_matrix.py` | 验证矩阵覆盖、枚举值、路径唯一性和关闭状态 |
| `tests/architecture/test_test_convergence_matrix_guardrail.py` | 通过脚本接口验证矩阵和新增测试不会绕过归类 |
| `tests/support/test_suite_topology.py` | 最终目录层级、默认排除目录和文件体量规则 |
| `tests/architecture/test_test_suite_topology_guardrail.py` | 验证最终测试拓扑与默认收集边界 |
| `scripts/check_fast_test_budget.py` | 从 JUnit XML 验证默认套件总耗时、单例耗时和纯单元 p95 |
| `tests/scripts/test_check_fast_test_budget.py` | 预算解析、边界值和失败输出的单元测试 |
| `scripts/check_business_legacy_absence_gate.py` | 最终旧架构符号、import、配置和 fallback 的单一缺席扫描 |
| `tests/architecture/test_business_legacy_absence_guardrail.py` | 缺席扫描的规则合同 |
| `scripts/git-quality-gate.sh` | 只编排静态检查、一次架构测试和一次默认快速回归 |
| `pyproject.toml` | Pytest 默认只收集 `FAST` |
| `Jenkinsfile` | 分别执行架构门禁、默认快速回归和受环境支持的重测试 |
| `tests/README.md` | 最终测试所有权、目录、预算和运行方式 |
| `AGENTS.md` | Agent 新增和修改测试时的长期硬约束 |

目标业务测试路径由矩阵锁定，并随对应生产实现工作包一起创建或改写。矩阵完成前不得开始目标运行时代码修改。

## 7. 实施任务

### Task 1：建立完整语义与重量清单

**Files:**

- Create: `docs/architecture/wes-test-convergence-matrix.csv`
- Create: `scripts/check_test_convergence_matrix.py`
- Create: `tests/architecture/test_test_convergence_matrix_guardrail.py`
- Modify: `tests/support/test_suite_topology.py`

- [ ] **Step 1：冻结现场快照**

运行：

```bash
rtk proxy find tests -type f -name 'test_*.py' | wc -l
rtk proxy find tests -type f -name 'test_*.py' -print0 | xargs -0 wc -l | tail -n 1
rtk uv run pytest --collect-only -q -o addopts=''
```

期望：保存执行日期、文件数、行数、默认收集数和各一级目录文件数；不把数量写入长期 `tests/README.md`。

- [ ] **Step 2：逐文件填写矩阵**

按测试内容而非文件名判断，确保每个当前测试文件恰好一行。优先审查包含 `RuntimeInbox`、`RuntimeIntent`、`RuntimeHold`、`ExecutionSession`、`WorklineSession`、`PluginBinding`、`SystemCapability`、`CellReservation`、`Reconciliation`、`generated_index` 和 `SystemOutbox` 的文件，但这些词只用于排序，不直接决定删除。

- [ ] **Step 3：实现矩阵校验**

脚本必须验证：

- 当前全部 `test_*.py` 路径被开放的 `test_path` 或已经落地的 `target_path` 覆盖。
- `semantic_action`、`execution_tier`、`work_package`、`status` 只使用固定枚举。
- 非 `DELETE` 行具有确切目标路径和测试层。
- `REWRITE`、`SPLIT`、`REPLACE_GATE` 行具有删除门禁。
- `OPEN` 行的 `test_path` 必须存在；`CLOSED` 非删除行的 `target_path` 必须存在。
- 新增测试若不是矩阵行或已声明目标路径，门禁失败。

- [ ] **Step 4：验证矩阵门禁**

运行：

```bash
rtk uv run pytest tests/architecture/test_test_convergence_matrix_guardrail.py -q
rtk uv run python scripts/check_test_convergence_matrix.py
```

期望：矩阵完整通过；故意遗漏一个临时 fixture 时合同测试能够证明脚本失败，恢复 fixture 后再次通过。

- [ ] **Step 5：提交基线清单**

```bash
rtk git add docs/architecture/wes-test-convergence-matrix.csv scripts/check_test_convergence_matrix.py tests/architecture/test_test_convergence_matrix_guardrail.py tests/support/test_suite_topology.py
rtk git commit -m "docs(test): 建立测试语义与重量收敛清单"
```

### Task 2：建立三层执行拓扑和速度预算

**Files:**

- Modify: `pyproject.toml`
- Modify: `tests/support/test_suite_topology.py`
- Modify: `tests/architecture/test_test_suite_topology_guardrail.py`
- Create: `scripts/check_fast_test_budget.py`
- Create: `tests/scripts/test_check_fast_test_budget.py`
- Modify: `tests/README.md`

- [ ] **Step 1：先写失败的拓扑合同**

增加以下断言：

- `tests/architecture/` 与五个重测试目录不进入默认收集。
- `FAST` 路径不包含矩阵中标记为 `QUALITY` 或 `HEAVY` 的文件。
- 新测试不得出现在 `tests/` 根目录。
- 默认测试文件不能依赖真实服务、主动等待或子进程；发现后必须移动到矩阵指定的重测试路径。

- [ ] **Step 2：先写失败的预算脚本测试**

`scripts/check_fast_test_budget.py` 只使用标准库解析 JUnit XML。实施期报告接口为：

```bash
rtk uv run python scripts/check_fast_test_budget.py reports/fast-tests.xml --report-only
```

默认阈值固定为套件 60 秒、单例 1 秒、`tests/unit/` 与 `tests/workline_plugins/` p95 100 毫秒；脚本必须明确打印超限用例和实际值。省略 `--report-only` 时超限退出非零，最终质量门禁只使用强制模式。

- [ ] **Step 3：收敛 Pytest 默认收集**

在 `pyproject.toml` 中把 `tests/architecture` 加入 `norecursedirs`。真实数据库、HTTP、Celery、并发、等待和性能测试按矩阵移动到现有五个重测试目录；不得通过 marker 让重测试继续混在 `FAST` 路径。

- [ ] **Step 4：更新测试指南**

`tests/README.md` 改为本计划第 3、4 节的最终所有权和运行方式，删除把 `tests/workline_runtime/`、orchestrator、intent、session resolver 和插件模板作为长期目标的说明。

- [ ] **Step 5：验证默认层**

运行：

```bash
rtk uv run pytest --collect-only -q -o addopts=''
rtk uv run pytest -q --junitxml=reports/fast-tests.xml
rtk uv run python scripts/check_fast_test_budget.py reports/fast-tests.xml --report-only
rtk uv run pytest tests/architecture/test_test_suite_topology_guardrail.py tests/scripts/test_check_fast_test_budget.py -q
```

期望：默认收集不含 `QUALITY` 和 `HEAVY`；此阶段允许总时长暂未达到最终预算，但每个超限文件必须已在矩阵中标为后续改写、合并、移动或删除，不能加入永久豁免。

- [ ] **Step 6：提交执行拓扑**

```bash
rtk git add pyproject.toml tests/support/test_suite_topology.py tests/architecture/test_test_suite_topology_guardrail.py scripts/check_fast_test_budget.py tests/scripts/test_check_fast_test_budget.py tests/README.md
rtk git commit -m "test(governance): 建立快速质量重测试分层"
```

### Task 3：用最终执行对象替换核心 Runtime 测试

**Scope:**

- `tests/runtime/`
- `tests/workline_runtime/`
- `tests/contracts/workline/`
- `tests/callback/`
- 与上述目录重复的 `tests/unit/runtime/` 和架构测试

- [ ] **Step 1：按矩阵提取 `CORE_ROUGH_SORTER` 行**

逐行确认最终所有者覆盖：

- `InboundEvidence` 的先持久化后 ACK、重复 Payload 和冲突证据。
- `DeviceCommand` 的持久化、ACK/CALLBACK 分离、设备忙闲和未知结果不自动重放。
- `TransportTask` 的请求、成员进度和批次结果。
- `WmsConfirmation` 的待确认事实、重试和依赖恢复。
- `LineRunEpoch`、具体 Execution、位置投影和设备投影。
- 粗分机 13 类入站判定、设备忙等待、成功 CALLBACK 和失败结果。

- [ ] **Step 2：先建立目标测试并观察失败**

每个可靠性不变量先在最终对象或端口上形成一个权威测试文件。旧测试可以提供输入和预期结果，但不得复制旧 fixture、旧状态枚举或旧服务装配。

- [ ] **Step 3：目标实现通过后删除对应旧测试**

删除只验证 RuntimeInbox 生命周期、RuntimeIntent/Effect、ExecutionSession、Plugin Binding、System Capability、RuntimeHold 和 generated index 的行。混合文件先迁移目标断言，再删除旧文件。

- [ ] **Step 4：消除跨层重复**

同一幂等、状态推进或插件决策只保留最低稳定层的完整断言；API、Repository 和集成层只保留自身新增风险。

- [ ] **Step 5：验证核心切片**

运行矩阵中 `CORE_ROUGH_SORTER` 的全部 `target_path`，然后运行：

```bash
rtk uv run pytest -q --junitxml=reports/fast-tests.xml
rtk uv run pytest tests/architecture -q
rtk uv run python scripts/check_test_convergence_matrix.py
```

期望：该工作包的矩阵行全部关闭，旧路径和目标路径不并存证明同一语义。

- [ ] **Step 6：提交核心测试替换**

只 stage 本工作包矩阵行列出的生产和测试路径，核对暂存 diff 后提交：

```bash
rtk git commit -m "test(execution): 收敛最小执行对象与粗分机测试"
```

### Task 4：收敛自动分拣入库与资源测试

**Scope:**

- `tests/resource/`
- `tests/active_objects/`
- `tests/reconciliation/`
- 自动分拣相关的 `tests/workline_runtime/`、`tests/contracts/` 和重测试

- [ ] **Step 1：保留最终资源不变量**

目标测试必须覆盖位置单一占用、设备忙闲、LIFO 货架投影、WMS 授权范围和放料前即时目标格选择。

- [ ] **Step 2：删除提前预约语义**

删除 `CellReservation`、TTL、提前锁格、自动 reconciliation 和推测式恢复测试；不得将其改名后继续保留。

- [ ] **Step 3：改写混合资源测试**

若当前测试同时证明有效的单一占用和旧 `RECONCILING` 状态，拆出单一占用目标断言，删除瞬态自动修复断言。

- [ ] **Step 4：分离真实数据库与并发**

纯目标选择和投影规则保留在 `FAST`；唯一约束、事务冲突和真实并发移动到 `tests/integration/`。

- [ ] **Step 5：验证工作包**

运行矩阵中 `AUTO_INBOUND` 的全部目标路径、受影响集成目录、默认快速回归、架构测试和矩阵检查。

- [ ] **Step 6：提交自动入库测试替换**

只 stage `AUTO_INBOUND` 矩阵行列出的确切路径，核对暂存 diff 后提交：

```bash
rtk git commit -m "test(workline): 收敛自动分拣入库与资源测试"
```

### Task 5：收敛人工分拣与四线约束测试

**Scope:**

- 人工分拣、四线串联、NG、owner workline 和 CTU 退料相关测试

- [ ] **Step 1：建立唯一业务所有者**

目标插件/领域测试覆盖业务 NG、硬件故障、依赖暂停、人工完成、不可变 `owner_workline_id`、NG 跨线直行和非 NG 同线进出。

- [ ] **Step 2：删除平台化替代语义**

删除 RuntimeHold、Reconciliation、SorterCorridor、跨线执行引擎和错误重新绑定测试。

- [ ] **Step 3：压缩场景组合**

相同行为使用参数化 fixture 共享输入和期望结果；API/E2E 不重复穷举插件层已证明的四线排列。

- [ ] **Step 4：验证工作包**

运行矩阵中 `MANUAL_FOUR_LINE` 的全部目标路径、受影响 E2E/韧性测试、默认快速回归、架构测试和矩阵检查。

- [ ] **Step 5：提交人工线测试替换**

只 stage `MANUAL_FOUR_LINE` 矩阵行列出的确切路径，核对暂存 diff 后提交：

```bash
rtk git commit -m "test(workline): 收敛人工分拣与四线约束测试"
```

### Task 6：收敛复杂出库与满箱交换测试

**Scope:**

- 出库来源、单层/五层/退货/转运货架、CTU 批次、满箱交换和 WMS 确认相关测试

- [ ] **Step 1：建立目标流程测试**

领域测试覆盖 WMS 来源分配、选择格口、出料、组盘/组箱、`TransportTask` 成员进度、满箱交换和 WMS 确认义务。

- [ ] **Step 2：限制 E2E 数量**

每类主要物理闭环保留一个代表性 E2E；来源类型和业务拒绝的排列在领域或合同层参数化覆盖。

- [ ] **Step 3：改写可靠性测试**

WMS 不可用、迟到 CALLBACK、确认重试和人工清线进入 `tests/resilience/`；删除 RuntimeHold、通用 Outbox 和自动 replay 所有权。

- [ ] **Step 4：验证工作包**

运行矩阵中 `OUTBOUND_EXCHANGE` 的全部目标路径、受影响 integration/e2e/resilience 目录、默认快速回归、架构测试和矩阵检查。

- [ ] **Step 5：提交出库测试替换**

只 stage `OUTBOUND_EXCHANGE` 矩阵行列出的确切路径，核对暂存 diff 后提交：

```bash
rtk git commit -m "test(workline): 收敛复杂出库与满箱交换测试"
```

### Task 7：删除旧平台、旧迁移和旧门禁

**Files:**

- Modify: `scripts/check_business_legacy_absence_gate.py`
- Modify: `tests/architecture/test_business_legacy_absence_guardrail.py`
- Modify/Delete: 矩阵中 `FINAL_REMOVAL` 和 `REPLACE_GATE` 的确切路径
- Delete: 只验证旧 revision chain、upgrade/downgrade、数据回填的测试

- [ ] **Step 1：确认可靠性替换闭合**

只有当目标 `InboundEvidence`、`DeviceCommand`、`TransportTask`、`WmsConfirmation`、具体 Execution 和人工清线测试均通过时，才删除最后一批旧 Runtime 测试。

- [ ] **Step 2：合并架构门禁**

将旧 Runtime、Manifest、Capability、Intent/Effect、Hold、Recovery、Reservation、兼容 import、alias 和 fallback 缺席统一到 `scripts/check_business_legacy_absence_gate.py`。删除重复的阶段性 guardrail 和旧平台存在性测试。

- [ ] **Step 3：删除旧迁移测试**

最终模型稳定并生成单一 Alembic 基线后，只保留空库 `upgrade head`、metadata、约束、索引和 TimescaleDB 扩展对象验证；删除旧 revision chain、数据转换和 downgrade 测试。

- [ ] **Step 4：验证永久缺席**

运行：

```bash
rtk uv run python scripts/check_business_legacy_absence_gate.py --mode final
rtk uv run pytest tests/architecture/test_business_legacy_absence_guardrail.py -q
rtk uv run python scripts/check_test_convergence_matrix.py
```

期望：所有 `FINAL_REMOVAL` 行关闭，生产代码、测试、配置和当前态文档不存在旧架构入口；不存在永久 allowlist。

- [ ] **Step 5：提交最终删除**

只 stage `FINAL_REMOVAL` 矩阵行、最终基线和缺席门禁列出的确切路径，核对暂存 diff 后提交：

```bash
rtk git commit -m "refactor(test): 删除旧平台与迁移测试"
```

### Task 8：收敛质量门禁和 CI 执行

**Files:**

- Modify: `scripts/git-quality-gate.sh`
- Modify: `scripts/architecture-guardrails.sh`
- Modify: `Jenkinsfile`
- Modify: `AGENTS.md`
- Modify: `tests/README.md`

- [ ] **Step 1：删除旧 Runtime 专用 gate 编排**

从 `scripts/git-quality-gate.sh` 删除硬编码 `tests/runtime/orchestration/`、`tests/contracts/workline/`、`tests/characterization/workline_legacy/`、旧 readiness/closure 和单文件 architecture 调用。

- [ ] **Step 2：建立单一质量路径**

本地 `quality` profile 顺序固定为：

1. Ruff format。
2. Ruff lint。
3. Bandit。
4. Import Linter。
5. 最终架构和旧架构缺席扫描。
6. 一次 `pytest tests/architecture -q`。
7. 一次默认 `pytest` 并生成 JUnit XML。
8. 快速测试预算检查。

删除不再有独立价值的 `ci-smoke`、`full` 和旧 Runtime 专用 check 名称，不保留命令兼容分支。

- [ ] **Step 3：更新 Jenkins**

`Architecture Guardrails` stage 只执行最终静态门禁和一次 architecture suite；`Unit Tests` stage 只执行默认 `FAST` 并检查 JUnit 预算。真实基础设施支持的 integration/e2e/resilience/load/mock 由独立 stage 或显式任务运行，不伪装成 Unit Tests。

- [ ] **Step 4：同步长期规则**

`AGENTS.md` 和 `tests/README.md` 使用最终对象、测试层和速度预算；删除 Runtime service、orchestrator、intent、session resolver、插件模板和固定旧路径说明。

- [ ] **Step 5：验证门禁没有重复执行**

运行：

```bash
rtk ./scripts/git-quality-gate.sh --profile quality
rtk rg -n "tests/runtime/orchestration|tests/contracts/workline|tests/characterization/workline_legacy|runtime-contract-guardrails" scripts/git-quality-gate.sh Jenkinsfile AGENTS.md tests/README.md
```

期望：质量门禁通过；第二条命令零命中；默认测试和 architecture suite 各只执行一次。

- [ ] **Step 6：提交门禁收敛**

```bash
rtk git add scripts/git-quality-gate.sh scripts/architecture-guardrails.sh Jenkinsfile AGENTS.md tests/README.md
rtk git commit -m "ci(test): 收敛测试门禁与执行预算"
```

### Task 9：关闭并移除实施期矩阵

**Files:**

- Delete: `docs/architecture/wes-test-convergence-matrix.csv`
- Delete: `scripts/check_test_convergence_matrix.py`
- Delete: `tests/architecture/test_test_convergence_matrix_guardrail.py`
- Modify: `tests/support/test_suite_topology.py`
- Modify: `tests/architecture/test_test_suite_topology_guardrail.py`

- [ ] **Step 1：验证矩阵全部关闭**

矩阵中不得存在仍指向旧路径的 `REWRITE`、`SPLIT`、`REPLACE_GATE` 行；所有非删除目标路径必须存在且由最终测试层收集。

- [ ] **Step 2：删除临时控制面**

删除矩阵、矩阵检查脚本和矩阵专用架构测试。把仍有长期价值的目录与速度边界保留在 topology、预算脚本和最终缺席门禁中。

- [ ] **Step 3：执行最终快速预算**

运行：

```bash
rtk uv run pytest -q --junitxml=reports/fast-tests.xml
rtk uv run python scripts/check_fast_test_budget.py reports/fast-tests.xml
rtk uv run pytest tests/architecture -q
```

期望：默认套件不超过 60 秒，单例不超过 1 秒，纯领域/插件单元 p95 不超过 100 毫秒。

- [ ] **Step 4：执行受影响重测试**

按各工作包记录的环境和路径分别运行 integration、e2e、resilience、load、mock；不得用默认 pytest 结果代替。

- [ ] **Step 5：执行最终仓库验收**

```bash
rtk uv run ruff format --check .
rtk uv run ruff check .
rtk uv run bandit -r src/
rtk ./scripts/git-quality-gate.sh --profile quality
rtk git diff --check
```

提交前按仓库规则运行 GitNexus detect changes，确认没有旧执行路径或无关符号被保留。

- [ ] **Step 6：提交矩阵退役**

```bash
rtk git add docs/architecture/wes-test-convergence-matrix.csv scripts/check_test_convergence_matrix.py tests/architecture/test_test_convergence_matrix_guardrail.py tests/support/test_suite_topology.py tests/architecture/test_test_suite_topology_guardrail.py
rtk git commit -m "docs(test): 完成测试收敛并退役实施矩阵"
```

## 8. 完成定义

全部条件同时满足才算测试收敛完成：

- 每个最终业务合同和可靠性不变量都有且只有一个主要测试所有者。
- 默认 `pytest` 只包含 `FAST`，满足 60 秒、单例 1 秒以及 `tests/unit/`、`tests/workline_plugins/` p95 100 毫秒预算。
- `tests/architecture/` 只由质量门禁显式运行一次。
- 真实数据库、HTTP、Celery、并发、等待、性能和多组件场景只存在于 `HEAVY`。
- 测试中不存在只验证旧 Runtime、Manifest、System Capability、Intent/Effect、RuntimeHold、Reconciliation、CellReservation、兼容路径或旧迁移链的代码。
- 不存在旧测试路径 alias、fixture compatibility、阶段性 allowlist 或双 gate。
- `tests/README.md`、`AGENTS.md`、Pytest 配置、质量门禁和 Jenkins 对测试层的定义一致。
- 实施期矩阵已经关闭并删除，永久 topology、预算和旧架构缺席门禁通过。
- 默认快速回归、架构测试、受影响重测试、Ruff、Bandit、Import Linter 和仓库质量门禁全部通过。

## 9. 风险控制

| 风险 | 控制 |
| --- | --- |
| 误删目标可靠性断言 | 先建立最终对象测试并通过，再关闭矩阵行和删除旧测试 |
| 把旧架构测试仅移动到重测试 | 语义动作与执行层分别校验，旧语义最终必须删除 |
| 默认套件继续膨胀 | 60 秒、单例 1 秒、纯单元 p95 100 毫秒自动预算 |
| 高层测试重复领域排列 | 一个行为一个所有者，高层只验证新增边界风险 |
| 架构扫描散落并重复运行 | `QUALITY` 一次显式收集，单一规则实现 |
| 速度优化削弱失败路径 | 去重按所有权，不按成功/失败类别；业务拒绝、冲突和异常结果必须保留 |
| 临时矩阵变成长期迁移负担 | 全部行关闭后删除，永久规则迁入 topology、预算和缺席门禁 |
| CI 与本地命令漂移 | `scripts/git-quality-gate.sh` 为本地编排真源，Jenkins 使用相同测试层和脚本接口 |
