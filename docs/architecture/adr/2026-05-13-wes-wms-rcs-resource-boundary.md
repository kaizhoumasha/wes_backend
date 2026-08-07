# ADR: WES/WMS/RCS 运行时资源边界

## 状态

Accepted - 2026-05-13；阶段所有权于 2026-08-07 澄清

## 背景

WES 需要保存作业期执行事实与资源投影，但不能把本地快照升级为 WMS 库存主账，也不能把经 WMS 转发的
RCS 动作混入 WMS HTTP Client。若访问封装、业务合同和执行职责混在同一 Adapter，库存权威、运输履约和 WES 本地执行会形成重复所有者。

## 决策

1. WMS 是库存、预留、扣减、账务、SAP 同步和业务任务的唯一权威，并负责给出全部业务资格、来源、目标、
   优先级、路线、NG/等待/替代、取消、恢复和业务终态结果。
2. WES 可以持久化执行事实、过程快照、资源关系投影与冲突证据；这些事实不能作为库存可用性、库存属性交换、
   库存扣减或资源授权的本地主账。
3. Phase 3 只建设无状态 WMS HTTP Client：统一 origin、GET/POST、query、headers、JSON 编解码、传输事实和关闭；
   不定义任何具体 WMS 业务 API、业务 Port、业务 DTO 或业务结果解释。具体能力随真实业务逐项实现并复用 Client。
4. AGV/CTU 的搬运、交换、旋转、入线、退线及履约状态属于 Phase 4 `Transport Port`。WMS 只是当前 RCS
   网络转发入口；Phase 4 的 WMS 转发 RCS Adapter 可复用 Phase 3 Client，但业务语义完全属于 Phase 4。
5. `TransportTask` 拥有运输意图、内部关联、超时、结果证据和状态推进。ACK、已受理或已下发都不是完成事实；
   只有 Phase 4 Transport 合同定义的终态结果才能推进等待中的运输动作。
6. WMS 普通业务 callback 与运输终态入口分离。普通 callback 不能直接终结 `TransportTask`，也不能绕过具体对象
   owner 修改执行对象或投影。
7. 满箱交换、生产发料、Pick_Fail、退料和入库确认都不得由 WES 本地改库存。WES 只记录执行事实并通过对应
   业务确认或 Transport 意图请求外部权威处理。
8. WES 只做执行决策：校验 WMS 结果的合同、关联、版本、时效和物理可执行性，并基于设备状态、并发、deadline、
   安全与终态证据决定等待、发送、暂停、隔离或对账。WES 不得重算或替换 WMS 给出的业务结果；结果缺失、过期、
   矛盾或不可执行时必须 fail closed。

## 端口与证据所有权

| 交互 | 阶段与 owner | WES 边界 |
| --- | --- | --- |
| WMS 权威业务查询 | 对应业务模块 + Phase 3 `WmsClient` | 同步读取一次；不跨请求缓存为主账 |
| WMS 业务决策结果 | 对应业务模块 + 具体执行 owner | 业务模块翻译；执行 owner 只映射为执行意图，不改变业务语义 |
| WES→WMS 业务确认 | 具体业务义务 owner + 对应业务模块 + `WmsClient` | Client 只发送一次；消费者拥有可靠性 |
| WMS→WES 业务命令 | 对应业务 ingress + 具体业务 owner | ingress 不越权执行编排或持久化 |
| AGV/CTU 搬运与状态 | Phase 4 `TransportTask` + `Transport Port` + WMS 转发 RCS Adapter | 可复用 Client，但不把业务语义放入 Phase 3 |
| ECS 设备动作 | `DeviceCommand` + ECS Adapter | 不与 Transport 或 WMS 确认共用通用状态机 |
| 作业期资源投影 | 对应执行对象 + projection writer | 只由已校验的终态 evidence 推进 |

## 后果

- `docs/contracts/wms-northbound-interaction-contract.md` 只冻结 Phase 3 WMS Client 使用标准，不承载具体业务或运输协议。
- Phase 4 必须单独冻结 WMS 转发 RCS 的 Transport 合同；不得从厂商历史编号或旧 WMS integration 实现推定目标 wire。
- Phase 3 不创建具体业务 API、业务 Port、`TransportTask`、`WmsConfirmation`、数据库模型、迁移、repository、evidence 表或通用熔断器。
- 任何实现计划若要求 WES 本地判断库存可用性、来源、目标、优先级、路线、NG/等待/替代、空箱授权或业务终态，
  均违反本 ADR；不得通过新增本地规则、兼容模式或通用决策引擎绕过 WMS。

## 验收

- SRS 继续保留原始业务需求，仅在架构说明处明确 WMS、WES 与 RCS 的 owner。
- Phase 3 只验证 Client 访问合同，不等待或验证任何具体 operation。
- Phase 3 生产包中不存在业务 API、业务 Port、AGV、CTU、RCS、状态轮询、持久化或可靠生命周期实现。
- Phase 4 Transport 业务测试与 Phase 3 WMS Client 访问测试互不替代。
