# WES 测试语义与重量收敛实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前测试套件收敛为以 WES 最小执行架构 SPEC 为唯一语义基线、默认执行轻量且每项行为只有一个明确测试所有者的最终测试体系。

**Architecture:** 目标测试由独立业务切片随垂直切片交付，测试治理在其通过后删除旧架构测试并去重。测试按 `FAST`、`QUALITY`、`HEAVY` 三个执行层分离；旧架构防回流靠永久缺席门禁，测试删除顺序与可靠性承接靠 AGENTS.md 硬约束 + PR review 保证，不引入临时逐文件矩阵控制面。

**Tech Stack:** Python 3.13、Pytest 9、pytest-asyncio、JUnit XML、Ruff、Bandit、GitNexus、Jenkins。

> **评审决策（2026-08-01 plan-eng-review，SCOPE_REDUCED）：** 原「逐文件处置矩阵」（CSV + checker + guardrail + pre-commit 预登记 + `--final`）已移除，违反未发布系统 KISS/YAGNI。测试治理改用：AGENTS.md「先建目标测试再删旧测试」顺序规则 + `check_business_legacy_absence_gate.py` 旧架构缺席门禁 + topology guardrail + PR review。可靠性承接审计靠 PR review（用户接受该代价）。SPEC 并发语义：物理并发互锁归 ECS/PLC（SPEC §5.2/§8.1），WES 不在并发互锁上过度设计。

---

## 1. 实施基线

唯一设计基线：

- `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`（frontmatter 已为 `status: Accepted`；Task 1 仅可基于该已接受 SPEC 启动，不得以未审查状态作为不可逆删除依据。）

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
10. 不引入临时逐文件矩阵控制面。测试删除顺序与可靠性承接靠 AGENTS.md「先建目标测试再删旧测试」硬约束 + PR review 保证；删除测试的 commit message 或 PR 描述须标注承接的目标测试路径或 `NONE`，便于审计语义承接缺口。
11. `tests/architecture/test_test_suite_topology_guardrail.py` 在 Task 2 通过 `git mv` 重命名为 `tests/architecture/test_suite_topology_guardrail.py`，并在同一提交同步更新 `scripts/git-quality-gate.sh` 引用，消除 `test_test_` 双前缀和已删除路径引用。
12. 默认 `pytest` 收集路径下 test_*.py 不得依赖真实数据库、HTTP、Celery、Redis、容器等真实服务。该约束靠三层保证且不引入 AST 黑名单（避免过度设计）：(a) 目录位置约定（真实服务测试只放 `tests/integration|e2e|resilience|load|mock`）+ `norecursedirs`；(b) PR review 拦截违规 import；(c) FAST 预算门禁（60s/单例 1s）天然暴露连 DB/HTTP 的慢测试。topology guardrail 只验证目录结构，不验证 import。
13. GitNexus 使用：修改函数、类、方法前必跑 `impact` 评估 blast radius（AGENTS.md 硬规则）；每个 commit 提交前必跑 `detect_changes --scope all`（在 `git add`/`git rm`/`git mv` 完成 staging 之后）确认变更范围。两者职责不同，不互相替代。
14. 目标生产对象、接口、目标代码**及其业务对象目标测试**的 commit 与验收由独立业务实施工作包随每个垂直切片一起交付后，Task 3-6 才可开始对应测试收敛（只删除对应旧测试并去重，不补建业务对象目标测试）。Task 10/11 建立的是跨切片可靠性/并发韧性测试（SPEC §9.3/§8.1 验收条件），不归属单一业务切片，由测试治理计划自己建立；测试治理计划不实现生产代码。**业务垂直切片必须同时交付两项 WES 软件层并发安全语义的生产实现与验收，作为 Task 10/11 的入口条件：(a) 处理幂等——同一 `InboundEvidence` 被并发 Handler 领取仅产生一次 `DeviceCommand`/投影推进；(b) CALLBACK fencing——CALLBACK 按 `command`/`execution`/`line`/`epoch` 四关联键路由，旧 Epoch 迟到 CALLBACK 只保存为证据、不写入新对象或新 Epoch 投影。** 最终 Alembic 基线也必须由独立生产工作包使用 Alembic generator 建成，并提供 commit、revision、空库 `upgrade head` 与 metadata 证据（**必须含 `wes_sys.audit_logs` hypertable、TimescaleDB 扩展对象、索引/约束的空库验证，见 TODOS P1 TimescaleDB**），之后才可开始 Task 7 的迁移测试删除、Task 9b 的 `HEAVY DB-Baseline` 和 Task 12 的最终 HEAVY 执行。
15. Task 7 独占显式 `git rm` 实际不再调用的四个 Runtime gate：`scripts/check_runtime_evidence_readiness_gate.py`、`scripts/check_runtime_production_closure_gate.py`、`scripts/check_runtime_production_e2e_gate.py`、`scripts/check_runtime_toggle_release_gate.py`，以及 `scripts/test_api_signature.sh`、`scripts/test_live_suite.sh` 与其对应 guardrail 测试；并在同一 Task 从 `scripts/git-quality-gate.sh` 删除 `run_runtime_contract_guardrails`、`--check runtime-contract-guardrails` 和 quality profile 调用。`runtime-contract-guardrails` 是聚合 profile/check，不是第五个脚本。Task 8 不重复删除或改写这些项。
16. `pyproject.toml` 清理 `addopts`：删除默认情况下无效的 `--cov-report=term-missing`（无 `--cov` 不触发），仅保留 `-v --durations=10 --tb=short`；JUnit XML 选项在质量门禁脚本中显式传。
17. 速度预算扩展：`tests/unit/` 与 `tests/workline_plugins/` p95 仅在 N≥30 时生效，N<30 跳过且不报 warn；`junit_family=xunit2` 在 `pyproject.toml` 设置；CI 容器固定 2 vCPU / 4 GB 配额。
18. 默认 `pytest` 预算基线：固定套件 60 秒、单例 1 秒、`tests/unit/` 与 `tests/workline_plugins/` p95 100 毫秒（p95 仅 N≥30 生效）；`pyproject.toml` 的 `addopts` 与 `norecursedirs` 决定 FAST 范围，预算在**本次 PR 的 JUnit 实测耗时**上一次性校验，**不引入历史缓存或跨 PR 状态**。若本次 FAST 实测耗时超过 60 秒，直接 fail 并提示按 §3 重新评估测试所有权。
19. `AGENTS.md` 与 `tests/README.md` 各按职责说明测试所有权与运行方式；不强制两文档做词级镜像检查。
20. `tests/architecture/` 不预设文件数上限；架构测试按领域自然收敛。
21. `tests/mock/` 虽位于 HEAVY 目录，但若仅依赖 fake port 与虚拟时钟、可以以纯 in-memory 实现稳定在 FAST 层时，应改入 FAST 路径；不允许仅以目录名判定 HEAVY。
22. 最终旧架构缺席扫描存在"伪命中"风险：扫描器必须使用 token 化分词，并且 source allowlist 只能是以下固定、精确的相对路径集合：`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`、`docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md`、`TODOS.md`。仅这些文件可对历史名称使用 tokenized exception；禁止目录或 glob、运行时读取、阶段性配置、源码或测试路径豁免，其余生产代码、测试和配置仍须零命中。

**范围限定**：本计划只负责测试治理（保留、改写、删除、分层与门禁），不是生产实现工作包。最终对象、接口和目标生产代码必须先由独立业务实施工作包完成 commit 与验收，并以可审计交接记录交付；本计划只在该门槛后开始相应测试收敛。最终 Alembic 基线同样由独立生产工作包通过 Alembic generator 创建并交付 commit、revision、空库 `upgrade head` 和 metadata 一致性证据；本计划不得创建、替代或补写生产实现。

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

- 参考 CI 容器（固定 2 vCPU / 4 GB 配额）中默认套件总耗时不超过 60 秒；预算可达性预检在 Task 2 Step 5 完成。
- 默认套件中单个用例不超过 1 秒。
- `tests/unit/` 与 `tests/workline_plugins/` 中的纯领域/插件单元测试 p95 不超过 100 毫秒；**仅在每目录用例数 N≥30 时生效，N<30 跳过且不报 warn**。
- 运行结果可重复，不依赖测试执行顺序和本机遗留状态。
- JUnit XML 显式 `junit_family=xunit2`；`check_fast_test_budget.py` 解析 `testsuite`/`testcase` 时**路径取 `classname` 属性**，`time` 单位为秒；不依赖 `pytest-junit` 默认方言。

### 4.2 `QUALITY`：显式质量门禁

内容：

- `tests/architecture/` 下的 import、依赖、缺席、文档和测试拓扑检查。
- Ruff、Bandit、Import Linter 和稳定的静态扫描。

约束：

- 不进入默认 `pytest`。
- 质量门禁只以一次 `uv run pytest tests/architecture -q` 执行全部架构测试。
- 同一规则只能有一个权威实现；Shell 扫描、Python guardrail 和 Pytest 断言不得重复维护同一名单。

### 4.3 `HEAVY`：显式重测试

目录：

- `tests/integration/`
- `tests/e2e/`
- `tests/resilience/`
- `tests/load/`
- `tests/mock/`（**例外**：若 mock 完全基于 fake port + 虚拟时钟且不依赖真实数据库/HTTP/Celery/容器/网络，可降级为 `FAST`；不强制以目录名判定 HEAVY。）

进入条件：

- 需要真实数据库、Redis、HTTP、Celery、Docker、多进程、并发、断连、时间等待或容量采样。
- 只有该层能证明的数据库、网络、进程或系统级风险。

重测试不设统一的本地全跑入口。每个实现工作包必须声明受影响目录、环境前置条件和实际运行命令。

