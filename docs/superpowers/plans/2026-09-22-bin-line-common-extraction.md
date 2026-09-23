# bin-line-common 共享包抽取 Implementation Plan

> **STATUS: NEEDS REBASE AGAINST WES RESPONSIBILITY CONVERGENCE — NOT CURRENT IMPLEMENTATION AUTHORITY**
>
> **DO NOT IMPLEMENT STAGE 1 UNTIL PREREQUISITES ARE REVALIDATED.** 本文保留作历史分析；下方任务清单、代码示例、四项前置假设和 merge SHA 门禁均不得直接执行。当前依据是 [SRS 第 0 章](../../architecture/SRS.md)、[WES 职责收敛账本](../../architecture/wes-responsibility-convergence-ledger.md)、[人工出库 WMS 合同](../../contracts/wms-manual-outbound-picking-integration-requirements.md)和 [Transport 合同](../../contracts/transport-fulfillment-contract.md)。每项前置能力先证明即使取消共享包抽取也仍为 WES 正确运行所必需，再分别闭合能力、合同、架构不变量和聚焦测试；merge SHA 只记录落地位置。完成职责收敛后重新比较两个插件，再决定是否抽取及抽取范围。

**Goal:** 把 `workline_plugins/manual-picking/` 里与业务无关的入线段、货架循环、回程段代码（约 2500 行）抽取为独立
的 `workline_plugins/bin-line-common/` 共享包，让 `automatic-picking` 插件后续可以复用同一套代码，不重复实现。

**Architecture:** 三段切法（入线段 / 货架循环 / 回程段归 `bin-line-common`；工作段归各插件私有），单一接缝是
`bin_line_common.entry.dispatch_scan_event(..., on_scan2=<插件自己的方法>)` 回调函数，不是抽象基类。`ManualPickingPassage`
宽表拆成 `bin_line_passages`（共享，入线+处置结果）、`bin_line_returns`（共享，回程）、`manual_picking_works`
（手工插件私有，PDA 准入与完成）三张表。来源货架不建立业务历史投影或跨 revision 业务围栏：WMS 的
`plan_delta` Evidence 是可靠输入，`PositionProjection` 只回答当前工作位物理事实，物理动作一经下发则继续由既有
`TransportDecisionBinding` 冻结原 `plan_revision/source_evidence_id/rack/face` 直到权威闭合。详见 spec。

**Tech Stack:** Python 3.13、SQLModel、Alembic、pytest、uv workspace（`workline_plugins/*` 通过 `[tool.uv.sources]` 的
`path` 依赖互相引用，不发布到包索引）。

**Spec:** [docs/superpowers/specs/2026-09-22-automatic-picking-plugin-system-design.md](../specs/2026-09-22-automatic-picking-plugin-system-design.md) §4～§6、§9、§12（架构、文件归属、接缝设计、顺序依赖、测试所有权）

## 历史前置假设（待重新裁决，不是当前 Stage 1 启动门禁）

本计划**不能**在以下四项合入 `develop` 之前开始，因为它们直接修改本计划要搬移的同一批文件或其上游合同：

1. [docs/superpowers/specs/2026-09-22-bin-line-scan-retry-fix.md](../specs/2026-09-22-bin-line-scan-retry-fix.md)（回程段扫码重试修正，独立计划，T1/T2）已合入 `develop`。
2. 按当前 SRS 和 Transport 合同验证的基础层可靠恢复改动（`reliable_rack_transport.py`、
   `position_projection_service.py`、`transport/service.py` 等）已合入 `develop`。
3. `plan_revision` 身份合同修正已合入：人工料箱准入/完成 wire 显式携带 revision；Entry/Work 冻结同一 revision，
   禁止用数据库 `id` 或“最近一轮”推断迟到回调归属。
4. 来源货架已改为 Evidence 驱动：删除 `PickingTaskBinSourceRack` 业务投影；调度从连续 `plan_delta` Evidence 找待执行
   occurrence，当前工作位只读 `PositionProjection`，首次物理下发后只以既有 `TransportDecisionBinding` 保存可靠身份。

四项分别合入后，把真实 merge SHA 填入下表；不能使用 commit message grep 或“某文件曾有提交”作为替代证据：

| 前置项 | develop merge SHA | 必须通过的聚焦验证 |
| --- | --- | --- |
| 回程扫码重试 | `<SCAN_RETRY_MERGE_SHA>` | 独立计划列出的 SCAN3/SCAN4 retry 回归 |
| 可靠恢复基础层 | `<RELIABLE_RECOVERY_MERGE_SHA>` | 独立 SPEC 的必选聚焦测试 |
| revision identity wire | `<REVISION_IDENTITY_MERGE_SHA>` | 重复 rack/bin 跨 revision 与迟到 callback 回归 |
| Evidence 驱动来源货架 | `<EVIDENCE_DRIVEN_RACK_MERGE_SHA>` | 窗口补货、取消、完成、到位 owner 与恢复回归 |

开始 Stage 1 前运行：

```bash
git merge-base --is-ancestor <SCAN_RETRY_MERGE_SHA> develop
git merge-base --is-ancestor <RELIABLE_RECOVERY_MERGE_SHA> develop
git merge-base --is-ancestor <REVISION_IDENTITY_MERGE_SHA> develop
git merge-base --is-ancestor <EVIDENCE_DRIVEN_RACK_MERGE_SHA> develop
# 随后运行上表四组聚焦验证；任一 SHA 未填、非祖先或测试失败都阻塞 Stage 1。
```

四条命令和四组聚焦验证都要成功，否则先完成对应前置计划，不要在未冻结的地基上开始抽取。

## Global Constraints

- 系统未发布：不做数据迁移，不保留旧表兼容层；新 Alembic revision 用 `uv run alembic revision -m "<message>"` 生成，
  不手写 revision ID，再编辑生成出来的文件。
- 状态类字段（`disposition`、`return_state`）一律 `VARCHAR + CHECK`，不用 PostgreSQL 原生 ENUM。
- 三张新表 Mixin 统一 `EnterpriseMixin, DataTableMixin`（无 `SoftDeleteMixin`，无乐观锁），与拆分前的
  `ManualPickingPassage` 实际用法一致。
- `bin-line-common` 没有 `plugin_key`，不能被 WorkLine 绑定，不导入任何具体插件；依赖方向永远是插件 → 共享包。
- 中间步骤只运行当前行为 owner 的聚焦测试；每个内聚 Stage 闭合后才运行一次对应插件/共享包全量测试。有效证据只在
  覆盖面被后续变更触及时刷新，禁止在签名传播尚未完成时用整目录失败列表寻找遗漏。
- **设计顶层原则（用户明确重申）：WES 层非必要不增加额外限制，业务判断都在 WMS 层完成，WES 认为 WMS 给的数据、
  API 请求都是正确的，WES 只做调度和执行。** 写任何唯一约束、判重逻辑或状态门禁前，先问"这是 WES 自己需要的
  执行安全（比如防止自己重复提交同一条物理命令），还是在替 WMS 做业务判断（比如假设一个业务身份只会出现一
  次）"——前者保留，后者删除或收窄到 WES 真正需要的最小范围。Task 1 的 `(task_id, bin_code)` 单终态约束就是
  一次后者的例子，见下方 Review Focus 第 7 条。

## Review Focus

1. **AGV 到位顺序不可控**（用户现实约束 A）：`batch_driver.py`/`rack_readiness.py` 判定货架是否可以推进，必须只
   看 `PositionProjection` 和 Transport 权威结果，不能假设"先下发 CTU01 的货架先到位"。拆分过程中如果有代码路径
   隐式依赖了下发顺序（比如按创建时间排序后直接假定这就是物理到位顺序），必须在这次拆分中显式测试出来。
2. **SCAN1→SCAN3 NG 直达路径跨段一致性**（用户现实约束 B）：料箱只有两种物理路径——
   `SCAN1→SCAN2(工作位)→SCAN3→SCAN4` 或 `SCAN1→SCAN3(异常跳出)→SCAN4/NGZone`。拆分后 SCAN1 归入线段
   （`bin_line_passages`），SCAN3 归回程段（`bin_line_returns`）；一个在 SCAN1 就被判 NG、从未经过 SCAN2 工作段
   的料箱，到 SCAN3 时必须能正确关联到它在入线段的 `disposition=NG` 记录并走 NGZone，不能因为拆表而找不到。
3. **`return_batch` 必须按 SCAN4 到位顺序下发**（用户现实约束 C）：`bin_line_returns` 的 FIFO 查询排序
   （`scan4_received_at, scan4_evidence_id`）必须在拆表后原样保留，不能被新表结构悄悄改变排序键。
4. **跨任务并发的入线/回程记录不能互相污染**：`bin_line_passages`/`bin_line_returns` 是 WorkLine 级共享表，不同
   `task_id` 的记录混在同一张表里；拆分后的查询方法如果漏加 `workline_id` 过滤，会在多任务并发时把别的任务的料箱
   算进当前任务的 FIFO 判断。
5. **`retry_count` 字段要在这次拆分中就建好**：它是[回程重试修正](../specs/2026-09-22-bin-line-scan-retry-fix.md)
   需要的字段，如果这次拆分漏掉，等重试修正落地时要再开一次 migration，徒增一次 schema 变更。
6. **`return_batch` 只有两条互斥的目标货架来源**（用户补充的现实约束）：借用当前面刚 `feed_complete` 但还没换面/
   离场的货架（`_advance_return_batch_before_rack_action`，[batch_driver.py:619](../../../workline_plugins/manual-picking/src/manual_picking/application/batch_driver.py:619)），
   或者在没有活动任务占着当前面时向 WMS 单独要一台专门的退箱货架（`_advance_drain`/`_fill_drain_window`）。两者
   互斥，因为 CTU 通道单一串行。拆分/搬迁不能把两条路径的判定条件搅到一起，也不能让其中一条在搬迁后失去测试覆盖。
7. **同一货架、同一料箱、同一 `task_id`，可以在不同 `plan_delta` revision 里重复出现**（用户确认的现实场景）：
   `plan_delta` 是增量下发的，同一货架、同一料箱可能在后续 revision 里被再次选中，走完整的
   SCAN1→SCAN2→WMS 准入→WMS 完成全流程。原有的 `(task_id, bin_code) WHERE wms_result IS NOT NULL` 单终态约束
   和 `uniquely_completed_for_update` 判重逻辑都假设"一个 (task_id, bin_code) 组合整个任务只终结一次"，这个假设
   是错的。此问题必须在前置 revision identity 计划中通过显式 `plan_revision` 修正；本计划不得用 `id DESC`、时间顺序
   或单终态业务围栏替代权威 revision。
8. **到位货架只按当前物理事实推进，不读取来源货架业务历史投影**：当前工作位货架的任务 owner 来自把它送到位的
   `TransportDecisionBinding.picking_task_id/source_evidence_id`，到位由 `PositionProjection` 证明。有任务 owner 时，
   `inbound_batch` 与 `return_batch` 公平交替；优先方向明确无候选时，同一 tick 可尝试另一方向。没有任务 owner 时只
   尝试 `return_batch`。交替起点由本次到位 Binding 之后最近封闭的 Batch Evidence 推导，不新增轮转状态表。

