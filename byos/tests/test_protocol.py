from protocol import DeviceReport, parse_report


def test_parse_full_display_headers():
    headers = {
        "ID": "44:1B:F6:81:A2:80",
        "Access-Token": "byos-x",
        "Model": "og",
        "FW-Version": "1.8.5",
        "Battery-Voltage": "4.05",
        "RSSI": "-57",
        "WiFi-SSID": "huang%20home",   # firmware percent-encodes
        "WiFi-Band": "2.4",
        "Refresh-Rate": "900",
        "Update-Source": "scheduled",
        "Wake-Time": "1234",
        "Image-Cached": "true",
        "Width": "800",
        "Height": "480",
    }
    r = parse_report(headers)
    assert isinstance(r, DeviceReport)
    assert r.mac == "44:1B:F6:81:A2:80"
    assert r.battery_voltage == 4.05
    assert r.rssi == -57
    assert r.wifi_ssid == "huang home"          # percent-decoded
    assert r.image_cached is True
    assert r.width == 800 and r.height == 480


def test_parse_is_case_insensitive_and_sparse():
    r = parse_report({"id": "AA:BB:CC:DD:EE:FF", "fw-version": "1.0"})
    assert r.mac == "AA:BB:CC:DD:EE:FF"
    assert r.fw_version == "1.0"
    assert r.battery_voltage is None
    assert r.rssi is None


def test_parse_tolerates_garbage_numbers():
    r = parse_report({"ID": "A", "Battery-Voltage": "n/a", "RSSI": ""})
    assert r.battery_voltage is None
    assert r.rssi is None
