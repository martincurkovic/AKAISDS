"""Loading audio into a Yamaha A4000/A5000 NATIVELY: the bulk messages that create a sample, as pure functions (no Qt/MIDI).

MEASURED on a real A4000 (2026-10-06, `tools/a4000_load_probe.py`; the full story is in dev_docs/a4000-editor-roadmap.md):

 - the unit accepts bulk dumps SENT TO it with Bulk Protect off, never answers one, and flashes "MIDI bulk received" on its screen;
 - a WAVE dump (WD) on its own is silently dropped - it only counts together with the SAMPLE dump (SP) that links it, sent back to back
   (either order worked: waves then sample, or sample then waves). A sample whose linked wave doesn't exist at that moment is refused;
 - a wave sent under the name of an EXISTING wave replaces its audio;
 - so "load a sample" = the wave dump(s) of its left (and, for a stereo sample, right) channel, then ONE sample dump that names them.
   Built from scratch (mono 8000 frames @ 22050 Hz, stereo 12000 frames @ 44100 Hz) the unit created the wave and sample objects and every
   frame read back identical; both played correctly.

The wave dumps come from `core.yamaha_wave.build_wave_messages`. The sample dump is a REAL sample's parameters (`_TEMPLATE`, a
sample the unit itself made from an SDS dump: a user sample at its defaults - original key C3 = 60, no fine tune, no loop) with the
name, the two wave names, the rate and the wave/loop addresses filled in; the unit rewrites its own internal pointers.

Wave objects get machine-style names (`SMP nnnnnn`, like the unit's own) rather than being derived from the sample's name, so two
samples can never collide on a wave and a re-send of a sample never overwrites another's audio.
"""

import dataclasses
import random

from core import yamaha_params as yp
from core import yamaha_sysex as ysx
from core import yamaha_wave

MAX_FRAMES = 16_777_215  # the address rows are 32-bit but documented 0..16777215
MAX_RATE = 65_535  # the sampling frequency row's range is 1..65535
NAME_LENGTH = ysx.NAME_LENGTH
GUARD_WORDS = yamaha_wave.GUARD_WORDS

#: the sample dump the unit made from an SDS sample, with the name, wave names, object address and internal pointers cleared
_TEMPLATE = bytes.fromhex(
    "104100000000000000000000000000000000000000001388000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000004a04012047050120490b01e0480c01e000000000000000000000000000000000"
    "00000000000000000200000002003c3cac44ac44000013ab15bf7f003000232800000000000000000000138400001384"
    "0000138400001384000000000000000000007f04007f0000000000003f00640000007f00007f7f7f00001a400a007f7f"
    "7f00000000000000007f7f7f000000000000000c7f7f7e087f7f0000000001270001000000017f007f00c1e01e3a2000"
    "3e20e1c600001384000013844a04012047050120490b01e0480c01e000000000000000000000017f007f005a5a000000"
)
assert len(_TEMPLATE) == 336

#: where the unit mirrors a wave/loop address for the RIGHT channel: the byte offset of each left row + 4 (found by comparing a
#: template of a known length - these four hold the same number as their left twin)
_RIGHT_TWIN_ROWS = ("wave_start_address", "wave_length", "loop_start_address", "loop_length")
_SIZE_OFFSET = 20  # the common block's UL: the wave's word count (frames + guard words) - the unit rewrites it anyway


class LoadError(ValueError):
    """The audio can't be loaded as it is (empty, too long, bad name...)."""


def sample_name_for(text):
    """A sample name the unit can hold: printable ASCII, at most 16 characters, trimmed. Raises LoadError if nothing is left."""
    cleaned = "".join(c if 32 <= ord(c) < 127 else "?" for c in str(text)).strip()[:NAME_LENGTH].rstrip()
    if not cleaned:
        raise LoadError("A sample name can't be empty")
    return cleaned


def unique_name(name, taken):
    """`name`, or `name` with a number added (still within 16 characters) so it differs - case-insensitively - from every name in
    `taken`."""
    used = {t.strip().lower() for t in taken}
    if name.strip().lower() not in used:
        return name
    n = 2
    while True:
        suffix = f" {n}"
        candidate = name[: NAME_LENGTH - len(suffix)].rstrip() + suffix
        if candidate.lower() not in used:
            return candidate
        n += 1


def new_wave_name(taken, rng=random):
    """An unused machine-style wave object name, `SMP nnnnnn`."""
    used = {t.strip().lower() for t in taken}
    while True:
        candidate = f"SMP {rng.randrange(1_000_000):06d}"
        if candidate.lower() not in used:
            return candidate


@dataclasses.dataclass
class SampleLoad:
    sample_name: str
    wave_names: list  # [left] or [left, right]
    frames: int
    rate: int
    messages: list  # bytes between F0 and F7, in the order to send them

    @property
    def stereo(self):
        return len(self.wave_names) == 2

    @property
    def wire_bytes(self):
        return sum(len(m) + 2 for m in self.messages)

    @property
    def wire_seconds(self):
        return self.wire_bytes * 10 / 31250


