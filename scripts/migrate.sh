#!/usr/bin/env bash
# 数据库迁移辅助脚本
# 提供常用的 Alembic 迁移命令快捷方式

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

# 检测是否在 UV 环境中
_alembic_cmd="alembic"
if command -v uv &> /dev/null && [ -f "pyproject.toml" ]; then
    _alembic_cmd="uv run alembic"
fi

show_help() {
    cat << EOF
数据库迁移管理脚本

用法: $0 <command> [options]

命令:
  create <message>    创建新的迁移文件（自动检测模型变更）
  upgrade [revision]  应用迁移（默认升级到最新版本）
  downgrade <revision> 回滚迁移到指定版本
  current             显示当前数据库版本
  history             显示迁移历史
  heads               显示当前的 head 版本
  show <revision>     显示指定迁移的详细信息
  stamp <revision>    标记数据库为指定版本（不执行迁移）
  check               检查是否有未应用的迁移

示例:
  $0 create "add user table"           # 创建新迁移
  $0 upgrade                            # 升级到最新版本
  $0 upgrade head                       # 升级到最新版本（显式）
  $0 upgrade +1                         # 升级一个版本
  $0 downgrade -1                       # 回滚一个版本
  $0 downgrade base                     # 回滚所有迁移
  $0 current                            # 查看当前版本
  $0 history                            # 查看迁移历史
  $0 check                              # 检查待应用的迁移

EOF
}

case "${1:-}" in
    create)
        if [ -z "${2:-}" ]; then
            echo "错误: 请提供迁移消息"
            echo "用法: $0 create <message>"
            exit 1
        fi
        echo "正在创建迁移: $2"
        $_alembic_cmd revision --autogenerate -m "$2"
        ;;
    
    upgrade)
        REVISION="${2:-head}"
        echo "正在升级数据库到: $REVISION"
        $_alembic_cmd upgrade "$REVISION"
        echo "✓ 数据库升级完成"
        ;;
    
    downgrade)
        if [ -z "${2:-}" ]; then
            echo "错误: 请提供目标版本"
            echo "用法: $0 downgrade <revision>"
            echo "示例: $0 downgrade -1  (回滚一个版本)"
            exit 1
        fi
        echo "警告: 即将回滚数据库到: $2"
        read -p "确认继续? (y/N) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            $_alembic_cmd downgrade "$2"
            echo "✓ 数据库回滚完成"
        else
            echo "已取消"
        fi
        ;;
    
    current)
        echo "当前数据库版本:"
        $_alembic_cmd current
        ;;
    
    history)
        echo "迁移历史:"
        $_alembic_cmd history --verbose
        ;;
    
    heads)
        echo "当前 head 版本:"
        $_alembic_cmd heads
        ;;
    
    show)
        if [ -z "${2:-}" ]; then
            echo "错误: 请提供迁移版本"
            echo "用法: $0 show <revision>"
            exit 1
        fi
        $_alembic_cmd show "$2"
        ;;
    
    stamp)
        if [ -z "${2:-}" ]; then
            echo "错误: 请提供目标版本"
            echo "用法: $0 stamp <revision>"
            exit 1
        fi
        echo "警告: 即将标记数据库版本为: $2 (不执行实际迁移)"
        read -p "确认继续? (y/N) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            $_alembic_cmd stamp "$2"
            echo "✓ 数据库版本已标记"
        else
            echo "已取消"
        fi
        ;;
    
    check)
        echo "检查待应用的迁移..."
        CURRENT=$($_alembic_cmd current 2>/dev/null | grep -oP '(?<=\(head\) )[a-f0-9]+' || echo "")
        HEAD=$($_alembic_cmd heads 2>/dev/null | grep -oP '^[a-f0-9]+' || echo "")
        
        if [ -z "$CURRENT" ]; then
            echo "⚠ 数据库未初始化，需要运行迁移"
            exit 1
        elif [ "$CURRENT" != "$HEAD" ]; then
            echo "⚠ 有待应用的迁移"
            echo "当前版本: $CURRENT"
            echo "最新版本: $HEAD"
            exit 1
        else
            echo "✓ 数据库已是最新版本"
        fi
        ;;
    
    help|--help|-h|"")
        show_help
        ;;
    
    *)
        echo "错误: 未知命令 '$1'"
        echo
        show_help
        exit 1
        ;;
esac
