# BYOS pluggable per-client renderers + admin page — design

Date: 2026-06-06
Branch: `byos-custom-server`
Status: approved design, pre-implementation

## Goal

Evolve the BYOS server (`byos/`) from a single-image server into a small **core**
that understands the TRMNL protocol/telemetry, with **per-client customization**
layered on top via a plugin registry, plus an **admin page**. Concretely:

1. **Core semantics.** A canonical layer that parses everything the firmware
   reports each poll (headers → a `DeviceReport`) and records the latest snapshot
   per device. The rest of the server builds on this.
2. **Pluggable, multi-client rendering.** Drop a Python module into `byos/clients/`
   and it is auto-imported at boot and registers one or more renderers, each bound
   to a **matcher** (exact MAC, tag/group, model, glob, or predicate). A device is
   dispatched to its best-matching renderer; unmatched devices fall back to a
   built-in default. Renderers can extend the default renderer.
3. **Declarative grouping.** A human-authored, Pydantic-validated
   `byos/inventory.yaml` (Ansible-inventory style) maps MACs (exact or glob) to
   tags/groups, and supplies per-group display config (refresh cadence).
4. **Per-group/device display config.** Renderers/groups control not just the
   image but `refresh_rate`, `full_refresh_every`, and `special_function`, by a
   clear precedence chain.
5. **Admin page.** `GET /admin` — a stats table (one row per client) with a live
   thumbnail and interactive action cells: force a FULL refresh on next poll,
   trigger a `special_function`, and reassign a device's group. Actions are
   queued as per-device overrides and applied on the device's next poll.

Non-goals: rolling history / trend charts (latest snapshot only); per-device
`refresh_rate` override in the UI (cadence stays group-driven); authentication on
the admin page (LAN-only, trusted network); host migration; firmware changes
(the `full_refresh_every` firmware edit remains the separately-specced, deferred
item — the server already emits the field).

## Background (verified against firmware)

The device is a pull-only client rendering 800×480 1-bit bitmaps. On every
`/api/display` poll it sends a rich header set
(`lib/trmnl/src/api-client/request_headers.cpp:4-39`), all of which the core will
parse into `DeviceReport`:

`ID` (MAC = client id), `Battery-Voltage`, `RSSI`, `WiFi-SSID` (percent-encoded),
`WiFi-Band`, `FW-Version`, `Model`, `Refresh-Rate`, `Update-Source` (wake reason),
`Wake-Time`, `Image-Cached`, `Width`, `Height`, `Temperature-Profile`,
`Access-Token`, and `special_function` (only when active). TRMNL **X** also sends
gauge-grade battery headers; the **OG** sends `Battery-Voltage` only (we derive a
rough % from it). `/api/setup` sends `ID`/`FW-Version`/`Model`; `/api/log` sends
`ID`/`Access-Token`.

The current server (`byos/server.py` FastAPI + `byos/state.py`) already parses a
subset into a Pydantic `state.json` and serves a single clock+stats frame. This
design refactors that into core + registry + plugins + admin.

The firmware honors these `/api/display` response fields (display parser uses
`| default`, so unknown/absent fields are safe): `status`, `image_url`,
`filename`, `refresh_rate`, `full_refresh_every` (server-emitted; firmware edit
deferred), `maximum_compatibility`, `temperature_profile`, `special_function`,
`update_firmware`/`firmware_url`, `reset_firmware`. `/api/setup` REQUIRES
`status == 200`.

## Design

### Module layout

