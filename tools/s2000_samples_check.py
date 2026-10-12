"""
Standalone dev tool - NOT part of the app. WRITES to a real Akai S2000/S3000 on the saved MIDI ports (app CLOSED), through the Program Editor's own
code (ProgramEditorWindow, offscreen, nobody to click its dialogs - they all answer Yes):

    QT_QPA_PLATFORM=offscreen uv run python tools/s2000_samples_check.py roundtrip [SAMPLE]   # receive a sample back, compare with what was sent
    QT_QPA_PLATFORM=offscreen uv run python tools/s2000_samples_check.py errors               # .p3 refusals: nothing may be written
    QT_QPA_PLATFORM=offscreen uv run python tools/s2000_samples_check.py edits [SAMPLE]       # fade, normalise, filter, reverse, trim - each read back

The samples used are the ones tools/s2000_slices_check.py sent (S2K-01..04, slices of tests/test_audio.wav).
"""

import functools
import os
import sys
import time

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import soundfile as sf
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

import s2000_check as sc
from controller.sampler_controller import SamplerController
from core import akai_program_file as apf
from core import app_config, midi_manager as mm
from core import program_editor_bridge as peb
from core import sample_editing
from s3k import params as p
from ui import program_editor_window as pew
from ui import theme

SRC_WAV = os.path.join(os.path.dirname(__file__), "..", "tests", "test_audio.wav")


class Rig:
    def __init__(self):
        self.app = QApplication([])
        theme.apply_to_app(self.app)
        self.midi = mm.MidiManager()
        in_name, out_name = app_config.get_saved_ports()
        self.midi.open_input(in_name)
        self.midi.open_output(out_name)
        self.controller = SamplerController(self.midi)
        self.controller.set_device_type("akai")
        self.bridge = peb.connect(self.midi, "akai_s2000_s3000")
        rig = self

        class Host:
            sampler_controller = rig.controller
            midi_manager = rig.midi

            def show(self):
                pass

        self.window = pew.ProgramEditorWindow(Host(), bridge=self.bridge)
        self.dialogs = []
        QMessageBox.question = staticmethod(lambda parent, title, text, *a, **k: (self.dialogs.append(("question", title, text)), QMessageBox.StandardButton.Yes)[1])
        QMessageBox.warning = staticmethod(lambda parent, title, text, *a, **k: (self.dialogs.append(("warning", title, text)), QMessageBox.StandardButton.Ok)[1])
        QMessageBox.information = staticmethod(lambda parent, title, text, *a, **k: (self.dialogs.append(("information", title, text)), QMessageBox.StandardButton.Ok)[1])
        sc.say("INFO", f"ports {in_name} / {out_name}")
        self.wait_for(lambda: self.window.program_list.count() > 0 and self.window._worker.is_idle(), 90)

    def spin(self, seconds):
        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()

    def wait_for(self, cond, timeout=120):
        end = time.monotonic() + timeout
        while time.monotonic() < end and not cond():
            self.spin(0.1)
        return cond()

    def idle(self):
        self.wait_for(self.window._worker.is_idle, 180)
        self.spin(0.5)
        self.wait_for(self.window._worker.is_idle, 180)

    def select(self, name):
        self.idle()
        names = self.bridge.sample_list()
        index = names.index(name)
        self.window.sample_list_widget.setCurrentRow(index)
        self.idle()
        return index

    def audio(self, name):
        """Receive `name` from the sampler (a real SDS dump) - what the window's own 'load waveform' does."""
        self.idle()
        index = self.bridge.sample_list().index(name)
        return self.window._fetch_sample_audio_blocking(self.controller, index)

    def header(self, name, fields):
        self.idle()
        index = self.bridge.sample_list().index(name)
        return {f: self.bridge.get_parameter(p.lookup(f, "sample"), index) for f in fields}


def source_slices(count=4):
    data, rate = sf.read(SRC_WAV, dtype="int16")
    if data.ndim > 1:
        data = data[:, 0]
    data = [int(v) for v in data]
    step = len(data) // count
    return [data[i * step : (i + 1) * step] for i in range(count)], rate


