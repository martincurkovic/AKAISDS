"""
Standalone dev tool - NOT part of the app. READ-ONLY probe of a Yamaha A4000/A5000's native WAVE DATA bulk dump ("WD").

Besides Sample Dump Standard, the unit serves a sample's audio as a Yamaha bulk dump of its WAVE objects (owner's manual
"MIDI Data Format", p.277-279: "1.1.4 Wave Data Bulk Dump 72+2*(wave data word size) byte"). Every sample links a left wave
object (SP payload @64) and, if stereo, a right one (@80); a wave object is requested by ITS OWN name (e.g. "SMP 045070"). One
dump, no per-packet handshake - and both channels of a stereo sample are reachable, which SDS (one number per sample) cannot do.

For each sample given it reads the sample's parameters (SP), then the WD dump of its left (and right) wave, decodes the words and
prints how they relate to the sample's wave start/length, a coarse picture, and (with --wav DIR) saves them as WAV files.
Nothing is written to the unit. Quit the AKAISDS app first.

    uv run python tools/a4000_wave_probe.py "sine wave" "MIDI 00101" "_NewSample" [--wav /tmp/waves]
"""

import argparse
import os
import struct
import sys
import time

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np
import soundfile as sf
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from controller.sampler_controller import SamplerController
from core import app_config, midi_manager as mm
from core import yamaha_params as yp
from core import yamaha_sysex as ysx
from a4000_sds_probe import picture


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("samples", nargs="+")
    ap.add_argument("--wav")
    ap.add_argument("--timeout", type=int, default=240, help="seconds to wait for one wave dump")
    args = ap.parse_args()

    app = QCoreApplication([])
    midi = mm.MidiManager()
    in_name, out_name = app_config.get_saved_ports()
    midi.open_input(in_name)
    midi.open_output(out_name)
    controller = SamplerController(midi)
    controller.set_device_type("yamaha_a4000")
    session = controller.yamaha_session()
    session.bulk_timeout_ms = args.timeout * 1000

    def fetch(fmt, name):
        got = []
        loop = QEventLoop()
        session.request_bulk(fmt, name, lambda d: (got.append(d), loop.quit()))
        QTimer.singleShot((args.timeout + 5) * 1000, loop.quit)
        loop.exec()
        return got[0] if got else None

    def fetch_wave(name):
        """A wave object's audio words. MEASURED (2026-10-06): a long wave arrives as SEVERAL complete bulk messages (each with its
        own 26-byte header, ~4 KB). Each message's data starts with a 2-byte block number (00 00, 00 01, ...); the first one then
        has the object's [Common] block, whose UL at data[22:26] is the word count (the sample's frames + 4 guard words), and the
        audio words begin at data[75] (frame k = the word at data[75 + 2k], verified against SDS dumps: sine wave exact)."""
        parts, state = [], {"need": None}
        loop = QEventLoop()
        idle = QTimer()
        idle.setSingleShot(True)
        idle.timeout.connect(loop.quit)

        def have():
            return (len(parts[0]) - 75 + sum(len(p) - 2 for p in parts[1:])) if parts else 0

        def on(message):
            try:
                if ysx.classify(message) != "bulk_dump":
                    return
                d = ysx.parse_bulk_dump(message)
            except ysx.YamahaSysexError:
                return
            if d.fmt != "WD" or d.name != name.rstrip():
                return
            data = bytes(d.data)
            if int.from_bytes(data[:2], "big") != len(parts):
                print(f"     chunk number {int.from_bytes(data[:2], 'big')} but expected {len(parts)} - ignored", flush=True)
                return
            parts.append(data)
            if state["need"] is None:
                state["need"] = 2 * int.from_bytes(parts[0][22:26], "big")
            print(f"     chunk {len(parts)}: {have()}/{state['need']} audio bytes", flush=True)
            if have() >= state["need"]:
                loop.quit()
            else:
                idle.start(8000)

        midi.sysex_received.connect(on)
        midi.send_sysex(ysx.build_dump_request(session.device, "WD", name))
        idle.start(15000)
        loop.exec()
        midi.sysex_received.disconnect(on)
        if not parts or have() < state["need"]:
            return None
        return (parts[0][75:] + b"".join(p[2:] for p in parts[1:]))[: state["need"]]

    try:
        for name in args.samples:
            sp = fetch("SP", name)
            if sp is None:
                print(f"{name!r}: no sample dump")
                continue
            g = lambda k: yp.extract(yp.get("sample", k), sp.data)  # noqa: E731
            left = bytes(sp.data[64:80]).decode("ascii", "replace").rstrip(" \x00")
            right = yp.wave_name_right(sp.data)
            print(f"\n{name!r}: wave_start {g('wave_start_address')} wave_length {g('wave_length')} rate {g('sampling_frequency_l')}"
                  f" loop_mode {g('loop_mode')}  waveL={left!r} waveR={right!r}")
            for label, wave in (("L", left), ("R", right)):
                if not wave:
                    continue
                t0 = time.monotonic()
                data = fetch_wave(wave)
                wd = data
                if wd is None:
                    print(f"  {label} {wave!r}: no WD dump (timeout {args.timeout}s)")
                    continue
                words = np.array(struct.unpack(f">{len(data) // 2}h", data))
                words = words[:-4]  # the last 4 words are the loop guard (a copy of the wave's start), not audio
                print(f"  {label} {wave!r}: {len(words)} frames in {time.monotonic() - t0:.1f}s; "
                      f"first {words[:6].tolist()} last {words[-6:].tolist()}")
                import hashlib
                print(f"     fingerprint {hashlib.md5(words.astype(np.int16).tobytes()).hexdigest()[:12]} (the same md5-of-int16 a4000_sds_numbering.py prints)")
                print(f"     picture {picture(words)}")
                if args.wav:
                    os.makedirs(args.wav, exist_ok=True)
                    path = os.path.join(args.wav, f"{name.strip().replace(' ', '_')}-{label}.wav")
                    sf.write(path, words.astype(np.int16), g("sampling_frequency_l") or 44100, subtype="PCM_16")
                    print("     saved", path)
    finally:
        midi.close_input()
        midi.close_output()


if __name__ == "__main__":
    main()
