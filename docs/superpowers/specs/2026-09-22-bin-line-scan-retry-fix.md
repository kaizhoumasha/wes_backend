---
title: 回程段扫码重试修正：命令终态后为同一 Requirement 创建新 Action
status: Plan — 工程评审闭合，重扫行为已实施；拆表待实施
created_at: 2026-09-22
updated_at: 2026-09-24
audience: WES 架构、WMS 对接开发、手工/自动拣料插件二次开发人员
scope: 修正合同 §3.3/§3.4"点3/点4首次扫码决定一经冻结，新事件不得重新分流或覆盖原命令"的规则，
  消除滚筒线料箱物理错位后现场无法自行恢复的卡死问题
related:
  - docs/contracts/wms-manual-outbound-picking-integration-requirements.md
  - docs/architecture/SRS.md
  - docs/architecture/wes-responsibility-convergence-ledger.md
  - docs/architecture/authority-matrix.md
  - docs/integration/third_party_integration_whitepaper.md
  - docs/superpowers/specs/2026-09-22-automatic-picking-plugin-system-design.md
---

# 回程段扫码重试修正：命令终态后为同一 Requirement 创建新 Action

> **工程评审已闭合。** 重扫行为已在当前 Passage 实现中落地；Passage/Return 生命周期和 FIFO 排序以
> [09-24 拆表方案](2026-09-24-return-segment-cycle-table-split.md)为准，拆表尚待实施。
> 2026-09-22 首版的结论"新的物理扫码事件覆盖旧决定"表述过宽：SRS §0 明确"首次 SCAN4 到位顺序保留首次权威事实，
> 重复到位不重新排序"（[SRS.md:39](../../architecture/SRS.md:39)、[:43](../../architecture/SRS.md:43)），账本 R09/Causality 把这条判
> 定标为 `KEEP — Automation SOP / Fact Projection`（[ledger:133](../../architecture/wes-responsibility-convergence-ledger.md:133)）。
> 如果字面实现"覆盖旧决定"，会连首次 `scan4_evidence_id + scan4_event_time` 这个 FIFO 排序锚点一起覆盖，直接违反该条。
> 本版把诉求收窄为 SRS §0 第 35 行已经许可的通用规则："前一次 Action 已明确终态、原 Requirement 仍有效、权威事实尚未
> 满足目标"时，为同一 Requirement 创建新 Action——不触碰已冻结的首次到位排序事实。详见"与 SRS/账本的对齐"一节。

## 定位

本文从 [自动拣料插件系统设计](2026-09-22-automatic-picking-plugin-system-design.md) 拆分出来，独立评审、独立
落地，不等那份文档整体通过。理由：本文修的是**现场正在发生的真实卡死**，不依赖任何 WMS 合同确认或设备附录，
自动拣料设计里外部依赖多、评审周期长，不应该拖慢一个纯 WES 内部行为修正。

本文修正的回程段（SCAN3/SCAN4、`RETURN_BUFFER`、drain）归属 `bin-line-common` 共享包（见自动拣料设计 §4），
对手工线、自动线同时生效。

## 落地顺序（评审确定）

本修正与共享包 `bin-line-common` 的抽取工作改的是**同一批文件**（`scan_flow.py` 的 SCAN3/4 分支）。这是一次
**行为变更**，不能和"零回归抽取"混在同一次改动里验证（项目原则：不把重构和新功能混在一次改动里验证）。固定
顺序：

1. 本修正先在 `workline_plugins/manual-picking/` 内部落地，包含下方"测试影响"一节要求的回归测试重写。
2. 合入 `develop`。
3. 自动拣料设计 §9 的 `bin-line-common` 抽取才能开始，抽取时这些测试断言的已经是本文修正后的新行为，
   不会在合并时出现"旧断言与新行为冲突"。

## 1. 问题

修正前合同 §3.3/§3.4（`wms-manual-outbound-picking-integration-requirements.md`，均为 `APPROVED`）规定"点3/点4
首次扫码决定与命令一经冻结，新事件不得重新分流或覆盖原命令"。这是防抖动的安全设计，但导致真实场景卡死：
滚筒线因料箱物理错位未能推进（如 SCAN4 出错），工人把料箱移回 SCAN3 之前重新走一遍是唯一现场恢复手段。

修正前代码的问题不是"规则过严"，而是**规则实现比合同措辞还宽**：`_apply_scan3`
（[scan_flow.py:573](../../../workline_plugins/manual-picking/src/manual_picking/application/scan_flow.py:573)）
与 `_apply_scan4`（[scan_flow.py:626](../../../workline_plugins/manual-picking/src/manual_picking/application/scan_flow.py:626)）
只要 `scan3_evidence_id`/`scan4_evidence_id` 已经写入，第二次扫码就直接 `return None`——**不检查对应命令
（`scan3_command_code`/`scan4_command_code`）是否已经到达确定终态**（`FAILED`/`TIMED_OUT`/`SUCCEEDED`）。
不创建任何命令，也不报错，现场表现为"扫了没反应"。这与 SRS §0 第 35 行的通用重试规则直接冲突：SRS 允许
"前一次 Action 已明确终态"后为同一 Requirement 创建新 Action，修正前代码却连终态判断都没做，无条件拒绝。

