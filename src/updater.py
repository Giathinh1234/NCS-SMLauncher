# -*- coding: utf-8 -*-
"""Opt-in, checksum-verified self-updater for HashPlay (NCS-SMLauncher).

Design rules. All four are deliberate and non-negotiable:

1. NEVER replace the running binary in place. The new build is downloaded,
   verified and STAGED under ``~/.ncs-smlauncher/updates/``. The actual swap
   happens on the NEXT launch, from a detached ``/bin/sh`` helper, after the
   current process has exited. Overwriting the live .app while it is running
   is how desktop apps get bricked.
2. NEVER automatic. The user asks to check, and separately asks to install.
   Nothing here runs unless it is called.
3. NEVER unverified. When the release metadata carries a size and/or a
   SHA-256 digest, the downloaded payload must match both. A mismatch is
   a hard failure, not a warning.
4. NEVER touch anything outside the app bundle it was launched from.

Everything degrades gracefully: no network, a bad payload or a missing asset
returns ``None`` / ``False``. No function in the public surface raises at the
caller -- a dead network must never take the music player down with it.
"""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

try:  # the version module ships next to this one
    import version
except Exception:  # pragma: no cover - only when running outside the bundle
    version = None


REPO = "Giathinh1234/NCS-SMLauncher"
API = "https://api.github.com/repos/{repo}/releases"
UPDATES_DIR = os.path.join(os.path.expanduser("~"), ".ncs-smlauncher", "updates")
TIMEOUT = 30

PENDING_NAME = "pending.json"
_CHUNK = 256 * 1024


# --------------------------------------------------------------------------
# version helpers
# --------------------------------------------------------------------------

def _current_version():
    """The version of the running build, or None when it cannot be read."""
    return getattr(version, "APP_VERSION", None)


def _version_tuple(text):
    """``"v1.0.0"`` -> ``(1, 0, 0)``. Unparseable -> None."""
    if not text:
        return None
    s = str(text).strip().lstrip("vV")
    if not s:
        return None
    # drop any pre-release / build suffix: "1.0.0-beta" -> "1.0.0"
    for sep in ("-", "+", "_"):
        if sep in s:
            s = s.split(sep, 1)[0]
    out = []
    for chunk in s.split("."):
        if not chunk.isdigit():
            return None
        out.append(int(chunk))
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def is_newer(candidate, current=None):
    """True when ``candidate`` is strictly newer than ``current``.

    An unparseable tag is never considered newer: a garbage tag must not be
    able to talk the app into replacing itself.
    """
    if current is None:
        current = _current_version()
    if current is None:
        return True
    a = _version_tuple(candidate)
    b = _version_tuple(current)
    if a is None:
        return False
    if b is None:
        return True
    return a > b


def _human_size(n):
    """Byte count, plain. The repr format is part of this module's contract."""
    try:
        return "%dB" % int(n)
    except (TypeError, ValueError):
        return "?"


# --------------------------------------------------------------------------
# release metadata
# --------------------------------------------------------------------------

class UpdateInfo:
    """What the updater knows about a candidate release.

    ``url``/``size``/``digest``/``name`` describe the *asset* once
    :func:`pick_asset` has run; before that they describe the release itself
    (``name`` is the release name, the rest are ``None``).
    """

    def __init__(self, version, url=None, size=None, digest=None,
                 prerelease=False, name=None, assets=None):
        self.version = version
        self.url = url
        self.size = size
        self.digest = digest
        self.prerelease = bool(prerelease)
        self.name = name
        self.assets = list(assets or [])

    def __repr__(self):
        return "<UpdateInfo %s %s prerelease=%s>" % (
            self.version, _human_size(self.size), self.prerelease)

    def copy(self, **kw):
        fields = dict(version=self.version, url=self.url, size=self.size,
                      digest=self.digest, prerelease=self.prerelease,
                      name=self.name, assets=self.assets)
        fields.update(kw)
        return UpdateInfo(**fields)

    @property
    def tag(self):
        return "v" + str(self.version).lstrip("vV")


