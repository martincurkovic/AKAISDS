# tests for core/s1000_bridge.py (the S1000 block adapter) and
# core/demo_s1000.py (the fake S1000 it's tested against).
#
# Everything here runs the REAL s3k.bridge.S3kBridge over FakeS1000's fake
# MIDI ports, so framing, nibble encoding, reply filtering and s3k.params'
# encoders/decoders are all genuinely exercised - the only fake is the
# sampler itself, and it deliberately stays SILENT for the S3000-only
# extended ops (0x20+) the way a real S1000 does. That silence is what broke
# the first real S1000 user's session, so a test that reaches for one fails
# with a TimeoutError, not a quiet wrong answer.

import pytest
import s3k.messages as m
import s3k.params as p
from s3k.bridge import DeviceError

from core import program_editor_bridge
from core.demo_s1000 import DEFAULT_BLOCK_SIZE, FakeS1000
from core.program_editor_bridge import BridgeWorker, LoggingBridge
from core.s1000_bridge import S1000Bridge, supports


@pytest.fixture
def fake():
    return FakeS1000()


@pytest.fixture
def bridge(fake):
    return S1000Bridge(fake.bridge(timeout=0.3))


def _param(name, region):
    return p.lookup(name, region)


# --- the failure this adapter exists to fix --------------------------------------


def test_plain_s3k_bridge_times_out_against_an_s1000(fake):
    # the real user's log: lists work (base ops), every get_parameter times
    # out ("no reply within 2.0s") because the S1000 ignores the S3000-only
    # header-bytes ops
    raw = fake.bridge(timeout=0.05)
    assert raw.program_list() == ["DRUMS", "PAD PROG"]
    assert raw.sample_list() == ["KICK", "SNARE", "PAD"]
    with pytest.raises(TimeoutError):
        raw.get_parameter(_param("GROUPS", "program"), 0)
    assert fake.ignored_ops, "the extended op should have gone unanswered"


def test_adapter_never_sends_an_s3000_only_op(fake, bridge):
    bridge.get_parameter(_param("GROUPS", "program"), 0)
    bridge.get_parameter(_param("LONOTE", "keygroup"), 0, keygroup=1)
    bridge.get_parameter(_param("SLNGTH", "sample"), 2)
    bridge.set_parameter(_param("PRLOUD", "program"), 0, 50)
    bridge.set_parameter(_param("FILFRQ", "keygroup"), 0, 40, keygroup=0)
    bridge.set_parameter(_param("SPITCH", "sample"), 0, 61)
    assert fake.ignored_ops == []
    assert all(op < 0x20 for op, _payload in fake.received)


# --- reads ---------------------------------------------------------------------------


def test_lists_pass_through(bridge):
    assert bridge.program_list() == ["DRUMS", "PAD PROG"]
    assert bridge.sample_list() == ["KICK", "SNARE", "PAD"]


def test_reads_program_fields(bridge):
    assert bridge.get_parameter(_param("PRNAME", "program"), 1).strip() == "PAD PROG"
    assert bridge.get_parameter(_param("GROUPS", "program"), 0) == 2
    assert bridge.get_parameter(_param("GROUPS", "program"), 1) == 1
    assert bridge.get_parameter(_param("PRLOUD", "program"), 0) == 80
    # PRGNUM carries s3k.params' display_offset (+1), same as on an S2000
    assert bridge.get_parameter(_param("PRGNUM", "program"), 1) == 2


def test_reads_keygroup_fields_by_keygroup_number(bridge):
    assert bridge.get_parameter(_param("LONOTE", "keygroup"), 0, keygroup=0) == 24
    assert bridge.get_parameter(_param("HINOTE", "keygroup"), 0, keygroup=0) == 59
    assert bridge.get_parameter(_param("LONOTE", "keygroup"), 0, keygroup=1) == 60
    assert (
        bridge.get_parameter(_param("SNAME1", "keygroup"), 0, keygroup=1).strip()
        == "SNARE"
    )


def test_reads_sample_fields(bridge):
    assert bridge.get_parameter(_param("SLNGTH", "sample"), 1) == 33000
    assert bridge.get_parameter(_param("SSRATE", "sample"), 1) == 44100
    assert bridge.get_parameter(_param("SPITCH", "sample"), 0) == 60
    assert bridge.get_parameter(_param("SHNAME", "sample"), 2).strip() == "PAD"


