"""An offline reverb for the Samples tab (Qt/MIDI-independent): the S2000 custom-OS reverb prototype, as integer arithmetic.

Copied from `prototype/reverb_model.py` of the owner's s2000-custom-os project (commit cac8680), which a patched S2000 runs bit for bit; only the
parts this editor needs are kept (no assembly state layout, no integer `setup_device` of the Timestretch page's borrowed controls). Keep the
arithmetic in `Engine.step` and `process` identical to the prototype: `tests/test_reverb.py` pins sample-for-sample results taken from it.

A small Schroeder reverb (Freeverb's family, with a fraction of the parts):

    x -> >>1 -> 4 parallel damped feedback combs -> sum -> 2 series allpasses -> wet
    out = dry * (1 - mix) + wet * mix * compensation

There is no floating point in `process`: samples and delay-line words are 16-bit signed, products are 32-bit, and anything that would not fit
is saturated, as on the sampler. Floating point is used only in `setup` (gains, delay lengths), once per run.

Controls, each 0..99:

    decay    reverb time, 0.1 s .. 5 s (exponential), measured to -60 dB
    size     delay lengths, x0.4 .. x2.0
    damping  how fast highs die in the tail: 0 = none .. 99 = the heaviest of 5 steps (see damp_k)
    mix      dry/wet, 0 = dry only, 99 = wet only

The result is longer than the input by (up to) the reverb time, so the tail rings out. As on the sampler the tail is processed in blocks and
stops at the first block in which nothing is louder than `TAIL_THRESHOLD` (-66 dBFS), so a short hit does not carry seconds of silence.
"""

import functools
import math
from collections.abc import Sequence
from dataclasses import dataclass, field

REF_RATE = 44100
BASE_COMBS = (1116, 1188, 1277, 1356)  # Freeverb's first four comb lengths, in samples at 44.1 kHz
BASE_ALLPASS = (556, 441)  # and its first two allpass lengths
INT16_MIN, INT16_MAX = -32768, 32767

MIN_RT60, MAX_RT60 = 0.1, 5.0  # the cap exists because the tail lives in sample memory
#: the tail stops at the first block of this many samples with nothing louder than TAIL_THRESHOLD (the sampler works in 0x800-sample blocks)
TAIL_BLOCK = 0x800
TAIL_THRESHOLD = 16  # -66 dBFS
#: longest result the editors will make: the S3000's 32 MB of sample RAM is 16 M samples (the same cap as core/time_stretch.py)
MAX_OUTPUT_FRAMES = 16_000_000

#: name -> (decay, size, damping, mix), the prototype's render presets
PRESETS = {
    "Small room": (29, 25, 99, 30),
    "Hall": (82, 70, 99, 35),
    "Bright": (65, 45, 0, 40),
    "Long and dark": (99, 85, 99, 40),
}
DEFAULTS = (50, 50, 30, 30)  # decay, size, damping, mix (Params' own defaults)


def sat16(v: int) -> int:
    return INT16_MAX if v > INT16_MAX else INT16_MIN if v < INT16_MIN else v


@dataclass(frozen=True)
class Params:
    decay: int = 50
    size: int = 50
    damping: int = 30
    mix: int = 30

    def __post_init__(self):
        for name in ("decay", "size", "damping", "mix"):
            v = getattr(self, name)
            if not 0 <= v <= 99:
                raise ValueError(f"{name} must be 0..99, not {v}")


def rt60_seconds(decay: int) -> float:
    return MIN_RT60 * (MAX_RT60 / MIN_RT60) ** (decay / 99)


def size_scale(size: int) -> float:
    return 0.4 + 1.6 * size / 99


NO_DAMPING_K = 15  # shifting a 16-bit value right by 15 leaves only its sign, which makes the filter below pass its input through


