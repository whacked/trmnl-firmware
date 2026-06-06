# BYOS: Boot straight into your own content (no activation)

**Date:** 2026-06-04
**Target hardware:** plain ESP32 + Waveshare e-paper driver (`waveshare-esp32-driver` env), 800×480 1-bit e-ink panel.

## Problem

The stock firmware boots → connects WiFi → POSTs the device MAC to `https://trmnl.app/api/setup`.
If the MAC is not pre-registered on TRMNL's server, the device shows a "MAC not registered"
screen and sleeps — the user must email support@ to get registered. After activation it GETs
`/api/display`, which returns an `image_url` to a server-rendered **bitmap** that it downloads
and shows.

The device has **no web browser**: it only renders 800×480 1-bit BMP (or PNG) images. "Loading a
webpage" is not possible on-device.

## Goal

Run our **own** server (BYOS — Bring Your Own Server) that speaks the same protocol but registers
any device automatically, so the firmware boots straight into content we control — no support@,
no TRMNL account. Provide a minimal end-to-end reference server.

## Design

### Key insight
The server base URL is already configurable in firmware
([`include/config.h:179`](../../../include/config.h#L179) `#define API_BASE_URL`, overridable via
NVS key `PREFERENCES_API_URL`). The HTTP layer
([`lib/trmnl/include/http_client.h:30`](../../../lib/trmnl/include/http_client.h#L30)) selects the
client by URL scheme: `http://` → plain `WiFiClient` (no TLS), `https://` → `WiFiClientSecure`
with `setInsecure()`. So a **plain-HTTP LAN server needs no certificates**. Because our server
answers `/api/setup` with a valid key for any MAC, **no activation code has to be removed** — the
existing boot flow simply succeeds against our server.

### Component 1 — Firmware: redirect the base URL (easy to find / change)

New file **`include/byos_config.h`**: a small, obviously-named header holding exactly one knob,
with a comment block explaining it. `config.h` includes it and defines `API_BASE_URL` from it.

```c
// include/byos_config.h
// ============================================================
//  BYOS server URL — point the device at YOUR server.
//  Change this one line, then rebuild + reflash.
//  Reference server lives in  byos/server.py
//  Use http:// for a plain LAN server (no TLS needed).
// ============================================================
#define BYOS_SERVER_URL "http://192.168.1.107:8080"
```

`config.h` change (around line 179):
```c
#include "byos_config.h"
#define API_BASE_URL BYOS_SERVER_URL   // was "https://trmnl.app"
```

Rationale: one named file, findable by filename alone; `config.h` stays the single source of the
`API_BASE_URL` symbol so nothing else in the codebase changes. Hardcoding (vs. a captive-portal
field) chosen for simplicity per user; trade-off is a reflash to change servers.

### Component 2 — Reference server `byos/server.py` (nix-shell launch + Pillow)

Python 3 `http.server` for the HTTP layer; **Pillow** for rendering. Dependencies are provided by a
**`nix-shell` shebang launcher** (no global install, no `uv`/venv) — `nix-shell` is available and a
`nixpkgs` channel is configured. The script header:

```python
#!/usr/bin/env nix-shell
#!nix-shell -i python3 -p "python3.withPackages(ps: with ps; [ pillow ])"
```

Launch with `./byos/server.py` (after `chmod +x`). First run builds/fetches the env (cached
afterwards). A `byos/shell.nix` is also provided so `nix-shell byos/shell.nix --run 'python server.py'`
works, and `byos/README.md` documents both this and the `uv` alternative
(`nix-shell -p uv --run 'uv venv && uv pip install pillow && uv run server.py'`) for users who prefer it.
Listens on `0.0.0.0:8080`.

Endpoints (mirroring the firmware's expectations):

- `GET|POST /api/setup` → `200`
  ```json
  {"status":200,"api_key":"local-dev","friendly_id":"DEV001",
   "image_url":"http://<host>:8080/current.bmp","filename":"setup"}
  ```
- `GET /api/display` → `200`
  ```json
  {"status":0,"image_url":"http://<host>:8080/current.bmp","filename":"frame",
   "refresh_rate":900,"update_firmware":false,"firmware_url":null,
   "reset_firmware":false,"special_function":"none"}
  ```
  `status:0` = normal. `refresh_rate` in seconds (sleep between refreshes).
- `GET /current.bmp` → an **800×480, 1-bit, 2-color BMP** generated on the fly, matching the strict
  validator in [`lib/trmnl/src/bmp.cpp:29`](../../../lib/trmnl/src/bmp.cpp#L29)
  (`width==800 && height==480 && bpp==1 && imageDataSize==48000 && palette==2`).

`<host>` is derived from the request `Host` header so the URLs always resolve from the device.

Rendering (Pillow):
- `Image.new("1", (800, 480), 1)` (1-bit, white background). Pillow saves mode `"1"` BMP as a
  62-byte-offset, 1bpp, 2-color-palette, 48000-byte-data file — exactly what the validator wants
  (total 48062 bytes). Color polarity (0=black/255=white) is adjustable in one place if inverted on panel.
- **`render_frame(draw, img)`** — the single customization hook, receives a PIL `ImageDraw`. Default
  draws a border, a "TRMNL BYOS" title, and the current timestamp so refreshes are visibly live.
  Users edit this one function (full PIL text/shape API) to draw "custom stuff."

### Data flow
```
boot → WiFi (captive portal, one-time) → POST /api/setup (our server, returns key)
     → GET /api/display (our server) → GET /current.bmp → render on e-ink
     → deep sleep refresh_rate seconds → repeat
```

## Verification

1. **Server, before flashing:** `./byos/server.py` (or `nix-shell byos/shell.nix --run 'python server.py'`), then
   - `curl -s http://localhost:8080/api/setup` → JSON with `status:200`.
   - `curl -s http://localhost:8080/api/display` → JSON with `status:0`.
   - `curl -s http://localhost:8080/current.bmp | head -c2` → `BM`; total size 48062 bytes;
     bytes 18-25 decode to width 800, height 480.
2. **Firmware:** set `BYOS_SERVER_URL` to `http://192.168.1.107:8080`, build + flash
   `waveshare-esp32-driver`, then `pio device monitor -b 115200` and confirm the log shows:
   `baseUrl from preferences: http://192.168.1.107:8080`, setup HTTP 200, display HTTP 200,
   `BMP Header Information ... Width: 800 Height: 480`, and the panel renders the frame.

## Out of scope (YAGNI)
- Captive-portal field for the URL (hardcode for now).
- HTTPS/TLS on the server (plain HTTP on LAN).
- Dynamic plugins / multi-device / persistence (single static-ish frame).
- HTML-to-image rendering of arbitrary webpages.

## Risks / notes
- Device and server must share the LAN; if the host IP changes, update `byos_config.h` and reflash.
- macOS firewall may prompt to allow incoming connections on port 8080 — must allow.
- First `nix-shell` launch builds/fetches the Python+Pillow env (one-time, then cached).
- PNG is also accepted by firmware, but 1-bit BMP is the strictly-validated native path, so we use BMP.
