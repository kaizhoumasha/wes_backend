# 料箱编码、WorkLine 运行入口与扫码驱动流程简化 SPEC

日期：2026-09-06
状态：后端核心退役代码、独立评审与标准 QUALITY/HEAVY 已完成；前端正式合同同步及插件镜像端到端验证尚未闭合，不表示 WMS 已联合确认或现场已验证。

当前进度：Operation 基线 `fa89416d` 已统一 WMS `bin_code` 并删除 NG 出口 operation，三份业务合同已明确 NG 独立分支。
当前工作树已完成内部 Transport 料箱成员改名、BinExecution 与 LineRunEpoch 定义及消费者迁移，以及已有 rough_sorter 业务关联迁移。
历史货架围栏、Transport 结果发布并发及退箱 owner 缓存问题已修复，独立代码评审通过；最终标准 HEAVY 383 项通过、零跳过。
QUIT 后重试接管曾出现间歇性退出超时，最终标准回归通过但原因尚未定位，保留在 S0 证据中。
本轮未提交、推送或部署；S3B 新站点业务、WMS 联合确认与现场验收仍待执行。实施范围和证据见 [S0 清单](../plans/2026-09-06-bin-workline-retirement-s0.md)。

> 执行入口：使用 `wes-implementation`，按项目 AGENTS.md 的 Execution Lock、影响分析、测试所有权和最终门禁推进。
> 本文采用 writing-plans 的任务与验收组织方式，按项目规则不粘贴完整实现或测试代码，不要求逐阶段重复确认、自动提交或部署。

## 1. 目标与范围

以扫码得到的 `bin_code` 作为料箱业务标识，取消强制贯穿供箱、线内作业、退箱及 NG 人工取走的 `BinExecution` 生命周期。
WorkLine 直接指定当前业务插件及运行配置，取消独立 LineRunEpoch 运行代际；只有完全收敛清线后才允许切换插件。
插件保存正常业务确实需要的工位等待、作业关联和 FIFO；基础能力继续负责可靠请求、接收、设备命令、搬运及物理证据。

本次包含：

- WMS operation 中表示料箱码的字段、SDK、内部 Transport 料箱成员、对应校验与测试的统一命名。
- 出库与入库 NG 出口上报的删除，以及 NG 从正常业务独立退出的合同。
- BinExecution 与 LineRunEpoch 消费者退役、位置授权和工作线清场判断解耦。
- 插件、设备及位置绑定收敛到 WorkLine；启动、停用、切换和运行配置修改采用共同准入检查。
- 自动出库、人工出库、入库上架三份合同及相关架构真源同步。

核心退役只迁移已有生产消费者；尚未实现的站点等待、NG 分流和退箱 FIFO 按 S3B 单独验收，不作为核心退役的交付前提。

不包含：实现三种尚未完成的整线业务、增加扫描排序协议、建立料箱库存主数据、重做 MaterialExecution、
改变 PLC/RCS 私有协议、提交/推送/合并或立即部署。后续统一安排联调服务器验证。

## 2. 输入与真源关系

- [自动出库合同](../../contracts/wms-outbound-picking-task-integration-requirements.md)
- [人工出库合同](../../contracts/wms-manual-outbound-picking-integration-requirements.md)
- [入库上架合同](../../contracts/wms-inbound-putaway-integration-requirements.md)
- [Transport 合同](../../contracts/transport-fulfillment-contract.md)
- [原 Operation 实施计划](2026-09-04-outbound-picking-task-plan-delta-design.md)

本文定义上述文档中料箱命名、全程 BinExecution、LineRunEpoch 与 NG 出口设计的目标替换范围。未涉及的 operation 行为继续沿用原合同。
当前工作树已同步替换 SRS、设备/事件及 WMS 合同中的 Epoch 运行依赖，仓库规则采用已确认的简化与所有权边界。
实施切片必须同时更新对应真源，不能以本文链接长期掩盖旧合同冲突。原 Operation 计划仍承担其他未完成任务，不整篇归档。
已被完整替代的独立过程文档更新引用后移至 `../archive_docs/wes_backend/`，保留原名和内容，禁止覆盖；硬件原始文档保留。

