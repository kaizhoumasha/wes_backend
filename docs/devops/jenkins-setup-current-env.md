# Jenkins CI/CD 配置指南 - 当前环境

## 📋 环境信息

- **GitLab 服务器**：192.168.0.220:9080（Docker）
- **Jenkins 服务器**：192.168.0.220（Docker）
- **Jenkins Node 节点**：192.168.0.221（已配置）
- **GitLab 仓库**：http://192.168.0.220:9080/wes/wes_backend.git
- **LDAP 账号**：zhoukai / Ctt123456
- **部署目标**：192.168.0.221（测试环境）

## 🚀 配置步骤

### 步骤 1：访问 Jenkins

1. 浏览器访问：`http://192.168.0.220:9081`
2. 使用 LDAP 账号登录：
   - 用户名：`zhoukai`
   - 密码：`Ctt123456`

### 步骤 2：验证 Jenkins Node 配置

1. 进入 **Manage Jenkins → Manage Nodes and Clouds**
2. 确认 192.168.0.221 节点状态为 **在线**
3. 查看节点标签（Labels），记录下来（例如：`test-node`、`docker` 等）

### 步骤 3：配置 GitLab 凭据

#### 3.1 生成 GitLab Personal Access Token

1. 登录 GitLab：`http://192.168.0.220:9080`
2. 用户头像 → **Preferences** → **Access Tokens**
3. 创建新 Token：
   ```
   Name: jenkins-ci
   Scopes:
     ✅ api
     ✅ read_repository
     ✅ write_repository
   ```
4. 点击 **Create personal access token**
5. **复制并保存** Token（只显示一次）

#### 3.2 在 Jenkins 中添加 GitLab 凭据

1. Jenkins → **Manage Jenkins → Manage Credentials**
2. 选择 **(global)** 域
3. 点击 **Add Credentials**
4. 配置：
   ```
   Kind: Username with password
   Scope: Global
   Username: zhoukai
   Password: <粘贴刚才的 GitLab Token>
   ID: gitlab-credentials
   Description: GitLab 凭据 - zhoukai
   ```
5. 点击 **Create**

### 步骤 4：配置 SSH 凭据（用于部署）

#### 4.1 检查 SSH 密钥

在 Jenkins 服务器（192.168.0.220）上执行：

```bash
# 进入 Jenkins 容器
docker exec -it jenkins bash

# 检查是否有 SSH 密钥
ls -la ~/.ssh/

# 如果没有，生成新密钥
ssh-keygen -t rsa -b 4096 -C "jenkins@wes_backend-ci" -f ~/.ssh/jenkins_rsa -N ""

# 查看公钥
cat ~/.ssh/jenkins_rsa.pub
```

#### 4.2 将公钥添加到部署服务器

在部署服务器（192.168.0.221）上执行：

```bash
# 添加 Jenkins 公钥
mkdir -p ~/.ssh
chmod 700 ~/.ssh

# 将上面的公钥内容添加到 authorized_keys
cat >> ~/.ssh/authorized_keys << 'EOF'
<粘贴 Jenkins 容器中的 ~/.ssh/jenkins_rsa.pub 内容>
EOF

chmod 600 ~/.ssh/authorized_keys
```

#### 4.3 在 Jenkins 中添加 SSH 凭据

1. Jenkins → **Manage Jenkins → Manage Credentials**
2. 点击 **Add Credentials**
3. 配置：
   ```
   Kind: SSH Username with private key
   Scope: Global
   ID: ssh-test-server
   Description: SSH 私钥 - 测试服务器 (192.168.0.221)
   Username: root
   Private Key: Enter directly
     - 在 Jenkins 容器中执行：cat ~/.ssh/jenkins_rsa
     - 粘贴私钥内容（包括 BEGIN 和 END 行）
   ```
4. 点击 **Create**

#### 4.4 测试 SSH 连接

在 Jenkins 容器中测试：

```bash
ssh -i ~/.ssh/jenkins_rsa root@192.168.0.221 "echo 'SSH 连接成功'"
```

### 步骤 5：创建 Jenkins Pipeline 项目

#### 5.1 创建项目

1. Jenkins 首页 → **New Item**
2. 输入项目名称：`wes_backend-ci`
3. 选择 **Pipeline**
4. 点击 **OK**

#### 5.2 配置项目

##### General 配置

- ✅ **Discard old builds**
  - Strategy: Log Rotation
  - Max # of builds to keep: `10`

- ✅ **GitLab Connection**（如果安装了 GitLab 插件）
  - GitLab connection: 选择或创建 GitLab 连接

##### Build Triggers

选择以下之一：

**方式 1：GitLab Webhook（推荐）**
- ✅ **Build when a change is pushed to GitLab**
- GitLab webhook URL: `http://192.168.0.220:9081/project/wes_backend-ci`
- 记录这个 URL，稍后在 GitLab 中配置

