# 前后端开发与发布流程优化设计

## 1. 背景

当前项目已经建立风险分级、FAST/QUALITY/HEAVY、不可变镜像、方向性合同兼容、FAST/FULL 发布和维护态保护。
下一阶段的问题不是缺少门禁，而是同一证据被重复执行、并行 Agent 共享写现场、前后端规则不一致，以及发布前缺少对未决物理执行的统一判断。

本设计只优化流程所有权和执行时机，不降低合并、发布或现场验收门槛，也不引入新的流程平台、兼容层或通用编排框架。

## 2. 目标

1. 按路径冲突和运行环境选择 direct 或 worktree；非重叠小变更不为形式隔离。
2. 开发循环只运行最小充分验证，完整 QUALITY 与 HEAVY 在最终快照各有一个权威 owner。
3. 前后端采用一致的风险、授权、Skill 和 worktree 内核规则，但不复制后端全文。
4. 删除前端重复类型检查和后端失效 HEAVY 映射。
5. 后端 FULL 发布在维护前先做在线预检，并在关闭新增入口后取得可授权停进程的稳定静默证据。
6. 使用现有 Jenkins、JUnit、RTK 和 release evidence 衡量改进，不建设新仪表盘。

## 3. 非目标

- 不改变 GitNexus 的风险语义，不允许仅凭 `LOW` 自动免除验证。
- 不删除 CI 完整 QUALITY，不放松 HEAVY selector 的 fail-closed。
- 不把本地 Commit 当作发布证书，也不允许绕过受保护分支。
- 不对 DeviceCommand、TransportTask、RuntimeInbox 或 callback 使用双版本 worker 蓝绿切换。
- 不把供应商一致性、HTTP ACK、设备结果、物理动作和业务验收合并成一个状态。
- 不新增中央 Agent 平台、测试注册中心或长期运行的流程数据库。

## 4. 核心决策

### 4.1 默认直接工作，出现真实冲突才隔离

默认在当前 checkout 直接工作，不要求 Agent 声明新的流程模式。dirty checkout 是必须保护的现场，不是自动创建 worktree 的理由；是否隔离只由目标路径、工具写入边界和运行环境决定。

直接工作的任务必须满足：

- 写前冻结 `git status --short`，必要时记录目标文件和无关 dirty 文件指纹；
- 只运行可限定到目标路径的编辑、格式和验证命令；
- Commit 已授权时使用 `git add -- <target>`，禁止 `git add .` 和 `git add -A`；
- 提交前检查 `git diff --cached --name-only`、目标 cached diff 和 `git diff --cached --check`；
- 一旦发现与其它活动任务路径重叠、生成物扩散或当前必选门禁无法可靠隔离，再协调 owner 或创建独立 worktree。

以下任一条件成立时才使用 worktree：目标路径与其它活动任务的修改重叠；多个任务修改同一生成物；当前交付必需的编辑、格式化或验证工具无法限制写入范围；需要独立分支、PR 或长线现场；需要独立依赖、容器、数据库、端口或进程。

路径重叠先判断所有权，不能机械隔离：属于当前任务或用户明确要求保留并继续的修改时，在当前 checkout 手术式续作；属于其它活动任务时先协调 owner，确需并行才隔离；所有权不明且无法从当前会话、状态或提交记录确认时暂停写入。不得从干净基线创建 worktree 后遗漏当前任务依赖的 dirty 内容。若不可限定的宽验证不是当前交付必选门禁，而由候选 Commit 的 CI 权威执行，则保留本地聚焦证据即可，不为获得一份宽验证日志创建 worktree。

行数不是唯一判断依据，但“一行纯文档删除、目标路径不重叠、无运行时行为”必须直接修改，不创建 worktree、不初始化环境、不运行 pytest。

流程分类以“当前内聚变更切片”为单位，不继承整段会话或后续发布任务的最高流程成本。一个包含 Review、Commit 或 Deploy 的大任务，其中独立的一行文档修正仍按
纯文档规则执行；后续动作到达各自授权边界时再单独判定。

### 4.2 风险决定开发验证，最终边界决定权威门禁

- 大型/高风险变更继续采用 RED → DEV → GREEN。
- 小型/低风险变更优先调整现有测试或采用类型检查、构建、lint、定向烟雾等替代验证。
- 纯人类文档、注释、规则和 Skill 永远不走代码式 RED/DEV/GREEN。
- 本地开发循环只运行受影响行为的聚焦验证。
- 后端 CI 对候选 Commit 执行完整 `quality`；HEAVY 只由 selector 对同一候选快照执行一次。
- 在 GitHub/GitLab 合并保护能够强制上述 CI 结果之前，不得把后端 pre-commit 从完整 `quality` 切换为轻量 `commit` profile。
- 验证证据记录命令、结果和快照指纹；收益测量和最终验收复用有效证据，不为统计或报告重跑同一命令。
- `--scope unstaged` 覆盖整个 checkout，而不是当前任务。存在无关机器配置或生产代码差异时，不得把其 manifest 描述为任务级证据；只有该验证是当前交付必选且无法限定路径时才使用 worktree，否则交给候选 Commit 的 CI。

