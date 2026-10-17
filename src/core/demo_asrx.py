"""A fake Ensoniq ASR-X that speaks real SysEx, for tests and demo mode.

Same idea as `core/demo_ensoniq.py`: it fakes the MIDI PORTS, not any Python API. `FakeASRX.out`/`.inp` have the rtmidi surface (`send_message(frame)` /
`get_message()`, frames INCLUDING the F0/F7), so the real engine (controller/asrx_transfers.py) runs on top of it with no hardware.

**It is built from the same reading of the ROM as the codec (core/asrx_sysex.py, dev_docs/asrx-rom-findings.md), so it can only catch our own inconsistencies, not a
misreading of the ROM or a real unit's quirks.** The first log from a real ASR-X is what confirms or refutes them.

What it answers, and how the real unit is believed to behave (all from the ROM):
 * `7E <dev> 06 01`           identity request -> `7E <dev> 06 02` + 11 bytes (the contents are a guess).
 * `7E <dev> 05 04 <slot>`     name request -> `7E <own> 05 03 <slot> 00 10 <16 name bytes>`; an empty slot, or a HIDDEN one (the unit's recording buffers), -> `0F 10 01 <own> 7F 41`.
 * `0F 10 01 <dev> 01|03 <type> <slot>`   whole-object download -> `41`/`43` ... with the checksum over the payload septets; empty -> `7F 41`.
 * `0F 10 01 <dev> 04 ...`     range read -> `44` ...; a range outside the object is answered with SILENCE (`support_range=False` makes it always silent).
 * `0F 10 01 <dev> 02 ...`     object upload -> stores it and answers `42` after the checksum matched; a wrong checksum leaves the data stored and sends nothing
                               (the ROM compares after it has written); not enough memory -> nothing stored, nothing sent.
Anything else lands in `ignored_ops`.

Fault switches (all counted down as they fire): `drop_acks`, `corrupt_range_replies`, `drop_range_replies`, `corrupt_object_checksum`, and `max_reply_bytes` (a reply longer than
this is never delivered - what an input that splits long SysEx would do to a whole-object download).
"""

from collections import deque

from core import asrx_sysex as a

SOX = 0xF0
EOX = 0xF7


def triangle(frames, period=200, amplitude=15000):
    half = period // 2
    return [int((abs((i % period) - half) / half * 2 - 1) * amplitude) for i in range(frames)]


def make_sample_object(slot, name, *, frames=2000, rate=44100, root=60, samples=None):
    if samples is None:
        samples = triangle(frames)
    return a.build_sample_object(slot, name, samples, rate, root_key=root)


class _FakeOut:
    def __init__(self, fake):
        self._fake = fake

    def send_message(self, message, *, write=False):
        self._fake.handle(list(message))

    def close_port(self):
        pass


class _FakeIn:
    def __init__(self, fake):
        self._fake = fake

    def get_message(self):
        if not self._fake._outbox:
            return None
        return (list(self._fake._outbox.popleft()), 0.0)

    def close_port(self):
        pass


