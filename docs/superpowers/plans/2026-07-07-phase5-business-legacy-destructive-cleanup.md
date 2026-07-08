# Phase5 Business Legacy Destructive Cleanup Implementation Plan

> **For Kai / Codex:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan.

> **Completion status (2026-07-07):** DONE and LANDED. 本计划已随 PR #79 合并到 `develop`（`v0.14.0.0`，merge SHA `8c833610c08005005406b3a774c92519f69b7886`）。business destructive cleanup final gate 已通过；`WorkLine.runtime_status` 物理字段删除仍按独立 schema/data cleanup 计划处理。

## Goal

在 Phase5 business readiness 已通过之后，安全删除仍承载业务语义的 legacy WorkLine / plugin / runtime 代码面，并把业务合同、证据、运行时入口全部收口到 Phase4 runtime capability 目标态。

本计划只处理 **business-bearing legacy destructive cleanup**。它不是重新补 Phase3/Phase4 evidence，也不是 UI、运营看板或供应商联调手册建设。

## Scope Check (Pre-Implementation Baseline)

执行前 cleanup matrix 中仍有 `phase4_carrier=True` 的 104 个条目，主要集中在：

- `src/app/workline/domain/contexts/`：粗分、分拣机入库上下文。
- `src/app/workline/domain/contracts/`：粗分、SixInOne、material identity、NG reason 等业务合同。
- `src/app/workline/domain/services/`：SMT handoff route / reason / usage policy。
- `src/app/workline/services/`：bin cell、NG return、single-layer rack、SMT inbound、start admission、station lease。
- `src/workline_runtime/`、`src/workline_plugins/`：如仍存在，只能作为待删除或非运行归档对象，不得继续生产可 import。
- 相关 characterization / contract tests：必须迁移到目标态测试或显式归档，不能因删除 legacy 而丢失业务断言。

明确保留：

- WorkLine 配置 CRUD、manifest、plane scene、业务配置展示等非执行能力。
- Phase3/Phase4 production evidence ledger 和 gate 机制。
- 已迁移到 `src/app/runtime/capabilities/phase4/` 的 runtime capability 服务。

单独受控：

- `WorkLine.runtime_status` 物理字段删除属于 schema/data destructive cleanup。只有当 API、monitor、trace、safety、START admission 全部改用 native runtime read model，并且 migration downgrade / 数据快照方案就绪后，才允许进入删除步骤。

## Architecture Decision

采用 **evidence-first, guardrail-first, packetized cleanup**：

```text
legacy characterization
  -> target Phase4 runtime capability contract tests
  -> state-aware absence guardrails
  -> real reference surface scan
  -> import replacement / symbol move
  -> destructive deletion
  -> Phase5 business + technical gates
```

执行形态：**单 PR**。PR 内仍按 3 个 packet 顺序提交和验收，降低 destructive blast radius：

1. **Packet A — Guardrails and ledger**：新增 cleanup ledger、absence guardrail、target contract coverage。
2. **Packet B — Business code deletion**：替换生产 import，删除或迁移 legacy business-bearing modules。
3. **Packet C — Schema/data cleanup decision**：仅在 native runtime read model 证明完成后处理 `WorkLine.runtime_status` 物理字段；否则记录为后续独立 schema plan。

Packet A → B → C 的提交顺序不可调整，不能先删 legacy 再补 guardrail。每个 packet 必须能独立解释本次变更对 cleanup ledger 的影响。

Guardrail 采用 **state-aware** 形态：`active-source` / `test-only` 行在迁移前只允许被 ledger 和 reference scan 识别为待处置，不得触发误杀；一旦某行 disposition 变为 `moved`、`deleted` 或 `test-only-migrated`，absence/import-fail guardrail 必须进入 strict 阻断。禁止通过长期 allowlist 绕过 strict guardrail。

## Target File Responsibilities

新增：

- `docs/architecture/phase5-business-destructive-cleanup-ledger.csv`
  Canonical tracked ledger。记录 104 个 business carrier 条目的 `entry_id`、tracked-file 状态、处置状态、目标 capability、证据来源、golden fixture / test 覆盖、真实引用扫描结果、外部 alias 证明和删除提交。
- `docs/architecture/phase5-business-destructive-cleanup-ledger.md`
  人读摘要。只解释 CSV ledger 的统计、执行顺序、剩余风险和审计口径，不作为机器校验真源。
- `tests/architecture/test_phase5_business_legacy_absence_guardrail.py`
  依据 cleanup ledger disposition 阻断 production code 重新 import 已完成迁移/删除的 legacy business-bearing paths；不得对仍处于 `active-source` 的行提前 strict fail。
- `tests/architecture/test_phase5_business_contract_no_cycle_guardrail.py`
  阻断 Phase4 低层合同包 import service / repository / database，并检查 capability catalog 与 Phase4 services 不形成循环依赖。
- `tests/contracts/test_phase5_business_destructive_cleanup_ledger.py`
  校验 cleanup ledger CSV 与 cleanup matrix / target capability catalog 一致。