## 3. 不变量

1. WMS 决定任务、库存、分配及业务结果；插件解释流程；扫码器只提供读码/到位事实；设备动作通过基础能力执行。
2. `src/`、SDK 不导入具体插件；业务状态在 `workline_plugins/`，宿主可靠能力可独立安装、运行和测试。
3. 不新建 BinSession、BinVisit、BinContext 或同义全程执行实体，不以 WorkLineRun、Session 或代际表替代 Epoch；不复制 outbox、幂等、重试、锁或 HTTP 框架。
4. 不保留旧字段别名、双 DTO、兼容 wrapper、旧 operation 路径或 no-op consumer。未发布系统直接替换合同。
5. 业务退出不等于物理完成；已接纳或结果未知的动作保留原身份、证据、资源围栏，直到权威闭合。
6. WorkLine 运行期间插件及配置不可修改；完全收敛清线前禁止切换，不建设跨插件接管、旧业务延续或恢复框架。
7. 数据可重建，不做旧业务数据迁移兼容；schema 变更仍须按项目规则生成 Alembic revision 并验证干净数据库迁移链。

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

当前 `manual_bin_processing` 仅有插件骨架和 prepare policy，尚无站点等待、扫码 handler 或退箱 FIFO。下表及本节业务语义是后续插件合同，
不是已实现能力；不得为核心退役创建占位 handler、模拟业务完成或强行补齐整线业务。

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
- 后续插件使用当前工位待处理记录关联 WorkLine、实际站点、首次到位 Evidence、可空实际 bin_code、处置决定和各阶段 DeviceCommand。
  首次到位 Evidence 是该次处理的本地稳定关联；后续重复扫码只关联现有待处理记录，不能另建动作。业务 task 仅在已知时保存。
- NG 日志只留审计事实，不能充当活动状态表。正常业务结束后，当前物理处置仍由工位待处理记录关联原命令；
  匹配的权威离位/释放结果完成后解除该站点等待。同箱下一次合法到位才建立新的关联，不按历史条码重用旧处置。
- 跨点承接必须使用设备合同已确认的移交关联或可靠物理队列；不可读码时也不得推测身份。无法证明对应关系时阻止该站点业务激活，
  不新增全程箱实体、通用站点引擎或永久条码索引来补洞。具体插件实现前须填明事件类型、关联字段及成功释放结果。
- 不新增 NG 人工取走作为正常业务完成门禁；实际 NG 通道的未决动作和占用按设备事实独立处理。

## 7. FIFO 与物理动作身份

正常 RETURN_BUFFER 以同一 WorkLine 的实际物理缓存队列为范围。已确认 SCAN N+3 能证明料箱进入缓存且可取走，
收到有效扫码后可靠入队。顺序的可实现边界是同一物理缓存入口在 WES 串行接纳的顺序，必须先于异步业务应用冻结，
不能取 worker 执行顺序或设备时间排序，也不声称能还原多进程在网卡上的绝对抵达时刻。任务字段只作业务关联，不产生独立队列。
入库原按 putaway_execution_id 拆队的条款同步收敛；业务准入仍由 WMS 决定，不允许混用不同物理缓存的顺序。

当前 `InboundEvidenceService.accept()` 只按消息身份加锁，提供可靠幂等接收，不提供不同消息的物理 FIFO；
`return_batch_owner` 已迁入 WorkLine owner，仍不持有退箱队列。队列由后续消费插件实现，复用现有事务和锁工具，不能称为已有能力。
插件队列入口对同一缓存串行接纳，在一个事务内关联已接收 Evidence、检查重复并写入队尾；稳定队列记录顺序在锁内生成并持久化。
若公共接收与插件应用分属事务，须先在接收边界保存可按缓存串行消费的顺序，插件只按该顺序处理；不得由完成较早的异步回调抢先入队。
具体事件接入尚未实现时，这一边界须在插件实施清单中冻结后才能启用 FIFO，不为核心退役新建队列框架。
冻结 READY 连续前缀时使用同一缓存锁，原子记录成员、目标及原 Transport 关联；请求重试复用原义务，未闭合队首不得被再次分配。
不增加设备全局序列、时钟校正、乱序重排或缓存位租约。
同一消息重投不重复入队；不能以永久 `bin_code` 唯一约束禁止该箱后续正常再次进入。
WMS READY 只能选连续队首并冻结成员与目标；NO_BATCH/WAIT 不跳队；READY、ACK 不等于队列物理退出或回库成功。
队列退出按现有匹配成员的权威物理结果闭合，不再附加 BinExecution 关闭条件。

