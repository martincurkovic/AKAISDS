# tests for core/akai_program_file.py (.p1/.p3 codec) and the BridgeWorker's
# export/import of a whole program (read every raw block / write them back as
# a NEW program), the latter run against FakeS1000 for a real S1000 round trip
# and against test_program_editor_bridge's recording fake for an S2000/S3000.

import pytest
import s3k.messages as m
import s3k.params as p

from core import akai_program_file as apf
from core.demo_s1000 import FakeS1000, make_keygroup_block, make_program_block
from core.program_editor_bridge import BridgeWorker, LoggingBridge
from core.s1000_bridge import S1000Bridge


def _blocks(size, groups=3, name="TEST PROG"):
    program = bytearray(make_program_block(name, 0, groups, size=size))
    # a stale, absolute-looking pointer, as read from a sampler
    program[1:3] = b"\x34\x12"
    keygroups = []
    for i in range(groups):
        kg = bytearray(make_keygroup_block(f"SMP{i}", 24 + i * 10, 33 + i * 10, size=size))
        kg[1:3] = b"\x78\x56"
        keygroups.append(bytes(kg))
    return bytes(program), keygroups


@pytest.mark.parametrize("size, ext", [(150, ".p1"), (192, ".p3")])
def test_build_file_lays_out_header_then_keygroups_with_file_relative_pointers(size, ext):
    program, keygroups = _blocks(size)
    data = apf.build_file(program, keygroups)

    assert len(data) == size * 4
    assert data[1:3] == size.to_bytes(2, "little")  # FIRSTKG = one block in
    # keygroup i's NXTKG is the offset of the next block, the last one's is EOF
    for i in range(3):
        start = size * (i + 1)
        assert data[start + 1 : start + 3] == (size * (i + 2)).to_bytes(2, "little")
    # a real S1000 reported exactly this for 10 keygroups: last NXTKG 0x672
    big = apf.build_file(*_blocks(150, groups=10))
    assert big[150 * 10 + 1 : 150 * 10 + 3] == b"\x72\x06"
    assert apf.BLOCK_SIZES[size] == ext


def test_build_file_changes_only_the_pointer_bytes():
    program, keygroups = _blocks(150)
    data = apf.build_file(program, keygroups)
    assert data[0] == program[0] and data[3:150] == program[3:]
    assert data[150 + 3 : 300] == keygroups[0][3:]


@pytest.mark.parametrize("size", [150, 192])
def test_parse_file_round_trips_blocks_and_name(size):
    program, keygroups = _blocks(size, name="MY PROGRAM")
    parsed = apf.parse_file(apf.build_file(program, keygroups))

    assert parsed.block_size == size
    assert parsed.name == "MY PROGRAM"
    assert parsed.family == ("s1000" if size == 150 else "s2000_s3000")
    assert parsed.extension == (".p1" if size == 150 else ".p3")
    assert len(parsed.keygroups) == 3
    # everything except the pointer bytes is exactly what was read
    assert parsed.program[3:] == program[3:]
    assert [k[3:] for k in parsed.keygroups] == [k[3:] for k in keygroups]


def test_parse_file_ignores_trailing_padding():
    data = apf.build_file(*_blocks(192)) + b"\x00" * 100
    assert len(apf.parse_file(data).keygroups) == 3


def test_parse_file_falls_back_to_total_length_when_firstkg_is_odd():
    data = bytearray(apf.build_file(*_blocks(192)))
    data[1:3] = b"\x00\x00"
    assert apf.parse_file(bytes(data)).block_size == 192


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d[:100], "unrecognised|shorter|truncated"),
        (lambda d: d[:-1], "truncated"),
        (lambda d: bytes([5]) + d[1:], "not a program file"),
        (lambda d: d[:42] + b"\x00" + d[43:], "keygroups"),
        (lambda d: d[:150] + bytes([9]) + d[151:], "identifier"),
        (lambda d: b"RIFF" + d[4:], "not a program|unrecognised"),
    ],
)
def test_parse_file_rejects_bad_files_with_a_readable_message(mutate, message):
    data = apf.build_file(*_blocks(150))
    with pytest.raises(apf.ProgramFileError, match=message):
        apf.parse_file(mutate(data))


def test_validate_blocks_rejects_an_unexpected_block_size_and_miscounts():
    program, keygroups = _blocks(150)
    with pytest.raises(apf.ProgramFileError, match="byte program block"):
        apf.validate_blocks(program + b"\x00" * 10, keygroups)
    with pytest.raises(apf.ProgramFileError, match="claims 3 keygroups"):
        apf.validate_blocks(program, keygroups[:2])


