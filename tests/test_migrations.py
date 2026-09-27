"""Tests for settings migrations (src/migrations.py).

Run: python3 tests/test_migrations.py

Each case writes a real settings.json in a temp dir and goes through
config.load_config / save_config, so the migration is exercised on the same
path the app actually uses.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import config  # noqa: E402
import migrations  # noqa: E402
from config import DEFAULT_KEYMAP  # noqa: E402


def write(tmp, name, payload):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


def main():
    tmp = tempfile.mkdtemp(prefix="ncs-migrations-test-")
    print("schema:", migrations.SCHEMA_VERSION, "| steps:", sorted(migrations.MIGRATIONS))

    print("1) a legacy v0 config keeps its values and gains the new bindings")
    legacy = write(tmp, "legacy.json", {
        "schema_version": 0,
        "library_folder": "/Users/giathinh/Music/NCS",
        "keymap": {"play_pause": "RETURN", "quit": "ESCAPE"},
    })
    loaded = config.load_config(legacy)
    out = migrations.migrate(loaded)
    assert out["schema_version"] == migrations.SCHEMA_VERSION
    assert out["library_folder"] == "/Users/giathinh/Music/NCS"
    assert out["keymap"]["play_pause"] == "RETURN", "user binding was clobbered"
    assert out["keymap"]["quit"] == "ESCAPE", "user binding was clobbered"
    for action, key_name in DEFAULT_KEYMAP.items():
        assert action in out["keymap"], action
        if action not in ("play_pause", "quit"):
            assert out["keymap"][action] == key_name, action
    print("   kept 2 custom bindings, filled in", len(DEFAULT_KEYMAP) - 2, "more")

    print("2) a minimal v0 config gets full defaults")
    minimal = write(tmp, "minimal.json", {"schema_version": 0})
    out = migrations.migrate(config.load_config(minimal))
    assert out["schema_version"] == migrations.SCHEMA_VERSION
    assert out["keymap"] == DEFAULT_KEYMAP, out["keymap"]
    assert out["library_folder"] == ""
    assert out["show_hints"] is True
    assert out["easter_eggs"] is True
    print("   defaults filled ->", sorted(out))

    print("3) a config that is already current is untouched")
    current = config.default_config()
    current["schema_version"] = migrations.SCHEMA_VERSION
    out = migrations.migrate(current)
    assert out == current, (out, current)
    print("   byte-identical to input")

    print("4) a user's library folder is never lost")
    for folder in ("/Users/giathinh/Music", "", "/Volumes/External/NCS"):
        p = write(tmp, "folder.json", {"library_folder": folder, "schema_version": 0})
        out = migrations.migrate(config.load_config(p))
        assert out["library_folder"] == folder, (folder, out["library_folder"])
    print("   preserved for 3 different folder values")

    print("5) migrating twice changes nothing (idempotent)")
    for name in ("legacy.json", "minimal.json", "folder.json"):
        p = os.path.join(tmp, name)
        once = migrations.migrate(config.load_config(p))
        twice = migrations.migrate(once)
        thrice = migrations.migrate(twice)
        assert once == twice, (name, once, twice)
        assert twice == thrice, name
    print("   migrate(migrate(x)) == migrate(x) for every fixture")

    print("6) a stale keymap entry is dropped, a valid one survives")
    p = write(tmp, "stale.json", {
        "schema_version": 0,
        "keymap": {"removed_action": "X", "play_pause": "P", "open_torrent": "B"},
        "unknown_top_level": "keep me",
    })
    out = migrations.migrate(config.load_config(p))
    assert "removed_action" not in out["keymap"], out["keymap"]
    assert out["keymap"]["play_pause"] == "P"
    assert out["keymap"]["open_torrent"] == "B"
    assert out["unknown_top_level"] == "keep me", "unknown top-level key was dropped"
    print("   dropped removed_action, kept play_pause/open_torrent + unknown key")

    print("7) a config from a future build is NOT downgraded")
    future = {"schema_version": migrations.SCHEMA_VERSION + 5,
              "library_folder": "/future", "keymap": {"play_pause": "P"}}
    out = migrations.migrate(future)
    assert out == future, (out, future)
    assert out["schema_version"] == migrations.SCHEMA_VERSION + 5, out["schema_version"]
    assert migrations.migrate(out) == future
    print("   schema_version", future["schema_version"], "left alone")

    print("8) garbage and missing schema_version are treated as v0")
    for payload in ({"library_folder": "/x"}, {"schema_version": "banana"},
                    {"schema_version": None}, {"schema_version": 0},
                    {"schema_version": 1.5}):
        out = migrations.migrate(payload)
        assert out["schema_version"] == migrations.SCHEMA_VERSION, (payload, out)
        assert out["keymap"] == DEFAULT_KEYMAP, payload
        assert out["show_hints"] is True, payload
        # A supplied folder survives; an absent one gets the default.
        expected = payload.get("library_folder", "")
        assert out["library_folder"] == expected, (payload, out["library_folder"])
    print("   all five forms migrated to the current schema")

    print("9) migrate() never mutates its input")
    original = {"schema_version": 0, "keymap": {"quit": "ESCAPE"}}
    snapshot = json.dumps(original, sort_keys=True)
    out = migrations.migrate(original)
    assert json.dumps(original, sort_keys=True) == snapshot, original
    out["keymap"]["quit"] = "Z"
    assert original["keymap"]["quit"] == "ESCAPE", "caller's dict was mutated"
    print("   input left untouched")

    print("10) migrated config survives a save/load round-trip")
    p = os.path.join(tmp, "roundtrip.json")
    legacy2 = write(tmp, "legacy2.json", {
        "schema_version": 0, "library_folder": "/Users/giathinh/Music/NCS",
        "keymap": {"play_pause": "P", "ghost": "X"}})
    out = migrations.migrate(config.load_config(legacy2))
    assert config.save_config(out, p) is True
    reloaded = config.load_config(p)
    assert reloaded == out, (reloaded, out)
    assert reloaded["schema_version"] == migrations.SCHEMA_VERSION
    assert "ghost" not in reloaded["keymap"]
    print("   reloaded identical, no .tmp left:",
          [n for n in os.listdir(tmp) if n.endswith(".tmp")] == [])

    print("\nALL MIGRATIONS TESTS PASSED")


if __name__ == "__main__":
    main()