## 5. 文件职责图

实施期治理文件：

| 文件 | 职责 |
| --- | --- |
| `tests/support/test_suite_topology.py` | 最终目录层级、默认排除目录、文件体量规则 |
| `tests/architecture/test_test_suite_topology_guardrail.py` → `tests/architecture/test_suite_topology_guardrail.py` | Task 2 使用 `git mv` 完成的拓扑 guardrail 重命名；同一 Task 更新质量门禁引用，验证最终测试拓扑与默认收集边界 |
| `scripts/check_fast_test_budget.py` | 从 JUnit XML（`junit_family=xunit2`，由 `pyproject.toml` 设置）验证默认套件总耗时、单例耗时与纯单元 p95（p95 仅 N≥30 生效） |
| `tests/scripts/test_check_fast_test_budget.py` | 预算解析、边界值、N<30 跳过与失败输出的单元测试 |
| `scripts/check_business_legacy_absence_gate.py` | 最终旧架构符号、import、配置和 fallback 的单一缺席扫描；source allowlist 固定且仅限 `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`、`docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md`、`TODOS.md` 的 tokenized historical-name exception，禁止目录/glob、运行时读取、阶段性配置、源码或测试路径豁免 |
| `tests/architecture/test_business_legacy_absence_guardrail.py` | 缺席扫描的规则合同 |
| `scripts/git-quality-gate.sh` | 只编排静态检查、一次架构测试和一次默认快速回归；显式 `--junitxml=reports/fast-tests.xml`（`junit_family=xunit2` 在 `pyproject.toml` 中设置） |
| `pyproject.toml` | Pytest 默认只收集 `FAST`；`addopts` 清理（删 `--cov-report=term-missing`） |
| `.githooks/pre-commit` | 仓库实际 Git hook；`scripts/install-git-hooks.sh` 设置 `core.hooksPath`；调用质量门禁 |
| `Jenkinsfile` | 分别执行架构门禁、默认快速回归和受环境支持的重测试；**集成重跑仅在独立生产工作包交付最终 Alembic 基线证据后，由 Task 12 执行** |
| `tests/README.md` | 最终测试所有权、目录、预算和运行方式 |
| `AGENTS.md` | Agent 新增和修改测试时的长期硬约束（不与 `tests/README.md` 做词级镜像检查） |

**长期治理文件**（不随 Task 12 退役，持续维护）：

| 文件 | 职责 |
| --- | --- |
| `scripts/select_heavy_tests.py` | HEAVY selector：按目标分支 diff 选受影响 HEAVY 测试，区分直接修改的 HEAVY 测试、候选范围与 ignore_globs |
| `docs/architecture/heavy-test-impact.toml` | selector 的机器可读映射真源；新增生产模块/迁移/基础设施配置必须补 `[[mapping]]` 或显式 NONE |
| `tests/scripts/test_select_heavy_tests.py` | selector 协议、候选范围分类与 TOML schema 的永久单元测试 |

目标业务测试路径随对应生产实现工作包一起创建或改写。**每个 commit 提交前必跑 GitNexus `detect_changes --scope all`（在 staging 之后）**。删除测试的 commit message 或 PR 描述须标注承接的目标测试路径或 `NONE`（决策 10）。

## 6. 实施任务

### Task 1：建立治理基线

**Files:**

- Modify: `tests/support/test_suite_topology.py`
- Modify: `AGENTS.md`（写入测试治理硬约束）
- Modify: `.githooks/pre-commit`（确保调用质量门禁）

> 注：`scripts/check_business_legacy_absence_gate.py` 与 `tests/architecture/test_business_legacy_absence_guardrail.py` **均已存在**（支持 `--mode draft/final`）；Task 1 不新建、不修改，旧架构符号清单在 Task 7 补全。

- [ ] **Step 1：冻结现场快照**

运行：

```bash
rtk proxy find tests -type f -name 'test_*.py' | wc -l
rtk proxy find tests -type f -name 'test_*.py' -print0 | xargs -0 wc -l | tail -n 1
rtk uv run pytest --collect-only -q -o addopts=''
```

期望：保存执行日期、文件数、行数、默认收集数和各一级目录文件数；不把数量写入长期 `tests/README.md`。

- [ ] **Step 2：在 AGENTS.md 写入测试治理硬约束**

写入以下长期硬约束（也写入 `tests/README.md` 的运行方式小节）：

- 先建立目标对象测试并通过，再删除对应旧测试；不得反向。
- 同一行为只有一个主要测试所有者。
- 删除测试的 commit message 或 PR 描述必须标注承接的目标测试路径或 `NONE`。
- 不得按 `replay`、`legacy`、`reconciliation` 等关键词批量删除测试。
- 默认 `pytest` 收集路径下 test_*.py 不得依赖真实数据库、HTTP、Celery、Redis、容器等真实服务；由目录位置和 `norecursedirs` 保证。

- [ ] **Step 3：增强 topology，复用现有 absence gate**

增强 `tests/support/test_suite_topology.py` 与拓扑 guardrail 的最终目录层级、默认排除目录与文件体量规则。`check_business_legacy_absence_gate.py` 与其 guardrail 测试**已存在**（支持 `--mode draft/final`），Task 1 复用、不新建；旧架构符号清单在 Task 7 删除旧平台时补全。

- [ ] **Step 4：验证治理基线**

运行：

```bash
rtk uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
rtk uv run python scripts/check_business_legacy_absence_gate.py
```

期望：拓扑 guardrail 通过；现有 absence gate 以当前符号清单运行通过（`--mode draft`）。

- [ ] **Step 5：提交治理基线**

```bash
rtk git add tests/support/test_suite_topology.py AGENTS.md .githooks/pre-commit
rtk gitnexus detect_changes --scope all
rtk ./.githooks/pre-commit
rtk git commit -m "test(governance): 建立测试治理基线（AGENTS 约束+topology 增强）"
```

### Task 2：建立三层执行拓扑和速度预算

**Files:**

- Modify: `pyproject.toml`
- Modify: `tests/support/test_suite_topology.py`
- Rename: `tests/architecture/test_test_suite_topology_guardrail.py` → `tests/architecture/test_suite_topology_guardrail.py`
- Create: `scripts/check_fast_test_budget.py`
- Create: `tests/scripts/test_check_fast_test_budget.py`
- Modify: `scripts/git-quality-gate.sh`（同一 Task 改为引用重命名后的拓扑 guardrail）
- Modify: `tests/README.md`

- [ ] **Step 1：先写失败的拓扑合同**

先执行 `git mv tests/architecture/test_test_suite_topology_guardrail.py tests/architecture/test_suite_topology_guardrail.py`，并在同一 Task 立即更新 `scripts/git-quality-gate.sh` 的路径引用；不得让 `.githooks/pre-commit` 所调用的质量门禁在该提交期间仍引用已删除文件。

增加以下断言：

- `tests/architecture/` 与五个重测试目录不进入默认收集。
- `tests/architecture/*` 路径不可被默认 `pytest` 收集；`tests/integration/`、`tests/e2e/`、`tests/resilience/`、`tests/load/`、`tests/mock/` 路径不可被默认 `pytest` 收集。
- 新测试不得出现在 `tests/` 根目录。
- 默认测试文件不能依赖真实服务、主动等待或子进程；**不通过 AST 黑名单扫描实现**，而由 `tests/integration|e2e|resilience|load|mock` 目录位置和 `norecursedirs` 共同保证；topology guardrail 仅验证目录与文件结构。
- `tests/scripts/` 下的 `test_check_fast_test_budget.py` 与 `tests/architecture/` 下的 guardrail 不在 FAST 默认收集范围内。

- [ ] **Step 2：先写失败的预算脚本测试**

`scripts/check_fast_test_budget.py` 只使用标准库解析 JUnit XML。`junit_family=xunit2` 在 `pyproject.toml` 设置。实施期报告接口为：

```bash
rtk uv run python scripts/check_fast_test_budget.py reports/fast-tests.xml --report-only
```

默认阈值固定为套件 60 秒、单例 1 秒、`tests/unit/` 与 `tests/workline_plugins/` p95 100 毫秒；**p95 预算仅在每目录用例数 N≥30 时生效，N<30 跳过且不报 warn**；脚本必须明确打印超限用例和实际值以及 N 计数。省略 `--report-only` 时超限退出非零，最终质量门禁只使用强制模式。CI 容器固定 2 vCPU / 4 GB 配额作为预算达标的硬条件。

- [ ] **Step 3：收敛 Pytest 默认收集**

在 `pyproject.toml` 中把 `tests/architecture` 与 `tests/scripts` 加入 `norecursedirs`。**清理 `addopts`**：删除 `--cov-report=term-missing`（默认无 `--cov` 不触发且增加噪声），仅保留 `-v --durations=10 --tb=short`；JUnit XML 选项在质量门禁脚本中显式传，`junit_family=xunit2` 在 `pyproject.toml` 中设置，不放入 `addopts`。真实数据库、HTTP、Celery、并发、等待和性能测试按 §4 分层移动到现有五个重测试目录；不得通过 marker 让重测试继续混在 `FAST` 路径。

`tests/architecture` 调出默认收集**必须晚于** Task 1 的 topology 门禁建立；Task 1 Step 4 通过前，topology guardrail 仍在默认收集范围。

- [ ] **Step 4：更新测试指南与两文档镜像**

`tests/README.md` 改为本计划第 3、4 节的最终所有权和运行方式，删除把 `tests/workline_runtime/`、orchestrator、intent、session resolver 和插件模板作为长期目标的说明。`AGENTS.md` 在 Task 8 Step 4 同步反映最终对象与测试层。

- [ ] **Step 5：验证默认层**

运行：

