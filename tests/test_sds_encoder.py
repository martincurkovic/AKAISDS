# tests for sds_encoder.py
# no Qt or MIDI hardware required, should be a fast batch of tests

import wave
import struct
import numpy as np
import soundfile as sf
import pytest
from core import sds_encoder


def test_sample_to_sds_bytes_round_trip_16bit():
    for sample in [0, 1, -1, 32767, -32768, 12345, -12345]:
        encoded = sds_encoder.sample_to_sds_bytes(sample, bit_depth=16)
        decoded = sds_encoder.sds_bytes_to_sample(encoded, bit_depth=16)
        assert decoded == sample


def test_sample_to_sds_bytes_round_trip_various_bit_depths():
    for bit_depth in [8, 10, 12, 14, 16]:
        for sample in [0, 100, -100, 1000, -1000]:
            reduced = sds_encoder.reduce_bit_depth(sample, bit_depth)
            encoded = sds_encoder.sample_to_sds_bytes(reduced, bit_depth=bit_depth)
            decoded = sds_encoder.sds_bytes_to_sample(encoded, bit_depth=bit_depth)
            assert decoded == reduced


def test_dump_header_round_trip():
    samples = [100, -100, 200, -200, 300]
    header = sds_encoder.build_dump_header(
        samples, framerate=44100, sample_number=5, channel=1, bit_depth=16
    )
    parsed = sds_encoder.parse_dump_header(bytes(header[1:-1]))
    assert parsed["sample_number"] == 5
    assert abs(parsed["sample_rate"] - 44100) <= 1
    # above is bcos sample rate stored as int nanosecond period, not the sample rate
    # so converting rate -> period -> rate loses a tiny bit of precision, so close enough is good enough
    assert parsed["bit_depth"] == 16
    assert parsed["sample_length"] == len(samples)


def test_read_wav_samples_16bit(tmp_path):
    left = [0, 1000, -1000, 32767, -32768]
    right = [0, -500, 500, -32768, 32767]
    wav_path = tmp_path / "test.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        interleaved = []
        for l, r in zip(left, right):
            interleaved.extend([l, r])
        wf.writeframes(struct.pack("<" + "h" * len(interleaved), *interleaved))

    samples, rate = sds_encoder.read_wav_samples(str(wav_path))
    assert list(samples) == left
    assert rate == 44100

    channels, rate = sds_encoder.read_wav_channels(str(wav_path))
    assert list(channels[0]) == left
    assert list(channels[1]) == right


def _pack_24bit_le(value):
    # helper for writing raw 24 bit PCM frames - stdlib wave/struct have no
    # native 3 byte format, so pack it manually (little endian, matches what
    # _normalize_to_16bit expects to unpack)
    value &= 0xFFFFFF
    return bytes([value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF])


def test_read_wav_samples_24bit_scales_down_to_16bit(tmp_path):
    # _normalize_to_16bit scales 24 bit down to 16 bit via "value >> 8" -
    # using desired-value * 256 as the raw 24 bit sample makes that scaling
    # lossless (low 8 bits are all zero), so the round trip is exact
    desired = [0, 1000, -1000, 32767, -32768]
    raw24 = [d * 256 for d in desired]

    wav_path = tmp_path / "test24.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(3)
        wf.setframerate(44100)
        wf.writeframes(b"".join(_pack_24bit_le(v) for v in raw24))

    samples, rate = sds_encoder.read_wav_samples(str(wav_path))
    assert list(samples) == desired
    assert rate == 44100


def test_read_wav_samples_32bit_scales_down_to_16bit(tmp_path):
    # same lossless-scaling trick as the 24 bit test above, but for the
    # sampwidth == 4 branch (32 bit signed PCM, scaled down via ">> 16")
    desired = [0, 1000, -1000, 32767, -32768]
    raw32 = [d << 16 for d in desired]

    wav_path = tmp_path / "test32.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(4)
        wf.setframerate(48000)
        wf.writeframes(struct.pack("<" + "i" * len(raw32), *raw32))

    samples, rate = sds_encoder.read_wav_samples(str(wav_path))
    assert list(samples) == desired
    assert rate == 48000


def test_read_wav_channels_32bit_stereo_scales_and_deinterleaves(tmp_path):
    left = [0, 1000, -1000]
    right = [0, -500, 32767]
    interleaved = []
    for l, r in zip(left, right):
        interleaved.extend([l << 16, r << 16])

    wav_path = tmp_path / "stereo32.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(4)
        wf.setframerate(44100)
        wf.writeframes(struct.pack("<" + "i" * len(interleaved), *interleaved))

    channels, rate = sds_encoder.read_wav_channels(str(wav_path))
    assert list(channels[0]) == left
    assert list(channels[1]) == right
    assert rate == 44100


