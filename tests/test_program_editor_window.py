# tests for program_editor_window.py
# needs a real (offscreen) QApplication since these are actual widgets,
# not pure logic - same pattern used to verify the waveform renderer earlier

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import s3k.messages as s3k_messages
import s3k.params as s3k_params
from PySide6.QtCore import Qt, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from ui.program_editor_window import ProgramEditorWindow, _LOOP_TYPE_OPTIONS
from core.program_editor_bridge import MULTI_PART_COUNT


class FakeBridge:
    def __init__(self):
        self._programs = ["Bass stab", "EPiano warm"]
        self._pan = {0: -10, 1: 25}
        self._samples = ["SQUARE", "SAWTOOTH", "PULSE", "SINE"]
        self._keygroups = {
            0: [(24, 60), (61, 96)],  # (LONOTE, HINOTE) per keygroup
            1: [(24, 96)],
        }
        self._details = {0: 72, 1: 8, 2: 30, 3: 60, 4: 90}
        # multipart channel/level/pan per part - distinct values so a test
        # can catch one part's write landing on the wrong part
        self.multipart_channels = list(range(MULTI_PART_COUNT))
        self.multipart_levels = [60 + i for i in range(MULTI_PART_COUNT)]
        self.multipart_pans = [i - 8 for i in range(MULTI_PART_COUNT)]
        self.multi_name = "DEMO MULTI"
        self.set_parameter_calls = []
        # sample header (Samples tab / loop points) - distinct values, real
        # loop math (LOOPAT1 is the loop END, not the start - see AGENTS.md/
        # _SAMPLE_DETAIL_FIELDS - so loop_start = 8000 - 3000 = 5000)
        self.sample_headers = {
            i: {
                "SSTART": 100, "SMPEND": 9999, "LOOPAT1": 8000,
                "LLNGTH1": 3000, "SLNGTH": 10000, "SSRATE": 44100,
                "SPTYPE": 0, "SPITCH": 60,
            }
            for i in range(len(self._samples))
        }

    def sample_list(self):
        return self._samples

    def delete_sample(self, sample_index):
        del self._samples[sample_index]
        del self.sample_headers[sample_index]
        # re-key every later header down by one, matching how _samples'
        # own positions just shifted - real hardware addresses samples by
        # position/number, so a lower-index delete shifts every later one
        # down too; without this, a header fetch for a sample after the
        # deleted one would silently return some OTHER sample's data
        self.sample_headers = {
            (i - 1 if i > sample_index else i): header
            for i, header in self.sample_headers.items()
        }

    def program_list(self):
        return self._programs

    # -- create program/keygroup (PDATA/KDATA) -------------------------------
    #
    # unlike this class's other fakes, get_header_bytes/send_and_receive
    # here actually decode/build real wire-format bytes via s3k.messages/
    # s3k.params, and send_and_receive genuinely mutates self._programs/
    # self._keygroups - so a "Duplicate Program"/"Duplicate Keygroup" test
    # exercises the real ProgramEditorWindow.._confirm_duplicate_*
    # -> BridgeWorker._handle_create_* -> program_list()/get_parameter()
    # round trip end to end, the same way test_reverse_sample_real_mode_
    # happy_path_* exercises trim/reverse's own multi-step real-mode flow

    def get_header_bytes(self, region, index, offset, count, selector=0, **kwargs):
        assert offset == 0 and count == 192
        header = bytearray(192)
        if region == "program":
            header[0] = 0x01  # BLOCK_IDENT["program"], see s3k.bridge
            name_param = s3k_params.lookup("PRNAME", "program")
            header[name_param.offset : name_param.offset + name_param.size] = (
                s3k_params.encode_field(name_param, self._programs[index])
            )
            groups_param = s3k_params.lookup("GROUPS", "program")
            header[groups_param.offset : groups_param.offset + groups_param.size] = (
                s3k_params.encode_field(groups_param, len(self._keygroups[index]))
            )
            return bytes(header)
        if region == "keygroup":
            header[0] = 0x02  # BLOCK_IDENT["keygroup"]
            lo, hi = self._keygroups[index][selector]
            lo_param = s3k_params.lookup("LONOTE", "keygroup")
            hi_param = s3k_params.lookup("HINOTE", "keygroup")
            header[lo_param.offset : lo_param.offset + lo_param.size] = (
                s3k_params.encode_field(lo_param, lo)
            )
            header[hi_param.offset : hi_param.offset + hi_param.size] = (
                s3k_params.encode_field(hi_param, hi)
            )
            return bytes(header)
        raise AssertionError(f"unexpected region {region!r}")

    def send_and_receive(self, frame, timeout=None):
        _channel, command, payload = s3k_messages.parse_frame(frame)
        if command == s3k_messages.Command.PDATA:
            program_index = payload[0] | (payload[1] << 7)
            header = s3k_messages.decode_nibbles(payload[2:])
            name_param = s3k_params.lookup("PRNAME", "program")
            name = s3k_params.decode_field(
                name_param,
                header[name_param.offset : name_param.offset + name_param.size],
            ).strip()
            if program_index == len(self._programs):
                self._programs.append(name)
                self._keygroups[program_index] = []
            else:
                self._programs[program_index] = name
        elif command == s3k_messages.Command.KDATA:
            program_index = payload[0] | (payload[1] << 7)
            keygroup_index = payload[2]
            header = s3k_messages.decode_nibbles(payload[3:])
            lo_param = s3k_params.lookup("LONOTE", "keygroup")
            hi_param = s3k_params.lookup("HINOTE", "keygroup")
            lo = s3k_params.decode_field(
                lo_param, header[lo_param.offset : lo_param.offset + lo_param.size]
            )
            hi = s3k_params.decode_field(
                hi_param, header[hi_param.offset : hi_param.offset + hi_param.size]
            )
            keygroups = self._keygroups.setdefault(program_index, [])
            if keygroup_index == len(keygroups):
                keygroups.append((lo, hi))
            else:
                keygroups[keygroup_index] = (lo, hi)
        else:
            raise AssertionError(f"unexpected command {command:#04x}")
        return s3k_messages.Reply(code=int(s3k_messages.ReplyCode.OK)).encode()

    def get_header(self, region, index, **kwargs):
        if region == "multipart":
            return {
                "PRNAME": self._programs[index % len(self._programs)],
                "PMCHAN": self.multipart_channels[index],
                "STEREO": self.multipart_levels[index],
                "PANPOS": self.multipart_pans[index],
            }
        raise AssertionError(f"unexpected region {region!r}")

    def set_parameter(self, param, program_index, value, *, keygroup=0):
        self.set_parameter_calls.append((param.name, program_index, value, keygroup))
        if param.region == "multi" and param.name == "MULTINAME":
            self.multi_name = value
            return
        if param.region == "sample" and param.name == "SHNAME":
            self._samples[program_index] = value
            return
        # PMCHAN/PANPOS exist in both the "program" and "multipart" regions
        # (see program_editor_window.py's midi_channel_combo/pan_knob vs.
        # this multipart bookkeeping) - keyed on region too so a program-level
        # write doesn't misattribute itself to some part's row
        if param.region != "multipart":
            return
        if param.name == "PMCHAN":
            self.multipart_channels[program_index] = value
        if param.name == "STEREO":
            self.multipart_levels[program_index] = value
        if param.name == "PANPOS":
            self.multipart_pans[program_index] = value

    def get_parameter(self, param, program_index, keygroup=0, **kwargs):
        if param.region == "sample":
            # program_index here is really a sample index - checked first
            # since it's unrelated to (and needn't be a valid) program index
            return self.sample_headers[program_index][param.name]
        if param.region == "multi" and param.name == "MULTINAME":
            return self.multi_name
        if param.name == "PANPOS":
            return self._pan[program_index]
        if param.name == "PRLOUD":
            return 65
        if param.name == "V_LOUD":
            return -15
        if param.name == "POLYPH":
            return 8
        if param.name == "PMCHAN":
            return 3
        if param.name == "PTUNO":
            return 256  # +1.00 semitone, see _tune_offset_to_semitones
        if param.name == "PRIORT":
            return 2  # high
        if param.name in ("LFORAT", "LFODEP", "LFODEL", "LFO1WAVE"):
            return 0
        # DESYNC is LFO1's own sync/desync toggle (see lfo1_sync_combo's
        # construction comment on the value polarity) - 1 here means
        # DESYNC=ON (desynced), which lfo1_sync_combo should show as index 1
        # ("Off", i.e. Sync Off)
        if param.name == "DESYNC":
            return 1
        # LFO2 (hardwired to Pan on this hardware - see
        # program_editor_window.py's LFO2 card comment) - deliberately
        # different from LFORAT/LFODEP/LFODEL/LFO1WAVE above so a test can
        # catch LFO2's fields being wired to LFO1's by mistake
        if param.name == "PANRAT":
            return 45
        if param.name == "PANDEP":
            return 17
        if param.name == "PANDEL":
            return 3
        if param.name == "LFO2WAVE":
            return 1  # Sawtooth
        if param.name == "LFO2TRIG":
            return 1  # On - boolean per hardware measurement, see AGENTS.md
        # modulation matrix - MODSPAN1/MODVPAN1 given real, distinct values
        # (rather than falling through to the generic "return 30" below)
        # since MODSPAN1 must be a valid combo index (0-13), and a
        # catch-all 30 would leave that combo showing no selection at all
        if param.name == "MODSPAN1":
            return 5  # velocity
        if param.name == "MODVPAN1":
            return -12
        if param.name == "MODVFILT1":
            return -8  # keygroup-region amount, see MODSFILT1's own source
        if param.name in ("SNAME1", "SNAME2", "SNAME3", "SNAME4"):
            return "SQUARE"  # a real sample name that matches _samples below
        if param.name in ("ZPLAY1", "ZPLAY2", "ZPLAY3", "ZPLAY4"):
            return int(param.name[-1]) - 1  # distinct per zone: 0, 1, 2, 3
        if param.name in ("CP1", "CP2", "CP3", "CP4"):
            return int(param.name[-1]) % 2  # alternates TRACK/CONST by zone
        if param.name == "B_PTCH":
            return 7
        if param.name == "B_PTCHD":
            return 4
        if param.name == "PORTEN":
            return 1  # On
        if param.name == "PORTIME":
            return 55
        if param.name == "PORTYPE":
            return 1  # Time
        keygroups = self._keygroups[program_index]
        if param.name == "GROUPS":
            return len(keygroups)
        if keygroup >= len(keygroups):
            raise ValueError(
                f"program {program_index} has {len(keygroups)} keygroup(s)"
            )
        if param.name == "LONOTE":
            return keygroups[keygroup][0]
        if param.name == "HINOTE":
            return keygroups[keygroup][1]
        if param.name == "FILFRQ":
            return 72
        if param.name == "FILQ":
            return 8
        if param.name == "K_FREQ":
            return -5
        return 30


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _pump_until(qapp, predicate, timeout=2.0):
    # a queued cross-thread signal isn't always delivered by a single
    # processEvents() call - measured to sometimes need a second pass before
    # the slot it triggers (which sets the attribute a caller is waiting on)
    # actually runs, so this polls instead of assuming one pump is enough
    deadline = time.monotonic() + timeout
    while not predicate():
        qapp.processEvents()
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met before timeout")


