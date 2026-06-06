# TRMNL BYOS — reference server (FastAPI)

A small "Bring Your Own Server" for a TRMNL device. It speaks just enough of the
TRMNL protocol that a device pointed at it boots straight into content **you**
control — no account, no MAC registration, no "email support@" step.

The device has **no browser**: it renders 800×480 1-bit **bitmaps**, not webpages.
So this server's job is to hand the device a bitmap. To change what's shown, edit
`render_frame()` in [`server.py`](server.py).

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
- `server.py` — FastAPI app: the 4 endpoints + the `render_frame()` dashboard hook.
- `state.py` — Pydantic device state (`DeviceState`/`ServerState`/`StateStore`),
  persisted atomically to `state.json` (gitignored, runtime data).
- `shell.nix` — dev shell (pillow, fastapi, uvicorn, pydantic).

## Run it

Deps are fully nix-specified (`pillow fastapi uvicorn pydantic`), so it runs the
same on macOS and the NixOS host:

```bash
./byos/server.py                                   # nix-shell shebang, just run it
# or
nix-shell byos/shell.nix --run 'python byos/server.py'
```

Listens on `0.0.0.0:8080`.

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

Edit `render_frame(draw, img, dev)` in `server.py`. `draw` is a PIL `ImageDraw`
on an 800×480 1-bit canvas (use `BLACK`/`WHITE`); `dev` is the requesting
`DeviceState` (battery, RSSI, WiFi, fw, last-seen) or `None`.

Knobs at the top of `server.py`: `PORT`, `REFRESH_RATE`, `FULL_REFRESH_EVERY`
(server-controlled e-ink full-refresh cadence — see `HANDOFF.md §7`), `INVERT`.

## What the device already reports (read into `state.json`)
From request headers (`lib/trmnl/src/api-client/request_headers.cpp`): `ID` (MAC),
`Battery-Voltage`, `RSSI`, `WiFi-SSID`, `FW-Version`, `Model`, `Refresh-Rate`,
`Update-Source`. (X-class devices also send gauge-grade battery headers; the OG
sends voltage only — we derive a rough % from it.)

## Notes
- Device and host must share a LAN; macOS may prompt to allow port 8080.
- First `nix-shell` launch builds/fetches the env, then it's cached.
