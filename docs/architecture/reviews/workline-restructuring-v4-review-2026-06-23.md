# WORKLINE + PLUGIN 重构顶层设计评审报告 (v4)

**评审文件**: `docs/architecture/workline-and-plugin-restructuring.md` v4 (2026-06-23)
**评审维度**: 重构要求 / 项目建设目标 / 物理世界约束 / WES 行业最佳实践
**评审方法**: sequentialthinking × 12 轮 + serena 代码库核对 + websearch WES 最佳实践
**评审人**: plan-eng-review (claude + gstack)
**评审日期**: 2026-06-23
**代码核对状态**: 实测 LOC 与文档 §2.4 完全一致（workline 32,979 / handling 2,229 / wms_integration 2,649 / workline_runtime 10,241 / workline_plugins 3,085）
**决策落盘 ID**: 6505024f (Phase 0 矛盾 3 项) / bee0431a (状态语义 3 项)

---

## 0. 总体评分

| 维度 | 评分 | 说明 |
| --- | --- | --- |
| 完整性 | **A+** | 13 章节 + 8 个 ADR + 28 个 auto-decision + 5 Phase + 16 不变量 |
| 一致性 | **A+** | ADR 决策与文档一致；白皮书约束与设备域一致；实测 LOC 与描述一致 |
| 可执行性 | **A** | 5 Phase critical path 串行；每 Phase 启动条件明确；Task 都有验证 |
| 风险识别 | **A** | 4 CRITICAL gap + 3 跨阶段主题 + 事实修正 2 条 |
| ADR 治理 | **A+** | 8 个关键决策都有 ADR；capability freeze 保护 v0.6-v0.8 |
| WES 行业对齐 | **A** | 与 Hexagonal / ACL / Adapter / event sourcing 模式完全对齐 |
| 物理世界边界 | **A** | WES/WMS/RCS/ECS/AGV/CTU/PLC 边界与白皮书一致 |

**结论**: 文档质量高、ADR 完备、目标态边界清晰。**建议批准进入 Phase 0 实施，但需先解决 3 项必改（CRITICAL）以免 Phase 0 P0-002 卡壳**。

---

## 1. 亮点（应保持）

按 4 维度分类列出 10 项核心亮点：

### 重构要求维度（5 项）
1. **B 方案 go/no-go 指标清晰**（§3.8）：不是默认 B，必然走 C+capability freeze+测试基线+4 critical path 任务——避免一上来就 B
2. **Capability Freeze 概念**（ADR 0001 决策 #3）：保护 v0.6-v0.8 公共契约，破坏性 PR 必须显式声明
3. **目标态契约优先**（§3.7）：旧 API/旧表/旧插件形态不作为新架构约束
4. **ExecutionCorrelation correlation key**（§3.3 + ADR 0007）：跨域不用 session_id FK，符合 event sourcing 最佳实践
5. **RuntimeIntentLog vs RuntimeSessionAggregate 显式拆分**（§3.3 + ADR 0007）：效果与状态源边界清晰

### 项目建设目标维度（2 项）
6. **Authority Matrix 12 事实类型权威来源拆分**（§3.4 + ADR 0008）：避免"影子 WMS"
7. **8 个域边界清晰**（§3.2）：workline 配置 / runtime 执行 / handling / resource / material / device / wms_integration / reconciliation（含 material 作为 WES 唯一自有根实体）

### 物理世界约束维度（2 项）
8. **WES 不与 PLC 通讯 / 不下发物理坐标/关节/安全回路**（§9.6）：与 `third_party_integration_whitepaper.md` §1.3 完全对齐
9. **WMS 履约接口权威，RCS 由 WMS 统一调度**（§2.3 不做 #9）：与 `wms_rcs_interface_requirements.md` §1.2 完全对齐

### WES 行业最佳实践维度（1 项）
10. **ReconciliationManager 5 个合法出口 + 5/30 分钟升级**（§6.4 + ADR 0002）：行业内"投影/回调/现场状态冲突"标准做法

