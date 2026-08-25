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
- **Script Path**: 分别使用 `Jenkinsfile.backend-ci`、`Jenkinsfile.release-checker-ci`、`Jenkinsfile.test-deploy`

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

仓库当前现役链路由三个独立 producer 和一个 release orchestrator 组成：

- `wes_backend-ci` → `Jenkinsfile.backend-ci`
- `wes_frontend-ci` → 前端独立流水线
- release checker producer → `Jenkinsfile.release-checker-ci`
- `wes_test_deploy` → `Jenkinsfile.test-deploy`

旧 `wes_backend` 单体 Pipeline 已退役并从 Jenkins 删除，不要再维护或引用它。

当前 Pipeline 关键点：

- `develop`：
  - 构建 CI 镜像
  - 执行质量检查与测试
  - GitLab PUSH 时校验 `gitlabBefore` → `gitlabAfter` fast-forward，以前一 SHA 为基线执行 Mock 与选中的 HEAVY
  - 导出 provider OpenAPI、provided permissions 和生产输入指纹
  - 上述门禁通过后构建并推送 backend immutable tag 与 `develop` channel tag，并校验镜像内原始制品和 `org.wes.release.*` label
- `main` / 其他分支：
  - `wes_backend-ci` 仍执行 CI
  - 不推送 runtime 镜像或 channel tag
- MR 和 Jenkins 手工构建：执行验证但不推送镜像
- `Jenkinsfile.release-checker-ci`：独立测试、构建和发布 checker 镜像，不导入 WES 应用，也不调用部署 Job
- 前端、后端和 checker producer 均不选择对端镜像，也不自动触发 `wes_test_deploy`

部署行为约束：

- `wes_test_deploy` 是唯一环境变更入口，参数为 `DEPLOY_SCOPE`、所选侧 `FRONTEND_CANDIDATE_DIGEST` / `BACKEND_CANDIDATE_DIGEST`、`DEPLOY_SOURCE_COMMIT_SHA`，以及可选 `FORCE_FULL`、`WARN_APPROVAL_REASON`
- `FRONTEND` 只接受 frontend candidate，`BACKEND` 只接受 backend candidate，`BOTH` 必须同时提供两侧；单侧当前 peer 由 live container 与最近成功报告自动发现，禁止人工输入
- checker digest 由 `DEPLOY_SOURCE_COMMIT_SHA` 固定，不是 Jenkins 参数。前后端 Commit、tree 和 digest 只作审计，不要求跨镜像相等
- `wes_test_deploy` 必须配置 Username with password 类型的 Jenkins 凭据 `wes-test-bootstrap-admin`；只绑定为 `BOOTSTRAP_ADMIN_USERNAME`、`BOOTSTRAP_ADMIN_PASSWORD`，凭据值不写入 Compose config、构建参数、仓库或日志
- 部署前将 `/opt/wes_backend` 对齐到 deploy-source 并核对 `HEAD`；该 Commit 固定 orchestrator、Compose、运行配置清单和 checker，不要求等于 candidate backend revision
- 维护前固定 candidate digest，交叉验证当前 peer，核对镜像自身 revision、原始制品和 label，计算有效配置 hash，读取 DB head，运行 checker，并使用所选后端镜像校验 WMS Provider profile；不探测真实 WMS/ECS
- checker `ERR`、超时或非法报告以 `PRE_CUTOVER_ABORTED` 结束；`WARN` 只有提供绑定本次三个 digest 与 diff hash 的理由才继续
- 模式由 candidate-vs-current 内容与运行输入决定；只允许用 `FORCE_FULL` 升级，不存在 force-FAST
- FAST 只切选择的一侧；backend FAST 仍原子重建 API、两个 Celery worker、WMS fulfillment、Beat 和 Flower
- FULL 才在维护态备份当前数据库、执行批准的 forward migration、权限零漂移、管理员登录、精确 topology 和共享 readiness；日常部署不得创建 fresh DB
- 全部门禁通过后才恢复 Nginx，再验证外部 `/health`、首页和最终 rendered/running service 集合；任一维护后失败以 `CUTOVER_FAILED_MAINTENANCE_HELD` 结束
- 报告保存到 `/srv/wes/releases/${RELEASE_ID}/compatibility-report.json` 并归档为 Jenkins artifact；发布编排不提取菜单 manifest、不运行菜单同步

