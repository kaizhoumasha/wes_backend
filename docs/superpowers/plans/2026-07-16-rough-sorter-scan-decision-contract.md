# 粗分机扫码到入料决策窄闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 批准并机器化验证粗分机首个真实窄闭环业务合同：从 `SCAN_COMPLETED` 到入料机械臂结果、测量与 WMS 准入决策，最终稳定地产生 `MOVE_FORWARD`、`MOVE_TO_NG` 或业务 Hold。

**Architecture:** 本阶段只交付单一权威业务规格、一个窄闭环 trace fixture 和对应合同测试，不修改生产 Runtime。规格明确入口、状态、能力需求、证据、Intent、幂等、超时和 replay；fixture 记录目标行为及当前覆盖状态，为后续最小 Runtime contract/Plugin runtime 计划提供真实需求输入。现有广域架构文档只引用该规格，不复制合同正文。

**Tech Stack:** Markdown、JSON、Python 3.13、pytest、现有 rough sorter contract/orchestrator 测试、Ruff、GitNexus。

---

## 实施边界与锁定决策

- 本计划只完成平台设计 T2，不实现 T3-T9，不修改 `src/`、数据库、API、Celery、provider wiring 或设备协议入口。
- 窄闭环起点固定为已归一化且被 RuntimeInbox 接受的 `SCAN_COMPLETED`；终点固定为下一个设备命令已持久化，或 Session 进入带稳定原因码的业务 Hold。`MOVE_FORWARD` / `MOVE_TO_NG` 的执行结果属于后续粗分机切片。
- 正常主线固定为：六合一码通过 → `PICK_AND_PUT` → 成功结果携带有效测量值 → WMS 准入通过 → `MOVE_FORWARD`。
- NG 分支固定为：条码业务拒绝、测量业务 NG、WMS 明确拒绝或无匹配 → `MOVE_TO_NG`。设备失败、命令超时、WMS 查询超时和合同冲突不是业务 NG，必须 Hold，禁止伪造 NG 或自动推进。
- 缺失、不可解析或非正数的 `reel_diameter` / `reel_thickness` 属输入合同无效，进入 material-scoped Hold；只有设备明确返回业务测量 NG 时才进入 `MOVE_TO_NG`。
- 同一 `event_id` / `command_code` 与相同规范化 payload digest 重放时复用已录制决策和 evidence，不重复创建 MaterialUnit、查询 WMS 或下发命令；相同幂等键但 digest 不同进入 `IDEMPOTENCY_CONFLICT` Hold。
- replay 只能读取首次 attempt 持久化的条码、测量和 WMS evidence；禁止在 replay 中重新调用实时 WMS。首次 attempt 的 QUERY 超时没有成功 evidence，不得伪装成可 replay 的成功决策。
- 本系统未发布，不增加旧字段、旧插件 API、兼容 alias 或双轨 dispatcher。`docs/archive/` 只作为语义发现证据，不能成为目标合同真源。
- fixture 是测试/评审证据，不是运行配置，不引入通用 DSL、schema generator 或 production loader（KISS/YAGNI）。

## 当前事实与目标缺口

| 范围 | 当前事实 | 本计划处理 |
|---|---|---|
| 扫码入口 | `_rough_sorter_scan_completed_intents()` 已处理条码 OK/NG，并生成 MaterialUnit、Context 和首条设备命令 | 固化为目标规格的已覆盖行为，禁止复制实现 |
| 入料结果 | `_command_result_intents()` 对成功只返回通用 `CONTINUE_NEXT`，失败统一 command-scoped Block | 在规格中定义测量/WMS 业务决策需求，并标记为后续实现缺口 |
| 合同常量 | `rough_sorter.py` 已定义事件、动作、角色、阶段和 payload builder | 作为当前类型证据，不新增平行枚举 |
| Context | `RoughSorterContext` 仍包含多个自由格式字典 | 只定义本切片所需状态与 evidence 字段；强类型改造留给 T4 |
| 业务文档 | runtime flow、入库验收和 sorter capability spec 覆盖范围不同且部分超前于实现 | 新窄规格成为本切片唯一业务真源，其余文档只保留上层说明和引用 |
| characterization | BC-05 仅验证来源文件存在 | 升级为对批准规格与 trace fixture 的存在性/完整性约束，不声称生产已实现 |

