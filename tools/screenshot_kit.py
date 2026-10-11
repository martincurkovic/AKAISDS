"""
Machinery for tools/make_screenshots.py: the isolated environment (set up on import), the @shot registry, the Context a recipe
is given, and the invented sounds the pictures use. Import this BEFORE any app module so the config/log/backup folders are
already pointed at a throwaway directory.
"""

import math
import os
import struct
import sys
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path

# --- environment: must be set before Qt or any app module is imported ---------------------------------------------------------
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_SCALE_FACTOR", "2")  # pictures at 2x, like a Retina capture
os.environ["AKAISDS_DEMO_SAMPLER"] = "1"  # the program editor's bridge is the in-memory demo, never a real port
os.environ["AKAISDS_DEMO_INSTANT"] = "1"  # ...and it doesn't pace sample reads like a real transfer
os.environ["AKAISDS_SHARED_MIDI_TRANSPORT"] = "1"  # the raw-port path the A4000/S950 fakes plug into
os.environ.pop("AKAISDS_UNLOCK_GLOBAL", None)

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))
DEFAULT_OUT = REPO / "src" / "ui" / "help" / "screenshots"

# --- keep the real ~/.akaisds out of it (before anything opens the log or reads the config) ---------------------------------------
_SCRATCH = Path(tempfile.mkdtemp(prefix="akaisds-shots-"))

from core import debug_log  # noqa: E402

debug_log.LOG_PATH = _SCRATCH / "akaisds.log"

from core import app_config  # noqa: E402

app_config.CONFIG_PATH = _SCRATCH / "config.json"

from controller import yamaha_session  # noqa: E402
from core import program_editor_bridge  # noqa: E402

yamaha_session.BACKUP_DIR = _SCRATCH / "a4000_backups"
program_editor_bridge.PROGRAM_BACKUP_DIR = _SCRATCH / "program_backups"
program_editor_bridge.MDATA_BACKUP_DIR = _SCRATCH / "mdata_backups"
program_editor_bridge._sleep = lambda seconds: None

# --- ...and never touch the real MIDI system. Listing ports creates a macOS CoreMIDI client, which coremidid sometimes refuses
# (OSStatus -304) by aborting the whole process from inside rtmidi, which no try/except can catch; and nothing here needs a real port.
import mido  # noqa: E402
from core import midi_transport  # noqa: E402


def _no_real_midi(*_args, **_kwargs):
    raise RuntimeError("screenshot tool: no real MIDI ports")


# ...nor the network: the main window checks GitHub for a newer version two seconds after it opens
from ui import update_helper  # noqa: E402

update_helper.UpdateCheckRunner.start = lambda self, *args, **kwargs: None

midi_transport.list_input_names = lambda: []
midi_transport.list_output_names = lambda: []
midi_transport._default_input_port_factory = _no_real_midi
midi_transport._default_output_port_factory = _no_real_midi
mido.get_input_names = lambda *a, **k: []
mido.get_output_names = lambda *a, **k: []
mido.open_input = mido.open_output = _no_real_midi

from PySide6.QtCore import QCoreApplication, QEvent, QRect, Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from core import dropped_files  # noqa: E402
from ui import theme  # noqa: E402

dropped_files._BASE_DIR = _SCRATCH / "dropped_files"  # (queued files are copied here, under ~/.akaisds by default)
theme._GENERATED_ICONS_DIR = str(_SCRATCH / "generated_icons")  # (the stylesheet's arrow icons, under ~/Library/Application Support)

SHOTS: dict[str, "Shot"] = {}


@dataclass
class Shot:
    name: str
    description: str
    recipe: callable
    scheme: str = "dark"  # which theme the app is in while it's built and photographed


def shot(name: str, description: str, scheme: str = "dark"):
    def register(fn):
        SHOTS[name] = Shot(name, description, fn, scheme)
        return fn

    return register


class Context:
    """What a recipe is given: the QApplication, a scratch folder, and helpers for waiting and tidying up."""

    def __init__(self, app: QApplication, scratch: Path):
        self.app = app
        self.dir = scratch
        self._cleanups: list = []

    # ---- waiting
    def pump(self, condition=None, timeout: float = 10.0, settle: float = 0.0) -> bool:
        """Run the event loop until `condition()` is true (or the time is up, returning False); `settle` keeps going a bit longer.
        With no condition it just runs for `settle` seconds."""
        end = time.monotonic() + (timeout if condition is not None else 0)
        ok = condition is None
        while time.monotonic() < end:
            self.app.processEvents()
            if condition():
                ok = True
                break
            time.sleep(0.005)
        stop = time.monotonic() + settle
        while time.monotonic() < stop:
            self.app.processEvents()
            time.sleep(0.005)
        return ok

    def show(self, widget: QWidget, size: tuple[int, int] | None = None) -> QWidget:
        """Show a window (at `size` if given) and let it lay itself out."""
        if size:
            widget.resize(*size)
        widget.show()
        self.pump(settle=0.15)
        if not size:
            # (a window can be sized before its rich-text labels know their real height, and adjustSize() won't grow past two thirds of
            # offscreen's tiny 800x600 "screen": give it the size it asks for now that it has been shown)
            widget.resize(widget.sizeHint().expandedTo(widget.size()))
            self.pump(settle=0.1)
        return widget

    def on_cleanup(self, fn) -> None:
        """Run `fn` after this picture is saved (stop a worker thread, close a dialog...)."""
        self._cleanups.append(fn)

    def cleanup(self) -> None:
        for fn in reversed(self._cleanups):
            try:
                fn()
            except Exception as e:  # tidying up must not hide the picture's own result
                print(f"  (cleanup problem: {e})")
        self._cleanups.clear()
        for widget in self.app.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        self.app.processEvents()

    # ---- invented audio (never anyone's real samples)
    def wav(self, name: str, samples: list[float], rate: int = 44100, channels: int = 1) -> str:
        """Write float samples (-1..1; interleaved if stereo) to a 16-bit WAV in the scratch folder, returning its path."""
        path = self.dir / f"{name}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767)) for s in samples))
        return str(path)


# ============================================================================== invented sounds
def tone(seconds: float, hz: float, decay: float = 3.0, rate: int = 44100) -> list[float]:
    return [0.8 * math.exp(-decay * i / rate) * math.sin(2 * math.pi * hz * i / rate) for i in range(int(seconds * rate))]


def noise_burst(seconds: float, decay: float, seed: int, rate: int = 44100) -> list[float]:
    import random

    rng = random.Random(seed)
    return [0.8 * math.exp(-decay * i / rate) * (rng.random() * 2 - 1) for i in range(int(seconds * rate))]


