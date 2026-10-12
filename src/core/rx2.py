# Reads Propellerhead/Reason Studios ReCycle REX2 (.rx2) loops through VelociLoops (third_party/velociloops,
# compiled by tools/build_velociloops.py into core/native/), a public-domain C library, via ctypes.
#
# An .rx2 is one recording plus a list of slices (the cut points ReCycle made). Everything here returns plain
# 16-bit int lists, the same shape the Slice Editor and sds_encoder already use - no numpy.
#
# Slices are returned at their RAW length (VLSliceInfo.sample_length): the audio that is in the file between one
# cut point and the next. vl_get_slice_frame_count() can be longer - when the file's transient-stretch is on, it adds
# a synthetic decay tail for playing the loop at a slower tempo; a sampler playing slices at their own pitch must
# not get that. Muted slices are kept: mute is an editing state in ReCycle, the audio is still in the file.

import array
import ctypes
import os
import sys
from dataclasses import dataclass, field

from s3k.messages import AKAI_CHARSET

RX2_EXTENSION = ".rx2"

_LIBRARY_NAME = {
    "darwin": "libvelociloops.dylib",
    "win32": "velociloops.dll",
}.get(sys.platform, "libvelociloops.so")
LIBRARY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "native", _LIBRARY_NAME)
# developers/tests can point at a library built elsewhere
_LIBRARY_ENV = "AKAISDS_VELOCILOOPS_LIB"


class Rx2Error(Exception):
    """The file could not be read; the message is short enough to show the user."""


class Rx2Unavailable(Rx2Error):
    """The VelociLoops library is missing or won't load, so no .rx2 can be read."""


class _FileInfo(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_int32)
        for name in (
            "channels sample_rate slice_count tempo original_tempo ppq_length time_sig_num time_sig_den "
            "bit_depth total_frames loop_start loop_end processing_gain transient_enabled transient_attack "
            "transient_decay transient_stretch silence_selected"
        ).split()
    ]


class _SliceInfo(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_int32)
        for name in "ppq_pos sample_length sample_start analysis_points flags".split()
    ]


_VL_OK = 0
_library = None
_library_error = None


def _load_library():
    global _library, _library_error
    if _library is not None:
        return _library
    if _library_error is not None:
        raise Rx2Unavailable(_library_error)
    path = os.environ.get(_LIBRARY_ENV) or LIBRARY_PATH
    try:
        lib = ctypes.CDLL(path)
        void_p = ctypes.c_void_p
        lib.vl_open.restype = void_p
        lib.vl_open.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
        lib.vl_close.restype = None
        lib.vl_close.argtypes = [void_p]
        lib.vl_get_info.restype = ctypes.c_int
        lib.vl_get_info.argtypes = [void_p, ctypes.POINTER(_FileInfo)]
        lib.vl_get_slice_info.restype = ctypes.c_int
        lib.vl_get_slice_info.argtypes = [void_p, ctypes.c_int32, ctypes.POINTER(_SliceInfo)]
        lib.vl_get_slice_frame_count.restype = ctypes.c_int32
        lib.vl_get_slice_frame_count.argtypes = [void_p, ctypes.c_int32]
        lib.vl_decode_slice.restype = ctypes.c_int
        lib.vl_decode_slice.argtypes = [
            void_p, ctypes.c_int32, void_p, void_p, ctypes.c_int32, ctypes.c_int32, ctypes.POINTER(ctypes.c_int32),
        ]
        lib.vl_error_string.restype = ctypes.c_char_p
        lib.vl_error_string.argtypes = [ctypes.c_int]
    except (OSError, AttributeError) as e:
        _library_error = f"Can't read .rx2 files: the VelociLoops library didn't load ({e})"
        raise Rx2Unavailable(_library_error) from e
    _library = lib
    return lib


def is_available():
    try:
        _load_library()
    except Rx2Unavailable:
        return False
    return True


def _error_text(lib, code):
    text = lib.vl_error_string(code)
    return text.decode("utf-8", "replace") if text else f"error {code}"


