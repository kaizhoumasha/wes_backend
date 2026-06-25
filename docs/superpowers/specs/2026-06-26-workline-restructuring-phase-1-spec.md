# WorkLine 重构 Phase 1：目标态骨架与 WMS ACL SPEC

## Context

`docs/superpowers/specs/2026-06-25-workline-restructuring-phase-0-spec.md` 的 Phase 0 已交付（v0.9.0.0，PR #63 merged），完成目标态边界锁定、行为契约测试基线、legacy 清理矩阵和自动化架构护栏。Phase 1 在 Phase 0 基线之上落地目标态骨架：建立 `runtime/orchestration` 执行域，补齐 `wms_integration` 7 个能力面 ports，落实 capability 注入边界（R-I3a/R-I3b enforced），不迁移旧执行入口。

直接读者是后端实现 agent 和 reviewer。本 SPEC 必须让实现者能按 sub-phase + CEO 任务包启动 Phase 1，不需要回到顶层设计或 autoplan 评审报告重新判断边界。

本 SPEC 综合 autoplan 评审（system-architect + backend-architect + security-engineer + quality-engineer 四维独立评审）的一致结论：**GO with conditions**——任务定义清晰、依赖可解，但 11 项 prerequisites 必须在 sprint 0 完成。

## Current State

Phase 0 已交付，主计划 §10.2 启动条件 "Phase 0 全部 7 项完成" 已满足。Phase 1 共 11 任务（主计划 §10.2），其中 CEO-002 4 方案决策表已在主计划 §3.8 归档（无需新工作）。

现有相关事实：

> 注：本 SPEC "文件数/LOC" 基线使用 `git ls-files <path> | wc -l` 或实测 `wc -l`，与 Phase 0 SPEC 保持一致。

| 证据 | 当前状态 | 对 Phase 1 的影响 |
| --- | --- | --- |
| `docs/architecture/workline-and-plugin-restructuring.md:1791` | Phase 1 包含 CEO-001~013 11 项 | SPEC 全量覆盖；CEO-002 仅作归档确认 |
| `docs/architecture/target-state-contract.md`（Phase 0 P0-001 交付） | 域边界 8 + 状态所有权 7 对象 + Authority Matrix 11 类 + Plane 边界 | Phase 1 实施必须严格对齐目标态合同，不得自行扩展边界 |
| `docs/architecture/legacy-cleanup-matrix.csv`（Phase 0 P0-002 交付） | 2191 entries / 0 pending-review / 含 327 services / 198 models / 43 api_routes | Phase 1 迁移须按矩阵 `drop_phase=phase1` 项实施；C1 5 处实际仅可清理 2 处（callback/handling），rack/workline 随 Phase 2/4 域迁移 |
| `docs/architecture/session-correlation-matrix.md`（Phase 0 P0-004 交付） | 39 跨域 session FK 0 遗漏；device session_id_int ↔ session.awaiting_command_id 外键环（HIGH 风险） | CEO-007/CEO-010 必须显式消解外键环 |
| `docs/architecture/device-command-contract.md`（Phase 0 P0-005 交付） | 设备 6 态 + Command-Ack-Callback + 禁止字段 | CEO-010 实施必须保持契约一致 |
| `docs/contracts/external-contract-profile.md`（Phase 0 P0-006 交付） | provider_code + contract_version + runtime_capabilities 字段表 | CEO-013 实施须升级 `tests/support/external_contract_profile.py` 到生产路径 |
| `tests/support/external_contract_profile.py:1` | Pydantic schema 在 tests/support/（禁止 src/app/ import） | CEO-013 升级路径：建议落 `src/app/contracts/` 共享层（避免 R-I3b 误报） |
| `tests/architecture/`（Phase 0 P0-007 交付） | 24 architecture tests（C1-C5 + R-I3a/b）+ 3 SPEC 锁定测试 | Phase 1 切 enforced（`ARCHITECTURE_PHASE=phase1`），首批 runtime 代码必须 0 新违规 |
| `tests/contracts/`（Phase 0 P0-003 交付） | 10 BC 全覆盖（28 pass + 3 strict xfail） | Phase 1 完成后唯一可解除 BC-02 (runtime snapshot)；BC-05/06 仍 strict xfail 到 Phase 3/4 |
| `scripts/architecture-guardrails.allowlist` | seed 31 条（C1×5 + C2×22 + R-I3b×4 含 device 实现）全部关联 `legacy_entry_id` | Phase 1 切 phase1 enforced，drop_phase ≥ phase2 的 26 条仍合法 |
| `src/app/wms_integration/`（实测 2649 LOC） | typed_ports.py 609 行 + circuit_breaker + callback_normalizer + cache + evidence | CEO-001 可复用核心三段事务契约（breaker before / HTTP 外 / evidence+breaker after），破坏性拆 `WmsInventoryPort` → query + transaction |
| `src/app/runtime/` | 不存在（Phase 0 时预期 0 R-I3 命中） | CEO-007 新建 `src/app/runtime/orchestration/` 域，首批代码必须 R-I3a/b 0 违规 |

## Proposed Change

新增 Phase 1 阶段级执行 SPEC，覆盖 CEO-001 ~ CEO-013 11 项任务。执行结果是目标态骨架（runtime/orchestration 可独立 worker + wms_integration 7 ports 可用 + DeviceCommand ECS contract 落地 + ExternalContractProfile 升级到 src/app/）。Phase 1 完成后旧执行入口未迁移（Phase 2 范围），但新骨架可独立运行。

按 autoplan 评审建议，Phase 1 拆 **4 sub-phase**（4 个独立 PR + 4 个版本号），单 PR 全做会爆炸式集成（11 任务 × M 级 ≈ 5-7 周 human / 1.5-2.5 周 CC+gstack）。

| Sub-phase | 范围 | 周期 | 版本号 | 完成门禁 |
| --- | --- | --- | --- | --- |
| **1a Foundation** | CEO-002（归档）+ CEO-005 + CEO-006 + CEO-012 + 6 个 H 级 prerequisites | 1-2 周 | v0.9.1.0 | schema/doc PR merge；query response schema 加 4 字段后 C3 测试通过；H1/H6 安全任务交付 |
| **1b ACL Ports & Device** | CEO-001 + CEO-010 + CEO-013 | 2 周 | v0.9.2.0 | wms_integration 7 port 单元测试 + R-I3b 内部域无违规 + DeviceCommand C4 字段白名单 + `src/app/contracts/` 共享层落地（升级 ExternalContractProfile）；H4 安全任务交付 |
| **1c Runtime Skeleton** | CEO-007 + CEO-008 + CEO-011 | 2 周 | v0.9.3.0 | runtime/orchestration 域可独立 worker；ExecutionSession.manifest_version pin；ConveyorQueueMembership active 唯一约束；7 张新表 Alembic upgrade/downgrade；H5 安全任务交付 |
| **1d Capability Boundary** | CEO-009 + Phase 2 go/no-go 准备 | 1 周 | v0.9.4.0 | capability 注入静态检查（R-I3a/R-I3b）零违规；inbound normalizer 不进入 capability；H2/H3 安全任务交付；准备 Phase 2 启动评审 |

