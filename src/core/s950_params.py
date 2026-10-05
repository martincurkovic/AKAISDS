"""Which Akai S900/S950 program fields the editor may change, their limits, and the
diff/validate/apply helpers a write is built from (Stage 5). Pure - no Qt, no MIDI.

A write is "set exactly these fields": the editor's working copy is DIFFED against the
program as it was loaded (`diff_programs`), the changes are validated, and only those
changes are applied onto a FRESH read of the program (`apply_changes`) right before it is
sent. Everything not changed - including every byte `core/s950_program.py` doesn't model,
and anything edited on the front panel in the meantime - passes through untouched.

**The limits below are s950tools' documented ranges (comments in `core/s950_program.py`),
none verified on a unit, plus one of our own (`TRANSPOSE_LIMIT_ST`).** They are guards against
typos and masked wire values (`Program.to_payload` masks silently), not claims about what the
hardware accepts. A field whose CURRENT value is outside its range is left alone unless the
user changes it (`validate_changes` only looks at changes).
"""

import dataclasses

from core import s950_sysex as s
from core.s950_program import MAX_KEYGROUPS

# program header fields the editor offers
PROGRAM_FIELDS = (
    "name",
    "key_tilt",
    "positional_xfade",
    "midi_program_number",
    "enable_midi_program",
)

# keygroup fields the editor offers. NOT offered: control_bits (the bit meanings are
# inferred), vel_xfade_50 and every reserved/undefined byte - they stay as read.
KEYGROUP_FIELDS = (
    "lower_key",
    "upper_key",
    "velocity_switch",
    "attack",
    "decay",
    "sustain",
    "release",
    "filter_attack",
    "filter_decay",
    "filter_sustain",
    "filter_release",
    "filter_vel",
    "filter_key_track",
    "attack_vel",
    "release_vel",
    "loudness_vel",
    "pitch_warp_vel",
    "pitch_warp_offset",
    "pitch_warp_recovery",
    "adsr_to_vcf",
    "aftertouch_depth",
    "modwheel_depth",
    "lfo_build",
    "lfo_rate",
    "lfo_depth",
    "voice_out",
    "midi_offset",
    "soft_sample",
    "soft_transpose",
    "soft_filter",
    "soft_loudness",
    "loud_sample",
    "loud_transpose",
    "loud_filter",
    "loud_loudness",
)

# OUR conservative limit, not the hardware's: 1/16-semitone units, +-2 octaves
TRANSPOSE_LIMIT_ST = 24
_TRANSPOSE_LIMIT_RAW = TRANSPOSE_LIMIT_ST * 16

PROGRAM_RANGES = {
    "key_tilt": (-50, 50),
    "midi_program_number": (0, 127),
}

KEYGROUP_RANGES = {
    "lower_key": (24, 127),
    "upper_key": (24, 127),
    "velocity_switch": (0, 128),  # 128 = no switch
    "attack": (0, 99),
    "decay": (0, 99),
    "sustain": (0, 99),
    "release": (0, 99),
    "filter_attack": (0, 99),
    "filter_decay": (0, 99),
    "filter_sustain": (0, 99),
    "filter_release": (0, 99),
    "filter_vel": (0, 99),
    "filter_key_track": (0, 99),
    "attack_vel": (0, 99),
    "release_vel": (-50, 50),
    "loudness_vel": (0, 99),
    "pitch_warp_vel": (0, 99),
    "pitch_warp_offset": (-50, 50),
    "pitch_warp_recovery": (0, 99),
    "adsr_to_vcf": (-50, 50),
    "aftertouch_depth": (0, 99),
    "modwheel_depth": (0, 99),
    "lfo_build": (0, 99),
    "lfo_rate": (0, 99),
    "lfo_depth": (0, 99),
    "midi_offset": (0, 15),
    "soft_transpose": (-_TRANSPOSE_LIMIT_RAW, _TRANSPOSE_LIMIT_RAW),
    "soft_filter": (0, 99),
    "soft_loudness": (-50, 50),
    "loud_transpose": (-_TRANSPOSE_LIMIT_RAW, _TRANSPOSE_LIMIT_RAW),
    "loud_filter": (0, 99),
    "loud_loudness": (-50, 50),
}

# keygroup voice_out values: 255 = all outputs, 0..9 individual (8/9 = left/right groups)
VOICE_OUT_ALL = 255
VOICE_OUT_VALUES = (VOICE_OUT_ALL, *range(10))

_NAME_FIELDS = ("name", "soft_sample", "loud_sample")


