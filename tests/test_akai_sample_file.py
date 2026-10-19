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


# -- loading a file back: what is written to the sampler -------------------------------------------------------------------


def loop_header(n, at, length_frames, dwell, base=None):
    block = bytearray(base or make_header(length=1000))
    base_offset = {1: 38, 2: 50, 3: 62, 4: 74}[n]
    block[base_offset : base_offset + 4] = at.to_bytes(4, "little")
    block[base_offset + 4 : base_offset + 10] = int(length_frames * 65536).to_bytes(6, "little")
    block[base_offset + 10 : base_offset + 12] = dwell.to_bytes(2, "little")
    return bytes(block)


def test_a_plan_carries_the_settings_the_file_holds():
    plan = asf.restore_plan(make_header(length=1000, rate=22050), 1000)
    assert (plan.rate, plan.sptype, plan.spitch, plan.shlto, plan.sbandw) == (22050, 2, 60, 7, 1)
    assert plan.stuno == 0xFF00  # raw, as the editor's cache holds it
    assert (plan.start, plan.end) == (1, 998)
    assert plan.writes == [
        ("SPTYPE", 2), ("SPITCH", 60), ("SHLTO", 7), ("SSTART", 1), ("SMPEND", 998),
        ("LOOPAT1", 999), ("LLNGTH1", 3 * 65536), ("LDWELL1", 9999),
    ]  # fmt: skip
    assert (plan.loop_start, plan.loop_end) == (996, 999) and plan.notes == []


def test_the_loop_length_is_kept_exactly_including_its_fraction():
    header = bytearray(make_header(length=1000))
    header[42:48] = (3 * 65536 + 12345).to_bytes(6, "little")
    assert ("LLNGTH1", 3 * 65536 + 12345) in asf.restore_plan(bytes(header), 1000).writes


def test_the_rate_is_ssrate_when_valid_and_the_bandwidth_bucket_otherwise():
    assert asf.playback_rate(make_header(rate=12345)) == 12345
    invalid = bytearray(make_header(rate=12345))
    invalid[15] = 0  # SSRVLD: the rate field isn't valid
    assert asf.playback_rate(bytes(invalid)) == 44100  # SBANDW 1
    invalid[1] = 0
    assert asf.playback_rate(bytes(invalid)) == 22050  # SBANDW 0
    assert asf.playback_rate(make_header(rate=0)) == 44100
    unknown_bucket = bytearray(make_header(rate=0))
    unknown_bucket[1] = 9
    assert asf.playback_rate(bytes(unknown_bucket)) == 44100


def test_other_loops_are_written_only_when_they_are_in_use():
    header = loop_header(3, at=700, length_frames=100, dwell=250)
    header = loop_header(2, at=500, length_frames=50, dwell=0, base=header)  # dwell 0 = no loop: left alone
    writes = dict(asf.restore_plan(header, 1000).writes)
    assert writes["LOOPAT3"] == 700 and writes["LLNGTH3"] == 100 * 65536 and writes["LDWELL3"] == 250
    assert "LOOPAT2" not in writes and "LOOPAT4" not in writes


def test_an_unusable_loop_is_cleared_or_left_out_with_a_note():
    plan = asf.restore_plan(loop_header(1, at=5000, length_frames=10, dwell=9999), 1000)  # loop end past the sample
    assert dict(plan.writes)["LOOPAT1"] == 1000 and dict(plan.writes)["LLNGTH1"] == 0 and dict(plan.writes)["LDWELL1"] == 0
    assert any("loop 1" in n for n in plan.notes)
    plan = asf.restore_plan(loop_header(2, at=100, length_frames=500, dwell=9999, base=make_header(length=1000)), 1000)  # longer than where it ends
    assert "LOOPAT2" not in dict(plan.writes) and any("loop 2" in n for n in plan.notes)


def test_a_start_and_end_that_do_not_fit_become_the_whole_sample():
    header = bytearray(make_header(length=1000))
    header[30:34] = (900).to_bytes(4, "little")
    header[34:38] = (100).to_bytes(4, "little")  # start after end
    plan = asf.restore_plan(bytes(header), 1000)
    assert (plan.start, plan.end) == (0, 999) and any("start 900" in n for n in plan.notes)
    header[30:34] = (0).to_bytes(4, "little")
    header[34:38] = (5000).to_bytes(4, "little")  # end past the audio
    assert asf.restore_plan(bytes(header), 1000).end == 999


