# core/akai_sample_file.py (the .s1/.s3 sample file), a receive saving it, the Dashboard's format choice, the Samples tab's "Save as Akai File" and
# tools/s2000_sample_check.py.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox

from core import akai_sample_file as asf
from core import akai_sysex
from core.program_editor_bridge import BridgeWorker
from test_program_editor_window import _pump_until, editor, qapp  # noqa: F401
from test_staged_edits import _MARKERS, _ORIGINAL, staged_editor  # noqa: F401


def make_header(length=6, size=192, name="KICK", rate=44100, **overrides):
    block = bytearray(size)
    block[0] = 3
    block[1] = 1  # SBANDW
    block[2] = 60  # SPITCH
    block[3:15] = akai_sysex.encode_name(name)
    block[15] = 0x80
    block[19] = 2  # SPTYPE
    block[20:22] = (0xFF00).to_bytes(2, "little")  # STUNO -1.00 st (raw 8.8 two's complement)
    block[22:26] = b"\x11\x22\x33\x44"  # SLOCAT: an address the sampler assigned
    block[26:30] = length.to_bytes(4, "little")
    block[30:34] = (1).to_bytes(4, "little")
    block[34:38] = max(length - 2, 0).to_bytes(4, "little")
    block[38:42] = max(length - 1, 0).to_bytes(4, "little")  # LOOPAT1
    block[42:48] = (3 * 65536).to_bytes(6, "little")  # LLNGTH1
    block[48:50] = (9999).to_bytes(2, "little")
    if size >= 138:
        block[136:138] = b"\x55\x66"  # SSPAIR
    block[138:140] = rate.to_bytes(2, "little")
    block[140] = 7  # SHLTO
    for offset, value in overrides.items():
        block[offset] = value
    return bytes(block)


SAMPLES = [100, -100, 2000, -2000, 32767, -32768]


# -- the codec ---------------------------------------------------------------------------------------------------------


def test_a_sample_file_is_the_header_then_16_bit_little_endian_words():
    header = make_header()
    data = asf.build_file(header, SAMPLES)
    assert len(data) == 192 + 2 * 6
    assert data[192:] == b"".join(int(v).to_bytes(2, "little", signed=True) for v in SAMPLES)


def test_everything_in_the_header_travels_except_the_two_sampler_assigned_addresses():
    header = make_header()
    data = asf.build_file(header, SAMPLES)
    written = data[:192]
    assert written[22:26] == bytes(4) and written[136:138] == bytes(2)  # SLOCAT and SSPAIR: unassigned
    for offset in range(192):
        if offset not in asf.IGNORED_OFFSETS:
            assert written[offset] == header[offset], asf.describe_offset(offset)  # loops, tune, root note, unmodelled bytes: as read


def test_a_saved_file_reads_back():
    sample = asf.parse_file(asf.build_file(make_header(name="SNARE 01", rate=22050), SAMPLES))
    assert sample.name == "SNARE 01" and sample.sample_rate == 22050 and sample.length == 6
    assert sample.samples == SAMPLES and sample.extension == ".s3" and sample.family == "s2000_s3000"
    assert sample.header[140] == 7  # SHLTO


def test_an_s1000_header_makes_an_s1_file():
    data = asf.build_file(make_header(size=150), SAMPLES)
    assert len(data) == 150 + 12
    sample = asf.parse_file(data)
    assert sample.extension == ".s1" and sample.family == "s1000" and sample.samples == SAMPLES


def test_the_audio_must_be_as_long_as_the_header_says():
    with pytest.raises(asf.SampleFileError, match="6 words but 5"):
        asf.build_file(make_header(), SAMPLES[:5])


@pytest.mark.parametrize("size", [0, 100, 191, 193, 400])
def test_only_the_two_header_sizes_are_accepted(size):
    with pytest.raises(asf.SampleFileError):
        asf.build_file(bytes(size), [])


def test_audio_that_is_not_16_bit_is_refused():
    with pytest.raises(asf.SampleFileError, match="16-bit"):
        asf.build_file(make_header(length=1), [70000])


def test_a_file_padded_to_a_block_size_parses_when_padding_is_allowed():
    data = asf.build_file(make_header(), SAMPLES)
    padded = data + bytes(1024 - len(data))
    with pytest.raises(asf.SampleFileError):
        asf.parse_file(padded)  # strict by default: padding is unconfirmed (the check tool will say whether a real file has any)
    sample = asf.parse_file(padded, allow_padding=True)
    assert sample.samples == SAMPLES and sample.block_size == 192


def test_the_header_size_is_worked_out_from_the_audio_length():
    # SLNGTH sits at the same offset in both headers; the exact total decides
    assert asf.parse_file(asf.build_file(make_header(size=150), SAMPLES)).block_size == 150
    assert asf.parse_file(asf.build_file(make_header(size=192), SAMPLES)).block_size == 192


