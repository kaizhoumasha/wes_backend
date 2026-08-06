# ADR: WES/WMS/RCS 运行时资源边界

## 状态

Accepted - 2026-05-13；阶段所有权于 2026-08-06 澄清

## 背景

WES 需要保存作业期执行事实与资源投影，但不能把本地快照升级为 WMS 库存主账，也不能把经 WMS 转发的
RCS 动作混入 WMS 业务 ACL。若三类职责混在同一 Adapter，库存权威、运输履约和 WES 本地执行会形成重复所有者。

## 决策

1. WMS 是库存、预留、扣减、账务、SAP 同步和业务任务的唯一权威。
2. WES 可以持久化执行事实、过程快照、资源关系投影与冲突证据；这些事实不能作为库存可用性、库存属性交换、
   库存扣减或资源授权的本地主账。
3. Phase 3 只建设无状态、消费者驱动的 WMS 业务 ACL：
   - 读取真实消费者需要的 WMS 权威业务事实；
   - 发送真实消费者需要的 WES→WMS 业务确认；
   - 标准化 WMS→WES 业务命令 DTO；
   - 每次调用只发送一次并返回封闭结果，不拥有数据库、evidence、重试、breaker 或生命周期。
4. AGV/CTU 的搬运、交换、旋转、入线、退线及履约状态属于 Phase 4 `Transport Port`。WMS 只是当前 RCS
   网络转发入口；Phase 4 的 WMS 转发 RCS Adapter 直接消费 Phase 2 HTTP Transport，不经过 Phase 3 WMS 业务 ACL。
5. `TransportTask` 拥有运输意图、内部关联、超时、结果证据和状态推进。ACK、已受理或已下发都不是完成事实；
   只有 Phase 4 Transport 合同定义的终态结果才能推进等待中的运输动作。
6. WMS 普通业务 callback 与运输终态入口分离。普通 callback 不能直接终结 `TransportTask`，也不能绕过具体对象
   owner 修改执行对象或投影。
7. 满箱交换、生产发料、Pick_Fail、退料和入库确认都不得由 WES 本地改库存。WES 只记录执行事实并通过对应
   业务确认或 Transport 意图请求外部权威处理。

## 端口与证据所有权

| 交互 | 阶段与 owner | WES 边界 |
| --- | --- | --- |
| WMS 权威业务查询 | Phase 3 WMS 业务 ACL | 同步读取一次；不跨请求缓存为主账 |
| WES→WMS 业务确认 | Phase 3 WMS 业务 ACL + 具体业务义务 owner | ACL 只翻译一次调用；消费者拥有可靠性 |
| WMS→WES 业务命令 | Phase 3 inbound DTO/normalizer + 具体业务 owner | normalizer 不执行编排或持久化 |
| AGV/CTU 搬运与状态 | Phase 4 `TransportTask` + `Transport Port` + WMS 转发 RCS Adapter | 不经过 Phase 3，不复制 RCS 实时位置或 SDK 状态 |
| ECS 设备动作 | `DeviceCommand` + ECS Adapter | 不与 Transport 或 WMS 确认共用通用状态机 |
| 作业期资源投影 | 对应执行对象 + projection writer | 只由已校验的终态 evidence 推进 |

## 后果

- `docs/contracts/wms-northbound-interaction-contract.md` 只冻结 Phase 3 WMS 业务交互，不再承载运输协议。
- Phase 4 必须单独冻结 WMS 转发 RCS 的 Transport 合同；不得从厂商历史编号或旧 WMS integration 实现推定目标 wire。
- Phase 3 不创建 `TransportTask`、`WmsConfirmation`、数据库模型、迁移、repository、evidence 表或通用熔断器。
- 任何实现计划若要求 WES 本地判断库存可用性、空箱授权或库存扣减，必须先提出新的 ADR。

## 验收

- SRS 继续保留原始业务需求，仅在架构说明处明确 WMS、WES 与 RCS 的 owner。
- Phase 3 合同中的每个 operation 都能追溯到真实消费者，并已由 WMS 批准 method/path/DTO/拒绝语义。
- Phase 3 生产包中不存在 Transport、AGV、CTU、RCS、状态轮询、持久化或可靠生命周期实现。
- Phase 4 Transport 测试与 Phase 3 WMS 业务 ACL 测试互不替代。
