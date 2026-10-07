"""Editing a Yamaha A4000/A5000 sample's AUDIO (trim, reverse, fade, normalise, filter), as pure functions (no Qt/MIDI).

The unit can neither delete nor overwrite a sample over MIDI (no known opcode; a sample re-sent under its own name has ALL its
parameters reset - see core/yamaha_load.py), so an edit makes a NEW sample: the edited audio, with the original's key, tuning, loop
mode and markers carried over (`params_for_copy`), under a name derived from the original (`copy_name`). The original is never
touched. The transforms are the S3000 editor's (core/sample_editing.py - samples + markers in, samples + markers out); this module
only runs them over one or two channels and builds the carried-over parameters.

A stereo sample's channels are always edited TOGETHER and identically: the same trim, the same fade, one filter on each, and a
normalise whose gain is worked out from BOTH channels' peak (so the balance between them is kept).
"""

import array
import functools
import sys
import wave

from core import sample_editing as se
from core import yamaha_params as yp

#: the unit's loop modes that loop (owner's manual p.123): continuous and loop-to-release
LOOPING_MODES = (1, 2)

#: edit key -> (name suffix for the copy, label)
EDITS = {
    "trim": (" TRIM", "Trim"),
    "reverse": (" REV", "Reverse"),
    "fade": (" FADE", "Fade In/Out"),
    "normalise": (" NORM", "Normalise"),
    "filter": (" FILT", "Filter"),
}

NAME_LENGTH = 16


def copy_name(name, suffix):
    """`name` + `suffix`, cut so the whole stays within the unit's 16 characters (the name is shortened, never the suffix)."""
    return name.rstrip()[: NAME_LENGTH - len(suffix)].rstrip() + suffix


def transform_for(kind, *, framerate=None, filter_options=None):
    """The `core.sample_editing` function for an edit, with its extra arguments bound (all share the 5-in/5-out shape)."""
    if kind == "trim":
        return se.trim_samples
    if kind == "reverse":
        return se.reverse_samples
    if kind == "fade":
        return se.fade_in_out_samples
    if kind == "normalise":
        return se.normalize_samples
    if kind == "filter":
        return functools.partial(se.filter_samples, framerate=framerate, **(filter_options or {}))
    raise ValueError(f"unknown edit {kind!r}")


def apply_edit(kind, channels, markers, *, framerate=None, filter_options=None):
    """Run an edit over every channel. `channels`: [left] or [left, right] (lists of int16, equal length); `markers`: (start,
    loop_start, loop_end, end) in frames, inclusive, with the loop inside [start, end]. Returns (new_channels, new_markers).

    Markers come out the same for every channel (they depend only on the lengths), so the left channel's are returned."""
    transform = transform_for(kind, framerate=framerate, filter_options=filter_options)
    if kind == "normalise" and len(channels) > 1:
        # one gain for both: normalise the two channels laid end to end, then cut the result back apart
        frames = len(channels[0])
        joined, *rest = transform(list(channels[0]) + list(channels[1]), *markers)
        return [joined[:frames], joined[frames:]], tuple(markers)
    results = [transform(list(channel), *markers) for channel in channels]
    return [r[0] for r in results], tuple(results[0][1:])


def params_for_copy(source, markers, loops_override=None):
    """The sample rows an edited copy carries over from the source sample's payload `source` (see `yamaha_load.CARRIED_ROWS`),
    with the wave/loop addresses for the new audio's `markers` (start, loop_start, loop_end, end - inclusive frames).

    A loop mode that doesn't loop keeps the loop at the wave end with no length, like a fresh sample (measured on a real unit)."""
    g = lambda key: yp.extract(yp.get("sample", key), source)  # noqa: E731
    start, loop_start, loop_end, end = markers
    loop_mode = g("loop_mode")
    loops = loop_mode in LOOPING_MODES if loops_override is None else loops_override
    params = {
        "original_key_l": g("original_key_l"),
        "original_key_r": g("original_key_r"),
        "coarse_tune": g("coarse_tune"),
        "fine_tune_l": g("fine_tune_l"),
        "fine_tune_r": g("fine_tune_r"),
        "loop_mode": loop_mode,
        "wave_start_address": start,
        "wave_end_address": end + 1,
        "wave_length": end + 1 - start,
    }
    if loops:
        params.update(loop_start_address=loop_start, loop_end_address=loop_end + 1, loop_length=loop_end + 1 - loop_start)
    else:
        params.update(loop_start_address=end + 1, loop_end_address=end + 1, loop_length=0)
    return params


def write_wav(path, channels, rate):
    """A 16-bit PCM WAV of one or two channels (interleaved) - what `YamahaTransfers.send_file_queue` loads."""
    frames = len(channels[0])
    count = len(channels)
    data = array.array("h", bytes(2 * frames * count))
    for index, channel in enumerate(channels):
        data[index::count] = array.array("h", (max(-32768, min(32767, int(v))) for v in channel))
    if sys.byteorder == "big":
        data.byteswap()
    with wave.open(str(path), "wb") as out:
        out.setnchannels(count)
        out.setsampwidth(2)
        out.setframerate(int(rate))
        out.writeframes(data.tobytes())
