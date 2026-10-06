"""The Transfer Dashboard's Yamaha A4000/A5000 side: the sample LIST and RECEIVING samples, over the unit's native protocol.

Built lazily by `SamplerController` (like `S950Transfers`) and reports through the controller's own signals, so the
Dashboard needs almost no Yamaha-specific code:

 - `refresh_list()` reads the object list and emits `sample_list_updated(names)` - the samples in list order. A sample's
   number is its position in that list (also what Sample Dump Standard calls it - measured, even after a delete).
 - `receive_samples([(number, path)])` saves each sample as a WAV: its sample dump (`SP`) names the left (and, for a stereo
   sample, right) WAVE object, each wave object is fetched as the native wave bulk dump (core/yamaha_wave.py), and the result
   is written as a mono or stereo WAV at the sample's own rate. Faster than SDS (no per-packet handshake), reaches BOTH
   channels of a stereo sample (SDS offers one number per sample) and kept working on a unit whose SDS dumps stalled.

SENDING samples to the unit is still plain Sample Dump Standard (the controller's generic path) - the native route for
loading audio is unmeasured.

Everything goes through `YamahaSession`, so it queues behind (and never interleaves with) the editor's own operations.
A wave dump can't be aborted on the unit: a cancel drains the rest of its stream first (see the session).
"""

from core import debug_log, sds_encoder
from core import yamaha_params as yp


def _log(message):
    debug_log.get_logger().info(f"YamahaTransfers: {message}")


class YamahaTransfers:
    def __init__(self, controller):
        self._c = controller
        self.names = []  # the samples of the last list read, in order (index == number)
        self.busy = False  # a receive is running
        self._queue = []
        self._token = 0  # bumped on every start/cancel so late callbacks from an abandoned receive are ignored
        self._total = 0
        self._done = 0

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
        _log("receive cancelled")
        self._token += 1
        self.busy = False
        self._queue = []
        self._session.cancel()  # fails whatever is in flight/queued - the stale callbacks are ignored by the token
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
