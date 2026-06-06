"""Example plugin: a 'example'-tagged group renderer that extends the default.
Copy this file and change the matcher/drawing for your own clients."""

from PIL import ImageDraw

from registry import renderer, tag
from render import default_render, font, BLACK


@renderer(match=tag("example"), priority=10, refresh_rate=1800)
def example(ctx):
    img = default_render(ctx)            # build on the default frame
    draw = ImageDraw.Draw(img)
    draw.text((44, 430), "example group", font=font(22), fill=BLACK)
    return img