def _asset_matches_platform(name, platform):
    """Which asset names are installable on ``platform``.

    macOS ships a zipped .app bundle or a bare ``HashPlay``; Linux ships a
    single executable. Anything else is unsupported, so the answer is False
    rather than a hopeful guess.
    """
    if not name:
        return False
    if platform == "darwin":
        return name.endswith(".app.zip") or name == "HashPlay"
    if platform == "linux":
        return "linux" in name.lower()
    return False


def _normalize_digest(digest):
    if not digest:
        return None
    d = str(digest).strip().lower()
    if d.startswith("sha256:"):
        d = d.split(":", 1)[1]
    if len(d) != 64 or any(c not in "0123456789abcdef" for c in d):
        return None
    return d


def parse_release(payload, current=None):
    """Turn one GitHub release dict into an :class:`UpdateInfo`, or None.

    None means "not an update": not a dict, a draft, no tag, or a tag that is
    not strictly newer than ``current`` (defaults to the running build).
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("draft"):
        return None
    tag = payload.get("tag_name") or payload.get("tag") or ""
    if not str(tag).strip():
        return None
    tag = str(tag).strip()
    if not is_newer(tag, current):
        return None
    assets = payload.get("assets")
    if not isinstance(assets, list):
        assets = []
    return UpdateInfo(
        version=tag.lstrip("vV"),
        url=payload.get("zipball_url") or payload.get("tarball_url"),
        size=None,
        digest=None,
        prerelease=bool(payload.get("prerelease")),
        name=payload.get("name") or tag,
        assets=assets,
    )


def pick_asset(info, platform=None):
    """Return ``info`` carrying the first asset installable on ``platform``."""
    if info is None:
        return None
    if platform is None:
        platform = sys.platform
    for asset in info.assets or []:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name") or ""
        if not _asset_matches_platform(name, platform):
            continue
        size = asset.get("size")
        try:
            size = int(size) if size is not None else None
        except (TypeError, ValueError):
            size = None
        return info.copy(url=asset.get("browser_download_url") or asset.get("url"),
                         size=size,
                         digest=_normalize_digest(asset.get("digest")),
                         name=name)
    return None


def fetch_latest(current=None, include_prerelease=True, timeout=TIMEOUT):
    """Look up the newest acceptable release. None means "no update"."""
    url = API.format(repo=REPO)
    if not include_prerelease:
        url += "?per_page=10"
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json",
                      "User-Agent": "HashPlay-updater"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None
    except Exception:
        return None

    if not isinstance(payload, list):
        return None
    for release in payload:
        info = parse_release(release, current)
        if info is None:
            continue
        if info.prerelease and not include_prerelease:
            continue
        return pick_asset(info)
    return None


# --------------------------------------------------------------------------
# download + verification
# --------------------------------------------------------------------------

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_download(path, digest=None, expected_size=None):
    """``(ok, reason)``. Never raises; a missing file is a failed check."""
    if not path or not os.path.isfile(path):
        return False, "missing file"
    if expected_size is not None:
        try:
            actual = os.path.getsize(path)
            want = int(expected_size)
        except (TypeError, ValueError, OSError):
            return False, "size check failed"
        if actual != want:
            return False, "size mismatch: %d != %d bytes" % (actual, want)
    want_digest = _normalize_digest(digest)
    if want_digest:
        try:
            actual = _sha256(path)
        except OSError:
            return False, "checksum could not be read"
        if actual != want_digest:
            return False, "checksum mismatch: %s != %s" % (actual, want_digest)
    return True, "ok"


def download(info, dest_dir=None):
    """Download ``info`` into the staging dir and verify it. Path or None."""
    if info is None or not getattr(info, "url", None):
        return None
    dest_dir = dest_dir or os.path.join(UPDATES_DIR, "downloads")
    try:
        os.makedirs(dest_dir, exist_ok=True)
    except OSError:
        return None

    name = os.path.basename(info.url.split("?")[0]) or "payload"
    final = os.path.join(dest_dir, "%s-%s" % (getattr(info, "version", "0"), name))
    tmp = final + ".part"
    req = urllib.request.Request(info.url, headers={"User-Agent": "HashPlay-updater"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp, \
                open(tmp, "wb") as out:
            shutil.copyfileobj(resp, out, _CHUNK)
        os.replace(tmp, final)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None

    ok, reason = verify_download(final, info.digest, info.size)
    if not ok:
        try:
            os.unlink(final)
        except OSError:
            pass
        return None
    return final


# --------------------------------------------------------------------------
# staging + the next-launch swap
# --------------------------------------------------------------------------

def _base(base_dir):
    return base_dir or UPDATES_DIR


def pending_meta(base_dir=None):
    """The parsed pending.json, or None."""
    path = os.path.join(_base(base_dir), PENDING_NAME)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def pending_path(base_dir=None):
    """Absolute path of the staged file, or None when nothing is pending."""
    meta = pending_meta(base_dir)
    if not meta:
        return None
    staged = meta.get("staged_as") or ""
    if not staged:
        return None
    if not os.path.isabs(staged):
        staged = os.path.join(_base(base_dir), staged)
    return staged


def stage_pending(src_path, new_version, base_dir=None):
    """Copy ``src_path`` into the staging area atomically; return dest path.

    The copy lands via a tempfile in the SAME directory and ``os.replace``,
    so ``os.replace`` is a same-filesystem rename: a crash can leave the
    staged file absent, never half-written.
    """
    if not src_path or not os.path.isfile(src_path):
        return None
    base = _base(base_dir)
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        return None

    meta_pending = pending_meta(base)
    dest = os.path.join(base, "HashPlay-%s" % new_version)
    fd, tmp = tempfile.mkstemp(prefix=".pending-", suffix=".tmp", dir=base)
    os.close(fd)
    try:
        shutil.copyfile(src_path, tmp)
        os.replace(tmp, dest)
    except (OSError, shutil.Error):
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None

    meta = {
        "version": str(new_version),
        "staged_from": os.path.abspath(src_path),
        "staged_as": dest,
        "from_version": _current_version() or "",
    }
    try:
        with open(os.path.join(base, PENDING_NAME), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2, sort_keys=True)
    except OSError:
        try:
            os.unlink(dest)
        except OSError:
            pass
        return None
    return dest


def consume_pending(base_dir=None):
    """Drop the staged file and pending.json. True when something was removed."""
    base = _base(base_dir)
    meta = pending_meta(base)
    staged = pending_path(base)
    removed = False
    if staged and os.path.isfile(staged):
        try:
            os.unlink(staged)
            removed = True
        except OSError:
            pass
    try:
        os.unlink(os.path.join(base, PENDING_NAME))
        removed = True
    except OSError:
        pass
    return removed


def _default_target():
    """The .app bundle (or executable) this process was launched from."""
    exe = getattr(sys, "frozen", False) and sys.executable or None
    if not exe:
        return None
    exe = os.path.abspath(exe)
    # dist/HashPlay.app/Contents/MacOS/HashPlay -> dist/HashPlay.app
    marker = os.sep + "Contents" + os.sep + "MacOS" + os.sep
    if marker in exe:
        bundle = exe.split(marker, 1)[0]
        if os.path.isdir(bundle):
            return bundle
    return exe


# How long the detached helper waits for the running app to exit before it
# gives up and leaves the update staged. `kill -0` also succeeds on a zombie,
# so an unbounded poll could spin forever in exactly the case it is spawned
# for. 60s is far longer than a normal quit needs.
WAIT_LIMIT = 60


def _swap_script(staged, target, pid):
    """A POSIX sh script that waits for us to die, then swaps and relaunches."""
    s = shlex.quote(str(staged))
    t = shlex.quote(str(target))
    old = t + ".old"
    return "\n".join([
        "#!/bin/sh",
        "# HashPlay staged self-update. Detached; runs after PID %d exits." % pid,
        "set -e",
        "STAGED=%s" % s,
        "TARGET=%s" % t,
        'OLD="$TARGET.old"',
        "",
        "# Wait for the running instance to exit. Never overwrite a live bundle.",
        "# The wait is BOUNDED and zombie-aware. `kill -0` alone is not enough:",
        "# it also succeeds on a zombie, i.e. a process that has already exited",
        "# but has not been reaped by its parent. Polling only that would spin",
        "# for the full limit in precisely the case the helper exists for. So we",
        "# read the real process state and treat Z as gone. If ps is unavailable",
        "# the state is empty and we fall through to waiting, which is the safe",
        "# direction to be wrong in.",
        "WAIT_LIMIT=%d" % WAIT_LIMIT,
        "waited=0",
        "while kill -0 %d 2>/dev/null; do" % pid,
        "  stat=$(ps -o stat= -p %d 2>/dev/null | tr -d ' ')" % pid,
        '  case "$stat" in Z*) break ;; esac',
        '  if [ "$waited" -ge "$WAIT_LIMIT" ]; then',
        "    echo \"HashPlay updater: PID %d still alive after ${WAIT_LIMIT}s; "
        "aborting, update stays staged for the next launch.\" >&2" % pid,
        "    exit 0",
        "  fi",
        "  sleep 1",
        "  waited=$((waited + 1))",
        "done",
        "",
        "# Move the old bundle aside rather than deleting it, so a failed",
        "# install is recoverable by hand.",
        "if [ -e \"$TARGET\" ]; then rm -rf \"$OLD\"; mv \"$TARGET\" \"$OLD\"; fi",
        'if [ -d "$STAGED" ]; then',
        '  mv "$STAGED" "$TARGET"',
        "else",
        '  cp "$STAGED" "$TARGET"',
        "fi",
        "",
        "# Relaunch. nohup so this helper can exit immediately.",
        'cd "$(dirname "$TARGET")"',
        'nohup "$TARGET" >/dev/null 2>&1 &',
        "exit 0",
    ])


def apply_pending(base_dir=None, spawn=None, target=None, confirmed=True):
    """Hand the staged build to a detached helper. True when it was launched.

    Refuses -- and spawns nothing -- when the user has not confirmed, when
    nothing is pending, or when the staged payload has gone missing.
    """
    if not confirmed:
        return False
    meta = pending_meta(base_dir)
    if not meta:
        return False
    staged = pending_path(base_dir)
    if not staged or not os.path.exists(staged):
        return False

    if target is None:
        target = _default_target()
    if not target:
        return False
    target = os.path.abspath(target)

    script = _swap_script(staged, target, os.getpid())
    if spawn is None:
        spawn = subprocess.Popen
    try:
        # start_new_session detaches the helper from our process group so it
        # survives us. It is the ONLY thing we start.
        spawn(["/bin/sh", "-c", script],
              stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL,
              stdin=subprocess.DEVNULL,
              start_new_session=True)
    except (OSError, subprocess.SubprocessError, TypeError):
        return False
    except Exception:
        return False
    return True


def _log(msg):  # pragma: no cover - convenience for manual runs
    sys.stderr.write("[updater] %s\n" % msg)


def self_update(include_prerelease=True, base_dir=None, target=None,
                timeout=TIMEOUT, log=_log):
    """The whole opt-in flow: check -> download -> verify -> stage.

    Returns the new version string on success, None otherwise. Nothing here
    touches the installed app; the swap happens on the next launch via
    :func:`apply_pending`.
    """
    info = fetch_latest(include_prerelease=include_prerelease, timeout=timeout)
    if info is None:
        return None
    if info.digest is None and info.size is None:
        # Unverified installs are not offered. Rule 3.
        log("release %s has no size or checksum metadata; refusing" % info.version)
        return None
    path = download(info, dest_dir=os.path.join(_base(base_dir), "downloads"))
    if path is None:
        log("download or verification failed for %s" % info.version)
        return None
    staged = stage_pending(path, info.version, base_dir=base_dir)
    if staged is None:
        return None
    log("staged %s; restart to install" % staged)
    return info.version
