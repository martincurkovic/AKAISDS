# S950Transfers.write_sample_params: changing a sample's replay mode / direction. The real engine and controller run against core/demo_s950.FakeS950, like
# test_s950_transfers.py, whose rig this reuses. The unit does not answer an SPRM write, so the read-back is the evidence; the backup is the original SPRM message.

import pytest

from core import s950_sysex as s
from controller import s950_transfers
from test_s950_transfers import qapp, rig, wait_until  # noqa: F401


class _Written:
    def __init__(self, controller):
        self.results = []
        controller.s950_sample_written.connect(lambda slot, ok, params, message: self.results.append((slot, ok, params, message)))


@pytest.fixture
def wrig(rig, tmp_path):  # noqa: F811
    rig.engine.backup_dir = tmp_path / "backups"
    rig.written = _Written(rig.controller)
    return rig


def stored(rig, slot=0):
    return rig.fake.samples[slot]["params"]


def test_changing_the_replay_mode_writes_it_verifies_and_backs_up_the_original(wrig):
    original = stored(wrig).to_payload()
    wrig.controller.write_s950_sample_params(0, {"replay_mode": s.REPLAY_LOOP})
    assert wait_until(lambda: wrig.written.results)
    slot, ok, held, message = wrig.written.results[0]
    assert (slot, ok) == (0, True) and "verified" in message and "ENT" in message
    assert held.replay_mode == s.REPLAY_LOOP and stored(wrig).replay_mode == s.REPLAY_LOOP
    assert wrig.fake.sprm_writes == 1
    # read, write, read back - in that order
    assert [m[2] for m in wrig.midi.sent] == [s.FUNC_RSPRM, s.FUNC_SPRM, s.FUNC_RSPRM]
    backups = list(wrig.engine.backup_dir.glob("sample00-KICK-*.syx"))
    assert len(backups) == 1
    raw = backups[0].read_bytes()
    assert raw[0] == 0xF0 and raw[-1] == 0xF7 and s.parse_akai(raw[1:-1]).payload == original  # the exact original SPRM
    assert wrig.engine.idle


def test_changing_the_direction_writes_only_that_flag(wrig):
    wrig.controller.write_s950_sample_params(1, {"reversed": s.PLAY_REVERSED})
    assert wait_until(lambda: wrig.written.results)
    assert wrig.written.results[0][1] is True
    assert stored(wrig, 1).reversed == s.PLAY_REVERSED and stored(wrig, 1).replay_mode == s.REPLAY_ONE_SHOT


def test_both_can_change_in_one_write(wrig):
    wrig.controller.write_s950_sample_params(0, {"replay_mode": s.REPLAY_ALTERNATING, "reversed": s.PLAY_REVERSED})
    assert wait_until(lambda: wrig.written.results)
    assert wrig.written.results[0][1] is True
    assert (stored(wrig).replay_mode, stored(wrig).reversed) == (s.REPLAY_ALTERNATING, s.PLAY_REVERSED)
    assert wrig.fake.sprm_writes == 1  # one write, not one per field


def test_every_other_byte_goes_back_exactly_as_read(wrig):
    # the unit keeps bytes this app does not model (several "undefined" S900 fields are used by the S950): they must survive
    payload = bytearray(stored(wrig).to_payload())
    payload[100:110] = bytes(range(1, 11))
    wrig.fake.samples[0]["params"] = s.SampleParams.from_payload(bytes(payload))
    before = wrig.fake.samples[0]["params"].to_payload()
    wrig.controller.write_s950_sample_params(0, {"replay_mode": s.REPLAY_LOOP})
    assert wait_until(lambda: wrig.written.results)
    after = stored(wrig).to_payload()
    differing = [i for i in range(len(before)) if before[i] != after[i]]
    assert differing and all(52 <= i <= 53 for i in differing)  # only the replay-mode DB (two wire bytes)
    assert after[100:110] == bytes(range(1, 11))


def test_other_samples_are_untouched(wrig):
    others = {n: stored(wrig, n).to_payload() for n in (1, 2)}
    wrig.controller.write_s950_sample_params(0, {"reversed": s.PLAY_REVERSED})
    assert wait_until(lambda: wrig.written.results)
    assert {n: stored(wrig, n).to_payload() for n in (1, 2)} == others