def _wait_for_program_load(editor, qapp):
    # the worker is a single persistent background thread now (see
    # BridgeWorker) rather than a fresh QThread per request, so
    # wait_until_idle() replaces waiting on a specific loader's own
    # .wait() - it blocks until every job submitted so far has actually
    # been processed and its result signal emitted. Delivery of that
    # (queued, cross-thread) signal to the slot that reacts to it still
    # needs a pump, same as before.
    editor._worker.wait_until_idle()  # program list loaded -> submits sample list
    _pump_until(qapp, lambda: editor.program_list.count() > 0)
    editor._worker.wait_until_idle()  # sample list loaded -> selects row 0
    _pump_until(qapp, lambda: editor.program_list.currentRow() == 0)


def _wait_for_keygroup_load(editor, qapp, expected_count):
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.keygroup_list.count() == expected_count)


@pytest.fixture
def editor(qapp):
    fake_main_window = QWidget()
    editor = ProgramEditorWindow(fake_main_window, bridge=FakeBridge())
    _wait_for_program_load(editor, qapp)
    _wait_for_keygroup_load(editor, qapp, expected_count=2)  # program 0 has 2
    yield editor
    # BridgeWorker is a persistent thread that only exits once told to (see
    # its stop()) - unlike the old per-action QThreads, it's still running
    # long after every test assertion above is done, so it must be shut
    # down explicitly or it gets garbage-collected mid-run, which is
    # exactly the real-hardware crash this class exists to prevent
    editor._worker.stop()
    editor._worker.wait()


def _keygroup_row_text(editor, row):
    # keygroup rows are a swatch + QLabel row widget (program_editor_window's
    # _add_keygroup_row), not plain text items - the item's own .text() is
    # always empty, the displayed text lives in the row widget's label
    item = editor.keygroup_list.item(row)
    label = editor.keygroup_list.itemWidget(item).findChild(QLabel, "keygroupRangeLabel")
    return label.text()


class FakeSamplerController(QObject):
    # stands in for main_window.sampler_controller in
    # _perform_sample_edit_real tests - a real QObject with real Signals
    # (not the fake-Qt harness test_sampler_controller.py uses), since
    # _wait_for_any_signal needs genuine signal/slot delivery through a
    # real QEventLoop. send_file_queue mutates the SAME FakeBridge
    # instance the editor's own BridgeWorker reads from, mirroring how
    # real hardware is one shared device behind two separate connections
    # (see AGENTS.md's "Samples tab" section) - a send here is visible to
    # a subsequent submit_sample_list() the same way it would be on a
    # real sampler.
    transfer_progress = Signal(int, int)
    transfer_finished = Signal(bool)
    file_transferred = Signal(str)

    def __init__(self, bridge):
        super().__init__()
        self._bridge = bridge
        self.sent_entries = []
        self.next_result = True  # what the next send's transfer_finished reports
        self.busy = False

    def is_transfer_busy(self):
        return self.busy

    def send_file_queue(self, file_entries, channel=None, starting_sample_number=None):
        entry = file_entries[0]
        # read the sent WAV's own samples now, while the file still
        # exists - _perform_sample_edit_real deletes its temp file in a
        # finally: block as soon as the whole operation returns, well
        # before a test gets a chance to inspect it afterwards
        from core import sds_encoder

        sent_samples, sent_rate = sds_encoder.read_wav_samples(entry["filepath"])
        self.sent_entries.append({**entry, "samples": list(sent_samples), "rate": sent_rate})
        result = self.next_result
        if result:
            new_index = len(self._bridge._samples)
            self._bridge._samples.append(entry["name"])
            self._bridge.sample_headers[new_index] = {
                "SSTART": 0, "SMPEND": 0, "LOOPAT1": 0, "LLNGTH1": 0,
                "SLNGTH": 0, "SSRATE": 44100, "SPTYPE": 0, "SPITCH": 60,
            }
        # deferred, not synchronous - _perform_sample_edit_real connects
        # its _wait_for_any_signal listener AFTER calling send_file_queue,
        # same as the real (fully async) SamplerController; emitting
        # synchronously here would fire before that connection exists and
        # the wait would hang forever, same as the real one would if it
        # somehow replied before the caller finished wiring up
        QTimer.singleShot(0, lambda: self._finish_send(result, entry["filepath"]))
        return True

    def _finish_send(self, result, filepath):
        if result:
            self.file_transferred.emit(filepath)
        self.transfer_finished.emit(result)


def test_mod_source_labels_match_s3k_params_minus_env3():
    # _MOD_SOURCE_LABELS is a hand-written parallel list (see its own
    # comment for why), not derived from s3k.params.MOD_SOURCES at import
    # time - this guards against it silently drifting out of sync if the
    # pinned s3ked/s3k revision ever changes that enum
    import s3k.params as p
    from ui.program_editor_window import _MOD_SOURCE_LABELS

    assert len(_MOD_SOURCE_LABELS) == len(p.MOD_SOURCES) - 1  # env3 excluded
    assert set(p.MOD_SOURCES) - {14} == set(range(len(_MOD_SOURCE_LABELS)))


def test_sample_playback_type_options_match_s3k_params_sptype():
    # _SAMPLE_PLAYBACK_TYPE_OPTIONS is a hand-written parallel list built
    # from _LOOP_TYPE_OPTIONS's own tooltips (see its own comment for why) -
    # this guards its LABELS against silently drifting out of sync with
    # SPTYPE's own raw-byte-order values= dict if the pinned s3k revision
    # ever changes it, the same way test_mod_source_labels_match_s3k_params
    # _minus_env3 guards _MOD_SOURCE_LABELS
    import s3k.params as p
    from ui.program_editor_window import _SAMPLE_PLAYBACK_TYPE_OPTIONS

    sptype = p.lookup("SPTYPE", "sample")
    assert len(_SAMPLE_PLAYBACK_TYPE_OPTIONS) == len(sptype.values)
    for raw_value, label in sptype.values.items():
        assert _SAMPLE_PLAYBACK_TYPE_OPTIONS[raw_value][0] == label


def test_first_program_is_preselected_with_its_keygroups_shown(editor):
    assert editor.program_list.currentItem().text() == "Bass stab"
    assert editor.keygroup_list.count() == 2
    assert _keygroup_row_text(editor, 0) == "Keygroup 1: C0 - C3"


# isHidden(), not isVisible() - the editor fixture never calls show() on
# the window (see its own comment), and isVisible() is unconditionally
# false for any widget whose top-level window was never shown. isHidden()
# tracks the widget's own explicit setVisible() state regardless.


def test_progress_bar_stays_hidden_across_the_fixtures_fast_loads(editor, qapp):
    # the editor fixture already drove program list + sample list + first
    # keygroup load to completion - FakeBridge answers near-instantly, well
    # under the 200ms hold-off, so the indeterminate bar should never have
    # actually become visible for any of it
    assert editor._loading_progress.isHidden()


