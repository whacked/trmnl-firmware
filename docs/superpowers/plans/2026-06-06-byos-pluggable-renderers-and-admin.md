# BYOS Pluggable Per-Client Renderers + Admin — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the BYOS server into a small protocol core plus a plugin registry of per-client renderers (matched by MAC/tag/model/glob/predicate via an Ansible-style `inventory.yaml`), with per-group display config and an interactive `/admin` page.

**Architecture:** `protocol.py` parses firmware headers → `DeviceReport`; `state.py` keeps the latest snapshot + overrides + one-shot pending commands per device; `inventory.py` maps MACs→tags+group config; `registry.py` matches a device to the best renderer and resolves display config; `render.py` holds the default renderer + image encoders; `byos/clients/*.py` are auto-imported plugins; `admin.py` serves the operator UI; `server.py` is thin FastAPI wiring.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, Pydantic v2, Pillow, PyYAML; tests with pytest + Starlette `TestClient` (httpx). All deps via nix.

**Spec:** `docs/superpowers/specs/2026-06-06-byos-pluggable-renderers-and-admin-design.md`

**Test command (used throughout):**
```bash
nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests -q'
```

---

## Task 0: Test/dev environment (nix deps + pytest scaffold)

**Files:**
- Modify: `byos/shell.nix`
- Modify: `byos/server.py:2` (shebang deps line)
- Create: `byos/tests/__init__.py`
- Create: `byos/tests/conftest.py`
- Create: `byos/tests/test_smoke.py`

- [ ] **Step 1: Add deps to the dev shell**

Replace the `packages` line in `byos/shell.nix` with:
```nix
  packages = [ (pkgs.python3.withPackages (ps: with ps; [ pillow fastapi uvicorn pydantic pyyaml pytest httpx ])) ];
```

- [ ] **Step 2: Add pyyaml to the server shebang**

Replace line 2 of `byos/server.py` with:
```python
#!nix-shell -i python3 -p "python3.withPackages(ps: with ps; [ pillow fastapi uvicorn pydantic pyyaml ])"
```

- [ ] **Step 3: Create the test package + import-path fixture**

Create `byos/tests/__init__.py` (empty file).

Create `byos/tests/conftest.py`:
```python
import os
import sys

# Make byos/ modules importable as top-level (state, protocol, registry, ...).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- [ ] **Step 4: Write a smoke test**

Create `byos/tests/test_smoke.py`:
```python
def test_env_has_deps():
    import pydantic  # noqa: F401
    import yaml  # noqa: F401
    import PIL  # noqa: F401
    assert True
```

- [ ] **Step 5: Run it**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests -q'`
Expected: PASS (1 passed). First run may provision the nix env.

- [ ] **Step 6: Commit**

```bash
git add byos/shell.nix byos/server.py byos/tests/
git commit -m "test: pytest scaffold + pyyaml/pytest/httpx in byos nix env"
```

---

## Task 1: `protocol.py` — header → DeviceReport

**Files:**
- Create: `byos/protocol.py`
- Create: `byos/tests/test_protocol.py`

- [ ] **Step 1: Write the failing test**

Create `byos/tests/test_protocol.py`:
```python
from protocol import DeviceReport, parse_report


def test_parse_full_display_headers():
    headers = {
        "ID": "44:1B:F6:81:A2:80",
        "Access-Token": "byos-x",
        "Model": "og",
        "FW-Version": "1.8.5",
        "Battery-Voltage": "4.05",
        "RSSI": "-57",
        "WiFi-SSID": "huang%20home",   # firmware percent-encodes
        "WiFi-Band": "2.4",
        "Refresh-Rate": "900",
        "Update-Source": "scheduled",
        "Wake-Time": "1234",
        "Image-Cached": "true",
        "Width": "800",
        "Height": "480",
    }
    r = parse_report(headers)
    assert isinstance(r, DeviceReport)
    assert r.mac == "44:1B:F6:81:A2:80"
    assert r.battery_voltage == 4.05
    assert r.rssi == -57
    assert r.wifi_ssid == "huang home"          # percent-decoded
    assert r.image_cached is True
    assert r.width == 800 and r.height == 480


def test_parse_is_case_insensitive_and_sparse():
    r = parse_report({"id": "AA:BB:CC:DD:EE:FF", "fw-version": "1.0"})
    assert r.mac == "AA:BB:CC:DD:EE:FF"
    assert r.fw_version == "1.0"
    assert r.battery_voltage is None
    assert r.rssi is None


def test_parse_tolerates_garbage_numbers():
    r = parse_report({"ID": "A", "Battery-Voltage": "n/a", "RSSI": ""})
    assert r.battery_voltage is None
    assert r.rssi is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests/test_protocol.py -q'`
Expected: FAIL (ModuleNotFoundError: protocol).

- [ ] **Step 3: Implement `protocol.py`**

