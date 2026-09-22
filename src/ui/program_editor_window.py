import math
import os
import random
import struct
import sys
import tempfile
import time
import wave

if __name__ == "__main__":
    # running this file directly (not through main.py) puts src/ui on
    # sys.path, not src/ - so the ui./core. imports below would otherwise
    # fail with "No module named 'ui'"
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtGui import (
    Qt,
    QAction,
    QKeySequence,
    QRegularExpressionValidator,
    QPainter,
    QColor,
)
from PySide6.QtCore import QEventLoop, QTimer, QRegularExpression
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollBar,
    QSizePolicy,
    QVBoxLayout,
    QComboBox,
    QWidget,
    QStackedWidget,
    QDoubleSpinBox,
    QButtonGroup,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QScrollArea,
    QProgressBar,
)
from s3k.messages import AKAI_CHARSET, NAME_LENGTH
from ui.knob import Knob
from ui.note_spinbox import NoteSpinBox
from ui.qt_helpers import FullWidthTabBar
from ui.envelope_graph import ADSREnvelopeGraph, Envelope2Graph
from ui.keygroup_range_bar import KeygroupRangeBar, keygroup_color
from ui.waveform_view import WaveformView
from ui import theme
from ui.about_dialog import AboutDialog
from ui.update_helper import UpdateCheckRunner
from core import debug_log
from core import sample_editing
from core import sds_encoder
from core.midi_notes import midi_note_to_name
from core.program_editor_bridge import BridgeWorker, MULTI_PART_COUNT

# shared by every list's "Delete ..." QAction (program/keygroup/sample) -
# see the construction comment where the program/keygroup ones are built
# in __init__ for why this needs to be a list of three QKeySequences and
# WidgetShortcut-scoped.
_DELETE_SHORTCUTS = [
    QKeySequence(Qt.Key.Key_Delete),
    QKeySequence(Qt.Key.Key_Backspace),
    QKeySequence("Ctrl+Backspace"),
]

# (label, tooltip) per ZPLAY value, in raw-byte order (0-4) - labels are the
# abbreviated forms the front panel itself uses; tooltips spell out what
# each actually does on playback. Module-level (not just __init__-local)
# since both the zone-building loop and _update_zone_panels need it.
_LOOP_TYPE_OPTIONS = [
    (
        "As sample",
        "Uses whichever loop points and loop type are already stored "
        "on the sample itself, rather than overriding them for this zone.",
    ),
    (
        "Loop in release",
        "Loops continuously while the note is held, then finishes the "
        "current loop pass before moving into the amp envelope's "
        "release stage - avoids cutting off mid-loop on note-off.",
    ),
    (
        "Loop til release",
        "Loops continuously until note-off, then jumps straight into "
        "the release stage from wherever the loop currently is.",
    ),
    (
        "No loops",
        "Ignores the sample's loop points and plays straight through "
        "once, gated by the amp envelope as usual.",
    ),
    (
        "Play to sample end",
        "Ignores note-off and always plays through to the physical "
        "end of the sample, regardless of when the key is released.",
    ),
]

# (label, tooltip) per SPTYPE value, in raw-byte order (0-3) - SPTYPE is the
# SAMPLE's own playback/loop type (Samples tab), a different hardware field
# from ZPLAY above (a per-KEYGROUP-ZONE override of it), but the same four
# underlying behaviours: s3k.params' own transcription of SPTYPE's values=
# ("Normal looping"/"Loop until release"/"No looping"/"Play to sample end")
# lines up 1:1 with ZPLAY's "Loop in release"/"Loop til release"/"No loops"/
# "Play to sample end" once ZPLAY's own extra "As sample" choice (index 0
# there) is dropped - that one means "defer to the sample's own SPTYPE",
# which is meaningless when SPTYPE is the very thing being edited here.
# Tooltips are taken directly from _LOOP_TYPE_OPTIONS (indices 1-4) rather
# than retyped by hand, pairing each with SPTYPE's own label wording -
# keeps the explanatory text identical between the two comboboxes, and
# means it can't silently drift if _LOOP_TYPE_OPTIONS's own wording is
# ever revised.
_SAMPLE_PLAYBACK_TYPE_OPTIONS = [
    ("Normal looping", _LOOP_TYPE_OPTIONS[1][1]),  # ZPLAY's "Loop in release"
    ("Loop until release", _LOOP_TYPE_OPTIONS[2][1]),  # ZPLAY's "Loop til release"
    ("No looping", _LOOP_TYPE_OPTIONS[3][1]),  # ZPLAY's "No loops"
    ("Play to sample end", _LOOP_TYPE_OPTIONS[4][1]),  # same label, same tooltip
]

# (label, tooltip) per PORTYPE value - s3k.params transcribes this field as
# "PORTAMENTO TYPE" with no decoded values={} map (unlike most other
# program enum fields), so 0="Rate"/1="Time" is inferred from this whole
# table's own "0 is the default/off state" convention, not from a measured
# or documented mapping. Module-level for the same reason as
# _LOOP_TYPE_OPTIONS above - both __init__ and the load path need it.
_PORTAMENTO_TYPE_OPTIONS = [
    (
        "Rate",
        "The pitch glide always moves at a fixed speed, so a wider "
        "interval between notes takes proportionally longer to glide "
        "through.",
    ),
    (
        "Time",
        "The pitch glide always takes the same amount of time to "
        "complete, so a wider interval between notes glides faster to "
        "still finish in that time.",
    ),
]

# Raw values 0-13 of s3k.params.MOD_SOURCES, in combo-index order - shared
# by every "assignable source" field in the modulation matrix (MODSPAN*,
# MODSAMP*, MODSLFO*, MODSFILT*, MODSPITCH). Combo index doubles as the raw
# byte, same "index is the value" convention as lfo_shape_combo/ZPLAY/CP
# elsewhere in this file (see _wire_combo_write) - MOD_SOURCES happens to be
# contiguous 0-13 once value 14 is excluded below, so no itemData lookup is
# needed here either.
#
# Value 14 (env3) is deliberately left out: s3k.params' own hardware notes
# say Envelope 3 genuinely works on a base machine with no expansion board
# (it's the SECOND FILTER it can also target that needs the IB304F board,
# not env3 itself) - but this app has no Envelope 3 editor page yet, so
# offering it as a source with no way to shape its ADSR would be confusing
# rather than useful. Revisit once/if an Envelope 3 page exists.
_MOD_SOURCE_LABELS = [
    "None",
    "Modwheel",
    "Bend",
    "Pressure",
    "External",
    "Velocity",
    "Key",
    "LFO1",
    "LFO2",
    "Env1",
    "Env2",
    "Modwheel (inverted)",
    "Bend (inverted)",
    "External (inverted)",
]

# Program names are NOT ASCII (s3k.messages.AKAI_CHARSET's own docstring:
# "names are not ASCII"): a name byte is an index into a 41-entry table -
# digits, space, A-Z, and "#+-." - and encode_name refuses anything outside
# it rather than mangling it. Built from AKAI_CHARSET/NAME_LENGTH directly
# rather than a hand-copied "0-9A-Z #+-." literal here, so this can never
# silently drift from what the dependency actually accepts. The hyphen is
# escaped since it's a range operator inside a [...] class otherwise.
_NAME_INPUT_PATTERN = (
    "[" + AKAI_CHARSET.replace("-", "\\-") + "]{0," + str(NAME_LENGTH) + "}"
)

# real audio for AKAISDS_DEMO_SAMPLER's fake sample-audio path (see
# _fetch_demo_sample_audio) - the same fixture the test suite uses, not
# anything under WaveformRenderer/ (that folder is the user's own separate
# reference repo, copied in for the WaveformView port - never AKAISDS's own
# fixture path, and its contents can move/disappear without notice)
_DEMO_TEST_AUDIO_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tests", "test_audio.wav")
)

# real transfers run at a fixed rate regardless of the audio's own sample
# rate - it's a fixed 31250 baud serial line underneath, so what actually
# changes with sample rate is the WORD COUNT, not the per-word cost. Backed
# out of the README's own measured transfer-time table (5-second samples,
# every row): (3.7*60)/(5*44100), (2.5*60)/(5*30000), (1.9*60)/(5*22050),
# (1.3*60)/(5*15000), 56/(5*11025), 41/(5*8000) - all land within a few
# percent of 1.0ms/word (1.007-1.040ms, mean 1.02ms), confirming the
# fixed-rate assumption rather than being sample-rate-dependent. Used only
# to pace _fetch_demo_sample_audio so someone can evaluate what this
# feature actually feels like (the progress bar, the frozen interface, how
# long "double-click and wait" really is) without needing hardware -
# nothing here talks to a real connection at any bit rate.
_DEMO_MS_PER_WORD = 1.02


def _synthesized_demo_frame_count(sample_index):
    # split out of _synthesize_demo_sample_audio so
    # ProgramEditorWindow._demo_sample_frame_count can learn the length it
    # would produce without generating the whole (synthesized, per-frame
    # Python loop) sample array just to read len() off it, and so the two
    # can never drift apart into disagreeing on the same sample_index's
    # length - deterministic per sample_index, same reasoning as
    # _synthesize_demo_sample_audio's own comment.
    return 20000 + (sample_index * 4127) % 60000


def _sample_edit_temp_name(original_name):
    # the working name a Trim/Reverse's transformed replacement is sent
    # under FIRST, before the original is deleted - see
    # ProgramEditorWindow._perform_sample_edit_real's own comment on why
    # this must never be the original's own name: s3k's own docs say
    # keygroup zones resolve a sample by NAME, live, against whatever
    # resident sample currently carries it, with no uniqueness enforced -
    # two samples briefly sharing a name makes that resolution genuinely
    # ambiguous (measured, not just theoretical - see AGENTS.md's own
    # writeup of this). A distinct temp name avoids that window outright.
    # AKAI_CHARSET has no underscore, so a hyphen suffix stands in for
    # one; names are capped at NAME_LENGTH (12) regardless of how long
    # the original already is, so this always truncates to leave room for
    # the fixed suffix rather than risking going over.
    suffix = "-TMP"
    return (original_name.strip()[: NAME_LENGTH - len(suffix)] + suffix)[:NAME_LENGTH]


class _ModMatrixGrid(QWidget):
    """Wraps a Modulation card's QGridLayout (see _build_mod_matrix_row/
    _build_mod_matrix_amount_row and their header counterparts) to paint,
    underneath the grid's own widgets, alternating-row shading for the
    destination rows (not the header) and thin vertical separators between
    slot column-groups - the grid/header alone made column membership
    readable but rows still ran together, and slot boundaries were only
    implied by header text, not by anything visible next to the data
    itself.

    QGridLayout has no per-cell background of its own and doesn't support
    z-ordering two widgets in one cell, so this paints directly onto the
    container BEHIND the grid's widgets (Qt paints a parent before its
    children, so this reaches only the gaps around each combo/knob/label,
    never on top of them) rather than trying to style individual cells.

    column_boundaries is a list of (left_col, right_col) tuples - a
    vertical line is drawn at the midpoint between each pair. Boundaries
    are only meaningful BETWEEN slots (and between the label column and
    the first slot) - never between a slot's own Source and Amount, which
    are one logical unit.
    """

    def __init__(self, grid, header_row_count, data_row_count, column_boundaries):
        super().__init__()
        self.setLayout(grid)
        self._grid = grid
        self._header_row_count = header_row_count
        self._data_row_count = data_row_count
        self._column_boundaries = column_boundaries

    def paintEvent(self, event):
        painter = QPainter(self)
        palette = theme.current_palette()

        stripe_color = QColor(palette["bg_zebra"])
        for data_row in range(self._data_row_count):
            if data_row % 2 == 0:
                continue  # first data row of each card stays unstriped
            rect = self._grid.cellRect(self._header_row_count + data_row, 0)
            if rect.isValid():
                painter.fillRect(
                    0, rect.top(), self.width(), rect.height(), stripe_color
                )

        # measured against the last header row (single column per slot
        # there - the "Slot N" super-header row on the Source+Amount card
        # spans 2 columns each, which would give a spanned item's cellRect
        # for either of its own sub-columns, not a clean per-column split)
        boundary_row = self._header_row_count - 1
        painter.setPen(QColor(palette["border"]))
        for left_col, right_col in self._column_boundaries:
            left_rect = self._grid.cellRect(boundary_row, left_col)
            right_rect = self._grid.cellRect(boundary_row, right_col)
            if not (left_rect.isValid() and right_rect.isValid()):
                continue
            x = (left_rect.right() + right_rect.left()) // 2
            painter.drawLine(x, 0, x, self.height())

        super().paintEvent(event)


