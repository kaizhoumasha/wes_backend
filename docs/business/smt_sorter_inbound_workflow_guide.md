# SMT 分拣入库工作流指南

> **日期**: 2026-05-21
> **状态**: Draft；v0.7.0.0 已落地后端 handoff/manifest P0 闭环
> **版本**: v0.2
> **适用范围**: `smt_sorter_inbound` 复合 WORKLINE、SMT 分拣机入库、单层货架零散料盘归集入五层货架

本文定义 SMT 分拣入库的业务流程、资源边界、事件口径和恢复原则。本文基于 21 轮逐条确认的业务决策，不替代硬件接口原文、SRS 或插件开发指南。

## 0. 当前后端落地状态

v0.7.0.0 已完成 SMT 分拣入库后端 handoff/manifest P0 闭环，范围是粗分机 release fact 到 source item `PICKED/SORTED/SKIPPED`、demand `COMPLETED` 的本地后端自动推进。

已落地合同：

- `smt_sorting_inbound@smt_sorting_inbound.v1` manifest 只声明 WES 管理的 source 单层货架位和 target 五层货架位；NG 区、工作站、扫码平台、料箱码和料格码不再作为 manifest `RackPosition` 或 `ResourceBoundary`。
- Handoff route 从 manifest `SORTING_INBOUND_SOURCE` / `SORTING_INBOUND_TARGET` boundary 解析 source/target rack position，并默认使用真实 ECS realtime probe 作为 source-pick admission。
- 粗分机 release fact 创建或更新 handoff demand 后，会先 `evaluate`，再按 demand scope 尝试 claim 一条 READY source item。
- Claim 使用两阶段短锁：外部 route/ECS probe 不持有 source item 或 target WorkLine 行锁，最终短事务内重锁 source item、demand 和 target WorkLine 后再创建 sorting session 与内部 Inbox。
- Claim 创建的 `smt_sorting_inbound@smt_sorting_inbound.v1` session 通过 `SortingInboundContext` 写入 `sorting.context_schema_version=1`、`source_pick_request`、source/target rack position evidence 和 `stations.scan_platform=EMPTY`。
- `SORTING_SOURCE_PICK SUCCESS` 通过 handoff service 统一写 `PICKED` ledger；`SORTING_TARGET_PLACE SUCCESS` / `SORTING_NG_PLACE SUCCESS` 统一写 `SORTED` / `SKIPPED` terminal ledger，并在首次 terminal success 后按 demand scope claim 下一条 READY item。
- Celery 兜底扫描覆盖 due demand 重算、post-claim recovery 和 READY claim fallback，summary 增加 `claimed`，并将 `scan_limit` / `recovery_limit` / `claim_limit` 拆分。

未落地范围仍按本文后续章节和 `TODOS.md` 跟踪：完整 CTU/WMS/NG 对账、运营看板、告警阈值、现场 Runbook、供应商硬件协议补充和同一 target WorkLine 多 item 并发处理。

详细合同与验收记录见：

- `docs/superpowers/archive/specs/2026-06-16-smt-sorting-inbound-manifest-flow-spec.md`
- `docs/superpowers/archive/plans/2026-06-16-smt-sorting-inbound-manifest-flow.md`

## 1. 文档定位

`smt_sorter_inbound` 是"SMT 分拣入库"复合 WORKLINE，承接上游粗分工作线完成后的结果——已存储在单层货架上的源料箱——进行零散料盘归集入库。

它是五类独立执行链路通过业务键关联的组合，不是单设备 ECS 插件。各链路是：

| 链路 | 职责 |
|------|------|
| 事件触发 + RACK_QUEUE | 单层货架从分拣机排队位到 STATION，再到空架/空箱区的控制链路 |
| 五层货架管理 | 检查分拣机五层货架工作位是否已有可用货架；无时请求 WMS 分配并调度 AGV 送达；活动面切换（换面）控制；两面均无可用或进入末任务收口时回库 |
| CTU 搬运 | 投料任务和退箱任务是两个独立任务，串行调度，事件驱动触发 |
| 流水线控制 | 扫码判定、工作位防呆确认、方向决策信号下发（挡停切换）、流水线自动推进 |
| 料盘分拣执行 | 单个源料盘从源料格取出、扫码、分箱、目标格放入的并行执行链路 |

**与上游粗分工作线的分工边界**：
- 上游粗分工作线负责：单层货架到达分拣机 STATION 后的扫码、测量、源货架快照记录、RACK_QUEUE 排队
- `smt_sorter_inbound` 负责：RACK_QUEUE 排队位单层货架的分拣执行（五层货架管理 + CTU 搬运 + 流水线控制 + 料盘分拣）
- `smt_sorter_inbound` 处理的所有源料箱都来自上游粗分工作线已处理完成的单层货架

## 2. 关键约定

| # | 约定 | 确认内容 |
|---|------|---------|
| 1 | 触发机制 | **事件驱动**为主触发，Celery BEAT 每 **1 分钟** 兜底扫描 |
| 2 | 五层货架可用 | 工作位有货架 **且** 当前活动面至少 **1 个含空料格** 的目标料箱 |
| 3 | WMS 分配 | **同步等待**，无可用货架时持续等待 E08 ACK 后的 status query 返回 typed terminal result |
| 4 | CTU 任务 | 投料/退箱为**两个独立任务**，串行调度，事件驱动触发 |
| 5 | 投料握手 | **每投一个料箱前都确认**（等步进电机移走 → 确认缓存位空 → 投下一个） |
| 6 | 流水线拒绝投料 | CTU **等待重试**，不退回五层货架 |
| 7 | 目标料箱扫码判定规则 | 目标料箱扫码失败、bin_id 不在授权、料箱类型不匹配、**朝向不正确**、**无空料格** |
| 8 | WES 放行决策 | 目标料箱扫码后 WES 显式下发方向决策信号（`MOVE_TO_WORK` 或 `MOVE_TO_OUTPUT`），硬件切换挡停方向 |
| 9 | 工作位防呆 | 扫码位和工作位之间有**缓存位**，工作位二次扫码做防呆确认 |
| 10 | 机械臂并发 | **并行模式**，扫码平台是同步点，硬件互锁 |
| 11 | 源料盘扫码失败 | 料盘已在扫码平台 → **TARGET_ARM** 将其放入 NG 转存位 |
| 12 | 分箱算法 | 依赖 **WES 本地实时投影**（每次投料/放料/出料都更新） |
| 13 | 源货架快照 | WES **持有**（粗分机处理后已记录） |
| 14 | 取料顺序 | 料盘堆叠，**LIFO** |
| 15 | 工作位放行 | WES 下发方向决策信号后，料箱经缓存位自动推入工作位；退箱时同理自动推入退箱位 |
| 16 | 退箱区 | **4~5 个**料箱位置，满时**自动停机**，CTU 搬走后**自动恢复** |
| 17 | 源货架释放 | 最后一个待处理源料盘离开源格后，单层货架即可请求移入空架/空箱区 |
| 18 | 货架切换 | 新货架**提前在排队位等待**，旧货架移出后立即推入 |
| 19 | 五层货架双面数据 | E08/E10 typed terminal result 返回每面料箱列表（bin_id + 各料格占用情况），WES 本地自行维护在途状态并计算可投数 |
| 20 | 外部等待超时 | **无超时取消**，达到阈值后记录告警/RuntimeHold；迟到 status result 仍须通过 ACK/reference 与版本校验后才能推进 |
| 21 | 五层货架回库 | 回库和请求新货架是**两个独立任务**，WES 重新请求，无可用时**隔段时间重试** |
| 22 | 料盘闭环 | 单个源料盘后续独立进入 COMPLETED、NG_RECORDED 或 BLOCKED，不反向阻塞源货架释放 |
| 23 | CTU 投料身份 | CTU 投料阶段不默认知道真实 `bin_id`，未扫码前只追踪批次、槽位或流水线临时占位 |
| 24 | 目标箱身份绑定 | 只有 `TARGET_BIN_SCAN_COMPLETED` 才能将流水线临时占位绑定到真实目标料箱 |
| 25 | 末任务收口 | 无后续源料盘/源货架时，当前 AT_WORK 目标料箱即使未满也必须释放回架 |
| 26 | 库存增量同步 | 五层货架回库前，WES 必须向 WMS 提交本次新增料盘清单并取得接收凭证，或在回库请求中携带凭证 |
| 27 | 目标箱差异对账 | 授权集合与实际扫码集合不一致时，按真实扫码箱收口，未出现的授权箱独立进入 `MISSING_RECONCILING` |
| 28 | 对账作用域 | 单个目标箱位置不明不必默认冻结整架货架；若当前货架实际快照可信，可回库并保留独立资源对账 |

