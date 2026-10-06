"""
Standalone dev tool - NOT part of the app. PROBES whether a Yamaha A4000/A5000 accepts audio as a NATIVE bulk load
(wave data "WD" + sample "SP" bulk dumps SENT TO the unit) instead of Sample Dump Standard. WRITES to the unit (RAM only): run
only on a unit with nothing of value in it, with the app CLOSED. The manual says a bulk dump "can be received if bulk protect is off".

Everything is built from messages the unit itself sent, so the first experiments need no knowledge of formats we haven't measured.
Stages (each is a subcommand; every one saves what it saw under ~/.akaisds/a4000_discovery/load_probe/):

    capture  SAMPLE          save the sample's SP dump and the raw messages of its wave dump(s) (read-only)
    same     SAMPLE          send the unit's OWN wave messages back unchanged, byte for byte; read the wave back and compare
    rename   SAMPLE NEWWAVE  send that wave under a NEW name (header + [Common] block rewritten); does a new wave object appear
                             in the object list? is the read-back audio identical?
    sample   SAMPLE NEWNAME NEWWAVE
                             send the sample's SP dump under a NEW name pointing at wave NEWWAVE (which must exist): does a
                             sample object appear, and does it read back right?

Add --gap SECONDS to change the pause between messages (default: the message's wire time + 0.4 s). Anything the unit says while
and after a send is printed (and saved), because the unit's reply to a bulk load (an ACK-like message? an error? silence?) is exactly
what is unknown.

    uv run python tools/a4000_load_probe.py capture "MIDI 00102"
    uv run python tools/a4000_load_probe.py rename "MIDI 00102" "PROBE WAVE"
"""

import argparse
import os
import sys
import time

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from core import app_config, midi_manager as mm
from core import yamaha_params as yp
from core import yamaha_sysex as ysx
from core import yamaha_wave as yw

OUT_DIR = os.path.expanduser("~/.akaisds/a4000_discovery/load_probe")
DEVICE = 0


class Rig:
    def __init__(self, listen_to=None):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.midi = mm.MidiManager()
        in_name, out_name = app_config.get_saved_ports()
        self.midi.open_input(in_name)
        self.midi.open_output(out_name)
        self.heard = []  # (monotonic time, message bytes)
        self.midi.sysex_received.connect(lambda m: self.heard.append((time.monotonic(), bytes(m))))
        print(f"ports: {in_name} / {out_name}")

    def pause(self, seconds):
        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()

    def send(self, message):
        self.midi.send_sysex(bytes(message))

    def request(self, fmt, name, *, idle=8.0, hard=300.0):
        """Request a bulk dump and collect every message of that format until the stream goes quiet. -> [raw messages]"""
        start = len(self.heard)
        self.send(ysx.build_dump_request(DEVICE, fmt, name))
        t0 = time.monotonic()
        got = []
        last = t0
        while time.monotonic() - t0 < hard:
            self.pause(0.1)
            for t, m in self.heard[start:]:
                if ysx.classify(m) == "bulk_dump":
                    try:
                        d = ysx.parse_bulk_dump(m)
                    except ysx.YamahaSysexError:
                        continue
                    if d.fmt == fmt and (fmt == "OL" or d.name == name.rstrip()):
                        got.append(m)
                        last = t
            start = len(self.heard)
            limit = idle if got else 6.0
            if time.monotonic() - last > limit:
                break
        return got

    def close(self):
        self.midi.close_input()
        self.midi.close_output()

    def report_heard_since(self, index, label):
        new = self.heard[index:]
        print(f"  {label}: the unit sent {len(new)} message(s)")
        for t, m in new[:12]:
            print(f"     {ysx.classify(m):17} {len(m):5d} bytes  {m[:16].hex(' ')}")
        return new


def save(name, blob):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, "wb") as f:
        f.write(blob)
    return path


def syx(messages):
    return b"".join(b"\xf0" + bytes(m) + b"\xf7" for m in messages)


def object_list(rig):
    got = rig.request("OL", "")
    if not got:
        raise SystemExit("no object list from the unit")
    d = ysx.parse_bulk_dump(got[0])
    return ysx.parse_object_list(d.data)


def wave_names_of(sp_data):
    left = bytes(sp_data[yp.WAVE_NAME_L_OFFSET : yp.WAVE_NAME_L_OFFSET + 16]).decode("ascii", "replace").strip(" \x00")
    right = yp.wave_name_right(sp_data)
    return left, right


def fetch_wave(rig, wave):
    """-> (raw messages, decoded frames) of a wave object."""
    raw = rig.request("WD", wave, idle=10.0)
    asm = yw.WaveAssembler(wave)
    for m in raw:
        asm.feed(ysx.parse_bulk_dump(m))
    return raw, (asm.frames if asm.done else None)


def wire_seconds(message):
    return (len(message) + 2) * 10 / 31250


def send_all(rig, messages, gap):
    mark = len(rig.heard)
    t0 = time.monotonic()
    for i, m in enumerate(messages):
        rig.send(m)
        rig.pause(gap if gap is not None else wire_seconds(m) + 0.4)
    print(f"  sent {len(messages)} message(s) in {time.monotonic() - t0:.1f}s")
    rig.pause(3.0)  # let any reply arrive
    return rig.report_heard_since(mark, "while and just after sending")