class ProgramEditorWindow(QMainWindow):
    def __init__(self, main_window, bridge):
        super().__init__()
        self.setWindowTitle("AKAISDS - Program Editor")
        # was 1040 - the new Modulation card's "Filter Frequency"/"Pan" rows
        # (up to 3 source+amount slots wide) forced a horizontal scrollbar
        # on the Program tab below that width; re-measured the same way
        # AGENTS.md describes for every other width tweak on this page -
        # grew the window until scroll_area.horizontalScrollBar().maximum()
        # hit zero on both tabs. Grew again, 1080 -> 1200, when the
        # Modulation card's grid moved each slot's amount knob beside its
        # source combo instead of stacked below it (each slot now needs
        # combo-width + knob-width side by side, not max(combo, knob)).
        # Grew once more, 1200 -> 1220, once the theme's actual stylesheet
        # (fonts/padding via Fusion) was applied rather than measured with
        # no theme at all - 1200 was 1px short with it on.
        self.setMinimumSize(1220, 800)
        self._sample_list = []
        self._keygroup_ranges = []  # [lo, hi] per keygroup - mirrors keygroup_range_bar
        self._pending_restore_state = None  # set only by _refresh_from_hardware()
        self._refresh_in_progress = False
        self._multi_refresh_in_progress = False
        # session-only cache of loaded sample audio + loop-point markers,
        # keyed by sample index - {"samples", "framerate", "start",
        # "loop_start", "loop_end", "end"}. Loading audio means a live SDS
        # transfer (see _load_sample_waveform), so re-selecting a sample
        # already loaded this session shows it instantly instead of
        # re-running that. Cleared whenever the sample list itself reloads
        # (_on_samples_loaded) - a resident sample's own index can start
        # meaning something else after that.
        self._sample_waveform_cache = {}

        self._main_window = main_window
        self._bridge = bridge

        # a single persistent worker thread owns every call to the bridge
        # for this window's whole lifetime, taking requests off a queue and
        # running them strictly one at a time - see BridgeWorker's own
        # docstring for why (S3kBridge isn't safe for concurrent calls, and
        # a one-shot QThread per UI action used to crash real hardware runs
        # by leaving more than one of those in flight at once)

        # indeterminate - a real hardware read has no predictable duration
        # to show progress against (a keygroup/detail fetch over 31250 baud
        # SysEx runs a couple of seconds; see BridgeWorker.busy_changed).
        # Built here, but only placed into the bottom row (next to the
        # Refresh button) much further down - the two timers below only
        # need it to exist by the time they can actually fire, which is
        # never before __init__ finishes.
        self._loading_progress = QProgressBar()
        self._loading_progress.setRange(0, 0)
        self._loading_progress.setFixedWidth(120)
        self._loading_progress.setTextVisible(False)
        self._loading_progress.setVisible(False)
        # _on_worker_busy_changed below debounces BridgeWorker.busy_changed
        # through these two one-shot timers rather than showing/hiding on
        # the raw signal directly, for two reasons that both showed up as
        # real test failures before this existed:
        #   - _show: a fast op (the demo bridge; a single field write on
        #     real hardware) would otherwise flash the bar for one frame
        #   - _hide: a burst of several jobs submitted back to back (e.g.
        #     Refresh: program list, multi parts, keygroups) can have the
        #     worker thread race ahead and briefly drain the queue to empty
        #     *between* two GUI-thread submit_*() calls, which reads as
        #     busy_changed(False) immediately followed by another True -
        #     without this, that shows as a visible flicker instead of one
        #     continuous span. Real hardware is slow enough this race
        #     realistically never fires (the GUI thread submits a whole
        #     batch in far under a millisecond; the worker is still busy
        #     with the first job when the rest land) - it's mainly the
        #     demo/fake bridges' near-instant responses that expose it.
        self._busy_show_timer = QTimer(self)
        self._busy_show_timer.setSingleShot(True)
        self._busy_show_timer.setInterval(200)
        self._busy_show_timer.timeout.connect(
            lambda: self._loading_progress.setVisible(True)
        )
        self._busy_hide_timer = QTimer(self)
        self._busy_hide_timer.setSingleShot(True)
        self._busy_hide_timer.setInterval(150)
        self._busy_hide_timer.timeout.connect(self._confirm_worker_idle)

        self._worker = BridgeWorker(self._bridge)
        self._worker.busy_changed.connect(self._on_worker_busy_changed)
        self._worker.programs_loaded.connect(self._on_programs_loaded)
        self._worker.programs_load_failed.connect(self._on_program_load_failed)
        self._worker.samples_loaded.connect(self._on_samples_loaded)
        self._worker.samples_load_failed.connect(
            lambda e: self.status_bar.showMessage(f"Couldn't load samples: {e}")
        )
        self._worker.keygroups_loaded.connect(self._on_keygroups_loaded)
        self._worker.keygroups_load_failed.connect(self._on_load_failed)
        self._worker.detail_loaded.connect(self._on_detail_loaded)
        self._worker.detail_load_failed.connect(self._on_detail_load_failed)
        self._worker.parts_loaded.connect(self._on_multi_parts_loaded)
        self._worker.parts_load_failed.connect(self._on_multi_load_failed)
        self._worker.multi_name_loaded.connect(self._on_multi_name_loaded)
        self._worker.change_sent.connect(
            lambda part_index, name: self.status_bar.showMessage(
                f"Part {part_index + 1}: sent Program Change for '{name}'"
            )
        )
        self._worker.change_send_failed.connect(
            lambda part_index, e: self.status_bar.showMessage(
                f"Part {part_index + 1}: couldn't send Program Change: {e}"
            )
        )
        self._worker.write_succeeded.connect(
            lambda _key, param_name, v: self.status_bar.showMessage(
                f"{param_name} → {v}"
            )
        )
        self._worker.write_failed.connect(
            lambda _key, param_name, e: self.status_bar.showMessage(
                f"Write failed ({param_name}): {e}"
            )
        )
        self._worker.program_deleted.connect(self._on_program_deleted)
        self._worker.program_delete_failed.connect(
            lambda _index, e: self.status_bar.showMessage(
                f"Couldn't delete program: {e}"
            )
        )
        self._worker.keygroup_deleted.connect(self._on_keygroup_deleted)
        self._worker.keygroup_delete_failed.connect(
            lambda _p, _k, e: self.status_bar.showMessage(
                f"Couldn't delete keygroup: {e}"
            )
        )
        self._worker.sample_deleted.connect(self._on_sample_deleted)
        self._worker.sample_delete_failed.connect(
            lambda _index, e: self.status_bar.showMessage(
                f"Couldn't delete sample: {e}"
            )
        )
        # automatic, async header-only fetch triggered by plain sample
        # selection (_on_sample_selected) - separate from
        # _fetch_sample_header_blocking's own transient listener on these
        # same two signals, used only by _load_sample_waveform's fallback
        # path; both coexisting is fine, Qt signals support multiple slots
        self._worker.sample_detail_loaded.connect(self._on_sample_detail_loaded)
        self._worker.sample_detail_load_failed.connect(
            self._on_sample_detail_load_failed
        )
        self._worker.start()

        # placeholder - real program, keygroup panels come later
        self.program_list = QListWidget()
        self.program_list.setObjectName("programList")
        self.program_list.setFixedWidth(160)

        self.keygroup_list = QListWidget()
        self.keygroup_list.setObjectName("keygroupList")
        self.keygroup_list.setFixedWidth(190)  # fits "Keygroup 12: C#1 - D#7"

        # DELP/DELK have no device-side confirmation prompt of their own -
        # s3k.bridge's own docstring on them: "The specification defines no
        # confirmation step for any of these. Callers must never key-bind
        # them -- always an explicit arm-then-fire flow." The shortcuts/
        # context-menu entries below are only ever wired to
        # _confirm_delete_program/_confirm_delete_keygroup, which show a
        # QMessageBox and only submit the delete if the user clicks through
        # it - that dialog IS the arm-then-fire flow, so nothing here ever
        # deletes directly off a keypress or a menu click.
        #
        # One QAction serves both the right-click context menu (via
        # ActionsContextMenu) and the keyboard shortcut. Key_Delete/
        # Key_Backspace covers plain "press the delete key" on every
        # platform (a Mac keyboard's own Delete key sends Key_Backspace,
        # not Key_Delete); "Ctrl+Backspace" adds an explicit alternate that
        # Qt maps to Cmd+Backspace on macOS. WidgetShortcut scopes each
        # action to its own list actually having focus - without it, both
        # actions would fire together (an "ambiguous shortcut" warning)
        # whenever the window has focus at all, since they'd otherwise
        # share the same shortcut at WindowShortcut scope. Module-level
        # (_DELETE_SHORTCUTS) rather than __init__-local since
        # _build_samples_tab's own delete-sample action needs the same
        # list and is built later, outside __init__.
        self.program_list.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        # keygroups have no name field on the hardware at all - PRNAME is a
        # program-level field (s3k.params) - so rename only ever applies to
        # the program list, never keygroup_list
        self._rename_program_action = QAction("Rename Program...", self.program_list)
        self._rename_program_action.triggered.connect(self._confirm_rename_program)
        self._rename_program_action.setEnabled(False)
        self.program_list.addAction(self._rename_program_action)

        _program_list_separator = QAction(self.program_list)
        _program_list_separator.setSeparator(True)
        self.program_list.addAction(_program_list_separator)

        self._delete_program_action = QAction("Delete Program...", self.program_list)
        self._delete_program_action.setShortcuts(_DELETE_SHORTCUTS)
        self._delete_program_action.setShortcutContext(
            Qt.ShortcutContext.WidgetShortcut
        )
        self._delete_program_action.triggered.connect(self._confirm_delete_program)
        self._delete_program_action.setEnabled(False)
        self.program_list.addAction(self._delete_program_action)

        self.keygroup_list.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        self._delete_keygroup_action = QAction("Delete Keygroup...", self.keygroup_list)
        self._delete_keygroup_action.setShortcuts(_DELETE_SHORTCUTS)
        self._delete_keygroup_action.setShortcutContext(
            Qt.ShortcutContext.WidgetShortcut
        )
        self._delete_keygroup_action.triggered.connect(self._confirm_delete_keygroup)
        self._delete_keygroup_action.setEnabled(False)
        self.keygroup_list.addAction(self._delete_keygroup_action)

        self.keygroup_range_bar = KeygroupRangeBar()

        # bold header + list, same layout shape as the dashboard's queue/hardware panels
        programs_column = QVBoxLayout()
        programs_column.setContentsMargins(0, 0, 0, 0)
        programs_column.setSpacing(6)
        programs_column.addWidget(QLabel("<b>Programs</b>"))
        programs_column.addWidget(self.program_list)
        programs_container = QWidget()
        programs_container.setLayout(programs_column)

        keygroups_column = QVBoxLayout()
        keygroups_column.setContentsMargins(0, 0, 0, 0)
        keygroups_column.setSpacing(6)
        keygroups_column.addWidget(QLabel("<b>Keygroups</b>"))
        keygroups_column.addWidget(self.keygroup_range_bar)
        keygroups_column.addWidget(self.keygroup_list)
        keygroups_container = QWidget()
        keygroups_container.setLayout(keygroups_column)

        self.note_lo_spinbox = NoteSpinBox()
        self.note_hi_spinbox = NoteSpinBox()
        note_range_row = QHBoxLayout()
        note_range_label = QLabel("Note Range")
        note_range_label.setFixedWidth(80)
        note_range_row.addWidget(note_range_label)
        note_range_row.addWidget(self.note_lo_spinbox)
        note_range_row.addWidget(QLabel("-"))
        note_range_row.addWidget(self.note_hi_spinbox)
        note_range_row.addStretch()

        self.cutoff_knob = Knob()
        self.cutoff_knob.setRange(0, 99)
        self.cutoff_knob.setDefaultValue(99)  # fully open - no filtering
        self.cutoff_knob.setFixedSize(56, 56)
        self.resonance_knob = Knob()
        self.resonance_knob.setRange(0, 15)
        self.resonance_knob.setDefaultValue(0)  # no resonance
        self.resonance_knob.setFixedSize(56, 56)
        self.key_filter_track_knob = Knob()
        # s3k.params declares K_FREQ's range as -30..99 (its own notes cite
        # a 2026-08-24 hardware sweep finding no clamp at 12 or 24 either),
        # but that's contradicted by this project's own hardware: confirmed
        # -24..+24 on real S3000-series hardware in front of the user
        # (2026-09-20) - going with the direct measurement over the
        # dependency's, per AGENTS.md (don't edit s3k/s3ked in-place; the
        # correction belongs here instead)
        self.key_filter_track_knob.setRange(-24, 24)
        self.key_filter_track_knob.setDefaultValue(0)  # no key tracking
        self.key_filter_track_knob.setFixedSize(56, 56)

        self.env1_graph = ADSREnvelopeGraph()
        self.env1_graph.setFixedSize(200, 90)
        self.env2_graph = Envelope2Graph()
        self.env2_graph.setFixedSize(200, 90)

        # ENV1 - a standard ADSR: one knob per stage, in a single row.
        # Defaults are the Akai factory-preset values (double-click a knob
        # to reset to these), not the range midpoint Knob.defaultValue()
        # would otherwise fall back to.
        self.attack1_knob = Knob()
        self.attack1_knob.setRange(0, 99)
        self.attack1_knob.setDefaultValue(25)
        self.attack1_knob.setFixedSize(40, 40)
        self.decay1_knob = Knob()
        self.decay1_knob.setRange(0, 99)
        self.decay1_knob.setDefaultValue(50)
        self.decay1_knob.setFixedSize(40, 40)
        self.sustain1_knob = Knob()
        self.sustain1_knob.setRange(0, 99)
        self.sustain1_knob.setDefaultValue(99)
        self.sustain1_knob.setFixedSize(40, 40)
        self.release1_knob = Knob()
        self.release1_knob.setRange(0, 99)
        self.release1_knob.setDefaultValue(45)
        self.release1_knob.setFixedSize(40, 40)

        attack1_col, self.attack1_value_label = self._build_knob_column(
            "Attack", self.attack1_knob
        )
        decay1_col, self.decay1_value_label = self._build_knob_column(
            "Decay", self.decay1_knob
        )
        sustain1_col, self.sustain1_value_label = self._build_knob_column(
            "Sustain", self.sustain1_knob
        )
        release1_col, self.release1_value_label = self._build_knob_column(
            "Release", self.release1_knob
        )

        env1_controls_row = QHBoxLayout()
        env1_controls_row.setSpacing(10)
        env1_controls_row.addLayout(attack1_col)
        env1_controls_row.addLayout(decay1_col)
        env1_controls_row.addLayout(sustain1_col)
        env1_controls_row.addLayout(release1_col)

        for knob, field in (
            (self.attack1_knob, "ATTAK1"),
            (self.decay1_knob, "DECAY1"),
            (self.sustain1_knob, "SUSTN1"),
            (self.release1_knob, "RELSE1"),
        ):
            knob.valueChanged.connect(self._on_env1_knob_changed)
            self._wire_knob_write(
                knob,
                field,
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )

        # ENV2 - a 4-stage rate/level generator: one column per stage
        # (left-to-right in order), a "Stage N" header naming each column
        # and "Rate"/"Level" row labels naming each row - same grid shape
        # as the Modulation cards' Amount-only card (_build_mod_matrix_
        # amount_header/_row), reused here rather than ENV1's per-stage
        # label-above/value-below columns (_build_knob_column): with two
        # knobs per stage instead of ENV1's one, repeating "Rate"/"Level"
        # under every single knob (as a first pass at this did, one column
        # per stage with its own two labelled rows) made the card wider
        # than ENV1's for no benefit - the row labels only need saying once
        # each, on the left, same as the Modulation card's destinations.
        self._env2_rate_knobs = [Knob() for _ in range(4)]
        self._env2_level_knobs = [Knob() for _ in range(4)]
        self._env2_rate_value_labels = []
        self._env2_level_value_labels = []
        env2_grid = QGridLayout()
        env2_grid.setHorizontalSpacing(14)
        env2_grid.setVerticalSpacing(6)
        env2_header_rows = self._build_mod_matrix_amount_header(
            env2_grid, 4, label_prefix="Stage"
        )
        env2_rate_layouts = []
        env2_level_layouts = []
        # Akai factory-preset values, one per stage (double-click a knob to
        # reset to these) - same reasoning as ENV1's defaults above
        _ENV2_RATE_DEFAULTS = [0, 50, 50, 45]
        _ENV2_LEVEL_DEFAULTS = [99, 99, 99, 0]
        for i, (rate_knob, level_knob) in enumerate(
            zip(self._env2_rate_knobs, self._env2_level_knobs), start=1
        ):
            rate_knob.setRange(0, 99)
            rate_knob.setDefaultValue(_ENV2_RATE_DEFAULTS[i - 1])
            rate_knob.setFixedSize(32, 32)
            level_knob.setRange(0, 99)
            level_knob.setDefaultValue(_ENV2_LEVEL_DEFAULTS[i - 1])
            level_knob.setFixedSize(32, 32)

            rate_layout, rate_value_label = self._build_knob_value_row(rate_knob)
            level_layout, level_value_label = self._build_knob_value_row(level_knob)
            env2_rate_layouts.append(rate_layout)
            env2_level_layouts.append(level_layout)

            self._env2_rate_value_labels.append(rate_value_label)
            self._env2_level_value_labels.append(level_value_label)

            rate_knob.valueChanged.connect(self._on_env2_knob_changed)
            level_knob.valueChanged.connect(self._on_env2_knob_changed)

        # "Rate"/"Level" are much shorter than the Modulation cards' own
        # destination names, so a narrow label_width keeps this grid from
        # inheriting that card's wider label column for no reason
        self._build_mod_matrix_amount_row(
            env2_grid, env2_header_rows + 0, "Rate", *env2_rate_layouts, label_width=45
        )
        self._build_mod_matrix_amount_row(
            env2_grid,
            env2_header_rows + 1,
            "Level",
            *env2_level_layouts,
            label_width=45,
        )

        # ENV2's eight fields aren't a plain rate1..4/level1..4 sequence -
        # ATTAK2/DECAY2/RELSE2/SUSTN2 keep their ADSR-flavoured names from
        # ENV1, and ENV2L1/ENV2R2/ENV2L2/ENV2L4 fill in the rest of the same
        # 4-stage rate/level structure (see s3k/params.py's notes on these).
        _ENV2_FIELDS = ["ATTAK2", "ENV2R2", "DECAY2", "RELSE2"]
        for rate_knob, level_knob, rate_field, level_field in zip(
            self._env2_rate_knobs,
            self._env2_level_knobs,
            _ENV2_FIELDS,
            ["ENV2L1", "ENV2L2", "SUSTN2", "ENV2L4"],
        ):
            self._wire_knob_write(
                rate_knob,
                rate_field,
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )
            self._wire_knob_write(
                level_knob,
                level_field,
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )

        env1_graph_row = QHBoxLayout()
        env1_graph_row.addStretch()
        env1_graph_row.addWidget(self.env1_graph)
        env1_graph_row.addStretch()
        env2_graph_row = QHBoxLayout()
        env2_graph_row.addStretch()
        env2_graph_row.addWidget(self.env2_graph)
        env2_graph_row.addStretch()

        cutoff_column, self.cutoff_value_label = self._build_knob_column(
            "Cutoff", self.cutoff_knob
        )
        resonance_column, self.resonance_value_label = self._build_knob_column(
            "Resonance", self.resonance_knob
        )
        key_filter_track_column, self.key_filter_track_value_label = (
            self._build_knob_column("Key Filter Track", self.key_filter_track_knob)
        )

        knobs_layout = QHBoxLayout()
        knobs_layout.addLayout(cutoff_column)
        knobs_layout.addLayout(resonance_column)
        knobs_layout.addLayout(key_filter_track_column)

        # per-zone control lists — indexed 0-3
        self._zone_combos = []
        self._zone_vel_lo = []
        self._zone_vel_hi = []
        self._zone_tune = []
        self._zone_loudness = []
        self._zone_pan = []
        self._zone_looptype = []
        self._zone_keytrack = []

        _ZONE_FIELDS = [
            (
                "SNAME1",
                "LOVEL1",
                "HIVEL1",
                "VTUNO1",
                "VLOUD1",
                "VPANO1",
                "ZPLAY1",
                "CP1",
            ),
            (
                "SNAME2",
                "LOVEL2",
                "HIVEL2",
                "VTUNO2",
                "VLOUD2",
                "VPANO2",
                "ZPLAY2",
                "CP2",
            ),
            (
                "SNAME3",
                "LOVEL3",
                "HIVEL3",
                "VTUNO3",
                "VLOUD3",
                "VPANO3",
                "ZPLAY3",
                "CP3",
            ),
            (
                "SNAME4",
                "LOVEL4",
                "HIVEL4",
                "VTUNO4",
                "VLOUD4",
                "VPANO4",
                "ZPLAY4",
                "CP4",
            ),
        ]

        zone_selector_row = QHBoxLayout()
        self._zone_button_group = QButtonGroup(self)
        self._zone_button_group.setExclusive(True)

        self._zone_stack = QStackedWidget()
        self._zone_stack.setStyleSheet("background: transparent;")

        self._zone_loudness_labels = []
        self._zone_pan_labels = []
        for zone_idx, (
            sname,
            lovel,
            hivel,
            vtuno,
            vloud,
            vpano,
            zplay,
            cp,
        ) in enumerate(_ZONE_FIELDS):
            btn = QPushButton(f"Zone {zone_idx + 1}")
            btn.setCheckable(True)
            btn.setChecked(zone_idx == 0)
            self._zone_button_group.addButton(btn, zone_idx)
            zone_selector_row.addWidget(btn)

            page = QWidget()
            page.setStyleSheet("background: transparent;")
            page_layout = QVBoxLayout()
            page_layout.setSpacing(6)

            # sample + loudness/pan all share one row - label above each
            # control (_build_labeled_combo_column/_build_labeled_knob_
            # value_column) instead of Sample's old label-to-the-left row
            # with Loud/Pan's own knob row further down the page; freeing
            # Loud/Pan's knobs down to 28px (matching the Multis tab/mod
            # matrix knobs elsewhere on this page) made room for them
            # beside Sample instead of needing a row of their own.
            # Left-aligned (center=False) and bold (<b>...</b>) - same
            # shape as the Multis tab's own column headers (e.g.
            # part_header/level_header/pan_header below), not this page's
            # usual centered/plain knob-column labels.
            combo = QComboBox()
            combo.setEnabled(False)
            sample_column = self._build_labeled_combo_column(
                "<b>Sample</b>", combo, center=False
            )
            self._zone_combos.append(combo)

            loud_knob = Knob()
            loud_knob.setRange(-50, 50)
            loud_knob.setFixedSize(28, 28)
            loud_knob.setEnabled(True)
            pan_knob = Knob()
            pan_knob.setRange(-50, 50)
            pan_knob.setFixedSize(28, 28)
            pan_knob.setEnabled(True)

            loud_col, loud_val_label = self._build_labeled_knob_value_column(
                "<b>Loud</b>", loud_knob, center=False
            )
            pan_col, pan_val_label = self._build_labeled_knob_value_column(
                "<b>Pan</b>", pan_knob, center=False
            )

            zone_top_row = QHBoxLayout()
            zone_top_row.addLayout(sample_column, stretch=1)
            zone_top_row.addLayout(loud_col)
            zone_top_row.addLayout(pan_col)
            page_layout.addLayout(zone_top_row)

            self._zone_loudness.append(loud_knob)
            self._zone_pan.append(pan_knob)
            self._zone_loudness_labels.append(loud_val_label)
            self._zone_pan_labels.append(pan_val_label)

            # velocity row
            vel_row = QHBoxLayout()
            vel_lo_label = QLabel("Velocity Low")
            vel_lo_label.setFixedWidth(80)
            vel_lo = QSpinBox()
            vel_lo.setRange(0, 127)
            vel_lo.setFixedWidth(60)
            vel_hi_label = QLabel("Velocity High")
            vel_hi_label.setFixedWidth(80)
            vel_hi = QSpinBox()
            vel_hi.setRange(0, 127)
            vel_hi.setFixedWidth(60)
            vel_row.addWidget(vel_lo_label)
            vel_row.addWidget(vel_lo)
            vel_row.addSpacing(12)
            vel_row.addWidget(vel_hi_label)
            vel_row.addWidget(vel_hi)
            vel_row.addStretch()
            page_layout.addLayout(vel_row)
            self._zone_vel_lo.append(vel_lo)
            self._zone_vel_hi.append(vel_hi)

            # tune stays as a spinbox — precision decimal values need it
            tune_row = QHBoxLayout()
            tune_row_label = QLabel("Tune")
            tune_row_label.setFixedWidth(35)
            tune = QDoubleSpinBox()
            tune.setRange(-50.0, 50.0)
            tune.setSingleStep(0.01)
            tune.setDecimals(2)
            tune.setSuffix(" st")
            tune.setFixedWidth(90)
            tune_row.addWidget(tune_row_label)
            tune_row.addWidget(tune)
            tune_row.addStretch()
            page_layout.addLayout(tune_row)
            self._zone_tune.append(tune)

            # loop type and keyboard tracking row
            loop_track_row = QHBoxLayout()
            loop_label = QLabel("Loop type")
            loop_label.setFixedWidth(70)
            loop_combo = QComboBox()
            loop_combo.setFixedWidth(160)
            for option_idx, (option_label, option_tooltip) in enumerate(
                _LOOP_TYPE_OPTIONS
            ):
                loop_combo.addItem(option_label)
                loop_combo.setItemData(
                    option_idx, option_tooltip, Qt.ItemDataRole.ToolTipRole
                )
            loop_combo.setToolTip(_LOOP_TYPE_OPTIONS[0][1])
            loop_combo.currentIndexChanged.connect(
                lambda i, combo=loop_combo: combo.setToolTip(_LOOP_TYPE_OPTIONS[i][1])
            )
            keytrack_label = QLabel("Keytrack")
            keytrack_label.setFixedWidth(60)
            keytrack_combo = QComboBox()
            keytrack_combo.setFixedWidth(110)
            keytrack_combo.addItems(["Track", "Const Pitch"])
            loop_track_row.addWidget(loop_label)
            loop_track_row.addWidget(loop_combo)
            loop_track_row.addSpacing(12)
            loop_track_row.addWidget(keytrack_label)
            loop_track_row.addWidget(keytrack_combo)
            loop_track_row.addStretch()
            page_layout.addLayout(loop_track_row)
            self._zone_looptype.append(loop_combo)
            self._zone_keytrack.append(keytrack_combo)

            page.setLayout(page_layout)
            self._zone_stack.addWidget(page)

            # wire write signals for this zone's controls
            self._wire_spinbox_write(
                vel_lo,
                lovel,
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )
            self._wire_spinbox_write(
                vel_hi,
                hivel,
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )
            self._wire_spinbox_write(
                tune,
                vtuno,
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
                value_converter=self._semitones_to_tune_offset,
            )
            self._wire_knob_write(
                loud_knob,
                vloud,
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )
            self._wire_knob_write(
                pan_knob,
                vpano,
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )
            self._wire_combo_write(
                loop_combo,
                zplay,
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )
            self._wire_combo_write(
                keytrack_combo,
                cp,
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )

        self._zone_button_group.idClicked.connect(self._zone_stack.setCurrentIndex)

        zone_header = QLabel("Zone")
        zone_header.setObjectName("sectionHeader")
        zone_section = QVBoxLayout()
        zone_section.setContentsMargins(12, 10, 12, 12)
        zone_section.setSpacing(10)
        zone_section.addWidget(zone_header)
        zone_section.addLayout(zone_selector_row)
        zone_section.addWidget(self._zone_stack)
        zone_card = QWidget()
        zone_card.setObjectName("zoneCard")
        zone_card.setLayout(zone_section)

        # same grouping principle as the Program tab: Range on its own,
        # every filter control together, then the existing per-zone card.
        # ENV1 and ENV2 get their own cards rather than sharing one - they're
        # independently meaningful generators (a genuine ADSR vs. ENV2's
        # 4-stage rate/level shape, see Envelope2Graph's own comment), and a
        # shared "Envelopes" title read as one thing when it's really two.
        range_section = self._build_section_card("Range", note_range_row)
        filter_section = self._build_section_card("Filter", knobs_layout)
        env1_section = self._build_section_card(
            "Envelope 1", env1_graph_row, env1_controls_row
        )
        env2_section = self._build_section_card("Envelope 2", env2_graph_row, env2_grid)

        # Range is a single short row and Filter is comparable in height -
        # side by side halves the vertical space these two cost together.
        # Same idea for the two envelope cards, which were side by side
        # already as one card's two halves - now just two cards instead.
        range_filter_row = QHBoxLayout()
        # 1:1, not the 2-for-Filter split this briefly had - measured
        # sizeHints say Filter's own content (273px: 3 knobs) is actually
        # narrower than Range's (299px: label + two note spinboxes), so the
        # 2x weight was giving Filter more than its share and stretching it
        # wider than Range needed to be. Matches every other paired row on
        # the Program tab, which is never anything but 1:1.
        range_filter_row.addWidget(range_section, stretch=1)
        range_filter_row.addWidget(filter_section, stretch=1)
        envelopes_row = QHBoxLayout()
        envelopes_row.addWidget(env1_section, stretch=1)
        envelopes_row.addWidget(env2_section, stretch=1)

        # Modulation (Keygroup tab half) - the other side of the Program
        # tab's Modulation card: amounts for the destinations whose value
        # is stored per-keygroup rather than per-program (see that card's
        # own comment for why). Sources for every row here are chosen on
        # the Program tab instead - shared by every keygroup in this
        # program, so there's nothing to pick per-keygroup.
        kg_filt1_col, self.mod_filt1_amount_knob, self.mod_filt1_amount_value_label = (
            self._build_mod_amount_only_column(
                "MODVFILT1",
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )
        )
        kg_filt2_col, self.mod_filt2_amount_knob, self.mod_filt2_amount_value_label = (
            self._build_mod_amount_only_column(
                "MODVFILT2",
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )
        )
        kg_filt3_col, self.mod_filt3_amount_knob, self.mod_filt3_amount_value_label = (
            self._build_mod_amount_only_column(
                "MODVFILT3",
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )
        )
        kg_mod_grid = QGridLayout()
        kg_mod_grid.setHorizontalSpacing(14)
        kg_mod_grid.setVerticalSpacing(10)
        # max slot count of any row below (Filter Frequency) - see the
        # Program tab's identical header call above for why
        kg_header_rows = self._build_mod_matrix_amount_header(kg_mod_grid, 3)
        self._build_mod_matrix_amount_row(
            kg_mod_grid,
            kg_header_rows + 0,
            "Filter Frequency",
            kg_filt1_col,
            kg_filt2_col,
            kg_filt3_col,
        )

        kg_pitch_col, self.mod_pitch_amount_knob, self.mod_pitch_amount_value_label = (
            self._build_mod_amount_only_column(
                "MODVPITCH",
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )
        )
        self._build_mod_matrix_amount_row(
            kg_mod_grid, kg_header_rows + 1, "Pitch (assignable)", kg_pitch_col
        )

        kg_amp3_col, self.mod_amp3_amount_knob, self.mod_amp3_amount_value_label = (
            self._build_mod_amount_only_column(
                "MODVAMP3",
                "keygroup",
                keygroup_index_getter=self.keygroup_list.currentRow,
            )
        )
        self._build_mod_matrix_amount_row(
            kg_mod_grid, kg_header_rows + 2, "Loudness (slot 3)", kg_amp3_col
        )

        # L_PTCH is NOT part of the 3-slot assignable matrix - it's a
        # separate, always-on LFO1-to-pitch route (a fixed vibrato depth),
        # same idea as the fixed MWLDEP/PRSDEP/VELDEP fields already on the
        # hardware for LFO1 depth (not yet exposed here either). No source
        # dropdown needed since the source is always LFO1.
        self.lfo1_to_pitch_knob = self._build_mod_amount_knob()
        lfo1_to_pitch_col, self.lfo1_to_pitch_value_label = self._build_knob_value_row(
            self.lfo1_to_pitch_knob
        )
        self._wire_knob_write(
            self.lfo1_to_pitch_knob,
            "L_PTCH",
            "keygroup",
            keygroup_index_getter=self.keygroup_list.currentRow,
        )
        self._build_mod_matrix_amount_row(
            kg_mod_grid, kg_header_rows + 3, "Pitch (LFO1)", lfo1_to_pitch_col
        )

        kg_mod_footnote = QLabel(
            "Sources for these are chosen on the Program tab's Modulation "
            "card, shared by every keygroup in this program."
        )
        kg_mod_footnote.setWordWrap(True)
        kg_mod_footnote.setObjectName("modFootnote")
        kg_mod_footnote_row = QHBoxLayout()
        kg_mod_footnote_row.addWidget(kg_mod_footnote)

        # 4 destination rows above (Filter Frequency, Pitch (assignable),
        # Loudness (slot 3), Pitch (LFO1)); this card is Amount-only (one
        # column per slot, no Source column - see _ModMatrixGrid's own
        # docstring), so a boundary sits between every column
        kg_mod_matrix = _ModMatrixGrid(
            kg_mod_grid, kg_header_rows, 4, [(0, 1), (1, 2), (2, 3)]
        )
        kg_mod_grid_row = QVBoxLayout()
        kg_mod_grid_row.setContentsMargins(0, 0, 0, 0)
        kg_mod_grid_row.addWidget(kg_mod_matrix)

        keygroup_modulation_section = self._build_section_card(
            "Modulation", kg_mod_grid_row, kg_mod_footnote_row
        )

        detail_container_layout = QVBoxLayout()
        detail_container_layout.setSpacing(12)
        detail_container_layout.addLayout(range_filter_row)
        detail_container_layout.addLayout(envelopes_row)
        detail_container_layout.addWidget(zone_card)
        detail_container_layout.addWidget(keygroup_modulation_section)
        detail_container_layout.addStretch()
        detail_container = QWidget()
        detail_container.setLayout(detail_container_layout)

        self.program_name_edit = self._build_akai_name_edit()
        # forced uppercase as-you-type, same as encode_name would do anyway
        # (it uppercases before encoding) - showing the user what will
        # actually be stored beats a display that quietly disagrees with it
        self.program_name_edit.textEdited.connect(self._on_program_name_typed)
        # deliberately NOT wired through _schedule_write's continuous
        # debounce like every other program field - that pattern sends a
        # write per keystroke's worth of change once the timer lands, which
        # for a name means firing off a string of half-typed names over
        # SysEx. A name commits once, on editingFinished (Enter or focus
        # loss), like note_lo_spinbox/note_hi_spinbox's _commit_note_range.
        self.program_name_edit.editingFinished.connect(self._commit_program_name)
        name_row = QHBoxLayout()
        name_row.addWidget(self.program_name_edit)
        name_row.addStretch()
        name_section = self._build_section_card("Program Name", name_row)

        self.pan_knob = Knob()
        self.pan_knob.setRange(-50, 50)
        self.pan_knob.setFixedSize(56, 56)
        pan_column, self.pan_value_label = self._build_knob_column("Pan", self.pan_knob)

        self.loud_knob = Knob()
        self.loud_knob.setRange(0, 99)
        self.loud_knob.setDefaultValue(80)
        self.loud_knob.setFixedSize(56, 56)
        loud_column, self.loud_value_label = self._build_knob_column(
            "Loud", self.loud_knob
        )

        self.velocity_knob = Knob()
        self.velocity_knob.setRange(-50, 50)
        self.velocity_knob.setDefaultValue(20)
        self.velocity_knob.setFixedSize(56, 56)
        velocity_column, self.velocity_value_label = self._build_knob_column(
            "Velocity", self.velocity_knob
        )

        self.lfo_rate_knob = Knob()
        self.lfo_rate_knob.setRange(0, 99)
        self.lfo_rate_knob.setDefaultValue(0)  # no modulation without depth anyway
        self.lfo_rate_knob.setFixedSize(56, 56)
        self.lfo_depth_knob = Knob()
        self.lfo_depth_knob.setRange(0, 99)
        self.lfo_depth_knob.setDefaultValue(0)  # no modulation
        self.lfo_depth_knob.setFixedSize(56, 56)
        self.lfo_delay_knob = Knob()
        self.lfo_delay_knob.setRange(0, 99)
        self.lfo_delay_knob.setDefaultValue(0)  # no delay before the LFO starts
        self.lfo_delay_knob.setFixedSize(56, 56)

        self.lfo_shape_combo = QComboBox()
        self.lfo_shape_combo.addItems(["Triangle", "Sawtooth", "Square", "Random"])
        self.lfo_shape_combo.setEnabled(True)
        self.lfo_shape_combo.currentIndexChanged.connect(
            lambda i: self._schedule_write("LFO1WAVE", "program", i)
        )

        lfo_shape_label = QLabel("LFO shape")
        lfo_shape_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lfo_shape_column = QVBoxLayout()
        lfo_shape_column.setSpacing(4)
        lfo_shape_column.addWidget(
            lfo_shape_label, alignment=Qt.AlignmentFlag.AlignHCenter
        )
        lfo_shape_column.addWidget(self.lfo_shape_combo)

        # LFO1 sync - s3k.params' own field is DESYNC (values={0:"OFF",
        # 1:"ON"}: "Enable de-synchronisation of LFO1 across notes"), which
        # is LFO1's sync control phrased as its own negation. This combo is
        # deliberately labeled the positive way round ("Sync": On/Off)
        # rather than "Desync": On/Off) to match how a user actually thinks
        # about it - but that means the label order is FLIPPED from
        # DESYNC's own OFF/ON text while the raw byte each index writes is
        # NOT flipped: index 0 ("On", i.e. LFO1 stays synced across notes)
        # is DESYNC=0, index 1 ("Off", desynced) is DESYNC=1 - same "combo
        # index is the raw byte" convention as every other combo here, just
        # with index 0 meaning Sync-On/DESYNC-Off. Don't reorder these items
        # without also checking this still lines up.
        self.lfo1_sync_combo = QComboBox()
        self.lfo1_sync_combo.addItems(["On", "Off"])
        lfo1_sync_column = self._build_labeled_combo_column(
            "LFO1 sync", self.lfo1_sync_combo
        )

        lfo_rate_column, self.lfo_rate_value_label = self._build_knob_column(
            "LFO rate", self.lfo_rate_knob
        )
        lfo_depth_column, self.lfo_depth_value_label = self._build_knob_column(
            "LFO depth", self.lfo_depth_knob
        )
        lfo_delay_column, self.lfo_delay_value_label = self._build_knob_column(
            "LFO delay", self.lfo_delay_knob
        )

        # LFO2 - on this hardware LFO2 is hardwired to modulate Pan (an
        # auto-pan effect): its rate/depth/delay are PANRAT/PANDEP/PANDEL,
        # stored in the "program.pan" region despite being LFO2's own
        # controls, not LFO1's pitch-modulation role. It's still a real,
        # independent oscillator though - LFO2 is one of the 13 sources
        # offered in the Modulation card below, same as LFO1/env1/env2.
        self.lfo2_rate_knob = Knob()
        self.lfo2_rate_knob.setRange(0, 99)
        self.lfo2_rate_knob.setDefaultValue(0)
        self.lfo2_rate_knob.setFixedSize(56, 56)
        self.lfo2_depth_knob = Knob()
        self.lfo2_depth_knob.setRange(0, 99)
        self.lfo2_depth_knob.setDefaultValue(0)
        self.lfo2_depth_knob.setFixedSize(56, 56)
        self.lfo2_delay_knob = Knob()
        self.lfo2_delay_knob.setRange(0, 99)
        self.lfo2_delay_knob.setDefaultValue(0)
        self.lfo2_delay_knob.setFixedSize(56, 56)

        self.lfo2_shape_combo = QComboBox()
        # only 3 shapes, not LFO1's 4 - LFO1WAVE's 4th ("Random") value was
        # measured by reading the pitch track LFO1 itself drives (see its
        # own notes); LFO2 drives pan instead, which hasn't been measured
        # the same way, so this offers only what s3k.params' LFO2WAVE desc
        # actually documents rather than assuming the same hidden 4th shape
        self.lfo2_shape_combo.addItems(["Triangle", "Sawtooth", "Square"])
        self.lfo2_shape_combo.currentIndexChanged.connect(
            lambda i: self._schedule_write("LFO2WAVE", "program", i)
        )

        lfo2_shape_label = QLabel("LFO2 shape")
        lfo2_shape_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lfo2_shape_column = QVBoxLayout()
        lfo2_shape_column.setSpacing(4)
        lfo2_shape_column.addWidget(
            lfo2_shape_label, alignment=Qt.AlignmentFlag.AlignHCenter
        )
        lfo2_shape_column.addWidget(self.lfo2_shape_combo)

        # LFO2TRIG's declared range is the full raw byte (0-255) with no
        # values={} map or measured note narrowing it (unlike LFO1WAVE/
        # LFO2WAVE's documented shape enums) - s3k.params itself doesn't say
        # what the real values mean. Measured on this project's own
        # S3000-series hardware (2026-09-21): it's a plain boolean, same
        # "trust the direct measurement, don't edit the dependency" call as
        # K_FREQ/B_PTCHD (AGENTS.md) - a third field in that list now.
        self.lfo2_trig_combo = QComboBox()
        self.lfo2_trig_combo.addItems(["Off", "On"])
        lfo2_trig_column = self._build_labeled_combo_column(
            "LFO2 retrig", self.lfo2_trig_combo
        )

        lfo2_rate_column, self.lfo2_rate_value_label = self._build_knob_column(
            "LFO2 rate", self.lfo2_rate_knob
        )
        lfo2_depth_column, self.lfo2_depth_value_label = self._build_knob_column(
            "LFO2 depth", self.lfo2_depth_knob
        )
        lfo2_delay_column, self.lfo2_delay_value_label = self._build_knob_column(
            "LFO2 delay", self.lfo2_delay_knob
        )

        self.polyph_combo = QComboBox()
        for voices in range(1, 33):  # 1-32 voices - the sampler's full range
            self.polyph_combo.addItem(str(voices), voices)
        self.polyph_combo.setEnabled(True)
        self.polyph_combo.currentIndexChanged.connect(
            lambda i: self._schedule_write(
                "POLYPH", "program", self.polyph_combo.itemData(i)
            )
        )
        polyph_label = QLabel("Polyphony")
        polyph_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        polyph_column = QVBoxLayout()
        polyph_column.setSpacing(4)
        polyph_column.addWidget(polyph_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        polyph_column.addWidget(self.polyph_combo)

        self.note_priority_combo = QComboBox()
        # index doubles as the raw PRIORT byte (0=low, 1=norm, 2=high,
        # 3=hold) - same "combo index is the value" convention as
        # lfo_shape_combo/LFO1WAVE above, no itemData needed
        self.note_priority_combo.addItems(["Low", "Normal", "High", "Hold"])
        self.note_priority_combo.setEnabled(True)
        self.note_priority_combo.currentIndexChanged.connect(
            lambda i: self._schedule_write("PRIORT", "program", i)
        )
        note_priority_label = QLabel("Note priority")
        note_priority_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        note_priority_column = QVBoxLayout()
        note_priority_column.setSpacing(4)
        note_priority_column.addWidget(
            note_priority_label, alignment=Qt.AlignmentFlag.AlignHCenter
        )
        note_priority_column.addWidget(self.note_priority_combo)

        self.midi_channel_combo = QComboBox()
        for channel in range(16):  # stored 0-15, panel shows channel 1-16
            self.midi_channel_combo.addItem(str(channel + 1), channel)
        # unlike the Multis tab's per-part PMCHAN (see _build_multis_tab -
        # that one is deliberately Omni-less because hardware measurement
        # showed values above 15 just clamp to Ch16 there), the program's
        # own PMCHAN has no such measured contradiction - s3k.params documents
        # 255 as a real OMNI sentinel for this region, so it's offered here
        self.midi_channel_combo.addItem("Omni", 255)
        self.midi_channel_combo.setEnabled(True)
        self.midi_channel_combo.currentIndexChanged.connect(
            lambda i: self._schedule_write(
                "PMCHAN", "program", self.midi_channel_combo.itemData(i)
            )
        )
        midi_channel_label = QLabel("MIDI channel")
        midi_channel_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        midi_channel_column = QVBoxLayout()
        midi_channel_column.setSpacing(4)
        midi_channel_column.addWidget(
            midi_channel_label, alignment=Qt.AlignmentFlag.AlignHCenter
        )
        midi_channel_column.addWidget(self.midi_channel_combo)

        # same encoding as the keygroup zone's Tune spinbox (see VTUNO
        # above): raw units are 1/256 semitone, 2.56 raw units per cent -
        # _semitones_to_tune_offset/_tune_offset_to_semitones do the
        # conversion for both
        self.program_tune_spinbox = QDoubleSpinBox()
        self.program_tune_spinbox.setRange(-50.0, 50.0)
        self.program_tune_spinbox.setSingleStep(0.01)
        self.program_tune_spinbox.setDecimals(2)
        self.program_tune_spinbox.setSuffix(" st")
        self.program_tune_spinbox.setFixedWidth(90)
        program_tune_label = QLabel("Tune")
        program_tune_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        program_tune_column = QVBoxLayout()
        program_tune_column.setSpacing(4)
        program_tune_column.addWidget(
            program_tune_label, alignment=Qt.AlignmentFlag.AlignHCenter
        )
        program_tune_column.addWidget(
            self.program_tune_spinbox, alignment=Qt.AlignmentFlag.AlignHCenter
        )

        # combos rather than spinboxes - same "combo index IS the raw byte"
        # convention as every other combo on this page (e.g. lfo1_sync_combo),
        # which fits naturally here since both fields are already a small
        # closed 0..24 range rather than free-form entry
        self.bend_up_combo = QComboBox()
        self.bend_up_combo.addItems([f"{i} st" for i in range(25)])
        self.bend_up_combo.setMaximumWidth(70)
        bend_up_column = self._build_labeled_combo_column("Bend up", self.bend_up_combo)

        self.bend_down_combo = QComboBox()
        # s3k.params declares B_PTCHD's range as 0-12 (asymmetric with
        # B_PTCH's 0-24), transcribed from the Akai spec - contradicted by
        # this project's own hardware: confirmed 0-24, matching bend-up,
        # on real S3000-series hardware in front of the user (2026-09-20).
        # Same call as K_FREQ above: trust the direct measurement, don't
        # edit the dependency (AGENTS.md)
        self.bend_down_combo.addItems([f"{i} st" for i in range(25)])
        self.bend_down_combo.setMaximumWidth(70)
        bend_down_column = self._build_labeled_combo_column(
            "Bend down", self.bend_down_combo
        )

        self.portamento_enable_combo = QComboBox()
        # s3k.params transcribes PORTEN as "PORTAMENTO ON/OFF" with no
        # decoded values={} map (unlike KXFADE/DESYNC's explicit
        # 0=OFF/1=ON) - inferred from this table's own convention, which
        # every other on/off-style program field follows without exception
        self.portamento_enable_combo.addItems(["Off", "On"])
        self.portamento_enable_combo.setMaximumWidth(70)
        portamento_enable_label = QLabel("Portamento")
        portamento_enable_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        portamento_enable_column = QVBoxLayout()
        portamento_enable_column.setSpacing(4)
        portamento_enable_column.addWidget(
            portamento_enable_label, alignment=Qt.AlignmentFlag.AlignHCenter
        )
        portamento_enable_column.addWidget(self.portamento_enable_combo)

        self.portamento_rate_knob = Knob()
        # PORTIME's declared range is the full byte (0-255, s3k.params has
        # no measurement narrowing it) but every other 2-digit performance
        # knob on this hardware (LFORAT, FILFRQ, VOSCL, ...) tops out at 99,
        # so 0-99 is assumed here rather than offering the raw byte
        self.portamento_rate_knob.setRange(0, 99)
        self.portamento_rate_knob.setDefaultValue(0)  # no known factory default
        self.portamento_rate_knob.setFixedSize(28, 28)
        portamento_rate_column, self.portamento_rate_value_label = (
            self._build_labeled_knob_value_column("Rate", self.portamento_rate_knob)
        )

        self.portamento_type_combo = QComboBox()
        for option_idx, (option_label, option_tooltip) in enumerate(
            _PORTAMENTO_TYPE_OPTIONS
        ):
            self.portamento_type_combo.addItem(option_label)
            self.portamento_type_combo.setItemData(
                option_idx, option_tooltip, Qt.ItemDataRole.ToolTipRole
            )
        self.portamento_type_combo.setToolTip(_PORTAMENTO_TYPE_OPTIONS[0][1])
        self.portamento_type_combo.currentIndexChanged.connect(
            lambda i, combo=self.portamento_type_combo: combo.setToolTip(
                _PORTAMENTO_TYPE_OPTIONS[i][1]
            )
        )
        self.portamento_type_combo.setMaximumWidth(70)
        portamento_type_label = QLabel("Type")
        portamento_type_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        portamento_type_column = QVBoxLayout()
        portamento_type_column.setSpacing(4)
        portamento_type_column.addWidget(
            portamento_type_label, alignment=Qt.AlignmentFlag.AlignHCenter
        )
        portamento_type_column.addWidget(self.portamento_type_combo)

        # constrained widths for the combo-only rows below
        self.lfo_shape_combo.setMaximumWidth(180)
        self.lfo1_sync_combo.setMaximumWidth(80)
        self.lfo2_shape_combo.setMaximumWidth(180)
        self.lfo2_trig_combo.setMaximumWidth(80)
        self.polyph_combo.setMaximumWidth(80)
        self.note_priority_combo.setMaximumWidth(90)
        self.midi_channel_combo.setMaximumWidth(80)

        # Volume, Pan & Velocity - level and stereo-field controls together
        volume_row = QHBoxLayout()
        volume_row.addLayout(pan_column)
        volume_row.addLayout(loud_column)
        volume_row.addLayout(velocity_column)
        volume_row.addStretch()
        volume_section = self._build_section_card("Volume, Pan & Velocity", volume_row)

        # LFO1 - every LFO1 control (shape, rate, depth, delay) in one place.
        # Titled "LFO1" now (was just "LFO") now that LFO2 has its own card
        # below - keeping the old bare title once there were two would read
        # as which one is "the" LFO.
        lfo_knobs_row = QHBoxLayout()
        lfo_knobs_row.addLayout(lfo_rate_column)
        lfo_knobs_row.addLayout(lfo_depth_column)
        lfo_knobs_row.addLayout(lfo_delay_column)
        lfo_knobs_row.addStretch()
        lfo_shape_row = QHBoxLayout()
        lfo_shape_row.addLayout(lfo_shape_column)
        lfo_shape_row.addLayout(lfo1_sync_column)
        lfo_shape_row.addStretch()
        lfo_section = self._build_section_card("LFO1", lfo_knobs_row, lfo_shape_row)

        # LFO2 - same shape as LFO1's card above. Unlike LFO1 (which
        # primarily drives pitch - see LFO1WAVE's notes), this hardware
        # wires LFO2's own rate/depth/delay/shape permanently to Pan (an
        # auto-pan effect) - see the PANRAT/PANDEP/PANDEL comment above.
        # LFO2 remains selectable as a source anywhere else in the
        # Modulation card below, same as any other source.
        lfo2_knobs_row = QHBoxLayout()
        lfo2_knobs_row.addLayout(lfo2_rate_column)
        lfo2_knobs_row.addLayout(lfo2_depth_column)
        lfo2_knobs_row.addLayout(lfo2_delay_column)
        lfo2_knobs_row.addStretch()
        lfo2_shape_row = QHBoxLayout()
        lfo2_shape_row.addLayout(lfo2_shape_column)
        lfo2_shape_row.addLayout(lfo2_trig_column)
        lfo2_shape_row.addStretch()
        lfo2_section = self._build_section_card("LFO2", lfo2_knobs_row, lfo2_shape_row)

        # Pitch - tuning offset and pitch-bend range, up and down
        pitch_row = QHBoxLayout()
        pitch_row.addLayout(program_tune_column)
        pitch_row.addLayout(bend_up_column)
        pitch_row.addLayout(bend_down_column)
        pitch_row.addStretch()
        pitch_section = self._build_section_card("Pitch", pitch_row)

        # Voice & MIDI - how the program responds to incoming MIDI and
        # allocates/steals voices
        voice_row = QHBoxLayout()
        voice_row.addLayout(midi_channel_column)
        voice_row.addLayout(polyph_column)
        voice_row.addLayout(note_priority_column)
        voice_row.addStretch()
        voice_section = self._build_section_card("Voice & MIDI", voice_row)

        # Portamento
        portamento_row = QHBoxLayout()
        portamento_row.addLayout(portamento_enable_column)
        portamento_row.addLayout(portamento_rate_column)
        portamento_row.addLayout(portamento_type_column)
        portamento_row.addStretch()
        portamento_section = self._build_section_card("Portamento", portamento_row)

        # Modulation - the assignable modulation matrix (MODS*/MODV*
        # fields). Every destination's SOURCE choice is a program-wide
        # decision (MODSPAN*/MODSAMP*/MODSLFO*/MODSFILT*/MODSPITCH all live
        # in the "program" region, shared by every keygroup), so this whole
        # card lives on the Program tab. Amounts follow the source onto
        # this tab too EXCEPT where the hardware stores the amount
        # per-keygroup instead (Loudness slot 3, Filter Frequency, Pitch) -
        # those rows show only the source here; their amount spinboxes are
        # on the Keygroup tab's own Modulation card instead (see
        # detail_container_layout below). LFO1's rate/depth/delay each get
        # exactly one assignable slot (MODSLFOT/L/D), not three, since
        # that's all the hardware offers per sub-parameter - unlike Pan/
        # Loudness/Filter Frequency's genuine 3 slots.
        mod_grid = QGridLayout()
        mod_grid.setHorizontalSpacing(14)
        mod_grid.setVerticalSpacing(10)
        # max slot count of any row below (Pan/Loudness/Filter Frequency) -
        # the header only needs as many "Slot N" columns as the widest row
        header_rows = self._build_mod_matrix_header(mod_grid, 3)

        pan1_combo, pan1_amount, self.mod_pan1_knob, self.mod_pan1_value_label = (
            self._build_mod_slot("MODSPAN1", "program", "MODVPAN1", "program")
        )
        self.mod_pan1_combo = pan1_combo
        pan2_combo, pan2_amount, self.mod_pan2_knob, self.mod_pan2_value_label = (
            self._build_mod_slot("MODSPAN2", "program", "MODVPAN2", "program")
        )
        self.mod_pan2_combo = pan2_combo
        pan3_combo, pan3_amount, self.mod_pan3_knob, self.mod_pan3_value_label = (
            self._build_mod_slot("MODSPAN3", "program", "MODVPAN3", "program")
        )
        self.mod_pan3_combo = pan3_combo
        self._build_mod_matrix_row(
            mod_grid,
            header_rows + 0,
            "Pan",
            (pan1_combo, pan1_amount),
            (pan2_combo, pan2_amount),
            (pan3_combo, pan3_amount),
        )

        amp1_combo, amp1_amount, self.mod_amp1_knob, self.mod_amp1_value_label = (
            self._build_mod_slot("MODSAMP1", "program", "MODVAMP1", "program")
        )
        self.mod_amp1_combo = amp1_combo
        amp2_combo, amp2_amount, self.mod_amp2_knob, self.mod_amp2_value_label = (
            self._build_mod_slot("MODSAMP2", "program", "MODVAMP2", "program")
        )
        self.mod_amp2_combo = amp2_combo
        self.mod_amp3_combo = self._build_mod_source_only_column("MODSAMP3", "program")
        self._build_mod_matrix_row(
            mod_grid,
            header_rows + 1,
            "Loudness",
            (amp1_combo, amp1_amount),
            (amp2_combo, amp2_amount),
            (self.mod_amp3_combo, None),
        )

        (
            lfo1_rate_combo,
            lfo1_rate_amount,
            self.mod_lfo1_rate_knob,
            self.mod_lfo1_rate_value_label,
        ) = self._build_mod_slot("MODSLFOT", "program", "MODVLFOR", "program")
        self.mod_lfo1_rate_combo = lfo1_rate_combo
        self._build_mod_matrix_row(
            mod_grid, header_rows + 2, "LFO1 Rate", (lfo1_rate_combo, lfo1_rate_amount)
        )

        (
            lfo1_depth_combo,
            lfo1_depth_amount,
            self.mod_lfo1_depth_knob,
            self.mod_lfo1_depth_value_label,
        ) = self._build_mod_slot("MODSLFOL", "program", "MODVLVOL", "program")
        self.mod_lfo1_depth_combo = lfo1_depth_combo
        self._build_mod_matrix_row(
            mod_grid,
            header_rows + 3,
            "LFO1 Depth",
            (lfo1_depth_combo, lfo1_depth_amount),
        )

        (
            lfo1_delay_combo,
            lfo1_delay_amount,
            self.mod_lfo1_delay_knob,
            self.mod_lfo1_delay_value_label,
        ) = self._build_mod_slot("MODSLFOD", "program", "MODVLFOD", "program")
        self.mod_lfo1_delay_combo = lfo1_delay_combo
        self._build_mod_matrix_row(
            mod_grid,
            header_rows + 4,
            "LFO1 Delay",
            (lfo1_delay_combo, lfo1_delay_amount),
        )

        self.mod_filt1_combo = self._build_mod_source_only_column(
            "MODSFILT1", "program"
        )
        self.mod_filt2_combo = self._build_mod_source_only_column(
            "MODSFILT2", "program"
        )
        self.mod_filt3_combo = self._build_mod_source_only_column(
            "MODSFILT3", "program"
        )
        self._build_mod_matrix_row(
            mod_grid,
            header_rows + 5,
            "Filter Freq.",
            (self.mod_filt1_combo, None),
            (self.mod_filt2_combo, None),
            (self.mod_filt3_combo, None),
        )

        self.mod_pitch_combo = self._build_mod_source_only_column(
            "MODSPITCH", "program"
        )
        self._build_mod_matrix_row(
            mod_grid, header_rows + 6, "Pitch", (self.mod_pitch_combo, None)
        )

        mod_footnote = QLabel(
            "Loudness slot 3, Filter Frequency and Pitch amounts are set "
            "per keygroup, on the Keygroup tab's own Modulation card."
        )
        mod_footnote.setWordWrap(True)
        mod_footnote.setObjectName("modFootnote")
        mod_footnote_row = QHBoxLayout()
        mod_footnote_row.addWidget(mod_footnote)

        # 7 destination rows above (Pan, Loudness, LFO1 Rate/Depth/Delay,
        # Filter Frequency, Pitch); boundaries separate the label column
        # from Slot 1, then Slot 1|2 and Slot 2|3 - never a slot's own
        # Source|Amount pair (see _ModMatrixGrid's own docstring)
        mod_matrix = _ModMatrixGrid(mod_grid, header_rows, 7, [(0, 1), (2, 3), (4, 5)])
        mod_grid_row = QVBoxLayout()
        mod_grid_row.setContentsMargins(0, 0, 0, 0)
        mod_grid_row.addWidget(mod_matrix)

        modulation_section = self._build_section_card(
            "Modulation", mod_grid_row, mod_footnote_row
        )

        # two cards per row rather than one long stacked column - halves
        # the page's height for the same content. Paired by content shape:
        # the two 3-knob cards, the two LFO cards (now that LFO2 has its
        # own card too), then the two combo/spinbox-only cards - no card
        # needs to be a leftover "odd one out" now that LFO2 exists.
        # Modulation is far bigger than any of these and gets a row of
        # its own, same as Portamento always has.
        program_row1 = QHBoxLayout()
        program_row1.addWidget(volume_section, stretch=1)
        program_row1.addWidget(pitch_section, stretch=1)

        program_row2 = QHBoxLayout()
        program_row2.addWidget(lfo_section, stretch=1)
        program_row2.addWidget(lfo2_section, stretch=1)

        program_row3 = QHBoxLayout()
        program_row3.addWidget(voice_section, stretch=1)
        program_row3.addWidget(portamento_section, stretch=1)

        program_page = QWidget()
        program_page_layout = QVBoxLayout()
        program_page_layout.setSpacing(12)
        program_page_layout.addWidget(name_section)
        program_page_layout.addLayout(program_row1)
        program_page_layout.addLayout(program_row2)
        program_page_layout.addLayout(program_row3)
        program_page_layout.addWidget(modulation_section)
        program_page_layout.addStretch()
        program_page.setLayout(program_page_layout)

        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(self._build_scroll_area(program_page))
        self.detail_stack.addWidget(self._build_scroll_area(detail_container))

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)

        refresh_button = QPushButton("⟳ Refresh")
        refresh_button.setToolTip(
            "Reload the current program/keygroup from the hardware (⌘R) - "
            "use this if you've changed something on the sampler's own front panel"
        )
        refresh_button.clicked.connect(self._refresh_from_hardware)

        content_layout = QHBoxLayout()
        content_layout.addWidget(programs_container)
        content_layout.addWidget(keygroups_container)
        content_layout.addWidget(self.detail_stack, stretch=1)
        programs_tab_page = QWidget()
        programs_tab_page.setLayout(content_layout)

        multis_tab_page = self._build_multis_tab()
        samples_tab_page = self._build_samples_tab()

        self.main_tabs = QTabWidget()
        # same full-width tab bar as the MIDI Settings dialog - must be
        # installed before any tabs are added, or they'd be dropped
        self.main_tabs.setTabBar(FullWidthTabBar(self.main_tabs))
        self.main_tabs.addTab(multis_tab_page, "Multis")
        self.main_tabs.addTab(programs_tab_page, "Programs")
        self._samples_tab_index = self.main_tabs.addTab(samples_tab_page, "Samples")
        # Multis stays the first tab, but isn't fully working yet - open on
        # Programs instead
        self.main_tabs.setCurrentIndex(1)
        self.main_tabs.currentChanged.connect(self._on_main_tab_changed)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(refresh_button)
        # ties the loading indicator to the action that most often triggers
        # a hardware sync, rather than tucking it into the status bar
        bottom_row.addWidget(self._loading_progress)
        bottom_row.addStretch()
        bottom_row.addWidget(close_button)

        main_layout = QVBoxLayout()
        main_layout.addWidget(self.main_tabs)
        main_layout.addLayout(bottom_row)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        refresh_action = QAction("Refresh from Hardware", self)
        refresh_action.setShortcut("Ctrl+R")  # shows as ⌘R on macOS
        refresh_action.triggered.connect(self._refresh_from_hardware)
        hardware_menu = self.menuBar().addMenu("&Hardware")
        hardware_menu.addAction(refresh_action)

        # only one of {dashboard, program editor} is ever open at a time -
        # two simultaneous MIDI connections to the hardware is untested and
        # may not be safe - mirrors the dashboard's own "&Window" menu
        # (main_window.py) so the same Ctrl+1/Ctrl+2 shortcuts work no
        # matter which window currently has focus
        window_menu = self.menuBar().addMenu("&Window")

        dashboard_action = QAction("Transfer Dashboard", self)
        dashboard_action.setShortcut("Ctrl+1")
        # closing (rather than hiding outright) reuses closeEvent()'s
        # existing "show the main window again" cleanup below
        dashboard_action.triggered.connect(self.close)
        window_menu.addAction(dashboard_action)

        editor_action = QAction("Program Editor", self)
        editor_action.setShortcut("Ctrl+2")
        editor_action.setEnabled(False)  # this window IS the program editor
        window_menu.addAction(editor_action)

        # Qt has no MenuRole for "check for updates" (only About/Preferences/
        # Quit get auto-relocated into the native app menu on macOS - see
        # QAction.MenuRole), so this stays a plain Help menu on every
        # platform, same as main_window.py's own
        help_menu = self.menuBar().addMenu("&Help")

        about_action = QAction("About AKAISDS...", self)
        about_action.setMenuRole(QAction.MenuRole.AboutRole)
        about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_action)

        check_for_updates_action = QAction("Check for Updates...", self)
        check_for_updates_action.triggered.connect(self._check_for_updates_manual)
        help_menu.addAction(check_for_updates_action)

        self._update_runner = UpdateCheckRunner(self)

        # same idea as the dashboard's status bar (dashboard.py), but that's
        # a plain QWidget so it builds its own QStatusBar into its layout -
        # this window is a QMainWindow, which docks one below the central
        # widget (bottom row included) natively, full width, for free
        self.status_bar = QStatusBar()
        self.status_bar.setSizeGripEnabled(False)
        self.status_bar.showMessage("Select a keygroup")
        self.setStatusBar(self.status_bar)

        self.program_list.currentItemChanged.connect(self._on_program_selected)
        self.program_list.itemClicked.connect(
            lambda item: self.detail_stack.setCurrentIndex(0)
        )
        self.keygroup_list.currentItemChanged.connect(self._on_keygroup_selected)
        self.keygroup_list.itemClicked.connect(
            lambda item: self.detail_stack.setCurrentIndex(1)
        )
        # keeps the two delete actions' enabled state in lockstep with
        # selection/roster changes without threading a call through every
        # handler that can change either - itemSelectionChanged fires from
        # clear()/addItems()/setCurrentItem() alike, including the ones
        # inside _on_programs_loaded/_on_keygroups_loaded
        self.program_list.itemSelectionChanged.connect(
            self._update_list_context_actions_enabled
        )
        self.keygroup_list.itemSelectionChanged.connect(
            self._update_list_context_actions_enabled
        )
        self.sample_list_widget.currentItemChanged.connect(self._on_sample_selected)
        self.sample_list_widget.itemSelectionChanged.connect(
            self._update_list_context_actions_enabled
        )
        self.waveform_view.load_requested.connect(self._load_sample_waveform)
        self.waveform_view.marker_committed.connect(self._on_waveform_marker_committed)
        self.waveform_view.markers_changed.connect(self._update_marker_spinboxes)
        self.waveform_view.view_changed.connect(self._on_waveform_view_changed)
        self.waveform_scrollbar.valueChanged.connect(self._on_waveform_scrollbar_moved)
        self._worker.submit_program_list()

        # enable knobs and wire their (debounced) writes
        self.cutoff_knob.setEnabled(True)
        self._wire_knob_write(
            self.cutoff_knob,
            "FILFRQ",
            "keygroup",
            keygroup_index_getter=self.keygroup_list.currentRow,
        )
        self.resonance_knob.setEnabled(True)
        self._wire_knob_write(
            self.resonance_knob,
            "FILQ",
            "keygroup",
            keygroup_index_getter=self.keygroup_list.currentRow,
        )
        self.key_filter_track_knob.setEnabled(True)
        self._wire_knob_write(
            self.key_filter_track_knob,
            "K_FREQ",
            "keygroup",
            keygroup_index_getter=self.keygroup_list.currentRow,
        )
        self.note_lo_spinbox.setEnabled(True)
        self.note_lo_spinbox.valueChanged.connect(self._on_note_range_changed)
        self.note_lo_spinbox.editingFinished.connect(self._commit_note_range)
        self.note_hi_spinbox.setEnabled(True)
        self.note_hi_spinbox.valueChanged.connect(self._on_note_range_changed)
        self.note_hi_spinbox.editingFinished.connect(self._commit_note_range)
        self.pan_knob.setEnabled(True)
        self._wire_knob_write(self.pan_knob, "PANPOS", "program")
        self.loud_knob.setEnabled(True)
        self._wire_knob_write(self.loud_knob, "PRLOUD", "program")
        self.velocity_knob.setEnabled(True)
        self._wire_knob_write(self.velocity_knob, "V_LOUD", "program")
        self.lfo_rate_knob.setEnabled(True)
        self._wire_knob_write(self.lfo_rate_knob, "LFORAT", "program")
        self.lfo_depth_knob.setEnabled(True)
        self._wire_knob_write(self.lfo_depth_knob, "LFODEP", "program")
        self.lfo_delay_knob.setEnabled(True)
        self._wire_knob_write(self.lfo_delay_knob, "LFODEL", "program")
        self.lfo1_sync_combo.setEnabled(True)
        self._wire_combo_write(self.lfo1_sync_combo, "DESYNC", "program")
        self.lfo2_rate_knob.setEnabled(True)
        self._wire_knob_write(self.lfo2_rate_knob, "PANRAT", "program")
        self.lfo2_depth_knob.setEnabled(True)
        self._wire_knob_write(self.lfo2_depth_knob, "PANDEP", "program")
        self.lfo2_delay_knob.setEnabled(True)
        self._wire_knob_write(self.lfo2_delay_knob, "PANDEL", "program")
        self.lfo2_trig_combo.setEnabled(True)
        self._wire_combo_write(self.lfo2_trig_combo, "LFO2TRIG", "program")
        self.program_tune_spinbox.setEnabled(True)
        self._wire_spinbox_write(
            self.program_tune_spinbox,
            "PTUNO",
            "program",
            value_converter=self._semitones_to_tune_offset,
        )
        self.bend_up_combo.setEnabled(True)
        self._wire_combo_write(self.bend_up_combo, "B_PTCH", "program")
        self.bend_down_combo.setEnabled(True)
        self._wire_combo_write(self.bend_down_combo, "B_PTCHD", "program")
        self.portamento_enable_combo.setEnabled(True)
        self._wire_combo_write(self.portamento_enable_combo, "PORTEN", "program")
        self.portamento_rate_knob.setEnabled(True)
        self._wire_knob_write(self.portamento_rate_knob, "PORTIME", "program")
        self.portamento_type_combo.setEnabled(True)
        self._wire_combo_write(self.portamento_type_combo, "PORTYPE", "program")
        for knob in (
            self.attack1_knob,
            self.decay1_knob,
            self.sustain1_knob,
            self.release1_knob,
            *self._env2_rate_knobs,
            *self._env2_level_knobs,
        ):
            knob.setEnabled(True)

    def _on_programs_loaded(self, programs):
        # also reached on every Refresh (see _refresh_from_hardware), not
        # just the initial load - is_refresh distinguishes the two so a
        # program created/renamed/deleted on the hardware since the editor
        # opened actually shows up rather than only appearing after the
        # window is closed and reopened
        previous_program = (
            self.program_list.currentItem().text()
            if self.program_list.currentItem()
            else None
        )
        is_refresh = self.program_list.count() > 0

        self.program_list.blockSignals(True)
        self.program_list.clear()
        self.program_list.addItems(programs)
        self.program_list.blockSignals(False)

        self._worker.submit_sample_list()

        # a part can only be assigned a program that actually exists -
        # repopulate all 16 combos with the current name list. Blank first
        # item (same convention as the zone sample combo) is the default;
        # on a refresh, each part's existing selection is kept if that
        # program still exists under the same name
        for combo in self._multi_program_combos:
            previous_selection = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("-")
            combo.addItems(programs)
            match = combo.findText(previous_selection) if is_refresh else -1
            combo.setCurrentIndex(match if match >= 0 else 0)
            combo.blockSignals(False)

        if is_refresh:
            # restore whichever program was selected before the refresh
            # (falling back to the first one if it was renamed/deleted)
            # rather than resetting to the top of the list every time
            match = (
                self.program_list.findItems(previous_program, Qt.MatchFlag.MatchExactly)
                if previous_program is not None
                else []
            )
            self.program_list.setCurrentItem(
                match[0] if match else self.program_list.item(0)
            )
        else:
            # first load only - _on_samples_loaded selects row 0 once
            # samples finish loading, which is what actually starts the
            # first keygroup load; refresh reloads reach _refresh_multi_parts
            # separately, from _refresh_from_hardware, so its "Multi parts
            # refreshed" confirmation flag isn't clobbered by this handler
            self._refresh_multi_parts()

    def _on_program_load_failed(self, error_message):
        self.status_bar.showMessage(f"Couldn't load programs: {error_message}")

    def _on_worker_busy_changed(self, busy):
        if busy:
            # a pending hide from a moment-ago False is cancelled outright -
            # this is what collapses a burst of back-to-back jobs into one
            # continuous visible span instead of flickering between them
            self._busy_hide_timer.stop()
            # isHidden(), not isVisible() - isVisible() is false for any
            # widget whose top-level window hasn't been shown yet
            # (unshown in tests; briefly true during real startup too),
            # which would restart this timer's countdown on every job in
            # a burst instead of only the first
            if (
                not self._busy_show_timer.isActive()
                and self._loading_progress.isHidden()
            ):
                self._busy_show_timer.start()
        else:
            # not hidden immediately - _confirm_worker_idle only actually
            # hides once this fires without a new busy_changed(True)
            # cancelling it first (see the comment on these two timers above)
            self._busy_hide_timer.start()

    def _confirm_worker_idle(self):
        self._busy_show_timer.stop()
        self._loading_progress.setVisible(False)

    def _refresh_multi_parts(self, *, show_confirmation=False):
        self._multi_refresh_in_progress = show_confirmation
        self._worker.submit_multi_parts()

    def _on_multi_parts_loaded(self, parts):
        # blockSignals during every one of these hardware-loaded setting
        # calls - currentIndexChanged is wired to write the value straight
        # back to hardware (see _on_multi_part_program_changed/
        # _on_multi_part_channel_changed), and an unblocked call here would
        # schedule a write that just echoes the just-loaded value back
        for part_index, (program_name, channel, level, pan) in enumerate(parts):
            program_combo = self._multi_program_combos[part_index]
            program_combo.blockSignals(True)
            match = program_combo.findText(program_name) if program_name else -1
            program_combo.setCurrentIndex(match if match >= 0 else 0)
            program_combo.blockSignals(False)

            channel_combo = self._multi_channel_combos[part_index]
            channel_combo.blockSignals(True)
            channel_combo.setCurrentIndex(channel_combo.findData(channel))
            channel_combo.blockSignals(False)

            level_knob = self._multi_level_knobs[part_index]
            level_knob.blockSignals(True)
            level_knob.setValue(level)
            level_knob.blockSignals(False)
            self._multi_level_value_labels[part_index].setText(str(level))

            pan_knob = self._multi_pan_knobs[part_index]
            pan_knob.blockSignals(True)
            pan_knob.setValue(pan)
            pan_knob.blockSignals(False)
            self._multi_pan_value_labels[part_index].setText(str(pan))

        if self._multi_refresh_in_progress:
            self._multi_refresh_in_progress = False
            self.status_bar.showMessage("Multi parts refreshed from hardware")

    def _on_multi_load_failed(self, error_message):
        self._multi_refresh_in_progress = False
        self.status_bar.showMessage(f"Couldn't load multi: {error_message}")

    def _refresh_from_hardware(self):
        # re-fetches the current program's keygroup list/ranges and
        # program-level knobs (Pan/LFO/Polyphony) from the sampler, so
        # changes made on the hardware's own front panel don't leave this
        # window showing stale values. Restores whatever the user was
        # looking at (which keygroup, which zone tab, program vs keygroup
        # page) rather than resetting the view. Also always refreshes the
        # program list itself (a program created/renamed/deleted on the
        # hardware since the editor opened used to only show up after
        # closing and reopening the window) and the Multis tab's 16 parts,
        # independent of program selection.
        self._worker.submit_program_list()
        self._refresh_multi_parts(show_confirmation=True)

        program_index = self.program_list.currentRow()
        if program_index < 0:
            return  # no program selected yet - nothing else to refresh

        self.status_bar.showMessage("Refreshing from hardware…")
        self._refresh_in_progress = True
        self._pending_restore_state = {
            "keygroup_index": self.keygroup_list.currentRow(),
            "stack_index": self.detail_stack.currentIndex(),
            "zone_index": self._zone_button_group.checkedId(),
        }

        self._worker.submit_keygroups(program_index)

    def _on_program_selected(self, current, previous):
        self.keygroup_list.clear()
        self._keygroup_ranges = []
        self.keygroup_range_bar.set_ranges([])
        self.status_bar.showMessage("Select a keygroup")
        self.detail_stack.setCurrentIndex(0)
        if current is None:
            self.program_name_edit.clear()
            return
        # program_list's own item text already IS the current PRNAME - it
        # comes straight from program_list() on the bridge - so this needs
        # no separate hardware round-trip the way every other program field
        # does via program_values in _on_keygroups_loaded below
        self.program_name_edit.setText(current.text())
        program_index = self.program_list.currentRow()
        self._worker.submit_keygroups(program_index)

    def _on_keygroups_loaded(self, program_index, keygroup_ranges, program_values):
        # ignore result for program the user has already clicked away from
        if program_index != self.program_list.currentRow():
            return
        # idempotent even though _on_program_selected already clears this -
        # _refresh_from_hardware() reaches this same handler without going
        # through _on_program_selected first
        self.keygroup_list.clear()
        for i, (lo, hi) in enumerate(keygroup_ranges):
            self._add_keygroup_row(i, lo, hi)
        self._keygroup_ranges = [list(r) for r in keygroup_ranges]
        self.keygroup_range_bar.set_ranges(self._keygroup_ranges)
        # blockSignals during every one of these loaded-from-hardware
        # setValue() calls - valueChanged now also schedules a debounced
        # write (see _wire_knob_write), so an unblocked setValue() here
        # would schedule a write that just echoes the value straight back
        # to the hardware a moment later. The value label and graph/other
        # side effects are set explicitly right here instead of relying on
        # the (now blocked) valueChanged connections.
        self.pan_knob.blockSignals(True)
        self.pan_knob.setValue(program_values["PANPOS"])
        self.pan_knob.blockSignals(False)
        self.pan_value_label.setText(str(program_values["PANPOS"]))
        self.loud_knob.blockSignals(True)
        self.loud_knob.setValue(program_values["PRLOUD"])
        self.loud_knob.blockSignals(False)
        self.loud_value_label.setText(str(program_values["PRLOUD"]))
        self.velocity_knob.blockSignals(True)
        self.velocity_knob.setValue(program_values["V_LOUD"])
        self.velocity_knob.blockSignals(False)
        self.velocity_value_label.setText(str(program_values["V_LOUD"]))
        self.lfo_rate_knob.blockSignals(True)
        self.lfo_rate_knob.setValue(program_values["LFORAT"])
        self.lfo_rate_knob.blockSignals(False)
        self.lfo_rate_value_label.setText(str(program_values["LFORAT"]))
        self.lfo_depth_knob.blockSignals(True)
        self.lfo_depth_knob.setValue(program_values["LFODEP"])
        self.lfo_depth_knob.blockSignals(False)
        self.lfo_depth_value_label.setText(str(program_values["LFODEP"]))
        self.lfo_delay_knob.blockSignals(True)
        self.lfo_delay_knob.setValue(program_values["LFODEL"])
        self.lfo_delay_knob.blockSignals(False)
        self.lfo_delay_value_label.setText(str(program_values["LFODEL"]))
        self.lfo_shape_combo.blockSignals(True)
        self.lfo_shape_combo.setCurrentIndex(program_values["LFO1WAVE"])
        self.lfo_shape_combo.blockSignals(False)
        self.lfo1_sync_combo.blockSignals(True)
        # DESYNC is the raw byte and IS the combo index here (see
        # lfo1_sync_combo's own construction comment on the polarity) -
        # setCurrentIndex(raw value) directly, no inversion
        self.lfo1_sync_combo.setCurrentIndex(program_values["DESYNC"])
        self.lfo1_sync_combo.blockSignals(False)

        # LFO2 - same shape as LFO1's own load block just above
        self.lfo2_rate_knob.blockSignals(True)
        self.lfo2_rate_knob.setValue(program_values["PANRAT"])
        self.lfo2_rate_knob.blockSignals(False)
        self.lfo2_rate_value_label.setText(str(program_values["PANRAT"]))
        self.lfo2_depth_knob.blockSignals(True)
        self.lfo2_depth_knob.setValue(program_values["PANDEP"])
        self.lfo2_depth_knob.blockSignals(False)
        self.lfo2_depth_value_label.setText(str(program_values["PANDEP"]))
        self.lfo2_delay_knob.blockSignals(True)
        self.lfo2_delay_knob.setValue(program_values["PANDEL"])
        self.lfo2_delay_knob.blockSignals(False)
        self.lfo2_delay_value_label.setText(str(program_values["PANDEL"]))
        self.lfo2_shape_combo.blockSignals(True)
        self.lfo2_shape_combo.setCurrentIndex(program_values["LFO2WAVE"])
        self.lfo2_shape_combo.blockSignals(False)
        self.lfo2_trig_combo.blockSignals(True)
        self.lfo2_trig_combo.setCurrentIndex(program_values["LFO2TRIG"])
        self.lfo2_trig_combo.blockSignals(False)

        # Modulation matrix (Program tab half) - source combos use "index
        # is the value" like every other combo on this page, so
        # setCurrentIndex(raw value) is correct directly (see
        # _MOD_SOURCE_LABELS); amount spinboxes just take the raw -50..50
        # value. A loop here rather than spelling out each one by hand,
        # same call as _on_multi_parts_loaded's per-part loop below - the
        # repetition across this many near-identical fields is what that
        # precedent is for.
        program_mod_combos = [
            (self.mod_pan1_combo, "MODSPAN1"),
            (self.mod_pan2_combo, "MODSPAN2"),
            (self.mod_pan3_combo, "MODSPAN3"),
            (self.mod_amp1_combo, "MODSAMP1"),
            (self.mod_amp2_combo, "MODSAMP2"),
            (self.mod_amp3_combo, "MODSAMP3"),
            (self.mod_lfo1_rate_combo, "MODSLFOT"),
            (self.mod_lfo1_depth_combo, "MODSLFOL"),
            (self.mod_lfo1_delay_combo, "MODSLFOD"),
            (self.mod_filt1_combo, "MODSFILT1"),
            (self.mod_filt2_combo, "MODSFILT2"),
            (self.mod_filt3_combo, "MODSFILT3"),
            (self.mod_pitch_combo, "MODSPITCH"),
        ]
        for combo, field in program_mod_combos:
            combo.blockSignals(True)
            combo.setCurrentIndex(program_values[field])
            combo.blockSignals(False)

        program_mod_knobs = [
            (self.mod_pan1_knob, self.mod_pan1_value_label, "MODVPAN1"),
            (self.mod_pan2_knob, self.mod_pan2_value_label, "MODVPAN2"),
            (self.mod_pan3_knob, self.mod_pan3_value_label, "MODVPAN3"),
            (self.mod_amp1_knob, self.mod_amp1_value_label, "MODVAMP1"),
            (self.mod_amp2_knob, self.mod_amp2_value_label, "MODVAMP2"),
            (self.mod_lfo1_rate_knob, self.mod_lfo1_rate_value_label, "MODVLFOR"),
            (self.mod_lfo1_depth_knob, self.mod_lfo1_depth_value_label, "MODVLVOL"),
            (self.mod_lfo1_delay_knob, self.mod_lfo1_delay_value_label, "MODVLFOD"),
        ]
        for knob, value_label, field in program_mod_knobs:
            knob.blockSignals(True)
            knob.setValue(program_values[field])
            knob.blockSignals(False)
            value_label.setText(str(program_values[field]))

        self.polyph_combo.blockSignals(True)
        self.polyph_combo.setCurrentIndex(
            self.polyph_combo.findData(program_values["POLYPH"])
        )
        self.polyph_combo.blockSignals(False)
        self.note_priority_combo.blockSignals(True)
        self.note_priority_combo.setCurrentIndex(program_values["PRIORT"])
        self.note_priority_combo.blockSignals(False)
        self.midi_channel_combo.blockSignals(True)
        self.midi_channel_combo.setCurrentIndex(
            self.midi_channel_combo.findData(program_values["PMCHAN"])
        )
        self.midi_channel_combo.blockSignals(False)
        self.program_tune_spinbox.blockSignals(True)
        self.program_tune_spinbox.setValue(
            self._tune_offset_to_semitones(program_values["PTUNO"])
        )
        self.program_tune_spinbox.blockSignals(False)
        self.bend_up_combo.blockSignals(True)
        self.bend_up_combo.setCurrentIndex(program_values["B_PTCH"])
        self.bend_up_combo.blockSignals(False)
        self.bend_down_combo.blockSignals(True)
        self.bend_down_combo.setCurrentIndex(program_values["B_PTCHD"])
        self.bend_down_combo.blockSignals(False)
        self.portamento_enable_combo.blockSignals(True)
        self.portamento_enable_combo.setCurrentIndex(program_values["PORTEN"])
        self.portamento_enable_combo.blockSignals(False)
        self.portamento_rate_knob.blockSignals(True)
        self.portamento_rate_knob.setValue(program_values["PORTIME"])
        self.portamento_rate_knob.blockSignals(False)
        self.portamento_rate_value_label.setText(str(program_values["PORTIME"]))
        self.portamento_type_combo.blockSignals(True)
        self.portamento_type_combo.setCurrentIndex(program_values["PORTYPE"])
        self.portamento_type_combo.blockSignals(False)
        self.portamento_type_combo.setToolTip(
            _PORTAMENTO_TYPE_OPTIONS[self.portamento_type_combo.currentIndex()][1]
        )

        # set by _refresh_from_hardware() and _on_keygroup_deleted() -
        # restores whatever the user was looking at before the reload (a
        # plain program selection never sets this, so this is a no-op on
        # the normal load path)
        restore = self._pending_restore_state
        self._pending_restore_state = None
        if restore is not None:
            keygroup_index = restore["keygroup_index"]
            if keygroup_index >= 0 and self.keygroup_list.count() > 0:
                # clamp rather than requiring an exact match - covers
                # deleting the keygroup that was last in the list, where
                # the old index is now one past the new last row (see
                # _on_keygroup_deleted)
                keygroup_index = min(keygroup_index, self.keygroup_list.count() - 1)
                self.keygroup_list.setCurrentRow(keygroup_index)
                self.detail_stack.setCurrentIndex(restore["stack_index"])
                zone_index = restore["zone_index"]
                if zone_index >= 0:
                    self._zone_button_group.button(zone_index).setChecked(True)
                    self._zone_stack.setCurrentIndex(zone_index)

        if self._refresh_in_progress:
            self._refresh_in_progress = False
            self.status_bar.showMessage("Refreshed from hardware")

    def _on_load_failed(self, program_index, error_message):
        self._refresh_in_progress = False
        self._pending_restore_state = None
        if program_index != self.program_list.currentRow():
            return
        self.status_bar.showMessage(f"Couldn't load keygroups: {error_message}")

    def _update_list_context_actions_enabled(self):
        # a lone remaining program can't actually be deleted - the hardware
        # acknowledges DELP against it and silently ignores it (see
        # s3ked's own DemoBridge.delete_program docstring, which reproduces
        # this on purpose: "The last program cannot be deleted... it
        # acknowledges the delete and the list stays at one"). Disabling
        # the action here avoids a confirm dialog whose "Yes" visibly does
        # nothing. Renaming has no such restriction - even the one
        # remaining program can be renamed.
        has_program = self.program_list.currentRow() >= 0
        self._rename_program_action.setEnabled(has_program)
        self._delete_program_action.setEnabled(
            has_program and self.program_list.count() > 1
        )
        self._delete_keygroup_action.setEnabled(self.keygroup_list.currentRow() >= 0)

        # unlike DELP, no "last one is silently ignored" restriction is
        # documented for DELS (see sample_deleted's own comment in
        # program_editor_bridge.py) - no count() > 1 guard needed here
        has_sample = self.sample_list_widget.currentRow() >= 0
        self._rename_sample_action.setEnabled(has_sample)
        self._delete_sample_action.setEnabled(has_sample)

    def _prompt_akai_name(self, title, label, current_name):
        # shared by _prompt_program_name and _prompt_sample_name - PRNAME
        # and SHNAME are both 12-char AKAI_CHARSET text fields, so the
        # dialog/validation shape is identical; only the title/label text
        # and what happens with the result differ per caller
        dialog = QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setTextValue(current_name)
        # same character-set/length constraints as the controls page's own
        # name field (_build_akai_name_edit) - QInputDialog builds its own
        # QLineEdit internally rather than taking one, so reach into it and
        # apply the same constraints instead of duplicating them
        line_edit = dialog.findChild(QLineEdit)
        line_edit.setMaxLength(NAME_LENGTH)
        name_pattern = QRegularExpression(_NAME_INPUT_PATTERN)
        name_pattern.setPatternOptions(
            QRegularExpression.PatternOption.CaseInsensitiveOption
        )
        line_edit.setValidator(QRegularExpressionValidator(name_pattern, line_edit))
        line_edit.selectAll()
        if not dialog.exec():
            return None
        # uppercase-on-commit, not live-as-you-type, since this dialog's
        # QLineEdit isn't wired to _on_program_name_typed's per-keystroke
        # handler - what's actually stored ends up identical either way,
        # since encode_name would uppercase it on write regardless (see
        # _on_program_name_typed's own comment)
        return dialog.textValue().rstrip().upper()

    def _prompt_program_name(self, current_name):
        return self._prompt_akai_name("Rename Program", "Program name:", current_name)

    def _prompt_sample_name(self, current_name):
        return self._prompt_akai_name("Rename Sample", "Sample name:", current_name)

    def _confirm_rename_program(self):
        item = self.program_list.currentItem()
        if item is None:
            return
        program_index = self.program_list.currentRow()
        current_name = item.text()
        new_name = self._prompt_program_name(current_name)
        if new_name is None or new_name == current_name:
            return
        item.setText(new_name)
        self._update_multi_program_combo_names(program_index, new_name)
        # keeps the controls page's own name field in step, same reasoning
        # as _commit_program_name - just triggered from the list here
        # instead of that widget, so nothing else updates it for us
        self.program_name_edit.blockSignals(True)
        self.program_name_edit.setText(new_name)
        self.program_name_edit.blockSignals(False)
        self._write_knob_value(
            "PRNAME", "program", new_name, keygroup_index=0, index=program_index
        )

    def _confirm_delete_program(self):
        item = self.program_list.currentItem()
        if item is None:
            return
        program_index = self.program_list.currentRow()
        program_name = item.text()
        answer = QMessageBox.question(
            self,
            "Delete Program",
            f'Delete program "{program_name}" and all of its keygroups?\n\n'
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.status_bar.showMessage(f'Deleting program "{program_name}"…')
        self._worker.submit_delete_program(program_index)

    def _confirm_delete_keygroup(self):
        keygroup_index = self.keygroup_list.currentRow()
        if keygroup_index < 0:
            return
        program_index = self.program_list.currentRow()
        lo, hi = self._keygroup_ranges[keygroup_index]
        range_text = f"{midi_note_to_name(lo)} - {midi_note_to_name(hi)}"
        answer = QMessageBox.question(
            self,
            "Delete Keygroup",
            f"Delete keygroup {keygroup_index + 1} ({range_text})?\n\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.status_bar.showMessage(f"Deleting keygroup {keygroup_index + 1}…")
        self._worker.submit_delete_keygroup(program_index, keygroup_index)

    def _on_program_deleted(self, program_index):
        self.status_bar.showMessage("Program deleted")
        # full reload, not a targeted removal - a DELP that lands on the
        # last remaining program is silently ignored by the hardware (see
        # _update_list_context_actions_enabled) and every other case renumbers/
        # reorders everything after it, the same as a manual Refresh
        self._worker.submit_program_list()

    def _on_keygroup_deleted(self, program_index, keygroup_index):
        if program_index != self.program_list.currentRow():
            return
        self.status_bar.showMessage("Keygroup deleted")
        # same restore-then-reload path _refresh_from_hardware uses - keeps
        # the view on the same row index (now showing whatever the delete
        # renumbered into that slot) instead of resetting to keygroup 0;
        # see the clamp in _on_keygroups_loaded for the case where the
        # deleted keygroup was the last one in the list
        self._pending_restore_state = {
            "keygroup_index": keygroup_index,
            "stack_index": self.detail_stack.currentIndex(),
            "zone_index": self._zone_button_group.checkedId(),
        }
        self._worker.submit_keygroups(program_index)

    def _confirm_rename_sample(self):
        item = self.sample_list_widget.currentItem()
        if item is None:
            return
        sample_index = self.sample_list_widget.currentRow()
        current_name = item.text()
        new_name = self._prompt_sample_name(current_name)
        if new_name is None or new_name == current_name:
            return
        item.setText(new_name)
        self._update_zone_sample_combo_names(sample_index, new_name)
        self._write_knob_value(
            "SHNAME", "sample", new_name, keygroup_index=0, index=sample_index
        )

    def _update_zone_sample_combo_names(self, sample_index, name):
        # same reasoning/convention as _update_multi_program_combo_names -
        # a zone's sample-choice combo (SNAME1..4) shows the assigned
        # sample's NAME as its current text, not just an index, and a
        # rename left these showing the old name until the next full
        # Refresh. combo index is always sample_index + 1 (index 0 is the
        # blank "-" placeholder - see _on_samples_loaded), same offset as
        # the multi-part program combos use for the same reason.
        if sample_index < 0:
            return
        for combo in self._zone_combos:
            combo.setItemText(sample_index + 1, name)

    def _confirm_delete_sample(self):
        item = self.sample_list_widget.currentItem()
        if item is None:
            return
        sample_index = self.sample_list_widget.currentRow()
        sample_name = item.text()
        answer = QMessageBox.question(
            self,
            "Delete Sample",
            f'Delete sample "{sample_name}"?\n\nThis cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.status_bar.showMessage(f'Deleting sample "{sample_name}"…')
        self._worker.submit_delete_sample(sample_index)

    def _on_sample_deleted(self, sample_index):
        self.status_bar.showMessage("Sample deleted")
        # full reload, not a targeted removal - every sample index after
        # the deleted one shifts down by one, same reasoning as
        # _on_program_deleted (no PRGNUM-style renumbering concern here,
        # but the index shift itself is the same problem a targeted
        # removal would get wrong)
        self._worker.submit_sample_list()

    def _on_keygroup_selected(self, current, previous):
        if current is None:
            return
        self._zone_stack.setCurrentIndex(0)
        self._zone_button_group.button(0).setChecked(True)
        self.detail_stack.setCurrentIndex(1)
        program_index = self.program_list.currentRow()
        keygroup_index = self.keygroup_list.currentRow()
        self._worker.submit_detail(program_index, keygroup_index)

    def _on_detail_loaded(self, program_index, keygroup_index, values):
        if (
            program_index != self.program_list.currentRow()
            or keygroup_index != self.keygroup_list.currentRow()
        ):
            return  # stale result from selection the user has already moved past
        self.note_lo_spinbox.blockSignals(True)
        self.note_lo_spinbox.setValue(values["LONOTE"])
        self.note_lo_spinbox.blockSignals(False)
        self.note_hi_spinbox.blockSignals(True)
        self.note_hi_spinbox.setValue(values["HINOTE"])
        self.note_hi_spinbox.blockSignals(False)
        # blockSignals during every loaded-from-hardware setValue() call -
        # valueChanged now also schedules a debounced write (see
        # _wire_knob_write), so leaving these unblocked would schedule a
        # write that echoes the just-loaded value straight back to the
        # hardware. Value labels and the envelope graphs are updated
        # explicitly right here instead of relying on the (blocked)
        # valueChanged connections that normally do it.
        self.cutoff_knob.blockSignals(True)
        self.cutoff_knob.setValue(values["FILFRQ"])
        self.cutoff_knob.blockSignals(False)
        self.cutoff_value_label.setText(str(values["FILFRQ"]))
        self.resonance_knob.blockSignals(True)
        self.resonance_knob.setValue(values["FILQ"])
        self.resonance_knob.blockSignals(False)
        self.resonance_value_label.setText(str(values["FILQ"]))
        self.key_filter_track_knob.blockSignals(True)
        self.key_filter_track_knob.setValue(values["K_FREQ"])
        self.key_filter_track_knob.blockSignals(False)
        self.key_filter_track_value_label.setText(str(values["K_FREQ"]))

        # Modulation matrix (Keygroup tab half) - amount-only spinboxes for
        # the per-keygroup half of the matrix; see the Program tab's own
        # Modulation card comment for which destinations split this way
        # and why. Loop for the same reason as _on_keygroups_loaded's own
        # modulation-loading loop above.
        keygroup_mod_knobs = [
            (
                self.mod_filt1_amount_knob,
                self.mod_filt1_amount_value_label,
                "MODVFILT1",
            ),
            (
                self.mod_filt2_amount_knob,
                self.mod_filt2_amount_value_label,
                "MODVFILT2",
            ),
            (
                self.mod_filt3_amount_knob,
                self.mod_filt3_amount_value_label,
                "MODVFILT3",
            ),
            (
                self.mod_pitch_amount_knob,
                self.mod_pitch_amount_value_label,
                "MODVPITCH",
            ),
            (self.mod_amp3_amount_knob, self.mod_amp3_amount_value_label, "MODVAMP3"),
            (self.lfo1_to_pitch_knob, self.lfo1_to_pitch_value_label, "L_PTCH"),
        ]
        for knob, value_label, field in keygroup_mod_knobs:
            knob.blockSignals(True)
            knob.setValue(values[field])
            knob.blockSignals(False)
            value_label.setText(str(values[field]))

        self.attack1_knob.blockSignals(True)
        self.attack1_knob.setValue(values["ATTAK1"])
        self.attack1_knob.blockSignals(False)
        self.attack1_value_label.setText(str(values["ATTAK1"]))
        self.decay1_knob.blockSignals(True)
        self.decay1_knob.setValue(values["DECAY1"])
        self.decay1_knob.blockSignals(False)
        self.decay1_value_label.setText(str(values["DECAY1"]))
        self.sustain1_knob.blockSignals(True)
        self.sustain1_knob.setValue(values["SUSTN1"])
        self.sustain1_knob.blockSignals(False)
        self.sustain1_value_label.setText(str(values["SUSTN1"]))
        self.release1_knob.blockSignals(True)
        self.release1_knob.setValue(values["RELSE1"])
        self.release1_knob.blockSignals(False)
        self.release1_value_label.setText(str(values["RELSE1"]))
        self.env1_graph.set_values(
            values["ATTAK1"], values["DECAY1"], values["SUSTN1"], values["RELSE1"]
        )

        env2_field_pairs = [
            ("ATTAK2", "ENV2L1"),
            ("ENV2R2", "ENV2L2"),
            ("DECAY2", "SUSTN2"),
            ("RELSE2", "ENV2L4"),
        ]
        for i, ((rate_field, level_field), rate_knob, level_knob) in enumerate(
            zip(env2_field_pairs, self._env2_rate_knobs, self._env2_level_knobs)
        ):
            rate_knob.blockSignals(True)
            rate_knob.setValue(values[rate_field])
            rate_knob.blockSignals(False)
            self._env2_rate_value_labels[i].setText(str(values[rate_field]))
            level_knob.blockSignals(True)
            level_knob.setValue(values[level_field])
            level_knob.blockSignals(False)
            self._env2_level_value_labels[i].setText(str(values[level_field]))
        self.env2_graph.set_values(
            values["ATTAK2"],
            values["ENV2L1"],
            values["ENV2R2"],
            values["ENV2L2"],
            values["DECAY2"],
            values["SUSTN2"],
            values["RELSE2"],
            values["ENV2L4"],
        )
        self._update_zone_panels(values)

    def _on_detail_load_failed(self, program_index, keygroup_index, error_message):
        if (
            program_index == self.program_list.currentRow()
            and keygroup_index == self.keygroup_list.currentRow()
        ):
            self.status_bar.showMessage(f"Couldn't load detail: {error_message}")

    def closeEvent(self, event):
        # runs regardless of how the window closes (close button, command + w,
        # etc) - stop() lets anything already queued (in particular, pending
        # writes) drain before the worker thread actually exits, then wait()
        # blocks until it does, so its QThread object is never destroyed
        # while still running
        self._worker.stop()
        self._worker.wait()

        # same reasoning as above, but for an in-flight "Check for
        # Updates..." request - dashboard.py replaces its editor_window
        # reference on every open_program_editor() call, so a closed editor
        # window can become unreferenced (and eligible for GC) well before
        # a check that was still running on it finishes
        self._update_runner.wait()

        self._main_window.show()
        event.accept()

    def _show_about_dialog(self):
        dialog = AboutDialog(self)
        dialog.exec()

    def _check_for_updates_manual(self):
        self._update_runner.start(manual=True)

    def _build_knob_column(self, label_text, knob, *, show_label=True):
        # show_label=False skips the name label entirely - used by the
        # Modulation matrix grid below, which shows "Amount" once as a
        # column header instead of repeating it above every single knob
        column = QVBoxLayout()
        column.setSpacing(4)  # fixed gap, in pixels - never stretches

        if show_label:
            name_label = QLabel(label_text)
            name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            name_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            column.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        value_label = QLabel("-")
        value_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        column.addWidget(knob, alignment=Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(value_label)

        knob.valueChanged.connect(lambda v: value_label.setText(str(v)))

        return column, value_label

    def _build_labeled_spinbox_column(self, label_text, spinbox):
        # same "label above, centered" shape as the Tune/MIDI channel/etc
        # columns already on the Program tab, for a plain spinbox rather
        # than a knob or combo
        name_label = QLabel(label_text)
        name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        column = QVBoxLayout()
        column.setSpacing(4)
        column.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(spinbox, alignment=Qt.AlignmentFlag.AlignHCenter)

        return column

    # --- modulation matrix helpers -------------------------------------
    # The assignable modulation matrix (MODS*/MODV* fields - see the
    # Modulation section cards on both the Program and Keygroup tabs) is
    # built from many near-identical (source dropdown, amount knob) pairs,
    # up to 3 per destination row. Each card is a real QGridLayout with
    # fixed columns per slot (2 - Source, Amount - on the Program tab's
    # card, which shows both halves; 1 - Amount only - on the Keygroup
    # tab's, which never shows a source), and a "Slot 1/2/3" + "Source"/
    # "Amount" header at the top instead of repeating those words on every
    # single control - what used to make 3 independent routings legible
    # (see the git history for the old per-control-labeled version) is now
    # the grid's own column alignment plus the slot header, not label text
    # repeated 20+ times down the card. These helpers are that repeated
    # unit, kept small and explicit rather than a single "spec table"
    # builder so each use site below still spells out its own field names,
    # matching how every other section on this page is built
    # (_build_knob_column, etc).

    def _build_mod_source_combo(self):
        combo = QComboBox()
        combo.addItems(_MOD_SOURCE_LABELS)
        # narrower than a source combo elsewhere on this page (150) - the
        # matrix grid now puts this beside its amount knob rather than
        # stacked above it (see this section's own comment), so shaving
        # width here matters more; Qt elides the two longest labels
        # ("Modwheel (inverted)"/"External (inverted)") rather than
        # breaking layout. Re-measured with the rest of this card the same
        # way this file's "section cards, scroll areas" note describes.
        combo.setMaximumWidth(130)
        return combo

    def _build_mod_amount_knob(self):
        # +/-50 like every MODV*/L_PTCH field's declared range, default
        # 0 (no modulation) same as every other "off by default" knob on
        # this page. 28x28, matching the Multis tab's knobs
        # (_build_multi_part_knob) rather than ENV2's 32x32 rate/level
        # knobs - since the matrix grid puts this beside a ~29px-tall
        # source combo rather than stacked below its own label, the
        # smaller size reads better next to the combo and keeps every
        # amount knob on this page (Multis tab included) the same size.
        knob = Knob()
        knob.setRange(-50, 50)
        knob.setDefaultValue(0)
        knob.setFixedSize(28, 28)
        # enabled here rather than in __init__'s later "enable knobs" block
        # (see Knob.__init__ - it starts disabled) - unlike every other
        # knob on this page, these are built AND wired together by one
        # helper call each, so there's nothing later that still needs to
        # happen before enabling one is safe
        knob.setEnabled(True)
        return knob

    def _build_knob_value_row(self, knob):
        # knob + live numeric readout side by side, same compact row shape
        # as the Multis tab's knobs (_build_multi_part_knob) - unlike
        # _build_knob_column's vertical (knob, then value below) layout,
        # this is for grids that already carry their own row/column
        # headers (the Modulation cards' "Slot N"/"Amount" header - see
        # _build_mod_matrix_header/_build_mod_matrix_amount_header - and
        # ENV2's "Stage N"/"Rate"/"Level" grid below), so there's no
        # per-knob name label to stack above the value here, just the knob
        # and its readout beside each other. Fixed width (matching
        # _build_multi_part_knob's own value_label) so the label's own
        # width doesn't change as its text does ("0" vs "-50") - unfixed,
        # a value crossing a digit-count boundary reflowed the row's total
        # width, which visibly nudged the knob sideways since these rows
        # sit centered in their grid cell (see _build_mod_matrix_row/
        # _build_mod_matrix_amount_row's AlignHCenter).
        value_label = QLabel("-")
        value_label.setFixedWidth(28)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(knob)
        row.addWidget(value_label)
        knob.valueChanged.connect(lambda v: value_label.setText(str(v)))
        return row, value_label

    def _build_mod_slot(
        self,
        source_param,
        source_region,
        amount_param,
        amount_region,
        *,
        keygroup_index_getter=None,
    ):
        # a full assignable slot: a source dropdown next to its amount
        # knob (no per-widget labels - the grid's own header row covers
        # that, see this section's own comment above). source_region/
        # amount_region are separate (not one shared region) because a
        # handful of destinations split across the wire: e.g. filter
        # frequency's SOURCE is chosen program-wide (MODSFILT*) but its
        # AMOUNT is stored per-keygroup (MODVFILT*) - see the Modulation
        # card comments below for the full list.
        combo = self._build_mod_source_combo()
        knob = self._build_mod_amount_knob()
        amount_column, value_label = self._build_knob_value_row(knob)

        self._wire_combo_write(
            combo,
            source_param,
            source_region,
            keygroup_index_getter=keygroup_index_getter,
        )
        self._wire_knob_write(
            knob,
            amount_param,
            amount_region,
            keygroup_index_getter=keygroup_index_getter,
        )
        return combo, amount_column, knob, value_label

    def _build_labeled_combo_column(self, label_text, combo, *, center=True):
        # same "label above, centered" shape as _build_labeled_spinbox_column,
        # for a combo instead of a spinbox - still used outside the
        # Modulation matrix (e.g. lfo1_sync_combo/lfo2_trig_combo), which
        # aren't part of a grid with its own header row. center=False
        # instead left-aligns the label flush with the combo's own
        # (left-aligned) displayed text - for the Zone card's Sample combo
        # (see _build_zone_page), which stretches to fill whatever width
        # its row gives it: centering that label put it in the middle of
        # the whole stretched width, nowhere near "BASS C1" etc.
        name_label = QLabel(label_text)
        column = QVBoxLayout()
        column.setSpacing(4)
        if center:
            name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            column.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        else:
            column.addWidget(name_label)
        column.addWidget(combo)
        return column

    def _build_labeled_knob_value_column(self, label_text, knob, *, center=True):
        # label above, knob+value beside each other below it - the Zone
        # card's Loud/Pan knobs' own shape (see _build_zone_page below),
        # sharing a row with the Sample combo (_build_labeled_combo_column)
        # rather than the knob row those two used to have further down the
        # page. Neither existing knob helper fit: _build_knob_column stacks
        # the value below the knob (fine stand-alone, but taller than this
        # row has room for next to a combo), and _build_knob_value_row has
        # no label at all (built for grids with their own header row,
        # which this single pair of knobs isn't part of). center=False
        # left-aligns the label instead, flush with the knob rather than
        # floating over its middle - same reasoning, and same "Sample"/
        # "Loud"/"Pan" row, as _build_labeled_combo_column's own center
        # param.
        name_label = QLabel(label_text)
        column = QVBoxLayout()
        column.setSpacing(4)
        if center:
            name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            column.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        else:
            column.addWidget(name_label)
        knob_row, value_label = self._build_knob_value_row(knob)
        column.addLayout(knob_row)
        return column, value_label

    def _build_mod_source_only_column(self, source_param, source_region):
        # used where the matching amount field is per-keygroup (so it's
        # shown on the Keygroup tab's own Modulation card instead) - just
        # the source dropdown, no amount knob here
        combo = self._build_mod_source_combo()
        self._wire_combo_write(combo, source_param, source_region)
        return combo

    def _build_mod_amount_only_column(
        self, amount_param, amount_region, *, keygroup_index_getter=None
    ):
        # the other half of _build_mod_source_only_column - an amount knob
        # with no source dropdown, for the Keygroup tab's own Modulation
        # card (the matching source lives on the Program tab)
        knob = self._build_mod_amount_knob()
        column, value_label = self._build_knob_value_row(knob)
        self._wire_knob_write(
            knob,
            amount_param,
            amount_region,
            keygroup_index_getter=keygroup_index_getter,
        )
        return column, knob, value_label

    def _build_mod_matrix_header(self, grid, slot_count):
        # 2-row header for a Source+Amount card (Program tab): "Slot N"
        # spans both of that slot's sub-columns, then "Source"/"Amount"
        # sub-labels underneath - column 0 (the destination label) is left
        # blank in both header rows. Returns how many grid rows it used,
        # so the caller knows where its own data rows start.
        for slot_index in range(slot_count):
            start_col = 1 + slot_index * 2
            slot_label = QLabel(f"Slot {slot_index + 1}")
            slot_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            grid.addWidget(
                slot_label, 0, start_col, 1, 2, Qt.AlignmentFlag.AlignHCenter
            )
            source_header = QLabel("Source")
            source_header.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            grid.addWidget(source_header, 1, start_col, Qt.AlignmentFlag.AlignHCenter)
            amount_header = QLabel("Amount")
            amount_header.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            grid.addWidget(
                amount_header, 1, start_col + 1, Qt.AlignmentFlag.AlignHCenter
            )
        return 2

    def _build_mod_matrix_row(self, grid, row, label_text, *slots):
        # one destination row of the Program tab's Modulation grid: a
        # left-hand label plus up to 3 (source_widget, amount_layout)
        # pairs, each placed under its own "Slot N" header column - either
        # half of a pair may be None (Filter Frequency/Pitch show only a
        # source here, their amount lives on the Keygroup tab instead; see
        # the Modulation card comments below for why). Slot count varies
        # per row (LFO1 Rate/Depth/Delay and Pitch only ever have 1;
        # Pan/Loudness/Filter Frequency have up to 3) - this just places
        # whatever it's given, leaving the rest of that row's cells empty.
        # amount_layout is centered (AlignHCenter, not just AlignVCenter)
        # so every amount knob lines up under the centered "Amount" header
        # instead of hugging the cell's left edge.
        label = QLabel(label_text)
        label.setFixedWidth(110)
        grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignVCenter)
        for slot_index, (source_widget, amount_layout) in enumerate(slots):
            start_col = 1 + slot_index * 2
            if source_widget is not None:
                grid.addWidget(
                    source_widget, row, start_col, Qt.AlignmentFlag.AlignVCenter
                )
            if amount_layout is not None:
                grid.addLayout(
                    amount_layout,
                    row,
                    start_col + 1,
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter,
                )

    def _build_mod_matrix_amount_header(self, grid, slot_count, *, label_prefix="Slot"):
        # 1-row header for an Amount-only card (Keygroup tab - it never
        # shows a source, see the Modulation card comments below): just
        # "Slot N" per column, one column per slot rather than two. Returns
        # how many grid rows it used, same contract as the header above.
        # label_prefix is also reused by ENV2's Rate/Level grid below
        # ("Stage N" instead of "Slot N") - same shape (a centered header
        # naming each column, so the data rows don't have to), different
        # word for what a column actually is on that card.
        for slot_index in range(slot_count):
            slot_label = QLabel(f"{label_prefix} {slot_index + 1}")
            slot_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            grid.addWidget(slot_label, 0, 1 + slot_index, Qt.AlignmentFlag.AlignHCenter)
        return 1

    def _build_mod_matrix_amount_row(
        self, grid, row, label_text, *amount_layouts, label_width=110
    ):
        # the Keygroup tab's equivalent of _build_mod_matrix_row - one
        # amount_layout (or None to leave that slot's cell blank) per
        # column, under _build_mod_matrix_amount_header's "Slot N" columns.
        # Centered (AlignHCenter, not just AlignVCenter) so every amount
        # knob lines up under its centered "Slot N" label instead of
        # hugging the cell's left edge. label_width defaults to fitting the
        # Modulation cards' longest destination name ("Filter Frequency")
        # - ENV2's Rate/Level grid below passes a much narrower one, since
        # "Rate"/"Level" are short and the whole point of that grid is to
        # not waste width on a label column sized for something else.
        label = QLabel(label_text)
        label.setFixedWidth(label_width)
        grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignVCenter)
        for slot_index, amount_layout in enumerate(amount_layouts):
            if amount_layout is not None:
                grid.addLayout(
                    amount_layout,
                    row,
                    1 + slot_index,
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter,
                )

    def _build_akai_name_edit(self):
        # shared construction for any field stored in the device's own
        # character set (s3k.messages.AKAI_CHARSET/NAME_LENGTH) - program
        # names and the multi's own name both use this. Case-insensitive so
        # lowercase isn't blocked outright; the caller's own textEdited
        # handler is what actually forces it uppercase as you type (see
        # _on_program_name_typed/_on_multi_name_typed) - this only stops
        # characters outside the device's 41-entry table from being typed
        # at all, and caps length at NAME_LENGTH.
        edit = QLineEdit()
        edit.setMaxLength(NAME_LENGTH)
        edit.setFixedWidth(140)
        name_pattern = QRegularExpression(_NAME_INPUT_PATTERN)
        name_pattern.setPatternOptions(
            QRegularExpression.PatternOption.CaseInsensitiveOption
        )
        edit.setValidator(QRegularExpressionValidator(name_pattern, edit))
        return edit

    def _build_section_card(self, title, *row_layouts):
        # groups related rows (e.g. every LFO control, or Volume/Pan/
        # Velocity together on the Program tab; Filter or Envelopes on the
        # Keygroup tab) into one visually distinct card - same surface/
        # border look as the keygroup zone card (see QWidget#zoneCard in
        # style.qss.template), just under a different objectName since this
        # isn't zone-selector content
        header = QLabel(title)
        header.setObjectName("sectionHeader")

        section_layout = QVBoxLayout()
        section_layout.setContentsMargins(12, 10, 12, 12)
        section_layout.setSpacing(10)
        section_layout.addWidget(header)
        for row in row_layouts:
            section_layout.addLayout(row)
        # without this, a card whose content is shorter than the row it's
        # paired with (Range next to Filter; Envelope 1 next to Envelope 2)
        # gets its leftover height split BEFORE the header too, not just
        # after the content - QBoxLayout distributes surplus space evenly
        # across every gap when nothing claims a stretch, which reads as
        # the whole card being vertically centered rather than top-aligned
        # like its taller neighbor. This claims all of it at the bottom
        # instead.
        section_layout.addStretch()

        card = QWidget()
        card.setObjectName("sectionCard")
        card.setLayout(section_layout)
        return card

    def _build_scroll_area(self, page):
        # both detail_stack pages (Program, Keygroup) are wrapped in one of
        # these rather than added directly - lets the window's minimum
        # height stay comfortable without needing to grow every time a
        # section card is added, at the cost of a scrollbar on a short
        # window instead of everything always fitting unscrolled
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setWidget(page)
        return scroll_area

    def _build_multi_part_knob(self, minimum, maximum, *, default):
        # compact knob + numeric readout for a Multis-tab row - unlike
        # _build_knob_column's vertical (label above, knob, value below)
        # layout meant for a standalone page, this stays a single row so 16
        # of these can sit one per part without blowing out row height
        knob = Knob()
        knob.setRange(minimum, maximum)
        knob.setDefaultValue(default)
        knob.setFixedSize(28, 28)
        knob.setEnabled(True)

        value_label = QLabel(str(default))
        value_label.setFixedWidth(28)

        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(knob)
        row.addWidget(value_label)

        return knob, value_label, widget

    def _build_samples_tab(self):
        # same row-widget-free QListWidget + labeled column shape as the
        # Programs tab's own program_list/keygroup_list (see __init__) -
        # deliberately not sharing that code since this list's rows are
        # plain sample names with no per-row widget, unlike the keygroup
        # list's colored-swatch rows
        self.sample_list_widget = QListWidget()
        self.sample_list_widget.setObjectName("sampleList")
        self.sample_list_widget.setFixedWidth(200)

        # rename/delete, same ActionsContextMenu + QAction shape as
        # program_list's own in __init__ (see its construction comment for
        # why: DELS has no device-side confirmation, so
        # _confirm_delete_sample's QMessageBox IS the required arm-then-
        # fire step, and WidgetShortcut scoping is what lets this list's
        # own Delete/Backspace not collide with program_list's/keygroup_
        # list's identical shortcuts). Built here rather than in __init__
        # since sample_list_widget itself doesn't exist until this method
        # runs.
        self.sample_list_widget.setContextMenuPolicy(
            Qt.ContextMenuPolicy.ActionsContextMenu
        )
        self._rename_sample_action = QAction(
            "Rename Sample...", self.sample_list_widget
        )
        self._rename_sample_action.triggered.connect(self._confirm_rename_sample)
        self._rename_sample_action.setEnabled(False)
        self.sample_list_widget.addAction(self._rename_sample_action)

        _sample_list_separator = QAction(self.sample_list_widget)
        _sample_list_separator.setSeparator(True)
        self.sample_list_widget.addAction(_sample_list_separator)

        self._delete_sample_action = QAction(
            "Delete Sample...", self.sample_list_widget
        )
        self._delete_sample_action.setShortcuts(_DELETE_SHORTCUTS)
        self._delete_sample_action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self._delete_sample_action.triggered.connect(self._confirm_delete_sample)
        self._delete_sample_action.setEnabled(False)
        self.sample_list_widget.addAction(self._delete_sample_action)

        samples_column = QVBoxLayout()
        samples_column.setContentsMargins(0, 0, 0, 0)
        samples_column.setSpacing(6)
        samples_column.addWidget(QLabel("<b>Samples</b>"))
        samples_column.addWidget(self.sample_list_widget)
        samples_container = QWidget()
        samples_container.setLayout(samples_column)

        self.waveform_view = WaveformView()

        # only shown while a sample's audio is actually being received -
        # see _on_sample_receive_progress/_load_sample_waveform. The
        # waveform view's own placeholder text already says loading will
        # freeze the interface; this is what proves it's actually making
        # progress rather than just hung, since receive_progress is real
        # data from the transfer (word count in vs. total), not a fake
        # animation.
        self.sample_load_progress = QProgressBar()
        self.sample_load_progress.setVisible(False)

        # zoom controls + horizontal pan scrollbar - WaveformView also
        # answers to Ctrl+wheel (zoom, centered on the cursor - this is
        # what a macOS trackpad pinch gesture arrives as too) and plain
        # wheel (pan), but neither is discoverable without a hint, so
        # these are the "I didn't know that was possible" path to the same
        # thing. See _on_waveform_view_changed/_on_waveform_scrollbar_moved
        # for how the scrollbar and WaveformView's own pan stay in sync.
        # plain ASCII hyphen, not the Unicode minus sign (U+2212) - the
        # latter rendered as a stray dot/glyph instead of a clean "-" with
        # this button's font
        # style.qss.template's QPushButton rule pads 12px each side (24px
        # total) plus a 1px border - 28px left only ~2px for the glyph
        # itself, which is why "+"/"-" rendered as barely-visible
        # fragments rather than a font/glyph problem
        zoom_out_button = QPushButton("-")
        zoom_out_button.setFixedWidth(36)
        zoom_out_button.setToolTip("Zoom out (Ctrl+scroll on the waveform also works)")
        zoom_out_button.clicked.connect(self.waveform_view.zoom_out)
        zoom_in_button = QPushButton("+")
        zoom_in_button.setFixedWidth(36)
        zoom_in_button.setToolTip("Zoom in (Ctrl+scroll on the waveform also works)")
        zoom_in_button.clicked.connect(self.waveform_view.zoom_in)
        zoom_fit_button = QPushButton("Fit")
        zoom_fit_button.setToolTip("Reset zoom to show the whole sample")
        zoom_fit_button.clicked.connect(self.waveform_view.reset_zoom)
        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(6)
        zoom_row.addWidget(QLabel("Zoom"))
        zoom_row.addWidget(zoom_out_button)
        zoom_row.addWidget(zoom_in_button)
        zoom_row.addWidget(zoom_fit_button)
        zoom_row.addStretch()

        self.waveform_scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.waveform_scrollbar.setVisible(False)
        # a plain setVisible(False) on the scrollbar directly collapses it
        # to zero height in the layout, so the spinbox row below jumps up
        # to fill the gap and then jumps back down the moment zooming in
        # makes the bar reappear - a fixed-height container reserves that
        # space permanently regardless of whether the bar inside it is
        # currently shown, so the spinboxes never move
        scrollbar_container = QWidget()
        scrollbar_container.setFixedHeight(self.waveform_scrollbar.sizeHint().height())
        scrollbar_container_layout = QVBoxLayout(scrollbar_container)
        scrollbar_container_layout.setContentsMargins(0, 0, 0, 0)
        scrollbar_container_layout.addWidget(self.waveform_scrollbar)

        # the canvas has no room for per-marker text without labels
        # overlapping once two markers are close together (start/loop_start/
        # loop_end can all sit right on top of each other for a short or
        # non-looping sample) - spinboxes underneath, always legible
        # regardless of marker spacing and zoom, are what actually answer
        # "which marker is which and where is it", AND give exact/keyboard-
        # precise editing (type a value, or arrow-key/spin nudge one frame
        # at a time) as an alternative to a mouse drag - kept in sync with
        # the waveform live in both directions (drag -> spinbox via
        # WaveformView.markers_changed/_update_marker_spinboxes; spinbox ->
        # drag via _on_marker_spinbox_changed calling WaveformView.
        # set_marker), not just after a commit.
        self._marker_spinboxes = {}
        legend_row = QHBoxLayout()
        legend_row.setSpacing(18)
        for name, display in (
            ("start", "Start"),
            ("loop_start", "Loop Start"),
            ("loop_end", "Loop End"),
            ("end", "End"),
        ):
            # same colors as the canvas markers (WaveformView._marker_colors)
            # - start/end share the neutral "boundary" tone, loop start/end
            # share the loop region's teal, since they're one region's two
            # edges rather than two independent things
            palette = theme.current_palette()
            swatch_color = (
                palette["text_disabled"]
                if name in ("start", "end")
                else palette["keygroup_color_3"]
            )
            swatch = QLabel()
            swatch.setFixedSize(10, 10)
            swatch.setStyleSheet(
                f"background-color: {swatch_color}; border-radius: 2px;"
            )
            spinbox = QSpinBox()
            spinbox.setRange(0, 0)
            spinbox.setEnabled(False)
            spinbox.setFixedWidth(80)
            spinbox.valueChanged.connect(
                lambda v, n=name: self._on_marker_spinbox_changed(n, v)
            )
            spinbox.editingFinished.connect(self._flush_marker_write)
            self._marker_spinboxes[name] = (swatch, spinbox)
            marker_field = QHBoxLayout()
            marker_field.setSpacing(4)
            marker_field.addWidget(swatch)
            marker_field.addWidget(QLabel(display + ":"))
            marker_field.addWidget(spinbox)
            legend_row.addLayout(marker_field)
        legend_row.addStretch()

        # loop type (SPTYPE) and root note (SPITCH) - two more sample-header
        # fields alongside the loop-point markers above, same "known from
        # the header alone, no audio load needed" reasoning as those (both
        # are in _SAMPLE_DETAIL_FIELDS) - own row rather than folded into
        # legend_row since these aren't waveform markers
        sample_meta_row = QHBoxLayout()
        sample_meta_row.setSpacing(18)
        loop_type_label = QLabel("Loop Type")
        loop_type_label.setFixedWidth(70)
        self.sample_loop_type_combo = QComboBox()
        self.sample_loop_type_combo.setFixedWidth(160)
        self.sample_loop_type_combo.setEnabled(False)
        for option_idx, (option_label, option_tooltip) in enumerate(
            _SAMPLE_PLAYBACK_TYPE_OPTIONS
        ):
            self.sample_loop_type_combo.addItem(option_label)
            self.sample_loop_type_combo.setItemData(
                option_idx, option_tooltip, Qt.ItemDataRole.ToolTipRole
            )
        self.sample_loop_type_combo.setToolTip(_SAMPLE_PLAYBACK_TYPE_OPTIONS[0][1])
        self.sample_loop_type_combo.currentIndexChanged.connect(
            self._on_sample_loop_type_changed
        )
        root_note_label = QLabel("Root Note")
        root_note_label.setFixedWidth(70)
        self.sample_root_note_spinbox = NoteSpinBox()
        # SPITCH's own declared range (s3k.params: "21 to 127 represents
        # A1 to G8") is narrower than NoteSpinBox's default 0-127
        self.sample_root_note_spinbox.setRange(21, 127)
        self.sample_root_note_spinbox.setFixedWidth(80)
        self.sample_root_note_spinbox.setEnabled(False)
        self.sample_root_note_spinbox.valueChanged.connect(
            self._on_sample_root_note_changed
        )
        self.sample_root_note_spinbox.editingFinished.connect(
            self._commit_sample_root_note
        )
        sample_meta_row.addWidget(loop_type_label)
        sample_meta_row.addWidget(self.sample_loop_type_combo)
        sample_meta_row.addSpacing(12)
        sample_meta_row.addWidget(root_note_label)
        sample_meta_row.addWidget(self.sample_root_note_spinbox)
        sample_meta_row.addStretch()

        # Trim/Reverse - destructive, hardware-write actions, so both stay
        # disabled until has_waveform() is true (real audio actually in
        # memory to transform, not just header-only markers) - see
        # _set_sample_edit_buttons_enabled. See _confirm_trim_sample/
        # _confirm_reverse_sample for the confirmation dialogs and
        # _perform_sample_edit_real for why this is a real, multi-step
        # send/delete/rename pipeline rather than a simple in-place write.
        sample_edit_row = QHBoxLayout()
        sample_edit_row.setSpacing(8)
        self.trim_sample_button = QPushButton("Trim to Markers")
        self.trim_sample_button.setToolTip(
            "Cut the sample down to the current Start/End markers, "
            "overwriting it on the sampler. Cannot be undone."
        )
        self.trim_sample_button.setEnabled(False)
        self.trim_sample_button.clicked.connect(self._confirm_trim_sample)
        self.reverse_sample_button = QPushButton("Reverse")
        self.reverse_sample_button.setToolTip(
            "Play the sample backwards, overwriting it on the sampler. "
            "Cannot be undone."
        )
        self.reverse_sample_button.setEnabled(False)
        self.reverse_sample_button.clicked.connect(self._confirm_reverse_sample)
        sample_edit_row.addWidget(self.trim_sample_button)
        sample_edit_row.addWidget(self.reverse_sample_button)
        sample_edit_row.addStretch()

        waveform_column = QVBoxLayout()
        waveform_column.setContentsMargins(0, 0, 0, 0)
        waveform_column.setSpacing(6)
        waveform_column.addWidget(QLabel("<b>Loop Points</b>"))
        waveform_column.addLayout(zoom_row)
        waveform_column.addWidget(self.waveform_view)
        waveform_column.addWidget(scrollbar_container)
        waveform_column.addLayout(legend_row)
        waveform_column.addLayout(sample_meta_row)
        waveform_column.addLayout(sample_edit_row)
        waveform_column.addWidget(self.sample_load_progress)
        waveform_column.addStretch()
        waveform_container = QWidget()
        waveform_container.setLayout(waveform_column)
        self._update_marker_spinboxes(None, None, None, None)

        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(14, 14, 14, 14)
        content_layout.addWidget(samples_container)
        content_layout.addWidget(waveform_container, stretch=1)
        page = QWidget()
        page.setLayout(content_layout)
        return page

    def _build_multis_tab(self):
        # the sampler holds exactly one resident multi - no list to choose
        # between, just its fixed 16 parts, each with an independent
        # program assignment, MIDI channel, level and pan
        self._multi_program_combos = []
        self._multi_channel_combos = []
        self._multi_level_knobs = []
        self._multi_pan_knobs = []
        self._multi_level_value_labels = []
        self._multi_pan_value_labels = []

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(6)

        self.multi_name_edit = self._build_akai_name_edit()
        self.multi_name_edit.textEdited.connect(self._on_multi_name_typed)
        # same "commit once, on Enter/focus-loss" reasoning as
        # program_name_edit - see the comment where that one is wired
        self.multi_name_edit.editingFinished.connect(self._commit_multi_name)
        name_row = QHBoxLayout()
        name_row.setSpacing(10)
        name_label = QLabel("<b>Multi Name</b>")
        name_row.addWidget(name_label)
        name_row.addWidget(self.multi_name_edit)
        name_row.addStretch()
        layout.addLayout(name_row)
        layout.addSpacing(6)

        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        part_header = QLabel("<b>Part</b>")
        part_header.setFixedWidth(60)
        program_header = QLabel("<b>Program</b>")
        channel_header = QLabel("<b>Channel</b>")
        channel_header.setFixedWidth(90)
        # matches the STEREO field's panel label ("Lev"), not the s3k.params
        # name - see the notes on _handle_multi_parts in program_editor_bridge.py
        level_header = QLabel("<b>Level</b>")
        level_header.setFixedWidth(60)
        pan_header = QLabel("<b>Pan</b>")
        pan_header.setFixedWidth(60)
        header_row.addWidget(part_header)
        header_row.addWidget(program_header, stretch=1)
        header_row.addWidget(channel_header)
        header_row.addWidget(level_header)
        header_row.addWidget(pan_header)
        layout.addLayout(header_row)

        for part_index in range(MULTI_PART_COUNT):
            row = QHBoxLayout()
            row.setSpacing(10)

            part_label = QLabel(f"Part {part_index + 1}")
            part_label.setFixedWidth(60)

            program_combo = QComboBox()
            # populated once the program list loads (a part can only be
            # assigned a program that actually exists on the sampler);
            # real value comes from _on_multi_parts_loaded once the
            # hardware read completes

            channel_combo = QComboBox()
            # no OMNI option - the hardware has no such setting for a
            # multi part's MIDI channel, only 1-16
            for channel in range(16):
                channel_combo.addItem(str(channel + 1), channel)
            channel_combo.setFixedWidth(90)
            # part N defaults to channel N until the hardware read in
            # _on_multi_parts_loaded overwrites it with the real value
            channel_combo.setCurrentIndex(channel_combo.findData(part_index))

            level_knob, level_value_label, level_widget = self._build_multi_part_knob(
                0, 99, default=99
            )
            pan_knob, pan_value_label, pan_widget = self._build_multi_part_knob(
                -50, 50, default=0
            )

            row.addWidget(part_label)
            row.addWidget(program_combo, stretch=1)
            row.addWidget(channel_combo)
            row.addWidget(level_widget)
            row.addWidget(pan_widget)
            layout.addLayout(row)

            self._multi_program_combos.append(program_combo)
            self._multi_channel_combos.append(channel_combo)
            self._multi_level_knobs.append(level_knob)
            self._multi_pan_knobs.append(pan_knob)
            self._multi_level_value_labels.append(level_value_label)
            self._multi_pan_value_labels.append(pan_value_label)

            program_combo.currentIndexChanged.connect(
                lambda program_index, z=part_index: self._on_multi_part_program_changed(
                    z, program_index
                )
            )
            channel_combo.currentIndexChanged.connect(
                lambda _, z=part_index: self._on_multi_part_channel_changed(z)
            )
            level_knob.valueChanged.connect(
                lambda v, lbl=level_value_label: lbl.setText(str(v))
            )
            level_knob.valueChanged.connect(
                lambda v, z=part_index: self._schedule_write(
                    "STEREO",
                    "multipart",
                    v,
                    index=z,
                    debounce_key=f"multipart_level_{z}",
                )
            )
            level_knob.sliderReleased.connect(
                lambda z=part_index: self._flush_write(f"multipart_level_{z}")
            )
            pan_knob.valueChanged.connect(
                lambda v, lbl=pan_value_label: lbl.setText(str(v))
            )
            pan_knob.valueChanged.connect(
                lambda v, z=part_index: self._schedule_write(
                    "PANPOS",
                    "multipart",
                    v,
                    index=z,
                    debounce_key=f"multipart_pan_{z}",
                )
            )
            pan_knob.sliderReleased.connect(
                lambda z=part_index: self._flush_write(f"multipart_pan_{z}")
            )

        layout.addStretch()
        return page

    def _on_multi_part_channel_changed(self, part_index):
        channel = self._multi_channel_combos[part_index].currentData()
        self._schedule_write(
            "PMCHAN",
            "multipart",
            channel,
            index=part_index,
            debounce_key=f"multipart_channel_{part_index}",
        )

    def _on_multi_part_program_changed(self, part_index, combo_index):
        if combo_index <= 0:
            return  # blank "-" placeholder selected/reset, not a real program
        # combo index 0 is the blank placeholder (see _build_multis_tab),
        # so the actual program_list position is one less than combo_index
        program_index = combo_index - 1
        program_name = self._multi_program_combos[part_index].currentText()
        channel = self._multi_channel_combos[part_index].currentData()

        self._worker.submit_program_change(
            part_index, program_index, program_name, channel
        )

    def _add_keygroup_row(self, index, lo, hi):
        # colored swatch + range text, same row-widget approach as the
        # dashboard's queue/hardware panels - keeps each row's identity tied
        # to its keygroup_range_bar segment (same color, same order) rather
        # than color alone
        item = QListWidgetItem(self.keygroup_list)

        row_widget = QWidget()
        row_widget.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(8, 6, 8, 6)
        row_layout.setSpacing(8)

        swatch = QLabel()
        swatch.setFixedSize(10, 10)
        swatch.setStyleSheet(
            f"background-color: {keygroup_color(index).name()}; border-radius: 2px;"
        )
        row_layout.addWidget(swatch)

        label = QLabel(
            f"Keygroup {index + 1}: {midi_note_to_name(lo)} - {midi_note_to_name(hi)}"
        )
        label.setObjectName("keygroupRangeLabel")
        row_layout.addWidget(label, stretch=1)

        item.setSizeHint(row_widget.sizeHint())
        self.keygroup_list.setItemWidget(item, row_widget)

    def _tune_offset_to_semitones(self, raw_value):
        return round(raw_value / 2.56) / 100

    def _semitones_to_tune_offset(self, semitones):
        return round(semitones * 100 * 2.56)

    # old sampler hardware can't keep up with a write per wheel-notch or
    # per drag-frame - a value is only actually sent once it's held still
    # for this long, unless a "the user is clearly done" signal
    # (sliderReleased/editingFinished) flushes it early
    _WRITE_DEBOUNCE_MS = 500

    def _schedule_write(
        self,
        param_name,
        region,
        value,
        *,
        keygroup_index=0,
        index=None,
        debounce_key=None,
    ):
        # call on every CONTINUOUS change signal (valueChanged,
        # currentIndexChanged) - covers inputs like mouse-wheel scrolling
        # that change the value but never fire editingFinished/
        # sliderReleased, which used to leave the write pending forever.
        #
        # debounce_key defaults to param_name, which is only safe when at
        # most one "thing" using that field name can be mid-edit at once
        # (true for every keygroup-scoped field, since only one keygroup is
        # ever selected at a time). The Multis tab breaks that assumption -
        # all 16 parts share the field name PMCHAN, but are all editable
        # simultaneously - so its callers pass a per-part debounce_key to
        # keep each part's pending write from clobbering another's.
        key = debounce_key if debounce_key is not None else param_name
        if not hasattr(self, "_pending_writes"):
            self._pending_writes = {}
            self._write_timers = {}

        self._pending_writes[key] = (param_name, region, value, keygroup_index, index)

        timer = self._write_timers.get(key)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda k=key: self._flush_write(k))
            self._write_timers[key] = timer
        timer.start(self._WRITE_DEBOUNCE_MS)

    def _flush_write(self, debounce_key):
        # sends a pending debounced value immediately and cancels its timer
        # - wired to sliderReleased/editingFinished so a deliberate
        # drag-then-release or type-then-Enter stays instant rather than
        # also waiting out the debounce window. A no-op if nothing is
        # pending (e.g. a click that didn't actually change the value).
        #
        # NOTE: callers outside this file's own multi-part wiring can keep
        # passing a bare param_name here exactly as before - debounce_key
        # defaults to param_name in _schedule_write, so the two agree.
        timer = getattr(self, "_write_timers", {}).get(debounce_key)
        if timer is not None:
            timer.stop()
        pending = getattr(self, "_pending_writes", {}).pop(debounce_key, None)
        if pending is None:
            return
        param_name, region, value, keygroup_index, index = pending
        self._write_knob_value(
            param_name,
            region,
            value,
            keygroup_index=keygroup_index,
            index=index,
            writer_key=debounce_key,
        )

    def _wire_knob_write(self, knob, param_name, region, *, keygroup_index_getter=None):
        getter = keygroup_index_getter or (lambda: 0)
        knob.valueChanged.connect(
            lambda v: self._schedule_write(
                param_name, region, v, keygroup_index=getter()
            )
        )
        knob.sliderReleased.connect(lambda: self._flush_write(param_name))

    def _wire_spinbox_write(
        self,
        spinbox,
        param_name,
        region,
        *,
        keygroup_index_getter=None,
        value_converter=None,
    ):
        getter = keygroup_index_getter or (lambda: 0)
        converter = value_converter or (lambda v: v)
        spinbox.valueChanged.connect(
            lambda v: self._schedule_write(
                param_name, region, converter(v), keygroup_index=getter()
            )
        )
        spinbox.editingFinished.connect(lambda: self._flush_write(param_name))

    def _wire_combo_write(
        self, combo, param_name, region, *, keygroup_index_getter=None
    ):
        # combo index doubles as the raw byte value for every field this is
        # used on (ZPLAY/CP, same "index is the value" convention as
        # lfo_shape_combo/note_priority_combo) - no itemData lookup needed.
        # No flush wiring: a discrete selection change, unlike a dragged
        # knob or a typed spinbox value, never needs one - it always waits
        # out the ordinary debounce window, same as the program-level combos
        getter = keygroup_index_getter or (lambda: 0)
        combo.currentIndexChanged.connect(
            lambda i: self._schedule_write(
                param_name, region, i, keygroup_index=getter()
            )
        )

    def _write_knob_value(
        self,
        param_name,
        region,
        value,
        *,
        keygroup_index=0,
        index=None,
        writer_key=None,
    ):
        # index overrides the usual "whichever program is selected in the
        # Programs tab" target - needed for multipart writes, where the
        # thing being addressed is a part number (0-15), unrelated to
        # program_list's own selection
        program_index = self.program_list.currentRow() if index is None else index
        # writer_key identifies this write in the worker's write_succeeded/
        # write_failed signals (also param_name by default) for the same
        # reason _schedule_write takes debounce_key - see its comment
        key = writer_key if writer_key is not None else param_name
        self._worker.submit_write(
            key, param_name, region, program_index, value, keygroup_index
        )

    def _on_env1_knob_changed(self):
        # redraws the graph immediately as any ADSR knob is dragged - the
        # actual hardware write is throttled separately, on sliderReleased
        self.env1_graph.set_values(
            self.attack1_knob.value(),
            self.decay1_knob.value(),
            self.sustain1_knob.value(),
            self.release1_knob.value(),
        )

    def _on_env2_knob_changed(self):
        # same live-redraw idea as ENV1, for all 4 rate/level stage pairs
        stage_values = []
        for rate_knob, level_knob in zip(self._env2_rate_knobs, self._env2_level_knobs):
            stage_values.append(rate_knob.value())
            stage_values.append(level_knob.value())
        self.env2_graph.set_values(*stage_values)

    def _on_note_range_changed(self):
        # low can't be raised past high (or high dropped past low) - the
        # bound that got pushed follows, same as the sampler is believed to
        # do on the front panel, so the UI never shows/sends an inverted range
        if self.sender() is self.note_lo_spinbox:
            if self.note_lo_spinbox.value() > self.note_hi_spinbox.value():
                self.note_hi_spinbox.blockSignals(True)
                self.note_hi_spinbox.setValue(self.note_lo_spinbox.value())
                self.note_hi_spinbox.blockSignals(False)
        elif self.sender() is self.note_hi_spinbox:
            if self.note_hi_spinbox.value() < self.note_lo_spinbox.value():
                self.note_lo_spinbox.blockSignals(True)
                self.note_lo_spinbox.setValue(self.note_hi_spinbox.value())
                self.note_lo_spinbox.blockSignals(False)

        # keeps the keygroup list's "Keygroup N: lo - hi" label in step while
        # the user edits, rather than only after the write round-trips back
        item = self.keygroup_list.currentItem()
        if item is None:
            return
        index = self.keygroup_list.currentRow()
        lo = self.note_lo_spinbox.value()
        hi = self.note_hi_spinbox.value()
        row_widget = self.keygroup_list.itemWidget(item)
        row_widget.findChild(QLabel, "keygroupRangeLabel").setText(
            f"Keygroup {index + 1}: {midi_note_to_name(lo)} - {midi_note_to_name(hi)}"
        )
        self._keygroup_ranges[index] = [lo, hi]
        self.keygroup_range_bar.set_ranges(self._keygroup_ranges)

        # both bounds scheduled together (not just the one the user
        # touched) - the pushed spinbox's own valueChanged is blocked
        # during the clamp above, so it never schedules a write of its own;
        # scheduling both here every time either one changes is what keeps
        # a pushed value from being left stale on the sampler
        keygroup_index = index
        self._schedule_write("LONOTE", "keygroup", lo, keygroup_index=keygroup_index)
        self._schedule_write("HINOTE", "keygroup", hi, keygroup_index=keygroup_index)

    def _commit_note_range(self):
        # flushes both bounds immediately instead of waiting out the
        # debounce window - pressing Enter or clicking away is a clear
        # "done editing" signal, same as sliderReleased for knobs
        self._flush_write("LONOTE")
        self._flush_write("HINOTE")

    def _on_program_name_typed(self, text):
        # uppercase-as-you-type: encode_name would uppercase it anyway on
        # write (s3k's AKAI_CHARSET has no lowercase letters at all), so
        # showing it uppercase immediately is what's actually going to be
        # stored, not a display that disagrees with the write it triggers
        cursor = self.program_name_edit.cursorPosition()
        self.program_name_edit.blockSignals(True)
        self.program_name_edit.setText(text.upper())
        self.program_name_edit.setCursorPosition(cursor)
        self.program_name_edit.blockSignals(False)

        # keeps the Programs list in step while the user types, same as
        # _on_note_range_changed does for the keygroup list's own label -
        # PRNAME itself only commits on _commit_program_name below
        item = self.program_list.currentItem()
        if item is not None:
            item.setText(self.program_name_edit.text())
        self._update_multi_program_combo_names(
            self.program_list.currentRow(), self.program_name_edit.text()
        )

    def _update_multi_program_combo_names(self, program_index, name):
        # a part's combo shows the assigned program's NAME as its current
        # text, not just an index - a rename left these showing the old
        # name until the next full Refresh, even though the Programs list
        # (and the hardware) had already moved on. combo index is always
        # program_index + 1 (index 0 is the blank "-" placeholder - see
        # _on_programs_loaded/_build_multis_tab), so this can go straight
        # to the right item rather than searching every combo for the old
        # name, which would misfire if two programs ever shared one
        if program_index < 0:
            return
        for combo in self._multi_program_combos:
            combo.setItemText(program_index + 1, name)

    def _commit_program_name(self):
        # commits once, on Enter/focus-loss - see the comment where this is
        # wired for why this doesn't go through _schedule_write's per-
        # keystroke debounce like every other program field
        program_index = self.program_list.currentRow()
        if program_index < 0:
            return
        # trailing spaces are typable (space is a valid AKAI_CHARSET
        # character) but decode_name strips them on every future read, so
        # stripping here keeps what's shown matching what a reload would
        # show rather than differing only until the next refresh
        name = self.program_name_edit.text().rstrip()
        self.program_name_edit.setText(name)
        item = self.program_list.currentItem()
        if item is not None:
            item.setText(name)
        self._update_multi_program_combo_names(program_index, name)
        self._write_knob_value("PRNAME", "program", name, keygroup_index=0)

    def _on_multi_name_typed(self, text):
        # same live-uppercase reasoning as _on_program_name_typed - no list
        # to keep in step here, since there's only ever one resident multi
        cursor = self.multi_name_edit.cursorPosition()
        self.multi_name_edit.blockSignals(True)
        self.multi_name_edit.setText(text.upper())
        self.multi_name_edit.setCursorPosition(cursor)
        self.multi_name_edit.blockSignals(False)

    def _commit_multi_name(self):
        # commits once, on Enter/focus-loss - same reasoning as
        # _commit_program_name. MULTINAME's item index is unused/reserved
        # (s3k.params: "Selector 0 of RMULTIDATA/MULTIDATA; the item index
        # is unused (reserved) for this section" - there's only ever one
        # resident multi), so index=0 here is a placeholder, not a real
        # target the way program_index is for PRNAME.
        name = self.multi_name_edit.text().rstrip()
        self.multi_name_edit.setText(name)
        self._write_knob_value("MULTINAME", "multi", name, index=0)

    def _on_multi_name_loaded(self, name):
        self.multi_name_edit.blockSignals(True)
        self.multi_name_edit.setText(name)
        self.multi_name_edit.blockSignals(False)

    def _on_samples_loaded(self, samples):
        self._sample_list = samples

        # a sample's INDEX is what addresses its header/audio (see
        # _load_sample_waveform) and that index can mean something
        # completely different after a reload (renamed/deleted/reordered
        # on the hardware) - cached audio keyed by the old index would be
        # silently wrong rather than just stale, so drop it rather than try
        # to carry it forward by name
        self._sample_waveform_cache = {}
        previous_sample = (
            self.sample_list_widget.currentItem().text()
            if self.sample_list_widget.currentItem()
            else None
        )
        self.sample_list_widget.blockSignals(True)
        self.sample_list_widget.clear()
        self.sample_list_widget.addItems(samples)
        self.sample_list_widget.blockSignals(False)
        if previous_sample is not None:
            match = self.sample_list_widget.findItems(
                previous_sample, Qt.MatchFlag.MatchExactly
            )
            self.sample_list_widget.setCurrentItem(match[0] if match else None)
        else:
            self._clear_waveform_view()

        for combo in self._zone_combos:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("-")
            combo.addItems(samples)
            combo.setEnabled(True)
            combo.blockSignals(False)
        # wire write signal now that items exist - doing it here rather than
        # in __init__ avoids currentIndexChanged firing before the list is populated
        sname_fields = ["SNAME1", "SNAME2", "SNAME3", "SNAME4"]
        for zone_idx, (combo, field) in enumerate(zip(self._zone_combos, sname_fields)):
            combo.currentIndexChanged.connect(
                lambda _, f=field, z=zone_idx: self._on_zone_sample_changed(f, z)
            )

        if self.program_list.count() > 0:
            self.program_list.setCurrentRow(
                0
            )  # this is what triggers keygroup loading for the first program

    def _on_main_tab_changed(self, index):
        # select the first sample by default the first time the user
        # switches to the Samples tab, same as the Programs tab already
        # auto-selects its own first row on load (_on_programs_loaded) -
        # only when nothing's selected yet, so this never overrides a
        # choice the user already made by switching away and back
        if (
            index == self._samples_tab_index
            and self.sample_list_widget.currentRow() < 0
            and self.sample_list_widget.count() > 0
        ):
            self.sample_list_widget.setCurrentRow(0)

    def _on_sample_selected(self, current, previous):
        if current is None:
            self._clear_waveform_view()
            return
        sample_index = self.sample_list_widget.currentRow()
        entry = self._sample_waveform_cache.get(sample_index)
        if entry is not None and entry["samples"] is not None:
            # range before set_waveform - see the comment at
            # _load_sample_waveform's own set_marker_spinbox_range call
            self._set_marker_spinbox_range(entry["frame_count"])
            self.waveform_view.set_waveform(
                entry["samples"],
                entry["start"],
                entry["loop_start"],
                entry["loop_end"],
                entry["end"],
            )
            self._update_sample_meta_controls(entry["sptype"], entry["spitch"])
            self._set_sample_edit_buttons_enabled(True)
        elif entry is not None:
            # header known, audio not (yet) - see _on_sample_detail_loaded
            self._set_marker_spinbox_range(entry["frame_count"])
            self.waveform_view.set_header(
                entry["frame_count"],
                entry["start"],
                entry["loop_start"],
                entry["loop_end"],
                entry["end"],
            )
            self._update_sample_meta_controls(entry["sptype"], entry["spitch"])
            self._set_sample_edit_buttons_enabled(False)
        else:
            # nothing known about this sample yet - clear to the
            # placeholder and kick off the (fast, async - not the blocking
            # audio transfer) header fetch automatically, so markers show
            # up and become draggable without the user needing to
            # double-click first. See _on_sample_detail_loaded.
            self._clear_waveform_view()
            self._worker.submit_sample_detail(sample_index)

    def _on_sample_detail_loaded(self, sample_index, values):
        # automatic header fetch triggered by _on_sample_selected (also
        # reachable via _fetch_sample_header_blocking's own transient
        # listener when _load_sample_waveform fetches directly - both are
        # fine to have connected at once, this one just does nothing
        # useful if that path's cache write beats it here since they'd
        # compute the same values)
        frame_count, start, loop_start, loop_end, end = self._markers_from_header(
            sample_index, values
        )
        entry = {
            "samples": None,
            "framerate": None,
            "frame_count": frame_count,
            "start": start,
            "loop_start": loop_start,
            "loop_end": loop_end,
            "end": end,
            "sptype": values["SPTYPE"],
            "spitch": values["SPITCH"],
        }
        self._sample_waveform_cache[sample_index] = entry
        if sample_index == self.sample_list_widget.currentRow():
            self._set_marker_spinbox_range(frame_count)
            self.waveform_view.set_header(frame_count, start, loop_start, loop_end, end)
            self._update_sample_meta_controls(values["SPTYPE"], values["SPITCH"])
            self._set_sample_edit_buttons_enabled(False)

    def _on_sample_detail_load_failed(self, sample_index, error):
        if sample_index != self.sample_list_widget.currentRow():
            return
        self.status_bar.showMessage(f"Couldn't read sample header: {error}")

    def _clear_waveform_view(self):
        self.waveform_view.clear()
        self._update_marker_spinboxes(None, None, None, None)
        self._set_marker_spinbox_range(0)
        self._update_sample_meta_controls(None, None)
        self._set_sample_edit_buttons_enabled(False)

    def _set_sample_edit_buttons_enabled(self, enabled):
        # Trim/Reverse need real audio in memory to transform, not just
        # header-only markers - has_waveform(), same gate
        # mouseDoubleClickEvent uses to decide whether a double-click
        # should even try loading audio again
        self.trim_sample_button.setEnabled(enabled)
        self.reverse_sample_button.setEnabled(enabled)

    def _update_sample_meta_controls(self, sptype, spitch):
        # sptype/spitch None means "nothing known about this sample yet" -
        # mirrors _update_marker_spinboxes' own None convention, disabling
        # both controls rather than showing a stale or zeroed-out value
        enabled = sptype is not None
        self.sample_loop_type_combo.setEnabled(enabled)
        self.sample_root_note_spinbox.setEnabled(enabled)
        if sptype is not None:
            self.sample_loop_type_combo.blockSignals(True)
            self.sample_loop_type_combo.setCurrentIndex(sptype)
            self.sample_loop_type_combo.blockSignals(False)
            self.sample_loop_type_combo.setToolTip(
                _SAMPLE_PLAYBACK_TYPE_OPTIONS[sptype][1]
            )
        if spitch is not None:
            self.sample_root_note_spinbox.blockSignals(True)
            self.sample_root_note_spinbox.setValue(spitch)
            self.sample_root_note_spinbox.blockSignals(False)

    def _on_sample_loop_type_changed(self, combo_index):
        sample_index = self.sample_list_widget.currentRow()
        if sample_index < 0:
            return
        entry = self._sample_waveform_cache.get(sample_index)
        if entry is not None:
            entry["sptype"] = combo_index
        self.sample_loop_type_combo.setToolTip(
            _SAMPLE_PLAYBACK_TYPE_OPTIONS[combo_index][1]
        )
        # discrete selection change, same as _wire_combo_write's own ZPLAY/
        # CP writes - no flush wiring needed, it always waits out the
        # ordinary debounce window
        self._schedule_write(
            "SPTYPE", "sample", combo_index, index=sample_index, debounce_key="SPTYPE"
        )

    def _on_sample_root_note_changed(self, value):
        sample_index = self.sample_list_widget.currentRow()
        if sample_index < 0:
            return
        entry = self._sample_waveform_cache.get(sample_index)
        if entry is not None:
            entry["spitch"] = value
        self._schedule_write(
            "SPITCH", "sample", value, index=sample_index, debounce_key="SPITCH"
        )

    def _commit_sample_root_note(self):
        self._flush_write("SPITCH")

    def _set_marker_spinbox_range(self, frame_count):
        # each spinbox can address any frame in the WHOLE sample (typing an
        # exact value shouldn't be limited by whatever's currently
        # scrolled into view) - clamp_marker still enforces start <=
        # loop_start <= loop_end <= end regardless of this range
        enabled = frame_count > 0
        maximum = max(0, frame_count - 1)
        for _name, (_swatch, spinbox) in self._marker_spinboxes.items():
            spinbox.blockSignals(True)
            spinbox.setEnabled(enabled)
            spinbox.setRange(0, maximum)
            spinbox.blockSignals(False)

    def _update_marker_spinboxes(self, start, loop_start, loop_end, end):
        # kept in sync with the waveform view live in both directions -
        # see WaveformView.markers_changed (drag/set_marker -> here) and
        # _on_marker_spinbox_changed (here -> WaveformView.set_marker)
        values = {
            "start": start,
            "loop_start": loop_start,
            "loop_end": loop_end,
            "end": end,
        }
        for name, (_swatch, spinbox) in self._marker_spinboxes.items():
            value = values[name]
            if value is None:
                continue
            spinbox.blockSignals(True)
            spinbox.setValue(value)
            spinbox.blockSignals(False)

    def _on_marker_spinbox_changed(self, name, value):
        # has_header, not has_waveform - editing must work before/without
        # audio ever loading (see WaveformView.set_header)
        if not self.waveform_view.has_header():
            return
        # captured before the edit - set_marker can push OTHER markers out
        # of the way too (see WaveformView.push_marker), not just the one
        # actually typed into, so this is what _schedule_marker_write
        # below needs to tell which field(s) actually need writing
        old_markers = self.waveform_view.markers()
        # set_marker pushes and emits markers_changed synchronously, which
        # is what actually syncs every spinbox's displayed value (including
        # this one, if the typed/stepped value got pushed back, and any
        # others a push moved along too) - see _update_marker_spinboxes
        clamped = self.waveform_view.set_marker(name, value)
        if clamped is None:
            return
        sample_index = self.sample_list_widget.currentRow()
        if sample_index < 0:
            return
        new_markers = self.waveform_view.markers()
        entry = self._sample_waveform_cache.get(sample_index)
        if entry is not None:
            entry.update(new_markers)
        self._schedule_marker_write(sample_index, old_markers, new_markers)

    def _wait_for_any_signal(self, signals, start, timeout_ms=None):
        # Blocks the calling (GUI) thread until the first of *signals*
        # fires, or *timeout_ms* elapses - while still pumping the Qt event
        # loop, so cross-thread signals (BridgeWorker) and MIDI callbacks
        # (SamplerController) still get delivered. This is the ONE
        # mechanism in this window for deliberately blocking instead of
        # returning and letting a result arrive later through a
        # persistently-connected slot the way every other BridgeWorker
        # signal in __init__ does - used by _load_sample_waveform and
        # _perform_sample_edit_real, both of which want a real, visible
        # freeze (see WaveformView's own placeholder copy) rather than
        # something to engineer around. timeout_ms=None waits forever -
        # appropriate for an actual SDS sample dump, which can
        # legitimately take minutes (see README's own transfer-time table)
        # and has no timeout of its own to inherit.
        #
        # *start* is the callable that actually kicks off the operation
        # (a submit_*()/send_*()/receive_*() call) - called only AFTER
        # every listener below is connected, never before. Every signal
        # here fires from a background thread (BridgeWorker) or async
        # MIDI callback (SamplerController), and there is no guarantee
        # the GUI thread reaches this method's own connect() calls before
        # that thread finishes and emits - a submit-then-connect ordering
        # left a real window where a fast-completing operation could
        # finish and emit before anything was listening, silently
        # dropping the result and timing out for no visible reason.
        # Found via a real hang: FakeBridge.delete_sample (a plain dict/
        # list operation with none of real hardware's SysEx round-trip
        # latency) reliably completed and emitted before a
        # submit_delete_sample()-then-_wait_for_any_signal() ordering's
        # own connect() calls ran. Connecting first closes this
        # regardless of how fast the operation underneath ever is - a
        # real S3kBridge/SamplerController call is never this fast today,
        # but nothing here should depend on that staying true.
        #
        # Returns (which_index, emitted_args) for whichever signal fired
        # first, or (None, None) on timeout.
        loop = QEventLoop()
        result = {}

        def _make_capture(index):
            def _capture(*args):
                result["which"] = index
                result["args"] = args
                loop.quit()

            return _capture

        connections = [sig.connect(_make_capture(i)) for i, sig in enumerate(signals)]
        timer = None
        if timeout_ms:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            timer.start(timeout_ms)
        # start() returning exactly False (not None, not omitted) means
        # the operation never actually began (e.g. send_file_queue
        # declining because another transfer is already running) - skip
        # the wait outright rather than sitting until *timeout_ms* (or,
        # with timeout_ms=None, forever) for a signal that will now never
        # come. Every other start() callable here returns None (a plain
        # submit_*() call) and is unaffected.
        if start() is not False:
            loop.exec()
        for sig, connection in zip(signals, connections):
            sig.disconnect(connection)
        if timer is not None:
            timer.stop()
        if "which" not in result:
            return None, None
        return result["which"], result["args"]

    def _fetch_sample_header_blocking(self, sample_index):
        # header only (SSTART/SMPEND/LOOPAT1/LLNGTH1/etc) - fast, goes
        # through the normal BridgeWorker/S3kBridge connection. See
        # _SAMPLE_DETAIL_FIELDS in program_editor_bridge.py for the
        # LOOPAT1-is-the-loop-END correction this relies on.
        which, args = self._wait_for_any_signal(
            [self._worker.sample_detail_loaded, self._worker.sample_detail_load_failed],
            start=lambda: self._worker.submit_sample_detail(sample_index),
            timeout_ms=20000,
        )
        logger = debug_log.get_logger()
        if which == 0:
            returned_index, values = args
            if returned_index == sample_index:
                return values
            logger.debug(
                "_fetch_sample_header_blocking: sample_detail_loaded arrived for "
                f"a stale index ({returned_index}, expected {sample_index}) - "
                "discarding"
            )
            return None
        if which == 1:
            _returned_index, error = args
            logger.debug(
                f"_fetch_sample_header_blocking: sample_detail_load_failed: {error}"
            )
            self.status_bar.showMessage(f"Couldn't read sample header: {error}")
            return None
        logger.debug(
            "_fetch_sample_header_blocking: timed out after 20s waiting for "
            "sample_detail_loaded/sample_detail_load_failed - neither ever fired"
        )
        self.status_bar.showMessage("Timed out reading sample header")
        return None

    def _fetch_sample_audio_blocking(self, sampler_controller, sample_index):
        # the slow part - a real SDS sample dump over the Transfer
        # Dashboard's own MIDI connection (main_window.sampler_controller),
        # completely separate from BridgeWorker's. s3k has no bulk
        # sample-audio transfer at all (see AGENTS.md), so this is the only
        # way to actually get audio out of the sampler. sample_received is
        # always emitted before receive_finished on success (see
        # SamplerController._finish_receiving), so racing the two still
        # resolves on the fast path rather than waiting for
        # receive_finished's extra pacing delay.
        fd, temp_path = tempfile.mkstemp(suffix=".wav", prefix="akaisds_sample_")
        os.close(fd)
        # real word-count-in/total progress from the transfer itself, not a
        # fake animation - shown next to the waveform (see
        # _on_sample_receive_progress) so a slow dump reads as "in
        # progress" instead of "hung", since the rest of the app really is
        # frozen the whole time this runs
        progress_connection = sampler_controller.receive_progress.connect(
            self._on_sample_receive_progress
        )
        # each already-decoded packet's worth of sample words, live -
        # feeds the same progressive-envelope path _fetch_demo_sample_audio
        # drives for its own simulated dump (see
        # WaveformView.append_live_samples), so the waveform actually
        # grows in step with the real transfer instead of only a plain
        # progress bar moving for however many minutes this takes. Purely
        # additive on SamplerController's side (see sample_chunk_received's
        # own comment) - the Transfer Dashboard's own receive path never
        # connects to this and is unaffected.
        chunk_connection = sampler_controller.sample_chunk_received.connect(
            self._on_sample_chunk_received
        )
        try:
            which, args = self._wait_for_any_signal(
                [
                    sampler_controller.sample_received,
                    sampler_controller.receive_finished,
                ],
                start=lambda: sampler_controller.receive_samples(
                    [(sample_index, temp_path)]
                ),
                timeout_ms=None,
            )
            if which == 0:
                try:
                    return self._read_wav_samples(temp_path)
                except (OSError, wave.Error) as e:
                    self.status_bar.showMessage(f"Couldn't read received sample: {e}")
                    return None, None
            self.status_bar.showMessage("Couldn't receive sample audio from hardware")
            return None, None
        finally:
            sampler_controller.receive_progress.disconnect(progress_connection)
            sampler_controller.sample_chunk_received.disconnect(chunk_connection)
            try:
                os.remove(temp_path)
            except OSError:
                pass

    def _on_sample_receive_progress(self, current, total):
        self.sample_load_progress.setRange(0, max(total, 1))
        self.sample_load_progress.setValue(current)
        self.sample_load_progress.setVisible(True)

    def _on_sample_chunk_received(self, chunk):
        # SamplerController.sample_chunk_received - see
        # _fetch_sample_audio_blocking's own comment on why this is
        # connected there. Already 16-bit-scaled by the controller, same
        # range WaveformView.paintEvent divides by everywhere else.
        self.waveform_view.append_live_samples(chunk)

    def _fetch_demo_sample_audio(self, sample_index):
        # AKAISDS_DEMO_SAMPLER has no equivalent on the audio side -
        # SamplerController always wants a real MIDI connection, unlike
        # program_editor_bridge.connect()'s DemoBridge (see AGENTS.md's
        # "Developing without hardware"). This loads real audio from
        # tests/test_audio.wav (same file the test suite's own fixtures
        # use - see _DEMO_TEST_AUDIO_PATH) so the Samples tab's loading/
        # progress/marker-editing UI can all be exercised without hardware
        # against something that actually looks like a waveform. Falls
        # back to a synthesized tone if that file is ever missing/moved
        # again rather than failing outright - demo mode staying broken
        # until someone notices and fixes a fixture path is exactly what
        # happened before this fallback existed.
        try:
            samples, framerate = self._read_wav_samples(_DEMO_TEST_AUDIO_PATH)
        except (OSError, wave.Error):
            samples, framerate = self._synthesize_demo_sample_audio(sample_index)

        # paced with real sleeps to roughly match how long an actual SDS
        # transfer takes (see _DEMO_MS_PER_WORD) rather than a token delay -
        # the whole point of demo mode here is letting someone evaluate
        # this feature's real-world feel (the progress bar, the frozen
        # interface, how long "double-click and wait" really is) without
        # needing hardware in front of them. ~200ms between progress ticks
        # is a reasonable UI update cadence regardless of how long the
        # whole thing takes; never fewer than 6 ticks even for a short
        # sample, so the progress bar still visibly moves rather than
        # jumping straight to done.
        total = len(samples)
        total_seconds = total * _DEMO_MS_PER_WORD / 1000
        steps = max(6, round(total_seconds / 0.2))
        step_seconds = total_seconds / steps
        last_pushed = 0
        for step in range(1, steps + 1):
            current = total * step // steps
            self._on_sample_receive_progress(current, total)
            # same progressive-envelope path the real hardware fetch feeds
            # from SamplerController.sample_chunk_received - demo mode has
            # no packets of its own (the whole file is already in hand
            # from the read above), so this just reveals it prefix by
            # prefix on the same cadence the progress bar already ticks
            # on, so someone evaluating this without hardware sees the
            # real feature, not just its progress bar
            if sample_index == self.sample_list_widget.currentRow():
                self.waveform_view.append_live_samples(samples[last_pushed:current])
            last_pushed = current
            self.status_bar.showMessage(
                f"Loading audio for sample {sample_index} (demo) - "
                f"{current}/{total} frames..."
            )
            QApplication.processEvents()
            time.sleep(step_seconds)

        return samples, framerate

    def _synthesize_demo_sample_audio(self, sample_index):
        # fallback for _fetch_demo_sample_audio when tests/test_audio.wav
        # can't be read - deterministic per sample_index (reloading the
        # same sample gives the same fake tone), not meant to resemble
        # real sampled audio content
        framerate = 44100
        frame_count = _synthesized_demo_frame_count(sample_index)
        rng = random.Random(sample_index)
        freq = 110 * (1.5 ** (sample_index % 5))
        samples = [0] * frame_count
        for i in range(frame_count):
            t = i / framerate
            decay = math.exp(-t * 1.5)
            tone = math.sin(2 * math.pi * freq * t)
            tone += 0.35 * math.sin(2 * math.pi * freq * 2 * t)
            noise = (rng.random() - 0.5) * 0.06
            samples[i] = int(max(-1.0, min(1.0, tone * decay + noise)) * 32000)
        return samples, framerate

    def _demo_sample_frame_count(self, sample_index):
        # the REAL length _fetch_demo_sample_audio will actually load for
        # *sample_index* - a cheap wave-header peek (getnframes() reads
        # just the header, not the audio data), or the fallback's own
        # deterministic formula when the fixture's missing. Used by
        # _markers_from_header's demo branch so header-only markers and
        # begin_live_capture's live-capture total agree with the real
        # eventual length from the moment a sample is selected. Getting
        # this wrong doesn't break anything by itself once the whole load
        # finishes (set_waveform always ends up with the real total
        # regardless) - but a progressive load in between will visibly
        # finish early/late and then snap once the mismatch surfaces. See
        # AGENTS.md's "Progressive waveform loading" section.
        try:
            with wave.open(_DEMO_TEST_AUDIO_PATH, "rb") as wf:
                return wf.getnframes()
        except (OSError, wave.Error):
            return _synthesized_demo_frame_count(sample_index)

    def _demo_loop_points(self, frame_count):
        # see the comment at _load_sample_waveform's call site - spreads
        # the four markers out across the sample instead of DemoBridge's
        # real (all-zero) header values, which collapse every one of them
        # to frame 0
        if frame_count <= 1:
            return 0, 0, 0, 0
        start = 0
        end = frame_count - 1
        loop_start = frame_count // 4
        loop_end = (frame_count * 3) // 4
        return start, loop_start, loop_end, end

    def _markers_from_header(self, sample_index, values):
        # shared by both header-fetch paths - the automatic, async one
        # that fires on plain sample selection (_on_sample_detail_loaded)
        # and _load_sample_waveform's own fallback for the rare case where
        # that hasn't resolved yet by the time the user double-clicks for
        # audio. Returns (frame_count, start, loop_start, loop_end, end).
        demo_mode = bool(os.environ.get("AKAISDS_DEMO_SAMPLER"))
        frame_count = values["SLNGTH"]
        if demo_mode:
            # DemoBridge's own sample headers are all-zero (this project
            # doesn't edit s3k/s3ked - see AGENTS.md), which collapses
            # every marker to frame 0: invisible, and effectively
            # un-draggable too, since clamp_marker's neighbour bounds
            # collapse to [0, 0] right along with it. frame_count is
            # substituted with the REAL length _fetch_demo_sample_audio
            # will actually load for this sample_index (see
            # _demo_sample_frame_count), not an arbitrary nominal
            # placeholder - an earlier version used a fixed 20000 here,
            # which usually didn't match tests/test_audio.wav's real
            # length (~31000 frames) and was harmless right up until
            # progressive loading existed to expose the mismatch mid-load
            # (see AGENTS.md's "Progressive waveform loading" section -
            # the envelope would visibly finish early/late, then the
            # whole thing would snap once the real total replaced it).
            frame_count = self._demo_sample_frame_count(sample_index)
            start, loop_start, loop_end, end = self._demo_loop_points(frame_count)
        else:
            start = values["SSTART"]
            # LOOPAT1 is the loop END, not the start (see
            # _SAMPLE_DETAIL_FIELDS's comment) - loop_start is derived,
            # never read directly
            loop_start = max(0, values["LOOPAT1"] - values["LLNGTH1"])
            loop_end = values["LOOPAT1"]
            end = values["SMPEND"]
        return frame_count, start, loop_start, loop_end, end

    def _read_wav_samples(self, path):
        # sds_encoder.write_wav_file (what the Dashboard's receive path
        # always writes through) only ever produces 8-bit unsigned or
        # 16-bit signed mono WAV - never anything else - so this doesn't
        # need the general-purpose format handling a standalone WAV loader
        # would (see WaveformRenderer/src/core/wav_parser.py, the prototype
        # this was adapted from, for the fuller version). Returns samples
        # normalised to a 16-bit-equivalent signed range, matching what
        # WaveformView's paintEvent divides by.
        with wave.open(path, "rb") as wf:
            framerate = wf.getframerate()
            sampwidth = wf.getsampwidth()
            raw = wf.readframes(wf.getnframes())
        if sampwidth == 1:
            samples = [(b - 128) << 8 for b in raw]
        else:
            samples = list(struct.unpack("<" + "h" * (len(raw) // 2), raw))
        return samples, framerate

    def _load_sample_waveform(self):
        # diagnostic-only logging through this whole method - added
        # specifically because a user hit intermittent "double-click does
        # nothing" failures in real interactive use that no scripted or
        # QTest-simulated repro could reproduce, and the shared debug log
        # (core/debug_log.py) showed the BridgeWorker/DemoBridge layer
        # itself was never at fault (zero FAILED entries, zero overlapping
        # calls across the whole session). That points at something
        # upstream of any bridge call - this is what should catch it next
        # time: every early return below is logged, so the log will show
        # exactly which guard is being hit instead of just "nothing
        # happened". See WaveformView.mouseDoubleClickEvent for the other
        # half (whether the double-click even got here).
        logger = debug_log.get_logger()
        sample_index = self.sample_list_widget.currentRow()
        logger.debug(f"_load_sample_waveform: entered, sample_index={sample_index}")
        if sample_index < 0:
            logger.debug("_load_sample_waveform: no sample selected - bailing out")
            return

        # same env var program_editor_bridge.connect() checks for the s3k
        # side (DemoBridge) - SamplerController has no demo mode of its
        # own, so this is what makes the audio side fake too, entirely
        # within this window (see _fetch_demo_sample_audio)
        demo_mode = bool(os.environ.get("AKAISDS_DEMO_SAMPLER"))
        sampler_controller = getattr(self._main_window, "sampler_controller", None)
        if not demo_mode:
            if sampler_controller is None:
                logger.debug(
                    "_load_sample_waveform: no sampler_controller available - bailing out"
                )
                self.status_bar.showMessage(
                    "Can't load sample audio - no Transfer Dashboard connection available"
                )
                return
            if sampler_controller.is_transfer_busy():
                logger.debug(
                    "_load_sample_waveform: sampler_controller busy with another "
                    "transfer - bailing out"
                )
                self.status_bar.showMessage(
                    "Can't load sample audio - a transfer is already in progress "
                    "on the Transfer Dashboard"
                )
                return

        self.waveform_view.set_loading(True)
        # freezes the rest of the editor for the duration, deliberately -
        # see WaveformView's own placeholder copy. Doesn't reach the menu
        # bar (Cmd+R/Cmd+Delete etc still fire), which is a known gap, not
        # a guarantee - the real backstop against overlapping transfers is
        # the is_transfer_busy() check above and BridgeWorker's own queue,
        # not this disable.
        self.main_tabs.setEnabled(False)
        self.status_bar.showMessage(
            f"Loading audio for sample {sample_index} - this can take a "
            "while and will freeze the interface..."
        )
        # let the "Loading…" placeholder and the disabled tab widget above
        # actually paint before the blocking waits below start - otherwise
        # the freeze begins before the user ever sees why
        QApplication.processEvents()

        try:
            entry = self._sample_waveform_cache.get(sample_index)
            if entry is not None:
                # header (and anything the user already dragged/typed
                # against it in header-only mode - see
                # _on_sample_detail_loaded/set_header) is already known
                # from the automatic fetch that ran when this sample was
                # first selected. Reuse it rather than re-deriving fresh
                # markers from a fresh header read, which would silently
                # discard any edit made before audio ever arrived.
                logger.debug(
                    "_load_sample_waveform: reusing already-known header/markers "
                    f"for sample {sample_index}"
                )
                start = entry["start"]
                loop_start = entry["loop_start"]
                loop_end = entry["loop_end"]
                end = entry["end"]
                sptype = entry["sptype"]
                spitch = entry["spitch"]
            else:
                # rare: the automatic on-selection fetch hasn't resolved
                # yet (the user double-clicked before it landed) - fall
                # back to fetching it directly
                logger.debug(
                    f"_load_sample_waveform: fetching header for sample {sample_index}"
                )
                header = self._fetch_sample_header_blocking(sample_index)
                if header is None:
                    logger.debug(
                        "_load_sample_waveform: header fetch returned None - bailing out"
                    )
                    return
                logger.debug(f"_load_sample_waveform: header fetched: {header!r}")
                frame_count, start, loop_start, loop_end, end = (
                    self._markers_from_header(sample_index, header)
                )
                sptype = header["SPTYPE"]
                spitch = header["SPITCH"]
                if sample_index == self.sample_list_widget.currentRow():
                    # normally already shown by _on_sample_detail_loaded's
                    # automatic fetch - this branch only runs when that
                    # hasn't resolved yet, so this widget has never seen
                    # this sample's header at all otherwise
                    self._set_marker_spinbox_range(frame_count)
                    self.waveform_view.set_header(
                        frame_count, start, loop_start, loop_end, end
                    )
                    self._update_sample_meta_controls(sptype, spitch)

            if sample_index == self.sample_list_widget.currentRow():
                # markers/frame_count are known either way by this point
                # (the cache-hit branch above, or set_header just now) -
                # switch the canvas from header-only to a progressively-
                # filling waveform before the fetch below actually starts,
                # so append_live_samples (called from within both fetch
                # paths as data arrives) has something to grow
                self.waveform_view.begin_live_capture()

            if demo_mode:
                samples, framerate = self._fetch_demo_sample_audio(sample_index)
            else:
                samples, framerate = self._fetch_sample_audio_blocking(
                    sampler_controller, sample_index
                )
            if samples is None:
                logger.debug(
                    "_load_sample_waveform: audio fetch returned None - bailing out"
                )
                return
            logger.debug(
                f"_load_sample_waveform: audio fetched, {len(samples)} frames "
                f"at {framerate}Hz"
            )

            entry = {
                "samples": samples,
                "framerate": framerate,
                "frame_count": len(samples),
                "start": start,
                "loop_start": loop_start,
                "loop_end": loop_end,
                "end": end,
                "sptype": sptype,
                "spitch": spitch,
            }
            self._sample_waveform_cache[sample_index] = entry
            if sample_index == self.sample_list_widget.currentRow():
                # range must be set BEFORE set_waveform - set_waveform's own
                # marker sync (_update_marker_spinboxes, via
                # markers_changed) calls spinbox.setValue(), which silently
                # clamps to whatever range is currently set; a spinbox
                # still at its old (0, 0) range swallows that value instead
                # of displaying it
                self._set_marker_spinbox_range(len(entry["samples"]))
                self.waveform_view.set_waveform(
                    entry["samples"],
                    entry["start"],
                    entry["loop_start"],
                    entry["loop_end"],
                    entry["end"],
                )
                self._set_sample_edit_buttons_enabled(True)
            self.status_bar.showMessage(
                f"Loaded {len(samples)} sample frames for sample {sample_index}"
            )
        finally:
            self.main_tabs.setEnabled(True)
            self.waveform_view.set_loading(False)
            self.sample_load_progress.setVisible(False)

    def _confirm_trim_sample(self):
        sample_index = self.sample_list_widget.currentRow()
        if sample_index < 0 or not self.waveform_view.has_waveform():
            return
        markers = self.waveform_view.markers()
        frame_count = self.waveform_view.frame_count()
        if markers["start"] == 0 and markers["end"] == frame_count - 1:
            self.status_bar.showMessage(
                "Nothing to trim - Start/End already cover the whole sample"
            )
            return
        item = self.sample_list_widget.currentItem()
        sample_name = item.text() if item is not None else ""
        answer = QMessageBox.question(
            self,
            "Trim Sample",
            f'Trim "{sample_name}" down to the current Start/End markers '
            f"({markers['start']}-{markers['end']} of {frame_count} frames)?\n\n"
            "This overwrites the sample's audio on the sampler and cannot "
            "be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._perform_sample_edit(sample_index, sample_editing.trim_samples, "Trim")

    def _confirm_reverse_sample(self):
        sample_index = self.sample_list_widget.currentRow()
        if sample_index < 0 or not self.waveform_view.has_waveform():
            return
        entry = self._sample_waveform_cache.get(sample_index)
        if entry is None or len(entry["samples"]) <= 1:
            self.status_bar.showMessage("Nothing to reverse - sample is too short")
            return
        item = self.sample_list_widget.currentItem()
        sample_name = item.text() if item is not None else ""
        answer = QMessageBox.question(
            self,
            "Reverse Sample",
            f'Reverse "{sample_name}"\'s audio?\n\n'
            "This overwrites the sample's audio on the sampler and cannot "
            "be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._perform_sample_edit(
            sample_index, sample_editing.reverse_samples, "Reverse"
        )

    def _perform_sample_edit(self, sample_index, transform, action_label):
        # shared by _confirm_trim_sample/_confirm_reverse_sample - both are
        # "take the samples already in memory, transform them with a pure
        # function from core/sample_editing.py, then get the result onto
        # the hardware" with nothing else actually different between them
        entry = self._sample_waveform_cache.get(sample_index)
        if entry is None or entry["samples"] is None:
            return
        item = self.sample_list_widget.currentItem()
        original_name = item.text() if item is not None else ""
        markers = self.waveform_view.markers()
        new_samples, new_start, new_loop_start, new_loop_end, new_end = transform(
            entry["samples"],
            markers["start"],
            markers["loop_start"],
            markers["loop_end"],
            markers["end"],
        )
        framerate = entry["framerate"]

        demo_mode = bool(os.environ.get("AKAISDS_DEMO_SAMPLER"))
        sampler_controller = getattr(self._main_window, "sampler_controller", None)
        if not demo_mode:
            if sampler_controller is None:
                self.status_bar.showMessage(
                    f"Can't {action_label.lower()} sample - no Transfer Dashboard "
                    "connection available"
                )
                return
            if sampler_controller.is_transfer_busy():
                self.status_bar.showMessage(
                    f"Can't {action_label.lower()} sample - a transfer is already "
                    "in progress on the Transfer Dashboard"
                )
                return

        self.waveform_view.set_loading(True)
        self.main_tabs.setEnabled(False)
        self.status_bar.showMessage(
            f'{action_label} "{original_name}" - this can take a while and will '
            "freeze the interface..."
        )
        QApplication.processEvents()

        try:
            if demo_mode:
                self._perform_sample_edit_demo(
                    sample_index,
                    entry,
                    new_samples,
                    framerate,
                    new_start,
                    new_loop_start,
                    new_loop_end,
                    new_end,
                    action_label,
                )
            else:
                self._perform_sample_edit_real(
                    sample_index,
                    sampler_controller,
                    original_name,
                    new_samples,
                    framerate,
                    action_label,
                )
        finally:
            self.main_tabs.setEnabled(True)
            self.waveform_view.set_loading(False)
            self.sample_load_progress.setVisible(False)

    def _perform_sample_edit_demo(
        self,
        sample_index,
        entry,
        new_samples,
        framerate,
        new_start,
        new_loop_start,
        new_loop_end,
        new_end,
        action_label,
    ):
        # SamplerController has no demo mode of its own (see AGENTS.md's
        # "Developing without hardware") - this bypasses it entirely,
        # mutating the cache/view directly, same reasoning as
        # _fetch_demo_sample_audio. Paced the same way so evaluating this
        # without hardware still shows the real time cost of a full
        # resend, not an instant no-op - real hardware genuinely does a
        # full SDS send for this (see _perform_sample_edit_real).
        total = len(new_samples)
        total_seconds = total * _DEMO_MS_PER_WORD / 1000
        steps = max(6, round(total_seconds / 0.2))
        step_seconds = total_seconds / steps
        for step in range(1, steps + 1):
            current = total * step // steps
            self._on_sample_receive_progress(current, total)
            self.status_bar.showMessage(
                f"{action_label} sample (demo) - {current}/{total} frames..."
            )
            QApplication.processEvents()
            time.sleep(step_seconds)

        entry["samples"] = new_samples
        entry["frame_count"] = len(new_samples)
        entry["framerate"] = framerate
        entry["start"] = new_start
        entry["loop_start"] = new_loop_start
        entry["loop_end"] = new_loop_end
        entry["end"] = new_end
        if sample_index == self.sample_list_widget.currentRow():
            self._set_marker_spinbox_range(len(new_samples))
            self.waveform_view.set_waveform(
                new_samples, new_start, new_loop_start, new_loop_end, new_end
            )
        self.status_bar.showMessage(f"{action_label} complete (demo)")

    def _perform_sample_edit_real(
        self,
        sample_index,
        sampler_controller,
        original_name,
        new_samples,
        framerate,
        action_label,
    ):
        # The only audio-replace mechanism anywhere in this stack (s3k has
        # none at all - see AGENTS.md) is: send a full SDS dump under some
        # sample number, then separately delete whatever used to be
        # there. This does it in the safer of the two possible orders -
        # send the replacement FIRST, under a temporary name
        # (_sample_edit_temp_name), and only delete the original once
        # that's confirmed resident:
        #
        # - Deleting first and sending second would risk losing the
        #   sample outright if the send then failed for any reason
        #   (a dropped connection, a MIDI hiccup) - a permanent loss for
        #   what was meant to be a routine edit.
        # - Sending the replacement under the ORIGINAL's own name first
        #   (skipping the temp-name step) would risk a worse, quieter
        #   failure: s3k's own docs say keygroup zones resolve a sample
        #   by NAME, live, against whatever resident sample currently
        #   carries it, with no uniqueness enforced on the hardware
        #   (measured, not just theoretical). Two samples briefly sharing
        #   the original's name would make every zone using that name
        #   genuinely ambiguous - which one actually plays is undefined -
        #   for however long that window lasts.
        #
        # So: send under a name nothing else on the sampler should be
        # using, confirm it landed, delete the original, look up where
        # the replacement actually ended up (its index shifts once the
        # original - at a lower index - is gone), then rename it back
        # with a plain SHNAME-only write (doesn't touch audio at all -
        # see akai_sysex.build_rename_sample_request's own comment).
        # This has not been exercised against real hardware in this
        # codebase - it's built entirely from already-tested primitives
        # (send_file_queue is the Transfer Dashboard's own real Send
        # path; submit_delete_sample and the SHNAME rename write were
        # both added and tested last session) but the specific sequence
        # is new. Watch the first real run of this closely.
        logger = debug_log.get_logger()
        temp_name = _sample_edit_temp_name(original_name)

        fd, temp_path = tempfile.mkstemp(suffix=".wav", prefix="akaisds_edit_")
        os.close(fd)
        try:
            sds_encoder.write_wav_file(temp_path, new_samples, framerate, bit_depth=16)

            progress_connection = sampler_controller.transfer_progress.connect(
                self._on_sample_receive_progress
            )

            def _start_send():
                # returning exactly False here (send_file_queue declining
                # to start) makes _wait_for_any_signal skip its own wait
                # instead of hanging until timeout_ms=None's "forever"
                return sampler_controller.send_file_queue(
                    [
                        {
                            "filepath": temp_path,
                            "name": temp_name,
                            "bit_depth": 16,
                            "sample_rate": None,
                            "mono": False,
                        }
                    ]
                )

            try:
                which, args = self._wait_for_any_signal(
                    [sampler_controller.transfer_finished],
                    start=_start_send,
                    timeout_ms=None,
                )
            finally:
                sampler_controller.transfer_progress.disconnect(progress_connection)

            if which is None or args is None:
                logger.debug(
                    "_perform_sample_edit_real: send_file_queue refused to start"
                )
                self.status_bar.showMessage(
                    f"{action_label} failed - couldn't start sending the "
                    "modified sample"
                )
                return

            if not args[0]:
                logger.debug(
                    f"_perform_sample_edit_real: send of {temp_name!r} did not "
                    "complete successfully"
                )
                self.status_bar.showMessage(
                    f"{action_label} failed - couldn't send the modified sample. "
                    "The original sample was not touched."
                )
                return

            # replacement confirmed resident under temp_name - now safe to
            # remove the original
            which, args = self._wait_for_any_signal(
                [self._worker.sample_deleted, self._worker.sample_delete_failed],
                start=lambda: self._worker.submit_delete_sample(sample_index),
                timeout_ms=20000,
            )
            if which != 0:
                error = args[1] if args else "timed out waiting for a response"
                logger.debug(
                    f"_perform_sample_edit_real: delete of sample {sample_index} "
                    f"failed: {error}"
                )
                self.status_bar.showMessage(
                    f'{action_label} sent successfully as "{temp_name}", but '
                    f"deleting the original failed ({error}) - both copies are "
                    "now resident; delete the original manually."
                )
                self._worker.submit_sample_list()
                return

            # reload to find the replacement's actual index - it shifted
            # once the original (at a lower index) was deleted, so this
            # can't be known in advance, only looked up - the same live
            # NAME-based lookup the hardware itself uses to resolve
            # keygroup zones, not a remembered number from the send above
            which, args = self._wait_for_any_signal(
                [self._worker.samples_loaded, self._worker.samples_load_failed],
                start=self._worker.submit_sample_list,
                timeout_ms=20000,
            )
            if which != 0:
                self.status_bar.showMessage(
                    f"{action_label} sent and original deleted, but couldn't "
                    f'refresh the sample list to rename "{temp_name}" back to '
                    f'"{original_name}" - do it manually.'
                )
                return
            samples_after_delete = args[0]
            if temp_name not in samples_after_delete:
                logger.debug(
                    f"_perform_sample_edit_real: {temp_name!r} missing from the "
                    f"reloaded list {samples_after_delete!r}"
                )
                self.status_bar.showMessage(
                    f"{action_label} sent and original deleted, but "
                    f'"{temp_name}" is missing from the reloaded sample list - '
                    "check the sampler directly."
                )
                return
            new_index = samples_after_delete.index(temp_name)

            # SHNAME-only write, fire-and-forget - same as every other
            # rename on this page (_confirm_rename_sample doesn't wait for
            # confirmation either); writes are never coalesced, so this is
            # guaranteed to reach the hardware
            self._write_knob_value(
                "SHNAME", "sample", original_name, keygroup_index=0, index=new_index
            )

            # one more reload so the sample list reflects the rename - and
            # to land on a clean final selection: nothing stayed selected
            # through the two reloads above for _on_samples_loaded's own
            # restore-by-name logic to restore, so this explicitly selects
            # the (correctly renamed again) sample itself
            which, args = self._wait_for_any_signal(
                [self._worker.samples_loaded, self._worker.samples_load_failed],
                start=self._worker.submit_sample_list,
                timeout_ms=20000,
            )
            self.status_bar.showMessage(f'{action_label} complete: "{original_name}"')
            if which == 0:
                final_samples = args[0]
                if original_name in final_samples:
                    self.sample_list_widget.setCurrentRow(
                        final_samples.index(original_name)
                    )
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass

    def _on_waveform_marker_committed(self, which, start, loop_start, loop_end, end):
        # canvas drag release - see _on_marker_spinbox_changed/
        # _flush_marker_write for the spinbox side of the same editing
        # surface, which goes through the exact same two helpers below so
        # the "which field(s) actually need writing" logic lives in
        # exactly one place regardless of which input drove the change.
        # *which* itself (the marker the user actually grabbed) isn't
        # enough to decide that alone any more - see _schedule_marker_
        # write's own comment on why.
        sample_index = self.sample_list_widget.currentRow()
        if sample_index < 0:
            return
        new_markers = {
            "start": start,
            "loop_start": loop_start,
            "loop_end": loop_end,
            "end": end,
        }
        entry = self._sample_waveform_cache.get(sample_index)
        # the cache only ever gets updated on a commit (here, or a spinbox
        # edit) - never on the live, per-mouse-move markers_changed
        # emissions a drag fires throughout - so this is exactly the
        # state as of whenever the drag STARTED, i.e. "old"
        old_markers = dict(entry) if entry is not None else None
        if entry is not None:
            entry.update(new_markers)
        # a drag-release is already a single, deliberate "I'm done" event
        # (the same reasoning sliderReleased gets elsewhere on this page) -
        # schedule immediately followed by an immediate flush is what
        # turns the normal debounce into an instant write without
        # duplicating _schedule_marker_write's field-pairing logic
        self._schedule_marker_write(sample_index, old_markers, new_markers)
        self._flush_marker_write()

    def _schedule_marker_write(self, sample_index, old_markers, new_markers):
        # LOOPAT1 (the loop END) and LLNGTH1 (measured backwards from it)
        # jointly encode the loop region - see _SAMPLE_DETAIL_FIELDS's
        # comment on LOOPAT1 - so moving either loop edge means writing
        # both fields, holding the OTHER edge fixed (loop_start moving
        # keeps loop_end/LOOPAT1 fixed and only changes LLNGTH1; loop_end
        # moving keeps loop_start fixed, so both LOOPAT1 and LLNGTH1
        # change).
        #
        # Which field(s) need writing can no longer be inferred from just
        # "which marker did the user grab" - WaveformView.push_marker
        # means dragging (or typing into) any ONE marker can shove others
        # along too, e.g. dragging "end" left far enough pushes loop_end
        # (and, in turn, loop_start) out of the way. Every marker whose
        # value actually differs from before this edit gets its field(s)
        # written, not just the one named *which* - old_markers is None
        # only when there was never a cached "before" to diff against
        # (shouldn't normally happen, since a marker can't be edited
        # before a header/cache entry exists), in which case every field
        # is written to be safe rather than silently skipping one.
        def _changed(name):
            return old_markers is None or new_markers[name] != old_markers[name]

        if _changed("start"):
            self._schedule_write(
                "SSTART",
                "sample",
                new_markers["start"],
                index=sample_index,
                debounce_key="SSTART",
            )
        if _changed("end"):
            self._schedule_write(
                "SMPEND",
                "sample",
                new_markers["end"],
                index=sample_index,
                debounce_key="SMPEND",
            )
        if _changed("loop_start") or _changed("loop_end"):
            self._schedule_write(
                "LOOPAT1",
                "sample",
                new_markers["loop_end"],
                index=sample_index,
                debounce_key="LOOPAT1",
            )
            self._schedule_write(
                "LLNGTH1",
                "sample",
                new_markers["loop_end"] - new_markers["loop_start"],
                index=sample_index,
                debounce_key="LLNGTH1",
            )

    def _flush_marker_write(self):
        # unconditionally flushes every marker-related debounce key -
        # _flush_write is already a no-op for a key with nothing pending,
        # so this is safe (and simpler than tracking which of the four
        # _schedule_marker_write actually scheduled) whether a push
        # touched one field or all four
        self._flush_write("SSTART")
        self._flush_write("SMPEND")
        self._flush_write("LOOPAT1")
        self._flush_write("LLNGTH1")

    def _on_waveform_view_changed(self, view_start, view_length, frame_count):
        # keeps the pan scrollbar in step with WaveformView's own zoom/pan
        # state, however it changed (wheel-zoom, wheel-pan, the Fit/+/-
        # buttons, or a fresh sample loading) - blockSignals so this never
        # bounces back through _on_waveform_scrollbar_moved
        self.waveform_scrollbar.blockSignals(True)
        if frame_count == 0 or view_length >= frame_count:
            # nothing loaded, or fully zoomed out - there's nothing to
            # scroll to, so the bar takes up space for no reason if it's
            # merely disabled rather than hidden outright
            self.waveform_scrollbar.setVisible(False)
            self.waveform_scrollbar.setRange(0, 0)
        else:
            self.waveform_scrollbar.setVisible(True)
            self.waveform_scrollbar.setPageStep(view_length)
            self.waveform_scrollbar.setRange(0, frame_count - view_length)
            self.waveform_scrollbar.setValue(view_start)
        self.waveform_scrollbar.blockSignals(False)

    def _on_waveform_scrollbar_moved(self, value):
        self.waveform_view.set_view_start(value)

    def _on_zone_sample_changed(self, field, zone_idx):
        text = self._zone_combos[zone_idx].currentText()
        sample_name = "" if text == "-" else text
        self._schedule_write(
            field,
            "keygroup",
            sample_name,
            keygroup_index=self.keygroup_list.currentRow(),
        )

    def _update_zone_panels(self, values):
        zone_field_map = [
            (
                "SNAME1",
                "LOVEL1",
                "HIVEL1",
                "VTUNO1",
                "VLOUD1",
                "VPANO1",
                "ZPLAY1",
                "CP1",
            ),
            (
                "SNAME2",
                "LOVEL2",
                "HIVEL2",
                "VTUNO2",
                "VLOUD2",
                "VPANO2",
                "ZPLAY2",
                "CP2",
            ),
            (
                "SNAME3",
                "LOVEL3",
                "HIVEL3",
                "VTUNO3",
                "VLOUD3",
                "VPANO3",
                "ZPLAY3",
                "CP3",
            ),
            (
                "SNAME4",
                "LOVEL4",
                "HIVEL4",
                "VTUNO4",
                "VLOUD4",
                "VPANO4",
                "ZPLAY4",
                "CP4",
            ),
        ]
        for z, (sname, lovel, hivel, vtuno, vloud, vpano, zplay, cp) in enumerate(
            zone_field_map
        ):
            combo = self._zone_combos[z]
            name = values.get(sname, "").strip()
            idx = combo.findText(name)
            combo.blockSignals(True)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

            for widget, field in [
                (self._zone_vel_lo[z], lovel),
                (self._zone_vel_hi[z], hivel),
            ]:
                widget.blockSignals(True)
                widget.setValue(values.get(field, 0))
                widget.blockSignals(False)

            # previously missing entirely - this zone's Tune spinbox kept
            # whatever value it last showed across keygroup switches instead
            # of reflecting hardware, so editing it could silently push a
            # stale value from a different keygroup back to VTUNO
            tune_widget = self._zone_tune[z]
            tune_widget.blockSignals(True)
            tune_widget.setValue(self._tune_offset_to_semitones(values.get(vtuno, 0)))
            tune_widget.blockSignals(False)

            loud_val = values.get(vloud, 0)
            self._zone_loudness[z].blockSignals(True)
            self._zone_loudness[z].setValue(loud_val)
            self._zone_loudness_labels[z].setText(str(loud_val))
            self._zone_loudness[z].blockSignals(False)

            pan_val = values.get(vpano, 0)
            self._zone_pan[z].blockSignals(True)
            self._zone_pan[z].setValue(pan_val)
            self._zone_pan_labels[z].setText(str(pan_val))
            self._zone_pan[z].blockSignals(False)

            loop_combo = self._zone_looptype[z]
            loop_combo.blockSignals(True)
            loop_combo.setCurrentIndex(values.get(zplay, 0))
            loop_combo.blockSignals(False)
            loop_combo.setToolTip(_LOOP_TYPE_OPTIONS[loop_combo.currentIndex()][1])

            keytrack_combo = self._zone_keytrack[z]
            keytrack_combo.blockSignals(True)
            keytrack_combo.setCurrentIndex(values.get(cp, 0))
            keytrack_combo.blockSignals(False)


if __name__ == "__main__":
    # standalone launch for UI work away from the hardware sampler - always
    # against the demo bridge, never a real port. For the real thing, go
    # through main.py, which wires the dashboard's "Program Editor" button to
    # program_editor_bridge.connect() instead.
    from PySide6.QtWidgets import QApplication
    from s3ked.demo import DemoBridge
    from ui import theme

    class _StandaloneHost:
        # ProgramEditorWindow.closeEvent() calls main_window.show() to bring
        # the dashboard back - there is none here, so close the app instead
        def show(self):
            QApplication.instance().quit()

    app = QApplication(sys.argv)
    theme.apply_to_app(app)
    window = ProgramEditorWindow(_StandaloneHost(), bridge=DemoBridge())
    window.show()
    sys.exit(app.exec())
