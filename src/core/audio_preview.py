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

import atexit
import struct
import weakref

import miniaudio
from PySide6.QtCore import QObject, QTimer, Signal

from core import app_config, debug_log

# every player that may own a running miniaudio device - stopped at interpreter exit (see _stop_all_players)
_LIVE_PLAYERS = weakref.WeakSet()


def _stop_all_players():
    """Stop every player's device before Python finalizes. A miniaudio device's real-time CoreAudio thread calls back INTO Python (a cffi
    callback that takes the GIL); if the process starts shutting down while one is still running, that thread is forced to exit inside the
    callback and macOS kills the whole process (SIGTRAP in `_os_workgroup_tsd_cleanup`, crash report 2026-10-07: a failed test left a
    preview running at exit; quitting the app mid-preview could do the same)."""
    for player in list(_LIVE_PLAYERS):
        try:
            player.stop()
        except Exception:  # noqa: BLE001 - at exit there is nothing useful left to do with a failure
            pass


atexit.register(_stop_all_players)

# how often the playhead position is re-read/re-emitted during playback -
# fast enough to look smooth, cheap enough that polling it costs nothing
_PLAYHEAD_TICK_MS = 30

_SAMPLE_FORMAT = miniaudio.SampleFormat.SIGNED16
_BYTES_PER_FRAME = 2  # SIGNED16, mono (1 channel) - struct.pack("<h", ...)


def _pack_values(left, right=None):
    """Little-endian int16 PCM for already-sliced channel lists: mono, or interleaved L R L R when `right` is given
    (the shorter channel decides the length)."""
    if right is None:
        return struct.pack("<" + "h" * len(left), *left)
    n = min(len(left), len(right))
    interleaved = [0] * (2 * n)
    interleaved[0::2] = left[:n]
    interleaved[1::2] = right[:n]
    return struct.pack("<" + "h" * len(interleaved), *interleaved)


def _pack_region(left, right, lo, hi):
    """The inclusive [lo, hi] frames of `left` (and `right`, for a stereo preview) as PCM bytes."""
    return _pack_values(left[lo : hi + 1], None if right is None else right[lo : hi + 1])


def _resample_region_for_cents(samples, lo, hi, cents):
    """The [lo, hi] (inclusive) region of samples, resampled to sound
    `cents` cents higher (positive) or lower (negative) - plain linear-
    interpolation resampling, same technique a hardware sampler's own tune
    control uses: shifting the effective playback rate shifts pitch AND
    duration together (a real pitch-preserving time-stretch is a lot more
    machinery than a quick, live-updating loop preview needs). Matches
    SHLTO's own +/-50 cent range (see tooltips.SAMPLE_LOOP_TUNE_KNOB) - at
    most a ~3% rate change, so the duration shift is barely audible.
    """
    region = samples[lo : hi + 1]
    if cents == 0 or len(region) < 2:
        return region
    ratio = 2.0 ** (cents / 1200.0)
    out_len = max(1, round(len(region) / ratio))
    last_index = len(region) - 1
    out = [0] * out_len
    for i in range(out_len):
        src_pos = i * ratio
        idx = int(src_pos)
        if idx >= last_index:
            out[i] = region[last_index]
            continue
        frac = src_pos - idx
        a = region[idx]
        b = region[idx + 1]
        out[i] = int(round(a + (b - a) * frac))
    return out


