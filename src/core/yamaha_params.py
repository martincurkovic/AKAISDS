"""Yamaha A4000/A5000 parameter tables: where every editable value lives, as data.

Transcribed from the Yamaha service manual (MIDI DATA FORMAT): Table 2 (parameter-change numbering
P1..P6, pages 40-42) for *how to address a value with a parameter request/change*, and Table 1 (bulk
dump layouts, pages 37-38) for *where the same value sits in a bulk dump*. One `Param` row ties the
two together, so a whole object can be read from ONE bulk dump (fast) and a single value changed with
ONE parameter change (see core/yamaha_sysex.py).

HOW MUCH OF THIS HAS BEEN CHECKED: rows are checked against a REAL A4000 by
`tools/a4000_verify_params.py`, which reads every row both ways (parameter request vs the bulk
offset) and prints any disagreement. Run it after touching this file. `tests/test_yamaha_params.py`
pins the table's internal consistency and decodes the real captures in `tests/fixtures/a4000/`.
Anything the verifier has not been run over is only "as good as the manual".

Not covered yet (deliberately): the program's effect blocks (P2=21) and controls (P2=22), the
MIDI-channel bitmaps (P2=1, 2), the "linked to program" / bank-member flags of a sample, stereo R
addresses (the manual gives no P-numbers for them), and the system parameters (the manual documents
their P-numbers but no bulk layout).

Scopes - an object's values live in different places:
- "program": the program bulk payload (PG), `offset` is absolute.
- "easy_edit": one 56-byte Easy Edit block per assigned sample, starting at bulk offset 408; `offset`
  is relative to the block and a request needs the slot (P2*100+P3 = slot).
- "sample": the `[Sample Parameter]` block of the sample bulk (SP), which starts at bulk offset 112;
  `offset` is relative to that.

Bitfields: `bits=(shift, width)` takes those bits of the byte at `offset` (manual notation "b5-3" =
shift 3, width 3). A signed bitfield is two's complement over `width` bits (Easy Edit's -1/0/1 pairs).
"""

import dataclasses

PROGRAM_EASY_EDIT_BASE = 408
EASY_EDIT_BLOCK_SIZE = 56
SAMPLE_PARAMETER_BASE = 112

SCOPES = ("program", "easy_edit", "sample")


@dataclasses.dataclass(frozen=True)
class Param:
    key: str  # stable id, e.g. "program_level"
    name: str  # the manual's name
    scope: str  # "program" | "easy_edit" | "sample"
    p: tuple  # P1..P6 (an easy_edit row's P2/P3 are placeholders - see `request_params`)
    size: int  # bytes on the wire: 1 UC/SC, 2 US, 4 UL, or the text length for kind "text"
    signed: bool
    lo: int  # the manual's range (informational + validation); text rows 0
    hi: int
    offset: int  # byte offset, relative to the scope's base (see module docstring)
    bits: tuple = None  # (shift, width) within the byte at `offset`
    kind: str = "int"  # "int" | "text"
    read_only: bool = False
    a5000_only: bool = False
    enum: str = None  # name of a table in ENUMS
    bulk_only: bool = False  # in the bulk dump but has no P-number (can't be requested/changed singly)

    @property
    def bulk_size(self):
        """Bytes this value occupies in the bulk dump (a bitfield sits inside one byte)."""
        return 1 if self.bits else self.size


def _int(scope, key, name, p, lo, hi, offset, *, size=1, signed=None, **kw):
    if signed is None:
        signed = lo < 0
    return Param(key, name, scope, tuple(p), size, signed, lo, hi, offset, **kw)


def _text(scope, key, name, p, length, offset, **kw):
    return Param(key, name, scope, tuple(p), length, False, 0, 0, offset, kind="text", read_only=True, **kw)


def _pad6(p):
    return tuple(p) + (0,) * (6 - len(p))


# -- program ----------------------------------------------------------------------------------------
# P1 = 1. Bulk offsets from Table 1 "1.1.1 Program Bulk Dump" (page 37).


