---
status: Phase 0 legacy 清理矩阵
created_at: 2026-06-25
parent: docs/architecture/workline-and-plugin-restructuring.md
spec: docs/superpowers/specs/2026-06-25-workline-restructuring-phase-0-spec.md
related: docs/architecture/target-state-contract.md, docs/architecture/session-correlation-matrix.md
data: docs/architecture/legacy-cleanup-matrix.csv
generator: scripts/generate_legacy_matrix.py
note: |
  逐入口数据在 legacy-cleanup-matrix.csv（852 条，由脚本生成，可复现）。
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

扫描覆盖：`src/app/workline/`、`src/workline_runtime/`、`src/workline_plugins/`、`tests/workline_runtime/`、`tests/workline_plugins/`、`docs/templates/workline_plugin/`，并登记 `guardrail_seed_scope` 跨域路径（callback/rack/handling/resource/wms_integration）。其中 `src/app/workline/services/` 按 `class` / `def` / `async def` 全量入库，不只统计 `*Service` 类；已迁入 runtime/orchestration 或 runtime/capabilities 的 WorkLine service shim 按旧入口记账、从实现文件扫描符号；`src/workline_runtime/` 与 `src/workline_plugins/` 同时登记 `__all__` exported symbol。

## 3. 汇总（截至 feature/phase2-burndown-stage4 @ 2026-06-30）

| 指标 | 数值 |
| --- | ---: |
| **total_entries** | **852** |
| phase4_carrier（承载 Phase 4 业务语义） | 241 |
| pending-review | 0 |

### total_entries_by_type

| entry_type | count |
| --- | ---: |
| service | 324 |
| model | 192 |
| plugin | 116 |
| domain_object | 91 |
| test | 62 |
| api_route | 41 |
| repository | 17 |
| doc_template | 8 |
| runtime_helper | 1 |

### total_entries_by_strategy

| strategy | count |
| --- | ---: |
| rebuild | 543 |
| keep-contract | 211 |
| delete | 81 |
| move | 17 |

### total_entries_by_drop_phase

| drop_phase | count |
| --- | ---: |
| phase2 | 309 |
| phase5-tech | 293 |
| phase4 | 241 |
| phase1 | 9 |

### total_entries_by_owner

| current_owner | count |
| --- | ---: |
| workline | 640 |
| workline_plugins | 178 |
| workline_runtime | 9 |
| runtime | 7 |
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

## 7. 按域说明

### 7.1 workline（640 entries）

| 类别 | 处理 | 说明 |
| --- | --- | --- |
| WorkLine 配置类（`models/workline.py:WorkLine/WorkLineBase`、manifest/topology/safety/rack_position） | move | 保留为配置域目标，Phase 2 调整 schema 对齐目标态 WorkLine manifest |
| 执行状态模型（`models/session.py`、`inbox.py`、`timeline.py`、`runtime_hold*.py`、`runtime.py`） | rebuild | 整体 move 到 `src/app/runtime/orchestration/models/`，内部 session_id 保留为 execution_session_id（见 P0-004 §4.6） |
| 业务流程模型（`smt_inbound_handoff.py`、`object_transition_event.py`） | rebuild | Phase 4 按目标态 capability 重建 |
| API routes（41 个，`v1/`） | rebuild | runtime 监控/handoff/trace/hold 路由迁 runtime 域；workline 配置 CRUD 路由保留 |
| Services（inbox_batch_processor/outbox_dispatch/device_command_gateway 等） | rebuild | `class` / `def` / `async def` 全量登记；执行状态服务迁 runtime 域，按 EffectPort/RuntimeInbox 重建 |
| `single_layer_rack_orchestration_service` | rebuild | [phase4] 单层机架编排，Phase 4 重建（C1 seed 关联） |

### 7.2 workline_runtime（9 entries）

| 类别 | 处理 | 说明 |
| --- | --- | --- |
| `src/workline_runtime/` 源码 | 已清空 | 当前矩阵不再保留历史源码条目；若未来恢复 tracked 文件，生成器会重新扫描 |
| `tests/workline_runtime/` | 已清空 | 当前矩阵不再保留历史测试条目；旧 runtime 行已由 Phase 2 迁移/删除提交消化 |
| `docs/templates/workline_plugin/` | delete | 当前 owner=workline_runtime 的 8 条均为旧 plugin 模板，见 §7.4 |
| `src/workline_runtime/services.py:build_workline_runtime_services` | rebuild | guardrail seed tombstone，用于当前 allowlist 精确反查 |

### 7.3 workline_plugins（178 entries，含 62 tests）

| 类别 | 处理 | 说明 |
| --- | --- | --- |
| `rough_sorter/`（plugin/contract/context） | rebuild | [phase4] 粗分机业务流程，Phase 4 重建 |
| `smt_sorting_inbound/`（plugin/flow_service/context） | rebuild | [phase4] SMT 分拣入库，Phase 4 重建 |
| `__all__` 导出符号 | rebuild / delete / keep-contract | 按 exported symbol 逐条登记，避免 plugin public API 漏扫 |
| manifest.yaml | keep-contract | characterization 输入 |

### 7.4 docs/templates/workline_plugin（8 entries）

| 类别 | 处理 | 说明 |
| --- | --- | --- |
| 全部模板（README/context.py.tmpl/contract.py.tmpl 等） | delete | 只描述旧 plugin，删除前确保新目标态 docs 已覆盖使用者入口 |

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
| 执行状态迁移（258 条执行状态语义；phase2 rebuild 总计 292 条） | phase2 | 整体迁 runtime/orchestration，须保证崩溃重放不丢 intent；旧 `WorklineInbox` 只作 characterization（C5 约束） |
| phase4 业务流程（241 entries） | phase4 | 粗分机/满箱交换/分拣机/SMT/NG 语义重建，须 characterization + contract test 先行 |
| `single_layer_rack_orchestration_service`（C1 seed） | phase2 | 跨域 WMS import，Phase 2 迁移时消除 |
| device `session_id_int` ↔ session `awaiting_command_id` 外键环 | phase1 | 见 P0-004 §4.4，Phase 1 CEO-010 同步处理 |

## 9. 验收（SPEC P0-002）

1. ✅ 每个旧入口都有且只有一个主策略（CSV 852 条，strategy 字段非空）
2. ✅ 标记是否承载 Phase 4 业务语义（phase4_carrier 字段，241 条）
3. ✅ 标记删除、迁移或重建前置条件（`blocking_tests` 字段非空）
4. ✅ pending-review 归零（全部 final）
5. ✅ `total_entries_by_type` 汇总存在，由脚本输出
6. ✅ `rebuild` / `move` 项均有 `target_path` 或 `target_capability`
7. ✅ seed allowlist 违规可反查 `guardrail_seed_scope` 条目（§7.5）