Create `byos/protocol.py`:
```python
"""Canonical parse of the firmware's request headers into a DeviceReport.

This is the ONLY module that knows header names (see
lib/trmnl/src/api-client/request_headers.cpp). Everything else works with the
typed DeviceReport.
"""

from __future__ import annotations

from typing import Mapping, Optional
from urllib.parse import unquote

from pydantic import BaseModel


class DeviceReport(BaseModel):
    mac: str
    api_key: Optional[str] = None
    model: Optional[str] = None
    fw_version: Optional[str] = None
    battery_voltage: Optional[float] = None
    rssi: Optional[int] = None
    wifi_ssid: Optional[str] = None
    wifi_band: Optional[str] = None
    refresh_rate: Optional[int] = None
    update_source: Optional[str] = None
    wake_time: Optional[int] = None
    image_cached: Optional[bool] = None
    width: Optional[int] = None
    height: Optional[int] = None


def _lower(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(k).lower(): v for k, v in headers.items()}


def _f(d: dict, k: str) -> Optional[float]:
    try:
        return float(d[k])
    except (KeyError, TypeError, ValueError):
        return None


def _i(d: dict, k: str) -> Optional[int]:
    try:
        return int(d[k])
    except (KeyError, TypeError, ValueError):
        return None


def parse_report(headers: Mapping[str, str]) -> DeviceReport:
    """Build a DeviceReport from request headers (case-insensitive)."""
    h = _lower(headers)
    ssid = h.get("wifi-ssid")
    return DeviceReport(
        mac=h.get("id", "00:00:00:00:00:00"),
        api_key=h.get("access-token"),
        model=h.get("model"),
        fw_version=h.get("fw-version"),
        battery_voltage=_f(h, "battery-voltage"),
        rssi=_i(h, "rssi"),
        wifi_ssid=unquote(ssid) if ssid else None,
        wifi_band=h.get("wifi-band"),
        refresh_rate=_i(h, "refresh-rate"),
        update_source=h.get("update-source"),
        wake_time=_i(h, "wake-time"),
        image_cached=(h["image-cached"].lower() == "true") if "image-cached" in h else None,
        width=_i(h, "width"),
        height=_i(h, "height"),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests/test_protocol.py -q'`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add byos/protocol.py byos/tests/test_protocol.py
git commit -m "feat(byos): protocol.py — header → DeviceReport core parse"
```

---

## Task 2: `state.py` — extend DeviceState + StateStore

**Files:**
- Modify: `byos/state.py` (full rewrite)
- Create: `byos/tests/test_state.py`

- [ ] **Step 1: Write the failing test**

Create `byos/tests/test_state.py`:
```python
import os
import tempfile

from protocol import DeviceReport
from state import StateStore


def _store():
    d = tempfile.mkdtemp()
    return StateStore(os.path.join(d, "state.json"))


def _report(mac, **kw):
    return DeviceReport(mac=mac, **kw)


def test_upsert_assigns_sequential_ids_and_keeps_them():
    s = _store()
    a = s.upsert_report(_report("AA:AA:AA:AA:AA:AA"), tags=["x"])
    b = s.upsert_report(_report("BB:BB:BB:BB:BB:BB"), tags=[])
    assert a.friendly_id == "DEV001"
    assert b.friendly_id == "DEV002"
    a2 = s.upsert_report(_report("AA:AA:AA:AA:AA:AA", battery_voltage=3.9), tags=["x", "y"])
    assert a2.friendly_id == "DEV001"
    assert a2.battery_voltage == 3.9
    assert a2.tags == ["x", "y"]


def test_battery_percent():
    s = _store()
    d = s.upsert_report(_report("AA:AA:AA:AA:AA:AA", battery_voltage=3.75), tags=[])
    assert d.battery_percent() == 50  # (3.75-3.30)/0.90*100


def test_pending_take_clears():
    s = _store()
    s.upsert_report(_report("AA:AA:AA:AA:AA:AA"), tags=[])
    s.queue_command("AA:AA:AA:AA:AA:AA", force_full_refresh=True, special_function="identify")
    p = s.take_pending("AA:AA:AA:AA:AA:AA")
    assert p.force_full_refresh is True and p.special_function == "identify"
    p2 = s.take_pending("AA:AA:AA:AA:AA:AA")
    assert p2.force_full_refresh is False and p2.special_function is None


def test_group_override_and_last_log():
    s = _store()
    s.upsert_report(_report("AA:AA:AA:AA:AA:AA"), tags=["auto"])
    s.set_group_override("AA:AA:AA:AA:AA:AA", "garden")
    s.set_last_log("AA:AA:AA:AA:AA:AA", "boot ok")
    d = s.get("AA:AA:AA:AA:AA:AA")
    assert d.group_override == "garden"
    assert d.last_log == "boot ok"


def test_roundtrip_and_corrupt(tmp_path):
    p = str(tmp_path / "s.json")
    s = StateStore(p)
    s.upsert_report(_report("AA:AA:AA:AA:AA:AA"), tags=[])
    s2 = StateStore(p)
    assert s2.get("AA:AA:AA:AA:AA:AA").friendly_id == "DEV001"
    open(p, "w").write("{ not json")
    s3 = StateStore(p)
    assert s3.latest() is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests/test_state.py -q'`
Expected: FAIL (StateStore has no `upsert_report`).

- [ ] **Step 3: Rewrite `byos/state.py`**

