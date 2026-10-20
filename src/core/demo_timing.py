"""Turning off the MIDI-speed emulation of the demo samplers (a developer convenience, never used against real hardware).

The fakes (core/demo_a4000.py, demo_s950.py, the Program Editor's DemoBridge) answer at once, but the app's own transfer engines pace
themselves like the real wire: the Yamaha session waits out each bulk message's wire time at 31250 baud plus a 400 ms gap, a link waits 4 s
for a unit that once went silent, a verification read waits 2.5 s; the S950 engine waits out wire time and a NAK window and streams ACKs
every 50 ms; the Program Editor's demo paces a sample load/replace at ~1 ms per word. That is what makes a demo feel real - and slow.

`--instant` on the demo tools (tools/a4000_demo.py, tools/s950_demo.py, the Program Editor's standalone launcher) or the environment
variable AKAISDS_DEMO_INSTANT=1 removes it. Without either, nothing changes.
"""

import os

ENV = "AKAISDS_DEMO_INSTANT"


def is_instant():
    """True when AKAISDS_DEMO_INSTANT is set to anything but "", 0, false, no, off (read live, like AKAISDS_DEMO_SAMPLER)."""
    value = os.environ.get(ENV)
    return value is not None and value.strip().lower() not in ("", "0", "false", "no", "off")


def requested(argv):
    """The demo tools' check: the --instant flag, or the environment variable."""
    return "--instant" in argv or is_instant()


def apply_to_yamaha(controller):
    """Make a Yamaha demo (tools/a4000_demo.py) run at the speed of the fake: no wire time, gaps or settle waits."""
    session = controller.yamaha_session()
    session.send_baud = 10**9
    session.send_gap_ms = 0
    session.send_tick_ms = 5
    session.edit_settle_ms = 10
    transfers = controller._yamaha_transfers
    transfers.verify_delay_ms = 0
    transfers.verify_retry_ms = 50
    from ui import yamaha_sample_edit

    yamaha_sample_edit.LINK_SETTLE_MS = 0


def apply_to_s950(controller):
    """Make the S900/S950 demo (tools/s950_demo.py) run at the speed of the fake."""
    engine = controller._s950
    engine.ack_interval_ms = 1
    engine.nak_window_ms = 20
    engine.sprm_settle_ms = 5
    engine.drain_floor_ms = 5
    engine.drain_margin = 0.0
    engine.progress_tick_ms = 50
