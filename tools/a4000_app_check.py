"""
Standalone dev tool - NOT part of the app. WRITES to a Yamaha A4000/A5000 (RAM only): drives the app's OWN code paths (the Dashboard's
native sample send, the Samples tab's edits, the Slice Editor's export, YamahaSession's link/write calls) headlessly against a real unit
and checks what really landed. Throwaway objects are named "T-...". Run only on a unit with nothing of value in it, with the app CLOSED.

    QT_QPA_PLATFORM=offscreen uv run python tools/a4000_app_check.py <stage> [...]

Stages (each prints PASS/FAIL/INFO lines): load, stereo-markers, edits, slices ... (see STAGES at the bottom).
"""

import array
import math
import os
import sys
import tempfile
import time
import wave

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from controller.sampler_controller import SamplerController
from core import app_config, midi_manager as mm
from core import yamaha_edit as ye
from core import yamaha_params as yp
from core import yamaha_sysex as ysx
from core import yamaha_wave as yw
from ui.yamaha_sample_edit import YamahaSampleEditor
from ui.yamaha_samples_tab import YamahaSamplesTab
from ui.yamaha_writer import WriteCoordinator

KEYS = (
    "wave_start_address", "wave_length", "wave_end_address", "loop_start_address", "loop_length", "loop_end_address", "loop_mode",
    "sampling_frequency_l", "sampling_frequency_r", "original_key_l", "key_range_low", "key_range_high", "coarse_tune", "fine_tune_l",
)
RESULTS = []


def say(kind, text):
    RESULTS.append((kind, text))
    print(f"{kind:5} {text}", flush=True)


def check(cond, text):
    say("PASS" if cond else "FAIL", text)
    return bool(cond)


def tone(n, rate, freq, amp=12000):
    return [round(amp * math.sin(2 * math.pi * freq * i / rate) * min(1, (n - i) / 300, i / 40 + 0.02)) for i in range(n)]