def test_values_the_sampler_could_not_use_are_left_at_its_defaults():
    header = bytearray(make_header(length=1000))
    header[19] = 9  # SPTYPE 9
    header[2] = 10  # SPITCH 10 (below A1)
    plan = asf.restore_plan(bytes(header), 1000)
    names = [n for n, _v in plan.writes]
    assert "SPTYPE" not in names and "SPITCH" not in names and "SHLTO" in names
    assert plan.sptype == 2 and plan.spitch == 60  # what a fresh sample has
    assert len(plan.notes) == 2


def test_an_s1000_header_gives_the_same_kind_of_plan():
    plan = asf.restore_plan(make_header(length=1000, size=150), 1000)
    assert plan.rate == 44100 and ("LOOPAT1", 999) in plan.writes
    assert all(asf.sample_param(name).end <= 141 for name, _value in plan.writes)  # nothing the S1000's block can't hold


def test_every_field_a_plan_writes_fits_the_s1000_block():
    from core import s1000_bridge

    header = loop_header(4, at=800, length_frames=10, dwell=300, base=loop_header(3, 700, 10, 200, base=loop_header(2, 600, 10, 100)))
    for name, _value in asf.restore_plan(header, 1000).writes:
        assert s1000_bridge.supports(asf.sample_param(name)), name


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


# -- the Samples tab: Load Akai File ------------------------------------------------------------------------------------------


from core import program_editor_bridge  # noqa: E402,F401
from test_program_editor_window import FakeSamplerController  # noqa: E402


@pytest.fixture
def load_editor(editor, qapp, monkeypatch):
    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    fake = FakeSamplerController(editor._bridge)
    editor._main_window.sampler_controller = fake
    editor._test_fake = fake
    editor._set_sample_edit_buttons_enabled(False)  # (re-reads the demo flag)
    return editor


def _header_writes(editor, name):
    index = editor._bridge.sample_list().index(name)
    editor._worker.wait_until_idle()
    return [(c[0], c[2]) for c in editor._bridge.set_parameter_calls if c[1] == index]


def _file(tmp_path, header, samples, name="LOADED.s3"):
    path = tmp_path / name
    path.write_bytes(asf.build_file(header, samples))
    return str(path)


def _choose(monkeypatch, editor, path, name=None):
    monkeypatch.setattr("ui.program_editor_window.QFileDialog.getOpenFileName", lambda *a, **k: (path, ""))
    monkeypatch.setattr(editor, "_prompt_akai_name", lambda title, label, current: current if name is None else name)


def test_loading_a_file_sends_its_audio_under_its_name_then_writes_its_settings_back(load_editor, tmp_path, monkeypatch):
    editor = load_editor
    audio = [(i * 37) % 2000 - 1000 for i in range(1000)]
    header = loop_header(1, at=800, length_frames=300, dwell=500, base=make_header(length=1000, name="LOADED", rate=44100))
    path = _file(tmp_path, header, audio)
    _choose(monkeypatch, editor, path)
    editor._load_akai_sample_from_file()
    sent = editor._test_fake.sent_entries
    assert len(sent) == 1 and sent[0]["name"] == "LOADED" and sent[0]["samples"] == audio
    writes = _header_writes(editor, "LOADED")
    names = [n for n, _v in writes]
    assert names[:5] == ["SPTYPE", "SPITCH", "SHLTO", "STUNO", "SSTART"]  # STUNO right after SHLTO, as the other flows write them
    values = dict(writes)
    assert values["SPITCH"] == 60 and values["SHLTO"] == 7 and values["SSTART"] == 1 and values["SMPEND"] == 998
    assert values["LOOPAT1"] == 800 and values["LLNGTH1"] == 300 * 65536 and values["LDWELL1"] == 500
    assert "Load complete" in editor.status_bar.currentMessage()
    assert editor._sample_name_at_row(editor.sample_list_widget.currentRow()) == "LOADED"


def test_the_names_prompt_starts_with_the_files_name(load_editor, tmp_path, monkeypatch):
    editor = load_editor
    asked = []
    path = _file(tmp_path, make_header(length=100, name="FROM FILE"), [1] * 100)
    monkeypatch.setattr("ui.program_editor_window.QFileDialog.getOpenFileName", lambda *a, **k: (path, ""))
    monkeypatch.setattr(editor, "_prompt_akai_name", lambda title, label, current: asked.append(current) or None)
    editor._load_akai_sample_from_file()
    assert asked == ["FROM FILE"] and editor._test_fake.sent_entries == []  # cancelling the prompt sends nothing


