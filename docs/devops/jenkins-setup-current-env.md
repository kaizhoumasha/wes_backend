# Jenkins CI/CD 配置指南 - 当前环境

## 📋 环境信息

- **GitLab 服务器**：192.168.0.220:9080（Docker）
- **Jenkins 服务器**：192.168.0.220（Docker）
- **Jenkins Node 节点**：192.168.0.221（已配置）
- **GitHub 开发真源**：https://github.com/kaizhoumasha/wes_backend.git
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

发布流水线必须使用 GitLab Webhook：

**方式 1：GitLab Webhook（发布必需）**
- ✅ **Build when a change is pushed to GitLab**
- 展开 **Advanced**，点击 **Secret Token → Generate**，只把生成值配置到 GitLab Webhook
- GitLab webhook URL: `http://192.168.0.220:9081/project/wes_backend-ci`
- 记录这个 URL，稍后在 GitLab 中配置

**方式 2：Poll SCM（仅验证，不可发布）**
- ✅ **Poll SCM**
- Schedule: `H/5 * * * *`（每 5 分钟检查一次）
- Poll SCM 不提供 webhook 的 `gitlabBefore` / `gitlabAfter`，因此发布门禁会 fail closed，不能作为 `develop` 发布触发

##### Pipeline 配置

- **Definition**: Pipeline script from SCM
- **SCM**: Git
  - **Repository URL**: `http://192.168.0.220:9080/wes/wes_backend.git`
  - **Credentials**: 选择 `gitlab-credentials`
  - **Branches to build**: `*/develop`
- **Script Path**: `Jenkinsfile.backend-ci`

发布 Job 的 Pipeline SCM 必须固定从 `develop` 加载当前门禁脚本；`main` 或其他分支如需验证，另建不发布镜像的 Job。

#### 5.3 保存配置

点击 **Save** 保存配置。

### 步骤 6：配置 GitLab Webhook（发布必需）

#### 6.1 在 GitLab 中配置 Webhook

1. 登录 GitLab：`http://192.168.0.220:9080`
2. 进入项目：**wes / wes_backend**
3. 左侧菜单 → **Settings → Webhooks**
4. 配置 Webhook：
   ```
   URL: http://192.168.0.220:9081/project/wes_backend-ci
   Secret token: 与 Jenkins Job Advanced 中生成的 per-project token 完全一致（必填）
   Trigger:
     ✅ Push events
     ✅ Merge request events
   SSL verification:
     ⚠️ Enable SSL verification（如果使用 HTTPS）
     或取消勾选（如果使用 HTTP）
   ```
5. 点击 **Add webhook**

Secret token 不得写入仓库、文档或构建日志；Jenkins 与 GitLab 任一侧缺失或不一致时，发布触发必须视为未配置。

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
  - GitLab PUSH 时校验 `gitlabBefore` → `gitlabAfter` fast-forward，以前一 SHA 为基线执行 Mock 与选中的 HEAVY
  - 上述门禁通过后构建并推送 backend immutable tag 与 `develop` channel tag
- `main` / 其他分支：
  - `wes_backend-ci` 仍执行 CI
  - 不推送 runtime 镜像或 channel tag
- MR 和 Jenkins 手工构建：执行验证但不推送镜像

部署行为约束：

- `wes_test_deploy` 是独立部署任务；前端 producer 传入无默认值的 immutable 前后端 tag、两端 commit、`DEPLOY_SOURCE_COMMIT_SHA`、OpenAPI SHA 与权限 SHA，缺少或不匹配即拒绝
- `wes_test_deploy` 必须配置 Username with password 类型的 Jenkins 凭据 `wes-test-bootstrap-admin`；用户名和密码仅作为 `BOOTSTRAP_ADMIN_USERNAME`、`BOOTSTRAP_ADMIN_PASSWORD` 注入一次性基础授权容器，不写入构建参数、仓库或日志
- `DEPLOY_SOURCE_COMMIT_SHA` 必须等于批准的后端镜像 revision；部署前会将 `/opt/wes_backend` 强制对齐到该 SHA 并再次核对 `HEAD`
- 前后端镜像拉取并固定到 digest 后，先核对 backend revision 与 frontend revision/backend-contract/OpenAPI/permission labels；全部通过后才停止 Nginx 并按 Compose project/service 标签停止旧应用
- 迁移前只允许 `db` 与 `redis` 运行；未知 service 使切换失败并保持 Nginx 关闭
- 每次部署先在保留的 TEST PostgreSQL volume 中创建由 Jenkins build/commit 唯一命名的新数据库并证明无业务表；不删除或复用旧 TEST 数据库
- 使用新后端镜像的一次性命令执行 Alembic、fresh-DB `bootstrap_foundation` 和权限 `--check`
- 若 bootstrap 报告独占整行的 `DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED`（详情另以 `CACHE_INVALIDATION_FAILURE_DETAIL:` 输出），只执行一次 `--repair-cache`，再执行新的 `--check`，不得重跑数据库 mutation
- 零漂移后才使用 `docker-compose.test-deploy.yml` 重建 `api`、两个 Celery worker、Beat、Flower 与 frontend，并从固定前端镜像提取菜单清单
- 同步完成后会校验 `wes_sys.menus` 数量必须大于 0
- 后端 `/ready`、前端资源、两端 revision、前端绑定的 backend revision、OpenAPI/permission labels 和菜单同步均通过后，才以 `compose up -d --no-deps nginx` 恢复入口；任一外部检查失败立即再次停止 Nginx

PROD 边界说明：

