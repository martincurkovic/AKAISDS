"""Rebuild tests/fixtures/time_stretch_golden.json from the real C++ `TimeStretch.h`.

    uv run python tools/make_time_stretch_golden.py --header path/to/TimeStretch.h

Fetch the header from https://github.com/dar-io-p/potenza-time-stretch (GPL-3.0) and keep it OUT of this repo. The script
compiles a tiny driver around it (needs a C++ compiler: c++/clang++/g++), renders a fixed test signal at a few grain sizes and stretch factors / pitch ratios
the way `core.time_stretch.stretch` and `pitch_shift` do (linear interpolation, one output sample per step at a pitch delta of 1, `round(n x factor)` samples
or, pitch compensated, n samples),
and stores input and output. `tests/test_time_stretch.py` checks the Python port against it, so a port that drifts from the C++ fails.
"""

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# (grain, factor, comp): comp False = time stretch by `factor`, True = pitch shift by ratio `factor` (same length)
CASES = [(300, 1.0, False), (300, 2.0, False), (300, 0.5, False), (200, 1.37, False), (80, 3.0, False),
         (300, 2.0, True), (300, 0.5, True), (200, 1.2599, True), (120, 1.0, True)]
FRAMES = 1200

DRIVER = r"""
#include <functional>
#include <cstdio>
#include <cmath>
#include <vector>
#include "TimeStretch.h"
int main(int argc, char** argv) {
    double grain = atof(argv[1]), factor = atof(argv[2]); bool comp = atoi(argv[3]) != 0;
    std::vector<float> in; float v;
    while (scanf("%f", &v) == 1) in.push_back(v);
    int n = in.size();
    std::function<float(double, float*)> interp = [n](double x, float* d) -> float {
        if (x <= 0) return d[0];
        if (x >= n - 1) return d[n - 1];
        int i = (int)x; double f = x - i;
        return (float)(d[i] * (1 - f) + d[i + 1] * f);
    };
    TimeStretch ts; ts.update(grain, factor, comp); ts.reset(0, 1);
    int outLen = comp ? n : (int)std::llround(n * factor);
    for (int i = 0; i < outLen; i++) {
        printf("%.9g\n", ts.get(in.data(), interp, 1));
        ts.advance(1.0, 0, n - 1, 1);
    }
}
"""


def test_signal():
    # two tones plus a click every 500 samples: tonal and transient material
    return [
        round(math.sin(i * 0.07) * 0.6 + math.sin(i * 0.31) * 0.3 + (0.5 if i % 500 < 20 else 0), 6)
        for i in range(FRAMES)
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--header", required=True, help="path to TimeStretch.h")
    args = parser.parse_args()
    compiler = next((c for c in ("c++", "clang++", "g++") if shutil.which(c)), None)
    if compiler is None:
        sys.exit("no C++ compiler found")
    signal = test_signal()
    stdin = "\n".join(f"{v:.9g}" for v in signal)
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy(args.header, Path(tmp) / "TimeStretch.h")
        (Path(tmp) / "ref.cpp").write_text(DRIVER)
        subprocess.run([compiler, "-std=c++17", "-O1", "-o", str(Path(tmp) / "ref"), str(Path(tmp) / "ref.cpp")], check=True)
        for grain, factor, comp in CASES:
            out = subprocess.run(
                [str(Path(tmp) / "ref"), str(grain), str(factor), str(int(comp))], input=stdin, capture_output=True, text=True, check=True
            ).stdout.split()
            cases.append({"grain": grain, "factor": factor, "comp": comp, "output": [float(f"{float(v):.7g}") for v in out]})
    target = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "time_stretch_golden.json"
    target.write_text(json.dumps({"input": signal, "cases": cases}, separators=(",", ":")))
    print(f"wrote {target} ({sum(len(c['output']) for c in cases)} output samples)")


if __name__ == "__main__":
    main()
