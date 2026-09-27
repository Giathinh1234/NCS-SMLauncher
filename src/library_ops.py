"""Safe bulk moves for a music library.

Flattening a messy Downloads-style tree into one chosen folder is the kind of
operation that eats a library if it is done naively: a destination inside the
source recurses forever, and a same-named destination silently overwrites a
track the user already had. Both are refused here, and a move whose byte count
does not survive the transfer is rolled back rather than reported as success.
"""

import os
import shutil

AUDIO_EXT = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".wma")


def _is_inside(child, parent):
    """True when `child` is `parent` itself or lives underneath it."""
    child = os.path.abspath(os.fspath(child))
    parent = os.path.abspath(os.fspath(parent))
    return child == parent or child.startswith(parent + os.sep)


def plan_move(src_dir, dst_dir, extensions=AUDIO_EXT):
    """Return ordered (src, dst) pairs for every audio file under `src_dir`.

    The relative sub-layout is preserved under `dst_dir`. Returns [] for every
    unsafe or meaningless request rather than raising: a non-directory source,
    an empty path, source == destination, or a destination nested inside the
    source (which would walk its own output forever).
    """
    if not src_dir or not dst_dir:
        return []
    src_dir = os.path.abspath(os.fspath(src_dir))
    dst_dir = os.path.abspath(os.fspath(dst_dir))
    if not os.path.isdir(src_dir):
        return []
    if src_dir == dst_dir:
        return []
    if _is_inside(dst_dir, src_dir):
        return []

    exts = {str(e).lower() for e in extensions}
    plan = []
    for root, dirs, files in os.walk(src_dir):
        dirs.sort()
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() not in exts:
                continue
            src = os.path.join(root, name)
            dst = os.path.join(dst_dir, os.path.relpath(src, src_dir))
            plan.append((src, dst))
    return plan


def execute_move(plan, dry_run=False):
    """Run a plan from `plan_move`. Returns (moved, skipped, failed).

    An existing destination is counted as skipped and left untouched -- never
    overwritten. `dry_run` counts what *would* move without writing anything.
    """
    moved = skipped = failed = 0
    for src, dst in plan:
        try:
            if os.path.exists(dst):
                skipped += 1
                continue
            if dry_run:
                moved += 1
                continue
            parent = os.path.dirname(dst)
            if parent:
                os.makedirs(parent, exist_ok=True)
            size_before = os.path.getsize(src)
            shutil.move(src, dst)
            if os.path.getsize(dst) != size_before:
                # Truncated or partial transfer: put the original back and
                # report the failure instead of a silent data loss.
                os.replace(dst, src)
                failed += 1
            else:
                moved += 1
        except OSError:
            failed += 1
    return moved, skipped, failed
