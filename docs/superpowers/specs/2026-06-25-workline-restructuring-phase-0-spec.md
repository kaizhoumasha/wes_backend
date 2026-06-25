# WorkLine 重构 Phase 0：目标态锁定与架构护栏 SPEC

## Context

`docs/architecture/workline-and-plugin-restructuring.md` 已完成顶层设计评审。Phase 0 的目标不是写新 runtime 代码，而是先锁定目标态边界、行为契约测试、legacy 清理矩阵和自动化架构护栏，避免后续实现被旧 WorkLine/plugin/runtime 形态反向约束。

直接读者是后端实现 agent 和 reviewer。本 SPEC 必须让实现者能按任务包启动 Phase 0，不需要回到顶层设计重新判断边界。

## Current State

顶层设计明确 Phase 0 有 7 项必做任务，且 Phase 0-5 按 critical path 严格串行，Phase 内任务可并行。Phase 0 完成前，不应启动 Phase 1 的目标态骨架实现。

现有相关事实：

> 注：下表所有"文件数"基线使用 `git ls-files <path> | wc -l`（gitignore 过滤后的实际跟踪文件数），与 §Proposed Change 刷新命令保持一致。`find -type f` 因包含 `__pycache__` 等忽略目录，结果约为 2 倍，不可作为对照。

| 证据 | 当前状态 | 对 Phase 0 的影响 |
| --- | --- | --- |
| `docs/architecture/workline-and-plugin-restructuring.md:1765` | Phase 0 包含 P0-001 到 P0-007 七项必做 | SPEC 必须全量覆盖 7 项,不只写触发清单里的技术文档 |
| `docs/architecture/workline-and-plugin-restructuring.md:2192` | Phase 0 启动时要求 `external-contract-profile-spec.md`、`integration-lab-and-simulator-spec.md`、`architecture-guardrails-spec.md` | 本 SPEC 在 P0-006/P0-007 子章节统一定义三者内容,实施时仍按主计划产出对应独立文件 |
| `docs/architecture/workline-and-plugin-restructuring.md:1166` | §7.5 定义 17 条关键不变量,核心 5 条必须自动检查 | `architecture-guardrails.sh` 和 `tests/architecture/` 必须映射这些不变量 |
| `docs/integration/wms_rcs_interface_requirements.md:24` | 当前 RCS 调度仍由 WMS 统一调度 | Phase 0 合同不能引入 WES 直连 RCS/AGV/CTU 的目标 |
| `docs/integration/third_party_integration_whitepaper.md:33` | 设备交互采用 Command -> Ack -> Callback 异步机制 | DeviceCommand 合同必须保持异步闭环,不允许 Event_Push 响应直接带动作指令 |
| `src/app/workline/` | 89 个文件（`git ls-files`） | legacy 清理矩阵必须逐入口分类 |
| `src/workline_runtime/` | 50 个文件（`git ls-files`） | 旧 runtime 只能作为业务事实样本和 characterization 来源 |
| `src/workline_plugins/` | 12 个文件（`git ls-files`） | 旧 plugin 框架不得作为目标态架构基础 |
| `tests/workline_runtime/` | 104 个文件（`git ls-files`） | 行为契约测试要保护业务语义,不以旧代码覆盖率为目标 |

## Proposed Change

新增 Phase 0 阶段级执行 SPEC，覆盖 P0-001 到 P0-007。执行结果是一组目标态文档、测试基线和 guardrails 脚本，不修改生产行为。

本轮 eng review 后，本 SPEC 固化 7 个执行约束，后续实现不得再把这些约束当作可选建议：

| 审计结论 | 本 SPEC 固化位置 |
| --- | --- |
| Phase 0 范围很大，但本阶段希望一个 PR 完成 | §Proposed Change 的单 PR review packet、§Implementation Tasks、§Single PR Execution Plan |
| guardrails 不能只作为手动脚本，必须进入本地质量门禁和 CI/Jenkins | P0-007、§Acceptance Criteria、§Testing Plan、T2 |
| C5 RuntimeInbox 状态机测试必须使用目标态测试模型，不能绑定 legacy inbox | P0-007 C5、§Risk Controls、T3 |
| seed allowlist 必须能反查 legacy matrix `entry_id` 和 `drop_phase` | P0-002 `guardrail_seed_scope`、P0-007 seed allowlist、T4 |
| BC-05/BC-06 不能只提取旧行为，必须同时有目标态 contract 壳 | P0-003、§Risk Controls、T5 |
| Phase 0 验证命令必须覆盖新增 contract/characterization 目录 | §Testing Plan、T6 |
| Phase 1 schema 缺口只允许 strict xfail，禁止裸 skip 或非 strict xfail | P0-003 测试文件硬要求、§Risk Controls、T7 |

执行前刷新当前基线，避免使用过期文件数：

```bash
for p in src/app/workline src/workline_runtime src/workline_plugins tests/workline_runtime; do
  printf "%s " "$p"
  git ls-files "$p" | wc -l | tr -d " "
done
```

“每个入口”的最小粒度定义：

| 入口类型 | Matrix 粒度 | 发现命令 |
| --- | --- | --- |
| API route | `path + router/function` | `rg -n "APIRouter|@router\\." src/app/workline/v1` |
| Service | `path + class/function` | `rg -n "^class .*Service|^async def |^def " src/app/workline/services src/workline_runtime` |
| Repository | `path + class` | `rg -n "^class .*Repository" src/app/workline/repositories` |
| Model/table | `path + class + __tablename__?` | `rg -n "class .*\\(|__tablename__" src/app/workline/models` |
| Plugin/runtime artifact | `path + exported symbol` | `rg -n "^class |^def |^async def |__all__" src/workline_runtime src/workline_plugins` |
| Test | `path + test function/class` | `rg -n "^class Test|^def test_|^async def test_" tests/workline_runtime tests/workline_plugins` |

完成判定：

1. 禁止抽样。发现命令输出的每个入口都必须进入 `legacy-cleanup-matrix.md`。
2. 如果同一文件有多个 route/function/class，按 symbol 逐条记录，不允许只记录文件。
3. matrix 生成后必须提供 `total_entries_by_type` 汇总，汇总数量必须等于发现命令输出数量。
4. 无法分类的入口必须标记 `classification_status=pending-review`，但 Phase 0 PR merge 前 `pending-review` 数量必须为 0。

Phase 0 交付物：

| Task | 交付物 | 说明 |
| --- | --- | --- |
| P0-001 | `docs/architecture/target-state-contract.md` | 锁定 P0 能力、域边界、状态所有权、允许破坏性删除范围 |
| P0-002 | `docs/architecture/legacy-cleanup-matrix.md` | 逐路径标记 delete / rebuild / move / keep-contract |
| P0-003 | 行为契约测试基线 | 先 characterization 旧业务语义，再按目标态命名 contract tests |
| P0-004 | `docs/architecture/session-correlation-matrix.md` | 逐文件说明跨域 session FK 如何收敛到 `ExecutionCorrelation` |
| P0-005 | `docs/architecture/device-command-contract.md` | ECS 设备接入边界合同 |
| P0-006 | `docs/contracts/external-contract-profile.md`、`docs/architecture/integration-lab-and-simulator.md` | 外部合同、simulator、sandbox、fixture、scenario runner 基线 |
| P0-007 | `docs/architecture/architecture-guardrails-spec.md`、`scripts/architecture-guardrails.sh`、`tests/architecture/` | 自动化架构护栏 |

Phase 0 按 **一个 PR** 交付，完整范围不裁剪。为控制 review 体量，PR 必须拆成 3 个 review packet；每个 packet 在 PR 描述中独立列出文件清单、验收项和验证命令。

| Review packet | 范围 | 完成门禁 |
| --- | --- | --- |
| Packet A: target + inventory | P0-001、P0-002a/b/c、P0-004、`guardrail_seed_scope` inventory | 目标态合同发布；legacy 和 seed 违规入口均有 `entry_id`；`session-correlation-matrix.md` 发布 |
| Packet B: contracts + external boundary | P0-003、P0-005、P0-006 | BC-01/03/04/05/06 有目标态 contract test；BC-05/06 同时有 characterization 输入提取；DeviceCommand 与 external profile 不越界 |
| Packet C: guardrails + quality gate | P0-007 | guardrails 脚本、allowlist、architecture tests、`git-quality-gate`/CI 接入完成 |

单 PR review packet 约束：

