import dataclasses
import os
import threading
import time
from collections import deque

from core import akai_sysex, app_config, debug_log
from s3k.bridge import DeviceError, S3kBridge
from PySide6.QtCore import QThread, Signal
import s3k.messages as m
import s3k.params as p

# the sampler holds exactly one resident multi (no list of multis to choose
# between) with a fixed 16 "multipart" slots
MULTI_PART_COUNT = 16

# Hardware-measured corrections to a field whose s3k.params-declared range
# disagrees with what this project's own hardware actually accepts - see
# AGENTS.md's "fields where this project's own hardware beats s3k.params'
# notes". B_PTCHD (pitch-bend-down range): s3k.params still declares 0..12
# as of the pinned rev, transcribed from the Akai spec; measured on this
# project's own S3000-series hardware (2026-09-20, reconfirmed 2026-09-21)
# to actually be 0..24, symmetric with B_PTCH (bend-up). program_editor_
# window.py's bend_down_combo already used 0..24 for the UI, but without
# this, any write above 12 raised ValueError from encode_field's own range
# check (s3k.params._encode_one) against the pinned dependency - a real,
# reproducible failure against actual hardware, not just a future risk.
#
# Only WRITES need this: decode_field never validates against minimum/
# maximum (see its own docstring - only encode_field does), so reading a
# stored value already outside the declared range works fine through the
# unmodified Parameter regardless. Parameter is a frozen dataclass, so this
# builds a corrected COPY at the point of use rather than editing the
# dependency in place (AGENTS.md: don't edit s3k/s3ked itself).
_HARDWARE_RANGE_OVERRIDES = {
    ("B_PTCHD", "program"): {"maximum": 24},
}


def _lookup_for_write(param_name, region):
    param = p.lookup(param_name, region)
    override = _HARDWARE_RANGE_OVERRIDES.get((param_name, region))
    if override is not None:
        param = dataclasses.replace(param, **override)
    return param


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
                        "program_list", "sample_list",
                        # create-program/create-keygroup's own low-level
                        # calls (program_editor_bridge.BridgeWorker._handle_
                        # create_program/_handle_create_keygroup) - brand
                        # new, destructive, untested-on-hardware as of
                        # 2026-09-22, so every step gets the same START/END/
                        # FAILED logging as everything else on this
                        # connection rather than being invisible to
                        # ~/.akaisds/editor_debug.log
                        "get_header_bytes", "send_and_receive")

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
    "K_FREQ",
    # modulation matrix, keygroup half - amounts for destinations whose
    # value is per-keygroup rather than per-program (their sources are
    # program-level, in _PROGRAM_LEVEL_FIELDS below instead) - see
    # program_editor_window.py's Modulation card comments for why this
    # splits across the two regions
    "MODVFILT1",
    "MODVFILT2",
    "MODVFILT3",
    "MODVPITCH",
    "MODVAMP3",
    "L_PTCH",
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
    "ZPLAY1",
    "CP1",
    "SNAME2",
    "LOVEL2",
    "HIVEL2",
    "VTUNO2",
    "VLOUD2",
    "VPANO2",
    "ZPLAY2",
    "CP2",
    "SNAME3",
    "LOVEL3",
    "HIVEL3",
    "VTUNO3",
    "VLOUD3",
    "VPANO3",
    "ZPLAY3",
    "CP3",
    "SNAME4",
    "LOVEL4",
    "HIVEL4",
    "VTUNO4",
    "VLOUD4",
    "VPANO4",
    "ZPLAY4",
    "CP4",
]

#: sample-header fields the Samples tab's waveform view needs. LOOPAT1 is
#: the loop's END, not its start - LLNGTH1 measures backwards from it, so
#: the loop region is [LOOPAT1 - LLNGTH1, LOOPAT1]. This is confirmed in
#: s3ked's own RESOLUTION_NOTES.md (search "LOOPAT1 is the loop END") -
#: the S3000XL manual and an independent implementation (ConvertWithMoss)
#: both agree, and it is NOT mentioned in s3k.params' own notes= field for
#: LOOPAT1, so it's easy to get backwards reading that file alone. Getting
#: this wrong doesn't just misplace a marker - it's the exact bug s3ked's
#: own history describes as producing silent/degraded loops.
_SAMPLE_DETAIL_FIELDS = [
    "SSTART",
    "SMPEND",
    "LOOPAT1",
    "LLNGTH1",
    "SLNGTH",
    "SSRATE",
    "SPTYPE",  # playback/loop type - 0..3, see program_editor_window.py's
    # _SAMPLE_PLAYBACK_TYPE_OPTIONS for the raw-byte-order label/tooltip list
    "SPITCH",  # original pitch (root note) - 21..127, narrower than the
    # usual 0..127 MIDI note range (s3k.params: "21 to 127 represents A1 to G8")
    "SHLTO",  # loop tune, in cents - -50..50, see program_editor_window.py's
    # sample_loop_tune_knob
    "STUNO",  # sample's own gross tuning offset, unsigned raw 0..65535
    # centered at 32768 - see program_editor_window.py's sample_tune_spinbox
    # and _semitones_to_sample_tune_offset/_sample_tune_offset_to_semitones
]

