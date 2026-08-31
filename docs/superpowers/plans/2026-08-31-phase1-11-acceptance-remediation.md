# Phase 1–11 验收阻塞项修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `wes-implementation` to execute this WES plan. If the user later explicitly authorizes subagents, use `superpowers:subagent-driven-development`; otherwise use `superpowers:executing-plans` and execute task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 闭合 Phase 1–11 当前验收中的五类阻塞：Transport 0.3 实施对齐、510056 联调步进消费新语义/WIRE、ECS ACK Approved 合同自洽、活动状态文档准确、当前 HEAD 的 Phase 8 不可变集成制品与 E2E 证据。

**Architecture:** 保留现有 `TransportService → WmsTransportAdapter → WmsClient`、插件 `Decision → DecisionApplier → TransportService` 和 `/ops/transport-diagnostics` 五步联调步进器路径，不增加兼容层、版本路由、自动循环器或供应商私有适配器。Transport 0.3 以普通不透明 JSON string 面向值、闭集 `rcs_template_id` 和 `RACK | ZONE | RACK_POSITION` 位置联合贯穿领域对象、冻结快照、数据库投影、OpenAPI、Mock、粗分插件与 510056 调试请求。当前 WMS/RCS 联调约定使用 `"90"`、`"270"`，但 WES 不把它们解释为角度或 A/B，只保证调用方数据、冻结快照、下发请求、回调、审计和投影原样传递并精确一致。

**Tech Stack:** Python 3.13、Pydantic、SQLModel/SQLAlchemy、Alembic、FastAPI、Pytest、PostgreSQL、Docker、Ruff、BasedPyright。

**Spec:**

- `docs/contracts/transport-fulfillment-contract.md`
- `docs/architecture/SRS.md`
- `docs/superpowers/specs/2026-08-14-wes-wms-transport-dto-design.md`
- `docs/superpowers/specs/2026-08-26-transport-integration-diagnostics-design.md`
- `docs/contracts/wms-rough-sorter-inbound-integration-requirements.md`
- `docs/integration/wes-wms-interface-requirements.md`
- `docs/integration/third_party_integration_whitepaper.md`
- Existing 510056 stepper: `../wes_frontend/src/views/ops/transport-diagnostics/useTransportDebugLoop.ts`

## Global Constraints

- 当前执行基线必须重新冻结；计划编写时为 `develop@ba9a360e873bf841eae1d6a37d15e07311a1e329`，不得把该 SHA 当作未来执行时的当前事实。
- 510056 debug-loop 线程快照显示 backend PR #192 已合入 `develop@30d3da57`；执行时仍须重新确认该 Commit/等价变更已在最新 `origin/develop`，不得把线程快照当作当前远端事实。确认后从更新后的 `origin/develop` 创建专用 `codex/feat-transport-03-alignment` backend worktree；禁止把本计划叠加到 debug 分支或先从旧 `develop` 开工再补冲突。
- 510056 前端合同同步线程快照停在 PR #87 `READY TO MERGE — NOT MERGED — NOT DEPLOYED`；Task 4A 开始前须重新确认 frontend `origin/develop` 是否已包含该同步结果。若未包含，则在独立 frontend worktree 的同一 Task 4A 变更中先承接该精确合同同步，不把旧 A/B 生成类型带入 0.3 步进器。
- Frontend `contract:freeze` 强制读取 clean backend `develop` checkout；因此 Task 4A backend 部分先进入 Task 7 的最终门禁，只有 backend Merge 另行授权且新合同确已进入 `develop` 后，才执行 Task 4A frontend contract sync。禁止从未合并 feature worktree 手工复制 OpenAPI 或伪造 `.contract-sync-record.json`。
- Transport 0.3 的 6 份目标文档已经由 PR #191 合入 `develop@80d094ad`；除本轮明确撤销 face 内容校验并改为普通 string 外，其余字段形态仍是 Approved 输入。实施先同步这项合同修订，证据闭合后再更新 alignment 状态。
- 使用 `uv run ...`；不依赖外部 Shell 已激活的虚拟环境。
- 本计划是实施授权前的执行设计，不授权 Commit、Push、PR、Merge、Deploy、数据库重建、外发合同或物理设备操作。
- 系统未发布，`migration_strategy=direct_replacement`：不保留 A/B enum、整数面、双写、shim、v2 路由或兼容解析。
- `rack_face`、`target_face`、`arrival_face` 按各自既有上下文允许 `None`；一旦提供，线上 JSON value 必须是非空 string。除拒绝 `""` 这条 presence 约束外，不定义 `RackFace` enum/class，不增加最大长度、字符集合、空白、BOM、控制字符、大小写或 Unicode normalization 规则。当前联调 happy path 使用 `"90" | "270"`，`"FACE@01"` 和 `"面-1"` 只用于证明非空 string 都按普通数据处理。
- WES 对 face string 不执行 trim、case folding、Unicode normalization、A/B 转换、角度计算、容差或其它内容解释；只对 JSON 解析后字符串做普通精确相等比较。底层 JSON/UTF-8/PostgreSQL 自身无法表示的输入按平台错误处理，不为此增加编码、映射或兼容层。
- `rcs_template_id` 只接受 `CTU01 | CTU02 | CTU03 | F01`；`move_rack()` 省略时在冻结请求前规范化为 `F01`，`rotate_rack()` 省略时规范化为 `CTU02`，wire 始终显式发送。除此之外不根据位置或 face 内容推断模板，也不建立模板映射表。
- Alembic 把 `arrival_face` 从真实基线的 `VARCHAR(1)` 扩为 `TEXT`，不增加 face 内容 CHECK，不推断或转换任何旧值；基线没有 A/B CHECK constraint，不得在 upgrade 或 downgrade 中虚构该历史合同。现有字符串原样保留。生产代码、恢复、回调、fixture 和 API 均直接使用普通 string。
- 当前仅处于联调阶段且没有生产数据；执行者可在运行迁移链前清理或重建明确识别的专用联调数据库，但数据库清理不是 migration 逻辑，不能把 `""` 改写为 `NULL`。实际清理前仍须核对精确目标、非生产标识和可恢复性；本评审只记录允许边界，不执行清理。
- `RACK_MOVE` 核心只表达一个确定 `rack_id` 的搬运，不判断单层/五层、空/满、容量、可达性、路径、车辆或执行顺序。
- 当前唯一生产业务调用链是粗分换架的 `OLD_OUT` 与 `NEW_IN`；其它场景即使通过核心合同测试，也只能写成“核心能力支持”，不能写成“业务流程已接入”。
- 510056 是既有 `TRANSPORT_DEBUG` 操作员联调调用链，不是生产业务调用链。它只消费请求内显式冻结的 face string，不创建或改写业务 `PositionProjection`，也不把操作员确认冒充 WMS/RCS/ECS 权威回调。
- 510056 固定联调数据沿用 rack `510056`、bins `A000001922/A000002653`、slots `510056A3F2C101/510056A2F2C101`、warehouse zone `WH01`、exact rack position `KT16`、handoffs `CNV0301/CNV0302` 和 SCAN9→12；当前冻结 `target_face/rack_face` 为显式 JSON string `"90"`。该值来自当前联调合同样例，不由旧 `A` 推导；`"270"` 继续作为同一 WIRE 的另一约定 string 值覆盖合同测试，但本计划不擅自改变 510056 现场姿态。
- `OLD_OUT` 与 `NEW_IN` 是两个独立任务，不是原子交换：新架正确到位后可继续目标请求而不等待旧架；旧架不确定只隔离旧架；新架不确定阻断后续目标请求。
- 两个换架任务的物理顺序、避让和共享工作位互锁由 RCS 负责，WES 不新增本地排序器或 `RACK_EXCHANGE`。
- 物理结果不确定时保留原 `transport_task_id`、冻结请求、Evidence 和资源围栏，继续 `DELIVERY_UNKNOWN/RECONCILING`。
- 真实 WMS/RCS/ECS、供应商、物理运动和业务验收保持 `NOT RUN`，不能由 Mock、E2E、HTTP 200、ACK 或容器健康替代。
- HEAVY 只执行 selector 输出；迁移验证只使用独占临时 PostgreSQL，不使用共享 dev 数据库。

## 交付轨道与门禁

本计划保留 Phase 1–11 仓内验收的完整范围，但不把五类阻塞压进同一个实施现场。三个轨道分别冻结变更、证据和 Review 边界：

```text
Task 0: 冻结共同 base / head / dirty / contract / test owner
  |
  +--> Track A: Transport 0.3 可执行合同
  |      Task 1 领域与冻结快照
  |        -> Task 2 持久化与 migration
  |        -> Task 3 WMS wire / OpenAPI / Mock
  |        -> Task 4 SDK / 粗分唯一生产消费者
  |        -> Task 4A 510056 五步联调消费者 / frontend contract sync
  |
  +--> Track B: 当前真源文档
         Task 5 ECS ACK Approved 自洽
           -> Task 6 在 Track A 实际状态已知后刷新活动状态
  |
  +--> Track C: 最终同快照验收
         Task 7 仅在 Track A + Track B 均闭合后运行
```

| Track | 独立完成条件 | 不得替代的证据 |
| --- | --- | --- |
| A | 领域、DB、ACL、SDK、插件调用链、510056 debug consumer 和各自 owner 测试对齐 | 不以文档或 Mock 代替生产代码、migration、插件或前端回归 |
| B | ACK 规则无矛盾，活动文档只陈述 Track A 的实际状态 | 不提前写 `ALIGNED`，不以历史绿灯描述当前 executable tree |
| C | QUALITY、selected HEAVY、migration、镜像和 E2E 绑定同一最终快照 | 不以本地 E2E 代替 Deploy、供应商、物理或业务验收 |

Backend 三个 Track 共享 Task 0 创建的唯一专用 backend 分支/worktree。Task 4A backend 部分随 Tasks 1–4 进入最终 backend 门禁；backend Merge 另行授权并进入 `develop` 后，Task 4A frontend 部分才在独立 frontend 分支/worktree消费该 clean `develop` Commit。两个仓库不共享 dirty 状态，且 backend/frontend 写入串行推进。不得为 Track B 创建第二个 backend dirty worktree，也不得在 Track C 前人工拼接未提交差异。Task 6 先记录 pending 状态，最终 alignment 等待两个仓库证据；每个仓库的 Commit、Push、PR、Merge 和 Deploy 都分别授权，不因总计划存在而扩大授权。

## 测试覆盖路径

本计划以 `pytest` 为测试框架，测试所有权与 FAST/HEAVY 边界以 `tests/README.md` 和 `docs/architecture/heavy-test-impact.toml` 为准。下图中的 `PLANNED ★★★` 表示计划已指定行为、边界和失败路径测试，但只有实施后在最终快照执行通过才能成为绿色证据。

```text
CODE PATHS                                                     ACCEPTANCE FLOWS
[+] Domain request                                             [+] WMS/Debug rack request
  ├─ [PLANNED ★★★] None only where context permits               ├─ [PLANNED ★★★] "90"/"270" stay strings
  ├─ [PLANNED ★★★] reject "" / number / bool                      ├─ [PLANNED ★★★] whitespace/Unicode preserved
  ├─ [PLANNED ★★★] preserve every other non-empty string          └─ [PLANNED ★★★] invalid input creates no task/outbox
  └─ [PLANNED ★★★] edge/template positive + negative matrix
          |
          v
[+] TransportService -> normalized request                    [+] Retry / concurrency semantics
  ├─ [PLANNED ★★★] move omitted == explicit F01                  ├─ [PLANNED ★★★] equivalent retry returns same handle
  ├─ [PLANNED ★★★] rotate omitted == explicit CTU02              └─ [PLANNED ★★★] changed template conflicts, no side effect
  ├─ [PLANNED ★★★] request_digest + request_json
  └─ [PLANNED ★★★] frozen submit body always carries template
          |
          v
[+] WMS adapter / callback                                    [+] Result acceptance
  ├─ [PLANNED ★★★] strict non-empty string / nullable context    ├─ [PLANNED ★★★] exact target: exact location required
  ├─ [PLANNED ★★★] no trim/map/normalize                        ├─ [PLANNED ★★★] RACK/ZONE: concrete position accepted
  └─ [PLANNED ★★★] OpenAPI + Mock use same schema                └─ [PLANNED ★★★] wrong rack/face/missing/unknown fenced
          |
          v
[+] Evidence -> member -> projection -> outcome               [+] Persistence / recovery
  ├─ [PLANNED ★★★] TEXT preserves NULL and raw strings           ├─ [PLANNED ★★★] base->head + safe downgrade
  ├─ [PLANNED ★★★] migration preserves legacy "" without rewrite ├─ [PLANNED ★★★] SDK/recovery rebuild exact frozen token
  └─ [PLANNED ★★★] callback mismatch keeps identity/fence        └─ [PLANNED ★★★] no success publish on mismatch
          |
          v
[+] Rough-sorter OLD_OUT / NEW_IN [->E2E]                    [+] Installed-image business flow [->E2E]
  ├─ [PLANNED ★★★] OLD_OUT=CTU03, face="90", isolated            ├─ [PLANNED ★★★] real API/worker/DB/Redis/WMS stub
  ├─ [PLANNED ★★★] NEW_IN=CTU01, face="270", gates target        ├─ [PLANNED ★★★] two independent transport identities
  ├─ [PLANNED ★★★] NEW_IN may complete before OLD_OUT            └─ [PLANNED ★★★] NEW_IN success releases next target once
  └─ [PLANNED ★★★] failure/unknown never enters target lane

[+] 510056 debug stepper                                     [+] Operator-gated joint-debug flow
  ├─ [PLANNED ★★★] ZONE WH01 -> RACK_POSITION KT16 / CTU01     ├─ [PLANNED ★★★] exact kind/target/face before reset
  ├─ [PLANNED ★★★] rack slots -> CNV0301 / face="90"           ├─ [PLANNED ★★★] OPERATOR_DEBUG is never authoritative
  ├─ [PLANNED ★★★] SCAN9..12 remains existing ECS debug flow   ├─ [PLANNED ★★★] failure/unknown stops without auto retry
  └─ [PLANNED ★★★] slots -> RACK_POSITION KT16 -> ZONE WH01     └─ [PLANNED ★★★] formal BIN_MOVE projection gate unchanged

COVERAGE AFTER ACCEPTED PLAN UPDATES: 40/40 branches have an explicit owner (100%)
QUALITY TARGET: ★★★ 40  |  REMAINING TEST GAPS: 0  |  LLM/EVAL: not applicable
Legend: ★★★ behavior + edge + error  |  [->E2E] immutable-image integration path
```

