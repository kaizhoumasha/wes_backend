> **拆分说明**: 本文档从 `workline-and-plugin-restructuring.md` v7 拆分（2026-07-10）。
> 完整设计目标与 frontmatter 仍以原文件为准, 详见同目录 index。
> 章节对应: security = 原文件 §7 安全设计。

---

## 7. 安全设计

### 7.1 威胁模型

| 威胁 | 攻击向量 | 影响 | 防线 |
| --- | --- | --- | --- |
| 运营数据泄露 | plane 接口无 RBAC，全员可读 `pkg_code/bin_code/dispatch_request_id/source/target/evidence` | 物料追踪 + 仓库地理边界 + 业务拒绝原因泄露 | §5.2 plane RBAC + 行级 + 脱敏 + 审计 |
| External callback 重放 | 合法签名后重放同一 callback payload | 重复触发状态机或冲突 evidence | §5.3 HMAC body 签名 + nonce 5 分钟 TTL |
| External callback body 篡改 | 修改 payload 但保持 signature 头 | 业务逻辑走错路径 | §5.3 HMAC body hash 包含 sha256(body) |
| 跨 session 幂等键复用 | 同 `idempotency_key` 被 2 个不同 WES session submit | 业务证据混淆 + 攻击信号 | §5.4 复合主键 + `request_hash` 校验，409 + 审计 |
| 时钟偏差攻击 | 篡改 timestamp 绕过 nonce TTL | replay 攻击窗口扩大 | §5.3 时钟偏差 > 30s 拒绝 |
| 内部域直接 import WMS DTO | 跨层强耦合，WMS schema 变化穿透 | WES 主数据污染风险 | §2.5 `WMS_INTEGRATION_BOUNDARY`：WMS DTO 只能存在于 `wms_integration` |
| 影子 WMS | 本地 active projection 冒充 WMS 全局库存 | 业务决策错误 + WMS 不可信 | §3.4 Authority Matrix + §2.5 `AUTHORITY_METADATA_BOUNDARY` |
| 设备到位信号伪造 | 攻击 ECS 事件或 device callback 接口 | 现场状态误判 | §3.4 Authority Matrix：设备到位归 ECS/device，WES 只接收 |
| 设备命令越界 | WES 下发 PLC/坐标/关节/安全回路指令，绕过 ECS | 现场安全边界被软件业务层污染 | §9.6：WES 不与 PLC 通讯，只通过 ECS API 下发业务命令 |
| 事件响应体偷渡动作 | 在 Event_Push ACK 中返回下一步动作 | 指令不可追踪、无法幂等、绕过 effect ledger | §9.6：Event_Push 只 ACK，动作必须走 DeviceCommand |
| 设备事件乱序/缺失 ID | ECS 事件重放、延迟、缺 `event_id` 或 `sequence_no` | Session 被旧事件推进 | §9.6：缺 ID/乱序事件只落 evidence + diagnostic |
| 绕过 WMS 直连 RCS | WES 内部域直接调用 RCS/AGV/CTU SDK | 调度权威分裂、WMS 账务/任务状态失真 | §3.5：当前阶段 RCS 调度由 WMS 统一调度 |
| 跨域 FK 误用 | `execution_session.id` 跨域强 FK | 域耦合、未来重构困难 | §2.2 跨域 correlation key |
| Event_Push 响应体偷渡检测缺失 | callback handler 返回非 ACK body 或携带 command-like 字段 | 供应商绕过 DeviceCommand 审计链 | callback 响应 schema 固定为 ACK；响应拦截器检测非 ACK 字段并告警 |

### 7.2 plane 接口 RBAC

详见 §5.2。

### 7.3 External callback HMAC

详见 §5.3。

### 7.4 idempotency 跨域审计

详见 §5.4。

### 7.5 北向运维入口与凭据观测

- 只读入口固定为 `GET /api/v1/workline/runtime-operations/northbound`，必须具备专用权限
  `sys:runtime-operations:view`；API 只调用 Query Service，Service 只调用 Repository。
- 普通用户的 tenant scope 以认证用户 ID 对齐 `WorkLine.created_by`。指定 `workline_id` 时必须先校验 owner；
  非 owner 返回拒绝，不执行聚合查询。超级管理员使用显式 `PLATFORM` scope。
- Repository 只聚合 typed columns 中的 operation/profile、状态、时间和 lease 字段；权限判断不得读取或解析
  payload、header、trace、credential reference、callback body 或业务键。
- 允许和拒绝的读取都写审计，审计参数仅包含 decision、scope、tenant/viewer/workline 及固定 API path；
  不得记录 payload、secret、header、凭据引用或行级 evidence。