1. Packet A 必须先完成；Packet B 和 Packet C 只能引用 Packet A 稳定下来的目标态合同、legacy matrix `entry_id` 和 correlation matrix。
2. Packet B 必须先稳定 `tests/support/`、`docs/architecture/` 和 `tests/fixtures/` 的公共合同；Packet C 最后接入 allowlist、脚本和质量门禁，避免 allowlist 反复漂移。
3. 单 PR merge 前必须满足 Phase 0 全部验收，不允许把“后续 PR 会补齐”作为当前 PR 通过理由。
4. PR 描述必须按 Packet A/B/C 分区，分别列出文件清单、关键 diff、验证命令和剩余风险；reviewer 可按 packet 顺序审查。
5. 若使用逻辑提交，建议至少保持 3 个提交分组：`target+inventory`、`contracts+external-boundary`、`guardrails+quality-gate`；最终是否 squash 由 ship 流程决定。

Phase 0 允许新增文档、测试、fixture、脚本和测试专用 schema 校验代码。测试专用 schema 必须放在 `tests/support/`（例如 `tests/support/external_contract_profile.py`）或 fixture 校验工具目录，**不得**落到 `src/app/<domain>/models/` 等生产 import path；Phase 1 才把这些 schema 升级到生产路径。Phase 0 不允许修改 runtime 生产行为，不允许新增生产 runtime worker，不允许把旧 API 转发到新 runtime。

生产路径硬边界：

1. `src/app/**`、`src/workline_runtime/**`、`src/workline_plugins/**` 在 Phase 0 只作为 inventory、characterization 和 fixture 输入读取，不作为行为改造目标。
2. 任何需要生产 schema、adapter、worker、API route 或 runtime service 的实现，必须进入 Phase 1+ 对应 SPEC，不得塞进 Phase 0 PR。
3. 如果 contract test 因目标态生产 schema 尚未存在而无法完全断言，只能使用 strict xfail 标明 Phase 1 解除条件，不能用 Phase 0 生产代码补洞。

既有资产复用边界：

| 既有资产 | Phase 0 用途 | 明确禁止 |
| --- | --- | --- |
| `docs/architecture/workline-and-plugin-restructuring.md` | Phase 0 目标态、核心不变量、执行顺序的主真源 | 在 Phase 0 子文档中自行扩展新的目标态边界 |
| `src/app/workline/`、`src/workline_runtime/`、`src/workline_plugins/` | legacy inventory、业务事实提取、characterization 输入 | 作为目标态 runtime/plugin 架构基础继续继承 |
| `tests/workline_runtime/`、`tests/workline_plugins/` | 提取业务语义、生成 contract fixture、标记可迁移测试 | 用旧测试覆盖率替代目标态 contract test |
| `src/app/workline/models/inbox.py` | 旧 inbox 行为的 characterization 来源 | 反向决定目标态 `RuntimeInbox` 状态命名；C5 必须使用 `tests/support/runtime_inbox_contract.py` |
| `scripts/git-quality-gate.sh`、`Jenkinsfile` | Packet C 接入 architecture guardrails 的现有质量门禁入口 | 只提供手动脚本、不接入本地门禁和 CI/Jenkins |
| `TODOS.md` WorkLine cleanup 记录 | 历史背景参考 | 作为 Phase 0 阻塞项；本轮清理范围以本 SPEC 和顶层设计为准 |

执行顺序：

1. 先完成 P0-001 目标态合同；后续矩阵、测试和 guardrails 只能引用该合同，不得自行扩展目标态。
2. P0-002 与 P0-004 可并行 inventory，但必须在 P0-003 final 前完成，因为 contract tests 需要引用清理策略和 correlation 目标。
3. P0-005 与 P0-006 可并行，但 `ExternalContractProfile` 不得声明 DeviceCommand 合同未允许的 ECS 字段。
4. P0-007 最后收口，必须把 P0-001 到 P0-006 的约束转成脚本、测试或 review checklist。

BLOCKED 策略：

| 情况 | 处理 | escalation_owner |
| --- | --- | --- |
| 顶层设计与现有代码事实冲突 | SPEC 实现 PR 标记 BLOCKED，提交冲突证据，不自行改目标态 | architecture lead |
| legacy 入口无法分类 | 先写 `classification_status=pending-review`，但 Phase 0 PR merge 前必须归零 | workline 域 owner + architecture lead |
| 旧测试无法提取业务语义 | 标记 `business_semantics=none`、`strategy=delete`，并写明证据 | workline 域 owner |
| 外部合同字段缺来源 | profile 标记 `unsupported_actions`，不得在 simulator 中偷偷支持 | WMS provider rep（WMS 字段）/ device team（ECS 字段） |
| guardrails 误报 | 先补失败样例和 allowlist reason，再决定是否放行 | architecture lead |

`escalation_owner` 字段含义：BLOCKED 出现 24 小时内必须通知对应 owner；owner 不解锁时由 architecture lead 决定退路（降级、延期或重写 SPEC）。具体人选由项目当期分工确定，不在 SPEC 写死姓名。

## Implementation Details

### P0-001 Target State Contract

`target-state-contract.md` 必须从顶层设计抽取可执行合同，不复制完整顶层设计。

必须包含：

| 合同项 | 要求 |
| --- | --- |
| P0 系统能力 | 配置、会话、设备、WMS 反腐、履约、投影、作业对象查询、物料位置查询、平面态势、可恢复 |
| 域边界 | `workline` 只拥有配置；`runtime/orchestration` 拥有执行状态；`wms_integration` 是 ACL；`resource` 维护作业期投影 |
| 状态所有权 | 必须以所有权矩阵明确 WorkLine 配置、Runtime 执行、Handling 业务意图、Resource 投影、Material 作业期实体、Device 事件命令、WMS 外部事实分别归属 |
| Authority Matrix | 摘要或引用主计划/ADR 中的事实权威来源，明确 WMS、ECS/device、WES runtime/resource/material 各自权威边界 |
| Plane 读模型边界 | 锁定前端最小平面态势图只消费 `PlaneSceneView + PlaneSnapshot`，不得直接拼接 resource/material/device/runtime 散表；字段 schema 留到 Phase 3 `plane-read-model-spec.md` |
| 破坏性删除范围 | 旧 API、旧表名、旧 plugin 形态不做兼容目标 |
| 不做清单 | 不复制 WMS 主数据、不替代 WMS 规划、不直接调度 RCS/AGV/CTU、不直连 PLC、不让 Event_Push 响应带动作 |

验收要求：

1. 文档能独立回答 “WES 做什么、不做什么、谁是外部权威、内部各域拥有什么状态”。
2. 不出现 “兼容旧 API/旧表/旧插件” 作为目标。
3. 明确旧代码仅作为业务事实样本和 characterization 输入。
4. Authority Matrix 作为目标态合同的一部分落地，至少覆盖 WMS 主数据/库存/单据、ECS/device 事件命令、WES 作业期投影与 runtime 状态的权威来源。
5. 状态所有权矩阵至少覆盖主计划 P0 验收列出的核心对象：WorkLine 配置、Runtime 执行、Handling 业务意图、Resource 投影、Material 作业期实体、Device 事件命令、WMS 外部事实。
6. Plane 边界作为目标态合同的一部分落地，明确 Phase 0 只锁定 `PlaneSceneView + PlaneSnapshot` 消费边界，不展开 Phase 3 schema。

### P0-002 Legacy Cleanup Matrix

`legacy-cleanup-matrix.md` 必须按实际路径列出处理策略。禁止写 “清理旧代码” 这类泛化条目。

最小 inventory：

| 路径 | 文件数基线 | Phase 0 分类要求 |
| --- | ---: | --- |
| `src/app/workline/` | 89 | 每个入口标记配置保留、执行迁移、业务事实保留、技术残留删除 |
| `src/workline_runtime/` | 50 | 标记业务语义、runtime 事实、plugin SDK、dead code |
| `src/workline_plugins/` | 12 | 标记 characterization 来源或未来删除 |
| `tests/workline_runtime/` | 104 | 标记 characterization、可迁移 contract test、废弃旧形态测试 |
| `docs/templates/workline_plugin/` | 现有模板 | 标记是否随旧 plugin 体系删除 |
| `guardrail_seed_scope` | 命中 seed allowlist 的具体文件或 symbol | 仅登记 C1/C2/C3/R-I3 seed 命中的 callback/rack/handling/resource/wms_integration 等跨域路径，供 allowlist 引用 |

`guardrail_seed_scope` 不是对应域的完整清理矩阵，只为 P0-007 的 seed allowlist 建立可追踪 `legacy_entry_id`。发现范围以 P0-007 seed 表和实际扫描结果为准；每条 seed allowlist 违规必须能反查到本矩阵中的一个 entry，且 `drop_phase` 一致。