def test_s3000_only_fields_read_as_neutral_zero_not_junk_or_error(fake):
    # the fake's blocks are 150 bytes with 0xA5 junk past the S1000 spec's
    # own fields - a field past that extent must NOT decode that junk
    bridge = S1000Bridge(fake.bridge(timeout=0.3))
    assert fake.block_size == DEFAULT_BLOCK_SIZE
    for name, region in [
        ("LEGATO", "program"),
        ("B_PTCHD", "program"),
        ("MODSPAN1", "program"),
        ("LFO1WAVE", "program"),
        ("FILQ", "keygroup"),
        ("ENV2R2", "keygroup"),
        ("MODVFILT1", "keygroup"),
    ]:
        param = _param(name, region)
        assert not supports(param)
        assert bridge.get_parameter(param, 0) == p.decode_field(
            param, bytes(param.size)
        ), name


def test_every_s1000_spec_field_is_supported():
    # spot-check the edges of each region's extent against the S1000 spec
    assert supports(_param("VSSCL", "program"))
    assert not supports(_param("LEGATO", "program"))
    assert supports(_param("KV_LO", "keygroup"))
    assert not supports(_param("FILQ", "keygroup"))
    assert supports(_param("SHLTO", "sample"))
    assert not supports(_param("MULTINAME", "multi"))


def test_get_header_returns_only_s1000_fields(bridge):
    header = bridge.get_header("program", 0)
    assert header["GROUPS"] == 2
    assert "LEGATO" not in header and "MODSPAN1" not in header


def test_out_of_range_index_raises_a_device_error(bridge):
    with pytest.raises(DeviceError):
        bridge.get_parameter(_param("GROUPS", "program"), 9)


# --- writes: read-modify-write of the WHOLE block -------------------------------------


def test_write_changes_only_that_field_and_round_trips_the_rest(fake, bridge):
    before = bytes(fake.programs[0]["block"])
    bridge.set_parameter(_param("PRLOUD", "program"), 0, 33)

    after = bytes(fake.programs[0]["block"])
    assert len(after) == len(before) == DEFAULT_BLOCK_SIZE  # length untouched
    loud = _param("PRLOUD", "program").offset
    assert after[loud] == 33
    assert after[:loud] + after[loud + 1 :] == before[:loud] + before[loud + 1 :]
    # including the junk bytes past the spec's fields - internal data this
    # app has no field for must survive the rewrite byte for byte
    assert after[72:] == before[72:] != bytes(len(before) - 72)
    assert fake.writes == 1


def test_write_goes_to_the_addressed_keygroup(fake, bridge):
    bridge.set_parameter(_param("LONOTE", "keygroup"), 0, 30, keygroup=1)
    assert bridge.get_parameter(_param("LONOTE", "keygroup"), 0, keygroup=1) == 30
    assert bridge.get_parameter(_param("LONOTE", "keygroup"), 0, keygroup=0) == 24


def test_write_name_field(bridge):
    bridge.set_parameter(_param("PRNAME", "program"), 1, "RENAMED")
    assert bridge.get_parameter(_param("PRNAME", "program"), 1).strip() == "RENAMED"


def test_write_sample_header_field(fake, bridge):
    bridge.set_parameter(_param("SPTYPE", "sample"), 0, 2)
    assert bridge.get_parameter(_param("SPTYPE", "sample"), 0) == 2
    assert fake.samples[0][_param("SPTYPE", "sample").offset] == 2


def test_writing_an_s3000_only_field_is_refused_without_touching_the_wire(
    fake, bridge
):
    with pytest.raises(ValueError, match="doesn't exist on an S1000"):
        bridge.set_parameter(_param("LEGATO", "program"), 0, 1)
    assert fake.received == []


def test_a_rejected_write_raises_and_is_not_cached_as_applied(fake, bridge):
    # the fake rejects a PDATA whose GROUPS doesn't match (the spec: "the
    # parameter GROUPS must be correct")
    bridge.get_parameter(_param("GROUPS", "program"), 0)
    with pytest.raises(DeviceError, match="error writing"):
        bridge.set_header_bytes(
            "program", 0, _param("GROUPS", "program").offset, bytes([5])
        )
    assert bridge.get_parameter(_param("GROUPS", "program"), 0) == 2


