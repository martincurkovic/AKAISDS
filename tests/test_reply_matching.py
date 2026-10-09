# tests for core/reply_matching.py - the real s3k.bridge.S3kBridge driven over scripted fake ports that interleave STRAY frames with the real answers (what the
# user's S2000 did on 2026-10-10: a REPLY OK next to nearly every reply, and late data frames for earlier requests).

import pytest

import s3k.messages as m
from s3k.bridge import DeviceError, S3kBridge

from core import program_editor_bridge, reply_matching
from core.reply_matching import make_reply_tolerant


def ok():
    return m.Reply(code=0).encode()


def error():
    return m.Reply(code=1).encode()


def misc_data(index, value, bank=1):
    return m.HeaderData(command=m.Command.MISCDATA, index=index, selector=bank, offset=0, data=bytes([value])).encode()


class ScriptedPorts:
    """One object playing both the MIDI out and in ports. Each request sent gets the next scripted batch of frames, which `get_message` then returns in order."""

    def __init__(self, *batches):
        self.batches = list(batches)
        self.queue = []
        self.sent = []

    def send_message(self, message, write=False):
        self.sent.append(bytes(message))
        if self.batches:
            self.queue.extend(self.batches.pop(0))

    def get_message(self):
        if not self.queue:
            return None
        return (list(self.queue.pop(0)), 0.0)

    def close_port(self):
        pass


def bridge_with(*batches, tolerant=True, timeout=0.3):
    ports = ScriptedPorts(*batches)
    bridge = S3kBridge(ports, ports, "scripted", timeout=timeout)
    if tolerant:
        make_reply_tolerant(bridge)
    return bridge, ports


def plist(*names):
    return m.build_frame(m.Command.PLIST, [*m.encode_u14(len(names)), *[b for n in names for b in m.encode_name(n)]])


def test_a_stray_ok_before_the_data_is_skipped_for_a_read():
    bridge, _ = bridge_with([ok(), plist("TEST PROGRAM")])
    assert bridge.program_list() == ["TEST PROGRAM"]
    assert bridge.skipped_replies == 1


def test_without_the_layer_the_same_stray_ok_breaks_the_read():
    # the failure the user saw ("expected command 0x03, got 0x16")
    bridge, _ = bridge_with([ok(), plist("TEST PROGRAM")], tolerant=False)
    with pytest.raises(ValueError):
        bridge.program_list()


def test_a_stray_ok_arriving_after_the_data_does_not_poison_the_next_read():
    # the OK that follows request 1's answer is already in the input when request 2 is sent: the pre-send drain would normally catch it, but one that
    # lands just after the drain must still be skipped
    bridge, ports = bridge_with([plist("A")], [ok(), plist("A", "B")])
    assert bridge.program_list() == ["A"]
    assert bridge.program_list() == ["A", "B"]


def test_a_late_data_frame_for_another_item_is_skipped():
    bridge, _ = bridge_with([misc_data(5, 99), ok(), misc_data(6, 77), misc_data(7, 42)])
    assert bridge._misc_byte(7) == 42
    assert bridge.skipped_replies == 3


def test_a_late_frame_for_the_same_index_in_another_bank_is_skipped():
    bridge, _ = bridge_with([misc_data(7, 5, bank=2), misc_data(7, 42, bank=1)])
    assert bridge._misc_byte(7) == 42


def test_a_genuine_error_reply_is_still_the_answer():
    bridge, _ = bridge_with([error()])
    with pytest.raises(DeviceError):
        bridge._misc_byte(7)
    assert bridge.skipped_replies == 0


def test_only_stray_frames_still_time_out():
    bridge, _ = bridge_with([ok(), ok(), misc_data(5, 1)], timeout=0.2)
    with pytest.raises(TimeoutError):
        bridge._misc_byte(7)


def test_a_clean_exchange_is_untouched():
    bridge, _ = bridge_with([misc_data(7, 42)])
    assert bridge._misc_byte(7) == 42
    assert bridge.skipped_replies == 0


