"""四个工具：read / write / edit / bash。

设计原则（对应第 3 讲「工具设计的四条硬规矩」）：
1. description 写给模型看 —— 说清什么时候该用、什么时候别用；
2. 错误信息是模型的下一步线索 —— 不抛 Traceback，返回可操作的中文提示；
3. 粒度尽量粗 —— 四个工具覆盖全部场景，不再加专用工具；
4. 数量少 —— 工具越少，选错的概率越低。
"""

from __future__ import annotations

import base64
import difflib
import mimetypes
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_FILE_BYTES = 2 * 1024 * 1024  # 单次读取上限，超过就提示用 bash 处理


@dataclass
class ToolResult:
    ok: bool
    text: str
    diff: str = ""
    images: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


class ToolError(Exception):
    """工具的可预期失败。信息会被原样交给模型，所以要写人话。"""


# --------------------------------------------------------------------------- #
# 工作区：所有路径都被钉死在这个目录里
# --------------------------------------------------------------------------- #
class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def resolve(self, raw: str) -> Path:
        if raw is None or str(raw).strip() == "":
            raise ToolError("path 不能为空。请给出相对于工作区的路径，例如 src/app.py")
        candidate = Path(str(raw).strip())
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise ToolError(f"路径无法解析：{raw}（{exc}）") from exc
        if resolved != self.root and self.root not in resolved.parents:
            raise ToolError(
                f"拒绝访问工作区之外的路径：{raw}\n"
                f"工作区是 {self.root}。请改用这个目录内的路径。"
            )
        return resolved

    def relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)


# --------------------------------------------------------------------------- #
# read
# --------------------------------------------------------------------------- #
def _numbered(lines: list[str], start: int, width: int) -> str:
    return "\n".join(f"{start + i:>{width}}| {line}" for i, line in enumerate(lines))


def tool_read(ws: Workspace, path: str, offset: int = 1, limit: int = 200) -> ToolResult:
    target = ws.resolve(path)
    if not target.exists():
        raise ToolError(
            f"文件不存在：{path}\n"
            f"当前目录有：{_siblings(target.parent)}。用 bash 的 ls 可以看到完整列表。"
        )
    if target.is_dir():
        raise ToolError(f"{path} 是目录，不是文件。要列目录请用 bash：ls -la {path}")

    size = target.stat().st_size
    suffix = target.suffix.lower()

    if suffix in IMAGE_SUFFIXES:
        if size > 8 * 1024 * 1024:
            raise ToolError(f"图片太大（{size / 1048576:.1f} MB），上限 8 MB。")
        mime = mimetypes.guess_type(target.name)[0] or "image/png"
        data = base64.b64encode(target.read_bytes()).decode()
        return ToolResult(
            ok=True,
            text=f"[图片] {ws.relative(target)} · {mime} · {size / 1024:.0f} KB，已作为图像内容附上。",
            images=[f"data:{mime};base64,{data}"],
            meta={"path": ws.relative(target), "kind": "image"},
        )

    if size > MAX_FILE_BYTES:
        raise ToolError(
            f"文件太大（{size / 1048576:.1f} MB），不能整读。\n"
            f"用 bash 精确取用，例如：sed -n '1,120p' {path} 或 rg 'keyword' {path}"
        )

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ToolError(f"读取失败：{path}（{exc}）") from exc

    all_lines = text.splitlines()
    total = len(all_lines)
    offset = max(1, int(offset or 1))
    limit = max(1, int(limit or 200))
    if total == 0:
        return ToolResult(ok=True, text=f"{ws.relative(target)} 是空文件（0 行）。", meta={"path": ws.relative(target), "total": 0})

    if offset > total:
        raise ToolError(
            f"offset={offset} 超出范围：{path} 一共 {total} 行。"
            f"想要末尾几行就用 offset={max(1, total - 40)}。"
        )

    end = min(total, offset - 1 + limit)
    chunk = all_lines[offset - 1 : end]
    width = len(str(end))
    body = _numbered(chunk, offset, width)

    remaining = total - end
    header = f"{ws.relative(target)} · 共 {total} 行 · 显示 {offset}-{end}"
    if remaining > 0:
        body += (
            f"\n…（还有 {remaining} 行未显示，需要就继续读 offset={end + 1}，不要一次读整个大文件）"
        )
    return ToolResult(
        ok=True,
        text=f"{header}\n{body}",
        meta={"path": ws.relative(target), "total": total, "offset": offset, "end": end, "remaining": remaining},
    )