- `scripts/check_phase5_business_destructive_cleanup_gate.py`
  本地和 CI 可复用的 destructive cleanup gate；只读检查，不生成业务数据。除 ledger / matrix / catalog 一致性外，必须扫描 AST import、`importlib`/动态 import 字符串、Celery task 字符串、manifest/config 引用、runtime registry、OpenAPI 暴露面和文档命令中仍会触发旧入口的引用。

修改：

- `src/app/runtime/capabilities/phase4/`
  接收仍有价值的业务合同、catalog、context builder 或 service helper。
- `src/app/workline/`
  只保留配置、manifest、plane scene、API façade 所需模块；删除 execution capability 和旧业务 runtime 语义。
- `tests/workline_runtime/`、`tests/contracts/`、`tests/api/`
  将 legacy characterization 迁到目标态合同测试，或改为 absence / import-fail guardrail。
- `docs/architecture/legacy-cleanup-matrix.md`、`docs/architecture/workline-and-plugin-restructuring.md`
  同步 business destructive cleanup 状态、剩余项和 schema cleanup 判定。
- `scripts/git-quality-gate.sh`
  将 destructive cleanup gate 加入 `--profile quality` 路径，保证本地提交门禁和 CI 使用同一检查。

可删除候选：

- `src/workline_plugins/`：不得继续位于 `src/` 下可 import。
- `src/workline_runtime/plugins/`、`src/workline_runtime/sessions/`、`src/workline_runtime/inbox/`：确认无生产 import 后删除或迁移到目标 runtime services。
- `src/app/workline/domain/contracts/*` 和 `src/app/workline/domain/contexts/*` 中仅服务旧执行流的业务合同。
- `src/app/workline/services/*` 中已由 Phase4 runtime capability 覆盖的执行服务。
- `tests/workline_plugins/*` 中只验证旧 plugin 入口的测试；业务断言迁移后删除。

## Machine Contract Details

### Cleanup Ledger Schema

`docs/architecture/phase5-business-destructive-cleanup-ledger.csv` 是本计划的执行账本，必须使用固定 header：

```text
entry_id,entry_type,relative_path,symbol_or_route,current_owner,business_semantics,phase4_carrier,tracked_state,semantic_status,cleanup_disposition,target_capability,target_capability_status,golden_fixture,contract_tests,reference_scan_status,external_alias_status,delete_commit,notes
```

字段规则：

- `entry_id` 必须逐字等于 `docs/architecture/legacy-cleanup-matrix.csv` 中 `phase4_carrier=True` 的 `entry_id`。不得另造 ID，不得用路径模糊匹配。
- ledger 行数从当前 matrix CSV 动态派生；测试不得硬编码 `104`，文档摘要可以展示当前统计值。
- ledger 必须按 `entry_id` ASCII 升序排序；重复 `entry_id`、缺失 matrix 行、额外未知行都必须 fail。
- `phase4_carrier` 只能是 `True`；如果 matrix 后续刷新改变 carrier 集合，先刷新 ledger，再运行 contract test。
- `golden_fixture`、`contract_tests`、`reference_scan_status`、`external_alias_status`、`target_capability_status` 是 final gate 必填列，不能用空值表达“稍后确认”。

枚举：

| Field | Values | Gate meaning |
| --- | --- | --- |
| `tracked_state` | `active-source` / `test-only` / `already-removed` / `schema-deferred` | 当前 git tracked surface 的事实状态，只用于 inventory 和执行顺序 |
| `semantic_status` | `semantics-covered` / `semantics-obsolete` / `semantics-unverified` | 业务语义是否已由目标态覆盖或确认废弃 |
| `cleanup_disposition` | `pending` / `moved` / `deleted` / `kept-config-only` / `already-removed` / `test-only-migrated` / `schema-deferred` | cleanup 处置状态；strict absence guardrail 只看这一列 |
| `target_capability_status` | `mapped` / `obsolete` / `not-applicable` / `blocked` | 是否有目标 capability；`blocked` 不能进入 final gate |
| `reference_scan_status` | `pending` / `clean` / `allowed-reference-only` / `blocked` | real reference scan 结果；`pending` / `blocked` 不能进入 final gate |
| `external_alias_status` | `not-applicable` / `internal-only` / `external-contract-blocker` / `breaking-change-deferred` | `plugin_key` / `contract_version` 等字段是否仍是外部合同 |

状态关系：

- `tracked_state` 不能驱动 strict fail；它只说明文件当前是否存在。`cleanup_disposition in {moved, deleted, test-only-migrated}` 才触发 strict absence/import-fail guardrail。
- `cleanup_disposition=pending` 不允许进入 final gate。
- `tracked_state=already-removed` 不能自动完成；必须配套 `semantic_status in {semantics-covered, semantics-obsolete}`，否则 final gate fail。
- `tracked_state=schema-deferred` 只能用于 `WorkLine.runtime_status` schema/data 类条目，并且必须配套 `cleanup_disposition=schema-deferred`。
- `external_alias_status=external-contract-blocker` 表示本 PR 不允许删除或改名对应字段；只能先改为目标合同字段 ownership，或另立 breaking-change plan。
- `external_alias_status=breaking-change-deferred` 表示字段删除被刻意推迟到独立破坏性合同计划；本 PR 不得把它计为已删除成果。
- `delete_commit` 在 Packet A 可为空；Packet B/C 完成后，进入 `deleted`、`moved`、`test-only-migrated` 的行必须填写删除或迁移提交 hash。

