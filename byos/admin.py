"""Operator admin page: stats table + PNG thumbnails + per-device actions.

LAN-only, no auth. Mutations go through StateStore; actions are applied on the
device's next /api/display poll.
"""

from __future__ import annotations

import datetime
import html

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

import render
from inventory import Inventory
from registry import resolve
from render import RenderContext, to_png
from state import StateStore

SPECIAL_FUNCTIONS = ["none", "identify", "sleep", "add_wifi", "restart_playlist",
                     "rewind", "send_to_me"]


def _ago(ts: datetime.datetime) -> str:
    secs = int((datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    return f"{secs // 3600}h ago"


def build_router(state: StateStore, inventory: Inventory) -> APIRouter:
    r = APIRouter()

    @r.get("/admin", response_class=HTMLResponse)
    def admin(request: Request):
        groups = [""] + sorted(inventory.groups.keys())
        rows = []
        for d in sorted(state.all(), key=lambda d: d.friendly_id):
            pct = d.battery_percent()
            batt = "—" if d.battery_voltage is None else f"{d.battery_voltage:.2f}V" + (
                f" ({pct}%)" if pct is not None else "")
            pend = []
            if d.pending.force_full_refresh:
                pend.append("full")
            if d.pending.special_function:
                pend.append(d.pending.special_function)
            pend_badge = f" <b>⏳ {'/'.join(pend)}</b>" if pend else ""
            sf_opts = "".join(
                f"<option {'selected' if s == (d.pending.special_function or 'none') else ''}>{s}</option>"
                for s in SPECIAL_FUNCTIONS)
            grp_opts = "".join(
                f"<option value='{g}' {'selected' if g == (d.group_override or '') else ''}>"
                f"{g or '(auto)'}</option>" for g in groups)
            rows.append(f"""
<tr>
  <td>{html.escape(d.friendly_id)}</td>
  <td><code>{html.escape(d.mac)}</code></td>
  <td>{html.escape(','.join(d.tags)) or '—'}{pend_badge}</td>
  <td>{_ago(d.last_seen)}</td>
  <td>{batt}</td>
  <td>{'' if d.rssi is None else str(d.rssi)+' dBm'}</td>
  <td>{html.escape(d.wifi_ssid or '—')}</td>
  <td>{html.escape(d.fw_version or '—')}</td>
  <td>{html.escape(d.last_log or '')}</td>
  <td><img src="/admin/thumb/{html.escape(d.mac)}.png" width="200"></td>
  <td>
    <form method="post" action="/admin/device/{html.escape(d.mac)}">
      <label><input type="checkbox" name="force_full_refresh"> full refresh</label><br>
      special: <select name="special_function">{sf_opts}</select><br>
      group: <select name="group">{grp_opts}</select><br>
      <button type="submit">Submit</button>
    </form>
  </td>
</tr>""")
        body = "".join(rows) or "<tr><td colspan=11>no devices yet</td></tr>"
        return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="15"><title>TRMNL BYOS admin</title>
<style>body{{font:14px sans-serif;margin:16px}}table{{border-collapse:collapse}}
td,th{{border:1px solid #ccc;padding:6px;vertical-align:top}}img{{image-rendering:pixelated}}</style>
</head><body><h2>TRMNL BYOS — devices</h2><table>
<tr><th>id</th><th>mac</th><th>tags</th><th>seen</th><th>batt</th><th>rssi</th>
<th>wifi</th><th>fw</th><th>last log</th><th>showing</th><th>actions</th></tr>
{body}</table></body></html>"""

    @r.get("/admin/thumb/{mac}.png")
    def thumb(mac: str):
        dev = state.get(mac)
        img = resolve(dev).fn(RenderContext(device=dev)) if dev else render.blank()
        return Response(content=to_png(img), media_type="image/png")

    @r.post("/admin/device/{mac}")
    async def action(mac: str, request: Request):
        form = await request.form()
        group = form.get("group") or None
        state.set_group_override(mac, group)
        sf = form.get("special_function")
        state.queue_command(
            mac,
            force_full_refresh=bool(form.get("force_full_refresh")),
            special_function=(sf if sf and sf != "none" else None),
        )
        return RedirectResponse("/admin", status_code=303)

    return r
