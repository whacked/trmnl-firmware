"""Renderer registry: match a device to the best renderer, resolve display config.

Plugins in byos/clients/ register via @renderer(match=..., priority=..., <config>).
"""

from __future__ import annotations

import fnmatch
import importlib.util
import os
from dataclasses import dataclass
from typing import Callable, Optional

# A matcher takes a device-like object (.mac, .model, .tags) -> bool.
Matcher = Callable[[object], bool]


# --- matchers ---------------------------------------------------------------
def mac(addr: str) -> Matcher:
    a = addr.lower()
    return lambda d: getattr(d, "mac", "").lower() == a


def tag(name: str) -> Matcher:
    return lambda d: name in getattr(d, "tags", [])


def model(name: str) -> Matcher:
    n = name.lower()
    return lambda d: (getattr(d, "model", None) or "").lower() == n


def glob(pattern: str) -> Matcher:
    p = pattern.lower()
    return lambda d: fnmatch.fnmatch(getattr(d, "mac", "").lower(), p)


def predicate(fn: Callable[[object], bool]) -> Matcher:
    return lambda d: bool(fn(d))


# --- config -----------------------------------------------------------------
@dataclass
class RenderConfig:
    refresh_rate: Optional[int] = None
    full_refresh_every: Optional[int] = None
    special_function: Optional[str] = None


@dataclass
class RegisteredRenderer:
    fn: Callable
    matcher: Matcher
    priority: int
    config: RenderConfig


_renderers: list[RegisteredRenderer] = []
_seq = 0  # registration order for stable tie-breaking
_default: Optional[Callable] = None


def reset() -> None:
    """Test helper: clear all registrations and the default."""
    global _renderers, _seq, _default
    _renderers = []
    _seq = 0
    _default = None


def set_default(fn: Callable) -> None:
    global _default
    _default = fn


def renderer(*, match: Matcher, priority: int = 0,
             refresh_rate: Optional[int] = None,
             full_refresh_every: Optional[int] = None,
             special_function: Optional[str] = None):
    def deco(fn):
        global _seq
        _renderers.append(RegisteredRenderer(
            fn=fn, matcher=match, priority=priority,
            config=RenderConfig(refresh_rate, full_refresh_every, special_function),
        ))
        _seq += 1
        return fn
    return deco


def resolve(device) -> RegisteredRenderer:
    matches = [r for r in _renderers if r.matcher(device)]
    if matches:
        # highest priority; ties -> earliest registered (stable: _renderers order)
        return max(matches, key=lambda r: r.priority)
    return RegisteredRenderer(fn=_default, matcher=lambda d: True,
                              priority=-(10 ** 9), config=RenderConfig())


def resolve_config(pending: dict, group_cfgs: list[RenderConfig],
                   renderer_cfg: RenderConfig, defaults: RenderConfig) -> RenderConfig:
    """Per-field precedence: pending > first group that sets it > renderer > default."""
    def pick(field):
        if field in pending and pending[field] is not None:
            return pending[field]
        for g in group_cfgs:
            v = getattr(g, field)
            if v is not None:
                return v
        v = getattr(renderer_cfg, field)
        return v if v is not None else getattr(defaults, field)

    return RenderConfig(
        refresh_rate=pick("refresh_rate"),
        full_refresh_every=pick("full_refresh_every"),
        special_function=pick("special_function"),
    )


def load_clients(directory: str) -> None:
    """Import every *.py in `directory` so its @renderer decorators run."""
    if not os.path.isdir(directory):
        return
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        path = os.path.join(directory, fname)
        modname = "byos_client_" + fname[:-3]
        try:
            spec = importlib.util.spec_from_file_location(modname, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            print(f"  [registry] loaded client {fname}")
        except Exception as e:
            print(f"  [registry] WARNING: failed to load {fname}: {e}")