同一次到位的重复扫码不得创建第二个放行/分流命令。命令身份关联当前待处理动作及物理阶段，复用原命令，
不直接用每个新扫码消息 ID 生成新动作，也不把 bin_code 单独当作跨多次经过的永久命令身份。
工位保存待处理命令关联；只有权威离位/释放事实才能结束该次动作。若现有设备事件无法判定这条边界，按第 10 节处理。

## 8. WorkLine 运行入口与核心退役边界

### 8.1 当前插件与配置的唯一归属

- WorkLine 保存当前插件、精确插件版本、配置、设备角色绑定和位置绑定；沿用已安装插件的静态选择，不新增动态 registry。
- 启动检查插件可用性、配置完整性、设备及位置约束；运行期间禁止修改上述内容。进程重启不重建业务身份，也不自动更换插件版本。
- 删除 LineRunEpoch 模型、绑定表、独立启停生命周期、外键、DTO、SDK 关联和生成物；已有 WorkLine 配置及绑定承接必要职责。
  不把 Epoch 改名为另一运行实体，不为每次启动增加新的代际身份。
- START 改为携带 WorkLine 当前 `version` 的启动命令，删除 `request_id`/Epoch 重放合同。在同一 WorkLine 锁及事务内校验
  版本、停用状态和启动条件，成功后推进版本并返回当前 WorkLine 状态及版本；旧版本请求返回 `409` 冲突，不再次启动。
  响应丢失或冲突后客户端重新读取状态，不自动取得新版本重发启动。停用、配置修改和再次启动都推进同一乐观锁版本，
  不重置版本、不新增启动历史实体；WMS operation、设备命令和 Transport 自身的幂等身份保持不变。
- 请求、DeviceCommand 和 Transport 保留自身身份、不可变载荷及执行必需的目标/设备合同信息。可靠重试不读取新配置改写原请求；
  不向每个对象复制整份 WorkLine 配置。历史审计沿用既有请求、命令、Evidence 和配置审计能力。

### 8.2 完全收敛清线后才能切换

切换以原流程全部结束为准入前提，检查在切换前完成，不允许先切换再处理原插件的未完成业务。

1. 现场工作人员按原业务和现场操作流程完成停料及物理清线，最终确认线内、缓存和交接位已清空后操作停用和插件切换。
   本次不实现主动停止接单、自动停料或排空编排；不新增清线确认表、字段、接口、确认人/时间记录或确认失效机制。
2. 系统检查原业务任务、工位等待、FIFO、NG 未决物理处置、WMS 可靠义务、待应用 Evidence、设备命令和 Transport，
   保证没有未闭合、领取中、待重试或结果未知的工作；沿用已有位置占用和插件 business_blocker 检查。
   工作人员确认物理清线不直接覆盖系统未闭合义务，不把未知结果改为成功或删除有效占用。
3. 复用 WorkLine 生命周期锁，在同一事务中复核上述系统条件后停用。新业务、事件应用和动作创建遵守同一准入边界，
   防止检查通过后又产生原流程工作；数据库事务不等待工作人员操作或外部设备响应。
4. 仅对已停用且系统检查通过的 WorkLine 修改插件/配置；重新启动校验新配置后才允许新业务。

系统检查不通过直接拒绝停用或切换；物理清线由现场工作人员最终确认，不要求额外的机器清线证明或新增软件审批流程。
已闭合消息的重复投递由原身份的幂等结果处理，不再次驱动插件；违反准入约束的输入保存拒绝证据并报错。
不设计切换后将原业务转交新插件或重新加载旧插件继续执行的路径。

