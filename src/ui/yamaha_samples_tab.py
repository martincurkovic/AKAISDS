"""The Samples tab of the Yamaha A4000 editor.

Laid out like the S3000/S950 editors' Samples tab (a sample list on the left, a waveform card and a column
of parameter cards on the right, from the same `ui/editor_layout.py` pieces), but for the A4000's data
model: a SAMPLE owns the whole sound - key range, tuning, loop, filter, the three envelopes, LFO, EQ,
outputs, controllers - and is shared by every program that plays it. So the header says which programs
use the selected sample ("Used in programs ...").

One bulk dump (`SP`) per sample fills every card (`FieldPanel.fill`); dumps are cached for the editor's
session in `sample_cache` (shared with the Programs tab, which needs each sample's key range).

EDITING (when the host passes a `WriteCoordinator`): a changed control is written to the sample through
`YamahaSession.write_parameter` (backup first, read back - see ui/yamaha_writer.py). A sample is SHARED by every
program that uses it, so the header says so. Not editable: the wave/loop start-length-end addresses are shown as
text only (they are coupled - writing one moves others - see dev_docs/a4000-editor-roadmap.md), as are the
sampling frequency, wave length and wave end (the unit ignored writes to them).

THE WAVEFORM comes from the unit's native WAVE DATA bulk dump (core/yamaha_wave.py), on request (double-click the waveform):
the sample links a left wave object (and, for a STEREO sample, a right one), each fetched by its own name through the
session. Both channels are shown, stacked at half height each; the view fills in progressively as each ~4 KB message
lands. Faster than Sample Dump Standard (no per-packet handshake), reaches the right channel SDS can't, and kept working
on a unit whose SDS dumps stalled. A long wave takes a while at MIDI speed (~620 frames/s), so there is a Cancel button; a wave
dump can't be aborted on the unit, so the session drains the rest of its stream before its next request (see
controller/yamaha_session.py). The loaded audio is checked against the sample's own parameters (frames == wave length or
wave end) and refused if it doesn't match, rather than showing the wrong sample's audio.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import debug_log
from core import yamaha_params as yp
from ui import tooltips as tt
from ui.editor_layout import (
    build_list_column,
    build_paired_row,
    build_samples_page,
    build_waveform_scrollbar,
    equalize_card_heights,
    style_card_page_layout,
    sync_waveform_scrollbar,
)
from ui.qt_helpers import build_scroll_area, build_section_card
from ui.waveform_view import WaveformView
from ui.yamaha_fields import Field, FieldPanel

_SPECIAL_ORIGINAL = {-1: "Original", 128: "Original"}


def _log(message):
    debug_log.get_logger().info(f"YamahaEditor: {message}")


# -- formatting (pure) -----------------------------------------------------------------------------------


def format_hz(value):
    return f"{value:,} Hz"


def format_address(value):
    return f"{value:,}"


def format_tempo(value):
    return f"{value / 100:.2f}"


def format_used_in(programs):
    if not programs:
        return "Not used by any program"
    shown = ", ".join(f"{n:03d}" for n in programs[:12])
    return f"Used in programs {shown}" + (f" and {len(programs) - 12} more" if len(programs) > 12 else "")


def format_duration(frames, rate):
    return f"{frames / rate:.3f} s" if rate > 0 else ""


#: loop modes that actually loop (owner's manual p.123): continuous loop and loop-to-release
_LOOPING_MODES = (1, 2)
_LOAD_HINT = "Double-click the waveform to load the sample's audio."
#: the waveform area is 180 px tall: one mono view, or a stereo pair at half height each
_MONO_VIEW_HEIGHT = 180
_STEREO_VIEW_HEIGHT = 90


def sample_markers(data):
    """(frames, start, loop_start, loop_end, end, loops) for the waveform, clamped into the sample.

    `frames` is the wave end address (the whole wave - for every sample seen it equals the SDS length);
    start/end are the wave start/end, a looping mode (continuous / to-release) shows the loop markers."""
    g = lambda key: yp.extract(yp.get("sample", key), data)  # noqa: E731
    frames = max(g("wave_end_address"), g("wave_length"))
    last = max(frames - 1, 0)
    start = min(max(g("wave_start_address"), 0), last)
    end = min(max(g("wave_end_address") - 1, start), last)
    loops = g("loop_mode") in _LOOPING_MODES
    loop_start = min(max(g("loop_start_address"), start), end) if loops else end
    loop_end = min(max(g("loop_end_address") - 1, loop_start), end) if loops else end
    return frames, start, loop_start, loop_end, end, loops


def audio_matches(sample_data, frames):
    """Whether a received wave of `frames` frames is plausibly THIS sample's (see the module docstring)."""
    g = lambda key: yp.extract(yp.get("sample", key), sample_data)  # noqa: E731
    return frames in (g("wave_length"), g("wave_end_address"))


