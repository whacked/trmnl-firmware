# TRMNL BYOS — minimal reference server

A tiny "Bring Your Own Server" for a TRMNL device. It speaks just enough of the
TRMNL protocol that a device pointed at it boots straight into content **you**
control — no account, no MAC registration, no "email support@" step.

The device has **no browser**: it renders 800×480 1-bit **bitmaps**, not webpages.
So this server's job is to hand the device a bitmap. To change what's shown, edit
`render_frame()` in [`server.py`](server.py).

## How it fits together

```
device boot → WiFi (captive portal, one-time)
            → POST /api/setup    (this server returns a key for ANY device)
            → GET  /api/display  (this server says "show /current.bmp")
            → GET  /current.bmp  (800×480 1-bit BMP, rendered live)
            → deep sleep refresh_rate seconds → repeat
```

The device knows to talk to this server because `include/byos_config.h` sets
`BYOS_SERVER_URL` to this machine's LAN IP. Change that one line and reflash to
move the server.

## Run it

The server needs Python 3 + [Pillow]. Easiest (no global install):

```bash
# nix-shell shebang — just run the file:
./server.py
```

Or via the dev shell:

```bash
nix-shell shell.nix --run 'python server.py'
```

### Alternative: uv (if you prefer a venv)

```bash
nix-shell -p uv --run 'uv venv && uv pip install pillow && uv run server.py'
```

### Alternative: existing Python + Pillow

```bash
pip install pillow   # if not already available
python3 server.py
```

It listens on `0.0.0.0:8080`.

## Verify without a device

```bash
curl -s http://localhost:8080/api/setup    | python3 -m json.tool
curl -s http://localhost:8080/api/display  | python3 -m json.tool
curl -s http://localhost:8080/current.bmp -o /tmp/f.bmp && \
  ls -l /tmp/f.bmp && head -c2 /tmp/f.bmp    # -> 48062 bytes, starts with "BM"
```

## Customize

Edit `render_frame(draw, img)` in `server.py`. `draw` is a PIL `ImageDraw` on an
800×480 1-bit canvas; use the `BLACK` / `WHITE` constants. The full PIL text and
shape API is available.

A few knobs at the top of `server.py`:

- `PORT` — must match the port in `include/byos_config.h`.
- `REFRESH_RATE` — seconds the device sleeps between refreshes.
- `INVERT` — flip to `True` if the image shows up inverted on the panel.

## Notes

- The device and this machine must be on the same LAN.
- macOS may prompt to allow incoming connections on port 8080 — allow it.
- First `nix-shell` launch builds/fetches the Python+Pillow env (then cached).

[Pillow]: https://python-pillow.org/
