"""读配置。

带 mtime 缓存：文件没改就不重读，改了下一次调用立刻生效。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class Config:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._overrides: dict[str, dict[str, Any]] = {}

    def override(self, rel: str, data: dict[str, Any]) -> None:
        """测试用：把这个配置整个换掉，不读文件。"""
        self._overrides[rel] = data

    # ---- 读文件（带缓存） ----------------------------------------
    def _load(self, rel: str) -> dict[str, Any]:
        if rel in self._overrides:
            return self._overrides[rel]
        path = self.root / rel
        if not path.exists():
            return {}
        mtime = path.stat().st_mtime
        hit = self._cache.get(rel)
        if hit and hit[0] == mtime:
            return hit[1]
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"配置格式不对（应该是字典）：{rel}")
        self._cache[rel] = (mtime, data)
        return data

    # ---- 五份配置 -------------------------------------------------
    @property
    def core(self) -> dict[str, Any]:
        return self._load("config/core.yml")

    @property
    def providers(self) -> dict[str, Any]:
        return self._load("config/providers.yml")

    @property
    def tiers(self) -> dict[str, Any]:
        return self._load("config/tiers.yml")

    @property
    def services(self) -> dict[str, Any]:
        return self._load("config/services.yml")

    def persona(self, persona_id: str) -> dict[str, Any]:
        return self._load(f"config/personas/{persona_id}/persona.yml")

    # ---- 常用取值 -------------------------------------------------
    def tier(self, name: str, persona_id: str | None = None) -> dict[str, Any]:
        """取一个档位，叠加人格覆盖（/model 改的就是 overrides）。"""
        tiers = self.tiers.get("tiers") or {}
        if name not in tiers:
            raise KeyError(f"档位不存在：{name}（tiers.yml 里没有）")
        tier = dict(tiers[name])
        if persona_id:
            overrides = self.persona(persona_id).get("overrides") or {}
            tier.update(overrides.get(name) or {})
        return tier

    def store_root(self) -> Path:
        rel = (self.core.get("paths") or {}).get("store", "store")
        return self.root / rel

    def admin_id(self, persona_id: str = "xi") -> int:
        return int((self.persona(persona_id).get("account") or {}).get("admin_id") or 0)

    def tool_allow(self, persona_id: str = "xi") -> list[str]:
        tools = self.persona(persona_id).get("tools") or {}
        return list(tools.get("allow") or [])
