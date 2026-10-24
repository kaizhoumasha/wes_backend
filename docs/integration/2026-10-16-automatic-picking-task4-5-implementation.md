# 自动拣料任务 4–5 实施记录

日期：2026-10-16。分支：`codex/automatic-picking-workflow`。任务 1–3 提交：`e558fe0b`。本文只记录软件实施，不构成整线或设备验收。

## 交付范围

独立 `automatic-picking` 包版本 `0.1.0`；显式 optional extra、部署定义/模型/运行关联及镜像 extra 选择，保持零插件、人工单装、自动单装与双插件隔离。基础层及 SDK 不导入业务插件，默认配置不启用自动线。

已确认路径包含 AUTO prepare 策略、按绑定位置生成原计划成员的货架进场意图、到位后的原成员 `inbound_batch`、冻结有序清单每段最多四箱、当前分段恢复、SCAN1 正常前进、SCAN2 实扫 Work 及 `work_plan` 三种结果。计划准入复用共享严格 DTO 和位置绑定校验，不增加无业务规则的成功占位 policy。

2026-10-16 用户确认自动线扫码沿用现有方向格式：`A`＋9 位 ASCII 数字，SCAN1 后缀 `-B`、SCAN2 后缀 `-C`；不去空格、不接受 Unicode 数字或无后缀编码。编码不符只留证；可识别但不属于当前批次的箱进入 Evidence 对账，不请求工作计划或自动放行。首批期限、机械臂映射和 PkgID 来源仍未确认，不新增默认值或替代字段。

## 持久对象与恢复 owner

| 对象/状态 | 唯一恢复 owner | 出口与边界 |
| --- | --- | --- |
| PickingTask 普通 prepare/plan | 宿主既有准备、计划接收和激活入口 | 使用自动插件 prepare policy 与原计划来源；不增加 AUTO 专用 worker |
| Feed `REQUESTED` | 插件 batch driver | 基础 Confirmation 保存原响应；确定 READY→ALLOCATED，最终空清单→FEED_COMPLETE，原义务对账→RECONCILING |
| Feed `ALLOCATED/FEEDING` | 插件 batch driver | 引用原完整清单和本段 Transport；权威成功、结果发布及已知交接终位后确认本段，只有仍需后段时等待本段身份实扫；末段直接 FEED_COMPLETE。未知保留原身份；明确失败/拒绝转 RECONCILING |
| Feed `CANCELLED` | 已确定取消后的投料尾部终点 | 只停止未提交尾部；在途原身份先闭合。保留末段 Transport 供迟到扫码核对；扫码先到而结果未闭合/发布时按原 Evidence 延后重放，不建新请求 |
| Feed `FEED_COMPLETE` | 下游来源换面/离场切片的持久交接 | 末段物理交接成功并发布即成立，不等 SCAN 或整批拣料/回架完成；待准入成员仍归原扫码 Evidence owner。本轮不创建后续运动 |
| Transport outcome | 公共 outcome 发布 owner＋冻结版本插件接收器 | 按原 binding 与 outcome version 保存 TRANSPORT_RESULT 后由宿主更新 published_outcome_version；空成员 UNKNOWN/REJECTED/本地 FAILED 也可靠留证，不伪造到位或新动作 |
| FeedScan | 本插件扫码事务 | 保存本批本箱入线 Evidence、原 SCAN1 Command 与 Work 已准入标记；不复制 Bin 库存，不查询 CLOSED Work 作准入依据 |
| Work `PLAN_PENDING` | 插件 batch driver 恢复路由；FactProcessor 应用业务 | Work 与 task-owned 原请求同事务；按原 task/bin/scanned_at/operation 核对结果。共享 Confirmation 唯一 owner 仍为 PickingTask |
| Work 结果应用 | FactProcessor 的静态业务消费者 | Work 阶段与 Evidence 发布同事务；中断全部回滚；不进入单盘 FactBuilder |
| Work `WAIT` | 插件 batch driver | 按原结果持久完成时间＋retry_after_ms；到期只创建一个新 operation_id，沿用本次 SCAN2；未知请求不换身份 |
| Work `READY` | Work 持有授权，后续任务 6 消费 | 本轮只保存授权，不创建 Pickup/机械臂命令 |
| Work `EXIT_PENDING` | 后续任务 8 的点2交接入口 | 原响应明确 NO_WORK；本轮不伪造方向 Command 或物理流出完成 |
| `RECONCILING` | 既有对账流程 | 保留原请求、Evidence、成员及执行身份；不自动替换动作 |

两个持久恢复入口不竞争业务应用：batch driver 对 COMPLETED Work 只恢复 WorkLine 路由上下文；静态消费者负责结果应用与发布。WAIT 只有在前次结果已同事务发布后才可被周期扫描重问，因此旧 WAIT 不会被新请求覆盖后失去发布出口。

终态 Feed 立即退出投料推进扫描，不再由 batch 读取原 Confirmation。只有原结果已发布且实际已投料成员全部 `work_admitted` 后，才从扫码候选和插件工作量阻塞中退出；Work 后续恢复独立继续。完成标记在当前来源成员仍有效时保持为当前记录，用于原成员一次申请和后续来源交接，不提前归档为可清理历史。未交接的成员仍保留原清单及末段因果身份；不得提前删除其活动引用。回归验证删除已交接 Feed 的 COMPLETED Confirmation、清理 CLOSED Work 后，当前 Work 恢复及不同成员/新箱候选查询仍成立。