---

## 2. 必改项（CRITICAL，3 项）

### 🔴 C1. §3.7 vs ADR 0001 决策 #4 vs §10.2 CEO-008 三方矛盾

**问题**:
- ADR 0001 决策 #4（taste 决策）："`BinTransitMembership`（8 队列 + RECONCILING + 部分唯一索引）保持不变；新增 `ConveyorQueueProjectionPort` 接口层代替直接 import"
- §3.7 目标态契约表：`BinTransitMembership / BinTransitQueue` "目标态处理 = 提取业务语义后删除" / "以 manifest 动态队列重建为 `ConveyorQueueMembership`"
- §10.2 Phase 1 CEO-008："`ConveyorQueueMembership` 目标模型" + "动态队列 membership 模型替代旧 8 enum 方案"

**风险**: Phase 0 P0-002 `legacy-cleanup-matrix.md` 会卡壳——不知道 BinTransitMembership 表是"破坏性 drop"、"保留做视图"、还是"双写迁移窗口"。

**建议**: 启动 Phase 0 前明确以下三选一：
- **方案 A（推荐）**：语义层用新名 `ConveyorQueueMembership`，物理层保留 `bin_transit_memberships` 表 + 添加 `queue_code VARCHAR` 列（manifest 动态），旧 `current_queue` enum 列迁移为 `queue_role` 快照
- **方案 B（破坏性更强）**：物理表重命名为 `conveyor_queue_memberships`，`BinTransitQueue` enum 替换为 `queue_code` VARCHAR + `queue_role` 快照
- **方案 C（双写窗口）**：保留两表，30 天过渡期后 drop 旧表

**理由**: 与 ADR 0001 决策 #4 "taste 决策" 调性一致；方案 A 是最稳的兼容路径。

**对应决策日志 ID**: 6505024f

### 🔴 C2. §10.3 缺 B 方案回退路径（fallback to C / D）

**问题**:
- §10.3 Phase 2 启动条件: "Phase 0 全部 5 项完成 + Phase 1 全部任务完成 + **重新跑 autoplan 或同等深度评审，确认 B 方案可执行**"
- 但**没有说明**: 如果 Phase 0+1 完成后 autoplan 评审认为 B 不可行，回退到 C / D 的迁移成本和工作量
- 也没有: 暂停条件（如发现阻塞性 bug）、回滚策略

**风险**: 工程风险——B 方案 XL 级别（2-3 月）已投入才发现不可行，回退成本可能等于 2-3 个月 B 方案工作量。

**建议**: 补 §10.3.1 "B 方案暂停/回退条件"：
- **暂停条件**: Phase 1 CEO-007 (RuntimeIntentLog 拆分) 失败 / Phase 0 P0-003 (行为契约测试) 覆盖率 < 70% / 重新评审发现 2+ 个 P0 阻塞
- **回退路径**: B → C（保留 runtime/orchestration 部分实现）→ D（保留 workline 单体 + 7 套 WMS port）
- **回退成本估算**: C 方案增量工作 2-3 周 + 保留 32,979 LOC 中可复用部分

**理由**: "Make the change easy, then make the easy change" 原则；增量重构需要可逆性。

**对应决策日志 ID**: 6505024f

### 🔴 C3. §5.4 idempotency 复合主键缺 WES 内部 key 命名空间约束

**问题**:
- §5.4 复合主键: `(provider_code, operation_kind, idempotency_key)`
- 现有 `runtime_hold_creation_service.py:create_for_callback_deadline_expired` 生成 `f"callback-timeout:{session_id}:{inbox_id}"`
- 现有 `create_for_dispatch_ack_exhausted` 生成 `f"dispatch-ack-exhausted:{outbox_id}:{command_key}"`
- 现有 `create_for_safety_estop` 生成 `f"safety-estop:{incident_id}"`

