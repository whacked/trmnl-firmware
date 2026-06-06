# BYOS server (FastAPI) + device state + server-controlled refresh cadence — design

Date: 2026-06-06
Branch: `byos-custom-server`
Status: approved design, pre-implementation

## Goal

Three coupled changes to the BYOS (Bring Your Own Server) setup:

1. **Server:** Rewrite `byos/server.py` on FastAPI so all four firmware-facing
   endpoints are explicitly specced (Pydantic models as the executable spec),
   including the currently-missing `POST /api/log`.
2. **State:** Track each device in a JSON file (`byos/state.json`) read and
   written exclusively through Pydantic models — never raw dict munging. Records
   what each device reports (battery, RSSI, fw, last-seen) from request headers,
   and issues each a stable `friendly_id` + `api_key`.
3. **Refresh cadence (approach B):** Make the e-ink FULL-refresh cadence
   server-controllable via a new `/api/display` field `full_refresh_every`,
   instead of the hardcoded "every 8th update" in firmware. This lets us tune
   flashing-vs-ghosting from the server without ever reflashing the device
   (which requires a painful physical BOOT + power-switch bootloader entry).

Non-goals: dashboard/layout work, deriving/rendering a battery percentage from
the stored voltage, boot-logo removal, host migration. Those remain open items
in `HANDOFF.md §8`. (State storage was previously deferred; it is now in scope
per item #2 above, but only the storage layer — not the dashboard that consumes
it.)

## Background (verified against firmware)

The firmware is a pull-only client that renders bitmaps. It speaks JSON
(ArduinoJson) and hits four endpoints on `BYOS_SERVER_URL`:

| Endpoint | Method | Firmware code | Firmware expects |
|---|---|---|---|
| `/api/setup` | GET | `src/api-client/setup.cpp:13` | JSON: `status` (**must be 200**), `api_key`, `friendly_id`, `image_url`, `message` |
| `/api/display` | GET | `src/api-client/display.cpp:66` | JSON: the `ApiDisplayResponse` fields (`lib/trmnl/include/api_types.h:38`) |
| `/api/log` | POST | `src/api-client/submit_log.cpp:18` | JSON body in; accepts `200` / `204` / `301`. **Not implemented in current server.** |
| `<image_url>` | GET | image fetch | 800×480 **1-bit** BMP (48062-byte file) or 1-bit PNG |

Setup parser (`parse_response_api_setup.cpp`): rejects anything where
`status != 200`. Display parser (`parse_response_api_display.cpp`): reads
optional fields with `| default` fallbacks, so unknown/absent fields are safe.

The FULL-refresh decision lives in `src/display.cpp:1703`:

```c
if ((iUpdateCount & 7) == 0 || apiDisplayResult.response.maximum_compatibility == true) {
    iRefreshMode = REFRESH_FULL; // force full refresh every 8 partials
}
```

`iUpdateCount` is `RTC_DATA_ATTR` — survives deep sleep, resets to 0 on
power-off. A FULL refresh is ~22 s with ~6 black/white inversions; PARTIAL is
silent. So today: every 8th wake (and every power-cycle) flashes.

### Cadence in wall-clock terms

FULL-refresh interval = `full_refresh_every × refresh_rate`. At the server's
current `refresh_rate = 900 s` (15 min/update), where 15-min updates stay
PARTIAL except the every-Nth FULL:

| `full_refresh_every` | FULL flash every | partials between flashes |
|---|---|---|
| 8 (stock) | 2 hours | 7 silent, then 1 flash |
| **16 (chosen default)** | **4 hours** | 15 silent, then 1 flash |
| 32 | 8 hours | 31 silent, then 1 flash |

The field controls **update count**, not minutes — if `refresh_rate` later
changes, the wall-clock interval scales with it. Trade-off: partial updates
accumulate e-ink ghosting; the FULL refresh clears it. 4 hours is the chosen
balance (calm, but cleared several times a day).

## Design

### Part 1 — FastAPI server

Rewrite `byos/server.py` using FastAPI + uvicorn. Keep the nix-shell shebang
(`fastapi`, `uvicorn`, `pydantic`, `pillow` are all in nixpkgs, so the deps stay
fully nix-specified); add `fastapi`, `uvicorn`, and `pydantic` to the
`python3.withPackages` set in both the shebang and `byos/shell.nix`.