def _program_rows():
    P = lambda key, name, p2, lo, hi, off, **kw: _int("program", key, name, _pad6((1, p2)), lo, hi, off, **kw)  # noqa: E731
    rows = [
        _text("program", "program_name", "program name (read only)", _pad6((1, 0)), 8, 64),
        P("ad_in_on", "AD in on", 3, 0, 1, 72, bits=(0, 1)),
        P("ad_in_source", "AD in source", 4, 0, 2, 72, bits=(1, 2)),
        P("ad_in_l_pan", "AD in (L) pan", 5, -63, 63, 78),
        P("ad_in_l_output1", "AD in (L) output1", 6, 0, 12, 373, enum="output1"),
        P("ad_in_l_output1_level", "AD in (L) output1 level", 7, 0, 127, 374),
        P("ad_in_l_output2", "AD in (L) output2", 8, 0, 12, 375, enum="output2"),
        P("ad_in_l_output2_level", "AD in (L) output2 level", 9, 0, 127, 376),
        P("program_level", "program level", 10, 0, 127, 83),
        P("lfo_cycle", "program LFO cycle", 11, 0, 6, 73, bits=(0, 3)),
        P("lfo_sync", "program LFO sync", 12, 0, 1, 72, bits=(6, 2)),
        P("transpose", "program transpose", 13, -127, 127, 86),
        P("lfo_tempo", "program LFO tempo", 14, 25, 250, 92),
        P("lfo_wave", "program LFO wave", 15, 0, 6, 73, bits=(3, 3)),
        P("portamento_type", "program portamento type", 16, 0, 3, 88),
        P("portamento_rate", "program portamento rate", 17, 1, 127, 89),
        P("portamento_time", "program portamento time", 18, 1, 127, 90),
        P("sh_speed", "S/H speed", 19, 0, 127, 91),
        P("assigned_samples", "number of assigned samples", 20, 0, 999, 94, size=2, read_only=True),
        P("effect_connection", "effect1-3 connection", 23, 0, 4, 72, bits=(3, 3)),
        P("lfo_initial_phase", "program LFO initial phase", 24, 0, 3, 73, bits=(6, 2)),
        P("lfo_reset_midi_channel", "program LFO reset MIDI channel", 25, -2, 32, 87),
        # Table 2 calls this UC but its range includes -1 ("all"); Table 1 says SC
        P("lfo_reset_note", "program LFO reset note", 26, -1, 127, 93),
        P("ad_in_r_pan", "AD in R pan", 27, -63, 63, 377),
        P("ad_in_r_output1", "AD in R output1", 28, 0, 12, 378, enum="output1"),
        P("ad_in_r_output1_level", "AD in R output1 level", 29, 0, 127, 379),
        P("ad_in_r_output2", "AD in R output2", 30, 0, 12, 380, enum="output2"),
        P("ad_in_r_output2_level", "AD in R output2 level", 31, 0, 127, 381),
        P("effect456_connection", "effect4-6 connection", 32, 0, 4, 372, bits=(0, 3), a5000_only=True),
        P("lfo_step_total", "total steps of program LFO step wave", 33, 0, 6, 398, bits=(0, 3)),
        P("lfo_step_slope", "program LFO step wave slope", 34, 0, 3, 398, bits=(3, 2)),
    ]
    for n in range(16):
        rows.append(
            _int("program", f"lfo_step_value_{n + 1}", f"program LFO step wave value{n + 1}",
                 _pad6((1, 35, n)), 0, 127, 382 + n)
        )
    return rows


# -- easy edit --------------------------------------------------------------------------------------
# P1 = 2, P2*100+P3 = which assigned sample (0 = first), P4 = parameter, P5 = 0 for the offsets.
# Offsets relative to the 56-byte block (Table 1 "[Easy Edit Parameter]", page 38).


