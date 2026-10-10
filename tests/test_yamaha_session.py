# tests for controller/yamaha_session.py - the Yamaha A4000 conversation engine.
#
# The real YamahaSession and the real SamplerController run against core/demo_a4000.FakeA4000 over the
# same fake MidiManager test_s950_transfers.py uses (replies arrive asynchronously, via the event loop).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication
from controller.sampler_controller import SamplerController
from core import demo_a4000 as demo
from core import yamaha_params as yp
from core import yamaha_sysex as y

from test_s950_transfers import _Midi, qapp, wait_until  # noqa: F401


class Rig:
    pass


def build(fake, model="yamaha_a4000"):
    rig = Rig()
    rig.fake = fake
    rig.midi = _Midi(fake)
    rig.controller = SamplerController(rig.midi)
    rig.controller.set_device_type(model)
    rig.statuses = []
    rig.controller.status_changed.connect(rig.statuses.append)
    if model == "yamaha_a4000":
        rig.session = rig.controller.yamaha_session()
        rig.session.device = fake.device
        rig.session.reply_timeout_ms = 300
        rig.session.bulk_timeout_ms = 400
        rig.session.select_settle_ms = 1
        rig.session.edit_settle_ms = 1
        rig.session.wave_chunk_timeout_ms = 300
        rig.session.drain_idle_ms = 60
    return rig


@pytest.fixture
def rig(qapp):  # noqa: F811
    return build(demo.FakeA4000())


def collect(rig, start):
    got = []
    start(got.append)
    assert wait_until(lambda: bool(got)), "the session never called back"
    return got[0]


# --- reads ------------------------------------------------------------------------------------------


def test_object_list(rig):
    entries = collect(rig, rig.session.request_object_list)
    assert len(entries) == 142
    assert [e.name for e in entries[:2]] == ["001", "002"]
    assert rig.session.idle


def test_program_and_sample_dumps(rig):
    rig.fake.assign(1, "sine wave")
    program = collect(rig, lambda cb: rig.session.request_bulk("PG", "001", cb))
    assert (program.fmt, program.name, len(program.data)) == ("PG", "001", 856)
    assert yp.extract(yp.get("program", "assigned_samples"), program.data) == 1
    sample = collect(rig, lambda cb: rig.session.request_bulk("SP", "saw up", cb))
    assert (sample.fmt, sample.name, len(sample.data)) == ("SP", "saw up", 336)


def test_parameter_reads_select_the_object_first_and_check_the_announcement(rig):
    level = yp.get("program", "program_level")
    pan = yp.get("program", "ad_in_l_pan")
    results = collect(
        rig, lambda cb: rig.session.request_parameters("program", "001", [level.p, pan.p], cb)
    )
    assert [yp.decode_reply(r_p, r.data) for r_p, r in zip((level, pan), results)] == [127, 0]
    assert rig.fake.received[0][0] == "parameter"  # the select went first
    assert results[0].params == level.p


def test_easy_edit_reads_use_the_slot(rig):
    rig.fake.assign(5, "triangle")
    name = yp.get("easy_edit", "assigned_name")
    results = collect(
        rig, lambda cb: rig.session.request_parameters("program", "005", [yp.request_params(name, 0)], cb)
    )
    assert yp.decode_reply(name, results[0].data) == "triangle"


def test_operations_run_one_at_a_time_in_order(rig):
    order = []
    rig.session.request_bulk("PG", "001", lambda d: order.append(("PG", d.name)))
    rig.session.request_bulk("SP", "square", lambda d: order.append(("SP", d.name)))
    rig.session.request_object_list(lambda e: order.append(("OL", len(e))))
    assert not rig.session.idle
    assert wait_until(lambda: len(order) == 3)
    assert order == [("PG", "001"), ("SP", "square"), ("OL", 142)]
    # one request on the wire per operation, in the same order
    assert [k for k, _m in rig.fake.received] == ["dump_request"] * 3
    assert rig.session.idle


# --- failures never hang ------------------------------------------------------------------------------


def test_a_silent_unit_times_out_and_says_what_to_check(qapp):  # noqa: F811
    rig = build(demo.FakeA4000(device_number_off=True))
    assert collect(rig, rig.session.request_object_list) is None
    assert any("Device Number" in s for s in rig.statuses)
    assert rig.session.idle


