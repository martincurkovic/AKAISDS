import os

from core import app_config
from s3k.bridge import S3kBridge
from PySide6.QtCore import QThread, Signal
import s3k.params as p


def connect():
    # lets the editor be developed away from the hardware sampler - same
    # dummy sampler s3ked itself ships for its --demo flag, duck-typing the
    # slice of S3kBridge this module's loaders/writers actually call
    if os.environ.get("AKAISDS_DEMO_SAMPLER"):
        from s3ked.demo import DemoBridge

        return DemoBridge()
    _input_name, output_name = app_config.get_saved_ports()
    return S3kBridge.standard(output_name)  # type: ignore


class ProgramListLoader(QThread):
    programs_loaded = Signal(list)
    load_failed = Signal(str)

    def __init__(self, bridge):
        super().__init__()
        self._bridge = bridge

    def run(self):
        try:
            programs = self._bridge.program_list()
        except Exception as e:
            self.load_failed.emit(str(e))
            return
        self.programs_loaded.emit(programs)


class SampleListLoader(QThread):
    # fetches the full list of sample names currently resident in the
    # sampler's memory - global, not per-program or per-keygroup, so
    # this runs once when the editor opens rather than on every selection
    samples_loaded = Signal(list)
    load_failed = Signal(str)

    def __init__(self, bridge):
        super().__init__()
        self._bridge = bridge

    def run(self):
        try:
            samples = self._bridge.sample_list()
        except Exception as e:
            self.load_failed.emit(str(e))
            return
        self.samples_loaded.emit(samples)


class KeygroupLoader(QThread):
    # program_index, [(lo_note, hi_note), ...] per keygroup, program_values
    keygroups_loaded = Signal(int, list, dict)
    load_failed = Signal(int, str)

    def __init__(self, bridge, program_index):
        super().__init__()
        self._bridge = bridge
        self._program_index = program_index

    def run(self):
        # runs on background thread
        keygroup_ranges = []
        try:
            program_values = {
                "PANPOS": self._bridge.get_parameter(
                    p.lookup("PANPOS", "program"), self._program_index
                ),
                "LFORAT": self._bridge.get_parameter(
                    p.lookup("LFORAT", "program"), self._program_index
                ),
                "LFODEP": self._bridge.get_parameter(
                    p.lookup("LFODEP", "program"), self._program_index
                ),
                "LFODEL": self._bridge.get_parameter(
                    p.lookup("LFODEL", "program"), self._program_index
                ),
                "LFO1WAVE": self._bridge.get_parameter(
                    p.lookup("LFO1WAVE", "program"), self._program_index
                ),
                "POLYPH": self._bridge.get_parameter(
                    p.lookup("POLYPH", "program"), self._program_index
                ),
            }
            # read the real keygroup count off the program header rather than
            # probing until an out-of-range read fails: the real bridge signals
            # that with ValueError, but s3ked's DemoBridge raises its own
            # DemoError, so probing silently dropped every keygroup when
            # running against the demo sampler
            group_count = self._bridge.get_parameter(
                p.lookup("GROUPS", "program"), self._program_index
            )
            for keygroup_index in range(group_count):
                lo = self._bridge.get_parameter(
                    p.lookup("LONOTE", "keygroup"),
                    self._program_index,
                    keygroup=keygroup_index,
                )
                hi = self._bridge.get_parameter(
                    p.lookup("HINOTE", "keygroup"),
                    self._program_index,
                    keygroup=keygroup_index,
                )
                keygroup_ranges.append((lo, hi))
        except Exception as e:
            self.load_failed.emit(self._program_index, str(e))
            return
        self.keygroups_loaded.emit(self._program_index, keygroup_ranges, program_values)


class KeygroupDetailLoader(QThread):
    detail_loaded = Signal(int, int, dict)  # program_index, keygroup_index, values
    load_failed = Signal(int, int, str)

    _FIELDS = [
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

    def __init__(self, bridge, program_index, keygroup_index):
        super().__init__()
        self._bridge = bridge
        self._program_index = program_index
        self._keygroup_index = keygroup_index

    def run(self):
        values = {}
        try:
            for field in self._FIELDS:
                values[field] = self._bridge.get_parameter(
                    p.lookup(field, "keygroup"),
                    self._program_index,
                    keygroup=self._keygroup_index,
                )
        except Exception as e:
            self.load_failed.emit(self._program_index, self._keygroup_index, str(e))
            return
        self.detail_loaded.emit(self._program_index, self._keygroup_index, values)


class ParameterWriter(QThread):
    write_succeeded = Signal(
        object
    )  # new_value = int for numeric params, str for text params
    write_failed = Signal(str)

    def __init__(
        self, bridge, param_name, region, program_index, new_value, *, keygroup_index=0
    ):
        super().__init__()
        self._bridge = bridge
        self._param_name = param_name
        self._region = region
        self._program_index = program_index
        self._keygroup_index = keygroup_index
        self._new_value = new_value

    def run(self):
        try:
            param = p.lookup(self._param_name, self._region)
            self._bridge.set_parameter(
                param,
                self._program_index,
                self._new_value,
                keygroup=self._keygroup_index,
            )
        except Exception as e:
            self.write_failed.emit(str(e))
            return
        self.write_succeeded.emit(self._new_value)