矩阵字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `entry_id` | string | yes | 稳定 ID，格式 `legacy:<relative_path>:<symbol_or_route>` |
| `entry_type` | enum | yes | `api_route` / `service` / `repository` / `model` / `plugin` / `runtime_helper` / `test` / `doc_template` / `domain_object` / `value_object` / `utility` / `config_module` / `other` |
| `relative_path` | string | yes | 仓库根目录相对路径 |
| `symbol_or_route` | string | yes | 类、函数、route 或测试名；文件级入口用 `<file>` |
| `current_owner` | enum | yes | `workline` / `workline_runtime` / `workline_plugins` / `callback` / `rack` / `handling` / `device` / `resource` / `wms_integration` |
| `business_semantics` | string | yes | 保留的业务语义，若无则写 `none` |
| `phase4_carrier` | bool | yes | 是否承载 Phase 4 才能重建的业务语义 |
| `classification_status` | enum | yes | `pending-review` / `final`；Phase 0 PR merge 前必须全部为 `final` |
| `strategy` | enum | yes | `delete` / `rebuild` / `move` / `keep-contract` |
| `target_path` | string | conditional | `move` 或 `rebuild` 时必填 |
| `target_capability` | string | conditional | `rebuild` 时必填，例如 `WmsFulfillmentPort.request_transport` 或 `RuntimeInboxService.process_one` |
| `blocking_tests` | string[] | yes | 允许删除或迁移前必须通过的测试 |
| `drop_phase` | enum | yes | `phase1` / `phase2` / `phase3` / `phase4` / `phase5-tech` / `phase5-business` |
| `risk` | enum | yes | `LOW` / `MEDIUM` / `HIGH` |
| `notes` | string | conditional | 当 `entry_type=other` 时必填,说明实际入口类型;其他类型可选 |

处理策略枚举：

| 策略 | 定义 |
| --- | --- |
| `delete` | Phase 5 可删除，无业务语义承载 |
| `rebuild` | 业务语义保留，但按目标态 port/capability 重建 |
| `move` | 可迁入新域，需记录目标路径和依赖条件 |
| `keep-contract` | 保留为 characterization/contract 来源，不作为目标态入口 |

策略判定规则：

| 条件 | strategy | 说明 |
| --- | --- | --- |
| 只服务旧 plugin discovery、旧 fake allocator、旧 context schema | `delete` | 业务语义已由新 contract test 覆盖后可删 |
| 承载粗分机、满箱交换、分拣机入库等业务语义，但 API/数据形态不符合目标态 | `rebuild` | 记录目标 capability/port，不搬旧接口 |
| 属于 WorkLine 配置、manifest activation、SafetyZone 等配置域能力 | `move` 或 `keep-contract` | 若现有代码可作为配置域目标继续存在则 `move`，否则只作合同来源 |
| 旧测试能描述业务语义但依赖旧 runtime/plugin 形态 | `keep-contract` | 只保留到对应目标态 contract test 完成 |
| 跨域 session FK、runtime state、inbox/outbox 等执行状态 | `rebuild` | 目标承载者是 runtime/orchestration |
| 纯文档模板、示例或脚手架且只描述旧 plugin | `delete` | 删除前确保新目标态 docs 已覆盖使用者入口 |

drop_phase 判定规则：

| 条件 | drop_phase |
| --- | --- |
| Phase 1 schema / authority metadata / port contract 落地后即可替换的目标态边界占位 | `phase1` |
| Phase 2 完成后即可删除的旧 runtime 执行入口或转发辅助 | `phase2` |
| Phase 3 安全、HMAC、idempotency、hold/replay 完成后才能删除的恢复/安全相关旧入口 | `phase3` |
| Phase 4 业务能力重建前仍承载 SMT/NG/分拣/满箱交换语义 | `phase4` |
| Phase 5 技术清理可删，业务语义已经由 contract tests 覆盖 | `phase5-tech` |
| Phase 5 业务确认后才能删，原因是现场流程或外部合同仍需人工确认 | `phase5-business` |

验收要求：

1. 每个旧入口都有且只有一个主策略。
2. 标记是否承载 Phase 4 业务语义。
3. 标记删除前置条件和对应 contract tests。
4. `delete` 项必须有 `business_semantics=none` 或对应 `blocking_tests`。
5. `rebuild` / `move` 项必须写 `target_path` 或 `target_capability`，不能只写 “new runtime”。

### P0-003 Behavior Contract Baseline

行为契约测试不复制旧 plugin 接口、旧 context schema 或 fake allocator。执行顺序是：

1. 从旧测试和旧 runtime 提取业务语义 characterization。
2. 用目标态命名重写 contract tests。
3. 将旧形态依赖标记为 `keep-contract` 或后续删除。

必须覆盖的业务语义：

| ID | 场景 | 目标态断言 | 推荐测试名 |
| --- | --- | --- | --- |
| BC-01 | Start admission | manifest 有效、设备角色满足、外部 port 可用、active projection 无阻塞冲突 | `test_start_admission_rejects_invalid_manifest_or_blocked_projection` |
| BC-02 | Runtime snapshot | active session 可查询 state、timeline、inbox、hold、pending intent、correlation | `test_runtime_snapshot_exposes_state_timeline_inbox_hold_intent_correlation` |
| BC-03 | Handoff | 交接只能由 callback 或 RuntimeIntentLog evidence 推进 | `test_handoff_requires_callback_or_intent_evidence` |
| BC-04 | Resource projection | 同一 object 在同一 WorkLine 内只有一个可解释 active 归属 | `test_active_object_has_single_workline_ownership` |
| BC-05 | 粗分机正常入库 | 扫码、识别、WMS 校验、箱格分配/预约、滚筒线路由语义被保护 | `test_rough_sorter_inbound_happy_path_contract` |
| BC-06 | 满箱交换前置分流 | 满箱/换箱/换架必须按外部履约 + 对账闭环建模 | `test_full_box_exchange_uses_fulfillment_and_reconciliation_contract` |
| BC-07 | 分拣机入库 | 对象级流水并发、NG、投箱、完成语义被保护 | `test_sorter_inbound_object_level_pipeline_contract` |
| BC-08 | 缺 `event_id` 的离散事件 | 可以 ACK，但不得推进 session 归属 | `test_event_without_event_id_acknowledged_but_not_correlated` |
| BC-09 | WMS 短缓存 | query 可短缓存，但不得改变 WMS 权威 | `test_wms_query_cache_preserves_authority_metadata` |
| BC-10 | Event_Push 响应 | HTTP 响应只 ACK，不含动作指令 | `test_event_push_response_rejects_command_like_fields` |

每个 BC 的 fixture、expected output 和 mock 边界：

| ID | Fixture input | Expected output | Mock 边界 |
| --- | --- | --- | --- |
| BC-01 | `tests/fixtures/workline_contract/start_admission/*.json`，包含 manifest、device roles、active projection、WMS/ECS 可用性 | admission decision、拒绝原因码、未创建 session/intent | mock WMS query port、ECS status port、resource projection query；不 mock admission decision |
| BC-02 | `runtime_snapshot/session_with_inbox_hold_intent.json` | snapshot 含 state、timeline、inbox、hold、pending intent、correlation | mock repository/query 返回；不 mock response assembler 字段裁剪 |
| BC-03 | `handoff/callback_evidence.json` 与 `handoff/no_evidence.json` | 有 callback 或 intent evidence 时推进；无 evidence 时 HOLD/拒绝 | mock evidence store；不 mock handoff policy |
| BC-04 | `resource_projection/duplicate_active_owner.json` | 同一 object 同一 WorkLine 内第二个 active 归属失败，瞬态窗口有解释 | mock time source；不 mock uniqueness/policy 判断。瞬态窗口语义对齐主计划 §2.2/§6.6：同一 object 跨投影重复归属在 `transient_until` 时间戳前视为合法瞬态（旧 owner 未释放、新 owner 已 claim 的物理时序），超时进入 `RECONCILING`。Phase 0 此字段占位 `transient_until=PHASE1_PENDING`，具体阈值（主计划 §6.6 暂取 N=30 秒）由 Phase 1 RuntimeIntentLog/projection spec 给出 |
| BC-05 | `rough_sorter_inbound/happy_path.json` | 扫码、识别、WMS 校验、箱格分配/预约、滚筒线路由意图顺序正确 | mock WMS/ECS/resource ports；不 mock capability orchestration |
| BC-06 | `full_box_exchange/pre_diversion.json` | 满箱/换箱/换架生成外部履约、等待回调、对账 evidence，不本地冒充完成 | mock WMS fulfillment port；不 mock reconciliation trigger |
| BC-07 | `sorter_inbound/object_pipeline.json` | 对象级并发、NG、投箱、完成结果按 work item 隔离 | mock device callbacks；不 mock work item 状态推进 |
| BC-08 | `device_event/missing_event_id.json` | HTTP ACK；不创建/推进 `ExecutionCorrelation` 或 session ownership | mock callback auth 成功；不 mock correlation guard |
| BC-09 | `wms_cache/query_cache_hit.json` | cache hit response 仍含 `authority=WMS`、`source`、`evidence_at` | mock provider response 与 cache clock；不 mock authority metadata validator |
| BC-10 | `event_push/command_like_response.json` | response schema validation fails 或拦截 command-like 字段 | mock handler result；不 mock response guard |

