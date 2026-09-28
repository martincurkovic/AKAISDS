# tests for core/sample_editing.py - pure sample-buffer math, no Qt/MIDI/
# hardware needed, should be instant

import math

import pytest

from core.sample_editing import (
    _MAX_AMPLITUDE,
    fade_in_out_samples,
    filter_samples,
    normalize_samples,
    trim_samples,
    reverse_samples,
)


def test_trim_keeps_only_the_marked_region():
    samples = list(range(100))  # samples[i] == i, easy to eyeball
    new_samples, start, loop_start, loop_end, end = trim_samples(
        samples, start=20, loop_start=40, loop_end=70, end=90
    )

    assert new_samples == list(range(20, 91))
    assert start == 0
    assert end == len(new_samples) - 1 == 70
    # loop re-based to the new buffer's own origin, same absolute region
    assert loop_start == 20
    assert loop_end == 50
    assert new_samples[loop_start] == 40  # still the same original sample
    assert new_samples[loop_end] == 70


def test_trim_to_the_whole_sample_is_a_no_op_on_content():
    samples = list(range(50))
    new_samples, start, loop_start, loop_end, end = trim_samples(
        samples, start=0, loop_start=10, loop_end=30, end=49
    )

    assert new_samples == samples
    assert (start, loop_start, loop_end, end) == (0, 10, 30, 49)


def test_trim_a_single_frame_region():
    samples = list(range(10))
    new_samples, start, loop_start, loop_end, end = trim_samples(
        samples, start=5, loop_start=5, loop_end=5, end=5
    )

    assert new_samples == [5]
    assert (start, loop_start, loop_end, end) == (0, 0, 0, 0)


def test_reverse_reverses_the_buffer():
    samples = [10, 20, 30, 40, 50]
    new_samples, *_ = reverse_samples(samples, start=0, loop_start=1, loop_end=3, end=4)
    assert new_samples == [50, 40, 30, 20, 10]


def test_reverse_mirrors_start_and_end_around_the_buffer():
    samples = list(range(100))  # frame_count = 100
    _new_samples, start, _ls, _le, end = reverse_samples(
        samples, start=10, loop_start=30, loop_end=60, end=90
    )
    # frame i -> frame_count - 1 - i = 99 - i
    assert start == 99 - 90
    assert end == 99 - 10


def test_reverse_mirrors_loop_points_and_swaps_their_order():
    samples = list(range(100))
    _new_samples, _s, loop_start, loop_end, _e = reverse_samples(
        samples, start=10, loop_start=30, loop_end=60, end=90
    )
    assert loop_start == 99 - 60
    assert loop_end == 99 - 30
    assert loop_start < loop_end  # still start <= end after mirroring


def test_reverse_preserves_loop_length():
    # the loop's own LENGTH must be invariant under reversal - only its
    # position mirrors - see reverse_samples' own comment
    samples = list(range(200))
    original_length = 60 - 25
    _new_samples, _s, loop_start, loop_end, _e = reverse_samples(
        samples, start=5, loop_start=25, loop_end=60, end=150
    )
    assert loop_end - loop_start == original_length


def test_reverse_is_its_own_inverse():
    # reversing twice must reproduce the exact original samples and markers
    samples = [7, 3, 9, 1, 8, 2, 5, 4, 6, 0]
    once = reverse_samples(samples, start=1, loop_start=2, loop_end=7, end=9)
    twice = reverse_samples(once[0], *once[1:])
    assert twice[0] == samples
    assert twice[1:] == (1, 2, 7, 9)


def test_reverse_a_sample_with_no_loop_region_at_all():
    # start == loop_start == loop_end == end (a non-looping sample, same
    # shape _demo_loop_points/a freshly-trimmed one-frame sample can produce)
    samples = list(range(20))
    _new_samples, start, loop_start, loop_end, end = reverse_samples(
        samples, start=0, loop_start=0, loop_end=0, end=19
    )
    assert (start, loop_start, loop_end, end) == (0, 19, 19, 19)


# --- fade_in_out_samples ---------------------------------------------------
# fades the LEAD-IN/LEAD-OUT around [start, end], not the region itself:
# silence at frame 0 -> full volume at Start, and full volume at End ->
# silence at the buffer's own last frame. [start, end] is left untouched -
# see its own docstring and the AGENTS.md design discussion (corrected
# 2026-09-23 after the first version faded the wrong region entirely).