### 8.3 位置授权与可靠 owner

- 删除 BinExecution 模型、Repository、Service、导出和专属约束；WmsConfirmation 删除 Bin/Epoch owner 分支。
  可靠 owner 复用真实任务或 WorkLine，无 task 的准入由 WorkLine 承担；保留唯一 owner 约束，不新建 owner registry。
- 位置投影保持 object_type/object_id、WorkLine、来源证据及 Transport 关联，取消 BinExecution/Epoch 外键。
  投影仍校验匹配冻结成员及权威来源，不允许任意上报改位置。
- Transport 创建可发送义务前，在 WorkLine 准入和对象资源校验下确认工作线允许该动作、对象归属及冻结成员合法；
  不能等物理结果返回后才发现没有执行授权。清场动作按原插件职责执行，禁止切换检查期间产生新业务动作。
- 复用 PositionProjection 的对象唯一性、对象锁及来源 Transport/Evidence。同箱再次合法使用由新 Transport 成员及资源校验授权，
  不凭旧投影或条码直接授权；其他 WorkLine 的有效占用和未知物理结果继续阻塞冲突动作。
- 业务关闭不删除尚有效的位置，也不释放未知动作资源；后续动作发生后旧位置不能继续冒充当前权威位置。
  权威结果证明对象位于本线绑定位置之外且位置确定时可保留已知位置，但不计作本线占用；绑定位置内或 UNKNOWN 仍阻塞清线。
- 原 Epoch lifecycle 锁与关闭事务的必要并发保护迁入 WorkLine 生命周期边界。停用不能以删除投影伪造清线，
  重新启动不能把旧投影当作新动作授权；原请求身份和已闭合结果保留，不随切换删除。

## 9. 实施切片与验收

核心切片顺序：S0 → S1 剩余项 → S2 剩余文档项 → S3A → S4。S3B 是依赖核心能力的后续插件工作，按实际插件单独排期；
第 10 节站点事实只阻塞相关插件激活；WorkLine 切换遵守第 8.2 节的系统检查和现场清线前提。
这些事实不阻塞 S1 或没有该业务消费者的核心退役，已有消费者的实际职责不能借此漏迁。
不得在 S1/S2 完成后宣称 BinExecution 已退役或完整业务已实现。

### S0：冻结消费者与合同清单

- [x] 固定新的实施 HEAD、staged/unstaged/untracked 指纹；已 SHIP 的 Operation 基线只作为输入，不重新实施。
- [x] 枚举 bin_id、BinExecution、bin_execution_id、LineRunEpoch、line_run_epoch_id、Epoch 绑定及两个 NG operation 的生产调用、SDK、插件、fixture、生成物与 HEAVY mapping。
- [x] 列全 Epoch 在 START、事件路由、DeviceCommand、WMS owner、Transport、配置摘要、SDK、API/前端生成类型和审计中的消费者；
  按职责迁入 WorkLine、请求/动作或删除。外部合同中的 line_run_epoch_id 逐项冻结删除后的严格 DTO 与关联方式，不机械替换字段。
- [x] 对生产符号执行 GitNexus upstream impact；不可用时明确记录精确 rg/调用链降级证据。
- [x] 将每个消费者归入“改名、现有能力承接、删除、真实物理事实待核实”，不能靠运行整目录失败发现调用点。

验收：清单覆盖本文第 11 节入口，列清每个被删不变量的承接者和测试 owner；确认第 10 节仅阻塞相关切片。

### S1：一次性统一 bin_code

- [x] Operation 基线已同步 WMS wire、typed intent/outcome、SDK facade、Adapter、插件调用及直接/间接测试。
- [x] WMS 合同已有 bin_code 可用、旧 bin_id 拒绝及编码原样保留的行为验证；后续改动仅刷新受影响证据。
- [x] 更新内部 Transport 成员名称，验证 container_id 外部 payload、资源互斥和结果成员匹配语义不变。
- [ ] 同步 OpenAPI、mock、operation inventory 与当前 WMS 示例；检查字段改名后的摘要和重试一致性。

