"""
Standalone dev tool - NOT part of the app. Works out how a Yamaha A4000's Sample Dump Standard NUMBERS follow its sample
list when samples are added and DELETED (and what a stereo sample looks like), by content - never by name.

The editor's waveform fetches SDS number == a sample's POSITION in the object list (measured on a unit that had never
deleted anything). If numbers shift or skip after a delete, the editor would show the wrong audio - so this finds out.

Subcommands (quit the AKAISDS app first):
    send      WRITES three tiny distinctive mono samples (and, with --stereo, one stereo file = two legs) to the unit
              through the app's own generic SDS send, as numbers 100+. Throwaway test data, in RAM.
    list      READ-ONLY: the object list's samples in order, each with its parameters (wave length, rate, loop mode,
              whether it has a right-channel wave).
    probe     READ-ONLY: SDS-fetches every number 0..N (N = samples + 2) and prints each answer's length, rate and a
              fingerprint, plus - with --save FILE - stores the fingerprints; with --compare FILE it says where each
              number's audio moved to since that run (use it before/after deleting a sample on the front panel).

    uv run python tools/a4000_sds_numbering.py send [--stereo]
    uv run python tools/a4000_sds_numbering.py list
    uv run python tools/a4000_sds_numbering.py probe --save /tmp/before.json
    (delete a sample on the unit: COMMAND > DELETE > Delete Type OneSample, Knob 5 picks it, Knob 1 EXEC)
    uv run python tools/a4000_sds_numbering.py list
    uv run python tools/a4000_sds_numbering.py probe --compare /tmp/before.json
"""

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import wave

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np
import soundfile as sf
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from controller.sampler_controller import SamplerController
from core import app_config, midi_manager as mm
from core import yamaha_params as yp
from a4000_sds_probe import picture

RATE = 44100
#: name -> (frames, waveform) - distinctive lengths so each can be told apart by size alone
TEST_SAMPLES = {
    "NUMTEST-A": (4000, lambda t: np.sin(2 * math.pi * 440 * t)),
    "NUMTEST-B": (5000, lambda t: 2 * ((t * 330) % 1) - 1),
    "NUMTEST-C": (6000, lambda t: np.sign(np.sin(2 * math.pi * 220 * t))),
}
STEREO = ("NUMTEST-ST", 4500)  # left = 500 Hz sine, right = 1500 Hz sine


def write_wav(path, data):
    sf.write(path, (np.clip(data, -1, 1) * 24000).astype(np.int16), RATE, subtype="PCM_16")


