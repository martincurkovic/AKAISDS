# tests for core/yamaha_restore.py, core/yamaha_backups.py and controller/yamaha_restore.py - restoring a Yamaha object from the
# .syx backups the editor saves. The job runs the real YamahaSession + SamplerController against core/demo_a4000.FakeA4000.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from controller.yamaha_restore import RestoreJob
from core import demo_a4000 as demo
from core import yamaha_backups as yb
from core import yamaha_params as yp
from core import yamaha_restore as yr
from core import yamaha_sysex as y

from test_s950_transfers import qapp, wait_until  # noqa: F401
from test_yamaha_session import build, collect


def dump_of(fmt, name, data):
    raw = y.pad_name(name)
    return y.BulkDump(0, fmt, y.HEADER_A4000, name, raw, bytes(data), 1)


def program(n=1, assigned=("sine wave",), **changes):
    data = demo.make_program_payload(n, list(assigned))
    for key, value in changes.items():
        scope, k = ("easy_edit", key[3:]) if key.startswith("ee_") else ("program", key)
        yp.store(yp.get(scope, k), data, value, 0 if scope == "easy_edit" else None)
    return data


# --- planning (pure) -----------------------------------------------------------------------------------------------


def test_identical_objects_need_nothing():
    d = dump_of("PG", "001", program())
    plan = yr.plan_restore(d, d)
    assert plan.identical and plan.notes == []


def test_only_writable_values_that_differ_are_planned_with_what_the_backup_holds():
    backup = dump_of("PG", "001", program(program_level=100, transpose=-5, ee_level_offset=12))
    now = dump_of("PG", "001", program(program_level=40, transpose=-5, ee_level_offset=-3))
    plan = yr.plan_restore(backup, now)
    got = {(i.row.key, i.slot): (i.value, i.current) for i in plan.items}
    assert got == {("program_level", None): (100, 40), ("level_offset", 0): (12, -3)}  # transpose matched: not touched


def test_read_only_and_ignored_rows_are_never_planned():
    backup_data, now_data = program(), program()
    yp.store(yp.get("program", "program_name"), now_data, "Changed")  # a read-only text row
    plan = yr.plan_restore(dump_of("PG", "001", backup_data), dump_of("PG", "001", now_data))
    assert plan.identical
    sample_backup, sample_now = bytearray(demo.make_sample_payload("x")), bytearray(demo.make_sample_payload("x"))
    yp.store(yp.get("sample", "sampling_frequency_l"), sample_now, 12345)  # accepted-and-ignored by the unit
    yp.store(yp.get("sample", "filter_cutoff"), sample_now, 5)
    plan = yr.plan_restore(dump_of("SP", "x", sample_backup), dump_of("SP", "x", sample_now))
    assert [i.row.key for i in plan.items] == ["filter_cutoff"]


def test_the_coupled_wave_and_loop_rows_are_written_last_in_a_fixed_order():
    backup, now = bytearray(demo.make_sample_payload("x")), bytearray(demo.make_sample_payload("x"))
    for key, value in (("loop_end_address", 90), ("loop_length", 50), ("loop_start_address", 8), ("wave_start_address", 4), ("pan", 12), ("filter_cutoff", 5)):
        yp.store(yp.get("sample", key), backup, value)
    plan = yr.plan_restore(dump_of("SP", "x", backup), dump_of("SP", "x", now))
    keys = [i.row.key for i in plan.items]
    assert keys[-4:] == ["wave_start_address", "loop_start_address", "loop_length", "loop_end_address"]
    assert set(keys[:-4]) == {"pan", "filter_cutoff"}


