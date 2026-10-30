# Load Multi: the pure planning helpers in core/akai_multi_file.py, BridgeWorker's plan/import jobs against a fake sampler that records every write, and the Multi tab's
# button and confirmation. Nothing here touches a real sampler; what a real unit does with these writes is in tools/s2000_multi_write_check.py's results.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox, QWidget
from menu_lookup import top_menu
from s3k import params as p

from core import akai_multi_file as amf
from core import program_editor_bridge as peb
from core.program_editor_bridge import BridgeWorker
from test_akai_multi_file import FakeBridgeWithMulti, _wait_for_program_load, qapp  # noqa: F401

PROGRAMS = ["KICK", "SNARE", "BASS", "LEAD"]


def _name_bytes(region, field, text):
    return p.encode_field(p.lookup(field, region), text)


def build(name="OLD MULTI", *, fx=(1, 2, 3, 4), parts=None):
    """A wire header and 16 part blocks. `parts` maps a part number (1-16) to {field: value or program name}; PRNAME is set directly (the sampler sets it on a Program Change)."""
    header = bytearray(amf.WIRE_HEADER_SIZE)
    header[3:15] = _name_bytes("multi", "MULTINAME", name)
    for i, value in enumerate(fx):
        header[p.lookup(f"FX{i + 1}", "multi").offset] = value
    blocks = []
    for n in range(amf.PART_COUNT):
        block = bytearray(amf.PART_SIZE)
        block[0] = 1
        block[1:3] = bytes([0x0C + n, 0x60])
        block[amf.PART_END_LINK[0] : amf.PART_END_LINK[1]] = b"\xff\xff"
        block[p.lookup("PMCHAN", "multipart").offset] = n  # channel n + 1
        block[p.lookup("STEREO", "multipart").offset] = 99
        block[p.lookup("PRIORT", "multipart").offset] = 1
        block[p.lookup("PLAYLO", "multipart").offset] = 24
        block[p.lookup("PLAYHI", "multipart").offset] = 127
        block[3:15] = _name_bytes("multipart", "PRNAME", "")
        blocks.append(block)
    for number, fields in (parts or {}).items():
        block = blocks[number - 1]
        for field_name, value in fields.items():
            prm = p.lookup(field_name, "multipart")
            raw = _name_bytes("multipart", field_name, value) if field_name == "PRNAME" else p.encode_field(prm, value)
            block[prm.offset : prm.offset + prm.size] = raw
    return bytes(header), [bytes(b) for b in blocks]


def as_multi(header, parts):
    return amf.parse_file(amf.build_file(header, parts))


class FakeOut:
    def __init__(self, bridge):
        self.bridge = bridge
        self.sent = []

    def send_message(self, message):
        self.sent.append(list(message))
        status, number = message
        if status & 0xF0 != 0xC0:
            return
        # a Program Change binds the program to every part on that channel
        for block in self.bridge.parts:
            if block[p.lookup("PMCHAN", "multipart").offset] == (status & 0x0F) and number < len(PROGRAMS):
                block[3:15] = _name_bytes("multipart", "PRNAME", PROGRAMS[number])


class LoadableBridge:
    """RMULTIDATA (selector 1 = a part) plus set_parameter on the multi regions, a program list and PRGNUMs. Misbehaviours are switches."""

    exclusive_channel = 0

    def __init__(self, header, parts, *, fail_on=None, drop=None, fail_restore=False, backup_dir=None):
        self.header = bytearray(header)
        self.parts = [bytearray(b) for b in parts]
        self.writes = []
        self.fail_on, self.drop, self.fail_restore = fail_on, drop, fail_restore
        self.out = FakeOut(self)
        self.renumbered = 0
        self.backup_dir = backup_dir
        self.backups_at_first_write = None

    def get_header_bytes(self, region, index, offset, count, selector=0, **kwargs):
        if region == "multi" or (region == "multipart" and selector == 0):
            return bytes(self.header + bytes(160))[offset : offset + count]
        return bytes(self.parts[index])[offset : offset + count]

    def program_list(self):
        return list(PROGRAMS)

    def renumber_programs(self):
        self.renumbered += 1

    def get_parameter(self, param, index, **kwargs):
        assert param.name == "PRGNUM"
        return index + param.display_offset

    def set_parameter(self, param, index, value, **kwargs):
        if self.backups_at_first_write is None:
            self.backups_at_first_write = sorted(os.listdir(self.backup_dir)) if self.backup_dir and os.path.isdir(self.backup_dir) else []
        self.writes.append((param.region, param.name, index, value))
        if self.fail_on == param.name and (not self.fail_restore or sum(1 for w in self.writes if w[1] == param.name) == 1):
            raise RuntimeError("REPLY ERROR")
        if self.fail_restore and param.name == "PANPOS" and sum(1 for w in self.writes if w[1] == "PANPOS") > 1:
            raise RuntimeError("REPLY ERROR (restore)")
        if self.drop == param.name:
            return
        raw = p.encode_field(param, value)
        target = self.header if param.region == "multi" else self.parts[index]
        target[param.offset : param.offset + param.size] = raw