class FakeASRX:
    def __init__(
        self,
        *,
        slots=None,
        hidden=None,
        device=0,
        memory_bytes=64 * 1024 * 1024,
        support_range=True,
        drop_acks=0,
        corrupt_range_replies=0,
        drop_range_replies=0,
        corrupt_object_checksum=0,
        max_reply_bytes=None,
    ):
        self.device = device
        self.memory_bytes = memory_bytes
        self.support_range = support_range
        self.drop_acks = drop_acks
        self.corrupt_range_replies = corrupt_range_replies
        self.drop_range_replies = drop_range_replies
        self.corrupt_object_checksum = corrupt_object_checksum
        self.max_reply_bytes = max_reply_bytes
        #: slot -> the raw SMP1 object bytes
        self.slots = {}
        #: slots that hold an object but never answer a name request (the recording buffers)
        self.hidden = set(hidden or ())
        self._outbox = deque()
        self.out = _FakeOut(self)
        self.inp = _FakeIn(self)

        self.ignored_ops = []
        self.received = []  # every frame, F0/F7 included
        self.rejected = []
        self.commands = []  # (command, slot) of every ASR-X command received
        self.uploads = []  # (slot, size, checksum_ok)
        self.range_reads = []  # (slot, offset, length)
        self.name_requests = []

        if slots is None:
            self._populate_default()
        else:
            self.slots.update(slots)

    def _populate_default(self):
        self.slots[0] = make_sample_object(0, "KICK", frames=3000, rate=44100)
        self.slots[1] = make_sample_object(1, "SNARE", frames=4000, rate=44100, root=48)
        self.slots[2] = make_sample_object(2, "PAD C3", frames=9000, rate=22050)

    @property
    def visible_slots(self):
        return sorted(s for s in self.slots if s not in self.hidden)

    def audio_of(self, slot):
        return a.parse_sample_object(self.slots[slot], slot=slot).samples

    # -- the unit's side of the wire --------------------------------------------------------------------------

    def _deliver(self, data):
        if self.max_reply_bytes is not None and len(data) > self.max_reply_bytes:
            self.rejected.append(("reply too long, dropped", len(data)))
            return
        self._outbox.append([SOX, *data, EOX])

    def _error(self, code=a.ERROR_NO_SUCH_OBJECT):
        self._deliver(a.HEADER + bytes((self.device, a.REPLY_ERROR, code)))

    def _addressed(self, device):
        return device in (self.device, a.BROADCAST_DEVICE)

    def handle(self, frame):
        if not frame or frame[0] != SOX or frame[-1] != EOX:
            return
        self.received.append(frame)
        data = bytes(frame[1:-1])
        if len(data) >= 4 and data[0] == a.UNIVERSAL_NON_REALTIME:
            self._handle_universal(data)
            return
        if not a.is_asrx(data):
            self.rejected.append(data)
            return
        if not self._addressed(data[3]):
            return
        command = data[4]
        if command in (a.CMD_GET_OBJECT, a.CMD_GET_OBJECT_EXPORT):
            self._handle_get(data, command)
        elif command == a.CMD_READ_RANGE:
            self._handle_range(data)
        elif command == a.CMD_PUT_OBJECT:
            self._handle_put(data)
        else:
            self.ignored_ops.append(command)

    def _handle_universal(self, data):
        if not self._addressed(data[1]):
            return
        if data[2] == 0x06 and data[3] == 0x01:
            self._deliver(bytes((0x7E, self.device, 0x06, 0x02)) + bytes((0x0F, 0x10, 0x01, 0x00, 0x02, 0x02, 0x7F, 0x7F, 0x76, 0x0A, 0x00)))
            return
        if data[2] == 0x05 and data[3] == 0x04 and len(data) >= 6:
            slot = a.decode_u14(data, 4)
            self.name_requests.append(slot)
            if slot in self.slots and slot not in self.hidden:
                name = a.parse_sample_object(self.slots[slot], slot=slot, header_only=True).name
                self._deliver(bytes((0x7E, self.device, 0x05, 0x03)) + a.encode_u14(slot) + bytes((0x00, 0x10)) + a.encode_name(name))
            else:
                self._error()
            return
        self.ignored_ops.append(("7E", data[2], data[3]))

    def _handle_get(self, data, command):
        type_, slot = data[5], a.decode_u14(data, 6)
        self.commands.append((command, slot))
        obj = self.slots.get(slot)
        if type_ != a.TYPE_SAMPLE or obj is None:
            self._error()
            return
        septets = a.pack_payload(obj)
        total = a.checksum(septets)
        if self.corrupt_object_checksum:
            self.corrupt_object_checksum -= 1
            total = (total + 1) & 0x3FFF
        reply = (
            a.HEADER
            + bytes((self.device, command + 0x40, type_))
            + a.encode_u14(slot)
            + a.encode_u32(len(obj))
            + septets
            + a.encode_u14(total)
        )
        self._deliver(reply)

    def _handle_range(self, data):
        type_, slot = data[5], a.decode_u14(data, 6)
        section, index = data[8], a.decode_u14(data, 9)
        offset, length = a.decode_u32(data, 11), a.decode_u32(data, 16)
        self.commands.append((a.CMD_READ_RANGE, slot))
        self.range_reads.append((slot, offset, length))
        if not self.support_range:
            self.ignored_ops.append("range")
            return
        obj = self.slots.get(slot)
        if type_ != a.TYPE_SAMPLE or obj is None or section != 0 or offset >= len(obj) or offset + length > len(obj) or length == 0:
            return  # the unit answers a bad range with silence
        if self.drop_range_replies:
            self.drop_range_replies -= 1
            return
        part = bytearray(obj[offset : offset + length])
        if self.corrupt_range_replies:
            self.corrupt_range_replies -= 1
            part[len(part) // 2] ^= 0x55
        reply = (
            a.HEADER
            + bytes((self.device, a.REPLY_RANGE, type_))
            + a.encode_u14(slot)
            + bytes((section,))
            + a.encode_u14(index)
            + a.encode_u32(offset)
            + a.encode_u32(length)
            + a.pack_payload(bytes(part))
        )
        self._deliver(reply)

    def _handle_put(self, data):
        type_, slot = data[5], a.decode_u14(data, 6)
        size = a.decode_u32(data, 8)
        self.commands.append((a.CMD_PUT_OBJECT, slot))
        septets = data[13:-2]
        if len(septets) != a.payload_septet_count(size):
            self.uploads.append((slot, size, False))
            self.ignored_ops.append("put: payload length does not match the size")
            return
        obj = a.unpack_payload(septets, size)
        used = sum(len(v) for k, v in self.slots.items() if k != slot)
        if used + size > self.memory_bytes:
            self.uploads.append((slot, size, None))
            return  # memory full: nothing stored, nothing sent
        sent = a.decode_u14(data, len(data) - 2)
        checksum_ok = sent == a.checksum(septets)
        self.uploads.append((slot, size, checksum_ok))
        if type_ != a.TYPE_SAMPLE or obj[4:8] != a.SAMPLE_TAG:
            self.ignored_ops.append("put: not a sample object")
            return
        self.slots[slot] = obj  # stored before the comparison, as the ROM does
        if not checksum_ok:
            return
        if self.drop_acks:
            self.drop_acks -= 1
            return
        self._deliver(a.HEADER + bytes((self.device, a.REPLY_PUT_ACK)))