现有 GitNexus 影响基线：`_rough_sorter_scan_completed_intents` 为 LOW，`SorterInboundRuntimeService` 与 `RoughSorterContext` 为 MEDIUM，`RuntimeCapabilityDispatcher` 为 LOW。本计划不修改这些符号；后续生产实现必须重新运行 impact analysis，若结果升至 HIGH/CRITICAL，先向用户报告并确认。

## 权威合同边界

新规格 `docs/business/rough_sorter_scan_decision_contract.md` 只拥有以下语义：

```text
SCAN_COMPLETED
  -> barcode decision
  -> PICK_AND_PUT result + measurement
  -> WMS admission evidence
  -> MOVE_FORWARD | MOVE_TO_NG | HOLD
```

它不拥有：输送线执行、格位预约、货架补给、出料机械臂、料格事实、WMS 入库记账、满箱交换和 SMT 分拣。`docs/architecture/sorter-inbound-capability-spec.md` 继续拥有完整 material-flow 架构，`docs/business/rough_sorter_runtime_flow.md` 继续拥有端到端设备协议示例，`docs/business/inbound_acceptance_steps.md` 继续拥有整线验收步骤，`docs/business/workline_business_data_event_flow_spec.md` 继续拥有跨系统数据与事件流。四者不得重复定义本切片的 reason code、replay 或分支判定。

### 状态与结果

| 场景 | 目标状态/结果 | 下一动作 | 稳定原因码 |
|---|---|---|---|
| 条码通过，等待入料结果 | `PICK_TO_PIPELINE` | `PICK_AND_PUT` | 无 |
| 入料成功、测量有效、WMS 准入 | `MOVING_FORWARD` | `MOVE_FORWARD` | 无 |
| 条码/测量/WMS 明确业务拒绝 | `NG_MOVING` | `MOVE_TO_NG` | 保留业务来源 reason code |
| 缺失或非法测量合同 | Hold(material) | 无 | `ROUGH_SORTER_MEASUREMENT_INVALID` |
| 入料设备失败 | Hold(command) | 无 | 优先设备 `error_code`，否则稳定分类码 |
| 入料结果超时 | Hold(command) | 无 | `ROUGH_SORTER_PICK_RESULT_TIMEOUT` |
| WMS 查询超时/不可用 | Hold(material) | 无 | `ROUGH_SORTER_WMS_ADMISSION_UNAVAILABLE` |
| 幂等键内容冲突 | Hold(material) | 无 | `IDEMPOTENCY_CONFLICT` |
| callback 关联不到当前等待命令 | 归档为 late/unknown evidence，不推进当前 Session | 无 | `COMMAND_RESULT_CORRELATION_MISMATCH` |

上述原因码属于本业务规格的目标语义。合同评审发现现有全局错误码已有等价名称时，必须统一到现有稳定成员并在规格决策记录中写明映射；禁止保留同义双码。

### Trace fixture 合同

`tests/fixtures/workline_contract/rough_sorter/scan_decision_cases.json` 顶层固定为：

- `schema_version = rough-sorter-scan-decision.v1`
- `slice_id = rough_sorter.scan_to_admission_decision`
- `cases`：只允许该切片的场景数组

每个 case 必须包含：`case_id`、`trigger`、`preconditions`、`recorded_evidence`、`expected_state`、`expected_intents`、`expected_outcome`、`replay_expectation`、`source_refs`、`implementation_status`。`implementation_status` 只允许 `covered`、`partial`、`gap`，表达当前事实而非测试豁免；任何 `gap` 都是 T3-T6 的输入，不能被描述为已完成。

固定场景集：

