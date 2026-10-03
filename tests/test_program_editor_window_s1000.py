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
from PySide6.QtCore import QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel, QWidget

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


def _dispose(editor):
    # stop the worker AND delete the window. Not deleting leaks every test's
    # editor for the rest of the run: each one stays alive, stays connected
    # to theme.notifier, and is restyled on every theme change - which made
    # the theme tests late in this file slower with every editor before them
    editor._worker.stop()
    editor._worker.wait()
    editor.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


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
    _dispose(editor)


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


def _label_above(editor, widget):
    # the QLabel in the same column layout as `widget`
    for label in widget.parentWidget().findChildren(QLabel):
        if label.text() in ("Bend up", "Bend range"):
            return label
    raise AssertionError("no bend label found")


def test_bend_is_one_range_not_a_direction_on_an_s1000(editor):
    # the S1000 has a single B_PTCH field, 0-12 semitones - no separate
    # up/down like the S3000's B_PTCH/B_PTCHD
    assert editor.bend_up_combo.count() == 13
    assert editor.bend_up_combo.itemText(12) == "12 st"
    assert _label_above(editor, editor.bend_up_combo).text() == "Bend range"
    assert "S1000" in editor.bend_up_combo.toolTip()


def test_value_ranges_match_the_s1000s_narrower_ones(editor):
    # POLYPH 1-16 (S3000: 1-32); note fields 24-127 = C0-G8 (S3000: 21-127)
    assert editor.polyph_combo.count() == 16
    assert editor.polyph_combo.itemData(15) == 16
    for spinbox in (
        editor.note_lo_spinbox,
        editor.note_hi_spinbox,
        editor.sample_root_note_spinbox,
    ):
        assert spinbox.minimum() == 24 and spinbox.maximum() == 127


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
        assert editor.polyph_combo.count() == 32
        assert editor.sample_root_note_spinbox.minimum() == 21
        assert _label_above(editor, editor.bend_up_combo).text() == "Bend up"
        assert all(k.isEnabled() for k in editor._env2_rate_knobs)
        assert editor.windowTitle() == "AKAISDS - Program Editor"
    finally:
        _dispose(editor)


def test_s2000_s3000_window_still_has_the_four_stage_envelope_2(qapp):
    from s3ked.demo import DemoBridge

    editor = _make_editor(qapp, "akai_s2000_s3000", DemoBridge())
    try:
        assert not hasattr(editor, "env2_adsr_graph")
        assert len(editor._env2_rate_knobs) == 4
    finally:
        _dispose(editor)


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


# --- S1000 controller routing cards + Pan LFO ---------------------------------------------------


def _find_control(editor, field, region):
    for knob, label, f, r in editor._s1000_controls:
        if f == field and r == region:
            return knob, label
    raise AssertionError(f"no S1000 controller knob for {region}/{field}")


def _poke(fake, region, name, value, keygroup=0):
    # change a field on the fake sampler behind the adapter's back, the way
    # a front-panel edit would, using the S1000's own (corrected) range
    from core.s1000_bridge import s1000_param

    param = s1000_param(p.lookup(name, region))
    target = (
        fake.programs[0]["block"]
        if region == "program"
        else fake.programs[0]["keygroups"][keygroup]
    )
    target[param.offset : param.end] = p.encode_field(param, value)


def _flush_all_writes(editor, qapp):
    for key in list(editor._pending_writes):
        editor._flush_write(key)
    editor._worker.wait_until_idle()
    for _ in range(10):
        qapp.processEvents()


def _raw(fake, region, name, keygroup=0):
    offset = p.lookup(name, region).offset
    if region == "program":
        return fake.programs[0]["block"][offset]
    return fake.programs[0]["keygroups"][keygroup][offset]


def test_pan_lfo_card_replaces_lfo2_on_an_s1000(editor):
    # the S1000 has a Pan LFO (PANRAT/PANDEP/PANDEL - the same three fields
    # the S3000's LFO2 card edits) but not LFO2's shape/retrigger
    for knob in (editor.lfo2_rate_knob, editor.lfo2_depth_knob, editor.lfo2_delay_knob):
        assert knob.isVisibleTo(editor)
    assert not editor.lfo2_shape_combo.isVisibleTo(editor)
    assert not editor.lfo2_trig_combo.isVisibleTo(editor)
    card = _section_card(editor.lfo2_rate_knob)
    assert card.findChild(QLabel, "sectionHeader").text() == "Pan LFO"
    texts = {l.text() for l in card.findChildren(QLabel)}
    assert {"Rate", "Depth", "Delay"} <= texts
    assert "LFO2 rate" not in texts


