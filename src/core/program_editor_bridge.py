import dataclasses
import datetime
import os
import threading
import time
from collections import deque
from pathlib import Path

from core import akai_sysex, app_config, debug_log, sampler_models
from core import midi_manager as midi_manager_module
from s3k.bridge import DeviceError, S3kBridge, ThrottledOut
from PySide6.QtCore import QThread, Signal
import s3k.messages as m
from s3k.messages import NAME_LENGTH
import s3k.params as p
from core import akai_program_file
from core.s1000_bridge import S1000Bridge
import core.s1000_bridge as s1000_bridge_module

# The sampler's GLOBAL "external controller" setting - what the modulation
# matrix's "External" source means (the S2000/S3000 manual's EXTRNL, p.200).
# Neither Akai spec nor s3k names this register: found by dumping every misc
# byte with the setting on each choice (tools/s2000_misc_probe.py, measured on
# a real S2000, 2026-10-09) - byte 38 was the only register that moved,
# Breath 0 / Footpedal 1 / Volume 2, and two dumps of the same setting were
# identical. An S2000/S3000 machine-wide setting, not per program. The
# S1000 has no such source (its controller routing is fixed) and ignores
# byte-addressable misc ops outright.
MISC_EXTERNAL_CONTROLLER = 38
EXTERNAL_CONTROLLER_LABELS = ("Breath", "Footpedal", "Volume")

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
                        # ~/.akaisds/akaisds.log
                        "get_header_bytes", "send_and_receive",
                        # delete_program/delete_keygroup/delete_sample were
                        # missing from this list entirely - a failed delete
                        # produced no trace at all in ~/.akaisds/akaisds.log,
                        # only a transient str(e) shown in the UI. These are
                        # some of the most likely destructive actions a
                        # remote user hits and reports as "it didn't work".
                        # (renumber_programs is deliberately NOT wrapped the
                        # same way - see _handle_program_change's own
                        # getattr(..., None) check below, which needs to
                        # keep telling a demo bridge without this method
                        # apart from a real one that has it and failed.)
                        "delete_program", "delete_keygroup", "delete_sample")

    def __init__(self, bridge, logger=None):
        self._bridge = bridge
        self._logger = logger or debug_log.get_logger()

    def __getattr__(self, name):
        value = getattr(self._bridge, name)
        if name == "out":
            return _LoggingOut(value, self._logger)
        return value

    @staticmethod
    def _describe(value):
        # a Parameter's own repr is its whole table entry (range, notes,
        # description - ~500 characters), printed twice per call (START and
        # END) for every one of the dozens of get_parameter calls a single
        # keygroup load makes: it was 99% of the log's volume, none of it
        # telling you anything the field's name doesn't. Everything else
        # (indexes, values, raw SysEx frames) is logged as before.
        if isinstance(value, p.Parameter):
            return f"{value.region}.{value.name}"
        return repr(value)

    def _call(self, method_name, *args, **kwargs):
        thread_name = threading.current_thread().name
        arguments = [self._describe(a) for a in args] + [
            f"{key}={self._describe(value)}" for key, value in kwargs.items()
        ]
        call_desc = f"{method_name}({', '.join(arguments)})"
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


def connect(midi_manager=None, sampler_model=None):
    # sampler_model is one of core/sampler_models.py's three selections
    # (None = the S2000/S3000 default). Only the S1000 differs here: it
    # doesn't implement the byte-addressable header SysEx S3kBridge's
    # get_parameter/set_parameter use, so its connection is wrapped in
    # core/s1000_bridge.py's block-based adapter - see that module.
    s1000 = sampler_models.is_s1000(sampler_model)
    # lets the editor be developed away from the hardware sampler - same
    # dummy sampler s3ked itself ships for its --demo flag, duck-typing the
    # slice of S3kBridge this module's loaders/writers actually call
    logger = debug_log.get_logger()
    if os.environ.get("AKAISDS_DEMO_SAMPLER") and s1000:
        # an S1000 demo is a fake that speaks real S1000 SysEx on fake
        # ports (core/demo_s1000.py), not s3ked's DemoBridge - that one
        # answers S3000-style header reads an S1000 never would
        from core.demo_s1000 import FakeS1000

        logger.info("program_editor_bridge.connect(): demo S1000")
        return LoggingBridge(S1000Bridge(FakeS1000().bridge()))
    if os.environ.get("AKAISDS_DEMO_SAMPLER"):
        from s3ked.demo import DemoBridge

        logger.info("program_editor_bridge.connect(): demo bridge")
        bridge = DemoBridge()
    elif (
        midi_manager_module.shared_transport_enabled()
        and midi_manager is not None
        and midi_manager.raw_input is not None
        and midi_manager.raw_output is not None
    ):
        # the Transfer Dashboard's own MidiManager already has a shared
        # raw-rtmidi connection open (see core/midi_manager.py) - build the
        # Program Editor's S3kBridge from THOSE same ports instead of
        # opening a second, independent connection to the same physical
        # device. ThrottledOut still wraps the shared output here exactly
        # as S3kBridge.standard() would wrap its own - the shared transport
        # only changes WHERE the raw port comes from, not S3kBridge's own
        # pacing behaviour. See core/midi_transport.py's own module
        # docstring and tests/midi_transport_consolidation_test_plan.md.
        # midi_manager.raw_input/raw_output are only ever non-None if
        # MidiManager itself already decided shared_transport_enabled() was
        # true when it opened them - re-checking it here too is deliberate
        # belt-and-braces, not redundant: see this module's own connect()
        # tests for exactly the scenario (a stubbed MidiManager with raw
        # ports set but the flag off) this second check exists to catch.
        logger.info(
            "program_editor_bridge.connect(): shared transport "
            f"({midi_manager.output_name!r})"
        )
        bridge = S3kBridge(
            ThrottledOut(midi_manager.raw_output),
            midi_manager.raw_input,
            f"{midi_manager.output_name} (shared)",
        )
    else:
        # AKAISDS_SHARED_MIDI_TRANSPORT=1 alone doesn't guarantee the
        # branch above - falls through here too if midi_manager is None or
        # its raw ports aren't open yet (e.g. the Dashboard's own
        # connection hasn't been established), which is exactly the kind
        # of silent fallback worth being able to see in the log rather
        # than guess at during hardware testing
        _input_name, output_name = app_config.get_saved_ports()
        logger.info(
            f"program_editor_bridge.connect(): standard connection ({output_name!r})"
        )
        bridge = S3kBridge.standard(output_name)  # type: ignore
    if s1000:
        logger.info("program_editor_bridge.connect(): S1000 block adapter")
        bridge = S1000Bridge(bridge)
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
    "SBANDW",  # native engine bandwidth this sample actually plays through
    # on hardware - 0=22050Hz, 1=44100Hz (s3k.params: "0 represents 10kHz,
    # 1 represents 20kHz" - Akai's own spec labels these by audio
    # bandwidth/Nyquist, not literally by sample rate). Read directly
    # rather than re-derived from SSRATE ("nearest bucket") for pitch
    # math - see core/akai_sysex.py's baseline_semitones_for_bandwidth for
    # why re-deriving it is wrong for a sample sent via the generic/
    # universal MIDI SDS path (any bit depth other than 16).
    "SPTYPE",  # playback/loop type - 0..3, see program_editor_window.py's
    # _SAMPLE_PLAYBACK_TYPE_OPTIONS for the raw-byte-order label/tooltip list
    "SPITCH",  # original pitch (root note) - 21..127, narrower than the
    # usual 0..127 MIDI note range (s3k.params: "21 to 127 represents A1 to G8")
    "SHLTO",  # loop tune, in cents - -50..50, see program_editor_window.py's
    # sample_loop_tune_knob
    "STUNO",  # sample's own gross tuning offset, unsigned raw 0..65535
    # centered at 32768 - see program_editor_window.py's sample_tune_spinbox
    # and _semitones_to_sample_tune_offset/_sample_tune_offset_to_semitones
    "LDWELL1",  # loop hold/dwell time, ms - 0..9999, 0="Off" (no loop),
    # 9999="Hold" (loop forever, the default), 1..9998 a plain dwell time.
    # Confirmed on a real S2000, matching s3k.params' own LDWELL1 notes.
    # This app only edits ONE loop region (LOOPAT1/LLNGTH1), so this is the
    # first loop's own dwell setting - see program_editor_window.py's
    # sample_loop_hold_knob (a Knob, ranged 0..9999 - far left is Off, far
    # right is Hold).
]