```bash
rtk uv run pytest --collect-only -q -o addopts=''
rtk uv run pytest -q --junitxml=reports/fast-tests.xml
rtk uv run python scripts/check_fast_test_budget.py reports/fast-tests.xml --report-only
rtk uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/scripts/test_check_fast_test_budget.py -q
```

**预算可达性预检**：跑一次 `uv run pytest -q --junitxml=reports/fast-tests.xml`，从 JUnit 实测耗时判断 60s 是否可达（**收集耗时不能代表执行耗时，禁止用 `--collect-only` 估算均值**）；若实测超过 60s，**回到 §3 重新评估测试所有权（缩小默认套件规模或重排）后重跑 Step 5**。此阶段允许总时长暂未达到最终预算，但每个超限文件必须已计划后续改写、合并、移动或删除，不能加入永久豁免。

- [ ] **Step 6：提交执行拓扑**

```bash
rtk git add tests/architecture/test_suite_topology_guardrail.py
rtk git add pyproject.toml tests/support/test_suite_topology.py scripts/check_fast_test_budget.py tests/scripts/test_check_fast_test_budget.py tests/README.md scripts/git-quality-gate.sh
rtk gitnexus detect_changes --scope all
rtk ./.githooks/pre-commit
rtk git commit -m "test(governance): 建立快速质量重测试分层"
```

### Task 3：用最终执行对象替换核心 Runtime 测试

**实施入口条件：**独立业务实施工作包已交付最终 `InboundEvidence`、`DeviceCommand`、`TransportTask`、`WmsConfirmation`、具体 Execution 等对象、接口及目标生产代码的 commit 和验收证据；本 Task 只在该交接后改写测试，不补写生产实现。

每创建、改写或删除一项本 Task 测试资产，commit 须按决策 10 标注承接的目标测试路径或 `NONE`。

**Scope:**

- `tests/runtime/`
- `tests/workline_runtime/`
- `tests/contracts/workline/`
- `tests/callback/`
- 与上述目录重复的 `tests/unit/runtime/` 和架构测试

- [ ] **Step 1：确认最终所有者覆盖**

逐项确认最终所有者覆盖：

- `InboundEvidence` 的先持久化后 ACK、重复 Payload 和冲突证据。
- `DeviceCommand` 的持久化、ACK/CALLBACK 分离、设备忙闲和未知结果不自动重放。
- `TransportTask` 的请求、成员进度和批次结果。
- `WmsConfirmation` 的待确认事实、重试和依赖恢复。
- `LineRunEpoch`、具体 Execution、位置投影和设备投影。
- 粗分机 13 类入站判定、设备忙等待、成功 CALLBACK 和失败结果。

- [ ] **Step 2：确认业务切片已交付目标测试**

按决策 14，目标测试由独立业务实施工作包随垂直切片交付，测试治理不补建。本 Step 确认业务切片已为每个可靠性不变量交付权威目标测试文件并通过（绿），作为删除对应旧测试的前提；不得复制旧 fixture、旧状态枚举或旧服务装配。

- [ ] **Step 3：目标实现通过后删除对应旧测试**

删除只验证 RuntimeInbox 生命周期、RuntimeIntent/Effect、ExecutionSession、Plugin Binding、System Capability、RuntimeHold 和 generated index 的测试。混合文件先迁移目标断言，再删除旧文件。

- [ ] **Step 4：消除跨层重复**

同一幂等、状态推进或插件决策只保留最低稳定层的完整断言；API、Repository 和集成层只保留自身新增风险。

- [ ] **Step 5：验证核心切片**

运行：

```bash
rtk uv run pytest -q --junitxml=reports/fast-tests.xml
rtk uv run pytest tests/architecture -q
```

- [ ] **Step 6：提交核心测试替换**

只 stage 本工作包的生产和测试路径，核对暂存 diff 后提交。提交前必跑：

```bash
rtk gitnexus detect_changes --scope all
rtk ./.githooks/pre-commit
rtk git commit -m "test(execution): 收敛最小执行对象与粗分机测试"
```

### Task 4：收敛自动分拣入库与资源测试

**实施入口条件：**独立业务实施工作包已交付自动分拣最终对象、接口及目标生产代码的 commit 和验收证据；本 Task 仅收敛对应测试资产，不实现该生产能力。

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

默认快速回归和架构测试必跑。受影响集成测试在最终 Alembic 基线交付前用**本机 Docker 隔离环境预验证**（非最终 schema，结果不作最终 schema 验证），基线交付后的最终重跑见 Task 12 Step 2。

- [ ] **Step 6：提交自动入库测试替换**

只 stage 本工作包确切路径，提交前必跑：

```bash
rtk gitnexus detect_changes --scope all
rtk ./.githooks/pre-commit
rtk git commit -m "test(workline): 收敛自动分拣入库与资源测试"
```

### Task 5：收敛人工分拣与四线约束测试

**实施入口条件：**独立业务实施工作包已交付人工分拣/四线最终对象、接口及目标生产代码的 commit 和验收证据；本 Task 只处理测试治理，不替代业务实现。

**Scope:**

- 人工分拣、四线串联、NG、owner workline 和 CTU 退料相关测试

- [ ] **Step 1：确认业务切片已交付唯一业务所有者测试**

按决策 14，目标测试由业务切片交付，测试治理不补建。本 Step 确认业务切片已交付覆盖业务 NG、硬件故障、依赖暂停、人工完成、不可变 `owner_workline_id`、NG 跨线直行和非 NG 同线进出的目标插件/领域测试并通过。

- [ ] **Step 2：删除平台化替代语义**

删除 RuntimeHold、Reconciliation、SorterCorridor、跨线执行引擎和错误重新绑定测试。

- [ ] **Step 3：压缩场景组合**

相同行为使用参数化 fixture 共享输入和期望结果；API/E2E 不重复穷举插件层已证明的四线排列。

- [ ] **Step 4：验证工作包**

默认快速回归和架构测试必跑。受影响 E2E/韧性测试在最终 Alembic 基线交付前用**本机 Docker 隔离环境预验证**（非最终 schema），基线交付后的最终重跑见 Task 12 Step 2。

- [ ] **Step 5：提交人工线测试替换**

只 stage 本工作包确切路径，提交前必跑：

```bash
rtk gitnexus detect_changes --scope all
rtk ./.githooks/pre-commit
rtk git commit -m "test(workline): 收敛人工分拣与四线约束测试"
```

### Task 6：收敛复杂出库与满箱交换测试

**实施入口条件：**独立业务实施工作包已交付复杂出库/满箱交换最终对象、接口及目标生产代码的 commit 和验收证据；本 Task 不将这些生产实现纳入范围。

**Scope:**

- 出库来源、单层/五层/退货/转运货架、CTU 批次、满箱交换和 WMS 确认相关测试

- [ ] **Step 1：确认业务切片已交付目标流程测试**

按决策 14，目标测试由业务切片交付，测试治理不补建。本 Step 确认业务切片已交付覆盖 WMS 来源分配、选择格口、出料、组盘/组箱、`TransportTask` 成员进度、满箱交换和 WMS 确认义务的领域测试并通过。

- [ ] **Step 2：限制 E2E 数量**

每类主要物理闭环保留一个代表性 E2E；来源类型和业务拒绝的排列在领域或合同层参数化覆盖。

- [ ] **Step 3：改写可靠性测试**

WMS 不可用、迟到 CALLBACK、确认重试和人工清线进入 `tests/resilience/`；删除 RuntimeHold、通用 Outbox 和自动 replay 所有权。

- [ ] **Step 4：验证工作包**

默认快速回归和架构测试必跑。受影响 integration/e2e/resilience 测试在最终 Alembic 基线交付前用**本机 Docker 隔离环境预验证**（非最终 schema），基线交付后的最终重跑见 Task 12 Step 2。

- [ ] **Step 5：提交出库测试替换**

只 stage 本工作包确切路径，提交前必跑：

```bash
rtk gitnexus detect_changes --scope all
rtk ./.githooks/pre-commit
rtk git commit -m "test(workline): 收敛复杂出库与满箱交换测试"
```

### Task 7：删除旧平台、旧迁移和旧门禁

**实施入口条件：**独立业务实施工作包已交付所有最终对象、接口及目标生产代码的 commit 与验收。迁移测试删除还必须等待独立生产工作包使用 Alembic generator 创建最终基线，并交付该基线的 commit、revision、空库 `upgrade head` 与 metadata 一致性证据。另由独立生产/文档工作包逐项改写、删除或归档 `docs/architecture` 及其余当前态文档中的旧符号，交付 commit 与 `rg` 零命中证据（仅排除决策 22 列出的三条精确 allowlist 路径）；未满足该文档交接不得进入最终缺席验证。本测试治理计划不执行这些文档改写，也不创建生产实现或 Alembic 基线。

**Files:**