**方式 2：Poll SCM（备选）**
- ✅ **Poll SCM**
- Schedule: `H/5 * * * *`（每 5 分钟检查一次）

##### Pipeline 配置

- **Definition**: Pipeline script from SCM
- **SCM**: Git
  - **Repository URL**: `http://192.168.0.220:9080/wes/wes_backend.git`
  - **Credentials**: 选择 `gitlab-credentials`
  - **Branches to build**: `*/develop`（或 `*/main`）
- **Script Path**: `Jenkinsfile.backend-ci`

#### 5.3 保存配置

点击 **Save** 保存配置。

### 步骤 6：配置 GitLab Webhook（推荐）

#### 6.1 在 GitLab 中配置 Webhook

1. 登录 GitLab：`http://192.168.0.220:9080`
2. 进入项目：**wes / wes_backend**
3. 左侧菜单 → **Settings → Webhooks**
4. 配置 Webhook：
   ```
   URL: http://192.168.0.220:9081/project/wes_backend-ci
   Secret token: (留空或设置一个密钥)
   Trigger:
     ✅ Push events
     ✅ Merge request events
   SSL verification:
     ⚠️ Enable SSL verification（如果使用 HTTPS）
     或取消勾选（如果使用 HTTP）
   ```
5. 点击 **Add webhook**

#### 6.2 测试 Webhook

1. 在 Webhook 列表中找到刚创建的 Webhook
2. 点击 **Test → Push events**
3. 查看响应：
   - **HTTP 200**：成功
   - **其他状态码**：检查 Jenkins 日志

### 步骤 7：配置部署服务器（192.168.0.221）

#### 7.1 初始化项目目录

在 192.168.0.221 上执行：

```bash
# 创建项目目录
sudo mkdir -p /opt/wes_backend
sudo chown -R root:root /opt/wes_backend

# 克隆代码
cd /opt/wes_backend
git clone http://192.168.0.220:9080/wes/wes_backend.git .

# 配置 Git 凭据（避免每次输入密码）
git config credential.helper store
git pull  # 输入一次用户名和密码后会保存
```

#### 7.2 创建环境文件

```bash
cd /opt/wes_backend

# 复制环境文件模板
cp .env.example .env.test

# 编辑环境文件
vim .env.test
```

配置示例：

```bash
# 数据库配置
DATABASE_URL=postgresql+psycopg://wes_user:wes_password@wes_postgres:5432/wes_db

# Redis 配置
REDIS_URL=redis://wes_redis:6379/0

# 应用配置
ENVIRONMENT=test
DEBUG=false
SECRET_KEY=<生成一个随机密钥>

# 时区配置
DATETIME_TIMEZONE=Asia/Shanghai

# 日志配置
LOG_LEVEL=INFO
```

#### 7.3 验证 Docker 和 Docker Compose

```bash
# 检查 Docker
docker --version
docker ps

# 检查 Docker Compose
docker-compose --version
```

### 步骤 8：确认 Jenkinsfile 与当前链路一致

仓库当前现役链路是三段式：

- `wes_backend-ci` → `Jenkinsfile.backend-ci`
- `wes_frontend-ci` → 前端独立流水线
- `wes_test_deploy` → `Jenkinsfile.test-deploy`

旧 `wes_backend` 单体 Pipeline 已退役并从 Jenkins 删除，不要再维护或引用它。

当前 Pipeline 关键点：

- `develop`：
  - 构建 CI 镜像
  - 执行质量检查与测试
  - 构建并推送 backend immutable tag 与 `develop` channel tag
  - 自动触发 `wes_test_deploy`
- `main` / 其他分支：
  - `wes_backend-ci` 仍执行 CI
  - 非 MR 时推送 backend immutable tag 与 channel tag
    - `main` → `prod`
    - 其他分支 → 分支同名 tag
  - 不自动触发 TEST 部署

部署行为约束：

- `wes_test_deploy` 负责 TEST 环境部署
- 自动链路默认拉取 backend `develop` 与 frontend `develop` 镜像
- 部署前会将 `/opt/wes_backend` 强制对齐到目标 commit，避免服务器本地漂移挡住发布
- 使用 `docker-compose.test-deploy.yml` 重建 TEST 应用
- API 容器会只读挂载同机的 `../wes_frontend`，供部署后菜单同步使用
- 部署成功前会执行 `python scripts/data/sync_menus.py --frontend-path /opt/wes_frontend`
- 同步完成后会校验 `wes_sys.menus` 数量必须大于 0
- 健康检查为 API 容器内 `http://127.0.0.1:8001/api/v1/admin/performance/health`
- 同时检查 nginx `/health` 和首页

建议核对项：

```bash
rg -n "IMAGE_REPO|Trigger Test Deploy|Push Runtime Image" Jenkinsfile.backend-ci
rg -n "DEPLOY_COMPOSE_FILE|HEALTH_CHECK_URL|IMAGE_PULL_RETRIES|sync_menus|MENU_COUNT" Jenkinsfile.test-deploy
```