def test_fade_in_ramps_from_silence_at_frame_zero_to_full_at_start():
    samples = [1000] * 11  # start=10 -> ramp spans frames 0..10 (11 frames)
    new_samples, start, loop_start, loop_end, end = fade_in_out_samples(
        samples, start=10, loop_start=10, loop_end=10, end=10
    )
    assert new_samples[0] == 0  # silence at frame 0
    assert new_samples[5] == 500  # linear midpoint
    assert new_samples[10] == 1000  # full volume exactly at Start
    # fading never resizes the buffer or moves any marker
    assert len(new_samples) == 11
    assert (start, loop_start, loop_end, end) == (10, 10, 10, 10)


def test_fade_out_ramps_from_full_at_end_to_silence_at_the_last_frame():
    samples = [1000] * 11  # end=0 -> ramp spans frames 0..10 (11 frames)
    new_samples, *_ = fade_in_out_samples(
        samples, start=0, loop_start=0, loop_end=0, end=0
    )
    assert new_samples[0] == 1000  # full volume exactly at End
    assert new_samples[5] == 500  # linear midpoint
    assert new_samples[10] == 0  # silence at the buffer's own last frame


def test_fade_leaves_the_marked_start_end_region_itself_untouched():
    samples = [1000] * 10 + [2000] * 20 + [3000] * 10
    new_samples, *_ = fade_in_out_samples(
        samples, start=10, loop_start=15, loop_end=25, end=29
    )
    # [start, end] (frames 10..29) is the flat, unfaded middle
    assert new_samples[10:30] == [2000] * 20
    # frame 0 (start of the lead-in) is silent, frame 9 (last of the
    # lead-in) is one linear step short of full volume
    assert new_samples[0] == 0
    assert new_samples[9] == round(1000 * 9 / 10)
    # frame 39 (last of the lead-out) is silent, frame 30 (first of the
    # lead-out) is one linear step down from full volume
    assert new_samples[39] == 0
    assert new_samples[30] == round(3000 * 9 / 10)


def test_fade_with_start_at_frame_zero_skips_the_fade_in():
    samples = [1000] * 50
    new_samples, *_ = fade_in_out_samples(
        samples, start=0, loop_start=0, loop_end=0, end=40
    )
    assert new_samples[0] == 1000  # nothing before Start to fade in from


def test_fade_with_end_at_the_last_frame_skips_the_fade_out():
    samples = [1000] * 50
    new_samples, *_ = fade_in_out_samples(
        samples, start=10, loop_start=10, loop_end=10, end=49
    )
    assert new_samples[49] == 1000  # nothing after End to fade out into


def test_fade_gain_never_exceeds_the_original_amplitude():
    # a linear fade can only ever attenuate, never amplify/clip
    samples = [12345, -12345] * 50
    new_samples, *_ = fade_in_out_samples(
        samples, start=20, loop_start=20, loop_end=20, end=80
    )
    assert all(abs(v) <= 12345 for v in new_samples)


def test_fade_is_a_no_op_when_start_and_end_already_cover_the_whole_buffer():
    samples = list(range(10))
    new_samples, start, loop_start, loop_end, end = fade_in_out_samples(
        samples, start=0, loop_start=0, loop_end=0, end=9
    )
    assert new_samples == samples
    assert (start, loop_start, loop_end, end) == (0, 0, 0, 9)


def test_fade_in_and_out_can_both_apply_at_once():
    samples = [1000] * 20
    new_samples, *_ = fade_in_out_samples(
        samples, start=5, loop_start=5, loop_end=5, end=15
    )
    assert new_samples[0] == 0
    assert new_samples[5] == 1000
    assert new_samples[5:16] == [1000] * 11
    assert new_samples[15] == 1000
    assert new_samples[19] == 0


# --- normalize_samples -------------------------------------------------------
# a single uniform gain over the WHOLE buffer (not just [start, end] -
# unlike trim/fade, matches reverse_samples' own whole-buffer scope) so the
# loudest sample hits _MAX_AMPLITUDE exactly.


def test_normalize_scales_the_peak_to_max_amplitude():
    samples = [1000, -2000, 500, -1500]
    new_samples, *_ = normalize_samples(
        samples, start=0, loop_start=0, loop_end=0, end=3
    )
    assert max(abs(v) for v in new_samples) == _MAX_AMPLITUDE
    # the loudest original sample (-2000) is what actually hits the ceiling
    assert new_samples[1] == -_MAX_AMPLITUDE


def test_normalize_preserves_every_samples_relative_ratio_and_sign():
    samples = [1000, -2000, 500, -1500, 0]
    new_samples, *_ = normalize_samples(
        samples, start=0, loop_start=0, loop_end=0, end=4
    )
    gain = _MAX_AMPLITUDE / 2000
    assert new_samples == [
        round(1000 * gain), round(-2000 * gain), round(500 * gain),
        round(-1500 * gain), 0,
    ]


