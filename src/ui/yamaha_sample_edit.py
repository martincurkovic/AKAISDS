"""The edit buttons of the Yamaha Samples tab: Trim / Reverse / Fade / Normalise / Filter, and the Slice Editor.

Laid out and worded like the S3000 editor's, with one difference that matters: the A4000 can neither delete nor overwrite a sample
over MIDI (no known opcode; a sample re-sent under its own name has every parameter reset - core/yamaha_load.py), so an edit makes a
NEW sample - the edited audio under a name derived from the original ("DRUM LOOP TRIM") with the original's key, tuning, loop mode and
markers carried over (core/yamaha_edit.py) - and the original is never touched. Each confirmation says so. The new sample is loaded
through the unit's native bulk load (controller/yamaha_transfers.py: wave dump(s) + sample dump, then read back to check), a
stereo sample stays stereo, and the tab's sample list is refreshed afterwards with the copy selected.

The Slice Editor (ui/slice_editor_window.py, the S3000 editor's own dialog) opens on the loaded audio; its Export sends every slice
as a new sample the same way (a stereo sample's slices are stereo: the dialog slices the other channel at the same frames). There is no
"create a program" step - the A4000 editor can't create programs.

Everything needs the audio of the shown sample in memory (double-click the waveform), like the S3000 editor.
"""

import os
import shutil
import tempfile

from PySide6.QtCore import QEventLoop, QObject
from PySide6.QtWidgets import QMessageBox

from core import debug_log
from core import sample_editing as se
from core import yamaha_edit as ye
from core import yamaha_load as yl
from core import yamaha_params as yp
from ui.filter_sample_dialog import FilterSampleDialog
from ui.slice_editor_window import SliceEditorWindow


def _log(message):
    debug_log.get_logger().info(f"YamahaEditor: {message}")


_NOT_CHANGED = (
    "\n\nThe original sample is not changed - the A4000 can't delete or overwrite samples over MIDI, so remove it on the "
    "front panel if you don't need it."
)