def stage_roundtrip(rig, name="S2K-01"):
    slices, rate = source_slices()
    want = slices[int(name.split("-")[1]) - 1]
    t0 = time.monotonic()
    got = rig.audio(name)
    if not sc.check(got is not None and got[0] is not None, f"received {name!r} ({time.monotonic() - t0:.0f}s)"):
        return
    samples, framerate = got
    sc.say("INFO", f"{len(samples)} frames @ {framerate} (sent {len(want)} @ {rate})")
    sc.check(framerate == rate, "sample rate came back unchanged")
    sc.check(list(samples) == want, "audio is IDENTICAL to what was sent")
    if list(samples) != want:
        n = min(len(samples), len(want))
        diffs = [i for i in range(n) if samples[i] != want[i]]
        sc.say("INFO", f"length differs by {len(samples) - len(want)}; {len(diffs)} differing frames, first at {diffs[:5]}")


def stage_errors(rig):
    before = sc.snapshot(rig.bridge)
    names = [s["name"] for s in before.values()]
    worker = peb.BridgeWorker(rig.bridge)
    exported = []
    worker.program_exported.connect(lambda i, f: exported.append(f))
    worker._handle_export_program(names.index("MULTIBASE") if "MULTIBASE" in names else 0)
    pf = exported[0]
    good = apf.build_file(pf.program, pf.keygroups)
    tmp = os.path.join(os.path.dirname(sc.__file__), "..", "..")  # unused; files go to a temp dir below
    import tempfile

    d = tempfile.mkdtemp(prefix="s2000errors_")

    def write(fname, data):
        path = os.path.join(d, fname)
        open(path, "wb").write(data)
        return path

    # a .p1: every block cut to 150 bytes (a different family)
    p1 = b"".join(blk[:150] for blk in [pf.program, *pf.keygroups])
    cases = {
        ".p1 on an S2000": (write("x.p1", apf.build_file(pf.program[:150], [k[:150] for k in pf.keygroups])), "different block sizes|sampler type"),
        "not a program": (write("x.p3", b"hello, this is not an akai program file " * 20), "Couldn't read this file"),
        "truncated": (write("t.p3", good[:-37]), "truncated"),
    }
    sent_before = rig.window._worker.is_idle()
    for label, (path, expect) in cases.items():
        rig.dialogs.clear()
        QFileDialog.getOpenFileName = staticmethod(lambda *a, _p=path, **k: (_p, ""))
        rig.window._load_program_from_file()
        rig.spin(0.5)
        shown = " | ".join(f"{k}:{t}:{x[:90]!r}" for k, t, x in rig.dialogs)
        import re

        sc.check(any(k == "warning" and re.search(expect, x) for k, t, x in rig.dialogs), f"{label}: refused with a message ({shown[:160]})")
    # a program whose zone points at a sample that is not on the sampler: the confirmation must name it - answer No
    kg = bytearray(pf.keygroups[0])
    sname = p.lookup("SNAME1", "keygroup")
    kg[sname.offset : sname.offset + sname.size] = p.encode_field(sname, "NOSUCHSAMPLE")
    missing = apf.build_file(pf.program, [bytes(kg), *pf.keygroups[1:]])
    rig.dialogs.clear()
    path = write("m.p3", missing)
    QFileDialog.getOpenFileName = staticmethod(lambda *a, _p=path, **k: (_p, ""))
    # (the name is resident, so it asks for another - the stub prompt returns a fresh one)
    rig.window._prompt_akai_name = lambda *a, **k: "MISSING1"
    QMessageBox.question = staticmethod(lambda parent, title, text, *a, **k: (rig.dialogs.append(("question", title, text)), QMessageBox.StandardButton.No)[1])
    rig.window._load_program_from_file()
    rig.spin(0.5)
    texts = [x for k, t, x in rig.dialogs if k == "question"]
    sc.check(texts and "NOSUCHSAMPLE" in texts[0] and "Not on the sampler" in texts[0], f"missing sample named in the confirmation: {texts[0][:160]!r}" if texts else "no confirmation shown")
    rig.idle()
    after = sc.snapshot(rig.bridge)
    same = [s["name"] for s in after.values()] == names and all(
        after[i]["header"] == before[i]["header"] and after[i]["keygroups"] == before[i]["keygroups"] for i in before
    )
    sc.check(same, "NOTHING was written to the sampler by any of the refused loads")


