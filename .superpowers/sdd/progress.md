# WMS 全工厂接入 — SDD Progress Ledger

Branch: `feature/wms-full-factory-integration`
Base: `6944d92e WIP: 完成 WMS 实施前工程评审`
Plan: `docs/superpowers/specs/2026-07-28-wms-full-factory-integration-design.md`

## Global Constraints

- 严格遵循 DRY、KISS、SOLID、YAGNI；不得保留向后兼容特性。
- API → Service → Repository → Database，禁止跨层调用。
- 新工厂在 WMS 完全兼容合同的前提下仅配置接入；本期不实现 Factory Adapter。
- QUERY 与 WMS 数据修改使用直接 REST；仅 CTU/AGV 调度任务使用 ACK/status 模式。
- WMS data lane 与 fulfillment lane 资源隔离，但复用同一 typed Gateway/EFFECT 执行引擎。
- 无旧版本、旧数据迁移要求；未发布迁移可以合并或重建。
- 用户已授权修改 GitNexus HIGH/CRITICAL 影响面；仍须先分析、控制在任务边界内并同步所有直接调用与回归测试。
- 所有项目命令使用 `uv run ...`；实现遵循 TDD。

## Tasks

- Task 1: complete — `6944d92e..6b2a3a23`，独立验收 `Spec Compliance ✅ / Approved`
- Task 2: complete — `68f0b837..a3470f32`，独立验收 `Spec Compliance ✅ / Approved`
- Task 3: complete — `8fc4785e..0a37aa4d`，独立验收 `Spec Compliance ✅ / Approved`
- Task 4: complete — `d59215da..234a5888`，独立验收 `Spec Compliance ✅ / Approved`
- Task 5: in progress — 接入 16 项 EFFECT 与双 WMS lane
  - G4.5a verified（2026-07-30）：RuntimeInbox attempt 可分别注册 QUERY 与
    `WmsEffectPreparationPort`；Stage 3 为当前 attempt 注入 effect Port resolver，避免
    `CAPABILITY_EFFECT_PORT_UNBOUND`，不缓存 registry/DB session。API 与 Celery 均从同一已校验
    `startup.catalog` 构造并发布 preparation runtime；关闭只解绑 owner，不回收外部资源。
    定向 runtime/startup 回归 `70 passed`，Ruff 与 diff check 通过；当时遗留的 fixture 返回合同漂移已在
    后续 T5 fixture-contract checkpoint 单独收口。
  - G4.5b1 verified（2026-07-30）：在唯一 System Capability / RuntimeIntentLog / SystemOutbox 管线内增加
    极窄 domain authority；静态 allowlist 仅允许 `SMT_INBOUND_HANDOFF → E11`。review fix 后由 production
    resolver 以 `FOR UPDATE` 锁定 correlation/handoff demand/workline，并从持久化 owner/release/workline
    事实派生 producer 与完整 binding snapshot，caller ORM-like 对象不再具备授权效力。domain MATCH 对
    `correlation_id + binding_snapshot_json` 做精确 reconciliation，拒绝同 payload 的 owner/workline/
    correlation 漂移。`RuntimeIntentLog.execution_session_id` 仅放宽 nullability 且保留 FK；真实
    PostgreSQL effect 回归 `5 passed`。scanner、handoff/FullBoxExchange、Celery、Handling/Rack 均未修改。
  - G4.5b2 verified（2026-07-30）：仅对已持久化且具备 `smt-inbound-handoff:<demand-id>` correlation 的
    handoff demand，scanner 逐条短事务通过 runtime domain authority 创建 typed E11。候选一次锁定并稳定只选
    一个满箱；preparation 原子写 owner、active root、Outbox 并置 parent 为
    `WAITING_FULL_BOX_EXCHANGE`。提交后仅唤醒 `WMS_FULFILLMENT`，入队失败保留 durable Outbox；本轮坏
    demand 会排除后继续处理健康 demand。缺 correlation、阶段门漂移或未绑定 runtime 全部 rollback/fail closed。
    真实 PostgreSQL scanner happy/missing-correlation 与两满箱 terminal 串行回归通过；不创建 release fact，T6
    仍负责粗分机移出事实与 demand producer。
  - legacy cleanup verified（2026-07-30，待独立复审）：删除 generic Rack/Handling RuntimeIntent 全链、
    旧聚合模型/服务/回调 fallback、SingleLayer façade、E11 manual/retry 旁路及 legacy manifest；生成
    `46f11dd0a874` drop migration。独立临时 PostgreSQL cold-start 到 head 后五张旧表均为 0；
    `tests/workline_runtime/` `1128 passed`，WMS/E11/粗分合同 `457 passed`，架构/absence/topology
    `52 passed`，默认收集 `5102 tests`，quality profile 通过。首轮复审要求把 batch ACK 绑定原 typed request
    与首次 ACK；4 类 forged/drift RED 精确复现后，E12 全量、E13 有序前缀及跨状态 provider/scope 冻结均已
    复用生产 validator 收口，reviewer/guardrail 定向 `50 passed` 且 quality 再次通过。当前只记录 verified
    checkpoint，不提前完成 T5。
- Task 6: pending — 固化粗分和分拣对象级流水
- Task 7: pending — 替换 WMS 普通事件与 status hint
- Task 8: pending — 建立 35 项参数化合同矩阵
- Task 9: pending — 关闭状态恢复和对账门禁
- Task 10: pending — 执行单 revision 冷启动与协议 GO

## Baseline

- `uv run pytest tests/`: 4253 passed, 5 skipped

## Minor Findings

- Verified checkpoint — T5 runtime fixture-contract synchronization（2026-07-30，非 Task 5 完成标记）：仅同步测试
  夹具与已落地生产合同。writeback 统一断言 `RuntimeInboxWriteBackResult.disposition`，effect fake 使用真实
  `RuntimeIntentEffectResult`（包含 `outbox_dispatch_targets`），WMS projector fake 接收并断言 frozen ACK，
  generated-index 由静态 `WMS_OPERATIONS` 注册表验证全部 40 项 capability（不再冻结旧 24 项计数）。
  `uv run pytest tests/workline_runtime/ -q` 实测 `1129 passed`；Task 5 仍为 in progress，后续实现/审查门禁不因
  本测试收口而跳过。