- Modify: `scripts/check_business_legacy_absence_gate.py`（**迁移**：从 ledger/matrix 校验改为决策 22 的纯 token 化 allowlist 扫描，补全旧架构符号清单）
- Modify/Rewrite: `tests/architecture/test_business_legacy_absence_guardrail.py`（改写为验证 allowlist 扫描，删除 ledger/matrix 校验断言）
- Delete: `tests/contracts/test_business_legacy_absence_ledger.py`（依赖被删的 ledger/matrix）
- Delete: `docs/architecture/legacy-cleanup-matrix.csv`、`docs/architecture/legacy-cleanup-matrix.md`、`docs/architecture/business-legacy-absence-ledger.csv`、`docs/architecture/business-legacy-absence-ledger.md`、`scripts/generate_legacy_matrix.py`（现有 absence gate 的 ledger/matrix 依赖资产，迁移到 allowlist 扫描后删除）
- Modify: `tests/architecture/test_suite_topology_guardrail.py`（删除旧 extension-platform 路径的存在/行数断言）
- Modify: `scripts/git-quality-gate.sh`（同步移除四个 Runtime gate 及 `runtime-contract-guardrails` 聚合 profile/check 的编排）
- Modify: `scripts/architecture-guardrails.sh`（移除 `matrix_drop_marker_for_entry` 函数及从 `legacy-cleanup-matrix.csv` 读 `entry_id`/`drop_phase` 的校验逻辑）
- Modify: `scripts/architecture-guardrails.allowlist`（移除 `legacy_entry_id`/`drop_phase` 两列，baseline validation 不再校验与 matrix 一致）
- Rewrite: `tests/architecture/test_capability_dependency_guardrail.py`（移除 `_matrix_drop_phases`/`_allowlist_rows_with_matrix_drop_phase` 及 matrix fixture，保留 capability 边界校验）
- Rewrite: `tests/architecture/test_device_command_boundary_guardrail.py`（移除 matrix/drop_phase 依赖，保留 device-command 边界校验）
- Rewrite: `tests/architecture/test_process_naming_guardrail.py`（移除 drop_phase 引用与 closure 测试 allowlist 引用，保留 process-naming 校验）
- Modify/Delete: 只验证旧 revision chain、upgrade/downgrade、数据回填的测试

- [ ] **Step 1：确认可靠性替换闭合**

只有当目标 `InboundEvidence`、`DeviceCommand`、`TransportTask`、`WmsConfirmation`、具体 Execution 和人工清线测试均通过时，才删除最后一批旧 Runtime 测试。

- [ ] **Step 2：迁移缺席门禁为纯 allowlist 扫描并合并架构门禁**

现有 `scripts/check_business_legacy_absence_gate.py` 依赖 `legacy-cleanup-matrix.csv` + `business-legacy-absence-ledger.csv`（`MATRIX_PATH`/`LEDGER_PATH`/`LEDGER_REQUIRED_FIELDS`/`_ledger_rows`/`_material_flow_matrix_rows`/`_ledger_identity_failures` 等 ledger 字段校验与 ledger/matrix identity 校验）。本 Step 把它**迁移**为决策 22 的纯 token 化 allowlist 扫描：删除上述 ledger/matrix 逻辑，保留并补全旧架构符号（Runtime、Manifest、Capability、Intent/Effect、Hold、Recovery、Reservation、兼容 import、alias、fallback）的 token 化扫描，source allowlist 固定为决策 22 的三条精确路径。

删除 ledger/matrix 资产：`docs/architecture/legacy-cleanup-matrix.csv/.md`、`docs/architecture/business-legacy-absence-ledger.csv/.md`、`scripts/generate_legacy_matrix.py`。删除 `tests/contracts/test_business_legacy_absence_ledger.py`（依赖被删的 ledger/matrix）。改写 `tests/architecture/test_business_legacy_absence_guardrail.py` 为验证 allowlist 扫描规则，删除 ledger/matrix 校验断言。删除重复的阶段性 guardrail 和旧平台存在性测试。

在 `scripts/git-quality-gate.sh` 中由 Task 7 唯一删除 `run_runtime_contract_guardrails` 实现、`--check runtime-contract-guardrails` 选项及 quality profile 对该 check 的调用；同步改写 `tests/architecture/test_git_quality_gate_architecture_profile.py` 为最终质量门禁测试，并移除所有相关引用。Task 8 仅验证这些名称均已不存在。

同步修改 `tests/architecture/test_suite_topology_guardrail.py`：删除对 `tests/architecture/test_runtime_extension_platform_guardrail.py` 的存在或行数断言，并把该 topology 约束调整为最终质量门禁集合。

同步迁移 architecture guardrail 体系对 `legacy-cleanup-matrix.csv` 的依赖：移除 `scripts/architecture-guardrails.sh` 的 `matrix_drop_marker_for_entry` 函数与 matrix 校验；移除 `scripts/architecture-guardrails.allowlist` 的 `legacy_entry_id`/`drop_phase` 两列（baseline validation 不再校验与 matrix 一致）；改写 `tests/architecture/test_capability_dependency_guardrail.py`（删除 `_matrix_drop_phases`/`_allowlist_rows_with_matrix_drop_phase` 与 matrix fixture）、`tests/architecture/test_device_command_boundary_guardrail.py`、`tests/architecture/test_process_naming_guardrail.py`，删除各自读 CSV / `drop_phase` 的辅助函数与 matrix fixture，保留各自的 capability / device-command / process-naming 边界校验。

- [ ] **Step 3：删除旧迁移测试**

仅在上述独立生产工作包已提供最终 Alembic 基线的 commit、revision、空库 `upgrade head` 和 metadata 一致性证据后（**基线必须含 `wes_sys.audit_logs` hypertable、TimescaleDB 扩展对象、索引/约束的空库验证，见 TODOS P1**），只保留空库 `upgrade head`、metadata、约束、索引、TimescaleDB 扩展对象与 `audit_logs` hypertable 验证；删除旧 revision chain、数据转换和 downgrade 测试。

- [ ] **Step 4：验证永久缺席**

运行：

```bash
rtk uv run python scripts/check_business_legacy_absence_gate.py --mode final
rtk uv run pytest tests/architecture/test_business_legacy_absence_guardrail.py -q
```

期望：生产代码、测试和配置不存在旧架构入口；source allowlist 仅为决策 22 的三条精确路径。

- [ ] **Step 5：提交最终删除**

**Task 7 独占显式处置下列唯一路径，不使用通配符：**

- `DELETE` 并 `git rm`：四个脚本 Runtime gate——`scripts/check_runtime_evidence_readiness_gate.py`、`scripts/check_runtime_production_closure_gate.py`、`scripts/check_runtime_production_e2e_gate.py`、`scripts/check_runtime_toggle_release_gate.py`——以及 `scripts/test_api_signature.sh`、`scripts/test_live_suite.sh`；同一 Task 在 `scripts/git-quality-gate.sh` 删除 `run_runtime_contract_guardrails`、`--check runtime-contract-guardrails` 和 quality profile 调用（该聚合 check 不是第五个脚本）。
- `DELETE` 并 `git rm`：`tests/architecture/test_runtime_repository_layering_guardrail.py`、`tests/architecture/test_runtime_inbox_processor_ownership.py`、`tests/architecture/test_runtime_inbox_state_machine_guardrail.py`、`tests/architecture/test_runtime_inbox_repository_consumer_guardrail.py`、`tests/architecture/test_runtime_status_owner_guardrail.py`、`tests/architecture/test_runtime_inbox_service_ownership_guardrail.py`、`tests/architecture/test_runtime_capability_context_routing.py`、`tests/architecture/test_runtime_extension_platform_guardrail.py`、`tests/architecture/test_northbound_legacy_removal.py`、`tests/architecture/test_northbound_wms_typed_operation_boundaries.py`、`tests/architecture/test_northbound_wms_operation_inventory.py`、`tests/architecture/test_no_legacy_unbound_runtime.py`、`tests/architecture/test_legacy_runtime_import_guardrail.py`、`tests/architecture/test_legacy_matrix_contract.py`、`tests/architecture/test_cleanup_matrix_guardrail.py`。
- `DELETE` 并 `git rm`（因旧 quality gate 删除而失效）：`tests/contracts/test_runtime_evidence_readiness_gate.py`、`tests/contracts/test_runtime_evidence_artifact_composer.py`、`tests/runtime/orchestration/test_production_closure_evidence_gate.py`、`tests/runtime/orchestration/test_runtime_production_closure_contract.py`。
- `REWRITE`：`git mv tests/architecture/test_git_quality_gate_architecture_profile.py tests/architecture/test_final_quality_gate_profile.py`，改写为只验证最终质量门禁。
- `DELETE` 并 `git rm`：现有 absence gate 的 ledger/matrix 依赖资产——`docs/architecture/legacy-cleanup-matrix.csv`、`docs/architecture/legacy-cleanup-matrix.md`、`docs/architecture/business-legacy-absence-ledger.csv`、`docs/architecture/business-legacy-absence-ledger.md`、`scripts/generate_legacy_matrix.py`、`tests/contracts/test_business_legacy_absence_ledger.py`、`tests/contracts/test_business_legacy_matrix_closure.py`（后两者直接读取上述资产，Step 2 迁移为 allowlist 扫描后删除）。同步从 `tests/architecture/test_process_naming_guardrail.py` 的 allowlist 移除 `tests/contracts/test_business_legacy_matrix_closure.py` 引用。

所有项在同一提交从 `scripts/git-quality-gate.sh` 和必要 guardrail 编排移除引用。Task 8 只断言无遗留引用，不重复 `git rm` 或改写这些项。提交前必跑：

