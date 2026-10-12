"""
Standalone dev tool - NOT part of the app. WRITES to a Yamaha A4000/A5000 (RAM only): proves the Samples tab's EDITABLE MARKERS and
CLICK-TO-PREVIEW against a real unit, through the app's own widget (YamahaSamplesTab) + write path (WriteCoordinator -> YamahaSession).

On a throwaway USER sample (default "MIDI 00102"; a built-in waveform ignores end changes) it loads the audio, then drags each marker the
way the mouse would - one at a time and several at once, in loop mode and in no-loop mode - and after every drag reads the sample back
and compares it with what the drag asked for. At the end it puts everything back and diffs the dump with the snapshot taken first.
Finally (--preview) it clicks the waveform to play the sample for a second through the default audio output and clicks again to stop
(listen for it; with a stereo sample both channels play). Run only on a unit with nothing of value in it, with the app CLOSED.

    QT_QPA_PLATFORM=offscreen uv run python tools/a4000_marker_check.py ["MIDI 00102"] [--preview]
"""

import argparse
import os
import sys
import time

os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QWidget

from controller.sampler_controller import SamplerController
from core import app_config, midi_manager as mm
from core import yamaha_markers as ym
from core import yamaha_restore as yr
from core import yamaha_params as yp
from ui.yamaha_samples_tab import YamahaSamplesTab
from ui.yamaha_writer import WriteCoordinator


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sample", nargs="?", default="MIDI 00102")
    ap.add_argument("--preview", action="store_true", help="also play the sample through the computer's audio output")
    ap.add_argument("--base", help="start,loop_start,loop_end,end[,loop_mode] to put the sample back to (when an earlier run was interrupted)")
    args = ap.parse_args()
    name = args.sample

    app = QApplication([])
    midi = mm.MidiManager()
    in_name, out_name = app_config.get_saved_ports()
    midi.open_input(in_name)
    midi.open_output(out_name)
    controller = SamplerController(midi)
    controller.set_device_type("yamaha_a4000")
    session = controller.yamaha_session()
    parent = QWidget()
    writer = WriteCoordinator(session, parent)
    cache = {}
    tab = YamahaSamplesTab(controller, session, cache, writer=writer)
    messages = []
    tab.status_message.connect(messages.append)
    writer.message.connect(messages.append)
    print(f"ports: {in_name} / {out_name}; sample {name!r}")

    def wait_for(cond, timeout=240.0):
        loop = QEventLoop()
        t = QTimer()
        t.setInterval(25)
        t.timeout.connect(lambda: loop.quit() if cond() else None)
        t.start()
        QTimer.singleShot(int(timeout * 1000), loop.quit)
        loop.exec()
        t.stop()
        return cond()

    def read_sp():
        got = []
        loop = QEventLoop()
        session.request_bulk("SP", name, lambda d: (got.append(d), loop.quit()))
        QTimer.singleShot(60_000, loop.quit)
        loop.exec()
        return got[0] if got else None

    def unit():
        return ym.read_markers(read_sp().data)

    def idle():
        return tab._marker_job is None and tab._marker_pending is None and not writer.busy

    ok = True

    def drag(*moves):
        """Drag one or more markers (name, frame) and let go - like the mouse: live moves, then one commit."""
        view = tab.waveform_view
        for marker, frame in moves:
            view.set_marker(marker, frame)
        m = view.markers()
        view.marker_committed.emit(moves[-1][0], m["start"], m["loop_start"], m["loop_end"], m["end"])
        wait_for(idle, 60)
        time.sleep(0.2)
        wait_for(idle, 60)

    def expect(label, want):
        nonlocal ok
        got = unit()
        good = got == want
        ok &= good
        print(f"  {'ok ' if good else 'BAD'} {label}: unit holds {got}" + ("" if good else f"   (wanted {want})"))
        shown = tab.waveform_view.markers()
        right = tab.waveform_view_right.markers() if not tab.waveform_view_right.isHidden() else shown
        if right != shown:
            ok = False
            print(f"  BAD the two channel views disagree: {shown} vs {right}")

    def set_mode(mode):
        got = []
        loop = QEventLoop()
        writer.edit(yp.get("sample", "loop_mode"), mode, name, None, lambda r: (got.append(r), loop.quit()))
        writer.flush()
        QTimer.singleShot(30_000, loop.quit)
        loop.exec()
        cache[name] = bytearray(read_sp().data)
        tab._after_cache_change(name, yp.get("sample", "loop_mode"))
        tab._show_waveform(name, cache[name])
        return got and got[0].ok

    snapshot = read_sp()
    if snapshot is None:
        raise SystemExit(f"{name!r}: no answer from the unit")
    base = ym.read_markers(snapshot.data)
    base_mode = yp.extract(yp.get("sample", "loop_mode"), snapshot.data)
    if args.base:
        v = [int(x) for x in args.base.split(",")]
        base, base_mode = ym.Markers(*v[:4]), (v[4] if len(v) > 4 else base_mode)
    print(f"snapshot: {base} loop_mode {base_mode}; stereo={yp.is_stereo(snapshot.data)}")

    tab.set_samples([name])
    tab.select_sample(name)
    wait_for(lambda: name in cache and tab._selected == name, 60)
    tab._load_audio()
    print("loading the audio ...")
    if not wait_for(lambda: tab._audio_name == name, 400):
        raise SystemExit("the audio didn't load")
    frames = len(tab._audio_samples)
    print(f"audio loaded: {frames} frames. Views: {tab.waveform_view.markers()}")
    if frames < 800:
        raise SystemExit("sample too short for this check (needs ~800 frames)")

    # a loop comfortably inside the wave, in a continuous-loop mode, so the four markers can all be exercised
    print("\n=== loop mode (continuous) ===")
    assert set_mode(1)
    end = ym.read_markers(cache[name]).end
    quarter = end // 4
    drag(("loop_start", quarter))
    expect("loop start", ym.Markers(base.start, quarter, ym.read_markers(cache[name]).loop_end, end))
    cur = unit()
    drag(("loop_end", cur.loop_start + quarter))
    cur2 = unit()
    expect("loop end", ym.Markers(cur.start, cur.loop_start, cur.loop_start + quarter + 1, cur.end))
    drag(("start", 50))
    expect("wave start", ym.Markers(50, cur2.loop_start, cur2.loop_end, cur2.end))
    cur3 = unit()
    drag(("end", cur3.end - 101))
    expect("wave end (pulled in)", ym.Markers(cur3.start, cur3.loop_start, cur3.loop_end, cur3.end - 100))
    drag(("end", frames - 1))
    cur4 = unit()
    expect("wave end (back out to the whole wave)", ym.Markers(cur4.start, cur4.loop_start, cur4.loop_end, frames))
    print("  -- several markers in one drag: pull the end in past the loop, so the loop has to come with it")
    drag(("end", cur4.loop_start + 20))
    got = unit()
    good = got.valid and got.end == cur4.loop_start + 21
    ok &= good
    print(f"  {'ok ' if good else 'BAD'} end past the loop: unit holds {got}")
    print("  -- the start moved above the loop start (the loop start has to give way first)")
    drag(("start", got.loop_start + 5))
    got = unit()
    ok &= got.valid
    print(f"  {'ok ' if got.valid else 'BAD'} start above loop: unit holds {got}")

    print("\n=== no-loop mode ===")
    assert set_mode(0)
    cur = unit()
    print(f"  loop values kept while nothing loops: {cur}")
    drag(("start", max(cur.start - 30, 0)))
    after = unit()
    good = after.start == max(cur.start - 30, 0)
    ok &= good
    print(f"  {'ok ' if good else 'BAD'} wave start: {after}")
    drag(("end", frames - 1))
    after = unit()
    good = after.end == frames and after.valid
    ok &= good
    print(f"  {'ok ' if good else 'BAD'} wave end out to the whole wave: {after}")

    if args.preview:
        print("\n=== preview ===")
        for mode in (base_mode if base_mode in (0, 1, 2, 3, 4, 5) else 0, 1):
            set_mode(mode)
            positions = []
            tab._preview.position_changed.connect(positions.append)
            tab._on_preview_requested()
            print(f"  clicked: playing={tab._preview.is_playing()} (mode {mode}); listen ...")
            wait_for(lambda: False, 1.2)
            moved = len(positions)
            if tab._preview.is_playing():  # a held loop; a short one-shot has already ended by itself
                tab._on_preview_requested()
            print(f"  after the second click: playing={tab._preview.is_playing()}; playhead moved {moved} times, last {positions[-1] if positions else None}")
            ok &= moved > 0 and not tab._preview.is_playing()
            tab._preview.position_changed.disconnect(positions.append)

    print("\n=== put it back ===")
    set_mode(1)  # the loop markers can only be placed while the loop is on
    for _ in range(4):  # a further go for anything a write moved
        if unit() == base:
            break
        drag(("end", base.end - 1), ("loop_end", base.loop_end - 1), ("loop_start", base.loop_start), ("start", base.start))
    if unit() != base:  # a drag can't reach every address the unit can hold (e.g. a loop start AT the wave end): write it directly
        for marker, address in ym.plan_marker_writes(unit(), base):
            got = []
            loop = QEventLoop()
            writer.edit(yp.get("sample", ym.ROW_FOR[marker]), address, name, None, lambda r: (got.append(r), loop.quit()))
            writer.flush()
            QTimer.singleShot(30_000, loop.quit)
            loop.exec()
    set_mode(base_mode)
    final = read_sp()
    residual = yr.residual_offsets(snapshot, final)
    print(f"  markers back to {unit()} (wanted {base}); bytes differing from the snapshot: {residual}")
    ok &= unit() == base and not residual
    print("  messages seen:", "; ".join(dict.fromkeys(messages)) or "(none)")
    print("\nMARKERS + PREVIEW BEHAVED" if ok else "\nSOMETHING DID NOT BEHAVE - see above")
    midi.close_input()
    midi.close_output()


if __name__ == "__main__":
    main()
