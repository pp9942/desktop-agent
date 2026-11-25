"""定制层第一块：AGENTS.md 双层注入。

两层约定：
- 全局层 ~/.desktop-agent/AGENTS.md（或 AGENT_DATA_DIR 下）——个人偏好，跨项目生效；
- 项目层 <workspace>/AGENTS.md——这个仓库的规矩。

约束（作业及格线）：
- 主上下文增量 <=500 token：全局层最多 200，项目层最多 300，超预算按整行截断；
- AGENT_CUSTOM=0 时零注入，行为退回 v1.1。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .config import Config, user_dir
from .skills import estimate_tokens

GLOBAL_BUDGET = 200  # token
PROJECT_BUDGET = 300
TOTAL_BUDGET = GLOBAL_BUDGET + PROJECT_BUDGET

HEADER = "## 本仓库的工作约定（AGENTS.md，两层：全局 + 项目）"


def _fit_budget(text: str, budget: int) -> tuple[str, bool]:
    """按整行截断到预算内。返回 (截断后的文本, 是否发生了截断)。"""
    if estimate_tokens(text) <= budget:
        return text, False
    lines: list[str] = []
    used = estimate_tokens(HEADER) + 2
    for line in text.splitlines():
        cost = estimate_tokens(line + "\n")
        if used + cost > budget - 1:
            break
        lines.append(line)
        used += cost
    lines.append("（已截断：AGENTS.md 超出 token 预算，请精简）")
    return "\n".join(lines), True


@dataclass
class AgentsMdResult:
    enabled: bool
    global_path: Path | None
    project_path: Path | None
    global_text: str
    project_text: str
    global_truncated: bool
    project_truncated: bool

    @property
    def total_tokens(self) -> int:
        return estimate_tokens(self.inject_text() or "")

    def inject_text(self):
        """拼进 system prompt 的文本；关闭或两层都不存在时 None（零注入）。"""
        if not self.enabled:
            return None
        parts = []
        if self.global_text:
            parts.append("### 全局（个人偏好）\n" + self.global_text)
        if self.project_text:
            parts.append("### 项目（本仓库规矩，优先级更高）\n" + self.project_text)
        if not parts:
            return None
        return HEADER + "\n" + "\n\n".join(parts)


def load_agents_md(cfg: Config, enabled=None):
    """读两层 AGENTS.md。文件缺失返回空文本，不报错。"""
    if enabled is None:
        enabled = os.environ.get("AGENT_CUSTOM", "1").lower() not in {"0", "false", "no"}

    result = AgentsMdResult(enabled, None, None, "", "", False, False)
    if not enabled:
        return result

    gpath = user_dir() / "AGENTS.md"
    if gpath.is_file():
        try:
            text, trunc = _fit_budget(gpath.read_text(encoding="utf-8"), GLOBAL_BUDGET)
            result.global_path = gpath
            result.global_text = text
            result.global_truncated = trunc
        except OSError:
            pass

    ppath = cfg.workspace / "AGENTS.md"
    if ppath.is_file():
        try:
            text, trunc = _fit_budget(ppath.read_text(encoding="utf-8"), PROJECT_BUDGET)
            result.project_path = ppath
            result.project_text = text
            result.project_truncated = trunc
        except OSError:
            pass

    return result