验收：相关 WMS 字段只剩 bin_code；通用 object_id/container_id 和真实内部主键按白名单保留；没有兼容输入/双路径。

### S2：删除 NG 出口义务

- [x] Operation 基线已删除出库 ng_exit wire/typed/adapter、facade、静态分派、owner 特例及专属测试/HEAVY 引用；实施时复核清单残留。
- [x] 同步入库与人工合同，去掉 NG 出口上报及人工取走绑定正常业务完成的条款。
- [ ] 已有插件 NG 行为按 TDD 改为独立分支；尚未实现的插件只修订合同，不虚构业务实现或为文档编写测试。
- [ ] 保留共享未知 operation 拒绝、可靠设备命令与业务应用反馈的有效覆盖，不为废弃 operation 建立永久兼容测试。

验收：两个 NG operation 在可执行树及当前接口清单不存在；NG 判定不触发正常后续作业，未决设备命令仍保留。

### S3A：核心退役 BinExecution/LineRunEpoch，运行入口收敛到 WorkLine

- [x] 先建立位置授权、同箱再次使用、未决资源、完整清线和停用/切换/重启的承接行为测试，再删除原 BinExecution/Epoch 测试。
- [ ] 按内聚切片迁移 WorkLine 配置/绑定、启动、事件路由、DeviceCommand、position/Transport authority、confirmation owner 及 S0 全部消费者。
- [x] 清场复用 `PositionProjectionRepository.get_active_workline_summary()`、工作线 lifecycle 锁及插件 business_blocker；
  宿主只调用基础协议，不能导入或查询具体插件业务表。未实现插件不得声称可生产启动。
- [ ] 同步 START 请求/响应、OpenAPI、前端调用及生成类型，删除 request_id 和 Epoch 返回字段；客户端冲突或响应丢失后重新读取状态。
- [ ] START 承接测试覆盖同版本并发仅一次成功、成功后重试返回 409、停用/修改插件后旧版本仍拒绝、事务失败回滚状态和版本；
  验证失败时没有启用工作线或产生动作。承接原 START 持久化测试，并在前端覆盖重复点击及响应丢失后的状态重读。
- [x] 验证运行中不能修改插件/配置；未闭合请求、待应用输入、未知动作或真实占用任一存在均不能切换。
- [x] 验证新业务/事件应用/动作创建与清线检查并发时不能绕过准入；重复旧结果不执行新动作；系统未闭合义务不能被现场清线操作绕过。
- [x] 移除 BinExecution/LineRunEpoch 全套实现和约束，不保留空壳、旧 import、旧枚举、临时兼容入口或同义运行实体。
- [x] 通过仓库命令生成 migration；验证干净逻辑库迁移链、约束和受影响真实持久化路径。

验收：没有 BinExecution/LineRunEpoch 运行依赖；缺少授权、错误对象/工位、未知物理结果仍不能放行或切换；基础可零插件独立验证。

### S3B：后续插件站点行为，独立排期与验收

- [ ] 对实际要实现的插件冻结工位待处理记录、事件关联、命令完成事实和最小 FIFO 接收边界；不同时实现三种整线。
- [ ] 插件测试覆盖同次重复扫码、不可读码 NG、跨点关联、原命令失败/未知、合法再次到位；事件事实未定则该插件保持未激活。
- [ ] 入库 return_batch 合同明确跨 putaway_execution_id 队首的请求组织、逐成员业务关联及 RETURN_PLACED 关联；
  合同未冻结前不启用入库 FIFO，不通过按任务另建队列规避。此项为待决合同，不宣称已获 WMS 确认。
- [ ] FIFO 持久化测试覆盖不同消息并发接收及异步应用反序、重投、事务回滚、连续队首冻结与权威出队；不复制共享可靠收发测试。
- [ ] 插件 business_blocker 覆盖当前等待、未闭合队列及 NG 物理处置；零业务请求不得绕过实际占用。

验收：仅在对应插件的动作身份、FIFO 与清场测试通过后报告该插件站点能力完成。此项未完成不等于核心仍依赖 BinExecution。

