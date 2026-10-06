"""
Standalone dev tool library - NOT part of the app. Helpers for hardware experiments on a Yamaha A4000's NATIVE sample load (wave "WD"
messages + a sample "SP" message sent TO the unit; see dev_docs/a4000-native-load-findings.md). WRITES to the unit (RAM only): throwaway
objects only, app closed. Used from small scripts:

    import sys; sys.path.insert(0, "tools")
    from a4000_load_lab import *
    lab = Lab2()                                   # Lab = raw MIDI; Lab2 adds a YamahaSession (link / write / prog)
    quick(lab, "T-X", [tone(3000, 22050, 220)], 22050)          # load + verify, one line
    report(lab, "label", "T-X", [tone(...)], 22050)             # load + full before/after report
    poll_identity(lab.rig)                                      # seconds until the unit answers again
"""
import math, os, sys, time, struct
os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)
import a4000_load_probe as p
from core import yamaha_sysex as ysx, yamaha_params as yp, yamaha_wave as yw
from PySide6.QtCore import QEventLoop, QTimer

DEVICE = 0
KEYS = ("wave_start_address","wave_length","wave_end_address","loop_start_address","loop_length","loop_end_address",
        "loop_mode","sampling_frequency_l","sampling_frequency_r","original_key_l","key_range_low","key_range_high","fine_tune_l","coarse_tune")

def tone(n, rate, f, amp=12000):
    return [round(amp * math.sin(2*math.pi*f*i/rate) * min(1, (n-i)/400, i/50+0.01)) for i in range(n)]

class Lab:
    def __init__(self):
        self.rig = p.Rig()
        sp = self.rig.request("SP", "MIDI 00102")
        self.template = ysx.parse_bulk_dump(sp[0])
    def close(self): self.rig.close()
    def objs(self):
        for attempt in range(6):
            got = self.rig.request("OL", "")
            if got:
                return [(e.kind, e.name) for e in ysx.parse_object_list(ysx.parse_bulk_dump(got[0]).data)]
            print("  (object list: no answer, retry %d)" % (attempt + 1)); self.rig.pause(3)
        raise SystemExit("unit never answered the object list")
    def sp(self, name):
        r = self.rig.request("SP", name)
        return ysx.parse_bulk_dump(r[0]) if r else None
    def params(self, name):
        d = self.sp(name)
        if d is None: return None
        out = {k: yp.extract(yp.get("sample", k), d.data) for k in KEYS}
        out["waves"] = p.wave_names_of(d.data)
        return out
    def wave(self, name):
        raw, fr = p.fetch_wave(self.rig, name)
        return fr
    def build(self, name, channels, rate, wave_names=None, sets=None, template=None):
        t = template or self.template
        data = bytearray(t.data)
        data[2:18] = ysx.pad_name(name)
        n = len(channels[0])
        names = wave_names or [f"{name}-{'LR'[i]}"[:16] for i in range(len(channels))]
        wd = []
        for ch, wn in zip(channels, names):
            wd.append(yw.build_wave_messages(DEVICE, wn, ch))
        data[yp.WAVE_NAME_L_OFFSET:yp.WAVE_NAME_L_OFFSET+16] = ysx.pad_name(names[0])
        data[yp.WAVE_NAME_R_OFFSET:yp.WAVE_NAME_R_OFFSET+16] = ysx.pad_name(names[1]) if len(names) > 1 else bytes(16)
        base = {"wave_start_address":0,"wave_length":n,"wave_end_address":n,"loop_start_address":n,"loop_length":0,
                "loop_end_address":n,"loop_mode":0,"sampling_frequency_l":rate}
        if len(names) > 1: base["sampling_frequency_r"] = rate
        base.update(sets or {})
        for k, v in base.items(): yp.store(yp.get("sample", k), data, v)
        sp = ysx.build_bulk_dump(DEVICE, "SP", ysx.pad_name(name), bytes(data), header=t.header)
        return wd, sp, bytes(data)
    def send(self, msgs, gap=None, quiet=True):
        mark = len(self.rig.heard)
        t0 = time.monotonic()
        for m in msgs:
            self.rig.send(m)
            self.rig.pause((p.wire_seconds(m) + 0.4) if gap is None else (p.wire_seconds(m) + gap))
        el = time.monotonic() - t0
        self.rig.pause(1.5)
        heard = self.rig.heard[mark:]
        return el, heard
    def load(self, name, channels, rate, gap=None, **kw):
        wd, sp, data = self.build(name, channels, rate, **kw)
        flat = [m for w in wd for m in w]
        el, heard = self.send(flat + [sp], gap)
        return el, len(heard), data