def test_pan_lfo_loads_and_writes_the_pan_lfo_fields(editor, qapp):
    editor._fake.programs[0]["block"][p.lookup("PANRAT", "program").offset] = 41
    editor._bridge.invalidate()
    editor._refresh_from_hardware()  # reloads program-level values
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.lfo2_rate_knob.value() == 41)

    editor.lfo2_depth_knob.setValue(66)
    _flush_all_writes(editor, qapp)
    assert _raw(editor._fake, "program", "PANDEP") == 66


def test_program_controller_knobs_have_the_s1000_ranges(editor):
    ranges = {
        field: (knob.minimum(), knob.maximum())
        for knob, _label, field, region in editor._s1000_controls
        if region == "program"
    }
    assert ranges == {
        "K_LOUD": (-50, 50), "P_LOUD": (-50, 50),
        "K_PANP": (-50, 50), "MW_PAN": (-50, 50),
        "P_PTCH": (-12, 12),
        "VELDEP": (0, 99), "PRSDEP": (0, 99), "MWLDEP": (0, 99),
        "K_LRAT": (-50, 50), "K_LDEP": (-50, 50), "K_LDEL": (-50, 50),
    }


def test_program_controller_knobs_load_signed_values_from_the_sampler(
    qapp, fake
):
    _poke(fake, "program", "K_LOUD", -20)
    _poke(fake, "program", "P_PTCH", -7)
    _poke(fake, "program", "MWLDEP", 88)
    _poke(fake, "program", "K_LDEL", 33)
    bridge = LoggingBridge(S1000Bridge(fake.bridge(timeout=0.3)))
    editor = _make_editor(qapp, "akai_s1000", bridge)
    try:
        for field, expected in (("K_LOUD", -20), ("P_PTCH", -7),
                                ("MWLDEP", 88), ("K_LDEL", 33), ("MW_PAN", 0)):
            knob, label = _find_control(editor, field, "program")
            assert knob.value() == expected, field
            assert label.text() == str(expected), field
    finally:
        _dispose(editor)


def test_editing_a_program_controller_writes_only_that_field(editor, qapp):
    before = bytes(editor._fake.programs[0]["block"])
    knob, _label = _find_control(editor, "K_PANP", "program")
    knob.setValue(-12)
    _flush_all_writes(editor, qapp)

    after = bytes(editor._fake.programs[0]["block"])
    offset = p.lookup("K_PANP", "program").offset
    assert after[offset] == (-12) & 0xFF
    assert after[:offset] + after[offset + 1 :] == before[:offset] + before[offset + 1 :]
    assert editor._fake.ignored_ops == []


def test_keygroup_controller_knobs_load_for_the_selected_keygroup(editor, qapp):
    _poke(editor._fake, "keygroup", "E_FREQ", 31, keygroup=1)
    _poke(editor._fake, "keygroup", "V_ATT2", -15, keygroup=1)
    _poke(editor._fake, "keygroup", "K_DAR1", 9, keygroup=1)
    _poke(editor._fake, "keygroup", "E_FREQ", -44, keygroup=0)
    editor._bridge.invalidate()

    editor.detail_stack.setCurrentIndex(1)
    _select_keygroup(editor, qapp, 1, expect_lo=60)
    for field, expected in (("E_FREQ", 31), ("V_ATT2", -15), ("K_DAR1", 9)):
        assert _find_control(editor, field, "keygroup")[0].value() == expected, field

    editor._bridge.invalidate()
    _select_keygroup(editor, qapp, 0, expect_lo=24)
    assert _find_control(editor, "E_FREQ", "keygroup")[0].value() == -44


def test_editing_a_keygroup_controller_writes_to_the_selected_keygroup(editor, qapp):
    editor.detail_stack.setCurrentIndex(1)
    _select_keygroup(editor, qapp, 1, expect_lo=60)
    knob, _label = _find_control(editor, "V_REL2", "keygroup")
    knob.setValue(-30)
    _flush_all_writes(editor, qapp)

    assert _raw(editor._fake, "keygroup", "V_REL2", keygroup=1) == (-30) & 0xFF
    assert _raw(editor._fake, "keygroup", "V_REL2", keygroup=0) == 0
    assert editor._fake.ignored_ops == []