```text
plan_delta InboundEvidence（可靠输入，不另建来源货架投影）
              │
              ├── 尚无物理 Binding ── 工作位容量允许 ── 下发原 occurrence
              │
              └── 已有 TransportDecisionBinding ── 保留原身份直到权威闭合
                                                   │
PositionProjection（当前点位事实）─────────────────┘
              │
              ├── 有 task owner：inbound_batch ⇄ return_batch（无候选可回退）
              └── 无 task owner：仅尝试 return_batch
```

---

## Stage 1：在 `manual-picking` 内闭合共享边界（模型 + 分发接缝，零业务语义变化）

本 Stage 包含原 Task 1/2。revision identity、回程 retry 和 Evidence 驱动货架推进均已在前置计划独立改变行为；本 Stage
只按已冻结合同拆表和拆模块，不再夹带“最近一轮”等新业务逻辑。

### Task 1.1：拆分 Passage 数据模型

**Files:**
- Create: `workline_plugins/manual-picking/src/manual_picking/application/entry_model.py`（`bin_line_passages` 形状，暂时仍在 manual-picking 包内）
- Create: `workline_plugins/manual-picking/src/manual_picking/application/return_model.py`（`bin_line_returns` 形状）
- Modify: `workline_plugins/manual-picking/src/manual_picking/application/passage_model.py`（改为只剩 `manual_picking_works` 形状，类名改 `ManualPickingWork`）
- Create: `workline_plugins/manual-picking/src/manual_picking/application/entry_repository.py`（对应 `entry_model.py` 的查询）
- Create: `workline_plugins/manual-picking/src/manual_picking/application/return_repository.py`（对应 `return_model.py` 的查询）
- Modify: `workline_plugins/manual-picking/src/manual_picking/application/passage_repository.py`（改为只剩 `manual_picking_works` 的三个方法：`by_admission_operation_for_update`、`waiting_for_completion_for_update`、`completed_for_revision_for_update`，类名改 `WorkRepository`）
- Modify: `workline_plugins/manual-picking/src/manual_picking/application/scan_flow.py`（改用三个新仓储）
- Modify: `workline_plugins/manual-picking/src/manual_picking/application/batch_driver.py`（`_passages.has_bin_before_return_buffer`/`unfinished_return_prefix_for_update`/`ready_return_prefix_for_update` 改用 `return_repository`）
- Modify: `workline_plugins/manual-picking/src/manual_picking/application/completion_repository.py`（`passage.wms_result.is_(None)` 改为 `entry_passage.disposition == 'OPEN'`）
- Create: `migrations/versions/<generated>_冻结_bin_line_passage_split.py`（drop `manual_picking_passages`，create 三张新表）
- Test: `workline_plugins/manual-picking/tests/test_passage_model.py`（改为覆盖三张表）
- Test: `workline_plugins/manual-picking/tests/test_scan_flow.py`（保持现有全部断言，仅底层仓储调用方式变化）
- Test: `workline_plugins/manual-picking/tests/test_completion_repository.py`（锁住 `disposition == 'OPEN'` 查询语义）
- Test: `tests/deployment/test_plugin_models.py`（共享表与人工私有表按静态消费者集合注册）

**Interfaces:**
- Consumes: 无（本任务是本计划的起点）
- Produces:
  - `EntryPassage`（原 `ManualPickingPassage` 的入线+处置字段，`bin_line_passages` 形状）
  - `ReturnLeg`（`bin_line_returns` 形状，`passage_id` 外键 `EntryPassage.id`）
  - `ManualPickingWork`（原 `manual_picking_works` 形状）
  - `EntryRepository`：`add`、`scan2_head_for_update`、`scan2_in_flight_for_update`、`scan1_unclosed_for_update`、
    `by_command_code_for_update`、`unique_open_bin_for_update`、`archive_open_work`、`get_unfinished_workload_summary`
  - `ReturnRepository`：`has_bin_before_return_buffer`、`ready_return_prefix_for_update`、`unfinished_return_prefix_for_update`
  - `WorkRepository`：`by_admission_operation_for_update`、`waiting_for_completion_for_update`、`completed_for_revision_for_update`

- [ ] **Step 1: 生成新 Alembic revision（不手写 ID）**

```bash
cd /Users/kaizhou/codeDev/wes_backend
uv run alembic revision -m "拆分手工拣料 passage 为入线/回程/工作三张表"
```

记下生成的文件路径（形如 `migrations/versions/<hash>_拆分手工拣料_passage_为入线_回程_工作三张表.py`），下一步编辑它。

- [ ] **Step 2: 编辑生成的 migration，drop 旧表、create 三张新表**

```python
"""拆分手工拣料 passage 为入线/回程/工作三张表"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "<保留生成器给的值>"
down_revision = "<保留生成器给的值>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("manual_picking_passages", schema="wes_biz")

    op.create_table(
        "bin_line_passages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("workline_id", sa.BigInteger(), sa.ForeignKey("wes_biz.work_lines.id"), nullable=False),
        sa.Column("task_id", sa.String(length=100), nullable=False),
        sa.Column("plan_revision", sa.BigInteger(), nullable=False),
        sa.Column("plan_source_evidence_id", sa.BigInteger(), sa.ForeignKey("wes_biz.inbound_evidences.id"), nullable=False),
        sa.Column("bin_code", sa.String(length=100), nullable=True),
        sa.Column("raw_scan1_code", sa.String(length=160), nullable=True),
        sa.Column("scan1_evidence_id", sa.BigInteger(), sa.ForeignKey("wes_biz.inbound_evidences.id"), nullable=False),
        sa.Column("scan1_received_at", sa.DateTime(), nullable=False),
        sa.Column("scan1_command_code", sa.String(length=160), nullable=True),
        sa.Column("scan2_evidence_id", sa.BigInteger(), sa.ForeignKey("wes_biz.inbound_evidences.id"), nullable=True),
        sa.Column("scan2_fault_evidence_id", sa.BigInteger(), sa.ForeignKey("wes_biz.inbound_evidences.id"), nullable=True),
        sa.Column("scan2_command_code", sa.String(length=160), nullable=True),
        sa.Column("scan2_fault_command_code", sa.String(length=160), nullable=True),
        sa.Column("disposition", sa.String(length=10), nullable=False, server_default="OPEN"),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("disposition IN ('OPEN', 'NORMAL', 'NG', 'CLOSED')", name="bin_line_passages_disposition_valid"),
        sa.CheckConstraint("plan_revision >= 1", name="bin_line_passages_plan_revision_positive"),
        sa.CheckConstraint("archived_at IS NULL OR disposition = 'CLOSED'", name="bin_line_passages_archive_closed"),
        sa.UniqueConstraint("scan1_evidence_id", name="ux_bin_line_passages_scan1_evidence"),
        sa.UniqueConstraint("scan2_evidence_id", name="ux_bin_line_passages_scan2_evidence"),
        sa.UniqueConstraint("scan2_fault_evidence_id", name="ux_bin_line_passages_scan2_fault_evidence"),
        schema="wes_biz",
    )
    op.create_index(
        "ix_bin_line_passages_scan2_fifo",
        "bin_line_passages",
        ["workline_id", "scan1_received_at", "scan1_evidence_id"],
        unique=False,
        schema="wes_biz",
        postgresql_where=sa.text("scan2_evidence_id IS NULL AND disposition <> 'CLOSED'"),
    )

    op.create_table(
        "bin_line_returns",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("passage_id", sa.BigInteger(), sa.ForeignKey("wes_biz.bin_line_passages.id"), nullable=False),
        sa.Column("workline_id", sa.BigInteger(), sa.ForeignKey("wes_biz.work_lines.id"), nullable=False),
        sa.Column("bin_code", sa.String(length=100), nullable=True),
        sa.Column("scan3_evidence_id", sa.BigInteger(), sa.ForeignKey("wes_biz.inbound_evidences.id"), nullable=True),
        sa.Column("scan3_command_code", sa.String(length=160), nullable=True),
        sa.Column("scan3_route", sa.String(length=20), nullable=True),
        sa.Column("scan4_evidence_id", sa.BigInteger(), sa.ForeignKey("wes_biz.inbound_evidences.id"), nullable=True),
        sa.Column("scan4_received_at", sa.DateTime(), nullable=True),
        sa.Column("scan4_command_code", sa.String(length=160), nullable=True),
        sa.Column("return_state", sa.String(length=20), nullable=False, server_default="NONE"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "return_state IN ('NONE', 'MOVE_PENDING', 'READY', 'RETURN_REQUESTED', 'RETURNED')",
            name="bin_line_returns_state_valid",
        ),
        sa.CheckConstraint(
            "(scan4_evidence_id IS NULL) = (scan4_received_at IS NULL)",
            name="bin_line_returns_scan4_order_complete",
        ),
        sa.UniqueConstraint("passage_id", name="ux_bin_line_returns_passage"),
        sa.UniqueConstraint("scan3_evidence_id", name="ux_bin_line_returns_scan3_evidence"),
        sa.UniqueConstraint("scan4_evidence_id", name="ux_bin_line_returns_scan4_evidence"),
        schema="wes_biz",
    )
    op.create_index(
        "ix_bin_line_returns_fifo",
        "bin_line_returns",
        ["workline_id", "scan4_received_at", "scan4_evidence_id"],
        unique=False,
        schema="wes_biz",
        postgresql_where=sa.text("scan4_evidence_id IS NOT NULL AND return_state <> 'RETURNED'"),
    )

    op.create_table(
        "manual_picking_works",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("passage_id", sa.BigInteger(), sa.ForeignKey("wes_biz.bin_line_passages.id"), nullable=False),
        sa.Column("task_id", sa.String(length=100), nullable=False),
        sa.Column("plan_revision", sa.BigInteger(), nullable=False),
        sa.Column("bin_code", sa.String(length=100), nullable=False),
        sa.Column("admission_operation_id", sa.String(length=160), nullable=True),
        sa.Column("admission_result", sa.String(length=20), nullable=True),
        sa.Column("admission_scanned_at", sa.BigInteger(), nullable=True),
        sa.Column("wms_result", sa.String(length=10), nullable=True),
        sa.Column("wms_completed_at", sa.DateTime(), nullable=True),
        sa.Column("wms_completed_evidence_id", sa.BigInteger(), sa.ForeignKey("wes_biz.inbound_evidences.id"), nullable=True),
        sa.CheckConstraint("wms_result IS NULL OR wms_result IN ('NORMAL', 'NG')", name="manual_picking_works_wms_result_valid"),
        sa.CheckConstraint("plan_revision >= 1", name="manual_picking_works_plan_revision_positive"),
        sa.UniqueConstraint("passage_id", name="ux_manual_picking_works_passage"),
        sa.UniqueConstraint("admission_operation_id", name="ux_manual_picking_works_admission_operation"),
        sa.UniqueConstraint("wms_completed_evidence_id", name="ux_manual_picking_works_wms_completed_evidence"),
        schema="wes_biz",
    )
    op.create_index(
        "ix_manual_picking_works_waiting",
        "manual_picking_works",
        ["task_id", "plan_revision", "bin_code"],
        unique=False,
        schema="wes_biz",
        postgresql_where=sa.text("wms_result IS NULL"),
    )
    op.create_index(
        "ix_manual_picking_works_completed",
        "manual_picking_works",
        ["task_id", "plan_revision", "bin_code"],
        unique=False,
        schema="wes_biz",
        postgresql_where=sa.text("wms_completed_evidence_id IS NOT NULL"),
    )
    # 不建跨 revision 的 (task_id, bin_code) 业务唯一索引。回调按显式
    # (task_id, plan_revision, bin_code) 匹配，不按 id 或时间顺序猜测。


def downgrade() -> None:
    op.drop_index("ix_manual_picking_works_completed", table_name="manual_picking_works", schema="wes_biz")
    op.drop_index("ix_manual_picking_works_waiting", table_name="manual_picking_works", schema="wes_biz")
    op.drop_table("manual_picking_works", schema="wes_biz")
    op.drop_table("bin_line_returns", schema="wes_biz")
    op.drop_table("bin_line_passages", schema="wes_biz")
    # 按直接父 revision 完整重建 manual_picking_passages：实施时在这里精确展开父版本的全部列、
    # CHECK、UNIQUE、FK 与 FIFO 索引，包括前置 identity 修正加入的 revision 字段。
    # 系统未发布，因此不回填开发数据；但 downgrade 后 schema 必须与父 revision 一致。
    op.create_table("manual_picking_passages", ..., schema="wes_biz")
    # 完整恢复父版本的全部索引，禁止只恢复表名。
```