# -- the cards (declared as data) ------------------------------------------------------------------------


def _knob(key, label, size=40):
    return Field(key, label, "knob", size)


def _spin(key, label):
    return Field(key, label, "spin")


def _combo(key, label):
    return Field(key, label, "combo")


def _check(key, label):
    return Field(key, label, "check")


def _text(key, label, fmt):
    return Field(key, label, "text", fmt=fmt)


def build_sample_cards(panel):
    """The sample parameter cards, as (title, rows) built through `panel` - returns the page's layout."""
    k, s, c, ck, t = _knob, _spin, _combo, _check, _text
    note = lambda key, label, specials=None: Field(key, label, "note", specials=specials)  # noqa: E731
    row = panel.labeled_row
    knobs = panel.knob_row

    pitch = build_section_card(
        "Pitch",
        row(note("original_key_l", "Original key"), 110),
        knobs([k("coarse_tune", "Coarse"), k("fine_tune_l", "Fine")]),
        row(s("detune", "Detune")),
        row(s("random_pitch", "Random pitch")),
        row(s("pitch_bend_range", "Bend range")),
        row(s("pitch_bend_type", "Bend type")),
    )
    key_range = build_section_card(
        "Key & Velocity Range",
        row(note("key_range_low", "Key low", _SPECIAL_ORIGINAL), 110),
        row(note("key_range_high", "Key high", _SPECIAL_ORIGINAL), 110),
        row(s("velocity_range_low", "Velocity low")),
        row(s("velocity_range_high", "Velocity high")),
        row(s("velocity_low_limit", "Velocity limit")),
        row(s("alternate_group", "Alternate group")),
        row(ck("mono_mode", "Mono mode")),
        row(ck("fixed_pitch_on", "Fixed pitch")),
    )
    equalize_card_heights(pitch, key_range)

    level = build_section_card(
        "Level & Pan",
        knobs([k("sample_level", "Level"), k("pan", "Pan"), k("velocity_sensitivity", "Velocity")]),
        row(s("level_key_scaling_break_1", "Scale break 1")),
        row(s("level_key_scaling_break_2", "Scale break 2")),
        row(s("level_key_scaling_level_1", "Scale level 1")),
        row(s("level_key_scaling_level_2", "Scale level 2")),
    )
    loop = build_section_card(
        "Loop & Wave",
        row(c("loop_mode", "Loop mode")),
        row(t("loop_tempo", "Loop tempo", format_tempo)),
        row(t("sampling_frequency_l", "Sample rate", format_hz)),
        row(t("wave_start_address", "Wave start", format_address)),
        row(t("wave_length", "Wave length", format_address)),
        row(t("loop_start_address", "Loop start", format_address)),
        row(t("loop_length", "Loop length", format_address)),
        row(t("loop_end_address", "Loop end", format_address)),
    )
    equalize_card_heights(level, loop)

    filt = build_section_card(
        "Filter",
        row(c("filter_type", "Type")),
        knobs([k("filter_cutoff", "Cutoff"), k("filter_q", "Q / Width"), k("filter_gain", "Gain")]),
        knobs([k("cutoff_distance", "Distance"), k("cutoff_velocity_sensitivity", "Cutoff vel"), k("q_velocity_sensitivity", "Q vel")]),
        row(s("cutoff_key_scaling_break_1", "Scale break 1")),
        row(s("cutoff_key_scaling_break_2", "Scale break 2")),
        row(s("cutoff_key_scaling_level_1", "Scale level 1")),
        row(s("cutoff_key_scaling_level_2", "Scale level 2")),
    )
    feg = build_section_card(
        "Filter Envelope",
        knobs([k("feg_attack_rate", "Attack"), k("feg_decay_rate", "Decay"), k("feg_release_rate", "Release")]),
        knobs([k("feg_init_level", "Init"), k("feg_attack_level", "Att lvl"), k("feg_sustain_level", "Sustain"), k("feg_release_level", "Rel lvl")]),
        knobs([k("feg_rate_key_scaling", "Key scale"), k("feg_rate_velocity_sensitivity", "Vel rate"), k("feg_attack_level_velocity_sensitivity", "Vel att"), k("feg_level_velocity_sensitivity", "Vel lvl")]),
    )
    equalize_card_heights(filt, feg)

    aeg = build_section_card(
        "Amplitude Envelope",
        row(c("aeg_attack_mode", "Attack mode")),
        knobs([k("aeg_attack_rate", "Attack"), k("aeg_decay_rate", "Decay"), k("aeg_sustain_level", "Sustain"), k("aeg_release_rate", "Release")]),
        knobs([k("aeg_rate_key_scaling", "Key scale"), k("aeg_rate_velocity_sensitivity", "Vel rate")]),
    )
    peg = build_section_card(
        "Pitch Envelope",
        knobs([k("peg_attack_rate", "Attack"), k("peg_decay_rate", "Decay"), k("peg_release_rate", "Release")]),
        knobs([k("peg_init_level", "Init"), k("peg_attack_level", "Att lvl"), k("peg_sustain_level", "Sustain"), k("peg_release_level", "Rel lvl")]),
        knobs([k("peg_range", "Range"), k("peg_rate_key_scaling", "Key scale"), k("peg_rate_velocity_sensitivity", "Vel rate"), k("peg_level_velocity_sensitivity", "Vel lvl")]),
    )
    equalize_card_heights(aeg, peg)

    lfo = build_section_card(
        "LFO",
        row(c("lfo_wave", "Wave")),
        knobs([k("lfo_speed", "Speed"), k("lfo_delay_time", "Delay")]),
        knobs([k("cutoff_mod_depth", "Cutoff"), k("pitch_mod_depth", "Pitch"), k("amplitude_mod_depth", "Amp")]),
        row(ck("lfo_sync_on", "Key sync")),
        row(ck("lfo_cutoff_mod_phase_invert", "Invert cutoff")),
        row(ck("lfo_pitch_mod_phase_invert", "Invert pitch")),
    )
    eq = build_section_card(
        "EQ",
        row(s("eq_type", "Type")),
        row(s("eq_frequency", "Frequency")),
        row(s("eq_gain", "Gain")),
        row(s("eq_width", "Width")),
    )
    equalize_card_heights(lfo, eq)

    out = build_section_card(
        "Output",
        row(c("output1", "Output 1")),
        row(c("output2", "Output 2")),
        knobs([k("output1_level", "Out 1 level"), k("output2_level", "Out 2 level")]),
    )
    controls = _controls_card(panel)
    equalize_card_heights(out, controls)

    layout = QVBoxLayout()
    style_card_page_layout(layout)
    for left, right in ((pitch, key_range), (level, loop), (filt, feg), (aeg, peg), (lfo, eq), (out, controls)):
        layout.addLayout(build_paired_row(left, right))
    layout.addStretch()
    page = QWidget()
    page.setLayout(layout)
    return page