def test_busy_changed_true_arms_the_show_timer_without_showing_the_bar_yet(editor):
    editor._on_worker_busy_changed(True)

    assert editor._busy_show_timer.isActive()
    assert editor._loading_progress.isHidden()


def test_show_timer_firing_while_still_busy_shows_the_bar(editor):
    editor._on_worker_busy_changed(True)
    editor._busy_show_timer.timeout.emit()  # simulates the real 200ms elapsing

    assert not editor._loading_progress.isHidden()


def test_busy_changed_false_does_not_hide_immediately(editor):
    # hiding is debounced through _busy_hide_timer (see the comment where
    # these two timers are built) - a bare busy_changed(False) only arms
    # that timer, it doesn't hide on the spot
    editor._on_worker_busy_changed(True)
    editor._busy_show_timer.timeout.emit()
    assert not editor._loading_progress.isHidden()

    editor._on_worker_busy_changed(False)

    assert editor._busy_hide_timer.isActive()
    assert not editor._loading_progress.isHidden()  # still shown until confirmed


def test_hide_timer_firing_confirms_idle_and_hides_the_bar(editor):
    editor._on_worker_busy_changed(True)
    editor._busy_show_timer.timeout.emit()
    editor._on_worker_busy_changed(False)

    editor._busy_hide_timer.timeout.emit()  # simulates the grace period elapsing

    assert editor._loading_progress.isHidden()
    assert not editor._busy_show_timer.isActive()


def test_a_new_busy_true_during_the_hide_grace_period_cancels_the_hide(editor):
    # this is what collapses a burst of back-to-back jobs (e.g. Refresh)
    # into one continuous visible span rather than flickering - see the
    # comment on _busy_hide_timer
    editor._on_worker_busy_changed(True)
    editor._busy_show_timer.timeout.emit()
    editor._on_worker_busy_changed(False)  # arms the hide timer
    editor._on_worker_busy_changed(True)  # a new job lands before it fires

    assert not editor._busy_hide_timer.isActive()
    assert not editor._loading_progress.isHidden()  # never actually hid


def test_refreshing_from_hardware_reports_one_continuous_busy_span(editor, qapp):
    # regression check for the real worry with this feature: on a fast
    # bridge (fake or demo), the worker thread can race ahead and briefly
    # drain the queue to empty *between* two of Refresh's back-to-back
    # submit_*() calls (program list, multi parts, keygroups), which is
    # exactly what _on_worker_busy_changed's hide-timer debounce exists to
    # absorb (see test_a_new_busy_true_during_the_hide_grace_period_cancels_the_hide
    # for that mechanism in isolation) - this just checks the real call
    # path reaches a clean hidden state rather than exercising the race
    # itself, which isn't reliably reproducible from a test
    editor._refresh_from_hardware()
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor._loading_progress.isHidden())
    assert editor._loading_progress.isHidden()


def test_program_name_field_loads_from_the_selected_list_item(editor):
    # program_list's item text already IS the current PRNAME (it comes
    # straight from FakeBridge.program_list()) - no separate hardware
    # round-trip needed to populate this field
    assert editor.program_name_edit.text() == "Bass stab"

    editor.program_list.setCurrentRow(1)

    assert editor.program_name_edit.text() == "EPiano warm"


def test_typing_a_program_name_uppercases_live_and_updates_the_list(editor):
    # _on_program_name_typed is wired to textEdited, which only fires from
    # real keystrokes - called directly here, same as this suite calls
    # other signal handlers directly elsewhere (e.g. _on_note_range_changed)
    editor._on_program_name_typed("bass hit")

    assert editor.program_name_edit.text() == "BASS HIT"
    # keeps the Programs list in step while typing, before any write -
    # same idea as the keygroup list's range label during note editing
    assert editor.program_list.currentItem().text() == "BASS HIT"


def test_committing_a_program_name_writes_prname_and_trims_trailing_spaces(
    editor, qapp
):
    bridge = editor._bridge

    editor.program_name_edit.setText("PAD  ")  # trailing spaces are typable
    editor._commit_program_name()
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert bridge.set_parameter_calls[-1] == ("PRNAME", 0, "PAD", 0)
    assert editor.program_name_edit.text() == "PAD"
    assert editor.program_list.currentItem().text() == "PAD"


def test_renaming_a_program_updates_every_multi_part_combo(editor):
    # regression test for a real bug: renaming updated the Programs list
    # but every one of the 16 Multis-tab part combos kept showing the old
    # name until the next full Refresh, even though they're populated from
    # the very same program list and the hardware had already moved on.
    # _multi_program_combos are populated in _on_programs_loaded regardless
    # of whether any part is actually assigned to program 0, so this
    # doesn't need a multi-parts load first - just the item text itself.
    editor.program_name_edit.setText("PAD")
    editor._commit_program_name()

    # program_list index 0 -> combo index 1 (index 0 is the blank "-"
    # placeholder - see _build_multis_tab)
    for combo in editor._multi_program_combos:
        assert combo.itemText(1) == "PAD"
        assert combo.itemText(2) == "EPiano warm"  # program 1 untouched


def test_typing_a_program_name_also_updates_multi_part_combos_live(editor):
    editor._on_program_name_typed("bass hit")

    for combo in editor._multi_program_combos:
        assert combo.itemText(1) == "BASS HIT"


def test_program_name_input_rejects_characters_outside_the_akai_charset(editor):
    # AKAI_CHARSET (s3k.messages) is "0-9, space, A-Z, #+-." - lowercase
    # is accepted at the validator level (case-insensitive, since
    # _on_program_name_typed uppercases separately) but punctuation like
    # "!" isn't in the device's character set at all and must be refused
    validator = editor.program_name_edit.validator()
    from PySide6.QtGui import QValidator

    assert validator.validate("bass", 4)[0] == QValidator.State.Acceptable
    assert validator.validate("BASS-1", 6)[0] == QValidator.State.Acceptable
    assert validator.validate("BASS!", 5)[0] != QValidator.State.Acceptable


def test_program_tab_loads_loud_and_velocity_from_hardware(editor):
    # FakeBridge.get_parameter reports PRLOUD=65, V_LOUD=-15
    assert editor.loud_knob.value() == 65
    assert editor.velocity_knob.value() == -15


def test_changing_loud_and_velocity_writes_prloud_and_v_loud(editor, qapp):
    bridge = editor._bridge

    editor.loud_knob.setValue(50)
    editor.velocity_knob.setValue(-30)
    editor._flush_write("PRLOUD")
    editor._flush_write("V_LOUD")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: len(bridge.set_parameter_calls) >= 2)

    assert bridge.set_parameter_calls[-2] == ("PRLOUD", 0, 50, 0)
    assert bridge.set_parameter_calls[-1] == ("V_LOUD", 0, -30, 0)


def test_program_tab_loads_channel_tune_and_priority_from_hardware(editor):
    # FakeBridge.get_parameter reports PMCHAN=3 (channel 4), PTUNO=256
    # (+1.00 semitone, see _tune_offset_to_semitones), PRIORT=2 (High)
    assert editor.midi_channel_combo.currentData() == 3
    assert editor.midi_channel_combo.currentText() == "4"
    assert editor.program_tune_spinbox.value() == pytest.approx(1.0)
    assert editor.note_priority_combo.currentText() == "High"


def test_changing_midi_channel_writes_pmchan(editor, qapp):
    bridge = editor._bridge

    editor.midi_channel_combo.setCurrentIndex(
        editor.midi_channel_combo.findData(255)  # Omni
    )
    editor._flush_write("PMCHAN")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert bridge.set_parameter_calls[-1] == ("PMCHAN", 0, 255, 0)


def test_changing_note_priority_writes_priort(editor, qapp):
    bridge = editor._bridge

    editor.note_priority_combo.setCurrentIndex(3)  # Hold

    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert bridge.set_parameter_calls[-1] == ("PRIORT", 0, 3, 0)


def test_changing_program_tune_writes_ptuno_in_raw_units(editor, qapp):
    # same raw encoding as the keygroup zone's Tune spinbox - 2.56 raw
    # units per cent, so -2.00 semitones is raw -512
    bridge = editor._bridge

    editor.program_tune_spinbox.setValue(-2.0)
    editor._flush_write("PTUNO")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert bridge.set_parameter_calls[-1] == ("PTUNO", 0, -512, 0)


def test_program_tab_loads_bend_and_portamento_from_hardware(editor):
    # FakeBridge.get_parameter: B_PTCH=7, B_PTCHD=4, PORTEN=1 (On),
    # PORTIME=55, PORTYPE=1 (Time)
    assert editor.bend_up_combo.currentIndex() == 7
    assert editor.bend_down_combo.currentIndex() == 4
    assert editor.portamento_enable_combo.currentText() == "On"
    assert editor.portamento_rate_knob.value() == 55
    assert editor.portamento_type_combo.currentText() == "Time"


