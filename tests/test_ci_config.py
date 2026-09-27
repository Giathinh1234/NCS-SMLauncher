"""Plain-script checks for the CI workflows, build scripts and docs.

Run:  python3 tests/test_ci_config.py

A malformed GitHub Actions workflow does not fail loudly -- it silently does
nothing, and the release you were about to ship never gets built. So the YAML
is parsed here, not just grepped, and the scripts are checked for the
executable bit GitHub needs to run them.
"""
import os
import stat
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_YML = os.path.join(ROOT, ".github", "workflows", "build.yml")
DEPS_YML = os.path.join(ROOT, ".github", "workflows", "dependencies.yml")
BUILDING = os.path.join(ROOT, "BUILDING.md")
SPEC = os.path.join(ROOT, "HashPlay.spec")
VENV_SH = os.path.join(ROOT, "scripts", "build_venv.sh")
LINUX_SH = os.path.join(ROOT, "scripts", "build_linux.sh")

n = 0
def ok(msg):
    global n
    n += 1
    print("%2d. %s" % (n, msg))

try:
    import yaml
except ImportError:
    print("error: pyyaml is required: pip install --user pyyaml", file=sys.stderr)
    raise SystemExit(1)


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# -- 1. the workflow files exist ------------------------------------------
assert os.path.isfile(BUILD_YML), "missing %s" % BUILD_YML
assert os.path.isfile(DEPS_YML), "missing %s" % DEPS_YML
ok("both workflow files exist under .github/workflows/")


# -- 2. build.yml: parses, and covers both platforms ----------------------
build_text = read(BUILD_YML)
build = yaml.safe_load(build_text)
assert isinstance(build, dict), "build.yml did not parse to a mapping"

jobs = build.get("jobs") or {}
assert "macos" in jobs, "build.yml has no macos job"
assert "linux" in jobs, "build.yml has no linux job"
assert jobs["macos"]["runs-on"] == "macos-latest", jobs["macos"]["runs-on"]
assert jobs["linux"]["runs-on"] == "ubuntu-latest", jobs["linux"]["runs-on"]
ok("build.yml parses: macos on macos-latest, linux on ubuntu-latest")

assert build.get("permissions") == {"contents": "read"}, build.get("permissions")
on = build.get(True, build.get("on"))  # YAML 1.1 parses the bare key `on` as True
assert isinstance(on, dict), "build.yml has no trigger block: %r" % (on,)
assert "push" in on and "pull_request" in on and "workflow_dispatch" in on, on
assert "v*" in on["push"]["tags"], on["push"]
ok("build.yml: contents:read, triggers on push/tags v*/PR/dispatch")

for token in ("macos-latest", "ubuntu-latest", "build_macos_app.sh",
              "build_linux.sh", "3.14"):
    assert token in build_text, "build.yml does not mention %r" % token
ok("build.yml mentions both runners, both build scripts and python 3.14")

macos_steps = [s.get("uses") or s.get("run", "") for s in jobs["macos"]["steps"]]
linux_steps = [s.get("uses") or s.get("run", "") for s in jobs["linux"]["steps"]]
assert any("requirements-dev.txt" in s for s in macos_steps), macos_steps
assert any("libsdl2-2.0-0" in s and "portaudio19-dev" in s for s in linux_steps), linux_steps
assert any("requirements.txt" in s for s in linux_steps), linux_steps
ok("build.yml installs the right requirements and Linux runtime deps per job")

art_macos = [s for s in jobs["macos"]["steps"] if s.get("uses", "").startswith("actions/upload-artifact")]
art_linux = [s for s in jobs["linux"]["steps"] if s.get("uses", "").startswith("actions/upload-artifact")]
assert len(art_macos) == 1 and art_macos[0]["with"]["name"] == "HashPlay-macos-arm64", art_macos
assert len(art_linux) == 1 and art_linux[0]["with"]["name"] == "HashPlay-linux-x86_64", art_linux
assert "dist/HashPlay" in art_macos[0]["with"]["path"] and "dist/HashPlay.app" in art_macos[0]["with"]["path"]
assert "dist/HashPlay" in art_linux[0]["with"]["path"] and "dist/*.tar.gz" in art_linux[0]["with"]["path"]
ok("artifacts upload as HashPlay-macos-arm64 and HashPlay-linux-x86_64 with the right paths")


# -- 3. dependencies.yml: parses, weekly, issues:write --------------------
deps_text = read(DEPS_YML)
deps = yaml.safe_load(deps_text)
assert isinstance(deps, dict), "dependencies.yml did not parse to a mapping"
assert deps.get("permissions", {}).get("issues") == "write", deps.get("permissions")

