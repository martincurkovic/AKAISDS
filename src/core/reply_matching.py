"""
Reply matching for `s3k.bridge.S3kBridge` on a REAL sampler (no Qt, no MIDI of its own).

`S3kBridge.send_and_receive` accepts the first frame whose opcode could answer the request - and a REPLY (0x16) is "always allowed, since any operation can
answer with an error instead". That breaks on a sampler that sends extra frames: on 2026-10-10 (the user's S2000, booted from its floppy) nearly every request
was accompanied by a stray `REPLY OK` (and, in dumps, late data frames for earlier requests). The editor took a stray OK as the answer to its NEXT request, so
`sample_list()` failed with "expected command 0x05, got 0x16" in 2.7 ms (too fast to be a real answer) and the program/multi reads failed the same way.
The cause of the strays is unknown (the same pattern began the previous evening, before the reboot); see AGENTS.md's "Global tab" incident notes.

`make_reply_tolerant(bridge)` replaces that ONE instance's `send_and_receive` so that, for a READ request (one answered by a data frame, request opcode + 1):
  - a stray `REPLY OK` is skipped (an OK is never the answer to a read) - a REPLY ERROR is still returned, because a genuine error answer looks exactly like that;
  - for the extended-header reads whose reply is verified to echo the item (program/keygroup/sample/misc/multi), a data frame whose item index / selector / offset differ from the request
    is a late answer to an earlier request and is skipped;
  - the wait still ends at the request's own timeout (a sampler that only sends strays times out as before).
Everything else (deletes, SETEX, whole-block writes, anything whose legitimate answer IS a REPLY OK) goes to the original method untouched. The S3kBridge write
paths that call `_receive(accept=REPLY only)` are not affected either. Skips are counted in `bridge.skipped_replies` and logged (WARNING the first time).
"""

import time

from s3k import messages as m

# requests answered by a DATA frame (request opcode + 1) - for these an OK REPLY is never the answer
_READ_REQUESTS = frozenset(
    int(c)
    for c in (
        m.Command.RSTAT,
        m.Command.RPLIST,
        m.Command.RSLIST,
        m.Command.RPDATA,
        m.Command.RKDATA,
        m.Command.RSDATA,
        m.Command.RDDATA,
        m.Command.RMDATA,
        m.Command.RPHEADER,
        m.Command.RKHEADER,
        m.Command.RSHEADER,
        m.Command.RFXDATA,
        m.Command.RCUEDATA,
        m.Command.RTAKEDATA,
        m.Command.RMISCDATA,
        m.Command.RVOLLIST,
        m.Command.RHDDIR,
        m.Command.RMULTIDATA,
    )
)
# The extended-header reads whose reply is VERIFIED (against real traffic in ~/.akaisds/akaisds.log) to echo the request's item index, selector and offset:
# program/keygroup/sample headers, misc bytes and the multi. The others (volume list, harddisk directory, FX, cue/take lists) are NOT matched on purpose: their
# replies may legitimately carry a different index/offset (s3k notes FX ignores the index, the lists walk past their end), and a wrong skip would turn a good
# answer into a timeout. They still get the stray-REPLY-OK skip.
_HEADER_READS = frozenset(
    int(c)
    for c in (
        m.Command.RPHEADER,
        m.Command.RKHEADER,
        m.Command.RSHEADER,
        m.Command.RMISCDATA,
        m.Command.RMULTIDATA,
    )
)

_ITEM_INDEX_MASK = m.ITEM_INDEX_MASK


def _item_key(payload):
    """(item index, selector, offset) from the first 7 payload bytes of an extended-header frame, or None if it is too short."""
    if len(payload) < 7:
        return None
    index = m.decode_u14(payload[0], payload[1]) & _ITEM_INDEX_MASK
    return index, payload[2], m.decode_u14(payload[3], payload[4])


def make_reply_tolerant(bridge, logger=None):
    """Replace `bridge.send_and_receive` (this instance only) with the matching version above. Idempotent."""
    if getattr(bridge, "reply_tolerant", False) or not hasattr(bridge, "send_and_receive"):
        return bridge  # already done, or not a real S3kBridge (a test double)
    original = bridge.send_and_receive
    bridge.skipped_replies = 0
    bridge.reply_tolerant = True

    def note(reason):
        bridge.skipped_replies += 1
        if logger is not None:
            level = "warning" if bridge.skipped_replies == 1 else "debug"
            getattr(logger, level)(f"reply matching: skipped {reason} (total {bridge.skipped_replies}) - the sampler is sending stray replies")

    def send_and_receive(frame, *, timeout=None):
        try:
            _channel, command, payload = m.parse_frame(frame)
        except ValueError:
            return original(frame, timeout=timeout)
        if int(command) not in _READ_REQUESTS:
            return original(frame, timeout=timeout)
        wanted = _item_key(payload) if int(command) in _HEADER_READS else None
        accept = bridge._answers_to(frame)
        deadline = time.monotonic() + (bridge.timeout if timeout is None else timeout)
        bridge._drain()
        bridge._send(frame)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no reply within {timeout or bridge.timeout}s")
            reply = bridge._receive(remaining, accept=accept)
            _c, reply_command, reply_payload = m.parse_frame(reply)
            if reply_command == m.Command.REPLY:
                try:
                    is_ok = m.Reply.decode(reply).ok
                except ValueError:
                    is_ok = False
                if is_ok:
                    note("a stray REPLY OK")
                    continue
                return reply  # a REPLY ERROR is a genuine answer to a read
            if wanted is not None:
                got = _item_key(reply_payload)
                if got is not None and got != wanted:
                    note(f"a late data frame for item {got} (asked for {wanted})")
                    continue
            return reply

    bridge.send_and_receive = send_and_receive
    return bridge
