import os
import sys

if __name__ == "__main__":
    # running this file directly (not through main.py) puts src/ui on
    # sys.path, not src/ - so the ui./core. imports below would otherwise
    # fail with "No module named 'ui'"
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtGui import Qt, QAction, QRegularExpressionValidator
from PySide6.QtCore import QTimer, QRegularExpression
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QLabel,
    QLineEdit,
    QPushButton,
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
from core.midi_notes import midi_note_to_name
from core.program_editor_bridge import BridgeWorker, MULTI_PART_COUNT

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

# Program names are NOT ASCII (s3k.messages.AKAI_CHARSET's own docstring:
# "names are not ASCII"): a name byte is an index into a 41-entry table -
# digits, space, A-Z, and "#+-." - and encode_name refuses anything outside
# it rather than mangling it. Built from AKAI_CHARSET/NAME_LENGTH directly
# rather than a hand-copied "0-9A-Z #+-." literal here, so this can never
# silently drift from what the dependency actually accepts. The hyphen is
# escaped since it's a range operator inside a [...] class otherwise.
_NAME_INPUT_PATTERN = "[" + AKAI_CHARSET.replace("-", "\\-") + "]{0," + str(NAME_LENGTH) + "}"


class ProgramEditorWindow(QMainWindow):
    def __init__(self, main_window, bridge):
        super().__init__()
        self.setWindowTitle("AKAISDS - Program Editor")
        # the old 800x500 predates the envelope controls - height in
        # particular is now well past what fits there. Both detail_stack
        # pages (Program, Keygroup) are grouped into titled section cards
        # (see _build_section_card) with related cards paired side by side
        # where they fit (e.g. Range+Filter, Volume/Pan/Velocity+LFO) rather
        # than stacked one per row, and each page is wrapped in a
        # QScrollArea (see _build_scroll_area) - so unlike the two prior
        # approaches here (800, then 950x1080, then 950x1070), the window's
        # height no longer needs to fit every card unscrolled: past this
        # minimum it scrolls instead of compressing cards into overlapping
        # each other the way a bare layout would. The width floor is real,
        # though - below ~1020 the paired cards themselves don't fit
        # side by side and force an unwanted HORIZONTAL scrollbar (measured
        # by growing the window until both tabs' QScrollArea stopped
        # reporting a horizontal range).
        self.setMinimumSize(1040, 900)
        self._sample_list = []
        self._keygroup_ranges = []  # [lo, hi] per keygroup - mirrors keygroup_range_bar
        self._pending_restore_state = None  # set only by _refresh_from_hardware()
        self._refresh_in_progress = False
        self._multi_refresh_in_progress = False

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
        self._worker.start()

        # placeholder - real program, keygroup panels come later
        self.program_list = QListWidget()
        self.program_list.setObjectName("programList")
        self.program_list.setFixedWidth(160)

        self.keygroup_list = QListWidget()
        self.keygroup_list.setObjectName("keygroupList")
        self.keygroup_list.setFixedWidth(190)  # fits "Keygroup 12: C#1 - D#7"

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
        self.cutoff_knob.setFixedSize(64, 64)
        self.resonance_knob = Knob()
        self.resonance_knob.setRange(0, 15)
        self.resonance_knob.setDefaultValue(0)  # no resonance
        self.resonance_knob.setFixedSize(64, 64)
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
        self.key_filter_track_knob.setFixedSize(64, 64)

        self.env1_graph = ADSREnvelopeGraph()
        self.env1_graph.setFixedSize(200, 90)
        self.env2_graph = Envelope2Graph()
        self.env2_graph.setFixedSize(200, 90)

        # ENV1 - a standard ADSR: one knob per stage, in a single row
        self.attack1_knob = Knob()
        self.attack1_knob.setRange(0, 99)
        self.attack1_knob.setFixedSize(40, 40)
        self.decay1_knob = Knob()
        self.decay1_knob.setRange(0, 99)
        self.decay1_knob.setFixedSize(40, 40)
        self.sustain1_knob = Knob()
        self.sustain1_knob.setRange(0, 99)
        self.sustain1_knob.setFixedSize(40, 40)
        self.release1_knob = Knob()
        self.release1_knob.setRange(0, 99)
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

        # ENV2 - a 4-stage rate/level generator: rate knobs on top, that
        # stage's level knob below it, stages left-to-right in order
        self._env2_rate_knobs = [Knob() for _ in range(4)]
        self._env2_level_knobs = [Knob() for _ in range(4)]
        self._env2_rate_value_labels = []
        self._env2_level_value_labels = []
        env2_rate_row = QHBoxLayout()
        env2_rate_row.setSpacing(6)
        env2_level_row = QHBoxLayout()
        env2_level_row.setSpacing(6)
        for i, (rate_knob, level_knob) in enumerate(
            zip(self._env2_rate_knobs, self._env2_level_knobs), start=1
        ):
            rate_knob.setRange(0, 99)
            rate_knob.setFixedSize(32, 32)
            level_knob.setRange(0, 99)
            level_knob.setFixedSize(32, 32)
            rate_col, rate_value_label = self._build_knob_column(f"R{i}", rate_knob)
            level_col, level_value_label = self._build_knob_column(f"L{i}", level_knob)
            env2_rate_row.addLayout(rate_col)
            env2_level_row.addLayout(level_col)
            self._env2_rate_value_labels.append(rate_value_label)
            self._env2_level_value_labels.append(level_value_label)

            rate_knob.valueChanged.connect(self._on_env2_knob_changed)
            level_knob.valueChanged.connect(self._on_env2_knob_changed)

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

        env2_controls_column = QVBoxLayout()
        env2_controls_column.setSpacing(4)
        env2_controls_column.addLayout(env2_rate_row)
        env2_controls_column.addLayout(env2_level_row)

        env1_column = self._build_labeled_column(
            "ENV1", self.env1_graph, extra_layout=env1_controls_row
        )
        env2_column = self._build_labeled_column(
            "ENV2", self.env2_graph, extra_layout=env2_controls_column
        )

        envelopes_layout = QHBoxLayout()
        envelopes_layout.addLayout(env1_column)
        envelopes_layout.addLayout(env2_column)

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

            # sample row
            sample_row = QHBoxLayout()
            sample_label = QLabel("Sample")
            sample_label.setFixedWidth(55)
            combo = QComboBox()
            combo.setEnabled(False)
            sample_row.addWidget(sample_label)
            sample_row.addWidget(combo, stretch=1)
            page_layout.addLayout(sample_row)
            self._zone_combos.append(combo)

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

            # loudness and pan as small knobs — ±50 range maps naturally
            loud_knob = Knob()
            loud_knob.setRange(-50, 50)
            loud_knob.setFixedSize(40, 40)
            loud_knob.setEnabled(True)
            pan_knob = Knob()
            pan_knob.setRange(-50, 50)
            pan_knob.setFixedSize(40, 40)
            pan_knob.setEnabled(True)

            loud_col, loud_val_label = self._build_knob_column("Loud", loud_knob)
            pan_col, pan_val_label = self._build_knob_column("Pan", pan_knob)

            zone_knob_row = QHBoxLayout()
            zone_knob_row.addLayout(loud_col)
            zone_knob_row.addLayout(pan_col)
            zone_knob_row.addStretch()
            page_layout.addLayout(zone_knob_row)

            self._zone_loudness.append(loud_knob)
            self._zone_pan.append(pan_knob)
            self._zone_loudness_labels.append(loud_val_label)
            self._zone_pan_labels.append(pan_val_label)

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
        # every filter control together, both envelope generators together,
        # then the existing per-zone card
        range_section = self._build_section_card("Range", note_range_row)
        filter_section = self._build_section_card("Filter", knobs_layout)
        envelopes_section = self._build_section_card("Envelopes", envelopes_layout)

        # Range is a single short row and Filter is comparable in height -
        # side by side halves the vertical space these two cost together
        range_filter_row = QHBoxLayout()
        range_filter_row.addWidget(range_section, stretch=1)
        range_filter_row.addWidget(filter_section, stretch=2)

        detail_container_layout = QVBoxLayout()
        detail_container_layout.setSpacing(12)
        detail_container_layout.addLayout(range_filter_row)
        detail_container_layout.addWidget(envelopes_section)
        detail_container_layout.addWidget(zone_card)
        detail_container_layout.addStretch()
        detail_container = QWidget()
        detail_container.setLayout(detail_container_layout)

        self.program_name_edit = QLineEdit()
        self.program_name_edit.setMaxLength(NAME_LENGTH)
        self.program_name_edit.setFixedWidth(140)
        name_pattern = QRegularExpression(_NAME_INPUT_PATTERN)
        name_pattern.setPatternOptions(
            QRegularExpression.PatternOption.CaseInsensitiveOption
        )
        self.program_name_edit.setValidator(
            QRegularExpressionValidator(name_pattern, self.program_name_edit)
        )
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
        self.pan_knob.setFixedSize(64, 64)
        pan_column, self.pan_value_label = self._build_knob_column("Pan", self.pan_knob)

        self.loud_knob = Knob()
        self.loud_knob.setRange(0, 99)
        self.loud_knob.setDefaultValue(80)
        self.loud_knob.setFixedSize(64, 64)
        loud_column, self.loud_value_label = self._build_knob_column(
            "Loud", self.loud_knob
        )

        self.velocity_knob = Knob()
        self.velocity_knob.setRange(-50, 50)
        self.velocity_knob.setDefaultValue(20)
        self.velocity_knob.setFixedSize(64, 64)
        velocity_column, self.velocity_value_label = self._build_knob_column(
            "Velocity", self.velocity_knob
        )

        self.lfo_rate_knob = Knob()
        self.lfo_rate_knob.setRange(0, 99)
        self.lfo_rate_knob.setDefaultValue(0)  # no modulation without depth anyway
        self.lfo_rate_knob.setFixedSize(64, 64)
        self.lfo_depth_knob = Knob()
        self.lfo_depth_knob.setRange(0, 99)
        self.lfo_depth_knob.setDefaultValue(0)  # no modulation
        self.lfo_depth_knob.setFixedSize(64, 64)
        self.lfo_delay_knob = Knob()
        self.lfo_delay_knob.setRange(0, 99)
        self.lfo_delay_knob.setDefaultValue(0)  # no delay before the LFO starts
        self.lfo_delay_knob.setFixedSize(64, 64)

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

        lfo_rate_column, self.lfo_rate_value_label = self._build_knob_column(
            "LFO rate", self.lfo_rate_knob
        )
        lfo_depth_column, self.lfo_depth_value_label = self._build_knob_column(
            "LFO depth", self.lfo_depth_knob
        )
        lfo_delay_column, self.lfo_delay_value_label = self._build_knob_column(
            "LFO delay", self.lfo_delay_knob
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

        self.bend_up_spinbox = QSpinBox()
        self.bend_up_spinbox.setRange(0, 24)
        self.bend_up_spinbox.setSuffix(" st")
        self.bend_up_spinbox.setFixedWidth(70)
        bend_up_column = self._build_labeled_spinbox_column(
            "Bend up", self.bend_up_spinbox
        )

        self.bend_down_spinbox = QSpinBox()
        # s3k.params declares B_PTCHD's range as 0-12 (asymmetric with
        # B_PTCH's 0-24), transcribed from the Akai spec - contradicted by
        # this project's own hardware: confirmed 0-24, matching bend-up,
        # on real S3000-series hardware in front of the user (2026-09-20).
        # Same call as K_FREQ above: trust the direct measurement, don't
        # edit the dependency (AGENTS.md)
        self.bend_down_spinbox.setRange(0, 24)
        self.bend_down_spinbox.setSuffix(" st")
        self.bend_down_spinbox.setFixedWidth(70)
        bend_down_column = self._build_labeled_spinbox_column(
            "Bend down", self.bend_down_spinbox
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

        self.portamento_rate_spinbox = QSpinBox()
        # PORTIME's declared range is the full byte (0-255, s3k.params has
        # no measurement narrowing it) but every other 2-digit performance
        # knob on this hardware (LFORAT, FILFRQ, VOSCL, ...) tops out at 99,
        # so 0-99 is assumed here rather than offering the raw byte
        self.portamento_rate_spinbox.setRange(0, 99)
        self.portamento_rate_spinbox.setFixedWidth(70)
        portamento_rate_column = self._build_labeled_spinbox_column(
            "Rate", self.portamento_rate_spinbox
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
        self.polyph_combo.setMaximumWidth(80)
        self.note_priority_combo.setMaximumWidth(90)
        self.midi_channel_combo.setMaximumWidth(80)

        # Volume, Pan & Velocity - level and stereo-field controls together
        volume_row = QHBoxLayout()
        volume_row.addLayout(pan_column)
        volume_row.addLayout(loud_column)
        volume_row.addLayout(velocity_column)
        volume_row.addStretch()
        volume_section = self._build_section_card("Volume, Pan && Velocity", volume_row)

        # LFO - every LFO1 control (shape, rate, depth, delay) in one place
        lfo_knobs_row = QHBoxLayout()
        lfo_knobs_row.addLayout(lfo_rate_column)
        lfo_knobs_row.addLayout(lfo_depth_column)
        lfo_knobs_row.addLayout(lfo_delay_column)
        lfo_knobs_row.addStretch()
        lfo_shape_row = QHBoxLayout()
        lfo_shape_row.addLayout(lfo_shape_column)
        lfo_shape_row.addStretch()
        lfo_section = self._build_section_card("LFO", lfo_knobs_row, lfo_shape_row)

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
        voice_section = self._build_section_card("Voice && MIDI", voice_row)

        # Portamento
        portamento_row = QHBoxLayout()
        portamento_row.addLayout(portamento_enable_column)
        portamento_row.addLayout(portamento_rate_column)
        portamento_row.addLayout(portamento_type_column)
        portamento_row.addStretch()
        portamento_section = self._build_section_card("Portamento", portamento_row)

        # two cards per row rather than one long stacked column - halves
        # the page's height for the same content. Paired by rough content
        # shape: the two knob-heavy cards together, then the two
        # single-row combo/spinbox cards together; Portamento is the odd
        # one out and gets a row to itself
        program_row1 = QHBoxLayout()
        program_row1.addWidget(volume_section, stretch=1)
        program_row1.addWidget(lfo_section, stretch=1)

        program_row2 = QHBoxLayout()
        program_row2.addWidget(pitch_section, stretch=1)
        program_row2.addWidget(voice_section, stretch=1)

        program_page = QWidget()
        program_page_layout = QVBoxLayout()
        program_page_layout.setSpacing(12)
        program_page_layout.addWidget(name_section)
        program_page_layout.addLayout(program_row1)
        program_page_layout.addLayout(program_row2)
        program_page_layout.addWidget(portamento_section)
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

        self.main_tabs = QTabWidget()
        # same full-width tab bar as the MIDI Settings dialog - must be
        # installed before any tabs are added, or they'd be dropped
        self.main_tabs.setTabBar(FullWidthTabBar(self.main_tabs))
        self.main_tabs.addTab(multis_tab_page, "Multis")
        self.main_tabs.addTab(programs_tab_page, "Programs")
        # Multis stays the first tab, but isn't fully working yet - open on
        # Programs instead
        self.main_tabs.setCurrentIndex(1)

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
        self.program_tune_spinbox.setEnabled(True)
        self._wire_spinbox_write(
            self.program_tune_spinbox,
            "PTUNO",
            "program",
            value_converter=self._semitones_to_tune_offset,
        )
        self.bend_up_spinbox.setEnabled(True)
        self._wire_spinbox_write(self.bend_up_spinbox, "B_PTCH", "program")
        self.bend_down_spinbox.setEnabled(True)
        self._wire_spinbox_write(self.bend_down_spinbox, "B_PTCHD", "program")
        self.portamento_enable_combo.setEnabled(True)
        self._wire_combo_write(self.portamento_enable_combo, "PORTEN", "program")
        self.portamento_rate_spinbox.setEnabled(True)
        self._wire_spinbox_write(self.portamento_rate_spinbox, "PORTIME", "program")
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
            if not self._busy_show_timer.isActive() and self._loading_progress.isHidden():
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
        self.bend_up_spinbox.blockSignals(True)
        self.bend_up_spinbox.setValue(program_values["B_PTCH"])
        self.bend_up_spinbox.blockSignals(False)
        self.bend_down_spinbox.blockSignals(True)
        self.bend_down_spinbox.setValue(program_values["B_PTCHD"])
        self.bend_down_spinbox.blockSignals(False)
        self.portamento_enable_combo.blockSignals(True)
        self.portamento_enable_combo.setCurrentIndex(program_values["PORTEN"])
        self.portamento_enable_combo.blockSignals(False)
        self.portamento_rate_spinbox.blockSignals(True)
        self.portamento_rate_spinbox.setValue(program_values["PORTIME"])
        self.portamento_rate_spinbox.blockSignals(False)
        self.portamento_type_combo.blockSignals(True)
        self.portamento_type_combo.setCurrentIndex(program_values["PORTYPE"])
        self.portamento_type_combo.blockSignals(False)
        self.portamento_type_combo.setToolTip(
            _PORTAMENTO_TYPE_OPTIONS[self.portamento_type_combo.currentIndex()][1]
        )

        # only ever set by _refresh_from_hardware() - restores whatever the
        # user was looking at before the refresh (a plain program selection
        # never sets this, so this is a no-op on the normal load path)
        restore = self._pending_restore_state
        self._pending_restore_state = None
        if restore is not None:
            keygroup_index = restore["keygroup_index"]
            if 0 <= keygroup_index < self.keygroup_list.count():
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

        self._main_window.show()
        event.accept()

    def _build_knob_column(self, label_text, knob):
        name_label = QLabel(label_text)
        name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        name_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        value_label = QLabel("-")
        value_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        # value_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        column = QVBoxLayout()
        column.setSpacing(4)  # fixed gap, in pixels - never stretches
        column.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignHCenter)
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

    def _build_labeled_column(self, label_text, widget, extra_layout=None):
        name_label = QLabel(label_text)
        name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        name_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        column = QVBoxLayout()
        column.setSpacing(4)
        column.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(widget, alignment=Qt.AlignmentFlag.AlignHCenter)
        if extra_layout is not None:
            column.addLayout(extra_layout)
        column.addStretch()

        return column

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
        self._write_knob_value("PRNAME", "program", name, keygroup_index=0)

    def _on_samples_loaded(self, samples):
        self._sample_list = samples
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
