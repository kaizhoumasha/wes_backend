# T5 G4.5b2：existing handoff demand → typed E11 报告

## 实现

- `FullBoxExchangeService.reserve_next_root` 一次锁定候选挂载与 usage，按稳定顺序只 reserve 一个满足冻结阈值的满箱；
  preparation 同事务写 owner、active E11 root、冻结决策、`WAITING_FULL_BOX_EXCHANGE` 与 Outbox。
- `SmtInboundHandoffService.evaluate` 删除 Handling generic move、独立重试 root 与人工完成旁路，只使用 runtime domain
  authority、`RuntimeIntent.system_capability` 和当前 event loop 已绑定的 WMS preparation runtime。
- Celery scanner 逐 demand commit；只接受空 target 或唯一 `WMS_FULFILLMENT`。commit 后 enqueue 失败不会回滚
  durable Outbox。已锁定的坏 demand 在本轮加入 exclusion 后继续尝试下一条，下一轮仍可重试。
- terminal 最后一箱成功时先从 `WAITING_FULL_BOX_EXCHANGE` 回到 `EVALUATING`，再复用既有摘要归约。

## RED → GREEN

- RED：domain ctx 测试证明旧实现强制伪造 `session`，并且缺 WorkLine 时错误合同不正确（`2 failed`）。
- GREEN：domain ctx 测试 `2 passed`；真实 PostgreSQL scanner happy path 产生恰好一个 RuntimeIntent 与一个
  SystemOutbox，且 parent 为 `WAITING_FULL_BOX_EXCHANGE`；缺 correlation 时 rollback，零 Intent/Outbox。
- terminal 串行 RED 暴露最后一箱成功仍卡在 WAITING；修复后两满箱仅在首个 terminal success 后选择第二箱，最终
  归约到 `READY_FOR_SORTING`。

## 验证

- `tests/workline_runtime/test_smt_inbound_handoff_full_box_contract.py` +
  `tests/workline_runtime/test_smt_recovery_task_transaction.py`：`7 passed`。
- 真实 PostgreSQL：scanner happy/missing-correlation：`2 passed`；two-full-bin terminal serialization：`1 passed`。
- topology guardrail：`6 passed`；Ruff 已通过；`./scripts/git-quality-gate.sh --profile quality` 已通过
  （含 Ruff、Bandit、runtime contract guardrails）。

## GitNexus 与 concern

- 修改前对 evaluate、scanner、reserve/prepare/context 执行 impact；前三者中 reserve/prepare/context 因落后 index
  返回 UNKNOWN，按获授权高风险边界实施。提交前需对 staged diff 再执行 detect。
- 不创建 demand 或 correlation；T6 仍拥有 release fact 与 station/rack-face authority。已知 plugin-attempt fixture
  的 `outbox_dispatch_targets` 失败未修改。
