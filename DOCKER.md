# 🐳 Docker 部署指南

> **P9 WES Backend - 统一 docker-compose.yml + profiles + 环境变量**

---

## 📖 目录

- [快速开始](#快速开始)
- [部署模式](#部署模式)
- [环境配置](#环境配置)
- [高级用法](#高级用法)
- [故障排查](#故障排查)

---

## 🚀 快速开始

### 1 分钟启动

```bash
# 开发环境
docker-compose --profile dev up -d

# 查看状态
docker-compose ps

# 查看日志
docker-compose logs -f api

# 访问服务
open http://localhost:8001/docs  # API 文档
open http://localhost:5555       # Celery 监控
```

### 使用部署脚本

```bash
# 开发环境
./scripts/docker-deploy-simple.sh dev up

# 生产环境
./scripts/docker-deploy-simple.sh prod up

# 扩展服务
./scripts/docker-deploy-simple.sh prod up --scale api=5 --scale celery=8
```

### 停止服务

```bash
# 停止但保留数据
docker-compose --profile dev down

# 停止并删除数据卷
docker-compose --profile dev down -v
```

### 首次部署（完整流程）

**首次部署需要初始化数据库结构和初始数据**，现在已由 Alembic 统一管理：

```bash
# 方式 1: 使用便捷脚本（推荐）✨

# === 开发/测试环境 ===
./scripts/init-env.sh dev
# 自动完成：
# - 复制 .env.dev → .env（用于宿主机运行迁移）
# - 保留数据库和 Redis 密码（确保容器和宿主机密码一致）
# - 生成应用层安全密钥：JWT_SECRET_KEY, API_SECRET_ENCRYPTION_KEY
# - 备份现有 .env 文件
# - 保存密钥对照信息到 .env.new_keys

# === 生产环境（⚠️ 重要区别） ===
./scripts/init-env.sh prod
# 自动完成：
# - 复制 .env.prod → .env
# - 生成所有新的强随机密码：POSTGRES_PASSWORD, REDIS_PASSWORD
# - 生成应用层安全密钥：JWT_SECRET_KEY, API_SECRET_ENCRYPTION_KEY
# - 备份现有 .env 文件
# - 保存所有密钥到 .env.new_keys
# ⚠️ 注意：必须将新生成的数据库密码更新到 .env.prod 并重启容器！

# 方式 2: 手动执行（不推荐，请使用方式 1）
# 1. 复制环境配置到 .env（用于宿主机运行迁移）
cp .env.dev .env

# 2. 手动生成应用层安全密钥（替换 .env 中的默认值）
# JWT_SECRET_KEY: python -c "import secrets; print(secrets.token_urlsafe(32))"
# API_SECRET_ENCRYPTION_KEY: python -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"

# 3. 启动基础设施（PostgreSQL + Redis）
docker-compose --profile dev up -d db redis

# 4. 等待数据库就绪
sleep 5

# 5. 执行数据库迁移（包含：创建 schemas + 表 + 初始数据）
uv run alembic upgrade head

# 6. 启动所有服务
docker-compose --profile dev up -d

# 7. 验证服务
docker-compose ps
docker-compose logs -f api
```

**init-env.sh 脚本功能**：
- ✅ **dev/test 环境**：保留数据库和 Redis 密码（确保容器和宿主机一致）
- ✅ **prod 环境**：生成所有新的强随机密码（确保生产安全）
- ✅ 自动生成应用层安全密钥：`JWT_SECRET_KEY` (43 字符), `API_SECRET_ENCRYPTION_KEY` (Fernet 格式)
- ✅ 备份现有 `.env` 文件（带时间戳）
- ✅ 保存密钥对照信息到 `.env.new_keys`
- ✅ 跳过注释行，只保留配置项

**Alembic 迁移会自动完成**：
- ✅ 创建 PostgreSQL schemas (`wes_sys`, `wes_biz`)
- ✅ 创建所有数据库表
- ✅ 初始化权限、角色、用户数据
- ✅ 建立角色权限关联和用户角色关联

**默认测试账号**：
```
admin / admin123     (超级管理员)
manager / admin123    (管理员)
operator / admin123   (运营人员)
finance / admin123    (财务人员)
user1 / admin123      (普通用户)
user2 / admin123      (普通用户)
```

⚠️ **生产环境请立即修改默认密码！**

---

## 🎯 部署模式

### 模式对照表

| 模式 | Profile | 环境文件 | 命令 | 包含服务 |
|------|---------|---------|------|---------|
| **开发环境** | `dev` | `.env.dev` | `--profile dev up` | API + Celery + DB + Redis + Flower |
| **测试环境** | `test` | `.env.test` | `--profile test up` | API + Pytest + Locust + DB + Redis |
| **生产环境** | `prod` | `.env.prod` | `--profile prod up` | Nginx + API + Celery + DB + Redis |
| **仅 Celery** | `celery` | 任意 | `--profile celery up` | Worker + Beat + Flower + DB + Redis |
| **仅 API** | `api` | 任意 | `--profile api up` | Nginx + API + DB + Redis |
| **基础设施** | `infra` | 任意 | `--profile infra up` | 仅 DB + Redis |

### 开发环境

```bash
docker-compose --profile dev up -d
```

**特性**：
- ✅ 热重载（源码挂载到容器）
- ✅ DEBUG 日志级别
- ✅ Flower 监控（http://localhost:5555）
- ✅ 单实例部署

### 开发环境（前后端同源联调）

```bash
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.frontend.yml --profile dev up -d
```

**特性**：
- ✅ `nginx` 统一代理前端和后端，浏览器入口为 `http://localhost/`
- ✅ 前端源码来自 `FRONTEND_ROOT`，默认 `../wes_frontend`
- ✅ 前端容器支持 Vite 热更新
- ✅ `node_modules` 与 pnpm store 使用独立 volume，避免宿主与容器依赖互相污染

### 测试环境

```bash
docker-compose --profile test up -d pytest
```

**特性**：
- ✅ 自动化测试（Pytest）
- ✅ 性能测试（Locust）
- ✅ 独立测试数据库

### 测试环境（前后端同源联调）

```bash
docker compose --env-file .env.test -f docker-compose.yml -f docker-compose.frontend.yml --profile test up -d
```

**特性**：
- ✅ 浏览器入口为 `http://localhost:8080/`
- ✅ `nginx` 与 `frontend/api` 拓扑和开发环境保持一致
- ✅ 前端容器继续使用相对 `/api`，通过代理验证同源链路

### 生产环境（完整）

```bash
docker-compose --profile prod up -d
```

**特性**：
- ✅ Nginx 负载均衡
- ✅ 多实例部署（API x3, Worker x4）
- ✅ 资源限制
- ✅ 日志轮转
- ✅ 健康检查

如果需要走正式发布镜像链路，而不是在生产机本地构建镜像，请参考：

- `docs/devops/prod-release-deploy.md`

该 Runbook 基于 `docker-compose.yml + docker-compose.deploy.yml` 组合执行，适用于手动拉取或离线导入 CI 产物镜像后发布。

### 分离部署（推荐）

**场景**：将 Celery 和 FastAPI 部署到不同服务器

```bash
# 服务器 A：数据库 + Redis
docker-compose --profile infra up -d

# 服务器 B：Celery（异步任务）
docker-compose --profile celery up -d --scale celery=4

# 服务器 C：FastAPI（API 服务）
docker-compose --profile api up -d --scale api=3
```

---

## ⚙️ 环境配置

### 配置文件结构

```
wes_backend/
├── docker-compose.yml          # 统一配置（使用 profiles）
├── .env.dev                    # 开发环境变量
├── .env.test                   # 测试环境变量
└── .env.prod                   # 生产环境变量
```

### 环境变量说明

| 变量 | 开发 | 测试 | 生产 | 说明 |
|------|------|------|------|------|
| `ENV` | `dev` | `test` | `prod` | 环境标识 |
| `BUILD_TARGET` | `development` | `testing` | `production` | Docker 构建阶段 |
| `APP_DEBUG` | `true` | `false` | `false` | DEBUG 模式 |
| `API_REPLICAS` | `1` | `1` | `3` | API 实例数 |
| `CELERY_WORKER_REPLICAS` | `1` | `1` | `4` | Celery Worker 实例数 |
| `LOG_LEVEL` | `DEBUG` | `INFO` | `INFO` | 日志级别 |
| `SOURCE_MOUNT` | `./src` | (空) | (空) | 源码挂载（热重载） |
| `LOG_JSON_OUTPUT` | `false` | `false` | `true` | JSON 格式日志 |

### .env.dev 示例

```bash
# 环境标识
ENV=dev
BUILD_TARGET=development

# 调试
APP_DEBUG=true
LOG_LEVEL=DEBUG

# 实例数量
API_REPLICAS=1
CELERY_WORKER_REPLICAS=1

# 热重载
SOURCE_MOUNT=./src

# 数据持久化
POSTGRES_HOST=db
POSTGRES_PORT=5432
REDIS_HOST=redis
REDIS_PORT=6379
```

### .env.prod 示例

```bash
# 环境标识
ENV=prod
BUILD_TARGET=production

# 调试
APP_DEBUG=false
LOG_LEVEL=INFO
LOG_JSON_OUTPUT=true

# 实例数量
API_REPLICAS=3
CELERY_WORKER_REPLICAS=4

# 数据持久化
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
REDIS_HOST=redis
REDIS_PORT=6379
```

### 安全密钥管理

项目使用环境差异化的密钥管理策略：

#### 密钥生成策略对照表

| 环境 | 数据库密码 | Redis 密码 | JWT 密钥 | 加密密钥 | 说明 |
|------|-----------|------------|---------|---------|------|
| **dev** | 固定强密码 | 固定强密码 | ✅ 自动生成 | ✅ 自动生成 | 开发环境，使用默认密码 |
| **test** | 固定强密码 | 固定强密码 | ✅ 自动生成 | ✅ 自动生成 | 测试环境，使用默认密码 |
| **prod** | ✅ **自动生成** | ✅ **自动生成** | ✅ 自动生成 | ✅ 自动生成 | 生产环境，所有密码自动生成 |

#### 1. 基础设施层密钥（数据库和 Redis）

**dev/test 环境**：
- 使用 `.env.dev/.env.test` 中配置的固定强密码
- 容器和宿主机使用相同密码（确保连接正常）
- 无需手动管理，开箱即用

**prod 环境**（⚠️ 重要）：
- `init-env.sh prod` 会自动生成新的强随机密码
- 生成的密码保存在 `.env`（宿主机使用）
- **必须手动将新密码更新到 `.env.prod`**（Docker 容器使用）
- 更新后重启容器：`docker-compose --profile prod up -d`

```bash
# 生产环境完整流程
./scripts/init-env.sh prod

# 查看生成的密码
cat .env.new_keys

# 将新密码更新到 .env.prod（重要！）
# POSTGRES_PASSWORD=<新生成的密码>
# REDIS_PASSWORD=<新生成的密码>

# 启动容器
docker-compose --profile prod up -d
```

#### 2. 应用层密钥（JWT 和加密）

| 密钥 | 用途 | 长度要求 | 生成方法 |
|------|------|----------|----------|
| `JWT_SECRET_KEY` | JWT Token 签名 | ≥ 32 字符 | `secrets.token_urlsafe(32)` |
| `API_SECRET_ENCRYPTION_KEY` | 敏感数据加密（Fernet） | 44 字符（base64） | `base64.urlsafe_b64encode(secrets.token_bytes(32))` |

**自动生成应用层密钥**（推荐）：

```bash
# dev/test 环境：只生成应用层密钥
./scripts/init-env.sh dev

# prod 环境：生成所有密钥和密码
./scripts/init-env.sh prod

# 查看生成的密钥
cat .env.new_keys
```

**手动生成应用层密钥**（不推荐）：

```bash
# JWT 密钥（43 字符）
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Fernet 加密密钥（44 字符，base64 URL-safe 编码）
python3 -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

**安全注意事项**：

- ✅ **不要将密钥提交到版本控制**：`.env*` 文件已在 `.gitignore` 中
- ✅ **生产环境必须使用强随机密钥**：不要使用 `dev_*` 或 `test_*` 等默认值
- ✅ **定期轮换密钥**：建议每 90-180 天更换一次
- ✅ **妥善保管 `.env.new_keys`**：记录了生成的应用层密钥
- ✅ **密钥泄露后立即更换**：并强制所有用户重新登录

---

## 🔧 高级用法

### 动态扩展服务

```bash
# 扩展 API 到 5 个实例
docker-compose --profile prod up -d --scale api=5

# 扩展 Celery Worker 到 8 个实例
docker-compose --profile celery up -d --scale celery=8

# 组合扩展
docker-compose --profile prod up -d --scale api=3 --scale celery=6
```

### 灵活组合 profiles

```bash
# 基础设施 + Celery
docker-compose --profile infra --profile celery up -d

# 基础设施 + API
docker-compose --profile infra --profile api up -d

# API + 性能测试
docker-compose --profile dev --profile test up -d
```

### 查看服务日志

```bash
# 查看所有服务日志
docker-compose logs -f

# 查看特定服务
docker-compose logs -f api
docker-compose logs -f celery
docker-compose logs -f celery_beat

# 查看最近 100 行
docker-compose logs --tail=100 api
```

### 进入容器调试

```bash
# 进入 API 容器
docker exec -it wes_api_prod bash

# 进入 Celery Worker 容器
docker exec -it wes_celery_prod_1 bash

# 进入数据库
docker exec -it wes_postgres_prod psql -U wesuser -d wesdb
```

### 执行命令

```bash
# 运行测试
docker-compose --profile test run pytest pytest

# 运行数据库迁移
docker-compose --profile dev run api alembic upgrade head

# 执行 Celery 任务
docker-compose --profile dev exec celery celery -A src.core.celery_app call src.app.warehousing.tasks.process_inbound
```

### 数据备份

```bash
# 备份 PostgreSQL
docker exec wes_postgres_prod pg_dump -U wesuser wesdb > backup.sql

# 恢复 PostgreSQL
docker exec -i wes_postgres_prod psql -U wesuser wesdb < backup.sql

# 备份 Redis
docker exec wes_redis_prod redis-cli SAVE
docker cp wes_redis_prod:/data/dump.rdb ./redis_backup.rdb
```

---

## 🔍 故障排查

### 服务无法启动

**检查日志**：
```bash
docker-compose logs api
docker-compose logs celery
```

**常见问题**：
1. **端口被占用**：修改 `.env` 中的端口配置
2. **数据库连接失败**：确保 `db` 和 `redis` 服务已启动
3. **权限问题**：检查数据卷目录权限

### 容器不断重启

**查看健康状态**：
```bash
docker-compose ps
docker inspect wes_api_prod | grep -A 10 Health
```

**常见原因**：
1. **数据库未就绪**：等待 `db` 服务健康检查通过
2. **依赖服务缺失**：检查 `depends_on` 配置
3. **内存不足**：调整 `deploy.resources.limits`

### 性能问题

**查看资源使用**：
```bash
docker stats
```

**优化建议**：
1. **增加实例数**：`--scale api=5`
2. **调整资源限制**：修改 `deploy.resources`
3. **启用缓存**：确保 Redis 配置正确

### 网络问题

**检查网络**：
```bash
docker network ls
docker network inspect wesp9-prod-network
```

**常见问题**：
1. **容器间无法通信**：确保在同一个网络
2. **DNS 解析失败**：使用服务名而非容器名

### 数据丢失

**检查数据卷**：
```bash
docker volume ls
docker volume inspect wes_backend_postgres_data
```

**预防措施**：
1. **定期备份**：使用 `pg_dump` 备份数据库
2. **使用命名卷**：避免使用匿名卷
3. **不要使用 `down -v`**：会删除所有数据卷

---

## 📚 最佳实践

### 开发环境

- ✅ 使用热重载提高开发效率
- ✅ DEBUG 日志级别便于调试
- ✅ 使用 Flower 监控 Celery 任务
- ✅ 单实例部署节省资源

### 生产环境

- ✅ 使用 Nginx 负载均衡
- ✅ 多实例部署提高可用性
- ✅ 启用资源限制防止单个容器耗尽资源
- ✅ JSON 格式日志便于日志收集
- ✅ 定期备份数据库
- ✅ 使用健康检查自动重启异常容器

### 安全建议

- ✅ 不要在 `.env` 文件中存储敏感信息（使用 Secrets 管理）
- ✅ 生产环境关闭 DEBUG 模式
- ✅ 使用非 root 用户运行容器
- ✅ 限制容器资源
- ✅ 定期更新镜像

---

## 🎉 总结

### 为什么选择统一配置？

| 方面 | 旧方案（多文件） | 新方案（统一配置） |
|------|-----------------|-------------------|
| 配置文件数量 | 5 个 | 1 个 |
| 配置代码行数 | ~800 行 | ~475 行 |
| 维护成本 | 高 | 低 |
| 灵活性 | 低 | 高 |
| 符合最佳实践 | 否 | 是 ✅ |

### 快速命令参考

```bash
# 开发
docker-compose --profile dev up -d

# 生产
docker-compose --profile prod up -d

# 扩展
docker-compose --profile prod up -d --scale api=5 --scale celery=8

# 查看状态
docker-compose ps

# 查看日志
docker-compose logs -f api

# 停止
docker-compose --profile dev down
```

---

**版本**: 2.0.0 | **更新时间**: 2026-02-04
