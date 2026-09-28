# Click-to-preview playback for the Slice Editor (ui/slice_editor_window.py,
# ui/slice_waveform_view.py) - the first audio output this app has ever
# opened (see AGENTS.md's Slice Editor section, "deliberately out of scope
# for this pass"). Everything MIDI-shaped elsewhere in this codebase talks
# to a sampler; this is the one place that talks to the computer's own
# speakers/interface instead.
#
# EXPERIMENTAL: uses miniaudio (a small C library with a Python/cffi
# binding) instead of Qt Multimedia's QAudioSink, which this app used
# originally. QAudioSink on Linux produced real, measured buffer underruns
# (rapid Idle/Active oscillation on a real QAudioSink, instrumented and
# confirmed - not guesswork) whenever the app's own buffer setting was
# smaller than the system audio server's own scheduling quantum; the same
# crackling was independently reported on macOS too, on hardware with no
# plausible CPU/performance excuse. miniaudio talks to the platform's
# native audio API directly (CoreAudio/WASAPI/PipeWire/ALSA) through a
# real-time callback with a dedicated, high-priority native thread, rather
# than going through Qt's own backend (FFmpeg-based on this build) and, on
# Linux, PulseAudio's server-side compatibility layer. If this doesn't
# actually fix the macOS crackle too, this file (and the miniaudio
# dependency in pyproject.toml) is the entire blast radius to revert.

import struct

import miniaudio
from PySide6.QtCore import QObject, QTimer, Signal

from core import app_config, debug_log

# how often the playhead position is re-read/re-emitted during playback -
# fast enough to look smooth, cheap enough that polling it costs nothing
_PLAYHEAD_TICK_MS = 30

_SAMPLE_FORMAT = miniaudio.SampleFormat.SIGNED16
_BYTES_PER_FRAME = 2  # SIGNED16, mono (1 channel) - struct.pack("<h", ...)


def list_output_devices():
    return miniaudio.Devices().get_playbacks()


def device_id_string(device):
    # device["id"] is an opaque cffi ma_device_id union (platform-specific,
    # e.g. an ALSA device string on Linux, a CoreAudio device UID on macOS)
    # - hex-encoded so it round-trips through app_config's plain-JSON
    # storage the same way every other saved setting does (see
    # save_ports/save_channel). Confirmed stable across repeated
    # miniaudio.Devices() enumerations for the same physical device.
    return bytes(miniaudio.ffi.buffer(device["id"])).hex()


def find_output_device(id_string):
    if not id_string:
        return None
    for device in list_output_devices():
        if device_id_string(device) == id_string:
            return device
    return None


def resolve_output_device():
    """The device dict (see list_output_devices()) a preview should play
    through, or None to mean "system default" - unlike the old QAudioDevice
    version, None is a real, meaningful answer here (miniaudio.PlaybackDevice
    itself resolves a None device_id to the system default), not just
    "nothing found". A saved device can easily go missing (an interface
    unplugged since it was chosen); falling back to the default beats
    refusing to preview at all.
    """
    saved_id = app_config.get_saved_audio_output_device()
    if not saved_id:
        return None
    device = find_output_device(saved_id)
    if device is not None:
        return device
    debug_log.get_logger().error(
        f"audio_preview: saved output device {saved_id!r} not found - "
        "falling back to the system default"
    )
    return None