def _put32(data, offset, value):
    data[offset : offset + 4] = int(value).to_bytes(4, "big")


#: the sample rows a caller may set when it makes a sample from an EXISTING one (an edited copy): the musical settings that belong to
#: the sound, and the wave/loop addresses (which the caller works out for the new audio). Everything else stays at the template's defaults.
CARRIED_ROWS = (
    "original_key_l", "original_key_r", "coarse_tune", "fine_tune_l", "fine_tune_r", "loop_mode",
    "wave_start_address", "wave_length", "wave_end_address", "loop_start_address", "loop_length", "loop_end_address",
)


def build_sample_payload(name, wave_names, frames, rate, *, original_key=60, params=None):
    """The 336-byte sample dump payload for a sample of `frames` frames at `rate` Hz that plays from start to end once (no loop).

    `params` ({row key: value}, keys from `CARRIED_ROWS`) overrides those defaults - how an edited copy of a sample keeps its key,
    tuning, loop mode and markers."""
    unknown = sorted(set(params or {}) - set(CARRIED_ROWS))
    if unknown:
        raise LoadError(f"Can't carry over {', '.join(unknown)}")
    data = bytearray(_TEMPLATE)
    data[2:18] = ysx.pad_name(name)
    data[yp.WAVE_NAME_L_OFFSET : yp.WAVE_NAME_L_OFFSET + 16] = ysx.pad_name(wave_names[0])
    if len(wave_names) > 1:
        data[yp.WAVE_NAME_R_OFFSET : yp.WAVE_NAME_R_OFFSET + 16] = ysx.pad_name(wave_names[1])
    _put32(data, _SIZE_OFFSET, frames + GUARD_WORDS)
    values = {
        "wave_start_address": 0,
        "wave_length": frames,
        "wave_end_address": frames,
        "loop_start_address": frames,  # a fresh user sample's loop sits at the very end (measured), mode "No loop"
        "loop_length": 0,
        "loop_end_address": frames,
        "loop_mode": 0,
        "sampling_frequency_l": rate,
        "original_key_l": original_key,
        "original_key_r": original_key,
    }
    values["sampling_frequency_r"] = rate  # (measured: a mono sample otherwise keeps the template's 44100 there)
    carried = dict(params or {})
    if "original_key_l" in carried:
        carried.setdefault("original_key_r", carried["original_key_l"])
    values.update(carried)
    for key, value in values.items():
        yp.store(yp.get("sample", key), data, value)
    for key in _RIGHT_TWIN_ROWS:  # the unit keeps a right-channel copy of these four addresses
        row = yp.get("sample", key)
        _put32(data, yp.SAMPLE_PARAMETER_BASE + row.offset + 4, values[key])
    return bytes(data)


def check_audio(channels, rate):
    """Raise `LoadError` unless the unit can hold this audio; returns the frame count."""
    if not 1 <= len(channels) <= 2:
        raise LoadError(f"Only mono or stereo samples can be loaded (got {len(channels)} channels)")
    frames = len(channels[0])
    if frames == 0:
        raise LoadError("The audio is empty")
    if any(len(c) != frames for c in channels):
        raise LoadError("The channels have different lengths")
    if frames > MAX_FRAMES:
        raise LoadError(f"Too long for the sampler ({frames:,} frames; the limit is {MAX_FRAMES:,})")
    if not 1 <= rate <= MAX_RATE:
        raise LoadError(f"The sampling rate {rate} Hz is outside what the sampler accepts (1 to {MAX_RATE})")
    return frames


def build_sample_load(
    device, name, channels, rate, *, taken_samples=(), taken_waves=(), original_key=60, rng=random, params=None
):
    """The messages that load a sample. `channels`: one list of int16 per channel (1 = mono, 2 = stereo, equal length); `rate` in Hz;
    `taken_samples` / `taken_waves`: names already on the unit (a clash is the CALLER's policy - see `unique_name` - this only
    guarantees the WAVE names are new); `params`: see `build_sample_payload`. Raises `LoadError` for audio the unit can't hold."""
    frames = check_audio(channels, rate)
    name = sample_name_for(name)
    taken = list(taken_waves)
    waves = []
    for _ in channels:
        waves.append(new_wave_name(taken + waves, rng))
    messages = []
    for wave, words in zip(waves, channels):
        messages += yamaha_wave.build_wave_messages(device, wave, [int(w) for w in words])
    payload = build_sample_payload(name, waves, frames, rate, original_key=original_key, params=params)
    messages.append(ysx.build_bulk_dump(device, "SP", ysx.pad_name(name), payload))
    return SampleLoad(name, waves, frames, rate, messages)
