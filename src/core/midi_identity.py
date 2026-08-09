from core.manufacturer_id_list import MANUFACTURERS


def build_identity_request_message():
    # send identity request string
    # should probably add ability to adjust SysEx channel (currently hard-coded to 0x7F aka, all channels)
    return [0x7E, 0x7F, 0x06, 0x01]


def parse_identity_response(data_bytes):
    # decodes identity response from hardware
    # example responses:
    # 0x7E, 0x00, 0x06, 0x02, 0x47, 0x01, 0x02, 0x03, 0x04, 0x01, 0x00, 0x00, 0x00
    # 0x7E, 0x00, 0x06, 0x02, 0x00, 0x01, 0x02, 0x01, 0x02, 0x03, 0x04, 0x01, 0x00, 0x00, 0x00
    if data_bytes[4] == 0x00:
        offset = 2
        manuf_id = (
            (data_bytes[4] & 0x7F),
            (data_bytes[5] & 0x7F),
            (data_bytes[6] & 0x7F),
        )
    else:
        offset = 0
        manuf_id = ((data_bytes[4] & 0x7F),)

    family_code = (data_bytes[offset + 5] & 0x7F) | (
        (data_bytes[offset + 6] & 0x7F) << 7
    )
    model_number = (data_bytes[offset + 7] & 0x7F) | (
        (data_bytes[offset + 8] & 0x7F) << 7
    )
    version_number = (
        (data_bytes[offset + 9] & 0x7F)
        | ((data_bytes[offset + 10] & 0x7F) << 7)
        | ((data_bytes[offset + 11] & 0x7F) << 14)
        | ((data_bytes[offset + 12] & 0x7F) << 21)
    )
    return {
        "manuf_id": manuf_id,
        "family_code": family_code,
        "model_number": model_number,
        "version_number": version_number,
    }


def lookup_manufacturer(manuf_id):
    # look up saved manufacturers dict with the manuf_id response
    return MANUFACTURERS.get(manuf_id, "Unknown manufacturer")


def build_rstat_request_message():
    # send Akai specific identity request string because it doesnt understand regular ID requests
    # (yeah i know, akai's gotta be special again instead of implementing a generic sysex function...)
    return [0x47, 0x00, 0x00, 0x48]


def parse_stat_response(data_bytes):
    # decodes the akai specific STAT message
    # example response:
    # 0x47, 0x00, 0x01, 0x48, 0x00, 0x11, 0x6E, 0x07, 0x68, 0x07, 0x00, 0x00, 0x40, 0x02, 0x00, 0x76, 0x3B, 0x02, 0x00
    version_minor = data_bytes[4] & 0x7F
    version_major = (data_bytes[5] & 0x7F) - 15
    # honestly this is kinda just an educated guess for the version number format
    # i only was able to test OS versions 1.30 and 2.00 on an S2000.
    # no idea if this pattern holds true for other samplers, so further testing will be required
    version_string = f"{version_major}.{version_minor:02d}"

    max_num_blocks = (data_bytes[6] & 0x7F) | ((data_bytes[7] & 0x7F) << 7)
    num_blocks_free = (data_bytes[8] & 0x7F) | ((data_bytes[9] & 0x7F) << 7)
    max_num_samp_words = (
        (data_bytes[10] & 0x7F)
        | ((data_bytes[11] & 0x7F) << 7)
        | ((data_bytes[12] & 0x7F) << 14)
        | ((data_bytes[13] & 0x7F) << 21)
    )
    num_words_free = (
        (data_bytes[14] & 0x7F)
        | ((data_bytes[15] & 0x7F) << 7)
        | ((data_bytes[16] & 0x7F) << 14)
        | ((data_bytes[17] & 0x7F) << 21)
    )
    curr_ex_chan_setting = data_bytes[18] & 0x7F

    return {
        "version_string": version_string,
        "max_num_blocks": max_num_blocks,
        "num_blocks_free": num_blocks_free,
        "max_num_samp_words": max_num_samp_words,
        "num_words_free": num_words_free,
        "curr_ex_chan_setting": curr_ex_chan_setting,
    }
