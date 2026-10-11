"""
The pictures for tools/make_screenshots.py: one @shot function each. A recipe builds the window it shows (against the demo fakes,
with invented sounds), brings it to the state the manual describes, and returns the widget (or QPixmap) to save.
Everything here may be edited freely when the UI changes: that is the point of having it.
"""

import math

import screenshot_kit as kit  # noqa: F401  (first: keeps the isolated config/log folders in place)
from screenshot_kit import noise_burst, shot, tone

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QPixmap

DASHBOARD_SIZE = (1030, 640)

QUEUE_SOUNDS = [
    ("808 HAT OPEN", dict(samples=noise_burst(0.5, 9, 1))),
    ("TOMZZ", dict(samples=tone(0.6, 110, 5))),
    ("ST PAD", dict(samples=[s for t in tone(1.0, 262, 1.5) for s in (t, t * 0.6)], channels=2)),
]
HARDWARE_SAMPLES = ["KICK", "SNARE", "HAT CLOSED", "HAT OPEN", "CLAP", "BASS LOW", "PAD", "SINE", "SQUARE"]


PORT = "Demo MIDI Interface"


def _connect_demo_ports(window):
    """Look like a MIDI interface is selected (so Open Editor, Refresh and Receive are enabled), without opening a real port."""
    mm = window.midi_manager
    mm.input_name = mm.output_name = PORT
    dash = window.dashboard_view
    dash._update_device_type_ui()
    dash._update_open_editor_enabled()
    dash._update_queue_buttons_state()


def _dashboard(ctx, queue=True, loaded=True, size=DASHBOARD_SIZE):
    """The real main window, with a couple of files in the queue and a sampler's sample list, as after a refresh."""
    from ui.main_window import ApplicationWindow

    window = ApplicationWindow()
    window.menuBar().setVisible(False)  # (the manual's pictures are of the window, which on a Mac has no menu bar of its own)
    ctx.show(window, size)
    _connect_demo_ports(window)
    dash = window.dashboard_view
    if queue:
        dash.on_files_dropped([ctx.wav(name.lower().replace(" ", "_"), **sound) for name, sound in QUEUE_SOUNDS])
        # the queue shows the file name, and a dropped copy keeps the original name
        for i, (name, _sound) in enumerate(QUEUE_SOUNDS):
            row = dash.list_local.itemWidget(dash.list_local.item(i))
            field = row.findChild(type(dash._active_edit_field) if dash._active_edit_field else __import__("PySide6.QtWidgets", fromlist=["QLineEdit"]).QLineEdit)
            if field is not None:
                field.setText(name)
                field.setCursorPosition(0)
    if loaded:
        dash.on_sample_list_updated(HARDWARE_SAMPLES)
        dash.on_memory_status_updated({"max_num_samp_words": 5242880, "num_words_free": 5103696, "num_blocks_free": 982})
    _connect_demo_ports(window)  # (again: adding rows re-evaluates what's enabled)
    ctx.pump(settle=0.2)
    return window


@shot("main-window-dark", "The Transfer Dashboard with files queued and a sampler's samples listed (dark theme)")
def main_window_dark(ctx):
    return _dashboard(ctx)


@shot("main-window-light", "The same, in the light theme", scheme="light")
def main_window_light(ctx):
    return _dashboard(ctx)


@shot("file-queue", "The logo and File Transfer Queue part of the window")
def file_queue(ctx):
    window = _dashboard(ctx, loaded=False, size=(1030, 470))
    dash = window.dashboard_view
    whole = dash.grab()
    scale = whole.devicePixelRatio()
    # from the window's corner to the bottom-right of the queue list, in the dashboard's own coordinates
    right = dash.list_local.mapTo(dash, QPoint(dash.list_local.width(), dash.list_local.height()))
    rect = QRect(0, 0, right.x() + 15, right.y() + 8)
    return whole.copy(QRect(int(rect.x() * scale), int(rect.y() * scale), int(rect.width() * scale), int(rect.height() * scale)))


# ============================================================================== the Program Editor (demo bridge)
EDITOR_SIZE = (1000, 1150)  # tall enough that the Programs page's Modulation card isn't cut off


class _Host:
    """What ProgramEditorWindow expects of the Transfer Dashboard that opened it: nothing here has one."""

    class _Controller:
        def is_transfer_busy(self):
            return False

        def cancel_transfer(self):
            pass

    sampler_controller = _Controller()

    def show(self):  # (closing the editor normally brings the dashboard back)
        pass


