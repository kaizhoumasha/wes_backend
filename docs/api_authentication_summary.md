# API 认证系统 - 快速参考

> 完整设计文档: [api_authentication_design.md](./api_authentication_design.md)

## 核心特性

✅ **遵循设计原则**: DRY / KISS / SOLID / YAGNI  
✅ **复用现有组件**: BaseService, Permission, Redis 缓存  
✅ **双重认证支持**: JWT (用户) + API Key (设备)  
✅ **完整安全机制**: 签名验证、IP 白名单、速率限制  
✅ **性能优化**: Redis 缓存、数据库索引  

## 快速开始

### 1. 环境配置

```bash
# 生成加密密钥
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 添加到 .env
echo "API_SECRET_ENCRYPTION_KEY=<生成的密钥>" >> .env
```

### 2. 数据库迁移

```bash
./scripts/migrate.sh upgrade
```

### 3. 创建 API 应用

```bash
curl -X POST http://localhost:8001/api/v1/api-auth/applications \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "SMT自动线",
    "app_type": "ECS",
    "ip_whitelist": ["192.168.1.100"]
  }'
```

### 4. 设备端调用

```python
import hmac, hashlib, time, requests, json

class WESClient:
    def __init__(self, base_url, app_id, app_secret):
        self.base_url = base_url
        self.app_id = app_id
        self.app_secret = app_secret
    
    def request(self, method, path, data=None):
        timestamp = str(int(time.time()))
        body = json.dumps(data) if data else ""
        
        # 计算签名
        sign_string = f"{self.app_id}{timestamp}{method}{path}{body}"
        signature = hmac.new(
            self.app_secret.encode(),
            sign_string.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # 发送请求
        headers = {
            "X-App-ID": self.app_id,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
            "Content-Type": "application/json"
        }
        
        response = requests.request(
            method, 
            f"{self.base_url}{path}", 
            headers=headers, 
            data=body
        )
        return response.json()

# 使用
client = WESClient(
    "http://localhost:8001",
    "app_xxx",
    "sec_yyy"
)

result = client.request("POST", "/api/v1/tasks/dispatch", {
    "task_type": "PICK",
    "material_id": "R001"
})
```

## 文件结构

```
src/app/api_auth/
├── models/
│   ├── api_application.py      # 应用模型
│   ├── api_access_log.py       # 日志模型
│   └── relationships.py        # 关联表
├── repositories/
│   └── app_repository.py       # 应用仓储
├── services/
│   ├── app_service.py          # 应用服务
│   ├── signature_service.py    # 签名服务
│   └── permission_service.py   # 权限服务
└── v1/
    └── application.py          # API 端点

src/core/
├── api_security.py             # 认证依赖
└── encryption.py               # 加密服务
```

## 核心概念

### 签名算法

```
签名字符串 = app_id + timestamp + method + path + body
签名 = HMAC-SHA256(app_secret, 签名字符串)
```

### 认证流程

```
1. 验证时间戳 (5分钟窗口)
2. 查询应用信息 (Redis缓存)
3. 验证签名
4. 检查 IP 白名单
5. 检查速率限制
6. 获取权限
7. 执行业务逻辑
```

### 速率限制

- 每分钟: 100 请求 (默认)
- 每小时: 5000 请求 (默认)

## 实施计划

| 阶段 | 任务 | 时间 |
|------|------|------|
| Phase 1 | 数据模型和迁移 | 30 分钟 |
| Phase 2 | 核心服务 | 1 小时 |
| Phase 3 | 认证依赖 | 30 分钟 |
| Phase 4 | API 端点 | 30 分钟 |
| Phase 5 | 测试和文档 | 30 分钟 |
| **总计** | | **3 小时** |

## 安全检查清单

- [x] 密钥加密存储 (Fernet)
- [x] 签名防时序攻击
- [x] 时间戳防重放
- [x] IP 白名单
- [x] 速率限制
- [x] 权限验证
- [x] 审计日志

## 性能指标

| 操作 | 目标延迟 |
|------|---------|
| 签名验证 | < 5ms |
| 缓存命中 | < 10ms |
| 缓存未命中 | < 50ms |
| 完整认证 | < 100ms |

## 常见问题

**Q: 为什么不使用 Nonce 防重放?**  
A: 时间戳 (5分钟窗口) 已提供足够保护，遵循 KISS 原则。

**Q: 为什么不支持 CIDR IP 白名单?**  
A: 当前需求不需要，简单列表足够，遵循 YAGNI 原则。

**Q: 密钥丢失怎么办?**  
A: 撤销旧应用，创建新应用，密钥仅在创建时返回一次。

**Q: 如何与 JWT 认证共存?**  
A: 使用 `DependsAPIAuth` (可选) 或 `DependsOptionalAuth` (JWT)，两者可同时存在。

---

**详细设计**: 请查看 [api_authentication_design.md](./api_authentication_design.md) (1800+ 行完整文档)