### S4：最终验证与联调交付清单

- [ ] 同步三份业务合同、设备/事件合同、SRS、最小执行架构、Agent 规则、原 Operation 计划、file_index 与生命周期索引；移出完整过期文档。
- [x] 完成测试所有权清单、残留扫描、diff 检查和唯一一次主 Review/反馈闭环。
- [x] 对最终可执行快照运行必需 QUALITY、selector 选中的 HEAVY 和迁移验证；worker wiring 改动用真实 worker 验证。
- [ ] 形成联调包清单：WMS 新字段示例、拒绝旧字段、重试身份、已删除 operation、schema 变更、待验证插件与设备事实。

验收：明确区分代码/测试/部署/业务验收；不把本地通过或文档完成报告为已部署。部署留待统一联调安排。

## 10. 事实门禁与明确失败行为

| 需核实事实 | 核实方法及不满足时的处理 |
| --- | --- |
| 同次重复扫码与下一次正常经过如何区分 | 查当前设备事件和命令完成合同；复用已存在的到位/离位关联。缺少事实时不猜时间窗、不增加软件序列，阻塞对应站点动作身份替换 |
| NG 跨点/跨线如何携带当前处置 | 查既有插件记录与设备路由；必须能关联当前处理而非历史同码。不能关联时不自动分流，阻塞该业务激活 |
| 切换前如何完成清线 | 系统检查已有占用、队列及未决义务，检查与准入共享 WorkLine 生命周期边界；物理清线由现场工作人员最终确认。无额外确认记录、接口或机器证明要求 |
| 本地判定 NG 是否要求另行通知 WMS | 三份合同明确 WMS 何时已知决定；若有未覆盖的通知需求，先明确既有交互承接，不恢复 ng_exit_report 作为隐式兜底 |

这些是执行边界所需事实，不授权为小概率情况建设长期补偿框架。不阻塞已确定的字段改名与废弃接口清理。
实施者先从现有合同/代码核实，仍缺少时仅提出缺失事实，不重复请求已授权范围的分阶段批准。

## 11. 文件与测试入口

以下是已核实的入口，不代替 S0 对间接消费者的完整枚举。

| 范围 | 实现/合同入口 | 测试主要 owner |
| --- | --- | --- |
| WMS 业务字段、NG 接口 | `src/app/wms_adapter/outbound_picking/`、`src/app/wms_adapter/confirmation_adapter.py`、`src/wes_plugin_sdk/src/wes_plugin_sdk/wms_operations.py` | `tests/contracts/wms_adapter/outbound_picking/`、`tests/integration/wms_adapter/outbound_picking/`、`tests/runtime/execution/test_plugin_sdk_wms_operations.py` |
| Bin 生命周期退役 | 原模型、Repository 和 Service 已删除；必要位置职责归下行入口 | 原专属测试已删除；由位置、Transport authority 与 execution 约束测试承接 |
| 位置与 Transport 授权 | `src/app/execution/models/position_projection.py`、`src/app/execution/services/position_projection_service.py`、`src/app/transport/models.py`、`src/app/transport/contracts.py` | `tests/runtime/execution/test_position_projection.py`、`tests/runtime/transport/test_transport_execution_authority.py` 及映射的持久化测试 |
| Epoch 退役与运行入口 | `src/app/workline/models/workline.py`、`src/app/workline/activation.py`、`src/app/workline/services/workline_start_service.py`、`src/app/workline/services/workline_configuration_service.py`、`src/app/workline/plugin_routing.py` | `tests/integration/workline_capabilities/test_workline_start_postgresql.py`、`test_workline_configuration_postgresql.py`、`test_workline_retirement_schema_postgresql.py`（后两者同目录）；设备/事件及插件测试按 S0 清单 |
| 可靠 owner 与切换 | `src/app/execution/services/wms_confirmation_service.py`、`src/app/workline/repositories/workline_repository.py` | `tests/runtime/execution/test_wms_confirmation_service.py`、`tests/integration/workline_capabilities/test_unfinished_execution_snapshot_postgresql.py` |
| 插件业务、动作关联 | `workline_plugins/rough_sorter/` 迁移已有业务；`workline_plugins/manual_bin_processing/` 新站点业务仍属 S3B | 插件各自 `tests/`；不进入核心默认 QUALITY/覆盖率 |
| 测试治理与 schema | `tests/README.md`、`docs/architecture/heavy-test-impact.toml`、`migrations/versions/` | 精确 selector、迁移链与选中 HEAVY；不扩大到无关整仓测试 |

