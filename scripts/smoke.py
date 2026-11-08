"""离线验收：不开窗口、不花 token，跑一遍完整循环并把事件打到控制台。

    python scripts/smoke.py

用的是脚本化假模型（MockClient），工具执行全是真的 —— 读文件、改文件、跑 pytest。
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from agent.config import Config  # noqa: E402
from agent.llm import MockClient  # noqa: E402
from agent.loop import AgentLoop, SessionStore  # noqa: E402
from agent.mcp_client import McpManager  # noqa: E402
from agent.tools import Workspace, build_tools  # noqa: E402
from server.api import seed_workspace  # noqa: E402


async def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="agent-smoke-"))
    seed_workspace(root)

    cfg = Config.load(str(root))
    cfg.mock = True

    workspace = Workspace(root)
    mcp = McpManager(ROOT / "mcp.json", ROOT)
    await mcp.start()

    async def emit(event: dict) -> None:
        if event["type"] == "assistant_delta":
            print(event["text"], end="", flush=True)
            return
        body = {k: v for k, v in event.items() if k != "type"}
        print(f"\n[{event['type']}] {str(body)[:800]}")

    loop = AgentLoop(
        cfg,
        MockClient(cfg),
        build_tools(workspace),
        mcp,
        emit,
        SessionStore(root / "session.json"),
    )
    reason = await loop.run("mean 对空列表处理有问题，改掉并跑测试")
    print(f"\nREASON: {reason}  STEPS: {loop.steps}")
    print("MCP:", mcp.status())
    await mcp.stop()
    shutil.rmtree(root, ignore_errors=True)
    return 0 if reason == "finished" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
