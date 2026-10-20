# One-off utility to generate the background image shown in the macOS DMG
# window (an arrow from AKAISDS.app to the Applications shortcut, plus a
# "drag to install" instruction). The output is committed, so the build
# doesn't depend on this script or on any font being installed.
#
# Needs macOS: it uses the system Helvetica font and the built-in `tiffutil`
# to combine the 1x and 2x renders into one hi-DPI TIFF, which Finder picks
# the right half of automatically (sharp on Retina, correct on non-Retina).
#
# Run it by doing:
# uv run python assets/dmg/create_dmg_background.py
#
# Output: assets/dmg/dmg_background.tiff
# Do not rename it - scripts/dmg_settings.py refers to it by name.
#
# The window size and icon positions below must match
# scripts/dmg_settings.py - change them together.

import os
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "dmg_background.tiff")
FONT_PATH = "/System/Library/Fonts/Helvetica.ttc"

# window content size in points, and where the two icons' centres sit
WIDTH, HEIGHT = 660, 400
APP_ICON_X, APPLICATIONS_ICON_X, ICON_Y = 170, 490, 210

# the app's dark theme (DARK_PALETTE in src/ui/theme.py): bg, text and accent
BACKGROUND = (0x1E, 0x1E, 0x24)
TEXT_COLOR = (0xE4, 0xE4, 0xEA)
ARROW_COLOR = (0x4A, 0x90, 0xD9)

# Finder draws the icon names in near-black whatever the window looks like and a script can't change that, so each name
# gets a light tag behind it (centred where Finder puts the name: 84pt below the icon's centre) to stay readable on the dark
LABEL_PLATE = (0xE4, 0xE4, 0xEA)
LABEL_Y, LABEL_HALF_WIDTH, LABEL_HALF_HEIGHT = 294, 62, 13


def draw_at(scale):
    img = Image.new("RGB", (WIDTH * scale, HEIGHT * scale), BACKGROUND)
    draw = ImageDraw.Draw(img)

    title_font = ImageFont.truetype(FONT_PATH, 26 * scale)
    draw.text(
        (WIDTH * scale // 2, 55 * scale),
        "Drag AKAISDS to the Applications folder",
        fill=TEXT_COLOR,
        font=title_font,
        anchor="mm",
    )

    # arrow centred between the two icons (128pt icons: edges at x=106..234
    # and 426..554), leaving a gap on each side
    arrow_start, arrow_tip = 255 * scale, 405 * scale
    head_len, head_half = 38 * scale, 24 * scale
    y = ICON_Y * scale
    draw.line(
        [(arrow_start, y), (arrow_tip - head_len, y)], fill=ARROW_COLOR, width=8 * scale
    )
    draw.polygon(
        [
            (arrow_tip - head_len, y - head_half),
            (arrow_tip, y),
            (arrow_tip - head_len, y + head_half),
        ],
        fill=ARROW_COLOR,
    )

    for x in (APP_ICON_X, APPLICATIONS_ICON_X):
        draw.rounded_rectangle(
            [
                (x - LABEL_HALF_WIDTH) * scale,
                (LABEL_Y - LABEL_HALF_HEIGHT) * scale,
                (x + LABEL_HALF_WIDTH) * scale,
                (LABEL_Y + LABEL_HALF_HEIGHT) * scale,
            ],
            radius=LABEL_HALF_HEIGHT * scale,
            fill=LABEL_PLATE,
        )
    return img


# Pillow doesn't anti-alias lines/polygons and hints small text harshly, so
# drawing straight at 1x/2x gives jagged arrow edges and soft-looking text.
# Instead draw once at 4x the 1x size (2x the 2x size) and downscale with a
# high-quality filter to get smooth edges at both resolutions.
SUPERSAMPLE = 4


def render(scale):
    big = draw_at(SUPERSAMPLE)
    return big.resize((WIDTH * scale, HEIGHT * scale), Image.Resampling.LANCZOS)


with tempfile.TemporaryDirectory() as tmp:
    one_x = os.path.join(tmp, "bg.tiff")
    two_x = os.path.join(tmp, "bg@2x.tiff")
    # Finder treats the second image as the 2x rep because it is exactly
    # twice the pixel size - set the DPI so tiffutil/Finder agree on that
    render(1).save(one_x, dpi=(72, 72))
    render(2).save(two_x, dpi=(144, 144))
    subprocess.run(
        ["tiffutil", "-cathidpicheck", one_x, two_x, "-out", OUTPUT], check=True
    )

print(f"Wrote {OUTPUT}")
