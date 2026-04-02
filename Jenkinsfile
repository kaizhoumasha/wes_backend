// ==============================================
// Jenkins Pipeline - P9 WES Backend
// ==============================================
// 环境:
//   - GitLab: 192.168.0.220:9080
//   - Jenkins: 192.168.0.220 (Docker)
//   - Jenkins Node: 192.168.0.221 (构建和部署)
// ==============================================

pipeline {
    // 在 Jenkins Node 上执行（192.168.0.221）
    agent {
        label 'WES'  // 请根据实际的 Node 标签修改
    }

    // 环境变量
    environment {
        // Python 版本
        PYTHON_VERSION = '3.13'
        // 时区配置
        DATETIME_TIMEZONE = 'Asia/Shanghai'
        // 部署配置（本地部署，因为在 Node 上执行）
        DEPLOY_PATH = '/opt/wes_backend'
        FRONTEND_DEPLOY_PATH = '/opt/wes_frontend'
        DEPLOY_ENV_FILE = '.env.test'
        DEPLOY_COMPOSE_FILE = 'docker-compose.yml'
        DEPLOY_FRONTEND_COMPOSE_FILE = 'docker-compose.frontend.yml'
        DEPLOY_PROFILE = 'test'
        FRONTEND_REPO_URL = 'http://192.168.0.220:9080/wes/wes_frontend.git'
        FRONTEND_DEPLOY_BRANCH = 'develop'
        // 健康检查配置
        HEALTH_CHECK_URL = 'http://localhost:8001/api/v1/performance/health'
        HEALTH_CHECK_RETRIES = '30'
        // CI 镜像构建目标
        CI_BUILD_TARGET = 'testing'
    }

    // 构建选项
    options {
        // 保留最近 10 次构建
        buildDiscarder(logRotator(numToKeepStr: '10'))
        // 超时配置
        timeout(time: 60, unit: 'MINUTES')
        // 使用显式 checkout，按 GitLab webhook 提供的分支检出代码
        skipDefaultCheckout(true)
        // 禁用并发构建
        disableConcurrentBuilds()
        // 时间戳
        timestamps()
    }

    stages {
        // ==============================================
        // 检出阶段：按 GitLab 事件检出源码
        // ==============================================
        stage('Checkout Source') {
            steps {
                script {
                    String sourceBranch = env.gitlabSourceBranch ?: env.gitlabBranch ?: 'develop'
                    String targetBranch = env.gitlabTargetBranch ?: ''
                    String gitlabActionType = (env.gitlabActionType ?: env.GITLAB_OBJECT_KIND ?: '').trim().toUpperCase()
                    boolean hasMergeRequestId = ((env.gitlabMergeRequestId ?: '').trim()) as boolean
                    boolean isMergeRequest = gitlabActionType.contains('MERGE') || hasMergeRequestId

                    env.CI_SOURCE_BRANCH = sourceBranch
                    env.CI_TARGET_BRANCH = targetBranch
                    env.CI_EVENT_TYPE = gitlabActionType ?: 'MANUAL'
                    env.CI_IS_MERGE_REQUEST = isMergeRequest ? 'true' : 'false'

                    echo "📥 检出源码: source=${sourceBranch}, target=${targetBranch ?: '-'}, event=${env.CI_EVENT_TYPE}"

                    def extensions = [[$class: 'CleanBeforeCheckout']]
                    if (isMergeRequest && targetBranch) {
                        extensions << [
                            $class: 'PreBuildMerge',
                            options: [
                                fastForwardMode: 'FF',
                                mergeRemote: 'origin',
                                mergeTarget: targetBranch
                            ]
                        ]
                    }

                    checkout([
                        $class: 'GitSCM',
                        branches: [[name: "origin/${sourceBranch}"]],
                        userRemoteConfigs: [[
                            name: 'origin',
                            url: 'http://192.168.0.220:9080/wes/wes_backend.git',
                            credentialsId: 'gitlab-http-creds',
                            refspec: '+refs/heads/*:refs/remotes/origin/*'
                        ]],
                        extensions: extensions
                    ])

                    String fullCommit = sh(returnStdout: true, script: 'git rev-parse HEAD').trim()
                    String shortCommit = fullCommit.take(7)
                    env.CI_COMMIT_SHA = fullCommit
                    env.CI_IMAGE = "wes-backend-ci:${env.BUILD_NUMBER}-${shortCommit}"
                    echo "🐳 CI 镜像标签: ${env.CI_IMAGE}"
                }
            }
        }

        // ==============================================
        // 准备阶段：构建 CI 镜像
        // ==============================================
        stage('Build CI Image') {
            steps {
                script {
                    echo '🐳 构建 CI 测试镜像...'
                    sh '''
                        set -e
                        docker build \
                            --target ${CI_BUILD_TARGET} \
                            -t ${CI_IMAGE} \
                            .
                    '''
                    echo '✅ CI 测试镜像构建完成'
                }
            }
        }

        // ==============================================
        // 质量阶段：代码检查（并行执行）
        // ==============================================
        stage('Quality Checks') {
            parallel {
                // 代码格式检查
                stage('Format Check') {
                    steps {
                        script {
                            echo '🔍 检查代码格式 (Ruff Format)...'
                            sh '''
                                set -e
                                docker run --rm \
                                    ${CI_IMAGE} \
                                    sh -c 'ruff format --check .'
                            '''
                            echo '✅ 代码格式检查通过'
                        }
                    }
                }

                // 代码质量检查
                stage('Lint Check') {
                    steps {
                        script {
                            echo '🔍 检查代码质量 (Ruff Lint)...'
                            sh '''
                                set -e
                                docker run --rm \
                                    ${CI_IMAGE} \
                                    sh -c 'ruff check .'
                            '''
                            echo '✅ 代码质量检查通过'
                        }
                    }
                }

                // 安全检查
                stage('Security Check') {
                    steps {
                        script {
                            echo '🔍 安全检查 (Bandit)...'
                            sh '''
                                set -e
                                mkdir -p reports
                                docker run --rm \
                                    -v "$WORKSPACE/reports:/artifacts/reports" \
                                    ${CI_IMAGE} \
                                    sh -c '
                                        mkdir -p /artifacts/reports && \
                                        bandit -r src/ -f json -o /artifacts/reports/bandit-report.json && \
                                        bandit -r src/ -f screen
                                    '
                            '''
                            echo '✅ 安全检查完成'
                        }
                    }
                    post {
                        always {
                            // 归档安全报告
                            archiveArtifacts artifacts: 'reports/bandit-report.json', allowEmptyArchive: true
                        }
                    }
                }
            }
        }

        // ==============================================
        // 测试阶段：单元测试（并行执行）
        // ==============================================
        stage('Tests') {
            parallel {
                // 单元测试
                stage('Unit Tests') {
                    steps {
                        script {
                            echo '🧪 运行单元测试...'
                            sh '''
                                set -e
                                mkdir -p reports/coverage
                                docker run --rm \
                                    --env-file "$WORKSPACE/.env.test" \
                                    -v "$WORKSPACE/reports:/artifacts/reports" \
                                    ${CI_IMAGE} \
                                    sh -c '
                                        mkdir -p /artifacts/reports/coverage && \
                                        pytest tests/ -v --tb=short \
                                            --cov=src \
                                            --cov-report=term-missing \
                                            --cov-report=html:/artifacts/reports/coverage \
                                            --cov-report=xml:/artifacts/reports/coverage.xml \
                                            --junitxml=/artifacts/reports/junit.xml
                                    '
                            '''
                            echo '✅ 单元测试通过'
                        }
                    }
                    post {
                        always {
                            // 发布测试报告
                            junit 'reports/junit.xml'
                            // 发布覆盖率报告
                            script {
                                if (fileExists('reports/coverage/index.html')) {
                                    try {
                                        publishHTML([
                                            allowMissing: false,
                                            alwaysLinkToLastBuild: true,
                                            keepAll: true,
                                            reportDir: 'reports/coverage',
                                            reportFiles: 'index.html',
                                            reportName: 'Coverage Report'
                                        ])
                                    } catch (Throwable error) {
                                        echo "⚠️ 跳过 HTML 覆盖率发布: ${error.getMessage()}"
                                    }
                                } else {
                                    echo '⚠️ 未找到 HTML 覆盖率报告，跳过 publishHTML'
                                }
                            }
                            // 归档报告
                            archiveArtifacts artifacts: 'reports/**/*', allowEmptyArchive: true
                        }
                    }
                }

                // API 签名测试
                stage('API Signature Tests') {
                    steps {
                        script {
                            echo '🔐 测试 API 签名认证...'
                            sh '''
                                set -e
                                docker run --rm \
                                    --env-file "$WORKSPACE/.env.test" \
                                    ${CI_IMAGE} \
                                    sh -c 'pytest tests/api/test_signature.py -v --tb=short'
                            '''
                            echo '✅ API 签名测试通过'
                        }
                    }
                }
            }
        }

        // ==============================================
        // 部署阶段：部署到测试环境（本地部署）
        // ==============================================
        stage('Deploy to Testing') {
            when {
                expression {
                    env.CI_SOURCE_BRANCH == 'develop' &&
                    env.CI_IS_MERGE_REQUEST != 'true'
                }
            }
            steps {
                script {
                    echo "🚀 开始部署到测试环境..."

                    withCredentials([usernamePassword(credentialsId: 'gitlab-http-creds', usernameVariable: 'GITLAB_USER', passwordVariable: 'GITLAB_TOKEN')]) {
                        sh '''
                            set -e
                            set -o pipefail

                            # 颜色输出
                            RED='\\033[0;31m'
                            GREEN='\\033[0;32m'
                            YELLOW='\\033[1;33m'
                            NC='\\033[0m'

                            TEST_INFRA_SERVICES="db redis"
                            TEST_APP_SERVICES="frontend api celery_worker nginx"

                            rollback_deploy() {
                                echo -e "${YELLOW}🔄 回滚后端到: $PREVIOUS_BACKEND_COMMIT${NC}"
                                git -C "${DEPLOY_PATH}" checkout --detach "$PREVIOUS_BACKEND_COMMIT" || true

                                if [ -n "${PREVIOUS_FRONTEND_COMMIT:-}" ]; then
                                    echo -e "${YELLOW}🔄 回滚前端到: $PREVIOUS_FRONTEND_COMMIT${NC}"
                                    git -C "${FRONTEND_DEPLOY_PATH}" checkout --detach "$PREVIOUS_FRONTEND_COMMIT" || true
                                fi

                                docker compose \
                                    -f "${DEPLOY_COMPOSE_FILE}" \
                                    -f "${DEPLOY_FRONTEND_COMPOSE_FILE}" \
                                    --env-file "${DEPLOY_ENV_FILE}" \
                                    --profile infra up -d ${TEST_INFRA_SERVICES} || true

                                docker compose \
                                    -f "${DEPLOY_COMPOSE_FILE}" \
                                    -f "${DEPLOY_FRONTEND_COMPOSE_FILE}" \
                                    --env-file "${DEPLOY_ENV_FILE}" \
                                    up -d --build --force-recreate ${TEST_APP_SERVICES} || true
                            }

                            echo -e "${GREEN}📂 切换到后端项目目录...${NC}"
                            cd "${DEPLOY_PATH}"

                            echo -e "${GREEN}📝 同步部署环境文件...${NC}"
                            cp -f "${DEPLOY_ENV_FILE}" .env

                            echo -e "${GREEN}📥 更新后端代码...${NC}"
                            PREVIOUS_BACKEND_COMMIT=$(git rev-parse HEAD)
                            echo "📌 后端当前提交: $PREVIOUS_BACKEND_COMMIT"
                            echo "📌 后端目标提交: ${CI_COMMIT_SHA}"
                            git fetch origin
                            git checkout --detach "${CI_COMMIT_SHA}"
                            echo -e "${GREEN}📌 后端新提交: $(git log -1 --oneline)${NC}"

                            echo -e "${GREEN}📥 同步前端代码...${NC}"
                            mkdir -p "$(dirname "${FRONTEND_DEPLOY_PATH}")"
                            GIT_AUTH_HEADER=$(printf '%s:%s' "$GITLAB_USER" "$GITLAB_TOKEN" | base64 -w0)

                            if [ ! -d "${FRONTEND_DEPLOY_PATH}/.git" ]; then
                                echo -e "${YELLOW}📦 前端仓库不存在，开始克隆...${NC}"
                                rm -rf "${FRONTEND_DEPLOY_PATH}"
                                git -c http.extraHeader="Authorization: Basic ${GIT_AUTH_HEADER}" \
                                    clone "${FRONTEND_REPO_URL}" "${FRONTEND_DEPLOY_PATH}"
                                PREVIOUS_FRONTEND_COMMIT=""
                            else
                                PREVIOUS_FRONTEND_COMMIT=$(git -C "${FRONTEND_DEPLOY_PATH}" rev-parse HEAD)
                                git -C "${FRONTEND_DEPLOY_PATH}" remote set-url origin "${FRONTEND_REPO_URL}"
                            fi

                            FRONTEND_TARGET_BRANCH="${FRONTEND_DEPLOY_BRANCH}"
                            if git -C "${FRONTEND_DEPLOY_PATH}" -c http.extraHeader="Authorization: Basic ${GIT_AUTH_HEADER}" \
                                ls-remote --exit-code --heads origin "${CI_SOURCE_BRANCH}" >/dev/null 2>&1; then
                                FRONTEND_TARGET_BRANCH="${CI_SOURCE_BRANCH}"
                            fi

                            git -C "${FRONTEND_DEPLOY_PATH}" -c http.extraHeader="Authorization: Basic ${GIT_AUTH_HEADER}" fetch origin
                            git -C "${FRONTEND_DEPLOY_PATH}" checkout --detach "origin/${FRONTEND_TARGET_BRANCH}"
                            echo "📌 前端目标分支: ${FRONTEND_TARGET_BRANCH}"
                            echo -e "${GREEN}📌 前端新提交: $(git -C "${FRONTEND_DEPLOY_PATH}" log -1 --oneline)${NC}"

                            if [ ! -f "${FRONTEND_DEPLOY_PATH}/package.json" ]; then
                                echo -e "${RED}❌ 前端源码目录异常: ${FRONTEND_DEPLOY_PATH}${NC}"
                                rollback_deploy
                                exit 1
                            fi

                            echo -e "${GREEN}🔧 配置 Docker 镜像加速器...${NC}"
                            if [ ! -f /etc/docker/daemon.json ] || ! grep -q "docker.happyjack.cn" /etc/docker/daemon.json; then
                                sudo mkdir -p /etc/docker
                                sudo tee /etc/docker/daemon.json > /dev/null << 'DOCKER_EOF'
{
  "registry-mirrors": ["https://docker.happyjack.cn"]
}
DOCKER_EOF
                                sudo systemctl restart docker
                                sleep 5
                                echo -e "${GREEN}✅ Docker 镜像加速器配置完成${NC}"
                            else
                                echo -e "${YELLOW}⏭️  Docker 镜像加速器已配置，跳过${NC}"
                            fi

                            echo -e "${GREEN}🗄️  确保测试基础设施在线...${NC}"
                            docker compose \
                                -f "${DEPLOY_COMPOSE_FILE}" \
                                -f "${DEPLOY_FRONTEND_COMPOSE_FILE}" \
                                --env-file "${DEPLOY_ENV_FILE}" \
                                --profile infra up -d ${TEST_INFRA_SERVICES} || {
                                echo -e "${RED}❌ 测试基础设施启动失败${NC}"
                                rollback_deploy
                                exit 1
                            }

                            echo -e "${GREEN}⚙️  启动测试应用服务...${NC}"
                            docker compose \
                                -f "${DEPLOY_COMPOSE_FILE}" \
                                -f "${DEPLOY_FRONTEND_COMPOSE_FILE}" \
                                --env-file "${DEPLOY_ENV_FILE}" \
                                up -d --build --force-recreate ${TEST_APP_SERVICES} || {
                                echo -e "${RED}❌ 容器启动失败${NC}"
                                rollback_deploy
                                exit 1
                            }

                            echo -e "${GREEN}⏳ 等待容器启动...${NC}"
                            sleep 15

                            echo -e "${GREEN}🗄️  运行数据库迁移...${NC}"
                            docker compose \
                                -f "${DEPLOY_COMPOSE_FILE}" \
                                -f "${DEPLOY_FRONTEND_COMPOSE_FILE}" \
                                --env-file "${DEPLOY_ENV_FILE}" \
                                exec -T api alembic upgrade head || {
                                echo -e "${YELLOW}⚠️  数据库迁移失败或已跳过${NC}"
                            }

                            NGINX_PORT=$(grep -E '^NGINX_HTTP_PORT=' "${DEPLOY_ENV_FILE}" | tail -n 1 | cut -d= -f2)
                            NGINX_PORT=${NGINX_PORT:-80}
                            NGINX_BASE_URL="http://localhost:${NGINX_PORT}"

                            echo -e "${GREEN}🏥 健康检查...${NC}"
                            RETRY_COUNT=0
                            MAX_RETRIES=${HEALTH_CHECK_RETRIES}
                            HEALTH_CHECK_PASSED=false

                            while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
                                if docker compose \
                                    -f "${DEPLOY_COMPOSE_FILE}" \
                                    -f "${DEPLOY_FRONTEND_COMPOSE_FILE}" \
                                    --env-file "${DEPLOY_ENV_FILE}" \
                                    exec -T api curl -f -s -o /dev/null -w "%{http_code}" "${HEALTH_CHECK_URL}" | grep -q "200" \
                                    && curl -f -s -o /dev/null -w "%{http_code}" "${NGINX_BASE_URL}/health" | grep -q "200" \
                                    && curl -f -s -o /dev/null -w "%{http_code}" "${NGINX_BASE_URL}/" | grep -q "200"; then
                                    echo -e "${GREEN}✅ 前后端链路健康检查通过 (尝试 $((RETRY_COUNT + 1))/$MAX_RETRIES)${NC}"
                                    HEALTH_CHECK_PASSED=true
                                    break
                                else
                                    RETRY_COUNT=$((RETRY_COUNT + 1))
                                    echo -e "${YELLOW}⏳ 链路健康检查失败，等待重试... ($RETRY_COUNT/$MAX_RETRIES)${NC}"
                                    sleep 5
                                fi
                            done

                            if [ "$HEALTH_CHECK_PASSED" = false ]; then
                                echo -e "${RED}❌ 链路健康检查失败，开始回滚...${NC}"
                                docker compose \
                                    -f "${DEPLOY_COMPOSE_FILE}" \
                                    -f "${DEPLOY_FRONTEND_COMPOSE_FILE}" \
                                    --env-file "${DEPLOY_ENV_FILE}" \
                                    logs --tail=120 frontend nginx api celery_worker
                                rollback_deploy
                                exit 1
                            fi

                            echo -e "${GREEN}🧹 清理未使用的 Docker 资源...${NC}"
                            docker system prune -f --volumes || true

                            echo -e "${GREEN}✅ 测试环境部署完成${NC}"
                            echo -e "${GREEN}📊 容器状态：${NC}"
                            docker compose \
                                -f "${DEPLOY_COMPOSE_FILE}" \
                                -f "${DEPLOY_FRONTEND_COMPOSE_FILE}" \
                                --env-file "${DEPLOY_ENV_FILE}" \
                                --profile "${DEPLOY_PROFILE}" ps

                            echo -e "${GREEN}📈 资源使用情况：${NC}"
                            docker stats --no-stream --format "table {{.Name}}\\t{{.CPUPerc}}\\t{{.MemUsage}}"
                        '''
                    }

                    echo '✅ 部署成功'
                }
            }
        }
    }

    // ==============================================
    // 后置处理
    // ==============================================
    post {
        always {
            script {
                if (env.CI_IMAGE?.trim()) {
                    sh '''
                        docker image rm -f ${CI_IMAGE} >/dev/null 2>&1 || true
                    '''
                }
            }
            // 清理工作空间
            cleanWs()
        }
        success {
            echo '✅ Pipeline 执行成功'
        }
        failure {
            echo '❌ Pipeline 执行失败'
        }
    }
}

