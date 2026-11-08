"""端到端：WebSocket 事件流跑一遍真实的循环（用 mock 模型，不花 token）。"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import Config  # noqa: E402
from server.api import create_app  # noqa: E402


def make_cfg(workspace: Path) -> Config:
    cfg = Config()
    cfg.mock = True
    cfg.workspace = workspace
    cfg.bash_timeout = 60
    return cfg


def test_state_and_files(tmp_path):
    app = create_app(make_cfg(tmp_path))
    with TestClient(app) as client:
        state = client.get("/api/state").json()
        assert state["config"]["mock"] is True
        assert {t["name"] for t in state["tools"]} == {"read", "write", "edit", "bash"}

        tree = client.get("/api/tree").json()
        names = {item["name"] for item in tree["items"]}
        assert "stats.py" in names and "test_stats.py" in names

        content = client.get("/api/file", params={"path": "stats.py"}).json()
        assert "def mean" in content["text"]

        assert client.get("/api/file", params={"path": "../../etc/passwd"}).status_code == 400


def test_ws_chat_runs_tools_and_reports_failure(tmp_path):
    app = create_app(make_cfg(tmp_path))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
            assert ws.receive_json()["type"] == "history"

            ws.send_json({"type": "chat", "text": "mean 对空列表处理有问题，改掉并跑测试"})

            calls: list[tuple[str, bool]] = []
            file_changed = []
            while True:
                event = ws.receive_json()
                if event["type"] == "tool_end":
                    calls.append((event["name"], event["ok"]))
                if event["type"] == "file_changed":
                    file_changed.append(event["path"])
                if event["type"] == "done":
                    assert event["reason"] == "finished"
                    break

            assert calls[0] == ("read", True)
            assert calls[1] == ("edit", False)  # 三次匹配，必须报错
            assert calls[2] == ("edit", True)  # 补上下文之后成功
            assert file_changed == ["stats.py"]

    assert "return None" in (tmp_path / "stats.py").read_text(encoding="utf-8")
