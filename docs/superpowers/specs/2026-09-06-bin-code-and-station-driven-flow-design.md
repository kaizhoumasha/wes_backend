# 料箱编码统一与扫码驱动流程简化 SPEC

日期：2026-09-06
状态：已形成实施 SPEC；用户已确认的命名及简化原则作为输入，架构替换按本文验收门禁执行；不表示代码已完成、WMS 已联合确认或现场已验证。

当前进度：Operation 基线 `fa89416d` 已统一 WMS `bin_code` 并删除 NG 出口 operation，三份业务合同已明确 NG 独立分支。
内部 Transport 料箱成员改名、BinExecution 退役及相关插件业务仍待实施；下文 S1/S2 的整体验收尚未闭合，不能视为全部待从零实施。
本次 SHIP 未部署至联调服务器，WMS 联合确认与现场验收仍待执行。

> 执行入口：使用 `wes-implementation`，按项目 AGENTS.md 的 Execution Lock、影响分析、测试所有权和最终门禁推进。
> 本文采用 writing-plans 的任务与验收组织方式，按项目规则不粘贴完整实现或测试代码，不要求逐阶段重复确认、自动提交或部署。

## 1. 目标与范围

以扫码得到的 `bin_code` 作为料箱业务标识，取消强制贯穿供箱、线内作业、退箱及 NG 人工取走的 `BinExecution` 生命周期。
插件保存正常业务确实需要的工位等待、作业关联和 FIFO；基础能力继续负责可靠请求、接收、设备命令、搬运及物理证据。

本次包含：

- WMS operation 中表示料箱码的字段、SDK、内部 Transport 料箱成员、对应校验与测试的统一命名。
- 出库与入库 NG 出口上报的删除，以及 NG 从正常业务独立退出的合同。
- BinExecution 消费者退役、位置授权和工作线清场判断解耦。
- 自动出库、人工出库、入库上架三份合同及相关架构真源同步。

不包含：实现三种尚未完成的整线业务、增加扫描排序协议、建立料箱库存主数据、重做 MaterialExecution、
改变 PLC/RCS 私有协议、提交/推送/合并或立即部署。后续统一安排联调服务器验证。

## 2. 输入与真源关系

- [自动出库合同](../../contracts/wms-outbound-picking-task-integration-requirements.md)
- [人工出库合同](../../contracts/wms-manual-outbound-picking-integration-requirements.md)
- [入库上架合同](../../contracts/wms-inbound-putaway-integration-requirements.md)
- [Transport 合同](../../contracts/transport-fulfillment-contract.md)
- [原 Operation 实施计划](2026-09-04-outbound-picking-task-plan-delta-design.md)

本文定义上述文档中料箱命名、全程 BinExecution 与 NG 出口设计的目标替换范围。未涉及的 operation 行为继续沿用原合同。
实施切片必须同时更新对应真源，不能以本文链接长期掩盖旧合同冲突。原 Operation 计划仍承担其他未完成任务，不整篇归档。
已被完整替代的独立过程文档更新引用后移至 `../archive_docs/wes_backend/`，保留原名和内容，禁止覆盖；硬件原始文档保留。

## 3. 不变量

1. WMS 决定任务、库存、分配及业务结果；插件解释流程；扫码器只提供读码/到位事实；设备动作通过基础能力执行。
2. `src/`、SDK 不导入具体插件；业务状态在 `workline_plugins/`，宿主可靠能力可独立安装、运行和测试。
3. 不新建 BinSession、BinVisit、BinContext 或同义全程执行实体，不复制 outbox、幂等、重试、锁或 HTTP 框架。
4. 不保留旧字段别名、双 DTO、兼容 wrapper、旧 operation 路径或 no-op consumer。未发布系统直接替换合同。
5. 业务退出不等于物理完成；已接纳或结果未知的动作保留原身份、证据、资源围栏，直到权威闭合。
6. 数据可重建，不做旧业务数据迁移兼容；schema 变更仍须按项目规则生成 Alembic revision 并验证干净数据库迁移链。

