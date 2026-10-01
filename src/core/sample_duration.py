def sample_duration_seconds(sample_length, sample_rate):
    """Sample length (frames) at a given rate (Hz), in seconds.

    Shared by the Transfer Dashboard's Sample Info dialog
    (`ui/sample_info_dialog.py`) and the Program Editor's Samples tab list
    (`ui/program_editor_window.py`), so both compute duration the same way.
    """
    rate = sample_rate or 1  # guard against a zero rate
    return sample_length / rate