Endpoints (each device-touching one upserts state — see Part 2):

- **`GET /api/setup`** → Pydantic `SetupResponse`: `status=200`, `api_key`,
  `friendly_id`, `image_url` (absolute, built from request `Host`), `filename`,
  `message`. Reads `ID`/`FW-Version`/`Model` headers, upserts the device, and
  returns that device's persisted `api_key` + `friendly_id`. No gatekeeping
  (this is what removes the activation step).
- **`GET /api/display`** → Pydantic `DisplayResponse` (see Part 3 for the new
  field): `status=0`, `image_url`, `filename`, `refresh_rate`,
  `full_refresh_every`, `update_firmware=false`, `firmware_url=null`,
  `reset_firmware=false`, `special_function="none"`. Reads the full display
  header set (`ID`, `Battery-Voltage`, `RSSI`, `WiFi-SSID`, `FW-Version`,
  `Model`, `Refresh-Rate`, `Update-Source`, `Image-Cached`, `Wake-Time`) and
  updates the device record before responding.
- **`POST /api/log`** (new) → read `ID`, touch the device's `last_seen`, accept
  an arbitrary JSON body, print a one-line summary, return **HTTP 204** (no
  content). Body is best-effort: never 4xx/5xx on a malformed log payload, so
  the device never errors on log submission.
- **`GET /current.bmp`** → 800×480 1-bit BMP via `render_frame()` (unchanged
  customization hook) + `render_bmp()`. Serve with `Content-Type: image/bmp`.
  Support `HEAD`. Stateless (no device header).

Absolute image URLs are still built from the request `Host` header so they
resolve back regardless of LAN IP. Server settings (`HOST`, `PORT`,
`REFRESH_RATE`, `FULL_REFRESH_EVERY`, `INVERT`, `WIDTH/HEIGHT`) stay as
module-level constants at the top of the file.

The Pydantic response models ARE the spec for the server side; a short comment
block maps each field to its firmware consumer.

### Part 2 — State (Pydantic-managed JSON file)

State lives in a new module `byos/state.py` (keeps HTTP/render in `server.py`,
state logic separately testable). All persistence goes through Pydantic — the
JSON file is only ever produced by `model_dump_json()` and consumed by
`model_validate_json()`; no hand-rolled `json.load`/dict access.

Models:

- **`DeviceState`** — one device, keyed by MAC. Fields, all populated from
  request headers (`request_headers.cpp`):
  `mac` (the `ID` header), `friendly_id`, `api_key`, `model`, `fw_version`,
  `first_seen` / `last_seen` (datetime), `battery_voltage` (float | None),
  `rssi` (int | None), `wifi_ssid` (str | None), `refresh_rate` (int | None),
  `update_source` (str | None), `last_image_filename` (str | None).
  Optional fields default to `None` so a sparse setup request (only
  `ID`/`FW-Version`/`Model`) validates fine.
- **`ServerState`** — `devices: dict[str, DeviceState]` keyed by MAC, plus
  `next_seq: int` (monotonic counter for assigning `friendly_id`s, **default
  1** so the first device is `DEV001`).

Store:

- **`StateStore`** — wraps a file path and an in-memory `ServerState`
  (authoritative copy). On init: load + validate the file if present, else start
  empty; tolerate a missing/corrupt file by starting empty (log a warning) so a
  bad write never bricks the server. A `threading.Lock` guards every
  read-modify-write since uvicorn serves requests concurrently.
- **`upsert(mac, **fields)`** — look up or create the `DeviceState`. On first
  sight, assign `friendly_id = f"DEV{next_seq:03d}"` (increment `next_seq`) and
  a deterministic `api_key = "byos-" + mac.replace(":","").lower()`. Update
  `last_seen` and any provided fields. Persist via an **atomic write** (write to
  `state.json.tmp`, `os.replace`) so a crash mid-write can't truncate the file.