def test_loading_controller_values_does_not_echo_writes_back(editor, qapp):
    # valueChanged schedules a debounced write - a load must set the knobs
    # with signals blocked, or every selection would rewrite what it just read
    editor.detail_stack.setCurrentIndex(1)
    _poke(editor._fake, "keygroup", "E_FREQ", 12, keygroup=1)
    editor._bridge.invalidate()
    writes_before = editor._fake.writes
    _select_keygroup(editor, qapp, 1, expect_lo=60)
    editor._worker.wait_until_idle()
    for _ in range(10):
        qapp.processEvents()
    assert not getattr(editor, "_pending_writes", {})
    assert editor._fake.writes == writes_before


def test_s2000_s3000_window_has_no_s1000_controller_cards(qapp):
    from s3ked.demo import DemoBridge

    editor = _make_editor(qapp, "akai_s2000_s3000", DemoBridge())
    try:
        assert editor._s1000_controls == []
        # and doesn't pay for reading their fields
        assert "K_LOUD" not in editor._worker._program_fields
        assert "E_FREQ" not in editor._worker._keygroup_fields
        card = _section_card(editor.lfo2_rate_knob)
        assert card.findChild(QLabel, "sectionHeader").text() == "LFO2"
        assert editor.lfo2_shape_combo.isVisibleTo(editor)
    finally:
        _dispose(editor)


# --- controller grids are styled like the S2000/S3000 mod matrix -----------------------------------


def _s1000_matrices(editor):
    # the S3000's (hidden) mod matrices are _ModMatrixGrid too - these are the
    # ones that actually hold S1000 controller knobs
    from ui.program_editor_window import _ModMatrixGrid

    return [
        matrix
        for matrix in editor.findChildren(_ModMatrixGrid)
        if any(matrix.isAncestorOf(knob) for knob, *_ in editor._s1000_controls)
    ]


def test_each_controller_grid_is_a_zebra_striped_mod_matrix(editor):
    matrices = _s1000_matrices(editor)
    # program Controllers, keygroup Controllers, keygroup Envelope response
    assert len(matrices) == 3
    assert sorted(m._data_row_count for m in matrices) == [2, 4, 6]
    for matrix in matrices:
        assert matrix._header_row_count == 1
        # a separator between the label column and every data column
        columns = len(matrix._column_boundaries)
        assert matrix._column_boundaries == [(c, c + 1) for c in range(columns)]


def test_controller_columns_share_the_width_and_the_label_column_does_not(editor):
    for matrix in _s1000_matrices(editor):
        grid = matrix._grid
        assert grid.columnStretch(0) == 0
        data_columns = len(matrix._column_boundaries)
        assert [grid.columnStretch(c) for c in range(1, data_columns + 1)] == [1] * data_columns
        # fixed label column: sized to its longest label, not stretched
        label = grid.itemAtPosition(1, 0).widget()
        assert label.minimumWidth() == label.maximumWidth() > 0


def test_controller_columns_widen_with_the_window(editor, qapp):
    editor.resize(1250, 900)
    editor.show()
    qapp.processEvents()
    matrix = next(m for m in _s1000_matrices(editor) if m._data_row_count == 6)
    editor.detail_stack.setCurrentIndex(0)
    qapp.processEvents()

    def column_width():
        return matrix._grid.cellRect(0, 2).width()

    narrow = column_width()
    editor.resize(1750, 900)
    qapp.processEvents()
    assert column_width() > narrow + 50
    editor.hide()


def test_label_column_is_sized_per_grid(editor):
    # the keygroup tab's two grids have very different label lengths ("Pitch"
    # vs "Envelope 2 (filter)") - one shared width would leave a gap in one
    widths = {
        matrix._data_row_count: matrix._grid.itemAtPosition(1, 0).widget().maximumWidth()
        for matrix in _s1000_matrices(editor)
    }
    assert widths[2] > widths[4]  # envelope response labels are longer


class _FakeThemeApp:
    # a real QApplication.setStyleSheet() restyles every live widget in the
    # process (slow once earlier tests have left windows around) - these
    # tests only need theme.py to switch palettes and notify
    def setStyle(self, name):
        pass

    def setStyleSheet(self, sheet):
        pass


# --- colors baked at build time follow a live theme switch ---------------------------------------


def _swatch_colors(editor, kind):
    return [
        label.styleSheet()
        for label in editor.findChildren(QLabel)
        if label.property("swatchKind") == kind
    ]


