#!/bin/bash

# 性能测试运行脚本
# 使用方法: ./scripts/run_performance_test.sh [test_type]

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的信息
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查服务是否运行
check_server() {
    print_info "检查服务器状态..."
    if curl -s http://localhost:8001/api/v1/performance/health > /dev/null; then
        print_success "服务器运行正常"
        return 0
    else
        print_error "服务器未运行或无法访问"
        print_info "请先启动服务器: uvicorn main:app --reload"
        exit 1
    fi
}

# 重置测试数据
reset_test_data() {
    print_info "重置测试数据..."
    curl -s -X POST http://localhost:8001/api/v1/performance/load-test/reset
    print_success "测试数据已重置"
}

# 运行健康检查
health_check() {
    print_info "执行健康检查..."
    response=$(curl -s http://localhost:8001/api/v1/performance/health)
    if command -v jq &> /dev/null; then
        echo "$response" | jq .
    else
        echo "$response" | python3 -m json.tool
    fi
}

# 获取性能指标
get_metrics() {
    print_info "获取性能指标..."
    response=$(curl -s http://localhost:8001/api/v1/performance/metrics)
    if command -v jq &> /dev/null; then
        echo "$response" | jq .
    else
        echo "$response" | python3 -m json.tool
    fi
}

# 运行 Locust 测试
run_locust_test() {
    print_info "启动 Locust 负载测试..."

    # 检查 locust 是否安装
    if ! command -v locust &> /dev/null; then
        print_warning "Locust 未安装，正在安装..."
        pip install locust
    fi

    # 检查参数
    if [ -z "$1" ]; then
        # 无参数：启动 Web UI
        print_info "访问 Locust Web UI: http://localhost:8089"
        print_info "推荐配置："
        print_info "  - 用户数: 100"
        print_info "  - 产生速率: 10 用户/秒"
        print_info "  - 运行时间: 1 分钟"
        locust -f tests/load/locustfile.py --host=http://localhost:8001
    else
        # 有参数：无头模式运行
        print_info "无头模式运行负载测试..."
        locust -f tests/load/locustfile.py \
            --host=http://localhost:8001 \
            --headless \
            --users=$1 \
            --spawn-rate=$2 \
            --run-time=$3 \
            --html=reports/locust_report.html \
            --csv=reports/locust_stats
        print_success "测试报告已生成: reports/locust_report.html"
    fi
}

# 运行简单压力测试（使用 ab）
run_apache_bench() {
    print_info "运行 Apache Bench 压力测试..."

    # 检查 ab 是否安装
    if ! command -v ab &> /dev/null; then
        print_error "Apache Bench (ab) 未安装"
        print_info "安装方法: brew install httpd"  # macOS
        return 1
    fi

    # 参数：请求数 并发数
    local requests=${1:-1000}
    local concurrency=${2:-10}

    print_info "测试配置: $requests 请求, $concurrency 并发"

    # 测试用户列表接口
    print_info "测试用户列表接口..."
    ab -n $requests -c $concurrency -g reports/ab_plot.tsv \
       http://localhost:8001/api/v1/users?page=1\&page_size=10

    print_success "测试完成，数据已保存到: reports/ab_plot.tsv"
}

# 运行并发测试
run_concurrent_test() {
    print_info "运行并发请求测试..."

    # 检测是否在 UV 环境中
    _python_cmd="python3"
    if command -v uv &> /dev/null && [ -f "pyproject.toml" ]; then
        _python_cmd="uv run python"
    fi

    # 创建测试脚本
    cat > /tmp/concurrent_test.py << 'EOF'
import asyncio
import time
import httpx

async def test_concurrent_requests():
    """并发请求测试"""
    base_url = "http://localhost:8001"

    async with httpx.AsyncClient() as client:
        # 测试不同并发级别
        for concurrency in [10, 50, 100, 200]:
            print(f"\n{'='*50}")
            print(f"并发级别: {concurrency}")
            print(f"{'='*50}")

            start = time.time()

            async def make_request(url):
                try:
                    response = await client.get(url)
                    return response.status_code, time.time()
                except Exception as e:
                    return None, time.time()

            start_time = time.time()
            tasks = [
                make_request(f"{base_url}/api/v1/users")
                for _ in range(concurrency)
            ]

            results = await asyncio.gather(*tasks)
            total_time = time.time() - start_time

            success = sum(1 for r in results if r[0] == 200)
            elapsed = [r[1] - start_time for r in results if r[0]]

            print(f"成功请求: {success}/{concurrency}")
            print(f"总耗时: {total_time:.2f}s")
            print(f"RPS: {concurrency/total_time:.2f}")
            if elapsed:
                print(f"平均响应时间: {sum(elapsed)/len(elapsed)*1000:.2f}ms")

if __name__ == "__main__":
    asyncio.run(test_concurrent_requests())
EOF

    $_python_cmd /tmp/concurrent_test.py
    rm /tmp/concurrent_test.py
}

# 运行完整性能测试套件
run_full_test() {
    print_info "运行完整性能测试套件..."
    echo ""

    # 1. 健康检查
    print_info "1. 健康检查"
    health_check
    echo ""

    # 2. 重置数据
    print_info "2. 重置测试数据"
    reset_test_data
    echo ""

    # 3. 获取初始指标
    print_info "3. 获取初始性能指标"
    get_metrics
    echo ""

    # 4. 并发测试
    print_info "4. 并发请求测试"
    run_concurrent_test
    echo ""

    # 5. 生成最终报告
    print_info "5. 生成最终报告"
    get_metrics

    print_success "性能测试套件执行完成"
}

# 创建报告目录
mkdir -p reports

# 解析命令行参数
case "${1:-help}" in
    health)
        check_server
        health_check
        ;;
    metrics)
        check_server
        get_metrics
        ;;
    reset)
        check_server
        reset_test_data
        ;;
    locust)
        check_server
        reset_test_data
        run_locust_test $2 $3 $4
        ;;
    locust-ui)
        check_server
        reset_test_data
        run_locust_test
        ;;
    ab)
        check_server
        run_apache_bench $2 $3
        ;;
    concurrent)
        check_server
        run_concurrent_test
        ;;
    full)
        check_server
        run_full_test
        ;;
    help|*)
        echo "性能测试脚本"
        echo ""
        echo "使用方法: $0 [command] [options]"
        echo ""
        echo "命令:"
        echo "  health              - 健康检查"
        echo "  metrics             - 获取性能指标"
        echo "  reset               - 重置测试数据"
        echo "  locust-ui           - 启动 Locust Web UI"
        echo "  locust [users] [spawn-rate] [run-time]"
        echo "                      - 无头模式运行 Locust"
        echo "                        例: $0 locust 100 10 1m"
        echo "  ab [requests] [concurrency]"
        echo "                      - Apache Bench 压力测试"
        echo "                        例: $0 ab 1000 10"
        echo "  concurrent          - 并发请求测试"
        echo "  full                - 运行完整测试套件"
        echo ""
        echo "示例:"
        echo "  $0 health           # 检查服务健康状态"
        echo "  $0 locust-ui        # 启动 Locust Web UI"
        echo "  $0 locust 100 10 1m # 100 用户，10/秒，运行 1 分钟"
        echo "  $0 ab 1000 50       # 1000 请求，50 并发"
        echo "  $0 concurrent       # 并发测试"
        echo "  $0 full             # 完整测试套件"
        ;;
esac
