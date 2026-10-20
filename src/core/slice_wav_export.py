# Save the Slice Editor's slices as WAV files on the computer's disk (no sampler involved). Pure file logic: the window asks for the
# folder, this works out where each slice goes and writes it. Always 16-bit at the source's own rate: the sampler-side bit depth and
# rate choices (8-16 bit, lo-fi rates) are formats for the sampler, not for a file on disk.

import os

from core import sds_encoder

# a Windows file name can't end in a space or a dot, and a sample name can (the base name is cut to fit)
_TRIMMED_FROM_END = " ."


def file_stem(name):
    return name.rstrip(_TRIMMED_FROM_END) or "slice"


def output_folder(parent_folder, base_name, slice_count):
    # several slices go in a folder named after the base name, so a 64-slice export doesn't spill into the chosen folder;
    # a single slice (a plain trim) is just one file and goes straight in
    if slice_count == 1:
        return parent_folder
    return os.path.join(parent_folder, file_stem(base_name.strip().upper()))


def wav_paths(folder, names):
    return [os.path.join(folder, file_stem(name) + ".wav") for name in names]


def write_slices(folder, paths, slices, framerate, right_slices=None):
    # right_slices: the other channel, sliced at the same frames, for a stereo source (written as stereo files)
    if len(paths) != len(slices):
        raise ValueError(f"{len(paths)} paths for {len(slices)} slices")
    os.makedirs(folder, exist_ok=True)
    for index, path in enumerate(paths):
        if right_slices is None:
            sds_encoder.write_wav_file(path, slices[index], framerate)
        else:
            sds_encoder.write_wav_file_stereo(path, slices[index], right_slices[index], framerate)
    return len(paths)