- `wes_backend-ci` 只负责后端 CI 与镜像发布，不自动部署 TEST 或生产环境
- 生产环境按手动部署 runbook 执行，不复用 `seed_initial_data.py`
- 生产发布说明文档：`prod-release-deploy.md`
- fresh DB 推荐顺序（完整维护态顺序以 `prod-release-deploy.md` 为准）：
  1. `./scripts/migrate.sh upgrade`
  2. 注入 `BOOTSTRAP_ADMIN_USERNAME`、`BOOTSTRAP_ADMIN_PASSWORD`、`BOOTSTRAP_ADMIN_FULL_NAME`、`BOOTSTRAP_ADMIN_EMAIL`
  3. `bash scripts/data/bootstrap_foundation.sh`
  4. `uv run python scripts/data/sync_permissions.py --check`
- 前后端分别维护 `.env.prod` 与 `.env.frontend.prod` 是正常做法
- 生产建议在 `.env.prod` 中启用 `USE_SNOWFLAKE_ID=true`
- 已有数据库按生产 Runbook 捕获 `sync_permissions.py --apply` 输出，普通非零立即 fail closed；只有精确裸 marker 行才按一次 `--repair-cache` → fresh `--check` 恢复，且不得重跑 `--apply`
- 固定版本应用启动后，从批准的前端 digest 提取菜单 manifest；菜单同步通过后才恢复 Nginx

建议核对项：

```bash
rg -n "IMAGE_REPO|Push Runtime Image|CI_EVENT_TYPE" Jenkinsfile.backend-ci
rg -n "DEPLOY_COMPOSE_FILE|IMAGE_PULL_RETRIES|bootstrap_foundation|--check|--repair-cache|sync_menus|MENU_COUNT" Jenkinsfile.test-deploy
```

确认 Jenkins 中 Pipeline 配置：

- **Definition**: Pipeline script from SCM
- **Repository URL**: `http://192.168.0.220:9080/wes/wes_backend.git`
- **Script Path**: `Jenkinsfile.backend-ci`
- **Build when a change is pushed to GitLab**: 已勾选

GitHub `origin/develop` 是代码评审与合入真源；GitLab `gitlab/develop` 只接收同一 GitHub merge SHA 并触发 Jenkins。
禁止直接在 GitLab 修复或用 force push 覆盖分叉。

### 步骤 9：从 GitHub 真源发布现役 Jenkins Pipeline

```bash
# 先在功能分支提交并通过 GitHub PR 合入。
git add Jenkinsfile.backend-ci Jenkinsfile.test-deploy
git commit -m "chore(ci): 更新现役 Jenkins Pipeline 配置"
git push origin <feature-branch>

# GitHub Merge 与 GitLab Push 分别取得授权后，发布精确 SHA。
git fetch origin
git fetch gitlab
release_sha="$(git rev-parse origin/develop)"
git merge-base --is-ancestor gitlab/develop "$release_sha"
git push gitlab "$release_sha:refs/heads/develop"
```

`git merge-base --is-ancestor` 失败表示远端已分叉，必须停止并治理；不得直接 cherry-pick、merge 或强推发布。

### 步骤 10：测试 Pipeline

#### 10.1 手动触发构建

1. Jenkins → **wes_backend-ci** 项目
2. 点击 **Build Now**
3. 查看构建日志：点击构建号 → **Console Output**

#### 10.2 验证各阶段

检查以下阶段是否成功：

- ✅ **Checkout Source**：源码检出与分支识别
- ✅ **Build CI Image**：CI 测试镜像构建
- ✅ **Quality Gate**：格式、Lint、Bandit、架构门禁、脚本合同和 FAST
- ✅ **Compose Contracts**：主机端渲染生产与 TEST 部署配置
- ✅ **RuntimeInbox PostgreSQL Acceptance**：隔离 PostgreSQL 验收
- ✅ **Mock Image Contracts**：MR 与已验证的 `develop` PUSH 构建并验证 Mock 镜像
- ✅ **HEAVY Required**：MR 按目标分支、`develop` PUSH 按 `gitlabBefore` 差异选择并执行 HEAVY
- ✅ **Build Runtime Image**：运行时镜像构建
- ✅ **Push Runtime Image**：仅门禁通过的 GitLab `develop` PUSH 发布后端镜像；MR、其他分支 PUSH 和手工构建不发布

`wes_backend-ci` 不再自动触发 TEST 部署或选择前端镜像。`wes_test_deploy` 由部署人员单独运行并显式选择前后端版本。

#### 10.3 查看报告

- **测试报告**：Jenkins → wes_backend-ci → **Test Result**
- **质量与 HEAVY 产物**：Jenkins → wes_backend-ci → **Artifacts**
- **安全报告**：Artifacts → `bandit-report.json`
- **TEST 部署日志**：Jenkins → wes_test_deploy → **Console Output**

### 步骤 11：验证部署

```bash
# 在任意机器上测试
curl http://192.168.0.221:8001/health

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
docker exec wes_api_test curl -f http://127.0.0.1:8001/health
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
- [ ] `wes_backend-ci` 是普通 Pipeline Job，不是 Multibranch Pipeline
- [ ] GitLab Webhook 已配置
- [ ] `Jenkinsfile.backend-ci` 与 `Jenkinsfile.test-deploy` 已提交到 GitLab
- [ ] 手动构建测试通过
- [ ] 所有阶段执行成功
- [ ] 测试报告正常显示
- [ ] 部署到测试环境成功
- [ ] 健康检查通过

## 🎯 下一步优化

1. **配置通知**：
   - 安装 Email Extension 插件
   - 配置构建失败邮件通知

2. **分支验证 Job**：
   - 如需自动发现分支，可另建不发布镜像的 Multibranch Pipeline
   - 不得替换依赖 `gitlabBefore` / `gitlabAfter` 的 `wes_backend-ci` 发布 Job

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
