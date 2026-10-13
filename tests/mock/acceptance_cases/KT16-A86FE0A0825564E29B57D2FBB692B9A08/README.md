# KT16 / A86FE0A0825564E29B57D2FBB692B9A08：异常恢复验收案例

## 案例状态与取证范围

这是本次现场任务的正常主线、历史残留阻塞与修复恢复全过程归档，供后续 Mock 验收取样和设计使用。遵循已有 GOOD CASE 的归档方式，**尚未接入可执行 Mock replay runner**；不替换 `tests/mock/data/kt16_manual_picking_good_case.json`。

- 服务：`100.94.216.118` / KT16，WorkLine `348950323769920`，插件 `manual-picking`。
- 任务：`A86FE0A0825564E29B57D2FBB692B9A08`，数据库 ID `358060080874048`，`MANUAL`，plan revision `1`。
- 创建：`2026-10-08 18:30:11.932776 UTC`（北京时间 10-09 02:30:11）。
- 当前快照：2026-10-12T19:47:05.622588+00:00 UTC；任务 **`EXECUTION_COMPLETED`**，完成状态更新 `2026-10-12 19:34:32.756775 UTC`（北京时间 10-13 03:34:32）。
- 首批计划没有 `target_rack_id/target_rack_face`；转运货架呼入、旋转、移出由人工在 WMS 操作，不为其增加 WES 限制。
- 取证使用一个 PostgreSQL repeatable-read、只读事务。保存任务直接记录、引用 Evidence、创建后 KT16 上下文和指定的旧排空上下文。KT16 上下文不等于全部属于本任务；按任务、operation、Transport、Return 身份关联。
- 时间统一为 UTC；数据库无时区字符串按项目数据库约定解释为 UTC。CSV 的“捕获时状态”是当前快照状态，**不是对应创建/接收时刻的状态**；不把可变行拼成不存在的状态历史。

文件：[原始持久化快照](evidence.json)、[时间线](timeline.csv)、[人工处理历史及缺口](operator_history.json)。快照数量：source_members 8、passages 8、batches 8、confirmations 40、evidences 166、commands 34、transports 37、returns 11。

## 逐箱主线与返料结果

SCAN1～4 对应 `STATION_SCAN9`～`STATION_SCAN12`；入口 `CNV0301`，出口 `CNV0302`，五格架工位 KT16。
八个源货架为 `510001/270`、`510003/270`、`510004/270`、`510006/270`、`510008/270`、`510021/90`、`510034/90`、`510035/270`；八批 inbound 均保留。

| 料箱 | 原入站储位 | Passage / WMS 工作结果 | 当前 Return ID / 状态 | 回架储位 / Transport 结果 |
| --- | --- | --- | --- | --- |
| `A000000541` | `510001B4F2C101` | `CLOSED/NORMAL` | `358373582979648/EXITED` | `510034A3F1C101` / `SUCCEEDED` |
| `A000000702` | `510034A3F1C101` | `CLOSED/NORMAL` | `359491458650688/NONE` | `尚未下发` / `无` |
| `A000001885` | `510003B1F2C101` | `CLOSED/NORMAL` | `358373292577344/EXITED` | `510021A2F1C101` / `SUCCEEDED` |
| `A000002213` | `510021A2F1C101` | `CLOSED/NORMAL` | `359490879689280/READY` | `尚未下发` / `无` |
| `A000002255` | `510004B2F2C101` | `CLOSED/NORMAL` | `358710109061696/EXITED` | `510035B2F1C101` / `SUCCEEDED` |
| `A000002456` | `510008B1F1C101` | `CLOSED/NORMAL` | `359480994873920/EXITED` | `510003B1F2C101` / `SUCCEEDED` |
| `A000002477` | `510006B4F1C101` | `CLOSED/NORMAL` | `358791384474176/EXITED` | `510001B4F2C101` / `SUCCEEDED` |
| `A000002701` | `510035B2F2C101` | `CLOSED/NORMAL` | `359490649096768/EXITED` | `510004A3F1C101` / `SUCCEEDED` |

