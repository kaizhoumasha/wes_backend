# 货架取消重试与进场容量调度优化 SPEC

状态：ReviewRequired；规则提案与代码调查已完成，生产优化未实施。

日期：2026-09-25。调查基线：`feature/workline-debug-instruction-drafts` / `1d01a3d9`。
交付范围：根据本轮讨论形成规则、调查当前实现、定义最小优化范围和验收；不包含代码修改、提交、部署或现场验收。

## 1. 目标与规则真源

同一 PickingTask 收到 A、B、C、D、E，capacity=3。B 的进场取消不阻塞 D、E 的首次呼叫；B 保留有效需求并在首次候选用尽、退避到期且容量允许时重新呼叫。退场取消由未完成离场义务驱动独立重试，不参与进场排序。

遵守 SRS 第 0 章的物理事实、业务所有权、可靠恢复与历史可清理原则。本文不会把方案标成当前行为。落地时同步 SRS、Transport 合同和出库业务合同，禁止保留互相矛盾的终态解释。

术语：

- 执行实例：一个 Transport，明确终态后不可重新打开；新的业务尝试使用新身份。
- 进场生命周期：同一物理货架对某目标点的一次占窗过程，可关联多个 revision/face/member。
- 首次候选：当前有效、已应用计划范围内、尚未形成首次进场绑定，且满足现有业务准入条件的货架。已有占窗同架复用不算新的物理候选。
- 重试候选：原实例明确终止、业务依据仍有效、目标尚未满足的后继尝试。
- 离场义务：由已冻结 WMS 离场决定和对应货架因果关联形成的未完成动作依据；不是 PickingTask 状态或当前 drain 指针。

## 2. 有效规则

R2–R4 决策流概览（不含退场，退场按 R5 独立判断）：

```
                    ┌───────────────────────┐
                    │  RCS/WMS CANCELLED     │
                    │  (权威 final_position) │
                    └──────────┬─────────────┘
                               │
                    ┌──────────▼───────────┐
                    │ 终位 == 目标点？       │
                    └────┬─────────────┬────┘
                     是  │             │ 否
              ┌──────────▼──┐    ┌─────▼────────┐
              │ 保留原名额    │    │ 释放名额(R2)  │
              │(同架复用)     │    │              │
              └──────┬───────┘    └──────┬───────┘
                     │                   │
                     ▼                   ▼
          ┌────────────────────────────────────────┐
          │ 需要新名额时的原子准入入口 (R6)            │
          │  RackInboundWindowService.admit         │
          └──────────────┬─────────────────────────┘
                          │
              ┌───────────▼────────────┐
              │ 有可准入首次候选？(R3)   │
              └───┬─────────────────┬──┘
              是  │                 │ 否
        ┌─────────▼──────┐   ┌──────▼────────────────┐
        │ 首次候选先补位    │   │ 退避到期的重试候选补位   │
        │(D/E 先于 B)      │   │ 仅事件驱动，无独立定时器 │
        └─────────────────┘   │ (R6，无事件不保证实时)   │
                               └──────┬─────────────────┘
                                      │
                            ┌─────────▼──────────────┐
                            │ 退避 1/2/4/8/16/32/60s   │
                            │ 无固定次数上限(R4)        │
                            │ 需配套等待时长可观测性     │
                            └─────────────────────────┘

退料架（drain）进场：即使与来源架共享同一目标点，也不参与本图的首次候选优先级仲裁（见 R3）。
```

### R1. 执行结果与业务需求分离

`CANCELLED` 只终止当前 Transport 实例，内部保持 `FAILED + RCS_TASK_CANCELLED`。回调继续要求权威 `RACK_POSITION final_position`，禁止擅自扩大 wire DTO。

仅原实例明确终态后才允许创建新的业务尝试。超时、`DELIVERY_UNKNOWN`、`UNKNOWN/RECONCILING` 保留原身份、冻结正文及证据，不能另建等价搬运。重放同一结果不得重复创建后继。

### R2. 容量定义与释放

capacity 限制尚未按合同释放的进场生命周期数量，不表示物理区域实时占用或 RCS queue slot。

