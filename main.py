from pathlib import Path

from dotenv import load_dotenv

from src.core.conf import settings
from src.register import register_app

load_dotenv()

app = register_app()

if __name__ == "__main__":
    # 如果你喜欢在 IDE 中进行 DEBUGmain 启动方法会很有帮助
    # 如果你喜欢通过 print 方式进行调试，建议使用 fastapi cli 方式启动服务
    import contextlib
    import multiprocessing

    import uvicorn

    # 设置 multiprocessing 启动方法为 'spawn'，避免资源冲突
    # 这在 macOS 和某些 Linux 环境中特别重要
    with contextlib.suppress(RuntimeError):
        # 启动方法已经设置过了
        multiprocessing.set_start_method("spawn", force=True)

    try:
        config = uvicorn.Config(
            app=f"{Path(__file__).stem}:app",
            reload=settings.APP_DEBUG,
            port=settings.APP_PORT,
            host=settings.APP_HOST,
            log_config=None,  # 使用已配置的 logging 系统（loguru）
        )

        server = uvicorn.Server(config)
        server.run()
    except Exception as e:
        raise e from e