def test_changing_bend_up_and_down_writes_separately(editor, qapp):
    # B_PTCH (up, 0-24) and B_PTCHD (down, 0-12) are different fields with
    # different hardware-declared ranges - not one control mirrored twice
    bridge = editor._bridge

    editor.bend_up_combo.setCurrentIndex(12)
    editor._flush_write("B_PTCH")
    editor.bend_down_combo.setCurrentIndex(9)
    editor._flush_write("B_PTCHD")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: len(bridge.set_parameter_calls) >= 2)

    assert bridge.set_parameter_calls[-2] == ("B_PTCH", 0, 12, 0)
    assert bridge.set_parameter_calls[-1] == ("B_PTCHD", 0, 9, 0)


def test_changing_portamento_controls_writes_porten_portime_portype(editor, qapp):
    bridge = editor._bridge

    editor.portamento_enable_combo.setCurrentIndex(0)  # Off (loaded as On)
    editor._flush_write("PORTEN")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)
    assert bridge.set_parameter_calls[-1] == ("PORTEN", 0, 0, 0)

    editor.portamento_rate_knob.setValue(20)
    editor._flush_write("PORTIME")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls[-1][0] == "PORTIME")
    assert bridge.set_parameter_calls[-1] == ("PORTIME", 0, 20, 0)

    editor.portamento_type_combo.setCurrentIndex(0)  # Rate (loaded as Time)
    editor._flush_write("PORTYPE")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls[-1][0] == "PORTYPE")
    assert bridge.set_parameter_calls[-1] == ("PORTYPE", 0, 0, 0)


def test_portamento_type_combo_has_a_tooltip_per_option(editor):
    from ui.program_editor_window import _PORTAMENTO_TYPE_OPTIONS

    combo = editor.portamento_type_combo
    assert combo.count() == len(_PORTAMENTO_TYPE_OPTIONS)
    for i, (label, tooltip) in enumerate(_PORTAMENTO_TYPE_OPTIONS):
        assert combo.itemText(i) == label
        assert combo.itemData(i, Qt.ItemDataRole.ToolTipRole) == tooltip

    combo.setCurrentIndex(0)
    assert combo.toolTip() == _PORTAMENTO_TYPE_OPTIONS[0][1]


def test_keygroup_panel_loads_and_writes_key_filter_tracking(editor, qapp):
    editor.keygroup_list.setCurrentRow(1)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.cutoff_knob.value() == 72)

    # FakeBridge.get_parameter reports K_FREQ=-5
    assert editor.key_filter_track_knob.value() == -5

    bridge = editor._bridge
    editor.key_filter_track_knob.setValue(20)  # within the confirmed -24..24
    editor._flush_write("K_FREQ")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert bridge.set_parameter_calls[-1] == ("K_FREQ", 0, 20, 1)


def test_lfo2_panel_loads_and_writes(editor, qapp):
    # FakeBridge.get_parameter reports PANRAT=45/PANDEP=17/PANDEL=3/
    # LFO2WAVE=1 - distinct from LFO1's own (all 0), so this also catches
    # LFO2's fields being accidentally wired to LFO1's
    assert editor.lfo2_rate_knob.value() == 45
    assert editor.lfo2_depth_knob.value() == 17
    assert editor.lfo2_delay_knob.value() == 3
    assert editor.lfo2_shape_combo.currentIndex() == 1

    bridge = editor._bridge
    editor.lfo2_rate_knob.setValue(60)
    editor._flush_write("PANRAT")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert bridge.set_parameter_calls[-1] == ("PANRAT", 0, 60, 0)


def test_lfo1_sync_combo_loads_and_writes(editor, qapp):
    # FakeBridge reports DESYNC=1 (desynced) - lfo1_sync_combo shows this
    # the positive "Sync" way round (see its own construction comment on
    # the polarity): index 1 = "Off" (Sync Off, i.e. desynced), even though
    # the raw byte and the combo index are NOT inverted relative to each
    # other (index 1 IS raw 1 here)
    assert editor.lfo1_sync_combo.currentIndex() == 1
    assert editor.lfo1_sync_combo.currentText() == "Off"

    bridge = editor._bridge
    editor.lfo1_sync_combo.setCurrentIndex(0)  # "On" -> DESYNC=0
    editor._flush_write("DESYNC")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert bridge.set_parameter_calls[-1] == ("DESYNC", 0, 0, 0)


def test_lfo2_retrig_combo_loads_and_writes(editor, qapp):
    # LFO2TRIG used to be a plain 0-255 spinbox with no write wiring at all
    # (s3k.params documents no enum for it) - now a boolean combo per
    # hardware measurement (see its own construction comment, and AGENTS.md)
    assert editor.lfo2_trig_combo.currentIndex() == 1
    assert editor.lfo2_trig_combo.currentText() == "On"

    bridge = editor._bridge
    editor.lfo2_trig_combo.setCurrentIndex(0)  # "Off"
    editor._flush_write("LFO2TRIG")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert bridge.set_parameter_calls[-1] == ("LFO2TRIG", 0, 0, 0)


def test_modulation_pan_slot_loads_and_writes(editor, qapp):
    # program-level half of the assignable modulation matrix - source
    # combo's index doubles as the raw MOD_SOURCES value (see
    # _MOD_SOURCE_LABELS), same convention as every other combo here
    assert editor.mod_pan1_combo.currentIndex() == 5  # "Velocity"
    assert editor.mod_pan1_combo.currentText() == "Velocity"
    assert editor.mod_pan1_knob.value() == -12

    bridge = editor._bridge
    editor.mod_pan1_combo.setCurrentIndex(7)  # "LFO1"
    editor._flush_write("MODSPAN1")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls[-1][0] == "MODSPAN1")
    assert bridge.set_parameter_calls[-1] == ("MODSPAN1", 0, 7, 0)

    editor.mod_pan1_knob.setValue(25)
    editor._flush_write("MODVPAN1")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls[-1][0] == "MODVPAN1")
    assert bridge.set_parameter_calls[-1] == ("MODVPAN1", 0, 25, 0)


def test_keygroup_modulation_filter_amount_loads_and_writes(editor, qapp):
    # keygroup-level half of the same destination (Filter Frequency): its
    # SOURCE is chosen program-wide (MODSFILT1, tested above via the Pan
    # slot's same mechanism) but its AMOUNT is stored per-keygroup - see
    # the Modulation card comments in program_editor_window.py
    editor.keygroup_list.setCurrentRow(1)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.mod_filt1_amount_knob.value() == -8)

    bridge = editor._bridge
    editor.mod_filt1_amount_knob.setValue(19)
    editor._flush_write("MODVFILT1")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls[-1][0] == "MODVFILT1")

    # keygroup_index (last element) is 1 - the currently selected keygroup
    assert bridge.set_parameter_calls[-1] == ("MODVFILT1", 0, 19, 1)


def test_refresh_picks_up_a_program_created_on_the_hardware(editor, qapp):
    # regression test for a real bug: Refresh re-loaded the current
    # program's keygroups but never re-fetched the program list itself, so
    # a program created on the hardware after the editor opened only ever
    # showed up after closing and reopening the window
    bridge = editor._bridge
    bridge._programs.append("New Program")

    editor._refresh_from_hardware()
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.program_list.count() == 3)

    assert [
        editor.program_list.item(i).text() for i in range(editor.program_list.count())
    ] == ["Bass stab", "EPiano warm", "New Program"]
    # the previously-selected program stays selected across the refresh,
    # rather than resetting to the top of the list
    assert editor.program_list.currentItem().text() == "Bass stab"


# -----------------------
# Duplicate Program / Duplicate Keygroup (PDATA/KDATA)
# -----------------------


def test_duplicate_actions_enabled_with_a_selection_disabled_without(editor, qapp):
    editor.keygroup_list.setCurrentRow(0)
    editor._worker.wait_until_idle()
    assert editor._duplicate_program_action.isEnabled() is True
    assert editor._duplicate_keygroup_action.isEnabled() is True

    editor.program_list.setCurrentRow(-1)
    editor._update_list_context_actions_enabled()
    assert editor._duplicate_program_action.isEnabled() is False
    # keygroup selection follows the program - clearing the program clears
    # the keygroup list too, so the keygroup action should also go disabled
    assert editor.keygroup_list.currentRow() == -1
    assert editor._duplicate_keygroup_action.isEnabled() is False


def test_duplicate_actions_disabled_in_demo_mode_even_with_a_selection(
    editor, qapp, monkeypatch
):
    editor.keygroup_list.setCurrentRow(0)
    editor._worker.wait_until_idle()
    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    editor._update_list_context_actions_enabled()

    assert editor.program_list.currentRow() >= 0
    assert editor.keygroup_list.currentRow() >= 0
    assert editor._duplicate_program_action.isEnabled() is False
    assert editor._duplicate_keygroup_action.isEnabled() is False


def test_confirm_duplicate_program_submits_create_on_valid_name(editor, qapp, monkeypatch):
    bridge = editor._bridge
    monkeypatch.setattr(editor, "_prompt_akai_name", lambda *a, **k: "NEW PROGRAM")

    editor._confirm_duplicate_program()
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.program_list.count() == 3)

    assert bridge._programs == ["Bass stab", "EPiano warm", "NEW PROGRAM"]
    # the freshly duplicated program keeps every keygroup of its source
    assert bridge._keygroups[2] == bridge._keygroups[0]
    # and ends up selected once the reload lands, rather than resetting to
    # row 0 or leaving the source program selected (see
    # _pending_program_selection_index's own comment)
    assert editor.program_list.currentRow() == 2
    assert editor.program_list.currentItem().text() == "NEW PROGRAM"


