"""Granular time stretch in the style of the Akai samplers (Qt/MIDI-independent).

A Python port of `TimeStretch.h` from https://github.com/dar-io-p/potenza-time-stretch (GPL-3.0, like this project; see
src/ui/help/THIRD_PARTY_NOTICES.md). Two overlapping grains play the source at its own speed (so the pitch is kept); every
`0.6 x grain` output samples a new grain starts `0.6 x grain / factor` source samples further on and the two are crossfaded
linearly over the 40% overlap. A factor above 1 therefore repeats material (longer), below 1 skips it (shorter).

`TimeStretch` is the upstream struct line for line (forward direction and the reverse/loop helpers included), so it can be
compared against the C++ - `tests/fixtures/time_stretch_golden.json` was made by compiling the real header
(`tools/make_time_stretch_golden.py`). The upstream struct is a playback engine, advanced once per output sample; `stretch`
below drives it offline: one output sample per step at a pitch delta of 1, for `round(len x factor)` samples.

Not verified: that this matches the sampler's OWN time stretch sample for sample (the upstream project says "akai style";
the S2000/S3000's actual algorithm and defaults are not published). Pitch-compensated mode (`comp`) is the same engine reading the source `ratio` times
faster while the grains keep the length: a pitch shift at constant length (`pitch_shift`).
"""

import functools

#: the UI's range for the length (output / input, in %) and the grain size (ms); the default grain is a guess, not the sampler's
MIN_PERCENT, MAX_PERCENT = 25, 400
MIN_GRAIN_MS, MAX_GRAIN_MS, DEFAULT_GRAIN_MS = 5, 500, 40
#: longest result the editors will make: the S3000's 32 MB of sample RAM is 16 M samples
MAX_OUTPUT_FRAMES = 16_000_000

#: crossfade amount of upstream's `c` (fraction of a grain that overlaps the next)
CROSSFADE = 0.4


class TimeStretch:
    def __init__(self):
        self.phase = [0.0, 0.0]
        self.grain = [0.0, 0.0]
        self.total_phase = 0.0
        self.grain_offset = 0.0
        self.grain_playing = 0
        self.stretch_factor = 1.0
        self.grain_size = 1.0
        self.stretch_comp = False
        self.pitch_compensator = 1.0
        self.stretch_factor_inverse = 1.0
        self.c = CROSSFADE
        self.c_prime = 1.0 - self.c
        self.m = 1.0 / (self.grain_size * self.c)
        self.f1 = self.grain_size * self.c
        self.f2 = self.grain_size * self.c_prime

    def update(self, grain_size, stretch_factor, comp=False):
        """grain_size in samples, stretch_factor = output length / input length; `comp` makes it a pitch shift instead."""
        self.pitch_compensator = stretch_factor if comp else 1.0
        self.stretch_comp = comp
        self.grain_size = grain_size
        self.stretch_factor = stretch_factor
        self.m = 1.0 / (grain_size * self.c)
        self.f1 = grain_size * self.c
        self.f2 = grain_size * self.c_prime
        self.stretch_factor_inverse = 1.0 / stretch_factor
        self.grain_offset = grain_size * self.c_prime * self.stretch_factor_inverse

    def reset(self, sample_pos, direction=1):
        self.grain_playing = 0
        if direction == 1:
            self.phase = [0.0, -1.0]
            self.grain = [sample_pos, sample_pos]
        else:
            self.phase = [self.grain_size, -1.0]
            self.grain = [sample_pos - self.grain_size, sample_pos - self.grain_size]
        self.total_phase = sample_pos

    def loop(self, loop_position, direction=1):
        other = 1 - self.grain_playing
        if direction == -1:
            self.grain[other] = loop_position - self.grain_size
            self.phase[other] = self.grain_size
        else:
            self.grain[other] = loop_position
            self.phase[other] = 0.0
        self.grain_playing = other
        self.total_phase = loop_position

    def advance(self, pitch_delta, sample_start, sample_end, direction=1):
        pitch_delta *= self.pitch_compensator
        playing = self.grain_playing
        other = 1 - playing
        phase, grain = self.phase, self.grain
        if direction != -1:
            self.total_phase += pitch_delta * self.stretch_factor_inverse
            if grain[playing] + phase[playing] < sample_end:
                phase[playing] += pitch_delta
            if phase[other] > -1 and grain[other] + phase[other] < sample_end:
                phase[other] += pitch_delta
            if phase[other] >= self.grain_size:
                phase[other] = -1.0
            if phase[playing] >= self.f2:
                phase[other] = 0.0
                grain[other] = grain[playing] + self.grain_offset
                self.grain_playing = 1 - playing
        else:
            self.total_phase -= pitch_delta * self.stretch_factor_inverse
            if grain[playing] + phase[playing] > sample_start:
                phase[playing] -= pitch_delta
            if phase[other] > -1 and grain[other] + phase[other] > sample_start:
                phase[other] -= pitch_delta
            if phase[other] <= 0:
                phase[other] = -1.0
            if phase[playing] <= self.f1:
                phase[other] = self.grain_size
                grain[other] = grain[playing] - self.grain_offset
                self.grain_playing = 1 - playing
        return self.total_phase

    def get(self, data, interp, direction=1):
        """The current output sample. `interp(position, data)` reads `data` at a fractional position."""
        phase, grain = self.phase, self.grain
        playing = self.grain_playing
        if phase[0] != -1 and phase[1] != -1:
            phi = phase[playing] if direction != -1 else self.grain_size - phase[playing]
            window = self.m * phi
            x0 = grain[playing] + phase[playing]
            x1 = grain[1 - playing] + phase[1 - playing]
            return interp(x0, data) * window + interp(x1, data) * (1 - window)
        return interp(grain[playing] + phase[playing], data)


