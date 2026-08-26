# Phase 11 数据库 Schema 与迁移基线重置实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在最终模型稳定且旧生产路径归零后，将 Task 1 实测冻结的全部未发布历史 Alembic revision 收敛为一个可从空 PostgreSQL/TimescaleDB 建立系统的初始基线。

**Architecture:** 先以测试锁定最终 metadata 和 PostgreSQL 专有对象，再在隔离空库上由 Alembic generator 生成随机 revision，人工补齐 autogenerate 无法表达的 schema、扩展、函数、触发器、部分索引和 TimescaleDB 对象，最后删除旧 revision 及只验证旧 revision 的测试。整个切换不转换旧数据、不提供 downgrade，也不保留旧 migration 桥接。

**Tech Stack:** Python 3.13、SQLModel/SQLAlchemy、Alembic、PostgreSQL、TimescaleDB、Docker Compose、Pytest、GitNexus、HEAVY selector。

## Global Constraints

- 本计划属于 Phase 11；Phase 10 零旧生产路径退出门禁未通过前，任何人不得删除现有 revision chain。
- 当前只冻结 Phase 11 的入口门禁与实施方法；最终 metadata、Phase 9 已实现的最小执行内核、当前 `rough_sorter` 模型、PostgreSQL/TimescaleDB 专有对象清单、安全数据库 wrapper 和待归档文档清单必须在 Phase 10 完成后重新生成并通过独立实施前评审，Task 1 未通过前不得进入 Task 2。
- 退役插件活动残留收敛必须完成，活动模型和当前 head 不得再包含退役插件字段；Phase 11 启动时以项目外归档
  `../archive_docs/wes_backend/docs/superpowers/plans/2026-08-15-wes-retired-plugin-residual-convergence.md` 为完成证据，不得要求已归档的过程计划继续留在项目内。
- Phase 9 已实现的最小执行内核、当前 WMS/RCS Adapter、设备统一接口及 `rough_sorter` 所需持久模型必须稳定；不得为 Phase 12/13 插件预建表、字段或 operation，否则停止，不生成临时基线。
- 不迁移旧数据，不保留兼容 schema、回填脚本、桥接表或 downgrade；开发/测试数据库统一清理后重建。
- 新初始 revision 必须由 Alembic generator 生成随机 revision ID，`down_revision = None`，不得手写 revision ID。
- 只在名称明确的隔离数据库中执行创建/删除；禁止对默认库、生产库或无法确认的连接执行 destructive SQL。
- 旧 revision 测试只有在最终基线 successor 先通过后才能删除；删除说明必须标注 successor 或 `NONE`。
- `docs/hardware/` 不参与数据库基线清理。

---

### Task 1: 验证 Phase 11 入口门禁并冻结对象清单

**Files:**

- Inspect: `migrations/versions/*.py`
- Inspect: `migrations/env.py`
- Inspect: `src/**/models/*.py` 及所有注册到 `SQLModel.metadata` 的模型
- Inspect: `tests/{database,migrations,integration,deployment}/`
- Inspect: `scripts/`、`tests/support/runtime_inbox_postgresql.py` 中已有的隔离数据库安全原语
- Inspect: `docs/superpowers/plans/*.md` 和其它仍引用旧 migration 过程的当前文档

**Interfaces:**

- Consumes: Phase 10 零旧路径证据、最终 SQLModel metadata、当前 PostgreSQL head。
- Produces: 可审计的 revision 清单，以及独立评审确认的 schema-qualified 完整数据库 manifest；该 manifest 不从 `migrations/env.py` 或当前 `SQLModel.metadata` 反向生成。

- [ ] **Step 1: 检查入口条件**

  Run: `rg -n "RuntimeIntent|RuntimeHold|Manifest|Capability|Effect|PLUGIN_|plugin_key|plugin_contract_version" src --glob '*.py'`

  Expected: 每个命中都能逐条关联 Phase 10 已评审的最终 owner 或 `NONE` 处置，未分类命中数为 `0`；不得用人工浏览后声称“看起来只有允许对象”。出现无 owner 旧路径立即停止 Phase 11。

