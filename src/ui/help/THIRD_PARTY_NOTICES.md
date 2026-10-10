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