def damp_k(damping: int) -> int:
    """The damping filter's shift, from the 0..99 control: 5 steps from `none` to heavy.

    Each trip round a comb the tail goes through   f = o + (f - o) / 2^k   (a one-pole low-pass with 1/2^k of the old state kept).
    k = 5, 4, 3, 2, 1 keeps 3%, 6%, 12%, 25%, 50% of the old state, so the tail loses more treble each pass. Written as
    `o + (f >> k) - (o >> k)` it is anchored to `o`, so the rounding error stays under 1 LSB however heavy the damping.
    """
    level = round(damping * 5 / 99)
    return NO_DAMPING_K if level == 0 else 6 - level


def damping_step(damping: int) -> int:
    """0 (none) .. 5 (heaviest): which of the filter's steps a 0..99 damping value lands on."""
    return round(damping * 5 / 99)


def mix_q8(mix: int) -> int:
    return round(mix * 256 / 99)


@dataclass
class Setup:
    """Everything fixed for one run (what the sampler's code computes once, before the sample loop)."""

    rate: int
    rt60: float
    comb_delays: tuple[int, ...]
    comb_gains: tuple[int, ...]  # Q15
    allpass_delays: tuple[int, ...]
    shift: int  # damping filter shift (damp_k)
    dry: int  # 256 - mix
    wet: int  # Q13 wet gain, mix and level compensation included
    tail: int  # extra samples the output runs on for
    words: int = field(init=False)  # delay-line memory, in 16-bit words

    def __post_init__(self):
        self.words = sum(self.comb_delays) + sum(self.allpass_delays)


def _lengths(base: Sequence[int], scale: float, rate: int) -> tuple[int, ...]:
    """Scaled delay lengths: odd (so they share fewer factors) and all different."""
    out: list[int] = []
    for b in base:
        d = max(9, round(b * scale * rate / REF_RATE)) | 1
        while d in out:
            d += 2
        out.append(d)
    return tuple(out)


def setup(params: Params, rate: int, combs: Sequence[int] = BASE_COMBS, allpass: Sequence[int] = BASE_ALLPASS) -> Setup:
    rt60 = rt60_seconds(params.decay)
    scale = size_scale(params.size)
    comb_delays = _lengths(combs, scale, rate)
    gains = [10 ** (-3 * d / (rt60 * rate)) for d in comb_delays]  # each comb decays 60 dB in rt60
    comb_gains = tuple(min(32767, round(g * 32768)) for g in gains)
    # Level compensation: for white noise in, make the wet signal as loud as the dry. Each comb's output variance is 1/(1-g^2) of its input's
    # (the input is x>>1), the four are summed as o>>2 each, and the allpasses are unity gain (the damping filter lowers it further for
    # broadband input; that is left uncompensated, a darker tail is a quieter one).
    k = 8 / math.sqrt(sum(1 / (1 - g * g) for g in gains))
    m = mix_q8(params.mix)
    return Setup(
        rate=rate,
        rt60=rt60,
        comb_delays=comb_delays,
        comb_gains=comb_gains,
        allpass_delays=_lengths(allpass, scale, rate),
        shift=damp_k(params.damping),
        dry=256 - m,
        wet=min(32767, round(k * (m / 256) * 8192)),
        tail=int(rt60 * rate),
    )


class Engine:
    """The reverb's per-sample state and arithmetic, exactly as the sampler's code does it."""

    def __init__(self, s: Setup):
        self.s = s
        self.cbuf = [[0] * d for d in s.comb_delays]
        self.cpos = [0] * len(self.cbuf)
        self.cfilt = [0] * len(self.cbuf)
        self.abuf = [[0] * d for d in s.allpass_delays]
        self.apos = [0] * len(self.abuf)
        self.peak_store = 0
        self.sat = {"comb": 0, "allpass_v": 0, "allpass_y": 0, "wet": 0, "out": 0}  # how often each saturating step engaged

    def _sat(self, v: int, where: str) -> int:
        if v > INT16_MAX or v < INT16_MIN:
            self.sat[where] += 1
            return sat16(v)
        return v

    def step(self, x: int) -> int:
        s = self.s
        xin = x >> 1
        acc = 0
        for i in range(len(self.cbuf)):
            buf, p = self.cbuf[i], self.cpos[i]
            o = buf[p]
            f = o + (self.cfilt[i] >> s.shift) - (o >> s.shift)  # one-pole low-pass in the feedback path (shift 15: f = o)
            self.cfilt[i] = f
            v = self._sat(xin + ((f * s.comb_gains[i] + 0x4000) >> 15), "comb")
            buf[p] = v
            self.peak_store = max(self.peak_store, abs(v))
            self.cpos[i] = p + 1 if p + 1 < len(buf) else 0
            acc += o >> 2
        w = acc
        for i in range(len(self.abuf)):
            buf, p = self.abuf[i], self.apos[i]
            b = buf[p]
            v = self._sat(w + (b >> 1), "allpass_v")  # v[n] = x[n] + g v[n-N],  g = 1/2
            y = self._sat(b - (v >> 1), "allpass_y")  # y[n] = v[n-N] - g v[n]: unity gain at every frequency
            buf[p] = v
            self.apos[i] = p + 1 if p + 1 < len(buf) else 0
            w = y
        # (the wet product is saturated on its own, then added to the dry part: the 16-bit code can only do it that way)
        return self._sat(((x * s.dry) >> 8) + self._sat((w * s.wet) >> 13, "wet"), "out")


