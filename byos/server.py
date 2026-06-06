#!/usr/bin/env nix-shell
#!nix-shell -i python3 -p "python3.withPackages(ps: with ps; [ pillow fastapi uvicorn pydantic pyyaml ])"
"""
TRMNL BYOS (Bring Your Own Server) — FastAPI reference server.

Speaks just enough of the TRMNL protocol that a device pointed here (via the
portal's server field, or the compiled-in BYOS_SERVER_URL) boots straight into
content WE control — no account, no MAC registration, no "email support@" step.

The Pydantic response models below ARE the spec for what the firmware consumes;
each field is annotated with its firmware consumer. The device renders BITMAPS
only (no HTML) — to change what shows, edit render_frame().

Endpoints (firmware code that calls each):
  GET  /api/setup    setup.cpp        -> SetupResponse   (status MUST be 200)
  GET  /api/display  display.cpp      -> DisplayResponse
  POST /api/log      submit_log.cpp   -> 204 (accept anything; never error)
  GET  /current.bmp  image fetch      -> 800x480 1-bit BMP (48062 bytes)

Run it:
  ./byos/server.py                         (nix-shell shebang provides all deps)
  nix-shell byos/shell.nix --run 'python byos/server.py'
"""

from __future__ import annotations

import io
import os
import sys
import datetime
from typing import Optional

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel
from PIL import Image, ImageDraw, ImageFont

from state import StateStore, DeviceState

# ---------------------------------------------------------------------------
# Settings (module-level constants; the only knobs)
# ---------------------------------------------------------------------------
HOST = "0.0.0.0"
PORT = 8080

# E-ink panel is exactly 800x480, 1 bit-per-pixel. Don't change unless the
# firmware's bmp.cpp validator changes too.
WIDTH, HEIGHT = 800, 480

# Seconds the device deep-sleeps between /api/display polls (server-controlled).
REFRESH_RATE = 900

# Do a FULL (flashing) e-ink refresh every Nth update; PARTIAL (silent) between.
# Emitted as `full_refresh_every`. The firmware honours it only once the (specced,
# currently-deferred) 3-line firmware edit ships; until then the device uses its
# built-in default (8) and ignores this field — harmless.
FULL_REFRESH_EVERY = 16

# Flip if the panel renders inverted.
INVERT = False

WHITE, BLACK = 255, 0