---

### Task 0: 冻结 Execution Lock 与完整影响面

**Files:**

- Inspect: `AGENTS.md`
- Inspect: `tests/README.md`
- Inspect: `docs/architecture/heavy-test-impact.toml`
- Inspect: 本计划列出的全部生产、测试、合同和生成物

**Interfaces:**

- Consumes: 已包含 backend debug-loop Land 结果的最新 `origin/develop`、frontend 当前 `origin/develop`/PR #87 状态、Approved Transport 0.3 合同、上一轮验收 findings。
- Produces: backend 与 frontend 各自的 base/head/dirty fingerprint、生产符号与调用点清单、测试 owner、HEAVY owner、迁移范围和无关 dirty 指纹。

- [ ] **Step 1: 验证 debug Land 并创建 backend 隔离 worktree**

  先读取当前 debug task/PR 的最终结果并刷新远端，确认 backend PR #192 的 merge Commit `30d3da57`（或包含完全等价变更的后继 Commit）是最新 `origin/develop` 的祖先；未合入或结果不明确时停止，不创建修复分支。确认后在 backend 主仓库 Run:

  ```bash
  git fetch origin develop
  git rev-parse origin/develop
  git merge-base --is-ancestor 30d3da57 origin/develop
  git log --oneline --decorate -20 origin/develop
  git worktree add -b codex/feat-transport-03-alignment /Users/kaizhou/codeDev/wes_backend-worktrees/codex-feat-transport-03-alignment origin/develop
  cd /Users/kaizhou/codeDev/wes_backend-worktrees/codex-feat-transport-03-alignment
  ./scripts/init-env.sh dev
  uv sync --dev
  ./scripts/install-git-hooks.sh
  git branch --show-current
  git rev-parse HEAD
  git rev-parse origin/develop
  git status --short
  git worktree list --porcelain
  ```

  Expected: debug-loop 的 service/API/tests 已进入基线；branch 为 `codex/feat-transport-03-alignment`，HEAD 等于当时的 `origin/develop`，worktree clean 且环境/Hook 独立。把此时 `git rev-parse origin/develop` 的完整 SHA 冻结为后续唯一 `review_base`；不得在 Task 7 用移动中的 `origin/develop` ref 替换它。明确记录 debug Land 的 PR/merge 证据与 base/head；任何已有同名 branch/worktree 或非 clean 现场均停止处理，不覆盖或复用。

  此 worktree 是 backend Tasks 1–7 的唯一写现场。Task 4A 的 frontend 改动使用独立 paired worktree，并通过 `contract:freeze` 绑定已 Land 的 clean backend `develop` Commit/OpenAPI；不得跨仓复制未提交生成物。

- [ ] **Step 2: 刷新 GitNexus 并保护 Agent 入口**

  Run:

  ```bash
  shasum -a 256 AGENTS.md CLAUDE.md
  npx gitnexus status
  npx gitnexus analyze
  shasum -a 256 AGENTS.md CLAUDE.md
  ```

  Expected: 索引指向当前 HEAD；若 `analyze` 改写 `AGENTS.md` 或 `CLAUDE.md`，只恢复工具生成且不与用户变更重叠的内容。存在重叠时停止实施并报告。

- [ ] **Step 3: 对计划内生产符号完成 upstream impact analysis**

  Symbols:

  ```text
  RackFace
  RackPosition
  MoveRackRequest
  RotateRackRequest
  TransportPort.move_rack
  TransportPort.rotate_rack
  TransportService.move_rack
  TransportService.move_rack_in_session
  TransportService.rotate_rack
  TransportService.move_bins_for_debug
  TransportService.reset_debug_task
  TransportService._audit_debug_step_confirmation
  _debug_step_matches_frozen_request
  build_submit_data
  validate_callback_envelope
  PositionProjectionService.apply_transport_result
  CreateTransportTask
  RackMoveLegPlan
  rack_move_plan
  ```

  Expected: 固定直接调用者、执行流程和风险级别。HIGH/CRITICAL 影响链在首次生产补丁前向用户报告并取得范围确认；若 GitNexus 仍不可用，以精确 `rg`、调用链、测试 owner 和 HEAVY mapping 降级。

- [ ] **Step 4: 固定旧合同残留清单**

  Run:

  ```bash
  rg -n '\bRackFace\b|CoreRackFace|(target_face|rack_face|arrival_face)\.value|FACE_(90|270)|Literal\["A", "B"\]|\{"A", "B"\}' src packages deployment scripts tests workline_plugins
  rg -n '"(rack_face|target_face|arrival_face)"[[:space:]]*:[[:space:]]*(90|270)([^0-9]|$)|"type"[[:space:]]*:[[:space:]]*"integer"' src packages deployment scripts tests workline_plugins docs/contracts/openapi
  rg -n 'rcs_template_id|MoveRackRequest|RotateRackRequest|move_rack\(|rotate_rack\(' src packages deployment scripts tests workline_plugins
  rg -n "RACK_FACE|rack_face: 'A'|target_face: 'A'|A 面|RackFace" /Users/kaizhou/codeDev/wes_frontend/src/views/ops/transport-diagnostics /Users/kaizhou/codeDev/wes_frontend/tests/unit/views/ops/transport-diagnostics
  ```

  Expected: 将命中项按生产代码、共享 fixture、FAST、integration、E2E、插件与 frontend 独立测试分类；明确列出 510056 stepper 的旧 `RACK_FACE='A'`、文案和断言，后续一次性替换为合同已显式给出的 `"90"`，但绝不实现 `A → "90"` 映射；不通过整目录失败逐个发现调用点。

---

### Task 1: 实施 Transport 0.3 领域合同与冻结快照

**Files:**

- Modify: `src/app/transport/contracts.py`
- Modify: `src/app/transport/__init__.py`
- Modify: `src/app/transport/service.py`
- Modify: `src/app/transport/submit_snapshot.py`
- Test: `tests/runtime/transport/test_transport_contracts.py`
- Test: `tests/runtime/transport/test_transport_service.py`
- Test: `tests/runtime/transport/test_transport_outcome.py`
- Test: `tests/runtime/transport/test_transport_acceptance_edges.py`
- Test: `tests/runtime/transport/test_transport_submit_fencing.py`
- Test: `tests/contracts/wms_adapter/test_transport_wire_acceptance.py`

**Interfaces:**

- Consumes: Transport 0.3 Approved contract。
- Produces:

  ```python
  class RcsTemplateId(StrEnum):
      CTU01 = "CTU01"
      CTU02 = "CTU02"
      CTU03 = "CTU03"
      F01 = "F01"

  @dataclass(frozen=True, slots=True)
  class RackReference:
      location_code: str
      kind: str = field(default="RACK", init=False)

  @dataclass(frozen=True, slots=True)
  class ZonePosition:
      location_code: str
      kind: str = field(default="ZONE", init=False)

  type RackMovePosition = RackReference | ZonePosition | RackPosition
  ```

  `move_rack(..., source: RackMovePosition, target: RackMovePosition, target_face: str, rcs_template_id: RcsTemplateId = RcsTemplateId.F01)`；`rotate_rack(..., position: RackPosition, target_face: str, rcs_template_id: RcsTemplateId = RcsTemplateId.CTU02)`。`RackBinSlot.rack_face`、callback `arrival_face` 和 outcome face 同样直接使用 `str`。联合类型只表达可用的位置值，不代表任意 source/target/template 组合都被合同接受；领域请求必须按下列封闭矩阵校验：

  | Template | Allowed source → target |
  | --- | --- |
  | `CTU01` | `ZONE → RACK_POSITION`、`RACK → RACK_POSITION`、`RACK_POSITION → RACK_POSITION` |
  | `CTU03` | `RACK_POSITION → RACK`、`RACK_POSITION → ZONE`、`RACK_POSITION → RACK_POSITION` |
  | `F01` | `RACK_POSITION → RACK_POSITION` |
  | `CTU02` | 仅 `RACK_ROTATE` 的同一精确 `RACK_POSITION`；不得用于 `RACK_MOVE` |

  矩阵外的组合必须在创建 `MoveRackRequest` / `RotateRackRequest` 时失败，不能下沉给 WMS/RCS 决定，也不能产生 outbox、HTTP 请求或物理义务。

- [ ] **Step 1: 写领域 RED 测试**

  Add focused cases proving:

  ```python
  assert MoveRackRequest(..., RackReference("rack-1"), RackPosition("WORK"), "90", RcsTemplateId.CTU01).target_face == "90"
  assert MoveRackRequest(..., ZonePosition("ZONE-A"), RackPosition("WORK"), "270", RcsTemplateId.CTU01).target_face == "270"
  assert MoveRackRequest(..., RackPosition("A"), RackPosition("B"), "FACE@01").target_face == "FACE@01"
  assert MoveRackRequest(..., RackPosition("A"), RackPosition("B"), "面-1").target_face == "面-1"
  assert RotateRackRequest(..., RackPosition("WORK"), "270").rcs_template_id is RcsTemplateId.CTU02
  ```

  Face 字段按上下文接受 `None` 或非空 string；必填请求字段仍不得为 `None`。拒绝空字符串、JSON number `90/270` 和 `bool`，但空白、控制字符以及任意长度的非空 string 均不触发其它 face 内容校验。另 reject `RACK.location_code != rack_id`、未知位置 kind、非法模板、`source == target`；逐项拒绝矩阵外的 `RACK → ZONE`、`ZONE → RACK`、`RACK → RACK`、`ZONE → ZONE`、模板与边不匹配以及 `CTU02 + RACK_MOVE`，并证明失败不会创建 outbox 或调用 WMS。`RotateRackRequest` 只接受 `CTU02` 和同一精确 `RackPosition`。精确比较测试证明 `"face" != "FACE"` 且 `"é" != "e\u0301"`，但两边都是合法 string。

  增加 rack 模板默认值的幂等回归：同一 `client_request_id` 下，`move_rack()` 省略模板与显式 `F01`、`rotate_rack()` 省略模板与显式 `CTU02` 必须得到相同 `request_digest`、冻结 `request_json`、submit body 和原 handle，数据库仍只有一个 task/outbox。使用同一 identity 改为另一条经位置矩阵允许的显式模板时必须产生 `TransportIdempotencyConflict`，且不得新增 task/outbox。该测试证明默认值在摘要和冻结之前规范化，不能只断言 dataclass 属性。

- [ ] **Step 2: 运行 RED 切片**

  Run:

  ```bash
  uv run pytest tests/runtime/transport/test_transport_contracts.py -q
  ```

  Expected: 新 0.3 cases 因旧 A/B enum、缺少普通 string 透传、精确比较和模板字段失败；旧无关合同保持绿色。

- [ ] **Step 3: 最小实现领域类型、端口签名和请求校验**

  删除 `RackFace` enum/class 和全部 `.value` 调用，face 字段直接标注为 `str` 或上下文所需的 `str | None`；领域入口显式检查 `None` 允许性、`type(value) is str` 和 `value != ""`，不新增 face helper、映射或其它内容 validator。Add only the two broad rack position dataclasses and one union。`MoveRackRequest`/`RotateRackRequest` 在 `__post_init__`（或同等单一领域入口）执行上述封闭位置/模板矩阵，且把规范化模板和原始 face string 写入 dataclass，使 `asdict()`、`request_digest` 和幂等冲突自然消费同一冻结字段；不得在摘要前修改内容。Service、Debug API、SDK 和 Mock 不复制 face 内容规则。