- [ ] **Step 2: 冻结 Git 与 Alembic 基线**

  Run: `git rev-parse HEAD && git status --short && uv run alembic heads && rg --files migrations/versions -g '*.py' | sort`

  Expected: 单一 head；记录精确提交和 revision 文件清单。revision 数量和路径只以 Task 1 当前命令实测冻结清单为准，不沿用规划编写时的快照数字。

- [ ] **Step 3: 枚举 autogenerate 不会完整表达的对象**

  Run: `rg -n "op\.execute|CREATE (SCHEMA|EXTENSION|FUNCTION|TRIGGER|VIEW|INDEX)|create_hypertable|timescaledb" migrations/versions migrations/env.py`

  Expected: 每个命中都被判定为最终保留或 `NONE`；不得把历史 SQL 原样复制进新基线。把最终保留对象固化为 Task 2 的 `EXPECTED_SCHEMA_MANIFEST` 输入，至少包含：

  - 每张表的 schema/name，以及每列的规范化 PostgreSQL type、nullable、server default；
  - 全部最终 PK、FK、UNIQUE、CHECK、EXCLUDE 约束的 schema/table/name/type 和规范化定义；
  - 全部索引的 schema/table/name、unique、access method、列或表达式、include 列和 predicate；
  - extension、function、trigger、view、TimescaleDB 对象等专有对象的稳定 identity 与规范化定义。

  `NULL` default 与不存在 default 必须按 PostgreSQL catalog 语义明确区分；不得只冻结对象名称或依赖 metadata 补齐定义。

- [ ] **Step 4: GitNexus 检查迁移环境与 metadata 注册影响**

  对 `migrations/env.py` 实际导入的 metadata 注册模块及 `tests/support/sqlmodel_metadata.py` 中的注册 helper，使用 GitNexus 的 `file_path` 参数运行 context/impact，避免同名模型或 helper 误命中。

  Expected: 明确所有模型注册入口；HIGH/CRITICAL 先向用户报告并取得确认。

- [ ] **Step 5: 对冻结清单执行实施前复审**

  使用 `superpowers:requesting-code-review` 只读评审 Phase 10 退出证据、`EXPECTED_SCHEMA_MANIFEST`、revision 清单和专有对象处置；通过 `superpowers:receiving-code-review` 核实意见并修订本计划。

  Expected: 无可操作意见后才能进入 Task 2；复审同时冻结安全数据库 wrapper、当时全部 revision 的精确删除分类、待归档文档和待删除 revision 的精确路径，并把最终数量与路径清单写回本计划后再次评审。若没有待归档的其它迁移过程文档，清单必须显式记录 `NONE`。若当时没有满足“loopback + 精确库名 + 子进程 URL 透传 + 异常清理 + DROP 后复查”的现成 wrapper，必须先在本计划 Task 2 前插入独立 TDD 任务并再次评审，不得在 Task 3 临场拼接 destructive shell。本文件当前内容不得替代这次实施时复审。

### Task 2: 先建立结构红灯与最终 Schema 绿灯 successor

**Files:**

- Create: `tests/integration/test_initial_schema_baseline_postgresql.py`
- Create: `tests/architecture/test_migration_baseline_structure.py`
- Modify only if its existing runtime-status assertions require adaptation: `tests/architecture/test_runtime_status_owner_guardrail.py`
- Preserve: `tests/support/sqlmodel_metadata.py`（只服务当前 8-model SQLite fixture，不作为最终 schema oracle）
- Modify in Task 3 after successor turns green: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: 冻结的最终 metadata 和 PostgreSQL 专有对象清单。
- Produces: 单 revision、空库 upgrade、metadata/schema 一致性和专有对象存在性的权威 HEAVY 验收。

- [ ] **Step 1: 写失败的单基线结构测试**

  在新的 `test_migration_baseline_structure.py` 中断言 `migrations/versions/` 只有一个 Python revision、`down_revision is None`、Alembic 只有一个 head；当前 revision chain 尚未收敛时应失败。`test_runtime_status_owner_guardrail.py` 继续只拥有 runtime-status 读写 owner，不承接 revision 数量。

