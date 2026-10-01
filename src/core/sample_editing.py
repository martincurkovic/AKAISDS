import math

# Pure, Qt/MIDI-independent sample-buffer transforms for the Samples tab's
# Trim/Reverse/Fade/Normalise/Filter actions (see program_editor_window.py's
# _confirm_trim_sample/_confirm_reverse_sample/_confirm_fade_sample/
# _confirm_normalize_sample/_confirm_filter_sample). No hardware or bridge
# code here at all - just the sample-list + marker-field math, kept
# separate so it's trivially unit-testable without a QApplication, a
# bridge, or real audio.
#
# All four functions take/return the same four markers WaveformView.markers()
# already uses (start, loop_start, loop_end, end) - the caller is
# responsible for actually sending the result to the hardware and updating
# the sample header fields (SSTART/SMPEND/LOOPAT1/LLNGTH1/SLNGTH) from
# them; LOOPAT1 is always loop_end and LLNGTH1 is always
# loop_end - loop_start, same convention documented in AGENTS.md and used
# throughout program_editor_bridge.py/program_editor_window.py.

# every sample value on this page is a plain signed 16-bit int (see
# sds_encoder.scale_sample_to_16bit/write_wav_file's own WAV round trip,
# and waveform_view.py's own /32768 scaling) - -32768..32767. 32767, not
# 32768, is normalize_samples' own ceiling in BOTH directions (even though
# -32768 is technically representable) - the conventional "safe" full-
# scale digital ceiling, so the loudest sample never lands on the one
# value with no positive counterpart.
_MAX_AMPLITUDE = 32767


def trim_samples(samples, start, loop_start, loop_end, end):
    """Keeps only samples[start:end+1] - the currently marked Start/End
    region - discarding everything outside it. Returns (new_samples,
    new_start, new_loop_start, new_loop_end, new_end).

    loop_start/loop_end are always within [start, end] on entry -
    WaveformView.clamp_marker enforces start <= loop_start <= loop_end <=
    end for every drag/typed edit - so the loop region itself is never
    clipped by a trim, only re-based to the new, shorter buffer's own
    origin. new_start is always 0 and new_end is always
    len(new_samples) - 1: trimming to the marked region collapses those
    two markers back to the buffer's own edges, same as a freshly
    received sample's markers would read.
    """
    new_samples = samples[start : end + 1]
    new_end = len(new_samples) - 1
    new_loop_start = loop_start - start
    new_loop_end = loop_end - start
    return new_samples, 0, new_loop_start, new_loop_end, new_end


def reverse_samples(samples, start, loop_start, loop_end, end):
    """Reverses the whole buffer so the sample plays backwards. Returns
    (new_samples, new_start, new_loop_start, new_loop_end, new_end).

    Reversing a frame_count-long buffer maps every frame index i to
    (frame_count - 1 - i), so every marker mirrors around that same axis -
    not just the loop points, Start/End too, since they're markers into
    the same buffer being reversed. The loop's own LENGTH
    (loop_end - loop_start) is invariant under this transform, only its
    position mirrors - a useful sanity check on this math: computing
    LLNGTH1 from the returned new_loop_start/new_loop_end should always
    reproduce the original LLNGTH1 unchanged.
    """
    frame_count = len(samples)
    new_samples = list(reversed(samples))
    new_start = frame_count - 1 - end
    new_end = frame_count - 1 - start
    new_loop_start = frame_count - 1 - loop_end
    new_loop_end = frame_count - 1 - loop_start
    return new_samples, new_start, new_loop_start, new_loop_end, new_end


def fade_in_out_samples(samples, start, loop_start, loop_end, end):
    """Linearly fades the LEAD-IN and LEAD-OUT around the marked [start,
    end] playback region, not the region itself: silence at frame 0
    ramping up to full volume exactly at the Start marker, and full
    volume at the End marker ramping down to silence at the buffer's own
    last frame. [start, end] itself is left untouched throughout - both
    ramps merely reach gain 1.0 at its own two edges, matching a normal
    "fade in, play, fade out" shape around whatever's actually marked to
    play. Returns (new_samples, start, loop_start, loop_end, end) - the
    same 5-tuple shape trim_samples/reverse_samples return, so
    program_editor_window.py's _perform_sample_edit can call any of the
    three identically, but unlike those two, every marker comes back
    UNCHANGED: fading never resizes or reorders the buffer, only scales
    some of its sample values.

    Either ramp is skipped outright when there's nothing to ramp across
    (start == 0: no lead-in; end == len(samples) - 1: no lead-out) rather
    than dividing by zero.
    """
    new_samples = list(samples)
    frame_count = len(samples)

    if start > 0:
        for i in range(start + 1):  # frame 0 (silence) .. start (full)
            gain = i / start
            new_samples[i] = int(round(new_samples[i] * gain))

    tail_length = frame_count - 1 - end
    if tail_length > 0:
        for i in range(end, frame_count):  # end (full) .. last frame (silence)
            gain = (frame_count - 1 - i) / tail_length
            new_samples[i] = int(round(new_samples[i] * gain))

    return new_samples, start, loop_start, loop_end, end