class Bench:
    def __init__(self):
        self.app = QApplication.instance() or QApplication([])
        self.midi = mm.MidiManager()
        in_name, out_name = app_config.get_saved_ports()
        self.midi.open_input(in_name)
        self.midi.open_output(out_name)
        self.controller = SamplerController(self.midi)
        self.controller.set_device_type("yamaha_a4000")
        self.session = self.controller.yamaha_session()
        self.parent = QWidget()
        self.writer = WriteCoordinator(self.session, self.parent)
        self.cache = {}
        self.tab = YamahaSamplesTab(self.controller, self.session, self.cache, writer=self.writer)
        self.editor = YamahaSampleEditor(self.tab)
        self.messages = []
        self.tab.status_message.connect(self.messages.append)
        self.writer.message.connect(self.messages.append)
        self.controller.status_changed.connect(self.messages.append)
        # every confirmation box answers Yes (nobody is here to click it)
        self.editor._confirm = lambda title, text: (say("INFO", f"confirm: {title}"), True)[1]
        QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
        say("INFO", f"ports {in_name} / {out_name}")

    def wait_for(self, cond, timeout=120.0):
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(25)
        timer.timeout.connect(lambda: loop.quit() if cond() else None)
        timer.start()
        QTimer.singleShot(int(timeout * 1000), loop.quit)
        loop.exec()
        timer.stop()
        return cond()

    def call(self, start, timeout=120.0):
        """start(callback) -> the callback's single argument (None on timeout)."""
        got = []
        loop = QEventLoop()
        start(lambda r=None: (got.append(r), loop.quit()))
        QTimer.singleShot(int(timeout * 1000), loop.quit)
        loop.exec()
        return got[0] if got else None

    def objects(self):
        entries = self.call(lambda cb: self.session.request_object_list(cb))
        return [(e.kind, e.name) for e in entries] if entries is not None else None

    def samples(self):
        return [n for k, n in self.objects() or [] if k == "sample"]

    def sp(self, name):
        d = self.call(lambda cb: self.session.request_bulk("SP", name, cb))
        return None if d is None else bytes(d.data)

    def pg(self, number):
        d = self.call(lambda cb: self.session.request_bulk("PG", ysx.program_object_name(number), cb))
        return None if d is None else bytes(d.data)

    def params(self, name):
        data = self.sp(name)
        if data is None:
            return None
        out = {k: yp.extract(yp.get("sample", k), data) for k in KEYS}
        out["stereo"] = yp.is_stereo(data)
        out["linked"] = yp.linked_programs(data)
        return out

    def wave_frames(self, wave_name):
        return self.call(lambda cb: self.session.request_wave(wave_name, cb), timeout=300)

    def sample_waves(self, name):
        data = self.sp(name)
        left = bytes(data[yp.WAVE_NAME_L_OFFSET : yp.WAVE_NAME_L_OFFSET + 16]).decode("ascii", "replace").strip("\x00 ")
        right = bytes(data[yp.WAVE_NAME_R_OFFSET : yp.WAVE_NAME_R_OFFSET + 16]).decode("ascii", "replace").strip("\x00 ")
        return left, right

    def send(self, entries, timeout=600.0):
        done = []
        self.controller.transfer_finished.connect(done.append)
        ok_started = self.controller.send_file_queue(entries)
        if not ok_started:
            say("FAIL", f"send_file_queue refused: {self.messages[-1:]}")
            return None
        self.wait_for(lambda: bool(done), timeout)
        self.controller.transfer_finished.disconnect(done.append)
        return done[0] if done else None

    def load(self, name, channels, rate, params=None):
        """Load WAV audio as a new sample through the Dashboard's native path. -> the name it really got (or None)."""
        path = os.path.join(tempfile.mkdtemp(prefix="a4000check_"), "x.wav")
        ye.write_wav(path, channels, rate)
        entry = {"filepath": path, "name": name, "sample_rate": rate, "mono": False, "bit_depth": 16}
        if params:
            entry["params"] = params
        t0 = time.monotonic()
        ok = self.send([entry])
        landed = self.controller.yamaha_loaded_names()
        say("INFO", f"load {name!r} ({len(channels)}ch x {len(channels[0])} @ {rate}): ok={ok} landed={landed} in {time.monotonic()-t0:.0f}s; {self.messages[-1:]}")
        return landed[-1] if ok and landed else None

    def select_and_load_audio(self, name, timeout=300):
        """What the user does: click the sample, double-click the waveform - the audio comes in."""
        self.tab.set_samples(self.samples())
        self.tab.set_connected(True) if hasattr(self.tab, "set_connected") else None
        self.tab.select_sample(name)
        self.wait_for(lambda: self.tab._selected == name and name in self.cache, 60)
        self.tab._load_audio(name) if hasattr(self.tab, "_load_audio") else None
        ok = self.wait_for(lambda: self.tab._audio_name == name and self.tab._audio_samples is not None, timeout)
        return ok


def stage_load(b):
    """Dashboard send through the app code: a looping mono sample and a stereo sample."""
    before = b.samples()
    say("INFO", f"{len(before)} samples on the unit")
    # a looping mono sample carrying loop params (what the edit/slice copies do)
    mono = tone(6000, 22050, 220)
    loop = {"loop_mode": 1, "loop_start_address": 1000, "loop_end_address": 5000, "loop_length": 4000, "original_key_l": 57, "coarse_tune": 0, "fine_tune_l": 17}
    name = b.load("T-LOOP", [mono], 22050, params=loop)
    if check(name is not None, "T-LOOP loaded"):
        p = b.params(name)
        say("INFO", f"T-LOOP params {p}")
        check(p["loop_mode"] == 1 and p["loop_start_address"] == 1000 and p["loop_end_address"] == 5000 and p["loop_length"] == 4000, "loop carried into the new sample")
        check(p["original_key_l"] == 57 and p["fine_tune_l"] == 17, "key and fine tune carried")
        lw, rw = b.sample_waves(name)
        check(b.wave_frames(lw) == mono, "wave audio read back identical to what was sent")
    # a real stereo sample
    left, right = tone(8000, 22050, 220), tone(8000, 22050, 330)
    name = b.load("T-STEREO", [left, right], 22050)
    if check(name is not None, "T-STEREO loaded"):
        p = b.params(name)
        say("INFO", f"T-STEREO params {p}")
        check(p["stereo"], "stereo detected (right wave name present)")
        lw, rw = b.sample_waves(name)
        say("INFO", f"waves {lw!r} {rw!r}")
        check(b.wave_frames(lw) == left, "left wave identical")
        check(b.wave_frames(rw) == right, "right wave identical")