class YamahaSampleEditor(QObject):
    def __init__(self, tab):
        super().__init__(tab)
        self._tab = tab
        self._temp_dir = None
        self._select_name = None
        buttons = tab.edit_buttons
        buttons["trim"].clicked.connect(self.trim)
        buttons["reverse"].clicked.connect(self.reverse)
        buttons["fade"].clicked.connect(self.fade)
        buttons["normalise"].clicked.connect(self.normalise)
        buttons["filter"].clicked.connect(self.filter)
        tab.slice_button.clicked.connect(self.open_slicer)

    # -- what the edits act on ------------------------------------------------------------------------------

    def _context(self):
        """The shown sample's audio, markers and parameters - or None (after saying why in the status bar)."""
        tab = self._tab
        name = tab._selected
        data = tab._cache.get(name)
        if name is None or data is None or tab._audio_name != name or tab._audio_samples is None:
            tab.status_message.emit("Double-click the waveform to load the sample's audio first")
            return None
        if tab._edit_busy or tab._controller.is_transfer_busy():
            tab.status_message.emit("A transfer is already in progress - wait for it to finish")
            return None
        channels = [list(tab._audio_samples)]
        if tab._audio_samples_right is not None:
            channels.append(list(tab._audio_samples_right))
        # not .markers(): the transforms assume the loop sits inside [start, end], which a non-looping sample's markers needn't
        m = tab.waveform_view.markers_with_loop_in_range()
        return {
            "name": name,
            "data": data,
            "channels": channels,
            "markers": (m["start"], m["loop_start"], m["loop_end"], m["end"]),
            "rate": yp.extract(yp.get("sample", "sampling_frequency_l"), data),
            "frames": len(channels[0]),
        }

    def _confirm(self, title, text):
        answer = QMessageBox.question(
            self._tab, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes
        )
        return answer == QMessageBox.StandardButton.Yes

    # -- the five edits ---------------------------------------------------------------------------------------

    def trim(self):
        ctx = self._context()
        if ctx is None:
            return
        start, _ls, _le, end = ctx["markers"]
        if start == 0 and end == ctx["frames"] - 1:
            self._tab.status_message.emit("Nothing to trim - Start/End already cover the whole sample")
            return
        name = ye.copy_name(ctx["name"], ye.EDITS["trim"][0])
        if self._confirm(
            "Trim Sample",
            f'Make "{name}" from "{ctx["name"]}" trimmed to the Start/End markers ({start:,}-{end:,} of {ctx["frames"]:,} frames)?'
            + _NOT_CHANGED,
        ):
            self._run("trim", ctx)

    def reverse(self):
        ctx = self._context()
        if ctx is None:
            return
        if ctx["frames"] <= 1:
            self._tab.status_message.emit("Nothing to reverse - the sample is too short")
            return
        name = ye.copy_name(ctx["name"], ye.EDITS["reverse"][0])
        if self._confirm("Reverse Sample", f'Make "{name}" from "{ctx["name"]}" with its audio reversed?' + _NOT_CHANGED):
            self._run("reverse", ctx)

    def fade(self):
        ctx = self._context()
        if ctx is None:
            return
        start, _ls, _le, end = ctx["markers"]
        # the fades are the lead-in/lead-out AROUND [start, end] (core/sample_editing.fade_in_out_samples)
        if start == 0 and end == ctx["frames"] - 1:
            self._tab.status_message.emit("Nothing to fade - Start/End already cover the whole sample")
            return
        name = ye.copy_name(ctx["name"], ye.EDITS["fade"][0])
        if self._confirm(
            "Fade Sample",
            f'Make "{name}" from "{ctx["name"]}", fading in from frame 0 up to Start ({start:,}) and out from End ({end:,}) '
            "to the last frame?" + _NOT_CHANGED,
        ):
            self._run("fade", ctx)

    def normalise(self):
        ctx = self._context()
        if ctx is None:
            return
        peak = max((abs(v) for ch in ctx["channels"] for v in ch), default=0)
        if peak == 0:
            self._tab.status_message.emit("Nothing to normalise - the sample is silent")
            return
        if peak >= se._MAX_AMPLITUDE:
            self._tab.status_message.emit("The sample is already normalised")
            return
        name = ye.copy_name(ctx["name"], ye.EDITS["normalise"][0])
        if self._confirm(
            "Normalise Sample",
            f'Make "{name}" from "{ctx["name"]}", gained up so its loudest point hits maximum amplitude'
            + (" (one gain for both channels)?" if len(ctx["channels"]) > 1 else "?")
            + _NOT_CHANGED,
        ):
            self._run("normalise", ctx)

    def filter(self):
        ctx = self._context()
        if ctx is None:
            return
        # the dialog previews the left (or only) channel; the chosen filter is then run over every channel
        dialog = FilterSampleDialog(self._tab, ctx["name"], ctx["channels"][0], ctx["rate"])
        if not dialog.exec():
            return
        name = ye.copy_name(ctx["name"], ye.EDITS["filter"][0])
        if not self._confirm("Filter Sample", f'Make "{name}" from "{ctx["name"]}" with this filter applied?' + _NOT_CHANGED):
            return
        options = dict(
            highpass_enabled=dialog.highpass_enabled(),
            highpass_cutoff_hz=dialog.highpass_cutoff_hz(),
            highpass_slope_db_per_octave=dialog.highpass_slope_db_per_octave(),
            lowpass_enabled=dialog.lowpass_enabled(),
            lowpass_cutoff_hz=dialog.lowpass_cutoff_hz(),
            lowpass_slope_db_per_octave=dialog.lowpass_slope_db_per_octave(),
        )
        self._run("filter", ctx, options)

    def _run(self, kind, ctx, filter_options=None):
        suffix, label = ye.EDITS[kind]
        tab = self._tab
        try:
            channels, markers = ye.apply_edit(
                kind, ctx["channels"], ctx["markers"], framerate=ctx["rate"], filter_options=filter_options
            )
            name = ye.copy_name(ctx["name"], suffix)
            params = ye.params_for_copy(ctx["data"], markers)
            temp_dir = tempfile.mkdtemp(prefix="akaisds_yamaha_edit_")
            path = os.path.join(temp_dir, "edit.wav")
            ye.write_wav(path, channels, ctx["rate"])
        except Exception as e:  # the pure transform/WAV step runs before anything touches the unit
            _log(f"{label} of {ctx['name']!r} failed before sending: {e!r}")
            tab.status_message.emit(f"Couldn't {label.lower()} the sample: {e}")
            return
        entry = {"filepath": path, "name": name, "sample_rate": ctx["rate"], "mono": False, "params": params}
        _log(f"{label} of {ctx['name']!r} -> new sample {name!r} ({len(channels[0]):,} frames, {len(channels)} ch)")
        expected = yl.unique_name(yl.sample_name_for(name), tab._names)
        self._start_send([entry], temp_dir, expected)

    # -- sending ---------------------------------------------------------------------------------------------

    def _start_send(self, entries, temp_dir, select_name):
        tab = self._tab
        controller = tab._controller
        self._temp_dir, self._select_name = temp_dir, select_name
        controller.transfer_finished.connect(self._on_send_finished)
        tab._set_edit_busy(True)
        if not controller.send_file_queue(entries):
            controller.transfer_finished.disconnect(self._on_send_finished)
            tab._set_edit_busy(False)
            self._cleanup()
            return False
        return True

    def _on_send_finished(self, ok):
        tab = self._tab
        tab._controller.transfer_finished.disconnect(self._on_send_finished)
        self._cleanup()
        tab._set_edit_busy(False)
        landed = tab._controller.yamaha_loaded_names()  # the name it REALLY got (a clash adds a number)
        select, self._select_name = (landed[-1] if landed else self._select_name), None
        tab.samples_changed.emit(select if ok else "")

    def _cleanup(self):
        if self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None

    # -- the Slice Editor --------------------------------------------------------------------------------------

    def open_slicer(self):
        ctx = self._context()
        if ctx is None:
            return
        tab = self._tab
        data = ctx["data"]
        g = lambda key: yp.extract(yp.get("sample", key), data)  # noqa: E731
        self._slice_params = {
            "original_key_l": g("original_key_l"),
            "original_key_r": g("original_key_r"),
            "coarse_tune": g("coarse_tune"),
            "fine_tune_l": g("fine_tune_l"),
            "fine_tune_r": g("fine_tune_r"),
        }
        dialog = SliceEditorWindow(
            tab,
            ctx["name"],
            ctx["channels"][0],
            ctx["rate"],
            g("original_key_l"),
            0,
            0,
            lambda: list(tab._names),
            self._export_slices,
            cancel_callback=tab._controller.cancel_transfer,
            extra_channels=ctx["channels"][1:] or None,
            hide_bit_depth=True,
        )
        dialog.exec()
        if dialog.export_succeeded:
            tab.samples_changed.emit("")

    def _export_slices(
        self, names, slices, framerate, bit_depth, sample_rate, spitch, stuno, shlto,
        progress_callback, status_callback, busy_callback=None, extra_slices=None,
    ):
        """The hardware half of the Slice Editor (called synchronously from its Export button, which blocks the way every send on
        the S3000 editor's page does): every slice becomes a new sample, in ONE send queue. Returns (ok, message)."""
        tab = self._tab
        controller = tab._controller
        if controller.is_transfer_busy():
            return False, "Export failed - a transfer is already in progress"
        temp_dir = tempfile.mkdtemp(prefix="akaisds_yamaha_slices_")
        try:
            entries = []
            for index, (name, slice_samples) in enumerate(zip(names, slices)):
                channels = [slice_samples] + [extra[index] for extra in (extra_slices or [])]
                path = os.path.join(temp_dir, f"slice_{index:03d}.wav")
                ye.write_wav(path, channels, framerate)
                entries.append(
                    {"filepath": path, "name": name, "sample_rate": sample_rate, "mono": False, "params": dict(self._slice_params)}
                )
            result = {"ok": None, "message": ""}
            loop = QEventLoop()

            def finished(ok):
                result["ok"] = ok
                loop.quit()

            def status(text):
                result["message"] = text
                status_callback(text)

            controller.transfer_finished.connect(finished)
            controller.transfer_progress.connect(progress_callback)
            controller.status_changed.connect(status)
            tab._set_edit_busy(True)
            try:
                if not controller.send_file_queue(entries):
                    return False, result["message"] or "Export failed - the sampler's loader did not start"
                if result["ok"] is None:
                    loop.exec()
            finally:
                controller.transfer_finished.disconnect(finished)
                controller.transfer_progress.disconnect(progress_callback)
                controller.status_changed.disconnect(status)
                tab._set_edit_busy(False)
            _log(f"slice export: ok={result['ok']} - {result['message']}")
            return bool(result["ok"]), result["message"]
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
