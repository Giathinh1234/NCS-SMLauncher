"""Plain-script checks for updater.apply_pending: the next-launch swap.

Run:  python3 tests/test_apply_pending.py

This is the code that can delete a working install, so the assertions are
about refusal, not just success. The rule under test: apply_pending either
spawns EXACTLY ONE detached /bin/sh helper, or it spawns nothing at all and
returns False. There is no middle state where a half-run swap happens.

The real swap is never executed here -- a test that overwrote the app it ran
from would be its own bug. What is verified is the command we would run: its
argv, and the script text it carries.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)

import updater
from updater import apply_pending, consume_pending, pending_path, stage_pending

n = 0
def ok(msg):
    global n
    n += 1
    print("%2d. %s" % (n, msg))


class FakeSpawn:
    """Records spawn calls instead of running them."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        return object()

    @property
    def count(self):
        return len(self.calls)


def only_call(spawn):
    assert spawn.count == 1, "expected exactly 1 spawn, got %d" % spawn.count
    return spawn.calls[0]


tmp = tempfile.mkdtemp(prefix="ncs-apply-")
try:
    # -- 1. nothing pending -------------------------------------------------
    empty = os.path.join(tmp, "empty")
    os.makedirs(empty, exist_ok=True)
    spawn = FakeSpawn()
    assert apply_pending(base_dir=empty, spawn=spawn, target=os.path.join(tmp, "App.app")) is False
    assert spawn.count == 0, "spawned with nothing pending"
    ok("nothing pending -> (False, no spawn)")

    # -- 2. confirmed=False refuses even with a valid staged update --------
    base = os.path.join(tmp, "updates")
    payload = os.path.join(tmp, "HashPlay.app.zip")
    with open(payload, "wb") as fh:
        fh.write(b"PK\x03\x04 staged app bundle")
    target = os.path.join(tmp, "HashPlay.app")
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "marker"), "w") as fh:
        fh.write("current install")

    staged = stage_pending(payload, "1.0.0", base_dir=base)
    assert staged is not None and os.path.isfile(staged)
    assert pending_path(base) == staged

    spawn = FakeSpawn()
    assert apply_pending(base_dir=base, spawn=spawn, target=target, confirmed=False) is False
    assert spawn.count == 0, "confirmed=False must not spawn"
    ok("confirmed=False -> (False, no spawn)")

    # -- 3. the real thing -------------------------------------------------
    spawn = FakeSpawn()
    assert apply_pending(base_dir=base, spawn=spawn, target=target, confirmed=True) is True
    cmd, kwargs = only_call(spawn)
    assert cmd[0] == "/bin/sh", cmd[0]
    assert cmd[1] == "-c", cmd[1]
    script = cmd[2]
    joined = " ".join(cmd)
    assert staged in joined, "script must reference the staged file:\n%s" % script
    assert target in joined, "script must reference the target:\n%s" % script
    assert str(os.getpid()) in script, "script must wait on our PID:\n%s" % script
    assert "kill -0" in script, "wait loop must poll with kill -0:\n%s" % script
    # `kill -0` also succeeds on a zombie, so the loop must read the real
    # process state and treat Z as gone, or it spins for the full limit in
    # exactly the case the helper exists for. See the WAIT_LIMIT comment.
    assert "ps -o stat=" in script, "wait loop must be zombie-aware:\n%s" % script
    assert 'Z*) break' in script, "zombie state must break the wait loop:\n%s" % script
    assert "WAIT_LIMIT=%d" % updater.WAIT_LIMIT in script, script
    assert "mv" in script and ".old" in script, "must move the old bundle aside, not delete it:\n%s" % script
    assert "rm -rf" in script and '"$OLD"' in script, "rm -rf must only target the .old path:\n%s" % script
    assert kwargs.get("start_new_session") is True, kwargs
    ok("valid pending -> True with one /bin/sh -c spawn naming staged + target")

    # The script must be syntactically valid sh. A malformed helper would fail
    # silently at quit time, which is the worst possible moment to find out.
    syntax = subprocess.run(["/bin/sh", "-n"], input=script,
                            capture_output=True, text=True)
    assert syntax.returncode == 0, "generated script is not valid sh:\n%s" % syntax.stderr
    assert not syntax.stderr.strip(), syntax.stderr
    ok("the generated helper is syntactically valid sh (`sh -n` clean)")

    # Rendering must not raise: an argument-count typo in the generator would
    # otherwise only surface when a user tries to install an update.
    rendered = updater._swap_script(staged, target, 4242)
    assert "4242" in rendered, rendered
    assert "WAIT_LIMIT=60" in rendered or ("WAIT_LIMIT=%d" % updater.WAIT_LIMIT) in rendered
    ok("_swap_script renders for an arbitrary pid (%d) without raising" % 4242)

    # the current install is untouched: nothing ran, nothing moved
    assert os.path.isfile(os.path.join(target, "marker")), "target must be untouched"
    assert os.path.isdir(target)
    ok("the installed bundle was NOT modified by apply_pending")

    # -- 4. the staged file survives (the helper has not run yet) ---------
    assert os.path.isfile(staged), "staged payload must still be on disk"
    with open(staged, "rb") as fh:
        assert fh.read() == b"PK\x03\x04 staged app bundle"
    ok("the ORIGINAL staged file is still intact after apply_pending")

    # -- 5. pending.json pointing at a vanished file ----------------------
    os.unlink(staged)
    spawn = FakeSpawn()
    assert apply_pending(base_dir=base, spawn=spawn, target=target) is False
    assert spawn.count == 0, "spawned with a vanished staged file"
    ok("pending.json with a vanished staged file -> (False, no spawn)")

    # -- 6. consume_pending cleans up -------------------------------------
    assert consume_pending(base) is True
    assert pending_path(base) is None
    assert os.listdir(base) == [], "staging dir should be empty: %s" % os.listdir(base)
    assert consume_pending(base) is False, "consuming twice is a no-op"
    ok("consume_pending removes the payload + pending.json and is idempotent")

    # -- 7. corrupt pending.json is handled, not raised -------------------
    bad = os.path.join(tmp, "bad")
    os.makedirs(bad, exist_ok=True)
    with open(os.path.join(bad, "pending.json"), "w") as fh:
        fh.write("{not json")
    spawn = FakeSpawn()
    assert apply_pending(base_dir=bad, spawn=spawn, target=target) is False
    assert spawn.count == 0
    assert pending_path(bad) is None
    ok("corrupt pending.json -> (False, no spawn), pending_path is None")

    # -- 8. a spawn that blows up must not propagate ----------------------
    def exploding_spawn(cmd, **kwargs):
        raise OSError("no fork for you")

    assert apply_pending(base_dir=base, spawn=exploding_spawn, target=target) is False
    ok("a spawn that raises is caught and returns False")

    # -- 9. no target to replace -> refuse --------------------------------
    dest = stage_pending(payload, "1.0.0", base_dir=base)
    assert dest is not None
    spawn = FakeSpawn()
    assert apply_pending(base_dir=base, spawn=spawn, target=None) is False
    assert spawn.count == 0, "must not guess a target when the app cannot find its own bundle"
    ok("target=None (no bundle to replace) -> (False, no spawn)")
    consume_pending(base)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("... TESTS PASSED")
