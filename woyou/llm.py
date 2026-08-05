# -*- coding: utf-8 -*-
"""卧游 · DeepSeek 客户端（零依赖，urllib 实现）。

- 环境变量 DEEPSEEK_API_KEY（或项目根目录 .env）
- 可选 DEEPSEEK_BASE_URL（默认 https://api.deepseek.com）
- 可选 WOYOU_MODEL（默认 deepseek-chat）
引擎的数值结算从不依赖 LLM；LLM 只产出文字（以及 do 命令的意图分类）。
"""
import json
import os
import time
import urllib.error
import urllib.request

from .util import load_env


class LLMError(Exception):
    pass


def has_key() -> bool:
    load_env()
    return bool(os.environ.get("DEEPSEEK_API_KEY"))


class DeepSeek:
    def __init__(self, model: str = None):
        load_env()
        self.api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        self.base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.model = model or os.environ.get("WOYOU_MODEL", "deepseek-chat")
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    def chat(self, system: str, user: str, json_mode: bool = False,
             temperature: float = 0.8, max_tokens: int = 900,
             retries: int = 3, timeout: int = 180) -> str:
        if not self.api_key:
            raise LLMError("未配置 DEEPSEEK_API_KEY（可在项目根目录 .env 中设置）")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            self.base + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        last_err = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                u = data.get("usage", {})
                self.usage["prompt_tokens"] += u.get("prompt_tokens", 0)
                self.usage["completion_tokens"] += u.get("completion_tokens", 0)
                self.usage["calls"] += 1
                return data["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8")[:300]
                except Exception:
                    pass
                last_err = LLMError(f"DeepSeek HTTP {e.code}: {body}")
                if e.code in (401, 402, 403, 422):   # 无效 key / 欠费 / 参数错，重试无意义
                    raise last_err
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = LLMError(f"网络错误：{e}")
            except (KeyError, json.JSONDecodeError) as e:
                last_err = LLMError(f"响应解析失败：{e}")
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
        raise last_err or LLMError("未知错误")

    def chat_json(self, system: str, user: str, **kw) -> dict:
        """JSON 模式调用 + 解析（含代码块剥离兜底）。"""
        text = self.chat(system, user, json_mode=True, **kw)
        return parse_json_loose(text)


def parse_json_loose(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)