### Matrix Source Of Truth

- `docs/architecture/legacy-cleanup-matrix.csv` 是 cleanup matrix 的机器真源，由 `scripts/generate_legacy_matrix.py` 生成。
- `docs/architecture/legacy-cleanup-matrix.md` 只做字段说明、统计摘要和人读审计说明；当 `.csv` 与 `.md` 冲突时，执行和测试以 `.csv` 为准。
- destructive cleanup ledger 只能从 `.csv` 的 `phase4_carrier=True` 行派生；`.md` 不得作为脚本输入。
- T6 更新 `.md` 时必须说明：`.csv` → destructive cleanup ledger → final gate 是执行链路，`.md` 不是 closure 证据。

### Reference Scan Boundary

destructive cleanup gate 的 scan roots：

- 必扫：`src/`、`scripts/`、`tests/`、`migrations/versions/`、`docs/architecture/`、`docs/contracts/`、`docs/superpowers/plans/`、`pyproject.toml`、`alembic.ini`。
- 必扫非 import 字符串：Celery task name、Beat schedule、runtime registry、provider profile、manifest YAML、OpenAPI path / query / schema、shell/python script 参数、文档中的可执行命令。
- 默认排除：`.git/`、`.venv/`、`__pycache__/`、`.pytest_cache/`、raw `reports/` artifacts、generated coverage/cache 输出。
- `docs/archive/phase5-business-cleanup/` 只允许 historical/reference-only 内容。若其中出现可复制运行的 import path、CLI 命令、Celery task name 或 manifest/config 示例，必须 fail。
- 非 archive 文档中出现 legacy path 时，只有明确标注为 historical/non-runnable 且 ledger 行为 `reference_scan_status=allowed-reference-only` 才允许通过。

### Target Capability Catalog Contract

cleanup ledger 的 `target_capability` 使用带 namespace 的稳定 identifier：

| Namespace | Format | Canonical source |
| --- | --- | --- |
| `runtime` | `runtime:<capability_key>` | `src/app/runtime/runtime_capability_catalog.py` 的 `RuntimeCapabilityCatalog` |
| `phase4` | `phase4:<module>.<symbol>` | `src/app/runtime/capabilities/phase4/` 及其低层 `contracts/` 包 |
| `external` | `external:<Port.method>` | `ExternalContractProfile.runtime_capabilities_query/effect` |
| `workline-config` | `workline-config:<symbol>` | 保留的 WorkLine config / manifest / plane scene 只读边界 |

规则：

- `src/app/runtime/runtime_capability_catalog.py` 是 RuntimeInbox → dispatcher 热路径 capability wiring 的真源。
- `src/app/runtime/capability_catalog.py` 当前是 WorkLine 业务 capability catalog 的过渡真源；被它引用的纯业务合同必须在本计划中迁入 `src/app/runtime/capabilities/phase4/contracts/` 或标记 `kept-config-only`。
- 如果业务语义已废弃，`target_capability_status=obsolete` 且 `target_capability` 留空；`notes` 必须写明废弃原因和覆盖测试。
- 如果条目只是 schema/data 决策或历史 matrix 行，`target_capability_status=not-applicable`；不得伪造 Phase4 capability。
- `target_capability_status=blocked` 不能进入 final gate。

### External Alias Resolution

本计划“不保留 `plugin_key` / `contract_version` legacy alias”只针对旧 plugin registry / legacy selector 兼容层，不等于删除目标态外部合同字段。

删除或改名任何 `plugin_key` / `contract_version` 前，必须扫描：

- provider payload / callback schema。
- `ExternalContractProfile`、provider profile、runtime capability evidence。
- trace、diagnostic、report、production evidence artifact。
- manifest YAML、OpenAPI route/query/schema。
- SQL seed、data scripts、historical fixture、golden payload。

决策规则：

- 仅旧内部 alias 命中：`external_alias_status=internal-only`，可随本 PR 删除。
- 仍是目标外部合同字段：`external_alias_status=external-contract-blocker`，本 PR 不删除字段，只删除旧 legacy 入口或迁移 ownership。
- 确实要移除外部字段：`external_alias_status=breaking-change-deferred`，必须另立 breaking-change plan；本 PR 不能 claim alias 删除完成。

## Implementation Tasks

### T0 — Preflight and Baseline