def _easy_edit_rows():
    E = lambda key, name, p4, lo, hi, off, p5=0, **kw: _int(  # noqa: E731
        "easy_edit", key, name, (2, 0, 0, p4, p5, 0), lo, hi, off, **kw
    )
    return [
        _text("easy_edit", "assigned_name", "assigned sample(bank) name (read only)", (2, 0, 0, 0, 0, 0), 16, 0),
        _int("easy_edit", "assigned_type", "assigned object type", (2, 0, 0, 0, 0, 0), 0, 255, 20,
             read_only=True, bulk_only=True),
        E("receive_channel", "MIDI receive channel assign", 2, -1, 32, 21),
        E("level_offset", "level offset", 3, -127, 127, 22),
        E("pan_offset", "pan offset", 4, -127, 127, 24),
        E("fine_tune_offset", "fine tune offset", 5, -127, 127, 26),
        E("coarse_tune_offset", "coarse tune offset", 6, -127, 127, 28),
        E("key_limit_high", "key limit high", 7, 0, 127, 30, p5=0),
        E("key_limit_low", "key limit low", 8, 0, 127, 31),
        E("key_range_shift", "key range shift", 9, -127, 127, 32),
        E("velocity_limit_high", "velocity limit high", 10, 0, 127, 33),
        E("velocity_limit_low", "velocity limit low", 11, 0, 127, 34),
        E("portamento", "portamento (-1 =sample, 0 off, 1 =program)", 12, -1, 1, 35, bits=(0, 2)),
        E("mono_mode", "mono mode (-1 =sample)", 13, -1, 1, 35, bits=(2, 2)),
        E("key_xfade_on", "key x-fade on (-1 =sample)", 14, -1, 1, 35, bits=(4, 2)),
        E("alternate_group", "alternate group number (-1 =sample)", 16, -1, 16, 36),
        E("aeg_attack_rate_offset", "AEG attack rate offset", 17, -127, 127, 37),
        E("aeg_release_rate_offset", "AEG release rate offset", 18, -127, 127, 39),
        E("filter_cutoff_offset", "filter cutoff offset", 19, -127, 127, 41),
        E("filter_q_offset", "filter Q/width offset", 20, -31, 31, 43),
        E("output1", "output1 (-1 =sample)", 21, -1, 12, 29, enum="output1"),
        E("output1_level_offset", "output1 level offset", 22, -127, 127, 47),
        E("output2", "output2 (-1 =sample)", 23, -1, 12, 40, enum="output2"),
        E("output2_level_offset", "output2 level offset", 24, -127, 127, 50),
        E("midi_control_on", "MIDI control on", 25, 0, 1, 51),
        E("aeg_decay_rate_offset", "AEG decay rate offset", 27, -127, 127, 38),
        E("filter_gain_offset", "filter gain offset", 28, -63, 63, 42),
        E("cutoff_distance_offset", "cutoff distance offset", 29, -127, 127, 44),
        E("velocity_xfade_low_offset", "velocity x-fade low offset", 30, -127, 127, 27),
        E("velocity_xfade_high_offset", "velocity x-fade high offset", 31, -127, 127, 25),
        E("velocity_sensitivity_offset", "velocity sensitivity", 32, -127, 127, 23),
    ]


# -- sample -----------------------------------------------------------------------------------------
# P1 = 2 ("Sample Parameter" half of Table 2, pages 41-42). Offsets relative to the 224-byte
# [Sample Parameter] block, which starts at bulk offset 112 of the sample dump (Table 1, page 38).