- 凭据解析由 `AuditedVersionedCredentialProvider` 统一包装；日志/指标只允许
  `provider_kind ∈ {environment, custom}` 与
  `outcome ∈ {RESOLVED, REVOKED, RESOLUTION_FAILED, PROVIDER_ERROR}`。原始异常信息、secret ref 和 secret material
  一律不进入观测面。

### 7.6 关键不变量（17 条 / 三级分类）

按强制等级分三档：**核心 5 条**（CI/pre-commit 必须自动检查，违反立即阻塞 PR）、**重要 8 条**（强制门禁检查，违反阻塞 Phase 完成）、**设计 4 条**（评审检查，违反需评审决议）。

**核心 5 条（强制自动检查）**：

| # | 不变量 | 出处 | 检查手段 |
| --- | --- | --- | --- |
| WMS_INTEGRATION_BOUNDARY | WMS DTO / client / 状态码 / 供应商字段只能存在于 `wms_integration` adapter/ACL 层——内部域直接 import WMS 类型立即拒绝 | §2.5 | import 静态检查 |
| EXECUTION_CORRELATION_BOUNDARY | 跨域 session FK 收敛为 `ExecutionCorrelation` correlation key | §3.3 | FK 引用扫描 + schema lint |
| AUTHORITY_METADATA_BOUNDARY | 查询响应强制带 `scope/authority/source/evidence_at`——不允许本地 active projection 冒充 WMS 全局库存 | §3.4 | schema 校验测试 |
| DEVICE_COMMAND_BOUNDARY | WES 不与 PLC 通讯，不下发坐标/关节/安全回路指令；设备控制只能经 ECS/设备上位机标准 API | §9.6 | DeviceCommand 字段白名单 |
| RUNTIME_INBOX_STATE_MACHINE | RuntimeInbox 必须支持 `RECEIVED -> PROCESSING -> PROCESSED/FAILED/DEAD_LETTER`，callback ACK 后处理失败必须可重试、死信和人工重放 | §9.2 | 状态机契约测试 |

**重要 8 条（强制门禁检查）**：

| # | 不变量 | 出处 |
| --- | --- | --- |
| I1 | WMS/RCS/ECS/device External callback 必须 body HMAC + nonce TTL + path canonical | §5.3 |
| I2 | idempotency_key 复合主键 `(provider_code, operation_kind, idempotency_key)`——跨 session 同 key 不同 hash 返回 409 + 安全审计 | §5.4 |
| I3 | Runtime capability 只能通过注入的 port contract 使用 WMS/设备出站能力；不能注入 `wms_integration` / `device` 实现对象、HTTP client、DTO、provider exception、service locator、`WmsEventPort`、`DeviceEventPort` 或 `RuntimeInbox` consumer | §3.5 |
| I4 | 设备 Event_Push 只能 ACK；任何后续动作必须经 RuntimeIntentLog + DeviceCommand 下发 | §9.6 |
| I5 | WorkLine manifest 在 ExecutionSession 创建时 pin 版本；运行中 session 不热切 manifest | §9.1 |
| I6 | DeviceCommand dispatch 前必须确认 ECS 设备状态为 IDLE；RUNNING 有界等待，ERROR/OFFLINE/查询超时短退避后 RuntimeHold | §9.6 |
| I7 | 作业期位置只能通过 evidence/RuntimeLocationEvent 投影，不允许裸写 location summary 覆盖冲突事实 | §9.4 |
| I8 | Event_Push HTTP 响应 schema 固定为 ACK；任何 command-like 字段都必须由响应拦截器拒绝并告警 | §7.1 / §9.6 |

**设计 4 条（评审决议级）**：

| # | 不变量 | 出处 |
| --- | --- | --- |
| D1 | 目标态契约优先：旧 API / 旧表 / 旧插件形态不得反向约束新架构 | §3.7 |
| D2 | B 方案以目标态边界 + 行为契约测试 + 破坏性清理清单为前置 | §3.8 |
| D3 | plane 接口不允许全员可读全量运营数据 | §5.2 |
| D4 | 当前阶段 RCS/AGV/CTU 调度只能经 WMS 履约 port；直连能力仅作条件触发扩展，必须通过 provider adapter 替换，不允许内部域直连 SDK | §3.5 / §10.5 |

**分级使用约定**：

- 核心 5 条由 CI / pre-commit hook 自动校验，违反立即阻塞 PR；不允许任何 review override。
- 重要 8 条由 Phase 完成门禁强制检查，违反阻塞对应 Phase 验收；仅 architecture lead 可批准临时豁免并必须同步开 follow-up issue。
- 设计 4 条由 PR review + ADR 决议管理，违反需重新评审并更新 ADR。

---