from controller.sampler_controller import SamplerController

class Lab2(Lab):
    def __init__(self):
        super().__init__()
        self.ctl = SamplerController(self.rig.midi)
        self.ctl.set_device_type("yamaha_a4000")
        self.session = self.ctl.yamaha_session()
    def _wait(self, start, timeout=90):
        got = []; loop = QEventLoop()
        start(lambda r: (got.append(r), loop.quit()))
        QTimer.singleShot(timeout*1000, loop.quit); loop.exec()
        return got[0] if got else None
    def link(self, sample, program=128):
        return self._wait(lambda cb: self.session.change_link(ysx.program_object_name(program), sample, True, cb))
    def write(self, sample, key, value):
        return self._wait(lambda cb: self.session.write_parameter(yp.get("sample", key), value, sample, cb))
    def write_easy(self, program, key, value, slot):
        return self._wait(lambda cb: self.session.write_parameter(yp.get("easy_edit", key), value, ysx.program_object_name(program), cb, slot=slot))
    def prog(self, program=128):
        r = self.rig.request("PG", ysx.program_object_name(program))
        d = ysx.parse_bulk_dump(r[0])
        n = yp.extract(yp.get("program", "assigned_samples"), d.data)
        return [(yp.extract(yp.get("easy_edit", "assigned_name"), d.data, s), yp.extract(yp.get("easy_edit", "level_offset"), d.data, s)) for s in range(n)]

def poll_identity(rig, max_s=900, step=2.0):
    """Seconds until the unit answers an identity request (None if it never does within max_s)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < max_s:
        mark = len(rig.heard)
        rig.send(ysx.build_identity_request(0x7F)); rig.pause(step)
        if any(ysx.classify(m) == "identity_reply" for t, m in rig.heard[mark:]):
            return time.monotonic() - t0
    return None

def report(lab, label, name, channels, rate, wave_names=None, **kw):
    print(f"\n=== {label}: load {name!r}: {len(channels)} ch, {len(channels[0])} frames @ {rate}")
    before = lab.objs()
    el, heard, data = lab.load(name, channels, rate, wave_names=wave_names, **kw)
    a = poll_identity(lab.rig, 300, 2.0)
    after = lab.objs()
    print(f"sent {el:.1f}s, unit sent {heard} msgs, alive again after {a:.1f}s; objects {len(before)} -> {len(after)}")
    print("  added:", [o for o in after if o not in before], " removed:", [o for o in before if o not in after])
    names = [o for o in after if o[1] == name]
    print("  entries named", name, ":", names)
    pr = lab.params(name)
    print("  sample params:", pr)
    sp = lab.sp(name)
    if sp:
        print("  filter_cutoff/pan/original_key:", [yp.extract(yp.get("sample", k), sp.data) for k in ("filter_cutoff", "pan", "original_key_l")], " linked programs:", yp.linked_programs(sp.data))
        wl, wr = p.wave_names_of(sp.data)
        for side, wn, ch in (("L", wl, channels[0]), ("R", wr, channels[1] if len(channels) > 1 else None)):
            if wn:
                fr = lab.wave(wn)
                print(f"  wave {side} {wn!r}: read {len(fr) if fr else None} frames; identical to sent: {fr == ch if ch is not None else 'n/a (mono)'}")
    return before, after

def quick(lab, name, channels, rate, **kw):
    """load + wait alive + compact verification line"""
    t0 = time.monotonic()
    el, heard, data = lab.load(name, channels, rate, **kw)
    a = poll_identity(lab.rig, 300, 2.0)
    pr = lab.params(name)
    ok = {}
    if pr:
        wl, wr = pr["waves"]
        for side, wn, ch in (("L", wl, channels[0]), ("R", wr, channels[1] if len(channels) > 1 else None)):
            if wn:
                fr = lab.wave(wn)
                ok[side] = (len(fr) if fr else None, fr == ch)
    line = (f"{name!r}: {len(channels)}ch n={len(channels[0])} rate={rate} -> sent {el:.1f}s ({len(channels[0])*len(channels)/el:.0f} fr/s) alive+{a:.1f}s; "
            + (f"SP rate L/R={pr['sampling_frequency_l']}/{pr['sampling_frequency_r']} start={pr['wave_start_address']} len={pr['wave_length']} end={pr['wave_end_address']} "
               f"loop={pr['loop_start_address']},{pr['loop_length']},{pr['loop_end_address']} mode={pr['loop_mode']} waves={ok}" if pr else "NO SAMPLE"))
    print(line, flush=True)
    return pr