- [x] 确认当前分支基于 `develop`，且 worktree 状态已审计；`AGENTS.md` / `CLAUDE.md` 存在既有未提交变更，本计划不触碰。
- [x] 运行 readiness baseline：
  - `uv run python scripts/check_phase5_readiness_gate.py --lane technical`
  - `uv run python scripts/check_phase5_readiness_gate.py --lane business --phase3-p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --phase3-benchmark-artifact reports/phase3/phase3-production-benchmark.json --phase4-evidence-artifact reports/phase4/runtime-evidence-production.json`
- [x] 从 `docs/architecture/legacy-cleanup-matrix.csv` 导出 `phase4_carrier=True` 条目，生成人工可审查的 cleanup ledger 初稿。
- [x] cleanup ledger 初稿必须使用本计划 `Cleanup Ledger Schema` 的固定 header、enum 和排序规则；contract test 先验证 schema，再验证语义。
- [x] 使用 `git ls-files` 对当前 `phase4_carrier=True` matrix 条目做 tracked-file reconciliation，cleanup ledger 必须区分：
  - `active-source`：当前仍有 tracked production source 或 test source，需要本 PR 处置。
  - `already-removed`：matrix 记录的是历史路径，当前 tracked source 已不存在，不能计入本 PR 删除成果。
  - `test-only`：只剩 characterization / contract test，必须迁移到目标态测试或反转为 absence guardrail。
  - `schema-deferred`：只和 `WorkLine.runtime_status` schema/data 决策相关。
- [x] 对每个 `already-removed` 行补业务语义状态：`semantics-covered`、`semantics-obsolete` 或 `semantics-unverified`。`already-removed` 只能说明文件已不在 git，不能自动视为业务语义已迁移。
- [x] T3/T4 的实际删除清单只能从 cleanup ledger 的 `active-source` / `test-only` 行导出，不再手工维护静态服务名单。
- [x] 对将要修改的 class / function / method 逐组运行 GitNexus impact analysis；HIGH / CRITICAL 风险必须暂停并告知用户。

### T1 — Guardrails Before Deletion

- [x] 新增 cleanup ledger contract test，要求每个 `phase4_carrier=True` 条目具备：
  - 与 matrix CSV 完全一致且唯一的 `entry_id`。
  - 固定 header、enum、排序和 required field 校验。
  - legacy path / symbol。
  - tracked-file 状态。
  - target Phase4 capability 或明确的删除原因。
  - legacy characterization fixture / target contract test 覆盖。
  - 真实引用扫描状态。
  - `plugin_key` / `contract_version` 等旧 alias 的外部合同证明状态。
  - cleanup disposition：`pending`、`moved`、`deleted`、`kept-config-only`、`already-removed`、`test-only-migrated`、`schema-deferred`。
- [x] 新增 state-aware absence guardrail，按 cleanup ledger disposition 区分 pending 与 strict：
  - `active-source` / `test-only`：允许存在，但必须在 ledger 中有目标 capability、迁移测试计划和 owner。
  - `moved` / `deleted` / `test-only-migrated`：legacy import、动态 import、runtime registry 或旧测试要求旧路径可 import 时必须 fail。
  - `already-removed`：不得作为本 PR 删除成果；若业务语义未验证，必须留 `semantics-unverified` 并阻断最终 closure。
- [x] absence guardrail 阻断 production code import：
  - `src.workline_plugins`
  - `src.workline_runtime.plugins`
  - `src.workline_runtime.sessions`
  - `src.workline_runtime.inbox`
  - 已完成迁移的 `src.app.workline.domain.contracts.*`
  - 已完成迁移的 `src.app.workline.services.*`
- [x] 新增 legacy import fail 测试，确保删除后的旧 module path 不再可 import；测试只对 disposition 已进入 strict 的行生效。
- [x] 新增 destructive cleanup reference scan，覆盖：
  - Python AST import / from-import。
  - `importlib.import_module`、`__import__`、字符串拼接形式的旧 module path。
  - Celery task names、Beat schedule、manifest/config、runtime capability catalog、provider profile、OpenAPI schema、脚本参数和文档命令。
  - `Reference Scan Boundary` 中定义的 include/exclude roots 和 archive reference-only 规则。
- [x] 反转旧 mirror / shim 保护网，避免旧测试继续要求 legacy path 存在：
  - `tests/architecture/test_workline_domain_mirror.py`
  - `tests/architecture/test_workline_service_shim_contract.py`
  - `tests/architecture/test_workline_compat_mirror.py`
  - `tests/architecture/test_phase0_legacy_matrix_contract.py`
  - `tests/architecture/test_phase5_legacy_absence_guardrail.py`
- [x] 运行：
  - `uv run pytest tests/architecture/test_phase5_business_legacy_absence_guardrail.py tests/architecture/test_phase5_business_contract_no_cycle_guardrail.py tests/contracts/test_phase5_business_destructive_cleanup_ledger.py -q`
  - `uv run pytest tests/architecture/test_workline_domain_mirror.py tests/architecture/test_workline_service_shim_contract.py tests/architecture/test_workline_compat_mirror.py tests/architecture/test_phase0_legacy_matrix_contract.py tests/architecture/test_phase5_legacy_absence_guardrail.py -q`

