# WorkLine 多对象状态机收敛设计

日期：2026-06-19
状态：Draft for review（已审计修订）

## 审计修订记录

本文档于 2026-06-19 经 `/plan-eng-review` 多轮审计 + 端到端流程模拟。各轮累计修订要点（详见正文相应小节）：

| 轮次 | 主题 | 关键修订 |
| --- | --- | --- |
| 一 | 事实偏差修正 | `plugin_state` 已被迁移删除、当轮曾决定重新落库（后续 CEO 评审已废弃此决定）；WorkLine 生命周期枚举纠正为 `STOPPED/READY/RECONCILING/ESTOPPED`；`DeviceCommand` 字段 `action→task_type`；`MATERIAL_UNIT/REEL/physical_form` 标注为新引入抽象；7 队列+扫码点3/4 标注为物理现状；补状态映射表/transition 合同/ASCII 图/RECONCILING 触发退出/manifest 扩展成本；新增三层对象边界总纲与端到端生命周期。 |
| 二 | 一致性与合同 | `current_activity` 表 `HandlingMove.current_queue→BinTransitMembership.current_queue`；命名风格"不混用"改为"按语义选前缀"；DRY 收敛料盘终结三连表述；RECONCILING 转移由"任意状态"约束为受约束恢复集；第一阶段纳入非法 transition 软告警；`NG_HANDLING` 成功/失败语义、RELEASE_FACT 归因精确化。 |
| 三 | 逻辑漏洞与可实施性 | 补 `allocation_policy` 四类出口（`PROJECTION_INCONSISTENT→RECONCILING`、其它 `REJECTED→BLOCKED`）；`awaiting_command_id` 行号 208→209；落库补 downgrade 策略；软告警补判定时机/`from_state` 来源；当轮曾补字段归属（旧结论为仅 `plugin_state` 落库，后续已由 `material_units.status` 取代）。 |
| 四 | 纠正三轮误判 | 纠正 SMT 粒度：非"一箱多盘循环"，而是一盘一 Session（`session_code` 带 `source-item:{item.id}`）；纠正 complete 时机：两边设计一致（入库成功自动 complete），SMT 现状重置回 `WAITING_SOURCE_PICK` 不 complete 是旧"一箱一 Session"遗留死代码，修复方向 `RuntimeIntent.complete()` 对齐粗分机。 |
| 五 | 纠正四轮残留 + 粒度精确 | 修验收标准残留"粗分机自动/SMT 人工"旧表述（与四轮结论冲突）；补 SMT 线性推进约束（后修正：非 WorkLine 串行锁，是扫码平台容量=1 的设备约束）；端到端图区分目标态/现状；压缩本修订记录为摘要表。 |
| 六 | 人工介入边界 | 新增权威边界：仅 `NG_HANDLING`/`RECONCILING` 允许人工，其余全自动；端到端图"人工满箱交换"→系统自动；`SORTING_SESSION_COMPLETE_REQUESTED` 人工 complete 事件由"保留兜底"改为"移除"（违反边界）；BLOCKED 恢复改为自动重试/升级、不依赖人工；`RuntimeHold.reason_code` 仅 NG/RECONCILING 相关可人工解除。 |
| 七 | 收尾性审查 | 修验收标准 805/809 重复（合并为一条）；SMT 自动 complete 修复补入实施层面验收（原漏）；`reconciliation_from_state` 字段决策落库（原 transition 合同要求记录 from_state 但现状无此字段，C 阶段强校验缺依据），当轮曾随 plugin_state 迁移同批生成，后续重塑后改归 `material_units.reconciliation_from_state`。 |
| 流程模拟 | 料盘实体缺口 | 模拟扫码流程时发现：料盘身份散在 `context_json`（扫码时）与 `resource_bin_material_mounts`（入箱时），无独立料盘实体表，导致跨 Session 不可关联、在途/NG 料盘无活记录。新增「料盘身份记录现状与缺口」小节，明确不纳入第一阶段、列为后续 TODO。 |
| 数据模型诊断 | resource 域散乱 | 核验 resource 域 18 张表后诊断：料盘定位跨 5 表、Occupancy/Mount 字段冗余、缺料盘实体表（根因）、session_id 类型不一致、投影表无外键。合并升级为「数据模型现状诊断与后续治理」节，给出 P1-P5 真问题 + A1-A3 可接受权衡 + 治理优先级，全部列为后续工作项、不纳入第一阶段。 |
| 料盘根域 | 目标架构方向 | 端到端流程走通后确立目标架构：料盘为根域（料盘中心模型），从 SCAN_COMPLETED 建料盘实体、后续操作为料盘状态+位置变化。两个关键约束：全局 ID 用 `pkg_code`（非 `material_identity_key`，后者同批次共享）；料盘实体与 resource 投影叠加非替换（投影仍承载料箱格容量/冲突/对账）。列为后续独立架构演进，需单独评审，不纳入第一阶段。 |
| 串行误读纠正 | 设备线性推进 | 纠正前几轮"单线串行"过度概括：WorkLine 不按整体串行控制，料盘按设备节点线性推进。SMT 扫码平台容量=1，上一盘占用（`current_material` 未关）期间 `_target_has_open_current_material`（`TARGET_SESSION_BUSY`，`service.py:712`）不 claim 下一盘；离开扫码平台即可 claim。是设备容量约束非 WorkLine 串行锁。同步修 complete 时机小节、验收断言、第五轮记录措辞。 |
| CEO 评审重塑 | 第一阶段方向重定 | /autoplan CEO 双声（codex+Claude subagent）一致：原"plugin_state 列治标+根域治本分两阶段"是"6个月后悔"风险。重塑：料盘根域合并为第一阶段主体，plugin_state 列不加。料盘状态机 5 态（IN_TRANSIT/STORED/COMPLETED/NG/RECONCILING），物视角。NG 是业务问题（料盘不合格进NG域，单向不回正常流，写ng_return_items+清理material_unit）；RECONCILING 是功能问题（系统状态不可信，对账后可回正常态，保持现状不预设分类）。SMT complete 移除+自动转NG安全阀。砍任务6审计、砍RECONCILING自动恢复设计。 |
| 重塑自洽修正 | 旧方向残留清理 | 第十三轮 Eng 评审发现重塑改了4处但10+处旧 plugin_state 内容没同步，文档自相矛盾。修正：背景/目标/方案选型B/对象边界表/C阶段TODO/数据模型诊断边界/实施验收/开放点 全部改为料盘根域方向；旧9态章节（命名/Session状态/转移图/映射表）加重塑说明标注"保留作现状代码参照，非权威定义"；验收标准 alembic 迁移改为 material_units 建表。 |
| Epic 对齐复审 | Phase1 范围校准 | 参考 `~/.gstack/projects/kaizhoumasha-wes_backend/specs/20260620-124757-1216-workline-root-domain-phase1-epic.md`，确认第一阶段按 6 个子 issue 合并为 1 个 PR 推进，不再收缩范围；修正文档残留：manifest `state_owner=MaterialUnit.status`、软告警挂载在 `material_units.status` 写入、`current_activity` 不再引用落库 `plugin_state`、RECONCILING `from_state` 归 `material_units.reconciliation_from_state`、C 阶段 TODO 改为根域 transition。 |

正文为事实准绳，本记录仅索引各轮主题与关键结论，不重复正文。

## 背景

当前 WorkLine 插件中的业务状态主要散落在插件 `context_json` 中，例如粗分机的 `phase`（`src/workline_plugins/rough_sorter/context.py:19`、`contract.py:20-26`）和 SMT 分拣机的 `sorting.business_phase`（`src/workline_plugins/smt_sorting_inbound/context.py:223`、`constants.py:13-18`）。这些字段混合了承载对象不同的状态：

- 料盘/物料处理单元的业务阶段。
- 料箱在流水线中的通行位置。
- 货架位 active projection 是否可用。
- Station / 扫码平台是否被占用。
- 设备命令等待、Result、Timeout。
- Session 完成、失败、人工对账等生命周期。

这会导致两个问题：

1. 插件状态名越来越细，容易把每个设备节点都写成业务阶段。
2. SMT 分拣机场景中，料盘、料箱、货架位、流水线队列同时存在，单一 `business_phase` 无法准确表达多对象并发状态。

> **代码现状**：`WorklineSession` 模型（`src/app/workline/models/session.py:338`）当前**没有** `plugin_state` 字段（已被迁移删除），业务阶段散落在 `context_json` 中。`Session.status`（`SessionStatus`，`session.py:30-40`）取值 `NEW/RUNNING/WAITING_DEVICE_RESULT/WAITING_EXTERNAL/MANUAL_HOLD/COMPLETED/FAILED/CANCELLED`。

本设计目标是收敛各对象状态机的边界和描述方式。第一阶段做**料盘根域（material_units）+ 合同 + 可观测性**；第二阶段（C 阶段）才让 Runtime 强制执行所有 transition。

## 目标

- 统一 WorkLine 状态机描述方式，避免每个插件自定义一套概念。
- 明确料盘根域 `material_units` 的对象归属和粒度，**第一阶段建料盘根实体表**（取代原 plugin_state 列方向）。
- 区分料盘状态、料箱流水线队列、货架位投影、Station 占用、命令状态和 WorkLine 生命周期。
- 支持粗分机和 SMT 分拣机作为首批示例。
- 将 Runtime 强制 transition 校验放入 C 阶段 TODO。

## 非目标

- 不复制 WMS 的货架和料箱主数据生命周期。
- 不把每个设备节点建成 `Session.plugin_state`。
- 不在第一阶段新增 Runtime 强制 transition 机制（C 阶段才做）。
- 不改变现有 `/callback/event`、`/callback/result` 入站协议。
- 不在第一阶段迁移历史 `context_json` 全量数据（仅迁移当前活跃 phase/business_phase 值到新枚举）。
- 不在本设计中实现转线能力；扫码点 3 的转线只作为后续扩展点。
- 不新增 `workline_sessions.plugin_state` 列（料盘根域 `material_units.status` 取代，见「第一阶段决策」）。
- 不预设 RECONCILING 触发分类与自动/人工恢复边界（保持现状，上线后按需优化）。
- 不一次性迁移 context_json 散读面（~500 处，第一批只改关键读路径，散读待 C 阶段）。

## 方案选型（A/B/C）

第一阶段聚焦范围有三档方案：

| 方案 | 内容 | 取舍 |
| --- | --- | --- |
| A | 只写文档合同，状态仍散落 `context_json`，不动 DB。 | 改动最小，但 Runtime 无法强校验、状态不可查询索引、C 阶段无合同落点。 |
| **B（本设计采纳，CEO 评审重塑）** | **料盘根域（material_units）+ manifest 合同 + 状态写面迁移 + 关键读路径直连 + SMT 自动 complete + NG 自动进域 + 软告警。Runtime 不强制 transition。** | **料盘状态可查询、跨 Session 直连、定位一次查询；治本避免 plugin_state 列返工。第一阶段有一次 Alembic 迁移（建 material_units 表）。** |
| C | B 全部 + Runtime 强制 transition + 统一 transition event。 | 完整版，但第一阶段改动过大、风险高，拆到 C 阶段。 |

> 原稿「第一阶段采用方案 B」无上下文，此处补全 A/B/C 对比。第一阶段采纳 B，C 阶段见末尾 TODO。

## 第一阶段决策：料盘根域（material_units）

> **重塑说明**：经 CEO 双声评审（codex + Claude subagent 一致），第一阶段从原"落库 `plugin_state` 列"重塑为"料盘根域"。根因：文档自诊断 P1/P2/P3 同源于"缺料盘实体表"，治本应优先于治标。`plugin_state` 列不加——`material_units.status` 取代它，避免"加列→C阶段迁移→与根域同步"的三段式浪费与双写不一致。

### 料盘根实体表 material_units

新增 `wes_biz.material_units` 表，扫码时建实体，状态/位置变化时更新。**料盘是根域，后续插件操作都是对该料盘状态及位置的变化。**

```text
material_units (自主主键, 复用 BaseMixin)
  id              主键 (BaseMixin 自增/雪花)
  pkg_code        业务键 (PkgID, 单盘物理唯一, 索引)
  material_identity_key  物料属性键 (MAT:code:vendor:date:lot, 同批次共享)
  six_in_one      JSON (六合一码全字段)
  status          料盘状态机 (IN_TRANSIT/STORED/COMPLETED/NG/RECONCILING)
  current_location 当前格位/工位 (bin_code+cell / 工位码)
  current_session_id  当前处理 Session (引用 workline_sessions.id)
  reconciliation_from_state  对账前 status (RECONCILING 时记, nullable)
```

