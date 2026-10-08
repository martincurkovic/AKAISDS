"""Tooltip wording for the Yamaha A4000/A5000 editor's controls, built from the parameter table.

Two tiers, so every one of the 204 rows has a readable tooltip and the controls people use most have a real explanation:

1. AUTOMATIC: `humanise` turns the table's manual-style name ("AEG attack rate", "program LFO step wave value3", "pan (-64 = random)") into
   sentence case with the abbreviations spelled out, and the range line carries any special values. Nothing to maintain per row.
2. WRITTEN: `DESCRIPTIONS` (one short sentence for a control, keyed by (scope, key)) and the rules in `_describe` (an envelope's rates and
   levels, an Easy Edit "_offset") add a second line. Keep them SHORT - a tooltip is a hint, not a manual - and say only what is known:
   the envelope rates are rates (higher is faster); anything not understood is left to tier 1 rather than guessed.

Result, plain text (the app's other tooltips are plain too):

    Attack rate (amplitude envelope)
    How fast the level rises after note-on. Higher is faster.
    Range: 0 to 127
"""

import re

_ENVELOPES = {"aeg": "amplitude envelope", "feg": "filter envelope", "peg": "pitch envelope"}
_ENV_ABBREVIATIONS = {"AEG": "amplitude envelope", "FEG": "filter envelope", "PEG": "pitch envelope"}

