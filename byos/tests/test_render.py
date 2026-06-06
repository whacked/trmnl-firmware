import io

from PIL import Image

from protocol import DeviceReport
from state import StateStore
from render import RenderContext, default_render, to_bmp, to_png, WIDTH, HEIGHT


def _device():
    s = StateStore(io.StringIO().name if False else "/tmp/byos_render_test_state.json")
    return s.upsert_report(DeviceReport(mac="44:1B:F6:81:A2:80", battery_voltage=4.05,
                                        rssi=-57, wifi_ssid="huanghome", fw_version="1.8.5",
                                        model="og"), tags=["desk"])


def test_default_render_returns_1bit_canvas():
    img = default_render(RenderContext(device=_device()))
    assert img.mode == "1"
    assert img.size == (WIDTH, HEIGHT)


def test_to_bmp_is_exact_firmware_size():
    img = default_render(RenderContext(device=_device()))
    data = to_bmp(img)
    assert data[:2] == b"BM"
    assert len(data) == 48062  # matches firmware bmp.cpp validator


def test_to_png_decodes():
    img = default_render(RenderContext(device=_device()))
    png = to_png(img)
    assert Image.open(io.BytesIO(png)).size == (WIDTH, HEIGHT)