每个 sub-phase 单独 PR 合一次后再启动下一个，降低 reviewer 负担与回归爆炸面。

### Sprint 0 Prerequisites（启动 Phase 1 实施前必做，共 11 项）

#### Architectural (5 项)

| # | 任务 | Owner | Effort | Sub-phase |
| --- | --- | --- | --- | --- |
| AP1 | **决定 `src/app/contracts/` 共享层** + 形成 ADR（避免 R-I3b 误报 + 避免反向 ACL） | architect | 0.5 day | 1b 前 |
| AP2 | **P0-004 device FK 环消解列入 CEO-007/CEO-010 显式子任务** + 更新主计划 §10.2 验证栏 | architect | 0.5 day | 1b/1c 前 |
| AP3 | **发布 `docs/architecture/runtime-orchestration-spec.md` 占位** + 7 实体 schema freeze（ExecutionSession / ExecutionCorrelation / ExecutionWorkItem / RuntimeInbox / RuntimeTimeline / RuntimeHold / RuntimeIntentLog） | architect | 1 day | 1c 前 |
| AP4 | **Phase 1 数据库迁移序列文档** + Alembic 顺序 + downgrade 策略（覆盖 1b/1c 的 7+ 张新表 + DeviceCommand 字段裁剪 + manifest schema 扩展） | architect | 0.5 day | 1c 前 |
| AP5 | **Seed allowlist 完整性回归** + 删任意 seed 行失败验证 + 切 `ARCHITECTURE_PHASE=phase1` enforced 模式 | dev ops | 0.5 day | 1a 前 |

#### Security HIGH (6 项，必须并入各 sub-phase)

| # | 任务 | Sub-phase | 验收 |
| --- | --- | --- | --- |
| H1 | CEO-005 C3 schema 校验扩展（pytest parametrize 枚举所有 `*Response` 类必含 AuthorityMetadata） | 1a | `tests/architecture/test_c3_response_schema_inventory.py` 全过 |
| H2 | CEO-009 RuntimeCapabilityContext 加 `_INBOUND_NORMALIZER_TYPES` registry type guard + factory pattern | 1d | 静态检查拒绝业务 capability 持有 `WmsEventPort` / `DeviceEventPort` / `RuntimeInbox consumer` |
| H3 | import-linter 配置 `capability-isolation` contract 接入 `git-quality-gate.sh` | 1d | `tach`/`import-linter` 跑通；与 guardrails 并行 |
| H4 | CEO-010 `DeviceCommand.params` 改 typed Pydantic union（禁用 `dict[str, Any]`）；所有 inbound command schema `extra="forbid"` | 1b | C4 字段白名单测试通过；params 改 typed union |
| H5 | `idempotency_keys` 表落地（schema-only，不实现 409） + WES 内部 key 命名约束测试 | 1c | `tests/architecture/test_i2_idempotency_schema.py` 全过；表结构 + 命名约束（`WES-{OPERATION_KIND}-{HASH}`） |
| H6 | 启动时 hard guard：`APP_DEBUG=False` 时禁止 `SKIP_API_AUTH=True`（移到 `src/core/conf.py` settings validator） | 1a | `pytest tests/api_auth/test_settings_hard_guard.py` 全过 |

## Implementation Details

### Sub-phase 1a Foundation（v0.9.1.0）

#### CEO-002 4 方案决策表归档

| 状态 | 内容 |
| --- | --- |
| 现状 | 主计划 §3.8 已归档 4 方案决策（A/B/C/D 决策表 + B 方案启动条件 + 5 项 go/no-go 指标） |
| Phase 1 动作 | 仅在 Sprint 0 PR 中明确"CEO-002 已闭环"；不新增文档；引用主计划 §3.8 |
| 验收 | PR 描述含 CEO-002 闭环确认；review checklist 勾选 |

#### CEO-005 查询响应 schema 加 `scope/authority/source/evidence_at` 强制字段

| 项 | 内容 |
| --- | --- |
| 涉及域 | wms_integration、resource、material、handling、workline(plane)、device、runtime/orchestration（CEO-007 新建） |
| 实施 | 引入 `AuthorityMetadata` 通用 Pydantic 字段，在所有 query response schema 中复合或继承 |
| C3 测试 | 扩展为 (a) typed model 合同测试（已存在）+ (b) `tests/architecture/test_c3_response_schema_inventory.py`（pytest parametrize 枚举所有 `*Response` 类） |
| 验收 | 所有 query response 含完整 4 字段；C3 测试覆盖率 100% |
| 风险 | 影响面广（多域 cascade）；建议先做 inventory 测试枚举影响面，再批量加 authority block |

#### CEO-006 Authority Matrix 文档

| 项 | 内容 |
| --- | --- |
| 文件 | `docs/architecture/authority-matrix.md`（独立成稿） |
| 内容 | 11 类事实类型 + 权威来源（从 `target-state-contract.md` §4 抽出 + 扩展案例和反例） |
| 验收 | Authority Matrix 文档发布；与主计划 §3.4 + target-state-contract.md §4 一致 |

#### CEO-012 WorkLine SafetyZone / shared-device manifest schema

| 项 | 内容 |
| --- | --- |
| 文件 | `src/app/workline/`（manifest schema）+ `src/app/device/`（共享设备影响范围） |
| 实施 | manifest YAML 增加 `safety_zones`、`shared_devices` 顶层字段；SafetyZone validator 校验 |
| 测试 | shared device 影响范围 / required/optional role / SafetyZone validator 测试 |
| 验收 | manifest schema 校验测试全过；与 P0-005 device-command-contract.md §7 启停门禁一致 |

#### Sprint 0 Prerequisites 落地（1a 完成）

- AP5: Seed allowlist 回归 + 切 phase1 enforced
- H1: C3 schema inventory 测试
- H6: APP_DEBUG hard guard