def test_normalize_never_exceeds_int16_range_either_direction():
    samples = [32767, -32768, 100, -50]
    new_samples, *_ = normalize_samples(
        samples, start=0, loop_start=0, loop_end=0, end=3
    )
    assert all(-32768 <= v <= 32767 for v in new_samples)


def test_normalize_a_silent_sample_is_a_no_op():
    samples = [0, 0, 0, 0]
    new_samples, start, loop_start, loop_end, end = normalize_samples(
        samples, start=0, loop_start=1, loop_end=2, end=3
    )
    assert new_samples == samples
    assert (start, loop_start, loop_end, end) == (0, 1, 2, 3)


def test_normalize_already_at_peak_is_a_no_op_on_content():
    samples = [_MAX_AMPLITUDE, -1000, 200]
    new_samples, *_ = normalize_samples(
        samples, start=0, loop_start=0, loop_end=0, end=2
    )
    assert new_samples == samples


def test_normalize_touches_the_whole_buffer_not_just_start_end():
    # unlike trim_samples/fade_in_out_samples, normalize_samples has no
    # [start, end]-relative behaviour at all - everything scales, including
    # material outside the marked region
    samples = [100] * 5 + [2000] * 5 + [100] * 5
    new_samples, *_ = normalize_samples(
        samples, start=5, loop_start=5, loop_end=5, end=9
    )
    gain = _MAX_AMPLITUDE / 2000
    assert new_samples[0] == round(100 * gain)  # before start - still scaled
    assert new_samples[14] == round(100 * gain)  # after end - still scaled
    assert new_samples[5] == _MAX_AMPLITUDE  # the peak itself


def test_normalize_keeps_every_marker_unchanged():
    samples = [100, -5000, 300]
    _new_samples, start, loop_start, loop_end, end = normalize_samples(
        samples, start=0, loop_start=1, loop_end=2, end=2
    )
    assert (start, loop_start, loop_end, end) == (0, 1, 2, 2)


# --- filter_samples (highpass/lowpass biquad) --------------------------------


def _sine(freq_hz, framerate, n_frames, amplitude=10000):
    return [
        int(round(amplitude * math.sin(2 * math.pi * freq_hz * i / framerate)))
        for i in range(n_frames)
    ]


def _rms(samples):
    return math.sqrt(sum(s * s for s in samples) / len(samples))


# every RMS comparison below skips the filter's own settling transient at
# the very start of the buffer (the biquad's internal state starts at
# zero, so the first handful of samples don't yet reflect its steady-state
# response) - comparing only the settled tail is what actually isolates
# "does this filter do what a highpass/lowpass should," not an artifact of
# where the buffer happens to start
_SETTLE = 500


def test_lowpass_attenuates_a_tone_well_above_cutoff():
    framerate = 44100
    tone = _sine(8000, framerate, 4410)  # 8kHz tone
    filtered, *_ = filter_samples(
        tone, 0, 0, len(tone) - 1, len(tone) - 1, framerate,
        lowpass_enabled=True, lowpass_cutoff_hz=1000,
    )
    assert _rms(filtered[_SETTLE:]) < _rms(tone[_SETTLE:]) * 0.5


def test_lowpass_mostly_passes_a_tone_well_below_cutoff():
    framerate = 44100
    tone = _sine(100, framerate, 4410)  # 100Hz tone
    filtered, *_ = filter_samples(
        tone, 0, 0, len(tone) - 1, len(tone) - 1, framerate,
        lowpass_enabled=True, lowpass_cutoff_hz=5000,
    )
    assert _rms(filtered[_SETTLE:]) > _rms(tone[_SETTLE:]) * 0.8


def test_highpass_attenuates_a_tone_well_below_cutoff():
    framerate = 44100
    tone = _sine(100, framerate, 4410)  # 100Hz tone
    filtered, *_ = filter_samples(
        tone, 0, 0, len(tone) - 1, len(tone) - 1, framerate,
        highpass_enabled=True, highpass_cutoff_hz=2000,
    )
    assert _rms(filtered[_SETTLE:]) < _rms(tone[_SETTLE:]) * 0.5


def test_highpass_mostly_passes_a_tone_well_above_cutoff():
    framerate = 44100
    tone = _sine(8000, framerate, 4410)  # 8kHz tone
    filtered, *_ = filter_samples(
        tone, 0, 0, len(tone) - 1, len(tone) - 1, framerate,
        highpass_enabled=True, highpass_cutoff_hz=500,
    )
    assert _rms(filtered[_SETTLE:]) > _rms(tone[_SETTLE:]) * 0.8