关键约束（设计文档已定，实施时落地）：
- **主键是自主 ID（BaseMixin），不是 `pkg_code`**——`pkg_code` 是业务键可变/可复用，不做主键。
- **全局料盘 ID 用 `pkg_code`**（非 `material_identity_key`，后者同批次共享，是物料属性不是料盘身份）。
- **material_units 保留当前料盘根实体**——NG 判定且搬运成功后写入 `ng_return_items`（已存在表，`runtime_hold.py:215`），并清空当前 Session 绑定；`material_units` 中保留 NG 状态用于当前追溯。
- **与 resource 投影叠加非替换**——`resource_bin_material_mounts` 等仍承载料箱格容量/冲突/对账，通过 `pkg_code` 关联 material_unit。

### 料盘状态机 material_units.status

料盘视角（物在哪/什么处境），与 Session 处理过程解耦：

| 状态 | 含义 | 触发 | 去向 |
|---|---|---|---|
| `IN_TRANSIT` | 料盘在途（扫码建实体→流水线/扫码平台/搬运/分配/放盘） | SCAN_COMPLETED 建实体；状态变化停留本态 | 自动流转 |
| `STORED` | 粗分上架（单层箱暂存，待 SMT 分拣） | 粗分 MATERIAL_MOUNTED 成功 | 自动（SMT 取出回 IN_TRANSIT） |
| `COMPLETED` | 最终上架（五层箱，端到端完成） | SMT MATERIAL_MOUNTED 成功 | 终态 |
| `NG` | **业务问题**——料盘不合格，进 NG 域（单向，不回正常流） | 扫码NG/测量NG/WMS拒绝/身份不一致 | 自动进 NG 域（搬运成功→写 ng_return_items + 清空 Session 绑定，保留 material_unit） |
| `RECONCILING` | **功能问题**——系统状态不可信，必须对账确认 | 现有触发源保留（见下） | 对账后回正常态（不预设分类，上线后按需） |

**NG vs RECONCILING 本质区别**：NG 是**业务问题**（料盘本身不合格，业务判定进 NG 域，单向不回正常流）；RECONCILING 是**功能问题**（系统状态不可信，如超时/ACK耗尽/投影冲突，对账后可回正常态）。两者不重叠。

#### 料盘状态机 transition 合同（权威定义，manifest 填充 + 软告警校验依据）

```text
IN_TRANSIT  -> STORED | COMPLETED | NG | RECONCILING
              # STORED: 粗分 MATERIAL_MOUNTED 成功(单层箱暂存)
              # COMPLETED: SMT MATERIAL_MOUNTED 成功(五层箱终态)
              # NG: 业务判定不合格(扫码NG/测量NG/WMS拒绝/身份不一致), 进NG域
              # RECONCILING: 功能问题(超时/ACK耗尽/分发失败/投影冲突), 状态不可信
STORED       -> IN_TRANSIT | NG | RECONCILING
              # IN_TRANSIT: SMT 取出分拣
              # NG/RECONCILING: 同上
NG           -> (终态: 写 ng_return_items + 清空 Session 绑定, 不回正常流)
              # NG 搬运命令成功后保留 material_unit 供当前追溯
RECONCILING  -> IN_TRANSIT | STORED | COMPLETED | NG
              # 对账结论决定回哪个正常态(或转NG)
              # 受约束恢复集: 回对账前状态或合法后继, 不跳任意状态
              # 第一阶段不预设自动/人工, 保持现状
COMPLETED    -> (终态)
```

> 此 transition 合同是 manifest `state_machines.transitions` 的填充依据（子issue5）、软告警的校验依据（子issue6）。RECONCILING 出口第一阶段按"目标态在合法恢复集内"校验（不依赖 reconciliation_from_state 运行时值），C 阶段强校验再用 from_state。

### RECONCILING 现状保留（前期不过度设计）

前期**不预设 RECONCILING 的触发分类与自动/人工恢复边界**。代码现有触发源保留不动：
- Session 级（`session.py:58-71`）：`CALLBACK_DEADLINE_EXPIRED`/`COMMAND_ACK_EXHAUSTED`/`OUTBOX_DISPATCH_FAILED`（超时/ACK耗尽/分发失败）
- Resource 级（`projection_service.py` 8 处 conflict hold）：入账/出账/放置投影冲突
- `resolve_runtime_reconciliation` 人工解除 API（`operation.py:314`）保留

**不在第一阶段设计"哪些自动恢复/哪些人工"**——上线后遇到实际问题再优化。砍掉原任务6（RuntimeHold reason_code 审计）。

### NG 自动记账（决策2 安全阀）

SMT complete 移除 `SORTING_SESSION_COMPLETE_REQUESTED` 后，安全阀用"自动转 NG"替代（NG 是业务问题，自动进 NG 域）：
- 放置成功后若检测到异常（如下游确认失败）→ 自动 `material_units.status=NG` + 写 `ng_return_items` + 清空 Session 绑定
- **不进 RECONCILING**（RECONCILING 是功能问题，留给现有机制）
- NG 搬运命令成功后才清空 Session 绑定，`material_units` 保留 NG 状态（避免料盘物理在途但记录消失，并支持当前追溯）

### 不做的（对比原 backlog）

- ❌ `workline_sessions.plugin_state` 列——不加，material_units.status 取代
- ❌ `workline_sessions.reconciliation_from_state` 列——改归 material_units
- ❌ 任务6 RuntimeHold reason_code 审计——砍，上线后按需
- ❌ RECONCILING 自动恢复路径设计——不预设，保持现状
- ❌ context_json 散读面（~500 处）一次性迁移——第一批只改关键读路径（handoff/resource/诊断 ~20-30 处），散读保留兼容待 C 阶段

## 核心结论

### 统一 Session 主体

`WorklineSession` 统一定义为一个 `MATERIAL_UNIT` 的处理会话。

> **新引入抽象**：`MATERIAL_UNIT` / `physical_form` / `REEL` 是本设计引入的统一身份分类概念，**代码现状不存在**该字段（`physical_form`、`MATERIAL_UNIT` 全仓库零命中）。当前料盘语义靠 `reel_thickness_mm`、`reel_diameter` 隐式表达。本设计在 manifest 合同层引入该抽象统一描述，第一阶段不强制改 Session 数据模型加 `physical_form` 列，作为 manifest 合同概念存在。

当前粗分机和 SMT 分拣机处理的物料单元物理形态都是 `REEL`。差异只在身份来源：

| 插件 | Session 主体 | 身份来源 |
| --- | --- | --- |
| 粗分机 | `MATERIAL_UNIT / REEL` | `PkgID`、六合一码、barcode decision business key |
| SMT 分拣机 | `MATERIAL_UNIT / REEL` | `handoff_source_item_id`、source pick request、后续 `material_identity_key` |

`source_item` 不是状态机主体，它只是 SMT 分拣机创建料盘 Session 的业务锚点。

### 料盘状态粗粒度

第一阶段权威状态落点是 `material_units.status`。它表达"这盘料的业务处境"，不是"这盘料经过的每个设备节点"。

一个状态值得进入 `material_units.status`，至少应满足一项：

- 影响下一步业务决策。
- 失败恢复时需要知道这盘料的业务处境。
- 人工处理界面需要用它判断当前处理方式。
- 跨越异步等待，例如等待设备结果、外部资源、WMS/RCS 或人工确认。
- 表示不可逆业务节点，例如源格出账、目标格入账、NG 判定。

不应进入 `material_units.status` 的内容：

- 等某个机械臂 command result。
- 经过某个 conveyor 或设备 role。
- 某个 topology edge 正在执行。
- 命令 ACK、Result、Timeout。

这些应由 `DeviceCommand`、`awaiting_command_id`（`session.py:209`）、Timeline、resource fact、resource wait 和 topology 高亮表达。

### 多对象不是一个大状态机

统一的是状态机描述方式，不是把所有对象塞进一个状态机。

对象边界如下：

| 对象 | 状态归属 | 第一阶段用途 |
| --- | --- | --- |
| 料盘 / Material Unit | `material_units.status`（料盘根实体，第一阶段新建） | 当前料盘状态、位置、跨 Session 关联 |
| 料箱在流水线中 | Handling / Pipeline queue membership | 多料箱并发、队列顺序、扫码门控 |
| 料箱可用性投影 | Resource active projection | 当前目标/源料箱能否参与作业 |
| 货架位 | Rack position active projection | WorkLine 位置是否有可信可用投影 |
| Station / 扫码平台 | Station projection / lease | 占用、可用、对账 |
| DeviceCommand | Runtime / DeviceCommand | 命令生命周期 |
| WorkLine | WorkLine lifecycle | `STOPPED/READY/RECONCILING/ESTOPPED`（见 `workline/models/safety.py:18`） |

> WorkLine 生命周期使用现有 `WorkLineRuntimeStatus` 枚举（`STOPPED/READY/RECONCILING/ESTOPPED`），本设计不改该枚举。

## 货架位 / 料箱 / 料盘 三层对象状态边界

货架、料箱、料盘三个对象常被混在一起谈，但它们的状态机完全独立。谁动谁不动必须分清，这是本设计的核心边界。

### 料盘（Material Unit / REEL）— 状态机主体

- 状态归属：`material_units.status`（第一阶段新建料盘根实体表）。
- 生命周期：从进入处理链路到**落入目标格上架完成**为止。
- 终结点：`TARGET_PLACING` 成功 + `MATERIAL_MOUNTED` 入账五层目标格 = **入库上架完成**。此刻料盘状态机终结。
- `Session.status=COMPLETED` 是 Session 的收尾确认（无在途物料），**不是**料盘入库完成的判定点。单盘闭环即入库完成。
- 料盘上架后，料箱后续任何移动（满箱回库、搬运、队列变化）都**不改变料盘状态**。

### 料箱（BIN）— 两个独立视角

- `BIN_PROJECTION`：料箱能否被当前作业使用（`AVAILABLE/RESERVED/UNAVAILABLE/RECONCILING`），归 resource domain。
- `BIN_TRANSIT`：料箱在 SMT 流水线队列中的通行与门控（`QUEUED/GATE_CHECKING/ACTIVE/BLOCKED/DONE/RECONCILING`），归 Handling / Pipeline queue。
- 满箱回库、CTU 搬运、料箱在队列间移动，全部是 **`BIN_TRANSIT` 视角**，与料盘状态无关。
- 当前**不支持料箱转线**（一条 SMT 线的料箱转到另一条 SMT 线）。转线即使支持，也只改变料箱 `BIN_TRANSIT` 的 `current_queue`，**不改变料盘状态**——料盘一旦在 `TARGET_PLACING` 上架完成，其状态机已终结。

### 货架 / 货架位（RACK_POSITION）— 只维护投影

- WMS 拥有货架主数据和完整生命周期，WES 不复制。
- WES 只维护"某 WorkLine 货架位是否绑定可信 active projection、是否可作业"（`UNBOUND/AWAITING_BINDING/AVAILABLE/UNAVAILABLE/RECONCILING`）。
- 货架位是料箱的物理承载位置；料箱投影依赖货架位投影可用。货架上挂的是料箱，料箱格里放的是料盘。

### 三者关系（谁承载谁、谁依赖谁、谁动谁不动）

```text
WMS/RCS: 货架资产生命周期（主数据，WES 不复制）
   │
   │  投影
   ▼
WES 货架位状态（RACK_POSITION projection）
   │  UNBOUND / AWAITING_BINDING / AVAILABLE / UNAVAILABLE / RECONCILING
   │
   │  货架位 AVAILABLE 才能承载料箱
   ▼
WES 料箱投影（BIN_PROJECTION）              WES 料箱通行（BIN_TRANSIT）
   │  AVAILABLE / RESERVED /                  │  QUEUED / GATE_CHECKING /
   │  UNAVAILABLE / RECONCILING               │  ACTIVE / BLOCKED / DONE
   │  （能否被作业使用）                        │  （在流水线队列哪一段）
   │                                          │
   │  目标料箱 AVAILABLE + 工作位 ACTIVE        │
   │  才允许料盘入库                            │
   ▼                                          │
WES 料盘状态（material_units.status）          │
   │  IN_TRANSIT / STORED / COMPLETED / NG      │
   │  / RECONCILING                            │
   │  （最终入五层目标格后 COMPLETED）            │
   ▼                                           │
入库上架完成（料盘状态机终结）                   │
                                              │
   满箱回库 / CTU 搬运 / 料箱队列移动 ◄─────────┘
   （BIN_TRANSIT 视角；料盘已上架，状态不再变）
```

关键边界（权威定义，后文引用此处）：

- 料盘状态机**不关心**料箱在流水线经过几个扫码点，只关心目标资源是否满足作业条件（目标料箱在工作位 `ACTIVE` + `BIN_PROJECTION=AVAILABLE`）。
- 料盘落入目标格后状态机终结；料箱后续移动（满箱回库、搬运、队列变化、转线）属 `BIN_TRANSIT`，不改变料盘状态。料箱转线当前不支持，即使支持也只动 `current_queue`。

### 人工介入边界（权威定义，后文引用此处）

