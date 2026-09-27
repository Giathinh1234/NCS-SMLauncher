"""Headless checks for the safe bulk library move.

Run:  python3 tests/test_library_ops.py

Every case here is one that can destroy a real library, so each is exercised
against real temp directories rather than mocks: a destination nested inside
the source (walks its own output forever), a same-named destination (silent
overwrite of a track the user already had), and a dry run that must not touch
disk. The nested subfolder layout must survive the move.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, "/Users/giathinh/ncs-music-launcher/src")

from library_ops import AUDIO_EXT, execute_move, plan_move

_num = [0]


def check(label, condition, detail=""):
    _num[0] += 1
    assert condition, "FAILED #%d %s %s" % (_num[0], label, detail)
    print("  %2d. ok  %s%s" % (_num[0], label, (" -- " + detail) if detail else ""))


def build_tree(root):
    """src/a.mp3, src/nested/b.mp3, src/notes.txt, with known bytes."""
    os.makedirs(os.path.join(root, "src", "nested"))
    with open(os.path.join(root, "src", "a.mp3"), "wb") as fh:
        fh.write(b"AAA-audio-payload")
    with open(os.path.join(root, "src", "nested", "b.mp3"), "wb") as fh:
        fh.write(b"BBB-nested-audio")
    with open(os.path.join(root, "src", "notes.txt"), "wb") as fh:
        fh.write(b"not audio")


def tree_state(root):
    """Every file under root as {relpath: bytes}."""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(dirpath, name)
            out[os.path.relpath(p, root)] = open(p, "rb").read()
    return out


def main():
    tmp = tempfile.mkdtemp(prefix="ncs_libops_")
    try:
        src_dir = os.path.join(tmp, "src")
        dst_dir = os.path.join(tmp, "dst")
        build_tree(tmp)

        print("\n1. plan_move contents")
        plan = plan_move(src_dir, dst_dir)
        rel_dsts = sorted(os.path.relpath(d, dst_dir) for _s, d in plan)
        check("plan has exactly 2 audio entries", len(plan) == 2, "got %d" % len(plan))
        check("plan preserves the nested/ layout",
              rel_dsts == sorted(["a.mp3", os.path.join("nested", "b.mp3")]),
              str(rel_dsts))
        check("notes.txt is excluded",
              not any(d.endswith("notes.txt") for _s, d in plan))
        check("all sources exist", all(os.path.isfile(s) for s, _d in plan))
        check("plan is deterministic", plan == plan_move(src_dir, dst_dir))

        print("\n2. dry run touches nothing")
        result = execute_move(plan, dry_run=True)
        check("dry run reports (2, 0, 0)", result == (2, 0, 0), str(result))
        check("dry run moved no files", not os.path.exists(dst_dir))
        check("dry run left the source intact", len(tree_state(src_dir)) == 3)
        check("dry run did not modify a.mp3",
              open(os.path.join(src_dir, "a.mp3"), "rb").read() == b"AAA-audio-payload")

        print("\n3. real move")
        result = execute_move(plan)
        check("real run reports (2, 0, 0)", result == (2, 0, 0), str(result))
        check("a.mp3 landed at the top level",
              open(os.path.join(dst_dir, "a.mp3"), "rb").read() == b"AAA-audio-payload")
        check("b.mp3 landed under nested/",
              open(os.path.join(dst_dir, "nested", "b.mp3"), "rb").read()
              == b"BBB-nested-audio")
        check("notes.txt stayed behind",
              os.path.isfile(os.path.join(src_dir, "notes.txt")))
        check("audio files left the source",
              not os.path.isfile(os.path.join(src_dir, "a.mp3"))
              and not os.path.isfile(os.path.join(src_dir, "nested", "b.mp3")))

        print("\n4. pre-existing destination is skipped, not clobbered")
        clash_src = os.path.join(tmp, "clash_src")
        os.makedirs(clash_src)
        with open(os.path.join(clash_src, "a.mp3"), "wb") as fh:
            fh.write(b"NEW-source-version")
        clash_dst = os.path.join(tmp, "clash_dst")
        os.makedirs(clash_dst)
        with open(os.path.join(clash_dst, "a.mp3"), "wb") as fh:
            fh.write(b"PRE-EXISTING-destination-version")
        clash_plan = plan_move(clash_src, clash_dst)
        result = execute_move(clash_plan)
        check("clash run reports (0, 1, 0)", result == (0, 1, 0), str(result))
        check("existing destination bytes are unchanged",
              open(os.path.join(clash_dst, "a.mp3"), "rb").read()
              == b"PRE-EXISTING-destination-version")
        check("skipped source file still in place",
              os.path.isfile(os.path.join(clash_src, "a.mp3")))

        print("\n5. unsafe / meaningless requests return []")
        check("src == dst returns []", plan_move(src_dir, src_dir) == [])
        check("dst inside src returns []",
              plan_move(src_dir, os.path.join(src_dir, "inside")) == [])
        check("dst deeply inside src returns []",
              plan_move(src_dir, os.path.join(src_dir, "a", "b", "c")) == [])
        check("missing src returns []",
              plan_move(os.path.join(tmp, "does_not_exist"), dst_dir) == [])
        check("empty src returns []", plan_move("", dst_dir) == [])
        check("empty dst returns []", plan_move(src_dir, "") == [])
        check("src that is a file returns []",
              plan_move(os.path.join(src_dir, "notes.txt"), dst_dir) == [])

        print("\n6. extension filtering")
        filt_dir = os.path.join(tmp, "filt_src")
        os.makedirs(filt_dir)
        for name in ("keep.mp3", "keep2.FLAC", "drop.txt", "drop.jpg"):
            with open(os.path.join(filt_dir, name), "wb") as fh:
                fh.write(b"x")
        filt = plan_move(filt_dir, os.path.join(tmp, "filt_dst"))
        names = sorted(os.path.basename(s) for s, _d in filt)
        check("only audio extensions planned (case-insensitive)",
              names == ["keep.mp3", "keep2.FLAC"], str(names))
        check("AUDIO_EXT contains .mp3 and .flac",
              ".mp3" in AUDIO_EXT and ".flac" in AUDIO_EXT)

        print("\n7. empty plan is a no-op")
        result = execute_move([])
        check("empty plan reports (0, 0, 0)", result == (0, 0, 0), str(result))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n... TESTS PASSED")


if __name__ == "__main__":
    main()