文档本身只做路径/引用、结构和 diff 检查，禁止新增正文测试。行为实现按高风险 TDD 推进。

## 12. 完成判据

核心完成按 S0/S1/S2/S3A/S4 验收；以下涉及扫码动作、业务 NG 和 FIFO 的项目属于对应插件 S3B，未实现项明确标记未激活，
不能计作核心失败，也不能以核心通过代替插件业务通过。

- [ ] WMS 料箱业务字段为 bin_code；Transport 对外仍为 container_id；无旧字段兼容。
- [ ] BinExecution/LineRunEpoch 全部实际职责已有必要且唯一的承接者，模型、外键、服务、owner、测试和文档残留闭合。
- [ ] WorkLine 是插件和运行配置的唯一入口；完全收敛清线后才能切换，运行配置不可在线修改；无替代 Epoch 实体。
- [ ] 系统检查与新业务/事件/动作准入无并发空隙；工作人员最终确认物理清线，无新增确认机制；可靠身份、不可变请求和幂等结果保留。
- [ ] NG 是独立分支，无 NG 出口 operation，无等待 WMS 人工业务完成的正常执行尾巴。
- [ ] 重复扫码/回调不重复动作；同箱下一次合法进入可正常处理；未知物理动作不丢失身份和围栏。
- [ ] 真实 RETURN_BUFFER FIFO 不按任务绕队；READY 不被解释成物理完成。
- [ ] 基础与插件分别验证；原 prepare、issued、Transport 的受影响回归通过。
- [ ] 第 10 节未核实部分明确保留为未激活边界；没有把未实现的三种整线业务报告为完成。
- [ ] 无双通道、同义替代实体、过期当前态文档或未授权 Git/部署动作。

## 13. 工程评审结论与实施验证图

本节保留实施前的设计评审结论；当前实施状态与验证结果见本文开头及 S0 清单。

本轮评审以 WorkLine 唯一运行入口、BinExecution/LineRunEpoch 退役和完全清线后切换为已批准范围，不重新扩大业务范围。

| 问题 | 结论 | 实施验证 |
| --- | --- | --- |
| D1：Epoch 承担 START 幂等结果，删除后旧启动请求语义缺失 | 已确认改为 WorkLine version 校验，旧版本返回 409 | 同版本并发、停用再启后的旧版本、事务回滚、客户端响应丢失后的状态重读 |
| D2：清线前置条件与主动停料/排空实现混在一起 | 已确认本次只检查并切换，物理清线由工作人员最终确认；不增加确认记录或状态机 | 系统负载阻塞、准入并发、停用后禁止新业务；人工清线为现场操作前提，不编写确认记录测试 |

### 已有能力与测试承接

- WorkLineConfigurationService.save/deactivate 已有版本、停用状态、共同负载与插件 blocker 检查，复用并迁移 Epoch 依赖。
- PositionProjectionRepository 已有按工作线查询占用和 UNKNOWN 的汇总，保留对象唯一约束及 WorkLine 索引，迁移绑定关联。
- WmsConfirmation/InboundEvidence/DeviceCommand/Transport 继续拥有各自可靠身份，不复制公共机制。
- 原 START PostgreSQL 测试证明 Epoch 关闭后旧 request_id 不再次启动；目标采用新的版本冲突合同，必须以第 8.1 节承接，不能只删断言。

以下是目标验收图；“承接”表示已有旧合同测试可迁移，不表示目标代码已实现或测试已运行。