八箱工作结果均为 `NORMAL`，Passage 均已闭合。六箱回架 Transport 已有 `SUCCEEDED`；`A000002213` 为 `READY`，`A000000702` 为 `NONE`，尚未捕获对应回架 Transport。`A000002255` 另有三条 `VOIDED` Return，必须保留并按 Return ID 区分，不能把它们当作当前返料。

任务业务完成不代表全部退箱完成。完成确认 `outbound.picking_task.completion_confirm@v1`：operation `01a1319b-e181-72f5-ba32-f58b9af99a34`，Confirmation `359491427643968`，响应 Evidence `359491472187968`，`COMPLETED`。任务完成后的退箱仍有独立 owner，继续按原身份执行。

## 全过程与异常分支

### 1. 旧排空阻塞与人工清理

旧排空不是本任务源计划，而是 KT16 上一轮依赖：drain operation `01a0f49e-c7ee-7c10-991d-58e841b4ef3b`，rack `510017/270`，Evidence `355300362568256`，绑定 `355300362981952`。货架进站 Transport `transport-2f83ef0a-3ad3-4dcb-9a0f-31acaeac9e6b` 曾成功。

旧料箱 `A000001624` 的 `transport-17cfb9bc-7658-47a7-afa4-1c601f0089e4` 为 `CNV0302 → 510017B3F2C101`，当时仅 `ACCEPTED`，没有终态。用户报告 RCS 手动取消、WMS 手动回架，但 WMS 没有途径通知 WES；不能由此伪造成功。

按用户指令先删除旧 drain Confirmation `355300318065216`，本任务得以准备，prepare operation `01a120fc-6710-71de-8c8d-8749b5abdf5b` 获 `PREPARE_ACCEPTED`。随后按用户明确指令删除 `A000001624` 相关 24 条记录（明细类别见 operator_history），人工移出滚筒线。旧 drain 绑定仍在，因此“删当前排空”尚未解除残留业务窗口。

这些删除过的记录不能从当前数据库取回，以上来自本会话已执行处理记录；当前 JSON 只保存仍存在的旧 Evidence/绑定。不把人工动作当成收到物理结果，也不把一次性数据库清理写成自动恢复验收步骤。

### 2. A000001885：接纳后硬件故障

回架 `510021A2F1C101` 下发后，用户报告 CTU 故障。当时保持原 Transport 身份及未知结果，`ACCEPTED`/Return `EXITED` 不证明物理回架。当前快照中对应 Transport `transport-32f4a96f-4704-490a-b909-85c266c2dd7c` 已为 `SUCCEEDED`，更新于 `2026-10-10 16:33:44.306295 UTC`；归档保留最终权威结果，不猜测现场维修过程。

### 3. A000002477：已到返料节点，残留窗口阻塞 drain

原入站 Transport `transport-eb155f07-727c-48b5-9b74-0d5859727996` 成功。WMS 工作完成后，SCAN3/SCAN4 均到达，Return `358791384474176` 已 `READY`；旧 `510017` drain 窗口令 `_five_rack_window_occupied` 判断占用，未触发新的返料批次。

`2026-10-12 18:37:03.884794 UTC` 释放旧绑定业务窗口。修复规则：没有 current drain 时收敛残留 `DRAIN_RACK_IN_STEP` 窗口，不忽略仍有效的 source rack window。
随后 drain operation `01a13167-7747-7e83-b257-f59e2bbcf36f` 获 `510001/270`，rack-in `transport-820271d2-5ce1-49f7-80d3-220f895bb58b` 继续执行；返料批次 operation `01a1316d-203a-70db-909f-a7bd71dfba86`，响应 Evidence `359478881894976`。箱回架 Transport `transport-81635033-8e86-4e38-93c8-f9289182fdfd` 已 `SUCCEEDED`，目标 `510001B4F2C101`。

### 4. A000002701：SCAN2 乱序准入

入站：`510035B2F2C101 → CNV0301`，Transport `transport-ada8e6d5-1175-4a05-aa41-4e1fed34c4d8` 成功。SCAN1 Evidence `358791506436672`；本箱 Passage `358791506801217`。

