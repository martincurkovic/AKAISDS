import os
import threading
import time
from collections import deque

from core import app_config, debug_log
from s3k.bridge import S3kBridge
from PySide6.QtCore import QThread, Signal
import s3k.params as p

# the sampler holds exactly one resident multi (no list of multis to choose
# between) with a fixed 16 "multipart" slots
MULTI_PART_COUNT = 16


class _LoggingOut:
    # wraps S3kBridge.out (used directly by ProgramChangeSender, bypassing
    # get_parameter/set_parameter) with the same START/END/failure logging
    # as LoggingBridge below, so a raw MIDI send shows up in the same
    # timeline as everything else on the connection
    def __init__(self, out, logger):
        self._out = out
        self._logger = logger

    def __getattr__(self, name):
        return getattr(self._out, name)

    def send_message(self, message):
        thread_name = threading.current_thread().name
        call_desc = f"out.send_message({message!r})"
        self._logger.debug(f"[{thread_name}] START {call_desc}")
        start = time.monotonic()
        try:
            result = self._out.send_message(message)
        except Exception:
            elapsed_ms = (time.monotonic() - start) * 1000
            self._logger.error(
                f"[{thread_name}] FAILED {call_desc} after {elapsed_ms:.1f}ms",
                exc_info=True,
            )
            raise
        elapsed_ms = (time.monotonic() - start) * 1000
        self._logger.debug(f"[{thread_name}] END {call_desc} ({elapsed_ms:.1f}ms)")
        return result


class LoggingBridge:
    # transparent wrapper around a bridge (S3kBridge or DemoBridge) that
    # logs every call this module's loaders/writers make - see
    # core/debug_log.py for why (S3kBridge is documented as unsafe for
    # concurrent calls, and this module doesn't serialise them today)
    _WRAPPED_METHODS = ("get_parameter", "set_parameter", "get_header",
                        "program_list", "sample_list")

    def __init__(self, bridge, logger=None):
        self._bridge = bridge
        self._logger = logger or debug_log.get_logger()

    def __getattr__(self, name):
        value = getattr(self._bridge, name)
        if name == "out":
            return _LoggingOut(value, self._logger)
        return value

    def _call(self, method_name, *args, **kwargs):
        thread_name = threading.current_thread().name
        call_desc = f"{method_name}(args={args!r}, kwargs={kwargs!r})"
        self._logger.debug(f"[{thread_name}] START {call_desc}")
        start = time.monotonic()
        try:
            result = getattr(self._bridge, method_name)(*args, **kwargs)
        except Exception:
            elapsed_ms = (time.monotonic() - start) * 1000
            self._logger.error(
                f"[{thread_name}] FAILED {call_desc} after {elapsed_ms:.1f}ms",
                exc_info=True,
            )
            raise
        elapsed_ms = (time.monotonic() - start) * 1000
        self._logger.debug(
            f"[{thread_name}] END {call_desc} -> {result!r} ({elapsed_ms:.1f}ms)"
        )
        return result


def _make_wrapped_method(method_name):
    def method(self, *args, **kwargs):
        return self._call(method_name, *args, **kwargs)

    method.__name__ = method_name
    return method


for _name in LoggingBridge._WRAPPED_METHODS:
    setattr(LoggingBridge, _name, _make_wrapped_method(_name))
del _name


def connect():
    # lets the editor be developed away from the hardware sampler - same
    # dummy sampler s3ked itself ships for its --demo flag, duck-typing the
    # slice of S3kBridge this module's loaders/writers actually call
    if os.environ.get("AKAISDS_DEMO_SAMPLER"):
        from s3ked.demo import DemoBridge

        bridge = DemoBridge()
    else:
        _input_name, output_name = app_config.get_saved_ports()
        bridge = S3kBridge.standard(output_name)  # type: ignore
    return LoggingBridge(bridge)