def test_an_unknown_object_times_out(rig):
    assert collect(rig, lambda cb: rig.session.request_bulk("PG", "200", cb)) is None


def test_parameter_reads_fail_as_none_after_the_first_failure(rig):
    results = collect(
        rig, lambda cb: rig.session.request_parameters("program", "001", [(1, 10, 0, 0, 0, 0), (1, 99, 0, 0, 0, 0), (1, 11, 0, 0, 0, 0)], cb)
    )
    assert results[0] is not None and results[1] is None and results[2] is None
    assert rig.session.idle


def test_a_value_announced_for_another_object_is_refused(qapp):  # noqa: F811
    class Confused(demo.FakeA4000):
        def _param_request(self, m):
            self.current = (y.OBJECT_TYPES["program"], 2)  # answer from program 2 although 001 was selected
            super()._param_request(m)

    rig = build(Confused())
    results = collect(rig, lambda cb: rig.session.request_parameters("program", "001", [(1, 10, 0, 0, 0, 0)], cb))
    assert results == [None]
    assert any("different object" in s for s in rig.statuses)


def test_a_failing_send_fails_the_operation_instead_of_hanging(rig):
    rig.midi.fail_sends = True
    assert collect(rig, rig.session.request_object_list) is None
    assert any("Couldn't send" in s for s in rig.statuses)


def test_cancel_fails_everything_pending(rig):
    got = []
    rig.session.request_bulk("PG", "001", got.append)
    rig.session.request_bulk("PG", "002", got.append)
    rig.session.cancel()
    assert got == [None, None] and rig.session.idle


# --- stale and duplicated messages ----------------------------------------------------------------------------


def test_every_message_delivered_twice_still_reads_the_right_values(qapp):  # noqa: F811
    # the bug that bit the real smoke test: a duplicated reply for program 1 was taken as the answer to program 2
    class Twice(_Midi):
        def _deliver(self):
            while (got := self.fake.inp.get_message()) is not None:
                data = bytes(got[0][1:-1])
                self.sysex_received.emit(data)
                self.sysex_received.emit(data)

    fake = demo.FakeA4000()
    fake.assign(1, "sine wave")
    rig = build(fake)
    rig.midi.__class__ = Twice
    level = yp.get("program", "assigned_samples")
    counts = {}
    for n in (1, 2, 3):
        rig.session.request_parameters(
            "program", f"{n:03d}", [level.p], lambda r, n=n: counts.__setitem__(n, yp.decode_reply(level, r[0].data))
        )
    assert wait_until(lambda: len(counts) == 3)
    assert counts == {1: 1, 2: 0, 3: 0}


def test_a_value_with_no_announce_is_not_accepted(qapp):  # noqa: F811
    class Silent(demo.FakeA4000):
        def _param_request(self, m):
            found = self._payload_and_row(tuple(m[4:10]))
            data, row, slot = found
            self._say(y.build_object_edit(self.device, tuple(m[4:10]), self._value_bytes(row, data, slot)))  # no announce

    rig = build(Silent())
    got = collect(rig, lambda cb: rig.session.request_parameters("program", "001", [(1, 10, 0, 0, 0, 0)], cb))
    assert got == [None]  # refused, then timed out


# --- routing through the controller -----------------------------------------------------------------------


def test_yamaha_traffic_never_reaches_the_generic_parser(rig):
    rig.controller.on_sysex_received(y.build_object_select(0, "001", "program"))  # an unsolicited announce
    assert not any("unrecognised" in s for s in rig.statuses)


def test_the_session_only_exists_for_the_yamaha_model(qapp):  # noqa: F811
    other = build(demo.FakeA4000(), model="generic")
    with pytest.raises(RuntimeError):
        other.controller.yamaha_session()
    # and a stray 0x43 message there is still reported as unrecognised, exactly as before
    other.controller.on_sysex_received(y.build_object_select(0, "001", "program"))
    assert any("unrecognised" in s for s in other.statuses)


def test_yamaha_transfers_use_the_generic_family(rig):
    assert rig.controller.device_type == "generic" and rig.controller.is_yamaha_model()
    assert rig.controller.is_yamaha_idle()


