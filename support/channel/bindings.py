"""绑定：外部账号 → 核心人员 id。

**这是唯一知道"QQ""微信"这些词的地方。** 核心只看到 1 号、2 号。

规则：
- 主人固定 1 号（配置里的 owner）
- 见过的账号记下来；没见过的自动分配新号
- 终端（stub）不会来别人，配置里直接绑 1 号
"""

from __future__ import annotations

from typing import Any

from core import repo


class Bindings:
    def __init__(self, cfg, persona: str = "xi"):
        self.cfg = cfg
        self.persona = persona
        self.owner_id = int((cfg.core.get("owner") or {}).get("id") or 1)
        self.owner_name = str((cfg.core.get("owner") or {}).get("name") or "主人")
        self._ensure_owner()

    # ---- 主人 -----------------------------------------------------
    def _ensure_owner(self) -> None:
        repo.upsert_person(
            self.cfg, person_id=self.owner_id, name=self.owner_name, is_owner=True,
            persona=self.persona,
        )
        for binding in (self.cfg.core.get("owner") or {}).get("bindings") or []:
            self.bind(str(binding.get("platform")), str(binding.get("external")), self.owner_id)

    # ---- 绑定 -----------------------------------------------------
    def bind(self, platform: str, external_id: str, person_id: int) -> None:
        repo.add_binding(self.cfg, platform, external_id, person_id, persona=self.persona)

    def resolve(self, platform: str, external_id: str, name: str | None = None) -> dict[str, Any]:
        """外部账号 → {person_id, name, is_owner}。

        没绑过就分配一个新号——她认识新朋友，不需要人去配。
        """
        pid = repo.resolve_binding(self.cfg, platform, external_id, persona=self.persona)
        if pid is None:
            pid = repo.next_person_id(self.cfg, persona=self.persona)
            self.bind(platform, external_id, pid)

        person = repo.get_person(self.cfg, pid, persona=self.persona) or {}
        if name:
            repo.upsert_person(
                self.cfg, person_id=pid, name=name, is_owner=bool(person.get("is_owner")),
                persona=self.persona,
            )
        return {
            "person_id": pid,
            "name": name or person.get("name") or f"{pid}号",
            "is_owner": bool(person.get("is_owner")),
        }