> **重塑（决策3）**：原"仅 NG/RECONCILING 人工"收窄。NG 是业务问题（料盘不合格，自动进 NG 域，单向不回正常流）；RECONCILING 是功能问题（系统状态不可信，对账后可回正常态），但前期不预设触发分类与自动/人工边界。

**NG 是业务问题，全自动**：料盘不合格（扫码NG/测量NG/WMS拒绝/身份不一致）→ 自动进 NG 域（搬运 + 记账 ng_return_items + 清空 Session 绑定，保留 material_unit）。单向，不回正常流，不人工。

**RECONCILING 是功能问题，不可自动恢复**：系统状态不可信（超时/ACK耗尽/分发失败/投影冲突），必须对账确认。前期保持现状触发源，不预设"哪些自动/哪些人工"——上线后遇到实际问题再优化。

**其余全自动**：
- 正常入库成功后 Session 自动 `complete`，不需要人工发完成事件。
- 满箱换箱/换架由系统自动调度（`smt_inbound_handoff_service._request_full_box_exchange`，`service.py:2047`）。
- 资源等待等外部系统（WMS/RCS）回调。
- SMT complete 移除后，安全阀用"自动转 NG"替代（NG 是业务问题，自动进 NG 域），不进 RECONCILING（功能问题，留现有机制）。

> 现状偏差：SMT `SORTING_SESSION_COMPLETE_REQUESTED` 把正常入库的 Session 收尾做成了人工事件（见「complete 时机」），违反此边界，属待修遗留。

## 料盘端到端生命周期（粗分机 → 分拣入库上架完成）

一个料盘（REEL）依次经过两条独立工作线：粗分机（前端准入 + 粗分入单层箱）→ SMT 分拣机（二次扫码 + 精分入五层箱）。物理交接点是**单层料箱格**，两条线通过 `SmtInboundHandoffDemand / SourceItem` 单向 handoff 串联，不是同一条线的两阶段，也不是转线。

> **料盘线性推进 vs 料箱多区域并发**（勿混淆）：料盘按设备节点线性推进——SMT 扫码平台容量=1，一次处理一盘（见「complete 时机」线性推进约束）；料箱则在流水线多区域并发——多个料箱同时分布在入料缓存/等待/回收等不同队列（见「流水线建模为多个队列」）。前者是料盘视角的设备容量约束，后者是料箱视角的流水线队列并发，两者不矛盾：一盘料线性通过扫码平台的同时，多个料箱在流水线各区域排队通行。

### 端到端全景图

```text
┌─────────────── 粗分机工作线 (ROUGH_SORTER) ───────────────┐
│  来料料盘  POST /callback/event  SCAN_COMPLETED             │
│     │  barcode_decision OK                                   │
│     ▼                                                        │
│  INTAKE_HANDLING (扫码→取盘→测量→WMS准入→前进)               │
│     │  bin_allocator.plan_allocation()                       │
│     ├──ALLOCATED──────────┐    └──RACK_OPERATION_REQUIRED──►AWAITING_STORAGE_RESOURCE
│     │                      │              WMS/RCS货架到位回调──┐
│     │                      ▼                                   ▼
│     │                 STORING_TO_BIN  ◄──────────────────────重试
│     │                      │  PUT_TO_BIN SUCCESS
│     │                      │  CONSUME_BIN_CELL + MATERIAL_MOUNTED(入单层格)
│     ▼                      ▼
│  NG_HANDLING ◄──NG    粗分 Session 闭环(material_units.status=STORED)
│  (搬至NG区)              │
│                          │  产出 ROUGH_SORTER_RELEASE_FACT
│                          │  (single_layer_rack_code + bin_snapshots)
└──────────────────────────┼──────────────────────────────────┘
                           │  smt_inbound_handoff_service.create_or_get_from_release()
                           │  幂等创建 Demand + SourceItem(每料格一个)
                           │  evaluate() 判断直接分拣 / 满箱交换
                           ▼
┌─────────────── SMT 分拣机工作线 (SMT_SORTING_INBOUND) ──────┐
│  claim_next_source_item() 两阶段认领(路由+ECS探针)            │
│     │  写入 handoff_source_item_id, 下发 SORTING_SOURCE_PICK_REQUESTED
│     ▼                                                        │
│  SOURCE_PICKING  COMMAND_SOURCE_PICK成功, 源格出账             │
│     │  MATERIAL_UNMOUNTED, 扫码平台OCCUPIED                   │
│     ▼                                                        │
│  AWAITING_SCAN  ◄── WORKING_BIN_SCAN(校验物料身份+厚度)        │
│     │  allocation_policy.allocate()                           │
│     │        │                                                │
│     │   ALLOCATED──┐         └──NO_CAPACITY──►AWAITING_TARGET_RESOURCE
│     │              │                            (目标箱满, 等换箱)
│     │              ▼              系统自动满箱交换后重试────────┘
│     │     TARGET_PLACING  COMMAND_TARGET_PLACE成功             │
│     │              │  MATERIAL_MOUNTED(入五层目标格)            │
│     │              ▼                                           │
│     │     料盘状态机终结(入库上架完成) ◄── NG路径: NG_HANDLING  │
│     │     (料盘生命周期到此为止)            (身份不一致→NG放盘)   │
│     │     Session自动COMPLETED (实施修复后; 现状见「complete时机」⚠️)│
└─────┼──────────────────────────────────────────────────────────┘
      │  (以下全是料箱 BIN_TRANSIT 视角, 与料盘状态无关)
      ▼
满箱 → handling_operation(CTU搬运, 非"转线") → buffer/回库
料箱在流水线队列 current_queue/queue_position 变化
```

### 第一段：粗分机（前端准入 + 粗分入单层箱）

**入口**：外部设备 `POST /api/v1/callback/event` 上报 `SCAN_COMPLETED`（`callback.py:75`、`plugin.py:697`、`contract.py:12`）。

| 转移 | 触发 | 代码 |
| --- | --- | --- |
| 创建 → `INTAKE_HANDLING` | SCAN_COMPLETED 入站，解析六合一码 | `plugin.py:697-760` |
| `INTAKE_HANDLING` 内 | barcode_decision OK → 取盘入线 → 测量 + WMS 库存准入（匹配 HHPN/LotCode）→ 前进到出料决策点 | `plugin.py:710-729`、`875-970`、`633-696` |
| → `AWAITING_STORAGE_RESOURCE` | `bin_allocator` 返回 `RACK_OPERATION_REQUIRED`（无合适格/需换架） | `_rack_operation_required_intents:1194-1271` |
| → `STORING_TO_BIN` | `bin_allocator` 返回 `ALLOCATED` | `_storage_allocation_intents:1091-1136` |
| `AWAITING_STORAGE_RESOURCE` → `STORING_TO_BIN` | WMS/RCS 货架到位回调 → 内部 `ROUGH_SORTER_STORAGE_RETRY` 重试分配 | `on_external_http:800` → `handle_storage_retry:762` |
| `STORING_TO_BIN` → **粗分 Session 闭环 / `material_units.status=STORED`** | PUT_TO_BIN SUCCESS，消耗料格 + 记录 `MATERIAL_MOUNTED` | `plugin.py:1021-1083` |
| 任意 → `NG_HANDLING` | 扫码 NG / 测量 NG / WMS 拒绝，搬至 NG 区 | `plugin.py:740/890/929` |

**目标存储资源**：单层料箱格 `SINGLE_LAYER_A`（`manifest.yaml:29-37`）。料盘由 output arm 放入单层箱某格（`plugin.py:1178-1191`）。

**产出**：粗分机入箱完成后，由 `single_layer_rack_orchestration_service` / `runtime_hold_release_service._write_release_facts` 写入 `ROUGH_SORTER_RELEASE_FACT`（含 `single_layer_rack_code` + `bin_snapshots_json`），非插件直接产出。

### 交接：单层箱是物理交接点

- 粗分机 output arm 把料盘**放入**单层料箱格；SMT source arm 从**同一个**单层料箱格**取盘**（`flow_service.py:117`，SMT manifest `SOURCE_STATION_A/B` 允许 `SINGLE_LAYER`）。
- 串联靠 handoff 应用服务：`single_layer_rack_orchestration_service.py:131-177` 收到 RELEASE_FACT → `smt_inbound_handoff_service.create_or_get_from_release()` 幂等创建 Demand + 每料格一个 SourceItem（`service.py:115-198`）。
- `handoff_source_item_id` 来自粗分机产出的 release fact，SMT 创建 Session 时写入 `SortingInboundContext.source_pick_request`（`service.py:1557-1610`、`context.py:129-161`）。

### 第二段：SMT 分拣机（二次扫码 + 精分入五层箱）

**入口**：`claim_next_source_item()` 两阶段认领（路由 + ECS 探针，`service.py:413-444`）→ 创建 Session → 下发内部事件 `SORTING_SOURCE_PICK_REQUESTED` → `plugin.py:143` 捕获。

| 转移 | 触发 | 代码 |
| --- | --- | --- |
| 创建 → `SOURCE_PICKING` | 隐式初始 | `service.py:733-768`、`context.py:607` |
| `SOURCE_PICKING` → `AWAITING_SCAN` | COMMAND_SOURCE_PICK 成功，源格出账 `MATERIAL_UNMOUNTED`，扫码平台 OCCUPIED | `flow_service.py:117-163` |
| `AWAITING_SCAN` 内 | WORKING_BIN_SCAN：校验扫码身份与源格出账身份一致 + 厚度有效 | `flow_service.py:181-263` |
| → `TARGET_PLACING` | `allocation_policy.allocate()` 返回 ALLOCATED | `flow_service.py:241-263` |
| → `AWAITING_TARGET_RESOURCE` | 分配返回 NO_CAPACITY（目标箱满，等换箱） | `_allocation_rejection_intents:493-534` → `:512` |
| `TARGET_PLACING` → **料盘状态机终结** | COMMAND_TARGET_PLACE 成功，`MATERIAL_MOUNTED` 入账五层目标格 | `flow_service.py:265-298` |
| → `NG_HANDLING` | 扫码身份与源格不一致 | `flow_service.py:199-222` |

### 入库上架完成 = 料盘状态机终结点

- 料盘在 `TARGET_PLACING` 成功 + `MATERIAL_MOUNTED` 入五层目标格那一刻即**入库上架完成**，料盘状态机终结，料盘生命周期到此为止。
- `Session.status=COMPLETED` 只是 Session 收尾确认，**不是**入库完成的判定点。单盘闭环即入库完成。

### complete 时机：粗分机与 SMT 设计一致，SMT 现状有遗留待修

两条线设计上都是**一盘一 Session、入库成功后自动 complete**，粒度一致。**线性推进约束（设备容量驱动，非 WorkLine 串行锁）**：WorkLine 本身不按整体串行控制，料盘按设备节点线性推进——SMT 扫码平台容量为 1，上一盘占用扫码平台（`current_material` 未关闭）期间，`claim_next_source_item` 经 `_target_has_open_current_material`（`TARGET_SESSION_BUSY`，`smt_inbound_handoff_service.py:712`）不认领下一盘；上一盘离开扫码平台（进 `TARGET_PLACING` 或 NG）即可 claim 下一盘。这是设备节点容量约束，不是 WorkLine 级串行调度。粗分机同理按设备节点推进。

| 工作线 | Session 粒度 | 入库成功后（设计/现状） | 代码 |
| --- | --- | --- | --- |
| 粗分机 | 一盘一 Session | PUT_TO_BIN 成功 → 插件 `RuntimeIntent.complete()` → **自动 COMPLETED** ✅ | `plugin.py:1085`（NG 搬运成功同 `plugin.py:982`） |
| SMT 分拣机 | 一盘一 Session（`session_code` 带 `source-item:{item.id}`，claim 每次 `limit=1` 建新 Session，`smt_inbound_handoff_service.py:1567/413`） | TARGET_PLACE 成功后**应自动 complete**，但现状只 `update_context` 把 `business_phase` 重置回 `WAITING_SOURCE_PICK`、扫码平台清空（`flow_service.py:627`），**不 complete**；只有人工发 `SORTING_SESSION_COMPLETE_REQUESTED` 才 COMPLETED（`flow_service.py:434`） ⚠️ | `flow_service.py:265`（success，不 complete）、`flow_service.py:418-434`（人工 complete） |

**SMT 现状遗留**：`handle_target_place_success` 成功后重置回 `WAITING_SOURCE_PICK` 是旧"一箱一 Session"设计的残留死代码。改为一盘一 Session 后，该 Session 重置回初始态再也不会被驱动，会僵在 `WAITING_SOURCE_PICK` 直到人工对它发 complete——既无业务意义，也与粗分机行为不一致，**且违反人工介入边界**（正常入库不应有人工操作，见「人工介入边界」）。

