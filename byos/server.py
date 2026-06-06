#!/usr/bin/env nix-shell
#!nix-shell -i python3 -p "python3.withPackages(ps: with ps; [ pillow ])"
"""
TRMNL BYOS (Bring Your Own Server) — minimal reference server.

Speaks just enough of the TRMNL protocol that a device pointed at this server
(see include/byos_config.h) boots straight into content WE control — no account,
no MAC registration, no "email support@" step.

Endpoints:
  GET|POST /api/setup    -> hands every device an api_key + friendly_id (no gate)
  GET      /api/display  -> tells the device which image to show, and when to wake
  GET      /current.bmp  -> an 800x480 1-bit BMP, generated on the fly

The device only renders bitmaps, not webpages. To change what's displayed,
edit render_frame() below — that's the single customization hook.

Run it:
  ./byos/server.py                      (nix-shell shebang provides python+pillow)
  nix-shell shell.nix --run 'python server.py'
See byos/README.md for the uv alternative.
"""

import io
import sys
import json
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
HOST = "0.0.0.0"
PORT = 8080

# E-ink panel is exactly 800x480, 1 bit-per-pixel. Do not change unless your
# firmware's bmp.cpp validator changes too.
WIDTH, HEIGHT = 800, 480

# Seconds the device deep-sleeps between refreshes (server tells it each time).
REFRESH_RATE = 900

# Some panels show inverted black/white. If your image comes out inverted on the
# device, flip this to True.
INVERT = False

WHITE, BLACK = 255, 0


# ---------------------------------------------------------------------------
# >>> CUSTOMIZATION HOOK <<<
# Draw whatever you want here. `draw` is a PIL.ImageDraw on an 800x480 1-bit
# canvas; use BLACK / WHITE for the two colors. Full PIL text/shape API works.
# ---------------------------------------------------------------------------
def render_frame(draw: ImageDraw.ImageDraw, img: Image.Image) -> None:
    now = datetime.datetime.now()

    # Try a nicer font; fall back to PIL's built-in if unavailable.
    def font(size):
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

    # Border
    draw.rectangle([4, 4, WIDTH - 5, HEIGHT - 5], outline=BLACK, width=3)

    # Title
    draw.text((40, 40), "TRMNL BYOS", font=font(72), fill=BLACK)
    draw.text((40, 130), "your own server", font=font(36), fill=BLACK)

    # Big live clock — proves the frame is freshly rendered each refresh.
    draw.text((40, 230), now.strftime("%H:%M:%S"), font=font(140), fill=BLACK)
    draw.text((40, 400), now.strftime("%A, %d %B %Y"), font=font(40), fill=BLACK)


def render_bmp() -> bytes:
    """Render the current frame to a 1-bit BMP the firmware will accept."""
    img = Image.new("1", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)
    render_frame(draw, img)
    if INVERT:
        img = img.point(lambda p: WHITE if p == BLACK else BLACK)
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "trmnl-byos/1.0"

    def _image_base(self) -> str:
        # Build absolute URLs from the Host the device used, so they always
        # resolve back to this server regardless of our LAN IP.
        host = self.headers.get("Host") or f"127.0.0.1:{PORT}"
        return f"http://{host}"

    def _send_json(self, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        path = self.path.split("?", 1)[0]
        base = self._image_base()

        if path == "/api/setup":
            # No gatekeeping: every device gets credentials immediately.
            self._send_json({
                "status": 200,
                "api_key": "local-dev",
                "friendly_id": "DEV001",
                "image_url": f"{base}/current.bmp",
                "filename": "setup",
                "message": "BYOS setup ok",
            })

        elif path == "/api/display":
            self._send_json({
                "status": 0,                       # 0 = normal content
                "image_url": f"{base}/current.bmp",
                "filename": f"frame-{datetime.datetime.now():%Y%m%d%H%M%S}",
                "refresh_rate": REFRESH_RATE,
                "update_firmware": False,
                "firmware_url": None,
                "reset_firmware": False,
                "special_function": "none",
            })

        elif path == "/current.bmp":
            data = render_bmp()
            self.send_response(200)
            self.send_header("Content-Type", "image/bmp")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        else:
            self.send_error(404, "not found")

    do_GET = _handle
    do_POST = _handle
    do_HEAD = _handle

    def log_message(self, fmt, *args):
        # Note: log_error() also routes here on malformed requests, before
        # self.command/self.path are set — so guard with getattr.
        cmd = getattr(self, "command", "?")
        path = getattr(self, "path", "?")
        print(f"  {self.address_string()} {cmd} {path} :: {fmt % args}")


def main() -> None:
    # Flush each log line immediately so requests are visible in real time
    # even when stdout is redirected to a file.
    sys.stdout.reconfigure(line_buffering=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"TRMNL BYOS server listening on http://{HOST}:{PORT}")
    print("  GET|POST /api/setup   GET /api/display   GET /current.bmp")
    print("Point include/byos_config.h at this machine's LAN IP, then reflash.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
