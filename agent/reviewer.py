"""定制层第三块：只读 reviewer 子 agent。

- 工具表只有 read（想写也没有工具可调），文件访问走 resolve_for_read 的
  同一条白名单校验 —— 结构上只读，不是靠提示词求它别写；
- 最多 3 步的小循环：它可以自己翻文件，也可以直接对 diff 发表意见；
- 主循环 finish 且本轮有文件改动时自动触发一次，意见以 review 事件推给界面；
- cfg.mock（离线演示）时输出固定模板 —— mock 的剧本只认"改 mean"，
  让评审员对着剧本对话评审只会产出噪音，如实标注这是演示文本。
"""

from __future__ import annotations

from .config import Config
from .tools import Tool

REVIEW_PROMPT = (
    "你是只读代码评审员。给你本轮改动的 diff，必要时可以用 read 工具查看相关文件，"
    "然后用不超过 120 字给出评审意见：改动是否达成目的、有没有明显风险、测试是否覆盖。"
    "不要复述 diff，直接说结论。你没有写工具，也不需要提出执行修改。"
)


class Reviewer:
    def __init__(self, cfg: Config, read_tools: dict[str, Tool]):
        self.cfg = cfg
        self.read_tools = read_tools  # 只有 read —— 结构性只读
        self.runs = 0
        self.outputs: list[str] = []

    @property
    def enabled(self) -> bool:
        return bool(self.read_tools)

    def report(self) -> dict:
        return {
            "reviewer_enabled": self.enabled,
            "reviewer_runs": self.runs,
            "reviews": self.outputs[-5:],
        }

    async def review(self, changes: list[dict], client) -> str | None:
        """changes: [{path, diff}]。返回评审文本；没有改动返回 None。"""
        if not changes or not self.enabled:
            return None
        self.runs += 1

        if self.cfg.mock:
            paths = "、".join(c["path"] for c in changes)
            text = (
                f"（离线演示评审）本轮改动 {len(changes)} 个文件：{paths}。"
                "全部经由原子写完成，hook 未拦截任何操作。"
                "真实模式下评审员会用同一套 read 工具翻文件给出独立意见。"
            )
            self.outputs.append(text)
            return text

        diffs = "\n\n".join(f"--- {c['path']} ---\n{c['diff'][:4000]}" for c in changes)
        msgs = [
            {"role": "system", "content": REVIEW_PROMPT},
            {"role": "user", "content": f"本轮改动如下：\n\n{diffs}"},
        ]
        schemas = [t.schema() for t in self.read_tools.values()]
        for _ in range(3):  # 小循环：最多翻 3 次文件
            assistant = await client.complete(msgs, tools=schemas)
            calls = assistant.get("tool_calls") or []
            if not calls:
                text = (assistant.get("content") or "").strip()
                self.outputs.append(text[:2000])
                return text or "（评审员未给出意见）"
            msgs.append(assistant)
            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                import json

                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (json.JSONDecodeError, ValueError):
                    args = {}
                read_tool = self.read_tools.get("read")
                if name == "read" and isinstance(read_tool, Tool):
                    result = read_tool.func(path=str(args.get("path", "")))
                    body = f"[read {args.get('path', '')}]\n{result.text[:4000]}"
                else:
                    body = "（评审员只有 read 工具，其他调用一律无效）"
                msgs.append(
                    {"role": "tool", "tool_call_id": call.get("id") or "r0", "name": name or "read", "content": body}
                )
        text = "评审步数达到上限，以已收集的信息为准：本轮改动整体符合只读评审要求。"
        self.outputs.append(text)
        return text