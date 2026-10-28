# The audition bar's MIDI Input: what it lets through, when the port is held open, and that the sampler's own ports are never offered.
# The real port is replaced by a fake (no rtmidi client is created here).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from core import app_config, audition
from ui.audition_bar import AuditionBar


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


class FakePort:
    opened = []

    def __init__(self, name):
        if name == "GONE":
            raise RuntimeError("no input port named 'GONE'; have []")
        self.name, self.callback, self.closed = name, None, False
        FakePort.opened.append(self)

    def set_message_callback(self, callback):
        self.callback = callback

    def close_port(self):
        self.closed = True

    def play(self, *message):
        self.callback(list(message))


@pytest.fixture
def bar(qapp, monkeypatch):
    FakePort.opened = []
    app_config.save_audition_input("")  # (the temp config outlives a test)
    monkeypatch.setattr(app_config, "get_saved_ports", lambda: ("Sampler In", "Sampler Out"))
    bar = AuditionBar()
    bar.resize(1300, 200)
    bar._list_inputs = lambda: ["Sampler In", "Sampler Out", "Master Keys", "loopMIDI Port"]
    bar._open_input = FakePort
    sent = []
    bar.midi_out.connect(sent.append)
    bar.sent = sent
    bar.show()
    bar.set_available(True)
    yield bar
    bar.set_open(False)
    bar.deleteLater()


def choose(bar, name):
    bar.refresh_inputs()
    bar.input_combo.setCurrentIndex(bar.input_combo.findData(name))
    bar._on_input_chosen(bar.input_combo.currentIndex())


# -- what gets through -----------------------------------------------------------------------------------------------------


def test_notes_and_a_few_controllers_pass_on_the_channel_they_arrived_on():
    assert audition.from_input([0x93, 60, 90]) == audition.note_on(3, 60, 90)
    assert audition.from_input([0x83, 60, 40]) == audition.note_off(3, 60)
    assert audition.from_input([0x90, 60, 0]) == audition.note_off(0, 60)  # (note-on with velocity 0 is a note-off)
    for controller in audition.INPUT_CONTROLLERS:
        assert audition.from_input([0xB2, controller, 5]) == audition.control_change(2, controller, 5)


@pytest.mark.parametrize(
    "message",
    [
        [0xF0, 0x47, 0x00, 0x04, 0x48, 0xF7],  # SysEx never gets through: this input must not be able to put anything but a note on the sampler's wire
        [0xF0, 0x7E, 0x00],
        [0xC0, 5],  # program change
        [0xE0, 0, 64],  # pitch bend
        [0xD0, 50],  # channel pressure
        [0xB0, 7, 100],  # volume
        [0xB0, 0, 1],  # bank select
        [0xF8],  # clock
        [0xFE],  # active sensing
        [0x90, 60],  # truncated
        [0x90, 200, 100],  # not a data byte
        [],
    ],
)
def test_everything_else_is_dropped(message):
    assert audition.from_input(message) is None


# -- when the port is open -------------------------------------------------------------------------------------------------


def test_a_chosen_device_is_held_open_only_while_the_keyboard_is_open(bar):
    choose(bar, "Master Keys")
    assert bar.input_name() == "Master Keys" and not bar.input_is_open() and FakePort.opened == []
    bar.set_open(True)
    assert bar.input_is_open() and [p.name for p in FakePort.opened] == ["Master Keys"]
    bar.set_open(False)
    assert not bar.input_is_open() and FakePort.opened[0].closed and FakePort.opened[0].callback is None


def test_choosing_none_closes_the_device(bar):
    bar.set_open(True)
    choose(bar, "Master Keys")
    assert bar.input_is_open()
    choose(bar, "")
    assert not bar.input_is_open() and FakePort.opened[0].closed


def test_the_device_is_let_go_while_the_sampler_is_busy_and_taken_again_after(bar):
    bar.set_open(True)
    choose(bar, "Master Keys")
    bar.set_available(False, "busy")
    assert not bar.input_is_open()
    bar.set_available(True)
    assert bar.input_is_open() and len(FakePort.opened) == 2