def diff_offsets(a, b):
    return [i for i in range(min(len(a), len(b))) if a[i] != b[i]]


def row_at(offset):
    """The sample rows whose bytes cover a payload offset (to name what changed)."""
    out = []
    for key in yp.SAMPLE_ROW_KEYS if hasattr(yp, "SAMPLE_ROW_KEYS") else ():
        out.append(key)
    return out


def stage_twins(b, name="T-STEREO"):
    """Does the unit move a stereo sample's RIGHT-channel address bytes when only the left rows are written?"""
    before = b.sp(name)
    say("INFO", f"{name!r} stereo={yp.is_stereo(before)}")
    for key, value in (("loop_mode", 1), ("loop_start_address", 2000), ("loop_end_address", 6000), ("wave_start_address", 100), ("wave_end_address", 7000)):
        res = b.call(lambda cb: b.session.write_parameter(yp.get("sample", key), value, name, cb), 60)
        check(res is not None and res.ok, f"write {key}={value} -> {getattr(res, 'message', res)}")
        after = b.sp(name)
        changed = diff_offsets(before, after)
        say("INFO", f"after {key}: {len(changed)} payload bytes differ from the start: {changed}")
    after = b.sp(name)
    left = {}
    for key in ("wave_start_address", "wave_length", "wave_end_address", "loop_start_address", "loop_length", "loop_end_address"):
        left[key] = yp.extract(yp.get("sample", key), after)
    say("INFO", f"left rows now {left}")
    changed = diff_offsets(before, after)
    say("INFO", f"final changed offsets: {changed}")
    # restore by hand
    for key in ("loop_mode",):
        b.call(lambda cb: b.session.write_parameter(yp.get("sample", key), 0, name, cb), 60)
    return changed


def stage_twins_mono(b, name="T-LOOP"):
    """The same left-row writes on a MONO sample: offsets that change only on the stereo one are right-channel twins."""
    before = b.sp(name)
    for key, value in (("loop_mode", 1), ("loop_start_address", 2000), ("loop_end_address", 5000), ("wave_start_address", 100), ("wave_end_address", 5500)):
        res = b.call(lambda cb: b.session.write_parameter(yp.get("sample", key), value, name, cb), 60)
        check(res is not None and res.ok, f"write {key}={value}")
    after = b.sp(name)
    say("INFO", f"mono changed offsets: {diff_offsets(before, after)}")
    stereo = [173, 179, 183, 186, 187, 190, 191, 194, 195, 198, 199, 202, 203, 206, 207, 294, 295, 298, 299]
    only = [o for o in stereo if o not in diff_offsets(before, after)]
    say("INFO", f"changed on the stereo sample but NOT on the mono one (= right-channel twins): {only}")
    say("INFO", f"stereo SP bytes 170-210 now: {b.sp('T-STEREO')[170:210].hex(' ')}")
    say("INFO", f"mono   SP bytes 170-210 now: {after[170:210].hex(' ')}")
    say("INFO", f"stereo SP bytes 290-302 now: {b.sp('T-STEREO')[290:302].hex(' ')}")
    say("INFO", f"mono   SP bytes 290-302 now: {after[290:302].hex(' ')}")


def alive(b, wait=15.0):
    """Does the unit answer an object-list request within `wait` seconds?"""
    got = b.call(lambda cb: b.session.request_object_list(cb), wait)
    return got is not None


def link_step(b, program, sample, linked):
    t0 = time.monotonic()
    res = b.call(lambda cb: b.session.change_link(ysx.program_object_name(program), sample, linked, cb), 90)
    ok = bool(res and res.ok)
    say("PASS" if ok else "FAIL", f"{'link' if linked else 'unlink'} {sample!r} program {program}: {getattr(res, 'message', res)} ({time.monotonic()-t0:.0f}s)")
    time.sleep(2)
    a = alive(b)
    say("INFO" if a else "FAIL", f"unit answers afterwards: {a}")
    return ok and a