def test_confirm_duplicate_program_does_nothing_when_dialog_cancelled(
    editor, qapp, monkeypatch
):
    bridge = editor._bridge
    monkeypatch.setattr(editor, "_prompt_akai_name", lambda *a, **k: None)

    editor._confirm_duplicate_program()

    assert bridge._programs == ["Bass stab", "EPiano warm"]


def test_confirm_duplicate_program_refuses_a_name_collision(editor, qapp, monkeypatch):
    import ui.program_editor_window as pew

    bridge = editor._bridge
    monkeypatch.setattr(editor, "_prompt_akai_name", lambda *a, **k: "EPiano warm")
    warnings = []
    monkeypatch.setattr(
        pew.QMessageBox,
        "warning",
        lambda *a, **k: warnings.append(a) or pew.QMessageBox.StandardButton.Ok,
    )

    editor._confirm_duplicate_program()

    # refused client-side before ever reaching the bridge - a same-name
    # PDATA write would silently delete the existing "EPiano warm" program
    # (see _confirm_duplicate_program's own comment)
    assert bridge._programs == ["Bass stab", "EPiano warm"]
    assert len(warnings) == 1


def test_confirm_duplicate_keygroup_submits_create_on_confirm(editor, qapp, monkeypatch):
    import ui.program_editor_window as pew

    bridge = editor._bridge
    editor.keygroup_list.setCurrentRow(0)
    editor._worker.wait_until_idle()
    monkeypatch.setattr(
        pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.Yes
    )

    editor._confirm_duplicate_keygroup()
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.keygroup_list.count() == 3)

    # appended as a clone of keygroup 0, (24, 60) per the fixture
    assert bridge._keygroups[0] == [(24, 60), (61, 96), (24, 60)]
    assert _keygroup_row_text(editor, 2) == "Keygroup 3: C0 - C3"


def test_confirm_duplicate_keygroup_does_nothing_when_declined(editor, qapp, monkeypatch):
    import ui.program_editor_window as pew

    bridge = editor._bridge
    editor.keygroup_list.setCurrentRow(0)
    editor._worker.wait_until_idle()
    monkeypatch.setattr(
        pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.No
    )

    editor._confirm_duplicate_keygroup()

    assert bridge._keygroups[0] == [(24, 60), (61, 96)]
    assert editor.keygroup_list.count() == 2


def test_switching_program_replaces_keygroup_list_without_crashing(editor, qapp):
    editor.program_list.setCurrentRow(1)
    _wait_for_keygroup_load(editor, qapp, expected_count=1)  # program 1 has 1

    assert editor.keygroup_list.count() == 1
    assert _keygroup_row_text(editor, 0) == "Keygroup 1: C0 - C6"


def test_selecting_a_keygroup_shows_the_keygroup_panel_with_real_values(editor, qapp):
    editor.keygroup_list.setCurrentRow(1)
    editor._worker.wait_until_idle()
    # detail_stack switches synchronously on selection, before detail_loaded
    # is even delivered, so pump until the loader's actual effect lands
    _pump_until(qapp, lambda: editor.cutoff_knob.value() == 72)

    assert editor.detail_stack.currentIndex() == 1  # switched to the keygroup panel
    assert editor.cutoff_knob.value() == 72
    assert editor.resonance_knob.value() == 8
    assert editor.note_lo_spinbox.value() == 61
    assert editor.note_hi_spinbox.value() == 96


def test_zone_panels_load_loop_type_keytrack_and_tune_from_hardware(editor, qapp):
    editor.keygroup_list.setCurrentRow(1)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.cutoff_knob.value() == 72)

    # FakeBridge.get_parameter: ZPLAY{n} -> n-1, CP{n} -> n%2, and VTUNO
    # isn't given an explicit branch so it falls to the generic 30, which
    # is +0.12 semitones (round(30/2.56)/100, see _tune_offset_to_semitones)
    assert [c.currentIndex() for c in editor._zone_looptype] == [0, 1, 2, 3]
    assert [c.currentIndex() for c in editor._zone_keytrack] == [1, 0, 1, 0]
    for tune_spinbox in editor._zone_tune:
        assert tune_spinbox.value() == pytest.approx(0.12)


def test_loop_type_combo_has_a_tooltip_per_option(editor):
    loop_combo = editor._zone_looptype[0]
    assert loop_combo.count() == len(_LOOP_TYPE_OPTIONS)
    for i, (label, tooltip) in enumerate(_LOOP_TYPE_OPTIONS):
        assert loop_combo.itemText(i) == label
        assert loop_combo.itemData(i, Qt.ItemDataRole.ToolTipRole) == tooltip

    # the closed combo box's own tooltip (shown without opening the popup)
    # tracks whichever option is currently selected
    loop_combo.setCurrentIndex(2)
    assert loop_combo.toolTip() == _LOOP_TYPE_OPTIONS[2][1]


def test_changing_zone_loop_type_and_keytrack_writes_zplay_and_cp(editor, qapp):
    editor.keygroup_list.setCurrentRow(1)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.cutoff_knob.value() == 72)
    bridge = editor._bridge

    # zone 1 loads as ZPLAY1=0 (As sample), CP1=1 (Const Pitch) - see
    # FakeBridge.get_parameter - so these are real changes, not no-ops
    editor._zone_looptype[0].setCurrentIndex(4)  # Play to sample end
    editor._zone_keytrack[0].setCurrentIndex(0)  # Track

    editor._flush_write("ZPLAY1")
    editor._flush_write("CP1")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: len(bridge.set_parameter_calls) >= 2)

    assert bridge.set_parameter_calls[-2] == ("ZPLAY1", 0, 4, 1)
    assert bridge.set_parameter_calls[-1] == ("CP1", 0, 0, 1)


def _wait_for_multi_parts_load(editor, qapp):
    editor._worker.wait_until_idle()
    # part 0's channel combo already defaults to channel 0 before any load
    # (see _build_multis_tab), so it can't tell "loaded" from "not loaded
    # yet" - the program combo can: it starts on the blank "-" placeholder
    # and only shows a real program once _on_multi_parts_loaded runs
    _pump_until(qapp, lambda: editor._multi_program_combos[0].currentText() != "-")


def test_multi_parts_loaded_populates_combos_from_hardware(editor, qapp):
    _wait_for_multi_parts_load(editor, qapp)

    # FakeBridge's get_header cycles ["Bass stab", "EPiano warm"] by part
    # index and reports multipart_channels[index] == index, level 60+index,
    # pan index-8
    assert editor._multi_program_combos[0].currentText() == "Bass stab"
    assert editor._multi_channel_combos[0].currentData() == 0
    assert editor._multi_level_knobs[0].value() == 60
    assert editor._multi_pan_knobs[0].value() == -8
    assert editor._multi_level_knobs[1].value() == 61
    assert editor._multi_pan_knobs[1].value() == -7
    assert editor._multi_program_combos[1].currentText() == "EPiano warm"
    assert editor._multi_channel_combos[1].currentData() == 1


def test_multi_name_loads_from_hardware(editor, qapp):
    _wait_for_multi_parts_load(editor, qapp)

    assert editor.multi_name_edit.text() == "DEMO MULTI"


def test_typing_a_multi_name_uppercases_live(editor, qapp):
    _wait_for_multi_parts_load(editor, qapp)

    editor._on_multi_name_typed("my multi")

    assert editor.multi_name_edit.text() == "MY MULTI"


def test_committing_a_multi_name_writes_multiname_with_unused_index(editor, qapp):
    # MULTINAME's item index is unused/reserved (only one resident multi) -
    # index=0 here is a placeholder, not a real per-multi target
    _wait_for_multi_parts_load(editor, qapp)
    bridge = editor._bridge

    editor.multi_name_edit.setText("NEW MULTI  ")  # trailing spaces typable
    editor._commit_multi_name()
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert bridge.set_parameter_calls[-1] == ("MULTINAME", 0, "NEW MULTI", 0)
    assert editor.multi_name_edit.text() == "NEW MULTI"
    assert bridge.multi_name == "NEW MULTI"


def test_selecting_a_multi_part_program_targets_the_right_program_index(editor, qapp):
    # regression test for an off-by-one: the program combo has a blank "-"
    # placeholder at index 0 (see _build_multis_tab), so its currentIndex
    # is always one more than the program's actual position in
    # program_list. A part assigned "Bass stab" (program_list index 0)
    # must send a Program Change for program_index 0, not 1.
    _wait_for_multi_parts_load(editor, qapp)
    sent = []
    editor._worker.submit_program_change = lambda *a: sent.append(a)

    program_combo = editor._multi_program_combos[0]
    program_combo.setCurrentIndex(program_combo.findText("EPiano warm"))

    assert sent == [(0, 1, "EPiano warm", 0)]


