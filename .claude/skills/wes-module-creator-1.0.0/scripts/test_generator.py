#!/usr/bin/env python3
"""
单元测试生成器

自动为模块生成单元测试文件
"""

from pathlib import Path


class TestGenerator:
    """单元测试生成器"""

    def __init__(self, module_context, project_root: Path | None = None):
        from template_engine import ModuleContext

        if not isinstance(module_context, ModuleContext):
            raise TypeError("module_context must be ModuleContext instance")

        self.context = module_context

        if project_root is None:
            project_root = Path(__file__).parent.parent.parent.parent

        self.project_root = Path(project_root)

    def generate(self):
        """生成测试文件"""
        test_file = self.project_root / "tests" / f"test_{self.context.module_name}.py"

        content = self._generate_test_content()

        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(content)

        print(f"   ✓ 生成测试: {test_file}")
        return test_file

    def _generate_test_content(self) -> str:
        """生成测试内容"""
        # 构建测试数据
        test_data = self._build_test_data()

        module = self.context.module_name
        cls = self.context.class_name
        app = self.context.app_name

        content = f'''"""
{cls} 模块测试
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.{app}.{module}.services import {module}_service
from src.core.cache_service import CacheService


@pytest.mark.asyncio
async def test_create_{module}(db_session: AsyncSession, cache_service: CacheService):
    """测试创建{cls}\"\"\"
    {test_data}

    obj = await {module}_service.create(db_session, cache_service, data)
    assert obj.id > 0
    {self._generate_assertions()}


@pytest.mark.asyncio
async def test_get_by_id(db_session: AsyncSession, cache_service: CacheService):
    """测试查询单个对象\"\"\"
    # 先创建
    {test_data}
    created = await {module}_service.create(db_session, cache_service, data)

    # 查询
    obj = await {module}_service.get_by_id(db_session, cache_service, created.id)
    assert obj is not None
    assert obj.id == created.id


@pytest.mark.asyncio
async def test_update_{module}(db_session: AsyncSession, cache_service: CacheService):
    """测试更新对象\"\"\"
    # 先创建
    {test_data}
    created = await {module}_service.create(db_session, cache_service, data)

    # 更新
    update_data = {{
        {self._build_update_data()}
        "version": created.version,  # ✅ 必须包含 version 字段
    }}
    updated = await {module}_service.update(
        db_session, cache_service, created.id, update_data
    )
    assert updated.id == created.id


@pytest.mark.asyncio
async def test_delete_{module}(db_session: AsyncSession, cache_service: CacheService):
    """测试软删除对象\"\"\"
    # 先创建
    {test_data}
    created = await {module}_service.create(db_session, cache_service, data)

    # 软删除
    result = await {module}_service.delete(db_session, cache_service, created.id)
    assert result is True

    # 验证已删除
    deleted = await {module}_service.get_by_id(
        db_session, cache_service, created.id, include_deleted=True
    )
    assert deleted is not None
    assert deleted.is_deleted is True


@pytest.mark.asyncio
async def test_restore_{module}(db_session: AsyncSession, cache_service: CacheService):
    """测试恢复已删除对象\"\"\"
    # 先创建并删除
    {test_data}
    created = await {module}_service.create(db_session, cache_service, data)
    await {module}_service.delete(db_session, cache_service, created.id)

    # 恢复
    restored = await {module}_service.restore(
        db_session, cache_service, created.id
    )
    assert restored is not None
    assert restored.is_deleted is False


@pytest.mark.asyncio
async def test_list_{module}s(db_session: AsyncSession, cache_service: CacheService):
    """测试查询列表\"\"\"
    # 创建多个对象
    {test_data}
    await {module}_service.create(db_session, cache_service, data.copy())
    await {module}_service.create(db_session, cache_service, data.copy())

    # 查询列表
    total, items = await {module}_service.get_list(
        db_session, cache_service, limit=10, offset=0
    )
    assert total >= 2
    assert len(items) >= 2
'''

        return content.strip()

    def _build_test_data(self) -> str:
        """构建测试数据"""
        if not self.context.fields:
            return "data = {}  # TODO: 添加测试数据"

        lines = ["data = {"]
        for field in self.context.fields:
            name = field["name"]
            ftype = field["type"]
            desc = field.get("description", "")
            default = field.get("default", "")

            if ftype == "str":
                lines.append(f'    "{name}": "测试{desc}",')
            elif ftype == "int":
                lines.append(f'    "{name}": 100,')
            elif ftype == "bool":
                lines.append(f'    "{name}": True,')
            elif ftype in ["str | None", "int | None"]:
                lines.append(f'    "{name}": None,')
            elif default:
                lines.append(f'    "{name}": {default},')
            else:
                lines.append(f'    "{name}": ...,  # TODO: 设置测试值')

        lines.append("}")
        return "\n".join(lines)

    def _build_update_data(self) -> str:
        """构建更新数据"""
        if not self.context.fields:
            return '        "description": "更新后的描述"'

        # 使用第一个可更新的字段
        for field in self.context.fields:
            name = field["name"]
            ftype = field["type"]
            desc = field.get("description", "")

            if ftype == "str" and not field.get("optional"):
                return f'        "{name}": "更新后的{desc}",'
            if ftype == "int":
                return f'        "{name}": 200,'

        return '        "description": "更新后的描述"'

    def _generate_assertions(self) -> str:
        """生成断言"""
        if not self.context.fields:
            return "    # TODO: 添加断言"

        assertions = []
        for field in self.context.fields[:3]:  # 只断言前 3 个字段
            name = field["name"]
            ftype = field["type"]

            if name in ["created_at", "updated_at"]:
                continue
            if ftype == "str":
                assertions.append(f"    assert obj.{name}")
            elif ftype == "int":
                assertions.append(f"    assert obj.{name} >= 0")
            elif ftype == "bool":
                assertions.append(f"    assert isinstance(obj.{name}, bool)")

        return "\n".join(assertions) if assertions else "    # TODO: 添加断言"
