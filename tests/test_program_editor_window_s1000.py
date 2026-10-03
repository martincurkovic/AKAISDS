# ProgramEditorWindow in Akai S1000 mode, driven end to end against
# core/demo_s1000.py's FakeS1000 (real S3kBridge over fake MIDI ports, the
# real S1000Bridge adapter, the real BridgeWorker thread) - see
# test_s1000_bridge.py for the adapter on its own.
#
# What the S1000 doesn't have (modulation matrix, LFO2, portamento, Multi,
# ENV3, ...) is removed from the window once, at construction -
# ProgramEditorWindow._apply_s1000_gating. Visibility assertions use
# isHidden(), not isVisible(): the window is never shown here (see
# AGENTS.md's "Widget visibility assertions" note).

import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

import s3k.params as p

from core.demo_s1000 import FakeS1000
from core.program_editor_bridge import LoggingBridge
from core.s1000_bridge import S1000Bridge
from ui.program_editor_window import ProgramEditorWindow


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _pump_until(qapp, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        qapp.processEvents()
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met before timeout")


class _Host(QWidget):
    # stands in for MainWindow: the editor only reads sampler_controller off
    # it (the model, and is_transfer_busy() in a few guards)
    def __init__(self, model):
        super().__init__()
        self.sampler_controller = SimpleNamespace(
            sampler_model=model, is_transfer_busy=lambda: False
        )


def _make_editor(qapp, model, bridge):
    host = _Host(model)
    editor = ProgramEditorWindow(host, bridge=bridge)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.program_list.count() > 0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.program_list.currentRow() == 0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.keygroup_list.count() > 0)
    editor._worker.wait_until_idle()
    for _ in range(10):
        qapp.processEvents()
    return editor


@pytest.fixture
def fake():
    return FakeS1000()


@pytest.fixture
def editor(qapp, fake):
    bridge = LoggingBridge(S1000Bridge(fake.bridge(timeout=0.3)))
    editor = _make_editor(qapp, "akai_s1000", bridge)
    editor._fake = fake
    yield editor
    editor._worker.stop()
    editor._worker.wait()


def test_loads_programs_keygroups_and_samples_from_an_s1000(editor):
    assert [editor.program_list.item(i).text() for i in range(2)] == [
        "DRUMS",
        "PAD PROG",
    ]
    assert editor.keygroup_list.count() == 2  # DRUMS has two keygroups
    assert editor.sample_list_widget.count() == 3
    assert editor.loud_knob.value() == 80
    assert editor._fake.ignored_ops == []


def test_selecting_a_keygroup_loads_its_detail(editor, qapp):
    editor.keygroup_list.setCurrentRow(1)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.note_lo_spinbox.value() == 60)
    assert editor.note_hi_spinbox.value() == 127
    assert editor._fake.ignored_ops == []


def test_editing_a_knob_writes_the_whole_block_back(editor, qapp):
    before = bytes(editor._fake.programs[0]["block"])
    editor.loud_knob.setValue(33)
    editor._flush_write(next(iter(editor._pending_writes)))
    editor._worker.wait_until_idle()
    for _ in range(10):
        qapp.processEvents()
    after = bytes(editor._fake.programs[0]["block"])
    loud = p.lookup("PRLOUD", "program").offset
    assert after[loud] == 33
    assert len(after) == len(before)
    assert after[:loud] == before[:loud]
    assert after[loud + 1 :] == before[loud + 1 :]


# --- S3000-only features are gated ------------------------------------------------------


