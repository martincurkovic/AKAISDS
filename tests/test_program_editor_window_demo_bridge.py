# Integration tests for ProgramEditorWindow against the REAL s3ked.demo.
# DemoBridge, not the hand-authored FakeBridge used in
# test_program_editor_window.py.
#
# Why this file exists: FakeBridge duck-types whatever program_editor_window.py
# and program_editor_bridge.py currently expect from s3k/s3ked - it can never
# catch the pinned dependency (see pyproject.toml's [tool.uv.sources] s3ked
# rev) renaming/removing a parameter, changing which region a field lives in,
# narrowing a declared min/max, or DemoBridge/S3kBridge's own method surface
# drifting (see AGENTS.md's "Third-party dependencies you should actually go
# read"). None of that would fail a single FakeBridge-based test since
# FakeBridge never calls s3k.params.lookup()/encode_field()/decode_field() at
# all. Driving the real DemoBridge through the real BridgeWorker exercises
# every one of those exact seams - which is the whole point when the plan is
# to bump the s3ked pin and needs a safety net first.
#
# DemoBridge is safe to use here: it's an in-memory, deterministic, no-MIDI
# fake that s3ked itself ships as production code for its own --demo flag
# (see s3ked/demo.py's own docstring), not something built for this test
# suite - same reasoning as AGENTS.md's "Developing without hardware" section.

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

import s3k.params as p
from s3ked.demo import DemoBridge

from ui.program_editor_window import ProgramEditorWindow


# s3ked.demo's own fixed demo state (s3ked/demo.py's _DEMO_KEYGROUPS) - how
# many keygroups each of its 5 demo programs has. Read here instead of
# hard-coded blind so the intent ("drive every program, every keygroup
# count the demo actually has") stays obvious; if s3ked's own demo shape
# ever changes, DemoBridge.keygroup_count() below is asked directly rather
# than assumed.
_DEMO_PROGRAM_COUNT = 5


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _pump_until(qapp, predicate, timeout=5.0):
    # see test_program_editor_window.py's identical helper - a queued
    # cross-thread signal isn't always delivered by a single processEvents()
    deadline = time.monotonic() + timeout
    while not predicate():
        qapp.processEvents()
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met before timeout")


def _settle(editor, qapp, pumps=10):
    # for assertions that only care "did this finish without blowing up",
    # not a specific resulting value to poll for
    editor._worker.wait_until_idle()
    for _ in range(pumps):
        qapp.processEvents()


class _FailureCollector:
    """Records every *_load_failed/write_failed/change_send_failed signal.

    Tests assert this stays empty across ordinary use of the real DemoBridge
    - any entry here means the real dependency raised something the app
    didn't handle as a normal outcome.
    """

    def __init__(self, worker):
        self.events = []
        worker.programs_load_failed.connect(
            lambda e: self.events.append(("programs", e))
        )
        worker.samples_load_failed.connect(
            lambda e: self.events.append(("samples", e))
        )
        worker.keygroups_load_failed.connect(
            lambda pi, e: self.events.append(("keygroups", pi, e))
        )
        worker.detail_load_failed.connect(
            lambda pi, ki, e: self.events.append(("detail", pi, ki, e))
        )
        worker.parts_load_failed.connect(lambda e: self.events.append(("parts", e)))
        worker.change_send_failed.connect(
            lambda pi, e: self.events.append(("change", pi, e))
        )
        worker.write_failed.connect(
            lambda key, name, e: self.events.append(("write", key, name, e))
        )


@pytest.fixture
def editor(qapp):
    fake_main_window = QWidget()
    bridge = DemoBridge()
    editor = ProgramEditorWindow(fake_main_window, bridge=bridge)
    failures = _FailureCollector(editor._worker)
    editor._worker.wait_until_idle()  # program list loaded -> submits sample list
    _pump_until(qapp, lambda: editor.program_list.count() > 0)
    editor._worker.wait_until_idle()  # sample list loaded -> selects row 0
    _pump_until(qapp, lambda: editor.program_list.currentRow() == 0)
    editor._worker.wait_until_idle()  # row 0 selection -> keygroup load
    _pump_until(qapp, lambda: editor.keygroup_list.count() == bridge.keygroup_count(0))
    editor._failures = failures
    editor._demo_bridge = bridge
    yield editor
    # see AGENTS.md's BridgeWorker section - must be stopped explicitly or
    # the worker thread is still running when the test ends
    editor._worker.stop()
    editor._worker.wait()