同一线的投箱/退箱 CTU 通道沿已有 Transport 事实串行；已发布的明确 REJECTED 不再占用通道，本 Feed 保留对账。该限制不扩展到独立货架或两臂。分配请求只依赖本来源架到位，不额外等待其他架或 CTU 批次；实际分段提交受当前 CTU 通道约束。两臂仅声明独立角色，当前没有取盘或 PUT 命令，不增加扫码台锁、平台容量或全线物料 FIFO。

## 最小建模与尚未实施的部分

新增三表：`automatic_picking_works` 保存真实工作轮次和当前授权；`automatic_picking_feeds` 保存原来源成员请求及分段游标；`automatic_picking_feed_scans` 保存当前成员入线与消费索引。内容继续由原 Evidence/Confirmation/Transport 持有，不复制完整清单或另建可靠队列。迁移为 `c106df25b129`，上游 `f72bd038e915`，只增加这三表。

G6 未给定业务期限、合法范围和配置合同，故首批超期、任务确认进度和 completion driver 留待确认后实施。`NO_WORK` 的实际点2放行、回程、换面/离场门禁、drain 及任务终态属于后续切片；真实取盘、扫码关联、设备映射及 PkgID 问题也未推进。任务 5 仅已确认子路径完成，不能作为生产整线启用依据。

## 验证与测试所有权

- 插件业务 FAST 与 PostgreSQL/真实 worker 验收位于 `workline_plugins/automatic-picking/tests/`，不加入核心默认 pytest、QUALITY 或 HEAVY selector。
- 核心 `tests/deployment/` 仅证明零/人工装配、静态选择、表筛选、缺包拒绝及基础制品入口；自动单装/双装且依赖自动包的用例移至插件 `tests/test_plugin_composition.py`。CI 继续仅安装 `manual-picking` extra，不为插件业务测试扩大安装集合；`tests/scripts/test_select_heavy_tests.py` 维护新增迁移的精确映射。
- 核心 HEAVY 仅执行 selector 给出的 manifest。三表业务语义由插件 PostgreSQL 测试负责；共享装配及迁移链使用既有核心 owner。
- 本轮完整核对为主 Agent 自审，未派生 Reviewer；GitNexus 全图提示流程截断，调用点与测试 owner 另以限定路径检索闭合，不把图谱输出称作完整证明。

最终快照验证：

| 验证 | 结果 |
| --- | --- |
| 显式插件完整套件（包含独立 PostgreSQL 与真实 worker） | 55 passed，0 skipped；其中 42 FAST、13 持久/接线用例，包含真实 Confirmation worker 与公共 Transport 结果发布链 |
| 完整 QUALITY | passed；默认 FAST 4279 passed、5 skipped，脚本集 428 passed；最终仅插件局部改动后刷新全仓 ruff 与插件套件，复用未受影响的核心证据；跳过项未计入插件或 HEAVY 通过证据 |
| selector `unstaged` 的核心 HEAVY | 12 文件、85 passed、0 skipped；本轮在仅人工插件安装环境重新执行 |
| 迁移 | 插件测试使用干净临时逻辑库 base→head；另验证 f72bd038e915→c106df25b129、downgrade→f72bd038e915→head |
| 边界和文档 | 插件所有权守卫、装配/缺包、selector 精确迁移列表、`git diff --check`、本地链接核对通过 |

本轮修订的 5 项闭环：

| 原 Review 问题 | 修订与回归证据 |
| --- | --- |
| Transport outcome 无接收器，published version 不推进 | 安装固定版本接收器，复用公共可靠发布与 Evidence；真实来源架结果发布后只申请一次 inbound，投箱结果也经过原发布链；未知/拒绝/本地失败空成员有具名回归 |
| 末段错误等待 SCAN，feed_complete 无出口 | 末段与取消尾部按已发布的确定物理结果退出，保留原末段身份；中间段仍等本段实扫，最多四箱且不越过未闭合段 |
| 取消、扫码与结果乱序造成永久对账 | 原 Transport 成员识别暂缓扫码；SCAN1/SCAN2 均用原 Evidence 延后恢复。真实 PG 验证取消后 SCAN2 先于结果及发布，不产生 Work，发布后缺 SCAN1 仍可准入；同批其他成员失败/位置未知不覆盖本箱已发布的确定成功事实，异常成员仍保持对账 |
| 核心测试误依赖可选自动包，CI 缺包 | 自动/双装测试归插件；核心在实际未安装 automatic_picking 的 manual-only 环境完成 QUALITY，不扩大 CI extras |
| 终态 Feed 恢复依赖已闭合 Confirmation，工作量不退出 | 投料扫描只选 REQUESTED/ALLOCATED/FEEDING；准入扫描及阻塞统计只保留尚未完成交接的 Feed。发布与成员交接分别设门禁，未交接不提前退出；取消前已实扫入线并交给 Work 的成员不因未提交段计数而残留阻塞；原 Confirmation 清理不影响已交接当前事实与 Work 恢复 |

同一轮完整自审同时核对原 5 项及最终变更。补到的明确 REJECTED 状态出口、通道释放、空成员结果接收、部分成员异常下的本箱确定事实也已进入回归；没有增加基础业务状态、通用抽取、可靠队列或重复持久 owner。代码/测试证据不等于服务器部署或真实设备验收。

任务 4–5 的已确认实现及本轮修订在当前工作分支提交保存；未推送或部署。首批期限等未决项继续保留；原服务器任务 1–3 的部署证据不作为此插件的运行证明。
