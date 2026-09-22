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


def build_slist_request(channel=0):
    return [0x47, channel & 0x7F, 0x04, 0x48]


REVERSE_CHAR_MAP = {v: k for k, v in AKAI_CHAR_MAP.items()}


def encode_name(name, length=12):
    # turn plain text name into 12 char akai encoded name
    # padded with spaces to make up to 12 char length
    # unknown chars fall back to space instead of crashing
    # longer file names get chopped down to 12 chars
    name = name.upper()[:length].ljust(length)
    return [REVERSE_CHAR_MAP.get(ch, 0x0A) for ch in name]


def build_stereo_channel_name(base_name, suffix, total_length=12):
    # build name with channel suffix (ie -L or -R) that ALWAYS = 12 chars
    # truncate longer file names if required
    max_base_length = total_length - len(suffix)
    return base_name[:max_base_length] + suffix


def build_dels_request(sample_number, channel=0):
    # build DELS (Delete Sample Header and Data) request
    # F0, 47, cc, DELS(0x14), 48, ss, ss, F7
    # where ss, ss is sample number, LSB first 7 bit
    ss_lsb = sample_number & 0x7F
    ss_msb = (sample_number >> 7) & 0x7F
    return [0x47, channel & 0x7F, 0x14, 0x48, ss_lsb, ss_msb]


def build_rsdata_request(sample_number, channel=0):
    # build RSDATA request - asks for existing sample's header
    ss_lsb = sample_number & 0x7F
    ss_msb = (sample_number >> 7) & 0x7F
    return [0x47, channel & 0x7F, 0x0A, 0x48, ss_lsb, ss_msb]


def _to_4byte_lsb_first(value):
    # encode unsigned int into 4 lsb first 7 bit midi bytes (up to 28 bits)
    # used by RSPACK's offset/count fields which are wider than the header's 21 bit fields
    return [
        value & 0x7F,
        (value >> 7) & 0x7F,
        (value >> 14) & 0x7F,
        (value >> 21) & 0x7F,
    ]


def build_rspack_request(
    sample_number, offset, num_samples, interval=1, function=0, channel=0
):
    # build RSPACK (request sample data packet) request
    # F0,47,cc,RSPACK(0x0C),48,
    #     ss,ss              sample number (LSB-first 7-bit)
    #     oo,oo,oo,oo        address offset from start of sample (LSB-first, up to 28 bits)
    #     nn,nn,nn,nn        number of samples required (LSB-first, up to 28 bits)
    #     ii                 interval between samples (1 = every sample, no decimation)
    #     ff                 function (0 = plain samples, 1 = average, 2 = peak)
    # F7
    # after this, the akai sampler responds with standard universal data packets (sub-ID 0x02)
    ss = [sample_number & 0x7F, (sample_number >> 7) & 0x7F]
    oo = _to_4byte_lsb_first(offset)
    nn = _to_4byte_lsb_first(num_samples)
    return (
        [0x47, channel & 0x7F, 0x0C, 0x48]
        + ss
        + oo
        + nn
        + [interval & 0x7F, function & 0x7F]
    )


def parse_sdata_response(data_bytes):
    # decode incoming SDATA (0x0B) response
    # returns sample_number, name, sample_length, sample_rate, root_key, detune
    # bit depth is NOT an explicit field in this structure:
    # the whole S1000/S2000/S3000 family ALWAYS store sample data internally as 16 bit
    sample_number = (data_bytes[4] & 0x7F) | ((data_bytes[5] & 0x7F) << 7)
    body_nibbles = data_bytes[6:]

    raw = []
    for i in range(0, len(body_nibbles) - 1, 2):
        lo, hi = body_nibbles[i], body_nibbles[i + 1]
        raw.append((lo & 0x0F) | ((hi & 0x0F) << 4))
    raw = bytes(raw)

    name = "".join(AKAI_CHAR_MAP.get(b, "?") for b in raw[3:15]).strip()

    def _le_word(b, o):
        return b[o] | (b[o + 1] << 8)

    def _le_dword(b, o):
        return _le_word(b, o) + _le_word(b, o + 2) * 65536

    return {
        "sample_number": sample_number,
        "name": name,
        "bit_depth": 16,  # fixed - see docstring
        "sample_length": _le_dword(raw, 26),
        "sample_rate": _le_word(raw, 138),
        "root_key": raw[
            2
        ],  # original_pitch, same offset build_sample_header_block writes to
        "detune": decode_tuning_offset(
            raw[20:22]
        ),  # STUNO - semitone offset, eg 0.5 = half semitone sharp
    }