### Sub-phase 1b ACL Ports & Device（v0.9.2.0）

#### CEO-001 wms_integration 7 ports

| 现状（实测） | 行数 | Phase 1 处置 |
| --- | --- | --- |
| `services/typed_ports.py` | 609 | 保留三段事务契约骨架，按 port 拆分类 |
| `services/transport_contract.py` | 323 | 破坏性重写为 `WmsFulfillmentPort` 子模块（含 C2 seed 清理） |
| `services/circuit_breaker_service.py` | 272 | 保留 |
| `services/evidence_service.py` | 173 | 保留 |
| `services/callback_normalizer.py` | 146 | 提升为 `WmsEventPort` 的 normalizer 部分 |
| `services/endpoint_config.py` | 139 | 扩到 7 port 的 op 全集 |
| `services/service_locator.py` | 7 | **删除**（I3 不变量禁止 service_locator） |
| `models/ports.py` | 151 | 按 port 拆 7 个文件 |

**7 个目标 Port**（主计划 §5.1）：

1. `WmsMasterDataPort`（新增）— 物料、区域、地码、货架、料箱、库位元数据
2. `WmsDocumentPort`（新增）— GRN、入库单、出库单、波次、任务快照
3. `WmsInventoryQueryPort`（由现有 `WmsInventoryPort` 拆出只读部分）— `query_inventory` / `query_empty_bins`
4. `WmsInventoryTransactionPort`（由现有 `WmsInventoryPort` 拆出事务部分）— `reserve_inventory` / `release_reservation` / `confirm_transfer`
5. `WmsFulfillmentPort`（新增）— `request_rack_supply` / `request_rack_transport` / `change_rack_face` / `full_box_exchange` / `move_bin_to_conveyor_entry/exit` / `notify_pkg_binding`
6. `WmsEventPort`（新增）— `WMS_GRN_RECEIVED` / `WMS_PALLET_ARRIVED` / `WMS_RACK_ARRIVED` / `WMS_TRANSPORT_COMPLETED` 等回调 normalizer
7. `WmsReconciliationQueryPort`（新增）— `check_bin_drift` / `check_rack_drift` / `check_full_drift` 只读

**WmsInventoryPort 拆分边界规则**：

1. 改变 WMS 端事务状态 → Transaction，否则 → Query
2. 需要 `RuntimeIntentLog` 前置 → Transaction
3. 允许本地短 TTL 缓存（主计划 §6 库存 30s）→ Query
4. 失败重试语义：Query 可幂等重试；Transaction 必须靠 `idempotency_key + request_hash` 防双发

**C1 5 处违规清理**（实际 Phase 1 只能清理 2 处）：

| 调用方 | Phase 1 处置 |
| --- | --- |
| `src/app/handling/services/gateway.py:8` | **必须 Phase 1 清理**：改为通过 `WmsFulfillmentPort` 接口注入（依赖反转） |
| `src/app/callback/services/callback_ingress_service.py:30` | **可 Phase 1 清理**：通过 `WmsEventPort` 暴露 normalizer 注册表，避免直接 import |
| `src/app/rack/services/gateway.py:7` | 维持 allowlist（drop_phase=phase2，rack 整体域迁移在 Phase 2） |
| `src/app/workline/services/single_layer_rack_orchestration_service.py:22` | 维持 allowlist（drop_phase=phase4，承载 phase4 业务语义） |
| `src/app/workline/repositories/debug_data_cleanup_repository.py:1046` | 维持 allowlist（drop_phase=phase5-tech） |

**CEO-001 验收门禁修正**（autoplan 评审反馈）：主计划当前措辞"内部域无 WMS DTO/client import"应改为"**内部业务域**无 WMS DTO/client import（callback ACL 域和 legacy 标注豁免）"——避免误把 callback ACL 域（其本职即是 normalizer）和 legacy 标注违规当成 Phase 1 必清。

**ports 目录约定**：7 个 port 协议必须放 `src/app/wms_integration/ports/`（新目录），不要放 `services/models/`，避免 C1 静态规则误报（C1 当前 scan 排除 `wms_integration/*` 但 internal 域 import `wms_integration/ports/*` 是合法的，需 SPEC 中明确）。

#### CEO-010 DeviceCommand ECS API contract + manifest concurrency limit

| 项 | 内容 |
| --- | --- |
| 关联文件 | `src/app/device/`、`docs/architecture/device-command-contract.md`（Phase 0 已 freeze） |
| 实施 | 落地 Command-Ack-Callback 闭环 + 设备 6 态（IDLE/RUNNING/ERROR/OFFLINE/UNKNOWN/MAINTENANCE）+ DeviceCommand 字段白名单 + manifest concurrency limit |
| AP2 同步处理 | **P0-004 §4.4 device session_id_int ↔ session.awaiting_command_id 外键环消解**（DeviceCommand 不持 session FK，只持 correlation_id；`awaiting_command_id` 改为 `awaiting_command_correlation_id`，引用 DeviceCommand.command_code） |
| H4 同步落地 | `DeviceCommand.params` 改 typed Pydantic union（按 task_type 区分）；inbound command schema `extra="forbid"` |
| 测试 | command_code 幂等 / IDLE 校验 / RUNNING 有界等待 / ERROR/OFFLINE 短退避 / Event_Push 只 ACK / 缺 event_id 不推进 / in-flight 限制 / 扫码平台互锁（~12 case） |
| 验收 | `tests/contracts/device/test_device_command_contract.py` 12 case 全过；外键环消解 Alembic 迁移 upgrade/downgrade 通过 |

#### CEO-013 ExternalContractProfile + provider simulator registry

| 项 | 内容 |
| --- | --- |
| 关联文件 | `docs/contracts/external-contract-profile.md`（Phase 0 已 freeze） + `tests/support/external_contract_profile.py`（待升级） |
| AP1 决策 | **`tests/support/external_contract_profile.py` 升级到 `src/app/contracts/external_contract_profile.py`**（共享 contract 层，避免 R-I3b 误报） |
| 实施 | ExternalContractProfile + RuntimeCapabilityProfile + InboundNormalizerProfile 三个 typed DTO 在 `src/app/contracts/`；WMS/ECS provider simulator registry |
| 测试 | simulator 行为与 adapter contract 等价；profile 切换不影响已加载 session；fixture set 与 adapter contract 共享（~10 case） |
| 验收 | adapter/normalizer 不泄漏外部 DTO；未声明的 query/effect 能力被拒；未声明的 callback/event/result normalizer 被拒；R-I3b guardrail 不误报 |

