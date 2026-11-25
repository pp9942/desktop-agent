"""增量 B 第三块：只读 reviewer 子 agent。"""

from __future__ import annotations

import pytest

from agent.reviewer import REVIEW_PROMPT, Reviewer


class FakeClient:
    """按预设脚本回话；记录每次调用收到的 tools。"""

    def __init__(self, script):
        self.script = list(script)
        self.seen_tools = []

    async def complete(self, messages, tools=None, on_delta=None):
        self.seen_tools.append([t["function"]["name"] for t in (tools or [])])
        return self.script.pop(0)


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    from agent.config import Config

    c = Config.load(str(tmp_path / "ws"))
    c.ensure_workspace()
    (c.workspace / "stats.py").write_text("def mean(nums):\n    return None\n", encoding="utf-8")
    return c


@pytest.fixture()
def read_tools(cfg):
    from agent.tools import Workspace, build_tools

    return {k: v for k, v in build_tools(Workspace(cfg.workspace)).items() if k == "read"}


CHANGES = [{"path": "stats.py", "diff": "-    return 0\n+    return None"}]


@pytest.mark.asyncio
async def test_reviewer_can_read_but_only_read(cfg, read_tools):
    client = FakeClient(
        [
            {"content": None, "tool_calls": [{"id": "c1", "function": {"name": "read", "arguments": '{"path": "stats.py"}'}}]},
            {"content": "改动达成目的，测试覆盖了空列表分支。", "tool_calls": []},
        ]
    )
    r = Reviewer(cfg, read_tools)
    text = await r.review(CHANGES, client)
    assert "测试覆盖" in text
    # 工具表里只有 read —— 结构性只读
    assert client.seen_tools[0] == ["read"]


@pytest.mark.asyncio
async def test_reviewer_rejects_non_read_calls(cfg, read_tools):
    client = FakeClient(
        [
            {"content": None, "tool_calls": [{"id": "c1", "function": {"name": "write", "arguments": '{"path":"x.py","content":"hacked"}'}}]},
            {"content": "仍然只有结论：改动没问题。", "tool_calls": []},
        ]
    )
    r = Reviewer(cfg, read_tools)
    text = await r.review(CHANGES, client)
    assert "结论" in text
    assert not (cfg.workspace / "x.py").exists()  # 就算它想写也写不了


@pytest.mark.asyncio
async def test_mock_mode_returns_fixed_template(cfg, read_tools):
    cfg.mock = True
    r = Reviewer(cfg, read_tools)
    text = await r.review(CHANGES, FakeClient([]))
    assert "离线演示评审" in text
    assert r.report()["reviewer_runs"] == 1


@pytest.mark.asyncio
async def test_no_changes_means_no_review(cfg, read_tools):
    r = Reviewer(cfg, read_tools)
    assert await r.review([], FakeClient([])) is None
    assert r.report()["reviewer_runs"] == 0


def test_review_prompt_declares_readonly():
    assert "只读" in REVIEW_PROMPT and "没有写工具" in REVIEW_PROMPT