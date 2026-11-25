"""增量 B 第二块：hooks——拦截、替代方案、写后自动测、开关。"""

from __future__ import annotations

import pytest

from agent.hooks import HookManager


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    from agent.config import Config

    c = Config.load(str(tmp_path / "ws"))
    c.ensure_workspace()
    return c


def test_dangerous_bash_blocked_with_alternative(cfg):
    h = HookManager(cfg)
    v = h.pre_execute("bash", {"cmd": "rm -rf /"})
    assert v.allowed is False
    assert "拦截" in v.reason
    assert v.alternative  # 及格线：拦截必须带替代方案
    assert "stash" in h.pre_execute("bash", {"cmd": "git reset --hard"}).alternative
    assert h.report()["intercepted_count"] == 2  # 两次拦截都记入报告


def test_safe_bash_passes(cfg):
    h = HookManager(cfg)
    assert h.pre_execute("bash", {"cmd": "python -m unittest -q"}).allowed is True
    assert h.pre_execute("bash", {"cmd": "git status"}).allowed is True


def test_sensitive_file_blocked(cfg):
    h = HookManager(cfg)
    for path in (".env", "server.pem", "my_secret.txt", "api_key.json"):
        v = h.pre_execute("write", {"path": path})
        assert v.allowed is False, path
        assert v.alternative
    assert h.pre_execute("write", {"path": "stats.py"}).allowed is True


def test_switch_off_disables_everything(cfg, monkeypatch):
    monkeypatch.setenv("AGENT_HOOKS", "0")
    h = HookManager(cfg)
    assert h.enabled is False
    assert h.pre_execute("bash", {"cmd": "rm -rf /"}).allowed is True
    assert h.pre_execute("write", {"path": ".env"}).allowed is True
    assert h.post_execute("write", {"path": "a.py"}, True) is None
    assert h.report()["intercepted_count"] == 0


def test_post_hook_runs_tests_after_write(cfg):
    h = HookManager(cfg)
    note = h.post_execute("write", {"path": "stats.py"}, True)
    assert note and "自动测试" in note
    assert h.report()["autotest_runs"] == 1
    assert h.post_execute("bash", {"cmd": "x"}, True) is None  # 非写工具不触发
    assert h.post_execute("write", {"path": "a.py"}, False) is None  # 失败不触发