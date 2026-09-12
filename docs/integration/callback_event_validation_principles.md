# Callback 入站基础能力边界

> 顶层统一接口（wire）：[`third_party_integration_whitepaper.md`](third_party_integration_whitepaper.md)
>
> 权威架构：[`2026-07-31-wes-minimal-execution-architecture-convergence-design.md`](../superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md)
>
> Transport 合同：[`transport-fulfillment-contract.md`](../contracts/transport-fulfillment-contract.md)

## 1. 文档定位

本文只定义 WES 核心回调入口（callback ingress）的共享基础能力。所有供应商必须适配白皮书规定的统一公共包络
（envelope）；设备合同附录拥有具体 `event_type`、`task_type`、`data` 和 `error_detail`，WMS 拥有业务结果，TransportResult
应用端口拥有运输终态分发，WorkLine 插件只验证结果关联并映射执行。各边界不得相互代测或越权解释。

## 2. 分层职责

### 2.1 核心入口（core ingress）

核心只负责：

- 固定 HTTP 路径、方法、请求大小和 JSON 可解析性；
- 校验白皮书定义的公共包络、设备身份、合同身份和部署级唯一稳定事件身份；
- 在 ACK 前持久化原始载荷、来源身份、接收时间、载荷摘要（payload digest）和已确定的命令/WorkLine 关联；
- 处理相同幂等身份的重复或冲突请求；
- 对结果回调校验原命令及其冻结合同身份，并强制每个 `command_code` 只有一个已接纳终态结果；
- 对设备事件按有效设备绑定校验合同及 WorkLine 关联，保留首次证据关联，不因后续配置变化改绑；
- 返回传输层 ACK，并将已持久化的 `InboundEvidence` 交给后续处理。

核心不定义全局 `event_type` 枚举，不解释具体设备业务字段，也不决定业务是否成立。纯局域网目标协议不增加供应商私有
Token、签名、Nonce 或 HMAC 分支；网络隔离和访问控制由部署边界负责。

`ESTOP_PRESSED` 是例外的 ECS 自有安全事实，不属于 WES callback 合同。核心入口在建立 Evidence、解析插件业务或注册任何 wake 之前以统一包络校验错误拒绝；急停记录、物理急停、复位和恢复执行由 ECS 独立负责。

### 2.2 设备合同附录（device contract annex）

每个获批设备附录负责：

- 允许的 `event_type`、`task_type` 和字段闭集；
- `data`、`params` 与 `error_detail` 的类型和语义；
- 设备能力、状态最大观察年龄、完成时限、原始错误证据和一致性验收场景。

供应商或其网关直接实现统一接口，不在 WES 内建立供应商私有适配器（Adapter）、数据传输对象（DTO）别名或动态注册表。

### 2.3 运输结果应用端口（TransportResult application port）

运输结果复用 WMS Event 入口的信封校验、幂等和持久化后应答能力，但不进入普通 WMS 业务事件 Handler：

- 固定 operation 为 `transport.task.resulted@v1`；
- 入口先以 `operation + operation_id` 持久化 `TransportCallbackReceipt`；合法回调在同一事务保存
  `TransportEvidence`，再返回 ACK 并分发类型化 `TransportResult`；
- 应用端口校验 `transport_task_id`、请求版本、对象身份和冻结成员；
- 只有 `TransportTask` owner 可以接受终态并推进相关投影；
- 普通 WMS 业务事件、callback hint、ACK 或远端内部进度都不能终结 `TransportTask`。

TransportResult 不使用设备统一接口的 `command_code`、设备合同身份或设备附录字段。两者只复用基础入站不变量，不共享
业务 DTO 或终态 owner。

### 2.4 WorkLine 插件（WorkLine plugin）

插件只处理已按统一接口和设备合同附录验证、并由核心持久化的证据：

- 校验当前工作线输入和 WMS 结果的关联、版本、时效与物理可执行性；
- 读取注入的只读投影和 WMS 类型化业务结果；
- 将 WMS 结果映射为等待、发送、暂停、隔离或对账等封闭执行决定（Decision）。

扫码数据属于设备合同附录；料盘业务身份、目标料格、业务异常分类和替代来源属于 WMS；物理等待、NG 路由及下一条逻辑
设备动作属于插件执行映射。这些职责都不属于 callback ingress。

## 3. 持久化后应答（ACK-after-persist）

合法输入遵循固定顺序：

