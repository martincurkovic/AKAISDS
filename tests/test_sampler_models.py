# tests for core/sampler_models.py - the three Settings "Sampler Type"
# choices and what each implies

from core import sampler_models


def test_choices_are_alphabetical_by_label():
    labels = [label for label, _selection in sampler_models.CHOICES]
    assert labels == ["Akai S1000", "Akai S2000/S3000", "Generic SDS"]
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