# --- writes ---------------------------------------------------------------------------------------------------


def write(rig, row_scope, key, value, name, slot=None):
    row = yp.get(row_scope, key)
    return collect(rig, lambda cb: rig.session.write_parameter(row, value, name, cb, slot=slot))


def test_a_write_backs_up_the_object_first_then_changes_exactly_that_value(rig):
    before = bytes(rig.fake.programs[1])
    result = write(rig, "program", "program_level", 50, "001")
    assert result.ok and (result.previous, result.readback, result.requested) == (127, 50, 50)
    assert result.edit_sent and result.backup_path is not None and result.backup_path.exists()
    # the backup is the object as it was BEFORE the write, as a loadable .syx
    saved = y.parse_bulk_dump(y.split_messages(result.backup_path.read_bytes())[0])
    assert (saved.fmt, saved.name, bytes(saved.data)) == ("PG", "001", before)
    # wire order: the dump request (backup), then select, probe, edit, read-back
    assert [k for k, _m in rig.fake.received] == [
        "dump_request", "parameter", "parameter_request", "parameter", "parameter_request",
    ]
    assert yp.extract(yp.get("program", "program_level"), rig.fake.programs[1]) == 50
    assert rig.fake.edits == 1 and rig.session.idle


def test_only_the_first_write_to_an_object_makes_a_backup(rig):
    first = write(rig, "program", "program_level", 50, "001")
    second = write(rig, "program", "program_level", 60, "001")
    assert second.ok and second.backup_path == first.backup_path  # the ORIGINAL is what stays saved
    assert [k for k, _m in rig.fake.received].count("dump_request") == 1
    other = write(rig, "program", "program_level", 60, "002")
    assert other.backup_path != first.backup_path  # another object is backed up on its own
    assert [k for k, _m in rig.fake.received].count("dump_request") == 2


def test_easy_edit_and_sample_writes_go_to_the_right_object_and_slot(rig):
    rig.fake.assign(1, "sine wave")
    rig.fake.assign(1, "saw up")
    result = write(rig, "easy_edit", "level_offset", 20, "001", slot=1)
    assert result.ok and result.previous == 0
    assert yp.extract(yp.get("easy_edit", "level_offset"), rig.fake.programs[1], 1) == 20
    assert yp.extract(yp.get("easy_edit", "level_offset"), rig.fake.programs[1], 0) == 0
    result = write(rig, "sample", "filter_cutoff", 77, "saw up")
    assert result.ok and yp.extract(yp.get("sample", "filter_cutoff"), rig.fake.samples["saw up"]) == 77
    assert yp.extract(yp.get("sample", "filter_cutoff"), rig.fake.samples["sine wave"]) != 77


def test_signed_and_bitfield_values_round_trip(rig):
    assert write(rig, "program", "transpose", -12, "001").ok
    assert yp.extract(yp.get("program", "transpose"), rig.fake.programs[1]) == -12
    assert write(rig, "program", "lfo_cycle", 5, "001").ok  # a 3-bit field inside a shared byte
    assert yp.extract(yp.get("program", "lfo_cycle"), rig.fake.programs[1]) == 5
    assert yp.extract(yp.get("program", "lfo_wave"), rig.fake.programs[1]) == 0  # its neighbour is untouched


def test_a_write_the_unit_does_not_take_is_reported_with_what_it_holds(qapp):  # noqa: F811
    rig = build(demo.FakeA4000(bulk_protect=True))
    result = write(rig, "program", "program_level", 50, "001")
    assert not result.ok and result.edit_sent and result.readback == 127
    assert "Bulk Protect" in result.message
    assert result.backup_path is not None  # the backup was still made first


