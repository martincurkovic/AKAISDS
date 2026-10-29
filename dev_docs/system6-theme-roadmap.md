# Macintosh System 6 theme for AKAISDS: roadmap

Status 2026-10-29 (evening): **skeleton and a first pass at the painted widgets are done and look right in offscreen pictures; nothing seen on a real screen, no build made, nothing committed** (see "Progress" at the end). Written when it was **not started**: Written after the same theme was built for two sibling apps in one day, so the plan is mostly a
list of what to copy and of the traps those two ports found. Everything marked *seen* was looked at in offscreen pictures on macOS
(`QT_QPA_PLATFORM=offscreen`, scale 1 and 2), and some of it on the owner's real screen; nothing was looked at on Windows or Linux.

Read first:

- `~/Development/jv-studio/dev_docs/system6-theme.md` (the first port: demo in a day, then a polish pass; the long list of what
  QSS cannot do).
- `~/Development/akaiutil-GUI/dev_docs/system6-theme.md` (the second port and the later fixes: scroll bars, spin boxes, message box
  icons, Markdown, selection, usage bar).
- The code to copy from is in those two repos (see "What to copy").

## What it is

A third theme, **Macintosh System 6** (View > Theme / Settings > Appearance): black and white plus greys, square corners, a
bitmap-style font (ChiKareGo2), inverted video for selection, dithered scroll bar tracks, boxed scroll arrows, X check boxes. The
only colour kept is what carries meaning (a warning orange, a critical red, a "connected" green). The native window frame, the macOS
menu bar and the OS file dialogs stay native: Qt cannot restyle them (the dropped "Phase B" in the jv-studio doc says what a
frameless System 6 window would cost: days, and per-OS testing).