def test_keygroup_swatches_recolor_when_the_theme_changes(editor, qapp, monkeypatch, tmp_path):
    from ui import theme

    monkeypatch.setattr(theme, "_GENERATED_ICONS_DIR", str(tmp_path / "icons"))
    saved = (theme._active_palette, theme._app, theme._preference)
    try:
        theme.apply_to_app(_FakeThemeApp(), "dark")
        assert editor.keygroup_list.count() == 2
        dark = _swatch_colors(editor, "keygroup")
        assert len(dark) == 2
        assert theme.DARK_PALETTE["keygroup_color_1"] in dark[0]

        theme.set_theme_preference("light")
        light = _swatch_colors(editor, "keygroup")
        assert theme.LIGHT_PALETTE["keygroup_color_1"] in light[0]
        assert theme.LIGHT_PALETTE["keygroup_color_2"] in light[1]
        assert light != dark
    finally:
        QGuiApplication.styleHints().unsetColorScheme()
        theme._active_palette, theme._app, theme._preference = saved


def test_marker_legend_swatches_recolor_when_the_theme_changes(editor, qapp, monkeypatch, tmp_path):
    from ui import theme

    monkeypatch.setattr(theme, "_GENERATED_ICONS_DIR", str(tmp_path / "icons"))
    saved = (theme._active_palette, theme._app, theme._preference)
    try:
        theme.apply_to_app(_FakeThemeApp(), "dark")
        theme.set_theme_preference("light")
        boundary = _swatch_colors(editor, "marker_boundary")
        loop = _swatch_colors(editor, "marker_loop")
        assert len(boundary) == 2 and len(loop) == 2  # start/end, loop start/end
        assert all(theme.LIGHT_PALETTE["text_disabled"] in s for s in boundary)
        assert all(theme.LIGHT_PALETTE["keygroup_color_3"] in s for s in loop)
        # a loop-less sample greys the loop swatches - and that must survive
        # a theme change too, not snap back to the loop color
        editor._loop_markers_enabled = False
        theme.set_theme_preference("dark")
        assert all(theme.DARK_PALETTE["text_disabled"] in s for s in _swatch_colors(editor, "marker_loop"))
    finally:
        QGuiApplication.styleHints().unsetColorScheme()
        theme._active_palette, theme._app, theme._preference = saved


def test_a_closed_editor_stops_listening_for_theme_changes(qapp, fake, monkeypatch, tmp_path):
    from ui import theme

    monkeypatch.setattr(theme, "_GENERATED_ICONS_DIR", str(tmp_path / "icons"))
    # counted at the CLASS level, before the editor exists, so the signal
    # connection made in __init__ is to this very function
    calls = []
    original = ProgramEditorWindow._refresh_themed_swatches
    monkeypatch.setattr(
        ProgramEditorWindow,
        "_refresh_themed_swatches",
        lambda self: calls.append(self) or original(self),
    )
    bridge = LoggingBridge(S1000Bridge(fake.bridge(timeout=0.3)))
    editor = _make_editor(qapp, "akai_s1000", bridge)
    saved = (theme._active_palette, theme._app, theme._preference)
    try:
        theme.apply_to_app(_FakeThemeApp(), "dark")
        theme.set_theme_preference("light")
        # by identity - editors leaked by OTHER test files are still
        # connected and would be counted too
        assert editor in calls, "an open editor should hear about theme changes"

        editor.close()
        calls.clear()
        theme.set_theme_preference("dark")
        theme.set_theme_preference("light")
        assert editor not in calls  # closed: no longer listening
    finally:
        QGuiApplication.styleHints().unsetColorScheme()
        theme._active_palette, theme._app, theme._preference = saved
        _dispose(editor)


# --- renames must not land on another item's name (S1000 deletes the other) ----------------------


@pytest.fixture
def warnings(monkeypatch):
    from ui import program_editor_window

    shown = []
    monkeypatch.setattr(
        program_editor_window.QMessageBox,
        "warning",
        lambda parent, title, text, *a: shown.append((title, text)),
    )
    return shown


def _settle(editor, qapp):
    editor._worker.wait_until_idle()
    for _ in range(10):
        qapp.processEvents()


def test_renaming_a_program_to_an_existing_name_is_refused(editor, qapp, warnings, monkeypatch):
    monkeypatch.setattr(editor, "_prompt_program_name", lambda current: "PAD PROG")
    writes = editor._fake.writes
    editor.program_list.setCurrentRow(0)
    editor._confirm_rename_program()
    _settle(editor, qapp)

    assert editor._fake.writes == writes  # nothing sent
    assert editor._fake.deleted_by_name_clash == []
    assert editor.program_list.item(0).text() == "DRUMS"
    assert editor._bridge.program_list() == ["DRUMS", "PAD PROG"]
    assert warnings and "Rename Program" in warnings[0][0]
    assert "deletes the existing one" in warnings[0][1]


