# WES Backend 代码库评审报告（2026-03-16 更新）

## 评审结果总结

### ✅ 已修复问题验证
1. **app_service.py (P0)** - 完全修复
   - `_query_by_app_id` 已委托给 `APIAppRepository.get_by_app_id()`
   - `assign_permissions` 已委托给 `APIAppRepository.assign_permissions()`

2. **role_service.py 和 perm_service.py (P1)** - 已修复
   - `_query_user_ids_by_role_id` 已委托给 `RoleRepository`
   - `_query_user_ids_by_permission_id` 已委托给 `PermRepository`

### 🆕 新发现的架构违规（3个）

#### 🔴 P1: permission_service.py
**文件**: `src/app/api_auth/services/permission_service.py:26`
**问题**: `get_permissions_by_app_id` 直接使用 `select()` 和 `db.execute()`
**影响**: API 认证核心逻辑，影响权限验证
**重构**: 委托给 `PermissionRepository.get_permission_ids_by_app_id()`

#### 🟡 P2: perm_service.py
**文件**: `src/app/admin/services/perm_service.py:191`
**问题**: `_query_app_ids_by_permission_id` 直接查询关联表
**影响**: 权限变更时的应用缓存失效
**重构**: 委托给 `PermRepository.get_app_ids_by_permission_id()`

#### 🟢 P2: device_command_service.py（遗留）
**文件**: `src/app/device/services/device_command_service.py:274`
**问题**: `update_event_log` 直接使用 `update()` 和 `db.execute()`
**影响**: 设备事件日志更新
**重构**: 委托给 `DeviceEventLogRepository.update_event_log()`

### ✅ 架构健康度良好
1. **API 层完全合规** - 所有 v1/*.py 无直接数据库访问
2. **Mixin 继承规范正确** - 无重复继承 AuditMixin
3. **Repository 层正确使用 SQLAlchemy**

### 📊 进度对比
| 指标 | 之前 | 当前 | 变化 |
|------|------|------|------|
| SRP 违规 | 6个 | 3个 | ➜ 50%减少 |
| P0 问题 | 2个 | 0个 | ✅ 全部修复 |
| P1 问题 | 2个 | 1个 | ➜ 50%减少 |

### 🎯 重构优先级
1. **立即**: permission_service.py (P1)
2. **本周**: perm_service.py (P2)
3. **下周**: device_command_service.py (P2)

### 设计原则遵循
- ✅ **DRY**: BaseRepository/BaseService 避免重复
- ✅ **KISS**: 优先框架默认能力
- ✅ **SOLID**: 大部分模块符合 SRP
- ✅ **YAGNI**: 只实现当前需求

---
**评审日期**: 2026-03-16
**下次评审**: 修复剩余3个问题后