#: (scope, key) -> one short sentence. Scope is "program" (the program page), "easy_edit" (an assigned sample's page) or "sample".
DESCRIPTIONS = {
    # -- program page -----------------------------------------------------------------------------------------------
    ("program", "ad_in_on"): "Plays the audio input through this program.",
    ("program", "ad_in_source"): "Which input to use: L/R as stereo, L+R mixed to mono, or two independent mono inputs.",
    ("program", "ad_in_l_pan"): "Pan position of the left audio input.",
    ("program", "ad_in_r_pan"): "Pan position of the right audio input.",
    ("program", "ad_in_l_output1"): "Where the left audio input is sent (first destination).",
    ("program", "ad_in_l_output2"): "Where the left audio input is sent (second destination).",
    ("program", "ad_in_r_output1"): "Where the right audio input is sent (first destination).",
    ("program", "ad_in_r_output2"): "Where the right audio input is sent (second destination).",
    ("program", "ad_in_l_output1_level"): "Level of the left audio input sent to its first destination.",
    ("program", "ad_in_l_output2_level"): "Level of the left audio input sent to its second destination.",
    ("program", "ad_in_r_output1_level"): "Level of the right audio input sent to its first destination.",
    ("program", "ad_in_r_output2_level"): "Level of the right audio input sent to its second destination.",
    ("program", "program_level"): "Overall level of the program.",
    ("program", "transpose"): "Shifts the pitch of every sample in the program.",
    ("program", "lfo_cycle"): "Length of one LFO cycle, relative to the LFO tempo.",
    ("program", "lfo_sync"): "Manual: the LFO runs at the Tempo setting. MIDI: it follows incoming MIDI.",
    ("program", "lfo_tempo"): "Tempo the LFO cycle is based on.",
    ("program", "lfo_wave"): "Shape of the program LFO.",
    ("program", "lfo_initial_phase"): "Point in its cycle where the LFO starts.",
    ("program", "lfo_reset_midi_channel"): "MIDI channel whose notes restart the LFO. Off: never. Audition: the panel's Audition button.",
    ("program", "lfo_reset_note"): "Note that restarts the LFO. -1: any note.",
    ("program", "lfo_step_total"): "Number of steps in the step wave.",
    ("program", "lfo_step_slope"): "Slope between steps: none, rising, falling, or both.",
    ("program", "portamento_type"): "Slide between notes at a fixed rate or in a fixed time. Fingered: only while the first key is held.",
    ("program", "portamento_rate"): "Slide speed (for the rate types).",
    ("program", "portamento_time"): "Slide time (for the time types).",
    ("program", "sh_speed"): "Speed of the sample & hold LFO.",
    # -- an assigned sample's page (Easy Edit) --------------------------------------------------------------------------
    ("easy_edit", "receive_channel"): "MIDI channel that plays this sample in this program. =Sample uses the sample's own.",
    ("easy_edit", "key_limit_high"): "Highest key that plays this sample in this program.",
    ("easy_edit", "key_limit_low"): "Lowest key that plays this sample in this program.",
    ("easy_edit", "key_range_shift"): "Moves this sample's key range up or down, for this program only.",
    ("easy_edit", "velocity_limit_high"): "Highest velocity that plays this sample in this program.",
    ("easy_edit", "velocity_limit_low"): "Lowest velocity that plays this sample in this program.",
    ("easy_edit", "portamento"): "Slide between notes. =Sample uses the sample's own setting; =Program the program's.",
    ("easy_edit", "mono_mode"): "Plays one note at a time. =Sample uses the sample's own setting.",
    ("easy_edit", "key_xfade_on"): "Crossfades at the edges of the key range. =Sample uses the sample's own setting.",
    ("easy_edit", "alternate_group"): "Samples in the same group cut each other off, like hi-hats. -1: the sample's own setting.",
    ("easy_edit", "output1"): "Where this sample is sent. =Sample uses the sample's own setting.",
    ("easy_edit", "output2"): "Where this sample is sent. =Sample uses the sample's own setting.",
    # -- a sample ---------------------------------------------------------------------------------------------------------
    ("sample", "receive_channel"): "MIDI channel that plays this sample. Basic channel follows the sampler's basic receive channel.",
    ("sample", "pitch_bend_type"): "How the pitch wheel bends this sample - a numbered type from the sampler's own list.",
    ("sample", "pitch_bend_range"): "How far the pitch wheel bends the pitch.",
    ("sample", "original_key_l"): "The key at which the sample plays at its recorded pitch.",
    ("sample", "fine_tune_l"): "Fine pitch adjustment.",
    ("sample", "coarse_tune"): "Coarse pitch adjustment.",
    ("sample", "key_range_high"): "Highest key that plays this sample.",
    ("sample", "key_range_low"): "Lowest key that plays this sample.",
    ("sample", "loop_tempo"): "Tempo of the loop, in BPM.",
    ("sample", "start_address_velocity_sensitivity"): "How much velocity moves the point playback starts from.",
    ("sample", "filter_cutoff"): "Cutoff frequency of the filter.",
    ("sample", "filter_q"): "Resonance (Q) or bandwidth of the filter, depending on its type.",
    ("sample", "filter_gain"): "Boost or cut of the filter, for the types that have one.",
    ("sample", "cutoff_distance"): "Distance between the two cutoffs of the dual filter types.",
    ("sample", "cutoff_velocity_sensitivity"): "How much velocity changes the cutoff.",
    ("sample", "q_velocity_sensitivity"): "How much velocity changes the Q.",
    ("sample", "cutoff_key_scaling_break_1"): "First key at which the cutoff scaling changes.",
    ("sample", "cutoff_key_scaling_break_2"): "Second key at which the cutoff scaling changes.",
    ("sample", "cutoff_key_scaling_level_1"): "Cutoff change at the first break point.",
    ("sample", "cutoff_key_scaling_level_2"): "Cutoff change at the second break point.",
    ("sample", "level_key_scaling_break_1"): "First key at which the level scaling changes.",
    ("sample", "level_key_scaling_break_2"): "Second key at which the level scaling changes.",
    ("sample", "level_key_scaling_level_1"): "Level at the first break point.",
    ("sample", "level_key_scaling_level_2"): "Level at the second break point.",
    ("sample", "fixed_pitch_on"): "Plays at one pitch whichever key is played.",
    ("sample", "detune"): "Small pitch offset.",
    ("sample", "random_pitch"): "Random pitch variation on each note.",
    ("sample", "sample_level"): "Level of the sample.",
    ("sample", "pan"): "Stereo position of the sample.",
    ("sample", "velocity_range_high"): "Highest velocity that plays this sample.",
    ("sample", "velocity_range_low"): "Lowest velocity that plays this sample.",
    ("sample", "velocity_sensitivity"): "How much velocity changes the level. Negative values invert it.",
    ("sample", "mono_mode"): "Plays one note at a time.",
    ("sample", "key_xfade_on"): "Crossfades at the edges of the key range.",
    ("sample", "alternate_group"): "Samples in the same group cut each other off, like hi-hats. 0: none.",
    ("sample", "lfo_speed"): "Speed of the sample's LFO.",
    ("sample", "lfo_delay_time"): "Time after note-on before the LFO takes effect.",
    ("sample", "lfo_pitch_mod_phase_invert"): "Inverts the LFO's effect on pitch.",
    ("sample", "lfo_cutoff_mod_phase_invert"): "Inverts the LFO's effect on the cutoff.",
    ("sample", "cutoff_mod_depth"): "How much the LFO modulates the cutoff.",
    ("sample", "pitch_mod_depth"): "How much the LFO modulates the pitch.",
    ("sample", "amplitude_mod_depth"): "How much the LFO modulates the level.",
    ("sample", "eq_type"): "Peak/dip boosts or cuts a band; the shelves boost or cut everything below or above the frequency.",
    ("sample", "eq_frequency"): "Frequency the sample EQ acts on.",
    ("sample", "eq_gain"): "Boost or cut of the sample EQ.",
    ("sample", "eq_width"): "Width of the band the sample EQ acts on.",
    ("sample", "output1"): "Where the sample is sent (first destination).",
    ("sample", "output2"): "Where the sample is sent (second destination).",
    ("sample", "output1_level"): "Level sent to the first destination.",
    ("sample", "output2_level"): "Level sent to the second destination.",
}

