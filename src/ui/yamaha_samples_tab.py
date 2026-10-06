"""The Samples tab of the Yamaha A4000 editor - VIEW-ONLY in this first release.

Laid out like the S3000/S950 editors' Samples tab (a sample list on the left, a waveform card and a column
of parameter cards on the right, from the same `ui/editor_layout.py` pieces), but for the A4000's data
model: a SAMPLE owns the whole sound - key range, tuning, loop, filter, the three envelopes, LFO, EQ,
outputs, controllers - and is shared by every program that plays it. So the header says which programs
use the selected sample ("Used in programs ...").

One bulk dump (`SP`) per sample fills every card (`FieldPanel.fill`); dumps are cached for the editor's
session in `sample_cache` (shared with the Programs tab, which needs each sample's key range).

Not editable yet: the wave/loop start-length-end addresses are shown as text only (they are coupled -
writing one moves others - see dev_docs/a4000-editor-roadmap.md), as are the sampling frequency, wave
length and wave end (the unit ignored writes to them).

THE WAVEFORM comes over plain Sample Dump Standard through the Dashboard's `SamplerController`
(`receive_sample_generic`), on request (double-click the waveform - a long sample takes a while at MIDI
speed). MEASURED on a real A4000: an SDS dump request's number is the sample's POSITION in the sample list
(0-based): the seven factory waveforms are 0-6 and a sample sent later came back as 7, not as the number it
was sent as (100) and not as the number in its name ("MIDI 00101"); a number with no sample gets a CANCEL.
Because that is measured only on a unit with no deleted samples, every received dump is CHECKED against the
sample's own parameters (frames == wave length or wave end, rate within 2 Hz) and refused if it doesn't
match, rather than showing the wrong sample's audio.
"""

import os
import tempfile

from PySide6.QtCore import QTimer, Signal
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

from core import debug_log, sds_encoder
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
#: SDS rounds the sample period to whole nanoseconds, so 48000 Hz comes back as 48001
RATE_TOLERANCE_HZ = 2


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