```
byos/
  server.py      FastAPI app: /api/setup, /api/display, /api/log, /current.bmp, /admin*; thin glue.
  protocol.py    DeviceReport (Pydantic) + parse_report(request) — the canonical header→data layer.
  state.py       [extend] StateStore: latest DeviceState per MAC + overrides + pending; atomic JSON.
  inventory.py   Inventory (Pydantic) loaded from inventory.yaml; resolve_tags(mac), group_settings(tag).
  registry.py    renderer registry, matchers (mac/tag/model/glob/predicate), resolve(device)->Renderer.
  render.py      RenderContext, RenderResult, default_render(), to_bmp()/to_png() helpers.
  clients/       auto-imported at boot; each module registers renderer(s).
    default.py   the built-in renderer (clock + device-stats frame); importable for extension.
  admin.py       /admin HTML table + thumbnail + POST action handler (kept out of server.py).
  inventory.yaml committed config (human-authored).
  state.json     runtime data (gitignored).
```

Each unit has one job and a narrow interface: `protocol` (parse), `state`
(persist), `inventory` (group data), `registry` (match→renderer), `render`
(pixels + image encoding), `admin` (operator UI), `server` (HTTP wiring). Files
stay small and independently testable.

### Core: `protocol.py`

`DeviceReport` (Pydantic) is built by `parse_report(request)` from request
headers, with safe coercion (missing/garbled → `None`):

```
mac (ID), api_key (Access-Token), model, fw_version,
battery_voltage: float|None, rssi: int|None, wifi_ssid: str|None (percent-DEcoded),
wifi_band: str|None, refresh_rate: int|None, update_source: str|None,
wake_time: int|None, image_cached: bool|None, width: int|None, height: int|None
```

This is the single place that knows header names. `server.py` calls it; nothing
else touches `request.headers` for telemetry.

### State: `state.py` (extend existing)

`DeviceState` (latest snapshot, keyed by MAC) gains, on top of today's fields:
`wifi_band`, `wake_time`, `image_cached`, `width`, `height`, `tags: list[str]`
(resolved at last poll, for admin display), `group_override: str | None`
(admin-set, takes precedence over inventory), `last_log: str | None`, and
`pending: PendingCommands`.

`PendingCommands` (Pydantic): `force_full_refresh: bool = False`,
`special_function: str | None = None`. One-shot: consumed and cleared when the
device next polls `/api/display`.

`StateStore` (thread-safe, atomic write — unchanged mechanism) gains:
- `upsert_report(report: DeviceReport, tags: list[str]) -> DeviceState` — update
  snapshot + `last_seen` + tags; assign `friendly_id`/`api_key` on first sight.
- `set_group_override(mac, group | None)` and `set_last_log(mac, line)`.
- `queue_command(mac, *, force_full_refresh=False, special_function=None)`.
- `take_pending(mac) -> PendingCommands` — return and atomically clear.

`api_key`/`friendly_id` stay cosmetic (server never gates on `Access-Token`).

### Inventory: `inventory.py` + `inventory.yaml`

Ansible-inventory-style YAML, validated by Pydantic:

```yaml
groups:
  garden:
    members: ["A0:EA:DE:AD:BE:EF", "DC:44:*"]   # exact MAC or fnmatch glob (case-insensitive)
    refresh_rate: 3600
    full_refresh_every: 32
    special_function: null
  desk:
    members: ["44:1B:F6:81:A2:80"]
```

Models: `GroupSettings {members: list[str], priority: int = 0,
refresh_rate: int|None, full_refresh_every: int|None, special_function: str|None}`;
`Inventory {groups: dict[str, GroupSettings]}`. `priority` disambiguates config
when a device is in several groups (higher wins; ties → file order).

API:
- `Inventory.load(path) -> Inventory` — parse YAML→dict→`model_validate`; **missing
  file ⇒ empty inventory; malformed ⇒ log warning + empty** (never crash the
  server on a bad edit). Loaded once at boot; `server.py` may expose a reload.
- `resolve_tags(mac) -> list[str]` — every group whose `members` match `mac`
  (exact or glob, case-insensitive). A device can be in several.
- `group_settings(tag) -> GroupSettings`.

Glob note: TRMNL MACs aren't serial, so globs mostly catch OUI/vendor prefixes;
exact MACs are the norm. Both supported via `fnmatch`.