SCAN2 Evidence `359481302495808`（`2026-10-12 18:53:09 UTC`）携带 `A000002701-C`。旧逻辑按队首 `A000002213` 判断，错误 NG 放行且把 fault 关联到另一箱。原命令 `01a13176-29ca-7070-90d5-0d61513fd714` 已成功，不得重复发送。

修复后 SCAN2 只校验属于当前有效 inbound_batch，不要求队首、前序位置或 SCAN1；仍保留有效批次、源成员未取消和本箱身份检查。重放原 SCAN2 后为本箱创建 admission operation `01a13187-fb0f-79a6-9d70-1e4ca7421cab`，获 `WORK_REQUIRED`，Evidence `359486095376960`；另一箱错误 fault 被清除，原命令和原 Evidence 保留。

### 5. 命令身份冲突与冻结期限重放

本箱 WMS 完成通知 Evidence `359486370964032`，operation `01a13189-0aef-70f0-b47d-7a56260f7496`，工作时间 `2026-10-12 19:13:47.247 UTC`。旧 `_move` 用同一执行引用重算 deadline，基础命令服务拒绝身份漂移，完成通知应用回滚，Passage 未闭合。SCAN3 Evidence `359487957180992` 已保存但依赖未满足，尚未发布。

修复读取同一 execution_ref 的原命令，复用原 deadline；仍由基础服务核对冻结请求，正文漂移继续报错。部署后恢复两条原 Evidence 的持久消费，不改 payload，不换 operation_id，不重新下发旧 SCAN2。

原 SCAN2：execution_ref `manual-picking:359481302495808:SCAN2`，command_code `01a13176-29ca-7070-90d5-0d61513fd714`，deadline `2026-10-12 18:53:39.958194 UTC`，`SUCCEEDED`，attempt_count `1`。Passage `CLOSED/NORMAL`；SCAN3 创建本轮 Return `359490649096768`，SCAN3 新命令 `01a13198-fb00-71da-aa76-ffdac3187113` 成功。

后续真实 SCAN4 Evidence `359491124740672` 于 `2026-10-12 19:33:07.875632 UTC` 到达。返料批次 operation `01a1319f-f85d-74c3-bbbc-ba0a5e04a65d`，Evidence `359492536562240`；回架 Transport `transport-6b712028-d9c2-4399-8d01-156f58b48f30`，原 client_request_id `01a131a0-0398-771f-a8d8-34b579d48a3c`，目标 `510004A3F1C101/90`，`SUCCEEDED`，更新 `2026-10-12 19:39:44.892997 UTC`。此终态来自当前快照，区别于先前“等待 SCAN4”的中间观察。

## Mock 验收切片与现有自动化所有者

所有场景使用归档身份作为数据样本，在隔离 Mock 环境内注入；禁止向现场重放整份归档。以下是验收断言与已有测试映射，不表示本次运行过整条可执行现场 replay。

