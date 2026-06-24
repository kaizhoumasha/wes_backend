# Decision Audit Trail — WORKLINE + PLUGIN 重构 autoplan

**日期**: 2026-06-23
**plan 文件**: `docs/architecture/workline-and-plugin-restructuring.md`
**总决策数**: 28（27 mechanical + 1 taste）
**用户挑战**: 0（双 voice 高度共识）

| # | Phase | Decision | Classification | Principle | Rejected |
|---|-------|----------|---------------|-----------|----------|
| 1 | CEO | `external/wms` 包装 760 行 `wms_integration`，不新建平行域 | Mechanical | DRY (P4) | 新建 `external/wms` 域 |
| 2 | CEO | 追加 A/B/port-only/monolith+ACL 4 方案决策表 | Mechanical | Completeness (P1) | 仅口述 B |
| 3 | CEO | 追加"能力冻结" + per-PR 兼容声明 | Mechanical | Boil lakes (P2) | 仅泛指 legacy 清理 |
| 4 | CEO | `ConveyorQueueProjectionPort` 别名层 + 保留物理表名 | **Taste** | Explicit (P5) | 直接重命名 |
| 5 | CEO | 查询响应强制带 `scope/authority/source/evidence_at` | Mechanical | Completeness (P1) | 隐式信任 |
| 6 | CEO | Authority Matrix（PLC/device vs WMS vs RCS） | Mechanical | Explicit (P5) | 所有外部事实 → WMS |
| 7 | CEO | `RuntimeIntent` 与 `RuntimeSessionAggregate` 显式拆分 | Mechanical | Explicit (P5) | 混为一谈 |
| 8 | CEO | 追加"为什么 B 而不是 thin ACL"的 go/no-go 指标 | Mechanical | Pragmatic (P3) | 直接 B 必然 |
| 9 | Design | `PlaneSceneView` 必须含 `schema_version` + `generated_at` | Mechanical | Explicit (P5) | 隐式版本 |
| 10 | Design | scene/snapshot 单独 GET；空态 200 + 空集合 | Mechanical | Completeness (P1) | 缺 snapshot 抛 500 |
| 11 | Design | 枚举值冻结 `presence_type` | Mechanical | Explicit (P5) | 自由字符串 |
| 12 | Design | label/code 分离 | Mechanical | Explicit (P5) | i18n 基线 |
| 13 | Design | 极态清单显式 | Mechanical | Completeness (P1) | 默认 happy path |
| 14 | Eng | CEO 决策 #1 措辞修订：补全 5 套缺失 port | Mechanical | DRY/Explicit | 维持原措辞 |
| 15 | Eng | `ExecutionCorrelation` correlation key | Mechanical | Explicit (P5) | 允许 session FK 跨域 |
| 16 | Eng | `RuntimeIntent` 与 `RuntimeSessionAggregate` 显式拆分 | Mechanical | Explicit (P5) | 混为一谈 |
| 17 | Eng | 统一 `ReconciliationManager` + RECONCILING 恢复路径矩阵 | Mechanical | Completeness (P1) | 仅 mention 触发 |
| 18 | Eng | WorkLine 启动 manifest validator + 未知队列拒绝 | Mechanical | Explicit (P5) | DB 唯一约束兜底 |
| 19 | Eng | 11 态机补 4 条 timeout + circuit breaker `BLOCKED_BY_CB` | Mechanical | Completeness (P1) | 原 11 态 |
| 20 | Eng | `PlaneSnapshot` 首版拆 scene/snapshot + 容量上限 | Mechanical | Completeness (P1) | 聚合后置拆分 |
| 21 | Eng | `WorklineActiveObjects` 引入 `ActiveObjectRegistry` 跨投影唯一归属 | Mechanical | Completeness (P1) | 仅 conflict evidence |
| 22 | Eng | plane 接口 RBAC + 行级 + 脱敏 + audit log | Mechanical | Explicit (P5) | `biz:workline:list` |
| 23 | Eng | WMS callback body HMAC + nonce TTL + allow-list | Mechanical | Completeness (P1) | 仅验字段不验 body |
| 24 | Eng | idempotency_key 复合主键 + request_hash + session 审计 | Mechanical | Explicit (P5) | 单一主键 |
| 25 | Eng | typed `ExternalReference` + typed evidence envelope | Mechanical | Explicit (P5) | 自由 JSON |
| 26 | Eng | 测试基线（覆盖率快照 + 100% 行覆盖 4 个 service + 13 个新 contract test） | Mechanical | Completeness (P1) | 无基线 |
| 27 | Eng | TODOS P0 "19 失败" 撤掉或更新（实测 22 passed） | Mechanical | Explicit (P5) | 维持陈旧 TODO |
| 28 | Eng | Legacy 路径校正到 `src/{workline_runtime,workline_plugins}` | Mechanical | Explicit (P5) | `src/app/workline` |

## 统计

- Auto-Decided 全部 28 项：Mechanical 27 项 + Taste 1 项（#4 queue 命名）
- User Challenges: 0（双 voice 高度共识）
- Cross-Phase Themes: 3 项独立命中
  1. 外部 ACL 应补全不重建（CEO F1 + Eng F14）
  2. RuntimeIntent/RuntimeSession 显式拆分（CEO F7 + Eng F3/F4）
  3. 过早在命名/schema 画死（CEO F4 + Eng F5/F12）
