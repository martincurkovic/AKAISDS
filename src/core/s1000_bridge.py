"""Program Editor bridge adapter for the Akai S1000/S1100.

WHY THIS EXISTS. `s3k.bridge.S3kBridge.get_parameter`/`set_parameter` read
and write ONE FIELD at a time with the S3000-only "header bytes" SysEx
extension (function codes 0x27-0x38: item index, selector, byte offset, byte
count). The S1000 doesn't implement any of them - it silently ignores the
request, so every read times out (confirmed from a real user's log: the
program/sample LISTS worked, being S1000 base ops 0x02/0x04, and every
`get_parameter` failed with "no reply within 2.0s").

What the S1000 does have is whole-block access: RPDATA/RKDATA/RSDATA (0x06/
0x08/0x0A) return a program/keygroup/sample header as one nibble-encoded
block, and PDATA/KDATA/SDATA (0x07/0x09/0x0B) write one back. There is no
partial read or write, so this adapter implements the same duck-typed
surface the editor already uses (`get_parameter`, `set_parameter`,
`get_header_bytes`, `set_header_bytes`, `get_header`, `program_list`...) on
top of those:

  - a read fetches the whole block and slices the field out of it
  - a write is READ-MODIFY-WRITE: fetch the block, patch the field's bytes,
    send the WHOLE block back

THE S3000 BLOCK LAYOUT IS A SUPERSET OF THE S1000's, at the same offsets
(checked field-by-field against the S1000 spec's own program/keygroup/sample
block listings and `s3k.params`) - so `s3k.params`' own offsets, encoders and
decoders are reused as-is, for the subset of fields that exist on an S1000
(`_S1000_BLOCK_SIZES`). Anything past that (the modulation matrix, LFO2,
ENV3, portamento, ...) doesn't exist on an S1000: reads of those return a
neutral zero value (so the editor's own loaders, which read a fixed list of
fields, need no per-model branches), writes raise.

THE BLOCK LENGTH IS NEVER ASSUMED. The S1000 spec says its blocks are "about
150 bytes" without giving exact sizes. Every block read is cached at
whatever length the device really sent, patched in place, and written back
at that same length - so an S1000 whose real block is longer than the
field-bearing prefix (internal bytes this app has no field for) round-trips
those bytes untouched. Every read logs the raw block length and bytes
(core/debug_log.py) so a real-hardware bug report shows what an S1000
actually sends.

Untested against real hardware as of writing - there was none available.
See AGENTS.md's "Akai S1000 support" section.
"""

import dataclasses
import time

import s3k.messages as m
import s3k.params as p
from s3k.bridge import DeviceError

from core import debug_log

#: how many bytes of each region's block carry S1000 fields - one past the
#: last field in the S1000 spec's own listing (program VSSCL, keygroup
#: KV_LO, sample SHLTO). A field whose span ends past this doesn't exist on
#: an S1000, however many bytes the device's block really has (it may send
#: its full internal block, with unrelated bytes past these offsets).
_S1000_BLOCK_SIZES = {"program": 72, "keygroup": 149, "sample": 141}

#: Whether the editor offers "Delete Keygroup" at all on an S1000. A real
#: S1000's DELK was seen (2026-10-05 log) to leave the program's GROUPS count
#: unchanged AND to leave the program's chain ending in a stale pointer, after
#: which the next KDATA walked off the chain and spliced a keygroup into a
#: DIFFERENT program. `BridgeWorker._handle_delete_keygroup` therefore repairs
#: GROUPS and then verifies (against a before/after snapshot of every program)
#: that nothing else moved; this is the one-line kill switch if a real S1000
#: shows that can't be made safe - False disables the action for the S1000
#: (S2000/S3000 unaffected).
#:
#: 2026-10-06 tester log: the ORIGINAL flow (DELK of the chosen keygroup, then
#: PDATA to rewrite GROUPS) failed on a real S1000 - DELK of keygroup 0 advanced
#: the program's FIRSTKG by 150 (0x10fe -> 0x1194, no compaction, GROUPS still
#: 10) and the PDATA carrying GROUPS=9 was rejected with REPLY error 01. The
#: flow is now shift-down + DELK of the LAST keygroup + GROUPS rewrite
#: (`BridgeWorker._delete_keygroup_s1000`), ON for the next test build as an
#: UNMEASURED hypothesis. If a tester's log shows it failing, set this False
#: (see AGENTS.md "S1000 memory layout and DELK").
KEYGROUP_DELETE_SUPPORTED = True

