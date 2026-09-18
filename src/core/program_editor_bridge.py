from core import app_config
from s3k.bridge import S3kBridge
from PySide6.QtCore import QThread, Signal
import s3k.params as p


def connect():
    _input_name, output_name = app_config.get_saved_ports()
    return S3kBridge.standard(output_name)  # type: ignore


class KeygroupLoader(QThread):
    keygroups_loaded = Signal(int, list)
    load_failed = Signal(int, str)

    def __init__(self, bridge, program_index):
        super().__init__()
        self._bridge = bridge
        self._program_index = program_index

    def run(self):
        # runs on background thread
        keygroup_ranges = []
        keygroup_index = 0
        try:
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
        self.keygroups_loaded.emit(self._program_index, keygroup_ranges)