def test_a_different_object_or_a_different_wave_is_refused():
    with pytest.raises(yr.RestoreError):
        yr.plan_restore(dump_of("PG", "001", program()), dump_of("PG", "002", program(2)))
    with pytest.raises(yr.RestoreError):
        yr.plan_restore(dump_of("SP", "x", demo.make_sample_payload("x")), dump_of("PG", "x", program()))
    other_wave = bytearray(demo.make_sample_payload("x"))
    other_wave[yp.WAVE_NAME_L_OFFSET : yp.WAVE_NAME_L_OFFSET + 16] = y.pad_name("SMP 999999")
    with pytest.raises(yr.RestoreError, match="left wave"):
        yr.plan_restore(dump_of("SP", "x", demo.make_sample_payload("x")), dump_of("SP", "x", other_wave))
    with pytest.raises(yr.RestoreError):
        yr.plan_restore(dump_of("OL", "", b"x" * 17), dump_of("OL", "", b"x" * 17))


def test_changed_assignments_are_noted_and_only_matching_slots_are_restored():
    backup = dump_of("PG", "001", program(assigned=("sine wave", "saw up"), ee_level_offset=9))
    now_data = demo.make_program_payload(1, ["sine wave"])  # one sample fewer now
    yp.store(yp.get("easy_edit", "level_offset"), now_data, 1, 0)
    plan = yr.plan_restore(backup, dump_of("PG", "001", now_data))
    assert any("assigned sample" in n for n in plan.notes)
    assert [(i.row.key, i.slot, i.value) for i in plan.items] == [("level_offset", 0, 9)]
    swapped = demo.make_program_payload(1, ["triangle", "saw up"])  # slot 0 holds a different sample now
    plan = yr.plan_restore(backup, dump_of("PG", "001", swapped))
    assert any("Slot 0" in n for n in plan.notes)
    assert all(i.slot != 0 for i in plan.items)


def test_residual_bytes_ignore_the_edited_flag_only():
    a, b = bytearray(program()), bytearray(program())
    b[1] |= 1  # the unit's "edited" flag: any edit sets it
    assert yr.residual_offsets(dump_of("PG", "001", a), dump_of("PG", "001", b)) == []
    b[200] ^= 0x10
    assert yr.residual_offsets(dump_of("PG", "001", a), dump_of("PG", "001", b)) == [200]


# --- backups on disk ---------------------------------------------------------------------------------------------


def write_backup(directory, name, fmt, data, mtime):
    blob = b"\xf0" + y.build_bulk_dump(0, fmt, y.pad_name(name), bytes(data)) + b"\xf7"
    path = directory / f"{fmt}-{name}-{int(mtime)}.syx"
    path.write_bytes(blob)
    os.utime(path, (mtime, mtime))
    return path


def test_backups_are_listed_newest_first_and_junk_is_skipped(tmp_path):
    write_backup(tmp_path, "001", "PG", program(), 1_000)
    write_backup(tmp_path, "pulse 3", "SP", demo.make_sample_payload("pulse 3"), 3_000)
    write_backup(tmp_path, "002", "PG", program(2), 2_000)
    (tmp_path / "notes.txt").write_text("hi")
    (tmp_path / "broken.syx").write_bytes(b"\xf0\x43\x00\xf7")
    entries = yb.list_backups(tmp_path)
    assert [(e.kind, e.name) for e in entries] == [("Sample", "pulse 3"), ("Program", "002"), ("Program", "001")]
    assert "Program 002" in entries[1].text
    assert yb.list_backups(tmp_path / "missing") == []


def test_load_backup_refuses_a_file_that_is_not_a_dump(tmp_path):
    (tmp_path / "x.syx").write_bytes(b"not sysex")
    with pytest.raises(yr.RestoreError):
        yr.load_backup(tmp_path / "x.syx")
    with pytest.raises(yr.RestoreError):
        yr.load_backup(tmp_path / "missing.syx")


# --- the job, against the fake unit ----------------------------------------------------------------------------------


@pytest.fixture
def rig(qapp):  # noqa: F811
    fake = demo.FakeA4000()
    fake.assign(1, "sine wave")
    fake.assign(1, "saw up")
    rig = build(fake)
    rig.statuses.clear()
    return rig