```bash
rtk git mv tests/architecture/test_git_quality_gate_architecture_profile.py tests/architecture/test_final_quality_gate_profile.py
rtk git rm scripts/check_runtime_evidence_readiness_gate.py scripts/check_runtime_production_closure_gate.py scripts/check_runtime_production_e2e_gate.py scripts/check_runtime_toggle_release_gate.py scripts/test_api_signature.sh scripts/test_live_suite.sh scripts/generate_legacy_matrix.py
rtk git rm tests/architecture/test_runtime_repository_layering_guardrail.py tests/architecture/test_runtime_inbox_processor_ownership.py tests/architecture/test_runtime_inbox_state_machine_guardrail.py tests/architecture/test_runtime_inbox_repository_consumer_guardrail.py tests/architecture/test_runtime_status_owner_guardrail.py tests/architecture/test_runtime_inbox_service_ownership_guardrail.py tests/architecture/test_runtime_capability_context_routing.py tests/architecture/test_runtime_extension_platform_guardrail.py tests/architecture/test_northbound_legacy_removal.py tests/architecture/test_northbound_wms_typed_operation_boundaries.py tests/architecture/test_northbound_wms_operation_inventory.py tests/architecture/test_no_legacy_unbound_runtime.py tests/architecture/test_legacy_runtime_import_guardrail.py tests/architecture/test_legacy_matrix_contract.py tests/architecture/test_cleanup_matrix_guardrail.py
rtk git rm tests/contracts/test_runtime_evidence_readiness_gate.py tests/contracts/test_runtime_evidence_artifact_composer.py tests/contracts/test_business_legacy_absence_ledger.py tests/contracts/test_business_legacy_matrix_closure.py tests/runtime/orchestration/test_production_closure_evidence_gate.py tests/runtime/orchestration/test_runtime_production_closure_contract.py
rtk git rm docs/architecture/legacy-cleanup-matrix.csv docs/architecture/legacy-cleanup-matrix.md docs/architecture/business-legacy-absence-ledger.csv docs/architecture/business-legacy-absence-ledger.md
rtk git add scripts/check_business_legacy_absence_gate.py tests/architecture/test_business_legacy_absence_guardrail.py tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_process_naming_guardrail.py tests/architecture/test_capability_dependency_guardrail.py tests/architecture/test_device_command_boundary_guardrail.py tests/architecture/test_final_quality_gate_profile.py scripts/git-quality-gate.sh scripts/architecture-guardrails.sh scripts/architecture-guardrails.allowlist
rtk gitnexus detect_changes --scope all
rtk ./.githooks/pre-commit
rtk git commit -m "refactor(test): 删除旧平台与迁移测试"
```

### Task 8：收敛质量门禁和 CI 执行

**Files:**

- Modify: `scripts/git-quality-gate.sh`
- Modify: `scripts/architecture-guardrails.sh`
- Modify: `Jenkinsfile`
- Modify: `AGENTS.md`
- Modify: `tests/README.md`

**实施入口条件：**业务实施交接已满足 Task 7 的最终对象、接口和目标生产代码要求；本 Task 不删除或改写 Runtime/migration 测试、四个 Runtime gate 或 `runtime-contract-guardrails` 聚合 profile/check，只验证 Task 7 已完成删除，并收敛其余质量路径、Jenkins 与长期文档。

- [ ] **Step 1：确认 Task 7 删除后的剩余质量边界**

不重复 `git rm` 或改写 Runtime/migration gate、`run_runtime_contract_guardrails`、`--check runtime-contract-guardrails` 或其 quality profile 调用。只用 `rg` 验证 `scripts/git-quality-gate.sh`、`scripts/architecture-guardrails.sh`、`Jenkinsfile`、`AGENTS.md` 与 `tests/README.md` 已不再引用 Task 7 删除的四个 Runtime gate、`runtime-contract-guardrails`、`test_api_signature.sh`、`test_live_suite.sh`、`tests/runtime/orchestration/`、`tests/contracts/workline/` 或 `tests/characterization/workline_legacy/`。如仍有引用，回到 Task 7 补齐删除与编排同步。

- [ ] **Step 2：建立单一质量路径**

本地 `quality` profile 顺序固定为：

1. Ruff format。
2. Ruff lint。
3. Bandit。
4. Import Linter。
5. 最终架构和旧架构缺席扫描。
6. 一次 `uv run pytest tests/architecture -q`。
7. 一次默认 `uv run pytest -q --junitxml=reports/fast-tests.xml`。
8. 快速测试预算检查（强制模式，非 `--report-only`）。

保持 Task 7 已完成的旧 Runtime 专用 check 删除结果；本 Task 不再删除或改写这些项。

- [ ] **Step 3：更新 Jenkins**

`Architecture Guardrails` stage 只执行最终静态门禁和一次 architecture suite；`Unit Tests` stage 只执行默认 `FAST` 并检查 JUnit 预算。**真实基础设施支持的 `tests/integration/`、`tests/e2e/`、`tests/resilience/`、`tests/load/`、`tests/mock/` 重测必须由独立 stage 执行，且仅在独立生产工作包交付最终 Alembic 基线证据后运行（最终重跑见 Task 12 Step 2）**；Task 3-6 的 integration 结果不视为最终 schema 验证。重测 stage 不伪装成 Unit Tests；HEAVY 必跑集合与受 PR 改动选跑由 Task 9a/9b 显式落地。

- [ ] **Step 4：同步长期规则**

`AGENTS.md` 和 `tests/README.md` 使用最终对象、测试层和速度预算；删除 Runtime service、orchestrator、intent、session resolver、插件模板和固定旧路径说明。

- [ ] **Step 5：验证门禁没有重复执行**

运行：

```bash
rtk ./scripts/git-quality-gate.sh --profile quality
rtk rg -n "tests/runtime/orchestration|tests/contracts/workline|tests/characterization/workline_legacy|run_runtime_contract_guardrails|runtime-contract-guardrails" scripts/git-quality-gate.sh Jenkinsfile AGENTS.md tests/README.md
rtk ./.githooks/pre-commit
```

期望：质量门禁通过；第二条命令零命中；默认测试和 architecture suite 各只执行一次；`.githooks/pre-commit` 实际质量门禁全绿。

- [ ] **Step 6：提交门禁收敛**

```bash
rtk git add scripts/git-quality-gate.sh scripts/architecture-guardrails.sh Jenkinsfile AGENTS.md tests/README.md
rtk gitnexus detect_changes --scope all
rtk ./.githooks/pre-commit
rtk git commit -m "ci(test): 收敛测试门禁与执行预算"
```

### Task 9a：HEAVY selector 落地（独立可提交，不依赖后续 Task）

本 Task 拆分原"Task 9"，先把 selector 脚本、其单元测试、HEAVY 影响 TOML 映射、Jenkinsfile 增量交付并提交；minimum smoke 与最终 Jenkinsfile HEAVY Required stage 留到 Task 9b（Task 10/11 之后），避免 forward reference。

**Files:**

- Create: `scripts/select_heavy_tests.py`
- Create: `tests/scripts/test_select_heavy_tests.py`
- Create: `docs/architecture/heavy-test-impact.toml`（selector 的机器可读映射真源）
- Modify: `Jenkinsfile`（仅增加 `HEAVY Selector Smoke` stage，**不**启用 HEAVY Required 与 minimum smoke）
- Modify: `scripts/git-quality-gate.sh`（quality profile 追加 `uv run pytest tests/scripts -q`；**不**加真实 HEAVY，HEAVY 只留 Jenkins stage）
- Modify: `AGENTS.md`（新增生产模块必须补 mapping/NONE 的硬规则；selector 与 TOML 的长期维护责任）
- Modify: `tests/README.md`（selector 本地运行方式与 HEAVY 映射维护说明）

**实施入口条件：**selector 与 CI 治理资产可独立落地，但不得借本 Task 实现任何业务生产代码；涉及最终业务 HEAVY 测试的 mapping 与 minimum smoke 仅在独立业务实施工作包交付相应对象、接口、目标代码 commit 与验收后启用。

- [ ] **Step 1：定义 selector 输入协议与候选范围**

`scripts/select_heavy_tests.py` 接受以下输入之一（互斥）：

- `--scope <staged|unstaged>`：本地开发用，与 `gitnexus detect_changes` 一致；默认 `unstaged`。
- `--base <ref>`：CI 用，计算 `<ref>...HEAD` 的提交差异。Jenkins 经 `CleanBeforeCheckout` 检出已提交代码后工作区无 unstaged/staged diff，**CI 必须用 `--base origin/${CI_TARGET_BRANCH}`**；`--scope` 在 CI 会稳定返回空集，不能作为 PR CI 协议。
- 输出：每行一个测试路径到 stdout。

selector 必须按以下顺序分类每个改动文件，再决定选择、忽略或 fail：

- **直接修改的 HEAVY 测试**（路径匹配 `tests/{integration,e2e,resilience,load,mock}/**/test_*.py`）：直接选中该测试自身，无需 mapping。
- **候选范围**（可能影响运行时，**必须命中** mapping 或显式 NONE，未命中 fail closed）：生产代码与应用入口 `src/**`、`main.py`；Alembic 迁移与配置 `migrations/**`、`alembic.ini`；运行时基础设施配置 `docker-compose*.yml`、`pyproject.toml`、`.env*`；HEAVY 支撑资产 `tests/{integration,e2e,resilience,load,mock}/**`（HEAVY 目录下非 `test_*.py` 的全部资产，含 conftest、fixtures、support 与平铺辅助如 `tests/mock/device_simulator.py`、`tests/load/runtime_benchmark_scenarios.py`）、共享测试资产 `tests/fixtures/**`、`tests/conftest.py`、`tests/<dir>/conftest.py`、`tests/support/**`。
- **明确无 HEAVY 影响的 ignore_globs**：只在前两步均未命中后才忽略；包括 `docs/**`、`*.md`、`tests/**`（兜底，故不会遮蔽前述 HEAVY 测试、HEAVY 支撑资产、共享测试资产 `tests/fixtures/**`/`tests/conftest.py`/`tests/<dir>/conftest.py` 或 `tests/support/**`）、CI/Hook 编排 `Jenkinsfile`、`.githooks/**`、`.github/**`、`.gitlab-ci.yml`、`README*`、`LICENSE*`。

退出码语义：

- 退出 0 + 非空输出：选出的受影响 HEAVY 测试。
- 退出 0 + 空输出：改动只在 ignore_globs 或显式 NONE 内（合法无影响）；Jenkins 据此只跑 minimum smoke，**不** fail。
- 退出非 0：候选范围内未命中任何 `source_glob` 或显式 NONE、真正未知的业务/运行时路径、git diff 失败、输出路径非 `tests/.../test_*.py` → fail closed。