`api_key`/`friendly_id` are cosmetic here — the server never gates on
`Access-Token`, so the already-onboarded device (which holds `api_key`
`local-dev` in NVS and won't call `/api/setup` again) is unaffected; new devices
simply get a generated, stable key.

`byos/state.json` is runtime data → add to `.gitignore`.

### Part 3 — `full_refresh_every` (server-controlled cadence)

**Server:** add module constant `FULL_REFRESH_EVERY = 16` and emit it in the
`/api/display` response.

**Firmware (3 small edits):**

1. `lib/trmnl/include/api_types.h` — add to `ApiDisplayResponse`:
   ```c
   uint32_t full_refresh_every;
   ```
2. `lib/trmnl/src/parse_response_api_display.cpp` — parse with a default that
   preserves stock behavior when the field is absent (e.g. stock cloud):
   ```c
   .full_refresh_every = doc["full_refresh_every"] | 8,
   ```
3. `src/display.cpp:1703` — replace the bitmask with a modulo using the parsed
   value (modulo drops the power-of-2 constraint and reads clearer); guard
   against 0 to avoid divide-by-zero:
   ```c
   uint32_t fre = apiDisplayResult.response.full_refresh_every;
   if (fre == 0) fre = 8;
   if ((iUpdateCount % fre) == 0 || apiDisplayResult.response.maximum_compatibility == true) {
       iRefreshMode = REFRESH_FULL; // force full refresh every `fre` updates (server-controlled)
   }
   ```

Behavior matrix:
- Field present (our server, =16) ⇒ FULL every 16th update (~4 h).
- Field absent (stock cloud) ⇒ defaults to 8 ⇒ identical to today.
- Field = 0 (defensive) ⇒ treated as 8.
- `maximum_compatibility=true` ⇒ still always FULL (unchanged).

The existing `refresh_rate >= 30 min ⇒ FAST` rule at `display.cpp:1708` is
untouched and composes as before.

## Testing / verification

**Firmware** (no device required):
- `PATH="$HOME/.platformio/penv/bin:$PATH" ~/.platformio/penv/bin/pio run -e TRMNL_7inch5_OG_DIY_Kit`
  builds clean. Confirms the struct field, parser, and `display.cpp` edit
  compile.

**Server** (run locally, curl with `-H "ID: AA:BB:CC:DD:EE:FF"`):
- `GET /api/setup` → 200, JSON has `status==200` and all five required keys;
  a `byos/state.json` appears with one device record.
- `GET /api/display` (with `-H "Battery-Voltage: 4.05" -H "RSSI: -57"`) → 200,
  JSON has `status`, `image_url`, `refresh_rate`, `full_refresh_every == 16`;
  the device's record gains `battery_voltage`/`rssi`/`last_seen`.
- `POST /api/log` with a JSON body → 204, no error; device `last_seen` bumped.
- `GET /current.bmp` → 200, `image/bmp`, body is exactly 48062 bytes, parses
  as a 1-bit 800×480 BMP (matches `bmp.cpp` validator).
- `HEAD /current.bmp` → 200 with `Content-Length`, no body.

**State layer** (`byos/state.py`, unit-level, no HTTP):
- Fresh `StateStore` on a missing path starts empty; `upsert` assigns
  `DEV001` then `DEV002` to two MACs; same MAC twice keeps its `friendly_id`.
- Round-trip: after `upsert`, a new `StateStore` on the same path
  re-validates the file and sees the persisted devices.
- A corrupt `state.json` ⇒ store starts empty + warns, does not raise.

**End-to-end** (optional, needs device): after a server-only change the device
picks up `full_refresh_every` on its next `/api/display` poll — no reflash. The
firmware edit only ships on the next planned reflash (HANDOFF §8 item #1); until
then the device defaults to 8, which is harmless.

## Files touched

- `byos/server.py` — rewrite (FastAPI, 4 endpoints, `FULL_REFRESH_EVERY`,
  wires the `StateStore` into each device-touching endpoint).
- `byos/state.py` — new: `DeviceState`, `ServerState`, `StateStore` (Pydantic +
  atomic JSON persistence).
- `byos/shell.nix` — add `fastapi`, `uvicorn`, `pydantic`.
- `.gitignore` — ignore `byos/state.json`.
- `byos/README.md` — update run instructions / endpoint list / new field /
  state file note.
- `lib/trmnl/include/api_types.h` — `full_refresh_every` field.
- `lib/trmnl/src/parse_response_api_display.cpp` — parse the field.
- `src/display.cpp` — modulo cadence using the field.
- `HANDOFF.md` — note `full_refresh_every` under the `/api/display` field table
  and §7 levers; mark `/api/log` now implemented.