#: first byte of each block type ("PRIDENT"/"KGIDENT"/"SHIDENT")
_BLOCK_IDENT = {"program": 1, "keygroup": 2, "sample": 3}

_REQUEST = {
    "program": m.Command.RPDATA,
    "keygroup": m.Command.RKDATA,
    "sample": m.Command.RSDATA,
}
_REPLY = {
    "program": m.Command.PDATA,
    "keygroup": m.Command.KDATA,
    "sample": m.Command.SDATA,
}
_WRITE = _REPLY

#: how long a block fetched for one field read stays valid for the next.
#: The editor loads a keygroup by reading ~50 fields back to back through
#: get_parameter - without this each would be its own multi-hundred-byte
#: SysEx round trip at 31250 baud. Short enough that a following Refresh/
#: re-selection (seconds later at the soonest) always re-reads from the
#: hardware; program_list()/sample_list() (called by every Refresh) also
#: invalidate it explicitly.
_CACHE_TTL_SECONDS = 1.5


#: Fields `s3k.params` declares "Not used - fixed value in the specification"
#: (range 0..0) because the S3000 documents it that way, but that the S1000
#: spec lists as real controller-routing parameters, each "+/-50" - the
#: S1000's fixed equivalent of the S3000's assignable modulation matrix
#: (K_LOUD "Key>Loudness", P_LOUD "Pressure>Loudness", K_PANP "Key>Pan
#: position", MW_PAN "Modwheel pan amount", K_LRAT/K_LDEP/K_LDEL "Key>LFO
#: rate/depth/delay"; keygroup: V_FREQ/P_FREQ/E_FREQ "Velocity/Pressure/
#: Envelope>Filter freq", E_PTCH "Envelope>Pitch", KV_LO "Velocity>Loudness
#: offset"). Left alone, s3k's own range check would refuse every non-zero
#: write, and decode_field (which sign-extends only a field whose DECLARED
#: range goes negative) would read a stored -20 back as 236 - so the
#: corrected range has to be applied to BOTH directions, which is why this
#: lives in the adapter (applied to whatever Parameter the caller hands in,
#: by name) rather than in each caller. Only ever applied on an S1000 -
#: s3k.params itself is untouched (AGENTS.md: don't edit the dependency).
_S1000_RANGE_OVERRIDES = {
    (name, region): {"minimum": -50, "maximum": 50}
    for region, names in (
        ("program", ("K_LOUD", "P_LOUD", "K_PANP", "MW_PAN", "K_LRAT", "K_LDEP", "K_LDEL")),
        ("keygroup", ("V_FREQ", "P_FREQ", "E_FREQ", "E_PTCH", "KV_LO")),
    )
    for name in names
}


def s1000_param(param):
    """*param* as the S1000 defines it - a corrected copy where `s3k.params`
    declares an S3000-only meaning, otherwise the same object."""
    override = _S1000_RANGE_OVERRIDES.get((param.name, param.region))
    return dataclasses.replace(param, **override) if override else param


def supports(param):
    """Whether *param* exists on an S1000 at all."""
    limit = _S1000_BLOCK_SIZES.get(param.region)
    return limit is not None and param.end <= limit


