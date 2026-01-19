#!/bin/bash
# 环境搭建主脚本
USER="root"
CICD_IP="192.168.0.220"
TEST_IP="192.168.0.221"

echo "==========================================="
echo "正在配置 CI/CD 服务器 ($CICD_IP)..."
echo "系统将提示您输入 root 密码。"
echo "==========================================="

scp scripts/devops/setup_cicd_server.sh $USER@$CICD_IP:/tmp/
ssh $USER@$CICD_IP "chmod +x /tmp/setup_cicd_server.sh && /tmp/setup_cicd_server.sh"

echo "==========================================="
echo "正在配置测试服务器 ($TEST_IP)..."
echo "系统将提示您输入 root 密码。"
echo "==========================================="

scp scripts/devops/setup_test_server.sh $USER@$TEST_IP:/tmp/
ssh $USER@$TEST_IP "chmod +x /tmp/setup_test_server.sh && /tmp/setup_test_server.sh"

echo "==========================================="
echo "环境搭建全部完成！"
echo "==========================================="