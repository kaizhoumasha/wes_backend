# RuntimeInbox 验收闭环修复设计

## 背景与目标

`docs/superpowers/plans/2026-07-10-runtime-inbox-single-source-of-truth.md` 的主运行链路已经基本收敛，但逐项验收仍发现数据库约束、模块归属、重放语义、运维脚本、当前文档和真实 PostgreSQL 门禁未完全闭合。

本轮目标是消除这些验收阻断项，使原计划 Task 1–9 的完成状态能够由当前代码、迁移、运行文档和可重复测试证据共同证明，而不是依赖历史执行记录。

## 范围

本轮包含：

- 补齐 RuntimeInbox 数据库约束和 pre-cutover audit-only 迁移语义。
- 将 `RuntimeInboxService` 迁入锁定的 service 模块并删除 consumers 兼容入口。
- 固化人工重放的 `REPLAY_REQUEST` 合同。
- 修复运行数据 reset 脚本并扩大旧入口零引用门禁。
- 更新仍被视为当前事实源的业务、架构和运行文档。
- 让 PostgreSQL migration、integration、resilience 和 benchmark 验收可以安全、重复地执行。
- 重新验收并同步原实施计划状态。

本轮不建设运营 UI、完整告警平台或现场 Runbook，不改变 RuntimeInbox 五态状态机，也不恢复任何 WorklineInbox 兼容表面。

## 架构决策

### 1. 数据库合同

`RuntimeInbox.kind` 与 `RuntimeInbox.status` 使用命名 CHECK constraint，合法集合分别与六类 ingress 和五态状态机一致。新写入保持 canonical envelope 必填语义。

Revision A 负责识别切换前缺少 canonical payload 的旧行，将其转换为明确的 audit-only 终态证据。此类记录不得进入 claim、retry 或人工 replay。Repository 的 claim 条件同时加入防御性 envelope 条件，避免异常或手工数据绕过迁移约束。

迁移必须兼容 fresh database、从 Revision A 父版本升级、Revision A/B 回环，以及包含真实毫秒值和 pre-cutover 行的数据集。

### 2. Service 模块归属

`RuntimeInboxService`、领域异常、接受/重放结果类型和单例统一迁入 `src/app/runtime/orchestration/services/runtime_inbox/`。该目录的 `__init__.py` 是正式导出边界。

`consumers/` 只保留协议 adapter，例如 callback writer。原 `consumers/runtime_inbox_service.py` 物理删除，不提供 import shim。所有生产代码和测试一次性切换到新入口。

Service 继续遵守 Service → Repository → Database，不在迁移过程中引入直接 SQL 查询。

### 3. 人工重放合同

人工重放创建新的 `REPLAY_REQUEST` RuntimeInbox，而不是复制原 `kind`。原始业务类型、source inbox、原 source identity、actor 和 reason 作为 canonical payload 与审计证据保存。

重放记录使用独立、稳定且长度受控的 source identity；重复同一重放请求按同 hash ACK，内容冲突走现有冲突合同。原 DEAD_LETTER 记录保持终态，不被改写。

Processor 对 `REPLAY_REQUEST` 显式解包并路由到原业务语义，仍受 claim、FIFO、token fencing 和 effect 幂等约束。

### 4. 运维与零引用边界

运行数据 reset 脚本的表清单改为显式 schema-qualified identity，不再假设所有运行表都位于 `wes_biz`。删除 `wes_biz.workline_inbox`，增加 `wes_runtime.runtime_inbox`，并保持 dry-run、主数据保护和显式 `--yes` 安全边界。

WorklineInbox 退役 guardrail 扩展扫描 `scripts/` 和被认定为 current 的文档集合。历史迁移、归档文档、负向测试和 downgrade DDL 使用窄化 allowlist，不允许目录级豁免。

### 5. 当前文档口径

以下类型文档必须描述 RuntimeInbox 当前链路：业务 SSOT、runtime workflow guide、当前 E2E 操作指南、file index、runtime ownership、observability 和当前 ADR。

仍有参考价值但描述旧架构的文档移动到归档区或在标题和开头明确标注历史状态。不能让标记为“当前实现”“SSOT”或“唯一权威合同”的文档继续使用 WorklineInbox、旧 task 或旧表名。

`TODOS.md` 仅保留真实未完成且仍在范围内的后续工作；已完成条目删除或移动到历史记录。本轮不强制删除与其他领域相关的有效 TODO。

### 6. PostgreSQL 与性能门禁

Heavy tests 继续要求显式 `INTEGRATION_DATABASE_URL`，只允许创建安全前缀的随机临时数据库，并 patch 真实任务队列 gateway。提供统一的验收入口和环境前置检查，使失败能区分配置缺失、数据库容量不足与业务断言失败。

Benchmark 固化以下硬门禁：

- 1000 条混合 backlog、4 worker。
- claim p95 不高于 150ms。
- 吞吐不低于 1000 条/秒。
- duplicate claim 为 0。
- 锁等待不超过已锁定阈值。
- query plan 命中目标 partial/composite index，不接受非预期 Seq Scan。

每次正式验收生成包含环境、样本、指标、query plan 和 commit SHA 的 evidence artifact；仓库 fixture 只用于合同测试，不能替代本次实测。

## 错误处理与安全边界

- 迁移遇到不合法 kind/status 或无法安全分类的旧行时必须显式失败，不静默丢弃。
- audit-only 行的 replay 请求返回稳定领域错误，不产生新消息。
- reset 脚本在目标表不存在、schema 不匹配或包含主数据表时拒绝 apply。
- Heavy runner 缺少显式数据库 URL、连接容量不足或目标不安全时在创建临时库前失败。
- 文档和零引用门禁失败直接阻止最终验收，不以代码测试通过代替。

## 测试与验收策略

实施按依赖顺序进行：数据库合同 → Service 迁移 → replay → 运维门禁 → 文档 → heavy gate → 全量验收。

每个任务使用失败测试锁定缺口，再做最小实现，并运行受影响领域回归。数据库和重放任务必须覆盖成功、拒绝、重复、冲突和并发边界；模块迁移必须包含 import/零引用 guardrail；运维脚本必须覆盖 dry-run 和 apply 目标集合。

最终验收至少包括：

- 默认快速测试、测试拓扑、Ruff、Bandit 和项目质量门禁。
- PostgreSQL migration round-trip。
- RuntimeInbox 完整处理链路和两个 crash window。
- 真实 benchmark 与 evidence artifact 校验。
- current docs 和 active code/scripts 的旧入口零引用检查。

只有上述门禁全部取得当前 commit 的新证据后，才能把原计划对应 Task 恢复为 100%。

## 实施边界与提交策略

本轮在 `feature/runtime-inbox-single-source-of-truth` 分支和当前主目录继续，不新建 worktree。每个任务独立提交，修改符号前运行 GitNexus upstream impact，提交前运行 GitNexus detect changes，并显式 stage 当前任务文件。
