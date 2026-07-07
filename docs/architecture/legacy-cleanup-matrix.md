---
status: Phase 0 legacy 清理矩阵
created_at: 2026-06-25
parent: docs/architecture/workline-and-plugin-restructuring.md
spec: docs/superpowers/specs/2026-06-25-workline-restructuring-phase-0-spec.md
related: docs/architecture/target-state-contract.md, docs/architecture/session-correlation-matrix.md
data: docs/architecture/legacy-cleanup-matrix.csv
generator: scripts/generate_legacy_matrix.py
note: |
  逐入口数据在 legacy-cleanup-matrix.csv（638 条，由脚本生成，可复现）。
  本文档定义字段规范、策略规则、按域判定、高风险项与汇总。
  刷新: uv run python scripts/generate_legacy_matrix.py
---

# Legacy 清理矩阵（P0-002）

> 父设计：主计划 §3.7 目标态契约与 Legacy 处理
> 目标态合同：`target-state-contract.md` §8
> 逐条数据：`docs/architecture/legacy-cleanup-matrix.csv`（脚本生成）

## 1. 编写目的

按实际路径列出旧 WorkLine/plugin/runtime 每个入口的处理策略，禁止写"清理旧代码"这类泛化条目。供后续 Phase 1-5 实施者判断每个 legacy 入口的去留。

## 2. 数据来源与刷新

逐条数据由 `scripts/generate_legacy_matrix.py` 扫描生成，落到 `docs/architecture/legacy-cleanup-matrix.csv`。

```bash
# 刷新基线（对齐 SPEC §Proposed Change 入口粒度）
uv run python scripts/generate_legacy_matrix.py
```

扫描覆盖现存的 `src/app/workline/`、`src/workline_runtime/`、`tests/workline_runtime/`、`tests/workline_plugins/`，并登记 `guardrail_seed_scope` 跨域路径（callback/rack/handling/resource/wms_integration）。生成器仍会扫描 `src/workline_plugins/` 与 `docs/templates/workline_plugin/`（若目录存在），但 Phase5 technical lane 后这两个 legacy 运行/模板路径应保持为空，absence guardrail 负责阻断回流。其中 `src/app/workline/services/` 按 `class` / `def` / `async def` 全量入库，不只统计 `*Service` 类；已迁入 runtime/orchestration 或 runtime/capabilities 的 WorkLine service shim 按旧入口记账、从实现文件扫描符号；Phase5 business destructive cleanup 后，已迁入 `src/app/runtime/capabilities/phase4/contracts/` 与 `tests/contracts/workline/` 的业务合同/测试仍按 legacy entry_id 进入 CSV，避免删除旧路径造成 audit trace 误绿。

## 3. 汇总（截至 Phase5 business destructive cleanup @ 2026-07-07）

| 指标 | 数值 |
| --- | ---: |
| **total_entries** | **638** |
| phase4_carrier（承载 Phase 4 业务语义） | 104 |
| pending-review | 0 |

### total_entries_by_type

| entry_type | count |
| --- | ---: |
| service | 357 |
| domain_object | 114 |
| test | 93 |
| model | 45 |
| api_route | 21 |
| repository | 7 |
| runtime_helper | 1 |

### total_entries_by_strategy

| strategy | count |
| --- | ---: |
| rebuild | 341 |
| keep-contract | 232 |
| delete | 58 |
| move | 7 |

### total_entries_by_drop_phase

| drop_phase | count |
| --- | ---: |
| phase5-tech | 291 |
| phase2 | 234 |
| phase4 | 104 |
| phase1 | 9 |

### total_entries_by_owner

| current_owner | count |
| --- | ---: |
| workline | 518 |
| workline_runtime | 83 |
| workline_plugins | 11 |
| runtime | 8 |
| handling | 6 |
| rack | 5 |
| resource | 5 |
| callback | 1 |
| wms_integration | 1 |

## 4. 矩阵字段