测试路径和 fixture 来源：

| 类别 | 目标路径 | Fixture 来源 |
| --- | --- | --- |
| 目标态 contract tests | `tests/contracts/workline/` | 从旧测试提取业务输入后重命名为目标态 fixture |
| characterization tests | `tests/characterization/workline_legacy/` | `tests/workline_runtime/`、`tests/workline_plugins/`、`src/workline_plugins/*/manifest.yaml` |
| architecture tests | `tests/architecture/` | 手写最小失败样例，不依赖生产数据 |

70% 基线计算方式：Phase 2 go/no-go 前，BC-01 到 BC-10 至少 7 项必须有目标态 contract test 或 characterization fixture；BC-01、BC-03、BC-04、BC-05、BC-06 为强制 contract test 项，BC-07（分拣机入库）为强制 characterization fixture 项，其余 BC（BC-02、BC-08、BC-09、BC-10）至少补到累计 7 项；强制项不可用其他项替代。

测试文件硬要求：

| 文件 | 内容 | 失败/跳过策略 |
| --- | --- | --- |
| `tests/contracts/workline/test_start_admission_contract.py` | BC-01 | 必须通过；不可 skip |
| `tests/contracts/workline/test_runtime_snapshot_contract.py` | BC-02 | Phase 0 可标记 expected-fail，仅当缺 Phase 1 schema 时允许 |
| `tests/contracts/workline/test_handoff_contract.py` | BC-03 | 必须通过；不可 skip |
| `tests/contracts/workline/test_resource_projection_contract.py` | BC-04 | 必须通过；不可 skip |
| `tests/characterization/workline_legacy/test_rough_sorter_inbound_characterization.py` | BC-05 输入提取 | 必须通过或明确标记旧依赖缺失证据 |
| `tests/contracts/workline/test_rough_sorter_inbound_contract.py` | BC-05 目标态合同 | contract 壳必须存在；缺 Phase 1 schema 时仅允许 strict xfail |
| `tests/characterization/workline_legacy/test_full_box_exchange_characterization.py` | BC-06 输入提取 | 必须通过或明确标记旧依赖缺失证据 |
| `tests/contracts/workline/test_full_box_exchange_contract.py` | BC-06 目标态合同 | contract 壳必须存在；缺 Phase 1 schema 时仅允许 strict xfail |
| `tests/characterization/workline_legacy/test_sorter_inbound_characterization.py` | BC-07 输入提取 | Phase 0 可 pending，但必须有 fixture draft |
| `tests/contracts/workline/test_external_event_contract.py` | BC-08、BC-10 | 必须通过；不可 skip |
| `tests/contracts/wms_integration/test_authority_cache_contract.py` | BC-09 | 必须通过；不可 skip |

允许的 `xfail` 仅限目标态 schema 尚未实现的 Phase 1 依赖，必须使用 `pytest.mark.xfail(strict=True, reason="Phase 1 ...; unblock when ...")`，写明对应 Phase 1 task 和解除条件。不得使用裸 `skip` 隐藏未实现的强制 BC。

验收要求：

1. 新 contract tests 不依赖 `src/workline_plugins/` 作为目标态入口。
2. 测试名表达业务语义，不表达旧 plugin 实现细节。
3. 关键业务语义覆盖率达到 Phase 2 go/no-go 要求的 70% 基线。

### P0-004 ExecutionCorrelation Migration Matrix

`session-correlation-matrix.md` 必须逐文件列出跨域 session FK 的处理方式。

矩阵字段：

| 字段 | 说明 |
| --- | --- |
| `source_path` | 当前引用 session FK 的文件 |
| `current_symbol_or_table` | 当前符号、字段或表 |
| `owner_domain` | 当前状态真实 owner |
| `target_reference` | `ExecutionCorrelation.correlation_id`、runtime 内部 `execution_session_id` 或删除 |
| `phase` | Phase 1/2/3/5 |
| `migration_action` | add / replace / delete / keep-in-runtime |
| `risk` | LOW / MEDIUM / HIGH |

`ExecutionCorrelation` Phase 1 目标 schema 草案，供矩阵引用。字段对齐主计划 §4.1/§4.2/§9.2；idempotency 不并入本表，按主计划 §5.4 独立 `idempotency_keys` 表通过 `execution_correlation_id` 引用本表：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | bigint | yes | 内部主键 |
| `correlation_id` | string(120) | yes | 跨域稳定 correlation key，唯一 |
| `execution_session_id` | bigint | nullable | runtime/orchestration 内部强 FK；跨域不得引用 |
| `trace_id` | string(120) | yes | 跨域 trace 时间线 |
| `source_event_id` | string(160) | nullable | 外部事件归因（request_id / event_id / command_code） |
| `business_owner_key` | string(160) | nullable | 业务 owner 审计、查询和冲突定位 |
| `created_at` | datetime | yes | naive UTC for DB |
| `updated_at` | datetime | yes | naive UTC for DB |

`idempotency_keys` 独立表（主计划 §5.4）：PRIMARY KEY `(provider_code, operation_kind, idempotency_key)`，含 `request_hash`、`execution_correlation_id`（引用本表 `correlation_id`）、`business_owner_key`、`created_at`（TTL 30 天）。同 key 同 hash 返回既有 record，同 key 不同 hash 返回 409 + 安全审计。`ExecutionWorkItem` 通过自身 `correlation_id` 字段引用本表，不在本表冗余 `work_item_id`。

验收要求：

1. 跨域 FK 必须收敛为 correlation key。
2. runtime/orchestration 内部可以保留 `execution_session_id`，但跨域只能通过 `ExecutionCorrelation`。
3. 高风险迁移项必须进入 Phase 1 或 Phase 2 SPEC 的风险表。

### P0-005 Device Command Contract

`device-command-contract.md` 必须以第三方设备白皮书为权威输入。

必须锁定：

| 合同项 | 要求 |
| --- | --- |
| 命令下发 | WES 调用 ECS `Receive Command` |
| 同步响应 | 只表示收到/接受，不代表任务完成 |
| 异步结果 | ECS 调用 WES callback result/event |
| Event_Push 响应 | 固定 ACK，不允许 action/command-like 字段 |
| 设备状态 | `IDLE/RUNNING/ERROR/OFFLINE/UNKNOWN/MAINTENANCE`（完整 6 态见主计划 §9.6 M6 回归） |
| 设备准入 | dispatch 前实时确认 ECS IDLE；`UNKNOWN`/`MAINTENANCE` 不得下发 |
| RUNNING | 有界等待 |
| ERROR/OFFLINE/UNKNOWN/timeout | 短退避后 RuntimeHold；`MAINTENANCE` 跳过该设备 |
| 禁止字段 | PLC、物理坐标、关节角度、安全回路 |

验收要求：

1. DeviceCommand 字段白名单可被 `architecture-guardrails.sh` 扫描。
2. `data.event_id` 缺失时，不允许推进异步链路归属。
3. 不建立 WES 直连 PLC 的任何字段或 adapter。

### P0-006 External Contract Profile and IntegrationLab

本 SPEC 合并顶层设计触发清单里的 `external-contract-profile-spec.md` 与 `integration-lab-and-simulator-spec.md`。

`docs/contracts/external-contract-profile.md` 必须定义：

```yaml
provider_code: WMS
contract_version: "2026-06-25"
environment: sandbox
runtime_capabilities:
  query:
    - WmsMasterDataPort.get_material
    - WmsInventoryQueryPort.query_inventory
  effect:
    - WmsFulfillmentPort.request_transport
inbound_normalizers:
  event:
    - WMS_GRN_RECEIVED
    - WMS_TRANSPORT_COMPLETED
  result: []
field_mapping:
  WMS_GRN_RECEIVED:
    source_event_id: data.event_id
    external_ref: data.grn_id
timeout_retry:
  query_timeout_seconds: 10
  retry_backoff_seconds: [1, 2, 4]
  cache_ttl_seconds: 30
fixture_set:
  path: tests/fixtures/external_contracts/wms/default
unsupported_actions:
  - direct_rcs_dispatch
```

schema 校验方式：