**修复方向（实施时落地，非本设计粘贴代码）**：`handle_target_place_success` 成功后改为 `RuntimeIntent.complete()`（与粗分机 `plugin.py:1085` 对齐），删除 `_target_success_context_patch` 里把 `business_phase` 重置回 `WAITING_SOURCE_PICK` 的逻辑。`SORTING_SESSION_COMPLETE_REQUESTED` 人工 complete 事件**移除**——按人工介入边界，正常入库收尾必须自动，不应保留人工 complete 路径；异常残留 Session 的收尾应通过自动 NG/RECONCILING 升级处理，而非人工 complete。

> 入库判定点两条线一致：单盘 `MATERIAL_MOUNTED` 入目标格即入库上架完成。`Session.status=COMPLETED` 只是 Session 收尾确认，非入库判定点。

此后满箱回库、CTU 搬运、料箱在队列间移动，全部是 `BIN_TRANSIT` 视角，与料盘状态无关（见「满箱回库与料箱转线」小节）。

### 料盘身份两次校验

料盘身份经过两次独立校验，中间靠 handoff 的 source item 锚定：

1. **粗分机**：六合一码 + WMS 库存匹配 HHPN/LotCode（`plugin.py:633-696`）。
2. **SMT 二次扫码**：扫码身份 vs 源格出账身份一致（`flow_service.py:199-222`）。

## 数据模型现状诊断与后续治理

走通端到端流程并核验 resource 域后，确认数据模型存在真实散乱。本节诊断根因、给出治理方向。**料盘根域已纳入第一阶段（CEO 评审重塑），P4/P5 等其余治理项为后续。**

### 现状：料盘身份散落 + resource 域 18 张表

料盘身份记录时机割裂：

| 时机 | 载体 | 内容 | 代码 |
| --- | --- | --- | --- |
| 扫码时（粗分机/SMT 入口） | `WorklineSession.context_json.six_in_one` / `business_key` | 六合一码（PkgID/HHPN/LotCode 等）、业务追溯键 | `rough_sorter/context.py:11-12`、`smt_sorting_inbound/context.py` |
| 入箱上架时（`MATERIAL_MOUNTED`） | `resource_bin_material_mounts` 表 | `pkg_code`/`material_identity_key`/`bin_code`/`bin_cell_index`/`cell_stack_position`，`ended_at IS NULL` 表示活记录 | `resource/models/resource.py:660`、`projection_service.py:582` |

扫码到入箱之间（在途、NG、测量中），料盘身份只在 Session 的 JSON blob 里，没有独立行记录。

resource 域围绕料盘/料箱/货架有 18 张表，分六层（主数据/事件账本/当前投影/快照/调度预占/货架操作），层数清晰但存在跨层冗余与关联薄弱。

### 诊断：五个真问题 + 三个可接受权衡

**真问题（值得治）：**

- **P1 料盘定位跨 5 表**：查一个料盘当前位置要 `BinMaterialMount → BinCellOccupancy → BinPlacement/RackBinMount → RackPlacement`，无物化视图，靠 `SmtActiveRackSnapshotService` 运行时拼（`active_rack_snapshot_service.py:207`）。性能与可维护性痛点。
- **P2 `BinCellOccupancy` 与 `BinMaterialMount` 字段冗余**：`material_identity_key`/`material_code`/`lot_code`/`date_code` 两表重复。意图是 Occupancy 做格位聚合、Mount 做料盘明细，但增删改双写同步（`projection_service.py:880-913`），易不一致。散乱根源之一。
- **P3 缺独立料盘实体表**：`pkg_code` 散在 `BinMaterialMount`/`BinContentSnapshotItem`/`WorklineBinCellReservation` 三表，无主表。**这是 P1/P2 的共同根因**——缺料盘本体导致身份散落、定位链长、双写冗余。
- **P4 `session_id` 类型不一致**：resource 投影表 `session_id` 为 `str | None`（`resource/models.py:371/429/474/522/586/632`），`WorklineBinCellReservation.session_id`/`RackTask.material_session_id` 为 `int`。不能直接 SQL join，历史类型债。
- **P5 resource 投影表全无 SQL 外键**：关联靠业务字段隐含约定 + 应用层保证（如 `bin_cell_occupancy_id` 是 int 约定非 FK）。脏数据风险高。

**可接受的设计权衡（不误伤）：**

- **A1** `BinContentSnapshot` 与 active 投影字段相似但语义不同：快照是时间点证据（append-only，对账/handoff 用），active 投影是当前状态（`ended_at` 判活）。字段集相似正常，表不该合并，可共用字段 mixin 减少重复定义。
- **A2** 18 表分层清晰（主数据→事件账本→当前投影→快照→预占→货架操作），每层职责单一，拆分本身没错。
- **A3** `ended_at IS NULL` 判活是 bi-temporal 投影标准做法，非散乱。

### 根因与目标架构方向：料盘为根域

P1/P2/P3 同源：**缺料盘实体表（P3）→ 身份散落 → 定位链长（P1）→ 双写冗余（P2）**。

**现状是料箱格中心模型**：resource 域以料箱格为锚点，料盘是挂载在格位上的从属记录（`BinMaterialMount`）。料盘没有自己的实体表，位置靠"查哪个格位的 mount 记录 `ended_at IS NULL`"反推。后果就是 P1——查料盘位置要跨 5 表。

**目标架构方向：料盘中心模型（料盘为根域）**。从粗分机 `SCAN_COMPLETED` 起即建立料盘实体记录，后续插件操作都是对该料盘数据状态及料格/料箱/货架位置的变化，根域是料盘。这样"这盘料现在在哪/经历什么"一次查询可得，直接解决 P1/P3 根因。

```
料盘中心模型(目标):
  SCAN_COMPLETED → 建料盘实体(pkg_code 全局ID, status, current_location, current_session_id)
  后续每次操作 → 更新料盘实体的 status + current_location
  粗分机 Session / SMT Session → 都关联到同一料盘实体(pkg_code), 跨 Session 直接串联

料箱格中心模型(现状, 保留为料箱格视角):
  BinMaterialMount/BinCellOccupancy → 料箱格容量/冲突/对账, 通过 pkg_code 关联料盘实体
```

### 两个关键约束

- **约束 1：料盘全局 ID 是 `pkg_code`，不是 `material_identity_key`**。核验：`material_identity_key` 是物料属性键（`MAT:material_code:vendor:date:lot`，`material_identity.py:8`），**同批次多盘料共享一个**——它是"物料属性"不是"料盘身份"。`pkg_code`（PkgID）才是单盘物理唯一键。根域主键必须选 `pkg_code`，否则同批次料盘会被误当成一盘。
- **约束 2：料盘实体表与 resource 投影是叠加，不是替换**。`BinMaterialMount`/`BinCellOccupancy` 不只给料盘定位——还承载料箱格容量管理（一格装几盘、深度用了多少）、冲突检测（同格重复入账）、对账快照，这些是料箱格视角需求，料盘实体替代不了。新增料盘实体表（料盘视角），保留 resource 投影（料箱格视角），两者通过 `pkg_code` 关联。
- **约束 3：位置双写必须同事务，投影仍是格位权威**。`material_units.current_location` 是面向诊断和跨 Session 串联的派生缓存，不是替代 `resource_bin_material_mounts`/`BinCellOccupancy` 的最终事实源。实施时 `MATERIAL_MOUNTED`/`MATERIAL_UNMOUNTED` 对 resource 投影和 `material_units.current_location/status` 的写入必须在同一事务内完成；若线上排查发现两者不一致，以 resource 投影为准，并进入 `RECONCILING` 或人工对账路径。

### 治理优先级

1. **料盘实体表 + 料盘根域**（**已纳入第一阶段**，CEO 评审重塑）：新增 `material_units`（自主主键，`pkg_code` 业务键，含 `material_identity_key`/`six_in_one`/`status`/`current_location`/`current_session_id`），扫码建记录、入箱/出账/NG/对账更新状态与位置；粗分机与 SMT Session 通过 `pkg_code` 直接串联，替代 `handoff_source_item_id` 间接关联。
2. **P1/P2 定位链与双写**：料盘根域落地后，料盘位置查询收敛到"实体表 current_location"一次查询；Occupancy/Mount 冗余字段收敛到从料盘实体派生，缓解双写（双写一致性策略见 backlog 子issue3）。
3. **P4 类型债**（后续）：resource 投影表 `session_id` 统一为 `int`（与 `WorklineBinCellReservation`/`RackTask` 对齐），或全部显式 FK。
4. **P5 外键债**（后续）：投影表间补 SQL 外键约束（或明确文档化为应用层保证、加校验）。
5. **料盘实体与 Session 状态关系**（后续）：与 C 阶段 transition 强校验一起评估——料盘实体是"物"、Session 是"处理过程"，两者状态映射但不混用。

### 边界：料盘根域已纳入第一阶段（CEO 评审重塑）

> **重塑**：经 CEO 双声评审，料盘根域从"后续独立 epic"提升为**第一阶段主体**。原"plugin_state 列治标 + 根域治本分两阶段"被判为"6 个月后悔"风险（B 发 C 未发、三种状态表示并存）。重塑后第一阶段直接建 material_units，plugin_state 列不加。

第一阶段聚焦：material_units 料盘根实体 + 状态写面迁移 + 关键读路径直连 + SMT 自动 complete 修复 + NG 自动记账 + manifest 合同 + 软告警。**不做的**：plugin_state 列、reconciliation_from_state(Session) 列、RuntimeHold 审计、RECONCILING 自动恢复设计、context_json 散读面一次性迁移。

**人工边界收窄（决策3）**：NG 是业务问题（料盘不合格，自动进 NG 域，单向不回正常流）；RECONCILING 是功能问题（系统状态不可信，对账后可回正常态），但前期不预设触发分类与自动/人工边界，保持现状，上线后按需优化。

> 实施者需知晓：第一阶段建立料盘根域后，跨 Session 追踪靠 `material_units.pkg_code` + `current_session_id` 直连（替代 handoff_source_item_id 间接关联）；料盘定位一次查询 `material_units.current_location`（缓解跨 5 表）；context_json 散读面保留兼容待 C 阶段清理。

> **重塑说明（CEO 评审后）**：以下「命名风格统一」「粗分机/SMT Session 状态」「状态转移图」「映射表」章节使用重塑前的 9 态处理过程枚举（INTAKE_HANDLING/AWAITING_STORAGE_RESOURCE/...）。CEO 评审重塑后，第一阶段改为料盘根域 5 态物视角（IN_TRANSIT/STORED/COMPLETED/NG/RECONCILING，见「第一阶段决策」）。以下章节**保留作现状代码参照**（plugin.py/flow_service.py 的 phase/business_phase 流转仍按此走，实施时需参照映射到料盘5态），不作为第一阶段状态机合同的权威定义。权威定义见「第一阶段决策」的料盘状态机表。

## 旧 9 态过程态命名参照（非第一阶段权威状态）

> 本节及后续「粗分机 Session 状态」「SMT 分拣机料盘 Session 状态」「状态转移图」描述的是现有 `context_json.phase/business_phase` 的处理过程视角，用于实施者理解旧代码如何映射到料盘根域。第一阶段权威状态仍是 `material_units.status` 5 态；不要按本节新增 `workline_sessions.plugin_state`。

状态命名按语义选前缀：进行中的业务处境用 `_ING` 动名词（`INTAKE_HANDLING`、`STORING_TO_BIN`、`SOURCE_PICKING`、`TARGET_PLACING`），异步等待资源/结果用 `AWAITING_` 前缀（`AWAITING_STORAGE_RESOURCE`、`AWAITING_SCAN`、`AWAITING_TARGET_RESOURCE`），对账统一用 `RECONCILING`。同一状态机内允许 `_ING` 与 `AWAITING_` 共存（按该状态是"正在做"还是"在等"选择），资源/队列状态用大写名词。

原稿粗分机用 `WAITING_` 前缀（`WAITING_STORAGE_RESOURCE`），SMT 用 `WAITING_` 前缀（`WAITING_SCAN` 等），本次统一为 `AWAITING_` 前缀，与"正在做"的 `_ING` 区分开。

## 粗分机旧过程态映射（现状代码参照）

粗分机旧提议过程态比当前 `phase` 更粗，但第一阶段不落库为 `plugin_state`；实施时把这些过程态写面映射到 `material_units.status`。

建议状态：

| 状态 | 含义 |
| --- | --- |
| `INTAKE_HANDLING` | 条码已进入处理链路，正在入线、测量、WMS 准入或移动到出料决策点。 |
| `AWAITING_STORAGE_RESOURCE` | 当前料盘等待目标存储资源，例如料箱格、货架位 active projection、WMS/RCS 操作。 |
| `STORING_TO_BIN` | 目标格已确定，正在执行入箱动作。 |
| `NG_HANDLING` | 已判定业务 NG，正在执行 NG 搬运或等待 NG 闭环。 |
| `RECONCILING` | 物理位置、资源投影或设备结果不可信，需要对账。 |

旧过程态参照中的 `COMPLETED` 不作为活动态建模。粗分 PUT_TO_BIN 成功后 Session 自动 `COMPLETED`，料盘根域状态应为 `STORED`，等待 SMT 后续取出。