| Case | 场景 | 当前预期状态 |
|---|---|---|
| `RS-SD-001` | 有效扫码生成 MaterialUnit、Context 与 `PICK_AND_PUT` | covered |
| `RS-SD-002` | 条码明确业务 NG，生成 `MOVE_TO_NG` | covered |
| `RS-SD-003` | 缺少 `PkgID`，不创建物料/命令并 Hold | gap |
| `RS-SD-004` | 入料成功 + 有效测量 + WMS 准入，生成 `MOVE_FORWARD` | gap |
| `RS-SD-005` | 入料成功但测量业务 NG，生成 `MOVE_TO_NG` | gap |
| `RS-SD-006` | WMS 明确拒绝/无匹配，生成 `MOVE_TO_NG` | gap |
| `RS-SD-007` | 测量合同无效，material-scoped Hold | gap |
| `RS-SD-008` | 入料设备失败，command-scoped Hold | covered |
| `RS-SD-009` | 入料命令结果超时，command-scoped Hold | partial |
| `RS-SD-010` | WMS timeout/unavailable，保留 evidence 并 Hold | gap |
| `RS-SD-011` | 同键同 digest 重放，不重复 QUERY/EFFECT | gap |
| `RS-SD-012` | 同键不同 digest，`IDEMPOTENCY_CONFLICT` Hold | gap |
| `RS-SD-013` | late/unknown callback 不推进当前 Session | partial |

## NOT in scope

- 修改 `_rough_sorter_scan_completed_intents()`、`_command_result_intents()`、`RoughSorterContext` 或任何生产符号。
- 定义最终 `CapabilityDefinition`、`PluginDefinition`、Gateway、PluginState、PluginDecision、EffectApplier 或静态索引；属于 T3-T6。
- 实现或验证 WMS provider 的具体 HTTP/SDK 合同；本阶段只批准业务所需 QUERY 语义及 evidence。
- 完成整条粗分机、满箱交换或 SMT 分拣规格。
- 添加数据库迁移、API、CLI、脚手架、性能框架、通用 fixture loader 或兼容层。
- 把 `docs/archive/`、测试 fixture 或 Markdown 解析器接入生产运行时。

## 执行前置条件

- [ ] 从最新 `develop` 创建隔离分支/worktree；先提交或妥善保留当前已暂存文档，禁止覆盖已有修改。
- [ ] 使用 `superpowers:using-git-worktrees` 准备环境，运行 `./scripts/init-env.sh dev`、`uv sync --dev`。
- [ ] 运行 GitNexus context/impact：`_rough_sorter_scan_completed_intents`、`_command_result_intents`、`RoughSorterContext`、`SorterInboundRuntimeService`；只读文档阶段也记录基线，后续若误触生产符号立即停止。
- [ ] 运行当前合同基线：

  `uv run pytest tests/contracts/workline/test_rough_sorter_inbound_contract.py tests/workline_runtime/test_runtime_capability_dispatcher.py tests/characterization/workline_legacy/test_business_semantics_characterization.py -q`

  Expected: 全部通过；失败先按 `superpowers:systematic-debugging` 查明，不把已有失败混入规格提交。

### Task 1: 建立机器可判定的窄闭环规格包

**Files:**

- Create: `tests/contracts/workline/test_rough_sorter_scan_decision_spec.py`
- Create: `tests/fixtures/workline_contract/rough_sorter/scan_decision_cases.json`
- Create: `docs/business/rough_sorter_scan_decision_contract.md`

- [ ] **Step 1: 先写规格包结构失败测试**

测试直接读取固定 fixture 路径，不新增共享 loader。先锁定顶层版本/slice、13 个 case ID、必需字段、闭合枚举、唯一 case ID、非空 `source_refs`，并要求规格文档存在且包含合同版本、切片边界、批准状态、状态表、能力/evidence 表、异常矩阵、replay 与验收章节。只验证稳定标题和元数据，不对整篇 Markdown 做脆弱快照。

- [ ] **Step 2: 运行测试并确认因规格包尚不存在而失败**