def test_read_wav_samples_32bit_float_reads_successfully(tmp_path):
    # unlike stdlib 'wave', soundfile can read FLOAT wav data directly and
    # convert it to int16 via the dtype argument - this is a real capability
    # gain from moving off stdlib wave, not something to guard against
    float_wav_path = tmp_path / "float32.wav"
    sf.write(
        str(float_wav_path),
        np.array([0.0, 0.5, -0.5], dtype=np.float32),
        44100,
        subtype="FLOAT",
    )

    samples, rate = sds_encoder.read_wav_samples(str(float_wav_path))
    assert rate == 44100
    assert len(samples) == 3


def test_read_wav_samples_8bit_recentres_unsigned_to_signed(tmp_path):
    # 8 bit WAV is unsigned (0-255, 128=silence) - reading it back through
    # read_wav_samples should hand back proper signed values, not raw
    # unsigned bytes. Chose values that land on clean signed round numbers
    # once re-centred: unsigned 0/128/255 -> signed -128/0/127
    unsigned_bytes = bytes([0, 128, 255])
    wav_path = tmp_path / "test8.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(1)
        wf.setframerate(22050)
        wf.writeframes(unsigned_bytes)

    samples, rate = sds_encoder.read_wav_samples(str(wav_path))
    assert list(samples) == [-32768, 0, 32512]
    assert rate == 22050


def test_read_aiff_samples(tmp_path):
    left = np.array([0, 1000, -1000, 32767, -32768], dtype=np.int16)
    right = np.array([0, -500, 500, -32768, 32767], dtype=np.int16)
    stereo = np.stack([left, right], axis=1)
    aiff_path = tmp_path / "test.aiff"
    sf.write(str(aiff_path), stereo, 44100, subtype="PCM_16")

    samples, rate = sds_encoder.read_wav_samples(str(aiff_path))
    assert list(samples) == list(left)
    assert rate == 44100

    n_channels, rate = sds_encoder.read_wav_info(str(aiff_path))
    assert n_channels == 2

    bit_depth = sds_encoder.read_wav_native_bit_depth(str(aiff_path))
    assert bit_depth == 16


def test_classify_response_ack_nak_wait_cancel():
    assert sds_encoder.classify_response([0x7E, 0x00, 0x7F, 0x00]) == "ack"
    assert sds_encoder.classify_response([0x7E, 0x00, 0x7E, 0x00]) == "nak"
    assert sds_encoder.classify_response([0x7E, 0x00, 0x7C, 0x00]) == "wait"
    assert sds_encoder.classify_response([0x7E, 0x00, 0x7D, 0x00]) == "cancel"


def test_classify_response_eof():
    assert sds_encoder.classify_response([0x7E, 0x00, 0x7B, 0x00]) == "eof"


def test_classify_response_none_for_non_sds_message():
    # akai manufacturer ID (0x47), not universal (0x7E) - not a handshake at all
    assert sds_encoder.classify_response([0x47, 0x00, 0x01, 0x48]) is None


def test_classify_response_none_for_too_short_message():
    assert sds_encoder.classify_response([0x7E, 0x00]) is None


# -----------------------
# RESAMPLING
# -----------------------


def test_resample_same_rate_returns_a_copy_unchanged():
    samples = [1, 2, 3]
    out, rate = sds_encoder.resample_to_target_rate(samples, 44100, 44100)
    assert out == samples
    assert out is not samples  # must be a copy, not the same list object
    assert rate == 44100


def test_resample_rejects_non_positive_target_rate():
    with pytest.raises(ValueError):
        sds_encoder.resample_to_target_rate([1, 2, 3], 44100, 0)


def test_resample_empty_input_returns_empty():
    out, rate = sds_encoder.resample_to_target_rate([], 44100, 22050)
    assert out == []
    assert rate == 22050


def test_resample_upsampling_produces_more_samples():
    samples = list(range(100))
    out, rate = sds_encoder.resample_to_target_rate(samples, 22050, 44100)
    assert rate == 44100
    assert len(out) > len(samples)
    assert out[0] == samples[0]  # interpolation must start exactly at the source


def test_resample_downsampling_produces_fewer_samples():
    samples = list(range(1000))
    out, rate = sds_encoder.resample_to_target_rate(samples, 44100, 22050)
    assert rate == 22050
    assert len(out) < len(samples)