def audio_matches(sample_data, frames, rate):
    """Whether a received dump of `frames` frames at `rate` Hz is plausibly THIS sample (see the module docstring)."""
    g = lambda key: yp.extract(yp.get("sample", key), sample_data)  # noqa: E731
    return frames in (g("wave_length"), g("wave_end_address")) and abs(rate - g("sampling_frequency_l")) <= RATE_TOLERANCE_HZ


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

    _RETRY_MS = 150
    _AUDIO_RETRIES = 40  # x _RETRY_MS: how long a load waits for the session to go idle

    def __init__(self, controller, session, sample_cache, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._session = session
        self._cache = sample_cache  # {sample name: bulk payload} - shared with the Programs tab
        self._names = []
        self._selected = None
        self._token = 0  # bumped per selection so a late dump for an older one is ignored
        # audio: one load at a time, shown only if its sample is still selected when it lands
        self._wave_name = None
        self._wave_path = None
        self._audio_name = None
        self._audio_samples = None
        self.panel = FieldPanel("sample")
        self._build()
        self._connected = True
        controller.sample_received.connect(self._on_audio_file)
        controller.receive_finished.connect(self._on_receive_finished)
        controller.receive_progress.connect(self._on_receive_progress)

    def _build(self):
        self.sample_list_widget = QListWidget()
        self.sample_list_widget.setObjectName("sampleList")
        self.sample_list_widget.setFixedWidth(200)
        self.sample_list_widget.currentItemChanged.connect(self._on_selected)
        samples_container = build_list_column("Samples", self.sample_list_widget)

        self.waveform_view = WaveformView()
        self.waveform_view.set_markers_locked(True)
        self.waveform_view.set_placeholder_text("Select a sample on the left")
        self.waveform_view.load_requested.connect(self._load_audio)
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
        scrollbar_container, self.waveform_scrollbar = build_waveform_scrollbar()
        self.waveform_scrollbar.valueChanged.connect(self.waveform_view.set_view_start)
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
            "Waveform", zoom_row, self._row(self.waveform_view), self._row(scrollbar_container), self._row(self.waveform_hint)
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

    def _show_placeholder(self, text):
        self.waveform_view.clear()
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
        self._cache[name] = bytes(dump.data)
        if token == self._token:
            self._show(name)

    def _show(self, name):
        data = self._cache[name]
        self.panel.fill(data)
        rate = yp.extract(yp.get("sample", "sampling_frequency_l"), data)
        frames = yp.extract(yp.get("sample", "wave_length"), data)
        self.name_label.setText(name)
        self.summary_label.setText(f"{format_hz(rate)}, {frames:,} frames ({format_duration(frames, rate)})")
        self.used_label.setText(format_used_in(yp.linked_programs(data)))
        self.placeholder.setVisible(False)
        self.cards_scroll.setVisible(True)
        self._show_waveform(name, data)

    def _show_waveform(self, name, data):
        view = self.waveform_view
        frames, start, loop_start, loop_end, end, loops = sample_markers(data)
        if frames <= 0:
            view.clear()
            view.set_placeholder_text("This sample is empty")
            return
        view.set_loop_enabled(loops)
        if name == self._audio_name and self._audio_samples is not None:
            view.set_waveform(self._audio_samples, start, loop_start, loop_end, end)
            self.waveform_hint.setText("The markers are shown for reference - they can't be edited yet.")
        else:
            view.set_header(frames, start, loop_start, loop_end, end)
            self.waveform_hint.setText(_LOAD_HINT)

    def set_active(self, active):
        # nothing runs in the background here; kept so the window can treat both tabs alike
        self._active = active

    def refresh(self):
        """Forget the audio read so far (the host is about to re-read everything)."""
        self._audio_name = self._audio_samples = None

    # -- loading audio (Sample Dump Standard) --------------------------------------------------------------

    def _load_audio(self, attempt=0):
        name = self._selected
        row = self.sample_list_widget.currentRow()
        if name is None or row < 0 or name not in self._cache or self._wave_path is not None:
            return
        if self._controller.is_transfer_busy() or not self._session.idle:
            # the wire is in use (a transfer, or a read of the program list): try again shortly
            if attempt < self._AUDIO_RETRIES:
                QTimer.singleShot(self._RETRY_MS, lambda: self._load_audio(attempt + 1))
            else:
                self.status_message.emit("The sampler is busy - try loading the waveform again")
            return
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="akaisds-a4000-")
        os.close(fd)
        self._wave_name, self._wave_path = name, path
        self.waveform_view.set_loading(True)
        _log(f"loading audio of {name!r} as SDS number {row}")
        self.status_message.emit(f"Receiving {name!r}...")
        # the SDS number is the sample's POSITION in the list (measured - see the module docstring)
        self._controller.receive_sample_generic(row, path)

    def _on_receive_progress(self, received, total):
        if self._wave_path is not None and total:
            self.status_message.emit(f"Receiving {self._wave_name!r}: {int(100 * received / total)}%")

    def _on_audio_file(self, path):
        if path != self._wave_path:
            return  # a receive the Dashboard (or another window) asked for
        name = self._wave_name
        try:
            channels, rate = sds_encoder.read_wav_channels(path)
            samples = list(channels[0])
        except Exception as e:
            debug_log.get_logger().warning(f"YamahaSamplesTab: couldn't read the received audio of {name!r}: {e!r}")
            self.status_message.emit(f"Couldn't read the received audio: {e}")
            self._finish_load()
            return
        self._finish_load()
        data = self._cache.get(name)
        if data is None or not audio_matches(data, len(samples), rate):
            _log(f"audio for {name!r} REFUSED: {len(samples)} frames at {rate} Hz does not match its parameters")
            self.status_message.emit(
                f"The sampler sent a different sample than {name!r} ({len(samples):,} frames at {rate} Hz) - not shown"
            )
            return
        self._audio_name, self._audio_samples = name, samples
        # QTimer: the controller's own "Saved sample N to <temp file>" status arrives around now and would win
        QTimer.singleShot(0, lambda: self.status_message.emit(f"Loaded {name!r} ({len(samples):,} frames)"))
        if name == self._selected:
            self._show_waveform(name, data)

    def _on_receive_finished(self, completed):
        if self._wave_path is not None and not completed:
            self._finish_load()  # the controller already said why (timeout, cancel, ...)

    def _finish_load(self):
        self._discard_temp_file()
        self._wave_name = self._wave_path = None
        self.waveform_view.set_loading(False)

    def _discard_temp_file(self):
        if self._wave_path:
            try:
                os.remove(self._wave_path)
            except OSError:
                pass

    def disconnect_controller(self):
        if not self._connected:
            return
        self._connected = False
        c = self._controller
        for signal, slot in (
            (c.sample_received, self._on_audio_file),
            (c.receive_finished, self._on_receive_finished),
            (c.receive_progress, self._on_receive_progress),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._discard_temp_file()