**风险**: 这些 WES 内部生成的 idempotency_key 与 WMS 提供的 key 在同一复合主键下共存，**撞车风险**（虽然概率低）。WMS 可能传 `WMS_GRN_RECEIVED:request_id=RECONCILE-001`，而 WES 内部 `callback-timeout:100:200` 的 hash 可能在 `request_id` 维度撞车。

**建议**: §5.4 补一条强约束:
- **provider_code 命名空间**: WES 内部生成的 idempotency_key 必须带 provider_code 显式前缀（如 `WES-CALLBACK-TIMEOUT-001`、`WES-DISPATCH-ACK-EXHAUSTED-001`），禁止使用纯 `WES` 命名空间
- **operation_kind 命名空间**: 内部 `operation_kind` 需扩展为 `wes-callback-timeout` / `wes-dispatch-ack-exhausted` / `wes-safety-estop` / `wes-resource-reconciliation` 等细分类
- **格式约定**: 内部 key 格式 `WES-{operation_kind}-{deterministic_hash(source_id, source_event_id)}`，确保跨域跨 provider 唯一

**理由**: 幂等键撞车会触发 409 误报，导致 WES 内部业务被错误拒绝；同时 §5.4 "同 key 不同 request_hash → 409 + 安全审计"会触发安全审计风暴。

**对应决策日志 ID**: 6505024f

---

## 3. 建议改项（MEDIUM，10 项）

### 🟡 M1. §5.2 plane 接口三种实时方案需统一

**问题**: §5.2 同时提到 "1 Hz 轮询足够" / "实时刷新 SSE/WebSocket 或 250ms 轮询" / `/plane/events` 后续扩展——三种方案并存但没有选型。

**建议**: Phase 3 ENG-008 启动前明确"Plane 接口实时性分级":
- `plane/scene` (manifest 派生)：1 Hz 轮询即可（SSE 浪费）
- `plane/snapshot` (active projection)：优先 SSE 长连接，断线 fallback 250ms 轮询
- `plane/events` (增量事件流)：SSE 单向流，客户端订阅

### 🟡 M2. §6.1 BLOCKED_BY_CB 状态语义需在 ADR 0002 显式说明

**问题**: §6.1 把 BLOCKED_BY_CB 列为"非终态"，但 §6.3 描述"CB open → half-open 转移时所有 BLOCKED_BY_CB 请求自动恢复为 REQUESTED"——意味着 BLOCKED_BY_CB 是"系统侧延迟状态"而非业务状态。

**建议**: 在 ADR 0002 补:
- BLOCKED_BY_CB 是"circuit breaker open 期间的占位状态"
- 业务查询 API 应过滤掉 BLOCKED_BY_CB（不在"in-flight 请求列表"中显示）
- BLOCKED_BY_CB 不计入 P95 履约请求指标

### 🟡 M3. §9.1 DRAINING 状态与 §6.4 HOLD 关系需明确

**问题**: §9.1 manifest version pin 提到"DRAINING -> HOLD -> VALIDATE -> ACTIVATE" 流程；但 §6.4 RECONCILING 已有 HOLD。

**建议**: 补 DRAINING vs HOLD 边界:
- **HOLD** (session 状态): 整个 session 暂停接受新 effect
- **DRAINING** (workline 状态): 整个 WorkLine 准备切换 manifest，停止为旧版本创建新 session
- **VALIDATE** (临时状态): 启动时验证新 manifest 不污染 active projection
- 状态机: `DRAINING -> HOLD (per session) -> VALIDATE -> ACTIVE`

### 🟡 M4. §6.4 RECONCILING 触发矩阵缺"物理异常"分类

**问题**: §6.4 触发矩阵列了 5 类（投影冲突 / callback 不一致 / device 状态矛盾 / CB 半开期间旧 callback / WMS drift），但**物理现场异常**未覆盖：传感器抖动（光电误触发）/ 通信丢包（WMS 回调延迟）/ 重复上报（device + WMS 都报成功）。

