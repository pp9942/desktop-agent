"""后端：FastAPI + WebSocket。

前端永远不碰 API Key —— 所有模型调用都发生在这一侧。
WebSocket 只做一件事：把循环里发生的事，一条一条推给界面。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agent.config import Config, resource_dir, user_dir
from agent.llm import build_client
from agent.loop import AgentLoop, SessionStore
from agent.mcp_client import McpManager
from agent.tools import Workspace, build_tools

UI_DIR = resource_dir() / "ui"


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg.ensure_workspace()
        seed_workspace(cfg.workspace)
        ws = Workspace(cfg.workspace)
        mcp = McpManager(resource_dir() / "mcp.json", resource_dir())
        await mcp.start()

        app.state.cfg = cfg
        app.state.workspace = ws
        app.state.tools = build_tools(ws)
        app.state.mcp = mcp
        app.state.store = SessionStore(user_dir() / "last_session.json")
        app.state.client = build_client(cfg)
        try:
            yield
        finally:
            await mcp.stop()

    app = FastAPI(title="Desktop Agent", lifespan=lifespan)

    if UI_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")

    @app.get("/")
    async def index():
        return FileResponse(str(UI_DIR / "index.html"))

    @app.get("/api/state")
    async def state():
        return {
            "config": cfg.public(),
            "tools": [
                {"name": t.name, "description": t.description.splitlines()[0]}
                for t in app.state.tools.values()
            ],
            "mcp": app.state.mcp.status(),
        }

    @app.get("/api/settings")
    async def get_settings():
        return {
            "base_url": cfg.base_url,
            "model": cfg.model,
            "has_key": bool(cfg.api_key),
            "mock": cfg.mock,
        }

    @app.post("/api/settings")
    async def save_settings(payload: dict):
        base_url = (payload.get("base_url") or cfg.base_url).strip().rstrip("/")
        model = (payload.get("model") or cfg.model).strip()
        api_key = (payload.get("api_key") or "").strip()
        if not base_url.startswith(("http://", "https://")):
            raise HTTPException(400, "Base URL 必须以 http:// 或 https:// 开头")
        if "/chat/completions" in base_url:
            raise HTTPException(400, "Base URL 不要带 /chat/completions，只填到 /v1")
        if not model:
            raise HTTPException(400, "模型名不能为空")

        cfg.base_url = base_url
        cfg.model = model
        if api_key or cfg.api_key:
            cfg.mock = False  # 填了 Key 就走真模型
        cfg.save_user(api_key or None)
        app.state.client = build_client(cfg)
        return {"ok": True, "config": cfg.public()}

    @app.get("/api/tree")
    async def tree(path: str = Query(".")):
        try:
            target = app.state.workspace.resolve(path)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        if not target.is_dir():
            raise HTTPException(400, "不是目录")
        items = []
        for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if child.name.startswith(".") or child.name == "__pycache__":
                continue
            items.append(
                {
                    "name": child.name,
                    "path": app.state.workspace.relative(child),
                    "dir": child.is_dir(),
                    "size": child.stat().st_size if child.is_file() else 0,
                }
            )
        return {"path": app.state.workspace.relative(target), "items": items}

    @app.get("/api/file")
    async def file(path: str = Query(...)):
        try:
            target = app.state.workspace.resolve(path)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        if not target.is_file():
            raise HTTPException(404, "文件不存在")
        if target.stat().st_size > 2 * 1024 * 1024:
            raise HTTPException(400, "文件太大，编辑器不预览")
        try:
            text = target.read_text(encoding="utf-8")
            binary = False
        except UnicodeDecodeError:
            text = "（二进制文件，不预览）"
            binary = True
        return {"path": app.state.workspace.relative(target), "text": text, "binary": binary}

    @app.get("/api/session")
    async def session():
        data = app.state.store.load()
        messages = [m for m in data.get("messages", []) if m.get("role") in {"user", "assistant"}]
        return {"messages": messages, "saved_at": data.get("saved_at")}

    @app.websocket("/ws")
    async def websocket(websocket: WebSocket):
        await websocket.accept()
        lock = asyncio.Lock()
        send_failed = False

        async def emit(event: dict) -> None:
            nonlocal send_failed
            if send_failed:
                return
            async with lock:
                try:
                    await websocket.send_json(event)
                except Exception:
                    send_failed = True

        loop = AgentLoop(
            cfg=cfg,
            client=app.state.client,
            tools=app.state.tools,
            mcp=app.state.mcp,
            emit=emit,
            store=app.state.store,
        )
        history = loop.restore()
        await websocket.send_json({"type": "hello", "config": cfg.public(), "mcp": app.state.mcp.status()})
        await websocket.send_json({"type": "history", "messages": public_history(history)})

        running: asyncio.Task | None = None
        try:
            while True:
                try:
                    data = await websocket.receive_json()
                except (WebSocketDisconnect, RuntimeError):
                    break
                kind = data.get("type")

                if kind == "chat":
                    text = (data.get("text") or "").strip()
                    if not text:
                        continue
                    if running and not running.done():
                        await emit({"type": "notice", "message": "上一轮还没结束，先点停止。"})
                        continue
                    loop.client = app.state.client  # 设置可能刚被改过
                    running = asyncio.create_task(_guarded(loop.run(text), emit))

                elif kind == "stop":
                    loop.request_stop()

                elif kind == "reset":
                    if running and not running.done():
                        loop.request_stop()
                        running.cancel()
                    loop.messages = []
                    app.state.store.save([])
                    await emit({"type": "reset"})

                elif kind == "ping":
                    await emit({"type": "pong"})
        finally:
            loop.request_stop()
            if running and not running.done():
                running.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await running

    return app


async def _guarded(coro, emit) -> None:
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # 循环里的意外错误不能让 WS 静默死掉
        await emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        await emit({"type": "done", "reason": "error", "steps": 0})


def public_history(messages: list[dict]) -> list[dict]:
    """只回放用户和助手的文本，工具过程不进历史，避免界面一打开就是几十张卡片。"""
    out = []
    for msg in messages:
        if msg.get("role") in {"user", "assistant"} and isinstance(msg.get("content"), str):
            out.append({"role": msg["role"], "content": msg["content"]})
    return out


# --------------------------------------------------------------------------- #
# 首次运行时给一个能立刻练手的工作区
# --------------------------------------------------------------------------- #
SEED_FILES = {
    "stats.py": '''"""三个统计函数，测试在 test_stats.py。"""


def mean(nums):
    if not nums:
        return 0
    total = 0
    for n in nums:
        total += n
    return total / len(nums)


def median(nums):
    if not nums:
        return 0
    ordered = sorted(nums)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def mode(nums):
    if not nums:
        return 0
    counts = {}
    for n in nums:
        counts[n] = counts.get(n, 0) + 1
    return max(counts, key=counts.get)
''',
    "test_stats.py": '''import unittest

from stats import mean, median, mode


class StatsTest(unittest.TestCase):
    def test_mean(self):
        self.assertEqual(mean([1, 2, 3]), 2)

    def test_mean_empty(self):
        self.assertIsNone(mean([]))

    def test_median(self):
        self.assertEqual(median([3, 1, 2]), 2)

    def test_median_empty(self):
        self.assertEqual(median([]), 0)

    def test_mode(self):
        self.assertEqual(mode([1, 1, 2]), 1)


if __name__ == "__main__":
    unittest.main()
''',
    "README.md": """# 练习工作区

这里有三个统计函数，`mean` 对空列表的处理不太对 —— 试着让窗口里的智能体改掉它。

```bash
python -m unittest -q
```
""",
}


def seed_workspace(root: Path) -> None:
    if any(root.iterdir()):
        return
    for name, content in SEED_FILES.items():
        (root / name).write_text(content, encoding="utf-8")
