from ui.main_window import _build_audio_filter_string
from core import sds_encoder


def test_audio_filter_string_includes_every_supported_extension():
    # this is the whole point of extracting the helper - if a format ever
    # gets added to SUPPORTED_AUDIO_EXTENSIONS, this test catches whether
    # the file-open dialog would actually show it, without needing to
    # touch any real Qt dialog at all
    filter_string = _build_audio_filter_string()
    for ext in sds_encoder.SUPPORTED_AUDIO_EXTENSIONS:
        assert f"*{ext}" in filter_string
