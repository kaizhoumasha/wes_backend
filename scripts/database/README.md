# 数据库初始化脚本

本目录包含 WES Backend 系统的数据库初始化脚本。

## 文件说明

### init_db.sql
PostgreSQL 数据库初始化 SQL 脚本，包含：
- 创建基础权限（用户、角色、权限管理）
- 创建系统角色（超级管理员、管理员）
- 创建超级管理员账户
- 建立角色与权限的关联
- 建立用户与角色的关联
- 创建操作审计日志表

### run_init_db.sh
自动化初始化脚本，功能：
- 自动读取 `.env` 配置文件
- 检查数据库连接状态
- 生成 **Argon2** 密码哈希 (pwdlib/passlib)
- 执行 SQL 初始化脚本
- 验证初始化结果

## 使用方法

### 1. 使用自动化脚本（推荐）

```bash
# 使用默认配置初始化
./scripts/database/run_init_db.sh

# 指定管理员密码
./scripts/database/run_init_db.sh -p mypassword123

# 强制重新初始化（会清空现有数据）
./scripts/database/run_init_db.sh --force

# 查看帮助
./scripts/database/run_init_db.sh --help
```

### 2. 手动执行 SQL

```bash
# 通过 Docker 容器执行
docker exec -i wes_postgres psql -U postgres -d wes_db < scripts/database/init_db.sql

# 或使用本地 psql
psql -h localhost -U postgres -d wes_db -f scripts/database/init_db.sql
```

## 环境配置

脚本会自动读取项目根目录的 `.env` 文件，需要配置以下变量：

```bash
# PostgreSQL 配置
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password
POSTGRES_DB=wes_db
POSTGRES_PORT=5432
POSTGRES_HOST=localhost

# Docker 容器名称（可选）
DB_CONTAINER=wes_postgres
```

## 初始化内容

### 默认管理员账户

```
用户名: admin
密码: admin123
邮箱: admin@wes.local
```

⚠️ **安全提示**：请在首次登录后立即修改默认密码！

### 初始化的角色

| 角色名称 | 描述 | 权限范围 |
|---------|------|---------|
| 超级管理员 | 拥有系统所有权限 | 所有用户、角色、权限管理 |
| 管理员 | 拥有基本管理权限 | 查看和列表权限 |

### 初始化的权限

#### 用户管理 (admin:user:*)
- `admin:user:create` - 创建用户
- `admin:user:update` - 更新用户
- `admin:user:delete` - 删除用户
- `admin:user:detail` - 查看用户详情
- `admin:user:list` - 查看用户列表
- `admin:user:menu` - 用户管理菜单

#### 角色管理 (admin:role:*)
- `admin:role:create` - 创建角色
- `admin:role:update` - 更新角色
- `admin:role:delete` - 删除角色
- `admin:role:detail` - 查看角色详情
- `admin:role:list` - 查看角色列表
- `admin:role:menu` - 角色管理菜单

#### 权限管理 (admin:permission:*)
- `admin:permission:create` - 创建权限
- `admin:permission:update` - 更新权限
- `admin:permission:delete` - 删除权限
- `admin:permission:detail` - 查看权限详情
- `admin:permission:list` - 查看权限列表
- `admin:permission:menu` - 权限管理菜单

## 数据库表结构

### 核心表
- `users` - 用户表
- `roles` - 角色表
- `permissions` - 权限表
- `user_roles` - 用户-角色关联表
- `role_permissions` - 角色-权限关联表
- `audit_logs` - 操作审计日志表

### 关联关系

```
users ←→ user_roles ←→ roles ←→ role_permissions ←→ permissions
```

## 技术细节

### 密码哈希

系统使用 **Argon2** 算法进行密码哈希，这是目前最安全的密码哈希算法之一：

- **库**: `pwdlib` 或 `passlib`
- **算法**: Argon2id
- **参数**: m=65536, t=3, p=4 (内存成本 64MB, 时间迭代 3 次, 并行度 4)
- **哈希格式**: `$argon2id$v=19$m=65536,t=3,p=4$...$...`

**为什么选择 Argon2？**
- 抗 GPU/ASIC 破解攻击
- 内存硬度，增加暴力破解成本
- 2015 年密码哈希竞赛冠军
- OWASP 推荐的密码哈希算法

### 其他设置

- **时区**: UTC
- **级联删除**: 用户/角色删除时，关联关系自动删除
- **审计日志**: 所有操作都会记录到 `audit_logs` 表

## 故障排除

### 数据库连接失败
```bash
# 检查 Docker 容器状态
docker ps | grep wes_postgres

# 启动数据库容器
docker-compose up -d db

# 查看容器日志
docker logs wes_postgres
```

### 密码哈希生成失败
```bash
# 检查 Python 是否安装
python3 --version

# 安装 pwdlib (推荐) 或 passlib
pip install pwdlib
# 或
pip install passlib
```

### 权限错误
```bash
# 添加脚本执行权限
chmod +x scripts/database/run_init_db.sh
```

### 手动生成密码哈希
如果需要手动生成密码哈希（用于自定义 SQL）：

```python
# 方法 1: 使用 pwdlib
from pwdlib import PasswordHash
from pwdlib.hashers import argon2

pwd_hasher = PasswordHash([argon2.Argon2Hasher()])
hashed = pwd_hasher.hash("your_password")
print(hashed)

# 方法 2: 使用 passlib
from passlib.hash import argon2

hashed = argon2.hash("your_password")
print(hashed)
```

## 生产环境建议

1. **密码安全**
   - 使用强密码作为初始管理员密码（至少 16 位，包含大小写字母、数字、符号）
   - 初始化完成后立即修改默认密码
   - 定期更新管理员密码

2. **数据安全**
   - 定期备份数据库
   - 启用 SSL/TLS 连接
   - 配置防火墙规则
   - 限制数据库访问 IP

3. **系统维护**
   - 定期更新数据库软件
   - 监控审计日志
   - 定期检查用户权限
   - 及时删除不再使用的账户

## 开发环境快速开始

```bash
# 1. 启动数据库
docker-compose up -d

# 2. 初始化数据库
./scripts/database/run_init_db.sh

# 3. 启动应用
uvicorn src.main:app --reload

# 4. 访问系统
# 用户名: admin
# 密码: admin123
```