It is a **homage, not affiliated with Apple**: no Apple fonts or artwork (Chicago itself is Apple's). The font is **ChiKareGo2** by
Giles Booth, CC BY: it must be credited (Help > Licences / `THIRD_PARTY_NOTICES.md`, and `ui/fonts/README.txt`).

## How AKAISDS compares (read before estimating)

AKAISDS has the same theme machinery as the other two (`ui/theme.py`: `DARK_PALETTE` / `LIGHT_PALETTE` dicts, `render_stylesheet`
over `ui/style.qss.template`, `current_palette()`, `notifier`, `CHOICES`), so the port is mostly mechanical. What is bigger:

| Thing | In AKAISDS | Why it matters |
|---|---|---|
| Dialogs and windows | dozens of modules in `src/ui/` (program editors, slice editor, sample dialogs, Yamaha tabs) | each one is a place for a clipped label or a stripe |
| `QMessageBox` statics | about 90 calls | their icons are Qt's coloured ones: needs the proxy style below |
| Painted widgets | `knob.py`, `envelope_graph.py`, `keyboard.py`, `keygroup_range_bar.py`, `memory_bar.py`, `waveform_view.py`, `slice_waveform_view.py`, `loop_preview_view.py`, `section_card.py`, `fine_slider.py`, `note_spinbox.py` | all read `current_palette()` or hard-code colours; each needs a look |
| Categorical colours | the 8-slot `keygroup_color_1..8` (keygroup range bar; this is where the other apps' volume colours came from) | no colour in System 6: see "Greys, not hatching" |
| Inline styles | `setStyleSheet` in `dashboard.py` (3), `keyboard.py`, `quickstart_dialog.py`, `s950_program_editor.py`, `program_editor_window.py`, `yamaha_program_editor.py`, `yamaha_samples_tab.py` | an inline sheet beats the app one: check each against the new theme |
| Markdown viewers | `quickstart_dialog.py`, `about_dialog.py`, `update_dialog.py` (`setMarkdown`) | code spans and links need fixing (below) |
| Fixed-pitch text | `about_dialog.py` / `loopback_results_dialog.py` use `FixedFont`, `dashboard.py` uses `QFont("Menlo")` for the logo | the ASCII logo must keep a monospace font whatever the theme says |
| Tabs, spin boxes, sliders, progress bars | tabs in `qt_helpers.py`, `settings_dialog.py` and the program editors; `QSpinBox` in 10 modules, `QSlider` in 3, `QProgressBar` in 9 | all four needed fixes in the ports |

It is the only one of the three that talks to real samplers: **the theme must never change what is sent**, and the hardware rules in
`AGENTS.md` ("Real-hardware safety", "House rules") apply while testing. The demo mode (`tools/demo.py`, "Developing without
hardware") is enough for every picture.

## What to copy

From **akaiutil-GUI** (`src/akaiutil_gui/ui/`; it has the later fixes, so start here) and **jv-studio** (`src/jv_studio/ui/`):

- `style_system6.qss.template`: the extra rules, appended after the shared template **only** for this theme. Menus, push buttons
  (default ring), check boxes and radio buttons (an X), combo and spin boxes, scroll bars, progress bars, sliders, tabs, status bar,
  tool tips, the bitmap font rule, selection colours.
- `theme.py`: `SYSTEM6_PALETTE`, the render changes (`square_corners` regex, extra template, `scroll_track*` images via
  `_write_checker(name, orientation)`, `s6_icon_dir`), `_apply_qt_palette`, `_load_bundled_fonts`, `_palette_for_preference`,
  `antialiasing()`, and `_Style` (a `QProxyStyle` over Fusion; see "Icons").
- `ui/fonts/ChiKareGo2.ttf` + `README.txt` (credit), `ui/icons/s6_*.svg` (arrows, check X, radio dot, the four `s6_scroll_*` boxes and
  their `_pressed` versions).
- `mono_icons.py` (black-and-white tree icons and message box icons), `volume_colors.py` (greys, inverted when selected).
- The Markdown helpers in `update_dialog.py` (`restyle_code_runs`, `apply_theme_to_markdown`).
- jv-studio only: `section_card.py` (a striped title bar and close-box arrow; `card_titlebar`), the knob / envelope / piano keyboard keys
  (`knob_track`, `knob_arc`, `graph_line`, `key_down_white`, `key_down_black`, `hard_edges`), `tools/make_screenshots.py --scheme system6`.
- Tests: `tests/test_theme.py` (akaiutil-GUI) and `tests/test_system6_theme.py` (jv-studio) show the kinds of check that worked.

New settings value `"system6"` (`app_config.THEME_SYSTEM6`, added to `THEME_VALUES`), a `CHOICES` entry ("Macintosh System 6"), the
font folder and the extra template added to **all three build scripts' data args and `pysidedeploy.spec`** (a missing data file only
shows up in a built app).

## Plan

1. **Skeleton (about one session).** Palette, `THEME_SYSTEM6`, the extra template, font and icons, build-script data args, the
   Qt palette override. Run it with `uv run python src/main.py --demo` (or the screenshot tool) and look at the main window and the
   program editor. Dark and light must come out **pixel for pixel as before** (see "Check dark and light").
2. **Painted widgets.** Knobs, envelope graphs, the keyboard, the keygroup range bar, the memory bar, the waveform views, the section
   cards, `fine_slider`, `note_spinbox`: hard edges, palette keys, greys for the categorical colours.
3. **Dialogs, message boxes, Markdown.** Render every dialog (the screenshot loop below). The proxy style for message box icons.
   The Markdown fixes. The about logo font. The inline styles.
4. **Tests, docs, builds.** Tests as below; the manual / quick start mentions the theme; `AGENTS.md` gets a short section pointing at this
   file; `THIRD_PARTY_NOTICES.md` credits the font; a README gallery (see akaiutil-GUI's `make_manual_screenshots.py --gallery`).
5. **Real screens.** Ask the owner for screenshots of a few windows on the Mac: the problems found after the offscreen pass were all
   things only a real window showed (see lesson 33).

## Lessons from the two ports

Each is something that went wrong once or cost an iteration. Dated 2026-10-29 unless noted; *seen* means in a picture.

### Structure

1. **Do not edit the shared stylesheet.** Add a palette and an *extra* template that is appended for System 6 only: the other themes
   cannot change by accident. Rules in the extra file win over shared ones of equal specificity because they come later. Where the
   shared file hard-codes something (every `border-radius`, a green `#3aa88a` on slider parts), override it in the extra file or, for
   the radii, rewrite them at render time (`square_corners`: one regex over the shared text before the extra part is appended;
   the extra file's own round radio buttons and 6 px push buttons are then not caught).
2. **A missing palette key must still fail loudly at start-up** (`render_stylesheet` raises on an unknown `${key}`); keys only System 6
   has go in `SYSTEM6_PALETTE` alone and are read with `.get()` by painters, so the other palettes are untouched.
3. **Pick the palette from the preference, not from the OS colour scheme.** System 6 is a pinned choice; set Qt's colour scheme to
   Light for it so native chrome matches. AKAISDS already pins by preference (its `AGENTS.md` says so); keep that.
4. **Qt's own widgets read the application `QPalette`**, which QSS does not paint (a slider's filled groove, a native menu highlight):
   set a black/white one while the theme is on and restore Qt's own (only if you replaced it) when leaving.
5. **Check dark and light are unchanged, by pixels.** `git worktree add <dir> HEAD`, run the same offscreen picture script with
   `PYTHONPATH=<dir>/src` and compare the two images. Only the status-bar text may differ (it names the akaiutil path in
   akaiutil-GUI; find the equivalent). Do this after every change to shared code (a painter, a dialog), not just the template.
6. **Never `str.replace` over QSS text without checking the result.** A "remove the stray `};`" replace also matched `${key};\n}` and
   silently deleted the closing brace of 18 rules. Qt then says only "Could not parse application stylesheet" and the old sheet stays.
   After any bulk edit: count braces (after masking `${...}` and comments) and render the stylesheet once.

### Font and sizes

7. **The font is wider and taller than the 13 px UI font**, and **bitmap fonts only look right at integer scales**. 16 px for text and
   13 px for dense places (the Details table, Markdown tables) worked; at 16 px a ten-column zone table broke words mid-word
   ("Veloc ity"). A wider font also makes the details pane choose two columns later (`details_two_columns_min_width`).
8. **Anything with a fixed width or height clips.** Fixed-width dialogs squeezed their buttons ("Edit Without Backup" lost its first
   and last letters): size such a dialog to `max(base, what its button row needs)`, from the layout's own size hint, not from a number.
   Search AKAISDS for `setFixedWidth`, `setFixedHeight`, `setMaximumWidth` and `resize` in dialogs before calling it done.
9. **The font's `…` glyph is taller than its line**, so any button whose text ends in "..." came out about 5 px taller than its
   neighbours. Give push buttons a **minimum** height (`min-height: 20px`). A fixed `max-height: 15px` was tried first and cut the
   descenders off the g in "Image" (found on the owner's real screen, not offscreen).
10. **Bundle the font with the app** (`--include-data-dir=ui/fonts=ui/fonts` in all three scripts and the spec) and register it with
    `QFontDatabase.addApplicationFont` once; the font list falls back to Geneva / Monaco / monospace if it is missing.

### QSS traps that cost a round each

11. **Scroll bars.**
    - A `border` on the `QScrollBar` itself makes Qt **clip the arrow boxes' own side borders**; a border on the `add-line` /
      `sub-line` boxes is drawn over or clipped. Result: the outline "isn't there" on the sides.
    - Fix: put the outlines in **images**. The track is a tile (16 x 2 px for a vertical bar, 2 x 16 for a horizontal one) whose
      first and last column / row are black and whose middle is a 50% checker; each arrow box is an SVG with its outline, plus an
      inverted `_pressed` SVG. The thumb keeps a normal 1 px border.
    - Qt caches stylesheet images **by path**: generated files must carry their colour or kind in the file name.
12. **Spin boxes**: the shared sheet puts the up/down buttons at `subcontrol-origin: border`, over the box's own outline, which hides
    the right, top and bottom edges. Use `padding`.
13. **Tabs**: the shared rule drops each tab's bottom border and the pane's top border sits under the strip, so unselected tabs had no
    bottom edge. Give every tab `border-bottom: 1px solid`.
14. **Progress bars** show no percentage (unreadable on a dither): `color: transparent`. **Sliders**: the shared green parts become black.
15. **Menus.** The macOS menu bar stays native; QSS only styles the in-window one (offscreen pictures always show it, a real Mac
    window does not). Popup menus are styled.
16. **Selection is inverted video.** Black row, white text: set the item text colour on `::item:selected` for trees, tables and lists
    **and** `selection-color`, and the selected tab. Things painted by hand need it too: **tree branch arrows and any custom glyph
    (the eject button) draw in the selected-text colour when the row is selected**, and **icons need a `QIcon.Mode.Selected` pixmap**
    (invert the normal one) or Qt tints them and a black swatch vanishes on a black row.
17. **Hover highlights** on rows are invisible in this theme (white on white); the owner also wanted the file list's gone in every theme.

### Painted widgets and colour

18. **Anti-aliasing off** for painted widgets (`hard_edges`, one helper `theme.antialiasing()`); knobs, graphs, waveforms, bars.
19. **Greys, not hatching.** The 8 categorical colours cannot exist. Hatch patterns were built first (they are the authentic 1-bit way
    to fake grey) and the owner preferred **eight plain greys, dark and light taking turns so neighbours always differ**
    (`#000`, `#c6c6c6`, `#454545`, `#e4e4e4`, `#808080`, `#222`, `#a3a3a3`, `#626262`), each swatch drawn with a 1 px outline. Things
    that colour **text** by position (loop numbers) need a separate, darker set of greys so the text stays legible. The same applies
    to the keygroup range bar's `keygroup_color_1..8`: pick the greys with the range bar next to a neighbour in mind.
20. **Fills that meant a state** (a bar that went orange then red as a partition filled) became solid black for "how full", with a white
    hairline after each divider; the warning colours stay for text and tooltips. Decide the same for the memory bar.
21. **A loop or range wash** should be a light grey with dashed edges (black at about 15% alpha), the same in every place it appears
    (the owner asked for the main window's waveform to match the editor's).
22. **Piano keys**: pressed keys go grey, not black (a black pressed key stays visible, a white one must not turn solid black).
23. **Section cards (jv-studio's)**: a striped title bar, a close-box arrow, and **one white patch behind the box and the title,
    both as tall as the tallest**: patches sized from each widget left a stripe under the arrow.

### Icons and message boxes

24. **Qt's standard icons are coloured** (folders, drives, the message box icons). Paint black-and-white ones (`mono_icons.py`): no
    asset files, hard edges, a `Selected` mode.
25. **`QMessageBox` icons come from the style.** A `QProxyStyle` over Fusion overrides `standardIcon` for the four message box
    pixmaps while the theme is on. Install it **once per application** (`setStyle` with a new object restyles every widget, and the
    tests call `apply_to_app` repeatedly). With about 90 static calls in AKAISDS this is worth it; check the existing seam
    (`diagnostics_ui.install_dialog_logging`) first so a themed box and a logged one do not fight.

### Markdown (`QTextBrowser.setMarkdown`)

26. **Inline code** is marked monospace at the document's own point size, which is the system's fixed font and bigger than the theme's
    text: after loading, merge a format (the theme's font families, the pixel size of the surrounding text, fixed-pitch off) onto
    every fixed-pitch fragment.
27. **Links** ignore `QPalette.Link` in rendered Markdown and in `QLabel`s (a `QLabel`'s palette colour was ignored even when set to
    red; a `QTextBrowser`'s was honoured): recolour the anchor fragments, and **underline** them, since black links are otherwise
    the same as the text. The About dialog's one `QLabel` link stayed blue in akaiutil-GUI: leave it, or draw it in a `QTextBrowser`.
28. Both fixes are applied once, when the document loads, so a theme switched while the window is open shows them the next time it opens.

### Testing and process

29. **Tests that switch theme restyle every live widget.** The two apps' suites slowed from seconds to minutes until leftover windows
    were deleted first (jv-studio: `_delete_leftover_windows`; akaiutil-GUI: an autouse fixture). AKAISDS's own rule is stricter: pass
    a stand-in to `apply_to_app`, never restyle the real `QApplication` (`AGENTS.md`).
30. **Assert what the picture shows, with pixels:** a selected row has white text on black; a scroll track's tile has black outline
    columns and a checker; the title bar has no stripe under the arrow; the usage bar holds only black and white. A pixel test must
    **fail on the old code**: `git stash` the fix and run it. Two of mine passed on the bug at first because the font's line was only
    one pixel taller than the arrow in the test environment; force the difference (a taller label) instead.
31. **Do not assert exact widths or heights that depend on fonts.** One test asserted a dialog was exactly 540 px; on the Linux CI
    runner the buttons needed 548 and it failed. Assert the intent (no button narrower than its size hint, the dialog not wider than
    a limit).
32. **Render every window and dialog offscreen, crop, and look.** A small script per picture; scale crops up 3x to see edges.
    jv-studio's `tools/make_screenshots.py --out DIR --scheme system6` photographs its existing recipes in the theme; AKAISDS has
    `tools/demo.py` for a window with no hardware.
33. **Offscreen is not the owner's screen.** The offscreen pass missed the clipped descenders, the clipped dialog button and the tab
    bottom edge (the last two seen only in the owner's real-window screenshots). Hand over builds or `--demo` early and ask for screenshots.

## Not possible, or not worth it (settled in the other two repos)

- Using system.css's CSS: QSS has no variables, shadows, `::before` or `calc`.
- A true 1-bit look: text is anti-aliased and sub-pixel positioned, QSS cannot dither text. Greys stand in for dithers.
- Restyling the native title bar, the macOS menu bar, or native file and colour dialogs.
- Apple's fonts, the Apple logo.
- Colour-coding state without colour: the warning orange, critical red and connected green stay.

## Open questions for the owner

1. Which windows must look right first (the Program editor and the keygroup range bar, probably), and which can wait?
2. The keygroup range bar: eight greys (what the other apps ended up with) or something that tells keygroups apart better, since
   adjacent keygroups matter more there than volumes do in akaiutil-GUI?
3. The memory bar: black for used and white for free, like the usage bar?
4. Should the logo (`QFont("Menlo")`) keep its monospace look? (In both ports it must.)
5. Ship it as a theme choice only, or also mention it in the README / manual with a gallery?

## Checklist (copy into the working notes as items are done)

- [ ] `THEME_SYSTEM6`, `CHOICES`, palette, extra template, font, icons; all three build scripts and the spec ship them
- [ ] dark and light pixel-identical (worktree comparison)
- [ ] scroll bars (tiles + SVG boxes), spin boxes, tabs, sliders, progress bars, check boxes, menus
- [ ] selection: lists, tabs, tree arrows, hand-drawn glyphs, icons in `Selected` mode
- [ ] every painted widget: hard edges, palette keys, greys for categorical colours
- [ ] push buttons: `min-height`, default ring; every fixed-width dialog sized from its buttons
- [ ] `QMessageBox` icons via the proxy style (once per app)
- [ ] Markdown: code font, underlined links; the About logo's monospace font kept
- [ ] each dialog rendered and looked at; the owner's real-screen screenshots checked
- [ ] tests (pixel asserts that fail without the fix; no exact font-dependent sizes); the theme-switching rules in `AGENTS.md` followed
- [ ] notices credit the font; manual and README mention it; `AGENTS.md` points here

## Progress (2026-10-29, same day, after the skeleton)

Done, by checklist item (offscreen pictures of all 22 `tools/make_screenshots.py` recipes with `--scheme system6`):

- [x] `THEME_SYSTEM6`, `CHOICES`, palette, extra template, font, icons; the three build scripts now ship the template and `ui/fonts` (not built).
- [x] Dark and light pixel-identical on all 22 recipes against a `git worktree` at HEAD (only the version string differed: the worktree has no `_version.py`).
- [x] Scroll bars, spin boxes, tabs, sliders, progress bars, check boxes, menus (copied rules; seen in pictures except the menus: macOS keeps its own).
- [x] Selection: lists (plain and row-widget lists via `theme_fit.sync_row_selection`), tabs. Not done: the swatch in a selected keygroup row.
- [x] Painted widgets: knobs, envelope graphs, keyboard (grey pressed keys), range bar and swatches (eight greys), memory bar (black fill), waveform / slice views (hard edges via `theme.antialiasing()`).
- [x] Push buttons (`min-height`, default ring); **fixed widths** handled by `ui/theme_fit.py` instead of editing every `setFixedWidth` (the editors have dozens).
- [x] `QMessageBox` icons via the proxy style, once per app.
- [x] Markdown: code font and underlined links in the Quick Start, notices and update notes; the ASCII logos keep a monospace font (`monoText`).
- [x] Tests: `tests/test_system6_theme.py`; notices credit the font; `AGENTS.md` points here.
- [x] Second pass (same day, after the owner's first look at a real window): windows now raise their minimum width as well as growing (the Settings dialog with a long device name was the case), push buttons got more side padding, every tab has a bottom edge, and the editors' section cards are JV Studio's striped title bars.
- [x] Waveform loop region in akaiutil-GUI's style (wash + dashed edges).
- [x] **Loop Preview card header** was taller than the other cards' (its zoom buttons are taller than the title). Done as option (a), chosen by the owner: -/+/1:1 are small title-bar boxes like a System 6 window's zoom box, at the right end of the striped bar (14 px high, `1:1` wider); other themes unchanged. (b) a row in the card body, and (c) an overlay in the preview's corner, were the alternatives.
- [ ] Each dialog looked at: all 22 recipes were rendered, and I looked at the Dashboard, the Program Editor's four tabs, the keyboard bar, Settings, Slice Editor, Reverb, the stray-MIDI box and the Yamaha Samples tab (clean at that size). Not rendered: the S950 editor, Ensoniq / ASR-X dashboards, Yamaha Programs tab, Sample Info, About, Quick Start. Known nits: the Multi tab's key-range spin boxes show a clipped right edge; a selected keygroup row's swatch vanishes. The owner's real-screen screenshots are still needed (lesson 33).
- [ ] The window-grows-to-fit step (`grow_window_for_scroll`) is unit tested only: offscreen's screen is 800 px wide.
- [ ] README / manual mention and gallery.
- [ ] Windows and Linux.

Answers to the open questions used so far (taken as defaults, not confirmed by the owner): Program Editor first; eight greys for keygroups; black for used on the memory bar; the logos stay monospace; theme choice only for now.
