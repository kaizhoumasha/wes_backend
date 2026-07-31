# WES 测试语义与重量收敛实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前测试套件收敛为以 WES 最小执行架构 SPEC 为唯一语义基线、默认执行轻量且每项行为只有一个明确测试所有者的最终测试体系。

**Architecture:** 先用逐文件处置矩阵冻结现状和目标归属，再以目标垂直切片为单位先建立最终对象测试、后删除旧架构测试。测试按 `FAST`、`QUALITY`、`HEAVY` 三个执行层分离；语义处置与执行重量正交管理，避免用移动目录掩盖旧行为，也避免因删除旧实现而丢失可靠性不变量。

**Tech Stack:** Python 3.13、Pytest 9、pytest-asyncio、JUnit XML、Ruff、Bandit、GitNexus、Jenkins。

---

## 1. 实施基线

唯一设计基线：

- `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`（**该 SPEC 必须在 Task 1 启动前先合并为非 `Review Requested` 状态**；当前 frontmatter `status: Review Requested` 状态不能作为不可逆删除依据。）

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
11. 矩阵 schema 去除 `execution_tier` 字段，tier 由 `target_path` 路径前缀唯一决定。
12. 矩阵 CSV 强制 `csv.QUOTE_ALL` + LF，由 Python `csv` 模块负责 quoting：双引号转 `""`、反斜杠保留原样、不引入手工转义。
13. 新增 `test_*.py` 必须先在矩阵登记（OPEN）才能提交；pre-commit hook 强制矩阵预登记。
14. `tests/architecture/test_test_suite_topology_guardrail.py` 重命名为 `tests/architecture/test_suite_topology_guardrail.py`，消除 `test_test_` 双前缀。
15. 默认 `pytest` 收集路径下 test_*.py 不得依赖真实数据库、HTTP、Celery、Redis、容器等真实服务；不通过 import AST 黑名单扫描实现，而由 `tests/integration|e2e|resilience|load|mock` 目录位置和 `norecursedirs` 共同保证。
16. 矩阵 `evidence_note` 字段：长度 ≥ 30 字符；`REWRITE` / `SPLIT` 行必须同时含原路径与目标路径关键词；`status=CLOSED` 行 `evidence_note` 不得为空。
17. `check_test_convergence_matrix.py` 支持 `--final` 旗标：要求矩阵中**所有行**（含 `DELETE`）都已 `CLOSED`；不通过则 fail。`--final` 仅在 Task 12 Step 1（矩阵退役前最后门禁）执行；Task 1 至 Task 9b（含 9a、10、11）的每次提交前都跑默认完整性检查。
18. 矩阵 `work_package` 字段为临时列，Task 12 末期随矩阵一起删除。
19. 矩阵覆盖范围扩展至所有 `tests/conftest.py`、`tests/<dir>/conftest.py`、`tests/fixtures/`、`tests/support/`、`tests/mock/` 等非 test_*.py 测试资产；`tests/integration/conftest.py` 依赖 `RuntimeInbox` / `SystemOutbox` / `WorklineSession` 等旧符号，删除旧 Runtime 后会引发 pytest collection 阶段 ImportError，必须在矩阵中显式登记处置。
20. `status=CLOSED` 非 `DELETE` 行要求 `target_path` 存在；`CLOSED` 的 `DELETE` 行 `target_path` 为空且 `test_path` 已从仓库删除。
21. 门禁顺序：Task 1 矩阵与拓扑门禁在 Task 2 把 `tests/architecture` 调出默认收集之前完成；Task 1 至 Task 9b（含 9a、10、11）的每次提交前都跑 `check_test_convergence_matrix.py` 默认完整性检查，**`--final` 仅在 Task 12 Step 1 执行**。
22. GitNexus 使用：修改函数、类、方法前必跑 `impact` 评估 blast radius（AGENTS.md 硬规则）；每个 commit 提交前必跑 `detect_changes --scope unstaged` 确认变更范围。两者职责不同，不互相替代。
23. Task 12 集成重跑在最终 Alembic 基线重建后强制执行；Task 3-6 的 integration 结果不视为最终 schema 验证。
24. Task 8 显式 `git rm` 实际不再调用的 `scripts/check_runtime_*_gate.py` 5 个、`scripts/test_api_signature.sh`、`scripts/test_live_suite.sh` 及其对应 guardrail 测试；不只从 `git-quality-gate.sh` 删硬编码引用。
25. `pyproject.toml` 清理 `addopts`：删除默认情况下无效的 `--cov-report=term-missing`（无 `--cov` 不触发），仅保留 `-v --durations=10 --tb=short`；JUnit XML 选项在质量门禁脚本中显式传。
26. 速度预算扩展：`tests/unit/` 与 `tests/workline_plugins/` p95 仅在 N≥30 时生效，N<30 跳过且不报 warn；`junit_family=xunit2` 在 `pyproject.toml` 设置；CI 容器固定 2 vCPU / 4 GB 配额。
27. 默认 `pytest` 预算基线：固定套件 60 秒、单例 1 秒、`tests/unit/` 与 `tests/workline_plugins/` p95 100 毫秒（p95 仅 N≥30 生效）；`pyproject.toml` 的 `addopts` 与 `norecursedirs` 决定 FAST 范围，预算在**本次 PR 的 JUnit 实测耗时**上一次性校验，**不引入历史缓存或跨 PR 状态**。若本次 FAST 实测耗时超过 60 秒，直接 fail 并提示按 §3 重新评估测试所有权。
28. `AGENTS.md` 与 `tests/README.md` 各按职责说明测试所有权与运行方式；不强制两文档做词级镜像检查。

29. `tests/architecture/` 不预设文件数上限；架构测试按领域自然收敛。

30. `tests/mock/` 虽位于 HEAVY 目录，但若仅依赖 fake port 与虚拟时钟、可以以纯 in-memory 实现稳定在 FAST 层时，应改入 FAST 路径；不允许仅以目录名判定 HEAVY。
31. 最终旧架构缺席扫描存在"伪命中"风险：当前态文档和 SPEC 本身必须提及被禁止的旧名称；扫描器必须使用 token 化分词并显式 allowlist 当前态文档和 SPEC 自身。

**范围限定**：本计划只负责测试治理（保留、改写、删除、分层与门禁）。Tasks 3-6 中“目标实现”涉及的生产代码由独立业务实施工作包承担，不在本计划展开。

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
- 质量门禁只以一次 `pytest tests/architecture -q` 执行全部架构测试。
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

## 5. 逐文件处置矩阵

实施期创建 `docs/architecture/wes-test-convergence-matrix.csv`，**覆盖以下所有测试资产**：

- 当前 `tests/` 下所有 `test_*.py`（共 459 个起点）
- 所有 `tests/conftest.py` 与 `tests/<dir>/conftest.py`
- `tests/fixtures/`、`tests/support/`、`tests/mock/` 资产

每个资产恰好一行，字段固定为：

| 字段 | 约束 |
| --- | --- |
| `test_path` | 处置前测试资产路径（test_*.py 或 conftest.py 或 fixture/support/mock 资产路径），唯一；`OPEN` 时必须存在，关闭改写或删除后允许不存在 |
| `current_semantics` | 当前实际证明的行为，不写文件名复述 |
| `target_capability` | SPEC 中的目标合同、对象、业务流程或 `NONE` |
| `behavior_owner` | 最终唯一测试层和领域 |
| `semantic_action` | `KEEP`、`REWRITE`、`SPLIT`、`REPLACE_GATE`、`DELETE` |
| `target_path` | 保留或替换后的确切路径；`DELETE` 使用空值 |
| `duplicate_of` | 被合并到的权威测试路径；不存在重复时为空 |
| `work_package` | `BASELINE`、`CORE_ROUGH_SORTER`、`AUTO_INBOUND`、`MANUAL_FOUR_LINE`、`OUTBOUND_EXCHANGE`、`FINAL_REMOVAL`（**临时列，Task 12 末期随矩阵一起删除**） |
| `deletion_gate` | 删除前必须已通过的目标测试或永久门禁路径 |
| `evidence_note` | 保留、改写或删除的具体理由；**长度 ≥ 30 字符**；`REWRITE`/`SPLIT` 行必须同时含原路径与目标路径关键词；`status=CLOSED` 行不得为空 |
| `status` | `OPEN` 或 `CLOSED`；`--final` 时所有行必须为 `CLOSED`：非 `DELETE` 行 `target_path` 必须存在，`DELETE` 行 `target_path` 为空且 `test_path` 不存在。由脚本按当前文件状态校验（不依赖 `git log`/`git show`） |