def stage_linktest(b, program=128, samples=("T-STEREO", "T-LOOP", "pulse 1")):
    """Which link raises the front-panel dialog that silences the unit? One at a time, checking the unit after each."""
    for s in samples:
        if not link_step(b, program, s, True):
            say("INFO", "stopping: the unit is not answering - press OK on it, then re-run")
            return
    for s in reversed(samples):
        if not link_step(b, program, s, False):
            say("INFO", "stopping: the unit is not answering")
            return


class _StubFilterDialog:
    """Stands in for FilterSampleDialog (nobody is here to click it): a low-pass at 2 kHz."""

    def __init__(self, *a, **k):
        pass

    def exec(self):
        return True

    highpass_enabled = staticmethod(lambda: False)
    highpass_cutoff_hz = staticmethod(lambda: 100.0)
    highpass_slope_db_per_octave = staticmethod(lambda: 12)
    lowpass_enabled = staticmethod(lambda: True)
    lowpass_cutoff_hz = staticmethod(lambda: 2000.0)
    lowpass_slope_db_per_octave = staticmethod(lambda: 12)


def prepare_tab(b, name):
    b.tab.set_samples(b.samples())
    b.tab.select_sample(name)
    b.wait_for(lambda: b.tab._selected == name and name in b.cache, 60)
    b.cache.pop(name, None)  # always the unit's current copy
    b.tab._on_selected(b.tab.sample_list_widget.currentItem(), None)
    b.wait_for(lambda: name in b.cache, 60)
    b.tab._audio_name, b.tab._audio_samples, b.tab._audio_samples_right = None, None, None  # else a LATE finish of the old load resets the markers
    b.tab._load_audio()
    return b.wait_for(lambda: b.tab._audio_name == name and b.tab._audio_samples is not None, 300)


def run_edit(b, source, kind, start=None, end=None):
    import ui.yamaha_sample_edit as se_mod

    se_mod.FilterSampleDialog = _StubFilterDialog
    if not check(prepare_tab(b, source), f"[{kind}] audio of {source!r} loaded"):
        return
    view = b.tab.waveform_view
    if start is not None:
        view.set_marker("start", start)
    if end is not None:
        view.set_marker("end", end)
    ctx = b.editor._context()
    if ctx is None:
        say("FAIL", f"[{kind}] no context: {b.messages[-1:]}")
        return
    opts = dict(highpass_enabled=False, highpass_cutoff_hz=100.0, highpass_slope_db_per_octave=12,
                lowpass_enabled=True, lowpass_cutoff_hz=2000.0, lowpass_slope_db_per_octave=12) if kind == "filter" else None
    exp_channels, exp_markers = ye.apply_edit(kind, ctx["channels"], ctx["markers"], framerate=ctx["rate"], filter_options=opts)
    exp_params = ye.params_for_copy(ctx["data"], exp_markers)
    src_params = b.params(source)
    say("INFO", f"[{kind}] {source!r}: markers {ctx['markers']} mode {src_params['loop_mode']} stereo {src_params['stereo']} -> expect markers {exp_markers}, {len(exp_channels[0])} frames")
    changed = []
    b.tab.samples_changed.connect(changed.append)
    t0 = time.monotonic()
    getattr(b.editor, kind)()
    if not b.wait_for(lambda: bool(changed), 900):
        say("FAIL", f"[{kind}] never finished: {b.messages[-3:]}")
        return
    b.tab.samples_changed.disconnect(changed.append)
    new = changed[0]
    say("INFO", f"[{kind}] finished in {time.monotonic()-t0:.0f}s -> {new!r}; {b.messages[-2:]}")
    if not check(new, f"[{kind}] a new sample was created"):
        return
    got = b.params(new)
    say("INFO", f"[{kind}] copy params {got}")
    check(got["stereo"] == src_params["stereo"], f"[{kind}] stereo-ness kept ({got['stereo']})")
    for key in ("loop_mode", "wave_start_address", "wave_end_address", "wave_length", "loop_start_address", "loop_end_address", "loop_length"):
        check(got[key] == exp_params[key], f"[{kind}] {key}: {got[key]} (expected {exp_params[key]})")
    for key in ("original_key_l", "coarse_tune", "fine_tune_l"):
        check(got[key] == src_params[key], f"[{kind}] {key} carried: {got[key]} (source {src_params[key]})")
    check(got["sampling_frequency_l"] == src_params["sampling_frequency_l"], f"[{kind}] rate kept ({got['sampling_frequency_l']})")
    lw, rw = b.sample_waves(new)
    check(b.wave_frames(lw) == exp_channels[0], f"[{kind}] left audio identical to the computed edit")
    if len(exp_channels) > 1:
        check(bool(rw) and b.wave_frames(rw) == exp_channels[1], f"[{kind}] right audio identical to the computed edit")