def test_a_write_past_the_end_of_the_block_is_refused(fake, bridge):
    with pytest.raises(ValueError, match="runs past"):
        bridge.set_header_bytes("sample", 0, DEFAULT_BLOCK_SIZE - 1, bytes(4))


# --- block cache ------------------------------------------------------------------------


def _block_requests(fake):
    return [op for op, _payload in fake.received if op == m.Command.RPDATA]


def test_reading_many_fields_costs_one_block_fetch(fake, bridge):
    for name in ("PRNAME", "PRGNUM", "GROUPS", "PRLOUD", "PANPOS", "POLYPH"):
        bridge.get_parameter(_param(name, "program"), 0)
    assert len(_block_requests(fake)) == 1


def test_a_list_refresh_invalidates_the_cache(fake, bridge):
    bridge.get_parameter(_param("PRLOUD", "program"), 0)
    # changed "on the front panel" behind the adapter's back
    fake.programs[0]["block"][_param("PRLOUD", "program").offset] = 11
    bridge.program_list()  # every Refresh does this first
    assert bridge.get_parameter(_param("PRLOUD", "program"), 0) == 11


def test_the_cache_expires(fake, bridge, monkeypatch):
    import core.s1000_bridge as s1000_bridge

    now = [1000.0]
    monkeypatch.setattr(s1000_bridge.time, "monotonic", lambda: now[0])
    bridge.get_parameter(_param("PRLOUD", "program"), 0)
    fake.programs[0]["block"][_param("PRLOUD", "program").offset] = 12
    now[0] += 0.5
    assert bridge.get_parameter(_param("PRLOUD", "program"), 0) == 80  # cached
    now[0] += 5
    assert bridge.get_parameter(_param("PRLOUD", "program"), 0) == 12  # re-read


def test_deleting_invalidates_the_cache(fake, bridge):
    assert bridge.get_parameter(_param("PRNAME", "program"), 0).strip() == "DRUMS"
    bridge.delete_program(0)
    assert bridge.program_list() == ["PAD PROG"]
    assert bridge.get_parameter(_param("PRNAME", "program"), 0).strip() == "PAD PROG"


def test_delete_keygroup_leaves_groups_stale_like_the_real_s1000(fake, bridge):
    # a real S1000 acknowledged DELK but kept GROUPS unchanged (2026-10-05
    # log); the fake reproduces that unless told otherwise
    bridge.delete_keygroup(0, 1)
    assert len(fake.programs[0]["keygroups"]) == 1
    assert bridge.get_parameter(_param("GROUPS", "program"), 0) == 2


def test_delete_keygroup_updates_groups_when_the_fake_is_told_to():
    fake = FakeS1000(delk_updates_groups=True)
    bridge = S1000Bridge(fake.bridge(timeout=0.3))
    bridge.delete_keygroup(0, 1)
    assert bridge.get_parameter(_param("GROUPS", "program"), 0) == 1


def test_fake_refuses_a_keygroup_written_past_the_end_of_the_chain(fake, bridge):
    from core import akai_sysex

    block = bridge.get_header_bytes("keygroup", 0, 0, 192, selector=0)
    reply = bridge.send_and_receive(akai_sysex.build_kdata_request(0, 5, block))
    assert not m.Reply.decode(reply).ok
    reply = bridge.send_and_receive(akai_sysex.build_kdata_request(0, 2, block))
    assert m.Reply.decode(reply).ok  # appending at exactly len() is fine


def test_delete_sample(fake, bridge):
    bridge.delete_sample(0)
    assert bridge.sample_list() == ["SNARE", "PAD"]


def test_block_with_the_wrong_identifier_is_rejected(fake, bridge):
    fake.programs[0]["block"][0] = 3
    with pytest.raises(DeviceError, match="block identifier"):
        bridge.get_parameter(_param("GROUPS", "program"), 0)


def test_other_attributes_pass_through_to_the_wrapped_bridge(bridge):
    assert bridge.exclusive_channel == 0
    assert bridge.status().version == "2.00"


def test_renumber_programs_is_disabled_so_the_worker_skips_it(bridge):
    # S3kBridge.renumber_programs would time out on an S1000 - and
    # BridgeWorker._handle_program_change skips it when this is None
    assert getattr(bridge, "renumber_programs", "missing") is None


