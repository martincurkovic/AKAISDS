# tests for core/demo_s950.py - the fake S950 on fake MIDI ports.
# These drive it only through the rtmidi-style ports with frames built by
# core.s950_sysex, the way a real transport would.

import pytest

from core import s950_program as prog
from core import s950_sysex as s
from core.demo_s950 import FakeS950, make_sample

SOX, EOX = 0xF0, 0xF7


def frame(data):
    return [SOX, *data, EOX]


def drain(fake):
    # everything the fake has said, as message bodies (F0/F7 stripped)
    out = []
    while (msg := fake.inp.get_message()) is not None:
        out.append(msg[0][1:-1])
    return out


def send(fake, data):
    fake.out.send_message(frame(data))
    return drain(fake)


@pytest.fixture
def fake():
    return FakeS950()


def test_ports_have_the_rtmidi_surface(fake):
    assert fake.inp.get_message() is None
    fake.out.send_message(frame(s.build_akai_request(s.FUNC_RCAT)))
    message, timestamp = fake.inp.get_message()
    assert message[0] == SOX and message[-1] == EOX
    fake.out.close_port()
    fake.inp.close_port()


def test_catalog_lists_programs_then_samples(fake):
    (reply,) = send(fake, s.build_akai_request(s.FUNC_RCAT))
    message = s.parse_akai(reply)
    assert message.function == s.FUNC_CAT
    entries = s.parse_catalog(message.payload)
    assert [(e.kind, e.num, e.name) for e in entries] == [
        ("P", 0, "DRUMS"),
        ("P", 1, "PAD PROG"),
        ("S", 0, "KICK"),
        ("S", 1, "SNARE"),
        ("S", 2, "PAD"),
    ]


def test_replies_use_the_requests_channel_only():
    fake = FakeS950(channel=5)
    assert send(fake, s.build_akai_request(s.FUNC_RCAT, channel=0)) == []
    (reply,) = send(fake, s.build_akai_request(s.FUNC_RCAT, channel=5))
    assert s.parse_akai(reply).channel == 5


def test_sprm_read_modify_write_round_trip_and_no_reply(fake):
    (reply,) = send(fake, s.build_akai_request(s.FUNC_RSPRM, 1))
    params = s.SampleParams.from_payload(s.parse_akai(reply).payload)
    assert params.name == "SNARE"
    params.name = "SNARE 2"
    params.nominal_pitch = 1000
    # writes get NO answer on a real S950
    assert send(fake, s.build_akai_data(s.FUNC_SPRM, 1, params.to_payload())) == []
    assert fake.sprm_writes == 1
    (reply,) = send(fake, s.build_akai_request(s.FUNC_RSPRM, 1))
    again = s.SampleParams.from_payload(s.parse_akai(reply).payload)
    assert (again.name, again.nominal_pitch) == ("SNARE 2", 1000)


def test_sprm_for_an_empty_slot_is_silent_both_ways(fake):
    assert send(fake, s.build_akai_request(s.FUNC_RSPRM, 50)) == []
    payload = s.SampleParams(name="NOPE").to_payload()
    assert send(fake, s.build_akai_data(s.FUNC_SPRM, 50, payload)) == []
    assert 50 not in fake.samples and fake.sprm_writes == 0


def test_program_read_and_write_round_trip(fake):
    (reply,) = send(fake, s.build_akai_request(s.FUNC_RPRGM, 0))
    message = s.parse_akai(reply)
    program = prog.Program.from_payload(message.payload)
    assert program.name == "DRUMS" and program.num_keygroups == 2
    assert program.keygroups[1].soft_sample == "SNARE"

    program.keygroups[0].attack = 42
    assert send(fake, s.build_akai_data(s.FUNC_PRGM, 0, program.to_payload())) == []
    # writing to a free slot creates a program
    program.name = "COPY"
    send(fake, s.build_akai_data(s.FUNC_PRGM, 7, program.to_payload()))
    assert fake.prgm_writes == 2
    (reply,) = send(fake, s.build_akai_request(s.FUNC_RPRGM, 7))
    copy = prog.Program.from_payload(s.parse_akai(reply).payload)
    assert copy.name == "COPY" and copy.keygroups[0].attack == 42
    assert send(fake, s.build_akai_request(s.FUNC_RPRGM, 9)) == []


