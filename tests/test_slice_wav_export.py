import wave

from core import slice_wav_export as swe


def test_several_slices_go_in_a_folder_named_after_the_base_name(tmp_path):
    assert swe.output_folder(str(tmp_path), " kick ", 4) == str(tmp_path / "KICK")


def test_a_single_slice_goes_straight_into_the_chosen_folder(tmp_path):
    assert swe.output_folder(str(tmp_path), "kick", 1) == str(tmp_path)


def test_a_name_never_ends_in_a_space_or_dot_on_disk(tmp_path):
    assert swe.wav_paths(str(tmp_path), ["A. ", "B"]) == [str(tmp_path / "A.wav"), str(tmp_path / "B.wav")]
    assert swe.file_stem(" .") == "slice"


def test_slices_are_written_as_16_bit_mono_wavs_at_the_source_rate(tmp_path):
    folder = tmp_path / "BREAK"
    paths = swe.wav_paths(str(folder), ["BREAK-1", "BREAK-2"])
    swe.write_slices(str(folder), paths, [[1, -2, 3], [4, 5]], 22050)
    with wave.open(paths[0], "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) == (1, 2, 22050, 3)
    with wave.open(paths[1], "rb") as wav:
        assert wav.getnframes() == 2


def test_a_second_channel_makes_stereo_files(tmp_path):
    paths = swe.wav_paths(str(tmp_path), ["X"])
    swe.write_slices(str(tmp_path), paths, [[1, 2, 3]], 44100, right_slices=[[4, 5, 6]])
    with wave.open(paths[0], "rb") as wav:
        assert (wav.getnchannels(), wav.getnframes()) == (2, 3)