_PROGRAM_LEVEL_FIELDS = [
    "PRGNUM",  # the program's own assignable MIDI program number - see
    # program_editor_window.py's program_number_spinbox and AGENTS.md's
    # "PRGNUM and Program Change" section for the renumber_programs()
    # interaction this can run into
    "PANPOS",
    "PRLOUD",
    "V_LOUD",
    "LFORAT",
    "LFODEP",
    "LFODEL",
    "LFO1WAVE",
    "POLYPH",
    "PMCHAN",
    "PTUNO",
    "PRIORT",
    "B_PTCH",
    "B_PTCHD",
    "PORTEN",
    "PORTIME",
    "PORTYPE",
    "LEGATO",
    # LFO2 - hardwired to Pan on this hardware (PANRAT/PANDEP/PANDEL are
    # LFO2's own rate/depth/delay despite the field names), plus its own
    # waveform/retrigger. See program_editor_window.py's LFO2 card comment.
    "PANRAT",
    "PANDEP",
    "PANDEL",
    "LFO2WAVE",
    "LFO2TRIG",
    # LFO1's sync/desync toggle - genuinely independent of LFO1's own
    # rate/depth/delay/shape above (all program.lfo group) despite living in
    # the program.midi group in s3k.params; see program_editor_window.py's
    # lfo1_sync_combo comment for the value-polarity note
    "DESYNC",
    # modulation matrix, program half - assignable sources for every
    # destination (shared by every keygroup), plus the amounts that are
    # also program-level (Pan x3, Loudness slots 1-2, LFO1 Rate/Depth/
    # Delay x1 each). Filter Frequency/Pitch/Loudness-slot-3's amounts are
    # keygroup-level instead - see _KEYGROUP_DETAIL_FIELDS above.
    "MODSPAN1",
    "MODSPAN2",
    "MODSPAN3",
    "MODVPAN1",
    "MODVPAN2",
    "MODVPAN3",
    "MODSAMP1",
    "MODSAMP2",
    "MODSAMP3",
    "MODVAMP1",
    "MODVAMP2",
    "MODSLFOT",
    "MODSLFOL",
    "MODSLFOD",
    "MODVLFOR",
    "MODVLVOL",
    "MODVLFOD",
    "MODSFILT1",
    "MODSFILT2",
    "MODSFILT3",
    "MODSPITCH",
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
    _COALESCE_KINDS = {"keygroups", "detail", "multi_parts", "sample_detail"}

    programs_loaded = Signal(list)
    programs_load_failed = Signal(str)

    samples_loaded = Signal(list)
    samples_load_failed = Signal(str)

    keygroups_loaded = Signal(int, list, dict)  # program_index, ranges, program_values
    keygroups_load_failed = Signal(int, str)

    detail_loaded = Signal(int, int, dict)  # program_index, keygroup_index, values
    detail_load_failed = Signal(int, int, str)

    # sample_index, values (_SAMPLE_DETAIL_FIELDS) - header only (name/loop
    # points/rate etc), never audio. s3k has no bulk sample-audio transfer
    # at all (no RSPACK/ASPACK) - actual waveform data comes from the
    # Transfer Dashboard's own SamplerController instead, over a completely
    # separate MIDI connection, driven directly from program_editor_window.py
    # rather than through this worker (see its _load_sample_waveform).
    sample_detail_loaded = Signal(int, dict)
    sample_detail_load_failed = Signal(int, str)

    parts_loaded = Signal(list)  # [(program_name, channel, level, pan), ...] per part
    parts_load_failed = Signal(str)

    # the multi file header's own name (MULTINAME, region "multi") - read
    # alongside the 16 parts in the same submit_multi_parts() job since
    # there's only ever one resident multi to fetch it for; a failure here
    # folds into parts_load_failed rather than getting its own, since both
    # come out of the same try/except in _handle_multi_parts
    multi_name_loaded = Signal(str)

    change_sent = Signal(int, str)  # part_index, program_name (for the status bar)
    change_send_failed = Signal(int, str)

    # writer_key, param_name, new_value / error - writer_key is whatever
    # _active_writers used to be keyed by (param_name, or a per-part key for
    # the Multis tab's shared PMCHAN field); param_name is separate so the
    # status bar can still show the real field name either way
    write_succeeded = Signal(str, str, object)
    write_failed = Signal(str, str, str)

    # DELP/DELK/DELS - S3kBridge.delete_program/delete_keygroup/delete_sample
    # default to confirm=True, which waits for and raises on the hardware's
    # own OK/error reply (see s3k.bridge's "_destructive"), so a *_deleted
    # signal here means the sampler actually applied the delete, not just
    # that the frame was sent. Like writes, these are never coalesced -
    # every delete the user confirms must reach the hardware.
    program_deleted = Signal(int)  # program_index
    program_delete_failed = Signal(int, str)

    keygroup_deleted = Signal(int, int)  # program_index, keygroup_index
    keygroup_delete_failed = Signal(int, int, str)

    # unlike DELP, s3k/s3ked document no "last one is silently ignored"
    # restriction for DELS - S3kBridge.clear_memory empties the sample list
    # down to zero the same way it does programs down to one, and
    # DemoBridge.delete_sample has no last-sample special case either. So,
    # unlike _update_list_context_actions_enabled's program_list.count() > 1
    # guard, deleting a sample is enabled whenever one is selected, with no
    # count floor.
    sample_deleted = Signal(int)  # sample_index
    sample_delete_failed = Signal(int, str)

    # PDATA/KDATA - whole-header writes to an index that doesn't yet exist,
    # which is how this protocol "creates" a program/keygroup (see
    # _handle_create_program/_handle_create_keygroup below and AGENTS.md's
    # own write-up). Like the deletes above, a *_created signal here means
    # the sampler actually acknowledged every step with an OK REPLY, not
    # just that the frames were sent.
    program_created = Signal(int, int)  # source_index, new_index
    program_create_failed = Signal(int, str)  # source_index, error

    keygroup_created = Signal(int, int)  # program_index, new_keygroup_index
    keygroup_create_failed = Signal(int, str)  # program_index, error

    # True the moment any job is queued while nothing else is pending, False
    # the moment the queue drains back to empty - one continuous span across
    # a whole burst of jobs (e.g. Refresh submits several at once) rather
    # than toggling between each one. Real hardware reads take real time
    # (~2s for a keygroup/detail fetch over 31250 baud SysEx, per the user);
    # this is what lets the UI show that something is happening without
    # guessing how long it'll take - see ProgramEditorWindow's indeterminate
    # progress bar.
    busy_changed = Signal(bool)

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
            was_idle = not self._queue and not self._busy
            kind = job[0]
            if kind in self._COALESCE_KINDS:
                self._queue = deque(j for j in self._queue if j[0] != kind)
            self._queue.append(job)
            self._idle.notify_all()
        # diagnostic-only - see AGENTS.md's "Diagnosing a load that
        # silently does nothing". isRunning()/isFinished() are QThread's
        # own state, independent of anything this class tracks itself -
        # if the OS thread backing run() has already died (an exception
        # escaping run() entirely, a stale/destroyed QThread object,
        # anything not caught by _safe_dispatch), this is what would
        # actually prove it rather than inferring it from an absence of
        # activity.
        debug_log.get_logger().debug(
            f"BridgeWorker._submit: queued {job!r} (queue_len={len(self._queue)}, "
            f"is_running={self.isRunning()}, is_finished={self.isFinished()})"
        )
        if was_idle:
            self.busy_changed.emit(True)

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

    def submit_sample_detail(self, sample_index):
        self._submit(("sample_detail", sample_index))

    def submit_program_change(self, part_index, program_index, program_name, channel):
        self._submit(("program_change", part_index, program_index, program_name, channel))

    def submit_write(
        self, writer_key, param_name, region, program_index, value, keygroup_index
    ):
        self._submit(
            ("write", writer_key, param_name, region, program_index, value, keygroup_index)
        )

    def submit_delete_program(self, program_index):
        self._submit(("delete_program", program_index))

    def submit_delete_keygroup(self, program_index, keygroup_index):
        self._submit(("delete_keygroup", program_index, keygroup_index))

    def submit_delete_sample(self, sample_index):
        self._submit(("delete_sample", sample_index))

    def submit_create_program(self, source_index, new_name):
        self._submit(("create_program", source_index, new_name))

    def submit_create_keygroup(self, program_index, source_keygroup_index):
        self._submit(("create_keygroup", program_index, source_keygroup_index))

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
            self._safe_dispatch(job)
            with self._idle:
                self._busy = False
                now_idle = not self._queue
                self._idle.notify_all()
            if now_idle:
                self.busy_changed.emit(False)

    def process_pending(self):
        # synchronously drains whatever is currently queued without
        # blocking - lets tests exercise job handling without a real
        # background thread (this is never called from run() itself).
        # Mirrors run()'s own busy_changed(False)/_busy bookkeeping so a
        # test can exercise that signal without spinning up a real thread.
        while True:
            with self._idle:
                if not self._queue:
                    return
                job = self._queue.popleft()
                self._busy = True
            self._safe_dispatch(job)
            with self._idle:
                self._busy = False
                now_idle = not self._queue
                self._idle.notify_all()
            if now_idle:
                self.busy_changed.emit(False)

    def _safe_dispatch(self, job):
        # An exception escaping _dispatch() here would propagate all the
        # way out of run() and silently kill this thread for the rest of
        # the app's life: the GUI thread stays completely responsive
        # (submit_*() just keeps appending to self._queue), but nothing
        # ever pops from that queue again - every future request just sits
        # there forever with no error anywhere. This exact failure mode
        # cost real debugging time (see AGENTS.md's "Diagnosing a load
        # that silently does nothing") before the "GUI still works but
        # nothing ever loads again" pattern was traced back to the worker
        # thread itself having died. Every individual _handle_* already
        # wraps its own bridge calls in a try/except and always emits a
        # *_failed signal - this is the backstop for anything that still
        # gets past that (a bug in a handler itself, a malformed job
        # tuple, anything not anticipated by that handler's own except
        # clause).
        try:
            self._dispatch(job)
        except Exception:
            debug_log.get_logger().error(
                f"BridgeWorker: unhandled exception dispatching {job!r} - "
                "would otherwise have killed the worker thread silently",
                exc_info=True,
            )

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
            # was one hand-spelled get_parameter call per field until this
            # grew past a dozen entries - _PROGRAM_LEVEL_FIELDS follows the
            # same list+loop shape _KEYGROUP_DETAIL_FIELDS already uses below
            program_values = {
                field: self._bridge.get_parameter(p.lookup(field, "program"), program_index)
                for field in _PROGRAM_LEVEL_FIELDS
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

    def _handle_sample_detail(self, sample_index):
        # diagnostic-only - see AGENTS.md's "Diagnosing a load that
        # silently does nothing" - confirms the worker thread actually
        # started dispatching this job at all, distinct from _submit's own
        # logging (which only proves it was queued, not that anything ever
        # picked it up)
        debug_log.get_logger().debug(
            f"[{threading.current_thread().name}] _handle_sample_detail: "
            f"dispatch started for sample_index={sample_index}"
        )
        values = {}
        try:
            for field in _SAMPLE_DETAIL_FIELDS:
                values[field] = self._bridge.get_parameter(
                    p.lookup(field, "sample"), sample_index
                )
        except Exception as e:
            self.sample_detail_load_failed.emit(sample_index, str(e))
            return
        self.sample_detail_loaded.emit(sample_index, values)

    def _handle_multi_parts(self):
        parts = []
        try:
            multi_name = self._bridge.get_parameter(
                p.lookup("MULTINAME", "multi"), 0
            ).strip()
            for part_index in range(MULTI_PART_COUNT):
                header = self._bridge.get_header("multipart", part_index)
                # STEREO is this field's name in the spec, but the panel
                # itself labels it "Lev" (s3k.params notes, §134) - level is
                # what's shown/written here, never "stereo"
                parts.append((
                    header["PRNAME"].strip(),
                    header["PMCHAN"],
                    header["STEREO"],
                    header["PANPOS"],
                ))
        except Exception as e:
            self.parts_load_failed.emit(str(e))
            return
        self.multi_name_loaded.emit(multi_name)
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
            param = _lookup_for_write(param_name, region)
            self._bridge.set_parameter(
                param, program_index, value, keygroup=keygroup_index
            )
        except Exception as e:
            self.write_failed.emit(writer_key, param_name, str(e))
            return
        self.write_succeeded.emit(writer_key, param_name, value)

    def _handle_delete_program(self, program_index):
        try:
            self._bridge.delete_program(program_index)
        except Exception as e:
            self.program_delete_failed.emit(program_index, str(e))
            return
        # the roster just changed - same reasoning as _handle_program_list's
        # own reset: PRGNUM uniqueness for whatever remains isn't guaranteed
        self._programs_renumbered = False
        self.program_deleted.emit(program_index)

    def _handle_delete_keygroup(self, program_index, keygroup_index):
        try:
            self._bridge.delete_keygroup(program_index, keygroup_index)
        except Exception as e:
            self.keygroup_delete_failed.emit(program_index, keygroup_index, str(e))
            return
        self.keygroup_deleted.emit(program_index, keygroup_index)

    def _handle_delete_sample(self, sample_index):
        try:
            self._bridge.delete_sample(sample_index)
        except Exception as e:
            self.sample_delete_failed.emit(sample_index, str(e))
            return
        self.sample_deleted.emit(sample_index)

    # -- create program/keygroup (PDATA/KDATA) -------------------------------
    #
    # Neither s3k nor s3ked ever builds or sends PDATA (whole program header,
    # function code 0x07) or KDATA (whole keygroup header, function code
    # 0x09) - s3k.messages.Command names both and classifies them
    # DESTRUCTIVE_ON_WRITE, but nothing in either package ever constructs one.
    # This is confirmed working protocol, not guesswork: s3000editor (a
    # second, independent open-source Akai editor, source vendored at
    # s3000editor-main/ in this repo's root) uses exactly these two opcodes
    # this same way to implement its own "Add Program"/"Add Keygroup" - see
    # its ui/MainComponent.cpp's addProgram()/addKeygroup() and
    # s3000/ProgramEncoder.cpp/KeygroupEncoder.cpp.
    #
    # Both opcodes write a WHOLE raw 192 byte header to an index that
    # doesn't exist yet - there's no separate "create" command. 192 is
    # s3k.params.REGION_SIZES's own figure for "program"/"keygroup" (the
    # table S3kBridge._check_bounds itself trusts), not region_params()'s
    # smaller ~115-byte extent for "program" - the latter only reflects how
    # many individual fields s3k.params happens to document, not the real
    # on-wire header size, and building a new header from only those fields
    # would leave ~77 real bytes unaccounted for. So this NEVER synthesizes
    # a header from scratch: it clones the full 192 bytes of an existing
    # resident program/keygroup (get_header_bytes(..., 0, 192), bypassing
    # get_header()'s own smaller extent) and only patches the handful of
    # fields that must differ (PRNAME, GROUPS) via s3k.params.encode_field -
    # exactly what s3000editor's own ProgramEncoder::encode/
    # KeygroupEncoder::encode do to a cloned buffer.
    #
    # GROUPS is documented readonly=True in s3k.params, and its own notes
    # says so explicitly: "To change the number of keygroups in a program,
    # the KDATA and DELK commands should be used." Patching it via
    # encode_field on a locally-held buffer (never through set_parameter) is
    # exactly what that note describes.

    def _patch_field(self, header, param_name, region, value):
        # splice one field's encoded bytes into a raw header bytearray at
        # its documented offset - used to patch just PRNAME/GROUPS onto a
        # cloned buffer, never to build one from nothing (see above)
        param = p.lookup(param_name, region)
        header[param.offset : param.offset + param.size] = p.encode_field(
            param, value
        )

    def _send_and_check(self, frame, what):
        # send a raw PDATA/KDATA frame and raise unless the device answers
        # with an OK REPLY - reimplements the same ~4-line check
        # S3kBridge._raise_for_reply does internally (that method is
        # private API, so this is this app's own code rather than reaching
        # into the dependency for it)
        reply = self._bridge.send_and_receive(frame)
        _channel, command, _payload = m.parse_frame(reply)
        if command != m.Command.REPLY:
            raise DeviceError(
                f"expected REPLY {what}, got command {command:#04x}"
            )
        result = m.Reply.decode(reply)
        if not result.ok:
            raise DeviceError(f"device rejected {what} (code {result.code})")

    def _handle_create_program(self, source_index, new_name):
        try:
            if not hasattr(self._bridge, "send_and_receive"):
                # defensive backstop - the UI layer already disables this
                # action in demo mode (s3ked's DemoBridge has no add-
                # program primitive at all, only delete), so this should
                # never actually fire in normal use
                raise RuntimeError(
                    "creating a program needs a real hardware connection "
                    "(not available in demo mode)"
                )
            source_header = self._bridge.get_header_bytes(
                "program", source_index, 0, 192
            )
            group_count = self._bridge.get_parameter(
                p.lookup("GROUPS", "program"), source_index
            )
            new_index = len(self._bridge.program_list())

            header = bytearray(source_header)
            self._patch_field(header, "PRNAME", "program", new_name)
            self._patch_field(header, "GROUPS", "program", 1)
            self._send_and_check(
                akai_sysex.build_pdata_request(new_index, bytes(header)),
                f"creating program {new_index}",
            )

            # keygroup 0 bootstrap - same PDATA(groups=1)-then-KDATA(0)
            # order s3000editor's own addProgram() uses for a brand new
            # program (the program has to exist before a keygroup can be
            # written under it)
            kg0_raw = self._bridge.get_header_bytes(
                "keygroup", source_index, 0, 192, selector=0
            )
            self._send_and_check(
                akai_sysex.build_kdata_request(new_index, 0, kg0_raw),
                f"creating keygroup 0 of program {new_index}",
            )

            # every further keygroup: KDATA first, then re-send the whole
            # PDATA with GROUPS incremented - same order/reasoning as
            # _handle_create_keygroup below (s3000editor's own "add
            # keygroup to an existing program" step pair), just reused in
            # a loop since this app clones every keygroup, not just one
            for keygroup_index in range(1, group_count):
                # selector must be the SOURCE program's keygroup index here
                # - the default is 0, which would silently clone keygroup 0
                # over and over for a multi-keygroup source
                kg_raw = self._bridge.get_header_bytes(
                    "keygroup", source_index, 0, 192, selector=keygroup_index
                )
                self._send_and_check(
                    akai_sysex.build_kdata_request(
                        new_index, keygroup_index, kg_raw
                    ),
                    f"creating keygroup {keygroup_index} of program {new_index}",
                )
                self._patch_field(header, "GROUPS", "program", keygroup_index + 1)
                self._send_and_check(
                    akai_sysex.build_pdata_request(new_index, bytes(header)),
                    f"updating program {new_index} groups="
                    f"{keygroup_index + 1}",
                )
        except Exception as e:
            self.program_create_failed.emit(source_index, str(e))
            return
        # the roster just changed - same reasoning as _handle_delete_
        # program's own reset: PRGNUM uniqueness for the new entry isn't
        # guaranteed
        self._programs_renumbered = False
        self.program_created.emit(source_index, new_index)

    def _handle_create_keygroup(self, program_index, source_keygroup_index):
        try:
            if not hasattr(self._bridge, "send_and_receive"):
                # see _handle_create_program's own comment - same demo-mode
                # backstop
                raise RuntimeError(
                    "creating a keygroup needs a real hardware connection "
                    "(not available in demo mode)"
                )
            group_count = self._bridge.get_parameter(
                p.lookup("GROUPS", "program"), program_index
            )
            if group_count >= 99:
                # same ceiling s3000editor's own addKeygroup() guards
                # client-side, matching GROUPS's own declared 1..99 range
                raise ValueError(
                    "program already has the maximum of 99 keygroups"
                )
            new_index = group_count

            # KDATA for the new keygroup FIRST, then re-send the whole
            # program header with GROUPS+1 - s3000editor's own addKeygroup()
            # order for adding a keygroup to an EXISTING program (the
            # program already exists here, so it's safe to write the new
            # keygroup's storage before officially raising the group count -
            # unlike _handle_create_program's bootstrap step, which has to
            # create the program first since nothing exists yet to write a
            # keygroup under)
            kg_raw = self._bridge.get_header_bytes(
                "keygroup", program_index, 0, 192, selector=source_keygroup_index
            )
            self._send_and_check(
                akai_sysex.build_kdata_request(program_index, new_index, kg_raw),
                f"creating keygroup {new_index}",
            )

            prog_raw = self._bridge.get_header_bytes(
                "program", program_index, 0, 192
            )
            header = bytearray(prog_raw)
            self._patch_field(header, "GROUPS", "program", group_count + 1)
            self._send_and_check(
                akai_sysex.build_pdata_request(program_index, bytes(header)),
                f"updating program {program_index} groups={group_count + 1}",
            )
        except Exception as e:
            self.keygroup_create_failed.emit(program_index, str(e))
            return
        self.keygroup_created.emit(program_index, new_index)
