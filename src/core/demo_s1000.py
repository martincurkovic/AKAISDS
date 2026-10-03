"""A fake Akai S1000 that speaks real SysEx, for tests and demo mode.

Unlike s3ked's `DemoBridge` (which fakes the bridge's own Python surface),
this fakes the MIDI PORTS: `FakeS1000.out`/`.inp` stand in for the rtmidi
output/input an `s3k.bridge.S3kBridge` would open, and it answers whatever
frames arrive the way an S1000 does - so the whole real stack (`S3kBridge`,
`core.s1000_bridge.S1000Bridge`, `BridgeWorker`, the editor window) can be
exercised against it with no hardware.

It deliberately reproduces the quirk that broke the first S1000 user's
session: the S3000-only extended ops (function codes 0x20 and up, which is
everything `S3kBridge.get_header_bytes` uses) are IGNORED - no reply, not
even an error - so a code path that still reaches for one times out here
exactly as it does on the real machine (and shows up in `ignored_ops`).

Supported, per the "S1000 MIDI Exclusive Communication" spec: RSTAT,
RPLIST, RSLIST, RPDATA/PDATA, RKDATA/KDATA, RSDATA/SDATA, DELP, DELK, DELS.
Not supported (answered with silence, like anything else unknown): sample
data packets, drum/misc data, SETEX.

`block_size` is each block's real length on the wire. The spec only says
"about 150 bytes"; bytes past the spec's own fields are filled with a
non-zero junk pattern so any code that wrongly treats them as fields (or
fails to round-trip them) is caught rather than hidden behind zeros.
"""

from collections import deque

import s3k.messages as m
import s3k.params as p

DEFAULT_BLOCK_SIZE = 150
_JUNK = 0xA5

# the S1000 spec's own field extents (program VSSCL, keygroup KV_LO, sample
# SHLTO) - bytes past these are "unknown to this app" padding
_FIELD_EXTENT = {"program": 72, "keygroup": 149, "sample": 141}
_IDENT = {"program": 1, "keygroup": 2, "sample": 3}


def _set(block, region, name, value):
    param = p.lookup(name, region)
    block[param.offset : param.end] = p.encode_field(param, value)


def make_sample_block(name, *, frames=44100, rate=44100, size=DEFAULT_BLOCK_SIZE):
    block = bytearray([_JUNK] * max(size, _FIELD_EXTENT["sample"]))
    block[: _FIELD_EXTENT["sample"]] = bytes(_FIELD_EXTENT["sample"])
    block[0] = _IDENT["sample"]
    for field, value in [
        ("SBANDW", 1 if rate > 22050 else 0),
        ("SPITCH", 60),
        ("SHNAME", name),
        ("SSRVLD", 0x80),
        ("SPTYPE", 0),
        ("STUNO", 0),
        ("SLNGTH", frames),
        ("SSTART", 0),
        ("SMPEND", frames - 1),
        ("LOOPAT1", frames - 1),
        ("LLNGTH1", 0),
        ("LDWELL1", 9999),
        ("SSRATE", rate),
        ("SHLTO", 0),
    ]:
        _set(block, "sample", field, value)
    return block


def make_keygroup_block(
    sample_name, lo=24, hi=127, *, size=DEFAULT_BLOCK_SIZE
):
    block = bytearray([_JUNK] * max(size, _FIELD_EXTENT["keygroup"]))
    block[: _FIELD_EXTENT["keygroup"]] = bytes(_FIELD_EXTENT["keygroup"])
    block[0] = _IDENT["keygroup"]
    for field, value in [
        ("LONOTE", lo),
        ("HINOTE", hi),
        ("FILFRQ", 99),
        ("SUSTN1", 99),
        ("SUSTN2", 99),
        ("SNAME1", sample_name),
        ("LOVEL1", 0),
        ("HIVEL1", 127),
    ]:
        _set(block, "keygroup", field, value)
    # zones 2-4 unused: empty (all-space) names, full velocity range
    for zone in (2, 3, 4):
        _set(block, "keygroup", f"SNAME{zone}", "")
        _set(block, "keygroup", f"HIVEL{zone}", 127)
    return block