### 4.3 暂缓轻量 Commit profile

2026-08-26 的只读核验显示 GitHub `develop` 尚未启用 branch protection，而后端 Jenkins 由 GitLab MR/Push 驱动。当前无法证明完整 QUALITY 和 HEAVY 能在权威代码进入 `develop` 前阻断合并，因此继续保留 pre-commit 的完整 `quality` fallback。

只有权威合并入口强制以下条件后，才单独设计轻量 Commit profile：禁止直接推送；PR/MR 必须通过完整 QUALITY；selector HEAVY 必须通过或明确 `NONE`；候选 Commit 与 CI 快照一致。在此前提成立前，不编写 profile 代码和测试。

文档-only 和 release-metadata 的既有专用分支保持不变。`.txt` 快捷分类必须具备目录语义：`docs/**/*.txt` 可视为人类文档，`src/`、`scripts/`、`tests/` 和依赖清单中的 `.txt` 不得绕过完整门禁。

### 4.4 前端流程内核与测试命令收敛

前端 `AGENTS.md` 只增加以下跨仓内核，不复制后端专项规则：

- 风险分类和最小验证；
- 纯文档不走 TDD；
- 最小 Skill 集合；
- 按路径冲突和独立运行环境选择 direct 或 worktree；
- Commit、Push、PR、Merge、Deploy 独立授权；
- 浏览器 QA 只服务真实交互、认证、SSE、重连和页面行为。

`pnpm lint` 继续作为包含 `type:check` 的唯一静态门禁；`pnpm check` 只转发 `pnpm lint`，不得再次执行 `type:check`。

Skill 也按当前切片选择：非重叠纯文档小修改不调用 brainstorming、writing-plans、worktree、浏览器 QA 或完整 Review Skill；计划实施不因任务列表较长就自动启用
Subagent。只有独立子任务和隔离条件同时成立时才使用相应能力。

### 4.5 后端规则减重

后端 `AGENTS.md` 继续遵守现有不超过 24 KiB 的目标，不为任意更小数字进行全文重写。只手术式删除确有重复且可由直接引用承接的内容，保留：

1. 授权与 dirty work；
2. 分层和系统所有权；
3. 风险/TDD 边界；
4. FAST/QUALITY/HEAVY 所有权；
5. Git/worktree/GitNexus；
6. 验证证据与完成状态。

命令目录、测试拓扑、迁移细节和部署 Runbook 继续引用现有专项文档，不创建同义文档。

### 4.6 HEAVY 只清理错误所有权

- 删除已经不存在且不再参与当前差异检测的精确 source mapping。
- 不修改未知候选 fail-closed 行为。
- 不批量声明 `heavy_tests = []`。
- 先重新测量 selector fan-out、环境启动、迁移和 pytest 时间，再决定是否优化构建或数据库缓存。
- 当前 runner 已存在迁移后数据库模板，不重复实现同义模板能力。

### 4.7 发布运行静默门禁

仅 `BACKEND FULL` 和 `BOTH FULL` 执行两阶段运行静默检查；`FRONTEND` 不查询或改变业务执行状态。

检查只读取四个执行域的五张既有权威表：

| 权威表 | `WAIT_DRAIN` | `BLOCK` |
| --- | --- | --- |
| DeviceCommand | `PENDING`、`DISPATCHING`、`ACKNOWLEDGED` | `RECONCILING` |
| TransportTask | `PENDING`、`ACCEPTED` | `RECONCILING` |
| RuntimeIntentLog | `PROPOSED`、`ACCEPTED` | `UNKNOWN`、`RECONCILING` |
| SystemOutbox | `NEW`、`DISPATCHING`、`RETRY_WAIT` | `UNKNOWN` |
| RuntimeInbox | `RECEIVED`、`PROCESSING`；仅 `next_retry_at IS NOT NULL AND attempt_count < max_retries` 的 `FAILED` | `next_retry_at IS NULL OR attempt_count >= max_retries` 的 `FAILED`；`DEAD_LETTER` 仅作为证据报告 |

判定规则：