def normalize_samples(samples, start, loop_start, loop_end, end):
    """Applies a single uniform gain to the WHOLE buffer - not just
    [start, end] - so the loudest sample anywhere in it hits
    _MAX_AMPLITUDE exactly, every other sample scaled by that same
    factor. Unlike trim_samples/fade_in_out_samples, this isn't scoped to
    the marked playback region: normalizing is a global level change
    (matches reverse_samples' own "whole buffer" scope, not Trim/Fade's
    [start, end]-relative one). Returns (new_samples, start, loop_start,
    loop_end, end) - same 5-tuple shape as the other three; markers are
    always unchanged, only levels change.

    A silent buffer (every sample already 0) is returned unchanged rather
    than dividing by zero - there's no gain that makes silence louder.
    Clamped defensively to [-_MAX_AMPLITUDE - 1, _MAX_AMPLITUDE] (the
    full int16 range) even though the gain is derived FROM the buffer's
    own peak so no sample can mathematically exceed _MAX_AMPLITUDE after
    scaling - only float rounding at the very peak sample could ever push
    it out by one, and this costs nothing to guard against anyway.
    """
    peak = max((abs(v) for v in samples), default=0)
    if peak == 0:
        return list(samples), start, loop_start, loop_end, end
    gain = _MAX_AMPLITUDE / peak
    new_samples = [
        max(-_MAX_AMPLITUDE - 1, min(_MAX_AMPLITUDE, int(round(v * gain))))
        for v in samples
    ]
    return new_samples, start, loop_start, loop_end, end


# --- Filter (highpass/lowpass, adjustable slope) -----------------------------
# Hand-rolled rather than a scipy/numpy dependency - a standard 2nd-order
# Butterworth biquad (the "RBJ Audio EQ Cookbook" formulas, the same
# derivation almost every hand-written audio EQ traces back to) is
# well-understood, exactly-reproducible math and maybe 40 lines, matching
# this module's own established style (see trim/reverse/fade/normalize
# above) and sds_encoder.py's own precedent (_lowpass_filter, used there
# for anti-aliasing before downsampling) - not worth a heavy, Nuitka-
# unfriendly dependency for something this contained.
#
# "Slope" (steepness) is not a separate algorithm - a single biquad stage
# is always a fixed 12dB/octave rolloff; cascading N identical stages in
# series (each filtering the PREVIOUS stage's own output) gives 12*N
# dB/octave, the standard way any analog or digital filter gets a steeper
# slope. _SLOPE_DB_PER_OCTAVE_OPTIONS/_stages_for_slope just turn a
# dB/octave choice into "how many times to run the same filter."

FILTER_TYPES = ("lowpass", "highpass")
SLOPE_DB_PER_OCTAVE_OPTIONS = (12, 24, 36, 48)


def _stages_for_slope(slope_db_per_octave):
    stages, remainder = divmod(slope_db_per_octave, 12)
    if remainder != 0 or stages < 1:
        raise ValueError(
            "slope_db_per_octave must be a positive multiple of 12 "
            f"(got {slope_db_per_octave!r})"
        )
    return stages


def _biquad_coefficients(filter_type, cutoff_hz, framerate):
    # Q = 1/sqrt(2) (~0.7071) - the maximally-flat Butterworth response
    # (no resonant peak at the cutoff), not exposed as its own control:
    # this is meant as a practical "tame the highs/lows" tool, not a
    # resonant-filter sound-design one - if that's ever wanted, it's a
    # one-line change to accept Q as a parameter instead of a fixed
    # constant, not a rewrite of the math itself.
    q = 1 / math.sqrt(2)
    nyquist = framerate / 2
    # clamped strictly inside (0, nyquist) - w0 must stay inside (0, pi)
    # for sin/cos below to produce a stable, meaningful filter; the
    # dialog's own spinbox range should already guarantee this, but a
    # cutoff placed exactly ON or past Nyquist has no valid biquad
    # response at all, so this guards it defensively rather than trust
    # every caller
    cutoff_hz = max(1.0, min(nyquist - 1.0, cutoff_hz))
    w0 = 2 * math.pi * cutoff_hz / framerate
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2 * q)

    if filter_type == "lowpass":
        b0 = (1 - cos_w0) / 2
        b1 = 1 - cos_w0
        b2 = (1 - cos_w0) / 2
    elif filter_type == "highpass":
        b0 = (1 + cos_w0) / 2
        b1 = -(1 + cos_w0)
        b2 = (1 + cos_w0) / 2
    else:
        raise ValueError(f"Unknown filter_type {filter_type!r} - expected one of {FILTER_TYPES}")

    a0 = 1 + alpha
    a1 = -2 * cos_w0
    a2 = 1 - alpha

    # normalized so a0 == 1 - the difference equation below assumes this
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