- [ ] **Step 2: 写 PostgreSQL 空库 characterization successor**

  使用现有 `temporary_database()` harness，在隔离数据库运行 `alembic upgrade head`。`test_initial_schema_baseline_postgresql.py` 内固化独立评审通过的 `EXPECTED_SCHEMA_MANIFEST`，逐项验证：

  - `wes_sys`、`wes_biz`、`wes_runtime` 等最终 schema；
  - 全部最终表及逐列 type/nullability/server default；
  - 全部最终 PK、FK、UNIQUE、CHECK、EXCLUDE 约束及规范化定义；
  - 全部最终索引的 unique/access method/列或表达式/include/predicate 定义；
  - 冻结清单中明确保留的 PostgreSQL/TimescaleDB 专有对象；
  - `alembic_version` 位于 `wes_sys`；
  - 退役插件表、字段、索引和约束不存在。

  显式 manifest 必须与当前旧 revision chain 升级后的空库实际结构完全相等，并在 Task 4 对新初始基线执行同一套完整等值验收；Task 4 再以 `alembic check` 证明新基线实际结构与 `migrations/env.py` 的 target metadata 一致。这样即使 env.py 漏导入模型、数据库约束、列默认值或索引细节，也会先由独立 manifest 对实际结构的差异检出，不得用同一 metadata 同时充当生成源和唯一验收 oracle。该测试是删除旧 chain 前的 successor characterization，当前应先通过，不得重复断言 revision 数量或 `down_revision`。

- [ ] **Step 3: 分别验证结构红灯和 Schema successor 绿灯**

  Run: `uv run pytest tests/architecture/test_migration_baseline_structure.py tests/architecture/test_runtime_status_owner_guardrail.py -q`

  Run with isolated PostgreSQL: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: `test_migration_baseline_structure.py` 只因当前存在多条 revision 而 FAIL；`test_runtime_status_owner_guardrail.py` PASS。PostgreSQL successor 必须实际执行且 PASS、无跳过，证明旧 chain 已能建立冻结的最终 schema；若 HEAVY 失败，停止 Phase 11，不得删除旧 revision。

- [ ] **Step 4: 冻结 HEAVY mapping 处置清单**

  对 Task 1 冻结的全部 revision 逐个建立精确 deletion classification，并分别记录已有 mapping 与未配置数量。不得只枚举已有 mapping owner，也不得新增 `migrations/versions/**` 宽泛 mapping；它会与精确规则形成不同策略重叠。新基线 revision 与所有待删除 revision 的精确 tombstone mapping 在 Task 3 原子加入当前配置，统一指向 `test_initial_schema_baseline_postgresql.py`，以覆盖 staged 和 CI base diff；含删除的提交合入 `develop` 前不得移除这些 tombstone。

### Task 3: 在隔离空库生成唯一初始 revision

**Files:**