Replace the entire file `byos/state.py` with:
```python
"""Latest-snapshot device state for the BYOS server.

The JSON file is only ever produced by model_dump_json() and consumed by
model_validate_json(). In-memory ServerState is authoritative; the file is an
atomically-written mirror. Thread-safe (uvicorn serves concurrently).
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from protocol import DeviceReport


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PendingCommands(BaseModel):
    force_full_refresh: bool = False
    special_function: Optional[str] = None


class DeviceState(BaseModel):
    mac: str
    friendly_id: str
    api_key: str
    model: Optional[str] = None
    fw_version: Optional[str] = None
    first_seen: datetime = Field(default_factory=_now)
    last_seen: datetime = Field(default_factory=_now)

    # reported telemetry (latest)
    battery_voltage: Optional[float] = None
    rssi: Optional[int] = None
    wifi_ssid: Optional[str] = None
    wifi_band: Optional[str] = None
    refresh_rate: Optional[int] = None
    update_source: Optional[str] = None
    wake_time: Optional[int] = None
    image_cached: Optional[bool] = None
    width: Optional[int] = None
    height: Optional[int] = None
    last_log: Optional[str] = None

    # resolution + admin
    tags: list[str] = Field(default_factory=list)
    group_override: Optional[str] = None
    pending: PendingCommands = Field(default_factory=PendingCommands)

    def battery_percent(self) -> Optional[int]:
        if self.battery_voltage is None:
            return None
        pct = (self.battery_voltage - 3.30) / (4.20 - 3.30) * 100.0
        return max(0, min(100, round(pct)))


class ServerState(BaseModel):
    devices: dict[str, DeviceState] = Field(default_factory=dict)
    next_seq: int = 1


# Fields copied from a DeviceReport into a DeviceState on each poll.
_REPORT_FIELDS = (
    "model", "fw_version", "battery_voltage", "rssi", "wifi_ssid", "wifi_band",
    "refresh_rate", "update_source", "wake_time", "image_cached", "width", "height",
)


class StateStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._state = self._load()

    def _load(self) -> ServerState:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                return ServerState.model_validate_json(f.read())
        except FileNotFoundError:
            return ServerState()
        except Exception as e:
            print(f"  [state] WARNING: {self._path} unreadable ({e}); starting empty")
            return ServerState()

    def _persist_locked(self) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(self._state.model_dump_json(indent=2))
        os.replace(tmp, self._path)

    def _get_or_create_locked(self, mac: str) -> DeviceState:
        dev = self._state.devices.get(mac)
        if dev is None:
            seq = self._state.next_seq
            self._state.next_seq += 1
            dev = DeviceState(
                mac=mac,
                friendly_id=f"DEV{seq:03d}",
                api_key="byos-" + mac.replace(":", "").lower(),
            )
            self._state.devices[mac] = dev
        return dev

    def upsert_report(self, report: DeviceReport, tags: list[str]) -> DeviceState:
        with self._lock:
            dev = self._get_or_create_locked(report.mac)
            for f in _REPORT_FIELDS:
                v = getattr(report, f)
                if v is not None:
                    setattr(dev, f, v)
            if report.api_key:
                dev.api_key = report.api_key
            dev.tags = list(tags)
            dev.last_seen = _now()
            self._persist_locked()
            return dev.model_copy(deep=True)

    def queue_command(self, mac: str, *, force_full_refresh: bool = False,
                      special_function: Optional[str] = None) -> None:
        with self._lock:
            dev = self._get_or_create_locked(mac)
            if force_full_refresh:
                dev.pending.force_full_refresh = True
            if special_function is not None:
                dev.pending.special_function = special_function or None
            self._persist_locked()

    def take_pending(self, mac: str) -> PendingCommands:
        with self._lock:
            dev = self._state.devices.get(mac)
            if dev is None:
                return PendingCommands()
            p = dev.pending.model_copy(deep=True)
            dev.pending = PendingCommands()
            self._persist_locked()
            return p

    def set_group_override(self, mac: str, group: Optional[str]) -> None:
        with self._lock:
            dev = self._get_or_create_locked(mac)
            dev.group_override = group or None
            self._persist_locked()

    def set_last_log(self, mac: str, line: str) -> None:
        with self._lock:
            dev = self._get_or_create_locked(mac)
            dev.last_log = line
            dev.last_seen = _now()
            self._persist_locked()

    def get(self, mac: str) -> Optional[DeviceState]:
        with self._lock:
            dev = self._state.devices.get(mac)
            return dev.model_copy(deep=True) if dev else None

    def all(self) -> list[DeviceState]:
        with self._lock:
            return [d.model_copy(deep=True) for d in self._state.devices.values()]

    def latest(self) -> Optional[DeviceState]:
        with self._lock:
            if not self._state.devices:
                return None
            return max(self._state.devices.values(), key=lambda d: d.last_seen).model_copy(deep=True)
```

- [ ] **Step 4: Run to verify it passes**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests/test_state.py -q'`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add byos/state.py byos/tests/test_state.py
git commit -m "feat(byos): extend state — reports, tags, overrides, pending commands"
```

---

## Task 3: `inventory.py` + `inventory.yaml` — tags & group config

**Files:**
- Create: `byos/inventory.py`
- Create: `byos/inventory.yaml`
- Create: `byos/tests/test_inventory.py`

- [ ] **Step 1: Write the failing test**

Create `byos/tests/test_inventory.py`:
```python
from inventory import Inventory


def _inv():
    return Inventory.model_validate({
        "groups": {
            "garden": {"members": ["A0:EA:DE:AD:BE:EF", "DC:44:*"],
                       "refresh_rate": 3600, "priority": 5},
            "desk": {"members": ["44:1B:F6:81:A2:80"], "refresh_rate": 900},
            "all_og": {"members": ["*"], "full_refresh_every": 32, "priority": -10},
        }
    })


def test_exact_and_glob_membership_case_insensitive():
    inv = _inv()
    assert set(inv.resolve_tags("a0:ea:de:ad:be:ef")) >= {"garden", "all_og"}
    assert "garden" in inv.resolve_tags("DC:44:11:22:33:44")
    assert inv.resolve_tags("99:99:99:99:99:99") == ["all_og"]


def test_settings_ordered_by_priority_desc():
    inv = _inv()
    tags = inv.resolve_tags("44:1B:F6:81:A2:80")  # desk(0) + all_og(-10)
    ordered = inv.settings_for(tags)
    assert [name for name, _ in ordered][0] == "desk"
    assert ordered[-1][0] == "all_og"


def test_missing_file_is_empty(tmp_path):
    inv = Inventory.load(str(tmp_path / "nope.yaml"))
    assert inv.groups == {}
    assert inv.resolve_tags("any") == []