## 3. 运行粒度与状态归属

### 3.1 Session 第一性原理

`WorklineSession` 不等同于物理对象生命周期。它表达的是一条可恢复、可追溯、当前最多等待一个外部结果的执行链路。

当前 Runtime 的 Session 是单等待点模型，因此 `smt_sorter_inbound` 不能把整架单层货架、多个目标料箱、多个源料盘、CTU、流水线和机械臂全部放进一个 Session。

### 3.2 资源状态粒度

| 对象 | 状态归属 | 说明 |
|------|----------|------|
| 单层货架 | RACK_QUEUE 控制链路 + 资源状态 | 控制链路负责搬运与 STATION 释放；资源状态表达当前位置和占用 |
| 单层货架上的源料箱 | 随单层货架保持挂载 | 在分拣机流程中不脱离单层货架，只更新料箱内源料盘处理进度 |
| 源料盘 | 料盘分拣执行链路 | 每个源料盘独立取料、扫码、分格、放入或阻断 |
| 五层货架 | 五层货架管理链路 + 资源状态 | 负责工作位检查、WMS E08 分配请求、E08/E10 typed terminal result、当前操作面和换面；资源状态表达货架位置和朝向 |
| CTU 搬运任务 | CTU 搬运链路 | 负责一次单向批量投料或一次单向批量退箱/回架 |
| CTU 背篓空闲槽数 | CTU 搬运链路 | 投料数量计算输入 = 背篓总槽数 - 已装载槽数 |
| 五层货架目标料箱 | 流水线控制链路 + 资源状态 | 负责单个目标料箱生命周期和位置投影 |
| 目标料格 | 资源投影/目标格计划 | 分格成功先形成计划和在途投影；目标侧机械臂放入成功后才成为物理占用事实 |

一架单层货架是否可释放，应由源侧最后一个待分拣料盘已经从该货架源料箱/源料格取出的事实决定。该事实形成后，RACK_QUEUE 即可请求 WMS/RCS 将单层货架移入空架/空箱区；后续扫码、目标格放料、目标料箱出料/回架或 NG 处置不得反向阻塞该源货架。

源料盘是否完成，应由对应料盘分拣执行链路独立表达。源料盘离开源格后，后续可能进入 COMPLETED、NG_RECORDED 或 BLOCKED；这些状态用于料盘追溯和业务闭环，不再作为单层货架释放的前置条件。

### 3.3 料盘分拣执行 Session 模型

每个源料盘离开源格时创建独立的 `WorklineSession`，用于追溯单个料盘的完整分拣链路。

| 字段 | 值 |
|------|------|
| business_key | `{source_rack_id}:{source_bin_id}:{source_cell_id}:{reel_id}` |
| 等待点 1 | `SOURCE_REEL_SCAN_COMPLETED`（扫码结果） |
| 等待点 2 | `TARGET_ARM_PUT` 命令结果（放入目标料格） |
| 终态 | `COMPLETED` / `NG_RECORDED` / `BLOCKED` |

料盘分拣执行 Session 与 RACK_QUEUE 控制链路相互独立。料盘 Session 的终态不反向阻塞源货架释放——源货架释放只取决于最后一个待处理源料盘已离开源格的事实。

**并发性**：由于扫码平台是单占用同步点，同一时刻最多只有 1 个料盘分拣 Session 处于活跃态（等待扫码或等待放料）。但 Session 的创建不依赖前一个 Session 的终结——当 TARGET_ARM 从扫码平台取走料盘后、放料完成前，SOURCE_ARM 可以放入下一个料盘并创建新 Session。Session 独立存在的意义是异常追溯和恢复，不是为了并发执行。

## 4. 参与资源

| 资源 | 数量/角色 | WES 语义 |
|------|-----------|----------|
| 单层货架 STATION | 2 个 | 分拣机可处理的源货架停靠位 |
| 五层货架停靠位 | 至少 1 个 | AGV 将五层货架搬运到工作区后的接驳位置 |
| CTU 背篓 | 1 个或多个槽位 | 在五层货架与流水线入/出料口之间搬运目标料箱 |
| AGV 货架搬运/换面能力 | 外部调度能力 | 将五层货架搬运到工作区，并在另一面仍有可用料箱时执行换面或调整朝向 |
| 流水线 STATION_INPUT | 多个入料位 | CTU 投放目标料箱的入口 |
| 流水线 STATION_SCAN | 多个扫码位 | 流水线对目标料箱进行扫码识别的位置 |
| 流水线 STATION_WORK | 多个物理编码，v1 全线单工作位 | 机械臂可向目标料箱料格放入料盘的位置；v1 同一时间只允许 1 个可信 AT_WORK 目标料箱 |
| 流水线 STATION_OUTPUT | 多个出料位 | 退箱区位置，CTU 从流水线取回目标料箱的位置 |
| 源侧机械臂 | 配置角色 | 从单层货架源料箱/料格取料盘，并放到扫码平台 |
| 扫码平台 | 1 个或多个 | 对源侧机械臂取出的源料盘进行身份确认 |
| 目标侧机械臂 | 配置角色 | 从扫码平台取料盘，并放入目标料箱指定料格 |
| NG 转存位 | 配置项 | 接收扫码失败或异常的源料盘；容量、满位停线和自动恢复由硬件负责 |
| WMS/RCS | 外部系统 | WMS 分配可用五层货架，AGV 调度，RCS/CTU 执行目标料箱搬运 |

