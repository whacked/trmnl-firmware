"""
Device state for the TRMNL BYOS server.

Every device that talks to us is tracked in a JSON file (byos/state.json) that is
*only* ever produced by Pydantic's model_dump_json() and consumed by
model_validate_json() — no hand-rolled dict munging. The in-memory ServerState is
authoritative; the file is a durable mirror written atomically.

Fields come straight from the firmware's request headers
(lib/trmnl/src/api-client/request_headers.cpp). See server.py for the mapping.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DeviceState(BaseModel):
    """One device, keyed by its MAC (the `ID` header)."""

    mac: str
    friendly_id: str
    api_key: str
    model: str | None = None
    fw_version: str | None = None
    first_seen: datetime = Field(default_factory=_now)
    last_seen: datetime = Field(default_factory=_now)

    # Reported each /api/display poll (all optional → a sparse /api/setup validates).
    battery_voltage: float | None = None
    rssi: int | None = None
    wifi_ssid: str | None = None
    refresh_rate: int | None = None
    update_source: str | None = None
    last_image_filename: str | None = None

    def battery_percent(self) -> int | None:
        """Rough LiPo state-of-charge from voltage (no fuel gauge on the OG).

        Linear 3.30 V (0%) … 4.20 V (100%); clamped. Coarse but useful.
        """
        if self.battery_voltage is None:
            return None
        pct = (self.battery_voltage - 3.30) / (4.20 - 3.30) * 100.0
        return max(0, min(100, round(pct)))


class ServerState(BaseModel):
    devices: dict[str, DeviceState] = Field(default_factory=dict)
    next_seq: int = 1  # first device → DEV001


class StateStore:
    """Thread-safe, atomically-persisted wrapper around a ServerState."""

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
        except Exception as e:  # corrupt/unreadable → start fresh, never brick
            print(f"  [state] WARNING: {self._path} unreadable ({e}); starting empty")
            return ServerState()

    def _persist_locked(self) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(self._state.model_dump_json(indent=2))
        os.replace(tmp, self._path)  # atomic; a crash mid-write can't truncate

    def upsert(self, mac: str, **fields) -> DeviceState:
        """Create-or-update the device for `mac`, set provided fields, persist."""
        with self._lock:
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
            for k, v in fields.items():
                if v is not None and hasattr(dev, k):
                    setattr(dev, k, v)
            dev.last_seen = _now()
            self._persist_locked()
            # return a copy so callers can't mutate the stored model unlocked
            return dev.model_copy(deep=True)

    def get(self, mac: str) -> DeviceState | None:
        with self._lock:
            dev = self._state.devices.get(mac)
            return dev.model_copy(deep=True) if dev else None

    def latest(self) -> DeviceState | None:
        """Most-recently-seen device (used when no specific device is requested)."""
        with self._lock:
            if not self._state.devices:
                return None
            dev = max(self._state.devices.values(), key=lambda d: d.last_seen)
            return dev.model_copy(deep=True)
