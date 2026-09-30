# Pure, Qt/MIDI-independent root-note (fundamental pitch) detection for
# the Samples tab's "Detect Root Note" button (see program_editor_window.py)
# - same reasoning as core/transient_detection.py/core/sample_slicing.py:
# kept separate so it's trivially unit-testable without a QApplication, a
# bridge, or real audio.
#
# Hand-rolled, time-domain autocorrelation pitch detection - deliberately
# simple, same "no new dependency" reasoning as transient_detection.py
# (numpy/scipy/aubio/librosa were all considered and rejected - see the
# conversation this was built from). Monophonic only (a single melodic
# note, not a chord or a mix) - the intended use case (a sampler's own
# root note) never needs more than that. Good enough for a plucked/held
# instrument note, not a research-grade pitch tracker.
#
# Octave-error mitigation: autocorrelation of a genuinely periodic signal
# is ALSO strongly correlated at every integer multiple of the true
# period (a signal periodic with period T is trivially also periodic with
# 2T, 3T, ...), so the raw autocorrelation function typically shows
# several real, valid peaks, not just one at the true fundamental. Rather
# than inventing a heuristic to guess which peak is "the real"
# fundamental, this collects every strong peak as a genuine candidate and
# lets the CALLER'S already-set root note (an anchor - see
# detect_root_note's own docstring) pick whichever is closest, in
# semitones - using whatever rough register the user already has in mind
# (or a sample library's own prior root note metadata) as the tie-breaker,
# rather than a blind absolute pitch guess.

import math

# ~11kHz-ish target analysis rate - naive decimation (plain skip-sampling,
# no anti-alias lowpass first): this only needs to preserve enough of the
# fundamental + a few early harmonics to find A periodicity, not clean
# audio (core/audio_preview.py's own SlicePreviewPlayer is the actual
# audio-quality path). Keeping both the sample count AND the lag search
# range roughly a quarter of the original is what keeps the
# O(n * lag_range) autocorrelation pass fast enough to feel closer to
# instant for a one-click button - this is a discrete action, not a live,
# continuously-dragged control the way transient_detection.py's own flux
# precompute has to be.
_TARGET_ANALYSIS_RATE = 11025

# matches sample_root_note_spinbox's own declared range (s3k.params:
# "21 to 127 represents A1 to G8")
_MIN_MIDI_NOTE = 21
_MAX_MIDI_NOTE = 127

# a peak below this normalized-correlation floor isn't even considered a
# real candidate periodicity - separate from (and lower than)
# CONFIDENCE_THRESHOLD below, which is what actually decides "reliable
# enough to act on" for the caller
_PEAK_FLOOR = 0.3

# confidence below this reads as "couldn't reliably detect a pitch" - not
# applied inside this module (see detect_root_note's own docstring on
# why) - needs real material to validate/adjust further, same "art not
# science" reasoning as transient_detection.py's own sensitivity default
CONFIDENCE_THRESHOLD = 0.5

# how close (as a fraction of the strongest peak found) another peak must
# be to also count as a CREDIBLE candidate for the anchor to choose among
# - see detect_root_note's own comment on the real bug this fixes (the
# anchor reaching past a strong peak to grab a much weaker, spurious one
# just because it was numerically closer, making reported confidence swing
# wildly based on the anchor alone rather than the audio)
_CANDIDATE_CONFIDENCE_RATIO = 0.8


def _decimate(samples, factor):
    if factor <= 1:
        return samples
    return samples[::factor]


def _midi_note_to_frequency(midi_note):
    return 440.0 * (2.0 ** ((midi_note - 69) / 12.0))


def _frequency_to_midi_note(frequency):
    return 69.0 + 12.0 * math.log2(frequency / 440.0)


def _autocorrelation(samples, lag):
    """Normalized cross-correlation between samples[:-lag] and
    samples[lag:] - the standard "autocorrelation coefficient" definition
    (each half normalized by its OWN energy, not the whole buffer's) so a
    decaying envelope across the analysis window (e.g. a plucked string's
    natural decay) doesn't bias the result the way dividing by a single
    fixed zero-lag energy would.
    """
    n = len(samples)
    count = n - lag
    if count <= 1:
        return 0.0
    a = samples[:count]
    b = samples[lag : lag + count]
    numerator = sum(x * y for x, y in zip(a, b))
    energy_a = sum(x * x for x in a)
    energy_b = sum(y * y for y in b)
    denom = math.sqrt(energy_a * energy_b)
    if denom <= 0:
        return 0.0
    return numerator / denom


def _refine_peak(correlations, index):
    """Sub-lag (fractional) refinement of a local peak via parabolic
    interpolation through it and its two immediate neighbours - a
    standard, cheap technique for getting cents-level pitch accuracy out
    of an integer-lag autocorrelation without needing a much finer (and
    much slower) lag step. Falls back to the un-refined integer lag at
    either end of *correlations*, where there's no neighbour on one side
    to fit a parabola through.
    """
    if index <= 0 or index >= len(correlations) - 1:
        lag, value = correlations[index]
        return float(lag), value
    (_, c_prev), (lag, c), (_, c_next) = (
        correlations[index - 1],
        correlations[index],
        correlations[index + 1],
    )
    denom = c_prev - 2 * c + c_next
    if denom == 0:
        return float(lag), c
    offset = 0.5 * (c_prev - c_next) / denom
    offset = max(-1.0, min(1.0, offset))
    return lag + offset, c