| 事实 | 名额处理 |
| --- | --- |
| 创建进场 Transport | 占一个名额 |
| 进场成功到位 | 继续占用 |
| 进场明确未接纳 | 释放 |
| 进场明确失败/取消，权威终位不等于冻结目标点 | 释放 |
| 进场明确失败/取消，终位仍等于目标点 | 保留；有业务重试需求时复用，不重复占额 |
| 发送结果或位置未知 | 保留原身份和原名额 |
| 业务完成且对应离场 Transport 获得 ACCEPTED | 释放；后续离场取消不自动重新占窗 |

不新增目标点与物理排队位置的映射。不因取消恢复原 RCS queue slot。

### R3. 首次呼叫优先，仅约束需要新名额的进场

排序作用域为同一 WorkLine、同一 PickingTask、同一目标点的来源架有效进场候选。不跨任务重排 WMS 业务优先级，不新增全局队列。退料架（drain）进场即使与来源架共享同一目标点的容量窗口，也不参与本条的首次候选优先级仲裁：`_submit_drain_racks` 按现有 drain 推进逻辑独立提交，不因本次规则变更而改变、也不纳入本条排序（评审中经 Codex 复核发现两者实际共享目标点，2026-09-26 已确认维持现状并在此明确边界，避免与"同一目标点独立执行"字面表述冲突）。

每次需要申请新名额时，先尝试首次候选；没有可准入首次候选，再尝试退避到期的重试候选。首次候选沿用 `plan_revision, member.id` 的稳定顺序，按已有同架生命周期语义去重。等待退避的 B 不挡住 D/E。

首次候选若因已有对象生命周期而暂不可准入，不应阻塞其他独立候选。原名额仍保留的同架重试不与首次候选争新名额，不受本条延后规则阻塞。

同一 task 后续已应用 revision 中的新首次候选也适用首次优先。因此，本方案不承诺在持续追加新需求时对旧重试提供有界等待；不引入配额轮转、老化优先级或跨任务公平算法。此取舍必须在评审中显式认可。

评审结论（2026-09-26）：接受该取舍，不引入公平算法；最小实现须新增重试候选等待时长的可观测性（日志/指标，超阈值告警），用于事后发现"隐性饿死"场景，不改变排序逻辑本身。

多个可重试候选沿用原成员稳定顺序；不得按异步 worker 抢锁先后决定业务优先级。已有不同 face/revision 的成员身份不得合并为仅 rack_id；物理窗口复用不消除业务身份。

### R4. 进场重试资格与退避

每次尝试前重新核对原成员未取消、冻结业务身份仍匹配、权威目标事实未满足；不能用父 PickingTask 的 `EXECUTION_COMPLETED` 代替成员取消。一个成员取消不撤销同架其他有效成员的需求。

沿用现有 1、2、4、8、16、32、60 秒、上限 60 秒的退避，无固定次数耗尽即放弃。仅创建了新的业务尝试才推进尝试编号；单纯容量不足或首次候选优先不算执行失败，不增加退避级数。等待容量不重启已过去的退避时间。

停止条件是对应需求失效、权威目标已满足，或进入无法安全决定动作的对账状态。后者保留未完成义务，不伪装成功/取消。

本次新增确定排序针对 RACK_MOVE 进场。RACK_ROTATE 不竞争新进场名额；已有旋转重试保持原合同，不套入首次呼叫排序。BIN 搬运不在本次范围。

### R5. 退场取消独立重试

来源架、退料架、转运架和 drain 货架的退场均按原未完成离场义务判断。收到明确 CANCELLED，义务有效且目标未满足时，独立退避并创建新 Transport，不申请进场 capacity、不等待 D/E、不抢回已经释放的进场名额。

PickingTask 完成、原进场成员取消或 current drain 切换，均不能单独成为放弃已冻结离场义务的理由。停止必须有匹配的目标成功事实，或明确的权威撤销/替代依据；找不到父对象不等于义务不存在。