- [ ] **Step 3: 在干净测试库跑一次 migration，验证能正常 upgrade**

```bash
uv run alembic upgrade head
uv run alembic downgrade <DIRECT_PARENT_REVISION>
uv run alembic upgrade head
uv run alembic current
```

Expected: 在独占干净 PostgreSQL 逻辑库完成 parent → head → parent → head；每一步 schema 与对应模型一致，最终
`alembic current` 显示新 revision。不得使用共享 dev 数据库。

- [ ] **Step 4: 创建 `entry_model.py`**

```python
"""入线段（bin-line-common）料箱经过：SCAN1 到位与处置结果。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import ClassVar

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class EntryPassage(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "bin_line_passages"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("scan1_evidence_id", name="ux_bin_line_passages_scan1_evidence"),
        UniqueConstraint("scan2_evidence_id", name="ux_bin_line_passages_scan2_evidence"),
        UniqueConstraint("scan2_fault_evidence_id", name="ux_bin_line_passages_scan2_fault_evidence"),
        Index(
            "ix_bin_line_passages_scan2_fifo",
            "workline_id",
            "scan1_received_at",
            "scan1_evidence_id",
            postgresql_where=text("scan2_evidence_id IS NULL AND disposition <> 'CLOSED'"),
            sqlite_where=text("scan2_evidence_id IS NULL AND disposition <> 'CLOSED'"),
        ),
        CheckConstraint("disposition IN ('OPEN', 'NORMAL', 'NG', 'CLOSED')", name="bin_line_passages_disposition_valid"),
        CheckConstraint("plan_revision >= 1", name="bin_line_passages_plan_revision_positive"),
        CheckConstraint("archived_at IS NULL OR disposition = 'CLOSED'", name="bin_line_passages_archive_closed"),
        {"schema": SchemaType.BIZ.value},
    )

    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    task_id: str = Field(min_length=1, max_length=100)
    plan_revision: int = Field(ge=1, sa_type=SQL_COMPAT_BIGINT)
    plan_source_evidence_id: int = Field(
        foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    bin_code: str | None = Field(default=None, max_length=100)
    raw_scan1_code: str | None = Field(default=None, max_length=160)
    scan1_evidence_id: int = Field(foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT)
    scan1_received_at: datetime
    scan1_command_code: str | None = Field(default=None, max_length=160)
    scan2_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    scan2_fault_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    scan2_command_code: str | None = Field(default=None, max_length=160)
    scan2_fault_command_code: str | None = Field(default=None, max_length=160)
    disposition: str = Field(default="OPEN", max_length=10)
    reason_code: str | None = Field(default=None, max_length=64, description="处置原因码，如 MANUAL_PICK_NG")
    archived_at: datetime | None = Field(default=None, description="运维清线归档时间；不代表业务或设备完成")


__all__ = ["EntryPassage"]
```

- [ ] **Step 5: 创建 `return_model.py`**

```python
"""回程段（bin-line-common）：SCAN3/SCAN4 与 RETURN_BUFFER 状态。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import ClassVar

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class ReturnLeg(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "bin_line_returns"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("passage_id", name="ux_bin_line_returns_passage"),
        UniqueConstraint("scan3_evidence_id", name="ux_bin_line_returns_scan3_evidence"),
        UniqueConstraint("scan4_evidence_id", name="ux_bin_line_returns_scan4_evidence"),
        Index(
            "ix_bin_line_returns_fifo",
            "workline_id",
            "scan4_received_at",
            "scan4_evidence_id",
            postgresql_where=text("scan4_evidence_id IS NOT NULL AND return_state <> 'RETURNED'"),
            sqlite_where=text("scan4_evidence_id IS NOT NULL AND return_state <> 'RETURNED'"),
        ),
        CheckConstraint(
            "return_state IN ('NONE', 'MOVE_PENDING', 'READY', 'RETURN_REQUESTED', 'RETURNED')",
            name="bin_line_returns_state_valid",
        ),
        CheckConstraint(
            "(scan4_evidence_id IS NULL) = (scan4_received_at IS NULL)",
            name="bin_line_returns_scan4_order_complete",
        ),
        {"schema": SchemaType.BIZ.value},
    )

    passage_id: int = Field(foreign_key="wes_biz.bin_line_passages.id", sa_type=SQL_COMPAT_BIGINT)
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", sa_type=SQL_COMPAT_BIGINT)
    bin_code: str | None = Field(default=None, max_length=100)
    scan3_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    scan3_command_code: str | None = Field(default=None, max_length=160)
    scan3_route: str | None = Field(default=None, max_length=20)
    scan4_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )
    scan4_received_at: datetime | None = Field(default=None)
    scan4_command_code: str | None = Field(default=None, max_length=160)
    return_state: str = Field(default="NONE", max_length=20)
    retry_count: int = Field(default=0, description="诊断用：本条回程记录被新物理扫码事件覆盖的次数，不参与业务判断")


__all__ = ["ReturnLeg"]
```

- [ ] **Step 6: 把 `passage_model.py` 改为只剩 `ManualPickingWork`**

编辑 `workline_plugins/manual-picking/src/manual_picking/application/passage_model.py`，删除原
`ManualPickingPassage` 里的 `raw_scan1_code`、`scan1_*`、`scan2_*`、`scan3_*`、`scan4_*`、`disposition`、
`return_state`、`archived_at` 字段和对应约束/索引（这些已经搬到 `EntryPassage`/`ReturnLeg`），只保留：

```python
"""手工拣料私有表：PDA 准入与完成事实。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import ClassVar

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class ManualPickingWork(EnterpriseMixin, DataTableMixin, table=True):
    __tablename__: ClassVar[str] = "manual_picking_works"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint("passage_id", name="ux_manual_picking_works_passage"),
        UniqueConstraint("admission_operation_id", name="ux_manual_picking_works_admission_operation"),
        UniqueConstraint("wms_completed_evidence_id", name="ux_manual_picking_works_wms_completed_evidence"),
        CheckConstraint("wms_result IS NULL OR wms_result IN ('NORMAL', 'NG')", name="manual_picking_works_wms_result_valid"),
        CheckConstraint("plan_revision >= 1", name="manual_picking_works_plan_revision_positive"),
        Index(
            "ix_manual_picking_works_waiting",
            "task_id",
            "plan_revision",
            "bin_code",
            postgresql_where=text("wms_result IS NULL"),
            sqlite_where=text("wms_result IS NULL"),
        ),
        Index(
            "ix_manual_picking_works_completed",
            "task_id",
            "plan_revision",
            "bin_code",
            postgresql_where=text("wms_completed_evidence_id IS NOT NULL"),
            sqlite_where=text("wms_completed_evidence_id IS NOT NULL"),
        ),
        {"schema": SchemaType.BIZ.value},
    )
    # 不建跨 revision 的 (task_id, bin_code) 业务唯一索引，理由同 Step 2 migration 注释。

    passage_id: int = Field(foreign_key="wes_biz.bin_line_passages.id", sa_type=SQL_COMPAT_BIGINT)
    task_id: str = Field(min_length=1, max_length=100)
    plan_revision: int = Field(ge=1, sa_type=SQL_COMPAT_BIGINT)
    bin_code: str = Field(min_length=1, max_length=100)
    admission_operation_id: str | None = Field(default=None, max_length=160)
    admission_result: str | None = Field(default=None, max_length=20)
    admission_scanned_at: int | None = Field(default=None, sa_type=SQL_COMPAT_BIGINT)
    wms_result: str | None = Field(default=None, max_length=10)
    wms_completed_at: datetime | None = Field(default=None)
    wms_completed_evidence_id: int | None = Field(
        default=None, foreign_key="wes_biz.inbound_evidences.id", sa_type=SQL_COMPAT_BIGINT
    )


__all__ = ["ManualPickingWork"]
```

- [ ] **Step 7: 创建 `entry_repository.py`**

把原 `passage_repository.py`（`PassageRepository`）里只读写 `EntryPassage` 字段的方法原样搬过来，改成对
`EntryPassage` 建模：`add`、`scan2_head_for_update`、`scan2_in_flight_for_update`、`scan1_unclosed_for_update`、
`by_command_code_for_update`（只比较 `scan1_command_code`/`scan2_command_code`/`scan2_fault_command_code`，
不再包含 `scan3_command_code`/`scan4_command_code`）、`unique_open_bin_for_update`（在同一 SQL 中用关联
`EXISTS`/`NOT EXISTS` 判断 `ReturnLeg` 是否存在：`after_scan2=True` 要求存在，`False` 要求不存在，`None` 不加条件）、`archive_open_work`、
`get_unfinished_workload_summary`。方法签名和返回类型与原 `PassageRepository` 保持一致，只是操作的模型换了。

- [ ] **Step 8: 创建 `return_repository.py`**

把 `has_bin_before_return_buffer`、`ready_return_prefix_for_update`、`unfinished_return_prefix_for_update` 三个方法
搬过来，改为对 `ReturnLeg` 建模，`workline_id` 直接是 `ReturnLeg` 自己的字段（不再需要 join `EntryPassage`）。新增
一个 `get_or_create_for_passage(db, passage_id, workline_id, bin_code)` 方法：SCAN3 第一次关联到某次经过时调用，
如果该 `passage_id` 已有 `ReturnLeg` 行则返回它（幂等），否则创建新行——这是原来隐式发生在 `_apply_scan3` 里的
"首次创建" 逻辑，拆分后需要显式成一个方法，Task 1.2 会调用它。

- [ ] **Step 9: 改 `passage_repository.py` 为 `WorkRepository`**

只保留 `by_admission_operation_for_update`、`waiting_for_completion_for_update`、`completed_for_revision_for_update`
三个方法，改为对 `ManualPickingWork` 建模并 join `EntryPassage`（这三个方法原来读 `disposition`/`bin_code` 等
入线段字段，现在要跨表读）。类名改为 `WorkRepository`。