1. 完成共享传输检查并校验统一公共包络，取得稳定身份和规范化载荷摘要；
2. 结果回调取得原命令及其冻结合同；设备事件首次观察时原子保存稳定身份、摘要，并按设备有效绑定确定 WorkLine 关联；
3. 校验 `contract_key`/`contract_version`：结果必须匹配原命令冻结值，普通事件必须符合有效设备绑定的合同；
4. 根据获批设备附录校验具体事件字段，并检查结果回调的命令关联和唯一终态；
5. 核心持久化 `InboundEvidence`；普通事件没有有效 WorkLine 关联时保存拒绝证据并返回准入错误，调试 EVENT 按独立调试合同处理；
6. 合法接纳的证据事务提交后同步返回 ACK；
7. `is_debug=true` 的 EVENT 不调用 WorkLine 插件，而是由异步 evidence worker 复用 DeviceCommand 基础能力尝试创建一条
   `EVENT_DEBUG/MOVE_FORWARD` 独立联调命令。新 EVENT 使用自己的身份创建唯一命令，不等待同设备旧命令闭合；同 EVENT 重放复用原命令，正文漂移冲突。创建后将 evidence 标记为不参与业务消费的 `IGNORED`，事务提交后唤醒现有派发扫描；唤醒失败由 Beat 补偿，不回滚 evidence 或命令。普通 EVENT 仅在 WorkLine 当前准入、设备绑定及业务关联校验通过后调用显式装配的插件。

上述顺序只适用于 WES 合同内的合法事件。`ESTOP_PRESSED` 不进入步骤 2–7，即使带有 `is_debug=true` 也不持久化、不 ACK、不唤醒 execution 或 transport-debug。

新 EVENT 不改写、标失败、释放或重发旧 DeviceCommand；旧命令及其 Evidence、对账原因和资源围栏只由匹配的权威 Result Callback 或既有对账事实闭合。WES 不提供 EVENT blocker 查询、人工 reprocess 或设备空闲探测续行入口。

渐进业务接入按[插件顶层设计 §7.13](../superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md#713-声明先行与渐进业务接入)：
普通合法 EVENT 无对应 handler 时仅保存观察证据，不创建业务指令、不无限重试、不认定整线故障；已接入 handler
执行失败仍走原错误路径，不能当成未接入忽略。缺少有效 WorkLine、合同不匹配或身份冲突仍按原准入规则拒绝。
普通观察不使用上述 `is_debug=true` 自动放行路径；原命令 Result Callback 和已有可靠义务不能因业务 handler 缺席被忽略。
增加 handler 不自动重放历史观察事件。此为目标语义，代码实现和验证须单独闭合。

无法建立证据、幂等身份冲突或合同校验失败时不得返回成功 ACK。ACK 成功不表示业务决定成功，更不表示设备动作完成。

## 4. 未知与重复输入

- 已接纳的相同幂等身份、相同摘要：返回首次 ACK，不重复持久化证据或执行插件。
- 明确未接纳的相同幂等身份、相同摘要：保留身份、摘要和首次已确定的关联，再按当前合同执行接纳判断；不得借重传改绑业务。
  首次接纳后持久化唯一证据，事务提交后返回 ACK；业务处理仍须通过当前准入检查。
- 相同幂等身份、不同摘要：拒绝并保存冲突证据。
- 同一 `command_code` 出现不同结果身份、不同摘要或矛盾终态：返回冲突并保存证据，不推进业务对象。
- 未知命令返回 `404 COMMAND_NOT_FOUND`；未知设备、合同版本不匹配、未知附录值或缺失稳定事件身份只保存允许保留的接入
  诊断，不推进业务对象。
- 发送方时间戳只作为不可变证据；时钟偏差不丢弃真实物理回调，需要时间判断时结合接收时间失败关闭或进入人工对账。
- 已 ACK 后的业务失败：保留插件返回的真实稳定原因，不覆盖为通用 schema 错误。

`source_event_id` 在整个 WES 部署范围内跨供应商、设备、结果和事件回调永久不复用。稳定身份一旦与规范化语义载荷绑定，
即使请求被明确拒绝也不得改载荷复用；只有明确未接纳且修正导致摘要变化时，才能使用新的部署级唯一身份重新上报。
设备事件不得因插件或配置变化改写首次证据关联；缺少业务关联时仅保留诊断证据，不满足当前准入时记录错误并拒绝推进。
`is_debug` 是可选严格布尔值并参与规范化摘要；省略与 `false` 等价，`true` 与普通事件不是同一身份。重复调试 EVENT 复用同一
命令身份，不能借重报绕过 ECS 明确未接纳、静态合同失败或结果不明状态。

## 5. 测试所有权

| 测试内容 | 唯一所有者 |
| --- | --- |
| 固定路径、HTTP、大小限制、ACK-after-persist、幂等与证据可靠性 | WES 核心统一接口合同测试 |
| 具体设备字段闭集与供应商实现一致性 | 设备合同附录验收；不作为核心业务测试 |
| WMS 业务 OK/NG/目标结果 | 对应 WMS 业务模块合同测试；共享 Client 测试不得替代 |
| TransportResult 静态分发、终态关联与冲突 | Transport 合同与 WMS Adapter 入站合同测试；普通 WMS Event 测试不得替代 |
| 结果关联、物理执行校验和后续命令映射 | WorkLine 插件测试 |

核心测试不得构造具体工作线成功路径来证明入口能力；插件测试不得替代核心持久化、幂等和传输可靠性测试。
