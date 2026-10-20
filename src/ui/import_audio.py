"""File > Import Audio...: pick an audio file or a ReCycle .rx2 loop and decode it for the Slice Editor (shared by the Akai and
Yamaha editors, which each open the Slice Editor their own way).

A plain audio file opens with no markers (exporting with none sends the whole selection as one sample, so the Slice Editor doubles as
a trimmer); an .rx2 opens with its cut points marked and both channels kept.

Length: the Slice Editor holds the whole file as Python lists. Measured 2026-10-12 on a 10 minute stereo MP3: decoding takes ~1 s but
peak memory is ~2.2 GB (~3 GB once the editor has built its mixes) - about 3.7 MB per second of stereo. So a file longer than
WARN_SECONDS asks first, and one longer than REFUSE_SECONDS is not opened.
"""

import os
import time
from dataclasses import dataclass, field

import soundfile as sf
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from core import debug_log, rx2, sds_encoder

WARN_SECONDS = 60  # about 0.2 GB of memory for a stereo file, and already longer than most samplers' memory holds as a sample
REFUSE_SECONDS = 15 * 60  # about 3.5 GB

FILTER = "Audio and ReCycle loops (" + " ".join(
    f"*{ext}" for ext in sorted(sds_encoder.SUPPORTED_AUDIO_EXTENSIONS | {rx2.RX2_EXTENSION})
) + ")"


@dataclass
class ImportedAudio:
    name: str  # the slice names' stem, already in the sampler's alphabet
    channels: list  # one list of 16-bit ints per channel (1 or 2)
    rate: int
    markers: list = field(default_factory=list)  # cut points to start with (an .rx2's own); [] for plain audio
    is_loop: bool = False  # an .rx2

    @property
    def stereo(self):
        return len(self.channels) == 2


def format_duration(seconds):
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"


def pick_and_load(parent, report):
    """Ask for a file and decode it. Returns an ImportedAudio, or None if cancelled or it can't be used - after saying why through
    report(text) (the editor's status bar)."""
    path, _ = QFileDialog.getOpenFileName(parent, "Import Audio", os.path.expanduser("~"), FILTER)
    if not path:
        debug_log.get_logger().debug("ImportAudio: cancelled")
        return None
    return load_path(parent, path, report)


def load_path(parent, path, report):
    base = os.path.basename(path)
    logger = debug_log.get_logger()
    started = time.monotonic()
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        if os.path.splitext(path)[1].lower() == rx2.RX2_EXTENSION:
            loop = rx2.load(path)
            channels, markers = loop.channel_buffers()
            logger.info(f"ImportAudio: {base!r} (.rx2): {len(markers) + 1} slices, {len(channels)} ch, {loop.sample_rate} Hz")
            return ImportedAudio(rx2.slice_base_name(path), channels, loop.sample_rate, markers, is_loop=True)
        try:
            info = sf.info(path)
        except Exception as e:
            logger.warning(f"ImportAudio: couldn't read {path!r}: {e}")
            report(f"Couldn't read {base}: {e}")
            return None
        seconds = info.frames / info.samplerate if info.samplerate else 0
        logger.debug(
            f"ImportAudio: {base!r}: {info.format}/{info.subtype}, {info.channels} ch, {info.samplerate} Hz, "
            f"{info.frames} frames ({seconds:.1f} s)"
        )
        if seconds > REFUSE_SECONDS:
            logger.warning(f"ImportAudio: refused {base!r}: {seconds:.0f} s is over the {REFUSE_SECONDS} s limit")
            report(
                f"{base} is {format_duration(seconds)} long - the Slice Editor opens files up to "
                f"{format_duration(REFUSE_SECONDS)}. Trim it first."
            )
            return None
        if seconds > WARN_SECONDS:
            QApplication.restoreOverrideCursor()
            answer = QMessageBox.question(
                parent,
                "Import Audio",
                f"{base} is {format_duration(seconds)} long. It is all loaded into memory (about "
                f"{info.channels * seconds * 1.9 / 1000:.1f} GB) and is likely too big for the sampler. Open it anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            logger.info(f"ImportAudio: {base!r} is {seconds:.0f} s (over {WARN_SECONDS} s): user said {answer.name}")
            if answer != QMessageBox.StandardButton.Yes:
                return None
        channels, rate = sds_encoder.read_wav_channels(path)
        logger.info(
            f"ImportAudio: {base!r}: {len(channels)} ch, {rate} Hz, {len(channels[0])} frames decoded in "
            f"{(time.monotonic() - started) * 1000:.0f} ms"
        )
        return ImportedAudio(rx2.slice_base_name(path), channels, rate)
    except (rx2.Rx2Error, ValueError, OSError) as e:
        logger.warning(f"ImportAudio: couldn't open {path!r}: {e}")
        report(str(e))
        return None
    finally:
        QApplication.restoreOverrideCursor()
