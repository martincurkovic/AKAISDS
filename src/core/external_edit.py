"""Round-tripping a sample's audio through an external editor (Qt-free, no MIDI).

The editors write the loaded audio to a WAV, open it in the user's audio editor, notice when the file is saved, and read it back.
This module is the pure part: launching the app, reading the result into what the sampler needs (the original's rate, mono for
the Akai, up to stereo for the Yamaha) and keeping the Start/End/loop markers valid when the edit changed the length.
`ui/external_edit_tracker.py` owns the files and the watching.
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

from core import sds_encoder

_LAUNCH_TIMEOUT_S = 10

#: a copy of an Akai sample's audio as it was just before an edited version replaced it (tests point this at a temp dir)
BACKUP_DIR = Path.home() / ".akaisds" / "external_edit_backups"


def safe_filename(name):
    """A file name stem for a sample name: the editor shows it, so keep the name but drop what a file system dislikes."""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(name)).strip().strip(".")
    return cleaned or "sample"


def backup_original(name, samples, rate):
    """Save the audio about to be replaced as a 16-bit mono WAV under BACKUP_DIR and return its path. Raises OSError."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"{safe_filename(name)}-{time.strftime('%Y%m%d-%H%M%S')}.wav"
    sds_encoder.write_wav_file(str(path), samples, rate, bit_depth=16)
    return str(path)


def fingerprint(path):
    """(mtime_ns, size) of a file, or None if it is gone. Two different values mean the file was saved over."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def launch_command(path, editor=None, platform=None):
    """The command that opens `path` in `editor` (an application, or None for the system default)."""
    platform = platform or sys.platform
    if platform == "darwin":
        return ["open", "-a", editor, path] if editor else ["open", path]
    if editor:
        return [editor, path]
    if platform.startswith("win"):
        return None  # os.startfile, see launch()
    return ["xdg-open", path]


def launch(path, editor=None):
    """Open `path` in the editor. Returns None on success, else a short reason."""
    command = launch_command(path, editor)
    try:
        if command is None:
            os.startfile(path)  # noqa: S606 - Windows only; the default app for the file type
            return None
        if sys.platform == "darwin":
            # `open` returns as soon as the app has the file, and says why when it can't (no such app)
            done = subprocess.run(command, capture_output=True, text=True, timeout=_LAUNCH_TIMEOUT_S)
            if done.returncode != 0:
                lines = (done.stderr or "").strip().splitlines()
                return lines[-1] if lines else "couldn't open it"
            return None
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return None
    except (OSError, subprocess.SubprocessError) as e:
        return str(e) or "couldn't open it"


def read_edited(path, target_rate):
    """Read an edited file back: returns (channels, source_rate) with every channel at `target_rate` (resampled if the editor
    changed the rate). One or two channels. Raises ValueError with a short message for an unreadable, empty or 3+ channel file."""
    channels, rate = sds_encoder.read_wav_channels(path)
    if not channels or not channels[0]:
        raise ValueError("The edited file has no audio")
    if rate != target_rate:
        channels = [sds_encoder.resample_to_target_rate(channel, rate, target_rate)[0] for channel in channels]
    return channels, rate


def mix_to_mono(channels):
    """One channel from one or two (the average), for the Akai, which holds mono samples."""
    if len(channels) == 1:
        return list(channels[0])
    left, right = channels[0], channels[1]
    return [(a + b) >> 1 for a, b in zip(left, right)]


def remap_markers(old_frames, new_frames, start, loop_start, loop_end, end):
    """Start/loop/End markers for audio whose length changed from `old_frames` to `new_frames`.

    Same length: untouched. Otherwise Start/End keep their positions (clamped into the new audio) - except that markers which
    covered the whole old sample cover the whole new one - and the loop is clamped to stay inside [start, end] in order."""
    if new_frames == old_frames:
        return start, loop_start, loop_end, end
    last = max(new_frames - 1, 0)
    if start == 0 and end == old_frames - 1:
        new_start, new_end = 0, last
    else:
        new_start = min(start, last)
        new_end = min(max(end, new_start), last)
    new_loop_start = min(max(loop_start, new_start), new_end)
    new_loop_end = min(max(loop_end, new_loop_start), new_end)
    return new_start, new_loop_start, new_loop_end, new_end