- Create: `migrations/versions/<generated>_create_initial_wes_schema.py`
- Delete after generation: 旧 `migrations/versions/*.py`
- Review and modify: `migrations/env.py`
- Modify: `tests/scripts/test_select_heavy_tests.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Execute through: Task 1 冻结并通过自动化测试的精确安全数据库 wrapper；不得直接拼接临时 shell

**Interfaces:**

- Consumes: Task 1 对象清单和 Task 2 红灯测试。
- Produces: `down_revision = None` 的单一初始 migration。

- [ ] **Step 1: 创建并验证精确隔离数据库**

  使用 Task 1 冻结的 wrapper 在本地 HEAVY Compose PostgreSQL 上创建固定用途数据库 `wes_phase11_baseline_generation`。wrapper 必须在不打印密码的前提下输出 host、port、database，确认 host 为 loopback 且 database 名完全匹配，并把只指向该库的 `ALEMBIC_DATABASE_URL` 传给后续每个 Alembic 子进程；不复用现有开发库。整个生成流程由 wrapper 的 trap/finally 清理 guard 包裹，任何中间异常都进入 Step 6 的精确清理。

- [ ] **Step 2: 只 stamp 当前 head，不建立旧 schema**

  对该空数据库运行 `uv run alembic stamp head`，使 Alembic 允许从最终 metadata autogenerate。`migrations/env.py` 会预建最终空 schema，因此验收应为：只允许配置声明的空 schema 与 `wes_sys.alembic_version`，不得存在任何业务表、业务索引、函数、触发器或 TimescaleDB 对象。

- [ ] **Step 3: 使用 generator 生成 revision**

  Run: `uv run alembic revision --autogenerate -m "建立最终初始数据库基线"`

  Expected: Alembic 生成随机 revision ID；不得手工创建 migration 文件。

- [ ] **Step 4: 将生成 revision 转为初始基线**

  将 `down_revision` 改为 `None`，检查 upgrade 先建立所需 schema，再创建表和约束。按照 Task 1 冻结清单补入 autogenerate 无法表达但最终仍需要的 PostgreSQL/TimescaleDB 对象；`downgrade()` 明确抛出 `NotImplementedError`。同时审查 `migrations/env.py` 的 `transaction_per_migration` 与 autocommit 配置：若新基线仍含并发索引/autocommit block，则保留并改为不绑定历史 revision 名称的当前事实注释；若不再需要，则最小化配置并删除“Revision C”等失效叙述。

- [ ] **Step 5: 删除旧 revision 文件**

  仅删除 Task 1 已冻结清单中的旧 tracked revision；不得使用仓库级 `git clean` 或通配递归删除。Git 历史本身保留追溯能力，不在项目内复制旧 migration。为新生成 revision 增加只指向 `test_initial_schema_baseline_postgresql.py` 的精确 mapping；对每个被删旧 revision 保留同一 successor 的精确 tombstone mapping，保证 staged 与 `origin/develop` base diff 都能分类。不得在含删除的同一分支中直接删除旧 mapping，也不得用宽 glob 代替 Task 1 冻结的逐文件精确分类。

  在 `tests/scripts/test_select_heavy_tests.py` 先增加基于 Task 1 冻结删除清单的合同：把全部旧 revision 路径作为 changed files 输入当前配置时，不得 fail closed，且选择结果包含最终基线 PostgreSQL successor；任意漏配路径仍应失败。先观察测试失败，再为冻结清单中的每条旧 revision 加入精确 tombstone，并加入新 revision mapping 使其通过。

- [ ] **Step 6: 清理基线生成数据库**

  无论 autogenerate 成功或失败，wrapper 都必须终止该数据库的剩余连接并删除名称完全匹配的 `wes_phase11_baseline_generation`；再次查询系统目录证明数据库不存在。不得把该固定生成库留给 Task 4 或后续开发复用；wrapper 的拒绝非 loopback、拒绝错误库名、子进程失败仍清理和成功清理路径必须已有自动化测试。

- [ ] **Step 7: 运行绿灯结构测试**

  Run: `uv run alembic heads`

  Run: `uv run pytest tests/architecture/test_migration_baseline_structure.py tests/architecture/test_runtime_status_owner_guardrail.py tests/scripts -q`

  Expected: 唯一 head；revision 目录结构测试 PASS。

### Task 4: 用空库验证新基线与 metadata 完全一致

**Files:**

- Modify if failures expose real gaps: `migrations/versions/<generated>_create_initial_wes_schema.py`
- Modify if final metadata registration is incomplete: `migrations/env.py`
- Preserve: `tests/support/sqlmodel_metadata.py`（不得扩张为最终 schema oracle）

**Interfaces:**

- Consumes: 唯一初始 revision。
- Produces: 可重复建立且无 metadata 漂移的最终数据库。

- [ ] **Step 1: 从全新临时库执行 upgrade**

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: selector 实际运行 `test_initial_schema_baseline_postgresql.py`，JUnit 中 `total > 0` 且 `skipped = 0`。

- [ ] **Step 2: 验证 Alembic 无后续差异**

  `test_initial_schema_baseline_postgresql.py` 必须在同一个临时数据库完成 upgrade 后，用当前 Python 解释器执行 `python -m alembic check`；测试通过环境变量把同一个临时数据库 URL 传给子进程，禁止嵌套调用 `uv run` 或切换数据库。

  Expected: 子进程输出 `No new upgrade operations detected.`。

- [ ] **Step 3: 验证重复建库**

  删除第一次临时数据库后重新创建第二个随机临时数据库，再次执行 `alembic upgrade head` 和完整基线测试。

  Expected: 第二次结果一致；不存在依赖第一次运行残留的 schema、extension 或全局状态。

### Task 5: 迁移 revision 专属测试到最终 schema owner

**Files:**

- Review and possibly delete: `tests/database/test_*_migration.py`
- Review and possibly delete: `tests/migrations/test_*.py`
- Review and possibly delete: `tests/deployment/test_retire_workline_inbox_migration.py`
- Review and possibly delete: `tests/integration/test_workline_plugin_schema_retirement.py`
- Review and update: `tests/scripts/test_select_heavy_tests.py`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: 已通过的 `test_initial_schema_baseline_postgresql.py`。
- Produces: 只验证最终 schema/行为的测试树，不再读取已删除的 revision 文件名或正文。

- [ ] **Step 1: 逐个建立 successor/`NONE` 清单**

  Run: `rg -n "migrations/versions|MIGRATION =|glob\(.*migration|read_text\(" tests/{database,migrations,integration,deployment,scripts} --glob '*.py'`

  对每个命中明确：最终 schema successor、仍有独立行为价值的测试 owner，或 `NONE`。不得按文件名关键词批量删除。

- [ ] **Step 2: 先运行 successor**

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: PASS 且无跳过。未通过前不得删除旧测试。

- [ ] **Step 3: 删除旧 revision 专属断言并更新 selector**

  删除只读取旧 migration 文件名、SQL 文本、upgrade/downgrade 或回填过程的测试；保留最终模型行为、可靠性和数据库约束测试。HEAVY mapping 统一指向最终基线测试和仍有效的领域 PostgreSQL 测试。

- [ ] **Step 4: 运行测试拓扑与 selector 合同**

  Run: `uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py tests/scripts -q`

  Expected: PASS。

- [ ] **Step 5: 冻结数据库基线原子提交清单**

  根据 Task 1 的 revision 删除清单和本任务的 successor/`NONE` 清单，冻结待提交的精确路径；此处不得提交。删除项必须通过 `git add -u -- migrations/versions` 暂存，新增/修改项必须逐个列出完整路径，禁止 `git add migrations tests` 等目录级暂存。

  Expected: 清单包含生成的新 revision、所有旧 revision 删除、实际修改的 `migrations/env.py`、两个新测试 owner、实际保留/删除的旧迁移测试、`tests/scripts/test_select_heavy_tests.py` 和 HEAVY TOML；Commit/PR 说明草稿逐项列出被删除测试的 successor 或 `NONE`。

### Task 6: 最终质量门禁、独立评审与文档生命周期收尾

**Files:**

- Modify: `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- Modify: `docs/superpowers/README.md`
- Verify archived prerequisite: `../archive_docs/wes_backend/docs/superpowers/plans/2026-08-15-wes-retired-plugin-residual-convergence.md`
- Archive externally when completed: `docs/superpowers/plans/2026-08-15-wes-schema-and-migration-baseline-reset.md` → `../archive_docs/wes_backend/docs/superpowers/plans/2026-08-15-wes-schema-and-migration-baseline-reset.md`
- Archive externally when completed, only if Task 1 freezes exact paths: 被本计划取代的其它迁移过程文档逐项源路径 → 逐项目标路径；当前尚未冻结，Task 2 前必须回写精确清单或 `NONE`