# --- whatever block length the device really uses ---------------------------------------


@pytest.mark.parametrize("block_size", [72, 141, 149, 150, 192])
def test_works_whatever_the_real_block_length_is(block_size):
    # the spec only says "about 150 bytes" - the adapter must neither assume
    # a length nor change it on a write
    fake = FakeS1000(block_size=block_size)
    bridge = S1000Bridge(fake.bridge(timeout=0.3))
    assert bridge.get_parameter(_param("GROUPS", "program"), 0) == 2
    bridge.set_parameter(_param("PRLOUD", "program"), 0, 7)
    assert bridge.get_parameter(_param("PRLOUD", "program"), 0) == 7
    assert len(fake.programs[0]["block"]) == max(block_size, 72)
    assert fake.ignored_ops == []


def test_a_block_shorter_than_the_spec_extent_degrades_to_neutral(caplog):
    # a layout-table mistake must not take the whole editor down
    fake = FakeS1000(block_size=60)
    fake.programs[0]["block"] = fake.programs[0]["block"][:60]
    bridge = S1000Bridge(fake.bridge(timeout=0.3))
    assert bridge.get_parameter(_param("GROUPS", "program"), 0) == 2  # offset 42
    assert bridge.get_parameter(_param("PTUNO", "program"), 0) == p.decode_field(
        _param("PTUNO", "program"), bytes(2)
    )  # offset 65, past the end


# --- the whole stack: connect() -> LoggingBridge -> BridgeWorker -----------------------------


def test_connect_builds_an_s1000_adapter_for_the_s1000_model(monkeypatch):
    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    s1000 = program_editor_bridge.connect(sampler_model="akai_s1000")
    assert isinstance(s1000, LoggingBridge)
    assert isinstance(s1000._bridge, S1000Bridge)

    from s3ked.demo import DemoBridge

    s3000 = program_editor_bridge.connect(sampler_model="akai_s2000_s3000")
    assert isinstance(s3000._bridge, DemoBridge)


def test_connect_wraps_a_real_connection_for_the_s1000_model(monkeypatch):
    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    monkeypatch.setattr(
        program_editor_bridge.app_config, "get_saved_ports", lambda: ("In", "Out")
    )
    sentinel = FakeS1000().bridge()
    monkeypatch.setattr(
        program_editor_bridge.S3kBridge, "standard", lambda output_name: sentinel
    )
    wrapped = program_editor_bridge.connect(sampler_model="akai_s1000")
    assert isinstance(wrapped._bridge, S1000Bridge)
    assert wrapped._bridge._bridge is sentinel


def test_bridge_worker_loads_keygroups_and_details_from_an_s1000(fake):
    worker = BridgeWorker(LoggingBridge(S1000Bridge(fake.bridge(timeout=0.3))))
    loaded = {}
    failed = []
    worker.keygroups_loaded.connect(
        lambda pi, ranges, values: loaded.update(ranges=ranges, program=values)
    )
    worker.detail_loaded.connect(lambda pi, ki, values: loaded.update(detail=values))
    worker.keygroups_load_failed.connect(lambda pi, e: failed.append(e))
    worker.detail_load_failed.connect(lambda pi, ki, e: failed.append(e))
    worker.sample_detail_loaded.connect(lambda i, values: loaded.update(sample=values))
    worker.sample_detail_load_failed.connect(lambda i, e: failed.append(e))

    worker.submit_keygroups(0)
    worker.submit_detail(0, 1)
    worker.submit_sample_detail(1)
    worker.process_pending()

    assert failed == []
    assert loaded["ranges"] == [(24, 59), (60, 127)]
    assert loaded["program"]["PRLOUD"] == 80
    assert loaded["program"]["LEGATO"] == 0  # S3000-only field: neutral
    assert loaded["detail"]["SNAME1"].strip() == "SNARE"
    assert loaded["detail"]["FILQ"] == 0  # S3000-only field: neutral
    assert loaded["sample"]["SLNGTH"] == 33000
    assert fake.ignored_ops == []