前置 identity 计划已经把 `plan_revision` 加入人工准入/完成 wire；这里按显式 revision 匹配，不再保留原方法的
“唯一终态”假设，也禁止用最新 `id` 推断轮次：

```python
async def completed_for_revision_for_update(
    self, db: AsyncSession, *, workline_id: int, task_id: str, plan_revision: int, bin_code: str
) -> ManualPickingWork | None:
    """按 WMS 明确给出的 revision identity 查找该轮已完成记录。"""
    statement = (
        select(ManualPickingWork)
        .join(EntryPassage, ManualPickingWork.passage_id == EntryPassage.id)
        .where(
            EntryPassage.workline_id == workline_id,
            ManualPickingWork.task_id == task_id,
            ManualPickingWork.plan_revision == plan_revision,
            ManualPickingWork.bin_code == bin_code,
            ManualPickingWork.wms_completed_evidence_id.is_not(None),
        )
        .with_for_update()
    )
    return (await db.execute(statement)).scalar_one_or_none()
```

调用方 `_apply_completed` 使用回调中的 `plan_revision`；只有同一显式 identity 且结果相同才算幂等
`DUPLICATE_COMPLETION`。找不到该 revision 或内容漂移时按前置合同进入冲突/对账，不回退到最近记录。

- [ ] **Step 10: 改 `scan_flow.py` 和 `batch_driver.py` 的仓储调用**

把 `scan_flow.py` 里所有 `self._passages.xxx()` 调用按方法名分流到 `self._entry.xxx()` / `self._returns.xxx()` /
`self._works.xxx()` 三个新仓储实例；`batch_driver.py` 里 `self._passages.has_bin_before_return_buffer(...)`、
`unfinished_return_prefix_for_update(...)`、`ready_return_prefix_for_update(...)` 改成 `self._returns.xxx(...)`。

- [ ] **Step 11: 改 `completion_repository.py`（Review Focus 项，见 spec §6.2）**

把 `ready_to_confirm` 里的原生 SQL：

```python
passage = cast("Any", ManualPickingPassage).__table__.c
...
passage.disposition != "CLOSED",
passage.wms_result.is_(None),
```

改为：

```python
entry = cast("Any", EntryPassage).__table__.c
...
entry.disposition == "OPEN",
```

（`disposition != 'CLOSED' AND wms_result IS NULL` 等价于 `disposition == 'OPEN'`：`NORMAL`/`NG` 在工作段结算时
已经从 `OPEN` 变化，见 spec 的 disposition 状态机图。）

- [ ] **Step 12: 运行全量测试**

```bash
cd /Users/kaizhou/codeDev/wes_backend
uv run pytest workline_plugins/manual-picking/tests/ -v
```

Expected: 全绿。如果 `test_passage_model.py` 因为类名/字段变化而失败，按 Step 13 更新它，其余测试文件的**断言内容
不应该变**（只是内部调用的仓储对象变了，外部行为不变）。

- [ ] **Step 13: 更新 `test_passage_model.py` 覆盖三张新表**

把原本对 `ManualPickingPassage` 的建表/约束测试拆成三份，分别验证 `EntryPassage`、`ReturnLeg`、`ManualPickingWork`
各自的唯一约束和 CHECK 约束（例如 `ReturnLeg.retry_count` 默认值为 0、`bin_line_returns_scan4_order_complete`
约束在 `scan4_evidence_id` 和 `scan4_received_at` 不一致时拒绝插入）。

- [ ] **Step 14: Review Focus 回归测试 —— 约束 B（SCAN1→SCAN3 NG 直达）**

在 `test_scan_flow.py` 新增：

```python
@pytest.mark.asyncio
async def test_scan1_ng_bin_reaches_scan3_without_ever_touching_scan2() -> None:
    """约束 B：SCAN1 判 NG 的料箱直达 SCAN3，从未创建 ReturnLeg 以外的工作段记录。"""
    flow, evidences, entry, returns, works, commands, _ = _setup()
    evidences.rows[1] = _scan(1, "S1", "A000000001-C")  # 非法后缀，SCAN1 判 NG
    evidences.rows[3] = _scan(3, "S3", "A000000001-B")
    evidences.rows[4] = _result(4, "COMMAND-2", device_code="S3")

    await flow.apply_in_session(object(), 1, workline_id=7)
    commands.statuses["COMMAND-1"] = "SUCCEEDED"
    await flow.apply_in_session(object(), 3, workline_id=7)
    await flow.apply_in_session(object(), 4, workline_id=7)

    assert entry.rows[0].disposition == "CLOSED"
    assert returns.rows[0].passage_id == entry.rows[0].id
    assert len(works.rows) == 0  # 从未进入工作段，manual_picking_works 里没有这条料箱的记录
```

Run: `uv run pytest workline_plugins/manual-picking/tests/test_scan_flow.py -k scan1_ng_bin_reaches_scan3 -v`
Expected: FAIL（fixture `_setup`/`_scan`/`_result` 的返回值签名会因为仓储拆分需要更新，先跑一次看清楚报错，
再按 Step 10 的新仓储签名调整 `_setup` helper，不是改测试期望）。

- [ ] **Step 15: 确认 Step 14 测试通过**

```bash
uv run pytest workline_plugins/manual-picking/tests/test_scan_flow.py -k scan1_ng_bin_reaches_scan3 -v
```

Expected: PASS。

- [ ] **Step 16: Review Focus 回归测试 —— 跨 WorkLine 隔离 + 同线多任务共存**

在 `test_scan_flow.py` 新增：

```python
@pytest.mark.asyncio
async def test_scan1_fifo_only_reads_requested_workline() -> None:
    """WorkLine 7 的 FIFO 查询不能读取 WorkLine 8 的开放 Passage。"""
    flow, evidences, entry, returns, works, commands, _ = _setup()
    entry.rows.extend([_entry(workline_id=8, bin_code="OTHER"), _entry(workline_id=7, bin_code="OWN")])
    assert [row.bin_code for row in await entry.scan1_unclosed_for_update(object(), 7)] == ["OWN"]


async def test_same_workline_multiple_tasks_keep_independent_passages() -> None:
    """同线不同 task 的 Passage 可共存，但 FIFO 仍以 WorkLine 为作用域。"""
    # 沿用原计划的双 task 场景，断言两条记录互不覆盖且都属于同一 WorkLine。
    ...
```

对 `EntryRepository` 与 `ReturnRepository` 的所有 FIFO/prefix 查询各至少覆盖一次 WorkLine 7/8 混合数据。
Run: `uv run pytest workline_plugins/manual-picking/tests/test_scan_flow.py -k "requested_workline or multiple_tasks" -v`

- [ ] **Step 17: Review Focus 回归测试 —— 同一料箱同一 task_id 被多轮 plan_delta 完整下发（约束7）**

在 `test_scan_flow.py` 新增两个测试，分别锁住 Step 2/Step 9 的两处修正：

```python
@pytest.mark.asyncio
async def test_same_bin_same_task_completes_two_independent_rounds() -> None:
    """同一 (task_id, bin_code) 走完两轮全程，第二轮不因第一轮已终态而被数据库约束拒绝。"""
    flow, evidences, entry, returns, works, commands, admissions = _setup()
    # 第一轮：A000000001 走 SCAN1→SCAN2→NO_WORK，正常关闭。
    evidences.rows[1] = _scan(1, "S1", "A000000001-B")
    evidences.rows[2] = _scan(2, "S2", "A000000001-C")
    await flow.apply_in_session(object(), 1, workline_id=7)
    commands.statuses["COMMAND-1"] = "SUCCEEDED"
    await flow.apply_in_session(object(), 2, workline_id=7)
    evidences.rows[3] = _wms(
        3, InboundEvidenceKind.WMS_RESULT, admissions.intents[0].operation_id, {"result": "NO_WORK"}
    )
    await flow.apply_in_session(object(), 3, workline_id=7)
    commands.statuses[entry.rows[0].scan2_command_code] = "SUCCEEDED"
    # ...（补 SCAN3/SCAN4 让第一轮的 EntryPassage.disposition 走到 CLOSED，用现有 helper 推进）

    # 第二轮：同一 task_id、同一 bin_code 的第二次 plan_delta 下发，重新从 SCAN1 进线。
    evidences.rows[10] = _scan(10, "S1", "A000000001-B")
    evidences.rows[11] = _scan(11, "S2", "A000000001-C")
    result = await flow.apply_in_session(object(), 10, workline_id=7)

    assert result.disposition is BusinessEvidenceDisposition.APPLIED
    assert len(entry.rows) == 2  # 两条独立的 EntryPassage，第二条不被第一条的历史记录挡住
    assert entry.rows[0].task_id == entry.rows[1].task_id == "PICK-001"


@pytest.mark.asyncio
async def test_late_duplicate_completion_matches_explicit_revision() -> None:
    """迟到 work_completed 必须按其 plan_revision 命中原轮次，不能按最近记录猜测。"""
    works = _WorkRepositoryFake()
    # 构造两条历史 ManualPickingWork：round 1（passage_id=1, wms_result=NG），round 2（passage_id=2, wms_result=NORMAL）。
    works.rows = [
        _work_row(passage_id=1, task_id="PICK-001", plan_revision=1, bin_code="A000000001", wms_result="NG"),
        _work_row(passage_id=2, task_id="PICK-001", plan_revision=2, bin_code="A000000001", wms_result="NORMAL"),
    ]

    found = await works.completed_for_revision_for_update(
        db, workline_id=7, task_id="PICK-001", plan_revision=1, bin_code="A000000001"
    )

    assert found.passage_id == 1
    assert found.wms_result == "NG"
```

Run: `uv run pytest workline_plugins/manual-picking/tests/test_scan_flow.py -k "same_bin_same_task_completes_two_independent_rounds or late_duplicate_completion_matches_explicit_revision" -v`
Expected: 两轮均可完成；迟到 revision 1 回调仍命中 revision 1，不会误命中 revision 2。

- [ ] **Step 18: Stage 1.1 全量测试 + 可提交检查点**

```bash
uv run pytest workline_plugins/manual-picking/tests/ -v
uv run ruff format workline_plugins/manual-picking/ migrations/
uv run ruff check workline_plugins/manual-picking/ migrations/
```

当前用户目标包含 Commit、Ship 或创建 PR 时，才暂存本 Stage 文件并使用建议消息
`refactor(manual-picking): 拆分 passage 为入线/回程/工作三张表`；仅要求实施时在验证通过后继续 Stage 1.2，
不暂存、不提交。

---

### Task 1.2：拆分 `scan_flow.py` 为入线+回程分发器与工作段私有方法

**Files:**
- Create: `workline_plugins/manual-picking/src/manual_picking/application/entry_dispatch.py`（`dispatch_scan_event` 雏形，函数签名与 spec §6.3 一致）
- Modify: `workline_plugins/manual-picking/src/manual_picking/application/scan_flow.py`（瘦身为壳：路由 + `_apply_admission_result`/`_apply_completed`/SCAN2 分支）
- Test: `workline_plugins/manual-picking/tests/test_scan_flow.py`