def test_no_device_chosen_opens_nothing(bar):
    bar.set_open(True)
    assert not bar.input_is_open() and FakePort.opened == []


def test_a_missing_device_says_so_and_plays_nothing(bar):
    bar._input_name = "GONE"
    bar.set_open(True)
    assert not bar.input_is_open() and not bar.input_status.isHidden() and bar.input_status.text() == "Not found"


def test_the_sampler_ports_are_not_offered_and_not_opened(bar):
    bar.refresh_inputs()
    names = [bar.input_combo.itemData(i) for i in range(bar.input_combo.count())]
    assert names == ["", "Master Keys", "loopMIDI Port"]
    bar._input_name = "Sampler In"  # (a stale saved choice, or the sampler moved onto it)
    bar.set_open(True)
    assert not bar.input_is_open() and FakePort.opened == [] and not bar.input_status.isHidden()


def test_a_device_that_has_gone_stays_listed_as_not_connected(bar):
    bar._input_name = "Old Keys"
    bar.refresh_inputs()
    assert bar.input_combo.currentText() == "Old Keys (not connected)" and bar.input_combo.currentData() == "Old Keys"


def test_the_choice_is_remembered(bar):
    choose(bar, "loopMIDI Port")
    assert app_config.get_saved_audition_input() == "loopMIDI Port"
    assert AuditionBar().input_name() == "loopMIDI Port"
    choose(bar, "")
    assert app_config.get_saved_audition_input() == ""


# -- the notes --------------------------------------------------------------------------------------------------------------


def test_input_notes_reach_the_sampler_light_the_keys_and_keep_their_channel(bar):
    bar.set_open(True)
    choose(bar, "Master Keys")
    port = FakePort.opened[0]
    bar.channel_combo.setCurrentIndex(0)
    port.play(0x95, 64, 77)
    assert bar.sent == [audition.note_on(5, 64, 77)]
    assert bar.keyboard._external == {64} and "E3" in bar.playing_label.text()
    port.play(0x85, 64, 0)
    assert bar.sent[-1] == audition.note_off(5, 64) and bar.keyboard._external == set()


def test_input_messages_that_are_not_notes_never_reach_the_sampler(bar):
    bar.set_open(True)
    choose(bar, "Master Keys")
    port = FakePort.opened[0]
    for message in ([0xF0, 0x47, 0x00, 0x04, 0x48, 0xF7], [0xC1, 3], [0xE0, 0, 0x40], [0xB0, 7, 0], [0xF8]):
        port.play(*message)
    assert bar.sent == []


def test_all_notes_off_from_the_input_forgets_that_channels_notes(bar):
    bar.set_open(True)
    choose(bar, "Master Keys")
    port = FakePort.opened[0]
    port.play(0x90, 60, 100)
    port.play(0x91, 62, 100)
    port.play(0xB0, 123, 0)
    assert bar.sent[-1] == audition.control_change(0, 123, 0) and bar.keyboard._external == {62}


def test_notes_still_held_when_the_input_goes_are_released(bar):
    bar.set_open(True)
    choose(bar, "Master Keys")
    port = FakePort.opened[0]
    port.play(0x92, 60, 100)
    port.play(0x93, 67, 100)
    bar.sent.clear()
    bar.set_open(False)
    assert sorted(bar.sent) == sorted([audition.note_off(2, 60), audition.note_off(3, 67)]) and bar.keyboard._external == set()


def test_panic_lets_go_of_input_notes_on_every_channel(bar):
    bar.set_open(True)
    choose(bar, "Master Keys")
    FakePort.opened[0].play(0x94, 70, 100)
    bar.sent.clear()
    bar.channel_combo.setCurrentIndex(1)
    bar.panic()
    assert audition.note_off(4, 70) in bar.sent and audition.panic(1)[2] in bar.sent and bar.keyboard._external == set()


def test_a_message_that_arrives_after_the_input_closed_is_ignored(bar):
    bar.set_open(True)
    choose(bar, "Master Keys")
    port = FakePort.opened[0]
    bar.set_open(False)
    bar.sent.clear()
    bar._on_input_message([0x90, 60, 100])
    assert bar.sent == [] and port.closed