- 任一 `BLOCK` 计数大于零：退出码 `2`，状态 `BLOCK`；不存在绕过参数。
- 无 `BLOCK` 但任一 `WAIT_DRAIN` 计数大于零：退出码 `3`，状态 `WAIT_DRAIN`。
- 两类计数均为零：退出码 `0`，状态 `READY`。
- 查询失败、表缺失或任一表出现完整生命周期集合之外的状态：退出码 `1`，fail closed，不得解释为 `READY`。
- 输出只包含状态、分类计数、汇总和生成时间，不输出版本兼容字段、payload、密钥或业务明细。

一次在线查询不能授权停机，因为查询后仍可能有新 EVENT、命令或定时任务进入。FULL 发布必须执行两阶段门禁：

```text
ONLINE ──在线预检──> READY ──关闭 Nginx/API/Beat admission──> QUIESCING
   │                    │                                      │
   └─非 READY：在线终止  └─不停止执行 worker                    ├─连续两次 READY：停止执行进程并迁移
                                                               └─超时/异常/BLOCK：保持维护态并终止
```

- 在线预检发生在任何 live runtime、maintenance 或数据库 mutation 之前；非 `READY` 保持服务在线并终止发布。
- 进入维护态后先停止 Nginx，再给 API 最多 30 秒优雅结束在途请求并停止 API，随后停止 `celery_beat`；必须验证 Nginx 与 API 宿主 listener 均已关闭。Redis 和执行 worker 继续运行用于自然排空。
- 当前 Compose 还发布 Redis 宿主端口。复用审计必须证明它没有合同内外部任务生产者；只有证明所有合法入队路径均已关闭且入队前先写入上述五张表，才能把五表快照作为完整静默依据。
- API 关闭后，worker 只能收敛不依赖新 HTTP callback 的已落账内部工作；门禁不得承诺所有 `WAIT_DRAIN` 都能在维护态自然排空。60 秒后仍非 `READY` 必须失败并保持入口关闭。
- 权威复核使用同一 PostgreSQL statement snapshot 返回全部分类计数和未知状态计数；Service 以 10 秒取消边界约束单次查询，连续两次 `READY` 且间隔 2 秒才算稳定静默，整体等待最多 60 秒。
- 稳定静默前不得停止执行 worker、切换部署源、备份或迁移。维护态内出现 `BLOCK`、查询失败或等待超时，沿用既有失败路径保持外部入口关闭，不自动恢复、重试业务或重发物理指令。
- 若复用审计发现仍有未被五张表覆盖的 broker-only 工作、API 之外的内部直连入口或其它生产者，必须先更新静默合同，不得带着已知盲区实施门禁。

该门禁只判断是否适合停止执行进程，不自动取消、重试、修复或对账任何业务记录。

## 5. 交付拆分

由于开发门禁与现场运行静默属于两个可独立审批、独立回滚的子系统，分别实施：

1. `2026-08-26-development-workflow-efficiency.md`：前后端 Agent、Git、QUALITY 和 HEAVY 流程。
2. `2026-08-26-release-operational-readiness.md`：后端 FULL 发布前的只读运行静默门禁。

第一份计划不依赖第二份；第二份不得反向改变日常开发测试策略。

## 6. 验收标准

- 非重叠的一行纯文档修改即使位于 dirty checkout，也直接修改并精确路径暂存，不创建 worktree、不运行 pytest。
- 只有与其它活动任务路径重叠、共享生成物、当前必选写入/验证范围不可控或独立运行环境的任务使用 worktree；当前任务自身 dirty 修改不得被隔离后遗漏。
- 前端 `pnpm check` 的执行轨迹只出现一次 `vue-tsc --noEmit`。
- GitHub `develop` 未保护期间不实施轻量 Commit profile；未来只有权威合并入口强制完整 QUALITY 和 selector HEAVY 后才重新立项。
- 后端 `AGENTS.md` 不超过 24 KiB，且本次只修改与流程优化直接相关的规则。
- `docs/**/*.txt` 可以走文档门禁，执行目录和依赖清单中的 `.txt` 必须进入完整质量门禁。
- HEAVY 失效精确 mapping 被删除，selector 回归保持绿色和 fail-closed。
- `BACKEND/BOTH FULL` 先在线预检，再优雅停止 Nginx、API 和 Beat admission 并取得连续两个稳定 `READY`；稳定静默前不得停止执行 worker 或开始 migration。
- `RuntimeInbox.FAILED` 按是否仍可自然重试区分 `WAIT_DRAIN` 与 `BLOCK`，不得把永久卡死记录当作可排空负载。
- 五张表的分类计数和未知状态计数来自同一 PostgreSQL statement snapshot，单次查询和整体静默等待均有硬超时。
- 发布静默门禁不修改数据库，不重试命令，不改变业务状态。