# offset within the 192 byte raw sample header block where SHNAME lives
_SHNAME_OFFSET = 3
_SHNAME_LENGTH = 12


def build_rename_sample_request(sample_number, new_name, channel=0):
    # rename existing sample WITHOUT touching its audio data or length
    # uses the documented offset addressed "receive sample header bytes" commmand (function code 0x2C)
    # writing only the header 12-byte SHNAME field (offset 3) rather than the WHOLE header
    # this is important because sending a full SDATA header for an existing sample number risks the device
    # mis-interpreting it as a full create/replace sample
    # at least I THINK this will work, should probably test this hey?
    ss_lsb = sample_number & 0x7F
    ss_msb = (sample_number >> 7) & 0x7F
    offset_lsb = _SHNAME_OFFSET & 0x7F
    offset_msb = (_SHNAME_OFFSET >> 7) & 0x7F
    length_lsb = _SHNAME_LENGTH & 0x7F
    length_msb = (_SHNAME_LENGTH >> 7) & 0x7F

    name_bytes = encode_name(new_name, length=_SHNAME_LENGTH)
    nibbled = to_nibble_pairs(name_bytes)

    return [
        0x47,
        channel & 0x7F,
        0x2C,
        0x48,
        ss_lsb,
        ss_msb,
        0x00,
        offset_lsb,
        offset_msb,
        length_lsb,
        length_msb,
    ] + nibbled


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


def decode_tuning_offset(byte_pair):
    # basically the exact opposite to encode_tuning_offset
    # semitone_offset
    raw = byte_pair[0] | (byte_pair[1] << 8)
    if raw & 0x8000:
        raw -= 0x10000  # sign-extend from 16 bit two's complement
    return raw / 256


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


def build_pdata_request(program_index, raw_header_bytes, channel=0):
    # build PDATA (Program Common Data) sysex message - akai's own
    # 'create or replace this program header' command, function code 0x07
    # raw_header_bytes must be the FULL 192 byte raw program header (clone
    # an existing one and patch just the fields that differ - see
    # program_editor_bridge.py's create-program/create-keygroup handlers -
    # never synthesize one from scratch, s3k.params only documents ~115 of
    # the 192 real bytes)
    # F0, 47, cc, PDATA(0x07), 48, pp, pp, <192 bytes nibbled>, F7
    # where pp, pp is program number, LSB first 7 bit
    pp_lsb = program_index & 0x7F
    pp_msb = (program_index >> 7) & 0x7F
    nibbled = to_nibble_pairs(raw_header_bytes)
    payload = [0x47, channel & 0x7F, 0x07, 0x48, pp_lsb, pp_msb] + nibbled
    return bytes([0xF0] + payload + [0xF7])


def build_kdata_request(program_index, keygroup_index, raw_header_bytes, channel=0):
    # build KDATA (Keygroup Data) sysex message - akai's own 'create or
    # replace this keygroup header' command, function code 0x09
    # raw_header_bytes must be the FULL 192 byte raw keygroup header, same
    # clone-then-patch rule as build_pdata_request above
    # F0, 47, cc, KDATA(0x09), 48, pp, pp, kk, <192 bytes nibbled>, F7
    # where pp, pp is program number (LSB first 7 bit), kk is keygroup number
    pp_lsb = program_index & 0x7F
    pp_msb = (program_index >> 7) & 0x7F
    kk = keygroup_index & 0x7F
    nibbled = to_nibble_pairs(raw_header_bytes)
    payload = [0x47, channel & 0x7F, 0x09, 0x48, pp_lsb, pp_msb, kk] + nibbled
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
