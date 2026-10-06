# AKAISDS: macOS 12 (Monterey) support - findings and handoff

Written 2026-10-03 (late) for whoever picks this up next. **Status: investigation and both
experiments done; nothing changed in the repo yet.** Two users have asked for macOS 12 support.
Results of the experiments: PySide6 **6.9.3 passes the whole test suite** and targets macOS 12.0,
and NumPy's macOS-14 wheel can be avoided with `uv sync --python-platform ...` (NOT with
`MACOSX_DEPLOYMENT_TARGET`, which `uv` ignores - an earlier version of this file said otherwise).
See sections 4 and 6. What remains is the actual change, a local build + scan, and a real
Monterey test.

Working-style notes for the next agent: the owner (Martin) is learning git and makes his own
branches/commits/tags - do not create branches, commit, tag or push unless asked. Ask before
anything outward-facing. Run tests with `uv run pytest tests/ -v` (~25 s). CI sets
`AKAISDS_CI_NO_AUDIO_DEVICE=1`. `AGENTS.md` in the repo root has deep context on the app.

## 1. Short answer

Yes, it looks possible, and **it is essentially one dependency**: PySide6/Qt. The app currently
ships Qt 6.11.2, which needs macOS 13+. Qt 6.9.x (and 6.8 LTS) support macOS 12. A second, smaller
issue is the NumPy wheel that gets bundled (macOS 14 build). Everything else already targets
macOS 11 or older. I could not test on macOS 12 (the dev machine runs macOS 26.7), so the real
proof needs a Monterey user or VM.

## 2. Evidence (verified by inspecting the published v1.2.1 macOS builds)

I downloaded `AKAISDS-macos-arm.dmg` / `AKAISDS-macos-intel.dmg` from the v1.2.1 release,
mounted them and read each Mach-O file's minimum macOS (`vtool -show-build`, field `minos`).

Arm build (133 MB bundle), count of Mach-O files by minimum macOS:

| minos | files | what they are |
|---|---|---|
| 11.0 | 50 | Nuitka `main` executable, Python framework, CPython extension modules, `_cffi_backend`, `_miniaudio`, `rtmidi`, libsndfile, openssl |
| **13.0** | 21 | **Qt frameworks** (QtCore, QtGui, QtWidgets, plus QtQml/QtQuick/QtPdf/QtVirtualKeyboard etc. - see 2a) |
| **14.0** | 13 | **NumPy** (`numpy/_core/_multiarray_umath.so`, `numpy/random/*`, `numpy/linalg`, `numpy/fft`) |
| **15.0** | 7 | **PySide6 bindings**: `libpyside6`, `libshiboken6`, `PySide6/QtCore.so`, `QtGui.so`, `QtWidgets.so`, `QtSvg.so`, `shiboken6/Shiboken.so` |

The Intel build shows the same 13.0 / 14.0 / 15.0 groups (and `10.15` for the cffi module).

Other facts:
- The Nuitka-built `main` executable targets **11.0**, so the build is *not* forcing a high
  deployment target itself.
- The app's `Info.plist` has **no `LSMinimumSystemVersion`**, so macOS will not block the launch
  up front; a too-old system would fail later (missing symbols / unsupported Qt).
- Installed wheel tags (`.venv/.../*.dist-info/WHEEL`): `PySide6 6.11.2` is
  `cp310-abi3-macosx_13_0_universal2`; python-rtmidi 1.5.8, miniaudio 1.71, soundfile 0.14.0,
  cffi 2.1.1, pillow 12.3.0 are all `macosx_11_0_arm64`. **Curiosity:** PySide6 6.11.2's own
  binding `.so` files are stamped 15.0 even though the wheel is tagged 13_0. I don't know whether
  dyld enforces that; if it did, macOS 13/14 users of the current build would already be broken,
  and nobody has reported that. Worth asking a macOS 13/14 user.
- Qt's published support matrix (from doc.qt.io, via search): Qt 6.5 -> macOS 11+; **Qt 6.9 ->
  macOS 12+ (up to 15)**; Qt 6.10 and 6.11 -> macOS 13+ (6.10/6.11 add macOS 26). PySide6 6.8.x and
  6.9.0 wheels carry the `macosx_12_0_universal2` tag. (6.8's macOS 12 floor is inferred from those
  wheel names, not read from the Qt page.)