**建议**: §6.4 补 3 类物理异常处理策略:
- **传感器抖动**: N 秒内同 sensor 多次触发合并为单次；超阈值进入 RuntimeHold
- **通信丢包**: WMS callback TTL 30s 超时后 WES 主动 query WMS 状态
- **重复上报**: 严格幂等（§5.4）+ 允许合并 evidence

### 🟡 M5. §6.6 3 路 UNION 冲突只覆盖"料箱"维度

**问题**: §6.6 "3 路 UNION 冲突 policy" 只描述"料箱"在 `ON_CONVEYOR` + `AT_WORK_POSITION` + `IN_TRANSFER` 的冲突；"货架/命令"维度的冲突 policy 未覆盖。

**建议**: §6.6 补:
- **货架维度**: 一旦 `RackPlacement` 状态进入 `IN_TRANSIT`，新 Placement 写入必须基于 WMS 确认；冲突进 RECONCILING
- **命令维度**: 同 `correlation_id` 不可同时存在 `DeviceCommand.status=RUNNING` 与 WMS typed
  terminal result 已完成的不可解释组合

### 🟡 M6. §9.6 DeviceRuntime 状态缺 UNKNOWN/MAINTENANCE

**问题**: §9.6 DeviceRuntime 状态只有 IDLE/RUNNING/ERROR/OFFLINE（4 态），但物理现场存在"未知"（WES 没收到最近一次状态查询响应）和"维护中"（人工按本地按钮进入维护态）。

**建议**: §9.6 补 2 态:
- **UNKNOWN**: 状态查询超时或未查询；WES 不下发命令，等待 status_snapshot TTL 过期后重新查询
- **MAINTENANCE**: 设备本地进入维护态；WES 跳过该设备直到收到 DEVICE_ONLINE 事件

### 🟡 M7. §3.5 命名澄清"external/ 父目录永不建立"未明示

**问题**: §3.5 命名澄清解释 `wms_integration` 不重命名为 `external/wms`，但读者可能困惑"未来 rcs_integration/agv_integration/ctu_integration 时是否也走 ACL 域而不放 external/ 父目录"。

**建议**: §3.5 末尾补一句:
> **强制约束**: 未来任何外部系统（`rcs_integration` / `agv_integration` / `ctu_integration` 等）一律以 `src/app/<system>_integration/` 镜像命名建立 ACL 域，**永不**建立 `src/app/external/` 父目录。

### 🟡 M8. §11.4 命名规范前缀"Runtime vs Execution"未统一

**问题**: §3.2 / §9.2 用 `runtime/orchestration`（小写）+ `RuntimeSessionAggregate` / `RuntimeInbox` / `RuntimeHold` / `RuntimeIntentLog`；§3.3 / §4.2 / §5.4 / §6.1 用 `ExecutionSession` / `ExecutionCorrelation` / `EffectPort`——两个前缀混用。

**建议**: §11.4 统一:
- **域/包名**: `runtime/orchestration`（执行域 = runtime）
- **聚合根**: `RuntimeSessionAggregate`（状态源）
- **业务名词**: `ExecutionSession` / `ExecutionCorrelation`（业务身份，跨域可见）
- **效果日志**: `RuntimeIntentLog` / `EffectPort`（执行域内部）
- **新代码禁止使用 `WorklineSession` 前缀**（与 Workline 配置域边界混淆）

### 🟡 M9. §9.4 resource 域 work_position_code 归属需明确

**问题**: §9.4 `RackPlacement.work_position_code` 是 WES 自有字段，但没明确"work_position_code 是 WES 自有概念，不引用 WMS location_code"。

**建议**: §9.4 补强:
- `work_position_code` 是 WES 内部的工作位编号（如 `WP-KITTING-01`），与 WMS `location_code`（如 `KITTING_AREA_LOC_01`）是 1:1 映射
- 映射关系由 WMS `WmsMasterDataPort.list_locations()` 获取后写入 WES 配置（`PipelineQueue` 或 `WorklineConfig`）
- WES 业务查询只暴露 `work_position_code`，外部 WMS 引用通过映射表查询

### 🟡 M10. §6.5 WMS master-data drift 处理慢是否会触发 5 分钟升级需明确