def _editor(ctx, tab: int, size=EDITOR_SIZE):
    """The real Program Editor on s3ked's in-memory DemoBridge, loaded and on tab `tab` (0 Multi, 1 Programs, 2 Samples, 3 Global)."""
    from s3ked.demo import DemoBridge
    from ui.program_editor_window import ProgramEditorWindow

    editor = ProgramEditorWindow(_Host(), bridge=DemoBridge())
    editor.menuBar().setVisible(False)

    def stop_worker():
        editor._worker.stop()
        editor._worker.wait()

    ctx.on_cleanup(stop_worker)
    ctx.show(editor, size)
    ctx.pump(lambda: editor.program_list.count() > 0, timeout=15)
    editor._worker.wait_until_idle()
    ctx.pump(settle=0.3)
    editor.main_tabs.setCurrentIndex(tab)
    ctx.pump(settle=0.3)
    return editor


@shot("editor-program-page", "Program Editor, Programs tab: a program selected (Save/Load buttons, Global tab in the tab bar)")
def editor_program_page(ctx):
    editor = _editor(ctx, 1)
    editor.program_list.setCurrentRow(0)
    ctx.pump(lambda: editor.keygroup_list.count() > 0, timeout=10)
    editor._worker.wait_until_idle()
    ctx.pump(settle=0.4)
    return editor


@shot("editor-keygroup-page", "Program Editor, Programs tab: a keygroup selected (Mute Group in the Range card, Vel. start on a zone)")
def editor_keygroup_page(ctx):
    editor = _editor(ctx, 1, size=(1000, 1080))
    editor.program_list.setCurrentRow(0)
    ctx.pump(lambda: editor.keygroup_list.count() > 0, timeout=10)
    editor.keygroup_list.setCurrentRow(0)
    editor._worker.wait_until_idle()
    ctx.pump(settle=0.5)
    return editor


@shot("editor-multi-page", "Program Editor, Multi tab")
def editor_multi_page(ctx):
    editor = _editor(ctx, 0, size=(1000, 790))
    editor._worker.wait_until_idle()
    ctx.pump(settle=0.4)
    return editor


@shot("editor-sample-page", "Program Editor, Samples tab: a sample's waveform loaded, with its loop")
def editor_sample_page(ctx):
    editor = _editor(ctx, 2, size=(1000, 920))
    ctx.pump(lambda: editor.sample_list_widget.count() > 0, timeout=10)
    editor.sample_list_widget.setCurrentRow(0)
    ctx.pump(settle=0.3)
    editor._load_sample_waveform()  # what double-clicking the waveform placeholder does
    editor._worker.wait_until_idle()
    ctx.pump(lambda: editor.waveform_view.has_waveform(), timeout=20, settle=0.6)
    return editor


GLOBAL_VALUES = {
    "external_controller": 1, "tune_semitones": 0, "tune_cents": 0, "output_level": 0, "program_change_channel": 17,
    "play_note": 60, "play_channel": 1, "play_velocity": 100, "scsi_disk_id": 0, "scsi_sector": 1, "scsi_local_id": 7,
}


@shot("editor-global-page", "Program Editor, Global tab (S2000/S3000 GLOBAL-page settings)")
def editor_global_page(ctx):
    editor = _editor(ctx, 3, size=(1000, 560))
    editor._on_global_settings_loaded(GLOBAL_VALUES)  # (the demo bridge has no misc registers, so feed it what a real read gives)
    ctx.pump(settle=0.4)
    return editor


# ============================================================================== dialogs on the dashboard
def _break_samples(rate=44100):
    """Two seconds of an invented drum break: hits of different weight and length at uneven spacing, so the slicer has something to find."""
    import random

    rng = random.Random(7)
    out = [0.0] * int(2.0 * rate)
    for start, level, decay, kind in [
        (0.00, 1.0, 14, "kick"), (0.25, 0.8, 22, "snare"), (0.50, 0.9, 14, "kick"), (0.62, 0.5, 40, "hat"),
        (0.75, 0.85, 22, "snare"), (1.00, 1.0, 14, "kick"), (1.12, 0.5, 40, "hat"), (1.25, 0.8, 22, "snare"),
        (1.375, 0.6, 18, "kick"), (1.50, 0.9, 22, "snare"), (1.75, 0.5, 40, "hat"), (1.85, 0.7, 12, "snare"),
    ]:
        first = int(start * rate)
        for i in range(min(int(0.3 * rate), len(out) - first)):
            t = i / rate
            body = math.sin(2 * math.pi * (60 + 120 * math.exp(-t * 30)) * t) if kind == "kick" else rng.random() * 2 - 1
            out[first + i] += level * 0.8 * math.exp(-decay * t) * body
    peak = max(abs(s) for s in out)
    return [s / peak * 0.9 for s in out]


