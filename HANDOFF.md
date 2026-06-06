# TRMNL BYOS — Handoff

Goal: make a TRMNL 7.5" device boot straight into content from **our own server**
(no account, no "MAC not registered – email support@" activation), and iterate on
the server into a real dashboard.

**Current status: working end-to-end.** The device boots, joins WiFi, talks to our
server, and renders our image. No activation step.

---

## 1. Background & hard-won learnings (read this first)

### The hardware is TWO boards; only one drives the panel
The kit is the **Seeed "TRMNL 7.5" OG DIY Kit"** — a *XIAO ESP32-S3 Plus* on a
XIAO ePaper dev board. The e-paper panel is wired to the **XIAO ESP32-S3**.

During the first long debugging session we were flashing and probing a **different,
unrelated board**: a classic **ESP32-D0WDQ6 + CH340** (enumerates as
`/dev/cu.usbserial-XXXX`). That board had **no panel attached**. Every "the display
won't refresh / BUSY is stuck / panel never responds" symptom was simply *nothing
connected to the pins we were driving*. Hours of pin/controller/power theories
(BUSY=GPIO22, wrong panel type, power-enable sweeps, FPC reseating) were all chasing
a phantom. **Lesson: confirm which physical board the panel is on, and which USB
device is which, before touching firmware.** A floating BUSY line that never changes
across reseats = you're probably driving a board with no panel.

### The two boards, by USB signature
| Board | Chip | USB | macOS port | Has panel? |
|---|---|---|---|---|
| Stray classic ESP32 | ESP32-D0WDQ6 (dual-core) | CH340 (VID `1a86`) | `/dev/cu.usbserial-*` | **No** |
| The actual TRMNL | XIAO ESP32-S3 (native USB) | Espressif JTAG/serial (VID `303a`) | `/dev/cu.usbmodem*` | **Yes** |

If only `usbserial-*` shows up, the XIAO is asleep or not in bootloader mode (see
flashing below). A XIAO in deep sleep drops its native USB, so the port disappears.

### Once on the right board, it worked first try
Flashing the correct env to the XIAO drew immediately. No pin/controller mystery —
the kit's pins were already correct in the repo.

---

## 2. Build & flash (the correct board)

- **PlatformIO env:** `TRMNL_7inch5_OG_DIY_Kit` (board `seeed_xiao_esp32s3`,
  `-D BOARD_XIAO_EPAPER_DISPLAY`).
- **EPD pins** (already correct in `src/DEV_Config.h`, `BOARD_XIAO_EPAPER_DISPLAY`):
  SCK=7, MOSI=9, CS=44, RST=38, DC=10, **BUSY=4**. Panel controller = UC8179
  (`EP75_800x480`, `BBEP_CHIP_UC81xx`).
- **`pio` is not on PATH**; use `~/.platformio/penv/bin/pio`. The kit env's
  `post_build_seeed.py` calls `pio`, so prepend it to PATH for builds:

```bash
cd <repo>
PATH="$HOME/.platformio/penv/bin:$PATH" \
PLATFORMIO_BUILD_FLAGS="-D DEV_FIRMWARE -D ARDUINO_USB_MODE=1 -D ARDUINO_USB_CDC_ON_BOOT=1" \
~/.platformio/penv/bin/pio run -e TRMNL_7inch5_OG_DIY_Kit -t upload --upload-port /dev/cu.usbmodemXXXX
```

- `DEV_FIRMWARE` = enables serial logging (gates `Serial.begin`/`Log.begin` in
  `bl.cpp`). Without it, **no app logs**, even though the firmware runs.
- `ARDUINO_USB_MODE=1` + `ARDUINO_USB_CDC_ON_BOOT=1` = logs over the XIAO's native
  USB CDC. Omit all three flags for a pure production build.
- **Enter flashing mode (XIAO ESP32-S3):** board OFF (power switch down) → **hold
  BOOT** → switch ON → release BOOT. It then enumerates as
  `Espressif USB JTAG/serial debug unit` → `/dev/cu.usbmodem*`. A plain BOOT+RESET
  without the power switch will not do it if the board is off.