def test_s3000_only_cards_are_hidden(editor):
    # Program tab: LFO2, Portamento, the whole Modulation matrix.
    # isVisibleTo, not isHidden, for widgets inside a card - it's the CARD
    # that was hidden, which only hides its children transitively
    for widget in (
        editor.lfo2_rate_knob,
        editor.portamento_enable_combo,
        editor.mono_legato_combo,
        editor.mod_pan1_combo,
        editor.mod_pitch_combo,
    ):
        assert not widget.isVisibleTo(editor)
    # while the cards an S1000 does have stay
    for widget in (editor.lfo_rate_knob, editor.pan_knob, editor.polyph_combo):
        assert widget.isVisibleTo(editor)

    # Keygroup tab: its own Modulation card, and the 4-stage ENV2 graph.
    # That page isn't the current one by default, so show it for real
    # (otherwise every widget on it is trivially "not visible")
    editor.detail_stack.setCurrentIndex(1)
    assert editor.cutoff_knob.isVisibleTo(editor)  # sanity: page IS showing
    assert not editor.mod_filt1_source_mirror.isVisibleTo(editor)
    assert editor.env2_graph.isHidden()


def test_s3000_only_individual_controls_are_hidden(editor):
    # (laid out in a nested layout rather than a card of their own, so they
    # were hidden one by one - isHidden is exact here)
    assert editor.lfo_shape_combo.isHidden()
    assert editor.bend_down_combo.isHidden()
    assert editor.resonance_knob.isHidden()
    # while the ones an S1000 does have stay
    assert not editor.bend_up_combo.isHidden()
    assert not editor.cutoff_knob.isHidden()
    assert not editor.lfo1_sync_combo.isHidden()


def test_bend_up_is_limited_to_the_s1000s_12_semitones(editor):
    assert editor.bend_up_combo.count() == 13
    assert editor.bend_up_combo.itemText(12) == "12 st"


def _set_env2(fake, keygroup, **fields):
    block = fake.programs[0]["keygroups"][keygroup]
    for name, value in fields.items():
        param = p.lookup(name, "keygroup")
        block[param.offset : param.end] = p.encode_field(param, value)


def _select_keygroup(editor, qapp, row, expect_lo):
    editor.keygroup_list.setCurrentRow(row)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.note_lo_spinbox.value() == expect_lo)
    for _ in range(5):
        qapp.processEvents()


def test_envelope_2_is_an_adsr_on_an_s1000(editor, qapp):
    # the S1000's ENV2 is a plain ADSR (ATTAK2/DECAY2/SUSTN2/RELSE2 sit in
    # the same KDATA block as ENV1's four) - the S3000's 4-stage rate/level
    # grid and graph are replaced by an ENV1-style graph + four knobs
    _set_env2(editor._fake, 1, ATTAK2=11, DECAY2=22, SUSTN2=33, RELSE2=44)
    # changed "on the front panel" behind the adapter's back, after it
    # already cached this keygroup's block during the program load
    editor._bridge.invalidate()
    editor.detail_stack.setCurrentIndex(1)
    _select_keygroup(editor, qapp, 1, expect_lo=60)

    assert editor.env2_graph.isHidden()  # the 4-stage graph
    assert not any(k.isVisibleTo(editor) for k in editor._env2_rate_knobs)
    assert not any(k.isVisibleTo(editor) for k in editor._env2_level_knobs)
    for knob in editor._env2_adsr_knobs:
        assert knob.isVisibleTo(editor) and knob.isEnabled()
    assert editor.env2_adsr_graph.isVisibleTo(editor)
    assert [k.value() for k in editor._env2_adsr_knobs] == [11, 22, 33, 44]
    assert [l.text() for l in editor._env2_adsr_value_labels] == ["11", "22", "33", "44"]


def test_editing_envelope_2_writes_the_adsr_fields(editor, qapp):
    editor.detail_stack.setCurrentIndex(1)
    _select_keygroup(editor, qapp, 0, expect_lo=24)
    for knob, value in zip(editor._env2_adsr_knobs, (5, 6, 7, 8)):
        knob.setValue(value)
    for key in list(editor._pending_writes):
        editor._flush_write(key)
    editor._worker.wait_until_idle()
    for _ in range(10):
        qapp.processEvents()
    block = editor._fake.programs[0]["keygroups"][0]
    got = [block[p.lookup(n, "keygroup").offset] for n in ("ATTAK2", "DECAY2", "SUSTN2", "RELSE2")]
    assert got == [5, 6, 7, 8]
    # ENV1 untouched, and the S3000-only stage fields never written
    assert editor._fake.ignored_ops == []