def test_zone_sample_names_lists_each_used_name_once_in_order():
    program, keygroups = _blocks(150)
    parsed = apf.parse_file(apf.build_file(program, keygroups))
    assert apf.zone_sample_names(parsed) == ["SMP0", "SMP1", "SMP2"]
    parsed.keygroups[1] = parsed.keygroups[0]
    assert apf.zone_sample_names(parsed) == ["SMP0", "SMP2"]


# --- BridgeWorker: export/import against a fake sampler that really allocates addresses ----------


class PointerFake(FakeS1000):
    """FakeS1000 plus the part FakeS1000 leaves out: the sampler assigns the addresses.

    Every program/keygroup gets a memory address; FIRSTKG/NXTKG hold them. A new
    program is placed at the free end; an appended keygroup is placed there too
    and the PREVIOUS keygroup is repointed at it, while the new keygroup keeps
    the NXTKG it was SENT (both measured on a real S1000). A replace (KDATA/PDATA
    on an existing slot) stores the bytes literally, so a wrong pointer sent
    breaks the chain - which is what the import must never do.
    """

    def __init__(self, *, honor_first_pointer=False, **kwargs):
        super().__init__(**kwargs)
        self.honor_first_pointer = honor_first_pointer
        self.sent_pointers = []  # (op, bytes) of every PDATA/KDATA pointer received
        address = 0
        for entry in self.programs:
            entry["addr"] = address
            self._link(entry)
            address += self.block_size * (1 + len(entry["keygroups"]))
        self._free = address

    def _link(self, entry):
        size, base = self.block_size, entry["addr"]
        entry["block"][1:3] = (base + size).to_bytes(2, "little")
        for i, kg in enumerate(entry["keygroups"]):
            kg[1:3] = (base + size * (i + 2)).to_bytes(2, "little")

    def _pdata(self, payload):
        block = m.decode_nibbles(payload[2:])
        self.sent_pointers.append(("PDATA", bytes(block[1:3])))
        before = len(self.programs)
        super()._pdata(payload)
        if len(self.programs) > before:
            entry = self.programs[-1]
            entry["addr"] = self._free
            self._link(entry)
            self._free += self.block_size * (1 + len(entry["keygroups"]))
            if self.honor_first_pointer:
                entry["block"][1:3] = bytes(block[1:3])

    def _kdata(self, payload):
        block = m.decode_nibbles(payload[3:])
        self.sent_pointers.append(("KDATA", bytes(block[1:3])))
        entry = self._program(m.decode_u14(payload[0], payload[1]))
        appending = entry is not None and payload[2] == len(entry["keygroups"])
        super()._kdata(payload)
        if appending:
            entry["keygroups"][payload[2] - 1][1:3] = self._free.to_bytes(2, "little")
            self._free += self.block_size
            self.after_append(entry)

    def after_append(self, entry):
        """Hook for a subclass to misbehave."""


def _worker(fake):
    return BridgeWorker(LoggingBridge(S1000Bridge(fake.bridge(timeout=0.3))))


def _export(fake, index=0):
    worker = _worker(fake)
    got = []
    worker.program_exported.connect(lambda i, f: got.append(f))
    worker.submit_export_program(index)
    worker.process_pending()
    return worker, got[0]


def _import(worker, parsed, name):
    imported, failed = [], []
    worker.program_imported.connect(lambda i, n: imported.append((i, n)))
    worker.program_import_failed.connect(lambda n, e: failed.append(e))
    worker.submit_import_program(parsed, name)
    worker.process_pending()
    return imported, failed


def _reload(original):
    return apf.parse_file(apf.build_file(original.program, original.keygroups))


def _snapshot(fake):
    return [
        (bytes(e["block"]), [bytes(k) for k in e["keygroups"]]) for e in fake.programs
    ]


@pytest.mark.parametrize("size", [150, 192])
def test_export_then_import_recreates_the_program_with_the_samplers_own_pointers(size):
    fake = PointerFake(block_size=size)
    worker, original = _export(fake)
    assert original.name == "DRUMS" and original.block_size == size
    assert len(original.keygroups) == 2
    parsed = _reload(original)  # through the file format (pointers made file-relative)
    others = _snapshot(fake)

    imported, failed = _import(worker, parsed, "DRUMS B")

    assert failed == []
    assert imported == [(2, "DRUMS B")]
    assert fake.deleted_by_name_clash == []
    assert _snapshot(fake)[:2] == others  # the other programs are untouched, pointers included
    copy = fake.programs[2]
    assert len(copy["keygroups"]) == 2
    # the chain is the SAMPLER's: first keygroup right after the program block,
    # keygroup 0 -> keygroup 1, none of it the file's 150/300 stand-ins
    base = copy["addr"]
    assert copy["block"][1:3] == (base + size).to_bytes(2, "little")
    kg0_next = int.from_bytes(copy["keygroups"][0][1:3], "little")
    assert kg0_next not in (150, 300, 450) and kg0_next > base
    # the sequence of pointers sent: only the create carries the file's; every
    # later one (keygroup 0, keygroup 1, the GROUPS=2 PDATA) is what the sampler held
    file_pointers = {parsed.program[1:3], *(k[1:3] for k in parsed.keygroups)}
    sent = [ptr for _op, ptr in fake.sent_pointers]
    assert [op for op, _ in fake.sent_pointers] == ["PDATA", "KDATA", "KDATA", "PDATA"]
    assert sent[0] == parsed.program[1:3]
    assert all(ptr not in file_pointers for ptr in sent[1:])
    assert sent[3] == copy["block"][1:3]  # the update re-sent the sampler's own FIRSTKG
    # everything but pointers/name equals the original
    name = p.lookup("PRNAME", "program")
    keep = lambda blk: bytes(blk[3 : name.offset]) + bytes(blk[name.offset + 12 :])
    assert keep(fake.programs[0]["block"]) == keep(copy["block"])
    assert [bytes(k[3:]) for k in copy["keygroups"]] == [
        bytes(k[3:]) for k in fake.programs[0]["keygroups"]
    ]
    assert fake.ignored_ops == []


