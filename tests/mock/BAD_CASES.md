# MOCK BAD CASE

## KT16-20260925：旧退箱异常阻塞货架下一轮执行

现场：`510002 / 270` 已再次成功到达 KT16，WES 没有继续申请下一批作业。

### 原始身份与触发条件

- PickingTask：`A842C8F688957405A821CF1D8A8D577D1`，新计划 revision `8`。
- 新到位 Transport：`transport-f99f913c-81e7-43ea-bdf5-c4d82342714f`，`SUCCEEDED`；到位结果已经应用。
- 旧 return_batch operation：`01a0d943-de51-71b8-ac6f-5a445a01db6e`，候选 `A000000341`、`A000000541`；响应 Evidence `353420529795648`。
- 旧 return_batch operation：`01a0d944-2cb6-73c3-b05c-1733c2f32bb0`，候选 `A000000541`；响应 Evidence `353420610568768`。
- 两笔 WmsConfirmation 均为 `COMPLETED`；响应为 `RECONCILING`，`published_at=NULL`。
- 原取消任务 `transport-aa406a2c-f066-4a7d-979a-fc882a779dc8` 已按用户确认取消终止。

根因：货架面检查只看响应是否未发布，把不可继续应用的异常响应也当成待执行动作，导致旧异常冻结同一货架面的新任务。

### 可执行故障注入回归

业务测试所有者为 [test_batch_repository.py](../../workline_plugins/manual-picking/tests/test_batch_repository.py) 中的
`test_mock_bad_case_kt16_old_return_response_does_not_block_next_round`。
测试在隔离 SQLite 中注入两笔旧响应，调用生产 Repository；不依赖真实设备或现场数据，不向服务器重放旧请求。

```bash
PYTHONPATH=.:workline_plugins/manual-picking/src uv run pytest \
  workline_plugins/manual-picking/tests/test_batch_repository.py \
  -k mock_bad_case -q -o addopts=''
```

预期：

- `RECONCILING`、`IGNORED` 的响应不再阻塞下一轮货架面作业。
- `PENDING`、`APPLIED` 且尚未发布的正常响应仍保留批次交接约束。
- 原异常状态、身份和未发布标记不被修改，不伪造物理成功。
- 已创建且仍在途的 Transport 仍由已有执行检查处理；不得把异常响应隔离误用为重发在途动作的依据。

现场验收应分别检查新 WMS 请求、响应应用、新 Transport 接纳及物理完成；测试通过不代表设备完成。

### 本次服务器恢复证据

2026-09-25 19:00:43 UTC，部署后服务器自动创建 revision `8` 的 `inbound_batch`
`01a0d9f0-fa92-7b1b-b2c4-1df3c6420a29`，WMS 决策完成。
随后创建 `transport-0bfc9e4f-1174-4799-992e-a6399891316a`，从 `510002 / 270 / 510002B3F1C101`
搬运 `A000000341` 到 `CNV0301`，已接纳（`ACCEPTED`）。这证明新一轮执行已恢复，尚不代表物理搬运完成。

## KT16-20260925-SCAN4：完成回调缺失，实物比可派发队列多一个箱

现场确认：`A000002456` 实物已在退箱通道中，但没有进入 WES 可派发的退箱候选，造成实物比候选队列多一个箱。
这里的“少一个”指 `READY` 候选，不是 Return 记录丢失；该箱的 Return 仍存在，状态为 `MOVE_PENDING`。

### 原始身份与证据

- WorkLine：`KT16`（`348950323769920`）；箱号：`A000002456`，SCAN4 原始条码 `A000002456-B`。
- Return：`353416576205376`；SCAN4 Evidence：`353416610947648`，已 `APPLIED`。
- SCAN4 设备：`STATION_SCAN12`；`MOVE_FORWARD` 命令：`01a0d935-6d32-735b-a7a0-6b2221437ab5`。
- 2026-09-25 15:35:52 UTC，WES 收到 SCAN4 `SCAN_COMPLETED`，返回 HTTP 200；随后放行命令收到接收 ACK。
- 未查到该命令的执行完成回调或被拒绝的接收记录；命令随后进入 `RECONCILING / ACK_DEADLINE_EXPIRED`，`result_evidence_id` 为空。
- 原始入口日志：服务器 `/srv/wes/app/current-single/logs/nginx/ecs_callback_body.log`，当次查询第 427 行为该箱 SCAN4 扫码；全文没有上述命令号。当次日志末条时间为 16:38:04 UTC。