def test_asking_for_what_the_sample_already_holds_writes_nothing(wrig):
    wrig.controller.write_s950_sample_params(0, {"replay_mode": s.REPLAY_ONE_SHOT, "reversed": s.PLAY_FORWARD})
    assert wait_until(lambda: wrig.written.results)
    slot, ok, held, message = wrig.written.results[0]
    assert ok is True and "nothing written" in message
    assert wrig.fake.sprm_writes == 0 and not wrig.engine.backup_dir.exists()
    assert [m[2] for m in wrig.midi.sent] == [s.FUNC_RSPRM]


@pytest.mark.parametrize(
    "changes, fragment",
    [
        ({"replay_mode": ord("X")}, "replay mode"),
        ({"reversed": 0}, "direction"),
        ({"name": "BAD"}, "can't change name"),
        ({"start": 5}, "can't change start"),
        ({}, "nothing to change"),
    ],
)
def test_anything_else_is_refused_before_a_byte_is_sent(wrig, changes, fragment):
    wrig.controller.write_s950_sample_params(0, changes)
    slot, ok, held, message = wrig.written.results[0]
    assert ok is False and held is None and fragment in message and message.startswith("Not written")
    assert wrig.midi.sent == [] and wrig.fake.sprm_writes == 0


def test_a_read_back_that_disagrees_is_reported_with_the_backup(wrig):
    wrig.fake._sprm = lambda slot, payload: None  # the unit acknowledges nothing and stores nothing
    wrig.controller.write_s950_sample_params(0, {"replay_mode": s.REPLAY_LOOP})
    assert wait_until(lambda: wrig.written.results)
    slot, ok, held, message = wrig.written.results[0]
    assert ok is False and held.replay_mode == s.REPLAY_ONE_SHOT  # what the sampler reports is returned
    assert "different values" in message and "replay_mode" in message and "s950_backups" in message


def test_no_backup_no_write(wrig, tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    wrig.engine.backup_dir = blocker / "inside"  # cannot be created
    wrig.controller.write_s950_sample_params(0, {"replay_mode": s.REPLAY_LOOP})
    assert wait_until(lambda: wrig.written.results)
    slot, ok, held, message = wrig.written.results[0]
    assert ok is False and "couldn't save a backup" in message and "nothing was changed" in message
    assert wrig.fake.sprm_writes == 0 and stored(wrig).replay_mode == s.REPLAY_ONE_SHOT
    assert wrig.engine.idle


def test_a_nak_during_the_write_fails_it_and_names_the_backup(wrig):
    def nak_then_store(slot, payload):
        wrig.fake.samples[slot]["params"] = s.SampleParams.from_payload(payload)
        wrig.fake._send(s.build_handshake(s.CODE_NAKS))

    wrig.fake._sprm = nak_then_store
    wrig.controller.write_s950_sample_params(0, {"replay_mode": s.REPLAY_LOOP})
    assert wait_until(lambda: wrig.written.results)
    slot, ok, held, message = wrig.written.results[0]
    assert ok is False and "NAK" in message and "s950_backups" in message
    assert wrig.engine.idle


def test_an_empty_slot_gets_no_answer_and_nothing_is_written(wrig):
    wrig.engine.reply_timeout_ms = 60
    wrig.controller.write_s950_sample_params(77, {"replay_mode": s.REPLAY_LOOP})
    assert wait_until(lambda: wrig.written.results)
    slot, ok, held, message = wrig.written.results[0]
    assert (slot, ok, held) == (77, False, None)
    assert wrig.fake.sprm_writes == 0 and wrig.engine.idle


def test_a_busy_engine_refuses_politely(wrig):
    wrig.engine.refresh_catalog()  # an operation in flight
    wrig.controller.write_s950_sample_params(0, {"replay_mode": s.REPLAY_LOOP})
    slot, ok, held, message = wrig.written.results[0]
    assert ok is False and "in progress" in message
    assert wait_until(lambda: wrig.engine.idle)
    assert wrig.fake.sprm_writes == 0


def test_the_validator_accepts_exactly_the_wire_letters():
    assert s950_transfers.sample_write_problems({"replay_mode": s.REPLAY_LOOP, "reversed": s.PLAY_REVERSED}) == []
    assert s950_transfers.sample_write_problems({"replay_mode": 3}) and s950_transfers.sample_write_problems({"reversed": 1})