def test_changing_two_multi_part_channels_writes_both_independently(editor, qapp):
    # regression test for a real bug: _schedule_write/_flush_write/
    # _write_knob_value used to key every pending write by bare param_name
    # alone, which was safe when only one keygroup could ever be "current"
    # at a time. All 16 Multis-tab parts share the field name PMCHAN but are
    # all independently editable at once, so changing two parts' channels in
    # quick succession used to let the second write silently clobber the
    # first's pending value (and risk its in-flight QThread being garbage
    # collected before it finished).
    _wait_for_multi_parts_load(editor, qapp)
    bridge = editor._bridge

    editor._multi_channel_combos[0].setCurrentIndex(
        editor._multi_channel_combos[0].findData(5)
    )
    editor._multi_channel_combos[1].setCurrentIndex(
        editor._multi_channel_combos[1].findData(9)
    )

    # flush both pending debounced writes immediately rather than waiting
    # out the real 500ms timer
    editor._flush_write("multipart_channel_0")
    editor._flush_write("multipart_channel_1")

    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: len(bridge.set_parameter_calls) >= 2)

    pmchan_calls = [c for c in bridge.set_parameter_calls if c[0] == "PMCHAN"]
    assert ("PMCHAN", 0, 5, 0) in pmchan_calls
    assert ("PMCHAN", 1, 9, 0) in pmchan_calls


def test_changing_two_multi_part_levels_writes_both_independently(editor, qapp):
    # same per-part debounce_key concern as the channel test above - level
    # knobs share the field name STEREO across all 16 parts
    _wait_for_multi_parts_load(editor, qapp)
    bridge = editor._bridge

    editor._multi_level_knobs[0].setValue(40)
    editor._multi_level_knobs[1].setValue(70)

    editor._flush_write("multipart_level_0")
    editor._flush_write("multipart_level_1")

    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: len(bridge.set_parameter_calls) >= 2)

    stereo_calls = [c for c in bridge.set_parameter_calls if c[0] == "STEREO"]
    assert ("STEREO", 0, 40, 0) in stereo_calls
    assert ("STEREO", 1, 70, 0) in stereo_calls
    assert editor._multi_level_value_labels[0].text() == "40"


def test_changing_two_multi_part_pans_writes_both_independently(editor, qapp):
    # same per-part debounce_key concern - pan knobs share the field name
    # PANPOS across all 16 parts (and with the program-level Pan knob)
    _wait_for_multi_parts_load(editor, qapp)
    bridge = editor._bridge

    editor._multi_pan_knobs[0].setValue(-30)
    editor._multi_pan_knobs[1].setValue(25)

    editor._flush_write("multipart_pan_0")
    editor._flush_write("multipart_pan_1")

    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: len(bridge.set_parameter_calls) >= 2)

    panpos_calls = [c for c in bridge.set_parameter_calls if c[0] == "PANPOS"]
    assert ("PANPOS", 0, -30, 0) in panpos_calls
    assert ("PANPOS", 1, 25, 0) in panpos_calls
    assert editor._multi_pan_value_labels[0].text() == "-30"


def test_multi_part_channel_write_targets_the_part_not_the_selected_program(
    editor, qapp
):
    # _write_knob_value's index= override is what makes the write above
    # target part_index rather than program_list.currentRow() (the usual
    # target for every other field) - pin that down directly, since a
    # regression here would silently redirect every multipart write to
    # whatever program happens to be selected on the Programs tab
    _wait_for_multi_parts_load(editor, qapp)
    assert editor.program_list.currentRow() == 0  # from the editor fixture
    bridge = editor._bridge

    editor._schedule_write(
        "PMCHAN", "multipart", 11, index=7, debounce_key="multipart_channel_7"
    )
    editor._flush_write("multipart_channel_7")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert bridge.set_parameter_calls[-1] == ("PMCHAN", 7, 11, 0)


# --- Samples tab: header-only editing (see AGENTS.md's "Editing loop points
# without audio") -----------------------------------------------------------


def test_selecting_a_sample_shows_header_only_markers_before_audio_loads(editor, qapp):
    # the whole point of set_header/has_header - markers should show up
    # fast, from the header alone, well before anyone chooses to load the
    # slow audio (a real SDS transfer, seconds to minutes)
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())

    assert editor.waveform_view.has_waveform() is False
    # LOOPAT1=8000 is the loop END, LLNGTH1=3000 measured backwards from it
    # - loop_start = 8000 - 3000 = 5000 (see FakeBridge.sample_headers)
    assert editor.waveform_view.markers() == {
        "start": 100, "loop_start": 5000, "loop_end": 8000, "end": 9999,
    }

    loop_start_spinbox = editor._marker_spinboxes["loop_start"][1]
    assert loop_start_spinbox.isEnabled() is True
    assert loop_start_spinbox.value() == 5000


def test_switching_to_samples_tab_selects_first_sample_only_once(editor, qapp):
    assert editor.sample_list_widget.currentRow() == -1
    editor.main_tabs.setCurrentIndex(editor._samples_tab_index)
    assert editor.sample_list_widget.currentRow() == 0

    # a deliberate different selection must survive switching away and
    # back - this only ever fires when nothing is selected yet
    editor.sample_list_widget.setCurrentRow(2)
    editor.main_tabs.setCurrentIndex(0)  # Multis
    editor.main_tabs.setCurrentIndex(editor._samples_tab_index)
    assert editor.sample_list_widget.currentRow() == 2


def test_load_sample_waveform_preserves_header_only_edits(editor, qapp, monkeypatch):
    # regression test for the exact bug this whole feature is built to
    # avoid: editing a marker while only the header is loaded must survive
    # once real audio arrives, not get silently overwritten by a fresh
    # header re-read or demo re-derivation (_load_sample_waveform must
    # reuse the cache, not recompute markers, whenever an entry already
    # exists)
    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())

    # simulate the user having already dragged/typed loop_start to 4000
    # while only the header was loaded
    editor._sample_waveform_cache[0]["loop_start"] = 4000
    editor.waveform_view.set_marker("loop_start", 4000)

    # stub the slow audio fetch with something instant - the realistic
    # throttle (_DEMO_MS_PER_WORD) is exercised elsewhere and would make
    # this test take real wall-clock seconds otherwise
    monkeypatch.setattr(
        editor, "_fetch_demo_sample_audio", lambda sample_index: ([0] * 500, 44100)
    )

    editor._load_sample_waveform()

    assert editor._sample_waveform_cache[0]["loop_start"] == 4000
    assert editor.waveform_view.markers()["loop_start"] == 4000
    assert editor.waveform_view.has_waveform() is True


# --- Samples tab: marker push writes every field that actually moved -------
# WaveformView.push_marker means dragging/typing one marker can shove
# others out of the way too (see AGENTS.md) - _schedule_marker_write must
# write every field whose value actually changed, not just the one the
# user directly touched, and must NOT write fields nothing moved.


def test_marker_spinbox_edit_without_a_push_only_writes_its_own_field(editor, qapp):
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    bridge = editor._bridge
    # FakeBridge sample 0: start=100, loop_start=5000, loop_end=8000, end=9999

    editor._on_marker_spinbox_changed("start", 200)  # well short of loop_start
    editor._flush_marker_write()
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert [c[0] for c in bridge.set_parameter_calls] == ["SSTART"]
    assert bridge.set_parameter_calls[0][2] == 200


def test_marker_spinbox_push_writes_every_field_that_actually_moved(editor, qapp):
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    bridge = editor._bridge
    # start=100, loop_start=5000, loop_end=8000, end=9999 - dragging "end"
    # to 3000 must push loop_end AND loop_start down with it (both below
    # 3000), while "start" (100, already below 3000) stays untouched

    editor._on_marker_spinbox_changed("end", 3000)
    editor._flush_marker_write()
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    calls = {c[0]: c[2] for c in bridge.set_parameter_calls}
    assert calls == {"SMPEND": 3000, "LOOPAT1": 3000, "LLNGTH1": 0}
    assert editor.waveform_view.markers() == {
        "start": 100, "loop_start": 3000, "loop_end": 3000, "end": 3000,
    }


def test_drag_release_push_writes_every_field_that_actually_moved(editor, qapp):
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    bridge = editor._bridge

    # simulate the canvas drag itself (WaveformView.set_marker, same push
    # math mouseMoveEvent drives) then the release signal exactly as
    # WaveformView.marker_committed would emit it
    editor.waveform_view.set_marker("end", 6000)  # pushes loop_end (8000) only
    m = editor.waveform_view.markers()
    assert m == {"start": 100, "loop_start": 5000, "loop_end": 6000, "end": 6000}

    editor._on_waveform_marker_committed(
        "end", m["start"], m["loop_start"], m["loop_end"], m["end"]
    )
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    calls = {c[0]: c[2] for c in bridge.set_parameter_calls}
    # loop_start (5000) never moved - SSTART/loop_start untouched, only
    # the fields end/loop_end actually changed get written
    assert calls == {"SMPEND": 6000, "LOOPAT1": 6000, "LLNGTH1": 1000}