**Interfaces:**
- Consumes: Task 1 的 `EntryRepository`、`ReturnRepository`、`WorkRepository`
- Produces: `dispatch_scan_event(db, evidence, workline, bindings, *, entry, returns, on_scan2, plugin_key) -> str | None`
  —— `on_scan2` 签名固定为 `async def on_scan2(db, evidence, workline_id, bindings, raw_code, scanned_at, passage) -> str | None`，
  返回值语义与原 `_apply_scan2` 一致（路由字符串或 `None`）。`bin-line-common` 迁移后这个函数原样搬迁，签名不变。

- [ ] **Step 1: 写 `entry_dispatch.py`，把 `_apply_device_event` 的路由逻辑和 SCAN1/3/4 处理函数原样搬进来**

把原 `scan_flow.py` 里的 `_apply_scan1`、`_apply_scan3`、`_apply_scan4`、`_apply_device_result`、
`_apply_batch_result`（及其两个子方法）、`_apply_transport_result`、`_apply_batch_transport_result`、
`_device_has_unclosed`、`_move`、`_position_readiness`、`_scan1_physical_readiness`、`_projection_at` 这些**不含
`role == "SCAN2"` 判断**的方法，整体搬到 `entry_dispatch.py`，组成一个函数。

**接线 `get_or_create_for_passage`（Task 1 Step 8 已定义，这里是它唯一的调用点）**：原 `_apply_scan3` 直接在
`ManualPickingPassage` 单行上赋值 `scan3_evidence_id`/`scan3_command_code`；拆表后 `ReturnLeg` 是独立行，SCAN3
第一次关联到某次经过时该行还不存在。`_apply_scan3` 的新版本要先调用
`return_leg = await self._returns.get_or_create_for_passage(db, passage.id, workline_id, passage.bin_code)`
拿到（或新建）对应的 `ReturnLeg`，再对它赋值 `scan3_evidence_id`/`scan3_command_code`/`scan3_route`，不是对
`EntryPassage` 赋值。`_apply_scan4` 同理，操作的是已经存在的 `return_leg`（SCAN4 之前 SCAN3 必然先发生）。

```python
async def dispatch_scan_event(
    db: Any,
    evidence: Any,
    workline: Any,
    bindings: dict[str, str],
    *,
    entry: Any,
    returns: Any,
    works: Any,
    on_scan2: Callable[..., Awaitable[str | None]],
    plugin_key: str,
) -> tuple[str | None, str]:
    """入线/货架循环/回程段的统一分发；SCAN2 之后委托给插件自己的 on_scan2。"""
    ...
    if role == "SCAN2":
        return await on_scan2(db, evidence, workline_id, bindings, raw_code, event.timestamp, passage), role
    ...
```

- [ ] **Step 2: 瘦身 `scan_flow.py`，只留 `_apply_admission_result`、`_apply_completed`，`apply_in_session` 改为调用 `dispatch_scan_event`**

```python
async def apply_in_session(self, db: Any, evidence_id: int, *, workline_id: int) -> BusinessEvidenceApplication:
    ...
    result, role = await dispatch_scan_event(
        db, evidence, workline, bindings,
        entry=self._entry, returns=self._returns, works=self._works,
        on_scan2=self._apply_scan2,
        plugin_key=DEFINITION.plugin_key,
    )
    ...
```

`_apply_scan2` 保留在 `scan_flow.py`（是 spec §6.3 说的插件私有方法），签名改为符合 `on_scan2` 的约定。

- [ ] **Step 3: 运行全量测试，确认零行为变化**

```bash
uv run pytest workline_plugins/manual-picking/tests/ -v
```

Expected: 全绿，包括 Task 1 Step 14 新增的约束 B 回归测试。这一步纯粹是代码搬家，任何一个既有测试的断言内容都
不应该改变。

- [ ] **Step 4: Review Focus 回归测试 —— 约束 A（AGV 到位顺序不可控）**

在 `test_rack_cycle_postgresql.py`（已有真实 PostgreSQL 集成测试基座）新增：

```python
async def test_two_racks_ctu01_dispatched_in_order_arrive_out_of_order(pg_session) -> None:
    """约束 A：先下发 CTU01 的货架不保证先到位，WES 不能按下发顺序推进。"""
    # 依赖既有 rack_cycle_support.py 的 fixture 构造两个货架的 CTU01 请求，
    # 先下发 RACK-A 的 CTU01，再下发 RACK-B 的 CTU01；
    # 但让 RACK-B 的 Transport 结果先权威到达（SUCCEEDED），RACK-A 的结果后到。
    # 断言：RACK-B 的 inbound_batch 可以先推进，不因为它是"后下发的"而被阻塞；
    #       RACK-A 的结果到达后独立推进，两者不互相依赖下发顺序。
    ...
```

Run: `uv run pytest workline_plugins/manual-picking/tests/test_rack_cycle_postgresql.py -k out_of_order -v`

（这个测试需要真实 PostgreSQL，按 `scripts/run-integration-tests.sh` 的既有集成测试入口跑，不在默认 FAST 回归里；
参照 `rack_cycle_support.py` 里已有的 fixture 写法填充具体断言，不要新建一套 fixture 机制。）

Expected: 先 FAIL 或 SKIP（如果当前代码已经正确处理，这一步应该直接 PASS，因为 `batch_driver.py`/`rack_readiness.py`
的判定本来就基于 `PositionProjection` 和 Transport 权威结果，不基于下发顺序——写这个测试的目的是把这条已有但
隐式的不变量显式锁住，防止未来的改动不小心引入顺序假设）。

- [ ] **Step 5: 拆分 `test_scan_flow.py`，让测试归属跟着生产代码走**

Task 1.2 Step 1～2 把生产代码拆成了 `entry_dispatch.py`（bin-line-common 归属）和瘦身后的 `scan_flow.py`（manual-picking
私有）。`test_scan_flow.py` 现在混着两段的测试用例，如果整个留在 `manual-picking`，`bin-line-common` 的 SCAN1/3/4
逻辑就没有自己包里的直接测试（spec §12 要求"`bin-line-common` 的入线/货架循环/回程/drain/任务完成行为"测试归属
`workline_plugins/bin-line-common/tests/`）。

拆分规则：一个测试函数只要**没有**调用 `_wms(...)`（构造 `InboundEvidenceKind.WMS_RESULT`/`WMS_EVENT` 证据，即
`work_admission`/`work_completed` 相关）、也**没有**断言 `admission_result`/`wms_result` 相关字段，就属于入线/回程段，
移到新文件；反之留在 `test_scan_flow.py`。用这条规则快速过一遍先用 grep 定位候选：

```bash
grep -n "^async def test_" workline_plugins/manual-picking/tests/test_scan_flow.py \
  | grep -vi "admission\|completed\|work_required\|no_work\|wms"
```

把 grep 出来的函数（含 Task 1.1 Step 14、Task 1.2 Step 4 两个本计划新增的测试）整体剪切到新文件
`workline_plugins/manual-picking/tests/test_entry_dispatch.py`，import 改为从 `entry_dispatch`/`bin_line_common`
引用；没被 grep 出来的（`_apply_scan2`、`_apply_admission_result`、`_apply_completed` 相关）留在
`test_scan_flow.py`。两个文件都跑一遍确认没有测试在拆分过程中丢失：

```bash
uv run pytest workline_plugins/manual-picking/tests/test_scan_flow.py workline_plugins/manual-picking/tests/test_entry_dispatch.py -v --collect-only | grep "test session starts" -A1
```

Expected: 两个文件收集到的用例总数等于拆分前 `test_scan_flow.py` 的用例数（不多不少）。

- [ ] **Step 6: 确认 Step 5 测试通过，形成 Stage 1 可提交检查点**

```bash
uv run pytest workline_plugins/manual-picking/tests/ -v
```

当前用户目标包含 Commit、Ship 或创建 PR 时，才暂存 Stage 1 的模型、migration、Repository、分发器与测试，建议消息
`refactor(manual-picking): 闭合 bin line 共享边界`；仅要求实施时不暂存、不提交。

---

## Stage 2：一次性创建目标包并完成命名、搬迁和所有权迁移

本 Stage 折叠原 Task 3/4/5，不产生空包或只改名的独立交付。先枚举全部生产/测试调用点，再在一个受控传播中完成
包创建、共享拓扑迁移、公共符号改名、文件搬迁、静态模型注册和测试 owner 迁移；完成前不运行整目录测试。

### Step 2.1：机械改名（§6.4 清单）

**Files:**
- Modify: `workline_plugins/manual-picking/src/manual_picking/application/entry_dispatch.py`（Step 1 里的 step 常量）
- Modify: `workline_plugins/manual-picking/src/manual_picking/application/return_repository.py`（drain 相关 step 常量若引用在这里）
- Test: 无新增，靠 Task 1/2 已有测试兜底

**Interfaces:**
- Consumes: Task 1.2 产出的 `entry_dispatch.py`
- Produces: 改名后的 step 常量和公共符号，供 Step 2.3 直接搬迁

- [ ] **Step 1: 按 spec §6.4 表格逐个改名**

在 `entry_dispatch.py` 和相关 drain/batch 文件里，把：

| 原值 | 改为 |
| --- | --- |
| `MANUAL_PICKING_TRANSFER_RACK_OUT` | `BIN_LINE_TRANSFER_RACK_OUT` |
| `MANUAL_PICKING_SOURCE_RACK_ROTATE` | `BIN_LINE_SOURCE_RACK_ROTATE` |
| `MANUAL_PICKING_SOURCE_RACK_OUT` | `BIN_LINE_SOURCE_RACK_OUT` |
| `MANUAL_PICKING_RETURN_RACK_ROTATE` | `BIN_LINE_RETURN_RACK_ROTATE` |
| `MANUAL_PICKING_RETURN_RACK_OUT` | `BIN_LINE_RETURN_RACK_OUT` |
| `MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN` | `BIN_LINE_RETURN_BUFFER_DRAIN_RACK_IN` |
| `MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_ROTATE` | `BIN_LINE_RETURN_BUFFER_DRAIN_RACK_ROTATE` |
| `MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT` | `BIN_LINE_RETURN_BUFFER_DRAIN_RACK_OUT` |
| `"MANUAL_PICKING_INBOUND_BATCH"` | `"BIN_LINE_INBOUND_BATCH"` |
| `"MANUAL_PICKING_RETURN_BATCH"` | `"BIN_LINE_RETURN_BATCH"` |

`execution_ref_id=f"manual-picking:{evidence_id}:{role}"` 改为 `execution_ref_id=f"{plugin_key}:{evidence_id}:{role}"`，
`_move`/`release_point2` 相关函数新增 `plugin_key: str` 参数，调用方（`scan_flow.py` 的 `_apply_scan2` 之外的入口）
传 `DEFINITION.plugin_key`。

日志原因码 `manual_picking.return_rack_arrival_*` 去掉插件名前缀，改为 `bin_line.return_rack_arrival_*`。

共享生产类同步改名，不保留兼容别名：

| 原符号 | 目标符号 |
| --- | --- |
| `ManualPickingBatchDriver` | `BinLineBatchDriver` |
| `ManualPickingBatchFlow` | `BinLineBatchFlow` |
| `ManualPickingBatchResultFlow` | `BinLineBatchResultFlow` |
| `ManualPickingDrainFlow` | `BinLineDrainFlow` |
| `ManualPickingCompletionFlow` | `BinLineCompletionFlow` |
| `ManualPickingTransportOutcomePublisher` | `BinLineTransportOutcomePublisher` |

