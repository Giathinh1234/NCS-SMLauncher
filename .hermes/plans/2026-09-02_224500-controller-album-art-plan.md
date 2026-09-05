# Controller Support and Album Art Viewer Format Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add gamepad controller support and a new visualizer mode that displays album art fetched from metadata.

**Architecture:** 
- Controller support will be added by initializing pygame.joystick and handling JOYBUTTONDOWN, JOYAXISMOTION, and JOYHATMOTION events in the main event loop.
- The album art viewer format will be a new mode in the visualizer cycle, displaying the album art (if available) with a slow zoom/rotation effect and track information.

**Tech Stack:** Python, Pygame (for controller and visualizer), mutagen (for metadata).

---
### Task 1: Add controller initialization and basic event handling

**Objective:** Initialize pygame.joystick, detect connected controllers, and handle basic button events for play/pause, next track, previous track.

**Files:**
- Modify: `src/ncs_launcher.py`

**Step 1: Add joystick initialization after pygame.init()**

```python
# Initialize joystick support
pygame.joystick.init()
joysticks = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
for joystick in joysticks:
    joystick.init()
```

**Step 2: Add event handling for JOYBUTTONDOWN in the main event loop**

```python
elif event.type == pygame.JOYBUTTONDOWN:
    if event.button == 0:  # A button (commonly)
        player.toggle_pause()
    elif event.button == 1:  # B button
        # Stop playback? We'll just pause for now
        player.toggle_pause()
        if player.paused:
            # Optionally reset position? Not now.
            pass
    elif event.button == 2:  # X button -> next track
        if tracks:
            selected = (selected + 1) % len(tracks)
            nonlocal_selected[0] = selected
            player.load(tracks[selected]['path'])
    elif event.button == 3:  # Y button -> previous track
        if tracks:
            selected = (selected - 1) % len(tracks)
            nonlocal_selected[0] = selected
            player.load(tracks[selected]['path'])
```

**Step 3: Run the application and verify that a connected controller responds to these buttons.**

**Step 4: Commit**

```bash
git add src/ncs_launcher.py
git commit -m "feat: add basic controller support (play/pause, next/prev)"
```

---
### Task 2: Add axis and hat (D-pad) support for navigation and seeking

**Objective:** Use the left analog stick or D-pad to navigate the playlist and the right analog stick for seeking (if available) or use triggers for volume.

**Files:**
- Modify: `src/ncs_launcher.py`

**Step 1: Add handling for JOYAXISMOTION and JOYHATMOTION**

We'll use:
- Left analog stick (axis 0 and 1) for playlist navigation (vertical axis for up/down, horizontal for fast scroll?).
- D-pad (hat 0) for playlist navigation as well.
- Right analog stick (axis 2 and 3) for seeking (horizontal for seek, vertical for volume?).
- Triggers (axis 4 and 5) for volume.

But to keep it simple, we'll map:
- D-pad up/down: move selection up/down.
- Left analog stick vertical: same as D-pad.
- Right analog stick horizontal: seek +/-5s when moved beyond a threshold.

**Step 2: Add variables to track last axis states to avoid spamming events.**

We'll add a dictionary to store the last axis values and only act on significant changes.

**Step 3: In the event loop, handle JOYAXISMOTION for axis 1 (left stick vertical) and axis 3 (right stick horizontal) and JOYHATMOTION for hat 0.**

**Step 4: Run and test that the controller can navigate the playlist and seek.**

**Step 5: Commit**

```bash
git add src/ncs_launcher.py
git commit -m "feat: add controller axis and hat support for navigation and seeking"
```

---
### Task 3: Add album art viewer format (new visualizer mode)

**Objective:** Add a new mode called "album" that displays the album art (if available) with a slow zoom or rotation effect, and shows the track title and artist.

**Files:**
- Modify: `src/ncs_launcher.py`

**Step 1: Add "album" to the modes list in main()**

```python
modes = ["bars", "mirror", "radial", "disc", "album"]
```

**Step 2: In the draw_visualizer function, add a new case for mode == "album"**

We'll do:
- If album art is available, display it centered, possibly with a slow pulsating zoom or rotation.
- If no album art, display a placeholder (maybe a colored gradient or the NCS logo?).
- Also, display the track title and artist at the bottom.

**Step 3: Implement the album art viewer**

We'll use the current_track_metadata to get the art_path. We'll load the image, resize it to fit the screen (with some margin), and then apply a slow zoom based on time (e.g., zoom = 1.0 + 0.1 * sin(t * 0.2)).

We'll also draw the track title and artist below the image.

**Step 4: Run and test that pressing F cycles through the modes and the album mode shows the art.**

**Step 5: Commit**

```bash
git add src/ncs_launcher.py
git commit -m "feat: add album art viewer visualizer mode"
```

---
### Task 4: Update controls hint to indicate controller support (optional)

**Objective:** Add a note in the hint that controller is supported, or just leave it as is since the controls are the same.

We can update the hint string to include "or use controller" but it's not necessary.

**Files:**
- Modify: `src/ncs_launcher.py`

**Step 1: Update the render_hint function to add a note about controller.**

```python
def render_hint():
    return font.render("↑↓ select  ⏎ play  space pause  ←→ seek  "
                       "F visual  T torrent  O folder  M mute  Q quit "
                       "(Controller supported)",
                       True, (120, 120, 130))
```

**Step 2: Commit**

```bash
git add src/ncs_launcher.py
git commit -m "docs: update hint to note controller support"
```

---
### Task 5: Test all features together

**Objective:** Ensure that the controller works in all visualizer modes and that the album art viewer works correctly.

**Files:**
- None (just testing)

**Step 1: Launch the application with a music folder containing tracks with album art.**

**Step 2: Connect a controller and verify:
   - Buttons: A (play/pause), B (pause), X (next), Y (prev) work.
   - D-pad and left stick: navigate playlist.
   - Right stick horizontal: seek.

**Step 3: Cycle to the album mode and verify the album art is displayed with a slow zoom effect.

**Step 4: Test with tracks that have no album art to ensure a fallback is shown.

**Step 5: Commit any final fixes.

**Files:** (if any changes)
```bash
git add src/ncs_launcher.py
git commit -m "fix: ensure controller and album mode work together"
```

---
### Risks and Tradeoffs
- Controller mapping may vary between devices. We assume a standard Xbox-like layout. We might need to make it configurable in the future, but for now we hardcode.
- The album art viewer may be CPU-intensive if we scale the image every frame. We can cache the scaled image at a few sizes or only resize when the window changes size.
- We are adding more modes, which increases the cycle length when pressing F. We could consider making the modes configurable, but that's out of scope.

### Open Questions
- Should we add a way to exit the album art viewer with a controller button? Not needed, the same controls apply.
- Should we display the album art in the existing UI panel (like the disc mode) or fullscreen? We chose fullscreen for the album mode to really showcase the art.

### Validation
- Manual testing with a controller and a music library.
- Verify that the application still works without a controller (no errors).

**Plan complete and ready for execution via subagent-driven-development.**