class S1000Bridge:
    def __init__(self, bridge, logger=None):
        self._bridge = bridge
        self._logger = logger or debug_log.get_logger()
        # (region, index, selector) -> (monotonic timestamp, bytearray)
        self._blocks = {}

    # S3kBridge.renumber_programs reads/writes PRGNUM through the S3000-only
    # header ops - it would just time out on an S1000. It only exists for the
    # Multi tab's Program Change flow (which an S1000 doesn't have), and
    # BridgeWorker._handle_program_change skips it when getattr(...) is None.
    renumber_programs = None

    def __getattr__(self, name):
        # everything this class doesn't override (status, out, inp,
        # exclusive_channel, send_and_receive, ...) is the wrapped S3kBridge's
        return getattr(self._bridge, name)

    # -- lists / deletes: base-protocol ops the wrapped bridge already does --
    # (all four can change what any cached block's index refers to)

    def program_list(self, **kwargs):
        self.invalidate()
        return self._bridge.program_list(**kwargs)

    def sample_list(self, **kwargs):
        self.invalidate()
        return self._bridge.sample_list(**kwargs)

    def delete_program(self, *args, **kwargs):
        self.invalidate()
        return self._bridge.delete_program(*args, **kwargs)

    def delete_keygroup(self, *args, **kwargs):
        self.invalidate()
        return self._bridge.delete_keygroup(*args, **kwargs)

    def delete_sample(self, *args, **kwargs):
        self.invalidate()
        return self._bridge.delete_sample(*args, **kwargs)

    def invalidate(self):
        self._blocks = {}

    def send_and_receive(self, frame, **kwargs):
        # raw frames sent straight through (BridgeWorker's create-program/
        # keygroup flows send PDATA/KDATA this way) change what any cached
        # block holds - a read right after one must see the hardware's copy,
        # not the stale one, or the editor's reload after a duplicate would
        # show the pre-duplicate GROUPS count
        self.invalidate()
        return self._bridge.send_and_receive(frame, **kwargs)

    # -- whole-block transport ------------------------------------------------

    @staticmethod
    def _selector(region, keygroup):
        # only a keygroup read/write is addressed by a second number
        return keygroup if region == "keygroup" else 0

    @staticmethod
    def _address(region, index, selector):
        payload = list(m.encode_u14(index))
        if region == "keygroup":
            payload.append(selector)
        return payload

    @staticmethod
    def _describe(region, index, selector):
        # "program 2", "keygroup 1 of program 2", "sample 5" - for log lines
        # and error messages (a keygroup is addressed by program AND keygroup)
        if region == "keygroup":
            return f"keygroup {selector} of program {index}"
        return f"{region} {index}"

    def _read_block(self, region, index, selector, *, timeout=None, fresh=False):
        key = (region, index, selector)
        cached = self._blocks.get(key)
        if (
            cached is not None
            and not fresh
            and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS
        ):
            return cached[1]

        what = self._describe(region, index, selector)
        frame = m.build_frame(
            _REQUEST[region],
            self._address(region, index, selector),
            exclusive_channel=self._bridge.exclusive_channel,
        )
        reply = self._bridge.send_and_receive(frame, timeout=timeout)
        _channel, command, payload = m.parse_frame(reply)
        if command == m.Command.REPLY:
            raise DeviceError(f"device reported an error reading {what}")
        if command != _REPLY[region]:
            raise DeviceError(
                f"expected {int(_REPLY[region]):#04x} reading {what}, "
                f"got {command:#04x}"
            )
        header_len = len(self._address(region, index, selector))
        try:
            data = bytearray(m.decode_nibbles(payload[header_len:]))
        except ValueError as e:
            raise DeviceError(f"malformed block reading {what}: {e}") from e
        if not data or data[0] != _BLOCK_IDENT[region]:
            raise DeviceError(
                f"reading {what}: block identifier is "
                f"{data[0] if data else None!r}, expected "
                f"{_BLOCK_IDENT[region]} - the index may be out of range"
            )
        if len(data) < _S1000_BLOCK_SIZES[region]:
            # not fatal - fields past what the device really sent just read
            # as neutral values (see get_parameter) - but worth seeing in a
            # bug report, since it means this module's layout table is off
            self._logger.warning(
                f"S1000Bridge: {what} block is only {len(data)} bytes, expected "
                f"at least {_S1000_BLOCK_SIZES[region]}"
            )
        self._logger.debug(
            f"S1000Bridge: read {what}: {len(data)} bytes: {bytes(data).hex(' ')}"
        )
        self._blocks[key] = (time.monotonic(), data)
        return data

    def _write_block(self, region, index, selector, block, what, *, timeout=None):
        frame = m.build_frame(
            _WRITE[region],
            [*self._address(region, index, selector), *m.encode_nibbles(block)],
            exclusive_channel=self._bridge.exclusive_channel,
        )
        # whatever happens, the cached copy is no longer known to match the
        # hardware - dropped first, re-added below only on a confirmed OK
        self._blocks.pop((region, index, selector), None)
        reply = self._bridge.send_and_receive(frame, timeout=timeout)
        _channel, command, _payload = m.parse_frame(reply)
        if command != m.Command.REPLY:
            raise DeviceError(f"expected REPLY writing {what}, got {command:#04x}")
        if not m.Reply.decode(reply).ok:
            raise DeviceError(f"device reported an error writing {what}")
        self._logger.debug(
            f"S1000Bridge: wrote {what}: {len(block)} bytes: {bytes(block).hex(' ')}"
        )
        self._blocks[(region, index, selector)] = (time.monotonic(), block)

    # -- the S3kBridge surface the editor uses -----------------------------------

    def get_header_bytes(
        self, region, index, offset, count, *, selector=0, timeout=None, _bounds=True
    ):
        if region not in _S1000_BLOCK_SIZES:
            raise KeyError(f"{region!r} doesn't exist on an S1000")
        block = self._read_block(region, index, selector, timeout=timeout)
        # clipped, not an error, when asked for more than the block holds:
        # the create-program/keygroup flows ask for the S3000's 192 bytes to
        # clone a whole header
        return bytes(block[offset : offset + count])

    def set_header_bytes(
        self,
        region,
        index,
        offset,
        data,
        *,
        selector=0,
        postpone=None,
        confirm=True,
        timeout=None,
        _bounds=True,
    ):
        if region not in _S1000_BLOCK_SIZES:
            raise KeyError(f"{region!r} doesn't exist on an S1000")
        block = bytearray(self._read_block(region, index, selector, timeout=timeout))
        if offset + len(data) > len(block):
            raise ValueError(
                f"write of {len(data)} bytes at offset {offset} runs past this "
                f"{region} block's {len(block)} bytes"
            )
        block[offset : offset + len(data)] = data
        self._write_block(
            region,
            index,
            selector,
            block,
            f"{self._describe(region, index, selector)} at offset {offset}",
            timeout=timeout,
        )

    def get_parameter(
        self, param, index, *, keygroup=0, region=None, timeout=None, _bounds=True
    ):
        param = s1000_param(
            param if isinstance(param, p.Parameter) else p.lookup(param, region)
        )
        if not supports(param):
            # not an S1000 field - a neutral value rather than an error so
            # the editor's fixed field lists load unmodified (its widgets
            # for these are disabled/hidden in S1000 mode instead)
            return p.decode_field(param, bytes(param.size))
        raw = self.get_header_bytes(
            param.region,
            index,
            param.offset,
            param.size,
            selector=self._selector(param.region, keygroup),
            timeout=timeout,
        )
        if len(raw) < param.size:
            return p.decode_field(param, bytes(param.size))
        return p.decode_field(param, raw)

    def set_parameter(
        self,
        param,
        index,
        value,
        *,
        keygroup=0,
        region=None,
        postpone=None,
        confirm=True,
        timeout=None,
    ):
        param = s1000_param(
            param if isinstance(param, p.Parameter) else p.lookup(param, region)
        )
        if not supports(param):
            raise ValueError(f"{param.name} doesn't exist on an S1000")
        if not param.writable:
            why = "read-only" if param.readonly else "an internal block address"
            raise ValueError(f"{param.name} is {why} and must not be written")
        self.set_header_bytes(
            param.region,
            index,
            param.offset,
            p.encode_field(param, value),
            selector=self._selector(param.region, keygroup),
            timeout=timeout,
        )

    def get_header(self, region, index, *, keygroup=0, timeout=None):
        block = self._read_block(
            region, index, self._selector(region, keygroup), timeout=timeout
        )
        return {
            x.name: p.decode_field(s1000_param(x), bytes(block[x.offset : x.end]))
            for x in p.region_params(region)
            if supports(x) and x.end <= len(block)
        }