- [ ] **Step 4: 更新唯一 submit 快照**

  `build_submit_data()` 的 rack family 固定输出；`MoveRackRequest` 使用 `request.target`，`RotateRackRequest` 使用 `request.position` 同时形成 `source` 和 `target`：

  ```python
  {
      "transport_task_id": transport_task_id,
      "kind": request.kind.value,
      "rcs_template_id": request.rcs_template_id.value,
      "rack_id": request.rack_id,
      "source": _json_value(request.source),
      "target": _json_value(request.target),
      "target_face": request.target_face,
  }
  ```

  `RACK_ROTATE` 分支把 `request.position` 分别写入 `source` 与 `target`，不增加第二种 Schema。

- [ ] **Step 5: 修正服务对宽泛目标的最终结果校验**

  - `RACK_POSITION` 目标：`SUCCEEDED` 必须与冻结精确地码相等。
  - `RACK`/`ZONE` 目标：结果必须为精确 `RACK_POSITION`，WES 不自行解析货架主数据或区域成员关系。
  - 所有 rack 成功结果：`arrival_face` 必须与冻结 string `target_face` 精确相等。
  - `rotate_rack`：可信当前位置和当前面仍在创建前失败关闭，目标面必须与当前面不同。

  在 `test_transport_outcome.py` / `test_transport_acceptance_edges.py` 增加参数化 callback 验收矩阵：精确 `RACK_POSITION` 目标只在最终地码相等时成功；`RACK` 与 `ZONE` 宽泛目标可接受任意具体 `RACK_POSITION`，但必须保持冻结 `rack_id` 与 `arrival_face` 精确一致。对 wrong exact location、wrong rack、wrong face、缺失 face、非具体 rack 最终位置和 `POSITION_UNKNOWN` 分别断言失败，原 task identity、Evidence 和资源围栏保持，不发布成功 outcome、不释放后续业务门禁，也不根据区域/库位名称推导关系。

- [ ] **Step 6: 运行领域与服务 GREEN**

  Run:

  ```bash
  uv run pytest tests/runtime/transport/test_transport_contracts.py tests/runtime/transport/test_transport_service.py tests/runtime/transport/test_transport_outcome.py tests/runtime/transport/test_transport_acceptance_edges.py tests/runtime/transport/test_transport_submit_fencing.py -q
  uv run pytest tests/contracts/wms_adapter/test_transport_wire_acceptance.py::test_rack_submit_data_uses_one_shared_wire_shape -q
  ```

  Expected: 所有测试通过；submit JSON 使用原始 string token 并始终携带模板；当前联调值 `"90"/"270"` 不发生类型或内容变化；省略与显式默认模板具有同一幂等语义，模板变化仍被识别为 payload 冲突；精确与宽泛目标 callback 各自按矩阵闭合，失败路径保留原身份和围栏。

- [ ] **Step 7: 提交边界**

  未取得 Commit 授权时停止在 `IMPLEMENTED - FOCUSED VERIFIED`。若用户另行授权，精确暂存本任务文件并使用：

  ```bash
  git commit -m "feat(transport): 对齐 0.3 领域合同"
  ```

---

### Task 2: 将面向事实改为普通字符串存储并增加新 migration

**Files:**

- Modify: `src/app/transport/models.py`
- Modify: `src/app/execution/models/position_projection.py`
- Modify: `src/app/execution/services/position_projection_service.py`
- Modify: `src/app/transport/service.py`
- Modify: `tests/support/transport_projections.py`
- Create: `migrations/versions/` 下由 `uv run alembic revision -m "Transport 面向扩为字符串 token"` 生成的唯一新 revision 文件
- Modify: `tests/architecture/test_migration_baseline_structure.py`
- Test: `tests/integration/transport/test_transport_schema.py`
- Test: `tests/integration/transport/test_transport_evidence_transaction.py`
- Test: `tests/integration/execution/test_execution_constraints.py`
- Test: `tests/integration/test_initial_schema_baseline_postgresql.py`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: Task 1 的普通 `str` face 字段。
- Produces: `TransportMember.arrival_face: str | None`、`PositionProjection.arrival_face: str | None`；数据库使用 `TEXT` 原样保存，不增加 face 内容 CHECK。Alembic 仍保持一个 root、一个 head、无 branch。

- [ ] **Step 1: 写 schema RED 测试**

  Add PostgreSQL assertions for both tables:

  ```text
  arrival_face TEXT NULL
  no face-specific CHECK constraint
  ```

  Add persistence cases for `NULL`、`"90"`、`"270"`、`"FACE@01"`、`"面-1"`、空白 string 和长 string，并验证读回值与写入值精确一致。另在 migration-only baseline fixture 中放入历史 `""`，证明扩宽 migration 原样保留且不转为 `NULL`；应用/JSON 领域测试仍必须拒绝新请求中的空字符串。数据库测试只证明 `NULL | TEXT` 存储，不增加 face-specific CHECK。

- [ ] **Step 2: 生成随机 revision**

  Run:

  ```bash
  uv run alembic revision -m "Transport 面向扩为字符串 token"
  ```

  Expected: 新 revision 的 `down_revision` 等于执行时现有 head；禁止手写 revision ID，禁止改写 Phase 11 初始 migration。

- [ ] **Step 3: 从真实基线扩宽字符串存储**

  Upgrade order:

  1. 在同一事务内锁定 `wes_runtime.transport_members` 和 `wes_biz.position_projections`；先断言当前列类型为 `VARCHAR(1)`，不得尝试删除基线中不存在的 face CHECK constraint。
  2. 把两列从 `VARCHAR(1)` 改为 `TEXT`；不运行 `UPDATE`，不翻译任何值，现存字符串保持原始内容。
  3. 不增加 face-specific CHECK constraint；不在 migration 复制 JSON、Unicode 或业务内容规则。

  联调执行可在此之前通过独立运维步骤清理/重建已确认的专用联调库；迁移测试本身仍覆盖历史 `""` 原样保留，以防实现者把联调清理许可误写成 migration 数据转换。任何目标身份或非生产属性不明确时停止，不触碰数据库。

  Downgrade 同样先锁表，并要求每个非空值长度不超过 1；满足时只缩回真实历史类型 `VARCHAR(1)`，不增加 A/B 闭集。存在 `"90"`、`"270"` 或任意其它多 code point string 时失败关闭。Upgrade/downgrade 均不执行内容翻译或历史数据修复。

- [ ] **Step 4: 更新模型、投影服务与测试 helper**

  生产路径和测试 helper 直接传递 `str | None`，仅在边界显式拒绝 `""`，不做 `str()` coercion、trim、case folding 或 Unicode normalization；从数据库重建 outcome 后必须得到相同 string 或 `None`。不得使用 `if face`、`member.get("arrival_face") if ...` 等 truthiness 分支把 `""` 静默折叠为 `None`。

- [ ] **Step 5: 放宽 Phase 11 结构测试为可持续迁移链合同**

  Replace “仓库只能有一个 revision” with:

  ```text
  exactly one revision has down_revision=None
  exactly one Alembic head exists
  every non-root revision is reachable from that root
  the immutable Phase 11 root remains f9c7c2e5f501
  ```

  `test_initial_schema_baseline_postgresql.py` 继续单独证明 root baseline；新增 full `base → head` fresh PostgreSQL 证据承接当前 schema。

- [ ] **Step 6: 更新 HEAVY mapping**

  为新 migration 增加精确 mapping，至少选择 migration chain、Transport schema、Evidence transaction 和 execution projection PostgreSQL owners；不使用空映射。

- [ ] **Step 7: 运行 migration GREEN**

  Run:

  ```bash
  uv run pytest tests/architecture/test_migration_baseline_structure.py -q
  uv run pytest tests/integration/transport/test_transport_schema.py tests/integration/transport/test_transport_evidence_transaction.py tests/integration/execution/test_execution_constraints.py tests/integration/test_initial_schema_baseline_postgresql.py -q
  uv run alembic heads
  ```

  Expected: 一个 head；空库 fresh `base → head` 通过；upgrade fixture 中已有 `""` 和单 code point 字符串均原样保留，升级后 `NULL`、`"90"`、`"270"`、`"FACE@01"`、`"面-1"`、空白和长 string 可写入并精确读回；应用边界仍拒绝新增 `""`，且不存在 face-specific CHECK。Downgrade 遇到任意多 code point 非空值时明确失败，`NULL`、`""` 和单 code point 值可无损恢复到真实基线。

- [ ] **Step 8: 提交边界**

  未取得 Commit 授权时不提交。若另行授权：

  ```bash
  git commit -m "feat(transport): 持久化字符串面向 token"
  ```

---

### Task 3: 对齐 WMS wire、OpenAPI、Mock 与调试 API

**Files:**

- Modify: `src/app/wms_adapter/transport_wire.py`
- Modify: `src/app/wms_adapter/transport_openapi.py`
- Modify: `src/app/transport/v1/tasks.py`
- Modify: `docs/contracts/openapi/wes-wms-transport.openapi.json`
- Modify: `docs/contracts/transport-fulfillment-contract.md`
- Modify: `docs/superpowers/specs/2026-08-14-wes-wms-transport-dto-design.md`
- Modify: `docs/integration/wes-wms-interface-requirements.md`
- Modify: `tests/mock/wms_transport_mock_openapi.py`
- Modify: `tests/mock/wms_mock_server.py`
- Modify: `scripts/verify_wms_northbound_feasibility.py`
- Test: `tests/contracts/wms_adapter/test_transport_wire_acceptance.py`
- Test: `tests/contracts/wms_adapter/test_transport_openapi.py`
- Test: `tests/contracts/wms_adapter/test_transport_adapter.py`
- Test: `tests/contracts/wms_adapter/test_transport_adapter_qa_regressions.py`
- Test: `tests/mock/test_wms_transport_mock_server.py`
- Test: `tests/api/test_transport_tasks.py`
- Test: `tests/integration/test_wms_northbound_feasibility_probe.py`

**Interfaces:**

- Consumes: Task 1 domain values and Task 2 persistence representation。
- Produces: callback parser、OpenAPI 3.0.3、Mock、debug API 和人类合同都把 face 表达为可按上下文 nullable、提供时非空且不解释内容的 JSON string；submit rack family 使用统一 0.3 shape；Mock T1/T2/T3 证据边界与生产 Transport 一致。

- [ ] **Step 1: 写 callback/OpenAPI RED 测试**

  Assert:

  ```python
  validated = validate_callback_envelope(rack_success_with(arrival_face="90"))
  assert validated["data"]["arrival_face"] == "90"
  ```

  同时接受 `"270"`、`"FACE@01"`、`"面-1"`、空白 string 和长 string，且精确保留内容；按上下文接受 `None`。Reject `""`、JSON number `90/270`、`True` 等非法值。OpenAPI 对非 nullable 的 `rack_face/target_face/arrival_face` 使用：

  ```json
  {"type": "string", "minLength": 1, "description": "Opaque non-empty face value; preserve exactly"}
  ```

  nullable 上下文按 OpenAPI 3.0.3 增加 `nullable: true`；不声明 `enum`、`pattern`、`maxLength` 或数值格式，不暗示角度/A-B 语义，也不增加非空之外的 face 内容规则。

- [ ] **Step 2: 实施严格 wire 解析**

  `transport_wire.py` 的 face 分支显式检查 `type(value) is str and value != ""` 并返回原值；nullable 字段先精确处理 `None`。不调用会拒绝空白的 `_nonblank()`，不新增 face helper，不 trim、不 normalize、不 casefold，也不把 `"90"/"270"` 转换为整数。API/Pydantic face 字段使用 strict `str` / `str | None` 和 `min_length=1`，禁止把 number/bool coercion 为 string，但不校验非空之外的 string 内容。

- [ ] **Step 3: 更新 OpenAPI builder 和权威 JSON artifact**

  修改共享 builder 后，用 `apply_patch` 同步权威 JSON，使：

  ```bash
  uv run pytest tests/contracts/wms_adapter/test_transport_openapi.py::test_standalone_transport_openapi_303_artifact_is_generated_from_the_shared_builder -q
  ```

  精确通过。把测试名中的 `v02` 更新为 `v03`，并断言 face schema 不再包含 A/B enum 或 integer/number 类型，且 request/callback 示例保留 JSON string token。

  同步三份人类合同，删除旧 `1..64`、空白/BOM/控制字符和专用 Unicode lexical 约束；只保留“按上下文可为 `null`，提供时为非空 JSON string，并原样传递、精确相等”。同时把模板缺省规则改为 `move_rack → F01`、`rotate_rack → CTU02`。公共 HTTP Body 的 UTF-8/JSON 语法规则仍属于整体信封，不改写为 face 业务 validator。