`AWAITING_STORAGE_RESOURCE` 替代更窄的 `WAITING_RACK`。它描述的是"料盘等待存储资源"，不是货架本体状态。

### 粗分机状态映射表（现状 → 提议）

| 现状 `phase`（`contract.py:20-26`） | 旧过程态参照 | 说明 |
| --- | --- | --- |
| `SCANNED` | `INTAKE_HANDLING` | 扫码完成是 INTAKE 子阶段，不单列。 |
| `PICK_TO_PIPELINE` | `INTAKE_HANDLING` | 取盘入线属 INTAKE。 |
| `MOVING_FORWARD` | `INTAKE_HANDLING` | 流水线前进到出料决策点。 |
| `WAITING_RACK` | `AWAITING_STORAGE_RESOURCE` | 改名、含义拓宽。 |
| `PUTTING_TO_BIN` | `STORING_TO_BIN` | 改名。 |
| `NG_MOVING` | `NG_HANDLING` | 改名。 |
| `COMPLETED` | （旧过程态不建活动态） | 粗分 Session 由 `Session.status=COMPLETED` 表达；料盘根域映射为 `STORED`。 |

## SMT 分拣机旧过程态映射（现状代码参照）

建议状态：

| 状态 | 含义 |
| --- | --- |
| `SOURCE_PICKING` | 已锁定一个 material unit，等待源端从源料箱/源格取盘。 |
| `AWAITING_SCAN` | 源格已出账，料盘在扫码平台，等待扫码识别。 |
| `AWAITING_TARGET_RESOURCE` | 料盘身份可信，但目标箱、目标格或 Station 不可用，等待资源满足。含"目标箱满→等换箱/换架"全周期：换箱中（满箱被搬走、新箱未到位）也停留本态，不另设"换箱中"子态；换箱完成并重新 allocate 成功才进 `TARGET_PLACING`。 |
| `TARGET_PLACING` | 目标落点已确定，等待目标端放盘闭环。 |
| `NG_HANDLING` | 身份不一致或本地 NG，等待 NG 放置闭环。 |
| `RECONCILING` | 料盘位置、目标投影或 NG 证据不可信。 |

旧过程态参照中的 `COMPLETED` 不作为活动态建模。SMT TARGET_PLACE 成功后 Session 自动 `COMPLETED`，料盘根域状态应为 `COMPLETED`。

`WAITING_TARGET_BIN_SWITCH`（现状 `constants.py:16`）收敛为 `AWAITING_TARGET_RESOURCE`。具体是换目标箱、换面、调新五层货架，归资源/Handling 视角表达，不进入 Session 状态名。`AWAITING_TARGET_RESOURCE` 的入口（见 SMT transition 合同）：`AWAITING_SCAN` 经 allocate `NO_CAPACITY` 进入，换箱中自环，换箱完成重新 allocate `ALLOCATED` 后进 `TARGET_PLACING`。

### SMT 状态映射表（现状 → 提议）

| 现状 `business_phase`（`constants.py:13-18`） | 旧过程态参照 | 说明 |
| --- | --- | --- |
| `WAITING_SOURCE_PICK` | `SOURCE_PICKING` | 改名。 |
| `WAITING_SCAN` | `AWAITING_SCAN` | 改名。 |
| `WAITING_TARGET_BIN_SWITCH` | `AWAITING_TARGET_RESOURCE` | 收敛，换箱/换面/换架统一为资源等待。 |
| `WAITING_TARGET_PLACE` | `TARGET_PLACING` | 改名。 |
| `WAITING_NG_PLACE` | `NG_HANDLING` | 改名。 |
| `COMPLETED` | （旧过程态不建活动态） | SMT Session 由 `Session.status=COMPLETED` 表达；料盘根域映射为 `COMPLETED`。 |

## 粗分机 Session 状态转移图

```text
                  ┌──────────────────────────┐
                  │                          │
                  ▼                          │
            INTAKE_HANDLING ────NG判定────► NG_HANDLING
                │   │  │                     │
                │   │  └──资源/位置不可信──► RECONCILING
       分配ALLOCATED(直连)                    
                │   └──RACK_OPERATION_REQUIRED─┐
                ▼                              ▼
          STORING_TO_BIN  ◄──────── AWAITING_STORAGE_RESOURCE ◄──资源再次不满足
                │                          WMS/RCS货架到位重试
        入单层格成功│                          
                ▼                          
          粗分 Session 闭环 / material_units.status=STORED
          Session.status=COMPLETED 只是粗分会话收尾确认, 非最终入库完成判定点
          任何状态可在不可信时切到 RECONCILING（对账完成后回到对应业务状态）
```

合法 transition（第一阶段合同声明，Runtime 不强制）：

```text
INTAKE_HANDLING          -> AWAITING_STORAGE_RESOURCE | STORING_TO_BIN | NG_HANDLING | RECONCILING
                          # NG_HANDLING 出口触发源: 扫码NG / 测量NG / WMS库存拒绝(均plugin.py:740/890/929)
AWAITING_STORAGE_RESOURCE-> STORING_TO_BIN | NG_HANDLING | RECONCILING
STORING_TO_BIN           -> 粗分Session闭环(material_units.status=STORED) | NG_HANDLING | RECONCILING
                          # MATERIAL_MOUNTED 入单层格后粗分会话停止; 料盘根域进入 STORED, 待 SMT 后续取出
NG_HANDLING              -> (Session.status=COMPLETED/FAILED) | RECONCILING
                          # NG搬运成功 -> COMPLETED; NG搬运命令本身失败 -> FAILED
RECONCILING              -> AWAITING_STORAGE_RESOURCE | STORING_TO_BIN | NG_HANDLING
                          # 受约束恢复集：只能回到对账前状态或明确恢复目标，不能跳任意状态
                          # 进入RECONCILING时记录 from_state，对账后回到 from_state 或其合法后继
```

## SMT 料盘 Session 状态转移图

```text
        SOURCE_PICKING
              │ 源格出账
              ▼
        AWAITING_SCAN ────身份不一致──► NG_HANDLING
              │ │                         
   目标资源就绪│ └──位置/证据不可信──► RECONCILING
              ▼                          
      AWAITING_TARGET_RESOURCE ◄──目标资源再次不满足
              │ 目标落点确定              
              ▼                          
        TARGET_PLACING ──放盘成功+MATERIAL_MOUNTED 入五层目标格──► 料盘状态机终结
              │                          （入库上架完成；详见「入库上架完成 = 料盘状态机终结点」）
              └──任何状态可在不可信时切到 RECONCILING（受约束恢复集，见下）
```

合法 transition：

```text
SOURCE_PICKING          -> AWAITING_SCAN | NG_HANDLING | RECONCILING
AWAITING_SCAN           -> TARGET_PLACING | AWAITING_TARGET_RESOURCE | NG_HANDLING | RECONCILING
                          # allocate() 三种结果：
                          #   ALLOCATED            -> TARGET_PLACING
                          #   NO_CAPACITY(目标箱满) -> AWAITING_TARGET_RESOURCE(等换箱/换架)
                          #   PROJECTION_INCONSISTENT -> RECONCILING (flow_service.py:524, SORTING_TARGET_CELL_RECONCILING)
                          #   其它 REJECTED          -> BLOCKED(自动重试/自动升级), 不直接进 NG; 见下 BLOCKED 说明
AWAITING_TARGET_RESOURCE-> TARGET_PLACING | NG_HANDLING | RECONCILING
                          # 换箱/换架完成 + 重新 allocate ALLOCATED -> TARGET_PLACING
                          # 换箱中(满箱被搬走、新箱未到位)仍停留本态, 不另设"换箱中"子态
TARGET_PLACING          -> 料盘状态机终结(入库上架完成) | NG_HANDLING | RECONCILING
                          # 终结: MATERIAL_MOUNTED 入五层目标格后, 当前这盘料状态机停止
                          # SMT 一盘一 Session, 入库成功后 Session 应自动 COMPLETED (见下「complete 时机」)
NG_HANDLING             -> (Session.status=COMPLETED/FAILED) | RECONCILING
                          # NG放盘成功 -> COMPLETED; NG放盘命令本身失败 -> FAILED
RECONCILING             -> AWAITING_SCAN | AWAITING_TARGET_RESOURCE | TARGET_PLACING | NG_HANDLING
                          # 受约束恢复集：只能回到对账前状态或明确恢复目标，不能跳任意状态
                          # 进入RECONCILING时记录 from_state，对账后回到 from_state 或其合法后继
```

> **BLOCKED 说明**：`allocation_policy` 返回非 `NO_CAPACITY`/`PROJECTION_INCONSISTENT` 的 `REJECTED`（如临时策略不满足）时，现状走 `_block("SORTING_TARGET_ALLOCATION_REJECTED")`（`flow_service.py:531`），进入 RuntimeHold，**不**直接进 `NG_HANDLING` 也不进 `RECONCILING`。第一阶段 `material_units.status` 保持进入 BLOCKED 前的料盘状态（通常仍是 `IN_TRANSIT`），由 `RuntimeHold.reason_code` 表达阻断原因，`current_activity` 推导"等待重试分配"；不新增 `BLOCKED` 到 `material_units.status` 枚举——BLOCKED 是命令/等待层概念，不是料盘业务阶段。**恢复路径按人工介入边界**：BLOCKED 应自动重试或自动升级 NG/RECONCILING，常规不依赖人工解阻；仅当升级为 NG/RECONCILING 后才进入对应处理。

## 货架位状态，而不是货架状态

WMS 拥有货架主数据和完整生命周期。WES 只维护工作状态下的 active projection。

因此不维护"货架状态机"，而维护"货架位状态机"：

```text
WMS/RCS: 货架资产生命周期
WES: WorkLine 某个货架位是否绑定可信 projection、是否可作业
```

建议状态：

| 状态 | 含义 |
| --- | --- |
| `UNBOUND` | 该 WorkLine 货架位没有可信 active projection。 |
| `AWAITING_BINDING` | 已请求或等待 WMS/RCS 将可用货架投影到该位置。 |
| `AVAILABLE` | 有可信 active projection，且可被当前 WorkLine 使用。 |
| `UNAVAILABLE` | 有投影但不可用，例如满、空、冻结、lease 不满足、策略不满足。 |
| `RECONCILING` | WES 投影、现场或 WMS evidence 不一致。 |

适用对象：

- 粗分机 `SINGLE_LAYER_A` 分类工作位。
- SMT `SOURCE_STATION_A/B` 源货架位。
- SMT `TARGET_STATION` 目标货架位。

`active_rack_code`、`rack_kind`、`snapshot_version` 和 bin projection 是货架位状态的证据和 payload，不是状态主体。

> **代码现状**：resource domain 当前是命令式事实记录（`src/app/resource/services/projection_service.py:1233` `record_resource_fact()`，事实类型含 `RACK_ARRIVED`/`BIN_ARRIVED`），以及 `BinContentSnapshot`（`resource/models/resource.py:697`）。本设计提议的是声明式状态机合同 + 可观测状态，抽象层在现有事实记录之上叠加。`WorklineRackPosition`（`workline/models/rack_position.py:55`）是静态合同模型。

## 料箱拆成两个视角

### BIN_PROJECTION

表示 active projection 中某个料箱能否被当前作业使用。

建议状态：

| 状态 | 含义 |
| --- | --- |
| `AVAILABLE` | 当前可作为源/目标料箱使用。 |
| `RESERVED` | 已被当前操作占用或预期占用。 |
| `UNAVAILABLE` | 满、空、冻结、厚度不足、lease 不可用或策略不满足。 |
| `RECONCILING` | 投影、现场或 WMS evidence 不一致。 |

这个视角不表达料箱在流水线哪一段。

### BIN_TRANSIT

表示料箱进入 SMT 分拣机流水线后，在 WES 管辖范围内的通行和门控。

这不是 WMS 料箱主数据生命周期，也不是简单资源可用性。它应归 Handling / Pipeline queue 视角。

## 流水线建模为多个队列

> **物理现状 vs 代码差距**：SMT 分拣机物理流水线上存在多个扫码门控点（入料扫码、工作位二次扫码、出口路由扫码、回收扫码等），多料箱同时在不同区域通行。**当前代码抽象不足**：只建模了单扫码平台 `SORTING_SCAN_PLATFORM` 和单扫码事件 `WORKING_BIN_SCAN`（`smt_sorting_inbound/constants.py:25`、`flow_service.py:181`），扫码点 3/4、转线扩展点、多料箱并发队列在代码中尚未实现。本节的队列建模是对物理现状的正确抽象，第一阶段 manifest 声明合同，运行时按现有单扫码平台能力先支持部分队列，多队列 membership 完整实现随代码抽象补齐落地。

SMT 分拣机流水线上会同时存在多个料箱，处于不同区域。相比维护一串很长的料箱状态，更合适的模型是：

```text
PipelineQueue + BinTransitMembership
```

