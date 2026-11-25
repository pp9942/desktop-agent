"""定制层第二块：三条 hook。

- 危险命令拦截（bash pre-hook）：rm -rf /、format、git push --force 一类，
  拒绝执行并给出替代方案；
- 敏感文件拒绝（write/edit pre-hook）：.env、密钥类文件不许写，
  提示改用环境变量；
- 写后自动测（post-hook）：write/edit 成功后自动跑一遍 unittest，
  把结果作为附注回给模型 —— 它不用想起来跑，测试自己会跑。

验收口径（作业及格线）：拦截 >=2 次且每次带替代方案；AGENT_HOOKS=0 全部失效。
每次拦截记入 report()，供 app.inspect 出具 custom_report.json。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config

# 危险命令：宁可误杀一次（给了替代方案），不可放过一次
DANGEROUS_PATTERNS: list[tuple[str, str, str]] = [
    (r"rm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\S*\s+(/|~|\*)", "递归强删根目录/家目录",
     "删除单个文件用 rm <路径>，先 ls 确认目标存在"),
    (r"del\s+/[sqf]", "Windows 递归强删", "删除单个文件用 del <路径>；删目录前先 dir 确认内容"),
    (r"rd\s+/s", "Windows 递归删目录", "先列出目录内容确认，再逐个删除需要的文件"),
    (r"Remove-Item\s+[^|]*-Recurse[^|]*-Force", "PowerShell 递归强删",
     "先 Get-ChildItem 确认范围，删除时保留 -WhatIf 预演"),
    (r"format\s+[a-zA-Z]:", "格式化磁盘", "没有正当场景，需要清理空间请用具体文件的删除"),
    (r"git\s+push\s+\S+\s+\S+\s+--force", "强推远程分支",
     "先 git pull --rebase 合并本地提交，确认无冲突后再普通 push"),
    (r"git\s+reset\s+--hard", "丢弃全部未提交修改",
     "先用 git stash 保存现场（可恢复），确认不要了再 git stash drop"),
]

SENSITIVE_SUFFIXES = {".env", ".pem", ".key", ".p12", ".pfx"}
SENSITIVE_WORDS = ("secret", "credential", "password", "apikey", "api_key", "token")


@dataclass
class Verdict:
    allowed: bool
    reason: str = ""
    alternative: str = ""


@dataclass
class HookManager:
    cfg: Config
    enabled: bool = True
    intercepted: list[dict] = field(default_factory=list)
    autotests: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if os.environ.get("AGENT_HOOKS", "1").lower() in {"0", "false", "no"}:
            self.enabled = False

    # ---- pre-hook：工具执行前 ----
    def pre_execute(self, tool_name: str, args: dict) -> Verdict:
        if not self.enabled:
            return Verdict(True)
        if tool_name == "bash":
            return self._guard_bash(str(args.get("cmd", "")))
        if tool_name in {"write", "edit"}:
            return self._guard_write(str(args.get("path", "")))
        return Verdict(True)

    def _guard_bash(self, cmd: str) -> Verdict:
        low = cmd.lower()
        for pattern, why, alt in DANGEROUS_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE) or re.search(pattern, low, re.IGNORECASE):
                self._record("bash", cmd, why, alt)
                return Verdict(False, f"hook 拦截：{why}", alt)
        return Verdict(True)

    def _guard_write(self, path: str) -> Verdict:
        name = Path(path).name.lower()
        if name in {".env", ".env.local", ".env.production"} or name.endswith(tuple(SENSITIVE_SUFFIXES)):
            why = "目标文件是密钥/证书类文件"
            alt = "密钥请放环境变量或 ~/.desktop-agent/.env（本机专用，不入仓库不入工作区）"
            self._record("write/edit", path, why, alt)
            return Verdict(False, f"hook 拦截：{why}", alt)
        if any(w in name.replace("_", "").replace("-", "") for w in SENSITIVE_WORDS):
            why = "文件名含敏感词（secret/credential/password/token/apikey）"
            alt = "如果确实要存配置，改用不含敏感词的名字，并确保里面没有真实密钥"
            self._record("write/edit", path, why, alt)
            return Verdict(False, f"hook 拦截：{why}", alt)
        return Verdict(True)

    # ---- post-hook：写操作成功后自动测 ----
    def post_execute(self, tool_name: str, args: dict, ok: bool) -> str | None:
        """返回附注文本（会拼进工具结果回给模型），无附注返回 None。"""
        if not self.enabled or tool_name not in {"write", "edit"} or not ok:
            return None
        has_tests = any((self.cfg.workspace / n).is_file()
                        for n in ("test_stats.py", "test_*.py"))
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", "-q"],
                cwd=self.cfg.workspace, capture_output=True, text=True, timeout=60,
                encoding="utf-8", errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            note = f"自动测试未能运行：{exc}"
            self.autotests.append({"ok": False, "note": note})
            return f"[自动测试 hook] {note}（请手动确认改动没有破坏测试）"
        passed = proc.returncode == 0
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = " | ".join(tail[-3:]) if tail else "无输出"
        note = f"自动测试{'通过' if passed else '失败'}：{tail}"
        self.autotests.append({"ok": passed, "note": note, "has_tests": has_tests})
        return f"[自动测试 hook] {note}" + ("" if passed else "（请在继续之前修复测试）")

    def _record(self, tool: str, target: str, why: str, alt: str) -> None:
        self.intercepted.append({"tool": tool, "target": target[:120], "why": why, "alternative": alt})

    # ---- 验收报告 ----
    def report(self) -> dict:
        return {
            "hooks_enabled": self.enabled,
            "intercepted_count": len(self.intercepted),
            "interceptions": self.intercepted,
            "autotest_runs": len(self.autotests),
            "autotest_failed": sum(1 for a in self.autotests if not a.get("ok")),
        }