| 校验项 | 要求 |
| --- | --- |
| 格式 | YAML 或 JSON 均可，但必须由 Pydantic model 校验 |
| Phase 0 测试 model 路径 | `tests/support/external_contract_profile.py`（仅供 fixture 校验与 contract tests import；**禁止**`src/app/` 下任何模块 import） |
| Phase 1 生产 model 路径 | `src/app/wms_integration/models/external_contract_profile.py`（Phase 1 CEO-013 实施，从 `tests/support/` 升级；Phase 0 不创建此文件） |
| fixture 校验 | 每个 fixture 必须声明 `provider_code`、`contract_version`、`case_id`、`expected_port` |
| unsupported action | provider 未声明能力时，runtime capability 和 callback API 必须拒绝 |

`ExternalContractProfile` 字段表：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `provider_code` | string | yes | 稳定 provider ID，如 `WMS`、`ECS` |
| `contract_version` | string | yes | 合同版本，建议 ISO date 或 semver |
| `environment` | enum | yes | `sandbox` / `staging` / `production`；Phase 0 fixture 只能用 `sandbox` |
| `runtime_capabilities.query` | string[] | yes | 只能列 query port method，例如 `WmsMasterDataPort.get_material` |
| `runtime_capabilities.effect` | string[] | yes | 只能列 effect port method，例如 `WmsFulfillmentPort.request_transport` |
| `inbound_normalizers.event` | string[] | yes | provider 允许的 event type |
| `inbound_normalizers.result` | string[] | yes | provider 允许的 result/callback type |
| `field_mapping` | object | yes | event/result 到 typed envelope 字段的映射 |
| `timeout_retry.query_timeout_seconds` | int | yes | query 超时，必须大于 0 |
| `timeout_retry.effect_timeout_seconds` | int | conditional | effect port 存在时必填 |
| `timeout_retry.retry_backoff_seconds` | int[] | yes | 递增短退避数组 |
| `timeout_retry.cache_ttl_seconds` | int | conditional | query cache 存在时必填；0 表示禁用 |
| `fixture_set.path` | string | yes | `tests/fixtures/external_contracts/<provider>/<profile>` |
| `fixture_set.required_cases` | string[] | yes | 至少覆盖 success、reject、timeout、duplicate、missing_event_id 中适用场景 |
| `unsupported_actions` | string[] | yes | 未支持动作，例如 `direct_rcs_dispatch` |
| `security_profile` | object | optional | Phase 0 只允许占位，不展开 HMAC canonical |
| `notes` | string | optional | 仅记录合同解释，不允许写实现 workaround |

fixture schema：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `case_id` | string | yes | 稳定测试用例 ID |
| `provider_code` | string | yes | 必须与 profile 一致 |
| `contract_version` | string | yes | 必须与 profile 一致 |
| `expected_port` | string | yes | `Port.method` |
| `direction` | enum | yes | `query` / `effect` / `event` / `result` |
| `raw_request` | object | conditional | 出站 query/effect 必填 |
| `raw_response` | object | conditional | 出站 query/effect 必填 |
| `raw_callback` | object | conditional | 入站 event/result 必填 |
| `expected_typed` | object | yes | adapter/normalizer 的 typed output |
| `expected_error` | object | optional | reject/timeout/invalid case 的错误模型 |

`docs/architecture/integration-lab-and-simulator.md` 必须定义：

| 能力 | 要求 |
| --- | --- |
| WMS simulator | P0/P1 WMS query/effect/callback fixture |
| ECS simulator | status、Receive Command、result/event callback fixture |
| sandbox provider profile | 仅用于联调和测试，不进入生产 fallback |
| scenario runner | 支持正常流、拒绝、超时、重复事件、缺 event_id |
| 环境隔离 | simulator/sandbox 不允许被 production profile 引用 |

验收要求：

1. simulator 和 sandbox 只能走正式 port contract。
2. 不允许业务代码直接依赖 simulator 实现。
3. fixture 可被 adapter contract tests 复用。

### P0-007 Architecture Guardrails

`architecture-guardrails-spec.md` 必须将以下 17 条不变量映射到脚本、测试或 review checklist，避免实现者回读顶层设计才能执行。

核心 5 条自动检查：

| ID | 检查目标 | 自动化入口 |
| --- | --- | --- |
| C1 | 内部域不得 import WMS DTO/client/provider 字段 | `scripts/architecture-guardrails.sh` import scan |
| C2 | 跨域 session FK 收敛为 `ExecutionCorrelation` | schema lint + FK 引用扫描 |
| C3 | 查询响应强制 `scope/authority/source/evidence_at` | schema 校验测试 |
| C4 | DeviceCommand 不含 PLC/坐标/关节/安全回路字段 | 字段白名单扫描 |
| C5 | RuntimeInbox 状态机契约 | `tests/architecture/` 状态机测试 |

C2 schema lint 目标：

| 检查 | 允许 | 失败 |
| --- | --- | --- |
| `execution_session_id` 字段 | `src/app/runtime/orchestration/**` 内部表、`ExecutionCorrelation` nullable 反向引用 | `src/app/resource/**`、`src/app/workline/**`、`src/app/wms_integration/**` 等跨域表强 FK |
| `session_id` 字段 | legacy 文件在 `legacy-cleanup-matrix.md` 标记且 `drop_phase` 已记录 | 新增目标态跨域 schema 或 API response 暴露裸 session FK |
| FK 约束名 | runtime/orchestration 内部 `fk_runtime_*` | 跨域 `fk_*execution_session*` |

C3 最小目标 schema：

| 类型 | 字段 | 规则 |
| --- | --- | --- |
| `AuthorityMetadata` | `scope`, `authority`, `source`, `evidence_at` | 四字段必填，`evidence_at` 使用 aware ISO response 或 DB evidence 转换后的 ISO |
| Query response model | 直接包含或组合 `AuthorityMetadata` | WMS/global/query projection response 缺任一字段即失败 |
| Local config response | 可豁免 | 必须在 allowlist 标明 `scope=WORKLINE_LOCAL` 或不属于跨权威查询 |

C5 最小状态机转移：

| From | To | 条件 |
| --- | --- | --- |
| `RECEIVED` | `PROCESSING` | claim 成功并写 lease |
| `PROCESSING` | `PROCESSED` | handler 完成且 intent/evidence 写入成功 |
| `PROCESSING` | `FAILED` | 可重试异常，记录 `retry_count` 与 `next_retry_at` |
| `FAILED` | `RECEIVED` | 到达 `next_retry_at` 且未超过最大重试 |
| `FAILED` | `DEAD_LETTER` | 超过最大重试或不可重试错误 |
| `PROCESSING` | `RECEIVED` | `lease_until` 过期，允许 crash replay |

Phase 0 的 C5 测试使用 `tests/support/runtime_inbox_contract.py` 或等价测试专用模型表达目标态合同，不 import legacy `src.app.workline.models.inbox.WorklineInbox`。旧 `WorklineInbox` 的 `NEW/RETRY/PROCESSING` 只可作为 characterization 来源，不得反向决定目标态 `RuntimeInbox` 状态命名。

I3 capability 注入扫描范围：

| 扫描路径 | 禁止模式 | 说明 |
| --- | --- | --- |
| `src/app/runtime/**` | `http_client`, `service_locator`, `WmsEventPort`, `DeviceEventPort`, `RuntimeInbox` | capability 不得拿入站 normalizer 或底层实现 |
| `src/app/workline/**` | `from src.app.wms_integration.services`, `from src.app.device.services` | 目标态业务 capability 只能依赖 port contract |
| `tests/**` | 允许 fixture/mock import | 仅测试文件允许，但不得成为生产 fallback |

重要 8 条 Phase 门禁检查：

| ID | 检查目标 |
| --- | --- |
| I1 | callback HMAC + nonce TTL + path canonical 在 Phase 3 前有 SPEC 占位和门禁 |
| I2 | idempotency 复合键和 hash 冲突 409 |
| I3 | capability 注入只能暴露 port contract |
| I4 | Event_Push 只能 ACK |
| I5 | manifest version pin |
| I6 | DeviceCommand dispatch 前 ECS IDLE |
| I7 | 位置投影只能来自 evidence/RuntimeLocationEvent |
| I8 | Event_Push 响应拦截 command-like 字段 |

设计 4 条 review 检查：

| ID | 检查目标 |
| --- | --- |
| D1 | 目标态契约优先，旧 API / 旧表 / 旧插件形态不得反向约束新架构 |
| D2 | B 方案以目标态边界 + 行为契约测试 + 破坏性清理清单为前置 |
| D3 | plane 接口不允许全员可读全量运营数据 |
| D4 | 当前阶段 RCS/AGV/CTU 调度只能经 WMS 履约 port；直连能力仅作条件触发扩展，必须通过 provider adapter 替换，不允许内部域直连 SDK |

脚本最小扫描规则：