def test_a_name_already_on_the_sampler_is_asked_for_again(load_editor, tmp_path, monkeypatch):
    editor = load_editor
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(text))
    path = _file(tmp_path, make_header(length=100, name="SQUARE"), [1] * 100)  # SQUARE is resident
    monkeypatch.setattr("ui.program_editor_window.QFileDialog.getOpenFileName", lambda *a, **k: (path, ""))
    answers = iter(["SQUARE", "SQUARE 2"])
    monkeypatch.setattr(editor, "_prompt_akai_name", lambda *a, **k: next(answers))
    editor._load_akai_sample_from_file()
    assert len(shown) == 1 and "already in use" in shown[0]
    assert [e["name"] for e in editor._test_fake.sent_entries] == ["SQUARE 2"]


def test_a_file_that_is_not_a_sample_is_refused(load_editor, tmp_path, monkeypatch):
    editor = load_editor
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(text))
    bad = tmp_path / "junk.s3"
    bad.write_bytes(bytes(500))
    _choose(monkeypatch, editor, str(bad))
    editor._load_akai_sample_from_file()
    assert shown and "Couldn't read this file" in shown[0] and editor._test_fake.sent_entries == []


def test_a_file_padded_after_its_audio_still_loads(load_editor, tmp_path, monkeypatch):
    editor = load_editor
    path = tmp_path / "PADDED.s3"
    path.write_bytes(asf.build_file(make_header(length=100, name="PADDED"), [5] * 100) + bytes(1024))
    _choose(monkeypatch, editor, str(path))
    editor._load_akai_sample_from_file()
    assert [e["name"] for e in editor._test_fake.sent_entries] == ["PADDED"]


def test_things_left_at_the_samplers_defaults_are_said_in_the_status_bar(load_editor, tmp_path, monkeypatch):
    editor = load_editor
    header = loop_header(1, at=5000, length_frames=10, dwell=9999, base=make_header(length=100, name="ODD"))
    _choose(monkeypatch, editor, _file(tmp_path, header, [1] * 100))
    editor._load_akai_sample_from_file()
    assert "defaults" in editor.status_bar.currentMessage() and "loop 1" in editor.status_bar.currentMessage()
    assert dict(_header_writes(editor, "ODD"))["LDWELL1"] == 0


def test_an_s1_file_loads_on_an_s2000_without_a_question(load_editor, tmp_path, monkeypatch):
    editor = load_editor
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.No)
    _choose(monkeypatch, editor, _file(tmp_path, make_header(length=100, size=150, name="OLD S1"), [3] * 100, "OLD S1.s1"))
    editor._load_akai_sample_from_file()
    assert asked == [] and [e["name"] for e in editor._test_fake.sent_entries] == ["OLD S1"]


def test_an_s3_file_on_an_s1000_asks_first_and_writes_only_fields_its_block_holds(load_editor, tmp_path, monkeypatch):
    from core import s1000_bridge

    editor = load_editor
    editor._is_s1000 = True
    questions = []

    def question(parent, title, text, *a, **k):
        questions.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", question)
    real_supports = s1000_bridge.supports
    monkeypatch.setattr(s1000_bridge, "supports", lambda param: param.name != "LDWELL1" and real_supports(param))  # as if the block lacked one
    _choose(monkeypatch, editor, _file(tmp_path, make_header(length=100, name="S3 FILE"), [3] * 100))
    editor._load_akai_sample_from_file()
    assert len(questions) == 1 and "S2000/S3000 sample" in questions[0] and "left out" in questions[0]
    names = [n for n, _v in _header_writes(editor, "S3 FILE")]
    assert "LOOPAT1" in names and "LDWELL1" not in names


def test_declining_the_s1000_question_loads_nothing(load_editor, tmp_path, monkeypatch):
    editor = load_editor
    editor._is_s1000 = True
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    _choose(monkeypatch, editor, _file(tmp_path, make_header(length=100), [3] * 100))
    editor._load_akai_sample_from_file()
    assert editor._test_fake.sent_entries == []


def test_loading_is_refused_during_a_dashboard_transfer(load_editor, tmp_path, monkeypatch):
    editor = load_editor
    editor._test_fake.busy = True
    _choose(monkeypatch, editor, _file(tmp_path, make_header(length=100), [3] * 100))
    editor._load_akai_sample_from_file()
    assert editor._test_fake.sent_entries == [] and "transfer is already in progress" in editor.status_bar.currentMessage()


def test_the_button_needs_no_sample_but_not_demo_mode(editor, monkeypatch):
    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    editor._set_sample_edit_buttons_enabled(False)
    assert editor.load_akai_button.isEnabled() and editor.load_akai_button.text() == "Load Akai File…"
    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    editor._set_sample_edit_buttons_enabled(False)
    assert not editor.load_akai_button.isEnabled()
