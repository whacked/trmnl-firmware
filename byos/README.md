# TRMNL BYOS — reference server (FastAPI)

A small "Bring Your Own Server" for a TRMNL device. It speaks just enough of the
TRMNL protocol that a device pointed at it boots straight into content **you**
control — no account, no MAC registration, no "email support@" step.

The device has **no browser**: it renders 800×480 1-bit **bitmaps**, not webpages.
So this server's job is to hand the device a bitmap. Drop a `.py` file in
`byos/clients/` and register a renderer with `@renderer(match=…)` to control what
each device (or group of devices) sees.

## How it fits together

```
device boot → WiFi (captive portal, one-time)
            → GET  /api/setup    (returns a key + friendly_id for ANY device)
            → GET  /api/display  (says "show /current.bmp?mac=…", how often, cadence)
            → GET  /current.bmp  (800×480 1-bit BMP, rendered live per device)
            → POST /api/log      (device uploads logs; we accept + record)
            → deep sleep refresh_rate seconds → repeat
```

### Pointing the device here
Two ways (no hardcoded IP in source — see `include/byos_config.h`):

1. **Runtime (preferred):** type your server URL (e.g. `http://192.168.1.107:8080`)
   into the **server field on the device's WiFi setup portal**. Saved to NVS,
   overrides the compile default. No reflash to move the server.
2. **Build time:** `PLATFORMIO_BUILD_FLAGS='-D BYOS_SERVER_URL=\"http://IP:8080\"'`.

## Files

Core modules (loaded in pipeline order):

| File | Role |
|---|---|
| `protocol.py` | Parses firmware request headers (case-insensitive) into a typed `DeviceReport`. The only file that knows raw header names. |
| `state.py` | `StateStore` — thread-safe, atomically-persisted `state.json`; tracks every device by MAC with telemetry, `friendly_id`, tags, group override, and one-shot pending commands. |
| `inventory.py` + `inventory.yaml` | Ansible-style inventory: maps MACs (exact or glob) to named groups with per-group display config. Loaded at startup; see format below. |
| `registry.py` | Matcher API + `@renderer` decorator + `resolve()` + `resolve_config()` + `load_clients()`. Matches a device to the highest-priority registered renderer and resolves the display config (pending > group > renderer > global default). |
| `render.py` | `RenderContext`, `default_render()`, `to_bmp()`, `to_png()`, `blank()`. The default renderer draws a clock/date + per-device stats. |
| `clients/` | Auto-loaded renderer plugins. Drop a `.py` here; it's imported at startup so its `@renderer(…)` decorators register. `clients/default.py` registers the fallback; `clients/example_group.py` shows a tagged-group plugin. |
| `admin.py` | Serves `/admin` — an auto-refreshing table of all devices with battery/RSSI/tags/pending state, PNG thumbnails, and a per-device action form. Queued actions apply on the device's next `/api/display` poll. |
| `server.py` | Thin FastAPI wiring: `GET /api/setup`, `GET /api/display`, `POST /api/log`, `GET /current.bmp`, `HEAD /current.bmp`. Mounts the admin router. |
| `shell.nix` | Dev shell (pillow, fastapi, uvicorn, pydantic, pyyaml, pytest, httpx). |

Runtime data (`state.json`) is gitignored.

## Run it

Deps are fully nix-specified (`pillow fastapi uvicorn pydantic pyyaml`) in both the
shebang and `shell.nix`, so it runs the same on macOS and the NixOS host:

```bash
./byos/server.py                                   # nix-shell shebang, just run it
# or
nix-shell byos/shell.nix --run 'python byos/server.py'
```

Listens on `0.0.0.0:8080`. Admin page at `http://localhost:8080/admin`.

## Verify without a device

