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
        // 部署配置（在 Jenkins Node 上执行拉镜像部署）
        DEPLOY_PATH = '/opt/wes_backend'
        DEPLOY_COMPOSE_FILE = 'docker-compose.deploy.yml'
        HEALTH_CHECK_RETRIES = '5'
        // 镜像配置
        CI_BUILD_TARGET = 'testing'
        RUNTIME_BUILD_TARGET = 'production'
        REGISTRY_HOST = '192.168.0.220:5050'
        IMAGE_NAMESPACE = 'wes/wes_backend'
        REGISTRY_CREDENTIALS_ID = 'gitlab-registry-creds'
    }

    // 构建选项
    options {
        // 保留最近 10 次构建
        buildDiscarder(logRotator(numToKeepStr: '10'))
        // 超时配置
        timeout(time: 30, unit: 'MINUTES')
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
                    String sourceBranch = env.gitlabSourceBranch ?: env.gitlabBranch ?: env.BRANCH_NAME ?: 'develop'
                    String targetBranch = env.gitlabTargetBranch ?: ''
                    boolean isMergeRequest = env.GITLAB_OBJECT_KIND == 'merge_request'

                    echo "📥 检出源码: source=${sourceBranch}, target=${targetBranch ?: '-'}, event=${env.GITLAB_OBJECT_KIND ?: 'manual'}"

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

                    String shortCommit = sh(returnStdout: true, script: 'git rev-parse --short HEAD').trim()
                    env.SOURCE_BRANCH = sourceBranch
                    env.CI_IMAGE = "wes-backend-ci:${env.BUILD_NUMBER}-${shortCommit}"
                    env.IMAGE_REPOSITORY = "${env.REGISTRY_HOST}/${env.IMAGE_NAMESPACE}"
                    env.IMMUTABLE_IMAGE_TAG = "${sourceBranch}-${env.BUILD_NUMBER}-${shortCommit}".replaceAll(/[^A-Za-z0-9_.-]/, '-')
                    env.PUBLISH_IMAGE = 'false'
                    env.DEPLOY_ENABLED = 'false'
                    env.CHANNEL_IMAGE_TAG = ''
                    env.DEPLOY_NAME = ''
                    env.DEPLOY_ENV_FILE = ''
                    env.DEPLOY_SERVICES = ''
                    env.DEPLOY_CONTAINER_NAME = ''
                    env.DEPLOY_REQUIRED_CONTAINERS = ''
                    env.RUNTIME_IMAGE = ''
                    env.CHANNEL_IMAGE = ''

                    switch (sourceBranch) {
                        case 'develop':
                            env.PUBLISH_IMAGE = 'true'
                            env.DEPLOY_ENABLED = 'true'
                            env.CHANNEL_IMAGE_TAG = 'develop'
                            env.DEPLOY_NAME = 'testing'
                            env.DEPLOY_ENV_FILE = '.env.test'
                            env.DEPLOY_SERVICES = 'api celery_worker'
                            env.DEPLOY_CONTAINER_NAME = 'wes_api_test'
                            env.DEPLOY_REQUIRED_CONTAINERS = 'wes_postgres_test wes_redis_test'
                            break
                        case 'main':
                            env.PUBLISH_IMAGE = 'true'
                            env.DEPLOY_ENABLED = 'true'
                            env.CHANNEL_IMAGE_TAG = 'prod'
                            env.DEPLOY_NAME = 'production'
                            env.DEPLOY_ENV_FILE = '.env.prod'
                            env.DEPLOY_SERVICES = 'api celery_worker celery_beat flower'
                            env.DEPLOY_CONTAINER_NAME = 'wes_api_prod'
                            env.DEPLOY_REQUIRED_CONTAINERS = 'wes_postgres_prod wes_redis_prod'
                            break
                    }

                    if (env.PUBLISH_IMAGE == 'true') {
                        env.RUNTIME_IMAGE = "${env.IMAGE_REPOSITORY}:${env.IMMUTABLE_IMAGE_TAG}"
                        env.CHANNEL_IMAGE = "${env.IMAGE_REPOSITORY}:${env.CHANNEL_IMAGE_TAG}"
                    }

                    echo "🐳 CI 镜像标签: ${env.CI_IMAGE}"
                    echo "📦 发布策略: publish=${env.PUBLISH_IMAGE}, branch=${env.SOURCE_BRANCH}, immutable=${env.RUNTIME_IMAGE ?: '-'}, channel=${env.CHANNEL_IMAGE ?: '-'}"
                    echo "🚀 部署策略: enabled=${env.DEPLOY_ENABLED}, target=${env.DEPLOY_NAME ?: '-'}, services=${env.DEPLOY_SERVICES ?: '-'}"
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
                                    sh -lc 'ruff format --check .'
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
                                    sh -lc 'ruff check .'
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
                                    sh -lc '
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
                                    -v "$WORKSPACE/reports:/artifacts/reports" \
                                    ${CI_IMAGE} \
                                    sh -lc '
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
                            publishHTML([
                                allowMissing: false,
                                alwaysLinkToLastBuild: true,
                                keepAll: true,
                                reportDir: 'reports/coverage',
                                reportFiles: 'index.html',
                                reportName: 'Coverage Report'
                            ])
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
                                    ${CI_IMAGE} \
                                    sh -lc 'pytest tests/api/test_signature.py -v --tb=short'
                            '''
                            echo '✅ API 签名测试通过'
                        }
                    }
                }
            }
        }

        // ==============================================
        // 构建运行时镜像：仅正式分支执行
        // ==============================================
        stage('Build Runtime Image') {
            when {
                expression {
                    env.PUBLISH_IMAGE == 'true'
                }
            }
            steps {
                script {
                    echo "🐳 构建运行时镜像: ${env.RUNTIME_IMAGE}"
                    sh '''
                        set -e
                        docker build \
                            --target ${RUNTIME_BUILD_TARGET} \
                            -t ${RUNTIME_IMAGE} \
                            -t ${CHANNEL_IMAGE} \
                            .
                    '''
                    echo '✅ 运行时镜像构建完成'
                }
            }
        }

        // ==============================================
        // 推送运行时镜像：仅 develop/main 执行
        // ==============================================
        stage('Publish Runtime Image') {
            when {
                expression {
                    env.PUBLISH_IMAGE == 'true'
                }
            }
            steps {
                script {
                    echo "📤 推送镜像到仓库: ${env.RUNTIME_IMAGE} / ${env.CHANNEL_IMAGE}"
                    withCredentials([
                        usernamePassword(
                            credentialsId: "${env.REGISTRY_CREDENTIALS_ID}",
                            usernameVariable: 'REGISTRY_USERNAME',
                            passwordVariable: 'REGISTRY_PASSWORD'
                        )
                    ]) {
                        sh '''
                            set -e
                            trap 'docker logout ${REGISTRY_HOST} >/dev/null 2>&1 || true' EXIT
                            echo "$REGISTRY_PASSWORD" | docker login ${REGISTRY_HOST} -u "$REGISTRY_USERNAME" --password-stdin
                            docker push ${RUNTIME_IMAGE}
                            docker push ${CHANNEL_IMAGE}
                        '''
                    }
                    echo '✅ 镜像推送完成'
                }
            }
        }

        // ==============================================
        // 部署阶段：仅正式分支执行拉镜像自动部署
        // ==============================================
        stage('Deploy Runtime') {
            when {
                expression {
                    env.DEPLOY_ENABLED == 'true'
                }
            }
            steps {
                script {
                    echo "🚀 开始部署到${env.DEPLOY_NAME}: ${env.RUNTIME_IMAGE}"

                    withCredentials([
                        usernamePassword(
                            credentialsId: "${env.REGISTRY_CREDENTIALS_ID}",
                            usernameVariable: 'REGISTRY_USERNAME',
                            passwordVariable: 'REGISTRY_PASSWORD'
                        )
                    ]) {
                        sh '''
                            set -e
                            set -o pipefail

                            RED='\\033[0;31m'
                            GREEN='\\033[0;32m'
                            YELLOW='\\033[1;33m'
                            NC='\\033[0m'

                            echo -e "${GREEN}📂 切换到项目目录...${NC}"
                            cd ${DEPLOY_PATH}

                            echo -e "${GREEN}📥 更新部署清单...${NC}"
                            PREVIOUS_COMMIT=$(git rev-parse HEAD)
                            PREVIOUS_IMAGE=$(docker inspect -f '{{.Config.Image}}' ${DEPLOY_CONTAINER_NAME} 2>/dev/null || true)
                            echo "📌 当前提交: $PREVIOUS_COMMIT"
                            echo "📦 当前镜像: ${PREVIOUS_IMAGE:-<none>}"

                            git fetch origin
                            git checkout ${GIT_COMMIT}
                            echo -e "${GREEN}📌 新提交: $(git log -1 --oneline)${NC}"

                            export BACKEND_ENV_FILE=${DEPLOY_ENV_FILE}
                            export BACKEND_IMAGE=${RUNTIME_IMAGE}
                            COMPOSE_CMD="docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE}"
                            HEALTH_ENDPOINT='http://127.0.0.1:8001/api/v1/performance/health'

                            trap 'docker logout ${REGISTRY_HOST} >/dev/null 2>&1 || true' EXIT
                            echo "$REGISTRY_PASSWORD" | docker login ${REGISTRY_HOST} -u "$REGISTRY_USERNAME" --password-stdin

                            echo -e "${GREEN}🧱 检查基础设施容器状态...${NC}"
                            for required_container in ${DEPLOY_REQUIRED_CONTAINERS}; do
                                if ! docker inspect "$required_container" >/dev/null 2>&1; then
                                    echo -e "${RED}❌ 缺少基础设施容器: $required_container${NC}"
                                    echo -e "${YELLOW}ℹ️  热修部署不会自动创建或升级基础设施，请先单独初始化 infra${NC}"
                                    exit 1
                                fi

                                if [ "$(docker inspect -f '{{.State.Running}}' "$required_container")" != "true" ]; then
                                    echo -e "${RED}❌ 基础设施容器未运行: $required_container${NC}"
                                    exit 1
                                fi

                                container_health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$required_container")
                                if [ "$container_health" != "healthy" ] && [ "$container_health" != "none" ]; then
                                    echo -e "${RED}❌ 基础设施容器不健康: $required_container ($container_health)${NC}"
                                    exit 1
                                fi
                            done

                            echo -e "${GREEN}📥 拉取目标镜像...${NC}"
                            docker pull ${BACKEND_IMAGE}

                            echo -e "${GREEN}⚙️  启动新容器...${NC}"
                            $COMPOSE_CMD up -d --no-build --no-deps ${DEPLOY_SERVICES} || {
                                echo -e "${RED}❌ 容器启动失败${NC}"
                                exit 1
                            }

                            echo -e "${GREEN}⏳ 等待容器启动...${NC}"
                            sleep 15

                            echo -e "${GREEN}🗄️  运行数据库迁移...${NC}"
                            $COMPOSE_CMD exec -T api alembic upgrade head

                            echo -e "${GREEN}🏥 健康检查...${NC}"
                            RETRY_COUNT=0
                            MAX_RETRIES=${HEALTH_CHECK_RETRIES}
                            HEALTH_CHECK_PASSED=false

                            while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
                                if docker exec ${DEPLOY_CONTAINER_NAME} curl -f -s -o /dev/null -w "%{http_code}" ${HEALTH_ENDPOINT} | grep -q "200"; then
                                    echo -e "${GREEN}✅ 健康检查通过 (尝试 $((RETRY_COUNT + 1))/$MAX_RETRIES)${NC}"
                                    HEALTH_CHECK_PASSED=true
                                    break
                                fi

                                RETRY_COUNT=$((RETRY_COUNT + 1))
                                echo -e "${YELLOW}⏳ 健康检查失败，等待重试... ($RETRY_COUNT/$MAX_RETRIES)${NC}"
                                sleep 5
                            done

                            if [ "$HEALTH_CHECK_PASSED" = false ]; then
                                echo -e "${RED}❌ 健康检查失败，开始回滚...${NC}"
                                $COMPOSE_CMD logs --tail=100 api

                                if [ -n "$PREVIOUS_IMAGE" ]; then
                                    echo -e "${YELLOW}🔄 回滚镜像: $PREVIOUS_IMAGE${NC}"
                                    export BACKEND_IMAGE="$PREVIOUS_IMAGE"
                                    docker pull ${BACKEND_IMAGE} || true
                                    $COMPOSE_CMD up -d --no-build --no-deps ${DEPLOY_SERVICES}
                                else
                                    echo -e "${YELLOW}⚠️  未找到可回滚镜像，跳过回滚${NC}"
                                fi

                                git checkout $PREVIOUS_COMMIT || true
                                exit 1
                            fi

                            echo -e "${GREEN}✅ ${DEPLOY_NAME} 部署完成${NC}"
                            $COMPOSE_CMD ps
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
//   - HTML Publisher Plugin
//   - Git Plugin
//
// 必需的配置:
//   1. Jenkins Node (192.168.0.221) 已配置并在线
//   2. Node 标签: WES
//   3. GitLab HTTP 凭据已配置（ID: gitlab-http-creds）
//   4. Docker Registry 凭据已配置（ID: gitlab-registry-creds）
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
//   2. Jenkins 在 Node (192.168.0.221) 上构建 CI 测试镜像
//   3. 并行执行代码检查和测试
//   4. develop 分支推送 immutable + develop 镜像并自动部署 testing
//   5. main 分支推送 immutable + prod 镜像并自动部署 production
//   6. 部署机更新部署清单后拉取镜像并以 --no-build 方式启动服务
//   7. 运行数据库迁移
//   8. 健康检查（5 次重试）
//   9. 失败时自动回滚到上一个镜像版本
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
