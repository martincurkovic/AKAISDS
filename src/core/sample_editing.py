# Pure, Qt/MIDI-independent sample-buffer transforms for the Samples tab's
# Trim/Reverse/Fade/Normalise actions (see program_editor_window.py's
# _confirm_trim_sample/_confirm_reverse_sample/_confirm_fade_sample/
# _confirm_normalize_sample). No hardware or bridge code here at all - just
# the sample-list + marker-field math, kept separate so it's trivially
# unit-testable without a QApplication, a bridge, or real audio.
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
