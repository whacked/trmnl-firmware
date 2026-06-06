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