def norm(data):
    data = bytearray(data)
    data[1] &= 0xFE
    return bytes(data)


def write(rig, scope, key, value, name, slot=None):
    return collect(rig, lambda cb: rig.session.write_parameter(yp.get(scope, key), value, name, cb, slot=slot))


def prepare(rig, backup):
    job = RestoreJob(rig.session, backup)
    got = []
    job.prepare(lambda plan, msg: got.append((plan, msg)))
    assert wait_until(lambda: bool(got))
    return job, got[0]


def run(job, **kw):
    got, progress = [], []
    job.run(got.append, on_progress=lambda *a: progress.append(a))
    assert wait_until(lambda: bool(got), timeout=30)
    return got[0], progress


def backup_of(rig, fmt, name):
    return collect(rig, lambda cb: rig.session.request_bulk(fmt, name, cb))


def test_a_program_is_put_back_exactly_after_edits(rig):
    original = bytes(rig.fake.programs[1])
    backup = backup_of(rig, "PG", "001")
    for key, value in (("program_level", 30), ("transpose", -7), ("lfo_cycle", 3)):
        assert write(rig, "program", key, value, "001").ok
    assert write(rig, "easy_edit", "level_offset", 20, "001", slot=1).ok
    assert norm(rig.fake.programs[1]) != norm(original)
    job, (plan, message) = prepare(rig, backup)
    assert plan is not None and len(plan.items) == 4
    result, progress = run(job)
    assert result.ok and result.written == 4 and result.passes == 1 and result.remaining == []
    assert norm(rig.fake.programs[1]) == norm(original)
    assert [p[0] for p in progress] == [0, 1, 2, 3] and progress[0][2]  # progress says which value is being written
    assert "Restored program '001'" in result.message


def test_the_current_state_is_snapshotted_first_so_a_restore_can_be_undone(rig):
    backup = backup_of(rig, "SP", "saw up")
    assert write(rig, "sample", "filter_cutoff", 33, "saw up").ok
    assert write(rig, "sample", "pan", -20, "saw up").ok
    edited = bytes(rig.fake.samples["saw up"])
    job, _ = prepare(rig, backup)
    result, _ = run(job)
    assert result.ok and result.snapshot_path is not None and result.snapshot_path.exists()
    snapshot = yr.load_backup(result.snapshot_path)
    assert bytes(snapshot.data) == edited  # exactly the state the restore replaced - including edits made since the backup
    # and restoring THAT puts the edits back
    job2, _ = prepare(rig, snapshot)
    result2, _ = run(job2)
    assert result2.ok and norm(rig.fake.samples["saw up"]) == norm(edited)


def test_an_object_that_already_matches_is_left_alone(rig):
    backup = backup_of(rig, "SP", "square")
    edits = rig.fake.edits
    job, (plan, _msg) = prepare(rig, backup)
    assert plan.identical
    result, _ = run(job)
    assert result.ok and result.written == 0 and "already matches" in result.message and rig.fake.edits == edits
    assert result.snapshot_path is None  # nothing to protect, nothing saved


def test_a_restore_onto_the_wrong_object_is_refused_before_anything_is_written(rig):
    backup = backup_of(rig, "SP", "square")
    other = y.BulkDump(0, "SP", backup.header, "triangle", y.pad_name("triangle"), backup.data, 1)
    job, (plan, message) = prepare(rig, other)  # the backup says "triangle" but its WAVE is square's
    assert plan is None and "wave" in message and rig.fake.edits == 0


def test_bulk_protect_stops_the_restore_at_once_and_says_so(rig):
    backup = backup_of(rig, "PG", "001")
    assert write(rig, "program", "program_level", 30, "001").ok
    rig.fake.bulk_protect = True
    job, (plan, _msg) = prepare(rig, backup)
    result, _ = run(job)
    assert not result.ok and result.written == 0 and result.passes == 0  # it did not even go round again
    assert "Stopped" in result.message and "Bulk Protect" in result.message
    assert result.remaining  # it lists what was not restored