`STATION_INPUT/SCAN/WORK/OUTPUT` 是本文使用的角色名。具体设备协议中可能出现 `STATION_INPUT1~4`、`STATION_SCAN1~16`、`STATION_OUTPUT1~4` 等实例编码，运行时应通过 WorkLine/Device/Position 配置解析。v1 对 `STATION_WORK` 采用全线单工作位口径：多个物理编码仅作为配置兼容，不启用多个目标料箱并行分拣。

## 5. 权责边界

| 系统 | 负责 | 不负责 |
|------|------|--------|
| WES | WORKLINE 编排、分格决策、命令意图、执行事实、资源投影、异常对账、流水线方向决策（MOVE_TO_WORK / MOVE_TO_OUTPUT 挡停信号） | 库存主账、空箱资源授权、AGV/CTU 路径规划、硬件实时互锁 |
| WMS/RCS | 分配可用五层货架、AGV 搬运五层货架到分拣机五层货架工作位、AGV 换面、CTU 执行闭环、空架/空箱区流转、库存/资源主账、料箱回架确认 | 机械臂动作编排、WES 内部分格算法、WorklineSession 状态推进、向 WES 暴露 AGV/CTU 硬件互锁细节 |
| ECS/流水线 | 通过步进电机移动料箱、料箱扫码、到达/离开事件上报、挡停切换执行、设备执行结果、基础点位防呆和停线恢复 | 目标料格选择、库存准入判断、跨设备业务状态机 |
| ECS/机械臂 | 按 WES 命令执行取放，并回传结果 | 判断源料盘是否应放入某个目标料格 |

v1 的控制原则是"硬件负责实时防呆与挡停切换，WES 负责业务闭环与方向决策"。WES 不用数据库快照替代 PLC/ECS 点位互锁，不维护复杂软件安全状态机，不为 NG 容量做逐料盘预留。

## 6. 主流程

### 6.1 事件触发

```
1.1 上游粗分工作线完成单层货架粗分处理后，记录源货架快照，创建 `SINGLE_LAYER_RACK_READY_FOR_SORTING` 事件
1.2 WES 收到事件 → 校验 remaining_reel_count > 0
    - > 0 → 创建 RACK_QUEUE 控制链路
    - = 0 → 不进入分拣队列，走空架/空箱区调度
1.3 Celery BEAT 每 1 分钟扫描一次未处理的分拣任务（兜底，需分布式锁）
```

### 6.2 检查五层货架

```
2.1 查询 WES 本地资源投影：分拣机五层货架工作位是否有可用货架？
    可用 = 工作位有货架 且 当前活动面至少有 1 个含空料格的目标料箱
2.2 有可用货架 → 跳到 6.3
2.3 无可用货架 → 向 WMS 请求分配可用五层货架
2.4 流程进入等待状态（同步等待）
2.5 WMS E08 status query 返回 typed terminal result：五层货架到位 + 当前操作面 + 各面可用料箱清单 + 资源授权版本
2.6 WES 校验 ACK/reference 与版本后更新本地资源投影
```

### 6.3 CTU 搬运

```
3.1 CTU 空闲时先检查退箱区：
    - 退箱区有待返回料箱，或达到清理阈值/堵塞风险 → 创建 CTU 退箱任务
    - 无退箱需求 → 继续评估投料
3.2 计算本次投料数量：
    feed_count = min(CTU 空闲背篓槽数, 投料水位允许数, 当前操作面可用目标料箱数)
    投料水位允许数 = 流水线空投料缓存位数 - 当前已投入但尚未离开缓存位的料箱数
3.3 feed_count = 0 → 暂停投料，等待水位恢复
3.4 feed_count > 0 → 创建 CTU 投料任务
    - 记录本批授权候选集合 expected_authorized_bin_ids
    - 记录 CTU 背篓槽位或任务序号；若 CTU 无法逐箱识别真实 bin_id，WES 不提前绑定目标料箱身份
3.5 CTU 从五层货架当前操作面批量取料箱 → 背篓装载
3.6 CTU 到达流水线投料口
3.7 逐箱投料循环（事件驱动）：
    3.7.1 请求 ECS 查询 PLC：是否允许投料？（检查项：缓存位空 + 流水线运行状态）
    3.7.2 PLC 允许 → CTU 投放一个物理料箱到缓存位；未扫码前创建流水线临时占位，不绑定真实 bin_id
    3.7.3 PLC 拒绝 → CTU 等待重试（不退回五层货架）
    3.7.4 消费 `BIN_DEPARTED @ STATION_INPUT` 事件（步进电机已将入口缓存位物理箱移走）→ 投下一个
    3.7.5 重复 3.7.1～3.7.4，直到背篓清空
3.8 已开始的投料批次不可被退箱任务抢占；退箱区风险通过投料水位和保留出料容量前置规避
3.9 背篓清空且最后一个入口临时占位已离开 STATION_INPUT → 投料任务完成
3.10 CTU 空闲，重新从 3.1 开始选择下一任务
```

`BIN_DEPARTED @ STATION_INPUT` 只证明投料口缓存位恢复可投状态，不证明该物理料箱的业务身份。目标料箱身份必须以后续 `TARGET_BIN_SCAN_COMPLETED` 的扫码结果为准。

### 6.4 流水线扫码 + WES 判定

```
4.1 流水线事件 BIN_ARRIVED @ STATION_SCAN → WES 收到（流水线将一个物理料箱推入扫码位并挡停）
4.2 WES 将扫码位物理箱与流水线临时占位关联；若此前无占位，创建异常占位并记录来源不明
4.3 流水线执行目标料箱扫码 → TARGET_BIN_SCAN_COMPLETED 事件上报 WES
4.4 若扫码成功解析出 bin_id → WES 将临时占位绑定到该 bin_id，并更新本批 actual_scanned_bin_ids
4.4a 若扫码失败或无可信 bin_id → 临时占位保持 UNKNOWN，不更新 actual_scanned_bin_ids
4.5 WES 判定：
    - 扫码失败 → 判定退箱
    - bin_id 不在 WMS 授权列表 → 判定退箱
    - bin_type 与工作线配置不匹配 → 判定退箱
    - 朝向不正确 → 判定退箱
    - 无空料格 → 判定退箱
    - 其他 → 判定进入工作位
4.6 WES 下发方向决策信号（MOVE_TO_WORK 或 MOVE_TO_OUTPUT）→ 硬件切换扫码位挡停方向
4.7 挡停方向切换后，料箱沿对应方向移动：
    - MOVE_TO_WORK → 料箱进入缓存位 → 工作位空闲时自动推入工作位
    - MOVE_TO_OUTPUT → 料箱进入退箱区
4.8 流水线自动将排队位下一个料箱推入扫码位并挡停（硬件自主推进，不等待 WES 指令）
4.9 当投料批次收口时，WES 比对 expected_authorized_bin_ids 与 actual_scanned_bin_ids；未出现的授权箱进入资源对账，未授权但实际出现的箱按真实物理位置进入退箱/回架或隔离
```

