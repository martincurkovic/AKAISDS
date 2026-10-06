"""The Yamaha sample's WAVE and LOOP markers as four addresses, and the ORDER in which they must be written.

A sample has a wave region [start, end) and a loop region [loop_start, loop_end) inside it. In the parameter table they are six rows
(`wave_start_address`, `wave_length`, `wave_end_address`, `loop_start_address`, `loop_length`, `loop_end_address`) that the unit keeps
COUPLED. MEASURED on a real A4000 (2026-10-06, `tools/a4000_marker_probe.py`, a user sample sent over SDS):

 - wave start write: the END stays, the length is derived (end - start). wave END write (or wave_length write): the start stays.
   (On a BUILT-IN waveform the end/length writes are accepted and ignored - see `yamaha_params`.)
 - loop start write: the loop END stays, the length is derived. loop_end write: the start stays, the length is derived. loop_length
   write: the start stays and the END moves. So the four ADDRESSES (start, end, loop_start, loop_end) are the independent values and the
   two lengths always follow them.
 - the unit keeps start <= loop_start <= loop_end <= end and SILENTLY IGNORES (changes nothing) a write that would break it: a wave
   start above the loop start, a loop start above the loop end, a loop end below the loop start or beyond the wave end.
 - the right channel's twin address bytes (no P-numbers) move with every write; for a stereo sample the unit therefore keeps both
   channels' markers linked itself (to be confirmed on a real stereo sample).

So a marker editor can't write the four values in any order. `plan_marker_writes` orders them so no step is ever ignored: widen the outer
region, widen the loop, narrow the loop, narrow the outer region. Addresses are the unit's own numbers; the waveform view's inclusive
frame markers convert with `address_from_frame`/`frame_from_address` (an END address is one past the last frame).
"""

import dataclasses

from core import yamaha_params as yp

#: marker name -> the parameter row that writes it
ROW_FOR = {
    "start": "wave_start_address",
    "end": "wave_end_address",
    "loop_start": "loop_start_address",
    "loop_end": "loop_end_address",
}


@dataclasses.dataclass(frozen=True)
class Markers:
    start: int
    loop_start: int
    loop_end: int
    end: int

    @property
    def valid(self):
        return 0 <= self.start <= self.loop_start <= self.loop_end <= self.end


def read_markers(data):
    """The four addresses out of a sample's bulk payload."""
    g = lambda key: yp.extract(yp.get("sample", key), data)  # noqa: E731
    return Markers(g("wave_start_address"), g("loop_start_address"), g("loop_end_address"), g("wave_end_address"))


def plan_marker_writes(current, target):
    """[(marker name, new address)] in the order they must be written to go from `current` to `target` without the unit ignoring a
    step. Only values that change are listed. Raises ValueError for a target that breaks start <= loop_start <= loop_end <= end."""
    if not target.valid:
        raise ValueError(f"markers out of order: {target}")
    steps, now = [], dataclasses.asdict(current)

    def move(name, value):
        if now[name] != value:
            steps.append((name, value))
            now[name] = value

    t = dataclasses.asdict(target)
    # 1. widen the outer region (a wider region can never contradict the loop inside it)
    move("start", min(now["start"], t["start"]))
    move("end", max(now["end"], t["end"]))
    # 2. widen the loop within it
    move("loop_start", min(now["loop_start"], t["loop_start"]))
    move("loop_end", max(now["loop_end"], t["loop_end"]))
    # 3. narrow the loop to its target
    move("loop_start", t["loop_start"])
    move("loop_end", t["loop_end"])
    # 4. narrow the outer region to its target (the loop is already inside it)
    move("start", t["start"])
    move("end", t["end"])
    return steps


def address_from_frame(marker, frame):
    """The unit's address for a waveform-view marker (an inclusive frame): the two END markers are one past the last frame."""
    return frame + 1 if marker in ("end", "loop_end") else frame


def frame_from_address(marker, address):
    return address - 1 if marker in ("end", "loop_end") else address


def view_markers(data):
    """(start, loop_start, loop_end, end) as the waveform view wants them (inclusive frames) from a sample payload."""
    m = read_markers(data)
    return (
        frame_from_address("start", m.start),
        frame_from_address("loop_start", m.loop_start),
        frame_from_address("loop_end", m.loop_end),
        frame_from_address("end", m.end),
    )


NAMES = ("start", "loop_start", "loop_end", "end")


def target_for_edit(current, before, after, loops):
    """The `Markers` a drag asks for. `current`: what the unit holds (`Markers`); `before` / `after`: the waveform view's four
    markers (start, loop_start, loop_end, end - inclusive frames) before and after the drag; `loops`: whether the sample's loop
    mode loops. Only the markers that MOVED are taken from the view - the rest keep what the unit holds (the view draws a
    non-looping sample's loop markers at the wave end, which is not what the unit stores) - and a loop that no longer fits
    inside the wave is pulled back into it (a non-looping sample's loop isn't being edited, just kept valid)."""
    t = dataclasses.asdict(current)
    for name, was, now in zip(NAMES, before, after):
        if was != now:
            t[name] = address_from_frame(name, now)
    if not loops:
        t["loop_start"] = min(max(t["loop_start"], t["start"]), t["end"])
        t["loop_end"] = min(max(t["loop_end"], t["loop_start"]), t["end"])
    return Markers(**t)