def _controls_card(panel):
    """The six sample controllers (device / function / type / range) as a small grid."""
    grid = QGridLayout()
    grid.setHorizontalSpacing(10)
    grid.setVerticalSpacing(4)
    for column, title in enumerate(("", "Device", "Function", "Type", "Range")):
        grid.addWidget(QLabel(f"<b>{title}</b>" if title else ""), 0, column)
    for n in range(1, 7):
        grid.addWidget(QLabel(f"Control {n}"), n, 0)
        for column, part in enumerate(("device", "function", "type", "range"), start=1):
            grid.addWidget(panel.widget(Field(f"control{n}_{part}", f"Control {n} {part}", "spin")), n, column)
    wrapper = QHBoxLayout()
    wrapper.addLayout(grid)
    wrapper.addStretch()
    return build_section_card("Controllers", wrapper)


# -- the tab ---------------------------------------------------------------------------------------------


class YamahaSamplesTab(QWidget):
    #: a short message for the host window's status bar
    status_message = Signal(str)
    #: the user asked to jump to a program that uses the sample
    sample_selected = Signal(str)

    def __init__(self, controller, session, sample_cache, writer=None, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._session = session
        self._writer = writer  # None = view only
        self._cache = sample_cache  # {sample name: bulk payload} - shared with the Programs tab
        self._names = []
        self._selected = None
        self._token = 0  # bumped per selection so a late dump for an older one is ignored
        # audio: one load at a time, shown only if its sample is still selected when it lands
        self._loading_name = None  # the sample whose waves are being fetched (None = no load running)
        self._load_token = 0  # bumped per load and per cancel so a late callback from an abandoned one is ignored
        self._audio_name = None
        self._audio_samples = None  # the left (or only) channel of the sample whose audio is loaded
        self._audio_samples_right = None
        self._syncing = False
        self.panel = FieldPanel("sample")
        self._build()
        if writer is not None:
            self.panel.set_editable(True)
            self.panel.edited.connect(self._on_edited)
        self._connected = True

    def _build(self):
        self.sample_list_widget = QListWidget()
        self.sample_list_widget.setObjectName("sampleList")
        self.sample_list_widget.setFixedWidth(200)
        self.sample_list_widget.currentItemChanged.connect(self._on_selected)
        samples_container = build_list_column("Samples", self.sample_list_widget)

        # the left (or only) channel; a stereo sample adds the right one below it, both at half height
        self.waveform_view = WaveformView()
        self.waveform_view_right = WaveformView()
        for view in (self.waveform_view, self.waveform_view_right):
            view.set_markers_locked(True)
            view.set_placeholder_text("Select a sample on the left")
            view.load_requested.connect(self._load_audio)
        self.waveform_view_right.setVisible(False)
        self.waveform_view.view_changed.connect(lambda *_a: self._follow(self.waveform_view, self.waveform_view_right))
        self.waveform_view_right.view_changed.connect(lambda *_a: self._follow(self.waveform_view_right, self.waveform_view))
        zoom_out = QPushButton("-")
        zoom_out.setFixedWidth(36)
        zoom_out.setToolTip(tt.SAMPLE_ZOOM_OUT)
        zoom_out.clicked.connect(self.waveform_view.zoom_out)
        zoom_in = QPushButton("+")
        zoom_in.setFixedWidth(36)
        zoom_in.setToolTip(tt.SAMPLE_ZOOM_IN)
        zoom_in.clicked.connect(self.waveform_view.zoom_in)
        zoom_fit = QPushButton("Fit")
        zoom_fit.setToolTip(tt.SAMPLE_ZOOM_FIT)
        zoom_fit.clicked.connect(self.waveform_view.reset_zoom)
        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(6)
        zoom_row.addWidget(QLabel("Zoom"))
        zoom_row.addWidget(zoom_out)
        zoom_row.addWidget(zoom_in)
        zoom_row.addWidget(zoom_fit)
        zoom_row.addStretch()
        self.cancel_load_button = QPushButton("Cancel load")
        self.cancel_load_button.setToolTip("Stop receiving this sample's audio (a long sample takes minutes at MIDI speed)")
        self.cancel_load_button.clicked.connect(self._cancel_load)
        self.cancel_load_button.setVisible(False)
        zoom_row.addWidget(self.cancel_load_button)
        scrollbar_container, self.waveform_scrollbar = build_waveform_scrollbar()
        self.waveform_scrollbar.valueChanged.connect(self.waveform_view.set_view_start)  # the right view follows
        self.waveform_view.view_changed.connect(
            lambda start, length, count: sync_waveform_scrollbar(self.waveform_scrollbar, start, length, count)
        )

        self.name_label = QLabel("")
        self.name_label.setObjectName("sectionHeader")
        self.used_label = QLabel("")
        self.used_label.setObjectName("mutedLabel")
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("mutedLabel")
        header = QVBoxLayout()
        header.setSpacing(2)
        header.addWidget(self.name_label)
        header.addWidget(self.summary_label)
        header.addWidget(self.used_label)

        self.waveform_hint = QLabel(_LOAD_HINT)
        self.waveform_hint.setObjectName("mutedLabel")
        waveform_card = build_section_card(
            "Waveform",
            zoom_row,
            self._row(self.waveform_view),
            self._row(self.waveform_view_right),
            self._row(scrollbar_container),
            self._row(self.waveform_hint),
        )
        self.cards_page = build_sample_cards(self.panel)

        cards_layout = QVBoxLayout()
        style_card_page_layout(cards_layout)
        cards_layout.addLayout(header)
        cards_layout.addWidget(waveform_card)
        cards_layout.addWidget(self.cards_page)
        cards_container = QWidget()
        cards_container.setLayout(cards_layout)
        self.cards_scroll = build_scroll_area(cards_container)

        self.placeholder = QLabel("Select a sample on the left")
        self.placeholder.setObjectName("emptyQueueLabel")
        self.placeholder.setWordWrap(True)
        self.cards_scroll.setVisible(False)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.placeholder, 1)
        right.addWidget(self.cards_scroll, 1)
        right_container = QWidget()
        right_container.setLayout(right)
        page = build_samples_page(samples_container, right_container)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)

    @staticmethod
    def _row(widget):
        row = QHBoxLayout()
        row.addWidget(widget)
        return row

    # -- the list -----------------------------------------------------------------------------------------

    def set_samples(self, names):
        """The unit's sample names, in object-list order."""
        previous = self._selected
        self._names = list(names)
        self.sample_list_widget.blockSignals(True)
        self.sample_list_widget.clear()
        for name in self._names:
            QListWidgetItem(name, self.sample_list_widget)
        self.sample_list_widget.blockSignals(False)
        if previous in self._names:
            self.select_sample(previous)
        else:
            self._selected = None
            self._show_placeholder("Select a sample on the left" if self._names else "No samples on the unit")

    def select_sample(self, name):
        if name in self._names:
            self.sample_list_widget.setCurrentRow(self._names.index(name))

    def _follow(self, source, target):
        """Keep the two channel views on the same stretch of the wave (zoom and pan)."""
        if self._syncing or not target.isVisibleTo(self) or target.frame_count() != source.frame_count():
            return
        self._syncing = True
        try:
            target.set_view_state(*source.view_state())
        finally:
            self._syncing = False

    def _show_placeholder(self, text):
        self.waveform_view.clear()
        self.waveform_view_right.clear()
        self.placeholder.setText(text)
        self.placeholder.setVisible(True)
        self.cards_scroll.setVisible(False)

    def _on_selected(self, current, _previous):
        if current is None:
            return
        name = current.text()
        self._selected = name
        self._token += 1
        token = self._token
        _log(f"sample selected: {name!r}")
        if name in self._cache:
            self._show(name)
            return
        self._show_placeholder(f"Reading {name!r} from the sampler...")
        self._session.request_bulk("SP", name, lambda dump, t=token, n=name: self._on_dump(t, n, dump))

    def _on_dump(self, token, name, dump):
        if dump is None:
            if token == self._token:
                self._show_placeholder(f"Couldn't read {name!r} from the sampler")
                self.status_message.emit(f"Couldn't read sample {name!r}")
            return
        self._cache[name] = bytearray(dump.data)
        if token == self._token:
            self._show(name)

    def _show(self, name):
        data = self._cache[name]
        self._show_values(name)
        self.placeholder.setVisible(False)
        self.cards_scroll.setVisible(True)
        self._show_waveform(name, data)

    def _show_values(self, name):
        """The cards and the header, from the cached payload (the waveform is left alone)."""
        data = self._cache[name]
        self.panel.fill(data)
        rate = yp.extract(yp.get("sample", "sampling_frequency_l"), data)
        frames = yp.extract(yp.get("sample", "wave_length"), data)
        self.name_label.setText(name)
        summary = f"{format_hz(rate)}, {frames:,} frames ({format_duration(frames, rate)})"
        if yp.is_stereo(data):
            summary += " - stereo"
        self.summary_label.setText(summary)
        programs = yp.linked_programs(data)
        used = format_used_in(programs)
        if programs and self._writer is not None:
            used += " - a change here affects all of them"
        self.used_label.setText(used)

    # -- editing -------------------------------------------------------------------------------------------

    def _on_edited(self, key, value):
        name = self._selected
        if name is None or name not in self._cache:
            return
        row = yp.get("sample", key)
        done = lambda result, n=name, r=row: self._on_write_done(n, r, result)  # noqa: E731
        if not self._writer.edit(row, value, name, None, done):
            self._show_values(name)  # declined the warning: put the widget back
            return
        yp.store(row, self._cache[name], value)  # shown at once; the read-back confirms it
        self._after_cache_change(name, row)

    def _on_write_done(self, name, row, result):
        if not self._connected or name not in self._cache:
            return
        if result.readback is not None:
            yp.store(row, self._cache[name], result.readback)  # whatever the unit really holds
        if name == self._selected and not result.ok:
            self._show_values(name)
        self._after_cache_change(name, row)
        if result.edit_sent:
            self._writer.schedule_reread("SP", name, lambda dump, n=name: self._on_reread(n, dump))

    def _on_reread(self, name, dump):
        if not self._connected or dump is None:
            return
        self._cache[name] = bytearray(dump.data)
        if name == self._selected:
            self._show_values(name)
            if self._loading_name != name:  # never reset the header under a wave that is still arriving
                self._show_waveform(name, self._cache[name])

    def _after_cache_change(self, name, row):
        # the loop mode decides whether the loop markers are shown
        if name == self._selected and row.key == "loop_mode" and self._loading_name != name:
            self._show_waveform(name, self._cache[name])

    def _show_waveform(self, name, data):
        views = (self.waveform_view, self.waveform_view_right)
        frames, start, loop_start, loop_end, end, loops = sample_markers(data)
        stereo = yp.is_stereo(data)
        self.waveform_view_right.setVisible(stereo)
        # one view at full height, or two at half height each - the waveform area keeps the same height either way
        for view in views:
            view.set_view_height(_STEREO_VIEW_HEIGHT if stereo else _MONO_VIEW_HEIGHT)
        if frames <= 0:
            for view in views:
                view.clear()
                view.set_placeholder_text("This sample is empty")
            return
        loaded = name == self._audio_name and self._audio_samples is not None
        for view, samples in ((self.waveform_view, self._audio_samples), (self.waveform_view_right, self._audio_samples_right)):
            view.set_loop_enabled(loops)
            if loaded and samples is not None:
                view.set_waveform(samples, start, loop_start, loop_end, end)
            else:
                view.set_header(frames, start, loop_start, loop_end, end)
        self.waveform_hint.setText(
            "The markers are shown for reference - they can't be edited yet." if loaded else _LOAD_HINT
        )

    def set_active(self, active):
        # nothing runs in the background here; kept so the window can treat both tabs alike
        self._active = active

    def refresh(self):
        """Forget the audio read so far (the host is about to re-read everything)."""
        self._load_token += 1  # a load in flight is abandoned (the host cancels the session)
        self._loading_name = None
        self.cancel_load_button.setVisible(False)
        self._audio_name = self._audio_samples = self._audio_samples_right = None

    # -- loading audio (the native wave dump) ------------------------------------------------------------------

    def _load_audio(self):
        name = self._selected
        if name is None or name not in self._cache or self._loading_name is not None:
            return
        data = self._cache[name]
        waves = [bytes(data[yp.WAVE_NAME_L_OFFSET : yp.WAVE_NAME_L_OFFSET + 16]).decode("ascii", "replace").strip(" \x00")]
        if yp.is_stereo(data):
            waves.append(yp.wave_name_right(data))
        if not waves[0]:
            self.status_message.emit(f"{name!r} has no wave to load")
            return
        self._loading_name = name
        self._load_token += 1
        token = self._load_token
        views = (self.waveform_view, self.waveform_view_right)
        for view in views[: len(waves)]:
            view.set_loading(True)
            view.begin_live_capture()  # the wave draws itself in as it arrives
        self.cancel_load_button.setVisible(True)
        self.waveform_hint.setText("Receiving the wave - it fills in as it arrives. Cancel stops waiting for it.")
        _log(f"loading audio of {name!r}: wave object(s) {waves}")
        self.status_message.emit(f"Receiving {name!r}...")
        self._load_wave(token, name, waves, [])

    def _load_wave(self, token, name, waves, got):
        index = len(got)
        view = (self.waveform_view, self.waveform_view_right)[index]
        side = "" if len(waves) == 1 else (" left" if index == 0 else " right")
        progress = {"frames": 0}

        def chunk(new, total):
            if token != self._load_token:
                return
            progress["frames"] += len(new)
            view.append_live_samples(list(new))
            self.status_message.emit(f"Receiving {name!r}{side}: {100 * progress['frames'] // max(total, 1)}%")

        self._session.request_wave(
            waves[index], lambda frames: self._on_wave(token, name, waves, got, frames), on_chunk=chunk
        )

    def _on_wave(self, token, name, waves, got, frames):
        if token != self._load_token or not self._connected:
            return
        if frames is None:
            self._finish_load()
            if name in self._cache:
                self._show_waveform(name, self._cache[name])  # back to the header-only view
            self.status_message.emit(f"Couldn't load the audio of {name!r}")
            return
        got = got + [frames]
        if len(got) < len(waves):
            self._load_wave(token, name, waves, got)
            return
        self._finish_load()
        data = self._cache.get(name)
        if data is None or not audio_matches(data, len(got[0])):
            _log(f"audio for {name!r} REFUSED: {len(got[0])} frames does not match its parameters")
            self.status_message.emit(
                f"The sampler sent {len(got[0]):,} frames for {name!r}, which doesn't match its parameters - not shown"
            )
            if name == self._selected and data is not None:
                self._show_waveform(name, data)
            return
        self._audio_name, self._audio_samples = name, got[0]
        self._audio_samples_right = got[1] if len(got) > 1 else None
        self.status_message.emit(f"Loaded {name!r} ({len(got[0]):,} frames{', stereo' if len(got) > 1 else ''})")
        if name == self._selected:
            self._show_waveform(name, data)

    def _cancel_load(self):
        if self._loading_name is None:
            return
        name = self._loading_name
        _log(f"audio load of {name!r} cancelled by the user")
        self._load_token += 1  # whatever is still in flight is ignored
        # a wave dump can't be aborted on the unit: the session stays busy until its stream has gone quiet
        self._session.cancel()
        self._finish_load()
        if name == self._selected and name in self._cache:
            self._show_waveform(name, self._cache[name])  # back to the header-only view
        self.status_message.emit(f"Cancelled loading {name!r}")

    def _finish_load(self):
        self._loading_name = None
        for view in (self.waveform_view, self.waveform_view_right):
            view.set_loading(False)
        self.cancel_load_button.setVisible(False)
        self.waveform_hint.setText(_LOAD_HINT)

    @property
    def loading(self):
        """True while the audio of a sample is being received."""
        return self._loading_name is not None

    def disconnect_controller(self):
        """The host window is closing: abandon a running load (the session drains its stream)."""
        if not self._connected:
            return
        self._connected = False
        if self._loading_name is not None:
            self._load_token += 1
            self._session.cancel()
            self._loading_name = None
