# ADR 0004: plane 接口 RBAC + 容量上限 + 极态覆盖

**状态**: Accepted
**日期**: 2026-06-23
**适用范围**: `GET /worklines/{id}/plane/scene` + `GET /worklines/{id}/plane/snapshot`

## 背景

`GET /worklines/{id}/plane` 现状（autoplan 验证）暴露 `pkg_code / bin_code / dispatch_request_id / source / target / evidence` 等运营敏感数据，但 `src/app/workline/v1/*.py` 8 个 router 文件**无 RBAC 装饰**——任何能访问该接口的 token 都能拿全量运营数据。`PlaneSnapshot.conflicts[]` 和 `in_transfer[]` 当前无大小上限，10x load 下会拖垮查询面。

## 决策

1. **首版就拆两个独立接口**：禁止提供 `GET /worklines/{id}/plane` 聚合接口；拆 `biz:workline:view-plane-scene` / `biz:workline:view-plane-snapshot` 两套权限。
2. **行级 WorkLine 域过滤**：默认用户只能读自己 WorkLine 域内的 WorkLine；跨域读需 `wes.observer` 角色。
3. **evidence_json 默认脱敏**：`pkg_code` 后 4 位掩码、`bin_code` 前缀掩码；仅 `wes.engineer` 角色可见全量。
4. **审计日志**：每次 plane 读取写 `audit_logs`：`viewer_user_id, viewer_ip, snapshot_version, snapshot_status, result_size, read_at`。
5. **容量上限 + truncated 标记**：`conflicts[]` ≤ 50；`in_transfer[]` ≤ 100；`active_material_units[]` ≤ 200；`devices[]` ≤ 50；`queue_memberships[]` ≤ 200；超限打 `truncated=true` + `total_counts` 字段。
6. **Stale 检测**：`now - generated_at > stale_threshold_seconds` → `snapshot.stale=true` + `snapshot_status=STALE`。
7. **极态覆盖**：`snapshot_status` 显式枚举 `OK / EMPTY / CONFLICTS_ONLY / STALE / RECONCILING`；前端 dashboard 按 status 区分告警。
8. **性能门禁**：P95 < 500ms（无 10x load）；10x load P95 < 1.5s；超过触发自动降级（精简 `devices[]` 和 `in_transfer[]`）。

## 后果

- plane 接口不再全员可读全量运营数据。
- 10x load 下 plane 接口可控（容量上限 + truncated + 性能降级）。
- 异常态（empty / conflicts_only / stale / reconciling）可观测。
- 前端 dashboard 可按 snapshot_status 区分告警级别。

## 验收

- `docs/architecture/specs/workline-restructuring/50-plane-read-model.md` 发布。
- plane 接口实现 + RBAC 装饰 + audit log + 脱敏。
- 10x load 测试通过。
- 安全测试覆盖 plane RBAC 矩阵。

## 引用

- 顶层设计：[`../../workline-and-plugin-restructuring.md`](../../workline-and-plugin-restructuring.md)
- Sub-spec 50：[`../../specs/workline-restructuring/50-plane-read-model.md`](../../specs/workline-restructuring/50-plane-read-model.md)