def test_bridge_worker_write_round_trips_through_an_s1000(fake):
    worker = BridgeWorker(LoggingBridge(S1000Bridge(fake.bridge(timeout=0.3))))
    results = []
    worker.write_succeeded.connect(lambda key, name, value: results.append(("ok", name, value)))
    worker.write_failed.connect(lambda key, name, e: results.append(("fail", name, e)))

    worker.submit_write("k", "PRLOUD", "program", 0, 42, 0)
    worker.submit_write("k", "FILFRQ", "keygroup", 0, 17, 1)
    worker.process_pending()

    assert results == [("ok", "PRLOUD", 42), ("ok", "FILFRQ", 17)]
    assert fake.programs[0]["block"][_param("PRLOUD", "program").offset] == 42
    assert fake.programs[0]["keygroups"][1][_param("FILFRQ", "keygroup").offset] == 17


# --- duplicating (PDATA/KDATA creation) on an S1000 -------------------------------------------


def test_raw_frames_sent_through_the_adapter_invalidate_its_cache(fake, bridge):
    # BridgeWorker's create-program/keygroup flows send PDATA/KDATA through
    # send_and_receive directly - a read right afterwards must not see the
    # block cached before it
    assert bridge.get_parameter(_param("GROUPS", "program"), 0) == 2
    kg = bridge.get_header_bytes("keygroup", 0, 0, 192, selector=0)
    frame = __import__("core.akai_sysex", fromlist=["x"]).build_kdata_request(0, 2, kg)
    reply = bridge.send_and_receive(frame)
    assert m.Reply.decode(reply).ok
    assert bridge.get_parameter(_param("GROUPS", "program"), 0) == 3


def _worker_for(fake):
    worker = BridgeWorker(LoggingBridge(S1000Bridge(fake.bridge(timeout=0.3))))
    events = []
    worker.program_created.connect(lambda s, n: events.append(("program", s, n)))
    worker.program_create_failed.connect(lambda s, e: events.append(("program-failed", e)))
    worker.keygroup_created.connect(lambda pi, k: events.append(("keygroup", pi, k)))
    worker.keygroup_create_failed.connect(lambda pi, e: events.append(("keygroup-failed", e)))
    return worker, events


def test_duplicate_program_clones_every_keygroup(fake):
    worker, events = _worker_for(fake)
    worker.submit_create_program(0, "DRUMS COPY")
    worker.process_pending()

    assert events == [("program", 0, 2)]
    assert len(fake.programs) == 3
    new = fake.programs[2]
    source = fake.programs[0]
    name = _param("PRNAME", "program")
    assert p.decode_field(name, bytes(new["block"][name.offset : name.end])).strip() == "DRUMS COPY"
    assert len(new["keygroups"]) == 2
    # whole blocks cloned verbatim, including bytes past the spec's fields
    assert new["keygroups"] == source["keygroups"]
    groups = _param("GROUPS", "program").offset
    assert new["block"][groups] == 2
    assert len(new["block"]) == len(source["block"])
    assert fake.ignored_ops == []


def test_duplicate_single_keygroup_program(fake):
    worker, events = _worker_for(fake)
    worker.submit_create_program(1, "PAD COPY")
    worker.process_pending()
    assert events == [("program", 1, 2)]
    assert len(fake.programs[2]["keygroups"]) == 1


def test_duplicate_keygroup_appends_a_copy(fake):
    worker, events = _worker_for(fake)
    worker.submit_create_keygroup(0, 1)
    worker.process_pending()

    assert events == [("keygroup", 0, 2)]
    keygroups = fake.programs[0]["keygroups"]
    assert len(keygroups) == 3
    assert keygroups[2] == keygroups[1]
    assert fake.programs[0]["block"][_param("GROUPS", "program").offset] == 3
    assert fake.ignored_ops == []


def test_reading_after_a_duplicate_sees_the_new_group_count(fake):
    # the editor reloads keygroups right after keygroup_created - through
    # the adapter's cache, which must not still hold the old GROUPS
    bridge = LoggingBridge(S1000Bridge(fake.bridge(timeout=0.3)))
    worker = BridgeWorker(bridge)
    loaded = []
    worker.keygroups_loaded.connect(lambda pi, ranges, values: loaded.append(ranges))
    # processed one at a time - BridgeWorker coalesces a queued duplicate
    # "current state" request, so queueing both loads up front would drop one
    worker.submit_keygroups(0)
    worker.process_pending()
    worker.submit_create_keygroup(0, 0)
    worker.process_pending()
    worker.submit_keygroups(0)
    worker.process_pending()
    assert [len(r) for r in loaded] == [2, 3]