- GitHub no longer offers macOS 12 runners, so CI cannot test Monterey. A user, or a macOS 12 VM,
  is needed. (I'm not sure about macOS 13 runners either - check.)

### 2a. Side findings (not required for macOS 12, but noticed)
- **NumPy is a real runtime dependency**, just not declared as one. `soundfile` 0.14 does
  `import numpy` at module top level, and `imageio` imports it too. In `pyproject.toml` it's only in
  the `dev` dependency group; it ends up in the shipped app because CI runs plain `uv sync`
  (which installs the dev group). Consider declaring `numpy` as a normal dependency so that
  isn't an accident.
- The bundle includes Qt modules the app never uses (QtPdf, QtQml, QtQuick, QtVirtualKeyboard,
  QtNetwork, QtOpenGL...) even though `src/pysidedeploy.spec` says
  `modules = Core,DBus,Gui,Svg,Widgets`; they come in as dependencies of the Qt plugins. They add
  size and, for this task, each one is another framework that has to be macOS-12-compatible.

## 3. Build pipeline facts

- `.github/workflows/build.yml`: mac jobs run on `macos-15` (arm64) and `macos-15-intel` (x86_64),
  `uv sync`, then `scripts/build_macos.sh`, then a DMG. Python 3.12 (`.python-version`) via
  `actions/setup-python` (its macOS Python framework is 11.0 in the bundle - fine).
- `scripts/build_macos.sh` writes `ui/_version.py` from the git tag and runs
  `uv run pyside6-deploy -c src/pysidedeploy.spec` (Nuitka, `Nuitka==4.1.3` pinned).
- The release workflow is tag-driven (`v*.*.*`), creates a **draft** release; the update checker
  only sees published, non-pre-release releases.
- `pyproject.toml` pins `PySide6==6.11.2` for every platform.

## 4. Plan

1. **Pin PySide6 to a macOS-12-capable Qt: 6.9.x** (latest 6.9.* patch), or 6.8.x (LTS). Two ways:
   - one pin for all platforms: simplest, but also downgrades Windows/Linux; or
   - platform markers, e.g. `"PySide6==6.9.x; sys_platform == 'darwin'"` and keep 6.11.2 elsewhere
     (check `shiboken6`/`pyside6-essentials`/`pyside6-addons` follow along; `uv.lock` must
     re-resolve).
2. **Force the older NumPy wheel on macOS.** NumPy 2.x publishes two arm64 wheels
   (`macosx_11_0_arm64` OpenBLAS and `macosx_14_0_arm64` Accelerate) and two x86_64 ones
   (`macosx_10_13_x86_64` and `macosx_14_0_x86_64`); on a macOS 15 runner plain `uv sync` picks the
   14_0 ones (that's what shipped in v1.2.1). **Verified experimentally (uv 0.12.22):**
   - `MACOSX_DEPLOYMENT_TARGET=12.0` has **no effect** on `uv sync` / `uv pip install` - still
     `macosx_14_0_arm64`.
   - `uv sync --python-platform aarch64-apple-darwin` (and `uv pip install --python-platform ...`)
     **does** select `macosx_11_0_arm64` (NumPy's `_multiarray_umath.so` then reports minos 11.0),
     and every other wheel stays at 11_0. `uv`'s docs say that platform defaults to a macOS 13
     floor and respects `MACOSX_DEPLOYMENT_TARGET` when `--python-platform` is used.
   - For the Intel job use `--python-platform x86_64-apple-darwin`; a dry run picked
     `macosx_10_13_x86_64` for NumPy (not run end-to-end on Intel).
   - Use it in both `scripts/build_macos.sh` (it runs `uv sync` itself) and the workflow's own
     `uv sync` step in the mac jobs. Pick the platform from `uname -m` in the script. `uv sync
     --python-platform` did not modify `uv.lock` or the repo in my test.
   - Watch out: `uv run` re-syncs by default and may undo this - the build script calls
     `uv run pyside6-deploy`; use `uv run --no-sync` (or `UV_NO_SYNC=1`) there, or confirm with the
     scan that the older wheels survived.
3. **Re-scan the built bundle** with the script below; the pass condition is every Mach-O file
   reports minos <= 12.0 (Intel: <= 12.0 too).
4. **Declare the minimum**: add `LSMinimumSystemVersion` = 12.0 to the app's Info.plist (check how
   to do it through `pyside6-deploy`/Nuitka options, e.g. a Nuitka macOS option in
   `extra_args`), so unsupported systems get macOS's clear "requires macOS 12" dialog rather
   than a crash.
5. **Decide single build vs. two builds** (see below).
6. **Test on a real Monterey machine** (a user, or a macOS 12 VM), and test the same build on the
   developer's macOS 26 machine.
7. Update `README.md` / release notes with the minimum macOS version.

### Single build vs. two builds
- **A. One mac build on Qt 6.9 for everyone.** Simplest CI. Risk: Qt 6.9's official range is macOS
  12-15; macOS 26 (Tahoe) support arrived in Qt 6.10, so a 6.9 build may have cosmetic or
  behavioural quirks there (window chrome, fonts, dark-mode switching - and this app has live theme
  switching, `ui/theme.py`). The developer is on macOS 26, so this is easy to try first.
- **B. Two mac builds** ("macOS 12-14 / legacy, Qt 6.9" and "macOS 13+/current, Qt 6.11"): four mac
  jobs instead of two, two download links to explain. Only needed if A shows problems on 26.
- Recommendation: try A, check it on macOS 26, fall back to B only if needed.

### Code-compatibility notes
- `ui/theme.py` uses `QStyleHints.setColorScheme/unsetColorScheme` (Qt 6.8+) behind a `hasattr`
  guard, and `colorScheme()` / `colorSchemeChanged` (Qt 6.5+) - fine on 6.9. A pinned theme is
  decided from the preference, not from what Qt reports, precisely because that override isn't
  honoured everywhere. See `AGENTS.md` "Theme preference".
- Nothing in the app needed Qt newer than 6.9: the full test suite passes on 6.9.3 (section 6a).

## 5. Reusable commands

Inspect the published builds (minimum macOS per Mach-O file):

```bash
curl -sL -o AKAISDS-macos-arm.dmg \
  https://github.com/martincurkovic/AKAISDS/releases/download/v1.2.1/AKAISDS-macos-arm.dmg
hdiutil attach -nobrowse -readonly -mountpoint /tmp/akaisds_mnt AKAISDS-macos-arm.dmg
app=/tmp/akaisds_mnt/AKAISDS.app
find "$app" -type f | while read f; do
  if file -b "$f" | grep -q "Mach-O"; then
    m=$(vtool -show-build "$f" 2>/dev/null | awk '/minos/ {print $2; exit}')
    echo "$m  ${f#$app/Contents/}"
  fi
done | sort -V | awk '{print $1}' | uniq -c      # summary; drop the awk for the file list
hdiutil detach /tmp/akaisds_mnt
```

(`vtool` prints nothing for some x86_64 slices; use `vtool -arch x86_64 -show-build FILE` on
universal binaries.) To scan a **local** build, point `app=` at `src/AKAISDS.app`.

Check an installed wheel's platform tag:
`cat .venv/lib/python3.12/site-packages/pyside6-*.dist-info/WHEEL | grep Tag`.

## 6. Experiments run (2026-10-03) and what is still to do

**6a. Does the code/test suite work on PySide6 6.9.x? Yes.** Built a throwaway env
(`UV_PROJECT_ENVIRONMENT=<tmp> uv sync`, then `uv pip install "PySide6>=6.9,<6.10"` -> PySide6 /
shiboken6 / essentials / addons **6.9.3**, Qt 6.9.3). Wheel tags are `cp39-abi3-macosx_12_0_universal2`;
`QtCore`, `QtWidgets`, `libqcocoa`, the binding `.so`s and `libshiboken6` all report **minos 12.0**.
Full suite with `QT_QPA_PLATFORM=offscreen AKAISDS_CI_NO_AUDIO_DEVICE=1`: **1209 passed, 9 skipped**
(the 9 are the audio-device tests CI skips; without that variable the suite is 1218 tests). The
theme-switching code, which uses `QStyleHints.setColorScheme` behind a `hasattr` guard, is fine. Not
yet tried: 6.8.x. Caveat: the suite runs offscreen, so it can't see real macOS rendering.

Repro:

```bash
export V=/tmp/venv69
UV_PROJECT_ENVIRONMENT=$V uv sync
uv pip install --python $V/bin/python "PySide6>=6.9,<6.10"
QT_QPA_PLATFORM=offscreen AKAISDS_CI_NO_AUDIO_DEVICE=1 $V/bin/python -m pytest tests -q
```

(The download of `pyside6-addons` is ~300 MB; one attempt silently stalled and had to be killed and
restarted without `-q`, so run it with progress output.)

**6b. NumPy wheel selection** - see plan step 2 above (the `MACOSX_DEPLOYMENT_TARGET` idea was
tested and rejected; `--python-platform` works).

**Still to do:**
1. Make the change in a branch: PySide6 pin (section 4, step 1), `--python-platform` in the mac
   build paths, declare `numpy` properly.
2. Build the app locally (`scripts/build_macos.sh`) and run the **section 5 minos scan** on the
   resulting `.app`: every Mach-O file should report minos <= 12.0. This is the real pass/fail test.
   Expect surprises from anything not yet checked (Nuitka-generated files, openssl, etc.).
3. Run that build on the developer's macOS 26 machine (single-build decision, section 4).
4. Get a Monterey user (or VM) to try it.

## 7. Open questions
1. ~~Does PySide6 6.9.x pass the full suite?~~ **Yes** (6.9.3: 1209 passed, 9 skipped).
2. ~~Does `MACOSX_DEPLOYMENT_TARGET=12.0` make `uv` pick the older NumPy wheel?~~ **No** - use
   `uv sync --python-platform aarch64-apple-darwin` / `x86_64-apple-darwin` instead (section 4).
   Still open: does it survive `uv run` re-syncing in `build_macos.sh`, and does the Intel job pick
   the older wheel end-to-end?
3. Does Qt 6.9 behave well on macOS 26 (single build) or do we need two builds?
4. Do the PySide6 6.11.2 binding modules' 15.0 stamp actually break macOS 13/14 today?
5. How do we set `LSMinimumSystemVersion` through `pyside6-deploy`/Nuitka?
6. Is it worth trimming the unused Qt modules from the bundle while we're in there (smaller
   download, fewer frameworks to keep compatible)?
7. Any macOS-12-specific runtime problems in the app's own code paths (CoreMIDI via
   python-rtmidi, audio preview via miniaudio) - only a real Monterey test will show.

## 8. What I'd tell the two users who asked
Not promised yet. The most useful thing they can do once a build exists is run it and send
`~/.akaisds/akaisds.log` (and any macOS crash report) if it fails to launch - the log should
show whether Qt or something else gave up first.