- [ ] **Step 4: 更新 Debug API 和示例**

  `_RackMoveData` 支持 discriminator union `RACK | ZONE | RACK_POSITION`，增加可选 `rcs_template_id` 并在 service 调用前规范化为 `F01`；`_RackRotateData` 仍只接受精确位置，增加可选 `rcs_template_id` 并规范化为 `CTU02`。显式传入的模板仍须通过封闭矩阵。请求和响应中的 face 字段直接使用非空 strict `str` / `str | None`，不把 `""` 转为 `None`。

- [ ] **Step 5: 保持 Mock T1 身份与 ACK 语义**

  `tests/mock/wms_mock_server.py` 的公开入口保持 `POST /api/v1/wes/transport-requests`、auth `NONE`。T1 identity 固定为 `(operation, operation_id)`：

  ```text
  same complete message       -> 200 / DUPLICATE
  same identity, new payload  -> 409 / CONFLICT
  valid first submission      -> 202 / RECEIVED
  ```

  Invalid `transport_task_id` 不回显非法值；Mock 必须验证 `rcs_template_id`、不透明 string 面向和 rack 位置联合。它不接入 WES 数据库、TransportTask、库存、排程或 RCS 私有动作。

- [ ] **Step 6: 用 Approved 10 场景矩阵驱动参数化合同测试**

  在 `test_transport_wire_acceptance.py` 和 `test_wms_transport_mock_server.py` 复用一组明确 fixture，覆盖：

  | # | Kind | Template | Source | Target |
  | --- | --- | --- | --- | --- |
  | 1 | `RACK_MOVE` | `CTU01` | `ZONE` | `RACK_POSITION` |
  | 2 | `RACK_MOVE` | `CTU01` | `RACK` | `RACK_POSITION` |
  | 3 | `RACK_MOVE` | `CTU01` | `RACK_POSITION` | `RACK_POSITION` |
  | 4 | `RACK_ROTATE` | `CTU02` | `RACK_POSITION` | 同一 `RACK_POSITION` |
  | 5 | `RACK_MOVE` | `CTU03` | `RACK_POSITION` | `RACK` |
  | 6 | `RACK_MOVE` | `CTU03` | `RACK_POSITION` | `ZONE` |
  | 7 | `RACK_MOVE` | `CTU03` | `RACK_POSITION` | `RACK_POSITION` |
  | 8 | `RACK_MOVE` | `F01` | `RACK_POSITION` | `RACK_POSITION` |
  | 9 | `BIN_MOVE` | none | `RACK_BIN_SLOT/HANDOFF_POSITION` | 对应目标 |
  | 10 | `BIN_EXCHANGE` | none | 2 或 4 个闭环成员 | 对应目标 |

  当前 WMS/RCS 联调 happy path 的货架 face 使用 JSON string `"90"` 或 `"270"`。每个货架成功 callback 必须返回精确 `RACK_POSITION/location_code` 且 `arrival_face == target_face`；另用 `"FACE@01"`、`"面-1"`、空白和长 string 覆盖普通非空 string 的透传合同，并用 `""` 证明边界稳定拒绝。接口文档样例 3/9/10 与 Task 4A 共用一组类型化 510056 canonical fixture：rack `510056`、bins `A000001922/A000002653`、slots `510056A3F2C101/510056A2F2C101`、`ZONE("WH01")`、`RACK_POSITION("KT16")`、handoffs `CNV0301/CNV0302`、SCAN9→12、进站 `rcs_template_id="CTU01"`、回库 `rcs_template_id="CTU03"`，以及显式 `target_face/rack_face="90"`。进站 callback 的 exact target 必须为 `RACK_POSITION("KT16")`；回库请求的 target 是宽泛 `ZONE("WH01")`，callback 必须返回 WMS/RCS 选定的实际 `RACK_POSITION`，WES 不把 `WH01` 当作精确点位，也不自行推导区域成员关系。该 fixture 是新的 WIRE/步进器共同输入，不从旧 A/B 数据换算，也不从 `station_id` 推导 template。对应 callback fixture 标为“合同预期数据”，不能声称是现场回调抓包。

  另保留 3 类失败结果：已知失败位置、`POSITION_UNKNOWN`、成员或冻结目标不匹配。文档中的 JSON 块继续作为人类合同样例；测试用类型化 fixture 证明相同场景，不在运行时解析 Markdown。

  为 approved matrix 增加独立的负例参数表，覆盖所有未批准的位置边、模板错配和 `CTU02 + RACK_MOVE`；断言在领域边界失败，且 WMS Mock 的防御性校验对直接非法 wire payload 返回稳定 4xx。

- [ ] **Step 7: 验证 T2/T3 证据边界**

  - T2 `TARGET_PLACED` 只在存在权威逐容器位置事实时使用；当前 CTU/RCS 不因 Mock 能力而自动启用。
  - T3 callback 的 `200/202` 只证明接收/持久化，不证明冻结成员、目标、面向、revision 和业务释放条件已经校验通过。
  - 所有四种 kind 都要覆盖重复 identity、跨 operation 相同 UUID、成员数量和有序结果；`BIN_EXCHANGE` 保持一个协调任务，不暴露 `exchange_pairs` 或 left/right wire 角色。
  - `verify_wms_northbound_feasibility.py` 的四种 probe payload 同步改为 0.3；ACK matching 继续要求 JSON UTF-8 Content-Type、identity Content-Encoding 和 signed Int64 timestamp，不能因 DTO 更新放宽 HTTP 边界。

- [ ] **Step 8: 运行 WMS 合同 GREEN**

  Run:

  ```bash
  uv run pytest tests/contracts/wms_adapter tests/mock tests/api/test_transport_tasks.py tests/integration/test_wms_northbound_feasibility_probe.py -q
  ```

  Expected: builder、权威 JSON、Mock、runtime route 和 wire acceptance 全部一致。

- [ ] **Step 9: 提交边界**

  未取得 Commit 授权时不提交。若另行授权：

  ```bash
  git commit -m "feat(wms): 对齐 Transport 0.3 机器合同"
  ```

---

### Task 4: 传播 Transport 0.3 到 SDK 与 Phase 8 粗分换架链

**Files:**

- Modify: `packages/wes_plugin_sdk/src/wes_plugin_sdk/decisions.py`
- Modify: `packages/wes_plugin_sdk/src/wes_plugin_sdk/__init__.py`
- Modify: `src/app/wms_adapter/inbound_wire.py`
- Modify: `src/app/execution/services/decision_applier.py`
- Modify: `deployment/_rough_sorter_values.py`
- Modify: `deployment/_rough_sorter_transport_recovery_facts.py`
- Modify: `workline_plugins/rough_sorter/src/rough_sorter/facts.py`
- Modify: `workline_plugins/rough_sorter/src/rough_sorter/handlers/replacement_plan_decided.py`
- Create: `tests/runtime/execution/test_plugin_sdk_transport_contract.py`
- Test: `tests/runtime/execution/test_decision_applier.py`
- Test: `tests/contracts/wms_adapter/test_inbound_wire_acceptance.py`
- Test: `tests/deployment/test_rough_sorter_plugin_startup.py`
- Test: `workline_plugins/rough_sorter/tests/test_placement_and_replacement.py`
- Test: `workline_plugins/rough_sorter/tests/test_transport_and_recovery.py`

**Interfaces:**

- Consumes: WMS replacement plan `rack_id + source + target + target_face`；模板由 WES 按 leg 冻结。
- Produces: `OLD_OUT → CTU03`、`NEW_IN → CTU01`；SDK source/target 位置联合与 core Transport 位置联合一一映射；插件不产生通用模板 registry。

- [ ] **Step 1: 写 SDK 与 replacement RED 测试**

  Cover:

  ```text
  old_loaded_rack target may be RACK or ZONE
  new_empty_rack source may be RACK or ZONE
  current joint target_face values are exact strings "90"/"270"
  arbitrary strings such as "FACE@01" and "面-1" round-trip unchanged
  OLD_OUT CreateTransportTask freezes CTU03
  NEW_IN CreateTransportTask freezes CTU01
  recovery reconstructs exactly the same frozen MoveRackRequest
  NEW_IN matching success may request the next target without waiting for OLD_OUT
  OLD_OUT failure or unknown cannot enter the material decision lane
  NEW_IN failure, unknown, wrong rack, wrong exact target, or wrong face blocks target creation
  two rack moves remain independent tasks; no RACK_EXCHANGE or plugin-side physical ordering
  ```

- [ ] **Step 2: 更新 SDK 的最小传输值对象**

  SDK 删除 `RackFace` enum/class，`CreateTransportTask.target_face` 和相关 facts 直接使用 `str` / `str | None`，只做运行时 nullable、string 类型和非空检查，不解释内容。SDK 保留与 core 对等但不依赖 core 的 `TransportRcsTemplateId(StrEnum)`、`TransportRackReference`、`TransportZonePosition`、`TransportRackPosition` 和 union；`CreateTransportTask` 显式携带模板，不根据位置编码或 face 内容推断。

- [ ] **Step 3: 更新 WMS replacement response parser**

  `RackMovePlan.source/target` 使用 discriminator union；`rack_move_plan()` 同时接受线上字段 `kind`，禁止继续只接受旧 `type=RACK_POSITION`。`RACK.location_code` 必须等于外层 `rack_id`。

- [ ] **Step 4: 更新插件 decision 与 core DecisionApplier**

  Handler 创建两腿时显式传入：

  ```python
  OLD_OUT: TransportRcsTemplateId.CTU03
  NEW_IN: TransportRcsTemplateId.CTU01
  ```

  `DecisionApplier` 只做 SDK→core 类型映射，随后调用现有 `move_rack_in_session()`；不绕过 Service 或直接访问 Transport Repository。

- [ ] **Step 5: 更新 recovery 和成功结果校验**

  Recovery 重建请求时包含模板、原始 string face token 和原位置 kind。对 `NEW_IN`：冻结目标为 `RACK_POSITION` 时要求最终地码相等；冻结目标为 `RACK/ZONE` 时只要求 WMS 返回精确 `RACK_POSITION`、实际 `rack_id` 匹配且 `arrival_face` 与冻结 token 精确相等，不在 WES 推导区域/库位关系或面语义。

  `TransportOutcomePublishedHandler` 必须继续区分两条腿：`OLD_OUT` 结果不进入 material target lane；`NEW_IN` 只有身份、最终位置规则和到达面全部闭合后才能重新发起 `inbound.material.target_decide@v1`。这组行为由现有 `test_placement_and_replacement.py` 和 `test_transport_and_recovery.py` 承接，不新建第二套粗分状态机。

- [ ] **Step 6: 一次完成机械传播和旧值残留扫描**

  Update all listed fixtures with exact raw string `"90"`/`"270"` values declared by the current WMS/RCS cases，并加入 `None`、非数字、空白和长 string 透传测试以及空字符串拒绝测试；禁止根据旧 A/B fixture 推导或转换新值。审计所有 face 提取路径，特别是 `member.get("arrival_face")` truthiness 和 `required_string(...face)`：前者不得把 `""` 折叠为 `None`，后者不得误拒绝空白 string。Then run:

  ```bash
  rg -n '\bRackFace\b|CoreRackFace|(target_face|rack_face|arrival_face)\.value|FACE_(90|270)|Literal\["A", "B"\]|\{"A", "B"\}' src packages deployment scripts tests workline_plugins
  rg -n '"(rack_face|target_face|arrival_face)"[[:space:]]*:[[:space:]]*(90|270)([^0-9]|$)' src packages deployment scripts tests workline_plugins docs/contracts/openapi
  rg -n 'required_string\([^\n]*(target_face|rack_face|arrival_face)|if [^:\n]*(target_face|rack_face|arrival_face)|\.get\("(target_face|rack_face|arrival_face)"\)[^\n]*(if|else)' src packages deployment scripts workline_plugins
  ```

  Expected: no `RackFace` type、old face enum、numeric JSON face、face normalization/mapping helper，且没有 truthiness/nonblank 路径把 `""` 当作 `None` 或把合法空白 string 拒绝；空字符串只在明确的非空边界稳定失败。station names such as `STATION_A` are outside this replacement and remain unchanged。

- [ ] **Step 7: 运行 SDK、deployment 与插件 GREEN**

  Run:

  ```bash
  uv run pytest tests/runtime/execution/test_plugin_sdk_transport_contract.py tests/runtime/execution/test_decision_applier.py tests/deployment -q
  cd workline_plugins/rough_sorter && uv run pytest tests --ignore=tests/e2e -q
  cd workline_plugins/rough_sorter && uv run ruff format --check . && uv run ruff check . && uv run basedpyright
  ```

  Expected: core/SDK 类型一致；插件非 E2E 与静态检查全绿；两腿独立性、NEW_IN 放行门禁和 OLD_OUT 隔离边界均有聚焦证据。

