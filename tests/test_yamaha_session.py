# tests for controller/yamaha_session.py - the Yamaha A4000 conversation engine.
#
# The real YamahaSession and the real SamplerController run against core/demo_a4000.FakeA4000 over the
# same fake MidiManager test_s950_transfers.py uses (replies arrive asynchronously, via the event loop).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

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
