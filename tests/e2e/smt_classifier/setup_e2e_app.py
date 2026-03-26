"""
创建 E2E 测试环境配置

运行方式:
    uv run python tests/e2e/smt_classifier/setup_e2e_app.py

说明:
    此脚本生成 SMT 粗分机 E2E 测试所需的 .env.e2e 环境变量文件。
    使用与 scripts/data/seed_e2e_test_data.py 相同的 API 应用凭证。

    凭证信息:
    - app_id: app_Gqnvr3dpjGwlrjtO
    - app_secret: sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao

    运行前请确保:
    1. 已执行 scripts/data/seed_e2e_test_data.py 初始化数据库
    2. WES 服务已启动 (uvicorn main:app --reload)
"""

from __future__ import annotations

import sys
from pathlib import Path

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def setup_e2e_env() -> Path:
    """设置 E2E 测试环境变量文件"""
    env_file = Path(__file__).parent / ".env.e2e"

    # E2E 测试专用的 API 应用凭证
    # 与 scripts/data/seed_e2e_test_data.py 中创建的凭证一致
    app_id = "app_Gqnvr3dpjGwlrjtO"
    app_secret = "sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao"

    env_content = f"""# SMT 粗分机 E2E 测试环境变量
# 自动生成，请勿手动修改
# 凭证来源: scripts/data/seed_e2e_test_data.py

# API 应用凭证（用于 Mock 服务回调 WES）
API_APP_ID={app_id}
API_APP_SECRET={app_secret}

# WES 服务地址
WES_BASE_URL=http://localhost:8001
WES_EVENT_CALLBACK_URL=http://localhost:8001/api/v1/callback/event
WES_RESULT_CALLBACK_URL=http://localhost:8001/api/v1/callback/result
"""

    env_file.write_text(env_content, encoding="utf-8")
    return env_file


def main() -> None:
    """主函数"""
    print("=" * 60)
    print("设置 SMT 粗分机 E2E 测试环境")
    print("=" * 60)

    try:
        # 生成环境变量文件
        env_file = setup_e2e_env()

        print(f"\n✓ 环境变量文件已创建: {env_file}")
        print("\n  包含以下配置:")
        print("    - API_APP_ID: app_Gqnvr3dpjGwlrjtO")
        print("    - API_APP_SECRET: sec_***tao")
        print("    - WES_BASE_URL: http://localhost:8001")

        print("\n" + "=" * 60)
        print("使用说明:")
        print("=" * 60)
        print("\n1. 确保已初始化 E2E 测试数据:")
        print("   uv run python scripts/data/seed_e2e_test_data.py")
        print("\n2. 确保 WES 服务已启动:")
        print("   uvicorn main:app --reload")
        print("\n3. 运行 E2E 测试:")
        print("   uv run pytest tests/e2e/smt_classifier/ -v -m e2e")
        print("\n" + "=" * 60)

    except Exception as e:
        print(f"\n✗ 设置失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
