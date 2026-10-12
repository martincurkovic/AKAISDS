"""Draws the MIDI cabling diagrams used by the manual (quickstart.md) and the Settings > Troubleshooting cards.

    uv run python tools/make_diagrams.py            # writes src/ui/help/diagrams/*.png
    uv run python tools/make_diagrams.py --svg      # also keeps the .svg sources next to them

Two pictures: how to plug in the loopback cable and how to connect a sampler. Deliberately plain (no captions, step
numbers or side labels) - the text next to each picture explains it. Needs `rsvg-convert` (brew install librsvg).
Each picture carries its own light background so it reads the same in light and dark mode. PNGs are rendered at 2x:
the Quick Start dialog and the Settings window both treat them as Retina images and show them at half the pixel size.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "ui" / "help" / "diagrams"

# shown at this fraction of the 720-unit drawing below (so 0.44 -> a picture about 320 px wide)
SCALE = 0.44
LABEL_SIZE = 28  # in drawing units, i.e. before SCALE
TITLE_SIZE = 23

ORANGE = "#e8a317"
BLUE = "#3b82d6"
PANEL = "#2b2f36"

DEFS = """
  <defs>
    <g id="socket">
      <circle r="27" fill="#101216"/>
      <circle r="21" fill="#d9dce2"/>
      <rect x="-4" y="-24" width="8" height="7" rx="1.5" fill="#101216"/>
      <g fill="#101216">
        <circle cx="-14" cy="3" r="2.6"/><circle cx="-7" cy="10" r="2.6"/><circle cx="0" cy="12" r="2.6"/>
        <circle cx="7" cy="10" r="2.6"/><circle cx="14" cy="3" r="2.6"/>
      </g>
    </g>
  </defs>"""


def panel(y, height, title, socket_labels, label_y):
    out = [
        f'<rect x="70" y="{y}" width="580" height="{height}" rx="10" fill="{PANEL}"/>',
        f'<text x="96" y="{y + 28}" font-size="{TITLE_SIZE}" font-weight="600" fill="#aeb4bf" letter-spacing="1">{title}</text>',
    ]
    for x, text in socket_labels:
        out.append(
            f'<text x="{x}" y="{label_y}" font-size="{LABEL_SIZE}" font-weight="700" fill="#ffffff" text-anchor="middle">{text}</text>'
        )
    return out


def sockets(y, xs):
    return [f'<use href="#socket" x="{x}" y="{y}"/>' for x in xs]


def plug(x, y, colour):
    pins = "".join(
        f'<circle cx="{x + dx}" cy="{y + dy}" r="2.6"/>'
        for dx, dy in ((-14, 3), (-7, 10), (0, 12), (7, 10), (14, 3))
    )
    return [
        f'<circle cx="{x}" cy="{y}" r="30" fill="#4b515c" stroke="{colour}" stroke-width="4"/>',
        f'<g fill="#cfd3da">{pins}</g>',
    ]


def cable(path, colour):
    return f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="9" stroke-linecap="round"/>'


def arrow(x, y, colour, up):
    tip, base = (y - 11, y + 8) if up else (y + 11, y - 8)
    return (
        f'<path d="M{x} {tip} L{x - 11} {base} L{x + 11} {base} Z" fill="{colour}" '
        f'stroke="#f5f3ee" stroke-width="2" stroke-linejoin="round"/>'
    )


def wrap(height, title, desc, body):
    w, h = round(720 * SCALE), round(height * SCALE)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'font-family="Helvetica, Arial, sans-serif">\n  <title>{title}</title>\n  <desc>{desc}</desc>{DEFS}\n'
        f'  <g transform="scale({SCALE})">\n  <rect width="720" height="{height}" rx="{14 / SCALE:.0f}" fill="#f5f3ee"/>\n  '
        + "\n  ".join(body)
        + "\n  </g>\n</svg>\n"
    )


def loopback():
    body = panel(20, 140, "INTERFACE", [(250, "IN"), (470, "OUT")], 82)
    body += sockets(122, (250, 470))
    body.append(cable("M470 122 V178 Q470 212 436 212 H284 Q250 212 250 178 V122", ORANGE))
    body += plug(250, 122, ORANGE) + plug(470, 122, ORANGE)
    return wrap(236, "MIDI loopback cable", "One MIDI cable from the interface's MIDI OUT to its own MIDI IN.", body)


def sampler():
    body = panel(20, 140, "INTERFACE", [(250, "IN"), (470, "OUT")], 82)
    body += panel(214, 152, "SAMPLER", [(250, "OUT"), (470, "IN")], 354)
    body += sockets(122, (250, 470)) + sockets(292, (250, 470))
    # interface OUT -> sampler IN (right), sampler OUT -> interface IN (left)
    body.append(cable("M470 122 V292", ORANGE))
    body.append(cable("M250 292 V122", BLUE))
    body.append(arrow(470, 187, ORANGE, up=False))
    body.append(arrow(250, 187, BLUE, up=True))
    body += plug(250, 122, BLUE) + plug(470, 122, ORANGE) + plug(250, 292, BLUE) + plug(470, 292, ORANGE)
    return wrap(
        386, "Connecting a sampler",
        "Interface MIDI OUT to sampler MIDI IN, and sampler MIDI OUT to interface MIDI IN.", body,
    )


PICTURES = {"midi-loopback-cable": loopback, "midi-sampler-cable": sampler}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--svg", action="store_true", help="keep the .svg sources next to the PNGs")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, draw in PICTURES.items():
            svg_path = (args.out if args.svg else Path(tmp)) / f"{name}.svg"
            svg_path.write_text(draw(), encoding="utf-8")
            png_path = args.out / f"{name}.png"
            try:
                subprocess.run(["rsvg-convert", "-z", "2", str(svg_path), "-o", str(png_path)], check=True)
            except FileNotFoundError:
                sys.exit("rsvg-convert not found (brew install librsvg)")
            print(f"saved {png_path.name}")


if __name__ == "__main__":
    main()