# --- S1000 controller routing: fields s3k.params calls "not used" ----------------------------
#
# s3k.params declares K_LOUD/P_LOUD/K_PANP/MW_PAN/K_LRAT/K_LDEP/K_LDEL (program)
# and V_FREQ/P_FREQ/E_FREQ/E_PTCH/KV_LO (keygroup) as range 0..0, "fixed value
# in the specification" (true of the S3000). The S1000 spec lists every one as
# "+/-50". Left uncorrected, the range check refuses every non-zero write and a
# stored negative reads back as 256 - n.

from core.program_editor_bridge import S1000_KEYGROUP_FIELDS, S1000_PROGRAM_FIELDS
from core.s1000_bridge import s1000_param

_NOT_USED_ON_S3000 = [
    ("K_LOUD", "program"),
    ("P_LOUD", "program"),
    ("K_PANP", "program"),
    ("MW_PAN", "program"),
    ("K_LRAT", "program"),
    ("K_LDEP", "program"),
    ("K_LDEL", "program"),
    ("V_FREQ", "keygroup"),
    ("P_FREQ", "keygroup"),
    ("E_FREQ", "keygroup"),
    ("E_PTCH", "keygroup"),
    ("KV_LO", "keygroup"),
]


def _raw_byte(fake, name, region, index=0, keygroup=0):
    offset = _param(name, region).offset
    if region == "program":
        return fake.programs[index]["block"][offset]
    return fake.programs[index]["keygroups"][keygroup][offset]


@pytest.mark.parametrize("name,region", _NOT_USED_ON_S3000)
def test_s3000_not_used_fields_get_the_s1000s_plus_minus_50_range(name, region):
    corrected = s1000_param(_param(name, region))
    assert (corrected.minimum, corrected.maximum) == (-50, 50)
    # a corrected COPY - s3k.params itself is untouched (it's a dependency)
    assert _param(name, region).maximum == 0


@pytest.mark.parametrize("name,region", _NOT_USED_ON_S3000)
def test_negative_amounts_round_trip_through_the_declared_parameter(
    fake, bridge, name, region
):
    # exactly what the editor does: hands in s3k.params' OWN (0..0) Parameter
    declared = _param(name, region)
    bridge.set_parameter(declared, 0, -20, keygroup=1)
    assert bridge.get_parameter(declared, 0, keygroup=1) == -20
    # stored as the two's-complement byte the S1000 expects
    assert _raw_byte(fake, name, region, keygroup=1) == (-20) & 0xFF
    bridge.set_parameter(declared, 0, 50, keygroup=1)
    assert bridge.get_parameter(declared, 0, keygroup=1) == 50
    bridge.set_parameter(declared, 0, -50, keygroup=1)
    assert bridge.get_parameter(declared, 0, keygroup=1) == -50


@pytest.mark.parametrize("name,region", _NOT_USED_ON_S3000)
def test_out_of_range_amounts_are_still_refused(fake, bridge, name, region):
    declared = _param(name, region)
    for value in (51, -51):
        with pytest.raises(ValueError):
            bridge.set_parameter(declared, 0, value)
    assert fake.writes == 0


def test_a_negative_byte_already_on_the_sampler_reads_as_negative(fake, bridge):
    offset = _param("E_FREQ", "keygroup").offset
    fake.programs[0]["keygroups"][0][offset] = 0xEC  # -20, written on the panel
    assert bridge.get_parameter(_param("E_FREQ", "keygroup"), 0, keygroup=0) == -20


def test_get_header_applies_the_corrections_too(fake, bridge):
    fake.programs[0]["block"][_param("K_LOUD", "program").offset] = 0xF6  # -10
    assert bridge.get_header("program", 0)["K_LOUD"] == -10


def test_fields_with_a_correct_declared_range_are_untouched():
    for name, region in [("V_LOUD", "program"), ("P_PTCH", "program"),
                         ("MWLDEP", "program"), ("V_ATT1", "keygroup"),
                         ("V_ENV2", "keygroup"), ("K_FREQ", "keygroup")]:
        declared = _param(name, region)
        assert s1000_param(declared) is declared


