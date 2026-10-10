# Apple-Music-Style Captions + Real Cover Art — Implementation Plan

> **For Hermes:** Use subagent-driven-development to implement this plan task-by-task.
> Written 2026-10-11 for the HashPlay repo (`/Users/giathinh/ncs-music-launcher`).

**Goal:** Two features — (A) synced, animated lyrics ("captions") like Apple Music, and (B) real embedded cover art in the library and now-playing views.

**Architecture:** Both features hang off the existing `PlayerController`/`PlaybackService` Media3 pipeline. Captions are a timed-text overlay fed from `.lrc` sidecar files (phase 1) and embedded lyrics tags (phase 2); cover art comes from embedded ID3/FLAC/M4A pictures via Media3 + `MediaMetadataRetriever`, surfaced through `MediaMetadata` so the lock screen gets it for free.

**Tech Stack:** Media3 `MediaMetadata` (artwork), `MediaMetadataRetriever` (embedded art + lyrics extraction), `.lrc` parser (hand-rolled, ~80 lines), Compose overlay UI, no new dependencies.

---

## Part A: Captions (Apple-Music-style lyrics)

### What "Apple Music style" means, concretely

1. Lyrics shown **full-screen over the visualizer**, a few lines visible at a time.
2. The **active line is highlighted** (brighter, slightly larger) and *sweeps* — a per-word gradient fill as the words are sung, not just a color flip.
3. Lines **auto-scroll** so the active line stays at a fixed height (~1/3 from top).
4. Tapping a line **seeks** to that timestamp.
5. When no lyrics exist: show a tasteful empty state (album art + "No lyrics"), never a broken panel.

### Data sources, in priority order

| Priority | Source | Where it lives | Effort |
|---|---|---|---|
| 1 | `.lrc` sidecar file | same folder as track, same basename (`song.lrc` next to `song.mp3`) | trivial |
| 2 | Embedded `USLT`/`SYLT` (ID3) or `©lyr` (M4A) or `LYRICS` Vorbis comment | inside the audio file | medium |
| 3 | Apple Music/ Musixmatch API | **out of scope** — needs keys/licensing, and the app is offline-first | n/a |

Phases 1–2 are honest and self-contained. Explicitly NOT doing: scraping lyric services (legal + reliability mess, needs accounts).

### LRC format primer (what the parser must handle)

```
[00:12.34] First line of lyrics
[00:15.20] Second line
[00:12.34] ← multiple tags per line are legal
[ar: Artist] ← metadata tags (id3: ignore)
[00:12] ← centiseconds optional
```

Also handle: "enhanced" A2 extension (`[00:12.34]<00:12.34>word1 <00:13.10>word2`) — that's what enables the **per-word sweep**. Parse it if present; fall back to per-line highlight when absent.

### Tasks