def stage_edits(b):
    # put both sources in a known, looping, valid state first
    for name, writes in (("T-LOOP", (("loop_mode", 1),)), ("T-STEREO", (("loop_mode", 1),))):
        for key, value in writes:
            b.call(lambda cb: b.session.write_parameter(yp.get("sample", key), value, name, cb), 60)
        say("INFO", f"{name}: {b.params(name)}")
    for kind, kw in (("reverse", {}), ("normalise", {}), ("trim", dict(start=400, end=4800)), ("fade", dict(start=600, end=4500)), ("filter", {})):
        run_edit(b, "T-LOOP", kind, **kw)
    for kind, kw in (("reverse", {}), ("normalise", {}), ("trim", dict(start=400, end=6500)), ("fade", dict(start=600, end=6000)), ("filter", {})):
        run_edit(b, "T-STEREO", kind, **kw)


def stage_markers_stick(b, name="T-LOOP"):
    """Do markers set on the waveform view stay put while the event loop runs afterwards (a late reset would corrupt an edit)?"""
    check(prepare_tab(b, name), "audio loaded")
    view = b.tab.waveform_view
    say("INFO", f"markers right after the load: {view.markers()}")
    view.set_marker("start", 400)
    view.set_marker("end", 4800)
    set_to = view.markers()
    say("INFO", f"after set_marker: {set_to}")
    for secs in (0.5, 1, 2, 4, 8):
        b.wait_for(lambda: False, secs)
        say("PASS" if view.markers() == set_to else "FAIL", f"after +{secs}s of event loop: {view.markers()}")
    src = b.params(name)  # a unit round trip
    say("PASS" if view.markers() == set_to else "FAIL", f"after reading the sample from the unit: {view.markers()}")
    say("INFO", f"unit holds start={src['wave_start_address']} end={src['wave_end_address']}")


def stage_edits_mono_tf(b):
    for kind, kw in (("trim", dict(start=400, end=4800)), ("fade", dict(start=600, end=4500))):
        run_edit(b, "T-LOOP", kind, **kw)


def program_count(b, number):
    d = b.pg(number)
    return None if d is None else yp.extract(yp.get("program", "assigned_samples"), d)