#: the envelope rows, by what follows the "aeg_" / "feg_" / "peg_" prefix ({env} = the envelope's name)
_ENVELOPE_PARTS = {
    "attack_rate": "How fast the {env} rises to its attack level. Higher is faster.",
    "decay_rate": "How fast the {env} falls to the sustain level. Higher is faster.",
    "release_rate": "How fast the {env} falls after the key is released. Higher is faster.",
    "init_level": "Level the {env} starts from.",
    "attack_level": "Level the {env} reaches at the end of its attack.",
    "sustain_level": "Level the {env} holds while the key is down.",
    "release_level": "Level the {env} reaches at the end of its release.",
    "rate_key_scaling": "How much the key played speeds up or slows down the {env}.",
    "rate_velocity_sensitivity": "How much velocity speeds up or slows down the {env}.",
    "attack_level_velocity_sensitivity": "How much velocity changes the {env}'s attack level.",
    "level_velocity_sensitivity": "How much velocity changes the {env}'s levels.",
    "range": "Overall depth of the {env}.",
}


def _describe(scope, key):
    """The second line for a row: a written sentence, or None."""
    text = DESCRIPTIONS.get((scope, key))
    if text:
        return text
    if scope == "sample":
        prefix, _, rest = key.partition("_")
        if prefix in _ENVELOPES and rest in _ENVELOPE_PARTS:
            return _ENVELOPE_PARTS[rest].format(env=_ENVELOPES[prefix])
        match = re.fullmatch(r"control(\d)_(device|function|type|range)", key)
        if match:
            n, part = match.groups()
            return {
                "device": f"MIDI controller that drives control {n}.",
                "function": f"What control {n} changes.",
                "type": f"How control {n}'s value is applied.",
                "range": f"How far control {n} moves its function.",
            }[part]
    if scope == "program":
        match = re.fullmatch(r"lfo_step_value_(\d+)", key)
        if match:
            return f"Level of step {match.group(1)} of the step wave."
    if scope == "easy_edit" and key.endswith("_offset"):
        return "Added to the sample's own value, for this program only."
    return None


def humanise(name):
    """A table name ("AEG attack rate", "output1 level", "key x-fade on (-1 =sample)") as a sentence-case title.

    Parenthesised special-value notes ("(-1 =sample)") are dropped here - `specials` puts them on the range line - as is "(read only)"."""
    text = re.sub(r"\s*\((?:read only)\)", "", name)
    text = re.sub(r"\s*\([^()]*=[^()]*\)", "", text)
    text = re.sub(r"\s*\(x100[^()]*\)", "", text)  # "(x100, 80.00-159.99)": the range line shows the real values
    text = text.replace("AD in", "audio input").replace("x-fade", "crossfade")
    for abbreviation, full in _ENV_ABBREVIATIONS.items():
        text = re.sub(rf"\b{abbreviation}\b", full, text)
    text = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", text)  # value3 -> value 3, output1 -> output 1
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1].upper() + text[1:]


def specials(name):
    """The special values a table name mentions ("-64 = random", "-1 = Sample"), or "" - for the range line."""
    notes = re.findall(r"\(([^()]*=[^()]*)\)", name)
    text = ", ".join(note.strip() for note in notes)
    return text.replace("=sample", "= Sample").replace("=program", "= Program").replace("Rnd1-5", "random 1-5")


def tooltip(row):
    """The tooltip of a `core.yamaha_params.Param`: a title, an optional sentence, and (unless it is a dropdown) the range."""
    lines = [humanise(row.name)]
    description = _describe(row.scope, row.key)
    if description:
        lines.append(description)
    if not row.enum:
        scaled = re.search(r"x100, ([\d.]+)-([\d.]+)", row.name)  # stored x100: show the value the panel shows
        line = f"Range: {scaled.group(1)} to {scaled.group(2)}" if scaled else f"Range: {row.lo} to {row.hi}"
        note = specials(row.name)
        lines.append(f"{line} ({note})" if note else line)
    return "\n".join(lines)