@pytest.fixture
def backups(tmp_path, monkeypatch):
    monkeypatch.setattr(peb, "MULTI_BACKUP_DIR", tmp_path / "multi_backups")
    return tmp_path / "multi_backups"


def run_job(bridge, kind, multi_file, qapp):  # noqa: F811
    worker = BridgeWorker(bridge)
    seen = {"plan": [], "done": [], "failed": []}
    worker.multi_plan_ready.connect(lambda *a: seen["plan"].append(a))
    worker.multi_imported.connect(seen["done"].append)
    worker.multi_import_failed.connect(lambda *a: seen["failed"].append(a))
    (worker.submit_plan_multi if kind == "plan" else worker.submit_import_multi)(multi_file)
    worker.process_pending()
    return worker, seen


def target_with(parts=None, fx=(1, 2, 3, 4), name="NEW MULTI"):
    return as_multi(*build(name, fx=fx, parts=parts))


# -- the pure helpers ----------------------------------------------------------------------------------------------------------


def test_only_checked_fields_are_written_and_unchecked_ones_are_listed():
    current = as_multi(*build())
    target = target_with({2: {"PRIORT": 2, "OUTPUT": 3, "PFXSLEV": 40, "STEREO": 80}})
    plan = amf.plan_load(current, target)
    assert {(c.scope, c.name) for c in amf.writable_changes(plan)} >= {("part 2", "PRIORT"), ("part 2", "STEREO"), ("multi", "MULTINAME")}
    assert {(c.scope, c.name) for c in amf.skipped_changes(plan)} == {("part 2", "OUTPUT"), ("part 2", "PFXSLEV")}
    assert len(amf.writable_changes(plan)) + len(amf.skipped_changes(plan)) == len(plan.changes)


def test_a_program_is_assigned_on_the_parts_channel_by_its_position_in_the_list():
    current = as_multi(*build())
    # part 3 swaps channels with part 10, so no part is left sharing a channel
    target = target_with({1: {"PRNAME": "BASS"}, 3: {"PRNAME": "KICK", "PMCHAN": 9}, 10: {"PMCHAN": 2}})
    plan = amf.plan_load(current, target, PROGRAMS)
    assignments, notes = amf.program_assignments(plan, current, target, PROGRAMS)
    assert [(a.part, a.name, a.program_index, a.channel) for a in assignments] == [(1, "BASS", 2, 0), (3, "KICK", 0, 9)]  # part 3's NEW channel
    assert notes == []


def test_a_program_change_would_also_hit_another_part_on_that_channel_so_that_part_is_skipped():
    current = as_multi(*build())
    target = target_with({3: {"PRNAME": "KICK", "PMCHAN": 9}})  # part 10 is on channel 10 too, with no program
    plan = amf.plan_load(current, target, PROGRAMS)
    assignments, notes = amf.program_assignments(plan, current, target, PROGRAMS)
    assert assignments == [] and "part 3: shares channel 10 with part 10" in notes[0]


def test_programs_that_cannot_be_assigned_are_explained():
    current = as_multi(*build(parts={5: {"PMCHAN": 255}}))
    target = target_with(
        {
            1: {"PRNAME": "NOT HERE"},  # not resident
            2: {"PRNAME": "SNARE", "PMCHAN": 6},  # shares channel 7 with part 7 below, which stays blank... (blank = keeps its current, blank)
            5: {"PRNAME": "LEAD", "PMCHAN": 255},  # OMNI
        }
    )
    plan = amf.plan_load(current, target, PROGRAMS)
    assignments, notes = amf.program_assignments(plan, current, target, PROGRAMS)
    text = "\n".join(notes)
    assert "part 1: program 'NOT HERE' is not on the sampler" in text
    assert "part 5: on OMNI" in text
    assert "part 2" in text and "shares channel 7 with part 7" in text
    assert assignments == []


def test_two_parts_on_one_channel_wanting_the_same_program_send_one_change():
    current = as_multi(*build())
    target = target_with({1: {"PRNAME": "LEAD", "PMCHAN": 0}, 2: {"PRNAME": "LEAD", "PMCHAN": 0}})
    plan = amf.plan_load(current, target, PROGRAMS)
    assignments, _ = amf.program_assignments(plan, current, target, PROGRAMS)
    assert [(a.part, a.channel) for a in assignments] == [(1, 0)]


