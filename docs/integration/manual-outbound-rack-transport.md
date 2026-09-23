# 人工出库货架搬运规则

2026-09-10 现场确认。货架角色来自已应用的
`plan_delta`，不得根据货架号前缀或位置编码猜测。

| 计划资源 / 货架类型 | 动作 | RCS 模板 | 源位置 | 目标位置 |
| --- | --- | --- | --- | --- |
| `target_rack` / 转运货架 | 出库搬运 | `F01` | `RACK`，货架号 | `RACK_POSITION` |
| `target_rack` / 转运货架 | 换面 | `CK04` | `RACK`，货架号 | 当前实际所在的 `RACK_POSITION` |
| `target_rack` / 转运货架 | 离场搬运 | `F01` | `RACK`，货架号 | WMS `departure_decide.READY` 的 `ZONE \| RACK_POSITION` |
| `bin_source_racks` / 五层货架 | 出库搬运 | `CTU01` | `RACK`，货架号 | `RACK_POSITION` |
| `bin_source_racks` / 五层货架 | 换面 | `CTU02` | `RACK`，货架号 | 当前实际所在的 `RACK_POSITION` |
| `bin_source_racks` / 五层货架 | 离场搬运 | `CTU03` | `RACK`，货架号 | WMS `departure_decide.READY` 的 `ZONE \| RACK_POSITION` |
| WorkLine drain 货架 | 离场搬运 | `CTU03` | `RACK`，货架号 | WMS `departure_decide.READY` 的 `ZONE \| RACK_POSITION` |

`rack_id` 与 `RACK.location_code` 必须一致。面向是非空不透明字符串，`"90"`、`"270"` 原样传递，
不进行数值转换或去空格。换面不得根据计划目标推断当前位置；回库目标遵循 WMS 的权威决定。

`manual-picking` 工作线业务以全部 inbound 分段及成员权威成功、结果发布和交接位正确作为
`feed_complete`，不等待扫码、业务完成或后续回架。已有相关义务闭合后，同架下一面立即创建 CTU02；所有面投料完成后先请求
`departure_decide`，READY 后按 WMS 原样 destination 创建唯一 CTU03。当前 PickingTask 获 WMS 完成确认且转运架原进场成功、仍在当前工作位时，可请求转运架
`departure_decide`；请求不等待五层架 CTU03 返回终态，`READY` 后创建 `F01` 到 WMS 决定的原目的地。
转运货架 `CK04` 换面仍属独立合同。代码接入不代表 WMS/ECS 已接收或现场货架已完成物理闭环。

多个五层来源架的进场与前一架 `CTU03` 离场独立：`CTU03 ACCEPTED` 或 `DELIVERY_UNKNOWN` 后，WES 只将前一架在
KT16 的确定投影标为 unknown，不推定离位成功；后一架自己的原进场 Transport `SUCCEEDED`，
且成功成员与绑定工作位的精确 rack/face 投影匹配后，即可继续其当前面流程，无需等待前一架最终位置回调。前一架的原 CTU03 成功回调若给出指定区域内的实际 `RACK_POSITION`，该最终位置是其权威终态并更新投影；
若没有最终回调则保持 unknown，原 Transport 身份和对账义务不变。

工作线自动 RackCycle 对每个确定来源架或 WMS drain reservation 以稳定步骤身份最多创建一次 CTU01，不以 `capacity`、历史任务或离场状态扣减物理准入名额。RCS 排队和自主进位；每个架的后续作业仍等待该架自己的权威到位、面向及原 Transport 结果。CTU02 成功表示旋转后已经回到工作位。

共享的幂等、物理事实和可靠接收规则见 [Transport 履约合同](../contracts/transport-fulfillment-contract.md)。
