# Pure, Qt/MIDI-independent math for the Slice Editor (see
# ui/slice_editor_window.py, ui/slice_waveform_view.py). Same reasoning as
# core/sample_editing.py - kept separate so it's trivially unit-testable
# without a QApplication, a bridge, or real audio.
#
# A "slice" is a contiguous, non-overlapping region of the sample buffer
# bounded by [start, end] (the Slice Editor's own trim handles, distinct
# from the Samples tab's own SSTART/SMPEND - see slice_editor_window.py's
# own comment on why) and zero or more interior marker frames the user
# placed. slice_bounds/slice_samples always return exactly
# len(markers) + 1 slices covering [start, end] with no gaps or overlaps.


def find_nearest_zero_crossing(samples, frame, search_radius=2000):
    """Nearest frame to *frame* where the waveform actually crosses (or
    touches) zero amplitude, searched outward up to *search_radius* frames
    either side. Falls back to *frame* itself unchanged if nothing crosses
    zero within range (a sample that's silent has a crossing at every
    frame; a sample with a strong DC offset and no true crossing nearby is
    the only realistic way this happens).

    A crossing is checked BETWEEN each adjacent pair (i, i+1): either one
    of them is exactly 0, or they have opposite sign. Landing exactly on a
    zero-valued sample is preferred where one exists; otherwise whichever
    of the two straddling samples has the smaller absolute value is
    reported (the discrete case has no true zero to land on, so this is
    the closest reachable approximation - the actual point of snapping
    here is "cut where amplitude is near zero," not "cut at literally
    sample value 0").

    Searches outward from *frame* (distance 0, 1, 2, ...) so the FIRST
    match found is already the nearest one - no need to scan the whole
    window and compare distances afterward.
    """
    n = len(samples)
    if n == 0:
        return frame
    frame = max(0, min(n - 1, frame))
    if samples[frame] == 0:
        return frame

    def _crossing_at(i):
        # returns the better of (i, i+1) to land on, or None if this pair
        # doesn't straddle zero at all
        a, b = samples[i], samples[i + 1]
        if a == 0:
            return i
        if b == 0:
            return i + 1
        if (a < 0) != (b < 0):
            return i if abs(a) <= abs(b) else i + 1
        return None

    # pair indices (p, p + 1) - the two nearest pairs to frame are p == frame
    # (the pair starting AT frame, i.e. its right-hand neighbour) and
    # p == frame - 1 (the pair ending at frame, i.e. its left-hand
    # neighbour); each step of d moves one pair further out on both sides
    last_valid_index = n - 2  # _crossing_at(p) reads samples[p] and [p + 1]
    for d in range(search_radius):
        right = frame + d
        if 0 <= right <= last_valid_index:
            candidate = _crossing_at(right)
            if candidate is not None:
                return candidate
        left = frame - 1 - d
        if 0 <= left <= last_valid_index:
            candidate = _crossing_at(left)
            if candidate is not None:
                return candidate
        if right > last_valid_index and left < 0:
            break  # search window exhausted both directions
    return frame


def equal_slice_markers(start, end, count):
    """*count* - 1 interior boundary frames, evenly spaced within (start,
    end) - the "Equal Slices" quick-start action. Returns [] for count < 2
    (nothing to divide) or a degenerate start/end. Boundaries are clamped
    strictly inside (start, end) and deduplicated in order - a region too
    short for the requested count silently yields fewer, evenly-spaced-as-
    possible boundaries rather than raising, since the caller (the Slice
    Editor window) is in a far better position to tell the user "only got
    N of the M slices you asked for" than this pure function is.
    """
    if count < 2 or end <= start + 1:
        return []
    span = end - start + 1
    markers = []
    seen = set()
    for i in range(1, count):
        frame = start + round(span * i / count)
        frame = max(start + 1, min(end - 1, frame))
        if frame not in seen:
            seen.add(frame)
            markers.append(frame)
    return markers


def slice_bounds(start, end, markers):
    """[(slice_start, slice_end), ...] - inclusive frame ranges, in order,
    covering [start, end] with no gaps or overlaps. *markers* need not
    already be sorted/deduplicated/validated - anything outside the open
    interval (start, end) is dropped defensively (the Slice Editor window
    itself never lets one get placed there, but this is cheap to guard
    directly rather than trust every caller).
    """
    interior = sorted({m for m in markers if start < m < end})
    boundaries = [start] + interior + [end + 1]
    return [
        (boundaries[i], boundaries[i + 1] - 1) for i in range(len(boundaries) - 1)
    ]


def slice_samples(samples, start, end, markers):
    """The actual sub-buffers for each slice_bounds() region, in order -
    plain lists, no metadata. Always returns len(markers-after-dedup) + 1
    slices; a call with no markers at all returns a single slice covering
    the whole [start, end] region (a valid, if degenerate, "export").
    """
    return [list(samples[a : b + 1]) for a, b in slice_bounds(start, end, markers)]
