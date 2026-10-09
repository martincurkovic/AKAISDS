"""
Standalone dev tool - NOT part of the app. SAFE: the ONLY thing it ever sends is an RSTAT (a read-only status request - the same one the editor uses as its liveness check).
It finds out whether anything on this computer is sending the sampler's replies back to it ("echo"), and what else is on the MIDI input - so it can be run WITH the DAW
open, one Ableton setting at a time, to find the setting that causes it.

    uv run python tools/midi_echo_check.py                 # 5 status requests, then a verdict

Why this exists (2026-10-10, the user's S2000): with Ableton open on the same interface the sampler sent a stray `REPLY OK` next to nearly every reply, which is what it does
when it is HANDED ITS OWN REPLY FRAMES BACK (it executes a returned data frame as a write and acknowledges it). Closing Ableton made it stop. The editor now tolerates the strays
and warns, but a returned frame is still executed by the sampler, so the real fix is to stop the echo. Nobody has seen WHICH Ableton setting does it.

How to use it (the AKAISDS app must be CLOSED - it owns the ports; the sampler on, MIDI connected as usual):
  1. Ableton CLOSED:  run it. Expect: every request answered by exactly ONE frame (the STAT), nothing else on the input -> verdict "clean". That is the baseline.
  2. Open Ableton with its MIDI preferences (Preferences > Link, Tempo & MIDI) set for the interface's ports as you intend to use them, with NO MIDI track routing the sampler's
     input back to its output. Run it again. If the verdict is "clean", the two can share the interface as set up.
  3. If it says "ECHO": change ONE setting, run again, repeat. Candidates, in order: a MIDI track whose input is the sampler's port (or "All Ins") and whose output is the same
     interface, with Monitor = In/Auto; the port's INPUT "Track"/"Sync"/"Remote" switches; the port's OUTPUT "Sync" (MIDI clock) and "Remote". Keep what you change in the log.
  4. Whatever is left on in the clean configuration is the setup AKAISDS can run beside.
It also reports MIDI clock (0xF8), active sensing (0xFE) and any non-SysEx traffic arriving on the input while idle: a sampler sitting there with a DAW sending it clock may be slow to
answer, even without echo.

What it reports per request: how many SysEx frames came back, and of what kind (STAT = the answer; REPLY OK / REPLY ERROR / anything else = stray). A REPLY after the STAT is the
echo signature: a returned STAT is not a valid command, so the sampler answers it with an error REPLY; returned data frames would be acknowledged with OK.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

import s2000_misc_probe as probe  # noqa: E402  (open_bridge)
from s3k import messages as m  # noqa: E402

REQUESTS = 5
IDLE_LISTEN_S = 3.0
SETTLE_S = 1.5  # how long to keep listening after each request for stray frames


def classify_raw(message):
    """A short label for one raw MIDI message (a list of bytes)."""
    if not message:
        return "empty"
    status = message[0]
    if status == 0xF8:
        return "MIDI clock (0xF8)"
    if status == 0xFE:
        return "active sensing (0xFE)"
    if status == 0xFA or status == 0xFB or status == 0xFC:
        return {0xFA: "MIDI start", 0xFB: "MIDI continue", 0xFC: "MIDI stop"}[status]
    if status == 0xF0:
        try:
            _channel, command, payload = m.parse_frame(bytes(message))
        except ValueError:
            return "SysEx (not an Akai frame)"
        if command == m.Command.STAT:
            return "STAT (the answer)"
        if command == m.Command.REPLY:
            try:
                ok = m.Reply.decode(bytes(message)).ok
            except ValueError:
                return "REPLY (malformed)"
            return "REPLY OK (stray)" if ok else "REPLY ERROR (stray)"
        return f"Akai frame {int(command):#04x} (stray)"
    if status & 0xF0 in (0x80, 0x90, 0xA0, 0xB0, 0xC0, 0xD0, 0xE0):
        return "channel message (note/CC/program/...)"
    return f"other ({status:#04x})"


def drain(bridge, seconds):
    """Everything arriving on the input for `seconds`, as (label, bytes) in order."""
    seen = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        message = bridge.inp.get_message()
        if message is None:
            time.sleep(0.002)
            continue
        raw = list(message[0])
        seen.append((classify_raw(raw), raw))
    return seen


def verdict(idle, per_request):
    """(text, clean) from what was heard while idle and after each request."""
    stray = [label for frames in per_request for label, _ in frames if label.endswith("(stray)")]
    unanswered = sum(1 for frames in per_request if not any(label == "STAT (the answer)" for label, _ in frames))
    notes = []
    if stray:
        notes.append(f"ECHO / STRAY FRAMES: {len(stray)} stray frame(s) after {REQUESTS} requests ({', '.join(sorted(set(stray)))}). "
                     "Something is probably sending the sampler's replies back to it - the sampler executes a returned data frame as a WRITE.")
    if unanswered:
        notes.append(f"{unanswered} of {REQUESTS} requests got no STAT answer (the sampler is slow, busy, or off).")
    realtime = sorted({label for label, _ in idle if "clock" in label or "sensing" in label or "MIDI start" in label or "MIDI stop" in label or "continue" in label})
    if realtime:
        notes.append("Idle input traffic: " + ", ".join(realtime) + " (a DAW sending sync/clock; harmless to AKAISDS but it can slow a sampler's SysEx handling).")
    other = sorted({label for label, _ in idle if label not in realtime})
    if other:
        notes.append("Other idle input traffic: " + ", ".join(other) + ".")
    if not stray and not unanswered:
        return "clean: one STAT answer per request, nothing stray." + ((" " + " ".join(notes)) if notes else ""), True
    return " ".join(notes), False


def main():
    bridge = probe.open_bridge()[1]
    channel = getattr(bridge, "exclusive_channel", m.DEFAULT_EXCLUSIVE_CHANNEL)
    frame = m.RequestStatus(exclusive_channel=channel).encode()
    print(f"listening {IDLE_LISTEN_S:.0f}s with nothing sent...", flush=True)
    idle = drain(bridge, IDLE_LISTEN_S)
    print(f"  idle input: {len(idle)} message(s)" + (f" - {', '.join(sorted({l for l, _ in idle}))}" if idle else ""), flush=True)
    per_request = []
    for n in range(1, REQUESTS + 1):
        bridge._drain()
        bridge._send(frame)  # an RSTAT: read-only
        frames = drain(bridge, SETTLE_S)
        per_request.append(frames)
        print(f"  request {n}: " + (", ".join(label for label, _ in frames) or "no reply"), flush=True)
    text, clean = verdict(idle, per_request)
    print(f"\nverdict: {text}", flush=True)
    os._exit(0 if clean else 1)


if __name__ == "__main__":
    main()