def test_the_plan_is_read_only(qapp):  # noqa: F811
    bridge = LoadableBridge(*build())
    _worker, seen = run_job(bridge, "plan", target_with({2: {"PRIORT": 2}}), qapp)
    assert bridge.writes == [] and bridge.out.sent == []
    multi_file, plan, assignments, notes = seen["plan"][0]
    assert any(c.name == "PRIORT" for c in plan.changes) and seen["failed"] == []


# -- the import job ------------------------------------------------------------------------------------------------------------


def full_target():
    return target_with(
        {
            1: {"PRNAME": "KICK", "PMCHAN": 4, "STEREO": 70, "PANPOS": -20},
            5: {"PMCHAN": 0},  # (part 5 takes part 1's old channel, so nobody shares one)
            2: {"PRIORT": 2, "PLAYLO": 36, "PLAYHI": 96, "TRANSPOSE": 5, "PTUNOCM": 20, "PRNAME": "SNARE"},
            3: {"OUTPUT": 3, "VOSCL": 30, "PFXCHAN": 1, "PFXSLEV": 40},
        },
        fx=(10, 20, 30, 250),
    )


def test_a_load_writes_the_planned_fields_assigns_programs_and_reads_back(qapp, backups):  # noqa: F811
    bridge = LoadableBridge(*build(), backup_dir=backups)
    before = [bytes(b) for b in bridge.parts]
    target = full_target()
    _worker, seen = run_job(bridge, "import", target, qapp)
    assert seen["failed"] == [] and len(seen["done"]) == 1
    result = seen["done"][0]
    assert result.mismatches == [] and result.not_assigned == []
    after = amf.parse_file(amf.build_file(bytes(bridge.header), [bytes(b) for b in bridge.parts]))
    for name in ("PRIORT", "PLAYLO", "PLAYHI", "TRANSPOSE", "PTUNOCM"):
        assert amf._decode("multipart", name, after.parts[1]) == amf._decode("multipart", name, target.parts[1]), name
    assert amf._decode("multipart", "STEREO", after.parts[0]) == 70 and amf._decode("multipart", "PANPOS", after.parts[0]) == -20
    assert amf._decode("multipart", "PMCHAN", after.parts[0]) == 4
    assert after.name == "NEW MULTI"
    assert [after.part_name(0), after.part_name(1)] == ["KICK", "SNARE"]
    # the unchecked fields of part 3 were left alone and reported
    assert {c.name for c in result.skipped} == {"OUTPUT", "VOSCL", "PFXCHAN", "PFXSLEV"}
    assert after.parts[2][p.lookup("OUTPUT", "multipart").offset] == before[2][p.lookup("OUTPUT", "multipart").offset]
    # Program Changes: program 0 on part 1's NEW channel (4), program 1 on part 2's channel (1); PRGNUMs renumbered once
    assert bridge.out.sent == [[0xC0 | 4, 0], [0xC0 | 1, 1]] and bridge.renumbered == 1


def test_the_multi_is_saved_before_the_first_write(qapp, backups):  # noqa: F811
    bridge = LoadableBridge(*build(), backup_dir=backups)
    result = run_job(bridge, "import", full_target(), qapp)[1]["done"][0]
    assert bridge.backups_at_first_write and bridge.backups_at_first_write[0].endswith(".m3")
    saved = amf.parse_file(open(result.backup, "rb").read())
    assert saved.name == "OLD MULTI" and amf._decode("multipart", "STEREO", saved.parts[0]) == 99  # the multi as it was


def test_no_pointer_name_or_unmodelled_byte_is_written(qapp, backups):  # noqa: F811
    bridge = LoadableBridge(*build(), backup_dir=backups)
    before_parts = [bytes(b) for b in bridge.parts]
    before_header = bytes(bridge.header)
    run_job(bridge, "import", full_target(), qapp)
    allowed = {p.lookup(name, "multipart").offset for name, evidence in amf.LOADABLE_PART_FIELDS if evidence != "unproven"}
    for index in range(amf.PART_COUNT):
        changed = {o for o in range(amf.PART_SIZE) if bridge.parts[index][o] != before_parts[index][o]}
        changed -= set(range(3, 15)) if index in (0, 1) else set()  # PRNAME: set by the Program Change, not a write
        assert changed <= allowed, (index, sorted(changed - allowed))
    assert not {w[2] for w in bridge.writes if w[0] == "multipart"} - set(range(16))
    assert {w[1] for w in bridge.writes} <= {n for n, e in amf.LOADABLE_PART_FIELDS + amf.LOADABLE_HEADER_FIELDS if e != "unproven"}
    assert bridge.header[16:20][3] <= amf.FX_MAX and bridge.header != before_header