- [ ] **Step 2：定义 HEAVY 映射 TOML schema**

`docs/architecture/heavy-test-impact.toml` 是 selector 的机器可读映射真源（不用 Markdown，Markdown 无法表达确定性匹配规则）。字段：

- 顶层 `ignore_globs`：确切 glob 列表（与 Step 1 一致）。
- 每条 `[[mapping]]`：
  - `source_glob`：候选范围内的生产/迁移/运行时配置/HEAVY 支撑与共享测试资产 glob（相对仓库根）。
  - `heavy_tests`：对应 HEAVY 测试路径列表。**空列表 = 显式 NONE**。

匹配与去重规则：

- 改动文件先判是否直接修改的 HEAVY 测试（自选）→ 再判候选范围的 mapping（含 HEAVY 支撑资产、共享测试资产 `tests/fixtures/**`/`tests/conftest.py`/`tests/<dir>/conftest.py` 与 `tests/support/**`）→ 最后才判 ignore_globs；`tests/**` 兜底 ignore 不得遮蔽前两类。
- 候选范围文件匹配多条 `source_glob` 时，取 `heavy_tests` 并集后去重。
- 两条 `source_glob` 不允许重叠且 `heavy_tests` 冲突；schema 校验发现歧义即 fail。
- selector 输出的每个路径必须匹配 `tests/.../test_*.py`，否则 fail closed。
- **glob 语法合同**：selector 实现必须先把每条 glob 的 `{a,b}` 展开为独立模式，再用 `PurePath.full_match`（POSIX 风格）匹配；`**` 定义为匹配零或多层目录。

初始 mapping 至少覆盖粗分机、自动分拣入库、人工分拣、出库/满箱交换模块，以及共享测试资产（`tests/fixtures/**`、`tests/conftest.py`、`tests/<dir>/conftest.py`、`tests/support/**` 变更映射到 integration 受影响回归集）；并发模块由 Task 11 Step 4 补充。

- [ ] **Step 3：selector 单元测试**

`tests/scripts/test_select_heavy_tests.py` 覆盖：

- `--scope` / `--base <ref>` diff 解析 fixture（unstaged 单文件、staged 多文件、空 diff、`origin/develop...HEAD`）
- 直接修改 HEAVY 测试 fixture（改动 `tests/integration/test_foo.py` → 选中自身）
- HEAVY 支撑/共享资产 fixture（改动 `tests/integration/conftest.py`、`tests/fixtures/**`、`tests/conftest.py`、`tests/<dir>/conftest.py` 或 `tests/support/**` → 选中对应回归集 mapping）
- docs-only / Jenkinsfile-only fixture（命中 ignore_globs → 退出 0 + 空输出）
- `.githooks/pre-commit` 与 `tests/runtime/`、`tests/contracts/`、`tests/api/` 普通测试 fixture（前两步均未命中后走 ignore → 退出 0 + 空输出）
- 运行时入口/迁移配置 fixture（改动 `main.py`、`migrations/env.py` 或 `alembic.ini` → 命中全局 HEAVY 回归集 mapping）
- 未分类路径 fixture（任何未归类路径 → 退出非 0，fail closed）
- TOML 命中 fixture、显式 NONE fixture、输出路径校验 fixture、glob brace 展开 fixture、`**` 零层目录 fixture、TOML schema 歧义 fixture。

`tests/scripts/` 已通过 `norecursedirs` 排除出默认 `pytest`；本测试由 `uv run pytest tests/scripts -q` 显式收集，并进入 `scripts/git-quality-gate.sh` 的 quality profile（**永久门禁**）。

- [ ] **Step 4：写入长期维护规则并提交**

`AGENTS.md` 与 `tests/README.md` 记录 selector 与 TOML 的长期维护责任：

- 新增可能影响运行时的生产模块、迁移或基础设施配置时，必须在 `heavy-test-impact.toml` 补 `[[mapping]]` 或显式 NONE，否则 selector fail closed。
- 修改或新增 HEAVY 测试路径时同步更新对应 `heavy_tests`。
- 本地验证：`uv run scripts/select_heavy_tests.py --scope unstaged`。

```bash
rtk git add scripts/select_heavy_tests.py tests/scripts/test_select_heavy_tests.py docs/architecture/heavy-test-impact.toml Jenkinsfile scripts/git-quality-gate.sh AGENTS.md tests/README.md
rtk gitnexus detect_changes --scope all
rtk ./.githooks/pre-commit
rtk git commit -m "test(ci): 落地 HEAVY selector、TOML 映射与长期维护规则"
```

Jenkinsfile 改动只新增 `HEAVY Selector Smoke` stage（仅 `uv run pytest tests/scripts -q`），不启用 HEAVY Required。

### Task 10：关键可靠性崩溃窗口覆盖

崩溃窗口与处理幂等是 SPEC §9.3 与 §15.6 的可靠性验收条件。本 Task 显式定义五类场景（四类崩溃窗口 + 处理幂等）与 SPEC §9.3 兼容语义。

**实施入口条件：**独立业务实施工作包已交付这些最终对象、接口和目标生产代码的 commit 与验收。按决策 14，本 Task 建立的是跨切片可靠性崩溃窗口测试（SPEC §9.3 验收条件），由测试治理计划自己建立、不依赖业务切片交付目标测试。需要真实数据库的 HEAVY 运行必须等待最终 Alembic 基线交付后才可作为最终验证。

**Files:**

- Create: `tests/integration/test_inbound_evidence_ack_crash.py`
- Create: `tests/integration/test_device_command_persist_crash.py`
- Create: `tests/integration/test_callback_txn_concurrency.py`
- Create: `tests/integration/test_wms_idempotent_retry.py`
- Create: `tests/integration/test_evidence_concurrent_processing.py`（处理幂等，决策 14 交接条件）
- Create: `tests/resilience/test_human_clearline_after_restart.py`

- [ ] **Step 1：场景 1 — `InboundEvidence` 提交后但 ECS ACK 前进程崩溃**

模拟 ECS 接收事件后、ACK 响应返回前进程崩溃。验证：

- 重启后未 ACK 的 Evidence 仍可重发 ACK（按幂等键）
- 不重复持久化
- 状态推进为已 ACK，不进入重复判定

落 `tests/integration/test_inbound_evidence_ack_crash.py`。

- [ ] **Step 2：场景 2 — `DeviceCommand` 持久化后但 ECS 发送前进程崩溃**

按 SPEC §9.3：重启后**不自动补发物理命令**。验证：

- 启动时检测 `status=PERSISTED` 但未发送的 Command 标记为「需要人工清线」
- 物理命令绝不自动 replay
- 人工清线后产生新 `LineRunEpoch`

落 `tests/integration/test_device_command_persist_crash.py` 与 `tests/resilience/test_human_clearline_after_restart.py`。

- [ ] **Step 3：场景 3 — CALLBACK 抵达与本地事务提交并发**

模拟 CALLBACK 抵达后的崩溃点。CALLBACK 处理分两个独立事务：事务 A 把 CALLBACK payload 写入 `InboundEvidence` 并**独立提交**；事务 B 基于该 Evidence 推进对象/位置投影。崩溃点 = 事务 A 已提交、事务 B 未提交之间。按 SPEC §9.3 区分两类 callback，验证：

- **投影事务未完成的 callback**（事务 A 已提交持久化 Evidence，事务 B 投影推进未提交时崩溃）：重启后幂等重做事务 B（基于已提交的 Evidence 重新推进投影），完成本次状态推进；这属于 in-flight 投影事务恢复，**不是**物理编排自动恢复。
- **清线后的迟到 callback**（人工清线并创建新 Epoch 后才到达的旧 CALLBACK）：只保存为证据（事务 A），**不**推进任何对象状态、**不**写入新 Epoch 投影（SPEC §9.3「迟到 CALLBACK 继续保存，但不自动恢复物理编排」）。
- 重复 CALLBACK 通过幂等键去重，不导致双重状态推进。
- 与 `InboundEvidence` 幂等键互不干扰。

落 `tests/integration/test_callback_txn_concurrency.py`。

- [ ] **Step 4：场景 4 — WMS 同步成功 + 本地物理落账失败**

按 SPEC §9.3 与"WMS 同步结果 + 本地事务"原子性，验证：

- 本地事务中先持久化物理事实和 `WmsConfirmation`（待确认状态），提交后才调用 WMS
- WMS 200 OK → 在同一事务内把 `WmsConfirmation` 更新为完成状态后提交 → 正常完成
- WMS 200 OK 但**完成状态更新事务失败**（进程崩溃或数据库故障）→ 重启后按幂等键重新确认；WMS 已记录该业务事实，重试幂等，最终把 `WmsConfirmation` 更新为完成，**不重复物理动作**
- WMS 失败（连接/超时/5xx）→ 同一幂等键重试；不事后补建 `WmsConfirmation`
- 持久重试到第 N 次后仍未成功 → 保持 `WmsConfirmation` 待确认并进入依赖暂停（**仅物理状态无法确认时才进入人工清线**）

落 `tests/integration/test_wms_idempotent_retry.py`。

- [ ] **Step 5：场景 5 — 处理幂等（同一 Evidence 并发领取仅一次投影）**

按决策 14 处理幂等交接条件，验证 WES 软件层 Handler 领取去重（物理并发互锁归 ECS，此处只验证 WES 进程内并发领取）：

- 同一 `InboundEvidence` 被多个并发 Handler 领取时，仅产生一个 `DeviceCommand`/一次投影推进
- Handler 领取按幂等键 + 处理状态去重，不依赖外部锁
- 领取失败/崩溃的 Handler 不阻塞其他 Handler 的幂等领取

