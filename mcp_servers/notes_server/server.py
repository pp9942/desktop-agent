"""一个独立的 MCP server：把「笔记」暴露给任何支持 MCP 的客户端。

它跑在自己进程里（stdio），桌面端只是它的一个客户端 ——
这正是 MCP 的意义：同一份工具，Cursor / Claude Code / 我们自己的窗口都能接。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

DB = Path(os.environ.get("NOTES_DB") or (Path.home() / ".desktop-agent" / "notes.json"))

mcp = MCPServer("notes")


def _load() -> list[dict]:
    try:
        return json.loads(DB.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save(notes: list[dict]) -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    tmp = DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, DB)


@mcp.tool()
def save_note(title: str, content: str) -> str:
    """保存一条笔记。标题重复时覆盖旧内容。

    什么时候该用：用户说「记一下」「记到笔记里」，或者你在排查问题时
    发现了一个值得留存的结论（比如某个坑的解法）。
    """
    if not title.strip():
        return "保存失败：title 不能为空。"
    notes = [n for n in _load() if n.get("title") != title.strip()]
    notes.append({"title": title.strip(), "content": content.strip()})
    _save(notes)
    return f"已保存笔记「{title.strip()}」，当前共 {len(notes)} 条。"


@mcp.tool()
def search_notes(query: str) -> str:
    """按关键字搜索笔记，标题和正文都匹配。返回最多 5 条。

    什么时候该用：用户提到「上次那个问题」「之前记过的」，
    先搜一遍，不要凭空回答。
    """
    q = (query or "").strip().lower()
    if not q:
        return "搜索失败：query 不能为空。"
    hits = [
        n
        for n in _load()
        if q in str(n.get("title", "")).lower() or q in str(n.get("content", "")).lower()
    ][:5]
    if not hits:
        return f"没有找到包含「{query}」的笔记（共 {len(_load())} 条）。"
    return "\n\n".join(f"# {n['title']}\n{n['content']}" for n in hits)


@mcp.tool()
def list_notes() -> str:
    """列出所有笔记的标题。想知道记过什么的时候用。"""
    notes = _load()
    if not notes:
        return "还没有任何笔记。"
    return "\n".join(f"- {n['title']}" for n in notes)


def serve() -> None:
    mcp.run()


if __name__ == "__main__":
    serve()
