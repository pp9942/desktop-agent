"""智能体循环：决策 → 动作 → 观察 → 再决策。

三道护栏（第 2 讲的核心）：
- 步数：max_steps，防止无限循环；
- 时间：单步 step_timeout + bash 自己的 timeout；
- 预算：上下文超过 context_budget_chars 就压缩，不让窗口悄悄爆掉。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import Config
from .llm import LLMError
from .mcp_client import McpManager, McpError, render_mcp_result
from .tools import Tool, ToolError, ToolResult, Workspace

Emit = Callable[[dict], Awaitable[None]]

SYSTEM_PROMPT = """你是一个跑在桌面窗口里的编程智能体，工作目录是 {workspace}。

你能用的工具：
- read / write / edit / bash：读写文件、执行命令。
- mcp__* 工具来自外部 MCP server，用法同普通工具。

工作方式（必须遵守）：
1. 改代码之前先 read。不要凭记忆猜文件内容。
2. 只改该改的地方，优先 edit；只有新建文件或整体重写才用 write。
3. 改完要验证：能跑测试就跑测试；没有测试就至少再读一遍改动的上下文。
4. 命令失败时读 stderr 再决定下一步，不要原样重试同一条命令。
5. 不要假装执行过命令 —— 只有工具返回的结果才算事实。
6. 用中文回答，简洁、直给结论，不要复述工具输出，不要写长篇总结。