CSV 列（对齐 SPEC P0-002 矩阵字段表）：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `entry_id` | string | yes | 稳定 ID，格式 `legacy:<relative_path>:<symbol_or_route>` |
| `entry_type` | enum | yes | api_route / service / repository / model / plugin / runtime_helper / test / doc_template / domain_object / value_object / utility / config_module / other |
| `relative_path` | string | yes | 仓库根目录相对路径 |
| `symbol_or_route` | string | yes | 类/函数/route/测试名；文件级用 `<file>` |
| `current_owner` | enum | yes | workline / workline_runtime / workline_plugins / callback / rack / handling / device / resource / wms_integration |
| `business_semantics` | string | yes | 保留的业务语义，若无则 `none` |
| `phase4_carrier` | bool | yes | 是否承载 Phase 4 才能重建的业务语义 |
| `classification_status` | enum | yes | `final`（Phase 0 PR merge 前 pending-review 归零） |
| `strategy` | enum | yes | delete / rebuild / move / keep-contract |
| `target_path` | string | conditional | `move` 或 `rebuild` 时必填；可指向目标目录或目标模块 |
| `target_capability` | string | conditional | rebuild 时必填；例如 `RuntimeInboxService.process_one` / `WmsFulfillmentPort.request_transport` |
| `blocking_tests` | string | yes | 删除、迁移或重建前必须通过的测试；多项用 `;` 分隔 |
| `drop_phase` | enum | yes | phase1 / phase2 / phase3 / phase4 / phase5-tech / phase5-business |
| `risk` | enum | yes | LOW / MEDIUM / HIGH |
| `notes` | string | optional | 说明；seed_scope 条目标 `guardrail_seed_scope` |

## 5. 处理策略枚举

| 策略 | 定义 |
| --- | --- |
| `delete` | Phase 5 可删除，无业务语义承载 |
| `rebuild` | 业务语义保留，但按目标态 port/capability 重建 |
| `move` | 可迁入新域，需记录目标路径和依赖条件 |
| `keep-contract` | 保留为 characterization/contract 来源，不作为目标态入口 |

## 6. 判定规则（脚本内嵌，可复现）

### 6.1 business_semantics 判定（顺序敏感，先匹配更具体类别）

1. 旧 plugin 框架（plugin_base/plugin_context/plugin_manifest/plugin_sdk 等）→ "旧 plugin 框架，目标删除"
2. phase4 业务流程（rough_sorter/full_box/sorter_inbound/smt_inbound/ng_return/single_layer_rack/station_lease/bin_cell_reservation/material_identity/six_in_one/start_admission/smt_usage）→ "[phase4] xxx 业务流程"
3. WorkLine 配置域（WorkLine 主配置类/manifest/topology/safety_zone/plane/rack_position/pipeline_queue/event_binding/command_binding/state_machine/device_requirement）→ "WorkLine 配置域能力"
4. 执行状态（session/inbox/timeline/runtime_hold/intent/effect/outbox/orchestrat/dispatch/runtime.py/trace_）→ "执行状态，目标迁 runtime/orchestration"
5. 跨域 session（workline_session_id/material_session_id/current_session_id/sorting_session_id）→ "跨域 session 引用"
6. 技术残留（debug/sandbox/fake/mock/cleanup/diagnostic/integration_debug）→ "技术残留/调试"
7. 默认按 owner：test→"测试"，plugin→"旧 plugin 框架"，其余→"none"

### 6.2 strategy + drop_phase + risk 判定

| business_semantics 类别 | strategy | drop_phase | risk |
| --- | --- | --- | --- |
| 旧 plugin 框架 | delete | phase5-tech | MEDIUM |
| 技术残留/调试 | delete | phase5-tech | LOW |
| 执行状态 | rebuild | phase2 | HIGH |
| 跨域 session 引用 | rebuild | phase1 | MEDIUM |
| [phase4] 业务流程 | rebuild | phase4 | HIGH |
| WorkLine 配置域能力 | move | phase2 | MEDIUM |
| 测试 | keep-contract | phase5-tech | LOW |
| doc_template / 默认 | delete / keep-contract | phase5-tech | LOW |

### 6.3 Phase5 双 lane 删除前置

Phase5 不再用单一“清理旧代码”口径推进，删除前必须先判定 technical lane 或 business lane，并把对应证据写入 PR：

当前 gate 状态：

- `phase5_technical_lane_status: ready-for-technical-cleanup`
- `phase5_business_lane_status: ready-for-business-cleanup`

执行入口：

