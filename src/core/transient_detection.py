# Pure, Qt/MIDI-independent transient/onset detection for the Slice
# Editor's live "Sensitivity" slider (see ui/slice_editor_window.py) -
# same reasoning as core/sample_slicing.py: kept separate so it's
# trivially unit-testable without a QApplication, a bridge, or real audio.
#
# Hand-rolled energy-flux onset detection, deliberately simple - see
# AGENTS.md's Slice Editor section: automatic transient detection was cut
# from the first pass as "a separate, much larger undertaking, if it's
# ever added, it should produce the same plain frame-index marker list
# this already consumes." This is that follow-up. No FFT, no new
# dependency (numpy/scipy/aubio etc. were all considered and rejected -
# see the conversation this was built from) - onset detection tuning is
# inherently a matter of taste, not a correctness problem this app can
# solve for the user, and manual add/drag/delete already exist for
# whatever this gets wrong. Good enough for chopping a drum break, not a
# research-grade onset detector.
#
# Algorithm: short-time RMS energy over fixed-size, non-overlapping
# windows, half-wave-rectified frame-to-frame flux (only RISES count -
# a transient is a sudden onset of energy, not a decay), then plain
# threshold + local-peak + minimum-gap picking. *sensitivity* (0-100,
# matching ReCycle's own "Sensitivity" knob convention) sets the threshold
# as a fraction of the loudest flux actually observed in this buffer - 100
# finds almost every real rise above silence, 0 is fully OFF (see
# markers_from_flux's own docstring on why 0 is special-cased rather than
# just "a very high threshold").
#
# Split into compute_flux() (the one-time, O(n) analysis pass over the
# whole buffer) and markers_from_flux() (a cheap, O(len(flux)) peak-pick
# that's the only part depending on *sensitivity*) specifically so the
# Slice Editor's sensitivity slider can live-update as it's dragged:
# compute_flux() runs once per loaded sample, markers_from_flux() runs
# once per slider tick.

_WINDOW_MS = 10
_MIN_GAP_MS = 30  # minimum spacing enforced between two detected onsets


def _rms_windows(samples, window_frames):
    """RMS energy per non-overlapping window of *window_frames* samples,
    in order. The last window is included even if shorter than
    window_frames (the sample's own tail), rather than silently dropped.
    """
    if window_frames <= 0 or not samples:
        return []
    energies = []
    for start in range(0, len(samples), window_frames):
        chunk = samples[start : start + window_frames]
        mean_square = sum(v * v for v in chunk) / len(chunk)
        energies.append(mean_square**0.5)
    return energies


def compute_flux(samples, framerate):
    """The one-time analysis pass: (flux, window_frames), or (None, None)
    if there's nothing resembling an onset anywhere in this buffer (too
    few frames to form at least two windows, or no energy variation at
    all - silence, or a constant DC value). Meant to be computed ONCE per
    loaded sample and reused across many markers_from_flux() calls, not
    recomputed on every UI tick - see this module's own top comment.
    """
    n = len(samples)
    if n < 2 or framerate <= 0:
        return None, None
    window_frames = max(1, round(framerate * _WINDOW_MS / 1000))
    energies = _rms_windows(samples, window_frames)
    if len(energies) < 2:
        return None, None

    flux = [0.0] * len(energies)
    for i in range(1, len(energies)):
        flux[i] = max(0.0, energies[i] - energies[i - 1])

    if max(flux) <= 0:
        return None, None
    return flux, window_frames


def markers_from_flux(flux, window_frames, sensitivity, frame_count, min_gap_ms=_MIN_GAP_MS):
    """Interior frame indices - same shape core.sample_slicing.
    equal_slice_markers returns - picked from an already-computed
    (flux, window_frames) pair (see compute_flux). Cheap enough to call on
    every tick of a live-dragged slider.

    sensitivity <= 0 always returns [] outright - "fully off," the
    slider's own default/rest position - rather than whatever a threshold
    of exactly peak_flux would happen to still pick out. Without this
    special case, 0 would mean "only the single loudest hit(s)," not
    "nothing," which doesn't match dragging the slider up from a clean
    slate at rest.
    """
    if flux is None or not flux:
        return []
    sensitivity = max(0, min(100, sensitivity))
    if sensitivity == 0:
        return []
    peak_flux = max(flux)
    if peak_flux <= 0:
        return []
    threshold = peak_flux * (1 - sensitivity / 100.0)

    min_gap_windows = max(1, round(min_gap_ms / _WINDOW_MS))

    onsets = []
    last_onset_index = -min_gap_windows
    for i, value in enumerate(flux):
        # value == 0 is never a real onset, regardless of how low
        # *threshold* goes (sensitivity=100 => threshold=0) - without this,
        # a long silent/flat stretch (a whole run of equal 0.0 flux values)
        # would read as one giant "local plateau" and fire constantly,
        # spaced only by min_gap_windows, on total silence.
        if value <= 0 or value < threshold:
            continue
        left = flux[i - 1] if i > 0 else 0.0
        right = flux[i + 1] if i + 1 < len(flux) else 0.0
        if value < left or value < right:
            continue  # not the local peak of its own rise
        if i - last_onset_index < min_gap_windows:
            continue
        onsets.append(i)
        last_onset_index = i

    return [min(frame_count - 1, i * window_frames) for i in onsets]


def detect_transients(samples, sensitivity, framerate, min_gap_ms=_MIN_GAP_MS):
    """Convenience one-shot wrapper - compute_flux() + markers_from_flux()
    in a single call, for simple one-off callers/tests. A live-updating UI
    should call compute_flux() once per sample buffer and
    markers_from_flux() once per update instead (see both their own
    docstrings), rather than re-paying the O(n) analysis pass on every
    call.
    """
    flux, window_frames = compute_flux(samples, framerate)
    if flux is None:
        return []
    return markers_from_flux(
        flux, window_frames, sensitivity, len(samples), min_gap_ms
    )