def process(
    samples: Sequence[int], params: Params, rate: int, stats: dict | None = None, trim: int | None = TAIL_THRESHOLD, block: int = TAIL_BLOCK
) -> list[int]:
    """Reverberate `samples` (16-bit signed ints).

    Without `trim` the result is len(samples) + tail samples long (the reverb time). With `trim=T` (the default: `TAIL_THRESHOLD`) the tail is
    processed `block` samples at a time and stops at the first block in which nothing is louder than T; the result ends at the last sample
    louder than T, and is never shorter than the source. The result is always a prefix of the untrimmed one.
    """
    s = setup(params, rate)
    n_in = len(samples)
    eng = Engine(s)
    out = [eng.step(x) for x in samples]
    cap = n_in + s.tail
    if trim is None:
        out += [eng.step(0) for _ in range(s.tail)]
        keep = len(out)
    else:
        last_loud = n_in - 1
        while len(out) < cap:
            chunk = [eng.step(0) for _ in range(min(block, cap - len(out)))]
            base = len(out)
            out += chunk
            loud = [i for i, v in enumerate(chunk) if abs(v) > trim]
            if not loud:
                break
            last_loud = base + loud[-1]
        keep = max(n_in, last_loud + 1)
    processed = len(out)
    out = out[:keep]
    if stats is not None:
        stats.update(setup=s, peak_store=eng.peak_store, clipped=eng.sat["comb"], out_peak=max(map(abs, out), default=0), processed=processed)
    return out


# --- for the editors ------------------------------------------------------------------------------------------------


def max_output_frames(frames: int, decay: int, rate: int) -> int:
    """The longest the result can be (the source plus the whole reverb time): what the editors check against MAX_OUTPUT_FRAMES before running."""
    return frames + int(rt60_seconds(decay) * rate)


def changes_anything(mix: int) -> bool:
    """A mix of 0 is the dry signal only: nothing to apply."""
    return mix > 0


def reverb_with_markers(samples, start, loop_start, loop_end, end, *, decay, size, damping, mix, framerate):
    """The `core.sample_editing` shape (samples + markers in, samples + markers out).

    The whole buffer goes through the reverb and comes out longer by the tail. Start and the loop stay where they are, so they still bracket the
    same material; End moves to the new last frame when it was on the old one (the tail is then part of the sample), and stays put when the
    user had set it earlier."""
    old_frames = len(samples)
    new_samples = process([int(v) for v in samples], Params(decay, size, damping, mix), framerate)
    new_last = max(len(new_samples) - 1, 0)
    if end >= old_frames - 1:
        end = new_last
    end = min(end, new_last)
    start = min(start, end)
    loop_start = min(max(loop_start, start), end)
    loop_end = min(max(loop_end, loop_start), end)
    return new_samples, start, loop_start, loop_end, end


def transform_for(**options):
    """`reverb_with_markers` with the knob values and the sample's rate filled in: decay, size, damping, mix, framerate."""
    return functools.partial(reverb_with_markers, **options)