#: Extra fields read ONLY when the editor is talking to an S1000 (see
#: BridgeWorker's `extra_*_fields`): its fixed controller routing - the
#: S1000's equivalent of the S3000's assignable modulation matrix, whose
#: fields the S3000 spec calls "not used" and so the S2000/S3000 editor has
#: no controls for. Kept out of the base lists so an S2000/S3000 doesn't
#: pay a SysEx round trip each for fields it never shows. See
#: core/s1000_bridge.py's _S1000_RANGE_OVERRIDES for why several of them
#: need a corrected range.
S1000_PROGRAM_FIELDS = [
    "K_LOUD",  # Key>Loudness
    "P_LOUD",  # Pressure>Loudness
    "K_PANP",  # Key>Pan position
    "MW_PAN",  # Modwheel>Pan
    "P_PTCH",  # Pressure>Pitch (+/-12 semitones)
    "MWLDEP",  # Modwheel>LFO1 depth
    "PRSDEP",  # Pressure>LFO1 depth
    "VELDEP",  # Velocity>LFO1 depth
    "K_LRAT",  # Key>LFO1 rate
    "K_LDEP",  # Key>LFO1 depth
    "K_LDEL",  # Key>LFO1 delay
]
S1000_KEYGROUP_FIELDS = [
    "V_FREQ",  # Velocity>Filter frequency
    "P_FREQ",  # Pressure>Filter frequency
    "E_FREQ",  # Envelope 2>Filter frequency (the filter envelope's depth)
    "V_ENV2",  # Velocity>Envelope 2 output level
    "E_PTCH",  # Envelope 2>Pitch
    "KV_LO",  # Velocity>Loudness offset
    "V_ATT1", "V_REL1", "O_REL1", "K_DAR1",  # Envelope 1 (amp) response
    "V_ATT2", "V_REL2", "O_REL2", "K_DAR2",  # Envelope 2 (filter) response
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


#: where a program's .p1/.p3 backup goes before anything destructive is done to it
#: (the S1000 delete-keygroup rebuild). tests/conftest.py points it at a temp dir.
PROGRAM_BACKUP_DIR = Path.home() / ".akaisds" / "program_backups"


class _ImportSafetyError(DeviceError):
    """A program load stopped or failed its own check (message is user-facing)."""


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
    _COALESCE_KINDS = {
        "keygroups",
        "detail",
        "multi_parts",
        "sample_detail",
        "external_controller",
    }

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

    # sample_index, sample_length (frames), sample_rate (Hz) - a
    # deliberately narrower two-field fetch than sample_detail_loaded above
    # (which reads all of _SAMPLE_DETAIL_FIELDS, 11 round-trips). Used to
    # populate every row of the Samples tab's list with a duration once the
    # list itself loads (names only) - fetching the full 11-field detail for
    # every sample just to show a duration would be 5x more round-trips
    # than needed for something shown at a glance, not on selection.
    sample_length_loaded = Signal(int, int, int)
    sample_length_load_failed = Signal(int, str)

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

    # the sampler-wide "external controller" choice (Breath/Footpedal/
    # Volume) - not part of any program, so not a submit_write(). The
    # value is an index into EXTERNAL_CONTROLLER_LABELS.
    external_controller_loaded = Signal(int)
    external_controller_load_failed = Signal(str)
    external_controller_written = Signal(int)
    external_controller_write_failed = Signal(str)

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

    # whole-program save/load (.p1/.p3 files, core/akai_program_file.py): the
    # export reads every raw block of one program, the import writes a parsed
    # file back as a NEW program with the same PDATA/KDATA sequence the
    # duplicate flow uses
    program_exported = Signal(int, object)  # program_index, akai_program_file.ProgramFile
    program_export_failed = Signal(int, str)  # program_index, error
    # an S1000 keygroup delete done by rebuilding the program: the program MOVED
    # (the rebuilt copy is last in the list)
    program_rebuilt = Signal(int, int, int)  # old program_index, deleted keygroup_index, new program_index
    program_imported = Signal(int, str)  # new_index, name
    program_import_failed = Signal(str, str)  # name, error

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

    def __init__(
        self,
        bridge,
        *,
        extra_program_fields=(),
        extra_keygroup_fields=(),
        s1000=False,
    ):
        super().__init__()
        self._bridge = bridge
        # True when `bridge` is (a wrapper around) an S1000Bridge - the
        # worker can't isinstance() it, since LoggingBridge wraps it. Only
        # used to pick the S1000-safe variant of a few destructive flows
        # (see _delete_keygroup_s1000 and _handle_create_program).
        self._s1000 = s1000
        # set the first time an S1000 keygroup delete fails its own
        # before/after verification - the UI then disables Delete Keygroup
        # for the rest of the session (see _delete_keygroup_s1000)
        self.s1000_keygroup_delete_blocked = False
        # model-specific additions to what _handle_keygroups/_handle_detail
        # read (S1000_PROGRAM_FIELDS/S1000_KEYGROUP_FIELDS for an S1000,
        # none otherwise)
        self._program_fields = _PROGRAM_LEVEL_FIELDS + list(extra_program_fields)
        self._keygroup_fields = _KEYGROUP_DETAIL_FIELDS + list(extra_keygroup_fields)
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

    def submit_sample_length(self, sample_index):
        # not in _COALESCE_KINDS - unlike "sample_detail" (only the current
        # selection matters), the caller queues one of these per sample in
        # the list and wants every one of them delivered, not just the last
        self._submit(("sample_length", sample_index))

    def submit_program_change(self, part_index, program_index, program_name, channel):
        self._submit(("program_change", part_index, program_index, program_name, channel))

    def submit_write(
        self, writer_key, param_name, region, program_index, value, keygroup_index
    ):
        self._submit(
            ("write", writer_key, param_name, region, program_index, value, keygroup_index)
        )

    def submit_external_controller(self):
        self._submit(("external_controller",))

    def submit_set_external_controller(self, value):
        # not coalesced: every choice must reach the sampler, in order
        self._submit(("set_external_controller", value))

    def submit_delete_program(self, program_index):
        self._submit(("delete_program", program_index))

    def submit_delete_keygroup(self, program_index, keygroup_index):
        self._submit(("delete_keygroup", program_index, keygroup_index))

    def submit_delete_sample(self, sample_index):
        self._submit(("delete_sample", sample_index))

    def submit_create_program(self, source_index, new_name, first_keygroup_only=False):
        self._submit(("create_program", source_index, new_name, first_keygroup_only))

    def submit_export_program(self, program_index):
        self._submit(("export_program", program_index))

    def submit_import_program(self, program_file, new_name):
        self._submit(("import_program", program_file, new_name))

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

    def is_idle(self):
        # non-blocking snapshot - safe to call from the GUI thread any
        # time. Pairs with busy_changed(False): a caller on the GUI thread
        # that needs to wait for idle WITHOUT freezing the UI (unlike
        # wait_until_idle() - see its own docstring) checks this first to
        # skip waiting entirely if already idle, then waits for
        # busy_changed(False) (via a QEventLoop-pumping wait, e.g.
        # ProgramEditorWindow._wait_for_any_signal) otherwise -
        # busy_changed(False) only fires once the whole queue drains, not
        # once already-idle, so skipping ahead of time is required, not
        # just an optimization.
        with self._idle:
            return not self._queue and not self._busy

    def set_bridge(self, bridge):
        # swaps the underlying bridge run() dispatches every job to -
        # lets ui/program_editor_window.py rebuild its connection in place
        # (e.g. after the shared MIDI ports get reopened out from under it
        # by a MidiSettingsDialog Apply/diagnostic elsewhere - see
        # MidiManager.connection_changed's own docstring there) without
        # tearing down and recreating this QThread, which would mean
        # reconnecting every one of its Signals by hand at every call site
        # in that file. CALLER'S responsibility to have already confirmed
        # the worker is idle first (wait_until_idle()) - same
        # single-job-at-a-time assumption run() already makes about
        # self._bridge everywhere else, just not one this method can
        # enforce on its own from the calling (GUI) thread.
        with self._idle:
            self._bridge = bridge

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
                for field in self._program_fields
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
            for field in self._keygroup_fields:
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

    def _handle_sample_length(self, sample_index):
        try:
            sample_length = self._bridge.get_parameter(
                p.lookup("SLNGTH", "sample"), sample_index
            )
            sample_rate = self._bridge.get_parameter(
                p.lookup("SSRATE", "sample"), sample_index
            )
        except Exception as e:
            self.sample_length_load_failed.emit(sample_index, str(e))
            return
        self.sample_length_loaded.emit(sample_index, sample_length, sample_rate)

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
                    # not one of LoggingBridge's wrapped methods (its
                    # existence has to stay tellable-apart from a demo
                    # bridge that simply lacks it - see the comment on
                    # LoggingBridge._WRAPPED_METHODS), so it gets its own
                    # explicit START/FAILED logging here instead. A failed
                    # renumber silently breaks every Program Change sent
                    # afterward (every program can go back to colliding on
                    # PRGNUM==0 - see this method's own docstring above),
                    # so this needs to be visible on its own, not just
                    # inferred from the Program Change itself failing.
                    thread_name = threading.current_thread().name
                    logger = debug_log.get_logger()
                    logger.debug(f"[{thread_name}] START renumber_programs()")
                    try:
                        renumber()
                    except Exception:
                        logger.error(
                            f"[{thread_name}] FAILED renumber_programs()",
                            exc_info=True,
                        )
                        raise
                    logger.debug(f"[{thread_name}] END renumber_programs()")
                self._programs_renumbered = True
            # get_parameter applies PRGNUM's display_offset (the panel's
            # 1-based numbering, since s3ked's params table declared it -
            # see AGENTS.md's "PRGNUM and Program Change" section) - a MIDI
            # Program Change needs the raw 0-based wire value, so the
            # offset is subtracted back out here rather than assumed away.
            prgnum_param = p.lookup("PRGNUM", "program")
            program_number = (
                self._bridge.get_parameter(prgnum_param, program_index)
                - prgnum_param.display_offset
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

    def _handle_external_controller(self):
        try:
            value = self._read_external_controller()
        except Exception as e:
            self.external_controller_load_failed.emit(str(e))
            return
        self.external_controller_loaded.emit(value)

    def _handle_set_external_controller(self, value):
        try:
            if not 0 <= value < len(EXTERNAL_CONTROLLER_LABELS):
                raise ValueError(f"external controller {value} is not 0-2")
            write_verify = getattr(self._bridge, "_misc_write_verify", None)
            if write_verify is None or self._s1000:
                raise DeviceError("not available on this connection")
            # write, then believe the READ: several misc registers answer a
            # good write with an error code (see S3kBridge._misc_write_verify)
            write_verify(
                MISC_EXTERNAL_CONTROLLER, value, "setting the external controller"
            )
        except Exception as e:
            debug_log.get_logger().error(
                f"external controller: write of {value} failed: {e}"
            )
            self.external_controller_write_failed.emit(str(e))
            return
        debug_log.get_logger().info(
            f"external controller: set to {EXTERNAL_CONTROLLER_LABELS[value]} ({value})"
        )
        self.external_controller_written.emit(value)

    def _read_external_controller(self):
        read_byte = getattr(self._bridge, "_misc_byte", None)
        if read_byte is None or self._s1000:
            raise DeviceError("not available on this connection")
        value = read_byte(MISC_EXTERNAL_CONTROLLER)
        if not 0 <= value < len(EXTERNAL_CONTROLLER_LABELS):
            # a firmware that keeps something else here: show nothing rather
            # than a wrong choice
            raise DeviceError(f"unexpected value {value} in the register")
        return value

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
            if self._s1000 and s1000_bridge_module.KEYGROUP_DELETE_BY_REBUILD:
                new_index = self._delete_keygroup_s1000_rebuild(
                    program_index, keygroup_index
                )
            elif self._s1000:
                self._delete_keygroup_s1000(program_index, keygroup_index)
            else:
                self._bridge.delete_keygroup(program_index, keygroup_index)
        except Exception as e:
            self.keygroup_delete_failed.emit(program_index, keygroup_index, str(e))
            return
        if self._s1000 and s1000_bridge_module.KEYGROUP_DELETE_BY_REBUILD:
            self._programs_renumbered = False
            self.program_rebuilt.emit(program_index, keygroup_index, new_index)
            return
        self.keygroup_deleted.emit(program_index, keygroup_index)

    #: how many block reads the post-delete check may spend on programs
    #: OTHER than the one being edited (~0.1s each over MIDI). The target
    #: program is always checked in full.
    _S1000_VERIFY_READ_BUDGET = 60

    @staticmethod
    def _without_pointer(block):
        # a program block's bytes 1-2 are FIRSTKG, a keygroup block's NXTKG:
        # absolute addresses the sampler is free to rewrite, so content
        # comparisons ignore them (they're compared and logged separately)
        return bytes(block[:1]) + bytes(block[3:])

    def _s1000_snapshot(self, target_index):
        # {program_index: {"groups", "program", "keygroups", "pointers"}} for
        # the target program (in full) and as many others as the read budget
        # allows. Everything is read FRESH from the hardware: S1000Bridge
        # caches blocks for 1.5s, and a PDATA write also leaves its own
        # copy cached - neither may stand in for what the sampler holds.
        invalidate = getattr(self._bridge, "invalidate", None)
        if invalidate is not None:
            invalidate()
        count = len(self._bridge.program_list())
        order = [target_index] + [i for i in range(count) if i != target_index]
        budget = self._S1000_VERIFY_READ_BUDGET
        snapshot = {}
        for index in order:
            if index >= count:
                continue
            program_block = self._bridge.get_header_bytes("program", index, 0, 192)
            groups = self._bridge.get_parameter(p.lookup("GROUPS", "program"), index)
            if index != target_index:
                if budget < groups + 1:
                    debug_log.get_logger().info(
                        "S1000 delete check: program %d not snapshotted "
                        "(read budget exhausted)",
                        index,
                    )
                    continue
                budget -= groups + 1
            keygroups = [
                self._bridge.get_header_bytes(
                    "keygroup", index, 0, 192, selector=k
                )
                for k in range(groups)
            ]
            snapshot[index] = {
                "groups": groups,
                "program": self._without_pointer(program_block),
                "keygroups": [self._without_pointer(b) for b in keygroups],
                "pointers": [bytes(program_block[1:3])]
                + [bytes(b[1:3]) for b in keygroups],
            }
        return snapshot

    def _read_keygroups_to_shift_s1000(self, program_index, keygroup_index, groups):
        # the FULL blocks (pointer bytes included) of keygroups k..N-1, read
        # fresh before anything is written
        return [
            bytes(
                self._bridge.get_header_bytes(
                    "keygroup", program_index, 0, 192, selector=i
                )
            )
            for i in range(keygroup_index, groups)
        ]

    def _shift_keygroups_down_s1000(self, program_index, keygroup_index, blocks):
        """Overwrite keygroup k..N-2 with the content of k+1..N-1.

        Why delete the LAST keygroup instead of the chosen one: a real S1000
        (2026-10-06 log) answered DELK of keygroup 0 by moving the program's
        FIRSTKG forward 150 bytes, and then rejected the PDATA that rewrites
        GROUPS (every PDATA that ever worked carried an unchanged FIRSTKG).
        Deleting the last keygroup should leave FIRSTKG alone. Each slot keeps
        ITS OWN pointer bytes (1-2, NXTKG: an absolute address) - only the
        content moves - so the chain's addresses never change. Ascending
        order, from blocks read up front, so nothing is read after being
        overwritten. Until the DELK that follows, this only duplicates content:
        a failure part-way leaves a valid program with a repeated keygroup.
        """
        for offset in range(len(blocks) - 1):
            slot = keygroup_index + offset
            slot_block = blocks[offset]
            donor = blocks[offset + 1]
            new_block = slot_block[:1] + slot_block[1:3] + donor[3:]
            if new_block == slot_block:
                continue
            self._send_and_check(
                akai_sysex.build_kdata_request(program_index, slot, new_block),
                f"moving keygroup {slot + 1} down to {slot} of program {program_index}",
            )

    def _repair_groups_after_delk(self, program_index, keygroup_index, groups):
        logger = debug_log.get_logger()
        groups_param = p.lookup("GROUPS", "program")
        groups_now = self._bridge.get_parameter(groups_param, program_index)
        logger.info(
            "S1000 delete keygroup %d of program %d: GROUPS %d -> %d after DELK",
            keygroup_index, program_index, groups, groups_now,
        )
        if groups_now == groups:
            # the observed S1000 behaviour: DELK left the count alone
            header = bytearray(
                self._bridge.get_header_bytes("program", program_index, 0, 192)
            )
            self._patch_field(header, "GROUPS", "program", groups - 1)
            self._send_and_check(
                akai_sysex.build_pdata_request(program_index, bytes(header)),
                f"updating program {program_index} groups={groups - 1}",
            )
        elif groups_now != groups - 1:
            raise DeviceError(
                f"program {program_index} reports {groups_now} keygroups after "
                f"deleting one of {groups} - check the sampler directly"
            )

    def _delete_keygroup_s1000(self, program_index, keygroup_index):
        """DELK on an S1000, repaired and verified.

        A real S1000 (2026-10-05 log) acknowledged DELK but left the
        program's GROUPS unchanged. The next "add keygroup" then used that
        stale count as the new keygroup's index, walked off the end of the
        program's chain and linked the new keygroup into ANOTHER program,
        orphaning that program's tail. So after DELK this rewrites the
        program with the real GROUPS, then compares a before/after snapshot:
        the surviving keygroups must be unchanged and in order, and no other
        program may have changed. A mismatch raises (the UI shows it), logs
        everything, and blocks further S1000 keygroup deletes this session.

        The DELK itself always targets the LAST keygroup, after the ones
        behind the chosen one were shifted down over it
        (_shift_keygroups_down_s1000) - deleting keygroup 0 directly broke
        the GROUPS repair on a real S1000 (2026-10-06, see AGENTS.md).
        """
        logger = debug_log.get_logger()
        if self.s1000_keygroup_delete_blocked:
            raise DeviceError(
                "keygroup delete is disabled for this session after an "
                "earlier delete failed its safety check"
            )
        if not s1000_bridge_module.KEYGROUP_DELETE_SUPPORTED:
            raise DeviceError("deleting a keygroup isn't supported on an S1000")

        before = self._s1000_snapshot(program_index)
        target = before[program_index]
        groups = target["groups"]
        if not 0 <= keygroup_index < groups:
            raise ValueError(
                f"keygroup {keygroup_index} is out of range (program has {groups})"
            )
        if groups <= 1:
            raise ValueError(
                "can't delete a program's only keygroup (delete the program instead)"
            )

        to_shift = self._read_keygroups_to_shift_s1000(
            program_index, keygroup_index, groups
        )
        logger.info(
            "S1000 delete keygroup %d of program %d (%d keygroups): shifting %d "
            "down, then DELK of the last (%d). Program pointers before: %s",
            keygroup_index, program_index, groups, len(to_shift) - 1, groups - 1,
            target["pointers"][0].hex(),
        )

        # From here the sampler IS being changed. Any failure leaves a program
        # that is repeated-keygroup at best and unreadable at worst (2026-10-06
        # log: GROUPS stale, the PDATA rewriting it rejected), so it blocks
        # further deletes exactly like a failed safety check - otherwise the
        # tester can retry and compound the damage. No recovery is attempted:
        # a blind KDATA against a stale GROUPS is what linked a keygroup into
        # another program on 2026-10-05.
        try:
            self._shift_keygroups_down_s1000(program_index, keygroup_index, to_shift)
            self._bridge.delete_keygroup(program_index, groups - 1)
            self._repair_groups_after_delk(program_index, groups - 1, groups)
        except Exception as e:
            self.s1000_keygroup_delete_blocked = True
            logger.error("S1000 keygroup delete FAILED part-way: %s", e)
            raise DeviceError(
                f"The delete was only partly applied to program {program_index} "
                f"({e}). Keygroup deleting is now disabled; check that program "
                "on the sampler directly - it may need to be deleted."
            ) from e

        problems = []
        try:
            after = self._s1000_snapshot(program_index)
        except Exception as e:
            problems.append(f"couldn't read the program back ({e})")
            after = {}
        expected = [
            kg for i, kg in enumerate(target["keygroups"]) if i != keygroup_index
        ]
        mine = after.get(program_index)
        if after:
            if mine is None:
                problems.append("the edited program is missing afterwards")
            else:
                if mine["groups"] != groups - 1:
                    problems.append(
                        f"program {program_index} has {mine['groups']} keygroups, "
                        f"expected {groups - 1}"
                    )
                if mine["keygroups"] != expected:
                    problems.append(
                        f"program {program_index}'s remaining keygroups differ "
                        "from what they were before the delete"
                    )
            for index, was in before.items():
                if index == program_index or index not in after:
                    continue
                now = after[index]
                if now["groups"] != was["groups"] or now["keygroups"] != was["keygroups"]:
                    problems.append(f"program {index} changed")
                elif now["pointers"] != was["pointers"]:
                    # content intact but addresses moved: not a failure, but
                    # exactly what a tester's log should show
                    logger.info(
                        "S1000 delete check: program %d's addresses moved "
                        "(content intact)", index,
                    )
        for index, was in before.items():
            logger.info(
                "S1000 delete check: program %d before: pointers %s",
                index, [x.hex() for x in was["pointers"]],
            )
        for index, now in after.items():
            logger.info(
                "S1000 delete check: program %d after: pointers %s",
                index, [x.hex() for x in now["pointers"]],
            )
        if problems:
            self.s1000_keygroup_delete_blocked = True
            message = "; ".join(problems)
            logger.error("S1000 keygroup delete FAILED its safety check: %s", message)
            raise DeviceError(
                f"The sampler's programs didn't come back as expected after "
                f"the delete ({message}). Keygroup deleting is now disabled; "
                "check the programs on the sampler directly."
            )

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

    def _locate_new_program(self, names_before):
        """Index the sampler gave a program we just created with PDATA.

        A PDATA addressed one past the end does NOT necessarily append: the
        sampler keeps its program list ordered (by PRGNUM - a clone carries
        its template's, so it sorts right after it), and measured on a real
        S2000 the new program landed BETWEEN two existing ones. Addressing
        the next KDATA/PDATA at `len(list)` then hit the program that had
        been pushed down to that slot and overwrote ITS keygroups. So re-read
        the list and take the first position where it differs from the one
        before the write. (Callers refuse a name that is already resident,
        so a duplicate name can't make this ambiguous.)
        """
        names_after = list(self._bridge.program_list())
        if len(names_after) != len(names_before) + 1:
            raise DeviceError(
                f"the sampler now lists {len(names_after)} programs, expected "
                f"{len(names_before) + 1} - can't tell where the new program went"
            )
        for index, (after, before) in enumerate(zip(names_after, names_before)):
            if after != before:
                return index
        return len(names_before)

    def _handle_create_program(self, source_index, new_name, first_keygroup_only=False):
        # first_keygroup_only: clone just the template's keygroup 0 instead
        # of every keygroup. Create-program-from-slices uses this on an
        # S1000, where it would otherwise clone N keygroups only to DELK
        # N-1 of them - and an S1000 DELK corrupts the chain (see
        # _delete_keygroup_s1000)
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
            names_before = list(self._bridge.program_list())
            # read every keygroup to clone BEFORE the first write: the
            # insert below can shift the source program's own index
            source_keygroups = [
                self._bridge.get_header_bytes(
                    "keygroup", source_index, 0, 192, selector=keygroup_index
                )
                for keygroup_index in range(
                    1 if first_keygroup_only else group_count
                )
            ]

            header = bytearray(source_header)
            self._patch_field(header, "PRNAME", "program", new_name)
            self._patch_field(header, "GROUPS", "program", 1)
            # sent as "one past the end" = append, but the sampler decides
            # where the program really lands (see _locate_new_program) -
            # every later KDATA/PDATA must use THAT index
            self._send_and_check(
                akai_sysex.build_pdata_request(len(names_before), bytes(header)),
                f"creating program {len(names_before)}",
            )
            new_index = self._locate_new_program(names_before)

            # keygroup 0 bootstrap - same PDATA(groups=1)-then-KDATA(0)
            # order s3000editor's own addProgram() uses for a brand new
            # program (the program has to exist before a keygroup can be
            # written under it)
            self._send_and_check(
                akai_sysex.build_kdata_request(new_index, 0, source_keygroups[0]),
                f"creating keygroup 0 of program {new_index}",
            )

            # every further keygroup: KDATA first, then re-send the whole
            # PDATA with GROUPS incremented - same order/reasoning as
            # _handle_create_keygroup below (s3000editor's own "add
            # keygroup to an existing program" step pair), just reused in
            # a loop since this app clones every keygroup, not just one
            for keygroup_index in range(1, len(source_keygroups)):
                self._send_and_check(
                    akai_sysex.build_kdata_request(
                        new_index, keygroup_index, source_keygroups[keygroup_index]
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

    def _read_program_file(self, program_index):
        # 192 is what an S2000/S3000 block is; an S1000 adapter clips the
        # read to the real (150-byte) block, so the same call serves both
        program = bytes(
            self._bridge.get_header_bytes("program", program_index, 0, 192)
        )
        if len(program) <= akai_program_file._GROUPS_OFFSET:
            raise DeviceError(f"program block is only {len(program)} bytes")
        groups = program[akai_program_file._GROUPS_OFFSET]
        keygroups = [
            bytes(
                self._bridge.get_header_bytes(
                    "keygroup", program_index, 0, 192, selector=k
                )
            )
            for k in range(groups)
        ]
        return akai_program_file.ProgramFile(
            block_size=akai_program_file.validate_blocks(program, keygroups),
            program=program,
            keygroups=keygroups,
        )

    def _handle_export_program(self, program_index):
        try:
            program_file = self._read_program_file(program_index)
        except Exception as e:
            debug_log.get_logger().error(
                "export program %d failed: %s", program_index, e, exc_info=True
            )
            self.program_export_failed.emit(program_index, str(e))
            return
        self.program_exported.emit(program_index, program_file)

    # -- S1000 Delete Keygroup by rebuilding the program ---------------------------
    #
    # DELK on a real S1000 only unlinks the keygroup and leaves GROUPS and the
    # chain inconsistent, and the sampler refuses the GROUPS fix (see
    # _delete_keygroup_s1000). So never DELK: copy the program without the
    # keygroup through the program-file load, then swap. Every step leaves a
    # COMPLETE program: the original is deleted only after the copy has loaded
    # and passed the load's own checks, and the backup .p1 is written first.

    @staticmethod
    def _rebuild_temp_name(name, existing):
        # distinct from every resident name (a clash would make the sampler delete
        # that program) and from the original's; hyphen because AKAI_CHARSET has
        # no underscore. Truncates the original to leave room for the suffix.
        base = name.rstrip()[: NAME_LENGTH - 4]
        for suffix in ("-TMP", "-TM2", "-TM3", "-TM4", "-TM5", "-TM6", "-TM7", "-TM8", "-TM9"):
            candidate = base + suffix
            if candidate not in existing:
                return candidate
        raise DeviceError("couldn't find an unused temporary program name")

    def _save_program_backup(self, program_file, name):
        PROGRAM_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = PROGRAM_BACKUP_DIR / (
            f"{akai_program_file.safe_file_stem(name)}-{stamp}{program_file.extension}"
        )
        path.write_bytes(
            akai_program_file.build_file(program_file.program, program_file.keygroups)
        )
        return path

    def _delete_keygroup_s1000_rebuild(self, program_index, keygroup_index):
        """Returns the rebuilt program's new index. Raises (with a user-facing message) otherwise."""
        logger = debug_log.get_logger()
        if self.program_import_blocked:
            raise DeviceError(
                "program loading is disabled for this session after an earlier "
                "load failed its safety check - check the sampler's programs "
                "directly, then restart the editor"
            )
        names = self._bridge.program_list()
        original_name = names[program_index]
        original = self._read_program_file(program_index)
        groups = len(original.keygroups)
        if not 0 <= keygroup_index < groups:
            raise ValueError(
                f"keygroup {keygroup_index} is out of range (program has {groups})"
            )
        if groups <= 1:
            raise ValueError(
                "can't delete a program's only keygroup (delete the program instead)"
            )

        backup = self._save_program_backup(original, original_name)  # no backup, no change
        logger.info(
            "S1000 delete keygroup %d of program %d %r by rebuild: backup %s",
            keygroup_index, program_index, original_name, backup,
        )
        header = bytearray(original.program)
        self._patch_field(header, "GROUPS", "program", groups - 1)
        edited = akai_program_file.ProgramFile(
            block_size=original.block_size,
            program=bytes(header),
            keygroups=[k for i, k in enumerate(original.keygroups) if i != keygroup_index],
        )
        temp_name = self._rebuild_temp_name(original_name, names)

        # 1. the copy. Until it is verified the original is untouched.
        try:
            self._import_program(edited, temp_name, logger)
        except Exception as e:
            if isinstance(e, _ImportSafetyError):
                self.program_import_blocked = True
            raise DeviceError(
                f"{e}\n\nThe original program \"{original_name}\" was NOT changed "
                f"(a backup is in {backup}). A partly loaded copy named "
                f'"{temp_name}" may be on the sampler - delete it if so.'
            ) from e
        copy_index = self._imported_index

        # 2. delete the original, then check the others came through
        stage = f'"{original_name}" was deleted but'
        try:
            names_now = self._bridge.program_list()
            before = self._s1000_snapshot(copy_index)
            self._bridge.delete_program(program_index)
            expected = [n for i, n in enumerate(names_now) if i != program_index]
            if self._bridge.program_list() != expected:
                raise _ImportSafetyError(
                    "the program list after the delete is not what was expected"
                )
            copy_index -= 1  # the original was before it in the list
            after = self._s1000_snapshot(copy_index)
            problems = []
            for old_index, was in before.items():
                if old_index == program_index:
                    continue
                now = after.get(old_index - (1 if old_index > program_index else 0))
                if now is None:
                    continue
                if (
                    now["groups"] != was["groups"]
                    or now["program"] != was["program"]
                    or now["keygroups"] != was["keygroups"]
                ):
                    problems.append(f"program {old_index} changed")
                elif now["pointers"] != was["pointers"]:
                    logger.info(
                        "S1000 rebuild: program %d's addresses moved after DELP "
                        "(content intact)", old_index,
                    )
            if problems:
                raise _ImportSafetyError("; ".join(problems))

            # 3. the copy takes the original's name (the original is gone, so
            # no clash for the sampler to act on)
            self._bridge.set_parameter(
                p.lookup("PRNAME", "program"), copy_index, original_name
            )
            if self._bridge.program_list()[copy_index] != original_name:
                raise _ImportSafetyError("the rename did not take")
        except Exception as e:
            self.program_import_blocked = True
            raise DeviceError(
                f"{stage} the check afterwards failed ({e}). The program without "
                f'that keygroup is on the sampler as "{temp_name}"; a backup of '
                f"the original is in {backup}. Check the sampler's programs; loading "
                "is disabled for this session."
            ) from e
        logger.info(
            "S1000 delete keygroup by rebuild done: %r is now program %d", original_name, copy_index
        )
        return copy_index

    # -- import a program file as a NEW program --------------------------------
    #
    # Bytes 1-2 of a program block (FIRSTKG) and of a keygroup block (NXTKG) are
    # ADDRESSES in the sampler's memory that the sampler assigns and links
    # itself (the spec calls them "internal use"). A program file carries
    # file-relative stand-ins (150, 300, ...) which on a sampler are addresses
    # of OTHER programs' blocks - and a wrong pointer is exactly what scrambled
    # programs on a real S1000 after DELK (see _delete_keygroup_s1000). Whether a
    # sampler honours the pointer bytes of an incoming PDATA/KDATA is not known
    # (measured only: an appended keygroup keeps the NXTKG it was sent). So
    # nothing here ever sends a pointer value the sampler did not itself hand us:
    # every block goes out with the pointer bytes that slot ALREADY holds, read
    # back from the sampler, and the load stops - before writing any keygroup
    # content - if the sampler's own addresses for the new program alias another
    # program's. Everything is then re-read and compared with a before snapshot.

    #: bytes the sampler is free to compute itself, so a content comparison skips
    #: them: program KGRP1@ (1-2) and TPNUM (43); keygroup NXTKG@ (1-2) and, per
    #: zone, LVXF/HVXF (crossfade factors) and SBADD (the calculated sample
    #: header address) at 54-57 (+24 per zone)
    _IMPORT_PROGRAM_INTERNAL = frozenset({1, 2, 43})
    _IMPORT_KEYGROUP_INTERNAL = frozenset(
        {1, 2} | {54 + 24 * z + d for z in range(4) for d in range(4)}
    )

    #: set after a load failed its safety check; further loads are refused for
    #: the session (like s1000_keygroup_delete_blocked) so a tester can't pile
    #: a second load on top of a damaged state
    program_import_blocked = False

    @staticmethod
    def _address(pointer_bytes):
        return pointer_bytes[0] | (pointer_bytes[1] << 8)

    def _read_block_fresh(self, region, index, selector=0):
        invalidate = getattr(self._bridge, "invalidate", None)
        if invalidate is not None:
            invalidate()
        return bytes(
            self._bridge.get_header_bytes(region, index, 0, 192, selector=selector)
        )

    @staticmethod
    def _with_pointer(block, pointer_bytes):
        out = bytearray(block)
        out[1:3] = pointer_bytes
        return bytes(out)

    @staticmethod
    def _differences(expected, actual, internal):
        return [
            i
            for i in range(max(len(expected), len(actual)))
            if i not in internal
            and (i >= len(expected) or i >= len(actual) or expected[i] != actual[i])
        ]

    def _handle_import_program(self, program_file, new_name):
        logger = debug_log.get_logger()
        name = new_name or program_file.name
        try:
            self._import_program(program_file, name, logger)
        except Exception as e:
            logger.error("import program %r failed: %s", name, e, exc_info=True)
            if isinstance(e, _ImportSafetyError):
                self.program_import_blocked = True
                message = str(e)
            else:
                message = (
                    f"{e} - a partly loaded program may now be on the sampler; "
                    "refresh and delete it if so"
                )
            self.program_import_failed.emit(name, message)
            return
        self._programs_renumbered = False
        self.program_imported.emit(self._imported_index, name)

    def _import_program(self, program_file, name, logger):
        if self.program_import_blocked:
            raise DeviceError(
                "loading programs is disabled for this session after an earlier "
                "load failed its safety check - check the sampler's programs "
                "directly (and send the log), then restart the editor"
            )
        if not hasattr(self._bridge, "send_and_receive"):
            raise RuntimeError(
                "loading a program needs a real hardware connection "
                "(not available in demo mode)"
            )
        existing = self._bridge.program_list()
        if name in existing:
            # PDATA deletes a resident program with the same name first
            raise DeviceError(f'a program named "{name}" is already on the sampler')

        # nothing has been written yet: if the sampler can't be read, refuse
        before = self._s1000_snapshot(0)
        taken = {
            self._address(pointer)
            for snap in before.values()
            for pointer in snap["pointers"][: snap["groups"]]
        }
        keygroups = program_file.keygroups
        block_size = program_file.block_size

        def header_for(groups, first_pointer):
            header = bytearray(program_file.program)
            self._patch_field(header, "PRNAME", "program", name)
            self._patch_field(header, "GROUPS", "program", groups)
            if first_pointer is not None:
                header[1:3] = first_pointer
            return bytes(header)

        # 1. create the program (GROUPS=1, as the measured duplicate flow does).
        # Its pointer bytes are the file's - the one write where there is
        # nothing of the sampler's to carry yet - so check what came of them
        # before any keygroup data follows.
        self._send_and_check(
            akai_sysex.build_pdata_request(len(existing), header_for(1, None)),
            f"creating program {len(existing)}",
        )
        new_index = self._locate_new_program(existing)
        created = self._read_block_fresh("program", new_index)
        if len(created) != block_size:
            raise _ImportSafetyError(
                f"the sampler's program block is {len(created)} bytes, the file's "
                f"is {block_size} - nothing was loaded, but an empty program "
                f'"{name}" may remain; delete it'
            )
        first_pointer = created[1:3]
        logger.info(
            "program load: new program %d FIRSTKG sent %s, sampler holds %s",
            new_index, program_file.program[1:3].hex(), first_pointer.hex(),
        )
        own = {self._address(first_pointer)}
        if self._address(first_pointer) in taken:
            raise _ImportSafetyError(
                f"the sampler gave the new program the first-keygroup address "
                f"{self._address(first_pointer)}, which another program already "
                "uses. Loading stopped BEFORE any keygroup was written. An empty "
                f'program "{name}" may remain - do not edit it; check the other '
                "programs and delete it."
            )

        # 2. keygroup 0 replaces the dummy the sampler made: keep ITS pointer
        slot0 = self._read_block_fresh("keygroup", new_index, selector=0)
        self._send_and_check(
            akai_sysex.build_kdata_request(
                new_index, 0, self._with_pointer(keygroups[0], slot0[1:3])
            ),
            f"creating keygroup 0 of program {new_index}",
        )

        # 3. each further keygroup is appended; its NXTKG is the terminator the
        # previous slot holds (a sampler-made value, not ours), and the previous
        # keygroup's own pointer is re-read afterwards: that is the address the
        # sampler gave the new block - it must not collide with anything
        for index in range(1, len(keygroups)):
            previous = self._read_block_fresh("keygroup", new_index, selector=index - 1)
            self._send_and_check(
                akai_sysex.build_kdata_request(
                    new_index, index, self._with_pointer(keygroups[index], previous[1:3])
                ),
                f"creating keygroup {index} of program {new_index}",
            )
            linked = self._read_block_fresh("keygroup", new_index, selector=index - 1)
            address = self._address(linked[1:3])
            logger.info(
                "program load: keygroup %d placed at %d (previous terminator %d)",
                index, address, self._address(previous[1:3]),
            )
            if address in taken or address in own:
                raise _ImportSafetyError(
                    f"keygroup {index + 1} was linked at address {address}, which "
                    f"{'this program' if address in own else 'another program'} "
                    "already uses. Loading stopped; the new program is incomplete "
                    f'("{name}") - do not edit it; check the other programs and '
                    "delete it."
                )
            own.add(address)
            current = self._read_block_fresh("program", new_index)
            self._send_and_check(
                akai_sysex.build_pdata_request(
                    new_index, header_for(index + 1, current[1:3])
                ),
                f"updating program {new_index} groups={index + 1}",
            )

        # 4. read everything back and compare
        after = self._s1000_snapshot(new_index)
        problems = []
        mine = after.get(new_index)
        if mine is None:
            problems.append("the new program can't be read back")
        else:
            if mine["groups"] != len(keygroups):
                problems.append(
                    f"it has {mine['groups']} keygroups, the file has {len(keygroups)}"
                )
            actual_program = self._read_block_fresh("program", new_index)
            # the name (3-14) is ours, possibly renamed at load time
            name_bytes = frozenset(range(3, 15))
            diffs = self._differences(
                program_file.program,
                actual_program,
                self._IMPORT_PROGRAM_INTERNAL | name_bytes,
            )
            if diffs:
                problems.append(f"the program header differs at bytes {diffs}")
            for index in range(min(len(keygroups), mine["groups"])):
                actual = self._read_block_fresh("keygroup", new_index, selector=index)
                diffs = self._differences(
                    keygroups[index], actual, self._IMPORT_KEYGROUP_INTERNAL
                )
                if diffs:
                    problems.append(f"keygroup {index + 1} differs at bytes {diffs}")
                logger.info(
                    "program load: keygroup %d NXTKG %s, zone SBADD read back %s "
                    "(file had %s)",
                    index, actual[1:3].hex(),
                    [actual[56 + 24 * z : 58 + 24 * z].hex() for z in range(4)],
                    [keygroups[index][56 + 24 * z : 58 + 24 * z].hex() for z in range(4)],
                )
            chain = [self._address(pointer) for pointer in mine["pointers"][: mine["groups"]]]
            if chain != sorted(set(chain)):
                problems.append(f"its keygroup addresses are not in order: {chain}")
            if set(chain) & taken:
                problems.append("its keygroup addresses overlap another program's")
        for index, was in before.items():
            # the new program may have been slotted in BEFORE this one (the sampler keeps its list ordered), moving it down a place
            now = after.get(index if index < new_index else index + 1)
            if now is None:
                continue
            if (
                now["groups"] != was["groups"]
                or now["keygroups"] != was["keygroups"]
                or now["program"] != was["program"]
                or now["pointers"] != was["pointers"]
            ):
                problems.append(f"program {index} was changed by the load")
        if problems:
            raise _ImportSafetyError(
                f'"{name}" was loaded but did not check out afterwards: '
                + "; ".join(problems)
                + ". Do not trust it or the other programs until you have "
                "checked them on the sampler; loading is disabled for this session."
            )
        self._imported_index = new_index

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