**execution_tier 字段已删除**：tier 由 `target_path` 路径前缀唯一决定，路径必须落在 §4.1 / §4.2 / §4.3 列出的目录中。脚本 `check_test_convergence_matrix.py` 自动派生，不在 CSV 中重复表达。

**CSV 物理格式**：实施期 `check_test_convergence_matrix.py` 使用 `csv.writer(..., quoting=csv.QUOTE_ALL, lineterminator="\n")`；**所有 quoting 由 Python `csv` 模块负责**——`csv.QUOTE_ALL` 下双引号转 `""`、**反斜杠保持原样不转义**；不引入手工第二套转义规则。任何手写或外部工具未遵守此格式的修改在 Task 1 Step 4 验证中将被拒绝。

语义动作定义：

- `KEEP`：已经直接使用目标对象并证明最终行为，仅允许去重和轻量化。
- `REWRITE`：业务语义保留，但测试必须从旧对象重写到最终对象。
- `SPLIT`：同一文件混合目标行为与旧实现断言；目标行为迁入权威路径，旧断言删除。
- `REPLACE_GATE`：旧架构存在性或旧平台形状测试，替换为最终架构缺席或依赖边界门禁。
- `DELETE`：只验证旧架构、兼容、迁移链、实现细节，或与权威测试完全重复。

矩阵不能使用模糊说明。每个 `REWRITE`、`SPLIT`、`REPLACE_GATE` 行必须给出确切 `target_path` 和 `deletion_gate`；每个 `DELETE` 行必须说明为什么不存在需要迁移的目标行为。实施中新增的测试文件也必须作为既有 `target_path` 落地，或新增独立矩阵行；矩阵自身的临时 guardrail 标为 `DELETE`，在 `FINAL_REMOVAL` 关闭。

**`check_test_convergence_matrix.py` 验证规则**（每条规则在 `tests/architecture/test_convergence_matrix_guardrail.py` 中有独立正反例 fixture）：

1. 当前全部 `test_*.py`、全部 `conftest.py`、`tests/fixtures/`、`tests/support/`、`tests/mock/` 资产路径被矩阵行覆盖。**覆盖**定义为：资产路径命中某行 `test_path`（OPEN 状态，资产未动）或某行 `target_path`（CLOSED 状态，资产已落地到 target）；DELETE 行的 `target_path` 为空，仅适用于旧资产已不在当前 tree 的情形。新增资产若未在矩阵登记（无对应 OPEN 行），脚本退出非零。
2. `semantic_action`、`work_package`、`status` 只使用固定枚举值。
3. 非 `DELETE` 行具有确切 `target_path`，且 `target_path` 路径前缀唯一决定其 tier（`tests/architecture/*` → QUALITY；`tests/integration/`、`tests/e2e/`、`tests/resilience/`、`tests/load/`、`tests/mock/`（含 `tests/integration/concurrency/`）→ HEAVY；其他 → FAST）。
4. `REWRITE` / `SPLIT` / `REPLACE_GATE` 行具有 `deletion_gate`；`status=CLOSED` 行的 `deletion_gate` 字段已记录为对应目标测试路径或永久门禁路径字符串。checker 只校验该字段完整性与当前文件状态；**门禁真实通过由 Task 12 Step 2/3 的一次统一执行覆盖 `deletion_gate` 清单证明**（不依赖 `git log`/`git show`，它们只证明文件历史）。
5. `OPEN` 行的 `test_path` 必须存在（资产未动）；`CLOSED` 非 `DELETE` 行的 `target_path` 必须存在（资产已落地）；`CLOSED` 的 `DELETE` 行 `target_path` 为空且 `test_path` 已实际从仓库删除。
6. CSV 物理格式：每字段以 `"` 包裹，lineterminator=`\n`；`evidence_note` ≥ 30 字符，`REWRITE`/`SPLIT` 行 `evidence_note` 含原路径与目标路径关键词。
7. **新增测试若不是矩阵行或已声明目标路径，门禁失败**（pre-commit hook 在 commit 前调用脚本）。
8. `--final` 旗标：要求矩阵中**所有行**（含 `DELETE`）都已 `CLOSED`；任何 `OPEN` 行都使脚本退出非零。`--final` 仅在 Task 12 Step 1（矩阵退役前最后门禁）执行一次；Task 12 Step 4 删除脚本后所有阶段不再调用 `check_test_convergence_matrix.py`。

## 6. 文件职责图

实施期治理文件：

| 文件 | 职责 |
| --- | --- |
| `docs/architecture/wes-test-convergence-matrix.csv` | 临时逐文件语义、所有权、删除门禁与通过时间；CSV `QUOTE_ALL` + LF 物理格式 |
| `scripts/check_test_convergence_matrix.py` | 验证矩阵覆盖、枚举值、路径唯一性、关闭状态、evidence_note 长度/关键标识；支持 `--final` 旗标 |
| `tests/architecture/test_convergence_matrix_guardrail.py` | 通过脚本接口验证矩阵和新增测试不会绕过归类；每条验证规则有独立正反例 fixture |
| `tests/support/test_suite_topology.py` | 最终目录层级、默认排除目录、文件体量规则 |
| `tests/architecture/test_suite_topology_guardrail.py` | 验证最终测试拓扑与默认收集边界；**文件已重命名** |
| `scripts/check_fast_test_budget.py` | 从 JUnit XML（`junit_family=xunit2`，由 `pyproject.toml` 设置）验证默认套件总耗时、单例耗时与纯单元 p95（p95 仅 N≥30 生效） |
| `tests/scripts/test_check_fast_test_budget.py` | 预算解析、边界值、N<30 跳过与失败输出的单元测试 |
| `scripts/check_business_legacy_absence_gate.py` | 最终旧架构符号、import、配置和 fallback 的单一缺席扫描；当前态文档与 SPEC 自身 token 化 allowlist |
| `tests/architecture/test_business_legacy_absence_guardrail.py` | 缺席扫描的规则合同 |
| `scripts/git-quality-gate.sh` | 只编排静态检查、一次架构测试和一次默认快速回归；显式 `--junitxml=reports/fast-tests.xml`（`junit_family=xunit2` 在 `pyproject.toml` 中设置） |
| `pyproject.toml` | Pytest 默认只收集 `FAST`；`addopts` 清理（删 `--cov-report=term-missing`） |
| `.pre-commit-config.yaml` | pre-commit hook 强制 `check_test_convergence_matrix.py` 在 commit 前通过（矩阵存在期间 Task 1 至 Task 9b 有效，Task 12 Step 4 同步移除） |
| `Jenkinsfile` | 分别执行架构门禁、默认快速回归和受环境支持的重测试；**集成重跑必须在 Task 12 单一干净基线重建后执行** |
| `tests/README.md` | 最终测试所有权、目录、预算和运行方式 |
| `AGENTS.md` | Agent 新增和修改测试时的长期硬约束（不与 `tests/README.md` 做词级镜像检查） |

**长期治理文件**（不随 Task 12 退役，持续维护）：