def _sample_rows():
    S = lambda key, name, p2, lo, hi, off, p3=0, p4=None, **kw: _int(  # noqa: E731
        "sample", key, name, _pad6((2, p2, p3) if p4 is None else (2, p2, p3, p4)), lo, hi, off, **kw
    )
    rows = [
        S("receive_channel", "MIDI receive channel", 3, 0, 32, 42),
        S("pitch_bend_type", "pitch bend type", 4, 0, 13, 43),
        S("pitch_bend_range", "pitch bend range", 5, 0, 24, 44),
        S("original_key_l", "original key L", 6, 0, 127, 46),
        S("original_key_r", "original key R", 6, 0, 127, 47, p3=1),
        S("sampling_frequency_l", "sampling frequency L (Hz)", 7, 1, 65535, 48, size=2),
        S("sampling_frequency_r", "sampling frequency R (Hz)", 7, 1, 65535, 50, p3=1, size=2),
        S("fine_tune_l", "fine tune L", 8, -63, 63, 52),
        S("fine_tune_r", "fine tune R", 8, -63, 63, 53, p3=1),
        S("coarse_tune", "coarse tune", 9, -127, 127, 45),
        S("key_range_high", "key range high (128 = original)", 10, 0, 128, 58, signed=False),
        S("key_range_low", "key range low (-1 = original)", 11, -1, 127, 59),
        S("loop_mode", "loop mode", 12, 0, 5, 61),
        S("wave_start_address", "wave start address (L)", 13, 0, 16777215, 64, size=4),
        S("wave_length", "wave length (end - start + 1) (L)", 14, 0, 16777215, 72, size=4),
        S("wave_end_address", "wave end address", 15, 0, 16777215, 180, size=4),
        S("loop_start_address", "loop start address (L)", 16, 0, 16777215, 80, size=4),
        S("loop_length", "loop length (end - start + 1) (L)", 17, 0, 16777215, 88, size=4),
        S("loop_end_address", "loop end address", 18, 0, 16777215, 184, size=4),
        S("start_address_velocity_sensitivity", "start address velocity sensitivity", 19, -63, 63, 96),
        S("loop_tempo", "loop tempo (x100, 80.00-159.99)", 20, 8000, 15999, 62, size=2),
        S("filter_type", "filter type", 21, 0, 16, 97, enum="filter_type"),
        S("filter_cutoff", "filter cutoff frequency", 22, 0, 127, 98),
        S("filter_q", "filter Q/width", 23, 0, 31, 99),
        S("cutoff_key_scaling_break_1", "cutoff key scaling break point 1", 24, 0, 127, 100),
        S("cutoff_key_scaling_break_2", "cutoff key scaling break point 2", 24, 0, 127, 101, p3=1),
        S("cutoff_key_scaling_level_1", "cutoff key scaling level 1", 25, -127, 127, 102),
        S("cutoff_key_scaling_level_2", "cutoff key scaling level 2", 25, -127, 127, 103, p3=1),
        S("cutoff_velocity_sensitivity", "cutoff velocity sensitivity (64-68 = Rnd1-5)", 26, -68, 68, 104),
        S("q_velocity_sensitivity", "Q/width velocity sensitivity (64-68 = Rnd1-5)", 27, -68, 68, 105),
        S("fixed_pitch_on", "fixed pitch on", 28, 0, 1, 41, bits=(4, 1)),
        S("detune", "detune", 29, -7, 7, 106),
        S("dephase", "dephase", 30, -63, 63, 107),
        S("expand_width", "expand width", 31, -63, 63, 108),
        S("random_pitch", "random pitch", 32, 0, 63, 109),
        S("sample_level", "sample level", 33, 0, 127, 110),
        S("pan", "pan (-64 = random)", 34, -64, 63, 111),
        S("velocity_low_limit", "velocity low limit", 35, 0, 127, 112),
        S("velocity_offset", "velocity offset", 36, -127, 127, 113),
        S("velocity_range_high", "velocity range high", 37, 0, 127, 114),
        S("velocity_range_low", "velocity range low", 38, 0, 127, 115),
        S("level_key_scaling_break_1", "level key scaling break point 1", 39, 0, 127, 116),
        S("level_key_scaling_break_2", "level key scaling break point 2", 39, 0, 127, 117, p3=1),
        S("level_key_scaling_level_1", "level key scaling level 1", 40, 0, 127, 118),
        S("level_key_scaling_level_2", "level key scaling level 2", 40, 0, 127, 119, p3=1),
        S("velocity_sensitivity", "velocity sensitivity", 41, -127, 127, 120),
        S("portamento_type", "sample portamento type", 42, 0, 5, 218),
        S("mono_mode", "mono mode", 43, 0, 1, 41, bits=(1, 1)),
        S("key_xfade_on", "key x-fade on", 44, 0, 1, 41, bits=(2, 1)),
        S("velocity_xfade_low", "velocity x-fade low", 46, 0, 127, 213),
        S("velocity_xfade_high", "velocity x-fade high", 47, 0, 127, 212),
        S("alternate_group", "alternate group number", 48, 0, 16, 121),
        S("eq_frequency", "EQ frequency", 49, 4, 58, 122),
        S("eq_gain", "EQ gain", 50, 52, 76, 123, signed=False),
        S("eq_width", "EQ width", 51, 10, 120, 124),
        S("cutoff_distance", "cutoff distance", 52, -63, 63, 125),
        S("feg_attack_rate", "FEG attack rate", 53, 0, 127, 126),
        S("feg_decay_rate", "FEG decay rate", 53, 0, 127, 127, p3=1),
        S("feg_release_rate", "FEG release rate", 53, 0, 127, 128, p3=2),
        S("feg_init_level", "FEG init level", 54, -127, 127, 129),
        S("feg_attack_level", "FEG attack level", 54, -127, 127, 130, p3=1),
        S("feg_sustain_level", "FEG sustain level", 54, -127, 127, 131, p3=2),
        S("feg_release_level", "FEG release level", 54, -127, 127, 132, p3=3),
        S("feg_rate_key_scaling", "FEG rate key scaling", 55, -7, 7, 133),
        S("feg_rate_velocity_sensitivity", "FEG rate velocity sensitivity", 56, -63, 63, 134),
        S("feg_attack_level_velocity_sensitivity", "FEG attack level velocity sensitivity", 57, -63, 63, 135),
        S("feg_level_velocity_sensitivity", "FEG level velocity sensitivity", 58, -63, 63, 136),
        S("peg_attack_rate", "PEG attack rate", 59, 0, 127, 137),
        S("peg_decay_rate", "PEG decay rate", 59, 0, 127, 138, p3=1),
        S("peg_release_rate", "PEG release rate", 59, 0, 127, 139, p3=2),
        S("peg_init_level", "PEG init level", 60, -127, 127, 140),
        S("peg_attack_level", "PEG attack level", 60, -127, 127, 141, p3=1),
        S("peg_sustain_level", "PEG sustain level", 60, -127, 127, 142, p3=2),
        S("peg_release_level", "PEG release level", 60, -127, 127, 143, p3=3),
        S("peg_rate_key_scaling", "PEG rate key scaling", 61, -7, 7, 144),
        S("peg_rate_velocity_sensitivity", "PEG rate velocity sensitivity", 62, -63, 63, 145),
        S("peg_level_velocity_sensitivity", "PEG level velocity sensitivity", 63, -63, 63, 146),
        S("peg_range", "PEG range", 64, -63, 63, 147),
        S("aeg_attack_rate", "AEG attack rate", 65, 0, 127, 148),
        S("aeg_decay_rate", "AEG decay rate", 65, 0, 127, 149, p3=1),
        S("aeg_release_rate", "AEG release rate", 65, 0, 127, 150, p3=2),
        # MEASURED (2026-10-06): (2,66,n) is a 4-byte array at +151..+154 - the manual's Table 2 says "P3 0-1"
        # for the sustain level, but the real sustain level is P3=2 (+153); P3=0/1 and 3 are reserved bytes
        S("aeg_sustain_level", "AEG sustain level", 66, 0, 127, 153, p3=2),
        S("aeg_rate_key_scaling", "AEG rate key scaling", 67, -7, 7, 156),
        S("aeg_rate_velocity_sensitivity", "AEG rate velocity sensitivity", 68, -63, 63, 157),
        S("aeg_attack_mode", "AEG attack mode", 69, 0, 2, 155),
        S("lfo_wave", "LFO wave", 70, 0, 3, 158),
        S("lfo_speed", "LFO speed", 71, 0, 127, 159),
        S("lfo_delay_time", "LFO delay time", 72, 0, 127, 160),
        S("lfo_sync_on", "LFO sync on", 73, 0, 1, 161, bits=(0, 1)),
        S("lfo_pitch_mod_phase_invert", "LFO pitch mod phase invert on", 74, 0, 1, 161, bits=(2, 1)),
        S("lfo_cutoff_mod_phase_invert", "LFO cutoff mod phase invert on", 75, 0, 1, 161, bits=(1, 1)),
        S("cutoff_mod_depth", "cutoff mod depth", 76, 0, 127, 162),
        S("pitch_mod_depth", "pitch mod depth", 77, 0, 127, 163),
        S("amplitude_mod_depth", "amplitude mod depth", 78, 0, 127, 164),
        S("output1", "output1", 79, 0, 12, 214, enum="output1"),
        S("output1_level", "output1 level", 80, 0, 127, 215),
        S("output2", "output2", 81, 0, 12, 216, enum="output2"),
        S("output2_level", "output2 level", 82, 0, 127, 217),
        S("filter_gain", "filter gain", 84, -31, 31, 169),
        S("eq_type", "EQ type", 85, 0, 2, 41, bits=(6, 2)),
        S("portamento_rate", "sample portamento rate", 87, 1, 127, 219),
        S("portamento_time", "sample portamento time", 88, 1, 127, 220),
    ]
    # sample controls 1-6: P = (2, 83, n, field); [Control] block at +188, 4 bytes each
    for n in range(6):
        base = 188 + 4 * n
        rows += [
            S(f"control{n + 1}_device", f"control device {n + 1}", 83, 0, 126, base, p3=n, p4=0),
            S(f"control{n + 1}_function", f"control function {n + 1}", 83, 0, 36, base + 1, p3=n, p4=1),
            S(f"control{n + 1}_type", f"control type {n + 1}", 83, 0, 3, base + 2, p3=n, p4=2),
            S(f"control{n + 1}_range", f"control range {n + 1}", 83, -63, 63, base + 3, p3=n, p4=3),
        ]
    return rows


