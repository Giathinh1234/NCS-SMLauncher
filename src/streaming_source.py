"""Incremental audio decoding for files that are still being written.

The NCS launcher plays tracks that a torrent is still fetching, so the file on
disk grows underneath us. A one-shot decode would either fail or pin the track
at whatever prefix happened to be on disk, so this module decodes *forward*:
`pump()` re-enters the decoder at the mono-frame cursor it already holds,
appends only the new tail, and keeps whatever decoded cleanly if the tail is
torn mid-frame.

Two miniaudio facts drive the design, both verified against a real stereo MP3:

* `decode_file()` on a truncated MP3 does not raise. It silently returns the
  decodable prefix, which is why the old behaviour "worked" while quietly
  truncating in-progress tracks.
* `mp3_stream_file()` emits the file's *native* channel count, so a stereo file
  yields exactly twice the mono sample count. A downmix is mandatory or the
  visualizer runs at double speed and every band is pitch-shifted.

`mp3_stream_file()` accepts neither `output_format` nor `nchannels`; passing
either raises TypeError. A seek cursor buys no measurable speed (the generator
still rescans headers), so the cursor is kept simple: a frame index.
"""

import os

import miniaudio
import numpy as np

SAMPLE_RATE = 44100
FRAMES_TO_READ = 8192

try:  # miniaudio exposes SIGNED16 both at module level and on SampleFormat
    _SIGNED16 = miniaudio.SIGNED16
except AttributeError:  # pragma: no cover - depends on miniaudio version
    _SIGNED16 = miniaudio.SampleFormat.SIGNED16


class StreamingSource:
    """A mono float32 view of an audio file that may still be growing."""

    def __init__(self, path, kind=None):
        self.path = os.fspath(path)
        if kind is None:
            ext = os.path.splitext(self.path)[1].lower()
            kind = "mp3" if ext == ".mp3" else "generic"
        self.kind = kind
        self.channels = self._probe_channels()
        self._buffer = np.zeros(0, dtype=np.float32)
        # Byte count of the file as of the last pump that reached the end of
        # whatever was decodable. A pump that stopped early (max_seconds) or
        # died on a torn frame leaves this stale, so the next pump retries.
        self._decoded_bytes = 0

    # -- introspection ---------------------------------------------------

    def size_on_disk(self):
        try:
            return int(os.path.getsize(self.path))
        except OSError:
            return 0

    def is_complete(self, expected_size=None, path=None):
        target = self.path if path is None else os.fspath(path)
        try:
            size = int(os.path.getsize(target))
        except OSError:
            return False
        if expected_size is None:
            return size > 0
        try:
            expected = int(expected_size)
        except (TypeError, ValueError):
            return False
        return size >= expected

    def samples(self):
        return self._buffer

    def available_duration(self):
        return float(self._buffer.size) / float(SAMPLE_RATE)

    def __len__(self):
        return int(self._buffer.size)

    # -- decoding -------------------------------------------------------

    def _probe_channels(self):
        """Native channel count, 1 whenever the header cannot be read."""
        try:
            info = miniaudio.get_file_info(self.path)
            return max(1, int(info.nchannels))
        except Exception:
            return 1

    def _iter_mono(self, seek_frame=0):
        """Yield raw native-channel blocks starting at `seek_frame` mono frames.

        Blocks are int16 interleaved (or (frames, channels) shaped); the caller
        downmixes. For MP3 the file's own channel layout is used because
        `mp3_stream_file` rejects an output_format/nchannels override.
        """
        seek = max(0, int(seek_frame))
        if self.kind == "mp3":
            stream = miniaudio.mp3_stream_file(
                self.path,
                frames_to_read=FRAMES_TO_READ,
                seek_frame=seek,
            )
        else:
            stream = miniaudio.stream_file(
                self.path,
                output_format=_SIGNED16,
                nchannels=self.channels,
                sample_rate=SAMPLE_RATE,
                frames_to_read=FRAMES_TO_READ,
                seek_frame=seek,
            )
        for block in stream:
            yield block

    def _to_mono_float(self, block):
        """int16 (any shape) -> float32 mono in [-1, 1]."""
        arr = np.asarray(block)
        if arr.ndim > 1:
            arr = arr.reshape(-1)  # (frames, channels) -> interleaved
        if np.issubdtype(arr.dtype, np.integer):
            arr = arr.astype(np.float32) / np.float32(32768.0)
        else:
            arr = arr.astype(np.float32, copy=False)
        ch = self.channels
        if ch > 1 and arr.size:
            # A torn frame can leave an odd trailing sample; dropping it keeps
            # the mono stream aligned instead of shearing channel by channel.
            usable = (arr.size // ch) * ch
            if usable != arr.size:
                arr = arr[:usable]
            if arr.size:
                arr = arr.reshape(-1, ch).mean(axis=1).astype(np.float32, copy=False)
        return arr

    def pump(self, max_seconds=300.0):
        """Decode newly-landed bytes forward from the cursor.

        Returns the number of mono samples appended. Returns 0 without touching
        the decoder when the file has not grown since the last completed pump.
        """
        size = self.size_on_disk()
        if size <= 0 or size <= self._decoded_bytes:
            return 0
        limit = int(max(0.0, float(max_seconds)) * SAMPLE_RATE)
        if limit <= 0:
            return 0

        start = int(self._buffer.size)
        chunks = []
        added = 0
        exhausted = False
        try:
            for block in self._iter_mono(start):
                chunk = self._to_mono_float(block)
                if chunk.size:
                    if added + int(chunk.size) > limit:
                        chunk = chunk[: limit - added]
                    chunks.append(chunk)
                    added += int(chunk.size)
                if added >= limit:
                    break
            else:
                exhausted = True
        except Exception:
            # Torn tail mid-frame. Keep what decoded cleanly; the next pump
            # retries from the cursor once more bytes have landed.
            exhausted = False

        if chunks:
            self._buffer = np.concatenate([self._buffer] + chunks)
        if exhausted:
            self._decoded_bytes = size
        return added

    def decode_all(self):
        """Pump until the file is exhausted; return samples appended."""
        total = 0
        for _ in range(8):  # 8h at 1h per pass; the early exit is the real stop
            n = self.pump(max_seconds=3600.0)
            if n <= 0:
                break
            total += n
        return total