def test_load_sample_waveform_progressively_fills_the_envelope_in_demo_mode(
    editor, qapp, monkeypatch
):
    # the actual point of begin_live_capture/append_live_samples (see
    # AGENTS.md's Samples tab section and waveform_view.py) - a demo-mode
    # load should visibly grow the waveform across several calls while
    # it's still "in flight", not just show it all at once at the end.
    # Unlike test_load_sample_waveform_preserves_header_only_edits above,
    # this lets _fetch_demo_sample_audio's real pacing loop run (with
    # _DEMO_MS_PER_WORD cut way down so the test doesn't take real
    # wall-clock seconds) since that loop is the thing under test here.
    import ui.program_editor_window as pew

    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    monkeypatch.setattr(pew, "_DEMO_MS_PER_WORD", 0.001)

    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())

    calls = []
    real_append = editor.waveform_view.append_live_samples

    def _recording_append(chunk):
        calls.append(len(chunk))
        real_append(chunk)

    monkeypatch.setattr(editor.waveform_view, "append_live_samples", _recording_append)

    editor._load_sample_waveform()

    # _fetch_demo_sample_audio's own "never fewer than 6 ticks" floor -
    # pinning this down catches a regression back to "load everything at
    # once and call append_live_samples (if at all) exactly once"
    assert len(calls) >= 6
    assert sum(calls) == len(editor._sample_waveform_cache[0]["samples"])
    assert editor.waveform_view.has_waveform() is True


def test_demo_sample_frame_count_matches_the_real_fixture_length(editor):
    # _demo_sample_frame_count is the fix for a real bug a screen
    # recording caught (see the two tests below and AGENTS.md's
    # "Progressive waveform loading" section) - it must predict exactly
    # what _fetch_demo_sample_audio will actually load, or header-only
    # mode/begin_live_capture's total drifts from reality all over again
    import ui.program_editor_window as pew

    real_samples, _framerate = editor._read_wav_samples(pew._DEMO_TEST_AUDIO_PATH)
    assert editor._demo_sample_frame_count(0) == len(real_samples)


def test_demo_header_frame_count_matches_the_eventual_loaded_audio_length(
    editor, qapp, monkeypatch
):
    # regression test for the exact bug the screen recording caught:
    # header-only mode's frame_count (shown the moment a sample is
    # selected, and what begin_live_capture's live-capture total is based
    # on) used to be an arbitrary nominal placeholder unrelated to the
    # real fixture's length, so the envelope would visibly finish loading
    # early and then the whole waveform would snap/rescale (markers
    # included) once the real total replaced it via set_waveform. The two
    # must always agree - no jump at the end.
    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    import ui.program_editor_window as pew

    monkeypatch.setattr(pew, "_DEMO_MS_PER_WORD", 0.001)

    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    header_only_frame_count = editor.waveform_view.frame_count()

    editor._load_sample_waveform()

    assert editor.waveform_view.frame_count() == header_only_frame_count


def test_progressive_load_never_looks_complete_before_the_last_chunk_in_demo_mode(
    editor, qapp, monkeypatch
):
    # the other half of the same regression: with a correct frame_count
    # from the start, the envelope's on-screen width should only ever be
    # proportional to how much of the REAL total has actually loaded - it
    # must never reach full canvas width before the transfer is actually
    # done (that's what "visually finishes early, then snaps" looked
    # like)
    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    import ui.program_editor_window as pew

    monkeypatch.setattr(pew, "_DEMO_MS_PER_WORD", 0.001)

    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())

    view = editor.waveform_view
    widths = []
    real_append = view.append_live_samples

    def _recording_append(chunk):
        real_append(chunk)
        widths.append(len(view._envelope))

    monkeypatch.setattr(view, "append_live_samples", _recording_append)

    editor._load_sample_waveform()

    assert len(widths) >= 6
    assert all(w < view.width() for w in widths[:-1])
    assert widths[-1] == view.width()


# --- Samples tab: loop type (SPTYPE), root note (SPITCH), rename/delete -----


def test_selecting_a_sample_shows_loop_type_and_root_note_from_header(editor, qapp):
    # SPTYPE/SPITCH are in _SAMPLE_DETAIL_FIELDS now, same header-only fetch
    # every other sample-header field (loop points) already used - see
    # AGENTS.md's "Editing loop points without audio"
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())

    assert editor.sample_loop_type_combo.isEnabled() is True
    assert editor.sample_loop_type_combo.currentText() == "Normal looping"  # SPTYPE=0
    assert editor.sample_root_note_spinbox.isEnabled() is True
    assert editor.sample_root_note_spinbox.value() == 60  # SPITCH=60, FakeBridge


def test_nothing_selected_disables_loop_type_and_root_note(editor, qapp):
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())

    editor.sample_list_widget.setCurrentRow(-1)

    assert editor.sample_loop_type_combo.isEnabled() is False
    assert editor.sample_root_note_spinbox.isEnabled() is False


def test_changing_sample_loop_type_writes_sptype(editor, qapp):
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    bridge = editor._bridge

    editor.sample_loop_type_combo.setCurrentIndex(2)  # "No looping"
    editor._flush_write("SPTYPE")
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert ("SPTYPE", 0, 2, 0) in bridge.set_parameter_calls
    assert editor._sample_waveform_cache[0]["sptype"] == 2


def test_changing_sample_root_note_writes_spitch(editor, qapp):
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    bridge = editor._bridge

    editor.sample_root_note_spinbox.setValue(72)
    editor.sample_root_note_spinbox.editingFinished.emit()
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert ("SPITCH", 0, 72, 0) in bridge.set_parameter_calls
    assert editor._sample_waveform_cache[0]["spitch"] == 72


def test_confirm_rename_sample_updates_list_zone_combos_and_writes_shname(
    editor, qapp, monkeypatch
):
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    bridge = editor._bridge
    monkeypatch.setattr(editor, "_prompt_sample_name", lambda _current: "RENAMED")

    editor._confirm_rename_sample()
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: bridge.set_parameter_calls)

    assert editor.sample_list_widget.item(0).text() == "RENAMED"
    assert ("SHNAME", 0, "RENAMED", 0) in bridge.set_parameter_calls
    # index 0 is the blank "-" placeholder (see _on_samples_loaded) - sample
    # 0's own entry is index 1, same offset _update_multi_program_combo_names
    # uses for the Multis tab's program combos
    for combo in editor._zone_combos:
        assert combo.itemText(1) == "RENAMED"


def test_confirm_rename_sample_does_nothing_when_dialog_cancelled(
    editor, qapp, monkeypatch
):
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    bridge = editor._bridge
    monkeypatch.setattr(editor, "_prompt_sample_name", lambda _current: None)

    editor._confirm_rename_sample()

    assert bridge.set_parameter_calls == []
    assert editor.sample_list_widget.item(0).text() == "SQUARE"


def test_confirm_delete_sample_submits_delete_on_confirm(editor, qapp, monkeypatch):
    import ui.program_editor_window as pew

    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    bridge = editor._bridge
    monkeypatch.setattr(
        pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.Yes
    )

    editor._confirm_delete_sample()
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.sample_list_widget.count() == 3)

    assert bridge.sample_list() == ["SAWTOOTH", "PULSE", "SINE"]
    # a full reload, not a targeted removal (see _on_sample_deleted) -
    # the list widget itself should reflect the same shrunk roster
    assert [
        editor.sample_list_widget.item(i).text()
        for i in range(editor.sample_list_widget.count())
    ] == ["SAWTOOTH", "PULSE", "SINE"]


def test_confirm_delete_sample_does_nothing_when_declined(editor, qapp, monkeypatch):
    import ui.program_editor_window as pew

    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    bridge = editor._bridge
    monkeypatch.setattr(
        pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.No
    )

    editor._confirm_delete_sample()

    assert bridge.sample_list() == ["SQUARE", "SAWTOOTH", "PULSE", "SINE"]


def test_sample_list_context_actions_disabled_with_nothing_selected(editor, qapp):
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    assert editor._rename_sample_action.isEnabled() is True
    assert editor._delete_sample_action.isEnabled() is True

    editor.sample_list_widget.setCurrentRow(-1)

    assert editor._rename_sample_action.isEnabled() is False
    assert editor._delete_sample_action.isEnabled() is False


# --- Samples tab: Trim/Reverse ------------------------------------------


def _stub_demo_audio(editor, monkeypatch, samples, framerate=44100):
    # _fetch_demo_sample_audio and _demo_sample_frame_count must always
    # agree on a sample's length (see AGENTS.md's "Progressive waveform
    # loading" - a mismatch between the two is exactly the bug fixed
    # there) - the cached header/markers come from _demo_sample_frame_
    # count, so stubbing only _fetch_demo_sample_audio with a different
    # length leaves stale, mismatched markers behind, which broke these
    # tests' own "nothing to do" guards during development. Stub both,
    # always in agreement.
    monkeypatch.setattr(
        editor, "_fetch_demo_sample_audio", lambda sample_index: (samples, framerate)
    )
    monkeypatch.setattr(
        editor, "_demo_sample_frame_count", lambda sample_index: len(samples)
    )


