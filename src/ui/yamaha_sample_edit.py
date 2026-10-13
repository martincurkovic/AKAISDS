"""The edit buttons of the Yamaha Samples tab: Trim / Reverse / Fade / Normalise / Filter / Stretch-Pitch, the external-editor round trip, and the Slice Editor.

Since 2026-10-13 the six edits and "Load Edited Audio" change the shown sample's WORKING COPY (ui/yamaha_samples_tab.py `_Staged`) and send nothing: the
tab shows the edited audio, edits chain, Undo steps back, Revert goes back to the unit's version. "Send as New Sample" (the banner, or the Filter / Stretch
dialogs' own button) is what reaches the unit. The rest of this docstring describes that last step, which is unchanged:

Laid out and worded like the S3000 editor's, with one difference that matters: the A4000 can neither delete nor overwrite a sample
over MIDI (no known opcode; a sample re-sent under its own name has every parameter reset - core/yamaha_load.py), so an edit makes a
NEW sample - the edited audio under a name derived from the original ("DRUM LOOP TRIM") with the original's key, tuning, loop mode and
markers carried over (core/yamaha_edit.py) - and the original is never touched. Each confirmation says so. The new sample is loaded
through the unit's native bulk load (controller/yamaha_transfers.py: wave dump(s) + sample dump, then read back to check), a
stereo sample stays stereo, and the tab's sample list is refreshed afterwards with the copy selected.

The Slice Editor (ui/slice_editor_window.py, the S3000 editor's own dialog) opens on the loaded audio; its Export sends every slice
as a new sample the same way (a stereo sample's slices are stereo: the dialog slices the other channel at the same frames). Its "also fill a
program" option assigns the slices to an EMPTY program (the A4000 can't create or rename programs), each on its own key - see `_create_program`.

Everything needs the audio of the shown sample in memory (double-click the waveform), like the S3000 editor.
"""

import os
import shutil
import tempfile

from PySide6.QtCore import QEventLoop, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from controller.yamaha_session import LinkResult
from core import app_config, debug_log
from core import external_edit
from core import sample_editing as se
from core import yamaha_edit as ye
from core import yamaha_load as yl
from core import yamaha_params as yp
from core import yamaha_sysex as ysx
from ui import import_audio
from ui.external_edit_tracker import ExternalEditTracker
from ui.filter_sample_dialog import FilterSampleDialog
from ui.slice_editor_window import SliceEditorWindow
from ui.time_stretch_dialog import TimeStretchDialog


def _log(message):
    debug_log.get_logger().info(f"YamahaEditor: {message}")


#: the Slice Editor's "fill a program": slice i is mapped to key FIRST_SLICE_NOTE + i (36 = C1 in this app's C3-at-60 note names, like the
#: Akai export), so at most 128 - 36 slices fit on the keyboard
FIRST_SLICE_NOTE = 36
MAX_PROGRAM_SLICES = 128 - FIRST_SLICE_NOTE
#: the loop mode a slice gets when it is going into a program: 4 = One-shot (plays right through, like the Akai export's one-shot slices)
ONE_SHOT = 4
#: a link made right after samples were loaded once left a real unit silent for 28 s to ~8 min (unexplained, dev_docs/a4000-native-load-
#: findings.md): wait a moment first, and let each link's answer take as long as that. (Tests shorten both.)
LINK_SETTLE_MS = 4000
LINK_REPLY_TIMEOUT_MS = 60000
#: how long to wait for a unit that has stopped answering (a front-panel "MIDI Bulk Received" message waiting for OK - measured 2026-10-07,
#: dev_docs/a4000-editor-roadmap.md) before giving up on a slice's assignment, and how often to ask whether it is back
SILENCE_GIVE_UP_S = 900
SILENCE_POLL_MS = 3000