## 4. 料箱标识合同

| 场景 | 目标约定 |
| --- | --- |
| 扫码、WMS 业务请求/响应、FIFO 与 NG 记录 | 使用 `bin_code`，值为实际料箱条码 |
| 人工任务关联 | `task_id + bin_code`；保持现有单业务终态约束，不以新请求身份绕过冲突 |
| WMS operation 请求重试 | 仍为 `(operation, operation_id)`；内容规范化与漂移冲突规则不变 |
| 内部 Transport BinMove / BinExchangePair | `bin_code`、`left_bin_code`、`right_bin_code`；字段值与外部 `container_id` 直接对应 |
| Transport 既有外部线协议 | 保持 `container_id`；不增加 `bin_code` 别名，不改变其通用容器含义 |
| 通用位置 `object_id` | 保持通用字段；对象类型为 BIN 时值为料箱码，不机械改名 |
| 数据库内部主键 | 仅在确有实体和引用需要时保留 ID；不为编码转换新增料箱表 |

WMS 业务 `bin_code` 复用原 Identifier 校验：`[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}`；保留大小写及前导零，
不增加自动 trim、转大写或数字化转换。Transport 保持其既有文本校验边界，不借改名统一不同合同的验证规则。
与码有关的错误信息、重复检查、摘要输入和生成物同步更新。未知字段 `bin_id` 必须按原严格 DTO 规则拒绝。
`bin_execution_id` 是待删除执行关联，不能机械改成 `bin_code` 外键。被删除 NG operation 的字段随 operation 一起删除。

## 5. 三种业务的最小状态

| 流程 | 保留 | 删除/禁止 |
| --- | --- | --- |
| 自动出库 | 已冻结供箱成员对应的 task、实际工作位扫码、Cell 工作计划和必要进度 | 因经过每个扫码点而创建/推进全程 BinExecution；仅凭条码猜 task |
| 人工出库 | point2 当前等待的 task、bin_code、准入决定、最终结果及释放命令关联 | 以 BinExecution 为回调应用前提；等待 NG 人工取走才结束正常业务 |
| 入库上架 | 当前可投料目标箱、真实工位占用、未完成 placement/Cell 分配与释放决定 | 用 WMS 库存代替当前工位事实；全程箱执行作为可投料的唯一授权 |

已有业务记录能够承接就直接复用。确需持久化当前工位等待时，仅保存能支撑等待与动作关联的字段，
由插件拥有，不引入通用工位工作流引擎。不扫描全部 Evidence JSON 重建当前业务状态。

人工 `work_admission_decide` 仍由实际 `bin_code + scanned_at` 请求；`WORK_REQUIRED` 冻结 task 与当前工位关联。
`work_completed` 使用 `task_id + bin_code` 匹配当前等待，重复已应用结果返回原处理结果，不重复创建释放命令。
不存在当前等待不能放行另一只箱。人工完成的业务应用反馈职责保留，不与 NG 出口上报混同。

自动出库 `work_plan` 保留 `task_id + bin_code + scanned_at`，task 来自冻结供箱关联。
取消 BinExecution 不授权将未匹配搬运成员直接当作另一任务的正常料箱。不能读码时不得伪造料箱码或猜测批次顺序。

入库 SCAN1/SCAN2 在合同规定的业务判断处请求 WMS；SCAN3 执行已有正常/NG 决定，SCAN4 记录实际退箱入队。
保留库存主账所需的 `SUPPLY_PLACED`、`RETURN_PLACED` 事实职责，其 payload 删除内部 BinExecution 主键；
沿用既有 operation、搬运与业务关联，不另造料箱生命周期身份。

## 6. NG 分支

