# 数据库初始化数据文档

## 概述

`init_db_data.py` 脚本用于初始化系统基础数据,包括权限(Permissions)、角色(Roles)和用户(Users)。

## 初始数据

### 权限 (20 条)

| 权限标识 | 描述 |
|---------|------|
| `user:create` | 创建用户 |
| `user:read` | 查看用户 |
| `user:update` | 更新用户 |
| `user:delete` | 删除用户 |
| `user:export` | 导出用户数据 |
| `role:create` | 创建角色 |
| `role:read` | 查看角色 |
| `role:update` | 更新角色 |
| `role:delete` | 删除角色 |
| `role:assign` | 分配用户角色 |
| `permission:create` | 创建权限 |
| `permission:read` | 查看权限 |
| `permission:update` | 更新权限 |
| `permission:delete` | 删除权限 |
| `system:config` | 系统配置管理 |
| `system:log` | 查看系统日志 |
| `system:monitor` | 系统监控 |
| `file:upload` | 上传文件 |
| `file:download` | 下载文件 |
| `file:delete` | 删除文件 |

### 角色 (4 个)

| 角色名称 | 描述 | 权限数 |
|---------|------|--------|
| 超级管理员 | 系统最高权限管理员,拥有所有权限 | 20 |
| 管理员 | 系统管理员,拥有大部分管理权限 | 6 |
| 普通用户 | 系统普通用户,基础权限 | 2 |
| 访客 | 访客用户,只读权限 | 1 |

### 用户 (3 个)

| 用户名 | 邮箱 | 全名 | 密码 | 角色 | 超级用户 |
|--------|------|------|------|------|----------|
| `admin` | admin@example.com | 系统管理员 | `admin123456` | 超级管理员 | ✓ |
| `manager` | manager@example.com | 系统经理 | `manager123456` | 管理员 | ✗ |
| `user` | user@example.com | 测试用户 | `user123456` | 普通用户 | ✗ |

## 使用方法

### 1. 确保环境配置正确

确保 `.env` 文件中的数据库配置正确:

```bash
# 数据库连接 URL
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/dbname

# 其他配置...
```

### 2. 启动数据库

确保数据库服务正在运行:

```bash
# PostgreSQL
brew services start postgresql
# 或
systemctl start postgresql
```

### 3. 运行初始化脚本

```bash
# 在项目根目录执行
python scripts/init_db_data.py
```

### 4. 验证数据

脚本执行完成后,会输出摘要信息:

```
============================================================
数据库基础数据初始化完成!
============================================================
权限: 20 条
角色: 4 条
用户: 3 条

默认登录账号:
  - admin / admin123456
  - manager / manager123456
  - user / user123456

⚠️  生产环境请立即修改默认密码!
```

## 脚本特性

### 幂等性

脚本具有幂等性,可以安全地多次运行:

- 如果权限/角色/用户已存在,会跳过创建
- 不会重复创建数据
- 不会修改已存在的数据

### 数据关联

脚本会自动处理数据之间的关联关系:

- 角色 ↔ 权限关联 (多对多)
- 用户 ↔ 角色关联 (多对多)

### 密码安全

- 所有密码使用 Argon2 算法哈希存储
- 永远不要在生产环境使用默认密码

## 自定义初始数据

### 修改权限列表

编辑 `INITIAL_PERMISSIONS` 列表:

```python
INITIAL_PERMISSIONS = [
    {"name": "custom:permission", "description": "自定义权限"},
    # ... 添加更多权限
]
```

### 修改角色配置

编辑 `INITIAL_ROLES` 列表:

```python
INITIAL_ROLES = [
    {
        "name": "自定义角色",
        "description": "角色描述",
        "is_active": True,
        "permissions": ["user:read", "user:create"],  # 或 "*" 表示所有权限
    },
    # ... 添加更多角色
]
```

### 修改初始用户

编辑 `INITIAL_USERS` 和 `INITIAL_USERS_CREDENTIALS` 列表:

```python
INITIAL_USERS_CREDENTIALS = [
    {"username": "newuser", "password": "newpassword123"},
]

INITIAL_USERS = [
    {
        "username": "newuser",
        "email": "newuser@example.com",
        "full_name": "新用户",
        "password": "newpassword123",
        "is_active": True,
        "is_superuser": False,
        "is_multi_login": True,
        "roles": ["普通用户"],
    },
]
```

## 生产环境注意事项

### ⚠️ 安全警告

1. **修改默认密码**: 脚本执行后立即修改所有默认密码
2. **删除测试账号**: 生产环境应删除或禁用 `manager` 和 `user` 账号
3. **使用强密码**: 密码应包含大小写字母、数字和特殊字符,长度至少 12 位
4. **限制超级用户**: 超级用户账号应严格控制,仅必要时使用

### 密码修改建议

```bash
# 通过 API 修改密码
curl -X POST http://localhost:8000/api/v1/auth/change-password \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "old_password": "admin123456",
    "new_password": "Str0ng!P@ssw0rd#2024"
  }'
```

## 故障排除

### 数据库连接失败

```
ERROR: 初始化失败: could not connect to server
```

**解决方案**:
1. 检查数据库服务是否运行
2. 验证 `DATABASE_URL` 配置是否正确
3. 确认数据库用户权限

### 表不存在

```
ERROR: relation "users" does not exist
```

**解决方案**:
1. 先运行数据库迁移创建表结构
2. 或使用 `init_db.py` 创建表

### 权限不足

```
ERROR: permission denied for table users
```

**解决方案**:
1. 确认数据库用户有 INSERT 权限
2. 检查表的所有者和权限设置

## 相关文件

- `scripts/init_db.py` - 数据库表结构初始化
- `src/app/admin/models.py` - 用户/角色/权限模型定义
- `src/core/security.py` - 密码哈希工具
- `src/database/db.py` - 数据库连接配置

## 维护指南

### 添加新权限

1. 在 `INITIAL_PERMISSIONS` 中添加权限定义
2. 运行脚本: `python scripts/init_db_data.py`
3. 更新相关角色的权限配置

### 添加新角色

1. 在 `INITIAL_ROLES` 中添加角色定义
2. 配置角色的权限列表
3. 运行脚本: `python scripts/init_db_data.py`

### 更新现有角色权限

1. 修改 `INITIAL_ROLES` 中角色的 `permissions` 配置
2. 删除数据库中的角色权限关联(可选)
3. 运行脚本: `python scripts/init_db_data.py`

注意: 脚本不会修改已存在的角色权限,如需更新请手动操作或编写专门的更新脚本。

## 技术细节

### 数据库事务

脚本使用数据库事务确保数据一致性:

- 所有操作在一个事务中执行
- 失败时自动回滚
- 成功时提交所有更改

### 异步处理

脚本使用 SQLAlchemy 异步接口:

- 使用 `async_sessionmaker` 创建会话
- 使用 `await` 处理异步操作
- 高效处理大量数据

### 关系处理

多对多关系通过 SQLAlchemy 的 `relationship` 处理:

```python
role.permissions.append(permission)  # 添加权限到角色
user.roles.append(role)             # 添加角色到用户
```

## 许可证

本脚本是项目的一部分,遵循项目许可证。