### Registry + matching: `registry.py`

A renderer registers via decorator with a matcher, optional priority, and optional
display-config defaults:

```python
@renderer(match=tag("garden"), priority=50, refresh_rate=3600, full_refresh_every=32)
def garden(ctx: RenderContext) -> "PIL.Image": ...
```

Matchers (all return `bool` given a `DeviceState`+tags): `mac(addr)`,
`tag(name)`, `model(name)`, `glob(pattern)` (on MAC), `predicate(fn)`.
`@renderer(...)` stores `RegisteredRenderer{fn, matcher, priority, config}`.

`resolve(device) -> RegisteredRenderer`: of all registrations whose matcher
matches, return the highest `priority` (ties broken by registration order); if
none, the built-in `default`. `default` is always registered (priority −inf).

`load_clients(dir)` (called at boot from `server.py`): import every `*.py` in
`byos/clients/` (via `importlib`), which runs their `@renderer` decorators. Import
errors are logged and skipped (one bad plugin can't down the server).

### Rendering: `render.py`

- `RenderContext`: the `DeviceState` snapshot (mac, friendly_id, battery_voltage
  + `battery_percent()`, rssi, wifi_ssid, fw, model, tags, last_seen) plus
  `width`/`height` (800×480) and a `now` timestamp. Read-only view for renderers.
- A renderer returns a 1-bit `PIL.Image` of size (width, height). (Returning the
  image — not drawing into a passed canvas — lets renderers compose:
  `img = default_render(ctx); ImageDraw.Draw(img)...`).
- `default_render(ctx)` — today's clock+date+device-stats frame, moved here and
  made `ctx`-driven; exported for plugins to extend.
- `to_bmp(img) -> bytes` (the 48062-byte 1-bit BMP the firmware validates) and
  `to_png(img) -> bytes` (for admin thumbnails). `INVERT` handled here.

### Display-config precedence

Renderer selection and display-config are resolved **independently** (both
deterministic), so "who draws" and "which cadence" never entangle. For each of
`refresh_rate`, `full_refresh_every`, `special_function`, take the first non-None
of:

1. **pending one-shot** (force-full-refresh ⇒ `full_refresh_every = 1`; queued
   `special_function`), then cleared after this response,
2. **group settings** — among the device's matched groups, the one with the
   highest `priority` that sets this field (ties → inventory file order),
3. **renderer-declared default** — the `@renderer(...)` kwarg for this field,
4. **global default** — `REFRESH_RATE`, `FULL_REFRESH_EVERY`, `"none"`.

This is per-field (a group may set only `refresh_rate`; cadence then falls through
to the renderer/global). Renderer selection uses the separate renderer `priority`;
group config uses group `priority`.

### Dispatch flow

`GET /api/display`:
1. `report = parse_report(request)`
2. `tags = [override] if group_override(mac) else inventory.resolve_tags(mac)`
   — an admin group override **replaces** the inventory-resolved tags (clear it to
   return to auto).
3. `device = state.upsert_report(report, tags)`
4. `reg = registry.resolve(device)` — best-matching renderer (renderer `priority`).
5. `pending = state.take_pending(mac)`
6. `cfg = resolve_config(pending, device.tags→group settings, reg.config, globals)`
   — per-field precedence from the section above.
7. return `DisplayResponse(status=0, image_url=f"{base}/current.bmp?mac={mac}",
   filename=f"frame-{ts}", refresh_rate=cfg.refresh_rate,
   full_refresh_every=cfg.full_refresh_every, special_function=cfg.special_function,
   …defaults…)`

`GET /current.bmp?mac=`: load device → `registry.resolve` → render → `to_bmp`. No
`mac` (or unknown) ⇒ render with the latest device (or a placeholder frame).

`GET /api/setup`: upsert (sparse report), return `SetupResponse(status=200, …)`.

`POST /api/log`: read `ID` + body, `state.set_last_log(mac, first_line)`, touch
last_seen, return **204**; never error on a bad body.

### Admin: `admin.py`

- `GET /admin` → HTML (server-rendered, no JS framework; `<meta refresh>` every
  ~15 s). One row per device: friendly_id · MAC · tags (with override marked) ·
  last-seen (relative) · battery V + % · RSSI · WiFi · FW · model · wake-reason ·
  current refresh_rate · last_log (truncated) · **thumbnail** `<img
  src="/admin/thumb/{mac}.png">` · **action form**: `force full ☐` ·
  `special_function ▾` (none/identify/sleep/…) · `group ▾` (inventory groups +
  "(auto)") · `Submit`.
- `GET /admin/thumb/{mac}.png` → `to_png(render(device))` (browsers don't render
  1-bit BMP well).
- `POST /admin/device/{mac}` → set group override (or clear), queue
  force-full-refresh / special_function via `state`, then redirect to `/admin`.
  Pending commands shown as a "⏳ pending" badge until the next poll consumes them.
- No auth (LAN-only). Mutations go only through `StateStore` (atomic).

### Dependencies

Add `pyyaml` to the nix-shell shebang and `byos/shell.nix` (already have
`pillow fastapi uvicorn pydantic`). All in nixpkgs → still fully nix-specified,
runs unchanged on the Mac and the NixOS host. No JS/CSS build step.

## Testing / verification

Unit (no HTTP):
- `inventory`: exact + glob (case-insensitive) membership; multi-group; missing
  file ⇒ empty; malformed YAML ⇒ empty + warning (no raise).
- `registry`: priority ordering; tie → load order; no match ⇒ default; each
  matcher type (mac/tag/model/glob/predicate).
- `state`: upsert assigns DEV001/DEV002, same MAC keeps id; group override;
  `queue_command` then `take_pending` returns then clears; round-trip reload;
  corrupt `state.json` ⇒ empty + warning.
- `render`: `default_render` → `to_bmp` is exactly 48062 bytes, 1-bit 800×480
  (matches `bmp.cpp`); `to_png` decodes.

Integration (curl, run locally):
- `/api/display` for a MAC in `garden` returns that group's `refresh_rate` /
  `full_refresh_every`; a MAC in no group returns globals.
- A `clients/` plugin matching by tag draws a distinct frame (verify via
  `/current.bmp?mac=`).
- `/admin` renders a row per known device with a working thumbnail.
- `POST /admin/device/{mac}` with force-full-refresh ⇒ next `/api/display` returns
  `full_refresh_every == 1` once, then reverts on the following poll.
- `POST /api/log` ⇒ 204 and the line shows in that device's admin row.

## Files touched

- `byos/protocol.py` — new: `DeviceReport`, `parse_report`.
- `byos/inventory.py` — new: `Inventory`/`GroupSettings`, load + resolve.
- `byos/registry.py` — new: `@renderer`, matchers, `resolve`, `load_clients`.
- `byos/render.py` — new: `RenderContext`, `default_render`, `to_bmp`/`to_png`.
- `byos/clients/default.py` — new: built-in renderer (moved from `render_frame`).
- `byos/admin.py` — new: `/admin`, thumbnail, action POST.
- `byos/server.py` — slim down to wiring: parse → state → inventory → registry →
  response; mount admin; `load_clients` + `Inventory.load` at startup; emit
  resolved display-config; `/api/log` stores last line.
- `byos/state.py` — extend `DeviceState` (+tags/override/pending/last_log/extra
  headers) and `StateStore` (upsert_report, overrides, pending take/clear).
- `byos/inventory.yaml` — new: starter inventory (committed).
- `byos/shell.nix` + server shebang — add `pyyaml`.
- `byos/README.md` — document `clients/`, the matcher API, `inventory.yaml`, and
  `/admin`.
- `.gitignore` — already ignores `state.json`; no change.
- `HANDOFF.md` — note the plugin/admin architecture under the server section.