重试前验证冻结 WMS READY 决定、目标、货架和因果身份仍适用。当前退场以 RACK 引用为 source，可保留有效冻结目标并让 WMS/RCS 解析货架位置；不得从旧投影猜当前位置。ZONE 目标也必须有匹配该义务的权威成功事实，不能只检查精确点目标。

评审发现（Codex 复核，2026-09-26 已核实）：`scan_flow.py` 当前只在目标 `kind == RACK_POSITION` 时判定成功，没有 ZONE 分支；第 6 节验证矩阵承诺覆盖 ZONE 的测试因此暂无可实现的断言标准，尤其是货架到位后又发生移动的情况。实施前必须先定义 ZONE 目标"权威成功事实已满足"的具体判定规则（例如：以哪个成功 Transport、哪个终位投影、按什么优先级关联原离场义务；货架移动后是否失效），再据此补齐 §14.1/§14.2 引用的验证矩阵行，不能带着未定义的判定标准进入实施。

若原 READY 决定不再适用，应使用现有 departure_decide 合同重新求值；当前面向等必需权威事实不足时进入对账并暴露缺失项，不编造面向、不新增未批准 API。普通 FAILED 的面向恢复缺口不因本 SPEC 自动解决。

本次不把所有退场 FAILED 都升级为无限自动重试。明确 REJECTED 保持现有重新询问 departure_decide 的分支；其他失败按其合同分类。CANCELLED 的定时重试不得混入普通失败的未知恢复分支。

### R6. 原子性与恢复 owner

所有需要新名额的进场继续通过 `ReliableRackTransportCreator.create_windowed_inbound → RackInboundWindowService.admit`；同一目标点行锁覆盖计数、准入、Transport 与 binding 登记。

候选顺序由插件在现有 WorkLine 业务串行边界内统一判断，然后调用该入口；不能一条路径检查首次候选后解锁，另一条路径直接创建重试。基础窗口不查询 plan_delta 或内置首次优先策略。

结果终态与名额释放同事务提交，提交后唤醒补位；释放和后续申请不是一个大事务。准入读到未提交释放最多暂缓，不能超额。真实 PostgreSQL 并发测试仍须证明上述边界，Mock 通过不能替代。

重试下一 owner 为原冻结插件上下文对应的持久 Evidence 决策处理；退避时间复用 `decision_next_attempt_at`，不新增定时器表。首次补位与重试补位均复用现有业务推进：`decision_next_attempt_at` 到期由既有 Celery beat 任务 `process-execution-facts-batch`（每 10 秒，见 `src/celery_app/config.py`）驱动 `FactProcessor.process_batch → InboundEvidenceRepository.claim_decision_batch` 重新捞取并调用 `ManualPickingScanFlow._retry_terminal_rack`，不依赖新的外部事件；ECS scan/callback 事件到达时也会走同一决策路径提前触发。

评审纠正（2026-09-27）：本文此前（2026-09-26 评审）曾错误声称"仓库当前没有独立的后台定时扫描器,仅事件驱动",并据此在上文 R3 增加了等待时长可观测性要求。经复核确认该判断有误：`process-execution-facts-batch` 是真实存在的 10 秒周期 Celery beat 任务,`claim_decision_batch` 的查询条件明确包含 `decision_next_attempt_at <= now`,退避到期后最多 10 秒内会被重新评估,不需要等待偶然事件。R3 的等待时长可观测性要求本身仍保留（持续追加新首次候选场景下,10 秒轮询间隔不改变"无有界公平保证"的取舍,仍值得监控),但不再以"无周期扫描"为理由。进程崩溃、通知丢失、重复 Evidence 后仍须能重新发现候选且至多创建一个后继。

未完成义务依赖的原 Evidence/Binding/Transport 必须保留到义务闭合；闭合后不得以这些历史记录作为未来新业务的前提。

## 3. 场景验收基准

前提：A～E 无严格业务先后依赖；B 取消的终位在目标点外。

| 事件 | 窗口成员 | 首次候选 | 重试候选 |
| --- | --- | --- | --- |
| 初始 | A/B/C | D/E | 无 |
| B 取消并释放 | A/C/D | E | B |
| C 释放名额 | A/D/E | 无 | B |
| A 释放，B 退避到期 | D/E/B | 无 | 无 |
| B 再取消 | D/E | 无 | B |
| B 退避到期且需求有效 | D/E/B | 无 | 无 |