def _find_peaks(correlations):
    """Every local maximum in *correlations* ((lag, value) pairs, in
    ascending lag order) at or above _PEAK_FLOOR, refined via
    _refine_peak - a real periodic signal's own autocorrelation typically
    shows several of these (see this module's own top comment on why),
    not just one.
    """
    peaks = []
    for i in range(1, len(correlations) - 1):
        _, value = correlations[i]
        if value < _PEAK_FLOOR:
            continue
        if value < correlations[i - 1][1] or value < correlations[i + 1][1]:
            continue
        peaks.append(_refine_peak(correlations, i))
    return peaks


def detect_root_note(samples, framerate, anchor_midi_note):
    """Best-guess (midi_note, cents, confidence) for a monophonic melodic
    sample's fundamental pitch, or None if nothing resembling a pitch was
    found at all (silence, noise, or too short a buffer to even try).

    *samples* should already be whatever stable, representative window the
    caller chose (e.g. a loop region, or an attack-skipped chunk of the
    full sample) - this function has no concept of start/end/loop markers
    itself, same layering as core/sample_editing.py's transforms.

    *anchor_midi_note* is the sample's CURRENT root note setting (e.g.
    sample_root_note_spinbox's own value) - used ONLY to break ties among
    CREDIBLE candidate periodicities (peaks within
    _CANDIDATE_CONFIDENCE_RATIO of the strongest one found - see this
    module's own top comment), never to reach past them at a much weaker,
    spurious peak just because it happens to be numerically closer, and
    never to bias the pitch measurement itself. A genuinely wrong anchor
    can still steer this toward the wrong octave AMONG credible
    candidates, same as it would for a person guessing - but it can no
    longer manufacture false confidence in a peak that was barely there to
    begin with.

    midi_note is an int (nearest semitone); cents is the leftover
    fractional pitch in [-50, 50), positive meaning sharp. confidence is
    the winning candidate's own normalized autocorrelation peak height, in
    [0, 1] - deliberately close to anchor-INDEPENDENT (see above): for the
    same audio, it can only ever land on one of the CREDIBLE peaks' own
    heights, all of which are already close to the strongest one found, so
    it stays a genuine property of the signal rather than swinging
    depending on whatever the anchor happens to be. This function makes no
    "reliable enough" judgment call itself (compare against
    CONFIDENCE_THRESHOLD, or roll your own); that decision belongs to the
    caller (see program_editor_window.py's own _confirm_detect_root_note).
    """
    if framerate <= 0 or len(samples) < 2:
        return None

    decimation = max(1, framerate // _TARGET_ANALYSIS_RATE)
    decimated = _decimate(samples, decimation)
    analysis_rate = framerate / decimation

    min_lag = max(1, round(analysis_rate / _midi_note_to_frequency(_MAX_MIDI_NOTE)))
    max_lag = round(analysis_rate / _midi_note_to_frequency(_MIN_MIDI_NOTE))
    # require at least 2 full periods of overlap for a lag to be trusted -
    # correlating over less than one period isn't a meaningful measurement
    # at all, and a decaying/short window otherwise silently favours
    # whichever long lags happen to have the least (least reliable) data
    max_lag = min(max_lag, len(decimated) // 2 - 1)
    if max_lag <= min_lag:
        return None

    correlations = [
        (lag, _autocorrelation(decimated, lag)) for lag in range(min_lag, max_lag + 1)
    ]
    peaks = _find_peaks(correlations)
    if not peaks:
        return None

    def _note_for(lag):
        return _frequency_to_midi_note(analysis_rate / lag)

    # a real, reported bug in an earlier version: picking whichever peak
    # was NEAREST the anchor, full stop, meant a badly-set (or default)
    # anchor could pick out a weak, spurious peak over a much stronger,
    # more genuine one - and since the reported confidence was THAT
    # peak's own height, the same audio could swing from ~35% to ~80%
    # "confidence" depending purely on the anchor, with no change to the
    # audio at all. Confidence is supposed to answer "how sure are we
    # this is a clear, single pitch" - a property of the SIGNAL, not of
    # whatever the anchor happens to be. Restricting the anchor's own
    # say-so to CREDIBLE peaks only (within _CANDIDATE_CONFIDENCE_RATIO
    # of the strongest one found) fixes this: the anchor still resolves
    # genuine octave ambiguity among comparably-strong candidates (a
    # clean tone's own T/2T/3T family are all close in height - see this
    # module's own top comment), but can no longer reach past them to
    # grab something far weaker just because it's numerically closer.
    strongest_confidence = max(value for _, value in peaks)
    credible_peaks = [
        peak
        for peak in peaks
        if peak[1] >= strongest_confidence * _CANDIDATE_CONFIDENCE_RATIO
    ]
    best_lag, best_confidence = min(
        credible_peaks, key=lambda peak: abs(_note_for(peak[0]) - anchor_midi_note)
    )
    note_float = _note_for(best_lag)
    midi_note = int(round(note_float))
    cents = (note_float - midi_note) * 100.0
    return midi_note, cents, best_confidence