def _siblings(directory: Path) -> str:
    try:
        names = sorted(p.name + ("/" if p.is_dir() else "") for p in list(directory.iterdir())[:12])
        return "、".join(names) or "（空目录）"
    except OSError:
        return "（读不到）"


# --------------------------------------------------------------------------- #
# write
# --------------------------------------------------------------------------- #
def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(content, encoding="utf-8", newline="")
        os.replace(tmp, target)  # 同一个目录内 rename 是原子的
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def tool_write(ws: Workspace, path: str, content: str) -> ToolResult:
    target = ws.resolve(path)
    if target.is_dir():
        raise ToolError(f"{path} 是目录，不能当文件写。")
    existed = target.exists()
    old_text = target.read_text(encoding="utf-8", errors="replace") if existed else ""
    old_lines = len(old_text.splitlines())
    _atomic_write(target, content)
    new_lines = len(content.splitlines())
    diff = ""
    if existed and old_text != content:
        diff = _unified(ws.relative(target), old_text, content)
    delta = new_lines - old_lines
    verb = "新建" if not existed else "覆盖"
    sign = f"+{delta}" if delta >= 0 else str(delta)
    return ToolResult(
        ok=True,
        text=f"已{verb} {ws.relative(target)}（{old_lines} 行 → {new_lines} 行，{sign}）",
        diff=diff,
        meta={"path": ws.relative(target), "lines": new_lines, "created": not existed},
    )


# --------------------------------------------------------------------------- #
# edit
# --------------------------------------------------------------------------- #
def _unified(name: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            n=3,
        )
    )


def tool_edit(ws: Workspace, path: str, old: str, new: str) -> ToolResult:
    target = ws.resolve(path)
    if not target.exists():
        raise ToolError(f"文件不存在：{path}。要新建文件请用 write，edit 只改已存在的文件。")
    if not old:
        raise ToolError("old 不能为空。edit 需要一段能在文件里唯一定位的原文。")

    before = target.read_text(encoding="utf-8", errors="replace")
    count = before.count(old)

    if count == 0:
        hint = _closest_hint(before, old)
        raise ToolError(
            f"在 {path} 里没找到这段原文，一个字符都不能差（包括缩进）。\n{hint}"
        )

    if count > 1:
        lines = before.splitlines()
        first = old.splitlines()[0] if old.splitlines() else old
        hits = [i + 1 for i, line in enumerate(lines) if first and first in line][:6]
        raise ToolError(
            f"这段原文在 {path} 里出现了 {count} 次，无法确定改哪一处。\n"
            f"命中的行：{hits}。请把 old 补上前后几行上下文，让它在文件里唯一。"
        )

    after = before.replace(old, new, 1)
    _atomic_write(target, after)
    diff = _unified(ws.relative(target), before, after)
    changed = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    return ToolResult(
        ok=True,
        text=f"已修改 {ws.relative(target)}（+{changed} -{removed} 行，唯一匹配）",
        diff=diff,
        meta={"path": ws.relative(target), "added": changed, "removed": removed},
    )


def _closest_hint(content: str, old: str) -> str:
    """old 匹配不上时，给出最接近的一行 —— 让模型知道差在哪。"""
    target_line = old.splitlines()[0].strip()
    if not target_line:
        return "提示：old 的第一行是空行，请从有内容的行开始取。"
    lines = content.splitlines()
    best = difflib.get_close_matches(target_line, [l.strip() for l in lines], n=3, cutoff=0.4)
    if not best:
        return "提示：先用 read 重新看一遍这段代码，再复制原文（缩进、空行都要一致）。"
    parts = []
    for match in best:
        for i, line in enumerate(lines, 1):
            if line.strip() == match:
                parts.append(f"  第 {i} 行：{line.strip()}")
                break
    return "最接近的是：\n" + "\n".join(parts)


