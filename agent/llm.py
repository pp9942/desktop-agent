"""模型客户端：OpenAI 兼容的流式 chat/completions。

只依赖 httpx，不绑定任何一家 SDK —— 换 DeepSeek / 通义 / OpenAI
只改 .env 里的 BASE_URL 和 MODEL。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

import httpx

from .config import Config

DeltaHook = Callable[[str], Awaitable[None]]


class LLMError(Exception):
    pass


class _Retryable(LLMError):
    pass


class LLMClient:
    """真实模型。重试策略：429 / 5xx / 网络错误最多 3 次，指数退避。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.name = cfg.model

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_delta: DeltaHook | None = None,
    ) -> dict:
        emitted = False

        async def hook(text: str) -> None:
            nonlocal emitted
            emitted = True
            if on_delta:
                await on_delta(text)

        last: Exception | None = None
        for attempt in range(1, self.cfg.llm_retries + 1):
            try:
                return await self._once(messages, tools, hook)
            except _Retryable as exc:
                last = exc
                if emitted:  # 已经开始吐字，重试会让用户看到重复内容
                    raise LLMError(str(exc)) from exc
                if attempt == self.cfg.llm_retries:
                    break
                await asyncio.sleep(1.5 * attempt)
        raise LLMError(f"模型调用失败（已重试 {self.cfg.llm_retries} 次）：{last}")

    async def _once(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        on_delta: Callable[[str], Awaitable[None]],
    ) -> dict:
        if not self.cfg.api_key:
            raise LLMError(
                "还没有配置 API Key：点右上角「设置」填一个，"
                "或把 .env 放到用户目录（%USERPROFILE%\\.desktop-agent\\.env）；"
                "想先看效果就启动时加 --mock。"
            )

        payload: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        url = f"{self.cfg.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }

        text_parts: list[str] = []
        calls: dict[int, dict] = {}

        timeout = httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", "replace")[:500]
                        if resp.status_code in (408, 409, 429) or resp.status_code >= 500:
                            raise _Retryable(f"HTTP {resp.status_code}：{body}")
                        raise LLMError(f"HTTP {resp.status_code}：{body}")

                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if "error" in chunk:
                            raise LLMError(str(chunk["error"])[:500])
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            text_parts.append(piece)
                            await on_delta(piece)
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            slot = calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]
        except httpx.HTTPError as exc:
            raise _Retryable(f"网络错误：{exc}") from exc

        message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
        if calls:
            message["tool_calls"] = [
                {
                    "id": slot["id"] or f"call_{idx}",
                    "type": "function",
                    "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"},
                }
                for idx, slot in sorted(calls.items())
                if slot["name"]
            ]
        return message


class MockClient:
    """离线演示模型：按脚本触发工具，用来在没有 Key 的情况下验证整条链路。

    它走的是和真实模型完全一样的消息协议 —— 循环、工具、前端一行代码都不用改。
    """

    name = "mock-demo"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.step = 0

    async def _say(self, on_delta, text: str) -> None:
        for i in range(0, len(text), 6):
            await on_delta(text[i : i + 6])
            await asyncio.sleep(0.012)

    async def complete(self, messages, tools=None, on_delta=None) -> dict:
        step = self.step
        self.step += 1

        if step == 0:
            text = "先读一下 stats.py，看看空列表是怎么处理的。"
            await self._say(on_delta, text)
            return _call(text, "read", {"path": "stats.py"})

        if step == 1:
            text = "空列表时返回 0 在统计上没有意义，改成 None。"
            await self._say(on_delta, text)
            return _call(
                text,
                "edit",
                {
                    "path": "stats.py",
                    "old": "    if not nums:\n        return 0",
                    "new": "    if not nums:\n        return None",
                },
            )

        if step == 2:
            text = "匹配不唯一 —— 补上函数签名再改一次。"
            await self._say(on_delta, text)
            return _call(
                text,
                "edit",
                {
                    "path": "stats.py",
                    "old": "def mean(nums):\n    if not nums:\n        return 0",
                    "new": "def mean(nums):\n    if not nums:\n        return None",
                },
            )

        if step == 3:
            text = "跑一遍测试。"
            await self._say(on_delta, text)
            return _call(text, "bash", {"command": "python -m unittest -q", "timeout": 120})

        text = "改好了，只动了 mean 一处：空列表现在返回 None，其余逻辑没碰。测试全绿。"
        await self._say(on_delta, text)
        return {"role": "assistant", "content": text}


def _call(text: str, name: str, args: dict) -> dict:
    return {
        "role": "assistant",
        "content": text or None,
        "tool_calls": [
            {
                "id": f"call_{name}_{abs(hash((name, json.dumps(args, sort_keys=True)))) % 100000}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            }
        ],
    }


def build_client(cfg: Config):
    return MockClient(cfg) if cfg.mock else LLMClient(cfg)