| 文件 | 职责 |
| --- | --- |
| `scripts/select_heavy_tests.py` | HEAVY selector：按目标分支 diff 选受影响 HEAVY 测试，区分直接修改的 HEAVY 测试、候选范围与 ignore_globs |
| `docs/architecture/heavy-test-impact.toml` | selector 的机器可读映射真源；新增生产模块/迁移/基础设施配置必须补 `[[mapping]]` 或显式 NONE |
| `tests/scripts/test_select_heavy_tests.py` | selector 协议、候选范围分类与 TOML schema 的永久单元测试 |

目标业务测试路径由矩阵锁定，并随对应生产实现工作包一起创建或改写。**Task 1 矩阵与拓扑门禁必须先于 Task 2 把 `tests/architecture` 调出默认收集完成**；Task 1 至 Task 9b（含 9a、10、11）的每次提交前都跑 `check_test_convergence_matrix.py` 默认完整性检查，**`--final` 仅在 Task 12 Step 1 执行**。**每个 commit 提交前必跑 GitNexus `detect_changes --scope unstaged`**。

## 7. 实施任务

### Task 1：建立完整语义与重量清单

**Files:**

- Create: `docs/architecture/wes-test-convergence-matrix.csv`
- Create: `scripts/check_test_convergence_matrix.py`
- Create: `tests/architecture/test_convergence_matrix_guardrail.py`
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

按测试内容而非文件名判断。**覆盖范围**包括：

- 459 个 `test_*.py` 起点
- 所有 `tests/conftest.py` 与 `tests/<dir>/conftest.py`（特别关注 `tests/integration/conftest.py` 对 `RuntimeInbox` / `SystemOutbox` / `WorklineSession` 的依赖）
- `tests/fixtures/`、`tests/support/`、`tests/mock/` 资产

优先审查包含 `RuntimeInbox`、`RuntimeIntent`、`RuntimeHold`、`ExecutionSession`、`WorklineSession`、`PluginBinding`、`SystemCapability`、`CellReservation`、`Reconciliation`、`generated_index` 和 `SystemOutbox` 的文件，但这些词只用于排序，不直接决定删除。

CSV 物理格式必须遵守 `csv.QUOTE_ALL` + LF：**仅由 Python `csv` 模块负责 quoting**——`csv.QUOTE_ALL` 下双引号转 `""`、**反斜杠保持原样不转义**；**禁止第二套手工转义规则**。

- [ ] **Step 3：实现矩阵校验**

脚本必须实现 §5 列出的 8 条验证规则，并支持 `--final` 旗标：

1. 全部 `test_*.py`、conftest、fixtures、support、mock 资产路径被 `OPEN` 行覆盖；新增资产若未登记则 fail。
2. `semantic_action` / `work_package` / `status` 只用固定枚举。
3. 非 `DELETE` 行有确切 `target_path`；`target_path` 路径前缀唯一决定 tier。
4. `REWRITE` / `SPLIT` / `REPLACE_GATE` 行具有 `deletion_gate`；`status=CLOSED` 行其 `deletion_gate` 已记录为对应目标测试路径或永久门禁路径字符串。
5. `OPEN` 行的 `test_path` 必须存在；`CLOSED` 非 `DELETE` 行的 `target_path` 必须存在；`CLOSED` 的 `DELETE` 行 `target_path` 为空且 `test_path` 已删除。
6. CSV 物理格式：QUOTE_ALL + LF；`evidence_note` ≥ 30 字符；`REWRITE`/`SPLIT` 行 `evidence_note` 含原路径与目标路径关键词。
7. 新增测试若未在矩阵登记，门禁失败（pre-commit hook 强制点）。
8. `--final` 旗标：要求矩阵中**所有行**（含 `DELETE`）都已 `CLOSED`；任何 `OPEN` 行都使脚本退出非零。`--final` 仅在 Task 12 Step 1 执行。

每条规则在 `tests/architecture/test_convergence_matrix_guardrail.py` 都有独立正反例 fixture：含 11 个字段的合法 CSV、missing row、bad enum、missing target_path、missing deletion_gate（REWRITE 行）、OPEN 不存在路径、CLOSED target_path 不存在、未在矩阵的新 `test_*.py`、`evidence_note` 长度不足、QUOTE_ALL 不一致等。

- [ ] **Step 4：验证矩阵门禁**

运行：

```bash
rtk uv run pytest tests/architecture/test_convergence_matrix_guardrail.py -q
rtk uv run python scripts/check_test_convergence_matrix.py
```

期望：矩阵完整通过；故意遗漏一个临时 fixture 时合同测试能够证明脚本失败，恢复 fixture 后再次通过。`--final` 不在本步骤执行（仅 Task 12 Step 1）。

- [ ] **Step 5：提交基线清单**

```bash
rtk gitnexus detect_changes --scope unstaged
rtk git add docs/architecture/wes-test-convergence-matrix.csv scripts/check_test_convergence_matrix.py tests/architecture/test_convergence_matrix_guardrail.py tests/support/test_suite_topology.py
rtk git commit -m "docs(test): 建立测试语义与重量收敛清单"
```

### Task 2：建立三层执行拓扑和速度预算

**Files:**

- Modify: `pyproject.toml`
- Modify: `tests/support/test_suite_topology.py`
- Modify: `tests/architecture/test_suite_topology_guardrail.py`
- Create: `scripts/check_fast_test_budget.py`
- Create: `tests/scripts/test_check_fast_test_budget.py`
- Modify: `.pre-commit-config.yaml`
- Modify: `tests/README.md`

- [ ] **Step 1：先写失败的拓扑合同**

增加以下断言：

- `tests/architecture/` 与五个重测试目录不进入默认收集。
- **删除**“`FAST` 路径不包含矩阵中标记为 `QUALITY` 或 `HEAVY` 的文件”——`execution_tier` 字段已删除，tier 由 `target_path` 路径前缀唯一决定；该断言改为“`tests/architecture/*` 路径不可被默认 `pytest` 收集；`tests/integration/`、`tests/e2e/`、`tests/resilience/`、`tests/load/`、`tests/mock/` 路径不可被默认 `pytest` 收集”。
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

在 `pyproject.toml` 中把 `tests/architecture` 与 `tests/scripts` 加入 `norecursedirs`。**清理 `addopts`**：删除 `--cov-report=term-missing`（默认无 `--cov` 不触发且增加噪声），仅保留 `-v --durations=10 --tb=short`；JUnit XML 选项在质量门禁脚本中显式传，`junit_family=xunit2` 在 `pyproject.toml` 中设置，不放入 `addopts`。真实数据库、HTTP、Celery、并发、等待和性能测试按矩阵移动到现有五个重测试目录；不得通过 marker 让重测试继续混在 `FAST` 路径。

`tests/architecture` 调出默认收集**必须晚于** Task 1 的矩阵与拓扑门禁建立；Task 1 Step 4 通过前，矩阵与 topology guardrail 仍在默认收集范围。

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

**预算可达性预检**：跑一次 `uv run pytest -q --junitxml=reports/fast-tests.xml`，从 JUnit 实测耗时判断 60s 是否可达（**收集耗时不能代表执行耗时，禁止用 `--collect-only` 估算均值**）；若实测超过 60s，**回到 Step 1 调整矩阵覆盖（缩小默认套件规模或重排）后重跑 Step 5**。此阶段允许总时长暂未达到最终预算，但每个超限文件必须已在矩阵中标为后续改写、合并、移动或删除，不能加入永久豁免。

- [ ] **Step 6：提交执行拓扑**

