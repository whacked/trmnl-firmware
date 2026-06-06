"""Rendering: the canvas context, the default renderer, and image encoders.

A renderer is `fn(ctx: RenderContext) -> PIL.Image` returning a 1-bit WIDTHxHEIGHT
image. Plugins may import default_render and draw on top of it.
"""

from __future__ import annotations

import io
import datetime
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from state import DeviceState

WIDTH, HEIGHT = 800, 480
WHITE, BLACK = 255, 0
INVERT = False


@dataclass
class RenderContext:
    device: DeviceState
    width: int = WIDTH
    height: int = HEIGHT

    @property
    def now(self) -> datetime.datetime:
        return datetime.datetime.now()


def font(size: int):
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


def blank() -> Image.Image:
    return Image.new("1", (WIDTH, HEIGHT), WHITE)


def default_render(ctx: RenderContext) -> Image.Image:
    img = blank()
    draw = ImageDraw.Draw(img)
    dev = ctx.device
    now = ctx.now

    draw.rectangle([4, 4, WIDTH - 5, HEIGHT - 5], outline=BLACK, width=3)
    draw.text((40, 34), "TRMNL BYOS", font=font(64), fill=BLACK)
    draw.text((42, 110), "your own server", font=font(30), fill=BLACK)
    draw.line([40, 156, WIDTH - 40, 156], fill=BLACK, width=2)
    draw.text((40, 180), now.strftime("%H:%M"), font=font(150), fill=BLACK)
    draw.text((44, 350), now.strftime("%A, %d %B %Y"), font=font(34), fill=BLACK)

    x = 448
    pct = dev.battery_percent()
    batt = "—"
    if dev.battery_voltage is not None:
        batt = f"{dev.battery_voltage:.2f} V" + (f"  ({pct}%)" if pct is not None else "")
    rows = [
        ("Device", dev.friendly_id),
        ("MAC", dev.mac),
        ("Battery", batt),
        ("WiFi", dev.wifi_ssid or "—"),
        ("RSSI", f"{dev.rssi} dBm" if dev.rssi is not None else "—"),
        ("FW", dev.fw_version or "—"),
        ("Seen", dev.last_seen.astimezone().strftime("%H:%M:%S")),
    ]
    y = 184
    for label, value in rows:
        draw.text((x, y), f"{label}:", font=font(20), fill=BLACK)
        draw.text((x + 100, y), str(value), font=font(20), fill=BLACK)
        y += 38
    return img


def _maybe_invert(img: Image.Image) -> Image.Image:
    return img.point(lambda p: WHITE if p == BLACK else BLACK) if INVERT else img


def to_bmp(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    _maybe_invert(img).save(buf, format="BMP")
    return buf.getvalue()


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    _maybe_invert(img).convert("L").save(buf, format="PNG")
    return buf.getvalue()