def make_program_block(
    name, prgnum, groups, *, size=DEFAULT_BLOCK_SIZE
):
    block = bytearray([_JUNK] * max(size, _FIELD_EXTENT["program"]))
    block[: _FIELD_EXTENT["program"]] = bytes(_FIELD_EXTENT["program"])
    block[0] = _IDENT["program"]
    for field, value in [
        ("PRNAME", name),
        ("PRGNUM", prgnum + 1),  # prgnum is the raw byte; s3k adds the +1 display offset
        ("POLYPH", 16),
        ("PRIORT", 1),
        ("PLAYLO", 24),
        ("PLAYHI", 127),
        ("STEREO", 99),
        ("PRLOUD", 80),
        ("GROUPS", groups),
    ]:
        _set(block, "program", field, value)
    return block


class _FakeOut:
    # stands in for S3kBridge.out (a ThrottledOut around an rtmidi MidiOut)
    def __init__(self, fake):
        self._fake = fake

    def send_message(self, message, *, write=False):
        self._fake.handle(bytes(message))

    def close_port(self):
        pass


class _FakeIn:
    # stands in for S3kBridge.inp (an rtmidi MidiIn-compatible)
    def __init__(self, fake):
        self._fake = fake

    def get_message(self):
        if not self._fake._outbox:
            return None
        return (list(self._fake._outbox.popleft()), 0.0)

    def close_port(self):
        pass