def test_demo_bridge_construction_loads_everything_without_errors(editor):
    # the fixture above already drove program list -> sample list -> first
    # keygroup load to completion, and _on_programs_loaded's first-load
    # branch submits the multi parts load too (see program_editor_window.py)
    assert editor.program_list.count() == _DEMO_PROGRAM_COUNT
    assert editor.keygroup_list.count() == editor._demo_bridge.keygroup_count(0)
    assert editor._failures.events == []


def test_demo_bridge_switching_through_every_program_loads_its_real_keygroup_count(
    editor, qapp
):
    bridge = editor._demo_bridge
    for program_index in range(_DEMO_PROGRAM_COUNT):
        expected_count = bridge.keygroup_count(program_index)
        editor.program_list.setCurrentRow(program_index)
        editor._worker.wait_until_idle()
        _pump_until(qapp, lambda: editor.keygroup_list.count() == expected_count)

        for keygroup_index in range(expected_count):
            editor.keygroup_list.setCurrentRow(keygroup_index)
            _settle(editor, qapp)
            assert editor.detail_stack.currentIndex() == 1

    assert editor._failures.events == []


def test_demo_bridge_program_level_write_round_trips_through_real_encode_decode(
    editor, qapp
):
    # loud_knob -> PRLOUD, region "program" - a representative program-level
    # field. Confirms the write actually reached DemoBridge's own storage
    # (a real encode_field/decode_field round trip through the real Parameter
    # table), not just that FakeBridge recorded a call.
    bridge = editor._demo_bridge
    new_value = 42
    assert bridge.get_parameter(p.lookup("PRLOUD", "program"), 0) != new_value

    editor.loud_knob.setValue(new_value)
    editor._flush_write("PRLOUD")
    editor._worker.wait_until_idle()
    _pump_until(
        qapp, lambda: bridge.get_parameter(p.lookup("PRLOUD", "program"), 0) == new_value
    )

    assert editor._failures.events == []


def test_demo_bridge_keygroup_level_write_round_trips_through_real_encode_decode(
    editor, qapp
):
    # cutoff_knob -> FILFRQ, region "keygroup", writes to whichever keygroup
    # is selected (keygroup_list.currentRow(), see _wire_knob_write's
    # keygroup_index_getter) - the fixture loads the keygroup list but never
    # selects a row (currentRow() stays -1 until a real selection, matching
    # test_program_editor_window.py's own convention of always selecting a
    # row before touching keygroup-scoped fields), so select one explicitly
    editor.keygroup_list.setCurrentRow(0)
    _settle(editor, qapp)
    bridge = editor._demo_bridge
    new_value = 55
    assert (
        bridge.get_parameter(p.lookup("FILFRQ", "keygroup"), 0, keygroup=0)
        != new_value
    )

    editor.cutoff_knob.setValue(new_value)
    editor._flush_write("FILFRQ")
    editor._worker.wait_until_idle()
    _pump_until(
        qapp,
        lambda: bridge.get_parameter(p.lookup("FILFRQ", "keygroup"), 0, keygroup=0)
        == new_value,
    )

    assert editor._failures.events == []


def test_demo_bridge_lfo1_sync_and_lfo2_retrig_combos_round_trip(editor, qapp):
    # both booleans, region "program" - DESYNC and LFO2TRIG. DESYNC's raw
    # value equals the combo index directly despite the "Sync" label being
    # the field's own polarity flipped (see lfo1_sync_combo's construction
    # comment); LFO2TRIG is a plain hardware-measured boolean (AGENTS.md).
    bridge = editor._demo_bridge

    editor.lfo1_sync_combo.setCurrentIndex(1)  # "Off" -> DESYNC=1
    editor._flush_write("DESYNC")
    editor.lfo2_trig_combo.setCurrentIndex(1)  # "On" -> LFO2TRIG=1
    editor._flush_write("LFO2TRIG")
    editor._worker.wait_until_idle()
    _pump_until(
        qapp,
        lambda: bridge.get_parameter(p.lookup("DESYNC", "program"), 0) == 1
        and bridge.get_parameter(p.lookup("LFO2TRIG", "program"), 0) == 1,
    )

    assert editor._failures.events == []


