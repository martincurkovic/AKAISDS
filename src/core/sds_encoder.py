import struct
import wave

DATA_BYTES_PER_PACKET = 120  # fixed by SDS spec
NO_LOOP = 0x7F  # loop type byte which translated to one-shot or no loop

# SDS HANDSHAKE IDs
ACK = 0x7F
NAK = 0x7E
CANCEL = 0x7D
WAIT = 0x7C
EOF_MSG = 0x7B

_HANDSHAKE_NAMES = {
    ACK: "ack",
    NAK: "nak",
    CANCEL: "cancel",
    WAIT: "wait",
    EOF_MSG: "eof",
}


def classify_response(data_bytes):
    # identify whether the incoming sysex is a universal SDS handshake, and if so, which one
    # returns a string or None if this doesnt look like a handshake
    if len(data_bytes) < 4:
        return None
    if data_bytes[0] != 0x7E:
        return None
    sub_id = data_bytes[2]
    return _HANDSHAKE_NAMES.get(sub_id)


# WAV READING CODE


def read_wav_samples(path):
    # reads 16 bit wav file and returns (samples, framerate)
    # samples: tuple of signed ints, one per mono channel (for now, stereo files are down-mixed to just left channel)
    # framerate: samples per second (ie, 44100)

    with wave.open(path, "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sampwidth != 2:
        raise ValueError(
            f"Only 16 bit WAV files are supported for now (got {sampwidth * 8} bits)"
        )

    # '<' = little endian, 'h' = signed 16 bit int, one per sample
    fmt = "<" + "h" * (len(raw) // 2)
    samples = struct.unpack(fmt, raw)

    if n_channels == 2:
        # take left channel only if the input file is mono
        samples = samples[0::2]

    return samples, framerate


def read_wav_channels(path):
    # read 16 bit PCM WAV and return (channels, framerate) WITHOUT downmixing stereo file
    # channels: list with one tuple per channel - [samples] for mono, [left_samples, right_samples] for stereo
    # framerate = samples per second (ie, 44100 or whaterver)

    with wave.open(path, "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sampwidth != 2:
        raise ValueError(
            f"Only 16-bit PCM WAV files are supported right now (got {sampwidth * 8}-bit"
        )

    fmt = "<" + "h" * (len(raw) // 2)
    interleaved = struct.unpack(fmt, raw)

    if n_channels == 1:
        return [interleaved], framerate
    elif n_channels == 2:
        left = interleaved[0::2]
        right = interleaved[1::2]
        return [left, right], framerate
    else:
        raise ValueError(
            f"Only mono or stereo WAV files are supported right now (got {n_channels} channels)"
        )


# BIT PACKING HELPERS AND STUFF


def sample_to_sds_bytes(sample, bit_depth=16):
    # left justify one sample into ceil(bit_depth / 7) MSB first 7 bit midi bytes
    # used for data packets. bits are packed with real bit_depth bits pushed to the top of the smallest 7 bit multiple container, zero padded at the bottom
    # 16 bit = 3 bytes (21 bit container)
    # 12 bit = 2 bytes (14 bit container)
    # IMPORTANT: MIDI SDS represents sample words as UNSIGNED quantities, with silence sitting at mid-scale (0x8000 for 16 bit)
    # not as signed two's complement

    bytes_per_word = (
        bit_depth + 6
    ) // 7  # ceiling division - how many 7 bit bytes are needed
    container_bits = bytes_per_word * 7
    shift = container_bits - bit_depth

    mask = (1 << bit_depth) - 1
    raw = sample & mask
    sign_bit = 1 << (bit_depth - 1)
    unsigned = raw ^ sign_bit  # convert two's complement to unsigned

    val = (unsigned << shift) & ((1 << container_bits) - 1)

    result = []
    for i in range(bytes_per_word):
        shift_amount = 7 * (bytes_per_word - 1 - i)  # MSB first
        result.append((val >> shift_amount) & 0x7F)
    return result

    # raw16 = sample & 0xFFFF
    # unsigned16 = raw16 ^ 0x8000  # two's complement -> unsigned/offset-binary
    # val21 = (unsigned16 << 5) & 0x1FFFFF  # shift by (21 - 16) = 5 bits
    # b0 = (val21 >> 14) & 0x7F
    # b1 = (val21 >> 7) & 0x7F
    # b2 = val21 & 0x7F
    # return b0, b1, b2


def bitcrush_sample(sample_16bit, effective_bits):
    # reduce 16 bit sample's effective resolution to something lower for sonic purposes only
    # still sent as a 16 bit sample, but the bottom bits are zero'd out
    # cant send anything other than 16 bit samples to the akai, so this doesnt actually speed up the transfer :(
    # pass if effective bits is 16 or higher
    if effective_bits >= 16:
        return sample_16bit
    shift = 16 - effective_bits
    return (sample_16bit >> shift) << shift  # zero out the low "shift" bits


def reduce_bit_depth(sample_16bit, target_bit_depth):
    # actually reduce bit depth for REALS instead of fake bitcrushing by zero-ing out lower bits
    # this wont work for akai sds, only generic sds (akai sds can only send 16 bit samples)
    if target_bit_depth >= 16:
        return sample_16bit
    shift = 16 - target_bit_depth
    return sample_16bit >> shift  # arithmetic shift - preserves sign, shrinks range


def downsample_samples(samples, decimation_factor):
    # reduce sample rate by integer factor
    # instead of just throwing away in-between samples (causing aliasing),
    # this does a simple box-filter low-pass. not as clean as proper FIR filtering,
    # but a whole lot better than just pure decimation
    if decimation_factor <= 1:
        return list(samples)

    downsampled = []
    for i in range(0, len(samples), decimation_factor):
        chunk = samples[i : i + decimation_factor]
        downsampled.append(sum(chunk) // len(chunk))
    return downsampled


def resample_to_target_rate(samples, source_rate, target_rate):
    # convert 'samples' from source_rate down to target_rate
    # only exact int ratio downsampling supported rn
    # upsampling and non int ratios raise ValueError instead of outputting wrong data
    # returns (new_samples, new_rate)
    if target_rate == source_rate:
        return list(samples), source_rate
    if target_rate > source_rate:
        raise ValueError(
            f"Upsampling ({source_rate}Hz --> {target_rate}Hz) isn't supported."
        )
    if source_rate % target_rate != 0:
        raise ValueError(
            f"Only exact integer-ratio downsampling is supported right now - "
            f"{source_rate} is not an exact multiple of {target_rate}"
        )

    decimation_factor = source_rate // target_rate
    return downsample_samples(samples, decimation_factor), target_rate


def to_3byte_lsb_first(value, bits=21):
    # encode unsigned int into 3 LSB first 7 bit MIDI bytes
    # used for header fields (sample period, sample length, loop points)
    # opposite byte order compared to sample sds bytes

    value &= (1 << bits) - 1  # mask down to "bits" bits (eg, 21)
    b0 = value & 0x7F
    b1 = (value >> 7) & 0x7F
    b2 = (value >> 14) & 0x7F
    return [b0, b1, b2]


def xor_checksum(bytes_list):
    # XOR every byte together - the single byte error check that SDS uses
    checksum = 0
    for b in bytes_list:
        checksum ^= b
    return checksum & 0x7F


# DUMP HEADER PACKET
def build_dump_header(samples, framerate, sample_number=0, channel=0, bit_depth=16):
    # build single dump header sysex packet that must be sent before any data packets
    # samples: the full tuple of sample words (we need its length)
    # framerate: samples per second (eg, 44100)
    # sample_number: which sample slot on the receiver this dump targets
    # channel: MIDI channel (0-127, NOT the same thing as a MIDI note channel 1-16)
    # bit_depth: bits per sample word - 16 for a standard 16 bit wav
    sample_length = len(samples)

    # SDS want the sample period in NANOSECONDS between samples, not a sample rate in Hz
    # so we need to invert framerate and convert seconds -> nanoseconds
    sample_period_ns = round(1_000_000_000 / framerate)

    # no sustain loop: conventionally, start/end both = sample length
    # and the loop type byte says "no loop" so the receiver ignores these points and just plays the sample once start to end
    loop_start = sample_length
    loop_end = sample_length

    sample_number_bytes = to_3byte_lsb_first(sample_number, bits=14)[
        :2
    ]  # 2 bytes, 0-16383
    period_bytes = to_3byte_lsb_first(sample_period_ns)
    length_bytes = to_3byte_lsb_first(sample_length)
    loop_start_bytes = to_3byte_lsb_first(loop_start)
    loop_end_bytes = to_3byte_lsb_first(loop_end)

    header = (
        [0x7E, channel & 0x7F, 0x01]
        + sample_number_bytes
        + [bit_depth & 0x7F]
        + period_bytes
        + length_bytes
        + loop_start_bytes
        + loop_end_bytes
        + [NO_LOOP]
    )

    packet = bytes([0xF0] + header + [0xF7])
    return packet


# DATA PACKETS
def build_data_packets(samples, channel=0, bit_depth=16):
    # build list of data packet sysex messages carrying the auidio
    # for 16 bit samples each pack holds exactly 40 sdample words. final packet is zero padded if sample count
    # is not a multiple of 40 - the header already told the receiver the tru sample length,
    # so the receiver knows when to stop giving a fck

    bytes_per_word = (bit_depth + 6) // 7
    words_per_packet = DATA_BYTES_PER_PACKET // bytes_per_word

    packets = []
    packet_num = 0

    for i in range(0, len(samples), words_per_packet):
        chunk = samples[i : i + words_per_packet]

        data_bytes = []
        for s in chunk:
            data_bytes.extend(sample_to_sds_bytes(s, bit_depth))

        while len(data_bytes) < words_per_packet * bytes_per_word:
            data_bytes.append(0)

        header = [0x7E, channel & 0x7F, 0x02, packet_num & 0x7F]
        checksum = xor_checksum(header + data_bytes)

        packet = bytes([0xF0] + header + data_bytes + [checksum, 0xF7])
        packets.append(packet)

        packet_num = (packet_num + 1) % 128  # SDS packet numbers wrap- at 128

    return packets


# TOP LEVEL ENTRY POINT
def build_sds_dump(samples, framerate, sample_number=0, channel=0, bit_depth=16):
    # build full list of sysex packets for one sample:
    # dump header first then every data packet in order
    # returns list of 'bytes' objects, each a complete ready to send sysex message (F0 ... F7)

    header_packet = build_dump_header(
        samples, framerate, sample_number, channel, bit_depth
    )
    data_packets = build_data_packets(samples, channel, bit_depth)
    return [header_packet] + data_packets


if __name__ == "__main__":
    # standalone smoke test with bullshit data.
    # have your fire extinguisher ready lmao
    fake_samples = list(range(-100, 100))  # 200 fake sample words
    fake_framerate = 44100

    dump = build_sds_dump(fake_samples, fake_framerate, sample_number=0)
    print(f"Built {len(dump)} packets total (1 header + {len(dump) - 1} data packets)")
    print(f"Header packet ({len(dump[0])} bytes): {dump[0].hex(' ')}")
    print(f"First data packet ({len(dump[1])} bytes): {dump[1].hex(' ')}")
