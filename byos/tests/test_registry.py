import types

import registry
from registry import (renderer, mac, tag, model, glob, predicate,
                      resolve, resolve_config, set_default, RenderConfig)


class Dev:
    def __init__(self, mac="44:1B:F6:81:A2:80", model="og", tags=None):
        self.mac = mac
        self.model = model
        self.tags = tags or []


def setup_function(_):
    registry.reset()  # test helper: clear registrations + default


def test_priority_and_fallback():
    set_default(lambda ctx: "DEFAULT")

    @renderer(match=tag("garden"), priority=10)
    def g(ctx):
        return "GARDEN"

    @renderer(match=model("og"), priority=1)
    def o(ctx):
        return "OG"

    assert resolve(Dev(tags=["garden"])).fn(None) == "GARDEN"   # higher priority wins
    assert resolve(Dev(tags=[])).fn(None) == "OG"               # model match
    assert resolve(Dev(model="x", tags=[])).fn(None) == "DEFAULT"  # nothing matches


def test_matchers():
    assert mac("AA:BB:CC:DD:EE:FF")(Dev(mac="aa:bb:cc:dd:ee:ff"))      # case-insensitive
    assert tag("garden")(Dev(tags=["garden"]))
    assert model("OG")(Dev(model="og"))
    assert glob("44:1B:*")(Dev(mac="44:1B:F6:81:A2:80"))
    assert predicate(lambda d: d.rssi if hasattr(d, "rssi") else True)(Dev())


def test_resolve_config_precedence():
    # pending > group > renderer default > global
    pending = {"full_refresh_every": 1}
    groups = [RenderConfig(refresh_rate=3600)]
    rcfg = RenderConfig(refresh_rate=900, full_refresh_every=16, special_function="none")
    out = resolve_config(pending, groups, rcfg,
                         defaults=RenderConfig(refresh_rate=300, full_refresh_every=8,
                                               special_function="none"))
    assert out.full_refresh_every == 1      # from pending
    assert out.refresh_rate == 3600         # from group (beats renderer default)
    assert out.special_function == "none"   # from renderer default


def test_load_clients(tmp_path):
    set_default(lambda ctx: "DEFAULT")
    (tmp_path / "p.py").write_text(
        "from registry import renderer, tag\n"
        "@renderer(match=tag('plug'), priority=3)\n"
        "def p(ctx):\n    return 'PLUG'\n"
    )
    registry.load_clients(str(tmp_path))
    assert resolve(Dev(tags=["plug"])).fn(None) == "PLUG"