### T2 — Move Business Contracts Into Target Runtime Surface

- [x] 审核并迁移仍有价值的业务合同：
  - Rough sorter payload / command / callback classifier。
  - Sorter inbound context builder。
  - SixInOne contract validation。
  - Material identity hash / resolution。
  - NG reason catalog。
  - SMT inbound handoff reason / route / usage policy。
- [x] 目标命名空间优先使用 `src/app/runtime/capabilities/phase4/`；仅纯配置合同可留在 WorkLine config domain。
- [x] 明确“不得迁移”的旧语义：旧 plugin lifecycle、旧 registry key 兼容层、旧 template/build surface、仅为旧测试服务的 shim、会触发 service/repository/database import 的 helper 不得搬进 Phase4。
- [x] 新建或使用 `src/app/runtime/capabilities/phase4/contracts/` 作为低层业务合同包：
  - 该包不得 import Phase4 services、repositories、SQLAlchemy、数据库 model 或 runtime effect applier。
  - 该包 import 时不得加载配置、注册 capability、读取 env、初始化缓存/HTTP client 或产生数据库连接。
  - `src/app/runtime/capability_catalog.py` 与 Phase4 services 只能向下依赖该合同包。
  - `src/app/runtime/capabilities/phase4/__init__.py` 不得 re-export 会触发 service import 的合同对象。
- [x] 新增 no-cycle guardrail，验证 Phase4 contracts package 可单独 import，且 catalog/services 不形成 `catalog -> service -> catalog` 循环。
- [x] 更新 target runtime services 的 import，不保留 `plugin_key` / `contract_version` legacy alias；删除前必须扫描 provider payload、callback schema、trace/evidence/report schema、manifest、OpenAPI 和历史 fixture，证明这些字段不是外部合同。
- [x] 若 `plugin_key` / `contract_version` 是 `ExternalContractProfile`、OpenAPI、manifest、evidence 或 callback 的目标合同字段，不得在本 PR 删除；ledger 标记 `external-contract-blocker` 或 `breaking-change-deferred`，并只清理旧 legacy alias ownership。
- [x] 冻结 legacy characterization fixtures / golden payloads / callback 样本，再将旧 characterization tests 改写为目标态 contract tests；每个迁移项必须证明 target test 断言覆盖旧 fixture 的成功、未知 capability、未声明 profile、duplicate callback、business key mismatch 和业务失败分类。
- [x] 运行：
  - `uv run pytest tests/contracts/workline tests/workline_runtime -q`

### T3 — Delete Legacy Business Execution Services

- [x] 逐个替换并删除 cleanup ledger 中 `active-source` 的 legacy execution services / domain services。历史 matrix 行如果当前 tracked source 已不存在，只能标记 `already-removed`，不得作为本 PR 删除成果。
- [x] 对 `single_layer_rack_orchestration_service` 做 capability mapping 复核；cleanup matrix 当前目标 capability 需要与真实 Phase4 owner 对齐。
- [x] WorkLine API / service 层如仍需要读取配置，只能依赖 config/read-model service，不得调用 runtime execution service。
- [x] 删除前后各运行一次 reference scan，确认生产入口、Celery task、runtime registry、manifest/config、OpenAPI schema、脚本和文档命令都不会再触发已 strict 的旧路径。
- [x] 运行：
  - `uv run pytest tests/workline_runtime tests/contracts tests/api -q`

### T4 — Remove Legacy Runtime and Plugin Surfaces

- [x] 如果 `src/workline_plugins/` 仍存在，将非运行参考材料迁到 `docs/archive/phase5-business-cleanup/`；不得继续保留在 `src/`。
- [x] 如果 `src/workline_runtime/` 子目录仍承载旧 plugin/session/inbox 入口，先确认目标态分别由 RuntimeInbox、ExecutionSession、RuntimeCapabilityDispatcher / Phase4 service 覆盖，再删除。
- [x] 删除或迁移 `tests/workline_plugins/*`。业务断言迁入 `tests/contracts/`，旧入口断言改为 absence guardrail。
- [x] 所有旧 mirror / shim 测试必须同步反转为目标态 import 或 absence 断言，不允许保留“legacy module 必须 importable”的测试。
- [x] 对旧 runtime/plugin surface 做非 import 引用审计：Celery task 字符串、Beat schedule、shell/data scripts、manifest YAML、provider profiles、config defaults、docs 命令和 runtime registry 不得再引用 `src/workline_plugins` 或 legacy `src/workline_runtime` 子入口。
- [x] 运行：
  - `uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q`
  - `uv run pytest --collect-only -q -o addopts='' | tail -5`

### T5 — Runtime Status Schema/Data Decision

