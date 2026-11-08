"""四个工具的行为测试。

重点不是覆盖率，而是把「最容易做错的地方」钉住：
分页提示、唯一性校验、原子写、路径逃逸、超时。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.tools import (  # noqa: E402
    ToolError,
    Workspace,
    tool_bash,
    tool_edit,
    tool_read,
    tool_write,
)


@pytest.fixture()
def ws(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path)


def test_read_reports_line_numbers_and_tail(ws: Workspace):
    (ws.root / "big.py").write_text("\n".join(f"line {i}" for i in range(1, 301)), encoding="utf-8")
    result = tool_read(ws, "big.py", offset=1, limit=200)
    assert result.ok
    assert "共 300 行" in result.text
    assert "还有 100 行未显示" in result.text
    assert "  1| line 1" in result.text


def test_read_missing_file_lists_siblings(ws: Workspace):
    (ws.root / "a.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        tool_read(ws, "nope.py")
    assert "a.py" in str(exc.value)


def test_read_rejects_path_escape(ws: Workspace):
    with pytest.raises(ToolError) as exc:
        tool_read(ws, "../../etc/passwd")
    assert "工作区之外" in str(exc.value)


def test_write_creates_parents_and_reports_delta(ws: Workspace):
    result = tool_write(ws, "pkg/sub/mod.py", "a = 1\nb = 2\n")
    assert result.ok
    assert (ws.root / "pkg/sub/mod.py").read_text(encoding="utf-8") == "a = 1\nb = 2\n"
    assert "2 行" in result.text


def test_edit_requires_unique_match(ws: Workspace):
    target = ws.root / "dup.py"
    target.write_text("def a():\n    return 0\n\ndef b():\n    return 0\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        tool_edit(ws, "dup.py", "    return 0", "    return None")
    message = str(exc.value)
    assert "出现了 2 次" in message
    assert target.read_text(encoding="utf-8").count("return 0") == 2  # 没被改


def test_edit_with_context_succeeds_and_returns_diff(ws: Workspace):
    target = ws.root / "dup.py"
    target.write_text("def a():\n    return 0\n\ndef b():\n    return 0\n", encoding="utf-8")
    result = tool_edit(ws, "dup.py", "def a():\n    return 0", "def a():\n    return None")
    assert result.ok
    assert "+    return None" in result.diff and "-    return 0" in result.diff
    assert target.read_text(encoding="utf-8").splitlines()[1] == "    return None"


def test_edit_not_found_gives_hint(ws: Workspace):
    (ws.root / "x.py").write_text("def mean(nums):\n    return 0\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        tool_edit(ws, "x.py", "def mean(num):\n    return 0", "pass")
    assert "没找到" in str(exc.value)


def test_bash_returns_exit_code_and_stderr(ws: Workspace):
    result = tool_bash(ws, "python -c \"import sys; print('hi'); sys.exit(3)\"")
    assert result.ok  # 命令失败也是有效观察
    assert result.meta["exit_code"] == 3
    assert "hi" in result.text
    assert "不要原样重试" in result.text


def test_bash_timeout_is_killed(ws: Workspace):
    result = tool_bash(ws, "python -c \"import time; time.sleep(30)\"", timeout=1)
    assert result.meta["timed_out"] is True
    assert "[超时]" in result.text


def test_bash_truncates_huge_output(ws: Workspace):
    result = tool_bash(ws, "python -c \"print('x' * 20000)\"")
    assert "输出已截断" in result.text
    assert len(result.text) < 20000
