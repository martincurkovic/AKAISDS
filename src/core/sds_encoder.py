import struct
import wave

WORDS_PER_DATA_PACKET = 40  # fixed by SDS spec
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


# BIT PACKING HELPERS AND STUFF


def sample_to_sds_bytes(sample):
    # left justify a 16 bit sample into 3 MSB first 7 bit MIDI bytes
    # used for data packets. bits are packed with the real 16 bits pushed to the top of a 21 bit container, zero padded at the bottom
    # ie left justified
    raw16 = sample & 0xFFFF
    val21 = (raw16 << 5) & 0x1FFFFF  # shift by (21 - 16) = 5 bits
    b0 = (val21 >> 14) & 0x7F
    b1 = (val21 >> 7) & 0x7F
    b2 = val21 & 0x7F
    return b0, b1, b2


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
def build_data_packets(samples, channel=0):
    # build list of data packet sysex messages carrying the auidio
    # each pack holds exactly 40 sdample words. final packet is zero padded if sample count
    # is not a multiple of 40 - the header already told the receiver the tru sample length,
    # so the receiver knows when to stop giving a fck
    packets = []
    packet_num = 0

    for i in range(0, len(samples), WORDS_PER_DATA_PACKET):
        chunk = samples[i : i + WORDS_PER_DATA_PACKET]

        data_bytes = []
        for s in chunk:
            data_bytes.extend(sample_to_sds_bytes(s))

        while len(data_bytes) < WORDS_PER_DATA_PACKET * 3:
            data_bytes.append(0)

        header = [0x7E, channel & 0x7F, 0x02, packet_num & 0x7F]
        checksum = xor_checksum(header + data_bytes)

        packet = bytes([0x70] + header + data_bytes + [checksum, 0xF7])
        packets.append(packet)

        packet_num = (packet_num + 1) % 128  # SDS packet numbers wrap- at 128

    return packets


# TOP LEVEL ENTRY POINT
def build_sds_dump(samples, framerate, sample_number=0, channel=0):
    # build full list of sysex packets for one sample:
    # dump header first then every data packet in order
    # returns list of 'bytes' objects, each a complete ready to send sysex message (F0 ... F7)

    header_packet = build_dump_header(samples, framerate, sample_number, channel)
    data_packets = build_data_packets(samples, channel)
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