```bash
rtk gitnexus detect_changes --scope unstaged
rtk git add pyproject.toml tests/support/test_suite_topology.py tests/architecture/test_suite_topology_guardrail.py scripts/check_fast_test_budget.py tests/scripts/test_check_fast_test_budget.py tests/README.md .pre-commit-config.yaml
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

只 stage 本工作包矩阵行列出的生产和测试路径，核对暂存 diff 后提交。提交前必跑：

```bash
rtk gitnexus detect_changes --scope unstaged
rtk uv run python scripts/check_test_convergence_matrix.py
rtk uv run pre-commit run --all-files
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

只 stage `AUTO_INBOUND` 矩阵行列出的确切路径，提交前必跑：

```bash
rtk gitnexus detect_changes --scope unstaged
rtk uv run python scripts/check_test_convergence_matrix.py
rtk uv run pre-commit run --all-files
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

只 stage `MANUAL_FOUR_LINE` 矩阵行列出的确切路径，提交前必跑：

```bash
rtk gitnexus detect_changes --scope unstaged
rtk uv run python scripts/check_test_convergence_matrix.py
rtk uv run pre-commit run --all-files
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

只 stage `OUTBOUND_EXCHANGE` 矩阵行列出的确切路径，提交前必跑：

```bash
rtk gitnexus detect_changes --scope unstaged
rtk uv run python scripts/check_test_convergence_matrix.py
rtk uv run pre-commit run --all-files
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

只 stage `FINAL_REMOVAL` 矩阵行、最终基线和缺席门禁列出的确切路径。** 显式 `git rm`**：`scripts/check_runtime_evidence_readiness_gate.py`、`scripts/check_runtime_production_closure_gate.py`、`scripts/check_runtime_production_e2e_gate.py`、`scripts/check_runtime_toggle_release_gate.py`、`scripts/test_api_signature.sh`、`scripts/test_live_suite.sh` 与其对应 guardrail 测试（`tests/architecture/test_runtime_*`、`test_no_legacy_unbound_runtime.py`、`test_northbound_*` 等）必须实际从仓库删除；不接受"仅从 `git-quality-gate.sh` 删硬编码引用"形式。提交前必跑：

```bash
rtk gitnexus detect_changes --scope unstaged
rtk uv run python scripts/check_test_convergence_matrix.py
rtk uv run pre-commit run --all-files
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

`scripts/git-quality-gate.sh` 删除硬编码 `tests/runtime/orchestration/`、`tests/contracts/workline/`、`tests/characterization/workline_legacy/`、旧 readiness/closure 和单文件 architecture 调用。同时**显式 `git rm`**：

- `scripts/check_runtime_evidence_readiness_gate.py`
- `scripts/check_runtime_production_closure_gate.py`
- `scripts/check_runtime_production_e2e_gate.py`
- `scripts/check_runtime_toggle_release_gate.py`
- `scripts/test_api_signature.sh`
- `scripts/test_live_suite.sh`
- `tests/architecture/test_runtime_*`（逐一确认）
- `tests/architecture/test_no_legacy_unbound_runtime.py`
- `tests/architecture/test_northbound_legacy_removal.py` 与其他 `test_northbound_*` 旧架构 guardrail
- `tests/architecture/test_legacy_matrix_contract.py` 与 `tests/architecture/test_cleanup_matrix_guardrail.py` 旧 Runtime 清理矩阵（其语义已被本计划的 `check_test_convergence_matrix.py` 取代）

未发布系统零兼容；不接受"仅删硬编码引用"形式。

- [ ] **Step 2：建立单一质量路径**

本地 `quality` profile 顺序固定为：

1. Ruff format。
2. Ruff lint。
3. Bandit。
4. Import Linter。
5. 最终架构和旧架构缺席扫描。
6. 一次 `pytest tests/architecture -q`。
7. 一次默认 `pytest -q --junitxml=reports/fast-tests.xml`。
8. 快速测试预算检查（强制模式，非 `--report-only`）。

删除不再有独立价值的 `ci-smoke`、`full` 和旧 Runtime 专用 check 名称，不保留命令兼容分支。

- [ ] **Step 3：更新 Jenkins**

`Architecture Guardrails` stage 只执行最终静态门禁和一次 architecture suite；`Unit Tests` stage 只执行默认 `FAST` 并检查 JUnit 预算。**真实基础设施支持的 `tests/integration/`、`tests/e2e/`、`tests/resilience/`、`tests/load/`、`tests/mock/` 重测必须由独立 stage 执行，且强制在 Task 7 Step 3 单一干净 Alembic 基线重建后运行（最终重跑见 Task 12 Step 3）**；Task 3-6 的 integration 结果不视为最终 schema 验证。重测 stage 不伪装成 Unit Tests；HEAVY 必跑集合与受 PR 改动选跑由 Task 9a/9b 显式落地。

- [ ] **Step 4：同步长期规则**

`AGENTS.md` 和 `tests/README.md` 使用最终对象、测试层和速度预算；删除 Runtime service、orchestrator、intent、session resolver、插件模板和固定旧路径说明。

- [ ] **Step 5：验证门禁没有重复执行**

运行：

```bash
rtk ./scripts/git-quality-gate.sh --profile quality
rtk rg -n "tests/runtime/orchestration|tests/contracts/workline|tests/characterization/workline_legacy|runtime-contract-guardrails" scripts/git-quality-gate.sh Jenkinsfile AGENTS.md tests/README.md
rtk uv run pre-commit run --all-files
```

期望：质量门禁通过；第二条命令零命中；默认测试和 architecture suite 各只执行一次；pre-commit 全绿。

- [ ] **Step 6：提交门禁收敛**

```bash
rtk gitnexus detect_changes --scope unstaged
rtk git add scripts/git-quality-gate.sh scripts/architecture-guardrails.sh Jenkinsfile AGENTS.md tests/README.md
rtk git commit -m "ci(test): 收敛测试门禁与执行预算"
```

### Task 9a：HEAVY selector 落地（独立可提交，不依赖后续 Task）

本 Task 拆分原“Task 9”，先把 selector 脚本、其单元测试、HEAVY 影响 TOML 映射、Jenkinsfile 增量交付并提交；minimum smoke 与最终 Jenkinsfile HEAVY Required stage 留到 Task 9b（Task 10/11 之后），避免 forward reference。

**Files:**

- Create: `scripts/select_heavy_tests.py`
- Create: `tests/scripts/test_select_heavy_tests.py`
- Create: `docs/architecture/heavy-test-impact.toml`（selector 的机器可读映射真源）
- Modify: `Jenkinsfile`（仅增加 `HEAVY Selector Smoke` stage，**不**启用 HEAVY Required 与 minimum smoke）
- Modify: `scripts/git-quality-gate.sh`（quality profile 追加 `pytest tests/scripts -q`；**不**加真实 HEAVY，HEAVY 只留 Jenkins stage）
- Modify: `AGENTS.md`（新增生产模块必须补 mapping/NONE 的硬规则；selector 与 TOML 的长期维护责任）
- Modify: `tests/README.md`（selector 本地运行方式与 HEAVY 映射维护说明）

- [ ] **Step 1：定义 selector 输入协议与候选范围**

`scripts/select_heavy_tests.py` 接受以下输入之一（互斥）：

- `--scope <staged|unstaged>`：本地开发用，与 `gitnexus detect_changes` 一致；默认 `unstaged`。
- `--base <ref>`：CI 用，计算 `<ref>...HEAD` 的提交差异。Jenkins 经 `CleanBeforeCheckout` 检出已提交代码后工作区无 unstaged/staged diff，**CI 必须用 `--base origin/${CI_TARGET_BRANCH}`**；`--scope` 在 CI 会稳定返回空集，不能作为 PR CI 协议。
- 输出：每行一个测试路径到 stdout。

selector 先把每个改动文件分到三类之一，再决定选择或 fail（避免 docs/CI 编排改动阻断自身 PR）：