def rewrite_wave_messages(messages, new_name, zero_pointer=False, flags=None):
    """The unit's own wave messages, renamed: the header's object name in every message and the [Common] block's name
    (data[4:20] of the first). Checksums are rebuilt by the codec."""
    out = []
    raw = ysx.pad_name(new_name)
    for i, m in enumerate(messages):
        d = ysx.parse_bulk_dump(m)
        data = bytearray(d.data)
        if i == 0:
            data[4:20] = raw
            if zero_pointer:  # [Common] offset 60 (UL): the object's own internal address in every dump the unit sent
                data[62:66] = bytes(4)
            if flags is not None:
                data[3] = flags
        out.append(ysx.build_bulk_dump(d.device, "WD", raw, bytes(data), header=d.header)[0:])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["capture", "same", "rename", "sample", "invert", "pair", "synth"])
    ap.add_argument("names", nargs="+")
    ap.add_argument("--gap", type=float, default=None)
    ap.add_argument("--frames", type=int, default=8000, help="synth: frames of the generated tone")
    ap.add_argument("--rate", type=int, default=22050, help="synth: sampling rate")
    ap.add_argument("--stereo", action="store_true", help="synth: a left AND a right wave (the right an octave up)")
    ap.add_argument("--sp-first", action="store_true", help="pair: send the sample dump before the wave dump")
    ap.add_argument("--zero-pointer", action="store_true", help="rename: zero the [Common] internal-address bytes (offset 60)")
    ap.add_argument("--flags", type=lambda v: int(v, 0), default=None, help="rename: set [Common] byte 1")
    args = ap.parse_args()
    rig = Rig()
    try:
        sample = args.names[0]
        sp = rig.request("SP", sample)
        if not sp:
            raise SystemExit(f"{sample!r}: no sample dump")
        sp_dump = ysx.parse_bulk_dump(sp[0])
        left, right = wave_names_of(sp_dump.data)
        print(f"{sample!r}: wave L {left!r} R {right!r}; SP {len(sp_dump.data)} bytes; stereo={yp.is_stereo(sp_dump.data)}")

        if args.stage == "capture":
            print("saved", save(f"SP-{sample.replace(' ', '_')}.syx", syx(sp)))
            for label, wave in (("L", left), ("R", right)):
                if not wave:
                    continue
                raw, frames = fetch_wave(rig, wave)
                print(f"  wave {label} {wave!r}: {len(raw)} message(s), {len(frames) if frames else None} frames;",
                      "saved", save(f"WD-{wave.replace(' ', '_')}.syx", syx(raw)))
            return

        raw, frames = fetch_wave(rig, left)
        if frames is None:
            raise SystemExit("couldn't read the wave")
        before = [(e.kind, e.name) for e in object_list(rig)]
        print(f"object list: {len(before)} objects")

        if args.stage == "same":
            print(f"\nSENDING the unit's own wave {left!r} back unchanged ({len(raw)} messages)")
            send_all(rig, raw, args.gap)
            raw2, frames2 = fetch_wave(rig, left)
            print(f"  read back: {len(frames2) if frames2 else None} frames; identical audio: {frames2 == frames}; "
                  f"identical bytes: {raw2 == raw}")
        elif args.stage == "invert":
            import struct
            parts = []
            for i, m in enumerate(raw):
                d = ysx.parse_bulk_dump(m)
                parts.append((d, 75 if i == 0 else 2))
            blob = b"".join(bytes(d.data[off:]) for d, off in parts)
            words = struct.unpack(f">{len(blob) // 2}h", blob[: len(blob) // 2 * 2])
            flipped = struct.pack(f">{len(words)}h", *[max(-32768, min(32767, -w)) for w in words])
            msgs, pos = [], 0
            for d, off in parts:
                n = len(d.data) - off
                data = bytes(d.data[:off]) + flipped[pos : pos + n]
                pos += n
                msgs.append(ysx.build_bulk_dump(d.device, "WD", d.name_raw, data, header=d.header))
            print(f"\nSENDING wave {left!r} back under its own name with the audio negated")
            send_all(rig, msgs, args.gap)
            raw2, frames2 = fetch_wave(rig, left)
            changed = frames2 is not None and frames2 != frames
            print(f"  read back: {len(frames2) if frames2 else None} frames; audio CHANGED: {changed}; "
                  f"negated as sent: {frames2 == [max(-32768, min(32767, -w)) for w in frames] if frames2 else None}")
        elif args.stage == "synth":
            import math
            new_sample = args.names[1]
            n = args.frames
            tone = lambda f: [round(12000 * math.sin(2 * math.pi * f * i / args.rate) * min(1, (n - i) / 400, i / 50 + 0.01)) for i in range(n)]  # noqa: E731
            channels = [("L", tone(220.0))] + ([("R", tone(440.0))] if args.stereo else [])
            data = bytearray(sp_dump.data)
            data[2:18] = ysx.pad_name(new_sample)
            msgs = []
            names = []
            for side, words in channels:
                wname = f"{new_sample}-{side}"[:16]
                names.append(wname)
                msgs += yw.build_wave_messages(DEVICE, wname, words)
            data[yp.WAVE_NAME_L_OFFSET : yp.WAVE_NAME_L_OFFSET + 16] = ysx.pad_name(names[0])
            data[yp.WAVE_NAME_R_OFFSET : yp.WAVE_NAME_R_OFFSET + 16] = ysx.pad_name(names[1]) if len(names) > 1 else bytes(16)
            sets = {"wave_start_address": 0, "wave_length": n, "wave_end_address": n, "loop_start_address": n, "loop_length": 0,
                    "loop_end_address": n, "loop_mode": 0, "sampling_frequency_l": args.rate}
            if len(names) > 1:
                sets["sampling_frequency_r"] = args.rate
            for key, value in sets.items():
                yp.store(yp.get("sample", key), data, value)
            sp_msg = ysx.build_bulk_dump(DEVICE, "SP", ysx.pad_name(new_sample), bytes(data), header=sp_dump.header)
            print(f"\nSENDING {len(msgs)} wave message(s) then the sample {new_sample!r} ({n} frames @ {args.rate} Hz, "
                  f"{'stereo' if len(names) > 1 else 'mono'}) - built from scratch")
            send_all(rig, [m for m in msgs] + [sp_msg], args.gap)
            after = [(e.kind, e.name) for e in object_list(rig)]
            print(f"  object list now {len(after)}: added {[o for o in after if o not in before]}")
            back = rig.request("SP", new_sample)
            if back:
                d = ysx.parse_bulk_dump(back[0])
                diff = [i for i in range(min(len(d.data), len(data))) if d.data[i] != data[i]]
                print(f"  sample read back: {len(d.data)} bytes; {len(diff)} differing byte(s): {diff[:40]}")
            for (side, words), wname in zip(channels, names):
                _, fr = fetch_wave(rig, wname)
                print(f"  wave {wname!r}: read back {len(fr) if fr else None} frames; audio identical: {fr == words}")
        elif args.stage == "pair":
            new_sample, new_wave = args.names[1], args.names[2]
            data = bytearray(sp_dump.data)
            data[2:18] = ysx.pad_name(new_sample)
            data[yp.WAVE_NAME_L_OFFSET : yp.WAVE_NAME_L_OFFSET + 16] = ysx.pad_name(new_wave)
            sp_msg = ysx.build_bulk_dump(DEVICE, "SP", ysx.pad_name(new_sample), bytes(data), header=sp_dump.header)
            wd_msgs = rewrite_wave_messages(raw, new_wave, args.zero_pointer, args.flags)
            order = [sp_msg] + wd_msgs if args.sp_first else wd_msgs + [sp_msg]
            print(f"\nSENDING {'SP then WD' if args.sp_first else 'WD then SP'}: sample {new_sample!r} + wave {new_wave!r}")
            send_all(rig, order, args.gap)
            after = [(e.kind, e.name) for e in object_list(rig)]
            print(f"  object list now {len(after)}: added {[o for o in after if o not in before]}")
        elif args.stage == "rename":
            new = args.names[1]
            print(f"\nSENDING wave {left!r} as NEW wave {new!r}")
            send_all(rig, rewrite_wave_messages(raw, new, args.zero_pointer, args.flags), args.gap)
            after = [(e.kind, e.name) for e in object_list(rig)]
            added = [o for o in after if o not in before]
            print(f"  object list now {len(after)}: added {added}; removed {[o for o in before if o not in after]}")
            raw2, frames2 = fetch_wave(rig, new)
            print(f"  read back {new!r}: {len(frames2) if frames2 else None} frames; identical audio: {frames2 == frames}")
        else:
            new_sample, new_wave = args.names[1], args.names[2]
            data = bytearray(sp_dump.data)
            data[2:18] = ysx.pad_name(new_sample)
            data[yp.WAVE_NAME_L_OFFSET : yp.WAVE_NAME_L_OFFSET + 16] = ysx.pad_name(new_wave)
            msg = ysx.build_bulk_dump(DEVICE, "SP", ysx.pad_name(new_sample), bytes(data), header=sp_dump.header)
            print(f"\nSENDING sample {sample!r} as NEW sample {new_sample!r} using wave {new_wave!r}")
            send_all(rig, [msg], args.gap)
            after = [(e.kind, e.name) for e in object_list(rig)]
            print(f"  object list now {len(after)}: added {[o for o in after if o not in before]}")
            back = rig.request("SP", new_sample)
            if back:
                d = ysx.parse_bulk_dump(back[0])
                same = bytes(d.data) == bytes(data)
                diff = [i for i in range(min(len(d.data), len(data))) if d.data[i] != data[i]]
                print(f"  read back {new_sample!r}: {len(d.data)} bytes; identical to what was sent: {same}; "
                      f"{len(diff)} differing byte(s): {diff[:40]}")
            else:
                print(f"  no sample {new_sample!r} came back")
    finally:
        rig.close()


if __name__ == "__main__":
    main()