```text
START(version)
  +-- 版本旧 / 已启用 / 配置不完整 -> 拒绝，无副作用 [D1 必补：API + PostgreSQL]
  +-- 同版本并发 -> 仅一次启动并推进版本          [D1 必补：PostgreSQL]
  +-- 事务失败 -> 状态、版本共同回滚              [承接 START PostgreSQL]
  +-- 响应丢失 -> 客户端读取状态，不自动再启       [D1 必补：前端]

工作人员完成物理清线 -> 操作停用
  +-- 未决义务 / UNKNOWN / 已有占用 -> 拒绝       [承接 workload / projection]
  +-- 新业务与停用并发 -> 同一 WorkLine 锁串行化   [承接 configuration PostgreSQL]
  +-- 无阻塞 -> 原子停用 -> 改配置 -> 新版本 START [承接 API + PostgreSQL]

Transport / DeviceCommand / WMS
  +-- 缺少授权 / 对象冲突 -> 创建发送义务前拒绝    [S3A 承接测试]
  +-- 结果未知 -> 保留身份和围栏，阻塞切换         [承接现有可靠性测试]
  +-- 重复已闭合结果 -> 幂等返回，不执行新动作     [承接现有可靠性测试]
```

主要测试 owner 为 `tests/integration/workline_capabilities/test_workline_start_postgresql.py`、
`test_workline_configuration_postgresql.py`（同目录）、`tests/runtime/execution/test_position_projection.py`、
`tests/runtime/execution/test_wms_confirmation_service.py` 及 S0 枚举的直接/间接消费者；
前端 START 调用位于 `wes_frontend/src/views/admin/worklines/composables/useWorkLineStart.ts`，实施时先定位其现有测试承接。
测试只覆盖软件可观察行为，物理清线不能由单元测试或 Mock 宣称验证。

### 性能、失败路径与实施顺序

代码组织与性能评审未发现新增阻塞项。共同锁只覆盖数据库检查和提交，不等待人工、HTTP 或设备；按 WorkLine 查询当前负载，
复用已有索引，不扫描历史 Evidence JSON 重建状态，不引入缓存或新锁框架。迁移时保留对象唯一性和所需查询索引。
版本冲突、活动配置修改、未决负载与授权失败均须返回明确拒绝；事务失败回滚，结果未知保留围栏。目标处理按 S3A 验证，实际实施证据见 S0 清单。

S1 Transport 改名与 S3A 共享合同及生成物，核心顺序实施，不拆并行写入；S3B 在核心完成后按独立插件排期。
本轮不新增 TODO：自动排空已有 TODOS 条目，入库跨执行 FIFO 合同待决项保留在 S3B。
不在本次范围：三种整线实现、自动清线编排、清线确认机制、跨插件恢复，以及 Commit/Push/Merge/Deploy。

### Implementation Tasks

- [ ] **T1（P1；人工约 1 天 / Agent 约 2–4 小时，仅估算）**：按 D1 替换 START 合同、客户端及测试；
  涉及 WorkLine start DTO/Service/API、前端 START 调用和生成类型；验证版本冲突、并发、回滚及状态重读。
- [ ] **T2（P2；文档已完成，实施纳入 S3A）**：按 D2 保留系统检查与原子切换，物理清线作为现场前提；
  复用 configuration 并发测试验证未决义务和新业务不能绕过检查，不增加自动排空或确认机制。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| Eng Review | plan-eng-review | Epoch 退役后的合同、边界和测试承接 | 1（本轮） | 评审决策已闭合 | 架构 2 项已确认；代码组织 0 项；D1 测试要求已纳入；性能 0 项 |

本轮范围保留；测试图已给出；未新增 TODO；Outside voice 未运行；核心顺序实施。
未发现目标设计中同时缺少错误处理与测试要求的静默失败路径；这不是现有代码验证结论。
本轮无新增需持久化的通用经验。

**VERDICT:** 核心设计评审意见已闭合，可进入 S0 消费者与合同冻结；S0 的逐项 wire 决策仍需在对应实现前完成。
S3B 的入库 FIFO 和站点事实仍按既定门禁单独冻结；本结论不代表整个业务已可激活、代码已通过测试或现场已验收。

NO UNRESOLVED DECISIONS
