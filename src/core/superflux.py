# SuperFlux onset detection (Bock & Widmer 2013) for the Slice Editor's "High quality" mode, through VelociLoops (core/rx2.py loads
# the library). Compared with core/transient_detection.py (RMS rises) it works on a log-frequency spectrogram, so it also catches hits
# that change the SOUND without getting louder - on VelociLoops' own drum loops it found ~90% of ReCycle's own cut points with no extra
# markers where the simple detector found 20-70% (measured 2026-10-12, see AGENTS.md).
#
# VelociLoops only exposes detection as vl_create_from_superflux(), which builds a whole sliced RX2 around the audio; the cut points are
# read back from that file's slices. So every call redoes the analysis (about 35 ms per second of audio) - there is no "analyse once,
# re-pick per slider tick" like the simple detector's compute_flux/markers_from_flux. The Slice Editor therefore runs this off the GUI
# thread and debounces the slider (ui/slice_editor_window.py).

import array
import ctypes

from core import rx2

#: the slider's 0-100% maps to SuperFlux's peak-picking threshold on a log scale: 100% is the most sensitive (a low bar to count as
#: an onset), ~30% is VelociLoops' own default (1.1). Higher thresholds only ever drop the weaker hits.
_THRESHOLD_LEAST_SENSITIVE = 4.0
_THRESHOLD_MOST_SENSITIVE = 0.05

#: a sensitivity of 0 is OFF (like the simple detector) - no markers at all
MIN_SENSITIVITY = 1


class _Options(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_int32)
        for name in "frame_size fps filter_bands max_bins diff_frames min_slice_frames filter_equal online".split()
    ] + [
        (name, ctypes.c_float)
        for name in (
            "threshold combine_ms pre_avg pre_max post_avg post_max delay_ms ratio fmin fmax log_mul log_add"
        ).split()
    ]


def threshold_for_sensitivity(sensitivity):
    sensitivity = max(MIN_SENSITIVITY, min(100, sensitivity))
    return _THRESHOLD_LEAST_SENSITIVE * (_THRESHOLD_MOST_SENSITIVE / _THRESHOLD_LEAST_SENSITIVE) ** (sensitivity / 100.0)


def is_available():
    return rx2.is_available()


def to_float_buffer(samples):
    """16-bit ints -> a float32 buffer (-1..1) the library reads; convert ONCE and reuse it across detections."""
    return array.array("f", [v / 32768.0 for v in samples])


def detect(buffer, sample_rate, sensitivity):
    """Interior cut points (frame indices, ascending) found in a MONO float buffer (see to_float_buffer).

    Raises rx2.Rx2Unavailable (no library) or rx2.Rx2Error (the library refused the audio, e.g. an unsupported sample rate).
    sensitivity <= 0 returns [] without calling the library."""
    if sensitivity <= 0 or len(buffer) == 0:
        return []
    lib = rx2._load_library()
    options = _Options()
    lib.vl_superflux_default_options(ctypes.byref(options))
    options.threshold = threshold_for_sensitivity(sensitivity)
    address, length = buffer.buffer_info()
    error = ctypes.c_int(0)
    handle = lib.vl_create_from_superflux(
        1, int(sample_rate), 120000, ctypes.c_void_p(address), None, length, ctypes.byref(options), ctypes.byref(error)
    )
    if not handle:
        raise rx2.Rx2Error(f"SuperFlux couldn't analyse this audio: {rx2._error_text(lib, error.value)}")
    try:
        info = rx2._FileInfo()
        lib.vl_get_info(handle, ctypes.byref(info))
        markers = []
        for index in range(info.slice_count):
            slice_info = rx2._SliceInfo()
            if lib.vl_get_slice_info(handle, index, ctypes.byref(slice_info)) == 0 and 0 < slice_info.sample_start < length:
                markers.append(slice_info.sample_start)
        return sorted(set(markers))
    finally:
        lib.vl_close(handle)