def _queued(ctx, name="super cool pad", samples=None):
    """The dashboard with one file in the queue, returning (window, list item, its name field)."""
    from PySide6.QtWidgets import QLineEdit

    window = _dashboard(ctx, queue=False, loaded=True)
    dash = window.dashboard_view
    dash.on_files_dropped([ctx.wav(name.replace(" ", "_"), samples or tone(1.0, 262, 1.5))])
    item = dash.list_local.item(0)
    field = dash.list_local.itemWidget(item).findChild(QLineEdit)
    field.setText(name)
    return window, item, field


def _keep_modal_dialogs_open(ctx, cls):
    """Make `cls.exec()` return at once (instead of blocking in a modal loop) so a recipe can photograph the dialog it opened."""
    shown = []
    original = cls.exec
    cls.exec = lambda self: shown.append(self) or 0
    ctx.on_cleanup(lambda: setattr(cls, "exec", original))
    return shown


@shot("sample-settings", "The per-file Sample Settings dialog (name, bit depth, sample rate, mono) from a queue row's Edit button")
def sample_settings(ctx):
    from ui.sample_settings_dialog import SampleSettingsDialog

    window, item, field = _queued(ctx)
    shown = _keep_modal_dialogs_open(ctx, SampleSettingsDialog)
    window.dashboard_view.open_edit_dialog(item, field)
    return ctx.show(shown[0])


@shot("transmission-settings", "The Transmission Settings dialog (bit depth, sample rate, mono for everything in the queue)")
def transmission_settings(ctx):
    from ui.sample_settings_dialog import SampleSettingsDialog

    window = _dashboard(ctx)
    shown = _keep_modal_dialogs_open(ctx, SampleSettingsDialog)
    window.dashboard_view.open_global_settings_dialog()
    return ctx.show(shown[0])


@shot("sample-info", "The Sample Info dialog for a sample on the sampler")
def sample_info(ctx):
    from ui.sample_info_dialog import SampleInfoDialog

    window = _dashboard(ctx)
    shown = _keep_modal_dialogs_open(ctx, SampleInfoDialog)
    window.dashboard_view.on_sample_info_received(
        {"name": "BELL SOFT", "sample_number": 4, "bit_depth": 16, "sample_rate": 22050, "sample_length": 35280, "root_key": 69, "detune": 0.0}
    )
    return ctx.show(shown[0])


def _slice_editor(ctx, setup):
    """The Slice Editor on a queued file (as opened from a queue row's Edit button), after `setup(dialog)`."""
    from ui import slice_editor_window as sew

    window, item, field = _queued(ctx, name="break", samples=_break_samples())
    shown = _keep_modal_dialogs_open(ctx, sew.SliceEditorWindow)
    window.dashboard_view._open_slice_editor(item, field)
    dialog = shown[0]
    ctx.on_cleanup(lambda: setattr(dialog, "_dirty", False))  # (so closing it doesn't ask whether to discard the markers)
    ctx.show(dialog, (900, 600))
    setup(dialog)
    ctx.pump(settle=0.4)
    return dialog


@shot("slice-editor-manual", "The Slice Editor with a few markers placed by hand")
def slice_editor_manual(ctx):
    def place(dialog):
        total = len(dialog._samples)
        dialog.waveform.set_markers([int(total * f) for f in (0.125, 0.25, 0.31, 0.375, 0.5, 0.625, 0.75, 0.875)])
        dialog.name_edit.setText("CHOPPED")

    return _slice_editor(ctx, place)


@shot("slice-editor-auto", "The Slice Editor after the Sensitivity slider found the hits")
def slice_editor_auto(ctx):
    def detect(dialog):
        dialog.transient_sensitivity_slider.setValue(87)
        dialog.name_edit.setText("CHOPPYBREAK")

    return _slice_editor(ctx, detect)


# ============================================================================== Settings and the MIDI tests
def _settings_dialog(ctx, tab: int):
    from controller.sampler_controller import SamplerController
    from core.midi_manager import MidiManager
    from PySide6.QtWidgets import QTabWidget
    from ui.settings_dialog import MidiSettingsDialog

    mm = MidiManager()
    mm.list_inputs = lambda: [PORT, "IAC Driver Bus 1"]  # (no real ports are looked at or opened)
    mm.list_outputs = lambda: [PORT, "IAC Driver Bus 1"]
    mm.input_name = mm.output_name = PORT
    controller = SamplerController(mm)
    dialog = MidiSettingsDialog(mm, controller)
    ctx.show(dialog)
    dialog.findChild(QTabWidget).setCurrentIndex(tab)
    if tab == 1:  # what the Hardware Test shows once it has found a sampler
        dialog.id_results_label.setText(
            dialog._format_stat_result(
                {"version_string": "2.00", "num_blocks_free": 982, "max_num_blocks": 1006, "num_words_free": 5103696, "max_num_samp_words": 5242880}
            )
        )
    ctx.pump(settle=0.3)
    return dialog