def test_an_effect_index_above_204_is_written_as_204(qapp, backups):  # noqa: F811
    bridge = LoadableBridge(*build(), backup_dir=backups)
    run_job(bridge, "import", full_target(), qapp)
    assert bridge.header[p.lookup("FX4", "multi").offset] == amf.FX_MAX  # the file said 250


def test_a_failed_write_puts_the_earlier_ones_back_and_says_where_the_backup_is(qapp, backups):  # noqa: F811
    bridge = LoadableBridge(*build(), fail_on="PRIORT", backup_dir=backups)
    before = (bytes(bridge.header), [bytes(b) for b in bridge.parts])
    worker, seen = run_job(bridge, "import", full_target(), qapp)
    assert seen["done"] == [] and len(seen["failed"]) == 1
    error, backup = seen["failed"][0]
    assert "REPLY ERROR" in error and backup.endswith(".m3") and os.path.exists(backup)
    assert (bytes(bridge.header), [bytes(b) for b in bridge.parts]) == before  # everything written before it was written back
    assert bridge.out.sent == [] and worker.multi_import_blocked is False


def test_a_rollback_that_fails_blocks_further_loads(qapp, backups):  # noqa: F811
    bridge = LoadableBridge(*build(), fail_on="PRIORT", fail_restore=True, backup_dir=backups)
    worker, seen = run_job(bridge, "import", full_target(), qapp)
    assert "could not be put back" in seen["failed"][0][0] and worker.multi_import_blocked is True


def test_a_write_that_does_not_read_back_is_reported_and_blocks_more_loads(qapp, backups):  # noqa: F811
    bridge = LoadableBridge(*build(), drop="TRANSPOSE", backup_dir=backups)
    worker, seen = run_job(bridge, "import", full_target(), qapp)
    result = seen["done"][0]
    assert any("TRANSPOSE" in line for line in result.mismatches) and worker.multi_import_blocked is True
    worker.submit_import_multi(full_target())
    worker.process_pending()
    assert "disabled for this session" in seen["failed"][-1][0]


def test_a_load_that_changes_nothing_sends_nothing(qapp, backups):  # noqa: F811
    header, parts = build()
    bridge = LoadableBridge(header, parts, backup_dir=backups)
    result = run_job(bridge, "import", as_multi(header, parts), qapp)[1]["done"][0]
    assert result.written == [] and bridge.writes == [] and bridge.out.sent == []


def test_a_program_that_is_not_resident_is_left_unassigned_and_listed(qapp, backups):  # noqa: F811
    bridge = LoadableBridge(*build(), backup_dir=backups)
    result = run_job(bridge, "import", target_with({1: {"PRNAME": "GONE"}}), qapp)[1]["done"][0]
    assert result.assigned == [] and any("'GONE' is not on the sampler" in line for line in result.not_assigned) and bridge.out.sent == []


def test_program_changes_still_report_through_the_old_signals(qapp):  # noqa: F811
    # the Multi tab's own assignment path was refactored to share _send_program_change: it still emits change_sent / change_send_failed
    bridge = LoadableBridge(*build())
    worker = BridgeWorker(bridge)
    sent, failed = [], []
    worker.change_sent.connect(lambda part, name: sent.append((part, name)))
    worker.change_send_failed.connect(lambda part, error: failed.append((part, error)))
    worker.submit_program_change(2, 1, "SNARE", 2)
    worker.process_pending()
    assert sent == [(2, "SNARE")] and failed == [] and bridge.out.sent == [[0xC2, 1]]
    bridge.out = None
    worker.submit_program_change(2, 1, "SNARE", 2)
    worker.process_pending()
    assert sent == [(2, "SNARE"), (2, "SNARE")]  # no port (the demo bridge): reported as sent, as before


# -- the Multi tab -------------------------------------------------------------------------------------------------------------


@pytest.fixture
def editor(qapp):  # noqa: F811
    from ui.program_editor_window import ProgramEditorWindow

    editor = ProgramEditorWindow(QWidget(), bridge=FakeBridgeWithMulti())
    _wait_for_program_load(editor, qapp)
    yield editor
    editor._worker.stop()
    editor._worker.wait()