- 判定 NG 后立即结束该料箱的正常业务处理，保存实际读码结果、原因、扫码 Evidence、原业务决定及相关设备命令关联。
- 不可读码允许 NG 记录缺少 `bin_code`；不得填充 expected code 冒充实际码。
- 必要滚筒分流通过 DeviceCommand 完成。记录 NG 不代表命令发送成功，更不代表物理退出。
- 删除 `outbound.bin.ng_exit_report@v1` 和 `putaway.target_bin.ng_exit_report@v1` 的设计、实现与专属测试。
- WMS 人工业务无需等待 WES NG 出口报告；若 WMS 需要获知本地 NG 原因，必须明确复用的既有交互，不能假定它会自动收到本地日志。
- 下游需要继续分流时读取当前物理处理所关联的已保存 NG 决定，不重新做正常业务判断；禁止按该箱历史上曾 NG 就永久分流。
- 不新增 NG 人工取走作为正常业务完成门禁；实际 NG 通道的未决动作和占用按设备事实独立处理。

## 7. FIFO 与物理动作身份

正常 RETURN_BUFFER 以同一 WorkLine 活动 Epoch 下的实际物理队列为范围。已确认 SCAN N+3 能证明料箱进入缓存且可取走，
收到有效扫码后可靠入队，按请求抵达 WES 的顺序冻结队列。任务字段只作业务关联，不产生可绕过同一物理队头的独立队列。
入库原按 putaway_execution_id 拆队的条款同步收敛；业务准入仍由 WMS 决定，不允许混用不同物理缓存的顺序。

复用入口已有可靠接收身份和顺序保存能力，不增加设备全局序列、时钟校正、乱序重排或缓存位租约。
同一消息重投不重复入队；不能以永久 `bin_code` 唯一约束禁止该箱后续正常再次进入。
WMS READY 只能选连续队首并冻结成员与目标；NO_BATCH/WAIT 不跳队；READY、ACK 不等于队列物理退出或回库成功。
队列退出按现有匹配成员的权威物理结果闭合，不再附加 BinExecution 关闭条件。

同一次到位的重复扫码不得创建第二个放行/分流命令。命令身份关联当前待处理动作及物理阶段，复用原命令，
不直接用每个新扫码消息 ID 生成新动作，也不把 bin_code 单独当作跨多次经过的永久命令身份。
工位保存待处理命令关联；只有权威离位/释放事实才能结束该次动作。若现有设备事件无法判定这条边界，按第 10 节处理。

## 8. BinExecution 退役后的基础边界

- 删除模型、Repository、Service、导出、SDK 专属关联以及 WmsConfirmation 的 Bin owner 分支/约束。
- WMS 请求可靠 owner 复用真实现存的任务或 Epoch；人工无 task 的准入可由当前 Epoch 承担，业务关联仍由插件核对。
  不为删除 Bin owner 建新 owner registry；零插件测试不证明人工业务完成。
- 位置投影保持 object_type/object_id、WorkLine/Epoch、来源证据及 Transport 关联，取消 BIN 必须有 BinExecution 的约束。
  写入依然校验活动 Epoch、匹配的冻结对象和权威来源，不能退化为任意上报即可改位置。
- Transport authority 保留现有 WorkLine/Epoch 与任务冻结成员校验，删除 BinExecution 外键；资源冲突继续按实际被搬运对象判断。
- 业务关闭不自动删除尚有效的物理位置，也不释放结果未知动作资源。旧位置不能在下一次动作后继续冒充当前权威位置。
- WorkLine 切换检查以未决请求、命令、搬运、业务等待、队列及需清场的真实占用为依据，删除活动 BinExecution 计数。
  不能把“请求全部关闭”或“暂时没有命令”当作线体已空；复用现有清场/插件准入边界，不新增跨 Epoch 接管机制。

## 9. 实施切片与验收

各切片依赖顺序：S0 → S1 → S2 → S3 → S4。S1/S2 可形成独立已验证变更；S3 受第 10 节相关事实门禁约束。
不得在 S1/S2 完成后宣称 BinExecution 已退役或完整业务已实现。

### S0：冻结消费者与合同清单

