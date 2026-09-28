# Click-to-preview playback for the Slice Editor (ui/slice_editor_window.py,
# ui/slice_waveform_view.py) - the first audio output this app has ever
# opened (see AGENTS.md's Slice Editor section, "deliberately out of scope
# for this pass"). Everything MIDI-shaped elsewhere in this codebase talks
# to a sampler; this is the one place that talks to the computer's own
# speakers/interface instead.

import struct

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QTimer, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

from core import app_config, debug_log, sds_encoder

# how often the playhead position is re-read/re-emitted during playback -
# fast enough to look smooth, cheap enough that polling it costs nothing
# (processedUSecs() is a plain counter read, not a real query)
_PLAYHEAD_TICK_MS = 30


def list_output_devices():
    return QMediaDevices.audioOutputs()


def device_id_string(device):
    # QAudioDevice.id() is an opaque, platform-specific QByteArray - hex-
    # encoded so it round-trips through app_config's plain-JSON storage the
    # same way every other saved setting does (see save_ports/save_channel)
    return bytes(device.id()).hex()


def find_output_device(id_string):
    if not id_string:
        return None
    for device in QMediaDevices.audioOutputs():
        if device_id_string(device) == id_string:
            return device
    return None


def resolve_output_device():
    """The QAudioDevice a preview should actually play through: the user's
    saved choice if it still exists, otherwise the system default - a saved
    device can easily go missing (an interface unplugged since it was
    chosen), and silently falling back beats refusing to preview at all.
    """
    saved_id = app_config.get_saved_audio_output_device()
    device = find_output_device(saved_id) if saved_id else None
    if device is not None:
        return device
    if saved_id:
        debug_log.get_logger().error(
            f"audio_preview: saved output device {saved_id!r} not found - "
            "falling back to the system default"
        )
    return QMediaDevices.defaultAudioOutput()


class SlicePreviewPlayer(QObject):
    """Plays one mono 16-bit PCM slice at a time out the user's chosen audio
    output device (Settings > Audio Preview). Owns exactly one QAudioSink +
    QBuffer at a time - both must be kept alive on self for as long as
    they're playing (QAudioSink doesn't take ownership of its QIODevice,
    and a local reference getting garbage-collected mid-playback is a real
    crash, not just a leak). A new play() call always stops whatever's
    already playing first (retrigger, not queue/overlap) - matches how a
    user actually clicks through slices while chopping a break, quickly and
    repeatedly.

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
        self._sink = None
        self._buffer = None
        self._play_start_frame = 0
        self._play_end_frame = 0
        self._playback_framerate = 44100
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

        device = resolve_output_device()
        if device is None or device.isNull():
            debug_log.get_logger().error(
                "SlicePreviewPlayer: no audio output device available"
            )
            return

        chunk = samples[start_frame : end_frame + 1]
        if not chunk:
            return

        # Match the OUTPUT DEVICE's own native rate/channel count instead of
        # asking for this sample's own (mono, rarely device-native) rate
        # directly. Confirmed as a real source of the crackle/underrun
        # reports on Linux: a mismatch here makes PipeWire/PulseAudio
        # resample AND channel-upmix in real time on every playback, which
        # a small low-latency buffer is prone to glitching under -
        # reproduced against a PreSonus Studio 26 running its "Surround
        # 4.0" profile (native 48kHz/4ch) while this app requested
        # 44.1kHz/mono, with isFormatSupported() reporting the mismatched
        # request as "supported" the whole time (PulseAudio's compat layer
        # accepts almost any format and converts server-side, so this
        # never hit the fallback path below to reveal itself). Resampling
        # and channel-duplicating ONCE here, off the real-time path, and
        # only asking the device for a bit-depth conversion (int16 -> its
        # native sample type, a trivial/glitch-free conversion) avoids
        # that real-time work entirely.
        preferred = device.preferredFormat()
        device_rate = preferred.sampleRate() or framerate
        device_channels = preferred.channelCount() or 1

        fmt = QAudioFormat()
        fmt.setSampleRate(device_rate)
        fmt.setChannelCount(device_channels)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(fmt):
            debug_log.get_logger().error(
                f"SlicePreviewPlayer: {device.description()!r} doesn't "
                f"support {device_rate}Hz {device_channels}ch 16-bit - "
                "falling back to its own preferred format (pitch may be off)"
            )
            fmt = preferred
            device_rate = fmt.sampleRate()
            device_channels = fmt.channelCount()

        resampled, _ = sds_encoder.resample_to_target_rate(
            chunk, framerate, device_rate
        )
        if device_channels > 1:
            interleaved = []
            for sample in resampled:
                interleaved.extend([sample] * device_channels)
            resampled = interleaved
        pcm_bytes = struct.pack("<" + "h" * len(resampled), *resampled)

        try:
            sink = QAudioSink(device, fmt, self)
            buffer_frames = app_config.get_saved_audio_buffer_samples()
            sink.setBufferSize(fmt.bytesForFrames(buffer_frames))
            buffer = QBuffer(self)
            buffer.setData(QByteArray(pcm_bytes))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            sink.start(buffer)
        except Exception:
            debug_log.get_logger().error(
                "SlicePreviewPlayer: couldn't start playback", exc_info=True
            )
            return

        self._sink = sink
        self._buffer = buffer
        # the playhead is always reported in terms of the ORIGINAL sample's
        # own frame rate, regardless of whether fmt fell back to the
        # device's preferredFormat() above - processedUSecs() measures real
        # elapsed time, which converts to source frames via *framerate*
        # either way (a fallback already means pitch/speed are off; the
        # playhead tracking that too would just compound the same issue)
        self._play_start_frame = start_frame
        self._play_end_frame = end_frame
        self._playback_framerate = framerate
        self._timer.start()

    def stop(self):
        was_playing = self._sink is not None
        self._timer.stop()
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
            self._sink = None
        if self._buffer is not None:
            self._buffer.close()
            self._buffer.deleteLater()
            self._buffer = None
        if was_playing:
            self.finished.emit()

    def _on_tick(self):
        if self._sink is None:
            return
        state = self._sink.state()
        if state == QAudio.State.StoppedState:
            self.stop()
            return
        elapsed_seconds = self._sink.processedUSecs() / 1_000_000
        frame = self._play_start_frame + int(elapsed_seconds * self._playback_framerate)
        if frame >= self._play_end_frame or state == QAudio.State.IdleState:
            self.stop()
            return
        self.position_changed.emit(frame)
