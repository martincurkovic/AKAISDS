"""
Standalone dev tool - NOT part of the app. One launcher for every demo sampler (fake units on fake ports, no hardware).

    uv run python tools/demo.py list
    uv run python tools/demo.py <sampler> [--editor] [--instant] [--smoke]

    s1000    the Akai Program Editor on core/demo_s1000.FakeS1000
    s3000    the Akai Program Editor on s3ked's DemoBridge (also: s2000)
    s950     the Transfer Dashboard on core/demo_s950.FakeS950 (Open Editor for the S950 editor)
    a4000    the Transfer Dashboard on core/demo_a4000.FakeA4000 (--editor goes straight to the Yamaha editor's Samples tab)
    ensoniq  the Transfer Dashboard on core/demo_ensoniq.FakeEnsoniq (EPS/ASR)
    asrx     the Transfer Dashboard on core/demo_asrx.FakeASRX

    --instant  no MIDI-speed emulation (also AKAISDS_DEMO_INSTANT=1); --smoke  headless self-check (QT_QPA_PLATFORM=offscreen).
Not every flag applies to every sampler: the launcher says so instead of ignoring one silently.
"""

import argparse
import importlib
import os
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(TOOLS, "..", "src")

# name -> (what it opens, the flags it understands)
SAMPLERS = {
    "s1000": ("Akai S1000 Program Editor", {"instant", "smoke"}),
    "s3000": ("Akai S2000/S3000 Program Editor", {"instant", "smoke"}),
    "s950": ("Akai S900/S950 Dashboard", {"instant", "smoke"}),
    "a4000": ("Yamaha A4000 Dashboard", {"editor", "instant", "smoke"}),
    "ensoniq": ("Ensoniq EPS/ASR Dashboard", {"smoke"}),
    "asrx": ("Ensoniq ASR-X Dashboard", {"smoke"}),
}
ALIASES = {"s2000": "s3000"}


def _run_akai_editor(model, instant, smoke):
    # connect() hands back the demo bridge for the model while this is set; the editor then also treats itself as demo (no sends)
    os.environ["AKAISDS_DEMO_SAMPLER"] = "1"
    if instant:
        os.environ["AKAISDS_DEMO_INSTANT"] = "1"
    sys.path.insert(0, SRC)
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from core import program_editor_bridge
    from ui import spinbox_drag, theme, wheel_guard
    from ui.program_editor_window import ProgramEditorWindow

    class _Controller:
        # the only parts of a SamplerController the editor reaches for with no Dashboard around
        sampler_model = model

        def is_transfer_busy(self):
            return False

        def cancel_transfer(self):
            pass

    class _Host:
        # closing the editor (or Window > Transfer Dashboard) returns to the Dashboard in the real app: here it ends the demo
        sampler_controller = _Controller()

        def show(self):
            QApplication.instance().quit()

    app = QApplication(sys.argv)
    theme.apply_to_app(app)
    wheel_guard.install(app)
    spinbox_drag.install(app)
    window = ProgramEditorWindow(_Host(), bridge=program_editor_bridge.connect(sampler_model=model))
    window.show()
    result = {"code": 0}
    if smoke:
        def _report():
            programs = window.program_list.count()
            print(f"smoke: {programs} programs listed")
            result["code"] = 0 if programs else 1
            window.close()  # (stops the bridge worker thread: quitting under it aborts the process)

        QTimer.singleShot(1500, _report)
    app.exec()
    sys.exit(result["code"])


def _run_tool(module_name, **options):
    sys.path.insert(0, TOOLS)
    importlib.import_module(module_name).main(**options)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Launch a demo sampler.", usage="demo.py {list,%s} [--editor] [--instant] [--smoke]" % ",".join(SAMPLERS))
    parser.add_argument("sampler", help="a sampler name, or 'list'")
    parser.add_argument("--editor", action="store_true")
    parser.add_argument("--instant", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)

    if args.sampler == "list":
        for name, (what, flags) in SAMPLERS.items():
            print(f"{name:8} {what}  [{' '.join('--' + f for f in sorted(flags))}]")
        return 0
    name = ALIASES.get(args.sampler, args.sampler)
    if name not in SAMPLERS:
        parser.error(f"unknown sampler {args.sampler!r} (try 'list')")
    what, flags = SAMPLERS[name]
    unsupported = [f for f in ("editor", "instant", "smoke") if getattr(args, f) and f not in flags]
    if unsupported:
        parser.error(f"--{unsupported[0]} doesn't apply to {name}")

    instant = args.instant or os.environ.get("AKAISDS_DEMO_INSTANT", "").lower() not in ("", "0", "false", "no", "off")
    if name == "s1000":
        _run_akai_editor("akai_s1000", instant, args.smoke)
    elif name == "s3000":
        _run_akai_editor("akai_s2000_s3000", instant, args.smoke)
    elif name == "s950":
        _run_tool("s950_demo", smoke=args.smoke, instant=instant)
    elif name == "a4000":
        _run_tool("a4000_demo", smoke=args.smoke, editor=args.editor, instant=instant)
    else:
        _run_tool(f"{name}_demo", smoke=args.smoke)
    return 0


if __name__ == "__main__":
    sys.exit(main())
