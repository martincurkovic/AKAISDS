# The three "Sampler Type" choices in Settings, and what each one implies.
#
# A selection is one of three strings (what MidiSettingsDialog's combo stores
# as item data, and what app_config persists as "device_type"):
#
#   "akai_s2000_s3000" - Akai S2000/S3000 series
#   "akai_s1000"       - Akai S1000 (and S1100)
#   "generic"          - any MIDI Sample Dump Standard device
#
# Before the S1000 existed the S2000/S3000 entry was saved as plain "akai".
# That legacy value is still ACCEPTED everywhere (normalize() maps it), and
# app_config.ensure_defaults_saved() rewrites it in config.json at startup so
# the file names the actual hardware - see LEGACY_AKAI.
#
# Everything transfer-related (SamplerController) only cares about the
# PROTOCOL FAMILY - "akai" vs "generic" - since an S1000's SDS/RSTAT/list
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
GENERIC_SDS = "generic"

FAMILY_AKAI = "akai"
FAMILY_GENERIC = "generic"

#: (label, selection) in the order the Settings combo shows them -
#: alphabetical by label
CHOICES = [
    ("Akai S1000", AKAI_S1000),
    ("Akai S2000/S3000", AKAI_S2000_S3000),
    ("Generic SDS", GENERIC_SDS),
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
    # "akai" or "generic" - what SamplerController.device_type holds
    return FAMILY_GENERIC if normalize(selection) == GENERIC_SDS else FAMILY_AKAI


def is_s1000(selection):
    return normalize(selection) == AKAI_S1000
