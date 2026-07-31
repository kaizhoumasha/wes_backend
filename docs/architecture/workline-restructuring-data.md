> **拆分说明**: 本文档从 `workline-and-plugin-restructuring.md` v7 拆分（2026-07-10）。
> 完整设计目标与 frontmatter 仍以原文件为准, 详见同目录 index。
> 章节对应: data = 原文件 §4 数据设计。

---

## 4. 数据设计

### 4.1 域核心实体

runtime 域内表可使用 `execution_session_id` 作为 `ExecutionSession` FK；若实现层沿用字段名 `session_id`，必须标注为“仅 runtime 域内 FK”。跨域实体只允许持有 `ExecutionCorrelation.correlation_id`，不得重新扩散 `execution_session.id` 强 FK。

| 域 | 核心实体 | 关键字段 |
| --- | --- | --- |
| workline | WorkLine | id, line_code, manifest_yaml, status, config_version |
| workline | ConveyorLine | id, workline_id, code, label, layout |
| workline | PipelineQueue | id, workline_id, conveyor_code, code, role, capacity, order_policy |
| workline | EntryPoint / ExitPoint | id, workline_id, code, conveyor_code, queue_code, external_handler |
| workline | Device (配置) | id, workline_id, role, code, capabilities |
| runtime | ExecutionSession | id, workline_id, state, started_at, ended_at |
| runtime | ExecutionWorkItem | id, execution_session_id（仅 runtime 域内 FK）, correlation_id, object_type, object_key, current_step, status, parent_correlation_id, concurrency_scope, deadline_at, lease_expires_at, idempotency_key |
| runtime | RuntimeInbox | id, execution_session_id（仅 runtime 域内 FK，可为空）, correlation_resolution_status, source, provider_code, event_type, source_event_id, payload_hash, status, attempt_count, next_retry_at, dead_letter_at |
| runtime | RuntimeTimeline | id, execution_session_id（仅 runtime 域内 FK）, event_type, trace_id, occurred_at |
| runtime | RuntimeHold | id, execution_session_id（仅 runtime 域内 FK）, reason, hold_type, scope_type, scope_key, affected_work_item_id, affected_device_code, affected_resource_key, allowed_next_effect_scope, created_at, resolved_at |
| runtime | RuntimeIntentLog | id, execution_session_id（仅 runtime 域内 FK）, correlation_id, target_domain, target_action, request_hash, provider_code, idempotency_key, dispatch_status, attempt_count |
| runtime | ExecutionCorrelation | correlation_id, execution_session_id, trace_id, source_event_id, business_owner_key, created_at |
| runtime | EffectPort（仅契约/dispatcher） | 不建表；从 RuntimeIntentLog 读取待派发 effect，返回 dispatch 结果 |
| runtime | InboundEventPort（仅契约） | 外部 callback/event normalizer 契约；不建表；只写 RuntimeInbox |
| handling | HandlingOperation | id, workline_id, kind, coarse_business_status, source, target |
| handling | HandlingMove | id, handling_operation_id, from_location, to_location, kind, status |
| runtime | ConveyorQueueMembership | id, bin_code/placeholder_key, workline_id, conveyor_code, queue_code, status, entered_at, left_at |
| resource | RackPlacement / RackBinMount / BinPlacement | id, workline_id, rack_code, bin_code, status, correlation_id, evidence |
| resource | BinMaterialMount / BinCellOccupancy | id, cell_code, pkg_code, material_identity, status, correlation_id |
| resource | ResourceStateEvent | id, workline_id, event_type, source_event_id, payload, occurred_at |
| resource | RuntimeLocationEvent | id, workline_id, object_type, object_key, location_scope, location_code, business_step, source, evidence_json, occurred_at |
| material | material_units | id, pkg_code, material_identity_key, status, location_summary, current_session_correlation_id |
| device | DeviceRuntime | id, device_code, role, last_event_at, last_result_at, diagnostic_state |
| wms_integration | operation-specific fulfillment evidence | operation_identity, idempotency_key, provider_reference, status, typed terminal result |
| wms_integration | WmsCallbackEnvelope | id, callback_type, source_event_id, source_version, signature, timestamp, raw_body_hash, normalized_evidence_json |
| reconciliation | ReconciliationRecord | id, conflict_type, detected_at, resolution_decision, owner_scope, allowed_next_effect_scope, resolved_at, evidence |

### 4.2 ExecutionCorrelation correlation key

