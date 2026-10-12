"""Compile third_party/velociloops into src/core/native/ (the REX2 reader behind core/rx2.py).

    uv run python tools/build_velociloops.py

Needs a C++17 compiler (clang++/g++ on macOS and Linux; cl or g++ on Windows). The three build scripts in
scripts/ run this before packaging, and CI runs it before the tests. The vendored source is NOT edited:
upstream marks no symbols for export, so on Windows the exports come from a .def file made from the header.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SOURCE_DIR = os.path.join(ROOT, "third_party", "velociloops")
OUT_DIR = os.path.join(ROOT, "src", "core", "native")

# keep in step with core/rx2.py's _LIBRARY_NAMES
LIBRARY_NAME = {
    "darwin": "libvelociloops.dylib",
    "win32": "velociloops.dll",
}.get(sys.platform, "libvelociloops.so")


def library_path():
    return os.path.join(OUT_DIR, LIBRARY_NAME)


def _exported_functions():
    with open(os.path.join(SOURCE_DIR, "velociloops.h"), encoding="utf-8") as f:
        return sorted(set(re.findall(r"\b(vl_[a-z0-9_]+)\s*\(", f.read())))


def _command(source, out, workdir):
    if sys.platform == "win32" and shutil.which("cl"):
        def_path = os.path.join(workdir, "velociloops.def")
        with open(def_path, "w") as f:
            f.write("EXPORTS\n" + "\n".join(f"    {n}" for n in _exported_functions()) + "\n")
        return [
            "cl", "/nologo", "/O2", "/MT", "/std:c++17", "/EHsc", "/LD", source,
            f"/Fo{workdir}\\", f"/Fe{out}", "/link", f"/DEF:{def_path}",
        ]
    compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        raise SystemExit("No C++ compiler found (need clang++, g++ or cl).")
    flags = ["-O2", "-std=c++17", "-fPIC", "-shared", "-fvisibility=default"]
    if sys.platform == "darwin":
        flags = ["-O2", "-std=c++17", "-dynamiclib", "-fvisibility=default"]
    elif sys.platform == "win32":
        # MinGW exports every symbol; link the runtime in so the DLL loads on a machine without MinGW
        flags = ["-O2", "-std=c++17", "-shared", "-static-libstdc++", "-static-libgcc"]
    return [compiler, *flags, source, "-o", out]


def build():
    os.makedirs(OUT_DIR, exist_ok=True)
    out = library_path()
    with tempfile.TemporaryDirectory() as workdir:
        cmd = _command(os.path.join(SOURCE_DIR, "velociloops.cpp"), out, workdir)
        print(" ".join(cmd))
        subprocess.run(cmd, check=True, cwd=workdir)
    print(f"Built {out}")
    return out


if __name__ == "__main__":
    build()