@pytest.mark.parametrize("data", [b"", bytes(100), bytes(192), bytes(192) + bytes(10)])
def test_bytes_that_are_not_a_sample_file_are_refused(data):
    with pytest.raises(asf.SampleFileError, match="doesn't look like"):
        asf.parse_file(data)


def test_a_truncated_file_is_refused():
    data = asf.build_file(make_header(), SAMPLES)
    with pytest.raises(asf.SampleFileError):
        asf.parse_file(data[:-2])


def test_markers_can_be_filled_in_for_edited_audio():
    header = asf.with_markers(make_header(), frames=1000, start=10, end=900, loop_start=100, loop_end=500)
    sample = asf.SampleFile(header=header)
    assert sample.length == 1000
    assert int.from_bytes(header[30:34], "little") == 10 and int.from_bytes(header[34:38], "little") == 900
    assert int.from_bytes(header[38:42], "little") == 500  # LOOPAT1 is the loop's END
    assert int.from_bytes(header[42:48], "little") == 400 * 65536  # LLNGTH1 is 32.16 fixed point, measured back from LOOPAT1
    original = make_header()
    for offset in range(192):  # nothing else changed
        if not (26 <= offset < 48):
            assert header[offset] == original[offset]


def test_safe_file_stem():
    assert asf.safe_file_stem("SNARE 01") == "SNARE 01" and asf.safe_file_stem("A/B") == "A_B" and asf.safe_file_stem(" ") == "SAMPLE"


def test_headers_compare_ignoring_the_addresses_and_naming_the_rest():
    a = make_header()
    assert asf.compare_headers(asf.normalized_header(a), a) == []
    b = bytearray(a)
    b[20] = 1  # STUNO
    b[145] = 9  # not modelled
    lines = asf.compare_headers(a, bytes(b))
    assert any("STUNO" in line for line in lines) and any("145 (not modelled)" in line for line in lines) and len(lines) == 2
    assert asf.compare_headers(a, a[:150]) and "lengths differ" in asf.compare_headers(a, a[:150])[0]


# -- the worker's header read -------------------------------------------------------------------------------------------


class HeaderBridge:
    def __init__(self, header):
        self.header = header
        self.calls = []

    def get_header_bytes(self, region, index, offset, count, **kwargs):
        self.calls.append((region, index, offset, count))
        return self.header[offset : offset + count]


def test_the_worker_reads_one_sample_header(qapp):
    bridge = HeaderBridge(make_header())
    worker = BridgeWorker(bridge)
    got, failed = [], []
    worker.sample_header_exported.connect(lambda i, h: got.append((i, h)))
    worker.sample_header_export_failed.connect(lambda i, e: failed.append((i, e)))
    worker.submit_export_sample_header(4)
    worker.process_pending()
    assert failed == [] and got == [(4, make_header())]
    assert bridge.calls == [("sample", 4, 0, 192)]  # one request


def test_the_worker_reports_a_header_of_the_wrong_size(qapp):
    worker = BridgeWorker(HeaderBridge(bytes(100)))
    failed = []
    worker.sample_header_export_failed.connect(lambda i, e: failed.append((i, e)))
    worker.submit_export_sample_header(0)
    worker.process_pending()
    assert failed and failed[0][0] == 0 and "100-byte" in failed[0][1]


# -- the Samples tab -------------------------------------------------------------------------------------------------------


def _answer_header(editor, header):
    editor._bridge.get_header_bytes = lambda region, index, offset, count, selector=0, **kw: header[offset : offset + count]


def test_save_as_akai_file_writes_the_samplers_header_unchanged_for_an_untouched_sample(staged_editor, qapp, tmp_path, monkeypatch):
    editor = staged_editor
    header = make_header(length=100, name="SQUARE")
    _answer_header(editor, header)
    target = tmp_path / "SQUARE.s3"
    monkeypatch.setattr("ui.program_editor_window.QFileDialog.getSaveFileName", lambda *a, **k: (str(target), ""))
    editor._save_sample_as_akai_file()
    _pump_until(qapp, lambda: target.exists())
    sample = asf.parse_file(target.read_bytes())
    assert sample.samples == _ORIGINAL and sample.name == "SQUARE"
    assert sample.header == asf.normalized_header(header)  # exactly what the sampler has, minus its addresses
    assert "Saved" in editor.status_bar.currentMessage() and "SQUARE.s3" in editor.status_bar.currentMessage()


def test_a_working_copy_goes_out_with_its_own_length_and_markers(staged_editor, qapp, tmp_path, monkeypatch):
    editor = staged_editor
    header = make_header(length=100, name="SQUARE")
    _answer_header(editor, header)
    editor._confirm_reverse_sample()  # a working copy, same length
    editor.waveform_view.set_marker("start", 20)  # (a local marker edit while staged)
    markers = editor.waveform_view.markers_with_loop_in_range()
    target = tmp_path / "SQUARE.s3"
    monkeypatch.setattr("ui.program_editor_window.QFileDialog.getSaveFileName", lambda *a, **k: (str(target), ""))
    editor._save_sample_as_akai_file()
    _pump_until(qapp, lambda: target.exists())
    sample = asf.parse_file(target.read_bytes())
    assert sample.samples == list(reversed(_ORIGINAL))
    assert int.from_bytes(sample.header[30:34], "little") == markers["start"] == 20
    assert sample.header[140] == 7 and sample.header[20:22] == make_header()[20:22]  # tune and the rest are the sampler's


