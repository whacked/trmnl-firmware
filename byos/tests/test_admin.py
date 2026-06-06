import importlib

from starlette.testclient import TestClient


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("BYOS_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("BYOS_INVENTORY", str(tmp_path / "inv.yaml"))
    (tmp_path / "inv.yaml").write_text("groups:\n  garden:\n    members: ['*']\n")
    import server
    importlib.reload(server)
    return TestClient(server.app), server


def test_admin_lists_devices(tmp_path, monkeypatch):
    c, server = _client(tmp_path, monkeypatch)
    c.get("/api/display", headers={"ID": "44:1B:F6:81:A2:80", "Battery-Voltage": "4.05"})
    html = c.get("/admin").text
    assert "DEV001" in html
    assert "44:1B:F6:81:A2:80" in html
    assert "/admin/thumb/44:1B:F6:81:A2:80.png" in html


def test_admin_thumb_is_png(tmp_path, monkeypatch):
    c, server = _client(tmp_path, monkeypatch)
    c.get("/api/setup", headers={"ID": "44:1B:F6:81:A2:80"})
    r = c.get("/admin/thumb/44:1B:F6:81:A2:80.png")
    assert r.status_code == 200
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_admin_post_queues_force_full_and_group(tmp_path, monkeypatch):
    c, server = _client(tmp_path, monkeypatch)
    c.get("/api/setup", headers={"ID": "44:1B:F6:81:A2:80"})
    r = c.post("/admin/device/44:1B:F6:81:A2:80",
               data={"force_full_refresh": "on", "special_function": "identify",
                     "group": "garden"},
               follow_redirects=False)
    assert r.status_code in (302, 303)
    dev = server.STATE.get("44:1B:F6:81:A2:80")
    assert dev.group_override == "garden"
    assert dev.pending.force_full_refresh is True
    assert dev.pending.special_function == "identify"
