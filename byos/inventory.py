"""Ansible-style device inventory: MAC/glob -> tags + per-group display config."""

from __future__ import annotations

import fnmatch
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class GroupSettings(BaseModel):
    members: list[str] = Field(default_factory=list)
    priority: int = 0
    refresh_rate: Optional[int] = None
    full_refresh_every: Optional[int] = None
    special_function: Optional[str] = None


class Inventory(BaseModel):
    groups: dict[str, GroupSettings] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: str) -> "Inventory":
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return cls.model_validate(data)
        except FileNotFoundError:
            return cls()
        except Exception as e:
            print(f"  [inventory] WARNING: {path} unreadable ({e}); no groups")
            return cls()

    def resolve_tags(self, mac: str) -> list[str]:
        m = mac.lower()
        out = []
        for name, g in self.groups.items():
            for pat in g.members:
                if fnmatch.fnmatch(m, pat.lower()):
                    out.append(name)
                    break
        return out

    def settings_for(self, tags: list[str]) -> list[tuple[str, GroupSettings]]:
        """(name, settings) for the given tags, highest priority first (ties: file order)."""
        items = [(n, self.groups[n]) for n in tags if n in self.groups]
        # stable sort on file order already; sort by -priority keeps ties in order
        return sorted(items, key=lambda kv: -kv[1].priority)