- **直接修改的 HEAVY 测试**（路径匹配 `tests/{integration,e2e,resilience,load,mock}/**/test_*.py`）：直接选中该测试自身，无需 mapping。
- **候选范围**（可能影响运行时，**必须命中** mapping 或显式 NONE，未命中 fail closed）：生产代码与应用入口 `src/**`、`main.py`；Alembic 迁移与配置 `migrations/**`、`alembic.ini`；运行时基础设施配置 `docker-compose*.yml`、`pyproject.toml`、`.env*`；HEAVY 支撑资产 `tests/{integration,e2e,resilience,load,mock}/**`（HEAVY 目录下非 `test_*.py` 的全部资产，含 conftest、fixtures、support 与平铺辅助如 `tests/mock/device_simulator.py`、`tests/load/runtime_benchmark_scenarios.py`）、`tests/support/**`。
- **ignore_globs**（不影响 HEAVY，命中即忽略，不选择也不 fail）：`docs/**`、`*.md`、`tests/{unit,workline_plugins,scripts,architecture}/**`、CI 编排 `Jenkinsfile`、`.pre-commit-config.yaml`、`.github/**`、`.gitlab-ci.yml`、`README*`、`LICENSE*`。

CI 编排文件（`Jenkinsfile`、`.pre-commit-config.yaml` 等）只影响 CI 流程，不映射到业务 HEAVY，由 selector 单元测试与 HEAVY minimum smoke 验证。

退出码语义：

- 退出 0 + 非空输出：选出的受影响 HEAVY 测试（含直接修改的 HEAVY 测试自身 + 候选范围命中的 mapping）。
- 退出 0 + 空输出：改动只在 ignore_globs 或显式 NONE 内（合法无影响）；Jenkins 据此只跑 minimum smoke，**不** fail。
- 退出非 0：**任何未分类路径**（不在 ignore_globs、不属于直接修改的 HEAVY 测试、未命中任何 `source_glob` 或显式 NONE）、git diff 失败、输出路径非 `tests/.../test_*.py` → fail closed，不 fallback 到 minimum smoke。未分类一律 fail closed，强制开发者明确归类，避免静默遗漏。

- [ ] **Step 2：定义 HEAVY 映射 TOML schema**

`docs/architecture/heavy-test-impact.toml` 是 selector 的机器可读映射真源（不用 Markdown，Markdown 无法表达确定性匹配规则）。字段：

- 顶层 `ignore_globs`：确切 glob 列表（与 Step 1 一致：`docs/**`、`*.md`、`tests/{unit,workline_plugins,scripts,architecture}/**`、`Jenkinsfile`、`.pre-commit-config.yaml`、`.github/**`、`.gitlab-ci.yml`、`README*`、`LICENSE*`）；命中即不选择也不 fail。
- 每条 `[[mapping]]`：
  - `source_glob`：候选范围内的生产/迁移/运行时配置/HEAVY 支撑资产 glob（相对仓库根）。
  - `heavy_tests`：对应 HEAVY 测试路径列表（仓库内 `tests/.../test_*.py`）。**空列表 = 显式 NONE**（该模块已评估，改动不触发任何 HEAVY 测试）。

初始 mapping 必须登记：运行时入口与迁移配置（`main.py`、`migrations/env.py`、`alembic.ini` 等变更映射到全局 HEAVY 回归集或显式 NONE）；HEAVY 支撑资产（`tests/{integration,e2e,resilience,load,mock}/**` 下非 `test_*.py` 的 conftest、fixtures、support 与平铺辅助，以及 `tests/support/**`）变更映射到对应 HEAVY 回归集（共享 integration conftest 变更至少跑 integration 受影响回归集）；粗分机、自动分拣、人工分拣、出库/满箱交换模块由业务 mapping 覆盖，并发模块由 Task 11 Step 4 补充。

匹配与去重规则：

- 改动文件先判 ignore_globs → 再判是否直接修改的 HEAVY 测试（自选）→ 再判候选范围的 mapping。
- 候选范围文件匹配多条 `source_glob` 时，取 `heavy_tests` 并集后去重。
- 两条 `source_glob` 不允许重叠且 `heavy_tests` 冲突；schema 校验发现歧义即 fail。
- selector 输出的每个路径必须匹配 `tests/.../test_*.py`，否则 fail closed（拒绝非测试路径与空白注入）。
- **任何未分类路径**（不在 ignore_globs、不属于直接修改的 HEAVY 测试、未命中任何 `source_glob` 或显式 NONE）→ fail closed，强制明确归类。ignore_globs 内的文件不触发此规则。
- **glob 语法合同**：TOML 允许 `{a,b}` brace 简写，但 Python 标准库 `glob`/`fnmatch` 不展开 brace。selector 实现必须先把每条 glob 的 `{a,b}` 展开为独立模式，再用 `PurePath.full_match`（POSIX 风格）匹配；`**` 定义为匹配零或多层目录（`tests/integration/**` 同时命中顶层 `tests/integration/test_x.py` 与嵌套 `tests/integration/sub/test_y.py`）。`source_glob` 不得依赖具体匹配 API 的方言差异。

初始 mapping 至少覆盖粗分机、自动分拣入库、人工分拣、出库/满箱交换模块；并发模块由 Task 11 Step 4 补充。

- [ ] **Step 3：selector 单元测试**

`tests/scripts/test_select_heavy_tests.py` 覆盖：

- `--scope` / `--base <ref>` diff 解析 fixture（unstaged 单文件、staged 多文件、空 diff、`origin/develop...HEAD`）
- 直接修改 HEAVY 测试 fixture（改动 `tests/integration/test_foo.py` → 选中自身，无需 mapping）
- HEAVY 支撑资产 fixture（改动 `tests/integration/conftest.py` 或 `tests/support/**` → 选中对应回归集 mapping）
- docs-only / Jenkinsfile-only fixture（命中 ignore_globs → 退出 0 + 空输出，不 fail）
- 运行时入口/迁移配置 fixture（改动 `main.py`、`migrations/env.py` 或 `alembic.ini` → 命中全局 HEAVY 回归集 mapping）
- 未分类路径 fixture（任何未归类路径，如新增的顶层脚本 → 退出非 0，fail closed）
- TOML 命中 fixture（命中 `heavy_tests` 目标路径，输出去重）
- 显式 NONE fixture（候选范围命中 `heavy_tests=[]` → 退出 0 + 空输出）
- 输出路径校验 fixture（`heavy_tests` 含非 `tests/.../test_*.py` → fail closed）
- glob brace 展开 fixture（`tests/{unit,architecture}/**` 展开后命中 `tests/unit/test_x.py` 与 `tests/architecture/test_y.py`）
- `**` 零层目录 fixture（`tests/integration/**` 命中顶层 `tests/integration/test_a.py` 与嵌套 `tests/integration/sub/test_b.py`）
- TOML schema 歧义 fixture（`source_glob` 重叠冲突 → 校验 fail）

`tests/scripts/` 已通过 `norecursedirs` 排除出默认 `pytest`；本测试由 `pytest tests/scripts -q` 显式收集，并进入 `scripts/git-quality-gate.sh` 的 quality profile（**永久门禁**，不仅实施期有效）。

- [ ] **Step 4：写入长期维护规则并提交**

`AGENTS.md` 与 `tests/README.md` 记录 selector 与 TOML 的长期维护责任（这两个文件不随 Task 12 删除）：

- 新增可能影响运行时的生产模块、迁移或基础设施配置时，必须在 `heavy-test-impact.toml` 补 `[[mapping]]` 或显式 NONE，否则 selector fail closed。
- 修改或新增 HEAVY 测试路径时同步更新对应 `heavy_tests`。
- 本地验证：`uv run scripts/select_heavy_tests.py --scope unstaged`（应输出受影响 HEAVY 或空集；候选范围内未知路径退出非零）。

