"""
Dev tool - NOT part of the app.

Regenerates the pictures in the manual (src/ui/help/screenshots/) by running the REAL windows offscreen against the demo
fakes (no MIDI hardware, no real files touched), puts each one into the state its picture needs and saves it as a PNG.
Run it again whenever the UI changes, so the manual doesn't drift behind it.

    uv run python tools/make_screenshots.py                     # make the pictures that don't exist yet
    uv run python tools/make_screenshots.py --list              # every picture it knows, and whether it exists
    uv run python tools/make_screenshots.py --only editor-global-page main-window-dark
    uv run python tools/make_screenshots.py --only main-window-dark --overwrite
    uv run python tools/make_screenshots.py --overwrite         # redo every one (replaces hand-captured ones!)
    uv run python tools/make_screenshots.py --out /tmp/shots    # somewhere else (then nothing is "skipped")

An existing file is never replaced unless you say --overwrite: the hand-captured screenshots in the manual have the macOS title
bar and shadow, and these are the window CONTENTS only (no frame), at twice their on-screen size (QT_SCALE_FACTOR=2, which is
what ui/quickstart_dialog.py's _SCREENSHOT_PIXEL_RATIO expects).

The pictures use invented sounds and demo sampler data, never anyone's real samples or settings: this tool points the app's
config, log and backup folders at a throwaway directory first (same as tests/conftest.py).

Adding a picture: write a function decorated with @shot("name", "what it shows") below. It gets a Context, builds and shows
the window (ctx.show), and returns the widget to photograph (or a QPixmap).
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import screenshot_kit as kit  # (sets up the isolated environment: keep this before the app imports below)
from screenshot_kit import SHOTS, Context, theme
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

def run(names: list[str], out: Path, overwrite: bool) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    out.mkdir(parents=True, exist_ok=True)
    made = skipped = failed = 0
    for name in names:
        s = SHOTS[name]
        target = out / f"{name}.png"
        if target.exists() and not overwrite:
            print(f"skip  {name}.png (exists; --overwrite to replace)")
            skipped += 1
            continue
        theme.apply_to_app(app, s.scheme)
        ctx = Context(app, Path(tempfile.mkdtemp(dir=kit._SCRATCH, prefix=name + "-")))
        try:
            result = s.recipe(ctx)
            pixmap = result if isinstance(result, QPixmap) else result.grab()
            if pixmap.isNull() or pixmap.width() < 20:
                raise RuntimeError("the window came out empty")
            pixmap.save(str(target))
            print(f"saved {name}.png  {pixmap.width()}x{pixmap.height()}")
            made += 1
        except Exception as e:
            import traceback

            traceback.print_exc()
            print(f"FAIL  {name}: {e}")
            failed += 1
        finally:
            ctx.cleanup()
    print(f"\n{made} saved, {skipped} skipped, {failed} failed  ->  {out}")
    return 1 if failed else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate the manual's screenshots offscreen from the demo fakes.")
    parser.add_argument("--only", nargs="+", metavar="NAME", help="just these pictures (names as in --list, with or without .png)")
    parser.add_argument("--overwrite", action="store_true", help="replace pictures that already exist (default: leave them)")
    parser.add_argument("--out", type=Path, default=kit.DEFAULT_OUT, help=f"where to write them (default {kit.DEFAULT_OUT.relative_to(kit.REPO)})")
    parser.add_argument("--list", action="store_true", help="list the pictures and whether each exists, then stop")
    args = parser.parse_args(argv)

    import screenshot_recipes  # noqa: F401  (registers the @shot functions)

    if args.list:
        for name, s in SHOTS.items():
            mark = "exists" if (args.out / f"{name}.png").exists() else "missing"
            print(f"{name:34} {mark:8} {s.description}")
        return 0
    names = list(SHOTS)
    if args.only:
        wanted = [n.removesuffix(".png") for n in args.only]
        unknown = [n for n in wanted if n not in SHOTS]
        if unknown:
            print("unknown picture(s):", ", ".join(unknown), "\nknown:", ", ".join(SHOTS))
            return 2
        names = wanted
    try:
        return run(names, args.out, args.overwrite)
    finally:
        shutil.rmtree(kit._SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