d_on = deps.get(True, deps.get("on"))
assert isinstance(d_on, dict), "dependencies.yml has no trigger block"
assert "schedule" in d_on, d_on
assert d_on["schedule"][0]["cron"] == "0 7 * * 1", d_on["schedule"]
assert "workflow_dispatch" in d_on, d_on
ok("dependencies.yml: cron '0 7 * * 1' + manual, permissions issues:write")

for token in ("outdated", "requirements.txt", "github-script"):
    assert token in deps_text, "dependencies.yml does not mention %r" % token
ok("dependencies.yml writes the outdated list and opens the issue via github-script")

# The empty case must return early, and the dedup must check for an existing
# issue -- a weekly job that spams a new issue every Monday is a bug.
assert "Everything is current." in deps_text, "missing the empty-list early return"
assert "listForRepo" in deps_text, "dependencies.yml does not check for an existing issue"
assert "issues.create" in deps_text, "dependencies.yml never creates the issue"
ok("dependencies.yml returns early when current and dedups the tracking issue")


# -- 4. BUILDING.md ------------------------------------------------------
assert os.path.isfile(BUILDING), "missing BUILDING.md"
doc = read(BUILDING)
for token in ("macOS", "Linux", "python3.14", "build_venv.sh",
              "build_macos_app.sh", "build_linux.sh", "Android", "VERSION"):
    assert token in doc, "BUILDING.md does not mention %r" % token
ok("BUILDING.md covers macOS, Linux, Android, python3.14 and the build scripts")

for token in ("libtorrent", "optional", "libsdl2", "portaudio",
              "SDL_VIDEODRIVER=dummy",
              "MACOS_MIN", "LSMinimumSystemVersion", "AGP 8.13.0", "Kotlin 2.2.20",
              "Gradle 8.13", "compileSdk 34", "JDK 17", "local.properties"):
    assert token.lower() in doc.lower(), "BUILDING.md does not mention %r" % token
ok("BUILDING.md notes libtorrent is OPTIONAL, headless SDL, MACOS_MIN and the Android toolchain")


# -- 5. the build scripts are executable ---------------------------------
for path in (VENV_SH, LINUX_SH):
    assert os.path.isfile(path), "missing %s" % path
    mode = os.stat(path).st_mode
    assert mode & stat.S_IXUSR, "%s is not executable (mode %o)" % (path, mode & 0o777)
    assert not mode & stat.S_ISUID, "%s should not be setuid" % path
    assert read(path).startswith("#!"), "%s has no shebang" % path
ok("both build scripts exist, are +x and have a shebang (GitHub runs them directly)")

venv_text = read(VENV_SH)
assert "set -euo pipefail" in venv_text, "build_venv.sh is not strict-mode"
assert "python3.14" in venv_text and "brew --prefix python@3.14" in venv_text, venv_text
assert "requirements-dev.txt" in venv_text, venv_text
assert "brew install python@3.14" in venv_text, "no install hint on failure"
ok("build_venv.sh: strict mode, finds 3.14 via PATH or brew, hints on failure")

linux_text = read(LINUX_SH)
assert "set -euo pipefail" in linux_text, "build_linux.sh is not strict-mode"
assert 'VERSION="$(cat VERSION)"' in linux_text, "build_linux.sh does not read the VERSION file"
assert "git rev-parse --short HEAD" in linux_text and "|| echo nogit" in linux_text, linux_text
assert ".venv/bin/python -m PyInstaller" in linux_text, "no venv PyInstaller preference"
assert "HashPlay.spec --noconfirm" in linux_text, linux_text
assert "tar -czf" in linux_text and 'dist/HashPlay-linux-x86_64-${VERSION}.tar.gz' in linux_text, linux_text
ok("build_linux.sh: reads VERSION, prefers .venv, runs PyInstaller, makes the tarball")


# -- 6. the spec needs no change for new src modules ----------------------
assert os.path.isfile(SPEC), "missing HashPlay.spec"
spec = read(SPEC)
# updater.py is a plain `import` from another src/ module, which PyInstaller's
# static analysis already follows. It is not a data file, so it must NOT appear
# in `datas` -- that would ship it twice and never actually import it.
assert "updater" not in spec.split("datas=")[1].split(")")[0], \
    "updater.py must not be listed in datas; it is an import, not a data file"
assert "src/ncs_launcher.py" in spec, "spec entrypoint moved"
assert "pathex=['src']" in spec, "spec no longer puts src/ on the import path"
assert "hiddenimports" in spec, "spec lost its hiddenimports list"
ok("HashPlay.spec needs no change: updater.py is followed as a normal import, not a data file")

print("... CI CONFIG TESTS PASSED")