def test_corrupt_and_foreign_frames_are_dropped(fake):
    good = s.build_akai_data(s.FUNC_SPRM, 0, s.SampleParams(name="HACKED").to_payload())
    bad_checksum = good[:-1] + [good[-1] ^ 1]
    s1000_style = good[:3] + [0x48] + good[4:]
    assert send(fake, bad_checksum) == []
    assert send(fake, s1000_style) == []
    assert fake.samples[0]["params"].name == "KICK"
    assert len(fake.rejected) == 2 and fake.sprm_writes == 0


def test_unknown_requests_are_silently_ignored_and_recorded(fake):
    for function in (s.FUNC_ROVS, s.FUNC_RDRS, s.FUNC_SECRE):
        assert send(fake, s.build_akai_request(function)) == []
    assert fake.ignored_ops == [s.FUNC_ROVS, s.FUNC_RDRS, s.FUNC_SECRE]
    # plain MIDI (a program change) and fragments are not even recorded
    fake.out.send_message([0xC0, 1])
    fake.out.send_message([SOX, 0x47])
    assert len(fake.received) == 3


def test_standard_sds_framing_means_nothing_to_it(fake):
    # request a dump the standard way (F0 7E cc 03 ss ss F7) and send the
    # 6-byte ACK (F0 7E cc 7F pp F7), both on channel 2
    assert send(fake, [0x7E, 0x02, 0x03, 0x00, 0x00]) == []
    assert send(fake, [0x7E, 0x02, 0x7F, 0x00]) == []
    assert len(fake.ignored_ops) == 2 and fake.acks_received == 0


def test_a_standard_sds_ack_on_channel_0_collides_with_the_s950_dump_request(fake):
    # F0 7E 00 7F 00 F7 is a perfectly good standard ACK (channel 0, packet 0x7F)
    # and ALSO byte-for-byte the S950's own "request sample dump, slot 127".
    # This is the hazard of running Generic SDS against an S950: what comes
    # back is nonsense, not a clean failure. (What a real unit does with it
    # is unmeasured; the fake treats it as the dump request it looks like.)
    assert send(fake, [0x7E, 0x00, 0x7F, 0x00]) == [[0x7E, s.CODE_NAKS]]


# --- a dump the fake SENDS ---------------------------------------------------------


def test_requested_dump_stalls_until_acked_then_arrives_as_one_message(fake):
    assert send(fake, s.build_request_sample_dump(1)) == []  # held back
    blocks = s.num_blocks(len(fake.samples[1]["words"]))
    for _ in range(blocks - 1):
        assert send(fake, s.build_handshake(s.CODE_ACKS)) == []
    (dump,) = send(fake, s.build_handshake(s.CODE_ACKS))
    assert dump[:2] == [0x7E, 0x01]
    header, words = s.parse_sample_dump(dump, expected_slot=1)
    assert words == fake.samples[1]["words"]
    assert header.period_ns == s.hz_to_period_ns(22050)
    # one SysEx for the whole thing: header, every block, a single F7
    assert len(dump) == s.DUMP_HEADER_SIZE + blocks * s.BLOCK_SIZE


def test_a_standard_sds_ack_does_not_release_the_dump(fake):
    send(fake, s.build_request_sample_dump(0))
    for _ in range(100):
        assert send(fake, [0x7E, 0x02, 0x7F, 0x00]) == []
    assert fake.acks_received == 0


def test_abort_cancels_a_stalled_dump(fake):
    send(fake, s.build_request_sample_dump(0))
    send(fake, s.build_handshake(s.CODE_ASD))
    for _ in range(50):
        assert send(fake, s.build_handshake(s.CODE_ACKS)) == []


def test_dump_can_be_released_immediately():
    fake = FakeS950(dump_acks_required=0)
    (dump,) = send(fake, s.build_request_sample_dump(2))
    assert s.parse_sample_dump(dump, expected_slot=2)[0].total_words == 9000


def test_requesting_an_empty_slot_is_naked(fake):
    assert send(fake, s.build_request_sample_dump(60)) == [[0x7E, s.CODE_NAKS]]


def test_a_looped_sample_reports_its_loop_in_the_header():
    fake = FakeS950(dump_acks_required=0)
    sample = make_sample("LOOPY", frames=1000)
    sample["params"].replay_mode = s.REPLAY_LOOP
    sample["params"].end = 900
    sample["params"].loop_length = 200
    fake.samples[3] = sample
    (dump,) = send(fake, s.build_request_sample_dump(3))
    header = s.SampleDumpHeader.from_bytes(dump)
    assert (header.loop_start, header.loop_end, header.mode) == (700, 900, 0)


# --- a dump the fake RECEIVES --------------------------------------------------------


