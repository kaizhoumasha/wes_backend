#!/bin/bash
# ============================================
# CI/CD 部署服务器初始化脚本
# ============================================
# 用途: 在 220 和 221 服务器上初始化部署环境
# 使用: ./scripts/init-deploy-servers.sh [test|prod|both]
# ============================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[✓]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }

# 配置
TEST_HOST="192.168.0.221"
PROD_HOST="192.168.0.220"
DEPLOY_USER="root"  # 根据实际情况修改
DEPLOY_PATH="/opt/wes_backend"
PROJECT_NAME="P9 WES Backend"

# 显示帮助
show_help() {
    cat << EOF
CI/CD 部署服务器初始化脚本

用法: $0 [环境] [选项]

环境:
  test        初始化测试服务器 (221)
  prod        初始化生产服务器 (220)
  both        初始化两台服务器

选项:
  --skip-docker  跳过 Docker 安装
  --user <name>  指定 SSH 用户 (默认: root)
  --path <path>  指定部署路径 (默认: /opt/wes_backend)

示例:
  $0 test                    # 初始化测试服务器
  $0 prod --user deploy      # 使用 deploy 用户初始化生产服务器
  $0 both --skip-docker      # 初始化两台服务器（跳过 Docker 安装）

此脚本将:
  1. 检查服务器连接
  2. 安装 Docker 和 Docker Compose
  3. 创建项目目录
  4. 配置 SSH 密钥认证
  5. 复制环境配置文件

EOF
}

# 检查 SSH 连接
check_ssh_connection() {
    local host=$1
    local user=$2

    print_info "检查 $host 的 SSH 连接..."

    if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "${user}@${host}" "echo '连接成功'" > /dev/null 2>&1; then
        print_success "SSH 连接正常: $host"
        return 0
    else
        print_error "SSH 连接失败: $host"
        return 1
    fi
}

# 初始化服务器
init_server() {
    local host=$1
    local env=$2
    local user=$3
    local skip_docker=$4

    print_info "========== 初始化 ${env} 环境 ($host) =========="

    # 检查 SSH 连接
    if ! check_ssh_connection "$host" "$user"; then
        print_error "无法连接到 $host，请检查网络和 SSH 配置"
        return 1
    fi

    # 远程执行初始化命令
    ssh "${user}@${host}" << EOSSH
        set -e

        # 检测操作系统类型
        if [ -f /etc/os-release ]; then
            . /etc/os-release
            OS=\$ID
            echo "📋 检测到操作系统: \$OS"
        else
            echo "⚠️  无法检测操作系统类型"
            OS="unknown"
        fi

        # 安装 Docker（如果需要）
        if [ "$skip_docker" != "true" ]; then
            if ! command -v docker &> /dev/null; then
                echo "🐳 安装 Docker..."

                # 使用 Docker 官方安装脚本（支持多种发行版）
                curl -fsSL https://get.docker.com -o get-docker.sh
                sh get-docker.sh
                rm get-docker.sh

                systemctl enable docker
                systemctl start docker
                echo "✅ Docker 安装完成"
            else
                echo "✅ Docker 已安装"
            fi
        fi

        # 创建项目目录
        echo "📁 创建项目目录..."
        mkdir -p $DEPLOY_PATH/{logs,backups,docker_data,reports}

        # 创建 docker-compose.override.yml（用于特定环境配置）
        if [ ! -f "$DEPLOY_PATH/docker-compose.override.yml" ]; then
            cat > $DEPLOY_PATH/docker-compose.override.yml << 'YAML'
# 环境特定覆盖配置
# 此文件会自动覆盖 docker-compose.yml 中的配置
services:
  api:
    ports:
      - "8001:8001"  # 根据需要调整
YAML
            echo "✅ 创建 docker-compose.override.yml"
        fi

        # 复制环境配置文件（如果存在）
        if [ -f ".env.$env" ]; then
            echo "📄 复制环境配置文件..."
            cp ".env.$env" "$DEPLOY_PATH/.env.$env"
            echo "✅ 环境配置已复制"
        else
            echo "⚠️  警告: .env.$env 文件不存在，请手动创建"
        fi

        # 设置权限
        chown -R $user:$user $DEPLOY_PATH
        chmod -R 755 $DEPLOY_PATH

        echo "✅ $env 环境初始化完成"
EOSSH

    print_success "${env} 环境初始化完成: $host"
}

# 生成 SSH 密钥对
generate_ssh_key() {
    local key_name=$1
    local key_file="$HOME/.ssh/${key_name}_rsa"

    if [ -f "$key_file" ]; then
        print_warning "SSH 密钥已存在: $key_file"
        return 0
    fi

    print_info "生成 SSH 密钥对: $key_file"
    ssh-keygen -t rsa -b 4096 -f "$key_file" -N "" -C "gitlab-ci-${key_name}"
    print_success "SSH 密钥已生成: $key_file"
    print_info "私钥内容 (请复制到 GitLab CI/CD Variables):"
    cat "${key_file}"

    print_info "公钥内容 (请添加到服务器 ~/.ssh/authorized_keys):"
    cat "${key_file}.pub"
}

