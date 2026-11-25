"""增量 B 第一块：AGENTS.md 双层注入——加载、预算、开关。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import user_dir
from agent.custom import GLOBAL_BUDGET, PROJECT_BUDGET, load_agents_md


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    """隔离的数据目录 + 工作区，不碰真实用户目录。"""
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    from agent.config import Config

    c = Config.load(str(tmp_path / "ws"))
    c.ensure_workspace()
    return c


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_both_layers_injected(cfg):
    _write(user_dir() / "AGENTS.md", "全局偏好：回答用中文。")
    _write(cfg.workspace / "AGENTS.md", "项目规矩：跑测试用 python -m unittest -q。")
    r = load_agents_md(cfg)
    text = r.inject_text()
    assert text and "全局偏好" in text and "项目规矩" in text
    assert r.total_tokens <= 500  # 及格线：主上下文增量 <=500 token


def test_project_only_when_no_global(cfg):
    _write(cfg.workspace / "AGENTS.md", "只有项目层。")
    r = load_agents_md(cfg)
    assert r.global_path is None and r.project_path is not None
    assert "只有项目层" in r.inject_text()


def test_no_files_zero_injection(cfg):
    assert load_agents_md(cfg).inject_text() is None


def test_switch_off_means_no_injection(cfg, monkeypatch):
    _write(cfg.workspace / "AGENTS.md", "不该被注入。")
    monkeypatch.setenv("AGENT_CUSTOM", "0")
    assert load_agents_md(cfg).inject_text() is None


def test_budget_truncates_long_file(cfg):
    _write(cfg.workspace / "AGENTS.md", "很长的约定行。\n" * 200)
    r = load_agents_md(cfg)
    assert r.project_truncated is True
    assert r.total_tokens <= 500
    assert "已截断" in r.inject_text()
    assert PROJECT_BUDGET == 300 and GLOBAL_BUDGET == 200