def test_renaming_a_program_to_a_free_name_still_works(editor, qapp, warnings, monkeypatch):
    monkeypatch.setattr(editor, "_prompt_program_name", lambda current: "BREAKS")
    editor.program_list.setCurrentRow(0)
    editor._confirm_rename_program()
    _settle(editor, qapp)

    assert warnings == []
    assert editor.program_list.item(0).text() == "BREAKS"
    assert editor._fake.deleted_by_name_clash == []
    assert editor._bridge.program_list() == ["BREAKS", "PAD PROG"]


def test_typing_an_existing_program_name_is_reverted_not_written(editor, qapp, warnings):
    editor.program_list.setCurrentRow(0)
    _settle(editor, qapp)
    writes = editor._fake.writes
    editor.program_name_edit.setText("PAD PROG")
    editor._on_program_name_typed("PAD PROG")  # the live-typing handler
    editor._commit_program_name()  # Enter / click away
    _settle(editor, qapp)

    assert editor._fake.writes == writes
    assert editor._fake.deleted_by_name_clash == []
    assert editor.program_name_edit.text() == "DRUMS"  # restored
    assert editor.program_list.item(0).text() == "DRUMS"
    assert warnings


def test_typing_a_free_program_name_is_written(editor, qapp, warnings):
    editor.program_list.setCurrentRow(0)
    _settle(editor, qapp)
    editor.program_name_edit.setText("LOOPS")
    editor._on_program_name_typed("LOOPS")
    editor._commit_program_name()
    _settle(editor, qapp)

    assert warnings == []
    assert editor._bridge.program_list() == ["LOOPS", "PAD PROG"]
    # and the new name is now the baseline: committing it again is a no-op
    writes = editor._fake.writes
    editor._commit_program_name()
    _settle(editor, qapp)
    assert editor._fake.writes == writes


def test_clicking_away_from_an_unchanged_program_name_writes_nothing(editor, qapp, warnings):
    # editingFinished fires on any click-away - on an S1000 every write
    # re-sends the whole program block, so an unchanged name must not write
    editor.program_list.setCurrentRow(0)
    _settle(editor, qapp)
    writes = editor._fake.writes
    editor._commit_program_name()
    _settle(editor, qapp)
    assert editor._fake.writes == writes
    assert warnings == []


def test_renaming_a_sample_to_an_existing_name_is_refused(editor, qapp, warnings, monkeypatch):
    monkeypatch.setattr(editor, "_prompt_sample_name", lambda current: "SNARE")
    _pump_until(qapp, lambda: len(editor._sample_list) == 3)
    editor.sample_list_widget.setCurrentRow(0)
    writes = editor._fake.writes
    editor._confirm_rename_sample()
    _settle(editor, qapp)

    assert editor._fake.writes == writes
    assert editor._fake.deleted_by_name_clash == []
    assert editor._sample_list == ["KICK", "SNARE", "PAD"]
    assert warnings and "Rename Sample" in warnings[0][0]


def test_renaming_a_sample_to_a_free_name_still_works(editor, qapp, warnings, monkeypatch):
    monkeypatch.setattr(editor, "_prompt_sample_name", lambda current: "KICK2")
    _pump_until(qapp, lambda: len(editor._sample_list) == 3)
    editor.sample_list_widget.setCurrentRow(0)
    editor._confirm_rename_sample()
    _settle(editor, qapp)

    assert warnings == []
    assert editor._bridge.sample_list() == ["KICK2", "SNARE", "PAD"]


def test_the_s2000_s3000_window_does_not_apply_the_s1000_rename_guard(qapp, warnings, monkeypatch):
    # there a duplicate name is ambiguous, not destructive - behaviour unchanged
    from s3ked.demo import DemoBridge

    editor = _make_editor(qapp, "akai_s2000_s3000", DemoBridge())
    try:
        names = [editor.program_list.item(i).text() for i in range(editor.program_list.count())]
        assert len(names) >= 2
        monkeypatch.setattr(editor, "_prompt_program_name", lambda current: names[1])
        editor.program_list.setCurrentRow(0)
        editor._confirm_rename_program()
        assert warnings == []
        assert editor.program_list.item(0).text() == names[1]
    finally:
        _dispose(editor)