未扫码前，WES 追踪的是流水线临时占位，不是目标料箱主数据对象。只有扫码成功后，临时占位才绑定到真实 `bin_id`；扫码失败时，该占位按未知物理箱退箱或进入人工对账。

### 6.5 工作位二次扫码（防呆确认）

扫码位和工作位之间有缓存位，工作位二次扫码用于防呆确认，确保到达工作位的料箱就是步骤 4 判定通过的那个。

```
5.1 流水线事件 BIN_ARRIVED @ STATION_WORK → WES 收到（料箱从缓存位自动推入工作位）
5.2 流水线执行工作位扫码 → WORK_BIN_SCAN_COMPLETED 事件上报 WES
5.3 WES 校验：工作位扫码身份 = 步骤 4 准入身份
5.4 一致 → 更新投影：目标料箱状态 = AT_WORK，可供料盘分拣执行使用
5.5 不一致 → 记录异常，WES 下发方向信号到退箱位 → BLOCKED
```

### 6.6 机械臂取料 → 扫码 → 分格 → 放入（并行）

SOURCE_ARM 和 TARGET_ARM **并行工作**，扫码平台是唯一的同步点，硬件互锁保证安全。

节拍模型：
```
T0: SOURCE_ARM 取料盘 A → 放扫码平台
T1: 扫码平台扫码 A → 分箱算法 → 分配目标料格
T2: 有兼容目标格 → TARGET_ARM 从扫码平台取走 A → 放入目标料格
T3: 收到 A 的南向 PICK ACK 后，WES 可触发 SOURCE_ARM 取料盘 B；PLC/机器人仍独立执行平台物理互锁
```

若 A 扫码成功但暂时无兼容目标格，A 不会产生南向 PICK ACK，因此 WES 不触发 B；平台物理占用只由
PLC/机器人互锁，不成为 WES 的业务准入投影。

```
6.1 WES 按 LIFO 顺序选择当前活动料格中下一个待处理源料盘（料盘级严格 LIFO，不跳过）
6.2 首件由 Session 启动；后续物料检查上一件 `southbound_pick_acknowledged`
6.3 已收到上一件南向 PICK ACK → 向 SOURCE_ARM 下发取料命令
6.4 SOURCE_ARM 取料 → 放扫码平台 → 回调 WES
6.5 扫码平台扫码 → SOURCE_REEL_SCAN_COMPLETED 事件 → WES 收到
6.6 WES 校验扫码身份 = 预期身份（基于源货架快照）
    - 快照不存在 → 记录异常（数据缺失）→ 相关链路进入 BLOCKED → 等待人工确认
    - 一致 → 跳到 6.8 分箱算法
6.7 不一致 → TARGET_ARM 将料盘放入 NG 位 → 记录 NG 事实 → 跳到 6.12
6.8 分箱算法：查询 WES 本地目标料箱投影 → 分配目标料格
6.9 无兼容目标格 → 释放当前工作位目标料箱（流程同第 6.9 节目标料箱释放）→ 流水线自动将排队位下一个料箱推入工作位 → 料盘留在扫码平台等待新料箱的可兼容料格 → 源侧暂停继续取料
6.10 有兼容目标格 → 向 TARGET_ARM 下发放料命令（目标料箱 + 目标料格）
6.11 TARGET_ARM 取料 → 放目标料格 → 回调 WES → 更新投影（料格占用 + 料盘身份）
6.12 南向 PICK ACK 到达后，下一个源料盘回到 6.1；平台物理互锁仍由 PLC/机器人负责
```

### 6.7 继续处理同料格

同料格中按 LIFO 逐个处理，逻辑已包含在 6.6 的 LIFO 策略中。

### 6.8 同物料优先

```
8.1 当前源料格已空（LIFO 取完所有料盘）
8.2 WES 查询源货架快照：是否有同 Material + Vendor + DC/LC 的料盘？
8.3 有 → 选择该料盘所在的源料格 → 跳到 6.1（新料格继续严格按 LIFO）
8.4 无 → 按默认顺序（料箱优先级 → 料格顺序）选择下一个 → 跳到 6.1
```

**同物料优先作用域**：仅在料格级切换生效，不是料盘级重排。当前活动料格的所有料盘取完后，优先选择同物料同 DC/LC 的其他料格；一旦切换到新料格，继续按 LIFO 取完该料格的所有料盘。

### 6.9 目标料箱释放（满格/无兼容格）

v1 采用全线单工作位口径——一次只有一个可信 AT_WORK 目标料箱。

目标料箱释放触发条件：
- **满格**：目标料箱无可用料格
- **无兼容格**：当前工作位料箱无与源料盘兼容的料格（分格规则 #7）
- **无后续源料盘**：当前没有待处理源料盘或后续源货架任务，工作位目标料箱即使未满也需要释放回架

以上条件共用同一释放流程（WES 下发方向决策信号到退箱位 → 硬件切换挡停 → 料箱进入退箱区）。

```
9.1 WES 判定目标料箱释放条件触发（满格、无兼容格或无后续源料盘）
9.2 WES 标记该目标料箱 OUTPUT_READY
9.3 WES 下发方向信号到退箱位 → 硬件切换挡停，料箱从工作位进入退箱区
9.4 流水线事件 BIN_DEPARTED @ STATION_WORK → WES 收到（工作位释放）
9.5 WES 检查退箱区状态
9.6 退箱区未满 → WES 检查扫码位是否有已通过准入的排队料箱
    - 有排队料箱 → 流水线自动将下一个料箱推入工作位
    - 无排队料箱 → 检查五层货架当前操作面是否有可用目标料箱
        - 有 → 触发 CTU 投料任务补充料箱
        - 无 → 等待 WMS 分配新五层货架
        - 同时启动工作位空闲计时器，达到阈值后告警/RuntimeHold
9.7 退箱区满 → 流水线自动停机 → WES 消费 WORKLINE_STOPPED
9.8 WES 优先调度 CTU 执行退箱批次任务（退箱区 → 五层货架）
9.9 CTU 搬走料箱 → 退箱区恢复 → 流水线自动恢复 → WES 消费 WORKLINE_RESUMED
```

### 6.10 单层货架源格耗尽后移出

```
10.1 WES 判定：源货架快照中最后一个待处理源料盘已离开源格
10.2 WES 向 WMS/RCS 请求：将单层货架移入空架/空箱区
10.3 新单层货架可提前在排队位等待（不必等旧货架完全移出 STATION）
10.4 等待 WMS E09 status query 返回 typed terminal result：单层货架已移出 → STATION 释放
10.5 STATION 释放 → 调度排队位下一架单层货架移入 STATION
10.6 若无后续单层货架任务 → 进入 6.12 末任务收口
10.7 若仍有后续任务 → 跳到 6.2
```

