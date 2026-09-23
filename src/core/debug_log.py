"""File-based debug logging for core/program_editor_bridge.py.

S3kBridge documents itself as NOT safe for two concurrent requests on one
connection - "callers with a UI thread should serialise every call through
a single lock or worker". This module's logging is what caught the real
consequence of that on hardware: a one-shot QThread per UI action used to
leave more than one of them calling the bridge at once, which interleaved
SysEx frames on the wire and, separately, crashed the app outright when a
stale loader's Python object was garbage-collected while its thread was
still mid-call (Qt's QThread::~QThread() aborts the process in that case).

BridgeWorker (see program_editor_bridge.py) now owns the one live
connection for the editor's whole lifetime and processes every request off
a queue strictly one at a time, so neither failure mode can happen any
more. This logging stays in place as the record of that: every bridge
call's START and END (with thread identity, timing, and a full traceback
on failure) goes to a rotating file, so a regression back toward
concurrent calls - Thread B's START appearing before Thread A's END -
would show up directly in the log timeline instead of downstream as an
unexplained protocol error.
"""

import logging
import logging.handlers
from pathlib import Path

LOG_PATH = Path.home() / ".akaisds" / "editor_debug.log"

_logger = None


def get_logger():
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger("akaisds.bridge")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=8_000_000, backupCount=3
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s.%(msecs)03d %(levelname)s %(message)s", "%H:%M:%S"
            )
        )
        logger.addHandler(handler)
    except OSError as e:
        # logging must never be why the editor itself fails to start
        logger.addHandler(logging.NullHandler())
        print(f"[WARN] Couldn't open debug log at {LOG_PATH}: {e}")

    _logger = logger
    return logger