def _shifted_framerate(framerate, pitch_shift_semitones):
    if not pitch_shift_semitones:
        return framerate
    return max(1, round(framerate * 2.0 ** (pitch_shift_semitones / 12.0)))


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
    """Plays one mono 16-bit PCM region at a time out the user's chosen audio
    output device (Settings > Audio Preview), via a miniaudio.PlaybackDevice.
    Owns exactly one device at a time - a new play()/play_loop() call always
    stops whatever's already playing first (retrigger, not queue/overlap) -
    matches how a user actually clicks through slices while chopping a
    break, quickly and repeatedly.

    miniaudio pulls audio through a plain Python generator, invoked from
    its own native, high-priority real-time audio thread (not the Qt/GUI
    thread) every time it needs more frames - so a generator closure below
    must not do anything slow or blocking. It tracks its own progress
    (self._current_frame, self._finished) as plain attributes; the
    GUI-thread poll timer below only ever READS them, never writes them, so
    there's nothing to lock (Python attribute assignment/read is already
    atomic enough for polling like this - a torn read would at worst show a
    playhead one tick stale, not a crash).

    Also drives a waveform's playhead: while something is sounding,
    position_changed(frame) fires roughly every _PLAYHEAD_TICK_MS with the
    current absolute frame position (same indexing as the source *samples*
    passed in), and finished() fires once when playback stops for any
    reason (ran off the end, or a caller/retrigger/click-to-stop cut it
    off) - callers wire both straight into a waveform view's own
    set_playhead()/clear_playhead() (SliceWaveformView for play(),
    WaveformView for play_loop() - see program_editor_window.py).
    """

    position_changed = Signal(int)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        _LIVE_PLAYERS.add(self)
        self._device = None
        self._current_frame = 0
        # set True by a generator right before it ends, for any reason
        # (ran off the end, or an internal error - see each generator's own
        # try/finally) - _on_tick is what actually notices and stops the
        # device. Stays False indefinitely for play_loop()'s own "Hold"
        # case (dwell_ms=None) - that generator never reaches its own end
        # on its own, only via an external stop() call (e.g. the user
        # clicking the waveform again to stop it - see WaveformView's own
        # preview_requested).
        self._finished = False
        # live loop-point bounds for an in-progress play_loop() - see its
        # own docstring and update_loop_points() below. Meaningless (never
        # read) outside an active play_loop() call, so a default of 0 here
        # is only ever there to make update_loop_points() callable safely
        # before the first play_loop() ever happens, not a real value.
        self._live_loop_start = 0
        self._live_loop_end = 0
        # live loop-tune (SHLTO) cents offset for an in-progress play_loop()
        # - see update_loop_tune_cents() below. Same "meaningless outside an
        # active play_loop() call" reasoning as _live_loop_start/_live_loop_
        # end above.
        self._live_loop_tune_cents = 0
        self._timer = QTimer(self)
        self._timer.setInterval(_PLAYHEAD_TICK_MS)
        self._timer.timeout.connect(self._on_tick)

    def is_playing(self):
        return self._device is not None

    def _open_device(self, framerate, channels=1):
        # shared by play()/play_loop() - resolves the configured output
        # device/buffer size the same way for both; returns None (having
        # already logged) on failure, never raises
        device_entry = resolve_output_device()
        device_id = device_entry["id"] if device_entry is not None else None

        # app_config stores this as a frame count (see its own comment,
        # matching an Ableton-style buffer-size dropdown), but miniaudio
        # wants milliseconds - converted against THIS sound's own rate,
        # same reasoning core/audio_preview.py always used framerate (not a
        # fixed 44100) for anything time-based here.
        buffer_frames = app_config.get_saved_audio_buffer_samples()
        buffersize_msec = max(1, round(buffer_frames / framerate * 1000))

        try:
            return miniaudio.PlaybackDevice(
                output_format=_SAMPLE_FORMAT,
                nchannels=channels,
                sample_rate=framerate,
                buffersize_msec=buffersize_msec,
                device_id=device_id,
            )
        except miniaudio.MiniaudioError:
            debug_log.get_logger().error(
                "SlicePreviewPlayer: couldn't open output device", exc_info=True
            )
            return None

    def _open_device_for(self, framerate, right_samples):
        # mono keeps the one-argument call every existing caller (and test double) of _open_device() knows
        return self._open_device(framerate) if right_samples is None else self._open_device(framerate, 2)

    def _start(self, device, generator_fn, start_frame):
        # primed with an empty yield first - see miniaudio's own
        # stream_raw_pcm_memory, this is its documented generator protocol,
        # not specific to this app
        gen = generator_fn()
        next(gen)

        try:
            device.start(gen)
        except Exception:
            debug_log.get_logger().error(
                "SlicePreviewPlayer: couldn't start playback", exc_info=True
            )
            device.close()
            return

        self._device = device
        self._current_frame = start_frame
        self._finished = False
        self._timer.start()

    def play(self, samples, start_frame, end_frame, framerate, pitch_shift_semitones=0, right_samples=None):
        """samples: the full mono int16 sample list/array already loaded for
        this sound (same list slice_bounds()' frame indices are relative
        to); start_frame/end_frame: one slice_bounds() entry, inclusive.
        Never raises into a Qt mouse-event slot - a misconfigured or
        unplugged output device logs and no-ops instead.

        pitch_shift_semitones: an overall pitch offset applied to the WHOLE
        chunk by opening the output device at a scaled sample rate (same
        "shift rate, shift pitch+duration together" technique
        _resample_region_for_cents uses, just via the device's own rate
        instead of resampling the data) - see program_editor_window.py's
        own caller for why this exists (STUNO, the sample's own tuning
        offset, has to shift the ENTIRE preview, not just a loop region).
        """
        # right_samples: the right channel of a STEREO sample (same length/indexing as `samples`, which is then the
        # left one) - played as real two-channel audio. None = the mono behaviour every other caller has always had.
        self.stop()

        chunk = samples[start_frame : end_frame + 1]
        if len(chunk) == 0:
            return
        pcm_bytes = _pack_region(samples, right_samples, start_frame, end_frame)
        bytes_per_frame = _BYTES_PER_FRAME * (1 if right_samples is None else 2)

        device = self._open_device_for(_shifted_framerate(framerate, pitch_shift_semitones), right_samples)
        if device is None:
            return

        def generator():
            try:
                required_frames = yield b""
                pos = 0
                while pos < len(pcm_bytes):
                    end = pos + required_frames * bytes_per_frame
                    out = pcm_bytes[pos:end]
                    pos += len(out)
                    self._current_frame = start_frame + pos // bytes_per_frame
                    if len(out) < required_frames * bytes_per_frame:
                        # the true final chunk, shorter than what was
                        # asked for - miniaudio.PlaybackDevice._data_
                        # callback only memmove()s exactly len(out) bytes
                        # into its native output buffer; zero-padding here
                        # (silence) fills the rest deliberately rather
                        # than leaving whatever native memory was already
                        # there (stale audio from an earlier callback) to
                        # play as an audible click right at the very end
                        out = out + b"\x00" * (
                            required_frames * bytes_per_frame - len(out)
                        )
                    required_frames = yield out
                # exhausted: from here the device just keeps calling back
                # for more (StopIteration) and gets silence, harmless -
                # the finally below is what actually gets _on_tick to stop
                # the device for real
            except Exception:
                # this runs on miniaudio's own native real-time audio
                # thread, invoked through a cffi callback - an uncaught
                # exception here doesn't reach this app's normal call
                # stack at all (miniaudio.PlaybackDevice._data_callback
                # re-raises it straight into the cffi boundary), so
                # without this it's either silently swallowed by cffi's
                # own default callback error handling or, worst case,
                # takes the process down - either way invisible in a
                # packaged build with no console. Logging (and letting the
                # finally below end the preview cleanly) avoids that.
                debug_log.get_logger().error(
                    "SlicePreviewPlayer: playback generator raised",
                    exc_info=True,
                )
            finally:
                self._finished = True

        self._start(device, generator, start_frame)

    def play_loop(
        self,
        samples,
        start_frame,
        loop_start_frame,
        loop_end_frame,
        end_frame,
        framerate,
        dwell_ms=None,
        loop_tune_cents=0,
        pitch_shift_semitones=0,
        right_samples=None,
    ):
        """Simulates a sample's own loop settings for a single click preview
        (the Program Editor's Samples tab waveform - see
        program_editor_window.py's _on_waveform_preview_requested): plays
        the attack once ([start_frame, loop_end_frame]), then repeats the
        loop region ([loop_start_frame, loop_end_frame]) either for
        dwell_ms milliseconds or, if dwell_ms is None ("Hold"),
        indefinitely - never stopping on its own, only via a later stop()
        call (e.g. the user clicking the waveform again) - then plays the
        release tail ([loop_end_frame, end_frame]) once and stops on its
        own. A dwell that doesn't divide evenly into the loop region's own
        length always finishes its CURRENT pass before moving on to the
        tail, rather than cutting off mid-loop - matches "Loop in
        release"'s own real hardware behaviour, and avoids an audible glitch
        either way.

        Callers decide whether a loop applies at ALL (a "No looping"/
        "One-shot" SPTYPE, or a dwell of 0/"Off", should call plain play()
        instead) - this method always assumes there IS one to repeat.

        Live loop-point dragging: while this is playing, a caller can call
        update_loop_points(new_loop_start, new_loop_end) at any time (e.g.
        on every WaveformView.markers_changed tick during a drag) and the
        LOOP REGION - only that, not the attack/tail, which have either
        already played once or haven't started yet - picks up the new
        bounds at the start of its next pass, without stopping/restarting
        playback. This is what lets a user dial in exact loop points by
        ear against continuous audio instead of stop/start/stop/start.
        Deferred to the next pass boundary rather than applied instantly
        mid-buffer, same reasoning dwell already finishes its current pass
        before moving to the tail (see above) - jumping to an arbitrary
        new position mid-buffer is an audible click, not just a design
        nicety.

        loop_tune_cents (SHLTO): a fine pitch offset applied ONLY to the
        loop region, same as real hardware (see tooltips.
        SAMPLE_LOOP_TUNE_KNOB) - the attack and tail always play at the
        sample's own recorded pitch. update_loop_tune_cents() live-updates
        this the same deferred-to-next-pass way update_loop_points() does.

        pitch_shift_semitones: see play()'s own docstring - applied to the
        WHOLE playback (attack, loop, and tail alike) via the device's own
        opened sample rate, so it stacks on top of loop_tune_cents' own
        loop-only resampling rather than replacing it.

        right_samples: see play() - the right channel of a stereo sample; every segment (attack, loop, tail) is then
        interleaved two-channel audio and both channels share the loop points.
        """
        self.stop()

        if loop_end_frame <= loop_start_frame:
            # degenerate/zero-length loop region - nothing to repeat, falls
            # back to playing the whole range once rather than looping an
            # empty buffer forever
            self.play(
                samples, start_frame, end_frame, framerate, pitch_shift_semitones, right_samples=right_samples
            )
            return

        channels = 1 if right_samples is None else 2
        bytes_per_frame = _BYTES_PER_FRAME * channels
        attack_bytes = _pack_region(samples, right_samples, start_frame, loop_end_frame)
        tail_bytes = _pack_region(samples, right_samples, loop_end_frame, end_frame)

        loop_frame_budget = (
            None if dwell_ms is None else max(0, round(dwell_ms / 1000 * framerate))
        )

        # live, mutable loop bounds/tune - update_loop_points()/
        # update_loop_tune_cents() below write these; the generator reads
        # them fresh at the top of every pass. Starts at whatever
        # play_loop() was actually called with.
        self._live_loop_start = loop_start_frame
        self._live_loop_end = loop_end_frame
        self._live_loop_tune_cents = loop_tune_cents

        device = self._open_device_for(_shifted_framerate(framerate, pitch_shift_semitones), right_samples)
        if device is None:
            return

        def _loop_region_bytes(lo, hi, cents):
            region = _resample_region_for_cents(samples, lo, hi, cents)
            right_region = None if right_samples is None else _resample_region_for_cents(right_samples, lo, hi, cents)
            return _pack_values(region, right_region)

        def _segments():
            # yields (segment_start_frame, raw_bytes) pairs in play order:
            # the attack once, then the loop region repeated for
            # loop_frame_budget frames (or forever - "Hold" - if
            # loop_frame_budget is None), then the release tail once.
            # segment_start_frame is the absolute sample frame the FIRST
            # byte of that segment corresponds to, for the generator
            # below's own self._current_frame tracking as it consumes
            # bytes across segment boundaries.
            #
            # Live loop-point updates (update_loop_points(), e.g. from a
            # WaveformView.markers_changed tick during a drag) are only
            # picked up here, between whole passes - never mid-pass, same
            # as before this was split out of the generator itself - a
            # dwell that doesn't divide evenly into the loop region's own
            # length still always finishes its CURRENT pass before
            # rechecking the budget or moving to the tail.
            if attack_bytes:
                yield start_frame, attack_bytes

            looped_frames = 0
            cur_loop_start = loop_start_frame
            cur_loop_end = loop_end_frame
            cur_loop_cents = self._live_loop_tune_cents
            loop_bytes = _loop_region_bytes(cur_loop_start, cur_loop_end, cur_loop_cents)
            while loop_frame_budget is None or looped_frames < loop_frame_budget:
                live_start = self._live_loop_start
                live_end = self._live_loop_end
                live_cents = self._live_loop_tune_cents
                if live_end > live_start and (live_start, live_end, live_cents) != (
                    cur_loop_start,
                    cur_loop_end,
                    cur_loop_cents,
                ):
                    cur_loop_start, cur_loop_end, cur_loop_cents = (
                        live_start,
                        live_end,
                        live_cents,
                    )
                    loop_bytes = _loop_region_bytes(
                        cur_loop_start, cur_loop_end, cur_loop_cents
                    )
                looped_frames += len(loop_bytes) // bytes_per_frame
                yield cur_loop_start, loop_bytes

            # release tail, once - from the loop's own last-used end point
            # (cur_loop_end), which may have moved since play_loop() was
            # first called
            tail_start = cur_loop_end
            out_bytes = (
                _pack_region(samples, right_samples, tail_start, end_frame)
                if cur_loop_end != loop_end_frame
                else tail_bytes
            )
            if out_bytes:
                yield tail_start, out_bytes

        def generator():
            try:
                required_frames = yield b""
                # Every yield below must be EXACTLY required_frames frames
                # (never short) except the true final one at total
                # exhaustion - miniaudio.PlaybackDevice._data_callback
                # does `ffi.memmove(output, samples_bytes,
                # len(samples_bytes))` and nothing else: a short yield
                # only overwrites the FIRST len(samples_bytes) bytes of
                # its native output buffer, leaving the REST as whatever
                # was already there (stale audio from an earlier
                # callback). The previous version yielded a short chunk at
                # the end of EVERY loop pass whenever the loop region's
                # own length didn't divide evenly by the buffer size -
                # which for a short loop region even yielded the SAME
                # short chunk on every single pass - that stale-tail
                # garbage is exactly the "chop"/"glitch" reported at both
                # large and small buffer sizes. Concatenating seamlessly
                # across segment (and pass) boundaries (seg_iter/
                # _advance_segment below) is what actually fixes it;
                # _segments() above still owns what audio comes next, this
                # only owns chunking it to the size miniaudio actually
                # asked for.
                seg_iter = _segments()
                seg_start_frame = 0
                seg_bytes = b""
                seg_pos = 0

                def _advance_segment():
                    nonlocal seg_start_frame, seg_bytes, seg_pos
                    try:
                        seg_start_frame, seg_bytes = next(seg_iter)
                    except StopIteration:
                        seg_bytes = None
                    seg_pos = 0

                _advance_segment()
                while seg_bytes is not None:
                    need = required_frames * bytes_per_frame
                    out = bytearray()
                    while len(out) < need and seg_bytes is not None:
                        take_n = min(len(seg_bytes) - seg_pos, need - len(out))
                        out += seg_bytes[seg_pos : seg_pos + take_n]
                        seg_pos += take_n
                        self._current_frame = (
                            seg_start_frame + seg_pos // bytes_per_frame
                        )
                        if seg_pos >= len(seg_bytes):
                            _advance_segment()
                    if not out:
                        break
                    if len(out) < need:
                        # true final chunk (dwell_ms finite and every
                        # segment now exhausted) - same zero-pad-the-
                        # remainder reasoning as play()'s own generator,
                        # so miniaudio's memmove never leaves stale native
                        # buffer content to play as a click right at the
                        # very end
                        out += b"\x00" * (need - len(out))
                    required_frames = yield bytes(out)
                # exhausted (dwell_ms finite and reached, tail fully
                # played): from here the device just keeps calling back
                # for more (StopIteration) and gets silence, harmless -
                # the finally below is what actually gets _on_tick to stop
                # the device for real. Same as play()'s own generator.
            except Exception:
                # this runs on miniaudio's own native real-time audio
                # thread, invoked through a cffi callback - an uncaught
                # exception here doesn't reach this app's normal call
                # stack at all (miniaudio.PlaybackDevice._data_callback
                # re-raises it straight into the cffi boundary), so
                # without this it's either silently swallowed by cffi's
                # own default callback error handling or, worst case,
                # takes the process down - either way invisible in a
                # packaged build with no console. Logging (and letting the
                # finally below end the preview cleanly) avoids that.
                debug_log.get_logger().error(
                    "SlicePreviewPlayer: playback generator raised",
                    exc_info=True,
                )
            finally:
                self._finished = True

        self._start(device, generator, start_frame)

    def update_loop_points(self, loop_start_frame, loop_end_frame):
        """Live-updates the loop region of an in-progress play_loop() -
        see that method's own docstring. A no-op if nothing is currently
        looping (play() is active instead, or nothing's playing at all) or
        the given bounds are degenerate (end <= start) - the generator
        simply keeps whatever bounds it last had in either case, rather
        than being told to loop zero/negative frames.
        """
        if loop_end_frame <= loop_start_frame:
            return
        self._live_loop_start = loop_start_frame
        self._live_loop_end = loop_end_frame

    def update_loop_tune_cents(self, cents):
        """Live-updates the loop-tune (SHLTO) pitch offset of an
        in-progress play_loop() - see that method's own docstring. Same
        deferred-to-next-pass timing as update_loop_points(), and a safe
        no-op if nothing is currently looping (the generator simply keeps
        whatever cents it last had).
        """
        self._live_loop_tune_cents = cents

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
        if self._finished:
            self.stop()
            return
        self.position_changed.emit(self._current_frame)
