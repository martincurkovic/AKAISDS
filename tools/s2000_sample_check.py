"""
Standalone dev tool - NOT part of the app. READ-ONLY on the sampler: `compare` sends ONE sample-header read request (RSHEADER for the sample you name), nothing is
written. App CLOSED (it owns the MIDI ports) and CLOSE ANY OTHER MIDI SOFTWARE (a DAW can echo the sampler's replies back at it, and a sampler executes a returned
data frame as a WRITE; see AGENTS.md, "Real-hardware safety").

It settles the open questions in src/core/akai_sample_file.py: does the header we write match the one in a sample file the sampler itself wrote?

    uv run python tools/s2000_sample_check.py show FILE.s3            # offline: what a sample file holds, and whether its size is exactly header + audio
    uv run python tools/s2000_sample_check.py compare INDEX REAL.s3   # read sample INDEX's header over SysEx, build the header Save as Akai File WOULD write, diff it
                                                                      # against REAL.s3 (the same sample, saved to disk by the sampler and extracted with akaiutil)
    uv run python tools/s2000_sample_check.py diff A.s3 B.s3          # offline: the same header diff between two files

Procedure for `compare`:
  1. Pick a sample with something to see: a loop (or two), a non-zero tune, a root note that is not the default, SPTYPE other than "no looping".
  2. Save it to disk with the sampler's own SAVE page, and export it as a .s3 file with akaiutil (or your disk tool): that is REAL.s3.
  3. With the sample unchanged and still resident, `compare INDEX REAL.s3` (INDEX is its position in the sampler's sample list, counted from 0).
An empty diff means Save as Akai File would write the same header the sampler did, apart from the two addresses the sampler assigns (SLOCAT, SSPAIR - ignored
on purpose; their values in the real file are printed, since that is the question). Any line printed is a byte our header gets wrong, with its field name when the app
knows it. `compare` also says whether the real file's size is exactly header + 2 x SLNGTH or padded (a block size?). Send the whole output.
"""

import os
import sys

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import akai_sample_file as asf


def load_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def header_of(data):
    """The header block of a file's bytes (the size is worked out from SLNGTH; a file that doesn't parse is reported by the caller)."""
    return asf.parse_file(data, allow_padding=True).header


def summary(label, header, size=None):
    sample = asf.SampleFile(header=header)
    lines = [f"{label}: {len(header)}-byte header ({sample.extension}), name {sample.name!r}, {sample.sample_rate} Hz, SLNGTH {sample.length} words"]
    lines.append(f"  SLOCAT {bytes(header[asf.SLOCAT[0]:asf.SLOCAT[1]]).hex(' ')}   SSPAIR {bytes(header[asf.SSPAIR[0]:asf.SSPAIR[1]]).hex(' ')}")
    if size is not None:
        exact = len(header) + 2 * sample.length
        lines.append(
            f"  file size {size}: header + 2 x SLNGTH = {exact}" + ("  (exactly)" if size == exact else f"  (the file is {size - exact:+d} bytes from that)")
        )
    return "\n".join(lines)


def report(lines, a_label, b_label):
    if not lines:
        print(f"\nIDENTICAL: {a_label} and {b_label} agree on every header byte except the sampler-assigned addresses (ignored).")
        return 0
    print(f"\n{len(lines)} DIFFERENCE(S), {a_label} vs {b_label}:")
    for line in lines:
        print(f"  {line}")
    return 1


def read_header(index):
    from core import app_config, midi_manager as mm
    from core import program_editor_bridge as peb

    midi = mm.MidiManager()
    in_name, out_name = app_config.get_saved_ports()
    midi.open_input(in_name)
    midi.open_output(out_name)
    bridge = peb.connect(midi, "akai_s2000_s3000")
    print(f"ports {in_name} / {out_name}", flush=True)
    header = bytes(bridge.get_header_bytes("sample", index, 0, asf.S3000_HEADER_SIZE))
    asf.validate_header(header)
    print(f"read the header of sample {index} ({len(header)} bytes)", flush=True)
    return header


def main(argv):
    if len(argv) < 2 or argv[0] not in ("show", "compare", "diff"):
        print(__doc__)
        return 2
    command, rest = argv[0], argv[1:]
    if command == "show":
        data = load_bytes(rest[0])
        print(summary(rest[0], header_of(data), len(data)))
        return 0
    if command == "diff":
        a, b = load_bytes(rest[0]), load_bytes(rest[1])
        ha, hb = header_of(a), header_of(b)
        print(summary(rest[0], ha, len(a)))
        print(summary(rest[1], hb, len(b)))
        return report(asf.compare_headers(ha, hb), rest[0], rest[1])
    index = int(rest[0])
    real_data = load_bytes(rest[1])
    real = header_of(real_data)
    mine = asf.normalized_header(read_header(index))
    print(summary("from the sampler (as Save as Akai File would write it)", mine))
    print(summary(f"real file {rest[1]}", real, len(real_data)))
    return report(asf.compare_headers(mine, real), "our header", "the real file's")


if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    os._exit(code)  # (the MIDI ports' native threads can keep a plain exit waiting)