def test_malformed_file_is_empty(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("groups: [this is not a mapping")
    inv = Inventory.load(str(p))
    assert inv.groups == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests/test_inventory.py -q'`
Expected: FAIL (ModuleNotFoundError: inventory).

- [ ] **Step 3: Implement `inventory.py`**

Create `byos/inventory.py`:
```python
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
```

- [ ] **Step 4: Create a starter `inventory.yaml`**

Create `byos/inventory.yaml`:
```yaml
# Ansible-style device inventory for the BYOS server.
#   <group-name>:
#     members: list of MACs (exact) or globs (fnmatch, e.g. "DC:44:*"), case-insensitive
#     priority: higher wins when a device is in several groups (default 0)
#     refresh_rate / full_refresh_every / special_function: optional per-group display config
#
# A device's tags = every group it matches. A plugin renderer in byos/clients/
# can target a group via match=tag("garden").
groups:
  example_desk:
    members: []          # e.g. ["44:1B:F6:81:A2:80"]
    refresh_rate: 900
```

- [ ] **Step 5: Run to verify it passes**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests/test_inventory.py -q'`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add byos/inventory.py byos/inventory.yaml byos/tests/test_inventory.py
git commit -m "feat(byos): inventory.yaml + Pydantic loader (tags, globs, group config)"
```

---

## Task 4: `render.py` — RenderContext, default renderer, encoders

**Files:**
- Create: `byos/render.py`
- Create: `byos/tests/test_render.py`

- [ ] **Step 1: Write the failing test**

Create `byos/tests/test_render.py`:
```python
import io

from PIL import Image

from protocol import DeviceReport
from state import StateStore
from render import RenderContext, default_render, to_bmp, to_png, WIDTH, HEIGHT


def _device():
    s = StateStore(io.StringIO().name if False else "/tmp/byos_render_test_state.json")
    return s.upsert_report(DeviceReport(mac="44:1B:F6:81:A2:80", battery_voltage=4.05,
                                        rssi=-57, wifi_ssid="huanghome", fw_version="1.8.5",
                                        model="og"), tags=["desk"])


def test_default_render_returns_1bit_canvas():
    img = default_render(RenderContext(device=_device()))
    assert img.mode == "1"
    assert img.size == (WIDTH, HEIGHT)


def test_to_bmp_is_exact_firmware_size():
    img = default_render(RenderContext(device=_device()))
    data = to_bmp(img)
    assert data[:2] == b"BM"
    assert len(data) == 48062  # matches firmware bmp.cpp validator


def test_to_png_decodes():
    img = default_render(RenderContext(device=_device()))
    png = to_png(img)
    assert Image.open(io.BytesIO(png)).size == (WIDTH, HEIGHT)
```

- [ ] **Step 2: Run to verify it fails**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests/test_render.py -q'`
Expected: FAIL (ModuleNotFoundError: render).

- [ ] **Step 3: Implement `render.py`**

Create `byos/render.py`:
```python
"""Rendering: the canvas context, the default renderer, and image encoders.

A renderer is `fn(ctx: RenderContext) -> PIL.Image` returning a 1-bit WIDTHxHEIGHT
image. Plugins may import default_render and draw on top of it.
"""

from __future__ import annotations

import io
import datetime
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from state import DeviceState

WIDTH, HEIGHT = 800, 480
WHITE, BLACK = 255, 0
INVERT = False


@dataclass
class RenderContext:
    device: DeviceState
    width: int = WIDTH
    height: int = HEIGHT

    @property
    def now(self) -> datetime.datetime:
        return datetime.datetime.now()


def font(size: int):
    for path in (
        "/run/current-system/sw/share/X11/fonts/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/SFNS.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def blank() -> Image.Image:
    return Image.new("1", (WIDTH, HEIGHT), WHITE)


def default_render(ctx: RenderContext) -> Image.Image:
    img = blank()
    draw = ImageDraw.Draw(img)
    dev = ctx.device
    now = ctx.now

    draw.rectangle([4, 4, WIDTH - 5, HEIGHT - 5], outline=BLACK, width=3)
    draw.text((40, 34), "TRMNL BYOS", font=font(64), fill=BLACK)
    draw.text((42, 110), "your own server", font=font(30), fill=BLACK)
    draw.line([40, 156, WIDTH - 40, 156], fill=BLACK, width=2)
    draw.text((40, 180), now.strftime("%H:%M"), font=font(150), fill=BLACK)
    draw.text((44, 350), now.strftime("%A, %d %B %Y"), font=font(34), fill=BLACK)

    x = 448
    pct = dev.battery_percent()
    batt = "—"
    if dev.battery_voltage is not None:
        batt = f"{dev.battery_voltage:.2f} V" + (f"  ({pct}%)" if pct is not None else "")
    rows = [
        ("Device", dev.friendly_id),
        ("MAC", dev.mac),
        ("Battery", batt),
        ("WiFi", dev.wifi_ssid or "—"),
        ("RSSI", f"{dev.rssi} dBm" if dev.rssi is not None else "—"),
        ("FW", dev.fw_version or "—"),
        ("Seen", dev.last_seen.astimezone().strftime("%H:%M:%S")),
    ]
    y = 184
    for label, value in rows:
        draw.text((x, y), f"{label}:", font=font(20), fill=BLACK)
        draw.text((x + 100, y), str(value), font=font(20), fill=BLACK)
        y += 38
    return img


def _maybe_invert(img: Image.Image) -> Image.Image:
    return img.point(lambda p: WHITE if p == BLACK else BLACK) if INVERT else img


def to_bmp(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    _maybe_invert(img).save(buf, format="BMP")
    return buf.getvalue()


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    _maybe_invert(img).convert("L").save(buf, format="PNG")
    return buf.getvalue()
```

- [ ] **Step 4: Run to verify it passes**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests/test_render.py -q'`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add byos/render.py byos/tests/test_render.py
git commit -m "feat(byos): render.py — RenderContext, default renderer, BMP/PNG encoders"
```

---

## Task 5: `registry.py` — matchers, @renderer, resolve, config, load_clients

**Files:**
- Create: `byos/registry.py`
- Create: `byos/tests/test_registry.py`

- [ ] **Step 1: Write the failing test**

Create `byos/tests/test_registry.py`:
```python
import types

import registry
from registry import (renderer, mac, tag, model, glob, predicate,
                      resolve, resolve_config, set_default, RenderConfig)


class Dev:
    def __init__(self, mac="44:1B:F6:81:A2:80", model="og", tags=None):
        self.mac = mac
        self.model = model
        self.tags = tags or []


def setup_function(_):
    registry.reset()  # test helper: clear registrations + default


def test_priority_and_fallback():
    set_default(lambda ctx: "DEFAULT")

    @renderer(match=tag("garden"), priority=10)
    def g(ctx):
        return "GARDEN"

    @renderer(match=model("og"), priority=1)
    def o(ctx):
        return "OG"

    assert resolve(Dev(tags=["garden"])).fn(None) == "GARDEN"   # higher priority wins
    assert resolve(Dev(tags=[])).fn(None) == "OG"               # model match
    assert resolve(Dev(model="x", tags=[])).fn(None) == "DEFAULT"  # nothing matches


def test_matchers():
    assert mac("AA:BB:CC:DD:EE:FF")(Dev(mac="aa:bb:cc:dd:ee:ff"))      # case-insensitive
    assert tag("garden")(Dev(tags=["garden"]))
    assert model("OG")(Dev(model="og"))
    assert glob("44:1B:*")(Dev(mac="44:1B:F6:81:A2:80"))
    assert predicate(lambda d: d.rssi if hasattr(d, "rssi") else True)(Dev())


def test_resolve_config_precedence():
    # pending > group > renderer default > global
    pending = {"full_refresh_every": 1}
    groups = [RenderConfig(refresh_rate=3600)]
    rcfg = RenderConfig(refresh_rate=900, full_refresh_every=16, special_function="none")
    out = resolve_config(pending, groups, rcfg,
                         defaults=RenderConfig(refresh_rate=300, full_refresh_every=8,
                                               special_function="none"))
    assert out.full_refresh_every == 1      # from pending
    assert out.refresh_rate == 3600         # from group (beats renderer default)
    assert out.special_function == "none"   # from renderer default


def test_load_clients(tmp_path):
    set_default(lambda ctx: "DEFAULT")
    (tmp_path / "p.py").write_text(
        "from registry import renderer, tag\n"
        "@renderer(match=tag('plug'), priority=3)\n"
        "def p(ctx):\n    return 'PLUG'\n"
    )
    registry.load_clients(str(tmp_path))
    assert resolve(Dev(tags=["plug"])).fn(None) == "PLUG"
```

- [ ] **Step 2: Run to verify it fails**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests/test_registry.py -q'`
Expected: FAIL (ModuleNotFoundError: registry).

- [ ] **Step 3: Implement `registry.py`**

Create `byos/registry.py`:
```python
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
```

(Max picks the first-registered among equal priorities because Python's `max`
returns the first maximal element and `_renderers` preserves registration order.)

- [ ] **Step 4: Run to verify it passes**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests/test_registry.py -q'`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add byos/registry.py byos/tests/test_registry.py
git commit -m "feat(byos): registry — matchers, @renderer, resolve, config precedence, load_clients"
```

---

## Task 6: `clients/default.py` — register the built-in default

**Files:**
- Create: `byos/clients/default.py`
- Create: `byos/clients/example_group.py`
- Create: `byos/tests/test_clients.py`

- [ ] **Step 1: Write the failing test**

Create `byos/tests/test_clients.py`:
```python
import os

import registry
from registry import resolve


class Dev:
    def __init__(self, mac="44:1B:F6:81:A2:80", model="og", tags=None):
        self.mac = mac
        self.model = model
        self.tags = tags or []


def test_clients_dir_loads_default_and_example():
    registry.reset()
    clients_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "clients")
    registry.load_clients(clients_dir)
    # default must be set (catch-all) and example_group renderer registered
    assert resolve(Dev(tags=[])).fn is not None
    assert resolve(Dev(tags=["example"])).priority >= 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests/test_clients.py -q'`
