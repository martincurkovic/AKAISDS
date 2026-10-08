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
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import debug_log
from core import root_note_detection
from core import yamaha_markers as ym
from core import yamaha_params as yp
from core.audio_preview import SlicePreviewPlayer
from core.midi_notes import midi_note_to_name
from ui import theme
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
from ui.knob import Knob
from ui.loop_preview_view import HALF_WINDOW_FRAMES, LoopJoinPreview
from ui.qt_helpers import build_scroll_area, build_section_card
from ui.waveform_view import WaveformView
from ui.yamaha_fields import Field, FieldPanel
from ui.yamaha_sample_edit import YamahaSampleEditor

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


#: How many Fine tune steps make one cent. UNMEASURED: the row is -63..+63; if a step is really ~1/64 or 1/128 of a semitone
#: (1.56 / 0.78 cents) the correction is off by up to that factor, never more than ~half a semitone either way. Check on the
#: unit (detect a pitch, then listen against a tuner) and change this one number if it needs it.
FINE_TUNE_STEPS_PER_CENT = 1.0

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
_MARKERS = (("start", "Start"), ("loop_start", "Loop Start"), ("loop_end", "Loop End"), ("end", "End"))
_MARKER_TIPS = {
    "start": "Wave start: the frame playback begins at",
    "loop_start": "Loop start: the frame the loop jumps back to (only for the looping modes)",
    "loop_end": "Loop end: the last frame of the loop, after which it jumps back to the loop start (only for the looping modes)",
    "end": "Wave end: the last frame that plays",
}
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

    # "Detect Pitch" sits right of the Original key spinbox (before the row's trailing stretch); the tab wires it up
    original_key_row = row(note("original_key_l", "Original key"))
    panel.detect_pitch_button = QPushButton("Detect Pitch")
    panel.detect_pitch_button.setToolTip(tt.YAMAHA_DETECT_PITCH_BUTTON)
    panel.detect_pitch_button.setEnabled(False)
    original_key_row.insertWidget(original_key_row.count() - 1, panel.detect_pitch_button)
    pitch = build_section_card(
        "Pitch",
        original_key_row,
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
    # (the loop mode, the four addresses and the edit buttons live in the Loop Controls card above these cards - see the tab's _build)
    info = build_section_card(
        "Wave",
        row(t("sampling_frequency_l", "Sample rate", format_hz)),
        row(t("wave_length", "Wave length", format_address)),
        row(t("loop_length", "Loop length", format_address)),
        row(t("loop_tempo", "Loop tempo", format_tempo)),
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
    for left, right in ((pitch, key_range), (level, info), (filt, feg), (aeg, peg), (lfo, controls), (out, eq)):
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
    #: a short message for the host window's status bar
    status_message = Signal(str)
    #: the user asked to jump to a program that uses the sample
    sample_selected = Signal(str)
    #: the edit buttons / Slice Editor made new samples on the unit: the host should re-read its sample list. The argument is the
    #: name to select afterwards ("" = leave the selection)
    samples_changed = Signal(str)
    #: samples were assigned to a program (the Slice Editor's "fill a program"): (program number, the sample names). The host re-reads it.
    programs_changed = Signal(int, list)
    #: a long operation (an audio load, or an edit's / slice export's send) started or ended: the host window locks its navigation while it runs
    busy_changed = Signal(bool)

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
        #: set by the host: callable() -> [(program number, label)] of the programs that hold no sample (what the Slice Editor can fill)
        self.free_programs_provider = None
        self._edit_busy = False  # an edit's new sample is being sent to the unit
        self._reported_busy = False
        self._loops_enabled = False  # the shown sample's loop mode loops (the loop markers/knobs/preview apply)
        self._marker_knobs = {}  # marker name -> (swatch, knob, value label)
        self._syncing_knobs = False
        self._preview_commit_timer = QTimer(self)  # a drag in the Loop Preview commits once it has been still for a moment
        self._preview_commit_timer.setSingleShot(True)
        self._preview_commit_timer.setInterval(400)
        self._preview_commit_timer.timeout.connect(self._commit_markers_from_view)
        self._preview_reversed = 0  # frames in the reversed copy being played (0 = a forward preview)
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
        self.panel.detect_pitch_button.clicked.connect(self._detect_pitch)
        self._preview.position_changed.connect(self._on_preview_position)
        self._preview.finished.connect(self._clear_playheads)
        theme.notifier.changed.connect(self._refresh_themed_swatches)
        self._edits = YamahaSampleEditor(self)
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
        zoom_row.addSpacing(12)
        # the S3000 editor's edit-progress bar, in the same place: a Trim/Reverse/... sends the new sample out, and the waveform
        # (unlike a load) has nothing of its own to show for that
        self.edit_progress = QProgressBar()
        self.edit_progress.setFixedWidth(140)
        self.edit_progress.setVisible(False)
        zoom_row.addWidget(self.edit_progress)
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
        # The Loop Controls card is laid out like the S3000 editor's: zoom, the waveform(s), then the four markers as knobs, the loop
        # mode and the edit buttons. (The A4000 has no loop hold or loop tune: its loop settings are the mode - owner's manual p.123 -
        # and a loop tempo, shown in the Wave card below.)
        marker_row = self._build_marker_row()
        loop_mode_row = self.panel.labeled_row(_combo("loop_mode", "Loop Mode"), label_width=70)
        edit_row = self._build_edit_row()
        waveform_card = build_section_card(
            "Loop Controls",
            zoom_row,
            channel_stack,
            self._row(scrollbar_container),
            self._row(self.waveform_hint),
            marker_row,
            loop_mode_row,
            edit_row,
        )
        # what the loop makes of the audio at its join (left channel), kept live with the markers
        self.loop_preview = LoopJoinPreview()
        self.loop_preview.marker_drag_delta.connect(self._on_loop_preview_dragged)
        loop_preview_card = build_section_card("Loop Preview", self._row(self.loop_preview))
        self.waveform_view.markers_changed.connect(self._update_marker_knobs)
        self.waveform_view.markers_changed.connect(lambda _s, ls, le, _e: self._refresh_loop_preview(ls, le))
        self.cards_page = build_sample_cards(self.panel)

        cards_layout = QVBoxLayout()
        style_card_page_layout(cards_layout)
        cards_layout.addLayout(header)
        cards_layout.addWidget(waveform_card)
        cards_layout.addWidget(loop_preview_card)
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

    # -- the marker knobs, loop preview and edit buttons (built like the S3000 editor's Loop Controls) ------------------

    def _build_marker_row(self):
        """Start / Loop Start / Loop End / End: a colour swatch, a name, a small knob and the frame number, like the S3000 editor.
        Dragging a knob moves the marker on the waveform (pushing its neighbours, as a drag on the waveform does); letting go (or
        typing a value) writes the addresses that changed - the same guarded path as dragging a marker."""
        row = QHBoxLayout()
        row.setSpacing(18)
        for name, display in _MARKERS:
            swatch = QLabel()
            swatch.setFixedSize(10, 10)
            swatch.setProperty("swatchKind", "marker_boundary" if name in ("start", "end") else "marker_loop")
            self._refresh_swatch(swatch)
            knob = Knob()
            knob.setRange(0, 0)
            knob.setFixedSize(28, 28)
            knob.setToolTip(_MARKER_TIPS[name])
            knob.setEnabled(False)
            value_label = QLabel("-")
            value_label.setFixedWidth(64)  # frame counts run well past six digits
            knob.valueChanged.connect(lambda v, lbl=value_label: lbl.setText(f"{v:,}"))
            knob.valueChanged.connect(lambda v, n=name: self._on_marker_knob_changed(n, v))
            knob.sliderReleased.connect(self._commit_markers_from_view)  # fires on a drag's release AND a typed value
            self._marker_knobs[name] = (swatch, knob, value_label)
            field = QHBoxLayout()
            field.setSpacing(4)
            field.addWidget(swatch)
            field.addWidget(QLabel(display + ":"))
            field.addWidget(knob)
            field.addWidget(value_label)
            row.addLayout(field)
        row.addStretch()
        return row

    def _build_edit_row(self):
        """Trim / Reverse / Fade / Normalise / Filter, and the Slice Editor. Each edit makes a NEW sample (the A4000 can't delete or
        overwrite a sample over MIDI), so the original is never touched - see ui/yamaha_sample_edit.py."""
        row = QHBoxLayout()
        row.setSpacing(8)
        self.edit_buttons = {}
        for key, label, tip in (
            ("trim", "Trim to Markers", tt.YAMAHA_TRIM_SAMPLE_BUTTON),
            ("reverse", "Reverse sample", tt.YAMAHA_REVERSE_SAMPLE_BUTTON),
            ("fade", "Fade In/Out", tt.YAMAHA_FADE_SAMPLE_BUTTON),
            ("normalise", "Normalise Sample", tt.YAMAHA_NORMALISE_SAMPLE_BUTTON),
            ("filter", "Filter Sample\u2026", tt.YAMAHA_FILTER_SAMPLE_BUTTON),
        ):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.setEnabled(False)
            self.edit_buttons[key] = button
            row.addWidget(button)
        row.addStretch()
        self.slice_button = QPushButton("Slice Editor\u2026")
        self.slice_button.setToolTip(tt.YAMAHA_SLICE_EDITOR_BUTTON)
        self.slice_button.setEnabled(False)
        row.addWidget(self.slice_button)
        return row

    def _swatch_color(self, swatch):
        palette = theme.current_palette()
        if swatch.property("swatchKind") == "marker_boundary" or not self._loops_enabled:
            return palette["text_disabled"]  # (a loop swatch greys out with its loop, like the S3000's)
        return palette["keygroup_color_3"]

    def _refresh_swatch(self, swatch):
        swatch.setStyleSheet(f"background-color: {self._swatch_color(swatch)}; border-radius: 2px;")

    def _refresh_themed_swatches(self):
        for swatch, _knob, _label in self._marker_knobs.values():
            self._refresh_swatch(swatch)

    def _set_marker_knob_range(self, frames):
        for _swatch, knob, _label in self._marker_knobs.values():
            knob.blockSignals(True)
            knob.setRange(0, max(frames - 1, 0))
            knob.blockSignals(False)

    def _update_marker_knobs(self, start, loop_start, loop_end, end):
        """The waveform's markers changed (a drag, a pushed neighbour, a new sample): show them on the knobs without echoing back."""
        values = {"start": start, "loop_start": loop_start, "loop_end": loop_end, "end": end}
        for name, (_swatch, knob, label) in self._marker_knobs.items():
            knob.blockSignals(True)
            knob.setValue(values[name])
            knob.blockSignals(False)
            label.setText(f"{values[name]:,}")

    def _clear_marker_knobs(self):
        self._set_marker_knob_range(0)
        for _swatch, knob, label in self._marker_knobs.values():
            knob.blockSignals(True)
            knob.setValue(0)
            knob.blockSignals(False)
            label.setText("-")

    def _on_marker_knob_changed(self, name, value):
        if not self.waveform_view.has_header():
            return
        # pushes neighbours exactly like a drag, then markers_changed re-syncs every knob (this one too, if it was pushed back)
        self.waveform_view.set_marker(name, value)

    def _commit_markers_from_view(self):
        """Write whatever the markers on the waveform now say (the knob was let go, or a Loop Preview drag went still)."""
        self._preview_commit_timer.stop()
        view = self.waveform_view
        if not view.has_header():
            return
        m = view.markers()
        self._on_marker_committed(view, None, (m["start"], m["loop_start"], m["loop_end"], m["end"]))

    def _on_loop_preview_dragged(self, name, delta):
        # the Loop Preview only reports a frame DELTA against its own zoom: apply it through the same entry point the knobs use,
        # and write once the drag has been still for a moment (the preview has no "let go" signal of its own)
        if not self.waveform_view.has_header() or not self._markers_editable():
            return
        self._on_marker_knob_changed(name, self.waveform_view.markers()[name] + delta)
        self._preview_commit_timer.start()

    def _refresh_loop_preview(self, loop_start, loop_end):
        if not self._loops_enabled:
            self.loop_preview.clear_no_loop()
            return
        before = self.waveform_view.samples_before(loop_end, HALF_WINDOW_FRAMES)
        after = self.waveform_view.samples_after(loop_start, HALF_WINDOW_FRAMES)
        if before is None or after is None:
            self.loop_preview.clear()
        else:
            self.loop_preview.set_join(before, after)

    def _markers_editable(self):
        return self._writer is not None and self._loading_name is None and not self._edit_busy

    def free_programs(self):
        return list(self.free_programs_provider()) if self.free_programs_provider is not None else []

    def show_edit_progress(self, sent, total, label):
        """The edit's new sample is going out: the small bar next to the zoom buttons, plus the percentage in the status bar."""
        self.edit_progress.setRange(0, max(total, 1))
        self.edit_progress.setValue(min(sent, max(total, 1)))
        self.edit_progress.setVisible(True)
        self.status_message.emit(f"{label} - {100 * sent // total if total else 0}%")

    def _set_edit_busy(self, busy):
        """A new sample is being sent to the unit (an edit or a slice export): nothing else on the tab may start meanwhile."""
        self._edit_busy = busy
        if not busy:
            self.edit_progress.setVisible(False)
        self._apply_marker_lock()  # also refreshes the edit buttons

    def _set_loops_enabled(self, loops):
        """The shown sample's loop mode loops (or not): the loop markers, knobs, swatches and preview follow."""
        self._loops_enabled = loops
        for kind in ("loop_start", "loop_end"):
            self._refresh_swatch(self._marker_knobs[kind][0])
        self._apply_marker_lock()
        m = self.waveform_view.markers() if self.waveform_view.has_header() else None
        if m is None:
            self.loop_preview.clear_no_loop() if not loops else self.loop_preview.clear()
        else:
            self._refresh_loop_preview(m["loop_start"], m["loop_end"])

    # -- the list -----------------------------------------------------------------------------------------

    def set_samples(self, names):
        """The unit's sample names, in object-list order."""
        previous = self._selected
        self._names = list(names)
        self.sample_list_widget.blockSignals(True)
        self.sample_list_widget.clear()
        for name in self._names:
            # a row widget (the S3000 editor's list row), so the ITEM gets no text of its own
            # (it would double-paint - AGENTS.md); the name lives in the item's data. The row's duration label is left
            # blank on purpose: reading every sample's length took one full SP dump each, one at a time, over MIDI
            item = QListWidgetItem(self.sample_list_widget)
            item.setData(Qt.ItemDataRole.UserRole, name)
            row_widget, _name_label, _duration_label = build_sample_list_row_widget(name)
            item.setSizeHint(row_widget.sizeHint())
            self.sample_list_widget.setItemWidget(item, row_widget)
        self.sample_list_widget.blockSignals(False)
        if previous in self._names:
            self.select_sample(previous)
        else:
            self._selected = None
            self._show_placeholder("Select a sample on the left" if self._names else "No samples on the unit")

    # -- the list's names ------------------------------------------------------------------------------------

    def sample_names(self):
        """The names in the list, in order."""
        return [self.sample_list_widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.sample_list_widget.count())]

    def reload_sample(self, name):
        """The host changed a sample on the unit behind our back (a restore): read it again and show what it holds now."""
        self._cache.pop(name, None)
        if name == self._selected and self.sample_list_widget.currentItem() is not None:
            self._on_selected(self.sample_list_widget.currentItem(), None)

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
            self._clear_marker_knobs()
            self._set_loops_enabled(False)
            return
        self._shown_markers = (start, loop_start, loop_end, end)
        loaded = name == self._audio_name and self._audio_samples is not None
        self._set_marker_knob_range(frames)
        for view, samples in ((self.waveform_view, self._audio_samples), (self.waveform_view_right, self._audio_samples_right)):
            view.set_loop_enabled(loops)
            if loaded and samples is not None:
                view.set_waveform(samples, start, loop_start, loop_end, end)
            else:
                view.set_header(frames, start, loop_start, loop_end, end)
        self._update_marker_knobs(start, loop_start, loop_end, end)
        self._set_loops_enabled(loops)
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
        self._reset_reveal()
        self._loading_name = None
        self.cancel_load_button.setVisible(False)
        self._audio_name = self._audio_samples = self._audio_samples_right = None

    # -- the markers ---------------------------------------------------------------------------------------

    def _apply_marker_lock(self):
        """Markers can be dragged when the tab can write, except while the audio is arriving (the views are being filled)."""
        locked = self._writer is None or self._loading_name is not None or self._edit_busy
        for view in (self.waveform_view, self.waveform_view_right):
            view.set_markers_locked(locked)
        has_header = self.waveform_view.has_header()
        for name, (_swatch, knob, _label) in self._marker_knobs.items():
            knob.setEnabled(has_header and not locked and (self._loops_enabled or name in ("start", "end")))
        self.update_edit_buttons()
        if self.long_operation != self._reported_busy:
            self._reported_busy = self.long_operation
            self.busy_changed.emit(self._reported_busy)

    @property
    def long_operation(self):
        """An audio load or an edit/slice send is running (the minutes-long things the host window freezes its navigation for)."""
        return self._loading_name is not None or self._edit_busy

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

    def update_edit_buttons(self):
        """The edit buttons need the audio of the SHOWN sample in memory, no load or edit running, and a tab that can write."""
        ready = (
            self._writer is not None
            and self._selected is not None
            and self._audio_name == self._selected
            and self._audio_samples is not None
            and self._loading_name is None
            and not self._edit_busy
        )
        for button in self.edit_buttons.values():
            button.setEnabled(ready)
        self.slice_button.setEnabled(ready)
        self.panel.detect_pitch_button.setEnabled(ready)

    def _detect_pitch(self):
        """Estimate the loaded sample's pitch (left channel) and, if the user agrees, write Original key and Fine tune."""
        name, data = self._selected, self._cache.get(self._selected)
        if data is None or self._audio_name != name or self._audio_samples is None or not self.waveform_view.has_header():
            return
        rate = yp.extract(yp.get("sample", "sampling_frequency_l"), data)
        window = root_note_detection.analysis_window(self._audio_samples, rate, self.waveform_view.markers_with_loop_in_range())
        result = root_note_detection.detect_root_note(window, rate, self.panel.value("original_key_l"))
        if result is None or result[2] < root_note_detection.CONFIDENCE_THRESHOLD:
            _log(f"pitch detection for {name!r}: no reliable pitch ({result})")
            QMessageBox.information(self, "Detect Pitch", "Couldn't reliably detect a pitch for this sample.")
            return
        note, cents, confidence = result
        # The sample sounds `cents` sharp of `note` when played at its original key, so Fine tune takes that off again (a
        # positive Fine tune is taken to raise the pitch). Coarse tune is left alone; the unit mirrors the left channel's key and
        # fine tune into the right one itself.
        fine_row = yp.get("sample", "fine_tune_l")
        fine = max(fine_row.lo, min(fine_row.hi, round(-cents * FINE_TUNE_STEPS_PER_CENT)))
        _log(f"pitch detection for {name!r}: {midi_note_to_name(note)} {cents:+.1f} cents, confidence {confidence:.2f} -> fine {fine}")
        answer = QMessageBox.question(
            self,
            "Detect Pitch",
            f"Detected pitch: {midi_note_to_name(note)} ({confidence * 100:.0f}% confidence, {cents:+.0f} cents).\n\n"
            f"Set this sample's Original key to {midi_note_to_name(note)} and Fine tune to {fine:+d}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        # through the widgets, so each change takes the ordinary guarded edit path (backup, write, read-back); a value that
        # is already set emits nothing and so writes nothing
        self.panel.set_value("original_key_l", note)
        self.panel.set_value("fine_tune_l", fine)

    @property
    def loading(self):
        """True while the audio of a sample is being received."""
        return self._loading_name is not None

    def disconnect_controller(self):
        """The host window is closing: abandon a running load (the session drains its stream)."""
        if not self._connected:
            return
        self._connected = False
        theme.notifier.changed.disconnect(self._refresh_themed_swatches)
        self._preview.stop()
        self._marker_pending = None
        self._reset_reveal()
        if self._loading_name is not None:
            self._load_token += 1
            self._session.cancel()
            self._loading_name = None