@shot("settings-audio-midi", "The Settings dialog's Settings tab (MIDI ports, Device ID, Sampler Type, audio output, theme)")
def settings_audio_midi(ctx):
    return _settings_dialog(ctx, 0)


@shot("settings-troubleshooting", "The Settings dialog's Troubleshooting tab, after a Hardware Test found a sampler")
def settings_troubleshooting(ctx):
    return _settings_dialog(ctx, 1)


@shot("midi-interface-test-results", "The Loopback Test Results window for a clean run")
def midi_interface_test_results(ctx):
    from ui.loopback_results_dialog import LoopbackResultsDialog

    results = [(size, True, "") for size in (8, 32, 64, 127, 256, 512, 1024, 1536, 2048, 2560, 3072)]
    return ctx.show(LoopbackResultsDialog(results, PORT, PORT))


@shot("stray-midi-replies-dialog", "The \"Stray MIDI replies\" warning the Program Editor shows")
def stray_midi_replies_dialog(ctx):
    from PySide6.QtWidgets import QLabel, QMessageBox

    editor = _editor(ctx, 1, size=(1000, 700))
    shown = []
    original = QMessageBox.warning

    def capture(parent, title, text, *args, **kwargs):  # (the real call is modal: take its title and text and show a box we can photograph)
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=parent)
        # offscreen's tiny 800x600 "screen" makes Qt wrap a message box's text far narrower than on a real display
        box.layout().setColumnMinimumWidth(2, 360)
        shown.append(box)
        return QMessageBox.StandardButton.Ok

    QMessageBox.warning = staticmethod(capture)
    ctx.on_cleanup(lambda: setattr(QMessageBox, "warning", original))
    editor._on_stray_replies_detected(3)  # the app's own handler, so the picture always carries the app's own wording
    box = ctx.show(shown[0])
    label = box.findChild(QLabel, "qt_msgbox_label")  # (a message box only wraps its text once shown, so it can only be measured now)
    label.setMinimumHeight(label.heightForWidth(label.width()))
    box.setFixedSize(box.layout().totalSizeHint())
    ctx.pump(settle=0.2)
    return box


# ============================================================================== the Yamaha A4000/A5000 editor (demo fake)
def _yamaha_editor(ctx, size=(1150, 760)):
    """The Yamaha editor on core.demo_a4000.FakeA4000 (the real session code over fake MIDI ports, as tools/a4000_demo.py does)."""
    import a4000_demo
    from controller.sampler_controller import SamplerController
    from core import sampler_models
    from core.demo_a4000 import FakeA4000
    from core.midi_manager import MidiManager
    from PySide6.QtWidgets import QMainWindow
    from ui.yamaha_program_editor import YamahaProgramEditorWindow

    fake = FakeA4000()
    a4000_demo.add_demo_samples(fake)
    mm = MidiManager()
    mm.raw_output = a4000_demo._FakePort(fake, mm)
    mm.input_name, mm.output_name = "Demo A4000 (in)", "Demo A4000 (out)"
    controller = SamplerController(mm)
    controller.set_device_type(sampler_models.YAMAHA_A4000)
    host = QMainWindow()  # (what the dashboard window would be)
    ctx.on_cleanup(host.close)
    editor = YamahaProgramEditorWindow(host, controller)
    editor.menuBar().setVisible(False)
    ctx.show(editor, size)
    return editor


@shot("yamaha-programs-tab", "Yamaha A4000/A5000 editor, Programs tab: a program with its assigned samples")
def yamaha_programs_tab(ctx):
    editor = _yamaha_editor(ctx)
    ctx.pump(lambda: editor._program_numbers and not editor._scanning, timeout=20, settle=0.5)
    return editor


@shot("yamaha-samples-tab", "Yamaha A4000/A5000 editor, Samples tab: a looping sample with its waveform loaded and its markers")
def yamaha_samples_tab(ctx):
    editor = _yamaha_editor(ctx)
    ctx.pump(lambda: editor._program_numbers and not editor._scanning, timeout=20, settle=0.3)
    editor.main_tabs.setCurrentIndex(editor._samples_tab_index)
    tab = editor.samples_tab
    ctx.pump(lambda: "DEMO LOOP" in tab.sample_names(), timeout=15)
    tab.select_sample("DEMO LOOP")
    ctx.pump(lambda: tab.waveform_view.has_header(), timeout=15, settle=0.3)
    tab._load_audio()  # what double-clicking the waveform does
    ctx.pump(lambda: tab._audio_samples is not None and tab._loading_name is None, timeout=30, settle=0.6)
    ctx.on_cleanup(lambda: tab.refresh())  # (drops anything still in flight before the window goes)
    return editor
