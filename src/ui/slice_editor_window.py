from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollBar,
    QSpinBox,
    QVBoxLayout,
)

from core import sample_slicing
from core.audio_preview import SlicePreviewPlayer
from s3k.messages import AKAI_CHARSET, NAME_LENGTH
from ui.qt_helpers import widen_popup_to_fit_items
from ui.sample_settings_dialog import _BIT_DEPTH_OPTIONS, _RATE_OPTIONS
from ui.slice_waveform_view import SliceWaveformView

# same shape as program_editor_window.py's own _NAME_INPUT_PATTERN
# (AKAI_CHARSET/NAME_LENGTH-based) - duplicated rather than imported to
# avoid a circular import (program_editor_window.py is what constructs this
# dialog), same small-scale duplication program_editor_window.py's own
# _sample_edit_temp_name/_duplicate_default_name already accept for a
# single-expression, unlikely-to-drift shape
_NAME_INPUT_PATTERN = (
    "[" + AKAI_CHARSET.replace("-", "\\-") + "]{0," + str(NAME_LENGTH) + "}"
)

_EQUAL_SLICE_MIN = 2
_EQUAL_SLICE_MAX = 64
_EQUAL_SLICE_DEFAULT = 8


def slice_export_names(base_name, count):
    """The exact resident-sample names an export of *count* slices will use:
    <base>-01, <base>-02, ... - zero-padded to a FIXED width (derived once
    from *count*, not per-name) so every name in the same batch truncates
    the base identically. Getting this wrong (e.g. "-1".."-10" - two
    different suffix widths) would silently truncate slice 1's base one
    character longer than slice 10's, an inconsistency nobody asked for and
    that would look like a bug the moment two names in the same export
    don't share a common prefix.
    """
    width = len(str(count))
    suffixes = [f"-{i:0{width}d}" for i in range(1, count + 1)]
    max_suffix_len = max(len(s) for s in suffixes)
    truncated_base = base_name.strip().upper()[: NAME_LENGTH - max_suffix_len]
    return [(truncated_base + suffix)[:NAME_LENGTH] for suffix in suffixes]


def _akai_name_edit(default_text):
    edit = QLineEdit(default_text)
    edit.setMaxLength(NAME_LENGTH)
    edit.setFixedWidth(140)
    pattern = QRegularExpression(_NAME_INPUT_PATTERN)
    pattern.setPatternOptions(QRegularExpression.PatternOption.CaseInsensitiveOption)
    edit.setValidator(QRegularExpressionValidator(pattern, edit))
    return edit


def _build_quality_combo(options, default):
    combo = QComboBox()
    for label, value in options:
        combo.addItem(label, value)
    idx = combo.findData(default)
    combo.setCurrentIndex(idx if idx >= 0 else 0)
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    widen_popup_to_fit_items(combo)
    return combo