落 `tests/integration/test_evidence_concurrent_processing.py`。

- [ ] **Step 6：提交 Task 10**

```bash
rtk git add tests/integration/test_inbound_evidence_ack_crash.py tests/integration/test_device_command_persist_crash.py tests/integration/test_callback_txn_concurrency.py tests/integration/test_wms_idempotent_retry.py tests/integration/test_evidence_concurrent_processing.py tests/resilience/test_human_clearline_after_restart.py
rtk gitnexus detect_changes --scope all
rtk ./.githooks/pre-commit
rtk git commit -m "test(reliability): 覆盖五类关键可靠性场景（SPEC §9.3 兼容 + 处理幂等）"
```

### Task 11：多对象流水并发的独立并发风险覆盖

多对象并发是 SPEC §8.1 与 §15.3 核心验收条件；本 Task 只覆盖**独立并发风险**（设备竞争、乱序 CALLBACK），不复制粗分机 13 类判定的逐个并发版本。物理并发互锁归 ECS/PLC（SPEC §5.2/§8.1）；SPEC §10.4 用单投放设备拓扑 + 设备忙闲保证不会并发计算同一目标，故不设位置竞争场景（避免与 §10.4 拓扑约束冲突，复用设备竞争的 WAITING 覆盖即可）。WES 测试只验证 WES 软件层的并发决策正确性。

并发测试归入 `tests/integration/concurrency/`，不另设 `tests/concurrency/` 层级。`tests/integration/` 已在 `norecursedirs` 中，并发测试由 HEAVY selector 选跑。

**实施入口条件：**独立业务实施工作包已交付并发所依赖的最终对象、接口与目标生产代码 commit 和验收。按决策 14，本 Task 建立的是跨切片并发韧性测试（SPEC §8.1/§15.3 验收条件），由测试治理计划自己建立；本 Task 不实现 handler。

**Files:**

- Create: `tests/integration/concurrency/__init__.py`
- Create: `tests/integration/concurrency/test_device_contention.py`
- Create: `tests/integration/concurrency/test_callback_out_of_order.py`
- Modify: `docs/architecture/heavy-test-impact.toml`（追加并发模块 `[[mapping]]`）

- [ ] **Step 1：场景 1 — 设备竞争**

同一设备的并发命令（第二次命令到达时第一次尚未 ACK）。验证：

- 第二条命令**保持在本地 WAITING 状态，不得下发到 ECS**（SPEC §5.2 明确"目标设备忙时由 WES 等待"，第二条被 ECS 拒收**不是**等价正确结果）
- 持久化 `DeviceCommand` 不重复
- 不存在自动 retry（SPEC §9.3）

落 `tests/integration/concurrency/test_device_contention.py`。

- [ ] **Step 2：场景 2 — 乱序 CALLBACK 与 fencing**

同一对象的多次 CALLBACK 抵达顺序与持久化顺序不一致。验证：

- 状态推进**按当前权威 `DeviceCommand`、幂等键和合法状态转移**处理（SPEC 未规定按厂商事件时间戳推进状态）
- 迟到 CALLBACK **不**主动回退已推进状态、**不**擅自推进新状态；仅作为证据持久化
- 重复 CALLBACK 通过幂等键去重
- **CALLBACK fencing**（决策 14 交接条件）：CALLBACK 携带并校验 `command`/`execution`/`line`/`epoch` 四关联键；旧 Epoch 的迟到 CALLBACK（人工清线并创建新 Epoch 后到达）只持久化为证据，**不**写入新对象或新 Epoch 投影
- 不存在"补偿"或"重放"

落 `tests/integration/concurrency/test_callback_out_of_order.py`。

- [ ] **Step 3：与 HEAVY selector 配合**

在 `docs/architecture/heavy-test-impact.toml`（Task 9a 创建）追加并发模块的 `[[mapping]]`：`source_glob` 覆盖粗分机/自动分拣/人工分拣的并发相关生产代码，`heavy_tests` 指向 `tests/integration/concurrency/` 下对应测试。

- [ ] **Step 4：提交 Task 11**

```bash
rtk git add tests/integration/concurrency/ docs/architecture/heavy-test-impact.toml
rtk gitnexus detect_changes --scope all
rtk ./.githooks/pre-commit
rtk git commit -m "test(concurrency): 覆盖独立并发风险并补 HEAVY 映射"
```

### Task 9b：Jenkinsfile HEAVY Required stage 落地（Task 10/11 之后执行）

本 Task 启用 HEAVY Required stage：在 Task 10（崩溃窗口）与 Task 11（并发）的实际测试文件已落地、且最终 Alembic 基线已交付后，引用它们作为 minimum smoke。HEAVY Required 依赖 `alembic upgrade head` 在最终干净基线上建库，基线交付前不落地。

**实施入口条件：**Task 10/11 的测试资产已经交付；业务对象、接口和目标生产代码已由独立业务实施工作包验收；**最终 Alembic generator 基线已交付**（commit、revision、空库 `upgrade head`、metadata 证据）。HEAVY Required stage 每 MR 跑 `alembic upgrade head` 建库，必须基于最终干净基线，否则结果无效；故本 Task 整体在最终基线交付后才落地（与 `HEAVY DB-Baseline` 同一基线门槛）。本 Task 不创建该基线。

**Files:**

- Modify: `Jenkinsfile`（启用 HEAVY Required stage + env harness）

本 Task 仅修改 Jenkins CI 配置，不新增或删除 `tests/` 测试资产。

- [ ] **Step 1：HEAVY 必跑集合**

每个 MR 的 HEAVY Required stage 只跑 minimum smoke + selector 选出的受影响子集：

- **每个 MR 必跑**（固定）：minimum smoke 三个文件——`tests/integration/test_inbound_evidence_ack_crash.py`、`tests/integration/test_wms_idempotent_retry.py`（Task 10）、`tests/integration/concurrency/test_device_contention.py`（Task 11）。
- **selector 选跑**（`--base origin/${CI_TARGET_BRANCH}`）：受影响 HEAVY 子集。
- **受影响时选跑**：`tests/resilience/`、`tests/e2e/`、`tests/load/`、`tests/mock/` 中被 selector 选中或被直接修改的测试。
- **nightly 全跑**（`HEAVY Full` stage）：`tests/integration`、`tests/e2e`、`tests/resilience`、`tests/load`、`tests/mock` 全集。

selector 返回空集时，stage 只跑三个 minimum smoke，不 fail。

- [ ] **Step 2：环境与执行合同**

**触发与基线**：stage 仅在 Merge Request 构建触发（`when` 限定 MR）；非 MR 的 develop/main 分支构建不跑该 stage。若未来需在非 MR 构建跑，必须为基线定义确定性回退（如 `origin/develop`），禁止空值。

**执行位置**：alembic 与 pytest 在 Jenkins agent 上执行；所有 pytest 调用必须使用 `uv run pytest`；postgres/redis 仅作容器化数据库服务，**不在 DB 容器内跑 alembic/pytest**。

**环境变量合同**：`ALEMBIC_DATABASE_URL` 显式指向测试库（`migrations/env.py` 读 `ALEMBIC_DATABASE_URL`，缺失时回退 `settings.DATABASE_URL`，故 HEAVY stage 必须显式设置 `ALEMBIC_DATABASE_URL` 指向测试库，否则迁移到错误库）；`INTEGRATION_DATABASE_URL` / `INTEGRATION_REDIS_URL` 供 pytest 集成 fixture 用；容器化服务必须在 stage `post { always }` 中无条件清理。

**测试选择与传参**：selector 用 `--base origin/${CI_TARGET_BRANCH}`，输出到文件，pytest 通过该文件读取路径（POSIX sh 安全）；minimum smoke 始终显式追加，空集不 fail。

**health check 与失败条件**：postgres/redis 就绪检查用带退避的重试循环；fail closed：env 任一缺失、selector 退出非零、服务未就绪、`alembic upgrade head` 失败、pytest 任一用例失败 → stage 退出非零。selector 退出 0 + 空集不 fail。

**最终 schema 验证**：Task 3-6 的 integration 结果不视为最终 schema 验证；最终基线交付后的全量重跑见 Task 12 Step 2。

- [ ] **Step 3：Jenkinsfile 完整 stage 表**

| Jenkins stage | 触发 | 必跑 |
|---|---|---|
| `Architecture Guardrails` | 每次 PR | Ruff/Bandit/Import Linter + 一次 `uv run pytest tests/architecture -q` |
| `Unit Tests (FAST)` | 每次 PR | `uv run pytest -q --junitxml=reports/fast-tests.xml` + 预算检查 |
| `HEAVY Selector Smoke` | 每次 PR | `uv run pytest tests/scripts -q`（Task 9a 落地后即生效） |
| `HEAVY Required` | 每次 MR（最终基线交付后启用） | selector(`--base origin/${CI_TARGET_BRANCH}`) 选受影响 HEAVY + minimum smoke；env 缺失或 selector 退出非零则 fail closed（空集只跑 minimum smoke） |
| `HEAVY Full` | nightly | 全量 `uv run pytest -q tests/integration tests/e2e tests/resilience tests/load tests/mock`（显式列出） |
| `HEAVY DB-Baseline` | 手动 | 独立生产工作包交付最终 Alembic 基线证据后，执行 Task 7 Step 3 的基线验证与全量重测 |

- [ ] **Step 4：提交 Task 9b**

```bash
rtk git add Jenkinsfile
rtk gitnexus detect_changes --scope all
rtk ./.githooks/pre-commit
rtk git commit -m "ci(test): 启用 HEAVY Required stage 与 env harness（Task 9b）"
```

### Task 12：最终验收