料箱运行态主要由以下字段表达：

| 字段 | 含义 |
| --- | --- |
| `current_queue` | 当前所在流水线队列或工位。 |
| `queue_position` | 同队列内顺序。 |
| `transit_status` | 粗生命周期，例如 queued、active、blocked、done。 |
| `last_gate` | 最近一次扫码或门控事件。 |
| `gate_result` | 最近门控结果。 |
| `handling_operation_id` | 关联 CTU 投料、回收或其它 handling operation。 |

建议队列（对应物理扫码门控点）：

| 队列 | 角色 | 容量 | 说明 |
| --- | --- | --- | --- |
| `INFEED_BUFFER_QUEUE` | Buffer | 多个 | CTU 投料后进入入料缓存区。 |
| `ENTRY_SCAN_QUEUE` | Gate | 1 | 扫码点 1，判断 NG 或批准进入工作位。 |
| `WORKSTATION_WAIT_QUEUE` | Wait | 多个 | 已批准，等待进入工作位。 |
| `WORKSTATION_ACTIVE` | Workstation | 1 | 工作位二次扫码通过后，作为当前可作业目标箱。 |
| `EXIT_ROUTING_SCAN_QUEUE` | Gate | 1 | 扫码点 3，判断是否还有其它线任务；当前只支持放行。 |
| `RETURN_SCAN_QUEUE` | Gate | 1 | 扫码点 4，进入待回收队列前确认。 |
| `RETURN_WAIT_QUEUE` | Wait | 多个 | 等待 CTU 回收。 |
| `NG_REJECT_QUEUE` | Exception | 多个 | 扫码点 1 或其它门控拒绝后的异常队列。 |

`BIN_TRANSIT` 的粗状态可以收敛为：

| 状态 | 含义 |
| --- | --- |
| `QUEUED` | 位于某个队列中等待处理。 |
| `GATE_CHECKING` | 正在门控扫码或判定。 |
| `ACTIVE` | 正在工作位参与作业。 |
| `BLOCKED` | 因 NG、冻结、设备或业务原因阻断。 |
| `DONE` | 当前 WorkLine 通行流程已完成（料箱回库/离线）。 |
| `RECONCILING` | 料箱身份、位置、任务归属或门控 evidence 不可信。 |

### 流水线队列流转图

```text
  CTU投料
     │
     ▼
INFEED_BUFFER_QUEUE ──►ENTRY_SCAN_QUEUE(Gate1)──PASS──►WORKSTATION_WAIT_QUEUE
                              │                          │
                            NG│                          ▼
                              ▼                   WORKSTATION_ACTIVE(二次扫码)
                        NG_REJECT_QUEUE                  │
                              │                    作业完成│
                              ▼                          ▼
                          (异常处理)          EXIT_ROUTING_SCAN_QUEUE(Gate3,放行)
                                                         │
                                                         ▼
                                              RETURN_SCAN_QUEUE(Gate4)
                                                         │
                                                         ▼
                                              RETURN_WAIT_QUEUE ──►CTU回收
```

### 满箱回库与料箱转线（BIN_TRANSIT 视角）

目标五层料箱满 → 分配返回 NO_CAPACITY → 料盘 Session 进入 `AWAITING_TARGET_RESOURCE`（等待换目标箱）。满箱被搬走、CTU 回库属于 **`BIN_TRANSIT` / Handling 视角**的料箱移动，不改变已上架料盘的状态：

- 满箱回库通过 handling operation（carrier=CTU，如 `SINGLE_LAYER_FULL_BOX_EXCHANGE`）把满箱从工作位移到 buffer/回库，对应 `BIN_TRANSIT` 的 `current_queue` 从 `WORKSTATION_ACTIVE` → `RETURN_WAIT_QUEUE` → `DONE`。
- 这是料箱被搬运的记录，**不是料箱转线能力**。
- 当前**不支持料箱转线**（一条 SMT 线的料箱转到另一条 SMT 线）。转线即使支持，也只改变料箱 `BIN_TRANSIT` 的 `current_queue`，**不改变料盘状态**——料盘一旦在 `TARGET_PLACING` 入五层格上架完成，其状态机已终结，料箱后续移动与料盘状态解耦。

> 料盘状态机与料箱流水线队列是两个独立对象。料盘只关心"目标资源是否满足作业条件"（目标料箱在工作位 `ACTIVE` + `BIN_PROJECTION=AVAILABLE`），不关心料箱在流水线经过几个扫码点、是否被搬走回库。

示例：

```yaml
bin_transit:
  bin_code: B001
  current_queue: RETURN_WAIT_QUEUE
  queue_position: 5
  transit_status: QUEUED
  last_gate: BIN_SCAN_4_COMPLETED
  gate_result: PASS
  handling_operation_id: 123
```

### BinTransitMembership 与 HandlingMove 的关系（推荐方向）

> **代码现状**：`HandlingMove`（`src/app/handling/models/operation.py:139`）字段为 `operation_id/operation_key/sequence_no/object_type/move_status/rack_code/rack_slot_code/bin_code/source_type/target_type/carrier_type` 等，是"一次 source→target 搬运记录"。它**不具备** `current_queue/queue_position/transit_status/last_gate/gate_result` 等流水线队列字段。

**推荐**：BinTransitMembership 新建专门的投影视图，不复用 `HandlingMove`。原因：

- `HandlingMove` 语义是单次搬运记录（source→target），与流水线队列成员（料箱在某队列的通行态）语义不同。
- 队列成员有 `current_queue/queue_position` 等队列特有字段，硬塞 HandlingMove 会污染两边模型。
- `handling_operation_id` 仅作为关联引用字段存在于 BinTransitMembership，不反向来承载 HandlingMove。

## Session 与流水线队列的关系

料盘 Session 不关心目标料箱经过了几个扫码点。它只关心目标资源是否已经满足作业条件。

例如：

```text
material_units.status = IN_TRANSIT
Session.current_activity = AWAITING_TARGET_RESOURCE
等待条件：
  target_bin.current_queue = WORKSTATION_ACTIVE
  target_bin.transit_status = ACTIVE
  workstation second scan passed
  target_bin_projection = AVAILABLE
```

满足后，料盘 Session 才能进入：

```text
TARGET_PLACING
```

这样 Session 状态不被流水线细节污染，但诊断仍然能解释为什么目标资源未就绪。

## Manifest 第一阶段合同

> **代码现状**：manifest 顶层字段由 `src/workline_runtime/plugin_manifest.py:686` 的 `_expect_yaml_keys` 严格白名单限定为 `{plugin_key, contract_version, device_roles, rack_positions, topology, resource_boundaries}`。`WorklinePluginManifest` dataclass（`plugin_manifest.py:651`）字段对应。`state_machines`/`pipeline_queues`/`session_subject` 当前**不存在**，`ACTIVE_PROJECTION`、`subject`、`state_owner`、`granularity` 等结构代码中零命中。

第一阶段在 manifest 中声明状态机和队列合同，但不要求 Runtime 强制执行所有 transition。

**manifest 扩展成本**（实施时必须做，非零成本文档动作）：

1. 扩展 `WorklinePluginManifest` dataclass，新增 `session_subject`、`state_machines`、`pipeline_queues` 三个字段及其子结构 dataclass。
2. 扩展 `_expect_yaml_keys` 白名单与 `from_yaml_dict()` 投影逻辑。
3. bump `contract_version`（粗分机、SMT 两个 manifest 都要改）。
4. 补 manifest 校验：state_machines 的 subject/state_owner 引用合法、pipeline_queues 的 capacity/order_policy 合法。

示意结构（仅展示 SMT 部分对象，非完整 manifest；粗分机 state machine、resource projection 等按同结构补全）：

```yaml
session_subject:
  type: MATERIAL_UNIT
  physical_form: REEL
  identity_sources:
    - PkgID
    - material_identity_key
    - handoff_source_item_id

state_machines:
  - id: smt_material_unit_reel
    subject:
      category: MATERIAL_UNIT
      type: MATERIAL_UNIT
      physical_form: REEL
    state_owner:
      model: MaterialUnit
      field: status
    granularity: MATERIAL_LIFECYCLE
    transitions:        # 第一阶段声明合法转移合同，Runtime 不强制
      - from: IN_TRANSIT
        to: [STORED, COMPLETED, NG, RECONCILING]
      - from: STORED
        to: [IN_TRANSIT, NG, RECONCILING]
      - from: RECONCILING
        to: [IN_TRANSIT, STORED, COMPLETED, NG]

  - id: smt_target_position_projection
    subject:
      category: RESOURCE
      type: RACK_POSITION
      role: TARGET
    scope: ACTIVE_PROJECTION
    state_owner:
      domain: resource

pipeline_queues:
  - code: INFEED_BUFFER_QUEUE
    role: BUFFER
    capacity: MANY
    order_policy: FIFO
  - code: ENTRY_SCAN_QUEUE
    role: GATE
    capacity: 1
  - code: WORKSTATION_ACTIVE
    role: WORKSTATION
    capacity: 1
```

第一阶段 manifest 的用途：

- 为插件作者提供统一对象边界。
- 为前端/诊断提供可解释的状态目录。
- 为后续 Runtime transition 校验提供合同基础。
- 避免插件继续扩散私有 `phase` / `business_phase` 概念。
- **非法 transition 软告警**（第一阶段可观测性落点）：插件发出不在 manifest `state_machines.transitions` 合同内的转移时，Runtime 记录 WARN 日志（含 `object_type`/`object_id`/`from_state`/`to_state`/插件 key），**不阻断**业务。比 C 阶段才上强校验更早暴露合同偏离，为 C 阶段强校验预热。
  - **判定时机**：在写入 `material_units.status` 时，比对该料盘上一帧 `status` 与 manifest `transitions[from].to`。
  - **`from_state` 来源**：`material_units.status` 更新前的持久化值；进入 `RECONCILING` 时同步写入 `material_units.reconciliation_from_state`。
  - **判定豁免**：`RECONCILING` 出口第一阶段按 manifest 静态恢复集校验（目标态属于合法恢复集即可），非法恢复集跳出同样 WARN；C 阶段再用 `reconciliation_from_state` 做更严格恢复集约束。

## 当前活动不等于业务状态

UI 和诊断仍然需要看到设备节点和当前动作，但不应通过 `material_units.status` 表达。

建议引入可推导的 `current_activity`：

| 来源 | 现状 | 说明 |
| --- | --- | --- |
| `awaiting_command_id` | ✅ 已有（`session.py:209`） | 当前等待哪个设备命令结果。 |
| `DeviceCommand.task_type` | ✅ 已有（`command.py:92`，注意是 task_type 非 action） | 当前设备动作类型。 |
| `current_wait_type` | ✅ 已有（`session.py:186`） | 当前等待类型。 |
| `latest_resource_wait` | 🆕 第一阶段新增 | 等待哪个资源、原因和建议动作。 |
| `BinTransitMembership.current_queue` | 🆕 第一阶段新增（见 BinTransitMembership，不复用 HandlingMove） | 料箱当前所在流水线队列。 |
| `RuntimeHold.reason_code` | ✅ 已有（`workline/models/runtime_hold.py:69`） | 当前阻断/对账原因。仅 NG/RECONCILING 相关 hold 可人工解除，其余应自动恢复（见「人工介入边界」）。 |
| `current_activity`（派生视图） | 🆕 第一阶段新增 | 由上述来源推导的当前活动描述。 |

> **🆕 字段归属**：`current_activity` 是纯派生视图，不落库（由上述来源运行时拼装）；`latest_resource_wait` 作为结构化字段归 `context_json`（与现有 `current_wait_type`/`waiting_since` 同层），第一阶段不额外加列。第一阶段唯一新增状态落点是 `material_units.status`，不是 `workline_sessions.plugin_state`。`BinTransitMembership.current_queue` 归 BinTransitMembership 投影视图（新建，非 Session 列）。

示例：

```text
material_units.status = IN_TRANSIT
current_activity = waiting command SORTING_TARGET_PLACE result on SORTING_TARGET_ARM
```

## RECONCILING 触发与退出

`RECONCILING` 在多个对象状态机中出现，统一定义触发与退出条件，避免泛化滥用。料盘根域中，`RECONCILING` 是功能问题状态，表示系统状态不可信；NG 是业务问题状态，表示料盘不合格并自动进 NG 域。第一阶段保持现有对账触发源，不预设新的自动/人工恢复分类。

**触发条件**（满足任一即进入）：

- 物理位置与 WES 投影不一致（例如料盘/料箱实测位置与 active projection 记录不符）。
- 设备结果与业务期望不符且无法自动判定（例如命令 FAILED 但无法定位 NG 原因）。
- WMS evidence 与 WES 本地状态冲突。
- 人工触发对账。

**退出条件**（受约束恢复集，不能跳任意状态）：

