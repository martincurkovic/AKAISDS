"""The Transfer Dashboard's Yamaha A4000/A5000 side: the sample LIST, RECEIVING and SENDING samples, over the unit's native protocol.

Built lazily by `SamplerController` (like `S950Transfers`) and reports through the controller's own signals, so the
Dashboard needs almost no Yamaha-specific code:

 - `refresh_list()` reads the object list and emits `sample_list_updated(names)` - the samples in list order. A sample's
   number is its position in that list (also what Sample Dump Standard calls it - measured, even after a delete).
 - `receive_samples([(number, path)])` saves each sample as a WAV: its sample dump (`SP`) names the left (and, for a stereo
   sample, right) WAVE object, each wave object is fetched as the native wave bulk dump (core/yamaha_wave.py), and the result
   is written as a mono or stereo WAV at the sample's own rate. Faster than SDS (no per-packet handshake), reaches BOTH
   channels of a stereo sample (SDS offers one number per sample) and kept working on a unit whose SDS dumps stalled.

 - `send_file_queue(entries)` loads WAV files as new samples with the native bulk load (core/yamaha_load.py): the wave dump(s) then
   the sample dump, ~620 frames/s per channel on the wire (the same MIDI speed as a receive). Stereo files become real STEREO samples.
   A sample is never overwritten: a name already on the unit gets a number added. Each load is VERIFIED afterwards (the object list
   must hold the new sample and its waves, and the sample's own dump must describe what was sent) because the unit never answers a
   bulk load - a refusal (Bulk Protect on, wave memory full...) is otherwise invisible.

Everything goes through `YamahaSession`, so it queues behind (and never interleaves with) the editor's own operations.
A wave dump can't be aborted on the unit: a cancel drains the rest of its stream first (see the session).
"""

import os

from PySide6.QtCore import QTimer

from core import debug_log, sds_encoder
from core import yamaha_load as yl
from core import yamaha_params as yp
from core import yamaha_sysex as ysx


def _log(message):
    debug_log.get_logger().info(f"YamahaTransfers: {message}")