单层货架移出只表示源侧可释放，不表示该货架产生的所有源料盘都已完成业务闭环。已经离开源格但仍在扫码平台、目标放料、NG 或人工处置中的源料盘，继续由各自料盘分拣执行链路追溯。

### 6.11 五层货架换面/回库

```
11.1 WES 检查是否无后续分拣任务或已进入末任务收口模式
11.2 WES 检查当前五层货架是否仍有在途目标料箱（IN_CTU/IN_PIPELINE/AT_WORK/OUTPUT_READY/RETURNING）
11.3 有在途目标料箱 → 优先调度出料/退箱/回架，等待在途目标料箱全部回架收口
11.4 无在途目标箱且无后续分拣任务/已进入末任务收口模式 → 准备五层货架回库
11.5 仍有后续分拣任务时，检查当前操作面可投目标料箱数
11.6 当前面可投目标料箱数 > 0 → 跳到 6.3 继续 CTU 投料
11.7 当前面可投目标料箱数 = 0，且另一面可投目标料箱数 > 0 → 请求 WMS/RCS 执行 AGV 换面
11.8 等待 WMS E10 status query 返回 typed terminal result：换面完成 + 新 current_operable_side + 各面可用料箱清单
11.9 换面超时报警（不中断，设置报警阈值）
11.10 换面成功 → 跳到 6.3
11.11 当前面可投目标料箱数 = 0，且另一面也无可投目标料箱 → 准备五层货架回库
11.12 WES 汇总本轮写入该五层货架/目标料箱的库存增量，向 WMS 提交新增料盘清单
11.13 WMS 接收库存增量并返回凭证；若 WMS 支持合并请求，回库请求必须携带等价库存增量凭证
11.14 WES 请求 WMS/RCS 将该五层货架回库，回库请求携带当前可信货架快照、库存增量凭证和未关闭资源对账引用
11.15 等待 WMS E09 status query 返回 typed terminal result：回库完成
11.16 判断是否还有后续分拣任务：
    - 有 → WES 重新请求分配新五层货架 → 跳到 6.2
    - 无 → 流程结束
11.17 WMS 无可用货架 → 隔段时间自动重试
```

五层货架回库前必须区分两类事实：当前货架实际快照是否可信，以及是否存在单个目标料箱资源对账。若 WMS/RCS 已确认当前货架实际箱清单可信，且该货架无在途目标箱，则单个目标箱位置不明不必默认阻塞整架货架回库；位置不明的目标箱应独立冻结为资源对账项。

### 6.12 末任务/无后续源货架时的目标箱收口

当 WES 确认当前无后续源料盘、无后续单层货架任务或人工选择停工收口时，不再创建新的 CTU 投料任务，已离开五层货架的目标料箱必须全部收口。

```
12.1 WES 进入末任务收口模式，停止创建新的目标料箱投料任务
12.2 若 CTU 背篓中仍有未投入目标箱 → 优先创建 CTU 回架任务，直接回五层货架或 WMS 指定位置
12.3 若工作位存在可信 AT_WORK 目标箱 → 按 6.9 以 NO_MORE_SOURCE_REELS 原因释放到退箱区
12.4 若流水线上存在已识别目标箱 → 下发 MOVE_TO_OUTPUT，进入退箱区/回架链路
12.5 若扫码区前、入口缓存或扫码位存在未识别临时占位：
    - 可推进到扫码位时 → 先扫码绑定身份，再下发 MOVE_TO_OUTPUT
    - 无法扫码或位置不可信时 → 记录 RuntimeHold/人工对账，不猜测 bin_id
12.6 CTU 优先清空退箱区，将真实存在的目标箱回架或隔离
12.7 WES 对比本批 expected_authorized_bin_ids 与 actual_scanned_bin_ids，未出现的授权箱进入独立资源对账
12.8 所有在途目标箱收口后，WES 提交库存增量，并按 6.11 的“无后续分拣任务”分支请求五层货架回库
```

末任务收口的核心原则：凡是离开五层货架的目标料箱，必须有明确终态（`RETURNED`、隔离回收、RuntimeHold/人工对账，或 WMS/RCS 可信补证），不得以流水线临时占位、CTU 背篓占位或扫码区前占位悬空结束。

## 7. 资源竞争仲裁

| 共享资源 | 仲裁规则 |
|---------|---------|
| SOURCE_ARM | 一次服务一个料盘分拣链路，按 LIFO + 同物料优先级队列调度 |
| TARGET_ARM | 一次服务一个料盘分拣链路，按 FIFO（谁先完成扫码谁先获得） |
| 扫码平台 | 硬件互锁，SOURCE_ARM 放盘时平台必须空；无兼容目标格时平台保持占用，WES 释放工作位料箱并等待流水线自动补箱或人工处置 |
| 工作位 | v1 全线单工作位，当前料箱释放后才能放行下一个 AT_WORK 目标料箱 |
| CTU | 新任务退箱优先；单次任务单向且不可抢占，背篓清空后才接受新任务 |
| STATION | 单占用，移出确认后才释放 |

## 8. WES 投影维护清单

| 投影对象 | 更新时机 |
|---------|---------|
| 五层货架工作位状态 | WMS E08/E09/E10 typed terminal result 校验通过后 |
| 五层货架双面料箱分布 | WMS E08/E10 typed terminal result 校验通过后 |
| 五层货架每面可投目标料箱数 | WMS E08/E10 typed terminal result + 每次料箱状态变化（ON_RACK/IN_CTU/AT_WORK 等）；计算公式：当前可信快照中 ON_RACK 且状态不属于冻结、缺失或独立对账的目标箱数 - 已预约但尚未离架的投料任务数 |
| 目标料箱位置 | 真实 bin_id 已知后更新；至少区分 ON_RACK、IN_CTU、IN_PIPELINE、AT_WORK、OUTPUT_READY、RETURNING、RETURNED、MISSING_RECONCILING |
| 流水线临时占位 | CTU 投料、BIN_ARRIVED/BIN_DEPARTED、TARGET_BIN_SCAN_COMPLETED；用于未扫码物理箱，字段至少包含 placeholder_id、position、identity_status、candidate_authorized_bin_ids、resolved_bin_id |
| 目标料箱内容（各料格占用） | 每次 TARGET_ARM 放料完成 |
| WMS 库存增量同步状态 | TARGET_ARM 放料成功后形成待同步增量；WMS 接收后记录 inventory_delta_ref，五层货架回库必须引用该凭证 |
| 源货架快照（料盘身份） | 入口事件携带；单层货架到达 STATION 时一次性加载到 Redis 缓存（key=`source_rack:{rack_id}`，TTL=该货架处理周期） |
| 南向 PICK ACK evidence | `southbound_pick_acknowledged`；是下一次北向取料的唯一业务解锁条件 |
| 退箱区占用数 | 料箱进入/离开退箱区事件 |
| STATION 占用状态 | 单层货架到达/移出回调 |