Expected: FAIL (clients dir missing / default not set).

- [ ] **Step 3: Create `byos/clients/default.py`**

```python
"""Built-in default renderer registration (auto-loaded). Sets the fallback used
when no other plugin matches a device."""

from registry import set_default
from render import default_render

set_default(default_render)
```

- [ ] **Step 4: Create `byos/clients/example_group.py`**

```python
"""Example plugin: a 'example'-tagged group renderer that extends the default.
Copy this file and change the matcher/drawing for your own clients."""

from PIL import ImageDraw

from registry import renderer, tag
from render import default_render, font, BLACK


@renderer(match=tag("example"), priority=10, refresh_rate=1800)
def example(ctx):
    img = default_render(ctx)            # build on the default frame
    draw = ImageDraw.Draw(img)
    draw.text((44, 430), "example group", font=font(22), fill=BLACK)
    return img
```

- [ ] **Step 5: Run to verify it passes**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests/test_clients.py -q'`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
git add byos/clients/
git commit -m "feat(byos): clients/ — default renderer registration + example group plugin"
```

---

## Task 7: `server.py` — slim FastAPI wiring over the core

**Files:**
- Modify: `byos/server.py` (rewrite body; keep shebang from Task 0)
- Create: `byos/tests/test_server.py`

- [ ] **Step 1: Write the failing test**

Create `byos/tests/test_server.py`:
```python
import importlib