PROD 边界说明：

- 三个 producer 只负责自身 CI 与镜像发布，不自动部署 TEST 或生产环境
- 生产环境使用 [生产发布 Runbook](prod-release-deploy.md) 描述的同一 orchestrator 合同，不复用 `seed_initial_data.py`
- fresh DB 只允许首次空站点初始化：migration 后注入 `BOOTSTRAP_ADMIN_*`，执行 `bootstrap_foundation.sh` 和新的权限 `--check`
- 日常 FULL 对当前数据库备份并仅向前迁移；权限 mutation 失败只接受精确 post-commit marker 的一次 repair，且不得重跑 mutation
- 前后端环境文件分离维护；只归档有效配置 hash，不记录秘密
- 菜单由前端静态路由拥有，不属于发布门禁

建议核对项：

```bash
rg -n "provider-openapi|provided-permissions|org.wes.release|Push Runtime Image" Jenkinsfile.backend-ci Dockerfile
rg -n "oasdiff|Push Checker" Jenkinsfile.release-checker-ci tools/release_checker
rg -n "DEPLOY_SCOPE|CANDIDATE_DIGEST|FORCE_FULL|WARN_APPROVAL_REASON|compatibility-report" Jenkinsfile.test-deploy
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
git add Jenkinsfile.backend-ci Jenkinsfile.release-checker-ci Jenkinsfile.test-deploy
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

1. 分别进入 backend、release checker producer Job 执行验证构建。
2. 确认手工构建只验证、不发布、不部署。
3. 使用批准的 immutable digest 单独运行 **wes_test_deploy**，查看构建号 → **Console Output**。

#### 10.2 验证各阶段

检查以下阶段是否成功：

- ✅ **Checkout Source**：源码检出与分支识别
- ✅ **Build CI Image**：CI 测试镜像构建
- ✅ **Quality Gate**：格式、Lint、Bandit、架构门禁、脚本合同和 FAST
- ✅ **Compose Contracts**：主机端渲染生产与 TEST 部署配置
- ✅ **RuntimeInbox PostgreSQL Acceptance**：隔离 PostgreSQL 验收
- ✅ **Mock Image Contracts**：MR 与已验证的 `develop` PUSH 构建并验证 Mock 镜像
- ✅ **HEAVY Required**：MR 按目标分支、`develop` PUSH 按 `gitlabBefore` 差异选择并执行 HEAVY
- ✅ **Export Provider Release Artifacts**：导出 provider OpenAPI、provided permissions 和生产输入指纹
- ✅ **Build Runtime Image**：运行时镜像构建
- ✅ **Verify Release Artifacts**：校验镜像内原始制品和对应 OCI label
- ✅ **Push Runtime Image**：仅门禁通过的 GitLab `develop` PUSH 发布后端镜像；MR、其他分支 PUSH 和手工构建不发布

release checker Job 还必须证明固定 `oasdiff`、独立镜像、自测和不触发部署。`wes_test_deploy` 要验证三个 scope 的输入拒绝矩阵、FAST/FULL、当前 peer 自动发现、兼容报告和维护态前后失败终态。

#### 10.3 查看报告

- **测试报告**：Jenkins → wes_backend-ci → **Test Result**
- **质量与 HEAVY 产物**：Jenkins → wes_backend-ci → **Artifacts**
- **安全报告**：Artifacts → `bandit-report.json`
- **兼容报告**：Jenkins → wes_test_deploy → **Artifacts** → `compatibility-report.json`
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
- [ ] `Jenkinsfile.backend-ci`、`Jenkinsfile.release-checker-ci` 与 `Jenkinsfile.test-deploy` 已提交到 GitLab
- [ ] 手动构建测试通过
- [ ] 所有阶段执行成功
- [ ] 测试报告正常显示
- [ ] 三个 producer 都不触发部署，发布结果标记为 `PUBLISHED — NOT DEPLOYED`
- [ ] TEST 使用 scope 与 candidate digest 运行并归档 `compatibility-report.json`
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