#### Sprint 0 Prerequisites 落地（1b 完成）

- AP1: `src/app/contracts/` 共享层 ADR + 实施
- AP2: device FK 环消解（与 CEO-007 协同）
- H4: DeviceCommand typed params

### Sub-phase 1c Runtime Skeleton（v0.9.3.0）

#### CEO-007 runtime/orchestration 最小骨架

**7 张新表的 Alembic 迁移依赖顺序**（按 FK 反向拓扑）：

```
1. execution_sessions              (无 FK，根)
2. execution_correlations          (FK→execution_sessions, nullable)
3. runtime_inbox                   (FK→execution_sessions nullable, FK→execution_correlations nullable)
4. runtime_timelines               (FK→execution_sessions)
5. execution_work_items            (FK→execution_sessions, FK→execution_correlations, 自引 parent_correlation_id)
6. runtime_holds                   (FK→execution_sessions, FK→execution_work_items nullable)
7. runtime_intent_logs             (FK→execution_sessions, FK→execution_correlations)
8. idempotency_keys (H5)           (FK→execution_correlations via execution_correlation_id, PRIMARY KEY 复合主键)
```

**字段对齐主计划 §9.2**（已在 Phase 0 P0-004 schema 草案中冻结）：

| 实体 | 关键字段组 |
| --- | --- |
| `ExecutionSession` | `workline_id`, `manifest_version`, `state`, lifecycle timestamps |
| `ExecutionCorrelation` | `correlation_id`, `execution_session_id?`, `trace_id`, `source_event_id`, `business_owner_key` |
| `ExecutionWorkItem` | `execution_session_id`, `correlation_id`, object identity, current step, step status, parent_correlation_id, concurrency_scope, lease_expires_at |
| `RuntimeInbox` | `execution_session_id?`, `correlation_id?`, `provider_code`, `event_type`, `source_event_id`, `payload_hash`, `status`, retry fields |
| `RuntimeTimeline` | `execution_session_id`, `trace_id`, `correlation_id?`, `event_type`, `occurred_at` |
| `RuntimeHold` | `execution_session_id`, `correlation_id?`, `reason`, `hold_type`, scope fields, `resolved_at`, `affected_work_item_id`, `allowed_next_effect_scope` |
| `RuntimeIntentLog` | `execution_session_id`, `correlation_id`, `provider_code`, `target_domain`, `target_action`, `request_hash`, `idempotency_key`, `dispatch_status`, retry fields |

**对象级流水并发契约**（主计划 §9.2）：
- ExecutionSession 不是整条 WorkLine 的串行锁
- ExecutionWorkItem 是 runtime capability 的最小推进单位
- 设备串行只按 `DeviceDispatchPolicy` 和设备 `concurrency_limit` 控制
- step 完成以 DeviceResult / WMS callback / RuntimeLocationEvent evidence 为准

**RuntimeInbox 处理契约**（主计划 §9.2）：
- Callback API 在鉴权、schema normalize、幂等检查通过后立即写入 `RuntimeInbox(status=RECEIVED)` 并 ACK
- 异步 worker `RECEIVED → PROCESSING → PROCESSED` 为唯一成功路径
- FAILED 超过重试上限或超过业务 deadline 转 DEAD_LETTER + 创建 RuntimeHold + 人工审计队列
- 人工重放只能从 DEAD_LETTER 复制生成新 inbox 记录，保留原 payload_hash/source_event_id/idempotency_key

**测试**：4 个测试文件 ~30 case
- `tests/unit/runtime/orchestration/test_execution_session_lifecycle.py`（~8 case）
- `tests/unit/runtime/orchestration/test_runtime_inbox_state_machine.py`（~10 case）
- `tests/unit/runtime/orchestration/test_runtime_intent_log_effect_ledger.py`（~6 case）
- `tests/unit/runtime/orchestration/test_execution_correlation_key.py`（~6 case）

**BC-02 strict xfail 解除**（Phase 1 唯一可解除）：CEO-007 完成后，新建 `src/app/runtime/orchestration/services/runtime_snapshot_assembler.py`（装配 6 字段视图），改写 `tests/contracts/workline/test_runtime_snapshot_contract.py` 移除 `@pytest.mark.xfail`。

#### CEO-008 ConveyorQueueMembership 目标模型

| 项 | 内容 |
| --- | --- |
| 关联文件 | `src/app/runtime/orchestration/`（active 投影） + `src/app/workline/`（manifest queue_code 校验） |
| 实施 | 动态队列模型（`queue_code VARCHAR` 来自 manifest `pipeline_queues.code`）替代旧 `BinTransitMembership` 8 enum 方案 |
| 字段（主计划 §4.4） | `bin_code` / `placeholder_key`, `workline_id`, `conveyor_code`, `queue_code`, `queue_role`, `membership_status (ACTIVE/LEFT/RECONCILING)`, `entered_at`, `left_at`, `correlation_id`, `evidence_json` |
| 约束 | 同 `bin_code` 或 `placeholder_key` 在同 WorkLine 下最多一个 active membership；queue_code 必须来自 manifest |
| 测试 | manifest queue_code 校验 + active 唯一约束 + 跨 manifest 切换（~6 case） |

#### CEO-011 WorkLine manifest version pin

| 项 | 内容 |
| --- | --- |
| 关联文件 | `src/app/workline/`、`src/app/runtime/orchestration/` |
| 实施 | ExecutionSession 创建时 pin `manifest_version`；RUNNING session 不热切 manifest；activation-time validator |
| 测试 | RUNNING session 固定 manifest_version + 新 manifest 只影响新 session + activation-time validator（~4 case） |

#### Sprint 0 Prerequisites 落地（1c 完成）

- AP3: runtime-orchestration-spec.md 子 SPEC 撰写（与 1c PR 同步发布）
- AP4: Phase 1 数据库迁移序列文档
- H5: idempotency_keys 表落地

### Sub-phase 1d Capability Boundary（v0.9.4.0）

#### CEO-009 RuntimeCapabilityContext / CapabilityPortRegistry