@pytest.mark.parametrize("name", S1000_PROGRAM_FIELDS)
def test_every_s1000_program_controller_field_round_trips(fake, bridge, name):
    param = s1000_param(_param(name, "program"))
    assert supports(param) and param.writable
    for value in sorted({param.minimum, param.maximum, 7}):
        bridge.set_parameter(_param(name, "program"), 1, value)
        assert bridge.get_parameter(_param(name, "program"), 1) == value


@pytest.mark.parametrize("name", S1000_KEYGROUP_FIELDS)
def test_every_s1000_keygroup_controller_field_round_trips(fake, bridge, name):
    param = s1000_param(_param(name, "keygroup"))
    assert supports(param) and param.writable
    for value in sorted({param.minimum, param.maximum, 7}):
        bridge.set_parameter(_param(name, "keygroup"), 0, value, keygroup=1)
        assert bridge.get_parameter(_param(name, "keygroup"), 0, keygroup=1) == value
    assert fake.ignored_ops == []


def test_writing_one_controller_leaves_its_neighbours_alone(fake, bridge):
    before = bytes(fake.programs[0]["keygroups"][0])
    bridge.set_parameter(_param("E_FREQ", "keygroup"), 0, -33, keygroup=0)
    after = bytes(fake.programs[0]["keygroups"][0])
    offset = _param("E_FREQ", "keygroup").offset
    assert after[offset] == (-33) & 0xFF
    assert after[:offset] + after[offset + 1 :] == before[:offset] + before[offset + 1 :]


def test_worker_reads_the_controller_fields_only_when_asked(fake):
    fake.programs[0]["block"][_param("K_LOUD", "program").offset] = 0xF6  # -10
    fake.programs[0]["keygroups"][0][_param("E_FREQ", "keygroup").offset] = 30
    bridge = LoggingBridge(S1000Bridge(fake.bridge(timeout=0.3)))

    def load(**extras):
        worker = BridgeWorker(bridge, **extras)
        got = {}
        worker.keygroups_loaded.connect(lambda pi, r, v: got.update(program=v))
        worker.detail_loaded.connect(lambda pi, ki, v: got.update(detail=v))
        worker.submit_keygroups(0)
        worker.process_pending()
        worker.submit_detail(0, 0)
        worker.process_pending()
        return got

    with_extras = load(
        extra_program_fields=S1000_PROGRAM_FIELDS,
        extra_keygroup_fields=S1000_KEYGROUP_FIELDS,
    )
    assert with_extras["program"]["K_LOUD"] == -10
    assert with_extras["detail"]["E_FREQ"] == 30
    for name in S1000_PROGRAM_FIELDS:
        assert name in with_extras["program"]
    for name in S1000_KEYGROUP_FIELDS:
        assert name in with_extras["detail"]

    # an S2000/S3000 worker never reads them (each would cost a round trip)
    without = load()
    assert "K_LOUD" not in without["program"]
    assert "E_FREQ" not in without["detail"]


# --- the spec's name rule (modelled by FakeS1000) -----------------------------------------------


def test_fake_models_a_clashing_program_name_deleting_the_other_program(fake, bridge):
    # S1000 spec: a PDATA whose name matches another resident program
    # deletes that program first - this is the hazard the editor's rename
    # guards exist for
    bridge.set_parameter(_param("PRNAME", "program"), 1, "DRUMS")  # DRUMS exists at 0
    assert bridge.program_list() == ["DRUMS"]
    assert fake.deleted_by_name_clash == [("program", 0)]


def test_fake_models_a_clashing_sample_name_deleting_the_other_sample(fake, bridge):
    bridge.set_parameter(_param("SHNAME", "sample"), 0, "SNARE")  # SNARE exists at 1
    assert bridge.sample_list() == ["SNARE", "PAD"]
    assert fake.deleted_by_name_clash == [("sample", 1)]


def test_rewriting_an_item_under_its_own_name_deletes_nothing(fake, bridge):
    # every editor write re-sends the block with its own unchanged name - the
    # fake treats that as an in-place replace (the spec doesn't say so
    # outright; a real S1000 is the thing to confirm it)
    bridge.set_parameter(_param("PRLOUD", "program"), 0, 40)
    bridge.set_parameter(_param("SPITCH", "sample"), 2, 61)
    assert fake.deleted_by_name_clash == []
    assert bridge.program_list() == ["DRUMS", "PAD PROG"]
    assert bridge.sample_list() == ["KICK", "SNARE", "PAD"]