def test_trim_and_reverse_buttons_disabled_until_audio_loaded(editor, qapp, monkeypatch):
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())

    assert editor.trim_sample_button.isEnabled() is False
    assert editor.reverse_sample_button.isEnabled() is False

    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    _stub_demo_audio(editor, monkeypatch, [0] * 500)
    editor._load_sample_waveform()

    assert editor.trim_sample_button.isEnabled() is True
    assert editor.reverse_sample_button.isEnabled() is True

    editor.sample_list_widget.setCurrentRow(-1)

    assert editor.trim_sample_button.isEnabled() is False
    assert editor.reverse_sample_button.isEnabled() is False


def test_confirm_trim_sample_does_nothing_when_markers_cover_whole_sample(
    editor, qapp, monkeypatch
):
    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    # stub BEFORE selecting the sample - the automatic async header fetch
    # _on_sample_selected kicks off runs as soon as the row is selected,
    # and caches markers derived from whichever _demo_sample_frame_count
    # is live at that moment; stubbing afterwards leaves those markers
    # stale/mismatched against the differently-sized stubbed audio
    _stub_demo_audio(editor, monkeypatch, list(range(500)))
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    editor._load_sample_waveform()
    before = dict(editor._sample_waveform_cache[0])

    editor._confirm_trim_sample()  # start=0, end=frame_count-1 already - nothing to do

    assert editor._sample_waveform_cache[0] == before


def test_confirm_reverse_sample_does_nothing_when_too_short(editor, qapp, monkeypatch):
    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    _stub_demo_audio(editor, monkeypatch, [42])
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    editor._load_sample_waveform()

    editor._confirm_reverse_sample()

    assert editor._sample_waveform_cache[0]["samples"] == [42]


def test_trim_sample_in_demo_mode_shrinks_the_cached_sample_and_waveform(
    editor, qapp, monkeypatch
):
    import ui.program_editor_window as pew

    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    _stub_demo_audio(editor, monkeypatch, list(range(500)))
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    editor._load_sample_waveform()

    editor.waveform_view.set_marker("start", 100)
    editor.waveform_view.set_marker("loop_start", 150)
    editor.waveform_view.set_marker("loop_end", 300)
    editor.waveform_view.set_marker("end", 400)

    monkeypatch.setattr(
        pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(pew, "_DEMO_MS_PER_WORD", 0.001)

    editor._confirm_trim_sample()

    entry = editor._sample_waveform_cache[0]
    assert entry["samples"] == list(range(100, 401))
    assert entry["frame_count"] == 301
    assert (entry["start"], entry["loop_start"], entry["loop_end"], entry["end"]) == (
        0, 50, 200, 300,
    )
    assert editor.waveform_view.frame_count() == 301
    assert editor.waveform_view.markers() == {
        "start": 0, "loop_start": 50, "loop_end": 200, "end": 300,
    }


def test_trim_sample_does_nothing_when_declined(editor, qapp, monkeypatch):
    import ui.program_editor_window as pew

    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    _stub_demo_audio(editor, monkeypatch, list(range(500)))
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    editor._load_sample_waveform()
    editor.waveform_view.set_marker("start", 100)
    monkeypatch.setattr(
        pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.No
    )

    editor._confirm_trim_sample()

    assert editor._sample_waveform_cache[0]["frame_count"] == 500


def test_reverse_sample_in_demo_mode_reverses_the_cached_sample(editor, qapp, monkeypatch):
    import ui.program_editor_window as pew

    monkeypatch.setenv("AKAISDS_DEMO_SAMPLER", "1")
    _stub_demo_audio(editor, monkeypatch, list(range(100)))
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    editor._load_sample_waveform()

    editor.waveform_view.set_marker("start", 10)
    editor.waveform_view.set_marker("loop_start", 30)
    editor.waveform_view.set_marker("loop_end", 60)
    editor.waveform_view.set_marker("end", 90)

    monkeypatch.setattr(
        pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(pew, "_DEMO_MS_PER_WORD", 0.001)

    editor._confirm_reverse_sample()

    entry = editor._sample_waveform_cache[0]
    assert entry["samples"] == list(reversed(range(100)))
    assert entry["frame_count"] == 100
    # frame i -> 99 - i
    assert (entry["start"], entry["loop_start"], entry["loop_end"], entry["end"]) == (
        99 - 90, 99 - 60, 99 - 30, 99 - 10,
    )


def test_trim_sample_real_mode_without_sampler_controller_shows_message(
    editor, qapp, monkeypatch
):
    import ui.program_editor_window as pew

    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    editor.sample_list_widget.setCurrentRow(0)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    # real mode has no sampler_controller on the fixture's bare QWidget
    # main_window, mirroring _load_sample_waveform's own equivalent guard
    monkeypatch.setattr(
        pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.Yes
    )
    # force has_waveform() true without a real (demo-mode-only) audio
    # fetch, since this test is specifically about the pre-fetch guard
    editor.waveform_view.set_waveform([0] * 10, 0, 2, 7, 9)
    editor._sample_waveform_cache[0] = {
        "samples": [0] * 10, "framerate": 44100, "frame_count": 10,
        "start": 0, "loop_start": 2, "loop_end": 7, "end": 9,
        "sptype": 0, "spitch": 60,
    }

    editor._confirm_reverse_sample()

    assert "no Transfer Dashboard connection available" in editor.status_bar.currentMessage()


def _select_sample_with_full_audio(editor, qapp, sample_index, samples, markers):
    editor.sample_list_widget.setCurrentRow(sample_index)
    editor._worker.wait_until_idle()
    _pump_until(qapp, lambda: editor.waveform_view.has_header())
    editor.waveform_view.set_waveform(
        samples, markers["start"], markers["loop_start"], markers["loop_end"],
        markers["end"],
    )
    editor._sample_waveform_cache[sample_index] = {
        "samples": samples, "framerate": 44100, "frame_count": len(samples),
        "sptype": 0, "spitch": 60, **markers,
    }


def test_reverse_sample_real_mode_happy_path_sends_deletes_and_renames(
    editor, qapp, monkeypatch
):
    # the real (non-demo) send/delete/rename pipeline - see
    # _perform_sample_edit_real's own comment for why this exact order
    # (send under a temp name, confirm, delete original, look up by name,
    # rename back) was chosen. FakeSamplerController mutates the SAME
    # FakeBridge the editor's BridgeWorker reads from, mirroring how real
    # hardware is one shared device behind two connections.
    import ui.program_editor_window as pew

    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    monkeypatch.setattr(
        pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.Yes
    )
    bridge = editor._bridge
    fake_sampler = FakeSamplerController(bridge)
    editor._main_window.sampler_controller = fake_sampler

    original_samples = list(range(100))
    _select_sample_with_full_audio(  # sample 0 == "SQUARE"
        editor, qapp, 0, original_samples,
        {"start": 10, "loop_start": 30, "loop_end": 60, "end": 90},
    )

    editor._confirm_reverse_sample()

    # sent the reversed audio under a distinct temp name first, never the
    # original's own name (see the ambiguous-name-resolution reasoning in
    # _perform_sample_edit_real's own comment)
    assert len(fake_sampler.sent_entries) == 1
    assert fake_sampler.sent_entries[0]["name"] == "SQUARE-TMP"
    assert fake_sampler.sent_entries[0]["samples"] == list(reversed(original_samples))

    # original deleted, replacement (now at whatever index the delete
    # shifted it to) renamed back to the original's own name
    assert "SQUARE-TMP" not in bridge.sample_list()
    assert bridge.sample_list().count("SQUARE") == 1
    rename_calls = [c for c in bridge.set_parameter_calls if c[0] == "SHNAME"]
    assert len(rename_calls) == 1
    assert rename_calls[0][2] == "SQUARE"

    assert "complete" in editor.status_bar.currentMessage().lower()
    # landed on a clean final selection - the renamed sample, not nothing
    assert editor.sample_list_widget.currentItem() is not None
    assert editor.sample_list_widget.currentItem().text() == "SQUARE"


def test_reverse_sample_real_mode_send_failure_leaves_original_untouched(
    editor, qapp, monkeypatch
):
    # if the send fails, the original must never be deleted - see
    # _perform_sample_edit_real's own comment on why this ordering
    # (send-then-delete, not delete-then-send) was chosen specifically to
    # avoid this failure mode losing the sample outright
    import ui.program_editor_window as pew

    monkeypatch.delenv("AKAISDS_DEMO_SAMPLER", raising=False)
    monkeypatch.setattr(
        pew.QMessageBox, "question", lambda *a, **k: pew.QMessageBox.StandardButton.Yes
    )
    bridge = editor._bridge
    original_samples_before = list(bridge.sample_list())
    fake_sampler = FakeSamplerController(bridge)
    fake_sampler.next_result = False  # simulate a failed/incomplete send
    editor._main_window.sampler_controller = fake_sampler

    _select_sample_with_full_audio(
        editor, qapp, 0, list(range(50)),
        {"start": 0, "loop_start": 10, "loop_end": 40, "end": 49},
    )

    editor._confirm_reverse_sample()

    assert bridge.sample_list() == original_samples_before  # nothing deleted
    assert not any(c[0] == "SHNAME" for c in bridge.set_parameter_calls)
    message = editor.status_bar.currentMessage().lower()
    assert "failed" in message
    assert "not touched" in message
