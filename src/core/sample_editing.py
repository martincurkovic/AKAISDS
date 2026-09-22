# Pure, Qt/MIDI-independent sample-buffer transforms for the Samples tab's
# Trim/Reverse actions (see program_editor_window.py's _confirm_trim_sample/
# _confirm_reverse_sample). No hardware or bridge code here at all - just
# the sample-list + marker-field math, kept separate so it's trivially
# unit-testable without a QApplication, a bridge, or real audio.
#
# Both functions take/return the same four markers WaveformView.markers()
# already uses (start, loop_start, loop_end, end) - the caller is
# responsible for actually sending the result to the hardware and updating
# the sample header fields (SSTART/SMPEND/LOOPAT1/LLNGTH1/SLNGTH) from
# them; LOOPAT1 is always loop_end and LLNGTH1 is always
# loop_end - loop_start, same convention documented in AGENTS.md and used
# throughout program_editor_bridge.py/program_editor_window.py.


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