**Interfaces:**

- Consumes: Tasks 1–5 的完整 diff。
- Produces: Phase 11 退出证据和 Phase 12 教学式插件开发使用的唯一空库基线。

- [ ] **Step 1: 精确暂存完整数据库基线差异**

  先确认工作区不存在 Task 1 冻结范围外的并发变更。对旧 revision 删除执行 `git add -u -- migrations/versions`；随后只按 Task 5 冻结清单逐个暂存生成的新 revision、实际修改的 `migrations/env.py`、`tests/architecture/test_migration_baseline_structure.py`、`tests/integration/test_initial_schema_baseline_postgresql.py`、实际保留或修改的迁移测试、`tests/scripts/test_select_heavy_tests.py` 和 `docs/architecture/heavy-test-impact.toml`。禁止暂存目录、glob、尚未核对的测试或外部文档。

  Run: `git diff --cached --name-status`

  Expected: 与 Task 5 的精确原子提交清单完全相等；否则取消错误路径的暂存并停止。

- [ ] **Step 2: 运行默认与质量门禁**

  Run: `uv run pytest --collect-only -q -o addopts='' | tail -5`

  Run: `./scripts/git-quality-gate.sh --profile quality`

  Expected: 收集成功；质量门禁退出码 `0`。

- [ ] **Step 3: 运行暂存 HEAVY**

  Run: `uv run scripts/select_heavy_tests.py --scope staged`

  Run: `./scripts/run_selected_heavy_local.sh --scope staged`

  Expected: 新基线、受影响领域 PostgreSQL 测试全部实际执行且无跳过。

