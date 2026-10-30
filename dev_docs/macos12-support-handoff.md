# macOS 12 (Monterey) support - findings and plan

Written 2026-10-03, rewritten 2026-10-31. **Status: investigated, plan chosen, nothing built yet.** Two users asked for macOS 12. The dev machine
runs macOS 26, so the real proof needs a Monterey user or VM (GitHub no longer offers macOS 12 runners).

**Decision (owner, 2026-10-31): leave the current build exactly as it is (Qt 6.11.2) and add a SEPARATE legacy build on Qt 6.8 LTS for macOS 12+.**
The legacy build never runs on macOS 26, so nobody has to vouch for an old Qt on Liquid Glass, and the current build's look (System 6 theme,
chrome, fonts) carries no regression risk. One codebase, a second dependency set, extra DMGs on the same release.

## Why the current build doesn't run on 12

Measured on the published v1.2.1 builds (`vtool -show-build`, field `minos`, per Mach-O file; arm shown, Intel the same):

| minos | what |
|---|---|
| 11.0 | the Nuitka executable, Python framework, extension modules, rtmidi, miniaudio, libsndfile, openssl |
| **13.0** | **Qt frameworks** (Qt 6.11.2 needs macOS 13+) |
| **14.0** | **NumPy** (the `macosx_14_0` wheel that plain `uv sync` picks on a macOS 15 runner) |
| 15.0 | PySide6 binding `.so` files (inside a wheel tagged 13_0; macOS does NOT enforce it, see below) |

Qt support: 6.5 -> macOS 11+; 6.8 and **6.9 -> macOS 12-15**; 6.10/6.11 -> macOS 13+ (and add macOS 26).

## Measured 2026-10-31 (dev venv, arm64 only; no Intel wheel, built `.app` or Mac with macOS 12/13 was checked)

Wheel tags in `.venv` (`WHEEL` file) and a `vtool` scan of every `.so`/`.dylib` in the venv:

| package | tag / minos | fine on 13? |
|---|---|---|
| PySide6, Essentials, Addons, shiboken6 (6.11.2) | `macosx_13_0_universal2`; 172 Qt files at 13.0, **61 PySide6 + 2 shiboken6 files at 15.0**, 10 at 12.0 | yes (the 15.0 stamps are not enforced: 1.3.0 ran on 14.8.9) |
| python-rtmidi, miniaudio, soundfile, cffi | `macosx_11_0_arm64` | yes |
| imageio, certifi, mido | pure Python | yes |
| **NumPy 2.5.1** | **`macosx_14_0_arm64`**, 19 files at 14.0 | **unknown** (see below) |

- **The macOS 13 report is the plist key.** `LSMinimumSystemVersion` 14.0 arrived in commit `f257708` (2026-10-20) and first shipped in
  **v1.4.0** (v1.3.0 has no key). A user on macOS 13 says the app stopped opening since the change: LaunchServices now refuses the launch before any
  library loads. So macOS 13 probably worked before. **Not confirmed**: the user hasn't said which version ran, nor tried the test below.
- **NumPy is the one real risk on 13.** The `14_0` arm64 wheels are built against the newer Accelerate; I BELIEVE they can fail at import on 13 with a
  missing symbol. Unverified. Every NumPy from 2.2 to 2.5 publishes `macosx_11_0_arm64` AND `macosx_14_0_arm64`, and for Intel
  `macosx_10_13_x86_64` AND `macosx_14_0_x86_64` (PyPI file lists, 2026-10-31), so **Intel is exposed too**: plain `uv sync` on `macos-15-intel`
  may pick the 14.0 wheel.
