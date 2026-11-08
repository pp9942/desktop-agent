"""运行配置。

所有可调参数集中在这里，代码里不再出现魔法数字。
配置优先级：环境变量 > .env > 默认值。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "desktop-agent"


def resource_dir() -> Path:
    """只读资源目录（打包后是 PyInstaller 的解包目录）。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def user_dir() -> Path:
    """可写目录。打包后不能往 exe 同级写（可能只读）。"""
    path = Path.home() / f".{APP_NAME}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_dotenv(path: Path) -> None:
    """极简 .env 解析，避免为一行配置引入 python-dotenv。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_user_settings() -> dict:
    """界面上填过的配置，存在用户目录，不进仓库。"""
    path = user_dir() / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


@dataclass
class Config:
    # —— 模型 ——
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    temperature: float = 0.2
    mock: bool = False

    # —— 工作区 ——
    workspace: Path = field(default_factory=lambda: resource_dir() / "workspace")

    # —— 护栏：步数 / 单步超时 / bash 超时（作业要求三道护栏）——
    max_steps: int = 20
    step_timeout: float = 180.0
    bash_timeout: int = 60
    llm_retries: int = 3

    # —— 输出与上下文 ——
    max_output_chars: int = 12_000
    context_budget_chars: int = 60_000
    keep_recent_messages: int = 8

    @classmethod
    def load(cls, workspace: str | None = None) -> "Config":
        # 用户目录优先：打包后的 _MEIPASS 是只读的
        load_dotenv(user_dir() / ".env")
        load_dotenv(resource_dir() / ".env")
        saved = load_user_settings()
        # 打包后 _MEIPASS 是临时解包目录，工作区必须放在用户目录
        default_ws = user_dir() / "workspace" if getattr(sys, "frozen", False) else resource_dir() / "workspace"
        ws = workspace or os.environ.get("AGENT_WORKSPACE") or str(default_ws)
        return cls(
            api_key=saved.get("api_key") or os.environ.get("OPENAI_API_KEY", ""),
            base_url=(saved.get("base_url") or os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1")).rstrip("/"),
            model=saved.get("model") or os.environ.get("AGENT_MODEL", "deepseek-chat"),
            temperature=float(os.environ.get("AGENT_TEMPERATURE", "0.2")),
            mock=os.environ.get("AGENT_MOCK", "").lower() in {"1", "true", "yes"},
            workspace=Path(ws).expanduser().resolve(),
            max_steps=int(os.environ.get("AGENT_MAX_STEPS", "20")),
            step_timeout=float(os.environ.get("AGENT_STEP_TIMEOUT", "180")),
            bash_timeout=int(os.environ.get("AGENT_BASH_TIMEOUT", "60")),
        )

    def ensure_workspace(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)

    def public(self) -> dict:
        """给前端看的安全信息 —— 永远不含 api_key。"""
        return {
            "model": self.model,
            "base_url": self.base_url,
            "mock": self.mock,
            "workspace": str(self.workspace),
            "max_steps": self.max_steps,
            "bash_timeout": self.bash_timeout,
            "has_key": bool(self.api_key),
        }

    def save_user(self, api_key: str | None = None) -> None:
        """把界面上填的配置写到用户目录（不写 key 以外的秘密，也不写进仓库）。"""
        if api_key:
            self.api_key = api_key.strip()
        payload = {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model": self.model,
        }
        path = user_dir() / "config.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
