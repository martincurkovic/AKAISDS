import math

AKAI_CHAR_MAP = {
    0x00: "0",
    0x01: "1",
    0x02: "2",
    0x03: "3",
    0x04: "4",
    0x05: "5",
    0x06: "6",
    0x07: "7",
    0x08: "8",
    0x09: "9",
    0x0A: " ",
    0x0B: "A",
    0x0C: "B",
    0x0D: "C",
    0x0E: "D",
    0x0F: "E",
    0x10: "F",
    0x11: "G",
    0x12: "H",
    0x13: "I",
    0x14: "J",
    0x15: "K",
    0x16: "L",
    0x17: "M",
    0x18: "N",
    0x19: "O",
    0x1A: "P",
    0x1B: "Q",
    0x1C: "R",
    0x1D: "S",
    0x1E: "T",
    0x1F: "U",
    0x20: "V",
    0x21: "W",
    0x22: "X",
    0x23: "Y",
    0x24: "Z",
    0x25: "#",
    0x26: "+",
    0x27: "-",
    0x28: ".",
}


def decode_name(name_bytes):
    name_ascii = []
    for i in name_bytes:
        name_ascii.append(AKAI_CHAR_MAP.get(i, "?"))
    name_ascii = "".join(name_ascii)
    return name_ascii


def parse_slist_response(data_bytes):
    lsb = data_bytes[4]
    msb = data_bytes[5]
    count = lsb | (msb << 7)

    sample_names = []
    for n in range(count):
        start = 6 + (n * 12)
        end = start + 12
        name_bytes = data_bytes[start:end]
        sample_names.append(decode_name(name_bytes))

    return count, sample_names


def build_slist_request():
    return [0x47, 0x00, 0x04, 0x48]


REVERSE_CHAR_MAP = {v: k for k, v in AKAI_CHAR_MAP.items()}


def encode_name(name, length=12):
    # turn plain text name into 12 char akai encoded name
    # padded with spaces to make up to 12 char length
    # unknown chars fall back to space instead of crashing
    # longer file names get chopped down to 12 chars
    name = name.upper()[:length].ljust(length)
    return [REVERSE_CHAR_MAP.get(ch, 0x0A) for ch in name]


def build_dels_request(sample_number, channel=0):
    # build DELS (Delete Sample Header and Data) request
    # F0, 47, cc, DELS(0x14), 48, ss, ss, F7
    # where ss, ss is sample number, LSB first 7 bit
    ss_lsb = sample_number & 0x7F
    ss_msb = (sample_number >> 7) & 0x7F
    return [0x47, channel & 0x7F, 0x14, 0x48, ss_lsb, ss_msb]


def to_nibble_pairs(raw_bytes):
    # split each raw byte into low, hi nibble midi byte pairs
    # encoding used by SDATA/PDATA/KDATA messages
    # NOT the same as plain char codes used by SLIST
    # NOT teh same as MSB first bit packing used by standard SDS
    pairs = []
    for b in raw_bytes:
        pairs.append(b & 0x0F)
        pairs.append((b >> 4) & 0x0F)
    return pairs


def _le_word(value):
    # 2 byte LITTLE ENDIAN word as used inside header block
    value &= 0xFFFF
    return [value & 0xFF, (value >> 8) & 0xFF]


def _le_word_as_two_words(value):
    # 4 byte field expressed as 2 little endian 16 bit words (low word first)
    # matches how SLOCAT/SLNGTH/SSTART/SMPEND are stored
    value &= 0xFFFFFFFF
    return _le_word(value & 0xFFFF) + _le_word((value >> 16) & 0xFFFF)


# akai hardware playback engine only runs at either 10 or 20kHz bandwidth
# (SBANDW=0 or 1 respectively)
# non native sample rates get compensated with a tuning offset
_NATIVE_RATES = {0: 22050, 1: 44100}


def compute_bandwidth_and_tuning(sample_rate):
    # pick whichever native engine speed is closer to sample rate
    # calculate how many semitones of tuning offset required to correct for remaining gap
    # returns (bandwidth, semitone_offset)
    # semitone_offset may be fractional, so pass to encode_tuning_offset for wire format
    bandwidth = min(
        _NATIVE_RATES, key=lambda b: abs(math.log2(sample_rate / _NATIVE_RATES[b]))
    )
    native_rate = _NATIVE_RATES[bandwidth]
    semitone_offset = 12 * math.log2(sample_rate / native_rate)
    return bandwidth, semitone_offset


