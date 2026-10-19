# core/akai_multi_file.py (the 4096-byte multi file), the worker's Save Multi read, the Multi tab's Save button and tools/s2000_multi_check.py.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox, QWidget
from s3ked.demo import DemoBridge

from core import akai_multi_file as amf
from core.program_editor_bridge import BridgeWorker, LoggingBridge
from test_program_editor_window import FakeBridge, _pump_until, _wait_for_program_load, qapp  # noqa: F401


def make_blocks(seed=0):
    """A wire header and 16 distinguishable part blocks (name, channel, pointers, last note all set)."""
    header = bytearray(amf.WIRE_HEADER_SIZE)
    header[0:3] = b"\x00\x00\x00"
    header[3:15] = bytes(range(10, 22))  # a name in the sampler's own character codes
    header[16:20] = bytes([1, 2, 3, 204])
    parts = []
    for n in range(amf.PART_COUNT):
        block = bytearray(amf.PART_SIZE)
        block[0] = 0  # (the wire's byte 0 is not known; the demo bridge sends 0)
        block[1:3] = bytes([0x0C + n, 0x90])
        block[3:15] = bytes((n + i + seed) % 40 for i in range(12))
        block[16] = n if n != 15 else 255  # PMCHAN
        block[23] = 99 - n  # the Lev field
        block[0x6D] = 60 + n
        block[0xBE:0xC0] = bytes([0x12, 0x34])
        parts.append(bytes(block))
    return bytes(header), parts


def test_a_multi_file_is_4096_bytes_with_the_header_then_16_parts():
    header, parts = make_blocks()
    data = amf.build_file(header, parts)
    assert len(data) == amf.MULTI_FILE_SIZE == 4096
    assert data[:32] == header and data[32:1024] == bytes(992)
    for n in range(16):
        record = data[0x400 + n * 0xC0 : 0x400 + (n + 1) * 0xC0]
        assert len(record) == 192
        # the part is the wire block with exactly three things normalised
        expected = bytearray(parts[n])
        expected[0] = 0x01
        expected[0xBE:0xC0] = b"\xff\xff"
        assert record == bytes(expected)


def test_the_pointer_and_last_note_bytes_are_kept_as_the_sampler_reported_them():
    header, parts = make_blocks()
    data = amf.build_file(header, parts)
    part3 = data[0x400 + 2 * 0xC0 : 0x400 + 3 * 0xC0]
    assert part3[1:3] == parts[2][1:3] and part3[0x6D] == parts[2][0x6D]
    assert part3[0xBE:0xC0] == b"\xff\xff"  # but the end link is "unassigned", as a third-party note says it must be


def test_a_saved_file_reads_back():
    header, parts = make_blocks()
    multi = amf.parse_file(amf.build_file(header, parts))
    assert multi.header == header and multi.fx == (1, 2, 3, 204)
    assert multi.part_channel(0) == 0 and multi.part_channel(15) == 255 and multi.part_channel(7) == 7
    assert len(multi.parts) == 16 and multi.extra_header == bytes(992)
    # build -> parse -> build is stable
    assert amf.build_file(multi.header, multi.parts, multi.extra_header) == amf.build_file(header, parts)


def test_names_are_decoded_with_the_sampler_character_set():
    bridge = DemoBridge()
    multi = amf.read_from_bridge(bridge)
    assert multi.name == str(bridge.get_parameter(amf.p.lookup("MULTINAME", "multi"), 0)).rstrip()
    assert all(isinstance(multi.part_name(i), str) for i in range(16))


def test_the_extra_header_of_a_real_file_is_kept_when_parsing_and_rebuilding():
    header, parts = make_blocks()
    data = bytearray(amf.build_file(header, parts))
    data[0x100:0x104] = b"\x07\x08\x09\x0a"
    multi = amf.parse_file(bytes(data))
    assert multi.extra_header[0x100 - 32 : 0x104 - 32] == b"\x07\x08\x09\x0a"
    assert amf.build_file(multi.header, multi.parts, multi.extra_header) == bytes(data)


@pytest.mark.parametrize(
    "header, parts",
    [
        (bytes(31), [bytes(192)] * 16),
        (bytes(32), [bytes(192)] * 15),
        (bytes(32), [bytes(192)] * 15 + [bytes(191)]),
    ],
)
def test_wrong_sized_blocks_are_refused(header, parts):
    with pytest.raises(amf.MultiFileError):
        amf.build_file(header, parts)


@pytest.mark.parametrize("size", [0, 100, 4095, 4097, 8192])
def test_only_a_4096_byte_file_parses(size):
    with pytest.raises(amf.MultiFileError, match="4096"):
        amf.parse_file(bytes(size))