def run_slices(b, source, count, program, label):
    """The Slice Editor's own export + 'fill a program' callbacks, driven directly (no dialog to click)."""
    import types

    if not check(prepare_tab(b, source), f"[{label}] audio of {source!r} loaded"):
        return
    ctx = b.editor._context()
    channels = ctx["channels"]
    frames = len(channels[0])
    step = frames // count
    names = [f"T-{label}-{i + 1:02d}" for i in range(count)]
    slices = [channels[0][i * step : (i + 1) * step] for i in range(count)]
    extra = [[ch[i * step : (i + 1) * step] for i in range(count)] for ch in channels[1:]]
    g = lambda key: yp.extract(yp.get("sample", key), ctx["data"])  # noqa: E731
    b.editor._slice_params = {k: g(k) for k in ("original_key_l", "original_key_r", "coarse_tune", "fine_tune_l", "fine_tune_r")}
    b.editor._dialog = types.SimpleNamespace(create_program_checkbox=types.SimpleNamespace(isChecked=lambda: program is not None))
    b.editor._free_programs = [(program, f"{program:03d}")] if program else []
    say("INFO", f"[{label}] {count} slices of {step} frames, {len(channels)}ch, program {program}; programs before: {program_count(b, program) if program else '-'} samples")
    t0 = time.monotonic()
    ok, message = b.editor._export_slices(names, slices, ctx["rate"], 16, ctx["rate"], 60, 0, 0, lambda *a: None, lambda t: None,
                                          extra_slices=extra or None)
    say("PASS" if ok else "FAIL", f"[{label}] export: {message} ({time.monotonic()-t0:.0f}s)")
    on_unit = [n for n in names if n in b.samples()]
    check(len(on_unit) == count, f"[{label}] {len(on_unit)}/{count} slice samples are on the unit")
    if program:
        t0 = time.monotonic()
        ok, message = b.editor._create_program(names, 0, "", lambda *a: None, lambda t: None)
        say("PASS" if ok else "FAIL", f"[{label}] fill program: {message} ({time.monotonic()-t0:.0f}s)")
        say("INFO", f"[{label}] program {program} now holds {program_count(b, program)} samples")
    bad = 0
    for i, n in enumerate(on_unit):
        p = b.params(n)
        want = 36 + i
        good = (p["key_range_low"], p["key_range_high"], p["original_key_l"], p["loop_mode"]) == (want, want, want, 4) if program else True
        good = good and p["stereo"] == (len(channels) > 1) and p["wave_length"] == step
        if program:
            good = good and p["linked"] == [program]
        if not good:
            bad += 1
            say("FAIL", f"[{label}] {n}: {p}")
    check(bad == 0, f"[{label}] every slice has its own key, one-shot, right length/stereo-ness{', and is linked' if program else ''}")


def stage_slices_a(b):
    free = [n for n in (127, 126, 125, 124) if program_count(b, n) == 0]
    say("INFO", f"empty programs among 124-127: {free}")
    if len(free) < 2:
        return
    run_slices(b, "T-STEREO", 8, free[0], "A")


def stage_fill_a(b):
    import types

    names = [f"T-A-{i + 1:02d}" for i in range(8)]
    say("INFO", f"alive: {alive(b, 20)}; slices on the unit: {[n for n in names if n in b.samples()]}")
    b.editor._dialog = types.SimpleNamespace(create_program_checkbox=types.SimpleNamespace(isChecked=lambda: True))
    b.editor._free_programs = [(127, "127")]
    say("INFO", f"program 127 holds {program_count(b, 127)}")
    ok, message = b.editor._create_program(names, 0, "", lambda *a: None, lambda t: None)
    say("PASS" if ok else "FAIL", f"fill program: {message}")
    say("INFO", f"program 127 now holds {program_count(b, 127)}")
    for i, n in enumerate(names):
        p = b.params(n)
        if p is None:
            say("FAIL", f"{n}: no answer")
            continue
        want = 36 + i
        check((p["key_range_low"], p["key_range_high"], p["original_key_l"], p["loop_mode"], p["linked"]) == (want, want, want, 4, [127]), f"{n}: {p['key_range_low']}-{p['key_range_high']} key {p['original_key_l']} mode {p['loop_mode']} linked {p['linked']} stereo {p['stereo']}")