- [ ] **Step 8: 提交边界**

  未取得 Commit 授权时不提交。若另行授权：

  ```bash
  git commit -m "feat(rough-sorter): 消费 Transport 0.3 换架合同"
  ```

---

### Task 4A: 收敛 510056 联调步进到 Transport 0.3

**Files:**

- Modify: `src/app/transport/service.py`
- Modify: `src/app/transport/v1/tasks.py`
- Test: `tests/runtime/transport/test_transport_acceptance_edges.py`
- Test: `tests/api/test_transport_tasks.py`
- Test: `tests/integration/transport/test_transport_debug_reset.py`
- Inspect/Modify if mapping changes: `docs/architecture/heavy-test-impact.toml`
- Frontend Modify: `../wes_frontend/src/views/ops/transport-diagnostics/useTransportDebugLoop.ts`
- Frontend Modify: `../wes_frontend/src/views/ops/transport-diagnostics/TransportDebugLoopDialog.vue`
- Frontend Modify only as generated by canonical sync: `../wes_frontend/contracts/openapi.current.json`、`../wes_frontend/contracts/permissions.current.json`、`../wes_frontend/.contract-sync-record.json`、`../wes_frontend/.permission-sync-record.json`、`../wes_frontend/src/api/generated/`
- Frontend Test: `../wes_frontend/tests/unit/views/ops/transport-diagnostics/useTransportDebugLoop.test.ts`
- Frontend Test: `../wes_frontend/tests/unit/views/ops/transport-diagnostics/TransportDiagnosticsPage.test.ts`
- Frontend Test: `../wes_frontend/tests/unit/views/ops/transport-diagnostics/useTransportDiagnostics.test.ts`

**Interfaces:**

- Consumes: Task 3 的 canonical OpenAPI/WIRE、既有 `TRANSPORT_DEBUG` 专用 BIN_MOVE、operator confirmation + atomic audit/reset，以及已部署但尚未按 0.3 对齐的五步 510056 stepper。
- Produces: 同一五步 UI 直接下发 Transport 0.3 payload；`target_face/rack_face` 是显式冻结的普通 string，当前 510056 fixture 使用 `"90"`；两条 rack leg 的 `rcs_template_id` 分别显式冻结并下发 `"CTU01"` / `"CTU03"`。确认、审计和 reset 对冻结请求做精确一致性检查，但不写业务 `PositionProjection`、不伪造 callback。

- [ ] **Step 1: 冻结唯一 510056 canonical fixture 与五步边界**

  Backend 合同样例、API tests 和 frontend stepper 共用以下数据，不从旧 `A` 计算任何值：

  | Step | Request / existing flow | Frozen data |
  | --- | --- | --- |
  | `RACK_TO_STATION` | `RACK_MOVE` | `510056: ZONE("WH01") → RACK_POSITION("KT16"), target_face="90", rcs_template_id="CTU01"` |
  | `BINS_TO_INFEED` | `BIN_MOVE` | 两个原储位 `rack_face="90"` → `CNV0301`；不携带 `rcs_template_id` |
  | `CONVEYOR_TO_OUTFEED` | existing ECS `is_debug:true` | `SCAN9 → SCAN10 → SCAN11 → SCAN12 → CNV0302`；不创建 TransportTask，不携带 `rcs_template_id` |
  | `BINS_TO_RACK` | `BIN_MOVE` | `CNV0302` → 两个原储位 `rack_face="90"`；不携带 `rcs_template_id` |
  | `RACK_TO_STORAGE` | `RACK_MOVE` | `510056: RACK_POSITION("KT16") → ZONE("WH01"), target_face="90", rcs_template_id="CTU03"` |

  两个容器/储位固定为 `A000001922 → 510056A3F2C101`、`A000002653 → 510056A2F2C101`。这里储位编码里的 `A` 只是现场标识的一部分，不能被读取、删除或转换为 face 语义。`"270"` 继续由 Task 1/3 的 WIRE 参数化合同测试证明可原样收发；510056 fixture 不为覆盖数值而擅自改变现场姿态。`rcs_template_id` 是独立 WIRE 字段：不能从 `station_id="CTU01"`、face、位置编码或步骤名称推导，也不能把缺失值默认成 `F01` 后仍宣称 510056 payload 正确。

- [ ] **Step 2: 写 backend debug consumer RED 测试**

  在现有 owner 中覆盖：

  - 四个 Transport 步骤创建的冻结 request 与 submit body 精确保留 `"90"`；audit `frozen_targets` 继续记录当前目标，其中 rack move/rack-slot target 的 face 原样为 `"90"`；JSON 中不得出现 face `"A"`、number `90` 或转换后的值；
  - `RACK_MOVE` 进站腿的 debug API body、领域冻结 request 和 WMS submit body 都精确包含 `rcs_template_id="CTU01"`；回库腿三处都精确包含 `rcs_template_id="CTU03"`，不能省略、互换或落入默认 `F01`；
  - 对 rack `510056` 的 canonical `ZONE("WH01") → RACK_POSITION("KT16")` / `RACK_POSITION("KT16") → ZONE("WH01")` route，缺少或错传 template 在创建 task/outbox、调用 WMS 前返回 400；若把 `WH01` 错传为 `RACK_POSITION`，同样在创建前失败；其它 debug route 继续服从 Task 1/3 的通用模板矩阵，不建立全局 route-template mapping；
  - 两个 `BIN_MOVE` body 不出现 `rcs_template_id`，每个 rack slot 携带同一冻结 `rack_face="90"`；SCAN step 不创建 Transport payload；
  - `station_id="CTU01"` 只保留 caller/station 身份，测试必须证明它没有代替、生成或覆盖 `rcs_template_id`；
  - operator confirmation 只在 task kind、rack/bin identity、source/target 的 `kind + location_code`、slot/handoff、face 和 rack leg 的 `rcs_template_id` 与当前 step 的 canonical fixture 全部精确匹配时进入同事务 audit/reset；任一项不匹配返回 400，task/evidence/binding 不删除；
  - audit 继续记录 `source=OPERATOR_DEBUG`、`business_authoritative=false`、`transport_task_id`、step、assertion 和 frozen targets；不新增业务 projection；
  - `move_bins_for_debug()` 仍只允许 `TRANSPORT_DEBUG` 使用请求内冻结 face；正式 `move_bins()` 继续依赖可信业务投影并 fail closed。

  不新增 face validator/helper。上述测试直接消费 Tasks 1/3 已建立的 `str | None`、提供时非空边界和精确相等语义。

- [ ] **Step 3: 最小修改 backend debug 匹配与 API**

  复用现有 `/api/v1/transport/debug-tasks`、`reset-preview` 和 `reset`；只把 Task 3 已建立的普通 string/模板/位置类型传播到 debug DTO 和 dispatch。Debug API 必须把显式 `rcs_template_id` 原样传给 `move_rack()` 并进入 request digest/冻结 submit body；在现有 debug dispatch 中对 510056 两条固定 route 做直接 equality 检查，缺失/错值立即失败，不抽取 validator 或映射 abstraction。收紧 `_debug_step_matches_frozen_request()` 的 510056 专用比对，使它覆盖 Step 2 列出的完整冻结数据、face exact equality 和 rack leg template exact equality；不新增 endpoint、表、后台 loop、timer、callback relay、临时投影或兼容解析。

  WMS submit/ACK 不等于物理完成。创建、reset、审计或 ECS 调试步骤失败时停止当前 step，不自动重试或换 `transport_task_id`；只有操作员实际确认当前物理目标已达到，才调用现有 confirmation + atomic reset 继续。无法确认时保留原 identity/evidence/fence，进入既有诊断/对账，不盲目清理。

  Backend Run:

  ```bash
  uv run pytest tests/runtime/transport/test_transport_acceptance_edges.py tests/api/test_transport_tasks.py -q
  uv run scripts/select_heavy_tests.py --scope unstaged
  ```

  Expected: backend debug/API focused tests 通过并证明新语义/WIRE 可表达完整 510056 请求。`tests/integration/transport/test_transport_debug_reset.py` 由 HEAVY mapping 拥有；此处只确认 selector manifest，真实 PostgreSQL owner 在 Task 7 的最终 backend 快照执行，不用 skip 代替通过。

- [ ] **Step 4: Backend Land 后同步 frontend canonical contract 并替换旧 A/B**

  本步骤延后到 Task 7 Step 7A：backend Tasks 1–4A 已通过最终门禁、Commit/Push/PR/Merge 分别获授权，且含新 0.3/debug API 的 Commit 已进入 clean backend `develop` 后执行。在 frontend 主仓库先冻结 dirty/base并重新确认 PR #87 状态；从最新 frontend `origin/develop` 创建独立 `codex/feat-transport-03-alignment` worktree。任何同名 branch/worktree 或 dirty 冲突都停止，不覆盖。Run:

  ```bash
  cd /Users/kaizhou/codeDev/wes_frontend
  git fetch origin develop
  ./scripts/git-worktree.sh add codex/feat-transport-03-alignment
  cd /Users/kaizhou/codeDev/wes_frontend-worktrees/codex-feat-transport-03-alignment
  git status --short
  pnpm contract:freeze -- --backend-root /Users/kaizhou/codeDev/wes_backend
  pnpm generate:types
  pnpm generate:zod
  pnpm contract:verify
  ```

  `useTransportDebugLoop.ts` 删除 `RACK_FACE = 'A'`，直接冻结 canonical `RACK_FACE = '90'`。`RACK_TO_STATION` 精确发送 `source={kind:'ZONE', location_code:'WH01'}`、`target={kind:'RACK_POSITION', location_code:'KT16'}` 和 `rcs_template_id:'CTU01'`；`RACK_TO_STORAGE` 精确发送相反位置联合与 `rcs_template_id:'CTU03'`。两个 BIN step 不发送 template。`station_id:'CTU01'` 继续只是 caller/station 数据，不能当作 rack template。Dialog 中删除“A 面朝向”文案，改为仅显示本次冻结 face string、rack template 和物理目标。不得增加 A/B 映射、角度文案、face selector、template 映射、registry、validator 或第二套 API 类型；当前固定联调步进不需要新的配置系统。

- [ ] **Step 5: 验证五步编排和停止条件**

  Frontend tests 精确断言四个创建请求：进站 rack request 为 `ZONE("WH01") → RACK_POSITION("KT16") + CTU01`，回库 rack request 为 `RACK_POSITION("KT16") → ZONE("WH01") + CTU03`；两个 BIN request 不带 template，四个请求的 face 均按各自位置使用 `"90"` string。SCAN step 不创建 TransportTask；每个物理 Transport step 必须先 confirmation/reset 才能前进；确认、reset 或下一任务创建失败时保持可恢复状态且不重复确认。完整一轮后只标记完成，必须由操作员再次点击才开始下一轮，不新增无人值守自动循环。

  Frontend Run:

  ```bash
  pnpm test -- tests/unit/views/ops/transport-diagnostics/useTransportDebugLoop.test.ts tests/unit/views/ops/transport-diagnostics/TransportDiagnosticsPage.test.ts tests/unit/views/ops/transport-diagnostics/useTransportDiagnostics.test.ts
  pnpm contract:test
  pnpm contract:verify
  pnpm lint
  pnpm build
  ```

  Expected: frontend component/contract evidence证明新语义与 WIRE 能驱动完整 510056 步进；同一 canonical input 再生成无差异。未触发真实设备，不把测试结果写成现场联调通过。

- [ ] **Step 6: 两阶段提交边界**

  Backend Steps 1–3 随 Tasks 1–4 进入 backend 候选，未取得 Commit/Push/PR/Merge 各自授权时不前进到 Step 4。Backend 合入 `develop` 后才执行 frontend Steps 4–5；frontend 未取得独立 Commit 授权时不提交。两个仓库各自只暂存授权范围；建议 Commit 分别为：

  ```text
  feat(transport): 对齐 510056 联调字符串面向
  feat(transport): 消费 510056 Transport 0.3 合同
  ```

  任一仓库的 Commit、Push、PR、Merge 或 Deploy 均不授权另一个仓库的对应动作；真实 510056 物理运行另行授权。

---

### Task 5: 修复 ECS ACK Approved 合同内部矛盾

**Files:**

- Modify: `docs/integration/third_party_integration_whitepaper.md`

**Interfaces:**

- Consumes: 当前 `_classify_submit_result()` 行为和 `tests/contracts/device/test_uniform_ecs_wire.py`。
- Produces: 一个自洽规则：HTTP 200 ACK 必须满足 `code=200`、`message="ACK"` 和合法可选 `trace_id`；未知额外顶层字段被忽略，不改变 ACK 分类。

- [ ] **Step 1: 修改相反的失败条件**

  删除“任何额外字段均进入人工对账”的句子，改为：未知额外字段不参与 ACK 判定；已知字段若存在仍必须满足类型和值约束。

