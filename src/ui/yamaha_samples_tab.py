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
program that uses it, so the header says so. The wave/loop addresses are shown as text; they are edited by DRAGGING THE
WAVEFORM'S MARKERS (below). The sampling frequency is shown only (the unit ignores writes to it).

MARKERS (wave start/end, loop start/end): dragging one writes the addresses that changed, one at a time and in the ORDER
core/yamaha_markers.py works out - the unit silently ignores a write that would put the loop outside the wave, and keeps the
two lengths itself - each through the same guarded write as any control, then re-reads the sample so what is shown is what
the unit holds. A sample's right channel has no markers of its own: the two views are linked, a drag in either moves both
(the unit keeps one set of addresses). On a built-in waveform the unit ignores a change of the wave END (said in the status
bar); everything else works there too.

PREVIEW: a single click on the waveform (once its audio is loaded) plays the sample through the computer's audio output, like
the S3000 editor: per the loop mode - a plain run from start to end, a looped one (held until the next click, or for a couple of
seconds for "loop to release"), or reversed - in real stereo for a stereo sample. Click again to stop; dragging a loop marker
while a looped preview plays moves the loop live.

THE WAVEFORM comes from the unit's native WAVE DATA bulk dump (core/yamaha_wave.py), on request (double-click the waveform):
the sample links a left wave object (and, for a STEREO sample, a right one), each fetched by its own name through the
session. Both channels are shown, stacked at half height each; the view fills in progressively as each ~4 KB message
lands. Faster than Sample Dump Standard (no per-packet handshake), reaches the right channel SDS can't, and kept working
on a unit whose SDS dumps stalled. A long wave takes a while at MIDI speed (~620 frames/s), so there is a Cancel button; a wave
dump can't be aborted on the unit, so the session drains the rest of its stream before its next request (see
controller/yamaha_session.py). The loaded audio is checked against the sample's own parameters (frames == wave length or
wave end) and refused if it doesn't match, rather than showing the wrong sample's audio.

