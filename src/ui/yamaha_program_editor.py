"""Yamaha A4000/A5000 program editor - VIEW-ONLY in this first release.

Laid out like the S3000 editor (Programs | assigned samples | cards, plus a Samples tab) but for the
A4000's data model, which differs from an Akai's: a PROGRAM holds program-level settings and a list of
assigned samples; each assigned sample carries Easy Edit overrides that apply to THIS program only, while
the sample's own sound (filter, envelopes, key range ...) lives on the sample and is shared by every
program that plays it - that part is the Samples tab. So the middle column lists a program's assigned
samples the way the S3000 editor lists keygroups, with each one's EFFECTIVE key range on the bar: the
sample's own range moved by the Easy Edit key shift and cut by its key limits (owner's manual p.99).

All talking to the unit goes through the Dashboard's `SamplerController` -> `YamahaSession`
(controller/yamaha_session.py): the SAME MIDI connection, one operation at a time, no worker thread.
A program is read with ONE bulk dump (`PG`), a sample with one (`SP`); the program list is filled by a
background scan of every program's name and assigned-sample count (~7 s), and programs with nothing
assigned are hidden unless "Show empty programs" is ticked (there are always 128).

Nothing is written to the unit: every control is shown disabled. Editing arrives with the write stage.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core import debug_log
from core import yamaha_params as yp
from core import yamaha_sysex as ysx
from core.midi_notes import midi_note_to_name
from ui import theme, tooltips as tt
from ui.diagnostics_ui import add_open_log_folder_action
from ui.editor_layout import (
    add_keygroup_row,
    build_content_row,
    build_list_column,
    build_paired_row,
    equalize_card_heights,
    keygroup_row_label,
    style_card_page_layout,
)
from ui.keygroup_range_bar import KeygroupRangeBar, keygroup_color
from ui.qt_helpers import FullWidthTabBar, build_scroll_area, build_section_card
from ui.yamaha_fields import Field, FieldPanel
from ui.yamaha_samples_tab import YamahaSamplesTab, _check, _combo, _knob, _spin

_SPECIAL_ALL_NOTES = {-1: "All notes"}
NOTE_MAX = 127


def _log(message):
    debug_log.get_logger().info(f"YamahaEditor: {message}")


# -- pure helpers (tested without a window) -----------------------------------------------------------


def program_row_text(number, name):
    return f"{number:03d}  {name}" if name else f"{number:03d}"


def effective_key_range(sample_data, easy_data, slot):
    """(low, high) a sample really plays in a program: its own range moved by the Easy Edit key range shift and
    cut by the key limits (owner's manual p.99: the shift offsets the sample's original key, low and high
    together; notes below the low limit / above the high limit do not sound). A sample's "Original" ends
    (low -1, high 128) are taken as the keyboard's ends. `easy_data` is the PROGRAM payload (slot picks the block)."""
    low = yp.extract(yp.get("sample", "key_range_low"), sample_data)
    high = yp.extract(yp.get("sample", "key_range_high"), sample_data)
    low = 0 if low < 0 else low
    high = NOTE_MAX if high > NOTE_MAX else high
    shift = yp.extract(yp.get("easy_edit", "key_range_shift"), easy_data, slot)
    limit_low = yp.extract(yp.get("easy_edit", "key_limit_low"), easy_data, slot)
    limit_high = yp.extract(yp.get("easy_edit", "key_limit_high"), easy_data, slot)
    low, high = max(low + shift, limit_low), min(high + shift, limit_high)
    low = min(max(low, 0), NOTE_MAX)
    high = min(max(high, low), NOTE_MAX)
    return low, high


def format_range(low, high):
    return f"{midi_note_to_name(low)} - {midi_note_to_name(high)}"


# -- the window ---------------------------------------------------------------------------------------------


