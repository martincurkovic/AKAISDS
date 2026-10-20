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


def panic(channel):
    """Silence a channel: hold pedal up, All Sound Off, All Notes Off (CC 64, 120, 123)."""
    return [control_change(channel, 64, 0), control_change(channel, 120, 0), control_change(channel, 123, 0)]