- [ ] 固定 HEAD、staged/unstaged/untracked 指纹，保护当前大范围未提交工作。
- [ ] 枚举 bin_id、BinExecution、bin_execution_id、两个 NG operation 的生产调用、SDK、插件、fixture、生成物与 HEAVY mapping。
- [ ] 对生产符号执行 GitNexus upstream impact；不可用时明确记录精确 rg/调用链降级证据。
- [ ] 将每个消费者归入“改名、现有能力承接、删除、真实物理事实待核实”，不能靠运行整目录失败发现调用点。

验收：清单覆盖本文第 11 节入口，列清每个被删不变量的承接者和测试 owner；确认第 10 节仅阻塞相关切片。

### S1：一次性统一 bin_code

- [ ] 同时修改同一合同的 wire、typed intent/outcome、SDK facade、Adapter、内部调用及直接/间接测试。
- [ ] 先用合同测试证明 bin_code 可用、旧 bin_id 被拒绝、原编码原样保留，再实施替换。
- [ ] 更新内部 Transport 成员名称，验证 container_id 外部 payload、资源互斥和结果成员匹配语义不变。
- [ ] 同步 OpenAPI、mock、operation inventory 与当前 WMS 示例；检查字段改名后的摘要和重试一致性。

验收：相关 WMS 字段只剩 bin_code；通用 object_id/container_id 和真实内部主键按白名单保留；没有兼容输入/双路径。

### S2：删除 NG 出口义务

- [ ] 删除出库 ng_exit wire/typed/adapter、facade、静态分派、owner 特例、OpenAPI 和专属测试/HEAVY 引用。
- [ ] 同步入库与人工合同，去掉 NG 出口上报及人工取走绑定正常业务完成的条款。
- [ ] 已有插件 NG 行为按 TDD 改为独立分支；尚未实现的插件只修订合同，不虚构业务实现或为文档编写测试。
- [ ] 保留共享未知 operation 拒绝、可靠设备命令与业务应用反馈的有效覆盖，不为废弃 operation 建立永久兼容测试。

验收：两个 NG operation 在可执行树及当前接口清单不存在；NG 判定不触发正常后续作业，未决设备命令仍保留。

### S3：解除物理授权耦合并退役 BinExecution

- [ ] 先建立位置授权、重复扫码动作身份、未决资源、Epoch 切换的承接行为测试，再删除原 BinExecution 测试。
- [ ] 同一内聚变更中修改 position/Transport authority、confirmation owner、WorkLine 未完成快照及插件当前业务关联。
- [ ] 移除 BinExecution 全套实现和约束，不保留空壳、旧 import、旧枚举、临时兼容入口。
- [ ] 通过仓库命令生成 migration；验证干净逻辑库迁移链、约束和受影响真实持久化路径。

验收：没有 BinExecution 运行依赖；缺少授权、错误对象/工位、未知物理结果仍不能放行或切换；基础可零插件独立验证。

### S4：最终验证与联调交付清单

- [ ] 同步三份业务合同、SRS、最小执行架构、原 Operation 计划、file_index 与生命周期索引；移出完整过期文档。
- [ ] 完成测试所有权清单、残留扫描、diff 检查和唯一一次主 Review/反馈闭环。
- [ ] 对最终可执行快照运行必需 QUALITY、selector 选中的 HEAVY 和迁移验证；worker wiring 改动用真实 worker 验证。
- [ ] 形成联调包清单：WMS 新字段示例、拒绝旧字段、重试身份、已删除 operation、schema 变更、待验证插件与设备事实。

验收：明确区分代码/测试/部署/业务验收；不把本地通过或文档完成报告为已部署。部署留待统一联调安排。

## 10. 事实门禁与明确失败行为

