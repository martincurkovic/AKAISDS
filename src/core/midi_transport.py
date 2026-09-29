"""Shared raw-rtmidi transport: one real MIDI port pair, usable by both a
mido-style callback consumer (MidiManager, for the Transfer Dashboard's
SamplerController) and a poll-style consumer (s3k.bridge.S3kBridge, for the
Program Editor) at once - instead of two independent connections to the
same physical device.

Gated behind the AKAISDS_SHARED_MIDI_TRANSPORT env var (see
core/midi_manager.py) - OFF by default. This is new, not-yet-hardware-
validated code; see test_scripts/midi_transport_consolidation_test_plan.md
for exactly what real-hardware testing it needs before that default flips.

Split into two layers on purpose:
  - _MessageFanout / the write-lock logic in SharedMidiOutput.send_message
    are pure (or near-pure) and fully unit-testable without any real MIDI
    backend - see tests/test_midi_transport.py.
  - SharedMidiInput/SharedMidiOutput's own port-opening needs a real rtmidi
    backend (and, to mean anything, real hardware) - not exercised by the
    automated test suite, covered instead by the hardware test plan above.
"""

import queue
import threading

import rtmidi

from core import debug_log

SOX = 0xF0
EOX = 0xF7


def _delete_quiet(port):
    # same defensive swallow s3k.bridge._delete_quiet already uses - an
    # rtmidi port's underlying native object needs an explicit delete() on
    # some backends (a comment in s3k.bridge notes ALSA client-slot
    # exhaustion if this is skipped), but failing to delete is never worth
    # raising over
    try:
        port.delete()
    except Exception:
        pass


def list_input_names():
    port = rtmidi.MidiIn()
    try:
        return port.get_ports()
    finally:
        _delete_quiet(port)


def list_output_names():
    port = rtmidi.MidiOut()
    try:
        return port.get_ports()
    finally:
        _delete_quiet(port)


def _default_input_port_factory(port_name):
    port = rtmidi.MidiIn(queue_size_limit=8192)
    names = port.get_ports()
    if port_name not in names:
        _delete_quiet(port)
        raise RuntimeError(f"no input port named {port_name!r}; have {names}")
    port.open_port(names.index(port_name))
    port.ignore_types(sysex=False)
    return port


def _default_output_port_factory(port_name):
    port = rtmidi.MidiOut()
    names = port.get_ports()
    if port_name not in names:
        _delete_quiet(port)
        raise RuntimeError(f"no output port named {port_name!r}; have {names}")
    port.open_port(names.index(port_name))
    return port


class _MessageFanout:
    """One incoming message, delivered to two consumers - kept dependency-
    free (no rtmidi import, no port object) specifically so it's testable
    with synthetic messages and no real MIDI backend at all.

    - a thread-safe queue, drained via get_message() - deliberately shaped
      to match s3k.bridge.MultiIn.get_message()'s own contract exactly:
      non-blocking, returns None if nothing is pending, otherwise
      [message_bytes_list, delta_time] - s3k.bridge._receive polls this in
      a tight sleep loop and must see the identical shape it already gets
      from a non-shared MultiIn.
    - an optional plain callback, invoked synchronously inside push() -
      matching MidiManager._on_message's existing contract of being called
      from whatever thread the real MIDI callback fires on (mido's rtmidi
      backend already delivers this way; this preserves that, it doesn't
      change it).
    """

    def __init__(self):
        self._queue = queue.Queue()
        self._callback = None
        self._callback_lock = threading.Lock()

    def set_message_callback(self, callback):
        with self._callback_lock:
            self._callback = callback

    def push(self, message, delta_time, logger=None):
        self._queue.put((message, delta_time))
        with self._callback_lock:
            callback = self._callback
        if callback is None:
            return
        try:
            callback(message)
        except Exception:
            if logger is not None:
                logger.error(
                    "_MessageFanout: message callback raised", exc_info=True
                )

    def get_message(self):
        try:
            message, delta_time = self._queue.get_nowait()
        except queue.Empty:
            return None
        return [message, delta_time]


class SharedMidiInput:
    """One real rtmidi.MidiIn, fanned out via _MessageFanout (see its own
    docstring) to a poll-style consumer (s3k.bridge.S3kBridge, via
    get_message()) and a callback-style consumer (MidiManager) at once.

    rtmidi only supports ONE set_callback registration per MidiIn instance,
    and treats callback-mode and poll-mode (get_message()) as mutually
    exclusive on one real port - reconciling S3kBridge's poll model with
    MidiManager's callback model onto ONE real port, without either one
    ever missing a message, is the reason this class (and the
    AKAISDS_SHARED_MIDI_TRANSPORT flag gating it) exists at all.
    """

    def __init__(self, port_name, *, _port_factory=_default_input_port_factory):
        self._logger = debug_log.get_logger()
        self._port = _port_factory(port_name)
        self._fanout = _MessageFanout()
        self._port.set_callback(self._on_rtmidi_message)
        self._logger.info(f"SharedMidiInput: opened input {port_name!r}")

    def set_message_callback(self, callback):
        self._fanout.set_message_callback(callback)

    def _on_rtmidi_message(self, event, _data=None):
        # rtmidi's own callback shape: event = (message: list[int], delta_time)
        message, delta_time = event
        self._fanout.push(message, delta_time, logger=self._logger)

    # -- s3k.bridge.MultiIn-compatible poll interface (the ONLY two methods
    # S3kBridge itself actually calls on self.inp: get_message() from
    # _drain/_receive) -------------------------------------------------------

    def get_message(self):
        return self._fanout.get_message()

    def close_port(self):
        self._port.close_port()
        _delete_quiet(self._port)
        self._logger.info("SharedMidiInput: closed input")


class SharedMidiOutput:
    """One real rtmidi.MidiOut, with a hard lock around every actual write.

    Once two logical senders (SamplerController's own sends, S3kBridge's
    own writes via ThrottledOut) can reach the same physical output, a
    SysEx frame from one could otherwise be interleaved mid-transmission
    with one from the other - corrupting both on the wire. This lock is
    what makes a send atomic regardless of which thread it comes from; it
    is NOT a substitute for s3k.bridge.ThrottledOut's own inter-message
    pacing (still wrapped around this from the S3kBridge side - see
    program_editor_bridge.connect()), which is about not flooding the
    device, a different concern from not corrupting one frame.
    """

    def __init__(self, port_name, *, _port_factory=_default_output_port_factory):
        self._logger = debug_log.get_logger()
        self._port = _port_factory(port_name)
        self._write_lock = threading.Lock()
        self._logger.info(f"SharedMidiOutput: opened output {port_name!r}")

    def send_message(self, message):
        with self._write_lock:
            self._port.send_message(message)

    def close_port(self):
        self._port.close_port()
        _delete_quiet(self._port)
        self._logger.info("SharedMidiOutput: closed output")
