"""技能系统：把你重复过的流程写成文件，目录常驻、正文按需读取。

增量 A 的设计约束：
- 不新增工具、不重写循环 —— 技能正文靠模型用现成的 read 工具按需读取，
  所以技能目录被列入 read 的只读白名单（写操作仍然钉死在工作区）；
- ENABLE_SKILLS=0 时零注入，行为完全退回 v1.0；
- skill_report.json 的每个数字都由 `python app.py inspect` 打印，手写不算分。

disclosure_ratio 的含义：正文全部加载会花多少 token，除以只注入目录花的 token。
比例越大，说明「目录常驻、正文按需」省得越多 —— 及格线 5。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

FRONTMATTER_KEYS = {"name", "kind", "description", "triggers"}

# 触发试验：6 条 —— 3 条正例（应命中对应技能）、3 条负例（不应命中任何技能）。
# 「零误触发」指负例一条都不能命中。
TRIALS: tuple[tuple[str, str | None], ...] = (
    ("帮我把这次改动写一条提交信息", "commit-message"),
    ("生成这个工作区的测试报告", "test-report"),
    ("把这次修复的思路记到笔记里", "notes"),
    ("给 stats.py 加一个 stdev 函数并补测试", None),
    ("读一下 README.md，概括它在讲什么", None),
    ("修复 mean 对空列表的处理，然后跑一遍测试", None),
)


def estimate_tokens(text: str) -> int:
    """粗估 token 数：CJK 按每字 1 个，其余按每 4 字符 1 个（有余数进 1）。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    rest = len(text) - cjk
    return cjk + rest // 4 + (1 if rest % 4 else 0)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 SKILL.md 开头的 --- 块，返回 (元数据, 正文)。没有 frontmatter 就报错。"""
    if not text.startswith("---"):
        raise ValueError("SKILL.md 缺少 frontmatter（应以 --- 开头）")
    try:
        end = text.index("\n---", 3)
    except ValueError as exc:
        raise ValueError("frontmatter 没有结束的 ---") from exc
    meta: dict[str, str] = {}
    for raw in text[3:end].strip().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip() in FRONTMATTER_KEYS:
            meta[key.strip()] = value.strip()
    body = text[end + 4:].strip()
    return meta, body


@dataclass
class Skill:
    name: str
    kind: str  # prompt / script / mcp
    description: str
    triggers: tuple[str, ...]
    path: Path
    body: str

    @property
    def body_tokens(self) -> int:
        return estimate_tokens(self.body)


class SkillManager:
    """扫描技能目录，构建常驻注入的目录段。

    目录里只放「一行描述 + 正文绝对路径」，正文由模型用 read 按需读 ——
    这正是 disclosure_ratio 的分子分母之差所在。
    """

    def __init__(self, root: Path, enabled: bool = True):
        self.root = Path(root)
        self.enabled = enabled
        self.skills: list[Skill] = []
        self.load()

    def load(self) -> None:
        self.skills = []
        if not self.root.is_dir():
            return
        for md in sorted(self.root.glob("*/SKILL.md")):
            try:
                meta, body = parse_frontmatter(md.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # 坏技能文件跳过，不炸整个系统
            triggers = tuple(
                t.strip() for t in meta.get("triggers", "").split(",") if t.strip()
            )
            self.skills.append(
                Skill(
                    name=meta.get("name") or md.parent.name,
                    kind=meta.get("kind") or "prompt",
                    description=meta.get("description", ""),
                    triggers=triggers,
                    path=md.resolve(),
                    body=body,
                )
            )

    def catalog_text(self) -> str | None:
        """要注入 system prompt 的目录段；关闭或没有技能时返回 None（零注入）。"""
        if not self.enabled or not self.skills:
            return None
        lines = [
            "## 可用技能（按需加载）",
            "下面只列目录。接到匹配的任务时，先用 read 读取该技能的 SKILL.md 全文，按里面的流程做；",
            "不匹配的任务不要读，也不要凭目录里的半句话自由发挥。",
        ]
        for s in self.skills:
            lines.append(f"- {s.name} —— {s.description}（正文：{s.path}）")
        return "\n".join(lines)

    def match(self, query: str) -> list[str]:
        """触发匹配：query 含某技能的任一触发词即算命中。"""
        return [s.name for s in self.skills if any(t and t in query for t in s.triggers)]

    def report(self) -> dict:
        catalog = self.catalog_text() or ""
        full_tokens = sum(s.body_tokens for s in self.skills)
        catalog_tokens = estimate_tokens(catalog) if catalog else 0
        trials = []
        for query, expected in TRIALS:
            hit = (self.match(query) or [None])[0]
            trials.append(
                {"query": query, "expected": expected, "matched": hit, "pass": hit == expected}
            )
        ratio = round(full_tokens / catalog_tokens, 2) if catalog_tokens else 0.0
        return {
            "enabled": self.enabled,
            "skills_count": len(self.skills),
            "skills": [
                {
                    "name": s.name,
                    "kind": s.kind,
                    "description": s.description,
                    "triggers": list(s.triggers),
                    "body_tokens": s.body_tokens,
                }
                for s in self.skills
            ],
            "catalog_tokens": catalog_tokens,
            "full_tokens": full_tokens,
            "disclosure_ratio": ratio,
            "disclosure_ratio_pass": ratio >= 5,
            "trials": trials,
            "trials_passed": sum(1 for t in trials if t["pass"]),
            "trials_total": len(trials),
            "zero_false_trigger": all(t["pass"] for t in trials if t["expected"] is None),
        }


_cached: tuple[str, bool, SkillManager] | None = None


def get_manager(root: Path, enabled: bool | None = None) -> SkillManager:
    """按 (root, 开关) 缓存的单例 —— 循环每一步都问目录，不要每次重扫盘。"""
    global _cached
    if enabled is None:
        enabled = os.environ.get("ENABLE_SKILLS", "1").lower() not in {"0", "false", "no"}
    if _cached is None or _cached[0] != str(root) or _cached[1] != enabled:
        _cached = (str(root), enabled, SkillManager(root, enabled))
    return _cached[2]