def _apply_biquad_float(samples, coefficients):
    # keeps output as plain floats, no rounding/clipping - used internally
    # so a multi-stage/dual-filter cascade (see filter_samples) doesn't
    # accumulate quantization noise at every intermediate stage, only
    # once, at the very end of the whole chain (_quantize_to_int16)
    b0, b1, b2, a1, a2 = coefficients
    x1 = x2 = 0.0  # previous two INPUT samples
    y1 = y2 = 0.0  # previous two OUTPUT samples
    new_samples = [0.0] * len(samples)
    for i, x0 in enumerate(samples):
        y0 = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        new_samples[i] = y0
        x2, x1 = x1, x0
        y2, y1 = y1, y0
    return new_samples


def _quantize_to_int16(samples):
    return [max(-_MAX_AMPLITUDE - 1, min(_MAX_AMPLITUDE, int(round(v)))) for v in samples]


def filter_samples(
    samples,
    start,
    loop_start,
    loop_end,
    end,
    framerate,
    highpass_enabled=False,
    highpass_cutoff_hz=None,
    highpass_slope_db_per_octave=12,
    lowpass_enabled=False,
    lowpass_cutoff_hz=None,
    lowpass_slope_db_per_octave=12,
):
    """Runs the WHOLE buffer through an optional highpass and/or an
    optional lowpass Butterworth filter, each independently enabled/
    bypassed with its own cutoff and slope - same whole-buffer scope as
    reverse_samples/normalize_samples, not [start, end]-relative like
    trim/fade: frequency content isn't a concept scoped to the marked
    playback region the way trim/fade's timing-based edits are, and
    filtering only part of a sample while leaving the rest untouched would
    introduce an audible discontinuity right at the [start, end] boundary.
    Markers are always returned unchanged - filtering only changes sample
    VALUES, never timing, same as fade_in_out_samples/normalize_samples.

    When both are enabled, highpass is applied FIRST, then lowpass -
    cascaded IIR stages interact nonlinearly with each other regardless of
    order, so there's no "correct" order in an absolute sense, just this
    one, picked for being the conventional "clean up the bottom, then the
    top" signal chain. Quantization to int16 happens exactly ONCE, after
    every stage of both filters has run (see _apply_biquad_float/
    _quantize_to_int16) - not after each individual stage - so a steep
    (multi-stage) slope doesn't accumulate extra rounding noise beyond
    what the filter math itself already introduces.

    Unlike the other three transforms, this needs parameters beyond the
    standard 5 (samples, start, loop_start, loop_end, end) -
    program_editor_window.py's _confirm_filter_sample binds the rest via
    functools.partial before handing this to _perform_sample_edit, which
    only ever calls transform(samples, start, loop_start, loop_end, end) -
    so this function's own extra parameters are never visible to that
    dispatcher.
    """
    result = [float(v) for v in samples]
    if highpass_enabled:
        if highpass_cutoff_hz is None:
            raise ValueError("highpass_cutoff_hz is required when highpass_enabled is True")
        coefficients = _biquad_coefficients("highpass", highpass_cutoff_hz, framerate)
        for _ in range(_stages_for_slope(highpass_slope_db_per_octave)):
            result = _apply_biquad_float(result, coefficients)
    if lowpass_enabled:
        if lowpass_cutoff_hz is None:
            raise ValueError("lowpass_cutoff_hz is required when lowpass_enabled is True")
        coefficients = _biquad_coefficients("lowpass", lowpass_cutoff_hz, framerate)
        for _ in range(_stages_for_slope(lowpass_slope_db_per_octave)):
            result = _apply_biquad_float(result, coefficients)
    new_samples = _quantize_to_int16(result)
    return new_samples, start, loop_start, loop_end, end