- **Serial capture gotcha:** the native USB CDC drops on reset/deep-sleep, so the
  `DTR/RTS` reset-then-read trick is unreliable here; just open the port and read,
  and watch the panel as ground truth. NVS (WiFi creds, api_key) **survives
  reflash** — esptool does not erase the nvs partition.

### Current device state
- Flashed with the kit firmware + our BYOS config. WiFi `huanghome` onboarded via
  the captive portal (SSID `TRMNL-81A280`, open, portal at `http://192.168.4.1`).
- Pulls and displays our server image. `refresh_rate` currently 900 s (15 min).
- **Pending (deferred by user):** one clean reflash to drop the boot-time
  black/white **test pattern** that was used to prove control. The repo is already
  clean of it (see §7) — just needs a final upload when convenient.

---

## 3. The BYOS server (this repo: `byos/`)

`byos/server.py` — **FastAPI** TRMNL-protocol server (`/api/setup`, `/api/display`,
`POST /api/log`, `/current.bmp`). `byos/state.py` — Pydantic device state persisted
atomically to `byos/state.json` (gitignored): every device tracked by MAC with the
headers it reports (battery, RSSI, WiFi, fw, last-seen) and an issued
`friendly_id`/`api_key`. Deps are fully nix-specified (`pillow fastapi uvicorn
pydantic`). Run: `./byos/server.py` or `nix-shell byos/shell.nix --run 'python
byos/server.py'`.

**Pointing the device here** (no hardcoded IP): enter the URL in the WiFi portal's
server field (runtime, saved to NVS `api_url`), or build with
`-D BYOS_SERVER_URL=\"http://host:8080\"`. The compile default is stock
`https://trmnl.app` (`include/byos_config.h`).

Customization hook: `render_frame(draw, img, dev)` in `server.py` draws the 800×480
1-bit frame — v1 shows a clock/date + the requesting device's stats.

---

## 4. Client ↔ server communication method

Plain HTTP (no TLS needed on a LAN; `http_client.h` picks `WiFiClient` for `http://`,
`WiFiClientSecure+setInsecure` for `https://`). The device is **pull-only** and
renders **bitmaps only** — no HTML/JS on the device. Flow:

1. **`GET /api/setup`** (only until it has an api_key). Server returns JSON:
   `status, api_key, friendly_id, image_url, filename, message`. A 404 here is the
   stock cloud's "not registered" gate — our server always returns 200, which is
   what removes the activation step.
2. **`GET /api/display`** every cycle. Server returns the JSON below; device acts on
   it, downloads `image_url`, shows it, then **deep-sleeps `refresh_rate` seconds**.
3. **`GET <image_url>`** (e.g. `/current.bmp`) — an 800×480 **1-bit** image. Validator
   (`bmp.cpp`) requires exactly: width 800, height 480, 1 bpp, 2-color table,
   48000-byte data / 48062-byte file. PNG (1-bit) is also accepted.

### Response fields the firmware honors (`/api/display`)
Parsed in `lib/trmnl/src/parse_response_api_display.cpp` → `ApiDisplayResponse`:

| Field | Effect on device |
|---|---|
| `image_url` | Image to download/show (empty ⇒ keep cached). |
| `filename` | ID/log only. |
| `refresh_rate` (s) | Deep-sleep duration **and** a refresh-mode lever (≥1800 s ⇒ FAST, see §7). |
| `full_refresh_every` (int) | FULL-refresh cadence (every Nth update). Server emits 16; **firmware honours it only after the deferred Part 3 edit ships** — until then firmware uses its built-in 8 and ignores this. |
| `maximum_compatibility` (bool) | `true` ⇒ **force FULL refresh every update** (max flashing). Default false. |
| `temperature_profile` (`"a"`/`"b"`/absent) | Picks LUT profile 1/2/0 → speed vs ghosting (see §7). Persisted in flash. |
| `special_function` | `identify/sleep/add_wifi/restart_playlist/rewind/send_to_me/guest_mode`. |
| `update_firmware` + `firmware_url` | OTA. |
| `reset_firmware` | Factory reset. |
| `image_url_timeout`, `action`, `touchbar_mode` | Stored; situational. |