def test_the_multi_tab_has_a_load_button_and_the_file_menu_an_entry(editor):
    assert editor.load_multi_button.text() == "Load from File..." and editor.load_multi_button.isEnabled()
    file_menu = top_menu(editor, "&File")
    assert editor._load_multi_action in file_menu.actions() and editor._load_multi_action.isEnabled()


def test_loading_is_off_in_demo_mode_and_while_busy(editor, monkeypatch):
    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    editor._sync_save_multi_enabled(False)
    assert not editor.load_multi_button.isEnabled() and editor.save_multi_button.isEnabled()  # saving only reads
    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER")
    editor._sync_save_multi_enabled(True)
    assert not editor.load_multi_button.isEnabled() and not editor._load_multi_action.isEnabled()
    editor._sync_save_multi_enabled(False)
    assert editor.load_multi_button.isEnabled()


def _answer(monkeypatch, label):
    shown = []

    def exec_(self):
        shown.append((self.text(), self.informativeText(), self.detailedText()))
        return 0

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: next((b for b in self.buttons() if b.text() == label), None))
    return shown


def _plan_for(target):
    current = as_multi(*build())
    plan = amf.plan_load(current, target, PROGRAMS)
    assignments, notes = amf.program_assignments(plan, current, target, PROGRAMS)
    return plan, assignments, notes


def test_the_confirmation_lists_the_changes_and_load_submits_the_job(editor, monkeypatch):
    target = full_target()
    plan, assignments, notes = _plan_for(target)
    submitted = []
    monkeypatch.setattr(editor._worker, "submit_import_multi", submitted.append)
    shown = _answer(monkeypatch, "Load")
    editor._pending_multi_load = True
    editor._on_multi_plan_ready(target, plan, assignments, notes)
    assert submitted == [target]
    text, info, detail = shown[0]
    assert 'Load multi "NEW MULTI"' in text and "saved first" in info
    assert "part 2 PRIORT: 1 -> 2" in detail and "part 1 STEREO: 99 -> 70" in detail and "part 1: program 'KICK'" in detail
    assert "Not loaded" in detail and "part 3 OUTPUT: not checked on a unit yet" in detail


def test_cancelling_the_confirmation_sends_nothing_and_frees_the_buttons(editor, monkeypatch):
    target = full_target()
    plan, assignments, notes = _plan_for(target)
    submitted = []
    monkeypatch.setattr(editor._worker, "submit_import_multi", submitted.append)
    _answer(monkeypatch, "Cancel")
    editor._pending_multi_load = True
    editor._on_multi_plan_ready(target, plan, assignments, notes)
    assert submitted == [] and editor._pending_multi_load is False and editor.load_multi_button.isEnabled()


def test_a_file_that_matches_the_sampler_says_so_and_sends_nothing(editor, monkeypatch):
    target = as_multi(*build())
    plan, assignments, notes = _plan_for(target)
    submitted = []
    monkeypatch.setattr(editor._worker, "submit_import_multi", submitted.append)
    shown = _answer(monkeypatch, "Ok")
    editor._pending_multi_load = True
    editor._on_multi_plan_ready(target, plan, assignments, notes)
    assert submitted == [] and shown[0][0] == "The sampler already matches this file." and editor._pending_multi_load is False


def test_a_bad_file_is_refused_before_the_sampler_is_asked(editor, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    path = tmp_path / "BAD.M3"
    path.write_bytes(b"not a multi")
    warned = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warned.append(a[2]))
    planned = []
    monkeypatch.setattr(editor._worker, "submit_plan_multi", planned.append)
    editor._load_multi_from_file()
    assert planned == [] and warned and "isn't an Akai multi file" in warned[0]
    assert not getattr(editor, "_pending_multi_load", False) and editor.load_multi_button.isEnabled()


def test_a_chosen_file_asks_the_sampler_for_a_plan_and_blocks_a_second_load(editor, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    path = tmp_path / "GOOD.M3"
    path.write_bytes(amf.build_file(*build("GOOD")))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    planned = []
    monkeypatch.setattr(editor._worker, "submit_plan_multi", planned.append)
    editor._load_multi_from_file()
    assert len(planned) == 1 and planned[0].name == "GOOD" and not editor.load_multi_button.isEnabled()
    editor._load_multi_from_file()
    assert len(planned) == 1  # (and the button is greyed while the plan is being read)


def test_a_failed_load_shows_the_error_and_the_backup_path(editor, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warned.append(a[2]))
    editor._pending_multi_load = True
    editor._on_multi_import_failed("REPLY ERROR", "/tmp/OLD-before-load.m3")
    assert "REPLY ERROR" in warned[0] and "/tmp/OLD-before-load.m3" in warned[0] and editor.load_multi_button.isEnabled()
