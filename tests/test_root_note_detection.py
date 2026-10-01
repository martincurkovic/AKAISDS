# tests for core/root_note_detection.py - deliberately not exhaustive (see
# AGENTS.md/the transient-detection precedent this was built alongside:
# pitch-detection tuning is a matter of taste/real-hardware validation,
# not a correctness problem this suite can fully pin down) - covers the
# actual DESIGNED behaviour (octave tie-break via the anchor, confidence
# separating a clean tone from noise) plus anything obviously broken
# (crashes, degenerate inputs).

import math
import os

from core import root_note_detection
from core import sds_encoder
from core.root_note_detection import detect_root_note, CONFIDENCE_THRESHOLD

_FRAMERATE = 44100
_TEST_AUDIO_PATH = os.path.join(os.path.dirname(__file__), "test_audio.wav")


def _sine(freq, framerate=_FRAMERATE, seconds=0.3, amplitude=10000):
    n = round(framerate * seconds)
    return [
        int(amplitude * math.sin(2 * math.pi * freq * i / framerate))
        for i in range(n)
    ]


def _midi_to_freq(midi_note):
    return 440.0 * (2.0 ** ((midi_note - 69) / 12.0))


def test_empty_or_too_short_returns_none():
    assert detect_root_note([], _FRAMERATE, anchor_midi_note=60) is None
    assert detect_root_note([1], _FRAMERATE, anchor_midi_note=60) is None


def test_degenerate_framerate_returns_none():
    assert detect_root_note([1, 2, 3, 4], 0, anchor_midi_note=60) is None
    assert detect_root_note([1, 2, 3, 4], -1, anchor_midi_note=60) is None


def test_silence_returns_none():
    assert detect_root_note([0] * 20000, _FRAMERATE, anchor_midi_note=60) is None


def test_a_clean_a4_sine_is_detected_as_midi_69_with_near_zero_cents():
    samples = _sine(440.0)
    result = detect_root_note(samples, _FRAMERATE, anchor_midi_note=69)
    assert result is not None
    midi_note, cents, confidence = result
    assert midi_note == 69
    assert abs(cents) < 5
    assert confidence > 0.9


def test_a_detuned_tone_reports_the_right_sign_and_rough_magnitude_of_cents():
    # ~+19.5 cents sharp of A4
    samples = _sine(445.0)
    result = detect_root_note(samples, _FRAMERATE, anchor_midi_note=69)
    assert result is not None
    midi_note, cents, confidence = result
    assert midi_note == 69
    assert 10 < cents < 30


def test_confidence_is_much_higher_for_a_clean_tone_than_for_noise():
    import random

    rng = random.Random(0)
    tone = detect_root_note(_sine(330.0), _FRAMERATE, anchor_midi_note=64)
    noise_samples = [rng.randint(-10000, 10000) for _ in range(13230)]
    noise = detect_root_note(noise_samples, _FRAMERATE, anchor_midi_note=64)

    assert tone is not None
    assert tone[2] > CONFIDENCE_THRESHOLD
    # noise may still turn up SOME spurious peak (finitely many random
    # samples can coincidentally correlate a little) - the point is it's
    # never as confident as a genuine clean tone, not that it's exactly 0
    assert noise is None or noise[2] < tone[2]


def test_anchor_selects_a_lower_octave_when_it_is_the_closer_candidate():
    # A PURE sine's own autocorrelation is exactly as strongly peaked at
    # 2x/3x/4x... the true period as at the true period itself (a signal
    # periodic with period T is trivially also periodic with 2T, 3T, ...)
    # - this is the exact real-world ambiguity detect_root_note's own
    # octave-mitigation strategy (this module's own top comment) exists
    # to resolve via the anchor, and a pure sine reproduces it cleanly
    # without needing anything synthetic/contrived.
    samples = _sine(220.0)  # true fundamental = A3 = midi 57

    agreeing = detect_root_note(samples, _FRAMERATE, anchor_midi_note=57)
    assert agreeing is not None
    assert agreeing[0] == 57

    anchored_down = detect_root_note(samples, _FRAMERATE, anchor_midi_note=57 - 12)
    assert anchored_down is not None
    assert anchored_down[0] == 57 - 12


def test_result_note_is_within_the_declared_spinbox_range():
    samples = _sine(_midi_to_freq(100))
    result = detect_root_note(samples, _FRAMERATE, anchor_midi_note=100)
    assert result is not None
    midi_note, _cents, _confidence = result
    assert root_note_detection._MIN_MIDI_NOTE <= midi_note <= root_note_detection._MAX_MIDI_NOTE


def test_confidence_does_not_swing_low_based_on_anchor_alone():
    # regression test for a real, reported bug: picking whichever peak was
    # nearest the anchor, full stop, let a badly-set (or default) anchor
    # reach past a strong periodicity and grab a much weaker, spurious
    # peak instead - the SAME audio then reported wildly different
    # "confidence" (as low as ~37%, as high as ~79%, across these same
    # five anchors) purely because of the anchor, with nothing about the
    # actual audio changing. Uses the real chord-stab fixture this was
    # found against (tests/test_audio.wav, the demo sampler's own fixed
    # test audio - see AGENTS.md) and the exact loop-region window
    # program_editor_window.py's own _detect_root_note_analysis_window
    # picks for it, not a synthetic signal - deliberately messier,
    # realistic material.
    samples, framerate = sds_encoder.read_wav_samples(_TEST_AUDIO_PATH)
    window = samples[7792 : 7792 + 13230]

    confidences = [
        detect_root_note(window, framerate, anchor_midi_note=anchor)[2]
        for anchor in (21, 48, 60, 72, 93)
    ]
    # before the fix: 0.369, 0.418, 0.361, 0.361, 0.788 - now every
    # candidate the anchor can actually reach is a genuinely strong peak,
    # not an arbitrary weak one that merely happened to be closest
    assert all(c >= 0.6 for c in confidences)
