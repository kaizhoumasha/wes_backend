# `notify_pkg_binding` typed EFFECT 迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 将 `notify_pkg_binding` 硬切换为唯一 `wms.fulfillment.notify_pkg_binding@v1` typed EFFECT，并删除该 operation 的旧 Port、字符串路由、通用 endpoint、消费者、测试、文档和 inventory 引用。

**架构：** material-flow 只创建基于 `provider + package + pallet` 稳定业务 identity 的 `RuntimeIntent.system_capability`；operation-owned handler 在调用方事务内复用 T8 双账本写入口。外部 I/O、冻结 binding、lease/fencing、typed transport、callback reducer、UNKNOWN/reconciliation 与 crash recovery 均复用 T8a-g，callback 不直接修改业务状态。

**技术栈：** Python 3.13、Pydantic、SQLModel/SQLAlchemy async、PostgreSQL、Pytest、GitNexus。

## 全局约束

- API → Service → Repository → Database；不跨层直接访问。
- 所有项目命令使用 `uv run ...`，Shell 命令使用 RTK。
- 每个既有函数、类或方法修改前运行 GitNexus upstream impact。
- 严格 RED → GREEN；没有失败测试不得修改生产代码。
- 不保留 alias、delegate、fallback、旧数据迁移或双运行。
- 不迁移其它 fulfillment operation，不实施 T11、Jenkins 或 GitLab。
- 不提交用户已有的 `AGENTS.md`、`CLAUDE.md` 修改。

### Task 1：冻结 operation-owned OUTBOX_ASYNC 合同

**边界：**

- 新增 definition、effect contract、intent/effect adapter、handler、callback adapter 和 preparation service。
- definition 仅接受 typed request/admission，durable acceptance 不冒充远端完成。
- gateway 冻结 canonical payload 与 endpoint binding；重放只复用既有 intent/outbox。

- [x] 先写合同测试，覆盖 typed definition、稳定 identity、冻结 binding、双账本与 callback bridge。
- [x] 运行聚焦测试，确认缺少实现时失败。
- [x] 实现最小 operation-owned EFFECT，并重新生成 System Capability index。

### Task 2：硬切 material-flow 消费者

**边界：**

- 粗分机 runtime 产生唯一 typed SYSTEM_CAPABILITY，不再产生旧 EXTERNAL_REQUEST。
- preview 只暴露稳定 operation identity。
- 插件声明允许新 capability；其它 fulfillment operation 保持不变。

- [x] 先写消费者测试，覆盖 request replay 稳定性与旧 string effect 归零。
- [x] 修改 runtime、preview 与插件声明并运行聚焦测试。

### Task 3：callback reducer 时序与 PostgreSQL 证据

**边界：**

- callback-before-response、重复、迟到、矛盾与 timeout-success 全部进入唯一 reducer。
- terminal 单调；矛盾只追加 evidence/open case；UNKNOWN 可由成功 callback 闭合。
- PostgreSQL 验证同业务 identity 单 pair，endpoint rotation 只作用于新 intent。

- [x] 先写 reducer 时序、PostgreSQL integration 与 resilience 测试并确认 RED。
- [x] 仅补齐 operation 接线，不修改 T8 状态机语义。

### Task 4：删除旧链并令 inventory 归零

**边界：**

- 删除 family Port 中的料盘绑定方法、旧 result、catalog string、通用 `WMS_FULFILLMENT` target/config 和对应消费者。
- 更新活动测试/文档为 stable operation identity；T10 inventory 行全部移出活动清单。
- 保留 ADR 决策历史，不迁移其它 fulfillment operation。

- [x] 新增旧链归零门禁，删除所有 active legacy 引用且不加兼容层。
- [x] 更新 inventory guard 的 completed identity 与历史 ADR。
- [x] 运行全仓 `rg`、inventory 双向门禁和相关测试。

### Task 5：验证、detect changes、报告与提交

- [x] 运行测试拓扑、聚焦单测、Docker PostgreSQL integration/resilience 和完整 quality profile。
- [x] 暂存时排除 `AGENTS.md`、`CLAUDE.md`，运行 GitNexus detect changes。
- [x] 写 `.superpowers/sdd/task-T10-report.md`，使用中文 Conventional Commit 提交。
