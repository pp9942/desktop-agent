"""增量 A：技能系统——开关、目录注入、disclosure_ratio、触发试验。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent.skills import SkillManager, get_manager

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"


@pytest.fixture(scope="module")
def manager() -> SkillManager:
    return SkillManager(SKILLS, enabled=True)


def test_three_skills_one_of_each_kind(manager: SkillManager):
    """≥3 个技能，且纯提示词 / 带脚本 / 走 MCP 各一。"""
    kinds = {s.name: s.kind for s in manager.skills}
    assert len(manager.skills) >= 3
    assert kinds.get("commit-message") == "prompt"
    assert kinds.get("test-report") == "script"
    assert kinds.get("notes") == "mcp"
    # 带脚本的技能真的有脚本
    assert (SKILLS / "test-report" / "scripts" / "collect.py").is_file()


def test_catalog_injects_and_body_stays_out(manager: SkillManager):
    """目录里有名字和正文路径，但不把正文塞进 system prompt。"""
    catalog = manager.catalog_text()
    assert catalog and "commit-message" in catalog and "test-report" in catalog
    for skill in manager.skills:
        assert str(skill.path) in catalog
        assert skill.body[:30] not in catalog  # 正文不进目录


def test_disabled_means_zero_injection():
    """ENABLE_SKILLS=0：目录返回 None，行为退回 v1.0。"""
    mgr = SkillManager(SKILLS, enabled=False)
    assert mgr.catalog_text() is None


def test_disclosure_ratio_at_least_5(manager: SkillManager):
    """及格线：全量正文 token / 目录 token ≥ 5。"""
    r = manager.report()
    assert r["catalog_tokens"] > 0
    assert r["disclosure_ratio"] >= 5, (
        f"disclosure_ratio={r['disclosure_ratio']}（目录 {r['catalog_tokens']} / 全量 {r['full_tokens']}）"
    )


def test_six_trials_zero_false_trigger(manager: SkillManager):
    """6 条触发试验全过：正例命中对应技能，负例零误触发。"""
    r = manager.report()
    assert r["trials_total"] == 6
    assert r["trials_passed"] == 6, json.dumps(r["trials"], ensure_ascii=False, indent=2)
    assert r["zero_false_trigger"]


def test_collect_script_runs_and_outputs_json(tmp_path: Path):
    """带脚本技能的 collect.py 真的能跑，输出合法 JSON。"""
    (tmp_path / "test_demo.py").write_text(
        "def test_ok():\n    assert 1 == 1\n", encoding="utf-8"
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(SKILLS / "test-report" / "scripts" / "collect.py"),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["tests"] == 1 and data["status"] == "ok"


def test_manager_cached_per_config():
    """get_manager 按 (root, 开关) 缓存：同一配置拿到同一个实例。"""
    a = get_manager(SKILLS, True)
    b = get_manager(SKILLS, True)
    c = get_manager(SKILLS, False)
    assert a is b and a is not c
