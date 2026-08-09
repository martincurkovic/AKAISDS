# tests for core/midi_identity.py
# tests both universal midi identity request and reply mechanism
# and the akai proprietary one (akai samplers dont respond to universal requests at all...)
# the STAT tests use actual data captured from an S2000 running OS 1.30 and 2.00
# No other Akai samplers have been tested and i'm low key just guessing that the OS version
# numbering pattern is the same, so will need to test it on other hardware, but i dont have anything
# else other than an S2000 and a Yamaha A4000 (which i used to test the universal midi identity request)

from core import midi_identity


def test_build_identity_request_message():
    assert midi_identity.build_identity_request_message() == [0x7E, 0x7F, 0x06, 0x01]


def test_parse_identity_response_simple_1byte_manufacturer():
    # test manufacturer ID code
    data = [
        0x7E,
        0x00,
        0x06,
        0x02,
        0x47,
        0x01,
        0x02,
        0x03,
        0x04,
        0x01,
        0x00,
        0x00,
        0x00,
    ]
    result = midi_identity.parse_identity_response(data)
    assert result["manuf_id"] == (0x47,)
    assert result["family_code"] == 257
    assert result["model_number"] == 515
    assert result["version_number"] == 1


def test_parse_identity_response_extended_3byte_manufacturer():
    # tesst longer manufacturer codes
    data = [
        0x7E,
        0x00,
        0x06,
        0x02,
        0x00,
        0x01,
        0x02,
        0x01,
        0x02,
        0x03,
        0x04,
        0x01,
        0x00,
        0x00,
        0x00,
    ]
    result = midi_identity.parse_identity_response(data)
    assert result["manuf_id"] == (0x00, 0x01, 0x02)
    assert result["family_code"] == 257
    assert result["model_number"] == 515
    assert result["version_number"] == 1


def test_manuf_id_1byte_and_3byte_forms_never_collide():
    # i love unnecessarily long function names
    # just checking for collisions between manufacturer codes
    # eg, Shure = 0x00, 0x01, 0x00 and Sequential Circuits = 0x01
    # numerically theyre the same value but DIFFERENT manufacturers
    # this is also why the manuf_id is a tuple, so the length can distiinguish the 2 forms
    shure_style = (0x00, 0x01, 0x00)
    sequential_circuits_style = (0x01,)
    assert shure_style != sequential_circuits_style


def test_lookup_manufacturer_known_id():
    assert midi_identity.lookup_manufacturer((0x47,)) == "Akai Electric Co. Ltd."


def test_lookup_manufacturer_unknown_id_has_safe_fallback():
    # fallback and dont make shit up lol
    result = midi_identity.lookup_manufacturer((0x7E,))
    assert result == "Unknown manufacturer"


def test_build_rstat_request_message():
    assert midi_identity.build_rstat_request_message() == [0x47, 0x00, 0x00, 0x48]


def test_parse_stat_response_real_hardware_os_200():
    # captured directly from a real Akai S2000 running OS 2.00
    data = [
        0x47,
        0x00,
        0x01,
        0x48,
        0x00,
        0x11,
        0x6E,
        0x07,
        0x68,
        0x07,
        0x00,
        0x00,
        0x40,
        0x02,
        0x00,
        0x76,
        0x3B,
        0x02,
        0x00,
    ]
    result = midi_identity.parse_stat_response(data)
    assert result["version_string"] == "2.00"
    assert result["max_num_blocks"] == 1006
    assert result["num_blocks_free"] == 1000
    assert result["max_num_samp_words"] == 5242880
    assert result["num_words_free"] == 5176064
    assert result["curr_ex_chan_setting"] == 0


def test_parse_stat_response_real_hardware_os_130():
    # captured from the SAME S2000 unit booted from a different OS
    # floppy (1.30) - only the version bytes should differ from the
    # 2.00 capture above, everything else (memory/channel) should match
    # exactly, since it's the same physical unit, riiiight???
    data = [
        0x47,
        0x00,
        0x01,
        0x48,
        0x1E,
        0x10,
        0x6E,
        0x07,
        0x68,
        0x07,
        0x00,
        0x00,
        0x40,
        0x02,
        0x00,
        0x76,
        0x3B,
        0x02,
        0x00,
    ]
    result = midi_identity.parse_stat_response(data)
    assert result["version_string"] == "1.30"
    assert result["max_num_blocks"] == 1006
    assert result["num_blocks_free"] == 1000
    assert result["max_num_samp_words"] == 5242880
    assert result["num_words_free"] == 5176064