def _wait_for_multi_parts_load(editor, qapp):
    # multi parts load is already submitted during the editor fixture's
    # first-load sequence (see _on_programs_loaded's "first load only"
    # branch) - just wait for it to land
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor._multi_program_combos[0].currentText() != "-")


def test_demo_bridge_multipart_write_round_trips_through_real_encode_decode(
    editor, qapp
):
    _wait_for_multi_parts_load(editor, qapp)
    bridge = editor._demo_bridge
    new_channel = 9
    assert (
        bridge.get_parameter(p.lookup("PMCHAN", "multipart"), 0) != new_channel
    )

    editor._multi_channel_combos[0].setCurrentIndex(
        editor._multi_channel_combos[0].findData(new_channel)
    )
    editor._flush_write("multipart_channel_0")
    editor._worker.wait_until_idle()
    _pump_until(
        qapp,
        lambda: bridge.get_parameter(p.lookup("PMCHAN", "multipart"), 0)
        == new_channel,
    )

    assert editor._failures.events == []


def test_demo_bridge_multi_name_write_round_trips_through_real_encode_decode(
    editor, qapp
):
    _wait_for_multi_parts_load(editor, qapp)
    bridge = editor._demo_bridge

    editor.multi_name_edit.setText("NEW MULTI")
    editor._commit_multi_name()
    editor._worker.wait_until_idle()
    _pump_until(
        qapp,
        lambda: bridge.get_parameter(p.lookup("MULTINAME", "multi"), 0).strip()
        == "NEW MULTI",
    )

    assert editor._failures.events == []


def test_demo_bridge_program_change_with_no_live_midi_connection_reports_change_sent(
    editor, qapp
):
    # DemoBridge has no .out (no live MIDI connection) - _handle_program_change
    # takes the "demo bridge" branch and emits change_sent without trying to
    # send anything (see program_editor_bridge.py's _handle_program_change).
    # This confirms that path - and the PRGNUM read it does before bailing -
    # still resolves cleanly against the real dependency.
    _wait_for_multi_parts_load(editor, qapp)
    sent = []
    editor._worker.change_sent.connect(lambda *a: sent.append(a))

    program_combo = editor._multi_program_combos[0]
    other_index = 1 if program_combo.currentIndex() != 1 else 2
    program_combo.setCurrentIndex(other_index)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: sent)

    assert editor._failures.events == []


# --- Contract checks for AGENTS.md's documented widget-range overrides ---
#
# AGENTS.md records that key_filter_track_knob (K_FREQ, -24..24) and
# bend_down_combo (B_PTCHD, 0..24) deliberately use a range that differs
# from what s3k.params itself declares, based on hardware measurements taken
# on this project's own S3000-series unit rather than the dependency's
# transcribed manual values (reconfirmed for B_PTCHD by the user directly,
# both directions, after the mismatch below was found). The two cases
# aren't symmetric:
#
# - K_FREQ's widget range (-24..24) is a NARROWER subset of s3k.params'
#   declared range (-30..99 as of the pinned rev) - always safe to write
#   with no override needed.
# - B_PTCHD's widget range (0..24) is WIDER than s3k.params' declared range
#   (0..12 as of the pinned rev) - values above 12 fail encode_field's own
#   range check (see s3k.params._encode_one) against the raw, unmodified
#   Parameter. program_editor_bridge.py's _HARDWARE_RANGE_OVERRIDES/
#   _lookup_for_write patches a corrected copy of B_PTCHD's Parameter
#   (maximum 12 -> 24) in front of every write, so the app itself no longer
#   hits this - but p.lookup("B_PTCHD", "program") on its own still returns
#   the dependency's original 0..12 unless routed through that helper.