- 进入 `RECONCILING` 时记录 `from_state`（对账前状态）。
- 对账结论明确：回到 `from_state` 或其合法后继状态（见「第一阶段决策」的料盘 5 态 transition 合同 `RECONCILING -> IN_TRANSIT | STORED | COMPLETED | NG`），不能跳到与对账前处境无关的状态。
- 对账确认 NG：`material_units.status` 转为 `NG`，后续按 NG 域流程搬运、写 `ng_return_items` 并清空 Session 绑定；若 Session 处理无法闭环，再由 Session lifecycle 标记 `FAILED`。
- 对账无法闭环：维持 `RECONCILING`，升级人工处理。

**关联现有字段**：`WorklineSession` 已有 10+ 个 `reconciliation_*` 字段（`session.py:267-332`）记录对账触发源，第一阶段继续消费这些字段作为对账证据和进度载体，不另起一套对账触发数据结构。**`from_state` 落库**：现有字段无对账前料盘状态，第一阶段在 `material_units.reconciliation_from_state` 记录进入 RECONCILING 前的 `material_units.status`，C 阶段强校验据此校验恢复集合法性。

## C 阶段 TODO

第一阶段采用方案 B（重塑）：料盘根域（material_units）+ manifest 合同 + 状态写面迁移 + 可观测性，Runtime 不强制执行 transition。

下一阶段 C 进入 Runtime 强校验和统一 transition：

- 增加 `RuntimeIntent.transition(...)`。（第一阶段已上非法 transition 软告警 WARN 不阻断，C 阶段在此基础上升级为强校验阻断。）
- Runtime 根据 manifest `state_machines.transitions` 校验 `material_units.status` transition。
- `material_units.status` 由 Runtime/领域服务统一写入，插件不得再通过 context patch 写料盘业务阶段。
- 命令创建时记录 `issued_material_unit_status`（或等价命令发起快照），用于校验 callback 返回时料盘状态是否仍匹配发令时上下文。
- resource domain 产出 resource projection transition event。
- handling domain 产出 queue membership transition event。
- `RESOURCE_WAIT` 必须引用 manifest 中声明的 subject。
- Trace 展示 `object_type`、`object_id`、`from_state`、`to_state`、`reason`。
- 前端按对象视角展示：料盘 Session、货架位 projection、料箱 projection、料箱流水线队列、命令活动。
- 扫码点 3 的转线能力作为 C 阶段之后的扩展，不在第一轮强制实现。
- 废弃 `context_json` 中残留的 `phase` / `business_phase`，C 阶段清理。

## 验收标准

文档/合同层面（可验证）：

- 文档中 WorkLine 生命周期枚举与 `src/app/workline/models/safety.py:18` 一致（`STOPPED/READY/RECONCILING/ESTOPPED`），无 `START/STOP/RUNNING/PAUSED` 残留。
- `MATERIAL_UNIT / REEL / physical_form` 出现处均标注"新引入抽象，代码现状不存在"。
- 文档不再把货架本体生命周期作为 WES 状态机；货架位/料箱/料盘三层对象状态归属不互相串。
- `material_units.status` 只表达 `MATERIAL_UNIT / REEL` 的粗物视角状态；命令 ACK/Result/Timeout、设备 role、topology edge 不进 `material_units.status`。
- 粗分机和 SMT 分拣机用同一套 Session 主体概念解释；两者设计上一致（一盘一 Session、按设备节点线性推进、入库成功后自动 complete）。**线性推进可验证**：WorkLine 不按整体串行控制，但同一 SMT workline 同一时刻不存在两个 `RUNNING` 且带 `current_material`（占用扫码平台）的 Session——由 `_target_has_open_current_material`（`TARGET_SESSION_BUSY`，`smt_inbound_handoff_service.py:712`）保证，是扫码平台容量=1 的设备约束，非 WorkLine 串行锁。文档已标注 SMT 现状 `flow_service.py:627` 重置回 `WAITING_SOURCE_PICK` 不 complete 为遗留死代码、修复方向为 `handle_target_place_success` 改 `RuntimeIntent.complete()`、移除 `SORTING_SESSION_COMPLETE_REQUESTED` 人工 complete 事件。
- SMT 目标料箱流水线过程通过队列和 membership 表达，而不是超长 Bin 状态枚举；料箱可用性（BIN_PROJECTION）与流水线通行（BIN_TRANSIT）分离。
- RECONCILING 的退出受约束恢复集约束，不存在 `RECONCILING -> 任意状态`；进入 RECONCILING 时记录 `from_state`（字段归属为 `material_units.reconciliation_from_state`）。
- SMT allocate（料盘 `IN_TRANSIT` 在途子阶段）出口含 `ALLOCATED`/`NO_CAPACITY`/`PROJECTION_INCONSISTENT`/其它 `REJECTED` 四类；`ALLOCATED`/`NO_CAPACITY`/`REJECTED` 均留在 `IN_TRANSIT`（在途继续），`PROJECTION_INCONSISTENT → RECONCILING`，`REJECTED → BLOCKED(RuntimeHold)` 有明确归属。
- 第一阶段含非法 transition 软告警（WARN 不阻断，判定时机为 `material_units.status` 写入时，`from_state` 取上一帧）；Runtime 强校验留给 C 阶段。
- 落库迁移含 downgrade 策略（drop `material_units` 表与 `workline_sessions.current_material_unit_id` 列；不丢 `context_json` 原始信息）。
- **人工介入边界**：NG 是业务问题并自动进 NG 域；RECONCILING 是功能问题，保持现有对账触发与人工解除入口。正常入库 Session 自动 complete（无人工 complete 事件）；满箱换箱系统自动调度（非人工）；BLOCKED 自动重试/升级，不依赖人工解阻。

实施层面（第一阶段落地后可验证）：

- `uv run alembic` 存在 `add material_units reel root entity` 迁移，新建 `material_units` 表（自主主键 + pkg_code 业务键 + material_identity_key + six_in_one + status + current_location + current_session_id + reconciliation_from_state），并给 `workline_sessions` 增加 `current_material_unit_id` 追溯列。
- `src/workline_runtime/plugin_manifest.py` 的 `_expect_yaml_keys` 白名单包含 `state_machines`、`pipeline_queues`、`session_subject`。
- 粗分机、SMT manifest `contract_version` 已 bump。
- 粗分机 `phase` 常量、SMT `business_phase` 常量按料盘状态机 5 态重命名，13 处写入改为更新 `material_units.status`。
- Runtime 在 `material_units.status` 出现非合同 transition 时输出 WARN 日志（不阻断），日志含 `object_type/object_id/from_state/to_state/pkg_code`。
- SMT `handle_target_place_success` 成功后发 `RuntimeIntent.complete()` 自动收尾，删除 `_target_success_context_patch` 重置 `WAITING_SOURCE_PICK` 逻辑，移除 `SORTING_SESSION_COMPLETE_REQUESTED` 事件入口。
- RECONCILING `from_state` 字段落库（`material_units.reconciliation_from_state` 列，随 material_units 表同批 Alembic 生成）；C 阶段强校验据此校验恢复集合法性。

## 开放点（已给推荐方向）

以下问题不阻塞第一阶段文档，但实施计划前需确认。本次审计给出推荐：

- **`pipeline_queues` 顶层 vs 子结构**：推荐作为 manifest **顶层字段**。队列是流水线一等公民，与 state_machines 平行；塞进 state_machines subject 子结构会让队列概念被状态机掩盖。
- **`BinTransitMembership` 复用 `HandlingMove` vs 新建投影视图**：推荐**新建专门投影视图**。HandlingMove 是单次搬运记录，语义不同，复用会污染两边（见「BinTransitMembership 与 HandlingMove 的关系」）。
- **粗分机是否需要 `pipeline_queues`**：推荐**第一阶段不定义**，粗分机流水线较简单（单流向 3 段），用 Session 状态 + topology + current_activity 足够；待粗分机出现多料箱并发再补。
- ~~`plugin_state` 字段是否落库~~：**已决策重塑**——不加 plugin_state 列，第一阶段建 material_units 料盘根域（status 取代 plugin_state，见「第一阶段决策」）。
- **SMT `handle_target_place_success` 是否自动 complete**：**已决策为自动**（方案 b）。SMT 与粗分机一致——一盘一 Session、入库成功后 `RuntimeIntent.complete()` 自动收尾。现状 `flow_service.py:627` 重置回 `WAITING_SOURCE_PICK` + 仅人工 `SORTING_SESSION_COMPLETE_REQUESTED` complete 是旧"一箱一 Session"遗留，违反人工介入边界（正常入库不应有人工）。实施时改为自动 complete 并删重置逻辑，**移除** `SORTING_SESSION_COMPLETE_REQUESTED` 人工 complete 事件；异常残留 Session 通过自动 NG/RECONCILING 升级处理。

## 再次 Eng Review 报告（2026-06-21）

本轮复审参考：

- 本文档当前稿。
- `~/.gstack/projects/kaizhoumasha-wes_backend/specs/20260620-124757-1216-workline-root-domain-phase1-epic.md`。
- 代码事实抽查：`WorklineSession` 无 `plugin_state` 字段，`SessionStatus` 为 `NEW/RUNNING/WAITING_DEVICE_RESULT/WAITING_EXTERNAL/MANUAL_HOLD/COMPLETED/FAILED/CANCELLED`；`flow_service.py` 当前 SMT target place 成功后只 `update_context`，人工 complete 入口仍存在；manifest 白名单仍只有现有 6 个顶层字段；`BinMaterialMount` 已有 active `pkg_code` 唯一索引；`NgReturnItem` 已存在；`HandlingMove` 不具备流水线队列 membership 字段；`WorkLineRuntimeStatus` 为 `STOPPED/READY/RECONCILING/ESTOPPED`。

### Step 0：Scope Challenge

结论：**接受完整 Phase 1，不再建议收缩**。参考 Epic 已把第一阶段收敛为 6 个子 issue 并合并为 1 个 PR。虽然该范围会触碰超过 8 个文件，也会新增超过 2 个模型/结构，但这是因为根域缺失是 P1/P2/P3 的共同根因；继续做 `plugin_state` 加列会制造三种状态表示并存，后续返工更大。

最小可交付不是“只建表”或“只写 manifest 合同”，而是以下闭环：

1. `material_units` 表 + `workline_sessions.current_material_unit_id`。
2. 13 处 `phase/business_phase` 写面迁移到 `material_units.status`。
3. handoff/resource/诊断关键读路径直连。
4. SMT 自动 complete + 自动 NG 安全阀。
5. manifest 合同，`state_owner=MaterialUnit.status`。
6. 非法 transition 软告警，挂载在 `material_units.status` 写入时。

不做上述任一项，都会留下“状态有落点但流程还读旧散点”或“合同有声明但没有可观测写面”的半成品。

### What Already Exists

| 既有能力 | 代码事实 | 本计划处理 |
| --- | --- | --- |
| Session 生命周期 | `SessionStatus` 已表达会话生命周期，不需要新造 Session 状态枚举。 | 复用，不把料盘生命周期塞回 `Session.status`。 |
| 对账证据字段 | `WorklineSession` 已有 10+ 个 `reconciliation_*` 字段。 | 继续消费，新增的只是料盘对账前状态 `material_units.reconciliation_from_state`。 |
| resource active projection | `resource_bin_material_mounts` 已有 active `pkg_code` 唯一索引。 | 保留为格位容量/冲突权威，`material_units.current_location` 只做派生缓存。 |
| NG 长期记录 | `ng_return_items` 已存在。 | 插件 NG 搬运成功后写入，不复用 RuntimeHold 解除路径的写入逻辑。 |
| Handling 单次搬运记录 | `HandlingMove` 是 source→target 搬运记录。 | 不复用为队列 membership，新建 `BinTransitMembership` 投影视图。 |
| manifest loader | `WorklinePluginManifest` 已有 dataclass + YAML 白名单 + 投影逻辑。 | 在同一模式下扩展 `session_subject/state_machines/pipeline_queues`。 |

### NOT In Scope

- `workline_sessions.plugin_state`：已废弃，`material_units.status` 取代。
- `workline_sessions.reconciliation_from_state`：归 `material_units.reconciliation_from_state`。
- Runtime transition 强校验阻断：C 阶段做，本阶段只 WARN 不阻断。
- context_json 散读面全量迁移：约 500 处，第一阶段只改关键读路径。
- RuntimeHold reason_code 审计：砍掉，上线后按真实问题补。
- RECONCILING 自动恢复分类：保持现有触发和解除入口。
- 料箱转线能力：扫码点 3 转线为后续扩展。
- 粗分机 `pipeline_queues`：第一阶段不定义，避免过度建模。

### Architecture Review Findings

`[P1] (confidence: 9/10) docs/superpowers/specs/2026-06-19-workline-multi-object-state-machine-design.md — 旧 plugin_state 落点残留会让实施者误加列。`