# -----------------------
# WAV WRITING
# -----------------------


def test_write_wav_file_16bit_round_trips(tmp_path):
    samples = [0, 1000, -1000, 32767, -32768]
    path = tmp_path / "out16.wav"
    sds_encoder.write_wav_file(str(path), samples, framerate=44100, bit_depth=16)

    read_back, rate = sds_encoder.read_wav_samples(str(path))
    assert list(read_back) == samples
    assert rate == 44100


def test_write_wav_file_8bit_uses_unsigned_pcm(tmp_path):
    samples = [-128, -1, 0, 1, 127]
    path = tmp_path / "out8.wav"
    sds_encoder.write_wav_file(str(path), samples, framerate=22050, bit_depth=8)

    with wave.open(str(path), "rb") as wf:
        assert wf.getsampwidth() == 1
        assert wf.getframerate() == 22050
        raw = wf.readframes(wf.getnframes())

    # 8 bit WAV is unsigned, zero-centered at 128 - opposite of our signed samples
    assert list(raw) == [(s + 128) & 0xFF for s in samples]


def test_write_wav_file_non_16bit_upscales_to_fit_16bit_container(tmp_path):
    samples = [1, -1, 100]
    path = tmp_path / "out12.wav"
    sds_encoder.write_wav_file(str(path), samples, framerate=44100, bit_depth=12)

    read_back, _rate = sds_encoder.read_wav_samples(str(path))
    assert list(read_back) == [s << 4 for s in samples]  # left-shifted by (16 - 12)


def test_scale_sample_to_16bit_matches_the_write_wav_file_round_trip(tmp_path):
    # scale_sample_to_16bit exists so a live SDS receive (sampler_
    # controller.py's sample_chunk_received) can hand a listener 16-bit-
    # equivalent sample words directly, with no WAV file round trip to do
    # this implicitly the way write_wav_file/read_wav_samples normally
    # would - it has to produce the exact same numbers that round trip
    # would, for every bit depth, or a live-loading waveform would jump/
    # rescale the moment the final (WAV-file-backed) read replaces it
    for bit_depth, samples in [
        (16, [0, 1000, -1000, 32767, -32768]),
        (12, [1, -1, 100, -100]),
        (8, [-128, -1, 0, 1, 127]),
    ]:
        path = tmp_path / f"scale_check_{bit_depth}.wav"
        sds_encoder.write_wav_file(str(path), samples, framerate=44100, bit_depth=bit_depth)
        expected, _rate = sds_encoder.read_wav_samples(str(path))
        scaled = [sds_encoder.scale_sample_to_16bit(s, bit_depth) for s in samples]
        assert scaled == list(expected)


# -----------------------
# MISC HEADER/PACKET HELPERS
# -----------------------


def test_xor_checksum_known_value():
    assert sds_encoder.xor_checksum([0x01, 0x02, 0x03]) == (0x01 ^ 0x02 ^ 0x03)


def test_xor_checksum_masks_to_7_bits():
    assert sds_encoder.xor_checksum([0xFF]) == 0xFF & 0x7F


def test_build_dump_request_encodes_sample_number_and_channel():
    request = sds_encoder.build_dump_request(sample_number=300, channel=5)
    assert request[0] == 0x7E
    assert request[1] == 5
    assert request[2] == 0x03
    assert request[3] == 300 & 0x7F
    assert request[4] == (300 >> 7) & 0x7F


def test_build_data_packets_splits_and_zero_pads_final_packet():
    samples = list(range(45))  # more than one packet's worth (40 words/packet @ 16 bit)
    packets = sds_encoder.build_data_packets(samples, channel=0, bit_depth=16)
    assert len(packets) == 2

    # F0, header(4), 120 data bytes, checksum, F7 - both packets always this length,
    # the second one just has trailing zero-padded words
    assert len(packets[0]) == 1 + 4 + 120 + 1 + 1
    assert len(packets[1]) == 1 + 4 + 120 + 1 + 1

    # packet layout is F0, 0x7E, channel, 0x02, packet_num, ...data..., checksum, F7
    assert packets[0][4] == 0  # packet_num
    assert packets[1][4] == 1


def test_build_data_packets_packet_number_wraps_at_128():
    samples = list(range(40 * 130))  # forces just over 128 packets
    packets = sds_encoder.build_data_packets(samples, channel=0, bit_depth=16)
    assert len(packets) == 130
    assert packets[127][4] == 127
    assert packets[128][4] == 0  # wrapped back around per SDS spec
