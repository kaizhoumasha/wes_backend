# 人工出库联调：货架搬运规则

2026-09-10 现场确认。适用于 `/ops/manual-outbound-integration` 的计划资源；货架角色来自已应用的
`plan_delta`，不得根据货架号前缀或位置编码猜测。

| 计划资源 / 货架类型 | 动作 | RCS 模板 | 源位置 | 目标位置 |
| --- | --- | --- | --- | --- |
| `target_rack` / 转运货架 | 出库搬运 | `F01` | `RACK`，货架号 | `RACK_POSITION` |
| `target_rack` / 转运货架 | 换面 | `CK04` | `RACK`，货架号 | 当前实际所在的 `RACK_POSITION` |
| `target_rack` / 转运货架 | 回库搬运 | `F01` | `RACK`，货架号 | `ZONE` |
| `bin_source_racks` / 五层货架 | 出库搬运 | `CTU01` | `RACK`，货架号 | `RACK_POSITION` |
| `bin_source_racks` / 五层货架 | 换面 | `CTU02` | `RACK`，货架号 | 当前实际所在的 `RACK_POSITION` |
| `bin_source_racks` / 五层货架 | 回库搬运 | `CTU03` | `RACK`，货架号 | `ZONE` |

`rack_id` 与 `RACK.location_code` 必须一致。面向是非空不透明字符串，`"90"`、`"270"` 原样传递，
不进行数值转换或去空格。换面不得根据计划目标推断当前位置；回库目标遵循 WMS 的权威决定。

## 第 4 步的实现范围

第 4 步只创建出库 `MOVE_RACK`，逐架提交一个 TransportTask：

- 从已应用的 `target_rack`、`bin_source_racks` 选择货架，带入角色、模板及计划面向。
- 转运货架使用 `F01`，目标为现场配置的 `outbound_transfer_position`（当前 `OUT65`）。
- 五层货架使用 `CTU01`，目标只能在现场配置的 `bin_rack_positions` 中选择（当前 `KT16`、`KT17`）。
  按计划顺序预选；没有对应工作位的资源必须人工选择，不将多余资源静默映射到最后一个工作位。
- 后端按计划角色校验模板、目标位置和面向，不能只依赖页面默认值；未确定角色的资源不得按五层货架兜底。
- 保留现有请求身份、内容漂移冲突、重复成员保护及资源围栏。ACK 或创建成功不等于货架到位。

示例：`610007 / "90" → F01 / OUT65`；`510002 / "90" → CTU01 / KT16`；
`510012 / "270" → CTU01 / KT17`。同一时刻能否执行仍由现有资源准入控制。

联调页面第 4 步仍只创建进场 Transport。`manual-picking` 工作线业务在面级结束后，按已应用计划与当前权威位置
创建五层来源架 `CTU02` 换面或经 WMS `departure_decide READY` 后创建 `CTU03 / RACK → ZONE` 离场；
转运货架回库与 `CK04` 换面仍属独立合同。代码接入不代表 WMS/ECS 已接收或现场货架已完成物理闭环。

多个五层来源架的进场与前一架 `CTU03` 离场独立：`CTU03 ACCEPTED` 或 `DELIVERY_UNKNOWN` 后，WES 只将前一架在
KT16 的确定投影标为 unknown，不推定离位成功；后一架自己的原进场 Transport 一旦 `SUCCEEDED`，即可继续其当前面流程，
无需等待前一架最终位置回调。前一架若没有最终回调则保持 unknown，原 Transport 身份和对账义务不变。

共享的幂等、物理事实和可靠接收规则见 [Transport 履约合同](../contracts/transport-fulfillment-contract.md)。


## 第 6–7 步：整批料箱投料

- 第 6 步 `outbound.bin.inbound_batch@v1` 一次取得当前货架面完整且最终的料箱清单；空面返回 `RACK_FACE_DONE`。
- 第 7 步从当前 Run 保存的 `inbound_bins` 按顺序每次引用最多 4 箱。页面列出每箱箱号、来源货架、面向和精确储位，目标为配置的投料口（当前 `CNV0301`）。
- 联调动作使用 `MOVE_BINS`，`source={kind:RACK, location_code:来源货架号}` 引用批次，不填写单箱 `bin_code`。
  后端将当前分段的每个 WMS `source_locator` 映射为 `RACK_BIN_SLOT`，生成一个 `BIN_MOVE` TransportTask 的 `moves`，每项目标均为 `HANDOFF_POSITION`。
  联调入口的 `RACK` 仅用于选择已冻结批次；发给 RCS 的每项来源始终是精确货架储位，不是货架搬运。
- 批次完整内容随动作摘要冻结；相同身份的内容漂移拒绝，不允许换身份重复搬运。
  必须等整条 TransportTask 的 `SUCCEEDED` 终态后，才能确认本步骤；创建或接收 ACK 不代表整批已完成。
- 本次范围是整批投料。后续扫码、人工处理和退箱仍按每个实际料箱的既有流程执行；多箱全业务闭环需另行现场验收。
