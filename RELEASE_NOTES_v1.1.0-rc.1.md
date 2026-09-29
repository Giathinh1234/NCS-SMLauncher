# HashPlay 1.1.0-rc.1

A release candidate. **Marked prerelease on purpose** — the escape-hatch
behaviour below has not been manually confirmed on a real keyboard, and this
is the build you can try if you want to help check it.

Nothing in the in-app updater will offer this to you automatically.
Prereleases are opt-in, so a build this unverified cannot be pushed at
someone who did not ask for it.

## The big one: your music now advances on its own

**The app could never advance a track by itself.** There was no end-of-track
check anywhere in the program. The comment on the relevant line claimed the
UI handled it; nothing did. You got one track, and then the app sat on the
final sample showing NOW PLAYING until you pressed next. Play an album now
and it plays through.

## Playing a file that will not decode no longer kills the app

`load()` had 18 call sites with no error handling, and miniaudio raises on a
truncated or zero-length file — so one bad file in your library took the
whole thing down. The repository itself shipped a zero-byte `music/test.mp3`,
which made this reachable without doing anything unusual.

It now reports the file, names it, and moves on. The file has been removed.

## Video sidecars no longer flood the machine

If a sidecar video was shorter than its track, the playhead and the stored
seek target disagreed permanently, so the app re-seeked *every frame*:
measured at 60 ffmpeg process spawns and 14.6 seconds of work per second of
frames. It now seeks once.

## Bugs that were silently doing nothing

- **Media keys never worked at all.** The event mask referenced a constant
  that does not exist in pyobjc on this machine, and the failure was
  swallowed. Previous-track was separately dead — it was wired to a name the
  handler never matched.
- **The "O" key forgot your library folder.** It reimplemented the folder
  picker without the part that saves. Two copies of one action, and they
  disagreed.
- **Turning off the control hints needed a restart.**
- **Two of the six visualizer modes rendered nothing** — "disc" and "album"
  painted into a surface that was never composited, and burned a PIL decode
  per frame for a window nobody could see. Measured 0 non-black pixels;
  now ~118k and ~99k.
- **Every message in the app was invisible.** All 26 of them drew from
  inside the torrent panel, which requires libtorrent. With it absent you
  were told nothing — not about resizes, not about a missing library, not
  about a failed API bind. There is now a message bar that does not depend
  on it.
- **Escape needed three presses to quit**, in a fresh install. The first was
  spent dismissing a torrent panel that was never on screen. It is two now.

## The self-updater did not work at all, and was not even reachable

Not "unreliable" — it could not have worked for any release this project has
ever produced, for five independent reasons, all fixed:

1. It could not see our own macOS asset. The filter accepted only `.app.zip`
   or exactly `HashPlay`; releases publish `HashPlay-macos-arm64`. Every
   lookup returned nothing, so the app always reported "up to date".
2. A release with no installable artifact ended the whole scan, so an older
   release that *did* have one was never tried.
3. `self_update` never passed the current version, so nothing could ever
   qualify as newer.
4. The swap relaunched with `nohup "$TARGET"` — but on macOS that target is
   `HashPlay.app`, a directory. You quit, the swap happens, and the app never
   comes back. It uses `open` now.
5. Every request to GitHub failed TLS verification, because the system trust
   store on this machine is missing the issuer chain. That is the one worth
   dwelling on: the updater's own `except Exception: return None` turned the
   `SSLCertVerificationError` into a quiet "you are up to date", so the
   feature reported success while being completely broken. It now uses
   certifi, which is bundled.

And the fifth thing was the real one: **nothing in the app ever called it.**
The module shipped in the bundle and had tests, but no import, no dispatch
entry, no key — so there was no path from the interface to the updater at
all. Describing it as a "shipped headline feature" was not accurate.

There is now an `update_check` command that reports whether a newer release
exists. It is deliberately check-only: anything that can reach the loopback
API can ask, but nothing can replace your binary without you deciding to.
Installing is still a manual step, and that is a known gap, not an oversight.

A related caveat worth stating plainly: that `except Exception: return None`
is exactly the shape of code that hides bugs. It is the reason four of these
five went unnoticed. Some failures should be reported, not swallowed.

## Let your bots and agents drive it

`POST /webhook` — one flat endpoint, so a bot does not need to know the
command set:

```bash
curl -X POST "http://127.0.0.1:8777/webhook?token=$TOKEN" \
     -d "track=daft punk"
```

Accepts JSON or ordinary form encoding, so Discord, IFTTT, n8n and a plain
script all work without special handling. Aliases (`track`, `title`, `q`,
`song`, `name`) are normalised, and the response is plain text, because
webhook test screens display response bodies clearly. `hashplay-ctl hook`
prints the paste-ready URL.

This is **local only**. A bot on another machine still needs a relay, a
tunnel, or an exposed listener.

## First run

There is now a setup wizard: welcome, dependencies, library folder, summary.
It only appears when it should — not when you passed a folder on the command
line, not when one is already saved, and skipping is remembered.

## Also

- API and webhook authentication; the token lives in a `0600` file and is
  no longer printed in full on screen.
- `requirements.txt` now exists. CI referenced it in three places and it was
  in neither the repository nor `.gitignore`, so every build had been failing
  at the install step.
- A hostile-startup test suite: corrupt config, bad environment values,
  missing folders, a port that is already taken.
- ffmpeg processes are reaped properly instead of piling up as zombies.
- Long folder paths no longer hang the UI in an infinite ellipsis loop. This
  one is worth calling out: it silently froze the app, and it only showed up
  when testing the live API, because `/status` stops answering when the frame
  counter stops moving.

## Testing

38 test files, all passing. Every fix above is covered by a test that fails
without the fix — including the auto-advance, which is verified end to end
against the real app playing real audio, and the seek storm, which is
measured rather than asserted.

## Known gaps in this candidate

- **The escape-hatch behaviour is unverified on a real keyboard.** The logic
  is covered by a test that runs the actual code path, but synthetic
  keystrokes are blocked on this machine, so nobody has pressed it. That is
  the main reason this is an rc.
- The API and webhook are **loopback only**.
- The macOS binary is **unsigned**.
- Streaming playback and library organisation are written but not yet
  wired into the main UI.
- No Linux artifact.

## Install

Download `HashPlay-macos-arm64` and move it to Applications, or get it from
Homebrew once the final ships. Check the SHA256SUMS file if you want to
verify the download.