**问题**: §6.5 WMS drift 处理动作中 `MISSING_IN_WMS` 5 分钟升级告警、`METADATA_DRIFT` 30 分钟升级 P1；但如果 drift 恢复本身依赖 WMS 网络恢复（WMS 长时间不可用），会持续触发告警风暴。

**建议**: §6.5 补:
- **drift SLA 分级**: drift 恢复 SLA 与 WMS 可用性 SLA 分离
- **降级策略**: WMS 长时间不可用时，进入"drift 待 WMS 恢复"模式，停止重复告警，仅保留 P0 一次告警

---

## 4. 待澄清（需用户/PM 决定，10 项）

1. **§3.7 旧 capability 业务语义保留如何量化**（不变量清单？）—— 建议补"Start admission 不变量 N 条 / Runtime monitor 不变量 M 条"清单
2. **§5.2 plane 接口 P0 不限前端场景还是限定操作员终端/可视化大屏** —— 影响前端消费契约
3. **§6.4 RECONCILING 是否需要分级告警**（info/warn/critical）和 PagerDuty/钉钉集成 —— 影响运维成本
4. **§10.7 B 方案启动后如果发现问题，是暂停 + 重新评审还是直接回滚到 C 方案** —— 决策 C2 必改
5. **§9.6 设备长时间 OFFLINE 是否影响 WorkLine 启停条件** —— 决定 WorkLine manifest 启停门禁
6. **§6.4 物理现场 RECONCILING 是否需要双通道**（设备 push + WES pull）—— 决定 WES 长尾查询 API 是否需要
7. **§3.4 Authority Matrix 未来直连 AGV vs CTU 是否需要在 WMS port 列表中区分** —— 调度模型差异大
8. **§5.4 WMS 提供的 idempotency_key 与 WES 内部生成 key 撞车风险如何处理**（命名空间隔离？）—— 决策 C3 必改
9. **§9.6 业务决策在 Event_Push 响应中偷渡动作的检测机制**（监控告警？）—— 安全审计
10. **§10.4 Phase 4 ENG-014 5 子目录清理矩阵是否应移至 Phase 5**（避免 Phase 4 任务膨胀）—— 依赖图调整

---

## 5. 实施建议

### 5.1 Phase 0 启动前（必做）

| 顺序 | 动作 | 责任方 | 阻塞项 |
| --- | --- | --- | --- |
| 1 | 解决 C1: 选 BinTransitMigration 方案 A/B/C | 架构 lead + DBA | P0-002 legacy 清理矩阵 |
| 2 | 补 §10.3.1 B 方案回退路径 | 架构 lead | Phase 2 启动条件 |
| 3 | 补 §5.4 idempotency 命名空间约束 | 架构 lead + WES owner | ENG-009 |
| 4 | 解决 M1-M3 (plane 实时分级 / BLOCKED_BY_CB / DRAINING) | 架构 lead | ENG-008 / DESIGN-001 |
| 5 | 解决 M8 (Runtime vs Execution 前缀) | 命名规范 owner | 全部新代码 |

### 5.2 Phase 0 期间（建议做）

- 解决 M4-M7 (物理异常 / 3 路 UNION / UNKNOWN 态 / 镜像命名)
- 解决 M9-M10 (work_position_code / drift SLA)

### 5.3 Phase 1 启动前（验证）

- ADR 0001 决策 #4 与 §3.7 表述一致化
- 8 个 ADR 全部加 "Status: Accepted | Superseded by" 字段

### 5.4 Phase 2 启动前（B 方案 go/no-go 重新评审）

- 跑 autoplan 或同等深度评审
- 验证 Phase 0+1 全部完成
- 如 B 不可行，按 §10.3.1 回退路径降级到 C / D

---

## 6. 风险登记