_KEYGROUP_DETAIL_FIELDS = [
    "LONOTE",
    "HINOTE",
    "FILFRQ",
    "FILQ",
    "ATTAK1",
    "DECAY1",
    "SUSTN1",
    "RELSE1",
    "ATTAK2",
    "DECAY2",
    "SUSTN2",
    "RELSE2",
    "ENV2R2",
    "ENV2L1",
    "ENV2L2",
    "ENV2L4",
    "SNAME1",
    "LOVEL1",
    "HIVEL1",
    "VTUNO1",
    "VLOUD1",
    "VPANO1",
    "SNAME2",
    "LOVEL2",
    "HIVEL2",
    "VTUNO2",
    "VLOUD2",
    "VPANO2",
    "SNAME3",
    "LOVEL3",
    "HIVEL3",
    "VTUNO3",
    "VLOUD3",
    "VPANO3",
    "SNAME4",
    "LOVEL4",
    "HIVEL4",
    "VTUNO4",
    "VLOUD4",
    "VPANO4",
]


class BridgeWorker(QThread):
    # S3kBridge documents itself as unsafe for concurrent calls, and a real
    # crash (Qt aborting via QThread::~QThread() when a stale loader's
    # thread was still mid-call) confirmed this app was hitting exactly
    # that: every UI action used to spin up its own one-shot QThread against
    # the same connection, with nothing stopping two of them from being
    # in flight at once and interleaving SysEx frames on the wire.
    #
    # This replaces every one of those (ProgramListLoader, SampleListLoader,
    # KeygroupLoader, KeygroupDetailLoader, MultiPartsLoader,
    # ProgramChangeSender, ParameterWriter) with one persistent thread that
    # owns the bridge for the editor's whole lifetime, taking requests off a
    # queue and running them strictly one at a time - so only ever one call
    # is in flight on the wire, and there is exactly one QThread object to
    # ever worry about outliving.
    #
    # For the "give me the current state of X" kinds (see _COALESCE_KINDS),
    # a newly submitted request drops any not-yet-started one of the same
    # kind still sitting in the queue - clicking through five keygroups
    # fast should end up showing the fifth, not sit through four stale
    # reads first. Writes and Program Changes are never coalesced: every
    # one of those must reach the hardware.
    _COALESCE_KINDS = {"keygroups", "detail", "multi_parts"}

    programs_loaded = Signal(list)
    programs_load_failed = Signal(str)

    samples_loaded = Signal(list)
    samples_load_failed = Signal(str)

    keygroups_loaded = Signal(int, list, dict)  # program_index, ranges, program_values
    keygroups_load_failed = Signal(int, str)

    detail_loaded = Signal(int, int, dict)  # program_index, keygroup_index, values
    detail_load_failed = Signal(int, int, str)

    parts_loaded = Signal(list)  # [(program_name, channel), ...] per part
    parts_load_failed = Signal(str)

    change_sent = Signal(int, str)  # part_index, program_name (for the status bar)
    change_send_failed = Signal(int, str)

    # writer_key, param_name, new_value / error - writer_key is whatever
    # _active_writers used to be keyed by (param_name, or a per-part key for
    # the Multis tab's shared PMCHAN field); param_name is separate so the
    # status bar can still show the real field name either way
    write_succeeded = Signal(str, str, object)
    write_failed = Signal(str, str, str)

    def __init__(self, bridge):
        super().__init__()
        self._bridge = bridge
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._queue = deque()
        self._busy = False
        self._stopped = False
        # PRGNUM (a program's own MIDI program number, independent of its
        # position in the program list) is what a Program Change actually
        # addresses - and it is NOT guaranteed unique: freshly created or
        # independently-loaded programs commonly all read 0, in which case
        # every part assigned any of them plays whichever one the hardware
        # happens to associate with that number, regardless of which was
        # picked. renumber_programs() (see s3k.bridge.S3kBridge) gives every
        # resident program a distinct number in list order and needs no
        # follow-up BTSORT for that - see its own docstring. Invalidated
        # whenever the program list reloads, since a newly created/loaded
        # program may not carry a number distinct from the rest.
        self._programs_renumbered = False

    def _submit(self, job):
        with self._idle:
            kind = job[0]
            if kind in self._COALESCE_KINDS:
                self._queue = deque(j for j in self._queue if j[0] != kind)
            self._queue.append(job)
            self._idle.notify_all()

    def submit_program_list(self):
        self._submit(("program_list",))

    def submit_sample_list(self):
        self._submit(("sample_list",))

    def submit_keygroups(self, program_index):
        self._submit(("keygroups", program_index))

    def submit_detail(self, program_index, keygroup_index):
        self._submit(("detail", program_index, keygroup_index))

    def submit_multi_parts(self):
        self._submit(("multi_parts",))

    def submit_program_change(self, part_index, program_index, program_name, channel):
        self._submit(("program_change", part_index, program_index, program_name, channel))

    def submit_write(
        self, writer_key, param_name, region, program_index, value, keygroup_index
    ):
        self._submit(
            ("write", writer_key, param_name, region, program_index, value, keygroup_index)
        )

    def stop(self):
        # lets whatever is already queued (in particular, pending writes)
        # drain before the thread actually exits - see run()
        with self._idle:
            self._stopped = True
            self._idle.notify_all()

    def wait_until_idle(self, timeout=None):
        # blocks the CALLING thread (never call this from the worker thread
        # itself) until every job submitted so far has been processed and
        # its result signal emitted. Not for the GUI thread during normal
        # operation - it would freeze the UI for as long as the hardware
        # call takes - but exactly the synchronization tests need in place
        # of the old per-loader .wait().
        with self._idle:
            return self._idle.wait_for(
                lambda: not self._queue and not self._busy, timeout
            )

    def run(self):
        while True:
            with self._idle:
                while not self._queue and not self._stopped:
                    self._idle.wait()
                if not self._queue and self._stopped:
                    return
                job = self._queue.popleft()
                self._busy = True
            self._dispatch(job)
            with self._idle:
                self._busy = False
                self._idle.notify_all()

    def process_pending(self):
        # synchronously drains whatever is currently queued without
        # blocking - lets tests exercise job handling without a real
        # background thread (this is never called from run() itself)
        while True:
            with self._idle:
                if not self._queue:
                    return
                job = self._queue.popleft()
            self._dispatch(job)

    def _dispatch(self, job):
        kind = job[0]
        getattr(self, f"_handle_{kind}")(*job[1:])

    def _handle_program_list(self):
        try:
            programs = self._bridge.program_list()
        except Exception as e:
            self.programs_load_failed.emit(str(e))
            return
        # the roster may have changed (a program created/renamed/deleted on
        # the hardware) - PRGNUM uniqueness isn't guaranteed for whatever's
        # newly resident, so the next program change re-establishes it
        self._programs_renumbered = False
        self.programs_loaded.emit(programs)

    def _handle_sample_list(self):
        try:
            samples = self._bridge.sample_list()
        except Exception as e:
            self.samples_load_failed.emit(str(e))
            return
        self.samples_loaded.emit(samples)

    def _handle_keygroups(self, program_index):
        keygroup_ranges = []
        try:
            program_values = {
                "PANPOS": self._bridge.get_parameter(
                    p.lookup("PANPOS", "program"), program_index
                ),
                "LFORAT": self._bridge.get_parameter(
                    p.lookup("LFORAT", "program"), program_index
                ),
                "LFODEP": self._bridge.get_parameter(
                    p.lookup("LFODEP", "program"), program_index
                ),
                "LFODEL": self._bridge.get_parameter(
                    p.lookup("LFODEL", "program"), program_index
                ),
                "LFO1WAVE": self._bridge.get_parameter(
                    p.lookup("LFO1WAVE", "program"), program_index
                ),
                "POLYPH": self._bridge.get_parameter(
                    p.lookup("POLYPH", "program"), program_index
                ),
            }
            # read the real keygroup count off the program header rather than
            # probing until an out-of-range read fails: the real bridge signals
            # that with ValueError, but s3ked's DemoBridge raises its own
            # DemoError, so probing silently dropped every keygroup when
            # running against the demo sampler
            group_count = self._bridge.get_parameter(
                p.lookup("GROUPS", "program"), program_index
            )
            for keygroup_index in range(group_count):
                lo = self._bridge.get_parameter(
                    p.lookup("LONOTE", "keygroup"),
                    program_index,
                    keygroup=keygroup_index,
                )
                hi = self._bridge.get_parameter(
                    p.lookup("HINOTE", "keygroup"),
                    program_index,
                    keygroup=keygroup_index,
                )
                keygroup_ranges.append((lo, hi))
        except Exception as e:
            self.keygroups_load_failed.emit(program_index, str(e))
            return
        self.keygroups_loaded.emit(program_index, keygroup_ranges, program_values)

    def _handle_detail(self, program_index, keygroup_index):
        values = {}
        try:
            for field in _KEYGROUP_DETAIL_FIELDS:
                values[field] = self._bridge.get_parameter(
                    p.lookup(field, "keygroup"),
                    program_index,
                    keygroup=keygroup_index,
                )
        except Exception as e:
            self.detail_load_failed.emit(program_index, keygroup_index, str(e))
            return
        self.detail_loaded.emit(program_index, keygroup_index, values)

    def _handle_multi_parts(self):
        parts = []
        try:
            for part_index in range(MULTI_PART_COUNT):
                header = self._bridge.get_header("multipart", part_index)
                parts.append((header["PRNAME"].strip(), header["PMCHAN"]))
        except Exception as e:
            self.parts_load_failed.emit(str(e))
            return
        self.parts_loaded.emit(parts)

    def _handle_program_change(self, part_index, program_index, program_name, channel):
        # Assigning a program to a multi part has no working SysEx write in
        # the s3k protocol: PRNAME (multipart region) is read-only, and per
        # hardware measurements documented in s3k itself, writing it anyway
        # has no effect on what actually plays. The mechanism the hardware
        # actually needs is a MIDI Program Change on the part's own
        # channel, using THAT PROGRAM's own assignable MIDI program number
        # (PRGNUM, program region) - independent of the program's position
        # in the program list, so it's read fresh here rather than assumed
        # to match program_index.
        #
        # Reuses the bridge's own already-open MIDI connection (out)
        # rather than opening a second one. Fire-and-forget: Program Change
        # has no reply in the MIDI spec, so there is nothing to read back
        # to confirm the sampler actually received or acted on it.
        out = getattr(self._bridge, "out", None)
        if out is None:
            # demo bridge has no live MIDI connection to send this on
            self.change_sent.emit(part_index, program_name)
            return
        try:
            if not self._programs_renumbered:
                # see the comment on _programs_renumbered in __init__ - this
                # is what actually fixes "picking the Nth program always
                # sends the Program Change for the 1st": every program gets
                # its own distinct number before the first change is sent
                renumber = getattr(self._bridge, "renumber_programs", None)
                if renumber is not None:
                    renumber()
                self._programs_renumbered = True
            program_number = self._bridge.get_parameter(
                p.lookup("PRGNUM", "program"), program_index
            )
            out.send_message([0xC0 | (channel & 0x0F), program_number & 0x7F])
        except Exception as e:
            self.change_send_failed.emit(part_index, str(e))
            return
        self.change_sent.emit(part_index, program_name)

    def _handle_write(
        self, writer_key, param_name, region, program_index, value, keygroup_index
    ):
        try:
            param = p.lookup(param_name, region)
            self._bridge.set_parameter(
                param, program_index, value, keygroup=keygroup_index
            )
        except Exception as e:
            self.write_failed.emit(writer_key, param_name, str(e))
            return
        self.write_succeeded.emit(writer_key, param_name, value)