def encode_tuning_offset(semitone_offset):
    # encode possibly fractional semitone offset as 2 byte signed 8.8 fixed point value
    # that the akai tuning offset fields use
    raw = round(semitone_offset * 256)
    raw &= 0xFFFF  # wrap negative values into 16 bit two's complement
    return _le_word(raw)


def build_sample_header_block(
    name,
    sample_length,
    sample_rate,
    original_pitch=60,
    playback_type=2,  # 2 = no looping/one shot
):
    # build the raw pre-nibble-split sample header block bytes
    bandwidth, semitone_offset = compute_bandwidth_and_tuning(sample_rate)
    block = bytearray(192)

    block[0] = 0x03  # SHINDENT = S3000 style header (CHECK THIS)
    block[1] = bandwidth & 0xFF
    block[2] = original_pitch & 0xFF
    block[3:15] = bytes(encode_name(name))  # SHNAME, 12 bytes
    block[15] = 0x80  # SSRVLD = sample rate valid
    block[16] = 0x00  # SLOOPS (internal use)
    block[17] = 0x00  # SALOOP (internal use)
    block[18] = 0x00  # spare i think
    block[19] = playback_type & 0xFF  # SPTYPE

    block[20:22] = bytes(encode_tuning_offset(semitone_offset))  # STUNO (tune offset)
    block[22:26] = bytes(
        _le_word_as_two_words(0)
    )  # SLOCAT (i think the hardware fills this in...)
    block[26:30] = bytes(_le_word_as_two_words(sample_length))  # SLNGTH
    block[30:34] = bytes(_le_word_as_two_words(0))  # SSTART
    block[34:38] = bytes(_le_word_as_two_words(max(sample_length - 1, 0)))  # SMPEND

    block[38:42] = bytes(_le_word_as_two_words(sample_length))
    block[48:50] = bytes(_le_word(9999))
    # loop slots 1-8 stay zero filled (bytearray default) - no looping

    block[138:140] = bytes(_le_word(sample_rate))  # SSRATE

    return bytes(block)


def build_sdata_message(
    name, sample_length, sample_rate, sample_number=0, channel=0, **header_kwargs
):
    # build full SDATA sysex message - akai's own 'create or replace this sample header' command
    header_block = build_sample_header_block(
        name, sample_length, sample_rate, **header_kwargs
    )
    nibbled = to_nibble_pairs(header_block)
    sample_num_bytes = [sample_number & 0x7F, (sample_number >> 7) & 0x7F]

    payload = [0x47, channel & 0x7F, 0x0B, 0x48] + sample_num_bytes + nibbled
    return bytes([0xF0] + payload + [0xF7])


if __name__ == "__main__":
    # a quick sanity check against some dummy file name data_bytes
    # only runs when this file is executed directly, not on import
    raw_sysex_from_akai = [
        0x47,
        0x00,
        0x05,
        0x48,
        0x08,
        0x00,
        0x1D,
        0x1B,
        0x1F,
        0x0B,
        0x1C,
        0x0F,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
        0x1D,
        0x0B,
        0x21,
        0x1E,
        0x19,
        0x19,
        0x1E,
        0x12,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
        0x1A,
        0x1F,
        0x16,
        0x1D,
        0x0F,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
        0x1D,
        0x13,
        0x18,
        0x0F,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
        0x1D,
        0x1E,
        0x0B,
        0x0C,
        0x01,
        0x0A,
        0x00,
        0x01,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
        0x1D,
        0x1E,
        0x0B,
        0x0C,
        0x01,
        0x0A,
        0x00,
        0x02,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
        0x1D,
        0x1E,
        0x0B,
        0x0C,
        0x01,
        0x0A,
        0x00,
        0x03,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
        0x1D,
        0x1E,
        0x0B,
        0x0C,
        0x01,
        0x0A,
        0x00,
        0x04,
        0x0A,
        0x0A,
        0x0A,
        0x0A,
    ]

    print(parse_slist_response(raw_sysex_from_akai))