| Case | 输入 / 触发 | 必须满足的断言 | 主要已有测试所有者 |
| --- | --- | --- | --- |
| C01 | MANUAL 首批无 target_rack | 正常准备、源架入站；不创建转运架动作；AUTO 约束不因本例放宽 | `test_plan_admission.py`、`test_plan_applied_handler.py` |
| C02 | 无 current drain，残留 drain window | 清理业务窗口并继续 drain；原 Evidence 不改为 SUCCEEDED | `test_drain_flow.py::test_no_current_drain_ignores_residual_drain_window` |
| C03 | 残留 drain window 与真实 source window 共存 | 不因清理历史窗口忽略当前有效 source window | `test_drain_flow.py::test_residual_drain_window_does_not_hide_current_source_window` |
| C04 | 有效 batch 的 A000002701 提前到 SCAN2 | 按本箱准入，不能改 A000002213；不检查队首/前序位置 | `test_scan_flow.py::test_scan2_batch_member_out_of_order_does_not_mutate_other_box` |
| C05 | 有效 batch 箱无 SCAN1/位置投影 | 允许自己的 admission，缺少历史位置不构成围栏 | `test_scan_flow.py::test_scan2_batch_member_without_scan1_or_projection_requests_own_admission` |
| C06 | 不属于有效 batch / 另一箱正在 admission | 非成员不得污染其他 Passage；无关等待不阻塞有效成员 | `test_scan_flow.py::test_scan2_non_batch_bin_does_not_mutate_another_passage`、`test_scan2_unrelated_admission_wait_does_not_block_valid_batch_member` |
| C07 | 同一 SCAN execution_ref 再次求值 | 四个 SCAN 角色均复用 command_code/deadline，派发次数不增加 | `test_scan_flow.py::test_scan_command_replay_reuses_frozen_deadline_without_second_dispatch` |
| C08 | 同一 execution_ref 的正文漂移 | 继续拒绝身份冲突，不以复用 deadline 绕过正文校验 | `test_scan_flow.py::test_scan_command_replay_still_rejects_payload_drift` |
| C09 | 原 WMS 完成通知 + 已保存 SCAN3 | 原 Evidence 被消费，Passage NORMAL/CLOSED，创建唯一当前 Return；旧 SCAN2 不重复派发 | C07 + `test_scan_flow.py` 的完成通知/SCAN3 行为；跨 worker 组合的本次证据为现场恢复记录 |
| C10 | ACCEPTED、超时或人工取消报告 | 不当作物理成功、不换等价 Transport 身份；真实匹配 Result 才闭合 | Transport 基础合同已有测试；本案取样为 A000001885 及旧 A000001624，未新增通用重试机制 |
| C11 | 本轮 Return 收到真实 SCAN4 / return_batch | 按 Return 身份冻结顺序与结果；技术重试沿用原请求；A000002701 回架结果与出口交接分开判断 | `test_scan_flow.py`、`test_batch_transport.py`、`test_return_projection.py`、`test_drain_flow.py` |
| C12 | 任务 completion_confirm 先于全部退箱结束 | 不伪造余下箱成功，不撤销既有返料可靠义务；VOIDED Return 不混入新轮 | 当前快照及现有 completion/return 测试；未新增组合 runner |

测试路径均为 `workline_plugins/manual-picking/tests/`，Transport 基础测试按其宿主目录归属。下表映射到已存在的测试；缺少整链执行的场景明确使用取证依据，不能用几个单元测试推定端到端通过。

本次修复的已有验证记录：SCAN2 规则阶段插件 476 passed；冻结命令重放后插件 481 passed，基础 DeviceCommandService 22 passed；QUALITY 4170 passed / 5 skipped；受影响 PostgreSQL HEAVY 10 passed / 0 skipped，迁移升级/降级/重升级及空 SCAN1 的真实持久化路径已验证。详情见仓库 `reports/scan2-batch-admission-20261013/report.md`、`reports/scan2-command-replay-20261013/report.md`；这些是此前修复快照的证据，本次归档未重复执行。

部署记录：`wes-backend:scan2-replay-20261013`，SCAN2 migration `c47a2b9d610e`；API、两个 worker、beat、Flower 均已重建并核对 source hot mount。仅健康检查不作为业务通过依据；本案例使用实际持久化 Evidence/命令/Transport 结果。

## 当前未闭合与数据缺口

- `A000002213` / Return `359490879689280` 为 READY，`A000000702` / Return `359491458650688` 为 NONE；归档时仍缺这两箱回架结果。当前 drain execution `359492864815680` ACTIVE。任务不是全箱返料完成的 GOOD CASE。
- 已删除的 A000001624 原始 24 行只保留操作摘要，不能复原所有原 payload 或 callback 内容。operator_history 的未定时事件没有伪造时间戳。
- 当前数据库不能重建每次状态转移；CSV 保留可核查时间字段和当前状态，而非宣称有全量审计日志。没有导出 WMS/RCS 内部日志、鉴权配置或现场视频。
- 人工清理只是本次被授权的恢复经过，后续验收检查自动规则，不以人工删库作为正常流程。

## 归档完整性

- `evidence.json` SHA-256：`cb4c59dfefc012fde583ee53587110c5c0f7db2bced2bc390f384d764be2a678`。
- `timeline.csv` SHA-256：`1264988a6cf20233be3b19c5c9a96d77acd62d1f4a71e04317404867736ab368`。
- `operator_history.json` SHA-256：`22ea695104845a0774c5b75dbd73c92d6da0b94cc9e4a373f7a92f9e14d21a9c`。