| 需核实事实 | 核实方法及不满足时的处理 |
| --- | --- |
| 同次重复扫码与下一次正常经过如何区分 | 查当前设备事件和命令完成合同；复用已存在的到位/离位关联。缺少事实时不猜时间窗、不增加软件序列，阻塞对应站点动作身份替换 |
| NG 跨点/跨线如何携带当前处置 | 查既有插件记录与设备路由；必须能关联当前处理而非历史同码。不能关联时不自动分流，阻塞该业务激活 |
| 切换时如何证明线内及缓存已清场 | 核实已有占用、队列和清场证据。缺失时保持禁止切换，不以零请求替代 |
| 本地判定 NG 是否要求另行通知 WMS | 三份合同明确 WMS 何时已知决定；若有未覆盖的通知需求，先明确既有交互承接，不恢复 ng_exit_report 作为隐式兜底 |

这些是执行边界所需事实，不授权为小概率情况建设长期补偿框架。不阻塞已确定的字段改名与废弃接口清理。
实施者先从现有合同/代码核实，仍缺少时仅提出缺失事实，不重复请求已授权范围的分阶段批准。

## 11. 文件与测试入口

以下是已核实的入口，不代替 S0 对间接消费者的完整枚举。

| 范围 | 实现/合同入口 | 测试主要 owner |
| --- | --- | --- |
| WMS 业务字段、NG 接口 | `src/app/wms_adapter/outbound_picking/`、`src/app/wms_adapter/confirmation_adapter.py`、`src/wes_plugin_sdk/src/wes_plugin_sdk/wms_operations.py` | `tests/contracts/wms_adapter/outbound_picking/`、`tests/integration/wms_adapter/outbound_picking/`、`tests/runtime/execution/test_plugin_sdk_wms_operations.py` |
| Bin 生命周期退役 | `src/app/execution/models/bin_execution.py`、`repositories/bin_execution_repository.py`、`services/bin_execution_service.py`（后两者相对同一 execution 根） | `tests/runtime/execution/test_bin_execution.py`，删除前归属到承接行为 |
| 位置与 Transport 授权 | `src/app/execution/models/position_projection.py`、`src/app/execution/services/position_projection_service.py`、`src/app/transport/models.py`、`src/app/transport/contracts.py` | `tests/runtime/execution/test_position_projection.py`、`tests/runtime/transport/test_transport_execution_authority.py` 及映射的持久化测试 |
| 可靠 owner 与切换 | `src/app/execution/services/wms_confirmation_service.py`、`src/app/workline/repositories/workline_repository.py` | `tests/runtime/execution/test_wms_confirmation_service.py`、`tests/integration/workline_capabilities/test_unfinished_execution_snapshot_postgresql.py` |
| 插件业务、动作关联 | `workline_plugins/manual_bin_processing/`；其他插件仅按真实消费者纳入 | 插件各自 `tests/`；不进入核心默认 QUALITY/覆盖率 |
| 测试治理与 schema | `tests/README.md`、`docs/architecture/heavy-test-impact.toml`、`migrations/versions/` | 精确 selector、迁移链与选中 HEAVY；不扩大到无关整仓测试 |

文档本身只做路径/引用、结构和 diff 检查，禁止新增正文测试。行为实现按高风险 TDD 推进。

## 12. 完成判据

- [ ] WMS 料箱业务字段为 bin_code；Transport 对外仍为 container_id；无旧字段兼容。
- [ ] BinExecution 全部实际职责已有必要且唯一的承接者，模型、外键、服务、owner、测试和文档残留闭合。
- [ ] NG 是独立分支，无 NG 出口 operation，无等待 WMS 人工业务完成的正常执行尾巴。
- [ ] 重复扫码/回调不重复动作；同箱下一次合法进入可正常处理；未知物理动作不丢失身份和围栏。
- [ ] 真实 RETURN_BUFFER FIFO 不按任务绕队；READY 不被解释成物理完成。
- [ ] 基础与插件分别验证；原 prepare、issued、Transport 的受影响回归通过。
- [ ] 第 10 节未核实部分明确保留为未激活边界；没有把未实现的三种整线业务报告为完成。
- [ ] 无双通道、同义替代实体、过期当前态文档或未授权 Git/部署动作。
