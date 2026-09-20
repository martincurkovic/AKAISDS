# tests for ui/sample_info_dialog.py's _format_size() - pure function, no
# QApplication needed. Akai samples are always 16-bit internally regardless
# of how they were sent, so size is simply sample_length * 2 bytes; this
# pins down the KB/MB switchover, which is easy to get off-by-one on.

from ui.sample_info_dialog import _format_size


def test_zero_length_formats_as_kb():
    assert _format_size(0) == "0.0 KB"


def test_just_under_one_megabyte_still_shows_kb():
    # 524287 * 2 = 1048574 bytes, one byte short of 1 MiB
    assert _format_size(524287) == "1024.0 KB"


def test_exactly_one_megabyte_switches_to_mb():
    # 524288 * 2 = 1048576 bytes = exactly 1 MiB
    assert _format_size(524288) == "1.00 MB"


def test_a_few_megabytes_formats_with_two_decimal_places():
    assert _format_size(524288 * 2) == "2.00 MB"