```bash
rtk gitnexus detect_changes --scope unstaged
rtk uv run python scripts/check_test_convergence_matrix.py
rtk git add scripts/select_heavy_tests.py tests/scripts/test_select_heavy_tests.py docs/architecture/heavy-test-impact.toml Jenkinsfile scripts/git-quality-gate.sh AGENTS.md tests/README.md
rtk git commit -m "test(ci): 落地 HEAVY selector、TOML 映射与长期维护规则"
```

Jenkinsfile 改动只新增 `HEAVY Selector Smoke` stage（仅 `pytest tests/scripts -q`），不启用 HEAVY Required。

### Task 10：关键可靠性崩溃窗口覆盖

原本是 follow-up 拖延；崩溃窗口是 SPEC §9.3 与 §15.6 的可靠性验收条件，必须并入本计划。本 Task 显式定义四类场景与 SPEC §9.3 兼容语义。

**Files:**

- Create: `tests/integration/test_inbound_evidence_ack_crash.py`
- Create: `tests/integration/test_device_command_persist_crash.py`
- Create: `tests/integration/test_callback_txn_concurrency.py`
- Create: `tests/integration/test_wms_idempotent_retry.py`
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

模拟 CALLBACK 已写持久化、本地事务尚未提交时进程崩溃。验证：

- 重启后**幂等重新处理持久化证据**即可（不引入事务补偿编排）
- 重复 CALLBACK 不会导致双重状态推进
- 与 `InboundEvidence` 幂等键互不干扰

落 `tests/integration/test_callback_txn_concurrency.py`。

- [ ] **Step 4：场景 4 — WMS 同步成功 + 本地物理落账失败**

按 SPEC §9.3 与“WMS 同步结果 + 本地事务”原子性：验证：

- 本地事务中先持久化物理事实和 `WmsConfirmation`（待确认状态），提交后才调用 WMS
- WMS 200 OK → 在同一事务内把 `WmsConfirmation` 更新为完成状态后提交 → 正常完成
- WMS 200 OK 但**完成状态更新事务失败**（进程崩溃或数据库故障）→ 重启后按幂等键重新确认；WMS 已记录该业务事实，重试幂等，最终把 `WmsConfirmation` 更新为完成，**不重复物理动作**
- WMS 失败（连接/超时/5xx）→ 同一幂等键重试；不事后补建 `WmsConfirmation`
- 持久重试到第 N 次后仍未成功 → 保持 `WmsConfirmation` 待确认并进入依赖暂停（**仅物理状态无法确认时才进入人工清线**）

落 `tests/integration/test_wms_idempotent_retry.py`。

- [ ] **Step 5：提交 Task 10**

```bash
rtk gitnexus detect_changes --scope unstaged
rtk uv run python scripts/check_test_convergence_matrix.py
rtk git add tests/integration/test_inbound_evidence_ack_crash.py tests/integration/test_device_command_persist_crash.py tests/integration/test_callback_txn_concurrency.py tests/integration/test_wms_idempotent_retry.py tests/resilience/test_human_clearline_after_restart.py
rtk git commit -m "test(reliability): 覆盖四类关键可靠性崩溃窗口（SPEC §9.3 兼容）"
```

### Task 11：多对象流水并发的独立并发风险覆盖

多对象并发是 SPEC §8.1 与 §15.3 核心验收条件，不应作为 follow-up 拖延；所有权属于测试矩阵和测试文件，不要求生产 handler 声明"测试责任"。本 Task 只覆盖**独立并发风险**（设备竞争、位置竞争、乱序 CALLBACK），不复制粗分机 13 类判定的逐个并发版本。

并发测试归入 `tests/integration/concurrency/`，不另设 `tests/concurrency/` 层级（`tests/concurrency/` 不在 `norecursedirs`，会落入默认 FAST）。`tests/integration/` 已在 `norecursedirs` 中，并发测试由 HEAVY selector 选跑。

**Files:**

- Create: `tests/integration/concurrency/__init__.py`
- Create: `tests/integration/concurrency/test_device_contention.py`
- Create: `tests/integration/concurrency/test_location_contention.py`
- Create: `tests/integration/concurrency/test_callback_out_of_order.py`
- Modify: `docs/architecture/heavy-test-impact.toml`（追加并发模块 `[[mapping]]`）

- [ ] **Step 1：场景 1 — 设备竞争**

同一设备的并发命令（第二次命令到达时第一次尚未 ACK）。验证：

- 第二条命令**保持在本地 WAITING 状态，不得下发到 ECS**（SPEC §5.2 明确“目标设备忙时由 WES 等待”，第二条被 ECS 拒收**不是**等价正确结果）
- 持久化 `DeviceCommand` 不重复
- 不存在自动 retry（SPEC §9.3）

落 `tests/integration/concurrency/test_device_contention.py`。

- [ ] **Step 2：场景 2 — 位置竞争**

两个对象并发 PUT 到同一目标格/队列。验证：

- 容量计算按目标位置真实可用容量（不会两个设备同时向同一目标料箱放料，SPEC §10.4）
- 落败方标记为「需要重选目标」，不静默改写到其他位置
- 目标设备忙闲自然串行化

落 `tests/integration/concurrency/test_location_contention.py`。

- [ ] **Step 3：场景 3 — 乱序 CALLBACK**

同一对象的多次 CALLBACK 抵达顺序与持久化顺序不一致。验证：

- 状态推进**按当前权威 `DeviceCommand`、幂等键和合法状态转移**处理（SPEC 未规定按厂商事件时间戳推进状态）
- 迟到 CALLBACK **不**主动回退已推进状态、**不**擅自推进新状态；仅作为证据持久化
- 重复 CALLBACK 通过幂等键去重
- 不存在"补偿"或"重放"

落 `tests/integration/concurrency/test_callback_out_of_order.py`。

- [ ] **Step 4：与 HEAVY selector 配合**

在 `docs/architecture/heavy-test-impact.toml`（Task 9a 创建）追加并发模块的 `[[mapping]]`：`source_glob` 覆盖粗分机/自动分拣/人工分拣的并发相关生产代码，`heavy_tests` 指向 `tests/integration/concurrency/` 下对应测试。

- [ ] **Step 5：提交 Task 11**

```bash
rtk gitnexus detect_changes --scope unstaged
rtk uv run python scripts/check_test_convergence_matrix.py
rtk git add tests/integration/concurrency/ docs/architecture/heavy-test-impact.toml
rtk git commit -m "test(concurrency): 覆盖独立并发风险并补 HEAVY 映射"
```

### Task 9b：Jenkinsfile HEAVY Required stage 落地（Task 10/11 之后执行）

本 Task 启用 HEAVY Required stage：在 Task 10（崩溃窗口）与 Task 11（并发）的实际测试文件已落地后，引用它们作为 minimum smoke。

**Files:**

- Modify: `Jenkinsfile`（启用 HEAVY Required stage + env harness）

- [ ] **Step 1：HEAVY 必跑集合**

`tests/integration/` 44 个测试文件（Python 共 20,565 行，含 `conftest.py`）+ `tests/resilience/` 6 文件 = **禁止**每次 MR 全量跑通。每个 MR 的 HEAVY Required stage 只跑 minimum smoke + selector 选出的受影响子集；resilience/E2E 不再默认全跑，改为受影响选跑 + nightly 全跑（与 Step 2 合同、Step 3 stage 表单一语义）：

- **每个 MR 必跑**（固定，不依赖 selector 结果）：minimum smoke 三个文件，覆盖环境 harness 是否就绪——`tests/integration/test_inbound_evidence_ack_crash.py`、`tests/integration/test_wms_idempotent_retry.py`（Task 10）、`tests/integration/concurrency/test_device_contention.py`（Task 11）。
- **selector 选跑**（`--base origin/${CI_TARGET_BRANCH}`）：受影响 HEAVY 子集，含直接修改的 HEAVY 测试自身与候选范围命中的 mapping。
- **受影响时选跑**：`tests/resilience/`、`tests/e2e/`、`tests/load/`、`tests/mock/` 中被 selector 选中或被直接修改的测试。
- **nightly 全跑**（`HEAVY Full` stage）：`tests/integration`、`tests/e2e`、`tests/resilience`、`tests/load`、`tests/mock` 全集。