故障链：SCAN4 扫码已处理 → 放行命令已接收 → 完成回调缺失 → Return 未转为 `READY` → 实物与可派发候选数量不一致。
不是扫码回调丢失，也没有证据表明完成回调因超时被 WES 丢弃；ECS 是否曾发送该完成回调仍未确定。
现场实物情况由用户确认，扫码和 ACK 本身不证明已完成到 OUTLET 的移动。

### MOCK 复现与验收条件

1. 上报该箱 SCAN4 扫码，正常接收 WES 的 `MOVE_FORWARD` 并返回 ACK；模拟实际移动，但抑制该命令的完成回调。
2. 让命令等待超时，确认该箱仍有 Return 记录且为 `MOVE_PENDING`，不进入 `return_batch` 候选。
3. 后续箱正常扫码并完成放行：其 `READY` 候选和独立任务应继续推进，不能被前箱缺失结果阻塞。
4. 另测迟到回调：在超时后以原命令身份发送 `SUCCESS`，应自动应用真实结果并恢复该箱资格；重复发送不得产生重复 Return 或重复搬运。
5. 始终不发送完成回调的分支须单独记录：后续箱恢复不等于该箱已自动恢复，不得用伪造成功或要求日常人员手工改库掩盖缺口。

### 已有回归与覆盖边界

[test_return_model.py](../../workline_plugins/manual-picking/tests/test_return_model.py) 的
`test_return_fifo_isolates_unready_bin_and_rejects_second_open_bin` 已覆盖：前箱保持 `MOVE_PENDING` 时，后箱 `READY` 仍可入选，且原箱记录保留。

```bash
PYTHONPATH=.:workline_plugins/manual-picking/src uv run pytest \
  workline_plugins/manual-picking/tests/test_return_model.py \
  -k test_return_fifo_isolates_unready_bin_and_rejects_second_open_bin -q -o addopts=''
```

该测试直接构造数据库状态，不模拟物理移动、ECS 丢回调或迟到回调全链路。
本条登记保留上述完整 MOCK 场景用于后续排查，不将部分队列回归标记为整起异常已经解决。

## KT16-20260925-SCAN3-REENTRY：异常箱重新投入被旧 Return 阻塞

`A000002456` 缺少上一轮 SCAN4 完成结果后，重新投入 SCAN3。
18:38:11 UTC 的扫码 Evidence `353461418492480` 已收到，却因旧 Return 已有 SCAN4 记录被转为 `RECONCILING`，没有下发新放行命令。

批准规则：SCAN3 是 SCAN3/SCAN4 异常恢复点。未派发回架搬运、当前方向命令为 `FAILED`、`TIMED_OUT` 或 `RECONCILING` 的箱，
以更新的有效 SCAN3 扫码开启新轮次；旧 Return 置 `VOIDED`，保留旧事件和命令，新 Return 重新等待自己的 SCAN4 到位与结果。
不伪造旧命令成功；正常在途命令重复扫码不重复下发，已派发回架搬运不能借此换身份重发。

可执行回归：

```bash
PYTHONPATH=.:workline_plugins/manual-picking/src uv run pytest \
  workline_plugins/manual-picking/tests/test_scan_flow.py \
  workline_plugins/manual-picking/tests/test_return_model.py \
  -k 'mock_bad_case_scan3 or scan3_recovery_replaces' -q -o addopts=''
```

- `test_mock_bad_case_scan3_recovers_failed_return_cycle` 覆盖 SCAN3/SCAN4 三种异常状态、新轮次放行至 `READY`、重复扫码幂等、旧扫码重放与旧命令迟到成功隔离。
- `test_scan3_recovery_replaces_current_return_atomically` 使用隔离数据库验证唯一当前 Return、旧事实保留以及事务失败整体回滚。
- 原轮次 `MOVE_PENDING` 不直接改为 `READY`；新轮次须取得自己的 SCAN4 成功结果。