- [x] 扫描 `WorkLine.runtime_status` 物理字段消费者，确认 API、monitor、trace、safety、START admission 的 runtime 状态来源。
- [x] 默认处置为 `schema-deferred`。若仍有任一生产消费者依赖物理字段，本 packet 只更新 ledger 为 `schema-deferred`，不得删除字段。
- [x] 本轮判定不删除 schema 字段；下列 migration 分支不适用，需进入独立 schema/data cleanup 计划。
- [x] 本轮未进入 schema 字段删除分支：仍有兼容投影 / seed / smoke consumer，schema/data cleanup 独立计划处理。
  - 新增 runtime status consumer inventory test，枚举并验证模型、API、query/trace、safety、START admission、monitor smoke、seed/reset 脚本不再依赖物理字段。
  - 更新并测试 `scripts/data/reset_runtime_data.py`、`scripts/data/seed_runtime_monitor_smoke.py`、`scripts/data/sync_test_workline_devices.py` 等数据脚本。
  - 补 projection/service/API tests，覆盖 `runtime_status_snapshot` 替代路径、API runtime status 响应、runtime monitor smoke 数据准备。
  - 使用 Alembic generator 创建 migration。
  - 提供 downgrade。
  - 在删除字段前记录数据库快照 / rollback runbook，覆盖 migration 写入窗口、应用版本错配、downgrade 顺序、回滚后 guardrail 状态和数据重放边界。
  - 不 drop 业务承载数据表，不清理 production evidence。
- [x] migration 验证不适用：本轮未生成 Alembic migration。
  - `uv run pytest tests/architecture/test_phase2_runtime_status_owner_guardrail.py tests/workline_runtime/test_workline_runtime_status_projection_service.py tests/api/test_workline_runtime_sse.py tests/api/test_workline_safety_operation_api.py -q`
  - `uv run pytest tests/scripts/test_sync_test_workline_devices.py -q`
  - `uv run alembic upgrade head`
  - `uv run alembic downgrade -1`
  - `uv run alembic upgrade head`

### T6 — Close Docs, Gates, and Review Evidence

- [x] 更新 cleanup ledger，将当前全部 `phase4_carrier=True` business carrier 条目的 disposition 全部关闭或标记为 schema-deferred。
- [x] 更新 `docs/architecture/workline-and-plugin-restructuring.md`：
  - Phase5 business destructive cleanup 的完成状态。
  - 剩余 schema/data 项的明确原因。
  - WorkLine 现在只承载 config / manifest / plane scene 的边界。
- [x] 更新 `docs/architecture/legacy-cleanup-matrix.md`，说明 matrix 与 destructive cleanup ledger 的关系，不手工伪造生成结果。
- [x] 更新 `docs/architecture/legacy-cleanup-matrix.md` 时必须声明 `.csv` 是机器真源、`.md` 是摘要；任何 closure 数字都从 `.csv` / ledger contract test 派生。
- [x] 先让 `scripts/check_phase5_business_destructive_cleanup_gate.py --mode final` 独立通过，再接入 `scripts/git-quality-gate.sh --profile quality`；不得把不稳定 gate 直接接进全局质量门禁。
- [x] 运行 final gates：
  - `uv run python scripts/check_phase5_business_destructive_cleanup_gate.py --mode final`
  - `uv run python scripts/check_phase5_readiness_gate.py --lane technical`
  - `uv run python scripts/check_phase5_readiness_gate.py --lane business --phase3-p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --phase3-benchmark-artifact reports/phase3/phase3-production-benchmark.json --phase4-evidence-artifact reports/phase4/runtime-evidence-production.json`
  - `uv run python -c "from main import app; app.openapi(); print('openapi ok')"`
  - `uv run python -c "from src.celery_app.app import celery_app; celery_app.loader.import_default_modules(); print('celery import ok')"`
  - `./scripts/git-quality-gate.sh --profile quality`
- [x] Commit 前运行 GitNexus detect changes，确认影响范围只覆盖 Phase5 business cleanup 相关模块。结果为 high；其中 `AGENTS.md` / `CLAUDE.md` 是既有未提交变更，本轮不触碰、不 stage。其余影响集中在 Phase5 business cleanup 的合同迁移、ledger、gate、docs 与已迁移 runtime/service 调用面。

## Acceptance Criteria

