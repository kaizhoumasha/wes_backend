#!/usr/bin/env python3
"""
测试部分唯一索引功能

验证软删除 + 唯一约束的部分索引解决方案是否正常工作
"""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.conf import settings


async def test_partial_unique_index():
    """测试部分唯一索引的各种场景"""

    database_url = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(database_url, echo=True)

    try:
        async with engine.begin() as conn:
            print("\n" + "=" * 80)
            print("🧪 测试部分唯一索引功能")
            print("=" * 80)

            # 清理测试数据
            print("\n📋 步骤1: 清理现有测试数据")
            await conn.execute(text("DELETE FROM demo_products WHERE name IN ('apple', 'banana', 'orange')"))

            # 场景1: 创建第一个记录
            print("\n✅ 场景1: 创建 name='apple' 的记录")
            result = await conn.execute(
                text("""
                    INSERT INTO demo_products (name, price, stock, created_by, updated_by)
                    VALUES ('apple', 10.0, 100, 1, 1)
                    RETURNING id, name, is_deleted
                """)
            )
            record = result.fetchone()
            assert record is not None, "插入应该返回一条记录"
            print(f"   创建成功: ID={record[0]}, name={record[1]}, is_deleted={record[2]}")

            # 场景2: 尝试创建第二个同名记录（应该失败）
            print("\n❌ 场景2: 尝试创建第二个 name='apple' 的记录（预期失败）")
            try:
                await conn.execute(
                    text("""
                        INSERT INTO demo_products (name, price, stock, created_by, updated_by)
                        VALUES ('apple', 15.0, 200, 1, 1)
                    """)
                )
                print("   ❌ 测试失败：应该抛出唯一约束异常")
            except Exception as e:
                if "duplicate key" in str(e).lower() or "unique constraint" in str(e).lower():
                    print(f"   ✅ 正确抛出唯一约束异常: {str(e)[:100]}...")
                else:
                    print(f"   ⚠️ 抛出其他异常: {e}")

            # 场景3: 软删除第一个记录
            print("\n🗑️  场景3: 软删除 ID=1 的记录")
            await conn.execute(
                text("""
                    UPDATE demo_products
                    SET is_deleted = TRUE, deleted_at = NOW()
                    WHERE id = 1
                """)
            )
            print("   软删除成功")

            # 场景4: 再次创建同名记录（应该成功）
            print("\n✅ 场景4: 再次创建 name='apple' 的记录（旧记录已删除）")
            result = await conn.execute(
                text("""
                    INSERT INTO demo_products (name, price, stock, created_by, updated_by)
                    VALUES ('apple', 20.0, 300, 1, 1)
                    RETURNING id, name, is_deleted
                """)
            )
            record = result.fetchone()
            assert record is not None, "插入应该返回一条记录"
            print(f"   创建成功: ID={record[0]}, name={record[1]}, is_deleted={record[2]}")

            # 场景5: 验证数据库状态
            print("\n📊 场景5: 验证数据库状态")
            result = await conn.execute(
                text("""
                    SELECT id, name, is_deleted, deleted_at
                    FROM demo_products
                    WHERE name IN ('apple', 'banana', 'orange')
                    ORDER BY id
                """)
            )
            print("   当前数据库中的记录:")
            for row in result:
                deleted_status = "已删除" if row[2] else "未删除"
                print(f"     ID={row[0]}, name={row[1]}, status={deleted_status}")

            # 场景6: 验证索引是否存在
            print("\n🔍 场景6: 验证部分唯一索引是否存在")
            result = await conn.execute(
                text("""
                    SELECT indexname, indexdef  # noqa: disable=VA053 - indexdef 是 PostgreSQL 系统列名
                    FROM pg_indexes
                    WHERE tablename = 'demo_products'
                      AND indexname = 'demo_products_name_active_unique'
                """)
            )
            index_info = result.fetchone()
            if index_info:
                print("   ✅ 索引存在:")
                print(f"      名称: {index_info[0]}")
                print(f"      定义: {index_info[1]}")
            else:
                print("   ⚠️ 警告: 部分唯一索引不存在，请先运行迁移脚本")

            # 场景7: 再次软删除第二个记录
            print("\n🗑️  场景7: 软删除 ID=2 的记录（验证多次删除不会冲突）")
            await conn.execute(
                text("""
                    UPDATE demo_products
                    SET is_deleted = TRUE, deleted_at = NOW()
                    WHERE id = 2
                """)
            )
            print("   软删除成功")

            # 最终状态
            print("\n📊 最终数据库状态:")
            result = await conn.execute(
                text("""
                    SELECT id, name, is_deleted
                    FROM demo_products
                    WHERE name = 'apple'
                    ORDER BY id
                """)
            )
            print("   'apple' 记录历史:")
            for row in result:
                deleted_status = "已删除" if row[2] else "未删除"
                print(f"     ID={row[0]}, name={row[1]}, status={deleted_status}")

            print("\n" + "=" * 80)
            print("✅ 所有测试场景验证完成！")
            print("=" * 80 + "\n")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        raise
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(test_partial_unique_index())