`EntryPassage`、`ReturnLeg` 保持简洁领域名，不添加重复的 `BinLine` 前缀。

- [ ] **Step 2: 运行直接断言 step/类名的聚焦测试，不在机械传播完成前运行整目录**

```bash
uv run pytest workline_plugins/manual-picking/tests/test_batch_flow.py \
  workline_plugins/manual-picking/tests/test_transport_outcome.py \
  workline_plugins/manual-picking/tests/test_declaration.py -q
```

Expected: 全绿。如果测试断言具体 step 字面量，按新名字更新；不得借改名放宽行为断言。

- [ ] **Step 3: 扫描旧 step 和 `ManualPicking*` 共享符号残留；不单独提交，继续 Step 2.2**

---

### Step 2.2：创建 `bin-line-common` 包并接入依赖

**Files:**
- Create: `workline_plugins/bin-line-common/pyproject.toml`
- Create: `workline_plugins/bin-line-common/src/bin_line_common/__init__.py`
- Create: `workline_plugins/bin-line-common/src/bin_line_common/topology.py`
- Modify: `workline_plugins/manual-picking/pyproject.toml`（新增依赖）
- Test: 无（纯装配，验证靠 `uv sync` 成功即可）

**Interfaces:**
- Consumes: 无
- Produces: 共享包及唯一拓扑常量 owner；该包不能被 WorkLine 直接绑定

- [ ] **Step 1: 创建包骨架**

```bash
mkdir -p workline_plugins/bin-line-common/src/bin_line_common
```

`workline_plugins/bin-line-common/pyproject.toml`：

```toml
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "wes-bin-line-common"
version = "0.1.0"
description = "Bin 拣料线共享执行逻辑：入线段、货架循环、回程段（非插件，供业务插件依赖）"
requires-python = ">=3.13"
dependencies = ["wes-plugin-sdk==0.1.0"]

[tool.uv.sources]
wes-plugin-sdk = { path = "../../src/wes_plugin_sdk" }

[tool.hatch.build.targets.wheel]
packages = ["src/bin_line_common"]
```

`workline_plugins/bin-line-common/src/bin_line_common/__init__.py`：

```python
"""Bin 拣料线共享执行逻辑：入线段、货架循环、回程段。

非插件——没有 plugin_key，不能被 WorkLine 直接绑定。只被具体业务插件（manual-picking、
automatic-picking）依赖，不导入任何具体插件。
"""

__all__: list[str] = []
```

`topology.py` 从 `manual_picking.definition` 一次性迁入 `FIVE_RACK`、`RETURN_RACK`、`TRANSFER_RACK`、`INLET`、
`OUTLET` 五个不可变 `WorkLinePositionSlot`。`manual_picking.definition` 和未来 `automatic_picking.definition` 只引用
这些共享对象；共享包不得导入任一具体插件。具体插件的 device roles 和 `PluginDefinition` 仍由插件自己拥有。

- [ ] **Step 2: 让 `manual-picking` 依赖它**

编辑 `workline_plugins/manual-picking/pyproject.toml`，`dependencies` 加一行：

```toml
dependencies = ["wes-plugin-sdk==0.1.0", "wes-bin-line-common==0.1.0"]
```

`[tool.uv.sources]` 加一行：

```toml
wes-bin-line-common = { path = "../bin-line-common" }
```

- [ ] **Step 3: 验证依赖装配成功**

```bash
uv sync --dev
uv run python -c "import bin_line_common; import manual_picking; print('ok')"
```

Expected: 输出 `ok`，无 import 错误。

- [ ] **Step 4: 依赖解析成功后不创建空包提交，继续 Step 2.3 搬迁生产代码**

---

### Step 2.3：物理搬迁——把已验证的模块从 `manual-picking` 移进 `bin-line-common`

**Files:**
- Move: `workline_plugins/manual-picking/src/manual_picking/application/entry_model.py` → `workline_plugins/bin-line-common/src/bin_line_common/entry_model.py`
- Move: `entry_repository.py`、`entry_dispatch.py`、`return_model.py`、`return_repository.py` → 同上（`bin_line_common/` 下）
- Move: `batch_driver.py`、`batch_repository.py`、`batch_flow.py`、`batch_result.py`、`batch_policy.py`、`batch_transport.py` → `bin_line_common/`
- Move: `drain_flow.py`、`drain_repository.py`、`transport_outcome.py`、`rack_readiness.py`、`completion_flow.py` → `bin_line_common/`
- Move: `handlers/scan1.py`、`scan3.py`、`scan4.py`、`scan_types.py` → `bin_line_common/handlers/`
- Modify: `workline_plugins/manual-picking/src/manual_picking/application/scan_flow.py`（import 改为从 `bin_line_common` 引用）
- Modify: `workline_plugins/manual-picking/src/manual_picking/application/plugin.py`（同上）
- Modify: `workline_plugins/manual-picking/src/manual_picking/application/completion_repository.py`（同上）
- Modify: `deployment/plugin_models.py`（静态消费者集合注册共享模型）
- Modify: `deployment/plugin_composition.py`（改用 `BinLine*` 公共符号并显式传身份参数）
- Modify: `tests/architecture/test_plugin_sdk_boundary_guardrail.py`（禁止 `bin_line_common` 导入具体插件）
- Test: 纯共享行为测试移入 `workline_plugins/bin-line-common/tests/`；手工工作段和 Repository 测试留在原包

**Interfaces:**
- Consumes: Step 2.2 的包结构，Stage 1 已验证的模块
- Produces: `bin_line_common.entry`、`bin_line_common.batch`、`bin_line_common.drain`、`bin_line_common.handlers` 等子模块，供 `manual-picking` 和未来 `automatic-picking` 导入

- [ ] **Step 1: 用 `git mv` 逐个搬迁文件（保留 git 历史）**

```bash
cd /Users/kaizhou/codeDev/wes_backend
mkdir -p workline_plugins/bin-line-common/src/bin_line_common/handlers

git mv workline_plugins/manual-picking/src/manual_picking/application/entry_model.py \
       workline_plugins/bin-line-common/src/bin_line_common/entry_model.py
git mv workline_plugins/manual-picking/src/manual_picking/application/entry_repository.py \
       workline_plugins/bin-line-common/src/bin_line_common/entry_repository.py
git mv workline_plugins/manual-picking/src/manual_picking/application/entry_dispatch.py \
       workline_plugins/bin-line-common/src/bin_line_common/entry_dispatch.py
git mv workline_plugins/manual-picking/src/manual_picking/application/return_model.py \
       workline_plugins/bin-line-common/src/bin_line_common/return_model.py
git mv workline_plugins/manual-picking/src/manual_picking/application/return_repository.py \
       workline_plugins/bin-line-common/src/bin_line_common/return_repository.py
git mv workline_plugins/manual-picking/src/manual_picking/application/batch_driver.py \
       workline_plugins/bin-line-common/src/bin_line_common/batch_driver.py
git mv workline_plugins/manual-picking/src/manual_picking/application/batch_repository.py \
       workline_plugins/bin-line-common/src/bin_line_common/batch_repository.py
git mv workline_plugins/manual-picking/src/manual_picking/application/batch_flow.py \
       workline_plugins/bin-line-common/src/bin_line_common/batch_flow.py
git mv workline_plugins/manual-picking/src/manual_picking/application/batch_result.py \
       workline_plugins/bin-line-common/src/bin_line_common/batch_result.py
git mv workline_plugins/manual-picking/src/manual_picking/application/batch_policy.py \
       workline_plugins/bin-line-common/src/bin_line_common/batch_policy.py
git mv workline_plugins/manual-picking/src/manual_picking/application/batch_transport.py \
       workline_plugins/bin-line-common/src/bin_line_common/batch_transport.py
git mv workline_plugins/manual-picking/src/manual_picking/application/drain_flow.py \
       workline_plugins/bin-line-common/src/bin_line_common/drain_flow.py
git mv workline_plugins/manual-picking/src/manual_picking/application/drain_repository.py \
       workline_plugins/bin-line-common/src/bin_line_common/drain_repository.py
git mv workline_plugins/manual-picking/src/manual_picking/application/transport_outcome.py \
       workline_plugins/bin-line-common/src/bin_line_common/transport_outcome.py
git mv workline_plugins/manual-picking/src/manual_picking/application/rack_readiness.py \
       workline_plugins/bin-line-common/src/bin_line_common/rack_readiness.py
git mv workline_plugins/manual-picking/src/manual_picking/application/completion_flow.py \
       workline_plugins/bin-line-common/src/bin_line_common/completion_flow.py
git mv workline_plugins/manual-picking/src/manual_picking/handlers/scan1.py \
       workline_plugins/bin-line-common/src/bin_line_common/handlers/scan1.py
git mv workline_plugins/manual-picking/src/manual_picking/handlers/scan3.py \
       workline_plugins/bin-line-common/src/bin_line_common/handlers/scan3.py
git mv workline_plugins/manual-picking/src/manual_picking/handlers/scan4.py \
       workline_plugins/bin-line-common/src/bin_line_common/handlers/scan4.py
git mv workline_plugins/manual-picking/src/manual_picking/handlers/scan_types.py \
       workline_plugins/bin-line-common/src/bin_line_common/handlers/scan_types.py

touch workline_plugins/bin-line-common/src/bin_line_common/handlers/__init__.py
```

- [ ] **Step 2: 全局搜索并修正 import 路径**

```bash
grep -rl "from manual_picking.application.entry_model\|from manual_picking.application.entry_repository\|from manual_picking.application.entry_dispatch\|from manual_picking.application.return_model\|from manual_picking.application.return_repository\|from manual_picking.application.batch_\|from manual_picking.application.drain_\|from manual_picking.application.transport_outcome\|from manual_picking.application.rack_readiness\|from manual_picking.application.completion_flow\|from manual_picking.handlers.scan1\|from manual_picking.handlers.scan3\|from manual_picking.handlers.scan4\|from manual_picking.handlers.scan_types\|from \.entry_model\|from \.entry_repository\|from \.entry_dispatch\|from \.return_model\|from \.return_repository\|from \.batch_\|from \.drain_\|from \.transport_outcome\|from \.rack_readiness\|from \.completion_flow" \
  workline_plugins/manual-picking/src workline_plugins/manual-picking/tests
```

对每个匹配文件，把相对导入（`from .batch_driver import X`）改成绝对导入（`from bin_line_common.batch_driver import X`），
把 `from manual_picking.application.xxx import Y` 改成 `from bin_line_common.xxx import Y`。`handlers/__init__.py`
里对 `Scan1Handler`/`Scan3Handler`/`Scan4Handler` 的重导出也要改成从 `bin_line_common.handlers` 导入。

- [ ] **Step 3: 闭合共享身份、到位货架推进和静态模型注册**

1. `BinLineTransportOutcomePublisher` 构造时显式接收 `plugin_key` 与稳定 `contract_key`；错误文本使用传入的
   `plugin_key`，Evidence 持久化使用传入的 `contract_key`。禁止从名称字符串推导合同，也不保留人工插件 alias。
2. `BinLineBatchDriver` 不再读取来源货架成员投影。它从当前工作位 `PositionProjection.source_transport_task_id`
   反查原 `TransportDecisionBinding`，由 binding 的 `picking_task_id/source_evidence_id` 确定是否有 task owner；
   binding 缺失、未闭合或与位置事实不匹配时停止并对账，不猜 owner。