class YamahaTransfers:
    def __init__(self, controller):
        self._c = controller
        self.names = []  # the samples of the last list read, in order (index == number)
        self.loaded_names = []  # the names the samples of the LAST send really landed under (a clash adds a number), in order
        self.busy = False  # a receive or a send is running
        self._mode = None  # "receive" / "send" while busy
        self._send_total = 0
        self._send_done = 0
        self._send_skipped = 0
        self._send_current = None
        self._queue = []
        self._token = 0  # bumped on every start/cancel so late callbacks from an abandoned receive are ignored
        self._total = 0
        self._done = 0
        #: after the last message of a load, how long before the unit is asked anything (it answers an identity request 2-4 s
        #: after the last message, measured) and how often / how far apart a verification read is retried (the first request
        #: after a load is sometimes simply not answered)
        self.verify_delay_ms = 2500
        self.verify_retries = 3
        self.verify_retry_ms = 3000

    @property
    def _session(self):
        return self._c.yamaha_session()

    # -- the list --------------------------------------------------------------------------------------------

    def refresh_list(self, silent=False):
        def got(entries):
            if entries is None:
                self._c.status_changed.emit(
                    "Couldn't read the Yamaha sampler's sample list - check the MIDI ports, its Device Number and Bulk Protect"
                )
                return
            self.names = [e.name for e in entries if e.kind == "sample"]
            _log(f"sample list: {len(self.names)} sample(s)")
            self._c.sample_list_updated.emit(list(self.names))
            if not silent:
                self._c.status_changed.emit(f"Loaded {len(self.names)} sample(s) from hardware")

        if not silent:
            self._c.status_changed.emit("Requesting the sample list...")
        self._session.request_object_list(got)

    # -- receiving ---------------------------------------------------------------------------------------------

    def receive_samples(self, requests):
        """requests: [(sample number, save path)] - numbers are positions in the last list read."""
        if not requests:
            return
        if self.busy or self._c.is_sds_transfer_busy():
            self._c.status_changed.emit("A transfer is already in progress - please wait for it to finish")
            return
        self.busy = True
        self._mode = "receive"
        self._token += 1
        self._queue = list(requests)
        self._total = len(requests)
        self._done = 0
        _log(f"receive start: {self._total} sample(s)")
        self._next(self._token)

    def cancel(self):
        """Stop a running receive. True if there was one (the controller then has nothing more to do)."""
        if not self.busy:
            return False
        mode = self._mode
        _log(f"{mode} cancelled")
        self._token += 1
        self.busy = False
        self._mode = None
        self._queue = []
        self._session.cancel()  # fails whatever is in flight/queued - the stale callbacks are ignored by the token
        if mode == "send":
            sent = self._send_done
            self._c.status_changed.emit(
                "Transfer cancelled" + (f" - {sent} sample(s) were already loaded" if sent else "")
                + ("; the one being sent was not completed" if self._send_current else "")
            )
            self._send_current = None
            self._c.transfer_finished.emit(False)
            self._c.refresh_sample_list(silent=True)
        else:
            self._c.status_changed.emit("Transfer cancelled")
            self._c.receive_finished.emit(False)
        return True

    def _fail(self, token, message):
        if token != self._token:
            return
        self._token += 1
        self.busy = False
        self._queue = []
        _log(f"receive failed: {message}")
        self._c.status_changed.emit(message)
        self._c.receive_finished.emit(False)

    def _next(self, token):
        if token != self._token:
            return
        if not self._queue:
            self.busy = False
            self._c.status_changed.emit("All samples received")
            self._c.receive_finished.emit(True)
            return
        number, path = self._queue.pop(0)
        self._done += 1
        if not 0 <= number < len(self.names):
            self._fail(token, f"Sample {number} isn't in the list - refresh the sample list and try again")
            return
        name = self.names[number]
        label = f"'{name}' ({self._done}/{self._total})"
        self._c.status_changed.emit(f"Reading {label}...")
        self._c.receive_progress.emit(0, 1)
        self._session.request_bulk("SP", name, lambda dump: self._on_sample_dump(token, number, name, label, path, dump))

    def _on_sample_dump(self, token, number, name, label, path, dump):
        if token != self._token:
            return
        if dump is None:
            self._fail(token, f"Couldn't read {label} from the sampler")
            return
        data = bytes(dump.data)
        waves = [("left", bytes(data[yp.WAVE_NAME_L_OFFSET : yp.WAVE_NAME_L_OFFSET + 16]).decode("ascii", "replace").strip(" \x00"))]
        if yp.is_stereo(data):
            waves.append(("right", yp.wave_name_right(data)))
        rate = yp.extract(yp.get("sample", "sampling_frequency_l"), data)
        self._fetch_waves(token, label, path, rate, waves, [])

    def _fetch_waves(self, token, label, path, rate, waves, got):
        if token != self._token:
            return
        if len(got) == len(waves):
            self._write(token, label, path, rate, got)
            return
        side, wave = waves[len(got)]
        channels = len(waves)
        index = len(got)

        progress = {"frames": 0}

        def chunk(new, total):
            if token != self._token:
                return
            progress["frames"] += len(new)
            self._c.receive_progress.emit(index * total + progress["frames"], total * channels)
            self._c.status_changed.emit(
                f"Receiving {label}" + (f" {side} channel" if channels > 1 else "") + f": {100 * progress['frames'] // max(total, 1)}%"
            )

        self._session.request_wave(
            wave,
            lambda frames: self._on_wave(token, label, path, rate, waves, got, side, frames),
            on_chunk=chunk,
        )

    def _on_wave(self, token, label, path, rate, waves, got, side, frames):
        if token != self._token:
            return
        if frames is None:
            self._fail(token, f"Couldn't read the {side} channel of {label} from the sampler")
            return
        self._fetch_waves(token, label, path, rate, waves, got + [frames])

    def _write(self, token, label, path, rate, channels):
        try:
            if len(channels) == 1:
                sds_encoder.write_wav_file(path, channels[0], rate, 16)
            else:
                length = max(len(c) for c in channels)
                left, right = (list(c) + [0] * (length - len(c)) for c in channels)
                sds_encoder.write_wav_file_stereo(path, left, right, rate, 16)
        except Exception as e:
            debug_log.get_logger().error("YamahaTransfers: couldn't write the WAV", exc_info=True)
            self._fail(token, f"Couldn't save {label}: {e}")
            return
        _log(f"saved {label} to {path} ({len(channels)} channel(s), {len(channels[0])} frames @ {rate} Hz)")
        self._c.status_changed.emit(f"Saved {label} to {path}")
        self._c.sample_received.emit(path)
        self._next(token)

    # -- sending -----------------------------------------------------------------------------------------------

    def send_file_queue(self, entries):
        """Load WAV files as new samples. entries: the Dashboard's dicts (filepath, name or None, sample_rate or None, mono; the
        bit depth is ignored - the unit's waves are always 16-bit), optionally with `params`: sample rows to carry over
        (`core.yamaha_load.CARRIED_ROWS` - the Samples tab's edited copies keep their key, tuning, loop mode and markers). Reports through the controller's signals like the other
        engines: transfer_progress / unit_progress, file_transferred per file that landed, transfer_finished at the end."""
        if not entries:
            return False
        if self.busy or self._c.is_sds_transfer_busy():
            self._c.status_changed.emit("A transfer is already in progress - please wait for it to finish")
            return False
        if self._c.is_open_loop():
            self._c.status_changed.emit("Sending to a Yamaha sampler needs a MIDI input too - it is how each load is checked")
            return False
        self.busy = True
        self._mode = "send"
        self._token += 1
        self.loaded_names = []
        self._queue = list(entries)
        self._send_total = len(entries)
        self._send_done = 0
        self._send_skipped = 0
        self._send_current = None
        _log(f"send start: {self._send_total} file(s)")
        self._next_send(self._token)
        return True

    def _send_finished(self, token, ok, message):
        if token != self._token:
            return
        self._token += 1
        self.busy = False
        self._mode = None
        self._queue = []
        self._send_current = None
        _log(f"send finished ok={ok}: {message}")
        self._c.status_changed.emit(message)
        self._c.transfer_finished.emit(ok)
        self._c.refresh_sample_list(silent=True)  # show what is on the unit now

    def _next_send(self, token):
        if token != self._token:
            return
        if not self._queue:
            done, skipped = self._send_done, self._send_skipped
            parts = [f"Sent {done} sample{'' if done == 1 else 's'} to the Yamaha sampler"]
            if skipped:
                parts.append(f"skipped {skipped}")
            self._send_finished(token, True, ", ".join(parts))
            return
        entry = self._queue.pop(0)
        path = entry["filepath"]
        base = os.path.splitext(os.path.basename(path))[0]
        index = self._send_total - len(self._queue)
        self._c.status_changed.emit(f"Reading file {index}/{self._send_total}: {base}...")
        try:
            channels, rate = prepare_channels(
                *sds_encoder.read_wav_channels(path), target_rate=entry.get("sample_rate"), force_mono=entry.get("mono", False)
            )
            yl.check_audio(channels, rate)  # empty, too long...
        except (OSError, ValueError) as e:
            # skipped, not failed: the rest of the queue is unaffected and the file stays in the Dashboard's queue
            debug_log.get_logger().warning(f"YamahaTransfers: skipping {path!r}: {e!r}")
            self._c.status_changed.emit(f"Skipping {os.path.basename(path)}: {e}")
            self._send_skipped += 1
            self._next_send(token)
            return
        self._send_current = entry
        # fresh names every time: an earlier file of this batch (or the front panel) may have taken one
        self._session.request_object_list(
            lambda objects: self._on_names(token, entry, base, index, channels, rate, objects)
        )

    def _on_names(self, token, entry, base, index, channels, rate, objects):
        if token != self._token:
            return
        if objects is None:
            self._send_finished(
                token, False,
                "Couldn't read the Yamaha sampler's object list before sending - check the MIDI ports, its Device Number and Bulk Protect",
            )
            return
        samples = [o.name for o in objects if o.kind == "sample"]
        waves = [o.name for o in objects if o.kind == "wave"]
        try:
            wanted = yl.sample_name_for(entry.get("name") or base)
            name = yl.unique_name(wanted, samples)
            load = yl.build_sample_load(
                self._session.device, name, channels, rate, taken_samples=samples, taken_waves=waves, params=entry.get("params")
            )
        except yl.LoadError as e:
            self._c.status_changed.emit(f"Skipping {base}: {e}")
            self._send_skipped += 1
            self._next_send(token)
            return
        label = f"'{load.sample_name}' ({index}/{self._send_total})"
        renamed = f" (named '{load.sample_name}' - '{wanted}' is already on the sampler)" if name != wanted else ""
        _log(
            f"loading {label}: {load.frames} frames @ {load.rate} Hz, {'stereo' if load.stereo else 'mono'}, "
            f"{len(load.messages)} messages, {load.wire_bytes} bytes (~{load.wire_seconds:.0f} s); waves {load.wave_names}"
        )
        self._c.status_changed.emit(f"Sending {label}: {load.frames / load.rate:.1f} s of audio, about {load.wire_seconds:.0f} s on the wire{renamed}")

        def progress(sent, total):
            if token == self._token:
                self._c.transfer_progress.emit(sent, total)
                self._c.unit_progress.emit(sent / total if total else 0.0)

        self._session.send_messages(load.messages, lambda ok: self._on_sent(token, entry, load, label, ok), on_progress=progress)

    def _on_sent(self, token, entry, load, label, ok):
        if token != self._token:
            return
        if not ok:
            self._send_finished(token, False, f"Sending {label} failed - see the log")
            return
        self._c.status_changed.emit(f"Checking {label} on the sampler...")
        QTimer.singleShot(self.verify_delay_ms, lambda: self._check_objects(token, entry, load, label, 0))

    def _check_objects(self, token, entry, load, label, attempt):
        if token != self._token:
            return
        self._session.request_object_list(lambda objects: self._verify_objects(token, entry, load, label, objects, attempt))

    def _verify_objects(self, token, entry, load, label, objects, attempt=0):
        if token != self._token:
            return
        if objects is None and attempt < self.verify_retries:
            _log(f"verification of {label}: no object list (attempt {attempt + 1}), retrying")
            QTimer.singleShot(self.verify_retry_ms, lambda: self._check_objects(token, entry, load, label, attempt + 1))
            return
        names = {(o.kind, o.name) for o in objects or []}
        missing = [n for n in [("sample", load.sample_name)] + [("wave", w) for w in load.wave_names] if n not in names]
        if objects is None or missing:
            self._send_failed_check(token, load, label, "it did not appear on the sampler" if objects is not None else "the sampler didn't answer")
            return
        self._read_sample(token, entry, load, label, 0)

    def _read_sample(self, token, entry, load, label, attempt):
        self._session.request_bulk("SP", load.sample_name, lambda dump: self._verify_sample(token, entry, load, label, dump, attempt))

    def _verify_sample(self, token, entry, load, label, dump, attempt=0):
        if token != self._token:
            return
        if dump is None and attempt < self.verify_retries:
            _log(f"verification of {label}: no sample dump (attempt {attempt + 1}), retrying")
            QTimer.singleShot(self.verify_retry_ms, lambda: self._read_sample(token, entry, load, label, attempt + 1) if token == self._token else None)
            return
        problem = None
        if dump is None:
            problem = "the sampler didn't answer when it was read back"
        else:
            data = bytes(dump.data)
            got = (
                yp.extract(yp.get("sample", "wave_length"), data),
                yp.extract(yp.get("sample", "sampling_frequency_l"), data),
                yp.is_stereo(data),
            )
            # an edited copy carries its source's Start/End window (`params` wave_length), which can be shorter than the audio it holds
            expected_length = (entry.get("params") or {}).get("wave_length", load.frames)
            if got != (expected_length, load.rate, load.stereo):
                problem = (
                    f"the sampler holds {got[0]:,} frames at {got[1]} Hz ({'stereo' if got[2] else 'mono'}) "
                    f"instead of {expected_length:,} at {load.rate}"
                )
        if problem:
            self._send_failed_check(token, load, label, problem)
            return
        self._send_done += 1
        self.loaded_names.append(load.sample_name)
        self._send_current = None
        self._c.unit_progress.emit(1.0)
        self._c.file_transferred.emit(entry["filepath"])
        self._next_send(token)

    def _send_failed_check(self, token, load, label, why):
        # the unit never answers a bulk load, so this is the only place a refusal shows up
        done = self._send_done
        self._send_finished(
            token, False,
            f"{label} was not loaded: {why}. Check that Bulk Protect is off, that the sampler has free wave memory and that its display "
            "isn't showing a 'MIDI Bulk Received' message waiting for OK (it stops answering until you press OK)"
            + (f" ({done} earlier sample(s) did load)" if done else ""),
        )


def prepare_channels(channels, framerate, *, target_rate=None, force_mono=False):
    """WAV channels -> (channels, rate) ready for `yamaha_load.build_sample_load`: left only when `force_mono`, resampled to
    `target_rate` if given (or down to 48000 Hz if the file's own rate is above what the unit's rate row can hold)."""
    channels = [list(c) for c in (channels[:1] if force_mono else channels)]
    rate = int(target_rate or framerate)
    if rate > yl.MAX_RATE:
        rate = 48000
    if rate != framerate:
        channels = [sds_encoder.resample_to_target_rate(c, framerate, rate)[0] for c in channels]
        rate = int(rate)
    return channels, rate