| ID | 等级 | 风险 | 当前对策 | 建议补强 |
| --- | --- | --- | --- | --- |
| R1 | CRITICAL | BinTransitMembership 物理表迁移策略不明 | 暂无 | C1 三选一方案 |
| R2 | CRITICAL | B 方案无回退路径 | 暂无 | C2 §10.3.1 |
| R3 | CRITICAL | idempotency 内部 key 撞车 | 复合主键 | C3 命名空间约束 |
| R4 | HIGH | plane 接口实时方案未选型 | "1Hz / 250ms / SSE" 文字表述 | M1 Phase 3 前选型 |
| R5 | HIGH | BLOCKED_BY_CB 语义不明 | ADR 0002 决策 #19 文字表述 | M2 ADR 增补 |
| R6 | MEDIUM | DRAINING vs HOLD 重复定义 | 暂无 | M3 状态机补 |
| R7 | MEDIUM | 物理现场异常未分类 | 暂无 | M4 §6.4 补 |
| R8 | MEDIUM | 3 路 UNION 冲突维度不全 | 料箱维度有 | M5 §6.6 补 |
| R9 | MEDIUM | DeviceRuntime 状态缺 UNKNOWN/MAINTENANCE | 4 态 | M6 §9.6 补 |
| R10 | LOW | external/ 父目录约束不明 | 命名澄清 5 条理由 | M7 §3.5 末补强约束 |
| R11 | LOW | Runtime vs Execution 前缀混用 | 现行混用 | M8 §11.4 统一 |
| R12 | LOW | work_position_code 归属不明 | §9.4 隐含 | M9 显式 |
| R13 | LOW | WMS drift 告警风暴 | 5/30 分钟升级 | M10 SLA 分级 |

---

## 7. 评审结论

**STATUS**: `DONE_WITH_CONCERNS`

**REASON**: 文档质量高（13 章节 / 8 ADR / 28 决策 / 5 Phase / 16 不变量），实测 LOC 与代码完全一致，白皮书约束与设备域完全对齐。但 3 项必改（CRITICAL）需要在 Phase 0 启动前解决，否则 Phase 0 P0-002 卡壳、Phase 2 启动条件缺失、idempotency 撞车风险。

**ATTEMPTED**:
- sequentialthinking 12 轮结构化分析
- serena 探索 workline/handling/wms_integration/runtime 4 个核心域 + ADR/白皮书/WMS 需求文档
- websearch 验证 WES 行业最佳实践（Hexagonal / ACL / Adapter / correlation key / HMAC / ReconciliationManager）
- 实测 LOC 与文档 §2.4 完全对齐
- 决策日志落盘到 `~/.gstack/projects/kaizhoumasha-wes_backend/decisions.active.json`（ID 6505024f / bee0431a）

**RECOMMENDATION**:
- 批准进入 Phase 0 实施
- 强制要求: 启动 Phase 0 前先解决 C1 / C2 / C3 三项必改
- 建议要求: Phase 0 期间解决 M1-M3 三项建议改
- 持续监控: R4-R13 风险登记 10 项，Phase 1 启动前再 review

---

## 8. 引用

- 评审目标文件: `docs/architecture/workline-and-plugin-restructuring.md`
- 现有 ADR: `docs/architecture/adr/workline-restructuring/0001-0008`
- 现有评审: `docs/architecture/reviews/autoplan-workline-restructuring-2026-06-23.md`
- 决策审计: `docs/architecture/reviews/decision-audit-trail.md`（28 个 auto-decision）
- WMS 接口需求: `docs/integration/wms_rcs_interface_requirements.md`
- 设备白皮书: `docs/integration/third_party_integration_whitepaper.md`
- 决策日志: `~/.gstack/projects/kaizhoumasha-wes_backend/decisions.active.json`（ID 6505024f / bee0431a）
- Eureka 日志: `~/.gstack/analytics/eureka.jsonl`

---

**评审人**: plan-eng-review (claude + gstack)
**评审日期**: 2026-06-23
**评审耗时**: ~15 分钟
**下次评审触发点**: Phase 0 启动前（必改解决后）+ Phase 1 完成后（B 方案 go/no-go 重新评审）