3. 有 task owner 时，从“本次到位 Binding 之后、当前 rack/face”的最近封闭 Batch Evidence 推导优先方向：
   `inbound_batch` 后优先 `return_batch`，反之亦然；优先方向明确 `NO_BATCH`/无候选时，同一 tick 尝试另一方向。
   无 task owner 时只尝试 `return_batch`。不得新增轮转状态表或来源货架历史投影。
4. `deployment/plugin_models.py` 使用静态消费者集合：启用 `manual-picking` 或 `automatic-picking` 任一者时注册
   `EntryPassage`/`ReturnLeg`；只有启用 `manual-picking` 时注册 `ManualPickingWork`。`include_plugin_table` 使用同一规则。
5. 扩展既有 architecture dependency scanner：`bin_line_common` 可依赖 SDK 与已批准宿主基础端口，但禁止静态、
   动态或字符串拼接导入 `manual_picking`、`automatic_picking` 等具体插件。

- [ ] **Step 4: 只做 import/装配 smoke，整目录测试留到测试 owner 搬迁完成后**

```bash
uv sync --dev
uv run python -c "import bin_line_common; import manual_picking; print('ok')"
uv run pytest workline_plugins/manual-picking/tests/test_declaration.py -q
```

Expected: import 输出 `ok`，装配测试通过。失败先检查 import/构造参数传播，不修改行为断言迁就错误。

- [ ] **Step 4: Review Focus 回归测试 —— 约束 C（`return_batch` 按 SCAN4 顺序）**

确认 `bin_line_common/return_repository.py` 的 `unfinished_return_prefix_for_update`/`ready_return_prefix_for_update`
搬迁后排序键没变，在 `test_batch_flow.py` 新增：

```python
@pytest.mark.asyncio
async def test_return_batch_dispatched_in_scan4_arrival_order_not_task_order() -> None:
    """约束 C：出口料箱按 SCAN4 到位顺序下发 return_batch，与所属任务无关。"""
    flow, evidences, entry, returns, works, commands, admissions = _setup()
    # 构造两个不同 task_id 的料箱，让 task B 的料箱先到 SCAN4，task A 的后到；
    # 断言 return_batch 的候选顺序是 [B 的料箱, A 的料箱]，不是按 task_id 或创建顺序排序。
    ...
```

Run: `uv run pytest workline_plugins/bin-line-common/tests/ -k return_batch_dispatched_in_scan4_arrival_order -v`

（注意：这个测试文件此时应该也已经搬到 `bin-line-common` 自己的测试目录——见 Step 6。）

- [ ] **Step 5: Review Focus 回归测试 —— 借用与单独呼叫互斥（用户补充约束6）**

`test_batch_flow.py:688` 的 `test_initial_feed_and_finished_face_do_not_start_opportunistic_return` 已经覆盖了
"不该借用"的负向场景，但没有测试正向互斥——当前面正处于借用窗口（`feed_complete=True`、还没换面/离场，见
`batch_driver.py:395` 的 `_advance_return_batch_before_rack_action` 调用点）时，`_advance_drain`
（[batch_driver.py:194](../../../workline_plugins/manual-picking/src/manual_picking/application/batch_driver.py:194)）
不应该同时尝试给这条 WorkLine 分配专属退箱货架。在 `test_batch_flow.py` 新增：

```python
@pytest.mark.asyncio
async def test_opportunistic_return_and_dedicated_drain_are_mutually_exclusive() -> None:
    """借用刚 feed_complete 的当前架做 return_batch 时，drain 不会同时申请专属退箱货架。"""
    driver = _build_driver()  # 复用既有 fixture 工厂，参照文件内其它 test_ 函数的构造方式
    line, task = ...  # 构造当前面 feed_complete=True、尚未换面/离场，且 RETURN_BUFFER 有 ready 候选的场景
    filled = await driver.advance_in_session(db, line, task)
    # 断言：产生了借用路径的 return_batch 请求（_advance_return_batch_before_rack_action 命中，filled >= 1）；
    # 且同一次 advance_in_session 调用里，没有创建任何 DRAIN_RACK_IN_STEP 请求
    # （_advance_drain 的 allow_active_task 判定此时当前面被占用，应该直接跳过）。
    ...
```

Run: `uv run pytest workline_plugins/manual-picking/tests/test_batch_flow.py -k mutually_exclusive -v`
Expected: PASS——如果失败，说明现有代码的互斥条件本身有问题（不是本次抽取引入的回归），按
superpowers:systematic-debugging 处理，不要为了让测试通过而放宽断言。

- [ ] **Step 5B: 到位事实驱动与公平交替回归**

在搬迁后的 `bin-line-common/tests/test_batch_flow.py` 覆盖以下分支，每个分支都以当前
`PositionProjection.source_transport_task_id` 和匹配的 `TransportDecisionBinding` 为起点：

1. 有 task owner、最近封闭的是 `inbound_batch`、return 有候选：先创建 `return_batch`。
2. 有 task owner、优先 return 明确无候选、inbound 有候选：同一 tick 回退创建 `inbound_batch`。
3. 有 task owner、最近封闭的是 `return_batch`：先尝试 `inbound_batch`。
4. 无 task owner：即使 inbound Evidence 存在也不创建 `inbound_batch`，只尝试 `return_batch`。
5. 当前到位 Binding 之前的旧 Batch 历史不参与本轮交替起点。
6. Projection、Transport 与 Binding 任一 identity 不匹配时不推进，并保留原物理身份进入对账。
7. 同一 rack/face 在不同 revision 重复出现时，只使用把当前货架送到位的 Binding 对应 revision，不读“最新”记录。

Run: `uv run pytest workline_plugins/bin-line-common/tests/test_batch_flow.py -k "alternat or task_owner or arrival_binding" -q`

- [ ] **Step 6: 把纯粹测试共享逻辑的测试文件也搬到 `bin-line-common`**

```bash
mkdir -p workline_plugins/bin-line-common/tests

git mv workline_plugins/manual-picking/tests/test_batch_flow.py workline_plugins/bin-line-common/tests/test_batch_flow.py
git mv workline_plugins/manual-picking/tests/test_batch_policy.py workline_plugins/bin-line-common/tests/test_batch_policy.py
git mv workline_plugins/manual-picking/tests/test_batch_repository.py workline_plugins/bin-line-common/tests/test_batch_repository.py
git mv workline_plugins/manual-picking/tests/test_batch_transport.py workline_plugins/bin-line-common/tests/test_batch_transport.py
git mv workline_plugins/manual-picking/tests/test_drain_flow.py workline_plugins/bin-line-common/tests/test_drain_flow.py
git mv workline_plugins/manual-picking/tests/test_drain_repository.py workline_plugins/bin-line-common/tests/test_drain_repository.py
git mv workline_plugins/manual-picking/tests/test_rack_readiness.py workline_plugins/bin-line-common/tests/test_rack_readiness.py
git mv workline_plugins/manual-picking/tests/test_transport_outcome.py workline_plugins/bin-line-common/tests/test_transport_outcome.py
git mv workline_plugins/manual-picking/tests/test_completion_flow.py workline_plugins/bin-line-common/tests/test_completion_flow.py
git mv workline_plugins/manual-picking/tests/test_passage_model.py workline_plugins/bin-line-common/tests/test_entry_return_model.py
git mv workline_plugins/manual-picking/tests/test_scan_handlers.py workline_plugins/bin-line-common/tests/test_scan_handlers.py
git mv workline_plugins/manual-picking/tests/test_source_progression.py workline_plugins/bin-line-common/tests/test_source_progression.py
git mv workline_plugins/manual-picking/tests/test_return_rack_progression.py workline_plugins/bin-line-common/tests/test_return_rack_progression.py
git mv workline_plugins/manual-picking/tests/rack_cycle_support.py workline_plugins/bin-line-common/tests/rack_cycle_support.py
git mv workline_plugins/manual-picking/tests/test_rack_cycle_postgresql.py workline_plugins/bin-line-common/tests/test_rack_cycle_postgresql.py
git mv workline_plugins/manual-picking/tests/test_entry_dispatch.py workline_plugins/bin-line-common/tests/test_entry_dispatch.py
```

`test_scan_flow.py`（Task 1.2 Step 5 拆分后只剩 SCAN2/准入/完成用例）、`test_completion_repository.py`、
`test_plan_admission.py`、`test_prepare_policy.py`、`test_declaration.py`、
`test_plan_applied_handler.py`、`test_departure_owner.py`、`test_business_loop.py` **留在 `manual-picking`**——
它们验证的是插件整体行为（含 SCAN2 工作段）、人工私有 Repository 或装配/策略，不是纯共享逻辑。
`test_completion_flow.py` 与 `test_entry_dispatch.py` 跟随共享生产 owner 搬到 `bin-line-common`。

更新这批搬迁文件里的 import（同 Step 2 的做法）。

- [ ] **Step 7: 两边都跑一次全量测试**

```bash
uv run pytest workline_plugins/bin-line-common/tests/ -v
uv run pytest workline_plugins/manual-picking/tests/ -v
uv run pytest tests/architecture/test_plugin_sdk_boundary_guardrail.py tests/deployment/test_plugin_models.py -q
```

Expected: 两边都全绿。

- [ ] **Step 8: 同步 `docs/architecture/heavy-test-impact.toml`**

按 CLAUDE.md 要求，新增的 `workline_plugins/bin-line-common/` 路径要加进精确 mapping，避免它被算进默认快速回归集
之外（或者反之，看现有 `heavy-test-impact.toml` 对 `manual-picking` 现有测试的分类方式，照搬同样的分类逻辑）。

- [ ] **Step 9: 同步 `docs/architecture/file_index.md`**

按 CLAUDE.md"文档同步规则"，把 `workline_plugins/bin-line-common/` 的新增文件加进项目文件索引。

- [ ] **Step 10: Stage 2 验证与可提交检查点**

当前用户目标包含 Commit、Ship 或创建 PR 时，才暂存 Stage 2 文件并使用建议消息
`refactor(bin-line-common): 抽取入线货架循环与回程逻辑`；仅要求实施时不暂存、不提交。

---

## Stage 3：宿主静态 composition、迁移链与最终门禁

**Files:**
- Modify: `deployment/plugin_composition.py`（使用 `BinLine*` 符号并向 outcome publisher 显式传 `plugin_key/contract_key`）
- Modify: `deployment/plugin_models.py`（共享模型消费者集合 + 人工私有模型条件注册）
- Test: `tests/deployment/test_plugin_models.py` 与既有 composition 测试
- Test: 干净 PostgreSQL migration parent → head → parent → head

**Interfaces:**
- Consumes: Stage 2 完成后的 `manual-picking`（依赖 `bin-line-common`）
- Produces: 四种插件启用组合的确定模型注册、最终验证证据

- [ ] **Step 1: 跑既有部署/装配测试，确认手工插件单独启动仍然正常**

```bash
uv run pytest tests/deployment/ -v
```

Expected: 全绿。覆盖 `none`、仅 `manual-picking`、仅 `automatic-picking`、两者同时启用：共享表在至少一个消费者
启用时注册，`manual_picking_works` 只随人工插件注册；`bin-line-common` 自身永远不成为 `plugin_key`。