# --------------------------------------------------------------------------- #
# bash
# --------------------------------------------------------------------------- #
def _decode(raw: bytes) -> str:
    """Windows 上 cmd 的输出常常是 GBK，而 Python 子进程默认 UTF-8。

    先按 UTF-8 试，失败再退到 GBK —— 不猜，按能否无损解码决定。
    """
    if not raw:
        return ""
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = int(limit * 0.7)
    tail = limit - head
    return f"{text[:head]}\n…（中间省略 {len(text) - limit} 字符）…\n{text[-tail:]}", True


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
            )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def tool_bash(ws: Workspace, command: str, timeout: int = 60) -> ToolResult:
    if not command or not command.strip():
        raise ToolError("command 不能为空。")

    timeout = max(1, min(int(timeout or 60), 600))
    shell = os.environ.get("COMSPEC", "cmd.exe") if sys.platform == "win32" else "/bin/sh"
    # 让 `python` 指向当前解释器，而不是机器上那个碰巧在 PATH 里的老版本
    env = os.environ.copy()
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    started = time.time()
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=str(ws.root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        executable=shell,
        env=env,
    )
    timed_out = False
    try:
        out_b, err_b = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            out_b, err_b = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out_b, err_b = b"", b""
    out = _decode(out_b)
    err = _decode(err_b)

    duration = time.time() - started
    out, cut_out = _truncate(out or "", 8000)
    err, cut_err = _truncate(err or "", 4000)
    code = proc.returncode

    parts = [f"$ {command}"]
    if out.strip():
        parts.append(out.rstrip())
    if err.strip():
        parts.append(f"[stderr]\n{err.rstrip()}")

    if timed_out:
        parts.append(
            f"[超时] 超过 {timeout}s 被强制终止（退出码 {code}）。\n"
            f"换个更快的方式，或把命令拆小：加 --timeout、只跑单个测试、缩小搜索范围。"
        )
    elif code != 0:
        parts.append(f"[退出码 {code}] 命令失败。先读上面的 stderr，再决定是改代码还是改命令，不要原样重试。")
    if cut_out or cut_err:
        parts.append("[输出已截断] 只保留了首尾，用更精确的命令缩小范围。")

    return ToolResult(
        ok=True,  # 命令失败也是有效观察，不当成工具异常
        text="\n".join(parts),
        meta={
            "command": command,
            "exit_code": code,
            "duration": round(duration, 2),
            "failed": timed_out or code != 0,
            "timed_out": timed_out,
        },
    )


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., ToolResult]

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def build_tools(ws: Workspace) -> dict[str, Tool]:
    tools = [
        Tool(
            name="read",
            description=(
                "读文件内容，返回带行号的文本；读图片时返回图像本身。\n"
                "什么时候用：改代码之前必须先读，不要凭记忆猜。\n"
                "什么时候别用：列目录、搜关键字、看 git 状态，都用 bash。\n"
                "大文件必须分页：先读 200 行，需要再往后翻，不要一次读完。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区的路径，如 src/app.py"},
                    "offset": {"type": "integer", "description": "起始行号，从 1 开始，默认 1"},
                    "limit": {"type": "integer", "description": "最多读多少行，默认 200"},
                },
                "required": ["path"],
            },
            func=lambda path, offset=1, limit=200: tool_read(ws, path, offset, limit),
        ),
        Tool(
            name="write",
            description=(
                "新建文件，或用新内容整体覆盖已有文件。\n"
                "什么时候用：文件不存在，或某个文件要整体重写。\n"
                "什么时候别用：只改几行就用 edit —— 覆盖会让没看过的内容一起消失。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区的路径"},
                    "content": {"type": "string", "description": "完整的新文件内容"},
                },
                "required": ["path", "content"],
            },
            func=lambda path, content: tool_write(ws, path, content),
        ),
        Tool(
            name="edit",
            description=(
                "把文件里一段唯一的原文替换成新内容，返回 diff。\n"
                "什么时候用：改几行、加一个函数、修一个 bug —— 首选它，而不是 write。\n"
                "原文必须逐字符匹配（含缩进）。如果这段原文出现 2 次以上，工具会报错，"
                "此时要补上前后几行上下文，让它唯一；不要改用一个更短的片段去猜。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区的路径"},
                    "old": {"type": "string", "description": "要被替换的原文，必须与文件内容完全一致"},
                    "new": {"type": "string", "description": "替换后的新内容"},
                },
                "required": ["path", "old", "new"],
            },
            func=lambda path, old, new: tool_edit(ws, path, old, new),
        ),
        Tool(
            name="bash",
            description=(
                "在工作区执行一条 shell 命令，返回 stdout、stderr 和退出码。\n"
                "什么时候用：列目录、搜索（findstr/rg）、跑测试、git、装依赖、运行脚本 —— 所有非文本编辑的事。\n"
                "注意：必须给超时；输出可能很长，用 --quiet、-n 限制；长时间运行的命令要拆开。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令，如 python -m pytest -q"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认 60，上限 600"},
                },
                "required": ["command"],
            },
            func=lambda command, timeout=60: tool_bash(ws, command, timeout),
        ),
    ]
    return {tool.name: tool for tool in tools}