def stage_edits(rig, name="S2K-04"):
    from ui import filter_sample_dialog  # noqa: F401

    class StubFilter:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return True

        highpass_enabled = staticmethod(lambda: False)
        highpass_cutoff_hz = staticmethod(lambda: 100.0)
        highpass_slope_db_per_octave = staticmethod(lambda: 12)
        lowpass_enabled = staticmethod(lambda: True)
        lowpass_cutoff_hz = staticmethod(lambda: 3000.0)
        lowpass_slope_db_per_octave = staticmethod(lambda: 12)

    pew.FilterSampleDialog = StubFilter
    window = rig.window
    fields = ("SPTYPE", "SPITCH", "SHLTO", "STUNO", "SSTART", "SMPEND", "LOOPAT1", "LLNGTH1")

    def run(kind, confirm, transform, markers=None):
        idx = rig.select(name)
        window._sample_waveform_cache.pop(idx, None)
        window._load_sample_waveform()
        rig.idle()
        entry = window._sample_waveform_cache.get(idx)
        if not sc.check(entry and entry["samples"] is not None, f"[{kind}] audio of {name!r} loaded ({len(entry['samples']) if entry and entry['samples'] else 0} frames)"):
            return False
        if markers:
            for k, v in markers.items():
                window.waveform_view.set_marker(k, v)
        m = window.waveform_view.markers_with_loop_in_range()
        pre = rig.header(name, fields)
        want, ns, nls, nle, ne = transform(entry["samples"], m["start"], m["loop_start"], m["loop_end"], m["end"])
        count_before = len(rig.bridge.sample_list())
        t0 = time.monotonic()
        confirm()
        rig.idle()
        sc.say("INFO", f"[{kind}] finished in {time.monotonic() - t0:.0f}s; status: {window.status_bar.currentMessage()!r}")
        names_after = rig.bridge.sample_list()
        sc.check(names_after.count(name) == 1 and len(names_after) == count_before and not any(n.endswith("-TMP") for n in names_after),
                 f"[{kind}] exactly one {name!r}, same sample count ({len(names_after)}), no temp sample left")
        got = rig.audio(name)
        ok_audio = got is not None and got[0] is not None and list(got[0]) == list(want)
        sc.check(ok_audio, f"[{kind}] audio read back == computed result ({len(want)} frames)")
        if got and got[0] is not None and list(got[0]) != list(want):
            sc.say("INFO", f"   got {len(got[0])} frames, wanted {len(want)}")
        post = rig.header(name, fields)
        sc.say("INFO", f"[{kind}] header before {pre}")
        sc.say("INFO", f"[{kind}] header after  {post}")
        for f in ("SPTYPE", "SPITCH", "SHLTO", "STUNO"):
            sc.check(post[f] == pre[f], f"[{kind}] {f} kept ({post[f]})")
        sc.check((post["SSTART"], post["SMPEND"]) == (ns, ne), f"[{kind}] SSTART/SMPEND = new markers ({post['SSTART']}, {post['SMPEND']} vs {ns}, {ne})")
        return True

    run("fade", window._confirm_fade_sample, sample_editing.fade_in_out_samples, dict(start=800, end=6000))
    run("normalise", window._confirm_normalize_sample, sample_editing.normalize_samples if hasattr(sample_editing, "normalize_samples") else sample_editing.normalise_samples)
    flt = functools.partial(sample_editing.filter_samples, framerate=44100, highpass_enabled=False, highpass_cutoff_hz=100.0, highpass_slope_db_per_octave=12,
                            lowpass_enabled=True, lowpass_cutoff_hz=3000.0, lowpass_slope_db_per_octave=12)
    run("filter", window._confirm_filter_sample, flt)
    run("reverse", window._confirm_reverse_sample, sample_editing.reverse_samples)
    run("trim", window._confirm_trim_sample, sample_editing.trim_samples, dict(start=500, end=5500))


STAGES = {"roundtrip": stage_roundtrip, "errors": stage_errors, "edits": stage_edits}


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "roundtrip"
    rig = Rig()
    STAGES[stage](rig, *sys.argv[2:3]) if stage != "errors" else STAGES[stage](rig)
    rig.window._worker.stop()
    rig.window._worker.wait()
    print(f"\n{sc.RESULTS.count('PASS')} passed, {sc.RESULTS.count('FAIL')} failed", flush=True)
    os._exit(1 if "FAIL" in sc.RESULTS else 0)


if __name__ == "__main__":
    main()