class FakeS1000:
    def __init__(
        self,
        *,
        programs=None,
        samples=None,
        exclusive_channel=0,
        block_size=DEFAULT_BLOCK_SIZE,
    ):
        self.exclusive_channel = exclusive_channel
        self.block_size = block_size
        # [{"block": bytearray, "keygroups": [bytearray, ...]}]
        self.programs = []
        self.samples = []
        self._outbox = deque()
        self.out = _FakeOut(self)
        self.inp = _FakeIn(self)
        #: function codes of every frame received that this fake doesn't
        #: implement (and therefore never answered)
        self.ignored_ops = []
        #: every frame received, decoded as (op, payload)
        self.received = []
        #: how many whole-block writes (PDATA/KDATA/SDATA) were accepted
        self.writes = 0

        if programs is None and samples is None:
            self._populate_default()
        else:
            for name, block in samples or []:
                self.samples.append(block)
            for entry in programs or []:
                self.programs.append(entry)

    def _populate_default(self):
        for name, frames in [("KICK", 22050), ("SNARE", 33000), ("PAD", 88200)]:
            self.samples.append(
                make_sample_block(name, frames=frames, size=self.block_size)
            )
        self.programs.append(
            {
                "block": make_program_block(
                    "DRUMS", 0, 2, size=self.block_size
                ),
                "keygroups": [
                    make_keygroup_block("KICK", 24, 59, size=self.block_size),
                    make_keygroup_block("SNARE", 60, 127, size=self.block_size),
                ],
            }
        )
        self.programs.append(
            {
                "block": make_program_block(
                    "PAD PROG", 1, 1, size=self.block_size
                ),
                "keygroups": [
                    make_keygroup_block("PAD", 24, 127, size=self.block_size)
                ],
            }
        )

    def bridge(self, *, timeout=2.0):
        """A real `S3kBridge` wired to this fake's ports."""
        from s3k.bridge import S3kBridge

        return S3kBridge(
            self.out,
            self.inp,
            "fake S1000",
            exclusive_channel=self.exclusive_channel,
            timeout=timeout,
        )

    # -- the S1000 side of the wire ---------------------------------------------

    def _reply(self, command, payload=()):
        self._outbox.append(
            m.build_frame(
                command, payload, exclusive_channel=self.exclusive_channel
            )
        )

    def _reply_ok(self, ok=True):
        self._reply(m.Command.REPLY, [0 if ok else 1])

    def handle(self, frame):
        if not frame or frame[0] != m.SOX:
            return  # plain MIDI (a Program Change) - nothing to answer
        try:
            channel, command, payload = m.parse_frame(frame)
        except ValueError:
            return
        if channel != self.exclusive_channel:
            return
        self.received.append((command, payload))
        handler = {
            m.Command.RSTAT: self._rstat,
            m.Command.RPLIST: self._rplist,
            m.Command.RSLIST: self._rslist,
            m.Command.RPDATA: self._rpdata,
            m.Command.PDATA: self._pdata,
            m.Command.RKDATA: self._rkdata,
            m.Command.KDATA: self._kdata,
            m.Command.RSDATA: self._rsdata,
            m.Command.SDATA: self._sdata,
            m.Command.DELP: self._delp,
            m.Command.DELK: self._delk,
            m.Command.DELS: self._dels,
        }.get(command)
        if handler is None:
            # an S3000-only extended op, or something this fake doesn't do:
            # the real S1000 stays silent
            self.ignored_ops.append(command)
            return
        handler(payload)

    def _rstat(self, payload):
        self._reply(
            m.Command.STAT,
            [
                0,  # minor
                2,  # major - "2.00"
                *m.encode_u14(480),
                *m.encode_u14(480 - len(self.samples) - len(self.programs)),
                *m.encode_lsb_bytes(2_000_000, 4),
                *m.encode_lsb_bytes(1_000_000, 4),
                self.exclusive_channel,
            ],
        )

    def _name_list(self, command, blocks, name_offset):
        payload = list(m.encode_u14(len(blocks)))
        for block in blocks:
            payload.extend(block[name_offset : name_offset + 12])
        self._reply(command, payload)

    def _rplist(self, payload):
        self._name_list(
            m.Command.PLIST,
            [entry["block"] for entry in self.programs],
            p.lookup("PRNAME", "program").offset,
        )

    def _rslist(self, payload):
        self._name_list(
            m.Command.SLIST, self.samples, p.lookup("SHNAME", "sample").offset
        )

    def _program(self, index):
        return self.programs[index] if 0 <= index < len(self.programs) else None

    def _rpdata(self, payload):
        entry = self._program(m.decode_u14(payload[0], payload[1]))
        if entry is None:
            return self._reply_ok(False)
        self._reply(
            m.Command.PDATA,
            [*payload[:2], *m.encode_nibbles(entry["block"])],
        )

    def _pdata(self, payload):
        index = m.decode_u14(payload[0], payload[1])
        block = bytearray(m.decode_nibbles(payload[2:]))
        entry = self._program(index)
        if entry is None:
            # creating a program: needs `GROUPS` dummy keygroups to follow
            groups = block[p.lookup("GROUPS", "program").offset]
            self.programs.append(
                {
                    "block": block,
                    "keygroups": [
                        make_keygroup_block("", size=self.block_size)
                        for _ in range(groups)
                    ],
                }
            )
        else:
            groups = block[p.lookup("GROUPS", "program").offset]
            if groups != len(entry["keygroups"]):
                # "the parameter GROUPS must be correct" - spec'd as an error
                return self._reply_ok(False)
            entry["block"] = block
        self.writes += 1
        self._reply_ok()

    def _rkdata(self, payload):
        entry = self._program(m.decode_u14(payload[0], payload[1]))
        kg = payload[2]
        if entry is None or kg >= len(entry["keygroups"]):
            return self._reply_ok(False)
        self._reply(
            m.Command.KDATA,
            [*payload[:3], *m.encode_nibbles(entry["keygroups"][kg])],
        )

    def _kdata(self, payload):
        entry = self._program(m.decode_u14(payload[0], payload[1]))
        kg = payload[2]
        block = bytearray(m.decode_nibbles(payload[3:]))
        if entry is None:
            return self._reply_ok(False)
        if kg < len(entry["keygroups"]):
            entry["keygroups"][kg] = block
        else:
            entry["keygroups"].append(block)
            entry["block"][p.lookup("GROUPS", "program").offset] = len(
                entry["keygroups"]
            )
        self.writes += 1
        self._reply_ok()

    def _rsdata(self, payload):
        index = m.decode_u14(payload[0], payload[1])
        if not 0 <= index < len(self.samples):
            return self._reply_ok(False)
        self._reply(
            m.Command.SDATA,
            [*payload[:2], *m.encode_nibbles(self.samples[index])],
        )

    def _sdata(self, payload):
        index = m.decode_u14(payload[0], payload[1])
        block = bytearray(m.decode_nibbles(payload[2:]))
        if not 0 <= index < len(self.samples):
            return self._reply_ok(False)
        self.samples[index] = block
        self.writes += 1
        self._reply_ok()

    def _delp(self, payload):
        index = m.decode_u14(payload[0], payload[1])
        if self._program(index) is None:
            return self._reply_ok(False)
        del self.programs[index]
        self._reply_ok()

    def _delk(self, payload):
        entry = self._program(m.decode_u14(payload[0], payload[1]))
        kg = payload[2]
        if entry is None or kg >= len(entry["keygroups"]):
            return self._reply_ok(False)
        del entry["keygroups"][kg]
        entry["block"][p.lookup("GROUPS", "program").offset] = len(
            entry["keygroups"]
        )
        self._reply_ok()

    def _dels(self, payload):
        index = m.decode_u14(payload[0], payload[1])
        if not 0 <= index < len(self.samples):
            return self._reply_ok(False)
        del self.samples[index]
        self._reply_ok()
