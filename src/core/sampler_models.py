# The five "Sampler Type" choices in Settings, and what each one implies.
#
# A selection is one of five strings (what MidiSettingsDialog's combo stores
# as item data, and what app_config persists as "device_type"):
#
#   "akai_s2000_s3000" - Akai S2000/S3000 series
#   "akai_s1000"       - Akai S1000 (and S1100)
#   "akai_s900_s950"   - Akai S900/S950 (a different protocol: see core/s950_sysex.py)
#   "generic"          - any MIDI Sample Dump Standard device
#   "yamaha_a4000"     - Yamaha A4000/A5000: SENDING samples is plain Sample Dump Standard (the "generic"
#                        family), but listing and receiving samples and the Program Editor speak Yamaha SysEx
#                        (core/yamaha_sysex.py, controller/yamaha_transfers.py)
#
# Before the S1000 existed the S2000/S3000 entry was saved as plain "akai".
# That legacy value is still ACCEPTED everywhere (normalize() maps it), and
# app_config.ensure_defaults_saved() rewrites it in config.json at startup so
# the file names the actual hardware - see LEGACY_AKAI.
#
# Everything transfer-related (SamplerController) only cares about the
# PROTOCOL FAMILY - "akai", "generic" or "s950" - since an S1000's SDS/RSTAT/list
# traffic is the same as the S2000/S3000's (the Dashboard already worked
# against a real S1000 before this existed). Only the Program Editor cares
# about the S1000 vs S2000/S3000 split, because the two answer completely
# different SysEx for editing parameters - see core/s1000_bridge.py. That's
# why a model can't be told from MIDI identity either: all three Akai
# families answer an identity/status request identically.

AKAI_S2000_S3000 = "akai_s2000_s3000"
#: what config.json held for the S2000/S3000 before the S1000 was added
LEGACY_AKAI = "akai"
AKAI_S1000 = "akai_s1000"
AKAI_S900_S950 = "akai_s900_s950"
GENERIC_SDS = "generic"
YAMAHA_A4000 = "yamaha_a4000"

FAMILY_AKAI = "akai"
FAMILY_GENERIC = "generic"
#: the S900/S950 speak neither of the above (device byte 0x40, other function
#: codes, one-SysEx sample dumps) - nothing may fall through to the "akai" or
#: "generic" branches for it
FAMILY_S950 = "s950"

#: (label, selection) in the order the Settings combo shows them -
#: alphabetical by label
CHOICES = [
    ("Akai S1000", AKAI_S1000),
    ("Akai S2000/S3000", AKAI_S2000_S3000),
    ("Akai S900/S950 (experimental)", AKAI_S900_S950),
    ("Generic SDS", GENERIC_SDS),
    ("Yamaha A4000/A5000 (experimental)", YAMAHA_A4000),
]

_VALID = {selection for _label, selection in CHOICES}


def normalize(selection):
    # anything unrecognised (a hand-edited or newer config.json) falls back
    # to the long-standing default rather than crashing or leaving the app
    # in a state none of the checks below understand
    if selection == LEGACY_AKAI:
        return AKAI_S2000_S3000
    return selection if selection in _VALID else AKAI_S2000_S3000


def protocol_family(selection):
    # "akai", "generic" or "s950" - what SamplerController.device_type holds
    selection = normalize(selection)
    # the Yamaha's sample SENDS are standard SDS; its list, receives and editor are Yamaha-specific (is_yamaha)
    if selection in (GENERIC_SDS, YAMAHA_A4000):
        return FAMILY_GENERIC
    if selection == AKAI_S900_S950:
        return FAMILY_S950
    return FAMILY_AKAI


def is_s1000(selection):
    return normalize(selection) == AKAI_S1000


def is_s950(selection):
    return normalize(selection) == AKAI_S900_S950


def is_yamaha(selection):
    return normalize(selection) == YAMAHA_A4000