class YamahaProgramEditorWindow(QMainWindow):
    def __init__(self, main_window, sampler_controller):
        super().__init__()
        self._main_window = main_window
        self._controller = sampler_controller
        self._session = sampler_controller.yamaha_session()
        _log("opened")
        self.setWindowTitle("AKAISDS - Yamaha A4000/A5000 Program Editor")
        self.setMinimumSize(1150, 720)

        self._program_numbers = []  # 1..128 from the object list
        self._sample_names = []
        self._counts = {}  # program number -> assigned-sample count (None until scanned)
        self._names = {}  # program number -> its 8-character name
        self._program_data = {}  # program number -> bulk payload
        self._sample_cache = {}  # sample name -> bulk payload (shared with the Samples tab)
        self._items = {}  # program number -> QListWidgetItem
        self._selected = None  # program number being shown
        self._scan_generation = 0  # bumped by every refresh so a late scan result is dropped
        self._scanning = False
        self._loading_program = None
        self._range_rows = []  # [(low, high)] of the assigned list (None until known)
        self._assigned = []  # [(sample name, object type)] of the shown program
        self._connected = True

        self.program_panel = FieldPanel("program")
        self.easy_panel = FieldPanel("easy_edit")
        self._build_ui()
        self._build_menus()
        self._controller.status_changed.connect(self._on_controller_status)
        theme.notifier.changed.connect(self._refresh_themed_swatches)

        self._show_placeholder("Reading the object list...")
        QTimer.singleShot(0, self._refresh)

    # -- construction -------------------------------------------------------------------------------------

    def _build_ui(self):
        self.program_list = QListWidget()
        self.program_list.setObjectName("programList")
        self.program_list.setFixedWidth(190)
        self.program_list.currentItemChanged.connect(self._on_program_selected)
        self.program_list.itemClicked.connect(lambda _item: self.detail_stack.setCurrentIndex(0))
        self.show_empty_check = QCheckBox("Show empty programs")
        self.show_empty_check.setToolTip("There are always 128 programs; most are empty")
        self.show_empty_check.toggled.connect(self._apply_visibility)
        self.scan_label = QLabel("")
        self.scan_label.setObjectName("mutedLabel")
        programs_container = build_list_column("Programs", self.program_list, self.show_empty_check, self.scan_label)

        self.assigned_list = QListWidget()
        self.assigned_list.setObjectName("keygroupList")
        self.assigned_list.setFixedWidth(230)
        self.assigned_list.currentRowChanged.connect(self._show_assigned)
        self.assigned_list.itemClicked.connect(lambda _item: self.detail_stack.setCurrentIndex(1))
        self.range_bar = KeygroupRangeBar()
        assigned_container = build_list_column("Assigned samples", self.range_bar, self.assigned_list)

        self.placeholder = QLabel()
        self.placeholder.setObjectName("emptyQueueLabel")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(build_scroll_area(self._build_program_page()))
        self.detail_stack.addWidget(build_scroll_area(self._build_assigned_page()))
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.placeholder, 1)
        right.addWidget(self.detail_stack, 1)
        right_container = QWidget()
        right_container.setLayout(right)

        programs_tab = QWidget()
        programs_tab.setLayout(build_content_row(programs_container, assigned_container, right_container))
        self.samples_tab = YamahaSamplesTab(self._controller, self._session, self._sample_cache)
        self.samples_tab.status_message.connect(lambda m: self.status_bar.showMessage(m, 8000))
        self.main_tabs = QTabWidget()
        self.main_tabs.setTabBar(FullWidthTabBar(self.main_tabs))
        self.main_tabs.addTab(programs_tab, "Programs")
        self._samples_tab_index = self.main_tabs.addTab(self.samples_tab, "Samples")
        self.main_tabs.currentChanged.connect(self._on_tab_changed)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("Re-read the program and sample lists from the sampler")
        self.refresh_button.clicked.connect(self._refresh)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addWidget(self.refresh_button)
        bottom.addStretch()
        bottom.addWidget(close_button)
        layout = QVBoxLayout()
        layout.addWidget(self.main_tabs, stretch=1)
        layout.addLayout(bottom)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.status_bar = QStatusBar()
        self.status_bar.setSizeGripEnabled(False)
        self.setStatusBar(self.status_bar)
        note = QLabel("Experimental - view only: nothing is written to the sampler")
        note.setObjectName("mutedLabel")
        self.status_bar.addPermanentWidget(note)

    def _build_program_page(self):
        p = self.program_panel
        row, knobs = p.labeled_row, p.knob_row
        k, s, c, ck = _knob, _spin, _combo, _check
        note = Field("lfo_reset_note", "Reset note", "note", specials=_SPECIAL_ALL_NOTES)

        program = build_section_card(
            "Program",
            row(Field("program_name", "Name", "text")),
            row(Field("assigned_samples", "Assigned samples", "text")),
            knobs([k("program_level", "Level", 56)]),
            row(s("transpose", "Transpose")),
        )
        lfo = build_section_card(
            "LFO",
            row(c("lfo_wave", "Wave")),
            row(c("lfo_sync", "Sync")),
            row(s("lfo_tempo", "Tempo")),
            row(s("lfo_cycle", "Cycle")),
            row(s("lfo_initial_phase", "Initial phase")),
            row(note),
            row(c("lfo_reset_midi_channel", "Reset channel")),
        )
        equalize_card_heights(program, lfo)

        porta = build_section_card(
            "Portamento & S/H",
            row(s("portamento_type", "Type")),
            knobs([k("portamento_rate", "Rate"), k("portamento_time", "Time"), k("sh_speed", "S/H speed")]),
        )
        audio = build_section_card(
            "Audio Input",
            row(ck("ad_in_on", "Input on")),
            row(s("ad_in_source", "Source")),
            knobs([k("ad_in_l_pan", "L pan"), k("ad_in_r_pan", "R pan")]),
            row(c("ad_in_l_output1", "L output 1")),
            row(c("ad_in_l_output2", "L output 2")),
            row(c("ad_in_r_output1", "R output 1")),
            row(c("ad_in_r_output2", "R output 2")),
            knobs([k("ad_in_l_output1_level", "L out 1"), k("ad_in_l_output2_level", "L out 2"),
                   k("ad_in_r_output1_level", "R out 1"), k("ad_in_r_output2_level", "R out 2")]),
        )
        equalize_card_heights(porta, audio)

        steps = [k(f"lfo_step_value_{n}", str(n), 28) for n in range(1, 17)]
        step_card = build_section_card(
            "LFO Step Wave",
            row(s("lfo_step_total", "Total steps")),
            row(s("lfo_step_slope", "Slope")),
            p.knob_row(steps[:8], spacing=6),
            p.knob_row(steps[8:], spacing=6),
        )
        effects_note = QLabel("Effects and controllers are not shown yet.")
        effects_note.setObjectName("mutedLabel")

        layout = QVBoxLayout()
        style_card_page_layout(layout)
        layout.addLayout(build_paired_row(program, lfo))
        layout.addLayout(build_paired_row(porta, audio))
        layout.addWidget(step_card)
        layout.addWidget(effects_note)
        layout.addStretch()
        page = QWidget()
        page.setLayout(layout)
        return page

    def _build_assigned_page(self):
        e = self.easy_panel
        row, knobs = e.labeled_row, e.knob_row
        k, s, c, ck = _knob, _spin, _combo, _check
        low_note = Field("key_limit_low", "Key low limit", "note")
        high_note = Field("key_limit_high", "Key high limit", "note")

        self.assigned_name = QLabel("")
        self.assigned_name.setObjectName("sectionHeader")
        self.assigned_info = QLabel("")
        self.assigned_info.setObjectName("mutedLabel")
        self.assigned_info.setWordWrap(True)
        self.edit_sample_button = QPushButton("Edit Sample...")
        self.edit_sample_button.setToolTip("Open this sample's own parameters (filter, envelopes, loop...) in the Samples tab")
        self.edit_sample_button.clicked.connect(self._edit_sample)
        head = QHBoxLayout()
        title = QVBoxLayout()
        title.setSpacing(2)
        title.addWidget(self.assigned_name)
        title.addWidget(self.assigned_info)
        head.addLayout(title, stretch=1)
        head.addWidget(self.edit_sample_button)

        key = build_section_card(
            "Key",
            row(low_note),
            row(high_note),
            row(s("key_range_shift", "Key range shift")),
            row(s("velocity_limit_low", "Velocity low limit")),
            row(s("velocity_limit_high", "Velocity high limit")),
        )
        level = build_section_card(
            "Level & Pan",
            knobs([k("level_offset", "Level"), k("pan_offset", "Pan"), k("velocity_sensitivity_offset", "Velocity")]),
        )
        equalize_card_heights(key, level)

        pitch = build_section_card(
            "Pitch",
            knobs([k("coarse_tune_offset", "Coarse"), k("fine_tune_offset", "Fine")]),
            row(c("portamento", "Portamento")),
        )
        filt = build_section_card(
            "Filter",
            knobs([k("filter_cutoff_offset", "Cutoff"), k("filter_q_offset", "Q / Width"),
                   k("filter_gain_offset", "Gain"), k("cutoff_distance_offset", "Distance")]),
        )
        equalize_card_heights(pitch, filt)

        aeg = build_section_card(
            "Amplitude Envelope",
            knobs([k("aeg_attack_rate_offset", "Attack"), k("aeg_decay_rate_offset", "Decay"),
                   k("aeg_release_rate_offset", "Release")]),
        )
        xfade = build_section_card(
            "Velocity Crossfade",
            knobs([k("velocity_xfade_low_offset", "Low"), k("velocity_xfade_high_offset", "High")]),
        )
        equalize_card_heights(aeg, xfade)

        out = build_section_card(
            "Output & Playback",
            row(c("output1", "Output 1")),
            row(c("output2", "Output 2")),
            knobs([k("output1_level_offset", "Out 1 level"), k("output2_level_offset", "Out 2 level")]),
            row(c("receive_channel", "MIDI channel")),
            row(c("mono_mode", "Mono mode")),
            row(c("key_xfade_on", "Key crossfade")),
            row(Field("alternate_group", "Alternate group", "spin", specials={-1: "=Sample"})),
            row(ck("midi_control_on", "MIDI control")),
        )

        layout = QVBoxLayout()
        style_card_page_layout(layout)
        layout.addLayout(head)
        layout.addLayout(build_paired_row(key, level))
        layout.addLayout(build_paired_row(pitch, filt))
        layout.addLayout(build_paired_row(aeg, xfade))
        layout.addWidget(out)
        layout.addStretch()
        page = QWidget()
        page.setLayout(layout)
        return page

    def _build_menus(self):
        hardware = self.menuBar().addMenu("&Hardware")
        self._refresh_action = QAction("Refresh from Hardware", self)
        self._refresh_action.setShortcut("Ctrl+R")
        self._refresh_action.triggered.connect(self._refresh)
        hardware.addAction(self._refresh_action)
        hardware.addSeparator()
        add_open_log_folder_action(hardware, self)

        window = self.menuBar().addMenu("&Window")
        dashboard = QAction("Transfer Dashboard", self)
        dashboard.setShortcut("Ctrl+T")
        dashboard.triggered.connect(self.close)
        window.addAction(dashboard)
        editor = QAction("Program Editor", self)
        editor.setShortcut("Ctrl+E")
        editor.setEnabled(False)
        window.addAction(editor)
        window.addSeparator()
        programs = QAction("Programs Tab", self)
        programs.setShortcut("Ctrl+2")
        programs.triggered.connect(lambda: self.main_tabs.setCurrentIndex(0))
        window.addAction(programs)
        samples = QAction("Samples Tab", self)
        samples.setShortcut("Ctrl+3")
        samples.triggered.connect(lambda: self.main_tabs.setCurrentIndex(self._samples_tab_index))
        window.addAction(samples)

    def _on_tab_changed(self, index):
        on_samples = index == self._samples_tab_index
        _log(f"tab: {'Samples' if on_samples else 'Programs'}")
        self.samples_tab.set_active(on_samples)

    # -- loading ------------------------------------------------------------------------------------------

    def _show_placeholder(self, text):
        self.placeholder.setText(text)
        self.placeholder.setVisible(True)
        self.detail_stack.setVisible(False)
        self.assigned_list.clear()
        self.range_bar.set_ranges([])

    def _show_details(self):
        self.placeholder.setVisible(False)
        self.detail_stack.setVisible(True)

    def _refresh(self):
        _log("refresh")
        self._scan_generation += 1
        self._scanning = False
        self._session.cancel()
        self._counts.clear()
        self._names.clear()
        self._program_data.clear()
        self._sample_cache.clear()
        self.samples_tab.refresh()
        self._selected = None
        self.program_list.blockSignals(True)
        self.program_list.clear()
        self.program_list.blockSignals(False)
        self._items.clear()
        self._show_placeholder("Reading the object list...")
        self.scan_label.setText("")
        self._session.request_object_list(self._on_object_list)

    def _on_object_list(self, entries):
        if entries is None:
            self._show_placeholder(
                "Couldn't read the sampler's object list.\n\nCheck the MIDI ports, that its Device Number is set "
                "(0 unless config.json's \"yamaha_device_number\" says otherwise) and that Bulk Protect is off."
            )
            return
        self._program_numbers = [int(e.name) for e in entries if e.kind == "program" and e.name.isdigit()]
        self._sample_names = [e.name for e in entries if e.kind == "sample"]
        _log(f"object list: {len(self._program_numbers)} programs, {len(self._sample_names)} samples")
        self.program_list.blockSignals(True)
        for number in self._program_numbers:
            item = QListWidgetItem(program_row_text(number, ""))
            item.setData(Qt.ItemDataRole.UserRole, number)
            self.program_list.addItem(item)
            self._items[number] = item
        self.program_list.blockSignals(False)
        self.samples_tab.set_samples(self._sample_names)
        self._apply_visibility()
        self._show_placeholder("Reading the programs...")
        self._start_scan()

    # -- the background scan (names + assigned counts) --------------------------------------------------------

    def _start_scan(self):
        self._scanning = True
        self._scan_next(self._scan_generation)

    def _scan_next(self, generation):
        if generation != self._scan_generation or not self._connected:
            return
        todo = [n for n in self._program_numbers if n not in self._counts]
        if todo:
            # pass 1: every program's assigned-sample count (one small request each - ~7 s for all 128)
            number = todo[0]
            done = len(self._program_numbers) - len(todo)
            self.scan_label.setText(f"Scanning programs... {done}/{len(self._program_numbers)}")
            self._session.request_parameters(
                "program", ysx.program_object_name(number), [yp.get("program", "assigned_samples").p],
                lambda results, n=number, g=generation: self._on_scanned(g, n, results),
            )
            return
        # pass 2: the names - only of programs that have samples (an empty one is just its number)
        unnamed = [n for n in self._program_numbers if self._counts.get(n) and n not in self._names]
        if unnamed:
            number = unnamed[0]
            self._session.request_parameters(
                "program", ysx.program_object_name(number), [yp.get("program", "program_name").p],
                lambda results, n=number, g=generation: self._on_named(g, n, results),
            )
            return
        self._scanning = False
        shown = sum(1 for n in self._program_numbers if self._counts.get(n))
        self.scan_label.setText(f"{shown} of {len(self._program_numbers)} programs have samples")
        if self._selected is None and not self._first_visible_number():
            self._show_placeholder("No programs have samples assigned.\n\nTick \"Show empty programs\" to see all 128.")

    def _on_named(self, generation, number, results):
        if generation != self._scan_generation or not self._connected:
            return
        msg = results[0]
        # a failed name read just leaves the bare number (never retried this refresh)
        self._names[number] = "" if msg is None else yp.decode_reply(yp.get("program", "program_name"), msg.data)
        self._items[number].setText(program_row_text(number, self._names[number]))
        QTimer.singleShot(0, lambda g=generation: self._scan_next(g))

    def _on_scanned(self, generation, number, results):
        if generation != self._scan_generation or not self._connected:
            return
        count_msg = results[0]
        if count_msg is None:
            self._scanning = False
            self.scan_label.setText("The scan stopped - the sampler stopped answering")
            if self._selected is None:
                self._show_placeholder("The sampler stopped answering while reading the program list.\n\nPress Refresh to try again.")
            return
        self._counts[number] = yp.decode_reply(yp.get("program", "assigned_samples"), count_msg.data)
        item = self._items[number]
        self._apply_visibility(only=number)
        if self._selected is None and self._counts[number] and self._first_visible_number() == number:
            self.program_list.setCurrentItem(item)  # the first program with samples, as the S3000 editor does
        QTimer.singleShot(0, lambda g=generation: self._scan_next(g))

    def _first_visible_number(self):
        for number in self._program_numbers:
            if not self._items[number].isHidden():
                return number
        return None

    def _apply_visibility(self, _checked=None, only=None):
        show_all = self.show_empty_check.isChecked()
        for number in [only] if only is not None else self._program_numbers:
            item = self._items.get(number)
            if item is None:
                continue
            has_samples = bool(self._counts.get(number))
            item.setHidden(not (show_all or has_samples))
        current = self.program_list.currentItem()
        if current is not None and current.isHidden():
            self.program_list.setCurrentItem(None)

    # -- one program ------------------------------------------------------------------------------------------

    def _on_program_selected(self, current, _previous):
        if current is None:
            return
        number = current.data(Qt.ItemDataRole.UserRole)
        self._selected = number
        _log(f"program selected: {number:03d}")
        self.detail_stack.setCurrentIndex(0)
        if number in self._program_data:
            self._show_program(number)
            return
        self._show_placeholder(f"Reading program {number:03d}...")
        self._loading_program = number
        self._session.request_bulk(
            "PG", ysx.program_object_name(number), lambda dump, n=number: self._on_program_dump(n, dump)
        )

    def _on_program_dump(self, number, dump):
        if not self._connected:
            return
        if dump is None:
            if number == self._selected:
                self._show_placeholder(f"Couldn't read program {number:03d} from the sampler.")
            return
        self._program_data[number] = bytes(dump.data)
        if number == self._selected:
            self._show_program(number)

    def _show_program(self, number):
        data = self._program_data[number]
        self.program_panel.fill(data)
        count = yp.extract(yp.get("program", "assigned_samples"), data)
        self._assigned = []
        for slot in range(count):
            name = yp.extract(yp.get("easy_edit", "assigned_name"), data, slot)
            otype = yp.extract(yp.get("easy_edit", "assigned_type"), data, slot)
            self._assigned.append((name, otype))
        self._range_rows = [None] * count
        self.assigned_list.blockSignals(True)
        self.assigned_list.clear()
        for slot, (name, otype) in enumerate(self._assigned):
            add_keygroup_row(self.assigned_list, slot, 0, NOTE_MAX, self._refresh_swatch)
            self._set_row_text(slot)
        self.assigned_list.blockSignals(False)
        self._update_range_bar()
        self._show_details()
        self.detail_stack.setCurrentIndex(0)
        if count:
            self.assigned_list.setCurrentRow(0)
            self.detail_stack.setCurrentIndex(0)  # the program page first, as in the S3000 editor
            self._fetch_sample_ranges(number)

    def _fetch_sample_ranges(self, number):
        # each assigned sample's own key range comes from the sample's dump - one at a time, only for
        # samples (type 16) we don't already hold
        for slot, (name, otype) in enumerate(self._assigned):
            if otype != ysx.OBJECT_TYPES["sample"] or not name:
                continue
            if name in self._sample_cache:
                self._on_sample_for_range(number, name, self._sample_cache[name])
                continue
            self._session.request_bulk(
                "SP", name, lambda dump, n=number, nm=name: self._on_range_dump(n, nm, dump)
            )

    def _on_range_dump(self, number, name, dump):
        if not self._connected or dump is None:
            return
        self._sample_cache[name] = bytes(dump.data)
        self._on_sample_for_range(number, name, self._sample_cache[name])

    def _on_sample_for_range(self, number, name, sample_data):
        if number != self._selected:
            return
        program_data = self._program_data[number]
        for slot, (assigned_name, _otype) in enumerate(self._assigned):
            if assigned_name == name:
                self._range_rows[slot] = effective_key_range(sample_data, program_data, slot)
                self._set_row_text(slot)
        self._update_range_bar()
        if self.assigned_list.currentRow() >= 0:
            self._show_assigned(self.assigned_list.currentRow())

    def _set_row_text(self, slot):
        name, otype = self._assigned[slot]
        label = keygroup_row_label(self.assigned_list, slot)
        if label is None:
            return
        rng = self._range_rows[slot]
        if otype != ysx.OBJECT_TYPES["sample"]:
            suffix = "(sample bank)"
        else:
            suffix = format_range(*rng) if rng else "..."
        label.setText(f"{name}: {suffix}" if name else f"(empty slot): {suffix}")

    def _update_range_bar(self):
        self.range_bar.set_ranges([r for r in self._range_rows if r])

    def _refresh_swatch(self, swatch):
        color = keygroup_color(swatch.property("keygroupIndex")).name()
        swatch.setStyleSheet(f"background-color: {color}; border-radius: 2px;")

    def _refresh_themed_swatches(self):
        for label in self.assigned_list.findChildren(QLabel):
            if label.property("swatchKind"):
                self._refresh_swatch(label)

    # -- one assigned sample ---------------------------------------------------------------------------------

    def _show_assigned(self, row):
        number = self._selected
        if number is None or number not in self._program_data or not 0 <= row < len(self._assigned):
            return
        data = self._program_data[number]
        name, otype = self._assigned[row]
        self.easy_panel.fill(data, slot=row)
        self.assigned_name.setText(name or "(empty slot)")
        rng = self._range_rows[row]
        if otype != ysx.OBJECT_TYPES["sample"]:
            info = "A sample bank - its members' ranges aren't shown here."
        elif rng is None:
            info = "Reading the sample's key range..."
        else:
            own_low = yp.extract(yp.get("sample", "key_range_low"), self._sample_cache[name])
            own_high = yp.extract(yp.get("sample", "key_range_high"), self._sample_cache[name])
            own = "original" if own_low < 0 or own_high > NOTE_MAX else format_range(own_low, own_high)
            info = f"Plays {format_range(*rng)} in this program (the sample's own range: {own})"
        self.assigned_info.setText(info)
        self.edit_sample_button.setEnabled(otype == ysx.OBJECT_TYPES["sample"] and name in self._sample_names)

    def _edit_sample(self):
        row = self.assigned_list.currentRow()
        if 0 <= row < len(self._assigned):
            self.main_tabs.setCurrentIndex(self._samples_tab_index)
            self.samples_tab.select_sample(self._assigned[row][0])

    # -- status / closing --------------------------------------------------------------------------------------

    def _on_controller_status(self, message):
        self.status_bar.showMessage(message, 8000)

    def closeEvent(self, event):
        # same rule as the other editors: don't hand control back to the Dashboard while a transfer is mid-flight
        if self._controller.is_transfer_busy():
            self.status_bar.showMessage(
                "Can't switch to the Transfer Dashboard - a MIDI operation is already in progress", 5000
            )
            event.ignore()
            return
        _log("closed")
        self._scan_generation += 1
        self._session.cancel()
        if not self._connected:
            super().closeEvent(event)
            self._main_window.show()
            return
        self._connected = False
        self.samples_tab.disconnect_controller()
        for signal, slot in (
            (self._controller.status_changed, self._on_controller_status),
            (theme.notifier.changed, self._refresh_themed_swatches),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._main_window.show()
        super().closeEvent(event)