- **Quick test for a macOS 13 user** (skips LaunchServices' version check): run `/Applications/AKAISDS.app/Contents/MacOS/AKAISDS` from Terminal and
  send the output plus `~/.akaisds/akaisds.log`. If it opens, 13 works and the key is the only problem.
- **Qt 6.8.3 wheels exist for macOS 12**: PySide6, PySide6_Essentials, PySide6_Addons and shiboken6 are all `macosx_12_0_universal2` (PyPI,
  2026-10-31). 6.8.3 is the last 6.8 on PyPI. 6.9.3 is tagged the same way.

## Earlier experiments (2026-10-03)

- **PySide6 6.9.3 passes the whole suite** (1209 passed, 9 skipped at the time) and its wheels, Qt and binding `.so` files all report minos 12.0.
  The theme code uses `QStyleHints.setColorScheme` behind a `hasattr` guard (I believe the API arrived in 6.8; not checked). Offscreen, so it
  can't see real macOS rendering. **6.8.x not tried yet**: that is Phase 1 below. 6.9.3 is the fallback if 6.8 fails.
- **`MACOSX_DEPLOYMENT_TARGET` does NOT change which NumPy wheel `uv` picks.** `uv sync --python-platform aarch64-apple-darwin` (Intel:
  `x86_64-apple-darwin`) does, selecting `macosx_11_0_arm64` / `macosx_10_13_x86_64`. It didn't modify `uv.lock`. `uv run` re-syncs by default and may
  undo it, so the build script's later steps should use `uv run --no-sync`.
- NumPy is a real runtime dependency (`soundfile` imports it) declared only in the `dev` group; it ships because CI runs plain `uv sync`. Declare it properly.
- The bundle carries Qt modules the app never uses (QtPdf, QtQml, QtQuick, ...) via plugin dependencies, each another framework that must be 12-compatible.

## Plan: a separate legacy build

Only two dependencies differ (PySide6 and NumPy); everything else is already minos <= 11.

**Phase 1 - can the legacy dependency set exist? (pyproject.toml and uv.lock only)**
1. Move `PySide6==6.11.2` out of the base dependencies into a `qt-current` group (on by default); add a `qt-legacy` group with `PySide6==6.8.3`;
   declare them conflicting (`[tool.uv] conflicts`) so one lock covers both. Plain `uv sync` and CI must behave as before. **Untried**: check the
   git-sourced `s3ked` doesn't fight it. Fallback: a `requirements-legacy.txt` installed with `uv pip install --python-platform`.
2. Declare NumPy as a real dependency.
3. Run the suite on the legacy group. Expect a few Qt 6.8 vs 6.11 API-drift failures. Try 6.8.3 first (LTS, longer support); fall back to 6.9.3.

**Phase 2 - a local legacy build**
4. `scripts/build_macos.sh` gets a flavor switch (e.g. `AKAISDS_FLAVOR=legacy`): `MIN_MACOS=12.0`, `MACOSX_DEPLOYMENT_TARGET=12.0` (so
   `tools/build_velociloops.py` compiles for 12), `uv sync --group qt-legacy --python-platform <arch>-apple-darwin`, `uv run --no-sync` afterwards,
   DMG name `AKAISDS-macos-{arm,intel}-legacy.dmg`.
5. Turn the scan below into `scripts/check_minos.sh <max>`; the legacy build must report **every Mach-O <= 12.0**. Expect surprises from
   Nuitka's output and openssl.
6. Build the Intel DMG on Intel too: the wheels differ per arch.

**Phase 3 - CI and release**
7. Legacy jobs in `.github/workflows/build.yml` on `macos-15` and `macos-15-intel`, with the minos check as a gate; the release job attaches four DMGs
   to the same tag.
8. A `test.yml` job on the legacy group so Qt 6.10+ APIs can't creep in (no macOS 12 runner exists; this is the only automated protection).
9. Leave `publish-packages.yml` and the Homebrew cask alone: Homebrew itself dropped macOS 12, so legacy is a direct download (not checked how the
   cask would handle it).

**Phase 4 - the app knows its flavor**
10. The build writes the flavor into `ui/_version.py`; the startup `config (...)` log line gains `flavor=` and the Qt version.
11. ~~The update checker must point a legacy user at the `-legacy` DMG~~ Not needed (checked 2026-10-31): `UpdateAvailableDialog` only shows the
    version and notes and opens the release page (`html_url`); it never chooses an asset. What remains is the release page itself listing four
    macOS DMGs: say which is which in the release notes and README. A legacy user who grabs the wrong DMG gets macOS's own "requires macOS 14" refusal.
12. README and download docs say which Mac gets which DMG.

**Phase 5 - proof**
13. Find a Monterey tester (an Intel Mac if possible) and ask for `~/.akaisds/akaisds.log` and any crash report. Until then call it "untested on Monterey".

**Open decisions:** Intel legacy from day one (leaning yes: old Intel Macs are the likely audience; doubles CI minutes), and the name: `-legacy`
plus "macOS 12 and 13" in the README, or name it for what it was built for.

**Current build's floor: 14 -> 13 (decided 2026-10-31, built, NOT run on a macOS 13 Mac).** The trigger was a macOS 13 user whose 1.4.0 stopped opening: the 14.0 plist
key blocked it, and 1.3.0 had no key. `build_macos.sh` now syncs BOTH flavors with `--python-platform` and `MACOSX_DEPLOYMENT_TARGET` (13.0 for current), so NumPy is its
`macosx_11_0`/`10_13` wheel and VelociLoops is compiled for 13; plist 13.0. **Measured: arm64 current build, 116 slices, `check_minos.sh 13.0` reports only the 7 PySide6/shiboken
binding files stamped 15.0** (inside the `13_0`-tagged wheels; 1.3.0 ran with the same stamps on 14.8.9, but 13 is two majors lower and untried), NumPy and VelociLoops are now clean
(no 26.0 slice). Launched offscreen on macOS 26: Qt 6.11.2, `flavor=current`. The macOS 13 user's direct-run test (`.../Contents/MacOS/main` from Terminal) or a real 13 Mac decides it.
Because the current build now covers 13, the legacy build is meant for **macOS 12 and later** (owner, 2026-10-31: it runs fine on macOS 26 too, so it stays on offer as a FALLBACK for any Mac where the current build misbehaves; Qt itself lists 6.8 only for macOS 12-15, so 26 is works-in-practice): labels are `AKAISDS (macOS 13+)` and `AKAISDS Legacy (macOS 12+)`; README, cask and release notes say the same.
CI does not gate the current build with `check_minos.sh` (it would fail on those 7 stamps).

**Homebrew (2026-10-31):** the tap's `Casks/akaisds.rb` is now `depends_on macos: :ventura`, and a new `Casks/akaisds-legacy.rb` (`:monterey`, `conflicts_with` the main cask, `brew style` clean) has
placeholder checksums for 1.4.2. `publish-packages.yml` hashes the `-legacy` DMGs if they exist and bumps the legacy cask too; a release without them (v1.4.1 style) or a tap without the file just skips it
(the bump logic was dry-run against copies of both casks). **Push the tap's changes only around the 1.4.2 release**: before it, the main cask would let macOS 13 users install 1.4.1, which won't open there, and
the legacy cask would 404.

## Progress (2026-10-31)

**Phase 1 done** (`pyproject.toml`, `uv.lock`): `qt-current` (PySide6 6.11.2, default) and `qt-legacy` (6.8.3) are conflicting dependency groups; one
lock covers both, and plain `uv sync` is unchanged. Legacy: `uv sync --no-group qt-current --group qt-legacy`. NumPy is now a real dependency.
- Full suite on Qt 6.8.3 (macOS, arm64, 16m39s, with a macOS VM installing at the time): 3717 passed, 11 failed. **10 were one PySide 6.8 quirk**: the
  QMenu returned by `QAction.menu()` goes "already deleted" once the temporary QAction wrapper is released (the menu is still in the bar; reproduced
  standalone; the app's own two `.menu()` loops keep the action alive, so the app is fine). Tests now use `tests/menu_lookup.top_menu()`. The 8
  affected files pass on both Qt versions (473 each). The 11th, `test_changing_the_replay_mode_writes_it_verifies_and_backs_up_the_original`,
  passed alone 3x on both: probably load-sensitive. **A full legacy re-run since the fix hasn't been done.**
- **`--python-platform` + `MACOSX_DEPLOYMENT_TARGET=12.0` picks the old wheels** (uv reads the variable with `--python-platform`): arm64 NumPy
  `macosx_11_0_arm64`, Intel `macosx_10_13_x86_64`; Qt 12_0; the other native wheels <= 11_0 (arm) / 10_15 (Intel). Intel checked by installing into a scratch env, never run.

**Phase 2 done** (`scripts/build_macos.sh`, `scripts/check_minos.sh`): `AKAISDS_FLAVOR=legacy` uses its own `.venv-legacy`, exports the deployment target
(VelociLoops and Nuitka's C compile for 12), runs everything after the sync with `--no-sync`, rewrites the spec's `python_path` on every run (it was
hard-coded to `.venv`), writes `LSMinimumSystemVersion` 12.0 and names the DMG `AKAISDS-macos-{arm,intel}-legacy.dmg`. `check_minos.sh MAX [APP_OR_DMG]` fails on any slice newer than MAX.
- **Measured: the arm64 legacy build (built on macOS 26) has every one of its 113 slices at minos <= 12.0** (70 at 11.0, 43 at 12.0): no Nuitka or openssl surprise. Plist 12.0, `codesign --verify` passes.
  Launched offscreen on macOS 26: stays up, log says `PySide6 6.8.3, Qt 6.8.3`, VelociLoops loads. **Not run on macOS 12; real windows and theme not looked at; Intel not built.**
- For contrast, an older locally built CURRENT app had 57 slices above 12.0, one of them at 26.0 (a local compile on macOS 26 with no target set).
- **The Qt 6.8 `pyside6-deploy` reinstalls Nuitka 4.1.1** over our pinned 4.1.3 during the build, so the legacy app is compiled by 4.1.1. Works; pin the legacy group to 4.1.1 if the swap bothers you.

**Phase 4 mostly done:** `_version.py` gets `BUILD_FLAVOR` (macOS build only) and the `config (...)` log line carries `flavor=`. DMG volume names say the
macOS range: `AKAISDS (macOS 13+)` and `AKAISDS Legacy (macOS 12+)` (first `14+` / `12-13`, changed with the floors) (built and mounted to check, from the legacy app). README: a two-line note on which DMG to use. The
background picture does NOT mention the range (one picture for both). **README says macOS 12+; the legacy build has since run in a macOS 12 VM (below), but not on real hardware.**

**Reported by the owner, 2026-10-31: the legacy build runs in a macOS 12 VM on the owner's M1 MacBook Air (so arm64, Apple Silicon, virtualised).** Details
not recorded (which DMG, how far they got: launch only, the editors, the theme?). Still untested: Intel, real hardware (the 2015 Air), CoreMIDI with a real interface, a macOS 13 machine.

**Phase 3 written, NOT run on GitHub** (`build.yml`, `test.yml`): `build-macos-legacy` (matrix `macos-15` / `macos-15-intel`; build, `check_minos.sh 12.0`, upload)
and `test-legacy` (Qt 6.8 suite on ubuntu), both `continue-on-error` while new so a failure can't hold up the ordinary release; the release job lists
`build-macos-legacy` in `needs` and its `release-files/**/*` glob attaches whatever legacy DMGs exist. `publish-packages.yml` is untouched (it fetches the
current DMGs by exact name). **Assumed, not checked: that a `continue-on-error` job still lets `release` run when it fails.** First real test: run Build by hand from the Actions tab on a pushed branch.

## Scan a build

```bash
app=/tmp/akaisds_mnt/AKAISDS.app   # a mounted DMG, or src/AKAISDS.app for a local build
find "$app" -type f | while read f; do
  if file -b "$f" | grep -q "Mach-O"; then
    m=$(vtool -show-build "$f" 2>/dev/null | awk '/minos/ {print $2; exit}')
    echo "$m  ${f#$app/Contents/}"
  fi
done | sort -V | awk '{print $1}' | uniq -c      # summary; drop the awk for the file list
```

(`vtool` prints nothing for some x86_64 slices of universal binaries: use `vtool -arch x86_64 -show-build FILE`.) Check a wheel's tag:
`grep Tag .venv/lib/python3.12/site-packages/pyside6-*.dist-info/WHEEL`. The shell is fish: run the loop with `bash -c '...'`.

## Open questions

- Does `uv`'s conflicting-groups feature work with the git-sourced `s3ked`? Does the `--python-platform` selection survive `uv run` in
  `build_macos.sh` (use `--no-sync`), and does the Intel job pick the old wheel end to end?
- Does the suite pass on Qt 6.8.3? Does the 6.8 build look right on a real Mac (offscreen can't tell)?
- Does the 14.0 NumPy wheel actually fail to import on macOS 13? How is `LSMinimumSystemVersion` best kept in step per flavor?
- Is it worth trimming the unused Qt modules? Any macOS-12-specific problems in CoreMIDI via python-rtmidi or miniaudio (only a Monterey test shows)?