#: the name suffix of a sample made from audio edited in an external app ("DRUM LOOP EDIT")
EXTERNAL_SUFFIX = " EDIT"

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
        self._progress_label = ""
        self._free_programs = []  # [(number, label)] the Slice Editor can fill (see open_slicer)
        self._dialog = None
        for key, slot in (
            ("trim", self.trim),
            ("reverse", self.reverse),
            ("fade", self.fade),
            ("normalise", self.normalise),
            ("filter", self.filter),
            ("stretch", self.time_stretch),
            ("slice", self.open_slicer),
            ("external_edit", self.edit_externally),
            ("external_load", self.load_edited),
            ("stage_send", self.send_working_copy_as_new),
        ):
            tab.connect_control(key, slot)
        self._external = ExternalEditTracker(self)
        self._external.changed.connect(tab.update_edit_buttons)
        self._sending_name = None  # the sample whose working copy is going out as a new sample

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

    # -- the edits: they change the working copy ----------------------------------------------------------------

    def trim(self):
        ctx = self._context()
        if ctx is None:
            return
        start, _ls, _le, end = ctx["markers"]
        if start == 0 and end == ctx["frames"] - 1:
            self._tab.status_message.emit("Nothing to trim - Start/End already cover the whole sample")
            return
        self._apply("trim", ctx)

    def reverse(self):
        ctx = self._context()
        if ctx is None:
            return
        if ctx["frames"] <= 1:
            self._tab.status_message.emit("Nothing to reverse - the sample is too short")
            return
        self._apply("reverse", ctx)

    def fade(self):
        ctx = self._context()
        if ctx is None:
            return
        start, _ls, _le, end = ctx["markers"]
        # the fades are the lead-in/lead-out AROUND [start, end] (core/sample_editing.fade_in_out_samples)
        if start == 0 and end == ctx["frames"] - 1:
            self._tab.status_message.emit("Nothing to fade - Start/End already cover the whole sample")
            return
        self._apply("fade", ctx)

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
        self._apply("normalise", ctx)

    def filter(self):
        ctx = self._context()
        if ctx is None:
            return
        # the dialog previews the left (or only) channel; the chosen filter is then run over every channel. OK applies it to the working
        # copy; the dialog's own Send as New Sample makes the unit's copy at once
        dialog = FilterSampleDialog(
            self._tab, ctx["name"], ctx["channels"][0], ctx["rate"],
            pitch_shift_semitones=self._tab.preview_pitch_shift_semitones(ctx["data"]),
            allow_new_sample=True, applies_to_working_copy=True, send_name="Send as New Sample",
        )
        if not dialog.exec():
            return
        options = dict(
            highpass_enabled=dialog.highpass_enabled(),
            highpass_cutoff_hz=dialog.highpass_cutoff_hz(),
            highpass_slope_db_per_octave=dialog.highpass_slope_db_per_octave(),
            lowpass_enabled=dialog.lowpass_enabled(),
            lowpass_cutoff_hz=dialog.lowpass_cutoff_hz(),
            lowpass_slope_db_per_octave=dialog.lowpass_slope_db_per_octave(),
        )
        if dialog.send_as_new():
            name = ye.copy_name(ctx["name"], ye.EDITS["filter"][0])
            if self._confirm("Filter Sample", f'Make "{name}" from "{ctx["name"]}" with this filter applied?' + _NOT_CHANGED):
                self._run("filter", ctx, options)
            return
        self._apply("filter", ctx, options)

    def time_stretch(self):
        """Time Stretch / Pitch Shift: one dialog, two edits."""
        ctx = self._context()
        if ctx is None:
            return
        # the dialog previews the left (or only) channel; both channels of a stereo sample get the same edit
        dialog = TimeStretchDialog(
            self._tab, ctx["name"], ctx["channels"][0], ctx["rate"],
            pitch_shift_semitones=self._tab.preview_pitch_shift_semitones(ctx["data"]),
            allow_new_sample=True, applies_to_working_copy=True, send_name="Send as New Sample",
        )
        if not dialog.exec():
            return
        kind, options = dialog.edit()
        _log(f"{ye.EDITS[kind][1]} of {ctx['name']!r}: {options}, grain {dialog.grain_ms()} ms")
        if dialog.send_as_new():
            name = ye.copy_name(ctx["name"], ye.EDITS[kind][0])
            if self._confirm(ye.EDITS[kind][1], f'Make "{name}" from "{ctx["name"]}", {dialog.summary()}?' + _NOT_CHANGED):
                self._run(kind, ctx, options)
            return
        self._apply(kind, ctx, options)

    def _apply(self, kind, ctx, options=None):
        """Run an edit over the shown sample's channels and keep the result as its working copy. Nothing is sent: Undo and Revert make a
        mistake cheap, so there is no confirmation."""
        label = ye.EDITS[kind][1]
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            channels, markers = ye.apply_edit(
                kind, ctx["channels"], ctx["markers"], framerate=ctx["rate"], options=options
            )
        except Exception as e:  # the pure transform: nothing has changed yet
            _log(f"{label} of {ctx['name']!r} failed: {e!r}")
            self._tab.status_message.emit(f"Couldn't {label.lower()} the sample: {e}")
            return False
        finally:
            QApplication.restoreOverrideCursor()
        if not self._tab.stage_working_copy(ctx["name"], channels, markers):
            return False
        _log(f"{label} applied to {ctx['name']!r} ({len(channels[0]):,} frames, {len(channels)} ch) - not on the unit")
        self._tab.status_message.emit(f"{label} applied to {ctx['name']!r} - not on the unit yet")
        return True

    def _run(self, kind, ctx, options=None):
        suffix, label = ye.EDITS[kind]
        try:
            channels, markers = ye.apply_edit(
                kind, ctx["channels"], ctx["markers"], framerate=ctx["rate"], options=options
            )
        except Exception as e:  # the pure transform runs before anything touches the unit
            _log(f"{label} of {ctx['name']!r} failed before sending: {e!r}")
            self._tab.status_message.emit(f"Couldn't {label.lower()} the sample: {e}")
            return
        self._send_copy(ctx, channels, markers, suffix, label)

    def _send_copy(self, ctx, channels, markers, suffix, label):
        """Send `channels` (with `markers`) as a NEW sample named from the original's name + `suffix`, carrying its parameters over."""
        tab = self._tab
        try:
            name = ye.copy_name(ctx["name"], suffix)
            params = ye.params_for_copy(ctx["data"], markers)
            temp_dir = tempfile.mkdtemp(prefix="akaisds_yamaha_edit_")
            path = os.path.join(temp_dir, "edit.wav")
            ye.write_wav(path, channels, ctx["rate"])
        except Exception as e:  # the WAV step runs before anything touches the unit
            _log(f"{label} of {ctx['name']!r} failed before sending: {e!r}")
            tab.status_message.emit(f"Couldn't {label.lower()} the sample: {e}")
            self._sending_name = None
            return False
        # a send from a sample with a working copy: once the new sample is on the unit that work lives on as it, and the original goes back
        # to the unit's version
        self._sending_name = ctx["name"] if ctx["name"] in self._tab._staged else None
        entry = {"filepath": path, "name": name, "sample_rate": ctx["rate"], "mono": False, "params": params}
        _log(f"{label} of {ctx['name']!r} -> new sample {name!r} ({len(channels[0]):,} frames, {len(channels)} ch)")
        expected = yl.unique_name(yl.sample_name_for(name), tab._names)
        started = self._start_send([entry], temp_dir, expected, f'{label} "{ctx["name"]}"')
        if not started:
            self._sending_name = None
        return started

    # -- an external audio editor -------------------------------------------------------------------------------

    def edited_audio_ready(self, name):
        """True when the editor has saved over the file exported for sample `name` (what enables Send Edited Audio)."""
        return name is not None and self._external.is_ready(name)

    def edit_externally(self):
        ctx = self._context()
        if ctx is None:
            return
        name, channels, rate = ctx["name"], ctx["channels"], ctx["rate"]
        problem = self._external.open(name, name, lambda path: ye.write_wav(path, channels, rate))
        if problem:
            self._tab.status_message.emit(problem)
            return
        hint = "" if app_config.get_saved_external_editor() else " (pick an editor in Settings)"
        self._tab.status_message.emit(f'Opened "{name}"{hint} - save it there, then click Send Edited Audio')
        self._tab.update_edit_buttons()

    def load_edited(self):
        """The editor's saved file becomes the shown sample's working copy (nothing is sent)."""
        ctx = self._context()
        if ctx is None:
            return
        name = ctx["name"]
        if not self._external.is_ready(name):
            self._tab.status_message.emit("Save the file in the editor first")
            return
        try:
            channels, source_rate = external_edit.read_edited(self._external.path(name), ctx["rate"])
        except ValueError as e:
            _log(f"couldn't read the edited file for {name!r}: {e}")
            self._tab.status_message.emit(f"Couldn't read the edited file: {e}")
            return
        # the sample keeps its channel count: a stereo sample given a mono file gets it on both sides, a mono sample given a stereo
        # file gets the mix
        if len(ctx["channels"]) == 1 and len(channels) == 2:
            channels = [external_edit.mix_to_mono(channels)]
        elif len(ctx["channels"]) == 2 and len(channels) == 1:
            channels = [channels[0], list(channels[0])]
        frames = len(channels[0])
        markers = external_edit.remap_markers(ctx["frames"], frames, *ctx["markers"])
        _log(
            f"{name!r} edited externally: {ctx['frames']:,} -> {frames:,} frames, {len(channels)} ch at {source_rate} Hz "
            f"(sample rate {ctx['rate']}), markers {ctx['markers']} -> {markers}"
        )
        if self._tab.stage_working_copy(name, channels, markers):
            self._external.mark_sent(name)  # Load waits for the next save
            self._tab.status_message.emit(f"Loaded the edited audio for {name!r} - not on the unit yet")

    # -- the working copy goes out -----------------------------------------------------------------------------

    def send_working_copy_as_new(self):
        """The banner's Send as New Sample: the shown sample's working copy becomes a NEW sample on the unit (the A4000 can't overwrite)."""
        ctx = self._context()
        if ctx is None:
            return
        name = ctx["name"]
        staged = self._tab._staged.get(name)
        if staged is None:
            return
        new_name = ye.copy_name(name, EXTERNAL_SUFFIX)
        if not self._confirm(
            "Send as New Sample",
            f'Make "{new_name}" from the edited audio of "{name}" ({ctx["frames"]:,} frames, was {len(staged.original_channels[0]):,})?'
            + _NOT_CHANGED,
        ):
            return
        self._send_copy(ctx, ctx["channels"], ctx["markers"], EXTERNAL_SUFFIX, "Send")

    # -- sending ---------------------------------------------------------------------------------------------

    def _start_send(self, entries, temp_dir, select_name, label):
        tab = self._tab
        controller = tab._controller
        self._temp_dir, self._select_name = temp_dir, select_name
        self._progress_label = label
        controller.transfer_finished.connect(self._on_send_finished)
        controller.transfer_progress.connect(self._on_send_progress)
        tab._set_edit_busy(True)
        if not controller.send_file_queue(entries):
            controller.transfer_finished.disconnect(self._on_send_finished)
            controller.transfer_progress.disconnect(self._on_send_progress)
            tab._set_edit_busy(False)
            self._cleanup()
            return False
        return True

    def _on_send_progress(self, sent, total):
        self._tab.show_edit_progress(sent, total, self._progress_label)

    def _on_send_finished(self, ok):
        tab = self._tab
        tab._controller.transfer_finished.disconnect(self._on_send_finished)
        tab._controller.transfer_progress.disconnect(self._on_send_progress)
        self._cleanup()
        tab._set_edit_busy(False)
        sent, self._sending_name = self._sending_name, None
        if ok and sent is not None:
            tab.discard_working_copy(sent)  # it lives on as the new sample now
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
        data = ctx["data"]
        g = lambda key: yp.extract(yp.get("sample", key), data)  # noqa: E731
        self._run_slicer(
            ctx["name"],
            ctx["channels"],
            ctx["rate"],
            {
                "original_key_l": g("original_key_l"),
                "original_key_r": g("original_key_r"),
                "coarse_tune": g("coarse_tune"),
                "fine_tune_l": g("fine_tune_l"),
                "fine_tune_r": g("fine_tune_r"),
            },
            preview_pitch_shift=self._tab.preview_pitch_shift_semitones(data),
        )

    def import_audio(self):
        """File > Import Audio: an audio file or a ReCycle .rx2 loop in the Slice Editor (ui/import_audio.py) - an .rx2 with its cut points
        already marked, any other file with none. A stereo file stays stereo. Export sends each slice as a new sample (and can fill an
        empty program) - nothing import-specific goes to the unit. Needs no sample selected or loaded."""
        tab = self._tab
        if tab._edit_busy or tab._loading_name is not None or tab._controller.is_transfer_busy():
            tab.status_message.emit("A transfer is already in progress - wait for it to finish")
            return
        imported = import_audio.pick_and_load(tab, tab.status_message.emit)
        if imported is None:
            return
        _log(
            f"import audio {imported.name!r}: {len(imported.channels)} channel(s), {imported.rate} Hz, "
            f"{len(imported.channels[0])} frames, {len(imported.markers)} cut points"
        )
        # no sample to copy a key from: a plain C3 (60) root; the unit's own defaults for the rest (a fresh sample's tuning is 0)
        self._run_slicer(
            imported.name,
            imported.channels,
            imported.rate,
            {"original_key_l": 60, "original_key_r": 60},
            initial_markers=imported.markers or None,
        )

    def _run_slicer(self, name, channels, rate, slice_params, initial_markers=None, preview_pitch_shift=0.0):
        tab = self._tab
        self._slice_params = slice_params
        self._free_programs = tab.free_programs()  # [(number, label)], fixed for as long as the dialog is open
        self._dialog = None
        dialog = SliceEditorWindow(
            tab,
            name,
            channels[0],
            rate,
            slice_params["original_key_l"],
            0,
            0,
            lambda: list(tab._names),
            self._export_slices,
            cancel_callback=tab._controller.cancel_transfer,
            extra_channels=channels[1:] or None,
            hide_bit_depth=True,
            program_names_provider=lambda: [label for _number, label in self._free_programs],
            create_program_callback=self._create_program,
            initial_markers=initial_markers,
            # a sample on the unit previews as it plays (Original key, tuning); audio from a file has none
            preview_pitch_shift_semitones=preview_pitch_shift,
            program_labels={
                "checkbox": "Also fill a program with the slices",
                "combo": "Program:",
                "tooltip": (
                    "Assign every slice to an empty program, each on its own key starting at C1, played right through "
                    "(one-shot). The A4000 can't create programs or rename them over MIDI, so this fills a program that is "
                    "empty now, and it keeps its name."
                ),
                "no_options_tooltip": (
                    "There is no empty program to fill (every program holds samples, or the editor hasn't finished reading the "
                    "program list yet)"
                ),
                "has_name": False,
            },
            max_program_slices=MAX_PROGRAM_SLICES,
            program_confirm_message=lambda count, label: (
                f"Also assign the {count} slice{'s' if count != 1 else ''} to program {label.split()[0]} (empty now), each on its own "
                "key starting at C1, played right through (one-shot). The program keeps its name - the A4000's program names can't "
                "be changed over MIDI."
            ),
        )
        self._dialog = dialog
        dialog.exec()
        self._dialog = None
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
        # a slice that is going into a program is mapped to its own key (root note = that key, so it plays at its own pitch) and
        # one-shot, set in the sample dump itself rather than by a write per slice and row
        into_program = self._wants_program()
        temp_dir = tempfile.mkdtemp(prefix="akaisds_yamaha_slices_")
        try:
            entries = []
            for index, (name, slice_samples) in enumerate(zip(names, slices)):
                channels = [slice_samples] + [extra[index] for extra in (extra_slices or [])]
                path = os.path.join(temp_dir, f"slice_{index:03d}.wav")
                ye.write_wav(path, channels, framerate)
                params = dict(self._slice_params)
                if into_program:
                    note = FIRST_SLICE_NOTE + index
                    params.update(key_range_low=note, key_range_high=note, original_key_l=note, original_key_r=note, loop_mode=ONE_SHOT)
                entries.append({"filepath": path, "name": name, "sample_rate": sample_rate, "mono": False, "params": params})
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

    # -- "fill a program" -----------------------------------------------------------------------------------------

    def _wants_program(self):
        checkbox = getattr(getattr(self, "_dialog", None), "create_program_checkbox", None)
        return bool(checkbox is not None and checkbox.isChecked())

    def _create_program(self, names, template_index, program_name, progress_callback, status_callback, busy_callback=None):
        """The second half of the Slice Editor's export, called once every slice is on the unit: assign the slices, in order, to the
        empty program picked in the dialog. The A4000 has no create-program or program-name write (the 128 programs always exist and
        a program's name is read only), so "new program" means filling an empty one. Each assignment is the session's own guarded
        link (backup once, then ask the unit whether it holds the link). Returns (ok, message)."""
        tab = self._tab
        session = tab._session
        number = self._free_programs[template_index][0]
        program = ysx.program_object_name(number)
        count = len(names)
        busy = busy_callback or (lambda _busy: None)
        old_timeout = session.reply_timeout_ms
        busy(True)
        try:
            if LINK_SETTLE_MS:
                status_callback("Letting the sampler settle before assigning...")
                loop = QEventLoop()
                QTimer.singleShot(LINK_SETTLE_MS, loop.quit)
                loop.exec()
            session.reply_timeout_ms = LINK_REPLY_TIMEOUT_MS
            for index, name in enumerate(names):
                status_callback(f'Assigning "{name}" to program {number:03d} ({index + 1}/{count})...')
                result = self._link_and_wait(program, name)
                if result is None or not result.ok:
                    session.reply_timeout_ms = old_timeout  # (the probes below should fail fast)
                    result = self._recover_silent_link(program, number, name, result, status_callback)
                    session.reply_timeout_ms = LINK_REPLY_TIMEOUT_MS
                if result is None or not result.ok:
                    why = result.message if result is not None else "the sampler didn't answer"
                    _log(f"slice program {number:03d}: assigning {name!r} failed after {index} of {count}: {why}")
                    if index:
                        tab.programs_changed.emit(number, list(names[:index]))
                    return False, (
                        f"Assigned {index} of {count} slices to program {number:03d} - stopped at \"{name}\": {why}"
                    )
                progress_callback(index + 1, count)
        finally:
            session.reply_timeout_ms = old_timeout
            busy(False)
        tab.programs_changed.emit(number, list(names))
        _log(f"slice program: {count} slices assigned to program {number:03d}")
        return True, f"Assigned {count} slice{'s' if count != 1 else ''} to program {number:03d}"

    def _unit_answers(self, wait_ms=4000):
        """True if the unit answers an identity request within `wait_ms`. An identity request is handled without touching the unit's
        display; an object-list request pops up "Transmitting Object List" on it every time (measured 2026-10-07), which is the wrong
        thing to repeat at a unit that is waiting for someone to press OK. Blocks in a nested event loop, like the other waits here."""
        midi = self._tab._controller.midi_manager
        box = {"answered": False}
        loop = QEventLoop()

        def on_sysex(data):
            raw = bytes(data)
            if len(raw) > 3 and raw[0] == 0x7E and raw[2] == 0x06 and raw[3] == 0x02:  # General MIDI identity reply
                box["answered"] = True
                loop.quit()

        midi.sysex_received.connect(on_sysex)
        try:
            midi.send_sysex(bytes([0x7E, 0x7F, 0x06, 0x01]))
            QTimer.singleShot(wait_ms, loop.quit)
            loop.exec()
        finally:
            midi.sysex_received.disconnect(on_sysex)
        return box["answered"]

    def _assigned_names(self, number):
        box = {"done": False, "names": None}
        loop = QEventLoop()

        def got(dump):
            if dump is not None:
                data = bytes(dump.data)
                count = yp.extract(yp.get("program", "assigned_samples"), data)
                box["names"] = [yp.extract(yp.get("easy_edit", "assigned_name"), data, slot).strip(" \x00") for slot in range(count)]
            box["done"] = True
            loop.quit()

        self._tab._session.request_bulk("PG", ysx.program_object_name(number), got)
        if not box["done"]:
            loop.exec()
        return box["names"]

    def _recover_silent_link(self, program, number, name, result, status_callback):
        """A link got no confirmation. If the unit answered but says the link isn't there, it refused: hand the result back. If it NEVER
        answered (after bulk loads it can sit behind a front-panel "MIDI Bulk Received" message until OK is pressed - the link has usually
        been made by then), say what to press, wait until it answers (it may already have, by the time the reply timed out), then check
        whether the link landed and only send it again if it did not."""
        if result is not None and result.linked is not None:
            return result  # the unit DID answer (it says the link isn't there): a refusal, not silence
        _log(f"slice program {number:03d}: no answer after linking {name!r} - waiting for the unit")
        status_callback(
            "The sampler has stopped answering. If its display shows a message such as 'MIDI Bulk Received', press OK (Knob 5) on it - "
            "this carries on by itself once it answers..."
        )
        waited = 0.0
        while waited < SILENCE_GIVE_UP_S:
            if self._unit_answers():
                break
            loop = QEventLoop()
            QTimer.singleShot(SILENCE_POLL_MS, loop.quit)
            loop.exec()
            waited += SILENCE_POLL_MS / 1000
        else:
            return result
        if name in (self._assigned_names(number) or []):
            _log(f"slice program {number:03d}: {name!r} had been linked all along")
            return LinkResult(ok=True, requested=True, program=program, sample=name, linked=True, message=f"Assigned {name!r} to program {program}")
        return self._link_and_wait(program, name)

    def _link_and_wait(self, program, name):
        """Assign one sample and block (in a nested event loop, like the S3000 window's blocking sends) until the unit has answered."""
        box = {"result": None, "done": False}
        loop = QEventLoop()

        def done(result):
            box["result"], box["done"] = result, True
            loop.quit()

        self._tab._session.change_link(program, name, True, done, "sample")
        if not box["done"]:
            loop.exec()
        return box["result"]
