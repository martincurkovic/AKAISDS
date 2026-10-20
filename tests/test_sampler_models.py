# tests for core/sampler_models.py - the seven Settings "Sampler Type"
# choices and what each implies

from core import sampler_models


def test_choices_are_alphabetical_by_label():
    labels = [label for label, _selection in sampler_models.CHOICES]
    assert labels == [
        "Akai S1000",
        "Akai S2000/S3000",
        "Akai S900/S950 (experimental)",
        "Ensoniq ASR-X (untested, experimental)",
        "Ensoniq EPS/ASR (untested, experimental)",
        "Generic SDS",
        "Yamaha A4000/A5000 (experimental)",
    ]
    assert labels == sorted(labels)


def test_s2000_s3000_has_its_own_explicit_key():
    assert sampler_models.AKAI_S2000_S3000 == "akai_s2000_s3000"
    assert not sampler_models.is_s1000("akai_s2000_s3000")


def test_legacy_akai_key_is_normalised_to_s2000_s3000():
    # a config.json saved before the S1000 existed stored plain "akai" for
    # what is now the S2000/S3000 entry - it must keep meaning that
    assert sampler_models.LEGACY_AKAI == "akai"
    assert sampler_models.normalize("akai") == "akai_s2000_s3000"
    assert not sampler_models.is_s1000("akai")
    assert sampler_models.protocol_family("akai") == "akai"


def test_protocol_family_collapses_both_akai_models():
    assert sampler_models.protocol_family("akai") == "akai"
    assert sampler_models.protocol_family("akai_s1000") == "akai"
    assert sampler_models.protocol_family("generic") == "generic"


def test_s900_s950_is_its_own_protocol_family():
    # NOT "akai": the S900/S950 answer none of the S1000-family SysEx, so
    # nothing keyed on the akai/generic families may ever apply to it
    assert sampler_models.AKAI_S900_S950 == "akai_s900_s950"
    assert sampler_models.protocol_family("akai_s900_s950") == "s950"
    assert sampler_models.protocol_family("akai_s900_s950") not in (
        sampler_models.FAMILY_AKAI,
        sampler_models.FAMILY_GENERIC,
    )
    assert sampler_models.is_s950("akai_s900_s950")
    assert not sampler_models.is_s1000("akai_s900_s950")


def test_only_the_s900_s950_is_s950():
    for selection in ("akai", "akai_s1000", "akai_s2000_s3000", "generic", None):
        assert not sampler_models.is_s950(selection)


def test_s900_s950_selection_survives_normalize():
    assert sampler_models.normalize("akai_s900_s950") == "akai_s900_s950"


def test_only_s1000_is_s1000():
    assert sampler_models.is_s1000("akai_s1000")
    assert not sampler_models.is_s1000("generic")
    assert not sampler_models.is_s1000(None)


def test_unrecognised_selection_falls_back_to_s2000_s3000():
    assert (
        sampler_models.normalize("something-from-a-newer-version")
        == "akai_s2000_s3000"
    )
    assert sampler_models.normalize(None) == "akai_s2000_s3000"
    assert sampler_models.protocol_family(None) == "akai"


def test_yamaha_transfers_are_plain_sds_but_it_is_its_own_model():
    # the A4000's sample transfers are standard SDS (the generic path), so the PROTOCOL FAMILY is "generic"...
    assert sampler_models.YAMAHA_A4000 == "yamaha_a4000"
    assert sampler_models.protocol_family("yamaha_a4000") == "generic"
    # ...but the full model is how the Dashboard knows to offer the Yamaha editor
    assert sampler_models.is_yamaha("yamaha_a4000")
    assert not sampler_models.is_yamaha("generic")
    assert not sampler_models.is_s950("yamaha_a4000") and not sampler_models.is_s1000("yamaha_a4000")
    assert sampler_models.normalize("yamaha_a4000") == "yamaha_a4000"


def test_ensoniq_is_its_own_protocol_family_and_says_it_is_untested():
    # NOT "akai" or "generic": an Ensoniq answers neither, so nothing keyed on those families may apply to it
    assert sampler_models.ENSONIQ_EPS_ASR == "ensoniq_eps_asr"
    assert sampler_models.protocol_family("ensoniq_eps_asr") == "ensoniq"
    assert sampler_models.protocol_family("ensoniq_eps_asr") not in (
        sampler_models.FAMILY_AKAI,
        sampler_models.FAMILY_GENERIC,
        sampler_models.FAMILY_S950,
    )
    assert sampler_models.is_ensoniq("ensoniq_eps_asr")
    assert sampler_models.normalize("ensoniq_eps_asr") == "ensoniq_eps_asr"
    label = dict((sel, lbl) for lbl, sel in sampler_models.CHOICES)["ensoniq_eps_asr"]
    assert "untested" in label and "experimental" in label


def test_the_asrx_is_the_ensoniq_family_with_its_own_dialect():
    # the same protocol family as the EPS/ASR entry (the Dashboard branches are shared), told apart by the model
    assert sampler_models.ENSONIQ_ASRX == "ensoniq_asrx"
    assert sampler_models.protocol_family("ensoniq_asrx") == sampler_models.FAMILY_ENSONIQ
    assert sampler_models.is_ensoniq("ensoniq_asrx") and sampler_models.is_ensoniq("ensoniq_eps_asr")
    assert sampler_models.is_asrx("ensoniq_asrx") and not sampler_models.is_asrx("ensoniq_eps_asr")
    assert sampler_models.normalize("ensoniq_asrx") == "ensoniq_asrx"
    label = dict((sel, lbl) for lbl, sel in sampler_models.CHOICES)["ensoniq_asrx"]
    assert "untested" in label and "experimental" in label
    assert sampler_models.ensoniq_display_name("ensoniq_asrx") == "Ensoniq ASR-X"
    assert sampler_models.ensoniq_display_name("ensoniq_eps_asr") == "Ensoniq EPS/ASR"


def test_only_the_ensoniq_selection_is_ensoniq():
    for selection in ("akai", "akai_s1000", "akai_s900_s950", "generic", "yamaha_a4000", None):
        assert not sampler_models.is_ensoniq(selection)