So the **server already controls**: which image, how often, refresh mode (via
`refresh_rate`/`maximum_compatibility`/`temperature_profile`), OTA, and special
functions. Our `server.py` currently only sets `status/image_url/filename/
refresh_rate` — everything else is default.

---

## 5. What the client already sends (and what "add client-id + battery" means)

Headers are centralized (commit #384) in
`lib/trmnl/src/api-client/request_headers.cpp` (`buildDisplayHeaders`,
`buildSetupHeaders`, `buildLogHeaders`), populated by `loadApiDisplayInputs()` in
`bl.cpp`.

**`/api/display` already sends** (so the server just has to *read* them — no firmware
change needed for the two you asked about):
- **Client ID:** `ID` = MAC address (every request). Plus `friendly_id` is issued by
  our `/api/setup`. → use `ID` as the stable per-device key.
- **Battery:** `Battery-Voltage` (volts, float) — sent on **all** models incl. our OG.
- Also: `Access-Token` (api_key), `FW-Version`, `Model`, `Refresh-Rate`,
  `Update-Source` (wake reason), `RSSI`, `WiFi-Band`, `WiFi-SSID`, `Width`, `Height`,
  `Image-Cached`, `Wake-Time`, `Temperature-Profile`.
- **TRMNL X only** (not our OG): `Percent-Charged`, `Battery-Health`,
  `Battery-Current`, `Battery-Temp`, `Battery-Capacity`, `Battery-Count`,
  `Battery-Charging`, `USB-Connected`.

**`/api/setup` sends:** `ID`, `FW-Version`, `Model`, `Content-Type`.

> Action for the server: parse `ID` and `Battery-Voltage` from the request headers and
> store/track per device. On the OG there is **no `Percent-Charged`** — derive a
> rough % from `Battery-Voltage` (LiPo ~3.3 V empty … ~4.2 V full) if you want a
> percentage. If you truly need gauge-grade %/health/current, that's an X-class
> feature; adding it to OG would require firmware work + a fuel-gauge IC the OG lacks.

---

## 6. Server / dashboard / layout options

The device only needs a URL returning an 800×480 1-bit image, so "the server" splits
cleanly into **(a) the protocol shim** (3 endpoints — trivial, already done) and
**(b) the image generator** (the real work: a dashboard/layout). You can keep our
tiny protocol server and swap only the renderer, or adopt a full off-the-shelf BYOS.

### Off-the-shelf BYOS servers
| Project | Stack | Layout system | Weight | Fit for Pi Zero / Synology ARM |
|---|---|---|---|---|
| **Terminus** (`usetrmnl/terminus`, aka `byos_hanami`) — flagship | Ruby/Hanami + Postgres + Sidekiq + Valkey + ImageMagick, Docker | Full plugin/dashboard ecosystem (HTML + Liquid, 300+ plugins, layouts) | **Heavy** | Synology *with Docker*: OK. **Pi Zero: no** (too heavy). |
| **byos_node_lite** (`usetrmnl/byos_node_lite`) | Node.js | JSX/React **or** HTML+Liquid → image via **Satori** (no browser) | **Light–medium** | Synology (Node/Docker): good. Pi Zero: marginal but plausible (Satori needs no Chrome). |
| **byos_sinatra** (`usetrmnl/byos_sinatra`) | Ruby/Sinatra | DIY screen gen | Light | Either, if you want Ruby. |
| **LaraPaper** | PHP/Laravel | Laravel views | Medium | Synology w/ PHP; not Zero-friendly. |
| **trmnl-rs** (`tsangha/trmnl-rs`) | Rust | BYOS framework | Light, static binary | Great on both — but smaller community. |
| **ohAnd/trmnlServer** | serves your own BMPs | none (you supply images) | Tiny | Either; no layout engine. |

(Sources: <https://docs.trmnl.com/go/diy/byos>, <https://github.com/usetrmnl/terminus>,
<https://github.com/usetrmnl/byos_node_lite>, <https://github.com/usetrmnl/byos_sinatra>,
<https://github.com/tsangha/trmnl-rs>.) Note `usetrmnl/trmnl-display` is a **Go client**
(renders to a Linux framebuffer/e-paper), *not* a server — not what we need.

### The layout/rendering question (800×480, 1-bit)
TRMNL's model is HTML/CSS + Liquid → rasterize → 1-bit. Rasterizer choices, by host cost:
- **Headless Chromium / Puppeteer / chromedp**: best fidelity, **needs Chrome** →
  fine on Synology/Pi 4, **too heavy for Pi Zero**.
- **Satori** (JS: HTML/JSX → SVG → PNG, *no browser*): the sweet spot for "HTML-ish
  layout without Chrome". This is what `byos_node_lite` uses. Runs on Zero (slowly).
- **wkhtmltoimage**: WebKit, lighter than Chrome, ARM builds exist but it's stale.
- **Pure-Go drawing** (`fogleman/gg` + `golang.org/x/image/font`): no HTML — you place
  text/shapes/widgets yourself. Tiny **static binary**, runs anywhere incl. Zero.

### Recommendation for your constraints (Pi Zero or Synology ARM, prefer static binary / Go)
- **If you want off-the-shelf + real dashboard and will run on Synology with Docker:**
  use **Terminus**. Maximum effectiveness, zero rendering code to write.
- **If you want lightweight + HTML-style layouts on either host:** **byos_node_lite**
  (Satori). Good complexity/effectiveness, no Chrome.
- **If you want the static-binary / Pi-Zero path (your stated preference):** **write a
  small Go service** = our 3-endpoint shim + a renderer using `fogleman/gg`. You build
  a simple grid/widget layout (clock, weather, calendar, battery, etc.) drawn directly
  to a 1-bit BMP. Highest control + perf, single cross-compiled binary
  (`GOARCH=arm`/`arm64`), trivial on Pi Zero and Synology, no runtime deps. Cost: you
  write the widgets (no HTML/plugin ecosystem). **Best complexity/effectiveness for a
  Zero-class static target if a heavy off-the-shelf server is overkill.**
  - If you later want HTML layouts in Go without Chrome, `go-rod` still needs Chromium;
    there's no great pure-Go HTML rasterizer — hence `gg`-drawn widgets is the pragmatic
    static-binary route.

**Suggested next step:** decide host first (Synology-with-Docker ⇒ Terminus or
node_lite; bare Pi Zero ⇒ Go+`gg`). The protocol shim is identical either way; only
the renderer differs, and the device doesn't care.

---

## 7. Refresh cycle: timing & the black/white flashing

Observed: a full refresh ≈ **22 s** with ~**6 black/white inversions** — jarring.
Here's exactly what's happening and what's controllable. (Logic in
`src/display.cpp::display_show_image`, decision block ~lines 1692–1715.)

### The three modes (`bb_epaper.h`: FULL=0, FAST=1, PARTIAL=2)
- **FULL** (~22 s, ~6 flashes): full UC8179 LUT, many waveform inversions. Clears
  ghosting. Most jarring.
- **FAST** (~3–5 s, ~1–2 flashes): controller/OTP LUT, fewer cycles.
- **PARTIAL** (~1–2 s, **no flash**): updates changed pixels only; accumulates
  ghosting if overused.

### What forces FULL (the flashing)
```c
// display.cpp ~1703
if ((iUpdateCount & 7) == 0 || apiDisplayResult.response.maximum_compatibility)
    iRefreshMode = REFRESH_FULL;          // full refresh every 8 partials, or always if max-compat
// ~1708
if (refresh_seconds >= 30*60 && iRefreshMode == REFRESH_PARTIAL)
    iRefreshMode = REFRESH_FAST;          // long intervals → fast (anti-ghost)
```
- `iUpdateCount` is `RTC_DATA_ATTR` (`display.cpp:85`): survives **deep sleep** but
  **resets to 0 on power-off**. So `(iUpdateCount & 7)==0` ⇒ **every power-cycle and
  every 8th wake does a FULL refresh.** During our debugging we power-cycled the
  device constantly → it full-refreshed almost every time. **In normal deep-sleep
  operation, 7 of every 8 updates are PARTIAL (no flash).** A big chunk of the
  "always flashing" impression was a debugging artifact.
- Switching between 1-bit and 4-gray also forces FULL and resets the counter
  (`display.cpp:1559/1694`).
- **Extra draws per boot add extra refreshes:** on boot the firmware draws the TRMNL
  **logo** (`bl.cpp:1037/1043`) and any WiFi/status screen *before* the content — each
  is its own refresh. For a kiosk BYOS you can skip the logo to remove one
  refresh/boot (firmware change).

### Levers
| Lever | Who controls | Effect |
|---|---|---|
| `refresh_rate` ≥ 1800 s | **Server** (`/api/display`) | Demotes would-be PARTIALs to FAST (fewer flashes, anti-ghost). |
| `maximum_compatibility` | **Server** | `true` = always FULL (worst flashing); keep **false**. |
| `temperature_profile` `"a"` | **Server** | Selects GEN2/“fast” LUT profile (`dpList[1]`), faster waveform. |
| every-8-FULL rule | Firmware (RTC) | Anti-ghost cadence; change the `& 7` mask to flash less often (more ghosting risk). |
| boot logo / status draws | Firmware (`bl.cpp`) | Remove to cut one full refresh per boot. |
| keep device in **deep sleep** (don't power-cycle) | Operations | Preserves `iUpdateCount` so most updates stay PARTIAL. |
| `bWait=false` path | Firmware | Loading screens use PARTIAL/FAST intentionally. |

### Practical recommendation to make it fast & calm
1. Server: keep `maximum_compatibility=false`; set `temperature_profile:"a"`; choose
   `refresh_rate` deliberately (note the ≥30-min FAST cutoff — between those, updates
   are PARTIAL/no-flash except the every-8 FULL).
2. Firmware (optional, small PRs): skip the boot logo for BYOS; consider relaxing the
   `& 7` cadence (e.g. `& 15`) to halve full-refresh frequency; expose the cadence as a
   server field if you want server control.
3. Don't evaluate flashing by power-cycling — test across natural deep-sleep wakes.
4. Hard floor: a periodic FULL refresh is physically required to clear e-ink ghosting;
   you can make it rarer/faster but not eliminate it. ~22 s/FULL is largely the panel's
   UC8179 waveform; FAST/PARTIAL are the way to "skip" it most of the time.

---

## 8. Open items / next steps
Done in the FastAPI phase (`byos/server.py` rewrite + `byos/state.py`):
- [x] Final clean reflash of the XIAO (test pattern removed). Verified via server log.
- [x] Server host decided: **Python/FastAPI**, deps fully nix-specified
      (`pillow fastapi uvicorn pydantic`) → runs unchanged on this Mac and the NixOS box.
- [x] Server reads `ID` + `Battery-Voltage` (+ RSSI/WiFi/FW/Model/Update-Source) into a
      Pydantic `state.json`; **v1 dashboard** renders that device's stats (clock, date,
      friendly_id, battery V + rough %, RSSI, WiFi, last-seen), per-device via `?mac=`.
- [x] `POST /api/log` implemented (returns 204; records `last_seen`, prints body).
- [x] Server URL de-hardcoded: compile default is stock `https://trmnl.app`, overridable
      with `-D BYOS_SERVER_URL=\"http://host:8080\"`; **runtime portal field already
      exists** (`api_server` → NVS `api_url`, used before the compile default).
- [x] `full_refresh_every` emitted by the server (=16). **Firmware side deferred** (you
      judged refresh already calm): the 3-line firmware edit is specced in
      `docs/.../2026-06-06-byos-server-and-refresh-cadence-design.md §Part 3`, ready to
      apply on a future reflash; until then the device uses its built-in default (8) and
      ignores the field — harmless.

Still open:
- [ ] Migrate the server to the NixOS host (currently run on the Mac). Same code/deps.
- [ ] Build out the real dashboard beyond v1 (more widgets/layout) in `render_frame`.
- [ ] Optional firmware PRs: skip boot logo for BYOS; apply the `full_refresh_every`
      firmware edit (only if you decide you want server-tunable flash cadence).
- [ ] Power off the stray classic ESP32 (it also polls the server; only the XIAO
      drives the panel).