- [ ] **Step 2: 保留现场不确定性边界**

  明确 `{"code":200,"message":"OK","data":null}` 仍为 `RECONCILING`，原因是 `message != "ACK"`，不是因为存在 `data`。HTTP 200 或 JSON 可解析不代表物理完成。

- [ ] **Step 3: 文档相称验证**

  Run:

  ```bash
  ./scripts/markdownlint.sh docs/integration/third_party_integration_whitepaper.md
  rg -n '额外字段|未知字段|message.*ACK|message.*OK|RECONCILING' docs/integration/third_party_integration_whitepaper.md
  git diff --check
  ```

  Expected: 同章节不再同时出现“忽略额外字段”和“额外字段必然对账”两条相反规则。纯文档变更不新增 pytest。

- [ ] **Step 4: 提交边界**

  未取得 Commit 授权时不提交。若另行授权：

  ```bash
  git commit -m "docs(ecs): 统一 ACK 扩展字段规则"
  ```

---

### Task 6: 刷新 Phase 1–11 活动状态真源

**Files:**

- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- Modify: `docs/superpowers/plans/2026-08-03-wes-legacy-production-path-removal.md`
- Modify only if references change: `docs/architecture/file_index.md`

**Interfaces:**

- Consumes: GitHub merge事实、backend/frontend 当前 develop、Tasks 1–4、Task 4A backend Steps 1–3 与 Task 5 实际结果。
- Produces: 一组不互相矛盾的当前状态，不把仓内实现、Merge、Deploy、供应商或业务验收混为一体。

- [ ] **Step 1: 修正已确认的历史事实**

  Record exactly:

  ```text
  Phase 10 PR #187 merged to develop@97e6887a; merge commit not proven redeployed.
  Phase 11 PR #188 and #189 completed; baseline/cleanup reached develop@d458383a.
  Phase 11 status: MERGED — NOT DEPLOYED; old dd35f04b258f database cannot upgrade in place.
  Supplier/device physical/business acceptance: NOT RUN.
  ```

  Remove `NOT PUSHED / NO PR / NOT MERGED` and “Phase 11 待准入” from active current-state sections, while preserving historical task narrative where explicitly labeled as historical。

- [ ] **Step 2: 记录本修复的准确状态**

  Only after Tasks 1–4、Task 4A backend Steps 1–3 and Task 5 pass, record:

  ```text
  Transport 0.3 repository implementation: IMPLEMENTED — FINAL VALIDATION PENDING
  WMS external publication: pending
  Current-head immutable Phase 8 E2E: pending until Task 7 passes
  RACK_MOVE current production caller: rough-sorter OLD_OUT/NEW_IN only
  RACK_MOVE current debug caller: 510056 operator-gated stepper; backend aligned, frontend canonical sync pending backend Land
  Other approved RACK_MOVE scenarios: core contract supported, business flow not integrated
  Deployment/supplier/physical/business acceptance: NOT RUN
  ```

  上述步骤通过只证明 backend 目标实现和聚焦验证完成，不得在任何活动状态文档中写 `ALIGNED`。510056 必须明确 frontend canonical sync 尚待 backend Land，不能写成仓内双端对齐或现场循环已验收。Do not set `published_at` to a date and do not call the contract externally accepted without WMS joint review。

- [ ] **Step 3: 扫描所有活动交叉引用**

  Run:

  ```bash
  rg -n 'NOT PUSHED|NO PR|NOT MERGED|尚未合入|待准入|Phase 11.*未开始|Phase11.*未开始' README.md docs --glob '!docs/hardware/**'
  rg -n 'Phase 10|Phase 11|Transport 0.3|供应商|物理|业务验收' README.md docs/architecture/file_index.md docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md docs/superpowers/plans/2026-08-03-wes-legacy-production-path-removal.md
  ```

  Expected: remaining old-status hits are either corrected or explicitly marked as historical snapshot。

- [ ] **Step 4: 文档相称验证**

  Run:

  ```bash
  ./scripts/markdownlint.sh README.md docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md docs/superpowers/plans/2026-08-03-wes-legacy-production-path-removal.md docs/architecture/file_index.md
  git diff --check
  ```

- [ ] **Step 5: 提交边界**

  未取得 Commit 授权时不提交。若另行授权：

  ```bash
  git commit -m "docs(phases): 同步 Phase 10 与 Phase 11 当前状态"
  ```

---

### Task 7: 构建当前不可变制品并执行最终验收门禁

**Files:**

- Modify after evidence exists: `docs/integration/rough-sorter-joint-acceptance.md`
- Modify after all alignment passes: `docs/contracts/transport-fulfillment-contract.md`
- Modify after all alignment passes: `docs/superpowers/specs/2026-08-14-wes-wms-transport-dto-design.md`
- Modify after all alignment passes: `docs/integration/wes-wms-interface-requirements.md`
- Modify after all alignment passes: `docs/superpowers/specs/2026-08-26-transport-integration-diagnostics-design.md`
- Inspect for consistency: `docs/contracts/wms-rough-sorter-inbound-integration-requirements.md`
- Inspect for consistency: `docs/architecture/SRS.md`
- Modify/Test: `workline_plugins/rough_sorter/tests/e2e/test_business_loop.py`
- Modify if the expanded flow needs seed data: `workline_plugins/rough_sorter/fixtures/business-loop-seed.sql`
- Evidence only: Docker image labels、test logs、selector manifest、migration evidence、frontend Commit/tree/OpenAPI fingerprint

**Interfaces:**

- Consumes: backend Tasks 1–6（含 Task 4A backend Steps 1–3）的最终可执行树，以及用户对 backend 候选 Commit 的独立授权；没有 Commit 授权时 Track C 不启动镜像/E2E。
- Produces: 先绑定 backend HEAD/tree 的 QUALITY、selected HEAVY、migration 和 Phase 8 E2E；backend Land 另行授权后，再完成并绑定 frontend Task 4A HEAD/tree/OpenAPI fingerprint，最后形成合同状态证据。最终仍不代表 Deploy 或现场业务验收。

- [ ] **Step 1: 固定最终 working-tree 快照并运行聚焦回归**

  Run:

  ```bash
  git status --short
  git diff --check
  uv run pytest tests/runtime/transport tests/contracts/wms_adapter tests/runtime/execution/test_decision_applier.py tests/deployment -q
  cd workline_plugins/rough_sorter && uv run pytest tests --ignore=tests/e2e -q
  ```

  Expected: 全部通过；测试 owner、fixture、Mock 和插件消费者闭合。

  Additionally verify the Approved scenario inventory without overstating consumers:

  ```bash
  rg -n 'move_rack\(|move_rack_in_session\(' src deployment workline_plugins --glob '*.py'
  rg -n 'CTU01|CTU02|CTU03|F01|RACK_POSITION|ZONE|"RACK"' tests/contracts/wms_adapter tests/mock workline_plugins/rough_sorter/tests --glob '*.py'
  ```

  Expected: 生产业务调用点仍能精确归属到粗分换架；10 场景矩阵由 core/ACL/Mock tests 覆盖，但未出现凭测试声明自动补充货架或五层货架业务已经接入的文案。

- [ ] **Step 2: 运行唯一一次主 Review**

  Freeze backend base/head/scope and review current diff against Transport 0.3、510056 backend debug consumer、ECS contract、migration safety、physical-fact safety、test ownership and HEAVY mapping。生产代码或机器合同修复后，由同一 Reviewer 一轮同时闭合旧意见并 fresh review 当前 diff；Reviewer 不重复 QUALITY/HEAVY。Frontend diff 在 Step 7A 形成后按其仓库规则做一次 scoped Review。

- [ ] **Step 3: 运行 QUALITY**

  Run:

  ```bash
  ./scripts/git-quality-gate.sh --profile quality
  ```

  Expected: exit 0；记录命令、FAST 数量和最终 executable-tree fingerprint。

- [ ] **Step 4: 通过 Commit 授权门禁并运行 branch-wide HEAVY/migration chain**

  未取得独立 Commit 授权时在此停止，不暂存、不构建镜像、不运行 E2E，并报告：

  ```text
  BACKEND TRACK A/B IMPLEMENTED (INCLUDING 510056 BACKEND) — REVIEW/QUALITY VERIFIED — NOT COMMITTED — FRONTEND 510056 PENDING BACKEND LAND — NOT DEPLOYED
  ```

  取得授权并完成 backend Tasks 1–6 各自的精确暂存、staged GitNexus 检查和 Commit 后，使用 Task 0 冻结的完整 SHA；不得改用当前 `origin/develop`。Run:

  ```bash
  test -n "$review_base"
  git cat-file -e "$review_base^{commit}"
  git merge-base --is-ancestor "$review_base" HEAD
  git status --porcelain --untracked-files=all
  git diff --check "$review_base"...HEAD
  git diff --name-status "$review_base"...HEAD
  uv run scripts/select_heavy_tests.py --base "$review_base"
  ./scripts/run_selected_heavy_local.sh --base "$review_base"
  ```

  Expected: backend 候选 worktree clean；`review_base` 是 backend HEAD 祖先；branch-wide manifest 覆盖 backend Tasks 1–6 相对冻结 base 的全部已提交变更且只含授权范围；只运行 selector manifest；0 skipped；fresh PostgreSQL 完成 Phase 11 root 到当前 head，临时容器和 volume 在证据保存后清理。`origin/develop` 后续前移不改变这份证据；若执行者 rebase、merge 新 base 或修改 backend executable input，则对应证据失效并在新冻结快照刷新。

- [ ] **Step 5: 冻结已提交候选**

  Backend Tasks 1–6 可以按各自边界形成多个 Commit，但每个 Commit 均需用户明确授权，且 Commit 前必须精确暂存并运行 `git diff --cached --check` 与 `npx gitnexus detect-changes --scope staged --repo "$PWD"`。Commit hook 失败时只修复失败阶段并刷新被变更失效的证据。Step 4 通过后不再创建空 Commit 或重写历史，直接冻结当前 HEAD/tree。

  ```bash
  git status --porcelain --untracked-files=all
  git rev-parse HEAD
  git rev-parse HEAD^{tree}
  ```

  Expected: Docker build context 中不存在未提交或 untracked 输入；记录唯一候选 Commit/tree。若 worktree 非 clean，禁止继续或以旧 HEAD/tree 标记镜像。

- [ ] **Step 6: 构建绑定当前 HEAD/tree 的 Phase 8 测试镜像**

  Run:

  ```bash
  review_revision=$(git rev-parse HEAD)
  review_source_tree=$(git rev-parse HEAD^{tree})
  review_image="wes-backend:phase8-rough-sorter-${review_revision:0:12}"
  docker build --target testing -t "$review_image" --build-arg WES_VCS_REVISION="$review_revision" --build-arg WES_SOURCE_TREE="$review_source_tree" .
  docker image inspect "$review_image"
  ```

  Expected: 镜像 tag 绑定候选 Commit 短 SHA；`org.opencontainers.image.revision` 等于 `review_revision`，`com.zontec.wes.source-manifest` 等于 `review_source_tree`；不复用固定 tag 或旧 `c8144050` image。

- [ ] **Step 7: 执行 Phase 8 当前制品 E2E**

  扩展现有唯一 `test_business_loop.py` E2E owner，不新建第二套栈：在同一不可变 backend image、真实 API/worker/fulfillment worker、PostgreSQL、Redis 和 WMS stub 中加入一条粗分换架主链。WMS replacement plan 使用本计划的联调测试数据并冻结两腿：`OLD_OUT = CTU03 / target_face="90"`、`NEW_IN = CTU01 / target_face="270"`。Stub 必须捕获真实 Transport submit body，并通过公开 callback 入口回传匹配结果；测试断言：

  - 两腿拥有不同 `transport_task_id`，submit JSON 中模板与 face string 精确保持 `"90"` / `"270"`，没有 A/B 或数字转换；
  - callback 的 `arrival_face` 与各自冻结 `target_face` 精确一致并进入数据库 outcome/projection；
  - `NEW_IN` 匹配成功后可重新触发 material target request，不等待 `OLD_OUT` 完成；
  - `OLD_OUT` 后续成功只闭合自身，不重复创建 target；两腿没有被合并成 `RACK_EXCHANGE`；
  - 所有断言读取真实边界请求与持久化结果，不以 Stub 内部预设值替代 WES 行为。

  Run:

  ```bash
  cd workline_plugins/rough_sorter
  review_revision=$(git rev-parse HEAD)
  review_image="wes-backend:phase8-rough-sorter-${review_revision:0:12}"
  ROUGH_SORTER_E2E_BACKEND_IMAGE="$review_image" uv run pytest tests/e2e -q
  ```

  Expected: provenance gate、原有单物料主路径和新增换架主链 E2E 全部通过；测试创建的容器、网络和临时目录完成清理。该结果仅为当前仓库/Mock 集成证据，不代表真实 WMS/RCS/ECS 或物理验收。