| Rule | 命令形态 | 失败条件 |
| --- | --- | --- |
| C1 | `rg -n "(from src\\.app\\.wms_integration\\.(services|models|clients|providers).* import|import src\\.app\\.wms_integration\\.(services|models|clients|providers))" src/app --glob '!src/app/wms_integration/**'` | 内部域 import WMS implementation/DTO/client/provider |
| C2 | 扫描 `session_id` FK 字段并排除 runtime/orchestration 内部白名单 | 跨域表直接 FK 到 execution session |
| C3 | Pydantic schema test | query response 缺 `scope/authority/source/evidence_at` |
| C4 | `rg -n "(plc|coordinate|joint|axis|x_coord|y_coord|safety_loop)" src/app/device src/app/workline src/app/runtime` | DeviceCommand/manifest/runtime schema 出现禁止字段 |
| C5 | 状态机测试 | RuntimeInbox 无法覆盖 retry/dead-letter/replay |
| R-I3a | `rg -n "(http_client\|service_locator\|WmsEventPort\|DeviceEventPort\|RuntimeInbox\|WmsClientException\|DeviceClientException\|.*Dto$)" src/app/runtime src/app/workline` | capability 注入禁用关键词:HTTP client/service locator/inbound port/inbox consumer/provider exception/DTO |
| R-I3b | `rg -n "from src\\.app\\.(wms_integration\|device)\\.(services\|models)\\..* import" src/app/runtime src/app/workline` | capability 实施 import 了 `wms_integration` / `device` 的 services/models 实现 |

R-I3a 与 R-I3b 共同覆盖顶层设计 §7.5 I3 不变量声明的全部禁用对象（`wms_integration` / `device` 实现对象、HTTP client、DTO、provider exception、service locator、`WmsEventPort`、`DeviceEventPort`、`RuntimeInbox` consumer）。两条规则必须同时通过才算 I3 合规。

白名单要求：所有白名单必须写在 `scripts/architecture-guardrails.allowlist`，包含 `rule_id`、`path`、`reason`、`expires_at`、`legacy_entry_id`（关联 `legacy-cleanup-matrix.md` 的 `entry_id`，便于随 Phase 5 清理自动过期）。无过期时间的白名单视为失败。

allowlist 校验必须是脚本自身的一部分，不允许只靠 reviewer 人工核对：

1. `legacy_entry_id` 必须能在 `legacy-cleanup-matrix.md` 中找到。
2. allowlist 行的 `drop_phase` 必须与 matrix 中同一 `entry_id` 的 `drop_phase` 一致。
3. `expires_at` 必须存在且可解析；过期行在 `phase1` 先 warning，在 `phase2+` 失败。
4. 删除任意一个 seed allowlist 行后，`--phase phase1` 必须对对应历史违规返回非零，证明 enforcement 不是空跑。

#### Phase 0 Enforcement Mode

`scripts/architecture-guardrails.sh` 必须支持 `--phase` 参数。Phase 0 的目标是建立可阻塞机制、失败样例和 seed allowlist；历史违规通过 allowlist 可追踪放行，新增目标态违规不得进入 allowlist。Phase 1 起按主计划 §7.5 的 “核心 5 条违反立即阻塞 PR” 语义执行 enforced 模式。

Phase 0 实施 PR 默认运行 `--phase phase0`，行为如下：

| Phase | 模式 | 违规处理 | 退出码 | CI 阻塞 |
| --- | ---: | --- | ---: | --- |
| `phase0` | warn-only | 打印所有违规但不退出非零 | 0 | 否 |
| `phase1` | enforced | allowlist 之外的 C1/C2/C3/C4/C5/R-I3a/R-I3b 违规即失败 | 1 | 是（PR 不可合） |
| `phase2`+ | enforced + 缩减 allowlist | 同 phase1，并要求每个 PR 至少消除一条 expired allowlist 项 | 1 | 是 |

Phase 0 实施 PR 必须随脚本提交 `scripts/architecture-guardrails.allowlist` 的 **seed 版本**，预填以下当前已知违规，确保 Phase 1 切到 `enforced` 模式时不会因历史包袱直接 fail。seed allowlist 只覆盖 Phase 0 已登记的 legacy 事实，不能作为新增代码的豁免机制：

| 期望 seed 条目 | 来源扫描 | 违规位置（截至 develop @ 2026-06-25） | 备注 |
| --- | --- | --- | --- |
| `rule_id=C1` × 5 | C1 import scan | `src/app/callback/services/callback_ingress_service.py:30`、`src/app/rack/services/gateway.py:7`、`src/app/handling/services/gateway.py:8`、`src/app/workline/services/single_layer_rack_orchestration_service.py:22`、`src/app/workline/repositories/debug_data_cleanup_repository.py:1046` | `legacy_entry_id` 必须指向 legacy-cleanup-matrix 对应行；`drop_phase` 由矩阵给出 |
| `rule_id=C2` × N | C2 cross-domain session FK | `src/app/resource/services/projection_service.py`、`src/app/resource/services/projection_integrity_service.py`、`src/app/wms_integration/services/transport_contract.py` 等 `workline_session_id` / `material_session_id` 引用 | 全部归属 Phase 1 `ExecutionCorrelation` 落地后清理，`drop_phase=phase2` |
| `rule_id=C3` | C3 schema authority | 现有 query response 若缺 `scope/authority/source/evidence_at`，按 legacy-cleanup-matrix 标 `drop_phase=phase1` 或 `phase2` | 由 P0-002 inventory 时确定具体条目 |
| `rule_id=R-I3a` / `R-I3b` | I3 capability 注入 | Phase 0 时 `src/app/runtime/` 不存在，预期 0 命中；如果 P0-002 在 `src/app/workline/` 内发现新命中需补 allowlist | 同上 |

Phase 0 完成门禁不要求 allowlist 数量归零；只要求所有 seed 条目都引用了存在的 `legacy_entry_id` 且 `drop_phase` 与 legacy-cleanup-matrix 一致。

失败输出格式：

```text
[C1] Forbidden WMS import
file: src/app/runtime/orchestration/example.py
line: 12
reason: internal domain imported WMS implementation
fix: depend on WmsMasterDataPort contract instead
```

验收要求：

1. `scripts/architecture-guardrails.sh --phase phase0` 可本地运行且退出码为 0（warn-only），即使存在 seed 中列出的真实违规。
2. `scripts/architecture-guardrails.sh --phase phase1` 在 seed allowlist 覆盖下亦退出码为 0；删掉 seed 任意一行后必须失败，证明 enforcement 真正生效。
3. 脚本输出包含检查项 ID、失败文件、失败原因、修复提示。
4. 后续 Phase 门禁能直接调用该脚本并以 `--phase` 切换模式。
5. `scripts/git-quality-gate.sh` 增加 `--check architecture`；Packet C 更新 CI/Jenkins 质量门禁调用，确保 Phase 0 默认跑 `--phase phase0`，Phase 1 起可切到 enforced 模式。
6. Phase 0 PR 必须在 Packet C 的 PR 描述中提交至少一条“临时删除 seed 行触发失败”的本地验证记录，避免脚本只验证 happy path。

## Acceptance Criteria

1. `target-state-contract.md` 发布，不含旧 API、旧表或旧 plugin 兼容承诺，并包含 Authority Matrix 摘要或明确引用主计划/ADR 的权威来源矩阵；状态所有权覆盖 WorkLine / Runtime / Handling / Resource / Material / Device / WMS facts；Plane 边界锁定为 `PlaneSceneView + PlaneSnapshot`。
2. `legacy-cleanup-matrix.md` 发布，旧 WorkLine/plugin/runtime 每个入口都有处理策略。
3. 行为契约测试可运行，保护业务语义而非旧代码形态。
4. 粗分机正常入库、满箱交换前置分流必须以目标态 contract test 表达；分拣机入库（BC-07）以 characterization fixture draft + 测试草稿表达，Phase 1 完成 RuntimeIntentLog schema 后升级为 contract test。
5. `session-correlation-matrix.md` 发布。
6. `device-command-contract.md` 发布，字段与白皮书 Command -> Ack -> Callback 一致。
7. `external-contract-profile.md` 与 `integration-lab-and-simulator.md` 发布；Pydantic schema 落在 `tests/support/external_contract_profile.py`，未污染生产 import path。
8. `architecture-guardrails-spec.md` 发布，并映射 §7.5 核心 5 条不变量和 I3 capability 注入/import 边界（拆为 R-I3a + R-I3b）。
9. `scripts/architecture-guardrails.sh --phase phase0` 可本地运行且退出码为 0（warn-only）；`--phase phase1` 在 seed allowlist 覆盖下亦退出码为 0，删掉任意 seed 行必须失败。
10. `scripts/architecture-guardrails.allowlist` seed 提交，所有条目均关联 legacy-cleanup-matrix 的 `entry_id` 与 `drop_phase`。
11. `scripts/git-quality-gate.sh --check architecture` 和 CI/Jenkins 对应步骤已接入 guardrails。
12. Phase 0 不展开 Phase 3 的 11 态机完整转移表、HMAC canonical、Plane schema、ReconciliationManager 触发矩阵。