“释放”不是进场 SUCCEEDED，而是 R2 的释放事件。B 有空位且退避到期就可以重试，无需等待另一货架再次完成。该表不规定 RCS 实际进位顺序。

## 4. 当前代码调查与根因

以下定位基于调查基线；生产代码本轮未修改。

| 位置 | 当前事实 | 与目标差距 |
| --- | --- | --- |
| `src/app/execution/services/rack_inbound_window.py:41` `admit` | 目标点锁后计数和创建；同架目标点失败允许复用原名额 | 已有原子准入，不重建容量机制 |
| 同文件 `release_unarrived_terminal` | 有界检查终态与终位后释放 | 已符合 R2；释放不是业务候选排序 |
| `workline_plugins/manual-picking/src/manual_picking/application/batch_driver.py` `_submit_source_racks` | 已应用有效计划减去已有 binding，遍历首次候选 | 没有与结果重试统一选择 |
| `scan_flow.py:345` `_retry_terminal_rack` | 退避后直接申请容量，不检查 D/E 首次候选 | 空位可由重试先取得，不能保证 D→E→B |
| 同函数 PickingTask 分支 | 进场检查有效成员；退场主要检查父对象存在 | 缺少独立离场义务的有效性判断 |
| 同函数 drain 分支 | 必须存在 current drain 且 evidence_id 等于原 binding | current drain 变化可将未完成原离场重试 IGNORED |
| 同函数目标判断 | 仅 RACK_POSITION 目标检查成功 Transport + 位置投影 | ZONE 退场未执行等价目标满足判断 |
| 同函数 RACK_MOVE 构造 | 直接复用原 source/target/template，经 generic create 重试退场 | 未显式证明冻结离场决定仍适用；须核对后继与原义务关联 |
| `batch_driver.py` `_departure_rejected` | 仅识别 REJECTED，重新询问 departure_decide | 不能把它当作 CANCELLED 恢复能力 |
| `src/app/execution/services/fact_processor.py` | DEFERRED 持久化下一决策时间、领取和事务应用 | 复用可靠恢复能力，不另建重试 worker |

根因：基础容量原子性解决名额一致性，但插件补位与重试是独立决策入口；通用货架重试把不同生命周期依据放进同一分支，进场成员、current drain 和独立退场义务未完全分开。

合同冲突：`docs/contracts/wms-outbound-picking-task-integration-requirements.md` §14.1 的 `REJECTED | FAILED` 表和 §14.2 仍笼统规定结束业务明细、由 WMS 创建新 PickingTask；Transport 合同 T1 及当前实现允许有效成员的货架取消/失败进场重试。必须按货架进场、货架退场、BIN 分别限定语义，不能继续沿用无条件总括句，也不扩大本次对 BIN 的授权。

## 5. 最小实现方案

1. 插件内统一“本次优先尝试哪些首次候选”的判断及下发路径，供 BatchDriver 的正常补位和 ScanFlow 的进场结果重试共用。复用当前计划查询、binding 去重、目标映射和 WorkLine 锁；不建立新的队列表。重试在同一业务串行边界内先让可准入首次候选使用空位，再尝试自身。
2. 在重试逻辑中按进场、退场、旋转分清资格。退场依赖冻结离场决定及原执行关联，不依赖 current drain 的存续或父任务状态。保留现有 correlation 唯一身份；明确重复旧结果不能绕开已有后继再创建一次。
3. 先用现有 Evidence、TransportDecisionBinding、departure confirmation 和 Transport 请求/结果完成义务关联。现有字段能满足则不加表/字段/状态；若某角色不能唯一定位原 READY 决定，实施前列出具体缺失关系并评审最小持久化补充，不用猜测或遍历已闭合历史替代。
4. 复用持久退避及后台扫描。新增候选等待不得把 Evidence 提前记 APPLIED 后失去下一 owner；成功创建后继才完成当前重试决策，或由另一个已持久化 owner 明确接手。
5. 修订三份长期合同的角色范围、优先策略、离场义务及恢复边界。本文保留为实施依据，不形成另一份长期 wire 真源。

