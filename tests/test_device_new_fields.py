"""
测试 Device 表新增字段的 CRUD 功能

验证架构 8.2 节添加的 5 个字段：
- device_role (必需): 设备业务角色
- role_index (必需): 同角色序号
- upstream_device_id (可选): 上游设备ID
- vendor_type (可选): 厂商类型
- capabilities (可选): 能力列表（JSON）
"""

import pytest
from sqlalchemy import select

from src.app.device.models.device import Device, DeviceType


@pytest.mark.asyncio
async def test_device_new_fields_create_read(db_session):
    """测试新字段的创建和读取"""

    # 创建：带新字段的设备
    print("\n1. 创建带新字段的设备...")
    new_device = Device(
        device_code="TEST-DEVICE-01",
        device_name="测试设备 01",
        device_type=DeviceType.INDUSTRIAL_PC.value,
        work_line_id=None,
        # 新字段（架构 8.2 节）
        device_role="SCANNER",
        role_index=1,
        upstream_device_id=None,
        vendor_type="KEYENCE",
        capabilities=["SCAN", "READ_BARCODE"],
    )

    db_session.add(new_device)
    await db_session.commit()
    await db_session.refresh(new_device)

    print(f"   ✓ 创建成功：ID={new_device.id}")
    print(f"   ✓ device_role={new_device.device_role}")
    print(f"   ✓ role_index={new_device.role_index}")
    print(f"   ✓ vendor_type={new_device.vendor_type}")
    print(f"   ✓ capabilities={new_device.capabilities}")

    # 读取：查询新字段
    print("\n2. 读取设备新字段...")
    result = await db_session.execute(select(Device).where(Device.device_code == "TEST-DEVICE-01"))
    fetched_device = result.scalar_one_or_none()

    assert fetched_device is not None, "设备应该存在"
    assert fetched_device.device_role == "SCANNER", "device_role 应该等于 SCANNER"
    assert fetched_device.role_index == 1, "role_index 应该等于 1"
    assert fetched_device.vendor_type == "KEYENCE", "vendor_type 应该等于 KEYENCE"
    assert fetched_device.capabilities == ["SCAN", "READ_BARCODE"], "capabilities 应该包含两个能力"

    print(f"   ✓ 查询成功：device_role={fetched_device.device_role}")
    print(f"   ✓ capabilities 长度：{len(fetched_device.capabilities)}")


@pytest.mark.asyncio
async def test_device_new_fields_update(db_session):
    """测试新字段的更新"""

    # 创建测试设备
    new_device = Device(
        device_code="TEST-DEVICE-02",
        device_name="测试设备 02",
        device_type=DeviceType.INDUSTRIAL_PC.value,
        work_line_id=None,
        device_role="SCANNER",
        role_index=1,
    )

    db_session.add(new_device)
    await db_session.commit()
    await db_session.refresh(new_device)

    # 更新：修改新字段
    print("\n3. 更新设备新字段...")
    new_device.device_role = "ROBOT_ARM"
    new_device.role_index = 2
    new_device.vendor_type = "FANUC"
    new_device.capabilities = ["PICK", "PUT", "ROTATE"]

    await db_session.commit()
    await db_session.refresh(new_device)

    assert new_device.device_role == "ROBOT_ARM", "device_role 应该更新为 ROBOT_ARM"
    assert new_device.role_index == 2, "role_index 应该更新为 2"
    assert new_device.capabilities == ["PICK", "PUT", "ROTATE"], "capabilities 应该更新"

    print(f"   ✓ 更新成功：device_role={new_device.device_role}")
    print(f"   ✓ 更新成功：role_index={new_device.role_index}")
    print(f"   ✓ 更新成功：capabilities={new_device.capabilities}")


@pytest.mark.asyncio
async def test_device_upstream_relationship(db_session):
    """测试上游设备关系"""

    # 创建上游设备
    upstream = Device(
        device_code="TEST-UPSTREAM-01",
        device_name="上游设备",
        device_type=DeviceType.CONVEYOR.value,
        work_line_id=None,
        device_role="CONVEYOR",
        role_index=1,
    )

    db_session.add(upstream)
    await db_session.commit()
    await db_session.refresh(upstream)

    # 创建下游设备，设置上游设备
    print("\n4. 测试上游设备关系...")
    downstream = Device(
        device_code="TEST-DOWNSTREAM-01",
        device_name="下游设备",
        device_type=DeviceType.SCANNER.value,
        work_line_id=None,
        device_role="SCANNER",
        role_index=1,
        upstream_device_id=upstream.id,
    )

    db_session.add(downstream)
    await db_session.commit()
    await db_session.refresh(downstream)

    assert downstream.upstream_device_id == upstream.id, "upstream_device_id 应该设置成功"

    print(f"   ✓ 设置上游设备：{upstream.device_code} (ID={upstream.id})")
    print(f"   ✓ upstream_device_id={downstream.upstream_device_id}")


@pytest.mark.asyncio
async def test_device_new_fields_delete(db_session):
    """测试新字段的删除"""

    # 创建测试设备
    new_device = Device(
        device_code="TEST-DEVICE-DELETE",
        device_name="待删除设备",
        device_type=DeviceType.INDUSTRIAL_PC.value,
        work_line_id=None,
        device_role="SCANNER",
        role_index=1,
        vendor_type="KEYENCE",
        capabilities=["SCAN"],
    )

    db_session.add(new_device)
    await db_session.commit()
    await db_session.refresh(new_device)
    device_id = new_device.id

    # 删除：清理测试数据
    print("\n5. 删除测试设备...")
    await db_session.delete(new_device)
    await db_session.commit()

    # 验证删除
    result = await db_session.execute(select(Device).where(Device.id == device_id))
    deleted_device = result.scalar_one_or_none()

    assert deleted_device is None, "设备应该已删除"

    print("   ✓ 删除成功")