def test_cancel_stops_after_the_write_in_flight_and_reports_how_far_it_got(rig):
    backup = backup_of(rig, "SP", "pulse 1")
    for key, value in (("filter_cutoff", 10), ("pan", 5), ("sample_level", 50), ("detune", 3)):
        assert write(rig, "sample", key, value, "pulse 1").ok
    job, (plan, _msg) = prepare(rig, backup)
    got = []
    job.run(got.append, on_progress=lambda done, total, label: job.cancel() if done == 1 else None)
    assert wait_until(lambda: bool(got), timeout=30)
    result = got[0]
    assert not result.ok and result.written == 2 and "partly restored" in result.message and len(result.remaining) == 2
    assert result.snapshot_path is not None and result.snapshot_path.exists()  # the pre-restore state is safe


def test_coupled_addresses_are_settled_by_a_second_pass(qapp):  # noqa: F811
    class Coupled(demo.FakeA4000):
        """Like the real unit (measured: writing one wave/loop address moves others): writing the loop length also nudges the wave
        start - which a restore had ALREADY written (the wave start goes first)."""

        def _edit(self, m):
            super()._edit(m)
            msg = y.parse_parameter_message(m)
            if self.current and msg.params == yp.get("sample", "loop_length").p:
                data = self.samples[self.current[1]]
                row = yp.get("sample", "wave_start_address")
                yp.store(row, data, yp.extract(row, data) + 1)

    fake = Coupled()
    rig = build(fake)
    backup = backup_of(rig, "SP", "pulse 2")
    assert write(rig, "sample", "wave_start_address", 4, "pulse 2").ok
    assert write(rig, "sample", "loop_length", 50, "pulse 2").ok
    assert write(rig, "sample", "pan", 9, "pulse 2").ok
    job, _ = prepare(rig, backup)
    result, _ = run(job)
    row = yp.get("sample", "wave_start_address")
    assert result.ok and result.passes >= 2  # the first pass left the wave start one off; the second put it right
    assert yp.extract(row, fake.samples["pulse 2"]) == yp.extract(row, backup.data)


def test_assignments_that_changed_are_reported_but_the_matching_values_are_restored(rig):
    backup = backup_of(rig, "PG", "001")  # two samples assigned
    assert write(rig, "easy_edit", "level_offset", 25, "001", slot=0).ok
    rig.fake.programs[1][yp.PROGRAM_EASY_EDIT_BASE + yp.EASY_EDIT_BLOCK_SIZE * 1 : yp.PROGRAM_EASY_EDIT_BASE + yp.EASY_EDIT_BLOCK_SIZE * 2] = demo.seed.EASY_EDIT_EMPTY
    yp.store(yp.get("program", "assigned_samples"), rig.fake.programs[1], 1)  # the second sample was unassigned on the front panel
    job, (plan, _msg) = prepare(rig, backup)
    assert any("assigned sample" in n for n in plan.notes)
    result, _ = run(job)
    assert result.ok and any("assigned sample" in n for n in result.notes)
    assert yp.extract(yp.get("easy_edit", "level_offset"), rig.fake.programs[1], 0) == 0  # slot 0's value is back
    assert yp.extract(yp.get("program", "assigned_samples"), rig.fake.programs[1]) == 1  # the assignment was not touched


def test_a_snapshot_that_cannot_be_saved_means_nothing_is_written(rig, tmp_path):
    backup = backup_of(rig, "SP", "pulse 3")
    assert write(rig, "sample", "pan", 4, "pulse 3").ok
    blocker = tmp_path / "file"
    blocker.write_text("x")
    rig.session.backup_dir = blocker / "backups"
    edits = rig.fake.edits
    job, _ = prepare(rig, backup)
    result, _ = run(job)
    assert not result.ok and result.written == 0 and "snapshot" in result.message and rig.fake.edits == edits