STATE = StateStore(os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json"))


# ---------------------------------------------------------------------------
# Response models = the server-side spec (field -> firmware consumer)
# ---------------------------------------------------------------------------
class SetupResponse(BaseModel):
    status: int = 200            # parse_response_api_setup.cpp REQUIRES 200
    api_key: str                 # saved to NVS; sent back as Access-Token
    friendly_id: str             # saved to NVS; shown on device
    image_url: str               # first image to show
    filename: str = "setup"
    message: str = "BYOS setup ok"


class DisplayResponse(BaseModel):
    status: int = 0                      # 0 = normal content
    image_url: str                       # image to download + show
    filename: str                        # cache id / log label
    refresh_rate: int = REFRESH_RATE     # deep-sleep seconds + refresh-mode lever
    full_refresh_every: int = FULL_REFRESH_EVERY  # FULL-refresh cadence (see above)
    update_firmware: bool = False
    firmware_url: Optional[str] = None
    reset_firmware: bool = False
    special_function: str = "none"


# ---------------------------------------------------------------------------
# Dashboard — the customization hook. `dev` is the requesting device (or None).
# `draw` is a PIL.ImageDraw on an 800x480 1-bit canvas; use BLACK / WHITE.
# ---------------------------------------------------------------------------
def _font(size: int):
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


def render_frame(draw: "ImageDraw.ImageDraw", img: "Image.Image", dev: Optional[DeviceState]) -> None:
    now = datetime.datetime.now()

    # Frame border
    draw.rectangle([4, 4, WIDTH - 5, HEIGHT - 5], outline=BLACK, width=3)

    # Header
    draw.text((40, 34), "TRMNL BYOS", font=_font(64), fill=BLACK)
    draw.text((42, 110), "your own server", font=_font(30), fill=BLACK)
    draw.line([40, 156, WIDTH - 40, 156], fill=BLACK, width=2)

    # Big live clock — proves the frame is freshly rendered each poll.
    draw.text((40, 180), now.strftime("%H:%M"), font=_font(150), fill=BLACK)
    draw.text((44, 350), now.strftime("%A, %d %B %Y"), font=_font(34), fill=BLACK)

    # Device status panel (right side) — from the headers the device reports.
    x = 448
    if dev is not None:
        pct = dev.battery_percent()
        batt = "—"
        if dev.battery_voltage is not None:
            batt = f"{dev.battery_voltage:.2f} V"
            if pct is not None:
                batt += f"  ({pct}%)"
        rows = [
            ("Device", dev.friendly_id),
            ("MAC", dev.mac),
            ("Battery", batt),
            ("WiFi", (dev.wifi_ssid or "—")),
            ("RSSI", (f"{dev.rssi} dBm" if dev.rssi is not None else "—")),
            ("FW", (dev.fw_version or "—")),
            ("Seen", dev.last_seen.astimezone().strftime("%H:%M:%S")),
        ]
    else:
        rows = [("Device", "waiting for first poll…")]

    y = 184
    for label, value in rows:
        draw.text((x, y), f"{label}:", font=_font(20), fill=BLACK)
        draw.text((x + 100, y), str(value), font=_font(20), fill=BLACK)
        y += 38


def render_bmp(dev: Optional[DeviceState]) -> bytes:
    img = Image.new("1", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)
    render_frame(draw, img, dev)
    if INVERT:
        img = img.point(lambda p: WHITE if p == BLACK else BLACK)
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------
app = FastAPI(title="trmnl-byos", version="2.0")


def _base(request: Request) -> str:
    host = request.headers.get("host") or f"127.0.0.1:{PORT}"
    return f"http://{host}"


def _int(request: Request, name: str) -> Optional[int]:
    v = request.headers.get(name)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def _float(request: Request, name: str) -> Optional[float]:
    v = request.headers.get(name)
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


@app.get("/api/setup")
def api_setup(request: Request):
    mac = request.headers.get("ID", "00:00:00:00:00:00")
    dev = STATE.upsert(
        mac,
        fw_version=request.headers.get("FW-Version"),
        model=request.headers.get("Model"),
    )
    print(f"  setup   {mac} -> {dev.friendly_id}")
    return SetupResponse(
        api_key=dev.api_key,
        friendly_id=dev.friendly_id,
        image_url=f"{_base(request)}/current.bmp?mac={mac}",
    )


@app.get("/api/display")
def api_display(request: Request):
    mac = request.headers.get("ID", "00:00:00:00:00:00")
    dev = STATE.upsert(
        mac,
        fw_version=request.headers.get("FW-Version"),
        model=request.headers.get("Model"),
        battery_voltage=_float(request, "Battery-Voltage"),
        rssi=_int(request, "RSSI"),
        wifi_ssid=request.headers.get("WiFi-SSID"),
        refresh_rate=_int(request, "Refresh-Rate"),
        update_source=request.headers.get("Update-Source"),
    )
    now = datetime.datetime.now()
    print(f"  display {mac} batt={dev.battery_voltage} rssi={dev.rssi} src={dev.update_source}")
    return DisplayResponse(
        image_url=f"{_base(request)}/current.bmp?mac={mac}",
        filename=f"frame-{now:%Y%m%d%H%M%S}",
    )


@app.post("/api/log")
async def api_log(request: Request):
    mac = request.headers.get("ID", "?")
    try:
        body = (await request.body()).decode("utf-8", "replace")
    except Exception:
        body = "<unreadable>"
    STATE.upsert(mac)  # just touch last_seen
    print(f"  log     {mac}: {body[:300]}")
    return Response(status_code=204)  # accept anything; never 4xx/5xx on a log


def _bmp_response(request: Request, head: bool) -> Response:
    mac = request.query_params.get("mac")
    dev = STATE.get(mac) if mac else STATE.latest()
    data = render_bmp(dev)
    headers = {"Content-Length": str(len(data))}
    if head:
        return Response(status_code=200, media_type="image/bmp", headers=headers)
    return Response(content=data, media_type="image/bmp", headers=headers)


@app.get("/current.bmp")
def current_bmp(request: Request):
    return _bmp_response(request, head=False)


@app.head("/current.bmp")
def current_bmp_head(request: Request):
    return _bmp_response(request, head=True)


def main() -> None:
    import uvicorn

    sys.stdout.reconfigure(line_buffering=True)
    print(f"TRMNL BYOS (FastAPI) on http://{HOST}:{PORT}")
    print("  GET /api/setup   GET /api/display   POST /api/log   GET /current.bmp")
    print(f"  refresh_rate={REFRESH_RATE}s  full_refresh_every={FULL_REFRESH_EVERY}  state={STATE._path}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