PARAMS = {
    "program": _program_rows(),
    "easy_edit": _easy_edit_rows(),
    "sample": _sample_rows(),
}
_BY_KEY = {(scope, p.key): p for scope, rows in PARAMS.items() for p in rows}

# -- enumerations (manual pp.41-42) ---------------------------------------------------------------------

FILTER_TYPES = (
    "Bypass", "LowPass1", "LowPass2", "HiPass1", "HiPass2", "BandPass", "BandElim", "LowPass3",
    "Peak1", "Peak2", "2Peaks", "2Dips", "DualLPFs", "LPF+Peak", "DualHPFs", "HPF+Peak", "LPF+HP",
)
#: value -> name; Easy Edit adds -1 = "=sample"; 10-12 are A5000-only
OUTPUT1 = {0: "off", 1: "stereo out", 2: "effect1", 3: "effect2", 4: "effect3", 5: "assignL&R", 6: "assign1&2",
           7: "assign3&4", 8: "assign5&6", 9: "DIG&OPT", 10: "effect4", 11: "effect5", 12: "effect6"}
OUTPUT2 = {0: "off", 1: "assignL&R", 2: "assign1&2", 3: "assign3&4", 4: "assign5&6", 5: "DIG&OPT",
           6: "stereo out", 7: "effect1", 8: "effect2", 9: "effect3", 10: "effect4", 11: "effect5", 12: "effect6"}