```
修正前后对比（以 SCAN3 为例，SCAN4 同构，但 SCAN4 额外保留首次到位排序锚点，见下一节）

修正前：
  第一次 SCAN3 evidence ──▶ 决定 MOVE_FORWARD ──▶ scan3_command_code = C1
                                                        │
  工人搬回、重新扫码                                      │（不判终态，直接丢弃）
  第二次 SCAN3 evidence（新 timestamp）──▶ 直接 return None，零命令，现场无反应 ✗

修正后：
  第一次 SCAN3 evidence ──▶ 决定 MOVE_FORWARD ──▶ scan3_command_code = C1
                                                        │
  工人搬回、重新扫码                                      │ 先查 C1 终态
  第二次 SCAN3 evidence（新 timestamp，非重复投递）
    ├─ C1 未到终态 ──▶ 按既有 _WAIT_FOR_RESULT 语义等待，不新建命令
    ├─ C1 = SUCCEEDED 且权威事实已满足目标 ──▶ 不新建动作（SRS §0:35："设备 Action 的 CANCELLED 也不等于
    │                                          业务目标未完成：若权威到位和方向事实已满足目标，不再创建动作"）
    └─ C1 = FAILED / TIMED_OUT（已明确终态）──▶ 按新证据重新判定，创建新 Action（新 scan3_command_code）
```

## 2. 结论

不加人工确认动作。ECS 是扫码事实的权威（[authority-matrix.md](../../architecture/authority-matrix.md) 第 3/6
类），WES 不应用自己的历史记录否定一次新的、权威的物理事实。事件幂等已经按内容判定
（白皮书 §4.2：同一 `device_code+event_type+timestamp+data` 才算重复），ECS 真正的网络重试会被这层挡住；能到
达业务逻辑的"新" evidence 就是一次真实发生的物理扫码。

**修正后的规则收窄为两层，不再是无条件"新事件覆盖旧决定"：**

- **Action 层（是否创建新命令）**：新的物理扫码事件（非重复投递）在对应命令**已到达确定终态**
  （`FAILED`/`TIMED_OUT`）且**权威事实尚未满足目标**时，触发重新判定并创建新 Action；命令仍未到终态时，
  沿用既有 `_WAIT_FOR_RESULT` 等待语义，不新建命令、不丢弃事件。这是 SRS §0 第 35 行已经许可的通用业务重试
  规则的一个实例，不是新增例外。
- **排序事实层（SCAN4 专属，不可变）**：拆表后的 FIFO 排序锚点是首次有效
  `scan4_evidence_id + scan4_event_time`，其中 `scan4_event_time` 来自该 Evidence 的设备扫码发生时间；
  `received_at` 仅供审计。二者首次写入后永不重写，真实重扫只更新当前 `scan4_command_code`。
  拆表前 [passage_repository.py](../../../workline_plugins/manual-picking/src/manual_picking/application/passage_repository.py)
  仍使用 `scan4_received_at` 排序，这是待替换的旧实现，不是目标合同。SCAN3 首次到位 Evidence 同样保留为
  Return BEGIN fact，真实重扫只更新当前 `scan3_command_code`；`scan3_route` 不迁入 Return。

不影响：

- 合同 §3.1（SCAN1/2 身份规则）——本场景工作段早已结束，不涉及。
- WMS `task_id+bin_code` 单终态约束——回程段调整完全在 WES 内部。
- SCAN4 的 FIFO 排序锚点（首次 `scan4_evidence_id + scan4_event_time`）——按上一段，永不重写。
- `_device_has_unclosed`（同设备未闭合命令门禁）——账本 R10 已裁决：已识别 SCAN3/SCAN4 料箱不再因**其他**
  料箱在同设备上的未闭合命令而等待（P1 已删除跨对象门禁，见
  [ledger:57](../../architecture/wes-responsibility-convergence-ledger.md:57)）；现有代码在 `_apply_scan3`
  （[scan_flow.py:575](../../../workline_plugins/manual-picking/src/manual_picking/application/scan_flow.py:575)）
  仅在 `passage is None`（无法关联到已知 Passage）时才检查该门禁，与账本裁决一致，本文不改动这部分。账本标注
  "其余调用点待审"（SCAN1/WMS completion/admission 分支），不在本文范围内。

现场确认扫码器只有两种触发源：正常离场-再进场，或工人在原点位手动触发 PLC 重置；两者都是真实发生的物理/人为
事件，不存在对静止料箱的误触发。因此不加额外的物理离场信号或最短间隔保护。

## 3. 统计与告警范围

