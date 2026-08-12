# tests for sds_encoder.py
# no Qt or MIDI hardware required, should be a fast batch of tests

import wave
import struct
import numpy as np
import soundfile as sf
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
