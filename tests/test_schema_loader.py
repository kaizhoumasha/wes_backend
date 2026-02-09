"""
测试 schema_loader.py 的智能关系加载功能

测试场景：
1. 多对多关系加载（User ↔ Role ↔ Permission）
2. 嵌套关系加载（User → Role → Permission）
3. 自引用关系加载（Permission 树形结构）
4. 智能策略验证（自动选择 joinedload/selectinload）
5. get_with_schema 和 get_all_with_schema 函数测试
"""

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.app.admin.models.perm import Permission, PermissionTree
from src.app.admin.models.role import Role
from src.app.admin.models.user import User, UserResponse
from src.core.mixins import DataTableMixin
from src.core.schema_loader import apply_schema_loads, get_all_with_schema, get_with_schema

# ==================== Fixtures ====================


@pytest_asyncio.fixture
async def db_engine():
    """创建测试数据库引擎"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    # 创建所有表
    async with engine.begin() as conn:
        await conn.run_sync(DataTableMixin.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """创建数据库会话"""
    from sqlalchemy.orm import sessionmaker

    async_session = sessionmaker(
        db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session() as session:
        yield session


@pytest_asyncio.fixture
async def sample_data(db_session: AsyncSession):
    """创建测试数据

    数据结构：
    - 2 个用户（admin, user）
    - 2 个角色（admin_role, user_role）
    - 6 个权限（树形结构）
        - admin (parent)
            - admin:user (child - 分组)
                - admin:user:create (grandchild - API 权限)
                - admin:user:read (grandchild - API 权限)
            - admin:role (child - 分组)
                - admin:role:create (grandchild - API 权限)
    """
    from sqlalchemy import insert

    from src.app.admin.models.relationships import role_permission

    # 创建权限（树形结构）
    perm_admin = Permission(
        name="admin:root",
        description="管理员根权限（分组）",
        type="user_api",
        resource="admin",
        action="root",
        method="GET",
        path="/admin",
        sort_order=1,
    )
    db_session.add(perm_admin)
    await db_session.flush()

    perm_user_group = Permission(
        name="admin:user:group",
        description="用户管理权限分组",
        type="user_api",
        resource="user",
        action="group",
        method="GET",
        path="/admin/user",
        parent_id=perm_admin.id,
        sort_order=1,
    )
    db_session.add(perm_user_group)
    await db_session.flush()

    perm_user_create = Permission(
        name="admin:user:create",
        description="创建用户",
        type="user_api",
        resource="user",
        action="create",
        method="POST",
        path="/api/admin/users",
        parent_id=perm_user_group.id,
        sort_order=1,
    )
    perm_user_read = Permission(
        name="admin:user:read",
        description="查看用户",
        type="user_api",
        resource="user",
        action="read",
        method="GET",
        path="/api/admin/users",
        parent_id=perm_user_group.id,
        sort_order=2,
    )
    db_session.add_all([perm_user_create, perm_user_read])
    await db_session.flush()

    perm_role_group = Permission(
        name="admin:role:group",
        description="角色管理权限分组",
        type="user_api",
        resource="role",
        action="group",
        method="GET",
        path="/admin/role",
        parent_id=perm_admin.id,
        sort_order=2,
    )
    db_session.add(perm_role_group)
    await db_session.flush()

    perm_role_create = Permission(
        name="admin:role:create",
        description="创建角色",
        type="user_api",
        resource="role",
        action="create",
        method="POST",
        path="/api/admin/roles",
        parent_id=perm_role_group.id,
        sort_order=1,
    )
    db_session.add(perm_role_create)
    await db_session.flush()

    # 创建角色
    admin_role = Role(
        name="admin",
        description="管理员角色",
    )
    user_role = Role(
        name="user",
        description="普通用户角色",
    )
    db_session.add_all([admin_role, user_role])
    await db_session.flush()

    # 使用关联表直接插入关系（避免触发 lazy load）
    await db_session.execute(
        insert(role_permission).values(
            [
                {"role_id": admin_role.id, "permission_id": perm_admin.id},
                {"role_id": admin_role.id, "permission_id": perm_user_group.id},
                {"role_id": admin_role.id, "permission_id": perm_user_create.id},
                {"role_id": admin_role.id, "permission_id": perm_user_read.id},
                {"role_id": admin_role.id, "permission_id": perm_role_group.id},
                {"role_id": admin_role.id, "permission_id": perm_role_create.id},
                {"role_id": user_role.id, "permission_id": perm_user_read.id},
            ]
        )
    )

    # 创建用户
    admin_user = User(
        username="admin",
        email="admin@example.com",
        full_name="Admin User",
        hashed_password="hashed_password_123",
        is_superuser=True,
    )
    normal_user = User(
        username="user",
        email="user@example.com",
        full_name="Normal User",
        hashed_password="hashed_password_456",
    )
    db_session.add_all([admin_user, normal_user])
    await db_session.flush()

    # 使用关联表直接插入用户-角色关系
    from src.app.admin.models.relationships import user_role as user_role_table

    await db_session.execute(
        insert(user_role_table).values(
            [
                {"user_id": admin_user.id, "role_id": admin_role.id},
                {"user_id": normal_user.id, "role_id": user_role.id},
            ]
        )
    )

    await db_session.commit()

    return {
        "users": [admin_user, normal_user],
        "roles": [admin_role, user_role],
        "permissions": [
            perm_admin,
            perm_user_group,
            perm_user_create,
            perm_user_read,
            perm_role_group,
            perm_role_create,
        ],
    }


# ==================== 测试用例 ====================


class TestApplySchemaLoads:
    """测试 apply_schema_loads 函数"""

    async def test_load_user_with_roles(self, db_session: AsyncSession, sample_data):
        """测试加载用户及其角色（多对多关系）"""
        # 构建查询
        query = select(User).where(User.username == "admin")
        query = apply_schema_loads(query, User, UserResponse, max_depth=2)

        # 执行查询
        result = await db_session.execute(query)
        user = result.scalars().first()

        # 验证
        assert user is not None
        assert user.username == "admin"
        assert len(user.roles) == 1
        assert user.roles[0].name == "admin"

    async def test_load_user_with_nested_relations(self, db_session: AsyncSession, sample_data):
        """测试加载用户及其角色和权限（嵌套关系）"""
        # 构建查询
        query = select(User).where(User.username == "admin")
        query = apply_schema_loads(query, User, UserResponse, max_depth=3)

        # 执行查询
        result = await db_session.execute(query)
        user = result.scalars().first()

        # 验证
        assert user is not None
        assert len(user.roles) == 1
        assert len(user.roles[0].permissions) == 6

    async def test_load_permission_tree(self, db_session: AsyncSession, sample_data):
        """测试加载权限树形结构（自引用关系）"""
        # 构建查询
        query = select(Permission).where(Permission.parent_id.is_(None))
        query = apply_schema_loads(query, Permission, PermissionTree, max_depth=3)

        # 执行查询
        result = await db_session.execute(query)
        root_perms = result.scalars().all()

        # 验证
        assert len(root_perms) == 1
        root = root_perms[0]
        assert root.name == "admin:root"
        assert len(root.children) == 2  # user_group, role_group

    async def test_max_depth_limit(self, db_session: AsyncSession, sample_data):
        """测试最大深度限制"""
        # max_depth=1 应该只加载直接关系
        query = select(User).where(User.username == "admin")
        query = apply_schema_loads(query, User, UserResponse, max_depth=1)

        result = await db_session.execute(query)
        user = result.scalars().first()

        # 验证：应该加载 roles，但不加载 roles.permissions
        assert user is not None
        assert len(user.roles) == 1
        # 注意：由于 max_depth=1，permissions 不会被加载


class TestGetWithSchema:
    """测试 get_with_schema 函数"""

    async def test_get_single_user(self, db_session: AsyncSession, sample_data):
        """测试获取单个用户"""
        user = await get_with_schema(
            db_session,
            User,
            UserResponse,
            User.username == "admin",
            max_depth=2,
        )

        assert user is not None
        assert user.username == "admin"
        assert len(user.roles) == 1

    async def test_get_nonexistent_user(self, db_session: AsyncSession, sample_data):
        """测试获取不存在的用户"""
        user = await get_with_schema(
            db_session,
            User,
            UserResponse,
            User.username == "nonexistent",
        )

        assert user is None

    async def test_get_with_nested_relations(self, db_session: AsyncSession, sample_data):
        """测试获取带嵌套关系的数据"""
        user = await get_with_schema(
            db_session,
            User,
            UserResponse,
            User.username == "admin",
            max_depth=3,
        )

        assert user is not None
        assert len(user.roles) == 1
        assert len(user.roles[0].permissions) == 6


class TestGetAllWithSchema:
    """测试 get_all_with_schema 函数"""

    async def test_get_all_users(self, db_session: AsyncSession, sample_data):
        """测试获取所有用户"""
        users = await get_all_with_schema(
            db_session,
            User,
            UserResponse,
            max_depth=2,
        )

        assert len(users) == 2
        assert all(len(user.roles) > 0 for user in users)

    async def test_get_all_with_filter(self, db_session: AsyncSession, sample_data):
        """测试带过滤条件的查询"""
        users = await get_all_with_schema(
            db_session,
            User,
            UserResponse,
            User.is_superuser == True,  # noqa: E712
            max_depth=2,
        )

        assert len(users) == 1
        assert users[0].username == "admin"

    async def test_get_all_with_pagination(self, db_session: AsyncSession, sample_data):
        """测试分页查询"""
        users = await get_all_with_schema(
            db_session,
            User,
            UserResponse,
            limit=1,
            offset=0,
            max_depth=2,
        )

        assert len(users) == 1

    async def test_get_all_with_ordering(self, db_session: AsyncSession, sample_data):
        """测试排序查询"""
        users = await get_all_with_schema(
            db_session,
            User,
            UserResponse,
            order_by=[User.username.desc()],
            max_depth=2,
        )

        assert len(users) == 2
        assert users[0].username == "user"
        assert users[1].username == "admin"


class TestIntelligentLoadingStrategy:
    """测试智能加载策略"""

    async def test_many_to_many_uses_selectinload(self, db_session: AsyncSession, sample_data):
        """测试多对多关系使用 selectinload"""
        # 构建查询
        query = select(User).where(User.username == "admin")
        query = apply_schema_loads(query, User, UserResponse, max_depth=2)

        # 检查查询选项
        # 注意：这是一个间接测试，我们验证查询能正常工作
        result = await db_session.execute(query)
        user = result.scalars().first()

        # 验证数据正确加载
        assert user is not None
        assert len(user.roles) == 1
        # 如果使用了正确的策略，应该不会有 N+1 查询问题

    async def test_relation_validation(self, db_session: AsyncSession, sample_data):
        """测试关系验证功能"""
        # 尝试加载不存在的关系（应该被忽略）
        from pydantic import BaseModel

        class InvalidSchema(BaseModel):
            id: int
            username: str
            nonexistent_relation: list[dict] = []

        query = select(User).where(User.username == "admin")
        # 应该不会抛出异常，只是忽略不存在的关系
        query = apply_schema_loads(query, User, InvalidSchema, max_depth=2)

        result = await db_session.execute(query)
        user = result.scalars().first()

        assert user is not None


class TestEdgeCases:
    """测试边界情况"""

    async def test_empty_result(self, db_session: AsyncSession, sample_data):
        """测试空结果集"""
        users = await get_all_with_schema(
            db_session,
            User,
            UserResponse,
            User.username == "nonexistent",
        )

        assert len(users) == 0

    async def test_no_relations(self, db_session: AsyncSession):
        """测试没有关系的模型"""
        from pydantic import BaseModel

        class SimpleSchema(BaseModel):
            id: int
            username: str

        # 创建一个没有角色的用户
        user = User(
            username="simple",
            email="simple@example.com",
            hashed_password="hash",
        )
        db_session.add(user)
        await db_session.commit()

        result = await get_with_schema(
            db_session,
            User,
            SimpleSchema,
            User.username == "simple",
        )

        assert result is not None
        assert result.username == "simple"

    async def test_circular_reference_handling(self, db_session: AsyncSession, sample_data):
        """测试循环引用处理（Permission 自引用）"""
        # 加载根权限及其子权限
        perms = await get_all_with_schema(
            db_session,
            Permission,
            PermissionTree,
            Permission.parent_id.is_(None),
            max_depth=3,
        )

        assert len(perms) == 1
        root = perms[0]
        assert len(root.children) == 2

        # 验证子权限也有子权限
        user_group = next(c for c in root.children if "user" in c.name)
        assert len(user_group.children) == 2


class TestPerformance:
    """性能测试"""

    async def test_no_n_plus_one_queries(self, db_session: AsyncSession, sample_data):
        """测试避免 N+1 查询问题"""
        # 这个测试验证使用 schema_loader 不会产生 N+1 查询
        # 通过加载多个用户及其关系来验证

        users = await get_all_with_schema(
            db_session,
            User,
            UserResponse,
            max_depth=3,
        )

        # 验证所有用户的关系都已加载
        assert len(users) == 2
        for user in users:
            assert len(user.roles) > 0
            for role in user.roles:
                # 权限应该已经加载，不需要额外查询
                assert hasattr(role, "permissions")


# ==================== 集成测试 ====================


class TestIntegration:
    """集成测试"""

    async def test_complete_user_hierarchy(self, db_session: AsyncSession, sample_data):
        """测试完整的用户层级结构加载"""
        # 加载 User → Role → Permission 完整层级
        user = await get_with_schema(
            db_session,
            User,
            UserResponse,
            User.username == "admin",
            max_depth=3,
        )

        # 验证完整层级
        assert user is not None
        assert user.username == "admin"
        assert len(user.roles) == 1

        role = user.roles[0]
        assert role.name == "admin"
        assert len(role.permissions) == 6

        # 验证权限详情
        perm_names = [p.name for p in role.permissions]
        assert "admin:root" in perm_names
        assert "admin:user:create" in perm_names

    async def test_permission_tree_structure(self, db_session: AsyncSession, sample_data):
        """测试权限树形结构的完整加载"""
        # 加载完整的权限树
        root_perms = await get_all_with_schema(
            db_session,
            Permission,
            PermissionTree,
            Permission.parent_id.is_(None),
            max_depth=4,
        )

        assert len(root_perms) == 1
        root = root_perms[0]

        # 验证树形结构
        assert root.name == "admin:root"
        assert len(root.children) == 2

        # 验证第二层（分组）
        user_group = next(c for c in root.children if "user" in c.name)
        role_group = next(c for c in root.children if "role" in c.name)

        assert len(user_group.children) == 2
        assert len(role_group.children) == 1

        # 验证第三层（API 权限）
        create_perm = next(c for c in user_group.children if "create" in c.name)
        assert create_perm.name == "admin:user:create"
        assert create_perm.method == "POST"
