# Third-party notices

## s950tools

Parts of the Akai S900/S950 support are Python ports of
[s950tools](https://github.com/diemonster/s950tools) by Brandon Ivers
(MIT licence). The derived files are:

- `src/core/s950_sysex.py` - from `internal/sysex/codec.go`,
  `internal/protocol/messages.go`, `internal/sample/convert.go`
- `src/core/s950_program.py` - from `internal/protocol/program.go`
- `tests/test_s950_sysex.py`, `tests/test_s950_program.py` - golden vectors and
  hardware captures from the corresponding `_test.go` files

```
MIT License

Copyright (c) 2026 Brandon Ivers

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

s950tools' own field layouts were in turn cross-checked against
[dxzl/akai-s950](https://github.com/dxzl/akai-s950), which has no licence. No
code or prose from that repository is used here - only byte offsets, which are
facts about the hardware.

## VelociLoops

Reading ReCycle `.rx2` files uses [VelociLoops](https://github.com/kunitoki/VelociLoops) by kunitoki, vendored unmodified
in `third_party/velociloops/` (commit `f77609e`) and compiled by `tools/build_velociloops.py`. It is released into the
public domain under the Unlicense:

```
This is free and unencumbered software released into the public domain.

Anyone is free to copy, modify, publish, use, compile, sell, or
distribute this software, either in source code form or as a compiled
binary, for any purpose, commercial or non-commercial, and by any
means.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.

For more information, please refer to <https://unlicense.org>
```

It includes `fft8g.h`, a general-purpose FFT routine (Ooura's package), which carries no licence text of its own.

## potenza-time-stretch

Time Stretch in the Samples tabs is a Python port of `TimeStretch.h` from
[potenza-time-stretch](https://github.com/dar-io-p/potenza-time-stretch) by dar-io-p, an "Akai style" granular time
stretcher released under the GNU General Public License v3.0 - the same licence as AKAISDS, so no further notice text
is needed here. The derived file is `src/core/time_stretch.py`. `tests/fixtures/time_stretch_golden.json` holds the
output of the real C++ header, made by `tools/make_time_stretch_golden.py`; the header itself is not in this
repository.

## s3ked

The Akai S1000/S2000/S3000 Program Editor is built on [s3ked](https://github.com/lentferj/s3ked) by Jan Lentfer and the
s3ked contributors (`s3k.bridge`, `s3k.params` and `s3ked.demo`), released under the GNU General Public License v2.0 or
later. AKAISDS uses it unmodified, as a dependency pinned to one commit in `pyproject.toml`, and is itself released
under GPL v3.0, which the "or later" allows, so no further notice text is needed here.

## ChiKareGo2

The "Macintosh System 6" theme (Settings > Appearance) draws its text in **ChiKareGo2** by Giles Booth, a recreation of the Chicago 12
bitmap font, released under the Creative Commons Attribution licence (CC BY). It was obtained from [system.css](https://github.com/sakofchit/system.css) as a
`.woff` and converted to `.ttf`; the file is `src/ui/fonts/ChiKareGo2.ttf`. The theme is a homage and is not affiliated with Apple:
Chicago itself is Apple's and is not shipped.