// ==============================================
// Jenkins 配置说明
// ==============================================
// 必需的 Jenkins 插件:
//   - GitLab Plugin
//   - JUnit Plugin
//   - Git Plugin
//
// 可选插件:
//   - HTML Publisher Plugin（用于 Jenkins 页面展示覆盖率 HTML 报告）
//
// 必需的配置:
//   1. Jenkins Node (192.168.0.221) 已配置并在线
//   2. Node 标签: WES
//   3. GitLab HTTP 凭据已配置（ID: gitlab-http-creds）
//
// 必需的服务（在 Jenkins Node 上）:
//   - PostgreSQL (Docker 容器)
//   - Redis (Docker 容器)
//   - Docker 和 Docker Compose
//
// 触发方式:
//   1. GitLab Webhook（推荐）
//   2. 手动触发: Jenkins → wes-backend → Build Now
//
// 部署流程:
//   1. GitLab 推送代码触发 Webhook
//   2. Jenkins 在 Node (192.168.0.221) 上执行构建
//   3. 安装依赖
//   4. 并行执行代码检查和测试
//   5. 在本地（Node）部署到测试环境
//   6. 拉取最新代码
//   7. 构建 Docker 镜像
//   8. 停止旧容器，启动新容器
//   9. 运行数据库迁移
//   10. 健康检查（5 次重试）
//   11. 失败时自动回滚到上一个版本
//
// 优势:
//   - 无需 SSH 连接（直接在 Node 上执行）
//   - 更快的部署速度
//   - 更简单的配置
//   - 更好的日志输出
//
// 注意事项:
//   - 确保 Jenkins Node 标签正确（agent { label 'test-node' }）
//   - 确保 Node 上已安装 uv、Docker、Docker Compose
//   - 确保 /opt/wes_backend 目录已初始化
//   - 确保 .env.test 文件已配置
//   - 生产环境部署需要单独配置
