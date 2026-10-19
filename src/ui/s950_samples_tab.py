"""The Samples tab of the Akai S900/S950 program editor.

Laid out like the S1000/S2000/S3000 editor's Samples tab (a sample list with durations on the
left, a waveform card and a details card on the right, built from the same
`ui/editor_layout.py` pieces and the same `WaveformView`). It shows:

 - the sample list, with each sample's duration (read lazily, one SPRM at a time, only while
   the tab is on screen),
 - the selected sample's parameters (length, rate, replay mode, start/end/loop, ...),
 - its audio, on request (double-click the waveform): a real sample dump, received
   ASYNCHRONOUSLY through the Dashboard's `SamplerController`/`S950Transfers` (the S3000
   editor blocks the whole window for this; the S950's engine is event-driven, so nothing here
   needs to).

What can be WRITTEN is the replay mode (one-shot / loop / alternating) and the direction
(forward / reversed), as a staged edit like the program editor's: change the combos, then press
"Write to Sampler" in the WINDOW's bottom bar (the tab has no buttons of its own: it offers
`write()` / `revert()` / `can_write()` and says when that changes with `state_changed`); the
engine reads the sample fresh, saves a backup, changes only those two
fields, writes and reads back (`S950Transfers.write_sample_params`). Start, end, loop length and
pitch stay read-only: how those SPRM fields behave is only inferred (s950tools' own GUI and
this tab even disagree about where a loop sits, see AGENTS.md), so a marker editor would be a
guess with real hardware writes behind it. s950tools also found that some SPRM changes (the
reverse flag for certain) only take effect once the sample is re-selected (ENT) on the unit.
Rename a sample from the Transfer Dashboard.
"""
import os
import tempfile

from functools import partial

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import app_config, debug_log, s950_sysex as s, sds_encoder
from ui import tooltips as tt
from ui.editor_layout import (
    build_list_column,
    build_sample_list_row_widget,
    build_samples_page,
    build_waveform_scrollbar,
    style_card_page_layout,
    sync_waveform_scrollbar,
)
from ui.qt_helpers import build_scroll_area, build_editor_card
from ui.waveform_view import WaveformView

# collapsible cards, remembered as "s950_editor/<title>" (ui/section_card.py)
_card = partial(build_editor_card, "s950_editor")

_REPLAY_MODE_LABELS = {
    s.REPLAY_ONE_SHOT: "One-shot",
    s.REPLAY_LOOP: "Loop",
    s.REPLAY_ALTERNATING: "Alternating loop",
}
_LOOPING_MODES = (s.REPLAY_LOOP, s.REPLAY_ALTERNATING)
_DIRECTION_LABELS = {s.PLAY_FORWARD: "Forward", s.PLAY_REVERSED: "Reversed"}
_LOAD_HINT = "Double-click the waveform to load the sample's audio."


# -- pure helpers (tested without a window) ------------------------------------------------------


def format_replay_mode(mode):
    return _REPLAY_MODE_LABELS.get(mode, f"unknown ({mode})")


def format_duration(words, rate_hz):
    """'0.45s' for the sample list; '' when the rate is unknown."""
    if rate_hz <= 0:
        return ""
    return f"{words / rate_hz:.2f}s"


def format_length(words, rate_hz):
    seconds = f" ({format_duration(words, rate_hz)})" if rate_hz > 0 else ""
    return f"{words:,} words{seconds}"


def sample_markers(params):
    """(start, loop_start, loop_end, end, loops) for the waveform, clamped into the sample.

    **The mapping is a guess** (the SPRM start/end/loop fields' behaviour is inferred, not
    measured): start = SSTART, end = SEND, and a looping sample loops over the last
    `loop_length` words before the end, the way the S3000 family does.
    """
    last = max(params.total_words - 1, 0)
    start = min(max(params.start, 0), last)
    end = min(max(params.end, start), last)
    loops = params.replay_mode in _LOOPING_MODES
    loop_start = min(max(end - params.loop_length, start), end) if loops else end
    return start, loop_start, end, end, loops