**问题**：实测 16+ 模型文件包含 `session_id` / `execution_session_id` / `current_session_id` 跨域 FK（`workline/models/runtime.py` 13 处、timeline.py 7 处、inbox.py 4 处、smt_inbound_handoff.py 3 处、operation.py 3 处、`handling/models/bin_transit_membership.py` 2 处、object_transition_event.py 2 处、material_unit.py 2 处、bin_cell_reservation.py 2 处、`resource/models/resource.py` 2 处 等）。**runtime 之外的域不能把 `execution_session.id` 作为强 FK 扩散**。

**解决**：引入 `ExecutionCorrelation` correlation key：

| 字段组 | 用途 |
| --- | --- |
| `correlation_id` | 跨域唯一业务关联键，作为主键 |
| `execution_session_id` | runtime 域内回放用 FK；其他域不得引用 |
| `trace_id` / `source_event_id` | trace 时间线与外部事件归因 |
| `business_owner_key` | 业务 owner 审计、查询和冲突定位 |

**索引**：

- `correlation_id` PRIMARY KEY
- `(execution_session_id, created_at)` 用于 runtime 域内回放
- `(trace_id, created_at)` 用于跨域 trace 时间线
- `(business_owner_key, created_at)` 用于 12 审计

**破坏性迁移策略**（ENG-001）：

- 现有 16+ 文件的 `session_id` FK 改造（rename / rebuild / drop-FK）
- 输出 `docs/architecture/session-correlation-matrix.md` 列出 per-file 迁移路径
- start_admission / runtime query / handoff 等旧流程只保留行为契约测试，代码允许重建
- 不保留旧 string `session_id` 兼容入口（C0 已决定破坏性切换）

### 4.3 typed `ExternalReference` 与 `EvidenceEnvelope`

**问题**：resource 域的 `rack_code / bin_code / location_code` 等外部引用当前是裸字符串，无 schema 无版本无对账标记；`evidence_json` 字段是裸 JSON dict，跨域写入方自由结构。

**解决**：typed Pydantic 模型，详细 schema 在 Phase SPEC 展开。

| 模型 | 字段组 | 用途 |
| --- | --- | --- |
| `ExternalReference` | `system`, `object_type`, `code`, `schema_version`, `validated_at`, `source_version` | 标识外部系统对象和最近对账版本，替代裸字符串 |
| `EvidenceEnvelope` | `schema_version`, `source_system`, `source_event_id`, `source_version`, `validated_at`, `request_hash`, payload | 统一 evidence 来源、版本、幂等和审计字段，替代裸 JSON |

**索引**：GIN 索引支持 `ExternalReference.code` + `EvidenceEnvelope.source_event_id` 等结构化字段查询。

**evidence schema 变更日志**：`docs/contracts/evidence-catalog.md` 维护每次 schema 升级的 source/target 映射。

### 4.4 conveyor queue membership 数据模型

**目标**：滚筒线队列是 WorkLine manifest 的动态配置，不是系统级 enum。队列 membership 是 runtime/orchestration 拥有的 current-state projection，只记录“某个料箱/占位符当前位于某条滚筒线的哪个 manifest queue”，不把具体队列名称写死到系统模型。Handling 只负责搬运意图和履约请求生命周期，不拥有滚筒线队列状态。

| 字段组 | 用途 |
| --- | --- |
| object identity | `bin_code` 或 `placeholder_key`，支持扫码前占位 |
| manifest scope | `workline_id`, `workline_code`, `conveyor_code`, `queue_code`, `queue_role` |
| state | `membership_status = ACTIVE / LEFT / RECONCILING`, `entered_at`, `left_at` |
| evidence | `handling_operation_id`, `handling_move_id`, `trace_id`, `execution_correlation_id`, `evidence_json` |

**约束**：

- `queue_code` 必须来自当前 WorkLine manifest 的 `pipeline_queues.code`
- `queue_role` 是写入时的 manifest role 快照，用于审计，不作为 enum 约束
- active 唯一约束保留业务语义：同一 `bin_code` 或 `placeholder_key` 在同一 WorkLine 下最多一个 active membership
- 旧 `BinTransitMembership` / `BinTransitQueue` 允许删除、重命名或迁移到一次性迁移脚本，不进入目标态模型
- 不定义系统级“7 队列”或“8 队列”常量；入口缓冲、扫码点、工位、出口路由、回收等待、NG 等队列只作为 manifest `pipeline_queues[]` 的配置实例存在。
- 多扫码点、Gate3/Gate4、出口路由扫码、回收扫码和多料箱并发写入由
  `ConveyorQueueMembershipWriter` 按 pinned manifest 解析；writer 只依赖 runtime/device event、
  operation-specific typed terminal result 和 `ExecutionCorrelation`。
