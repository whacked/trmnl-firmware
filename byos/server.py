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