## Risk Controls

Phase 0 不改生产 runtime，但会决定后续删除和重建边界。以下风险必须在文档、测试或 guardrails 中有明确门禁：

| 风险 | 失效表现 | Phase 0 门禁 |
| --- | --- | --- |
| legacy inventory 漏项 | 后续删除隐藏业务流，Phase 4 重建缺语义输入 | `total_entries_by_type` 必须等于发现命令输出；禁止抽样 |
| seed allowlist 悬空 | allowlist 引用不存在的 legacy 入口，过期机制失效 | 每条 seed 必须有 `legacy_entry_id`，并能反查 `guardrail_seed_scope` 或 legacy matrix |
| C5 绑定旧 inbox | 目标态测试实际验证旧 `NEW/RETRY/PROCESSING` 模型 | C5 只能 import `tests/support/runtime_inbox_contract.py`，不得 import legacy `WorklineInbox` |
| BC-05 粗分机语义丢失 | 重建后路由/分配语义缺失但测试仍绿 | characterization fixture + 目标态 contract test 壳同时存在 |
| BC-06 满箱交换越权完成 | 本地代码在无 callback/evidence 时标记外部履约完成 | contract 必须断言 external fulfillment + reconciliation evidence 语义 |
| guardrails 未进门禁 | 脚本存在但合并前没人运行 | `./scripts/git-quality-gate.sh --check architecture` 和 CI/Jenkins 步骤必接入 |
| 非 strict xfail 长期误绿 | Phase 1 schema 实现后测试仍被 xfail 吞掉 | Phase 0 允许的 xfail 必须 `strict=True` 并写解除条件 |

## Testing Plan

| Layer | What | Count |
| --- | --- | ---: |
| Documentation | 校验 7 个 Phase 0 新增文档是否包含要求章节和字段表 | +7 |
| Architecture | `scripts/architecture-guardrails.sh` happy path + 失败样例（每个 rule 一份失败 fixture） | +7 |
| Contract | start admission、runtime snapshot、handoff、resource projection、rough sorter inbound、full-box exchange、external event、authority cache | +8 |
| Characterization | 粗分机正常入库、满箱交换前置分流、分拣机入库（draft） | +3 |
| Regression | 现有 `tests/workline_runtime/` 关键业务测试迁移标记不丢失 | +1 inventory check |

覆盖矩阵：

```text
CODE PATHS / CONTRACTS                              REVIEWER / OPERATOR FLOWS
[+] Packet A target + inventory                     [+] Architecture reviewer validates scope
  ├── [planned] single PR review packet structure     ├── [planned] total_entries_by_type check
  ├── [planned] legacy matrix counts                  └── [planned] guardrail_seed_scope traceability
  └── [planned] session correlation matrix

[+] Packet B behavior contracts                     [+] Runtime behavior remains protected
  ├── [planned] BC-01 start admission                 ├── [planned] invalid manifest / blocked projection
  ├── [planned] BC-03 handoff evidence                ├── [planned] no callback / no intent evidence
  ├── [planned] BC-04 active ownership                ├── [planned] duplicate active owner
  ├── [planned] BC-05 contract + characterization     ├── [planned] rough sorter happy path
  ├── [planned] BC-06 contract + characterization     ├── [planned] full-box exchange waits on evidence
  ├── [planned] BC-07 characterization draft          └── [planned] sorter inbound fixture draft
  ├── [planned] BC-08 missing event_id ACK-only
  ├── [planned] BC-09 WMS authority cache
  └── [planned] BC-10 Event_Push ACK-only

[+] Packet C guardrails                             [+] Maintainer runs quality gate
  ├── [planned] architecture guardrails in local gate ├── [planned] `--check architecture`
  ├── [planned] phase0 warn-only                      └── [planned] CI/Jenkins architecture step
  ├── [planned] phase1 enforced with seed allowlist
  └── [planned] strict xfail for Phase 1 schema gaps
```

建议命令：

```bash
uv run pytest tests/architecture/ tests/contracts/ tests/characterization/workline_legacy/ tests/workline_runtime/ tests/resource/ tests/wms_integration/
sh scripts/architecture-guardrails.sh --phase phase0
sh scripts/architecture-guardrails.sh --phase phase1   # seed allowlist 必须让其通过
./scripts/git-quality-gate.sh --check architecture
rg -n "xfail" tests/contracts tests/architecture | rg -v "strict=True"  # Phase 0 contract xfail 不允许命中
```

## Rollback Plan

Phase 0 主要产物是文档、测试和脚本。回滚以 revert 对应 PR 为主。

如果 guardrails 脚本误报，先在同一 PR 中补失败样例和豁免说明，不允许直接删除检查项。若确需临时豁免重要 8 条，必须记录 architecture lead 批准和 follow-up issue。

## Effort Estimate

| Task | Effort | CC + gstack 估计 | 说明 |
| --- | --- | --- | --- |
| P0-001 target-state contract | M | 0.5 天 | 抽取顶层设计可执行合同 |
| **P0-002a** legacy matrix: workline + workline_runtime | L | 1.5-2 天 | git ls-files 共 139 个文件，预估 300+ entry |
| **P0-002b** legacy matrix: workline_plugins + tests | M | 0.5-1 天 | git ls-files 共 116 个文件，预估 130+ entry |
| **P0-002c** legacy matrix: docs/templates + 收口审查 | S | 0.5 天 | 模板入口 + 全表一致性 / pending-review 归零校验 |
| P0-003 behavior contract baseline | L | 1-1.5 天 | 10 个 BC，5 强制 contract + 1 强制 characterization |
| P0-004 correlation matrix | L | 0.5-1 天 | 跨域 FK 逐文件迁移路径 |
| P0-005 device command contract | M | 0.5 天 | 依赖白皮书锚点 |
| P0-006 external profile + integration lab | M | 0.5-1 天 | Pydantic schema 放 `tests/support/` |
| P0-007 architecture guardrails | M | 1-1.5 天 | 含 phase-aware mode 与 seed allowlist |

总计：CC + gstack 约 **6.5-9 天**（P0-002 拆 sub-package 后下限提高；单 PR 通过 Packet A/B/C 控制 review 体量）。人工团队约 **3-4 周**，取决于 review、旧业务语义确认和逐入口 inventory 数量。

## Implementation Tasks

这些任务来自 eng review 后的执行收敛清单，以及本轮文档边界收敛新增的 Phase 0 PR 复查项。实现阶段按单 PR review packet 拆分，逐项勾选，不再把审计附录当作执行依据。

- [ ] **T1 (P1, human: ~1h / CC: ~15min)** — Phase 0 单 PR review packet — 在实施 issue 和 PR 描述中保留 Packet A/B/C 分区。
  - 来源：scope review 发现 Phase 0 范围很大；本阶段选择一个 PR 完成，因此必须提高 PR 内部可审性。
  - 文件：`docs/superpowers/specs/2026-06-25-workline-restructuring-phase-0-spec.md`
  - 验证：Issue/PR 描述包含 Packet A/B/C 的文件清单、验收项、验证命令和剩余风险；不出现“后续 PR 补齐”作为通过理由。
- [ ] **T2 (P1, human: ~2h / CC: ~25min)** — 架构护栏门禁 — 将 architecture guardrails 接入 `git-quality-gate` 和 CI/Jenkins。
  - 来源：architecture review 发现 guardrails 若仅手动运行，合并前不可强制。
  - 文件：`scripts/git-quality-gate.sh`、`Jenkinsfile`、`scripts/architecture-guardrails.sh`
  - 验证：`./scripts/git-quality-gate.sh --check architecture`；临时删除任意 seed allowlist 行后 `sh scripts/architecture-guardrails.sh --phase phase1` 必须失败。
- [ ] **T3 (P1, human: ~1h / CC: ~15min)** — RuntimeInbox 合同 — C5 使用目标态测试专用模型。
  - 来源：architecture review 发现 C5 可能误绑定 legacy `WorklineInbox`。
  - 文件：`tests/support/runtime_inbox_contract.py`、`tests/architecture/test_c5_runtime_inbox_state_machine.py`
  - 验证：`uv run pytest tests/architecture/test_c5_runtime_inbox_state_machine.py`
- [ ] **T4 (P1, human: ~3h / CC: ~45min)** — Legacy matrix — 为 seed allowlist 违规补 `guardrail_seed_scope` 条目。
  - 来源：code quality review 发现 seed allowlist 缺 matrix entry 覆盖。
  - 文件：`docs/architecture/legacy-cleanup-matrix.md`、`scripts/architecture-guardrails.allowlist`
  - 验证：allowlist 校验在任意 seed `legacy_entry_id` 缺失、`drop_phase` 不一致或 `expires_at` 缺失时失败。