def test_import_stops_before_any_keygroup_if_the_sampler_honours_our_pointer():
    # a sampler that stores the incoming FIRSTKG literally would link the new
    # program into program 0 (address 150 is program 0's first keygroup)
    fake = PointerFake(honor_first_pointer=True)
    worker, original = _export(fake)
    others = _snapshot(fake)

    imported, failed = _import(worker, _reload(original), "DRUMS B")

    assert imported == []
    assert "BEFORE any keygroup was written" in failed[0]
    assert _snapshot(fake)[:2] == others
    assert [op for op, _ in fake.sent_pointers] == ["PDATA"]  # no KDATA was ever sent
    # and the session refuses a second attempt
    _imported, failed_again = _import(worker, _reload(original), "DRUMS C")
    assert "disabled for this session" in failed_again[0]
    assert len(fake.programs) == 3  # still just the one empty program from before


def test_import_reports_when_the_load_changed_another_program():
    class Corrupting(PointerFake):
        def after_append(self, entry):
            self.programs[1]["keygroups"][0][40] ^= 0xFF

    fake = Corrupting()
    worker, original = _export(fake)

    imported, failed = _import(worker, _reload(original), "DRUMS B")

    assert imported == []
    assert "program 1 was changed by the load" in failed[0]
    _i, again = _import(worker, _reload(original), "DRUMS C")
    assert "disabled for this session" in again[0]


def test_import_reports_a_keygroup_that_did_not_arrive_intact():
    class Lossy(PointerFake):
        def after_append(self, entry):
            entry["keygroups"][-1][4] ^= 0x01  # HINOTE

    fake = Lossy()
    worker, original = _export(fake)
    _imported, failed = _import(worker, _reload(original), "DRUMS B")
    assert "keygroup 2 differs at bytes [4]" in failed[0]


def test_import_refuses_a_name_that_would_make_the_sampler_delete_a_program():
    fake = PointerFake()
    worker, original = _export(fake)
    before = fake.writes
    imported, failed = _import(worker, _reload(original), "PAD PROG")
    assert imported == [] and "already on the sampler" in failed[0]
    assert fake.writes == before and len(fake.programs) == 2


def test_import_with_a_midway_failure_says_a_partial_program_may_remain():
    class Rejecting(PointerFake):
        def _kdata(self, payload):
            if payload[2] == 1:
                return self._reply_ok(False)
            super()._kdata(payload)

    fake = Rejecting()
    worker, original = _export(fake)
    imported, failed = _import(worker, _reload(original), "DRUMS B")
    assert imported == [] and "partly loaded" in failed[0]


def test_import_defaults_to_the_files_own_name():
    fake = PointerFake()
    worker, original = _export(fake)
    fake.programs[0]["block"][3:15] = bytes(fake.programs[0]["block"][3:15])  # unchanged
    parsed = _reload(original)
    del fake.programs[0]  # make the original's name free
    imported, failed = _import(worker, parsed, None)
    assert failed == [] and imported[0][1] == "DRUMS"


def test_export_reports_a_failure_for_a_missing_program():
    fake = FakeS1000()
    worker = _worker(fake)
    failed = []
    worker.program_export_failed.connect(lambda i, e: failed.append((i, e)))
    worker.submit_export_program(9)
    worker.process_pending()
    assert failed and failed[0][0] == 9


def test_export_refuses_a_block_size_that_is_neither_150_nor_192():
    fake = FakeS1000(block_size=160)
    worker = _worker(fake)
    exported, failed = [], []
    worker.program_exported.connect(lambda *a: exported.append(a))
    worker.program_export_failed.connect(lambda i, e: failed.append(e))
    worker.submit_export_program(0)
    worker.process_pending()
    assert exported == [] and "160-byte" in failed[0]
