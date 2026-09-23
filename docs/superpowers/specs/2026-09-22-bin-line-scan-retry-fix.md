---
title: 回程段扫码重试修正：新物理扫码事件覆盖旧决定
status: Needs Rebase
created_at: 2026-09-22
audience: WES 架构、WMS 对接开发、手工/自动拣料插件二次开发人员
scope: 修正合同 §3.3/§3.4"点3/点4首次扫码决定一经冻结，新事件不得重新分流或覆盖原命令"的规则，
  消除滚筒线料箱物理错位后现场无法自行恢复的卡死问题
related:
  - docs/contracts/wms-manual-outbound-picking-integration-requirements.md
  - docs/architecture/authority-matrix.md
  - docs/integration/third_party_integration_whitepaper.md
  - docs/superpowers/specs/2026-09-22-automatic-picking-plugin-system-design.md
---

# 回程段扫码重试修正：新物理扫码事件覆盖旧决定

> **NEEDS REBASE / NOT CURRENT IMPLEMENTATION AUTHORITY。** 本文的“新扫码覆盖旧决定”及同设备未闭合命令门禁，尚未按 [SRS 第 0 章](../../architecture/SRS.md)和 [WES 职责收敛账本](../../architecture/wes-responsibility-convergence-ledger.md)重新裁决。相同业务对象的前一 Action 未确定时保留原身份并等待；其他对象仅因共用设备而产生的物理互斥交由 ECS 裁决。不得直接按本文实施或作为 `bin-line-common` 抽取依据。

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

1. 本修正先在 `workline_plugins/manual-picking/` 内部落地，包含下方"测试影响"一节要求的两个回归测试重写。
2. 合入 `develop`。
3. 自动拣料设计 §9 的 `bin-line-common` 抽取才能开始，抽取时这两个测试断言的已经是本文修正后的新行为，
   不会在合并时出现"旧断言与新行为冲突"。

## 1. 问题

现有合同 §3.3/§3.4（`wms-manual-outbound-picking-integration-requirements.md`，均为 `APPROVED`）规定"点3/点4
首次扫码决定与命令一经冻结，新事件不得重新分流或覆盖原命令"。这是防抖动的安全设计，但导致真实场景卡死：
滚筒线因料箱物理错位未能推进（如 SCAN4 出错），工人把料箱移回 SCAN3 之前重新走一遍是唯一现场恢复手段，
现有规则会直接拒绝第二次扫码（[scan_flow.py:407](../../../workline_plugins/manual-picking/src/manual_picking/application/scan_flow.py:407)），
不创建任何命令，也不报错，现场表现为"扫了没反应"。

```
修正前后对比（以 SCAN3 为例，SCAN4 同构）

修正前：
  第一次 SCAN3 evidence ──▶ 决定 MOVE_FORWARD ──▶ 冻结 scan3_command_code
                                                        │
  工人搬回、重新扫码                                      │（frozen）
  第二次 SCAN3 evidence（新 timestamp）──▶ 直接 RECONCILING，零命令，现场无反应 ✗

修正后：
  第一次 SCAN3 evidence ──▶ 决定 MOVE_FORWARD ──▶ scan3_command_code = C1
                                                        │
  工人搬回、重新扫码                                      │ 覆盖
  第二次 SCAN3 evidence（新 timestamp，非重复投递）──▶ 旧决定失效 ──▶ 按新证据重新判定 ✓
                                                                        │
                                          retry_count += 1（诊断，见"失控保护"）
```

## 2. 结论

不加人工确认动作。ECS 是扫码事实的权威（[authority-matrix.md](../../architecture/authority-matrix.md) 第 3/6
类），WES 不应用自己的历史记录否定一次新的、权威的物理事实。事件幂等已经按内容判定
（白皮书 §4.2：同一 `device_code+event_type+timestamp+data` 才算重复），ECS 真正的网络重试会被这层挡住；能到
达业务逻辑的"新" evidence 就是一次真实发生的物理扫码。

修正：新的物理扫码事件（非重复投递）覆盖旧决定；只有被识别为同一次投递的重复上报才继续走既有幂等。不影响：

