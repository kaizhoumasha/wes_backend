#!/bin/bash
# 生成 Alembic 迁移并自动处理 ENUM 类型
# 用法: ./scripts/generate_migration.sh "migration message"

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

if [ -z "$1" ]; then
    echo -e "${RED}错误: 缺少迁移消息${NC}"
    echo "用法: $0 <migration_message>"
    echo "示例: $0 \"add user avatar field\""
    exit 1
fi

echo -e "${GREEN}正在生成迁移: $1${NC}"

# 检测是否在 UV 环境中（复用变量）
_alembic_cmd="alembic"
if command -v uv &> /dev/null && [ -f "pyproject.toml" ]; then
    echo -e "${GREEN}使用 UV 运行 Alembic${NC}"
    _alembic_cmd="uv run alembic"
fi
$_alembic_cmd revision --autogenerate -m "$1"

# 获取最新的迁移文件
LATEST_MIGRATION=$(ls -t migrations/versions/*.py | head -1)
echo -e "${GREEN}生成的迁移文件: $LATEST_MIGRATION${NC}"

# 检查迁移是否只包含不相关的 ENUM 类型变更
if grep -q "api_applications\|audit_logs" "$LATEST_MIGRATION" && grep -q "postgresql.ENUM" "$LATEST_MIGRATION"; then
    # 检查是否只包含这些表的 ENUM 变更
    # 统计操作类型
    alter_column_count=$(grep -c "^    op.alter_column" "$LATEST_MIGRATION" || true)
    # 检查是否有其他类型的操作（如 create_table, drop_table, add_column 等）
    other_ops=$(grep -E "^    op\.(create_table|drop_table|add_column|drop_column|create_foreign_key|drop_constraint)" "$LATEST_MIGRATION" | wc -l)

    # 如果只有 alter_column 操作，且涉及 api_applications/audit_logs，说明是 ENUM 误报
    if [ $alter_column_count -ge 4 ] && [ $other_ops -eq 0 ]; then
        echo ""
        echo -e "${YELLOW}⚠️  检测到迁移只包含已知的 ENUM 类型误报（不影响功能）${NC}"
        echo "正在清理不必要的迁移..."

        rm "$LATEST_MIGRATION"
        echo -e "${GREEN}✓ 已删除空迁移${NC}"
        echo ""
        echo -e "${GREEN}迁移生成完成！${NC}"
        echo ""
        echo "提示: 这是 Alembic 的已知问题（postgresql.ENUM vs sa.Enum），可以忽略"
        exit 0
    fi
fi

# 检查是否包含新的 ENUM 类型定义（需要处理）
if grep -q "sa.Enum.*name=" "$LATEST_MIGRATION"; then
    echo ""
    echo -e "${YELLOW}⚠️  检测到 ENUM 类型！${NC}"
    echo "正在自动处理 ENUM 类型的 schema 和 DROP TYPE 语句..."

    # 使用 Python 脚本处理 ENUM 类型的 schema 和 DROP TYPE 语句
    # 复用 UV 环境检测
    _python_cmd="python3"
    if [ "$_alembic_cmd" = "uv run alembic" ]; then
        _python_cmd="uv run python3"
    fi
    $_python_cmd << PYTHON_SCRIPT
import re

with open('$LATEST_MIGRATION', 'r') as f:
    content = f.read()

# 1. 为 sa.Enum 添加 schema 参数
# 匹配模式: sa.Enum(..., name="xxx") 添加 schema="yyy"
# 需要找到该列所属的表的 schema

def add_schema_to_enums(content):
    """为 sa.Enum 添加 schema 参数"""
    result = []
    lines = content.split('\n')
    current_schema = None
    in_create_table = False
    pending_lines = []  # 暂存可能需要修改的行

    i = 0
    while i < len(lines):
        line = lines[i]

        # 检测 op.create_table 开始
        if 'op.create_table(' in line:
            in_create_table = True
            current_schema = None
            pending_lines = []
            result.append(line)
            i += 1
            continue

        # 在 create_table 块中处理
        if in_create_table:
            # 查找 schema="xxx" 参数
            schema_match = re.search(r'schema=["\'](\w+)["\']', line)
            if schema_match:
                current_schema = schema_match.group(1)
                # 如果有待处理的行，现在处理它们
                if pending_lines:
                    for pending_line in pending_lines:
                        if 'sa.Enum' in pending_line and 'name=' in pending_line and 'schema=' not in pending_line:
                            pending_line = re.sub(
                                r'(name=["\'][\w]+["\'])',
                                r'\1, schema="' + current_schema + '"',
                                pending_line
                            )
                        result.append(pending_line)
                    pending_lines = []
                result.append(line)
                i += 1
                continue

            # 检测到 sa.Enum，如果还没有 schema 则暂存
            if 'sa.Enum' in line and 'name=' in line and current_schema is None:
                pending_lines.append(line)
                i += 1
                continue
            elif 'sa.Enum' in line and 'name=' in line and 'schema=' not in line and current_schema:
                # 已知 schema，直接添加
                line = re.sub(
                    r'(name=["\'][\w]+["\'])',
                    r'\1, schema="' + current_schema + '"',
                    line
                )

            # 检测到 create_table 结束（右括号且无缩进）
            if line.strip() and not line.startswith(' ') and not line.startswith('\t') and ')' in line and 'op.create_table' not in line:
                in_create_table = False
                current_schema = None
                # 清空待处理的行
                if pending_lines:
                    result.extend(pending_lines)
                    pending_lines = []

        result.append(line)
        i += 1

    return '\n'.join(result)

content = add_schema_to_enums(content)

# 2. 提取所有 ENUM 类型和对应的 schema
enum_info = {}
enum_pattern = r'sa\.Enum\([^)]*name=["\']([\w]+)["\'][^)]*schema=["\']([\w]+)["\']'

for enum_name, schema in re.findall(enum_pattern, content):
    if enum_name not in enum_info:
        enum_info[enum_name] = schema
    elif enum_info[enum_name] != schema:
        print(f"警告: ENUM {enum_name} 在多个 schema 中使用")

# 2.1 从历史迁移中提取已存在的 ENUM 类型，避免重复添加 DROP TYPE
import os
import glob

historical_enums = set()
migration_files = sorted(glob.glob('migrations/versions/*.py'))

# 排除当前正在处理的迁移文件
current_migration = os.path.basename('$LATEST_MIGRATION')
for migration_file in migration_files:
    if os.path.basename(migration_file) == current_migration:
        continue

    try:
        with open(migration_file, 'r') as f:
            hist_content = f.read()
            # 从历史迁移中提取已定义的 ENUM
            for hist_enum_name, hist_schema in re.findall(enum_pattern, hist_content):
                historical_enums.add((hist_enum_name, hist_schema))
    except Exception:
        pass

# 只保留当前迁移中新增的 ENUM
new_enums = {}
for enum_name, schema in enum_info.items():
    if (enum_name, schema) not in historical_enums:
        new_enums[enum_name] = schema
    else:
        print(f"跳过已存在的 ENUM: {enum_name} (schema: {schema})")

if not new_enums:
    print("当前迁移中没有新增的 ENUM 类型")
    # 仍然保存文件（可能已经修改了 schema 参数）
    with open('$LATEST_MIGRATION', 'w') as f:
        f.write(content)
    exit(0)

enum_info = new_enums

# 3. 在 upgrade() 函数中添加 DROP TYPE 语句
def add_drop_type_upgrade(content, enum_info):
    """在 upgrade() 函数开头添加 DROP TYPE 语句"""
    # 找到 def upgrade() -> None: 后的 "# ### commands auto generated" 行
    pattern = r'(def upgrade\(\) -> None:.*?)(    # ### commands auto generated by Alembic)'
    match = re.search(pattern, content, re.DOTALL)

    if match:
        # 生成 DROP TYPE 语句
        drop_statements = []
        for enum_name, schema in sorted(enum_info.items()):
            drop_statements.append(
                f'    # 删除已存在的 ENUM 类型（如果存在）\n'
                f'    op.execute("DROP TYPE IF EXISTS {schema}.{enum_name} CASCADE")'
            )

        if drop_statements:
            all_statements = '\n\n'.join(drop_statements)
            replacement = match.group(1) + all_statements + '\n\n' + match.group(2)
            content = re.sub(pattern, replacement, content, flags=re.DOTALL)

    return content

content = add_drop_type_upgrade(content, enum_info)

# 4. 在 downgrade() 函数末尾添加 DROP TYPE 语句
def add_drop_type_downgrade(content, enum_info):
    """在 downgrade() 函数末尾添加 DROP TYPE 语句"""
    # 找到 "# ### end Alembic commands ###" 前添加
    pattern = r'(def downgrade\(\) -> None:.*?)(    # ### end Alembic commands ###)'
    match = re.search(pattern, content, re.DOTALL)

    if match:
        # 生成 DROP TYPE 语句
        drop_statements = []
        for enum_name, schema in sorted(enum_info.items(), reverse=True):
            drop_statements.append(
                f'    # 删除 ENUM 类型\n'
                f'    op.execute("DROP TYPE IF EXISTS {schema}.{enum_name} CASCADE")'
            )

        if drop_statements:
            all_statements = '\n\n'.join(drop_statements)
            replacement = match.group(1) + all_statements + '\n\n    ' + match.group(2)
            content = re.sub(pattern, replacement, content, flags=re.DOTALL)

    return content

content = add_drop_type_downgrade(content, enum_info)

with open('$LATEST_MIGRATION', 'w') as f:
    f.write(content)

# 输出处理的 ENUM 信息
if enum_info:
    print("已处理的 ENUM 类型:")
    for enum_name, schema in sorted(enum_info.items()):
        print(f"  - {enum_name} (schema: {schema})")
PYTHON_SCRIPT

    echo ""
    echo -e "${GREEN}✓ ENUM 类型处理完成${NC}"
    echo -e "${YELLOW}请检查迁移文件以确认修改正确: $LATEST_MIGRATION${NC}"
else
    echo -e "${GREEN}✓ 未检测到 ENUM 类型，无需额外处理${NC}"
fi

echo ""
echo -e "${GREEN}迁移生成完成！${NC}"
echo ""
echo "下一步:"
echo "  1. 检查迁移文件: $LATEST_MIGRATION"
echo "  2. 运行迁移: ./scripts/migrate.sh upgrade"