优先检查范围：`batch_driver.py`、`scan_flow.py`、`plan_delta_repository.py` 的候选查询、`reliable_rack_transport.py` 的身份绑定，以及既有 departure reader/owner。基础窗口与数据库 schema 默认不改；仅发现可复现缺陷时调整。执行前必须完成 GitNexus upstream impact 和测试消费者清单，不以本 SPEC 的路径列表代替符号影响分析。

## 6. 验证矩阵与所有权

| 验收 | 主要 owner / 入口 |
| --- | --- |
| A～E 固定场景，任意 worker 调度下 D/E 优先于 B | 插件 `tests/test_source_progression.py` + `test_scan_flow.py`，一个组合行为主要 owner |
| 退料架（drain）不受 R3 首次候选优先级仲裁、与来源架共享名额时不因本次排序变更回归；同架跨 revision/face 不重复占窗 | 插件 `test_return_rack_progression.py`；基础复用不复制完整插件场景 |
| 退避未到期不创建；等待容量不递增尝试；满窗后恢复 | 插件 `test_scan_flow.py` |
| 原成员取消停止进场；父任务完成但成员有效仍可重试 | 插件 `test_scan_flow.py` |
| current drain 切换/消失后原离场义务仍可恢复 | 插件退场恢复测试，必须包含 taskless drain |
| 来源/退料/转运/drain 退场：CANCELLED 后退避，目标满足停止 | 插件退场恢复测试，覆盖 RACK_POSITION 与 ZONE |
| 退场无 capacity 申请、不恢复已释放名额，不取消已补发 D | 插件断言调用方向 + 基础窗口测试 |
| REJECTED 重新求值、普通 FAILED 缺少当前面向不猜测 | 插件 departure 场景；保留合同未闭合边界 |
| 同一取消结果重放、不同消息同事实、崩溃后恢复至多一个后继 | 插件身份测试 + 既有基础幂等/事务 owner |
| D 补位与 B 重试并发；释放未提交/回滚；创建失败回滚 | 独占 PostgreSQL，两会话验证基础窗口，插件验证优先级 |
| 两个重试候选同时退避到期、同时竞争同一释放名额；按原成员稳定顺序仅一方创建、另一方仍等待 | 独占 PostgreSQL，两会话验证基础窗口（评审新增，2026-09-26：Mock/推理不能替代真实并发） |
| 未知结果不换身份、不释放；迟到事实不覆盖较新因果投影 | 复用 Transport 现有 owner，新增断言仅覆盖本次差异 |
| 可清理闭合历史后新计划继续；未闭合义务仍能扫描恢复 | 插件恢复集成测试 |

基础 FAST owner：`tests/runtime/execution/test_rack_inbound_window.py`。当前窗口 HEAVY mapping 指向 `tests/integration/wms_adapter/outbound_picking/test_plan_delta_postgresql.py`；现有持久化测试不自动等于并发证明。新增真实并发资产按基础所有权定位并更新精确 mapping。

`reliable_rack_transport.py` 若修改，按 `docs/architecture/heavy-test-impact.toml` 选择现有 execution/transport HEAVY；插件测试仍独立运行，不纳入核心 QUALITY/默认 pytest。生产实施按高风险跨路径行为执行 RED→GREEN；无需为本文正文编写 pytest。若改变 worker/定时 wiring，再补真实 worker 证据；只改 handler 不预支全套部署验证。

## 7. 本轮证据与限制

执行：

```sh
uv run pytest tests/runtime/execution/test_rack_inbound_window.py workline_plugins/manual-picking/tests/test_scan_flow.py -k 'window or cancelled_rack_follows_business_basis_and_goal_fact' -q -o addopts=''
```

结果：23 passed，89 deselected，0.41s。证明当前窗口和现有进场重试测试通过，不证明目标优先级、退场闭环或真实数据库并发。