- [ ] **Step 7A: Backend Land 授权门禁后完成 frontend Task 4A**

  Steps 1–7 通过只产生 backend merge-ready candidate，不授权 Push、PR 或 Merge。若 backend Land 未获独立授权，停止并报告：

  ```text
  BACKEND MERGE READY — FRONTEND 510056 CANONICAL SYNC BLOCKED ON BACKEND LAND — NOT DEPLOYED
  ```

  Backend 经授权进入 `develop` 后，重新核对 clean backend `develop` HEAD 包含已验证候选，再执行 Task 4A Steps 4–5。Frontend diff 形成后做一次 scoped Review，运行该 Task 的 targeted Vitest、`contract:test`、`contract:verify`、lint 和 build；同一输入再次生成必须无差异。随后记录 frontend HEAD/tree、`.contract-sync-record.json` backend Commit 和 canonical OpenAPI SHA。Frontend Commit、Push、PR、Merge 仍分别授权；未 Merge 时状态只能是 `FRONTEND CANDIDATE VERIFIED — NOT MERGED`，不得把当前 `develop` 写成已对齐。

- [ ] **Step 8: 关闭合同 alignment 状态**

  Only after Steps 1–7、frontend Task 4A gates 全部通过且 frontend Merge 另行授权并确认进入 `develop`，才把 Task 6 的 `IMPLEMENTED — FINAL VALIDATION PENDING` 统一提升为 `ALIGNED`；这是 repository alignment 的唯一门禁。以下均为人类文档更新，不改变已验证 executable-tree fingerprint：

  - `docs/contracts/transport-fulfillment-contract.md`: `implementation_alignment: ALIGNED`。
  - `docs/superpowers/specs/2026-08-14-wes-wms-transport-dto-design.md`: `implementation_alignment: ALIGNED`，正文删除“代码尚未调整”的当前态句子。
  - `docs/integration/wes-wms-interface-requirements.md`: `wes_alignment: ALIGNED`，但总状态保持 `ReviewRequired`；Transport 与粗分入库只改为 repository/local Mock aligned，`published_at` 仍为 `pending`。
  - `docs/superpowers/specs/2026-08-26-transport-integration-diagnostics-design.md`: 把“Transport 0.3.0 请求字段尚未实施”改为仓内已对齐，同时保留未部署和未现场验收。
  - `docs/integration/rough-sorter-joint-acceptance.md`: 增加当前 HEAD/tree/image digest、命令与结果，历史 RC 保留且明确是旧冻结快照。
  - `README.md`、架构总控和 legacy removal 活动状态：把 Task 6 的 pending 状态统一改为 `Transport 0.3 repository alignment: ALIGNED`，不得遗漏仍在活动引用中的副本。
  - 在验收记录中把“核心合同场景支持”和“生产业务调用点”分栏：粗分 `OLD_OUT/NEW_IN` 为当前已接入调用链；补充 1–2 个单层货架、五层货架到 `FIVE_STATION` 等仍为合同能力，不得写成已交付业务闭环。
  - 单列 510056 为 `TRANSPORT_DEBUG consumer repository-aligned`：记录 backend/frontend Commit/tree/OpenAPI fingerprint、固定 `"90"` payload 和本地测试；状态仍为 `NOT PHYSICAL RUN / NOT BUSINESS AUTHORITATIVE`。
  - 保留 `NOT DEPLOYED / NOT SUPPLIER ACCEPTED / NOT PHYSICAL ACCEPTED / NOT BUSINESS ACCEPTED`。

- [ ] **Step 9: 最终残留与状态检查**

  Run:

  ```bash
  rg -n '\bRackFace\b|CoreRackFace|(target_face|rack_face|arrival_face)\.value|FACE_(90|270)|Literal\["A", "B"\]|\{"A", "B"\}' src packages deployment scripts tests workline_plugins
  rg -n '"(rack_face|target_face|arrival_face)"[[:space:]]*:[[:space:]]*(90|270)([^0-9]|$)' src packages deployment scripts tests workline_plugins docs/contracts/openapi
  rg -n 'ALIGNMENT_REQUIRED|NOT PUSHED|NO PR|NOT MERGED|Phase 11.*待准入' README.md docs
  rg -n "RACK_FACE[[:space:]]*=[[:space:]]*'A'|rack_face:[[:space:]]*'A'|target_face:[[:space:]]*'A'|A 面" /Users/kaizhou/codeDev/wes_frontend-worktrees/codex-feat-transport-03-alignment/src/views/ops/transport-diagnostics /Users/kaizhou/codeDev/wes_frontend-worktrees/codex-feat-transport-03-alignment/tests/unit/views/ops/transport-diagnostics
  git diff --check
  git status --short
  ```

  Expected: Transport 面向 enum、numeric wire 和 token normalization/mapping 无残留；510056 stepper 不再包含 face `A` 或 A 面文案；任何保留的 `ALIGNMENT_REQUIRED` 都属于不同的未批准业务附录并有明确 owner，不是 Transport 0.3、粗分当前链或 510056 debug consumer。

- [ ] **Step 10: 文档 Commit 与最终交付边界**

  Track C 候选 Commit 不自动授权 alignment 文档 Commit。若未取得第二次文档 Commit 授权，保留已验证候选和未提交文档差异并报告：

  ```text
  EXECUTABLE CANDIDATE COMMITTED — QUALITY/HEAVY/E2E VERIFIED — ALIGNMENT DOCS NOT COMMITTED — NOT DEPLOYED
  ```

  若用户授权文档 Commit，只精确暂存 Step 8–9 的人类文档并运行文档相称检查；不重复 QUALITY/HEAVY/migration/E2E。Push、PR、Merge 或 Deploy 仍分别确认授权和结果。部署前必须另行制定 Phase 11 后的空库重建/部署计划；不得尝试原地升级旧 `dd35f04b258f` 联调数据库。

---

## NOT in scope

- 不解释 `"90"` / `"270"`、A/B、角度或货架面的业务语义；WES 只保存、下发、回调并精确比较同一非空 string。
- 不新增 `RackFace` enum/class、face registry、映射表、normalization、最大长度或供应商兼容层；唯一内容边界是提供时不得为 `""`。
- 不新增 v2 API、双写、shim、旧整数/枚举兼容解析或 migration 数据翻译；系统未发布，直接替换目标合同。
- 不新增 `RACK_EXCHANGE`、WES 本地两腿排序器、路径规划、车辆选择、容量判断或 RCS 私有协议实现；物理顺序仍归 RCS。
- 不宣称其它业务线已接入 rack move；当前唯一生产消费者仍是粗分换架，10 场景矩阵只证明核心/ACL/Mock 能力。
- 不把 510056 stepper 改造成无人值守 loop、后台 scheduler、polling relay 或伪 callback；仍由操作员逐步确认，失败停止，每轮需人工重新开始。
- 不执行真实 WMS/RCS/ECS、供应商、物理运动、现场业务验收或 Deploy；本轮 E2E 仅为不可变本地镜像与 Mock 集成证据。
- 不在本评审中清理数据库、Commit、Push、创建 PR、Merge 或 Deploy；联调库清理许可只进入后续执行计划并仍需精确目标安全检查。
- 前端仅修改既有 510056 diagnostics stepper 及 canonical contract/generated artifacts；不新增页面、路由、菜单、配置系统，也不修改无关 Phase 1–11 业务功能、无关 schema、历史归档或零散 TODO。

## What already exists

| Existing capability | Reuse decision |
| --- | --- |
| `TransportService → WmsTransportAdapter → WmsClient` 分层与 `build_submit_data()` 冻结入口 | 原路径复用；只扩展 rack value objects、模板字段和结果规则，不另建 transport stack |
| `request_digest`、`client_request_id` 幂等、冻结 request body 和 outbox | 原机制复用；增加默认模板等价与模板变更冲突测试 |
| Evidence、`DELIVERY_UNKNOWN/RECONCILING`、resource binding/fence | 原安全模型复用；callback mismatch 不另建状态机 |
| Transport callback body `256 KiB` 限制与 WMS client request/response limit | 作为整体资源边界复用；不为 face 增加专用最大长度 |
| 共享 OpenAPI builder、权威 JSON artifact 和 WMS Mock | 同步升级为 0.3；不维护手写第二套 Schema |
| Phase 11 root migration、一个 root/head 的 Alembic 结构测试 | 保留不可变 root，新增唯一扩宽 revision 与完整链验证 |
| Plugin SDK、`DecisionApplier → TransportService` 和粗分 `OLD_OUT/NEW_IN` 恢复路径 | 原业务链复用；只传播普通 string、位置 union 和显式模板 |
| 既有 `/ops/transport-diagnostics` 510056 五步 dialog、`TRANSPORT_DEBUG` BIN_MOVE 和 confirmation + atomic audit/reset | 原步进器与后端 debug path 复用；删除 A/B 语义，传播 canonical `"90"`，并在 rack 两腿分别显式下发 `CTU01` / `CTU03`；不建第二套联调系统 |
| Frontend canonical OpenAPI freeze、生成类型/Zod 与 contract gates | 从冻结 backend Task 3 Commit 重新生成；不手写平行 DTO |
| `test_business_loop.py` 的真实 API/worker/PostgreSQL/Redis/WMS stub E2E 栈 | 扩展同一 owner 覆盖换架，不创建第二套 E2E 基础设施 |
| QUALITY、HEAVY selector、GitNexus 与镜像 provenance gate | 复用既有门禁；最终以冻结 `review_base` 覆盖整条分支 |

## Failure modes and observability

| Codepath | Realistic failure | Test coverage | Existing/planned handling | User/operator visibility |
| --- | --- | --- | --- | --- |
| Domain/API face boundary | `""`、number、bool 或必填 `None` 进入请求 | 领域、API、wire 参数化负例 | 创建前抛合同错误；不建 task/outbox | 明确 4xx/合同错误，不静默 |
| Opaque string propagation | trim/normalization/枚举转换破坏 `"90"` 或 Unicode | raw-string、空白、Unicode、长 string 回归 | 原值进入 request/digest/wire/DB | mismatch 会被 callback gate 显式拒绝 |
| Edge/template matrix | 非法边或 `CTU02 + RACK_MOVE` 被下发 | 全正例/负例矩阵 | 领域边界失败，无外部义务 | 明确合同错误 |
| Default template | 省略值在 digest 后才补齐，等价 retry 冲突 | omitted vs explicit idempotency test | 创建前规范化并冻结 | 冲突为显式 `TransportIdempotencyConflict` |
| WMS submit | timeout/ACK 不确定或 body 超限 | adapter/acceptance/fencing tests | 保留原 identity，进入 `DELIVERY_UNKNOWN/RECONCILING` | 诊断状态可见，不盲重发 |
| Callback exact/broad target | wrong location/rack/face、missing face、unknown result | 参数化 callback matrix | Evidence 留存、围栏保持、不发布成功 | conflict/reconciling 可见 |
| Nullable reconstruction | truthiness 把 `""` 静默变成 `None`，或 `_nonblank` 误拒绝空白 | extraction/recovery regression + residual scan | 显式 `is None` / `value != ""` 分支 | 非法空串明确失败；合法值不静默改写 |
| Migration | 错误 baseline、长值 downgrade 或锁竞争 | baseline/type、base→head、downgrade tests | 类型断言、事务锁、失败关闭；不 `UPDATE` | migration 失败日志明确；不部分成功 |
| SDK/plugin recovery | 模板、位置 kind 或 face 在恢复后漂移 | SDK/deployment/plugin tests | 从冻结 facts 精确重建 | mismatch 阻断 NEW_IN 放行 |
| Rough-sorter two legs | OLD_OUT/NEW_IN 合并、重复 target 或错误等待 | focused plugin tests + immutable-image E2E | 两个独立 identity；NEW_IN 单独控制 target lane | task/outcome/target evidence 可追踪 |
| 510056 stepper | 旧 `A`、把 `WH01` 当点位、rack leg 缺少/错传 template、错误 rack/bin/slot/face 被确认后清理 | backend debug/API/reset + frontend composable/component tests | `WH01` 非 `ZONE` 或 template 错误均在创建前 400；进站 `ZONE→RACK_POSITION + CTU01`、回库 `RACK_POSITION→ZONE + CTU03` 与完整 fixture 精确匹配后才 audit/reset | UI 停留在当前 step；未创建错误任务，已有 task/evidence/fence 可诊断 |
| Candidate image | 固定 tag 指向旧镜像 | SHA tag + OCI revision/tree provenance test | 非 clean tree 或 label mismatch 时停止 | 构建/E2E 门禁明确失败 |
| Alignment docs | 在最终证据前写成 `ALIGNED` | final status scan and diff review | Backend Tasks 1–6 / frontend Task 4A 完成前只写 `FINAL VALIDATION PENDING` | 文档明确暴露未完成边界 |