- [ ] **T5 (P1, human: ~4h / CC: ~60min)** — 行为合同 — BC-05、BC-06 同时具备 characterization 和目标态 contract 壳。
  - 来源：test review 发现 BC-05/BC-06 不能只停留在旧行为提取。
  - 文件：`tests/contracts/workline/`、`tests/characterization/workline_legacy/`、`tests/fixtures/workline_contract/`
  - 验证：`uv run pytest tests/contracts/workline/ tests/characterization/workline_legacy/`
- [ ] **T6 (P1, human: ~10min / CC: ~3min)** — 验证命令 — Phase 0 测试命令覆盖新增 contract/characterization 目录。
  - 来源：test review 发现原建议命令漏掉新测试目录。
  - 文件：本 SPEC、相关 PR 描述
  - 验证：命令包含 `tests/contracts/` 和 `tests/characterization/workline_legacy/`。
- [ ] **T7 (P2, human: ~15min / CC: ~5min)** — 测试纪律 — Phase 1 schema 缺口只允许 strict xfail。
  - 来源：test review 发现非 strict xfail 会隐藏已完成行为。
  - 文件：`tests/contracts/workline/`、`tests/architecture/`
  - 验证：`rg -n "xfail" tests | rg -v "strict=True"` 不返回 Phase 0 contract xfail。
- [ ] **T8 (P2, human: ~20min / CC: ~5min)** — Phase 0 边界复查 — 确认 Phase 0 PR 未修改生产 runtime 行为。
  - 来源：文档优化要求 Phase 0 只交付文档、测试、fixture、脚本和测试专用 schema。
  - 文件：Phase 0 PR diff
  - 验证：PR diff 中 `src/app/**`、`src/workline_runtime/**`、`src/workline_plugins/**` 无生产行为变更；如有变更，移入 Phase 1+ SPEC。

## Single PR Execution Plan

| Packet | Modules touched | Depends on |
| --- | --- | --- |
| Packet A target + inventory | `docs/architecture/`、`src/app/workline/`、`src/workline_runtime/`、`src/workline_plugins/`、`tests/workline_runtime/`、seed-scope source dirs | none |
| Packet B contracts + external boundary | `docs/architecture/`、`docs/contracts/`、`tests/contracts/`、`tests/characterization/`、`tests/fixtures/`、`tests/support/` | Packet A target contract and matrix IDs |
| Packet C guardrails + quality gate | `scripts/`、`tests/architecture/`、`tests/support/`、`Jenkinsfile`、`docs/architecture/` | Packet A matrix IDs and Packet B contract boundaries |

执行建议：

1. 在同一分支内先完成 Packet A，冻结目标态合同、matrix ID 和 correlation matrix。
2. 再完成 Packet B，稳定 `tests/support/`、`docs/architecture/` 和 `tests/fixtures/` 的公共合同。
3. 最后完成 Packet C，把 Packet A/B 的约束收口为 guardrails、allowlist、architecture tests、`git-quality-gate` 和 Jenkins 门禁。
4. 不建议为本阶段开启多个 worktree 并行实现；Packet B 与 Packet C 都会触碰 `tests/support/` 和 `docs/architecture/`，并行会提高 allowlist 和测试模型漂移风险。

## Files Reference

| File | Change |
| --- | --- |
| `docs/architecture/workline-and-plugin-restructuring.md` | Phase 0 事实来源，不修改 |
| `docs/architecture/target-state-contract.md` | 新增 |
| `docs/architecture/legacy-cleanup-matrix.md` | 新增 |
| `docs/architecture/session-correlation-matrix.md` | 新增 |
| `docs/architecture/device-command-contract.md` | 新增 |
| `docs/contracts/external-contract-profile.md` | 新增 |
| `docs/architecture/integration-lab-and-simulator.md` | 新增 |
| `docs/architecture/architecture-guardrails-spec.md` | 新增 |
| `scripts/architecture-guardrails.sh` | 新增，支持 `--phase phase0/phase1/phase2` |
| `scripts/architecture-guardrails.allowlist` | 新增，seed 关联 legacy-cleanup-matrix `entry_id` |
| `scripts/git-quality-gate.sh` | 更新，新增 `--check architecture` 并可被 CI 调用 |
| `Jenkinsfile` | 更新，接入 architecture guardrails 质量门禁步骤 |
| `tests/support/external_contract_profile.py` | 新增，Pydantic schema 测试模型（Phase 1 升级到生产路径） |
| `tests/support/runtime_inbox_contract.py` | 新增，C5 RuntimeInbox 目标态测试专用合同模型 |
| `tests/architecture/` | 新增，按 rule 拆分 |
| `tests/architecture/test_c1_wms_import_guardrail.py` | 新增，含 1 个 violation fixture |
| `tests/architecture/test_c2_cross_domain_fk_guardrail.py` | 新增，含 1 个 violation fixture |
| `tests/architecture/test_c3_authority_metadata_guardrail.py` | 新增，含 1 个 violation fixture |
| `tests/architecture/test_c4_device_command_fields_guardrail.py` | 新增，含 1 个 violation fixture |
| `tests/architecture/test_c5_runtime_inbox_state_machine.py` | 新增，覆盖 6 种状态转移 |
| `tests/architecture/test_ri3_capability_injection_guardrail.py` | 新增，覆盖 R-I3a 关键词 + R-I3b from-import |
| `tests/architecture/fixtures/c1_violation_example.py` | 新增（其他 rule 同构） |
| `tests/workline_runtime/` | 只读取并标记 characterization 来源，不以旧覆盖率替代目标态 contract |
| `tests/resource/` | 资源投影契约测试扩展（BC-04），不修改生产 resource 行为 |
| `tests/wms_integration/` | 外部合同 fixture/adapter contract 基线，不新增生产 provider fallback |
| `tests/characterization/workline_legacy/` | 新增 characterization 测试目录（BC-05/06/07） |
| `tests/contracts/workline/` | 新增目标态 contract tests（BC-01/02/03/04/05/06/08/10） |
| `tests/contracts/wms_integration/` | 新增 BC-09 authority cache contract |
| `tests/fixtures/workline_contract/` | 新增 BC fixture 集 |
| `tests/fixtures/external_contracts/wms/default/` | 新增 WMS 合同 fixture 集 |

## Suggested Issue Task Split

以下拆分用于 issue / checklist 管理，不代表 Phase 0 要拆成多个 PR；实施交付仍以单 PR + Packet A/B/C 为准。

| Issue | Scope | Dependency |
| --- | --- | --- |
| Phase 0 Epic | 本 SPEC 全部范围 | none |
| P0-001 | target-state contract | Phase 0 Epic |
| P0-002a | legacy matrix: workline + workline_runtime | P0-001 |
| P0-002b | legacy matrix: workline_plugins + tests | P0-001 |
| P0-002c | legacy matrix: docs/templates + 收口审查 | P0-002a + P0-002b |
| P0-003 | behavior contract + characterization fixture | P0-001、P0-002a（业务语义需 inventory 完成后引用） |
| P0-004 | correlation matrix | P0-001、P0-002a |
| P0-005 | device contract | P0-001 |
| P0-006 | external contract profile + IntegrationLab | P0-001 |
| P0-007 | architecture guardrails + seed allowlist | P0-001 到 P0-006，且需 P0-002c 完成（seed 引用 entry_id） |

## Out of Scope

- 不实现 Phase 1 runtime/orchestration 代码骨架。
- 不迁移旧执行入口。
- 不删除仍承载 Phase 4 业务语义的 legacy。
- 不展开 Phase 3 的 HMAC canonical、11 态机完整转移表、PlaneSceneView/Snapshot schema 或 ReconciliationManager 触发矩阵。
- 不为旧 API、旧表名、旧 plugin 形态提供兼容承诺。

## Related

- `docs/architecture/workline-and-plugin-restructuring.md`
- `docs/integration/wms_rcs_interface_requirements.md`
- `docs/integration/third_party_integration_whitepaper.md`
- `docs/architecture/reviews/autoplan-workline-restructuring-2026-06-23.md`
- `docs/architecture/reviews/decision-audit-trail.md`

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 fresh | not run | no fresh CEO review for this Phase 0 SPEC |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 fresh | not run | outside voice skipped |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 2 fresh | REVIEWING | 本轮发现 6 个文档对齐问题，已在本版 SPEC 收口 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 fresh | n/a | backend-only plan |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 fresh | not run | no DX review requested |

- **VERDICT:** REVIEWING — 本轮对齐问题已收口；实施前仍需基于主计划做最终只读验证。

NO UNRESOLVED DECISIONS