def test_parsing_does_not_repair_or_clamp_what_a_file_holds():
    data = bytearray(4096)
    data[16:20] = bytes([250, 250, 250, 250])  # FX bytes above the safe 204
    multi = amf.parse_file(bytes(data))
    assert multi.fx == (250, 250, 250, 250)  # a loader must clamp; the codec reports what is there
    assert amf.FX_MAX == 204


def test_safe_file_stem():
    assert amf.safe_file_stem("MY MULTI") == "MY MULTI"
    assert amf.safe_file_stem("A/B:C") == "A_B_C"
    assert amf.safe_file_stem("  ") == amf.DEFAULT_NAME


# -- comparing ----------------------------------------------------------------------------------------------------------


def test_identical_multis_compare_equal_and_the_ignored_bytes_do_not_count():
    header, parts = make_blocks()
    a = amf.parse_file(amf.build_file(header, parts))
    other = [bytearray(p) for p in parts]
    for block in other:
        block[1:3] = b"\xaa\xbb"  # link pointer
        block[0x6D] = 1  # last note played
    b = amf.parse_file(amf.build_file(header, [bytes(x) for x in other]))
    assert amf.compare(a, b) == []


def test_a_differing_setting_is_named():
    header, parts = make_blocks()
    a = amf.parse_file(amf.build_file(header, parts))
    changed = [bytearray(p) for p in parts]
    changed[2][16] = 9  # PMCHAN of part 3
    changed[4][0x47] = 5  # a byte the app does not model
    b = amf.parse_file(amf.build_file(header, [bytes(x) for x in changed]))
    lines = amf.compare(a, b)
    assert any("part 3" in line and "PMCHAN" in line and "2 vs 9" in line for line in lines)
    assert any("part 5" in line and "0x47" in line and "not modelled" in line for line in lines)
    assert len(lines) == 2


def test_header_differences_are_named_including_bytes_sysex_cannot_read():
    header, parts = make_blocks()
    a = amf.parse_file(amf.build_file(header, parts))
    other_header = bytearray(header)
    other_header[17] = 9  # FX2
    data = bytearray(amf.build_file(bytes(other_header), parts))
    data[0x200] = 1
    b = amf.parse_file(bytes(data))
    lines = amf.compare(a, b)
    assert any("FX2" in line for line in lines)
    assert any("beyond the 32 SysEx can read" in line and "0x200" in line for line in lines)


def test_describe_part_offset_uses_the_s3k_field_names():
    assert "PMCHAN" in amf.describe_part_offset(0x10) and "PRNAME" in amf.describe_part_offset(0x05)
    assert "not modelled" in amf.describe_part_offset(0x47)


# -- reading from the sampler ---------------------------------------------------------------------------------------------


class RecordingBridge:
    def __init__(self, inner):
        self.inner = inner
        self.calls = []

    def get_header_bytes(self, region, index, offset, count, **kwargs):
        self.calls.append((region, index, offset, count))
        return self.inner.get_header_bytes(region, index, offset, count, **kwargs)


def test_reading_asks_only_for_the_header_and_parts_0_to_15_one_request_each():
    recording = RecordingBridge(DemoBridge())
    multi = amf.read_from_bridge(recording)
    assert recording.calls == [("multi", 0, 0, 32)] + [("multipart", i, 0, 192) for i in range(16)]
    assert len(multi.parts) == 16 and len(multi.header) == 32


def test_a_short_answer_is_refused_not_saved():
    class Short(RecordingBridge):
        def get_header_bytes(self, region, index, offset, count, **kwargs):
            return super().get_header_bytes(region, index, offset, count, **kwargs)[:-1] if region == "multipart" and index == 4 else super().get_header_bytes(region, index, offset, count, **kwargs)

    with pytest.raises(amf.MultiFileError, match="part 5"):
        amf.read_from_bridge(Short(DemoBridge()))


def test_the_worker_exports_the_resident_multi(qapp):
    worker = BridgeWorker(LoggingBridge(DemoBridge()))
    got, failed = [], []
    worker.multi_exported.connect(got.append)
    worker.multi_export_failed.connect(failed.append)
    worker.submit_export_multi()
    worker.process_pending()
    assert failed == [] and len(got) == 1 and len(got[0].parts) == 16


def test_the_worker_reports_a_failed_read(qapp):
    class Dead:
        def get_header_bytes(self, *args, **kwargs):
            raise RuntimeError("no reply")

    worker = BridgeWorker(Dead())
    got, failed = [], []
    worker.multi_exported.connect(got.append)
    worker.multi_export_failed.connect(failed.append)
    worker.submit_export_multi()
    worker.process_pending()
    assert got == [] and failed == ["no reply"]


# -- the Multi tab --------------------------------------------------------------------------------------------------------