如果用户的请求不清楚，就用一次工具调用去确认，而不是反复追问。"""


class SessionStore:
    """会话持久化：关掉窗口再打开，上一轮的对话还在。"""

    def __init__(self, path: Path):
        self.path = path

    def save(self, messages: list[dict], meta: dict | None = None) -> None:
        try:
            payload = {"saved_at": time.time(), "meta": meta or {}, "messages": messages}
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"messages": [], "meta": {}}


def _sanitize_history(messages: list[dict]) -> list[dict]:
    """把历史修成 API 能接受的形状：assistant 的每个 tool_call 都有配对的 tool 响应。

    两处会产生断裂：恢复会话（上次运行可能死在工具执行中途），
    以及上下文压缩（切断点落在 tool_calls 和 tool 响应之间）。
    处理方式：孤儿 tool 消息丢弃，缺失的响应补一条占位 —— 比直接丢掉
    assistant 消息好，因为那会让「我调了工具」这个事实消失。
    """
    out: list[dict] = []
    pending: list[dict] = []  # 还没等到响应的 tool_call

    def flush() -> None:
        for call in pending:
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or "",
                    "name": (call.get("function") or {}).get("name", "tool"),
                    "content": "（会话在此中断，工具结果已丢失）",
                }
            )
        pending.clear()

    for msg in messages:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            flush()  # 上一批还没配完就来新的：先补占位，保证顺序合法
            out.append(msg)
            pending.extend(msg.get("tool_calls") or [])
        elif role == "tool":
            call = next((c for c in pending if c.get("id") == msg.get("tool_call_id")), None)
            if call is not None:
                pending.remove(call)
                out.append(msg)
            # 配不上对的 tool 消息是孤儿，丢弃
        else:
            flush()
            out.append(msg)
    flush()
    return out


class AgentLoop:
    def __init__(
        self,
        cfg: Config,
        client: Any,
        tools: dict[str, Tool],
        mcp: McpManager,
        emit: Emit,
        store: SessionStore | None = None,
    ):
        self.cfg = cfg
        self.client = client
        self.tools = tools
        self.mcp = mcp
        self.emit = emit
        self.store = store
        self.stop_event = asyncio.Event()
        self.messages: list[dict] = []
        self.steps = 0
        self.tool_chars = 0

    # ------------------------------------------------------------------ #
    def request_stop(self) -> None:
        self.stop_event.set()

    def restore(self) -> list[dict]:
        data = self.store.load() if self.store else {"messages": []}
        msgs = data.get("messages") or []
        self.messages = _sanitize_history(msgs)
        return self.messages

    # ------------------------------------------------------------------ #
    async def run(self, user_text: str) -> str:
        self.stop_event.clear()
        self.steps = 0
        self.tool_chars = 0

        if not self.messages:
            self.messages = []
        self.messages.append({"role": "user", "content": user_text})

        schemas = [tool.schema() for tool in self.tools.values()] + self.mcp.schemas()
        reason = "finished"

        for step in range(1, self.cfg.max_steps + 1):
            if self.stop_event.is_set():
                reason = "stopped"
                break
            self.steps = step
            await self.emit({"type": "step", "n": step, "max": self.cfg.max_steps})

            try:
                assistant = await self._ask(schemas)
            except LLMError as exc:
                await self.emit({"type": "error", "message": str(exc)})
                reason = "error"
                break
            except asyncio.CancelledError:
                reason = "stopped"
                break

            if self.stop_event.is_set():
                # 模型刚说完话，用户已经按了停止：这一轮当作被中断，不再继续
                self.messages.append(assistant)
                reason = "stopped"
                break

            self.messages.append(assistant)
            calls = assistant.get("tool_calls") or []
            if not calls:
                reason = "finished"
                break

            for call in calls:
                if self.stop_event.is_set():
                    reason = "stopped"
                    break
                await self._run_tool_call(call)

            if reason == "stopped":
                break

            await self._compress()
        else:
            reason = "max_steps"

        if reason == "stopped":
            await self.emit({"type": "notice", "message": "已手动中断 —— 工具执行结果已保留。"})
        elif reason == "max_steps":
            await self.emit(
                {
                    "type": "notice",
                    "message": f"到达步数上限（{self.cfg.max_steps} 步）已停止。把任务拆小，或调大 AGENT_MAX_STEPS。",
                }
            )

        if self.store:
            self.store.save(self.messages, {"steps": self.steps})
        await self.emit({"type": "done", "reason": reason, "steps": self.steps})
        return reason

    # ------------------------------------------------------------------ #
    async def _ask(self, schemas: list[dict]) -> dict:
        payload = [{"role": "system", "content": SYSTEM_PROMPT.format(workspace=self.cfg.workspace)}]
        payload.extend(self.messages)

        async def on_delta(text: str) -> None:
            # 流式过程中也要能停：每个 token 都是一个检查点
            if self.stop_event.is_set():
                raise asyncio.CancelledError
            await self.emit({"type": "assistant_delta", "text": text})

        return await self.client.complete(payload, schemas, on_delta)

    async def _run_tool_call(self, call: dict) -> None:
        fn = call.get("function") or {}
        name = fn.get("name") or ""
        call_id = call.get("id") or f"call_{int(time.time() * 1000)}"
        try:
            args = json.loads(fn.get("arguments") or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments 必须是 JSON 对象")
        except (json.JSONDecodeError, ValueError) as exc:
            result = ToolResult(ok=False, text=f"工具参数不是合法 JSON：{exc}\n收到的原文：{fn.get('arguments')}")
            await self._finish_tool(call_id, name, {}, result, 0.0)
            self.messages.append(self._tool_message(call_id, name, result))
            return

        await self.emit({"type": "tool_start", "id": call_id, "name": name, "args": args})
        started = time.time()
        result = await self._execute(name, args)
        duration = time.time() - started
        await self._finish_tool(call_id, name, args, result, duration)
        self.messages.append(self._tool_message(call_id, name, result))

        if result.diff:
            await self.emit({"type": "file_changed", "path": result.meta.get("path", ""), "diff": result.diff})
        for image in result.images[:2]:
            self.messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "（read 返回的图片）"},
                        {"type": "image_url", "image_url": {"url": image}},
                    ],
                }
            )

    async def _execute(self, name: str, args: dict) -> ToolResult:
        # MCP 工具
        if name.startswith("mcp__"):
            try:
                raw = await asyncio.wait_for(
                    self.mcp.call(name, args, timeout=self.cfg.step_timeout),
                    timeout=self.cfg.step_timeout + 5,
                )
                text = render_mcp_result(raw)
                return ToolResult(ok=not getattr(raw, "isError", False), text=text, meta={"source": name.split("__")[1]})
            except (McpError, asyncio.TimeoutError) as exc:
                return ToolResult(ok=False, text=f"MCP 工具失败：{exc}")
            except Exception as exc:
                return ToolResult(ok=False, text=f"MCP 工具异常：{type(exc).__name__}: {exc}")

        tool = self.tools.get(name)
        if tool is None:
            available = ", ".join(list(self.tools) + [s["function"]["name"] for s in self.mcp.schemas()])
            return ToolResult(ok=False, text=f"没有名为「{name}」的工具。可用的有：{available}。请选一个重试。")

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(lambda: tool.func(**args)), timeout=self.cfg.step_timeout
            )
        except asyncio.TimeoutError:
            return ToolResult(
                ok=False,
                text=f"{name} 超过单步上限 {self.cfg.step_timeout:.0f}s 被中止。把任务拆成更小的步骤。",
            )
        except TypeError as exc:
            return ToolResult(ok=False, text=f"{name} 的参数不对：{exc}。检查参数名和类型后重试。")
        except ToolError as exc:
            return ToolResult(ok=False, text=str(exc))
        except Exception as exc:
            return ToolResult(ok=False, text=f"{name} 出错：{type(exc).__name__}: {exc}")

    async def _finish_tool(self, call_id: str, name: str, args: dict, result: ToolResult, duration: float) -> None:
        text = result.text or ""
        self.tool_chars += len(text)
        preview = text if len(text) <= 4000 else text[:3000] + "\n…（预览截断）"
        lines = [line for line in text.splitlines() if line.strip()]
        if result.meta.get("exit_code") is not None:
            # 命令类工具：结果在最后一行，不是命令本身
            summary = lines[-1][:120] if lines else ""
            if result.meta.get("failed"):
                summary = f"exit {result.meta['exit_code']} · {summary}"
        else:
            summary = lines[0][:120] if lines else ""
        await self.emit(
            {
                "type": "tool_end",
                "id": call_id,
                "name": name,
                "ok": result.ok,
                "duration": round(duration, 2),
                "summary": summary,
                "output": preview,
                "diff": result.diff[:20000],
                "meta": {k: v for k, v in result.meta.items() if k != "diff"},
                "source": "mcp" if name.startswith("mcp__") else "local",
            }
        )

    @staticmethod
    def _tool_message(call_id: str, name: str, result: ToolResult) -> dict:
        body = result.text if result.ok else f"[工具失败] {result.text}"
        return {"role": "tool", "tool_call_id": call_id, "name": name, "content": body[:20000]}

    # ------------------------------------------------------------------ #
    async def _compress(self) -> None:
        """超出预算就压缩中间的工具输出，保留开头和最近的对话。"""
        size = sum(len(json.dumps(m, ensure_ascii=False)) for m in self.messages)
        if size <= self.cfg.context_budget_chars:
            return

        keep = max(4, self.cfg.keep_recent_messages)
        if len(self.messages) <= keep + 2:
            return

        head = self.messages[:1]  # 第一条 user
        middle = self.messages[1 : -keep]
        tail = self.messages[-keep:]

        notes = []
        for msg in middle:
            role = msg.get("role")
            if role == "tool":
                notes.append(f"- {msg.get('name', 'tool')} 返回：{str(msg.get('content'))[:120]}…")
            elif role == "assistant" and msg.get("content"):
                notes.append(f"- 我说过：{str(msg['content'])[:100]}…")
        summary = "\n".join(notes[-20:]) or "（无）"

        self.messages = _sanitize_history(
            head
            + [{"role": "user", "content": f"[上下文已压缩，以下是之前步骤的摘要]\n{summary[:2000]}"}]
            + tail
        )
        await self.emit(
            {
                "type": "notice",
                "message": f"上下文超过 {self.cfg.context_budget_chars // 1000}k 字符，已压缩中间 {len(middle)} 条消息。",
            }
        )