# 配置 SSH 公钥认证
configure_ssh_auth() {
    local host=$1
    local env=$2
    local user=$3
    local key_name="gitlab_${env}"
    local key_file="$HOME/.ssh/${key_name}_rsa"

    print_info "配置 SSH 公钥认证..."

    # 检查私钥是否存在
    if [ ! -f "$key_file" ]; then
        print_error "SSH 私钥不存在: $key_file"
        print_info "请先运行: $0 $env --generate-key"
        return 1
    fi

    # 复制公钥到服务器
    print_info "复制公钥到 $host..."
    ssh-copy-id -i "${key_file}.pub" "${user}@${host}"

    print_success "SSH 公钥已配置"
    print_info "请将以下私钥添加到 GitLab CI/CD Variables:"
    print_info "变量名: SSH_PRIVATE_KEY_${env^^}"
    cat "$key_file"
}

# 显示配置说明
show_config_instructions() {
    cat << EOF

===========================================
📋 GitLab CI/CD 配置说明
===========================================

1. 在 GitLab 项目中配置 CI/CD Variables:
   路径: Settings → CI/CD → Variables

   需要配置的变量:
   ┌─────────────────────────────────────────────────────────────┐
   │ 变量名                    │ 类型   │ 保护 │ 说明           │
   ├─────────────────────────────────────────────────────────────┤
   │ SSH_PRIVATE_KEY_TEST      │ File   │ Yes  │ 测试服务器 SSH │
   │ SSH_PRIVATE_KEY_PROD      │ File   │ Yes  │ 生产服务器 SSH │
   └─────────────────────────────────────────────────────────────┘

2. SSH 私钥生成方法:
   $ ssh-keygen -t rsa -b 4096 -f ~/.ssh/gitlab_test_rsa
   $ ssh-keygen -t rsa -b 4096 -f ~/.ssh/gitlab_prod_rsa

3. 复制公钥到服务器:
   $ ssh-copy-id -i ~/.ssh/gitlab_test_rsa.pub root@192.168.0.221
   $ ssh-copy-id -i ~/.ssh/gitlab_prod_rsa.pub root@192.168.0.220

4. 测试 SSH 连接:
   $ ssh -i ~/.ssh/gitlab_test_rsa root@192.168.0.221
   $ ssh -i ~/.ssh/gitlab_prod_rsa root@192.168.0.220

5. 在 GitLab Runner 中安装 Docker:
   # 在 GitLab Runner 服务器 (220) 上
   $ apt-get install docker-ce docker-ce-cli containerd.io docker-compose-plugin

6. 注册 GitLab Runner (如果未注册):
   $ gitlab-runner register \
     --url https://gitlab.example.com \
     --registration-token YOUR_TOKEN \
     --executor docker \
     --description "Docker Runner" \
     --docker-image "docker:27-dind" \
     --docker-privileged \
     --tag-list "docker"

===========================================

EOF
}

# 主函数
main() {
    if [ $# -lt 1 ]; then
        show_help
        exit 1
    fi

    local env=$1
    shift

    local user=$DEPLOY_USER
    local skip_docker=false
    local generate_key=false
    local configure_auth=false

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case $1 in
            --user)
                user="$2"
                shift 2
                ;;
            --path)
                DEPLOY_PATH="$2"
                shift 2
                ;;
            --skip-docker)
                skip_docker=true
                shift
                ;;
            --generate-key)
                generate_key=true
                shift
                ;;
            --configure-auth)
                configure_auth=true
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                print_error "未知选项: $1"
                show_help
                exit 1
                ;;
        esac
    done

    print_info "========== $PROJECT_NAME 部署服务器初始化 =========="

    # 生成 SSH 密钥
    if [ "$generate_key" = true ]; then
        case $env in
            test)
                generate_ssh_key "gitlab_test"
                ;;
            prod)
                generate_ssh_key "gitlab_prod"
                ;;
            both)
                generate_ssh_key "gitlab_test"
                generate_ssh_key "gitlab_prod"
                ;;
        esac
        exit 0
    fi

    # 配置 SSH 认证
    if [ "$configure_auth" = true ]; then
        case $env in
            test)
                configure_ssh_auth "$TEST_HOST" "test" "$user"
                ;;
            prod)
                configure_ssh_auth "$PROD_HOST" "prod" "$user"
                ;;
            both)
                configure_ssh_auth "$TEST_HOST" "test" "$user"
                configure_ssh_auth "$PROD_HOST" "prod" "$user"
                ;;
        esac
        exit 0
    fi

    # 初始化服务器
    case $env in
        test)
            init_server "$TEST_HOST" "test" "$user" "$skip_docker"
            ;;
        prod)
            init_server "$PROD_HOST" "prod" "$user" "$skip_docker"
            ;;
        both)
            init_server "$TEST_HOST" "test" "$user" "$skip_docker"
            init_server "$PROD_HOST" "prod" "$user" "$skip_docker"
            ;;
        *)
            print_error "无效环境: $env"
            show_help
            exit 1
            ;;
    esac

    # 显示配置说明
    show_config_instructions

    print_success "========== 初始化完成 =========="
}

# 运行主函数
main "$@"