- 并发写入必须使用 PostgreSQL 行级锁、savepoint/upsert 或等价 CAS；唯一冲突只允许两种结果：幂等重读成功，或写入 `RECONCILING` evidence。禁止让唯一冲突回滚主 callback ACK。
- placeholder resolve、terminal leave、queue switch 必须在同一事务内保证 active membership 收敛；跨事务重复事件依靠 `idempotency_key + request_hash` 识别。
- 投影失败分三类记录：预期并发冲突、外部合同缺字段、内部编程错误。生产默认 best-effort + diagnostic；测试/预发可启用严格模式，对非预期异常 re-raise。

**滚筒线扫描点路由契约**：

分拣机 `SCAN1 / SCAN2 / SCAN3`、粗分机入口/出口等点位不作为系统级 enum 固化；它们是 WorkLine manifest 中的设备角色、队列和节点配置。Runtime capability 必须按 pinned manifest 把“到位/扫码事件”解释成显式路由决策：

- SCAN 事件只来自 ECS/device callback，进入 `RuntimeInbox` 后由 worker 解析 correlation，不允许 callback API 同步返回下一步动作。
- 路由决策必须写 `RuntimeTimeline` 和 `RuntimeIntentLog`，再通过 `DeviceCommandPort` 下发滚筒线业务命令，例如进入工作位、进入 NG、进入退料线或等待换箱。
- 队列 membership 的变化必须由 DeviceResult、operation-specific typed terminal result 或可信
  RuntimeLocationEvent evidence 推进，不能由“已下发命令”直接假定成功。
- CTU/WMS 授权进入滚筒线的料箱必须先记录 `expected_authorized_bin_ids`；SCAN1 扫码结果写入 `actual_scanned_bin_ids`，只有命中授权集合时才允许 placeholder resolve 为 `bin_code` 并进入后续队列。未授权、重复、缺失或码制冲突的料箱进入 NG / RuntimeHold / RECONCILING，不得被静默接收。
- NG、退料、换箱、等待下一料箱都必须是可观测状态；等待状态必须有 deadline 或解除条件，不能无限等待。
- 行为契约测试必须覆盖 SCAN1 授权料箱入工作位/未授权进 NG、SCAN2 工作位到位、SCAN3 退料线/NG、重复扫码、乱序扫码、placeholder resolve 和 queue_code typo 不污染 active projection。

### 4.5 数据迁移策略

**Alembic migration 规范**：

- 可逆 schema migration 必须可 upgrade + downgrade
- 新增表/字段必须包含完整 downgrade
- 迁移顺序：先 schema、后数据（如果有）、最后 application 验证
- 破坏性迁移不保留旧兼容入口
- 不做长期 dual-write；若数据搬迁需要过渡脚本，必须在同一 Phase 给出清理 PR

**破坏性迁移分级**：

| 类型 | 允许操作 | 回滚方式 | 门禁 |
| --- | --- | --- | --- |
| Reversible schema | 新表、新字段、新索引、非破坏性 rename 前置 | Alembic downgrade | upgrade/downgrade + 结构断言 |
| Data reshape | evidence 搬迁、字段重算、旧 payload 归档 | 数据库快照 + 幂等重跑脚本 | dry-run 报告 + 行数校验 + 抽样校验 |
| Destructive cleanup | drop 旧表、旧 enum、旧字段、旧 API 路径 | 数据库快照回滚，不伪造数据 downgrade | 用户确认 + 快照点 + 清理矩阵逐项勾选 |

不可逆清理不能写“假 downgrade”来重建已删除数据。系统未发布，允许破坏性优化，但必须把回滚真实边界写清楚：可逆靠 Alembic，不可逆靠快照和清理矩阵。

**Legacy 数据处理**：

- 旧 evidence 可迁入新 `EvidenceEnvelope`，无法结构化的旧 payload 进入 `legacy_payload`
- 旧表/旧 enum 不作为目标态约束；迁移后可 drop
- 新写入只走目标态 schema

**schema 选择准则**：系统未发布，默认采用 drop/recreate 或新表重建，避免为旧字段形态设计兼容层。只有需要保留 evidence、审计链、外部 request id 或人工对账依据时，才执行 data reshape；普通配置、enum、旧 API 路径和旧插件状态不做 rename 兼容。

---