from starlette.testclient import TestClient


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("BYOS_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("BYOS_INVENTORY", str(tmp_path / "inv.yaml"))
    (tmp_path / "inv.yaml").write_text(
        "groups:\n  desk:\n    members: ['44:1B:F6:81:A2:80']\n    refresh_rate: 1234\n"
    )
    import server
    importlib.reload(server)
    return TestClient(server.app)


def test_setup_returns_200_and_keys(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/api/setup", headers={"ID": "44:1B:F6:81:A2:80", "Model": "og"})
    j = r.json()
    assert j["status"] == 200
    assert j["friendly_id"] == "DEV001"
    assert j["image_url"].endswith("/current.bmp?mac=44:1B:F6:81:A2:80")


def test_display_uses_group_refresh_rate(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/api/display", headers={"ID": "44:1B:F6:81:A2:80",
                                       "Battery-Voltage": "4.05", "RSSI": "-57"})
    j = r.json()
    assert j["refresh_rate"] == 1234         # from inventory group "desk"
    assert j["full_refresh_every"] == 16     # global default (no group/renderer override)


def test_current_bmp_size(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    c.get("/api/setup", headers={"ID": "44:1B:F6:81:A2:80"})
    r = c.get("/current.bmp", params={"mac": "44:1B:F6:81:A2:80"})
    assert r.headers["content-type"] == "image/bmp"
    assert len(r.content) == 48062


def test_log_204_and_recorded(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.post("/api/log", headers={"ID": "44:1B:F6:81:A2:80"},
               json={"log_array": [{"msg": "hello world"}]})
    assert r.status_code == 204


def test_pending_full_refresh_applies_once(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    import server
    server.STATE.upsert_report(server.parse_report({"ID": "44:1B:F6:81:A2:80"}), tags=[])
    server.STATE.queue_command("44:1B:F6:81:A2:80", force_full_refresh=True)
    r1 = c.get("/api/display", headers={"ID": "44:1B:F6:81:A2:80"})
    assert r1.json()["full_refresh_every"] == 1   # consumed
    r2 = c.get("/api/display", headers={"ID": "44:1B:F6:81:A2:80"})
    assert r2.json()["full_refresh_every"] == 16  # cleared -> back to default
```

- [ ] **Step 2: Run to verify it fails**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests/test_server.py -q'`
Expected: FAIL (server has no `app`/`parse_report`/`STATE` shape yet).

- [ ] **Step 3: Rewrite `byos/server.py`** (keep lines 1-2 shebang from Task 0)

Replace everything after the module docstring with:
```python
from __future__ import annotations

import os
import sys
import datetime
from typing import Optional

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel

import registry
import render
from protocol import parse_report
from inventory import Inventory
from state import StateStore
from registry import RenderConfig, resolve, resolve_config
from render import RenderContext, default_render, to_bmp

# --- settings / globals -----------------------------------------------------
HOST = "0.0.0.0"
PORT = 8080
REFRESH_RATE = 900
FULL_REFRESH_EVERY = 16
DEFAULTS = RenderConfig(refresh_rate=REFRESH_RATE, full_refresh_every=FULL_REFRESH_EVERY,
                        special_function="none")

_DIR = os.path.dirname(os.path.abspath(__file__))
STATE = StateStore(os.environ.get("BYOS_STATE", os.path.join(_DIR, "state.json")))
INVENTORY = Inventory.load(os.environ.get("BYOS_INVENTORY", os.path.join(_DIR, "inventory.yaml")))

# Register the default + load client plugins at import time.
registry.set_default(default_render)
registry.load_clients(os.path.join(_DIR, "clients"))


# --- response models --------------------------------------------------------
class SetupResponse(BaseModel):
    status: int = 200
    api_key: str
    friendly_id: str
    image_url: str
    filename: str = "setup"
    message: str = "BYOS setup ok"


class DisplayResponse(BaseModel):
    status: int = 0
    image_url: str
    filename: str
    refresh_rate: int
    full_refresh_every: int
    special_function: str = "none"
    update_firmware: bool = False
    firmware_url: Optional[str] = None
    reset_firmware: bool = False


app = FastAPI(title="trmnl-byos", version="3.0")


def _base(request: Request) -> str:
    return f"http://{request.headers.get('host') or f'127.0.0.1:{PORT}'}"


def _resolve_tags(mac: str) -> list[str]:
    dev = STATE.get(mac)
    if dev and dev.group_override:
        return [dev.group_override]
    return INVENTORY.resolve_tags(mac)


@app.get("/api/setup")
def api_setup(request: Request):
    report = parse_report(request.headers)
    dev = STATE.upsert_report(report, _resolve_tags(report.mac))
    print(f"  setup   {dev.mac} -> {dev.friendly_id}")
    return SetupResponse(api_key=dev.api_key, friendly_id=dev.friendly_id,
                         image_url=f"{_base(request)}/current.bmp?mac={dev.mac}")


@app.get("/api/display")
def api_display(request: Request):
    report = parse_report(request.headers)
    dev = STATE.upsert_report(report, _resolve_tags(report.mac))
    reg = resolve(dev)
    pending = STATE.take_pending(dev.mac)
    pending_cfg = {}
    if pending.force_full_refresh:
        pending_cfg["full_refresh_every"] = 1
    if pending.special_function:
        pending_cfg["special_function"] = pending.special_function
    group_cfgs = [RenderConfig(refresh_rate=g.refresh_rate,
                               full_refresh_every=g.full_refresh_every,
                               special_function=g.special_function)
                  for _, g in INVENTORY.settings_for(dev.tags)]
    cfg = resolve_config(pending_cfg, group_cfgs, reg.config, DEFAULTS)
    now = datetime.datetime.now()
    print(f"  display {dev.mac} batt={dev.battery_voltage} rssi={dev.rssi} "
          f"rate={cfg.refresh_rate} fre={cfg.full_refresh_every} sf={cfg.special_function}")
    return DisplayResponse(
        image_url=f"{_base(request)}/current.bmp?mac={dev.mac}",
        filename=f"frame-{now:%Y%m%d%H%M%S}",
        refresh_rate=cfg.refresh_rate, full_refresh_every=cfg.full_refresh_every,
        special_function=cfg.special_function or "none",
    )


@app.post("/api/log")
async def api_log(request: Request):
    mac = request.headers.get("ID", "?")
    try:
        body = (await request.body()).decode("utf-8", "replace")
    except Exception:
        body = ""
    STATE.set_last_log(mac, body.strip().splitlines()[0][:300] if body.strip() else "")
    print(f"  log     {mac}: {body[:200]}")
    return Response(status_code=204)


def _render_for(mac: Optional[str]):
    dev = STATE.get(mac) if mac else STATE.latest()
    if dev is None:
        return render.blank()
    return resolve(dev).fn(RenderContext(device=dev))


@app.get("/current.bmp")
def current_bmp(request: Request):
    data = to_bmp(_render_for(request.query_params.get("mac")))
    return Response(content=data, media_type="image/bmp",
                    headers={"Content-Length": str(len(data))})


@app.head("/current.bmp")
def current_bmp_head(request: Request):
    data = to_bmp(_render_for(request.query_params.get("mac")))
    return Response(status_code=200, media_type="image/bmp",
                    headers={"Content-Length": str(len(data))})


def main() -> None:
    import uvicorn
    sys.stdout.reconfigure(line_buffering=True)
    print(f"TRMNL BYOS (FastAPI) on http://{HOST}:{PORT}  state={STATE._path}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
```

Also delete the now-unneeded old `render_frame`/`render_bmp`/`DeviceState` import
remnants from the previous `server.py` (the rewrite above replaces them entirely;
ensure no duplicate symbol definitions remain).

- [ ] **Step 4: Run to verify it passes**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests/test_server.py -q'`
Expected: PASS (5 passed).

- [ ] **Step 5: Run the full suite**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests -q'`
Expected: PASS (all tasks).

- [ ] **Step 6: Commit**

```bash
git add byos/server.py byos/tests/test_server.py
git commit -m "feat(byos): slim FastAPI server wiring core+registry+inventory; admin-ready"
```

---

## Task 8: `admin.py` — interactive operator page

**Files:**
- Create: `byos/admin.py`
- Modify: `byos/server.py` (mount admin router)
- Create: `byos/tests/test_admin.py`

- [ ] **Step 1: Write the failing test**

Create `byos/tests/test_admin.py`:
```python
import importlib

from starlette.testclient import TestClient


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("BYOS_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("BYOS_INVENTORY", str(tmp_path / "inv.yaml"))
    (tmp_path / "inv.yaml").write_text("groups:\n  garden:\n    members: ['*']\n")
    import server
    importlib.reload(server)
    return TestClient(server.app), server


def test_admin_lists_devices(tmp_path, monkeypatch):
    c, server = _client(tmp_path, monkeypatch)
    c.get("/api/display", headers={"ID": "44:1B:F6:81:A2:80", "Battery-Voltage": "4.05"})
    html = c.get("/admin").text
    assert "DEV001" in html
    assert "44:1B:F6:81:A2:80" in html
    assert "/admin/thumb/44:1B:F6:81:A2:80.png" in html


def test_admin_thumb_is_png(tmp_path, monkeypatch):
    c, server = _client(tmp_path, monkeypatch)
    c.get("/api/setup", headers={"ID": "44:1B:F6:81:A2:80"})
    r = c.get("/admin/thumb/44:1B:F6:81:A2:80.png")
    assert r.status_code == 200
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_admin_post_queues_force_full_and_group(tmp_path, monkeypatch):
    c, server = _client(tmp_path, monkeypatch)
    c.get("/api/setup", headers={"ID": "44:1B:F6:81:A2:80"})
    r = c.post("/admin/device/44:1B:F6:81:A2:80",
               data={"force_full_refresh": "on", "special_function": "identify",
                     "group": "garden"},
               follow_redirects=False)
    assert r.status_code in (302, 303)
    dev = server.STATE.get("44:1B:F6:81:A2:80")
    assert dev.group_override == "garden"
    assert dev.pending.force_full_refresh is True
    assert dev.pending.special_function == "identify"
```

- [ ] **Step 2: Run to verify it fails**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests/test_admin.py -q'`
Expected: FAIL (no /admin route).

- [ ] **Step 3: Implement `byos/admin.py`**

```python
"""Operator admin page: stats table + PNG thumbnails + per-device actions.

LAN-only, no auth. Mutations go through StateStore; actions are applied on the
device's next /api/display poll.
"""

from __future__ import annotations

import datetime
import html

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

import render
from inventory import Inventory
from registry import resolve
from render import RenderContext, to_png
from state import StateStore

SPECIAL_FUNCTIONS = ["none", "identify", "sleep", "add_wifi", "restart_playlist",
                     "rewind", "send_to_me"]


def _ago(ts: datetime.datetime) -> str:
    secs = int((datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    return f"{secs // 3600}h ago"


def build_router(state: StateStore, inventory: Inventory) -> APIRouter:
    r = APIRouter()

    @r.get("/admin", response_class=HTMLResponse)
    def admin(request: Request):
        groups = [""] + sorted(inventory.groups.keys())
        rows = []
        for d in sorted(state.all(), key=lambda d: d.friendly_id):
            pct = d.battery_percent()
            batt = "—" if d.battery_voltage is None else f"{d.battery_voltage:.2f}V" + (
                f" ({pct}%)" if pct is not None else "")
            pend = []
            if d.pending.force_full_refresh:
                pend.append("full")
            if d.pending.special_function:
                pend.append(d.pending.special_function)
            pend_badge = f" <b>⏳ {'/'.join(pend)}</b>" if pend else ""
            sf_opts = "".join(
                f"<option {'selected' if s == (d.pending.special_function or 'none') else ''}>{s}</option>"
                for s in SPECIAL_FUNCTIONS)
            grp_opts = "".join(
                f"<option value='{g}' {'selected' if g == (d.group_override or '') else ''}>"
                f"{g or '(auto)'}</option>" for g in groups)
            rows.append(f"""
<tr>
  <td>{html.escape(d.friendly_id)}</td>
  <td><code>{html.escape(d.mac)}</code></td>
  <td>{html.escape(','.join(d.tags)) or '—'}{pend_badge}</td>
  <td>{_ago(d.last_seen)}</td>
  <td>{batt}</td>
  <td>{'' if d.rssi is None else str(d.rssi)+' dBm'}</td>
  <td>{html.escape(d.wifi_ssid or '—')}</td>
  <td>{html.escape(d.fw_version or '—')}</td>
  <td>{html.escape(d.last_log or '')}</td>
  <td><img src="/admin/thumb/{html.escape(d.mac)}.png" width="200"></td>
  <td>
    <form method="post" action="/admin/device/{html.escape(d.mac)}">
      <label><input type="checkbox" name="force_full_refresh"> full refresh</label><br>
      special: <select name="special_function">{sf_opts}</select><br>
      group: <select name="group">{grp_opts}</select><br>
      <button type="submit">Submit</button>
    </form>
  </td>
</tr>""")
        body = "".join(rows) or "<tr><td colspan=11>no devices yet</td></tr>"
        return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="15"><title>TRMNL BYOS admin</title>
<style>body{{font:14px sans-serif;margin:16px}}table{{border-collapse:collapse}}
td,th{{border:1px solid #ccc;padding:6px;vertical-align:top}}img{{image-rendering:pixelated}}</style>
</head><body><h2>TRMNL BYOS — devices</h2><table>
<tr><th>id</th><th>mac</th><th>tags</th><th>seen</th><th>batt</th><th>rssi</th>
<th>wifi</th><th>fw</th><th>last log</th><th>showing</th><th>actions</th></tr>
{body}</table></body></html>"""

    @r.get("/admin/thumb/{mac}.png")
    def thumb(mac: str):
        dev = state.get(mac)
        img = resolve(dev).fn(RenderContext(device=dev)) if dev else render.blank()
        return Response(content=to_png(img), media_type="image/png")

    @r.post("/admin/device/{mac}")
    async def action(mac: str, request: Request):
        form = await request.form()
        group = form.get("group") or None
        state.set_group_override(mac, group)
        sf = form.get("special_function")
        state.queue_command(
            mac,
            force_full_refresh=bool(form.get("force_full_refresh")),
            special_function=(sf if sf and sf != "none" else None),
        )
        return RedirectResponse("/admin", status_code=303)

    return r
```

- [ ] **Step 4: Mount the router in `server.py`**

Add near the other imports in `byos/server.py`:
```python
from admin import build_router
```
And after `app = FastAPI(...)` add:
```python
app.include_router(build_router(STATE, INVENTORY))
```

- [ ] **Step 5: Run to verify it passes**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests/test_admin.py -q'`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add byos/admin.py byos/server.py byos/tests/test_admin.py
git commit -m "feat(byos): /admin page — stats, PNG thumbnails, per-device actions"
```

---

## Task 9: Full suite + manual server smoke + docs

**Files:**
- Modify: `byos/README.md`
- Modify: `HANDOFF.md`

- [ ] **Step 1: Run the entire test suite**

Run: `nix-shell byos/shell.nix --run 'PYTHONPATH=byos python -m pytest byos/tests -q'`
Expected: PASS (all tests green).

- [ ] **Step 2: Manual server smoke (real uvicorn)**

```bash
pkill -f byos/server.py 2>/dev/null; sleep 1
nohup ./byos/server.py > /tmp/byos.log 2>&1 &
sleep 2
curl -s -H "ID: 44:1B:F6:81:A2:80" -H "Battery-Voltage: 4.05" -H "RSSI: -57" \
     http://localhost:8080/api/display | python3 -m json.tool
curl -s -o /dev/null -w "admin=%{http_code}\n" http://localhost:8080/admin
curl -s "http://localhost:8080/current.bmp?mac=44:1B:F6:81:A2:80" -o /tmp/f.bmp && \
     wc -c < /tmp/f.bmp   # 48062
```
Expected: display JSON with `refresh_rate`/`full_refresh_every`/`special_function`; `admin=200`; bmp 48062 bytes.

- [ ] **Step 3: Update `byos/README.md`**

Replace the "Files" and "Customize" sections so they describe the new layout:
`protocol.py`, `state.py`, `inventory.py` + `inventory.yaml`, `registry.py`,
`render.py`, `clients/` (drop a `*.py`, register with `@renderer(match=…)`),
`admin.py` (`/admin`). Document the matcher API (`mac/tag/model/glob/predicate`),
the inventory format (groups → members + priority + refresh_rate/full_refresh_every/
special_function), and that the admin page actions apply on the next poll. Note the
new run requirement (`pyyaml`) — already in the shebang/shell.nix.

- [ ] **Step 4: Update `HANDOFF.md` §3**

Add a sentence under the server section: the server is now a pluggable core
(`protocol`→`state`→`inventory`→`registry`→`render`) with auto-loaded `byos/clients/`
plugins and an interactive `/admin` page; per-client/group behavior is configured in
`byos/inventory.yaml` and `byos/clients/*.py`. Point to the spec + this plan.

- [ ] **Step 5: Commit**

```bash
git add byos/README.md HANDOFF.md
git commit -m "docs(byos): document plugin registry, inventory, and admin page"
```

---

## Self-Review notes (verified during planning)

- **Spec coverage:** core parse (T1) ✓; state+overrides+pending (T2) ✓; inventory/tags/globs/group config (T3) ✓; render+encoders (T4) ✓; registry matchers/priority/config-precedence/load_clients (T5) ✓; default + plugin example (T6) ✓; dispatch flow + endpoints + display-config + /api/log last-line (T7) ✓; admin stats+thumb+actions (T8) ✓; docs (T9) ✓. `full_refresh_every` firmware edit remains deferred (out of scope, per spec).
- **Type consistency:** `RenderConfig`, `RegisteredRenderer.fn`, `resolve()`, `resolve_config(pending: dict, group_cfgs, renderer_cfg, defaults)`, `StateStore.upsert_report/queue_command/take_pending/set_group_override/set_last_log/get/all/latest`, `RenderContext(device=...)`, `to_bmp/to_png/blank/default_render` are used identically across tasks.
- **No placeholders:** every code step is complete and runnable.