另用无数据库的 Python/AsyncMock 探针直接调用当前 `_retry_terminal_rack`，验证：

- `DRAIN_RACK_OUT_STEP`、原 binding 有效、current drain 为 None → 返回 `IGNORED`，没有创建重试。
- `MANUAL_PICKING_SOURCE_RACK_OUT`、父 task 存在且 COMPLETED、目标为 ZONE、退避已登记 → 返回 `RETRY_CREATED`，直接调用 generic create；该路径不读取独立离场义务或 ZONE 目标成功事实。

探针只证明代码分支，未伪造现场故障；未写生产代码或持久数据。对应输入须在实施阶段转为永久回归测试。

已查看相关文件最近提交：窗口引入 `f37da4e9`，当前最新相关恢复变更包含 `4e385874`。不能仅凭提交标题断言具体回归来源。

未运行真实 PostgreSQL/Redis/worker、QUALITY、HEAVY、迁移或现场验收。当前没有新增 wire、schema 或依赖；若实施发现必需持久化关系缺失，按第 5 节评审，不先建通用义务框架。

工程评审复核发现（2026-09-26）：截止评审时，工作区在 `docs/architecture/SRS.md`、`docs/contracts/transport-fulfillment-contract.md`、`workline_plugins/manual-picking/src/manual_picking/application/batch_flow.py` 存在未提交改动。前两处内容与本 SPEC 的 R2/R6 完全一致（属于本轮调查的自然产物）；后者是修复既有类型不一致（`has_unclosed_action_for_face` 的 `task_id`/`plan_revision` 早已在 `batch_driver.py:314` 以 `None, None` 调用，Protocol 类型声明一直未同步）。三处均已在此说明范围，不与"本轮未修改生产代码"的定位冲突；提交前仍需按 `AGENTS.md` 完成 GitNexus detect-changes 确认范围。

## 8. 完成与评审边界

本 SPEC 完成标准：规则明确、当前差距有代码/探针证据、最小变更与测试 owner 可定位。实现完成标准另含：所有验收通过、旧直接竞争入口退出、三份合同无冲突、未闭合状态有唯一恢复 owner。

评审需明确接受 R3 的作用域及持续追加时无有界公平保证；退场冻结 READY 的复用必须逐角色核对，不得把缺失权威事实写成已实现能力。本轮未请求或执行生产修复，调查状态为 DONE_WITH_CONCERNS（SPEC 已完成，运行时差距待实施验证）。

### 工程评审结论（2026-09-26）

本 SPEC 已完成一轮工程评审（含 Codex 外部复核），逐项决策如下：

1. R3 首次优先取舍：接受，补充重试候选等待时长可观测性（见 R3 末段）。
2. 与 `wms-outbound-picking-task-integration-requirements.md` §14.1/§14.2 的合同冲突：本轮已同步修订该合同，按货架进场/退场/BIN 分别限定语义（详见该文件 §14.1 表后说明与 §14.2 对应条目）。
3. ~~"周期扫描"表述与代码不符~~：**2026-09-27 纠正**——原判断有误，`process-execution-facts-batch` 是真实存在的 10 秒周期 Celery beat 任务（见 R6），退避到期最多 10 秒内会被重新评估，不依赖外部事件。R3 的可观测性要求保留，但不再以"无周期扫描"为理由。
4. 工作区未声明的代码/文档改动：已在第 7 节说明范围与来源。
5. 退料架（drain）与来源架共享目标点容量窗口但未参与首次优先仲裁（Codex 发现，已核实）：维持现状，已在 R3 明确边界避免与"独立执行"字面表述冲突。
6. ZONE 退场目标"权威成功事实"缺乏具体判定规则（Codex 发现，已核实）：要求实施前先定义判定规则，再落地第 6 节对应验证行（见 R5 末段）。
7. 验证矩阵补充"两个重试候选并发竞争同一名额"一行（见第 6 节）。

评审状态：DONE，2026-09-27 追加一条纠正（第 3 项）。下一步进入第 5 节最小实现方案时，需先完成第 6 项（ZONE 判定规则）再编写对应测试断言。