def silence_after(b, label, limit=1200):
    """After a request got no answer: poll until the unit answers again; report how long that took."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        if alive(b, 6):
            say("INFO", f"{label}: the unit answered again after {time.monotonic() - t0:.0f}s")
            return time.monotonic() - t0
    say("FAIL", f"{label}: still silent after {limit}s")
    return None


def stage_link_after_load(b):
    """Does a link right after a load silence the unit? mono then stereo, one tiny sample each, linked at once."""
    for name, channels in (("T-E1", [tone(1500, 22050, 440)]), ("T-E2", [tone(1500, 22050, 440), tone(1500, 22050, 660)])):
        landed = b.load(name, channels, 22050)
        if not check(landed, f"{name} loaded"):
            return
        say("INFO", f"alive right after the load: {alive(b, 10)}; sample read: {b.params(landed) is not None}")
        t0 = time.monotonic()
        res = b.call(lambda cb: b.session.change_link(ysx.program_object_name(126), landed, True, cb), 60)
        say("INFO", f"{name}: link reply in {time.monotonic() - t0:.0f}s: ok={getattr(res, 'ok', None)} {getattr(res, 'message', res)}")
        a = alive(b, 10)
        say("INFO", f"{name}: alive after the link: {a}")
        if not a:
            say("INFO", "THE UNIT IS SILENT - if its display shows a dialog, press OK; waiting for it to answer")
            if silence_after(b, name) is None:
                return
        say("INFO", f"{name}: program 126 holds {program_count(b, 126)}")


def push_knob5(b, device=0):
    """The front-panel remote: push Knob 5 (the unit's OK). F0 43 1n 58 03 <id=18> 00 00 00 00 00 <64> F7 (id/value from the Ctrlr panel)."""
    b.midi.send_sysex(bytes([0x43, 0x10 + device, 0x58, 0x03, 18, 0, 0, 0, 0, 0, 64]))  # (send_sysex adds F0/F7)


def stage_remote_ok(b, name="T-E3", stereo=False):
    """Load, push Knob 5 over SysEx while the unit still answers, then link: is it silent?"""
    time.sleep(3)
    channels = [tone(1500, 22050, 440)] + ([tone(1500, 22050, 660)] if stereo else [])
    landed = b.load(name, channels, 22050)
    if not check(landed, f"{name} loaded"):
        return
    say("INFO", f"alive after the load: {alive(b, 10)}")
    push_knob5(b)
    time.sleep(1.5)
    say("INFO", f"alive after the Knob 5 push: {alive(b, 10)}")
    t0 = time.monotonic()
    res = b.call(lambda cb: b.session.change_link(ysx.program_object_name(126), landed, True, cb), 60)
    say("INFO", f"link reply in {time.monotonic() - t0:.0f}s: ok={getattr(res, 'ok', None)} {getattr(res, 'message', res)}")
    a = alive(b, 10)
    say("PASS" if a else "FAIL", f"alive after the link (with the remote OK sent first): {a}")
    if not a:
        say("INFO", "THE UNIT IS SILENT - press OK on it; waiting")
        silence_after(b, name)


def stage_remote_ok_more(b):
    """More evidence for the remote OK: a batch of 3 mono loads + ONE push + 3 links, then one stereo load + push + link."""
    time.sleep(3)
    paths = []
    entries = []
    for i in range(3):
        path = os.path.join(tempfile.mkdtemp(prefix="a4000check_"), "x.wav")
        ye.write_wav(path, [tone(1500, 22050, 300 + 50 * i)], 22050)
        entries.append({"filepath": path, "name": f"T-F{i + 1}", "sample_rate": 22050, "mono": False, "bit_depth": 16})
    ok = b.send(entries)
    landed = b.controller.yamaha_loaded_names()
    check(ok and len(landed) == 3, f"batch of 3 loaded: {landed}")
    push_knob5(b)
    time.sleep(1.5)
    for n in landed:
        res = b.call(lambda cb: b.session.change_link(ysx.program_object_name(125), n, True, cb), 60)
        check(res is not None and res.ok, f"link {n!r} after ONE push: {getattr(res, 'message', res)}")
        if not alive(b, 10):
            say("FAIL", "unit silent - press OK on it")
            silence_after(b, n)
            return
    stage_remote_ok(b, "T-F4", stereo=True)


def stage_slices_b(b):
    free = [n for n in range(120, 100, -1) if program_count(b, n) == 0][:1]
    say("INFO", f"empty program to fill: {free}")
    if free:
        run_slices(b, "T-LOOP", 6, free[0], "E")


def stage_slices_big(b):
    free = [n for n in (122, 121, 120) if program_count(b, n) == 0]
    say("INFO", f"empty programs: {free}")
    if free:
        run_slices(b, "T-LOOP", 40, free[0], "D")  # 40 slices of 150 frames


def identity_alive(b, wait=4.0):
    """Cheap, display-free probe: an identity request (device 7F) answered within `wait` seconds? Needs the session idle."""
    got = []
    loop = QEventLoop()

    def on_sysex(data):
        raw = bytes(data)
        if len(raw) > 4 and raw[0] == 0x7E and raw[2] == 0x06 and raw[3] == 0x02:
            got.append(raw)
            loop.quit()

    b.midi.sysex_received.connect(on_sysex)
    b.midi.send_sysex(bytes([0x7E, 0x7F, 0x06, 0x01]))
    QTimer.singleShot(int(wait * 1000), loop.quit)
    loop.exec()
    b.midi.sysex_received.disconnect(on_sysex)
    return bool(got)


def stage_busy_or_dialog(b, limit=1500):
    """Is the silence after a link the unit being BUSY (it recovers by itself) or a dialog waiting for OK? Hands off the unit."""
    landed = b.load("T-G1", [tone(1500, 22050, 500)], 22050)
    if not check(landed, "T-G1 loaded"):
        return
    say("INFO", f"identity right after the load: {identity_alive(b)}")
    t0 = time.monotonic()
    res = b.call(lambda cb: b.session.change_link(ysx.program_object_name(121), landed, True, cb), 70)
    say("INFO", f"link reply after {time.monotonic() - t0:.0f}s: ok={getattr(res, 'ok', None)}")
    t1 = time.monotonic()
    while time.monotonic() - t1 < limit:
        if identity_alive(b):
            say("INFO", f"the unit answered an identity request {time.monotonic() - t0:.0f}s after the link (nobody touched it)")
            break
        time.sleep(1)
    else:
        say("FAIL", f"still silent {limit}s after the link - needs a person")


def stage_fill_d(b):
    import types

    names = [f"T-D-{i + 1:02d}" for i in range(40)]
    b.editor._dialog = types.SimpleNamespace(create_program_checkbox=types.SimpleNamespace(isChecked=lambda: True))
    b.editor._free_programs = [(122, "122")]
    say("INFO", f"program 122 holds {program_count(b, 122)}; slices on the unit: {sum(1 for n in names if n in b.samples())}")
    t0 = time.monotonic()
    ok, message = b.editor._create_program(names, 0, "", lambda *a: None, lambda t: say("INFO", t))
    say("PASS" if ok else "FAIL", f"fill 40: {message} ({time.monotonic()-t0:.0f}s)")
    count = program_count(b, 122)
    check(count == 40, f"program 122 holds {count} samples")
    bad = 0
    for i, n in enumerate(names):
        p = b.params(n)
        want = 36 + i
        if not p or (p["key_range_low"], p["key_range_high"], p["original_key_l"], p["loop_mode"], p["linked"]) != (want, want, want, 4, [122]):
            bad += 1
            say("FAIL", f"{n}: {p}")
    check(bad == 0, "all 40 slices on their own key, one-shot, linked")
    # how many samples will a program take? keep linking other throwaways until the unit refuses or 99
    extra = [n for n in b.samples() if n.startswith("T-") and n not in names]
    linked = 40
    for n in extra:
        res = b.call(lambda cb: b.session.change_link(ysx.program_object_name(122), n, True, cb), 90)
        if not (res and res.ok):
            say("INFO", f"the unit refused/ignored the link after {linked} samples (next: {n!r}): {getattr(res, 'message', res)}")
            break
        linked += 1
    say("INFO", f"program 122 now holds {program_count(b, 122)} samples (linked {linked}; {len(extra)} extra candidates)")


STAGES = {"fill-d": stage_fill_d, "busy-or-dialog": stage_busy_or_dialog, "slices-big": stage_slices_big, "slices-b": stage_slices_b, "remote-ok-more": stage_remote_ok_more, "remote-ok": stage_remote_ok, "link-after-load": stage_link_after_load, "fill-a": stage_fill_a, "slices-a": stage_slices_a, "edits-mono-tf": stage_edits_mono_tf, "markers-stick": stage_markers_stick, "edits": stage_edits, "load": stage_load, "twins": stage_twins, "twins-mono": stage_twins_mono, "linktest": stage_linktest, "link-stereo": lambda b: (link_step(b, 128, "T-STEREO", True), None)[1], "unlink-stereo": lambda b: (link_step(b, 128, "T-STEREO", False), None)[1]}


def main():
    names = sys.argv[1:] or list(STAGES)
    b = Bench()
    for n in names:
        say("INFO", f"=== stage {n}")
        STAGES[n](b)
    fails = [t for k, t in RESULTS if k == "FAIL"]
    print(f"\n{sum(1 for k, _ in RESULTS if k == 'PASS')} passed, {len(fails)} failed")
    os._exit(1 if fails else 0)


if __name__ == "__main__":
    main()