def _linear(position, data):
    """Linear interpolation, holding the first/last sample outside the data."""
    last = len(data) - 1
    if position <= 0:
        return data[0]
    if position >= last:
        return data[last]
    index = int(position)
    frac = position - index
    return data[index] * (1 - frac) + data[index + 1] * frac


def grain_frames_for(grain_ms, framerate):
    return max(2.0, grain_ms * framerate / 1000.0)


def output_length(frames, factor):
    return max(1, round(frames * factor))


def _render(samples, factor, comp, grain_frames, out_frames):
    """Drive the engine for `out_frames` output samples. `comp` False: `factor` is the length ratio; True: the pitch ratio."""
    frames = len(samples)
    engine = TimeStretch()
    engine.update(max(2.0, float(grain_frames)), factor, comp)
    engine.reset(0, 1)
    last = frames - 1
    out = [0.0] * out_frames
    get, advance = engine.get, engine.advance
    for i in range(out_frames):
        out[i] = get(samples, _linear, 1)
        advance(1.0, 0, last, 1)
    if isinstance(samples[0], int):
        return [max(-32768, min(32767, round(v))) for v in out]
    return out


def stretch(samples, factor, grain_frames):
    """`samples` (ints or floats) time-stretched to `round(len x factor)` samples at the same pitch. Ints come out as clamped
    16-bit ints; floats are returned as they are. `grain_frames` is the grain size in samples."""
    if not len(samples):
        return []
    if factor <= 0:
        raise ValueError("The stretch factor must be positive")
    return _render(samples, factor, False, grain_frames, output_length(len(samples), factor))


def pitch_ratio(semitones, cents=0):
    """The playback-speed ratio of a pitch change (+12 semitones = 2.0)."""
    return 2.0 ** ((semitones + cents / 100.0) / 12.0)


def pitch_shift(samples, ratio, grain_frames):
    """`samples` shifted in pitch by `ratio` (2.0 = an octave up) with the length unchanged: the upstream engine's pitch-
    compensated mode. Grains are as unaligned as in `stretch`, so a tone can warble."""
    if not len(samples):
        return []
    if ratio <= 0:
        raise ValueError("The pitch ratio must be positive")
    return _render(samples, ratio, True, grain_frames, len(samples))


def stretch_with_markers(samples, start, loop_start, loop_end, end, *, factor, grain_frames):
    """The `core.sample_editing` shape (samples + markers in, samples + markers out): the whole buffer is stretched and every
    marker moves in proportion, so Start/End still bracket the same material and the loop keeps its place."""
    old_frames = len(samples)
    new_samples = stretch(samples, factor, grain_frames)
    new_frames = len(new_samples)
    if old_frames <= 1 or new_frames <= 1:
        return new_samples, 0, 0, max(new_frames - 1, 0), max(new_frames - 1, 0)
    scale = (new_frames - 1) / (old_frames - 1)
    new_start, new_loop_start, new_loop_end, new_end = (
        min(round(m * scale), new_frames - 1) for m in (start, loop_start, loop_end, end)
    )
    new_loop_start = min(max(new_loop_start, new_start), new_end)
    new_loop_end = min(max(new_loop_end, new_loop_start), new_end)
    return new_samples, new_start, new_loop_start, new_loop_end, new_end


def pitch_shift_with_markers(samples, start, loop_start, loop_end, end, *, ratio, grain_frames):
    """`pitch_shift` in the `core.sample_editing` shape. The length doesn't change, so the markers don't either."""
    return pitch_shift(samples, ratio, grain_frames), start, loop_start, loop_end, end


#: the two edits the dialog offers, by key -> (function in the sample_editing shape, name suffix for a Yamaha copy, label)
EDITS = {
    "stretch": (stretch_with_markers, " STR", "Time Stretch"),
    "pitch": (pitch_shift_with_markers, " PITCH", "Pitch Shift"),
}


def transform_for(kind, **options):
    """The `core.sample_editing`-shaped transform for an edit `kind` ("stretch": factor, grain_frames; "pitch": ratio, grain_frames)."""
    return functools.partial(EDITS[kind][0], **options)