@dataclasses.dataclass(frozen=True)
class Change:
    keygroup: object  # keygroup index, or None for a program-header field
    attr: str
    old: object
    new: object

    def describe(self):
        where = "program" if self.keygroup is None else f"keygroup {self.keygroup + 1}"
        return f"{where} {self.attr}: {self.old!r} -> {self.new!r}"


def diff_programs(baseline, edited):
    """The editable fields where `edited` differs from `baseline`.

    Raises ValueError if the keygroup counts differ - adding/removing keygroups isn't
    supported (it changes the message length and has never been tried on a unit).
    """
    if baseline.num_keygroups != edited.num_keygroups:
        raise ValueError(
            f"changing the number of keygroups ({baseline.num_keygroups} -> "
            f"{edited.num_keygroups}) isn't supported"
        )
    changes = []
    for attr in PROGRAM_FIELDS:
        old, new = getattr(baseline, attr), getattr(edited, attr)
        if old != new:
            changes.append(Change(None, attr, old, new))
    for index, (before, after) in enumerate(zip(baseline.keygroups, edited.keygroups)):
        for attr in KEYGROUP_FIELDS:
            old, new = getattr(before, attr), getattr(after, attr)
            if old != new:
                changes.append(Change(index, attr, old, new))
    return changes


def validate_changes(changes):
    """Human-readable problems with `changes` (empty list = fine). Only CHANGED fields
    are checked, so a value the unit already holds outside our assumed range survives."""
    problems = []
    for c in changes:
        where = "Program" if c.keygroup is None else f"Keygroup {c.keygroup + 1}"
        if c.attr in _NAME_FIELDS:
            problem = _name_problem(c.new)
            if problem:
                problems.append(f"{where} {c.attr.replace('_', ' ')}: {problem}")
        elif c.attr == "voice_out":
            if c.new not in VOICE_OUT_VALUES:
                problems.append(f"{where} output must be 'all' or 0-9, not {c.new}")
        elif c.attr in ("positional_xfade", "enable_midi_program"):
            if not isinstance(c.new, bool):
                problems.append(f"{where} {c.attr} must be on or off")
        else:
            lo, hi = (PROGRAM_RANGES if c.keygroup is None else KEYGROUP_RANGES)[c.attr]
            if not isinstance(c.new, int) or isinstance(c.new, bool) or not lo <= c.new <= hi:
                problems.append(
                    f"{where} {c.attr.replace('_', ' ')} must be {lo}..{hi}, not {c.new}"
                )
    return problems


def validate_key_ranges(program):
    """Every keygroup's lower key must not exceed its upper key (the unit's own rule
    isn't known; an inverted zone is never what anyone meant)."""
    return [
        f"Keygroup {i + 1}: lower key is above the upper key"
        for i, kg in enumerate(program.keygroups)
        if kg.lower_key > kg.upper_key
    ]


def apply_changes(program, changes):
    """A copy of `program` with `changes` applied (the input is not modified). Every
    field not in `changes`, and all unmodelled raw bytes, are carried over as they are."""
    keygroups = list(program.keygroups)
    header = {}
    per_keygroup = {}
    for c in changes:
        if c.keygroup is None:
            header[c.attr] = c.new
        else:
            if not 0 <= c.keygroup < len(keygroups):
                raise ValueError(
                    f"the program on the sampler has {len(keygroups)} keygroup(s), "
                    f"but a change targets keygroup {c.keygroup + 1} - reload it first"
                )
            per_keygroup.setdefault(c.keygroup, {})[c.attr] = c.new
    for index, values in per_keygroup.items():
        keygroups[index] = dataclasses.replace(keygroups[index], **values)
    return dataclasses.replace(program, keygroups=keygroups, **header)


def _name_problem(value):
    if not isinstance(value, str):
        return "must be text"
    if len(value) > s.NAME_LENGTH:
        return f"is longer than {s.NAME_LENGTH} characters"
    if any(not 0x20 <= ord(ch) <= 0x7E for ch in value):
        return "has characters other than plain ASCII"
    return None


def program_problems(program, changes=()):
    """Everything wrong with writing `program` - the changed fields plus whole-program
    rules - for callers that hold the final program."""
    problems = validate_changes(list(changes))
    if not 1 <= program.num_keygroups <= MAX_KEYGROUPS:
        problems.append(f"a program needs 1..{MAX_KEYGROUPS} keygroups")
    problems += validate_key_ranges(program)
    return problems
