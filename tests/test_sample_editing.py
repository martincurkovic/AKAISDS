# tests for core/sample_editing.py - pure sample-buffer math, no Qt/MIDI/
# hardware needed, should be instant

from core.sample_editing import fade_in_out_samples, trim_samples, reverse_samples


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