def test_key_filter_track_widget_boundary_values_are_accepted_by_pinned_s3k_params():
    bridge = DemoBridge()
    param = p.lookup("K_FREQ", "keygroup")
    for boundary in (-24, 24):
        bridge.set_parameter(param, 0, boundary, keygroup=0)
        assert bridge.get_parameter(param, 0, keygroup=0) == boundary


def test_bend_down_raw_s3k_params_lookup_still_declares_the_narrower_0_to_12():
    # p.lookup() on its own (bypassing program_editor_bridge.py's override
    # helper) still returns what the pinned s3ked rev actually declares -
    # confirms the override below is doing real work, not patching
    # something that was never a problem. If a future s3ked pin bump widens
    # this itself, this assertion (not the round-trip test below) is what
    # will start failing - that's the signal the override in
    # program_editor_bridge.py can be retired, not a regression to chase.
    param = p.lookup("B_PTCHD", "program")
    assert (param.minimum, param.maximum) == (0, 12)

    bridge = DemoBridge()
    with pytest.raises(ValueError, match="outside 0..12"):
        bridge.set_parameter(param, 0, 20)


def test_bend_down_combo_above_12_reaches_hardware_via_the_range_override(
    editor, qapp
):
    # the UI-level payoff of _lookup_for_write's override: turning "Bend
    # down" up to 24 (confirmed on real hardware, both directions) now
    # actually reaches the sampler instead of silently failing to write
    # while the combo itself kept showing the unsaved value - see
    # program_editor_bridge.py's _HARDWARE_RANGE_OVERRIDES comment.
    bridge = editor._demo_bridge
    editor.bend_down_combo.setCurrentIndex(20)
    editor._flush_write("B_PTCHD")
    editor._worker.wait_until_idle()
    _pump_until(
        qapp,
        lambda: bridge.get_parameter(p.lookup("B_PTCHD", "program"), 0) == 20,
    )

    assert editor._failures.events == []


def test_demo_bridge_mono_legato_combo_round_trips_through_real_encode_decode(
    editor, qapp
):
    bridge = editor._demo_bridge
    assert bridge.get_parameter(p.lookup("LEGATO", "program"), 0) != 1

    editor.mono_legato_combo.setCurrentIndex(1)  # "On"
    editor._flush_write("LEGATO")
    editor._worker.wait_until_idle()
    _pump_until(
        qapp, lambda: bridge.get_parameter(p.lookup("LEGATO", "program"), 0) == 1
    )

    assert editor._failures.events == []


def test_demo_bridge_program_number_display_offset_round_trips(editor, qapp):
    # program_number_spinbox displays raw+1 and writes displayed-1 (see
    # AGENTS.md's "STUNO/PRGNUM raw-vs-display quirks") - confirms that
    # offset survives a real encode/decode round trip, not just FakeBridge
    # recording whatever value it was handed.
    bridge = editor._demo_bridge
    editor.program_number_spinbox.setValue(5)  # -> raw PRGNUM 4
    editor._flush_write("PRGNUM")
    editor._worker.wait_until_idle()
    _pump_until(
        qapp, lambda: bridge.get_parameter(p.lookup("PRGNUM", "program"), 0) == 4
    )

    assert editor._failures.events == []


# --- Samples tab: header math against the real dependency -------------------
#
# DemoBridge's own sample headers start entirely zeroed (SLNGTH included),
# which collapses every marker/spinbox to a disabled frame-0 state the same
# way AGENTS.md's "Sample loop type, root note, and rename/delete" section
# describes - so these tests seed a plausible header directly through the
# real bridge first (same shape FakeBridge's fixture data already uses),
# then drive the UI exactly as a user would against real hardware.


def _seed_sample_zero_header(bridge):
    bridge.set_parameter(p.lookup("SLNGTH", "sample"), 0, 10000)
    bridge.set_parameter(p.lookup("SSTART", "sample"), 0, 100)
    bridge.set_parameter(p.lookup("SMPEND", "sample"), 0, 9999)
    bridge.set_parameter(p.lookup("LOOPAT1", "sample"), 0, 8000)
    bridge.set_parameter(p.lookup("LLNGTH1", "sample"), 0, 3000 * 65536)