ENUMS = {
    "filter_type": dict(enumerate(FILTER_TYPES)),
    "output1": OUTPUT1,
    "output2": OUTPUT2,
}


# -- access ------------------------------------------------------------------------------------------


def get(scope, key):
    """The row for (scope, key); KeyError if there is none."""
    return _BY_KEY[(scope, key)]


def rows(scope):
    return list(PARAMS[scope])


def request_params(param, slot=None):
    """P1..P6 to put in a parameter request/change for `param`. Easy Edit rows need the sample `slot`
    (0 = first assigned); P2*100+P3 carries it. A `bulk_only` row has no P-number and raises."""
    if param.bulk_only:
        raise ValueError(f"{param.key} has no parameter number (bulk dump only)")
    if param.scope == "easy_edit":
        if slot is None or not 0 <= slot <= 999:
            raise ValueError("an Easy Edit parameter needs a slot 0-999")
        p = list(param.p)
        p[1], p[2] = slot // 100, slot % 100
        return tuple(p)
    return param.p


def bulk_offset(param, slot=None):
    """Absolute byte offset of `param` in its object's bulk payload (`BulkDump.data`)."""
    if param.scope == "program":
        return param.offset
    if param.scope == "easy_edit":
        if slot is None or slot < 0:
            raise ValueError("an Easy Edit parameter needs a slot")
        return PROGRAM_EASY_EDIT_BASE + EASY_EDIT_BLOCK_SIZE * slot + param.offset
    return SAMPLE_PARAMETER_BASE + param.offset


def _from_bits(value, width, signed):
    if signed and value >= 1 << (width - 1):
        value -= 1 << width
    return value


def extract(param, data, slot=None):
    """The value of `param` read out of a bulk payload (`BulkDump.data`): an int, or text for text rows."""
    offset = bulk_offset(param, slot)
    if offset + param.bulk_size > len(data):
        raise ValueError(f"{param.key}: offset {offset} is past the end of the {len(data)}-byte payload")
    if param.kind == "text":
        return bytes(data[offset : offset + param.size]).decode("ascii", "replace").rstrip(" \x00")
    if param.bits:
        shift, width = param.bits
        raw = (data[offset] >> shift) & ((1 << width) - 1)
        return _from_bits(raw, width, param.signed)
    return int.from_bytes(bytes(data[offset : offset + param.size]), "big", signed=param.signed)


def decode_reply(param, reply_data):
    """A parameter reply's un-nibbled value bytes (`ParameterMessage.data`) -> int / text."""
    if param.kind == "text":
        return bytes(reply_data).decode("ascii", "replace").rstrip(" \x00")
    return int.from_bytes(bytes(reply_data), "big", signed=param.signed)


def in_range(param, value):
    """True if `value` is within the manual's range for `param` (always False for read-only rows)."""
    return not param.read_only and param.kind == "int" and param.lo <= value <= param.hi