Run: `uv run pytest tests/contracts/workline/test_rough_sorter_scan_decision_spec.py -q`

Expected: fixture 或规格文件不存在导致失败；不得通过 skip/xfail 绕过。

- [ ] **Step 3: 写最小 trace fixture**

按本计划固定的 13 个 case 写入输入摘要、首次 attempt evidence、目标状态/Intent、replay 期望、当前覆盖状态和精确源文件引用。payload 只保留决策所需字段；敏感数据使用明显虚构值。`expected_intents` 使用现有稳定 kind/action 名，不复制完整设备协议 JSON。

- [ ] **Step 4: 写单一权威业务规格**

规格必须包含：

1. 文档元数据：`contract_version`、`status`、`owner`、`approved_by`、`approved_at`；批准前 `status` 固定为 `Review`，批准字段固定为空值，不得伪造签字。
2. 起点/终点与明确排除范围。
3. 输入归一化、业务键、correlation/idempotency key 和 payload digest 规则。
4. 状态转换与每个分支的 outcome、Intent、reason code。
5. 所需能力清单：条码判定（本地纯决策）、WMS 准入 QUERY、Material/Context/DeviceCommand EFFECT；只描述业务语义和 evidence，不预先设计通用平台接口。
6. 首次 attempt 与 replay 的证据所有权：输入快照、测量、WMS 响应摘要、决策、Intent 身份和 digest。
7. 成功、NG、合同无效、设备失败、命令超时、WMS 超时、重复、冲突、late callback 的验收矩阵。
8. “当前实现对照”章节：明确 covered/partial/gap，特别指出当前成功 callback 仅 `CONTINUE_NEXT`，不能声称已实现测量/WMS 分支。

- [ ] **Step 5: 运行结构合同测试并通过**

Run: `uv run pytest tests/contracts/workline/test_rough_sorter_scan_decision_spec.py -q`

Expected: 全部通过。

- [ ] **Step 6: 提交规格包**

```bash
git add docs/business/rough_sorter_scan_decision_contract.md tests/fixtures/workline_contract/rough_sorter/scan_decision_cases.json tests/contracts/workline/test_rough_sorter_scan_decision_spec.py
git commit -m "docs(workline): 定义粗分机扫码决策窄闭环"
```

### Task 2: 用现有公开合同核实 covered/partial 声明

**Files:**

- Modify: `tests/contracts/workline/test_rough_sorter_scan_decision_spec.py`
- Modify: `tests/characterization/workline_legacy/test_business_semantics_characterization.py`

- [ ] **Step 1: 先写当前能力对照失败测试**

参数化读取 fixture 中 `covered` / `partial` case，至少验证：六合一码只从 `data` 归一化、有效扫码生成 `PICK_AND_PUT` 合同、条码 NG 生成 `MOVE_TO_NG` 合同、缺 `PkgID` 的目标合同要求 fail closed，但实施核实发现现有 `BARCODE_INCOMPLETE` 会进入 NG 命令而非 Hold，因此 `RS-SD-003` 保持 gap；成功 command result 当前只产生 `CONTINUE_NEXT`、失败 result 产生 command-scoped Block、late/duplicate result 不推进当前 Session 的现有边界。测试可以调用现有公开 normalizer/builder 和 Orchestrator facade；禁止 import 私有 helper 或复制业务分支。

- [ ] **Step 2: 运行并确认 fixture/current mapping 尚未接入导致失败**

Run: `uv run pytest tests/contracts/workline/test_rough_sorter_scan_decision_spec.py tests/characterization/workline_legacy/test_business_semantics_characterization.py -q`

Expected: 新的 current mapping 或 characterization 来源断言失败，而非生产回归。

- [ ] **Step 3: 完成最小对照与 characterization 升级**

在 characterization 的 BC-05 sources 中加入新规格、fixture 和合同测试；注释明确 legacy archive 只是输入来源。合同测试只验证当前声明与现有行为相符，不为 `gap` 编写会让默认回归永久失败的生产实现断言；gap 通过精确 case 集与 `implementation_status` 被机器保留。