真实目标料箱状态不得使用“未知”语义。未扫码前，WES 只维护流水线临时占位；扫码成功后，临时占位绑定 `resolved_bin_id`，随后才更新对应目标料箱位置。扫码失败或无法扫码时，临时占位进入退箱/隔离或人工对账，不得猜测为某个授权 `bin_id`。

### 8.1 扫码平台设备证据与串行门禁

扫码平台是 SOURCE_ARM 和 TARGET_ARM 的物理交接点，但 WES 不复制一套软件互锁状态机。硬件实时互锁由
PLC/ECS 负责，WES 只持久化 typed command evidence，并按以下业务因果串行推进：

- SOURCE_ARM 放盘和 TARGET_ARM 投放仍分别持久化 typed ACK/RESULT evidence。
- 北向下一次取料只由上一物料的 `southbound_pick_acknowledged`（南向 `PICK ACK`）解锁。
- UI、轮询快照、推测的平台占用状态或南向投放 `COMMAND_RESULT` 均不得替代该 ACK 因果。
- 设备结果不确定时进入 RuntimeHold/对账，不猜测平台已经腾空。

### 8.2 投影对账机制

WES 本地投影必须与物理状态保持一致。对账分为定期对账和关键节点对账：

| 对账类型 | 触发时机 | 对账内容 | 差异处理 |
|---------|---------|---------|---------|
| 定期对账 | Celery BEAT 每 30 秒 | 五层货架每面料箱数 vs WMS status query 快照 | 差异 ≥ 1 → 告警 + RuntimeHold |
| 定期对账 | Celery BEAT 每 30 秒 | 目标料箱在途状态汇总 vs 流水线/ECS 上报 | 不一致 → 告警 + 人工对账 |
| 关键节点对账 | 五层货架换面前 | 当前面所有料箱位置状态全量比对 | 不一致 → 暂停换面 + 人工处置 |
| 关键节点对账 | 五层货架回库前 | 当前货架实际箱清单、在途目标箱、库存增量凭证 | 当前货架快照不可信或仍有在途箱 → BLOCKED；单个目标箱缺失但当前货架快照可信 → 目标箱独立 MISSING_RECONCILING |
| 关键节点对账 | 单层货架移出前 | 源货架快照中所有料盘已离开源格确认 | 未全部离开源格 → 不得移出 |

对账不一致时，WES 不基于推测自动修正投影。WES 应记录差异事实、触发告警/RuntimeHold；只有获得 WMS/RCS 可信补证或人工确认后，才允许按补证结果更新当前货架或目标料箱投影。

### 8.3 目标料箱身份对账

CTU 投料任务必须同时维护授权候选集合与实际扫码集合：

| 集合 | 含义 | 更新时机 |
|------|------|----------|
| expected_authorized_bin_ids | WMS/RCS 授权并计划从五层货架当前操作面取出的目标料箱 | CTU 投料任务创建时 |
| actual_scanned_bin_ids | 流水线目标箱扫码确认过的真实目标料箱 | 每次 TARGET_BIN_SCAN_COMPLETED 成功解析 bin_id 时 |

当两个集合不一致时：

- 实际扫码出现但不在授权集合内的目标箱，按真实物理位置进入 `MOVE_TO_OUTPUT`、回架、隔离或人工对账，不作为可用目标箱。
- 授权集合中未实际扫码出现的目标箱，不得被假设在退箱区、流水线或五层货架原位，应进入 `MISSING_RECONCILING`。
- 如果 WMS/RCS 或人工补证确认当前五层货架实际快照可信，WES 可以按当前快照更新该货架投影，并将缺失目标箱作为独立资源对账项。
- 独立资源对账项不得参与后续分格、投料水位、可投目标箱数或 CTU 投料计划。

## 9. 异常与恢复原则

| 异常 | 处理原则 |
|------|----------|
| 目标料箱扫码失败 | WES 下发方向信号到退箱位，目标料箱进入退箱/回架链路，不作为可用目标箱 |
| 目标料箱扫码为未授权 bin_id | WES 下发方向信号到退箱位；按实际扫码 bin_id 进入退箱/回架或隔离，同时记录 expected_authorized_bin_ids 与 actual_scanned_bin_ids 差异 |
| 授权目标料箱未实际扫码出现 | 不得假设该箱在退箱区或仍在原货架位；标记 `MISSING_RECONCILING`，等待 WMS/RCS 补证或人工确认 |
| 流水线临时占位无法扫码识别 | 若可退箱则按未知物理箱退箱/隔离；若位置不可信或无法推进，记录 RuntimeHold，等待人工或设备恢复 |
| 源料盘扫码失败（无法解析 6 合 1 码） | TARGET_ARM 将料盘放入 NG 转存位 → 记录 NG 事实 → 继续处理下一个源料盘 |
| 源料盘扫码成功但无兼容目标格 | WES 下发方向信号释放当前工作位目标料箱 → 流水线自动将排队位下一个料箱推入工作位 → 料盘留在扫码平台等待新料箱的可兼容料格 → 源侧暂停继续取料；超出等待阈值后报警或人工处置 |
| 放料失败（放错位置、掉落、设备故障） | 设备上报 COMMAND_FAILED → 记录故障 → 相关链路进入 BLOCKED → 等待人工处置 |
| 工作位扫码不一致 | WES 下发方向信号到退箱位 → 记录异常 → BLOCKED |
| 无后续源料盘但仍有在途目标箱 | 进入末任务收口：停止新投料，释放 AT_WORK 目标箱，已识别箱退箱回架，未识别占位先扫码再退箱或进入对账 |
| WMS 库存增量接收失败 | 五层货架不得按正常完成回库；保留已放料事实与目标格计划，进入库存同步对账/RuntimeHold |
| 退箱区满 | 流水线自动停机 → WES 消费 WORKLINE_STOPPED → 优先调度 CTU 清理退箱区 → 恢复后继续 |
| NG 位满 | 硬件上报 NG_FULL + WORKLINE_STOPPED → WES 记录停线事实 → 相关链路进入 BLOCKED → 人工清理后硬件自动恢复 |
| WMS 分配 status 超时 | Owner 链路保持等待态，无超时取消；达到阈值后记录告警/RuntimeHold，迟到 typed terminal result 通过 ACK/reference 与版本校验后才可推进 |
| WMS E10 换面 status 超时 | Owner 链路保持等待态，无超时取消；达到阈值后记录告警/RuntimeHold，迟到 typed terminal result 通过 ACK/reference 与版本校验后才可推进 |
| 流水线拒绝投料 | CTU 等待重试，不退回五层货架 |
| CTU 投料/退箱任务失败 | 记录失败事实 → 进入 BLOCKED → 等待人工处置 |
| CTU 投料后 BIN_DEPARTED 超时 | 料箱已投入缓存位但流水线停线/步进电机故障导致 BIN_DEPARTED 未到达 → 达到超时阈值后记录告警/RuntimeHold → 投料链路进入 BLOCKED → 等待人工处置 |

异常恢复必须保留以下证据：

