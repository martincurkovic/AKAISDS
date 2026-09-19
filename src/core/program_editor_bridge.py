from core import app_config
from s3k.bridge import S3kBridge
from PySide6.QtCore import QThread, Signal
import s3k.params as p


def connect():
    _input_name, output_name = app_config.get_saved_ports()
    return S3kBridge.standard(output_name)  # type: ignore


class KeygroupLoader(QThread):
    keygroups_loaded = Signal(int, list, dict)  # program_index, ranges, program_values
    load_failed = Signal(int, str)

    def __init__(self, bridge, program_index):
        super().__init__()
        self._bridge = bridge
        self._program_index = program_index

    def run(self):
        # runs on background thread
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
            }
            keygroup_index = 0
            keygroup_ranges = []
            while True:
                try:
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
                except ValueError:
                    break  # ran past the last real keygroup for this program
                keygroup_ranges.append(f"{lo} - {hi}")
                keygroup_index += 1
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