```bash
ID='-H "ID: AA:BB:CC:DD:EE:FF"'
curl -s -H "ID: AA:BB:CC:DD:EE:FF" http://localhost:8080/api/setup | python3 -m json.tool
curl -s -H "ID: AA:BB:CC:DD:EE:FF" -H "Battery-Voltage: 4.05" -H "RSSI: -57" \
     http://localhost:8080/api/display | python3 -m json.tool        # full_refresh_every: 16
curl -s -X POST -H "ID: AA:BB:CC:DD:EE:FF" -d '{"log":"hi"}' \
     -o /dev/null -w "%{http_code}\n" http://localhost:8080/api/log   # -> 204
curl -s "http://localhost:8080/current.bmp?mac=AA:BB:CC:DD:EE:FF" -o /tmp/f.bmp && \
     ls -l /tmp/f.bmp && head -c2 /tmp/f.bmp                          # 48062 bytes, "BM"
cat byos/state.json                                                   # device recorded
```

## Customize

### Per-device/group renderer plugins

Drop a `.py` in `byos/clients/`. At startup `registry.load_clients()` imports every
non-`_` file there, so your `@renderer(…)` decorators run automatically.

```python
# byos/clients/my_device.py
from registry import renderer, mac, tag, model, glob, predicate
from render import default_render, font, BLACK
from PIL import ImageDraw

@renderer(match=tag("garden"), priority=10, refresh_rate=3600)
def garden_render(ctx):
    img = default_render(ctx)          # extend the default frame
    draw = ImageDraw.Draw(img)
    draw.text((44, 430), "garden view", font=font(22), fill=BLACK)
    return img
```

**Matcher API** (all case-insensitive on MAC/model strings):

| Matcher | Matches when |
|---|---|
| `mac("AA:BB:…")` | exact MAC |
| `tag("name")` | device is in the named inventory group |
| `model("og")` | `Model` header equals this string |
| `glob("44:1B:*")` | MAC matches the fnmatch glob |
| `predicate(fn)` | arbitrary `fn(device) -> bool` |

When a device matches multiple renderers, the one with the highest `priority` wins
(ties → first registered). Unmatched devices fall back to the `default_render`.

### `inventory.yaml` — groups & display config

```yaml
groups:
  garden:
    members: ["A0:EA:DE:AD:BE:EF", "DC:44:*"]  # exact MACs or globs
    priority: 5               # higher priority wins when a device is in several groups
    refresh_rate: 3600        # overrides global REFRESH_RATE for these devices
    full_refresh_every: 32    # e-ink full-refresh cadence
    special_function: null    # identify/sleep/add_wifi/restart_playlist/rewind/…
  all_catch:
    members: ["*"]
    priority: -10
```

Display config precedence (per field): **pending admin command > group setting >
renderer `@renderer(…)` kwarg > global default** (`REFRESH_RATE`, `FULL_REFRESH_EVERY`
at the top of `server.py`).

### Admin page (`/admin`)

`http://localhost:8080/admin` shows a live table of all devices with battery/RSSI/
tags/last-log and a PNG thumbnail of what each device is currently showing. The
action form lets you:
- Queue a **force full refresh** (applied on the device's next `/api/display` poll).
- Queue a **special function** (`identify`, `sleep`, etc.).
- Set or clear a **group override** (overrides the inventory match for that device).

Queued commands are one-shot: consumed and cleared on the next poll.

### Global knobs

At the top of `server.py`: `PORT`, `REFRESH_RATE`, `FULL_REFRESH_EVERY`
(server-controlled e-ink full-refresh cadence — see `HANDOFF.md §7`), and `INVERT`
in `render.py`.

## What the device already reports (read into `state.json`)
From request headers (`lib/trmnl/src/api-client/request_headers.cpp`): `ID` (MAC),
`Battery-Voltage`, `RSSI`, `WiFi-SSID`, `FW-Version`, `Model`, `Refresh-Rate`,
`Update-Source`. (X-class devices also send gauge-grade battery headers; the OG
sends voltage only — we derive a rough % from it.)

## Notes
- Device and host must share a LAN; macOS may prompt to allow port 8080.
- First `nix-shell` launch builds/fetches the env, then it's cached.