- [ ] **Step 2: 运行 migration、最终聚焦门禁和官方本地环境检查**

```bash
uv run pytest workline_plugins/bin-line-common/tests/ -q
uv run pytest workline_plugins/manual-picking/tests/ -q
uv run pytest tests/architecture/test_plugin_sdk_boundary_guardrail.py tests/deployment/ -q
# 在独占干净 PostgreSQL 逻辑库执行 Stage 1 Step 3 migration 链验证。
./scripts/git-quality-gate.sh --profile quality
uv run scripts/select_heavy_tests.py --scope unstaged
# 仅运行 selector 输出的 manifest；若输出 NONE 则记录 NONE，不运行全量 HEAVY。
./scripts/dev-env.sh up
./scripts/dev-env.sh check
./scripts/dev-env.sh down
```

Expected: 所有必选门禁通过；官方本地环境正常启动并检查后关闭，且不携带 `--volumes`。

- [ ] **Step 3: 若 Stage 3 发现并修复真实缺口，是否提交仍按当前用户目标判断；验证本身不创建 Commit**

---

## Open Questions

无。`plan_revision` 已在前置合同中成为人工料箱准入/完成的显式身份组成部分；WMS 书面确认与对应 wire 实施是
Stage 1 的阻塞条件，不再作为非阻塞跟进项。

## 本计划完成后的状态

- `workline_plugins/bin-line-common/` 是一个不可独立部署的共享包，`manual-picking` 依赖它，现有测试全绿，行为
  零变化。
- `automatic-picking` 插件（另一份计划）可以在此基础上直接 `dependencies = ["wes-bin-line-common==0.1.0"]`，
  复用 `dispatch_scan_event`，不需要重新实现入线段/货架循环/回程段。
- [回程重试修正](../specs/2026-09-22-bin-line-scan-retry-fix.md)、可靠恢复、revision identity 和 Evidence 驱动
  来源货架改造均已作为冻结基线合入；抽取不会重新解释这些行为。

## What already exists

| 已有能力 | 本计划如何复用 |
| --- | --- |
| `InboundEvidence` | 保存 `plan_delta` 与迟到结果的不可变可靠输入，不新增来源货架历史投影 |
| `TransportDecisionBinding` | 保存已下发物理动作的原身份与资源围栏，不新增第二套业务围栏 |
| `PositionProjection` | 只判断当前工作位货架及物理位置，不从计划顺序猜测到位顺序 |
| manual-picking 入线/Batch/Drain/Completion 实现 | 先在原包内按 owner 拆分，再原样迁移通用行为 |
| `wes_plugin_sdk.wms_operations` | 继续创建 typed intent；共享包不重建 HTTP、可靠发送或 operation registry |
| 现有插件测试与架构依赖扫描器 | 测试随生产 owner 迁移；扩展现有 scanner，不另写 import parser |
| `deployment/plugin_models.py` | 扩展静态消费者规则，不引入动态插件模型 registry |

## NOT in scope

- `automatic-picking` 工作段、双臂流程和私有 schema：由后续已批准计划实现；本计划只提供共享执行包。
- 回程扫码 retry、可靠恢复、revision wire 与 Evidence 驱动来源货架的生产实现：它们是本计划前置项，不在抽取中混做。
- WMS 业务准入、货架优先级或料箱选择规则：继续由 WMS 决定，WES 只可靠保存、调度和执行。
- 新的来源货架历史表、轮转状态表、业务唯一围栏、兼容 alias 或旧 import wrapper：均明确不建设。
- 包索引发布：`wes-bin-line-common` 仅通过 uv workspace path 使用，不发布 PyPI 或独立制品。
- Merge、Deploy 与现场验收：本计划只定义实现和验证，后续动作按当时用户目标授权。

## Test Coverage Diagram

```text
CODE PATHS                                                TEST OWNERS
[+] Stage 1: schema / repository split
  ├── [★★★ PLANNED] parent → head → parent → head          clean PostgreSQL migration check
  ├── [★★★ PLANNED] Entry/Return/Work CHECK + UNIQUE       bin-line-common/test_entry_return_model.py
  ├── [★★★ PLANNED] WorkLine 7/8 isolation                manual-picking/test_scan_flow.py
  ├── [★★★ PLANNED] same-line multi-task coexistence      manual-picking/test_scan_flow.py
  ├── [★★★ PLANNED] explicit revision duplicate callback  manual-picking/test_scan_flow.py
  └── [★★★ PLANNED] SCAN1 NG → SCAN3 without SCAN2        manual-picking/test_scan_flow.py

[+] Stage 2: shared package
  ├── [★★★ EXISTING+MOVED] SCAN1/3/4 dispatch             bin-line-common/test_entry_dispatch.py
  ├── [★★★ EXISTING+MOVED] return FIFO by SCAN4 order     bin-line-common/test_batch_flow.py
  ├── [★★★ PLANNED] task owner inbound ⇄ return fallback  bin-line-common/test_batch_flow.py
  ├── [★★★ PLANNED] no task owner → return only           bin-line-common/test_batch_flow.py
  ├── [★★★ PLANNED] old Batch history before arrival ignored
  ├── [★★★ PLANNED] arrival Binding mismatch → reconcile
  ├── [★★★ EXISTING+MOVED] Drain/rack readiness/outcome    bin-line-common tests
  ├── [★★★ EXISTING+MOVED] Completion flow                bin-line-common/test_completion_flow.py
  └── [★★★ PLANNED] shared package cannot import plugins  architecture guardrail

[+] Stage 3: deployment
  ├── [★★★ PLANNED] no plugin / manual / automatic / both model registration
  ├── [★★★ EXISTING+UPDATED] manual composition starts
  ├── [★★★ PLANNED] publisher plugin_key + contract_key identity
  └── [★★★ PLANNED] official local environment import/health check

COVERAGE PLAN: every changed branch above has an existing or planned primary owner.
Legend: ★★★ = behavior + edge/error path | EXISTING+MOVED = current assertion retained under new owner
```

## Failure modes

| Code path | Realistic failure | Test | Handling / user-visible result |
| --- | --- | --- | --- |
| plan prerequisite gate | old base passes a weak history check | exact SHA ancestor + focused tests | Stage 1 blocked with named prerequisite |
| migration downgrade | revision rolls back but old table is absent | parent/head round trip | migration fails before delivery; no false success |
| cross-WorkLine query | FIFO reads another line's Bin | mixed WorkLine repository tests | transaction rejects/defers current Evidence |
| repeated revision callback | late revision 1 result mutates revision 2 | explicit revision test | conflict/reconciliation; never latest-row fallback |
| current rack owner | Projection points to Transport with mismatched Binding | arrival identity test | stop and reconcile; preserve original physical identity |
| Batch arbitration | historical Batch before this arrival flips priority | arrival-boundary test | ignore history before current Binding |
| shared outcome publisher | automatic result stored with manual contract key | parameterized identity test | explicit plugin/contract identity, no string inference |
| plugin model loading | automatic-only deployment omits shared tables | four-combination deployment test | startup/migration gate fails closed |
| package dependency | shared package imports concrete plugin | architecture scanner fixtures | QUALITY fails with exact forbidden import |

Critical gaps（无测试 + 无处理 + 静默失败）：0，前提是所有 PLANNED 测试随对应 Stage 实施。

## Worktree parallelization strategy

Sequential implementation, no parallelization opportunity. Stage 1 修改模型、migration、Repository 与分发接缝；Stage 2
移动同一批生产和测试文件；Stage 3 验证同一装配与 schema。三个 Stage 共享主要模块且严格依赖前一 Stage 的稳定结果，
拆到并行 worktree 会增加 rename/migration 冲突。四个前置计划可独立排期，但必须各自合入 `develop` 并把 merge SHA
写回本计划后，才启动本计划的单一实施 lane。

## Implementation Tasks

- [ ] **T1（P1，human: ~2-3d / CC: ~3-5h）** — WMS identity — 冻结并实施 revision identity 合同
  - Surfaced by: Architecture — 重复 rack/bin 不能用 `(task_id, bin_code)` 或最新 `id` 区分
  - Files: WMS manual-bin wire、plan delta/runtime identity、manual-picking schema/tests、对应合同
  - Verify: 跨 revision 重复 rack/bin 与迟到 callback 回归；记录 merge SHA
- [ ] **T2（P1，human: ~3-4d / CC: ~5-8h）** — rack scheduling — 改为 Evidence + Position + Binding 驱动
  - Surfaced by: Architecture — 不维护来源货架历史投影或业务围栏
  - Files: outbound-picking plan member owner、manual-picking batch/completion/cancel、tests
  - Verify: 到位 owner、重复 revision、取消、完成、恢复和公平 Batch 仲裁测试；记录 merge SHA
- [ ] **T3（P1，human: ~1d / CC: ~2h）** — manual-picking — 拆分 Passage schema 与 scan 分发接缝
  - Surfaced by: Architecture/Test — 三表 owner、显式 revision、跨 WorkLine 隔离、原子 EXISTS
  - Files: manual-picking models/repositories/scan/completion、migration、tests
  - Verify: Stage 1 聚焦测试、插件全量测试、干净 migration round trip
- [ ] **T4（P1，human: ~1d / CC: ~2h）** — bin-line-common — 一次性迁移共享生产与测试 owner
  - Surfaced by: Code Quality — topology、`BinLine*`、publisher identity、依赖方向
  - Files: bin-line-common、manual-picking imports、deployment composition、architecture guardrail
  - Verify: 两包全量测试、旧符号/import 残留扫描、依赖门禁
- [ ] **T5（P2，human: ~3h / CC: ~30min）** — deployment — 闭合模型注册和最终门禁
  - Surfaced by: Architecture/Test — 共享模型必须随任一消费者静态注册
  - Files: deployment/plugin_models.py、deployment tests、HEAVY mapping、file index
  - Verify: 四种启用组合、QUALITY、selector manifest、官方 dev-env check

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 current | — | 本后端抽取计划未要求新的产品范围评审 |
| Outside Review | Claude Code via `/plan-eng-review` | Independent 2nd opinion | 1 | COMPLETED | 8 findings；5 与主审一致，2 个新增缺口已折叠，1 个模型命名分歧由用户裁决 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 current | CLEAR | 16 issues，0 critical gaps，0 unresolved；scope reduced to 3 stages |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 后端-only，不适用 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 未运行 |

- **OUTSIDE COVERAGE:** provider=`claude-code`，host=`codex`，phase=`plan-review`，state=`completed`。外部复核要求
  reject-and-revise；revision identity、downgrade、悬空 Repository 接口、前置门禁与测试 owner 均已写回本计划。
- **CROSS-MODEL:** 两边同意不能用最新 `id` 代替 revision、downgrade 必须恢复父 schema、行为修正必须先于抽取。
  对模型名是否统一加 `BinLine` 前缀存在分歧；用户决定保留 `EntryPassage` / `ReturnLeg`，只修正错误的
  `ManualPicking*` 共享类名。
- **VERDICT:** ENG CLEARED；计划可在四个前置变更合入、真实 merge SHA 写回并通过聚焦验证后实施。

NO UNRESOLVED DECISIONS