- 所有 `phase4_carrier=True` 条目在 tracked ledger 中有处置状态和目标证据。
- Destructive cleanup ledger CSV 使用固定 header、enum、排序和状态关系；contract test 校验其与 cleanup matrix CSV / target capability catalog / real reference scan 一致。
- Cleanup matrix 以 `docs/architecture/legacy-cleanup-matrix.csv` 为机器真源，Markdown 只做摘要；任何 closure 数字都从 CSV 与 ledger 测试派生。
- Matrix historical rows 与当前 tracked source 已完成 reconciliation，本 PR 删除成果只来自 `active-source` / `test-only` 行。
- `already-removed` 行不得自动算完成；必须标注业务语义已覆盖、已废弃或仍未验证。
- strict absence/import-fail guardrail 只由 `cleanup_disposition in {moved, deleted, test-only-migrated}` 触发，不由 `tracked_state` 触发。
- 已迁移业务语义都有 legacy fixture / golden payload / callback 样本证明 target Phase4 contract test 覆盖等价行为。
- 任何 `plugin_key` / `contract_version` legacy alias 删除前都已完成 external alias scan；若字段仍属于 `ExternalContractProfile`、OpenAPI、manifest、evidence、callback 或报表合同，本 PR 不删除字段，只迁移 ownership 或记录 breaking-change-deferred。
- 已 strict 的 legacy business-bearing modules 在 AST import、动态 import、Celery task、manifest/config、runtime registry、OpenAPI、脚本和文档命令中均无残留引用。
- `docs/archive/phase5-business-cleanup/` 仅保留 historical/reference-only 内容；可执行 import/命令/manifest/config 示例不得指向已 strict legacy surface。
- `target_capability` 全部使用 `runtime:`、`phase4:`、`external:`、`workline-config:` namespace；obsolete / not-applicable 行不得伪造 capability。
- `src/workline_plugins/` 不再作为 production import surface 存在。
- `src/workline_runtime/plugins|sessions|inbox` 不再作为 legacy execution path 存在；若仍有 `src/workline_runtime/`，只允许目标态 runtime read model / orchestration 边界。
- WorkLine 模块保留边界清楚：config CRUD、manifest、plane scene、只读 façade；不再承载 Phase4 execution capability。
- Phase5 business gate 和 technical gate 均通过。
- 旧 plugin / workline characterization tests 已迁移为目标态 contract tests，或明确转为 absence guardrail。
- 旧 mirror / shim 架构测试已反转为目标态 import / absence guardrail，不再要求 business-bearing legacy module 可 import。
- Phase4 business contracts 位于低层合同包，no-cycle guardrail 证明其不依赖 service / repository / database。
- Phase4 business contracts package 可单独 import，且 import 无配置加载、env 读取、HTTP/cache 初始化、DB 连接等副作用。
- Destructive cleanup gate 已进入 `scripts/git-quality-gate.sh --profile quality`。
- FastAPI app import + OpenAPI generation、Celery app import + default task module import smoke 均通过。
- 未提交 raw `reports/` artifacts。
- 如果包含 Alembic migration，migration 由 generator 生成，downgrade 可执行，且无业务数据 drop。

## Tests And Gates

必跑：

- `uv run pytest tests/architecture/test_phase5_business_legacy_absence_guardrail.py tests/contracts/test_phase5_business_destructive_cleanup_ledger.py -q`
- `uv run pytest tests/architecture/test_phase5_business_contract_no_cycle_guardrail.py -q`
- `uv run pytest tests/architecture/test_workline_domain_mirror.py tests/architecture/test_workline_service_shim_contract.py tests/architecture/test_workline_compat_mirror.py tests/architecture/test_phase0_legacy_matrix_contract.py tests/architecture/test_phase5_legacy_absence_guardrail.py -q`
- `uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q`
- `uv run pytest tests/workline_runtime tests/contracts tests/api -q`
- `uv run pytest tests/contracts/test_phase5_business_lane_matrix_closure.py tests/contracts/test_phase5_readiness_gate.py -q`
- `uv run pytest --collect-only -q -o addopts='' | tail -5`
- `uv run python scripts/check_phase5_business_destructive_cleanup_gate.py --mode final`
- `uv run python scripts/check_phase5_readiness_gate.py --lane technical`
- `uv run python scripts/check_phase5_readiness_gate.py --lane business --phase3-p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --phase3-benchmark-artifact reports/phase3/phase3-production-benchmark.json --phase4-evidence-artifact reports/phase4/runtime-evidence-production.json`
- `uv run python -c "from main import app; app.openapi(); print('openapi ok')"`
- `uv run python -c "from src.celery_app.app import celery_app; celery_app.loader.import_default_modules(); print('celery import ok')"`
- `./scripts/git-quality-gate.sh --profile quality`

如果 T5 删除 schema 字段，追加：

- `uv run pytest tests/architecture/test_phase2_runtime_status_owner_guardrail.py tests/workline_runtime/test_workline_runtime_status_projection_service.py tests/api/test_workline_runtime_sse.py tests/api/test_workline_safety_operation_api.py -q`
- `uv run pytest tests/scripts/test_sync_test_workline_devices.py -q`
- `uv run alembic upgrade head`
- `uv run alembic downgrade -1`
- `uv run alembic upgrade head`

## Failure Modes And Rollback