def fingerprint(samples):
    return hashlib.md5(np.asarray(samples, dtype=np.int16).tobytes()).hexdigest()[:12]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("send", "list", "probe"))
    ap.add_argument("--stereo", action="store_true", help="send: also send one stereo file")
    ap.add_argument("--wav", help="send: send ONLY this file (instead of the generated test samples)")
    ap.add_argument("--save")
    ap.add_argument("--compare")
    ap.add_argument("--extra", type=int, default=2, help="probe: how many numbers past the last sample to try")
    ap.add_argument("--timeout", type=int, default=4000, help="ms to wait for a header before calling a number empty")
    args = ap.parse_args()

    app = QCoreApplication([])
    midi = mm.MidiManager()
    in_name, out_name = app_config.get_saved_ports()
    midi.open_input(in_name)
    midi.open_output(out_name)
    controller = SamplerController(midi)
    controller.set_device_type("yamaha_a4000")
    controller._receive_timeout_ms = args.timeout
    statuses = []
    controller.status_changed.connect(statuses.append)
    session = controller.yamaha_session()
    print(f"ports: {in_name} / {out_name}")

    def wait_signal(signal, start, timeout_ms=900_000):
        loop = QEventLoop()
        got = []
        signal.connect(lambda *a: (got.append(a), loop.quit()))
        QTimer.singleShot(0, start)
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        return got[0] if got else None

    def wait(start, timeout_ms=60_000):
        got = []
        loop = QEventLoop()
        start(lambda x: (got.append(x), loop.quit()))
        QTimer.singleShot(timeout_ms, loop.quit)
        loop.exec()
        return got[0] if got else None

    def sample_names():
        entries = wait(session.request_object_list)
        return [e.name for e in entries if e.kind == "sample"]

    try:
        if args.cmd == "send":
            tmp = tempfile.mkdtemp(prefix="a4000_numtest_")
            entries = []
            for name, (frames, fn) in TEST_SAMPLES.items():
                path = os.path.join(tmp, f"{name}.wav")
                write_wav(path, fn(np.arange(frames) / RATE))
                entries.append({"filepath": path, "name": name, "bit_depth": 16, "sample_rate": RATE, "mono": True})
            if args.wav:
                entries = [{"filepath": args.wav, "name": "NUMTEST-FILE", "bit_depth": 16, "sample_rate": RATE, "mono": True}]
            if args.stereo and not args.wav:
                name, frames = STEREO
                t = np.arange(frames) / RATE
                path = os.path.join(tmp, f"{name}.wav")
                stereo = np.stack([np.sin(2 * math.pi * 500 * t), np.sin(2 * math.pi * 1500 * t)], axis=1)
                sf.write(path, (stereo * 24000).astype(np.int16), RATE, subtype="PCM_16")
                entries.append({"filepath": path, "name": name, "bit_depth": 16, "sample_rate": RATE, "mono": False})
            print(f"sending {len(entries)} file(s): {[e['name'] for e in entries]}")
            before = sample_names()
            res = wait_signal(
                controller.transfer_finished, lambda: controller.send_file_queue(entries, channel=0, starting_sample_number=100)
            )
            print("transfer finished:", res, "| last status:", statuses[-1] if statuses else None)
            after = sample_names()
            print("samples before:", before)
            print("samples after: ", after)
            print("new:", [n for n in after if n not in before])
            return

        names = sample_names()
        if args.cmd == "list":
            print(f"{len(names)} samples in the object list (this is the order SDS positions follow, if positional):")
            for i, name in enumerate(names):
                d = wait(lambda cb: session.request_bulk("SP", name, cb))
                if d is None:
                    print(f"  {i:3d} {name!r}: no dump")
                    continue
                g = lambda k: yp.extract(yp.get("sample", k), d.data)  # noqa: E731
                wave_r = bytes(d.data[80:96]).decode("ascii", "replace").rstrip(" \x00")
                wave_l = bytes(d.data[64:80]).decode("ascii", "replace").rstrip(" \x00")
                print(
                    f"  {i:3d} {name!r:14} wave_length {g('wave_length'):6d} end {g('wave_end_address'):6d} "
                    f"rate {g('sampling_frequency_l'):6d}/{g('sampling_frequency_r'):6d} loop_mode {g('loop_mode')} "
                    f"waveL={wave_l!r} waveR={wave_r!r}{'  <- STEREO?' if wave_r else ''}"
                )
            return

        # probe
        results = {}
        tmp = tempfile.mkdtemp(prefix="a4000_numprobe_")
        top = len(names) + args.extra
        print(f"probing SDS numbers 0..{top - 1} ({len(names)} samples in the list)")
        for n in range(top):
            path = os.path.join(tmp, f"n{n}.wav")
            statuses.clear()
            res = wait_signal(controller.receive_finished, lambda n=n, path=path: controller.receive_sample_generic(n, path, 0))
            ok = bool(res and res[0]) and os.path.exists(path)
            if ok:
                data, rate = sf.read(path, dtype="int16", always_2d=False)
                channels = 1 if data.ndim == 1 else data.shape[1]
                mono = data if data.ndim == 1 else data[:, 0]
                results[n] = {"frames": int(len(mono)), "rate": int(rate), "fp": fingerprint(mono), "channels": channels}
                print(f"  number {n:3d}: {len(mono):6d} frames @ {rate} Hz  fp {results[n]['fp']}  {picture(mono)}")
            else:
                results[n] = None
                print(f"  number {n:3d}: nothing ({statuses[-1] if statuses else 'no status'})")
        if args.save:
            json.dump(results, open(args.save, "w"), indent=1)
            print("saved", args.save)
        if args.compare:
            old = {int(k): v for k, v in json.load(open(args.compare)).items()}
            print("\ncompared with", args.compare)
            by_fp = {v["fp"]: k for k, v in old.items() if v}
            for n, v in results.items():
                if v is None:
                    print(f"  number {n:3d}: nothing now" + (f" (was {old[n]['frames']} frames)" if old.get(n) else ""))
                else:
                    was = by_fp.get(v["fp"])
                    print(f"  number {n:3d}: now holds what number {was} held before" if was is not None else
                          f"  number {n:3d}: audio not seen in the earlier run")
    finally:
        midi.close_input()
        midi.close_output()


if __name__ == "__main__":
    main()
