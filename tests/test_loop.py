"""护栏测试：中断要真的停下，步数上限要真的停。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import Config  # noqa: E402
from agent.loop import AgentLoop, SessionStore, _sanitize_history  # noqa: E402
from agent.tools import Workspace, build_tools  # noqa: E402


class FakeMcp:
    def schemas(self):
        return []

    async def call(self, *args, **kwargs):
        raise AssertionError("不该走到 MCP")

    async def stop(self):
        pass


class SlowClient:
    """第一次不返回工具调用，只是慢慢说一句话 —— 用来测中断。"""

    def __init__(self):
        self.calls = 0

    async def complete(self, messages, tools=None, on_delta=None):
        self.calls += 1
        await asyncio.sleep(0.4)
        return {"role": "assistant", "content": "好的"}


class LoopClient:
    """每轮都要求一次工具调用 —— 用来测步数上限。"""

    def __init__(self):
        self.calls = 0

    async def complete(self, messages, tools=None, on_delta=None):
        self.calls += 1
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"c{self.calls}",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "echo hi"}'},
                }
            ],
        }


def make_loop(tmp_path: Path, client) -> tuple[AgentLoop, list[dict]]:
    cfg = Config()
    cfg.workspace = tmp_path
    cfg.max_steps = 3
    events: list[dict] = []

    async def emit(event):
        events.append(event)

    loop = AgentLoop(cfg, client, build_tools(Workspace(tmp_path)), FakeMcp(), emit)
    return loop, events


def test_stop_interrupts_before_next_step(tmp_path):
    async def scenario():
        loop, events = make_loop(tmp_path, SlowClient())
        task = asyncio.create_task(loop.run("慢一点"))
        await asyncio.sleep(0.1)
        loop.request_stop()
        return await task, events

    reason, events = asyncio.run(scenario())
    assert reason == "stopped"
    assert any(e["type"] == "done" and e["reason"] == "stopped" for e in events)


def test_max_steps_guard(tmp_path):
    async def scenario():
        loop, events = make_loop(tmp_path, LoopClient())
        return await loop.run("一直转"), events

    reason, events = asyncio.run(scenario())
    assert reason == "max_steps"
    assert sum(1 for e in events if e["type"] == "tool_start") == 3
    assert any(e["type"] == "notice" and "步数上限" in e["message"] for e in events)


def _pairing_ok(messages) -> bool:
    """模拟 API 的校验：每个 tool_call_id 后面必须有配对的 tool 消息。"""
    pending = set()
    for m in messages:
        if m.get("role") == "assistant":
            pending |= {c.get("id") for c in m.get("tool_calls") or []}
        elif m.get("role") == "tool":
            if m.get("tool_call_id") not in pending:
                return False
            pending.discard(m.get("tool_call_id"))
    return not pending


def test_sanitize_repairs_history_killed_mid_tool():
    """上次运行死在工具执行中途：恢复时必须补齐占位响应，否则 API 直接 400。"""
    broken = [
        {"role": "user", "content": "改一下 stats.py"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read", "arguments": '{"path": "stats.py"}'}},
                {"id": "c2", "type": "function", "function": {"name": "bash", "arguments": '{"command": "ls"}'}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "read", "content": "def mean(nums): ..."},
        # c2 的响应丢了，后面直接是新的 user 消息
        {"role": "user", "content": "继续"},
    ]
    fixed = _sanitize_history(broken)
    assert _pairing_ok(fixed)
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "c2" for m in fixed)
    assert fixed[0]["role"] == "user" and fixed[-1]["role"] == "user"


def test_sanitize_drops_orphan_tool_and_pairs_stray_calls():
    """孤儿 tool 消息丢弃；连续两个 tool_calls 中间夹了 user 也要先补占位。"""
    orphan = [{"role": "tool", "tool_call_id": "ghost", "content": "凭空出现"}]
    assert all(m.get("role") != "tool" for m in _sanitize_history(orphan))

    calls = lambda i: {  # noqa: E731
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "read", "arguments": "{}"}}],
    }
    fixed = _sanitize_history([calls(1), {"role": "user", "content": "别管刚才"}, calls(2)])
    assert _pairing_ok(fixed)


def test_restore_sanitizes_saved_session(tmp_path):
    """持久化里存着断裂历史时，restore 出来的必须能直接发给 API。"""
    store_path = tmp_path / "session.json"
    store_path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "hi"},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{"id": "c9", "type": "function", "function": {"name": "edit", "arguments": "{}"}}],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = Config()
    cfg.workspace = tmp_path
    loop, _ = make_loop(tmp_path, SlowClient())
    loop.store = SessionStore(store_path)
    restored = loop.restore()
    assert _pairing_ok(restored)