- trace、session、command、dispatch_key 或 source_event_id
- 发生位置、设备编码、外部任务 ID
- 当前源料盘身份和目标料箱/料格计划
- CTU 投料批次的 expected_authorized_bin_ids、actual_scanned_bin_ids、流水线临时占位和 resolved_bin_id
- 本次写入目标料箱/料格的库存增量与 WMS 接收凭证
- 已完成的物理动作和未确认的物理动作
- 操作员人工确认、设备恢复事件或 WMS/RCS 补证结果

## 10. 分格规则

1. 物料身份按 `Material + Vendor + DC/LC` 归集。
2. 当前字段映射中，`Material` 可来自 WMS 物料编码或 `ProductNo`，`Vendor` 可来自供应商编码或 `MfrPN`，`DC/LC` 对应 `DateCode/LotCode`。
3. 若目标料箱内已有相同 `Material + Vendor + DC/LC` 的兼容料格，优先归集到该料格。
4. 若没有兼容已占用料格，选择第一个满足尺寸、厚度、容量和禁混规则的空料格。
5. 不同 `Material`、不同 `Vendor`、不同 `DateCode` 或不同 `LotCode` 不允许混入同一料格。
6. 料盘尺寸、厚度、目标料箱类型、目标料格容量和累计厚度必须保持一致校验。
7. 源料盘扫码成功后仍找不到兼容目标格时，不进入 NG；料盘留在扫码平台，WES 释放当前工作位料箱并等待流水线自动补入有兼容料格的新料箱或人工处置
8. 分格成功只是计划和在途投影，不是物理事实。只有目标侧机械臂放入目标料格成功后，WES 才能记录物料占用事实。

## 11. 事件与命令边界

所有会推动状态机的输入都必须先由公共接入层持久化到 `RuntimeInbox`，再经 claim/processor 进入
generated dispatcher，由 Definition 与 `ROUTE_HANDLERS` 生成 typed intent/effect。所有对设备或外部系统的
副作用都必须通过 `SystemOutbox` 派发。

| 类型 | 来源 | 入口 | 说明 |
|------|------|------|------|
| 内部入口事件 | WES 上游流程 | `DEVICE_EVENT` | `SINGLE_LAYER_RACK_READY_FOR_SORTING` |
| 流水线位置事件 | ECS/流水线 | `DEVICE_EVENT` | `BIN_ARRIVED`、`BIN_DEPARTED`；未扫码前只更新流水线临时占位 |
| 目标料箱扫码事件 | ECS/流水线 | `DEVICE_EVENT` | `TARGET_BIN_SCAN_COMPLETED`；唯一可信目标箱身份绑定事件 |
| 工作位防呆扫码事件 | ECS/流水线 | `DEVICE_EVENT` | `WORK_BIN_SCAN_COMPLETED` |
| 源料盘扫码事件 | ECS/扫码平台 | `DEVICE_EVENT` | `SOURCE_REEL_SCAN_COMPLETED` |
| 工作线状态事件 | ECS/PLC | `DEVICE_EVENT` | `WORKLINE_STOPPED`、`WORKLINE_RESUMED`、`NG_FULL`、`NG_CLEARED` |
| 机械臂结果 | ECS/机械臂 | `COMMAND_RESULT` | 源侧/目标侧机械臂取放命令结果 |
| WMS 异步履约 | WMS | `SystemOutbox` 出站 + typed 状态 QUERY / `WMS_EFFECT_STATUS_HINT` | E08–E14 只按任务级 ACK 与终态结果收敛；逐箱物理推进来自 ECS 设备事件 |
| 超时/人工恢复 | Runtime/人工 | `TIMER_TIMEOUT` / `MANUAL_*` | 外部等待、设备等待和对账恢复 |

设备 ACK 只表示命令已被接收，不表示物理动作完成。推动流程继续的依据必须是命令结果、流水线事件或
WMS typed 终态结果/状态查询。

三类扫码事件必须使用不同事件类型，不依赖点位推断扫码主体。若现场协议只能上报通用扫码事件，接入层必须在
写入 `RuntimeInbox` 前映射为上述标准事件类型。

WES 向 WMS 提交库存增量、请求五层货架回库、请求目标箱回架、请求 CTU 投料/退箱等副作用，均必须通过 `SystemOutbox` 派发并保留幂等键。库存增量接收凭证必须能与目标货架回库请求关联。

## 12. 验收视角

首版流程文档可以通过以下场景检验是否覆盖完整业务闭环：

1. 一架单层货架（由上游粗分工作线完成粗分后）、一个五层货架、多个目标料箱的 happy path
2. 分拣机工作线开工时，WES 先检查分拣机五层货架工作位是否已有含空料格目标料箱的可用货架；无可用货架时请求 WMS 分配可用五层货架并调度 AGV 送达
3. 目标料箱满格后从流水线移出，CTU 取回并回五层货架
4. 两个单层货架中同 `Material + Vendor + DC/LC` 料盘可归集到兼容料格
5. 扫码平台发现源料盘身份与源侧机械臂预期不一致
6. 源料盘扫码成功但暂时无兼容目标格，WES 释放当前工作位目标料箱，流水线自动将排队位下一个料箱推入工作位，料盘留在扫码平台等待，源侧暂停继续取料
7. CTU 背篓满导致无法继续投料
8. 流水线出料口堵塞导致目标料箱无法取回
9. WMS status/typed terminal result 迟到、缺字段、reference 不一致或版本旧，WES 不静默推进；达到等待阈值后有告警/RuntimeHold 证据
10. 满箱交换后单层货架所有料箱均为空时，不进入分拣机排队位，而是调度至空架/空箱区
11. 当前操作面可用目标料箱取完后，先等待当前面在途目标料箱全部回架，再在另一面仍有可用目标料箱时提交 AGV 换面需求
12. 单层货架最后一个待处理源料盘离开源格后，AGV 即可将该货架移入空架/空箱区，STATION 释放后补入后续单层货架
13. 每个源料盘独立形成料盘分拣执行链路；单层货架搬运与补位由 RACK_QUEUE 控制链路闭环
14. AGV 送达五层货架或完成换面时必须带到达活动面，WES 按活动面约束请求 CTU
15. 流水线达到投料水位阈值时暂停继续投料，投料水位 = 空投料缓存位数 - 已投入但未离开缓存位的料箱数，水位恢复后继续
16. CTU 新任务选择退箱优先；已开始投料批次不可抢占，通过投料水位和保留容量避免退箱区死锁
17. 已离开源格的源料盘发生扫码或其他异常时进入 NG 处置，不回原单层货架
18. WES 以事件驱动为主触发，Celery BEAT 每 1 分钟兜底扫描
19. 目标料箱扫码使用 `TARGET_BIN_SCAN_COMPLETED`，WES 扫码后显式下发方向决策信号（MOVE_TO_WORK 或 MOVE_TO_OUTPUT），硬件切换挡停方向
20. 工作位二次扫码使用 `WORK_BIN_SCAN_COMPLETED` 做防呆确认（扫码位→工作位之间有缓存位，料箱到位后自动推入）
21. 源料盘扫码使用 `SOURCE_REEL_SCAN_COMPLETED`，不得与目标料箱扫码混用
22. CTU 投料/退箱是两个独立任务，串行调度，事件驱动触发
23. 五层货架回库和请求新货架是两个独立任务，WES 重新请求
24. v1 同一时间只有一个可信 AT_WORK 目标料箱，多个工作位物理编码不启用并行分拣
25. 目标料箱满格释放后，若无已通过准入的排队料箱，应触发 CTU 补充投料或等待 WMS 分配，同时启动工作位空闲计时器
26. 每个源料盘离开源格时创建独立料盘分拣 Session，business_key 唯一标识，扫码/放入为等待点，终态不反向阻塞源货架释放
27. 扫码平台不维护独立软件状态机；北向下一次取料只由上一物料的 `southbound_pick_acknowledged`（南向 `PICK ACK`）解锁
28. 定期对账（30 秒）和关键节点对账（换面/回库/移出）发现投影差异时，记录告警/RuntimeHold；未获得可信补证前不得基于推测修正投影
29. CTU 投料任务失败（背篓装载后无法到达投料口、投料循环中连续失败等）→ 记录失败事实，投料链路进入 BLOCKED，等待人工处置
30. CTU 投料阶段未扫码物理箱只生成流水线临时占位，不得提前绑定到真实 `bin_id`
31. `BIN_DEPARTED @ STATION_INPUT` 只表示入口缓存位腾空，不作为料箱身份确认依据
32. 授权集合 `{A, B}` 但实际扫码 `{A, C}` 时，`C` 按真实物理位置退箱/回架或隔离，`B` 独立进入 `MISSING_RECONCILING`
33. 无后续源料盘/源货架任务时，当前 AT_WORK 目标箱即使未满也必须释放到退箱区并回架
34. 末任务收口时，扫码区前、入口缓存、CTU 背篓中的目标箱必须回架、隔离或进入 RuntimeHold，不得悬空结束
35. 五层货架回库前，WES 必须提交本次新增料盘清单并取得 WMS 库存增量接收凭证，或回库请求携带等价凭证
36. 单个目标箱位置不明但当前货架实际快照可信时，当前货架可携带异常引用回库，缺失目标箱独立资源对账

