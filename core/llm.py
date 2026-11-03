"""LLM 客户端：openai-compatible，按档位取 provider 和参数。

换 API 只改 providers.yml，这里不用动。
key 从环境变量或 ~/.dsh/.credentials.yaml 的 refs 取，配置里只写名字。
"""

from __future__ import annotations

import json
import os
import random
import string
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CRED_FILE = Path(os.path.expanduser("~/.dsh/.credentials.yaml"))


class LLMError(RuntimeError):
    pass


def load_key(ref: str) -> str:
    """环境变量 → credentials.yaml 的 refs → siliconflow 的 key 文件。"""
    val = os.environ.get(ref)
    if val:
        return val.strip()

    if CRED_FILE.exists():
        try:
            import yaml

            data = yaml.safe_load(CRED_FILE.read_text(encoding="utf-8")) or {}
            refs = data.get("refs") or {}
            if isinstance(refs.get(ref), str):
                return str(refs[ref]).strip()
        except Exception:  # noqa: BLE001
            pass

    sf = Path(os.path.expanduser("~/.config/siliconflow/key"))
    if ref.upper().startswith("SILICONFLOW") and sf.exists():
        return sf.read_text(encoding="utf-8").strip()

    raise LLMError(f"找不到 key：{ref}（环境变量或 ~/.dsh/.credentials.yaml 的 refs 里都没有）")


def new_session() -> str:
    return "xi-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))


class LLM:
    """一个档位的调用器。每次 complete 一次。"""

    def __init__(self, cfg, tier_name: str, persona_id: str = "xi", session: str | None = None):
        self.cfg = cfg
        self.tier_name = tier_name
        self.tier = cfg.tier(tier_name, persona_id)

        providers = cfg.providers.get("providers") or {}
        provider_name = self.tier.get("provider")
        if provider_name not in providers:
            raise LLMError(f"档位 {tier_name} 引用了不存在的 provider：{provider_name}")
        self.provider_name = provider_name
        self.provider = providers[provider_name]

        self.base_url = str(self.provider["base_url"]).rstrip("/")
        self.key = load_key(self.provider.get("key_ref") or "")
        self.model = self.tier.get("model")
        self.temperature = self.tier.get("temperature")
        self.max_tokens = int(self.tier.get("max_tokens") or 1024)
        self.timeout = float(self.tier.get("timeout") or 60)
        self.session = session or new_session()

        self.extra_headers = {
            k: str(v).replace("{session}", self.session)
            for k, v in (self.provider.get("headers") or {}).items()
        }

    # ---- 调用 -----------------------------------------------------

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": int(max_tokens or self.max_tokens),
        }
        temp = self.temperature if temperature is None else temperature
        if temp is not None:
            payload["temperature"] = float(temp)
        effort = reasoning_effort or self.tier.get("reasoning_effort")
        if effort:
            payload["reasoning_effort"] = effort

        data = self._post(payload)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}

        return {
            "text": (message.get("content") or "").strip(),
            "reasoning": (message.get("reasoning_content") or "").strip(),
            "usage": {
                "in": usage.get("prompt_tokens"),
                "out": usage.get("completion_tokens"),
                "total": usage.get("total_tokens"),
            },
            "finish": choice.get("finish_reason"),
            "model": data.get("model") or self.model,
            "provider": self.provider_name,
            "tier": self.tier_name,
        }

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.key}",
            # 网关前面有 Cloudflare，默认 urllib UA 会被 403
            "User-Agent": "xi-project/0.2 (python-urllib)",
            "Accept": "application/json",
            **self.extra_headers,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body, headers=headers, method="POST"
        )

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "ignore")[:300]
                last_error = LLMError(f"HTTP {exc.code}：{detail}")
                if exc.code in (400, 401, 403, 404):
                    break  # 重试也没用
            except Exception as exc:  # noqa: BLE001
                last_error = exc
            time.sleep(1.0 + attempt)
        raise LLMError(f"{self.tier_name} 调用失败：{last_error}")