def _section_card(widget):
    while widget.objectName() != "sectionCard":
        widget = widget.parentWidget()
    return widget


def test_envelope_cards_are_equal_height_and_not_padded_by_the_hidden_grid(editor):
    # regression: hiding the 4-stage grid/graph in a not-yet-shown window
    # left their stale size hints in the card's layout, so the card stayed
    # pinned ~406px tall (vs ~225px for the same content as Envelope 1) with
    # a large empty gap below it
    card1 = _section_card(editor.attack1_knob)
    card2 = _section_card(editor.attack2_knob)
    assert card1.minimumHeight() == card2.minimumHeight()
    assert card1.minimumHeight() < 300


def test_multi_tab_is_hidden_and_never_loaded(editor, fake):
    assert not editor.main_tabs.isTabVisible(0)
    assert editor.main_tabs.isTabVisible(1)
    assert editor.main_tabs.currentIndex() == 1
    # the Multi's own SysEx (0x41) must never have been asked for
    assert 0x41 not in [op for op, _payload in fake.received]
    assert fake.ignored_ops == []


def test_multi_tab_shortcut_is_gone_from_the_window_menu(editor):
    for menu_action in editor.menuBar().actions():
        menu = menu_action.menu()
        if menu is not None:
            assert editor._multi_tab_action not in menu.actions()


def test_duplicate_program_and_keygroup_are_enabled(editor):
    editor.keygroup_list.setCurrentRow(0)
    editor._update_list_context_actions_enabled()
    assert editor._duplicate_program_action.isEnabled()
    assert editor._duplicate_keygroup_action.isEnabled()
    assert editor._rename_program_action.isEnabled()
    assert editor._delete_keygroup_action.isEnabled()


def test_window_title_says_experimental(editor):
    assert "S1000" in editor.windowTitle()


# --- the S2000/S3000 window is unchanged ----------------------------------------------------


def test_s2000_s3000_window_keeps_every_card(qapp):
    from s3ked.demo import DemoBridge

    editor = _make_editor(qapp, "akai_s2000_s3000", DemoBridge())
    try:
        assert editor.main_tabs.isTabVisible(0)
        assert not editor.lfo_shape_combo.isHidden()
        assert not editor.bend_down_combo.isHidden()
        assert not editor.resonance_knob.isHidden()
        assert not editor.env2_graph.isHidden()
        assert editor.lfo2_rate_knob.isVisibleTo(editor)
        assert editor.mod_pan1_combo.isVisibleTo(editor)
        editor.detail_stack.setCurrentIndex(1)
        assert editor.mod_filt1_source_mirror.isVisibleTo(editor)
        assert editor.bend_up_combo.count() == 25
        assert all(k.isEnabled() for k in editor._env2_rate_knobs)
        assert editor.windowTitle() == "AKAISDS - Program Editor"
    finally:
        editor._worker.stop()
        editor._worker.wait()


def test_s2000_s3000_window_still_has_the_four_stage_envelope_2(qapp):
    from s3ked.demo import DemoBridge

    editor = _make_editor(qapp, "akai_s2000_s3000", DemoBridge())
    try:
        assert not hasattr(editor, "env2_adsr_graph")
        assert len(editor._env2_rate_knobs) == 4
    finally:
        editor._worker.stop()
        editor._worker.wait()


def test_the_two_visible_tabs_span_the_full_width(editor, qapp):
    # with Multi hidden, Programs + Samples must share the whole tab bar,
    # not be left-aligned at two thirds of it (FullWidthTabBar used to
    # divide by the total tab count, hidden ones included)
    editor.show()
    qapp.processEvents()
    bar = editor.main_tabs.tabBar()
    widths = [bar.tabRect(i).width() for i in (1, 2)]
    assert sum(widths) == editor.main_tabs.width()
    assert abs(widths[0] - widths[1]) <= 1
    editor.hide()