本次没有按 execution 累计重扫或超过阈值告警的验收需求，不增加计数字段、阈值或告警逻辑。真实扫码、业务重试的新 Action 与同一 Command 的技术派发尝试分别由 Evidence、DeviceCommand 保留；`DeviceCommand.attempt_count` 不能当作物理重扫次数。未来如有明确诊断需求，先定义统计对象、扫码点和时间窗口，再从原始事实查询或建立可重建的诊断投影，不改变本次重扫准入。

## 4. 合同修改范围

`wms-manual-outbound-picking-integration-requirements.md` §3.3、§3.4 措辞从"决定一次即冻结，新事件只留证"
改为"命令到达确定终态（`FAILED`/`TIMED_OUT`）且权威事实尚未满足目标时，新的物理扫码事件视为最新事实，为同一
Requirement 创建新 Action；命令未到终态时按既有等待语义处理；SCAN4 的首次到位排序事实（用于 `return_batch`
FIFO）永不因重试改写；只有识别为同一事件的重复投递才继续走既有幂等"。不涉及任何 wire 字段、operation 或 WMS
可见行为，纯 WES 内部行为说明；因写在联合评审基线文档中，仍需走一遍评审流程，但范围远小于修改 §3.1 或新增
wire 字段，且不依赖 WMS 回复即可先在代码里落地——合同措辞更新和代码修正可以并行，代码修正不等合同文字评审
完成。

## 5. 测试影响（REGRESSION，强制）

当前工作区的 [test_scan_flow.py](../../../workline_plugins/manual-picking/tests/test_scan_flow.py) 已将 SCAN3 重扫拆为
“当前命令未终态时等待”“明确终态后创建新命令”“成功后不重复创建”三个场景；
`test_scan4_rescan_preserves_first_fifo_order_and_command` 仍验证未满足重试条件时保持原命令。
这些测试属于拆表前 Passage 实现，不能代替拆表后 Return 首次事实不变的验证。

需要新增的场景：

- 首次命令未到终态时收到新的物理扫码事件（非重复投递）——按既有 `_WAIT_FOR_RESULT` 语义处理，不新建命令、
  不丢弃事件。
- 首次命令到达 `FAILED`/`TIMED_OUT` 后收到新的物理扫码事件——重新判定并创建新命令，旧命令身份作废；
  同一 Evidence 重放仍命中原 Command。
- SCAN4 场景：拆表后验证当前命令明确失败并真实重扫时，首次 `scan4_evidence_id + scan4_event_time`
  保持不变、当前 `scan4_command_code` 更新，Return FIFO 排序不受重试影响。

## 与 SRS/账本的对齐（2026-09-24 新增）

| 首版（2026-09-22）表述 | SRS §0 / 账本裁决 | 本版处理 |
| --- | --- | --- |
| "新的物理扫码事件覆盖旧决定" | SRS §0:39/:43："首次 SCAN4 到位顺序保留首次权威事实，重复到位不重新排序"；账本 R09/Causality 标 `KEEP — Automation SOP / Fact Projection`（[ledger:133](../../architecture/wes-responsibility-convergence-ledger.md:133)） | 收窄为"命令到达确定终态后为同一 Requirement 创建新 Action"（SRS §0:35 已许可的通用规则），SCAN4 排序锚点永不重写 |
| `_device_has_unclosed`"待按对象级因果依赖与 ECS 物理互斥重新裁决" | 账本 R10 已裁决：已识别 SCAN3/SCAN4 料箱不等待其他料箱未闭合命令（P1 已删除跨对象门禁），"其余调用点待审"（[ledger:57](../../architecture/wes-responsibility-convergence-ledger.md:57)） | 现有代码在本文涉及的调用点已与账本裁决一致，不改动；"其余调用点"不在本文范围 |

## What already exists

| 既有能力 | 本文的处理 |
| --- | --- |
| 事件幂等（白皮书 §4.2，按内容判定） | 直接复用，是本修正"区分真实重扫和网络重试"的基础，不新增判定逻辑 |
| `_device_has_unclosed` 同设备未闭合命令门禁 | 本文涉及的调用点已与账本 R10 裁决一致，不改动；其余调用点由账本继续跟踪 |
| 拆表前 Passage 的 `scan4_received_at` + 首次 `scan4_evidence_id` | 当前实现保留首次值；拆表后改由 Return 的首次 `scan4_evidence_id + scan4_event_time` 排序，重试只替换当前 `scan4_command_code` |

## NOT in scope

- 不修改 SCAN1/SCAN2 的身份规则（合同 §3.1）。
- 不加人工确认/复位动作。
- 不加物理离场信号或最短时间间隔保护（现场确认扫码器只在离场-再进场或手动 PLC 重置时触发，不会对静止料箱
  重复触发）。
- 不处理"决定即冻结"在工作段（自动插件 SCAN2 之后）是否存在同类风险——留给工作段设计文档评估。
- 不改动 `_device_has_unclosed` 的"其余调用点"（账本 R10 仍标记待审的部分）。