2026-09-25 19:16:31 UTC，修复部署后重放上述已接收的 SCAN3 Evidence：旧 Return `353416576205376` 变为 `VOIDED`，
新 Return `353470840189504` 创建，原事件成功应用。新 SCAN3 `MOVE_FORWARD` 命令
`01a0d9ff-71d5-7102-b6a1-6a25dec26897` 于 19:16:32 UTC 获得 `ACKNOWLEDGED`。
该快照证明重新投入已恢复派发；尚未证明新轮次 SCAN4 或回库完成。

## KT16-20260925-CTU03：放箱未完成就提前下发货架离场

现场证据（UTC）：货架 `510027`，料箱 `A000000341`。

| 时间 | 记录 |
| --- | --- |
| 19:31:02 | `transport-86e18a3d-b519-4320-8a90-abb70e1d926a` 创建，将箱从 CNV0302 放入该架 |
| 19:31:11 | WES 请求 `departure_decide`，operation `01a0da0c-def8-7549-a991-8e47c4d87d56` |
| 19:31:12 | WMS 返回 `READY`，目的地 WH01 |
| 19:31:21 | CTU03 `transport-6d9c6807-5f3e-412d-a2c9-52de445e0981` 创建 |
| 19:31:23 | CTU03 被接纳 |
| 19:31:50 | 才收到上述 BIN_MOVE 的 TARGET_PLACED / SUCCEEDED |

根因：drain 路径将 Return 派发队列为空当成货架可离场，没有检查同轮正在向货架放箱的直接依赖。
接纳时间不能证明货架实际开始移动的时间。

修复：来源/drain 共用 CTU03 创建入口检查本轮相关 BIN_MOVE；本轮边界使用当前进站 Transport 创建时间，
同工作线、同 rack 的来源和目标成员均纳入。活跃且无确定放置事实时暂缓；确定放置或明确终态解除依赖。
旧轮次、其他 rack、其他工作线不阻塞。保留并复用本轮 WMS READY，不增加货架忙闲状态。

复现：先接纳 BIN_MOVE，延迟放置与终态回调，同时让 WMS 对 departure_decide 返回 READY。
预期尚无 CTU03；送达 TARGET_PLACED 或 SUCCEEDED 后重新推进，复用原 READY 创建 CTU03，不需新扫码或人工操作。
再分别注入旧轮次异常、其他货架在途任务，确认不影响当前架离场。

```bash
PYTHONPATH=.:workline_plugins/manual-picking/src uv run pytest \
  workline_plugins/manual-picking/tests/test_batch_repository.py \
  workline_plugins/manual-picking/tests/test_source_progression.py \
  -k mock_bad_case_ctu03 -q -o addopts=''
```

回归所有者：`test_mock_bad_case_ctu03_waits_only_for_current_rack_moves` 验证持久查询、来源/目标、状态及轮次隔离；
`test_mock_bad_case_ctu03_reuses_ready_when_current_bin_finishes` 验证来源/drain 两条路径的暂缓、再次评估和 READY 复用。
唤醒复用现有链路：位置 Evidence 应用后提交事务唤醒 plan driver；最终结果经插件结果发布唤醒 driver；每 10 秒周期扫描兜底。
测试采用隔离数据库和模拟端口；不能据此宣称现场物理顺序已验收。

## KT16-20260925-SCAN3-CANCELLED：已交接后取消的箱重新投入被误分流

`A000000541` 的旧 Return `353416162218560` 已因执行权交接成为 `EXITED`，随后退箱 Transport
`transport-aa406a2c-f066-4a7d-979a-fc882a779dc8` 被确认取消，记为 `FAILED / OPERATOR_CONFIRMED_CANCELLED`。
2026-09-25 19:42:17 UTC 的 SCAN3 扫码 Evidence `353477169455680` 被下发 `MOVE_LEFT`，
命令 `01a0da17-0684-7b49-824a-8427a7fcadd8` 于 19:42:20 UTC 成功，未建立新 Return。