- [ ] **Step 4: 验证最终缺席与单基线**

  Run: `test "$(rg --files migrations/versions -g '*.py' | wc -l | tr -d ' ')" = "1"`

  Run: `rg -n "smt_classifier|smt_sorting_inbound" migrations/versions --glob '*.py'`

  Expected: 单 revision；Phase 5 明确退役的业务 schema 词汇零命中；`test_initial_schema_baseline_postgresql.py` 按 Task 1 冻结的表/列/索引/约束矩阵证明全部退役对象不存在。不得把 `plugin_key`、`plugin_contract_version` 或 `rough_sorter` 目标身份作为全库禁词；其合法位置必须逐条来自 Task 1 冻结清单。Phase 12/13 插件名称和预留 schema 必须不存在。

- [ ] **Step 5: 提交前运行 GitNexus 变更检测**

  Run `gitnexus_detect_changes({scope: "staged"})`。

  Expected: 变更只影响数据库基线、迁移测试、selector 和 Phase 11 当前态文档。

- [ ] **Step 6: 独立代码评审**

  使用 `superpowers:requesting-code-review` 对完整 staged diff 做只读评审，并向 reviewer 提供 Step 1 冻结的路径、精确 staged 状态和 Steps 2–5 的验证证据；reviewer 不启动 Docker、不执行 HEAVY。通过 `superpowers:receiving-code-review` 核实意见并按 TDD 修复，修复后重新精确暂存并运行直接受影响测试；既有最终门禁证据标记为 `STALE`，中间评审轮次不重复执行完整 Steps 2–5。循环至无可操作问题后，回到 Step 1 核对最终 staged 清单，并在最终快照上完整重跑 Steps 2–5 一次；全部通过后才能提交。

- [ ] **Step 7: 提交数据库基线原子变更**

  在 staged HEAVY、GitNexus 检测和独立评审均无意见后运行：

  Run: `git commit -m "refactor(database): 重置未发布系统迁移基线"`

  Commit/PR 说明必须列出被删除测试的 successor 或 `NONE`；不得使用 `--no-verify`。

- [ ] **Step 8: 合入后清理 revision tombstone mappings**

  含 Task 1 冻结 revision 删除的提交合入 `develop` 后，以独立 cleanup 提交删除这些已不存在路径的精确 tombstone mappings，并同步删除只约束该过渡清单的 selector 测试数据；保留新基线 revision mapping 和通用 fail-closed 合同。先运行 `uv run pytest tests/scripts -q`，再对 cleanup 的 staged diff 运行 selector 与 `gitnexus_detect_changes({scope: "staged"})`，确认 CI base diff 已不再包含旧 revision 删除；全部通过后提交 `chore(database): 清理旧迁移 HEAVY tombstone`。不得在原基线 PR 内提前清理。

- [ ] **Step 9: 更新阶段状态并归档过程文档**

  只有全部门禁和 tombstone cleanup 通过后，才把 Phase 11 标记为完成并进入 Phase 12 教学式开发。先更新 master plan、README 和项目内所有当前态引用；确认活动残余计划已位于上述精确外部路径，否则停止。

  对本计划及 Task 1 写回的每个其它精确源路径分别计算 SHA-256，确认目标不存在；发生重名时先确定唯一目标名并回写清单，不得覆盖。逐项移动后验证项目内原路径缺席、外部归档存在且 SHA-256 相等；项目内不得保留副本、占位、软链接或转发文档。最后只精确暂存 master plan、README 和这些源文件删除，核对 `git diff --cached --name-status`，运行 `git diff --check`、引用扫描及 `gitnexus_detect_changes({scope: "staged"})`；全部通过后提交 `docs(database): 归档迁移基线重置计划`。