- 合同 §3.1（SCAN1/2 身份规则）——本场景工作段早已结束，不涉及。
- WMS `task_id+bin_code` 单终态约束——回程段调整完全在 WES 内部。
- `_device_has_unclosed`（同设备未闭合命令门禁）须逐调用点区分对象级因果依赖与跨对象物理互斥；本文不再将其整体视为应保留的围栏。

现场确认扫码器只有两种触发源：正常离场-再进场，或工人在原点位手动触发 PLC 重置；两者都是真实发生的物理/人为
事件，不存在对静止料箱的误触发。因此不加额外的物理离场信号或最短间隔保护，两种触发源都按"新物理扫码事件覆盖
旧决定"同一规则处理，不做区分。

## 3. 失控保护

`retry_count`（`bin_line_returns` 表字段，见自动拣料设计 §5.2）记录每条回程记录被新物理事件覆盖的次数，
**仅诊断用，不参与业务判断、不阻止释放**。工人正常复位（离场-再进场，或手动 PLC 重置）预期是 0～2 次；现场
确认扫码器不会对静止料箱重复触发，真正的反复覆盖意味着硬件异常（扫码器故障或机械振动误触发），而不是一次
真实的人工复位。

加一个告警阈值（具体次数由现场数据校准，实施时先取一个保守估计，例如 5 次）：`retry_count` 超过阈值时只记
log/metric，**不阻断业务**、不要求人工确认才能继续释放——阻断会引入本文已经明确拒绝的人工确认动作，与
"工人只管拉回去"的目标冲突。告警只是让异常硬件被人发现，不改变任何业务行为。校准阈值时建议分别统计两种
触发源（离场-再进场 vs 手动 PLC 重置）各自的正常频次，避免把手动重置这类合法操作误算进异常基数。

## 4. 合同修改范围

`wms-manual-outbound-picking-integration-requirements.md` §3.3、§3.4 措辞从"决定一次即冻结，新事件只留证"
改为"新的物理扫码事件视为最新事实，覆盖旧决定；只有识别为同一事件的重复投递才继续走既有幂等"。不涉及任何
wire 字段、operation 或 WMS 可见行为，纯 WES 内部行为说明；因写在联合评审基线文档中，仍需走一遍评审流程，
但范围远小于修改 §3.1 或新增 wire 字段，且不依赖 WMS 回复即可先在代码里落地——合同措辞更新和代码修正可以
并行，代码修正不等合同文字评审完成。

## 5. 测试影响（REGRESSION，强制）

`workline_plugins/manual-picking/tests/test_scan_flow.py` 里的两个测试当前锁定"冻结后重扫不变命令"的旧行为，
与本文修正直接矛盾，必须**重写**（改断言方向），不是新增测试覆盖新场景就够：

- `test_scan3_rescan_does_not_change_frozen_route_or_command`（[:1141](../../../workline_plugins/manual-picking/tests/test_scan_flow.py:1141)）：
  当前断言 `scan3_command_code == frozen_command` 且第二次扫码结果为 `RECONCILING`；改为断言新证据覆盖旧决定、
  产生新命令。
- `test_scan4_rescan_preserves_first_fifo_order_and_command`：同构，SCAN4 版本。

需要新增的场景：

- 新物理扫码事件（不同 `timestamp`，非重复投递）覆盖旧决定，产生新命令，旧命令身份作废。
- `retry_count` 正确递增。
- `retry_count` 超过告警阈值时触发 log/metric，不阻断业务（§3 的失控保护）。

## What already exists

| 既有能力 | 本文的处理 |
| --- | --- |
| 事件幂等（白皮书 §4.2，按内容判定） | 直接复用，是本修正"区分真实重扫和网络重试"的基础，不新增判定逻辑 |
| `_device_has_unclosed` 同设备未闭合命令门禁 | 待按对象级因果依赖与 ECS 物理互斥重新裁决 |

## NOT in scope

- 不修改 SCAN1/SCAN2 的身份规则（合同 §3.1）。
- 不加人工确认/复位动作。
- 不加物理离场信号或最短时间间隔保护（现场确认扫码器只在离场-再进场或手动 PLC 重置时触发，不会对静止料箱
  重复触发）。
- 不处理"决定即冻结"在工作段（自动插件 SCAN2 之后）是否存在同类风险——留给工作段设计文档评估。