- [ ] **Step 4: 运行粗分机合同域测试并通过**

Run: `uv run pytest tests/contracts/workline/test_rough_sorter_inbound_contract.py tests/contracts/workline/test_rough_sorter_scan_decision_spec.py tests/workline_runtime/test_runtime_capability_dispatcher.py tests/characterization/workline_legacy/test_business_semantics_characterization.py -q`

Expected: 全部通过，且 fixture 中仍明确保留未实现 case。

- [ ] **Step 5: 提交对照测试**

```bash
git add tests/contracts/workline/test_rough_sorter_scan_decision_spec.py tests/characterization/workline_legacy/test_business_semantics_characterization.py
git commit -m "test(workline): 锁定粗分机窄闭环现状差距"
```

### Task 3: 收束文档真源与业务批准

**Files:**

- Modify: `docs/architecture/sorter-inbound-capability-spec.md`
- Modify: `docs/business/rough_sorter_runtime_flow.md`
- Modify: `docs/business/inbound_acceptance_steps.md`
- Modify: `docs/business/workline_business_data_event_flow_spec.md`

- [ ] **Step 1: 先扩展文档引用合同测试并观察失败**

要求四份上层文档以相对路径引用 `rough_sorter_scan_decision_contract.md`，并分别声明自身所有权，不得包含第二份 reason-code/replay 真源。测试只检查引用与简短 ownership marker，不解析自然语言业务正文。

Run: `uv run pytest tests/contracts/workline/test_rough_sorter_scan_decision_spec.py -q`

Expected: 四份文档尚未全部引用新真源而失败。

- [ ] **Step 2: 最小化同步上层文档**

只增加“本切片以新规格为准”的引用和边界说明；若旧正文与批准合同冲突，删改冲突句并保留有价值的协议示例/整线验收上下文。禁止把新规格的状态表、reason code 或 replay 规则复制到四份文档。

- [ ] **Step 3: 进行业务批准门禁**

向业务 owner 展示 13 个 case，逐项确认：正常、三类 NG、非法测量、设备失败、两类 timeout、同键重放、同键冲突和 late callback。批准前不得把状态改为 `Approved`。获得明确批准后，在新规格写入真实 `approved_by` 与带时区的 `approved_at`，并把 `status` 改为 `Approved`；若任何分支未获确认，保留 `Review` 并停止 T3 计划，不用工程假设补齐业务决定。

- [ ] **Step 4: 运行文档/合同回归并通过**

Run: `uv run pytest tests/contracts/workline/test_rough_sorter_scan_decision_spec.py tests/characterization/workline_legacy/test_business_semantics_characterization.py -q`

Expected: 全部通过；测试同时断言 `Approved` 时批准人和 aware ISO 时间非空，`Review` 时批准字段为空。

- [ ] **Step 5: 提交文档真源同步**

```bash
git add docs/architecture/sorter-inbound-capability-spec.md docs/business/rough_sorter_runtime_flow.md docs/business/inbound_acceptance_steps.md docs/business/workline_business_data_event_flow_spec.md docs/business/rough_sorter_scan_decision_contract.md
git commit -m "docs(workline): 批准粗分机窄闭环业务合同"
```

### Task 4: 完成 T2 门禁并生成 T3 输入

**Files:**

- Modify: `docs/superpowers/specs/2026-07-15-workline-plugin-system-capability-platform-design.md`

- [ ] **Step 1: 更新平台进度但不夸大完成范围**

仅在新规格状态为 `Approved` 时勾选 T2，并链接规格与 fixture。进度说明必须保留：T1 remaining 未完成；T3-T9 未开始；当前生产 Runtime 仍存在 fixture 标记为 `partial` / `gap` 的实现缺口，不得把业务合同批准写成运行时交付。

- [ ] **Step 2: 从 fixture 生成下一计划的真实需求清单**

