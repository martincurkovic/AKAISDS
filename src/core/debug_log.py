"""File-based debug logging for core/program_editor_bridge.py.

S3kBridge documents itself as NOT safe for two concurrent requests on one
connection - "callers with a UI thread should serialise every call through
a single lock or worker" - and this app's loaders/writers don't do that
today (each one just spins up its own QThread without waiting for whatever
previous one might still be running). Against the demo bridge that's
invisible (plain, GIL-serialised Python calls, no real I/O); against real
hardware it can interleave SysEx frames on the wire.

This logs every bridge call's START and END (with thread identity, timing,
and a full traceback on failure) to a rotating file, so two overlapping
calls - Thread B's START appearing before Thread A's END - show up
directly in the log timeline instead of downstream as an unexplained
protocol error.
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
            LOG_PATH, maxBytes=2_000_000, backupCount=3
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