| 项 | 内容 |
| --- | --- |
| 关联文件 | `src/app/runtime/`（capability 容器） |
| 核心约束（主计划 §3.5） | 三类注入边界严格分离：QueryPort（只读）/ EffectPort（出站副作用，必须先写 RuntimeIntentLog）/ InboundEventPort（只写 RuntimeInbox，不进 capability） |
| 实施 | `CapabilityPortRegistry.register(port_protocol: type, factory: Callable)` factory pattern；不直接注入实现实例 |
| H2 同步落地 | `_INBOUND_NORMALIZER_TYPES` registry type guard：`if isinstance(port, _INBOUND_NORMALIZER_TYPES): raise RegistryError`（拒绝 WmsEventPort/DeviceEventPort/RuntimeInbox consumer） |
| H3 同步落地 | import-linter 配置 `capability-isolation` contract：`source_modules = src.app.runtime.orchestration.capabilities`，`forbidden_modules = src.app.wms_integration.services|models|clients|providers, src.app.device.services|models, src.app.callback.services` |
| 测试 | capability 只能拿 query/effect port contract；静态检查拒绝 `wms_integration` / `device` service / HTTP client / DTO / provider exception / service locator / WmsEventPort / DeviceEventPort / RuntimeInbox consumer |
| 验收 | R-I3a / R-I3b enforced 零违规；inbound normalizer 不进入 capability 上下文；import-linter 通过 |

#### Sprint 0 Prerequisites 落地（1d 完成）

- H2: RuntimeCapabilityContext type guard
- H3: import-linter capability-isolation contract

## Acceptance Criteria

每个 sub-phase 独立 acceptance criteria：

### 1a Foundation 验收

1. ✅ CEO-002 闭环确认（主计划 §3.8 引用）
2. ✅ 所有 query response schema 含 AuthorityMetadata 4 字段；`tests/architecture/test_c3_response_schema_inventory.py` 全过
3. ✅ `docs/architecture/authority-matrix.md` 发布，与 target-state-contract.md §4 一致
4. ✅ WorkLine manifest schema 支持 SafetyZone / shared_devices；validator 测试通过
5. ✅ AP5 seed allowlist 回归通过；`ARCHITECTURE_PHASE=phase1` 切 enforced
6. ✅ H1 C3 inventory 测试 + H6 APP_DEBUG hard guard 交付

### 1b ACL Ports & Device 验收

1. ✅ wms_integration 7 port 全部实现（`src/app/wms_integration/ports/`）+ 单元测试（每个 port 4 case = 28 case）
2. ✅ `WmsInventoryPort` 破坏性拆 query + transaction
3. ✅ C1 5 处违规：handling/gateway.py + callback/callback_ingress_service.py 清理（其余 3 处维持 allowlist）
4. ✅ DeviceCommand ECS API contract 实现（12 case）
5. ✅ AP2 device session_id_int ↔ awaiting_command_id 外键环消解（Alembic 迁移）
6. ✅ H4 DeviceCommand typed params + extra="forbid"
7. ✅ AP1 `src/app/contracts/` 共享层落地；ExternalContractProfile 从 tests/support/ 升级
8. ✅ ExternalContractProfile + simulator registry（10 case）

### 1c Runtime Skeleton 验收

1. ✅ runtime/orchestration 域可独立 worker；7 张新表 Alembic upgrade/downgrade 通过
2. ✅ ExecutionSession / ExecutionWorkItem / RuntimeInbox / RuntimeTimeline / RuntimeHold / RuntimeIntentLog / ExecutionCorrelation 实体落地，字段对齐主计划 §9.2
3. ✅ ExecutionSession.manifest_version pin（CEO-011）
4. ✅ ConveyorQueueMembership 动态队列模型；active 唯一约束测试
5. ✅ BC-02 strict xfail 解除（`test_runtime_snapshot_exposes_state_timeline_inbox_hold_intent_correlation` 移除 xfail）
6. ✅ 4 个 unit test 文件全过（~30 case）
7. ✅ H5 idempotency_keys 表落地 + WES 内部 key 命名约束

### 1d Capability Boundary 验收

1. ✅ RuntimeCapabilityContext / CapabilityPortRegistry 实现
2. ✅ H2 type guard 拒绝 inbound normalizer 注入业务 capability
3. ✅ H3 import-linter `capability-isolation` contract 接入 git-quality-gate
4. ✅ R-I3a / R-I3b enforced 零新违规
5. ✅ Phase 2 go/no-go 评审准备（重新跑 autoplan）

### Phase 1 整体完成门禁（对应主计划 §10.2 末尾 13 项）

1. ✅ wms_integration 7 port 全部实现
2. ✅ `wms_rcs_interface_requirements.md` P0 接口映射全覆盖
3. ✅ 内部业务域无 WMS DTO/client import（callback ACL + legacy 标注豁免）
4. ✅ Runtime capability 注入仅暴露 port contract
5. ✅ Authority Matrix 文档发布
6. ✅ runtime/orchestration 最小骨架完成，RuntimeIntentLog 含 effect ledger + 崩溃重放
7. ✅ DeviceCommand 不含 PLC/坐标/关节/安全回路字段；dispatch 前 IDLE 校验
8. ✅ DeviceRuntime 状态快照 TTL + DeviceDispatchPolicy 入 manifest/schema
9. ✅ ExecutionSession pin manifest_version
10. ✅ 动态队列 membership 替代旧 8 enum
11. ✅ WorkLine manifest 表达 SafetyZone + 共享设备 + 影响范围；无 PLC 字段
12. ✅ ExternalContractProfile 覆盖 WMS/ECS 初始 provider
13. ✅ provider 未声明能力被拒；inbound normalizer 不进入 capability

## Risk Controls

| 风险 | 失效表现 | Phase 1 门禁 |
| --- | --- | --- |
| ExternalContractProfile 归属包错误（落 wms_integration/models 触发 R-I3b 误报） | Runtime capability 无法 import profile；CEO-009 阻塞 | AP1 ADR 强制落 `src/app/contracts/` 共享层；1b PR 前完成 |
| P0-004 device FK 环未消解 → Phase 2 污染 | 跨域 session FK 顺势带到 ExecutionSession；目标态污染 | AP2 列入 CEO-007/010 显式子任务；1b/1c Alembic 迁移验收 |
| CEO-009 在 CEO-007 之前实施 | capability registry 先于 runtime 宿主存在，接口 over-engineer | sub-phase 1d 严格在 1c 之后；PR 描述声明依赖 |
| CEO-005 cascade 影响面失控 | 所有 query response schema 需同步改造，PR 巨大 | 先做 C3 inventory 测试枚举，再批量加 authority block；分文件粒度提交 |
| 切 phase1 enforced 后旧违规阻塞新 PR | seed allowlist 过期或 legacy_entry_id 失效 | AP5 sprint 0 完整回归；每次 sub-phase PR 前跑 `--phase phase1` |
| BC-05/06 strict xfail 意外变绿（Phase 1 不应解除） | 说明契约边界判断有误 | Phase 1 → 2 go/no-go 硬门禁：BC-05/06 保持 xfail；意外变绿 → 触发 SPEC 重审 |
| state machine 测试覆盖不足 | 状态转移分支错误，崩溃恢复丢 intent | 强制 state machine 100% 行 + 100% 分支；CI 单独 fail_under=100 |

