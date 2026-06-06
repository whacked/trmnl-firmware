from inventory import Inventory


def _inv():
    return Inventory.model_validate({
        "groups": {
            "garden": {"members": ["A0:EA:DE:AD:BE:EF", "DC:44:*"],
                       "refresh_rate": 3600, "priority": 5},
            "desk": {"members": ["44:1B:F6:81:A2:80"], "refresh_rate": 900},
            "all_og": {"members": ["*"], "full_refresh_every": 32, "priority": -10},
        }
    })


def test_exact_and_glob_membership_case_insensitive():
    inv = _inv()
    assert set(inv.resolve_tags("a0:ea:de:ad:be:ef")) >= {"garden", "all_og"}
    assert "garden" in inv.resolve_tags("DC:44:11:22:33:44")
    assert inv.resolve_tags("99:99:99:99:99:99") == ["all_og"]


def test_settings_ordered_by_priority_desc():
    inv = _inv()
    tags = inv.resolve_tags("44:1B:F6:81:A2:80")  # desk(0) + all_og(-10)
    ordered = inv.settings_for(tags)
    assert [name for name, _ in ordered][0] == "desk"
    assert ordered[-1][0] == "all_og"


def test_missing_file_is_empty(tmp_path):
    inv = Inventory.load(str(tmp_path / "nope.yaml"))
    assert inv.groups == {}
    assert inv.resolve_tags("any") == []


def test_malformed_file_is_empty(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("groups: [this is not a mapping")
    inv = Inventory.load(str(p))
    assert inv.groups == {}
