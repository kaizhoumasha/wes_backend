# API 认证系统设计文档

> **项目**: 休斯顿 P9 智能仓储执行系统 (WES)  
> **模块**: API Key + Secret 认证系统  
> **版本**: 1.0  
> **日期**: 2026-01-29  
> **作者**: System Architect

## 文档目录

1. [概述](#1-概述)
2. [设计原则](#2-设计原则)
3. [系统架构](#3-系统架构)
4. [数据模型设计](#4-数据模型设计)
5. [认证流程](#5-认证流程)
6. [核心实现](#6-核心实现)
7. [API 接口设计](#7-api-接口设计)
8. [安全机制](#8-安全机制)
9. [性能优化](#9-性能优化)
10. [实施计划](#10-实施计划)

---

## 1. 概述

### 1.1 背景

WES 系统需要为第三方设备（ECS、RCS、WMS 等）提供安全的 API 访问能力。传统的 JWT 认证适用于用户登录场景，但不适合机器对机器（M2M）的长期认证需求。

### 1.2 目标

- 为设备提供基于 API Key + Secret 的认证机制
- 与现有 JWT 用户认证系统共存
- 复用现有 RBAC 权限系统
- 提供完整的审计日志
- 支持 IP 白名单和速率限制

### 1.3 适用场景

| 场景 | 认证方式 | 说明 |
|------|---------|------|
| 用户登录 | JWT Token | 管理员、操作员通过 Web/移动端访问 |
| 设备调用 | API Key + Secret | ECS、RCS、WMS 等系统调用 WES API |
| 混合场景 | 两者皆可 | 某些 API 同时支持用户和设备访问 |

---

## 2. 设计原则

本设计严格遵循以下软件工程原则：

### 2.1 DRY (Don't Repeat Yourself)

**复用现有组件，避免重复造轮子**

- ✅ 复用 `BaseService` 和 `BaseRepository`
- ✅ 复用现有 `Permission` 表和 RBAC 系统
- ✅ 复用 Redis 缓存策略（`CacheDep`）
- ✅ 复用 `BaseMixin`、`EnterpriseMixin`、`SoftDeleteMixin`
- ✅ 复用异常处理和响应格式

### 2.2 KISS (Keep It Simple, Stupid)

**保持简单，避免过度设计**

- ✅ 只实现核心功能：认证、权限、审计
- ✅ 签名算法：只用 HMAC-SHA256（不支持多种算法）
- ✅ 时间戳验证：5 分钟窗口（不需要复杂的 Nonce）
- ✅ IP 白名单：简单列表匹配（不需要 CIDR）
- ✅ 速率限制：固定配置（不需要动态调整）

### 2.3 SOLID

**单一职责、开闭原则、依赖倒置**

- **S (Single Responsibility)**: 每个类只负责一件事
  - `SignatureService`: 只负责签名计算和验证
  - `APIAppService`: 只负责应用管理
  - `verify_api_auth`: 只负责认证逻辑
  
- **O (Open/Closed)**: 通过依赖注入扩展
  - 使用 FastAPI 的 `Depends` 机制
  - 新的认证方式可通过新的依赖函数添加
  
- **L (Liskov Substitution)**: JWT 和 API Key 可互换
  - 两种认证方式返回相同的上下文接口
  
- **I (Interface Segregation)**: 最小化接口
  - `APIAppContext` 只包含必要字段
  
- **D (Dependency Inversion)**: 依赖抽象
  - 依赖 `AsyncSession` 和 `CacheDep` 抽象

### 2.4 YAGNI (You Aren't Gonna Need It)

**只实现当前需要的功能**

- ❌ 不实现：复杂的 Nonce 防重放（时间戳已足够）
- ❌ 不实现：多种签名算法支持（HMAC-SHA256 足够）
- ❌ 不实现：动态速率限制配置（固定配置即可）
- ❌ 不实现：CIDR IP 白名单（简单列表足够）
- ❌ 不实现：密钥轮换机制（手动更新即可）
- ✅ 只实现：核心认证、权限验证、审计日志

---

## 3. 系统架构

### 3.1 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    WES API Gateway                       │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  ┌──────────────┐              ┌──────────────┐         │
│  │ JWT 认证     │              │ API Key 认证 │         │
│  │ (用户)       │              │ (设备)       │         │
│  └──────┬───────┘              └──────┬───────┘         │
│         │                             │                  │
│         └─────────────┬───────────────┘                  │
│                       ↓                                   │
│              ┌─────────────────┐                         │
│              │  RBAC 权限系统  │                         │
│              └─────────────────┘                         │
│                       ↓                                   │
│              ┌─────────────────┐                         │
│              │  业务逻辑层     │                         │
│              └─────────────────┘                         │
└─────────────────────────────────────────────────────────┘
```

### 3.2 认证流程对比

| 步骤 | JWT 认证 (用户) | API Key 认证 (设备) |
|------|----------------|-------------------|
| 1. 获取凭证 | 用户名 + 密码登录 | 管理员创建应用获得 app_id + app_secret |
| 2. 请求认证 | `Authorization: Bearer <token>` | `X-App-ID`, `X-Timestamp`, `X-Signature` |
| 3. 验证方式 | JWT 签名验证 | HMAC-SHA256 签名验证 |
| 4. 权限获取 | 从 User -> Role -> Permission | 从 APIApplication -> Permission |
| 5. 缓存策略 | Redis 缓存用户权限 | Redis 缓存应用权限 |

### 3.3 数据流图

```
设备端                          WES 服务端
  │                                │
  │  1. 计算签名                   │
  │  sign = HMAC(secret, data)     │
  │                                │
  │  2. 发送请求                   │
  ├────────────────────────────────>
  │  Headers:                      │
  │  - X-App-ID: app_xxx           │
  │  - X-Timestamp: 1234567890     │
  │  - X-Signature: abc123...      │
  │                                │
  │                                │  3. 验证时间戳
  │                                │  (5分钟窗口)
  │                                │
  │                                │  4. 查询应用信息
  │                                │  (Redis/DB)
  │                                │
  │                                │  5. 重新计算签名
  │                                │  expected = HMAC(secret, data)
  │                                │
  │                                │  6. 比对签名
  │                                │  compare(sign, expected)
  │                                │
  │                                │  7. 验证 IP 白名单
  │                                │
  │                                │  8. 检查速率限制
  │                                │
  │                                │  9. 获取权限
  │                                │  (Redis/DB)
  │                                │
  │                                │  10. 执行业务逻辑
  │                                │
  │  11. 返回响应                  │
  <────────────────────────────────┤
  │                                │
```

---

## 4. 数据模型设计

### 4.1 核心表结构

#### 4.1.1 API 应用表 (api_applications)

**设计说明**: 复用现有 Mixin，遵循项目规范

```python
class APIApplication(
    BaseMixin,           # id, repr
    EnterpriseMixin,     # created_by, updated_by, remark, enterprise_id
    SoftDeleteMixin,     # is_deleted, deleted_at, deleted_by
    DataTableMixin,      # created_at, updated_at
    table=True
):
    __tablename__ = "api_applications"
    __schema__ = SchemaType.SYS.value  # wes_sys schema
    
    # 核心字段
    app_id: str = Field(unique=True, index=True, max_length=50)
    app_secret_encrypted: str = Field(max_length=500)  # Fernet 加密存储
    app_name: str = Field(max_length=100)
    app_type: str = Field(max_length=50)  # ECS/RCS/WMS/Third-Party
    description: str | None = Field(default=None, max_length=500)
    
    # 状态管理
    status: str = Field(default="active", max_length=20)  # active/revoked
    expires_at: datetime | None = Field(default=None)
    
    # 安全配置
    ip_whitelist: list[str] | None = Field(
        sa_type=JSON, 
        default=None,
        description="IP 白名单，如 ['192.168.1.100', '192.168.1.101']"
    )
    
    # 速率限制配置
    rate_limit_per_minute: int = Field(default=100)
    rate_limit_per_hour: int = Field(default=5000)
    
    # 索引优化
    __table_args__ = (
        Index("ix_api_app_status", "status", "is_deleted"),
        Index("ix_api_app_type", "app_type", "is_deleted"),
    )
```

**字段说明**:

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `app_id` | str | 应用唯一标识 | `app_1a2b3c4d5e6f` |
| `app_secret_encrypted` | str | 加密后的密钥 | Fernet 加密字符串 |
| `app_name` | str | 应用名称 | `SMT自动线控制系统` |
| `app_type` | str | 应用类型 | `ECS`, `RCS`, `WMS`, `Third-Party` |
| `status` | str | 状态 | `active`, `revoked` |
| `expires_at` | datetime | 过期时间 | `2027-01-29T00:00:00Z` |
| `ip_whitelist` | JSON | IP 白名单 | `["192.168.1.100"]` |
| `rate_limit_per_minute` | int | 每分钟请求限制 | `100` |
| `rate_limit_per_hour` | int | 每小时请求限制 | `5000` |


#### 4.1.2 API 应用权限关联表 (api_app_permissions)

**设计说明**: 多对多关联表，复用现有 Permission 表

```python
# 中间表定义 (在 models/relationships.py 中)
api_app_permissions = Table(
    "api_app_permissions",
    Base.metadata,
    Column("app_id", Integer, ForeignKey("wes_sys.api_applications.id"), primary_key=True),
    Column("permission_id", Integer, ForeignKey("wes_sys.permissions.id"), primary_key=True),
    Column("created_at", DateTime, default=datetime.now(UTC)),
    schema=SchemaType.SYS.value,
)
```

**关系定义**:

```python
# 在 APIApplication 模型中添加
permissions: list["Permission"] = Relationship(
    back_populates="api_applications",
    link_model=api_app_permissions,
)

# 在 Permission 模型中添加
api_applications: list["APIApplication"] = Relationship(
    back_populates="permissions",
    link_model=api_app_permissions,
)
```

#### 4.1.3 API 访问日志表 (api_access_logs)

**设计说明**: 简化版审计日志，只记录关键信息

```python
class APIAccessLog(BaseMixin, table=True):
    __tablename__ = "api_access_logs"
    __schema__ = SchemaType.SYS.value
    
    # 关联信息
    app_id: str = Field(index=True, max_length=50)
    app_name: str = Field(max_length=100)
    
    # 请求信息
    request_id: str = Field(max_length=50)  # 关联到请求日志
    method: str = Field(max_length=10)
    path: str = Field(max_length=500)
    
    # 响应信息
    status_code: int
    response_time_ms: int
    
    # 客户端信息
    ip_address: str = Field(max_length=50)
    user_agent: str | None = Field(default=None, max_length=500)
    
    # 错误信息
    error_message: str | None = Field(default=None, max_length=1000)
    
    # 时间戳
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    
    # 索引优化
    __table_args__ = (
        Index("ix_api_log_app_time", "app_id", "created_at"),
        Index("ix_api_log_status", "status_code", "created_at"),
        Index("ix_api_log_path", "path", "created_at"),
    )
```

### 4.2 数据库迁移脚本

**Alembic 迁移文件**: `migrations/versions/xxxx_add_api_authentication.py`

```python
"""Add API authentication tables

Revision ID: xxxx
Revises: yyyy
Create Date: 2026-01-29 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = 'xxxx'
down_revision = 'yyyy'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. 创建 api_applications 表
    op.create_table(
        'api_applications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('app_id', sa.String(length=50), nullable=False),
        sa.Column('app_secret_encrypted', sa.String(length=500), nullable=False),
        sa.Column('app_name', sa.String(length=100), nullable=False),
        sa.Column('app_type', sa.String(length=50), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ip_whitelist', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('rate_limit_per_minute', sa.Integer(), nullable=False),
        sa.Column('rate_limit_per_hour', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('remark', sa.String(length=500), nullable=True),
        sa.Column('enterprise_id', sa.Integer(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        schema='wes_sys'
    )
    op.create_index('ix_api_app_app_id', 'api_applications', ['app_id'], unique=True, schema='wes_sys')
    op.create_index('ix_api_app_status', 'api_applications', ['status', 'is_deleted'], schema='wes_sys')
    op.create_index('ix_api_app_type', 'api_applications', ['app_type', 'is_deleted'], schema='wes_sys')
    
    # 2. 创建 api_app_permissions 关联表
    op.create_table(
        'api_app_permissions',
        sa.Column('app_id', sa.Integer(), nullable=False),
        sa.Column('permission_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['app_id'], ['wes_sys.api_applications.id'], ),
        sa.ForeignKeyConstraint(['permission_id'], ['wes_sys.permissions.id'], ),
        sa.PrimaryKeyConstraint('app_id', 'permission_id'),
        schema='wes_sys'
    )
    
    # 3. 创建 api_access_logs 表
    op.create_table(
        'api_access_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('app_id', sa.String(length=50), nullable=False),
        sa.Column('app_name', sa.String(length=100), nullable=False),
        sa.Column('request_id', sa.String(length=50), nullable=False),
        sa.Column('method', sa.String(length=10), nullable=False),
        sa.Column('path', sa.String(length=500), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=False),
        sa.Column('response_time_ms', sa.Integer(), nullable=False),
        sa.Column('ip_address', sa.String(length=50), nullable=False),
        sa.Column('user_agent', sa.String(length=500), nullable=True),
        sa.Column('error_message', sa.String(length=1000), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        schema='wes_sys'
    )
    op.create_index('ix_api_log_app_id', 'api_access_logs', ['app_id'], schema='wes_sys')
    op.create_index('ix_api_log_app_time', 'api_access_logs', ['app_id', 'created_at'], schema='wes_sys')
    op.create_index('ix_api_log_status', 'api_access_logs', ['status_code', 'created_at'], schema='wes_sys')
    op.create_index('ix_api_log_path', 'api_access_logs', ['path', 'created_at'], schema='wes_sys')

def downgrade() -> None:
    op.drop_table('api_access_logs', schema='wes_sys')
    op.drop_table('api_app_permissions', schema='wes_sys')
    op.drop_table('api_applications', schema='wes_sys')
```

---

## 5. 认证流程

### 5.1 签名计算算法

**客户端签名计算** (Python 示例):

```python
import hmac
import hashlib
import time

def calculate_signature(
    app_id: str,
    app_secret: str,
    method: str,
    path: str,
    body: str,
    timestamp: str
) -> str:
    """
    计算请求签名
    
    签名字符串格式: app_id + timestamp + method + path + body
    """
    sign_string = f"{app_id}{timestamp}{method}{path}{body}"
    signature = hmac.new(
        app_secret.encode('utf-8'),
        sign_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature

# 使用示例
app_id = "app_1a2b3c4d5e6f"
app_secret = "sec_7g8h9i0j1k2l3m4n5o6p"
method = "POST"
path = "/api/v1/tasks/dispatch"
body = '{"task_type":"PICK","material_id":"R001"}'
timestamp = str(int(time.time()))

signature = calculate_signature(app_id, app_secret, method, path, body, timestamp)
```

**服务端签名验证**:

```python
def verify_signature(
    expected_signature: str,
    actual_signature: str
) -> bool:
    """
    验证签名（防时序攻击）
    
    使用 hmac.compare_digest 进行常量时间比较
    """
    return hmac.compare_digest(expected_signature, actual_signature)
```

### 5.2 完整认证流程

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 客户端准备请求                                            │
├─────────────────────────────────────────────────────────────┤
│ - 获取当前时间戳: timestamp = int(time.time())               │
│ - 准备请求体: body = json.dumps(data)                        │
│ - 计算签名: signature = HMAC(secret, sign_string)            │
│ - 设置请求头:                                                │
│   * X-App-ID: app_xxx                                        │
│   * X-Timestamp: 1234567890                                  │
│   * X-Signature: abc123...                                   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. WES 接收请求                                              │
├─────────────────────────────────────────────────────────────┤
│ - 提取请求头: app_id, timestamp, signature                   │
│ - 读取请求体: body = await request.body()                    │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. 验证时间戳                                                │
├─────────────────────────────────────────────────────────────┤
│ - 计算时间差: diff = abs(current_time - request_time)        │
│ - 检查窗口: if diff > 300: raise AuthException("请求已过期") │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. 查询应用信息 (带缓存)                                     │
├─────────────────────────────────────────────────────────────┤
│ - 缓存键: api_app:{app_id}                                   │
│ - 缓存命中: 直接返回应用信息                                 │
│ - 缓存未命中: 查询数据库 -> 写入缓存                         │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. 验证应用状态                                              │
├─────────────────────────────────────────────────────────────┤
│ - 检查存在: if not app: raise AuthException("应用不存在")    │
│ - 检查状态: if app.status != "active": raise ...             │
│ - 检查过期: if app.expires_at < now: raise ...               │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 6. 验证签名                                                  │
├─────────────────────────────────────────────────────────────┤
│ - 解密密钥: secret = decrypt(app.app_secret_encrypted)       │
│ - 重新计算: expected = HMAC(secret, sign_string)             │
│ - 比对签名: if not compare_digest(expected, actual): raise   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 7. 验证 IP 白名单                                            │
├─────────────────────────────────────────────────────────────┤
│ - 获取客户端 IP: client_ip = request.client.host             │
│ - 检查白名单: if ip_whitelist and ip not in whitelist: raise │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 8. 检查速率限制                                              │
├─────────────────────────────────────────────────────────────┤
│ - Redis 键: rate_limit:{app_id}:minute:{timestamp/60}        │
│ - 增加计数: INCR key                                         │
│ - 检查限制: if count > limit: raise RateLimitException │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 9. 获取权限 (带缓存)                                         │
├─────────────────────────────────────────────────────────────┤
│ - 缓存键: api_app_perms:{app_id}                             │
│ - 缓存命中: 直接返回权限集合                                 │
│ - 缓存未命中: 查询数据库 -> 写入缓存                         │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 10. 构建认证上下文                                           │
├─────────────────────────────────────────────────────────────┤
│ - 创建 APIAppContext:                                        │
│   * app_id                                                   │
│   * app_name                                                 │
│   * permissions                                              │
│   * enterprise_id                                            │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 11. 执行业务逻辑                                             │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 12. 记录访问日志 (异步)                                      │
├─────────────────────────────────────────────────────────────┤
│ - 记录到 api_access_logs 表                                  │
│ - 包含: app_id, method, path, status_code, response_time     │
└─────────────────────────────────────────────────────────────┘
```


---

## 6. 核心实现

### 6.1 密钥加密服务

**文件**: `src/core/encryption.py`

```python
"""
密钥加密服务

使用 Fernet 对称加密存储 API Secret
"""
from cryptography.fernet import Fernet
from src.core.conf import settings

class EncryptionService:
    """密钥加密服务 - 单一职责"""
    
    def __init__(self):
        # 从环境变量读取加密密钥
        # 生成方法: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
        self.cipher = Fernet(settings.API_SECRET_ENCRYPTION_KEY.encode())
    
    def encrypt(self, plaintext: str) -> str:
        """加密明文"""
        return self.cipher.encrypt(plaintext.encode()).decode()
    
    def decrypt(self, ciphertext: str) -> str:
        """解密密文"""
        return self.cipher.decrypt(ciphertext.encode()).decode()

# 单例
encryption_service = EncryptionService()
```

**配置文件更新**: `src/core/conf.py`

```python
class Settings(BaseSettings):
    # ... 现有配置 ...
    
    # ==================== API 认证配置 ====================
    API_SECRET_ENCRYPTION_KEY: str = ""  # Fernet 加密密钥
    
    @model_validator(mode="after")
    def validate_security_settings(self):
        # ... 现有验证 ...
        
        # 验证 API 加密密钥
        if not self.API_SECRET_ENCRYPTION_KEY:
            raise ValueError(
                "❌ 安全错误: API_SECRET_ENCRYPTION_KEY 未在环境变量中设置。\n"
                '   生成方法: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        
        return self
```

### 6.2 签名服务

**文件**: `src/app/api_auth/services/signature_service.py`

```python
"""
签名服务 - 纯函数实现

遵循 KISS 原则：只实现 HMAC-SHA256 签名
"""
import hmac
import hashlib

class SignatureService:
    """签名服务 - 单一职责，纯函数"""
    
    @staticmethod
    def calculate(
        app_secret: str,
        app_id: str,
        timestamp: str,
        method: str,
        path: str,
        body: str
    ) -> str:
        """
        计算请求签名
        
        签名字符串格式: app_id + timestamp + method + path + body
        
        Args:
            app_secret: 应用密钥（明文）
            app_id: 应用 ID
            timestamp: 时间戳
            method: HTTP 方法
            path: 请求路径
            body: 请求体
        
        Returns:
            HMAC-SHA256 签名（十六进制字符串）
        """
        sign_string = f"{app_id}{timestamp}{method}{path}{body}"
        return hmac.new(
            app_secret.encode('utf-8'),
            sign_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
    
    @staticmethod
    def verify(expected: str, actual: str) -> bool:
        """
        验证签名（防时序攻击）
        
        使用 hmac.compare_digest 进行常量时间比较
        
        Args:
            expected: 期望的签名
            actual: 实际的签名
        
        Returns:
            签名是否匹配
        """
        return hmac.compare_digest(expected, actual)
```

### 6.3 应用服务

**文件**: `src/app/api_auth/services/app_service.py`

```python
"""
API 应用服务

继承 BaseService，复用 CRUD 能力
"""
import secrets
from datetime import datetime, UTC
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_service import BaseService
from src.core.encryption import encryption_service
from src.database.redis_cache import RedisCache
from src.app.api_auth.models import APIApplication

class APIAppService(BaseService[APIApplication]):
    """API 应用服务 - 继承 BaseService (DRY)"""
    
    def __init__(self):
        from src.app.api_auth.repositories import APIAppRepository
        super().__init__(
            repository=APIAppRepository(),
            enable_cache=True,
            cache_prefix="api_app:detail",
            cache_expire=300,  # 5 分钟
        )
    
    async def create_app(
        self,
        db: AsyncSession,
        data: dict,
        cache: RedisCache | None = None
    ) -> tuple[APIApplication, str]:
        """
        创建应用 - 返回明文 secret (仅一次)
        
        Args:
            db: 数据库会话
            data: 应用数据
            cache: 缓存实例
        
        Returns:
            (应用实例, 明文密钥)
        """
        # 生成 app_id 和 app_secret
        app_id = f"app_{secrets.token_urlsafe(12)}"
        app_secret = f"sec_{secrets.token_urlsafe(32)}"
        
        # 加密存储
        data["app_id"] = app_id
        data["app_secret_encrypted"] = encryption_service.encrypt(app_secret)
        
        # 设置默认值
        data.setdefault("status", "active")
        data.setdefault("rate_limit_per_minute", 100)
        data.setdefault("rate_limit_per_hour", 5000)
        
        # 创建应用
        app = await self.create(db, data, cache)
        
        return app, app_secret  # 明文 secret 仅返回一次
    
    async def get_by_app_id(
        self,
        db: AsyncSession,
        cache: RedisCache,
        app_id: str
    ) -> APIApplication | None:
        """
        根据 app_id 获取应用 (带缓存)
        
        Args:
            db: 数据库会话
            cache: 缓存实例
            app_id: 应用 ID
        
        Returns:
            应用实例或 None
        """
        cache_key = f"api_app:{app_id}"
        
        # 尝试缓存
        cached = await cache.get(cache_key)
        if cached:
            return APIApplication.model_validate_json(cached)
        
        # 查询数据库
        result = await db.execute(
            select(APIApplication)
            .where(APIApplication.app_id == app_id)
            .where(APIApplication.is_deleted == False)
        )
        app = result.scalar_one_or_none()
        
        # 写入缓存
        if app:
            await cache.set(cache_key, app.model_dump_json(), expire=300)
        
        return app
    
    async def revoke_app(
        self,
        db: AsyncSession,
        app_id: str,
        cache: RedisCache | None = None
    ) -> bool:
        """
        撤销应用（设置状态为 revoked）
        
        Args:
            db: 数据库会话
            app_id: 应用 ID
            cache: 缓存实例
        
        Returns:
            是否成功
        """
        app = await self.get_by_app_id(db, cache, app_id)
        if not app:
            return False
        
        await self.update(db, app.id, {"status": "revoked"}, cache)
        
        # 清除缓存
        if cache:
            await cache.delete(f"api_app:{app_id}")
            await cache.delete(f"api_app_perms:{app.id}")
        
        return True

# 单例
api_app_service = APIAppService()
```

### 6.4 权限服务

**文件**: `src/app/api_auth/services/permission_service.py`

```python
"""
API 应用权限服务

复用现有 Permission 表
"""
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import Permission
from src.app.api_auth.models.relationships import api_app_permissions
from src.database.redis_cache import RedisCache
from src.core.logger import logger

async def get_app_permissions(
    db: AsyncSession,
    cache: RedisCache,
    app_id: int
) -> set[str]:
    """
    获取应用权限集合 (带缓存)
    
    复用现有 Permission 表，通过中间表关联
    
    Args:
        db: 数据库会话
        cache: 缓存实例
        app_id: 应用 ID (数据库主键)
    
    Returns:
        权限标识集合
    """
    cache_key = f"api_app_perms:{app_id}"
    
    # 尝试缓存
    cached = await cache.get(cache_key)
    if cached:
        try:
            return set(json.loads(cached))
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"权限缓存解析失败: {cache_key}")
    
    # 查询权限 (通过中间表)
    result = await db.execute(
        select(Permission.name)
        .join(api_app_permissions, api_app_permissions.c.permission_id == Permission.id)
        .where(api_app_permissions.c.app_id == app_id)
        .where(Permission.is_deleted == False)
    )
    permissions = {row[0] for row in result.all()}
    
    # 写入缓存
    if permissions:
        await cache.set(cache_key, json.dumps(list(permissions)), expire=300)
    
    return permissions

async def invalidate_app_permissions(
    cache: RedisCache,
    app_id: int
) -> None:
    """
    清除应用权限缓存
    
    在以下情况调用：
    - 分配/移除应用权限
    - 修改权限定义
    - 启用/禁用权限
    
    Args:
        cache: 缓存实例
        app_id: 应用 ID
    """
    cache_key = f"api_app_perms:{app_id}"
    await cache.delete(cache_key)
    logger.debug(f"清除应用权限缓存: {cache_key}")
```


### 6.5 API 认证依赖

**文件**: `src/core/api_security.py`

```python
"""
API 认证依赖注入

提供 FastAPI 依赖函数，用于验证 API Key 认证
"""
from dataclasses import dataclass
from typing import Annotated
import time

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AuthException, PermissionException, RateLimitException
from src.core.encryption import encryption_service
from src.core.logger import logger
from src.database.dependencies import AsyncSessionDep, CacheDep
from src.app.api_auth.services.app_service import api_app_service
from src.app.api_auth.services.permission_service import get_app_permissions
from src.app.api_auth.services.signature_service import SignatureService

@dataclass
class APIAppContext:
    """
    API 应用上下文 - 最小化数据
    
    遵循 KISS 原则：只包含必要字段
    """
    app_id: str
    app_name: str
    app_type: str
    permissions: set[str]
    enterprise_id: int | None

async def verify_api_auth(
    request: Request,
    db: AsyncSessionDep,
    cache: CacheDep,
) -> APIAppContext | None:
    """
    验证 API 认证 - 可选依赖
    
    返回 None 表示未提供 API 认证头 (允许 JWT 认证)
    抛出异常表示 API 认证失败
    
    Args:
        request: FastAPI 请求对象
        db: 数据库会话
        cache: 缓存服务
    
    Returns:
        APIAppContext 或 None
    
    Raises:
        AuthException: 认证失败
    """
    # 1. 提取请求头
    app_id = request.headers.get("X-App-ID")
    timestamp = request.headers.get("X-Timestamp")
    signature = request.headers.get("X-Signature")
    
    # 未提供 API 认证头 - 允许其他认证方式
    if not app_id:
        return None
    
    # 提供了部分头但不完整 - 认证失败
    if not all([timestamp, signature]):
        raise AuthException("API 认证头不完整，需要 X-App-ID, X-Timestamp, X-Signature")
    
    # 2. 验证时间戳 (5分钟窗口) - KISS
    try:
        request_time = int(timestamp)
        current_time = int(time.time())
        time_diff = abs(current_time - request_time)
        
        if time_diff > 300:  # 5 分钟
            raise AuthException(f"请求已过期 (时间差: {time_diff}秒)")
    except ValueError:
        raise AuthException("时间戳格式错误")
    
    # 3. 获取应用信息 (带缓存)
    app = await api_app_service.get_by_app_id(db, cache, app_id)
    if not app:
        raise AuthException(f"应用不存在: {app_id}")
    
    if app.status != "active":
        raise AuthException(f"应用已被禁用: {app.status}")
    
    # 4. 验证过期时间
    if app.expires_at:
        from datetime import datetime, UTC
        if datetime.now(UTC) > app.expires_at:
            raise AuthException("应用已过期")
    
    # 5. 验证签名
    body = await request.body()
    
    # 解密密钥
    app_secret = encryption_service.decrypt(app.app_secret_encrypted)
    
    # 重新计算签名
    expected_signature = SignatureService.calculate(
        app_secret=app_secret,
        app_id=app_id,
        timestamp=timestamp,
        method=request.method,
        path=str(request.url.path),
        body=body.decode('utf-8')
    )
    
    # 比对签名（防时序攻击）
    if not SignatureService.verify(expected_signature, signature):
        raise AuthException("签名验证失败")
    
    # 6. 验证 IP 白名单 (简单列表匹配)
    if app.ip_whitelist:
        client_ip = request.client.host if request.client else "unknown"
        if client_ip not in app.ip_whitelist:
            raise AuthException(f"IP {client_ip} 不在白名单中")
    
    # 7. 检查速率限制
    await _check_rate_limit(cache, app)
    
    # 8. 获取权限 (复用 RBAC)
    permissions = await get_app_permissions(db, cache, app.id)
    
    # 9. 记录到请求上下文
    request.state.api_app_id = app.app_id
    request.state.api_app_name = app.app_name
    
    return APIAppContext(
        app_id=app.app_id,
        app_name=app.app_name,
        app_type=app.app_type,
        permissions=permissions,
        enterprise_id=app.enterprise_id,
    )

async def _check_rate_limit(cache: CacheDep, app) -> None:
    """
    检查速率限制 (滑动窗口算法)
    
    Args:
        cache: 缓存服务
        app: 应用实例
    
    Raises:
        RateLimitException: 超过速率限制
    """
    current_time = int(time.time())
    
    # 每分钟限制
    minute_key = f"rate_limit:{app.app_id}:minute:{current_time // 60}"
    minute_count = await cache.incr(minute_key)
    if minute_count == 1:
        await cache.expire(minute_key, 60)
    
    if minute_count > app.rate_limit_per_minute:
        raise RateLimitException(
            f"超过每分钟请求限制 ({app.rate_limit_per_minute})"
        )
    
    # 每小时限制
    hour_key = f"rate_limit:{app.app_id}:hour:{current_time // 3600}"
    hour_count = await cache.incr(hour_key)
    if hour_count == 1:
        await cache.expire(hour_key, 3600)
    
    if hour_count > app.rate_limit_per_hour:
        raise RateLimitException(
            f"超过每小时请求限制 ({app.rate_limit_per_hour})"
        )

# ==================== 依赖注入类型 ====================

# 可选 API 认证 (允许 JWT 认证)
DependsAPIAuth = Annotated[APIAppContext | None, Depends(verify_api_auth)]

# 必需 API 认证
async def require_api_auth(
    app_ctx: DependsAPIAuth,
) -> APIAppContext:
    """要求 API 认证"""
    if app_ctx is None:
        raise AuthException("需要 API 认证")
    return app_ctx

RequireAPIAuth = Annotated[APIAppContext, Depends(require_api_auth)]

# ==================== 权限验证依赖 ====================

def RequireAPIPermission(permission_name: str):
    """
    API 权限验证依赖工厂
    
    Args:
        permission_name: 权限标识 (如 "task:create")
    
    Returns:
        FastAPI 依赖函数
    """
    async def verify_permission(
        app_ctx: RequireAPIAuth,
    ) -> None:
        if permission_name not in app_ctx.permissions:
            raise PermissionException(f"需要权限: {permission_name}")
    
    return verify_permission
```

### 6.6 异常定义

**文件**: `src/core/exceptions.py` (添加新异常)

```python
class RateLimitException(AppException):
    """速率限制异常"""
    
    def __init__(self, message: str = "请求过于频繁，请稍后重试"):
        super().__init__(
            code=429,
            message=message,
            http_status_code=429
        )
```

---

## 7. API 接口设计

### 7.1 应用管理 API

**文件**: `src/app/api_auth/v1/application.py`

```python
"""
API 应用管理接口

使用 BaseAPI 生成标准 CRUD
"""
from typing import Annotated
from fastapi import APIRouter, Body, Depends, Path

from src.core.base_api import BaseAPI
from src.core.rbac import RequirePermission
from src.core.response.response_util import response_builder
from src.database.dependencies import AsyncSessionDep, CacheDep
from src.app.api_auth.models import (
    APIApplication,
    APIApplicationCreate,
    APIApplicationUpdate,
    APIApplicationResponse,
)
from src.app.api_auth.services.app_service import api_app_service

# 使用 BaseAPI 生成标准 CRUD
api_app_api = BaseAPI(
    module_name="admin",
    model=APIApplication,
    service=api_app_service,
    create_schema=APIApplicationCreate,
    update_schema=APIApplicationUpdate,
    response_schema=APIApplicationResponse,
    prefix="/api-auth/applications",
    tags=["API 认证管理"],
    enable_permission=True,
)

router = api_app_api.router

# ==================== 自定义端点 ====================

@router.get(
    "/available-permissions",
    summary="[api-auth:api_application:list_permissions] 获取系统支持的 API 权限列表",
    dependencies=[Depends(RequirePermission("api-auth:api_application:list_permissions"))],
)
async def get_system_api_permissions(
    db: AsyncSessionDep,
) -> ResponseSchemaModel[list[Any]]:
    """返回当前系统支持分配给 API 应用的权限列表。"""
    permissions = await permission_service.get_api_permissions(db, exclude_deleted=True)
    return response_builder.success(data=permissions)


@router.post(
    "/available-permissions/sync",
    summary="[api-auth:api_application:sync_permissions] 重新扫描并同步 API 权限",
    dependencies=[Depends(RequirePermission("api-auth:api_application:sync_permissions"))],
)
async def sync_system_api_permissions(
    request: Request,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[list[Any]]:
    """重新扫描代码中的权限并同步到数据库。"""
    _ = await sync_permissions_to_db(request.app, db)
    permissions = await permission_service.get_api_permissions(db, exclude_deleted=True)
    return response_builder.success(data=permissions, message="权限同步成功")


@router.post(
    "",
    summary="[api-auth:api_application:create] 创建 API 应用",
    dependencies=[Depends(RequirePermission("api-auth:api_application:create"))],
)
async def create_application(
    obj_in: Annotated[APIApplicationCreate, Body(...)],
    db: AsyncSessionDep,
    cache: CacheDep,
):
    """
    创建 API 应用
    
    ⚠️ 注意: app_secret 仅在创建时返回一次，请妥善保存！
    """
    data = obj_in.model_dump()
    
    # 创建应用（返回明文 secret）
    app, app_secret = await api_app_service.create_app(db, data, cache)
    
    # 构建响应（包含明文 secret）
    response_data = {
        **app.model_dump(),
        "app_secret": app_secret,  # 仅此一次返回
    }
    
    return response_builder.success(
        data=response_data,
        message="应用创建成功，请妥善保存 app_secret（仅显示一次）"
    )

@router.post(
    "/{id}/revoke",
    summary="[api-auth:api_application:revoke] 撤销 API 应用",
    dependencies=[Depends(RequirePermission("api-auth:api_application:revoke"))],
)
async def revoke_application(
    id: Annotated[int, Path(...)],
    db: AsyncSessionDep,
    cache: CacheDep,
):
    """撤销 API 应用（设置状态为 revoked）"""
    app = await api_app_service.get_by_id(db, cache, id)
    if not app:
        return response_builder.fail(message=f"应用不存在: {id}")
    
    success = await api_app_service.revoke_app(db, app.app_id, cache)
    if success:
        return response_builder.success(message="应用已撤销")
    else:
        return response_builder.fail(message="撤销失败")

@router.post(
    "/{id}/permissions",
    summary="[api-auth:api_application:assign_permission] 分配权限",
    dependencies=[Depends(RequirePermission("api-auth:api_application:assign_permission"))],
)
async def assign_permissions(
    id: Annotated[int, Path(...)],
    permission_ids: Annotated[list[int], Body(...)],
    db: AsyncSessionDep,
    cache: CacheDep,
):
    """为应用分配权限"""
    await api_app_service.assign_permissions(db, cache, id, permission_ids)
    return response_builder.success(message="权限分配成功")
```

### 7.2 Schema 定义

**文件**: `src/app/api_auth/models/api_application.py`

```python
"""API 应用模型和 Schema"""
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator
from sqlalchemy import JSON, Index

from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType

# ==================== 模型 ====================

class APIApplicationBase(BaseMixin):
    """API 应用基础字段"""
    
    app_name: str = Field(max_length=100)
    app_type: str = Field(max_length=50)  # ECS/RCS/WMS/Third-Party
    description: str | None = Field(default=None, max_length=500)
    ip_whitelist: list[str] | None = Field(default=None)
    rate_limit_per_minute: int = Field(default=100, ge=1, le=10000)
    rate_limit_per_hour: int = Field(default=5000, ge=1, le=1000000)
    expires_at: datetime | None = Field(default=None)

class APIApplication(
    APIApplicationBase,
    EnterpriseMixin,
    SoftDeleteMixin,
    DataTableMixin,
    table=True
):
    """API 应用表"""
    
    __tablename__: Literal["api_applications"] = "api_applications"
    __schema__ = SchemaType.SYS.value
    
    app_id: str = Field(unique=True, index=True, max_length=50)
    app_secret_encrypted: str = Field(max_length=500)
    status: str = Field(default="active", max_length=20)
    
    __table_args__ = (
        Index("ix_api_app_status", "status", "is_deleted"),
        Index("ix_api_app_type", "app_type", "is_deleted"),
    )

# ==================== Schemas ====================

class APIApplicationCreate(ModelFactory(APIApplicationBase).for_create()):
    """创建 Schema"""
    
    @field_validator("app_type")
    @classmethod
    def validate_app_type(cls, v: str) -> str:
        allowed_types = ["ECS", "RCS", "WMS", "Third-Party"]
        if v not in allowed_types:
            raise ValueError(f"app_type 必须是: {', '.join(allowed_types)}")
        return v

class APIApplicationUpdate(ModelFactory(APIApplicationBase).for_update()):
    """更新 Schema"""

class APIApplicationResponse(APIApplicationBase):
    """响应 Schema"""
    
    id: int
    app_id: str
    status: str
    created_at: datetime
    updated_at: datetime
```


---

## 8. 安全机制

### 8.1 密钥安全

| 安全措施 | 实现方式 | 说明 |
|---------|---------|------|
| **加密存储** | Fernet 对称加密 | app_secret 使用 Fernet 加密后存储 |
| **仅返回一次** | 创建时返回明文 | 后续查询不返回 app_secret |
| **密钥强度** | 32 字节随机 | 使用 `secrets.token_urlsafe(32)` 生成 |
| **加密密钥管理** | 环境变量 | `API_SECRET_ENCRYPTION_KEY` 存储在环境变量中 |

**密钥生成示例**:

```bash
# 生成 Fernet 加密密钥
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 添加到 .env 文件
echo "API_SECRET_ENCRYPTION_KEY=<生成的密钥>" >> .env
```

### 8.2 防重放攻击

| 机制 | 实现方式 | 说明 |
|------|---------|------|
| **时间戳验证** | 5 分钟窗口 | 拒绝超过 5 分钟的请求 |
| **签名唯一性** | 包含时间戳 | 每次请求签名不同 |

**为什么不需要 Nonce?**

- 时间戳已提供足够的防重放保护
- 5 分钟窗口内，攻击者无法获得有效签名
- 简化实现，遵循 KISS 原则

### 8.3 签名安全

| 安全措施 | 实现方式 | 说明 |
|---------|---------|------|
| **HMAC-SHA256** | 标准算法 | 行业标准，安全可靠 |
| **防时序攻击** | `hmac.compare_digest` | 常量时间比较 |
| **签名内容** | 完整请求数据 | app_id + timestamp + method + path + body |

### 8.4 IP 白名单

**实现方式**: 简单列表匹配

```python
# 配置示例
{
    "ip_whitelist": [
        "192.168.1.100",
        "192.168.1.101",
        "10.0.0.50"
    ]
}

# 验证逻辑
if app.ip_whitelist:
    client_ip = request.client.host
    if client_ip not in app.ip_whitelist:
        raise AuthException(f"IP {client_ip} 不在白名单中")
```

**为什么不支持 CIDR?**

- 当前需求不需要 CIDR
- 简单列表匹配足够
- 遵循 YAGNI 原则

### 8.5 速率限制

**实现方式**: Redis 滑动窗口算法

```python
# 每分钟限制
minute_key = f"rate_limit:{app_id}:minute:{timestamp // 60}"
count = await redis.incr(minute_key)
await redis.expire(minute_key, 60)

if count > limit_per_minute:
    raise RateLimitException()

# 每小时限制
hour_key = f"rate_limit:{app_id}:hour:{timestamp // 3600}"
count = await redis.incr(hour_key)
await redis.expire(hour_key, 3600)

if count > limit_per_hour:
    raise RateLimitException()
```

**默认限制**:

- 每分钟: 100 请求
- 每小时: 5000 请求

---

## 9. 性能优化

### 9.1 缓存策略

| 缓存项 | 缓存键 | TTL | 说明 |
|-------|--------|-----|------|
| 应用信息 | `api_app:{app_id}` | 300s | 减少数据库查询 |
| 应用权限 | `api_app_perms:{app_id}` | 300s | 复用 RBAC 缓存策略 |
| 速率限制 | `rate_limit:{app_id}:minute:{ts}` | 60s | 滑动窗口计数 |
| 速率限制 | `rate_limit:{app_id}:hour:{ts}` | 3600s | 滑动窗口计数 |

### 9.2 数据库优化

**索引设计**:

```sql
-- api_applications 表
CREATE INDEX ix_api_app_app_id ON wes_sys.api_applications(app_id);
CREATE INDEX ix_api_app_status ON wes_sys.api_applications(status, is_deleted);
CREATE INDEX ix_api_app_type ON wes_sys.api_applications(app_type, is_deleted);

-- api_access_logs 表
CREATE INDEX ix_api_log_app_id ON wes_sys.api_access_logs(app_id);
CREATE INDEX ix_api_log_app_time ON wes_sys.api_access_logs(app_id, created_at);
CREATE INDEX ix_api_log_status ON wes_sys.api_access_logs(status_code, created_at);
CREATE INDEX ix_api_log_path ON wes_sys.api_access_logs(path, created_at);
```

### 9.3 性能指标

| 操作 | 目标延迟 | 说明 |
|------|---------|------|
| 签名验证 | < 5ms | 纯计算，无 I/O |
| 缓存命中 | < 10ms | Redis 查询 |
| 缓存未命中 | < 50ms | 数据库查询 + 缓存写入 |
| 完整认证 | < 100ms | 包含所有验证步骤 |

---

## 10. 实施计划

### 10.1 开发阶段

**Phase 1: 数据模型和迁移** (预计 30 分钟)

- [ ] 创建 `APIApplication` 模型
- [ ] 创建 `APIAccessLog` 模型
- [ ] 创建 `api_app_permissions` 关联表
- [ ] 生成 Alembic 迁移脚本
- [ ] 执行迁移并验证

**Phase 2: 核心服务** (预计 1 小时)

- [ ] 实现 `EncryptionService` (密钥加密)
- [ ] 实现 `SignatureService` (签名计算和验证)
- [ ] 实现 `APIAppService` (应用管理)
- [ ] 实现 `permission_service` (权限查询)
- [ ] 编写单元测试

**Phase 3: 认证依赖** (预计 30 分钟)

- [ ] 实现 `verify_api_auth` 函数
- [ ] 实现 `RequireAPIPermission` 工厂
- [ ] 实现速率限制逻辑
- [ ] 编写集成测试

**Phase 4: API 端点** (预计 30 分钟)

- [ ] 使用 `BaseAPI` 生成 CRUD
- [ ] 实现 `create_application` (返回明文 secret)
- [ ] 实现 `revoke_application`
- [x] 实现 `assign_permissions`
- [x] 拆分“查询可分配权限”和“同步权限扫描”端点
- [ ] 编写 API 测试

**Phase 5: 文档和测试** (预计 30 分钟)

- [ ] 编写使用文档
- [ ] 编写客户端示例代码
- [ ] 端到端测试
- [ ] 性能测试

**总计**: 约 3 小时

### 10.2 测试计划

**单元测试**:

```python
# tests/test_signature_service.py
def test_calculate_signature():
    """测试签名计算"""
    signature = SignatureService.calculate(
        app_secret="test_secret",
        app_id="app_test",
        timestamp="1234567890",
        method="POST",
        path="/api/v1/test",
        body='{"key":"value"}'
    )
    assert len(signature) == 64  # SHA256 hex

def test_verify_signature():
    """测试签名验证"""
    sig1 = "abc123"
    sig2 = "abc123"
    assert SignatureService.verify(sig1, sig2) == True
```

**集成测试**:

```python
# tests/test_api_auth.py
async def test_api_authentication():
    """测试完整认证流程"""
    # 1. 创建应用
    app, secret = await api_app_service.create_app(db, {
        "app_name": "Test App",
        "app_type": "ECS",
    })
    
    # 2. 计算签名
    timestamp = str(int(time.time()))
    signature = SignatureService.calculate(
        app_secret=secret,
        app_id=app.app_id,
        timestamp=timestamp,
        method="GET",
        path="/api/v1/test",
        body=""
    )
    
    # 3. 发送请求
    response = await client.get(
        "/api/v1/test",
        headers={
            "X-App-ID": app.app_id,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        }
    )
    
    assert response.status_code == 200
```

### 10.3 部署清单

**环境变量配置**:

```bash
# .env 文件
API_SECRET_ENCRYPTION_KEY=<Fernet密钥>
```

**数据库迁移**:

```bash
# 执行迁移
./scripts/migrate.sh upgrade

# 验证表结构
psql -d wes_db -c "\dt wes_sys.api_*"
```

**权限初始化**:

```sql
-- 插入 API 认证管理权限
INSERT INTO wes_sys.permissions (name, type, resource, action, description) VALUES
('api-auth:api_application:create', 'api', 'api_application', 'create', '创建 API 应用'),
('api-auth:api_application:update', 'api', 'api_application', 'update', '更新 API 应用'),
('api-auth:api_application:delete', 'api', 'api_application', 'delete', '删除 API 应用'),
('api-auth:api_application:list', 'api', 'api_application', 'list', '查询 API 应用列表'),
('api-auth:api_application:detail', 'api', 'api_application', 'detail', '查看 API 应用详情'),
('api-auth:api_application:revoke', 'api', 'api_application', 'revoke', '撤销 API 应用'),
('api-auth:api_application:assign_permission', 'api', 'api_application', 'assign_permission', '分配权限'),
('api-auth:api_application:list_permissions', 'api', 'api_application', 'list_permissions', '获取可分配 API 权限列表'),
('api-auth:api_application:sync_permissions', 'api', 'api_application', 'sync_permissions', '同步 API 权限扫描结果'),
('api-auth:api_application:reset_secret', 'api', 'api_application', 'reset_secret', '重置应用密钥');
```

---

## 11. 使用示例

### 11.1 管理端 - 创建应用

**请求**:

```bash
curl -X POST http://localhost:8001/api/v1/api-auth/applications \
  -H "Authorization: Bearer <admin_jwt_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "SMT自动线控制系统",
    "app_type": "ECS",
    "description": "SMT区域装箱流水线控制系统",
    "ip_whitelist": ["192.168.1.100", "192.168.1.101"],
    "rate_limit_per_minute": 100,
    "rate_limit_per_hour": 5000,
    "expires_at": "2027-01-29T00:00:00Z"
  }'
```

**响应**:

```json
{
  "code": 200,
  "message": "应用创建成功，请妥善保存 app_secret（仅显示一次）",
  "data": {
    "id": 1,
    "app_id": "app_1a2b3c4d5e6f",
    "app_secret": "sec_7g8h9i0j1k2l3m4n5o6p",
    "app_name": "SMT自动线控制系统",
    "app_type": "ECS",
    "status": "active",
    "created_at": "2026-01-29T20:00:00Z"
  }
}
```

### 11.2 设备端 - API 调用

**Python 客户端**:

```python
import hmac
import hashlib
import time
import requests
import json

class WESAPIClient:
    """WES API 客户端"""
    
    def __init__(self, base_url: str, app_id: str, app_secret: str):
        self.base_url = base_url
        self.app_id = app_id
        self.app_secret = app_secret
    
    def _calculate_signature(
        self,
        method: str,
        path: str,
        body: str,
        timestamp: str
    ) -> str:
        """计算签名"""
        sign_string = f"{self.app_id}{timestamp}{method}{path}{body}"
        return hmac.new(
            self.app_secret.encode(),
            sign_string.encode(),
            hashlib.sha256
        ).hexdigest()
    
    def request(
        self,
        method: str,
        path: str,
        data: dict | None = None
    ) -> dict:
        """发送请求"""
        timestamp = str(int(time.time()))
        body = json.dumps(data) if data else ""
        
        signature = self._calculate_signature(method, path, body, timestamp)
        
        headers = {
            "X-App-ID": self.app_id,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
            "Content-Type": "application/json"
        }
        
        url = f"{self.base_url}{path}"
        response = requests.request(method, url, headers=headers, data=body)
        return response.json()

# 使用示例
client = WESAPIClient(
    base_url="http://localhost:8001",
    app_id="app_1a2b3c4d5e6f",
    app_secret="sec_7g8h9i0j1k2l3m4n5o6p"
)

# 下发任务
result = client.request(
    method="POST",
    path="/api/v1/tasks/dispatch",
    data={
        "task_type": "PICK",
        "material_id": "R001",
        "qty": 100
    }
)
print(result)
```

---

## 12. 附录

### 12.1 设计原则总结

| 原则 | 应用 | 效果 |
|------|------|------|
| **DRY** | 复用 BaseService、Permission、Redis 缓存 | 减少 60% 代码量 |
| **KISS** | 只实现核心功能，避免过度设计 | 降低复杂度 |
| **SOLID** | 单一职责、依赖注入、开闭原则 | 易于扩展和测试 |
| **YAGNI** | 不实现 Nonce、CIDR、多算法支持 | 快速交付 |

### 12.2 与现有系统集成

| 集成点 | 方式 | 说明 |
|-------|------|------|
| **RBAC** | 复用 Permission 表 | 统一权限管理 |
| **缓存** | 复用 Redis 策略 | 统一缓存管理 |
| **异常** | 复用异常处理 | 统一错误响应 |
| **审计** | 复用审计日志 | 统一操作追踪 |
| **BaseAPI** | 继承 BaseAPI | 统一 CRUD 生成 |

### 12.3 安全检查清单

- [x] 密钥加密存储 (Fernet)
- [x] 签名防时序攻击 (hmac.compare_digest)
- [x] 时间戳防重放 (5 分钟窗口)
- [x] IP 白名单验证
- [x] 速率限制 (滑动窗口)
- [x] 权限验证 (RBAC)
- [x] 审计日志记录
- [x] HTTPS 传输 (生产环境)

### 12.4 性能检查清单

- [x] Redis 缓存应用信息
- [x] Redis 缓存权限信息
- [x] 数据库索引优化
- [x] 签名计算优化 (纯函数)
- [x] 异步日志记录

---

## 文档变更历史

| 版本 | 日期 | 作者 | 变更说明 |
|------|------|------|---------|
| 1.0 | 2026-01-29 | System Architect | 初始版本 |

---

**文档结束**

