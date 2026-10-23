# 自动拣料插件实施计划

> 2026-10-23 最新接线基线：扫码要求可靠、唯一的取盘归属，不再强制 SCAN_COMPLETED 回显 command_code。取盘结果携带 barcode 仅为待设备确认方案，字段、原扫码时间及稳定性保证不得猜测。ARM02 退料取出单盘已确认，设备定位参数仍待确认；此前指定回显渠道和字段的阶段记录由本段及末尾接线收口取代，不表示实际设备方案已冻结。

> 2026-10-22 当前基线：出库物料统一使用 `barcode` 原文（非空、最多1000字符），decide、报告与关闭沿本盘同一扫码 Evidence；G5 独立 PkgID 来源门禁与读取端口已撤销，不做双字段兼容。下文既往实施/验收记录中的 PkgID、缺编号延期和外部编号 fixture 仅保留为历史说明，已由本轮扫码原文规则承接，不是当前待办或可复用的新合同证据。扫码精确关联、设备映射及 CK04 各自门禁不变，C1/C2 不扩大。

> 硬件说明书收敛：位置/料盘字符串、单元格1–7、层1–5、毫米/寸和必填字段已明确，business_key 不再是前置条件。下文旧阶段“完整参数由外部工厂给定”、O4/O10/O15/O16 整体未定的说明已由本文末尾本轮映射实施与机械臂附录 §10 承接；最新确认箱型直接为2/3/6、rack_face 原样传递，ARM01 正常取料并触发扫码、ARM02 入架；旧箱型/面向映射表已撤销。仅扫码精确关联、实际位置绑定/时限及 WMS 尺寸单位对齐限制各自真实路径；ARM02 退料单盘已确认，其设备定位参数及业务装配只限制退料路径，CK04 只限制目标 ROTATE。

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `superpowers:executing-plans` 按切片执行，使用复选框记录进度。推荐主 Agent 顺序实施；本计划不授权自动派生 Subagent、Commit、Push、Merge 或 Deploy。

**Goal:** 先完整对齐已确认的 WMS DTO、SDK、typed converter、OpenAPI 与测试，再交付独立 `automatic-picking` 插件的正常业务闭环及其持久恢复。

**Architecture:** 宿主提供 typed operation、可靠接收、执行、派发和重放，自动插件拥有当前业务授权、工作轮次、Cell 循环、目标准备及完成判断。复用既有插件端口和扫描入口，不抽取 `bin-line-common`，不导入人工插件业务实现。

**Tech Stack:** Python 3.13、现有 Pydantic/SQLModel/SQLAlchemy、PostgreSQL、Celery、pytest、`uv`；不增加依赖来替代既有基础能力。

**Spec:** [修订后的自动拣料插件设计](../specs/2026-09-22-automatic-picking-plugin-system-design.md)，SHA-256：`850182fc063f6813c64ba1c3b7c5ff9a7103d9bb4c92f91caf6360b78207a62f`。

