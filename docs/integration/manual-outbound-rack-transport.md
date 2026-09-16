# 人工出库联调：货架搬运规则

2026-09-10 现场确认。适用于 `/ops/manual-outbound-integration` 的计划资源；货架角色来自已应用的
`plan_delta`，不得根据货架号前缀或位置编码猜测。

| 计划资源 / 货架类型 | 动作 | RCS 模板 | 源位置 | 目标位置 |
| --- | --- | --- | --- | --- |
| `target_rack` / 转运货架 | 出库搬运 | `F01` | `RACK`，货架号 | `RACK_POSITION` |
| `target_rack` / 转运货架 | 换面 | `CK04` | `RACK`，货架号 | 当前实际所在的 `RACK_POSITION` |
| `target_rack` / 转运货架 | 离场搬运 | `F01` | `RACK`，货架号 | WMS `departure_decide.READY` 的 `ZONE \| RACK_POSITION` |
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

联调页面第 4 步仍只创建进场 Transport。`manual-picking` 工作线业务以全部 inbound 分段及成员权威成功、结果发布和交接位正确作为
`feed_complete`，不等待扫码、业务完成或后续回架。已有相关义务闭合后，同架下一面立即创建 CTU02；所有面投料完成后
直接创建 `CTU03 / RACK → ZONE WH01` 离场，使用原计划 Evidence
冻结唯一 Transport 身份。当前 PickingTask 获 WMS 完成确认且转运架原进场成功、仍在当前工作位时，可请求转运架
`departure_decide`；请求不等待五层架 CTU03 返回终态，`READY` 后创建 `F01` 到 WMS 决定的原目的地。
转运货架 `CK04` 换面仍属独立合同。代码接入不代表 WMS/ECS 已接收或现场货架已完成物理闭环。

多个五层来源架的进场与前一架 `CTU03` 离场独立：`CTU03 ACCEPTED` 或 `DELIVERY_UNKNOWN` 后，WES 只将前一架在
KT16 的确定投影标为 unknown，不推定离位成功；后一架自己的原进场 Transport `SUCCEEDED`，
且成功成员与绑定工作位的精确 rack/face 投影匹配后，即可继续其当前面流程，无需等待前一架最终位置回调。前一架的原 CTU03 成功回调若给出指定区域内的实际 `RACK_POSITION`，该最终位置是其权威终态并更新投影；
若没有最终回调则保持 unknown，原 Transport 身份和对账义务不变。

工作线自动 RackCycle 按绑定 FIVE_LAYER/FIVE_RACK 点位 `capacity` 补足 CTU01 窗口，物理当前架仍最多一个，由 RCS 排队和自主进位。
CTU02 不释放窗口，匹配 CTU03 已接纳才释放；接纳前 DELIVERY_UNKNOWN/CONFLICT 继续占窗，后续 RECONCILING 以
`result_deadline_at` 证明此前接纳。CTU02 成功表示旋转后已经回到工作位。上述工作线规则不将联调页面手动动作扩展为自动队列调度。

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