class SliceEditorWindow(QDialog):
    # The ReCycle-style breakbeat chopper: manual slice markers (no
    # transient detection - see AGENTS.md's own design discussion for why
    # that's out of scope) on a sample ALREADY fully loaded in memory (the
    # Samples tab's "Slice Editor" button - see program_editor_window.py's
    # _open_slice_editor - only enables once has_waveform() is true, the
    # same gate Duplicate Sample uses), plus an "Equal Slices" quick-start
    # and a batch export back to the sampler as one-shot samples.
    #
    # Deliberately an APPLICATION-modal QDialog (opened via .exec(), not
    # .show()) rather than a second free-floating top-level window: the
    # real export talks to the same sampler_controller/BridgeWorker
    # connections the rest of ProgramEditorWindow uses, and there's no
    # is_transfer_busy() re-check once export is underway - modality is
    # what actually prevents the user tabbing back to the Transfer
    # Dashboard and starting a conflicting send while this window is open,
    # rather than a race this window would otherwise have to poll for.
    # _wait_for_any_signal (in ProgramEditorWindow, which is what actually
    # performs the export - see export_callback below) already pumps its
    # own nested QEventLoop while blocking on a real send, so nesting
    # QDialog.exec() on top of that is nothing new for this codebase.
    #
    # This window owns naming/collision/confirmation UI and the pure
    # slicing math; export_callback (ProgramEditorWindow._export_slices)
    # owns everything hardware-shaped (temp WAVs, send_file_queue,
    # BridgeWorker header writes) - the same split AGENTS.md's "BridgeWorker"
    # section describes for the rest of this window's hardware access.
    def __init__(
        self,
        parent,
        sample_name,
        samples,
        framerate,
        spitch,
        stuno,
        shlto,
        existing_names_provider,
        export_callback,
        demo_mode=False,
        export_confirm_message=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f'Slice Editor - "{sample_name}"')
        self.setModal(True)

        self._samples = samples
        self._framerate = framerate
        self._spitch = spitch
        self._stuno = stuno
        self._shlto = shlto
        self._existing_names_provider = existing_names_provider
        self._export_callback = export_callback
        # (slice_count, names) -> str, shown in the Export confirmation
        # dialog - defaults to the original "send to the sampler" wording
        # (Program Editor's own use, unchanged), but the Transfer
        # Dashboard's own reuse of this window (slicing a QUEUED local
        # file, nothing sent to hardware yet, no freeze) needs genuinely
        # different wording, not just a substituted noun
        self._export_confirm_message = (
            export_confirm_message or self._default_export_confirm_message
        )
        self._exporting = False
        # whether any slice marker/start/end edit has happened since the
        # last successful export (or since opening, if never exported) -
        # what Close/Esc/window-X actually asks about below. Connected
        # AFTER set_waveform (right below) so its own initial
        # markers_changed emit doesn't mark a freshly-opened window dirty.
        self._dirty = False

        self._preview_player = SlicePreviewPlayer(self)

        self.waveform = SliceWaveformView()
        self.waveform.set_waveform(samples, 0, len(samples) - 1)
        self.waveform.markers_changed.connect(self._update_info_label)
        self.waveform.markers_changed.connect(self._mark_dirty)
        self.waveform.view_changed.connect(self._on_view_changed)
        self.waveform.slice_preview_requested.connect(self._preview_slice)
        self._preview_player.position_changed.connect(self.waveform.set_playhead)
        self._preview_player.finished.connect(self.waveform.clear_playhead)

        zoom_row = QHBoxLayout()
        hint_label = QLabel(
            "Click: preview slice   •   Double-click empty space: add slice   •   "
            "Double-click or right-click a marker: delete   •   "
            "Drag edges/markers: adjust"
        )
        self.zoom_out_button = QPushButton("−")
        # same 36px as the Samples tab's own zoom_out_button/zoom_in_button
        # (program_editor_window.py's _build_samples_tab) - keeps both
        # windows' zoom controls pixel-identical rather than each picking
        # its own width
        self.zoom_out_button.setFixedWidth(36)
        self.zoom_out_button.setToolTip("Zoom out")
        self.zoom_out_button.clicked.connect(lambda: self.waveform.zoom_out())
        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setFixedWidth(36)
        self.zoom_in_button.setToolTip("Zoom in")
        self.zoom_in_button.clicked.connect(lambda: self.waveform.zoom_in())
        self.zoom_fit_button = QPushButton("Fit")
        self.zoom_fit_button.setToolTip("Zoom to fit the whole sample")
        self.zoom_fit_button.clicked.connect(self.waveform.reset_zoom)
        zoom_row.addWidget(hint_label)
        zoom_row.addStretch()
        zoom_row.addWidget(self.zoom_out_button)
        zoom_row.addWidget(self.zoom_in_button)
        zoom_row.addWidget(self.zoom_fit_button)

        # unlike program_editor_window.py's own scrollbar_container (which
        # deliberately reserves its height even while hidden, so the
        # Samples tab's permanently-visible marker row never jumps as zoom
        # crosses the scrolling threshold), this dialog is short-lived and
        # its own layout below the waveform has nothing that minds
        # reflowing - added directly (no fixed-height wrapper) so it
        # actually collapses to zero height at "Fit" instead of leaving a
        # visible empty strip
        self.scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.scrollbar.valueChanged.connect(self._on_scrollbar_moved)

        self.info_label = QLabel()
        self.info_label.setWordWrap(True)

        equal_row = QHBoxLayout()
        self.equal_count_spin = QSpinBox()
        self.equal_count_spin.setRange(_EQUAL_SLICE_MIN, _EQUAL_SLICE_MAX)
        self.equal_count_spin.setValue(_EQUAL_SLICE_DEFAULT)
        self.equal_slices_button = QPushButton("Generate Equal Slices")
        self.equal_slices_button.clicked.connect(self._generate_equal_slices)
        equal_row.addWidget(QLabel("Equal slices:"))
        equal_row.addWidget(self.equal_count_spin)
        equal_row.addWidget(self.equal_slices_button)
        equal_row.addStretch()

        name_row = QHBoxLayout()
        self.name_edit = _akai_name_edit(sample_name)
        name_row.addWidget(QLabel("Base name:"))
        name_row.addWidget(self.name_edit)
        name_row.addSpacing(20)
        self.bit_depth_combo = _build_quality_combo(
            [(f"{d}-bit", d) for d in _BIT_DEPTH_OPTIONS], default=16
        )
        self.rate_combo = _build_quality_combo(_RATE_OPTIONS, default=None)
        name_row.addWidget(QLabel("Bit depth:"))
        name_row.addWidget(self.bit_depth_combo)
        name_row.addWidget(QLabel("Sample rate:"))
        name_row.addWidget(self.rate_combo)
        name_row.addStretch()

        export_row = QHBoxLayout()
        self.export_button = QPushButton("Export Slices")
        self.export_button.clicked.connect(self._confirm_export)
        if demo_mode:
            # unlike Duplicate Sample/Program/Keygroup (fully disabled in
            # demo mode - DemoBridge has no add-sample primitive at all),
            # the Slice Editor's marker placement and click-to-preview work
            # fine without hardware, so only THIS button - the one step
            # that actually writes new samples to the sampler - is disabled
            self.export_button.setEnabled(False)
            self.export_button.setToolTip(
                "Exporting slices to the sampler needs a real hardware "
                "connection - not available in demo mode. Marker placement "
                "and click-to-preview still work fully."
            )
        self.export_progress = QProgressBar()
        self.export_progress.setRange(0, 100)
        self.export_progress.setFixedWidth(160)
        self.export_progress.setVisible(False)
        self.status_label = QLabel("")
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        export_row.addWidget(self.export_button)
        export_row.addWidget(self.export_progress)
        export_row.addWidget(self.status_label, stretch=1)
        export_row.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addLayout(zoom_row)
        layout.addWidget(self.waveform)
        layout.addWidget(self.scrollbar)
        layout.addWidget(self.info_label)
        layout.addLayout(equal_row)
        layout.addLayout(name_row)
        layout.addLayout(export_row)
        self.resize(760, 420)

        self._update_info_label()
        self._on_view_changed(0, len(samples), len(samples))

    # --- waveform view <-> scrollbar plumbing (same pattern as the Samples
    # tab's own waveform_view/scrollbar wiring in program_editor_window.py) --

    def _on_view_changed(self, view_start, view_length, frame_count):
        self.scrollbar.blockSignals(True)
        self.scrollbar.setRange(0, max(0, frame_count - view_length))
        self.scrollbar.setPageStep(view_length)
        self.scrollbar.setValue(view_start)
        self.scrollbar.blockSignals(False)
        self.scrollbar.setVisible(view_length < frame_count)

    def _on_scrollbar_moved(self, value):
        self.waveform.set_view_start(value)

    # --- info label ------------------------------------------------------

    def _update_info_label(self):
        bounds = self.waveform.slice_bounds()
        lengths = ", ".join(str(b - a + 1) for a, b in bounds)
        self.info_label.setText(
            f"{len(bounds)} slice{'s' if len(bounds) != 1 else ''} - "
            f"lengths (frames): {lengths}"
        )

    # --- click-to-preview --------------------------------------------------

    def _preview_slice(self, slice_start, slice_end):
        self._preview_player.play(
            self._samples, slice_start, slice_end, self._framerate
        )

    # --- Equal Slices ------------------------------------------------------

    def _generate_equal_slices(self):
        existing = self.waveform.slice_markers()
        if existing:
            reply = QMessageBox.question(
                self,
                "Equal Slices",
                f"This replaces the {len(existing)} slice marker"
                f"{'s' if len(existing) != 1 else ''} already placed. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        count = self.equal_count_spin.value()
        markers = sample_slicing.equal_slice_markers(
            self.waveform.start(), self.waveform.end(), count
        )
        snapped = [
            sample_slicing.find_nearest_zero_crossing(self._samples, m)
            for m in markers
        ]
        self.waveform.set_markers(snapped)

    # --- export --------------------------------------------------------------

    @staticmethod
    def _default_export_confirm_message(slice_count, names):
        return (
            f'Send {slice_count} slices to the sampler as new samples '
            f'("{names[0]}".."{names[-1]}")? This can take a while and will '
            "freeze the interface."
        )

    def _confirm_export(self):
        markers = self.waveform.slice_markers()
        slice_count = len(markers) + 1
        if slice_count < 2:
            QMessageBox.information(
                self,
                "Export Slices",
                "Add at least one slice marker (double-click the waveform, "
                "or use Equal Slices) before exporting.",
            )
            return

        base_name = self.name_edit.text().strip()
        if not base_name:
            QMessageBox.warning(
                self, "Export Slices", "Enter a base name for the exported slices."
            )
            return

        names = slice_export_names(base_name, slice_count)
        if len(set(names)) != len(names):
            # only reachable with a base name short enough that truncation
            # can't be the cause - i.e. never, in practice (NAME_LENGTH=12
            # always leaves room for a distinct zero-padded suffix up to
            # _EQUAL_SLICE_MAX=64 slices) - cheap to guard anyway
            QMessageBox.warning(
                self,
                "Export Slices",
                "Generated slice names aren't unique - try a different base name.",
            )
            return
        existing = set(self._existing_names_provider())
        collisions = [n for n in names if n in existing]
        if collisions:
            QMessageBox.warning(
                self,
                "Export Slices",
                "These names are already in use by another resident sample:\n"
                + ", ".join(collisions)
                + "\n\nPick a different base name, or rename/delete the "
                "conflicting sample(s), then try again.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Export Slices",
            self._export_confirm_message(slice_count, names),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        bit_depth = self.bit_depth_combo.currentData()
        sample_rate = self.rate_combo.currentData()
        slices = sample_slicing.slice_samples(
            self._samples, self.waveform.start(), self.waveform.end(), markers
        )

        self._preview_player.stop()
        self._set_controls_enabled(False)
        self._exporting = True
        self.export_progress.setVisible(True)
        self.export_progress.setValue(0)
        self.status_label.setText(f"Exporting {slice_count} slices...")
        QApplication.processEvents()
        try:
            success, message = self._export_callback(
                names,
                slices,
                self._framerate,
                bit_depth,
                sample_rate,
                self._spitch,
                self._stuno,
                self._shlto,
                self._on_export_progress,
                self._on_export_status,
            )
        finally:
            self._exporting = False
            self._set_controls_enabled(True)
            self.export_progress.setVisible(False)

        self.status_label.setText(message)
        if success:
            self._dirty = False  # this exact marker layout is now on the sampler
            QMessageBox.information(self, "Export Slices", message)
        else:
            QMessageBox.warning(self, "Export Slices", message)

    def _on_export_progress(self, current, total):
        percent = int(100 * current / total) if total else 0
        self.export_progress.setValue(max(0, min(100, percent)))
        QApplication.processEvents()

    def _on_export_status(self, text):
        self.status_label.setText(text)
        QApplication.processEvents()

    def _mark_dirty(self):
        self._dirty = True

    def _set_controls_enabled(self, enabled):
        for widget in (
            self.waveform,
            self.zoom_out_button,
            self.zoom_in_button,
            self.zoom_fit_button,
            self.scrollbar,
            self.equal_count_spin,
            self.equal_slices_button,
            self.name_edit,
            self.bit_depth_combo,
            self.rate_combo,
            self.export_button,
            self.close_button,
        ):
            widget.setEnabled(enabled)

    def _confirm_discard(self):
        # shared by reject()/closeEvent() below - True means "go ahead and
        # actually close," False means "stay open"
        if self._exporting:
            return False  # ignore Close/Esc/window-X while a real send is in flight
        if not self._dirty:
            return True
        reply = QMessageBox.question(
            self,
            "Close Slice Editor",
            "Discard the slice markers placed since the last export?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            # closeEvent()'s own super().closeEvent(event) call re-enters
            # through QDialog's default closeEvent -> reject() (the X-button
            # path, not the in-window Close button) - without clearing this,
            # that second pass would hit _confirm_discard() again and ask
            # the same question twice for one close
            self._dirty = False
            return True
        return False

    def reject(self):
        if self._confirm_discard():
            self._preview_player.stop()
            super().reject()

    def closeEvent(self, event):
        if self._confirm_discard():
            self._preview_player.stop()
            super().closeEvent(event)
        else:
            event.ignore()