## Testing Plan

### Phase 1 新增测试预估（~110-120 case，~15 新测试文件）

| Layer | What | Count |
| --- | --- | ---: |
| 1a Foundation | C3 inventory + Authority Matrix consistency + SafetyZone validator + APP_DEBUG hard guard | +12 |
| 1b ACL Ports | 7 port × 4 case + DeviceCommand 12 case + ExternalContractProfile 10 case + 架构守护 | +51 |
| 1c Runtime Skeleton | 4 个 unit test 文件 30 case + ConveyorQueueMembership 6 case + manifest_version pin 4 case + idempotency_keys schema | +43 |
| 1d Capability Boundary | RuntimeCapabilityContext type guard + import-linter contract + R-I3a/b enforced regression | +6 |
| Strict xfail 解除 | BC-02 改 contract test + 5 case | +5 |
| Characterization 升级 | BC-07 sorter_inbound characterization → contract test（5 case） | +5 |
| **合计** | | **+122** |

### 推荐 CI gate 顺序

```yaml
stages:
  1. lint:              ruff format --check && ruff check        (~10s, fail-fast)
  2. architecture:      uv run pytest tests/architecture/        (~30s, guardrail 第一道)
  3. contracts:         uv run pytest tests/contracts/           (~60s, 外部契约第二道)
  4. unit:              uv run pytest tests/unit/                (~90s, 内部逻辑)
  5. characterization:  uv run pytest tests/characterization/    (~30s)
  6. integration:       uv run pytest tests/integration/         (较慢, 最后)
  7. coverage gate:     coverage report --fail-under=85          (阻断点)
  8. critical coverage: 100% for state_machines/ + intent_log/ + ports/
```

### Phase 1 → Phase 2 Go/No-Go 硬门禁（9 项）

1. ✅ 行为契约覆盖率（BC-01/03/04/08/09/10 + BC-02）≥ 80%
2. ✅ 强制 7 个 contract test 全绿（BC-01/02/03/04/08/09/10）
3. ✅ BC-05/06 保持 strict xfail
4. ✅ Architecture guardrail ≥ 27 个全绿（24 + Phase 1 新增 3）
5. ✅ State machine 100% 行 + 100% 分支覆盖
6. ✅ wms_integration 7 port × 4 case = 28 个全绿
7. ✅ DeviceCommand ECS contract 12 case 全绿
8. ✅ R-I3a/R-I3b allowlist 无新增 + 无过期
9. ✅ legacy matrix 一致性（CSV vs generator 0 漂移）

建议在 `scripts/` 下新增 `phase1-go-nogo.sh` 一键验证：

```bash
#!/bin/bash
set -e
uv run pytest tests/architecture/ tests/contracts/ \
  --cov=src/app/runtime --cov=src/app/wms_integration --cov-fail-under=80
uv run pytest --cov=src/app/runtime/orchestration/state_machines \
  --cov-fail-under=100 tests/unit/runtime/
uv run pytest tests/contracts/wms_integration/ tests/contracts/device/
bash scripts/architecture-guardrails.sh --phase phase1
```

## Rollback Plan

Phase 1 主要产物是新模块 + Alembic 迁移 + 测试。回滚以 sub-phase 粒度 revert 对应 PR 为主。

| Sub-phase | 回滚方式 |
| --- | --- |
| 1a | revert PR；CHANGELOG/VERSION/schema 改动可 Alembic downgrade |
| 1b | revert PR；wms_integration 拆分回滚到 1a 状态；DeviceCommand FK 环消解 Alembic downgrade |
| 1c | revert PR；7 张新表 Alembic downgrade（依赖反向顺序）；BC-02 重新加 strict xfail |
| 1d | revert PR；RuntimeCapabilityContext 移除；import-linter 配置移除 |

若 1c/1d 出现严重问题（如 Alembic downgrade 失败），按主计划 §10.3.1 B 暂停回退路径处理。

## Effort Estimate

| Sub-phase | Effort | CC + gstack 估计 | Human 估计 |
| --- | --- | --- | --- |
| **Sprint 0 Prerequisites** | M | 2-3 天 | 1 周 |
| **1a Foundation** | M | 1-2 天 | 1-2 周 |
| **1b ACL Ports & Device** | L | 3-4 天 | 2 周 |
| **1c Runtime Skeleton** | XL | 4-5 天 | 2 周 |
| **1d Capability Boundary** | M | 1-2 天 | 1 周 |
| **Phase 1 整体** | XL | **11-16 天**（CC+gstack） | **5-7 周**（human） |

## Implementation Tasks

按 sub-phase 拆分执行清单（实施阶段勾选）：

### Sprint 0 (前置)

- [ ] **AP1 (P1, human ~0.5d / CC ~30min)** — `src/app/contracts/` ADR + 目录占位
- [ ] **AP2 (P1, human ~0.5d / CC ~30min)** — P0-004 device FK 环消解列入 CEO-007/CEO-010 显式子任务（更新主计划 §10.2）
- [ ] **AP3 (P1, human ~1d / CC ~1h)** — `docs/architecture/runtime-orchestration-spec.md` 子 SPEC 撰写
- [ ] **AP4 (P1, human ~0.5d / CC ~30min)** — Phase 1 Alembic 迁移序列文档
- [ ] **AP5 (P1, human ~0.5d / CC ~15min)** — seed allowlist 回归 + 切 phase1 enforced

### Sub-phase 1a Foundation (v0.9.1.0)

- [ ] **CEO-002** 4 方案决策表归档确认
- [ ] **CEO-005** query response schema 加 AuthorityMetadata
- [ ] **CEO-006** Authority Matrix 文档
- [ ] **CEO-012** WorkLine SafetyZone / shared-device manifest schema
- [ ] **H1** C3 schema inventory 测试
- [ ] **H6** APP_DEBUG hard guard

### Sub-phase 1b ACL Ports & Device (v0.9.2.0)