def test_nothing_is_written_when_the_backup_cannot_be_saved(rig, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    rig.session.backup_dir = blocker / "backups"  # a directory can't be made under a file
    result = write(rig, "program", "program_level", 50, "001")
    assert not result.ok and not result.edit_sent
    assert rig.fake.edits == 0 and yp.extract(yp.get("program", "program_level"), rig.fake.programs[1]) == 127
    assert any("backup" in s for s in rig.statuses)
    assert rig.session.idle


def test_nothing_is_written_when_the_unit_never_answers_for_the_object(rig):
    result = write(rig, "program", "program_level", 50, "200")  # no such program: the select is ignored, no reply
    assert not result.ok and not result.edit_sent
    assert rig.fake.edits == 0


def test_a_lost_select_never_lets_an_edit_land_on_the_previous_object(rig):
    collect(rig, lambda cb: rig.session.request_parameters("program", "002", [(1, 10, 0, 0, 0, 0)], cb))
    rig.fake.drop_selects = True  # the unit keeps program 2 selected although 1 was asked for
    result = write(rig, "program", "program_level", 50, "001")
    assert not result.ok and not result.edit_sent
    assert rig.fake.edits == 0
    assert yp.extract(yp.get("program", "program_level"), rig.fake.programs[2]) == 127


@pytest.mark.parametrize(
    "scope,key,value,slot,fragment",
    [
        ("program", "program_level", 200, None, "range"),
        ("program", "program_name", 1, None, "can't be written"),
        ("program", "assigned_samples", 3, None, "can't be written"),
        ("program", "effect456_connection", 1, None, "A5000"),
        ("sample", "sampling_frequency_l", 44100, None, "not changed"),
        ("easy_edit", "level_offset", 5, None, "slot"),
    ],
)
def test_rows_and_values_that_must_not_be_written_are_refused_without_touching_the_unit(rig, scope, key, value, slot, fragment):
    result = write(rig, scope, key, value, "001", slot=slot)
    assert not result.ok and not result.edit_sent and fragment in result.message
    assert rig.fake.received == []


def test_a_queued_write_to_the_same_row_is_updated_instead_of_repeated(rig):
    row = yp.get("program", "program_level")
    got = []
    # the first write starts at once (backup dump goes out); the next two wait in the queue and merge
    rig.session.write_parameter(row, 10, "001", got.append)
    rig.session.write_parameter(row, 20, "001", got.append)
    rig.session.write_parameter(row, 30, "001", got.append)
    assert wait_until(lambda: len(got) == 3)
    assert [r.requested for r in got] == [10, 30, 30]
    assert all(r.ok for r in got)
    assert yp.extract(row, rig.fake.programs[1]) == 30
    assert rig.fake.edits == 2


def test_writes_wait_for_a_sample_transfer_like_every_other_operation(rig):
    rig.controller._receiving = True  # a Sample Dump transfer owns the wire
    got = []
    rig.session.busy_retry_ms = 5
    rig.session.write_parameter(yp.get("program", "program_level"), 50, "001", got.append)
    assert not wait_until(lambda: bool(got), timeout=0.15) and rig.fake.received == []
    rig.controller._receiving = False
    assert wait_until(lambda: bool(got)) and got[0].ok


def test_cancel_reports_a_pending_write_as_not_written(rig):
    got = []
    rig.session.write_parameter(yp.get("program", "program_level"), 50, "001", got.append)
    assert rig.session.writes_pending
    rig.session.cancel()
    assert len(got) == 1 and not got[0].ok and not rig.session.writes_pending


# --- waves (the native WD dump) -----------------------------------------------------------------------------------


def frames_of(n, seed=0):
    return [((i * 37 + seed) % 30000) - 15000 for i in range(n)]


def fetch_wave(rig, name, on_chunk=None):
    return collect(rig, lambda cb: rig.session.request_wave(name, cb, on_chunk=on_chunk))


def test_a_wave_is_assembled_from_its_messages_and_reported_chunk_by_chunk(rig):
    audio = frames_of(5000)
    rig.fake.add_sample("big", audio=audio)
    chunks = []
    frames = fetch_wave(rig, "big", on_chunk=lambda new, total: chunks.append((len(new), total)))
    assert frames == audio
    assert len(chunks) >= 3 and all(total == 5000 for _n, total in chunks)
    assert sum(n for n, _t in chunks) == 5000
    assert rig.session.idle  # a finished wave leaves nothing to drain


def test_a_factory_wave_and_a_tiny_one(rig):
    assert len(fetch_wave(rig, "sine wave")) == 128
    rig.fake.add_sample("tiny", audio=[1, 2, 3])
    assert fetch_wave(rig, "tiny") == [1, 2, 3]


def test_a_stereo_sample_has_two_wave_objects_each_fetched_by_its_own_name(rig):
    left, right = frames_of(3000), frames_of(3000, seed=500)
    rig.fake.add_sample("st", audio=left, audio_right=right)
    names = [e.name for e in collect(rig, rig.session.request_object_list) if e.kind == "wave"]
    assert "st-L" in names and "st-R" in names
    assert fetch_wave(rig, "st-L") == left and fetch_wave(rig, "st-R") == right


def test_an_unknown_wave_times_out_without_draining(rig):
    assert fetch_wave(rig, "no such wave") is None
    assert rig.session.idle  # nothing was streaming, so there is nothing to wait out


def test_a_wave_that_stops_mid_stream_fails_and_the_wire_is_then_drained(qapp):  # noqa: F811
    class Stalls(demo.FakeA4000):
        def _say(self, message):
            if len(self._outbox) >= 2 and message[:4] == bytes([0x43, 0x00, 0x7A, 0x1F]):
                return  # drop everything after the first two messages
            super()._say(message)

    fake = Stalls()
    fake.add_sample("big", audio=frames_of(9000))
    rig = build(fake)
    assert fetch_wave(rig, "big") is None
    assert any("Yamaha" in s or "No reply" in s for s in rig.statuses)


def test_a_corrupt_wave_message_fails_the_operation(qapp):  # noqa: F811
    class Corrupt(demo.FakeA4000):
        def _say(self, message):
            if message[:3] == bytes([0x43, 0x00, 0x7A]) and message[26:28] != b"":
                message = bytes(message[:-3]) + bytes([message[-3] ^ 0x01]) + bytes(message[-2:])
            super()._say(message)

    fake = Corrupt()
    fake.add_sample("big", audio=frames_of(3000))
    rig = build(fake)
    assert fetch_wave(rig, "big") is None
    assert any("corrupt" in s for s in rig.statuses)


def test_cancelling_a_wave_drains_the_stream_before_the_next_request_runs(qapp):  # noqa: F811
    fake = demo.FakeA4000()
    fake.add_sample("big", audio=frames_of(20000))  # ~10 messages
    rig = build(fake)
    rig.midi.paced = True
    seen, order = [], []
    done = []
    rig.session.request_wave("big", done.append, on_chunk=lambda new, total: seen.append(len(new)))
    assert wait_until(lambda: len(seen) >= 2)
    rig.session.cancel()
    assert done == [None] and not rig.session.idle  # draining: the unit is still streaming at us
    after = len(seen)
    rig.session.request_object_list(lambda entries: order.append(("list", entries is not None)))
    assert wait_until(lambda: bool(order), timeout=10)
    assert len(seen) == after  # the abandoned wave's late messages never reached the callback
    assert order == [("list", True)]  # and the next request got ITS answer, not the wave's tail
    assert rig.session.idle


def test_a_cancel_does_not_make_the_session_forget_its_backups(rig):
    first = write(rig, "program", "program_level", 50, "001")
    rig.session.cancel()
    second = write(rig, "program", "program_level", 60, "001")
    assert second.backup_path == first.backup_path  # the ORIGINAL stays the backup after a cancel/refresh
    assert [k for k, _m in rig.fake.received].count("dump_request") == 1


# --- assigning samples to programs (object link change; measured on the real unit) -----------------------------------------


def link(rig, program, sample, on):
    return collect(rig, lambda cb: rig.session.change_link(program, sample, on, cb))


def slots_of(fake, number):
    data = fake.programs[number]
    count = yp.extract(yp.get("program", "assigned_samples"), data)
    return [(yp.extract(yp.get("easy_edit", "assigned_name"), data, s), yp.extract(yp.get("easy_edit", "level_offset"), data, s)) for s in range(count)]


def test_a_sample_is_appended_as_the_next_slot_with_default_values_and_the_unit_confirms_it(rig):
    result = link(rig, "128", "pulse 3", True)
    assert result.ok and result.linked is True and result.requested is True
    assert result.message == "Assigned 'pulse 3' to program 128"
    data = rig.fake.programs[128]
    assert slots_of(rig.fake, 128) == [("pulse 3", 0)]
    assert yp.extract(yp.get("easy_edit", "assigned_type"), data, 0) == 16  # a sample
    assert yp.extract(yp.get("easy_edit", "receive_channel"), data, 0) == -1  # "=sample", as the real unit left it (MEASURED)
    sample = rig.fake.samples["pulse 3"]
    assert yp.linked_programs(sample) == [128]  # and the sample knows which programs use it
    block = data[yp.PROGRAM_EASY_EDIT_BASE : yp.PROGRAM_EASY_EDIT_BASE + 20]
    assert bytes(block[16:20]) == (int.from_bytes(sample[60:64], "big") + 0x18).to_bytes(4, "big")  # the id the unit writes


def test_the_first_change_to_a_program_backs_it_up_and_later_ones_do_not(rig):
    before = bytes(rig.fake.programs[128])
    first = link(rig, "128", "pulse 1", True)
    assert first.backup_path is not None and first.backup_path.exists()
    saved = y.parse_bulk_dump(y.split_messages(first.backup_path.read_bytes())[0])
    assert bytes(saved.data) == before and saved.name == "128"  # the program as it was BEFORE the change
    second = link(rig, "128", "pulse 2", True)
    assert second.backup_path == first.backup_path
    assert [k for k, _m in rig.fake.received].count("dump_request") == 1


def test_samples_are_added_in_order_and_removing_a_middle_one_closes_the_gap(rig):
    for name in ("pulse 1", "pulse 2", "pulse 3"):
        assert link(rig, "128", name, True).ok
    for slot, level in enumerate((11, 22, 33)):
        assert write(rig, "easy_edit", "level_offset", level, "128", slot=slot).ok
    assert link(rig, "128", "pulse 2", False).ok
    # the unit compacts: later slots shift down and KEEP their values (measured)
    assert slots_of(rig.fake, 128) == [("pulse 1", 11), ("pulse 3", 33)]
    assert yp.linked_programs(rig.fake.samples["pulse 2"]) == []
    assert link(rig, "128", "pulse 2", True).ok  # taking it again appends it at the end with defaults
    assert slots_of(rig.fake, 128) == [("pulse 1", 11), ("pulse 3", 33), ("pulse 2", 0)]


def test_linking_twice_changes_nothing_and_a_sample_can_serve_two_programs(rig):
    assert link(rig, "128", "pulse 1", True).ok
    edits = list(rig.fake.programs[128])
    assert link(rig, "128", "pulse 1", True).ok and list(rig.fake.programs[128]) == edits
    assert link(rig, "127", "pulse 1", True).ok
    assert yp.linked_programs(rig.fake.samples["pulse 1"]) == [127, 128]


def test_a_sample_the_unit_does_not_have_is_not_confirmed(rig):
    result = link(rig, "128", "no such sample", True)
    assert not result.ok and result.linked is None and "didn't confirm" in result.message
    assert slots_of(rig.fake, 128) == []


def test_removing_what_is_not_assigned_is_confirmed_as_already_so(rig):
    result = link(rig, "128", "pulse 1", False)
    assert result.ok and result.linked is False and "Removed" in result.message


def test_bulk_protect_makes_the_unit_ignore_the_link_and_the_result_says_so(qapp):  # noqa: F811
    rig = build(demo.FakeA4000(bulk_protect=True))
    result = link(rig, "128", "pulse 1", True)
    assert not result.ok and result.linked is False and "didn't assign" in result.message
    assert yp.extract(yp.get("program", "assigned_samples"), rig.fake.programs[128]) == 0


def test_a_link_in_the_queue_counts_as_a_pending_write(rig):
    got = []
    rig.session.change_link("128", "pulse 1", True, got.append)
    assert rig.session.writes_pending
    assert wait_until(lambda: bool(got)) and not rig.session.writes_pending


def test_a_link_waits_for_a_sample_transfer_like_every_other_operation(rig):
    rig.controller._receiving = True
    rig.session.busy_retry_ms = 5
    got = []
    rig.session.change_link("128", "pulse 1", True, got.append)
    assert not wait_until(lambda: bool(got), timeout=0.15) and rig.fake.received == []
    rig.controller._receiving = False
    assert wait_until(lambda: bool(got)) and got[0].ok


# --- sending bulk messages (a native sample load) -------------------------------------------------------------------


def test_send_messages_sends_each_in_order_paced_and_reports_progress(rig):
    rig.session.send_baud, rig.session.send_gap_ms, rig.session.send_tick_ms = 10_000_000, 0, 5
    messages = [bytes([0x43, 0x00, 0x7A, 1, 2, 3]), bytes([0x43, 0x00, 0x7A, 4, 5]), bytes([0x43, 0x00, 0x7A, 6])]
    progress = []
    assert collect(rig, lambda cb: rig.session.send_messages(messages, cb, on_progress=lambda s, t: progress.append((s, t)))) is True
    assert [bytes(m) for m in rig.midi.sent] == messages
    total = sum(len(m) + 2 for m in messages)
    assert progress[0] == (0, total) and progress[-1] == (total, total) and [p[0] for p in progress] == sorted(p[0] for p in progress)


def test_a_request_made_during_a_send_waits_for_it_and_cancel_stops_it(rig):
    rig.session.send_baud, rig.session.send_gap_ms = 100_000, 20  # each message ~ 25 ms
    done, bulk = [], []
    rig.session.send_messages([bytes([0x43, 0x00, 0x7A, i]) + bytes(2000) for i in range(6)], done.append)
    rig.session.request_bulk("SP", "sine wave", bulk.append)
    assert rig.session.writes_pending and not bulk  # a load counts as a write: a window must not close under it
    assert wait_until(lambda: bool(bulk), timeout=10)
    assert done == [True]  # the request was only sent once the whole load had gone out
    rig.session.send_messages([bytes([0x43, 0x00, 0x7A, 9]) + bytes(2000) for _ in range(40)], done.append)
    assert wait_until(lambda: len(rig.midi.sent) > 7)
    rig.session.cancel()
    sent = len(rig.midi.sent)
    assert done[-1] is False and rig.session.idle
    QCoreApplication.processEvents()
    assert len(rig.midi.sent) == sent


# --- logging: what a tester's log must show (operation lifecycle, ignored strays) -------------------------


def _messages(caplog):
    return [r.getMessage() for r in caplog.records]


def test_a_bulk_dump_logs_its_start_and_finish_with_a_duration(rig, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="akaisds")
    collect(rig, lambda cb: rig.session.request_bulk("PG", "001", cb))
    lines = _messages(caplog)
    assert any(line == "YamahaSession: start bulk dump PG '001'" for line in lines)
    done = [line for line in lines if line.startswith("YamahaSession: bulk dump PG '001' done in ")]
    assert done and done[0].endswith(" ms")


def test_a_parameter_read_is_logged_at_debug_not_info(rig, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="akaisds")
    level = yp.get("program", "program_level")
    collect(rig, lambda cb: rig.session.request_parameters("program", "001", [level.p], cb))
    starts = [r for r in caplog.records if r.getMessage().startswith("YamahaSession: start read 1 parameter(s) of '001'")]
    assert starts and all(r.levelno == logging.DEBUG for r in starts)


def test_a_failed_operation_is_a_warning_with_the_reason(rig, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="akaisds")
    # program "200" does not exist: the select gets no announce, so the read times out and fails
    collect(rig, lambda cb: rig.session.request_parameters("program", "200", [(1, 10, 0, 0, 0, 0)], cb))
    failed = [r for r in caplog.records if "FAILED in" in r.getMessage() and "read 1 parameter(s) of '200'" in r.getMessage()]
    assert failed and failed[0].levelno == logging.WARNING


def test_unsolicited_messages_while_idle_are_counted_and_warn_once(rig, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="akaisds")
    assert rig.session.idle
    for _ in range(25):
        rig.session.handle_sysex(b"\x43\x73\x01\x02")  # something the session never asked for
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "unsolicited messages while idle" in r.getMessage()]
    assert len(warnings) == 1  # at the 10th, once
    assert "another MIDI program" in warnings[0].getMessage()
    debug = [r for r in caplog.records if "unsolicited" in r.getMessage() and "while idle (#" in r.getMessage()]
    assert len(debug) == 25


def test_a_message_that_belongs_to_the_operation_is_not_counted_as_unsolicited(rig, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="akaisds")
    collect(rig, lambda cb: rig.session.request_bulk("PG", "001", cb))
    assert not [r for r in caplog.records if "while idle (#" in r.getMessage()]
