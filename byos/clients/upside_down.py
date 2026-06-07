"""Plugin: rotate the rendered frame 180° for devices tagged 'upside-down'.

Targets the inventory group of the same name (see byos/inventory.yaml). Builds
the normal default frame, then flips it — handy for a panel mounted upside down.
"""

from PIL import Image

from registry import renderer, tag
from render import default_render


@renderer(match=tag("upside-down"), priority=10)
def upside_down(ctx):
    img = default_render(ctx)             # build the normal frame...
    return img.transpose(Image.Transpose.ROTATE_180)   # ...then flip it 180°
