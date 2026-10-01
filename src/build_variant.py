"""Which variant is this build? Set by the build script, read at import time.

An environment variable does not work for this. PyInstaller analyses imports,
not the environment: HASHPLAY_LITE=1 while building changes nothing about what
gets collected, and `os.environ.get("HASHPLAY_LITE")` at run time still
evaluated false in a frozen binary -- verified, the flag appeared zero times in
a real lite build, which went on to open a listening socket and write a token
file while claiming to be lite. The env var was read fine from source and lost
in translation.

So the variant is a CONSTANT in a module, written before the build. That is
something PyInstaller can see, and something a test can read.

The full build leaves BUILD_LITE = False. scripts/build_macos_lite.sh
overwrites this file first, then builds.
"""

# Which features this build leaves out. Read at import time, so a value baked
# in here is what a frozen binary actually gets.
#
#   "lite"  -- no control API, no NCS ball
#   "micro" -- lite, and also no video (ffmpeg) and no torrents (libtorrent)
#
# The two were separated because they cost very different amounts: the API and
# the ball are 9.4 MB and 24.8 MB, but video is 25.5 MB on its own -- it pulls
# in ffmpeg, which is the largest single thing left once lite is done. Measured
# headlessly in tools/measure_lite_headroom.py.
#
# False means the full build, with everything on.
BUILD_LITE = False
# "lite" or "micro" when this is a stripped build; ignored when False.
BUILD_PROFILE = "full"
