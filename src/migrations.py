"""Ordered, idempotent migrations for the settings file.

`config.load_config` reads a settings.json written by any past build. This
module brings an old one up to the current shape. Two rules make that safe:

* Ordered - MIGRATIONS is keyed by the schema version being upgraded FROM, so
  migrate() walks 0 -> 1 -> 2 ... in sequence and each step only has to know
  about the version it produces.
* Idempotent - migrate(migrate(x)) == migrate(x). The app calls migrate on
  every load, so running it twice (or on a file that is already current) must
  not change anything, and must never destroy a user's library_folder.
"""
import copy

from config import DEFAULT_KEYMAP

SCHEMA_VERSION = 1


def _migrate_v0_to_v1(cfg):
    """v0 (pre-release) -> v1: a complete, validated keymap.

    Adds any action added since v0, drops keymap entries for actions that no
    longer exist, and fills in the settings flags. Unknown top-level keys are
    kept: a newer build may have written them and we do not know what they
    mean, only that they are not ours to delete.
    """
    out = copy.deepcopy(cfg)

    keymap = out.get("keymap")
    if not isinstance(keymap, dict):
        keymap = {}
    for action, key_name in DEFAULT_KEYMAP.items():
        keymap.setdefault(action, key_name)
    for action in list(keymap):
        if action not in DEFAULT_KEYMAP:
            del keymap[action]
    out["keymap"] = keymap

    out.setdefault("library_folder", "")
    out.setdefault("show_hints", True)
    out.setdefault("easter_eggs", True)
    out["schema_version"] = 1
    return out


# FROM version -> upgrade step. A gap in this table is tolerated: migrate()
# just tags the config and moves on, so an intermediate release can ship
# without a migration.
MIGRATIONS = {
    0: _migrate_v0_to_v1,
}


def _as_version(value):
    """Coerce a stored schema_version to an int; anything odd means 0.

    Deliberately strict: a bool, a fractional float, or a non-numeric string
    is a malformed tag, not a version. int() alone would truncate 1.5 to 1 and
    then treat the file as already current, skipping migration entirely.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else 0
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("+-").isdigit():
            return int(text)
    return 0


def migrate(cfg):
    """Return a migrated copy of `cfg`. The input is never modified."""
    out = copy.deepcopy(cfg) if isinstance(cfg, dict) else {}
    current = _as_version(out.get("schema_version", 0))

    if current > SCHEMA_VERSION:
        # Written by a newer build. It knows things we do not; applying our
        # older steps would corrupt it, so hand it back untouched.
        return out

    while current < SCHEMA_VERSION:
        step = MIGRATIONS.get(current)
        if step is not None:
            out = step(out)
        else:
            out["schema_version"] = current + 1
        current = _as_version(out.get("schema_version", current + 1))

    out["schema_version"] = SCHEMA_VERSION
    return out