def slice_base_name(file_name):
    """Uppercase, with anything outside the sampler's name alphabet (no underscore, for one) turned into a hyphen -
    the stem slice names are numbered from."""
    stem = os.path.splitext(os.path.basename(file_name))[0].strip().upper()
    return "".join(c if c in AKAI_CHARSET else "-" for c in stem) or "RX2"


def _to_int16(buffer, count):
    # float (-1..1) -> clipped 16-bit ints
    return [
        32767 if v >= 1.0 else -32768 if v <= -1.0 else int(round(v * 32767.0))
        for v in buffer[:count]
    ]


@dataclass
class Rx2Slice:
    left: list
    right: list = None  # None for a mono loop
    ppq_pos: int = 0
    muted: bool = False

    def __len__(self):
        return len(self.left)


@dataclass
class Rx2Loop:
    path: str
    sample_rate: int
    channels: int
    tempo_bpm: float
    slices: list = field(default_factory=list)

    @property
    def name(self):
        return os.path.splitext(os.path.basename(self.path))[0]

    def mono_buffer(self):
        """(samples, markers): the slices laid end to end as ONE mono buffer (stereo averaged), plus the frame each
        slice after the first begins at - exactly the input the Slice Editor takes (a buffer and interior markers)."""
        samples = []
        markers = []
        for index, piece in enumerate(self.slices):
            if index:
                markers.append(len(samples))
            if piece.right is None:
                samples.extend(piece.left)
            else:
                samples.extend((l + r) // 2 for l, r in zip(piece.left, piece.right))
        return samples, markers


def load(path):
    """Decode *path* into an Rx2Loop. Raises Rx2Unavailable (no library) or Rx2Error (bad/unsupported file)."""
    lib = _load_library()
    error = ctypes.c_int(0)
    handle = lib.vl_open(os.fsencode(path), ctypes.byref(error))
    if not handle:
        raise Rx2Error(f"Couldn't open {os.path.basename(path)}: {_error_text(lib, error.value)}")
    try:
        info = _FileInfo()
        code = lib.vl_get_info(handle, ctypes.byref(info))
        if code != _VL_OK:
            raise Rx2Error(f"Couldn't read {os.path.basename(path)}: {_error_text(lib, code)}")
        if info.channels not in (1, 2) or info.sample_rate <= 0:
            raise Rx2Error(f"{os.path.basename(path)} has an unsupported format")
        loop = Rx2Loop(path, info.sample_rate, info.channels, info.tempo / 1000.0)
        for index in range(info.slice_count):
            slice_info = _SliceInfo()
            code = lib.vl_get_slice_info(handle, index, ctypes.byref(slice_info))
            if code != _VL_OK:
                raise Rx2Error(f"Couldn't read slice {index + 1}: {_error_text(lib, code)}")
            raw_length = slice_info.sample_length
            rendered = lib.vl_get_slice_frame_count(handle, index)
            if raw_length <= 0 or rendered < raw_length:
                continue  # an empty/marker-only record, nothing to send
            left = (ctypes.c_float * rendered)()
            right = (ctypes.c_float * rendered)() if info.channels == 2 else None
            written = ctypes.c_int32(0)
            code = lib.vl_decode_slice(
                handle, index, left, right, 0, rendered, ctypes.byref(written)
            )
            if code != _VL_OK:
                raise Rx2Error(f"Couldn't decode slice {index + 1}: {_error_text(lib, code)}")
            loop.slices.append(
                Rx2Slice(
                    left=_to_int16(array.array("f", bytes(left)), raw_length),
                    right=_to_int16(array.array("f", bytes(right)), raw_length) if right else None,
                    ppq_pos=slice_info.ppq_pos,
                    muted=bool(slice_info.flags & 1),
                )
            )
        if not loop.slices:
            raise Rx2Error(f"{os.path.basename(path)} has no audio slices")
        return loop
    finally:
        lib.vl_close(handle)
