"""Tests for the JSON settings store (src/config.py).

Run: python3 tests/test_config.py

Everything runs in a temp dir; the real ~/.ncs-smlauncher is never touched.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

import config  # noqa: E402


def main():
    tmp = tempfile.mkdtemp(prefix="ncs-config-test-")
    path = os.path.join(tmp, "nested", "settings.json")

    print("1) missing file falls back to defaults")
    cfg = config.load_config(path)
    assert cfg == config.default_config(), cfg
    assert cfg["keymap"] == config.DEFAULT_KEYMAP, cfg["keymap"]
    assert not os.path.exists(path)
    print("   defaults OK ->", sorted(cfg))

    print("2) save/load round-trips")
    cfg["library_folder"] = "/Users/giathinh/Music/NCS"
    cfg["show_hints"] = False
    cfg["keymap"]["play_pause"] = "P"
    cfg["custom_future_key"] = {"nested": [1, 2, 3]}
    assert config.save_config(cfg, path) is True
    assert os.path.exists(path)
    back = config.load_config(path)
    assert back == cfg, (back, cfg)
    assert back["keymap"]["play_pause"] == "P"
    print("   round-trip OK -> library_folder =", back["library_folder"])

    print("3) corrupt JSON falls back to defaults silently")
    bad = os.path.join(tmp, "bad.json")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write("{not json at all,,,")
    recovered = config.load_config(bad)
    assert recovered == config.default_config(), recovered

    print("3b) non-dict JSON falls back too")
    for payload in ("[1, 2, 3]", '"just a string"', "42", "null"):
        p = os.path.join(tmp, "nd.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(payload)
        got = config.load_config(p)
        assert got == config.default_config(), (payload, got)
    print("   corrupt + non-dict OK")

    print("4) unknown keys are preserved (forward compat)")
    future = os.path.join(tmp, "future.json")
    with open(future, "w", encoding="utf-8") as fh:
        json.dump({"schema_version": 7, "quantum_mode": True,
                   "keymap": {"future_action": "Z"}}, fh)
    got = config.load_config(future)
    assert got["quantum_mode"] is True, got
    assert got["schema_version"] == 7, got
    assert got["keymap"]["future_action"] == "Z", got["keymap"]
    assert got["keymap"]["quit"] == "Q", got["keymap"]
    print("   preserved OK ->", sorted(got))

    print("5) save is atomic and leaves no .tmp behind")
    assert config.save_config(cfg, path) is True
    assert not os.path.exists(path + ".tmp"), "temp file left behind"
    leftovers = [n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")]
    assert not leftovers, leftovers
    # The write really happened (not a silent no-op returning True).
    with open(path, "r", encoding="utf-8") as fh:
        assert json.load(fh)["library_folder"] == "/Users/giathinh/Music/NCS"
    print("   atomic OK, dir =", sorted(os.listdir(os.path.dirname(path))))

    print("5b) save failure returns False instead of raising")
    unwritable = os.path.join(path, "settings.json")  # parent is a file
    assert config.save_config(cfg, unwritable) is False
    print("   OSError handled OK")

    print("6) default_config() never hands out a shared object")
    a = config.default_config()
    b = config.default_config()
    assert a is not b
    assert a == b
    a["keymap"]["quit"] = "Z"
    a["show_hints"] = False
    assert b["keymap"]["quit"] == "Q", b["keymap"]
    assert b["show_hints"] is True
    assert config.DEFAULT_CONFIG["keymap"] == {}, config.DEFAULT_CONFIG
    assert config.DEFAULT_KEYMAP["quit"] == "Q"
    assert config.default_config()["keymap"] == config.DEFAULT_KEYMAP
    print("   no shared state OK")

    print("7) module constants point at the home directory")
    assert config.CONFIG_DIR.endswith(".ncs-smlauncher"), config.CONFIG_DIR
    assert config.CONFIG_PATH.endswith("settings.json"), config.CONFIG_PATH
    assert os.path.dirname(config.CONFIG_PATH) == config.CONFIG_DIR
    print("   CONFIG_DIR =", config.CONFIG_DIR)

    print("\nALL CONFIG TESTS PASSED")


if __name__ == "__main__":
    main()
