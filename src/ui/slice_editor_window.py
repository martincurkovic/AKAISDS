from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QKeySequence, QRegularExpressionValidator, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollBar,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core import sample_slicing
from core import transient_detection
from core.audio_preview import SlicePreviewPlayer
from s3k.messages import AKAI_CHARSET, NAME_LENGTH
from ui.qt_helpers import widen_popup_to_fit_items
from ui.sample_settings_dialog import _BIT_DEPTH_OPTIONS, _RATE_OPTIONS
from ui.slice_waveform_view import SliceWaveformView
from ui import tooltips

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

# ReCycle's own "Sensitivity" convention: 0-100%, 0 (its own rest
# position) meaning fully off - see transient_detection.markers_from_flux's
# own docstring on why 0 is special-cased rather than just "a very high
# threshold"
_TRANSIENT_SENSITIVITY_DEFAULT = 0


def slice_export_names(base_name, count):
    """The exact resident-sample names an export of *count* slices will use:
    <base>-01, <base>-02, ... - zero-padded to a FIXED width (derived once
    from *count*, not per-name) so every name in the same batch truncates
    the base identically. Getting this wrong (e.g. "-1".."-10" - two
    different suffix widths) would silently truncate slice 1's base one
    character longer than slice 10's, an inconsistency nobody asked for and
    that would look like a bug the moment two names in the same export
    don't share a common prefix.

    count == 1 is a plain trim/crop (no slice markers at all - the whole
    [start, end] region exported as one sample - see SliceEditorWindow's
    own class docstring on doubling as a trimmer) rather than "slice 1 of
    1", so it skips the numeric suffix entirely and just returns the
    base name itself, truncated/uppercased the same way.
    """
    if count == 1:
        return [base_name.strip().upper()[:NAME_LENGTH]]
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
    # Also doubles as a plain trimmer: exporting with NO slice markers at
    # all (just the start/end trim handles adjusted) sends the whole
    # [start, end] region as ONE sample, not an error case - per direct
    # user request, for a quick "just crop this and send it" without
    # bothering to place any slice markers. slice_export_names skips the
    # usual "-01" numeric suffix in this case (a single trim isn't "slice
    # 1 of 1"), and _default_export_confirm_message reads "send X" rather
    # than "send N slices". core.sample_slicing.slice_samples already
    # returned exactly this (one slice covering the whole region) for a
    # markerless export from the start - the only change needed here was
    # actually letting the call through, not new slicing math.
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
        program_names_provider=None,
        create_program_callback=None,
        cancel_callback=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f'Slice Editor - "{sample_name}"')
        self.setModal(True)

        self._samples = samples
        self._framerate = framerate
        # computed ONCE here, reused on every Sensitivity slider tick - see
        # transient_detection.compute_flux's own docstring on why
        self._transient_flux, self._transient_window_frames = (
            transient_detection.compute_flux(samples, framerate)
        )
        self._spitch = spitch
        self._stuno = stuno
        self._shlto = shlto
        self._existing_names_provider = existing_names_provider
        self._export_callback = export_callback
        # None for dashboard.py's own reuse of this window (slicing a
        # queued LOCAL file - nothing hits the wire, so there's nothing to
        # cancel). program_editor_window.py's use passes
        # sampler_controller.cancel_transfer - see the Ctrl+. QShortcut
        # below, bound only while self._exporting is True
        self._cancel_callback = cancel_callback
        # both None for dashboard.py's own reuse of this window (slicing a
        # queued local file - nothing resident on the sampler yet to clone a
        # program from), which is why the checkbox/combo below are built
        # only when both are actually supplied
        self._program_names_provider = program_names_provider
        self._create_program_callback = create_program_callback
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
        # True once at least one export has actually succeeded this
        # session - a public flag (not underscore-prefixed) callers can
        # check after .exec() returns, so they only reload/refresh
        # whatever list this sample lives in when something on the
        # sampler could actually have changed, not on every close
        # regardless of whether Export was ever even clicked. See
        # ProgramEditorWindow._open_slice_editor's own use of this.
        self.export_succeeded = False

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
        self.zoom_out_button.clicked.connect(lambda: self.waveform.zoom_out())
        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setFixedWidth(36)
        self.zoom_in_button.clicked.connect(lambda: self.waveform.zoom_in())
        self.zoom_fit_button = QPushButton("Fit")
        self.zoom_fit_button.clicked.connect(self.waveform.reset_zoom)

        # this dialog is a QDialog with no menu bar (see AGENTS.md/research
        # for why a QMenuBar was deliberately not added here) - so unlike
        # program_editor_window.py's View menu, these are plain QShortcuts,
        # the same pattern quickstart_dialog.py already uses for its own
        # dialog-scoped shortcuts. There's no standard key for "zoom to fit"
        # so that one is a plain "Ctrl+0", matching the common browser/
        # editor "reset zoom" convention (same choice
        # program_editor_window.py's own Zoom to Fit action makes).
        def _bind_all(key_bindings, slot):
            # QShortcut only takes one QKeySequence each - a platform can map
            # a StandardKey to more than one binding (e.g. both Ctrl+= and
            # Ctrl++), so one QShortcut per binding, all triggering the same
            # slot. Returns the first (primary) binding, for the tooltip.
            for key_binding in key_bindings:
                shortcut = QShortcut(key_binding, self)
                shortcut.activated.connect(slot)
            return key_bindings[0] if key_bindings else QKeySequence()

        # "Ctrl+=" (auto-translates to "Cmd+=" on macOS) is the primary
        # zoom-in binding, put first so it's the one shown in the tooltip -
        # QKeySequence.StandardKey.ZoomIn's own platform-default primary
        # binding is "Ctrl++", which on a US keyboard needs Shift too
        # (effectively Ctrl+Shift+=). The standard key's own bindings are
        # still appended as secondary shortcuts, matching
        # program_editor_window.py's own View menu action.
        zoom_in_key = _bind_all(
            [QKeySequence("Ctrl+=")]
            + QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomIn),
            self.waveform.zoom_in,
        )
        # "-" needs no Shift on a US keyboard, so StandardKey.ZoomOut's own
        # default ("Ctrl+-") is used unmodified.
        zoom_out_key = _bind_all(
            QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomOut),
            self.waveform.zoom_out,
        )
        zoom_fit_key = _bind_all([QKeySequence("Ctrl+0")], self.waveform.reset_zoom)

        # Ctrl+. (auto-translates to Cmd+. on macOS, same as main_window.py's
        # own Transfer > Cancel Transfer action) - cancels an in-flight
        # export. export_callback (ProgramEditorWindow._export_slices)
        # blocks in its own nested QEventLoop the whole time an export is
        # running (see this class's own docstring on why that's safe to
        # nest a QShortcut activation into), so this reaches
        # sampler_controller.cancel_transfer() - already documented safe to
        # call at any time - and unblocks that wait the same way it already
        # unblocks a Dashboard transfer. Only meaningful (and only wired at
        # all - see cancel_callback's own docstring above) while an export
        # is actually running; a stray Ctrl+. the rest of the time is a
        # harmless no-op.
        self._cancel_export_shortcut = QShortcut(QKeySequence("Ctrl+."), self)
        self._cancel_export_shortcut.activated.connect(self._on_cancel_export_shortcut)

        for button, key, tooltip in (
            (self.zoom_out_button, zoom_out_key, tooltips.ZOOM_OUT),
            (self.zoom_in_button, zoom_in_key, tooltips.ZOOM_IN),
            (self.zoom_fit_button, zoom_fit_key, tooltips.ZOOM_FIT),
        ):
            shortcut_text = key.toString(QKeySequence.SequenceFormat.NativeText)
            button.setToolTip(f"{tooltip} ({shortcut_text})" if shortcut_text else tooltip)

        zoom_row.addWidget(hint_label)
        zoom_row.addStretch()
        zoom_row.addWidget(self.zoom_out_button)
        zoom_row.addWidget(self.zoom_in_button)
        zoom_row.addWidget(self.zoom_fit_button)

        # this used to be added directly (no fixed-height wrapper), on the
        # reasoning that a short-lived modal dialog's own layout below the
        # waveform had nothing that would mind reflowing - reversed after
        # real use: every row below the scrollbar (marker spinboxes, Equal
        # Slices, Sensitivity, name/quality, Export) visibly jumped up and
        # back down every time zooming crossed the scrolling threshold,
        # which is exactly as jarring here as it would be anywhere else.
        # Same fixed-height container program_editor_window.py's own
        # scrollbar_container already uses for the Samples tab's
        # WaveformView, for the same reason - see that one's own comment.
        self.scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.scrollbar.valueChanged.connect(self._on_scrollbar_moved)
        self._scrollbar_container = QWidget()
        # transparentContainer (style.qss.template) - otherwise this bare
        # QWidget picks up the base QWidget rule's own ${bg} rather than
        # blending into the QDialog's own ${bg_dialog}, which differs -
        # same fix program_editor_window.py's own scrollbar_container uses
        self._scrollbar_container.setObjectName("transparentContainer")
        self._scrollbar_container.setFixedHeight(self.scrollbar.sizeHint().height())
        scrollbar_container_layout = QVBoxLayout(self._scrollbar_container)
        scrollbar_container_layout.setContentsMargins(0, 0, 0, 0)
        scrollbar_container_layout.addWidget(self.scrollbar)

        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        # a plain QLabel defaults to a Preferred vertical size policy - the
        # only widget directly in the main layout below that isn't already
        # Fixed (every QHBoxLayout row's own buttons/spinboxes/combos are).
        # Without pinning this down too, it was the one thing that silently
        # grew to soak up the waveform's own newly-freed surplus space (see
        # SliceWaveformView's own construction comment) rather than the
        # waveform growing at all - same failure mode program_editor_
        # window.py's own _build_section_card already had to fix once for
        # its section cards (see AGENTS.md).
        self.info_label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )

        equal_row = QHBoxLayout()
        self.equal_count_spin = QSpinBox()
        self.equal_count_spin.setRange(_EQUAL_SLICE_MIN, _EQUAL_SLICE_MAX)
        self.equal_count_spin.setValue(_EQUAL_SLICE_DEFAULT)
        self.equal_slices_button = QPushButton("Generate Equal Slices")
        self.equal_slices_button.setToolTip(tooltips.EQUAL_SLICES_BUTTON)
        self.equal_slices_button.clicked.connect(self._generate_equal_slices)
        equal_row.addWidget(QLabel("Equal slices:"))
        equal_row.addWidget(self.equal_count_spin)
        equal_row.addWidget(self.equal_slices_button)
        equal_row.addStretch()

        # Hand-rolled energy-flux onset detection (core/transient_
        # detection.py) - deliberately simple, see that module's own
        # docstring for why. No separate "Detect" button - the slider IS
        # the live control: 0 (its own rest position/default) means fully
        # off, dragging it up live-regenerates the slice markers from
        # scratch on every tick (see _on_transient_sensitivity_changed),
        # same as turning a real ReCycle-style sensitivity knob. This
        # means moving the slider away from 0 REPLACES whatever markers
        # are currently there, hand-placed or not, with no confirmation
        # prompt - a confirm-per-tick while dragging would be unusable.
        # Drag back to 0 (or undo via manual editing) to recover.
        transient_row = QHBoxLayout()
        self.transient_sensitivity_slider = QSlider(Qt.Orientation.Horizontal)
        self.transient_sensitivity_slider.setRange(0, 100)
        self.transient_sensitivity_slider.setValue(_TRANSIENT_SENSITIVITY_DEFAULT)
        self.transient_sensitivity_slider.setFixedWidth(160)
        self.transient_sensitivity_slider.setToolTip(
            tooltips.TRANSIENT_SENSITIVITY_SLIDER
        )
        self.transient_sensitivity_value_label = QLabel(
            f"{_TRANSIENT_SENSITIVITY_DEFAULT}%"
        )
        self.transient_sensitivity_value_label.setFixedWidth(36)
        self.transient_sensitivity_slider.valueChanged.connect(
            self._on_transient_sensitivity_changed
        )
        transient_row.addWidget(QLabel("Sensitivity:"))
        transient_row.addWidget(self.transient_sensitivity_slider)
        transient_row.addWidget(self.transient_sensitivity_value_label)
        transient_row.addStretch()

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
        self.export_button.setToolTip(tooltips.EXPORT_SLICES_BUTTON)
        self.export_button.clicked.connect(self._confirm_export)
        if demo_mode:
            # unlike Duplicate Sample/Program/Keygroup (fully disabled in
            # demo mode - DemoBridge has no add-sample primitive at all),
            # the Slice Editor's marker placement and click-to-preview work
            # fine without hardware, so only THIS button - the one step
            # that actually writes new samples to the sampler - is disabled
            self.export_button.setEnabled(False)
            self.export_button.setToolTip(tooltips.EXPORT_SLICES_DEMO_MODE)
        export_row.addWidget(self.export_button)

        # only built when there's actually something to cancel - None for
        # dashboard.py's own reuse of this window (see cancel_callback's
        # own docstring above). Only shown while self._exporting is True
        # (see _confirm_export/_on_cancel_export_shortcut) - deliberately
        # left OUT of _set_controls_enabled's own widget list below, same
        # "has to stay reachable while everything else freezes" reasoning
        # as program_editor_window.py's own cancel_transfer_button
        self.cancel_export_button = None
        if cancel_callback is not None:
            self.cancel_export_button = QPushButton("Cancel Transfer")
            self.cancel_export_button.setToolTip(
                f"{tooltips.CANCEL_TRANSFER_BUTTON} "
                f"({self._cancel_export_shortcut.key().toString(QKeySequence.SequenceFormat.NativeText)})"
            )
            self.cancel_export_button.clicked.connect(
                self._on_cancel_export_shortcut
            )
            self.cancel_export_button.setVisible(False)
            export_row.addWidget(self.cancel_export_button)

        # ReCycle-style "export to Akai sampler format": in addition to
        # sending each slice as its own one-shot sample (above), optionally
        # create a whole new PROGRAM with one keygroup per slice, each
        # mapped to its own key starting at C1 (MIDI note 36 - see
        # core/midi_notes.py's own C3-at-60 convention), Const Pitch (no key
        # tracking - the sample always plays at its own recorded pitch
        # regardless of which key triggered it). Creating a program needs
        # an existing resident program to clone as a structural template
        # (filter/envelope/MIDI channel/etc. - see
        # ProgramEditorWindow._create_program_from_slices) - there's no
        # from-scratch "blank program" primitive - hence the Template combo.
        # program_names_provider/create_program_callback are both None for
        # dashboard.py's own reuse of this window, which has nothing
        # resident yet to build a program from - neither widget is built
        # there, rather than being built and disabled.
        self.create_program_checkbox = None
        self.template_program_combo = None
        self._template_program_label = None
        self._template_program_separator = None
        if program_names_provider is not None and create_program_callback is not None:
            program_names = program_names_provider()
            self.create_program_checkbox = QCheckBox("Create new program with slices")
            self.create_program_checkbox.setToolTip(tooltips.CREATE_PROGRAM_CHECKBOX)
            # same bullet-separator style as the zoom row's own hint_label
            # above ("Click: preview slice   •   ...") - a plain "Template:"
            # butted right up against the checkbox's own text read as one
            # run-on phrase rather than two distinct controls
            self._template_program_separator = QLabel("•")
            self._template_program_label = QLabel("Template:")
            self.template_program_combo = QComboBox()
            self.template_program_combo.addItems(program_names)
            # the separator/label/combo only ever APPEAR once the checkbox
            # is checked - per direct user request, rather than sitting
            # there always-visible-but-greyed-out
            self._template_program_separator.setVisible(False)
            self._template_program_label.setVisible(False)
            self.template_program_combo.setVisible(False)
            self.template_program_combo.setEnabled(False)
            self.create_program_checkbox.toggled.connect(
                self._on_create_program_toggled
            )
            # _set_controls_enabled(True) (after an export finishes) must
            # NOT blindly re-enable this checkbox if it was permanently
            # disabled for one of these two reasons - remembered here so
            # that method can restore the right state rather than the
            # demo-mode/no-template tooltip silently becoming re-enabled
            self._create_program_checkbox_allowed = True
            if demo_mode:
                # same reasoning as export_button's own demo-mode disable
                # above - DemoBridge has no add-program primitive either
                self._create_program_checkbox_allowed = False
                self.create_program_checkbox.setEnabled(False)
                self.create_program_checkbox.setToolTip(tooltips.CREATE_PROGRAM_DEMO_MODE)
            elif not program_names:
                self._create_program_checkbox_allowed = False
                self.create_program_checkbox.setEnabled(False)
                self.create_program_checkbox.setToolTip(
                    tooltips.CREATE_PROGRAM_NO_TEMPLATE
                )
            export_row.addWidget(self.create_program_checkbox)
            export_row.addWidget(self._template_program_separator)
            export_row.addWidget(self._template_program_label)
            export_row.addWidget(self.template_program_combo)

        self.export_progress = QProgressBar()
        self.export_progress.setRange(0, 100)
        self.export_progress.setFixedWidth(160)
        self.export_progress.setVisible(False)
        self.status_label = QLabel("")
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        export_row.addWidget(self.export_progress)
        export_row.addWidget(self.status_label, stretch=1)
        export_row.addWidget(self.close_button)

        # export_row's own container gets a FIXED height, computed from
        # every widget in it up front - including the Template separator/
        # label/combo, which start out hidden (see above). A QLayout
        # excludes a hidden widget from its row's height calculation
        # entirely, but QWidget.sizeHint() itself is unaffected by
        # visibility - so without this, the row (and via minimumSizeHint,
        # the whole dialog) grew a few pixels taller the instant Template
        # first appeared, since the Template combo's own sizeHint is
        # slightly taller than the checkbox/buttons alongside it. Fixing
        # the row's height from the tallest candidate up front, whether
        # currently visible or not, closes that gap - same "reserve the
        # space so nothing else has to reflow" reasoning as program_editor_
        # window.py's own scrollbar_container (see AGENTS.md), just scoped
        # to height here instead of visibility.
        self._export_row_container = QWidget()
        # zero margins - export_row used to be a NESTED layout (added via
        # layout.addLayout(export_row), which Qt gives 0 contents margins
        # by default); making it a QWidget's own top-level layout instead
        # would otherwise pick up that widget's default top-level margins
        # (~9-11px a side under Fusion) on top of the fixed height below,
        # silently eating into the row's usable height and clipping
        # Export/Cancel/Close at the bottom - confirmed from a screenshot,
        # not just reasoned about.
        export_row.setContentsMargins(0, 0, 0, 0)
        self._export_row_container.setLayout(export_row)
        export_row_height = max(
            export_row.itemAt(i).widget().sizeHint().height()
            for i in range(export_row.count())
        )
        self._export_row_container.setFixedHeight(export_row_height)

        layout = QVBoxLayout(self)
        layout.addLayout(zoom_row)
        layout.addWidget(self.waveform)
        layout.addWidget(self._scrollbar_container)
        layout.addWidget(self.info_label)
        layout.addLayout(equal_row)
        layout.addLayout(transient_row)
        layout.addLayout(name_row)
        layout.addWidget(self._export_row_container)
        # explicit belt-and-suspenders alongside the waveform's own
        # Expanding policy/info_label's own Fixed policy above - makes it
        # unambiguous that ALL surplus vertical space from resizing this
        # dialog goes to the waveform and nowhere else, resizing the
        # window otherwise leaves every other row pinned exactly where it
        # is
        layout.setStretchFactor(self.waveform, 1)
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
        # used to also append "- lengths (frames): 441, 2203, ..." (one
        # number per slice) - harmless at a handful of slices, but with
        # enough of them (Detect Transients at high sensitivity easily
        # produces 30+) the label's own wrapped text grew tall enough to
        # visibly push every row below it down the window - the opposite
        # of "fixed positioning," and confirmed as a real usability
        # problem, not just a cosmetic one. Slice count alone is what a
        # user actually needs at a glance; per-slice lengths are already
        # visible on the waveform itself (each region's own width).
        count = len(self.waveform.slice_bounds())
        self.info_label.setText(f"{count} slice{'s' if count != 1 else ''}")

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

    # --- Sensitivity slider: live transient detection -------------------------

    def _on_transient_sensitivity_changed(self, value):
        # no confirmation dialog, unlike Equal Slices - this fires on every
        # tick of a drag, so it fully OWNS the marker set whenever it's
        # non-zero rather than asking each time (see this slider's own
        # construction comment). value == 0 goes through markers_from_flux
        # too (rather than short-circuiting here) so cached/None flux
        # (silence, too-short buffer - see compute_flux) still behaves
        # identically: no markers, full stop.
        self.transient_sensitivity_value_label.setText(f"{value}%")
        candidates = transient_detection.markers_from_flux(
            self._transient_flux,
            self._transient_window_frames,
            value,
            len(self._samples),
        )
        # markers_from_flux works over the WHOLE buffer, not [start, end] -
        # it has no idea where the trim handles currently sit (same reason
        # equal_slice_markers is instead called with waveform.start()/end()
        # directly above) - filter to genuine interior candidates here so a
        # loud lead-in before Start doesn't silently produce a marker
        # nobody asked for outside the trimmed region. slice_bounds() would
        # drop these anyway, but set_markers()/slice_markers() round-trips
        # the raw list, so filtering here keeps that list honest too.
        start, end = self.waveform.start(), self.waveform.end()
        markers = [m for m in candidates if start < m < end]
        snapped = [
            sample_slicing.find_nearest_zero_crossing(self._samples, m)
            for m in markers
        ]
        self.waveform.set_markers(snapped)

    # --- export --------------------------------------------------------------

    @staticmethod
    def _default_export_confirm_message(slice_count, names):
        if slice_count == 1:
            # no slice markers at all - a plain trim/crop of [start, end],
            # not "1 slice" (see slice_export_names' own comment on why
            # this gets no numeric suffix either)
            return (
                f'Send "{names[0]}" to the sampler as a new sample? This '
                "can take a while and will freeze the interface."
            )
        return (
            f'Send {slice_count} slices to the sampler as new samples '
            f'("{names[0]}".."{names[-1]}")? This can take a while and will '
            "freeze the interface."
        )

    def _confirm_export(self):
        # slice_count == 1 (no interior markers at all) is a valid, if
        # degenerate, export - the whole [start, end] region as a single
        # plain trim/crop rather than an error case. See this window's own
        # class docstring on doubling as a trimmer, slice_export_names'
        # own comment on why that case gets no "-1" suffix, and
        # core.sample_slicing.slice_samples' own docstring, which already
        # documented "no markers -> one slice covering the whole region"
        # as a real, intended case long before anything actually used it.
        markers = self.waveform.slice_markers()
        slice_count = len(markers) + 1

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

        create_program = (
            self.create_program_checkbox is not None
            and self.create_program_checkbox.isChecked()
        )
        program_name = None
        template_index = None
        if create_program:
            # same 99-keygroup ceiling _handle_create_keygroup guards
            # hardware-side (matches GROUPS's own declared 1..99 range) -
            # checked here too so a doomed export never even starts
            # (see ProgramEditorWindow._create_program_from_slices)
            if slice_count > 99:
                QMessageBox.warning(
                    self,
                    "Export Slices",
                    f"Can't create a program - {slice_count} keygroups "
                    "requested, but a program can only hold 99. Uncheck "
                    '"Also create a new program..." or reduce the number '
                    "of slices.",
                )
                return
            program_name = base_name.strip().upper()[:NAME_LENGTH]
            template_index = self.template_program_combo.currentIndex()
            # same "PDATA silently overwrites a same-named program" hazard
            # ProgramEditorWindow._confirm_duplicate_program already guards
            # against - re-queried fresh rather than a stale snapshot, same
            # reasoning as the sample-name collision check just above
            if program_name in set(self._program_names_provider()):
                QMessageBox.warning(
                    self,
                    "Export Slices",
                    f'A program named "{program_name}" already exists. '
                    "Creating a program with the same name would overwrite "
                    "it on the sampler.\n\nPick a different base name, or "
                    "rename/delete the existing program, then try again.",
                )
                return

        confirm_message = self._export_confirm_message(slice_count, names)
        if create_program:
            template_name = self.template_program_combo.currentText()
            confirm_message += (
                f'\n\nAlso create a new program "{program_name}" with '
                f"{slice_count} keygroup{'s' if slice_count != 1 else ''} "
                f'(cloned from "{template_name}"), mapping each slice to '
                "its own key starting at C1, in Const Pitch / one-shot mode."
            )

        reply = QMessageBox.question(
            self,
            "Export Slices",
            confirm_message,
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
        if self.cancel_export_button is not None:
            self.cancel_export_button.setVisible(True)
        self.export_progress.setVisible(True)
        self.export_progress.setValue(0)
        self.status_label.setText(
            f'Sending "{names[0]}"...' if slice_count == 1
            else f"Exporting {slice_count} slices..."
        )
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
            if success and create_program:
                # runs AFTER every slice has actually landed as a resident
                # sample - SNAME1 addresses a keygroup's zone by NAME, so
                # this step only ever needs `names`, never a sample index
                self._on_export_status(f'Creating program "{program_name}"...')
                prog_success, prog_message = self._create_program_callback(
                    names,
                    template_index,
                    program_name,
                    self._on_export_progress,
                    self._on_export_status,
                )
                success = success and prog_success
                message = f"{message}\n{prog_message}"
        finally:
            self._exporting = False
            if self.cancel_export_button is not None:
                self.cancel_export_button.setVisible(False)
            self._set_controls_enabled(True)
            self.export_progress.setVisible(False)

        self.status_label.setText(message)
        if success:
            self._dirty = False  # this exact marker layout is now on the sampler
            self.export_succeeded = True
            QMessageBox.information(self, "Export Slices", message)
        else:
            QMessageBox.warning(self, "Export Slices", message)

    def _on_cancel_export_shortcut(self):
        if not self._exporting or self._cancel_callback is None:
            return
        self.status_label.setText("Cancelling...")
        QApplication.processEvents()
        self._cancel_callback()

    def _on_export_progress(self, current, total):
        percent = int(100 * current / total) if total else 0
        self.export_progress.setValue(max(0, min(100, percent)))
        QApplication.processEvents()

    def _on_export_status(self, text):
        self.status_label.setText(text)
        QApplication.processEvents()

    def _mark_dirty(self):
        self._dirty = True

    def _on_create_program_toggled(self, checked):
        # the Template combo (and its separator/label) only exist visually
        # once the checkbox is checked - see this window's own construction
        # comment
        self._template_program_separator.setVisible(checked)
        self._template_program_label.setVisible(checked)
        self.template_program_combo.setVisible(checked)
        self.template_program_combo.setEnabled(checked)

    def _set_controls_enabled(self, enabled):
        for widget in (
            self.waveform,
            self.zoom_out_button,
            self.zoom_in_button,
            self.zoom_fit_button,
            self.scrollbar,
            self.equal_count_spin,
            self.equal_slices_button,
            self.transient_sensitivity_slider,
            self.name_edit,
            self.bit_depth_combo,
            self.rate_combo,
            self.export_button,
            self.close_button,
        ):
            widget.setEnabled(enabled)
        if self.create_program_checkbox is not None:
            self.create_program_checkbox.setEnabled(
                enabled and self._create_program_checkbox_allowed
            )
            # the combo's own enabled state is otherwise driven by the
            # checkbox's toggled signal - re-enabling unconditionally here
            # would un-grey it even while the checkbox is unchecked
            self.template_program_combo.setEnabled(
                enabled and self.create_program_checkbox.isChecked()
            )

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