在平台设计 T3 任务下补充一段简短输入说明：最小 Runtime contract 必须先支持本切片用到的 typed outcome、QUERY evidence、Intent 身份与 replay，禁止加入第二条 Workline 或通用 DSL。这里仅记录需求边界，不编写 T3 实现计划。

- [ ] **Step 3: 运行完整计划验证**

Run:

```bash
uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
uv run pytest tests/contracts/workline/test_rough_sorter_inbound_contract.py tests/contracts/workline/test_rough_sorter_scan_decision_spec.py tests/workline_runtime/test_runtime_capability_dispatcher.py tests/characterization/workline_legacy/test_business_semantics_characterization.py -q
uv run pytest --collect-only -q -o addopts='' | tail -5
uv run ruff format --check tests/contracts/workline/test_rough_sorter_scan_decision_spec.py tests/characterization/workline_legacy/test_business_semantics_characterization.py
uv run ruff check tests/contracts/workline/test_rough_sorter_scan_decision_spec.py tests/characterization/workline_legacy/test_business_semantics_characterization.py
./scripts/git-quality-gate.sh --profile quality
```

Expected: topology、受影响合同域、collect-only、format、lint 和质量门禁全部通过。无生产代码变化，因此不运行 Alembic、Celery 或外部 WMS 集成；若执行中意外出现生产 diff，停止并拆入 T3+ 计划。

- [ ] **Step 4: 运行 GitNexus 变更检测**

Run: 调用 GitNexus MCP `gitnexus_detect_changes()`。

Expected: 只包含文档、fixture、合同测试和 characterization；不得出现生产执行流变化。

- [ ] **Step 5: 检查变更与占位符**

Run:

```bash
git diff --check
rg -n "T[B]D|TO BE DECIDE[D]|待[定]|待[补]|FIX[M]E|PLACEHOLD[E]R|example[.]com" docs/business/rough_sorter_scan_decision_contract.md tests/fixtures/workline_contract/rough_sorter/scan_decision_cases.json
git diff --stat develop...HEAD
```

Expected: diff check 通过；占位符扫描无输出；diff 仅为批准规格包、引用同步、测试和平台进度。

- [ ] **Step 6: 提交 T2 进度**

```bash
git add docs/superpowers/specs/2026-07-15-workline-plugin-system-capability-platform-design.md
git commit -m "docs(workline): 同步粗分机窄闭环进度"
```

## 验收标准

1. 新规格已获真实业务 owner 批准，且起点、终点、排除范围和文档所有权无歧义。
2. 13 个 trace case 机器可校验，完整覆盖输入、状态、能力、成功、NG、合同错误、timeout、幂等冲突、late callback 和 replay。
3. fixture 明确区分 `covered` / `partial` / `gap`；合同批准不被误报为生产实现完成。
4. replay 只消费 recorded evidence，同键重放不重复 QUERY/EFFECT，同键异载荷 fail closed。
5. 旧 archive 不再被视为目标真源；四份上层文档只引用窄规格，不复制判定规则。
6. 默认快速回归中的合同/characterization 测试能阻止规格包丢失、场景缩水、枚举漂移和批准元数据伪造。
7. 无 `src/`、数据库、API、Celery、provider wiring 或生产执行流变化。
8. 平台设计只标记 T2 完成，并为 T3 留下由真实 gap 驱动的最小合同输入。

## 失败与停止条件

- 业务 owner 未明确批准任一关键分支：规格保持 `Review`，T2 未完成，禁止启动 T3 实现。
- 现有稳定错误码与本计划建议码冲突：先统一规格映射，不创建同义码。
- 当前行为与 fixture 的 `covered` 声明不符：修正事实标记或另立生产缺口；不得为了让文档测试通过而改生产代码。
- 执行需要修改任何生产 symbol：停止本计划，重新运行 GitNexus impact，并在独立 T3-T6 实施计划中处理。
- 发现窄闭环必须跨越输送线、格位或出料才能形成可验收结果：先回到业务 owner 重新批准切片边界，不静默扩张本计划。