selector 返回空集时，stage 只跑三个 minimum smoke，不 fail（语义与 Step 2 合同一致）。

- [ ] **Step 2：环境与执行合同**

HEAVY Required stage 的执行约束（具体 Groovy 在实施时按 Jenkins 模板落地并经 dry-run 验证；本计划只锁定合同，不嵌入可复制脚本）：

**触发与基线**：stage 仅在 Merge Request 构建触发（`when` 限定 MR）；非 MR 的 develop/main 分支构建不跑该 stage，避免 `CI_TARGET_BRANCH` 为空时展开成无效的 `--base origin/` 阻断部署流水线。若未来需在非 MR 构建跑，必须为基线定义确定性回退（如 `origin/develop`），禁止空值。

**执行位置**：alembic 与 pytest 在 Jenkins agent（已装 `uv`、检出仓库代码）上执行；postgres/redis 仅作容器化数据库服务，**不在 DB 容器内跑 alembic/pytest**（Alpine 无仓库代码、无 `uv`、通常无 `bash`）。

**环境变量合同**：`ALEMBIC_DATABASE_URL` 显式指向测试库（`migrations/env.py` 只读此变量，**不读 `INTEGRATION_DATABASE_URL`**；缺失回退 `settings.DATABASE_URL` 迁移错误库）；`INTEGRATION_DATABASE_URL` / `INTEGRATION_REDIS_URL` 供 pytest 集成 fixture 用，指向同一组容器化测试服务；若 HEAVY 测试启动应用或 Celery 进程，同步设置实际的 `POSTGRES_*`、`REDIS_*` 等应用配置，不能只设 integration fixture 变量；端口映射、容器名、凭据由实施时按 agent 环境确定，容器化服务必须在 stage `post { always }` 中无条件清理。

**测试选择与传参**：selector 用 `--base origin/${CI_TARGET_BRANCH}`，输出到文件（如 `selected-heavy.txt`），pytest 通过该文件读取路径（POSIX sh 安全），不依赖 shell 数组或未加引号的变量展开；selector 输出的每个路径必须校验为仓库内 `tests/.../test_*.py`，拒绝非测试路径与空白注入；minimum smoke（Task 10/11 的三个文件）始终显式追加，即使 selector 选出空集也跑，空集不 fail。

**health check 与失败条件**：postgres/redis 就绪检查用带退避的重试循环（非单次 `pg_isready -t` 超时），超过上限则 stage 失败；fail closed：env 任一缺失、selector 退出非零、服务未就绪、`alembic upgrade head` 失败、pytest 任一用例失败 → stage 退出非零，PR 阻塞。selector 退出 0 + 空集不 fail（只跑 minimum smoke）。

- [ ] **Step 3：Jenkinsfile 完整 stage 表**

| Jenkins stage | 触发 | 必跑 |
|---|---|---|
| `Architecture Guardrails` | 每次 PR | Ruff/Bandit/Import Linter + 一次 `pytest tests/architecture -q` |
| `Unit Tests (FAST)` | 每次 PR | `pytest -q --junitxml=reports/fast-tests.xml` + 预算检查 |
| `HEAVY Selector Smoke` | 每次 PR | `pytest tests/scripts -q`（Task 9a 落地后即生效） |
| `HEAVY Required` | 每次 MR | selector(`--base origin/${CI_TARGET_BRANCH}`) 选受影响 HEAVY + minimum smoke；环境合同与失败条件见 Task 9b Step 2；env 缺失或 selector 退出非零则 fail closed（空集只跑 minimum smoke） |
| `HEAVY Full` | nightly | 全量 `pytest -q tests/integration tests/e2e tests/resilience tests/load tests/mock`（显式列出，不依赖 shell brace expansion） |
| `HEAVY DB-Baseline` | 手动 | Task 7 Step 3 重建后跑全量重测 |

- [ ] **Step 4：提交 Task 9b**

```bash
rtk gitnexus detect_changes --scope unstaged
rtk uv run python scripts/check_test_convergence_matrix.py
rtk git add Jenkinsfile
rtk git commit -m "ci(test): 启用 HEAVY Required stage 与 env harness（Task 9b）"
```

### Task 12：关闭并移除实施期矩阵

**Files:**

- Delete: `docs/architecture/wes-test-convergence-matrix.csv`
- Delete: `scripts/check_test_convergence_matrix.py`
- Delete: `tests/architecture/test_convergence_matrix_guardrail.py`
- Modify: `tests/support/test_suite_topology.py`
- Modify: `tests/architecture/test_suite_topology_guardrail.py`（Task 2 已重命名）
- Modify: `.pre-commit-config.yaml`（移除矩阵 hook）

- [ ] **Step 1：核对矩阵状态并收集 deletion_gate 清单**

运行 `check_test_convergence_matrix.py --final`：校验矩阵中**所有行**（含 `DELETE`）都已 `CLOSED`，任何 `OPEN` 行都退出非零。`CLOSED` 非 `DELETE` 行 `target_path` 必须存在且由最终测试层收集；`CLOSED` 的 `DELETE` 行 `target_path` 为空且 `test_path` 已实际从仓库删除。矩阵中不得存在仍指向旧路径的 `REWRITE`、`SPLIT`、`REPLACE_GATE` 行。

收集所有 `CLOSED` 行的 `deletion_gate` 字段，**去重**为一份清单。该清单中每个测试/门禁必须落在 FAST、architecture 或 HEAVY 范围内，由 Step 2/3 的**一次统一执行**覆盖；**不逐项重跑**，避免与 quality/HEAVY 重复执行。

`--final` 在矩阵退役前最后执行；矩阵退役是整个计划的最后一个 Task，确保 Task 9a/9b、10、11 新增的 Jenkins 配置与测试文件都已被矩阵登记与关闭。

- [ ] **Step 2：一次最终质量执行（FAST + architecture 的唯一执行）**

运行：

```bash
rtk ./scripts/git-quality-gate.sh --profile quality
rtk git diff --check
```

`quality` profile 顺序执行 Ruff format/lint、Bandit、Import Linter、旧架构缺席扫描、一次 `pytest tests/architecture -q`、一次默认 `pytest -q --junitxml=reports/fast-tests.xml` 与快速预算检查（见 Task 8 Step 2）。这是 FAST 与 architecture 的**唯一**执行；确认 Step 1 的 deletion_gate 清单中属于 FAST/architecture 的项已在此覆盖。

- [ ] **Step 3：一次 HEAVY 执行（基线重建后）**

按各工作包记录的环境和路径，在 Task 7 Step 3 最终 Alembic 基线重建后，**一次**跑完受影响的 integration/e2e/resilience/load/mock；不得用默认 pytest 结果代替。确认 Step 1 的 deletion_gate 清单中属于 HEAVY 的项已在此覆盖。

- [ ] **Step 4：删除临时控制面（含 pre-commit 同步移除）**

Step 1–3 全绿后，删除矩阵、矩阵检查脚本和矩阵专用架构测试（`docs/architecture/wes-test-convergence-matrix.csv`、`scripts/check_test_convergence_matrix.py`、`tests/architecture/test_convergence_matrix_guardrail.py`）。**`work_package` 临时列随矩阵一起删除**。

**同步移除 pre-commit hook**：