def test_cancelling_writes_nothing_and_a_short_audio_is_refused(staged_editor, qapp, tmp_path, monkeypatch):
    editor = staged_editor
    _answer_header(editor, make_header(length=100))
    monkeypatch.setattr("ui.program_editor_window.QFileDialog.getSaveFileName", lambda *a, **k: ("", ""))
    editor._save_sample_as_akai_file()
    _pump_until(qapp, lambda: "cancelled" in editor.status_bar.currentMessage())
    assert list(tmp_path.iterdir()) == []


def test_a_failed_header_read_is_reported(staged_editor, qapp, monkeypatch):
    editor = staged_editor
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append((title, text)))
    editor._bridge.get_header_bytes = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no reply"))
    editor._save_sample_as_akai_file()
    _pump_until(qapp, lambda: shown)
    assert shown[0][0] == "Save as Akai File" and "no reply" in shown[0][1]
    assert editor._pending_akai_sample_export is None


def test_the_button_needs_audio_and_sits_in_the_more_row(staged_editor):
    editor = staged_editor
    button, action = editor._sample_edit_controls["save_akai"]
    assert button.text() == "Save as Akai File…" and button.isEnabled() and editor._sample_control_wanted["save_akai"]
    assert action.text() == button.text()  # (the menu action is only enabled on the Samples tab: see _update_sample_actions_enabled)
    from ui.program_editor_window import _EDIT_SAMPLE_ROWS

    assert "save_akai" in [k for group in _EDIT_SAMPLE_ROWS[1][2] for k in group]
    editor._set_sample_edit_buttons_enabled(False)
    assert not button.isEnabled()


# -- the check tool ---------------------------------------------------------------------------------------------------------


def test_the_check_tool_shows_and_diffs_files_offline(tmp_path, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "s2000_sample_check", os.path.join(os.path.dirname(__file__), "..", "tools", "s2000_sample_check.py")
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    a, b = tmp_path / "a.s3", tmp_path / "b.s3"
    a.write_bytes(asf.build_file(make_header(), SAMPLES))
    b.write_bytes(asf.build_file(make_header(**{"20": 0}) if False else make_header(), SAMPLES) + bytes(10))  # padded
    changed = bytearray(make_header())
    changed[19] = 0  # SPTYPE
    c = tmp_path / "c.s3"
    c.write_bytes(asf.build_file(bytes(changed), SAMPLES))
    assert tool.main(["show", str(a)]) == 0
    out = capsys.readouterr().out
    assert "KICK" in out and "(exactly)" in out
    assert tool.main(["show", str(b)]) == 0
    assert "+10 bytes" in capsys.readouterr().out
    assert tool.main(["diff", str(a), str(b)]) == 0 and "IDENTICAL" in capsys.readouterr().out
    assert tool.main(["diff", str(a), str(c)]) == 1
    assert "1 DIFFERENCE(S)" in capsys.readouterr().out
    assert tool.main([]) == 2


# -- an S1000 (the fake one: real S1000 adapter, 150-byte blocks) ---------------------------------------------------------


def test_save_as_akai_file_on_an_s1000_writes_an_s1_file(qapp, tmp_path, monkeypatch):
    from core.demo_s1000 import FakeS1000
    from core.program_editor_bridge import LoggingBridge
    from core.s1000_bridge import S1000Bridge
    from test_program_editor_window import _select_sample_with_full_audio
    from test_program_editor_window_s1000 import _dispose, _make_editor

    fake = FakeS1000()
    editor = _make_editor(qapp, "akai_s1000", LoggingBridge(S1000Bridge(fake.bridge(timeout=0.3))))
    try:
        audio = [(i % 200) * 100 - 10000 for i in range(22050)]  # KICK is 22050 frames
        _select_sample_with_full_audio(
            editor, qapp, 0, audio, {"start": 0, "loop_start": 0, "loop_end": 22049, "end": 22049}
        )
        editor._set_sample_edit_buttons_enabled(True)
        target = tmp_path / "KICK.s1"
        asked = []
        monkeypatch.setattr(
            "ui.program_editor_window.QFileDialog.getSaveFileName",
            lambda parent, title, path, filters: (asked.append((path, filters)), (str(target), ""))[1],
        )
        editor._save_sample_as_akai_file()
        _pump_until(qapp, lambda: target.exists())
        sample = asf.parse_file(target.read_bytes())
        assert sample.extension == ".s1" and sample.family == "s1000" and len(sample.header) == 150
        assert sample.name == "KICK" and sample.length == 22050 and sample.samples == audio
        assert asked[0][0].endswith("KICK.s1") and "*.s1" in asked[0][1]
    finally:
        _dispose(editor)
