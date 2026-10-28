"""Hear the sampler from the app (Qt-free): plain MIDI channel messages for the on-screen keyboard (notes and Panic). Nothing is written to the sampler's memory.

The velocity steps are the ones JV-Studio's keyboard uses.
"""

DEFAULT_VELOCITY = 100
VELOCITY_STEPS = [1, *range(10, 121, 10), 127]  # what C and V walk through: multiples of 10, with the two ends of the range kept


def step_velocity(current, direction):
    """Snap `current` to the nearest stop on VELOCITY_STEPS (a tie goes up), then move one stop up (direction > 0) or down: 97 -> 100 -> 110 or 90."""
    index = VELOCITY_STEPS.index(min(VELOCITY_STEPS, key=lambda v: (abs(v - current), -v)))
    return VELOCITY_STEPS[max(0, min(len(VELOCITY_STEPS) - 1, index + (1 if direction > 0 else -1)))]


def _channel(channel):
    if not 0 <= channel <= 15:
        raise ValueError(f"MIDI channel {channel} (0-15 here; the screen shows 1-16)")
    return channel


def _data(value, what):
    if not 0 <= value <= 127:
        raise ValueError(f"{what} {value} is outside 0-127")
    return value


def note_on(channel, note, velocity=DEFAULT_VELOCITY):
    return bytes([0x90 | _channel(channel), _data(note, "note"), max(1, _data(velocity, "velocity"))])  # (velocity 0 would be a note-off)


def note_off(channel, note):
    return bytes([0x80 | _channel(channel), _data(note, "note"), 0])


def control_change(channel, controller, value):
    return bytes([0xB0 | _channel(channel), _data(controller, "controller"), _data(value, "value")])


#: pitch bend with the wheel at rest: LSB 0, MSB 64
PITCH_CENTER = (0, 64)


def pitch_bend(channel, lsb, msb):
    return bytes([0xE0 | _channel(channel), _data(lsb, "pitch bend LSB"), _data(msb, "pitch bend MSB")])


def panic(channel):
    """Silence a channel: hold pedal up, All Sound Off, All Notes Off (CC 64, 120, 123)."""
    return [control_change(channel, 64, 0), control_change(channel, 120, 0), control_change(channel, 123, 0)]


#: the controllers a MIDI input may pass on: mod wheel, sustain pedal, All Sound Off, All Notes Off
INPUT_CONTROLLERS = (1, 64, 120, 123)


def from_input(message):
    """What to send the sampler for a message that came in on the keyboard bar's MIDI input, or None to drop it.

    Only notes, pitch bend and INPUT_CONTROLLERS get through, on the channel they arrived on. Everything else (SysEx above all, a DAW's clock, program changes) is dropped: this
    input must never be able to put anything but a note on the sampler's wire. A note-on with velocity 0 is the note-off it means."""
    if len(message) != 3 or message[0] < 0x80 or any(b > 0x7F for b in message[1:]):
        return None
    kind, channel = message[0] & 0xF0, message[0] & 0x0F
    if kind == 0x90 and message[2] > 0:
        return note_on(channel, message[1], message[2])
    if kind == 0x80 or kind == 0x90:
        return note_off(channel, message[1])
    if kind == 0xE0:
        return pitch_bend(channel, message[1], message[2])
    if kind == 0xB0 and message[1] in INPUT_CONTROLLERS:
        return control_change(channel, message[1], message[2])
    return None