- [ ] **CEO-001** wms_integration 7 ports 实现 + 28 contract test
- [ ] **CEO-010** DeviceCommand ECS contract + 12 case + FK 环消解 Alembic
- [ ] **CEO-013** ExternalContractProfile + provider simulator registry + 10 case
- [ ] **H4** DeviceCommand typed params + extra="forbid"
- [ ] C1 5 处违规：handling + callback 清理

### Sub-phase 1c Runtime Skeleton (v0.9.3.0)

- [ ] **CEO-007** runtime/orchestration 7 表 + worker + 4 unit test 文件 30 case
- [ ] **CEO-008** ConveyorQueueMembership 动态队列模型 + 6 case
- [ ] **CEO-011** WorkLine manifest version pin + 4 case
- [ ] **H5** idempotency_keys 表 schema + WES 内部 key 命名约束
- [ ] **BC-02 strict xfail 解除** + 5 case
- [ ] **BC-07 characterization → contract test 升级** + 5 case

### Sub-phase 1d Capability Boundary (v0.9.4.0)

- [ ] **CEO-009** RuntimeCapabilityContext / CapabilityPortRegistry
- [ ] **H2** type guard 拒绝 inbound normalizer
- [ ] **H3** import-linter `capability-isolation` contract 接入 git-quality-gate
- [ ] Phase 2 go/no-go 评审准备（重新跑 autoplan）

## Files Reference