确认 Jenkins 中 Pipeline 配置：

- **Definition**: Pipeline script from SCM
- **Repository URL**: `http://192.168.0.220:9080/wes/wes_backend.git`
- **Script Path**: `Jenkinsfile.backend-ci`
- **Build when a change is pushed to GitLab**: 已勾选

### 步骤 9：提交现役 Jenkins Pipeline 到 GitLab

```bash
# 提交现役 Pipeline
git add Jenkinsfile.backend-ci Jenkinsfile.test-deploy
git commit -m "chore(ci): 更新现役 Jenkins Pipeline 配置"
git push gitlab develop
```

### 步骤 10：测试 Pipeline

#### 10.1 手动触发构建

1. Jenkins → **wes_backend-ci** 项目
2. 点击 **Build Now**
3. 查看构建日志：点击构建号 → **Console Output**

#### 10.2 验证各阶段

检查以下阶段是否成功：

- ✅ **Checkout Source**：源码检出与分支识别
- ✅ **Build CI Image**：CI 测试镜像构建
- ✅ **Quality Checks**：格式检查、代码质量、安全检查
- ✅ **Tests**：单元测试、API 签名测试
- ✅ **Build Runtime Image**：运行时镜像构建
- ✅ **Push Runtime Image**：非 MR 镜像推送
- ✅ **Trigger Test Deploy**：develop push 自动触发 TEST 部署

#### 10.3 查看报告

- **测试报告**：Jenkins → wes_backend-ci → **Test Result**
- **覆盖率报告**：Jenkins → wes_backend-ci → **Coverage Report**
- **安全报告**：Jenkins → wes_backend-ci → **Artifacts** → bandit-report.json
- **TEST 部署日志**：Jenkins → wes_test_deploy → **Console Output**

### 步骤 11：验证部署

```bash
# 在任意机器上测试
curl http://192.168.0.221:8001/api/v1/admin/performance/health

# 预期响应
{"status": "healthy"}
```

## 🔧 故障排查

### 问题 1：GitLab 凭据认证失败

**症状**：`Authentication failed` 或 `Permission denied`

**解决**：

1. 确认 GitLab Personal Access Token 是否正确
2. 确认 Token 权限包含 `api`、`read_repository`、`write_repository`
3. 在 Jenkins 中重新配置凭据

### 问题 2：Jenkins Node 离线

**症状**：构建失败，提示 `No available executors`

**解决**：

1. Jenkins → **Manage Jenkins → Manage Nodes and Clouds**
2. 检查 192.168.0.221 节点状态
3. 如果离线，点击节点 → **Launch agent**
4. 查看节点日志排查问题

### 问题 3：Docker 构建失败

**症状**：`docker: command not found` 或权限错误

**解决**：

```bash
# 在 Jenkins Node (192.168.0.221) 上检查
docker --version
docker ps

# 确保 Jenkins 用户有 Docker 权限
sudo usermod -aG docker jenkins
```

### 问题 4：健康检查失败

**症状**：部署后健康检查超时

**解决**：

```bash
# 在 192.168.0.221 上检查
cd /opt/wes_backend
docker compose -f docker-compose.deploy.yml --env-file .env.test ps
docker compose -f docker-compose.deploy.yml --env-file .env.test logs api

# 检查端口是否监听
netstat -tuln | grep 8001

# 手动测试健康检查
docker exec wes_api_test curl -f http://127.0.0.1:8001/api/v1/admin/performance/health
```

## 📊 验证清单

完成以下检查确保配置成功：

- [ ] Jenkins 可以访问（http://192.168.0.220:9081）
- [ ] LDAP 登录成功（zhoukai/Ctt123456）
- [ ] Jenkins Node (192.168.0.221) 状态在线
- [ ] GitLab 凭据已配置
- [ ] SSH 凭据已配置
- [ ] SSH 连接测试通过
- [ ] Pipeline 项目已创建
- [ ] GitLab Webhook 已配置
- [ ] Jenkinsfile 已提交到 GitLab
- [ ] 手动构建测试通过
- [ ] 所有阶段执行成功
- [ ] 测试报告正常显示
- [ ] 部署到测试环境成功
- [ ] 健康检查通过

## 🎯 下一步优化

1. **配置通知**：
   - 安装 Email Extension 插件
   - 配置构建失败邮件通知

2. **多分支 Pipeline**：
   - 创建 Multibranch Pipeline
   - 自动发现和构建所有分支

3. **生产环境部署**：
   - 添加生产环境部署阶段
   - 需要手动确认才能部署

4. **性能优化**：
   - 配置 Docker 卷缓存
   - 使用 Jenkins 缓存插件

## 📞 支持

如有问题，请联系：
- 技术支持：dev-team@example.com
- 文档反馈：提交 Issue 到 GitLab 项目
