# macOS 12 (Monterey) support - findings and plan

Written 2026-10-03, condensed 2026-10-10. **Status: investigated, nothing changed in the repo.** Two users asked for it. It comes down to one
dependency (PySide6/Qt) plus the NumPy wheel. The dev machine runs macOS 26, so the real proof needs a Monterey user or VM.

## Why it doesn't run today

Measured on the published v1.2.1 builds (`vtool -show-build`, field `minos`, per Mach-O file; arm shown, Intel is the same):

| minos | what |
|---|---|
| 11.0 | the Nuitka executable, Python framework, extension modules, rtmidi, miniaudio, libsndfile, openssl |
| **13.0** | **Qt frameworks** (Qt 6.11.2 needs macOS 13+) |
| **14.0** | **NumPy** (the `macosx_14_0` wheel that plain `uv sync` picks on a macOS 15 runner) |
| 15.0 | PySide6 binding `.so` files (odd: the wheel is tagged 13_0; whether dyld enforces it is unknown - ask a macOS 13/14 user) |

The app's `Info.plist` has no `LSMinimumSystemVersion`, so macOS doesn't block the launch; an old system fails later. Qt support: 6.5 -> macOS 11+;
**6.9 -> macOS 12-15**; 6.10/6.11 -> macOS 13+ (and add macOS 26). GitHub no longer offers macOS 12 runners, so CI can't test Monterey.

## Experiments (2026-10-03)

- **PySide6 6.9.3 passes the whole suite** (1209 passed, 9 skipped at the time) and its wheels, Qt and binding `.so` files all report minos 12.0.
  The theme code uses `QStyleHints.setColorScheme` behind a `hasattr` guard, fine on 6.9. Offscreen, so it can't see real macOS rendering. 6.8.x not tried.
- **`MACOSX_DEPLOYMENT_TARGET` does NOT change which NumPy wheel `uv` picks.** `uv sync --python-platform aarch64-apple-darwin` (Intel:
  `x86_64-apple-darwin`) does, selecting `macosx_11_0_arm64` / `macosx_10_13_x86_64`. It didn't modify `uv.lock`. `uv run` re-syncs by default and may
  undo it, so the build script's `uv run pyside6-deploy` should use `--no-sync` (or confirm with the scan below).
- NumPy is a real runtime dependency (`soundfile` imports it) declared only in the `dev` group; it ships because CI runs plain `uv sync`. Declare it properly.
- The bundle carries Qt modules the app never uses (QtPdf, QtQml, QtQuick, ...) via plugin dependencies, each another framework that must be 12-compatible.

## Plan

1. Pin PySide6 to 6.9.x (one pin for all platforms, or a `sys_platform == 'darwin'` marker keeping 6.11.2 elsewhere; `uv.lock` must re-resolve).
2. Use `uv sync --python-platform <arch>-apple-darwin` in `scripts/build_macos.sh` and the workflow's mac jobs (pick by `uname -m`).
3. Build locally and run the scan below: **every Mach-O file must report minos <= 12.0**. Expect surprises from Nuitka output and openssl.
4. Add `LSMinimumSystemVersion` = 12.0 to the app's `Info.plist` (find the `pyside6-deploy`/Nuitka option) so old systems get a clear dialog.
5. Single build vs two: try ONE mac build on Qt 6.9 first and check it on macOS 26 (window chrome, fonts, the live theme switch); only split into a
   legacy (6.9) and a current (6.11) build if 6.9 misbehaves there. Then update the README with the minimum macOS version, and get a Monterey
   user to try it and send `~/.akaisds/akaisds.log` (and any crash report) if it fails to launch.

CI facts: `.github/workflows/build.yml` runs mac jobs on `macos-15` (arm64) and `macos-15-intel`, `uv sync`, `scripts/build_macos.sh`
(writes `ui/_version.py` from the tag, runs `uv run pyside6-deploy -c src/pysidedeploy.spec`, Nuitka pinned), then a DMG. The release workflow is
tag-driven and makes a draft release.

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
`grep Tag .venv/lib/python3.12/site-packages/pyside6-*.dist-info/WHEEL`.

## Open questions

Does the `--python-platform` selection survive `uv run` in `build_macos.sh`, and does the Intel job pick the old wheel end to end? Does Qt 6.9 behave
on macOS 26? Does the 15.0 stamp on PySide6 6.11.2's bindings break macOS 13/14 today? How is `LSMinimumSystemVersion` set through `pyside6-deploy`?
Is it worth trimming the unused Qt modules? Any macOS-12-specific problems in CoreMIDI via python-rtmidi or miniaudio (only a Monterey test shows).