| File | Change |
| --- | --- |
| `docs/architecture/workline-and-plugin-restructuring.md` | 更新主计划 §10.2 CEO-010 验证栏（AP2 FK 环消解）；§10.2 CEO-001 验收门禁措辞修正 |
| `docs/architecture/authority-matrix.md` | 新增（CEO-006） |
| `docs/architecture/runtime-orchestration-spec.md` | 新增子 SPEC（AP3） |
| `docs/architecture/phase-1-alembic-migration-sequence.md` | 新增（AP4） |
| `docs/architecture/adr/workline-restructuring/0009-shared-contracts-package.md` | 新增 ADR（AP1） |
| `src/app/contracts/__init__.py` | 新增共享 contract 层（AP1） |
| `src/app/contracts/external_contract_profile.py` | 从 `tests/support/external_contract_profile.py` 升级（CEO-013） |
| `src/app/contracts/runtime_capability_profile.py` | 新增（CEO-013） |
| `src/app/contracts/inbound_normalizer_profile.py` | 新增（CEO-013） |
| `src/app/wms_integration/ports/__init__.py` | 新增 ports 目录（CEO-001） |
| `src/app/wms_integration/ports/master_data.py` | 新增（CEO-001） |
| `src/app/wms_integration/ports/document.py` | 新增（CEO-001） |
| `src/app/wms_integration/ports/inventory_query.py` | 新增（CEO-001，由 WmsInventoryPort 拆分） |
| `src/app/wms_integration/ports/inventory_transaction.py` | 新增（CEO-001，由 WmsInventoryPort 拆分） |
| `src/app/wms_integration/ports/fulfillment.py` | 新增（CEO-001） |
| `src/app/wms_integration/ports/event.py` | 新增（CEO-001） |
| `src/app/wms_integration/ports/reconciliation_query.py` | 新增（CEO-001） |
| `src/app/wms_integration/services/transport_contract.py` | 破坏性重写为 FulfillmentPort 子模块（C2 seed 清理） |
| `src/app/wms_integration/services/service_locator.py` | **删除**（I3 不变量） |
| `src/app/wms_integration/services/typed_ports.py` | 重构：保留三段事务契约，按 port 拆分（CEO-001） |
| `src/app/handling/services/gateway.py` | 改为通过 `WmsFulfillmentPort` 接口注入（C1 seed 清理） |
| `src/app/callback/services/callback_ingress_service.py` | 改为通过 `WmsEventPort` normalizer 注册表（C1 seed 清理） |
| `src/app/runtime/__init__.py` | 新增（CEO-007） |
| `src/app/runtime/orchestration/__init__.py` | 新增（CEO-007） |
| `src/app/runtime/orchestration/models/execution_session.py` | 新增（CEO-007） |
| `src/app/runtime/orchestration/models/execution_correlation.py` | 新增（CEO-007） |
| `src/app/runtime/orchestration/models/execution_work_item.py` | 新增（CEO-007） |
| `src/app/runtime/orchestration/models/runtime_inbox.py` | 新增（CEO-007） |
| `src/app/runtime/orchestration/models/runtime_timeline.py` | 新增（CEO-007） |
| `src/app/runtime/orchestration/models/runtime_hold.py` | 新增（CEO-007） |
| `src/app/runtime/orchestration/models/runtime_intent_log.py` | 新增（CEO-007） |
| `src/app/runtime/orchestration/models/idempotency_key.py` | 新增（H5） |
| `src/app/runtime/orchestration/models/conveyor_queue_membership.py` | 新增（CEO-008） |
| `src/app/runtime/orchestration/state_machines/__init__.py` | 新增（CEO-007） |
| `src/app/runtime/orchestration/services/runtime_snapshot_assembler.py` | 新增（BC-02 解除） |
| `src/app/runtime/capability_context.py` | 新增（CEO-009 + H2） |
| `src/app/runtime/capability_port_registry.py` | 新增（CEO-009） |
| `src/app/workline/models/manifest.py`（如已存在则扩展） | 新增 SafetyZone / shared_devices 字段（CEO-012） |
| `src/app/workline/models/workline.py` | manifest_version pin 字段（CEO-011） |
| `src/app/device/models/command.py` | typed params union + extra="forbid"（H4 + CEO-010） |
| `src/app/device/services/device_command_dispatcher.py` | IDLE 校验 + in-flight 限制（CEO-010） |
| `src/core/conf.py` | APP_DEBUG hard guard（H6） |
| `migrations/versions/xxx_phase1_runtime_orchestration.py` | 7 张新表 Alembic 迁移（CEO-007） |
| `migrations/versions/xxx_phase1_idempotency_keys.py` | idempotency_keys 表（H5） |
| `migrations/versions/xxx_phase1_conveyor_queue_membership.py` | ConveyorQueueMembership（CEO-008） |
| `migrations/versions/xxx_phase1_device_fk_ring_dissolve.py` | device session_id_int ↔ awaiting_command_id 外键环消解（AP2） |
| `migrations/versions/xxx_phase1_authority_metadata.py` | 各域 query response schema 加 authority block（CEO-005） |
| `pyproject.toml` | 加 import-linter 依赖 + capability-isolation contract（H3） |
| `scripts/git-quality-gate.sh` | 增加 import-linter 调用（H3） |
| `scripts/architecture-guardrails.sh` | R-I3a 关键字扩展到 `WmsEventPort\|DeviceEventPort\|RuntimeInbox.*Consumer\|.*Dto$`；C4 manifest YAML 扫描 |
| `scripts/architecture-guardrails.allowlist` | drop_phase=phase1 seed 项清理（如果 resource/* 跨域 session FK 已迁移） |
| `scripts/phase1-go-nogo.sh` | 新增（Phase 1 → 2 一键验证） |
| `tests/contracts/wms_integration/test_master_data_port_contract.py` | 新增（CEO-001） |
| `tests/contracts/wms_integration/test_document_port_contract.py` | 新增（CEO-001） |
| `tests/contracts/wms_integration/test_inventory_query_port_contract.py` | 新增（CEO-001） |
| `tests/contracts/wms_integration/test_inventory_transaction_port_contract.py` | 新增（CEO-001） |
| `tests/contracts/wms_integration/test_fulfillment_port_contract.py` | 新增（CEO-001） |
| `tests/contracts/wms_integration/test_event_port_contract.py` | 新增（CEO-001） |
| `tests/contracts/wms_integration/test_reconciliation_query_port_contract.py` | 新增（CEO-001） |
| `tests/contracts/device/test_device_command_contract.py` | 新增（CEO-010） |
| `tests/contracts/external_contracts/test_external_contract_profile_registry.py` | 新增（CEO-013） |
| `tests/contracts/external_contracts/test_provider_simulator_behavior.py` | 新增（CEO-013） |
| `tests/unit/runtime/orchestration/test_execution_session_lifecycle.py` | 新增（CEO-007） |
| `tests/unit/runtime/orchestration/test_runtime_inbox_state_machine.py` | 新增（CEO-007） |
| `tests/unit/runtime/orchestration/test_runtime_intent_log_effect_ledger.py` | 新增（CEO-007） |
| `tests/unit/runtime/orchestration/test_execution_correlation_key.py` | 新增（CEO-007） |
| `tests/unit/runtime/test_conveyor_queue_membership.py` | 新增（CEO-008） |
| `tests/unit/workline/test_manifest_version_pin.py` | 新增（CEO-011） |
| `tests/unit/workline/test_safety_zone_validator.py` | 新增（CEO-012） |
| `tests/unit/workline/test_shared_device_manifest.py` | 新增（CEO-012） |
| `tests/architecture/test_c3_response_schema_inventory.py` | 新增（H1） |
| `tests/architecture/test_i2_idempotency_schema.py` | 新增（H5） |
| `tests/architecture/test_authority_matrix_doc_consistency.py` | 新增（CEO-006） |
| `tests/api_auth/test_settings_hard_guard.py` | 新增（H6） |
| `tests/contracts/workline/test_runtime_snapshot_contract.py` | 修改：移除 strict xfail，补 5 case（BC-02 解除） |
| `tests/contracts/workline/test_sorter_inbound_intent_log_contract.py` | 新增（BC-07 升级） |
| `tests/support/external_contract_profile.py` | **删除**（迁到 src/app/contracts/） |
| `tests/support/runtime_inbox_contract.py` | 保留或迁到 src（CEO-007 决定） |

## Out of Scope

- 不迁移旧执行入口（Phase 2 范围）
- 不实现完整 HMAC body 签名（Phase 3 ENG-008，Phase 1 仅落 spec 占位 + security_profile schema 字段）
- 不实现 idempotency 同 key 不同 hash 409 + 审计（Phase 3 ENG-009，Phase 1 仅落 idempotency_keys 表 schema）
- 不实现 plane 接口完整 RBAC + 脱敏 + 审计（Phase 3 ENG-021 + plane-read-model-spec）
- 不实现 ReconciliationManager + 5/30 分钟超时升级（Phase 3 ENG-002）
- 不迁移 `runtime_hold.source_idempotency_key` 到复合主键（Phase 2/3）
- 不展开 Phase 3 的 11 态机完整转移表、HMAC canonical、PlaneSceneView/Snapshot 完整 schema
- 不为旧 API、旧表名、旧 plugin 形态提供兼容承诺

## Related

- `docs/architecture/workline-and-plugin-restructuring.md`（v4 草案，主计划）
- `docs/superpowers/specs/2026-06-25-workline-restructuring-phase-0-spec.md`（Phase 0 SPEC）
- `docs/architecture/target-state-contract.md`（Phase 0 P0-001 交付）
- `docs/architecture/legacy-cleanup-matrix.{md,csv}`（Phase 0 P0-002 交付）
- `docs/architecture/session-correlation-matrix.md`（Phase 0 P0-004 交付）
- `docs/architecture/device-command-contract.md`（Phase 0 P0-005 交付）
- `docs/contracts/external-contract-profile.md`（Phase 0 P0-006 交付）
- `docs/architecture/architecture-guardrails-spec.md`（Phase 0 P0-007 交付）
- `docs/integration/wms_rcs_interface_requirements.md`
- `docs/integration/third_party_integration_whitepaper.md`

## AUTOPLAN REVIEW REPORT

| Review | Trigger | Why | Status | Findings |
|--------|---------|-----|--------|----------|
| Architecture Review | system-architect subagent | Phase 1 go/no-go + sub-phase 拆分 + 关键路径 | **GO with conditions** | 5 项 architectural prerequisites + 推荐 4 sub-phase 拆分 |
| Backend Implementation Review | backend-architect subagent | wms_integration 拆分 + Alembic 顺序 + C1 实际清理范围 | **GO with conditions** | 2649 LOC 盘点 + 7 表迁移顺序 + C1 5 处实际只能 Phase 1 清理 2 处 |
| Security Review | security-engineer subagent | Phase 1 进入 enforced 模式就绪度 + I1-I3 推进 + 攻击面变化 | **READY-WITH-CONDITIONS** | 6 项 security HIGH 任务必须并入 Phase 1 |
| Quality Engineering Review | quality-engineer subagent | strict xfail 解除路径 + 新测试预估 + 覆盖率目标 + go/no-go 基线 | **GO** | BC-02 唯一可解除；~110-120 case / ~15 测试文件；9 项硬门禁 |

- **VERDICT:** GO with conditions — 4 维评审一致认可 Phase 1 范围、依赖、风险均清晰可解，但必须在 sprint 0 完成 5 architectural + 6 security 共 11 项 prerequisites。

NO UNRESOLVED DECISIONS（autoplan 评审已就 4 大风险给出明确路径）
