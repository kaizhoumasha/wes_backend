# CI/CD 完整配置指南

## 📖 文档说明

本文档整合了 CI/CD 配置的所有内容，包括快速开始、详细步骤、故障排查和最佳实践。

## 🎯 执行位置说明

- 🖥️ **本地机器**：你的开发电脑（当前操作机）
- 🌐 **GitLab UI**：浏览器访问 GitLab 网站
- 🔧 **220 服务器**：192.168.0.220（DevOps 环境：GitLab + Jenkins + GitLab Runner）
- 🧪 **221 服务器**：192.168.0.221（测试环境：自动部署目标）

---

## 📋 目录

- [快速开始（5 分钟）](#快速开始5-分钟)
- [架构概述](#架构概述)
- [详细执行步骤](#详细执行步骤)
- [验证清单](#验证清单)
- [故障排查](#故障排查)
- [最佳实践](#最佳实践)

---

## 🚀 快速开始（5 分钟）

### 前置条件检查

```bash
# 1. 检查服务器连接
ssh root@192.168.0.220  # DevOps 服务器 (GitLab + Jenkins + Runner)
ssh root@192.168.0.221  # 测试服务器 (自动部署目标)

# 2. 确认 Docker 已安装
ssh root@192.168.0.220 "docker --version"
ssh root@192.168.0.221 "docker --version"
```

### 步骤 1: 生成 SSH 密钥（2 分钟）
**位置**: 🖥️ 本地机器

```bash
# 生成测试环境密钥
ssh-keygen -t rsa -b 4096 -f ~/.ssh/gitlab_test_rsa_221 -N "" -C "gitlab-ci-test"

# 复制公钥到测试服务器
ssh-copy-id -i ~/.ssh/gitlab_test_rsa_221.pub root@192.168.0.221
```

**验证**:
```bash
# 测试免密登录
ssh -i ~/.ssh/gitlab_test_rsa_221 root@192.168.0.221 "echo '测试环境连接成功'"
```

### 步骤 2: 在测试服务器上安装 Docker（2 分钟）
**位置**: 🧪 221 服务器

```bash
# SSH 登录到测试服务器
ssh root@192.168.0.221

# 使用 Docker 官方安装脚本（支持多种 Linux 发行版）
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
rm get-docker.sh

# 启动 Docker 服务
systemctl enable docker
systemctl start docker

# 验证安装
docker --version
docker compose version
```

**预期结果**: 显示 Docker 和 Docker Compose 版本号

### 步骤 3: 创建项目目录（1 分钟）
**位置**: 🧪 221 服务器

```bash
# 创建项目目录结构
mkdir -p /opt/wes_backend/{logs,backups,docker_data,reports}

# 设置权限
chmod -R 755 /opt/wes_backend

# 验证目录创建
ls -la /opt/wes_backend/
```

**预期结果**: 看到创建的目录结构（logs, backups, docker_data, reports）

### 步骤 4: 复制项目文件到服务器（1 分钟）
**位置**: 🖥️ 本地机器

```bash
# 复制 docker-compose.yml 和 scripts 目录
scp docker-compose.yml root@192.168.0.221:/opt/wes_backend/
scp -r scripts/ root@192.168.0.221:/opt/wes_backend/

# 复制测试环境配置文件
scp .env.test root@192.168.0.221:/opt/wes_backend/
```

**预期结果**: 文件成功复制到服务器

**验证**:
```bash
ssh root@192.168.0.221 "ls -la /opt/wes_backend/"
```

### 步骤 5: 配置环境变量文件（2 分钟）
**位置**: 🧪 221 服务器

```bash
# SSH 登录到测试服务器
ssh root@192.168.0.221
cd /opt/wes_backend

# 编辑测试环境配置文件
nano .env.test
```

**配置内容**（根据实际情况修改）:
```bash
# 应用配置
ENVIRONMENT=test
APP_PORT=8001

# 数据库配置
POSTGRES_HOST=wes_postgres
POSTGRES_PORT=5432
POSTGRES_DB=wes_test
POSTGRES_USER=wes_user
POSTGRES_PASSWORD=your_secure_password_here  # 修改为强密码

# Redis 配置
REDIS_HOST=wes_redis
REDIS_PORT=6379
REDIS_PASSWORD=your_redis_password_here  # 修改为强密码

# JWT 配置
JWT_SECRET_KEY=your_jwt_secret_key_here  # 修改为随机字符串（至少 32 字符）
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# 时区
DATETIME_TIMEZONE=Asia/Shanghai
```

**保存并退出**: `Ctrl+X` → `Y` → `Enter`

**验证**:
```bash
cat .env.test | grep -v PASSWORD | grep -v SECRET
```

### 步骤 6: 配置 GitLab CI/CD Variables（1 分钟）
**位置**: 🌐 GitLab UI

1. **打开 GitLab 项目**：Settings → CI/CD → Variables

2. **添加测试环境私钥**：
   ```bash
   # 在本地机器复制私钥内容
   cat ~/.ssh/gitlab_test_rsa_221
   ```
   - Key: `SSH_PRIVATE_KEY_TEST`
   - Value: 粘贴完整私钥内容（包括 BEGIN 和 END 行）
   - Type: **File**（重要！）
   - Flags: ✅ Protect variable, ✅ Mask variable

### 步骤 4: 测试部署
**位置**: 🖥️ 本地机器

```bash
# 推送代码到 develop 分支触发测试环境部署
git checkout develop
git push origin develop
```

**位置**: 🌐 GitLab UI

查看 Pipeline 执行：项目 → CI/CD → Pipelines

**验证部署**:
```bash
# 检查健康状态
curl http://192.168.0.221:8001/api/health

# 或在浏览器访问
open http://192.168.0.221:8001/docs
```

**预期结果**: 健康检查返回 `{"status": "healthy"}`

---

## 🏗️ 架构概述

### CI/CD 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                     CI/CD 架构图                              │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐ │
│  │   GitLab    │─────▶│ GitLab CI   │─────▶│  Registry   │ │
│  │  (代码仓库)  │      │   (构建)     │      │ (镜像仓库)   │ │
│  └─────────────┘      └─────────────┘      └─────────────┘ │
│                                │                             │
│                                ▼                             │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐ │
│  │ 220 Server  │      │ GitLab CI   │─────▶│ 221 Server  │ │
│  │ (DevOps环境) │      │   (部署)     │      │ (测试环境)   │ │
│  │             │      │             │      │             │ │
│  │ • GitLab    │      │             │      │ • Test App  │ │
│  │ • Jenkins   │      │             │      │ • Docker    │ │
│  │ • Runner    │      │             │      │             │ │
│  └─────────────┘      └─────────────┘      └─────────────┘ │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### Pipeline 阶段说明

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ Prepare  │──▶│   Lint   │──▶│   Test   │──▶│  Build   │──▶│  Deploy  │
│ 依赖安装  │   │ 代码检查  │   │ 单元测试  │   │ Docker   │   │ 自动部署  │
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
   ~1 分钟        ~2 分钟        ~3 分钟        ~5 分钟        ~2 分钟
```

### 分支策略

| 分支 | 用途 | CI/CD 触发 | 部署目标 |
|------|------|------------|------------|
| `feature/*` | 功能开发 | 仅测试 | 无 |
| `develop` | 开发集成 | 构建 + 部署 | 221 (测试环境) |
| `main` | 生产发布 | 仅构建 | 无（生产环境不在 CI/CD 范围内） |

### 服务器要求

| 服务器 | IP | 用途 | 需要的软件 |
|--------|-----|------|------------|
| **DevOps 环境** | 192.168.0.220 | GitLab + Jenkins + Runner | Docker, GitLab, GitLab Runner |
| **测试环境** | 192.168.0.221 | 自动部署目标 | Docker, Docker Compose |

---

## 📝 详细执行步骤

### 阶段 1: 准备工作

#### 步骤 1.1: 检查服务器连接
**位置**: 🖥️ 本地机器

```bash
# 测试 SSH 连接
ssh root@192.168.0.220
exit

ssh root@192.168.0.221
exit
```

**预期结果**: 能够成功登录两台服务器

**如果失败**:
- 检查网络连接
- 确认 SSH 服务已启动：`systemctl status sshd`
- 检查防火墙设置：`ufw status`

#### 步骤 1.2: 生成 SSH 密钥对
**位置**: 🖥️ 本地机器

```bash
# 生成测试环境密钥
ssh-keygen -t rsa -b 4096 -f ~/.ssh/gitlab_test_rsa_221 -N "" -C "gitlab-ci-test"
```

**预期结果**: 生成 2 个文件
- `~/.ssh/gitlab_test_rsa_221`（测试环境私钥）
- `~/.ssh/gitlab_test_rsa_221.pub`（测试环境公钥）

**验证**:
```bash
ls -la ~/.ssh/gitlab_test*
```

#### 步骤 1.3: 复制公钥到服务器
**位置**: 🖥️ 本地机器

```bash
# 复制测试环境公钥到 221
ssh-copy-id -i ~/.ssh/gitlab_test_rsa_221.pub root@192.168.0.221
```

**预期结果**: 提示 "Number of key(s) added: 1"

**验证**:
```bash
# 测试免密登录
ssh -i ~/.ssh/gitlab_test_rsa_221 root@192.168.0.221 "echo '测试环境连接成功'"
```

### 阶段 2: 初始化服务器

#### 步骤 2.1: 运行初始化脚本
**位置**: 🖥️ 本地机器

```bash
# 确保脚本有执行权限
chmod +x scripts/init-deploy-servers.sh

# 初始化测试服务器
./scripts/init-deploy-servers.sh test
```

**这个脚本会做什么**:
1. 检查 SSH 连接
2. 安装 Docker 和 Docker Compose（如果未安装）
3. 创建项目目录 `/opt/wes_backend`
4. 创建子目录：logs, backups, docker_data, reports
5. 设置正确的权限

**预期结果**: 看到 "✅ test 环境初始化完成"

**如果失败**: 查看错误信息，可能需要手动在服务器上安装 Docker

#### 步骤 2.2: 复制项目文件到服务器
**位置**: 🖥️ 本地机器

```bash
# 复制到测试服务器 (221)
scp -r docker-compose.yml scripts/ .env.test root@192.168.0.221:/opt/wes_backend/
```

**预期结果**: 文件成功复制到服务器

**验证**:
```bash
# 检查测试服务器
ssh root@192.168.0.221 "ls -la /opt/wes_backend/"
```

#### 步骤 2.3: 配置环境变量文件
**位置**: 🧪 221 服务器

**在 221 测试服务器上**:
```bash
ssh root@192.168.0.221
cd /opt/wes_backend
nano .env.test
```

**编辑 `.env.test` 文件，确保以下配置正确**:
```bash
# 应用配置
ENVIRONMENT=test
APP_PORT=8001

# 数据库配置
POSTGRES_HOST=wes_postgres
POSTGRES_PORT=5432
POSTGRES_DB=wes_test
POSTGRES_USER=wes_user
POSTGRES_PASSWORD=your_secure_password_here  # 修改为强密码

# Redis 配置
REDIS_HOST=wes_redis
REDIS_PORT=6379
REDIS_PASSWORD=your_redis_password_here  # 修改为强密码

# JWT 配置
JWT_SECRET_KEY=your_jwt_secret_key_here  # 修改为随机字符串
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# 时区
DATETIME_TIMEZONE=Asia/Shanghai
```

**保存并退出**: `Ctrl+X` → `Y` → `Enter`

### 阶段 3: 配置 GitLab CI/CD

#### 步骤 3.1: 获取 SSH 私钥内容
**位置**: 🖥️ 本地机器

```bash
# 查看测试环境私钥
cat ~/.ssh/gitlab_test_rsa_221
```

**重要**: 复制完整的私钥内容（包括 `-----BEGIN` 和 `-----END` 行）

#### 步骤 3.2: 在 GitLab 中配置 CI/CD Variables
**位置**: 🌐 GitLab UI

1. **打开 GitLab 项目**
   - 浏览器访问你的 GitLab 项目

2. **进入 CI/CD 设置**
   - 点击左侧菜单：**Settings** → **CI/CD**
   - 展开 **Variables** 部分

3. **添加测试环境私钥**
   - 点击 **Add variable**
   - **Key**: `SSH_PRIVATE_KEY_TEST`
   - **Value**: 粘贴 `~/.ssh/gitlab_test_rsa_221` 的完整内容
   - **Type**: 选择 **File**（重要！）
   - **Flags**:
     - ✅ 勾选 **Protect variable**
     - ✅ 勾选 **Mask variable**
   - 点击 **Add variable**

**验证**: 应该看到一个变量，类型是 **File**，有 🔒 和 👁️ 图标

#### 步骤 3.3: 配置受保护分支
**位置**: 🌐 GitLab UI

1. **进入分支保护设置**
   - 左侧菜单：**Settings** → **Repository**
   - 展开 **Protected branches**

2. **保护 main 分支**
   - Branch: `main`
   - Allowed to merge: **Maintainers**
   - Allowed to push: **Maintainers**
   - 点击 **Protect**

3. **保护 develop 分支**
   - Branch: `develop`
   - Allowed to merge: **Developers + Maintainers**
   - Allowed to push: **Developers + Maintainers**
   - 点击 **Protect**

### 阶段 4: 配置 GitLab Runner

#### 步骤 4.1: 安装 GitLab Runner
**位置**: 🔧 220 服务器

```bash
ssh root@192.168.0.220

# 添加 GitLab Runner 仓库
curl -L https://packages.gitlab.com/install/repositories/runner/gitlab-runner/script.deb.sh | sudo bash

# 安装 GitLab Runner
apt-get install gitlab-runner

# 验证安装
gitlab-runner --version
```

**预期结果**: 显示 GitLab Runner 版本号

#### 步骤 4.2: 获取 Runner 注册令牌
**位置**: 🌐 GitLab UI

1. **进入 Runner 设置**
   - 项目页面：**Settings** → **CI/CD**
   - 展开 **Runners** 部分

2. **复制注册令牌**
   - 找到 "Set up a specific runner manually" 部分
   - 复制 **registration token**（类似 `GR1348941...`）

#### 步骤 4.3: 注册 GitLab Runner
**位置**: 🔧 220 服务器

```bash
# 注册 Runner（交互式）
sudo gitlab-runner register

# 按提示输入以下信息：
# GitLab instance URL: https://your-gitlab-domain.com
# Registration token: [粘贴刚才复制的 token]
# Description: Docker Runner for WES Backend
# Tags: docker
# Executor: docker
# Default Docker image: docker:27-dind
```

**或使用非交互式命令**:
```bash
sudo gitlab-runner register \
  --non-interactive \
  --url "https://your-gitlab-domain.com" \
  --registration-token "YOUR_REGISTRATION_TOKEN" \
  --executor "docker" \
  --docker-image "docker:27-dind" \
  --description "Docker Runner for WES Backend" \
  --tag-list "docker" \
  --docker-privileged \
  --run-untagged="false" \
  --locked="false"
```

**启动 Runner**:
```bash
systemctl enable gitlab-runner
systemctl start gitlab-runner
systemctl status gitlab-runner
```

**预期结果**: 显示 "active (running)"

#### 步骤 4.4: 验证 Runner 注册
**位置**: 🌐 GitLab UI

1. **刷新 Runner 页面**
   - 项目页面：**Settings** → **CI/CD** → **Runners**

2. **检查 Runner 状态**
   - 应该看到一个绿色的 Runner
   - 标签显示 "docker"
   - 状态显示 "online"

### 阶段 5: 测试部署

#### 步骤 5.1: 推送代码触发 Pipeline
**位置**: 🖥️ 本地机器

```bash
# 确保在项目目录
cd /Users/kaizhou/SynologyDrive/works/wes_backend

# 检查当前分支
git branch

# 切换到 develop 分支（如果不在）
git checkout develop

# 推送代码
git push origin develop
```

**预期结果**: 触发 GitLab CI/CD Pipeline

#### 步骤 5.2: 监控 Pipeline 执行
**位置**: 🌐 GitLab UI

1. **查看 Pipeline**
   - 项目页面：**CI/CD** → **Pipelines**
   - 点击最新的 Pipeline

2. **检查各阶段状态**
   - ✅ prepare: 依赖安装
   - ✅ lint: 代码检查
   - ✅ test: 单元测试
   - ✅ build: Docker 镜像构建
   - ✅ deploy: 自动部署到 221

3. **查看日志**
   - 点击任意 job 查看详细日志
   - 如果失败，查看错误信息

#### 步骤 5.3: 验证测试环境部署
**位置**: 🖥️ 本地机器

```bash
# 检查健康状态
curl http://192.168.0.221:8001/api/health

# 或在浏览器访问
open http://192.168.0.221:8001/docs
```

**预期结果**:
- 健康检查返回 `{"status": "healthy"}`
- API 文档页面正常显示

#### 步骤 5.4: 检查容器状态
**位置**: 🧪 221 服务器

```bash
ssh root@192.168.0.221
cd /opt/wes_backend

# 查看容器状态
docker compose ps

# 查看日志
docker compose logs -f --tail=100 api
```

**预期结果**: 所有容器状态为 "Up"

---

## ✅ 验证清单

完成所有步骤后，请验证：

### 本地机器
- [ ] SSH 密钥已生成（2 个文件）
- [ ] 可以免密登录测试服务器
- [ ] 项目文件已复制到服务器

### GitLab 配置
- [ ] CI/CD Variables 已配置（SSH_PRIVATE_KEY_TEST）
- [ ] 受保护分支已配置（main, develop）
- [ ] GitLab Runner 显示 online

### 服务器配置
- [ ] 221: Docker 已安装并运行
- [ ] 221: 项目目录已创建
- [ ] 221: 环境变量文件已配置
- [ ] 220: GitLab Runner 已注册并运行

### 部署验证
- [ ] develop 分支推送触发 Pipeline
- [ ] Pipeline 所有阶段通过
- [ ] 221 测试环境健康检查通过
- [ ] 221 容器正常运行

---

## 🚨 故障排查

### 常见问题

#### 问题 1: SSH 连接失败

**症状**:
```
Permission denied (publickey,password)
```

**解决方案**:
```bash
# 检查本地 SSH 密钥
ls -la ~/.ssh/gitlab_test*

# 检查服务器 authorized_keys
ssh root@192.168.0.221 "cat ~/.ssh/authorized_keys"

# 测试 SSH 连接（详细模式）
ssh -i ~/.ssh/gitlab_test_rsa_221 -v root@192.168.0.221

# 重新添加公钥
ssh-copy-id -i ~/.ssh/gitlab_test_rsa_221.pub root@192.168.0.221

# 检查 SSH 服务
ssh root@192.168.0.221 "systemctl status sshd"

# 检查防火墙
ssh root@192.168.0.221 "ufw status"
```

#### 问题 2: Docker 未安装

**症状**:
```
docker: command not found
```

**解决方案**:
```bash
# 手动安装 Docker
ssh root@192.168.0.221
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# 验证安装
docker --version
docker compose version
```

#### 问题 3: Pipeline 失败

**症状**: GitLab Pipeline 显示红色失败状态

**解决方案**:
```bash
# 1. 查看 GitLab Pipeline 日志
# 项目 → CI/CD → Pipelines → 点击失败的 job

# 2. 检查 GitLab Runner 状态
ssh root@192.168.0.220 "gitlab-runner verify"
ssh root@192.168.0.220 "systemctl status gitlab-runner"

# 3. 查看 Runner 日志
ssh root@192.168.0.220 "journalctl -u gitlab-runner -f"

# 4. 验证 CI/CD Variables 配置
# GitLab UI: Settings → CI/CD → Variables
```

#### 问题 4: Docker 镜像拉取失败

**症状**:
```
Error: image not found
```

**解决方案**:
```bash
# 检查 GitLab Registry 登录
docker login registry.gitlab.example.com

# 检查镜像是否存在
docker images | grep wes-backend

# 在服务器上手动拉取
ssh root@192.168.0.221
docker pull registry.gitlab.example.com/group/wes-backend:latest
```

#### 问题 5: 健康检查失败

**症状**:
```
curl: (7) Failed to connect to localhost port 8001
```

**解决方案**:
```bash
# 检查容器状态
ssh root@192.168.0.221
cd /opt/wes_backend
docker compose ps

# 查看容器日志
docker compose logs api

# 手动运行健康检查
curl http://localhost:8001/api/health

# 检查防火墙
ufw status
ufw allow 8001/tcp

# 检查端口占用
netstat -tulpn | grep 8001
lsof -i :8001
```

#### 问题 6: 数据库迁移失败

**症状**:
```
alembic.util.exc.CommandError: Target database is not up to date
```

**解决方案**:
```bash
# 查看当前迁移状态
docker compose exec api alembic current

# 查看迁移历史
docker compose exec api alembic history

# 手动运行迁移
docker compose exec api alembic upgrade head

# 回滚迁移
docker compose exec api alembic downgrade -1
```

#### 问题 7: 端口冲突

**症状**:
```
Error: bind: address already in use
```

**解决方案**:
```bash
# 查看端口占用
netstat -tulpn | grep 8001
lsof -i :8001

# 停止占用端口的容器
docker stop $(docker ps -q -f publish=8001)

# 修改端口
# 在 .env 文件中修改 APP_PORT=8002
```

### 调试技巧

#### 启用调试日志

```yaml
# 在 .gitlab-ci.yml 中添加
variables:
  CI_DEBUG_TRACE: "true"
```

#### 查看详细日志

```bash
# GitLab Runner 日志
ssh root@192.168.0.220 "journalctl -u gitlab-runner -f"

# Docker 日志
ssh root@192.168.0.221 "cd /opt/wes_backend && docker compose logs -f --tail=100 api"

# 系统日志
ssh root@192.168.0.221 "tail -f /var/log/syslog"
```

#### 手动运行 Pipeline 任务

```bash
# 使用 Docker 模拟 CI 环境
docker run -it --rm \
  -v $(pwd):/code \
  -w /code \
  ghcr.io/astral-sh/uv:python3.13-bookworm-slim \
  bash

# 在容器内运行测试
uv sync
uv run pytest
```

---

## 🛠️ 常用命令

### 部署流程

#### 测试环境（自动部署）

```bash
# 1. 创建功能分支
git checkout -b feature/new-feature

# 2. 开发并提交
git add .
git commit -m "feat: 添加新功能"

# 3. 合并到 develop 并推送
git checkout develop
git merge feature/new-feature
git push origin develop

# ✅ GitLab CI 自动部署到 221 测试服务器
```

### 查看部署状态

```bash
# 测试环境
ssh root@192.168.0.221 "cd /opt/wes_backend && docker compose ps"
```

### 查看日志

```bash
# 测试环境
ssh root@192.168.0.221 "cd /opt/wes_backend && docker compose logs -f --tail=100 api"
```

### 手动重启服务

```bash
# 测试环境
ssh root@192.168.0.221 "cd /opt/wes_backend && docker compose restart api"
```

### 手动部署命令

```bash
# 测试环境 (221)
ssh root@192.168.0.221
cd /opt/wes_backend
docker compose --env-file .env.test --profile test pull
docker compose --env-file .env.test --profile test up -d
docker compose --env-file .env.test exec -T api alembic upgrade head
```

---

## 💡 最佳实践

### 分支策略

1. **功能开发**：
   - 从 `develop` 创建 `feature/*` 分支
   - 开发完成后合并回 `develop`
   - 删除已合并的功能分支

2. **测试环境**：
   - `develop` 分支自动部署到 221
   - 每次推送都触发完整的 CI/CD 流程
   - 用于集成测试和功能验证

3. **生产环境**：
   - 生产环境不在 CI/CD 自动部署范围内
   - 需要手动部署到生产服务器
   - 只部署经过充分测试的代码

### 部署频率

| 环境 | 频率 | 触发方式 | 建议 |
|------|------|----------|------|
| 测试环境 | 每次推送 | 自动 | 频繁部署，快速验证 |
| 生产环境 | 按需部署 | 手动 | 充分测试后再部署 |

### 回滚策略

1. **Docker 镜像管理**：
   ```bash
   # 保留最近 10 个镜像
   docker images | grep wes-backend | tail -n +11 | awk '{print $3}' | xargs docker rmi
   ```

2. **数据库备份**：
   ```bash
   # 手动备份
   docker compose exec postgres pg_dump -U wes_user wes_test > backup.sql
   ```

3. **快速回滚**：
   ```bash
   # 回滚到上一个版本
   docker compose down
   docker compose up -d --force-recreate
   docker compose exec api alembic downgrade -1
   ```

### 监控和维护

#### 日常维护

```bash
# 清理未使用的 Docker 资源
docker system prune -a --volumes

# 清理 GitLab CI 缓存
# 在 GitLab UI: 项目 → Settings → CI/CD → Pipelines → 清理缓存

# 更新基础镜像
docker pull postgres:17-alpine
docker pull redis:8-alpine
```

#### 监控指标

```bash
# 检查磁盘使用
df -h

# 检查容器资源使用
docker stats

# 检查日志大小
du -sh /opt/wes_backend/logs/*

# 检查数据库大小
docker compose exec postgres psql -U wes_user -d wes_test -c "SELECT pg_size_pretty(pg_database_size('wes_test'));"
```

#### 定期检查

| 检查项 | 频率 | 命令 |
|--------|------|------|
| Pipeline 状态 | 每天 | GitLab UI → Pipelines |
| 服务器资源 | 每周 | `df -h`, `docker stats` |
| 日志大小 | 每周 | `du -sh /opt/wes_backend/logs/*` |
| 备份验证 | 每月 | 恢复测试备份 |

### 安全建议

1. **SSH 密钥管理**：
   - 定期更换 SSH 密钥（3-6 个月）
   - 使用强密码保护私钥
   - 不要将私钥提交到代码仓库
   - 限制密钥访问权限：`chmod 600 ~/.ssh/gitlab_test_rsa_221`

2. **环境变量**：
   - 敏感信息使用 GitLab CI/CD Variables
   - 启用 Protected 和 Masked 选项
   - 定期审查变量配置
   - 测试和生产使用不同的密码

3. **服务器安全**：
   - 启用防火墙（ufw）
   - 定期更新系统和 Docker
   - 使用非 root 用户部署（推荐）
   - 限制 SSH 访问（禁用密码登录）

4. **备份策略**：
   - 定期备份数据库和配置
   - 测试备份恢复流程
   - 异地存储备份文件

### 性能优化

1. **Docker 镜像优化**：
   - 使用多阶段构建
   - 清理不必要的文件
   - 使用 Alpine 基础镜像
   - 合理设置镜像层缓存

2. **Pipeline 优化**：
   - 使用缓存加速依赖安装
   - 并行运行独立任务
   - 只在必要时运行完整测试
   - 优化 Docker 镜像构建

3. **数据库优化**：
   - 定期清理日志表
   - 优化慢查询
   - 配置合理的连接池
   - 定期执行 VACUUM

---

## 📚 核心配置文件

| 文件 | 用途 | 位置 |
|------|------|------|
| `.gitlab-ci.yml` | CI/CD 配置 | 项目根目录 |
| `scripts/init-deploy-servers.sh` | 服务器初始化脚本 | scripts/ |
| `.env.test` | 测试环境配置 | 服务器 221 |
| `docker-compose.yml` | Docker 编排配置 | 项目根目录 |

---

## 🎯 下一步

完成配置后，你可以：

1. ✅ **日常开发**：
   - 在 `feature/*` 分支开发新功能
   - 合并到 `develop` 自动部署到测试环境
   - 验证功能后手动部署到生产环境

2. ✅ **监控维护**：
   - 定期检查 Pipeline 状态
   - 监控服务器资源使用
   - 查看应用日志

3. ✅ **持续改进**：
   - 优化 Pipeline 执行时间
   - 完善测试覆盖率
   - 改进部署流程

---

## 📞 获取帮助

如果遇到问题：

1. **查看本文档**：
   - 快速开始章节
   - 详细执行步骤
   - 故障排查章节

2. **检查日志**：
   - GitLab Pipeline 日志
   - 服务器日志：`docker compose logs`
   - GitLab Runner 日志：`journalctl -u gitlab-runner`

3. **验证配置**：
   - 检查环境变量
   - 验证 SSH 密钥
   - 确认 GitLab Runner 状态

4. **参考文档**：
   - 项目文档：`CLAUDE.md`
   - Docker 部署：`scripts/docker-deploy-simple.sh`
   - GitLab CI/CD 官方文档

---

## 🎉 完成

恭喜！你已经完成了 CI/CD 配置。现在：

- ✅ 推送到 `develop` 分支会自动部署到 221 测试环境
- ✅ 所有代码都经过自动化测试和检查
- ✅ 部署流程标准化、可重复、可追溯

**开始你的自动化部署之旅吧！** 🚀