class SlicePreviewPlayer(QObject):
    """Plays one mono 16-bit PCM slice at a time out the user's chosen audio
    output device (Settings > Audio Preview), via a miniaudio.PlaybackDevice.
    Owns exactly one device at a time - a new play() call always stops
    whatever's already playing first (retrigger, not queue/overlap) -
    matches how a user actually clicks through slices while chopping a
    break, quickly and repeatedly.

    miniaudio pulls audio through a plain Python generator, invoked from
    its own native, high-priority real-time audio thread (not the Qt/GUI
    thread) every time it needs more frames - so the generator closure
    below must not do anything slow or blocking. It tracks its own
    progress (self._frames_sent) as a plain int; the GUI-thread poll timer
    below only ever READS that int, never writes it, so there's nothing to
    lock (Python attribute assignment/read is already atomic enough for a
    polled progress counter like this - a torn read would at worst show a
    playhead one tick stale, not a crash).

    Also drives the Slice Editor's playhead: while a slice is sounding,
    position_changed(frame) fires roughly every _PLAYHEAD_TICK_MS with the
    current absolute frame position (same indexing as the source *samples*
    passed to play()), and finished() fires once when playback stops for
    any reason (ran off the end of the slice, or a caller/retrigger cut it
    off) - SliceEditorWindow wires both straight into SliceWaveformView's
    own set_playhead()/clear_playhead().
    """

    position_changed = Signal(int)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._device = None
        self._play_start_frame = 0
        self._play_end_frame = 0
        self._total_frames = 0
        self._frames_sent = 0
        self._timer = QTimer(self)
        self._timer.setInterval(_PLAYHEAD_TICK_MS)
        self._timer.timeout.connect(self._on_tick)

    def play(self, samples, start_frame, end_frame, framerate):
        """samples: the full mono int16 sample list/array already loaded for
        this sound (same list slice_bounds()' frame indices are relative
        to); start_frame/end_frame: one slice_bounds() entry, inclusive.
        Never raises into a Qt mouse-event slot - a misconfigured or
        unplugged output device logs and no-ops instead.
        """
        self.stop()

        chunk = samples[start_frame : end_frame + 1]
        if not chunk:
            return
        pcm_bytes = struct.pack("<" + "h" * len(chunk), *chunk)

        device_entry = resolve_output_device()
        device_id = device_entry["id"] if device_entry is not None else None

        # app_config stores this as a frame count (see its own comment,
        # matching an Ableton-style buffer-size dropdown), but miniaudio
        # wants milliseconds - converted against THIS slice's own rate,
        # same reasoning core/audio_preview.py always used framerate (not a
        # fixed 44100) for anything time-based here.
        buffer_frames = app_config.get_saved_audio_buffer_samples()
        buffersize_msec = max(1, round(buffer_frames / framerate * 1000))

        try:
            device = miniaudio.PlaybackDevice(
                output_format=_SAMPLE_FORMAT,
                nchannels=1,
                sample_rate=framerate,
                buffersize_msec=buffersize_msec,
                device_id=device_id,
            )
        except miniaudio.MiniaudioError:
            debug_log.get_logger().error(
                "SlicePreviewPlayer: couldn't open output device", exc_info=True
            )
            return

        self._frames_sent = 0

        def generator():
            # primed with an empty yield first - see miniaudio's own
            # stream_raw_pcm_memory, this is its documented generator
            # protocol, not specific to this app
            required_frames = yield b""
            pos = 0
            while pos < len(pcm_bytes):
                end = pos + required_frames * _BYTES_PER_FRAME
                out = pcm_bytes[pos:end]
                pos += len(out)
                self._frames_sent = pos // _BYTES_PER_FRAME
                required_frames = yield out
            # exhausted: from here the device just keeps calling back for
            # more (StopIteration) and gets silence, harmless - self._on_tick
            # is what actually notices self._frames_sent caught up to
            # self._total_frames and stops the device for real

        gen = generator()
        next(gen)  # prime it per the protocol above

        try:
            device.start(gen)
        except Exception:
            debug_log.get_logger().error(
                "SlicePreviewPlayer: couldn't start playback", exc_info=True
            )
            device.close()
            return

        self._device = device
        self._play_start_frame = start_frame
        self._play_end_frame = end_frame
        self._total_frames = len(chunk)
        self._timer.start()

    def stop(self):
        was_playing = self._device is not None
        self._timer.stop()
        if self._device is not None:
            self._device.stop()
            self._device.close()
            self._device = None
        if was_playing:
            self.finished.emit()

    def _on_tick(self):
        if self._device is None:
            return
        frame = self._play_start_frame + self._frames_sent
        if self._frames_sent >= self._total_frames:
            self.stop()
            return
        self.position_changed.emit(frame)
