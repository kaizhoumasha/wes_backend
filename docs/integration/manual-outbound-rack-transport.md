# 人工出库货架搬运规则

2026-09-10 现场确认。货架角色来自已应用的
`plan_delta`，不得根据货架号前缀或位置编码猜测。

| 计划资源 / 货架类型 | 动作 | RCS 模板 | 源位置 | 目标位置 |
| --- | --- | --- | --- | --- |
| `target_rack` / 转运货架 | 出库搬运 | `F01` | `RACK`，货架号 | `RACK_POSITION` |
| `target_rack` / 转运货架 | 换面 | `CK04` | `RACK`，货架号 | 当前实际所在的 `RACK_POSITION` |
| `target_rack` / 转运货架 | 离场搬运 | `F01` | `RACK`，货架号 | WMS `departure_decide.READY` 的 `ZONE \| RACK_POSITION` |
| `added_direct_picks` / 退料货架 | 进场搬运 | `F01` | `RACK`，货架号 | `RACK_POSITION` |
| `added_direct_picks` / 退料货架 | 离场搬运 | `F01` | `RACK`，货架号 | WMS `departure_decide.READY` 的 `ZONE \| RACK_POSITION` |
| `bin_source_racks` / 五层货架 | 出库搬运 | `CTU01` | `RACK`，货架号 | `RACK_POSITION` |
| `bin_source_racks` / 五层货架 | 换面 | `CTU02` | `RACK`，货架号 | 当前实际所在的 `RACK_POSITION` |
| `bin_source_racks` / 五层货架 | 离场搬运 | `CTU03` | `RACK`，货架号 | WMS `departure_decide.READY` 的 `ZONE \| RACK_POSITION` |
| WorkLine drain 货架 | 离场搬运 | `CTU03` | `RACK`，货架号 | WMS `departure_decide.READY` 的 `ZONE \| RACK_POSITION` |

同一货架的一次进出场生命周期必须配对：`CTU01 → CTU03`，`F01 → F01`；按已应用的计划资源角色选择模板，不得在离场时换用另一组模板。

`rack_id` 与 `RACK.location_code` 必须一致。面向是非空不透明字符串，`"90"`、`"270"` 原样传递，
不进行数值转换或去空格。换面不得根据计划目标推断当前位置；回库目标遵循 WMS 的权威决定。

`manual-picking` 工作线业务以全部 inbound 分段及成员权威成功、结果发布和交接位正确作为
`feed_complete`，不等待扫码、业务完成或后续回架。已有相关义务闭合后，同架下一面立即创建 CTU02；所有面投料完成后先请求
`departure_decide`，READY 后按 WMS 原样 destination 创建唯一 CTU03。当前 PickingTask 获 WMS 完成确认且转运架原进场成功、仍在当前工作位时，可请求转运架
`departure_decide`；请求不等待五层架 CTU03 返回终态，`READY` 后创建 `F01` 到 WMS 决定的原目的地。
转运货架 `CK04` 换面仍属独立合同。代码接入不代表 WMS/ECS 已接收或现场货架已完成物理闭环。

退料货架的**整架任务完成 API 尚未由 WES 实现**；WMS 已接受目标 wire，见
[自动拣料 WMS 联合确认清单中的请求/响应示例](wms-joint-confirmation-automatic-picking.md#61-退料货架整架任务完成-apiwms-已确认wes-待实施)。现有
`outbound.manual_rack.direct_pick_completed@v1` 只结清指定任务、revision 的直接取料货架面；
`outbound.picking_task.completion_confirm@v1` 只确认 PickingTask；`outbound.rack.departure_decide@v1`
只决定当前货架的离场去向。三者均不自动形成整架任务完成事实，`CTU03`/`F01` 接纳或
`departure_decide.READY` 也不能充当货架已完成物理离场的结果。WES 接线完成前，不按计划成员全部结清、
PickingTask `COMPLETED` 或离场请求已发送自行推定该 API 已交付。

多个五层来源架的进场与前一架 `CTU03` 离场独立：`CTU03 ACCEPTED` 或 `DELIVERY_UNKNOWN` 后，WES 只将前一架在
KT16 的确定投影标为 unknown，不推定离位成功；后一架自己的原进场 Transport `SUCCEEDED`，
且成功成员与绑定工作位的精确 rack/face 投影匹配后，即可继续其当前面流程，无需等待前一架最终位置回调。前一架的原 CTU03 成功回调若给出指定区域内的实际 `RACK_POSITION`，该最终位置是其权威终态并更新投影；
若没有最终回调则保持 unknown，原 Transport 身份和对账义务不变。

基础层按目标点 `capacity` 对五层来源架及同目标点的 drain rack 维护同一个滚动 CTU01 下发窗口；按物理货架进场生命周期占用名额，到位仍占用，对应离场 Transport `ACCEPTED` 后释放并补发 pending。跨 revision 新成员关联仍在占窗的同架进场生命周期，不重复下发 CTU01。RCS 负责实际排队和自主进位；每个架的后续作业仍等待该架自己的权威到位、面向及原 Transport 结果。CTU02 成功表示旋转后已经回到工作位。

进场明确未接纳或失败且权威终位在目标点外时释放窗口；失败终位仍在目标点的重试沿用原名额。离场明确未接纳后重新请求 WMS `departure_decide`。已接纳离场任务失败且留在目标点、但缺少当前面向时，恢复 API 的权威面向来源尚未确认，WES 不推测面向；见 [Transport 履约合同 §5.3](../contracts/transport-fulfillment-contract.md#53-搬运最终结果)。

共享的幂等、物理事实和可靠接收规则见 [Transport 履约合同](../contracts/transport-fulfillment-contract.md)。