class FakeBridgeWithMulti(FakeBridge):
    """FakeBridge (192-byte program reads only) plus the multi blocks."""

    def __init__(self):
        super().__init__()
        self.multi_header, self.multi_parts = make_blocks()
        self.multi_reads = []

    def get_header_bytes(self, region, index, offset, count, selector=0, **kwargs):
        if region == "multi":
            self.multi_reads.append((region, index))
            return self.multi_header[offset : offset + count]
        if region == "multipart":
            self.multi_reads.append((region, index))
            return self.multi_parts[index][offset : offset + count]
        return super().get_header_bytes(region, index, offset, count, selector, **kwargs)


@pytest.fixture
def editor(qapp):
    from ui.program_editor_window import ProgramEditorWindow

    editor = ProgramEditorWindow(QWidget(), bridge=FakeBridgeWithMulti())
    _wait_for_program_load(editor, qapp)
    yield editor
    editor._worker.stop()
    editor._worker.wait()


def test_the_multi_tab_has_a_save_button_and_the_file_menu_an_entry(editor):
    assert editor.save_multi_button.text() == "Save to File..." and editor.save_multi_button.isEnabled()
    file_menu = next(a.menu() for a in editor.menuBar().actions() if a.text() == "&File")
    assert editor._save_multi_action in file_menu.actions()
    assert editor._save_multi_action.isEnabled()


def test_save_multi_writes_a_4096_byte_file_from_a_read_only_request_sequence(editor, qapp, tmp_path, monkeypatch):
    target = tmp_path / "MINE.m3"
    monkeypatch.setattr("ui.program_editor_window.QFileDialog.getSaveFileName", lambda *a, **k: (str(target), ""))
    bridge = editor._bridge
    editor._save_multi_to_file()
    _pump_until(qapp, lambda: target.exists())
    data = target.read_bytes()
    assert len(data) == 4096
    saved = amf.parse_file(data)
    assert saved.header == bridge.multi_header and saved.fx == (1, 2, 3, 204)
    assert bridge.multi_reads == [("multi", 0)] + [("multipart", i) for i in range(16)]
    assert "Saved multi" in editor.status_bar.currentMessage() and "MINE.m3" in editor.status_bar.currentMessage()


def test_cancelling_the_save_dialog_writes_nothing(editor, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("ui.program_editor_window.QFileDialog.getSaveFileName", lambda *a, **k: ("", ""))
    editor._save_multi_to_file()
    _pump_until(qapp, lambda: "cancelled" in editor.status_bar.currentMessage())
    assert list(tmp_path.iterdir()) == []


def test_a_failed_read_is_reported(editor, qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append((title, text)))
    editor._bridge.get_header_bytes = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no reply"))
    editor._save_multi_to_file()
    _pump_until(qapp, lambda: shown)
    assert shown[0][0] == "Save Multi" and "no reply" in shown[0][1]
    assert not editor._pending_multi_export


def test_a_second_click_while_reading_does_not_queue_another_read(editor, qapp, monkeypatch):
    monkeypatch.setattr("ui.program_editor_window.QFileDialog.getSaveFileName", lambda *a, **k: ("", ""))
    editor._pending_multi_export = True
    editor._save_multi_to_file()
    editor._worker.wait_until_idle()
    assert editor._bridge.multi_reads == []
    editor._pending_multi_export = False


def test_save_multi_is_off_while_a_blocking_job_runs(editor):
    editor._set_hardware_busy_ui(True)
    assert not editor._save_multi_action.isEnabled() and not editor.save_multi_button.isEnabled()
    editor._set_hardware_busy_ui(False)
    assert editor._save_multi_action.isEnabled() and editor.save_multi_button.isEnabled()


# -- the check tool ---------------------------------------------------------------------------------------------------------


def test_the_check_tool_shows_diffs_and_compares_files_offline(tmp_path, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "s2000_multi_check", os.path.join(os.path.dirname(__file__), "..", "tools", "s2000_multi_check.py")
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    header, parts = make_blocks()
    a = tmp_path / "a.m3"
    b = tmp_path / "b.m3"
    a.write_bytes(amf.build_file(header, parts))
    changed = [bytearray(p) for p in parts]
    changed[0][16] = 7
    b.write_bytes(amf.build_file(header, [bytes(x) for x in changed]))
    assert tool.main(["show", str(a)]) == 0
    assert "part  1: program" in capsys.readouterr().out
    assert tool.main(["diff", str(a), str(a)]) == 0
    assert "IDENTICAL" in capsys.readouterr().out
    assert tool.main(["diff", str(a), str(b)]) == 1
    out = capsys.readouterr().out
    assert "1 DIFFERENCE(S)" in out and "part 1" in out and "PMCHAN" in out
    assert tool.main([]) == 2