- technical lane：`uv run python scripts/check_phase5_readiness_gate.py --lane technical`，并已接入 `./scripts/git-quality-gate.sh --check phase5-readiness` 与 `--profile quality`。
- business lane readiness：`uv run python scripts/check_phase5_readiness_gate.py --lane business --phase3-p0-e2e-artifact <p0.json> --phase3-benchmark-artifact <benchmark.json> --phase4-evidence-artifact <phase4.json>`。
- business destructive cleanup：`uv run python scripts/check_phase5_business_destructive_cleanup_gate.py --mode final`，并已接入 `./scripts/git-quality-gate.sh --check phase5-business-destructive-cleanup` 与 `--profile quality`。

2026-07-06 验收记录：

- technical lane 已通过 Phase2 owner guardrail、RuntimeInbox cutover、Phase3 mock closure、Phase5 technical contracts，并完成旧 plugin runtime/import 框架清理；执行记录见 `docs/architecture/legacy-cleanup-execution-plan.md`。
- business lane 携带 regenerated Phase3/Phase4 artifacts 后已通过 readiness gate；随后执行 destructive cleanup ledger 关闭：104 条 phase4 carrier 中 55 行 moved、10 行 test-only-migrated、18 行 kept-config-only、21 行 already-removed，0 pending。机器验收见 `docs/architecture/phase5-business-destructive-cleanup-ledger.csv` 与 `scripts/check_phase5_business_destructive_cleanup_gate.py --mode final`。
- 旧 `src/workline_plugins/*` 仅保留在 `docs/archive/legacy-workline-plugins/`，不得回流到 `src/` 可 import 路径；absence guardrail 负责阻断。

| lane | 适用条目 | 删除前置 | 不允许 |
| --- | --- | --- | --- |
| technical lane (`phase5-tech`) | debug/sandbox/fake/mock、旧 plugin 模板、已无生产 import 的 shim、仅服务开发/测试的辅助入口 | Phase2 runtime/orchestration owner guardrail 通过；Phase3 mock closure 或等价开发/测试门禁通过；`architecture-guardrails.sh` 与相关 characterization/contract test 通过；GitNexus detect-changes 确认只影响预期技术入口 | 以技术清理名义删除仍承载业务语义、API contract、trace/diagnostic evidence 或生产发布 profile 的入口 |
| business lane (`phase5-business`) | 旧 plugin / WorkLine 业务流程中仍承载 Phase4 语义、WMS/ECS evidence、生产 trace、benchmark 或人工处置合同的入口 | Phase4 capability 替代路径已生产可用；evidence manifest 引用文件齐全；production closure profile 通过；Phase4 capability / port / contract tests 全绿；旧入口 characterization/contract test 已迁为目标态测试或明确废弃；数据迁移/回填/审计留痕计划已执行 | 用 mock closure、lightweight benchmark 或缺 evidence / 缺 contract tests 的本地测试冒充业务承载删除前置 |

`WorkLine.runtime_status` 这类 compatibility projection 不按普通 technical lane 直接删除；必须先确认 API / monitor / trace / safety / START admission 已迁到 runtime/orchestration 原生读模型，再进入 schema 删除或字段改名计划。

## 7. 按域说明

### 7.1 workline（495 entries）

| 类别 | 处理 | 说明 |
| --- | --- | --- |
| WorkLine 配置类（`models/workline.py:WorkLine/WorkLineBase`、manifest/topology/safety/rack_position） | move | 保留为配置域目标，Phase 2 调整 schema 对齐目标态 WorkLine manifest |
| 执行状态模型（`models/session.py`、`inbox.py`、`timeline.py`、`runtime_hold*.py`、`runtime.py`） | rebuild | 整体 move 到 `src/app/runtime/orchestration/models/`，内部 session_id 保留为 execution_session_id（见 P0-004 §4.6） |
| 业务流程模型（`smt_inbound_handoff.py`、`object_transition_event.py`） | rebuild | Phase 4 按目标态 capability 重建 |
| API routes（21 个，`v1/`） | rebuild | runtime 监控/handoff/trace/hold 路由迁 runtime 域；workline 配置 CRUD 路由保留 |
| Services（inbox_batch_processor/outbox_dispatch/device_command_gateway 等） | rebuild | `class` / `def` / `async def` 全量登记；执行状态服务迁 runtime 域，按 EffectPort/RuntimeInbox 重建 |
| `single_layer_rack_orchestration_service` | rebuild | [phase4] 单层机架编排，Phase 4 重建（C1 seed 关联） |