### Task A1: LRC parser (`Lyrics.kt`, new file, main source set)
- `data class LyricLine(val timeMs: Long, val text: String, val words: List<WordTiming>?`)`
- `fun parseLrc(text: String): List<LyricLine>` — sorted, deduped, metadata tags ignored.
- TDD: write `tests/test_lyrics.py` as a static-contract test on the Kotlin source (matching the repo's existing test style), plus a fixture `.lrc` inline in the test.
- Verify: `python3 tests/test_lyrics.py` passes.
- Commit: `feat(android): lrc parser`.

### Task A2: Sidecar discovery in `LibraryScanner`
- When scanning tracks, check for `<basename>.lrc` next to each audio file (only possible for SAF-scanned folders; MediaStore tracks won't have sidecars visible — handle gracefully).
- Add `lyricsPath: String?` to `Track`.
- Verify: compile + existing tests green.
- Commit: `feat(android): discover lrc sidecars`.

### Task A3: Embedded lyrics extraction (phase 2, separate commit)
- `MediaMetadataRetriever.extractMetadata("LYRICS")` won't cover ID3 USLT on all files; use `MediaMetadataRetriever` raw picture/text approach: read `USLT` via a tiny ID3 reader OR defer to phase 3. **Recommendation: start with sidecars only (A1–A2), ship, then evaluate embedded.** Embedded text formats are a compatibility swamp; sidecars give 90% of the value.
- Commit (when attempted): `feat(android): embedded lyrics via id3 uslt`.

### Task A4: Caption UI (`CaptionsOverlay.kt`, new file)
- Full-screen overlay composable, drawn **above** the visualizer background but **below** transport, same layering rule the library panel uses.
- State: `lines: List<LyricLine>`, `positionMs: Long` (from `controller.state` — already flows).
- Active line = last line whose `timeMs <= positionMs`.
- Rendering:
  - `LazyColumn` with `rememberLazyListState()`; `LaunchedEffect(activeIndex) { animateScrollToItem(activeIndex - 2) }` so the active line sits ~1/3 down.
  - Active line: `PixelType.Body`, 22sp, color `0xFFD8DEE9`; inactive: 16sp, `0xFF6E768C` at 60% alpha.
  - **Per-word sweep (phase 1b):** if `words != null`, overlay a `Brush.linearGradient` on the active Text whose split point animates 0→1 across the line's duration. Use `ComposegraphicsLayer` alpha, not per-word Texts (per-word Texts break wrapping).
  - Tap on a line → `controller.seekTo(line.timeMs)`.
- Verify: compile, install, screenshot with a seeded `.lrc` (no audio playback needed — seek while paused, captions must still follow position).
- Commit: `feat(android): captions overlay`.

### Task A5: Settings wiring
- `HashSettings.captionsEnabled(prefs)` (default ON when lyrics exist), toggle in SettingsOverlay under a new "LYRICS" section.
- Commit: `feat(android): captions setting`.

### Task A6: Empty state + lock screen metadata (optional polish)
- When no lyrics: centered "no lyrics for this track" in `PixelType.Display`, dim.
- Pass lyrics through Media3 `MediaMetadata.artworkUri`? No — lyrics don't belong on the lock screen. Skip.

## Part B: Real cover art

### Where art comes from

| Priority | Source | Notes |
|---|---|---|
| 1 | Embedded ID3 `APIC` / FLAC `PICTURE` / M4A `covr` | inside the file, always travels with it |
| 2 | `folder.jpg` / `cover.jpg` / `albumart.jpg` in the track's folder | common in owned rips |
| 3 | Online lookup (Last.fm/Deezer) | **out of scope** same as lyrics — offline-first, no keys |

### Tasks

### Task B1: Extract embedded art (`CoverArt.kt`, new file)
- `fun loadEmbedded(context, uri): Bitmap?` using `MediaMetadataRetriever.embeddedPicture` (works for MP3/M4A/FLAC/OGG on API 29+).
- Cache: `context.cacheDir/coverart/<md5-of-uri>.png`, keyed by track uri + size; **cap the cache at ~30MB LRU** (`DiskLruCache`-style hand-rolled, or just delete-oldest when over cap).
- Decode with `BitmapFactory.Options(inSampleSize)` targeting ≤512×512 — full-res embedded art is routinely 3000px and would OOM a grid.
- Verify: unit-test the sizing math + cache key derivation; on-device test decodes one real track.
- Commit: `feat(android): embedded cover art extraction`.

### Task B2: Folder-fallback art
- If embedded is null, look for `folder.jpg|cover.jpg|album.jpg|front.jpg` (case-insensitive) in the track's parent directory (SAF tree only).
- Commit: `feat(android): folder cover fallback`.

### Task B3: Library row art
- `TrackRow` gains a 40dp square thumb: art, or a deterministic 2-letter monogram tile (first letters of title, tinted by hashing the album name into the existing palette) when none.
- Use `coil-compose`? **No** — adding a dependency for this; hand-roll with `remember(uri) { loadAsync(...) }` + `AsyncImage`-style manual state. Actually: Media3 already ships with the app — but Coil is cleaner for lists. **Decision: add Coil** (`io.coil-kt:coil-compose:2.5.0`, ~200KB). Hand-rolling async image loading + caching + cancellation for a scrolling list is exactly the kind of code that flakes; this is the one place a library earns its size.
- Commit: `feat(android): cover art in library rows`.

### Task B4: Now-playing art + lock screen
- Full-bleed blurred art behind the visualizer (like Apple Music's ambient view): `Modifлер.blur` isn't available pre-API 31 — use `RenderScript`-free approach: scale bitmap to 32px and scale back up (cheap fake blur, looks right at 8-bit aesthetic).
- `PlaybackService`: set `MediaMetadata.artworkUri`/`artworkData` on the session so the lock screen/media notification shows art (Media3 handles notification rendering).
- Commit: `feat(android): now-playing art + lock screen artwork`.

### Task B5: Settings
- "Show cover art" toggle (default ON) under INTERFACE.
- Commit: `feat(android): cover art settings`.

---

## Verification (both parts)

- No audio playback ever required: captions follow position while **paused** (seek → position changes → captions update). All on-device verification is screenshot-based, consistent with the no-sound rule.
- New static tests: `tests/test_lyrics.py`, extend `tests/test_ncs_flavor.py` for settings toggles.
- Compile gate: all three flavors (`:app:compileFullDebugKotlin :app:compileLiteDebugKotlin :app:compileNcsDebugKotlin`).
- Device gate: Nokia T20 screenshots — captions over background sphere; art thumbnails in library; lock screen art.

## Risks / tradeoffs

1. **`.lrc` availability** — most users' files won't have sidecars. The feature must be invisible when absent (empty state, not an empty panel). Getting `.lrc` files is the user's job (they're everywhere); we don't fetch.
2. **SAF vs MediaStore split** — sidecar discovery only works for SAF-picked folders. MediaStore tracks silently get no captions unless embedded lyrics (phase 2) land. Document in the settings description.
3. **Per-word sweep** is only as good as the `.lrc`'s A2 extension support; most `.lrc` files are line-timed only. Ship line-highlight first (A4 without sweep), add sweep in 1b.
4. **Coil dependency** — adds ~200KB to each APK. Justified only if we do list thumbnails (B3). If the answer is "no new deps", B3 becomes "art on now-playing only, monograms in the list".
5. **MediaMetadataRetriever on weird files** — wrap every call; a malformed file must yield null art, never an exception on the scan thread.
6. **Memory** — decoded bitmaps in a LazyColumn: enforce inSampleSize, use Coil's memory cache, never hold bitmaps in `Track`.

## Out of scope (explicitly)

- Streaming lyric/cover lookups (needs API keys + licensing).
- Karaoke-style syllable timing beyond A2 `.lrc`.
- Editing lyric timings in-app.

## Suggested order

A1 → A2 → A4 (line highlight only) → B1 → B2 → B3 → A4b (sweep) → A5 → B4 → B5.

Captions-with-sidecars and embedded-art are independent; if time is short, ship A1+A2+A4 and B1–B3 as the first cut.