def test_demo_bridge_sample_loop_markers_round_trip_through_real_loopat1_llngth1_math(
    editor, qapp
):
    # LOOPAT1 is the loop's END (not its start) and LLNGTH1 is 32.16 fixed
    # point, not a plain frame count - see AGENTS.md's "Samples tab: loop
    # points". Both the read-side derivation and the write-side re-encoding
    # need to survive the real s3k.params encode_field/decode_field, not
    # just FakeBridge echoing back whatever was handed to it.
    bridge = editor._demo_bridge
    _seed_sample_zero_header(bridge)

    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())

    assert editor.waveform_view.markers() == {
        "start": 100, "loop_start": 5000, "loop_end": 8000, "end": 9999,
    }

    # drags "end" from 9999 to 6000 - pushes loop_end (8000) down to 6000
    # along with it; loop_start (5000) is untouched
    editor.waveform_view.set_marker("end", 6000)
    m = editor.waveform_view.markers()
    assert m == {"start": 100, "loop_start": 5000, "loop_end": 6000, "end": 6000}
    editor._on_waveform_marker_committed(
        "end", m["start"], m["loop_start"], m["loop_end"], m["end"]
    )
    editor._worker.wait_until_idle()
    _pump_until(
        qapp,
        lambda: bridge.get_parameter(p.lookup("LLNGTH1", "sample"), 0) == 1000 * 65536,
    )

    assert bridge.get_parameter(p.lookup("SMPEND", "sample"), 0) == 6000
    assert bridge.get_parameter(p.lookup("LOOPAT1", "sample"), 0) == 6000

    # force a fresh header read (bypassing the cache) to also exercise the
    # DECODE side against the values just written - closes the round trip
    editor._worker.submit_sample_detail(0)
    editor._worker.wait_until_idle()
    _pump_until(
        qapp,
        lambda: editor.waveform_view.markers()
        == {"start": 100, "loop_start": 5000, "loop_end": 6000, "end": 6000},
    )

    assert editor._failures.events == []


def test_demo_bridge_sample_tune_negative_value_round_trips_through_real_sign_extension(
    editor, qapp
):
    # STUNO is declared unsigned 0..65535 by s3k.params, with no automatic
    # sign-extension on decode (unlike VTUNO/PTUNO/KGTUNO) - the app itself
    # manually sign-extends on read and wraps to unsigned two's-complement
    # on write (see _sample_tune_offset_to_semitones/
    # _semitones_to_sample_tune_offset, AGENTS.md's "STUNO/PRGNUM raw-vs-
    # display quirks"). This is the one field on this page where getting
    # the real encode_field's range check wrong would surface immediately.
    bridge = editor._demo_bridge
    _seed_sample_zero_header(bridge)

    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())

    editor.sample_tune_spinbox.setValue(-10.0)
    editor._commit_sample_tune()
    editor._worker.wait_until_idle()
    expected_raw = editor._semitones_to_sample_tune_offset(-10.0)
    _pump_until(
        qapp,
        lambda: bridge.get_parameter(p.lookup("STUNO", "sample"), 0) == expected_raw,
    )
    # confirms the write actually wrapped to a real unsigned raw value, not
    # a negative one encode_field would otherwise reject outright
    assert expected_raw > 0

    # force a fresh header read to exercise the manual sign-extension on
    # the way back too
    editor._worker.submit_sample_detail(0)
    editor._worker.wait_until_idle()
    _pump_until(
        qapp,
        lambda: editor.sample_tune_spinbox.value() == pytest.approx(-10.0, abs=0.01),
    )

    assert editor._failures.events == []


def test_demo_bridge_sample_loop_type_and_root_note_round_trip(editor, qapp):
    bridge = editor._demo_bridge
    _seed_sample_zero_header(bridge)

    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())

    editor.sample_loop_type_combo.setCurrentIndex(2)  # "No looping"
    editor.sample_root_note_spinbox.setValue(72)
    editor._commit_sample_root_note()
    editor._worker.wait_until_idle()
    _pump_until(
        qapp,
        lambda: bridge.get_parameter(p.lookup("SPTYPE", "sample"), 0) == 2
        and bridge.get_parameter(p.lookup("SPITCH", "sample"), 0) == 72,
    )

    assert editor._failures.events == []