# -- the tab -------------------------------------------------------------------------------------


class S950SamplesTab(QWidget):
    #: a short message for the host window's status bar
    status_message = Signal(str)
    #: the staged edit (or whether a write is on the wire) changed: the host window refreshes its Write / Revert buttons
    state_changed = Signal()

    _RETRY_MS = 150
    _AUDIO_RETRIES = 40  # x _RETRY_MS: how long a load waits for the wire to go idle

    def __init__(self, sampler_controller, parent=None):
        super().__init__(parent)
        self._controller = sampler_controller
        self._entries = []  # [(slot, name)] from the last catalog
        self._params = {}  # slot -> SampleParams
        self._failed = set()  # slots whose read failed - not retried until refresh()
        self._queue = []  # slots still to read
        self._inflight = None  # the slot whose SPRM read is on the wire
        self._active = False  # the tab is on screen - background reads only run then
        self._slot = None  # the selected sample
        self._rows = {}  # slot -> (name label, duration label)
        # audio: one load at a time, shown only if its sample is still selected when it lands
        self._wave_slot = None
        self._wave_path = None
        self._audio_slot = None
        self._audio_samples = None
        self._writing = False  # a replay-mode / direction write is on the wire
        self._build()

        c = self._controller
        self._connected = True
        c.s950_sample_written.connect(self._on_sample_written)
        c.sample_slots_updated.connect(self._on_catalog)
        c.s950_sample_params_received.connect(self._on_params)
        c.sample_received.connect(self._on_audio_file)
        c.receive_finished.connect(self._on_receive_finished)
        c.receive_progress.connect(self._on_receive_progress)

        self._show_sample()

    # -- construction ---------------------------------------------------------------------------

    def _build(self):
        self.sample_list_widget = QListWidget()
        self.sample_list_widget.setObjectName("sampleList")
        self.sample_list_widget.setFixedWidth(200)
        self.sample_list_widget.currentItemChanged.connect(self._on_selected)
        samples_container = build_list_column("Samples", self.sample_list_widget)

        # waveform: zoom buttons, the view, its pan bar and a hint
        self.waveform_view = WaveformView()
        self.waveform_view.set_markers_locked(True)  # nothing here is editable
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
            lambda start, length, count: sync_waveform_scrollbar(
                self.waveform_scrollbar, start, length, count
            )
        )
        self.waveform_hint = QLabel(_LOAD_HINT)
        self.waveform_hint.setObjectName("mutedLabel")

        waveform_card = _card(
            "Waveform",
            zoom_row,
            self._row(self.waveform_view),
            self._row(scrollbar_container),
            self._row(self.waveform_hint),
            collapsible=False,  # the waveform: the page is no use without it
        )

        # the selected sample's parameters
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        self._values = {}
        for r, (key, text) in enumerate(
            (
                ("name", "Name"),
                ("length", "Length"),
                ("rate", "Sample rate"),
                ("replay", "Replay mode"),
                ("direction", "Direction"),
                ("start", "Start (words)"),
                ("end", "End (words)"),
                ("loop_length", "Loop length (words)"),
                ("loud_offset", "Loudness offset"),
                ("pitch", "Nominal pitch"),
            )
        ):
            label = QLabel(text)
            label.setObjectName("mutedLabel")
            if key in ("replay", "direction"):
                # the two things this tab can write: staged in a combo, applied by "Write to Sampler"
                value = QComboBox()
                labels = _REPLAY_MODE_LABELS if key == "replay" else _DIRECTION_LABELS
                for byte, text_ in labels.items():
                    value.addItem(text_, byte)
                value.setFixedWidth(160)
                value.setToolTip(tt.S950_REPLAY_MODE if key == "replay" else tt.S950_DIRECTION)
                value.currentIndexChanged.connect(self._sync_state)
                setattr(self, "replay_combo" if key == "replay" else "direction_combo", value)
                grid.addWidget(label, r, 0, Qt.AlignmentFlag.AlignVCenter)
                grid.addWidget(value, r, 1, Qt.AlignmentFlag.AlignLeft)
                continue
            value = QLabel("-")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(label, r, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, r, 1)
            self._values[key] = value
        grid.setColumnStretch(1, 1)
        note = QLabel(
            "Replay mode and direction can be written (experimental). Start, end, loop and pitch are "
            "shown as read; what they mean is not verified on a unit."
        )
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        info_card = _card("Sample", grid, self._row(note))

        cards = QVBoxLayout()
        style_card_page_layout(cards)
        cards.addWidget(waveform_card)
        cards.addWidget(info_card)
        cards.addStretch()
        cards_container = QWidget()
        cards_container.setLayout(cards)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(build_samples_page(samples_container, build_scroll_area(cards_container)))

    @staticmethod
    def _row(widget):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(widget)
        return row

    # -- host hooks -----------------------------------------------------------------------------

    def set_active(self, active):
        """The host says whether this tab is the one on screen: background SPRM reads only run
        while it is, so the Programs tab never competes with them for the wire."""
        self._active = bool(active)
        if self._active:
            self._pump()

    def refresh(self):
        """Forget everything read so far (the host is about to re-read the catalog)."""
        self._params.clear()
        self._failed.clear()
        self._audio_slot = self._audio_samples = None

    def disconnect_controller(self):
        if not self._connected:
            return
        self._connected = False
        c = self._controller
        for signal, slot in (
            (c.sample_slots_updated, self._on_catalog),
            (c.s950_sample_written, self._on_sample_written),
            (c.s950_sample_params_received, self._on_params),
            (c.sample_received, self._on_audio_file),
            (c.receive_finished, self._on_receive_finished),
            (c.receive_progress, self._on_receive_progress),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._discard_temp_file()

    # -- the list ---------------------------------------------------------------------------------

    def _on_catalog(self, entries):
        entries = list(entries)
        known = {slot: name for slot, name in self._entries}
        for slot, name in entries:
            if slot in known and known[slot] != name:
                self._params.pop(slot, None)  # renamed/replaced: what we read is stale
                self._failed.discard(slot)
                if slot == self._audio_slot:
                    self._audio_slot = self._audio_samples = None
        live = {slot for slot, _ in entries}
        for slot in list(self._params):
            if slot not in live:
                del self._params[slot]
        self._entries = entries

        keep = self._slot
        self.sample_list_widget.blockSignals(True)
        self.sample_list_widget.clear()
        self._rows = {}
        select_row = -1
        for row, (slot, name) in enumerate(entries):
            # a row widget, so the ITEM gets no text of its own (it would double-paint - AGENTS.md)
            item = QListWidgetItem(self.sample_list_widget)
            item.setData(Qt.ItemDataRole.UserRole, slot)
            row_widget, name_label, duration_label = build_sample_list_row_widget(name)
            item.setSizeHint(row_widget.sizeHint())
            self.sample_list_widget.setItemWidget(item, row_widget)
            self._rows[slot] = (name_label, duration_label)
            self._show_duration(slot)
            if slot == keep:
                select_row = row
        if select_row >= 0:
            self.sample_list_widget.setCurrentRow(select_row)
        self.sample_list_widget.blockSignals(False)
        if select_row < 0:
            self._slot = None
        self._queue = [slot for slot, _ in entries if slot not in self._params and slot not in self._failed]
        self._show_sample()
        self._pump()

    def _show_duration(self, slot):
        labels = self._rows.get(slot)
        params = self._params.get(slot)
        if labels is not None and params is not None:
            labels[1].setText(format_duration(params.total_words, params.sample_rate_hz))

    def _on_selected(self, current, _previous):
        self._slot = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        if self._slot in self._queue:  # the one the user is looking at goes first
            self._queue.remove(self._slot)
            self._queue.insert(0, self._slot)
        self._show_sample()
        self._pump()

    # -- reading parameters -------------------------------------------------------------------

    def _pump(self):
        if not self._active or self._inflight is not None or not self._queue:
            return
        if not self._controller.is_s950_idle():
            # something else is on the wire (a catalog read, a program read/write, a dump)
            QTimer.singleShot(self._RETRY_MS, self._pump)
            return
        self._inflight = self._queue.pop(0)
        self._controller.request_sample_params(self._inflight)

    def _on_params(self, slot, params):
        if slot != self._inflight:
            return  # not a read this tab asked for
        self._inflight = None
        if params is None:
            self._failed.add(slot)
            if slot == self._slot:
                self._show_sample()  # "Reading..." becomes "Couldn't read ..."
        else:
            self._params[slot] = params
            self._show_duration(slot)
            if slot == self._slot:
                self._show_sample()
        QTimer.singleShot(0, self._pump)

    # -- showing the selected sample ----------------------------------------------------------

    def _show_sample(self):
        view = self.waveform_view
        slot = self._slot
        params = self._params.get(slot)
        if slot is None:
            self._clear_values()
            view.clear()
            view.set_placeholder_text("Select a sample on the left")
            return
        if params is None:
            self._clear_values()
            view.clear()
            view.set_placeholder_text(
                "Couldn't read this sample's parameters" if slot in self._failed else "Reading..."
            )
            return
        v = self._values
        v["name"].setText(params.name.strip() or "(unnamed)")
        v["length"].setText(format_length(params.total_words, params.sample_rate_hz))
        v["rate"].setText(f"{params.sample_rate_hz:,} Hz" if params.sample_rate_hz else "-")
        self._set_combos(params)
        v["start"].setText(f"{params.start:,}")
        v["end"].setText(f"{params.end:,}")
        v["loop_length"].setText(f"{params.loop_length:,}")
        v["loud_offset"].setText(f"{params.loud_offset:+d}")
        v["pitch"].setText(f"{params.nominal_pitch} (raw, unverified units)")

        if params.total_words <= 0:
            view.clear()
            view.set_placeholder_text("This sample is empty")
            return
        start, loop_start, loop_end, end, loops = sample_markers(params)
        view.set_loop_enabled(loops)
        if slot == self._audio_slot and self._audio_samples is not None:
            view.set_waveform(self._audio_samples, start, loop_start, loop_end, end)
            self.waveform_hint.setText("The markers are shown for reference - they can't be edited here.")
        else:
            view.set_header(params.total_words, start, loop_start, loop_end, end)
            self.waveform_hint.setText(_LOAD_HINT)

    def _clear_values(self):
        for label in self._values.values():
            label.setText("-")
        self._set_combos(None)

    # -- writing the replay mode and direction ------------------------------------------------------

    def _set_combos(self, params):
        """Show the sampler's replay mode and direction in the combos (no write is triggered); None = nothing to show."""
        for combo, value in ((self.replay_combo, params and params.replay_mode), (self.direction_combo, params and params.reversed)):
            combo.blockSignals(True)
            index = combo.findData(value) if params is not None else -1
            if params is not None and index < 0:
                combo.addItem(f"Unknown ({value})", value)  # a byte this app doesn't know: shown, never written by itself
                index = combo.findData(value)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)
        self._sync_state()

    def _changes(self):
        """{field: byte} for each combo that differs from what the sampler holds, for the selected sample."""
        params = self._params.get(self._slot)
        if params is None:
            return {}
        changes = {}
        if self.replay_combo.currentData() != params.replay_mode:
            changes["replay_mode"] = self.replay_combo.currentData()
        if self.direction_combo.currentData() != params.reversed:
            changes["reversed"] = self.direction_combo.currentData()
        return changes

    def _sync_state(self, *_args):
        known = self._params.get(self._slot) is not None
        for combo in (self.replay_combo, self.direction_combo):
            combo.setEnabled(known and not self._writing)
        self.state_changed.emit()

    def pending_changes(self):
        """How many of the two fields the user has staged a different value for."""
        return len(self._changes())

    def can_write(self):
        return self.pending_changes() > 0 and not self._writing

    def is_writing(self):
        return self._writing

    def revert(self):
        """Put the combos back to what the sampler holds."""
        self._show_sample()

    def _confirm_first_write(self):
        if app_config.get_s950_sample_write_warning_acknowledged():
            return True
        answer = QMessageBox.warning(
            self,
            "Akai S900/S950 - experimental",
            "Writing a sample's settings to the Akai S900/S950 is experimental.\n\n"
            "It was written from s950tools' notes without an S900/S950 to test against. Before every "
            "write the sample's settings are saved to ~/.akaisds/s950_backups as a .syx file, and "
            "afterwards they are read back and compared. The S950 may only follow a changed direction "
            "once you press ENT on the sample.\n\n"
            "Try a scratch sample first, and please report problems along with ~/.akaisds/akaisds.log.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return False
        app_config.save_s950_sample_write_warning_acknowledged(True)
        return True

    def write(self):
        """Write the staged replay mode / direction (after the one-time warning, and only if the wire is free)."""
        slot, changes = self._slot, self._changes()
        if slot is None or not changes or self._writing:
            return
        log = debug_log.get_logger()
        if not self._controller.is_s950_idle():
            self.status_message.emit("A MIDI operation is already in progress - try again in a moment")
            return
        if not self._confirm_first_write():
            log.info("S950Samples: write cancelled at the experimental warning")
            return
        log.info(f"S950Samples: write requested for sample {slot}: {changes}")
        self._writing = True
        self._sync_state()
        self.status_message.emit(f"Writing sample {slot}'s settings...")
        self._controller.write_s950_sample_params(slot, changes)

    def _on_sample_written(self, slot, verified, held, message):
        if not self._writing:
            return
        self._writing = False
        debug_log.get_logger().info(f"S950Samples: sample {slot} write result: verified={verified}, state returned={held is not None}: {message}")
        if held is not None:
            self._params[slot] = held  # what the sampler holds now is the truth, whether or not it is what was sent
        if slot == self._slot:
            self._show_sample()
        else:
            self._sync_state()
        self.status_message.emit(message)

    # -- loading audio ------------------------------------------------------------------------------

    def _load_audio(self, attempt=0):
        slot = self._slot
        if slot is None or slot not in self._params or self._wave_path is not None:
            return
        if not self._controller.is_s950_idle():
            if attempt < self._AUDIO_RETRIES:
                QTimer.singleShot(self._RETRY_MS, lambda: self._load_audio(attempt + 1))
            else:
                self.status_message.emit("The sampler is busy - try loading the waveform again")
            return
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="akaisds-s950-")
        os.close(fd)
        self._wave_slot, self._wave_path = slot, path
        self.waveform_view.set_loading(True)
        self.status_message.emit(f"Receiving sample {slot}...")
        self._controller.receive_samples([(slot, path)])

    def _on_receive_progress(self, received, total):
        if self._wave_path is not None and total:
            self.status_message.emit(
                f"Receiving sample {self._wave_slot}: {int(100 * received / total)}%"
            )

    def _on_audio_file(self, path):
        if path != self._wave_path:
            return  # a receive the Dashboard (or another window) asked for
        slot = self._wave_slot
        try:
            channels, _rate = sds_encoder.read_wav_channels(path)
            samples = list(channels[0])
        except Exception as e:
            debug_log.get_logger().warning(
                f"S950SamplesTab: couldn't read the received audio for sample {slot}: {e!r}"
            )
            self.status_message.emit(f"Couldn't read the received audio: {e}")
            self._finish_load()
            return
        self._finish_load()
        self._audio_slot, self._audio_samples = slot, samples
        if slot == self._slot:
            self._show_sample()

    def _on_receive_finished(self, completed):
        if self._wave_path is None:
            return
        if not completed:
            # the engine already reported why (timeout, NAK, cancel ...) on the status bar
            self._finish_load()

    def _finish_load(self):
        self._discard_temp_file()
        self._wave_slot = self._wave_path = None
        self.waveform_view.set_loading(False)

    def _discard_temp_file(self):
        path = self._wave_path
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
