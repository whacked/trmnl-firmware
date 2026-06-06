"""Built-in default renderer registration (auto-loaded). Sets the fallback used
when no other plugin matches a device."""

from registry import set_default
from render import default_render

set_default(default_render)