**状态与基线:** 原始计划基线为 `develop / d38cced4201e0a09e46ceff5fe7b9f84dce20f13`；截至 2026-10-23，当前工作分支 `codex/automatic-picking-workflow` 已提交至 `7251bd7b`；本轮最终转运架正常离场位于工作区，未提交。9H 历史证据见[9H 收口索引](#task9h-offline-closure)；搬运接线、正常投料到 Work、Work WAIT 扫描续问的当前归属见[任务 11 证据收口](#task11-transport-work-evidence)。用户已确认 manual-picking 货架与料箱搬运经过现场验证，自动插件沿用该基线，后续重点转到料箱到达工作位后的自动化业务。早期切片中的“本轮”“留下一片”“未提交”等只描述当时快照，当前提交状态以本段及收口索引为准。整项任务 9/11、自动插件整线和 WMS 联合验收尚未完成；本轮业务交付及验证边界以文末“最终转运架正常离场”记录为准；不提交、不部署、不启用生产装配或真实派发。
进入实施前核对设计指纹；授权设计修订后同步更新计划对应切片，不能在执行中自行解释合同。

## 全局约束

- 所有实现切片均按 `LARGE/HIGH-RISK` 管理：RED → 最小实现 → GREEN；文档整理不创建代码或 pytest。
- `API → Service → Repository → Database`；纯 Decision 只依赖 SDK、无 I/O；插件应用层经基础 Service 创建可靠对象。
- 公共能力各实现一次；operation 不重建 HTTP、inbox/outbox、领取、重试或队列。宿主及 SDK 不导入具体插件。
- 技术重试保留原身份、时间戳与冻结正文；确定 WAIT 才按业务决定创建新身份。ACK、ACCEPTED 和超时不替代物理终态。
- 只根据当前有效 Requirement 与权威 Fact 创建动作；原成员取消、父任务完成、已闭合历史和新轮次分别处理。
- 两臂命令可独立下发，实际交接与互锁由 ECS/PLC 裁决；不加平台释放事件、扫码台锁、设备占槽或全线 MaterialExecution FIFO。
- 不抽取共享人工业务包，不复制人工状态机，不建兼容别名、旧字段双路径、动态 registry 或成功占位消费者。
- 新表及迁移随真实业务切片增加，复用 MaterialExecution、Command、Transport、Evidence、Confirmation；不预建所有模型。
- 所有命令使用 `rtk`，项目命令使用 `uv run`；测试所有权遵守 [tests/README.md](../../../tests/README.md)。

## Review Focus

1. 回架候选查询为空但原请求未知，不能触发离场；独立架不受统一阻断。测试 owner：任务 8。
2. M1 报告先确认、M2 尚未创建时重启，当前授权仍由 Work 恢复且至多消费一次。测试 owner：任务 6、7。
3. PREPARING/revision 0 必须可达首批超期确认；计划先到或旧响应迟到不得错误完成任务。测试 owner：任务 5。
4. 父任务完成、没有新设备事件时，drain WAIT 到期仍能以新身份重问；新候选不加入旧 READY reservation。测试 owner：任务 9。
5. 共享类型传播不能遗漏：旧出库字段退出，新 revision 不被旧结果关闭；入库独立合同不得被误改。测试 owner：任务 1、2、10。
6. 尚无 MaterialExecution 时，SCAN2 的 work_plan 可经固定 typed 端口创建、读取和恢复；不得提前虚构单盘身份。基础接线 owner：任务 3A；业务 owner：任务 5。
7. 新建 drain 或 WAIT 到期重问前，同一 WorkLine 事务优先准备下一任务；原未决请求及 READY 不被覆盖。测试 owner：任务 9。

## 前置事实与未决项

协议真源为 [SRS](../../architecture/SRS.md)、[出库主合同](../../contracts/wms-outbound-picking-task-integration-requirements.md)、
[WMS 联合确认](../../integration/wms-joint-confirmation-automatic-picking.md)和[机械臂附录](../../integration/device-annex-automatic-picking-arms.md)。
以下限制只作用于相应字段或执行分支；不能用 Mock、放宽断言或临时字段绕过，也不能据此阻塞无关已确认部分。

| 编号 | 已明确 / 尚未明确 | 影响与解除证据 |
| --- | --- | --- |
| G1 字段结构与边界 | WMS 已确认数字类型/枚举与 barcode ≤1000；硬件说明书已明确设备侧字符串、单元格1–7、层1–5、毫米/寸及必填字段 | DTO 不变，插件设备边界转换；不再要求已明确字段重新冻结，不猜列数上限或其它物理范围 |
| G2 可靠取盘与扫码归属 | 不限定 SCAN 回显 command_code；设备承载报文、实际字段和延迟/重复/重启保证待确认。结果带 barcode 仅为候选，business_key 不再是门禁 | 仅限制真实扫码汇合；原样 barcode 与持久绑定已有实现，禁止最近命令/时间/平台容量推断 |
| G3 Bin 与目标设备映射 | 箱型直接使用2/3/6、统一 rack_face 原样传递，已删除转换表；正常链显式绑定 ARM01 来源、ARM02 目标。剩余为实际位置绑定与时限、WMS 尺寸单位对齐 | 缺哪个输入只停对应命令，原 Work/target owner 负责重扫。CK04 只限制目标 ROTATE；普通目标、F01 REPLACE 不等 CK04 或 C1/C2 |
| G4 DirectPick 映射 | ARM02 从退料架取出单盘已确认；退料架设备位置类型、slot 到设备定位字段及确需尺寸参数的来源未定，生产 driver 尚未装配 | 限制任务 10 的直接取料设备路径；若需扩展 plan_delta，先批准具体 wire，再同步全部消费者，不反推 bin_* 参数 |
| G5 barcode 统一（已收敛） | decide、movement_report 均为本盘扫码原文，1–1000字符 | 删除独立编号来源与读取端口；报告和关闭校验本盘原扫码及已发布决定，不再阻塞真实接入 |
| G6 首批期限 | 业务超期规则已确认；显式毫秒配置、持久到期点和原完成确认接续已实现，实际期限值待联调配置 | 未配置只保留等待；已冻结到期点由原 batch/completion owner 恢复，不复用设备30秒或技术派发窗口 |
| G7 异常分支 | REJECT、空取和 NG 协议保留，设备失败/空取语义及恢复动作仍有未决 | 正常切片只可靠留证并保留依赖；这些分支另做实施切片，不能因此删 DTO 或宣布整线业务验收 |

任务 1 可先完整推进。任务 2 按 2026-10-16 确认表开展 RED 与受控替换；G1 剩余设备映射不阻塞 DTO 与共享接线；
任务 3 及后续可先做不依赖未决值的 typed/纯业务工作，依赖这些值的生产接线须等待对应证据。
正常取盘生产闭环要求 G1/G2/G3/G5 的对应项闭合，首批超期路径另受 G6 约束；DirectPick 设备路径另须 G4。

### 实施顺序、代码依赖与生产门槛

编号是默认推进顺序，不把整项任务验收当成所有后续工作的硬依赖。先对齐相应路径已确认的合同，再接其业务；
允许独立声明、已确定接口及纯业务测试先完成，不能用旧 DTO、临时类型或成功占位绕过未决字段，不能把部分完成记成整片 GREEN。

| 可独立推进的部分 | 实际代码依赖 | 未决项只限制什么 |
| --- | --- | --- |
| 任务 4 包声明、静态装配 | 任务 0、现有插件 SPI；具体端口随所属业务片注入 | 不等待 G1–G6；未接业务时不允许宣称插件已可执行 |
| 任务 5 准备/投料、首批等待、Work | 分别依赖任务 4/既有投料端口、确认进度、任务 3A | G6 只限制首批期限配置与超期路径；Work DTO 已确认；G1 剩余项只限制对应设备映射，不依赖 G2/G3/G5 |
| 任务 8 的 NO_WORK 流出及回程 | 任务 5 的 Work/点2交接、既有方向命令与回架端口 | 生产调用须有完整获批 work_plan decoder 和对应方向合同；不依赖任务 6–7 或 G2/G3/G5，离线回程测试可先做 |
| 任务 8 的逐盘结束后流出 | 上一行，加任务 6–7 的明确无后继及本轮逐盘收口 | 只有这一入口继承取盘/报告的对应未决项，不扩大到回程公共推进 |
| 任务 9 drain 准入及完成判断 | 任务 8 的当前 FIFO/回程、任务 5 的 prepare 协调与确认进度 | 不等待全量取盘设备验收；实际关闭含物料任务仍须其必报事实已确认 |
| 任务 10 的取消/范围完成与直接取料 | 计划事实部分依赖任务 4–5 及既有接收；设备部分复用任务 6–7 | G4 只限制 DirectPick 设备接线，G5 只限制需逐盘报告的正常闭环，不要求先完成无关 drain |

### 当前任务 5–6 剩余项与继续条件

以下按已提交基线及后续 5A/6A/6B/6C 的明确验收范围核对，不把基础端口已有、单项测试通过或本计划的复选框当成整项业务已完成。

| 路径 | 当前事实与归属 | 继续条件 |
| --- | --- | --- |
| 任务 5 普通 prepare、投料、SCAN2/Work | 已实现并提交，Work 的 READY/WAIT/NO_WORK、结果应用/发布及持久恢复已有验收 | 不受 G2/G3/G5/G6 阻塞；后续只补本业务缺口，不重复实现共享可靠机制 |
| 任务 5 跨 revision 来源接续 | 切片 5A 的 6 项 PostgreSQL 验收已通过：当前进场复用原 Transport，各 revision 成员独立申请，重启/并发扫描防重、事务回滚、取消及闭合历史清理均已覆盖；未改生产代码 | 本项已闭合，不依赖首批期限或机械臂参数；首批确认和任务 6 按各自条件继续 |
| 任务 5 首批等待与 revision 0 确认 | 宿主在查找 EXECUTING 前已有 batch 钩子；自动插件目前只恢复 Feed/Work，尚无任务确认进度、配置入口或 completion driver | G6 需给出期限值、单位、合法范围及具体配置落点；不得借 30 秒技术派发期限、重启时间或 WMS 重试间隔代替 |
| 任务 5 正常完成确认的共用协调器 | 普通完成的业务发起、持久 current 与确定重问已由退料架级收口/正常完成交付；revision 0 首批分支仍待期限冻结 | 首批分支受 G6 限制；普通完成分支不因 G6 一概停工，但必须具备可证明的当前本地完成谓词。涉及逐盘报告、Work 放行及回程的完整验收随任务 7–9 闭合，不把空查询当成完成 |
| 任务 6 已确认纯业务规则与持久适配 | 6A 的来源规则/协调器由 6B 补入 Pickup、Work 消费/当前取出事实引用及 Repository；batch/Work owner 可显式注入来源协调器，生产组合根尚未启用 | 6A 离线证据与 6B PostgreSQL 证据分别记录；6C 补齐明确关联输入后的单盘应用；真实事实 reader、设备映射与生产扫码汇合仍未启用，不能以持久适配或离线接线代表生产取盘闭环 |
| 任务 6 Pickup/来源 Command 与扫码关联 | 同事务保存授权消费、Pickup、来源 Command；真实扫码必须精确关联 | G2 只保留实际事件设备和精确原 Command 引用，G3 只保留对应未知映射/绑定；不要求 business_key，不先建无 Command 的 Pickup 或仅先扣减授权 |
| 任务 6–7 不可逆取盘及正常逐盘闭环 | 需要可靠取出、关联扫码、decide、目标动作和 movement_report 都有确定合同 | 来源映射/关联及 G3 目标映射仍须闭合；G5 已统一为本盘 barcode 原文，不再等待独立编号 |

G1 的 WMS 字段类型和已确认枚举不重新列为阻塞：`cell_index=1..7`、`bin_type=2/3/6`、层列/层数整型、厚度/直径浮点及原始 barcode 长度已接通；这些不等于设备字段映射已经验收。G4 只限制任务 10 的直接取料设备路径；G7 只限制对应异常分支，不扩大成投料、Work 或纯规则测试的停工条件。

**任务 5 首批分支的唯一 owner 与出口：**G6 闭合后，由自动插件的同一个 completion driver 接入现有 batch 前置钩子，扫描 PREPARING/revision 0。首次匹配 PREPARE_ACCEPTED 的持久接收时间加冻结期限得到到期时间，重复扫描/重启不重置；计划先到停止新建 revision 0 请求，已有义务仍沿原身份交付。确认创建和进度同事务；未决结果由基础 Confirmation 恢复，明确 BUSINESS_IN_PROGRESS 的新请求由插件按到期重新求值，PLAN_REVISION_STALE 等补发增量，旧响应不能关闭新 revision。普通 EXECUTING 分支使用同一协调器，不另建 AUTO worker/timer 或第二个完成 owner。

**任务 6 的所有权边界：**初始及后继待消费授权归当前 Work；消费授权、创建/复用 Pickup 和原来源 Command、登记消费关联必须同事务。Command 技术恢复归设备基础服务；取出结果与扫码汇合归插件 driver；关联成功后才创建/复用 MaterialExecution。最终 ACCEPT 的本盘目标与后继授权/明确无后继交接同事务，Work 可在 M1 闭合后独立恢复 M2，不查询可清理的 M1 历史。只有当前栈顶可靠取出且有 WMS 后继授权才可开始下一来源；无需等待 M1 PUT/report，全线/平台不加软件锁，互锁归 ECS/PLC。

#### 实施切片 5A：跨 revision 来源接续验收（已完成）

**交付：**闭合任务 5 中不依赖 G6 的剩余来源身份路径；先验证现有能力，只在发现实际缺口时作必要修正。不新增模型、状态、配置、operation 或生产入口，不顺带做任务 6–9。

**文件与接口：**新增插件 owner `T/integration/test_source_revision_postgresql.py`；复用宿主 `PickingTaskPlanActivationService.activate_batch()`、插件 `AutomaticPickingBatchDriver.advance_in_session(db, line, task)`、`FeedRepository.source_ready(db, line, member)` 及既有 Transport/Confirmation。若回归证明插件有缺口，仅修改对应 `P/application/{batch_driver,feed_repository}.py` 或计划 handler；若是共享能力缺口，单独说明真实影响，不在插件复制基础机制。既有新旧批次扫码用例仍保留其主要测试所有权。

- [x] 先建立同任务、同 rack/face、两个 revision 的真实成员及当前进场事实，验证复用当前物理生命周期、原 Transport 身份不变；每个新有效成员恰好创建一次带本成员 revision 的 inbound_batch。
- [x] 验证重复扫描/重启/丢通知不重复申请；创建 Feed 与原 Confirmation 的事务中断全部回滚。旧成员取消只停其未提交尾部，不能取消新成员或让旧结果满足新成员。
- [x] 验证位置未知、未发布或实际面向不匹配时不得提前投料；新成员需要另一面时只验证等待其实际到位，不提前实现旋转/离场。清理不再承接当前事实的已闭合历史后，新成员仍按当前有效意图和事实运行。
- [x] 测试准备数据及批处理计数期望对齐现有端口后，6 项业务验收均通过，未发现生产实现缺口，保留现有生产代码。未为得到 RED 破坏实现。
- [x] 在隔离 PostgreSQL/显式插件环境运行新 owner 与插件受影响回归；如触及宿主入口、队列或模型再选择对应真实 worker、HEAVY/迁移验证。插件测试不进入核心默认套件；按最终差异做一次闭环 Review。

**5A 验证与 Review：**新增 [来源接续 PostgreSQL owner](../../../workline_plugins/automatic-picking/tests/integration/test_source_revision_postgresql.py) 共 6 项；最终显式插件套件 `uv run pytest -c workline_plugins/automatic-picking/pytest.ini workline_plugins/automatic-picking/tests -o addopts= -q --tb=short` 为 61 passed（42 FAST、19 PostgreSQL，含既有真实 Confirmation worker）。测试拓扑/插件所有权检查 14 passed，Ruff 检查、格式及 diff 检查通过，核心 HEAVY selector 为 NONE。每项 PostgreSQL 场景使用迁移至 head 的独占临时库；未改模型、迁移或宿主入口。
闭环 Review 核对了当前窗口/原 Transport、成员 revision、并发恢复、事务回滚及取消边界，无未关闭问题。历史清理用例先经真实 FactProcessor 发布旧结果，再删除已闭合的旧 Confirmation，并验证 revision 3 继续申请；当前成员、Feed 完成标记、进场绑定和权威位置依据继续保留，未把当前事实误作可删除历史。任务 5 其余未完成项和任务 6 保持原状态；未提交、未部署。

**验收：**当前生命周期复用和成员业务身份独立同时成立；每个未闭合 Feed 的 owner 仍是既有 batch driver，Confirmation/Transport 各沿原身份恢复；不读取已闭合历史决定新业务，无新机械臂 Command。验收后仅勾选任务 5 的来源跨 revision 项，首批超期与任务确认项、任务 6 仍不得整体勾选。

G6 获批后再做任务 5 的首批确认小片；任务 6 的纯规则及来源事务协调器已由下述 6A 完成离线验收，fixture 中设备参数仅为测试输入/非合同，不生成生产默认值、fallback 或成功占位消费者；模型仅随能够独立验证的生产原子业务片落地。生产 Pickup/Command 与扫码接线待对应合同闭合后整体接通。若转做任务 8 的明确 NO_WORK 出口，可按其原有独立依赖另开切片，不以任务 6/G5 为阻塞，也不混入本次 6A。

#### 实施切片 6A：来源规则与离线事务验收

以下记录 6A 结束时的范围；后续持久适配以 6B 的记录为准。

**范围：**插件新增 `P/handlers/source_authorization.py` 和 `P/application/source_flow.py`；分别由 `T/test_source_authorization.py`、`T/test_pickup_flow.py` 拥有验收。`WorkSourceFact` 只是当前 Work、直接 Requirement 和当前物理事实的不可变输入快照，不是生产授权表、Cell 库存或新业务状态。

- [x] READY/后继授权归当前 Work；按 Work 和原授权 Evidence 消费。同 Cell 可连续取多盘，不以 `(work, cell)` 去重；跨 Cell 只跟随 WMS 后继，不按 Cell 编号排序。
- [x] 消费授权、创建 Command/Pickup、记录消费关联共用调用方事务；ACCEPT 目标与后继授权/明确无后继也共用事务。中断、并发扫描、重启和旧结果重放只恢复当前身份。
- [x] 当前栈顶未取得可靠取出事实时不越过；后继授权有效且栈顶可靠取出后，不等 M1 的目标准备、PUT 或报告。实际双臂交接许可仍归 ECS/PLC。
- [x] M1 已交接的当前事实保留在 Work 输入中，注入 M1 已闭合的前提并清理旧 Pickup/Command 测试历史后仍可创建 M2；旧 READY/ACCEPT 不覆盖新授权。新 Work 轮次独立消费，实际同箱 SCAN2 准入仍由任务 5 的既有 owner 验证。
- [ ] 新增生产持久模型/Repository、真实权威事实 reader、完整的单盘 MaterialExecution/fact_factory、扫码汇合及 runtime composition；这些仍属于任务 6 的后续切片，不由离线 fake 承接。

**端口与恢复边界：**协调器要求 `source_for_update` 在调用方事务内按 WorkLine→Work 顺序锁定，ACCEPT 再锁定当前 Pickup；不提交事务、不执行网络 I/O、不等待其他机械臂或设备结果。待消费授权与消费关联由当前 Work 承接；Command 可靠恢复复用基础服务。请求映射器为必填注入项，仅返回现有 `DeviceCommandRequest`，保留 WorkLine 与扫码前 Pickup execution_ref，不能追改为稍后识别的 MaterialExecution；没有生产默认映射或 fallback。当前没有生产持久适配和装配，因此运行时尚不能创建或恢复此片的 Pickup；未来接通时由已有 batch/Work owner 恢复，不能另建自动 worker。

**离线证据：**新增 36 项（纯规则 12、事务 24），插件 FAST 合计 78 passed；核心测试拓扑/插件所有权 14 passed。事务端口仅在测试内实现，使用本地 SQLite 真实回滚与并发写事务；其写事务串行化是测试 harness，不是生产平台锁，也不证明 PostgreSQL 行锁实现。双臂验收只覆盖下一来源不等待前盘目标阶段，真实 PUT/双臂派发与互锁属于任务 7 及设备合同。Command 参数、可靠取出事实、M1 闭合前提和 MaterialExecution 关联均为明确的测试输入；不解析设备回调、不构造扫码关联、没有 PkgID，也不派发设备命令。19 项原有 PostgreSQL/worker 测试本轮未执行，生产入口未变，不以旧结果替代本片证据。

**后续准入：**G2/G3/G5 继续只约束对应的生产取盘、设备/扫码关联和逐盘闭环；G6/G4/G7 不扩大阻塞本片。任务 6 的完整验收项继续保持未勾选，不将 6A 当作可部署的取盘闭环。

**6A 闭环 Review：**已核对规则输入、Work 授权消费/交接、Command 原身份、测试所有权及当前运行时调用面，无未关闭问题。明确 WorkLine→Work→Pickup 锁顺序，补强连续取盘使用不同 MaterialExecution 的测试身份，保留真实 PostgreSQL 适配的验收门槛；纯规则仅导入 SDK，安装包可导入新增模块，核心 HEAVY selector 为 NONE。未新增生产持久模型、业务状态、配置或基础层业务逻辑，未接 composition，未提交、未部署。

#### 实施切片 6B：PostgreSQL 来源授权持久化与离线恢复

**范围：**复用 6A 的 `AutomaticPickingSourceFlow`，增加插件私有 Pickup 与 Repository。Work 只补 `consumed_pickup_id` 和 `top_removed_evidence_id`：前者用于恢复同一消费结果，后者将当前可靠取出事实保留给后继，不依赖已闭合前盘。来源 locator、本盘目标、ROTATE/REPLACE 及后继/明确无后继内容均引用原 Evidence，不复制响应、不新增授权表或业务状态。

**恢复 owner：**已有 batch/Work driver 显式注入来源协调器后扫描当前 Work；未消费授权归 Work，已创建 Command 的技术恢复归设备基础服务。取出尚未知时前盘仍是当前未闭合依赖；匹配事实转入 Work 后不查询其可清理 Pickup/Command。锁顺序 WorkLine→Work→Pickup，短事务内不执行外部 I/O。没有新 worker、平台锁、全线 FIFO 或跨臂等待。

**验收范围：**真实 PostgreSQL 及真实 `DeviceCommandService.create_command_in_session`；覆盖各写入阶段回滚、行锁等待、并发消费/ACCEPT 重放、同 Cell 和跨 Cell 后继、明确无后继、未知栈顶补证、成员取消、旧 READY/ACCEPT 重放、清理旧 Pickup/Command 后恢复、存量 Work 升级与有数据降级拒绝。模型/迁移差异、核心测试所有权及 selector 单独验证，插件事务测试唯一 owner 为 `T/integration/test_pickup_identity_postgresql.py`，不进入核心 HEAVY。

**未启用边界：**设备事实 reader 和请求映射器均为必填注入项，没有生产默认实现；测试明确提供设备参数、扫码/MaterialExecution 关联与可靠取出语义。生产组合根不注入来源流程，设备命令不派发；6B 结束时尚无单盘业务应用/FactProcessor handler，后续范围见 6C；生产扫码汇合及任务 7 仍未实现。G2/G3/G5 继续仅限制对应生产路径，G4/G6/G7 不扩大阻塞本片。未提交、未部署、不推进真实取盘。

**验证与 Review：**插件合计 128 passed（78 FAST、31 项本片 PostgreSQL、19 项既有 PostgreSQL/真实 worker），无跳过；核心安装/拓扑/selector 聚焦 252 passed。完整 QUALITY 通过（脚本 428 passed，默认 FAST 4280 passed/5 既有 skipped），核心 selector 仅选 2 文件，固定 PostgreSQL 17.10/TimescaleDB 2.27.1 上 HEAVY 18 passed。模型/迁移 diff 为零；全量核心 metadata 比对有既有 inbound_evidences/picking_tasks 环引用 SAWarning，不属于本片模型差异。

独立首轮 Review 发现 1 项：调用方预载取消前成员时，WorkLine 锁不会刷新 ORM identity map，可能让已取消 Requirement 创建 Command。真实 CancelService 交错回归 RED→GREEN，锁后显式刷新直接成员，未加状态或锁模型；最终受影响插件集合 109 passed。此前本片验收发现的“重复当前取出事实仍要求旧 Pickup 存在”也已由 RED→GREEN 修正。同一 Reviewer 在一轮中完成意见闭环及 fresh full Review，无未关闭 finding。后续修正只触及插件 Repository/测试，未失效的核心 QUALITY/HEAVY 与既有 worker 证据不重复执行。证据绑定当前可执行文件指纹及隔离环境，不代表生产来源流程启用或真实设备验收。

#### 实施切片 6C：精确关联输入后的单盘离线接线

**范围：**`P/application/material_flow.py` 实现必填 typed `MatchedMaterialScan` reader 边界、原扫描 Evidence 的单盘绑定和 Fact 增强；`P/handlers/material_flow.py` 只依赖 SDK，纯 handler 创建首次 `PickingMaterialIntent` 或确定 ACCEPT/WAIT 的 `Wait` Decision。扫描必须精确给出 WorkLine、Pickup、来源 Command、原始 barcode 和扫码时间；测试专用报文不是设备合同，不推断最近命令、设备位置或 PkgID。

- [x] 原扫码 Evidence、Pickup→MaterialExecution 关联同事务保存；重复、并发及重启沿原 admission Evidence 复用一个单盘。Pickup UUID 仅用作 WES 本地 trace/执行/首次 decide identity，不能作为 WMS PkgID；原 prescan Command 的 execution_ref 和空 material_execution_id 不改绑。
- [x] FactFactory 核对原 Work/成员/授权来源、当前单盘与扫码、typed 原请求、结果 operation/response Evidence 及摘要；纯 handler 保留 barcode 原文和 scanned_at，不在基础层解释 Cell、目标或后继业务。
- [x] 宿主只增加可选 `PluginDecisionApplication`：精确 frozen plugin binding 解析后，在 Decision 应用与 Evidence 发布之间调用，复用 FactProcessor 原事务、claim、失败退避与持久扫描；宿主既有 `material_decide_typed` 补持久请求到 SDK intent 的解码，Repository 校验持久摘要/请求身份，业务应用不读取 wire 信封；插件回调复用 6B `handoff_in_session`，目标结果引用与 Work 后继/明确无后继和 Evidence 发布同事务。未安装回调的插件行为不变。
- [x] 明确 WAIT 是已确定结果，原 Confirmation 完成、MaterialExecution HOLD、原授权仍已消费，不创建后继或新请求；交付未知尚无业务结果，基础 Confirmation 以 PENDING/retry_eligible/next_attempt_at 保留原 identity/body 技术重试。技术派发窗口不解释为业务重问期限。
- [x] 离线验证 ACCEPT 不等于可靠取出；得到匹配的栈顶取出 Evidence 后，M1 仍 HOLD 时 M2 可独立绑定/decide，不增加全线 FIFO、平台占槽或另一臂的报告依赖。
- [x] 6D：保存确定 WAIT 的到期进度并由已有插件 owner 到期创建唯一新 operation_id；实现与独立验收见下节，不将 WAIT 原请求重发当作业务重问。

**恢复 owner：**来源授权继续归当前 Work/batch；设备 Command 技术恢复仍归基础服务；精确扫码关联成功后的未发布 MaterialExecution Evidence 唯一归 FactProcessor 与冻结插件。ACCEPT 事务中断会回滚 Decision、Pickup 目标引用、Work 授权与发布标记，持久 claim/退避恢复原 Evidence。绑定及 FactProcessor 均按 Evidence→WorkLine→Work→Pickup 排序；来源 batch/Work 继续使用 6B 的 WorkLine→Work→Pickup，不反向锁扫码 Evidence。扫码与来源 Command 的生产汇合仍须 G2；没有新 worker、授权模型或业务状态。

**装配边界：**`build_plugin` 只有显式注入 material flow 时安装两个 handler/factory/事务回调，离线测试使用真实 FactProcessor、DecisionApplier、Confirmation/Command Service；生产 deployment 仍不注入来源或 material flow。G2 精确扫码生产合同、G3 设备映射/可靠事实读口、G5 权威 PkgID 不猜，任务 7 动作/报告尚未启动；G4/G6/G7 只限制各自分支。

**验证：**最终插件 FAST 84 passed；修正后的受影响 PostgreSQL 53 passed（6C 22 项、6B 31 项），无跳过。首轮完整 PostgreSQL/真实 worker 71 passed；其中未受修正影响的原 19 项 Feed/Work/revision/worker 证据复用，不将其描述为修正后重新全量执行。核心域合同与执行聚焦 949 passed；最终 QUALITY 通过（脚本 428 passed、默认 FAST 4286 passed/5 既有 skipped）。现行 selector 因触及 `outbound_picking/**` 扩为 19 文件，固定 PostgreSQL 17.10/TimescaleDB 2.27.1 与独占 Redis 上 113 passed、无跳过。插件测试不进入核心 selector。已有 metadata 环引用及 plan_delta 测试数据的 Pydantic 序列化 warning 不属于本片新增模型/合同变化。Ruff、格式、计划引用和 diff 检查通过；无 schema 或迁移变化。

**闭环 Review：**首轮发现 2 项 P2：绑定重放与 FactProcessor 的 Evidence↔WorkLine 反序死锁；插件应用层解释持久 wire 信封。先以真实 PostgreSQL 受控交错复现死锁，再统一锁序；宿主 converter 补严格字段类型的 `decode_request`，Repository 回传 typed intent，应用层退出信封解读。同一独立 reviewer 在一轮内完成旧意见闭环及当前完整 14 文件的 fresh full Review，逐文件指纹一致，无未关闭 finding。后续仅补本文证据，不失效当前可执行快照。未暂存、未提交、未部署，生产入口仍未启用，不把离线成功视为真实取盘或双臂物理验收。

#### 实施切片 6D：确定 WAIT 的持久到期续问

**最小持久进度：**Pickup 只增加 `material_confirmation_id`（当前可靠请求）和 `material_retry_at`（当前 WAIT 的 `Confirmation.completed_at + retry_after_ms`）。不复制 WMS 响应或来源授权，不新增模型、业务状态、定时 Evidence、worker 或配置。首次 decide 与游标登记、WAIT 到期与结果发布、新 Confirmation 与游标切换、ACCEPT 与 Work 授权交接分别复用原事务。纯 handler 仍只输出 typed Decision。

**恢复 owner：**已有关联且未 ACCEPT/未 CLOSED 的单盘由插件 material flow 扫描；已有 batch 的 `advance_completed_in_session` 在检查新任务状态和当前 Work 列表前委托，不依赖一次性通知。只有确定结果已由 FactProcessor 发布、到期且原直接成员未取消、原 Work 仍持有本盘消费时，才创建新 operation_id。未发布结果继续归 FactProcessor；当前请求交付未知或待对账继续归原 Confirmation，超时不转为新业务请求。已持久义务不因父任务状态变化丢弃；直接成员取消只停止新业务续问，不虚构持有物料的退出事实。

**身份边界：**续问沿同一 Pickup/MaterialExecution、扫码 Evidence、来源 Command、task/source locator、barcode 原文及 scanned_at，仅更换 operation_id 与新请求信封 timestamp。宿主同域 `MaterialDecideScheduler/ResultReader` 只复用基础可靠创建并严格读取 typed 原请求/结果，核对 owner、operation、Evidence、摘要及封闭响应；插件核对当前游标与原业务事实。旧结果不能回退游标或覆盖 ACCEPT；后续恢复不寻找“最近请求”，不依赖完整历史请求链。当前 MaterialExecution 仍引用的 HOLD 事实属于当前依据，不能当作可清理历史删除。

**存量与迁移：**迁移 `548db61057e6` 仅添加上述两个可空字段及当前请求外键。空游标统一按首次 operation_id=Pickup UUID 初始化，承接首次结果前及存量 6C WAIT；已发布 WAIT 从原 completed_at 恢复到期时间，不重投原 Evidence、不从重启时间重新计时。未发布结果仍留给 FactProcessor，丢失或漂移的精确请求 fail closed。有游标/到期数据时拒绝有损降级，不猜测转换或删除运行进度。

**测试所有权：**`T/integration/test_material_wait_postgresql.py` 是 WAIT 续问业务的唯一主 owner，覆盖并发、各事务崩溃恢复、WAIT→WAIT→ACCEPT、技术未知、旧结果重放、身份漂移、直接成员取消/父任务完成、可清理历史删除及存量 6C 升级；6C helper 补齐真实 Confirmation 完成时间。插件 FAST 只验证已有 batch 委托；核心 FAST 仅验证固定 typed 端口差异，核心 HEAVY 沿域映射及新增迁移映射运行，不接收插件业务测试。

**启用与未决范围：**生产组合根仍不注入来源或 material flow，无生产扫码关联、设备取盘/PUT 或 movement report。G2/G3/G5 分别约束设备扫码关联、映射与可靠事实、权威 PkgID；G6 首批期限不阻塞本片，使用已确认 retry_after_ms；G4/G7 仅约束各自分支。任务 6 完整闭环和任务 7 仍未完成，不做 bin-line-common 抽取。

**验证：**6D 新增 PostgreSQL 22 passed，既有插件事务/真实 worker 回归 72 passed，插件 FAST 85 passed，合计 179 passed、无跳过；含空库升降级、存量 6C WAIT 升级恢复、有进度拒绝降级及模型/迁移零差异。核心 typed 端口/selector/拓扑聚焦 254 passed；完整 QUALITY 通过（脚本 429 passed，默认 FAST 4292 passed/5 既有 skipped）。现行 selector 为 21 文件，固定 PostgreSQL 17.10/TimescaleDB 2.27.1 与独占 Redis 上最终 HEAVY 108 passed、无跳过。首轮 HEAVY 的 49 项 setup error 来自旧 integration fixture 拒绝 postgres 管理库命名；同实例预迁移 test_auto6d 后完整重跑同一 manifest 通过，没有为环境错误修改生产代码或放宽护栏。现有 metadata 环引用、Redis close 弃用及 plan_delta fixture 序列化 warning 保留，不扩面修正。

**闭环 Review：**独立 reviewer 核对固定 14 文件及 SHA-256、合同、事务/游标/恢复 owner、取消锁序、历史边界、测试/helper 与 HEAVY 所有权，无可证实 finding。所有可执行文件保持评审指纹；后续仅补本文验证记录，Ruff/格式及 diff 检查通过。Git HEAD、index 与共享 config 未漂移，无暂存；未提交、未部署，不推进真实取盘。离线成功不代表 G2/G3/G5 或任务 7 已具备生产准入。

#### 实施切片 7A：普通目标已就绪后的 PUT 创建离线事务

**范围：**仅 BIN_CELL 来源、最终 ACCEPT 的普通 RACK_SLOT 目标，且明确的 typed reader 已确认当前盘目标架/面/工作位及直接准备依赖就绪。`P/application/target_preparation.py` 通过现有 `DeviceCommandService` 创建 PUT；reader 和请求映射器均必填，没有生产默认实现。测试的设备、时限和报文只为离线输入，不构成 G2/G3 合同；不应用 PUT 成功为位置，不创建 movement_report、不关闭单盘，ROTATE/REPLACE 留给后续切片。

**最小持久关联：**Pickup 仅新增可空唯一 `put_command_code` 外键，命令正文和技术生命周期只保存在原 DeviceCommand。新命令与引用在 WorkLine→Work→Pickup 锁事务中同时提交/回滚；内部 `WORKLINE_BUSINESS / put:<pickup UUID>` 表示本盘 PUT 步骤，关联已存在的 MaterialExecution，不改变扫码前来源 Command。该内部身份不定义设备附录 O6 的 business_key，也不是 PkgID。迁移 `4e30101440d2` 增列、唯一约束及外键，有 PUT 引用时拒绝有损降级；无授权模型或新业务状态。

**业务与恢复 owner：**已有 batch 的 `advance_completed_in_session` 可选注入 target flow，在当前 Work/任务激活过滤前扫描当前未 CLOSED 的 ACCEPT Pickup；已发布 ACCEPT 是当前待创建目标动作的依据，不重投该 Evidence、不新问 material.decide。创建前核对当前请求、ACCEPT Evidence、WorkLine/单盘/来源及直接成员取消事实；复用现有 physical reader 按本盘冻结来源与原来源 Command 核对可靠取出 Evidence，未知则等待，不借 Work 当前栈顶解释旧盘；父任务终态不代替直接取消。已建 PUT 只按精确引用恢复，不重算目标、映射或 deadline，不因取消、超时或未知状态换身份；技术履约归原 Command 基础服务。本片不消费成功结果，也不借命令状态宣布物理完成或单盘收口。

**并发边界与所有权：**M1 PUT/报告不作为 M2 来源创建的统一门槛；M2 仍由当前 Work 授权及可靠栈顶取出事实准入，未知栈顶不得越过。`T/integration/test_target_put_postgresql.py` 唯一拥有 PUT 创建业务的 PostgreSQL 验收，复用 6B/6C 输入并实际使用 batch 入口、typed material reader 和 DeviceCommandService；不复制基础派发/ACK/技术重试矩阵。核心仅承接迁移/selector/拓扑测试，插件业务测试不进入核心 HEAVY。

**启用边界：**production composition 仍不注入来源、material 或 target flow；G2/G3/G5 继续约束对应生产路径，不猜设备映射、扫码关联或 PkgID。G4/G6/G7 不扩大为本片阻塞；后续成功 PUT 与位置/报告义务的原子联动必须单独完整实施，不以 fixture 将单盘直接标为 CLOSED。

**验证：**最终 PUT PostgreSQL 36 passed，插件既有 PostgreSQL/真实 worker 回归 94 passed，插件 FAST 86 passed，共 216 项通过且无 skipped；核心聚焦 248 passed。最终 QUALITY 通过（核心 FAST 4292 passed / 5 项既有 skipped，不将 skipped 作为通过证据）；selector 仅选中核心工作线配置 PostgreSQL 文件，8 passed。已覆盖命令/引用双侧故障回滚、并发重复创建、重启沿用原引用、旧 ACCEPT 重放、取消与身份漂移、未知取出等待、外来/未应用取出事实拒绝、M2 接续后仍按 M1 精确来源创建、空库/存量迁移、metadata 无差异及有损降级保护。

**闭环 Review：**独立 reviewer 首轮发现创建 PUT 缺少本盘可靠取出事实门禁；先以回归复现后修正，再由同一 reviewer 完成旧意见闭环与当前 10 文件完整复审，无新增 finding。跨工作线测试夹具另修为真实存在的 WorkLine，定向核对确认断言语义及 owner 不变，最终 36 项与 QUALITY 已重新通过。准确 RED 为 4 项真实门禁失败和 1 项无效 FK 夹具失败，后者不计业务失败证据。最终仅补本文验证记录，可执行文件保持评审指纹；未暂存、未提交、未部署，不推进真实取盘。任务 7 整体及 G2/G3/G5 生产准入仍未完成。

#### 实施切片 7B：确定 PUT 位置与报告义务的离线原子应用

**范围：**只接 7A 的 BIN_CELL→普通 RACK_SLOT 原 PUT Command。`P/application/placement_flow.py` 的必填读口显式给出匹配成功事实、实际放置时间和本盘权威 PkgID；没有生产读口，不从 barcode/trace 推导，不定义设备结果报文。插件纯 typed Fact/Handler 通过现有 facade 返回 movement_report intent，宿主原 DecisionApplier 创建 Confirmation；不改 SDK/wire/基础流程或生产 composition，不关闭单盘、不启用真实派发。

**最小持久关联：**Pickup 只增加成对的 `put_result_evidence_id` 与 `movement_confirmation_id`（迁移 `55adfc1a25a7`）。前者指向保存确定放置内容的原成功 Evidence，后者指向冻结完整报告的 Confirmation；不另存目标/PkgID/请求正文，不新增位置账本、授权模型或业务状态。配对约束防止半份关联；已有 7A Pickup 保持空引用，存量按当前 PUT 事实接续。存在已确认引用时拒绝有损降级。

**身份与原子性：**先核对原来源/单盘绑定、PUT 的 WorkLine/MaterialExecution/步骤身份、Command 的精确 result Evidence、SUCCEEDED 与匹配成功读口，再核对当前已发布 ACCEPT 的来源与精确目标。ACK、超时或单独状态值都不能生成位置报告。报告只带合同规定的 RACK_SLOT 架/面/槽位，层列仍属目标设备映射。报告 intent、当前位置/报告引用、Evidence Decision 摘要和发布在 FactProcessor 的同一应用事务中提交/回滚；重放沿相同 operation_id、Evidence 和正文，不查询最近历史。

**恢复 owner：**7A batch 继续发现/关联 PUT；成功结果 Evidence 由 FactProcessor 的既有领取、租约、延期与重领扫描恢复，冻结插件的唯一应用路径负责业务解释。已建报告归 Confirmation 原身份履约，不因父任务终态或直接成员取消丢弃已发生物理事实。未知结果/权威 PkgID 未给定时返回 DeferExecution，原 Evidence 未发布、无新报告；本片收到报告结果也明确延期给后续关闭切片，不加 no-op 成功消费者。Work 的下一来源授权仍独立，不等待 M1 报告。

**验收与所有权：**`T/test_placement_report.py` 拥有纯 SDK 决定，`T/integration/test_placement_report_postgresql.py` 拥有成功 PUT→位置/报告原子应用的 PostgreSQL 验收，复用 6B/6C/7A 输入和真实 FactProcessor/DecisionApplier/Confirmation。覆盖回调先于 ACK、并发、双侧故障回滚、租约到期、通知丢失、重放/身份漂移、缺成功/PkgID、M2 并行、存量迁移及报告结果不提前关闭。核心只承接精确迁移 mapping/selector、schema 与插件所有权；不复制基础派发完整矩阵。

**验证：**以已提交 7A `aac76175` 为基线，新增放置/报告 PostgreSQL 36 passed，既有插件 PostgreSQL/真实 worker 回归 130 passed，插件 FAST 88 passed，共 254 项通过且无 skipped；核心聚焦 257 passed，selector 仅选中工作线配置 PostgreSQL 文件，8 passed。最终 QUALITY 通过（核心 FAST 4292 passed、5 项既有 skipped，不计作通过证据）。metadata 无迁移差异，空/存量升级、成对引用和有损降级保护均已覆盖。首轮 35 passed/1 失败来自测试误把“处理并识别摘要冲突”的计数当成失败计数；依据现有内核行为修正计数，并补强原报告不变与 Evidence/MaterialExecution 进入既有 RECONCILING 的断言，最终 36 项已完整重跑通过。

**闭环 Review：**独立 reviewer 核对固定 13 文件及指纹、合同、精确身份、原子事务、恢复 owner、取消后事实、并发和测试/HEAVY 所有权，完整 Review 无可确认 finding。后续仅补本文验证记录，可执行文件保持评审指纹。HEAD、index 与共享 Git config 未漂移，无暂存；G2/G3/G5 仍只约束对应生产路径，fixture 的成功事实和权威 PkgID 不构成合同确认。未提交、未部署，不推进真实取盘，任务 7 整体和单盘关闭仍未完成。

#### 实施切片 7C：报告确认后的单盘关闭与 Work 当前事实续接

**范围：**只消费 7B 已冻结的 BIN_CELL→普通 RACK_SLOT 报告。宿主固定 `MaterialMovementReportResultReader` 按当前 Confirmation ID 校验 owner、operation/operation_id、请求摘要、响应 Evidence 的身份/摘要及 `COMPLETED + RECORDED/DUPLICATE`，返回纯 typed intent/outcome；不读取插件业务表、不决定关闭资格。插件 `MaterialReportedFact` / handler 使用现有 CompleteExecution，不改 wire/SDK，不新增模型、业务状态或迁移。

**关闭与原子性：**插件核对本盘当前报告、已发布 ACCEPT、已发布的精确成功 PUT 位置引用，以及报告冻结的原来源/普通目标。报告结果 Fact 的 operation_id、Evidence、typed outcome 必须对应同一当前义务。ACCEPT 已在 6B/6C 原事务交接下一来源或明确无下一来源，关闭时不再次交接；M2 无需已存在。若 Work 仍持有本盘 ACCEPT 且尚未消费，插件从既有可靠取出读口核对原来源 Command 的取出 Evidence，与 CLOSED、报告结果摘要和发布同一 FactProcessor 应用事务保存到 Work.top_removed_evidence_id；读口在准备阶段只读，不能提前提交该事实。缺可靠取出则由原 Evidence 延期恢复。若授权已消费或已推进，保留当前 Work 的游标与取出事实，不要求 M2 的取出/目标就绪，不回写前盘事实。直接成员取消或下一来源暂不可用不丢弃本盘已经确定的位置和报告事实；新来源仍遵守原准入。

**恢复 owner 与历史边界：**未完成报告义务归原 Confirmation；已保存未发布结果归原 FactProcessor 领取/租约/延期扫描。关闭前尚未移交的取出依赖归仍未闭合的 M1，关闭提交后归当前 Work；现有 batch 的 Work 扫描负责发现下一来源，不增加 worker/队列或关闭 Work。清理已闭合 M1 的 Pickup、原来源/PUT Command、已履约 Confirmation 及无当前引用的 PUT 历史后，Work 依靠仍被引用的当前 ACCEPT 和取出 Evidence 独立续接；这些当前事实及必要 owner 不是可清理历史。无下一来源由当前 ACCEPT 的省略字段明确表达，不以空查询推定完成。关闭 M1 不等同于 Work、Bin 或 PickingTask 完成。

**验收与所有权：**`T/integration/test_material_close_postgresql.py` 是报告确认→单盘关闭、Work 原子保存和跨历史恢复的主要 PostgreSQL owner，承接 7B 原延期关闭的两项测试；7B 文件继续拥有 PUT→报告创建。纯决定复用 `T/test_placement_report.py`，组合测试核对静态 handler。核心 FAST 拥有 typed decoder / reader 严格合同，核心 movement_report production wiring 验证无插件时 MaterialExecution owner 的可靠履约与 typed 读取；不复制插件业务矩阵。新读口由现有出库域 HEAVY mapping 覆盖，selector 合同新增该路径断言。

**启用与未决范围：**生产组合根仍不注入 material/来源/目标流程，不启用真实派发、不推进真实取盘。7C 读取已冻结的 7B 报告，不重新索取设备映射、扫码关联或 PkgID；G2/G3/G5 仍分别限制对应生产入口/事实读口和新报告身份的权威来源。普通目标以外的准备、RACK_SLOT/NG 分支及任务 7 其余路径仍未完成，未决值不猜，不做 bin-line-common 抽取。

**验证：**基线为已提交 7B `c1394d0a`。新增关闭 PostgreSQL 33 passed，既有插件 PostgreSQL/真实 worker 164 passed，插件 FAST 90 passed，合计 287 项通过、零 skipped；核心聚焦 305 passed。最终 selected HEAVY 为现有 mapping 选中的 20 个宿主文件，102 passed、零 skipped；最终 QUALITY 通过（核心 FAST 4314 passed、5 项既有 skipped，不计作通过证据）。7C 无新迁移；插件既有 schema/迁移回归和独占临时库的现有 head 升级均通过。

**失败闭环与 Review：**首轮宿主 HEAVY 因手动运行遗漏隔离 base 测试库迁移而有 49 项失败/初始化错误，迁移后定向 49 passed；新增 MaterialExecution owner 夹具另补必填设备标识及局部外键清理，完整业务断言保持不变，定向真实 worker 4 passed 后重跑完整 20 文件集合通过。独立 reviewer 核对固定 18 文件、typed 身份、原子关闭/发布、恢复 owner、历史清理、授权推进和测试/HEAVY 所有权，完整 Review 无可证实 finding；其后只变更核心夹具文件，同一 reviewer 定向核对确认能精确还原首轮指纹、无断言弱化或 owner 变化。生产可执行文件保持评审指纹，最终仅补本文证据。HEAD 不变、暂存区为空，保留工作区改动；本轮不提交、不部署、不启用真实派发或真实取盘，任务 7 整体仍未完成。

#### 实施切片 8A：NO_WORK 点2放行与首次 SCAN3 持久交接

**范围：**仅接当前 Work 的确定 NO_WORK，不接逐盘正常结束。`return_flow.py` 通过必填的离线命令 mapper 和精确 SCAN3 reader 使用现有 DeviceCommandService/FactProcessor；生产组合根未注入该 flow，不猜点2设备参数、期限、SCAN3 编码或关联规则，不启用真实派发。

**持久身份与原子性：**Work 仅增加唯一 `exit_command_code`，已有 EXIT_PENDING 保持唯一前置恢复依据。新 NO_WORK 的应用、点2 Command 创建/关联和 Evidence 发布同事务；存量已发布 EXIT_PENDING 由 batch 前置扫描补建，沿 Work/NO_WORK 冻结命令身份。已有命令恢复不重新计算正文或 deadline；明确 FAILED 转既有 RECONCILING，未知/超时沿原 Command 由基础机制履约，不创建替代动作。命令 ACK/成功不代替 SCAN3 到位，精确到位也不伪造命令终态。

**首次交接：**按当前 Work、原点2 Command、工作线、料箱、扫码设备、事件时间及当前 Evidence 核对显式关联。缺精确关联由原 Evidence 延期；错误身份进入既有对账。首次有效 SCAN3 在同一 FactProcessor 应用事务创建 `AutomaticPickingReturn`、关闭 Work 并发布该事实；重复扫码保留首次 Evidence。回程根仅保存工作线、料箱、原 Work 的不可变因果标识、原 Command 和首次 SCAN3 Evidence，不新增授权模型或状态，不引用已闭合 Work 外键。迁移 `bb07c44fb54f` 保留存量 EXIT_PENDING，拒绝丢失已建立的命令/回程身份的降级。

**恢复 owner 与截点：**SCAN3 前当前 Work/EXIT_PENDING 归既有 batch 前置扫描，且不要求父任务仍 EXECUTING；已建命令的派发/结果归基础 Command，未发布扫码事实归 FactProcessor 的领取、租约与延期恢复。交接后 Return 是后续回程的当前持久根，计入原 business blocker/archiver；重启及旧扫码重放不再读取已闭合 Work、WorkPlan Confirmation 或旧 SCAN2/NO_WORK 历史。下一片由既有 batch 接续该根；本片停在等待后续物理事实的离线交接点，不增加空成功消费者或假回架动作。

**测试所有权：**`T/test_bin_release.py` 拥有精确关联输入边界，`T/integration/test_returns_postgresql.py` 拥有 NO_WORK→点2命令→首次 SCAN3 原子交接、存量恢复、并发、双侧崩溃/发布、租约、重放、无 EXECUTING、回调早于 ACK、清理已闭合历史及新轮次隔离。核心只承接显式模型注册/过滤、迁移 selector 和既有 schema/工作线配置 HEAVY，不导入插件业务测试。SCAN4 FIFO、return_batch、drain 与逐盘正常结束入口留在后续切片，G2/G3/G5 继续仅约束各自生产路径。

**验证：**以已提交 7C `54b7ba6e` 为基线，8A PostgreSQL 33 passed、既有插件 PostgreSQL/真实本地 worker 回归 197 passed、插件 FAST 98 passed，合计 328 项通过且零 skipped。核心聚焦 255 passed，selector 仅选中 schema baseline 与工作线配置两个文件，HEAVY 18 passed、零 skipped；最终 QUALITY 通过（核心 FAST 4315 passed、5 项既有 skipped，不计作通过证据）。独占临时 PostgreSQL 完成干净迁移链、存量 EXIT_PENDING 升级、空引用降级/再升级及已建立命令/回程时的有损降级保护；Work/Return metadata 对比无差异。初次失败属于夹具终态/投料前提、漏载 metadata 及 FactProcessor 处理计数误用，修正后保留完整身份、状态、发布与回滚断言，最终 33 项统一重跑通过。

**闭环 Review：**独立 reviewer 核对固定 15 文件及 SHA256，完整检查精确身份、事务/锁序、原命令 owner、无 EXECUTING 的 batch 前置恢复、闭合历史清理、新轮次隔离、安装/迁移及测试所有权，无可确认 P1/P2 finding。后续仅补本文验证记录，可执行快照未变化。HEAD、暂存区、Git 配置与 worktree 判定未漂移，未提交、未部署、不启用真实派发或真实取盘；任务 8 整体及后续回程入口仍未完成。

#### 实施切片 8B：首次 SCAN4 事实、FIFO 投影与当前 Return 发现

**范围：**仅在精确关联显式给定的离线前提下接续 8A。SCAN4 reader 返回既有 Return 的本地身份、工作线、箱码、事件设备与时间；不新增设备 wire 字段，不猜扫码格式或沿用人工线/SCAN1–2 后缀。生产组合根未注入该 reader。业务仍在独立插件，不改 SDK、WMS DTO、基础 FactProcessor/Command 机制或抽取 bin-line-common。

**最小持久化与原子性：**现有 Return 只增加成对的 `scan4_evidence_id` 和 `scan4_event_time`，以唯一 Evidence 引用、完整配对约束及当前工作线/事件时间索引承接首次事实和 FIFO 运行投影；迁移 `d028d7c65eb7` 保留存量 8A 的空引用，不从 SCAN3、接收时间或历史命令回填。首次关联、时间投影与 Evidence 发布共用 FactProcessor 应用事务；并发只冻结一次，重扫及旧结果重放保留首次值。同一 Evidence 已归属另一 Return 时明确对账，不靠数据库唯一冲突重试。已有 SCAN4 事实时拒绝有损降级。

**FIFO 与资格分离：**使用获批的 SCAN4 同源事件时间，按当前已绑定的实际回程通道跨任务读取；不假定 SCAN3 与 SCAN4 设备时钟可比较，不使用 received_at、Return ID、条码或 source_event_id 作为物理排序键。仅已有 SCAN4 的当前 Return 进入观察队列；重扫不重排。不同 Return 的相同事件时间保留双方首次事实并沿既有 Evidence 对账，不能编造平局顺序。FIFO 查询对顺序冲突、首次事实未发布或身份/摘要/时间投影不一致返回不可判定；空列表仅表示无已观察入队记录，不表示物理 EMPTY 或 NO_BATCH。

**恢复 owner 与截点：**未发布 SCAN4 Evidence 归原 FactProcessor 领取、租约与延期扫描；缺精确读口延期保留原 Evidence，确定身份错误按既有对账边界留证。既有 batch 前置钩子直接发现当前 Return，独立于 Feed/未闭合 Work 和 EXECUTING 任务；发现是只读查询，不计作业务推进。当前根、FIFO 事实和原命令均保留给下一片，既有 business blocker/archiver 继续有效；清理可清理的前段 Work、Feed、已履约请求及旧 Evidence 后仍可发现、入队和重放。当前引用的必要事实及未闭合 Command 不是可清理历史。回程后继归 batch，既有命令仍归基础 owner；本片不创建方向命令、不赋予回架 READY、不消费 FIFO、不接 return_batch 或 drain，不增加队列表、业务状态、定时器或成功占位消费者。

**验收与所有权：**插件 `T/test_return_fifo.py` 拥有精确输入类型边界；`T/integration/test_return_fifo_postgresql.py` 拥有首次事实/投影/发布、并发、重放、前置 batch 接线、无 EXECUTING、存量升级与历史清理的 PostgreSQL 验收。8A 的原 `test_returns_postgresql.py` 继续拥有前段交接；核心只承接精确迁移 selector、模型/schema 和测试所有权边界。G2/G3/G5 继续限制对应逐盘生产路径，点3/4命令及生产扫码关联另按其实际合同闭合，不扩大阻塞本片离线事实与排序。

**验证与闭环 Review：**本片实现与验证完成。新增 PostgreSQL 验收 22 passed、8A 回归 33 passed、插件 FAST 107 passed；核心 selector/测试所有权 249 passed、选中宿主 HEAVY 8 passed。QUALITY 通过（4315 passed、5 个既有 skipped，跳过项不计通过）。迁移验证覆盖存量 8A 空配对升级、空事实降级后重升、已有 SCAN4 事实时拒绝有损降级，以及模型/数据库一致性；以上证据绑定本轮最终可执行快照。

独立只读 full Review 已核对 11 文件冻结清单、直接/间接测试所有权和恢复调用链，未发现需要修复的 P1/P2 问题。当前 Return 发现已接入无 EXECUTING 任务也可到达的 batch 前置入口，但查询结果尚不用于后继业务推进；生产精确关联 reader 与组合根装配、方向命令、READY、FIFO 消费、`return_batch` 和 drain 仍留待后续切片。本轮不提交、不部署、不启用真实派发或真实取盘。任务 8 整体仍未完成。


#### 实施切片 8C：方向命令、成功结果与回架候选资格

**范围与前提：**在 8B 当前 Return 上，精确关联、完整方向请求和权威后置条件由离线 reader/mapper 显式给定；不定义 SCAN3/SCAN4 生产条码/设备参数规则。正常方向采用既有 `MOVE_FORWARD` Command，源位置、参数和期限均由给定映射完整提供，并由现有 DeviceCommandService 校验冻结 binding。生产组合根仍不注入回程流程，不接 `return_batch`、FIFO 消费或 drain。

**最小持久化与原子性：**当前 Return 仅增加 SCAN3/SCAN4 Command 引用和 `ready_evidence_id`，不新增队列表或业务状态；迁移 `71246b611dbf` 保留存量 8B 的事实与空方向引用，已有方向义务或资格事实时拒绝有损降级。batch 同锁创建必要方向命令并关联 Return；SCAN4 首次事实已给定时不补发已失去必要性的 SCAN3 动作，已有前段命令仍沿原身份履约。设备失败或未知不自动创建新命令身份。

**资格与顺序：**首次 SCAN4 仍只冻结物理入队顺序。精确匹配本 Return、本点原 Command 的权威 `SUCCESS`、宿主已应用终态和显式 OUTLET 后置条件后，资格依据与 Evidence 发布由 FactProcessor 同事务落地。SCAN3 成功不授予回架资格，ACK 不替代成功；重扫、重放不改首次时间。此处候选资格不等于 WMS `return_batch READY` 的精确分配。原始结果摘要复用宿主 `UNIFORM_WIRE` 规范化，不复制可靠接收/派发机制。

**恢复 owner：**现有 batch 前置钩子独立发现当前 Return 并创建缺失的必要方向动作，不依赖 EXECUTING、已闭合 Work 或一次性通知；停用线保留当前根，不新建方向动作。已建 Command 的交付、技术重试、回调接收与终态归设备基础服务。未发布结果唯一归 FactProcessor 的持久 claim/租约/延期扫描；缺 reader 延期，确定错配/失败留证对账，不授予资格。当前 Return、必要首次事实与原 Command 引用保留；可清理前段历史不参与恢复。

**测试所有权与截点：**插件 `T/test_return_directions.py` 拥有 typed 关联输入边界，`T/integration/test_return_directions_postgresql.py` 拥有方向创建/关联与资格/发布原子性、并发、崩溃、重放、回调早于 ACK、无 EXECUTING、存量 8B 及历史清理后的恢复；8A/8B 的测试继续拥有其原行为。核心仅增加精确迁移 selector fixture/mapping 并验收 schema。生产精确关联、方向映射和成功后置条件仍须其实际合同闭合；G2/G3/G5 的取盘/报告未决项不扩大阻塞正常 NO_WORK 离线回程。

**验证与闭环 Review：**本片实现与验证完成。新增方向 PostgreSQL 32 passed、8A/8B 回归 55 passed、插件 FAST 115 passed；核心 selector/测试所有权 249 passed、选中宿主 HEAVY 8 passed。QUALITY 通过（4315 passed、5 个既有 skipped，跳过项不计通过）。迁移验收覆盖存量 8B 事实保持、空引用降级后重升、已有方向义务时拒绝有损降级及模型/schema 一致性；插件与选中 HEAVY 无跳过项。独立只读 full Review 核对 11 文件冻结 SHA、实际恢复调用链和测试所有权，未发现确认的 P1/P2/P3 问题。最终可执行快照保持评审指纹，后续仅补本文验证记录。

下一片再接 FIFO 合格连续前缀、冻结 `return_batch` 请求/决定和 BIN_MOVE 交接，不能把本片候选资格当作 WMS 精确目标分配；同架面下一轮分配及运动前门禁仍按主合同另行验收。任务 8 整体仍未完成；生产 reader/mapper 未启用，本轮不提交、不部署、不启用真实派发或真实取盘。


#### 实施切片 8D：冻结回架前缀、分配结果与 BIN_MOVE 交接

**范围：**在 8C 已形成候选资格的前提下，精确 HANDOFF_POSITION、当前实际 rack/face 与到位 Evidence、权威运输结果均由离线 typed reader 显式给定。只接正常 FIFO 前缀、`return_batch` 与原 BIN_MOVE 的可靠业务关联，不启用生产 reader/mapper、实际派发或取盘，不接 drain 或完整换面/离场入口。

**最小当前进度：**增加插件私有 `AutomaticPickingReturnAllocation`，仅保存当前到位/架面、原 operation、请求时有序 Return execution ID、结果引用/选中数量、原 Transport 及交接/收口引用。完整冻结请求和响应仍只归 Confirmation/Evidence，动作正文归 Transport。Return 只增加当前 allocation 引用；ACCEPTED 交接同事务清除该引用并关闭 Return，allocation 保留标量成员身份与原 Transport，不以已关闭 Return 的 FK 或旧 Work/Feed 恢复后段。迁移 `a448148f9636` 保留存量 8C 的空分配引用，有分配进度时拒绝有损降级；部署只增加模型注册，不装配生产业务流程。

**FIFO 与原子性：**按首次 SCAN4 时间从当前待派发物理队首读取，只取资格已应用发布、精确位置已知的连续前缀，最多 4 箱；未知/未就绪项截断，不先过滤 READY 再越过它。已由原请求领取的成员不重复进入待派发范围，不能据此宣称物理 EMPTY。当前进度、成员领取与原 Confirmation 创建同事务；请求内顺序号从 1 连续冻结，重提不换身份或正文，新成员不追加到旧请求。结果由既有 typed reader 按原义务校验，READY/NO_BATCH 的分配关联、未选成员释放及发布同事务；NO_BATCH 结束本次到位决定，不按其 retry_after_ms 在同一次物理到位轮询。

同一次实际到位可在前一 READY 批次满足终态门禁后继续处理 FIFO 尾部，新请求使用新 operation；不能把 NO_BATCH 的到位结束限制扩大到 READY。每轮仅以 operation 唯一，在既有 WorkLine 锁下串行检查当前进度；下一轮请求、成员领取及已交接成功旧进度的归档同事务，不依赖已归档的分配历史。机会式/运动前服务阶段的结束条件仍由后续阶段入口负责，本片不增加阶段状态或入口。

**BIN_MOVE 与门禁：**READY 已应用发布、原实际架面仍匹配且原源位置确认后，复用 ReliableBinTransportCreator 创建和关联单次 BIN_MOVE；冻结目标与其他活跃 Bin 搬运的冲突检查使用既有目标事务锁身份，不建立储位主账或自行改址。Return 按原 Transport 的明确 ACCEPTED（或已证实的成功终态）交接，不伪造物理终位。同一 WorkLine/rack/face 下一轮请求仍等原 BIN_MOVE 的权威终态在宿主及插件发布、最终位置已知且成功终位匹配冻结目标；明确未接纳的 REJECTED 只解除该轮分配等待，不伪造 Return 交接或业务重试。未知/失败恢复保留原身份和直接依赖，未确认的异常业务决定不猜。独立已知架面可继续，另一个架面事实未发布不构成全线围栏。

**唯一恢复 owner：**当前分配和必要后继归原 batch 前置入口，无 EXECUTING、无新事件仍扫描；原 WMS 请求的收发与技术恢复归 Confirmation；WMS/Transport 结果的业务应用及发布归 FactProcessor；原 BIN_MOVE 履约、物理结果及位置投影归 Transport。自动插件结果发布器只新增固定回程分支并校验原 WorkLine binding，不把跨任务回程挂回某个已结束的 PickingTask。已闭合 Return 清理后，allocation 仍独立承接同面等待。已交接成功的旧进度可随同到位下一轮请求原子归档，或在已收口且权威读口确认到位轮次已替换/结束后退出扫描；NO_BATCH 当前到位结束标记只走后一出口。未知读口不退役该标记，未发布的独立架面只限制其本身。

**所有权与后续：**插件 `T/test_return_allocation.py` 验证输入边界，`T/integration/test_return_allocation_postgresql.py` 拥有前缀、冻结/应用/搬运/交接原子性、并发、重放、同面门禁与历史清理验收。8A–8C 测试继续承接已有行为，核心仅验收部署模型隔离、schema、selector/测试拓扑。G2/G3/G5 仍只限制对应取盘、扫码和报告生产路径；当前回程的生产关联、位置及架面读口另外闭合，fixture 不成为映射合同。后续补运动前无候选/原义务分支及完整换面离场、正常逐盘结束入口，再进入任务 9 drain。

**验证与闭环 Review：**本片实现及验收完成。最终新增 PostgreSQL 35 passed、8A–8C 回归 87 passed、插件 FAST 126 passed；核心聚焦 256 passed、选中宿主 HEAVY 18 passed。QUALITY 通过（4316 passed、5 个既有 skipped，跳过项不计通过）。迁移验收覆盖存量 8C 保持、空进度降级后重升、有分配进度时拒绝有损降级，以及模型/schema 一致性；插件与选中 HEAVY 无跳过项。独立只读 full Review 发现同到位 READY 尾部被错误永久阻断的 P2，新增两个 PostgreSQL 反例先失败后通过；删除过宽到位唯一约束，在同一 WorkLine 锁及事务下创建下一轮并归档已交接成功旧进度。原 Reviewer 合并完成旧意见闭环与当前 fresh full Review，计划退役出口文字也同步闭合，无剩余确认的 P1/P2/P3 问题。受影响完整验收及门禁已刷新，证据绑定最终可执行快照；后续仅补本文验证记录。本轮不提交、不部署、不启用真实派发或真实取盘。任务 8 整体仍未完成。

#### 实施切片 8E：运动前求值的持久阶段与门禁

**范围：**复用 8D 的精确位置/实际架面读口、typed 结果应用与原 BIN_MOVE 恢复。运动前业务依据由离线 `MatchedReturnMotion` 显式给定，复用 `MatchedReturnFace`；不从扫码、任务状态或候选空查询推断运动意图，不创建旋转/离场 Transport，不接 drain、逐盘结束或生产 reader/mapper。

**最小当前进度：**插件私有 `AutomaticPickingReturnMotion` 仅保存 WorkLine、本次实际架面到位 Evidence、业务依据 Evidence、架/面、原 allocation execution 标量 ID、求值结束及归档时间；唯一到位/架面阶段只限制运动前服务，不给 8D 的所有 READY 加回到位唯一约束。无新授权模型、业务状态枚举或 timer。待处理阶段的原分配是当前依赖；阶段结束后仅凭本阶段事实抑制重开，不读取已关闭 allocation、Return、Work、Feed 或 Confirmation。迁移 `b1692d740313` 基于 `a448148f9636`，有阶段/出口事实时拒绝有损降级。

**先恢复后判断：**batch 原入口先推进 8D 可靠义务，再发现/创建当前运动前阶段。原请求未知、结果未应用/未发布、READY 未创建 BIN_MOVE、仅 ACCEPTED 或终位未知均保留等待；本架其他当前直接回架义务也须闭合。既有 NO_BATCH 必须已应用发布才可消费，不能跳过其他直接义务。READY 沿原 Transport 权威终态及已知成功目标收口；明确未接纳的 REJECTED 在结果发布后仅结束该等待，不关闭 Return、不伪造位置或新增业务重试。

**一次求值：**未产生本阶段请求时才在原义务检查之后读取当前 FIFO。顺序/资格/位置未知与真正无当前可派发候选分别返回；后者只结束求值，不声明物理 EMPTY。新请求、成员领取、Confirmation 与阶段关联同事务。阶段自己的 READY 所选成员及直接义务闭合即结束，未选尾部或后来候选不延长本阶段；此前独立批次完成后仍可进入尚未求值的运动前阶段。无运动前结束标记的 8D 正常路径保留同到位合法下一批能力。未知架面、空列表或其他独立架的事实不退役结束标记，确认同架新的权威到位后旧标记才退出当前扫描。

**恢复与测试所有权：**运动前阶段归同一个 batch 前置入口，缺新事件、缺 EXECUTING 或重启后未再次提供运动输入也能按当前记录恢复；结果仍归 FactProcessor，原请求/动作仍归 Confirmation/Transport。Work blocker/archiver 纳入未结束阶段；部署只注册模型，不启用生产业务。`T/test_return_motion.py` 拥有 typed 输入边界，`T/integration/test_return_before_motion_postgresql.py` 拥有阶段、原义务优先、并发/崩溃/重放及清理恢复；8D 与 8A–8C 继续承接原行为，核心只证明模型部署、schema 与 selector/测试拓扑。G2/G3/G5 及其他未决项仍只限制各自生产分支。

**验证与闭环 Review：**本片实现与离线验收完成。新增 PostgreSQL 26 passed、8D 回归 35 passed、8A–8C 回归 87 passed、插件 FAST 133 passed；核心聚焦 257 passed、选中宿主 HEAVY 18 passed。QUALITY 通过（4317 passed、5 个既有 skipped，跳过项不计通过）；中途终端脚本用例遇到一次系统 setpgid 权限瞬时错误，单项复查及随后完整 QUALITY 均通过，未修改该脚本或断言。迁移覆盖存量 8D 保持、空阶段降级后重升、有阶段事实时拒绝有损降级及模型/schema 一致性。独立只读 full Review 发现缺失本架事实也会退役结束标记的 P2，新增空列表/仅其他架两个 PostgreSQL 反例先失败，最小修复为同架新到位才退役；原 Reviewer 合并完成旧意见闭环与当前 fresh full Review，无剩余确认的 P1/P2/P3。格式化后的退役定向回归 3 passed；插件与选中 HEAVY 无跳过项。最终可执行快照与评审指纹一致，后续仅补本文验证记录。不提交、不部署、不启用真实派发或真实取盘。任务 8 整体仍未完成，实际旋转/离场、原进场轮次的运动绑定及机会式入口留在后续切片。

#### 实施切片 8F：原进场轮次与来源结束绑定

**范围：**仅在显式给定原进场 client identity、来源 Feed 和服务结束 Evidence 的离线前提下，把 8E 阶段绑定到来源成员及原进场轮次。不创建 CTU02/CTU03，不接 drain、机会式入口或生产 reader，不新增运动授权模型或业务状态。

**绑定与身份：**现有阶段补原进场 client identity、原轮次起点/因果 token、原 Feed 标量和确定服务结束 Evidence。原 RACK 进场执行、工作位、架/面及成员必须匹配；最终空清单沿原 inbound_batch 结果，非空清单只接受末段已发布、明确成功且到达绑定 HANDOFF_POSITION 的原结果。跨 revision 保留成员独立，旧结束事实不能满足新 Feed；原 Feed 冻结请求不得早于本轮已精确关联的权威到位依据；动作创建时间不足以替代到位，已知失败且位置明确的原动作也不覆盖后续成功换面的当前事实。当前到位本身为原进场、原窗口尚未释放，或换面 binding 的直接来源精确指向已绑定该原轮次的阶段服务结束事实，才具备正向轮次依据；不能用中间历史缺失推断。已冻结阶段重放不得换绑。存量 8E 行不猜轮次，在显式精确输入下同事务补绑定；尚未明确结束的未绑定当前记录继续列入未闭合统计。

**范围与接管：**同轮换面不重置起点，窗口释放不等于新轮次。先恢复所有原可靠义务，再按本轮范围检查本架各面的直接回架与实际 RACK/BIN 动作；更早轮次不成为新轮次围栏。同架在途、未知、未发布及其它工作线已提交未 ACK 动作不能跳过；计划、reservation 和不再承接当前事实的已闭合历史不构成接管。采用现有对象 authority 锁与 WorkLine 事务边界。明确本线同架新到位后，尚未创建请求的旧阶段可退役；跨工作线接管则必须由更新因果的当前 Projection、精确成功且已发布的 Transport 与 Evidence 共同证明，已绑定旧阶段关闭并保留结束标记，未绑定存量阶段关闭后归档。旧架面重放及清理归档行后均先核对当前到位，不能重建旧请求；已有请求仍先由原 owner 收口。

**恢复与清理：**仍归现有 batch 前置入口，FactProcessor/Confirmation/Transport 所有权不变。已绑定阶段以当前锚点与确定事实恢复，不重新读取已清理 Feed、Confirmation 或原进场历史；当前到位及服务结束事实仍是本阶段当前依赖。8E 出口只结束求值，不产生运动许可，后续真正创建动作时仍需同事务重新校验当前事实及接管。

**测试与迁移：**8F PostgreSQL 拥有原轮次/服务结束绑定、并发/崩溃/重放、窗口释放及清理恢复；8E 保留原行为断言，只将夹具补为真实原进场和 Feed 事实；8D/8A–8C 承接既有行为。迁移 `20d588a3a234` 基于 `b1692d740313`，保留未绑定存量行，有绑定事实时拒绝有损降级。G2/G3/G5/G6 等未决项不由夹具补造。

**验证与闭环 Review：**本片离线实现与验收完成。最终 PostgreSQL 63 passed（8F 37、8E 26），8D 35、8A–8C 87 及插件 FAST 150 保持通过；新增迁移的存量保留、全空/全有约束、降级重升及有绑定时拒绝有损降级均已覆盖。QUALITY 通过（4317 passed、5 个既有 skipped，跳过项不计通过），选中宿主 HEAVY 8 passed，格式和差异检查通过。独立 Review 确认的三个 P2（同面旧轮次围栏、完整旧 Feed/end 误绑定、跨线接管旧阶段无出口）均先补失败反例后最小修复；另覆盖 root 早于旧请求、清理中间进场历史、原进场已知失败后成功换面以及存量阶段归档清理后旧输入重放。原 Reviewer 合并完成闭环与当前 fresh full Review，无剩余确认的 P1/P2/P3。最终可执行快照匹配评审指纹，后续仅补本文验证记录；主工作区 61 个既有改动、HEAD、暂存区及 Git 配置未漂移。不提交、不部署、不启用真实派发或真实取盘。任务 8 整体仍未完成，CTU02/CTU03、机会式入口、生产 reader 与 drain 留待后续明确切片，G2/G3/G5/G6 未决项保留。

#### 实施切片 8G：普通来源架 CTU02 与下一面交接

**范围：**在精确到位、下一服务成员/架面、完整 `TransportRackPosition` 和权威结果显式给定的离线前提下，复用 8F 原轮次与 8E 已结束阶段，为普通来源架创建并关联唯一 CTU02。部署组合不安装生产 reader，不启用真实派发；不接 CTU03、drain 或机会式入口，不新增授权模型、业务表或业务状态，不抽取 bin-line-common。

**创建与结果：**调用方沿既有 WorkLine authority 事务，重验本架当前到位、原来源服务结束、本轮直接回架/BIN/RACK 义务及下一有效成员，沿原阶段唯一创建 Transport 并关联；窗口释放不重置轮次，未 ACK、未知或未发布义务不允许换身份。复用 ReliableRackTransportCreator 的 CTU02 与完整冻结正文；插件静态发布步骤接入现有 typed TransportOutcome、FactProcessor，不重建 HTTP/回调/outbox。回调早于 ACK 仍先保存原结果，再由 batch 核对匹配成功、当前权威位置与目标面，不能将 ACCEPTED 或数据库状态视为换面完成。

**持久交接：**现有阶段增加原旋转 Task 标量及结果 Evidence；下一面 Feed 同事务保存原进场 identity、起点/token、精确本次到位与成员独立 inbound_batch 请求，再归档上一阶段。下一次 8F 绑定从当前 Feed 接管，允许清理已闭合阶段、原 Feed/Confirmation/原进场 Transport，不回读已闭合历史。通用 batch 在原运动交接前不能旁路建 Feed，合法同面后续 revision 从当前 Feed/阶段承接同轮锚点。同架更新因果且明确成功的权威到位可终止旧阶段；计划、reservation 和未知结果不能构成接管。

**恢复 owner：**创建、原动作恢复和下一面事务交接归同一 batch 前置扫描，位于 EXECUTING 准入前；没有新输入或没有 EXECUTING 也继续接收原结果。物理原结果归 Transport，插件应用/发布归 FactProcessor，冻结请求归 Confirmation，下一面 Feed 归既有 batch。未知、未发布或不匹配结果保留原动作和当前阶段，停止受影响交接。旧结果重放不重开已交接阶段。

**测试与迁移：**插件 `test_source_rack_motion.py` 拥有显式 typed 输入；`integration/test_source_rack_motion_postgresql.py` 拥有唯一创建、原子关联、真实本地结果发布、并发/崩溃/重放、回调早于 ACK、跨 revision、窗口释放、同架接管、历史清理与下一面恢复。8F/8E、8D 及 8A–8C 继续证明各自原行为；核心仅证明模型部署/schema、selector 和测试所有权。迁移 `d061a18adea9` 基于 `20d588a3a234`，存量新增列保持空，有运动/交接锚点时拒绝有损降级。G2/G3/G5/G6、设备映射、扫码关联与 PkgID 未决项不由夹具补造，仍只阻塞各自真实分支。

**验证与闭环 Review：**本片离线实现与验收完成。最终 PostgreSQL 100 passed（8G 37、8F 37、8E 26），8D 35、8A–8C 87、直接受影响的 Feed/来源 revision 16 及插件 FAST 158 保持通过；核心 selector/测试拓扑 249、选中宿主 HEAVY 8 passed，最终 QUALITY 4317 passed、5 个既有 skipped（跳过项不计通过），格式与差异检查通过。迁移覆盖存量 8F 保留、完整锚点约束、降级重升、有运动时拒绝有损降级及模型/schema 一致性。真实本地发布路径发现 8F 额外要求 typed outcome 不含的 `kind`，最小修正为校验原 Transport 类型，保留原执行、摘要、成员及位置校验；另补通用 batch 交接前旁路门禁及当前轮次承接。最终专用 PostgreSQL 环境不配置 Redis；扩展执行的 3 个既有真实 worker 用例因缺 `INTEGRATION_REDIS_URL` 未完成，未改实现或断言、未计通过，直接影响集合独立重跑 16 passed、3 deselected；本片没有 worker/队列 wiring 变更。独立只读 full Review 核对 16 文件固定 SHA，无确认的 P1/P2/P3；对象锁疑点沿 `current_source → current_arrival` 复核后撤回，无须重复加锁或扩展模型。最终可执行快照与评审指纹一致，后续仅补本段验证记录。主工作区 61 个既有改动、HEAD、暂存区、依赖与 Git 配置保持不变；不提交、不部署、不启用真实派发或真实取盘。任务 8 整体仍未完成，CTU03、机会式入口、生产 reader 与 drain 留待后续切片，G2/G3/G5/G6 未决项继续保留。

#### 实施切片 8H：普通来源架 CTU03 的 READY 消费与离场交接

**范围：**仅在精确到位、原进场轮次、当前来源服务结束依据和已发布的原 departure READY 显式给定时，创建并接续普通来源架 CTU03。来源/drain 的 departure wire 是 WorkLine-owned、`task_id=null`；本片只实现普通来源架，不创建首次 departure 请求、不续问 WAIT 或执行拒绝后的业务重决策，不接 drain、生产 reader、真实派发或取盘，不新增授权模型、业务状态或 bin-line-common。

**身份与原子性：**沿既有阶段冻结原 departure operation、Transport 标量和结果 Evidence。原请求/响应须精确匹配 WorkLine、rack、当前物理 location/face、原 READY Evidence 和冻结目的地；按 `departure:<operation_id>` 创建静态 CTU03，创建、阶段关联及原窗口 departure 关联同事务提交。窗口已释放不重开，也不重置原轮次。当前阶段服务结束事实与原 READY 均不得早于其因果依据；本轮实际承接的未结束 Feed 仍阻止离场；当前阶段保留已应用发布的来源结束事实，不回读可清理 Feed。单纯计划成员不构成物理接管，不按缺失 Feed 否定结束事实；实际 RACK/BIN 在途/未知/未发布或接管仍重新核对。

**交接与恢复：**来源父任务终态不证明物理离场，最后换面阶段保留当前轮次给离场 owner；已交给下一面 Feed 的旧阶段仍按 8G 归档。离场冻结后通用 batch 不再为同架新成员发起投料。batch 前置扫描在 EXECUTING 查找之前恢复原阶段；Transport 及现有窗口服务拥有 ACK/物理结果/ACCEPTED 释放，FactProcessor 拥有插件结果应用/发布。接纳仅释放本地窗口，不关闭原物理义务、不推断 EMPTY 或 reservation 释放；精确已发布终态结果闭合原义务。未知和失败保留原执行，明确更新因果的权威同架接管只结束旧义务，不重开旧服务或重发物理动作。已归档结果重放不回查可清理 READY Confirmation。

**历史与测试所有权：**创建后恢复只依赖当前阶段、原离场 READY、原执行及其直接结果，不读已闭合 Feed/原进场历史。插件 `test_source_rack_departure.py` 拥有 typed 输入，`integration/test_source_rack_departure_postgresql.py` 拥有创建/关联/窗口交接/结果、并发/崩溃/重放、回调早于 ACK、跨 revision、同架接管、无 EXECUTING 与清理恢复。8G 仅修订父任务结束时的 owner 交接期望，其他业务断言保留；8F/8E 和直接 Feed 消费者回归。迁移 `43401479b2cc` 基于 `d061a18adea9`，只扩展既有阶段，保留存量空值并拒绝丢弃已冻结离场引用；核心仅承接模型/schema、精确 HEAVY mapping 和测试拓扑。

**后续边界：**首次 departure 决定、确定 WAIT 持久到期续问、明确 REJECTED 后的新决定，以及已接纳 FAILED 且仍在工作位而缺少权威面向的恢复留给后续切片；不将未知当失败或以计划面向补造。G2/G3/G5/G6 等未决值只限制对应生产分支，任务 8 整体及任务 9 drain 均未完成。

**验证与闭环 Review：**本片离线实现及验收完成。最终 8H PostgreSQL 44 passed（含迁移链、存量保留、模型/schema 一致性及有损降级拒绝），插件 FAST 167 passed；8E–8F 回归 63 passed、8G 回归 37 passed、直接 Feed 消费者离线回归 16 passed，未运行依赖真实 worker 的另外 3 项。核心聚焦 249 passed、精确 selector 选中宿主 HEAVY 8 passed，QUALITY 通过（4317 passed、5 个既有 skipped，跳过项不计通过）。schema 比对保留既有 Evidence/PickingTask 外键环警告，无新增迁移差异。独立只读 Review 发现创建前回读已闭合 Feed 历史、全取消来源失去离场出口两项 P2；三个反例先 RED，再按原 READY 与当前服务结束事实、实际本轮 Feed 因果范围修正，新增五类轮次/取消回归。原 Reviewer 合并完成旧意见闭环和最新完整 diff 的 fresh full Review，无剩余确认问题。验证绑定当前可执行快照，后续仅补本文记录；HEAD、暂存区、依赖文件及主工作区原有改动指纹保持不变。本轮不提交、不部署、不启用真实派发或真实取盘，任务 8 整体仍未完成。


#### 实施切片 8I：普通来源架首次 departure 决定与 WAIT 持久续问

**范围：**延续 8H 的显式精确到位、原进场轮次及全部当前来源服务结束依据；插件私有 matched 输入无既有 operation 时发起首次决定，有原 READY 时仍走 8H。生产服务结束候选 reader 不在本片装配，不以缺失 Feed、父任务完成或无 EXECUTING 任务推定服务结束。先收口当前阶段和直接物理义务，再创建 WorkLine-owned、`task_id=null` 的 typed departure 请求。未决设备映射、扫码关联、PkgID 和首批期限不设默认值；不接 drain、REJECTED 后业务重决策或已接纳 FAILED 面向恢复，不启用真实派发/取盘。

**最小持久进度：**复用既有阶段的 departure operation 游标，仅增加 `departure_retry_at`；无新授权模型、业务状态枚举或 timer。首次请求/原游标、到期新请求/游标替换分别同事务提交；迁移 `de97e0860fd5` 基于 `43401479b2cc`，允许 operation 先于 Transport 存在，仍拒绝孤立 deadline/result/Transport。存量 8H 引用不改写；请求期拒绝有损降级。请求未决、WAIT 与 READY 尚未创建 CTU03 均进入现有 batch 前置扫描，不要求再提供候选、设备事件或 EXECUTING 任务。

**结果与原子性：**基础 Confirmation 可靠保存响应后，固定插件 WMS consumer 经 FactProcessor 对精确当前 operation/原 Evidence/WorkLine/rack 做 typed 校验；WAIT Evidence 接收时间加 `retry_after_ms` 与结果发布同事务保存，重启/重放不重置。新增宿主 `RackDepartureResultReader.for_evidence` 仅读取原待应用响应，不拥有业务时机、时间或状态；原已发布 reader 仍禁止未发布响应驱动物理动作。WAIT 到期由 batch 在原 WorkLine/对象 authority 锁下重新核对当前游标、已发布原 WAIT 和当前事实，使用新 operation 与当时权威位置/面向创建请求；未知、RECONCILING、未发布或身份漂移不成为 WAIT，不换身份。正常未发布响应经精确 typed 校验后仅等待当前项，独立 Feed/回程等阶段仍可提交；身份和摘要冲突不被正常等待吞掉。READY 沿 8H 创建/关联唯一 CTU03；原结果重放不推进新游标，旧已闭合 WAIT/Feed/来源 Confirmation 可清理，不依赖历史最近响应。

**恢复与边界：**批处理仍先推进原回架/运动义务，再恢复 departure；请求期阶段即冻结来源服务，换面/通用退役/新 Feed 不再抢占同一阶段。计划或未来 revision 本身不延长停靠；当前本架在途/未知/未发布动作仍阻止新决定与运动，独立架不受统一阻断。已确认更新因果的实际接管结束旧本地阶段，保留原 Confirmation/Transport/Evidence 的可靠交付和物理结果，不恢复旧服务。窗口释放不重置原轮次。FactProcessor 只拥有当前响应应用/发布；业务到期新请求唯一归 batch；发送、原身份重提及物理接纳/结果沿现有 owner，不新增队列或 worker。

**测试所有权：**插件 typed 输入由原 FAST 文件承接；`integration/test_source_rack_departure_decision_postgresql.py` 拥有首次请求、当前游标、WAIT、FactProcessor 真实事务交接和应用/发布崩溃，以及并发/重放/跨 revision/轮次/接管/窗口释放/历史清理。8H 原孤立引用断言按本片请求期合同改为拒绝孤立 Transport，其他物理语义保留。宿主 FAST 和原生 PostgreSQL 仅验证 typed reader 的原身份与应用/发布读取边界；精确 HEAVY mapping 保留同文件 Scheduler 的真实 worker 派发验收，并增加 reader 原生 PostgreSQL owner；其他 20 个服务的原 HEAVY 集合保持不变。插件测试不进入核心 QUALITY/HEAVY。

**验证与闭环 Review：**本片离线实现及验收完成。最终 8I PostgreSQL 31 passed、8H 回归 44 passed、插件 FAST 168 passed；8E–8G 回归 100 passed、直接 Feed 消费者 16 passed，另外 3 个 Feed worker 用例未纳入本轮验收。核心聚焦 1176 passed；精确 selector 选中宿主 HEAVY 13 passed，包含 Scheduler 真实本地 worker 3 项、typed reader 原生 PostgreSQL 2 项和工作线配置/迁移 8 项。最终 QUALITY 4319 passed、5 个既有 skipped（跳过项不计通过），Ruff、格式与差异检查通过。迁移验证包含存量 8H 保留、模型/schema 一致性、完整约束和请求期有损降级拒绝；保留既有 Evidence/PickingTask 外键环警告。独立 Review 首轮发现正常未发布响应中断独立业务扫描、同文件 Scheduler HEAVY owner 遗漏两项 P2；分别以真实独立 Feed 和 selector 回归先 RED 后修正，再由同一 Reviewer 合并完成旧意见闭环与最新 21 文件 fresh full Review，无剩余确认问题。可执行文件保持评审指纹，后续仅补本段验证记录；HEAD、暂存区、Git 配置、依赖及主工作区 62 个既有改动指纹保持不变。测试仅使用独占临时 PostgreSQL/Redis 和本地 Mock WMS，不构成生产联调或物理验收；未暂存、未提交、未部署，不启用真实派发或真实取盘。生产候选 reader、drain 及 G2/G3/G5/G6 未决边界保留，任务 8 整体仍未完成。

#### 实施切片 8J：普通来源架真实候选发现与持久交接

**范围：**新增插件私有 `source_rack_candidates.py`，为现有当前架面、运动前来源绑定及首次 departure 三个读口提供真实 Repository 输入。当前面必须来自配置 FIVE_RACK 的精确 RACK 成员、原 Transport 成功且结果已发布、当前位置与因果匹配；识别普通 CTU01 与既有自动 CTU02 到位，不以最新历史响应、计划面向或空查询猜测。来源结束分别取精确 RACK_FACE_DONE，或非空投料最后分段的已发布权威交接；交接前当前面已接收且未取消的来源成员不能缺少完成依据。

**交接与恢复：**复用 8E 的运动前求值、8F 的原进场轮次/Feed/服务结束绑定及 8I 的 operation 游标。既有 batch 在 WorkLine 锁事务中发现、验证并保存阶段，满足门禁后同事务创建首次 departure；创建后由原阶段恢复，不依赖候选再次出现。当前阶段已经绑定时不因新 revision 重绑；冻结阶段之后的新成员不延长原停靠。既有合法未服务下一面不成为 departure 候选；CTU02 生产候选/参数 reader 不在本片新增。最后换面成功但没有下一 Feed 时，仍可用原阶段与真实当前面交给 8I，不能要求虚构 Feed。

**历史边界：**已有 Feed 与运动阶段分别增加 `completed_source_member_ids`，只承接同一任务/来源架已完成的不可变成员事实，不表示下一来源授权、当前面或物理位置。阶段绑定时同事务承接源 Feed 已继承的事实与仍存在的当前完成标记，CTU02 的下一面 Feed 再同事务继承；下一阶段沿该事实继续承接。A→B 交接后可清理 A Feed、Confirmation、旧阶段及原进场历史，B 阶段承接后也不再读取 B Feed/原 Confirmation；首次 departure 只核对当前阶段已承接的完成成员和当前合法未完成成员。缺失不是已完成；已完成成员不因新物理轮次重开，旧物理到位也不能证明本轮到位。迁移 `6680af0fe85b` 基于 `de97e0860fd5`，仅从仍存在的当前标记回填，不补造已丢失事实；活跃已绑定阶段的源 Feed 已清理，或活跃已交接 Feed 的前序同架已接收成员缺少当前标记、无法区分未服务和已清理时明确拒绝升级；标记完整的旧多面链正常回填。降级会丢失已交接且不再能从当前标记重建的完成依据时拒绝执行。仍被当前阶段直接引用的服务结束/到位 Evidence 不是可清理历史。

**所有权与未启用边界：**阶段与 departure 的业务恢复归现有 batch；结果应用/发布归 FactProcessor，原身份交付归 Confirmation，Command/Transport 沿现有基础恢复。不新增授权模型、业务状态、worker、timer 或业务共享层；生产组合根本轮不注入这些 flow，离线组合使用真实读口证明接续。扫码关联、方向/取盘/PUT 参数、PkgID、首批期限不猜；不接 departure 异常、drain 或真实派发。

**测试所有权与验收：**插件 `integration/test_source_rack_candidates_postgresql.py` 拥有真实发现、原子交接、并发、崩溃、跨 revision、窗口释放、同架接管、旧轮次及清理恢复，复用原 8F/8I owner 回归。插件业务测试不进入核心 QUALITY/HEAVY；新增迁移精确映射到宿主配置/迁移验收，核心承接 selector、拓扑和该 schema 路径。首轮独立 Review 发现多面历史依赖 P2，已用 A→B 清理反例先 RED 后修正，并补当前 B Feed 清理、有损降级拒绝及存量回填/schema 验收；闭环审查另发现普通 task 入口遗漏完成事实和存量源 Feed 清理后升级无出口两个 P2，已补真实 A1→B→A2 后 task advance 及旧库升级拒绝反例；共用准入同时覆盖 CTU01 等待阶段和 CTU02 换面后已继承完成事实；同一精确到位和 owner 优先读取已绑定 Motion 的完成事实，未绑定才回退 Feed，避免部分 Feed 清理后被旧列表遮蔽；新 revision 仍可接续；迁移覆盖 B 阶段创建前后，标记完整则回填、前序标记丢失则拒绝升级。最终验收：8J PostgreSQL 42 项、受影响投料/换面 PostgreSQL 47 项、插件 FAST 168 项、所选 HEAVY 8 项均通过；QUALITY 完整刷新通过（4319 passed、5 个既有 skipped），lint/format/diff 检查通过。3 项真实 worker 测试明确未运行，不计入通过。迁移覆盖干净库至 head、旧链回填/schema 对齐、缺失依据升级拒绝及有损降级保护。原 Reviewer 已合并完成旧意见闭环与当前完整审查，无剩余确认问题。保持未提交、未部署、未启用生产组合/真实派发；原工作区改动保持不动。

#### 实施切片 8K：普通来源架离场明确未接纳后的重新决定

**范围：**只接原 CTU03 Transport 明确 `REJECTED`、匹配结果已应用并发布、当前货架仍权威在原工作位且业务 owner/原轮次有效的离线路径。复用 Motion 当前 departure 引用及 8I/8H；不接已接纳后 `FAILED`、drain、生产装配或真实派发。

**事务与恢复：**batch 前置扫描从原 Motion 发现明确拒绝，核对原 Transport/READY/结果身份和摘要，再重验当前面向、来源服务结束及本架直接义务。新 departure 请求创建与 operation 游标替换、旧当前 Transport/result 引用退出在同一 WorkLine 锁事务提交；旧 Transport、请求正文及 Evidence 不改写。确定 WAIT 按原接收时间加 `retry_after_ms` 保存到期时间，未知沿原请求恢复；READY 用新决定创建唯一新 Transport。旧结果重放不覆盖新游标，也不复活已接管的旧轮次。没有候选、父任务完成或窗口释放不丢失原 owner。

**历史与边界：**源 Feed/原 Confirmation 已将必要事实交给 Motion 后可清理；当前服务结束和实际到位 Evidence 继续作为直接依据。活动进场窗口仍引用的拒绝 Transport 是当前依赖，保留至新 Transport 接管窗口引用后再清理；窗口已释放且新决定已接管时，旧离场记录可在新 READY 前清理。清理后新实例仍恢复当前请求和后续重新决定，不读历史最后响应。发送未知、仅 ACCEPTED、结果未发布、位置/面向未知和在途接管不产生替代动作；更新的权威实际接管按既有路径退役旧业务。未来 revision 本身不延长原停靠。

**所有权与验证：**业务重新决定继续归插件 batch/Motion，FactProcessor 负责原结果应用与发布，Confirmation/Transport 负责原身份可靠交付；不新增模型、业务状态、worker、timer、迁移或基础层业务规则。新增插件 PostgreSQL recovery 测试覆盖并发、事务崩溃、旧结果重放、跨 revision、窗口释放、同架/跨线接管、当前依赖及合法历史清理；复用 8H/8I/8J 回归。最终验证：新增 8K PostgreSQL 31 项，8H/8I/8J PostgreSQL 回归 117 项，共 148 项通过；插件 FAST 168 项、测试拓扑/所有权 14 项通过，Ruff、格式及 diff 检查通过，HEAVY selector 为 NONE。独立 Reviewer 完整闭环审查未确认 P1/P2/P3，无需代码修复。完整 QUALITY 由本轮提交 hook 执行；未决字段、设备/扫码映射、PkgID、首批期限保持原范围。本轮仅提交当前工作分支，不部署、不启用真实派发、不推进真实取盘。

#### 实施切片 9A：下一任务优先的 drain 决定与持久交接

**范围：**只实现 drain 首次准入、当前 operation/结果引用、确定 WAIT 到期续问和 READY 冻结交接；不创建 drain 货架 Transport，不接 reservation 执行或任务完成确认，不启用生产装配。当前插件未声明 RETURN_BUFFER 槽位，本片使用显式权威缓冲位置/容量及既有 MatchedReturnLocation 读口作为离线前提，不把 OUTLET 推断为退箱位，不生成生产默认值。

**准入与原子性：**现有 batch 前置入口在无 EXECUTING 任务时仍调用插件 drain owner。先按当前引用恢复未知请求、未发布结果或 READY，只有首次请求/确定 WAIT 到期才进入新决定准入；同 WorkLine 锁事务优先调用 prepare_next_in_session，claim、绑定及 prepare 义务原子提交。成功准备即交回既有 prepare 生命周期；只有明确 NO_ELIGIBLE_TASK 才继续核对本线当前 Work/Feed、未释放来源窗口及当前 FIFO。WORKLINE_NOT_READY 等原因不能当作无任务。FIFO 依既有首次 SCAN4 事实取序，已分配、未合格、位置未知的队头不可越过；权威缓冲位置/容量缺失不创建请求，数量超界明确拒绝，不截断冒充正确容量。

**当前进度与恢复：**新增插件 AutomaticPickingDrain 仅保存 WorkLine 当前 operation、响应 Evidence、WAIT 到期时间和冻结 Return ids；请求/完整响应及可靠交付仍只归 Confirmation/Evidence。新义务与当前引用创建/替换同事务，单线当前唯一索引为并发后盾；确定 WAIT 以原 Evidence 接收时间加 retry_after_ms 固定期限。未知沿原身份恢复，旧响应不能覆盖新游标；WAIT 到期无已确认需求或下一任务优先时退出当前等待，事实未知仍保留 owner。READY 保留原决定和原成员，新增候选不追加，独立任务可按既有 prepare 入口准备；本片不伪造 reservation 消费、物理完成或 EMPTY。

**恢复所有权与历史：**业务准入/WAIT/READY 继续归插件 batch，原结果应用与发布归 FactProcessor，技术交付归 Confirmation；未接 drain flow 时结果延期保留。新增当前进度计入业务停用/换绑 blocker，并接入现有显式归档。当前引用的请求、Evidence 和 READY 成员仍是直接依赖；旧 WAIT 引用退出、可清理前段 Work/Feed 删除后，新实例从当前进度恢复，不扫描最近历史响应或依赖已完成任务。没有新通用授权、timer、worker 或基础层业务规则，不导入人工 drain。

**测试所有权与验证：**插件 test_drain_flow 拥有输入与 batch 接线，integration/test_drain_progress_postgresql 拥有下一任务优先、当前引用、WAIT/READY、并发、崩溃、重放、无 EXECUTING、合法历史清理及本表迁移验收；插件 composition 测试保持静态 operation 集与默认未装配断言。核心只承接显式模型安装/过滤、拓扑、selector 及配置/schema HEAVY。最终验证：插件 PostgreSQL 43 项、受影响回归 41 项、插件 FAST 174 项、核心安装/拓扑/所有权 32 项、selector 合同 236 项及所选 HEAVY 18 项均通过；3 项既有真实 worker 用例明确未运行，不计通过。干净库降至父版本再升 head、schema/当前唯一索引及拒绝有损降级通过，Ruff/格式/diff 检查通过。首轮 Review 发现容量校验可被未知/未合格/已分配尾项提前返回绕过的 P2，已补三例真实 PostgreSQL 反例先 RED 后修正：每次确认成员加入前缀后立即校验原容量，不增加新规则；三例 GREEN、完整 43 项及 FAST 已刷新。同一 Reviewer 合并完成该意见闭环与当前完整审查，无剩余确认 P1/P2/P3。完整 QUALITY 由本轮提交 hook 执行。设备/扫码映射、PkgID、失败离场面向与首批期限仍按各自分支保留；本轮仅提交当前工作分支，不部署、不创建 drain 货架 Transport、不启用真实派发或真实取盘。

#### 实施切片 9B：drain CTU01 与到位持久交接

**范围：**在原 READY 已匹配、应用并发布，精确工作位、完整 CTU01 参数及权威结果显式给定的离线前提下，实现 reservation 每架唯一进场、精确绑定及首次到位交接。不接 return_batch、换面离场或 reservation 收口，不启用生产装配；不推断设备、扫码、RETURN_BUFFER 位置/容量或 PkgID。

**原子性与最小持久化：**新增 AutomaticPickingDrainRack 只关联 drain、rack、TransportDecisionBinding 和可空的到位 Evidence。每架执行与原 drain operation、READY、首个冻结面、目标工作位精确关联；Transport、窗口、Binding 与该执行引用同事务创建。复用 ReliableRackTransportCreator 和现有进场窗口，picking_task_id 为空。窗口 REUSED 不能代替本轮身份，旧同架窗口不被冒领；未知结果不创建替代动作。racks 是无序集合，一架缺参数或等待不阻塞其它可提交架，物理窗口仍由既有能力约束。

**到位与恢复 owner：**Transport 持续拥有原身份提交、技术重试及未知结果；插件静态 publisher 校验原 reservation 与动作，FactProcessor 校验结果身份/版本及完整冻结 outcome 并负责业务发布。batch 在没有 EXECUTING、通知丢失或重启时继续扫描原执行引用；先恢复原义务，再读取尚未创建架的显式参数。仅在匹配 SUCCEEDED、唯一成功 RACK 成员、精确目标/面和当前因果位置投影、宿主结果与业务 Evidence 均发布后，原子保存到位 Evidence。ACK/ACCEPTED 不代表到位；旧轮次/旧版本不能覆盖当前交接。单架到位可独立留给下一片，不等待整组到齐。

**历史与后续边界：**当前 drain READY/Confirmation、Binding、Transport 和到位 Evidence 仍是当前直接依赖，保留至后续 owner 接管；旧 WAIT 和已交接的前段 Work/Feed 可清理。恢复不查询最后历史响应、不创建虚构任务/Feed，不增加授权模型、业务状态、通用 timer 或基础层业务规则。下一片 9C 才将原冻结 Return ids 与实际到位架面接入普通 return_batch，9D 再处理冻结面顺序和离场；没有这些后续事实不关闭 drain 或释放 reservation。

**验证归属：**插件 integration/test_drain_inbound_postgresql.py 拥有唯一创建、原子回滚、并发、回调早于 ACK、结果重放、身份/内容漂移、多架独立推进、无 EXECUTING 与合法历史清理恢复；原 9A 和既有 Transport 分流路径做定向回归。核心只承接模型安装/过滤、测试拓扑/所有权、selector 与 schema/config HEAVY。新增迁移由 Alembic 生成 revision，支持干净库往返与拒绝丢失执行身份的降级；完整 QUALITY 留给后续明确授权提交的 hook。最终验证：9B PostgreSQL 33 项、9A 及旧 Transport 分流回归 49 项、插件 FAST 174 项、核心安装/拓扑/所有权/selector 269 项、所选 schema/config HEAVY 18 项通过；Ruff、格式和 diff 检查通过。迁移在隔离 PostgreSQL 的干净逻辑库执行，schema 比较无差异，拒绝丢失执行身份的降级；比较全局 metadata 时仍有既有循环外键 warning。本轮独立 Reviewer 核对固定 13 文件与日志并完成完整审查，未确认 P1/P2/P3，无需代码修正。未新增 worker、队列或 timer 接线，未运行真实 worker；不提交、不部署、不启用真实派发或真实取盘。

#### 实施切片 9C：原冻结成员回架与成功事实持久交接

**范围：**在精确到位、当前位置和权威结果显式给定的离线前提下，将原 READY 的冻结 Return ids 接入已有 return_batch、BIN_MOVE 和结果处理。不接换面、离场、补架或 drain 收口，不启用生产装配和真实派发；设备/扫码映射、RETURN_BUFFER 参数及 PkgID 仍按原分支保留。

**最小当前进度：**复用现有模型：Allocation 新增可空 drain_id，关联请求候选中原成员所属 drain；Drain 新增 returned_ids，仅保存已经精确匹配并发布的原成员回库成功事实。没有新通用模型、业务状态、授权或 timer，不复制 payload、储位或库存。普通架为原成员返料时同样关联原 owner，不把新候选扩充到旧 reservation。存量9B进度升级后成功事实为空，存量普通 Allocation 没有 drain 关联；不从旧历史猜测补齐。

**候选与事务：**9B当前进场、当前因果位置和已持久交接的到位身份共同决定 drain 架面；另一入口提供相同旧进场不能绕过重验，独立普通架面不被旧 reservation 当作资源围栏。drain 架只领取原范围内的合法 FIFO 连续前缀，未知、未合格、位置缺失、未交接领取及范围外队头不能被过滤跳过。原决定未知/未发布只保留其原成员直接依赖；确定 WAIT 不占有候选。请求创建、候选领取及 drain 关联同 WorkLine 事务提交；两入口共享原 Allocation/Confirmation/Transport，不建立第二套可靠机制。

**事实与恢复：**batch 先恢复原 Allocation，再发现新候选；FactProcessor 仍负责原请求/Transport结果应用与发布，Confirmation/Transport 沿冻结身份可靠恢复。BIN_MOVE 创建复用原 READY，ACCEPTED 只交接 Return，不计回库成功；settled、NO_BATCH、缺失或已归档 Return 也不是成功证明。位置 TARGET_PLACED 与原终态结果经宿主应用后，业务结果须匹配原请求、成员、目标、完整冻结 outcome 和发布版本；只把确定成功且位置明确的原成员写入 returned_ids，与本次推进同事务。部分成功先留存正事实，其余未知/失败仍保留原动作及依赖，不新建替代动作；当前领取尚未交接时不越过该队头。

**历史边界：**当前原请求、已分配成员和未闭合 Transport 仍保留。Return 接纳归档后可以删除其闭合源记录；Allocation 尚未完成时，其冻结 return_ids、请求和动作继续恢复成功结果。原成功事实交给 Drain 后，已整体闭合且没有其它当前引用的 Allocation/Transport/Confirmation 历史可清理，当前 drain 仍独立知道哪些原成员成功；不查询最后历史响应或凭没有记录推断完成。drain 本身不因本片成功而归档或释放 reservation。

**测试归属：**插件 integration/test_drain_return_postgresql.py 拥有原成员范围、两入口竞争、请求/动作/成功事实原子回滚、ACK/未知/未发布分类、部分成功、多架独立、同箱码新轮次、合法历史清理和本字段迁移验收。既有9B、回架分配及运动前门禁按直接调用回归；核心仅承接安装/拓扑/selector和所选配置/schema HEAVY。完整 QUALITY 留给后续获授权提交的 hook，本轮不提交、不部署、不启用真实派发或真实取盘。

**验证与闭环 Review：**9C PostgreSQL 31 项、9B 回归 33 项、其它聚焦回归 40 项、插件 FAST 174 项、核心安装/拓扑/selector 269 项、所选配置 HEAVY 8 项通过；40 项为定向选择，不代表相关领域全量验收。隔离 PostgreSQL 验证存量9B升级、干净库迁移往返、受影响表 schema 无差异及拒绝丢失当前归属/成功事实的降级；全局 metadata 比较仍有既有循环外键 warning。Ruff、格式和 diff 检查通过。独立 Reviewer 核对固定12文件及六组日志，完成完整审查，未确认 P1/P2/P3，无需修正；未改变已验证的可执行快照。恢复仍由原 batch/FactProcessor/Confirmation/Transport 承担，换面离场与 drain 收口留给后续切片，离线成功事实不代表现场业务已完成。

#### 实施切片 9D 首片：drain 冻结面序换面与到位交接

**范围：**在原 reservation、精确当前到位、完整冻结位置参数和权威结果显式给定的离线前提下，实现同架按 `rack_faces[]` 顺序的 CTU02 唯一创建、关联、结果消费及换面后向9C交接。不接 departure/CTU03、补架、异常自动重试或 drain 收口，不启用生产组合根、真实派发或取盘。

**最小持久接续：**复用 AutomaticPickingDrainRack 的 binding_id/arrival_evidence_id 保存本轮当前动作和已交接到位；首个 CTU01 的实际到位可交接给同 reservation 的当前 CTU02。新动作、当前 binding 替换和本面阶段出口同一 WorkLine 事务提交；新动作未确认成功发布前 arrival 引用为空。动作关联固定原 READY Evidence、drain operation、rack 和冻结面序号；位置沿本轮已经权威匹配的冻结工作位，不新增 WMS 字段或推断设备映射。没有新增表、字段、业务状态或迁移。

**门禁与恢复：**复用既有 AutomaticPickingReturnMotion 表达每面运动前求值，原 return_batch/分配及同架直接义务先恢复，未知、未发布或 READY 已分配未闭合均不按无候选处理。只在明确接入本片后，由 batch 在回架求值前准备 drain 面阶段，不伪造 Feed、来源时间或来源服务结束；本阶段的新请求继续由原回架 owner 原子关联。接入前的独立 READY 批次先恢复，不能直接代替本阶段最终求值；其闭合后仍按当前 FIFO 求值。普通9C同面分批能力保留。原义务闭合或确定无候选后冻结阶段，后到候选不重开。新面来自对应 CTU02 的权威成功、唯一 RACK 成员、当前因果位置/面及宿主和业务发布，不要求新投影继续匹配旧 CTU01；旧结果只留证，不覆盖当前动作。两入口仍通过原 Allocation/Confirmation 领取防重，独立架不等待其它架动作。当前架存在更新的未闭合动作或已被新因果事实接管时不创建本轮换面。

**唯一 owner 与历史：**插件 batch 的原 drain/inbound 入口先扫描当前动作并恢复已发布到位，再推进冻结面序。Transport 负责原动作提交和技术恢复，FactProcessor 负责原结果应用/发布；不增加 worker、timer 或通用授权机制。当前 READY 和当前动作/未闭合分配仍是直接依赖；成功交接后，未被其它当前对象引用的旧面阶段和已闭合分配可清理，恢复不查询最后历史响应。仍被宿主活动窗口引用的原对象继续保留。无 EXECUTING 或新通知也可恢复原义务；终面停在9C回架，不创建离场或归档 drain。

**测试所有权：**插件 integration/test_drain_rotation_postgresql.py 拥有冻结面序、创建/关联/到位事务、并发、重放、ACK时序、未知/发布门禁、两入口、多架、无EXECUTING和合法历史清理。9B/9C、回架分配/运动前阶段和普通来源运动/离场直接消费者定向回归；核心只验证安装和测试拓扑，HEAVY 按最终 selector 清单执行。完整 QUALITY 留后续获授权提交 hook；未决值继续按原分支保留。

**验证与闭环 Review：**本片28项与9C31项 PostgreSQL 最终联合验收59 passed；9B33项和普通回架/运动前及来源分流65项定向回归通过，合计157项不同 PostgreSQL 用例。末次修正只影响 drain 原范围外队头分支，按 Reviewer 核对的覆盖路径复用9B及普通域证据，并完整刷新本片/9C。插件 FAST174项、核心安装/拓扑33项通过，HEAVY selector 为 NONE（无 schema、基础层或核心测试资产变化）；Ruff/格式/diff检查通过。既有全局 metadata 循环外键 warning 保留，受影响 schema 比较无差异。

首轮两项P2及闭环追加一项P2均以真实事务反例先RED后GREEN：未知普通架面读口保留旧分配时不得用空来源时间；接入前独立READY不能替代本阶段最终求值；原成员已回库后，范围外新FIFO队头不得留住旧reservation架面。最终求值归原ReturnBatchFlow，明确范围外队头只结束本范围求值，不跳队领取或改写成功事实。原成员未知、未合格、未交接依旧等待。同一独立Reviewer合并完成完整审查、意见闭环及最终差异核对，无剩余确认P1/P2/P3。当前12文件可执行快照保持固定，无暂存/提交/部署；独占测试环境已清理。离场、drain收口及现场/真实worker验收不在本片完成结论内。

#### 实施切片 9D 后续片：终面求值与显式 departure READY 的 CTU03 交接

**范围：**原 drain READY、精确当前到位、完整参数及已发布 departure READY 显式给定的离线路径。终面同样先准备运动前阶段，再由原 ReturnBatchFlow 恢复原义务并完成一次求值；未接本片时维持9D首片的终面边界。不接首次 departure、WAIT续问、拒绝后重新决定、离场失败重试、补架或 drain 收口，不启用生产装配或真实派发。

**身份与事务：**显式输入只关联原 drain id、rack、当前到位 Evidence 和 departure operation；已有 Motion 的 departure operation/Transport/result 引用承接原执行。终面阶段已结束、本架所有原分配及直接义务已闭合、当前权威架面仍匹配且未被更新动作接管后，才消费精确 WorkLine-owned、task_id=null 的原 READY。READY 请求不得早于当前到位及本次阶段结束，目的地原样采用 WMS rack_destination，固定 CTU03；Transport、原 READY Binding、Motion 引用及原进场窗口 departure 关联同事务提交。不伪造来源 Feed、服务结束 Evidence 或来源轮次时间，不新增表、字段、业务状态、migration 或通用授权。

**交接与恢复 owner：**显式安装本片后，原 batch 前置入口先扫描持久 Motion 的原离场引用，再准备架面求值，最后按原 drain 推进新动作；恢复不需要新候选、EXECUTING任务或新通知，停用后仍可收口已建动作。Transport 保持原身份提交及技术恢复，FactProcessor 应用并发布匹配的原结果。ACCEPTED仅释放本地进场窗口，不代表物理成功、drain完成或WMS reservation释放；本架未闭合结果不统一阻塞其他架。只有原成功终态、唯一RACK成员、已知精确终位、宿主和业务发布均闭合后，才归档该面离场阶段；ZONE目标保留WMS原目标，实际成功终位仍须为明确RACK_POSITION。明确非成功或未知结果保留原阶段，不创建替代动作。

**所有权与历史：**普通来源 departure 扫描仅领取有来源绑定的Motion，drain结果使用独立静态step沿冻结Binding接入，不按task_id=null猜业务owner。原离场的READY、当前Motion/Binding/Transport及其结果仍为直接依赖；已交接且无当前引用的中间面阶段、分配和动作可清理，恢复不查询最后历史响应或已清理来源Feed。终面冻结后后到FIFO不重开阶段，不扩展原return_ids，原成员未全部回库也不冒充drain完成。

**测试所有权：**插件 integration/test_drain_departure_postgresql.py 拥有终面求值、原READY身份/发布门禁、创建和关联原子性、并发、崩溃、接纳与回调时序、多架、旧轮次/接管、无EXECUTING、停用、结果重放及合法历史清理。9B/9C/9D首片和普通来源departure/WAIT/明确未接纳路径做定向回归；核心只承接安装/拓扑，HEAVY执行最终selector清单。未决设备映射、扫码关联、PkgID和首批期限保持原阻塞范围；完整QUALITY留后续获授权提交hook。

**验证与闭环 Review：**本片29项、9D首片28项、9C31项及9B33项 PostgreSQL 联合验收121 passed；普通来源departure/WAIT/明确未接纳三域106 passed，合计227项不同用例。插件FAST174项、核心安装/拓扑33项通过；HEAVY selector为NONE，9个Python文件Ruff/格式及diff检查通过。既有全局metadata循环外键warning保留，未改schema。终面缺阶段与有READY未建CTU03均先取得真实RED再GREEN；开发回归中一项断言误将未业务发布视为完成、一项历史清理fixture缺一次持久扫描，分别按合同和既有owner补齐前置，未放宽生产门禁。独立Reviewer核对固定10文件及完整调用链、事务、对象锁、测试所有权和历史边界，未确认P1/P2/P3，无需代码修正；最终可执行快照保持不变。仅完成本片离线验收，不提交、不部署，不接首次departure、WAIT续问或drain收口。

#### 实施切片 9E：drain 首次 departure 与确定 WAIT 持久续问

**范围与身份：**沿用9D终面阶段、当前权威到位和原冻结drain reservation，所有直接回库义务闭合后才创建首次 typed departure 请求。Confirmation与Motion的当前 `departure_operation_id` 同事务落地；后续结果必须匹配原WorkLine、rack、当前operation、请求正文和响应Evidence。普通来源以来源绑定领取，drain以原READY关联领取，不能仅凭 `task_id=null` 选择owner。未扩展字段、模型、业务状态、通用timer、SDK或宿主业务逻辑。

**WAIT与READY：**确定WAIT的到期时间固定为原Evidence的 `received_at + retry_after_ms`，FactProcessor同事务应用业务结果和发布；已发布结果也可由持久扫描补记到期时间。到期后重新核验当前原架面、终面阶段及直接义务，以新operation和替换当前游标同事务续问。未知、对账或未发布结果保留原身份；重放旧结果不延长期限、不覆盖新游标。departure义务不受FIFO为空、无EXECUTING或下一任务准备结果取消。READY沿当前持久游标接回9D的CTU03创建、关联和结果恢复，不需要重提供显式READY输入。

**恢复owner与历史：**原batch入口先恢复operation-only和已建Transport，再推进新候选；Confirmation负责原身份交付，FactProcessor负责精确typed结果应用/发布，Transport继续负责原执行。当前drain根/READY、Motion、当前Confirmation及原动作仍是直接依赖；旧WAIT及已交接的闭合历史可清理。停用线遵循既有宿主领取规则，结果保留至恢复活动；不以停用制造新动作。没有通知、没有EXECUTING任务、双worker竞争或事务中断均不另建owner。

**测试所有权与边界：**插件 `integration/test_drain_departure_decision_postgresql.py` 承接首次请求、当前游标、WAIT期限、续问原子性、结果发布、READY接回、并发/崩溃/旧结果、来源owner隔离、多架独立、无EXECUTING及历史清理恢复。9C/9D和普通来源departure三域做定向回归，核心只验证安装/拓扑。不接drain收口或失败重决策，不启用生产装配或真实派发；设备映射、扫码关联、PkgID及首批期限等未决项保持原阻塞范围。

**验证与闭环 Review：**新增25项、9C/9D回归88项及普通来源departure三域106项 PostgreSQL 验收全部通过，合计219项不同用例；插件FAST174项、核心安装/拓扑33项通过。HEAVY selector为NONE，5个Python文件Ruff/格式及diff检查通过，无schema变化；既有metadata循环外键warning保留。首次请求和WAIT游标/发布先取得真实RED再GREEN。开发验证纠正离线时钟早于权威到位的fixture，并按SRS及宿主既有规则修正停用线测试前提，未放宽生产门禁。独立Reviewer核对固定6文件、事务/锁、恢复链、owner隔离、测试所有权与历史边界，未确认P1/P2/P3，无需代码修正；其后仅补齐回归结果和本文验收记录。完整QUALITY留后续获授权提交hook；本片不提交、不部署，不接drain收口或失败重决策。

#### 实施切片 9F：drain 离场明确未接纳后的重新决定

**范围与准入：**原CTU03明确REJECTED、原宿主结果和匹配业务Evidence均已发布，原drain/READY仍有效、当前到位架面仍权威匹配、终面阶段及直接义务闭合且没有更新动作接管时，由原batch departure恢复入口创建一次新决定。复用9E当前请求、WAIT续问和9D的READY→CTU03路径；发送未知、未发布、已接纳或已接纳后FAILED均保留原执行身份，不进入本分支。窗口释放不改变原进场轮次；同架其它owner的未ACK动作也须让位，独立架不受统一阻断。

**事务与恢复owner：**新Confirmation、Motion当前operation替换、旧Transport/result退出当前引用在原WorkLine事务中提交；提交前中断全部回滚，提交后丢通知由既有持久扫描恢复。旧请求、READY、Transport和Evidence不改写；重放旧结果不能覆盖新游标或新动作。沿用batch业务恢复、Confirmation可靠交付、FactProcessor应用/发布及Transport原动作恢复，不新增模型、字段、业务状态、通用timer、worker或基础层业务流程。

**历史与测试所有权：**当前drain根/READY、原进场到位、Motion和当前请求/动作仍为直接依赖。旧动作整体闭合且新动作已承接窗口引用后，旧departure请求/结果/动作可清理，恢复不查最后历史响应。插件 `integration/test_drain_departure_recovery_postgresql.py` 承接重决策、并发、事务崩溃、旧结果重放、窗口释放、owner接管、无EXECUTING、多架独立及历史清理恢复；9E/9D/9C做直接回归，核心仅承接安装与测试拓扑。普通来源实现及其fixture未改，不重复将其独立完整矩阵作为本片主owner。

**验证与闭环Review：**首次反例取得真实RED（发布拒绝后仍只有一个departure请求），最小修正后GREEN。最终新增27项、9E/9D/9C直接回归113项 PostgreSQL 验收全部通过，合计140项不同用例；开发轮25项不重复计数。插件FAST174项、核心安装/拓扑33项通过；HEAVY selector为NONE，3个Python文件Ruff/格式及diff检查通过，无schema变化。既有metadata循环外键warning保留。独立Reviewer核对固定4文件、原owner/事务/锁、结果发布与身份、直接义务、调用与测试所有权及历史清理边界，未确认P1/P2/P3，无需代码修正；其后仅补齐回归结果和本文验收记录，生产和测试快照未变化。

**后续边界：**完整QUALITY留后续获授权Commit hook。本片不提交、不部署，不接drain收口、已接纳FAILED重决策、补架或生产装配，不启用真实派发或取盘；设备映射、扫码关联、PkgID、权威失败面向及首批期限等未决项保持原范围。

#### 实施切片 9G：正常 drain 收口与原离场义务原子交接

**范围与出口：**原 READY 冻结成员全部沿 9C 权威回库成功事实登记，且每个冻结货架的原 CTU03 已明确 ACCEPTED，或其精确成功结果已完整发布时，根 drain 才归档退出。原 RACK 首次接纳冻结的 `result_deadline_at` 非空也保留其已经接纳的正事实，后续 FAILED/RECONCILING 先于首次扫描不能抹去交接；只读取该事实，不比较期限或引入新 timer。请求未决、确定 WAIT、未发布 READY、未接纳的 PENDING 或交付未知均不构成交接；少一个原成员或货架交接也不能退出。交接与根退出在原 WorkLine 事务提交，不把 ACCEPTED、根归档或历史缺失推定为物理离场、EMPTY、WMS 释放容量或任务完成。

**持久事实与恢复 owner：**根仅增加 `departure_handoffs`（冻结 rack→原 Transport identity）正事实，避免先完成货架的已闭合 Motion 清理后丢失根进度；不复制请求正文、位置、资源容量或创建授权模型。batch 以原 Motion 的 typed drain READY Evidence 发现 departure，脱离已归档根表和 DrainRack，继续恢复原请求/动作；Confirmation、FactProcessor 与 Transport 分别保留可靠交付、应用发布和物理结果 owner。根退出后未结束 Motion 仍计入既有未完工作量。原 Motion、当前 READY Evidence、决定与 Transport/Binding 是未闭合义务的直接因果依赖，须保留；可清理根、DrainRack 和已闭合 allocation 不再成为原离场发现入口。晚到原结果只闭合原 Motion，不能重开根或写入新轮次。

**迁移与测试所有权：**Alembic `77b256c269b3` 基于 `588d54e9fe3e` 仅增加交接映射，存量行置空，不从历史补造成功；降级遇非空交接事实拒绝丢失。插件 `integration/test_drain_completion_postgresql.py` 拥有正常出口、原子回滚、并发、重放、ACK 前成功回调、根历史清理后独立恢复、无 EXECUTING、多架闭合历史清理及新旧轮次隔离验收。9A/9C/9D/9E/9F 为直接回归；核心仅承接安装/测试拓扑及迁移所选 workline configuration HEAVY，不进入插件业务判断。

**验证与闭环 Review：**正常出口先取得真实 RED，再最小实现。独立首轮发现 1 个 P2：ACK 后、首轮扫描前的 FAILED/RECONCILING 会漏记交接；四组合回调反例取得 2 失败/2 通过，再复用原 RACK 首次接纳正事实修复，未增加字段、timer 或失败重决策。修后新领域集 25 项及受影响 departure 回归 81 项重新全部通过；9A/9C/rotation 的 102 项未安装 departure flow，不执行修正分支，证据仍有效。合计 208 项不同插件 PostgreSQL 用例，加所选真实 HEAVY 8 项为 216 项，均非 skip。存量 9F 升级、干净库迁移链、目标表 schema 比对及有损降级拒绝通过；既有 metadata 循环外键 warning 保留。插件 FAST 174 项、核心安装/拓扑 33 项、selector 合同 266 项通过，8 个 Python 文件 Ruff/格式及 diff 检查通过。原 Reviewer 以修订快照合并完成 P2 闭环与 fresh full Review，无新增 P1/P2/P3；其后仅更新本文验收记录，可执行快照未变。

**后续边界：**不接补架、完成确认或已接纳后 FAILED 重决策，不增加通用模型、业务状态、timer、worker 或生产装配，不做 bin-line-common 抽取。设备映射、精确扫码关联、PkgID、首批期限及失败权威架面等未决项保持原阻塞范围。当前仅离线实现与验证，不提交、不部署、不启用真实派发或取盘；完整 QUALITY 留获授权 Commit hook。

#### 实施切片 9H：正常补架的自动插件持久接续（current、WAIT 与连续 READY 已离线实现，真实容量待补证）

**合同状态与范围：**沿用[主合同 §9.2.3](../../contracts/wms-outbound-picking-task-integration-requirements.md#9h-replenishment-freeze)、人工线 §5.6 的 drain 基线及自动插件设计 §5.4。撤回两类请求必须新增 `reservation_operation_id`、先改 wire/SDK/converter/Adapter/OpenAPI 和人工消费者再实施的前置链；当前 drain 仍只传工作线和数量，return_batch 使用现有架面及候选。WMS 内部容量关联不因无新增字段而被判定缺失。本片只补自动插件已有 READY 在供架耗尽、原成员尚未回库时缺少的持久接续；容量接续及核销细节需补证，不把既有基线整体重新冻结。

**复用与最小建模：**复用 `AutomaticPickingDrain` 的当前请求、原成员与回库进度、供架交接正事实和 9A–9G 的可靠端口/执行链。原请求 operation、Evidence、DrainRack/Transport 关联区分本地轮次；不预设外部 reservation 新字段或新通用模型。完整剩余责任与本次实际请求 FIFO 前缀可能不同，独立已交接动作仍有责任但不重复计数量。当前进度须自含这两份范围及“已开始 READY 后续行”的本地正面依据，避免补架 WAIT 被当作初始 WAIT；具体最少持久关联及 schema 差额在实施切片首个补丁前审查，不从可清理历史或数量相等关系猜测。已有离线快照字段不是生产 schema 承诺。早期首次 READY reader 仅支持请求数量等于全部原成员数；现已直接替换为 `DrainReplenishmentReader`，当前结果按本次请求范围校验 intent/摘要，并单独校验完整剩余责任。successor 的读取差额已在 WAIT/连续 READY 片闭合，不通过改 wire 消除本地差额。

**单次 successor 的已实现事务边界。**在精确当前 READY、原成员范围、权威位置与供架交接显式给定的前提下，先恢复既有义务，再求值耗尽与正数可请求前缀；同一 WorkLine 锁事务创建现有 typed intent/Confirmation，冻结本次成员和完整剩余责任，使旧 current 退出并成立唯一新 current。旧退出是责任交接，不走“原成员全部回库”的 9G 正常完成语义，不改原 operation、动作身份或物理结果。回库事实在切换前后均按精确成员交给唯一当前责任 owner；新 current 不依赖可清理前驱。此片使用现有 DTO 和显式离线结果推进，不以 WMS 新字段冻结为前提；持久补架 WAIT 和连续第二次补架已由后续片补齐，生产装配仍未启用。

| 切片顺序 | 验收及依赖 | 恢复 owner |
| --- | --- | --- |
| 单次 current 原子交接（离线已实现） | 并发只有一个 successor；创建/冻结/旧退出/新 current 任一点失败全部回滚；重启不重复请求；回库在切换前后、两入口防重、旧结果及历史清理。复用已有首次 READY 求值，不再追加同义组合 | 自动 batch 负责准入与 current；Confirmation 负责交付；FactProcessor 负责事实应用发布；原 Allocation/Transport 保持身份 |
| 持久补架 WAIT 与责任归零出口（离线已验证） | 保留首次接收计算的固定到期点；到期后按当前责任和正数 FIFO 前缀原子续问。不套用初始 prepare 优先，未知先恢复原请求；已发布 WAIT 的责任清零后退出，不据此核销 WMS 容量。覆盖多次 WAIT、旧响应、无 EXECUTING、普通入口消费及历史清理 | 同一 batch/Confirmation/FactProcessor，原 Motion/Allocation/Transport 继续原义务，不新增 timer、worker 或业务状态 |
| 连续 READY 接续（离线已实现） | 续行 READY 按本轮请求、供架集合及原动作交接正事实读取耗尽依据；不复用首次 READY 的全范围数量前提。两轮耗尽到唯一 current C，或原责任全部权威回库后由 9G 正常收口；清理前驱后继续恢复 | 同一 batch 与既有供架执行 owner |
| READY 接回供架执行（离线已实现） | 新决定精确接回 9B–9G；同架等待原动作成功及已知终位，独立架不等无关历史；原未终态 CTU03 不因 current 切换丢 owner。只用未闭合 Motion 的原动作约束同架直接依赖 | 现有 DrainRack、Motion/Allocation、Transport 及 batch |
| 真实容量接续与核销验收 | 只对原 READY 承诺未履约完就耗尽的分支，取得 WMS 内部容量关联、WAIT/READY/重放和权威回库核销证据；若现有实现足够则不改 wire，只有真实歧义才另评最小合同差额 | WMS 维护容量账与关闭；WES 提供冻结请求、分配和原动作时序证据 |

<a id="task9h-offline-closure"></a>

**9H 离线证据收口（截至 `187950fb`）。**下表是已提交内容与主要测试 owner 的索引，不合并累计历次测试数量，也不把旧快照绿灯称为本次重跑。测试路径均相对 `workline_plugins/automatic-picking/tests/`；`integration/` 归插件显式 PostgreSQL/进程验收，纯规则归插件 FAST，均不进入核心默认 pytest/QUALITY/HEAVY。核心安装/拓扑测试只证明其基础边界。

| 已提交范围 | 提交 | 主要测试 owner | 已有证据及验收边界 |
| --- | --- | --- | --- |
| 纯规则；首次 READY 读取、并发与混合成员 | `5ff2c3b3`、`d99cb32c`、`16606ae3`、`777ca840` | `test_drain_replenishment.py`；`integration/test_drain_replenishment_postgresql.py` | 给定/持久事实到瞬时求值、FIFO/独立在途及只读竞争；早期 63 项规则、41 项读取等仅证明当时范围，后续按各片传播回归，不代替事务或容量核销 |
| 单次 current 原子接续 | `a82d4cca` | `integration/test_drain_continuation_postgresql.py` | 同事务新 Confirmation、旧 current 退出、新 current 成立及责任交接；含并发、回滚、回库竞争、历史清理和迁移 `c094b575576f` |
| 补架 WAIT、责任归零 | `23cef5aa` | `integration/test_drain_wait_postgresql.py` | 固定到期点、新身份续问、未知恢复原请求、普通入口和零责任退出；不从本地退出推定 WMS 核销 |
| 连续 READY 接回 9B–9G | `963d90d0` | `integration/test_drain_ready_continuation_postgresql.py` | 两轮耗尽到唯一 current C、同架直接依赖/独立架、正常收口及清理前驱后恢复；精确位置/完整参数/结果由离线夹具给定 |
| 宿主持久扫描入口 | `4dd2fa5c` | `integration/test_drain_host_recovery_postgresql.py` | 注入现有插件装配，扫描到期 WAIT、successor READY 和退出 current 后原离场义务；不等同生产装配 |
| 真实 worker＋Beat：WAIT | `38b94db5` | `integration/test_drain_wait_worker_recovery.py` | 无 EXECUTING、暂停恢复、Beat 唯一续问和重启防重；仅本地 PENDING Confirmation，不发 WMS 请求 |
| 真实 worker＋Beat：successor READY | `604d79cf` | `integration/test_drain_ready_worker_recovery.py` | 清理前驱根后唯一创建/关联本地 PENDING CTU01、无 EXECUTING、重启防重；不派发、不证明到位 |
| 真实 worker＋Beat：原离场义务 | `da68d7f2` | `integration/test_drain_departure_worker_recovery.py` | current 退出及根清理后，无成功事实保持原义务，匹配已发布成功后原 Motion 收口；本片事实由夹具准备 |
| FactProcessor 真实进程交接 | `18f7ff5d` | `integration/test_drain_fact_worker_recovery.py` | `device-command` 应用发布后才交 `celery` batch 收口；已保存 Evidence 为检查点，不由父进程推进 |
| 原 CTU03 回调链真实进程 | `df97c730` | `integration/test_drain_callback_worker_recovery.py` | 真实 HTTP 接收 → `wms-fulfillment` → FactProcessor → batch 分阶段持久交接；接收不提前收口、重启/重放防重 |
| FactProcessor 领取提交后真实失效 | `187950fb` | `integration/test_drain_fact_claim_worker_recovery.py` | SIGKILL、原租约到期前不抢占、同 Evidence 到期后新 token 重领、发布后收口及重启防重；不复制共享租约完整矩阵 |

最近一片的新增真实进程案例 `1 passed`、五个直接消费者回归 `5 passed`、拓扑/所有权 `14 passed`，均无 skip；提交 `187950fb` 的 hook 完整 QUALITY 通过（FAST 为 `4321 passed, 5 skipped`），该暂存范围 HEAVY 为 NONE。该记录来自对应提交报告，不把核心 skip 算作插件验收，也不延伸为最终任务 11 的全链门禁。本次仅核对 Git、既有验收记录与文档，不重跑测试、QUALITY、HEAVY 或迁移。

**剩余门禁与下一步：**当前 9H 已批准离线范围未发现新的必补插件接线缺口，不因任务 11 的总清单尚未勾选而重复业务矩阵、共享派发或租约机制测试。

| 门禁 | 尚需依据/动作 | 责任及阻塞范围 |
| --- | --- | --- |
| WMS 容量补证 | [C1/C2](../../integration/wms-joint-confirmation-automatic-picking.md#task9h-wms-evidence-gates) 逐项给出实现/合同版本、容量关联及核销账务或等价时序证据，再联合核对正反例 | WMS 提供、双方核对；只阻塞原承诺未履约完的真实补架/核销验收，不推定必须新增字段 |
| 生产装配 | 已提交 `5c42941c` 按人工线搬运基线补齐组合根的显式搬运接线；启用配置和生产实例未变。隔离验证采用同一工厂，本线位置/冻结设备合同提供参数，未接机械臂 Handler 或完成确认；外部派发及现场验收仍待相应授权和证据 | 部署装配负责关联既有插件，batch/Confirmation/FactProcessor/原动作 owner 不变；搬运接线和隔离验收已提交，不以重复搬运验收延后工位内自动化业务；启用配置和生产实例仍未变 |
| 设备与身份输入 | 设备映射、精确扫码关联、PkgID、首批期限分别按目标路径补齐权威依据 | 只限制实际依赖该输入的路径；不阻塞已完成的显式输入离线验收，不猜值 |
| 后续业务与最终门禁 | 完成确认、已接纳后 FAILED 重决策仍未纳入本轮；任务 11 仍需最终目标分支的迁移、全等待点与完整正常闭环及所选门禁 | 按对应业务 owner 和任务 11 范围单独授权/验收；不由 9H 的指定检查点局部通过替代 |

恢复归属未变：自动 batch 负责准入、current、固定 WAIT 到期与业务接续；Confirmation 负责冻结原请求可靠交付；FactProcessor 负责 Evidence 领取、应用、发布；DrainRack/Motion/Allocation/Transport 保持原动作及直接因果义务，current 退出不转移或删除未终态原动作；Beat/宿主持久扫描只负责发现与唤醒。WMS 拥有容量账与核销。共享 drain Confirmation 的零插件真实派发已有核心 `tests/integration/wms_adapter/return_buffer_drain/test_production_wiring.py` 作为主要 owner，不为自动插件复制公共交付矩阵。

**下列为逐片历史记录：**各段“本片未接”“留下一片”“未提交/QUALITY 留提交边界”只描述该片验证时的范围；当前提交状态以上表为准。后续片补齐的能力不倒写为早期证据已经覆盖，也不把 `FirstDrainReadyReader` 当作仍存在的兼容入口。

**WMS 补证与阻塞范围：**联合确认 §9 只保留容量接续和回库核销两项证据，不要求 F1–F8 或新增字段整体冻结。既有联调的已履约轮次结束、剩余 FIFO 新申请可复用；不能升级为“原承诺未履约完的补架核销”已验收。设备映射、扫码关联、PkgID、首批期限和已接纳后 FAILED 仍只限制各自路径，不扩大阻塞上述离线事务。完成确认、失败重决策、bin-line-common 抽取、生产装配及真实派发不在本片。

**9H 首片实施：离线纯规则（已实现，未接持久执行）。**插件新增 `automatic_picking/drain_replenishment.py`，仅依赖标准库和现有 SDK typed outcome；冻结局部事实快照并返回瞬时求值分类、全部剩余责任及本次 FIFO 请求前缀，不创建 intent/Confirmation、current、timer 或物理动作，不注册 Handler 或接入 batch。局部 `reservation_operation_id` 仅是显式给定的首次 READY 引用，不是 wire/SDK/schema 新字段，也不证明 WMS reservation 活动。首次 WAIT/未知请求不得伪装为补架；请求未知、结果未发布或供架尚未交接时，先交回原 owner，不要求新候选快照。已发布 WAIT 使用显式冻结到期点，原责任全部满足可结束本地 WAIT；READY 零剩余不创建补架，仍交既有正常收口路径处理。

供架耗尽只使用当前 READY 集合及原 CTU03 交接正事实，不读取中间面历史或用失败覆盖交接。原 execution 成功正事实扣除责任，明确已交接且离开待派发 FIFO 的独立在途成员仍保留责任而不重复计数。实际 FIFO 与原顺序冲突、成员既不在队列也无明确在途依据时拒绝，不从缺失记录推断；位置未知、未合格、已分配或新成员中止请求前缀，不跳过。输入只按 execution ID 关联，不以同箱码替代身份。没有可请求成员时不产生零数量请求；该求值不承担 WMS 核销或实际持久关闭。

**首片验收与恢复所有权：**`tests/test_drain_replenishment.py` 为唯一纯规则 owner，63 项覆盖正常耗尽、多架交接、剩余范围、FIFO 阻塞/顺序、独立在途、WAIT 到期与清零、未知/未发布、旧结果身份、首次 WAIT 隔离及非法输入。先取得能力缺失 RED，并由补充反例取得“先恢复原义务再读候选”5 项 RED、原 READY 引用边界5项 RED，再完成最小实现。插件 FAST 显式排除 integration 后 237 项通过；核心安装、测试拓扑与插件所有权23项通过；两文件 Ruff/格式及 diff 检查通过；核心 HEAVY selector 为 NONE，插件规则由插件验收、不新增核心 mapping。纯规则本身无恢复 owner；未来调用/持久接续仍由现有自动 batch 负责，Confirmation、FactProcessor、Transport 和 WMS 的既有职责不变。独立 Reviewer 对固定三文件快照完成完整 review，未发现 P1/P2/P3，无代码修正；审查前后执行快照一致，闭环完成。随后仅补记本句验收结果，不使既有可执行验证证据失效。

**首片完成时的后续准入（现已由单次接续片完成）：**现有 wire 可用于单次 current 接续的离线事务验收，无需先新增字段或迁移人工消费者。已有纯规则仍只证明给定快照下的求值；WMS 原容量未履约完的接续及核销需补证后才可作为该真实分支的验收依据。本次不改实现、模型或迁移，未决设备/扫码/PkgID/期限继续只限制各自真实路径。

**9H 后续片：首次 READY 的持久事实离线读取（已实现，未接运行路径）。**新增插件内 `application/drain_replenishment_reader.py` 的 `FirstDrainReadyReader`，调用方持有原 WorkLine 锁；复用 `ReturnBufferDrainResultReader.for_operation`、Drain/Return/Allocation repository，读取精确当前原请求、已应用发布且由 current 消费的 READY、成员范围、`returned_ids` 和 `departure_handoffs`。该读口只将首次 READY 请求自身 operation 作为本地快照引用，不证明 WMS 容量账身份；不能直接用于后续 successor 或 WAIT，不读取最近请求或历史前驱。没有 current、原决定未知/未发布/未应用、首次 WAIT 或尚未消费的 READY 返回未就绪，继续原 batch/FactProcessor owner；身份、正文摘要、成员数量及 current Evidence 漂移明确拒绝。供架未耗尽时先交回原义务，不读取新 FIFO/位置来阻塞它。

供架耗尽后读取权威 FIFO、资格/占用和显式精确位置；原成功成员只读 current 已落地正事实，已闭合 Return/Allocation 不成为依赖。独立在途只从原 Allocation 已交接正事实、精确 typed READY、原 Transport Binding 与完整成员来源/目标证明，仍保留责任而从待派发范围排除；无交接或仅 Return 归档不推断在途。原责任总数不套用当前缓冲位容量门槛，已在独立动作中的成员不占当前缓冲位。读口返回既有纯快照，求值仍调用首片纯函数；不创建 Confirmation/current/动作、不保存求值、不注册 Handler、batch 或生产装配，不改 SDK/wire/schema。未决设备位置映射继续使用显式权威读口，不以投影或历史缺失猜位置。

**验收与恢复 owner：**插件 `integration/test_drain_replenishment_postgresql.py` 唯一拥有该读取组合的 PostgreSQL 验收，29 项覆盖当前身份/摘要/消费门禁、耗尽、多成员与新 execution 隔离、FIFO 未知队头、独立接纳/未接纳、原分配和 Transport 漂移、删除已闭合 Return/Allocation/中间 Motion 后新 reader/会话重新求值及无 EXECUTING。全业务表指纹和 ORM dirty/new/deleted 检查证明读口与求值不写数据；测试建材沿用既有离线 owner，不等同真实派发。使用本片独占临时 PostgreSQL，各例干净逻辑库由现有迁移 harness 建立；没有新增迁移。先取得缺失读口 RED，再由独立在途/当前容量反例取得真实 RED 并最小移除过严读取门槛；最终新 PostgreSQL 29 项、插件 FAST 237 项、核心安装/拓扑/所有权23项通过，Ruff/格式/diff 检查通过，HEAVY selector 为 NONE。只读口和纯规则不取得新的持久 owner：原 batch、Confirmation、FactProcessor、Transport 继续各自恢复；本片不接持久补架 WAIT、跨 successor current 或 WMS 核销。独立 Reviewer 已对固定三文件快照完成完整 review，未发现 P1/P2/P3，无代码修正；审查前后 SHA 一致，闭环完成。随后仅补记本文审查结果，可执行证据未失效。

**9H 首次 READY 读取的并发验收补片（只补测试，未接持久执行）。**沿用同一插件 PostgreSQL 测试所有者，增加三类既有 owner 推进与只读求值的两事务竞争：9C 应用已发布、精确成功的回库事实，独立 BIN_MOVE 已接纳后的 Allocation 交接，以及 9G 将原 CTU03 交接事实与正常 drain 归档一起提交。每类分别覆盖读口先持锁、owner 先提交、owner 修改后回滚；使用 `pg_blocking_pids` 观察实际 WorkLine 锁等待后才放行，不以调度延时推断互斥。读口先行时 owner 等到求值完成；owner 先行时新会话只读到提交后的完整事实；回滚不泄露 returned/handoff/归档进度，再由原 owner 重跑恢复。

验收同时核对精确原 operation 和本地首次 READY 快照引用、剩余责任和独立在途范围；接纳交接不产生回库成功，全部回库但尚未完成供架交接仍由 9G 处理，根归档后读口返回无 current 而不找历史 READY。正常收口保留原未终态 CTU03/Motion 及其恢复归属；不因本地 drain 关闭推定物理离场完成。SQL 记录与 ORM 状态检查约束读口只执行 SELECT，竞争前后 Confirmation 身份集合不变，重启式新 reader/会话求值前后全业务表指纹不变；不创建补架 Confirmation、不替换 current、不保存求值结果。读取本身不取得持久恢复 owner，继续由原 return allocation/batch、FactProcessor、Confirmation、Transport/Motion 扫描恢复。WMS 容量接续与核销细节仍待补证，已撤回新增字段前提；本片未进入 successor current、完成确认或已接纳后 FAILED 重决策。

本补片验证：独占 PostgreSQL 17.10、现有每例干净迁移 harness 下，该读口完整 PostgreSQL 38 项通过（新增竞争 9 项），插件 FAST 237 项通过；Ruff、格式和 diff 检查通过，核心 HEAVY selector 为 NONE。未发现生产缺口，仅修改本测试所有者及计划；完整 QUALITY 留到用户授权的提交边界。独立 Reviewer 已对固定两文件快照完成完整 review，未发现 P1/P2/P3，未提出生产修正；审查前后文件 SHA 一致，闭环完成。随后仅补记此结论，可执行测试快照未变。

**9H 首次 READY 多架与混合成员的只读组合验收（仅测试，未接持久执行）。**在同一 PostgreSQL 测试所有者内复用既有两架 READY、return allocation、departure 与 batch 夹具，新增三项组合验收。逐架交接按 READY 正序与逆序各验证一次：零交接和仅一架交接均继续 WAIT，全部应需供架取得原 CTU03 交接正事实后才允许剩余原成员成为请求范围；其中最后一架仅 ACCEPTED，保留原 Transport/Motion 继续恢复，不推定物理离场。首架权威成功后清理其已归档 Motion，新会话、新 reader 仍读取原 current 的交接正事实，不回查可清理面历史。

混合成员用例在同一首次 READY 范围内，通过既有 owner 建立一名已权威回库、一名独立普通架 BIN_MOVE、一名实际 FIFO 候选。独立动作未交接时仍按已分配队头阻止越序；接纳并由 owner 交接后，在途成员留在完整剩余责任中，但不重复计入可请求数量，实际请求仅为 FIFO 中的合格原成员。普通入口保留本地原 drain 成员归属，不表示 WMS reservation 核销已接线。删除已归档的成功/在途 Return、已完成原分配及成功离场 Motion 后重新读取，快照、原 operation 和本地首次 READY 快照引用、责任与前缀保持一致；未终态独立 Allocation、原请求和动作直接依赖继续保留，不当作可清理历史。

每次读取与纯求值前后核对全业务表指纹，并沿用读口 ORM 无新增/修改/删除检查；不创建补架 Confirmation、不替换 current、不保存求值。只读求值无新增恢复 owner，原 batch/Allocation、FactProcessor、Confirmation、Transport/Motion 继续承担各自职责。WMS 容量接续与核销细节仍待补证，已撤回新增字段前提；本片未启用 successor、持久补架 WAIT、完成确认、已接纳后 FAILED 重决策或真实派发。

本组合补片验证：独占 PostgreSQL 17.10、每例干净迁移数据库下，该读取组合完整 PostgreSQL 41 项通过（新增3项），插件 FAST 237 项通过；Ruff、格式与 diff 检查通过，核心 HEAVY selector 为 NONE。未发现生产缺口，仅修改插件测试和计划，完整 QUALITY 留到提交边界。独立 Reviewer 已对固定两文件快照完成完整 review，未发现 P1/P2/P3，无需生产修正或追加测试；审查前后 SHA 一致，闭环完成。随后仅补记此结论，可执行测试快照不变。

**9H 确认清单收窄（本轮仅文档）。**[联合确认 §9](../../integration/wms-joint-confirmation-automatic-picking.md#9-9h-正常补架的双方冻结确认清单)保留原导航，撤回旧 F1–F8 必选提案及新增字段 JSON，改为两项 WMS 补证、既有人工线基线和差额验收向量，不再要求双方确认 WES 私有 current 结构或先改两 operation 全部消费者。2026-09-23 Accepted 不自动覆盖未补证细节；已有文档 review/测试记录只证明各自当时快照，不作为外部接受证据。本次只修订主合同、插件设计、本计划和现有确认清单，不改生产代码、测试、机器合同或持久执行，不提交、不部署、不启用真实派发。

**本轮文档闭环 review：**已交叉核对人工线基线、主合同/设计/计划/确认清单的状态出口、原子交接、唯一恢复 owner、测试范围及 WMS 补证门禁。修订中纠正人工线章节锚点，并补明首次 READY reader 的请求数量前提不能直接用于 successor；两点已闭合，未留文档一致性问题。既有离线快照字段仅保留实现说明，不升级为外部合同或生产字段；容量接续与核销仍为 C1/C2 待补证，不称联合验收通过。纯文档仅核对链接/锚点、表格、代码围栏、撤回提案残留及 diff，不重跑代码测试、QUALITY、HEAVY 或迁移。

**9H 单次 current 原子接续实施（离线已验证，review 已闭环）。**复用 `AutomaticPickingDrainFlow`，仅在显式安装既有 `FirstDrainReadyReader` 的离线应用中启用接续，不改部署装配。先恢复原义务，再调用已验收纯规则；在同一 WorkLine 锁事务中创建现有 drain Confirmation、退出旧 current、成立新 current，并将未闭合 Allocation 的业务 owner 引用移交新 current。原 request/Transport/Binding/结果与因果 Evidence 不改写。新 current 保留全部剩余 execution，`request_return_ids` 只保存本次实际请求成员；`required_slot_count` 按后者校验，独立在途不重复计数。

新增的 `started_operation_id` 只保存“原 READY 已开始”的本地正面依据，区分初始准入与原业务续行，不是 WMS reservation 引用，也不进入 DTO/SDK。已有 `return_ids/returned_ids` 继续表达本地责任与成功进度。迁移 `c094b575576f` 从旧记录原 `return_ids` 回填请求范围，不臆造首次 READY：该依据只在匹配 typed READY 应用/恢复时冻结；有接续差额时拒绝有损降级。当前记录与原直接动作能分别从既有 batch/Confirmation/FactProcessor/Transport owner 恢复，不追可清理旧根。

`integration/test_drain_continuation_postgresql.py` 唯一拥有新增单次事务验收：正常接续、独立在途、并发唯一性、Confirmation/旧退出/新 current/Allocation owner 四点崩溃回滚、回库事实先于/后于切换及原 owner 回滚、清理旧根后新实例处理原 CTU03/BIN_MOVE、旧 READY 重放、当前响应按请求范围应用、存量升级与有损降级保护。先取得“供架已耗尽仍返回同一 current”的真实 RED，再最小修正；沿用已有纯规则和只读夹具，不复制首次 READY 组合矩阵。

**单次接续片当时的边界：**后续 READY/WAIT 的当前结果可以可靠保存并校验；该片首次 READY 专用读口尚不能读取 successor。持久到期重问、WAIT 责任归零退出及连续第二次接续当时留待后续，现已分别由 `23cef5aa`、`963d90d0` 补齐；不扩大该片历史测试的证明范围。WMS C1/C2 容量接续与核销继续待补证，设备映射、扫码关联、PkgID、期限和已接纳后 FAILED 不猜值；不接完成确认、不做 bin-line-common 抽取、不提交、不部署、不启用真实派发。

**本片闭环 review：**独立 reviewer 固定基线 `03eeadfcf08f3bd0020e2e88803bc1916cf425fb` 与 12 文件 SHA256，核对同一事务、唯一 current、完整责任/请求子集、原分配与动作因果、存量迁移、旧根清理和测试所有权，未发现需修正的 P1/P2/P3。审查前后指纹一致、index 为空；reviewer 未重复执行测试或有状态门禁。其后只补记本文证据，不改变已评审的可执行快照。

**本片验证：**独占 PostgreSQL 17.11、每例干净迁移库；最终 drain/回架分配/运动前求值 12 文件 PostgreSQL 组合回归 361 项全部通过，包含新增事务验收 18 项，无失败、错误或 skip；6 条警告来自既有 schema 比对的 Evidence/PickingTask 外键循环。插件 FAST 237 项、迁移 selector 指定的核心 HEAVY 8 项、插件安装 19 项、测试拓扑与所有权 14 项均通过，无 skip；Ruff、格式、迁移单 head 与 diff 检查通过。额外 SDK 边界检查有 1 项基线失败：旧分类期望未包括 HEAD 已有的 `PickingMaterialIntent` / `MaterialMovementReportIntent`，SDK 与该断言相对 HEAD 均无变化，独立复现后不扩面修正或放宽断言；不据此宣称完整 QUALITY 通过，完整门禁留到授权提交边界。

**提交边界补齐：**首个 Commit hook 在脚本测试阶段发现 `test_select_heavy_tests.py` 的精确迁移清单漏列本片迁移；仅追加 `c094b575576f` 的现有路径，未改 selector、mapping 或断言语义。定向复现/验证及该单行 diff 审阅闭合后，重新运行完整 hook；原插件、迁移与 PostgreSQL 验收的文件指纹未变。

**9H 补架 WAIT 实施片（离线已验证，review 已闭环）。**基于 `a82d4cca`，将原首次 READY 读口直接替换为插件本地 `DrainReplenishmentReader`，读取范围仅为首次 READY 或已持久开始依据的补架 WAIT；无兼容入口，不接受 successor READY 作为连续耗尽依据。复用现有 DTO、纯规则、`continue_current()` 和 WorkLine 锁，在固定到期点后按当前完整责任及正数 FIFO 前缀创建新 Confirmation 并原子交接；新 WAIT 从自身首次接收固定新的到期点，原响应重放和重启不刷新期限。数据库微秒到期点保持不变，投影到毫秒纯规则前避免同毫秒提前到期。

已匹配且应用发布的补架 WAIT 在原 execution 全部权威回库后可退出 current，不等待到期、不发零数量；不从箱码、ACK、ACCEPTED 或缺失记录推定回库。普通入口仍可依法承接未分配成员，并把原分配成功交给当前责任 owner。已创建的当前 Confirmation 和事实发布先闭合，原 CTU03 等直接动作在 current 退出、旧根清理后继续由既有 Motion/Transport owner 恢复。未知/未发布请求继续原义务；到期但无合格连续前缀时保留责任。停用禁止新续问，已完成责任退出及原义务恢复不因停用丢失。

新增持久验收唯一归插件 `integration/test_drain_wait_postgresql.py`，复用既有建材与竞争锁验证；原单次交接用例只把 WAIT 断言限定为到期前，首次读取用例只同步读口名称。纯规则的现有快照允许无需候选的阶段缺少缓冲位，实际候选请求仍严格要求权威位置；该边界的两项 FAST 回归归既有 `test_drain_replenishment.py`。未改持久模型、迁移、SDK、宿主、worker 或部署装配；连续 READY 供架、完成确认和已接纳后 FAILED 重决策仍不接，WMS C1/C2 仍仅阻塞原容量未履约完时的真实接续/核销验收。

**首轮 review 修正：**独立 reviewer 发现一项 P2：责任已清零仍无条件读取 buffer，输入不可用会保留本应退出的 WAIT。先以 active/inactive 两项 PostgreSQL 反例和一项纯规则反例取得真实 RED，再让确定出口先于现场候选读取，不伪造位置；原请求、Evidence、期限及全量权威回库门禁保持不变。首轮未完成的组合回归在 29 项通过时主动中止，不作为最终验收；修复后的最终快照重新验证，并由同一 reviewer 一轮完成旧意见闭环和 fresh full review。

**WAIT 片闭环 review：**同一独立 reviewer 对固定 9 文件 SHA256 完成 P2 闭环和修复后完整复审，未发现剩余 P1/P2/P3。确定清零出口不再读取 buffer/FIFO/位置，候选请求仍拒绝缺失位置；身份、期限、原子交接、原动作 owner、普通入口和历史清理边界均已核对。审查前后文件指纹一致、HEAD 为 `a82d4cca`、index 为空；其后仅补本文验证证据，不改变已评审可执行快照。reviewer 未重复运行测试或有状态门禁，不把未完成的最终组合或现场验收称为通过。

**WAIT 片最终验证：**独占 PostgreSQL 17.11、每例干净迁移库下，WAIT/单次接续/首次 READY 读取/初始 drain 及普通入口、source owner 隔离的组合验收 131 项全部通过，含新增 WAIT 24 项，无失败、错误或 skip。插件 FAST 239 项通过（现有纯规则 owner 含 65 项），核心测试拓扑/所有权/插件安装 33 项通过；7 个变更 Python 文件的 Ruff/格式、文档结构、旧类名残留和 diff 检查通过。HEAVY selector 为 NONE，本片无 schema 或宿主变更；完整 QUALITY 留到授权提交边界。最终仅更新本文证据，HEAD/index 与其他工作区现场不变，未提交、未部署、不启用真实派发；不以离线结果代替 WMS C1/C2 或连续 READY 供架验收。

**9H 连续 READY 实施片（离线，未接生产装配）。**基于 `23cef5aa`，`DrainReplenishmentReader` 直接读取 successor 当前请求的 typed READY，沿用本次 `request_return_ids` 校验、完整剩余责任、供架集合和交接正事实；`started_operation_id` 仍只作本地开始依据，不回读已退出根。复用 9B–9G 创建并关联 CTU01、按冻结面序换面、return_batch/BIN_MOVE、departure 决定和 CTU03；不新增 DTO、模型、字段、业务状态、timer 或基础层流程。

验收发现同架真实缺口：旧 CTU03 已接纳释放窗口后，原链可提前创建该架下一轮 CTU01。最小修正只从当前 WorkLine 未闭合 drain Motion 的原离场引用约束同架直接依赖；原 owner 确认匹配成功、已知终位且结果已发布后退出，独立架不等待旧架未知结果。新轮创建独立 DrainRack/Binding，旧轮到位事实不充当新到位。原 CTU03 的 Motion/Transport owner 不因 current 切换或根历史清理丢失。

`integration/test_drain_ready_continuation_postgresql.py` 唯一拥有新增跨轮组合：两轮 READY 耗尽到唯一 current C、WAIT 后 READY 接回、多面与正常收口、同架/独立架、未发布/未知结果、并发、创建与切换崩溃回滚、原回库 owner 三种竞争顺序、旧 READY 重放及清理两轮根后的独立恢复。只复用已有夹具和真实 PostgreSQL 事务，不复制公共派发矩阵；WAIT 文件仅把旧“READY 不读取”断言改为“尚无本轮供架交接则等待”。batch 仍拥有 current、恢复扫描与业务接续；Confirmation、FactProcessor、DrainRack、Motion/Allocation 和 Transport 保持原责任。

**本片边界：**C1/C2 仍待 WMS 提供容量接续与核销证据；显式离线结果不升级为外部联合验收。设备映射、扫码关联、PkgID、期限及已接纳后 FAILED 只限制各自真实路径。暂不接完成确认或失败重决策，不做 bin-line-common 抽取；不提交、不部署、不启用真实派发。无 schema 或宿主变更，迁移未失效；完整 QUALITY 留到授权提交边界。

**连续 READY 片闭环 review：**独立 reviewer 对固定 6 文件 SHA256 完成完整只读审查，未发现 P1/P2/P3；确认当前请求/完整责任、原子交接、同架直接依赖、独立架推进、原动作恢复和闭合历史边界自洽。审查前后可执行文件指纹一致、HEAD 为 `23cef5aa`、index 为空。另将主合同状态单行澄清为“已离线实现、未接生产装配”，并完成该纯文档差额复核；不改变业务/wire 合同。Reviewer 未重复运行测试或有状态门禁。

**连续 READY 片最终验证：**独占 PostgreSQL 17、每例干净迁移库下，新增连续 READY、补架 WAIT、事实读取、进场与正常收口五文件组合 137 项全部通过，含新增 17 项，无失败、错误或 skip；本片无 schema 变更，明确排除 3 项既有升级/降级验收。插件 FAST 239 项、核心安装/测试拓扑/所有权 33 项通过；4 个 Python 文件 Ruff/格式、文档结构及 diff 检查通过。HEAVY selector 为 NONE，插件验收不进入核心 selector。先取得 successor READY 读取缺失及同架提前进场两项真实 RED，再完成上述最小修正；扩展测试中两处夹具调用时序/字段读取错误已在最终组合前修正，不放宽业务断言。最终组合与已评审可执行指纹一致，其后仅补记本段证据；完整 QUALITY 留到授权 Commit hook。未提交、未部署、不启用真实派发。

**9H 宿主持久扫描入口离线验收。**基于 `963d90d0`，新增插件 `integration/test_drain_host_recovery_postgresql.py`，测试中重建既有离线插件装配，注入 `PickingTaskPlanActivationService`；通过公开 `activate_batch()` 使用真实工作线发现、插件身份和 WorkLine 事务锁，进入现有 `advance_completed_in_session()`。不替换宿主扫描、Repository 或 batch 方法，不改部署组合根；该验收不代表生产已装配 drain，也不代表真实 worker/Beat 已验证。

只补三个入口检查点：无 EXECUTING 且丢失通知时发现到期补架 WAIT，按原责任产生唯一续问；清理前驱根后恢复 successor READY，并发宿主扫描只创建一次精确 CTU01；current 已正常退出且两轮根/原请求已清理后，宿主扫描仍按原 Motion/Transport 接入迟到成功事实并归档原离场义务，不产生新动作或重开 current。重建扫描实例与重复扫描保持冻结请求、动作身份和业务快照。复用现有 WAIT、successor、正常收口和清理夹具，不复制业务状态/崩溃矩阵。

宿主入口只负责持久发现及事务调用，业务 current/到期续问仍归自动 batch；Confirmation、FactProcessor、Motion/Allocation/Transport 和 WMS 容量账所有权不变。C1/C2 仍待补证，设备映射、扫码关联、PkgID 等未决值不猜；不接完成确认、已接纳后 FAILED 重决策，不新增模型、状态、timer 或 bin-line-common。后续真实 worker 离线验收只补队列/进程唤醒和周期恢复边界，待对应装配与环境就绪，不重跑本片业务矩阵。

**宿主入口片验证与闭环 review：**独占 PostgreSQL 17、每例干净迁移库下，最终三个入口验收全部通过，无失败、错误或 skip；测试拓扑/所有权 14 项通过，Ruff/格式、文档结构与 diff 检查通过，HEAVY selector 为 NONE。独立 reviewer 对固定两文件 SHA256 完成 scoped full review，确认真实宿主发现/锁调用、业务阳性断言、唯一 current/动作身份及清理后的原 owner 恢复，无 P1/P2/P3；首尾 HEAD 为 `963d90d0`，index 为空，文件指纹一致。未发现需要生产修正的接线缺口，仅新增插件验收和本计划记录，未重跑已闭合业务矩阵或完整 QUALITY；后者留授权提交边界。未验证真实 worker/Beat，未改生产装配、未提交、未部署或启用真实派发。review 后仅追加本段证据，可执行测试指纹不变。

**9H 真实 worker/Beat 的到期 WAIT 离线验收。**基于 `4dd2fa5c`，插件新增 `integration/drain_wait_worker_runtime.py` 与 `integration/test_drain_wait_worker_recovery.py`，只补子进程和周期唤醒边界。复用既有 broker harness 的真实 worker、readiness、隔离前缀及进程/连接清理，两次启动均显式固定 `pool="prefork"`，不受外部 pool 环境变量覆盖；测试启动时显式追加插件测试模块，不改共享 harness 或生产组合根。worker 在自己的 session factory 上装配既有插件 batch、drain 与宿主扫描服务，源码路径绑定当前 checkout 并留证，避免误用已安装的旧插件副本。Beat 仅保留已有计划扫描任务，沿用当前 10 秒周期、参数、过期配置与 `celery` 路由；不使用 harness 的手工发送入口，不启用其它周期派发。

预置的是已开始补架后的确定 WAIT 检查点及回程资格的已完成历史依据，不重演原供架或创建物理动作。真实 FIFO/wire/资格读口先校验该快照；工作线启用但没有 EXECUTING。暂停整个 worker 进程组后，由 Beat 发布任务且数据库不得推进；恢复后扫描创建唯一续问并原子交接 current，固定到期点不刷新。随后停止并重建 worker/Beat，以新的隔离 broker 前缀和原数据库再次扫描，当前请求与成员范围不变。发布者 PID 所属进程组必须对应两次真实 Beat（区分 uv 启动器与 Beat 子进程），消费 PID 对应两个新建 runtime；任务身份、队列、实际加载源码及前后数据库事实共同形成证据，不以子进程存活或返回成功单独判定通过。

本片只保存新的 PENDING Confirmation，不运行 WMS、Transport 或 Command 派发；WMS HTTP 接收数必须为零，Transport 为空，预置已完成命令的身份、摘要、状态和尝试次数保持不变。Beat 只提供周期唤醒，宿主负责工作线发现/锁，current/固定期限和业务接续仍归 batch，既有 Confirmation/FactProcessor/原动作 owner 不变。C1/C2 仍待 WMS 容量账补证，设备映射、扫码关联、PkgID 等不猜；不接完成确认或已接纳后 FAILED 重决策，不新增生产模型、状态、timer 或 bin-line-common，不启用生产装配、真实派发、提交或部署。不重跑 READY/回库/离场业务矩阵，也不将本片写成完整 worker/Beat 或正常闭环验收。

本片验证与闭环 review：在外部刻意设置 `TRANSPORT_TEST_POOL=solo` 的条件下，真实 PostgreSQL/Redis、prefork worker 与 Beat 的单案例验收 **1 passed，0 skipped**（27.69 秒）；两次发布、消费和 runtime 均精确留证，扫描结果依次为 `1/0`。测试所有权/拓扑检查 **14 passed**，两份新增文件 Ruff 检查和格式检查通过，HEAVY selector 为 `NONE`，文档与 diff 检查通过。首轮 review 发现的 pool 环境覆盖缺口已通过两处显式参数修复；同一 reviewer 完成闭环与当前范围 fresh review，无剩余 finding。生产代码、共享 harness、schema 和生产装配均未改；本片只覆盖暂停恢复与停止后重建，不宣称事务提交/ACK 临界点崩溃矩阵、完整任务 11 或生产业务验收完成。证据对应基线 `4dd2fa5c` 与本片测试工作树；未提交、未部署。

**9H successor READY 的真实 worker/Beat 离线验收。**基于 `38b94db5`，沿用 `drain_wait_worker_runtime.py` 与既有 worker/Beat 启停、源码路径留证和隔离 broker，新增 `integration/test_drain_ready_worker_recovery.py` 一个进程级检查点。复用 successor、精确进场参数及前驱根清理夹具，预置已发布 READY 和当前成员，不重演 9B–9H 业务矩阵；工作线启用且没有 EXECUTING。测试子进程在自己的 session factory 上补装现有 `DrainInboundFlow`、`ReliableRackTransportCreator`、窗口服务和真实 Repository，rotation/departure 保持未装配；只由 Beat 的既有计划扫描任务经 `celery` 队列推进，两次 worker 均显式使用 prefork。位置、面、模板及关联身份全部来自显式离线输入，不代表生产设备映射已确认。

验收同时核对唯一新增的本地 CTU01、完整 source/target/face、READY Evidence/correlation、DrainRack→Binding→Transport 关联，新增 Transport 必须保持 PENDING、尝试次数为零且没有发送或到位依据。前驱根及其原请求确已删除，当前 drain/完整成员与原有义务不变；重建 worker/Beat 后按原身份恢复、不新增请求或物理动作。真实发布者进程组、消费 PID、队列和实际加载的进场源码共同留证；首次扫描产生一次本地创建，后续扫描均不新增，不对合法周期扫描次数设额外限制。WAIT 无进场参数时保持原测试装配路径，并做其聚焦回归。

恢复 owner 不变：Beat 唤醒，宿主发现/加锁，batch/drain 解释当前 READY，DrainInbound 通过已有基础服务创建与关联原进场义务，既有 Transport owner 承接技术恢复。测试 provider 禁止提交，WMS HTTP 接收必须为空；不启用 WMS/Transport/Command 派发，不注入到位结果，不接换面离场、完成确认或已接纳后 FAILED 重决策，不新增模型、状态、timer 或 bin-line-common。C1/C2 仍待 WMS 容量接续与核销补证；本片不代替外部联合验收或生产装配，也不覆盖 current 退出、根清理后原离场义务的真实进程检查点。

**successor READY 进程片验证与闭环 review：**独占 PostgreSQL 17、每例干净迁移库和隔离 Redis 下，最终 READY＋原 WAIT 两文件聚焦验收 **2 passed，0 skipped**（42.71 秒）；外部设置 `TRANSPORT_TEST_POOL=solo`，实际启动仍由显式 prefork 保证。两例各留存两次 Beat 发布、消费和 runtime，结果均为 `1/0`，队列均为 `celery`；WMS 接收为空，新增 CTU01 保持 PENDING/零发送且无到位，既有记录与 current 完整快照保持不变。拓扑/测试所有权 **14 passed**，两份变更 Python 的 Ruff/格式与 diff 检查通过，HEAVY selector 为 NONE。初轮仅修正新测试把 SDK 类型名误当 Transport 正文字面量的断言，依据既有合同采用 `RACK/location_code`；未发现生产接线缺口。独立 reviewer 对固定三文件 SHA256 完成一次 scoped full review，无 P1/P2/P3，首尾 HEAD 为 `38b94db5`、index 为空；之后仅补本文证据，可执行指纹不变。未重跑已有领域矩阵或完整 QUALITY，后者留授权提交边界；未提交、未部署、不启用生产装配或真实派发。原离场义务的真实进程恢复及 C1/C2 外部补证继续保留。

**9H current 退出及根清理后原离场义务的真实 worker/Beat 离线验收。**基于 `604d79cf`，沿用测试子进程组合根及既有启停/隔离/源码留证机制，新增 `integration/test_drain_departure_worker_recovery.py` 一个检查点，复用宿主入口的 successor、正常收口及根清理夹具。两个原离场 Motion/CTU03 在验收开始前作为历史检查点准备；current 已退出、两轮根及原 drain 请求已清理、没有 EXECUTING。未闭合 Motion 引用的来源 Evidence、departure READY/请求、Binding、Transport/Member 和结果证据仍属活动依据，不能随根删除。

测试子进程补接既有 `DrainRotationFlow` 和 `DrainDepartureFlow`，沿真实 batch 的运动前恢复入口触达原义务；当前 drain 读取使用现有 Repository，不装配新分配、离场决定或重试端口。显式未知 buffer 返回 None，不推断 EMPTY，不依赖新位置/扫码/PkgID。三个独立 prefork worker/Beat 阶段只由既有 `celery` 计划扫描任务推进：无匹配成功事实时完整业务快照不变；夹具按原动作接收、应用并发布显式权威成功结果后，重建进程绑定原 Evidence 并归档原 Motion；再次重建后保持收口快照、不重开 current。夹具结果应用不调用 batch 收口，真实进程扫描才负责归档；这不代表 FactProcessor 或回调链的真实进程验收。

每阶段留存 Beat 进程组、消费 PID、任务名/队列、扫描结果及实际 departure 源码路径；核对原 Motion 的来源/到位/决定/执行/结果引用不变，原记录集合不增长，非 Motion 数据不因扫描变化。给定成功结果只更新原 Transport/Member/Evidence，不能换身份或正文；WMS HTTP 接收为空，不启用任何外部派发，不创建新动作。恢复 owner 仍为 Beat 唤醒、宿主发现/锁、batch→drain→原 departure/Motion 收口，Transport/FactProcessor 与 WMS 容量账各守原职责。C1/C2 继续待补证；不接完成确认、已接纳后 FAILED 重决策、bin-line-common 或生产装配，不将本片扩为正常业务闭环或崩溃矩阵。

**原离场进程片验证与闭环 review：**独占 PostgreSQL 17、每例干净迁移库和隔离 Redis 下，新增三阶段真实 worker/Beat 检查点 **1 passed，0 skipped**（35.51 秒），共享 runtime 的 WAIT/READY 两个直接消费者 **2 passed，0 skipped**（43.24 秒）；不重跑领域矩阵。外部设置 `TRANSPORT_TEST_POOL=solo`，三个 worker 仍显式 prefork。新增案例各阶段 runtime/Beat 发布/消费/结束记录均为三次，扫描结果 `0/2/0`、队列 `celery`；无成功时完整快照不变，匹配已发布成功后仅两个原 Motion 归档，再次重建后全快照不变，WMS 接收为空、对象集合不增长。拓扑/所有权 **14 passed**，两份 Python Ruff/格式、文档结构及 diff 检查通过，HEAVY selector 为 NONE。未发现生产缺口；独立 reviewer 核对三文件固定 SHA256、真实原 owner、活动依据和进程清理后完成一次 scoped full review，无 P1/P2/P3；首尾 HEAD 为 `604d79cf`，index 为空。review 后仅追加本文证据，可执行指纹不变。完整 QUALITY 留授权提交边界，未提交、未部署、不启用生产装配或真实派发；FactProcessor/回调链真实进程、C1/C2 外部证据及完整任务 11 均未由本片宣称完成。

**9H FactProcessor 真实 worker/Beat 离线验收。**基于 `da68d7f2`，复用已退出 current、两轮根及原 drain 请求已清理的原离场检查点，新增 `integration/test_drain_fact_worker_recovery.py` 一个进程级用例。父进程通过既有夹具的 `process=False` 只准备原 Transport/Member 成功及已保存、尚无业务 `published_at/decision_digest` 的两个 TRANSPORT_RESULT；不直接调用 FactProcessor、插件应用或 batch 收口，不将该准备过程描述为回调链真实进程验收。

测试子进程在原 departure 装配上接入真实 ScanFlow consumer、StaticPluginBinding 和 `build_execution_runtime`，暴露真实 execution 与 plugins，保留既有 worker 启动身份校验。两个独立 prefork 消费者共用隔离 broker 前缀，分别按正式路由消费 `celery` 与 `device-command`；只由既有 Beat 的计划扫描和事实扫描发布，不通过 harness.send 强行指定队列。先只运行计划消费者，核对未发布时完整快照不变；暂停其进程组后启动事实消费者，核对原 Motion 结果引用与 Evidence 摘要/发布时间持久落地、Motion 仍未归档；恢复计划消费者后才归档原 Motion。重建两类消费者及 Beat 后，两队列均实际消费而收口快照不变。子进程会话、任务身份、队列、发布进程组、消费 PID 与当前 FactProcessor 源码共同留证。

`device-command` 会初始化设备 runtime，因此本用例显式沿用既有配置测试的超时输入及固定队列名，保持真实 fail-closed 初始化，不提供设备端点或业务映射。设备/WMS/Transport 派发任务均不进入 Beat 调度；测试 WMS adapter 禁止发送，HTTP 接收为空。每阶段保持原 Evidence/Action 集合不增长及原执行身份，FactProcessor 负责业务证据领取/应用/发布，batch 负责原 Motion 收口，Beat 只唤醒，原 Transport 和 WMS 容量账 owner 不变。不改生产代码、共享 harness、DTO/schema 或既有 publisher 的唤醒返回，不新增状态、timer 或 bin-line-common；不接回调链、完成确认、已接纳后 FAILED 重决策、生产装配或真实派发。C1/C2 继续待补证，领取后崩溃/租约回收及完整任务 11 未由本片宣称完成。

本片最终验证：新增真实进程案例 `1 passed`（37.92 秒），共享装配的 WAIT、successor READY、原 departure 三个直接消费者回归 `3 passed`（82.51 秒），测试拓扑/所有权 `14 passed`，均无 skip；三个 Python 文件的 Ruff 检查和格式检查、`git diff --check` 通过，插件测试对应 HEAVY selector 为 NONE。新案例两轮共四个 runtime，计划任务结果为 `[0, 2, 0]`、事实任务为 `[2, 0, 0]`，两队列均有真实消费与重建防重证据。独立只读 scoped full review 无 P1/P2/P3 finding，核对期间四文件快照不变。验证基线为 `da68d7f2`，index 为空；未提交、部署或启用生产装配，完整 QUALITY 留待获准提交时由门禁执行。该结论只覆盖既有离场义务的发布与收口，不包含回调链、领取后崩溃/租约回收或完整任务 11 验收；C1/C2 继续待 WMS 补证。

**9H 原 CTU03 回调链真实进程离线验收。**基于 `18f7ff5d`，复用 current 已退出、两轮根及原请求已清理、无 EXECUTING 的两个原 CTU03 检查点，新增一个 `integration/test_drain_callback_worker_recovery.py` 用例及仅供测试子进程加载的 `drain_callback_http_runtime.py`。父进程只准备原 ACCEPTED 动作和显式结果正文；不调用回调保存、Transport Evidence 应用、outcome 发布、FactProcessor 或 batch 业务推进。成功 wire 正文按现有 parser 省略 `position_unknown`，不把内部规范化的 false 当作合法 wire 值；初收为 `202/RECEIVED`，同身份同正文重放为 `200/DUPLICATE`。

HTTP 子进程通过实际本地 TCP、Uvicorn、现有 events 路由及真实 Transport runtime 接收。先在消费者未启动时核对 Receipt/Evidence 已提交、原动作和 Motion 未提前成功/收口；重建 HTTP 子进程并重放后，原业务快照不变。随后三个独立 prefork 消费者共用隔离 broker 前缀，依正式路由分阶段启动：`wms-fulfillment` 应用原 Transport 结果并经 `InstalledPluginTransportOutcomePublisher` 形成唯一业务 Evidence，`device-command` 的真实 FactProcessor 应用并发布，最后 `celery` 经原 batch/drain/departure owner 归档 Motion。测试 runtime 补装现有 outcome publisher；只在显式 callback 测试标志下丢弃加速通知，真实 Beat 仅调度上述四类持久扫描，证明接续不依赖通知。各阶段核对业务记录集合、冻结正文及原因果身份；业务变化允许对应审计时间更新，不豁免其他业务字段。第二轮重建 HTTP、三类消费者及 Beat 后重放，要求四类任务均实际消费，闭合快照不变。

恢复 owner 不变：入口事务保存原 Receipt/Transport Evidence；TransportService 负责 Evidence 应用及 outcome 发布进度；冻结插件负责形成业务 Evidence，FactProcessor 负责业务应用/发布；batch 负责原 Motion 收口，Beat 只唤醒。测试归插件 integration owner，不重跑 FIFO、多架补架、WAIT、回库竞争或供应商业务矩阵，不修改共享 harness、生产代码、DTO/schema、设备映射或生产装配。测试服务只接收显式回调，WMS mock 只检测禁止的外发；不调度 WMS/Transport/设备派发，不创建新动作，不接完成确认、已接纳后 FAILED 重决策或 bin-line-common。C1/C2 仍待 WMS 补证，本片不证明 WMS 容量账或现场物理结果，不将通知丢失/阶段重启等同领取后崩溃/租约回收或完整任务 11。

本片最终聚焦验证：新增回调链真实进程用例 `1 passed`（37.48 秒），共享 runtime 的 WAIT、successor READY、原离场及 FactProcessor 四个直接消费者回归 `4 passed`（116.26 秒），拓扑/所有权 `14 passed`（1.08 秒），均无 skip。实际留证为三个 HTTP 进程、六个 worker runtime，三个正式队列，四类扫描的发布/消费/结束各八条，四类任务结果各为 `[2, 0]`；测试没有要求固定 Beat tick 数。三个 Python 文件 Ruff/格式检查、文档结构及 diff 检查通过，HEAVY selector 为 NONE。未发现生产缺口；未改 publisher 返回 false 的既有语义，业务 Evidence 继续由 FactProcessor 持久扫描接续。完整 QUALITY 留授权提交边界，WMS C1/C2 及未覆盖边界保持原状态。

独立只读 scoped full review 已完成，无 P1/P2/P3 finding：核对真实 HTTP 入口、三个正式队列、原 publisher、分阶段发布/收口、额外进程清理和直接测试 owner；首尾 HEAD 均为 `18f7ff5d`、index 为空、四文件 SHA256 一致。review 后只追加本段结论，可执行指纹不变。未提交、未部署、不启用生产装配或真实派发，未把本片扩展为回调通用冲突矩阵、领取后崩溃/租约回收或完整任务 11 验收。

**9H FactProcessor 领取提交后真实失效与租约回收离线验收。**基于 `df97c730`，复用原 CTU03 outcome 已发布、业务 Evidence 未发布的检查点，保留 current 已退出、两轮根及原请求已清理、无 EXECUTING。新增一个 `integration/test_drain_fact_claim_worker_recovery.py` 用例，测试 runtime 仅在显式 `claim_stop` 输入下包装当前 FactProcessor 的首次 `_prepare_fact` 入口：真实领取事务已提交，独立会话确认原 token/到期点，再以一次性测试标记停住子进程。父进程只在检查点准备阶段调用既有夹具；进入验收后不直接领取、应用、发布或推进业务，不改数据库租约列、不清空 token、不缩短既有租约。

首阶段由真实 Beat/`device-command` 领取，独立数据库快照确认原 Evidence claim 已提交、业务发布和 Motion 尚未发生；随后以 SIGKILL 终止整个消费者进程组，保留已提交 claim。第二阶段重建隔离 broker 的替换消费者及 Beat，到期前必须实际完成一次空扫描且全业务快照、原 token/到期点不变；越过原持久到期点后，真实扫描重领同一 Evidence，记录新的 token/PID，再经原插件应用/发布，Motion 仍未归档。最后才启动 `celery` batch owner 收口。第三阶段重建两个消费者及 Beat，实际消费后闭合快照不变。验收保留唯一一次预期中断任务，其余已结束任务须成功；原身份/正文/记录集合不增长，设备及 WMS 请求为空。

租约、领取与重领仍归 FactProcessor/InboundEvidenceRepository，插件解释原结果，batch/原 Motion owner 负责收口，Beat 只唤醒。测试不复制共享 claim/fencing/退避机制，不新增通用故障框架、timer、授权模型或业务状态；停点只在测试子进程显式装配。复用已有 HTTP/Transport 前半段证据，不重跑回调、FIFO、多架补架、WAIT 或供应商业务矩阵；共享 runtime 的原五个进程级消费者作为直接回归。不改生产代码、共享 harness、DTO/schema 或生产装配，不发 WMS 请求、不创建新动作，不接完成确认、已接纳后 FAILED 重决策或 bin-line-common。C1/C2 继续待 WMS 补证；本片不等同全部等待点崩溃矩阵、完整任务 11 或现场物理验收完成。

本片最终聚焦验证：新增真实失效/租约回收用例 `1 passed`（64.64 秒），共享 runtime 的 WAIT、successor READY、原离场、FactProcessor 及回调链五个直接消费者 `5 passed`（167.69 秒），拓扑/所有权 `14 passed`（0.84 秒），均无 skip。留证包含五个 runtime、两个 claim 观察点、唯一一次预期中断任务；Fact 扫描结果 `[0, 0, 0, 2, 0]`，batch 为 `[2, 0]`，测试不要求固定 tick 数。替换 worker 首次实际空扫描发生在原到期点前且快照不变，同 Evidence 的新 token 在原到期点之后约 2.21 秒出现；原失败 worker 进程组已真实 SIGKILL，租约列和生产常量均未改写。两个 Python 文件 Ruff/格式、文档结构及 diff 检查通过，核心 HEAVY selector 为 NONE。未发现生产缺口，完整 QUALITY 留授权提交边界，C1/C2 与片外边界保持原状态。

独立只读 scoped full review 已完成，无 P1/P2/P3 finding：确认真实提交后停点及 SIGKILL、到期前实际空扫描、原 Evidence 新 token 重领、发布/收口分工、直接测试 owner 和进程/连接清理；首尾 HEAD 为 `df97c730`、index 为空、三文件 SHA256 与冻结 manifest 一致。review 后仅追加本段结论，可执行指纹不变。未提交、未部署、不启用生产装配或真实派发；没有把共享租约机制矩阵、完成确认、已接纳后 FAILED 重决策、完整任务 11 或 WMS/现场验收纳入已完成范围。

## 变更面与文件归属

下表为计划内目标。路径缩写仅用于后续任务：`A=src/app/wms_adapter/outbound_picking/`，
`S=src/wes_plugin_sdk/src/wes_plugin_sdk/`，`P=workline_plugins/automatic-picking/src/automatic_picking/`，
`T=workline_plugins/automatic-picking/tests/`；任务 4–5 的文件已按下方进度创建，其余仍为计划路径。

| 区域 | 当前路径 / 计划新增路径 | 唯一职责 |
| --- | --- | --- |
| 出库 wire/转换/示例 | `A/{work_plan,material_decide,source_empty,movement_report}_{wire,typed}.py`、对应已有 adapter、`A/openapi.py` | 固定协议、类型转换及示例，四个 API 各在自己的所属能力内实现 |
| SDK | `S/wms_types.py`、`wms_operations.py`、`facts.py`、`decisions.py`、`__init__.py` | 不可变 intent/outcome、固定 facade 与封闭基础 Decision/Fact 合同 |
| 共享物料接线 | `src/app/execution/services/{fact_builder,decision_applier,fact_processor}.py`、`src/app/wms_adapter/confirmation_adapter.py` | 固定 typed 类型与已有可靠执行端口的接线，不拥有 AUTO 工作顺序 |
| Work 阶段 typed 接线 | 新建 `src/app/wms_integration/outbound_picking/services/bin_work_plan.py`、所属 `services/__init__.py`、现有 Confirmation/Task Repository 及 `deployment/plugin_composition.py` | 固定创建/读取端口，按原请求身份校验并返回 typed 值；不读取插件 Work 表、不判断业务重问 |
| 插件声明 | 包根 `pyproject.toml`、`P/{__init__,definition,prepare_policy}.py` | 独立安装包、静态声明及准备策略 |
| 插件应用协调 | `P/application/{plugin,flow,batch_driver,completion_driver}.py` | 既有插件端口实现、事务协调和当前业务扫描 |
| 插件业务与存储 | `P/handlers/`；`P/application/{work_model,work_repository,pickup_model,pickup_repository,task_confirmation_model,task_confirmation_repository}.py` | 当前轮次、扫码前关联、授权及任务确认进度；随任务 5–7 增加 |
| 插件回程与排空 | `P/application/{return_model,return_repository,return_flow,drain_model,drain_repository,drain_flow,drain_inbound,drain_inbound_repository,drain_return_repository}.py` | 本线回程 FIFO 和当前排空进度；随任务 8–9 增加 |
| 部署关联 | `deployment/{plugin_definitions,plugin_models,plugin_composition}.py`、根 `pyproject.toml`、`uv.lock` | 安装来源、静态组合、模型注册/过滤，不放工作流判断 |
| 迁移/验证归属 | Alembic 生成的 `migrations/versions/` 新文件、`migrations/env.py` 的既有接线、`docs/architecture/heavy-test-impact.toml` | 每个新增模型的持久化及精确核心 HEAVY 映射；插件测试不进入 selector |
| 生成合同 | `scripts/export_release_provider.py` 的现有导出入口及实际受影响消费方 | 生成当前 provider 快照，不手改生成物、不为本任务扩大前端交互范围 |

同一文件和执行路径由主 Agent 顺序维护。实施前补齐 SDK 导出、fixture、mock、QA 示例、直接/间接消费者 manifest，
机械传播一次列全；不以测试失败列表代替调用点枚举。公开 typed 变化触及的宿主、插件及工具消费者都须同批迁移。


### 搬运装配实施：复用人工线基线（已提交 `5c42941c`，隔离验收）

用户已确认货架与料箱搬运完全对照 manual-picking；相同基础端口和参数来源不再整体等待新映射合同。组合根显式调用自动插件自己的 `transport_composition.build_transport_plugin`，将已有回程、普通来源架换面/离场、return_batch 和 drain 的当前 owner 接回同一 batch/SCAN 消费者。首次来源架/目标架进场继续由现有 `PickingTaskPlanActivationService` 拥有，不新建第二个 CTU01 owner；机械臂 source/material/target flow 与 typed 单盘 Handler 仍不安装，不接完成确认或已接纳后 FAILED 重决策。

本线位置取 `position_bindings` 的 FIVE_RACK/INLET/OUTLET；SCAN2/3/4 的 MOVE_FORWARD 取冻结工作线命令 binding、合同版本和技术期限，空 params，重放保留原期限。正常 SCAN3/4 沿人工线 `A＋9位数字-B`，只关联当前 Work/Return 与本次 Evidence，不查最近命令、可清理 Work 或人工业务表。来源下一面读取当前已应用成员与本轮正面事实；drain CTU01 读取精确当前已发布 READY 和本线位置。原 Command、Transport/Binding/窗口、Confirmation、FactProcessor、batch 与 Motion/Allocation 继续原 owner，无新表、状态、timer 或 bin-line-common。

真实组合发现一项本地假设需校准：旧 `MatchedDrainBuffer.capacity` 被当作超过即报错的缓冲总数；本轮按人工线单次请求上限使用该局部输入，取不超过四箱的合格 FIFO 连续前缀，尾部保留，不把它当作物理空位或 WMS 容量账。该行为由插件 FAST 反例和原初始 drain PostgreSQL owner 承接；不改 wire/SDK 或 C1/C2 语义。

主要测试 owner：`tests/test_plugin_composition.py` 验证实际工厂组合和机械臂缺席，`tests/test_return_device_ports.py` / `tests/test_transport_readers.py` 验证新端口边界；`tests/integration/test_transport_composition_postgresql.py` 验证实际组合根从 NO_WORK→正常扫码→FIFO→首次 drain→CTU01 的交接、原进场 owner 复用及普通来源 CTU02→下一面→departure→CTU03。测试显式给定权威输入/结果，禁止 WMS/设备派发；只证明跨 flow 装配差额，不复制共享可靠机制和 9H 补架矩阵。现有回程 mapper 用例只做异步读口签名传播；初始 drain owner 按单次前缀合同校准相应断言。

C1/C2 仍待 WMS 容量接续与核销补证，仅限制对应真实补架验收；机械臂映射、单盘扫码关联、PkgID、首批期限各限制其路径。代码装配可用不等于已启用生产，更不等于任务 11 完成；本轮不改启用配置，不部署或启动生产实例，不真实派发。验证：插件 FAST `252 passed`，实际组合根 PostgreSQL 贯通 `3 passed`，核心安装/拓扑/所有权 `23 passed`。六文件 PostgreSQL 回归首轮为 `127 passed, 3 failed`；三项均是旧 Work-only 用例缺少新增装配所需的本线绑定，未修改生产容错或放宽断言，只补该用例的隔离配置及真实命令服务，随后该完整 Work 文件 `13 passed`。新贯通文件在最终端口快照另跑 `3 passed`；按节点身份对照，130 个不同回归节点均已有对应通过记录，不把首轮失败写成整组一次全绿。组合根既有 selector 选中 11 个核心 HEAVY 文件，最终 `75 passed`，含真实 worker；保留既有 serializer/schema cycle warning，不新增插件 HEAVY 映射。本轮无 schema 改动，复用一个独占临时 PostgreSQL，合规测试库的空库→当前 head 迁移通过。初次核心运行因入口库名不满足测试保护而中止，改用已迁移的 test 库后完整 manifest 重跑通过，未放宽保护；该失败运行不作为证据。上述通过项均无 skip。固定 16 文件快照的独立闭环 review 未发现可复现或有明确可达路径的 P1/P2/P3 问题；已核对唯一恢复 owner、原动作身份、参数来源、测试所有权及 C1/C2 边界。当前同轮历史清理有既有正面证据，不将其扩大为所有跨轮提前清理场景均已证明。末尾仅整理测试排版并校准本文 review 结论，完整 QUALITY 留获授权提交边界。

### 正常投料到首个 Work 请求：实际装配离线验收（已提交 `7ecfc802`）

以已提交搬运接线 `5c42941c` 为基线，本片仅补 `tests/integration/test_transport_composition_postgresql.py` 的组合验收，不改生产代码、schema、DTO、装配配置或恢复机制。现有来源到位→inbound_batch 与 NO_WORK→回程分别保留原测试 owner；本片填补两者之间正常投料及扫码到 Work 的接线差额，不重复 9H、共享派发或租约矩阵。

使用同一 `build_deployment_runtime` 工厂、真实本地 Transport/DeviceCommand 服务和 FactProcessor。来源计划、设备绑定、位置、单箱 READY 与匹配成功终位仅为显式隔离输入：原来源架到位发布→唯一 Feed/inbound_batch→原 READY 应用发布→唯一投料 BIN_MOVE→正常 SCAN1 `-B` 及原 MOVE_FORWARD→SCAN2 `-C`→唯一 Work 与首个 PENDING work_plan Confirmation。中途重建装配，重复扫码后保留原 Feed/Command/Work/operation 和首次 scanned_at；停在 PLAN_PENDING，不注入 Work 或中间 flow，不返回 work_plan 决定，不创建机械臂执行或回程。

恢复 owner 沿用宿主计划激活、自动 batch/Feed/ScanFlow/Work、原 Transport/Command/Confirmation 和 FactProcessor；通知丢失由现有持久扫描发现，本片不新增队列、状态、timer 或 bin-line-common。测试基础设施复用独占临时 PostgreSQL 及现有逐例迁移 harness，测试队列通知关闭，Transport provider 禁止发送，不启动 worker/Beat、生产实例或 WMS 派发。没有任务注册、路由或序列化变化，不扩展真实进程矩阵。

最终实际装配 PostgreSQL 文件 `4 passed`（新增正常投料到 Work 1 项、既有装配 3 项），测试拓扑/所有权 `14 passed`，均无 skip；Ruff check/format 和 diff 检查通过，未暂存 selector 为 NONE。固定两文件快照的独立闭环 review 未发现有证据且可达的 P1/P2/P3 问题；仅改变插件集成测试及本文，核心 HEAVY、完整 QUALITY 和未改动 9H 矩阵不重复运行。C1/C2 容量接续与核销仍待 WMS 补证；不涉及 PkgID、机械臂映射、首批期限、完成确认或已接纳后 FAILED 重决策，不以此片证明任务 11 整体或现场闭环完成。

### Work WAIT 经宿主持久扫描续问：PostgreSQL 验收（已提交 `432a0490`）

以 `7ecfc802` 为基线，只在现有 `tests/integration/test_work_and_task_progress_postgresql.py` 新增一个入口验收，不改生产代码、schema、DTO、配置或共享测试 helper。起点为业务依据仍有效的 Work；用实际工厂创建原请求，以显式隔离 WAIT、既有 typed parser/Confirmation/Evidence 和 FactProcessor 建立已应用发布的确定 WAIT 检查点，不重做投料/扫码或 WMS 派发。

后续仅经 `picking_task_plan_activation_service.activate_batch()` 调用实际宿主持久扫描，不直接调用插件推进方法。按已有测试时钟端口验证：固定到期点前反复扫描保留原 WAIT/operation/response 引用；到期时创建唯一新请求并切换当前 Work；再次扫描、重建装配及更晚扫描不重复创建。原 task/bin/feed、首次扫码 Evidence 与 scanned_at 不变，旧确定结果保留，新请求保持 PENDING，Work 停在新的 PLAN_PENDING，不授予取盘资格、不创建 Command/Transport。

恢复 owner 仍为宿主持久扫描发现工作线，自动 batch/WorkFlow 在本地事务内决定到期续问，Confirmation 负责已建请求的可靠交付，FactProcessor 负责结果应用发布；没有新 timer、队列、状态或通用恢复模型。测试复用同一生产工厂、真实本地服务和逐例隔离迁移 harness，关闭队列加速通知、禁止 Transport provider 发送；不启动 worker/Beat、WMS 派发或生产实例。

新增 PostgreSQL 用例 `1 passed`，直接 Work/batch FAST `11 passed`，测试拓扑/所有权 `14 passed`，均无 skip；Ruff check/format、文档结构与 diff 检查通过，未暂存 selector 为 NONE。固定两文件快照的独立闭环 review 未发现有证据且可达的 P1/P2/P3 问题；本片只证明既有 Work 的恢复入口，不扩大为新计划准入或完整投料验收。该差额不重复既有 Work worker 响应交付/事务矩阵、9H 或共享租约/派发验收；完整 QUALITY 留获授权提交时由 hook 执行。C1/C2 仍待补证，不接机械臂、完成确认、已接纳后 FAILED 重决策或 bin-line-common，不以本片证明任务 11 或现场业务整体完成。

<a id="task11-transport-work-evidence"></a>

### 任务 11：已确认搬运与 Work 范围的证据收口

**搬运基线与当前重点。**依据本次用户确认，manual-picking 的货架、料箱搬运已经现场验证；自动插件沿用其搬运合同、参数来源和基础能力。自动插件接线差额由下表离线证据承接，不再默认追加搬运、9H、共享派发/租约或逐层 worker/Beat/回调矩阵。后续业务重点是料箱到达工作位后：Work READY 的来源授权、单盘 decide/WAIT/ACCEPT、目标准备与报告、Work 后继接续。人工线搬运现场基线不扩大为自动插件机械臂或整线现场通过。

下表测试路径相对 `workline_plugins/automatic-picking/tests/`，每项保留原主要 owner；表中数字来自对应提交记录，本次没有重跑或累计测试数量。

| 已提交范围 | 提交 | 主要测试 owner | 已有证据及边界 |
| --- | --- | --- | --- |
| 搬运实际装配：NO_WORK 流出/回程、来源换面离场及 drain 接线 | `5c42941c` | `integration/test_transport_composition_postgresql.py`；`test_return_device_ports.py`、`test_transport_readers.py`；原 Return/Drain 域保持各自事务 owner | 当时组合根 3 项、插件 FAST 252 项、核心所选 HEAVY 75 项通过；显式权威输入、禁止外部派发。首次来源/目标进场仍归宿主计划；不证明自动插件物理或容量验收 |
| 正常投料→SCAN1/SCAN2→首个 Work 请求 | `7ecfc802` | 同一 `integration/test_transport_composition_postgresql.py` 的正常投料用例 | 最终文件 4 项包含前行 3 项及新增 1 项，不能再相加。实际工厂创建唯一 Work/首个 work_plan，重建重扫保留身份，停在 PLAN_PENDING，不含逐盘执行 |
| Work 确定 WAIT 经实际宿主持久扫描续问 | `432a0490` | `integration/test_work_and_task_progress_postgresql.py::test_due_work_wait_recovers_through_actual_host_scan_once`；直接规则归 `test_work_admission.py`、`test_batch_recovery.py` | 新增 PostgreSQL 1 项、直接 FAST 11 项、核心拓扑/所有权 14 项通过，无 skip；固定到期前不推进、到期唯一新请求、重建重扫防重，停在新的 PLAN_PENDING。只证明既有 Work 检查点恢复，不扩大为新计划准入 |

三个提交的 hook 均记录完整 QUALITY 通过，核心 FAST 各为 `4321 passed, 5 skipped`；这些是各自提交的记录，不能相加，也不是本文整理时的新执行证据。核心拓扑/所有权测试只证明基础边界，不能替代插件业务。`5c42941c` 的 HEAVY 75 项按其已验证范围复用；后两个提交的 staged selector 均为 NONE。9H 的宿主扫描、真实进程、回调与领取后失效仍统一引用前文索引，不复制另一份矩阵。后续仅在覆盖内容/环境变化使证据失效或发现真实新缺口时补验。

| 当前持久依据与出口 | 唯一业务恢复 owner／基础义务 owner | 恢复入口与既有验收归属 |
| --- | --- | --- |
| 原来源/目标进场及已发布到位；冻结 Feed 清单、分段原 Transport 与扫码交接 | 宿主计划激活负责首次进场；自动 batch/Feed 负责投料及交接；原 Transport 保持动作身份 | 宿主持久计划/batch 扫描；来源 revision、Feed/Work 和实际装配用例分别拥有，不从可清理历史或空查询推定到位 |
| 当前 Work、原 work_plan 请求及结果；WAIT 固定到期或 READY/NO_WORK 出口 | 自动 batch/WorkFlow 负责业务推进；未决请求归 Confirmation，已保存未发布结果归 FactProcessor | 当前 Work 持久扫描及原 Evidence 领取/发布；Work 域与上表新入口验收拥有，不把未知当 WAIT、不从重启时间重计 |
| 当前 Return/FIFO、分配、Motion、Drain 及其直接因果结果 | 自动 batch 与原 Return/Allocation/Motion/Drain owner；已建 Command/Transport/Confirmation 各自履约 | 既有 batch 前置恢复和 FactProcessor；任务 8–9 域及 9H 索引拥有，不因 current 退出丢掉原未终态义务 |
| 周期触发与丢通知发现 | 宿主扫描/Beat 只发现和唤醒，不取得业务 owner | 已有核心任务接线及 9H 指定真实进程证据；不因 Work 新增一个等待点就复制共享可靠机制 |

**后续门禁与工位内业务。**本片只整理证据，不实施下表路径；下一实施切片应从任务 6–7 的实际业务/接线差额中选择，复用已完成的 6A–7C 离线规则和事务，不默认再加一轮搬运证明。

| 路径／门禁 | 已有基础与真正剩余范围 | 当前责任与限制 |
| --- | --- | --- |
| 到工作位后的 Work READY→来源授权/单盘身份→decide→后继交接 | 来源授权与原子消费、精确身份显式输入、material WAIT/ACCEPT 已有 6A–6D 离线实现；实际来源/单盘流程及权威事实读口尚未装配 | 自动插件 Work/Pickup/material owner；只按 G2/G3 的精确关联和映射补真实差额，不重新证明已有纯规则；本次不接机械臂 |
| 目标准备、PUT/位置报告与 Work 接续 | 7A–7C 已覆盖普通目标已就绪、显式成功 PUT/PkgID、报告确认关闭及 M2 尚未创建的恢复；其余目标准备/生产读口和装配仍有差额 | 插件 target/material owner，Command/Transport/Confirmation/FactProcessor 保持原义务；G3/G5 只限制对应映射与权威身份，不猜 PkgID、不先取盘后补身份 |
| C1/C2 | WMS 仍须提供现有容量关联、继续供架与回库核销的实现/合同版本及账务或等价时序 | WMS 提供、双方核对；只限制对应真实容量接续/核销验收，不作为工位内 Work/单盘业务的统一前置，也不预设新字段 |
| 首批期限、完成确认及异常分支 | G6 仅约束首批期限；完成确认及已接纳后 FAILED 重决策未纳入当前授权 | 不借用技术期限，不猜值；本次不接完成确认或已接纳后 FAILED 重决策，G4/G7 仍只限制其分支 |
| 任务 11 最终范围与现场 | 最终目标分支规定 base→head 迁移、完整正常闭环和当时必选门禁，以及自动插件生产/WMS/物理验收 | 随最终范围形成再核对差额并按授权执行；当前不启用生产配置/实例，不用人工线基线或局部离线证据勾选整项任务 11 |

本次为现有计划与 C1/C2 清单的人类文档整理，不新增代码/测试，不运行 QUALITY、HEAVY、迁移、worker 或现场动作；文档只做提交/路径/结构/diff 核对及闭环 review，不把旧绿灯写成 fresh 验证。固定两文档快照的独立 review 未发现有证据支持的 P1/P2/P3 问题；末尾仅登记此结论。

## 实施切片

### 任务 0：固定执行范围和证据

**文件：**只读上述设计、合同、目标源文件、测试、HEAVY mapping 和 `AGENTS.md`；本任务不改代码。

- [x] 保存 HEAD、三份既有 dirty 文档及其他现场的路径/指纹，执行前核对设计 SHA；只对目标重叠变化读取 diff。
- [x] 为任务 1–3 冻结实际生产符号、调用点、摘要/规范化、fixture、测试 owner、生成物和 HEAVY 范围；后续插件切片在各自开始前补齐。
- [x] 对计划修改的生产函数/类先做 GitNexus upstream impact；缓存同一 base/意图的结果，HIGH/CRITICAL 先说明影响再在授权范围内继续。
- [x] 核对索引是否 stale；仅必要时分析一次并保护 Agent 入口。确认 FAST 收集和依赖，不为得到 skip 先跑名义集成测试。

**恢复 owner：**无新增持久对象。**验收：**变更 manifest、未决项与测试所有权可追溯；未改用户现场，不把计划记录当实施证据。

### 任务 1：对齐三条 RACK_SLOT 请求的 revision 身份

**依赖：**任务 0。**文件：**`A/{material_decide,source_empty,movement_report}_{wire,typed}.py`；`S/{wms_types,wms_operations,__init__}.py`；`A/openapi.py`。
**测试：**已有 `tests/contracts/wms_adapter/outbound_picking/{test_material_decide,test_source_empty,test_movement_report}.py`，以及 SDK 对应消费者测试。
**接口：**沿用 `outbound_material_decide`、`outbound_source_empty_decide`、`outbound_material_movement_report` 的具名方法及原返回 intent，增加条件 `plan_revision`。

- [x] RED：RACK_SLOT 缺少、null、bool、非正或超过合同整数范围的 revision 拒绝；不同 revision 正文不同，同身份内容漂移冲突。
- [x] 同批更新 wire、SDK、facade、converter、OpenAPI 请求示例和调用点；BIN_CELL 保持合同规定的请求形态，不额外发送 revision。
- [x] GREEN：确认从 facade 到冻结 JSON 的 revision 无丢失，物料决定、空取、上报均引用原成员版本；技术重试不换身份。
- [x] 运行三个目标测试文件及受影响 SDK 测试，检查目标 API 的旧调用残留；source.empty 仅对齐协议，不实现自动空取动作。

**恢复 owner：**沿用 WmsConfirmation，operation 身份及错误恢复不变。**验收：**三条接口同步、无只有 wire 有字段而 typed converter 丢失的情况。

### 任务 2：一次性替换单 Cell、扫码原文及目标/后继结构

**依赖：**任务 1；对应严格值域受 G1 约束。**文件：**`A/{work_plan,material_decide}_{wire,typed}.py`、对应 adapter、`A/openapi.py`；`S/{wms_types,wms_operations,__init__}.py`。
**测试：**已有 `test_work_plan.py`、`test_material_decide.py`、受影响 SDK/fixture，以及 `tests/api/test_qa_regression_transport_openapi.py`。
**接口：**保留 `BinWorkPlanReady`、`PickingMaterialIntent/Accept` 和固定 facade 名，按目标合同替换字段；`encode_request(intent, *, timestamp)`、`decode_outcome(payload)` 保持各 operation 独立归属。

- [x] RED：READY 单 Cell 的完整字段、原样 barcode、目标层列、后继对象/省略、DirectPick 无后继、ROTATE/REPLACE 以及 WAIT/REJECT 条件字段。
- [x] 替换 `cell_ids[]`、出库 `PickingSixInOne`、枚举型后继；目标层列使用出库目标 typed locator，不污染 plan_delta、来源 slot 或 movement_report 的独立 locator 合同。
- [x] 字段缺失/类型/禁止 null 按已确认表校验；未知长度、数值类型或映射不拿样例定型，不增 permissive parser、默认值或兼容分支。
- [x] 同批迁移 SDK 导出、四条转换链的所有受影响消费者、OpenAPI 业务响应示例与 QA fixture；原始字符串不裁剪、不拆条码，不解析 PkgID。
- [x] GREEN 后运行受影响出库合同领域、SDK 及 API OpenAPI QA；限定范围扫描旧字段/旧类型/旧后继枚举，确认旧路径和失去用途的 helper/import 退出。

**恢复 owner：**WMS 交付仍归 Confirmation，确定业务 WAIT 后的重问留给插件；Adapter 不加循环。
**验收：**DTO、SDK、converter、OpenAPI 和测试在同一最终快照一致；G1 未闭合的子项只报告未完成，不能宣布整个合同对齐。
入库自己的 `SixInOne` 及 REJECT.source_disposition 的 CONTINUE/CLOSE 保持独立语义，禁止无边界全仓替换。

### 任务 3：补齐 Work 与单盘的共享 typed 接线

**依赖：**任务 0 与任务 1–2 中本路径实际使用的合同；3A 不依赖 3B，接口轮廓可先落定，所用 DTO 未闭合时不宣称完整接线验收。
**文件：**3A 新建 `src/app/wms_integration/outbound_picking/services/bin_work_plan.py`，同步所属导出、Repository 必要方法及 deployment 注入；3B 修改 `S/{facts,decisions,wms_types}.py`、`src/app/execution/services/{fact_builder,decision_applier,fact_processor}.py`，实际需要时修改固定 confirmation adapter 接线。
**测试：**3A 新建 `tests/contracts/wms_adapter/outbound_picking/{test_work_plan_scheduler,test_work_plan_reader}.py`，扩展已有 `tests/integration/wms_adapter/outbound_picking/test_work_plan_production_wiring.py`；3B 新建 SDK `test_outbound_picking_facts.py`，复用核心 `tests/runtime/execution/{test_fact_builder,test_decision_applier,test_fact_processor,test_wms_confirmation_dispatch}.py`。

**3A：无 MaterialExecution 的 Work 请求与结果端口。**沿现有所属 operation service 模式新增固定 scheduler/reader，直接复用 Confirmation 生命周期；不把 work_plan 塞进单盘 Fact/Decision 路径。

- [x] RED：只有 PickingTask 和本次 SCAN2 上下文时，从 `BinWorkPlanIntent` 创建唯一义务，严格 converter 保留原请求；重复创建重放原身份，内容漂移冲突。
- [x] `BinWorkPlanScheduler.create_in_session(db, intent, *, picking_task_id, created_at)` 冻结原身份、timestamp、正文及 deadline；Confirmation 唯一 owner 为 PickingTask，Work 保存 operation 引用，不再同时设置 material_execution_id 或 workline_id owner。
- [x] `BinWorkPlanResultReader.by_operation_id(db, *, picking_task_id, operation_id)` 通过既有 Repository 按冻结身份取请求/响应，校验 operation、任务归属、摘要及 response Evidence 关联，返回不可变 typed intent、状态、Evidence 引用及 outcome；未决时 outcome 为空，不读“最近一条”。确定结果须匹配 COMPLETED Confirmation、原 response_evidence_id 及 APPLIED 的 WMS_RESULT；业务发布是后续应用结果，不能反过来成为首次读取条件。
- [x] reader 返回 typed intent 的 task/bin/scanned_at，供任务 5 插件核对当前 Work 并在可靠应用条件满足后推进；本任务不提前实现插件。reader 不读插件表，不解释 READY 后取盘、NO_WORK 放行或 WAIT 到期重问；无需新增通用 envelope 或另一套可靠表。
- [x] GREEN：FAST 用中性 fake 覆盖身份不匹配及原请求读取；扩展已有真实接线测试验证零插件可交付、无 MaterialExecution、三种结果可靠保存且 WAIT 不自动重问。任务 5 再证明结果应用和 Work 同事务关联；部署注入后才接业务。

**3B：已有 MaterialExecution 的物料 Fact/Decision。**扩展 `WmsResultReadyFact.outcome` 封闭集合及 Decision 接纳的正常出库 intent，复用所属 converter。

- [x] RED：从已保存的 material.decide/movement_report Evidence 构造 typed Fact，经静态 handler 与 DecisionApplier 创建唯一可靠义务；身份不匹配不得推进。
- [x] 让 FactBuilder 的固定出库 operation 分支调用所属 decoder，DecisionApplier 对已批准 intent 调用所属 encoder；私有静态选择不导出 generic call/裸 envelope 构造入口。
- [x] 同步封闭联合、构造校验、摘要 discriminator 及直接/间接测试；只补必需的类型接线，不增加 Cell、双臂、WAIT 重问或完成判断。
- [x] GREEN：核心使用中性 typed fake 验证事务、摘要、原身份重放及旧入库路径；插件业务事实增强仍由其 fact_factory/具名 reader 拥有。

**恢复 owner：**3A 的原请求由 Confirmation 派发/保存；Work 当前引用由任务 5 的插件 batch owner 周期发现，reader 只是读取端口，不是第二个恢复 owner。3B 的 Evidence 由 FactProcessor 领取/重放，Command/Transport 各自恢复。
**验收：**Work 和单盘两条链分别可达；创建 Work 请求不要求先有扫码或单盘，重启后可从当前 Work 的原 operation 引用继续；核心/SDK 不导入 automatic-picking，不依赖插件测试证明机制。

### 任务 4：独立插件包与静态装配

**依赖：**任务 0 与现有插件 SPI；包声明/静态装配不等待任务 1–3 全部验收，具体业务端口随其调用路径的合同及接线验收后注入。**新增：**插件包 `pyproject.toml`、`P/{__init__,definition}.py`、`P/application/plugin.py`、`T/test_definition.py`、`T/test_plugin_composition.py`。
**修改：**根 `pyproject.toml`、`uv.lock`、三个 `deployment/plugin_*.py` 文件；复用既有 installed-plugin/runtime-binding 合同。
**接口：**`DEFINITION` 的 `plugin_key="automatic-picking"`、`supported_line_types=("AUTO",)`；包版本在本切片冻结单一初始值，不随宿主运行动态选择。

- [x] RED：声明可独立导入，静态运行关联身份一致，空插件/人工单装/自动单装/双插件组合各自正确，重复和未知插件 fail closed。
- [x] 增加独立安装来源与 optional dependency，设备角色和位置声明只采用已批准语义；实际设备码、线号与位置取绑定，不从 KT11 示例写死。
- [x] 沿现有部署入口关联声明和逐步接入的运行对象；没有 handler 的合法事件只留证，不创建成功占位或自动消费历史观察。
- [x] GREEN：仅运行目标插件声明/组合 FAST；依赖配置变化后按仓库要求初始化环境和同步依赖，不复用其他 worktree 环境。

**恢复 owner：**尚无业务接管时基础服务继续保存结果。**验收：**独立安装/装配成立，不声称业务可执行，不引入新框架或人工业务依赖。

### 任务 5：任务准备、来源投料与工作位 Work

**依赖：**任务 4；投料使用既有 typed 端口，Work 请求/应用另依赖任务 3A 及其 DTO，首批超期路径受 G6 约束；三部分分开记录验收，不等待任务 3B 或 G2/G3/G5。**新增：**`P/prepare_policy.py`、`P/handlers/picking_task_plan_applied.py`；
`P/application/{work_model,work_repository,task_confirmation_model,task_confirmation_repository,batch_driver,completion_driver,flow}.py`。
**测试：**新增 `T/{test_prepare_and_plan,test_feed_flow,test_work_admission,test_task_confirmation}.py` 及 `T/integration/test_work_and_task_progress_postgresql.py`。
**接口：**`AutomaticPickingBatchDriver.advance_in_session(db, line, task)`、`advance_completed_in_session(db, line)`；`AutomaticPickingCompletionDriver.advance_in_session(db, line, task)`。
应用入口使用现有 `AsyncSession`、WorkLine、PickingTask；任务锁内调用同一插件完成协调器，返回本次推进计数，不执行网络 I/O。

- [x] 补齐跨 revision 复用当前物理进场而保持成员独立的插件验收，范围与出口见切片 5A；不受 G6 阻塞。
- [ ] G6 闭合后验收 PREPARING/revision 0 前置入口、期限重启不重置、计划先到和旧确认迟到。
- [ ] 首批确认分支待 G6 闭合后随片增加必要进度、Repository、迁移并接入同一个 completion driver；普通完成分支若具备本地前提可先实施，不为未决期限预建空进度。首批期限归插件唯一配置入口，不借派发 deadline 或新通用 timer 表，不重复创建已交付的 Work/Feed/FeedScan。
- [x] 实现插件计划准入/应用、来源到位后一次 inbound_batch、完整有序清单最多 4 箱分段及 feed_complete；直接依赖保留，独立架到位可推进。
- [x] 实现 SCAN2 实扫准入及 work_plan：Work 与任务 3A scheduler 创建的原请求引用同事务保存，经 reader 核对 typed 结果后同事务更新当前阶段；缺 SCAN1/乱序/来源取消后已入线按合同处理，同轮重扫不建新 Work，非预期箱只留证。
- [x] 在既有静态业务 Evidence 消费端口接入 work_plan；消费者与 batch driver 调用同一事务应用逻辑，按当前 Work/原请求防重。事件应用后发布与丢通知后的周期推进均不走单盘 FactBuilder，不等任务 6 才提供业务入口。
- [x] GREEN：READY 保存当前 Work 授权、NO_WORK 保存明确业务结束与点2交接、WAIT 保存原决定/到期时间并创建唯一新请求；未知沿原请求恢复。

**本轮进度（2026-10-16）：** 首批超期、任务确认进度及 completion driver 目前尚未实施，对应项保持未勾选；首批分支等待 G6 的期限值与合法范围，普通完成分支按其当前本地前提单独验收。跨 revision 来源接续已由切片 5A 的插件 PostgreSQL 测试闭合，未改生产代码。普通 prepare 复用宿主入口。已创建 Work、Feed、FeedScan 三表及迁移，FeedScan 只保存当前成员入线/已准入标记，避免依赖 CLOSED Work 历史。未另加重复的计划接收规则，继续使用共享严格 DTO 和启用时位置绑定校验。SCAN1/SCAN2 用户确认沿用 `A`＋9 位数字及 `-B`/`-C`；仅 SCAN1 已确认正常前进接线，未接异常分流或真实取盘。
`READY` 保存授权；`EXIT_PENDING` 保存明确 NO_WORK 与点2待交接；实际放行/回程属于任务 8。`FEED_COMPLETE` 由末段已发布的确定物理交接产生，不等 SCAN；中间分段才等本段实扫。Transport 结果接收器复用公共发布链；取消后乱序扫码沿原 Evidence 暂缓恢复。终态 Feed 退出投料扫描，原结果发布且成员交给 Work 后退出准入/阻塞统计，仍保留当前来源完成标记；不把可清理 Confirmation/CLOSED Work 作为运行依据。后续换面/离场门禁尚不属于本轮完整交付。当前状态、测试所有权和验收证据见[任务 4–5 实施记录](../../integration/2026-10-16-automatic-picking-task4-5-implementation.md)。不得将此进度宣称为整项任务 5 或整线业务闭环已完成。

**恢复 owner：**prepare/plan 通知由宿主入口驱动；首批超期与确认归插件 completion owner，经 batch 前置钩子在无 EXECUTING 任务时调用；Work/来源阶段归插件 batch owner。
**验收：**任务不能永久停在准备状态；投箱与 Work 可从持久记录恢复；新增表随片注册、迁移并通过干净临时库验证，不新增 Bin 库存主账。

### 任务 6：取盘、扫码与 ACCEPT 授权交接

**依赖：**任务 5 的 Work/来源授权与任务 3B；生产取盘受 G1/G2/G3/G5 的对应项约束，不扩大到任务 4、投料或 NO_WORK 路径。**新增：**`P/application/{pickup_model,pickup_repository,fact_factory}.py`、`P/handlers/material_flow.py`；扩展本插件 `flow.py` 与 batch driver。
**测试：**新增 `T/{test_pickup_flow,test_source_authorization,test_material_decisions}.py`、`T/integration/test_pickup_identity_postgresql.py`。
**接口：**`AutomaticPickingFlow.apply_in_session(db: AsyncSession, evidence_id: int, *, workline_id: int) -> BusinessEvidenceApplication`；fact_factory 实现现有 PluginFactFactory，返回不可变插件 Fact。

- [ ] RED：同 Cell 多盘 LIFO、同箱新轮次、扫码/来源结果/ACK 乱序、重复扫码、缺关联、并发消费同一来源授权及当前栈顶未知。
- [ ] 在一事务内冻结扫码前 Pickup、来源授权和 Command；关联成功扫码后经 MaterialExecution 服务创建单盘身份，原 Command 不改身份或正文。
- [ ] 使用宿主提供的具名 typed reader/facade 发起 material.decide，确定 WAIT 与未知请求分开恢复；应用 ACCEPT 时保存本盘目标并原子把后继授权交给 Work，省略则保存明确无后继。
- [ ] 消费 Work 授权时同事务创建/复用下一 Pickup/Command 并记录消费关联；旧 Evidence 重放不覆盖新授权，不查已闭合 M1 恢复 M2。
- [ ] GREEN：纯 Decision 与事务测试分别证明授权和幂等；未决设备参数只作为明确标记的测试输入，不实现生产 fallback，不用 fixture 宣称设备合同成立。

**恢复 owner：**Command 归设备基础服务；扫码汇合、当前栈顶及待消费 Work 授权归插件业务 driver；MaterialExecution Evidence 由 FactProcessor 调用冻结插件。
**验收：**后继创建在崩溃前后均至多一次，已闭合记录不成为运行依赖；G2/G3/G5 未闭合时不启用不可逆取盘。

### 任务 7：目标准备、PUT、位置报告与双臂并发

**依赖：**任务 6；完整生产闭环受 G3/G5 约束。**新增：**`P/application/target_preparation.py`；扩展本插件 Pickup、flow、material handler 和 composition。
**测试：**新增 `T/{test_target_preparation,test_placement_report,test_arm_concurrency}.py`、`T/integration/test_material_close_postgresql.py`。
**接口：**消费任务 2 的 ACCEPT typed outcome，经现有 ReliableRackTransportCreator、DeviceCommandService、WmsConfirmationService 创建对象，不新增外部 operation。

**当前离线进度：**7A 普通目标已就绪后的 PUT 创建、7B 确定位置/报告义务、7C 报告确认后的关闭与 Work 续接已分别验收。以下复选框仍代表完整任务（含其余目标准备/来源分支与生产合同准入），不把本轮离线通过视为任务 7 整体完成。实际关闭验收 owner 为 `T/integration/test_material_close_postgresql.py`。

**目标 ROTATE 离线准备片（实施基线 `0a3ef706`，本轮按授权提交）：**已发布本盘 BIN_CELL ACCEPT、精确位置/当前架面和完整参数显式给定时，`P/application/target_rotation.py` 经现有 `ReliableRackTransportCreator.create_rotate` 创建 CTU02。原 Transport、Binding 与 Pickup 的可空唯一 FK `target_rotation_transport_task_id` 同事务成立；迁移 `f489f59e9b22` 承接 `c094b575576f`，有当前引用时拒绝有损降级。不新增模型或业务状态，不重新 decide 已确定资格，不复用 Work 后继授权解释本盘。

恢复仍由现有 batch 的 `target_flow` 扫描当前未闭合 Pickup；先校验原引用，再判断新建资格，不因读口消失、取消或未知结果换身份。Transport owner 负责原动作可靠履约和位置投影；插件固定目标 ROTATE step 经现有 publisher / ScanFlow / FactProcessor 留存、应用和发布结果；只读 ready 口要求原动作当前版本的已发布成功 Evidence、精确目标架/面/位置及原因果投影同时匹配。当前已准备就绪不保存另一份状态，错误、未知、未发布、旧版本或投影被后续动作覆盖均不放行。

测试主 owner 为 `T/integration/test_target_rotation_postgresql.py`：20 项 PostgreSQL 验收通过（唯一创建/关联、并发、事务中断回滚、原身份重建恢复、回调早于 ACK、负向结果、Fact 发布边界、重放与直接取消、迁移往返/降级保护）；位置和结果是测试权威输入。插件 FAST 252 项、拓扑/所有权 14 项、直接消费者 PostgreSQL 回归 16 项和 selector 所选核心 HEAVY 8 项通过；不重跑 9H 或共享机制矩阵。停在 `ReadyTarget` 可读取，不创建 PUT/报告，不关闭单盘；该准备片的普通目标 PUT 路径仍拒绝 ROTATE/REPLACE，不把该片记作目标 ROTATE→PUT 完成；接续进度见下述离线接缝片。生产 composition 未启用，实际位置读口仍待相应 G3 合同；扫码关联 G2、机械臂映射 G3、PkgID G5 各限制依赖路径，C1/C2 只限制对应真实容量路径，不阻塞此离线片。REPLACE、机械臂、完成确认及已接纳后 FAILED 重决策均不纳入。

本片独立只读 full review 已闭合，无 P1/P2/P3 finding；评审前后基线 HEAD 与 9 文件 SHA256 一致，未发现额外模型、闭合历史运行依赖或业务进入基础层。证据：新增 PG 20 项（72.37 秒）、直接消费者 PG 16 项（95.73 秒）、所选核心 HEAVY 8 项（69.73 秒）均 0 skipped；插件 FAST 252 项、拓扑/所有权 14 项、7 个 Python 文件 Ruff/格式及 diff 检查通过。review 后仅追加此人类文档记录，可执行快照不变。验收收口未发现生产或迁移缺陷。提交门禁补齐 `tests/scripts/test_select_heavy_tests.py` 的新增 revision 精确清单一项，保留原断言；对应清单与隔离 runner 聚焦回归 14 项通过，未修改 runner。原插件可执行快照不变，复用上述聚焦/迁移/HEAVY 证据；该机械补齐另做定向闭环审阅。本次已授权提交，暂存范围限定本片 10 文件，按 staged selector 对照，由 commit hook 执行完整 QUALITY；提交门禁结果以对应 Git 提交输出为准。暂不部署、不启用生产装配或真实派发。

**ROTATE→7A PUT 离线接缝片（实施基线 `4097a29b`，本轮按授权提交）：**`P/application/target_preparation.py` 显式注入既有 rotation flow；普通目标沿原 reader，ROTATE 只沿本盘原 ROTATE reader，未装配时延期，REPLACE 仍不准入。现有 batch 的唯一 `target_flow` 同时保留 ROTATE 结果消费与 PUT 推进，原 ScanFlow/FactProcessor 路由不改。rotation 推进可限定精确 Pickup，先过滤其他盘再读取其上下文，避免恢复已有 PUT 时重新引入其他盘 ACCEPT/映射依赖；不新增通用协调器、worker 或状态。

原 PUT Command 恢复分支仍先于 ACCEPT、取出和准备读口；新 PUT 仍要求本盘已发布 ACCEPT、直接来源未取消、原来源 Command 匹配的可靠取出 Evidence，以及原 ROTATE 当前版本已发布成功与当前原因果位置同时成立。Transport 成功/插件事实未发布时不放行，不将普通 reader 的 ready 当作 ROTATE 完成。继续复用 7A 的 WorkLine→Work→Pickup 事务与 DeviceCommandService，同事务创建 PUT 并保存 `put_command_code`，不改原 Command/MaterialExecution 身份，不重问 material.decide，不改变 Work 后继授权或跨臂准入规则。

本片主要 PostgreSQL owner 为 `T/integration/test_rotated_target_put_postgresql.py`，仅验接缝：组合 batch/FactProcessor 发布交接、目标与可靠取出门禁、并发、创建/关联故障回滚、旧结果重放、原 Command 在读口失效/取消后恢复、错误/未知结果不得回退普通 reader、未装配消费者延期及 REPLACE 拒绝。fixture 明确提供已完成源事实与测试专用完整请求，不解释机械臂报文或生产成功条件；源事实的发布和决定摘要一并构造，避免将其空测试载荷重送其他域；FactProcessor 的处理数不等于成功发布数，延期按持久字段验收。7A/ROTATE 原矩阵仍归原 owner，只做直接回归；7B/7C 仅核对构造端口的直接消费者，不重复其报告/关闭矩阵或 9H、共享机制/worker 验收。

无需新 schema、迁移、DTO、SDK 或 WMS operation，生产装配不变；只冻结本地 PUT Command，停在待执行，不应用 PUT 成功、不创建报告或关闭单盘，不接 REPLACE/机械臂/真实派发。G3 继续限制真实目标/来源映射与成功后置条件，G2 限制真实扫码/Command 关联，G5 限制新报告权威身份及不可逆取盘生产闭环；本片已给定精确身份与测试参数，不以未决值补默认。C1/C2 仍只限制容量接续与回库核销对应路径，不扩大为本片门禁。

本接缝片最终验证：新增 PostgreSQL 13 passed（54.13 秒）、普通 PUT/原 ROTATE 及 7B/7C 直接消费者 PostgreSQL 17 passed（65.93 秒），均 0 skipped；插件 FAST 252 passed，拓扑/测试所有权 14 passed，3 文件 Ruff/格式与 diff 检查通过，HEAVY selector 为 NONE。初轮失败归类为测试专用源结果被重复消费及把 FactProcessor 处理数误作成功发布数，修正夹具时保留既有数据库发布约束、可靠取出/成功结果守卫；不计为业务回归通过证据。独立只读 full review 核对 HEAD 与 4 文件 SHA256、直接/间接 owner 和验收边界，无 P1/P2/P3 finding；评审后仅追加本段人类文档，可执行快照不变。本次按授权仅提交两个目标 flow、接缝 PostgreSQL owner 与本文共 4 文件；可执行快照沿用已完成闭环 review 的指纹，staged selector 对照后由 commit hook 运行完整 QUALITY，提交门禁结果以对应 Git 提交输出为准。不部署、不启用生产装配或真实派发，不创建报告或关闭单盘，不接 REPLACE/机械臂，不宣称任务 7/11 或现场完成。

**ROTATE 成功 PUT→7B 位置/报告离线接缝片（基线 `c9411ba4`，本轮按授权提交）：**`P/application/placement_flow.py` 的共用 ACCEPT 读取只校验冻结身份；准备类型准入留在各业务分支。PUT 事实分支允许普通目标和 ROTATE，继续拒绝 REPLACE；7C 报告确认分支保持普通目标边界，不因共用 helper 放宽而关闭 ROTATE 单盘。仍要求原 PUT Command 的精确成功 Evidence、本盘身份、已发布 ACCEPT、精确目标、显式权威 PkgID 与发生时间，不重新请求 material.decide。PUT 完成后以原成功事实确认位置，不再次要求当前架面维持原 ROTATE 就绪投影。

沿原 `MaterialPutFact` / handler、FactProcessor / DecisionApplier 原子创建唯一 movement_report Confirmation，同事务保存 Pickup 的成功 Evidence / 报告成对引用与 Evidence 发布，不新增模型、状态、迁移、SDK/wire、位置账本或 timer。原 Command 及设备结果链负责物理义务，FactProcessor 负责事实应用/延期恢复，Confirmation 负责已冻结报告义务；batch 保留 PUT 创建前的原恢复职责。缺权威 PkgID 沿原 Evidence 延期，无新报告；错误身份拒绝应用，不以 ACK 或超时推定成功。

主要 PostgreSQL owner 继续为 `T/integration/test_placement_report_postgresql.py`，复用已提交 ROTATE→PUT 装配及 7B 成功输入 helper，只补接缝：回调早于 ACK、并发、报告创建/关联后事务中断与重建、重放、错误 Command/Pickup/Evidence/目标、缺 PkgID、架面后续变化，以及 REPLACE 拒绝和 ROTATE 报告确认不进入 7C。原 7B/7C 机制矩阵归原 owner，只刷新直接消费者；不重复 9H、共享领取/租约或真实 worker/Beat 矩阵。故障验收为应用事务故障注入，不宣称真实进程崩溃验收。

本片验证：新增正例先在原 `_accepted` 的准备类型守卫上失败，修正后新增接缝 11 项通过；最终同一可执行快照的新增接缝加普通目标/7C 直接消费者 PostgreSQL 21 passed、0 skipped（78.68 秒），插件 FAST 252 passed、拓扑/所有权 14 passed，Ruff/格式、diff 检查通过，HEAVY selector 为 NONE。数据库为本片独占 PostgreSQL 17 tmpfs，每例独立临时逻辑库；没有宿主/SDK/schema/迁移/队列装配变更，不刷新无关完整 QUALITY、迁移或真实 worker 证据。独立只读 full review 核对固定 3 文件指纹、合同、直接/间接测试 owner 和恢复链，结果无 P1/P2/P3 finding；评审后仅补本句人类文档，可执行快照未变。最终状态止于 `IMPLEMENTED - FOCUSED VERIFIED`，不计为任务 7/11 或生产验收完成。

本片停在报告 PENDING，不发送 WMS 请求、不关闭单盘、不启用生产装配或真实派发，不接 REPLACE/机械臂。G2 仍约束真实扫码关联，G3 仍约束设备请求与成功事实读口，G5 仍约束新报告权威 PkgID；显式离线输入不构成现场合同确认。C1/C2 继续待补证，只限制对应容量接续/核销路径，不阻塞本片本地报告义务创建，也不由本片证明核销已完成。

**ROTATE 报告确认→7C 关闭/Work 事实续接离线片（基线 `3c8a1add`，本轮按授权提交）：**在上述 7B 接缝的报告已冻结、成功 PUT 与 ACCEPT 身份一致、当前报告有匹配 `RECORDED/DUPLICATE` 的离线前提下，`P/application/placement_flow.py::_reported` 允许普通目标或 ROTATE，继续拒绝 REPLACE。沿既有 typed report reader、`MaterialReportedFact` / CompleteExecution 与 FactProcessor 应用事务，同事务关闭本盘、发布报告结果，并按原条件保存 `Work.top_removed_evidence_id`；没有新模型、状态、迁移、SDK/wire 或基础层流程。

下一来源授权仍只在 ACCEPT 原事务交接；关闭时不重新授权、不消费下一来源。Work 尚持本盘 ACCEPT 且未消费后继时，必要可靠取出事实从未闭合 M1 交给当前 Work；授权已消费或推进时，不覆盖当前游标/取出事实、不等待 M2 的取出或目标就绪。没有后继由 ACCEPT 明确表达，不因本盘 CLOSED 而关闭 Work/Bin/PickingTask。报告已冻结后不重新索取 PkgID、扫码、设备参数或原 ROTATE 就绪投影，也不要求已闭合准备历史持续存在。

主 PostgreSQL owner 为 `T/integration/test_material_close_postgresql.py`，复用既有普通目标关闭断言及已提交 ROTATE→PUT→7B 装配。ROTATE fixture 可显式给定完整 ACCEPT，覆盖有/无 `next_source_action`；新增接缝验收承接后退役 7B 的“ROTATE 报告不得关闭”阶段性负例，REPLACE 负例保留。聚焦验证报告两种确认、M2 尚未创建、并发来源准入、关闭/Work 事实两侧事务故障回滚、原结果重放、授权已消费/推进、可清理 M1 历史删除后的独立 Work 恢复及错误证据不关闭；普通目标原矩阵仅刷新 helper 直接消费者，不重复 9H 或共享 worker/租约矩阵。

恢复 owner 不变：未确定报告义务归 Confirmation，已保存未应用结果归 FactProcessor 领取/延期/租约恢复；关闭前必要取出事实归原来源链，关闭提交后归当前 Work 与 batch/source flow。当前引用的 ACCEPT、取出 Evidence 及必要 owner 保留为当前数据；清理已闭合 Pickup、Command、报告和不再引用的位置历史后仍可继续。故障测试为应用事务注入，不宣称真实进程失效或现场验收。C1/C2 继续待补证且不扩大为该离线关闭片门禁；G2/G3/G5 仍约束对应真实入口和新事实/报告权威来源。本片不发送 WMS 请求、不启用生产装配/真实派发，不接 REPLACE 或机械臂，不计为任务 7/11 或完整业务完成。

本片验证：新增有后继正例先在原 7C 类型守卫上失败；最小修正后新增 ROTATE 接缝 15 passed，随后退役 7B 阶段性负例。最终快照 PostgreSQL 接缝/普通目标 helper 消费者/7B→7C 与 ROTATE→PUT 直接回归 29 passed（116.76 秒），修改后的 ROTATE fixture 既有 REPLACE 字符串输入回归另 1 passed，共 30 项、0 skipped；插件 FAST 252 passed，退役后的拓扑/测试所有权 14 passed，4 个 Python 文件 Ruff/格式和 diff 检查通过，HEAVY selector 为 NONE。沿本片独占 PostgreSQL 17 tmpfs、逐例独立逻辑库验证；无 schema/宿主/队列变化，不刷新无关迁移、完整 QUALITY 或真实进程矩阵。独立只读 full review 核对固定 5 文件指纹、合同、直接/间接消费者及测试 owner，结果无 P1/P2/P3 finding；评审后仅补本文记录，可执行快照未变。最终状态止于 `IMPLEMENTED - FOCUSED VERIFIED`，不提交或部署。

**REPLACE 旧目标架离场最小离线片（实施基线 `92b79281`，未提交）：**仅在本盘已发布 ACCEPT、精确旧架及原进场轮次、当前原因果到位、已发布 departure READY 和完整目的参数显式给定时，沿 manual-picking 的 TRANSFER_RACK / F01 搬运基线创建旧架离场。新增 `P/application/target_replacement.py` 与其 repository，经既有 target flow 显式注入；使用原进场 client_request_id 派生稳定的旧轮次关联，同一旧架轮次的多个 Pickup 共享原 F01。Transport、Binding 及 Pickup 的可空非唯一 FK `target_departure_transport_task_id` 同事务成立。迁移 `6a7b8c9d0112` 承接 `f489f59e9b22`，存在当前引用时拒绝有损降级；不新增业务模型、状态、DTO 或 WMS operation。

恢复 owner 不变：Transport 保存冻结请求、接纳/未知/终态与原身份，既有窗口机制在 ACCEPTED 后交接；WorkLine 绑定与原 READY 继续承担该离场义务。batch 先恢复原义务，FactProcessor 经静态目标离场 step 应用发布匹配结果，未装配消费者时持久延期。已关联原动作不重新读取旧进场或设备映射；新关联必须校验当前旧轮次；同轮合法目标 ROTATE 后按当前投影精确读取成功 Transport/Binding/已发布结果，要求原进场窗口未释放、因果顺序及当前架面/位置一致，不能误绑旧轮次或使用未发布换面结果。迟到旧结果不能覆盖新轮次。原 Transport/Binding/当前必要 Evidence 的恢复代码不读取 Pickup；本片实际清理验收删除旧进场及到位历史，保留尚未闭合 Pickup，不将“无 Pickup”写作已实测项；不将已闭合历史当运行围栏。初始目标进场的插件通知可能合法 IGNORED，须依原成功 Transport 与当前原因果投影证明到位，不伪造插件发布。

主要验收 owner 为 `T/integration/test_target_replacement_departure_postgresql.py`，覆盖唯一创建/关联、共享同轮离场、并发、创建/关联事务故障回滚、未发布 READY 或未知位置不建动作、回调早于 ACK、接纳窗口释放、原结果重放、错误身份、旧轮次隔离、清理后恢复、未装配消费者延期及迁移往返保护。故障为离线事务注入，不宣称真实进程崩溃或现场通过。仅刷新普通 PUT、ROTATE 接缝与 REPLACE 报告拒绝的直接消费者；不重复 9H、共享租约/worker 矩阵。

最终验证：本片 PostgreSQL 19 passed（68.26 秒）、直接边界回归 4 passed、插件 FAST 252 passed、selector/拓扑/测试所有权 250 passed，迁移 mapping 所选核心 PostgreSQL 8 passed，均无 skipped；Ruff/格式、diff 检查通过。独立 full review 发现同轮合法换面被初次进场因果守卫误拒的 1 项 P2，补齐当前原因果读取及三项接缝验收后，原 reviewer 一轮合并闭环与 fresh full review 确认已修复，当前 11 文件范围无 P1/P2/P3 finding。初轮集成失败先定位为离线夹具合同/装配缺失，修正中复用 matcher 的构造遗漏亦已修复，均不算通过证据；最终快照以上述 XML 为准。隔离 PostgreSQL/Redis 已清理，HEAD 不变、index 为空，状态止于 `IMPLEMENTED - FOCUSED VERIFIED`；本轮不跑提交用完整 QUALITY，不重复 9H 或共享 worker 矩阵。本片不接首次 departure 决定/WAIT、新架进场、PUT、报告、关闭或机械臂；不发送 WMS 请求、不启用生产装配/真实派发、不提交或部署。G2/G3/G5 继续只约束依赖真实扫码关联、设备映射和权威 PkgID 的路径；C1/C2 继续待补证且不扩大为本片离线门禁。既有目标 ROTATE 的角色模板问题另按原计划处理，本片只复用已确认 F01，不顺带改旋转协议。

**REPLACE 新目标架 F01 进场→ReadyTarget 最小离线片（基线 `947a979f`，未提交）：**沿原 replacement flow，在本盘已发布 REPLACE ACCEPT、精确旧架离场已正常 ACCEPTED 交接或已发布成功、TRANSFER_RACK 精确工作位与完整搬运参数显式给定时，复用 manual-picking 的 F01 / RACK→RACK_POSITION / 目标面与 `create_windowed_inbound`。新架/面/储位来自本盘原 ACCEPT，不重问物料资格、不修改初始 plan.target_rack。既有窗口负责下发节拍，不等价物理到位；旧架发送未知、未接纳及已接纳后 FAILED 均不在本片新建准入内，不自动换身份重试。

只新增 Pickup 可空非唯一 `target_inbound_transport_task_id`；迁移 `b514fdcfbefc` 由 Alembic 生成，承接 `6a7b8c9d0112`，有当前引用时拒绝有损降级。原旧架离场身份与新架构成稳定 round correlation，Transport/Binding/本盘引用同事务落地；同轮同新架的多个本盘引用共享原动作。窗口 PENDING 由原 batch 重扫，REUSED 只有找到本轮原绑定才关联，不冒领其它轮次。已有新架引用优先校验恢复，不重新读取旧进场、旧离场、位置绑定或映射作为恢复条件。

唯一恢复 owner：尚未建进场的当前 Pickup/ACCEPT 归 batch 的 replacement flow；已建原 Transport 归 Transport 可靠机制；已保存未发布结果归现有 FactProcessor；已发布成功后由 replacement flow 只读计算现有 `ReadyTarget`。新增固定目标进场 step 沿原 publisher→scan→target flow 消费，不另建 worker、timer、授权模型或 READY 状态。ready 要求本盘冻结目标、原新架当前版本成功且结果已发布、精确成员终位/面、当前 PositionProjection 的原 Transport/causal token/FINAL_RESULT 同时匹配，窗口已释放、位置未知、旧轮次或未发布均不放行。结果接收与业务发布、ACCEPTED 与物理成功继续分开；无需依赖旧架已闭合历史。

主要测试 owner 为 `T/integration/test_target_replacement_inbound_postgresql.py`，复用旧架离场、当前 material 与 Transport 夹具，只补本片接缝：唯一/共享创建、原子关联、并发、事务故障回滚与重建、窗口等待、旧架正常交接准入、回调早于 ACK、消费者缺席延期/恢复、重放、错误事实/旧轮隔离、无 EXECUTING 与可清理前驱历史删除后的独立恢复，以及迁移往返/降级保护。保留当前未闭合 Pickup、ACCEPT、其必要 Confirmation 与原新架 Transport/Binding/Evidence，清理不再被当前引用的旧进场/离场 Transport 及到位历史；不宣称删除仍被引用的当前数据。仅刷新旧架离场、普通 PUT、ROTATE 直接消费者；不重复 9H、共享租约或真实 worker 矩阵，事务中断不宣称真实进程故障验收。

最终验证：本片 PostgreSQL 18 passed（74.39 秒），直接消费者 PostgreSQL 8 passed（31.77 秒），迁移 mapping 所选核心 PostgreSQL 8 passed（33.54 秒），插件 FAST 252 passed、selector/拓扑/测试所有权 250 passed，均 0 skipped；10 个 Python 文件 Ruff/格式及 diff 检查通过。首个正常接续用例先在旧 flow 未创建新架处失败；实现后首轮 16 项中的 1 项失败属于测试夹具误写重试字段，改为推进既有 FactProcessor 时钟后，回调恢复/共享/FAILED 三项及最终 18 项均通过，没有放宽业务断言。最终 publisher 同一 set 去重不改变可观察语义；独立只读完整 review 核对 12 文件指纹、调用/测试 owner、当前轮次、结果门禁和历史清理，无 P1/P2/P3 finding。评审后仅补本文记录，可执行快照不变；HEAD 不变、index 为空，隔离测试资源清理后保留工作区改动。状态止于 `IMPLEMENTED - FOCUSED VERIFIED`。不接 PUT、报告、关闭、机械臂、首次 departure/WAIT 或失败重决策，不改生产装配、不发送 WMS 请求、不启用真实派发、不提交或部署。C1/C2 只限制对应真实容量接续与核销，不扩大为本片门禁；G2/G3/G5 只限制依赖真实扫码、设备参数及权威 PkgID 的路径。离线完整搬运参数不是对未决设备值的默认推断。

- [ ] RED：普通目标、ROTATE、REPLACE 顺序、目标未知、PUT 回调早于 ACK，以及 M1 报告先确认但 M2 尚未创建时重启。
- [ ] 按被冻结目标和当前权威位置准备货架；REPLACE 执行 departure 决定→旧架离场→新架进场→PUT，已确定物料资格不重问。
- [ ] 匹配成功 PUT 后保存位置并同事务形成报告义务；沿本盘原始 barcode 核对已发布决定，独立物料编号门禁已撤销。
- [ ] 仅报告 RECORDED/DUPLICATE 且授权交接完成后闭合 M1；M2 不必已创建，当前 Work 继续拥有授权；RACK_SLOT 无 Work 交接条件。
- [ ] GREEN：可靠取出且获后继授权即可与 M1 PUT/报告并行下发 M2；未知栈顶不越过，无平台锁、释放事件或跨臂统一等待。

**恢复 owner：**目标准备 Transport、PUT Command、报告 Confirmation 各由原基础对象恢复；插件 driver 根据其 typed 事实推进当前盘。
**验收：**本盘物理完成、WMS 记录、后继授权三个条件分别可追溯；G5 已由 barcode 统一收敛；真实接口仍需 G2/G3 及目标 ROTATE 的 CK04 合同。

### 任务 8：正常流出、回程 FIFO 与回架分配门禁

**依赖：**NO_WORK 入口只依赖任务 5 的 Work/点2交接、既有方向命令和回架 typed 端口；正常逐盘结束入口另依赖任务 6–7 的明确无后继与本轮逐盘收口。回程/FIFO/运动前门禁可独立验证，不等待全量取盘或 G5。**新增：**`P/application/{return_model,return_repository,return_flow}.py`、`P/handlers/bin_flow.py`；扩展 Work、batch driver 和部署模型注册。
**测试：**新增 `T/{test_bin_release,test_return_fifo,test_return_before_motion}.py`、`T/integration/test_returns_postgresql.py`。
**接口：**复用现有 BIN_MOVE、return_batch 与 departure typed 能力；回程 execution 为本插件私有，不导入人工 BinLineReturn。

- [ ] RED：NO_WORK/明确无后继后流出、首次 SCAN3 交接、SCAN4 首次物理时间排序、重复扫码、未知队头及同架面连续两轮分配。
- [ ] 随片增加回程记录和迁移；Work 业务结束与物理交接分开，READY 候选取连续前缀，未入线/位置未知不加入 FIFO。
- [ ] 将运动前阶段区分真正无候选、已应用发布 NO_BATCH、READY 分配、未知/未发布；先检查本轮原义务再判断候选，不能从空查询推断可离场。
- [ ] Return 可按明确 ACCEPTED 交接，但位置及同架面下一轮分配继续等待已发布权威终态和已知终位；本架直接 BIN_MOVE 未收口不换面/离场。
- [ ] GREEN：运动前求值一次，冻结后新候选不重开；其他独立架/面仍可推进，恢复不扩大为全线围栏；单独证明 NO_WORK→流出→回程不读取 PkgID、不创建虚构单盘、不等待取盘/PUT。

**恢复 owner：**回程与当前分配归插件 batch driver，已建请求/Transport/Command 归基础机制。**验收：**未知不当 EMPTY/NO_BATCH，首次 FIFO 顺序及原请求身份跨重启不变。

### 任务 9：跨任务 drain 与完成确认收口

**当前分项状态：**以下为原始整项验收清单，不代表 drain 尚未实施。9A–9H 已确认路径的离线证据按前文切片记录，9H 当前提交与 owner 见[收口索引](#task9h-offline-closure)；正常 revision>0 完成确认及退料架级收口已在显式正常链装配中离线实现；revision 0 首批期限、生产装配、WMS 容量接续/核销及真实设备验收未完成，故不整体勾选任务 9。初始 drain WAIT 的下一任务优先准入与补架 WAIT 的原责任续行分别沿各自既有规则，不能因本清单重做或互相套用。

**依赖：**任务 8 的当前 FIFO/回程能力、任务 5 的确认进度与 prepare 协调，不要求任务 6–7 或 DirectPick 设备路径先全部验收。实际任务完成仍逐项等待其必报事实。**新增：**`P/application/{drain_model,drain_repository,drain_flow}.py`；扩展 batch/completion driver 与插件 composition。
**测试：**新增 `T/{test_drain_flow,test_completion_flow,test_recovery_owners}.py`、`T/integration/test_drain_progress_postgresql.py`。
**接口：**drain 使用已有 workline-owned facade，完成确认使用 PickingTaskCompletionScheduler；下一任务准入调用 `PickingTaskPrepareCoordinator.prepare_next_in_session(db, line, now=now)`，注入插件应用层，不新建任务、claim 模型或 reservation API。

- [ ] RED：父任务完成且无新事件时 WAIT 到期、并发扫描、候选数量变化、无需求退出、READY 原范围与新任务并存；另覆盖可 claim 的 QUEUED 与 drain 同时推进、已有 PREPARING/EXECUTING、prepare 被阻断和整个准入事务崩溃。
- [ ] 新建 drain 或确定 WAIT 到期重问前，由 batch owner 在同一 WorkLine 锁事务内先核对当前 drain。原请求未决或 READY 时沿原引用恢复，不重问、不覆盖或释放 reservation；只有准备创建新决定时进入下一任务优先准入。
- [ ] 同锁内已有 PREPARING/EXECUTING 时不创建新 drain；否则先调用既有 prepare 协调器，claim、绑定、prepare 义务在当前事务原子提交。成功准备任务即退出本次 drain 准入，由下一任务的后续当前架承接 FIFO，不复制人工 drain 的容量窗口逻辑。
- [ ] 不能把 `prepared=False` 当成“无任务”：仅明确 `NO_ELIGIBLE_TASK` 且当前 FIFO 非空、其他准入成立时创建新 drain；WORKLINE_NOT_READY 等结果保留原因并退出本次扫描，由既有持久准备入口恢复，不越过候选自行创建决定。
- [ ] 随片保存 WorkLine 当前 drain 请求/决定、到期时间及冻结回程成员；WAIT 关闭原决定。到期无候选或下一任务优先时结束本次等待；只有重新通过上述准入时，才同事务以新 operation_id 创建一个当前请求并替换引用。
- [ ] READY 沿原 reservation 和原成员推进，新增候选不扩展原范围；WMS 决定容量及释放，插件不计算库存/容量，也不按最近历史响应恢复。
- [ ] 完成确认只看当前业务义务及已确认必报事实，保留 revision 0、STALE、BUSINESS_IN_PROGRESS 分支；任务完成不替代 Rack/Bin 清场。
- [ ] GREEN：通过前置 batch 钩子恢复已完成任务遗留义务，未闭合时阻止停用/换绑；不实现停线/切换触发 drain。

**恢复 owner：**drain/回程及新决定准入归插件 batch owner；它在事务内调用宿主 prepare 协调器，已 claim 的任务交回既有 prepare 生命周期，不另建恢复队列。确认业务时机归 completion owner，已建义务归基础派发。
**验收：**可准备任务与新 drain 不会在竞争中同时获准；WAIT 到期同样优先下一任务，原未知/READY 不被该判断覆盖；提交前崩溃全部回滚，提交后丢通知由对应持久入口恢复。每个非终态有当前记录、唯一 owner 和扫描入口，不新增通用 timer、整线 Session 或手工续行接口。

### 任务 10：直接取料、取消与 revision 范围完成

**依赖：**取消/范围完成依赖任务 4–5 的计划事实、成员关联及既有接收；设备动作复用任务 6–7 的原 Command/单盘机制并受 G4 约束，不要求任务 8–9 全部完成。2026-10-23 已确认 ARM02 退料单盘与现有 RACK_SLOT 一致；本轮已实现显式输入下的 DirectPick 离线正常链，真实设备归属及定位仍未冻结。**新增：**`P/application/direct_pick_flow.py`；扩展本插件 handler、flow、completion/batch driver。
**测试：**新增 `T/{test_direct_pick_flow,test_cancellation,test_return_rack_completion}.py`、`T/integration/test_direct_pick_progress_postgresql.py`。
**接口：**沿用现有 DirectPick 成员、arrival_report、direct_pick_completed、return_rack.task_completed 的基础接收及 typed reader；不导入人工结果消费者。

- [ ] RED：同架跨 revision 成员、单成员取消、through_plan_revision 范围完成、完成后追加、迟到结果与新的因果位置。
- [x] 为有效直接成员复用 Pickup/MaterialExecution 离线正常路径，沿当前成员与精确到位授权；退料取盘复用 ARM02 实际绑定，正常来源仍为 ARM01。直接取料优先仅作用于同一物理臂的可执行工作，不抢占已有 PUT/取盘 Command、不停止独立 ARM01 或 CTU/Bin 流；不创建虚构 Work。
- [x] 到位报告独立可靠交付，整架完成先等连续版本齐全，只关闭覆盖范围，不补造逐面证据；取消停止无依据新动作，已发生事实和已有报告继续收口。
- [x] GREEN（离线）：直接取料不等待不存在的 Work 授权交接，当前成员取消阻止新动作，既有义务仍恢复；真实设备归属和定位只限制该设备路径，面级/整架完成另行实施。

**恢复 owner：**范围完成 Evidence 延期由 FactProcessor 重领，业务关联由自动插件消费；直接取料及离场归插件 driver，物理对象沿原身份履约。
**验收：**旧 revision 不关闭新成员，原到位生命周期可复用，新轮次不依赖可清理历史；设备映射确认后才允许实际取盘。

### 任务 11：最终持久化、worker 与正常闭环验收


**前轮料箱到位后正常业务装配证据（基线 `a4d25e65`，未提交；REPLACE current/schema 本轮补齐见下）：**搬运沿已验证 manual-picking 能力与本分支既有装配复用，不再把每个接缝作为独立实施片。`P/application/transport_composition.py::build_picking_plugin` 在显式提供 physical/scan/target/put 读口、源/目标请求映射与货架准备参数后，统一关联 source、material、target、placement 和原 batch/scan。复制装配对象，保留原 transport plugin 不变；deployment 仍使用搬运装配，未启用本生产入口。该轮无新增 schema、SDK/WMS DTO、公共状态、队列或通用模型。

单盘扫码沿已有 business evidence consumer 精确绑定原 Pickup/来源 Command；同一 Evidence 交给 material Fact 创建首个可靠请求后才发布，不新增事件 registry。Work READY、WAIT 到期、后继授权消费与 LIFO、下一盘并行复用 6A–6D。REPLACE 的 ReadyTarget 接回共用 7A；普通/ROTATE/REPLACE 的精确成功 PUT 均接回 7B/7C，保留本盘可靠取出、原 Command 优先、本盘扫码原文、报告位置/关闭原子性，不重交授权。真实设备映射未决时由读口返回未知，不构造默认值。

正常流出补齐原 Work 当前明确无后继 ACCEPT、无待消费授权且本轮所有 Pickup/MaterialExecution 义务已关闭的准入；当前事实归 Work，未绑定物料或未关闭的 Pickup 继续阻止本箱流出。沿既有 EXIT_PENDING 和点2 Command，与 phase/关联同事务创建，不新增业务状态；原 Command 恢复先于重做准入。首次精确 SCAN3 复用已验证回程交接，不以 ACK 或任务状态替代到位，已有回程 FIFO/回架/drain 矩阵不重复。

唯一 owner 不变：当前 Work 授权由 source/batch 扫描，来源/PUT Command 与货架 Transport 归原基础可靠机制，已保存未应用事实归 FactProcessor，位置报告归 Confirmation，报告后当前 Work 和回程各沿现有 owner。确定未知、失败及未接入异常不换身份制造成功；已闭合 M1 不是 M2 继续或本箱正常结束的查询依据，仍被当前授权引用的 Evidence 保留为当前数据。

主要新增验收 owner 为 `T/integration/test_normal_picking_postgresql.py`，一次覆盖真实插件显式装配中的一/两盘循环、扫码持久交接、WAIT、下一来源与当前 PUT/report 并行、普通/ROTATE/REPLACE 目标分支、报告关闭/正常点2流出/SCAN3、原身份重建、事务中断、缺可靠物料身份延期和清理已闭合 Pickup 后继续。已有 6A–7C、F01、窗口与 9H 证据复用，只刷新受影响直接消费者；新增 REPLACE 正例通过后退役原7B/7C“REPLACE尚未接入”的阶段性负例，保留缺取出、未就绪和未装配准备流程的负例。故障注入为离线事务中断，不宣称真实进程失效或现场机械臂验收。

真实接口逐项门禁与已确认 WMS 字段对照集中在[机械臂附录 §10](../../integration/device-annex-automatic-picking-arms.md)。O1/O2、O6/O9、O10/O11/O15/O16 仅限制其真实接口，独立物料编号门禁已撤销；目标架 CK04 与现行 typed CTU02 的差异单列为目标 ROTATE 真实派发合同门禁；不扩散到普通目标/F01换架、现有可靠机制或其他已确认路径。C1/C2、直接取料及首批期限不扩大为本轮到位后业务阻塞。REPLACE 首次 departure/WAIT 的原阶段边界由下述完整接续实施取代；普通目标、ROTATE 及既有搬运/报告证据继续复用。

最终验收：统一装配 PostgreSQL 5 passed、受影响直接消费者 PostgreSQL 15 passed、插件 FAST 252 passed、测试拓扑/所有权 14 passed，均 0 skipped；11 个 Python 文件 Ruff/格式及 `git diff --check` 通过，HEAVY selector 为 NONE。独立完整只读 review 核查固定 13 文件指纹、直接/间接消费者、原身份恢复、当前事实/历史清理与测试承接，无 P1/P2/P3 finding；仅补本文及附录的边界说明后，可执行快照不变。记录位于 `reports/normal-loop-{main-final,direct-final,fast-final,ownership}.xml` 与 `reports/normal-loop-verification.json`。状态为 `IMPLEMENTED - FOCUSED VERIFIED`，不表示机械臂现场验收、完整 REPLACE 入口或任务 11 全部完成。不提交、不部署、不发 WMS 请求、不启用真实派发；未改 Celery/Beat 或生产注册，不重复 9H/共享机制的真实 worker 矩阵，也不执行仅提交所需的完整 QUALITY。

**REPLACE 首次 departure 与换架拣料完整接续（承接上述未提交正常装配）：**复用 manual-picking 的 WorkLine-owned `outbound.rack.departure_decide@v1` / task_id=null、WAIT 新 operation_id 及原 destination 语义，实际请求沿现有 typed facade、RackDepartureScheduler、Confirmation 可靠机制创建。首次精确读口不给 operation_id 即表示由插件创建首次请求；已有显式已发布 READY 输入仍可作为冻结义务接入。同一原进场轮次仅有一个当前请求，同轮多个 Pickup 原子共享/推进；不查询 latest 请求作为身份。Pickup 仅增加 `target_departure_confirmation_id`、`target_departure_round_id`、`target_departure_retry_at` 三个可空持久字段，迁移 `8c9d01122445` 承接 `b514fdcfbefc`，存量均为空且有当前引用时拒绝有损降级，不新增业务状态、模型、队列或 timer。

首次请求与 current/原轮次同事务，确定 WAIT 的固定到期点为原 Evidence.received_at + retry_after_ms，应用/发布交给原 FactProcessor。到期前、结果未知或未发布时不新问；到期以新身份创建 Confirmation 并同事务替换同轮所有 current，旧结果不改新游标。唯一业务恢复 owner 为原 target/batch 前置扫描，不依赖 EXECUTING；Confirmation 继续负责原请求可靠交付，既有 DeviceCommand/Transport 负责原动作技术恢复。READY 发布后以原轮次创建/关联唯一旧架 F01，ACCEPTED 交接窗口后创建新架 F01；精确新架到位后复用本盘可靠取出门禁和原 PUT、权威 PkgID 报告与关闭，物料 ACCEPT 资格和 Work 授权不重问、不重交。旧架 F01 已关联后，恢复不再读取可清理的初始进场/到位历史或旧 WAIT。 同轮原 F01 已存在时，先核实冻结 Binding/READY 并关联该原身份，不再要求旧架仍在位；第二盘晚于旧架 ACCEPTED 加入仍可共享原离场和新架进场。此前“清空关联后再以变化投影拒绝原 F01”的阶段性断言改为验证恢复同一原 Transport、绝不新建动作，首次未证明到位不得创建的负例继续保留。

主要验收 owner 仍为 `T/integration/test_target_replacement_departure_postgresql.py` 与 `T/integration/test_normal_picking_postgresql.py`，前者补首次/WAIT/current 的事务与身份差异，后者从首次请求经 WAIT 到完整换架、PUT、报告和关闭；现有 F01/ReadyTarget/报告矩阵不另复制。两个旧迁移用例仅在已有 F01 交接后退役本次后加的 current 字段，继续验证各自原迁移，不扩大或放宽新迁移的活引用保护。C1/C2、扫码关联、设备参数、PkgID 与 CK04/CTU02 继续按各自路径保留门禁；不以本片解除生产装配、真实派发或现场验收限制。最终统一验收：修复后 PostgreSQL 52 passed（179.39 秒），覆盖本次 departure/current、旧架/新架和完整正常装配，0 skipped；selector 所选核心 PostgreSQL 8 passed，源/drain 路由 2 passed，插件 FAST 252 passed，selector 237 passed、测试拓扑/所有权 14 passed。18 个当前 Python 文件 Ruff/格式与 diff 检查通过。独立完整 review 发现第二盘晚于旧 F01 ACK 加入会被旧在位门禁卡住的 P1，先由精确失败用例复现，再修复为冻结义务优先恢复；原 reviewer 一轮合并闭环与 fresh full review 确认 P1 关闭、无新增 P1/P2/P3。最终 XML 为 `reports/replace-departure-{reviewed-final,core-heavy,routing,fast-final,selector-final,ownership}.xml`，指纹/环境与结果记录在 `reports/replace-departure-verification.json`。首轮测试夹具 processed_at 与迁移 selector 精确列表遗漏已分别修正，早期失败不计为通过证据。HEAD 不变、index 为空；状态止于 `IMPLEMENTED - FOCUSED VERIFIED`，不提交、不部署、不发 WMS 网络请求、不启用生产装配或真实派发。

**正常机械臂已确认接线（基线 `54b034c3`，本轮未提交）：**集中补齐 `P/application/picking_device_ports.py` 的公共命令映射、来源 SCAN2/可靠取出、当前目标及 REPLACE 原架/原进场轮次、确定 PUT 成功读口。直接复用既有 Work/source、target/batch、DeviceCommand/Transport、DeviceEvidenceService、FactProcessor 和 Confirmation owner，不增加模型、业务状态、timer、迁移或生产注册。参数工厂统一为同事务 async 端口；只映射已确认角色、PICK_AND_PUT、原业务身份与冻结绑定时限，完整位置正文未给定时不创建命令。精确扫码关联核对后与单盘身份原子保存，不要求公共事件入口猜测来源命令。

本轮真实入口验收发现并最小修正设备摘要与 Fact 版本的接线差异：设备 Evidence 的摘要沿宿主 UNIFORM_WIRE 规则，相关静态 handler 显式消费统一设备合同 `1.1`，保留 WMS `1.0` 的原身份。来源结果由既有 business consumer 发布后才能作为可靠取出依据；PUT 的确定结果在原 FactProcessor 内读取，报告义务与位置/发布保持原子，不提前要求本次 PUT 已发布。权威 PkgID 缺失时保留原 Evidence 及 Command，不猜 barcode 或 UUID。

主要测试 owner 为 `T/integration/test_picking_device_ports_postgresql.py`，只补真实 DeviceEvidenceService/FactProcessor 与新读口的业务接线；普通目标完整离线链、显式扫码绑定的回滚/身份拒绝、来源发布门禁、严格单盘成功、未知或错误目标、完整参数/PkgID 缺失、并发/重建/重放及 REPLACE 原轮次发现。`T/test_picking_device_ports.py` 拥有公共角色/合同/时限映射。独立 review 复现并修正真实读口下第二盘晚于旧架 ACK 加入 REPLACE 的接线缺口：现有换架 owner 从精确相同目标架/架面的未闭合义务恢复唯一原 round/current；歧义报错保留身份，无论单盘自身是否已关联 incoming，都沿原 departure＋目标架的唯一 correlation 检查实际 Binding，已释放的新架窗口不冒领，原进场历史不是恢复输入。目标已权威就绪时允许读取现有 ReadyTarget，不创建额外搬运。新 owner 补 ACK 后、新架到位并关闭/清理原 Pickup 后两种加入时序，以及 peer 未保存 incoming 引用时仍排除已释放轮次。既有 6A–7C、REPLACE 完整链和历史清理证据复用，只刷新 async 调用点、摘要校验及该修复影响的直接回归，不复制 9H、租约或 worker/Beat 机制矩阵。

实际接入仍需机械臂附录 §10 所列设备位置编码与数值格式、精确扫码设备/来源命令字段，以及仅影响目标 ROTATE 的 CK04/CTU02 合同对齐。C1/C2 不扩大阻塞正常链；O6/O9 的 business_key/正式时限仍保留确认项。隔离测试输入不是设备协议冻结或现场验收，本轮不接生产装配/真实派发，不发送 WMS 请求。

本轮修复后的最终业务 PostgreSQL 验收 **58 passed、0 skipped，3 项未受本轮代码变更影响的既有迁移用例按名称 deselected**（本轮无 schema 变更）；插件 FAST **255 passed**，拓扑/所有权 **14 passed**，18 个 Python 文件 Ruff/格式、文档链接与 diff 检查通过，HEAVY selector 为 NONE。独立完整 review 及修复闭环确认 P1/P2 均关闭，无新增 finding；最终业务 XML 为 `reports/arm-ports-reviewed-final.xml`，审查与环境指纹见 `reports/arm-ports-{review-result,verification}.json`。扩展回归的旧 `test_plugin_source_models_match_migrated_schema` 发现 `target_rotation_transport_task_id` 唯一约束命名差异，使用 HEAD 原版测试副本复现同一失败；对应模型/迁移未改，作为既有基线失败单列，不把扩展回归称为全部通过，不在本轮扩大 schema 修订。早期加载修复前代码的结果仅用于未失效消费者证据，不能替代本次最终业务结果。状态止于离线实施与聚焦验证，不提交、不部署、不启用生产装配/派发、不发送 WMS 请求。

**已确认扫码适配续接（基线 `122a6f48`，本轮未提交）：**新增 `PickingMaterialScanReader`，直接读取已接收、已应用的统一 `SCAN_COMPLETED` 原文条码、平台及时间，核对 Evidence 元数据/摘要、原来源 Command/Pickup/Work 和冻结目标平台。精确来源关联必须由显式端口提供；绑定提交后沿原 Evidence 恢复，不依赖端口再次提供关联。来源尚未下发、缺关联或工作线暂停时，现有 FactProcessor 以 DEFERRED 留存重扫，避免已知扫码在取得关联前落入忽略分支；同事务绑定/首次请求仍沿原 material owner，不增加模型、状态或 timer。

隔离装配改用真实扫码读口，复用普通/REPLACE 正常链证据。新增 PostgreSQL 只验证缺关联、暂停、并发绑定及移除外部关联后重建防重；FAST 验证条码原文和长度、平台/身份错误及来源未下发。主要 owner 分别为 `T/integration/test_picking_device_ports_postgresql.py` 与 `T/test_picking_device_ports.py`；`test_normal_picking_postgresql.py` 为既有语义装配的直接回归。设备最终扫码身份与来源关联 wire 字段（O1/O2）、位置参数编码、CK04/CTU02 仍按各自依赖路径阻塞真实接入；不解析未冻结字段、不添加生产默认，不以离线显式输入解除门禁。C1/C2 不扩大阻塞正常链。本次不提交、不部署、不发外部请求、不启用生产装配或真实派发。

本次验证：插件 FAST **267 passed**；真实扫码读口及接线 PostgreSQL **10 passed**，既有正常业务装配 PostgreSQL **6 passed**；拓扑/所有权 **14 passed**；5 个 Python 文件 Ruff/格式、diff 检查通过，HEAVY selector 为 NONE。快照和环境索引见 `reports/known-arm-verification.json`，测试记录见 `reports/known-arm-{pg-dev,fast-final,normal-final,ownership}.log`。这些是隔离装配及事务证据，不代表真实设备、网络联调或生产装配通过。旧 schema 约束命名失败继续引用上段基线记录，本次无模型或迁移变化，不扩修、不重复该矩阵。

**依赖：**目标分支所需任务与未决项闭合。**新增：**`T/integration/test_worker_recovery.py`、`T/e2e/test_normal_picking.py`；核心有状态测试只补其新基础机制的主要 owner。
**范围：**全量需求对照、固定最终 diff/环境指纹，不扩大到未获批 NG/空取或供应商私有协议。

**已覆盖与剩余差额：**搬运、正常投料到 Work 和 Work WAIT 宿主扫描已提交至 `432a0490`，提交、主要测试/恢复 owner、证据计数及剩余门禁统一见[当前证据收口](#task11-transport-work-evidence)；9H 指定真实进程检查点继续引用原索引。manual-picking 搬运现场基线按用户确认复用，后续聚焦料箱到达工作位后的自动化业务，不机械追加同义运输/worker/Beat/回调矩阵。以下仍是任务 11 最终范围，需在相应业务和输入闭合后验证，不因未勾选重复已有有效证据；C1/C2 仅限制对应真实容量分支，机械臂映射/扫码/首批期限各限制依赖路径。

- [ ] 独占临时 PostgreSQL，逐片迁移按最终链在干净逻辑库完成规定 base→head；原模型、Alembic 注册/过滤和约束一致。
- [ ] 启动真实 Redis/worker/Beat 验证既有队列、序列化、提交后唤醒和周期扫描；逐个等待点验证重启/丢通知/租约回收，不以 skipped 计通过。
- [ ] 端到端覆盖来源架→投箱→SCAN2→work_plan→取盘/扫码→decide→PUT/report→后继→回程→完成，含 NO_WORK、WAIT、ROTATE、REPLACE。
- [ ] 在授权交接、请求创建、发送/结果保存、投影应用等边界注入中断，验证全部 Review Focus 及清理整体闭合历史后恢复新任务。
- [ ] 最终一次 full Review 同时闭合反馈和当前 diff；运行当前快照必选门禁，已有有效证据不重复。协议、本地 Mock、真实 worker 和供应商物理验收分开报告。

**恢复 owner：**逐项核对设计第 7 节，无“等消息”而没有持久查询依据的状态。**验收：**Bin 与 DirectPick 分别报告已实现、聚焦验证、持久化/worker、WMS 及物理验收，不把正常模拟闭环写成整线现场完成。

## 验证命令、门禁与交付证据

以下为实施命令模板，不是本次执行记录；已完成切片的实际测试及范围见各片证据。各切片先跑 Files/Test 所列的最小文件集，目标输出是相关断言通过且无非预期 skip。

```bash
# 合同首轮只跑变更文件；公共传播完成后再跑一次所需领域
rtk uv run pytest tests/contracts/wms_adapter/outbound_picking/test_material_decide.py tests/contracts/wms_adapter/outbound_picking/test_source_empty.py tests/contracts/wms_adapter/outbound_picking/test_movement_report.py -q
rtk uv run pytest tests/contracts/wms_adapter/outbound_picking/ src/wes_plugin_sdk/tests/ tests/api/test_qa_regression_transport_openapi.py -q
# 新插件 FAST 使用本切片明确文件，勿递归误收 integration/e2e
rtk uv run pytest workline_plugins/automatic-picking/tests/test_source_authorization.py workline_plugins/automatic-picking/tests/test_arm_concurrency.py -q
# provider 由现有入口导出；检查实际 schema/示例及受影响消费方
rtk uv run python scripts/export_release_provider.py --out-dir reports/automatic-picking/provider
rtk git diff --check
```

插件新增的 PostgreSQL/worker/E2E 测试只有在显式启用、数据库/Redis/worker 就绪时运行其精确文件；环境契约沿现有项目约定核对，不臆造环境变量。
Transport 专用 `docs/contracts/openapi/wes-wms-transport.openapi.json` 不因出库业务示例更新而重写；若对应 builder 真有变化，再生成并验证它。
前端若确有生成合同消费者则同步其实际生成物与校验，不自行增加页面或界面改造；没有影响时记录 NONE 的证据。

- [ ] 每个生产符号编辑前执行 upstream impact；每个迁移由 `uv run alembic revision -m "中文说明"` 生成随机 revision，不手写编号。
- [ ] 每片新表都同步 deployment 模型注册/过滤、迁移与相关测试资产；共享机器合同/核心模块补精确 HEAVY mapping，插件测试保持独立。
- [ ] 本地按实际差异使用 unstaged selector；最终含 Commit 时用 staged selector，仅执行输出 manifest，NONE 有效，不默认跑全量 HEAVY。
- [ ] 交付目标包含 Commit 时由 hook 产生完整 QUALITY，不使用 --no-verify；只有授权 Commit/Ship 才进入暂存、提交和后续动作。
- [ ] 验证证据记录命令、结果、执行树/环境指纹及日志；代码或环境变化只刷新失效部分。Review 不重复有状态门禁。
- [ ] 最终扫描限定范围的 `cell_ids`、`PickingSixInOne`、旧后继枚举、兼容导出及孤儿 helper；核对每个残留的所属语义，而非按关键词全仓删除。

## 需求覆盖与执行边界

| 设计要求 | 主实施切片 | 交付前必须具备 |
| --- | --- | --- |
| 已确认 WMS 合同与完整消费者替换 | 1–3 | 规范化、typed 转换、OpenAPI/fixture 一致及旧路径残留闭合；Work 的 3A 与单盘 3B 分开验收 |
| 独立声明、职责分离、渐进接线 | 4 | 四种静态组合，核心/SDK 不反向依赖插件 |
| 准备、投料、Work、revision 0 恢复 | 3A、5 | 无单盘时可创建/读取原 work_plan，当前进度、前置可达入口、唯一可靠请求及迁移验证 |
| Pickup、LIFO、单盘身份、原子授权交接 | 6–7 | 当前 Work 持有授权，M1 可独立闭合，崩溃重放不重复动作 |
| 目标与双臂并发、必报事实 | 7 | G3/G5 证据、匹配 PUT、原报告身份及并发直接依赖 |
| 回程、运动前求值、同面分配门禁 | 8 | FIFO、未知依赖和确定无分配分开；NO_WORK 回程不依赖 PkgID/逐盘闭环 |
| 跨任务 drain、业务完成与物理清场分离 | 9 | 新决定前同锁优先准备下一任务，WAIT 到期有 owner，READY 原范围、完成后扫描可恢复 |
| DirectPick、取消、范围整架完成 | 10 | G4 对应证据、成员身份及迟到事实保护 |
| 每个非终态的确定 owner、清理历史后可运行 | 5–11 | 插件与核心各自的持久恢复证据，不以测试绿灯替代物理事实 |

本计划推荐主 Agent 在同一执行现场顺序推进，避免 SDK、converter、共享接线和插件 driver 的交叉修改。
当前搬运与 Work 入口验收已提交至 `432a0490`；本次仅收口其证据与剩余门禁，见[任务 11 当前索引](#task11-transport-work-evidence)。搬运沿用已确认人工线现场基线，后续重点为料箱到工作位后的自动化业务；不因任务 11 总清单未完成重复加测。C1/C2 继续待补证，本次不新增代码或测试、不提交、不部署、不启用生产装配或真实派发，不接机械臂、完成确认或已接纳后 FAILED 重决策，不做 bin-line-common 抽取。

### 本轮 barcode 统一实施（基线 bf7f4111，未提交）

主合同、出库 DTO、SDK、typed converter、OpenAPI 与插件报告/关闭链一次性切换为 barcode。报告数据拒绝旧字段，不设别名或兼容路径；长度与 decide 一致为1–1000字符。PickingPutReader 仅解释确定的物理成功，不再读取独立编号；placement 复用现有 scan_reader，从本盘 admission Evidence 读取原文并核对原 Pickup/Command、decide 的 barcode 与扫码时间，报告确认关闭再核对冻结报告原文。当前执行与 Work 授权仍用原本地身份，条码不成为全局唯一键。

主要验收 owner：核心 movement_report 合同（原文/长度/拒绝旧字段/typed 往返与 OpenAPI）、SDK 出库合同；插件 placement/close 与正常装配 PostgreSQL（同盘原文、错误扫码身份/冻结报告、并发/崩溃/重放/历史清理）。旧缺 PkgID 负例由原扫码/决定不一致拒绝及无需独立编号的正例承接，不复制搬运、9H 或共享恢复矩阵。既有已冻结义务不改写请求正文，不新增迁移、状态、timer 或生产注册。入库/上架的独立合同不在本轮替换范围。

本轮聚焦验证：插件/SDK FAST **308 passed**，插件报告、关闭及正常装配 PostgreSQL **103 passed、0 skipped**（普通/ROTATE/REPLACE、1000字符原文、不同执行同 barcode、并发/崩溃/重放及历史清理恢复）；完整 QUALITY 通过，核心 FAST **4323 passed、5 skipped**，拓扑/所有权14通过。独立评审与 selector 所选19文件核心 HEAVY 的最终结果、快照和环境统一记录在 `reports/barcode-verification.json`；不把测试本机 Mock HTTP 或早期环境/fixture诊断结果当作真实 WMS/ECS 联调通过。状态止于离线实施与验证，未提交、未部署、未启用生产装配或真实派发。

### 机械臂参数映射与正常链装配（基线 7bf382b8，本轮未提交）

按硬件方回复的说明书实施已明确的 BIN→SCAN_PLATFORM→FIVE_LAYER_RACK 参数，统一位置/尺寸字符串、单元格1–7、层1–5、毫米/寸和必填字段；十进制总厚度按输入位数计算，避免二进制尾差，不新增物理枚举、取整或单位换算。`PickingArmCommandMapper` 读取显式 `PickingArmBinding`，不增加持久配置或生产默认值。未知箱型、面向或位置只停止依赖它的命令，原授权保留；PUT 使用本盘原来源 Command 的冻结尺寸，不误用已推进的 Work 授权，扫码平台改变或本盘身份错误时拒绝新 PUT。已创建 Command 继续由原身份恢复，不重映射其正文。

`build_picking_plugin` 用同一个 `arm_commands` 接回来源和 PUT，普通/ROTATE/REPLACE 复用既有目标、报告、关闭和 Work 事实交接。来源/目标业务恢复仍归 Work/source 与 target/batch，原 Command/Transport、FactProcessor、Confirmation 的技术与事实所有权不变；没有 schema、状态、timer、SDK/wire/OpenAPI 或生产 deployment 变化。机械臂私有包络仍由供应商适配统一接口，WES 不增加私有 Adapter。

主要测试 owner 为 `T/test_picking_device_ports.py`（完整参数、精确十进制、未知/部分绑定、错误原 Command 和层越界）及 `T/integration/test_picking_device_ports_postgresql.py`（真实读口/映射的有无后继正常闭环、并发唯一命令、可靠取出门禁、报告关闭与原身份恢复）；`test_normal_picking_postgresql.py` 承接装配签名及普通/ROTATE/REPLACE 直接回归。复用既有6A–7C、搬运和9H证据，不复制共享可靠机制。最终插件 FAST **277 passed**，两份直接 PostgreSQL **17 passed、0 skipped**；5个Python文件 Ruff/格式、文档本地引用及 diff 检查通过，核心 HEAVY selector 为 NONE。快照与命令记录在 `reports/arm-parameters-verification.json`，独立 review 结论也写入同一记录。

真实接入仅保留 O1/O2 精确扫码关联、数字箱型→设备字符串、面向→A/B、实际绑定/时限及 WMS 尺寸单位对齐。business_key、字段字符串类型、单元格、层范围和 location_type 不再是待冻结门禁；CK04 只限制目标 ROTATE，C1/C2 只限制 drain 容量/核销。设备示例及本轮显式离线绑定不是现场确认，不启用生产装配或真实派发，不发送 WMS/ECS 请求；既有约束命名失败保留，本轮不处理完成确认或已接纳后 FAILED 重决策。


## 最新机械臂分工与直接编码接续（2026-10-22，实施基线 `5f998aa9`）

本节覆盖上文旧阶段“箱型映射表”“rack_face→A/B”和“扫码设备身份未定”的前提。正常链 SOURCE_ARM 显式绑定 ARM01：滚筒线料箱→扫码平台并触发 SCAN_COMPLETED；TARGET_ARM 显式绑定 ARM02：扫码平台→转运货架。角色仍通过现有工作线绑定读取，隔离测试明确给出设备、位置与时限，不写生产配置或默认坐标。

设备 `bin_type` 直接为 `"2"`/`"3"`/`"6"`，WMS 的 int DTO 不变；目标仅发送 `rack_face`，沿 ACCEPT 授权原样传递，不 trim、不转换角度、不兼容旧字段。删除 PickingArmBinding 的 bin_types/rack_sides，保留三个实际位置输入及局部缺失门禁。扫码读口核对事件设备与原来源 Command 一致，精确命令仍必须由显式关联提供；不解析未冻结字段，不按最新命令、条码或平台状态推断。该轮退料对象待决现已由2026-10-23单盘确认取代；实际退料设备参数和业务装配仍见下节，不猜字段、不阻塞正常链。

恢复 owner 不变：Work/source 持有来源授权与本盘取出，target/batch 负责目标准备和 PUT；DeviceCommand/Transport 持有原动作，FactProcessor 持有事实应用与发布，Confirmation 持有 decide/报告可靠义务。已创建 Command 优先按原身份和冻结正文恢复，本次不重写旧命令、不换身份补发。没有新增模型、状态、timer、迁移、共享基础代码或生产装配。

主要测试 owner 为 `T/test_picking_device_ports.py`（直接编码、原样货架面、两臂分工及扫码设备身份）和 `T/integration/test_picking_device_ports_postgresql.py`（实际装配参数、事务关联及普通/REPLACE 完整离线正常链）；`T/integration/test_normal_picking_postgresql.py` 为不改动的直接消费者回归。复用已有并发、崩溃、重放、回调早于 ACK 及历史清理矩阵，不复制共享恢复或退箱补架验收。设备说明书原文保持原样，本附录和主合同记录最新确认的替代字段。

退箱补架容量接续与回库分配核销仍待 WMS 补证，仅限制对应 drain 路径；CK04 只限制目标 ROTATE。实际位置/时限和 WMS 尺寸单位按各自输入核对，精确扫码字段只限制真实扫码汇合；退料单盘已明确，设备定位和业务装配只限制退料路径。本轮不提交、不部署、不发外部请求、不启用生产装配或真实派发，不以隔离参数宣布现场联调完成。

统一离线验证：新行为先在旧实现上9项失败，再完成实现；最终插件 FAST 285 passed（1038 项 PostgreSQL 显式排除）、实际装配及直接消费者 PostgreSQL 17 passed、0 skipped。三个变更 Python 文件 Ruff/格式与 diff 检查通过；核心 HEAVY selector 输出为空，本次插件 PostgreSQL 由上述主要 owner 显式执行。未改 schema，不重复迁移/共享 worker 矩阵；既有 target_rotation_transport_task_id 约束命名失败继续保留，本轮未触及也不宣称修复。完整 QUALITY 留给获准提交时的门禁；本轮不提交。报告与快照见 `reports/arm-normal-contract-{fast-final,pg-final}.xml` 和 `reports/arm-normal-contract-verification.json`。独立完整只读 review 已核对8文件指纹、固定 diff、直接/间接测试 owner、原命令恢复及未决项范围，无 P1/P2/P3 finding。评审后只补本文结论，可执行快照不变，无需重复运行已有效的验收。HEAD 保持 `5f998aa9`、index 为空。


## 可靠取盘与扫码归属、退料单盘接线收口（2026-10-23，基线 `cfb7bf10`）

**当前结论：**扫码必须可靠、唯一地归属于原取盘，不再强制 SCAN_COMPLETED 回显 command_code。ARM02 退料取出单盘与既有 WMS RACK_SLOT/plan_revision 成员合同一致，正常箱型2/3/6、rack_face、两臂分工不变。取盘结果携带 barcode 仅列为待设备确认方案；不能把建议、说明书任意 data 扩展或本地状态当成已批准 wire。主合同 §10.1.1 与机械臂附录 §10.2 是本轮接线依据，覆盖历史“已选回显”的前提。

**已有与实际缺口：**硬件说明书 §5.1 的原命令结果只定义 actual_qty、pick_and_put_result，未定义 barcode；§5.2 的独立扫码定义位置、barcode、事件时间，未定义取盘因果。统一白皮书的相同内容去重不能单独证明归属，current_command_code/诊断扩展也不能提升为关联证据。正常 BIN_CELL 的 PickingMaterialScanReader 及现有 Pickup/MaterialExecution 已具备精确身份、持久绑定和重启恢复，source_commands 目前仅在隔离装配显式提供；不能称为真实设备归属已接通。当前读口只处理 DEVICE_EVENT，若最终选择结果携带条码，仍需按该合同补事实读取/准入差异，而非仅向旧事件 reader 填字段。DirectPick 的当前成员 driver/扫码上下文已按下节离线交付接通；真实设备事实适配仍未冻结，不用虚构 Work 或切换 SOURCE_ARM 绕过。

**设备回复前可以继续的整体业务：**以当前有效 DirectPick 成员、精确到位及显式离线动作/归属事实为前提，一次性完成成员授权→原取盘 Command/Pickup→扫码身份→decide（含确定 WAIT）→目标准备/PUT→报告确认与单盘关闭。补齐插件当前成员与单盘的必要持久引用，复用原命令、物料和恢复机制；不建新物料状态机，不从 Bin Work 借授权或参数。现有代码的 work_id 必填、BIN_CELL-only 来源检查和 Work 授权交接须随这段业务整体收敛，不能只改请求工厂宣称接通。该离线交付不生成设备未确认的真实报文，不执行真实派发，不扩大到面级/整架完成、任务完成确认或失败重决策。

**真实接入的最小决策与验收条件：**

1. 设备先提供同一盘原取盘请求、首次结果回调及完整扫码报文（若所选方案仍发送），明确条码、原扫码时间和原取盘之间的可靠对应，说明实际字段/层级/类型和重复、迟到、乱序、重启补报、新旧轮次隔离及无法恢复归属时的处理。允许确认后的原任务结果、独立关联事实或已有稳定任务/轮次标识，不指定新字段。仅靠当前唯一等待者、事件内容去重、平台容量、HTTP 顺序、近似时间或最新命令不能直接接入。
2. 若设备选择“首次取盘结果携带条码及原扫码时间”，须确认首次结果前是否已具备完整扫码、原结果正文如何冻结，扫码未完成/失败如何处理；保留物理完成时间和真实结果，不将缺码改为物理失败。扫码晚于原结果时不得同命令改写已确认正文，须有独立可靠归属事实。该方案未确认前不新增 parser、DTO/SDK/wire 字段、默认值、别名或示例报文。
3. 设备给出 ARM02 单盘来源→扫码台完整 PICK_AND_PUT 参数及 rack_id/rack_face/slot_id 的设备定位对应，确需额外尺寸再核对权威来源，不借 Bin 参数、不预先扩展 WMS DTO。定位缺口仅限制退料设备路径；CK04 只限制目标 ROTATE，退箱补架容量接续与回库分配核销只限制对应 drain。

**唯一恢复 owner 与并发：**现有 Pickup/MaterialExecution 保存当前业务关联，原 DeviceCommand/Transport 负责技术履约；FactProcessor 负责可靠应用、延期/发布；Confirmation 负责 decide/报告交付。正常 BIN_CELL 由 Work/source、target/batch 扫描，DirectPick 由当前成员及插件 driver 承接；已保存精确归属继续沿原引用恢复，无需重新解析设备原文或依赖已闭合历史。新方案的事实读取/业务解释仍归插件，公共 HTTP 接收层不按当前物料猜归属。缺证据保留原身份延期/对账，不误绑下一盘，不增平台资源锁、通用关联模型或 timer，不把双臂改成等待整盘报告后才能下发下一来源。

**测试所有权与不重复范围：**未来设备方案确定后的差异归 `T/test_picking_device_ports.py`、`T/integration/test_picking_device_ports_postgresql.py`，包括实际归属读取、原扫码时间、证据不可改写及旧盘不得绑定新盘；若结果承载扫码，验证该载荷的准入/物理结果边界，不能复用事件 reader 绿灯宣布已支持。DirectPick 主贯通归任务 10 规划 owner，一次覆盖无 Work 的单盘接续，只补成员并发唯一消费、取消/跨版本、报告关闭和重启/历史清理差异。原并发、延期/重放、运输、共享租约及 worker/Beat 矩阵继续复用，不另铺一套。

**本轮交付边界：**仅修改5份人类文档，对齐需求、硬件证据、候选方案、唯一 owner 和剩余设备回复；不改生产代码、测试、schema、可执行合同或供应商原文，不运行业务测试，不提交、不部署、不发外部请求、不启用真实派发，不做 bin-line-common 抽取。本地链接、3个新增章节引用、设计指纹及 diff 检查通过。独立完整纯文档 review 核对5文件固定快照及供应商/白皮书原文，无 P1/P2/P3 finding；评审后仅补记本句结论，不改变合同语义。报告见 `reports/scan-attribution-docs-{manifest,verification}.json`；HEAD 不变、index 为空，未提交。


## 退料单盘完整离线闭环（2026-10-23，本轮工作树）

**交付范围：**有效 `DirectPickExecution` → 当前成员进度 → 原取盘 Pickup/Command → 精确扫码 → decide/确定 WAIT 续问 → 既有目标准备与 PUT → movement_report → RECORDED/DUPLICATE 后单盘关闭。新增 `AutomaticPickingDirectPick` 只保存不可变成员引用、工作线/任务归属和当前完成 Evidence；没有新增物料状态、通用授权或 timer。Pickup 以约束保证 Work 与直接成员归属二选一；每个直接成员只消费一次。decide、续问、报告始终保留原 RACK_SLOT 与成员 revision，不从当前任务 revision 或 Bin Work 取值；DirectPick ACCEPT 不交接 Work 授权。

**恢复与原子性：**新成员在已获准任务入口发现，当前进度由同一 batch 持久扫描承接，不要求存在 EXECUTING。成员/原取盘 Command/Pickup 同事务创建；精确扫码绑定、decide 与原目标动作继续使用既有 FactProcessor、Confirmation、Command/Transport owner。目标准备和 PUT 查询同时包含 Work 与直接成员，不另建搬运流程；原 PUT 恢复先于新动作准入。正常报告关闭与成员完成 Evidence 同事务；清理已闭合 Pickup、Command、报告及 PUT 历史后，当前成员完成事实独立防止重复取盘。取消只禁止新取盘/续问/目标准备/PUT，不抹去已保存事实或既有报告义务。未完成直接成员纳入既有业务工作量读口，不能被当作空闲业务。

**验收归属：**`T/integration/test_direct_pick_progress_postgresql.py` 拥有无 Work 的整段贯通、原子回滚、并发唯一消费、确定 WAIT/无 EXECUTING 重建、取消/跨 revision、精确归属拒绝、报告关闭原子性、清理后防重及迁移保护；typed revision 传播归 `T/test_material_decisions.py`。普通/ROTATE/REPLACE 共用路径及其原动作矩阵复用 `T/integration/test_normal_picking_postgresql.py` 等已有 owner，只回归受影响消费者，不另铺共享机制或 worker/Beat 矩阵。

**实际设备边界：**`build_picking_plugin` 仅在调用方显式给定 `direct_pick_requests` 与来源事实/扫码/目标/PUT 读口时关联退料入口；测试明确提供精确成员、位置与完整动作输入。现有真实来源参数映射仍属于 BIN_CELL；ARM02 退料定位及其必需尺寸来源尚未冻结，不能借用料箱参数。可靠扫码归属字段/时序保证仍待设备确认；取盘结果携带 barcode 只是候选方案，未新增 wire 字段或解析器。迁移模型发现不等于启用生产装配。CK04 仅限制目标 ROTATE，退箱补架容量接续与回库核销待补证仅限制 drain；本轮不接面级/整架或任务完成确认、异常结果重决策，不提交、不部署、不发外部请求、不启用真实派发。


本轮验证记录：新增退料 PostgreSQL 差异验收8项通过；受影响领域回归82项通过（其中包含上述退料6项，勿重复计数），插件 FAST 287项通过；测试拓扑14项通过，完整 QUALITY 通过。独立完整 Review 对固定25文件快照无阻塞 finding。HEAD/index 未变，改动保留在本工作分支未提交。核心 HEAVY 历史 schema 检查曾因普通 PostgreSQL 17.11 与固定平台不匹配失败，结构诊断为零差异；该项在本地 PostgreSQL 17.10 / TimescaleDB 缓存镜像单独补验通过，其余所选核心持久化检查17项通过；不修改历史 golden fixture。


### 2026-10-23 退料架级收口与正常任务完成（当前工作区，未提交）

承接已提交的退料单盘完整闭环，统一实现退料架权威到位 → arrival_report → 当前面范围完成 → CTU02 换面/新到位报告 → departure 决定/固定 WAIT 续问 → F01 离场，以及 task-owned completion_confirm。使用 `AutomaticPickingReturnRack` 保存当前轮次/动作引用，`AutomaticPickingTaskCompletion` 保存当前确认引用；仅增加业务必需的持久进度，不新增业务状态、授权模型、timer 或 bin-line-common。迁移 `6781e26d6e19` 从 `81daa3e0047d` 前进，空表可回退，有当前义务时拒绝丢失性回退。

已完成单盘由当前成员完成 Evidence 直接结清，不等待重复面级或整架通知。范围完成阻止尚未创建的新取盘，不关闭已有物料或替换原 Command。全部本地成员和必报事实结清后可以请求任务完成；NO_WORK 后已无逐盘义务的 Work 不等待 SCAN3，退箱、drain、换面和离场不构成任务完成门禁。正常完成只核对当前计划；首批期限未确认的 revision 0、取消完成确认及异常终态重决策继续保留，不能视为本轮已实现。

恢复 owner：任务确认和到位报告沿既有 task-owned Confirmation/typed reader；插件 completion driver 由宿主扫描恢复当前请求及确定重问。退料架由既有 batch 前缀在无 EXECUTING 时继续恢复，范围和运动结果应用归 FactProcessor，原窗口接纳交接及动作履约仍归现有 Transport owner。原离场建立后退出对闭合原进场历史的依赖。宿主只新增按精确 Confirmation id 的 typed 读取及可选的中立计划候选过滤调用；已完成成员的业务过滤由自动 batch 拥有，原进场历史清理后不重建已完成成员进场，不承载业务规则或另建派发机制；deployment 继续使用原搬运装配。

主要测试 owner 为 `T/integration/test_return_rack_completion_postgresql.py`，复用原退料单盘/正常链证据，只验收范围、架级接续和任务完成。覆盖未报告/未关闭不完成、完成不等清场、整架截止版本延期与历史清理、原在途单盘保留、固定 WAIT/STALE/current 原子并发及原离场恢复；宿主合同测试只负责精确读取身份，schema/HEAVY 映射沿原所有权。真实扫码归属/稳定性保证及 ARM02 退料定位输入继续待设备确认，CK04 只限制 ROTATE；退箱补架容量接续与回库核销补证仅限制对应 drain 实际容量路径，不扩大正常链阻塞。未提交、未部署、未发外部请求、未启用生产装配或真实派发。


本轮整段 Review 的三项反馈已按原合同收敛：departure current 创建即冻结，未来同面/异面 revision 等下一实际到位；原 OUT 未终态但新轮次已到位可独立继续。直接回架依赖只取原 root binding.created_at 之后的本架分配，不按最新 CTU02 重置。RETURN_RACK_OUT 的独立结果发布重新精确验证原已发布 READY 及冻结 F01 目标，错 operation、未发布或目标漂移均拒绝发布。当前仍支撑在位架清退的计划成员不是可清理历史，不以查不到成员推定完成。对应回归归同一插件 PostgreSQL owner，不复制通用派发/租约矩阵。


本轮最终验收：插件 PostgreSQL 整段及受影响正常链 **27 passed**（新架级/完成 owner 13项，原退料/正常链14项，勿与前轮重复计数）；插件 FAST **287 passed**；完整 QUALITY **4324 passed / 5 skipped**。核心精确 reader/selector 聚焦 **256 passed**、宿主候选过滤及原宿主合同 **18 passed**、人工 completion 直接消费者 **16 passed**；所选5文件 HEAVY **33 passed**，含隔离 PostgreSQL/真实 worker 的已有可靠机制回归。迁移空表往返和有当前义务禁止降级随本轮PG owner通过。同一 Reviewer 完成三项修复闭环及 fresh full review，无新增明确问题；只补本条人类验收记录，不改变已评审可执行树。全部为本地隔离证据，不等于设备/WMS实际联调；未提交、未部署、未启用生产装配或真实派发。


### 最终转运架正常离场：本轮集中实施（2026-10-23，未提交）

- 范围：任务正常完成后首次 departure、持久 current、确定 WAIT 到期续问、READY 到唯一 F01 及结果关闭，一次接通；REPLACE 新架接续时持久记录真实任务与实际目标架，不再从已关闭 Pickup 或原计划旧架追溯。
- owner：既有 batch completed 前缀发现和恢复当前实际目标/请求/动作，无 EXECUTING 也推进；Confirmation 交付原请求，Transport 及原窗口处理 ACCEPTED 交接，FactProcessor 应用并发布原 F01 结果。任务完成驱动继续独立，完成不等待清场。
- 验收所有权：`T/integration/test_final_target_departure_postgresql.py` 拥有最终转运架业务接续，复用正常/REPLACE/退料架既有证据，不复制共享派发与单盘矩阵；核心精确 task-owned departure reader 只验证原请求/响应身份及摘要。迁移 `f5c962df755c` 新建插件当前表，持有现有义务时禁止降级。
- 正常出口：WAIT 固定到期、未知保留原身份、READY 原子创建关联 F01、接纳释放原窗口但不等于成功，已发布匹配成功后关闭。任务完成和可清理单盘/完成确认历史不作为新轮次围栏，原 F01 恢复不依赖已闭合进场根。
- 剩余条件：扫码可靠归属字段/稳定性保证、ARM02 退料单盘定位及首批期限仍待确认；CK04 仅限制目标 ROTATE，退箱补架容量接续与核销补证仅限制对应 drain 实际容量路径。本轮不猜参数、不接异常终态重新决定、不抽取 bin-line-common；不提交、不部署、不发外部请求、不启用生产装配或真实派发。
- 最终验证：新增最终目标架 PostgreSQL owner 11 项，加原正常链/REPLACE 进场/退料架相关 37 项共 48 项闭合。统一执行先为 47 passed、1 项旧迁移 fixture 被新表保护提前拦截；仅隔离该 fixture 的上层门禁后原项复验 1 passed，未重跑已绿色业务矩阵，也未改变生产逻辑或旧断言。插件 FAST 287 passed；完整 QUALITY 4330 passed / 5 skipped；所选 23 文件 HEAVY 122 passed / 0 skipped。新表空库迁移往返及持有义务禁止降级已验证。核心 reader/selector/架构聚焦 282 项属于上述 QUALITY 覆盖，不另累计。
- 闭环 review：固定 21 文件首次完整独立评审无 actionable finding；旧迁移 fixture 的第 22 文件调整随后定向复核，确认测试所有权、旧断言及生产可观察语义不变，无未关闭问题。相关生产代码未在完整评审后变化；最后仅补本文档验收记录。离线证据见 `reports/final-target-{review-manifest,verification}.json` 及对应 PG/QUALITY/HEAVY 日志；不代表现场设备/WMS 联调或部署。


### 完整正常拣料装配：真实 worker＋Beat 离线贯通（2026-10-23）

- 本轮只补测试装配和贯通验收，不修改生产代码、schema、合同或生产插件安装。`T/integration/normal_picking_worker_runtime.py` 在测试子进程中装配现有完整 `build_picking_plugin`、真实 FactProcessor、Transport outcome publisher 和宿主计划扫描；复用既有 broker/Beat 生命周期、日志和通知丢弃装配。料箱 Work READY、原来源架及转运架已权威到位、来源服务已结束是明确起点，不重复进场/投料验收。
- 唯一新增业务验收 owner 为 `T/integration/test_normal_picking_worker_recovery.py`：同一盘取盘 → 原始扫码归属 → 首次 decide/ACCEPT → 可靠取出门禁 → PUT → movement_report/RECORDED → 单盘关闭 → completion_confirm/COMPLETED → 当前实际转运架 departure/READY → 原 F01 成功后关闭。精确扫码对应、位置、动作参数和外部结果均为显式离线输入；不把候选设备字段或本地状态当作已冻结设备合同，不复制普通/ROTATE/REPLACE、WAIT、并发、租约或历史清理矩阵。
- 运行 `device-command`、`wms-fulfillment`、`celery` 三个独立真实 prefork worker 和真实 Beat。沿用现有五个扫描任务及原队列路由，测试进程仅将扫描周期缩短为1秒，不修改生产周期或业务到期计算；不投递 Command、Transport、Confirmation 派发任务。丢弃加速通知后仍能推进，所有任务发布仅来自 Beat。
- 恢复 owner 不变：Work/source 拥有来源授权与原取盘，设备 Evidence 处理器可靠应用外部事实，FactProcessor 负责 typed 业务应用和发布，Confirmation 保存 decide/报告/完成/离场原义务，batch/completion 持久扫描推进业务和终态收口，Transport 保存原 F01 及原结果。任务完成时箱仍为 EXIT_PENDING、最终架尚未离场；重启三个 worker/Beat 后先验证没有 EXECUTING、没有成功事实时保留原义务，再接收精确原 F01 成功，经真实消费链收口，Command/Confirmation/Transport 身份集合不变。
- 本轮边界：可靠扫码归属字段与稳定性保证、ARM02 退料定位、首批期限仍待确认；目标 ROTATE 的 CK04 只限制该分支，退箱补架容量接续与回库核销补证只限制对应 drain。本轮不接异常重决策，不抽取 bin-line-common，不发外部请求、不启用生产装配或真实派发，不提交、不部署。
- 最终验证：完整正常链真实 worker＋Beat PostgreSQL 用例 **1 passed / 0 skipped**（36.36秒）；测试拓扑与核心/插件所有权 **14 passed**；两份测试资产 Ruff、格式与 tracked/untracked 空白检查通过，核心 HEAVY selector 为 NONE。独立完整只读 review 核对固定3文件快照和直接/间接 owner，**无 actionable finding**。本轮未发现生产接线缺口，全部改动限于测试装配及本文；复用未变化生产树的既有业务证据，不重复 QUALITY、共享机制或业务分支矩阵。报告见 `reports/normal-worker-{review-manifest,verification}.json`、`reports/normal-worker.xml` 与进程事件记录；仅为隔离离线验收，未提交、未部署、未启用生产装配或真实派发。


### 生产接入准备：配置读取与统一装配（2026-10-23，基线 `d3f15fae`）

- 复用现有 `config.position_bindings`、工作位校验和 START 冻结机制；自动插件增加可选 `WORK_BIN`、`SCAN_PLATFORM` 两个 STATION 槽，分别表示机械臂料箱作业位和扫码平台，目标继续使用 `TRANSFER_RACK`。实际编码不设默认。`PickingArmBindingReader` 从锁定并刷新的当前工作线快照读取，核对 line/plugin/version/active，位置类型或编码错误、三者重叠拒绝准入；缺位置保持 None，由原来源/目标 owner 等待其所需输入。
- Web、worker 共用现有部署组合根；新增显式内部关联依赖 `automatic_picking_source_commands`，只有注入该端口且安装自动插件时才构造完整正常链。它不是 wire 字段、环境开关或设备归属实现。未注入的所有现有生产调用保持原搬运装配；没有修改 `src/register.py`、worker 初始化、Beat、队列或派发逻辑。关联端口仍待设备合同冻结后实现，不提供最新命令、条码或时间匹配 fallback。
- 只有一个完整插件实例：原 transport plugin 延伸后，其 material/scan/source/target、完成驱动、退料架/最终架驱动共同进入原 FactProcessor、持久扫描及 outcome publisher。未装配 ROTATE reader 时仅限制新旋转动作，原 flow 继续恢复已创建动作、消费结果并读取目标就绪；普通目标/REPLACE 沿原路径，CK04 不扩散。退料单盘业务与报告关闭已有证据继续有效，本轮不接未冻结的 ARM02 定位参数，也不实现首批期限分支。
- 恢复 owner 不变：未消费授权与当前取出事实归 Work/source；已创建 Command 先沿原身份恢复，不因当前配置缺失重新准入；扫码归属不足由原 Evidence/FactProcessor 延期，绑定后沿原 Pickup/MaterialExecution；decide/报告/完成/离场沿原 Confirmation；后续推进与终态收口仍归插件 batch/completion 和原 Transport。没有 schema、状态、timer、通用授权或 bin-line-common。
- 差异验收唯一归插件 `T/test_definition.py`、`T/test_picking_device_ports.py`、`T/test_plugin_composition.py`、`T/integration/test_picking_device_ports_postgresql.py` 的配置接入用例及 `T/integration/test_target_rotation_postgresql.py` 的缺参数恢复用例；验证默认未启用、显式依赖准入、同实例交接、缺项不消费授权、完整位置后唯一创建及重建保留原命令，以及未装配新旋转读口时保留原动作和目标就绪恢复。既有 `test_normal_picking_worker_recovery.py`、正常/ROTATE/REPLACE 和共享可靠机制矩阵不复制；装配受影响的核心消费者按既有测试/HEAVY mapping 验证，不由核心承接插件业务。
- 最终验证：插件 FAST **308 passed**；核心直接消费者 **109 passed**；新增 PostgreSQL 差异用例 **2 passed / 0 skipped**。共享 QUALITY **4330 passed / 5 skipped**（FAST 既有 skip，未计作集成证据）；组合根现有 mapping 选择的11份 HEAVY **75 passed / 0 skipped**，含真实 worker，164.67秒。首次 HEAVY 因临时库名不符合测试保护规则在 setup 被拒绝，改用独占 `test_` 逻辑库后完整通过，未放宽保护或修改测试。独立完整只读 review 对固定12文件快照 **无 actionable finding**；Ruff、格式、新增文档链接与 diff 检查通过。证据见 `reports/picking-preparation-{review-manifest,verification}.json`、FAST/core/PG/HEAVY JUnit 和 QUALITY 日志；仅收口人类文档不使执行证据失效。本轮未提交、未部署、未发外部请求，未启用生产装配或真实派发。


### 已知合同继续实施：首批等待与完成确认接续（2026-10-23，基线 `04fa7010`）

- 正常/退料取盘、扫码、decide、目标、PUT、报告关闭沿既有离线闭环，不再重写。两臂分工、箱型2/3/6、rack_face、barcode原文及毫米/寸已明确；实际位置、设备时限复用既有配置和 START 冻结，未知扫码字段和退料定位不填默认、不伪造设备适配。联调时校准真实输入；目标旋转指令只限制 ROTATE，退箱补架容量与核销补证仍只限制对应排空路径。
- 首批期限只有显式正整数毫秒输入才建立等待。新增基础读口 `PickingTaskPrepareResultReader` 仅核对原准备请求/响应的 typed 身份、owner、摘要和可靠接收时间，不解释工作线业务。插件在原 `AutomaticPickingTaskCompletion` current 冻结 `first_plan_due_at`；Confirmation 在到期后才创建，current 与新义务原子关联。无新授权模型、业务状态或通用 timer。迁移 `3a17b9c40d62` 允许到期等待暂未创建 Confirmation，同时用约束保证 current 至少有到期点或原 Confirmation；降级拒绝丢弃首批等待语义。
- 原宿主计划扫描仍锁工作线，插件 batch 在无 EXECUTING 的 PREPARING 任务上调用同一 completion owner。确定 BUSINESS_IN_PROGRESS 仍按原结果接收时间＋retry_after_ms 固定续问；未知交付保留原请求。首批计划到达后的版本竞争沿当前任务锁与版本校验处理，旧版本0结果不关闭新版本。任务完成不等清场。冻结后 completion 不重新读取准备结果或重新计时；原 prepare 在首批计划准入或任务尚未闭合前仍是当前业务依据，必须保留，不能按技术 COMPLETED 当成可清理历史。
- 新差异主要 owner 为 `T/integration/test_return_rack_completion_postgresql.py` 的首批期限用例；复用当前正常完成、最终架离场及已提交真实 worker＋Beat 证据。宿主 `tests/contracts/wms_adapter/outbound_picking/test_prepare_result_reader.py` 只证明新结果读口的身份/可靠事实；插件 FAST 装配验证显式参数传给同一恢复 owner。只回归受影响 owner，不再复制运输、扫码和共享租约矩阵。
- 仍未启用生产调用、真实扫码关联、退料定位或真实派发；实际首批时长未配置，不发外部请求、不提交、不部署、不抽取 bin-line-common。
- 最终证据：插件 FAST **314 passed**；宿主读口/直接消费者 **56 passed**；受影响 completion/final-target PostgreSQL **29 passed**，其中评审修订的两项另行 **2 passed**，迁移最终降级保护复验 **1 passed**，均无 skip。QUALITY **4338 passed / 5 skipped**（既有 FAST skip，不算集成通过）；最终 selector 的26份既有 HEAVY **139 passed / 0 skipped**，420.09秒，未新增真实 worker/运输业务矩阵。首轮门禁发现新增迁移漏同步既有 selector 精确列表，仅补路径后完整门禁通过。
- 独立 review 的唯一 P2 已闭环：撤销“等待首批期间可删原 prepare”的错误验收前提，保留当前准入依据，用失败读口证明 completion 不重读已冻结计时，再经真实 plan_delta Service 验证首批可接入、旧0不关闭新1。原 reviewer 合并完成闭环与当前18文件 fresh full review，无新增 actionable finding；未扩大生产范围。Ruff/格式/新增链接与 diff 检查通过。证据见 `reports/first-plan-{review-manifest,verification}.json` 及对应 JUnit；实施结束仅追加本文证据，不改变可执行快照。未提交、未部署、未发外部请求、未启用生产装配或真实派发。


## 2026-10-23 现场接入准备：统一正常链与退料来源读取

本段承接首批持久等待，取代此前“Web/worker 仅搬运、必须提供扫码关联端口才能装配正常链”的当前接线说明；历史验证记录仍保留。已确认箱型、rack_face、两臂分工和尺寸映射不重做。

- 统一组合根沿现有插件清单显式安装完整正常链，Web/worker 原调用点保持一致，不新增环境模式开关，不修改现场启用列表、绑定或派发配置。首批时长仍只接受显式值，没有默认期限。
- 扫码读口可独立恢复 Evidence 已保存的精确原 Command 引用；未绑定时只有明确提供的内部关联端口可返回身份，无依据沿原 Evidence/FactProcessor 延期。未增加或猜测设备字段，不宣称真实扫码归属已打通。
- 退料来源 reader 按有效成员、当前架级到位轮次、当前面、工作位和运动边界读取准入；原 Pickup 的可靠取出沿匹配已发布原 Command 结果读取。复用 ReturnRackRepository，不伪造 Work、不消费料箱 SCAN2、不增加模型、状态或 timer。
- 组合根提供显式退料定位请求注入，缺工厂则不安装新退料取盘入口；已创建单盘仍由共有 Material/target/Command owner 恢复。定位参数未确认只限制退料新动作，不撤掉正常出库链。
- 装配差异测试归插件原 `test_plugin_composition.py`、`test_picking_device_ports.py` 及 PostgreSQL owner；既有完整正常 worker＋Beat 验收改用真实部署组合根，只注入离线归属并隔离外发，不再手工复制生产装配。不新增业务矩阵或共享可靠机制验收。

验证与闭环 review：插件 FAST 324 passed；相关 PostgreSQL 四文件 37 passed；核心部署 FAST 19 passed；既有完整正常 worker＋Beat 经真实组合根 1 passed。独立完整只读 review 未发现 P0/P1/P2；附录旧阶段装配措辞已定向收敛。共享组合根 selector 的 11 文件门禁 75 passed、0 skipped；首次环境误用 admin 库被安全校验拒绝，改为本次隔离实例的新建已迁移测试库后完整通过，未放宽门禁或修改业务。当前可执行快照及报告记录于 `reports/picking-site-verification.json`，完整 QUALITY 留待授权提交边界。本轮不提交、不部署、不发外部请求、不启用真实派发；可靠归属的设备字段/稳定性、ARM02 定位及实际首批时长仍待联调。目标旋转只限制 ROTATE，退箱补架容量接续与核销补证只限制对应排空路径。
