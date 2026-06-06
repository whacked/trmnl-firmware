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