根因：恢复入口只处理尚未闭合的 Return；无当前 Return/Passage 时，即使 `-B` 有效也被当成未获正常授权而分流。
修复规则：SCAN3 的有效独立重新投入直接创建新 Return，不以闭合历史是否存在作为准入条件。
已有 Passage 的人工作业/NG 规则不变，同箱真实在途 BIN_MOVE 保留执行约束，终态历史不阻塞。
旧 MOVE_LEFT 已完成，不能重放同一事件来创建相反命令；现场下一次有效扫码使用新身份。

```bash
PYTHONPATH=.:workline_plugins/manual-picking/src uv run pytest \
  workline_plugins/manual-picking/tests/test_scan_flow.py \
  workline_plugins/manual-picking/tests/test_scan_handlers.py \
  -k 'without_history_dependency or same_bin_active_transport or accepts_reentry or independent_reentry' -q -o addopts=''
```

覆盖：保留/清除闭合历史均能重新投入、同事件幂等、新轮次 SCAN4 成功后 READY、同箱在途时延迟并在解除后推进，
以及当前 NG/未授权处置不能绕过。独立重新投入还覆盖另一箱 SCAN3 命令仍为 ACKNOWLEDGED 的场景：新箱正常 MOVE_FORWARD，原命令状态不变。
持久化在途查询的状态与箱身份隔离由 `test_batch_repository.py` 覆盖。

## KT16-20260925-SCAN1-TARGET-IN-FLIGHT：目标架在途使扫码永久停止

任务 `ABD3C805441534BF4ACBA6F0329623127` 的 `A000002165-B` 在 UTC 20:04:08 到达 SCAN1，
Evidence `353482541929024` 已接收，料箱入站 Transport 已 SUCCEEDED。
目标货架 `610017` 的 F01 `transport-ba8a3b32-8b68-4958-8fd4-15912f72c20c` 仍为 ACCEPTED，
目标 `OUT65 / 90` 尚无到位结果，当前位置因 ACK_INVALIDATION 标记为 unknown。
旧代码直接将扫码转为 RECONCILING，退出自动重试，造成即使后来到位也不能自动继续。

最终修复：取消 SCAN1 对目标货架的到位依赖。目标 F01 仍 ACCEPTED、目标位置未知或无位置记录，
只要本箱入站依据满足，即创建一条 MOVE_FORWARD。真正需要目标架参与的人工拣料由 WMS/PDA 准入控制。
保留本箱入站及 batch 当前货架的真实等待：位置 unknown 且原 Transport 为 PENDING/ACCEPTED 时返回 DEFERRED；
没有原 Transport 或已终态但位置仍 unknown，不伪造到位。无新增状态或等待机制。
先前临时修复仅恢复等待重试，已被上述删除目标架依赖的方案替代；原扫码恢复为 APPLIED 的审计记录为 `3`，
后续继续使用同一 Evidence，不要求现场重新扫描。
现场验证：UTC 20:19:31 原扫码自动发布，唯一 MOVE_FORWARD 命令
`01a0da39-1e3e-7964-a9a7-9f099a3a5b16` 于 20:19:34 收到 SUCCEEDED；此时目标 F01 仍 ACCEPTED。

```bash
PYTHONPATH=.:workline_plugins/manual-picking/src uv run pytest \
  workline_plugins/manual-picking/tests/test_scan_flow.py \
  -k 'admits_ready_bin or retries_unknown_position or unknown_position_without_in_flight' -q -o addopts=''
```

## KT16-20260925-DEPARTURE-WAKE：离场链路叠加两轮扫描延迟

`A000002456` 放入 `510024` 的 BIN_MOVE 于 UTC 20:40:23.199 成功，CTU03 于 20:40:37.966 接纳。
离场请求 20:40:22.979 已持久化，但到 20:40:30.356 才派发；WMS 20:40:31.225 返回 READY，
到 20:40:36.539 才创建 CTU03。主要延迟为两段 WES 唤醒缺失，不是 WMS 决策耗时。

