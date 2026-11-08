"""桌面入口：一个窗口 + 一个跑在后台的本机服务。

双击图标出窗口 —— 没有终端，也没有 localhost 地址栏。
开发时用 `python app.py --headless` 只起后端，方便 curl 调试。
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

import uvicorn

from agent.config import Config, user_dir
from server.api import create_app

WINDOW_TITLE = "Desktop Agent"
log = logging.getLogger("desktop-agent")


def setup_logging() -> None:
    """打包后没有控制台，所有信息落到用户目录的 app.log。"""
    try:
        handler = RotatingFileHandler(
            user_dir() / "app.log", maxBytes=512_000, backupCount=1, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.basicConfig(handlers=[handler], level=logging.INFO)
    except OSError:
        logging.basicConfig(level=logging.INFO)


def ensure_stdio() -> None:
    """windowed 模式下 sys.stdout / sys.stderr 是 None —— uvicorn 和 logging 都会因此炸掉。"""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(
                sys,
                name,
                open(os.devnull if name == "stdout" else user_dir() / "stderr.log", "a", encoding="utf-8", buffering=1),
            )


def find_port(start: int = 8756, tries: int = 40) -> int:
    for offset in range(tries):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("找不到可用端口")


def wait_until_up(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def run_server(port: int, cfg: Config) -> threading.Thread:
    config = uvicorn.Config(
        create_app(cfg),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        log_config=None,  # 用我们自己的文件日志，别让 uvicorn 去碰可能为 None 的 stdout
    )
    server = uvicorn.Server(config)

    def target() -> None:
        try:
            server.run()
        except BaseException:
            log.exception("uvicorn crashed")

    thread = threading.Thread(target=target, daemon=True, name="uvicorn")
    thread.start()
    return thread


def main() -> int:
    if "--run-mcp-server" in sys.argv:
        # 打包版：同一个 exe 被 MCP 客户端当成 stdio server 拉起来
        index = sys.argv.index("--run-mcp-server")
        name = sys.argv[index + 1] if len(sys.argv) > index + 1 else "notes"
        from mcp_servers.notes_server.server import serve

        serve()
        return 0

    parser = argparse.ArgumentParser(description="Desktop Agent")
    parser.add_argument("--headless", action="store_true", help="只启动后端，不开窗口")
    parser.add_argument("--port", type=int, default=0, help="固定端口（默认自动选择）")
    parser.add_argument("--workspace", type=str, default="", help="工作目录")
    parser.add_argument("--mock", action="store_true", help="离线演示模式（不需要 API Key）")
    args = parser.parse_args()
    ensure_stdio()
    setup_logging()

    cfg = Config.load(args.workspace or None)
    if args.mock:
        cfg.mock = True
    cfg.ensure_workspace()

    port = args.port or find_port()
    log.info("starting backend on 127.0.0.1:%s (workspace=%s, mock=%s)", port, cfg.workspace, cfg.mock)
    run_server(port, cfg)

    if not wait_until_up(port):
        log.error("backend failed to listen on port %s", port)
        if sys.stderr:
            print("后端启动失败，端口未监听。", file=sys.stderr)
        return 1

    if args.headless:
        log.info("headless mode: http://127.0.0.1:%s", port)
        if sys.stdout:
            print(f"http://127.0.0.1:{port}", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    import webview  # 延迟导入：headless 模式不需要 GUI 依赖

    url = f"http://127.0.0.1:{port}/"
    webview.create_window(
        WINDOW_TITLE,
        url,
        width=1380,
        height=880,
        min_size=(1080, 680),
        background_color="#0d0f12",
        text_select=True,
    )
    log.info("opening window %s -> %s", WINDOW_TITLE, url)
    webview.start()
    log.info("window closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
