### What's new in this build

- **Real NCS sphere.** The ball is now a genuine 3D point cloud: 30,000 points
  distributed over a Fibonacci sphere, displaced by a flowing wave field and
  projected with real perspective. A sharp gold crest forms the membrane that
  wraps over the surface, and the sphere interior stays dark so the ripples
  read as 3D topography. It reacts to the FFT, so the bass makes it pulse and
  swell. (The previous version was a flat disc with straight rays — it read as
  bars, and the album-art-on-a-ball version has been removed.)
- **Resizing actually works now.** The window was created `RESIZABLE`, but
  there was no `VIDEORESIZE` handler, so nothing on screen moved when you
  dragged the window edge. There is a real handler now, with a 640x480
  minimum, and the layout recomputes on every resize.
- **Text no longer overflows the menu.** Library rows truncate to fit the
  panel, the now-playing label stops before it collides with the time readout,
  and the hint bar swaps to a short form when the window is narrow.
- **Interactive Hermes chat panel** (press `C`). Type a question in the app and
  Hermes answers inside the panel, seeded with your current track, playback
  state, active visualizer mode, and library path. `Enter` sends, `Esc` closes,
  arrow keys / PageUp / PageDown scroll. Runs in the background so playback and
  the visualizer never stutter while it's thinking.

### Controls

    Up / Down     select track
    Space         pause / resume
    Left / Right  seek +/-5s
    M             mute
    F             cycle visualizer (bars / mirror / sphere / vinyl / album)
    C             open the Hermes chat panel
    T             torrent overlay — paste a magnet link or infohash
    O             change source folder
    Q / Esc       quit

### Known issues

- Controller support is partial: button mapping (A/B/X/Y, D-pad) works, but
  it was developed against one cheap BSP-D3 pad, so stick/axis behaviour varies
  by controller. The right stick is mapped to seek and is quite twitchy.
- Still a pre-release, not the official v1.0.

### Downloads

- `HashPlay` — standalone executable
- `HashPlay.app.zip` — double-clickable macOS app bundle
