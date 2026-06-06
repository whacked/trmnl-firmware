import importlib

from starlette.testclient import TestClient


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("BYOS_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("BYOS_INVENTORY", str(tmp_path / "inv.yaml"))
    (tmp_path / "inv.yaml").write_text(
        "groups:\n  desk:\n    members: ['44:1B:F6:81:A2:80']\n    refresh_rate: 1234\n"
    )
    import server
    importlib.reload(server)
    return TestClient(server.app)


def test_setup_returns_200_and_keys(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/api/setup", headers={"ID": "44:1B:F6:81:A2:80", "Model": "og"})
    j = r.json()
    assert j["status"] == 200
    assert j["friendly_id"] == "DEV001"
    assert j["image_url"].endswith("/current.bmp?mac=44:1B:F6:81:A2:80")


def test_display_uses_group_refresh_rate(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/api/display", headers={"ID": "44:1B:F6:81:A2:80",
                                       "Battery-Voltage": "4.05", "RSSI": "-57"})
    j = r.json()
    assert j["refresh_rate"] == 1234         # from inventory group "desk"
    assert j["full_refresh_every"] == 16     # global default (no group/renderer override)


def test_current_bmp_size(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    c.get("/api/setup", headers={"ID": "44:1B:F6:81:A2:80"})
    r = c.get("/current.bmp", params={"mac": "44:1B:F6:81:A2:80"})
    assert r.headers["content-type"] == "image/bmp"
    assert len(r.content) == 48062


def test_log_204_and_recorded(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    import server as server_mod
    r = c.post("/api/log", headers={"ID": "44:1B:F6:81:A2:80"},
               json={"log_array": [{"msg": "hello world"}]})
    assert r.status_code == 204
    dev = server_mod.STATE.get("44:1B:F6:81:A2:80")
    assert dev is not None and dev.last_log  # the line was recorded


def test_pending_full_refresh_applies_once(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    import server
    server.STATE.upsert_report(server.parse_report({"ID": "44:1B:F6:81:A2:80"}), tags=[])
    server.STATE.queue_command("44:1B:F6:81:A2:80", force_full_refresh=True)
    r1 = c.get("/api/display", headers={"ID": "44:1B:F6:81:A2:80"})
    assert r1.json()["full_refresh_every"] == 1   # consumed
    r2 = c.get("/api/display", headers={"ID": "44:1B:F6:81:A2:80"})
    assert r2.json()["full_refresh_every"] == 16  # cleared -> back to default