def _upload(fake, slot, words, rate=22050):
    header = s.SampleDumpHeader(
        num=slot,
        period_ns=s.hz_to_period_ns(rate),
        total_words=len(words),
        loop_start=len(words) - 5,
        loop_end=len(words) - 1,
    )
    return s.build_sample_dump(header, words), header


def test_upload_acks_every_block_and_stores_the_sample(fake):
    words = [(i * 11) & 0xFFF for i in range(500)]
    data, _ = _upload(fake, 10, words)
    replies = send(fake, data)
    assert replies == [[0x7E, s.CODE_ACKS]] * s.num_blocks(500)
    assert fake.uploads == [10]
    sample = fake.samples[10]
    assert sample["words"] == words
    assert sample["params"].sample_rate_hz == 22050
    assert sample["params"].replay_mode == s.REPLAY_ONE_SHOT
    assert sample["params"].total_words == 500


def test_upload_shows_up_in_the_catalog_after_the_acks(fake):
    data, _ = _upload(fake, 10, [100] * 300)
    fake.out.send_message(frame(data))
    fake.out.send_message(frame(s.build_akai_request(s.FUNC_RCAT)))
    replies = drain(fake)
    # leftover ACKs from the upload come BEFORE the catalog reply, which is
    # why a ready-check must wait for a real Akai message, not "any reply"
    assert replies[:-1] == [[0x7E, s.CODE_ACKS]] * 5
    entries = s.parse_catalog(s.parse_akai(replies[-1]).payload)
    assert ("S", 10, "SAMPLE 10") in [(e.kind, e.num, e.name) for e in entries]


def test_upload_then_sprm_renames_it(fake):
    data, _ = _upload(fake, 10, [100] * 300)
    send(fake, data)
    params = s.SampleParams.from_payload(fake.samples[10]["params"].to_payload())
    params.name = "MY SAMPLE"
    send(fake, s.build_akai_data(s.FUNC_SPRM, 10, params.to_payload()))
    assert fake.samples[10]["params"].name == "MY SAMPLE"


def test_overwriting_a_slot_keeps_its_stale_loop_values_by_default(fake):
    old = fake.samples[0]["params"]
    old_end = old.end
    data, _ = _upload(fake, 0, [7] * 800)
    send(fake, data)
    params = fake.samples[0]["params"]
    assert params.name == "KICK"  # kept
    assert params.total_words == 800  # updated
    assert params.end == old_end  # stale: a sender has to write SPRM after


def test_stale_values_can_be_turned_off():
    fake = FakeS950(stale_params_on_overwrite=False)
    data, header = _upload(fake, 0, [7] * 800)
    send(fake, data)
    assert fake.samples[0]["params"].end == header.loop_end


def test_a_corrupt_block_is_naked_and_nothing_is_stored(fake):
    data, _ = _upload(fake, 10, [5] * 300)  # 5 blocks
    corrupt = list(data)
    corrupt[s.DUMP_HEADER_SIZE + 2 * s.BLOCK_SIZE + 10] ^= 0x01
    replies = send(fake, corrupt)
    assert replies == [
        [0x7E, s.CODE_ACKS],
        [0x7E, s.CODE_ACKS],
        [0x7E, s.CODE_NAKS],
        [0x7E, s.CODE_ACKS],
        [0x7E, s.CODE_ACKS],
    ]
    assert 10 not in fake.samples and fake.uploads == []


def test_a_truncated_upload_stores_nothing(fake):
    data, _ = _upload(fake, 10, [5] * 300)
    send(fake, data[: s.DUMP_HEADER_SIZE + 3 * s.BLOCK_SIZE])
    assert 10 not in fake.samples


def test_what_the_fake_sends_it_can_also_receive_back(fake):
    # the dump a real unit sends is a valid upload for another one
    send(fake, s.build_request_sample_dump(2))
    dump = None
    for _ in range(s.num_blocks(9000)):
        got = send(fake, s.build_handshake(s.CODE_ACKS))
        dump = got[0] if got else dump
    assert dump is not None
    other = FakeS950(samples={})
    send(other, dump)
    assert other.samples[2]["words"] == fake.samples[2]["words"]


def test_the_header_can_arrive_as_a_message_of_its_own_first():
    fake = FakeS950(dump_acks_required=0, split_header_envelope=True)
    first, second = send(fake, s.build_request_sample_dump(1))
    assert len(first) == s.DUMP_HEADER_SIZE  # header only
    assert s.SampleDumpHeader.from_bytes(first).total_words == 4000
    assert len(second) > s.DUMP_HEADER_SIZE
    assert s.parse_sample_dump(second, expected_slot=1)[1] == fake.samples[1]["words"]