所有新路径均有测试 owner 和显式错误/状态；本次评审未发现“无测试 + 无处理 + 静默失败”的 critical gap。

## Inline ASCII diagram comments

- `src/app/transport/service.py`：在 rack callback final-target 分支旁保留一个紧凑决策图，说明 exact `RACK_POSITION` 与 broad `RACK/ZONE` 的不同成功条件以及 mismatch → fence 路径。
- `deployment/_rough_sorter_transport.py`（若两腿编排实际位于相邻 recovery 文件，则放在唯一 owner）：保留 `OLD_OUT` / `NEW_IN` 两个 identity、NEW_IN target gate 与独立闭合顺序图。
- 其它简单 dataclass、wire string 检查和 migration 不增加 ASCII 注释；计划与测试已足够，避免把直线逻辑过度文档化。

## Worktree and execution lanes

| Lane/step | Modules touched | Depends on |
| --- | --- | --- |
| Task 0 — Execution Lock | 两个仓库的 Git/worktree、contracts inventory、test ownership | backend PR #192 Land 重新验证；frontend PR #87 状态重新验证 |
| Lane A1 — Tasks 1–4 | Backend `src/app/transport/`、`src/app/wms_adapter/`、migration/models、SDK、deployment、rough-sorter plugin/tests | Task 0；内部按 Task 1 → 2 → 3 → 4 |
| Lane A2a — Task 4A Steps 1–3 | Backend debug service/API/tests | Task 3 canonical OpenAPI + Task 4 core/SDK propagation |
| Lane B — Task 5 | ECS ACK human contract docs | Task 0；逻辑独立但按用户选择不另建写 worktree |
| Convergence — Task 6 | README、Phase 1–11 pending status/current-state docs | Lane A1 + Lane A2a + Lane B 的实际结果 |
| Lane C1 — Task 7 Steps 1–7 | Backend validation evidence、E2E owner、backend Land gate | Backend Tasks 1–6、独立 Commit/Push/PR/Merge 授权、clean committed candidate |
| Lane A2b — Task 4A Steps 4–5 | Frontend 510056 stepper、canonical contract/generated types/tests | 已验证 backend candidate 已授权合入 clean `develop` |
| Lane C2 — Task 7 Steps 8–10 | 两仓 fingerprint、alignment docs | Frontend gates + 独立 Commit/Push/PR/Merge 授权 |

执行顺序为 `Task 0 → Lane A1 → Lane A2a → Lane B → Task 6 → Lane C1 → backend Land → Lane A2b → frontend Land → Lane C2`。Backend 只使用一个专用写 worktree；frontend 只在 Lane A2b 使用自己的专用 paired worktree。两个仓库不并行修改共享机器合同，只允许互不写共享状态的只读影响分析和 FAST 测试并行。保持 3 个逻辑 Track、0 个共享合同并行写 lane、6 个顺序门禁。

## Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above. Run with Codex using `wes-implementation`; checkbox as you ship.

- [ ] **T1 (P1, human: ~45min / Codex: ~8min)** — Execution Lock — 从 debug Land 后的 `origin/develop` 创建唯一干净候选
  - Surfaced by: Architecture Review — base/worktree、track convergence 与 branch-wide evidence 必须同源。
  - Files: Git/worktree metadata、执行证据目录；不改生产代码。
  - Verify: `git status --short`、`git rev-parse origin/develop`、`git merge-base --is-ancestor "$review_base" HEAD`。
- [ ] **T2 (P1, human: ~5h / Codex: ~45min)** — Transport core — 实施普通非空 string、位置/模板矩阵、默认模板和宽泛目标结果规则
  - Surfaced by: Architecture/Code Quality/Test Review — 移除 `RackFace` 语义、修正 move/rotate default、幂等与 callback matrix。
  - Files: `src/app/transport/contracts.py`、`src/app/transport/service.py`、`src/app/transport/submit_snapshot.py`、`tests/runtime/transport/`。
  - Verify: `uv run pytest tests/runtime/transport tests/contracts/wms_adapter/test_transport_wire_acceptance.py::test_rack_submit_data_uses_one_shared_wire_shape -q`。
- [ ] **T3 (P1, human: ~3h / Codex: ~30min)** — Persistence — 扩宽 `arrival_face` 为 TEXT 并验证透明迁移
  - Surfaced by: Architecture/Test Review — 真实 baseline 为 `VARCHAR(1)` 且无 A/B CHECK；legacy `""` 由 migration 原样保留。
  - Files: `src/app/transport/models.py`、`src/app/execution/models/position_projection.py`、新 Alembic revision、migration/integration tests、`docs/architecture/heavy-test-impact.toml`。
  - Verify: migration structure +独占 PostgreSQL `base → head`、upgrade/downgrade owner tests、`uv run alembic heads`。
- [ ] **T4 (P1, human: ~4h / Codex: ~40min)** — WMS boundary — 对齐 wire、OpenAPI、Mock 与 Debug API
  - Surfaced by: Architecture/Code Quality Review — nullable/non-empty string contract、closed edge/template matrix 与 `move=F01` / `rotate=CTU02`。
  - Files: `src/app/wms_adapter/`、`src/app/transport/v1/tasks.py`、`docs/contracts/openapi/`、`tests/contracts/wms_adapter/`、`tests/mock/`、`tests/api/test_transport_tasks.py`。
  - Verify: `uv run pytest tests/contracts/wms_adapter tests/mock tests/api/test_transport_tasks.py tests/integration/test_wms_northbound_feasibility_probe.py -q`。
- [ ] **T5 (P1, human: ~5h / Codex: ~50min)** — SDK and rough-sorter — 传播模板/位置/face 并清除 truthiness/nonblank 漂移
  - Surfaced by: Code Quality/Test Review — SDK/core parity、`""` 不得折叠为 `None`、OLD_OUT/NEW_IN 必须保持独立。
  - Files: plugin SDK transport contracts、`deployment/_rough_sorter_transport*.py`、`deployment/_rough_sorter_values.py`、rough-sorter plugin/tests。
  - Verify: SDK/DecisionApplier/deployment pytest、rough-sorter non-E2E pytest、旧类型与 truthiness 残留扫描。
- [ ] **T5A (P1, human: ~3h / Codex: ~30min)** — 510056 debug consumer — 用 canonical `"90"` WIRE 收敛既有五步联调
  - Surfaced by: Architecture/Code Quality/Test Review amendment — 现有 stepper 仍硬编码 `RACK_FACE='A'`，且未显式携带 0.3 CTU01/CTU03 模板。
  - Files: backend debug service/API/tests；frontend `useTransportDebugLoop.ts`、dialog、canonical OpenAPI/generated types 和对应 unit tests。
  - Verify: backend debug/API focused tests + HEAVY manifest；backend Land 后从 clean `develop` freeze；frontend targeted Vitest、`contract:test`、`contract:verify`、lint、build；rack payload 分别精确为 `ZONE("WH01")→RACK_POSITION("KT16") + CTU01` 与反向 `+ CTU03`，BIN/SCAN 不携带 template，face 精确为 `"90"` 且无 A/B 映射。
- [ ] **T6 (P2, human: ~1.5h / Codex: ~15min)** — Current-state docs — 修复 ECS ACK 矛盾并同步 Phase 1–11 状态
  - Surfaced by: Architecture Review — 文档不能提前写 `ALIGNED`，也不能把历史 ACK/Mock 夸大为当前验收。
  - Files: Task 5–6 列出的 ECS、README、Phase plan 与 current-state docs。
  - Verify: 文档引用/状态扫描、`git diff --check`；不运行无关 QUALITY/HEAVY。
- [ ] **T7 (P1, human: ~5h / Codex: ~55min plus test runtime)** — Final evidence — 绑定 backend gates/image/E2E，顺序完成 frontend canonical sync
  - Surfaced by: Architecture/Code Quality/Test Review — whole-branch selector、SHA image tag、真实组合中的换架链缺口，以及 frontend freeze 只能读取 clean backend `develop`。
  - Files: `workline_plugins/rough_sorter/tests/e2e/test_business_loop.py`、必要 seed fixture、frontend Task 4A consumer/generated contract、最终 alignment docs/evidence。
  - Verify: backend QUALITY、`--base "$review_base"` selected HEAVY、fresh migration chain、OCI labels、rough-sorter E2E；backend Land 后 frontend targeted tests/contract/lint/build 与两个仓库最终 fingerprint。

_No new tasks from Performance Review._

## Review retrospective

当前分支最近已有 `ea79c229 test(transport): 补齐联调确认边界覆盖` 与 `a351b85a feat(transport): 增加联调物理步骤确认`，说明 Transport 边界和物理事实曾在前一轮补强；本计划再次触及相同区域。因此本评审对 identity、digest、ACK/unknown、callback mismatch、resource fence 和不可变制品采用了更严格的显式矩阵与 E2E，而不把既有绿灯当作新 0.3 合同证据。

510056 amendment 进一步检查了已落地的 debug consumer：backend 已有 `TRANSPORT_DEBUG` 专用 BIN_MOVE、operator confirmation 与 atomic audit/reset，frontend 已有同页五步 stepper；实际缺口是旧 `RACK_FACE='A'` 与缺少 0.3 显式模板。计划因此只传播 canonical `"90"` WIRE 和完整冻结匹配，不新建 validator、页面、状态机或自动循环器。

## Engineering review completion

- Step 0 Scope Challenge: 保留 umbrella scope，但拆为 3 个带门禁逻辑轨道。
- Architecture Review: 9 issues found，全部折入计划；新增项为 510056 paired-repo consumer 与 backend canonical contract 的顺序门禁。
- Code Quality Review: 4 issues found，全部折入计划；最终采用 plain `str | None` + 提供时非空，不保留 `RackFace` 或专用 validator abstraction。
- Test Review: coverage diagram produced，5 gaps identified and closed，40/40 planned branches have owners；新增 510056 payload/confirmation/failure/正式路径隔离 owner。
- Performance Review: 0 issues found。
- NOT in scope: written。
- What already exists: written，全部优先复用现有主路径。
- TODOS.md updates: 0 items proposed；没有独立延期价值，未写入 TODO。
- Failure modes: 0 critical gaps flagged。
- Outside voice: skipped because this review already runs under Codex；未产生 cross-model tension。
- Parallelization: 3 logical Tracks，0 shared-contract parallel write lanes / 6 sequential gates；仅只读分析与独立 FAST tests 可并行。
- Lake Score: 14/16 section recommendations selected the recommended option；2 个偏离项均已转化为明确且完整的替代合同，无 unresolved decision。

---

## 验收完成定义

本计划只有在以下条件同时满足时，才能把 Phase 1–11 仓内工程验收从 `DONE_WITH_CONCERNS` 提升为 `ACCEPTED — REPOSITORY/LOCAL INTEGRATION ONLY`：

1. Transport 0.3 的领域、wire、OpenAPI、Mock、数据库投影、SDK 和粗分换架消费者一致；
2. 活动代码与测试中不存在 `RackFace` 类型、face enum、numeric face wire 或 normalization/mapping；face 按上下文接受 `None`，提供时必须是非空 string，`"90"/"270"` 与其它非空 string 均精确透传；
3. 510056 五步 debug consumer 的 rack/bin WIRE、冻结请求、确认审计和 frontend generated contract 使用精确 `"90"`；进站为 `ZONE("WH01") → RACK_POSITION("KT16")` 并显式下发 `rcs_template_id="CTU01"`，回库为相反位置联合并显式下发 `rcs_template_id="CTU03"`，BIN/SCAN 不携带该字段；不存在把 `WH01` 当精确点位、face `"A"`、A/B/template 映射或业务投影伪造；
4. 510056 operator confirmation 只在完整 frozen fixture 匹配时清理，失败停止且保留原 identity/evidence/fence；真实物理联调仍为 `NOT RUN`；
5. ECS ACK Approved 文档只有一套扩展字段规则；
6. README、主计划和 Phase 10 计划与已合并事实一致；
7. 当前 backend HEAD/tree 对应的不可变 Phase 8 image 通过 E2E，frontend 510056 evidence 绑定其独立 HEAD/tree/OpenAPI fingerprint；
8. QUALITY、selector HEAVY、fresh migration chain 和插件测试绑定同一最终 backend 可执行树；
9. 未将 Merge、容器健康、Mock 或 ACK 描述为部署、供应商、物理或业务验收；
10. 验收记录明确区分粗分换架唯一生产调用链、510056 debug consumer 与其它仅由 Transport 核心合同覆盖的场景。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | SKIPPED | Already running under Codex; nested pass skipped |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (AMENDED) | 17 issues, 0 critical gaps; 510056 consumer folded in |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**VERDICT:** ENG CLEARED (AMENDED) — ready to implement after execution-time backend PR #192/frontend PR #87 base checks, paired worktree freeze, and separate execution authorization.

NO UNRESOLVED DECISIONS