def test_a_request_whose_answer_is_an_ok_is_not_filtered():
    # a delete is answered by REPLY OK: the layer must hand it straight to the original method
    bridge, _ = bridge_with([ok()])
    frame = m.build_frame(m.Command.DELS, [0, 0])
    reply = bridge.send_and_receive(frame)
    assert m.parse_frame(reply)[1] == m.Command.REPLY
    assert bridge.skipped_replies == 0


def test_the_layer_is_idempotent_and_per_instance():
    bridge, _ = bridge_with([misc_data(1, 1)])
    method = bridge.send_and_receive
    make_reply_tolerant(bridge)
    assert bridge.send_and_receive is method
    other = S3kBridge(ScriptedPorts(), ScriptedPorts(), "other")
    assert not getattr(other, "reply_tolerant", False)  # the class is untouched


def test_read_requests_and_header_reads_cover_what_the_editor_uses():
    for command in (m.Command.RPLIST, m.Command.RSLIST, m.Command.RPHEADER, m.Command.RMISCDATA, m.Command.RMULTIDATA, m.Command.RMDATA, m.Command.RSTAT):
        assert int(command) in reply_matching._READ_REQUESTS
    for command in (m.Command.DELP, m.Command.DELK, m.Command.DELS, m.Command.SETEX, m.Command.PDATA, m.Command.MDATA):
        assert int(command) not in reply_matching._READ_REQUESTS
    # matched on item only where the reply is verified to echo it
    assert int(m.Command.RMISCDATA) in reply_matching._HEADER_READS
    assert int(m.Command.RFXDATA) not in reply_matching._HEADER_READS
    assert int(m.Command.RHDDIR) not in reply_matching._HEADER_READS


def test_connect_installs_the_layer_on_a_real_connection(monkeypatch):
    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    monkeypatch.setenv("AKAISDS_SHARED_MIDI_TRANSPORT", "1")
    ports = ScriptedPorts()

    class _Midi:
        raw_input = ports
        raw_output = ports
        output_name = "Fake"

    bridge = program_editor_bridge.connect(_Midi())
    assert bridge._bridge.reply_tolerant is True


def test_demo_connections_are_left_alone(monkeypatch):
    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    bridge = program_editor_bridge.connect()
    assert not getattr(bridge._bridge, "reply_tolerant", False)


# --- the S1000 editor sits on the same layer (S1000Bridge wraps the S3kBridge connect() patched) ---


def test_the_s1000_adapter_reads_through_the_tolerant_bridge_and_exposes_the_stray_counter():
    import s3k.params as p
    from core.s1000_bridge import S1000Bridge

    s3k_bridge, ports = bridge_with()  # patched, empty script - we fill the batch once we know the request address
    adapter = S1000Bridge(s3k_bridge)
    block = bytes([1] + [0] * 149)  # block identifier 1 = program; 150 bytes, all zero
    address = adapter._address("program", 0, 0)
    pdata = m.build_frame(m.Command.PDATA, [*address, *m.encode_nibbles(block)])
    ports.batches.append([ok(), ok(), pdata])  # two stray OKs ahead of the real block

    value = adapter.get_parameter(p.lookup("PRLOUD", "program"), 0)

    assert value == 0
    assert adapter.skipped_replies == 2  # the S1000 adapter forwards the counter, so BridgeWorker's stray warning works for it too
    assert program_editor_bridge.LoggingBridge(adapter).skipped_replies == 2


def test_the_s1000_connection_is_patched_before_it_is_wrapped(monkeypatch):
    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    monkeypatch.setenv("AKAISDS_SHARED_MIDI_TRANSPORT", "1")
    ports = ScriptedPorts()

    class _Midi:
        raw_input = ports
        raw_output = ports
        output_name = "Fake"

    bridge = program_editor_bridge.connect(_Midi(), "akai_s1000")
    assert bridge._bridge._bridge.reply_tolerant is True  # LoggingBridge -> S1000Bridge -> the patched S3kBridge
