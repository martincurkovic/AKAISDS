# tests for core/sample_editing.py - pure sample-buffer math, no Qt/MIDI/
# hardware needed, should be instant

from core.sample_editing import trim_samples, reverse_samples


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