## 13. 待补接口缺口

当前硬件和外部系统资料仍不足以直接进入代码实现。进入实施前必须补齐：

1. WMS E08–E14 ACK/status/typed terminal result 对“货架到位、批量任务完成/拒绝、料箱回架”的字段与 reference/version 约束
2. CTU 背篓容量、背篓槽位、可承载料箱类型和满载/空载状态口径
3. 分拣机五层货架工作位状态字段，以及 WMS E08 分配请求、ACK/status/typed terminal result 字段
4. 五层货架当前可操作面、目标料箱所在面，以及 WMS E10 换面请求、ACK/status/typed terminal result 字段
5. 满箱交换后"整架置空"的判断字段，以及空架/空箱区目标位置和完成回调
6. 单层货架从分拣机排队位到 STATION 的请求、到位、活动面和队列序号合同
7. 流水线步进电机点位事件、料箱到达/离开事件、挡停切换信号、轻量投料水位和保留出料容量配置项的现场默认值
8. 三类扫码事件合同：`TARGET_BIN_SCAN_COMPLETED`、`WORK_BIN_SCAN_COMPLETED`、`SOURCE_REEL_SCAN_COMPLETED` 的必填字段、扫码失败错误码和兼容映射规则
9. WES 投影维护目标料箱内容快照的具体字段和更新时机（目标料箱内容快照由 WES 本地实时投影维护，每次 TARGET_ARM 放料/退料后更新）
10. 源单层货架的源料盘顺序、料箱/料格快照和耗尽判定来源
11. 料箱回五层货架后的 WMS 库存确认与 WES 资源投影更新时间点
12. 工作线基础防呆事件和标准错误码
13. NG 位容量、满位停线、人工清理后自动恢复的事件字段
14. WES 方向决策信号合同：`MOVE_TO_WORK` 和 `MOVE_TO_OUTPUT` 的请求/响应字段（方向决策信号，ECS 收到后切换挡停方向）
15. WES 与 ECS/PLC 的投料握手协议：查询可用缓存位、逐箱允许信号、`BIN_DEPARTED @ STATION_INPUT` 事件用于投料循环（事件驱动，非同步等待）
16. 目标料箱在途状态字段：ON_RACK（在五层货架上）、IN_CTU（CTU 背篓中）、IN_PIPELINE（已扫码确认在流水线）、AT_WORK（工作位）、OUTPUT_READY（待退箱）、RETURNING（退箱中）、RETURNED（已回五层货架）、MISSING_RECONCILING（授权但位置不明）的事件来源和版本语义；五层货架每面可投数 = 当前可信快照中 ON_RACK 且状态不属于冻结、缺失或独立对账的目标箱数 - 已预约但尚未离架的投料任务数
17. 外部等待报警/RuntimeHold 阈值：WMS/RCS 分配、AGV 到位、AGV 换面、CTU 批量任务和回架确认分别采用的阈值
18. 源料盘停留扫码平台等待新料箱可兼容料格的超时阈值、人工处置方式和恢复事件字段
19. 工作位空闲超时告警阈值：释放当前工作位料箱后无已通过准入的排队料箱时，等待 CTU 补充投料或 WMS 分配新货架的超时阈值
20. 投影对账差异的告警阈值和升级策略：定期对账（30 秒）和关键节点对账（换面/回库/移出）发现不一致时的处理流程和人工介入条件
21. 源货架快照和五层货架投影的缓存策略：Redis 缓存 key 格式、TTL、缓存加载时机（单层货架到达 STATION / 五层货架到位换面）、放料成功后异步更新投影的时序
22. 流水线临时占位字段：placeholder_id、投料批次、入口/扫码位位置、identity_status、candidate_authorized_bin_ids、resolved_bin_id、resolved_at 和异常来源
23. CTU 投料任务回调字段：expected_authorized_bin_ids、CTU 背篓槽位、实际投放计数、无法识别单箱身份时的占位映射规则
24. 授权集合与实际扫码集合差异处理合同：未授权实际箱的退箱/隔离路径、授权未出现箱的 `MISSING_RECONCILING` 口径、WMS/RCS 补证字段
25. WES 向 WMS 提交库存增量的接口字段：rack_id、bin_id、cell_id、reel_id/pkg_id、6 合 1 码解析结果、Material、Vendor、DateCode、LotCode、规格、source/target 位置、put_completed_at、trace/session/command、接收凭证号
26. 五层货架回库请求携带字段：当前可信货架快照、库存增量凭证、未关闭资源对账引用，以及 WMS E09 ACK/status/typed terminal result 对当前快照的接受证据
27. 末任务收口合同：无后续源料盘时工作位目标箱释放、扫码区前未知占位推进/扫码/退箱、CTU 背篓未投箱直接回架、不可推进时 RuntimeHold 字段
