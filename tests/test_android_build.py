"""The Android build's contract, checked without a device or a Gradle run.

Every assertion here is about something that has actually gone wrong during
this work, or would go wrong silently:

  * a `packaging { }` block inside productFlavors is NOT variant-scoped, so
    excluding libtorrent4j there stripped it from the FULL build too -- and the
    build still went green
  * the release job's file list dropped HashPlay.app.zip when it was narrowed
    to explicit names, which would have quietly removed the main macOS
    download
  * TorrentManager.kt sat in src/main, so the lite stub redeclared it and lite
    stopped compiling

Checks are static. Building both APKs and inspecting them is
scripts/build_android_apks.sh's job, and it runs in CI.
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRADLE = os.path.join(REPO, "android", "app", "build.gradle.kts")
WORKFLOW = os.path.join(REPO, ".github", "workflows", "build.yml")
PROGUARD = os.path.join(REPO, "android", "app", "proguard-rules.pro")
ANDROID_SRC = os.path.join(REPO, "android", "app", "src")
BUILD_SCRIPT = os.path.join(REPO, "scripts", "build_android_apks.sh")

FAILS = []


def check(cond, label, detail=""):
    if not isinstance(detail, str):
        detail = str(detail)
    print("   %s  %s%s" % ("ok " if cond else "FAIL", label,
                           "" if cond or not detail else "  -- " + str(detail)))
    if not cond:
        FAILS.append(label)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


gradle = read(GRADLE)
workflow = read(WORKFLOW)

print("ANDROID BUILD")
check(os.path.isfile(GRADLE), "app/build.gradle.kts exists")
check(os.path.isfile(PROGUARD), "proguard-rules.pro exists")
check(os.path.isfile(BUILD_SCRIPT), "scripts/build_android_apks.sh exists")

print("\n  both flavors exist")
check("productFlavors" in gradle, "product flavors are declared")
check(re.search(r'create\("full"\)', gradle) is not None, "a full flavor")
check(re.search(r'create\("lite"\)', gradle) is not None, "a lite flavor")

print("\n  they can be installed side by side")
check('applicationIdSuffix = ".lite"' in gradle,
      "lite has its own applicationId suffix")
# If both had the same id, installing one replaces the other, which defeats
# the point of offering a choice.
ids = re.findall(r'applicationId(?:Suffix)?\s*=\s*"([^"]+)"', gradle)
check(len(set(ids)) >= 2, "full and lite have different application ids", ids)

print("\n  lite really drops the torrent engine")
# The exclude must be variant-scoped. A `packaging { }` inside
# productFlavors applies to every variant and silently removed the engine from
# the FULL build, which still passed.
check("androidComponents" in gradle,
      "the jniLibs exclude is scoped via androidComponents, not productFlavors")
flavor_block = gradle[gradle.find("productFlavors"):gradle.find("androidComponents")]
check("jniLibs" not in flavor_block,
      "no unscoped jniLibs exclude inside productFlavors")
check('"**/libtorrent4j.so"' in gradle, "libtorrent4j.so is excluded by name")
check('flavors.contains("lite")' in gradle and '!flavors.contains("full")' in gradle,
      "the exclude applies to lite and NOT to full")

print("\n  the two TorrentManagers do not collide")
check(os.path.isfile(os.path.join(ANDROID_SRC, "full", "java", "com",
                                  "giathinh", "hashplay", "TorrentManager.kt")),
      "the real TorrentManager lives in src/full/")
check(os.path.isfile(os.path.join(ANDROID_SRC, "lite", "java", "com",
                                  "giathinh", "hashplay", "TorrentManager.kt")),
      "the lite stub lives in src/lite/")
check(not os.path.isfile(os.path.join(ANDROID_SRC, "main", "java", "com",
                                      "giathinh", "hashplay", "TorrentManager.kt")),
      "neither is in src/main, where both would compile at once")

# Both APKs sit on the same home screen, so the labels must differ or there is
# no way to tell the icons apart. The label is a resource, not a manifest
# literal, precisely so the lite flavor can override it.
manifest = read(os.path.join(REPO, "android", "app", "src", "main",
                             "AndroidManifest.xml"))
check('android:label="@string/app_name"' in manifest,
      "the manifest label is a resource, so a flavor can override it")
lite_strings = read(os.path.join(ANDROID_SRC, "lite", "res", "values", "strings.xml"))
check("HashPlay Lite" in lite_strings, "lite labels itself 'HashPlay Lite'")

lite_stub = read(os.path.join(ANDROID_SRC, "lite", "java", "com",
                              "giathinh", "hashplay", "TorrentManager.kt"))
real = read(os.path.join(ANDROID_SRC, "full", "java", "com",
                         "giathinh", "hashplay", "TorrentManager.kt"))
# Match an IMPORT, not the bare word: the stub's comment explains what it
# replaces and names the library, so a substring check fails on its own
# documentation.
check(not re.search(r"^\s*import\s+org\.libtorrent4j", lite_stub, re.M),
      "the lite stub does not import libtorrent4j (that is what drops the .so)")
# PlayerScreen.kt is shared and compiles against whichever file is present, so
# the two must expose the same surface. A drift here breaks the lite build.
for symbol in ("class TorrentManager", "data class Status", "val statuses",
               "val messages", "val downloadDir", "fun start(",
               "fun normalize("):
    in_lite = symbol in lite_stub
    in_real = symbol in real
    check(in_lite and in_real,
          f"both TorrentManagers expose {symbol!r}",
          f"lite={in_lite} full={in_real}")

print("\n  R8 cannot strip what only the manifest references")
check("-keep class com.giathinh.hashplay.PlaybackService" in read(PROGUARD),
      "PlaybackService is kept (it is manifest-only, so R8 cannot see it)")
check("androidx.media3" in read(PROGUARD), "Media3 is kept")

print("\n  an unsigned release APK is refused, not shipped")
check("HASHPLAY_HAVE_SIGNING" in gradle, "signing is conditional on real key material")
check("isMinifyEnabled = true" in gradle, "R8 is on for release")

print("\n  CI builds and ships both")
check("name: Android (APK)" in workflow, "there is an android CI job")
check("build_android_apks.sh both" in workflow, "CI builds both flavors")
check("android-actions/setup-android" in workflow, "CI installs the Android SDK")
check("secrets.HASHPLAY_KEYSTORE_B64" in workflow, "CI gets the key from a secret")
check("jniLibs" in workflow or "libtorrent4j" in workflow,
      "CI asserts the lite APK really lost the engine")
check("needs: [macos, linux, windows, android, asset-names]" in workflow,
      "the release job waits for the APKs")
check("HashPlay-lite-android-arm64.apk" in workflow,
      "the lite APK is renamed to match the desktop naming before upload")
check("HashPlay-full-android-arm64.apk" in workflow,
      "the full APK is renamed too")
# The publish step must not narrow its globs. It did once and dropped
# HashPlay.app.zip, the main macOS download, with no error anywhere.
publish = workflow[workflow.find("name: Assemble the payload"):
                   workflow.find("name: Checksums")]
check("'*.zip'" in publish,
      "the publish step still matches zips broadly (it once dropped HashPlay.app.zip)")

print("\n  the verifier does not lie about signing")
bs = read(BUILD_SCRIPT)
check("apksigner" in bs,
      "signing is checked with apksigner, not by listing META-INF (v2/v3 signing "
      "stores no .RSA file, so a zip listing reports valid APKs as unsigned)")

print("\n" + ("ANDROID BUILD TESTS PASSED" if not FAILS
             else "%d FAILURES: %s" % (len(FAILS), FAILS)))
sys.exit(1 if FAILS else 0)
