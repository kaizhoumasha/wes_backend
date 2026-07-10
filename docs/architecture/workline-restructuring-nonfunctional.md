> **拆分说明**: 本文档从 `workline-and-plugin-restructuring.md` v7 拆分（2026-07-10）。
> 完整设计目标与 frontmatter 仍以原文件为准, 详见同目录 index。
> 章节对应: nonfunctional = 原文件 §8 非功能性设计。

---

## 8. 非功能性设计

### 8.1 性能设计

| 指标 | 目标 |
| --- | --- |
| Plane 接口 P95 | < 500ms（无 10x load）；< 1.5s（10x load） |
| 启动时 full reconcile | 1 条 WorkLine < 5 分钟 |
| 增量 reconcile | 5 分钟周期 |
| 关键业务语义测试 | characterization + contract tests 覆盖 |
| ConveyorQueueMembership active membership 写入 | 同 WorkLine 下 active 唯一约束 |
| DeviceCommand dispatch | 下发前校验 ECS 设备状态为 IDLE；RUNNING 有界等待；ERROR/OFFLINE/查询超时指数退避（1s/2s/4s，最多 3 次）；按 DeviceDispatchPolicy 串行/限流/取消 |
| RuntimeInbox 积压 | 超过 workline 阈值进入降级：停止新 effect、保留 ACK + evidence、告警 |
| DeviceRuntime 状态快照 | 默认 TTL 1000ms；过期必须重新查询 ECS；查询失败按短退避处理 |

### 8.2 容量设计

| 资源 | 上限 | 处置 |
| --- | --- | --- |
| `PlaneSnapshot.conflicts[]` | ≤ 50 | 超出打 `truncated=true` + `total_counts` 字段 |
| `PlaneSnapshot.in_transfer[]` | ≤ 100，限 active 30 天内 | 同上 |
| `PlaneSnapshot.active_material_units[]` | ≤ 200，限 active 30 天内 | 同上 |
| `PlaneSnapshot.devices[]` | ≤ 50，by last_event_at desc | 同上 |
| `PlaneSnapshot.queue_memberships[]` | ≤ 200，top by entered_at desc | 同上 |
| 1 条 WorkLine 同时活跃 session | ≤ 100（首版限 50） | 监控告警 |
| `idempotency_keys` TTL | 30 天 | 超时允许同 key 不同 hash 覆盖 |
| `material_units` per pkg_code | 唯一索引 | 1 个 active per pkg_code |
| `ConveyorQueueMembership` per bin_code | 同 WorkLine active 唯一约束 | 1 个 ACTIVE per bin_code |
| `DeviceCommand` in-flight per device | 默认 1，允许 manifest 按设备能力覆盖 | 超限或设备 RUNNING 时等待到 IDLE 或 deadline；ERROR/OFFLINE/查询超时短退避耗尽后 RuntimeHold，不排队无限增长 |
| `RuntimeInbox` unprocessed per workline | 默认 1,000 | 超限停止新 effect，优先处理安全/错误/结果事件 |

### 8.3 可靠性设计

| 维度 | 设计 |
| --- | --- |
| **Failure 隔离** | External callback 异步处理失败不影响 API ACK；circuit breaker 隔离下游故障；RuntimeInbox 死信进入人工审计 |
| **Graceful degradation** | plane 接口超载自动降级（精简 `devices[]` 和 `in_transfer[]` 字段）；RuntimeInbox 积压时停止新 effect 并保留 callback ACK/evidence |
| **Recovery decision** | RECONCILING 决议模型：owner-scoped `resolution_decision` + evidence；5/30 分钟两阶段超时升级 |
| **Idempotency** | 跨域 `idempotency_key` 复合主键；30 天 TTL 抗重试风暴 |
| **Backup & restore** | WMS 主数据不在 WES（WMS 是权威）；WES active projection 可从 WMS 事件、查询和本地 evidence 重放恢复 |
| **WMS evidence retention** | WMS evidence 必须支持 retention/archive 策略；保护 active trace、人工对账中 evidence 和安全审计，不允许无界增长 |
| **Benchmark gate** | Runtime worker、RuntimeInbox claim、ConveyorQueueMembership writer、ECS status GET + command POST 必须有基准场景；优化不得绕过 ECS 实时 IDLE 准入事实 |
| **Integration lab** | WMS/ECS simulator、scenario runner、sandbox provider profile 和 contract fixture 必须先于现场联调完成；simulator 只能走正式 port contract |
| **Replay recovery** | RuntimeInbox、RuntimeIntentLog、DeviceCommand、WMS fulfillment 与 projection evidence 必须支持脱敏录制和 deterministic replay，用于复现乱序、重复、超时和拒绝 |

**Benchmark gate 最小验收**：