**实施入口条件：**独立业务实施工作包已交付全部最终对象、接口、目标生产代码及最终 Alembic generator 基线的 commit、revision、空库 `upgrade head`、metadata 一致性证据。

**Files:**

- Modify: `tests/support/test_suite_topology.py`
- Modify: `tests/architecture/test_suite_topology_guardrail.py`

- [ ] **Step 1：核对可靠性承接与删除清单**

按决策 10，核对所有删除测试的 commit message / PR 描述已标注承接的目标测试路径或 `NONE`，确认无静默丢失可靠性断言。`tests/integration/conftest.py` 等对 `RuntimeInbox` / `SystemOutbox` / `WorklineSession` 的旧依赖已随 Task 7 清除。

- [ ] **Step 2：一次最终质量执行 + HEAVY 执行**

运行：

```bash
rtk ./scripts/git-quality-gate.sh --profile quality
rtk git diff --check
```

`quality` profile 顺序执行 Ruff format/lint、Bandit、Import Linter、旧架构缺席扫描、一次 `uv run pytest tests/architecture -q`、一次默认 `uv run pytest -q --junitxml=reports/fast-tests.xml` 与快速预算检查。

随后按各工作包记录的环境和路径，**一次**跑完受影响的 integration/e2e/resilience/load/mock（仅在最终 Alembic 基线证据齐备后）；不得用默认 pytest 结果代替。

- [ ] **Step 3：确认永久治理文件就位**

确认长期治理文件（topology、`check_fast_test_budget.py`、`check_business_legacy_absence_gate.py`、`select_heavy_tests.py`、`heavy-test-impact.toml` 及其 guardrail 测试）就位且通过；`.githooks/pre-commit` 调用质量门禁。

- [ ] **Step 4：提交最终验收**

```bash
rtk git add tests/support/test_suite_topology.py tests/architecture/test_suite_topology_guardrail.py
rtk gitnexus detect_changes --scope all
rtk ./.githooks/pre-commit
rtk git commit -m "docs(test): 完成测试收敛最终验收"
```

提交前按仓库规则运行 GitNexus `detect_changes` 确认变更范围，确认没有旧执行路径或无关符号被保留。

## 7. 完成定义

全部条件同时满足才算测试收敛完成：

- 每个最终业务合同和可靠性不变量都有且只有一个主要测试所有者。
- 默认 `pytest` 只包含 `FAST`，满足 60 秒、单例 1 秒以及 `tests/unit/`、`tests/workline_plugins/` p95 100 毫秒预算（**p95 仅 N≥30 生效，N<30 跳过且不报 warn**）。
- `tests/architecture/` 只由质量门禁显式运行一次；topology 门禁在 Task 1 完成后，Task 2 才把 `tests/architecture` 调出默认收集。
- 真实数据库、HTTP、Celery、并发、等待、性能和多组件场景只存在于 `HEAVY`；`tests/mock/` 允许 fake port + 虚拟时钟降级为 FAST。
- 测试中不存在只验证旧 Runtime、Manifest、System Capability、Intent/Effect、RuntimeHold、Reconciliation、CellReservation、兼容路径或旧迁移链的代码。
- 不存在旧测试路径 alias、fixture compatibility、阶段性 allowlist 或双 gate；缺席扫描仅允许决策 22 的三条精确路径 tokenized exception；旧 Runtime 脚本与对应 guardrail 测试**已实际 `git rm`**。
- `tests/README.md`、`AGENTS.md`、Pytest 配置、质量门禁和 Jenkins 对测试层的定义一致。
- 删除测试的 commit message / PR 描述按决策 10 标注承接的目标测试路径或 `NONE`，可审计语义承接缺口。
- 默认快速回归、架构测试、**基线重建后受影响重测试**、Ruff、Bandit、Import Linter 和仓库质量门禁全部通过。
- 每个 commit 提交前必跑 GitNexus `detect_changes --scope all`（在 staging 之后）。
- HEAVY 必跑集合由 Jenkins HEAVY Required stage 在每次 PR 通过 selector 选跑 + minimum smoke 强制执行。
- 多对象并发的独立风险（设备竞争、乱序 CALLBACK 与 fencing）由 Task 11 显式测试覆盖。
- 关键可靠性五类场景（Evidence→ACK、Command persist→发送、CALLBACK 事务边界、WMS 幂等键重试、处理幂等）由 Task 10 显式测试覆盖；Task 10 Step 3 区分事务未完成 callback 与清线后迟到 callback（SPEC §9.3 兼容）。CALLBACK fencing（`command`/`execution`/`line`/`epoch` 四关联键）由 Task 11 Step 2 覆盖。处理幂等与 CALLBACK fencing 是业务切片硬性交接条件（决策 14）。
- SPEC frontmatter 必须保持 `Accepted` 状态。

## 8. 风险控制

| 风险 | 控制 |
| --- | --- |
| 误删目标可靠性断言 | 先建立最终对象测试并通过，再删除旧测试；删除 commit 按 AGENTS.md 决策 10 标注承接路径 |
| 把旧架构测试仅移动到重测试 | 语义动作与执行层分别校验，旧语义最终必须删除 |
| 默认套件继续膨胀 | 60 秒、单例 1 秒、纯单元 p95 100 毫秒自动预算（N<30 跳过）；CI 容器固定 2 vCPU / 4 GB |
| 高层测试重复领域排列 | 一个行为一个所有者，高层只验证新增边界风险 |
| 架构扫描散落并重复运行 | `QUALITY` 一次显式收集，单一规则实现 |
| 速度优化削弱失败路径 | 去重按所有权，不按成功/失败类别；业务拒绝、冲突和异常结果必须保留 |
| CI 与本地命令漂移 | `scripts/git-quality-gate.sh` 为本地编排真源，Jenkins 使用相同测试层和脚本接口 |
| `tests/integration/conftest.py` 依赖旧 Runtime 未被发现 | Task 7 删除旧 Runtime 时同步清除该依赖，Task 12 Step 1 核对 |
| Task 2 调出 architecture 后中间提交盲区 | Task 1 topology 门禁先于 Task 2 完成；每个 commit 跑 `detect_changes` |
| AGENTS.md 与 `git commit` 流程脱节 | 修改符号前跑 `impact`、每个 commit 前跑 `detect_changes`（决策 13） |
| Task 3-6 integration 验证旧迁移而非最终 schema | 集成重跑在最终 Alembic 基线重建后强制（Task 12 Step 2） |
| 旧 Runtime 脚本文件未实际删除 | Task 7 独占显式 `git rm` 四个 Runtime gate、`test_api_signature.sh`、`test_live_suite.sh` 与对应 guardrail 测试 |
| `pyproject.toml` addopts 默认噪声 | 删 `--cov-report=term-missing`（默认无 `--cov`） |
| mock 强制 HEAVY 阻碍快速反馈 | mock 可基于 fake port + 虚拟时钟降级为 FAST |
| 旧架构缺席扫描伪命中 | token 化分词 + 仅决策 22 三条精确路径的 source allowlist |
| `test_test_*` 双前缀文件名 | Task 2 重命名为 `test_suite_topology_guardrail.py` |
| HEAVY 重测无强制门禁 | Task 9b 落地：Jenkins HEAVY Required stage 每次 PR 必跑 + 受 PR 改动选跑 |
| 关键可靠性崩溃窗口未覆盖 | Task 10 落地：4 类崩溃窗口测试 + SPEC §9.3 兼容语义 |
| 多对象流水并发缺明确所有者 | Task 11 显式覆盖：设备竞争、乱序 CALLBACK（位置竞争由 SPEC §10.4 单投放设备拓扑排除） |
| SPEC 未处于 `Accepted` 状态 | §1 加固，Task 1 启动前确认已接受 SPEC |
| 删除矩阵控制面后可靠性承接不可机器审计 | 决策 10：删除 commit 标注承接路径；用户接受 PR review 审计（D4） |

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` (outside voice) | Independent 2nd opinion | 1 | issues_found | 14 findings |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (PLAN) | 11 issues, 0 critical gaps, mode=SCOPE_REDUCED |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **CODEX:** Outside voice 提出 14 处——2 处直接挑战 review 决策（删矩阵后的可靠性承接审计、§10.4 拓扑约束仍缺原子 claim）、1 处 plan-spec 文字矛盾（§9.3 vs Task 10 Step 3）、其余为 spec 并发/幂等/fencing 语义缺口与 plan 内部问题（FAST 保证虚假、入口条件倒置、HEAVY selector 脆弱、基线顺序不一致、战略过度捆绑）。
- **CROSS-MODEL:**
  - 承接审计：Review 判 PR review 足够；Codex 指出 absence gate/topology 不能证明删掉的可靠性断言已被承接。用户裁决（D4）：接受不可审计代价，纯 PR review。
  - spec 并发语义：Review 只抓 §10.4；Codex 找到 6 处更深缺口（处理幂等、原子 claim、CALLBACK fencing、WMS 入站幂等、WMS 拒绝分歧、TransportTask 终态）。用户裁决（D5）：物理并发归 ECS，WES 不过度设计；§9.3 矛盾必修；处理幂等 + CALLBACK fencing 作实现清单（不进 spec 冻结）。
- **VERDICT:** ENG CLEARED (PLAN, SCOPE_REDUCED) — 5 项决策已决（D1-D5），0 unresolved，0 critical gaps，11 issues。7 个实施任务（T1-T7）已全部落地：plan 移除矩阵控制面（T1）、修 §9.3/Task 10 矛盾（T2）、FAST 三层保证（T4）、目标测试随切片交付（T5）、Task 9b 基线门槛（T6）；SPEC §10.4 拓扑约束（T3）；TODOS 记录 WES 软件层幂等/fencing 实现清单（T7）。

NO UNRESOLVED DECISIONS
