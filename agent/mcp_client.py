"""MCP 客户端：把外部 server 的工具并进同一份工具表。

关键点：stdio_client 的连接必须在「同一个 task」里进入和退出，
否则 anyio 的 cancel scope 会报跨 task 错误。所以每个 server 都有一个
专属后台 task，所有调用通过队列投递给它。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class McpError(Exception):
    pass


@dataclass
class McpServer:
    name: str
    spec: dict
    base_dir: Path
    session: ClientSession | None = None
    tools: list[Any] = field(default_factory=list)
    error: str = ""
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    _queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    _task: asyncio.Task | None = None
    _closed: bool = False

    # —— 生命周期 ——
    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"mcp-{self.name}")
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=20)
        except asyncio.TimeoutError:
            self.error = self.error or "启动超时（20s）"

    async def _run(self) -> None:
        command = str(self.spec.get("command") or "python")
        args = [self._abs_arg(a) for a in (self.spec.get("args") or [])]
        env = {**os.environ, **(self.spec.get("env") or {})}
        cwd = self._abs_arg(self.spec.get("cwd") or ".") if self.spec.get("cwd") else None

        if command in {"python", "python3", "py"}:
            command = self._python_for_server()
            if getattr(sys, "frozen", False) and self.spec.get("bundled"):
                # 打包版里没有解释器：让同一个 exe 把自己当成 stdio server 拉起来
                args = ["--run-mcp-server", self.name]

        params = StdioServerParameters(command=command, args=args, env=env, cwd=cwd)
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self.session = session
                    self.tools = list(listed.tools)
                    self.ready.set()
                    await self._serve()
        except Exception as exc:  # 第三方 server 挂了不应该拖垮整个应用
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            if not self.ready.is_set():
                self.ready.set()
            self.session = None

    async def _serve(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            name, arguments, future = item
            if future.cancelled():
                continue
            try:
                assert self.session is not None
                result = await self.session.call_tool(name, arguments)
                if not future.done():
                    future.set_result(result)
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)

    async def call(self, tool: str, arguments: dict, timeout: float = 60) -> Any:
        if self.session is None:
            raise McpError(self.error or f"MCP server「{self.name}」未连接")
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._queue.put((tool, arguments, future))
        return await asyncio.wait_for(future, timeout=timeout)

    async def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.put(None)
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()

    # —— 工具 ——
    def _abs_arg(self, value: str) -> str:
        path = Path(str(value))
        if path.is_absolute():
            return str(path)
        return str((self.base_dir / path).resolve())

    def _python_for_server(self) -> str:
        """打包后 sys.executable 是 GUI 程序，用同一个 exe 跑内置 server 会开窗口。

        所以打包版把 server 脚本作为数据带出去，再用 --run-mcp-server 交给同一个进程。
        """
        if getattr(sys, "frozen", False):
            return sys.executable
        return sys.executable or "python"

    def schemas(self) -> list[dict]:
        out = []
        for tool in self.tools:
            schema = getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}
            desc = (getattr(tool, "description", "") or "").strip().replace("\n", " ")
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": f"mcp__{self.name}__{tool.name}",
                        "description": f"[MCP:{self.name}] {desc[:400]}",
                        "parameters": schema,
                    },
                }
            )
        return out


class McpManager:
    def __init__(self, config_path: Path, base_dir: Path):
        self.config_path = Path(config_path)
        self.base_dir = Path(base_dir)
        self.servers: dict[str, McpServer] = {}

    async def start(self) -> None:
        if not self.config_path.exists():
            return
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for name, spec in (data.get("servers") or {}).items():
            if not spec or spec.get("enabled") is False:
                continue
            server = McpServer(name=name, spec=spec, base_dir=self.base_dir)
            self.servers[name] = server
            await server.start()

    def schemas(self) -> list[dict]:
        out = []
        for server in self.servers.values():
            out.extend(server.schemas())
        return out

    def route(self, full_name: str) -> tuple[McpServer, str] | None:
        if not full_name.startswith("mcp__"):
            return None
        parts = full_name.split("__", 2)
        if len(parts) != 3:
            return None
        _, server_name, tool_name = parts
        server = self.servers.get(server_name)
        return (server, tool_name) if server else None

    async def call(self, full_name: str, arguments: dict, timeout: float = 60):
        routed = self.route(full_name)
        if not routed:
            raise McpError(f"没有名为 {full_name} 的 MCP 工具")
        server, tool_name = routed
        result = await server.call(tool_name, arguments, timeout=timeout)
        return result

    def status(self) -> list[dict]:
        out = []
        for server in self.servers.values():
            out.append(
                {
                    "name": server.name,
                    "connected": bool(server.session),
                    "tools": [t.name for t in server.tools],
                    "error": server.error,
                }
            )
        return out

    async def stop(self) -> None:
        for server in self.servers.values():
            await server.stop()


def render_mcp_result(result: Any) -> str:
    """把 MCP 的 content 数组压成模型能读的一段文本。"""
    chunks: list[str] = []
    for item in getattr(result, "content", []) or []:
        kind = getattr(item, "type", "")
        if kind == "text":
            chunks.append(getattr(item, "text", ""))
        elif kind == "image":
            chunks.append(f"[图片 {getattr(item, 'mimeType', 'image')}]")
        elif kind == "resource":
            resource = getattr(item, "resource", None)
            chunks.append(f"[资源 {getattr(resource, 'uri', '')}]")
        else:
            chunks.append(str(item))
    if getattr(result, "isError", False):
        chunks.append("[MCP 工具返回错误]")
    return "\n".join(chunk for chunk in chunks if chunk).strip() or "（MCP 工具没有返回文本）"