- **Business contract drift**：target contract test 失败时，停止删除，先恢复业务断言，再修 target capability。
- **Weak semantic migration**：golden fixture / callback 样本无法被 target contract test 覆盖时，停止删除；不得用重写后的弱断言替代旧业务约束。
- **State-aware guardrail 误杀**：`active-source` 行在迁移前只能被识别为待处置，不能 strict fail；strict fail 只对 `moved` / `deleted` / `test-only-migrated` 生效。
- **Import cycle**：Phase4 contract 包引入 service / repository / database 时，no-cycle guardrail 阻断；合同层只能承载纯业务类型、parser、catalog 和无副作用 helper。
- **Contract import side effect**：Phase4 contract 包 import 时触发配置、env、HTTP/cache 或 DB 初始化，no-side-effect guardrail 阻断。
- **Legacy import 回流**：absence guardrail 阻断；不得通过 allowlist 绕过。
- **String reference 回流**：Celery task、manifest/config、runtime registry、OpenAPI 或脚本字符串仍指向旧路径时，destructive cleanup gate 阻断。
- **External alias breakage**：`plugin_key` / `contract_version` 若仍是目标外部合同字段，禁止在本 PR 删除字段；只删除旧 legacy alias ownership，或记录独立 breaking-change plan。
- **Unknown capability**：必须产生明确 diagnostic，不 fallback 到 legacy plugin 或 `null_plugin`。
- **Undeclared provider capability**：按 `ExternalContractProfile` fail closed。
- **Runtime status schema risk**：任一生产消费者未迁出时，不执行字段删除。
- **Startup/import breakage**：FastAPI OpenAPI 或 Celery app import smoke 失败时，不进入 review/ship。
- **Single PR rollback**：优先 `git revert`；若包含 migration，先执行 downgrade，再 revert；不得通过手工 SQL drop 业务数据。rollback runbook 必须说明数据写入窗口、应用版本错配、guardrail 反转状态和重放边界。
- **Packet rollback**：Packet A 可直接 revert；Packet B revert 后必须重跑 Phase5 gates；Packet C revert 必须同时回滚 migration 和 docs ledger 状态。

## Not In Scope

- 不删除业务配置、业务证据、审计数据或 production reports ledger。
- 不补建运营看板、告警平台、供应商联调手册。
- 不重新实现 Phase3/Phase4 production evidence composer。
- 不做 UI / frontend 改造。
- 不保留旧 plugin registry / legacy selector 的 `plugin_key` / `contract_version` 兼容 alias；不删除仍属于目标态外部合同的字段。
- 不用 mock evidence 通过 production cleanup gate。

## Execution Notes

- 每个删除 packet 都先跑 GitNexus impact analysis，再改代码。
- 对 HIGH / CRITICAL blast radius，先汇报影响范围并等待确认。
- 删除文件前，必须已经有目标态测试覆盖同一业务语义。
- 删除文件前，必须已经有 golden fixture 等价覆盖和 real reference scan 证明没有运行时字符串入口残留。
- 文档和 ledger 必须与代码同 PR packet 更新，避免出现“代码已删但审计证据无法解释”的状态。
- 执行完成后使用 code review / requesting-code-review 流程验收，直到无 actionable findings。

## Implementation Completion Status

- **Status:** DONE / LANDED.
- **Landing:** PR #79 merged into `develop` on 2026-07-07 as `v0.14.0.0`; merge SHA `8c833610c08005005406b3a774c92519f69b7886`.
- **Delivered:** 104 条 `phase4_carrier=True` business carrier 已在 `docs/architecture/phase5-business-destructive-cleanup-ledger.csv` 中关闭或保留为目标态测试证据；旧 WorkLine/plugin business-bearing contracts、contexts、SMT handoff route / reason / usage policy 已收口到 Phase4 runtime capability / contracts 目标态。
- **Guardrails:** `scripts/check_phase5_business_destructive_cleanup_gate.py --mode final` 通过，并已接入 `scripts/git-quality-gate.sh --profile quality`；absence/no-cycle/ledger guardrails 阻断 legacy business import 或 Phase4 contract layer 回流。
- **Verification at ship:** `uv run pytest tests/ -q` passed with `1820 passed, 5 skipped`; `uv run pytest --collect-only -q -o addopts=''` collected `1825`; Phase5 technical and business readiness gates passed.
- **Deploy status:** PR merged; no GitHub deploy workflow or production URL was configured, so post-merge canary was skipped by user choice and recorded as `DEPLOYED (UNVERIFIED)`.
- **Remaining scope:** `WorkLine.runtime_status` schema/data deletion remains explicitly deferred to a separate migration plan; this PR did not include Alembic migration or business data drop.

## GSTACK REVIEW REPORT

以下保留实施前终审记录；当前完成状态以上方 `Implementation Completion Status` 为准。

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | skipped | Backend destructive cleanup; no product scope change |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | folded | Guardrail phasing, real reference scan, golden fixture parity, external contract alias proof, startup/import smoke, rollback realism folded into plan |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 4 | clean | No new implementation blockers; final 6 blockers remain folded: ledger schema, matrix source, alias resolution, scan boundaries, target catalog contract, state/disposition model |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | skipped | Backend/doc/test only |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | skipped | Not required |

- **CODEX:** Previous outside voice findings folded; skill-instruction-conflict concern rejected as host-specific.
- **CROSS-MODEL:** Reviews agree on guardrail-first single-PR packet direction with explicit machine-contract hardening.
- **VERDICT:** ENG CLEARED — ready for implementation under Packet A → B → C discipline.
NO UNRESOLVED DECISIONS
