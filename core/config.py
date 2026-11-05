"""读配置。

带 mtime 缓存：文件没改就不重读，改了下一次调用立刻生效。
"""

from __future__ import annotations

import os
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

    # 默认用试验库：平时试玩、调试都不该碰正式数据。
    # 正式跑：XI_STORE=store python3 main.py
    DEV_STORE = "store/_dev"

    def store_root(self) -> Path:
        """数据根。

        **默认是试验库** `store/_dev`——随手跑一次不该写进她的正式记录。
        要往正式库写，必须显式 `XI_STORE=store`。

        人设不在这里，见 persona_root()：她是源码，不是数据。
        """
        rel = (self.core.get("paths") or {}).get("store", self.DEV_STORE)
        # 测试用 override 写死了路径，优先级最高
        if "config/core.yml" in self._overrides:
            return self.root / rel
        env = os.environ.get("XI_STORE")
        if env:
            p = Path(env)
            return p if p.is_absolute() else self.root / p
        return self.root / self.DEV_STORE

    def persona_root(self) -> Path:
        """人设目录。它进 git、跟代码一起走，不随数据根切换。"""
        rel = (self.core.get("paths") or {}).get("persona", "store/processed/1-persona")
        return self.root / rel

    def admin_id(self, persona_id: str = "xi") -> int:
        """兼容老代码：默认平台的号。新代码请用 is_owner()。"""
        accounts = self.owner_accounts(persona_id)
        return int(accounts.get("stub") or accounts.get("qq") or 0)

    def owner_accounts(self, persona_id: str = "xi") -> dict[str, str]:
        """主人在各平台上的号：{channel: user_id}。"""
        owner = self.persona(persona_id).get("owner") or {}
        return {str(k): str(v) for k, v in (owner.get("accounts") or {}).items()}

    def owner_id(self, persona_id: str = "xi") -> int:
        """核心人员 id：主人固定是 1。"""
        return int((self.core.get("owner") or {}).get("id") or 1)

    def owner_name(self, persona_id: str = "xi") -> str:
        return str((self.persona(persona_id).get("owner") or {}).get("name") or "主人")

    def is_owner(self, channel: str, user_id: str | int, persona_id: str = "xi") -> bool:
        """这个平台上这个号是不是主人。换平台也认得出。"""
        want = self.owner_accounts(persona_id).get(str(channel))
        return bool(want) and want == str(user_id)

    def tool_allow(self, persona_id: str = "xi") -> list[str]:
        tools = self.persona(persona_id).get("tools") or {}
        return list(tools.get("allow") or [])


class ProdStoreRefused(RuntimeError):
    """开发期往正式库里写东西，拒绝。"""


def guard_production_store(cfg: Config) -> None:
    """开发期禁止写正式库 `store/`。

    规则（2026-11-05 定）：项目正式完成之前，正式库里不存任何内容。
    试验库 `store/_dev/` 随便写、随时可删；要真用正式库测一次，
    必须显式 `XI_CONFIRM_PROD=1`，测完自己删干净。

    为什么写成代码而不是约定：上次已经发生过一次"误写进正式库"，
    靠记性挡不住第二次。
    """
    prod = (Path(cfg.root) / "store").resolve()
    if cfg.store_root().resolve() != prod:
        return
    if os.environ.get("XI_CONFIRM_PROD") == "1":
        return
    raise ProdStoreRefused(
        "拒绝写正式库 store/。开发期用默认试验库 store/_dev；"
        "确实要用正式库测试，加 XI_CONFIRM_PROD=1，用完删掉。"
    )
