import os

import mido

from core import app_config, debug_log
from core import midi_transport, sysex_trace
from PySide6.QtCore import QObject, Signal


def shared_transport_enabled():
    # public (no leading underscore) - core/program_editor_bridge.py's
    # connect() and ui/program_editor_window.py's own conditional Settings
    # menu item both need this exact same answer, not just MidiManager
    # itself, so this is the one place it's decided.
    #
    # MidiManager opens its ports through core/midi_transport.py's
    # raw-rtmidi SharedMidiInput/SharedMidiOutput instead of mido, and
    # exposes them (as raw_input/raw_output below) for
    # program_editor_bridge.connect() to build the Program Editor's own
    # S3kBridge from directly - one real connection instead of two.
    #
    # ON by default as of real-hardware validation (see AGENTS.md's "MIDI
    # transport consolidation" section and tests/midi_transport_
    # consolidation_test_plan.md) - config.json's "shared_midi_transport"
    # key is the persisted, manually-editable-by-hand override for an end
    # user who hits a problem with it (see app_config.get_shared_midi_
    # transport_enabled/ensure_defaults_saved - there's no
    # Settings UI for this on purpose). AKAISDS_SHARED_MIDI_TRANSPORT, if
    # explicitly set in the environment, takes priority over that - a
    # developer/tester override, same convention AKAISDS_DEMO_SAMPLER/
    # AKAISDS_DEMO_INSTANT already use elsewhere in this app. Recognizes
    # "0"/"false"/"no"/"off" (case-insensitive) as an explicit OFF, not
    # just an unset/empty check - bool(os.environ.get(...)) used to treat
    # the STRING "0" as truthy (any non-empty string is truthy in Python),
    # so AKAISDS_SHARED_MIDI_TRANSPORT=0 silently did nothing.
    #
    # Read live (not cached) either way, matching AKAISDS_DEMO_SAMPLER/
    # AKAISDS_DEMO_INSTANT's own convention, so tests can flip it per-case.
    env_value = os.environ.get("AKAISDS_SHARED_MIDI_TRANSPORT")
    if env_value is not None:
        return env_value.strip().lower() not in ("", "0", "false", "no", "off")
    return app_config.get_shared_midi_transport_enabled()


class MidiManager(QObject):
    # owns the real MIDI input/output ports - raw-rtmidi-backed and shared
    # with the Program Editor's own connection by default, or mido-backed
    # if shared_transport_enabled() above says otherwise

    sysex_received = Signal(bytes)
    connection_changed = Signal()

    def __init__(self):
        super().__init__()
        self.input_port = None
        self.output_port = None
        self.input_name = None
        self.output_name = None
        # only ever non-None while shared_transport_enabled(), and only
        # once a connection is actually open - the raw port objects
        # program_editor_bridge.connect() builds its own S3kBridge from
        self.raw_input = None
        self.raw_output = None
        # a bounded DEBUG trace of every SysEx in and out (core/sysex_trace.py) - the wire timeline for the Dashboard and the S950/Yamaha editors
        self._trace = sysex_trace.SysexTrace(debug_log.get_logger())

    @staticmethod
    def list_inputs():
        if shared_transport_enabled():
            return midi_transport.list_input_names()
        return mido.get_input_names()

    @staticmethod
    def list_outputs():
        if shared_transport_enabled():
            return midi_transport.list_output_names()
        return mido.get_output_names()

    def open_input(self, name):
        self.close_input()
        if name:
            try:
                if shared_transport_enabled():
                    self.raw_input = midi_transport.SharedMidiInput(name)
                    self.raw_input.set_message_callback(self._on_raw_message)
                else:
                    self.input_port = mido.open_input(name, callback=self._on_message)
            except Exception:
                debug_log.get_logger().error(
                    f"MidiManager: couldn't open input {name!r}", exc_info=True
                )
                raise
            self.input_name = name
            debug_log.get_logger().info(f"MidiManager: opened input {name!r}")
        self.connection_changed.emit()

    def close_input(self):
        if self.input_port is not None:
            self.input_port.close()
        if self.raw_input is not None:
            self.raw_input.close_port()
        self.input_port = None
        self.raw_input = None
        self.input_name = None

    def open_output(self, name):
        self.close_output()
        if name:
            try:
                if shared_transport_enabled():
                    self.raw_output = midi_transport.SharedMidiOutput(name)
                else:
                    self.output_port = mido.open_output(name)
            except Exception:
                debug_log.get_logger().error(
                    f"MidiManager: couldn't open output {name!r}", exc_info=True
                )
                raise
            self.output_name = name
            debug_log.get_logger().info(f"MidiManager: opened output {name!r}")
        self.connection_changed.emit()

    def close_output(self):
        if self.output_port is not None:
            self.output_port.close()
        if self.raw_output is not None:
            self.raw_output.close_port()
        self.output_port = None
        self.raw_output = None
        self.output_name = None

    def _on_message(self, message):
        if message.type == "sysex":
            self._trace.inbound(message.data)
            self.sysex_received.emit(bytes(message.data))

    def _on_raw_message(self, raw_message):
        # raw_message is a plain list[int] straight off the wire (see
        # core/midi_transport.py's _MessageFanout) - INCLUDING the F0/F7
        # SysEx wrapper bytes, unlike mido's own Message.data (which mido
        # already strips both from before _on_message ever sees it) -
        # stripped by hand here so sysex_received's own payload shape
        # matches exactly what every existing consumer (SamplerController)
        # already expects, regardless of which transport is active
        if not raw_message or raw_message[0] != midi_transport.SOX:
            return
        payload = (
            raw_message[1:-1]
            if raw_message[-1] == midi_transport.EOX
            else raw_message[1:]
        )
        self._trace.inbound(payload)
        self.sysex_received.emit(bytes(payload))

    def send_sysex(self, data_bytes):
        self._trace.out(data_bytes)
        if shared_transport_enabled():
            if self.raw_output is None:
                raise RuntimeError("No MIDI output port is open")
            try:
                self.raw_output.send_message(
                    [midi_transport.SOX, *data_bytes, midi_transport.EOX]
                )
            except Exception:
                debug_log.get_logger().error(
                    f"MidiManager: sysex send failed ({len(data_bytes)} bytes) on {self.output_name!r}",
                    exc_info=True,
                )
                raise
            return
        # note that mido automatically adds the F0/F7 bytes to the payload
        if self.output_port is None:
            raise RuntimeError("No MIDI output port is open")
        try:
            self.output_port.send(mido.Message("sysex", data=data_bytes))
        except Exception:
            debug_log.get_logger().error(
                f"MidiManager: sysex send failed ({len(data_bytes)} bytes) on {self.output_name!r}",
                exc_info=True,
            )
            raise

    def send_control_change(self, channel, control, value):
        if shared_transport_enabled():
            if self.raw_output is None:
                raise RuntimeError("No MIDI output port is open")
            self.raw_output.send_message(
                [0xB0 | (channel & 0x0F), control & 0x7F, value & 0x7F]
            )
            return
        if self.output_port is None:
            raise RuntimeError("No MIDI output port is open")
        self.output_port.send(
            mido.Message(
                "control_change", channel=channel, control=control, value=value
            )
        )