def test_neither_filter_enabled_is_a_no_op():
    samples = list(range(-500, 500))
    new_samples, *_ = filter_samples(
        samples, 0, 0, len(samples) - 1, len(samples) - 1, 44100
    )
    assert new_samples == samples


def test_both_filters_enabled_together_form_a_bandpass():
    # a highpass @ 2000Hz + a lowpass @ 3000Hz should pass a 2500Hz tone
    # (inside the band) far better than either a 200Hz tone (below the
    # highpass) or an 8000Hz tone (above the lowpass)
    framerate = 44100
    n_frames = 4410
    in_band = _sine(2500, framerate, n_frames)
    below_band = _sine(200, framerate, n_frames)
    above_band = _sine(8000, framerate, n_frames)

    def _band(tone):
        filtered, *_ = filter_samples(
            tone, 0, 0, len(tone) - 1, len(tone) - 1, framerate,
            highpass_enabled=True, highpass_cutoff_hz=2000,
            lowpass_enabled=True, lowpass_cutoff_hz=3000,
        )
        return _rms(filtered[_SETTLE:])

    in_band_rms = _band(in_band)
    assert in_band_rms > _rms(in_band[_SETTLE:]) * 0.5
    assert _band(below_band) < _rms(below_band[_SETTLE:]) * 0.2
    assert _band(above_band) < _rms(above_band[_SETTLE:]) * 0.2


def test_steeper_slope_attenuates_more_near_the_cutoff():
    # a tone only one octave above a lowpass cutoff should be attenuated
    # more by a steep (48dB/octave) slope than a gentle (12dB/octave) one -
    # the entire point of a slope control
    framerate = 44100
    cutoff = 1000
    tone = _sine(cutoff * 2, framerate, 4410)  # one octave above cutoff

    def _filtered_rms(slope):
        filtered, *_ = filter_samples(
            tone, 0, 0, len(tone) - 1, len(tone) - 1, framerate,
            lowpass_enabled=True, lowpass_cutoff_hz=cutoff,
            lowpass_slope_db_per_octave=slope,
        )
        return _rms(filtered[_SETTLE:])

    assert _filtered_rms(48) < _filtered_rms(12)


def test_filter_rejects_a_slope_that_isnt_a_multiple_of_12():
    with pytest.raises(ValueError):
        filter_samples(
            [0] * 100, 0, 0, 99, 99, 44100,
            lowpass_enabled=True, lowpass_cutoff_hz=1000,
            lowpass_slope_db_per_octave=20,
        )


def test_filter_requires_a_cutoff_when_enabled():
    with pytest.raises(ValueError):
        filter_samples(
            [0] * 100, 0, 0, 99, 99, 44100,
            highpass_enabled=True, highpass_cutoff_hz=None,
        )


def test_filter_keeps_every_marker_unchanged():
    samples = [100, -200, 300, -400]
    _new_samples, start, loop_start, loop_end, end = filter_samples(
        samples, 0, 1, 2, 3, 44100, lowpass_enabled=True, lowpass_cutoff_hz=1000
    )
    assert (start, loop_start, loop_end, end) == (0, 1, 2, 3)


def test_filter_output_length_matches_input():
    samples = list(range(-500, 500))
    new_samples, *_ = filter_samples(
        samples, 0, 0, len(samples) - 1, len(samples) - 1, 44100,
        highpass_enabled=True, highpass_cutoff_hz=1000,
    )
    assert len(new_samples) == len(samples)


def test_filter_output_stays_within_int16_range():
    samples = [32767, -32768] * 500
    new_samples, *_ = filter_samples(
        samples, 0, 0, len(samples) - 1, len(samples) - 1, 44100,
        lowpass_enabled=True, lowpass_cutoff_hz=5000,
    )
    assert all(-32768 <= v <= 32767 for v in new_samples)


def test_filter_touches_the_whole_buffer_not_just_start_end():
    # same whole-buffer scope as reverse_samples/normalize_samples - a
    # high-frequency tone entirely OUTSIDE [start, end] must still get
    # attenuated by a lowpass, not left untouched
    framerate = 44100
    tone = _sine(8000, framerate, 4410)
    filtered, *_ = filter_samples(
        tone, 2000, 2000, 2000, 2200, framerate,
        lowpass_enabled=True, lowpass_cutoff_hz=1000,
    )
    outside_start_original = tone[:2000]
    outside_start_filtered = filtered[:2000]
    assert _rms(outside_start_filtered[_SETTLE:]) < _rms(outside_start_original[_SETTLE:]) * 0.5