已处理：正文将状态权威落点统一为 `material_units.status`；manifest 示例改为 `state_owner.model=MaterialUnit`、`state_owner.field=status`；软告警判定时机改为写 `material_units.status`；验收标准改为 `material_units` 表 + `workline_sessions.current_material_unit_id`，downgrade 不再处理旧字段方向。

`[P1] (confidence: 9/10) docs/superpowers/specs/2026-06-19-workline-multi-object-state-machine-design.md — 旧 9 态过程态曾泄漏进 material_units 图示，粗分入单层箱曾被误写成最终完成点。`

已处理：三层对象图只展示 `IN_TRANSIT/STORED/COMPLETED/NG/RECONCILING` 5 态；粗分 PUT_TO_BIN 成功明确为 `material_units.status=STORED` + 粗分 Session 闭环，最终入五层目标格才是 `COMPLETED`。

`[P2] (confidence: 8/10) docs/superpowers/specs/2026-06-19-workline-multi-object-state-machine-design.md — material_units 与 resource 投影双写权威边界必须写清。`

已处理：新增“位置双写必须同事务，投影仍是格位权威”。`material_units.current_location` 是诊断/跨 Session 派生缓存；`resource_bin_material_mounts`/`BinCellOccupancy` 仍是格位容量、冲突、对账事实源。若不一致，以 resource 投影为准并进入 RECONCILING/人工对账路径。

`[P2] (confidence: 8/10) src/workline_runtime/plugin_manifest.py:651/686 — manifest 扩展不是纯 YAML 修改。`

已处理：正文已把成本写进“manifest 扩展成本”：dataclass、白名单、`from_yaml_dict()` 投影、contract_version bump、subject/state_owner/pipeline queue 校验。

### Code Quality Review Findings

No unresolved code-quality issues in the document after this pass.

需要在实施计划中保留的工程约束：

- 新 Service 必须按项目规则在 `__init__.py` 导出。
- 修改现有函数/类/方法前必须跑 GitNexus impact analysis；本轮只改文档，无需执行。
- 规划文档不粘贴完整函数/类/测试实现，继续只写接口名、状态流、字段和验收标准。
- 状态写入应收敛到领域服务或 Runtime 写面，避免插件散落 `context_json` patch 继续写业务阶段。

### Test Review

测试策略必须覆盖 6 个子 issue 的成功、失败和回滚路径。当前文档已补齐测试矩阵要求；实施计划阶段不得把这些测试压缩成“现有测试全绿”。

```text
CODE PATHS / DATA FLOWS                                  TEST REQUIREMENT

[+] #1 material_units migration
  ├── [GAP] upgrade creates table + current_material_unit_id
  ├── [GAP] downgrade drops table/column without losing context_json
  ├── [GAP] status CHECK rejects invalid value
  └── [GAP] MaterialUnit CRUD works with BaseMixin/DataTableMixin

[+] #2 status write surface
  ├── [GAP] rough_sorter scan/build entity -> IN_TRANSIT
  ├── [GAP] rough_sorter PUT_TO_BIN -> STORED
  ├── [GAP] SMT source pick -> IN_TRANSIT
  ├── [GAP] SMT target place -> COMPLETED
  └── [GAP] NG decision -> NG, then NG record + cleanup after move success

[+] #3 direct read path
  ├── [GAP] handoff claim links current_material_unit_id by pkg_code
  ├── [GAP] mount/unmount updates projection + material_units in one transaction
  ├── [GAP] diagnostic query prefers material_units.current_location
  └── [GAP] projection/material_units mismatch enters reconciliation path

[+] #4 SMT complete + NG safety valve
  ├── [GAP] handle_target_place_success returns RuntimeIntent.complete()
  ├── [GAP] _target_success_context_patch no longer resets WAITING_SOURCE_PICK
  ├── [GAP] SORTING_SESSION_COMPLETE_REQUESTED is removed from constants/plugin/manifest
  └── [GAP] abnormal post-place path auto-enters NG, not manual complete

[+] #5 manifest contract
  ├── [GAP] YAML loader accepts session_subject/state_machines/pipeline_queues
  ├── [GAP] invalid subject/state_owner/capacity/order_policy fails fast
  ├── [GAP] rough_sorter + SMT manifests load after contract_version bump
  └── [GAP] state_owner points only to MaterialUnit.status

[+] #6 soft warning
  ├── [GAP] legal transition has no WARN
  ├── [GAP] illegal transition emits WARN and still writes
  ├── [GAP] RECONCILING static recovery set branch
  └── [GAP] plugin without state_machines declaration does not warn

COVERAGE TARGET: 26/26 planned paths covered before PR is accepted
QUALITY TARGET: all state writes have success + failure tests; migration has upgrade + downgrade tests
```

### Performance Review

No P1/P2 performance blockers in the document after this pass.

Performance-sensitive constraints to keep in the implementation plan:

- `material_units.pkg_code`、`status`、`current_session_id` 需要索引，避免诊断和 handoff 直连退化为扫表。
- `material_units.current_location` 是一次查询优化，但必须以 resource 投影为事实源；不要反向从缓存覆盖投影。
- 软告警应复用已解析 manifest 合同，避免每次 status 写入重新解析 YAML。
- WARN 日志必须带 `object_type/object_id/from_state/to_state/pkg_code`，但不要在热路径输出大 payload。

### Failure Modes

| Flow | 生产失败方式 | 文档是否覆盖 | 测试要求 |
| --- | --- | --- | --- |
| migration | downgrade 漏删 `workline_sessions.current_material_unit_id` 或 CHECK 漏建。 | 已覆盖验收。 | migration upgrade/downgrade + CHECK 测试。 |
| 扫码建 material_unit | 同一个 `pkg_code` 重复扫码，出现重复活料盘。 | 已定义 `pkg_code` 业务键。 | 幂等/唯一冲突测试。 |
| 状态写面迁移 | 某处旧 `phase/business_phase` 仍写 context_json，导致诊断读到旧态。 | 已列 13 处写面迁移。 | grep 断言 + 流程集成测试。 |
| 双写投影 | `material_units.current_location` 与 resource 投影不同事务导致漂移。 | 已补同事务与权威边界。 | mount/unmount 事务失败回滚测试。 |
| SMT complete | target place 成功后 Session 仍 RUNNING。 | 已明确自动 complete。 | target place success 集成测试。 |
| NG 安全阀 | NG 搬运前清空 Session 绑定或删除 material_unit，物理在途但系统无记录。 | 已明确搬运成功后清空绑定且保留根实体。 | NG 搬运成功/失败两分支测试。 |
| manifest 合同 | YAML 接受错拼 `state_owner`，软告警永远不生效。 | 已列校验。 | invalid manifest 单测。 |
| soft warn | 非法 transition 被误阻断，影响生产流程。 | 已明确 WARN 不阻断。 | 非法 transition 写成功 + 日志断言。 |

无“无测试、无错误处理、且用户静默失败”的剩余 critical gap；所有风险都已转成验收或测试要求。

### Worktree Parallelization Strategy

| Step | Modules touched | Depends on |
| --- | --- | --- |
| #1 material_units 表 + 模型 + 迁移 | `migrations/`, `src/app/workline/models/` | 无 |
| #2 状态写面迁移 | `src/workline_plugins/rough_sorter/`, `src/workline_plugins/smt_sorting_inbound/`, workline services | #1 |
| #3 关键读路径直连 | `src/app/workline/services/`, `src/app/resource/services/`, diagnostics | #1 |
| #4 SMT complete + NG | `src/workline_plugins/smt_sorting_inbound/`, NG write path | #1 |
| #5 manifest 合同 | `src/workline_runtime/`, plugin manifest YAML | #1 |
| #6 soft warn | material_units status write logic, `src/workline_runtime/` helper | #1, #5 |

推荐并行方式：

```text
Lane A: #1 migration/model (must land first)
  └── after merge:
      Lane B: #2 status write surface
      Lane C: #3 direct read path
      Lane D: #4 SMT complete + NG
      Lane E: #5 manifest contract
  └── after Lane E + status write helper settle:
      Lane F: #6 soft warn
```

冲突提示：#2/#4 都会碰 SMT plugin；如果并行 worktree 执行，需要先约定 `flow_service.py` ownership，或把 #4 放在 #2 同一 lane。#3 与 #2 都可能碰 resource mount/unmount 写面，projection_service 的事务边界应由一个 lane 统一收口。

### Completion Summary

- Step 0 Scope Challenge：scope accepted as-is，完整 Phase 1 合并 6 个子 issue，不再收缩。
- Architecture Review：4 个问题，均已折入本文档。
- Code Quality Review：0 个未解决问题，实施约束已写入。
- Test Review：已产出 coverage diagram，26 个计划测试路径。
- Performance Review：0 个 P1/P2 blocker，索引、缓存权威、manifest 缓存约束已写入。
- NOT in scope：已写入。
- What already exists：已写入。
- Failure modes：0 个 critical gap。
- Parallelization：6 个 workstream，#1 串行前置，#2/#3/#4/#5 可并行，#6 后置。
- Lake Score：选择完整 root-domain Phase 1，而不是 `plugin_state` shortcut。

## Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above. Run with Claude Code or Codex; checkbox as you ship.

- [ ] **T1 (P1, human: ~5h / CC: ~45min)** — `material_units` — 建立料盘根实体表与 Session 追溯列
  - Surfaced by: Step 0 / Architecture Review — 第一阶段根因是缺料盘实体，`plugin_state` 加列已废弃。
  - Files: `migrations/versions/`, `src/app/workline/models/material_unit.py`, `src/app/workline/models/__init__.py`
  - Verify: `uv run alembic upgrade head`、`uv run alembic downgrade -1`、CHECK 约束测试、模型 CRUD 测试。
- [ ] **T2 (P1, human: ~10h / CC: ~90min)** — 状态写面 — 迁移 13 处 phase/business_phase 到 `material_units.status`
  - Surfaced by: Architecture Review — 旧状态写面必须统一到 5 态物视角。
  - Files: `src/workline_plugins/rough_sorter/`, `src/workline_plugins/smt_sorting_inbound/`
  - Verify: 粗分扫码/入单层箱、SMT 取盘/入五层箱、NG 判定流程测试。
- [ ] **T3 (P2, human: ~12h / CC: ~90min)** — 关键读路径 — handoff/resource/诊断改为 material_unit_id/pkg_code 直连
  - Surfaced by: Architecture Review — 跨 Session 关联和定位不能继续依赖散落 context_json。
  - Files: `src/app/workline/services/`, `src/app/resource/services/`, diagnostics 查询服务
  - Verify: handoff claim、mount/unmount、诊断位置查询、投影不一致 RECONCILING 测试。
- [ ] **T4 (P1, human: ~12h / CC: ~90min)** — SMT 收尾 — target place 成功自动 complete，并移除人工 complete 事件
  - Surfaced by: Architecture Review — `handle_target_place_success` 当前不 complete，人工 complete 违反边界。
  - Files: `src/workline_plugins/smt_sorting_inbound/flow_service.py`, `plugin.py`, `constants.py`, `manifest.yaml`
  - Verify: `SORTING_SESSION_COMPLETE_REQUESTED` 无残留，target place success 后 Session `COMPLETED`。
- [ ] **T5 (P2, human: ~11h / CC: ~90min)** — Manifest 合同 — 扩展 `session_subject/state_machines/pipeline_queues`
  - Surfaced by: Architecture Review — 当前 manifest 白名单没有新合同字段，纯 YAML 不会生效。
  - Files: `src/workline_runtime/plugin_manifest.py`, two plugin `manifest.yaml`
  - Verify: manifest loader 单测、invalid subject/state_owner/capacity/order_policy 测试、两 manifest 加载测试。
- [ ] **T6 (P2, human: ~7h / CC: ~60min)** — Soft Warn — `material_units.status` 非合同 transition 输出 WARN 不阻断
  - Surfaced by: Architecture Review / Failure Modes — C 阶段强校验前需要可观测性。
  - Files: material_units status 写面、`src/workline_runtime/plugin_manifest.py` helper
  - Verify: 合法/非法/RECONCILING/无声明四分支测试，确认非法转移仍写成功。
- [ ] **T7 (P2, human: ~4h / CC: ~45min)** — 测试矩阵 — 把本报告 26 个 planned paths 写入实施计划验收
  - Surfaced by: Test Review — 不能只写“现有测试全绿”。
  - Files: 后续 implementation plan、对应 `tests/` 目录
  - Verify: `uv run pytest` 覆盖 migration、写面、直连、SMT complete/NG、manifest、soft warn。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR | 参考 epic 已重塑为料盘根域 Phase 1，废弃 `plugin_state` backlog |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | N/A | 本轮未运行外部 Codex diff review，当前是文档计划审计 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 4 architecture findings folded, 26 planned test paths, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | N/A | 后端状态机/文档计划，无 UI 范围 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | N/A | 未运行；实施计划已保留测试命令与分工边界 |

**VERDICT:** CEO + ENG CLEARED — ready to write the implementation plan; this document is not an implementation.

NO UNRESOLVED DECISIONS
