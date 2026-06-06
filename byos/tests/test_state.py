import os
import tempfile

from protocol import DeviceReport
from state import StateStore


def _store():
    d = tempfile.mkdtemp()
    return StateStore(os.path.join(d, "state.json"))


def _report(mac, **kw):
    return DeviceReport(mac=mac, **kw)


def test_upsert_assigns_sequential_ids_and_keeps_them():
    s = _store()
    a = s.upsert_report(_report("AA:AA:AA:AA:AA:AA"), tags=["x"])
    b = s.upsert_report(_report("BB:BB:BB:BB:BB:BB"), tags=[])
    assert a.friendly_id == "DEV001"
    assert b.friendly_id == "DEV002"
    a2 = s.upsert_report(_report("AA:AA:AA:AA:AA:AA", battery_voltage=3.9), tags=["x", "y"])
    assert a2.friendly_id == "DEV001"
    assert a2.battery_voltage == 3.9
    assert a2.tags == ["x", "y"]


def test_battery_percent():
    s = _store()
    d = s.upsert_report(_report("AA:AA:AA:AA:AA:AA", battery_voltage=3.75), tags=[])
    assert d.battery_percent() == 50  # (3.75-3.30)/0.90*100


def test_pending_take_clears():
    s = _store()
    s.upsert_report(_report("AA:AA:AA:AA:AA:AA"), tags=[])
    s.queue_command("AA:AA:AA:AA:AA:AA", force_full_refresh=True, special_function="identify")
    p = s.take_pending("AA:AA:AA:AA:AA:AA")
    assert p.force_full_refresh is True and p.special_function == "identify"
    p2 = s.take_pending("AA:AA:AA:AA:AA:AA")
    assert p2.force_full_refresh is False and p2.special_function is None


def test_group_override_and_last_log():
    s = _store()
    s.upsert_report(_report("AA:AA:AA:AA:AA:AA"), tags=["auto"])
    s.set_group_override("AA:AA:AA:AA:AA:AA", "garden")
    s.set_last_log("AA:AA:AA:AA:AA:AA", "boot ok")
    d = s.get("AA:AA:AA:AA:AA:AA")
    assert d.group_override == "garden"
    assert d.last_log == "boot ok"


def test_roundtrip_and_corrupt(tmp_path):
    p = str(tmp_path / "s.json")
    s = StateStore(p)
    s.upsert_report(_report("AA:AA:AA:AA:AA:AA"), tags=[])
    s2 = StateStore(p)
    assert s2.get("AA:AA:AA:AA:AA:AA").friendly_id == "DEV001"
    open(p, "w").write("{ not json")
    s3 = StateStore(p)
    assert s3.latest() is None
