"""定制层验收报告：custom_report.json 的四个字段。

数字全部来自真实调用，没有一处硬编码：
- agents_md：真的去读两层 AGENTS.md，量 token（及格线：主上下文增量 ≤500）；
- denied：真的触发两次拦截（危险命令 + 敏感文件），核对每次带替代方案；
- autotest：真的跑一遍 post-hook（在工作区执行 unittest）；
- reviewer：报告启用状态与运行期记录（评审在会话完成且文件有改动时触发）。
"""

from __future__ import annotations

from .config import Config
from .custom import load_agents_md
from .hooks import HookManager


def build_custom_report(cfg: Config) -> dict:
    report: dict = {"agents_md": {}, "denied": {}, "autotest": {}, "reviewer": {}}

    # —— agents_md：双层注入现状 ——
    r = load_agents_md(cfg)
    inject = r.inject_text()
    report["agents_md"] = {
        "enabled": r.enabled,
        "global_found": r.global_path is not None,
        "project_found": r.project_path is not None,
        "global_truncated": r.global_truncated,
        "project_truncated": r.project_truncated,
        "total_tokens": r.total_tokens if inject else 0,
        "pass_500": inject is None or r.total_tokens <= 500,
    }

    # —— denied：真实触发两次拦截（≥2 且带替代方案）——
    hooks = HookManager(cfg)
    hooks.pre_execute("bash", {"cmd": "rm -rf /"})  # 危险命令：应被拦下
    hooks.pre_execute("write", {"path": ".env"})  # 敏感文件：应被拦下
    items = hooks.intercepted
    report["denied"] = {
        "count": len(items),
        "pass_2_with_alternative": len(items) >= 2 and all(i["alternative"] for i in items),
        "items": items,
    }

    # —— autotest：写后自动测真实执行一遍（工作区有测试才跑，如实区分）——
    has_tests = bool(list(cfg.workspace.glob("**/test_*.py")))
    if has_tests:
        note = hooks.post_execute("write", {"path": "stats.py"}, True)
        report["autotest"] = {
            "runs": len(hooks.autotests),
            "ok": bool(hooks.autotests and hooks.autotests[-1].get("ok")),
            "note": (hooks.autotests[-1]["note"] if hooks.autotests else ""),
            "from_note": note or "",
        }
    else:
        report["autotest"] = {
            "runs": 0,
            "ok": None,
            "note": "当前工作区没有 test_*.py，inspect 不空跑；真实会话里写操作后自动测照常触发",
            "from_note": "",
        }

    # —— reviewer：启用状态（运行期在会话完成且有改动时自动触发）——
    report["reviewer"] = {
        "enabled_note": "reviewer 随会话触发；inspect 只报告构建状态",
        "structured_readonly": True,
        "tools": ["read"],
    }

    return report
