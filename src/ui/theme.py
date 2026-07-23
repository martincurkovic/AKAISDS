"""
Theme palettes and stylesheet rendering.

Two full palettes (DARK_PALETTE / LIGHT_PALETTE) live here as plain
dicts - the single source of truth for every color in the app.
style.qss.template holds the actual QSS RULES (selectors, properties,
layout) with ${key} placeholders standing in for colors; this module
fills those in at load time.

To add a new themed color:
    1. Use ${your_key} somewhere in style.qss.template
    2. Add "your_key" to BOTH palettes below
Forgetting either one raises a clear KeyError at startup rather than
silently rendering something wrong - much safer than hand-maintaining
two separate .qss files that can quietly drift apart over time.
"""

import os
from string import Template

DARK_PALETTE = {
    "bg": "#1e1e24",
    "bg_panel": "#202028",
    "bg_dialog": "#232330",
    "bg_statusbar": "#17171c",
    "bg_input": "#2a2a35",
    "bg_button": "#2d2d38",
    "bg_button_hover": "#383846",
    "bg_button_pressed": "#232330",
    "bg_disabled": "#26262e",
    "bg_selected": "#2f3f57",
    "text": "#e4e4ea",
    "text_bright": "#f0f0f4",
    "text_disabled": "#6a6a76",
    "text_statusbar": "#c8c8d0",
    "border": "#40404c",
    "border_hover": "#4f4f5e",
    "border_disabled": "#333340",
    "border_list": "#33333e",
    "border_item": "#2a2a34",
    "border_checkbox": "#55555f",
    "border_progress": "#3a3a44",
    "accent": "#4a90d9",
    "text_on_accent": "#ffffff",
}

LIGHT_PALETTE = {
    # soft lavender-tinted neutrals, echoing the dark theme's cool
    # violet-grey cast instead of going plain/neutral grey
    "bg": "#f5f3fa",
    "bg_panel": "#ffffff",
    "bg_dialog": "#faf8ff",
    "bg_statusbar": "#ece7f5",
    "bg_input": "#ffffff",
    "bg_button": "#eeeaf7",
    "bg_button_hover": "#e1d8f0",
    "bg_button_pressed": "#d2c5e9",
    "bg_disabled": "#eeedf2",
    "bg_selected": "#e3d8f7",
    "text": "#2a2438",
    "text_bright": "#191320",
    "text_disabled": "#9a92aa",
    "text_statusbar": "#5c5470",
    "border": "#d6cce8",
    "border_hover": "#b9a4d9",
    "border_disabled": "#e3ddee",
    "border_list": "#ddd0ee",
    "border_item": "#ece4f7",
    "border_checkbox": "#b0a0d0",
    "border_progress": "#ddd0ee",
    "accent": "#8b5cf6",  # a genuine violet, distinct from dark theme's
    # blue accent - ties into the same purple-ish
    # family as the dark theme's neutrals without
    # just being a flat grey/blue inversion
    "text_on_accent": "#ffffff",
}

_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "style.qss.template")
_ICONS_DIR = os.path.join(os.path.dirname(__file__), "icons")
_GENERATED_ICONS_DIR = os.path.join(_ICONS_DIR, "_generated")


def _icon_path(filename):
    return os.path.join(_ICONS_DIR, filename).replace(os.sep, "/")


def _write_colored_chevron(direction, color, filename):
    # generate a chevron svg with the color theme baked in and write it to disk
    # this is the arrow for the dropdown menus/combo boxes
    if direction == "down":
        path_d = "M2 4 L6 8 L10 4"
    else:  # "up"
        path_d = "M2 8 L6 4 L10 8"

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12">'
        f'<path d="{path_d}" stroke="{color}" stroke-width="1.6" '
        f'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )

    os.makedirs(_GENERATED_ICONS_DIR, exist_ok=True)
    path = os.path.join(_GENERATED_ICONS_DIR, filename)
    with open(path, "w") as f:
        f.write(svg)
    return path.replace(os.sep, "/")


def render_stylesheet(palette):
    """Render the QSS template with the given palette dict.

    Raises KeyError with a clear, actionable message if the template
    references a color that's missing from this palette - this is the
    thing that keeps the two themes from silently drifting apart.
    """
    values = dict(palette)
    values["checkmark_icon"] = _icon_path("checkmark.svg")
    values["down_arrow_icon"] = _write_colored_chevron(
        "down", palette["text"], "down_arrow.svg"
    )
    values["down_arrow_icon_disabled"] = _write_colored_chevron(
        "down", palette["text_disabled"], "down_arrow_disabled.svg"
    )
    values["up_arrow_icon"] = _write_colored_chevron(
        "up", palette["text"], "up_arrow.svg"
    )

    with open(_TEMPLATE_PATH, "r") as f:
        template = Template(f.read())
    try:
        return template.substitute(**values)
    except KeyError as e:
        raise KeyError(
            f"style.qss.template references ${{{e.args[0]}}}, which isn't in this "
            f"palette - add it to both DARK_PALETTE and LIGHT_PALETTE in ui/theme.py"
        ) from e
