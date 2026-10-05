"""What a bug report needs beyond the per-operation logging: crash capture and
a one-line picture of how the app is configured.

`install_crash_handlers()` exists because nothing else catches these: an exception
raised inside a Qt slot goes to stderr (nowhere, in a packaged GUI app), and a native
crash - the app has had two confirmed SIGSEGVs - leaves no trace at all. Everything
lands in `~/.akaisds/akaisds.log`, except faulthandler's own native-crash traceback,
which has to be written from a signal handler and so goes to `crash.log` beside it.
"""

import faulthandler
import sys
import threading

from core import app_config, debug_log

CRASH_PATH = debug_log.LOG_PATH.parent / "crash.log"
_CRASH_LOG_MAX_BYTES = 100_000  # only ever grows on a crash, but never unbounded

_crash_file = None  # faulthandler needs the file kept open for the whole run


def _log_qt_message(msg_type, _context, message):
    # Qt's own warnings ("QThread: Destroyed while thread is still running" and its
    # kin) normally vanish into stderr; they were the first sign of past crashes
    from PySide6.QtCore import QtMsgType

    level = {
        QtMsgType.QtDebugMsg: "debug",
        QtMsgType.QtInfoMsg: "info",
        QtMsgType.QtWarningMsg: "warning",
        QtMsgType.QtCriticalMsg: "error",
        QtMsgType.QtFatalMsg: "critical",
    }.get(msg_type, "warning")
    getattr(debug_log.get_logger(), level)(f"Qt: {message}")


def install_crash_handlers():
    global _crash_file
    log = debug_log.get_logger()

    def excepthook(exc_type, exc, tb):
        log.critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    def thread_excepthook(args):
        name = args.thread.name if args.thread else "?"
        log.critical(
            f"Unhandled exception in thread {name!r}",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook

    try:
        from PySide6.QtCore import qInstallMessageHandler

        qInstallMessageHandler(_log_qt_message)
    except Exception:
        log.warning("couldn't install the Qt message handler", exc_info=True)

    try:
        previous = ""
        if CRASH_PATH.exists():
            previous = CRASH_PATH.read_text(errors="replace")
        if "Fatal Python error" in previous or "Current thread" in previous:
            log.warning(
                f"the previous session crashed natively - traceback in {CRASH_PATH}"
            )
        mode = "w" if len(previous) > _CRASH_LOG_MAX_BYTES else "a"
        _crash_file = open(CRASH_PATH, mode)
        faulthandler.enable(_crash_file, all_threads=True)
    except Exception:
        log.warning("couldn't enable faulthandler", exc_info=True)


def log_session_config(reason):
    # which sampler/ports/channel the user has chosen: every S1000/S950 report
    # starts with "which model was this?"
    log = debug_log.get_logger()
    try:
        from core.midi_manager import shared_transport_enabled

        input_name, output_name = app_config.get_saved_ports()
        log.info(
            f"config ({reason}): sampler_type={app_config.get_saved_device_type()} "
            f"in={input_name!r} out={output_name!r} "
            f"channel={app_config.get_saved_channel()} "
            f"shared_midi_transport={shared_transport_enabled()} "
            f"theme={app_config.get_saved_theme()}"
        )
    except Exception:
        log.error(f"couldn't read the config for the log ({reason})", exc_info=True)
