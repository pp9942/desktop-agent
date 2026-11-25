"""增量 C：崩溃续跑端到端 —— kill -9 后从落盘的步数继续，直到 finished。

流程：子进程起 mock 后端 → WS 发 chat → 收到第二个 tool_end 后
taskkill /F（等价 kill -9，不给任何清理机会）→ 重启子进程 →
hello.resumable 为真 → 发 resume → 剧本从落盘步数继续 → done。
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_backend(port: int, env: dict) -> subprocess.Popen:
    e = os.environ.copy()
    e.update(env)
    return subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--headless", "--port", str(port)],
        cwd=str(ROOT),
        env=e,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_up(port: int, timeout: float = 25.0) -> None:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.4)
    raise TimeoutError(f"backend {port} not up")


@pytest.mark.asyncio
async def test_kill9_then_resume(tmp_path):
    import websockets

    port = _free_port()
    env = {
        "AGENT_MOCK": "1",
        "AGENT_DATA_DIR": str(tmp_path / "data"),
        "AGENT_WORKSPACE": str(ROOT / "workspace"),
    }
    proc = _start_backend(port, env)
    try:
        _wait_up(port)
        seen_tool_ends = 0
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as w:
            await w.recv()  # hello
            await w.recv()  # history
            await w.send(json.dumps({"type": "chat", "text": "mean 对空列表处理有问题，改掉并跑测试"}))
            # 收到第二个 tool_end 就杀，模拟 kill -9（不给后端任何清理机会）
            try:
                while seen_tool_ends < 2:
                    ev = json.loads(await asyncio.wait_for(w.recv(), timeout=60))
                    if ev["type"] == "tool_end":
                        seen_tool_ends += 1
            except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed, OSError):
                pass  # 杀进程会导致连接断开，属预期
    finally:
        proc.kill()  # kill -9 等价
        proc.wait(timeout=10)

    # 落盘状态：未 finished，步数已记录
    from agent.config import user_dir
    import agent.config as cfgmod

    monkey_env = {"AGENT_DATA_DIR": str(tmp_path / "data")}
    old = os.environ.get("AGENT_DATA_DIR")
    os.environ["AGENT_DATA_DIR"] = monkey_env["AGENT_DATA_DIR"]
    try:
        store_path = user_dir() / "last_session.json"
        payload = json.loads(store_path.read_text(encoding="utf-8"))
        assert payload["meta"]["finished"] is False  # 中途死的，没打完成标记
        assert payload["meta"]["steps"] >= 2  # 已完成至少 2 步并落盘
        saved_steps = payload["meta"]["steps"]
        roles = [m.get("role") for m in payload["messages"]]
        assert "tool" in roles  # 崩溃前的工具结果完整落盘（恢复后模型上下文才连续）
    finally:
        if old is None:
            os.environ.pop("AGENT_DATA_DIR", None)
        else:
            os.environ["AGENT_DATA_DIR"] = old

    # 重启后端，恢复并续跑
    proc2 = _start_backend(port, env)
    try:
        _wait_up(port)
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as w:
            hello = json.loads(await asyncio.wait_for(w.recv(), timeout=20))
            assert hello["type"] == "hello"
            assert hello["resumable"] is True  # 识别到上次没跑完
            history = json.loads(await asyncio.wait_for(w.recv(), timeout=20))
            # UI 回放按设计只含 user/assistant 文本；工具消息的完整性已在落盘里断言
            assert history["type"] == "history"
            assert history["messages"]

            await w.send(json.dumps({"type": "chat", "text": "继续", "resume": True}))
            done = None
            while done is None:
                ev = json.loads(await asyncio.wait_for(w.recv(), timeout=90))
                if ev["type"] == "done":
                    done = ev
            assert done["reason"] == "finished"  # 从断点继续跑到了终点
            # mock 剧本共 5 步；恢复轮只跑了剩余步数 = 没有重放已完成的部分
            assert done["steps"] == 5 - saved_steps
    finally:
        proc2.kill()
        proc2.wait(timeout=10)

    # 结束后落盘标记为 finished —— 下次启动不再提示可恢复
    os.environ["AGENT_DATA_DIR"] = monkey_env["AGENT_DATA_DIR"]
    try:
        payload = json.loads((user_dir() / "last_session.json").read_text(encoding="utf-8"))
        assert payload["meta"]["finished"] is True
    finally:
        if old is None:
            os.environ.pop("AGENT_DATA_DIR", None)
        else:
            os.environ["AGENT_DATA_DIR"] = old