SMOOTH FILL: the unit sends ~4 KB messages (one every ~1.5 s) and the OS hands each over only when complete, so frames really
do arrive in lumps. To draw a continuous growth instead, each chunk's frames wait in a queue and are released to the waveform ~30
times a second, at a pace that would just drain them by the time the next chunk is expected (estimated from the gaps measured so
far). It is display only - what is shown is always real, received data, slightly behind; the load completes (and the final
waveform lands) only once the queue has drained.
"""

import collections
import time

from PySide6.QtCore import Qt, QTimer, Signal
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
from core import yamaha_markers as ym
from core import yamaha_params as yp
from core.audio_preview import SlicePreviewPlayer
from ui import tooltips as tt
from ui.editor_layout import (
    build_centered_row,
    build_list_column,
    build_paired_row,
    build_sample_list_row_widget,
    equalize_card_heights,
    build_samples_page,
    build_waveform_scrollbar,
    style_card_page_layout,
    sync_waveform_scrollbar,
)
from ui.envelope_graph import ADSREnvelopeGraph, LevelEnvelopeGraph, yamaha_adsr_values
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


def format_list_duration(frames, rate):
    """'0.45s' - the grey duration at the right of a sample list row (the same format the S3000/S950 lists use)."""
    return f"{frames / rate:.2f}s" if rate > 0 else ""


#: loop modes that actually loop (owner's manual p.123): continuous loop and loop-to-release
_LOOPING_MODES = (1, 2)
#: loop modes that play the wave backwards (3 = reverse, 5 = reverse one-shot)
_REVERSE_MODES = (3, 5)
#: said INSIDE the waveform (once, centered across a stereo pair) - the label under it stays empty until it has more to say
_LOAD_HINT = ""
_WAVEFORM_HINT = "Double-click to load the audio waveform"
#: how long a "loop to release" sample's loop is held in a preview (there is no key to release)
_RELEASE_PREVIEW_MS = 2000
#: the waveform area is 180 px tall: one mono view, or a stereo pair at half height each
_GRAPH_SIZE = (200, 80)  # the envelope graphs (the S3000 editor's are 200 x 90)
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
    """Whether a received wave of `frames` frames is plausibly THIS sample's (see the module docstring): it must hold at least
    the sample's end address (the end can be pulled in from the wave's own end, so equality would refuse a trimmed sample)."""
    g = lambda key: yp.extract(yp.get("sample", key), sample_data)  # noqa: E731
    return frames > 0 and (frames == g("wave_length") or frames >= g("wave_end_address"))


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
    # read-only values (the wave/loop addresses, the sample rate): grey, so they read differently from the ones you can edit
    return Field(key, label, "text", fmt=fmt, muted=True)


def build_sample_cards(panel):
    """The sample parameter cards, as (title, rows) built through `panel` - returns the page's layout."""
    k, s, c, ck, t = _knob, _spin, _combo, _check, _text
    note = lambda key, label, specials=None: Field(key, label, "note", specials=specials)  # noqa: E731
    row = panel.labeled_row
    knobs = panel.knob_row

    pitch = build_section_card(
        "Pitch",
        row(note("original_key_l", "Original key")),
        knobs([k("coarse_tune", "Coarse"), k("fine_tune_l", "Fine")]),
        row(s("detune", "Detune")),
        row(s("random_pitch", "Random pitch")),
        row(s("pitch_bend_range", "Bend range")),
        row(s("pitch_bend_type", "Bend type")),
    )
    key_range = build_section_card(
        "Key & Velocity Range",
        row(note("key_range_low", "Key low", _SPECIAL_ORIGINAL)),
        row(note("key_range_high", "Key high", _SPECIAL_ORIGINAL)),
        row(s("velocity_range_low", "Velocity low")),
        row(s("velocity_range_high", "Velocity high")),
        row(s("velocity_low_limit", "Velocity limit")),
        row(s("alternate_group", "Alternate group")),
        row(ck("mono_mode", "Mono mode")),
        row(ck("fixed_pitch_on", "Fixed pitch")),
    )

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
    graphs = panel.envelope_graphs = {
        "aeg": ADSREnvelopeGraph(),
        "feg": LevelEnvelopeGraph("#d97757"),  # theme-independent colours, like the S3000's graphs
        "peg": LevelEnvelopeGraph("#5aa9e6"),
    }
    for graph in graphs.values():
        graph.setFixedSize(*_GRAPH_SIZE)
    feg = build_section_card(
        "Filter Envelope",
        build_centered_row(graphs["feg"]),
        knobs([k("feg_attack_rate", "Attack"), k("feg_decay_rate", "Decay"), k("feg_release_rate", "Release")]),
        knobs([k("feg_init_level", "Init"), k("feg_attack_level", "Att lvl"), k("feg_sustain_level", "Sustain"), k("feg_release_level", "Rel lvl")]),
        knobs([k("feg_rate_key_scaling", "Key scale"), k("feg_rate_velocity_sensitivity", "Vel rate"), k("feg_attack_level_velocity_sensitivity", "Vel att"), k("feg_level_velocity_sensitivity", "Vel lvl")]),
    )

    aeg = build_section_card(
        "Amplitude Envelope",
        row(c("aeg_attack_mode", "Attack mode")),
        build_centered_row(graphs["aeg"]),
        knobs([k("aeg_attack_rate", "Attack"), k("aeg_decay_rate", "Decay"), k("aeg_sustain_level", "Sustain"), k("aeg_release_rate", "Release")]),
        knobs([k("aeg_rate_key_scaling", "Key scale"), k("aeg_rate_velocity_sensitivity", "Vel rate")]),
    )
    peg = build_section_card(
        "Pitch Envelope",
        build_centered_row(graphs["peg"]),
        knobs([k("peg_attack_rate", "Attack"), k("peg_decay_rate", "Decay"), k("peg_release_rate", "Release")]),
        knobs([k("peg_init_level", "Init"), k("peg_attack_level", "Att lvl"), k("peg_sustain_level", "Sustain"), k("peg_release_level", "Rel lvl")]),
        knobs([k("peg_range", "Range"), k("peg_rate_key_scaling", "Key scale"), k("peg_rate_velocity_sensitivity", "Vel rate"), k("peg_level_velocity_sensitivity", "Vel lvl")]),
    )

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

    out = build_section_card(
        "Output",
        row(c("output1", "Output 1")),
        row(c("output2", "Output 2")),
        knobs([k("output1_level", "Out 1 level"), k("output2_level", "Out 2 level")]),
    )
    controls = _controls_card(panel)

    # Side-by-side pairs, each pinned to equal heights so the two cards line up top and bottom. The pairs are chosen so the
    # cards in a row are about the same height (the least empty space inside a card): Pitch/Key, Level/Loop, Filter/Filter
    # Envelope, the two other envelopes, then LFO/Controllers and Output/EQ.
    layout = QVBoxLayout()
    style_card_page_layout(layout)
    # the page of cards sits inside a container that already applies the scroll-bar clearance on the right (the waveform card is
    # in it too): a second one here made every card 8 px narrower than the waveform card
    layout.setContentsMargins(0, 0, 0, 0)
    for left, right in ((pitch, key_range), (level, loop), (filt, feg), (aeg, peg), (lfo, controls), (out, eq)):
        equalize_card_heights(left, right)
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
    _SCAN_RETRY_MS = 400  # how often the duration scan checks whether the wire is free
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
        self._syncing_markers = False  # a mirrored marker move is in progress (never echo it back)
        self._shown_markers = None  # (start, loop_start, loop_end, end) frames as last shown/committed - what a drag starts from
        self._marker_job = None  # the marker write in progress: {"name", "steps", "index"}
        self._marker_pending = None  # the newest drag that arrived meanwhile: (sample name, view markers)
        self._preview = SlicePreviewPlayer(self)
        self._preview_reversed = 0  # frames in the reversed copy being played (0 = a forward preview)
        self._rows = {}  # sample name -> the duration label of its list row
        self._scan_token = 0  # bumped by every refresh/list change so a late scan result is dropped
        self._scan_failed = set()
        # smooth reveal of arriving chunks (see the module docstring)
        self._reveal_queue = collections.deque()  # [view, frames, offset] segments, oldest first
        self._reveal_pending = 0  # frames queued
        self._reveal_speed = 0.0  # frames per second
        self._reveal_interval_s = self._REVEAL_INITIAL_INTERVAL_S  # estimated gap between chunks (refined from the real ones)
        self._reveal_last_chunk = None
        self._reveal_last_tick = None
        self._reveal_when_done = None
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self._reveal_tick)
        self.panel = FieldPanel("sample")
        self._build()
        if writer is not None:
            self.panel.set_editable(True)
            self.panel.edited.connect(self._on_edited)
        self._preview.position_changed.connect(self._on_preview_position)
        self._preview.finished.connect(self._clear_playheads)
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
            view.set_markers_locked(self._writer is None)
            view.set_placeholder_text("Select a sample on the left")
            view.set_header_hint(_WAVEFORM_HINT)
            view.load_requested.connect(self._load_audio)
            view.preview_requested.connect(self._on_preview_requested)
            view.markers_changed.connect(lambda *m, v=view: self._on_markers_changed(v, m))
            view.marker_committed.connect(lambda which, *m, v=view: self._on_marker_committed(v, which, m))
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
        self.waveform_hint.setVisible(bool(_LOAD_HINT))
        # the two channels touch (no spacing) and draw as ONE display - see WaveformView.set_stack_position
        self.channel_stack = QVBoxLayout()
        channel_stack = self.channel_stack
        channel_stack.setSpacing(0)
        channel_stack.setContentsMargins(0, 0, 0, 0)
        channel_stack.addWidget(self.waveform_view)
        channel_stack.addWidget(self.waveform_view_right)
        waveform_card = build_section_card(
            "Waveform",
            zoom_row,
            channel_stack,
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
        self._rows = {}
        for name in self._names:
            # a row widget (name left, grey duration right - the S3000 editor's list), so the ITEM gets no text of its own
            # (it would double-paint - AGENTS.md); the name lives in the item's data
            item = QListWidgetItem(self.sample_list_widget)
            item.setData(Qt.ItemDataRole.UserRole, name)
            row_widget, name_label, duration_label = build_sample_list_row_widget(name)
            item.setSizeHint(row_widget.sizeHint())
            self.sample_list_widget.setItemWidget(item, row_widget)
            self._rows[name] = duration_label
            self._show_duration(name)
        self.sample_list_widget.blockSignals(False)
        if previous in self._names:
            self.select_sample(previous)
        else:
            self._selected = None
            self._show_placeholder("Select a sample on the left" if self._names else "No samples on the unit")
        self._start_duration_scan()

    # -- the durations in the list ---------------------------------------------------------------------------

    def sample_names(self):
        """The names in the list, in order."""
        return [self.sample_list_widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.sample_list_widget.count())]

    def _show_duration(self, name):
        label, data = self._rows.get(name), self._cache.get(name)
        if label is not None and data is not None:
            frames = max(yp.extract(yp.get("sample", "wave_length"), data), 0)
            try:
                label.setText(format_list_duration(frames, yp.extract(yp.get("sample", "sampling_frequency_l"), data)))
            except RuntimeError:  # the row was deleted under us (the list was rebuilt or the window is going away)
                self._rows.pop(name, None)

    def reload_sample(self, name):
        """The host changed a sample on the unit behind our back (a restore): read it again and show what it holds now."""
        self._cache.pop(name, None)
        if name == self._selected and self.sample_list_widget.currentItem() is not None:
            self._on_selected(self.sample_list_widget.currentItem(), None)
        else:
            self._show_duration(name)

    def note_cached(self, name):
        """The host cached a sample's dump (the Programs tab needs each assigned sample's key range): show its duration."""
        self._show_duration(name)

    def _start_duration_scan(self):
        """Read the dump of every sample not yet read, one at a time and only while the wire is otherwise idle, so the
        list can show each one's length without ever getting in front of something the user asked for."""
        self._scan_token += 1
        self._scan_failed = set()
        self._scan_next(self._scan_token)

    def _scan_next(self, token):
        if token != self._scan_token or not self._connected:
            return
        for name in self._names:
            self._show_duration(name)
        todo = [n for n in self._names if n not in self._cache and n not in self._scan_failed]
        if not todo:
            return
        if not self._session.idle:
            QTimer.singleShot(self._SCAN_RETRY_MS, lambda: self._scan_next(token))
            return
        name = todo[0]
        self._session.request_bulk("SP", name, lambda dump, n=name: self._on_scan_dump(token, n, dump))

    def _on_scan_dump(self, token, name, dump):
        if token != self._scan_token or not self._connected:
            return
        if dump is None:
            self._scan_failed.add(name)  # never retried until the next refresh
        elif name not in self._cache:
            self._cache[name] = bytearray(dump.data)
        self._show_duration(name)
        QTimer.singleShot(0, lambda: self._scan_next(token))

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

    def _set_hint(self, text):
        """The line under the waveform; hidden while empty so it doesn't leave a blank row in the card."""
        self.waveform_hint.setText(text)
        self.waveform_hint.setVisible(bool(text))

    def _show_placeholder(self, text):
        self.waveform_view.clear()
        self.waveform_view_right.clear()
        self.placeholder.setText(text)
        self.placeholder.setVisible(True)
        self.cards_scroll.setVisible(False)

    def _on_selected(self, current, _previous):
        if current is None:
            return
        name = current.data(Qt.ItemDataRole.UserRole)
        self._preview.stop()
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
        self._show_duration(name)
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
        self._update_graphs()

    def _update_graphs(self):
        """Redraw the three envelope graphs from the panel's current widget values (shape only - no calibrated timing)."""
        v = self.panel.value
        graphs = self.panel.envelope_graphs
        graphs["aeg"].set_values(*yamaha_adsr_values(v("aeg_attack_rate"), v("aeg_decay_rate"), v("aeg_sustain_level"), v("aeg_release_rate")))
        for kind, prefix in (("feg", "feg"), ("peg", "peg")):
            graphs[kind].set_values(
                v(f"{prefix}_init_level"), v(f"{prefix}_attack_level"), v(f"{prefix}_sustain_level"), v(f"{prefix}_release_level"),
                v(f"{prefix}_attack_rate"), v(f"{prefix}_decay_rate"), v(f"{prefix}_release_rate"),
            )

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
        self._update_graphs()
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
            # never reset the header under a wave that is still arriving, nor the markers under a drag being written
            if self._loading_name != name and self._marker_job is None:
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
        self.waveform_view.set_stack_position("top" if stereo else None, _STEREO_VIEW_HEIGHT, self.waveform_view_right)
        self.waveform_view_right.set_stack_position("bottom" if stereo else None, _STEREO_VIEW_HEIGHT, self.waveform_view)
        if frames <= 0:
            self._shown_markers = None
            for view in views:
                view.clear()
                view.set_placeholder_text("This sample is empty")
            return
        self._shown_markers = (start, loop_start, loop_end, end)
        loaded = name == self._audio_name and self._audio_samples is not None
        for view, samples in ((self.waveform_view, self._audio_samples), (self.waveform_view_right, self._audio_samples_right)):
            view.set_loop_enabled(loops)
            if loaded and samples is not None:
                view.set_waveform(samples, start, loop_start, loop_end, end)
            else:
                view.set_header(frames, start, loop_start, loop_end, end)
        if not loaded:
            self._set_hint(_LOAD_HINT)
        elif self._writer is None:
            self._set_hint("Click the waveform to preview it. The markers are shown for reference.")
        else:
            self._set_hint("Drag a marker to change it (written when you let go) - click the waveform to preview.")

    def set_active(self, active):
        self._active = active
        if not active:
            self._preview.stop()  # nothing keeps sounding behind another tab

    def refresh(self):
        """Forget the audio read so far (the host is about to re-read everything)."""
        self._preview.stop()
        self._load_token += 1  # a load in flight is abandoned (the host cancels the session)
        self._scan_token += 1  # ...and so is the duration scan (set_samples starts a new one)
        self._reset_reveal()
        self._loading_name = None
        self.cancel_load_button.setVisible(False)
        self._audio_name = self._audio_samples = self._audio_samples_right = None

    # -- the markers ---------------------------------------------------------------------------------------

    def _apply_marker_lock(self):
        """Markers can be dragged when the tab can write, except while the audio is arriving (the views are being filled)."""
        locked = self._writer is None or self._loading_name is not None
        for view in (self.waveform_view, self.waveform_view_right):
            view.set_markers_locked(locked)

    def _other_view(self, view):
        return self.waveform_view_right if view is self.waveform_view else self.waveform_view

    def _on_markers_changed(self, view, markers):
        """Every drag step (and whenever a view is loaded): the other channel mirrors the move, and a looped preview follows the
        loop markers live."""
        if self._syncing_markers:
            return
        other = self._other_view(view)
        if other.isVisibleTo(self) and other.frame_count() == view.frame_count():
            self._syncing_markers = True
            try:
                other.apply_markers(*markers)
            finally:
                self._syncing_markers = False
        if self._preview.is_playing() and not self._preview_reversed:
            self._preview.update_loop_points(markers[1], markers[2])

    def _on_marker_committed(self, view, _which, markers):
        """A drag was let go: write whatever it changed. (`markers`: start, loop_start, loop_end, end as drawn.)"""
        name, after = self._selected, tuple(markers)
        if self._writer is None or name is None or name not in self._cache or self._shown_markers is None:
            return
        before, self._shown_markers = self._shown_markers, after
        if self._marker_job is not None:  # one at a time: the newest drag waits (and merges with an earlier one that waited)
            if self._marker_pending is not None:
                before = self._marker_pending[1]
            self._marker_pending = (name, before, after)
            return
        self._start_marker_edit(name, before, after)

    def _start_marker_edit(self, name, before, after):
        data = self._cache.get(name)
        if data is None or name != self._selected:
            return
        loops = yp.extract(yp.get("sample", "loop_mode"), data) in _LOOPING_MODES
        current = ym.read_markers(data)
        try:
            steps = ym.plan_marker_writes(current, ym.target_for_edit(current, before, after, loops))
        except ValueError as e:
            _log(f"marker edit of {name!r} refused: {e}")
            self.status_message.emit("Those markers aren't in a valid order")
            self._show_waveform(name, data)
            return
        if not steps:
            return
        _log(f"marker edit of {name!r}: " + ", ".join(f"{m} -> {a}" for m, a in steps))
        self._marker_job = {"name": name}
        self._run_marker_step(name, steps, 0)

    def _run_marker_step(self, name, steps, index):
        if index >= len(steps):
            self._finish_marker_job(name, ok=True)
            return
        marker, address = steps[index]
        row = yp.get("sample", ym.ROW_FOR[marker])
        done = lambda result, r=row: self._on_marker_step_done(name, steps, index, r, result)  # noqa: E731
        if not self._writer.edit(row, address, name, None, done):  # declined the one-time warning
            self._finish_marker_job(name, ok=False, edit_sent=False)
            return
        self._writer.flush()

    def _on_marker_step_done(self, name, steps, index, row, result):
        if not self._connected:
            return
        if name in self._cache and result.readback is not None:
            yp.store(row, self._cache[name], result.readback)
        if result.ok:
            self._run_marker_step(name, steps, index + 1)
            return
        message = result.message
        if row.key == "wave_end_address" and result.edit_sent and result.readback == result.previous:
            message = "The sampler didn't move the wave end - it can't be changed on a built-in waveform"
        self._finish_marker_job(name, ok=False, message=message, edit_sent=result.edit_sent)

    def _finish_marker_job(self, name, *, ok, message=None, edit_sent=True):
        self._marker_job = None
        pending, self._marker_pending = self._marker_pending, None
        if message:
            self.status_message.emit(message)
        if not ok:
            # put the markers back where the unit really has them (the re-read below then settles whatever else moved)
            if name == self._selected and name in self._cache:
                self._show_waveform(name, self._cache[name])
        if edit_sent:
            self._writer.schedule_reread("SP", name, lambda dump, n=name: self._on_reread(n, dump))
        if ok and pending is not None:
            # plan the next drag from what the unit holds NOW (a write can move a coupled address)
            self._session.request_bulk("SP", name, lambda dump, p=pending: self._after_marker_reread(p, dump))

    def _after_marker_reread(self, pending, dump):
        name, before, after = pending
        if not self._connected:
            return
        if dump is not None:
            self._cache[name] = bytearray(dump.data)
        if name == self._selected:
            self._start_marker_edit(name, before, after)

    # -- click to preview ---------------------------------------------------------------------------------------

    def _on_preview_requested(self):
        if self._preview.is_playing():
            self._preview.stop()  # a click while something sounds stops it (the only way a held loop ends)
            return
        name = self._selected
        data = self._cache.get(name)
        if data is None or name != self._audio_name or self._audio_samples is None or self._loading_name is not None:
            return  # nothing loaded to play
        left = self._audio_samples
        right = self._audio_samples_right if yp.is_stereo(data) else None
        m = self.waveform_view.markers_with_loop_in_range()
        rate = yp.extract(yp.get("sample", "sampling_frequency_l"), data)
        mode = yp.extract(yp.get("sample", "loop_mode"), data)
        if mode in _LOOPING_MODES:
            self._preview_reversed = 0
            self._preview.play_loop(
                left, m["start"], m["loop_start"], m["loop_end"], m["end"], rate,
                dwell_ms=None if mode == 1 else _RELEASE_PREVIEW_MS, right_samples=right,
            )
        elif mode in _REVERSE_MODES:
            # play a reversed copy, mapping its positions back for the playhead
            n = len(left)
            self._preview.play(
                left[::-1], n - 1 - m["end"], n - 1 - m["start"], rate, right_samples=None if right is None else right[::-1]
            )
            self._preview_reversed = n if self._preview.is_playing() else 0
        else:
            self._preview_reversed = 0
            self._preview.play(left, m["start"], m["end"], rate, right_samples=right)

    def _on_preview_position(self, frame):
        if self._preview_reversed:
            frame = self._preview_reversed - 1 - frame
        for view in (self.waveform_view, self.waveform_view_right):
            view.set_playhead(frame)

    def _clear_playheads(self):
        self._preview_reversed = 0
        for view in (self.waveform_view, self.waveform_view_right):
            view.clear_playhead()

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
        self._preview.stop()
        self._loading_name = name
        self._apply_marker_lock()
        self._load_token += 1
        token = self._load_token
        self._reset_reveal()
        views = (self.waveform_view, self.waveform_view_right)
        for view in views[: len(waves)]:
            view.set_loading(True)
            view.begin_live_capture()  # the wave draws itself in as it arrives
        self.cancel_load_button.setVisible(True)
        self._set_hint("Receiving the wave - it fills in as it arrives. Cancel stops waiting for it.")
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
            self._queue_for_reveal(view, list(new))
            self.status_message.emit(f"Receiving {name!r}{side}: {100 * progress['frames'] // max(total, 1)}%")

        self._session.request_wave(
            waves[index], lambda frames: self._on_wave(token, name, waves, got, frames), on_chunk=chunk
        )

    def _on_wave(self, token, name, waves, got, frames):
        if token != self._load_token or not self._connected:
            return
        if frames is None:
            self._reset_reveal()
            self._finish_load()
            if name in self._cache:
                self._show_waveform(name, self._cache[name])  # back to the header-only view
            self.status_message.emit(f"Couldn't load the audio of {name!r}")
            return
        got = got + [frames]
        if len(got) < len(waves):
            self._load_wave(token, name, waves, got)
            return
        # the last frames are still being revealed: finish once the display has caught up with what was received
        self._when_revealed(lambda: self._complete_load(token, name, got))

    def _complete_load(self, token, name, got):
        if token != self._load_token or not self._connected:
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
        self._reset_reveal()
        # a wave dump can't be aborted on the unit: the session stays busy until its stream has gone quiet
        self._session.cancel()
        self._finish_load()
        if name == self._selected and name in self._cache:
            self._show_waveform(name, self._cache[name])  # back to the header-only view
        self.status_message.emit(f"Cancelled loading {name!r}")

    # -- the smooth reveal -------------------------------------------------------------------------------------

    _REVEAL_TICK_S = 1 / 30
    _REVEAL_INITIAL_INTERVAL_S = 1.5  # the real gap between a unit's wave messages (~1.3-1.6 s at MIDI speed)
    _REVEAL_FINISH_S = 0.4  # once the transfer is over, how long the display takes to catch up

    def _queue_for_reveal(self, view, frames):
        now = time.monotonic()
        if self._reveal_last_chunk is not None:
            # blend the measured gap into the estimate (never trust one outlier completely)
            self._reveal_interval_s = 0.5 * self._reveal_interval_s + 0.5 * max(now - self._reveal_last_chunk, 0.02)
        self._reveal_last_chunk = now
        self._reveal_queue.append([view, frames, 0])
        self._reveal_pending += len(frames)
        self._reveal_speed = self._reveal_pending / max(self._reveal_interval_s, 0.05)
        if not self._reveal_timer.isActive():
            self._reveal_last_tick = now
            self._reveal_timer.start(int(self._REVEAL_TICK_S * 1000))

    def _when_revealed(self, callback):
        """Run `callback` once everything queued has been shown (at once if nothing is waiting)."""
        if not self._reveal_queue:
            callback()
            return
        self._reveal_when_done = callback
        self._reveal_speed = self._reveal_pending / self._REVEAL_FINISH_S  # no more chunks coming: catch up promptly

    def _reveal_tick(self):
        now = time.monotonic()
        started = now
        dt = now - (self._reveal_last_tick or now)
        self._reveal_last_tick = now
        budget = max(1, int(self._reveal_speed * dt))
        while budget > 0 and self._reveal_queue:
            segment = self._reveal_queue[0]
            view, frames, offset = segment
            take = frames[offset : offset + budget]
            segment[2] += len(take)
            budget -= len(take)
            self._reveal_pending -= len(take)
            view.append_live_samples(take)
            if segment[2] >= len(frames):
                self._reveal_queue.popleft()
        if self._reveal_queue:
            # redrawing costs more on a long sample: never let the reveal eat the event loop
            cost = time.monotonic() - started
            self._reveal_timer.start(int(max(self._REVEAL_TICK_S, 4 * cost) * 1000))
            return
        callback, self._reveal_when_done = self._reveal_when_done, None
        if callback is not None:
            callback()

    def _reset_reveal(self):
        self._reveal_timer.stop()
        self._reveal_queue.clear()
        self._reveal_pending = 0
        self._reveal_speed = 0.0
        self._reveal_interval_s = self._REVEAL_INITIAL_INTERVAL_S
        self._reveal_last_chunk = self._reveal_last_tick = None
        self._reveal_when_done = None

    def _finish_load(self):
        self._loading_name = None
        self._apply_marker_lock()
        for view in (self.waveform_view, self.waveform_view_right):
            view.set_loading(False)
        self.cancel_load_button.setVisible(False)
        self._set_hint(_LOAD_HINT)

    @property
    def loading(self):
        """True while the audio of a sample is being received."""
        return self._loading_name is not None

    def disconnect_controller(self):
        """The host window is closing: abandon a running load (the session drains its stream)."""
        if not self._connected:
            return
        self._connected = False
        self._preview.stop()
        self._marker_pending = None
        self._scan_token += 1
        self._reset_reveal()
        if self._loading_name is not None:
            self._load_token += 1
            self._session.cancel()
            self._loading_name = None