- 从 `.pre-commit-config.yaml` 删除 `check_test_convergence_matrix.py` 的 hook 段
- 移除 hook 之前确认替代：topology guardrail、`check_business_legacy_absence_gate.py`、GitNexus `detect_changes` 已能在 commit 阶段捕获新 test_*.py 绕过情形
- 移除后本地跑一次 `pre-commit run --all-files` 验证不再调用已删除脚本

把仍有长期价值的目录与速度边界保留在 topology、预算脚本和最终缺席门禁中。

- [ ] **Step 5：提交矩阵退役**

```bash
rtk gitnexus detect_changes --scope unstaged
rtk git add docs/architecture/wes-test-convergence-matrix.csv scripts/check_test_convergence_matrix.py tests/architecture/test_convergence_matrix_guardrail.py tests/support/test_suite_topology.py tests/architecture/test_suite_topology_guardrail.py .pre-commit-config.yaml
rtk git commit -m "docs(test): 完成测试收敛并退役实施矩阵"
```

提交前按仓库规则运行 GitNexus `detect_changes` 确认变更范围，确认没有旧执行路径或无关符号被保留（修改符号前的 `impact` 已在各 Task 实施时按决策 22 完成）。

## 8. 完成定义

全部条件同时满足才算测试收敛完成：

- 每个最终业务合同和可靠性不变量都有且只有一个主要测试所有者。
- 默认 `pytest` 只包含 `FAST`，满足 60 秒、单例 1 秒以及 `tests/unit/`、`tests/workline_plugins/` p95 100 毫秒预算（**p95 仅 N≥30 生效，N<30 跳过且不报 warn**）。
- `tests/architecture/` 只由质量门禁显式运行一次；矩阵与拓扑门禁在 Task 1 完成早于 Task 2 把 `tests/architecture` 调出默认收集。
- 真实数据库、HTTP、Celery、并发、等待、性能和多组件场景只存在于 `HEAVY`；`tests/mock/` 允许 fake port + 虚拟时钟降级为 FAST。
- 测试中不存在只验证旧 Runtime、Manifest、System Capability、Intent/Effect、RuntimeHold、Reconciliation、CellReservation、兼容路径或旧迁移链的代码。
- 不存在旧测试路径 alias、fixture compatibility、阶段性 allowlist 或双 gate；旧 Runtime 脚本与对应 guardrail 测试**已实际 `git rm`**（不只从 `git-quality-gate.sh` 删硬编码引用）。
- `tests/README.md`、`AGENTS.md`、Pytest 配置、质量门禁和 Jenkins 对测试层的定义一致（不强制两文档做词级镜像检查）。
- 实施期矩阵在 Task 12（计划最后一个 Task）关闭并删除（`check_test_convergence_matrix.py --final` 在 Task 12 Step 1 通过，覆盖包括 `DELETE` 在内的所有行），永久 topology、预算和旧架构缺席门禁通过。Task 12 之前的 Task 9a/9b、10、11 新增的 Jenkins 配置与测试文件都已被矩阵登记与关闭。
- 默认快速回归、架构测试、**基线重建后受影响重测试**、Ruff、Bandit、Import Linter 和仓库质量门禁全部通过。
- pre-commit hook 强制 `check_test_convergence_matrix.py` 在 commit 前通过（矩阵存在期间 Task 1 至 Task 9b 有效，Task 12 Step 4 同步移除）；新增 test_*.py 必须有对应矩阵 OPEN 行。
- 每个 commit 提交前必跑 GitNexus `detect_changes --scope unstaged`。
- HEAVY 必跑集合由 Jenkins HEAVY Required stage 在每次 PR 通过 selector 选跑 + minimum smoke 强制执行。
- 多对象并发的三个独立风险（设备竞争、位置竞争、乱序 CALLBACK）由 Task 11 显式测试覆盖；不复制粗分机 13 类判定的逐个并发版本。
- 关键可靠性四类崩溃窗口（Evidence→ACK、Command persist→发送、CALLBACK 幂等重新处理、WMS 幂等键重试）由 Task 10 显式测试覆盖。
- SPEC 必须先合并为非 `Review Requested` 状态。

## 9. 风险控制

| 风险 | 控制 |
| --- | --- |
| 误删目标可靠性断言 | 先建立最终对象测试并通过，再关闭矩阵行和删除旧测试 |
| 把旧架构测试仅移动到重测试 | 语义动作与执行层分别校验，旧语义最终必须删除 |
| 默认套件继续膨胀 | 60 秒、单例 1 秒、纯单元 p95 100 毫秒自动预算（N<30 跳过）；CI 容器固定 2 vCPU / 4 GB |
| 高层测试重复领域排列 | 一个行为一个所有者，高层只验证新增边界风险 |
| 架构扫描散落并重复运行 | `QUALITY` 一次显式收集，单一规则实现 |
| 速度优化削弱失败路径 | 去重按所有权，不按成功/失败类别；业务拒绝、冲突和异常结果必须保留 |
| 临时矩阵变成长期迁移负担 | 全部行（含 `DELETE`）关闭后删除（`--final` 旗标仅 Task 12 Step 1），永久规则迁入 topology、预算和缺席门禁 |
| CI 与本地命令漂移 | `scripts/git-quality-gate.sh` 为本地编排真源，Jenkins 使用相同测试层和脚本接口 |
| CSV quoting/换行污染 | 强制 QUOTE_ALL + LF，脚本与 fixture 双重门禁 |
| 新增 test_*.py 绕过矩阵 | pre-commit hook 强制预登记 |
| 60s 预算与现有 import/fixture 不匹配 | Task 2 Step 5 预算可达性预检 |
| p95 在小样本集下报假阳性 | N<30 跳过且不报 warn |
| `tests/integration/conftest.py` 依赖旧 Runtime 未被发现 | 矩阵扩展覆盖 conftest.py 等非 test_*.py 资产 |
| Task 2 调出 architecture 后中间提交盲区 | Task 1 矩阵与拓扑门禁先于 Task 2 完成；Task 1 至 Task 9b（含 9a、10、11）的中间提交每次都跑 `check_test_convergence_matrix.py` 默认完整性检查；`--final` 仅 Task 12 Step 1 |
| AGENTS.md 与 `git commit` 流程脱节 | 修改符号前跑 `impact`、每个 commit 前跑 `detect_changes`（决策 22） |
| Task 3-6 integration 验证旧迁移而非最终 schema | 集成重跑在最终 Alembic 基线重建后强制（Task 12 Step 3） |
| 旧 Runtime 脚本文件未实际删除 | 显式 `git rm` 5 个 check_runtime_*_gate.py、test_api_signature.sh、test_live_suite.sh 与对应 guardrail 测试 |
| `pyproject.toml` addopts 默认噪声 | 删 `--cov-report=term-missing`（默认无 `--cov`） |
| `evidence_note` 沦为 checklist | 长度 ≥ 30 字符 + REWRITE/SPLIT 关键标识验证 |
| 矩阵 guardrail 仅靠 exit code 不可维护 | 每条规则独立正反例 fixture 测试 |
| mock 强制 HEAVY 阻碍快速反馈 | mock 可基于 fake port + 虚拟时钟降级为 FAST |
| 旧架构缺席扫描伪命中 | token 化分词 + 当前态文档与 SPEC 显式 allowlist |
| `test_test_*` 双前缀文件名 | 重命名为 `test_suite_topology_guardrail.py` |
| HEAVY 重测无强制门禁 | Task 9b 落地：Jenkins HEAVY Required stage 每次 PR 必跑 + 受 PR 改动选跑 |
| 关键可靠性崩溃窗口未覆盖 | Task 10 落地：4 类崩溃窗口测试 + SPEC §9.3 兼容语义 |
| 多对象流水并发缺明确所有者 | Task 11 显式覆盖：设备竞争、位置竞争、乱序 CALLBACK |
| SPEC 仍为 `Review Requested` 状态 | §1 加固，Task 1 启动前必须先合并 SPEC |