| 场景 | 基线规模 | 验收口径 | 失败处理 |
| --- | --- | --- | --- |
| Plane snapshot | 1 条 WorkLine、10 队列、50 设备、100 active sessions、200 active objects | P95 < 500ms；10x load P95 < 1.5s；返回 `truncated/total_counts` | 阻塞对应 Phase 完成 |
| RuntimeInbox claim | 1,000 unprocessed inbox、4 worker 并发 claim | 不重复 claim；dead-letter 可重放；claim P95 需记录基线 | 阻塞 runtime/orchestration 完成 |
| ConveyorQueueMembership writer | 同 WorkLine 200 active memberships、同 bin/placeholder 并发写入 | 唯一冲突只幂等重读或 RECONCILING；主 callback 不回滚 | 阻塞动态队列模型完成 |
| ECS status + command POST | status GET + command POST 串行/限流/timeout/mock failure | dispatch 前必须验证实时 IDLE 或有效快照；ERROR/OFFLINE/UNKNOWN 短退避耗尽进入 RuntimeHold | 阻塞 DeviceCommand contract 完成 |
| WMS/ECS simulator | 1 条完整入库链路 + 乱序/重复/超时/拒绝/断网 fixture | 正式 port contract 通过；projection diff、timeline、reconciliation 结果可断言 | 阻塞 IntegrationLab 完成 |

基准命令必须随对应任务固化到 `tests/load/` 或等价脚本：RuntimeInbox claim 随 CEO-007，ConveyorQueueMembership writer 随 CEO-008/ENG-016，ECS status + command POST 随 CEO-010，Plane snapshot 随 Phase 3 `plane-read-model-spec.md`。任何性能优化不得删除 ECS 实时 IDLE 准入、idempotency、HMAC 或 evidence 写入。

### 8.4 可观测性设计

| 维度 | 设计 |
| --- | --- |
| **Trace** | `ExecutionCorrelation.trace_id` 跨域追踪；`evidence_json.trace_id` 单条 evidence 追踪 |
| **Metrics** | `audit_logs`（plane 读取 + 安全事件 + 破坏性迁移审计 + WMS 漂移告警）；RuntimeInbox backlog；`correlation_resolution_failed_total`；Outbox blocked；DeviceCommand ACK age；WMS breaker/evidence |
| **Logging** | 结构化日志；统一 `payload` schema |
| **Alerting** | 5/30 分钟 RECONCILING 超时升级；10x load 触发降级告警；HMAC 失败 401 告警；idempotency 409 告警；WMS breaker OPEN/HALF_OPEN；evidence 写入失败；DeviceCommand ACK 前假死 |
| **Dashboard** | 平面态势（scene + snapshot）；冲突视图（conflicts[]）；恢复决议视图（resolution decisions） |
| **Semantic attributes** | 观测属性统一包含 `trace_id`, `correlation_id`, `provider_code`, `contract_version`, `operation_kind`, `command_code`, `source_event_id`, `workline_code`, `execution_session_id?` |

**观测口径**：

- 必须区分 RuntimeInbox `RESOURCE_WAIT` 与 Outbox `BLOCKED_RESOURCE`，避免把入口阻塞、外部履约等待和设备 dispatch busy 混成一个指标。
- 必须区分本地 `DeviceRuntime.diagnostic_state=IDLE` 与 ECS 实时 status probe `IDLE`；后者才是 dispatch admission 的放行事实。
- WMS timeout、5xx、business reject、breaker open、evidence 写入失败按 operation/provider 聚合，阈值在现场数据稳定后配置，不在首版硬编码。
- WMS/ECS HTTP 调用、callback normalize、RuntimeInbox claim、RuntimeIntentLog dispatch、DeviceCommand ACK/RESULT、ScenarioReplayRunner 都必须按 OpenTelemetry 风格定义 span name、metric name 和稳定 attributes；禁止临时日志字段替代观测合同。
- **Trace backend 与 sampling 策略**：具体 trace backend（Jaeger / Tempo / SkyWalking 等）由 Phase 3 ENG-021 SPEC 决策；首版现场运行默认 sampling 100%（现场数据量小）。压测、scenario replay 和 simulator 批量回放可在 ENG-021 SPEC 中使用独立采样策略，但异常 trace（RECONCILING / RuntimeHold / 409 / HMAC 失败）必须 100% 保留，不受采样率影响。

### 8.5 可维护性设计

| 维度 | 设计 |
| --- | --- |
| **Modularity** | 8 个域独立演进；域间通过 port 接口；域内 repository 隔离 |
| **Testability** | 关键业务语义 characterization tests；每个新 capability unit + integration + regression 三层覆盖 |
| **Documentation** | 顶层设计（本文件）+ 8 个 ADR（关键决策）+ 2 个 review 存档（autoplan 评审）；详细 SPEC 不在本文展开，Phase 启动前或启动时按需生成 |
| **Migration discipline** | 可逆 schema 走 Alembic upgrade + downgrade；数据重塑和破坏性清理必须说明 dry-run、快照回滚和清理矩阵 |
| **Naming discipline** | 目标态命名优先；跨域 correlation key 替代 session FK；typed Pydantic 模型替代裸字符串/JSON |
| **Toggle discipline** | 仅允许 typed release/ops toggle；每个 toggle 必须有 owner、expiry、scope、default、rollback 和测试矩阵；禁止 toggle 跳过安全/幂等/evidence |

---