### 7.2 workline_runtime（83 entries）

| 类别 | 处理 | 说明 |
| --- | --- | --- |
| `tests/workline_runtime/` | keep-contract / rebuild | 82 条 runtime / Phase4 characterization 与合同测试，作为目标态能力闭合和 Phase5 删除前的 blocking evidence；Phase5 dispatcher 新增测试已入矩阵 |
| `src/workline_runtime/services.py:build_workline_runtime_services` | rebuild | guardrail seed tombstone，用于当前 allowlist 精确反查 |

### 7.3 workline_plugins（11 entries，均为测试证据）

| 类别 | 处理 | 说明 |
| --- | --- | --- |
| `tests/workline_plugins/test_rough_sorter_contract.py` | rebuild | historical entry；业务断言已迁入 `tests/contracts/workline/test_rough_sorter_inbound_contract.py` |
| `tests/workline_plugins/test_barcode_decision_service.py` | keep-contract | historical entry；行为断言已迁入 `tests/contracts/workline/test_barcode_decision_contract.py` |

### 7.4 已删除的 legacy plugin 路径（0 entries）

| 类别 | 处理 | 说明 |
| --- | --- | --- |
| `src/workline_plugins/*` | delete | Phase5 technical lane 后不得继续存在于 `src/` 可 import 路径；旧代码只允许进入 `docs/archive/...` 等非运行路径 |
| `docs/templates/workline_plugin/*` | delete | 旧 plugin 模板已移除，新增模板不得恢复旧 plugin authoring 入口 |

### 7.5 guardrail_seed_scope（44 entries）

跨域路径登记，供 P0-007 seed allowlist 追溯 `legacy_entry_id`：

| seed rule | 路径 | owner | drop_phase |
| --- | --- | --- | --- |
| C1（WMS import，5 条） | callback_ingress_service.py、rack/gateway.py、handling/gateway.py、single_layer_rack_orchestration_service.py、`src/workline_runtime/services.py:build_workline_runtime_services` | callback/rack/handling/workline/workline_runtime | phase2/phase5-tech |
| C2（session FK） | resource/projection_service.py、projection_integrity_service.py、resource.py、wms_integration/transport_contract.py | resource/wms_integration | phase1 |
| R-I3b（capability forbidden import，18 条） | workline services/repositories 及已迁入 runtime 实现中逐文件枚举的 device/wms_integration services/models import | workline/runtime | phase2 |

> seed_scope 非对应域完整清理矩阵，仅为 P0-007 seed allowlist 建立可追踪 `legacy_entry_id`。每条 seed allowlist 违规必须能反查本矩阵 entry，且 `drop_phase` 一致。R-I3b allowlist 禁止目录前缀，必须逐文件枚举，避免未来新增违规被历史豁免吞掉。

## 8. 高风险项

| 风险项 | phase | 说明 |
| --- | --- | --- |
| 执行状态迁移（192 条执行状态语义；phase2 rebuild 总计 227 条） | phase2 | 整体迁 runtime/orchestration，须保证崩溃重放不丢 intent；旧 `WorklineInbox` 只作 characterization（C5 约束） |
| phase4 业务流程（104 entries） | phase4 | 粗分机/满箱交换/分拣机/SMT/NG 语义重建，须 characterization + contract test 先行 |
| `single_layer_rack_orchestration_service`（C1 seed） | phase2 | 跨域 WMS import，Phase 2 迁移时消除 |
| device `session_id_int` ↔ session `awaiting_command_id` 外键环 | phase1 | 见 P0-004 §4.4，Phase 1 CEO-010 同步处理 |

## 9. 验收（SPEC P0-002）

1. ✅ 每个旧入口都有且只有一个主策略（CSV 638 条，strategy 字段非空）
2. ✅ 标记是否承载 Phase 4 业务语义（phase4_carrier 字段，104 条）
3. ✅ 标记删除、迁移或重建前置条件（`blocking_tests` 字段非空）
4. ✅ pending-review 归零（全部 final）
5. ✅ `total_entries_by_type` 汇总存在，由脚本输出
6. ✅ `rebuild` / `move` 项均有 `target_path` 或 `target_capability`
7. ✅ seed allowlist 违规可反查 `guardrail_seed_scope` 条目（§7.5）