修复：RackDepartureScheduler 提交后唤醒 confirmation dispatch；确定的 PickingTask/WorkLine 响应提交后
唤醒现有 Plan driver。CTU03 创建后的 Transport 提交唤醒直接复用。保留 10 秒扫描，不新增状态/字段/模型。
同事务重复唤醒合并，回滚不发送；重复派发复用确认身份，重复离场推进复用原决定关联与 Transport 幂等。
当前 BIN_MOVE 未完成仍不能 CTU03，待现场依赖解除后重新判断。

回归：`test_departure_creation_wakes_dispatch_after_commit_and_coalesces_duplicates`、
`test_ready_result_wakes_plans_after_commit_without_redispatch`；真实 worker 回归由
`tests/integration/wms_adapter/outbound_picking/test_departure_production_wiring.py` 承担，关闭 Beat，
由提交后唤醒发起首次派发，验证重复请求只有一次 HTTP，并在 worker 日志确认 Plan 被唤醒。
验证结果：QUALITY 通过、核心聚焦 907 项、插件 330 项、所选 HEAVY 139 项通过。
部署后 `510017`：UTC 20:52:43.823 READY 持久化，20:52:44.073 创建 CTU03（约 250 ms），
20:52:48.143 接纳，Transport 为 `transport-54807596-f862-41c6-a413-0247d9b2dea1`。
该离场请求创建于服务重启期间，不能用其创建到派发耗时证明正常事件延迟；首次派发唤醒由无 Beat 的真实 worker 测试验证。

## KT16-20260925-CANCELLED-FACE-DONE：取消成员的最终空清单被忽略

任务 `AE36107C57FAE4214A5AB7FB78E13B3C3` 的 `510002/270` 于 UTC 21:56:53 被 WMS
`PLAN_MEMBERS` 取消（Evidence `353510250357312`），原 CTU01
`transport-6dd21596-4c12-4608-9840-19b2eff3fd42` 继续执行并于 22:00:50 到达 KT16。
按合同，到位货架仍需取得最终 `RACK_FACE_DONE`；WMS 已返回该结果，但扫码应用层对取消成员
一律提前返回 `INBOUND_RESULT_AFTER_CANCEL`，未记录 Batch 完成进度。驱动因此每轮重新申请 inbound_batch，货架无法离场。

修复：取消检查允许 typed `BinInboundBatchRackFaceDone` 继续进入现有到位校验和 Batch 结果持久化；
取消后的 READY 仍不能创建新 BIN_MOVE。不增加状态、字段或定时器，不修改历史取消事实。

验收：已取消成员的货架到位后，最终空清单必须写入当前 Batch 进度；随后按当前退箱和货架依赖
判断离场，不得再次请求同一面的入箱批次。取消后的 READY 不下发搬箱。回归入口：
`test_cancelled_arrived_face_applies_final_empty_batch`、
`test_late_inbound_ready_after_member_cancellation_creates_no_bin_move`，并联合现有 Batch、来源货架推进测试验证。

现场更新后，无人工改库或补造事实：UTC 22:07:08 自动记录最终空清单 Evidence `353512766399040`，
22:07:17 创建 `510002` 的 CTU03 `transport-5ba24115-e276-48b2-8830-0ad56a176bb2`，
22:07:19 已 ACCEPTED；下一货架 `510034` 的进场 Transport
`transport-4dcda3c4-9f0f-45ca-84d1-4414611109ed` 于 22:07:20 已 ACCEPTED。
此处现场验证到执行系统接纳，不把 ACCEPTED 当作物理离场完成。
聚焦测试 212 项通过，Ruff 与 diff whitespace 检查通过；服务文件哈希一致，三个相关服务 healthy。

### SCAN3 独立重投入补充验收

无旧 Passage 或同线 PositionProjection 的有效箱，从 SCAN3 新建 Return 后，匹配 SCAN4 MOVE_FORWARD 的 SUCCESS 必须使其 READY；不得因旧投影不存在或属于其他线而卡住。当前 Return 与 Command 身份仍严格匹配，ACK 不能代替 SUCCESS，也不伪造 Transport 投影来源。回归：`test_independent_scan3_reaches_ready_without_matching_old_projection`（无投影、异线旧投影两种情况）。
